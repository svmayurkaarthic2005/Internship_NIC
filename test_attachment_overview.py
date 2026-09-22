"""Overview questions about an uploaded file ("what it contains tell short"), also
misspelled, in English / Tanglish / Tamil, must answer from the file's own sections
-- never the "could not find this" refusal. Specific questions still go to retrieval.

python test_attachment_overview.py      # no LLM (stubbed)
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("backend").setLevel(logging.WARNING)

from sqlalchemy import select

from backend.database import AsyncSessionLocal, engine
from backend.models import ChatSession, SISOfficer
from backend.services import attachment_store, chatbot, doc_extract, rag
from backend.services.attachment_qa import is_overview_request
from backend.sample_db.build_lora_dataset import build_officer_context, _flatten_html

FAILS = []
OVERVIEW = [
    "what it contains tell short", "what it contians tell short", "wat it contains", "summary", "sumary of file",
    "summarize this file", "summarise the document", "what is in this file", "whats in it", "what is inside this document",
    "give me an overview", "overveiw please", "tell short", "in short", "contents", "what does this file have",
    "ithula enna irukku", "idhula enna irukku", "summary sollu", "file la enna irukku",
    "இதில் என்ன இருக்கு", "சுருக்கமாக சொல்லுங்கள்", "இந்த ஆவணத்தில் என்ன உள்ளது", "உள்ளடக்கம் என்ன",
]
SPECIFIC = ["what is the litigation rule", "which section talks about SRO", "how many words are there"]
NOT_OVERVIEW = ["what is the fee for ISD", "show pending applications", "how many rows"]


class StubLLM:
    temperature = 0.1

    def bind(self, **k): raise RuntimeError("stub")
    def bind_tools(self, *a, **k): raise RuntimeError("stub")

    async def ainvoke(self, *a, **k):
        class R: content = "[[LLM]]"
        return R()


def check(ok, label, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   {detail[:150]}" if detail and not ok else ""))
    if not ok:
        FAILS.append(label)


async def main():
    raw = Path("backend/documents/land_rules.txt").read_bytes()
    real = rag.llm
    rag.llm = StubLLM()
    print("1. detector")
    for m in OVERVIEW:
        check(is_overview_request(m), f"overview: {m!r}")
    for m in NOT_OVERVIEW:
        check(not is_overview_request(m), f"not an overview: {m!r}")
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(SISOfficer).where(SISOfficer.email == "csenthil@sis.tn.gov.in"))).scalars().first()
        ctx = await build_officer_context(db, row)
        sess = ChatSession(officer_id=ctx.officer_id, session_token="test-overview-" + __import__("uuid").uuid4().hex[:12])
        db.add(sess)
        await db.commit()
        await db.refresh(sess)
        sid = str(sess.id)
        ext = doc_extract.normalise_extension("land_rules.txt")
        doc = await attachment_store.save_document(
            db=db, officer_id=ctx.officer_id, session_id=sid, filename="land_rules.txt", ext=ext,
            mime_type="text/plain", raw=raw, extracted=doc_extract.extract(ext, raw))
        print("\n2. end to end, after an upload")
        for m in OVERVIEW[:6] + OVERVIEW[16:20] + OVERVIEW[20:23]:
            r = await chatbot.process_chat(m, sid, ctx, db, chat_history=[])
            t = _flatten_html(r.get("response") or "")
            check("could not find" not in t and "land_rules.txt" in t and "sections" in t.lower() + " பிரிவுகள்".lower()
                  or "பிரிவுகள்" in t, f"{m!r} answers with the file's sections", f"{r.get('intent')}: {t[:110]}")
        r = await chatbot.process_chat("what it contains tell short", sid, ctx, db, chat_history=[])
        t = _flatten_html(r.get("response") or "")
        check("LITIGATION" in t.upper(), "the outline names real sections of the file", t[:150])
        for m in SPECIFIC:
            r = await chatbot.process_chat(m, sid, ctx, db, chat_history=[])
            t = _flatten_html(r.get("response") or "")
            check("has " not in t[:60] or "sections" not in t, f"{m!r} is not answered with the outline", t[:100])
        print("\n3. with a file attached, chat about anything else is not claimed by the file")
        for m, bad in [("clear", "could not find"), ("hello", "could not find"), ("help", "could not find"),
                       ("who are you", "could not find"), ("asdfgh", "could not find"),
                       ("show pending applications", "could not find"), ("what is ISD", "could not find"),
                       ("how many ISD applications do I have", "could not find")]:
            r = await chatbot.process_chat(m, sid, ctx, db, chat_history=[])
            t2 = _flatten_html(r.get("response") or "")
            check(bad not in t2 and r.get("intent") != "attachment_answer", f"{m!r} -> {r.get('intent')}", t2[:100])
        sid2 = str(sess.id)
        await chatbot.process_chat("show pending applications", sid2, ctx, db, chat_history=[])
        r = await chatbot.process_chat("what do u think abt it", sid2, ctx, db, chat_history=[])
        t2 = _flatten_html(r.get("response") or "")
        check("could not find" not in t2, "an opinion question after a register answer is not sent to the file", t2[:100])
        print("\n4. a conversation about the file stays about the file")
        await chatbot.process_chat("what it contains tell short", sid2, ctx, db, chat_history=[])
        r = await chatbot.process_chat("which section talks about the SRO", sid2, ctx, db, chat_history=[])
        check(r.get("intent") not in ("pending_applications",), "follow-up after a file answer is answered from the file",
              f"{r.get('intent')}: {_flatten_html(r.get('response') or '')[:80]}")
        await attachment_store.delete_document(db, ctx.officer_id, sid, doc.id)
        await db.delete(sess)
        await db.commit()
    rag.llm = real
    await engine.dispose()
    print("\n" + ("ALL PASSED" if not FAILS else f"FAILED ({len(FAILS)}):\n  - " + "\n  - ".join(FAILS)))
    return 0 if not FAILS else 1


raise SystemExit(asyncio.run(main()))
