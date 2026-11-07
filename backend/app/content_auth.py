"""Current-session and owner-only content authorization, independent of support."""
from app import maintenance
from datetime import timedelta
from fastapi import HTTPException


def session(c, user, role=None):
    maintenance.ensure_open()
    row=c.execute("""SELECT u.role FROM sessions s JOIN users u ON u.id=s.user_id
        WHERE s.id=%s AND s.user_id=%s AND s.revoked_at IS NULL
        AND s.expires_at>clock_timestamp() AND u.disabled_at IS NULL""",
        (user.session_id,user.id)).fetchone()
    if row is None:raise HTTPException(401,'Login required')
    if row['role'] not in ('USER','ADMIN') or row['role']!=user.role or (role and row['role']!=role):
        raise HTTPException(403,'Role changed or unavailable')
    return row['role']


def authorize_owner(c,user,task_id):
    maintenance.ensure_open()
    from app.deletion_control import blocked, identity_ready, JournalError
    if not identity_ready():raise JournalError('Deletion control unavailable')
    # One statement observes the session, actual role, owner and task clock.
    t=c.execute("""WITH checked AS MATERIALIZED (SELECT clock_timestamp() AS at)
        SELECT t.*,checked.at AS checked_at FROM tasks t
        JOIN users actor ON actor.id=t.user_id
        JOIN sessions s ON s.user_id=actor.id CROSS JOIN checked
        WHERE t.id=%s AND t.user_id=%s AND s.id=%s
        AND s.revoked_at IS NULL AND s.expires_at>checked.at
        AND actor.disabled_at IS NULL AND actor.role IN ('USER','ADMIN')
        AND actor.role=%s""",(task_id,user.id,user.session_id,user.role)).fetchone()
    if t is None:
        session(c,user)
        raise HTTPException(404,'Task unavailable')
    if (t['deletion_requested_at'] or t['deleted_at']
        or t['created_at']+timedelta(days=30)<=t['checked_at'] or blocked(t['id'])):
        raise HTTPException(404,'Task unavailable')
    return t
