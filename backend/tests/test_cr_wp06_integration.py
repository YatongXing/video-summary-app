"""WP06 actual loopback HTTP + independent Workers + PG + private S3."""
import json,os,time,subprocess,statistics,math
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import httpx,psycopg,pytest,boto3
S=json.loads(Path(os.environ["WP06_STATE"]).read_text(encoding="utf-8")) if os.environ.get("WP06_STATE") else None
pytestmark=pytest.mark.skipif(S is None,reason="Explicit WP06 synthetic state required")
def wait(fn,timeout=60):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  v=fn()
  if v:return v
  time.sleep(.2)
 raise AssertionError("Synthetic condition timeout")
def docker(*args):
 r=subprocess.run(["docker",*args],capture_output=True,text=True,timeout=45)
 assert r.returncode==0,r.stderr[-1000:]
 return r.stdout
def owned(name):
 assert name in [S["containers"]["db"],S["containers"]["s3"],S["containers"]["api"],*S["containers"]["workers"]]
 labels=json.loads(docker("inspect","--format","{{json .Config.Labels}}",name))
 assert labels["vsa.work_package"]=="CR-WP06" and labels["vsa.synthetic_only"]=="true"
@pytest.fixture(scope="module")
def lab():
 assert S["db_url"].split("/")[-1].startswith("wp06")
 with psycopg.connect(S["db_url"],autocommit=True) as db:
  db.execute("UPDATE users SET credits_available=3 WHERE role='USER'")
 clients=[]
 for a in S["accounts"]:
  c=httpx.Client(base_url=S["api_url"] if os.getenv("WP06_DIRECT") else S["url"],timeout=60,trust_env=False)
  if os.getenv("WP06_DIRECT"):
   c.event_hooks["request"].append(lambda r:setattr(r,"url",r.url.copy_with(path=r.url.path.removeprefix("/api"))))
  r=c.post("/api/auth/login",json={"email":a["email"],"password":a["password"]})
  assert r.status_code==200,r.text
  c.headers["X-CSRF-Token"]=r.json()["csrf_token"];clients.append(c)
 tasks=[]
 for c in clients:
  r=c.post("/api/inputs/upload",content=Path(S["samples"]["short"]).read_bytes(),headers={"X-Upload-Authorized":"true","X-Video-Filename":"wp06-synthetic.mp4"})
  assert r.status_code==201,r.text
  tid=r.json()["id"];tasks.append(tid)
  wait(lambda:any(t["id"]==tid and t["status"]=="SUCCEEDED" for t in c.get("/api/tasks").json()))
 yield clients,tasks
 for c in clients:c.close()
def test_actual_pipeline_owner_json_and_range(lab):
 clients,tasks=lab
 for c,t in zip(clients,tasks):
  for lang in ("zh","en"):
   r=c.get(f"/api/tasks/{t}/result",params={"language":lang});assert r.status_code==200
   assert r.json()["language"]==lang and r.json()["offline_test"] is True
   e=c.get(f"/api/tasks/{t}/export",params={"language":lang});assert e.status_code==200 and e.json()["language"]==lang
  v=c.get(f"/api/tasks/{t}/video",headers={"Range":"bytes=0-31"})
  assert v.status_code==206 and len(v.content)==32 and v.headers["content-range"].startswith("bytes 0-31/")
 assert clients[2].get(f"/api/tasks/{tasks[0]}/result").status_code==404
 assert clients[0].get(f"/api/tasks/{tasks[1]}/result").status_code==404
def test_actual_service_failures_restore_same_data(lab):
 cs,ts=lab
 for component in ("s3","db"):
  name=S["containers"][component];owned(name)
  docker("stop",name)
  try:
   r=cs[2].get(f"/api/tasks/{ts[2]}/video",headers={"Range":"bytes=0-31"})
   assert r.status_code>=400
  finally:docker("start",name)
  wait(lambda:cs[2].get(f"/api/tasks/{ts[2]}/video",headers={"Range":"bytes=0-31"}).status_code==206)
