"""CAN number asked about the *previous* question.

"What is the status of <app>?" followed by "what is the CAN number of the
previous question?" must answer for that application, not invent one.

Rewritten from a scratch script that could no longer run: it built an
`OfficerContext` from fields the schema dropped long ago (`role`,
`jurisdiction_level`, `district_code`, ...) with a made-up officer UUID,
passed a session id that is not a UUID, and exited 0 even when its own check
printed FAILED. The officer is now the one who actually holds the application,
resolved from the database, so the jurisdiction check is exercised rather than
bypassed.

    python test_can_context_fix.py
"""
import asyncio
import sys
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import Application, SISOfficer
from backend.schemas import OfficerContext
from backend.services.auth_service import get_officer_jurisdiction_ids
from backend.services.chatbot import process_chat


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


async def main() -> int:
    print("=" * 78)
    print("CAN number asked about the previous question")
    print("=" * 78)

    async with AsyncSessionLocal() as db:
        # Any application will do, but it has to be one whose assigned officer
        # we can sign in as -- otherwise the answer is an access denial and the
        # test proves nothing about context extraction.
        app = (await db.execute(
            select(Application).where(Application.can_number.isnot(None)).limit(1)
        )).scalars().first()
        if app is None:
            print("No application carries a CAN number -- reseed.")
            return 1
        officer_row = (await db.execute(
            select(SISOfficer).where(SISOfficer.id == app.assigned_officer_id)
        )).scalars().first()
        if officer_row is None:
            print(f"{app.application_number} has no assigned officer -- reseed.")
            return 1

        officer = await officer_context(db, officer_row)
        app_no, expected_can = app.application_number, app.can_number

        chat_history = [
            {"role": "user", "content": f"What is the status of application {app_no}?"},
            {"role": "assistant",
             "content": f"Application {app_no} is currently {app.current_status}."},
        ]
        question = "what is the can number of the previous question?"

        print(f"\nOfficer:   {officer.name} ({officer.jurisdiction_type} "
              f"{officer.jurisdiction_name})")
        print(f"Context:   {app_no}   (CAN on record: {expected_can})")
        print(f"Question:  {question}\n")

        result = await process_chat(message=question,
                                    session_id=str(uuid.uuid4()),
                                    officer=officer, db=db,
                                    chat_history=chat_history)

    answer = str(result.get("response") or "")
    print(f"Intent:   {result.get('intent')}")
    print(f"Response: {answer[:300]}\n")

    failures = []
    if app_no not in answer:
        failures.append(f"the answer does not name {app_no} from the context")
    if str(expected_can) not in answer:
        failures.append(f"the answer does not carry the CAN on record ({expected_can})")

    print("=" * 78)
    if failures:
        for f in failures:
            print(f"  FAILED: {f}")
        return 1
    print("  PASS  the CAN number is answered for the application in context")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:  # a crash is a failure, not a pass
        print(f"\nERROR: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
