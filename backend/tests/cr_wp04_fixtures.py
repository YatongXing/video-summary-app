"""WP04 isolated PostgreSQL, explicit producer evidence and synthetic asset helpers."""
import json
import os
from pathlib import Path
from uuid import uuid4
import pytest
from psycopg.conninfo import conninfo_to_dict
from test_cleanup import cleanup_db,queued,asset
from test_results import identity,claimed,publish,authenticated,client

@pytest.fixture
def wp04_db(monkeypatch,tmp_path):
    url=os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Explicit isolated WP04 TEST_DATABASE_URL required")
    p=conninfo_to_dict(url)
    assert p["dbname"]=="wp04_test" and p["host"]=="127.0.0.1"
    if os.environ.get('WP07A_ISOLATED_INSTANCE'):
        assert os.environ['WP07A_ISOLATED_INSTANCE'].startswith('vsa-wp07a-test-')
        assert p['port']==os.environ['WP07A_TEST_PORT']
    else:
        assert os.environ["WP04_ISOLATED_INSTANCE"].startswith("vsa-wp04-")
        assert p["port"]==os.environ["WP04_TEST_PORT"]
    monkeypatch.setenv("MODEL_CALLS_ENABLED","false")
    monkeypatch.setenv("S3_ENDPOINT_URL","http://wp04-synthetic:9000")
    fixture=cleanup_db.__wrapped__(monkeypatch,tmp_path)
    try:
        db=next(fixture)
        root=Path(os.environ["DELETION_JOURNAL_DIR"])
        runtime=root/"runtime";runtime.mkdir(exist_ok=True)
        deployment=db.execute("SELECT deployment_id FROM deletion_control_state").fetchone()[0]
        (runtime/"producer-ready").write_text(json.dumps({"deployment_id":str(deployment)}))
        yield db
    finally:fixture.close()

def object_asset(db,user,task,key=None,kind="ORIGINAL",bucket="wp04-synthetic"):
    from app.object_store import backend_id
    key=key or f"originals/{user}/{uuid4()}"
    return db.execute("""INSERT INTO media_assets(task_id,user_id,asset_kind,storage_key,storage_backend,storage_bucket)
        VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",(task,user,kind,key,backend_id(),bucket)).fetchone()[0]

def row(db,task):
    from psycopg.rows import dict_row
    with db.cursor(row_factory=dict_row) as c:
        return c.execute("SELECT * FROM cleanup_jobs WHERE task_id=%s",(task,)).fetchone()
