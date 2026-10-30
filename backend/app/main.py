"""HTTP entry point and identity routes."""
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import psycopg

from app.auth import router
from app.ingest import router as input_router, lifespan, input_error_handler
from app.media import InputError
from app.task_routes import router as task_router
from app.database import database_ready
from app.storage import object_store_ready

app = FastAPI(title='Video Summary App', docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(router)
app.include_router(input_router)
app.include_router(task_router)
app.add_exception_handler(InputError, input_error_handler)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, _error: RequestValidationError):
    # Pydantic errors can include the original password input; never serialize them.
    return JSONResponse({'detail': 'Invalid request'}, status_code=422)


@app.exception_handler(psycopg.Error)
async def database_error(_request: Request, _error: psycopg.Error):
    return JSONResponse({'detail': 'Service temporarily unavailable'}, status_code=503)


@app.middleware('http')
async def private_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(('/auth/', '/tasks', '/quota', '/admin/', '/inputs/')):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/health/live')
def live() -> dict[str, str]:
    return {'status': 'alive'}


@app.get('/health/ready')
def ready() -> dict[str, str]:
    if not database_ready() or not object_store_ready():
        raise HTTPException(status_code=503, detail='dependencies unavailable')
    return {'status': 'ready'}
