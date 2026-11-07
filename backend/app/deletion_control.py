"""Minimal independent deletion journal and explicit offline restore gate.

Not a backup engine. The journal volume must not be rolled back with a database
snapshot. No body, filename, credentials or media paths are written to it.
"""
import argparse
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from uuid import UUID, uuid4
from botocore.exceptions import ClientError
from psycopg.rows import dict_row
class JournalError(RuntimeError):
    pass

from app import tasks, object_store


def _root():
    value=os.getenv('DELETION_JOURNAL_DIR')
    return Path(value) if value else None


def _write(path,value):
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as target:
            json.dump(value,target,ensure_ascii=True,sort_keys=True)
            target.flush();os.fsync(target.fileno())
        os.replace(temporary,path)
        # Persist the directory entry on the Linux runtime volume as well.
        if os.name!='nt':
            descriptor=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)
    finally:temporary.unlink(missing_ok=True)


def _instant(value):
    result=datetime.fromisoformat(value)
    if result.tzinfo is None:raise JournalError('Deletion time must include its timezone')
    return result.astimezone(timezone.utc)


def initialize():
    root=_root()
    if root is None:return
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    identity=root/'identity.json'
    with tasks.connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(773509009)')
        stored=c.execute('SELECT deployment_id FROM deletion_control_state WHERE singleton').fetchone()
        try: external=json.loads(identity.read_text(encoding='utf-8')) if identity.exists() else None
        except (OSError,ValueError):raise JournalError('Independent deletion journal identity is unreadable') from None
        if stored:
            if not external or external.get('deployment_id')!=str(stored['deployment_id']):
                raise JournalError('Independent deletion journal is missing or belongs to another deployment')
        else:
            identifier=UUID(external['deployment_id']) if external else uuid4()
            if external is None:_write(identity,{'deployment_id':str(identifier)})
            c.execute('INSERT INTO deletion_control_state(deployment_id) VALUES (%s)',(identifier,))


def identity_ready(connection=None):
    root=_root()
    if root is None:return True
    try:
        external=json.loads((root/'identity.json').read_text(encoding='utf-8'))
        with (tasks.connect() if connection is None else nullcontext(connection)) as c:
            with c.cursor(row_factory=dict_row) as cursor:
                row=cursor.execute('SELECT deployment_id FROM deletion_control_state WHERE singleton').fetchone()
        return bool(row and external.get('deployment_id')==str(row['deployment_id']))
    except Exception:return False


def record_intent(task_id,user_id,when):
    root=_root()
    if root is None:return
    # A normal running deployment has already initialized. Never silently
    # recreate a lost journal during a delete request.
    if not (root/'identity.json').is_file():raise JournalError('Deletion journal is not initialized')
    path=root/(str(UUID(str(task_id)))+'.json')
    value={'task_id':str(UUID(str(task_id))),'user_id':str(UUID(str(user_id))),
           'requested_at':when.astimezone(timezone.utc).isoformat()}
    if path.exists():
        old=_read(path)
        if old['user_id']!=value['user_id']:raise JournalError('Deletion journal owner mismatch')
        if _instant(old['requested_at'])<=when:return
    _write(path,value)


def _read(path):
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        if set(value)!= {'task_id','user_id','requested_at'}:raise ValueError()
        if str(UUID(value['task_id']))+'.json'!=path.name:raise ValueError()
        UUID(value['user_id']);_instant(value['requested_at'])
        return value
    except (OSError,ValueError,KeyError,TypeError):raise JournalError('Deletion journal entry is invalid') from None


def blocked(task_id):
    root=_root()
    return bool(root and (root/(str(UUID(str(task_id)))+'.json')).exists())


def replay_journal(force=False):
    return _replay_journal(force)

def replay_as_owner(connection, force=False):
    from app import maintenance
    maintenance.require_owner(connection)
    maintenance.read_state(connection)
    return _replay_journal(force, owner=connection)

