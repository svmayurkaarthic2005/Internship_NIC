"""Test "which application should I field visit next, and where?".

Covers routing (including the questions that must stay with the other
field-visit intents), the plan the database builds, and the answers both chat
pipelines render. No LLM is needed -- the plan is answered deterministically.

    python test_visit_plan_queries.py            # routing + DB + answers
    python test_visit_plan_queries.py --routing  # routing only, no database
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import Application, FieldVisit, SISOfficer
from backend.schemas import OfficerContext
from backend.services.auth_service import get_officer_jurisdiction_ids
from backend.services.chatbot import process_chat, process_chat_stream
from backend.services.postgres import get_visit_plan
from backend.services.rag import extract_date_range, parse_intent, parse_visit_plan_query

# Planning questions: which file to go and inspect, and where.
PLAN_CASES = [
    "which application should i field visit tomorrow",
    "which applications do i have field visit next day",
    "what field visits do i have next week",
    "which application should i visit next week",
    "which block should i go to tomorrow",
    "in which block is my next field visit",
    "which ward should i visit tomorrow",
    "what is my next field visit",
    "which applications are awaiting field visit",
    "which application needs a field visit next",
    "do i have any field visit tomorrow",
    "which survey numbers do i visit next week",
    "plan my field visits for next week by block",
    "which applications in block 0015 need a field visit",
    "which application should i inspect first tomorrow",
    "which applications have no field visit scheduled",
]

# These belong to the other field-visit intents and must not be taken.
KEEP_CASES = [
    ("Can I change the field visit date?", "fv_change_date"),
    ("Can I move the field visit to next week?", "fv_change_date"),
    ("Can I reschedule the inspection myself?", "fv_change_date"),
    ("Who approves a change of field visit date?", "fv_change_date"),
    ("Which field visits are scheduled this week?", "fv_scheduled_this_week"),
    ("Are there any field visit scheduling conflicts?", "fv_scheduling_conflicts"),
    ("Which field inspections are overdue?", "fv_overdue_inspections"),
    ("Which field visits were recently rescheduled?", "fv_recently_rescheduled"),
    ("Which field visits are unassigned and awaiting allocation?", "fv_unassigned_awaiting"),
    # "unscheduled" is the word officers actually use, and it was in none of the
    # cue lists -- every one of them spelled the idea out ("no schedule",
    # "awaiting scheduling"). So "show unscheduled applications" fell through to
    # pending_applications and answered with the whole open queue, and
    # "unscheduled field visits" reached the generic field_visits summary and
    # answered with the entire visit record, most of it already completed.
    ("show unscheduled applications", "fv_unassigned_awaiting"),
    ("unscheduled applications", "fv_unassigned_awaiting"),
    ("list unscheduled applications", "fv_unassigned_awaiting"),
    ("which applications are unscheduled", "fv_unassigned_awaiting"),
    ("show me unscheduled field visits", "fv_unassigned_awaiting"),
    ("show unscheduled", "fv_unassigned_awaiting"),
    ("applications not scheduled", "fv_unassigned_awaiting"),
    ("which are yet to be scheduled", "fv_unassigned_awaiting"),
    ("Are there pending field visits nearby?", "fv_nearby_pending"),
    ("which date should i select for the field visit", "fv_date_select"),
    ("how many field visits do i have", "field_visits"),
    ("Show my field visits", "field_visits"),
    ("show my field visits for next week", "field_visits"),
]

# The periods a plan can be asked for, and the window each must produce.
def window_cases():
    today = date.today()
    monday_next = today + timedelta(days=(7 - today.weekday()))
    return [
        ("which application should i visit tomorrow",
         today + timedelta(days=1), today + timedelta(days=1)),
        ("which application should i visit next day",
         today + timedelta(days=1), today + timedelta(days=1)),
        ("which application should i visit today", today, today),
        ("which applications should i visit next week",
         monday_next, monday_next + timedelta(days=6)),
        ("which applications should i visit coming week",
         monday_next, monday_next + timedelta(days=6)),
        ("what is my next field visit", None, None),
    ]


def run_routing() -> int:
    failures = 0
    print("── Routing: planning questions ──")
    for question in PLAN_CASES:
        intent = parse_intent(question)
        ok = intent == "fv_visit_plan" and parse_visit_plan_query(question) is not None
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {question}\n        -> {intent}")

    print("── Routing: questions the other field-visit intents keep ──")
    for question, expected in KEEP_CASES:
        intent = parse_intent(question)
        ok = intent == expected
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {question}\n        -> {intent} (expected {expected})")

    print("── The period asked for becomes the window ──")
    for question, start, end in window_cases():
        got_start, got_end = extract_date_range(question)
        ok = (got_start, got_end) == (start, end)
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {question}\n        -> {got_start} .. {got_end}")
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


async def check_plan(db, officer) -> int:
    """The plan against the tables it is built from."""
    failures = 0
    today = date.today()
    plan = await get_visit_plan(db, officer)
    if plan.get("error"):
        print(f"  FAIL  get_visit_plan errored: {plan['error']}")
        return 1

    # Every overdue entry is a booked visit whose date has passed.
    for entry in plan["overdue"]:
        scheduled = date.fromisoformat(entry["scheduled_date"])
        ok = scheduled < today and entry["days_late"] == (today - scheduled).days
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  overdue {entry['application_number']} "
              f"scheduled {entry['scheduled_date']}, {entry['days_late']} days late")

    # Nothing in `scheduled` is in the past, and nothing appears twice.
    numbers = [e["application_number"] for e in plan["scheduled"] + plan["overdue"]]
    ok = len(numbers) == len(set(numbers))
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  a visit is listed once: {len(numbers)} entries")

    # Awaiting: active ISD/MERGE with no visit booked, matching the tables.
    booked = {row for row in (await db.execute(
        select(FieldVisit.application_id).where(
            FieldVisit.status.in_(["scheduled", "rescheduled", "completed"]))
    )).scalars().all()}
    expected = set()
    for app in (await db.execute(select(Application).where(
        Application.assigned_officer_id == officer.officer_id
    ))).scalars().all():
        if (app.application_type in ("ISD", "MERGE")
                and app.current_status in ("pending", "in_progress", "escalated")
                and app.id not in booked and not app.field_visit_scheduled):
            expected.add(app.application_number)
    got = {e["application_number"] for e in plan["awaiting"]}
    ok = got == expected
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  awaiting a visit: {sorted(got)} "
          f"(expected {sorted(expected)})")

    # No NISD file is ever proposed for a visit -- its workflow has none.
    nisd = {a.application_number for a in (await db.execute(
        select(Application).where(Application.application_type == "NISD")
    )).scalars().all()}
    ok = not (got & nisd)
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  no NISD application is proposed for a field visit")

    # Overdue files come before the rest, longest-waiting first.
    order = [(not e["is_overdue"], -(e["days_pending"] or 0)) for e in plan["awaiting"]]
    ok = order == sorted(order)
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  awaiting order: overdue first, then oldest")

    # Every recommended entry names where to go, and the counts add up.
    recommended = plan["overdue"] + plan["awaiting"]
    ok = all(e.get("block_number") and e.get("ward_number") for e in recommended)
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  every entry names its ward and block")
    ok = sum(plan["blocks"].values()) == len(recommended) == sum(plan["wards"].values())
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  location counts total {len(recommended)}: "
          f"blocks={plan['blocks']} wards={plan['wards']}")

    # A window only ever narrows what is already booked.
    tomorrow = today + timedelta(days=1)
    narrowed = await get_visit_plan(db, officer, start_date=tomorrow, end_date=tomorrow)
    ok = all(e["scheduled_date"] == tomorrow.isoformat() for e in narrowed["scheduled"])
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  window tomorrow -> "
          f"{len(narrowed['scheduled'])} scheduled, all on {tomorrow}")
    return failures


async def run_chat() -> int:
    failures = 0
    async with AsyncSessionLocal() as db:
        officers = (await db.execute(
            select(SISOfficer).where(SISOfficer.is_active.is_(True))
        )).scalars().all()
        if not officers:
            print("No officer in the database — run the seed first.")
            return 1

        for officer_row in officers:
            officer = await officer_context(db, officer_row)
            print(f"\n── Plan data for {officer.employee_id} {officer.name} ──")
            failures += await check_plan(db, officer)

        officer = await officer_context(db, officers[0])
        print(f"\n── Answers ({officer.employee_id}) ──")
        for question in PLAN_CASES:
            result = await process_chat(db=db, message=question, officer=officer,
                                        session_id=str(uuid.uuid4()), chat_history=[])
            answer = (result.get("response") or "").strip()
            ok = result.get("intent") == "fv_visit_plan" and bool(answer)
            # The point of the intent: never stop at "nothing scheduled", and
            # always say where to go when there is anything to do.
            if "nothing is scheduled" in answer.lower() and len(answer.splitlines()) == 1:
                ok = False
            if "•" in answer and "Where:" not in answer:
                ok = False
            failures += 0 if ok else 1
            print(f"  {'PASS' if ok else 'FAIL'}  {question}\n"
                  + "\n".join(f"        {line}" for line in answer.splitlines()))

        print(f"\n── Streaming pipeline ({officer.employee_id}) ──")
        for question in PLAN_CASES[:4]:
            parts = []
            async for raw in process_chat_stream(db=db, message=question, officer=officer,
                                                 session_id=str(uuid.uuid4()),
                                                 chat_history=[]):
                for line in raw.decode("utf-8").splitlines():
                    if line.startswith("data: "):
                        payload = json.loads(line[6:])
                        if payload.get("content"):
                            parts.append(payload["content"])
            answer = "".join(parts).strip()
            ok = bool(answer) and "•" in answer or "no ISD" in answer
            failures += 0 if ok else 1
            print(f"  {'PASS' if ok else 'FAIL'}  {question}\n        -> "
                  f"{answer.splitlines()[0] if answer else '(empty)'}")
    return failures


async def main() -> int:
    failures = run_routing()
    if "--routing" not in sys.argv:
        failures += await run_chat()
    print(f"\n{'ALL PASSED' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