def test_worker_outage_fifo_quota_resume(lab):
 cs,ts=lab;workers=S["containers"]["workers"]
 for w in workers:owned(w);docker("stop",w)
 try:
  before=cs[1].get("/api/quota").json()["credits"]
  r=cs[1].post("/api/inputs/upload",content=Path(S["samples"]["short"]).read_bytes(),headers={"X-Upload-Authorized":"true","X-Video-Filename":"worker-outage.mp4"})
  assert r.status_code==201;r2=r.json()["id"]
  assert next(t for t in cs[1].get("/api/tasks").json() if t["id"]==r2)["status"]=="QUEUED"
  assert cs[1].get("/api/quota").json()["credits"]==before
 finally:
  for w in workers:docker("start",w)
 wait(lambda:any(t["id"]==r2 and t["status"]=="SUCCEEDED" for t in cs[1].get("/api/tasks").json()))
 assert cs[1].get("/api/quota").json()["credits"]==before-1
def test_delete_actual_objects_isolation(lab):
 cs,ts=lab;t=ts[0]
 with psycopg.connect(S["db_url"],autocommit=True) as db:
  key=db.execute("SELECT storage_key FROM media_assets WHERE task_id=%s AND asset_kind='ORIGINAL'",(t,)).fetchone()[0]
 r=cs[0].delete(f"/api/tasks/{t}");assert r.status_code==202 and r.json()["accepted"]
 assert cs[0].get(f"/api/tasks/{t}/result").status_code==404
 def complete():
  with psycopg.connect(S["db_url"],autocommit=True) as db:return db.execute("SELECT status FROM cleanup_jobs WHERE task_id=%s",(t,)).fetchone()==("COMPLETED",)
 wait(complete)
 s3=boto3.client("s3",endpoint_url=S["s3_endpoint"],aws_access_key_id=S["s3_access_key"],aws_secret_access_key=S["s3_secret_key"])
 from botocore.exceptions import ClientError
 with pytest.raises(ClientError):s3.head_object(Bucket=S["bucket"],Key=key)
 assert cs[1].get(f"/api/tasks/{ts[1]}/result").status_code==200

def test_real_backpressure_stream_revoke(lab):
 import socket,struct
 from urllib.parse import urlsplit
 from uuid import uuid4
 cs,_=lab;owner,_,admin=cs
 s3=boto3.client("s3",endpoint_url=S["s3_endpoint"],aws_access_key_id=S["s3_access_key"],aws_secret_access_key=S["s3_secret_key"])
 key=f"originals/{S['accounts'][0]['id']}/{uuid4()}"
 small=Path(S["samples"]["short"]).read_bytes();padding=32*1024*1024
 s3.put_object(Bucket=S["bucket"],Key=key,Body=small+struct.pack(">I4s",padding,b"free")+b"\0"*(padding-8))
 with psycopg.connect(S["db_url"],autocommit=True) as db:
  backend=db.execute("SELECT storage_backend FROM media_assets WHERE storage_backend LIKE 'S3:%' LIMIT 1").fetchone()[0]
  t=db.execute("INSERT INTO tasks(user_id,input_kind,status,display_filename,duration_seconds) VALUES(%s,'LOCAL','SUCCEEDED','transport-synthetic.mp4',3) RETURNING id",(S["accounts"][0]["id"],)).fetchone()[0]
  db.execute("INSERT INTO media_assets(task_id,user_id,asset_kind,storage_key,storage_backend,storage_bucket) VALUES(%s,%s,'ORIGINAL',%s,%s,%s)",(t,S["accounts"][0]["id"],key,backend,S["bucket"]))
 url=urlsplit(S["api_url"]);sock=socket.socket();sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,16384);sock.settimeout(30);sock.connect((url.hostname,url.port))
 cookie="; ".join(k+"="+v for k,v in owner.cookies.items())
 sock.sendall(f"GET /tasks/{t}/video HTTP/1.1\r\nHost: {url.netloc}\r\nCookie: {cookie}\r\nRange: bytes=0-\r\nConnection: close\r\n\r\n".encode())
 header=b""
 while b"\r\n\r\n" not in header:header+=sock.recv(1)
 assert b"206 Partial Content" in header
 expected=int(next(line.split(b":",1)[1] for line in header.split(b"\r\n") if line.lower().startswith(b"content-length:")))
 first=sock.recv(4096);assert first
 assert owner.delete(f"/api/tasks/{t}").status_code==202
 assert owner.get(f"/api/tasks/{t}/video",headers={"Range":"bytes=0-31"}).status_code==404
 sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024*1024)
 received=len(first)
 try:
  while True:
   chunk=sock.recv(65536)
   if not chunk:break
   received+=len(chunk)
 finally:sock.close()
 assert received<expected,(received,expected)
 assert owner.delete(f"/api/tasks/{t}").status_code==202

