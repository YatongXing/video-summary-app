"""Persistent, owner-operated maintenance gate. No HTTP bypass exists."""
import argparse
import json
import os
from pathlib import Path
from uuid import UUID
import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from app.deletion_control import JournalError

class MaintenanceClosed(JournalError):
    pass

def required():
    value=os.getenv('MAINTENANCE_GATE_REQUIRED','false').lower()
    if value not in ('true','false'):raise MaintenanceClosed('Maintenance configuration unavailable')
    return value=='true'

def _paths():
    value=os.getenv('DELETION_JOURNAL_DIR')
    if not value:raise MaintenanceClosed('Maintenance control unavailable')
    root=Path(value);runtime=root/'runtime';state=runtime/'maintenance.json'
    if not root.is_dir() or root.is_symlink() or not runtime.is_dir() or runtime.is_symlink() or state.is_symlink() or (root/'identity.json').is_symlink():
        raise MaintenanceClosed('Maintenance control unavailable')
    return root,state

def read_state(connection=None):
    from app.deletion_control import identity_ready
    try:
        root,path=_paths()
        identity=json.loads((root/'identity.json').read_text(encoding='utf-8'))
        value=json.loads(path.read_text(encoding='utf-8'))
        if (set(value)!={'deployment_id','state'} or value['state'] not in ('OPEN','CLOSED')
            or str(UUID(value['deployment_id']))!=value['deployment_id']
            or identity.get('deployment_id')!=value['deployment_id'] or not identity_ready(connection)):raise ValueError()
        return value
    except Exception:
        raise MaintenanceClosed('Maintenance control unavailable') from None

def ensure_open():
    if required() and read_state()['state']!='OPEN':raise MaintenanceClosed('Service under maintenance')

def ready():
    try:ensure_open();return True
    except Exception:return False

def require_owner(connection):
    with connection.cursor(row_factory=dict_row) as c:
        row=c.execute("SELECT current_user AS actor,pg_get_userbyid(relowner) AS owner FROM pg_class WHERE oid='tasks'::regclass").fetchone()
        if not row or row['actor']!=row['owner']:raise MaintenanceClosed('Maintenance owner required')

def set_state(state,connection=None):
    if state not in ('OPEN','CLOSED'):raise MaintenanceClosed('Invalid maintenance state')
    if connection is None:
        dsn=os.getenv('MAINTENANCE_DATABASE_URL')
        if not dsn:raise MaintenanceClosed('Maintenance owner required')
        with psycopg.connect(dsn,row_factory=dict_row) as c:return set_state(state,c)
    from app.deletion_control import _write
    if state=='OPEN' and connection.info.transaction_status!=TransactionStatus.IDLE:
        raise MaintenanceClosed('Maintenance owner connection must be idle')
    require_owner(connection)
    with connection.cursor(row_factory=dict_row) as c:
        db=c.execute('SELECT deployment_id FROM deletion_control_state WHERE singleton').fetchone()
    try:
        root,path=_paths()
        identity=json.loads((root/'identity.json').read_text(encoding='utf-8'))
        if not db or identity!={'deployment_id':str(db['deployment_id'])}:raise ValueError()
        if state=='OPEN':
            from app.deletion_control import replay_as_owner
            replay_as_owner(connection)
            connection.commit()
        value={'deployment_id':str(db['deployment_id']),'state':state}
        _write(path,value)
        return value
    except Exception:raise MaintenanceClosed('Maintenance control unavailable') from None

class MaintenanceMiddleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope.get('path')=='/health/live':
            return await self.app(scope,receive,send)
        from starlette.concurrency import run_in_threadpool
        from starlette.responses import JSONResponse
        started=False
        suppressed=False
        async def check():await run_in_threadpool(ensure_open)
        async def refuse():
            nonlocal started,suppressed
            if suppressed:return
            suppressed=True
            if started:
                await send({'type':'http.response.body','body':b'','more_body':False})
            else:
                response=JSONResponse({'detail':'Service temporarily unavailable'},status_code=503,headers={'Cache-Control':'no-store'})
                await response(scope,receive,send)
                started=True
        async def gated_receive():
            if suppressed:return {'type':'http.disconnect'}
            try:
                await check();message=await receive();await check();return message
            except JournalError:
                # After headers, disconnect without inviting a second error response.
                if started:
                    await refuse()
                    return {'type':'http.disconnect'}
                raise
        async def gated_send(message):
            nonlocal started
            if suppressed:return
            try:await check()
            except JournalError:
                # Never raise a handled error from send after Starlette marked its response started.
                await refuse();return
            if message['type']=='http.response.start':started=True
            await send(message)
        try:
            await check()
            await self.app(scope,gated_receive,gated_send)
        except JournalError:
            await refuse()


def main():
    parser=argparse.ArgumentParser(description='Owner-only persistent maintenance gate')
    parser.add_argument('command',choices=['status','close','open']);args=parser.parse_args()
    try:
        if args.command=='status':
            value=read_state() if required() else {'state':'LEGACY_UNGATED'}
            print(json.dumps({'required':required(),'trusted':True,**value}));return 0
        print(json.dumps(set_state('CLOSED' if args.command=='close' else 'OPEN')));return 0
    except Exception:
        print(json.dumps({'trusted':False,'state':'UNKNOWN','error':'MAINTENANCE_UNAVAILABLE'}));return 2
if __name__=='__main__':raise SystemExit(main())