def _replay_journal(force, owner=None):
    from app import cleanup, maintenance
    def check_gate():
        if owner is None:maintenance.ensure_open()

    check_gate()
    root=_root()
    if root is None:return []
    if owner is None and not maintenance.required():initialize()
    connect = tasks.connect if owner is None else lambda: nullcontext(owner)
    replayed=[];seen=set()
    for path in sorted(root.glob('*.json')):
        if path.name=='identity.json':continue
        check_gate()
        entry=_read(path);when=_instant(entry['requested_at']);seen.add(entry['task_id'])
        with connect() as c:
            task=c.execute('SELECT user_id,deletion_requested_at FROM tasks WHERE id=%s',(entry['task_id'],)).fetchone()
            check_gate()
        if task is None:continue
        if str(task['user_id'])!=entry['user_id']:raise JournalError('Restored task owner does not match deletion journal')
        if force or task['deletion_requested_at'] is None:
            cleanup._request_delete(entry['task_id'],entry['user_id'],'RESTORE_REPLAY',False,when,owner)
            if force:
                with connect() as c:
                    c.execute("UPDATE media_assets SET cleanup_state='REGISTERED',deleted_at=NULL WHERE task_id=%s",(entry['task_id'],))
                    c.execute('UPDATE tasks SET deleted_at=NULL WHERE id=%s',(entry['task_id'],))
                    c.execute("UPDATE cleanup_jobs SET status='PENDING',blocked_reason=NULL,completed_at=NULL,online_completed_at=NULL,next_attempt_at=now(),job_version=job_version+1 WHERE task_id=%s",(entry['task_id'],))
                    check_gate()
            replayed.append(entry['task_id'])
    if force:
        # A fresh object restore can recreate an already-deleted copy even if
        # its 90-day journal entry has expired. Retained DB controls still apply.
        check_gate()
        with connect() as c:
            remaining=c.execute('SELECT id,user_id,deletion_requested_at FROM tasks WHERE deletion_requested_at IS NOT NULL').fetchall()
            check_gate()
        for task in remaining:
            if str(task['id']) not in seen:
                cleanup._request_delete(task['id'],task['user_id'],'RESTORE_REPLAY',False,task['deletion_requested_at'],owner)
                replayed.append(str(task['id']))
        # A snapshot must not revive an old session or revoked content consent.
        # Owners sign in and grant fresh task-specific access after restoration.
        check_gate()
        with connect() as c:
            c.execute('UPDATE sessions SET revoked_at=now() WHERE revoked_at IS NULL')
            c.execute('UPDATE task_consents SET revoked_at=now() WHERE revoked_at IS NULL')
            rows=c.execute("UPDATE support_requests SET closed_at=clock_timestamp(),close_reason='REVOKED' WHERE closed_at IS NULL RETURNING task_id").fetchall()
            for row in rows:c.execute('UPDATE tasks SET support_version=support_version+1 WHERE id=%s',(row['task_id'],))
            check_gate()
    check_gate()
    return replayed


def prune_journal(now=None):
    from app import maintenance
    maintenance.ensure_open()
    root=_root()
    if root is None:return
    if not identity_ready():raise JournalError('Deletion identity unavailable')
    now=now or tasks.utcnow()
    for path in root.glob('*.json'):
        if path.name=='identity.json':continue
        entry=_read(path)
        if now-_instant(entry['requested_at'])>=timedelta(days=90):
            with tasks.connect() as c:
                job=c.execute("SELECT status,completed_at FROM cleanup_jobs WHERE task_id=%s",(entry['task_id'],)).fetchone()
            if job and job['status']=='COMPLETED' and job['completed_at'] is not None and now-job['completed_at']>=timedelta(days=90):
                maintenance.ensure_open()
                path.unlink()


def cleanup_backups(task_id):
    # Compatibility entry; approval and all physical work share cleanup locks.
    from app.cleanup import process_job
    with tasks.connect() as c:
        j=c.execute('SELECT id FROM cleanup_jobs WHERE task_id=%s',(task_id,)).fetchone()
    if not j:return 'PENDING'
    result=process_job(j['id'])
    return 'COMPLETED' if result and result['status']=='COMPLETED' else 'PENDING'

def main():
    parser=argparse.ArgumentParser(description='Offline deletion replay before restoring service access')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('check')
    restore=sub.add_parser('restore')
    restore.add_argument('--snapshot-created-at',required=True)
    args=parser.parse_args()
    if _root() is None:raise JournalError('An independent deletion journal must be configured')
    force=args.command=='restore'
    if force:
        stamp=_instant(args.snapshot_created_at);age=tasks.utcnow()-stamp
        if age<timedelta(0) or age>=timedelta(days=90):
            raise JournalError('Snapshot is outside the retained deletion journal window; cannot safely reopen it')
    from app import maintenance
    if maintenance.required():
        import psycopg
        dsn=os.getenv('MAINTENANCE_DATABASE_URL')
        if not dsn:raise JournalError('Maintenance owner required')
        try:
            with psycopg.connect(dsn,row_factory=dict_row) as connection:
                replayed=replay_as_owner(connection,force)
        except Exception:raise JournalError('Maintenance replay unavailable') from None
    else:replayed=replay_journal(force)
    print('Deletion control checked; replayed tasks:',len(replayed))
    return 0

if __name__=='__main__':raise SystemExit(main())
