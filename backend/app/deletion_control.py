"""Minimal independent deletion journal and explicit offline restore gate.

Not a backup engine. The journal volume must not be rolled back with a database
snapshot. No body, filename, credentials or media paths are written to it.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from uuid import UUID, uuid4
from botocore.exceptions import ClientError
from app import tasks, object_store

class JournalError(RuntimeError):
    pass


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


def identity_ready():
    root=_root()
    if root is None:return True
    try:
        external=json.loads((root/'identity.json').read_text(encoding='utf-8'))
        with tasks.connect() as c:
            row=c.execute('SELECT deployment_id FROM deletion_control_state WHERE singleton').fetchone()
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
    root=_root()
    if root is None:return []
    initialize()
    from app import cleanup
    replayed=[];seen=set()
    for path in sorted(root.glob('*.json')):
        if path.name=='identity.json':continue
        entry=_read(path);when=_instant(entry['requested_at']);seen.add(entry['task_id'])
        with tasks.connect() as c:
            task=c.execute('SELECT user_id,deletion_requested_at FROM tasks WHERE id=%s',(entry['task_id'],)).fetchone()
        if task is None:continue
        if str(task['user_id'])!=entry['user_id']:raise JournalError('Restored task owner does not match deletion journal')
        if force or task['deletion_requested_at'] is None:
            cleanup.request_delete(entry['task_id'],entry['user_id'],reason='RESTORE_REPLAY',record=False,requested_at=when)
            if force:
                with tasks.connect() as c:
                    c.execute("UPDATE media_assets SET cleanup_state='REGISTERED',deleted_at=NULL WHERE task_id=%s",(entry['task_id'],))
                    c.execute('UPDATE tasks SET deleted_at=NULL WHERE id=%s',(entry['task_id'],))
                    c.execute("UPDATE cleanup_jobs SET status='PENDING',completed_at=NULL,next_attempt_at=now(),backup_cleanup_state='PENDING',backup_completed_at=NULL WHERE task_id=%s",(entry['task_id'],))
            replayed.append(entry['task_id'])
    if force:
        # A fresh object restore can recreate an already-deleted copy even if
        # its 90-day journal entry has expired. Retained DB controls still apply.
        with tasks.connect() as c:
            remaining=c.execute('SELECT id,user_id,deletion_requested_at FROM tasks WHERE deletion_requested_at IS NOT NULL').fetchall()
        for task in remaining:
            if str(task['id']) not in seen:
                cleanup.request_delete(task['id'],task['user_id'],reason='RESTORE_REPLAY',record=False,requested_at=task['deletion_requested_at'])
                replayed.append(str(task['id']))
        # A snapshot must not revive an old session or revoked content consent.
        # Owners sign in and grant fresh task-specific access after restoration.
        with tasks.connect() as c:
            c.execute('UPDATE sessions SET revoked_at=now() WHERE revoked_at IS NULL')
            c.execute('UPDATE task_consents SET revoked_at=now() WHERE revoked_at IS NULL')
    return replayed


def prune_journal(now=None):
    root=_root()
    if root is None:return
    now=now or tasks.utcnow()
    for path in root.glob('*.json'):
        if path.name=='identity.json':continue
        entry=_read(path)
        if now-_instant(entry['requested_at'])>=timedelta(days=90):path.unlink()


def cleanup_backups(task_id):
    """Clean registered task-specific S3 backup copies; unknown inventory stays pending.

    Whole database/disk snapshots are not guessed or erased. They require the
    explicit operator inventory/cleanup procedure before a complete claim.
    """
    with tasks.connect() as c:
        assets=c.execute("SELECT id,storage_key FROM media_assets WHERE task_id=%s AND asset_kind='BACKUP' AND cleanup_state<>'DELETED'",(task_id,)).fetchall()
    failed=False
    for asset in assets:
        try:
            client=object_store.client();key=asset['storage_key']
            client.delete_object(Bucket=object_store.bucket(),Key=key)
            try:client.head_object(Bucket=object_store.bucket(),Key=key)
            except ClientError as error:
                if str(error.response['Error']['Code']) not in ('404','NoSuchKey','NotFound','NoSuchBucket'):raise
            else:raise JournalError('Registered backup is still present')
            with tasks.connect() as c:c.execute("UPDATE media_assets SET cleanup_state='DELETED',deleted_at=now() WHERE id=%s",(asset['id'],))
        except Exception:
            failed=True
            with tasks.connect() as c:c.execute("UPDATE media_assets SET cleanup_state='FAILED' WHERE id=%s",(asset['id'],))
    if failed:return 'FAILED'
    # False is deliberate: no backup inventory has been attested in this MVP.
    if os.getenv('BACKUP_INVENTORY_CONFIRMED','false').lower()!='true':return 'PENDING'
    return 'COMPLETED'


def main():
    parser=argparse.ArgumentParser(description='Offline deletion replay before restoring service access')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('check')
    restore=sub.add_parser('restore')
    restore.add_argument('--snapshot-created-at',required=True)
    args=parser.parse_args()
    if _root() is None:raise JournalError('An independent deletion journal must be configured')
    if args.command=='restore':
        stamp=_instant(args.snapshot_created_at);age=tasks.utcnow()-stamp
        if age<timedelta(0) or age>=timedelta(days=90):
            raise JournalError('Snapshot is outside the retained deletion journal window; cannot safely reopen it')
        replayed=replay_journal(force=True)
    else:replayed=replay_journal()
    print('Deletion control checked; replayed tasks:',len(replayed))
    return 0

if __name__=='__main__':raise SystemExit(main())
