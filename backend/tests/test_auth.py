"""Independent S3-03 HTTP and PostgreSQL acceptance checks.

Opt in with TEST_DATABASE_URL naming an isolated *_test database. Each test
owns and removes a fresh schema; no local environment file is read.
"""
import asyncio
import os
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest

PASSWORD = "Only-for-tests-93!"
ORIGIN = "http://127.0.0.1:8000"


@pytest.fixture
def auth_db(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable *_test database")
    if not conninfo_to_dict(url).get("dbname", "").endswith("_test"):
        pytest.fail("Authentication tests require a *_test database")
    schema = "auth_test_" + uuid4().hex
    with psycopg.connect(url, autocommit=True) as control:
        control.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        scoped = make_conninfo(url, options=f"-csearch_path={schema}")
        monkeypatch.setenv("DATABASE_URL", scoped)
        from app.migrate import run_migrations
        try:
            with psycopg.connect(scoped, autocommit=True) as connection:
                run_migrations(connection)
                yield connection
        finally:
            control.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def client(source="127.0.0.1"):
    from app.main import app
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(source, 12345)),
        base_url=ORIGIN, headers={"Origin": ORIGIN},
    )


async def register(c, email="owner@example.invalid"):
    response = await c.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return response.json()


async def login(c, email="owner@example.invalid"):
    response = await c.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response


def test_registration_persists_hash_and_login_logout(auth_db):
    async def scenario():
        async with client() as c:
            registered = await register(c)
            assert registered["role"] == "USER"
            row = auth_db.execute("SELECT password_hash FROM users").fetchone()
            assert row and row[0] != PASSWORD and PASSWORD not in row[0]
            assert (await c.get("/auth/me")).status_code == 401
            response = await login(c)
            cookie = response.headers["set-cookie"].lower()
            assert "httponly" in cookie and "samesite=strict" in cookie
            assert "max-age=" not in cookie and "expires=" not in cookie
            original_cookie = dict(c.cookies)
            payload = response.json()
            assert payload["user"]["id"] == registered["id"]
            assert payload["csrf_token"]
            token_hash = auth_db.execute("SELECT token_hash FROM sessions").fetchone()[0]
            assert all(value != token_hash for value in c.cookies.values())
            assert (await c.get("/auth/me")).status_code == 200
            assert (await c.post("/auth/logout")).status_code == 403
            result = await c.post("/auth/logout", headers={"X-CSRF-Token": payload["csrf_token"]})
            assert result.status_code == 204
            assert (await c.get("/auth/me")).status_code == 401
            c.cookies.update(original_cookie)
            assert (await c.get("/auth/me")).status_code == 401
            await login(c)
    asyncio.run(scenario())


def test_registration_rejects_escalation_and_normalizes_email(auth_db):
    async def scenario():
        async with client() as c:
            response = await c.post("/auth/register", json={
                "email": "intruder@example.invalid", "password": PASSWORD, "role": "ADMIN"})
            assert response.status_code in (400, 422)
            assert PASSWORD not in response.text
            assert auth_db.execute("SELECT count(*) FROM users").fetchone()[0] == 0
            await register(c, "Mixed@Example.invalid")
            response = await c.post("/auth/register", json={
                "email": "mixed@example.invalid", "password": PASSWORD})
            assert response.status_code == 409
            assert auth_db.execute("SELECT count(*) FROM users").fetchone()[0] == 1
    asyncio.run(scenario())


def test_anonymous_cross_user_and_deleted_task_access(auth_db):
    async def scenario():
        async with client("127.0.0.2") as owner, client("127.0.0.3") as other:
            user = await register(owner)
            await register(other, "other@example.invalid")
            task = auth_db.execute(
                "INSERT INTO tasks(user_id,input_kind) VALUES (%s,'LOCAL') RETURNING id",
                (user["id"],)).fetchone()[0]
            assert (await owner.get(f"/tasks/{task}")).status_code == 401
            await login(owner)
            other_login = (await login(other, "other@example.invalid")).json()
            assert (await owner.get(f"/tasks/{task}")).status_code == 200
            assert (await other.get(f"/tasks/{task}")).status_code in (403, 404)
            assert (await other.get("/admin/tasks")).status_code == 403
            blocked = await other.post(f"/tasks/{task}/support-requests", json={"operation":"CREATE","scope":"video","expected_version":0}, headers={"X-CSRF-Token": other_login["csrf_token"]})
            assert blocked.status_code == 410
            auth_db.execute("UPDATE tasks SET deletion_requested_at=now() WHERE id=%s", (task,))
            assert (await owner.get(f"/tasks/{task}")).status_code in (403, 404)
    asyncio.run(scenario())


def test_expired_session_is_rejected(auth_db):
    async def scenario():
        async with client() as c:
            await register(c)
            await login(c)
            auth_db.execute("UPDATE sessions SET created_at=now()-interval '2 days', "
                            "expires_at=now()-interval '1 day'")
            assert (await c.get("/auth/me")).status_code == 401
    asyncio.run(scenario())


def test_external_origin_and_account_failure_limit(auth_db):
    async def scenario():
        async with client() as c:
            response = await c.post("/auth/register", headers={"Origin": "https://evil.invalid"},
                                   json={"email": "bad@example.invalid", "password": PASSWORD})
            assert response.status_code == 403
            await register(c)
            for _ in range(5):
                response = await c.post("/auth/login", json={
                    "email": "owner@example.invalid", "password": "wrong-password"})
                assert response.status_code in (401, 429)
            response = await c.post("/auth/login", json={
                "email": "owner@example.invalid", "password": PASSWORD})
            assert response.status_code == 429
        async with client("127.0.0.22") as other_source:
            response = await other_source.post("/auth/login", json={
                "email": "owner@example.invalid", "password": PASSWORD})
            assert response.status_code == 429
            auth_db.execute("UPDATE auth_limits SET window_started_at=now()-interval '1 hour', blocked_until=now()-interval '1 second'")
            await login(other_source)
    asyncio.run(scenario())


