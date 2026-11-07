"""The only deliberately public deployment setting is the feedback address."""
import os
import re
from fastapi import APIRouter,Response
router=APIRouter()


@router.get('/config/public')
def public_config(response:Response):
    value=os.getenv('SUPPORT_EMAIL','').strip()
    # Restrict to plain email syntax safe for a mailto URL, never headers/query.
    valid=(len(value)<=254 and bool(re.fullmatch(r"[A-Za-z0-9._+-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}",value)))
    response.headers['Cache-Control']='no-store'
    return {'support_email':value if valid else None}
