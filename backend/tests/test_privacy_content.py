"""Task-scoped administrator consent on real endpoints, including revocation."""
import asyncio
import io
from app import auth,results
from test_results import result_db,identity,claimed,publish,authenticated,client


def headers(c):
    return {'Origin':'http://127.0.0.1:8000','X-CSRF-Token':auth.csrf_token(c.cookies.get(auth.COOKIE_NAME))}


def test_admin_needs_exact_current_task_purpose_scope_and_access_is_audited(result_db):
    db=result_db;uid,tid,generation=claimed(db);publish(tid,generation)
    admin=identity(db,'ADMIN');other=identity(db)
    async def scenario():
        async with client() as owner,client() as manager,client() as outsider:
            authenticated(db,owner,uid);authenticated(db,manager,admin);authenticated(db,outsider,other)
            assert (await manager.get(f'/admin/tasks/{tid}/result?purpose=diagnosis')).status_code==403
            assert (await outsider.get(f'/tasks/{tid}/consents')).status_code==404
            response=await owner.post(f'/tasks/{tid}/consents',json={'purpose':'diagnosis','access_scope':'summary'},headers=headers(owner))
            assert response.status_code==201
            consent_id=response.json()['id']
            assert len((await owner.get(f'/tasks/{tid}/consents')).json())==1
            assert (await manager.get(f'/admin/tasks/{tid}/result?purpose=other')).status_code==403
            assert (await manager.get(f'/admin/tasks/{tid}/result?purpose=diagnosis')).status_code==200
            assert (await manager.get(f'/admin/tasks/{tid}/export?purpose=diagnosis&language=en')).status_code==200
            assert (await manager.get(f'/admin/tasks/{tid}/video?purpose=diagnosis')).status_code==403
            assert (await manager.get(f'/tasks/{tid}/result')).status_code==404
            assert (await owner.delete(f'/tasks/{tid}/consents/{consent_id}',headers=headers(owner))).status_code==204
            assert (await manager.get(f'/admin/tasks/{tid}/result?purpose=diagnosis')).status_code==403
            assert (await manager.get(f'/admin/tasks/{tid}/export?purpose=diagnosis')).status_code==403
    asyncio.run(scenario())
    rows=db.execute("SELECT result,reason_code FROM audit_events WHERE event_type='ADMIN_CONTENT_ACCESS' AND actor_user_id=%s",(admin,)).fetchall()
    assert any(row[0]=='ALLOWED' for row in rows) and any(row[0]=='DENIED' for row in rows)
    assert all(row[1] in ('diagnosis','other') for row in rows)


def test_video_consent_revoke_during_object_read_stops_next_chunk(result_db,monkeypatch):
    db=result_db;uid,tid,generation=claimed(db);admin=identity(db,'ADMIN')
    db.execute("INSERT INTO media_assets(user_id,task_id,asset_kind,storage_key,byte_size) VALUES (%s,%s,'ORIGINAL','originals/test',10)",(uid,tid))
    cid=db.execute("INSERT INTO task_consents(task_id,user_id,purpose,access_scope) VALUES (%s,%s,'video-check','video') RETURNING id",(tid,uid)).fetchone()[0]
    revoked=[False]
    class Stream(io.BytesIO):
        def read(self,size=-1):
            data=super().read(size)
            db.execute('UPDATE task_consents SET revoked_at=now() WHERE id=%s',(cid,))
            revoked[0]=True
            return data
    class Store:
        def head_object(self,**kwargs):return {'ContentLength':10}
        def get_object(self,**kwargs):return {'Body':Stream(b'0123456789')}
    monkeypatch.setattr(results.object_store,'client',lambda:Store())
    async def scenario():
        async with client() as manager:
            authenticated(db,manager,admin)
            response=await manager.get(f'/admin/tasks/{tid}/video?purpose=video-check')
            assert response.content==b'' and revoked[0]
            assert (await manager.get(f'/admin/tasks/{tid}/video?purpose=video-check')).status_code==403
    asyncio.run(scenario())
    assert db.execute("SELECT count(*) FROM audit_events WHERE task_id=%s AND event_type='ADMIN_CONTENT_ACCESS'",(tid,)).fetchone()[0]==2


def test_deleted_task_blocks_previously_authorized_admin_content(result_db):
    from app import cleanup
    db=result_db;uid,tid,generation=claimed(db);publish(tid,generation);admin=identity(db,'ADMIN')
    db.execute("INSERT INTO task_consents(task_id,user_id,purpose,access_scope) VALUES (%s,%s,'support','summary')",(tid,uid))
    cleanup.request_delete(tid,uid)
    async def scenario():
        async with client() as manager:
            authenticated(db,manager,admin)
            assert (await manager.get(f'/admin/tasks/{tid}/result?purpose=support')).status_code in (403,404)
            assert (await manager.get(f'/admin/tasks/{tid}/export?purpose=support')).status_code in (403,404)
    asyncio.run(scenario())


def test_offline_restore_revokes_old_sessions_and_content_consents(result_db,monkeypatch,tmp_path):
    from app import deletion_control
    db=result_db;uid,tid,generation=claimed(db);publish(tid,generation)
    db.execute("INSERT INTO sessions(user_id,token_hash,expires_at) VALUES (%s,'synthetic-session',now()+interval '1 hour')",(uid,))
    db.execute("INSERT INTO task_consents(task_id,user_id,purpose,access_scope) VALUES (%s,%s,'old-consent','summary')",(tid,uid))
    monkeypatch.setenv('DELETION_JOURNAL_DIR',str(tmp_path/'restore-journal'))
    deletion_control.initialize();deletion_control.replay_journal(force=True)
    assert db.execute('SELECT count(*) FROM sessions WHERE revoked_at IS NULL').fetchone()[0]==0
    assert db.execute('SELECT count(*) FROM task_consents WHERE revoked_at IS NULL').fetchone()[0]==0
