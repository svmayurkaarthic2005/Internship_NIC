"""Arithmetic questions -- per year / month / quarter, averages, growth between years, percentages and
rates, fee totals / averages / extremes, turnaround times -- in English, Tamil and Tanglish.

The expected figures come from independent code paths: the officer's own listing rows for counts, the
existing `get_fee_summary` for fees, and `get_comparison` for turnaround times. No LLM.

    python test_stats.py            # non-streaming, three officers
    python test_stats.py --stream   # the streaming entry point
"""
import asyncio
import json
import logging
import re
import sys
import uuid
from collections import Counter
from datetime import date

logging.disable(logging.CRITICAL)
from sqlalchemy import select

from backend.database import AsyncSessionLocal, engine
from backend.models import SISOfficer
from backend.sample_db.build_lora_dataset import build_officer_context, _flatten_html
from backend.services import chatbot, rag, stats_qa
from backend.services.postgres import get_comparison, get_fee_summary, get_officer_applications

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


async def ask(db, ctx, q):
    return re.sub(r"\s+", " ", _flatten_html(await turn(db, ctx, str(uuid.uuid4()), q, [])))


def money(v):
    return f"₹{v:,.2f}"


def pct(a, b):
    return f"{a * 100 / b:.1f}%" if b else "0.0%"


async def one_officer(db, email):
    row = (await db.execute(select(SISOfficer).where(SISOfficer.email == email))).scalars().first()
    ctx = await build_officer_context(db, row)
    rows = (await get_officer_applications(db, ctx, status=ALL))["applications"]
    tag = email.split("@")[0]
    n = len(rows)
    yr = lambda r: str(r["submission_date"])[:4]
    by_year = Counter(yr(r) for r in rows)
    years = sorted(by_year)
    y0, y1 = int(years[0]), int(years[-1])
    # pick the two most recent years with applications
    ya, yb = int(years[-2]), int(years[-1])
    st = lambda r: str(r["status"]).lower().replace(" ", "_")

    # counts per year -----------------------------------------------------------------------------
    t = await ask(db, ctx, "applications per year")
    for y in years:
        check(f"{y} {by_year[y]}" in t, f"[{tag}] per year {y}", t[:200])
    check(f"total {n}" in t, f"[{tag}] per year total", t[:200])
    # growth ------------------------------------------------------------------------------------------
    a, b = by_year[str(ya)], by_year[str(yb)]
    t = await ask(db, ctx, f"percentage change from {ya} to {yb}")
    if a:
        ch = (b - a) * 100 / a
        check(f"{a} in {ya} → {b} in {yb}" in t and f"{abs(ch):.1f}%" in t, f"[{tag}] growth", t[:200])
    # per month in a year: sums to the year, averages per month -------------------------------------
    t = await ask(db, ctx, f"average applications per month in {yb}")
    check(f"{b} applications over 12 months" in t or f"{b} applications over" in t, f"[{tag}] avg per month", t[:200])
    mo = re.search(r"month in \d{4}: ([\d.]+)", t)
    if mo and yb != date.today().year:
        check(abs(float(mo.group(1)) - b / 12) < 0.06, f"[{tag}] avg per month value", t[:200])
    # percent filed in a year ---------------------------------------------------------------------------
    t = await ask(db, ctx, f"what percent of applications were filed in {yb}")
    check(f"{b} of your {n} applications ({pct(b, n)}) were filed in {yb}" in t, f"[{tag}] percent in year", t[:200])
    t = await ask(db, ctx, "percentage of applications per year")
    for y in years:
        check(f"{y} {pct(by_year[y], n)} ({by_year[y]})" in t, f"[{tag}] share per year {y}", t[:200])
    # which year had the most / least ---------------------------------------------------------------
    top = max(by_year.values())
    t = await ask(db, ctx, "which year had the most applications")
    check(f"— {top} applications, most" in t and all(y in t for y in years if by_year[y] == top), f"[{tag}] most", t[:160])
    low = min(by_year.values())
    t = await ask(db, ctx, "which year had the least applications")
    check(f"— {low} applications, fewest" in t, f"[{tag}] least", t[:160])
    # rates ------------------------------------------------------------------------------------------------
    t = await ask(db, ctx, "approval rate per year")
    for y in years:
        grp = [r for r in rows if yr(r) == y and st(r) in ("approved", "rejected")]
        if grp:
            k = sum(1 for r in grp if st(r) == "approved")
            check(f"{y} {pct(k, len(grp))} ({k} of {len(grp)})" in t, f"[{tag}] approval rate {y}", t[:200])
    dec = [r for r in rows if st(r) in ("approved", "rejected")]
    rj = sum(1 for r in dec if st(r) == "rejected")
    t = await ask(db, ctx, "rejection rate")
    check(f"{pct(rj, len(dec))} — {rj} of {len(dec)} decided" in t, f"[{tag}] rejection rate", t[:200])
    # quarter -----------------------------------------------------------------------------------------------
    q1 = sum(1 for r in rows if yr(r) == str(yb) and int(str(r["submission_date"])[5:7]) <= 3)
    t = await ask(db, ctx, f"applications in Q1 {yb}")
    check(f"{q1} applications were filed in Q1 {yb}" in t, f"[{tag}] Q1", t[:160])
    # fee: independent of the fee summary -----------------------------------------------------------------
    for y in (ya, yb):
        fs = (await get_fee_summary(db, ctx, start_date=date(y, 1, 1), end_date=date(y, 12, 31)))["fee_summary"]
    t = await ask(db, ctx, "total fee per year")
    for y in years:
        fs = (await get_fee_summary(db, ctx, start_date=date(int(y), 1, 1), end_date=date(int(y), 12, 31)))["fee_summary"]
        check(f"{y} {money(fs['total_fee'])}" in t, f"[{tag}] fee {y}", t[:240])
    fs_all = (await get_fee_summary(db, ctx))["fee_summary"]
    t = await ask(db, ctx, "average fee per application")
    check(f"{money(fs_all['total_fee'] / fs_all['with_fee'])} over the {fs_all['with_fee']} of {fs_all['total_applications']}" in t,
          f"[{tag}] average fee", t[:200])
    t = await ask(db, ctx, "highest fee")
    check(f"Highest fee on record: {money(fs_all['max_fee'])}" in t, f"[{tag}] highest fee", t[:160])
    t = await ask(db, ctx, "percentage of applications with fee recorded")
    check(f"{fs_all['with_fee']} of {fs_all['total_applications']}" in t, f"[{tag}] fee recorded", t[:160])
    fi = (await get_fee_summary(db, ctx, application_type="ISD"))["fee_summary"]
    fn = (await get_fee_summary(db, ctx, application_type="NISD"))["fee_summary"]
    t = await ask(db, ctx, "compare fee of ISD and NISD")
    check(f"ISD {money(fi['total_fee'])}" in t and f"NISD {money(fn['total_fee'])}" in t, f"[{tag}] fee ISD vs NISD", t[:240])
    t = await ask(db, ctx, "which type has higher fee")
    check(f"ISD {money(fi['total_fee'])}" in t and f"NISD {money(fn['total_fee'])}" in t, f"[{tag}] which type higher fee", t[:200])
    t = await ask(db, ctx, f"fee collected in {ya} vs {yb}")
    fa = (await get_fee_summary(db, ctx, start_date=date(ya, 1, 1), end_date=date(ya, 12, 31)))["fee_summary"]["total_fee"]
    fb = (await get_fee_summary(db, ctx, start_date=date(yb, 1, 1), end_date=date(yb, 12, 31)))["fee_summary"]["total_fee"]
    check(f"{ya}: {money(fa)}" in t and f"{yb}: {money(fb)}" in t, f"[{tag}] fee year vs year", t[:240])
    # turnaround: independent of get_comparison ---------------------------------------------------------
    cmp_ = await get_comparison(db, ctx, {"kind": "type", "left": "ISD", "right": "NISD", "sides": ["ISD", "NISD"], "aspect": "duration"})
    t = await ask(db, ctx, "average days to decide by type")
    for s_ in cmp_["sides"]:
        if s_["days"] is not None:
            check(f"{s_['label']} {s_['days']:.1f} days ({s_['population']})" in t, f"[{tag}] days {s_['label']}", t[:200])
    # existing answers are left alone --------------------------------------------------------------------------
    for q, want, bad in [("total fee", "Fee collected:", "Fee collected per"), ("what is the fee for ISD", "ISD", "Fee collected"),
                         ("compare 2024 and 2025", "Applications filed —", "up "), (f"show applications in {yb}", "Found", "Average"),
                         ("how many applications in total", "in total", "Average")]:
        t = await ask(db, ctx, q)
        check(want in t and bad not in t, f"[{tag}] unchanged: {q!r}", t[:160])
    # Tamil / Tanglish -------------------------------------------------------------------------------------------
    t = await ask(db, ctx, "ஆண்டு வாரியாக விண்ணப்பங்கள்")
    check(all(f"{y} {by_year[y]}" in t for y in years) and "மொத்தம்" in t, f"[{tag}] Tamil per year", t[:160])
    t = await ask(db, ctx, "varusham vaariyaaga applications")
    check(all(f"{y} {by_year[y]}" in t for y in years), f"[{tag}] Tanglish per year", t[:160])


