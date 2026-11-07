"""Authenticated deletion receipts and administrator cleanup operations."""
from uuid import UUID
from fastapi import APIRouter,Depends,HTTPException,Query
from app.auth import Principal,current_user,mutation_user
from app import cleanup
router=APIRouter()
@router.delete('/tasks/{task_id}',status_code=202)
def delete_task(task_id:UUID,user:Principal=Depends(mutation_user)):
    receipt=cleanup.request_delete(task_id,user.id)
    return {'id':receipt['id'],'task_id':receipt['task_id'],'accepted':True}
@router.get('/deletions')
def deletions(limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0),user:Principal=Depends(current_user)):
    raise HTTPException(410,'Deletion history retired')
@router.get('/admin/cleanup-jobs')
def admin_jobs(limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0),user:Principal=Depends(current_user)):
    if user.role!='ADMIN':raise HTTPException(403,'Administrator required')
    raise HTTPException(410,'Cleanup management retired')
@router.post('/admin/cleanup-jobs/{job_id}/retry',status_code=202)
def retry(job_id:UUID,user:Principal=Depends(mutation_user)):
    if user.role!='ADMIN':raise HTTPException(403,'Administrator required')
    raise HTTPException(410,'Cleanup management retired')