def test_health_persistent_signal_and_controlled_retry(lab):
 cs,_=lab;admin=cs[2];workers=S["containers"]["workers"]
 for w in workers:owned(w);docker("stop",w)
 try:
  # Freeze production: upload queues, so no quota spend or racing cleanup.
  r=admin.post("/api/inputs/upload",content=Path(S["samples"]["short"]).read_bytes(),headers={"X-Upload-Authorized":"true","X-Video-Filename":"health-synthetic.mp4"})
  assert r.status_code==201;t=r.json()["id"]
  d=admin.delete(f"/api/tasks/{t}");assert d.status_code==202;j=d.json()["id"]
  # Controlled PG fault/time seam, not a claim of five physical network failures.
  with psycopg.connect(S["db_url"],autocommit=True) as db:
   version=db.execute("UPDATE cleanup_jobs SET status='BLOCKED',blocked_reason='CONFIG',last_error_kind='CONFIG',last_error_code='STORAGE_CONFIGURATION',consecutive_failures=5,started_at=clock_timestamp()-interval '31 minutes',next_attempt_at=clock_timestamp()-interval '1 second',job_version=job_version+1 WHERE id=%s RETURNING job_version",(j,)).fetchone()[0]
  api=S["containers"]["api"]
  docker("exec",api,"python","-c","from app.cleanup import run_once;run_once()")
  h=subprocess.run(["docker","exec",api,"python","-m","app.cleanup_ops","health"],capture_output=True,text=True)
  assert h.returncode==2 and json.loads(h.stdout)["persistent_faults"]>=1
  status=json.loads(docker("exec",api,"python","-m","app.cleanup_ops","status","--job-id",j));assert status["status"]=="BLOCKED"
  with psycopg.connect(S["db_url"],autocommit=True) as db:
   assert db.execute("SELECT count(*) FROM cleanup_alerts WHERE cleanup_job_id=%s AND resolved_at IS NULL",(j,)).fetchone()[0]>=1
  docker("exec","-e","MAINTENANCE_DATABASE_URL="+S["internal_db_url"],api,"python","-m","app.cleanup_ops","retry","--job-id",j,"--expected-version",str(version),"--reason","STORAGE_RECOVERED")
  docker("exec",api,"python","-c","from app.cleanup import run_once;run_once()")
  with psycopg.connect(S["db_url"],autocommit=True) as db:
   assert db.execute("SELECT status FROM cleanup_jobs WHERE id=%s",(j,)).fetchone()==("COMPLETED",)
   assert db.execute("SELECT count(*) FROM audit_events WHERE task_id=%s AND reason_code LIKE 'STORAGE_RECOVERED:%%'",(t,)).fetchone()[0]==1
  h=json.loads(docker("exec",api,"python","-m","app.cleanup_ops","health"));assert h["healthy"]
  # A specific commit-time SQLSTATE must be reached, not any psycopg exception.
  with psycopg.connect(S["db_url"],autocommit=True) as db:
   version=db.execute("UPDATE cleanup_jobs SET status='BLOCKED',blocked_reason='CONFIG',completed_at=NULL,job_version=job_version+1 WHERE id=%s RETURNING job_version",(j,)).fetchone()[0]
   before=db.execute("SELECT status,job_version,next_attempt_at,attempt_count,consecutive_failures FROM cleanup_jobs WHERE id=%s",(j,)).fetchone()
   db.execute("CREATE FUNCTION wp06_reject_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic commit reject' USING ERRCODE='23514'; END $$")
   db.execute("CREATE CONSTRAINT TRIGGER wp06_reject_audit AFTER INSERT ON audit_events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION wp06_reject_audit()")
  code="import psycopg;from app.cleanup_ops import retry\ntry:retry("+repr(j)+","+str(version)+")\nexcept psycopg.Error as e:\n assert e.sqlstate=='23514',e.sqlstate;print(e.sqlstate)\nelse:raise AssertionError('Audit failure was not reached')"
  try:
   assert docker("exec","-e","MAINTENANCE_DATABASE_URL="+S["internal_db_url"],api,"python","-c",code).strip()=="23514"
   with psycopg.connect(S["db_url"],autocommit=True) as db:assert db.execute("SELECT status,job_version,next_attempt_at,attempt_count,consecutive_failures FROM cleanup_jobs WHERE id=%s",(j,)).fetchone()==before
  finally:
   with psycopg.connect(S["db_url"],autocommit=True) as db:db.execute("DROP TRIGGER wp06_reject_audit ON audit_events");db.execute("DROP FUNCTION wp06_reject_audit()")
  docker("exec","-e","MAINTENANCE_DATABASE_URL="+S["internal_db_url"],api,"python","-m","app.cleanup_ops","retry","--job-id",j,"--expected-version",str(version),"--reason","STORAGE_RECOVERED")
  docker("exec",api,"python","-c","from app.cleanup import run_once;run_once()")

 finally:
  for w in workers:docker("start",w)


