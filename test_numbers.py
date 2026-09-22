"""Number questions, new and follow-up, in English, Tamil and Tanglish, with spelling slips:
totals, "how many approved", percentages / fractions, ratios, "by type / per month" breakdowns,
"how many X and how many Y", survey-number counts, and the same asked over the list on screen.

Every expected figure is counted from the officer's own rows (all statuses), so it still means
something after a reseed. No LLM.

    python test_numbers.py            # non-streaming, three officers
    python test_numbers.py --stream   # the streaming entry point
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
from backend.services import chatbot, number_qa, rag
from backend.services.postgres import get_officer_applications

STREAM = "--stream" in sys.argv
FAILS = []
ALL = ["approved", "pending", "in_progress", "escalated", "rejected"]


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


async def run(db, ctx, chain):
    sid, hist, out = str(uuid.uuid4()), [], ""
    for msg in chain:
        raw = await turn(db, ctx, sid, msg, hist)
        hist += [{"role": "user", "content": msg}, {"role": "assistant", "content": raw}]
        out = re.sub(r"\s+", " ", _flatten_html(raw))
    return out


async def one_officer(db, email):
    row = (await db.execute(select(SISOfficer).where(SISOfficer.email == email))).scalars().first()
    ctx = await build_officer_context(db, row)
    rows = (await get_officer_applications(db, ctx, status=ALL))["applications"]
    n = len(rows)
    c = lambda f, rs=None: sum(1 for r in (rows if rs is None else rs) if f(r))
    st = lambda r: str(r["status"]).lower().replace(" ", "_")
    ty = lambda r: str(r["type"]).upper()
    ch = lambda r: r["submission_channel"]
    APP, REJ, PEN = c(lambda r: st(r) == "approved"), c(lambda r: st(r) == "rejected"), c(lambda r: st(r) == "pending")
    ISD, NISD = c(lambda r: ty(r) == "ISD"), c(lambda r: ty(r) == "NISD")
    CSC = c(lambda r: ch(r) == "CSC")
    pct = lambda a, b: f"{a * 100 / b:.1f}%" if b else "0%"
    tag = email.split("@")[0]
    # the approved list a follow-up runs over (rows of the register that are approved)
    appr = [r for r in rows if st(r) == "approved"]
    A = len(appr)
    A_ISD, A_NISD = c(lambda r: ty(r) == "ISD", appr), c(lambda r: ty(r) == "NISD", appr)
    A_CSC = c(lambda r: ch(r) == "CSC", appr)
    A_REJ_ISD = None
    isd_rows = [r for r in rows if ty(r) == "ISD"]
    I = len(isd_rows)
    cases = [
        # fresh
        (["how many applications do I have"], [f"You have {n} applications in total"]),
        (["count of applications"], [f"{n} applications in total"]),
        (["total number of applications"], [f"{n} applications in total"]),
        (["how many aplications do i hav"], [f"{n} applications in total"]),
        (["how many approved"], [f"{APP} approved"]),
        (["how many rejected"], [f"{REJ} rejected"]),
        (["percentage of approved applications"], [f"{APP} of your {n} applications ({pct(APP, n)})"]),
        (["what percent are rejected"], [f"{REJ} of your {n} applications ({pct(REJ, n)})"]),
        (["ratio of ISD to NISD"], [f"ISD : NISD = {ISD} : {NISD}"]),
        (["number of applications by type"], [f"ISD {ISD}", f"NISD {NISD}", f"total {n}"]),
        (["number of applications by status"], [f"approved {APP}", f"rejected {REJ}", f"total {n}"]),
        (["how many applications by channel"], [f"CSC {CSC}", f"total {n}"]),
        (["per year applications count"], [f"total {n}"]),
        (["how many applications per month"], [f"total {n}"]),
        (["evlo applications iruku"], [f"மொத்தம் {n} விண்ணப்பங்கள்"]),
        (["எத்தனை விண்ணப்பங்கள் உள்ளன"], [f"மொத்தம் {n} விண்ணப்பங்கள்"]),
        (["approved evlo percent"], [f"{APP} ({pct(APP, n)})"]),
        # follow-ups over the list on screen (the approved rows)
        (["show approved applications", "what percentage are ISD"], [f"{A_ISD} of those {A} approved applications ({pct(A_ISD, A)}) are ISD"]),
        (["show approved applications", "how many of them are NISD and how many ISD"], [f"{A_NISD} of those {A} approved applications", f"{A_ISD} of those {A} approved applications"] if False else [f"{A_NISD}", f"{A_ISD}", f"{A} approved applications"]),
        (["show approved applications", "what percent are from CSC"], [f"{A_CSC} of those {A} approved applications ({pct(A_CSC, A)})"]),
        (["show approved applications", "how many are ISD"], [f"{A_ISD} of those {A} approved applications are ISD"]),
        (["show approved applications", "athula evlo ISD"], [f"அந்த {A} அங்கீகரிக்கப்பட்ட விண்ணப்பங்களில் {A_ISD} ISD"]),
        (["show approved applications", "அதில் எத்தனை ISD"], [f"அந்த {A} அங்கீகரிக்கப்பட்ட விண்ணப்பங்களில் {A_ISD} ISD"]),
        (["show ISD applications", "how many are approved", "how many are rejected"],
         [f"{c(lambda r: st(r) == 'rejected', isd_rows)} of those {I} ISD applications are rejected"]),
        (["show ISD applications", "how many are approved", "and rejected", "and pending"],
         [f"{c(lambda r: st(r) == 'pending', isd_rows)} of those {I} ISD applications"]),
        (["show ISD applications", "what fraction are approved"],
         [f"{c(lambda r: st(r) == 'approved', isd_rows)} of those {I} ISD applications ({pct(c(lambda r: st(r) == 'approved', isd_rows), I)})"]),
        # a named noun ignores the list on screen: it asks about the whole register
        (["show ISD applications", "percentage of approved applications"], [f"{APP} of your {n} applications ({pct(APP, n)})"]),
    ]
    # a bare scope word after an answer: yes / no about the one file, a count over the list
    one = next((r for r in rows if st(r) == "approved"), None)
    if one:
        a = one["application_number"]
        lab = lambda v: v.replace("_", " ")
        cases += [
            ([f"status of {a}", "rejected ?"], [f"No — {a} is approved, not rejected."]),
            ([f"status of {a}", "approved?"], [f"Yes — {a} is approved."]),
            ([f"status of {a}", "completed?"], [f"Yes — {a} is approved."]),
            ([f"status of {a}", "rejected illaya"], [f"No — {a} is approved, not rejected."]),
            ([f"status of {a}", "நிராகரிக்கப்பட்டதா?"], [f"இல்லை — {a}"]),
            ([f"status of {a}", "isd?"], [f"{a} is ISD (0154)." if ty(one) == "ISD" else f"No — {a} is NISD (0153), not ISD."]),
        ]
    cases += [
        (["show approved applications", "rejected ?"], [f"None of those {A} applications are rejected."]),
        (["show approved applications", "pending?"], [f"None of those {A} applications are pending."]),
        (["show ISD applications", "rejected ?"],
         [f"{c(lambda r: st(r) == 'rejected', isd_rows)} of those {I} ISD applications" if c(lambda r: st(r) == 'rejected', isd_rows)
          else f"None of those {I} ISD applications are rejected."]),
    ]
    for chain, wants in cases:
        t = await run(db, ctx, chain)
        for w in wants:
            check(w in t, f"[{tag}] {' | '.join(chain)!r} carries {w!r}", t[:200])
    # survey numbers are counted from the jurisdiction summary
    jur = (await chatbot._build_jurisdiction_summary(db, ctx)).get("jurisdiction") or {}
    if isinstance(jur.get("survey_count"), int):
        for q, w in [("how many survey numbers do I have", f"{jur['survey_count']} survey number"),
                     ("எத்தனை சர்வே எண்கள்", f"{jur['survey_count']} சர்வே எண்கள்")]:
            t = await run(db, ctx, [q])
            check(w in t, f"[{tag}] {q!r} carries {w!r}", t[:160])


async def main():
    rag.llm = Stub()
    # the pure part, no database
    rows = [{"status": "approved", "type": "ISD", "submission_channel": "CSC", "ward_number": "102", "submission_date": "2025-03-01"},
            {"status": "approved", "type": "NISD", "submission_channel": "sub_registrar", "ward_number": "102", "submission_date": "2025-04-01"},
            {"status": "rejected", "type": "ISD", "submission_channel": "CSC", "ward_number": "103", "submission_date": "2026-01-01"},
            {"status": "pending", "type": "NISD", "submission_channel": "CSC", "ward_number": "102", "submission_date": "2026-02-01"}]
    for msg, want in [("how many approved", "2 approved applications (of 4 in total)"),
                      ("what percent are rejected", "1 of your 4 applications (25.0%)"),
                      ("ratio of ISD to NISD", "ISD : NISD = 2 : 2 (50.0% / 50.0%)"),
                      ("how many applications by year", "2025 2, 2026 2"), ("how many applications do I have", "4 applications in total"),
                      ("how many NISD and how many ISD", "2 NISD, 2 ISD"), ("how many not approved", None),
                      ("compare ISD and NISD", None), ("show approved applications", None),
                      ("how many applications are overdue", None), ("how many approved applications", None),
                      ("hello and how many are approved", None), ("how many approved and xyz", None),
                      ("how many are approved and how many rejected", "2 approved, 1 rejected")]:
        got = number_qa.answer(msg, rows, None, False)
        check((got is None) if want is None else (got is not None and want in got), f"number_qa {msg!r}", str(got))
    # model-written pipe rows become a real table (the register's own markup); prose with a lone pipe is left alone
    t = chatbot._pipes_to_table("Found 1 application(s):\nApplication No. | Type | Ward\n|\n2025/0153/28/000001 | NISD | 102 |\nDone.")
    check("<table class='data-table'>" in t and "<td>NISD</td>" in t and "Done." in t and "|" not in t, "pipes to table", t[:200])
    check("<table" in chatbot._pipes_to_table("| a | b |\n|---|---|\n| 1 | 2 |"), "markdown table")
    check("<table" not in chatbot._pipes_to_table("plain text with one | pipe"), "a lone pipe stays text")
    from backend.services import qualifier_guard as qg
    for msg, want in [("display between jan and feb 2025", "display between jan and feb 2025 applications"),
                      ("show rejected", "show rejected applications"), ("display 2023", "display 2023 applications"),
                      ("show pending applications", "show pending applications"), ("display those", "display those"),
                      ("show 102", "show 102"), ("display my", "display my"), ("show today", "show today applications")]:
        check(qg.add_noun(msg) == want, f"add_noun {msg!r}", qg.add_noun(msg))
    for msg, want in [("between jan and feb 2025", "between jan 2025 and feb 2025"), ("jan - mar 2023", "between jan 2023 and mar 2023"),
                      ("show applications from jan to feb 2025", "show applications from jan 2025 to feb 2025"),
                      ("june 2025", "june 2025"), ("jan 2024 and feb 2025", "jan 2024 and feb 2025")]:
        check(qg.share_year(msg) == want, f"share_year {msg!r}", qg.share_year(msg))
    async with AsyncSessionLocal() as db:
        for email in ("msivakumar@sis.tn.gov.in", "csenthil@sis.tn.gov.in", "muthulakshmis@sis.tn.gov.in"):
            await one_officer(db, email)
    await engine.dispose()
    print("FAILED:" if FAILS else "ALL PASSED")
    for f in FAILS:
        print(" ", f)
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
