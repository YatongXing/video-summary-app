"""Historical authorization cannot survive task deletion or offline restore."""
import asyncio
from app import auth
from test_results import result_db,identity,claimed,publish,authenticated,client


def headers(c):
    return {'Origin':'http://127.0.0.1:8000','X-CSRF-Token':auth.csrf_token(c.cookies.get(auth.COOKIE_NAME))}






def test_deleted_task_blocks_previously_authorized_admin_content(result_db):
    from app import cleanup
    db=result_db;uid,tid,generation=claimed(db);publish(tid,generation);admin=identity(db,'ADMIN')
    db.execute("INSERT INTO task_consents(task_id,user_id,purpose,access_scope) VALUES (%s,%s,'support','summary')",(tid,uid))
    cleanup.request_delete(tid,uid)
    async def scenario():
        async with client() as manager:
            authenticated(db,manager,admin)
            assert (await manager.get(f'/admin/tasks/{tid}/result?purpose=support')).status_code==410
            assert (await manager.get(f'/admin/tasks/{tid}/export?purpose=support')).status_code==410
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
