"""Test the per-application field-visit follow-up ("is field visit scheduled?").

The officer looks at one application, then asks about its field visit without
naming the file again. That question must be answered from that application's
record -- in words, yes or no -- and never fall through to the details card or
to the LLM.

Nothing here needs Ollama: the answers are rendered deterministically.

    python test_field_visit_followups.py            # routing + answers
    python test_field_visit_followups.py --routing  # routing only, no database
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

import logging


def _quiet_sql() -> None:
    from backend.database import engine
    engine.echo = False
    for name in ("sqlalchemy.engine", "sqlalchemy.engine.Engine"):
        logging.getLogger(name).setLevel(logging.WARNING)


# (question, prev_intent, expected intent)
ROUTING_CASES = [
    # Follow-ups on the application just shown -> that application's record.
    ("is field visit scheduled?", "application_status", "application_status"),
    ("when is the field visit", "application_status", "application_status"),
    ("what is the field visit date", "application_status", "application_status"),
    ("has the field visit happened?", "application_status", "application_status"),
    ("what is the applicant name and field visit date", "application_status", "application_status"),
    ("கள ஆய்வு திட்டமிடப்பட்டுள்ளதா?", "application_status", "application_status"),
    ("is field visit scheduled?", "last_application", "application_status"),
    # A definition is still a definition, even sitting on an application.
    # "what is an ISD application" now lands on service_code_lookup rather than
    # general_query: ISD IS service code 0154, so the definition is a table
    # lookup. It used to reach llama3.1:8b, which answered "the application
    # number format is NISD/DISTRICT_CODE/YEAR" -- a format this register has
    # never used. The property under test is unchanged: a definition must not
    # be answered as the application's own record.
    ("what is an ISD application", "application_status", "service_code_lookup"),
    ("what is a patta", "application_status", "general_query"),
    # With no application in view, the field-visit intents still own these.
    ("is field visit scheduled?", None, "field_visits"),
    ("show all field visits this week", "application_status", None),
    ("which applications need scheduling", "application_status", "fv_unassigned_awaiting"),
    ("reschedule the field visit", "application_status", None),
]

FOLLOW_UPS = [
    "is field visit scheduled?",
    "when is the field visit",
    "what is the field visit date",
    "has the field visit happened?",
]


def run_routing() -> int:
    from backend.services.rag import parse_intent
    failures = 0
    print("── Routing ──")
    for question, prev, expected in ROUTING_CASES:
        intent = parse_intent(question, prev_intent=prev)
        # expected None: anything except application_status (the question is a
        # listing / scheduling one, which belongs to the fv_* family).
        ok = (intent == expected) if expected else (intent != "application_status")
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  [prev={prev}] {question}\n        -> {intent}"
              + (f" (expected {expected})" if expected and intent != expected else ""))
    return failures


async def run_answers() -> int:
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import Application, SISOfficer
    from backend.schemas import OfficerContext
    from backend.services.auth_service import get_officer_jurisdiction_ids
    from backend.services.chatbot import process_chat

    _quiet_sql()
    failures = 0
    print("\n── Answers ──")
    async with AsyncSessionLocal() as db:

        async def context_for(app):
            officer = (await db.execute(
                select(SISOfficer).where(SISOfficer.id == app.assigned_officer_id)
            )).scalars().first()
            jur = await get_officer_jurisdiction_ids(officer.id, db)
            ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
                   + jur["ward_ids"] + jur["block_ids"])
            return OfficerContext(
                officer_id=officer.id, employee_id=officer.employee_id,
                name=officer.name, email=officer.email,
                designation=officer.designation,
                jurisdiction_type=jur["jurisdiction_type"],
                jurisdiction_name=jur["jurisdiction_name"],
                jurisdiction_ids=[i for i in ids if i])

        booked = (await db.execute(
            select(Application).where(Application.field_visit_date.isnot(None)).limit(1)
        )).scalars().first()
        unbooked = (await db.execute(
            select(Application).where(Application.field_visit_date.is_(None),
                                      Application.application_type == "ISD").limit(1)
        )).scalars().first()
        if not booked or not unbooked:
            print("  Need one application with and one without a visit -- reseed.")
            return 1

        for app, expect_yes in ((booked, True), (unbooked, False)):
            officer = await context_for(app)
            history = [
                {"role": "user", "content": f"status of {app.application_number}"},
                {"role": "assistant",
                 "content": f"Here are the details for {app.application_number}."},
            ]
            for question in FOLLOW_UPS:
                result = await process_chat(db=db, message=question, officer=officer,
                                            session_id=str(uuid.uuid4()),
                                            chat_history=history)
                answer = (result.get("response") or "").strip()
                lowered = answer.lower()
                ok = (
                    result.get("intent") == "application_status"
                    and app.application_number in answer
                    # a yes/no answer, not the details card and not a table
                    and lowered.startswith("yes" if expect_yes else "no")
                    and "<table" not in lowered
                    and "here are the details" not in lowered
                )
                if expect_yes and str(app.field_visit_date) not in answer:
                    ok = False
                failures += 0 if ok else 1
                print(f"  {'PASS' if ok else 'FAIL'}  [{app.application_number} "
                      f"visit={app.field_visit_date}] {question}\n        -> {answer[:160]}")
    return failures


def main() -> int:
    if "--routing" in set(sys.argv[1:]):
        return run_routing()
    failures = run_routing()
    failures += asyncio.run(run_answers())
    print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