def test_disabled_user_loses_existing_session_and_cannot_login(auth_db):
    async def scenario():
        async with client() as c:
            user = await register(c)
            await login(c)
            auth_db.execute("UPDATE users SET disabled_at=now() WHERE id=%s", (user["id"],))
            assert (await c.get("/auth/me")).status_code == 401
            response = await c.post("/auth/login", json={"email": user["email"], "password": PASSWORD})
            assert response.status_code == 401
    asyncio.run(scenario())


def test_retired_admin_content_preserves_metadata_and_csrf(auth_db):
    async def scenario():
        async with client("127.0.0.4") as owner, client("127.0.0.5") as admin:
            user=await register(owner)
            administrator=await register(admin,"admin@example.invalid")
            auth_db.execute("UPDATE users SET role='ADMIN' WHERE id=%s",(administrator["id"],))
            task=auth_db.execute("INSERT INTO tasks(user_id,input_kind) VALUES(%s,'LOCAL') RETURNING id",(user["id"],)).fetchone()[0]
            owner_login=(await login(owner)).json();await login(admin,"admin@example.invalid")
            metadata=await admin.get("/admin/tasks")
            assert metadata.status_code==200
            assert "storage_key" not in metadata.text and "password_hash" not in metadata.text
            assert (await admin.get(f"/admin/tasks/{task}/video?purpose=old")).status_code==410
            body={"operation":"CREATE","scope":"video","expected_version":0}
            assert (await owner.post(f"/tasks/{task}/support-requests",json=body)).status_code==403
            h={"X-CSRF-Token":owner_login["csrf_token"]}
            grant=await owner.post(f"/tasks/{task}/support-requests",json=body,headers=h)
            assert grant.status_code==410,grant.text
            assert (await admin.get(f"/tasks/{task}/result")).status_code==404
            assert auth_db.execute("SELECT count(*) FROM support_requests WHERE task_id=%s",(task,)).fetchone()==(0,)
            assert (await admin.get("/admin/support-requests")).status_code==410
    asyncio.run(scenario())


def test_source_failure_limit_cannot_be_bypassed_with_forwarded_header(auth_db):
    async def scenario():
        async with client("127.0.0.33") as c:
            for index in range(20):
                response = await c.post("/auth/login", headers={"X-Forwarded-For": f"192.0.2.{index}"},
                    json={"email": f"missing{index}@example.invalid", "password": "wrong-password"})
                assert response.status_code == 401, response.text
            response = await c.post("/auth/login", headers={"X-Forwarded-For": "203.0.113.9"},
                json={"email": "another@example.invalid", "password": "wrong-password"})
            assert response.status_code == 429
    asyncio.run(scenario())


def test_registration_source_limit_is_configurable(auth_db, monkeypatch):
    monkeypatch.setenv("AUTH_REGISTER_SOURCE_LIMIT", "2")
    async def scenario():
        async with client("127.0.0.44") as c:
            await register(c, "first@example.invalid")
            await register(c, "second@example.invalid")
            hashes = auth_db.execute("SELECT password_hash FROM users").fetchall()
            assert hashes[0][0] != hashes[1][0]
            response = await c.post("/auth/register", json={"email": "third@example.invalid", "password": PASSWORD})
            assert response.status_code == 429
            assert auth_db.execute("SELECT count(*) FROM users").fetchone()[0] == 2
    asyncio.run(scenario())


def test_deployer_admin_creation_and_disable(auth_db):
    from app.admin import create_admin, disable_user
    create_admin("deployer@example.invalid", PASSWORD)
    row = auth_db.execute("SELECT role,password_hash FROM users WHERE email_normalized=%s", ("deployer@example.invalid",)).fetchone()
    assert row[0] == "ADMIN" and row[1] != PASSWORD
    async def scenario():
        async with client() as c:
            await login(c, "deployer@example.invalid")
            assert (await c.get("/admin/tasks")).status_code == 200
            disable_user("deployer@example.invalid")
            assert (await c.get("/auth/me")).status_code == 401
            assert auth_db.execute("SELECT count(*) FROM sessions WHERE revoked_at IS NULL").fetchone()[0] == 0
    asyncio.run(scenario())


def test_admin_cli_cannot_promote_existing_user_or_read_noninteractive_password(auth_db, monkeypatch):
    from app import admin
    async def setup():
        async with client() as c:
            await register(c)
    asyncio.run(setup())
    with pytest.raises(ValueError):
        admin.create_admin("owner@example.invalid", PASSWORD)
    assert auth_db.execute("SELECT role FROM users WHERE email_normalized='owner@example.invalid'").fetchone()[0] == "USER"
    monkeypatch.setattr("sys.argv", ["admin", "create-admin", "--email", "new@example.invalid"])
    class NonInteractive:
        def isatty(self):
            return False
    monkeypatch.setattr("sys.stdin", NonInteractive())
    def forbidden_getpass(*args):
        pytest.fail("Noninteractive creation must refuse before reading a password")
    monkeypatch.setattr(admin, "getpass", forbidden_getpass)
    assert admin.main() == 2
    assert auth_db.execute("SELECT count(*) FROM users").fetchone()[0] == 1