async def main():
    rag.llm = Stub()
    # the pure part
    rows = [{"application_number": f"2025/0154/28/{i:06d}", "type": ty, "status": s, "submission_channel": "CSC",
             "submission_date": date.fromisoformat(d), "fee": fee, "days_to_decide": dd}
            for i, (ty, s, d, fee, dd) in enumerate([
                ("ISD", "approved", "2024-01-10", 600.0, 10), ("ISD", "rejected", "2024-02-11", 600.0, 20),
                ("NISD", "approved", "2025-01-12", 0.0, 5), ("NISD", "approved", "2025-02-13", None, 15),
                ("NISD", "pending", "2025-07-01", None, None), ("ISD", "approved", "2025-08-05", 600.0, 25)], 1)]
    for msg, want in [("applications per year", "2024 2, 2025 4"), ("percentage change from 2024 to 2025", "2 in 2024 → 4 in 2025 — up 2 (+100.0%)"),
                      ("fee collected in 2024 vs 2025", "2024: ₹1,200.00"), ("average fee per application", "₹450.00 over the 4 of 6"),
                      ("approval rate per year", "2024 50.0% (1 of 2), 2025 100.0% (3 of 3)"), ("highest fee", "₹600.00"),
                      ("average days to decide by type", "ISD 18.3 days (3), NISD 10.0 days (2)"),
                      ("which year had the most applications", "2025 — 4 applications, most"),
                      ("applications in Q1 2025", "2 applications were filed in Q1 2025"),
                      ("show applications in 2025", None), ("compare 2024 and 2025", None)]:
        got = stats_qa.answer(msg, rows, False)
        check((got is None) if want is None else (got is not None and want in got), f"stats_qa {msg!r}", str(got))
    async with AsyncSessionLocal() as db:
        for email in ("msivakumar@sis.tn.gov.in", "csenthil@sis.tn.gov.in", "muthulakshmis@sis.tn.gov.in"):
            await one_officer(db, email)
    await engine.dispose()
    print("FAILED:" if FAILS else "ALL PASSED")
    for f in FAILS:
        print(" ", f)
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
