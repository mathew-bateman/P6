"""Accept only full all-project/historical P6 refresh trials with complete QA.

Earlier latest-only timing receipts can never satisfy this policy. Missing or
incomplete evidence produces a failed acceptance receipt instead of success.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path

SOURCES=('p6_22','p6_23')
EXPECTED_PROJECTS={'p6_22':820,'p6_23':2384}
RAW_TABLES=frozenset(n.lower() for n in ('PROJECT','PROJWBS','TASK','TASKPRED','TASKRSRC','RSRC','CALENDAR','ACTVCODE','ACTVTYPE','TASKACTV','PROJPCAT','PCATTYPE','PCATVAL','UDFTYPE','UDFVALUE','RSRCRATE','ROLES','TASKMEMO','OBS','POBS','OBSPROJ'))
COVERAGE={'published_contracts':20,'raw_tables':21,'settings_tables':6,'result_tables':4,'function_tables':6,'deleted_discovery_tables':1,'selection_tables':1}
KIND_COUNTS={'published_contract':20,'raw':21,'settings':6,'result':4,'function_materialization':6,'deleted_discovery':1,'selection':1}
GENERATED=frozenset({'check_run_id','refreshed_at','calculated_date'})


def complete_pass(receipt,count=None):
    results=receipt.get('results',[])
    return (receipt.get('complete') is True and receipt.get('passed') is True
            and (count is None or len(results)==count)
            and all(r.get('passed') is True for r in results))


def full_generation(receipt,source):
    results=receipt.get('results',[])
    return (complete_pass(receipt,59) and receipt.get('scope')=='all_projects'
        and receipt.get('source')==source and receipt.get('coverage')==COVERAGE
        and receipt.get('all_project_count')==EXPECTED_PROJECTS[source]
        and receipt.get('reference_project_count')==EXPECTED_PROJECTS[source]
        and receipt.get('metadata_exclusions')==[]
        and receipt.get('publication_filter_applied') is False
        and Counter(r.get('kind') for r in results)==Counter(KIND_COUNTS)
        and all(r.get('publication_scoped') is False and r.get('normalized_metadata_columns')==[] for r in results))


def historical_availability(receipt):
    if not (complete_pass(receipt,40) and receipt.get('scope')=='all_projects'
        and receipt.get('metadata_exclusions')==[] and receipt.get('publication_filter_applied') is False
        and receipt.get('all_project_count')==sum(EXPECTED_PROJECTS.values())
        and receipt.get('combined_inventory_passed') is True and receipt.get('combined_history_relations')==47
        and len(receipt.get('column_layout_checks',[]))==94
        and all(r.get('passed') is True for r in receipt.get('column_layout_checks',[]))):return False
    sources=receipt.get('sources',{})
    if set(sources)!=set(SOURCES):return False
    for source,r in sources.items():
        if not (r.get('all_projects')==EXPECTED_PROJECTS[source]
            and r.get('calculated_project_metrics')==EXPECTED_PROJECTS[source]
            and r.get('history_relations')==47
            and r.get('nonlatest_project_metrics')==r['all_projects']-r['latest_projects']>0
            and r.get('nonlatest_evidence_rows',0)>0):return False
    checks=receipt.get('reader_checks',[])
    expected={(role,source) for role in ('p6_reporting_reader','p6_reporting_external') for source in (*SOURCES,'combined')}
    return (len(checks)==6 and {(r.get('role'),r.get('source')) for r in checks}==expected
        and all(r.get('passed') is True and r.get('scope')=='all_projects' for r in checks)
        and all(r.get('nonlatest_quality_rows',0)>0 and r.get('nonlatest_evidence_rows',0)>0 for r in checks if r.get('source')!='combined'))


def legacy_sql_equivalence(receipt,calculation):
    results=receipt.get('results',[])
    return (complete_pass(receipt,40) and receipt.get('scope')=='all_projects'
        and receipt.get('basis')=='independent_same_snapshot_legacy_SQL_replay'
        and receipt.get('settings_version')==37
        and Counter(r.get('source') for r in results)==Counter({'p6_22':20,'p6_23':20})
        and all(set(r.get('normalized_generated_columns',[]))<=GENERATED for r in results)
        and calculation.get('complete') is True and calculation.get('scope')=='all_projects'
        and set(calculation.get('sources',{}))==set(SOURCES)
        and all(r.get('status')=='success' and r.get('core_counts_unchanged') is True
            and r.get('evidence_count_checks_passed')==10
            and r.get('history',{}).get('requested_proj_id') is None
            and r.get('history',{}).get('processed_project_count')==EXPECTED_PROJECTS[source]
            and r.get('history',{}).get('config_version_id')==37
            for source,r in calculation.get('sources',{}).items()))


def raw_verification(receipt,source):
    tables=receipt.get('tables',[])
    names={t.get('entry',{}).get('name') for t in tables}
    return (receipt.get('status')=='verified' and receipt.get('method')=='ordered_arrow_exact_v1'
        and len(tables)==21 and names==RAW_TABLES
        and receipt.get('rows')==sum(t.get('entry',{}).get('rows',0) for t in tables)
        and all(t.get('entry',{}).get('source')==source
            and t['entry'].get('verification',{}).get('all_values_equal') is True
            and t['entry']['rows']==t['entry']['verification'].get('rows_compared')
            and t['entry']['verification'].get('method')=='ordered_arrow_exact_v1'
            for t in tables))


def summarize(directory,tags,qa_tag=None,min_runs=2):
    missing=[]
    def read(name):
        path=directory/name
        if not path.is_file():missing.append(name);return {}
        return json.loads(path.read_text())
    qa_tag=qa_tag or tags[-1]
    trials=[]
    for tag in tags:
        timed=read(tag+'_end_to_end.json');sources=[]
        all_scope=(timed.get('calculation_scope')=='all_projects' and timed.get('project_scope_restricted') is False)
        for source in SOURCES:
            transfer=read(tag+'_'+source+'.json')
            calculation=timed.get('calculation',{}).get('sources',{}).get(source,{})
            quality=calculation.get('quality',{})
            project_count=quality.get('processed_project_count')
            sources.append({'source':source,'all_values_verified':raw_verification(transfer,source),
                'raw_rows':transfer.get('rows'),'project_count':project_count,
                'all_projects_processed':project_count==EXPECTED_PROJECTS[source],
                'evidence_reconciliations':quality.get('evidence_count_checks_passed'),
                'transfer_seconds':transfer.get('seconds'),'calculation_seconds':calculation.get('calculation_seconds')})
        seconds=timed.get('total_seconds')
        trials.append({'tag':tag,'seconds':seconds,'scope_all_projects':all_scope,
            'latency_target_met':isinstance(seconds,(int,float)) and 0<seconds<=180,
            'sources':sources,'snapshot_seconds':timed.get('snapshot_stages'),
            'publication_seconds':timed.get('publication_seconds')})
    full={source:read('all_scope_parity_'+qa_tag+'_'+source+'.json') for source in SOURCES}
    history=read('historical_availability_'+qa_tag+'.json')
    filtered=read('all_scope_filtered_parity_'+qa_tag+'.json')
    derived=read('all_scope_derived_parity_'+qa_tag+'.json')
    inventory=read('all_projects_legacy_view_inventory.json')
    indices=read('all_scope_index_policy_'+qa_tag+'.json')
    sql=read('legacy_sql_equivalence.json');sql_calculation=read('legacy_sql_oracle_calculation.json')
    units=read('unit-tests.json');negative=read('native_verification_negative_control.json')
    conditions={
        'at_least_two_clean_all_project_runs':len(trials)>=min_runs and len(tags)==len(set(tags)) and all(t['scope_all_projects'] for t in trials),
        'all_measured_runs_within_180_seconds':all(t['latency_target_met'] for t in trials),
        'all_raw_values_verified_inside_each_timed_run':all(s['all_values_verified'] for t in trials for s in t['sources']),
        'all_3204_projects_calculated_each_run':all(s['all_projects_processed'] and s['evidence_reconciliations']==10 for t in trials for s in t['sources']),
        'all_118_unfiltered_reference_relations_match':all(full_generation(full[source],source) for source in SOURCES),
        'all_historical_results_reader_accessible':historical_availability(history),
        'existing_40_filtered_contracts_and_2_selections_match':complete_pass(filtered,42) and filtered.get('metadata_exclusions')==[],
        'existing_26_derived_views_match':complete_pass(derived,26) and derived.get('metadata_exclusions')==[],
        'all_20_legacy_SQL_view_names_retained':inventory.get('complete') is True and inventory.get('passed') is True and inventory.get('legacy_view_count')==20,
        'same_snapshot_legacy_SQL_results_match':legacy_sql_equivalence(sql,sql_calculation),
        'original_raw_indexes_valid_and_ready':complete_pass(indices,42),
        'regression_tests_pass_without_skips':units.get('passed') is True and units.get('skipped')==0,
        'same_count_corruption_rejected_and_restored':negative.get('same_row_count_corruption_detected') is True and negative.get('restored_full_value_verification')=='passed',
        'all_required_evidence_present':not missing,
    }
    result={'accepted_for_all_project_fixed_snapshot':all(conditions.values()),'target_seconds':180,
        'calculation_scope':'all_projects','expected_project_count':sum(EXPECTED_PROJECTS.values()),
        'expected_projects_by_source':EXPECTED_PROJECTS,'timed_trials':trials,'qa_tag':qa_tag,
        'conditions':conditions,'missing_evidence':missing,'production_deployed':False,
        'earlier_latest_only_runs_accepted_for_this_request':False,
        'scope_notes':['All historical programmes and quality/evidence outputs are required.','The earlier 93/97-second latest-only runs do not satisfy this requirement.','Every timed run includes complete source-to-destination raw-value verification.','Full reference/legacy SQL/report comparisons are additional offline QA.','The SQL oracle uses the same fixed 16:32 source snapshot and settings version 37.','Only independent SQL run IDs/timestamps and JSON object syntax may be normalized in the legacy SQL oracle; current PG reference comparisons exclude no fields.','This is isolated fixed-snapshot acceptance, not a production deployment or future runtime guarantee.']}
    (directory/'all-projects-acceptance-summary.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);p.add_argument('--tags',nargs='+',required=True);p.add_argument('--qa-tag');p.add_argument('--minimum-runs',type=int,default=2);a=p.parse_args()
    if not a.tags or any(not t.replace('_','').isalnum() for t in a.tags):raise SystemExit('Use explicit simple all-project trial tags')
    r=summarize(a.directory,a.tags,a.qa_tag,a.minimum_runs)
    print(json.dumps({'accepted_for_all_project_fixed_snapshot':r['accepted_for_all_project_fixed_snapshot'],'conditions':r['conditions'],'seconds':[t['seconds'] for t in r['timed_trials']],'missing_evidence':r['missing_evidence']},indent=2))
    raise SystemExit(0 if r['accepted_for_all_project_fixed_snapshot'] else 1)


