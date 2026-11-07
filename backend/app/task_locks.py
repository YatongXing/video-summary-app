"""Stable local Linux OFD locks. Closing the parent never unlocks a child FD."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import stat
from uuid import UUID
try:
    import fcntl
except ImportError:
    fcntl = None

class LockBusy(RuntimeError): pass
class LockUnsafe(RuntimeError): pass
_fds = ContextVar('producer_fds', default=())

def root():
    p=Path(os.getenv('DELETION_JOURNAL_DIR','/var/lib/video-summary-app/deletion-control'))
    if p.is_symlink(): raise LockUnsafe('CONTROL_UNSAFE')
    p.mkdir(parents=True,exist_ok=True,mode=0o700)
    return p

def runtime():
    p=root()/'runtime'
    if p.is_symlink():raise LockUnsafe('CONTROL_UNSAFE')
    p.mkdir(exist_ok=True,mode=0o700)
    return p

def name(identifier):
    value=str(identifier)
    if value.startswith('import-'):return 'import-'+str(UUID(value[7:]))
    return str(UUID(value))

def inherited_fds():return _fds.get()

@contextmanager
def _hold(key, shared=False):
    if fcntl is None:raise LockUnsafe('LINUX_LOCKS_REQUIRED')
    directory=root()/'locks'
    if directory.is_symlink():raise LockUnsafe('CONTROL_UNSAFE')
    directory.mkdir(exist_ok=True,mode=0o700)
    path=directory/(key+'.lock');anchor=directory/(key+'.anchor')
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    token=None
    try:
        try:os.link(path,anchor,follow_symlinks=False)
        except FileExistsError:pass
        def valid():
            a=os.lstat(anchor);p=os.lstat(path);f=os.fstat(fd)
            if not all(stat.S_ISREG(x.st_mode) for x in (a,p,f)) or len({(x.st_dev,x.st_ino) for x in (a,p,f)})!=1:
                raise LockUnsafe('LOCK_INODE_CHANGED')
        valid()
        try:fcntl.flock(fd,(fcntl.LOCK_SH if shared else fcntl.LOCK_EX)|fcntl.LOCK_NB)
        except BlockingIOError:raise LockBusy('PRODUCER_BUSY') from None
        valid()
        if shared:token=_fds.set(_fds.get()+(fd,))
        yield fd
        valid()
    finally:
        if token is not None:_fds.reset(token)
        os.close(fd) # Never LOCK_UN: children may still share the same OFD.

def producer(task_id):return _hold(name(task_id),True)
def exclusive(task_id):return _hold(name(task_id))
def cleanup_global():return _hold('cleanup-global')

def producer_ready():
    try:
        from app.deletion_control import identity_ready
        r=Path(os.getenv('DELETION_JOURNAL_DIR','/var/lib/video-summary-app/deletion-control'))
        directory=r/'runtime'
        if not r.is_dir() or r.is_symlink() or not directory.is_dir() or directory.is_symlink():return False
        identity=json.loads((r/'identity.json').read_text())
        ready_path=directory/'producer-ready'
        if ready_path.is_symlink():return False
        ready=json.loads(ready_path.read_text())
        return identity_ready() and ready=={'deployment_id':identity['deployment_id']}
    except Exception:return False
