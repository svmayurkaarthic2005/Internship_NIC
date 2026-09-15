"""Shared harness: build OfficerContext(s) and run questions through process_chat."""
import asyncio, sys, os, uuid, json, time, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services.auth_service import get_officer_jurisdiction_ids

# Quiet the engine echo=True that backend.database turns on in development.
import logging
AsyncSessionLocal.kw['bind'].echo = False
for _n in ('sqlalchemy.engine', 'sqlalchemy.engine.Engine', 'sqlalchemy.pool',
           'sqlalchemy.orm', 'httpx', 'httpcore', 'asyncio'):
    logging.getLogger(_n).setLevel(logging.WARNING)
logging.getLogger().setLevel(logging.WARNING)
try:
    import structlog
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))
except Exception:
    pass


async def officers(db, limit=None):
    res = await db.execute(select(SISOfficer).where(SISOfficer.is_active.is_(True))
                           .order_by(SISOfficer.employee_id))
    out = []
    for m in res.scalars().all():
        j = await get_officer_jurisdiction_ids(m.id, db)
        allids = j['district_ids'] + j['taluk_ids'] + j['town_ids'] + j['ward_ids'] + j['block_ids']
        out.append(OfficerContext(
            officer_id=m.id, employee_id=m.employee_id, name=m.name,
            name_tamil=m.name_tamil, email=m.email, designation=m.designation,
            jurisdiction_type=j.get('jurisdiction_type', 'ward'),
            jurisdiction_name=j.get('jurisdiction_name', ''),
            jurisdiction_ids=allids, district_ids=j['district_ids'],
            taluk_ids=j['taluk_ids'], town_ids=j['town_ids'],
            ward_ids=j['ward_ids'], block_ids=j['block_ids'],
            officer_stage='SIS', is_active=m.is_active))
    return out[:limit] if limit else out


async def ask(message, officer, db, history=None, session_id=None):
    from backend.services.chatbot import process_chat
    t0 = time.time()
    try:
        r = await process_chat(message, session_id or str(uuid.uuid4()), officer, db,
                               chat_history=history or [])
        r = dict(r)
    except Exception as e:
        r = {'response': '', 'error': f'{type(e).__name__}: {e}',
             'traceback': traceback.format_exc()}
    r['_ms'] = int((time.time() - t0) * 1000)
    return r
