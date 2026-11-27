"""Public feedback contact and processing model choices."""
import os
import re
from fastapi import APIRouter,Response
from app.model_selection import QWEN_MODEL, DEEPSEEK_MODEL, deepseek_enabled
router=APIRouter()


@router.get('/config/public')
def public_config(response:Response):
    value=os.getenv('SUPPORT_EMAIL','').strip()
    # Restrict to plain email syntax safe for a mailto URL, never headers/query.
    valid=(len(value)<=254 and bool(re.fullmatch(r"[A-Za-z0-9._+-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}",value)))
    response.headers['Cache-Control']='no-store'
    models=[QWEN_MODEL,DEEPSEEK_MODEL] if deepseek_enabled() else [QWEN_MODEL]
    return {'support_email':value if valid else None,'default_model':QWEN_MODEL,'available_models':models}
