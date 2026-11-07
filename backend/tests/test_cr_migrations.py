"""Migration asset/transaction tests against synthetic old state."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import shutil

import psycopg
import pytest
from cr_wp02_fixtures import wp02_factory, seed, MIGRATIONS

OLD_HASHES = [
"246dc645f61770138648b8afdf27cea69283029031d4ca46722fd7f24bb48cb5",
"2ac3bb5609846bd560bf13033f36e118c7ef05fb26af5a1766ebb9dd60cb65db",
"799ee805423e151f05cd83ab2795dd66cf6a8ebe596293791ec91987d86e65f6",
"c212c7235f20604f554f3b7a016d67244cfa1435439c09d44bb71783503b2ecd",
"3e0a90ef83d97453cfec34cfae026f0ee1f63f1d43ad7ec8e7ff78f7f992a713",
"3443e02303beeb178d4cc000734faf7f1dfef0b66b514cca8cc1ba37a3739f7f",
"ce980a3e24ba4c75eacca9d4846a3b271859b1b83d6add2a7011162b94536232",
"9f1fa0882639ee0a712131dd632ed7f16efe696be2ec2c88165464ef866a9e04",
"247406e2968cf2c709e082fde64587f6b6b2675bd9f0a2009995869da6d7af0f"]


def test_old_assets_frozen():
    files=sorted(MIGRATIONS.glob("00*.sql"))
    assert [sha256(x.read_bytes()).hexdigest() for x in files] == OLD_HASHES
    assert [p.name for p in MIGRATIONS.glob("01*.sql")] == ["010_cr_support_expand.sql"]


def old_directory(tmp_path):
    target=tmp_path/"old";target.mkdir()
    for p in sorted(MIGRATIONS.glob("00*.sql")):shutil.copyfile(p,target/p.name)
    return target


def snapshot(c):
    result={}
    for (name,) in c.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename!='schema_migrations'").fetchall():
        result[name]=c.execute(f"SELECT to_jsonb(t)::text FROM {name} t ORDER BY to_jsonb(t)::text").fetchall()
    return result


def test_upgrade_preserves_old_content_quota_and_consents(wp02_factory,tmp_path,monkeypatch):
    from app import migrate
    old=old_directory(tmp_path)
    db=wp02_factory(migrate=False)
    monkeypatch.setattr(migrate,"MIGRATIONS_DIR",old)
    with db.connect() as c:
        migrate.run_migrations(c)
        for role in ("USER","ADMIN"):
            for status in ("QUEUED","PROCESSING","SUCCEEDED","FAILED","CANCELLED"):
                u,t=seed(c,role,status)
                for purpose in ("first","second"):
                    for scope in ("summary","video"):
                        c.execute("INSERT INTO task_consents(task_id,user_id,purpose,access_scope) VALUES(%s,%s,%s,%s)",(t,u,purpose,scope))
                c.execute("INSERT INTO quota_events(user_id,event_key,event_type,delta) VALUES(%s,%s,'RECOVER',1)",(u,str(t)))
        # Store synthetic SQL summary assets to prove 010 does not touch bodies.
        u,t=seed(c)
        c.execute("""INSERT INTO task_results(task_id,user_id,language,lease_generation,export_document,presentation)
                     VALUES(%s,%s,'zh',0,'{"summary":"synthetic-only"}','{}')""",(t,u))
        before=snapshot(c)
        monkeypatch.setattr(migrate,"MIGRATIONS_DIR",MIGRATIONS)
        assert migrate.run_migrations(c)==["010_cr_support_expand.sql"]
        after=snapshot(c)
        # New column default is the only permitted difference to old task rows.
        import json
        oldtasks=[json.loads(x[0]) for x in before.pop("tasks")]
        newtasks=[json.loads(x[0]) for x in after.pop("tasks")]
        for row in newtasks:assert row.pop("support_version")==0
        assert sorted(oldtasks,key=lambda x:x["id"])==sorted(newtasks,key=lambda x:x["id"])
        assert after.pop("support_requests")==[]
        assert after==before
        assert migrate.run_migrations(c)==[]
        recorded=dict(c.execute("SELECT version,sha256 FROM schema_migrations"))
        assert all(recorded[p.name]==sha256(p.read_bytes()).hexdigest() for p in MIGRATIONS.glob("*.sql"))


@pytest.mark.parametrize("autocommit",[True,False])
def test_failure_boundary_and_replay(wp02_factory,tmp_path,monkeypatch,autocommit):
    from app import migrate
    target=tmp_path/"broken";target.mkdir()
    (target/"001_first.sql").write_text("CREATE TABLE first_probe(id int);")
    (target/"002_fail.sql").write_text("CREATE TABLE second_probe(id int); SELECT 1/0;")
    monkeypatch.setattr(migrate,"MIGRATIONS_DIR",target)
    db=wp02_factory(migrate=False)
    with db.connect(autocommit=autocommit) as c:
        if not autocommit:c.execute("SELECT 1")  # Existing outer transaction.
        with pytest.raises(psycopg.errors.DivisionByZero):migrate.run_migrations(c)
        if not autocommit:c.rollback()
    with db.connect() as c:
        assert (c.execute("SELECT to_regclass('first_probe')").fetchone()[0] is not None)==autocommit
        assert c.execute("SELECT to_regclass('second_probe')").fetchone()==(None,)
        # Failed file was never recorded; repair only the temporary copy.
        (target/"002_fail.sql").write_text("CREATE TABLE second_probe(id int);")
        applied=migrate.run_migrations(c)
        assert "002_fail.sql" in applied
        assert migrate.run_migrations(c)==[]


def test_applied_checksum_change_rejected(wp02_factory,tmp_path,monkeypatch):
    from app import migrate
    target=old_directory(tmp_path);monkeypatch.setattr(migrate,"MIGRATIONS_DIR",target)
    db=wp02_factory()
    with db.connect() as c:
        name=sorted(target.glob("*.sql"))[0]
        name.write_bytes(name.read_bytes()+b"\n-- synthetic tamper\n")
        with pytest.raises(RuntimeError,match="Applied migration changed"):migrate.run_migrations(c)
        assert c.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]==9


def test_two_migrators_record_each_version_once(wp02_factory):
    from app.migrate import run_migrations
    db=wp02_factory(migrate=False)
    def run():
        with db.connect() as c:return run_migrations(c)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=[x.result(timeout=30) for x in [pool.submit(run),pool.submit(run)]]
    assert sorted(name for result in outcomes for name in result)==sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    with db.connect() as c:assert c.execute("SELECT count(*) FROM schema_migrations").fetchone()==(10,)


def test_outer_transaction_can_rollback_successful_schema_upgrade(wp02_factory):
    from app.migrate import run_migrations
    db=wp02_factory(migrate=False)
    with db.connect(autocommit=False) as c:
        c.execute("SELECT 1")
        run_migrations(c)
        c.rollback()
    with db.connect() as c:
        assert c.execute("SELECT to_regclass('support_requests'),to_regclass('schema_migrations')").fetchone()==(None,None)
