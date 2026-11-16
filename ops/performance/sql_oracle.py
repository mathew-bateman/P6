"""Independent legacy-SQL oracle for disposable all-project performance trials.

Restore the reviewed pre-retirement definitions into a seed database, install
those exact definitions on the fixed 16:32 source restores, copy PostgreSQL
settings version 37 and run the original SQL all-project procedure. Production
connections/database names are rejected. SQL execution is acceptance QA outside
refresh timing; this module never publishes application views.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import re
import sys
import time
import uuid
from types import SimpleNamespace
from unittest.mock import patch

ALLOWED_SQL_DATABASES = frozenset({'master','perf_oracle_seed22','perf_sql_oracle_p6_22','perf_sql_oracle_p6_23'})
SOURCE_DATABASES = {'p6_22':'perf_sql_oracle_p6_22','p6_23':'perf_sql_oracle_p6_23'}
EXPECTED_PROJECTS = {'p6_22':820,'p6_23':2384}
RUNTIME_COLUMNS = frozenset({'check_run_id','refreshed_at','calculated_date'})
SEED_PATH = '/backups/oracle_seed22.bak'
SCHEMA_RE = re.compile(r'(?:load|native_test|native_shadow)_[0-9a-f]{16}_p6_(?:22|23)\Z')


def private_database(name):
    if name not in ALLOWED_SQL_DATABASES:
        raise ValueError('SQL oracle database is outside its exact private allowlist')
    return name


def normalize_module(definition):
    return re.sub(r'\bCREATE\s+(?:OR\s+ALTER\s+)?(VIEW|FUNCTION|PROCEDURE|TRIGGER)\b',r'CREATE OR ALTER \1',definition,count=1,flags=re.I)


def runtime_columns(columns):
    return sorted(RUNTIME_COLUMNS & {c['name'] for c in columns})


def oracle_projection(layout):
    """Preserve all business values; only independent run metadata is normalized."""
    from psycopg import sql
    fields=[]
    for field in layout:
        name=field['name'];value=sql.Identifier('t',name)
        if name in RUNTIME_COLUMNS:
            value=sql.SQL('({} IS NULL)').format(value)
        elif name=='detail_json':
            value=sql.SQL("convert_to(CASE WHEN {v} IS NULL OR {v}='' THEN {v} ELSE ({v}::jsonb)::text END,'UTF8')").format(v=value)
        elif field['category'] in {'S','E'} or field['type'] in {'json','xml'}:
            value=sql.SQL("convert_to({}::text,'UTF8')").format(value)
        fields.append(sql.SQL('{} AS {}').format(value,sql.Identifier(name)))
    return sql.SQL(',').join(fields)


class Oracle:
    def __init__(self, private_file, directory):
        import psycopg
        from psycopg.rows import dict_row
        from backups.services.mssql import connect
        from reporting_mirror.sql_staging import query
        self.psycopg=psycopg;self.dict_row=dict_row;self.sql_connect=connect;self.query=query
        self.cfg=json.loads(Path(private_file).read_text());self.directory=Path(directory)
        self.directory.mkdir(parents=True,exist_ok=True)
        c=self.cfg['staging']
        if c['host']!='p6-perf-sql' or c.get('hostname')!='p6-perf-sql':
            raise ValueError('Oracle must use the separate p6-perf-sql container')
        self.target=SimpleNamespace(sql_host=c['host'],sql_port=c['port'],sql_username=c['username'],sql_password=c['password'],odbc_driver='ODBC Driver 18 for SQL Server',encrypt_connection=True,trust_server_certificate=True)
        p=self.cfg['postgres'];self.pg_config={k:p[k] for k in ('host','port','dbname','user','password')}
        if self.pg_config['host']!='p6-perf-postgres' or self.pg_config['dbname']!='p6_perf_trial':
            raise ValueError('Oracle must use the separate p6_perf_trial database')

    def assert_sql(self,connection):
        row=self.query(connection,"SELECT DB_NAME() database_name,CAST(SERVERPROPERTY('MachineName') AS nvarchar(128)) machine")[0]
        if row['machine']!='p6-perf-sql':
            raise ValueError('Refusing non-benchmark SQL instance')
        private_database(row['database_name'])
        return row

    def connect(self,database='master',**kwargs):
        private_database(database)
        c=self.sql_connect(self.target,database=database,**kwargs)
        try:self.assert_sql(c)
        except Exception:c.close();raise
        c.timeout=5400
        return c

    def pg(self,**kwargs):
        c=self.psycopg.connect(**self.pg_config,row_factory=self.dict_row,**kwargs)
        try:
            if c.execute('SELECT current_database() n').fetchone()['n']!='p6_perf_trial' or not c.execute('SHOW cluster_name').fetchone()['cluster_name'].startswith('p6-perf'):
                raise ValueError('Refusing non-benchmark PostgreSQL target')
        except Exception:c.close();raise
        return c

    def restore(self,database,path):
        from backups.services.mssql import quote_identifier as qi,sql_string as qs
        private_database(database)
        if database=='master' or path not in {SEED_PATH,'/backups/p6_22.bak','/backups/p6_23.bak'}:
            raise ValueError('Unexpected oracle restore target or payload')
        with self.connect() as c:
            exists=self.query(c,'SELECT DB_ID(?) id',(database,))[0]['id']
            if exists:
                files=self.query(c,'SELECT physical_name FROM sys.master_files WHERE database_id=DB_ID(?)',(database,))
                if not files or any(not r['physical_name'].startswith('/var/opt/mssql/data/'+database+'_') for r in files):
                    raise ValueError('Existing database does not have the oracle-owned file paths')
                return {'database':database,'reused_private_restore':True}
            self.query(c,'RESTORE VERIFYONLY FROM DISK='+qs(path)+' WITH CHECKSUM')
            files=self.query(c,'RESTORE FILELISTONLY FROM DISK='+qs(path))
            if not files or any(r['Type'] not in {'D','L'} for r in files):raise ValueError('Unexpected SQL backup layout')
            moves=[]
            for n,r in enumerate(files):
                target='/var/opt/mssql/data/'+database+'_'+str(n)+('.ldf' if r['Type']=='L' else '.mdf')
                moves.append('MOVE '+qs(r['LogicalName'])+' TO '+qs(target))
            self.query(c,'RESTORE DATABASE '+qi(database)+' FROM DISK='+qs(path)+' WITH CHECKSUM,RECOVERY,'+','.join(moves))
            self.query(c,'ALTER DATABASE '+qi(database)+' SET AUTO_CLOSE OFF; ALTER DATABASE '+qi(database)+' SET AUTO_SHRINK OFF')
            return {'database':database,'restore_checksum_verified':True}

    def copy_definitions(self,destination):
        """Use the original installer, bound exclusively to private databases."""
        import reporting_mirror.sql_staging as staging
        with self.connect(destination) as c:
            if self.query(c,"SELECT count(*) n FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id WHERE s.name='powerbitables'")[0]['n']:
                raise ValueError('Destination already has reporting objects; do not overwrite an oracle')
        mapping={'p6_22':'perf_oracle_seed22','p6_23':destination}
        with ExitStack() as stack:
            stack.enter_context(patch.object(staging,'staging_target',lambda:self.target))
            stack.enter_context(patch.object(staging,'safe_stage_name',lambda code:private_database(mapping[code])))
            stack.enter_context(patch.object(staging,'assert_staging',self.assert_sql))
            staging.install_reporting_definitions(lambda m:print('ORACLE_DEFINITIONS',destination,flush=True))

    def settings(self):
        import reporting_mirror.native_pipeline as pipeline
        import reporting_mirror.sql_staging as staging
        with self.pg() as pg:
            pg.execute('SET TRANSACTION READ ONLY')
            row=pg.execute("SELECT v.* FROM quality_control.xertoolkit_schedule_quality_profile p JOIN quality_control.xertoolkit_schedule_quality_config_version v ON v.config_version_id=p.active_config_version_id WHERE p.profile_code='default'").fetchone()
            if row['config_version_id']!=37 or row['state']!='active':raise ValueError('Expected the fixed settings version 37 active header')
        with ExitStack() as stack:
            for module in (pipeline,staging):
                stack.enter_context(patch.object(module,'staging_target',lambda:self.target))
                stack.enter_context(patch.object(module,'safe_stage_name',lambda code:private_database(SOURCE_DATABASES[code])))
                stack.enter_context(patch.object(module,'assert_staging',self.assert_sql))
            stack.enter_context(patch.object(pipeline,'postgres_connection',self.pg))
            pipeline.configure_sql_staging()
        # Original installer also normalizes two SELECT-star view contracts.
        with patch.object(staging,'staging_target',lambda:self.target),patch.object(staging,'safe_stage_name',lambda code:private_database(SOURCE_DATABASES[code])),patch.object(staging,'assert_staging',self.assert_sql):
            staging.normalize_reporting_contracts()
        # Four legacy compatibility thresholds are maintained by the original
        # SQL publication path as well as the versioned settings tables.
        with self.pg() as pg:
            pg.execute('SET TRANSACTION READ ONLY')
            options={r['option_code']:r['numeric_value'] for r in pg.execute('SELECT option_code,numeric_value FROM quality_control.xertoolkit_schedule_quality_option WHERE config_version_id=37').fetchall()}
        for database in SOURCE_DATABASES.values():
            with self.connect(database) as c:
                for name,value in (('High Duration',options['high_duration_days']),('High Float',options['high_float_days']),('Critical Float',0),('Near Critical',options['near_critical_upper_days'])):
                    self.query(c,'UPDATE powerbitables.xertoolkit_settings_thresholds SET setting_value=? WHERE setting_name=?; IF @@ROWCOUNT=0 INSERT INTO powerbitables.xertoolkit_settings_thresholds(setting_name,setting_value) VALUES(?,?)',(value,name,name,value))
        return {'config_version_id':37,'settings_hash':row['settings_hash'].strip(),'compatibility_thresholds_copied':4}

    def catalogue(self,database):
        with self.connect(database) as c:
            return self.query(c,"SELECT o.name,RTRIM(o.type) type,CONVERT(varchar(64),HASHBYTES('SHA2_256',CONVERT(varbinary(max),OBJECT_DEFINITION(o.object_id))),2) module_sha256 FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id WHERE s.name='powerbitables' AND o.type IN ('V','P','IF','FN','TF','TR') ORDER BY o.type,o.name")

    def prepare(self):
        result={'complete':False,'scope':'all_projects','restores':[],'contract_projection_normalization':['xertoolkit_vw_pbi_openends','xertoolkit_vw_pbi_relationshipquality'],'normalization_basis':'existing sql_staging.normalize_reporting_contracts; bind SELECT-star function columns by name'}
        result['restores'].append(self.restore('perf_oracle_seed22',SEED_PATH))
        for code,database in SOURCE_DATABASES.items():
            result['restores'].append(self.restore(database,'/backups/'+code+'.bak'))
            self.copy_definitions(database)
        result['settings']=self.settings()
        result['catalogues']={code:self.catalogue(database) for code,database in SOURCE_DATABASES.items()}
        result['complete']=True;result['passed']=True
        (self.directory/'legacy_sql_oracle_prepare.json').write_text(json.dumps(result,indent=2,default=str))
        return result

    def counts(self,connection):
        return self.query(connection,'SELECT (SELECT COUNT_BIG(*) FROM dbo.PROJECT) projects,(SELECT COUNT_BIG(*) FROM dbo.TASK) activities,(SELECT COUNT_BIG(*) FROM dbo.TASKPRED) relationships')[0]

    def calculate(self):
        from reporting_mirror.sql_staging import rebuild_quality
        import reporting_mirror.sql_staging as staging
        results={}
        with patch.object(staging,'staging_target',lambda:self.target),patch.object(staging,'safe_stage_name',lambda code:private_database(SOURCE_DATABASES[code])),patch.object(staging,'assert_staging',self.assert_sql):
            for code,database in SOURCE_DATABASES.items():
                with self.connect(database) as c:before=self.counts(c)
                started=time.perf_counter()
                source=SimpleNamespace(code=code,get_code_display=lambda c=code:c)
                result=rebuild_quality(source,lambda m:print('ORACLE_CALCULATING',code,flush=True))
                with self.connect(database) as c:
                    after=self.counts(c)
                    history=self.query(c,'SELECT TOP(1) requested_proj_id,status,processed_project_count,config_version_id FROM powerbitables.xertoolkit_refresh_run_history ORDER BY check_run_id DESC')[0]
                if before!=after or history['requested_proj_id'] is not None or history['processed_project_count']!=EXPECTED_PROJECTS[code] or history['config_version_id']!=37:
                    raise RuntimeError('Legacy all-project calculation changed input counts or did not cover every project')
                results[code]={'status':'success','seconds':time.perf_counter()-started,'core_counts_unchanged':True,'counts':after,'history':history,'evidence_count_checks_passed':result['evidence_count_checks_passed']}
                (self.directory/'legacy_sql_oracle_calculation.json').write_text(json.dumps({'complete':len(results)==2,'scope':'all_projects','sources':results},indent=2,default=str))
        return results

    def copy_view(self,pg,source,schema,name,columns):
        """Independent original pyodbc transport; final EXCEPT ALL is the oracle check."""
        from psycopg import sql
        from backups.services.mssql import quote_identifier as qi
        from reporting_mirror.sync import pg_column,pg_type
        from reporting_mirror.sql_staging import object_columns
        actual=object_columns(source,'powerbitables',name)
        layout=[{'name':pg_column(c['name']),'type':pg_type(c)} for c in actual]
        if layout!=columns:raise RuntimeError('Legacy SQL view column/type contract differs: '+name)
        definitions=sql.SQL(',').join(sql.SQL('{} {}').format(sql.Identifier(c['name']),sql.SQL(c['type'])) for c in columns)
        fields=sql.SQL(',').join(sql.Identifier(c['name']) for c in columns)
        pg.execute(sql.SQL('CREATE TABLE {}.{} ({})').format(sql.Identifier(schema),sql.Identifier(name),definitions))
        count=0;cursor=source.cursor()
        try:
            cursor.execute('SELECT '+','.join(qi(c['name']) for c in columns)+' FROM powerbitables.'+qi(name))
            with pg.cursor().copy(sql.SQL('COPY {}.{} ({}) FROM STDIN').format(sql.Identifier(schema),sql.Identifier(name),fields)) as writer:
                while True:
                    rows=cursor.fetchmany(10000)
                    if not rows:break
                    for row in rows:
                        writer.write_row(tuple(int(v) if isinstance(v,bool) else bytes(v) if isinstance(v,bytearray) else v for v in row));count+=1
        finally:cursor.close()
        copied=pg.execute(sql.SQL('SELECT count(*) n FROM {}.{}').format(sql.Identifier(schema),sql.Identifier(name))).fetchone()['n']
        if copied!=count:raise RuntimeError('SQL oracle transport row counts disagree')
        return count

    def compare_view(self,pg,oracle,reference,name):
        from psycopg import sql
        from compare_reports import column_layout
        a=column_layout(pg,oracle,name);b=column_layout(pg,reference,name)
        if a!=b:return {'view':name,'passed':False,'schema_equal':False}
        projection=oracle_projection(a)
        row=pg.execute(sql.SQL('''WITH a AS MATERIALIZED (SELECT {p} FROM {a}.{n} t), b AS MATERIALIZED (SELECT {p} FROM {b}.{n} t)
          SELECT (SELECT count(*) FROM a) sql_rows,(SELECT count(*) FROM b) postgres_rows,
          (SELECT count(*) FROM (SELECT * FROM a EXCEPT ALL SELECT * FROM b) d) sql_only_rows,
          (SELECT count(*) FROM (SELECT * FROM b EXCEPT ALL SELECT * FROM a) d) postgres_only_rows''').format(p=projection,a=sql.Identifier(oracle),b=sql.Identifier(reference),n=sql.Identifier(name))).fetchone()
        return {'view':name,'schema_equal':True,'passed':row['sql_rows']==row['postgres_rows'] and row['sql_only_rows']==row['postgres_only_rows']==0,'normalized_generated_columns':runtime_columns(a),'json_syntax_normalised':name.endswith('schedulequalitytaskevidence'),**row}

    def compare(self,references):
        from psycopg import sql
        from reporting_mirror.native_engine import CONTRACTS
        token=uuid.uuid4().hex[:16];result={'complete':False,'passed':False,'scope':'all_projects','settings_version':37,'basis':'independent_same_snapshot_legacy_SQL_replay','source_transport':'original_pyodbc_typed_COPY','contract_projection_normalization':['xertoolkit_vw_pbi_openends','xertoolkit_vw_pbi_relationshipquality'],'sources':{},'results':[],'generated_columns_only':sorted(RUNTIME_COLUMNS),'json_syntax_normalised':True}
        with self.pg() as pg:
            pg.execute("SET statement_timeout='30min'");pg.execute("SET work_mem='128MB'")
            for code,database in SOURCE_DATABASES.items():
                reference=references[code]
                if not SCHEMA_RE.fullmatch(reference) or not reference.endswith(code):raise ValueError('Expected a private same-source reference generation')
                oracle='native_shadow_'+token+'_'+code
                pg.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(oracle)))
                outputs={n:c for n,c in CONTRACTS['published_by_source'][code].items() if n.startswith('xertoolkit_vw_')}
                with self.connect(database) as source:
                    catalogue={r['name'].lower() for r in self.catalogue(database) if r['type']=='V'}
                    if catalogue!=set(outputs):raise RuntimeError('Legacy SQL view catalogue differs from the complete 20-view native contract')
                    for name,columns in outputs.items():
                        started=time.perf_counter();rows=self.copy_view(pg,source,oracle,name,columns)
                        comparison=self.compare_view(pg,oracle,reference,name);comparison.update(source=code,source_rows_transferred=rows,seconds=time.perf_counter()-started)
                        result['results'].append(comparison)
                        result['sources'][code]={'projects':EXPECTED_PROJECTS[code],'views_compared':sum(r['source']==code for r in result['results']),'reference_schema':reference,'oracle_schema':oracle}
                        (self.directory/'legacy_sql_equivalence.json').write_text(json.dumps(result,indent=2,default=str))
                        print('ORACLE_VIEW',code,name,comparison['passed'],flush=True)
                pg.commit()
        result['complete']=True;result['passed']=len(result['results'])==40 and all(r['passed'] for r in result['results'])
        (self.directory/'legacy_sql_equivalence.json').write_text(json.dumps(result,indent=2,default=str))
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--private',type=Path,default=Path('/bench/private.json'));parser.add_argument('--directory',type=Path,default=Path('/bench/artifacts'));sub=parser.add_subparsers(dest='phase',required=True)
    sub.add_parser('prepare');sub.add_parser('calculate');compare=sub.add_parser('compare');compare.add_argument('--reference22',required=True);compare.add_argument('--reference23',required=True)
    args=parser.parse_args();sys.path[:0]=['/app',str(Path(__file__).parent)]
    import django;django.setup()
    oracle=Oracle(args.private,args.directory)
    if args.phase=='prepare':r=oracle.prepare()
    elif args.phase=='calculate':
        sources=oracle.calculate();r={'complete':len(sources)==2,'passed':all(s.get('status')=='success' for s in sources.values()),'sources':sources}
    else:r=oracle.compare({'p6_22':args.reference22,'p6_23':args.reference23})
    print(json.dumps({'phase':args.phase,'complete':r.get('complete') if isinstance(r,dict) else None,'passed':r.get('passed') if isinstance(r,dict) else None}))
    if isinstance(r,dict) and r.get('passed') is False and r.get('complete'):raise SystemExit(1)

if __name__=='__main__':main()




