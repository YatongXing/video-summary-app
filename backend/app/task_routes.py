"""Authenticated owner task controls; execution transitions are internal only."""
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from app.auth import Principal, current_user, mutation_user
from app import tasks
router=APIRouter()
class RetryInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_key: UUID
@router.get('/quota')
def quota(user:Principal=Depends(current_user)):
    return tasks.quota(user.id)
@router.get('/tasks')
def list_tasks(limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0),user:Principal=Depends(current_user)):
    return tasks.list_tasks(user.id,limit,offset)
@router.post('/tasks/{task_id}/cancel')
def cancel(task_id:UUID,user:Principal=Depends(mutation_user)):
    return tasks.cancel_task(task_id,user.id)
@router.post('/tasks/{task_id}/retry')
def retry(task_id:UUID,data:RetryInput,user:Principal=Depends(mutation_user)):
    return tasks.request_retry(task_id,user.id,data.request_key)
