"""Validated private bilingual results; public JSON is an exact, separate contract."""
from app import maintenance
from copy import deepcopy
import psycopg
from app.deletion_control import JournalError
import json
import math
import os
import re
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from psycopg.types.json import Jsonb
from app import tasks, object_store
from app.auth import Principal, current_user
from app.queue import assert_fence

router = APIRouter()

class OutputError(ValueError):
    def __init__(self):
        super().__init__('Model output does not satisfy the result contract')

def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected): raise OutputError()

def _text(value):
    if not isinstance(value,str) or not value.strip() or '\x00' in value: raise OutputError()
    return value

def _number(value, maximum):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=maximum: raise OutputError()
    return value

def _duplicates(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise OutputError()
        result[key]=value
    return result

def _parse(value):
    if not isinstance(value,str): return deepcopy(value)
    def parse(raw):
        return json.loads(raw,object_pairs_hook=_duplicates,parse_constant=lambda _: (_ for _ in ()).throw(OutputError()))
    try: return parse(value)
    except (ValueError,TypeError,RecursionError): pass
    # One optional local repair: unwrap a whole fenced JSON document. Never
    # discard fields, reorder events, infer missing text or call a model again.
    attempts=int(os.getenv('OUTPUT_REPAIR_ATTEMPTS','1'))
    if attempts not in (0,1): raise OutputError()
    if attempts:
        match=re.fullmatch(r'\s*```(?:json)?[ \t]*\r?\n(.*?)\r?\n```\s*',value,re.DOTALL)
        if match:
            try: return parse(match.group(1))
            except (ValueError,TypeError,RecursionError): pass
    raise OutputError()

def validate_export(document):
    _keys(document,('schema_version','video','language','summary','key_events'))
    if document['schema_version']!='1.0' or document['language'] not in ('zh','en'): raise OutputError()
    video=document['video']; _keys(video,('filename','duration_seconds'))
    name=_text(video['filename'])
    if name in ('.','..') or any(ch in name for ch in ('/','\\',':','?','\r','\n')): raise OutputError()
    duration=_number(video['duration_seconds'],7200)
    if duration<=0: raise OutputError()
    _text(document['summary'])
    events=document['key_events']
    if not isinstance(events,list): raise OutputError()
    previous=-1
    for event in events:
        _keys(event,('timestamp_seconds','title','description'))
        stamp=_number(event['timestamp_seconds'],duration)
        if stamp<previous: raise OutputError()
        previous=stamp; _text(event['title']); _text(event['description'])
    return document

def validate_output(candidate,filename,duration,language):
    candidate=_parse(candidate)
    _keys(candidate,('language','summary','key_events','overview','points','chapters','limitations'))
    if candidate['language']!=language or language not in ('zh','en'): raise OutputError()
    document=validate_export({'schema_version':'1.0','video':{'filename':filename,'duration_seconds':float(duration)},'language':language,'summary':candidate['summary'],'key_events':candidate['key_events']})
    _text(candidate['overview'])
    points=candidate['points']
    if not isinstance(points,list) or not 3<=len(points)<=7: raise OutputError()
    for item in points: _text(item)
    limitations=candidate['limitations']
    if not isinstance(limitations,list): raise OutputError()
    for item in limitations: _text(item)
    if len(document['key_events'])<3 and not limitations: raise OutputError()
    chapters=candidate['chapters']
    if not isinstance(chapters,list) or not chapters: raise OutputError()
    previous=-1
    for chapter in chapters:
        _keys(chapter,('timestamp_seconds','title'))
        stamp=_number(chapter['timestamp_seconds'],float(duration))
        if stamp<previous: raise OutputError()
        previous=stamp; _text(chapter['title'])
    return {'export':document,'presentation':{key:candidate[key] for key in ('overview','points','chapters','limitations')}}

def publish(task_id,generation,owner,candidates,synthetic=False):
    """The validated bodies, asset ledger and successful state commit together."""
    maintenance.ensure_open()
    if not isinstance(candidates,dict) or set(candidates)!= {'zh','en'}: raise OutputError()
    with tasks.connect() as connection:
        user,task=tasks._locks(connection,task_id)
        assert_fence(task,user,generation,owner,tasks.utcnow())
        results={language:validate_output(candidate,task['display_filename'],task['duration_seconds'],language) for language,candidate in candidates.items()}
        for language,result in results.items():
            identifier=uuid4()
            connection.execute('''INSERT INTO task_results(id,task_id,user_id,language,lease_generation,export_document,presentation,synthetic)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',(identifier,task_id,user['id'],language,generation,Jsonb(result['export']),Jsonb(result['presentation']),bool(synthetic)))
            for kind,suffix in (('RESULT_JSON','export'),('RESULT_DOCUMENT','presentation')):
                body=result['export'] if suffix=='export' else result['presentation']
                connection.execute('''INSERT INTO media_assets(task_id,user_id,asset_kind,storage_key,byte_size)
                    VALUES(%s,%s,%s,%s,%s)''',(task_id,user['id'],kind,f'db-result/{identifier}/{suffix}',len(json.dumps(body,ensure_ascii=False,allow_nan=False).encode())))
        now=tasks.utcnow(); assert_fence(task,user,generation,owner,now)
        maintenance.ensure_open()
        result=tasks._finish_locked(connection,user,task,now)
        maintenance.ensure_open()
        return result

def _active_owner(connection,task_id,user):
    from app.content_auth import authorize_owner
    return authorize_owner(connection,user,task_id)


def _active_content(connection,task_id,user,purpose=None,scope='summary',request_id=None,audit=False):
    from app.content_auth import authorize_owner
    if purpose is not None or request_id is not None:
        raise HTTPException(410, 'Support authorization retired')
    return authorize_owner(connection,user,task_id)


def get_result(task_id,user,language,purpose=None,*,request_id=None):
    with tasks.connect() as connection:
        task=_active_content(connection,task_id,user,purpose,'summary',request_id=request_id)
        if task['status']!='SUCCEEDED': raise HTTPException(409,'Result is not ready')
        row=connection.execute('SELECT * FROM task_results WHERE task_id=%s AND user_id=%s AND language=%s',(task_id,task['user_id'],language)).fetchone()
        if row is None: raise HTTPException(409,'Result is not ready')
        try:
            validate_export(row['export_document'])
            if row['export_document']['language']!=language: raise OutputError()
            candidate=dict(row['presentation'],language=language,summary=row['export_document']['summary'],key_events=row['export_document']['key_events'])
            validated=validate_output(candidate,task['display_filename'],task['duration_seconds'],language)
            if validated['export']!=row['export_document']: raise OutputError()
        except (OutputError,TypeError,KeyError,ValueError): raise HTTPException(409,'Stored result failed validation') from None
        _active_content(connection,task_id,user,purpose,'summary',request_id=request_id)
        return dict(validated,language=language,offline_test=row['synthetic'])

@router.get('/tasks/{task_id}/result')
def result_route(task_id:UUID,language:str=Query('zh',pattern='^(zh|en)$'),user:Principal=Depends(current_user)):
    return get_result(task_id,user,language)

@router.get('/tasks/{task_id}/export')
def export_route(task_id:UUID,language:str=Query('zh',pattern='^(zh|en)$'),user:Principal=Depends(current_user)):
    result=get_result(task_id,user,language)
    return Response(json.dumps(result['export'],ensure_ascii=False,allow_nan=False),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="video-summary-{language}.json"','X-Content-Type-Options':'nosniff'})

def _video_response(task_id:UUID,request:Request,user:Principal=Depends(current_user),*,purpose=None,request_id=None):
    with tasks.connect() as connection:
        task=_active_content(connection,task_id,user,purpose,'video',request_id=request_id)
        asset=connection.execute("SELECT storage_key,byte_size FROM media_assets WHERE task_id=%s AND user_id=%s AND asset_kind='ORIGINAL' AND cleanup_state='REGISTERED' ORDER BY created_at,id LIMIT 1",(task_id,task['user_id'])).fetchone()
        if asset is None: raise HTTPException(404,'Video not found')
    client=object_store.client()
    try:
        metadata=client.head_object(Bucket=object_store.bucket(),Key=asset['storage_key'])
        length=metadata['ContentLength']
    except Exception: raise HTTPException(503,'Video temporarily unavailable') from None
    with tasks.connect() as connection:
        _active_content(connection,task_id,user,purpose,'video',request_id=request_id)
    start,end=0,length-1; partial=False
    header=request.headers.get('range')
    if header:
        match=re.fullmatch(r'bytes=(\d*)-(\d*)',header)
        if not match or not any(match.groups()) or any(len(part)>20 for part in match.groups()): raise HTTPException(416,'Invalid range',headers={'Content-Range':f'bytes */{length}'})
        first,last=match.groups()
        if first: start=int(first); end=min(int(last),length-1) if last else length-1
        else: start=max(0,length-int(last))
        if start>=length or start>end: raise HTTPException(416,'Invalid range',headers={'Content-Range':f'bytes */{length}'})
        partial=True
    try: stream=client.get_object(Bucket=object_store.bucket(),Key=asset['storage_key'],Range=f'bytes={start}-{end}')['Body']
    except Exception: raise HTTPException(503,'Video temporarily unavailable') from None
    with tasks.connect() as connection:
        try:
            _active_content(connection,task_id,user,purpose,'video',request_id=request_id)
        except BaseException:
            stream.close()
            raise
    def content():
        try:
            remaining=end-start+1
            while remaining:
                with tasks.connect() as connection: _active_content(connection,task_id,user,purpose,'video',request_id=request_id)
                chunk=stream.read(min(256*1024,remaining))
                if not chunk: return
                # A revoke/delete during an object read must stop that continuation.
                with tasks.connect() as connection: _active_content(connection,task_id,user,purpose,'video',request_id=request_id)
                remaining-=len(chunk); yield chunk
        except (HTTPException, psycopg.Error, JournalError):
            return
        finally: stream.close()
    headers={'Accept-Ranges':'bytes','Content-Length':str(end-start+1),'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'}
    if partial: headers['Content-Range']=f'bytes {start}-{end}/{length}'
    return StreamingResponse(content(),status_code=206 if partial else 200,media_type='video/quicktime' if (task['display_filename'] or '').lower().endswith('.mov') else 'video/mp4',headers=headers)


@router.get('/tasks/{task_id}/video')
def video_route(task_id:UUID,request:Request,user:Principal=Depends(current_user)):
    return _video_response(task_id,request,user)


def retired_admin(user):
    from app.content_auth import session
    with tasks.connect() as c:
        session(c,user,'ADMIN')
    raise HTTPException(410,'Purpose authorization retired')


@router.get('/admin/tasks/{task_id}/result')
def admin_result_route(task_id:UUID,user:Principal=Depends(current_user)):
    retired_admin(user)


@router.get('/admin/tasks/{task_id}/export')
def admin_export_route(task_id:UUID,user:Principal=Depends(current_user)):
    retired_admin(user)


@router.get('/admin/tasks/{task_id}/video')
def admin_video_route(task_id:UUID,user:Principal=Depends(current_user)):
    retired_admin(user)


def request_context(request,user,request_id,scope):
    retired_admin(user)


@router.get('/admin/support-requests/{request_id}/result')
def support_result_route(request_id:UUID,user:Principal=Depends(current_user)):
    retired_admin(user)


@router.get('/admin/support-requests/{request_id}/export')
def support_export_route(request_id:UUID,user:Principal=Depends(current_user)):
    retired_admin(user)


@router.get('/admin/support-requests/{request_id}/video')
def support_video_route(request_id:UUID,user:Principal=Depends(current_user)):
    retired_admin(user)
