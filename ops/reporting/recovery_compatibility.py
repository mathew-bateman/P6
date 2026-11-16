"""Keep copied P6BackupAdmin recovery slots independent of XPS reporting services."""
import ast,datetime,hashlib,json,pathlib,sqlite3,tempfile

FUNCTION='''def disable_postgres_reporting_on_recovery(connection):
 # The recovery estate retains the original SQL report path. PostgreSQL is
 # rebuildable reporting infrastructure on XPS4, not part of SQL failover.
 if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reporting_mirror_reportingconfig'").fetchone():
  connection.execute('UPDATE reporting_mirror_reportingconfig SET enabled=0,serve_reports=0')
  columns={row[1] for row in connection.execute('PRAGMA table_info(reporting_mirror_reportingconfig)')}
  if 'calculation_engine' in columns:connection.execute("UPDATE reporting_mirror_reportingconfig SET calculation_engine='sql',manage_settings_in_postgres=0,pending_engine=''")
  if 'sql_parallel_enabled' in columns:connection.execute('UPDATE reporting_mirror_reportingconfig SET sql_parallel_enabled=0')
 connection.execute("UPDATE django_celery_beat_periodictask SET enabled=0 WHERE task='reporting_mirror.tasks.refresh_reporting'")

'''
namespace={};exec(FUNCTION,namespace)
with sqlite3.connect(':memory:') as db:
 db.executescript("CREATE TABLE reporting_mirror_reportingconfig(enabled int,serve_reports int);INSERT INTO reporting_mirror_reportingconfig VALUES(1,1);CREATE TABLE django_celery_beat_periodictask(task text,enabled int);INSERT INTO django_celery_beat_periodictask VALUES('reporting_mirror.tasks.refresh_reporting',1),('backups.tasks.execute_target_backup',1);")
 namespace['disable_postgres_reporting_on_recovery'](db)
 assert db.execute('SELECT enabled,serve_reports FROM reporting_mirror_reportingconfig').fetchone()==(0,0)
 assert db.execute("SELECT enabled FROM django_celery_beat_periodictask WHERE task='backups.tasks.execute_target_backup'").fetchone()[0]==1
 assert db.execute("SELECT enabled FROM django_celery_beat_periodictask WHERE task='reporting_mirror.tasks.refresh_reporting'").fetchone()[0]==0
with sqlite3.connect(':memory:') as db:
 db.executescript("CREATE TABLE reporting_mirror_reportingconfig(enabled int,serve_reports int,calculation_engine text,manage_settings_in_postgres int,pending_engine text,sql_parallel_enabled int);INSERT INTO reporting_mirror_reportingconfig VALUES(1,1,'postgres',1,'postgres',1);CREATE TABLE django_celery_beat_periodictask(task text,enabled int);INSERT INTO django_celery_beat_periodictask VALUES('reporting_mirror.tasks.refresh_reporting',1),('backups.tasks.execute_target_backup',1);")
 namespace['disable_postgres_reporting_on_recovery'](db)
 assert db.execute('SELECT enabled,serve_reports,calculation_engine,manage_settings_in_postgres,pending_engine,sql_parallel_enabled FROM reporting_mirror_reportingconfig').fetchone()==(0,0,'sql',0,'',0)
 assert db.execute("SELECT enabled FROM django_celery_beat_periodictask WHERE task='backups.tasks.execute_target_backup'").fetchone()[0]==1
p=pathlib.Path('/home/mat/P6-Continuity/scripts/controller.py')
original=p.read_text()
if 'def disable_postgres_reporting_on_recovery(' in original:
 tree=ast.parse(original)
 node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='disable_postgres_reporting_on_recovery')
 lines=original.splitlines(keepends=True)
 updated=''.join(lines[:node.lineno-1])+FUNCTION+''.join(lines[node.end_lineno:])
else:
 needle=" with sqlite3.connect(database) as connection:\n  enabled="
 assert original.count(needle)==1
 updated=original.replace('def make_slot(',FUNCTION+'def make_slot(',1).replace(needle," with sqlite3.connect(database) as connection:\n  disable_postgres_reporting_on_recovery(connection)\n  enabled=",1)
compile(updated,str(p),'exec')
backup=p.with_name('controller.pre-parallel-sql-reporting-20261025.py')
if not backup.exists():backup.write_text(original);backup.chmod(0o700)
temporary=p.with_suffix('.reporting.tmp');temporary.write_text(updated);temporary.chmod(0o700);temporary.replace(p)
print(json.dumps({'recovery_compatibility_patched':True,'SQL_jobs_preserved':True,'Postgres_reporting_disabled_before_promotion_state_is_recorded':True,'checks_passed':3,'controller_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))
