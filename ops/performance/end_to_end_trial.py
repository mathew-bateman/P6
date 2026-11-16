"""Time a complete private refresh: SQL capture/restore, native copy+verify,
calculation and atomic view publication. Offline report parity is separate.
"""
from __future__ import annotations
import argparse,json,sys,time,uuid
from pathlib import Path
from types import SimpleNamespace
sys.path[:0]=['/app',str(Path(__file__).parent)]
import django;django.setup()
from psycopg import sql
from backups.services.mssql import connect,quote_identifier as qi
from reporting_mirror.sql_staging import query,RAW_TABLES
from reporting_mirror.connections import postgres_connection
from reporting_mirror.sync import metadata_select,ensure_combined_project_indexes
from native_bulk.arrow_transfer import run as native_transfer
from calculate_trial import trial


def main(tag):
 cfg=json.loads(Path('/bench/private.json').read_text())
 pgcfg={k:cfg['postgres'][k] for k in ('host','port','dbname','user','password')}
 s=cfg['staging']; target=SimpleNamespace(sql_host=s['host'],sql_port=s['port'],sql_username=s['username'],sql_password=s['password'],odbc_driver='ODBC Driver 18 for SQL Server',encrypt_connection=True,trust_server_certificate=True)
 with postgres_connection() as pg:
  assert pg.execute('SELECT current_database() n').fetchone()['n']=='p6_perf_trial'
  baseline=pg.execute('SELECT * FROM reporting.refresh_status').fetchone()
 token=uuid.uuid4().hex[:16];result={'tag':tag,'token':token,'snapshot_stages':{},'source_counts':{},'fully_isolated':True}
 total=time.perf_counter()
 for code in ('p6_22','p6_23'):
  started=time.perf_counter();db='perf_stage_'+token[:8]+'_'+code;file='/var/opt/mssql/data/perf_capture_'+token+'_'+code+'.bak'
  with connect(target) as c:
   c.timeout=600
   query(c,'BACKUP DATABASE '+qi('perf_'+code)+" TO DISK=N'"+file+"' WITH COPY_ONLY,INIT,CHECKSUM")
   query(c,"RESTORE VERIFYONLY FROM DISK=N'"+file+"' WITH CHECKSUM")
   files=query(c,"RESTORE FILELISTONLY FROM DISK=N'"+file+"'")
   moves=[]
   for n,f in enumerate(files):
    p='/var/opt/mssql/data/'+db+'_'+str(n)+('.ldf' if f['Type']=='L' else '.mdf')
    moves.append("MOVE N'"+f['LogicalName'].replace("'","''")+"' TO N'"+p+"'")
   query(c,'RESTORE DATABASE '+qi(db)+" FROM DISK=N'"+file+"' WITH CHECKSUM,RECOVERY,"+','.join(moves))
   counts=query(c,'SELECT (SELECT COUNT_BIG(*) FROM '+qi(db)+'.dbo.PROJECT) projects,(SELECT COUNT_BIG(*) FROM '+qi(db)+'.dbo.TASK) activities,(SELECT COUNT_BIG(*) FROM '+qi(db)+'.dbo.TASKPRED) relationships')[0]
  result['source_counts'][code]=counts
  result['snapshot_stages'][code]=time.perf_counter()-started
  # Private source connection, no production secrets or endpoints.
  sourcecs=f"Server={s['host']},{s['port']};Database={db};UID={s['username']};PWD={s['password']};Encrypt=yes;TrustServerCertificate=yes;"
  tcfg={'benchmark_only':True,'postgres':pgcfg,'source_connection_string':sourcecs,'schema':'native_shadow_'+token+'_'+code,'source_code':code,'tables':sorted(RAW_TABLES),'workers':1,'batch_size':50000}
  receipt=native_transfer(tcfg)
  copied={r['entry']['name']:r['entry']['rows'] for r in receipt['tables']}
  if any(copied[t]!=counts[m] for t,m in (('project','projects'),('task','activities'),('taskpred','relationships'))):
   raise RuntimeError('Core source counts did not reconcile after full verification')
  Path('/bench/artifacts/'+tag+'_'+code+'.json').write_text(json.dumps(receipt,indent=2))
  print('TRANSFER_VERIFIED',code,round(receipt['seconds'],3),receipt['rows'],flush=True)
 result['calculation']=trial(compare=False,artifact_prefix=tag,output_prefix=tag+'_calculate')
 # Only the private source views change. Preserve baseline metadata so exact
 # report comparisons are possible. Existing public/UI/derived views reference
 # these source views and switch together in one committed transaction.
 started=time.perf_counter()
 with postgres_connection() as pg:
  assert pg.execute('SELECT current_database() n').fetchone()['n']=='p6_perf_trial'
  manifest=pg.execute('SELECT manifest FROM sync_audit.batches WHERE batch_id=%s',(baseline['batch_id'],)).fetchone()['manifest']
  schemas={code:r['candidate_schema'] for code,r in result['calculation']['sources'].items()}
  ensure_combined_project_indexes(pg,schemas,manifest)
  for code in ('p6_22','p6_23'):
   source=SimpleNamespace(code=code,target=SimpleNamespace(sql_database=baseline['sources'][code]['database']))
   for entry in manifest:
    if entry['source']!=code:continue
    stmt=metadata_select(schemas[code],entry,source,baseline['sources'][code],baseline['published_at'],baseline['batch_id'])
    pg.execute(sql.SQL('CREATE OR REPLACE VIEW {}.{} AS {}').format(sql.Identifier(code),sql.Identifier(entry['name']),stmt))
 result['publication_seconds']=time.perf_counter()-started
 result['total_seconds']=time.perf_counter()-total
 result['target_seconds']=180;result['latency_target_met']=result['total_seconds']<=180
 Path('/bench/artifacts/'+tag+'_end_to_end.json').write_text(json.dumps(result,indent=2,default=str))
 print('FULL_REFRESH_RESULT',json.dumps({k:result[k] for k in ('tag','total_seconds','publication_seconds','target_seconds','latency_target_met')}),flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('tag');a=p.parse_args();assert a.tag.replace('_','').isalnum();main(a.tag)



