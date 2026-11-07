"""Independent CR002 security tests: real PG/ASGI, explicit synthetic store."""
import asyncio,io
from datetime import timedelta
from uuid import uuid4
import pytest
from app import auth,results
from test_results import result_db,identity,claimed,publish,authenticated,client
from cr_wp03_fixtures import seed_request

def headers(c):return {'X-CSRF-Token':auth.csrf_token(c.cookies.get(auth.COOKIE_NAME,''))}

def asset(db,u,t):
 db.execute("INSERT INTO media_assets(task_id,user_id,asset_kind,storage_key,byte_size) VALUES(%s,%s,'ORIGINAL','originals/synthetic',10)",(t,u))
class Store:
 def __init__(self,callback=lambda:None,head=False):self.callback=callback;self.head=head
 def head_object(self,**kw):
  if self.head:self.callback()
  return {'ContentLength':10}
 def get_object(self,**kw):
  callback=self.callback
  class Stream(io.BytesIO):
   def read(self,size=-1):
    v=super().read(size);callback();return v
  start,end=map(int,kw['Range'][6:].split('-'))
  return {'Body':Stream(b'0123456789'[start:end+1])}

@pytest.mark.parametrize('role',['USER','ADMIN'])
def test_owner_matrix(result_db,monkeypatch,role):
 db=result_db;u=identity(db,role);u,t,g=claimed(db,u);publish(t,g);asset(db,u,t);other=identity(db);admin=identity(db,'ADMIN')
 monkeypatch.setattr(results.object_store,'client',lambda:Store())
 async def run():
  async with client() as c:
   authenticated(db,c,u)
   for lang in ['zh','en']:
    assert (await c.get(f'/tasks/{t}/result?language={lang}')).json()['language']==lang
    assert (await c.get(f'/tasks/{t}/export?language={lang}')).json()['language']==lang
   v=await c.get(f'/tasks/{t}/video',headers={'Range':'bytes=2-5'});assert v.status_code==206 and v.content==b'2345'
   for outsider in [other,admin]:
    authenticated(db,c,outsider)
    for kind in ['result','export','video']:assert (await c.get(f'/tasks/{t}/{kind}')).status_code==404
 asyncio.run(run())

@pytest.mark.parametrize('phase',['head','read'])
@pytest.mark.parametrize('change',['role','session','disabled','expired_session','delete','retention'])
def test_owner_stream_dynamic_invalidation(result_db,monkeypatch,phase,change):
 db=result_db;u,t,g=claimed(db);asset(db,u,t);changed=[]
 def invalidate():
  if changed:return
  changed.append(True)
  if change=='role':db.execute("UPDATE users SET role='ADMIN' WHERE id=%s",(u,))
  elif change=='session':db.execute("UPDATE sessions SET revoked_at=clock_timestamp() WHERE user_id=%s",(u,))
  elif change=='disabled':db.execute("UPDATE users SET disabled_at=clock_timestamp() WHERE id=%s",(u,))
  elif change=='expired_session':db.execute("UPDATE sessions SET expires_at=created_at+interval '1 microsecond' WHERE user_id=%s",(u,))
  elif change=='retention':db.execute("UPDATE tasks SET created_at=clock_timestamp()-interval '30 days' WHERE id=%s",(t,))
  else:
   from app.cleanup import request_delete
   request_delete(t,u)
 monkeypatch.setattr(results.object_store,'client',lambda:Store(invalidate,phase=='head'))
 async def run():
  async with client() as c:
   authenticated(db,c,u);r=await c.get(f'/tasks/{t}/video',headers={'Range':'bytes=0-9'})
   assert changed
   if phase=='head':assert r.status_code in [401,403,404]
   else:assert r.content==b''
 asyncio.run(run())

@pytest.mark.parametrize('scope',['summary','video','both'])
def test_historical_grants_never_authorize(result_db,scope):
 db=result_db;u,t,g=claimed(db);publish(t,g);a=identity(db,'ADMIN');rid=seed_request(db,u,t,scope)
 async def run():
  async with client() as c:
   authenticated(db,c,a)
   for suffix in ['', '/result','/export','/video']:
    assert (await c.get(f'/admin/support-requests/{rid}'+suffix)).status_code==410
   for kind in ['result','export','video']:
    assert (await c.get(f'/tasks/{t}/{kind}?request_id={rid}&purpose=support&code={rid}')).status_code==404
    assert (await c.get(f'/admin/tasks/{t}/{kind}?purpose=support')).status_code==410
 asyncio.run(run())
 from app import support_requests
 from fastapi import HTTPException
 principal=auth.Principal(a,'synthetic@example.invalid','ADMIN',db.execute('SELECT id FROM sessions WHERE user_id=%s ORDER BY created_at DESC LIMIT 1',(a,)).fetchone()[0])
 with auth.connect() as connection:
  with pytest.raises(HTTPException) as e:support_requests.authorize_content(connection,principal,request_id=rid)
 assert e.value.status_code==410

