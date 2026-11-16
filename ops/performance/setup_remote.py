"""Create benchmark-only containers and private configuration; never alter live settings."""
import json, pathlib, secrets, subprocess, sys
BASE = pathlib.Path('/home/mat/p6-performance-20261116')
BASE.mkdir(mode=0o700, exist_ok=True)
for name in ('reference', 'artifacts', 'sqlbackups', 'trial'):
    (BASE/name).mkdir(exist_ok=True)

def run(args, **kw):
    return subprocess.run(args, check=True, **kw)

def output(args):
    return subprocess.check_output(args, text=True).strip()

state = BASE/'private.json'
if state.exists():
    cfg=json.loads(state.read_text())
else:
    cfg={'postgres':{'host':'p6-perf-postgres','port':5432,'dbname':'p6_perf_trial','user':'postgres','password':secrets.token_urlsafe(30)}, 'staging':{'host':'p6-perf-sql','port':1433,'username':'sa','password':'P6!'+secrets.token_urlsafe(30),'hostname':'p6-perf-sql'}}
    cfg['postgres'].update(reader_user='postgres',reader_password=cfg['postgres']['password'],admin_password=cfg['postgres']['password'])
    state.write_text(json.dumps(cfg));state.chmod(0o600)
(BASE/'pg.env').write_text('POSTGRES_PASSWORD='+cfg['postgres']['password']+'\nPOSTGRES_DB=p6_perf_trial\n')
(BASE/'sql.env').write_text('ACCEPT_EULA=Y\nMSSQL_PID=Express\nMSSQL_SA_PASSWORD='+cfg['staging']['password']+'\n')
(BASE/'pg.env').chmod(0o600);(BASE/'sql.env').chmod(0o600)
net='p6-perf-isolated'
if subprocess.run(['docker','network','inspect',net],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
    run(['docker','network','create',net],stdout=subprocess.DEVNULL)
for name,image,opts in [
 ('p6-perf-postgres','postgres:17-bookworm',['--memory=6g','--cpus=2','--shm-size=256m','--env-file',str(BASE/'pg.env'),'-v','p6-perf-pgdata:/var/lib/postgresql/data']),
 ('p6-perf-sql','mcr.microsoft.com/mssql/server:2025-latest',['--hostname=p6-perf-sql','--memory=4g','--cpus=2','--env-file',str(BASE/'sql.env'),'-v','p6-perf-sqldata:/var/opt/mssql','-v',str(BASE/'sqlbackups')+':/backups:ro'])]:
    if subprocess.run(['docker','inspect',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
        command=['docker','run','-d','--name',name,'--network',net,*opts,image]
        if name=='p6-perf-postgres': command+=['-c','shared_buffers=1GB','-c','work_mem=16MB','-c','maintenance_work_mem=256MB']
        run(command,stdout=subprocess.DEVNULL)
    print(name,'ready/start requested',flush=True)
# A source-only archive, with no .env, private configuration or live SQLite files.
source='/home/mat/Docker/P6-Standby/app'
run(['tar','-C',source,'--exclude=__pycache__','--exclude=*.pyc','--exclude=*.bak*','--exclude=.codex-live-backups','--exclude=*.sqlite3','--exclude=.env','-cf',str(BASE/'reference.tar'),'manage.py','p6_backup_admin','graph','backups','reporting_mirror','templates','sql'])
run(['tar','-C',str(BASE/'reference'),'-xf',str(BASE/'reference.tar')])
print('SOURCE_COPY_COMPLETE',flush=True)
# Dump current published reporting database consistently; only read production PG.
with (BASE/'reference.dump').open('wb') as f:
    run(['docker','exec','p6-reporting-postgres','pg_dump','-U','postgres','-Fc','-Z','1','p6_reporting'],stdout=f)
print('PG_DUMP_COMPLETE',flush=True)

