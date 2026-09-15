"""
Fee / service-charge / money questions end to end.

Three layers, so a failure says which one broke:
  1. routing   -- parse_intent puts the question on the right handler
  2. data      -- the DB actually carries the fee record the answer claims
  3. answer    -- process_chat's reply contains the figure from the DB

Run from the project root (Ollama must be up for the answer layer):
    python test_fee_queries.py            # all three layers
    python test_fee_queries.py --fast     # routing + data only, no chat
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select, text

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services.auth_service import get_officer_jurisdiction_ids
from backend.services.postgres import get_fee_summary
from backend.services.rag import parse_intent

OFFICER_EMAIL = "msivakumar@sis.tn.gov.in"      # ward 102, carries most ISD files

# (question, expected intent)
ROUTING_CASES = [
    # per-application fee fields
    ("What is the fee amount for application 2025/0154/28/000286?", "application_status"),
    ("What is the challan number for 2025/0154/28/000286?",         "application_status"),
    ("What is the payment mode for 2024/0154/28/002252?",           "application_status"),
    ("2025/0154/28/000286 kku evvalavu fee?",                       "application_status"),
    ("விண்ணப்பம் 2025/0154/28/000286 கட்டணம் எவ்வளவு?",              "application_status"),
    # the fee schedule
    ("What is the service charge for an ISD application?",          "service_code_guide"),
    ("How much does a NISD application cost?",                      "service_code_guide"),
    ("What is the CSC service charge?",                             "service_code_guide"),
    ("What is the government fee for a MERGE application?",         "service_code_guide"),
    ("Is there any fee difference between ISD and NISD?",           "service_code_guide"),
    ("ISD ku enna fee?",                                            "service_code_guide"),
    # aggregates over the officer's own files
    ("What is the total fee collected from my applications?",       "fee_summary"),
    ("How much money was collected in my jurisdiction?",            "fee_summary"),
    ("Total fee collected for ISD applications",                    "fee_summary"),
    ("Fee collection breakdown by payment mode",                    "fee_summary"),
    ("How much fee did I collect in June 2026?",                    "fee_summary"),
    ("Total challan amount for my files",                           "fee_summary"),
    # money words must not swallow neighbouring intents
    ("Show my pending applications",                                "pending_applications"),
    ("How long has 2026/0154/28/001197 been pending?",              "application_status"),
]

# per-application answers: (question, application number, DB column, how to render it)
FIELD_CASES = [
    ("What is the fee amount for application {app}?",   "2025/0154/28/000286", "fee_amount",     "money"),
    ("What is the challan number for {app}?",           "2025/0154/28/000286", "challan_number", "raw"),
    ("What is the payment mode for {app}?",             "2024/0154/28/002252", "payment_mode",   "raw"),
    ("{app} kku evvalavu fee?",                         "2025/0154/28/000286", "fee_amount",     "money"),
    ("விண்ணப்பம் {app} கட்டணம் எவ்வளவு?",                "2025/0154/28/000286", "fee_amount",     "money"),
]


def money(value) -> str:
    return f"₹{float(value):,.2f}"


async def officer_context(db, officer) -> OfficerContext:
    jur = await get_officer_jurisdiction_ids(officer.id, db)
    ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
           + jur["ward_ids"] + jur["block_ids"])
    return OfficerContext(
        officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
        email=officer.email, designation=officer.designation,
        jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
        jurisdiction_ids=[i for i in ids if i])


def check_routing() -> list[str]:
    print("\n1. ROUTING")
    print("-" * 78)
    failures = []
    for question, expected in ROUTING_CASES:
        got = parse_intent(question)
        ok = got == expected
        print(f"   {'PASS' if ok else 'FAIL'}  {got:22s} {question[:48]}")
        if not ok:
            failures.append(f"routing: {question!r} -> {got}, expected {expected}")
    return failures


async def check_data(db) -> list[str]:
    print("\n2. FEE DATA IN THE DB")
    print("-" * 78)
    failures = []
    row = (await db.execute(text("""
        SELECT count(*)                     AS total,
               count(fee_amount)            AS with_fee,
               count(challan_number)        AS with_challan,
               count(payment_mode)          AS with_mode,
               coalesce(sum(fee_amount), 0) AS total_fee
        FROM applications
    """))).mappings().one()
    print(f"   applications: {row['total']}   with fee: {row['with_fee']}   "
          f"challan: {row['with_challan']}   payment mode: {row['with_mode']}")
    print(f"   total recorded fee: {money(row['total_fee'])}")
    if not row["with_fee"]:
        failures.append("data: no application carries a fee_amount -- rebuild the projection")

    modes = (await db.execute(text(
        "SELECT payment_mode, count(*), coalesce(sum(fee_amount),0) "
        "FROM applications GROUP BY 1 ORDER BY 2 DESC"))).all()
    for mode, count, total in modes:
        print(f"   {str(mode or 'not recorded'):14s} {count:4d}   {money(total)}")
    return failures


async def check_fee_summary(db, ctx) -> list[str]:
    print("\n3. get_fee_summary()")
    print("-" * 78)
    failures = []
    summary = (await get_fee_summary(db, ctx)).get("fee_summary", {})
    print(f"   total applications: {summary.get('total_applications')}   "
          f"with fee: {summary.get('with_fee')}   "
          f"total: {money(summary.get('total_fee') or 0)}")
    for row in summary.get("by_type", []):
        print(f"   {row['application_type']:6s} {row['applications']:4d} apps   "
              f"{money(row['total_fee'])}")
    if not summary.get("total_applications"):
        failures.append("fee summary: officer's jurisdiction resolved to zero applications")
    # the aggregate must agree with a plain SQL sum over the same officer's wards
    if summary.get("by_type"):
        by_type_total = sum(r["total_fee"] for r in summary["by_type"])
        if round(by_type_total, 2) != round(summary.get("total_fee") or 0, 2):
            failures.append(
                f"fee summary: by-type total {by_type_total} != overall {summary.get('total_fee')}")
    by_mode_total = sum(r["total_fee"] for r in summary.get("by_payment_mode", []))
    if summary.get("by_payment_mode") and round(by_mode_total, 2) != round(summary.get("total_fee") or 0, 2):
        failures.append(
            f"fee summary: by-mode total {by_mode_total} != overall {summary.get('total_fee')}")
    return failures


async def check_answers(db, ctx) -> list[str]:
    from backend.services.chatbot import process_chat

    print("\n4. CHAT ANSWERS")
    print("-" * 78)
    failures = []
    session_id = str(uuid.uuid4())

    for template, app_no, column, render in FIELD_CASES:
        question = template.format(app=app_no)
        expected_raw = (await db.execute(text(
            f"SELECT {column} FROM applications WHERE application_number = :n"),
            {"n": app_no})).scalar()
        expected = money(expected_raw) if render == "money" else str(expected_raw)
        answer = str((await process_chat(question, session_id, ctx, db)).get("response") or "")
        ok = expected in answer
        print(f"   {'PASS' if ok else 'FAIL'}  {question[:50]}")
        print(f"         expected {expected!r} -> {answer[:110]!r}")
        if not ok:
            failures.append(f"answer: {question!r} did not contain {expected!r}")

    # the fee schedule must come back as the service-code guide, with the CSC fee
    for question, must_contain in [
        ("What is the service charge for an ISD application?", ["0154", "60"]),
        ("What is the CSC service charge?", ["60"]),
    ]:
        answer = str((await process_chat(question, session_id, ctx, db)).get("response") or "")
        missing = [m for m in must_contain if m not in answer]
        print(f"   {'PASS' if not missing else 'FAIL'}  {question[:50]}")
        if missing:
            print(f"         missing {missing} -> {answer[:110]!r}")
            failures.append(f"answer: {question!r} missing {missing}")

    # the aggregate answer must carry the figure get_fee_summary computed
    summary = (await get_fee_summary(db, ctx)).get("fee_summary", {})
    expected_total = money(summary.get("total_fee") or 0)
    for question in ["What is the total fee collected from my applications?",
                     "Fee collection breakdown by payment mode"]:
        answer = str((await process_chat(question, session_id, ctx, db)).get("response") or "")
        ok = expected_total in answer
        print(f"   {'PASS' if ok else 'FAIL'}  {question[:50]}")
        print(f"         expected {expected_total!r} -> {answer[:110]!r}")
        if not ok:
            failures.append(f"answer: {question!r} did not carry {expected_total!r}")

    return failures


async def main(fast: bool) -> int:
    print("=" * 78)
    print("SIS CHATBOT - SERVICE CHARGE / FEE / MONEY QUERIES")
    print("=" * 78)

    failures = check_routing()

    async with AsyncSessionLocal() as db:
        officer = (await db.execute(
            select(SISOfficer).where(SISOfficer.email == OFFICER_EMAIL))).scalars().first()
        if officer is None:
            print(f"officer {OFFICER_EMAIL} not found -- run build_app_tables.py first")
            return 1
        ctx = await officer_context(db, officer)
        print(f"\nofficer: {officer.name} <{officer.email}> "
              f"({ctx.jurisdiction_type} {ctx.jurisdiction_name})")

        failures += await check_data(db)
        failures += await check_fee_summary(db, ctx)
        if not fast:
            failures += await check_answers(db, ctx)

    print("\n" + "=" * 78)
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1
    print("all fee / money checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--fast" in sys.argv)))
