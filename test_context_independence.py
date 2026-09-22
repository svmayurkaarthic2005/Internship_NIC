"""A fresh question must get the same answer whether or not a list is on screen.

Every question is asked in a clean session (the oracle) and again straight after
each of three contexts: a two-row listing, a thirty-row listing, and one
application's details. A question that names its own subject ("all applications",
"show ISD applications", "what is the fee for ISD") must not be read as a
continuation. Any difference is a misread follow-up.

python test_context_independence.py                 # every 2nd question
python test_context_independence.py --all           # every question
python test_context_independence.py --limit 60      # quick check
Writes context_independence_mismatches.json. No LLM (stubbed).
"""
import asyncio
import json
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
from backend.sample_db.question_bank import build_bank
from backend.services import chatbot, rag

OFFICER = "csenthil@sis.tn.gov.in"
CONTEXTS = {
    "list of 2": "show pending applications",
    "list of 30": "applications from CSC",
    "one application": "details of 2026/0154/28/001167",
    "a definition": "what is ISD",
    "field visits": "show my field visits",
    "a workflow answer": "explain isd workflow",
}
SENTINEL = "[[LLM]]"


class StubLLM:
    temperature = 0.1

    def bind(self, **k): raise RuntimeError("stub")
    def bind_tools(self, *a, **k): raise RuntimeError("stub")

    async def ainvoke(self, *a, **k):
        class R: content = SENTINEL
        return R()


def load_questions():
    qs = [q.text for q in build_bank()]
    for f in ("test_questions_200.txt", "test_questions_206.txt", "test_questions_openers.txt"):
        try:
            for line in open(f, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#") or line.upper().startswith("CATEGORY"):
                    continue
                qs.append(re.sub(r"^\d+\.\s*", "", line))
        except FileNotFoundError:
            pass
    seen, out = set(), []
    for q in qs:
        k = q.lower().strip()
        if k and k not in seen and "||" not in q:
            seen.add(k)
            out.append(q)
    return out


def norm(t):
    return re.sub(r"\s+", " ", t or "").strip()


async def ask(db, ctx, setup, q):
    sid = str(uuid.uuid4())
    if setup:
        await chatbot.process_chat(setup, sid, ctx, db, chat_history=[])
    r = await chatbot.process_chat(q, sid, ctx, db, chat_history=[])
    return r.get("intent"), norm(_flatten_html(r.get("response") or ""))


async def main():
    every = 1 if "--all" in sys.argv else 2
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
    qs = [q for q in load_questions() if not q.startswith("[")][::every]
    if "--openers" in sys.argv:
        qs = [q.strip() for q in open("test_questions_openers.txt", encoding="utf-8") if q.strip()]
    if "--retest" in sys.argv:
        prev = json.load(open("context_independence_mismatches.json", encoding="utf-8"))
        qs = sorted({m["q"] for m in prev})
    if limit:
        qs = qs[:limit]
    real = rag.llm
    rag.llm = StubLLM()
    mism, skipped, checked = [], 0, 0
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(SISOfficer).where(SISOfficer.email == OFFICER))).scalars().first()
        ctx = await build_officer_context(db, row)
        for i, q in enumerate(qs):
            try:
                o_intent, o_text = await asyncio.wait_for(ask(db, ctx, None, q), 40)
            except asyncio.TimeoutError:
                skipped += 1
                continue
            if SENTINEL in o_text or o_intent in ("followup_clarification", "TIMEOUT"):
                skipped += 1
                continue
            checked += 1
            for name, setup in CONTEXTS.items():
                try:
                    c_intent, c_text = await asyncio.wait_for(ask(db, ctx, setup, q), 40)
                except asyncio.TimeoutError:
                    c_intent, c_text = "TIMEOUT", ""
                if (c_intent, c_text) != (o_intent, o_text):
                    mism.append({"q": q, "context": name, "fresh_intent": o_intent, "ctx_intent": c_intent,
                                 "fresh": o_text[:220], "ctx": c_text[:220]})
            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{len(qs)}  mismatches so far: {len(mism)}", flush=True)
    rag.llm = real
    await engine.dispose()
    json.dump(mism, open("context_independence_mismatches.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    bad_q = sorted({m["q"] for m in mism})
    print(f"\nquestions checked: {checked} (skipped {skipped})   context runs: {checked * len(CONTEXTS)}")
    print(f"questions answered differently after a context: {len(bad_q)}   ({len(mism)} of {checked * len(CONTEXTS)} runs)")
    for m in mism[:25]:
        print(f"  [{m['context']}] {m['q'][:70]!r}: {m['fresh_intent']} -> {m['ctx_intent']}\n      fresh: {m['fresh'][:100]!r}\n      after: {m['ctx'][:100]!r}")
    return 0 if not mism else 1


raise SystemExit(asyncio.run(main()))
