"""Disposable WP02 instance only; never reads DATABASE_URL or .env."""
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest

MIGRATIONS = Path(__file__).parents[1] / "app" / "migrations"


@dataclass
class TestDB:
    __test__ = False
    url: str = field(repr=False)
    schema: str

    @contextmanager
    def connect(self, *, autocommit=True, user=None, password=None):
        params = conninfo_to_dict(self.url)
        if user:
            params.update(user=user, password=password)
        with psycopg.connect(make_conninfo(**params), autocommit=autocommit) as c:
            c.execute("SELECT set_config('search_path', %s, false)", (self.schema,))
            c.execute("SET statement_timeout = '8s'")
            c.execute("SET lock_timeout = '5s'")
            if not autocommit:
                c.commit()
            yield c


@pytest.fixture
def wp02_factory():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Explicit isolated WP02 TEST_DATABASE_URL required")
    params = conninfo_to_dict(url)
    assert params.get("dbname") == "wp02_test"
    assert params.get("host") == "127.0.0.1"
    assert params.get("user") == "wp02_bootstrap"
    if os.environ.get("WP07A_ISOLATED_INSTANCE"):
        assert os.environ["WP07A_ISOLATED_INSTANCE"].startswith("vsa-wp07a-test-")
        assert params.get("port") == os.environ.get("WP07A_TEST_PORT")
    else:
        assert os.environ.get("WP02_ISOLATED_INSTANCE", "").startswith("vsa-wp02-")
        assert params.get("port") == os.environ.get("WP02_TEST_PORT")
    schemas = []

    def create(*, migrate=True):
        name = "wp02_" + uuid4().hex
        with psycopg.connect(url, autocommit=True) as c:
            assert c.info.server_version // 10000 == 17
            c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        schemas.append(name)
        db = TestDB(url, name)
        if migrate:
            from app.migrate import run_migrations
            with db.connect() as c:
                run_migrations(c)
        return db

    yield create
    with psycopg.connect(url, autocommit=True) as c:
        for name in schemas:
            assert name.startswith("wp02_") and len(name) == 37
            c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture
def wp02_db(wp02_factory):
    return wp02_factory()


def seed(c, role="USER", status="QUEUED"):
    user = c.execute("INSERT INTO users(email_normalized,password_hash,role) VALUES(%s,'synthetic',%s) RETURNING id",
                     (f"{uuid4()}@example.invalid", role)).fetchone()[0]
    task = c.execute("INSERT INTO tasks(user_id,input_kind,status) VALUES(%s,'LOCAL',%s) RETURNING id",
                     (user, status)).fetchone()[0]
    return user, task


def request(c, user, task, scope="summary", *, created=None):
    if created is None:
        created = c.execute("SELECT clock_timestamp()").fetchone()[0]
    from datetime import timedelta
    return c.execute("""INSERT INTO support_requests(task_id,user_id,scope,created_at,expires_at)
                        VALUES(%s,%s,%s,%s,%s) RETURNING id""",
                     (task, user, scope, created, created + timedelta(seconds=86400))).fetchone()[0]
