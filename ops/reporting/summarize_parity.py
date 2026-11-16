"""Package full SQL/PostgreSQL comparison evidence without changing databases."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import zipfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparison',type=Path,required=True)
    args=parser.parse_args()
    root=args.comparison.parent
    evidence=json.loads(args.comparison.read_text())
    source_count=len(evidence['sources'])
    assert evidence['status']=='complete' and len(evidence['comparisons'])==40*source_count
    assert all(c['matched'] for c in evidence['comparisons']), 'Unresolved value differences require review'
    combined=json.loads((root/'combined-view-proof.json').read_text())
    pg_schema=json.loads((root/'postgres-schema-proof.json').read_text())
    assert combined['all_matched'] and combined['matched_views']==combined['total_views']==20
    assert pg_schema['postgres_structures_matched']==pg_schema['postgres_structures_checked']==40*source_count
    assert combined['batch_id']==pg_schema['batch_id']==evidence['publication']['batch_id']
    known={
        ('p6_22','xertoolkit_vw_pbi_openends'):{'is_milestone','in_open_start_scope','in_open_finish_scope'},
        ('p6_22','xertoolkit_vw_pbi_relationshipquality'):{'is_excessive_ss_eligible','is_excessive_ff_eligible'},
    }
    notes=[]
    with (root/'view-comparison.csv').open('w',newline='') as stream:
        fields=['source_system','view','full_history_matches','sql_full_rows','postgres_full_rows',
            'latest_published_matches','sql_latest_rows','postgres_latest_rows','column_names_and_types',
            'extra_internal_sql_columns','missing_sql_columns','different_types','projected_column_order_matches']
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for code,source in evidence['sources'].items():
            assert source['transaction_rolled_back']
            for schema in source['schema_comparisons']:
                name=schema['view']
                full=next(c for c in evidence['comparisons'] if c['source']==code and c['view']==name and c['scope']=='full_history')
                latest=next(c for c in evidence['comparisons'] if c['source']==code and c['view']==name and c['scope']=='latest_published')
                if not schema['matched']:
                    assert not schema['missing_columns'] and not schema['different_types']
                    assert set(schema['extra_columns'])==known[(code,name)], 'Unexplained legacy schema difference'
                    notes.append(dict(source=code,view=name,extra_internal_sql_columns=schema['extra_columns']))
                expected=list(schema['postgres_types'])
                projected=[n for n in schema['sql_types'] if n in schema['postgres_types']]
                writer.writerow(dict(source_system=code,view=name,full_history_matches=full['matched'],
                    sql_full_rows=full['rows'][0],postgres_full_rows=full['rows'][1],
                    latest_published_matches=latest['matched'],sql_latest_rows=latest['rows'][0],postgres_latest_rows=latest['rows'][1],
                    column_names_and_types='Match' if schema['matched'] else 'Known SELECT-star internal fields',
                    extra_internal_sql_columns='; '.join(schema['extra_columns']),
                    missing_sql_columns='; '.join(schema['missing_columns']),different_types=json.dumps(schema['different_types']),
                    projected_column_order_matches=projected==expected))
    assert len(notes)==2, 'The documented schema-note count changed'
    archive=Path('/app/work/sql-parallel-20261025/sql-definitions-before.json')
    plan=Path('/app/.recovery/20261116T1016Z-pre-sql-retirement/retirement-plan.json')
    expected={r['name']:r['definition_sha256'] for r in json.loads(plan.read_text())['catalogue']['objects'] if r['type']!='U'}
    with (root/'legacy-module-hashes.csv').open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['module','retirement_sha256','executed_body_sha256','matches'])
        count=0
        for row in json.loads(archive.read_text()):
            if row.get('schema_name')=='powerbitables' and row['name'] in expected:
                digest=hashlib.sha256(row['definition'].encode()).hexdigest()
                assert digest==expected[row['name']]
                writer.writerow([row['name'],expected[row['name']],digest,True]);count+=1
        assert count==36
    overview=dict(status='Business values match; two documented legacy schema projections',
        full_history_comparisons=20*source_count,full_history_matches=20*source_count,latest_comparisons=20*source_count,latest_matches=20*source_count,
        programme_records=sum(s['raw_counts']['projects'] for s in evidence['sources'].values()),
        latest_programmes=sum(len(s['latest_selection']['sql_ids']) for s in evidence['sources'].values()),
        legacy_schema_notes=notes,sql_reference_transactions_rolled_back=True,database_restore_performed=False,
        power_bi_dax_checked=False,publication=evidence['publication'],
        exclusions=evidence['runtime_columns_excluded'],
        original_sql_reference='36 archived module bodies match retirement hashes; only CREATE/ALTER prefixes adapted.',
        reference_binding='Archived definitions compiled afresh. Historical stale SELECT-star ordinal bindings are not replayed.',
        scope='P6 22 legacy SQL reporting only. P6 23 new functionality excluded at user request.')
    (root/'run-details.json').write_text(json.dumps(overview,default=str,indent=2))
    (root/'READ-ME.txt').write_text('''P6 22 OLD SQL / POSTGRESQL FULL COMPARISON - 16 NOVEMBER 2026

All 40 business-value comparisons passed: 20 P6 22 views, checked
against full history and again against the latest submitted public programmes.
The combined reporting layer's P6 22 subset was checked against its source views.

VIEW-COMPARISON.CSV contains every view comparison and its row counts.
PROGRAMME-METRICS.CSV contains all 820 P6 22 project records and count columns.
PROGRAMME-CHECKS.CSV contains the 16,400 scored check-input records. Soft
constraints are informational and are included in the programme-metrics file.
Filter is_latest_client_submitted=True to inspect the 29 current programmes.
The exported values come from PostgreSQL; old_sql_values_match records their
independent, complete project/view-group match against the original SQL.

Two P6 22 SELECT-star wrappers (OpenEnds and RelationshipQuality) expose extra
internal eligibility fields when rebuilt. PostgreSQL preserves the published
business-column contract. Extra fields are documented in view-comparison.csv.
Original definitions were compiled afresh; stale historical ordinal bindings
are not reproduced. Literal SELECT-star schema equality is not claimed.

Only check_run_id, refreshed_at and calculated_date are excluded from value
comparison. Added PostgreSQL lineage fields are outside the legacy contract.
Numeric driver representations and JSON syntax/property ordering normalize;
text, NULLs, array order and duplicate row counts remain significant.

All 36 SQL module bodies match the retirement hashes. The reference ran inside
rolled-back transactions on the matching staging snapshots. No backup restore,
live P6 writes, reporting-engine switch or report publication was performed.
P6 23 new functionality is excluded from this legacy comparison.
Power BI DAX and visual behaviour were not checked.

RUN-DETAILS.JSON identifies the exact publication and snapshot times.
COMPARISON.JSON retains all row counts and per-programme comparison digests.
POSTGRES-SCHEMA-PROOF.JSON and COMBINED-VIEW-PROOF.JSON record independent audits.
''')
    package=root/'old-new-sql-comparison-p6-22-20261116.zip'
    include=['READ-ME.txt','view-comparison.csv','programme-metrics.csv','programme-checks.csv',
        'legacy-module-hashes.csv','run-details.json','comparison.json','postgres-schema-proof.json',
        'combined-view-proof.json','exports.json','view-summary.csv','p6_22-latest-selection.sql']
    with zipfile.ZipFile(package,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as bundle:
        for name in include:bundle.write(root/name,arcname=name)
    print(json.dumps({'package':str(package),'bytes':package.stat().st_size,
        'business_value_comparisons_matched':len(evidence['comparisons']),'legacy_schema_notes':len(notes)}))


if __name__=='__main__':
    main()
