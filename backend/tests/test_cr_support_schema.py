"""Independent CR-AC08/10/19/20 and T29/32 structure checks, no HTTP claims."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from cr_wp02_fixtures import wp02_factory, wp02_db, seed, request


def test_minimal_structure_indexes_and_defaults(wp02_db):
    with wp02_db.connect() as c:
        cols = c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name='support_requests'", (wp02_db.schema,)).fetchall()
        assert {x[0] for x in cols} == {"id","task_id","user_id","scope","created_at","expires_at","closed_at","close_reason"}
        u,t = seed(c)
        r = request(c,u,t)
        assert r.version == 4 and r.variant == "specified in RFC 4122"
        assert c.execute("SELECT support_version FROM tasks WHERE id=%s",(t,)).fetchone()[0] == 0
        indexes = c.execute("SELECT indexdef FROM pg_indexes WHERE schemaname=%s AND tablename='support_requests'", (wp02_db.schema,)).fetchall()
        assert len(indexes) == 4
        text = " ".join(x[0] for x in indexes)
        assert "UNIQUE" in text and "closed_at IS NULL" in text
        assert "created_at, id" in text and "task_id, created_at" in text
        names = {x[0] for x in c.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s",(wp02_db.schema,))}
        assert not any("nonce" in n or "receipt" in n or "crypto" in n or "epoch" in n for n in names)


@pytest.mark.parametrize("scope", ["summary","video","both"])
def test_scope_and_elapsed_day_across_dst(wp02_db, scope):
    with wp02_db.connect() as c:
        c.execute("SET TIME ZONE 'America/Los_Angeles'")
        u,t=seed(c)
        start=datetime(2026,3,8,9,30,tzinfo=timezone.utc)
        r=request(c,u,t,scope,created=start)
        assert c.execute("SELECT extract(epoch FROM expires_at-created_at) FROM support_requests WHERE id=%s",(r,)).fetchone()[0] == 86400


@pytest.mark.parametrize("assignment,params", [
    ("scope=%s",("other",)), ("expires_at=created_at+interval '1 second'",()),
    ("closed_at=created_at,close_reason=NULL",()),
    ("closed_at=NULL,close_reason='REVOKED'",()),
    ("closed_at=created_at-interval '1 second',close_reason='REVOKED'",()),
    ("closed_at=created_at,close_reason='OTHER'",()),
    ("scope=NULL",()), ("user_id=NULL",()), ("task_id=NULL",()),
])
def test_invalid_insert_rejected(wp02_db, assignment, params):
    with wp02_db.connect() as c:
        u,t=seed(c)
        columns="task_id,user_id,scope,created_at,expires_at,closed_at,close_reason"
        values={"task_id":"%s","user_id":"%s","scope":"'summary'","created_at":"clock_timestamp()"}
        # A VALUES subquery supplies one sampled instant; mutate via SELECT.
        exprs={"task_id":"task_id","user_id":"user_id","scope":"scope","created_at":"created_at",
               "expires_at":"created_at+interval '86400 seconds'","closed_at":"NULL::timestamptz","close_reason":"NULL::text"}
        for part in assignment.split(","):
            key,value=part.split("=",1);exprs[key]=value
        with pytest.raises(psycopg.IntegrityError):
            with c.transaction():
                c.execute("INSERT INTO support_requests("+columns+") SELECT "+",".join(exprs.values())+
                          " FROM (SELECT %s::uuid AS task_id,%s::uuid AS user_id,'summary'::text AS scope,clock_timestamp() AS created_at) s",(*params,t,u))


def test_cross_owner_and_task_delete_restrict(wp02_db):
    with wp02_db.connect() as c:
        u,t=seed(c); other,_=seed(c)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with c.transaction():request(c,other,t)
        request(c,u,t)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with c.transaction():c.execute("DELETE FROM tasks WHERE id=%s",(t,))


@pytest.mark.parametrize("column,expression", [
    ("id","gen_random_uuid()"),("task_id","gen_random_uuid()"),("user_id","gen_random_uuid()"),
    ("scope","'both'"),("created_at","created_at+interval '1 second'"),
    ("expires_at","expires_at+interval '1 second'"),
])
def test_request_identity_and_time_immutable(wp02_db,column,expression):
    with wp02_db.connect() as c:
        u,t=seed(c);r=request(c,u,t)
        with pytest.raises(psycopg.IntegrityError):
            with c.transaction():c.execute(f"UPDATE support_requests SET {column}={expression} WHERE id=%s",(r,))


@pytest.mark.parametrize("reason", ["REVOKED","REPLACED","DISMISSED","TASK_DELETED","EXPIRED"])
def test_closed_request_cannot_reactivate_or_rewrite(wp02_db,reason):
    with wp02_db.connect() as c:
        u,t=seed(c);r=request(c,u,t)
        c.execute("UPDATE support_requests SET closed_at=clock_timestamp(),close_reason=%s WHERE id=%s",(reason,r))
        for assignment in ("closed_at=NULL,close_reason=NULL","closed_at=closed_at+interval '1 second'","close_reason='REPLACED'" if reason!="REPLACED" else "close_reason='REVOKED'"):
            with pytest.raises(psycopg.IntegrityError):
                with c.transaction():c.execute(f"UPDATE support_requests SET {assignment} WHERE id=%s",(r,))
        # Closure frees the slot but the old ID is still distinct.
        assert request(c,u,t) != r


def test_expired_unclosed_slot_must_be_closed_first(wp02_db):
    with wp02_db.connect() as c:
        u,t=seed(c);r=request(c,u,t,created=datetime(2025,1,1,tzinfo=timezone.utc))
        with pytest.raises(psycopg.errors.UniqueViolation):
            with c.transaction():request(c,u,t)
        c.execute("UPDATE support_requests SET closed_at=clock_timestamp(),close_reason='EXPIRED' WHERE id=%s",(r,))
        request(c,u,t)


def test_atomic_replacement_and_version_rollback(wp02_db):
    with wp02_db.connect() as c:
        u,t=seed(c);r=request(c,u,t)
        with pytest.raises(RuntimeError):
            with c.transaction():
                c.execute("UPDATE support_requests SET closed_at=clock_timestamp(),close_reason='REPLACED' WHERE id=%s",(r,))
                request(c,u,t)
                c.execute("UPDATE tasks SET support_version=support_version+1 WHERE id=%s",(t,))
                raise RuntimeError("synthetic failure before commit")
        assert c.execute("SELECT id FROM support_requests WHERE closed_at IS NULL").fetchall()==[(r,)]
        assert c.execute("SELECT support_version FROM tasks WHERE id=%s",(t,)).fetchone()==(0,)


@pytest.mark.parametrize("value",[-1,2,None])
def test_version_rejects_negative_jump_and_null(wp02_db,value):
    with wp02_db.connect() as c:
        _,t=seed(c)
        with pytest.raises(psycopg.IntegrityError):
            with c.transaction():c.execute("UPDATE tasks SET support_version=%s WHERE id=%s",(value,t))
        c.execute("UPDATE tasks SET support_version=1 WHERE id=%s",(t,))
        with pytest.raises(psycopg.IntegrityError):
            with c.transaction():c.execute("UPDATE tasks SET support_version=0 WHERE id=%s",(t,))
        c.execute("UPDATE tasks SET status='FAILED' WHERE id=%s",(t,))


def test_two_connection_unique_slot_and_version_cas(wp02_db):
    with wp02_db.connect() as c:u,t=seed(c)
    started=Event()
    def competing():
        with wp02_db.connect() as b:
            started.set()
            try:
                with b.transaction():request(b,u,t)
            except psycopg.errors.UniqueViolation:return "rejected"
            return "unexpected"
    with ThreadPoolExecutor(max_workers=2) as pool:
        with wp02_db.connect() as a:
            with a.transaction():
                request(a,u,t)
                f=pool.submit(competing)
                assert started.wait(3)
            assert f.result(timeout=8)=="rejected"
        def cas():
            with wp02_db.connect() as x:
                return x.execute("UPDATE tasks SET support_version=support_version+1 WHERE id=%s AND support_version=0 RETURNING support_version",(t,)).fetchall()
        # Real concurrent conditional updates serialize; exactly one wins.
        futures=[pool.submit(cas),pool.submit(cas)]
        assert sorted(len(f.result(timeout=8)) for f in futures)==[0,1]

def test_deferred_commit_failure_rolls_back_replacement_version_and_audit(wp02_db):
    with wp02_db.connect() as c:
        u,t=seed(c);old=request(c,u,t)
        c.execute("""CREATE FUNCTION wp02_reject_audit_commit() RETURNS trigger LANGUAGE plpgsql AS $$
                     BEGIN RAISE EXCEPTION 'synthetic commit failure' USING ERRCODE='23514'; END $$""")
        c.execute("""CREATE CONSTRAINT TRIGGER wp02_commit_failure AFTER INSERT ON audit_events
                     DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION wp02_reject_audit_commit()""")
        with pytest.raises(psycopg.errors.CheckViolation,match="synthetic commit failure"):
            with c.transaction():
                c.execute("UPDATE support_requests SET closed_at=clock_timestamp(),close_reason='REPLACED' WHERE id=%s",(old,))
                request(c,u,t)
                c.execute("UPDATE tasks SET support_version=support_version+1 WHERE id=%s",(t,))
                c.execute("""INSERT INTO audit_events(target_user_id,task_id,event_type,result,retention_class)
                             VALUES(%s,%s,'SYNTHETIC','RECORDED','TASK_30D')""",(u,t))
                # The body completes; rejection occurs during transaction COMMIT.
        with wp02_db.connect() as observer:
            assert observer.execute("SELECT id,closed_at FROM support_requests").fetchall()==[(old,None)]
            assert observer.execute("SELECT support_version FROM tasks WHERE id=%s",(t,)).fetchone()==(0,)
            assert observer.execute("SELECT count(*) FROM audit_events").fetchone()==(0,)


def test_calendar_day_across_dst_cannot_shorten_ttl(wp02_db):
    with wp02_db.connect() as c:
        c.execute("SET TIME ZONE 'America/Los_Angeles'")
        u,t=seed(c)
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():
                c.execute("""INSERT INTO support_requests(task_id,user_id,scope,created_at,expires_at)
                             VALUES(%s,%s,'summary','2026-03-08 01:30:00-08'::timestamptz,
                             '2026-03-08 01:30:00-08'::timestamptz+interval '1 day')""",(t,u))


def test_cross_user_concurrent_generation_and_duplicate_uuid(wp02_db):
    from threading import Barrier
    with wp02_db.connect() as c:
        first=seed(c);second=seed(c)
    barrier=Barrier(2)
    def create(pair):
        with wp02_db.connect() as c:
            barrier.wait(timeout=5)
            return request(c,*pair)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=[f.result(timeout=10) for f in [pool.submit(create,first),pool.submit(create,second)]]
    assert len(set(results))==2
    with wp02_db.connect() as c:
        u,t=seed(c)
        with pytest.raises(psycopg.errors.UniqueViolation):
            with c.transaction():
                c.execute("""INSERT INTO support_requests(id,task_id,user_id,scope,created_at,expires_at)
                             SELECT %s,%s,%s,'summary',created_at,expires_at FROM support_requests WHERE id=%s""",
                          (results[0],t,u,results[0]))
        assert c.execute("SELECT count(*) FROM support_requests").fetchone()==(2,)
