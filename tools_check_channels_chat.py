"""End-to-end: run real chat turns for channel combinations, print the answer."""
import asyncio, sys, re, html
sys.path.insert(0, '.')
from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.dependencies import get_officer_jurisdiction_ids
from backend.services import chatbot

async def officer_ctx(db, email):
    o = (await db.execute(select(SISOfficer).where(SISOfficer.email == email))).scalar_one()
    j = await get_officer_jurisdiction_ids(o.id, db)
    return OfficerContext(
        officer_id=o.id, employee_id=o.employee_id, name=o.name, email=o.email,
        designation=o.designation, jurisdiction_type=j["jurisdiction_type"],
        jurisdiction_name=j["jurisdiction_name"],
        jurisdiction_ids=j["district_ids"] + j["taluk_ids"] + j["town_ids"]
                         + j["ward_ids"] + j["block_ids"])

def summarise(text):
    intro = re.search(r"<div class='table-intro'>(.*?)</div>", text, re.S)
    head = re.findall(r"<th>(.*?)</th>", text)
    nrows = text.count("<tr>") - (1 if head else 0)
    out = []
    if intro:
        out.append("INTRO: " + html.unescape(re.sub(r"<[^>]+>", "", intro.group(1))).strip())
    if head:
        out.append("COLS : " + " | ".join(html.unescape(h) for h in head))
        out.append(f"ROWS : {nrows}")
    if not out:
        out.append((text or "")[:400].replace("\n", " "))
    return "\n      ".join(out)

QUESTIONS = [
    "applications from csc or citizen",
    "show applications from citizen portal",
    "show applications from CSC and sub registrar",
    "show me both csc and sro applications",
    "applications from csc or citizen",
    "show applications from all channels",
    "show applications from CSC",
    "how many applications from each channel",
    "show my applications with their submission channel",
]

async def main():
    async with AsyncSessionLocal() as db:
        officer = await officer_ctx(db, "muthulakshmis@sis.tn.gov.in")
        import uuid
        for q in QUESTIONS:
            sid = str(uuid.uuid4())
            res = await chatbot.process_chat(q, sid, officer, db, [])
            text = res.get("response") or res.get("message") or str(res)
            print(f"\n--- {q!r}\n      intent={res.get('intent')}\n      {summarise(text)}")

asyncio.run(main())
