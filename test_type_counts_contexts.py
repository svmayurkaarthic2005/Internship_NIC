"""ISD / NISD / MERGE show + count, with spelling errors, fresh and after a prior answer.

Every variant must (a) route to the same intent as the correct spelling, and (b) give the
same answer as the correct spelling asked in a clean session -- whatever was on screen
before. Counts are also compared with the database, rejected applications included.

python test_type_counts_contexts.py     # no LLM (stubbed), all three officers
"""
import asyncio
import logging
import re
import sys
import uuid

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.disable(logging.CRITICAL)

from sqlalchemy import text, select

from backend.database import AsyncSessionLocal, engine
from backend.models import SISOfficer
from backend.sample_db.build_lora_dataset import build_officer_context, _flatten_html
from backend.services import chatbot, rag

OFFICERS = ["csenthil", "msivakumar", "muthulakshmis"]
SETUPS = [None, "show applications", "show pending applications", "applications from CSC",
          "what is ISD", "how many field visits do I have"]
# (type, correct phrase, typo variants)
CASES = [
    ("NISD", "how many nisd applications", ["how mny nsid applications", "how many nsid applications",
                                            "hw many nisd aplications", "no of nsid applications"]),
    ("NISD", "show nisd applications", ["nsid applications", "show nsid", "show nisd aplications", "shw nisd applicaions"]),
    ("NISD", "total nisd applications", ["totl nisd applications", "total nsid aplications"]),
    ("ISD", "how many isd applications", ["how mny isd applications", "how many isd aplicatons", "hw many isd applications"]),
    ("ISD", "show isd applications", ["show isd aplications", "shw isd applicatons", "isd aplications"]),
    ("ISD", "total isd applications", ["totl isd applications", "total isd aplications"]),
]
FAILS = []


class Stub:
    temperature = 0.1

    def bind(self, **k): raise RuntimeError("stub")
    def bind_tools(self, *a, **k): raise RuntimeError("stub")

    async def ainvoke(self, *a, **k):
        class R: content = "[[LLM]]"
        return R()


def check(ok, label, detail=""):
    if not ok:
        FAILS.append(f"{label}: {detail[:160]}")


async def ask(db, ctx, setup, q):
    sid = str(uuid.uuid4())
    if setup:
        await chatbot.process_chat(setup, sid, ctx, db, chat_history=[])
    r = await chatbot.process_chat(q, sid, ctx, db, chat_history=[])
    return r.get("intent"), re.sub(r"\s+", " ", _flatten_html(r.get("response") or "")).strip()


async def main():
    rag.llm = Stub()
    n = 0
    async with AsyncSessionLocal() as db:
        for name in OFFICERS:
            row = (await db.execute(select(SISOfficer).where(SISOfficer.email == f"{name}@sis.tn.gov.in"))).scalars().first()
            ctx = await build_officer_context(db, row)
            truth = {}
            for t in ("ISD", "NISD"):
                truth[t] = (await db.execute(text(
                    """SELECT count(*) FROM applications a JOIN survey_numbers s ON s.id=a.survey_number_id
                       JOIN blocks b ON b.id=s.block_id JOIN officer_jurisdictions j ON j.officer_id=:o
                       WHERE b.ward_id=j.ward_id AND a.application_type=:t"""), {"o": row.id, "t": t})).scalar()
            for ty, ok_phrase, typos in CASES:
                o_intent, o_text = await ask(db, ctx, None, ok_phrase)
                is_count = "how many" in ok_phrase or ok_phrase.startswith("total")
                if is_count:
                    m = re.search(r"There (?:are|is) (\d+)|(\d+) ", o_text)
                    got = int(next(g for g in m.groups() if g)) if m else -1
                    check(got == truth[ty], f"{name} {ok_phrase!r} equals DB", f"{got} vs {truth[ty]}")
                else:
                    m = re.search(r"Found (\d+)", o_text)
                    check(m and int(m.group(1)) == truth[ty], f"{name} {ok_phrase!r} list equals DB", o_text[:80])
                for setup in SETUPS:
                    for q in [ok_phrase] + typos:
                        n += 1
                        intent, txt = await ask(db, ctx, setup, q)
                        check((intent, txt) == (o_intent, o_text), f"{name} [{setup}] {q!r}",
                              f"{intent}: {txt[:70]} | want {o_intent}: {o_text[:70]}")
    await engine.dispose()
    print(f"{n} asks checked")
    print("ALL PASSED" if not FAILS else f"FAILED ({len(FAILS)}):\n  - " + "\n  - ".join(FAILS[:40]))
    return 0 if not FAILS else 1

raise SystemExit(asyncio.run(main()))
