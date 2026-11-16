"""Provision isolated, private reporting containers; never changes P6 SQL containers."""
import json,pathlib,secrets,subprocess,datetime,time
ROOT=pathlib.Path('/home/mat/Docker/P6-Reporting')
STANDBY=pathlib.Path('/home/mat/Docker/P6-Standby')
ROOT.mkdir(mode=0o700,exist_ok=True)
secret_path=STANDBY/'reporting-secrets.json'
if secret_path.exists():
 config=json.loads(secret_path.read_text())
else:
 config={'postgres':{'host':'p6-reporting-postgres','port':5432,'dbname':'p6_reporting','user':'p6_reporting_sync','password':secrets.token_urlsafe(36),'reader_user':'p6_reporting_reader','reader_password':secrets.token_urlsafe(36),'admin_password':secrets.token_urlsafe(36)},'staging':{'host':'p6-reporting-sql-stage','port':1433,'username':'sa','password':secrets.token_urlsafe(36)+'aA1!','hostname':'xps4-reporting-stage'},'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 secret_path.write_text(json.dumps(config,indent=2));secret_path.chmod(0o600)
pg=config['postgres'];stage=config['staging']
env=ROOT/'containers.env'
env.write_text('POSTGRES_PASSWORD='+pg['admin_password']+'\nMSSQL_SA_PASSWORD='+stage['password']+'\n')
env.chmod(0o600)
def literal(s):return "'"+s.replace("'","''")+"'"
init=ROOT/'postgres-init.sql'
init.write_text('CREATE ROLE p6_reporting_sync LOGIN PASSWORD '+literal(pg['password'])+' NOSUPERUSER NOCREATEDB NOCREATEROLE;\nCREATE ROLE p6_reporting_reader LOGIN PASSWORD '+literal(pg['reader_password'])+' NOSUPERUSER NOCREATEDB NOCREATEROLE;\nCREATE DATABASE p6_reporting OWNER p6_reporting_sync;\nREVOKE ALL ON DATABASE p6_reporting FROM PUBLIC;\nGRANT CONNECT ON DATABASE p6_reporting TO p6_reporting_reader;\n\\connect p6_reporting\nREVOKE CREATE ON SCHEMA public FROM PUBLIC;\nALTER ROLE p6_reporting_reader SET default_transaction_read_only=on;\nALTER ROLE p6_reporting_reader SET statement_timeout=\'60s\';\n')
init.chmod(0o600)
compose={'services':{
 'postgres':{'image':'postgres@sha256:858bb5e98eed02eb8cafbcdc007f9a65d1b613972028944a8ec2133ccaf80d27','container_name':'p6-reporting-postgres','hostname':'p6-reporting-postgres','restart':'unless-stopped','env_file':['containers.env'],'environment':{'POSTGRES_USER':'postgres','POSTGRES_DB':'postgres','TZ':'UTC'},'volumes':['postgres-data:/var/lib/postgresql/data'],'networks':['p6-standby'],'mem_limit':'6g','cpus':2,'shm_size':'256m','command':['postgres','-c','shared_buffers=1GB','-c','work_mem=16MB','-c','maintenance_work_mem=256MB','-c','max_connections=40','-c','timezone=UTC'],'healthcheck':{'test':['CMD-SHELL',"psql -U postgres -d p6_reporting -tAc 'SELECT 1'"],'interval':'10s','timeout':'5s','retries':12}},
 'sql-stage':{'image':'mcr.microsoft.com/mssql/server@sha256:245f6b1c8903edae52cb20fa31419cefcde1a49938bb8c3447099011fc871ca3','container_name':'p6-reporting-sql-stage','hostname':'xps4-reporting-stage','restart':'unless-stopped','env_file':['containers.env'],'environment':{'ACCEPT_EULA':'Y','MSSQL_PID':'Express','MSSQL_MEMORY_LIMIT_MB':'3072','TZ':'UTC'},'volumes':['sql-stage-data:/var/opt/mssql',str(STANDBY/'backups')+':/source-backups:ro'],'networks':['p6-standby'],'mem_limit':'4g','cpus':2,'healthcheck':{'test':['CMD-SHELL','SQLCMDPASSWORD="$MSSQL_SA_PASSWORD" /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -C -b -Q "SELECT 1" >/dev/null'],'interval':'30s','timeout':'10s','start_period':'30s','retries':6}}},
 'volumes':{'postgres-data':{},'sql-stage-data':{}},'networks':{'p6-standby':{'external':True,'name':'p6-standby'}}}
(ROOT/'compose.json').write_text(json.dumps(compose,indent=2))
p=subprocess.run(['docker','compose','-p','p6-reporting','-f',str(ROOT/'compose.json'),'up','-d'],cwd=ROOT,capture_output=True,text=True)
if p.returncode:raise RuntimeError(p.stderr[-2000:])
for attempt in range(30):
 check=subprocess.run(['docker','exec','p6-reporting-postgres','psql','-U','postgres','-d','postgres','-Atc',"SELECT 1 FROM pg_database WHERE datname='p6_reporting'"],capture_output=True,text=True)
 if check.returncode==0:break
 time.sleep(1)
if check.returncode:raise RuntimeError('PostgreSQL did not become ready for initialization.')
if check.returncode==0 and check.stdout.strip()!='1':
 setup=subprocess.run(['docker','exec','-i','p6-reporting-postgres','psql','-U','postgres','-v','ON_ERROR_STOP=1','-d','postgres'],input=init.read_text(),capture_output=True,text=True)
 if setup.returncode:raise RuntimeError('PostgreSQL reporting role/database initialization failed; inspect restricted bootstrap logs.')
print(json.dumps({'provisioned':True,'postgres_private_only':True,'staging_private_only':True,'memory_limits_gb':{'postgres':6,'sql_stage':4},'cpus_each':2,'secret_file_created_without_output':True}))
