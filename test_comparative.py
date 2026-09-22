"""Comparative questions -- "ISD vs NISD", "which is older A or B", "are there more NISD than ISD",
"compare ward 102 and ward 103", scoped ones ("... but not rejected", "... approved only"), and the
follow-ups that name no sides ("compare them", "which is higher?", "by how much", "and rejected"),
in English, Tamil and Tanglish, with spelling slips.

Every figure is recomputed from the officer's own rows, so it still means something after a
reseed. No LLM.

    python test_comparative.py            # non-streaming
    python test_comparative.py --stream   # the streaming entry point
"""
import asyncio
import json
import logging
import re
import sys
import uuid

logging.disable(logging.CRITICAL)
from sqlalchemy import select

from backend.database import AsyncSessionLocal, engine
from backend.models import SISOfficer
from backend.sample_db.build_lora_dataset import build_officer_context, _flatten_html
from backend.services import chatbot, rag
from backend.services import compare_followup as cf
from backend.services.postgres import get_officer_applications

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
        FAILS.append(f"{label}: {detail[:220]}")


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


async def run(db, ctx, chain):
    sid, hist, out = str(uuid.uuid4()), [], ""
    for msg in chain:
        raw = await turn(db, ctx, sid, msg, hist)
        hist += [{"role": "user", "content": msg}, {"role": "assistant", "content": raw}]
        out = re.sub(r"\s+", " ", _flatten_html(raw)).replace("**", "")
    return out


