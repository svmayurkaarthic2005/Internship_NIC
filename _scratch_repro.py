import asyncio, sys, uuid, re, logging
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"/mnt/c/proj/nic_internship")
logging.disable(logging.INFO)
from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.sample_db.check_app_wiring import officer_context

email = sys.argv[1]
QS = sys.argv[2:]

def strip(h):
    h = re.sub(r"<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", h).strip()

async def main():
    from backend.services.chatbot import process_chat
    async with AsyncSessionLocal() as db:
        off = (await db.execute(select(SISOfficer).where(SISOfficer.email == email))).scalars().first()
        ctx = await officer_context(db, off)
        print("officer:", off.email, "|", ctx.jurisdiction_name)
        for q in QS:
            sess = str(uuid.uuid4())
            r = await process_chat(q, sess, ctx, db)
            print("\n" + "="*70)
            print("Q:", q, "  [intent:", r.get("intent"), "]")
            print("A:", strip(str(r.get("response") or r.get("answer")))[:1200])

asyncio.run(main())