def test_retired_routes_auth_role_csrf(result_db):
 db=result_db;u,t,g=claimed(db);a=identity(db,'ADMIN');rid=seed_request(db,u,t)
 async def run():
  async with client() as c:
   assert (await c.get(f'/tasks/{t}/support-request')).status_code==401
   authenticated(db,c,u)
   assert (await c.get(f'/tasks/{t}/support-request')).status_code==410
   assert (await c.post(f'/tasks/{t}/support-requests',json={})).status_code==403
   assert (await c.post(f'/tasks/{t}/support-requests',json={},headers=headers(c))).status_code==410
   assert (await c.delete(f'/tasks/{t}/support-requests/{rid}',headers=headers(c))).status_code==410
   assert (await c.get('/admin/support-requests')).status_code==403
   authenticated(db,c,a)
   assert (await c.get('/admin/support-requests')).status_code==410
   assert (await c.delete(f'/admin/support-requests/{rid}',headers=headers(c))).status_code==410
 asyncio.run(run())

def test_delete_closes_legacy_even_existing_job(result_db):
 from app import cleanup
 db=result_db;u,t,g=claimed(db);rid=seed_request(db,u,t)
 cleanup.request_delete(t,u)
 assert db.execute('SELECT close_reason FROM support_requests WHERE id=%s',(rid,)).fetchone()==('TASK_DELETED',)
 second=seed_request(db,u,t);cleanup.request_delete(t,u)
 assert db.execute('SELECT closed_at IS NOT NULL FROM support_requests WHERE id=%s',(second,)).fetchone()==(True,)

def test_admin_global_search_white_list_limits(result_db):
 db=result_db;u,t,g=claimed(db);publish(t,g);a=identity(db,'ADMIN')
 # More than one display page; search must execute across server dataset.
 ids=[str(t)]
 for _ in range(25):ids.append(str(db.execute("INSERT INTO tasks(user_id,input_kind) VALUES(%s,'LOCAL') RETURNING id",(u,)).fetchone()[0]))
 async def run():
  async with client() as c:
   authenticated(db,c,u);assert (await c.get('/admin/tasks')).status_code==403
   authenticated(db,c,a)
   target=ids[-1]
   for q in [target,target.upper(),target[:8].upper()]:
    r=await c.get('/admin/tasks',params={'task_id':q});assert r.status_code==200 and any(x['id']==target for x in r.json())
    assert all(x['id']==target for x in r.json())
   pages=[]
   for offset in [0,10,20]:
    r=await c.get('/admin/tasks',params={'limit':10,'offset':offset});assert r.status_code==200;pages+=r.json()
   assert len(pages)==26 and len({x['id'] for x in pages})==26
   forbidden=['summary','presentation','export_document','storage_key','url','input','output','filename']
   for row in pages:assert not any(k in row for k in forbidden)
   for params in [{'limit':101},{'offset':10001},{'user_id':str(u)}]:assert (await c.get('/admin/tasks',params=params)).status_code==422
   assert (await c.get('/admin/tasks',params={'task_id':'ffffffffffffffff'})).json()==[]
 asyncio.run(run())

@pytest.mark.parametrize('email,expected',[('',None),('invalid',None),('feedback@example.invalid','feedback@example.invalid'),('x@example.invalid\r\nHeader:bad',None)])
def test_public_email_config(monkeypatch,email,expected):
 monkeypatch.setenv('SUPPORT_EMAIL',email)
 async def run():
  async with client() as c:
   r=await c.get('/config/public');assert r.status_code==200 and r.json()=={'support_email':expected}
 asyncio.run(run())

@pytest.mark.parametrize('mode',['missing','corrupt','mismatch','directory_missing'])
@pytest.mark.parametrize('phase',['before','read'])
def test_identity_trust_fail_closed(result_db,monkeypatch,tmp_path,mode,phase):
 from app import deletion_control
 db=result_db;u,t,g=claimed(db);publish(t,g);asset(db,u,t)
 root=tmp_path/'journal';monkeypatch.setenv('DELETION_JOURNAL_DIR',str(root));deletion_control.initialize()
 def invalidate():
  p=root/'identity.json'
  if mode=='missing':p.unlink(missing_ok=True)
  elif mode=='directory_missing':
   if root.exists():root.rename(tmp_path/'detached-journal')
  elif mode=='corrupt':p.write_text('{broken')
  else:p.write_text('{"deployment_id":"00000000-0000-4000-8000-000000000000"}')
 monkeypatch.setattr(results.object_store,'client',lambda:Store(invalidate if phase=='read' else lambda:None))
 async def run():
  async with client() as c:
   authenticated(db,c,u)
   if phase=='before':
    invalidate()
    for kind in ['result','export','video']:
     r=await c.get(f'/tasks/{t}/{kind}');assert r.status_code==503
   else:
    r=await c.get(f'/tasks/{t}/video');assert r.content==b''
 asyncio.run(run())

