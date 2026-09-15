import asyncio, sys, re
sys.path.insert(0, '.')
from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services.rag import extract_submission_channels
from backend.services.postgres import get_pending_applications

from backend.dependencies import get_officer_jurisdiction_ids

async def officer_ctx(db, email):
    o = (await db.execute(select(SISOfficer).where(SISOfficer.email == email))).scalar_one()
    j = await get_officer_jurisdiction_ids(o.id, db)
    return OfficerContext(
        officer_id=o.id, employee_id=o.employee_id, name=o.name, email=o.email,
        designation=o.designation, jurisdiction_type=j["jurisdiction_type"],
        jurisdiction_name=j["jurisdiction_name"],
        jurisdiction_ids=j["district_ids"] + j["taluk_ids"] + j["town_ids"]
                         + j["ward_ids"] + j["block_ids"])

QUESTIONS = [
    "show applications from CSC",
    "show applications from CSC and sub registrar",
    "applications from csc or citizen",
    "show applications from all channels",
    "show me both csc and sro applications",
    "show applications from sub registrar and citizen",
    "applications from csc, sro and citizen",
]

async def main():
    async with AsyncSessionLocal() as db:
        for uname in ("msivakumar@sis.tn.gov.in", "csenthil@sis.tn.gov.in"):
            officer = await officer_ctx(db, uname)
            print(f"\n=== {uname} ===")
            for q in QUESTIONS:
                chans = extract_submission_channels(q) or None
                sd = await get_pending_applications(db, officer, submission_channel=chans)
                rows = sd.get("applications") or []
                split = {}
                for r in rows:
                    split[r.get("submission_channel")] = split.get(r.get("submission_channel"), 0) + 1
                print(f"  {q!r:52} chans={chans} -> {sd.get('count')} {split}")

asyncio.run(main())
