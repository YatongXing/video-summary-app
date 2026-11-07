"""Independent WP04 structure, immutable ownership and maintenance boundary."""
import hashlib
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg import sql
import pytest
from cr_wp04_fixtures import wp04_db,queued,object_asset

def test_four_states_and_removed_shadow_controls(wp04_db):
 db=wp04_db;u,t=queued(db)
 cols={x[0] for x in db.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='cleanup_jobs'")}
 assert {'job_version','consecutive_failures','online_completed_at','last_physical_started_at','blocked_reason'}<=cols
 assert not {'worker_safe_after','manual_requested','backup_cleanup_state','backup_completed_at','last_attempt_at','claim_token'}&cols
 for state in ('RUNNING','MANUAL_REQUIRED','invented'):
  with pytest.raises(psycopg.Error):
   db.execute("INSERT INTO cleanup_jobs(task_id,user_id,status) VALUES(%s,%s,%s)",(t,u,state))

@pytest.mark.parametrize('field,value',[('job_version',-1),('consecutive_failures',-1),('attempt_count',-1)])
def test_negative_control_rejected(wp04_db,field,value):
 db=wp04_db;u,t=queued(db)
 with pytest.raises(psycopg.Error):
  db.execute(sql.SQL("INSERT INTO cleanup_jobs(task_id,user_id,{}) VALUES(%s,%s,%s)").format(sql.Identifier(field)),(t,u,value))

@pytest.mark.parametrize('field',['task_id','user_id','storage_key','asset_kind','storage_backend','storage_bucket','storage_version'])
def test_locator_cannot_be_retargeted(wp04_db,field):
 db=wp04_db;u,t=queued(db);u2,t2=queued(db);aid=object_asset(db,u,t)
 value={'task_id':t2,'user_id':u2,'storage_key':'originals/other','asset_kind':'BACKUP','storage_backend':'S3:unknown','storage_bucket':'other','storage_version':'other'}[field]
 with pytest.raises(psycopg.Error):
  db.execute(sql.SQL("UPDATE media_assets SET {}=%s WHERE id=%s").format(sql.Identifier(field)),(value,aid))
 assert db.execute("SELECT task_id,user_id FROM media_assets WHERE id=%s",(aid,)).fetchone()==(t,u)

def test_runtime_cannot_insert_or_update_backup_approval(wp04_db):
 db=wp04_db;u,t=queued(db);role='wp04_runtime_'+uuid4().hex
 schema=db.execute("SELECT current_schema()").fetchone()[0]
 aid=object_asset(db,u,t,kind='BACKUP')
 try:
  db.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE").format(sql.Identifier(role)))
  db.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema),sql.Identifier(role)))
  db.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA {} TO {}").format(sql.Identifier(schema),sql.Identifier(role)))
  db.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
  with pytest.raises(psycopg.Error):db.execute("UPDATE media_assets SET cleanup_approved_at=clock_timestamp() WHERE id=%s",(aid,))
  with pytest.raises(psycopg.Error):db.execute("INSERT INTO media_assets(task_id,user_id,asset_kind,storage_key,cleanup_approved_at) VALUES(%s,%s,'BACKUP',%s,clock_timestamp())",(t,u,'backup/'+uuid4().hex))
 finally:
  db.execute("RESET ROLE")
  db.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
  db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

def test_frozen_migration_checksums(wp04_db):
 root=Path(__file__).parents[1]/'app'/'migrations'
 rows=wp04_db.execute("SELECT version,sha256 FROM schema_migrations ORDER BY version").fetchall()
 assert len(rows)==11
 for version,digest in rows:assert hashlib.sha256((root/version).read_bytes()).hexdigest()==digest

def test_completed_requires_verified_online_timestamp(wp04_db):
 db=wp04_db;u,t=queued(db)
 with pytest.raises(psycopg.Error):db.execute("INSERT INTO cleanup_jobs(task_id,user_id,status,completed_at) VALUES(%s,%s,'COMPLETED',clock_timestamp())",(t,u))

def test_blocked_requires_explicit_reason(wp04_db):
 db=wp04_db;u,t=queued(db)
 with pytest.raises(psycopg.Error):db.execute("INSERT INTO cleanup_jobs(task_id,user_id,status) VALUES(%s,%s,'BLOCKED')",(t,u))

def test_old_cleanup_mapping_preserves_counts_and_first_time(wp04_db,monkeypatch,tmp_path):
 import os,shutil
 from app import migrate
 from psycopg.conninfo import make_conninfo
 root=migrate.MIGRATIONS_DIR;legacy=tmp_path/'legacy';legacy.mkdir()
 for path in root.glob('*.sql'):
  if path.name<'011':shutil.copyfile(path,legacy/path.name)
 schema='wp04_legacy_'+uuid4().hex
 wp04_db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
 try:
  with psycopg.connect(make_conninfo(os.environ['TEST_DATABASE_URL'],options='-csearch_path='+schema),autocommit=True) as db:
   monkeypatch.setattr(migrate,'MIGRATIONS_DIR',legacy);migrate.run_migrations(db)
   cases=[('PENDING',None,'PENDING',None),('RUNNING',None,'BLOCKED','PRODUCER_UNKNOWN'),('COMPLETED',None,'PENDING',None),('MANUAL_REQUIRED','UNKNOWN_ASSET','BLOCKED','UNSAFE'),('MANUAL_REQUIRED','ASSET_CLEANUP_FAILED','RETRY_WAIT',None),('MANUAL_REQUIRED',None,'PENDING',None)]
   tids=[]
   for status,err,expected,reason in cases:
    u,t=queued(db);tids.append(t)
    db.execute("INSERT INTO cleanup_jobs(task_id,user_id,status,attempt_count,started_at,last_attempt_at,completed_at,last_error_code) VALUES(%s,%s,%s,7,clock_timestamp()-interval '31 days',clock_timestamp()-interval '10 seconds',CASE WHEN %s='COMPLETED' THEN clock_timestamp() ELSE NULL END,%s)",(t,u,status,status,err))
   before=db.execute('SELECT task_id,started_at FROM cleanup_jobs').fetchall()
   monkeypatch.setattr(migrate,'MIGRATIONS_DIR',root);migrate.run_migrations(db)
   for task,case in zip(tids,cases):
    status,reason,count,completed,n,next_at,last_at=db.execute('SELECT status,blocked_reason,attempt_count,completed_at,consecutive_failures,next_attempt_at,last_physical_started_at FROM cleanup_jobs WHERE task_id=%s',(task,)).fetchone()
    assert (status,reason,count,completed)==(case[2],case[3],7,None)
    assert n==7
    if case[2]=='RETRY_WAIT':assert next_at>=last_at+__import__('datetime').timedelta(seconds=60)
   assert db.execute('SELECT task_id,started_at FROM cleanup_jobs').fetchall()==before
 finally:wp04_db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
