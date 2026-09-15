"""Test the "my last / previous application" answers.

Routing and the DB lookup run without Ollama; the answers are rendered
deterministically in chatbot.py, so no LLM is needed here either.

    python test_last_application.py            # routing + DB + answers
    python test_last_application.py --routing  # routing only, no database
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import func, select

from backend.database import AsyncSessionLocal
from backend.models import (Application, ApplicationSubDivision, SISOfficer,
                            SurveyNumber, WorkflowHistory)
from backend.schemas import OfficerContext
from backend.services.auth_service import get_officer_jurisdiction_ids
from backend.services.chatbot import process_chat, process_chat_stream
from backend.services.chatbot import _last_app_field_value
from backend.services.postgres import get_application_detail, get_last_application
from backend.services.rag import parse_intent, parse_last_application_query

# (question, expected intent, expected status filter, expected yes/no form)
ROUTING_CASES = [
    ("what is my prev application is approved", "last_application", "approved", True),
    ("is my previous application approved?", "last_application", "approved", True),
    ("was my last application rejected", "last_application", "rejected", True),
    ("what is my previous application", "last_application", None, False),
    ("show my most recent application", "last_application", None, False),
    ("details of the last application", "last_application", None, False),
    ("my last approved application", "last_application", "approved", False),
    ("which was my last rejected application", "last_application", "rejected", False),
    ("what was the last application i approved", "last_application", "approved", False),
    ("my last isd application", "last_application", None, False),
    ("my previous nisd application", "last_application", None, False),
    ("என் கடைசி விண்ணப்பம்", "last_application", None, False),
    ("கடைசி நிராகரிக்கப்பட்ட விண்ணப்பம்", "last_application", "rejected", False),
    # Must NOT be captured by the new intent.
    ("applications last month", None, None, None),
    ("last 7 days applications", None, None, None),
    ("what is the latest action on 2026/0153/28/000254", None, None, None),
    ("when was my last field visit", None, None, None),
    ("my previous 2 applications", None, None, None),
    ("show pending applications", None, None, None),
    ("status of 2026/0153/28/000254", None, None, None),
]

# (question, expected status filter, expected field)
FIELD_ROUTING_CASES = [
    ("what was the area of my prev application i approved", "approved", "area"),
    ("what is the area of my previous application", None, "area"),
    ("area of my last rejected application", "rejected", "area"),
    ("what is the extent of my last approved application", "approved", "area"),
    ("how much area is in my previous application", None, "area"),
    ("total area of my last isd application", None, "area"),
    ("what is the area of my last pending application", "pending", "area"),
    ("who is the applicant on my previous application", None, "applicant"),
    ("what is the fee on my last approved application", "approved", "fee"),
    ("which survey number was my last rejected application on", "rejected", "survey"),
    ("what is the can number of my previous application", None, "can"),
    ("when was my last approved application submitted", "approved", "submission_date"),
]

AREA_CASES = [
    "what was the area of my prev application i approved",
    "what was the area of my last rejected application",
    "what is the area of my last pending application",
    "what is the area of my previous application",
    "total area of my last isd application",
    "who is the applicant on my previous application",
    "what is the fee on my last approved application",
    "what is the can number of my previous application",
]

CHAT_CASES = [
    "what is my prev application is approved",
    "is my previous application rejected?",
    "what is my previous application",
    "my last approved application",
    "which was my last rejected application",
    "my last isd application",
    "show my most recent application",
]


def run_routing() -> int:
    failures = 0
    print("── Routing ──")
    for question, expected_intent, expected_status, expected_yes_no in ROUTING_CASES:
        intent = parse_intent(question)
        parsed = parse_last_application_query(question)
        if expected_intent is None:
            ok = intent != "last_application" and parsed is None
        else:
            ok = (intent == expected_intent and parsed is not None
                  and parsed["status"] == expected_status
                  and parsed["yes_no"] == expected_yes_no)
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {question}\n        -> {intent} {parsed}")

    print("── Routing: one field of that application ──")
    for question, expected_status, expected_field in FIELD_ROUTING_CASES:
        intent = parse_intent(question)
        parsed = parse_last_application_query(question) or {}
        ok = (intent == "last_application"
              and parsed.get("status") == expected_status
              and parsed.get("field") == expected_field
              # a field question is never a yes/no
              and parsed.get("yes_no") is False)
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {question}\n        -> {intent} {parsed}")
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


async def expected_last(db, officer, status=None, app_type=None):
    """The same answer computed the long way, straight from the two tables."""
    apps = (await db.execute(
        select(Application).where(Application.assigned_officer_id == officer.officer_id)
    )).scalars().all()
    ranked = []
    for app in apps:
        if status and app.current_status != status:
            continue
        if app_type and app.application_type != app_type:
            continue
        last = (await db.execute(
            select(WorkflowHistory.performed_at)
            .where(WorkflowHistory.application_id == app.id)
            .order_by(WorkflowHistory.performed_at.desc()).limit(1)
        )).scalar_one_or_none()
        key = last.date() if last else app.submission_date
        ranked.append((key, app.submission_date, app.application_number))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    return ranked[0][2]


async def run_chat() -> int:
    failures = 0
    async with AsyncSessionLocal() as db:
        officer_row = (await db.execute(
            select(SISOfficer).where(SISOfficer.is_active.is_(True))
            .order_by(SISOfficer.employee_id).limit(1)
        )).scalars().first()
        if not officer_row:
            print("No officer in the database — run the seed first.")
            return 1
        officer = await officer_context(db, officer_row)
        print(f"\n── Data ── officer {officer.name} ({officer.employee_id})")

        for label, kwargs in (("latest overall", {}),
                              ("latest approved", {"status": "approved"}),
                              ("latest rejected", {"status": "rejected"}),
                              ("latest ISD", {"application_type": "ISD"})):
            got = await get_last_application(db, officer, **kwargs)
            want = await expected_last(db, officer,
                                       status=kwargs.get("status"),
                                       app_type=kwargs.get("application_type"))
            got_no = got.get("application_number") if got.get("found") else None
            ok = got_no == want
            failures += 0 if ok else 1
            print(f"  {'PASS' if ok else 'FAIL'}  {label}: {got_no} (expected {want})"
                  f" last_action={got.get('last_action_at')}")

        print("\n── Answers ──")
        session_id = str(uuid.uuid4())
        for question in CHAT_CASES:
            result = await process_chat(db=db, message=question, officer=officer,
                                        session_id=session_id, chat_history=[])
            answer = (result.get("response") or "").strip()
            ok = result.get("intent") == "last_application" and bool(answer)
            # The answer must never ask for an application number.
            if "provide" in answer.lower() and "application number" in answer.lower():
                ok = False
            failures += 0 if ok else 1
            print(f"  {'PASS' if ok else 'FAIL'}  {question}\n        -> {answer}")

        print("\n── Area: the reported figure against the tables it comes from ──")
        for label, kwargs in (("latest overall", {}),
                              ("latest approved", {"status": "approved"}),
                              ("latest rejected", {"status": "rejected"})):
            record = await get_last_application(db, officer, **kwargs)
            if not record.get("found"):
                print(f"  skip  {label}: nothing on record")
                continue
            app = (await db.execute(select(Application).where(
                Application.application_number == record["application_number"]
            ))).scalar_one()
            survey_area = (await db.execute(select(SurveyNumber.total_area_sqm).where(
                SurveyNumber.id == app.survey_number_id))).scalar_one_or_none()
            proposed = [a for a in (await db.execute(
                select(ApplicationSubDivision.proposed_area_sqm)
                .where(ApplicationSubDivision.application_id == app.id)
            )).scalars().all() if a is not None]
            # get_application_detail's documented precedence: a MERGE total wins,
            # then the proposed sub-divisions, then the parcel itself.
            expected = (float(sum(proposed)) if proposed
                        else (float(survey_area) if survey_area is not None else None))
            got = record.get("area_sqm")
            ok = (expected is None and got is None) or (
                got is not None and expected is not None and abs(got - expected) < 0.01)
            failures += 0 if ok else 1
            print(f"  {'PASS' if ok else 'FAIL'}  {label} {record['application_number']}: "
                  f"area_sqm={got} (survey {survey_area}, "
                  f"{len(proposed)} proposed sub-division areas -> expected {expected})")

        print("\n── Area when the application carries its own sub-division areas ──")
        with_subdivisions = (await db.execute(
            select(Application.application_number,
                   func.count(ApplicationSubDivision.id),
                   func.sum(ApplicationSubDivision.proposed_area_sqm))
            .join(ApplicationSubDivision,
                  ApplicationSubDivision.application_id == Application.id)
            .where(Application.assigned_officer_id == officer.officer_id)
            .group_by(Application.application_number)
            .having(func.sum(ApplicationSubDivision.proposed_area_sqm) > 0)
            .limit(3)
        )).all()
        if not with_subdivisions:
            print("  skip  no application of this officer has proposed sub-division areas")
        for app_number, sub_count, proposed_total in with_subdivisions:
            detail = await get_application_detail(db, app_number, officer=officer)
            rendered = _last_app_field_value(detail, "area")
            ok = (rendered is not None
                  and f"{float(proposed_total):,.2f} sq.m" in rendered
                  and "sub-division" in rendered)
            failures += 0 if ok else 1
            print(f"  {'PASS' if ok else 'FAIL'}  {app_number} ({sub_count} sub-divisions, "
                  f"{proposed_total} sq.m)\n        -> {rendered}")

        print("\n── Field answers (area and friends) ──")
        session_id = str(uuid.uuid4())
        for question in AREA_CASES:
            result = await process_chat(db=db, message=question, officer=officer,
                                        session_id=session_id, chat_history=[])
            answer = (result.get("response") or "").strip()
            ok = result.get("intent") == "last_application" and bool(answer)
            if "provide" in answer.lower() and "application number" in answer.lower():
                ok = False
            # An area question must come back with a figure or an explicit
            # "not recorded" -- never with a bare summary that ignores it.
            if "area" in question and not ("sq.m" in answer or "not recorded" in answer
                                           or "no " in answer.lower()):
                ok = False
            failures += 0 if ok else 1
            print(f"  {'PASS' if ok else 'FAIL'}  {question}\n        -> {answer}")

        print("\n── Streaming pipeline (what the frontend actually calls) ──")
        import json as _json
        for question in CHAT_CASES[:4]:
            session_id = str(uuid.uuid4())
            parts = []
            async for raw in process_chat_stream(db=db, message=question, officer=officer,
                                                 session_id=session_id, chat_history=[]):
                for line in raw.decode("utf-8").splitlines():
                    if line.startswith("data: "):
                        payload = _json.loads(line[6:])
                        if payload.get("content"):
                            parts.append(payload["content"])
            answer = "".join(parts).strip()
            asks_for_number = ("application number" in answer.lower()
                               and "provide" in answer.lower())
            ok = bool(answer) and not asks_for_number
            failures += 0 if ok else 1
            print(f"  {'PASS' if ok else 'FAIL'}  {question}\n        -> {answer[:300]}")

        print("\n── Follow-up on the application just named ──")
        session_id = str(uuid.uuid4())
        first = await process_chat(db=db, message="what is my previous application",
                                   officer=officer, session_id=session_id, chat_history=[])
        history = [{"role": "user", "content": "what is my previous application"},
                   {"role": "assistant", "content": first.get("response", "")}]
        for follow_up in ("who is the applicant?", "what is its status?",
                          "which survey number is it on?"):
            result = await process_chat(db=db, message=follow_up, officer=officer,
                                        session_id=session_id, chat_history=history)
            answer = (result.get("response") or "").strip()
            asks_for_number = ("application number" in answer.lower()
                               and "provide" in answer.lower())
            ok = bool(answer) and not asks_for_number
            failures += 0 if ok else 1
            print(f"  {'PASS' if ok else 'FAIL'}  {follow_up}\n        -> {answer[:300]}")
            history += [{"role": "user", "content": follow_up},
                        {"role": "assistant", "content": answer}]
    return failures


async def main() -> int:
    failures = run_routing()
    if "--routing" not in sys.argv:
        failures += await run_chat()
    print(f"\n{'ALL PASSED' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
