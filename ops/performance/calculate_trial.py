"""Calculate and compare a private full-size candidate against the cloned baseline."""
from __future__ import annotations
import argparse,json,sys,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path[:0]=['/app',str(Path(__file__).parent)]
import django
django.setup()
from psycopg import sql
from reporting_mirror.connections import postgres_connection
from reporting_mirror import native_engine
from reporting_mirror.native_settings import copy_settings,active_version
from reporting_mirror.latest_submission import prepare_latest_submissions
from reporting_mirror.native_pipeline import describe_control
from reporting_mirror.sql_staging import RAW_TABLES
from scope_trial import calculate_scoped
from compare_reports import compare_generations


def trial(*,scope=True,source_filter=None,compare=True,artifact_prefix='arrow',output_prefix='calculate'):
    result={'scope':scope,'sources':{},'input_snapshot':'2026-11-16T16:32Z','fully_isolated':True}
    with postgres_connection() as pg:
        assert pg.execute('SELECT current_database() n').fetchone()['n']=='p6_perf_trial'
        pg.execute("SET statement_timeout='30min'")
        reference=pg.execute('SELECT * FROM reporting.refresh_status').fetchone()
        version=active_version(pg)['config_version_id']
        for code,snapshot in reference['sources'].items():
            if source_filter and code!=source_filter: continue
            artifact=Path('/bench/artifacts/'+artifact_prefix+'_'+code+'.json')
            transfer=json.loads(artifact.read_text())
            assert transfer['status']=='verified'
            candidate=transfer['schema']
            ref='load_'+reference['batch_id'].hex[:16]+'_'+code
            fixed=pg.execute(sql.SQL('SELECT min(refreshed_at) n FROM {}.xertoolkit_result_project_metrics').format(sql.Identifier(ref))).fetchone()['n']
            class FrozenDateTime(datetime):
                @classmethod
                def now(cls,tz=None):
                    return fixed.replace(tzinfo=timezone.utc).astimezone(tz) if tz else fixed
            prefix=f'{code} '
            def progress(message): print(prefix+message,flush=True)
            start=time.perf_counter()
            copy_settings(pg,candidate,version)
            with patch.object(native_engine,'datetime',FrozenDateTime):
                if scope:
                    manifest,quality=calculate_scoped(pg,candidate,version,snapshot['quality']['check_run_id'],progress=progress)
                else:
                    manifest,quality=native_engine.calculate(pg,candidate,version,snapshot['quality']['check_run_id'],progress=progress)
            orphan=pg.execute(sql.SQL('SELECT count(*) n FROM {}.task t LEFT JOIN {}.project p USING(proj_id) WHERE p.proj_id IS NULL').format(sql.Identifier(candidate),sql.Identifier(candidate))).fetchone()['n']
            if orphan!=snapshot['orphan_activity_project_links']:
                raise RuntimeError('Candidate orphan-link count differs from fixed snapshot')
            control_manifest=describe_control(pg,SimpleNamespace(code=code),candidate)
            prepare_latest_submissions(pg,candidate)
            pg.commit()
            seconds=time.perf_counter()-start
            raw=[t['entry'] for t in transfer['tables']]
            entry={'candidate_schema':candidate,'reference_schema':ref,'calculation_seconds':seconds,'transfer_seconds':transfer['seconds'],'quality':quality,'manifest':raw+manifest+control_manifest,'orphan_activity_project_links':orphan,'fixed_calculated_at':fixed.isoformat()}
            result['sources'][code]=entry
            Path('/bench/artifacts/'+output_prefix+'_'+code+'.json').write_text(json.dumps(entry,indent=2,default=str))
            print('CALCULATION_COMPLETE',code,round(seconds,3),flush=True)
            if compare:
                started=time.perf_counter()
                comparisons=compare_generations(pg,ref,candidate,code,native_engine.CONTRACTS,RAW_TABLES,normalize_run_metadata=False)
                passed=all(r['passed'] for r in comparisons)
                receipt={'passed':passed,'seconds':time.perf_counter()-started,'comparisons':comparisons}
                Path('/bench/artifacts/'+output_prefix+'_parity_'+code+'.json').write_text(json.dumps(receipt,indent=2,default=str))
                failed=[r['reference'] for r in comparisons if not r['passed']]
                print('PARITY',code,passed,'relations',len(comparisons),'failed',failed,flush=True)
                if not passed: raise RuntimeError('Strict full-value parity failed: '+str(failed))
    result['timed_transfer_and_calculation_seconds']=sum(s['transfer_seconds']+s['calculation_seconds'] for s in result['sources'].values())
    Path('/bench/artifacts/'+output_prefix+'_result.json').write_text(json.dumps(result,indent=2,default=str))
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source');parser.add_argument('--unscoped',action='store_true');parser.add_argument('--skip-compare',action='store_true');a=parser.parse_args()
    trial(scope=not a.unscoped,source_filter=a.source,compare=not a.skip_compare)


