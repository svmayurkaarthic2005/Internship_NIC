"""Tamil / Tanglish follow-ups must behave like their English twins.

For every follow-up below, the English phrasing over the same listing is the
oracle (English projection handling is the verified behaviour). The Tamil or
Tanglish phrasing must produce the same per-row values, or name the same
applications. Phrasings come from the tamil_synthetic rows of train_augmented.jsonl.

python test_tamil_followups.py        # no LLM (stubbed)
"""
import asyncio
import logging
import re
import sys
import uuid

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("backend").setLevel(logging.WARNING)

from sqlalchemy import select

from backend.database import AsyncSessionLocal, engine
from backend.models import SISOfficer
from backend.sample_db.build_lora_dataset import build_officer_context, _flatten_html
from backend.services import chatbot, rag

APP = re.compile(r"\d{4}/\d{4}/\d{2}/\d{6}")
ROW = re.compile(r"^(\d{4}/\d{4}/\d{2}/\d{6}) \| (.+?)\s*\|?\s*$", re.MULTILINE)
FAILS = []

SETUPS = [("muthulakshmis", "show pending applications"),
          ("csenthil", "applications from CSC"),
          ("msivakumar", "show my ISD applications")]

# (label, english oracle, [tanglish / tamil phrasings])
# Plural / "their" phrasings: the English "their <field>" table is the oracle.
COLUMNS = [
    ("block", "their blocks", ["blocks enna?", "அவற்றின் block-ஐ காட்டு"]),
    ("ward", "their wards", ["ward details sollu", "ward details", "அவற்றின் ward-ஐ காட்டு"]),
    ("mobile", "their mobile numbers", ["அவற்றின் மொபைல் எண்கள்?"]),
    ("applicant", "their applicants", ["பெயர்களை காட்டு"]),
    ("status", "their status", ["status mattum", "அவற்றின் status என்ன?", "அவற்றின் நிலையை காட்டு"]),
    ("stage", "their stage", ["stage mattum"]),
    ("can", "their CAN numbers", ["avatroda can number kaatu", "can numbers sollu"]),
    ("igrs", "their igrs numbers", ["அவற்றின் igrs numbers?"]),
    ("survey", "their survey numbers", ["survey numbers sollu", "அவற்றின் survey number?"]),
    ("channel", "their submission channel", ["அவற்றின் submission channel?"]),
    ("fee", "their fees", ["அவற்றின் கட்டணங்களை காட்டு"]),
]
# A bare singular field name over a listing is answered the same way in every
# language: a table when the list is short, "which one do you mean?" when it is long.
SINGULAR = [
    ("mobile", "mobile number", ["mobile number sollu"]),
    ("applicant", "applicant name", ["applicant name என்ன?"]),
    ("igrs", "igrs number", ["igrs number sollu"]),
    ("channel", "submission channel", ["submission channel என்ன?"]),
    ("fee", "what is the total fee?", ["fee amount evlo?"]),
]
PICKS = [
    ("oldest", "which is oldest?", ["oldest edhu?", "பழையது எது?"]),
    ("newest", "which is newest?", ["newest edhu?", "புதியது எது?"]),
    ("first", "the first one", ["modhal file", "mudhal file", "முதல் விண்ணப்பம்"]),
    ("last", "last file", ["kadaisi file"]),
]


class StubLLM:
    temperature = 0.1

    def bind(self, **k): raise RuntimeError("stub")
    def bind_tools(self, *a, **k): raise RuntimeError("stub")

    async def ainvoke(self, *a, **k):
        class R: content = "[[LLM]]"
        return R()


def check(ok, label, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   {detail[:170]}" if detail and not ok else ""))
    if not ok:
        FAILS.append(label)


def pairs(text):
    return {m.group(1): re.sub(r"\s+", " ", m.group(2)).strip(" |") for m in ROW.finditer(text)}


async def run(db, ctx, setup, follow):
    sid = str(uuid.uuid4())
    await chatbot.process_chat(setup, sid, ctx, db, chat_history=[])
    r = await chatbot.process_chat(follow, sid, ctx, db, chat_history=[])
    return r, _flatten_html(r.get("response") or "")


async def main():
    real = rag.llm
    rag.llm = StubLLM()
    async with AsyncSessionLocal() as db:
        ctxs = {}
        for o, _s in SETUPS:
            row = (await db.execute(select(SISOfficer).where(SISOfficer.email == f"{o}@sis.tn.gov.in"))).scalars().first()
            ctxs[o] = await build_officer_context(db, row)
        for officer, setup in SETUPS:
            ctx = ctxs[officer]
            print(f"\n=== {officer}: {setup!r}")
            for label, oracle_q, variants in COLUMNS:
                _, oracle = await run(db, ctx, setup, oracle_q)
                want = pairs(oracle)
                check(bool(want), f"[{label}] English oracle produces a per-row table", oracle[:100])
                for v in variants:
                    r, t = await run(db, ctx, setup, v)
                    got = pairs(t)
                    check(got == want and bool(got), f"[{label}] {v!r} == English {oracle_q!r} ({len(want)} rows)",
                          f"{r.get('intent')} rows={len(got)}: {t[:110]}")
            for label, oracle_q, variants in SINGULAR:
                ro, oracle = await run(db, ctx, setup, oracle_q)
                for v in variants:
                    r, t = await run(db, ctx, setup, v)
                    same_kind = r.get("intent") == ro.get("intent")
                    money = lambda x: re.findall(r"₹[\d,]+\.\d\d", x)
                    same_rows = pairs(t) == pairs(oracle) and money(t) == money(oracle)
                    check(same_kind and same_rows, f"[{label}] {v!r} behaves like English {oracle_q!r} ({ro.get('intent')})",
                          f"{r.get('intent')} vs {ro.get('intent')}: {t[:100]}")
            for label, oracle_q, variants in PICKS:
                _, oracle = await run(db, ctx, setup, oracle_q)
                want = set(APP.findall(oracle))
                for v in variants:
                    r, t = await run(db, ctx, setup, v)
                    got = set(APP.findall(t))
                    check(bool(want) and got == want, f"[{label}] {v!r} names the same file as English {oracle_q!r}",
                          f"want {sorted(want)} got {sorted(got)} ({r.get('intent')}): {t[:80]}")
    rag.llm = real
    await engine.dispose()
    print("\n" + ("ALL PASSED" if not FAILS else f"FAILED ({len(FAILS)}):\n  - " + "\n  - ".join(FAILS)))
    return 0 if not FAILS else 1


raise SystemExit(asyncio.run(main()))
