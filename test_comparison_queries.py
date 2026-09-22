"""Comparison questions about applications: routing, the numbers, the verdict.

An officer comparing two things wants to be told which -- and by how much.
Before this suite existed each shape failed in its own way: "compare ISD and
NISD" answered with the single pending ISD file, two application numbers in one
message produced a two-row status dump that compared nothing, and "which took
the longest to approve" fell through to the LLM, which has no counts to compare.

Three layers, none of them needing Ollama:

  routing   the phrase reaches `compare_applications`, and questions that
            belong elsewhere ("which has been pending the longest", "is there a
            fee difference between ISD and NISD") keep their own handlers.
  data      both sides are counted straight from the ORM tables, and the same
            numbers are recomputed here the long way.
  answers   the rendered answer names both sides and states the verdict.

    python test_comparison_queries.py            # all three
    python test_comparison_queries.py --routing  # routing only, no database
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import structlog
from sqlalchemy import func, select

from backend.database import AsyncSessionLocal
from backend.models import Application, SISOfficer, WorkflowHistory
from backend.schemas import OfficerContext
from backend.services.auth_service import get_officer_jurisdiction_ids
from backend.services.chatbot import process_chat
from backend.services.postgres import get_comparison
from backend.services.rag import parse_comparison_query, parse_intent

logging.disable(logging.INFO)
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))

_A = "2026/0153/28/001190"
_B = "2025/0154/28/001914"

# ── 1. Routing: 66 phrasings, a third of them misspelled ────────────────────
# Officers type fast on a shared terminal. Every keyword below is matched on
# TOKENS through `is_token_typo_match`, the same edit-distance rule the rest of
# parse_intent uses, so a comparison survives "compair", "nsid", "approvd" and
# "longst". The negatives at the end matter as much as the positives: a parser
# loose enough to catch every typo also swallows questions that belong to other
# handlers.

# (question, expected intent, expected comparison kind or None)
ROUTING_CASES = [
    (f"compare {_A} and {_B}", "compare_applications", "applications"),
    (f"which is older {_A} or {_B}", "compare_applications", "applications"),
    (f"which took longer {_A} or {_B}", "compare_applications", "applications"),
    ("compare isd and nisd applications", "compare_applications", "type"),
    ("isd vs nisd", "compare_applications", "type"),
    ("pending versus approved", "compare_applications", "status"),
    ("compare csc and sub registrar applications", "compare_applications", "channel"),
    ("which channel has more applications", "compare_applications", "channel"),
    ("which application took the longest to approve",
     "compare_applications", "superlative"),
    ("which was the quickest approval", "compare_applications", "superlative"),
    ("which application was rejected the slowest",
     "compare_applications", "superlative"),
    # These belong to other handlers and must keep them.
    ("Which application has been pending the longest?", "pending_longest", None),
    ("Is there any fee difference between ISD and NISD?", "fee_lookup", None),
    ("show my approved applications", "pending_applications", None),
    (f"what is the status of {_A}", "application_status", None),
    ("how many isd applications do i have", "isd_applications", None),
    ("my last approved application", "last_application", None),
    ("which is my oldest pending application", "pending_applications", None),

    # ── more phrasings of the same three shapes ──
    (f"compare {_A} with {_B}", "compare_applications", "applications"),
    (f"{_A} vs {_B}", "compare_applications", "applications"),
    (f"{_A} versus {_B}", "compare_applications", "applications"),
    (f"which one is older, {_A} or {_B}?", "compare_applications", "applications"),
    (f"difference between {_A} and {_B}", "compare_applications", "applications"),
    (f"is {_A} bigger than {_B}", "compare_applications", "applications"),
    (f"which has more area {_A} or {_B}", "compare_applications", "applications"),
    (f"which was filed first {_A} or {_B}", "compare_applications", "applications"),
    (f"which was decided faster {_A} or {_B}", "compare_applications", "applications"),
    ("compare isd and nisd", "compare_applications", "type"),
    ("isd versus nisd applications", "compare_applications", "type"),
    ("compare isd with nisd", "compare_applications", "type"),
    ("how many isd compared to nisd", "compare_applications", "type"),
    ("approved vs rejected", "compare_applications", "status"),
    ("compare pending and rejected", "compare_applications", "status"),
    ("csc vs sro", "compare_applications", "channel"),
    ("csc versus citizen", "compare_applications", "channel"),
    ("merge vs isd", "compare_applications", "type"),
    ("which type has more applications", "compare_applications", "type"),
    ("isd vs nisd vs merge", "compare_applications", "type"),
    ("which took the least time to approve", "compare_applications", "superlative"),
    ("what is the slowest approval", "compare_applications", "superlative"),
    ("which had the fastest turnaround", "compare_applications", "superlative"),
    ("which application had the longest processing time",
     "compare_applications", "superlative"),

    # ── the same questions, misspelled ──
    (f"compair {_A} and {_B}", "compare_applications", "applications"),
    (f"comapre {_A} and {_B}", "compare_applications", "applications"),
    (f"campare {_A} and {_B}", "compare_applications", "applications"),
    (f"whcih is older {_A} or {_B}", "compare_applications", "applications"),
    (f"which is oldar {_A} or {_B}", "compare_applications", "applications"),
    (f"which took longger {_A} or {_B}", "compare_applications", "applications"),
    (f"which took lomger {_A} or {_B}", "compare_applications", "applications"),
    (f"differance between {_A} and {_B}", "compare_applications", "applications"),
    ("isd vs nsid", "compare_applications", "type"),
    ("compair isd and nisd", "compare_applications", "type"),
    ("compare isd and nsid applications", "compare_applications", "type"),
    ("isd verses nisd", "compare_applications", "type"),
    ("pendng versus approvd", "compare_applications", "status"),
    ("aproved vs rejcted", "compare_applications", "status"),
    ("compare csc and sub registrer", "compare_applications", "channel"),
    ("whcih channel has more applications", "compare_applications", "channel"),
    ("which application took the longst to approve",
     "compare_applications", "superlative"),
    ("which was the quickst approval", "compare_applications", "superlative"),
    ("which application took the longest to aprove",
     "compare_applications", "superlative"),
    ("which had the fastst turnaround", "compare_applications", "superlative"),

    # ── Tamil and Tanglish ──
    ("isd nisd ஒப்பிடு", "compare_applications", "type"),
    ("எது அதிகம் isd அல்லது nisd", "compare_applications", "type"),
    ("isd nisd compare pannu", "compare_applications", "type"),
    ("edhu adhigam isd or nisd", "compare_applications", "type"),
    ("எந்த விண்ணப்பம் அதிக நாட்கள் எடுத்தது",
     "compare_applications", "superlative"),

    # ── must keep their own handlers ──
    ("show pending applications", "pending_applications", None),
    ("what is the fee for isd", "fee_lookup", None),
    # ── two periods ──
    ("compare this month and last month", "compare_applications", "period"),
    ("this year vs last year", "compare_applications", "period"),
    ("2025 vs 2026", "compare_applications", "period"),
    ("compare march 2025 and march 2026", "compare_applications", "period"),
    ("compare june and july", "compare_applications", "period"),
    ("compair this month and last month", "compare_applications", "period"),
    ("இந்த மாதம் கடந்த மாதம் ஒப்பிடு", "compare_applications", "period"),
    # ── groups compared by TIME, not volume ──
    ("which took more time isd or nisd", "compare_applications", "type"),
    ("do isd take longer than nisd", "compare_applications", "type"),
    ("is csc faster than sub registrar", "compare_applications", "channel"),
    # ── wards, months, and the average over the whole desk ──
    ("compare ward 102 and ward 103", "compare_applications", "ward"),
    ("which ward has more applications", "compare_applications", "ward"),
    ("which month had the most applications", "compare_applications", "month"),
    ("which is the quietest month", "compare_applications", "month"),
    ("average time to approve", "compare_applications", "average_duration"),
    ("what is the average processing time", "compare_applications",
     "average_duration"),
    # ── per-application duration ──
    (f"how long did {_A} take", "application_status", None),
    (f"how many days did {_A} take to approve", "application_status", None),
    (f"what is the turnaround for {_A}", "application_status", None),
    # ── the ward and month rules must not steal a field lookup ──
    (f"which ward is {_A} in", "application_status", None),
    (f"which block is {_A} from", "application_status", None),
    ("show applications in ward 102", "pending_applications", None),
    ("which ward am i in", "jurisdiction_summary", None),
    # "last" is one edit from "least" and must never be read as a superlative.
    ("what about last month", "general_query", None),
    # A period named once is a scope, not a comparison.
    ("show applications last month", "pending_applications", None),
    ("applications in june and july", "pending_applications", None),
    # The service code inside an application number must not be read as a
    # comparison side: "0153" is the NISD token, and left in it turned this
    # into an ISD-vs-NISD count.
    (f"compare {_A} and 9999", "application_status", None),
    (f"compare {_A} and {_A}", "application_status", None),
]


def run_routing() -> int:
    failures = 0
    print("── Routing ──")
    for question, want_intent, want_kind in ROUTING_CASES:
        intent = parse_intent(question)
        spec = parse_comparison_query(question)
        got_kind = spec.get("kind") if spec else None
        ok = intent == want_intent and got_kind == want_kind
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {question}")
        print(f"        -> {intent} / {got_kind}"
              + ("" if ok else f"   (expected {want_intent} / {want_kind})"))
    return failures


async def officer_context(db, officer) -> OfficerContext:
    jur = await get_officer_jurisdiction_ids(officer.id, db)
    ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
           + jur["ward_ids"] + jur["block_ids"])
    return OfficerContext(
        officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
        email=officer.email, designation=officer.designation,
        jurisdiction_type=jur["jurisdiction_type"],
        jurisdiction_name=jur["jurisdiction_name"],
        jurisdiction_ids=[i for i in ids if i])


async def run_data(db, officer, officer_row) -> int:
    """The counts the handler reports, recomputed the long way."""
    failures = 0
    print("\n── Data ──")

    async def tally(column):
        rows = (await db.execute(
            select(column, func.count())
            .where(Application.assigned_officer_id == officer_row.id)
            .group_by(column))).all()
        return {r[0]: r[1] for r in rows if r[0]}

    for kind, column, pair in (
            ("type", Application.application_type, ("ISD", "NISD")),
            ("status", Application.current_status, ("pending", "approved")),
            ("channel", Application.submission_channel, ("CSC", "sub_registrar"))):
        want = await tally(column)
        got = await get_comparison(db, officer,
                                   {"kind": kind, "left": pair[0], "right": pair[1],
                                    "aspect": "count"})
        sides = {s["label"]: s["count"] for s in got.get("sides", [])}
        ok = all(sides.get(p, 0) == want.get(p, 0) for p in pair)
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {kind}: {sides} "
              f"(register holds {({p: want.get(p, 0) for p in pair})})")

    # Periods: counted on submission_date, recomputed here from the rows.
    from datetime import date as _d
    spec = {"kind": "period", "aspect": "count", "periods": [
        {"label": "2025", "start": "2025-01-01", "end": "2025-12-31"},
        {"label": "2026", "start": "2026-01-01", "end": "2026-12-31"}]}
    got = await get_comparison(db, officer, spec)
    all_apps = (await db.execute(
        select(Application)
        .where(Application.assigned_officer_id == officer_row.id))).scalars().all()
    want = {"2025": 0, "2026": 0}
    for app in all_apps:
        if not app.submission_date:
            continue
        year = str(app.submission_date.year)
        if year in want:
            want[year] += 1
    sides = {s["label"]: s["count"] for s in got.get("sides", [])}
    ok = sides == want
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  period: {sides} (register holds {want})")

    # The superlative, recomputed from the two dates it measures.
    got = await get_comparison(db, officer, {"kind": "superlative",
                                             "aspect": "duration",
                                             "status": "approved",
                                             "extreme": "max"})
    rows = (await db.execute(
        select(Application)
        .where(Application.assigned_officer_id == officer_row.id,
               Application.current_status == "approved"))).scalars().all()
    measured = []
    for app in rows:
        decided = (await db.execute(
            select(func.max(WorkflowHistory.performed_at))
            .where(WorkflowHistory.application_id == app.id,
                   WorkflowHistory.to_stage.in_(("COMPLETED", "REJECTED"))))).scalar()
        if not decided or not app.submission_date:
            continue
        measured.append(((decided.date() - app.submission_date).days,
                         app.application_number))
    # Several files take the same number of days, so the winner is only
    # well-defined with a tiebreak -- the handler uses the application number
    # and so does this recomputation, or the two disagree at random.
    measured.sort(key=lambda r: (-r[0], r[1]))
    longest, longest_no = measured[0] if measured else (-1, None)
    ok = got.get("found") and got.get("days") == longest and \
        got.get("application_number") == longest_no
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  slowest approval: "
          f"{got.get('application_number')} at {got.get('days')} days "
          f"(recomputed {longest_no} at {longest})")
    # The superlative must respect the status it was asked about.
    ok = all(a.current_status == "approved" for a in rows
             if a.application_number == got.get("application_number"))
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  \"longest to approve\" names an approved "
          f"application, not merely a slow one")

    # Asked twice, the same question must name the same application: several
    # files share the top duration, so without a tiebreak the answer moved.
    again = await get_comparison(db, officer, {"kind": "superlative",
                                              "aspect": "duration",
                                              "status": "approved",
                                              "extreme": "max"})
    ok = again.get("application_number") == got.get("application_number")
    failures += 0 if ok else 1
    tied = got.get("tied_with") or []
    print(f"  {'PASS' if ok else 'FAIL'}  the winner is stable across runs"
          f" ({len(tied)} other application(s) took exactly as long)")
    return failures


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


async def run_answers(db, officer) -> int:
    failures = 0
    print("\n── Answers ──")
    session_id = str(uuid.uuid4())

    # (question, substrings the answer must contain)
    CASES = [
        (f"which is older {_A} or {_B}", [_A, _B, "older", "2025-09-23"]),
        (f"which took longer {_A} or {_B}", [_A, _B, "took longer", "68", "19"]),
        (f"compare {_A} and {_B}", [_A, _B, "nisd", "isd", "approved", "rejected"]),
        ("isd vs nisd", ["isd", "nisd", "leads"]),
        ("pending versus approved", ["pending", "approved", "leads"]),
        ("compare csc and sub registrar applications",
         ["csc", "sub-registrar", "leads"]),
        ("which application took the longest to approve",
         ["longest", "days", "median", "approved"]),
        ("which was the quickest approval", ["quickest", "days", "median"]),
    ]
    for question, needles in CASES:
        result = await process_chat(db=db, message=question, officer=officer,
                                    session_id=session_id, chat_history=[])
        answer = _strip_html(result.get("response") or "").lower()
        missing = [n for n in needles if n.lower() not in answer]
        ok = result.get("intent") == "compare_applications" and not missing
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {question}")
        print(f"        intent={result.get('intent')} "
              f"{'' if ok else f'missing={missing} '}"
              f"answer={' '.join(answer.split())[:190]}")

    # Two periods stay apart -- extract_month_scopes deliberately MERGES
    # contiguous months, which would collapse "June and July" into one side.
    for question, needles in (
            ("this year vs last year", ["2026", "2025", "ahead of"]),
            ("compare march 2025 and march 2026",
             ["march 2025", "march 2026"]),
            ("compare june and july", ["june", "july"])):
        result = await process_chat(db=db, message=question, officer=officer,
                                    session_id=session_id, chat_history=[])
        answer = _strip_html(result.get("response") or "").lower()
        missing = [n for n in needles if n.lower() not in answer]
        ok = result.get("intent") == "compare_applications" and not missing
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {question}")
        print(f"        {'' if ok else f'missing={missing} '}"
              f"answer={' '.join(answer.split())[:150]}")

    # ── the shapes the broad sweep turned up ──────────────────────────────
    for question, needles in (
            ("do isd take longer than nisd",
             ["isd", "nisd", "days", "longer"]),
            ("is csc faster than sub registrar",
             ["csc", "sub-registrar", "days"]),
            # a ward the officer does not hold is refused, not reported as a count of zero
            ("compare ward 102 and ward 103", ["ward 103", "outside your assigned jurisdiction"]),
            ("which month had the most applications",
             ["busiest month", "applications"]),
            ("average time to approve",
             ["mean time", "median", "approved"]),
            (f"how long did {_A} take", [_A, "days", "filed"]),
            (f"how many days did {_A} take to approve", [_A, "days"])):
        result = await process_chat(db=db, message=question, officer=officer,
                                    session_id=session_id, chat_history=[])
        answer = _strip_html(result.get("response") or "").lower()
        missing = [n for n in needles if n.lower() not in answer]
        ok = not missing and result.get("intent") in (
            "compare_applications", "application_status")
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {question}")
        print(f"        {'' if ok else f'missing={missing} '}"
              f"answer={' '.join(answer.split())[:150]}")

    # An officer holding one ward has nothing to compare it against, and must
    # be told that rather than "they are level".
    result = await process_chat(db=db, message="which ward has more applications",
                                officer=officer, session_id=session_id,
                                chat_history=[])
    answer = _strip_html(result.get("response") or "").lower()
    ok = "only one" in answer or "leads" in answer
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  a single-sided comparison says so")
    print(f"        answer={' '.join(answer.split())[:130]}")

    # ── typos reach the same answer as the correct spelling ──────────────
    # Routing alone is not enough: a typo that routes correctly but then picks
    # a different side would be worse than one that fails outright.
    TYPO_PAIRS = [
        (f"which took longer {_A} or {_B}", f"which took longger {_A} or {_B}"),
        (f"compare {_A} and {_B}", f"compair {_A} and {_B}"),
        ("isd vs nisd", "isd vs nsid"),
        ("pending versus approved", "pendng versus approvd"),
        ("compare csc and sub registrar", "compare csc and sub registrer"),
        ("which application took the longest to approve",
         "which application took the longst to approve"),
        ("compare this month and last month",
         "compair this month and last month"),
    ]
    for correct, misspelled in TYPO_PAIRS:
        a = await process_chat(db=db, message=correct, officer=officer,
                               session_id=session_id, chat_history=[])
        b = await process_chat(db=db, message=misspelled, officer=officer,
                               session_id=session_id, chat_history=[])
        ok = (_strip_html(a.get("response") or "").strip()
              == _strip_html(b.get("response") or "").strip()
              and a.get("intent") == "compare_applications")
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  typo answers identically: "
              f"{misspelled[:52]}")
        if not ok:
            print(f"        correct={' '.join(_strip_html(a.get('response') or '').split())[:90]}")
            print(f"        typo   ={' '.join(_strip_html(b.get('response') or '').split())[:90]}")

    # ── a comparison asked as a follow-up ────────────────────────────────
    convo_id = str(uuid.uuid4())
    first = await process_chat(db=db, message=f"compare {_A} and {_B}",
                               officer=officer, session_id=convo_id, chat_history=[])
    history = [{"role": "user", "content": f"compare {_A} and {_B}"},
               {"role": "assistant", "content": first.get("response") or "",
                "intent": first.get("intent")}]
    result = await process_chat(db=db, message="which of these two took longer to approve",
                                officer=officer, session_id=convo_id,
                                chat_history=history)
    answer = _strip_html(result.get("response") or "").lower()
    ok = "took longer" in answer and "68" in answer
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  \"which of these two took longer\" "
          f"resolves against the pair just compared")
    print(f"        answer={' '.join(answer.split())[-120:]}")

    # ── three sides are all reported, not silently trimmed to two ────────
    result = await process_chat(db=db, message="isd vs nisd vs merge",
                                officer=officer, session_id=session_id,
                                chat_history=[])
    answer = _strip_html(result.get("response") or "").lower()
    ok = all(w in answer for w in ("isd", "nisd", "merge"))
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  three sides are all named")
    print(f"        answer={' '.join(answer.split())[:130]}")

    # A comparison against an application the officer cannot see says so
    # instead of inventing half of it.
    result = await process_chat(db=db, message=f"compare {_A} and 2099/0153/28/999999",
                                officer=officer, session_id=session_id, chat_history=[])
    answer = _strip_html(result.get("response") or "").lower()
    ok = "2099/0153/28/999999" in answer and "could not" in answer
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  a side that cannot be read is named, "
          f"not invented")
    print(f"        answer={' '.join(answer.split())[:150]}")
    return failures


async def main_async(routing_only: bool) -> int:
    failures = run_routing()
    if routing_only:
        return failures
    async with AsyncSessionLocal() as db:
        # The officer must be the one who actually holds _A and _B: the answer
        # cases below quote those two application numbers, and an officer who
        # does not hold them is correctly refused, which reads as a failure of
        # the comparison rather than of the fixture. Picking with a bare
        # limit(1) left it to heap order, so a rebuild of the projection could
        # silently hand the test a different officer.
        officer_row = (await db.execute(
            select(SISOfficer)
            .join(Application, Application.assigned_officer_id == SISOfficer.id)
            .where(SISOfficer.is_active.is_(True),
                   Application.application_number == _A)
            .limit(1)
        )).scalars().first()
        if not officer_row:
            officer_row = (await db.execute(
                select(SISOfficer).where(SISOfficer.is_active.is_(True))
                .order_by(SISOfficer.employee_id).limit(1)
            )).scalars().first()
        if not officer_row:
            print("No officer in the database — run the seed first.")
            return failures + 1
        officer = await officer_context(db, officer_row)
        print(f"\nofficer {officer.name} ({officer.employee_id}), "
              f"{officer.jurisdiction_name}")
        failures += await run_data(db, officer, officer_row)
        failures += await run_answers(db, officer)
    return failures


def main() -> int:
    failures = asyncio.run(main_async("--routing" in sys.argv))
    print(f"\n{'ALL PASS' if not failures else f'{failures} FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
