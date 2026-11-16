"""Join timing, full verification and strict report parity into one acceptance receipt."""
from pathlib import Path
import argparse,json


def summarize(directory):
 def read(name):return json.loads((directory/name).read_text())
 trials=[]
 for tag in ('trial2','trial3'):
  timed=read(tag+'_end_to_end.json');sources=[]
  for code in ('p6_22','p6_23'):
   receipt=read(tag+'_'+code+'.json')
   tables=receipt['tables']
   verified=len(tables)==21 and all(t['entry']['verification']['all_values_equal'] is True and t['entry']['rows']==t['entry']['verification']['rows_compared'] for t in tables)
   sources.append({'source':code,'rows':receipt['rows'],'tables':len(tables),'all_values_verified':verified,'transfer_seconds':receipt['seconds'],'calculation_seconds':timed['calculation']['sources'][code]['calculation_seconds']})
  trials.append({'tag':tag,'seconds':timed['total_seconds'],'latency_target_met':timed['latency_target_met'],'sources':sources,'snapshot_seconds':timed['snapshot_stages'],'publication_seconds':timed['publication_seconds']})
 raw=[read('calculate_parity_'+code+'.json') for code in ('p6_22','p6_23')]
 derived=read('derived_trial3_parity.json');reports=read('trial3_report_parity.json');unit=read('unit-tests.json');negative=read('native_verification_negative_control.json');indices=read('trial3_index_policy_parity.json')
 conditions={
  'both_clean_runs_within_180_seconds':all(r['latency_target_met'] for r in trials),
  'all_raw_values_verified_in_each_run':all(s['all_values_verified'] for r in trials for s in r['sources']),
  'independent_old_loader_reference_matches':all(r['passed'] for r in raw),
  'all_26_derived_reports_match':derived.get('complete') is True and derived['passed'] and len(derived['results'])==26,
  'all_40_contracts_and_2_selections_match':reports.get('complete') is True and reports['passed'] and len(reports['results'])==42,
  'original_raw_index_policy_preserved':indices['passed'],
  'regression_tests_pass':unit['passed'] and unit['skipped']==0,
  'same_row_count_corruption_rejected':negative['same_row_count_corruption_detected'] and negative['restored_full_value_verification']=='passed',
 }
 result={'accepted_for_isolated_fixed_snapshot':all(conditions.values()),'target_seconds':180,'baseline':read('baseline.json'),'timed_trials':trials,'conditions':conditions,'raw_and_control_reference_relations_compared':sum(len(r['comparisons']) for r in raw),'published_contracts_compared':40,'derived_views_compared':26,'selected_programmes':41,'raw_rows_per_full_run':sum(s['rows'] for s in trials[0]['sources']),'unit_tests':unit,'production_deployed':False,'scope_notes':['Fixed 16:32 UTC snapshot on 16 November 2026; settings version 37.','Full source-to-destination value verification is inside measured processing time.','Offline reference/report comparisons are additional acceptance QA outside processing time.','Fresh audit/history/checkpoint/retention integration and live contention are not represented by this prototype.','No production report pointers, schedules or calculation modules changed.','Exploratory trial1 needed an index-policy repair and is excluded from the accepted timing runs.']}
 (directory/'acceptance-summary.json').write_text(json.dumps(result,indent=2)+'\n')
 return result

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('directory',type=Path);a=p.parse_args();r=summarize(a.directory);print(json.dumps({'accepted_for_isolated_fixed_snapshot':r['accepted_for_isolated_fixed_snapshot'],'seconds':[t['seconds'] for t in r['timed_trials']],'conditions':r['conditions']},indent=2));raise SystemExit(0 if r['accepted_for_isolated_fixed_snapshot'] else 1)

