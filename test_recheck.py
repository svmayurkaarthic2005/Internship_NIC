"""Pushback -- "you are wrong", "check again", "are you sure", "there should be 40" -- in English,
Tamil and Tanglish, with spelling slips, after different kinds of answer.

The earlier question is asked of the register again and compared with the answer given; a
figure is never changed to please the officer. No LLM.

    python test_recheck.py            # non-streaming
    python test_recheck.py --stream   # the streaming entry point
"""
import asyncio
import json
import logging
import re
import sys
import uuid

logging.disable(logging.CRITICAL)
from sqlalchemy import select, text

from backend.database import AsyncSessionLocal, engine
from backend.models import SISOfficer
from backend.sample_db.build_lora_dataset import build_officer_context, _flatten_html
from backend.services import chatbot, rag, recheck

STREAM = "--stream" in sys.argv
FAILS = []


class Stub:
    temperature = 0.1
    def bind(self, **k): raise RuntimeError("stub")
    def bind_tools(self, *a, **k): raise RuntimeError("stub")
    async def ainvoke(self, *a, **k):
        class R: content = "[[LLM]]"
        return R()
    async def astream(self, *a, **k):
        class R: content = "[[LLM]]"
        yield R()


def check(ok, label, detail=""):
    if not ok:
        FAILS.append(f"{label}: {detail[:200]}")


async def turn(db, ctx, sid, msg, hist):
    if not STREAM:
        r = await chatbot.process_chat(msg, sid, ctx, db, chat_history=hist)
        return (r.get("response") or "") + " " + json.dumps(r.get("table_data") or "", ensure_ascii=False)
    parts = []
    async for chunk in chatbot.process_chat_stream(msg, sid, ctx, db, chat_history=hist):
        for line in chunk.decode("utf-8", "replace").splitlines():
            if line.startswith("data:"):
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    if ev.get("content"):
                        parts.append(str(ev["content"]))
                    if ev.get("table_data"):
                        parts.append(json.dumps(ev["table_data"], ensure_ascii=False))
    return " ".join(parts)


PUSH = ["you are wrong", "u r wrong check again", "you r rong", "ur wrng chek agn", "are you sure", "are you sure?",
        "thats incorrect", "no thats not right", "wrong answer, check again", "check again", "recheck",
        "nee thappu solra", "thirumba paaru", "thappu thirumba check pannu", "sariyilla marubadiyum paaru",
        "நீ சொன்னது தவறு", "நீங்கள் சொன்னது தவறு மீண்டும் சரி பாருங்கள்", "மீண்டும் சரிபார்", "உறுதியா?",
        "i think there are more than that", "no there should be 40"]
NOT_PUSH = ["show approved applications", "how many are wrong", "what is the status of it", "good job", "clear",
            "hello", "that is correct thanks", "ok", "which application is wrong", "show rejected applications again",
            "how many applications are not approved", "sure", "2026/0154/28/001197 status check again"]


async def main():
    rag.llm = Stub()
    for m in PUSH:
        check(recheck.is_pushback(m), f"is_pushback {m!r}")
    for m in NOT_PUSH:
        check(not recheck.is_pushback(m), f"not a pushback {m!r}")
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(SISOfficer).where(SISOfficer.email == "msivakumar@sis.tn.gov.in"))).scalars().first()
        ctx = await build_officer_context(db, row)
        # contexts x pushbacks: the question is repeated, the answer is the same, the wording says so
        for first, key in [("how many applications are not approved", "16"), ("show approved applications", "34 application"),
                           ("status of 2026/0154/28/001197", "pending"), ("how many ISD applications do I have", "22 ISD"),
                           ("how many field visits do I have", "13 field visit"), ("what is ISD", "0154"),
                           ("neither approved nor rejected", "2 application")]:
            for push in ["you are wrong", "ur wrng chek agn", "are you sure?", "thirumba paaru", "நீ சொன்னது தவறு"]:
                sid, hist = str(uuid.uuid4()), []
                a = await turn(db, ctx, sid, first, hist)
                hist += [{"role": "user", "content": first}, {"role": "assistant", "content": a}]
                b = await turn(db, ctx, sid, push, hist)
                t = re.sub(r"\s+", " ", _flatten_html(b))
                ta = bool(re.search(r"[஀-௿]", push)) or push in ("thirumba paaru",)
                label = f"{first!r} then {push!r}"
                check(key in t, label + ": same figure", t[:160])
                check(("சரிபார்த்தேன்" in t) if push == "நீ சொன்னது தவறு" else ("asked the register again" in t or "சரிபார்த்தேன்" in t),
                      label + ": says it checked", t[:120])
                check("same as before" in t or "முன்பு சொன்னதே" in t, label + ": says it is the same", t[:160])
        # no earlier answer / only a greeting
        for first in [None, "hello"]:
            for push in ["check again", "மீண்டும் சரிபார்"]:
                sid, hist = str(uuid.uuid4()), []
                if first:
                    a = await turn(db, ctx, sid, first, hist)
                    hist += [{"role": "user", "content": first}, {"role": "assistant", "content": a}]
                b = re.sub(r"\s+", " ", _flatten_html(await turn(db, ctx, sid, push, hist)))
                check("no earlier answer" in b or "முந்தைய பதில் இந்த உரையாடலில் இல்லை" in b, f"{first!r} then {push!r}: nothing to check", b[:120])
        # a bare complaint with nothing before keeps its own reply
        b = await turn(db, ctx, str(uuid.uuid4()), "you are wrong", [])
        check("which answer was wrong" in b, "bare complaint", b[:100])
        # a figure is never changed to match the officer's expectation
        sid, hist = str(uuid.uuid4()), []
        a = await turn(db, ctx, sid, "show approved applications", hist)
        hist += [{"role": "user", "content": "show approved applications"}, {"role": "assistant", "content": a}]
        b = re.sub(r"\s+", " ", _flatten_html(await turn(db, ctx, sid, "no there should be 40", hist)))
        check("34 application" in b and "not changed the figure to 40" in b and "Found 40" not in b, "asserted figure", b[:200])
        # an answer that really changed is reported as changed
        sid = str(uuid.uuid4())
        hist = [{"role": "user", "content": "how many ISD applications do I have"},
                {"role": "assistant", "content": "There are 15 ISD applications in your jurisdiction."}]
        b = re.sub(r"\s+", " ", _flatten_html(await turn(db, ctx, sid, "you are wrong", hist)))
        check("differs from my earlier one" in b and "22 ISD" in b, "changed answer is reported", b[:200])
        # the transcript keeps what was typed, not the repeated question
        sid = str(uuid.uuid4())
        a = await turn(db, ctx, sid, "show my pending applications", [])
        hist = [{"role": "user", "content": "show my pending applications"}, {"role": "assistant", "content": a}]
        await turn(db, ctx, sid, "u r wrong check again", hist)
        rows = (await db.execute(text("select role, content from chat_messages where session_id = :s order by created_at"),
                                 {"s": sid})).all()
        users = [r[1] for r in rows if r[0] == "user"]
        check(users == ["show my pending applications", "u r wrong check again"], "transcript", str(users))
    await engine.dispose()
    print("FAILED:" if FAILS else "ALL PASSED")
    for f in FAILS:
        print(" ", f)
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
