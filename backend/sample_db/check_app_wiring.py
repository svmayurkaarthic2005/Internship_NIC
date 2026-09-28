"""
Smoke test: prove the application answers from sis_chatbot_db.

Connects the way the app does (backend.database -> .env), signs in as a seeded
officer, then runs the real query service and the real chat pipeline. Prints
what came back so the data can be eyeballed against the sample tables.

Run from the project root:
    python -m backend.sample_db.check_app_wiring          # query layer only
    python -m backend.sample_db.check_app_wiring --chat   # also the chatbot (needs Ollama)
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

# Applicant and owner names come out of the extracts in Tamil, and a Windows
# console defaults to cp1252, which cannot encode them. Same guard as main.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select, text

from backend.database import AsyncSessionLocal, get_engine_url
from backend.models import Application, SISOfficer
from backend.schemas import OfficerContext
from backend.services import postgres
from backend.services.auth_service import get_officer_jurisdiction_ids, verify_password

EXPECTED_DB = "sis_chatbot_db"
DEFAULT_PASSWORD = "Test@1234"

CHAT_QUESTIONS = [
    "How many applications are pending with me?",
    "Show me my overdue applications",
    "What is my workload?",
    "What is service code 0154?",
]


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


def show(label, result, width=400):
    print(f"\n--- {label} ---")
    if isinstance(result, dict):
        for k, v in result.items():
            sv = str(v)
            print(f"   {k}: {sv[:width]}{'...' if len(sv) > width else ''}")
    else:
        print("  ", str(result)[:width])


async def main(run_chat: bool) -> int:
    failures = []
    print("engine:", get_engine_url())

    async with AsyncSessionLocal() as db:
        dbname = (await db.execute(text("SELECT current_database()"))).scalar()
        print("connected database:", dbname)
        if dbname != EXPECTED_DB:
            failures.append(f"app is on {dbname}, expected {EXPECTED_DB}")

        # the sample tables and the app tables share this database
        n_sample = (await db.execute(text(
            "SELECT count(*) FROM urban_application_log"))).scalar()
        n_apps = (await db.execute(text("SELECT count(*) FROM applications"))).scalar()
        print(f"urban_application_log rows: {n_sample}   applications rows: {n_apps}")
        if not n_sample or not n_apps:
            failures.append("sample tables or projected applications are empty")

        officer = (await db.execute(
            select(SISOfficer).order_by(SISOfficer.employee_id))).scalars().first()
        if officer is None:
            print("no officers -- run build_app_tables.py first")
            return 1
        print(f"officer: {officer.employee_id} {officer.name} <{officer.email}>")
        if not verify_password(DEFAULT_PASSWORD, officer.password_hash):
            failures.append("seeded officer password does not verify")

        ctx = await officer_context(db, officer)
        print(f"jurisdiction: {ctx.jurisdiction_type} / {ctx.jurisdiction_name}")

        workload = await postgres.get_officer_workload(db, ctx)
        show("get_officer_workload", workload)
        if not workload.get("total_active"):
            failures.append("officer has no active applications")

        pending = await postgres.get_pending_applications(db, ctx)
        show("get_pending_applications", pending)
        if not pending.get("count"):
            failures.append("no pending applications resolved for the officer")

        show("get_overdue_applications", await postgres.get_overdue_applications(db, ctx))

        num = (await db.execute(
            select(Application.application_number)
            .where(Application.assigned_officer_id == officer.id)
            .limit(1))).scalar()
        detail = await postgres.get_application_detail(db, num, ctx)
        show(f"get_application_detail({num})", detail)
        if not detail.get("found"):
            failures.append(f"application {num} not resolvable by its own officer")

        survey = (await db.execute(text(
            "SELECT survey_no FROM survey_numbers LIMIT 1"))).scalar()
        owners = await postgres.get_survey_owners(db, survey, ctx)
        show(f"get_survey_owners({survey})", owners)

        if run_chat:
            from backend.services.chatbot import process_chat
            session = str(uuid.uuid4())
            for q in CHAT_QUESTIONS:
                print("\n" + "=" * 70)
                print("Q:", q)
                try:
                    res = await process_chat(q, session, ctx, db)
                    answer = res.get("response") or res.get("answer") or res
                    print("A:", str(answer)[:600])
                except Exception as exc:                      # noqa: BLE001
                    failures.append(f"chat failed on {q!r}: {exc}")
                    print("ERROR:", type(exc).__name__, exc)

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1
    print("all checks passed -- the app is answering from", EXPECTED_DB)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--chat" in sys.argv)))
