"""Independent WP03 fixtures: real disposable PG, ASGI and loopback HTTP."""
import asyncio
import io
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest
from test_results import result_db, identity, claimed, publish, authenticated

@pytest.fixture
def wp03_db(monkeypatch, tmp_path):
    params=conninfo_to_dict(os.environ["TEST_DATABASE_URL"])
    assert params["host"]=="127.0.0.1"
    if params["dbname"]=="wp04_test":
        if os.environ.get('WP07A_ISOLATED_INSTANCE'):
            assert os.environ['WP07A_ISOLATED_INSTANCE'].startswith('vsa-wp07a-test-')
            assert params['port']==os.environ['WP07A_TEST_PORT']
        else:
            assert os.environ["WP04_ISOLATED_INSTANCE"].startswith("vsa-wp04-")
            assert params["port"]==os.environ["WP04_TEST_PORT"]
    else:
        assert params["dbname"]=="wp03_test"
        assert os.environ["WP03_ISOLATED_INSTANCE"].startswith("vsa-wp03-")
        assert params["port"]==os.environ["WP03_TEST_PORT"]
    monkeypatch.setenv("DELETION_JOURNAL_DIR",str(tmp_path/"journal"))
    monkeypatch.setenv("IMPORT_STAGING_DIR",str(tmp_path/"staging"))
    monkeypatch.setenv("MODEL_CALLS_ENABLED","false")
    from app.deletion_control import initialize
    resources=result_db.__wrapped__(monkeypatch)
    try:
        db=next(resources)
        initialize()
        yield db
    finally:
        resources.close()

def client():
    from app.main import app
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://127.0.0.1:8000")

def headers(c):
    from app import auth
    return {"Origin":"http://127.0.0.1:8000","X-CSRF-Token":auth.csrf_token(c.cookies.get(auth.COOKIE_NAME,""))}


def seed_request(db,u,t,scope="summary",remaining=3600):
    now=db.execute("SELECT clock_timestamp()").fetchone()[0]
    return db.execute("""INSERT INTO support_requests(task_id,user_id,scope,created_at,expires_at)
     VALUES(%s,%s,%s,%s,%s) RETURNING id""",
     (t,u,scope,now+timedelta(seconds=remaining-86400),now+timedelta(seconds=remaining))).fetchone()[0]

class Store:
    def __init__(self,callback=None):self.callback=callback
    def head_object(self,**kw):return {"ContentLength":600000}
    def get_object(self,**kw):
        callback=self.callback
        class Stream(io.BytesIO):
            def read(self,size=-1):
                out=super().read(size)
                if callback:callback()
                return out
        return {"Body":Stream(b"x"*600000)}

@contextmanager
def live_server(tmp_path):
    # Actual TCP/uvicorn process. Storage is an explicit synthetic adapter.
    with socket.socket() as s:s.bind(("127.0.0.1",0));port=s.getsockname()[1]
    code="""import io,uvicorn
from app import results
class Store:
 def head_object(self,**kw):return {'ContentLength':10}
 def get_object(self,**kw):
  start,end=map(int,kw['Range'][6:].split('-'))
  return {'Body':io.BytesIO(b'0123456789'[start:end+1])}
results.object_store.client=lambda:Store()
from app.main import app
uvicorn.run(app,host='127.0.0.1',port=PORT,log_level='error',access_log=False)
""".replace("PORT",str(port))
    env={k:v for k,v in os.environ.items() if k in ("SYSTEMROOT","WINDIR","PATH","TEMP","TMP","DATABASE_URL","DELETION_JOURNAL_DIR","IMPORT_STAGING_DIR")}
    env["PYTHONPATH"]=str(Path(__file__).parents[1]);env["MODEL_CALLS_ENABLED"]="false"
    with (tmp_path/"http.log").open("wb") as log:
        proc=subprocess.Popen([sys.executable,"-c",code],env=env,stdout=log,stderr=log)
        try:
            for _ in range(200):
                if proc.poll() is not None:raise AssertionError("Synthetic HTTP server exited")
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/health/live",timeout=.5).status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.05)
            else:raise AssertionError("HTTP readiness timeout")
            yield f"http://127.0.0.1:{port}"
        finally:
            proc.terminate()
            try:proc.wait(timeout=8)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
