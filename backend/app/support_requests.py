"""Retired CR-001 routes. Historical IDs never grant content access."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from app import auth, tasks
from app.content_auth import session

router=APIRouter()


def retired(user,role=None):
    with tasks.connect() as c:session(c,user,role)
    raise HTTPException(410,'Support authorization retired')


@router.get('/tasks/{task_id}/support-request')
def current_request(task_id:UUID,user=Depends(auth.current_user)):
    retired(user,'USER')


@router.post('/tasks/{task_id}/support-requests')
def create_request(task_id:UUID,user=Depends(auth.mutation_user)):
    retired(user,'USER')


@router.delete('/tasks/{task_id}/support-requests/{request_id}')
def revoke_request(task_id:UUID,request_id:UUID,user=Depends(auth.mutation_user)):
    retired(user,'USER')


@router.get('/admin/support-requests')
def list_requests(user=Depends(auth.current_user)):
    retired(user,'ADMIN')


@router.get('/admin/support-requests/{request_id}')
def resolve_request(request_id:UUID,user=Depends(auth.current_user)):
    retired(user,'ADMIN')


@router.delete('/admin/support-requests/{request_id}')
def dismiss_request(request_id:UUID,user=Depends(auth.mutation_user)):
    retired(user,'ADMIN')


def authorize_content(c,user,task_id=None,request_id=None,scope=None,audit=False):
    """Tombstone for callers of the removed grant helper; no owner fallback."""
    session(c,user)
    raise HTTPException(410,'Support authorization retired')
