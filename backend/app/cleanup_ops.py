"""Restricted deployment maintenance: no content, paths or forced deletion."""
import argparse
import json
import os
import time
from uuid import UUID
import psycopg
from psycopg.rows import dict_row
from app import cleanup,tasks,task_locks,object_store

class OpsError(RuntimeError):
    def __init__(self,code,exit_code=2):self.code,self.exit_code=code,exit_code;super().__init__(code)

def status(job_id,connection=None):
    if connection is None:
        with tasks.connect() as c:return status(job_id,c)
    row=connection.execute('SELECT * FROM cleanup_jobs WHERE id=%s',(job_id,)).fetchone()
    if not row:raise OpsError('JOB_UNAVAILABLE')
    return cleanup._receipt(connection,row)

def health():
    try:
        from pathlib import Path
        root=Path(os.getenv('DELETION_JOURNAL_DIR','/var/lib/video-summary-app/deletion-control'))
        directory=root/'runtime'
        if not root.is_dir() or root.is_symlink() or not directory.is_dir() or directory.is_symlink():raise ValueError()
        p=directory/'cleanup-progress.json'
        if p.is_symlink():raise ValueError()
        state=json.loads(p.read_text());now=time.time()
        completed=state.get('last_loop_completed')
        io_path=directory/'cleanup-io.json'
        if io_path.is_symlink():raise ValueError()
        io=json.loads(io_path.read_text());active=io.get('active_io_started')
        completed=max(completed or 0,io.get('last_io_completed') or 0)
        healthy=bool(isinstance(completed,(float,int)) and 0<=now-completed<=60 and(active is None or 0<=now-active<=60))
        from app.deletion_control import identity_ready
        healthy=healthy and identity_ready() and task_locks.producer_ready()
        with tasks.connect() as c:
            pending=c.execute("SELECT count(*) AS n FROM cleanup_jobs WHERE status<>'COMPLETED' AND(blocked_reason IS NOT NULL OR consecutive_failures>=5 OR started_at<clock_timestamp()-interval '30 minutes')").fetchone()['n']
            imports=c.execute("SELECT count(*) AS n FROM input_imports WHERE state='CLEANUP_REQUIRED'").fetchone()['n']
        return {'healthy':bool(healthy and not pending and not imports),'persistent_faults':pending,'import_faults':imports}
    except Exception:return {'healthy':False,'dependency_unknown':True}

def _maintenance(c):
    owner=c.execute("SELECT current_user AS actor,pg_get_userbyid(relowner) AS owner FROM pg_class WHERE oid='cleanup_jobs'::regclass").fetchone()
    if not owner or owner['actor']!=owner['owner']:raise OpsError('MAINTENANCE_REQUIRED')
    return owner['actor']

def retry(job_id,expected_version,reason='STORAGE_RECOVERED',connection=None):
    if reason!='STORAGE_RECOVERED' or type(expected_version)!=int or expected_version<0:raise OpsError('INVALID_ARGUMENT',3)
    if connection is None:
        dsn=os.environ.get('MAINTENANCE_DATABASE_URL')
        if not dsn:raise OpsError('MAINTENANCE_REQUIRED')
        with psycopg.connect(dsn,row_factory=dict_row) as c:return retry(job_id,expected_version,reason,c)
    c=connection
    with task_locks.cleanup_global():
        with c.transaction():
            actor=_maintenance(c)
            found=c.execute('SELECT * FROM cleanup_jobs WHERE id=%s',(job_id,)).fetchone()
            if not found:raise OpsError('JOB_UNAVAILABLE')
            if found['job_version']!=expected_version:raise OpsError('VERSION_CONFLICT',5)
            if found['status']=='COMPLETED' or found['status'] in ('PENDING','RETRY_WAIT'):return status(job_id,c)
            if found['blocked_reason']!='CONFIG':raise OpsError('UNSAFE_RETRY')
            if not task_locks.producer_ready():raise OpsError('PRODUCER_UNCONFIRMED')
            assets=c.execute("SELECT * FROM media_assets WHERE task_id=%s AND cleanup_state<>'DELETED' AND storage_backend LIKE 'S3%%'",(found['task_id'],)).fetchall()
            for a in assets:
                if a['storage_backend']!=object_store.backend_id() or a['storage_bucket']!=object_store.bucket() or a['storage_version']:raise OpsError('LOCATION_UNCONFIRMED')
            # Probe outside row locks. Never delete from a maintenance request.
            if assets:
                client=object_store.cleanup_client()
                cleanup._s3call(client,'head_bucket',Bucket=object_store.bucket())
                if cleanup._s3call(client,'get_bucket_versioning',Bucket=object_store.bucket()).get('Status') in ('Enabled','Suspended'):raise OpsError('VERSION_UNCONFIRMED')
            t,j=cleanup._locks(c,job_id)
            if j['job_version']!=expected_version:raise OpsError('VERSION_CONFLICT',5)
            if j['blocked_reason']!='CONFIG':raise OpsError('UNSAFE_RETRY')
            now=tasks.utcnow()
            row=c.execute("UPDATE cleanup_jobs SET status='PENDING',blocked_reason=NULL,job_version=job_version+1 WHERE id=%s RETURNING *",(job_id,)).fetchone()
            identity=c.execute('SELECT deployment_id FROM deletion_control_state WHERE singleton').fetchone()
            role=c.execute('SELECT oid FROM pg_roles WHERE rolname=current_user').fetchone()['oid']
            uid=os.getuid() if hasattr(os,'getuid') else -1
            if not identity:raise OpsError('CONTROL_UNKNOWN',4)
            evidence='STORAGE_RECOVERED:'+str(identity['deployment_id'])+':'+str(role)+':'+str(uid)
            cleanup._audit(c,t,None,evidence,'REQUESTED',now)
            # This is an instance/DB identity attestation, never a human operator ID.
            return dict(cleanup._receipt(c,row),maintenance_role=actor,container_uid=os.getuid() if hasattr(os,'getuid') else None)

class SafeParser(argparse.ArgumentParser):
    def error(self,message):raise OpsError('INVALID_ARGUMENT',3)

def main(argv=None):
    parser=SafeParser(description='Controlled cleanup maintenance')
    sub=parser.add_subparsers(dest='command',required=True)
    s=sub.add_parser('status');s.add_argument('--job-id',type=UUID,required=True);s.add_argument('--details',action='store_true')
    sub.add_parser('health')
    r=sub.add_parser('retry');r.add_argument('--job-id',type=UUID,required=True);r.add_argument('--expected-version',type=int,required=True);r.add_argument('--reason',choices=['STORAGE_RECOVERED'],required=True)
    try:args=parser.parse_args(argv)
    except SystemExit as exc:return 0 if exc.code==0 else 3
    except OpsError:print('{"error":"INVALID_ARGUMENT"}');return 3
    try:
        if args.command=='health':
            output=health();code=0 if output['healthy'] else 4 if output.get('dependency_unknown') else 2
        elif args.command=='status':output=status(args.job_id);code=0
        else:output=retry(args.job_id,args.expected_version,args.reason);code=0
        print(json.dumps(output,default=str));return code
    except OpsError as exc:print(json.dumps({'error':exc.code}));return exc.exit_code
    except task_locks.LockBusy:print('{"error":"CLEANUP_BUSY"}');return 2
    except Exception:print('{"error":"DEPENDENCY_UNKNOWN"}');return 4

if __name__=='__main__':raise SystemExit(main())