async def main():
    rag.llm = Stub()
    # the follow-up rewriter itself, no database
    n2 = ["2022/0153/28/000468", "2026/0154/28/001197"]
    for msg, nums, hist, want in [
        ("compare them", n2, [], "compare 2022/0153/28/000468 and 2026/0154/28/001197"),
        ("which one is older", n2, [], "which is older"), ("which is newr", n2, [], "which is newer"),
        ("எது பழையது", n2, [], "which is older"), ("which is faster", n2, [], "which took less time"),
        ("which is more", [], ["how many ISD applications", "and NISD"], "compare ISD and NISD"),
        ("whch is mor", [], ["how many isd aplications", "and nisd"], "compare ISD and NISD"),
        ("by how much", [], ["ISD vs NISD"], "compare ISD and NISD"),
        ("compare with NISD", [], ["show ISD applications"], "compare ISD and NISD"),
        ("edhu adhigam", [], ["ISD ah vida NISD athigama irukka"], "compare ISD and NISD"),
        ("compare them", [], ["how many approved", "and rejected"], "compare approved and rejected"),
        ("compare them", [], [], "compare"), ("which is older", [], [], "compare"),
        ("show approved applications", [], ["how many ISD applications"], None),
        ("which application is older than 2022/0153/28/000468", [], [], None),
        ("compare ISD and NISD", [], ["how many ISD applications"], None),
    ]:
        got = cf.rewrite(msg, nums, hist)
        check((got is None) if want is None else (got is not None and got.startswith(want)), f"rewrite {msg!r}", str(got))
    check(cf.swap_status("and rejected", ["how many approved"]) == "how many rejected", "swap status")
    check(cf.swap_status("and rejected", ["show ISD applications"]) is None, "swap needs a status")
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(SISOfficer).where(SISOfficer.email == "msivakumar@sis.tn.gov.in"))).scalars().first()
        ctx = await build_officer_context(db, row)
        rows = (await get_officer_applications(db, ctx, status=["approved", "pending", "in_progress", "escalated", "rejected"]))["applications"]
        by = {r["application_number"]: r for r in rows}
        c = lambda f: sum(1 for r in rows if f(r))
        ISD, NISD = c(lambda r: r["type"] == "ISD"), c(lambda r: r["type"] == "NISD")
        APP, REJ = c(lambda r: r["status"] == "approved"), c(lambda r: r["status"] == "rejected")
        CSC, SRO = c(lambda r: r["submission_channel"] == "CSC"), c(lambda r: r["submission_channel"] == "sub_registrar")
        nr = lambda r: r["status"] != "rejected"
        ap = lambda r: r["status"] == "approved"
        gap = lambda a, b: abs(a - b)

        def lead(a, b, an, bn):
            return f"{an if a > b else bn} leads {bn if a > b else an} by {gap(a, b)}" if a != b else "level"

        groups = [
            (["ISD vs NISD"], [f"ISD {ISD}, NISD {NISD}", lead(ISD, NISD, "ISD", "NISD")]),
            (["compare ISD and NISD"], [f"ISD {ISD}, NISD {NISD}"]),
            (["which has more applications ISD or NISD"], [f"ISD {ISD}, NISD {NISD}"]),
            (["are there more NISD than ISD applications"], [f"NISD {NISD}, ISD {ISD}"]),
            (["which is bigger ISD or NISD"], [f"ISD {ISD}, NISD {NISD}"]),
            (["compair aproved and rejectd applications"], [f"approved {APP}, rejected {REJ}"]),
            (["compare CSC and Sub Registrar"], [f"CSC {CSC}, Sub-Registrar {SRO}"]),
            (["CSC vs sub registrar which is higher"], [f"CSC {CSC}, Sub-Registrar {SRO}"]),
            (["ISD ah vida NISD athigama irukka"], [f"ISD {ISD}, NISD {NISD}", "அதிகம்"]),
            (["ISD NISD rendu la edhu adhigam"], [f"ISD {ISD}, NISD {NISD}"]),
            (["approved rejected compare pannu"], [f"{APP}", f"{REJ}"]),
            (["ISD மற்றும் NISD ஒப்பிடு"], [f"ISD {ISD}, NISD {NISD}"]),
            (["ISD ஐ விட NISD அதிகமா"], [f"ISD {ISD}, NISD {NISD}"]),
            # scoped
            (["compare ISD and NISD but not rejected"], [f"ISD {c(lambda r: r['type'] == 'ISD' and nr(r))}, NISD {c(lambda r: r['type'] == 'NISD' and nr(r))}", "excluding rejected"]),
            (["ISD vs NISD approved only"], [f"ISD {c(lambda r: r['type'] == 'ISD' and ap(r))}, NISD {c(lambda r: r['type'] == 'NISD' and ap(r))}", "approved only"]),
            (["compare CSC and Sub Registrar excluding rejected"], [f"CSC {c(lambda r: r['submission_channel'] == 'CSC' and nr(r))}, Sub-Registrar {c(lambda r: r['submission_channel'] == 'sub_registrar' and nr(r))}"]),
            # a ward the officer does not hold is refused, not counted as zero
            (["compare ward 102 and ward 103"], ["Ward 103 is outside your assigned jurisdiction"]),
            # follow-ups that name no sides
            (["how many ISD applications", "and NISD", "which is more"], [f"ISD {ISD}, NISD {NISD}"]),
            (["how many ISD applications", "and NISD", "compare them", "by how much"], [lead(ISD, NISD, "ISD", "NISD")]),
            (["how many ISD aplications", "and nisd", "whch is mor"], [f"ISD {ISD}, NISD {NISD}"]),
            (["how many ISD applications", "and NISD", "rendaiyum compare pannu"], [f"ISD {ISD}, NISD {NISD}"]),
            (["how many ISD applications", "and NISD", "எது அதிகம்"], [f"ISD {ISD}, NISD {NISD}"]),
            (["how many approved", "and rejected", "compare them"], [f"approved {APP}, rejected {REJ}"]),
            (["ISD vs NISD", "which is higher"], [f"ISD {ISD}, NISD {NISD}"]),
            (["show ISD applications", "compare with NISD"], [f"ISD {ISD}, NISD {NISD}"]),
            # nothing to compare with -> asks, never guesses
            (["compare them"], ["Tell me the two things to compare"]),
            (["compare"], ["Tell me the two things to compare"]),
            (["which is older"], ["Tell me the two things to compare"]),
        ]
        # two applications, the older by filing date
        a1, a2 = "2022/0153/28/000468", "2026/0154/28/001197"
        if a1 in by and a2 in by:
            old = min((a1, a2), key=lambda n: by[n]["submission_date"])
            new = max((a1, a2), key=lambda n: by[n]["submission_date"])
            both = f"show two applications {a1} {a2}"
            groups += [
                ([f"which is older {a1} or {a2}"], [f"{old} is older"]),
                ([f"which is newer, {a1} or {a2}"], [f"{new} is newer"]),
                ([f"compare {a1} and {a2}"], [f"{a1} vs {a2}"]),
                ([f"{a1} um {a2} um compare pannu"], ["வகை"]),
                ([f"எது பழையது {a1} அல்லது {a2}"], ["பழையது", old]),
                ([both, "which one is older"], [f"{old} is older"]),
                ([both, "which is newr"], [f"{new} is newer"]),
                ([both, "compare them"], [f"{a1} vs {a2}"]),
                ([both, "எது பழையது"], [old]),
                ([both, "which is faster"], [f"{a1} vs {a2}"]),
            ]
        for chain, wants in groups:
            t = await run(db, ctx, chain)
            for w in wants:
                check(w in t, f"{' | '.join(chain)!r} carries {w!r}", t[:200])
    await engine.dispose()
    print("FAILED:" if FAILS else "ALL PASSED")
    for f in FAILS:
        print(" ", f)
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
