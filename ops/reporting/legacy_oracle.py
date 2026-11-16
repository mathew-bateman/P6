"""Recreate a captured SQL oracle only inside caller-owned staging transactions.

No backup restore, live P6 database access, commit, refresh, or publication is
performed here. The caller must roll the outer transaction back after reading
the independent SQL outputs.
"""
import hashlib
import json
import re
from pathlib import Path

from backups.services.mssql import quote_identifier as qi
from reporting_mirror.sql_staging import assert_staging, query, safe_stage_name


DEFINITIONS = Path('/app/work/sql-parallel-20261025/sql-definitions-before.json')
PLAN = Path('/app/.recovery/20261116T1016Z-pre-sql-retirement/retirement-plan.json')
RESULT_TABLES = (
    'xertoolkit_result_project_metrics',
    'xertoolkit_result_logic_loop_tasks',
    'xertoolkit_result_out_of_sequence_exceptions',
    'xertoolkit_result_schedule_quality_task_evidence',
)
TYPE_NAMES = {
    34: 'image', 35: 'text', 36: 'uniqueidentifier', 40: 'date', 41: 'time',
    42: 'datetime2', 43: 'datetimeoffset', 48: 'tinyint', 52: 'smallint',
    56: 'int', 58: 'smalldatetime', 59: 'real', 60: 'money', 61: 'datetime',
    62: 'float', 99: 'ntext', 104: 'bit', 106: 'decimal', 108: 'numeric',
    122: 'smallmoney', 127: 'bigint', 165: 'varbinary', 167: 'varchar',
    173: 'binary', 175: 'char', 189: 'rowversion', 231: 'nvarchar',
    239: 'nchar', 241: 'xml',
}


