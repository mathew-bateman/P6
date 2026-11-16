"""Compare the retired SQL reports with one pinned PostgreSQL publication.

Run inside the application container. All SQL reference objects and result rows
exist only inside staging transactions that are rolled back. No report engine
switch, P6 source write, database restore or report publication occurs.
"""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
for parent in Path(__file__).resolve().parents:
    if (parent/'manage.py').is_file():
        sys.path.insert(0, str(parent))
        break


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sources',nargs='+',choices=['p6_22','p6_23'],default=['p6_22'])
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    import os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'p6_backup_admin.settings')
    import django
    django.setup()
    from legacy_oracle import prepare_reference_connection
    from parity_compare import compare_cursor_rows, difference_samples, RUNTIME_COLUMNS
    from psycopg import sql
    from backups.services.mssql import connect, quote_identifier as qi
    from reporting_mirror.connections import postgres_connection, staging_target
    from reporting_mirror.sql_staging import query, safe_stage_name, assert_staging, object_columns
    from reporting_mirror.native_engine import CONTRACTS
    from reporting_mirror.sync import pg_type

    results = {'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat(),
        'runtime_columns_excluded': sorted(RUNTIME_COLUMNS), 'sources': {}, 'comparisons': []}

    def progress(phase, **details):
        event = dict(at=datetime.now(timezone.utc).isoformat(), phase=phase, **details)
        (output/'progress.json').write_text(json.dumps(event, default=str, indent=2))
        with (output/'events.jsonl').open('a') as stream:
            stream.write(json.dumps(event, default=str)+'\n')
        print(json.dumps(event, default=str), flush=True)

    def save():
        temporary = output/'comparison.new.json'
        temporary.write_text(json.dumps(results, default=str, indent=2))
        temporary.replace(output/'comparison.json')

    def sql_rows(connection, statement):
        cursor = connection.cursor()
        try:
            cursor.execute(statement)
            while True:
                rows = cursor.fetchmany(2000)
                if not rows:
                    break
                yield from rows
        finally:
            cursor.close()

    def pg_rows(pg, schema, name, columns, where_ids=None):
        statement = sql.SQL('SELECT {} FROM {}.{} v').format(
            sql.SQL(',').join(sql.Identifier(c['name']) for c in columns),
            sql.Identifier(schema), sql.Identifier(name))
        parameters = None
        if where_ids is not None:
            statement += sql.SQL(' WHERE proj_id=ANY(%s)')
            parameters = (where_ids,)
        with pg.cursor(name='parity_'+str(len(results['comparisons']))+'_'+schema[:12]) as cursor:
            cursor.itersize = 2000
            cursor.execute(statement, parameters)
            yield from cursor

    lock = postgres_connection(autocommit=True)
    try:
        assert lock.execute('SELECT pg_try_advisory_lock(%s) acquired', (620252210,)).fetchone()['acquired'], 'A reporting refresh is active'
        with postgres_connection() as pg:
            pg.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            pg.execute("SET LOCAL statement_timeout='15min'")
            publication = pg.execute('SELECT * FROM reporting.refresh_status').fetchone()
            results['publication'] = publication
            base = 'load_'+str(publication['batch_id']).replace('-','')[:16]
            priority = ['xertoolkit_vw_pbi_projectmetrics','xertoolkit_vw_pbi_schedulequalityresults',
                'xertoolkit_vw_pbi_projects','xertoolkit_vw_pbi_schedulequalitytaskevidence',
                'xertoolkit_vw_pbi_activityqualityvalidation']
            for code in args.sources:
                schema = base+'_'+code
                stage = connect(staging_target(), database=safe_stage_name(code))
                stage.timeout = 1800
                source = {'database': safe_stage_name(code), 'transaction_rolled_back': False}
                results['sources'][code] = source
                try:
                    assert_staging(stage)
                    source['before_objects'] = query(stage, "SELECT name,RTRIM(type) type FROM sys.objects WHERE schema_id=SCHEMA_ID('powerbitables') ORDER BY name")
                    counts = query(stage, 'SELECT (SELECT COUNT_BIG(*) FROM dbo.PROJECT) projects,(SELECT COUNT_BIG(*) FROM dbo.TASK) activities,(SELECT COUNT_BIG(*) FROM dbo.TASKPRED) relationships')[0]
                    assert counts == publication['sources'][code]['counts'], 'Staging snapshot counts differ from the published snapshot'
                    source['raw_counts'] = counts
                    query(stage, 'BEGIN TRANSACTION')
                    progress('preparing_sql_reference', source=code)
                    source['reference'] = prepare_reference_connection(stage, code, progress=lambda message: progress('preparing_sql_reference',source=code,message=message))
                    settings = query(stage, "SELECT v.config_version_id,v.settings_hash,v.state FROM powerbitables.xertoolkit_schedule_quality_profile p JOIN powerbitables.xertoolkit_schedule_quality_config_version v ON v.config_version_id=p.active_config_version_id WHERE p.profile_code='default'")[0]
                    native_settings = pg.execute(sql.SQL("SELECT v.config_version_id,v.settings_hash,v.state FROM {}.xertoolkit_schedule_quality_profile p JOIN {}.xertoolkit_schedule_quality_config_version v ON v.config_version_id=p.active_config_version_id WHERE p.profile_code='default'").format(sql.Identifier(schema),sql.Identifier(schema))).fetchone()
                    assert settings == native_settings, 'SQL and PostgreSQL settings differ'
                    source['settings'] = settings
                    source['sql_session_id'] = query(stage, 'SELECT @@SPID id')[0]['id']
                    save()
                    progress('calculating_original_sql', source=code, sql_session_id=source['sql_session_id'])
                    query(stage, "EXEC powerbitables.xertoolkit_refresh_all_schedule_quality @trigger_type='manual'")
                    source['refresh'] = query(stage, 'SELECT TOP(1) status,processed_project_count,config_version_id FROM powerbitables.xertoolkit_refresh_run_history ORDER BY check_run_id DESC')[0]
                    assert source['refresh']['status']=='success'
                    assert query(stage, 'SELECT @@TRANCOUNT n')[0]['n']==1, 'Legacy procedure escaped the comparison transaction'
                    progress('original_sql_calculated', source=code, refresh=source['refresh'])

                    # Independently rank submitted programmes in the SQL engine.
                    selection_sql = """
WITH assignments AS (
 SELECT DISTINCT a.proj_id,LOWER(LTRIM(RTRIM(t.proj_catg_type))) kind,
  t.proj_catg_type_id,v.proj_catg_id,LOWER(LTRIM(RTRIM(v.proj_catg_name))) value
 FROM dbo.PROJPCAT a JOIN dbo.PCATTYPE t ON t.proj_catg_type_id=a.proj_catg_type_id
 JOIN dbo.PCATVAL v ON v.proj_catg_type_id=a.proj_catg_type_id AND v.proj_catg_id=a.proj_catg_id
 WHERE a.delete_date IS NULL AND a.delete_session_id IS NULL AND t.delete_date IS NULL
  AND t.delete_session_id IS NULL AND v.delete_date IS NULL AND v.delete_session_id IS NULL
), codes AS (
 SELECT proj_id,
 COUNT(CASE WHEN kind='project id' THEN 1 END) pcount,
 MAX(CASE WHEN kind='project id' THEN CONVERT(varchar(20),proj_catg_type_id)+':'+CONVERT(varchar(20),proj_catg_id) END) pid,
 COUNT(CASE WHEN kind='client project id' THEN 1 END) ccount,
 MAX(CASE WHEN kind='client project id' THEN CONVERT(varchar(20),proj_catg_type_id)+':'+CONVERT(varchar(20),proj_catg_id) END) cid,
 COUNT(CASE WHEN kind IN('project status','projects status') THEN 1 END) statuses,
 SUM(CASE WHEN kind IN('project status','projects status') AND ISNULL(value,'')<>'client submitted' THEN 1 ELSE 0 END) other_statuses
 FROM assignments GROUP BY proj_id
), candidates AS (
 SELECT p.proj_id,CASE WHEN c.pcount=1 THEN 'code:'+c.pid WHEN c.pcount=0 AND c.ccount=1 THEN 'code:'+c.cid ELSE 'project:'+CONVERT(varchar(20),p.proj_id) END identity_key,
  d.[Updated Date] updated_date,d.data_date,p.update_date
 FROM dbo.PROJECT p JOIN codes c ON c.proj_id=p.proj_id AND c.statuses>0 AND c.other_statuses=0
 JOIN powerbitables.xertoolkit_vw_PBI_Projects d ON d.proj_id=p.proj_id
 WHERE p.project_flag='Y' AND ISNULL(p.orig_proj_id,0)=0 AND p.delete_date IS NULL AND p.delete_session_id IS NULL
), ranked AS (
 SELECT *,ROW_NUMBER() OVER(PARTITION BY identity_key ORDER BY updated_date DESC,data_date DESC,update_date DESC,proj_id DESC) rn FROM candidates
)
SELECT proj_id FROM ranked WHERE rn=1 ORDER BY proj_id
"""
                    (output/(code+'-latest-selection.sql')).write_text(selection_sql)
                    selected = [r['proj_id'] for r in query(stage, selection_sql)]
                    native_selected = [r['proj_id'] for r in pg.execute(sql.SQL('SELECT proj_id FROM {}._latest_client_submitted_projects_v1 ORDER BY proj_id').format(sql.Identifier(schema))).fetchall()]
                    source['latest_selection'] = {'matched': selected==native_selected, 'sql_ids':selected, 'postgres_ids':native_selected}
                    assert selected==native_selected, 'Independent SQL latest programme selection differs'
                    query(stage, 'CREATE TABLE #LatestSubmitted(proj_id int NOT NULL PRIMARY KEY)')
                    for identifier in selected:
                        query(stage, 'INSERT INTO #LatestSubmitted VALUES(?)', (identifier,))
                    contracts = {n:c for n,c in CONTRACTS['published_by_source'][code].items() if n.startswith('xertoolkit_vw_')}
                    assert len(contracts)==20
                    names = sorted(contracts,key=lambda n:(priority.index(n) if n in priority else len(priority),n))
                    for name in names:
                        contract = [c for c in contracts[name] if c['name'] not in RUNTIME_COLUMNS]
                        actual_columns = object_columns(stage,'powerbitables',name)
                        actual_names = {c['name'] if ' ' in c['name'] else c['name'].lower() for c in actual_columns}
                        expected_names = {c['name'] for c in contracts[name]}
                        missing = sorted(expected_names-actual_names)
                        extra = sorted(actual_names-expected_names)
                        sql_types = {c['name'] if ' ' in c['name'] else c['name'].lower():pg_type(c) for c in actual_columns}
                        postgres_types = {c['name']:c['type'] for c in contracts[name]}
                        different_types = {field:[sql_types[field],postgres_types[field]] for field in expected_names & actual_names if sql_types[field]!=postgres_types[field]}
                        source.setdefault('schema_comparisons',[]).append({'view':name,'missing_columns':missing,'extra_columns':extra,
                            'sql_types':sql_types,'postgres_types':postgres_types,'different_types':different_types,
                            'matched':not missing and not extra and not different_types})
                        if missing:
                            results['comparisons'].append({'source':code,'view':name,'scope':'full_history','matched':False,'error':'Missing SQL columns: '+str(missing)})
                            save();continue
                        fields = ','.join('v.'+qi(c['name']) for c in contract)
                        full_sql = 'SELECT '+fields+' FROM powerbitables.'+qi(name)+' v'
                        progress('comparing_full_history', source=code, view=name)
                        comparison = compare_cursor_rows(sql_rows(stage,full_sql),pg_rows(pg,schema,name,contract),contract)
                        comparison.update(source=code,view=name,scope='full_history')
                        results['comparisons'].append(comparison)
                        progress('view_compared',source=code,view=name,scope='full_history',matched=comparison['matched'],rows=comparison['rows'],affected_projects=len(comparison['affected_project_ids']))
                        if not comparison['matched']:
                            ids = comparison['affected_project_ids'][:10]
                            restrict = ' WHERE v.proj_id IN ('+','.join(str(int(i)) for i in ids)+')' if ids and 'proj_id' in expected_names else ''
                            comparison['difference_samples'] = difference_samples(sql_rows(stage,full_sql+restrict),
                                pg_rows(pg,schema,name,contract,ids if restrict else None),contract,limit=12,scratch_directory=output)
                        if 'proj_id' in expected_names:
                            published_sql = full_sql+' WHERE EXISTS(SELECT 1 FROM #LatestSubmitted selected WHERE selected.proj_id=v.proj_id)'
                        else:
                            published_sql = full_sql
                        progress('comparing_published_scope',source=code,view=name)
                        published = compare_cursor_rows(sql_rows(stage,published_sql),pg_rows(pg,code,name,contract),contract)
                        published.update(source=code,view=name,scope='latest_published')
                        if not published['matched']:
                            published['difference_samples'] = difference_samples(sql_rows(stage,published_sql),pg_rows(pg,code,name,contract),contract,limit=12,scratch_directory=output)
                        results['comparisons'].append(published)
                        progress('view_compared',source=code,view=name,scope='latest_published',matched=published['matched'],rows=published['rows'],affected_projects=len(published['affected_project_ids']))
                        save()
                finally:
                    try:
                        query(stage, 'IF @@TRANCOUNT>0 ROLLBACK TRANSACTION')
                    finally:
                        stage.close()
                    with connect(staging_target(),database=safe_stage_name(code)) as check:
                        after = query(check,"SELECT name,RTRIM(type) type FROM sys.objects WHERE schema_id=SCHEMA_ID('powerbitables') ORDER BY name")
                        source['transaction_rolled_back'] = after==source['before_objects']
                        assert source['transaction_rolled_back'], 'SQL reference objects remain after rollback'
                    save()
            assert pg.execute('SELECT batch_id FROM reporting.refresh_status').fetchone()['batch_id']==publication['batch_id']
        results['status'] = 'complete'
        results['matched_comparisons'] = sum(c['matched'] for c in results['comparisons'])
        results['total_comparisons'] = len(results['comparisons'])
        schemas = [s for source in results['sources'].values() for s in source.get('schema_comparisons',[])]
        results['matched_schemas'] = sum(s['matched'] for s in schemas)
        results['total_schemas'] = len(schemas)
        expected_values=40*len(args.sources)
        expected_schemas=20*len(args.sources)
        results['all_matched'] = results['total_comparisons']==expected_values and results['matched_comparisons']==expected_values and results['matched_schemas']==expected_schemas
    except Exception as error:
        results['status']='failed';results['error']=str(error)
        (output/'error.txt').write_text(traceback.format_exc())
        progress('comparison_failed',error=str(error))
        raise
    finally:
        results['finished_at']=datetime.now(timezone.utc).isoformat();save()
        with (output/'view-summary.csv').open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=['source','view','scope','matched','sql_rows','postgres_rows','affected_projects'])
            writer.writeheader()
            for c in results['comparisons']:
                writer.writerow(dict(source=c['source'],view=c['view'],scope=c['scope'],matched=c['matched'],
                    sql_rows=c.get('rows',[None,None])[0],postgres_rows=c.get('rows',[None,None])[1],affected_projects=len(c.get('affected_project_ids',[]))))
        try:lock.execute('SELECT pg_advisory_unlock(%s)',(620252210,))
        finally:lock.close()
    progress('comparison_complete',all_matched=results.get('all_matched'),matched=results.get('matched_comparisons'),total=results.get('total_comparisons'))


if __name__ == '__main__':
    main()