@pytest.mark.parametrize('kind',['result','export'])
def test_final_summary_role_recheck(result_db,monkeypatch,kind):
 db=result_db;u,t,g=claimed(db);publish(t,g)
 original=results.validate_output
 def changed(*args,**kw):
  out=original(*args,**kw);db.execute("UPDATE users SET role='ADMIN' WHERE id=%s",(u,));return out
 monkeypatch.setattr(results,'validate_output',changed)
 async def run():
  async with client() as c:
   authenticated(db,c,u);assert (await c.get(f'/tasks/{t}/{kind}')).status_code==403
 asyncio.run(run())

def test_search_rate_and_statement_deadline(result_db):
 import psycopg
 db=result_db;assert db.info.dbname.endswith('_test') and db.info.host=='127.0.0.1'
 u,t,g=claimed(db);a=identity(db,'ADMIN')
 async def run():
  async with client() as c:
   authenticated(db,c,a)
   for _ in range(60):assert (await c.get('/admin/tasks',params={'task_id':str(t)})).status_code==200
   blocked=await c.get('/admin/tasks',params={'task_id':str(t)});assert blocked.status_code==429 and int(blocked.headers['retry-after'])>=1
   db.execute("DELETE FROM auth_limits WHERE scope LIKE 'admin_search_%%'")
   # The result_db fixture creates a UUID schema in explicitly supplied *_test PG.
   # Derive this connection ONLY from that verified live test connection.
   with psycopg.connect(__import__('os').environ['TEST_DATABASE_URL'],options=db.info.options) as lock:
    assert lock.info.dbname==db.info.dbname and lock.info.host==db.info.host and lock.info.port==db.info.port
    lock.execute('LOCK TABLE tasks IN ACCESS EXCLUSIVE MODE')
    r=await c.get('/admin/tasks');assert r.status_code==503 and 'SELECT' not in r.text and 'password' not in r.text
 asyncio.run(run())

def test_partial_multimatch_pages_and_explicit_nested_allowlist(result_db):
 from uuid import UUID
 db=result_db;u=identity(db);admin=identity(db,'ADMIN');prefix=uuid4().hex[:8];expected=[]
 for i in range(25):
  t=UUID(prefix+'-0000-4000-8000-'+str(i).zfill(12));expected.append(str(t))
  db.execute("INSERT INTO tasks(id,user_id,input_kind,submitted_at) VALUES(%s,%s,'LOCAL','2026-11-06T00:00:00Z')",(t,u))
 db.execute("INSERT INTO quota_events(user_id,task_id,event_key,event_type,delta) VALUES(%s,%s,'synthetic-event','SPEND',-1)",(u,expected[0]))
 async def run():
  async with client() as c:
   authenticated(db,c,admin);rows=[]
   for offset in [0,10,20]:
    r=await c.get('/admin/tasks',params={'task_id':prefix.upper(),'limit':10,'offset':offset});assert r.status_code==200;rows+=r.json()
   assert [r['id'] for r in rows]==expected
   assert len({r['id'] for r in rows})==25
   top={'id','user_id','status','created_at','submitted_at','started_at','finished_at','failure_category','failure_code','retry_count','credits_available','queue_seconds','processing_seconds','quota_unlimited','retry_records','quota_events','model_calls'}
   nested={'retry_records':{'source','accepted_at','cooldown_triggered'},'quota_events':{'event_type','delta','created_at'},'model_calls':{'status','model_version','adapter_version','prompt_version','input_tokens','output_tokens','cached_tokens','estimated_cost','currency','error_category','created_at','finished_at'}}
   for row in rows:
    assert set(row)==top
    for key,allowed in nested.items():
     for value in row[key]:assert set(value)<=allowed
 asyncio.run(run())

def test_source_search_limit_spans_admins_not_login(result_db):
 db=result_db;u,t,g=claimed(db);admins=[identity(db,'ADMIN') for _ in range(6)]
 async def run():
  async with client() as c:
   for a in admins:
    authenticated(db,c,a)
    for _ in range(50):assert (await c.get('/admin/tasks',params={'task_id':str(t)})).status_code==200
   assert (await c.get('/admin/tasks',params={'task_id':str(t)})).status_code==429
   assert (await c.get('/auth/me')).status_code==200
   assert db.execute("SELECT count(*) FROM auth_limits WHERE scope IN ('login_account','login_source')").fetchone()==(0,)
 asyncio.run(run())

@pytest.mark.parametrize('query',['1','g','中文','%','_',"'",'a'*120,'\\','0%0',"x' OR '1'='1"])
def test_unrestricted_task_search_is_literal(result_db,query):
 db=result_db;u=identity(db);a=identity(db,'ADMIN')
 ids=['10000000-0000-4000-8000-000000000001','22222222-2222-4222-8222-222222222222']
 for t in ids:db.execute("INSERT INTO tasks(id,user_id,input_kind) VALUES(%s,%s,'LOCAL')",(t,u))
 expected=[t for t in ids if query.lower() in t.lower()]
 async def run():
  async with client() as c:
   authenticated(db,c,a);r=await c.get('/admin/tasks',params={'task_id':query})
   assert r.status_code==200,r.text
   assert [row['id'] for row in r.json()]==expected
 asyncio.run(run())
