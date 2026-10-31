"""Authenticated deletion receipts and administrator cleanup operations."""
from uuid import UUID
from fastapi import APIRouter,Depends,HTTPException,Query
from app.auth import Principal,current_user,mutation_user
from app import cleanup
router=APIRouter()
@router.delete('/tasks/{task_id}',status_code=202)
def delete_task(task_id:UUID,user:Principal=Depends(mutation_user)):
    return cleanup.request_delete(task_id,user.id)
@router.get('/deletions')
def deletions(limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0),user:Principal=Depends(current_user)):
    return cleanup.list_jobs(user.id,limit,offset)
@router.get('/admin/cleanup-jobs')
def admin_jobs(limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0),user:Principal=Depends(current_user)):
    if user.role!='ADMIN':raise HTTPException(403,'Administrator required')
    return cleanup.list_jobs(None,limit,offset)
@router.post('/admin/cleanup-jobs/{job_id}/retry',status_code=202)
def retry(job_id:UUID,user:Principal=Depends(mutation_user)):
    if user.role!='ADMIN':raise HTTPException(403,'Administrator required')
    return cleanup.retry_job(job_id,user.id)
