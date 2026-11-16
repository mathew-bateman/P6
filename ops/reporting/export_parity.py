"""Export inspected programme values with their independent SQL-match status."""
import argparse
import csv
import json
import os
from pathlib import Path
import sys

for parent in Path(__file__).resolve().parents:
    if (parent/'manage.py').is_file():
        sys.path.insert(0,str(parent))
        break


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparison',type=Path,required=True)
    args=parser.parse_args()
    evidence=json.loads(args.comparison.read_text())
    assert evidence['status']=='complete', 'The full comparison has not finished'
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','p6_backup_admin.settings')
    import django
    django.setup()
    from psycopg import sql
    from reporting_mirror.connections import postgres_connection
    from reporting_mirror.native_engine import CONTRACTS
    directory=args.comparison.parent
    metric_fields=[c['name'] for c in CONTRACTS['published_by_source']['p6_22']['xertoolkit_vw_pbi_projectmetrics']]
    check_fields=[c['name'] for c in CONTRACTS['published_by_source']['p6_22']['xertoolkit_vw_pbi_schedulequalityresults'] if c['name'] not in {'check_run_id','refreshed_at','calculated_date'}]
    common=['source_system','proj_short_name','lead_planner','portfolio','project_state','project_status',
        'submission_updated_date','is_latest_client_submitted','old_sql_values_match']
    exporters={}
    with (directory/'programme-metrics.csv').open('w',newline='') as metrics,(directory/'programme-checks.csv').open('w',newline='') as checks,postgres_connection() as pg:
        pg.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        assert str(pg.execute('SELECT batch_id FROM reporting.refresh_status').fetchone()['batch_id'])==evidence['publication']['batch_id']
        for name,stream,fields in [('xertoolkit_vw_pbi_projectmetrics',metrics,metric_fields),('xertoolkit_vw_pbi_schedulequalityresults',checks,check_fields)]:
            writer=csv.DictWriter(stream,fieldnames=common+fields);writer.writeheader()
            count=0
            for code in evidence['sources']:
                schema='load_'+evidence['publication']['batch_id'].replace('-','')[:16]+'_'+code
                comparison=next(c for c in evidence['comparisons'] if c['source']==code and c['view']==name and c['scope']=='full_history')
                verified={g['proj_id']:g['matched'] for g in comparison['project_groups']}
                projects=next(c for c in evidence['comparisons'] if c['source']==code and c['view']=='xertoolkit_vw_pbi_projects' and c['scope']=='full_history')
                project_verified={g['proj_id']:g['matched'] for g in projects['project_groups']}
                statement=sql.SQL('''SELECT m.*,p.proj_short_name,p."Lead Planner" lead_planner,
                  p.account portfolio,p."Project State" project_state,p."Project Status" project_status,
                  p."Updated Date" submission_updated_date,
                  EXISTS(SELECT 1 FROM {s}._latest_client_submitted_projects_v1 chosen WHERE chosen.proj_id=m.proj_id) is_latest_client_submitted
                  FROM {s}.{name} m JOIN {s}.xertoolkit_vw_pbi_projects p USING(proj_id)
                  ORDER BY m.proj_id''').format(s=sql.Identifier(schema),name=sql.Identifier(name))
                with pg.cursor(name='export_'+code+'_'+name[-12:]) as cursor:
                    cursor.itersize=2000;cursor.execute(statement)
                    for row in cursor:
                        item={f:row[f] for f in fields}
                        item.update(source_system=code,proj_short_name=row['proj_short_name'],
                            lead_planner=row['lead_planner'],portfolio=row['portfolio'],
                            project_state=row['project_state'],project_status=row['project_status'],
                            submission_updated_date=row['submission_updated_date'],
                            is_latest_client_submitted=row['is_latest_client_submitted'],
                            old_sql_values_match=verified.get(row['proj_id'],False) and project_verified.get(row['proj_id'],False))
                        writer.writerow(item);count+=1
            exporters[name]=count
    result={'exports':exporters,'value_source':'Pinned PostgreSQL generation',
        'old_sql_values_match':'Every compared business value in the corresponding project/view group matches the independently executed original SQL.',
        'power_bi_dax_checked':False}
    (directory/'exports.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


if __name__=='__main__':
    main()