def test_real_cr002_search_retirement_and_config(lab):
 from uuid import uuid4
 cs,ts=lab;owner=cs[1];admin=cs[2];t=ts[1]
 assert owner.get('/api/admin/tasks').status_code==403
 for q in [t,t.upper(),t[:8].upper()]:
  r=admin.get('/api/admin/tasks',params={'task_id':q});assert r.status_code==200 and any(x['id']==t for x in r.json())
  for row in r.json():assert not {'summary','export_document','presentation','storage_key','url'} & set(row)
 assert admin.get('/api/admin/tasks',params={'task_id':'123'}).status_code==200
 assert admin.get('/api/admin/tasks',params={'offset':10001}).status_code==422
 assert admin.get('/api/admin/tasks',params={'user_id':str(uuid4())}).status_code==422
 with psycopg.connect(S['db_url'],autocommit=True) as db:
  rid=str(db.execute("INSERT INTO support_requests(task_id,user_id,scope,created_at,expires_at) SELECT id,user_id,'both',clock_timestamp(),clock_timestamp()+interval '86400 seconds' FROM tasks WHERE id=%s RETURNING support_requests.id",(t,)).fetchone()[0])
 for kind in ['result','export','video']:
  assert admin.get(f'/api/admin/support-requests/{rid}/{kind}').status_code==410
  assert admin.get(f'/api/tasks/{t}/{kind}').status_code==404
 assert owner.get(f'/api/tasks/{t}/support-request').status_code==410
 assert owner.post(f'/api/tasks/{t}/support-requests',json={'operation':'CREATE','scope':'both','expected_version':0}).status_code==410
 assert owner.get(f'/api/tasks/{t}/result').status_code==200
 assert admin.get('/api/config/public').json()=={'support_email':'feedback@example.invalid'}
