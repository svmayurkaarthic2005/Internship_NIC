import asyncio, sys, uuid, re, logging, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"/mnt/c/proj/nic_internship")
logging.disable(logging.INFO)
from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.sample_db.check_app_wiring import officer_context

email, mode = sys.argv[1], sys.argv[2]
QS = sys.argv[3:]

def strip(h):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(h))).strip()

async def main():
    from backend.services.chatbot import process_chat, process_chat_stream
    async with AsyncSessionLocal() as db:
        off = (await db.execute(select(SISOfficer).where(SISOfficer.email == email))).scalars().first()
        ctx = await officer_context(db, off)
        sess = str(uuid.uuid4())
        print("officer:", off.email, "| session-shared turns, mode:", mode)
        for q in QS:
            print("\n" + "="*70); print("Q:", q)
            if mode == "stream":
                out = []
                async for ch in process_chat_stream(q, sess, ctx, db):
                    out.append(ch.decode("utf-8","replace") if isinstance(ch, bytes) else str(ch))
                print("A:", strip("".join(out))[:900])
            else:
                r = await process_chat(q, sess, ctx, db)
                print("A:", strip(r.get("response") or r.get("answer"))[:900])

asyncio.run(main())