def column_type(column):
    kind = TYPE_NAMES[column['system_type_id']]
    if kind in {'varchar', 'char', 'nvarchar', 'nchar', 'binary', 'varbinary'}:
        length = column['max_length']
        size = 'max' if length == -1 else str(length // 2 if kind.startswith('n') else length)
        return kind + '(' + size + ')'
    if kind in {'decimal', 'numeric'}:
        return kind + '(' + str(column['precision']) + ',' + str(column['scale']) + ')'
    if kind in {'datetime2', 'datetimeoffset', 'time'}:
        return kind + '(' + str(column['scale']) + ')'
    return kind


def load_archived_modules(definitions_path=DEFINITIONS, plan_path=PLAN):
    """Require byte-for-byte bodies matching the final retirement catalogue."""
    plan = json.loads(Path(plan_path).read_text())['catalogue']
    expected = {o['name']: o for o in plan['objects'] if o['type'].strip() != 'U'}
    archive = json.loads(Path(definitions_path).read_text())
    modules = {}
    for row in archive:
        if row.get('schema_name') != 'powerbitables' or row['name'] not in expected:
            continue
        definition = row.get('definition')
        if not definition:
            continue
        digest = hashlib.sha256(definition.encode('utf-8')).hexdigest()
        if digest.lower() != expected[row['name']]['definition_sha256'].lower():
            raise RuntimeError('Archived SQL body differs from retirement: ' + row['name'])
        modules[row['name']] = dict(row, definition_sha256=digest)
    if len(expected) != 36 or set(modules) != set(expected):
        raise RuntimeError('The exact 36 retired executable SQL modules are unavailable.')
    return plan, list(modules.values())


def _clone_settings(connection, source_database):
    """Copy retained P6 22 configuration to an otherwise empty P6 23 schema."""
    source = qi(source_database)
    query(connection, "IF SCHEMA_ID('powerbitables') IS NULL EXEC('CREATE SCHEMA powerbitables')")
    tables = query(connection, 'SELECT t.name FROM ' + source + '.sys.tables t JOIN ' + source +
        ".sys.schemas s ON s.schema_id=t.schema_id WHERE s.name='powerbitables' ORDER BY t.name")
    for row in tables:
        name = row['name']
        if name in RESULT_TABLES:
            continue
        target = 'powerbitables.' + qi(name)
        if query(connection, 'SELECT OBJECT_ID(?) id', ('powerbitables.' + name,))[0]['id'] is not None:
            raise RuntimeError('Refusing to replace existing P6 23 oracle settings: ' + name)
        query(connection, 'SELECT TOP(0) * INTO ' + target + ' FROM ' + source + '.' + target)
        defaults = query(connection, 'SELECT c.name,d.definition FROM ' + source + '.sys.default_constraints d JOIN ' + source +
            '.sys.columns c ON c.object_id=d.parent_object_id AND c.column_id=d.parent_column_id '
            'WHERE d.parent_object_id=OBJECT_ID(?)', (source_database + '.powerbitables.' + name,))
        for default in defaults:
            query(connection, 'ALTER TABLE ' + target + ' ADD DEFAULT ' + default['definition'] + ' FOR ' + qi(default['name']))
        columns = query(connection, 'SELECT name,is_identity FROM ' + source + '.sys.columns '
            'WHERE object_id=OBJECT_ID(?) AND system_type_id<>189 ORDER BY column_id',
            (source_database + '.powerbitables.' + name,))
        if name != 'xertoolkit_refresh_run_history':
            fields = ','.join(qi(c['name']) for c in columns)
            identity = any(c['is_identity'] for c in columns)
            if identity:
                query(connection, 'SET IDENTITY_INSERT ' + target + ' ON')
            query(connection, 'INSERT INTO ' + target + ' (' + fields + ') SELECT ' + fields + ' FROM ' + source + '.' + target)
            if identity:
                query(connection, 'SET IDENTITY_INSERT ' + target + ' OFF')
        indexes = query(connection, 'SELECT i.name,i.is_unique,i.type,i.filter_definition,c.name column_name,'
            'ic.key_ordinal,ic.is_included_column,ic.is_descending_key FROM ' + source + '.sys.indexes i JOIN ' + source +
            '.sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id JOIN ' + source +
            '.sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id WHERE i.object_id=OBJECT_ID(?) '
            'AND i.type IN(1,2) AND i.is_disabled=0 AND i.is_hypothetical=0 '
            'ORDER BY i.index_id,ic.is_included_column,ic.key_ordinal,ic.index_column_id',
            (source_database + '.powerbitables.' + name,))
        grouped = {}
        for index in indexes:
            grouped.setdefault(index['name'], []).append(index)
        for index_name, entries in grouped.items():
            first = entries[0]
            keys = ','.join(qi(e['column_name']) + (' DESC' if e['is_descending_key'] else ' ASC')
                for e in entries if not e['is_included_column'])
            statement = 'CREATE ' + ('UNIQUE ' if first['is_unique'] else '') + (
                'CLUSTERED ' if first['type'] == 1 else 'NONCLUSTERED ') + 'INDEX ' + qi(index_name) + ' ON ' + target + ' (' + keys + ')'
            included = [qi(e['column_name']) for e in entries if e['is_included_column']]
            if included:
                statement += ' INCLUDE (' + ','.join(included) + ')'
            if first['filter_definition']:
                statement += ' WHERE ' + first['filter_definition']
            query(connection, statement)
    return len(tables)


def _create_result_tables(connection, plan):
    columns = {name: [] for name in RESULT_TABLES}
    for column in plan['columns']:
        if column['table_name'] in columns:
            columns[column['table_name']].append(column)
    # The archived original defaults remain in the matching P6 22 snapshot.
    original_defaults = query(connection, 'SELECT object_name,column_name,default_definition FROM ' +
        qi(safe_stage_name('p6_22')) + '.xertoolkit_rollback.schedule_quality_20260714_table_columns '
        'WHERE default_definition IS NOT NULL')
    defaults = {(d['object_name'],d['column_name']):d['default_definition'] for d in original_defaults}
    defaults.update({('xertoolkit_result_project_metrics', c): '((0))'
        for c in ('soft_constraint_count','soft_constraint_activity_count')})
    primary = {
        RESULT_TABLES[0]: ('proj_id',), RESULT_TABLES[1]: ('proj_id','task_id'),
        RESULT_TABLES[2]: ('proj_id','relationship_id'),
        RESULT_TABLES[3]: ('proj_id','check_code','task_id'),
    }
    for name, values in columns.items():
        if not values or query(connection, 'SELECT OBJECT_ID(?) id', ('powerbitables.'+name,))[0]['id'] is not None:
            raise RuntimeError('Oracle result table missing catalogue or already exists: ' + name)
        definitions = []
        for column in sorted(values,key=lambda c:c['column_id']):
            field = qi(column['column_name']) + ' ' + column_type(column)
            if column['is_identity']:
                field += ' IDENTITY(1,1)'
            field += ' NULL' if column['is_nullable'] else ' NOT NULL'
            default = defaults.get((name,column['column_name']))
            if default:
                field += ' DEFAULT ' + default
            definitions.append(field)
        definitions.append('PRIMARY KEY CLUSTERED (' + ','.join(qi(c) for c in primary[name]) + ')')
        query(connection, 'CREATE TABLE powerbitables.' + qi(name) + ' (' + ','.join(definitions) + ')')
        query(connection, 'CREATE INDEX ' + qi('IX_oracle_' + name.removeprefix('xertoolkit_result_')) +
            ' ON powerbitables.' + qi(name) + ' (config_version_id,proj_id)')
    query(connection, 'CREATE INDEX IX_oracle_evidence_check ON powerbitables.xertoolkit_result_schedule_quality_task_evidence (check_code,proj_id,task_id)')
    query(connection, 'CREATE INDEX IX_oracle_oos_successor ON powerbitables.xertoolkit_result_out_of_sequence_exceptions (proj_id,successor_task_id)')


def prepare_reference_connection(connection, code, *, definitions_path=DEFINITIONS,
                                 plan_path=PLAN, progress=lambda value: None):
    """Install exact legacy logic without committing the outer transaction.

    The four result tables reproduce captured column types/nullability and
    primary keys. Their indexes are comparison-only equivalents; SQL result
    values come from the original, hash-verified 36 executable module bodies.
    """
    assert_staging(connection)
    state = query(connection, 'SELECT DB_NAME() database_name,@@TRANCOUNT transactions')[0]
    if state['database_name'] != safe_stage_name(code) or state['transactions'] < 1:
        raise RuntimeError('Oracle setup requires the expected staging database and an outer transaction.')
    plan, modules = load_archived_modules(definitions_path, plan_path)
    if code == 'p6_23':
        progress('Copying retained SQL configuration into transactional P6 23 oracle')
        _clone_settings(connection, safe_stage_name('p6_22'))
    progress('Creating four transaction-local legacy result tables')
    _create_result_tables(connection, plan)
    # Failed dependency compilation must not doom the caller's outer transaction.
    query(connection, 'SET XACT_ABORT OFF')
    pending = list(modules)
    rewritten = []
    while pending:
        completed = []
        errors = {}
        for module in pending:
            original = module['definition']
            definition = re.sub(r'\b(?:CREATE\s+(?:OR\s+ALTER\s+)?|ALTER\s+)(VIEW|FUNCTION|PROCEDURE|TRIGGER)\b',
                r'CREATE OR ALTER \1',original,count=1,flags=re.I)
            try:
                query(connection,definition)
                completed.append(module)
                if definition != original:
                    rewritten.append(module['name'])
            except Exception as error:
                errors[module['name']] = str(error)[:700]
        if not completed:
            raise RuntimeError('Legacy oracle dependencies could not compile: '+json.dumps(errors))
        pending = [m for m in pending if m not in completed]
    query(connection, 'SET XACT_ABORT ON')
    return {'database':state['database_name'],'module_bodies_verified':len(modules),
        'result_tables_created':len(RESULT_TABLES),'ddl_prefix_rewritten':rewritten,
        'transaction_owned_by_caller':True,'committed':False}
