"""Server-side identity, sessions and reusable task authorization boundaries."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import re
import secrets
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

router = APIRouter()
COOKIE_NAME = 'vsa_session'


def connect():
    return psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row)


def setting(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise RuntimeError('Authentication settings must be positive')
    return value


def normalize_email(value: str) -> str:
    value = value.strip().lower()
    if not value.isprintable() or len(value) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        raise ValueError('Invalid email')
    return value


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=3, maxmem=64 * 1024 * 1024)
    return f'scrypt$32768$8$3${salt.hex()}${digest.hex()}'


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = stored.split('$')
        if (algorithm, n, r, p) != ('scrypt', '32768', '8', '3'):
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_token(token: str) -> str:
    return digest('csrf:' + token)


class Credentials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator('email')
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value)


class Registration(Credentials):
    password: str = Field(min_length=8, max_length=128)


class ConsentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    purpose: str = Field(min_length=1, max_length=200)
    access_scope: str

    @field_validator('purpose')
    @classmethod
    def valid_purpose(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Purpose required')
        return value

    @field_validator('access_scope')
    @classmethod
    def valid_scope(cls, value):
        if value not in ('video', 'summary'):
            raise ValueError('Invalid scope')
        return value


@dataclass(frozen=True)
class Principal:
    id: UUID
    email: str
    role: str
    session_id: UUID


def user_json(user):
    return {'id': str(user['id']), 'email': user['email_normalized'], 'role': user['role']}


def check_origin(request: Request) -> None:
    origin = request.headers.get('origin')
    allowed = os.getenv('AUTH_ALLOWED_ORIGINS', 'http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000,http://localhost:8000').split(',')
    if origin is not None and origin not in [x.strip() for x in allowed]:
        raise HTTPException(403, 'Origin rejected')


def current_user(request: Request) -> Principal:
    token = request.cookies.get(COOKIE_NAME, '')
    if not token or len(token) > 128:
        raise HTTPException(401, 'Login required')
    with connect() as connection:
        row = connection.execute('''SELECT u.id, u.email_normalized, u.role, s.id AS session_id
            FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token_hash=%s AND s.revoked_at IS NULL
            AND s.expires_at>now() AND u.disabled_at IS NULL''', (digest(token),)).fetchone()
    if row is None:
        raise HTTPException(401, 'Login required')
    return Principal(row['id'], row['email_normalized'], row['role'], row['session_id'])


def mutation_user(request: Request, user: Principal = Depends(current_user)) -> Principal:
    check_origin(request)
    supplied = request.headers.get('x-csrf-token', '')
    if not hmac.compare_digest(supplied.encode(), csrf_token(request.cookies.get(COOKIE_NAME, '')).encode()):
        raise HTTPException(403, 'CSRF token required')
    return user


def limit_keys(request, email=None, register=False):
    source = request.client.host if request.client else 'unknown'
    if register:
        return [('register_source', digest(source), setting('AUTH_REGISTER_SOURCE_LIMIT', 20))]
    return [('login_account', digest(email), setting('AUTH_ACCOUNT_LIMIT', 5)),
            ('login_source', digest(source), setting('AUTH_SOURCE_LIMIT', 20))]


def prepare_limits(connection, keys):
    # Sorted transaction locks serialize attempts across API processes and avoid lock inversions.
    for scope, subject, _ in sorted(keys):
        lock = int.from_bytes(hashlib.sha256((scope + subject).encode()).digest()[:8], 'big', signed=True)
        connection.execute('SELECT pg_advisory_xact_lock(%s)', (lock,))
        connection.execute('INSERT INTO auth_limits(scope,subject_hash) VALUES (%s,%s) ON CONFLICT DO NOTHING', (scope, subject))
    now = datetime.now(timezone.utc)
    window = timedelta(seconds=setting('AUTH_WINDOW_SECONDS', 900))
    for scope, subject, _ in keys:
        row = connection.execute('SELECT * FROM auth_limits WHERE scope=%s AND subject_hash=%s FOR UPDATE', (scope, subject)).fetchone()
        if row['blocked_until'] and row['blocked_until'] > now:
            return False
        if row['window_started_at'] + window <= now or row['blocked_until']:
            connection.execute('UPDATE auth_limits SET window_started_at=%s,failure_count=0,blocked_until=NULL WHERE scope=%s AND subject_hash=%s', (now, scope, subject))
    return True


def record_limit(connection, keys):
    for scope, subject, limit in keys:
        connection.execute('''UPDATE auth_limits SET failure_count=failure_count+1,
            blocked_until=CASE WHEN failure_count+1>=%s THEN now()+(%s * interval '1 second') ELSE NULL END
            WHERE scope=%s AND subject_hash=%s''', (limit, setting('AUTH_BLOCK_SECONDS', 900), scope, subject))


@router.post('/auth/register', status_code=201)
def register(data: Registration, request: Request):
    check_origin(request)
    keys = limit_keys(request, register=True)
    with connect() as connection:
        if not prepare_limits(connection, keys):
            return JSONResponse({'detail': 'Please try again later'}, status_code=429)
        record_limit(connection, keys)
        hashed = password_hash(data.password)
        row = connection.execute('''INSERT INTO users(email_normalized,password_hash) VALUES (%s,%s)
            ON CONFLICT (email_normalized) DO NOTHING RETURNING id,email_normalized,role''', (data.email, hashed)).fetchone()
        if row is None:
            return JSONResponse({'detail': 'Registration unavailable'}, status_code=409)
    return user_json(row)


@router.post('/auth/login')
def login(data: Credentials, request: Request, response: Response):
    check_origin(request)
    keys = limit_keys(request, data.email)
    with connect() as connection:
        if not prepare_limits(connection, keys):
            return JSONResponse({'detail': 'Please try again later'}, status_code=429)
        row = connection.execute('SELECT * FROM users WHERE email_normalized=%s FOR UPDATE', (data.email,)).fetchone()
        if row is None:
            # Equivalent password work avoids an obvious missing-account fast path.
            verify_password(data.password, _DUMMY_HASH)
        valid = row is not None and verify_password(data.password, row['password_hash'])
        if not valid or row['disabled_at'] is not None:
            record_limit(connection, keys)
            return JSONResponse({'detail': 'Email or password incorrect'}, status_code=401)
        connection.execute('UPDATE auth_limits SET failure_count=0,blocked_until=NULL,window_started_at=now() WHERE scope=%s AND subject_hash=%s', ('login_account', digest(data.email)))
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(seconds=setting('AUTH_SESSION_SECONDS', 28800))
        old = request.cookies.get(COOKIE_NAME)
        if old:
            connection.execute('UPDATE sessions SET revoked_at=now() WHERE token_hash=%s AND revoked_at IS NULL', (digest(old),))
        connection.execute('INSERT INTO sessions(user_id,token_hash,expires_at) VALUES (%s,%s,%s)', (row['id'], digest(token), expires))
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite='strict', secure=os.getenv('AUTH_COOKIE_SECURE', 'false').lower() == 'true', path='/')
    response.headers['Cache-Control'] = 'no-store'
    return {'user': user_json(row), 'csrf_token': csrf_token(token)}


@router.get('/auth/me')
def me(request: Request, response: Response, user: Principal = Depends(current_user)):
    response.headers['Cache-Control'] = 'no-store'
    return {'user': {'id': str(user.id), 'email': user.email, 'role': user.role}, 'csrf_token': csrf_token(request.cookies[COOKIE_NAME])}


@router.post('/auth/logout', status_code=204)
def logout(response: Response, user: Principal = Depends(mutation_user)):
    with connect() as connection:
        connection.execute('UPDATE sessions SET revoked_at=now() WHERE id=%s AND revoked_at IS NULL', (user.session_id,))
    response.delete_cookie(COOKIE_NAME, path='/')


def task_for_owner(connection, task_id: UUID, user: Principal):
    row = connection.execute('SELECT * FROM tasks WHERE id=%s AND user_id=%s AND deletion_requested_at IS NULL AND deleted_at IS NULL FOR UPDATE', (task_id, user.id)).fetchone()
    if row is None:
        raise HTTPException(404, 'Task not found')
    return row


@router.get('/tasks/{task_id}')
def task_metadata(task_id: UUID, user: Principal = Depends(current_user)):
    from app.tasks import get_task
    return get_task(task_id, user.id)


@router.post('/tasks/{task_id}/consents', status_code=201)
def grant_consent(task_id: UUID, data: ConsentInput, user: Principal = Depends(mutation_user)):
    with connect() as connection:
        task_for_owner(connection, task_id, user)
        row = connection.execute('''INSERT INTO task_consents(task_id,user_id,purpose,access_scope)
            VALUES (%s,%s,%s,%s) ON CONFLICT (task_id,purpose,access_scope) WHERE revoked_at IS NULL
            DO UPDATE SET purpose=EXCLUDED.purpose RETURNING id,purpose,access_scope,granted_at''', (task_id, user.id, data.purpose, data.access_scope)).fetchone()
    return row


@router.delete('/tasks/{task_id}/consents/{consent_id}', status_code=204)
def revoke_consent(task_id: UUID, consent_id: UUID, user: Principal = Depends(mutation_user)):
    with connect() as connection:
        task_for_owner(connection, task_id, user)
        row = connection.execute('UPDATE task_consents SET revoked_at=COALESCE(revoked_at,now()) WHERE id=%s AND task_id=%s AND user_id=%s RETURNING id', (consent_id, task_id, user.id)).fetchone()
        if row is None:
            raise HTTPException(404, 'Consent not found')


@router.get('/admin/tasks')
def admin_tasks(user_id: UUID | None = None, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), user: Principal = Depends(current_user)):
    if user.role != 'ADMIN':
        raise HTTPException(403, 'Administrator required')
    with connect() as connection:
        return connection.execute('''SELECT t.id,t.user_id,t.status,t.submitted_at,t.started_at,t.finished_at,
            t.failure_category,t.failure_code,t.retry_count,u.credits_available
            FROM tasks t JOIN users u ON u.id=t.user_id WHERE t.deletion_requested_at IS NULL
            AND (%s::uuid IS NULL OR t.user_id=%s) ORDER BY t.submitted_at,t.user_id,t.id LIMIT %s OFFSET %s''', (user_id, user_id, limit, offset)).fetchall()


def authorize_task_content(user: Principal, task_id: UUID, purpose: str, access_scope: str):
    """Check immediately before content access; future streaming must recheck each continuation."""
    allowed = False
    with connect() as connection:
        active = connection.execute('''SELECT u.role FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.id=%s AND s.user_id=%s AND s.revoked_at IS NULL
            AND s.expires_at>now() AND u.disabled_at IS NULL''', (user.session_id, user.id)).fetchone()
        if active is None:
            raise HTTPException(401, 'Login required')
        task = connection.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE', (task_id,)).fetchone()
        if task is None:
            raise HTTPException(404, 'Task not found')
        exists = not task['deletion_requested_at'] and not task['deleted_at']
        if user.id == task['user_id'] and exists:
            return task
        if active['role'] == 'ADMIN':
            allowed = bool(exists and access_scope in ('video', 'summary') and connection.execute('SELECT id FROM task_consents WHERE task_id=%s AND user_id=%s AND purpose=%s AND access_scope=%s AND revoked_at IS NULL', (task_id, task['user_id'], purpose, access_scope)).fetchone())
            connection.execute('''INSERT INTO audit_events(actor_user_id,target_user_id,task_id,event_type,reason_code,result,retention_class)
                VALUES (%s,%s,%s,'ADMIN_CONTENT_ACCESS',%s,%s,'TASK_30D')''', (user.id, task['user_id'], task_id, purpose[:200], 'ALLOWED' if allowed else 'DENIED'))
    if not allowed:
        raise HTTPException(403, 'Task content access denied')
    return task


_DUMMY_HASH = password_hash('synthetic-missing-account-password')



