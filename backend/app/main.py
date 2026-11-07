"""HTTP entry point and identity routes."""
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import psycopg

from app.public_config import router as public_config_router
from app.auth import router
from app.support_requests import router as support_router
from app.ingest import router as input_router, lifespan, input_error_handler
from app.media import InputError
from app.task_routes import router as task_router
from app.results import router as result_router
from app.cleanup_routes import router as cleanup_router
from app.database import database_ready
from app.storage import object_store_ready
from app.deletion_control import JournalError, identity_ready

app = FastAPI(title='Video Summary App', docs_url=None, redoc_url=None, lifespan=lifespan)
from app.maintenance import MaintenanceMiddleware, ready as maintenance_ready
app.add_middleware(MaintenanceMiddleware)

app.include_router(router)
app.include_router(public_config_router)
app.include_router(support_router)
app.include_router(input_router)
app.include_router(task_router)
app.include_router(result_router)
app.include_router(cleanup_router)
app.add_exception_handler(InputError, input_error_handler)


def _is_support(request):
    return '/support-request' in request.url.path


def _error_response(request,status,code,message,headers=None):
    body={'error':{'code':code,'message':message}} if _is_support(request) else {'detail':message}
    return JSONResponse(body,status_code=status,headers=headers)


@app.exception_handler(HTTPException)
async def http_error(request,exc):
    if not _is_support(request):
        return JSONResponse({'detail':exc.detail},status_code=exc.status_code,headers=exc.headers)
    detail=exc.detail if isinstance(exc.detail,dict) else {}
    return _error_response(request,exc.status_code,detail.get('code',{401:'LOGIN_REQUIRED',403:'ACCESS_DENIED',404:'REQUEST_UNAVAILABLE',409:'STATE_CONFLICT',422:'INVALID_REQUEST',429:'RATE_LIMITED',503:'DEPENDENCY_UNAVAILABLE'}.get(exc.status_code,'REQUEST_FAILED')),
                           detail.get('message','Request unavailable'),exc.headers)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, _error: RequestValidationError):
    # Pydantic errors can include the original password input; never serialize them.
    return _error_response(_request,422,'INVALID_REQUEST','Invalid request')


@app.exception_handler(JournalError)
async def deletion_control_error(_request: Request, _error: JournalError):
    return _error_response(_request,503,'CONTROL_UNAVAILABLE','Service temporarily unavailable')


@app.exception_handler(psycopg.Error)
async def database_error(_request: Request, _error: psycopg.Error):
    return _error_response(_request,503,'DEPENDENCY_UNAVAILABLE','Service temporarily unavailable')


@app.middleware('http')
async def private_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(('/auth/', '/tasks', '/quota', '/admin/', '/inputs/', '/deletions')):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/health/live')
def live() -> dict[str, str]:
    return {'status': 'alive'}


@app.get('/health/ready')
def ready() -> dict[str, str]:
    if not maintenance_ready() or not database_ready() or not object_store_ready() or not identity_ready():
        raise HTTPException(status_code=503, detail='dependencies unavailable')
    return {'status': 'ready'}
