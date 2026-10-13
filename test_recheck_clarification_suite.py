"""
Comprehensive Regression Test Suite for SIS Chatbot Recheck and Clarification Follow-up
Validates Scenarios A through G, streaming, and error handling.
"""
import asyncio
import os
import sys
import uuid

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.abspath("."))

from backend.database import AsyncSessionLocal
from sqlalchemy import select
from backend.models import SISOfficer, ChatAttachment
from backend.services import attachment_store, doc_extract, attachment_qa
from backend.services.chatbot import process_chat, process_chat_stream, create_chat_session
from backend.schemas import OfficerContext

CSV_A = b"""application_number,service_code,applicant_name
2026/0153/01/0001,0153,Ramesh
2026/0154/01/0002,0154,Suresh
2026/0153/01/0003,0153,Mahesh
"""

CSV_B = b"""application_number,service_code,applicant_name
2026/0154/01/0004,0154,Dinesh
2026/0178/01/0005,0178,Ganesh
"""

CSV_IDENTICAL = b"""application_number,service_code,applicant_name
2026/0153/01/0006,0153,Ramesh
2026/0154/01/0007,0154,Suresh
"""

test_results = []

def record(scenario_name, passed, details=""):
    test_results.append({
        "scenario": scenario_name,
        "passed": passed,
        "details": details
    })
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {scenario_name}: {details}")

async def get_test_officer(db):
    officer = (await db.execute(
        select(SISOfficer).where(SISOfficer.email == "arun_m@sis.tn.gov.in")
    )).scalar_one()
    officer_ctx = OfficerContext(
        officer_id=officer.id,
        employee_id=officer.employee_id,
        name=officer.name,
        email=officer.email,
        designation=officer.designation,
        role=officer.designation,
        jurisdiction_type="ward",
        jurisdiction_name="Ambattur",
        jurisdiction_ids=[officer.id]
    )
    return officer, officer_ctx

async def upload_fixtures(db, officer, session_id):
    ext_a = doc_extract.extract(".csv", CSV_A)
    doc_a = await attachment_store.save_document(
        db=db, officer_id=officer.id, session_id=session_id,
        filename="file_a.csv", ext=".csv", mime_type="text/csv",
        raw=CSV_A, extracted=ext_a
    )
    ext_b = doc_extract.extract(".csv", CSV_B)
    doc_b = await attachment_store.save_document(
        db=db, officer_id=officer.id, session_id=session_id,
        filename="file_b.csv", ext=".csv", mime_type="text/csv",
        raw=CSV_B, extracted=ext_b
    )
    return doc_a, doc_b

# ── Scenario A: Exact screenshot failure ──────────────────────────────────────
async def test_scenario_a():
    async with AsyncSessionLocal() as db:
        officer, officer_ctx = await get_test_officer(db)
        session = await create_chat_session(db, str(officer.id))
        session_id = str(session.id)
        chat_history = []

        # Turn -1: Setup prior service code claim
        res_prior = await process_chat("What is service code 0153?", session_id, officer_ctx, db, chat_history)
        chat_history.append({"role": "user", "content": "What is service code 0153?"})
        chat_history.append({"role": "assistant", "content": res_prior.get("response")})

        # Upload files
        await upload_fixtures(db, officer, session_id)

    # Turn 0: Compare service codes
    async with AsyncSessionLocal() as db:
        res0 = await process_chat("Compare the service codes in both files", session_id, officer_ctx, db, chat_history)
        chat_history.append({"role": "user", "content": "Compare the service codes in both files"})
        chat_history.append({"role": "assistant", "content": res0.get("response")})

    # Turn 1: Disagreement ("I don't think they are the same.")
    async with AsyncSessionLocal() as db:
        res1 = await process_chat("I don't think they are the same.", session_id, officer_ctx, db, chat_history)
        chat_history.append({"role": "user", "content": "I don't think they are the same."})
        chat_history.append({"role": "assistant", "content": res1.get("response")})
        is_clarif = "it's not clear which answer you mean" in res1.get("response")

    # Turn 2: User clarifies ("I don't think the files have the same service codes.")
    async with AsyncSessionLocal() as db:
        res2 = await process_chat("I don't think the files have the same service codes.", session_id, officer_ctx, db, chat_history)
        resp2 = res2.get("response") or ""
        has_table = "| Comparison |" in resp2 and "Unique service codes" in resp2
        has_codes = "0153" in resp2 and "0154" in resp2 and "0178" in resp2
        has_no_loop = "it's not clear which answer you mean" not in resp2 and "misunderstood or provided an inaccurate answer" not in resp2

    passed = is_clarif and has_table and has_codes and has_no_loop
    record("Scenario A (Exact Screenshot Failure)", passed,
           f"Clarified asked: {is_clarif}, Table returned: {has_table}, Grounded codes: {has_codes}, No loop: {has_no_loop}")

# ── Scenario B: Direct correction without clarification ───────────────────────
async def test_scenario_b():
    async with AsyncSessionLocal() as db:
        officer, officer_ctx = await get_test_officer(db)
        session = await create_chat_session(db, str(officer.id))
        session_id = str(session.id)
        chat_history = []
        await upload_fixtures(db, officer, session_id)

    # Turn 0: Compare
    async with AsyncSessionLocal() as db:
        res0 = await process_chat("Compare the service codes in both files", session_id, officer_ctx, db, chat_history)
        chat_history.append({"role": "user", "content": "Compare the service codes in both files"})
        chat_history.append({"role": "assistant", "content": res0.get("response")})

    # Turn 1: Direct correction ("I don't think the files have the same service codes.")
    async with AsyncSessionLocal() as db:
        res1 = await process_chat("I don't think the files have the same service codes.", session_id, officer_ctx, db, chat_history)
        resp1 = res1.get("response") or ""
        has_table = "| Comparison |" in resp1 and "Unique service codes" in resp1
        has_codes = "0153" in resp1 and "0154" in resp1 and "0178" in resp1
        has_discrepancy = "Discrepancy Confirmed" in resp1

    passed = has_table and has_codes and has_discrepancy
    record("Scenario B (Direct Correction)", passed,
           f"Table returned: {has_table}, Grounded codes: {has_codes}, Discrepancy confirmed: {has_discrepancy}")

# ── Scenario C: Explicit correction to service-code definitions ──────────────
async def test_scenario_c():
    async with AsyncSessionLocal() as db:
        officer, officer_ctx = await get_test_officer(db)
        session = await create_chat_session(db, str(officer.id))
        session_id = str(session.id)
        chat_history = []

    async with AsyncSessionLocal() as db:
        res = await process_chat("I don't think the F-Line and F-Line Appeal definitions are correct.", session_id, officer_ctx, db, chat_history)
        resp = res.get("response") or ""
        has_fline = "0178" in resp and "F-Line" in resp
        has_appeal = "0185" in resp and "F-Line Appeal" in resp
        is_statutory = "TAMILNILAM" in resp and "demarcation" in resp.lower()

    passed = has_fline and has_appeal and is_statutory
    record("Scenario C (Explicit Correction to Service Codes)", passed,
           f"Found 0178 F-Line: {has_fline}, Found 0185 Appeal: {has_appeal}, Statutory text: {is_statutory}")

# ── Scenario D: Correction after a topic change ──────────────────────────────
async def test_scenario_d():
    async with AsyncSessionLocal() as db:
        officer, officer_ctx = await get_test_officer(db)
        session = await create_chat_session(db, str(officer.id))
        session_id = str(session.id)
        chat_history = []
        await upload_fixtures(db, officer, session_id)

    # Turn 0: Compare
    async with AsyncSessionLocal() as db:
        res0 = await process_chat("Compare the service codes in both files", session_id, officer_ctx, db, chat_history)
        chat_history.append({"role": "user", "content": "Compare the service codes in both files"})
        chat_history.append({"role": "assistant", "content": res0.get("response")})

    # Turn 1: Unrelated question
    async with AsyncSessionLocal() as db:
        res1 = await process_chat("What is my jurisdiction?", session_id, officer_ctx, db, chat_history)
        chat_history.append({"role": "user", "content": "What is my jurisdiction?"})
        chat_history.append({"role": "assistant", "content": res1.get("response")})

    # Turn 2: Correction about the files
    async with AsyncSessionLocal() as db:
        res2 = await process_chat("I don't think the files have the same service codes.", session_id, officer_ctx, db, chat_history)
        resp2 = res2.get("response") or ""
        has_table = "| Comparison |" in resp2
        has_codes = "0153" in resp2 and "0154" in resp2 and "0178" in resp2

    passed = has_table and has_codes
    record("Scenario D (Correction After Topic Change)", passed,
           f"Table returned: {has_table}, Codes computed: {has_codes}")

# ── Scenario E: Still ambiguous (narrow question without infinite loop) ──────
async def test_scenario_e():
    async with AsyncSessionLocal() as db:
        officer, officer_ctx = await get_test_officer(db)
        session = await create_chat_session(db, str(officer.id))
        session_id = str(session.id)
        chat_history = []

        # Turn -1: Setup prior service code claim
        res_prior = await process_chat("What is service code 0153?", session_id, officer_ctx, db, chat_history)
        chat_history.append({"role": "user", "content": "What is service code 0153?"})
        chat_history.append({"role": "assistant", "content": res_prior.get("response")})

        await upload_fixtures(db, officer, session_id)

    # Turn 0: Compare
    async with AsyncSessionLocal() as db:
        res0 = await process_chat("Compare the service codes in both files", session_id, officer_ctx, db, chat_history)
        chat_history.append({"role": "user", "content": "Compare the service codes in both files"})
        chat_history.append({"role": "assistant", "content": res0.get("response")})

    # Turn 1: Disagreement
    async with AsyncSessionLocal() as db:
        res1 = await process_chat("I don't think they are the same.", session_id, officer_ctx, db, chat_history)
        chat_history.append({"role": "user", "content": "I don't think they are the same."})
        chat_history.append({"role": "assistant", "content": res1.get("response")})

    # Turn 2: Still ambiguous ("I still don't think they are the same.")
    async with AsyncSessionLocal() as db:
        res2 = await process_chat("I still don't think they are the same.", session_id, officer_ctx, db, chat_history)
        resp2 = res2.get("response") or ""
        # Narrowed question with explicit options 1 and 2
        is_narrowed = "1." in resp2 and "2." in resp2 and "uploaded CSV files" in resp2
        chat_history.append({"role": "user", "content": "I still don't think they are the same."})
        chat_history.append({"role": "assistant", "content": resp2})

    # Turn 3: Disagreement again -> Safeguard breaks loop and returns file comparison!
    async with AsyncSessionLocal() as db:
        res3 = await process_chat("neither, recheck again", session_id, officer_ctx, db, chat_history)
        resp3 = res3.get("response") or ""
        safeguard_table = "| Comparison |" in resp3

    passed = is_narrowed and safeguard_table
    record("Scenario E (Still Ambiguous & Safeguard)", passed,
           f"Narrowed options: {is_narrowed}, Safeguard broken loop to table: {safeguard_table}")

# ── Scenario F: Negation and difference filtering ─────────────────────────────
async def test_scenario_f():
    async with AsyncSessionLocal() as db:
        officer, officer_ctx = await get_test_officer(db)
        session = await create_chat_session(db, str(officer.id))
        session_id = str(session.id)
        chat_history = []
        await upload_fixtures(db, officer, session_id)

    # Turn 0: Compare
    async with AsyncSessionLocal() as db:
        res0 = await process_chat("Compare the service codes in both files", session_id, officer_ctx, db, chat_history)
        chat_history.append({"role": "user", "content": "Compare the service codes in both files"})
        chat_history.append({"role": "assistant", "content": res0.get("response")})

    # Turn 1: Filter differences only
    async with AsyncSessionLocal() as db:
        res1 = await process_chat("Don't include matching codes; show only the differences.", session_id, officer_ctx, db, chat_history)
        resp1 = res1.get("response") or ""
        has_diff_header = "Differences" in resp1
        no_common_row = "Codes found in both" not in resp1
        chat_history.append({"role": "user", "content": "Don't include matching codes; show only the differences."})
        chat_history.append({"role": "assistant", "content": resp1})

    # Turn 2: Recheck preserving difference filter
    async with AsyncSessionLocal() as db:
        res2 = await process_chat("I don't think that is correct. Recheck the files.", session_id, officer_ctx, db, chat_history)
        resp2 = res2.get("response") or ""
        preserved_diff_header = "Differences" in resp2
        preserved_no_common = "Codes found in both" not in resp2

    passed = has_diff_header and no_common_row and preserved_diff_header and preserved_no_common
    record("Scenario F (Negation & Difference Filtering)", passed,
           f"Differences filtered: {has_diff_header and no_common_row}, Filter preserved on recheck: {preserved_diff_header and preserved_no_common}")

# ── Scenario G: Tamil and Tanglish ────────────────────────────────────────────
async def test_scenario_g():
    # Tamil test
    async with AsyncSessionLocal() as db:
        officer, officer_ctx = await get_test_officer(db)
        session = await create_chat_session(db, str(officer.id))
        session_id = str(session.id)
        chat_history = []
        await upload_fixtures(db, officer, session_id)

    async with AsyncSessionLocal() as db:
        res_ta = await process_chat("இரண்டு கோப்புகளிலும் ஒரே சேவை குறியீடு இல்லை என்று நினைக்கிறேன்.", session_id, officer_ctx, db, chat_history)
        resp_ta = res_ta.get("response") or ""
        has_ta_table = "| ஒப்பீடு" in resp_ta or "| Comparison" in resp_ta
        has_ta_diff = "முரண்பாடு உறுதி செய்யப்பட்டது" in resp_ta or "Discrepancy" in resp_ta

    # Tanglish test
    async with AsyncSessionLocal() as db:
        session_tg = await create_chat_session(db, str(officer.id))
        session_id_tg = str(session_tg.id)
        chat_history_tg = []
        await upload_fixtures(db, officer, session_id_tg)

    async with AsyncSessionLocal() as db:
        res_tg = await process_chat("files la same service codes illa, recheck pannunga", session_id_tg, officer_ctx, db, chat_history_tg)
        resp_tg = res_tg.get("response") or ""
        has_tg_table = "| Comparison" in resp_tg or "| ஒப்பீடு" in resp_tg
        has_tg_codes = "0153" in resp_tg and "0178" in resp_tg

    passed = (has_ta_table and has_ta_diff) and (has_tg_table and has_tg_codes)
    record("Scenario G (Tamil & Tanglish)", passed,
           f"Tamil table & verdict: {has_ta_table and has_ta_diff}, Tanglish table & codes: {has_tg_table and has_tg_codes}")

# ── Scenario H: Missing or expired attachment handling ───────────────────────
async def test_scenario_h():
    async with AsyncSessionLocal() as db:
        officer, officer_ctx = await get_test_officer(db)
        session = await create_chat_session(db, str(officer.id))
        session_id = str(session.id)
        # Create an expired attachment
        from datetime import datetime, timezone, timedelta
        ext_a = doc_extract.extract(".csv", CSV_A)
        doc_a = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=session_id,
            filename="expired_a.csv", ext=".csv", mime_type="text/csv",
            raw=CSV_A, extracted=ext_a
        )
        doc_a.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        doc_a.is_active = False
        await db.commit()

        # Try to compare with expired doc
        plan = await attachment_qa.compare_csv_service_codes(db, [doc_a], "en")
        passed = "expired or is unavailable" in plan.text

    record("Scenario H (Missing/Expired Attachments)", passed, f"Plan text: '{plan.text}'")

# ── Scenario I: Streaming endpoint verification ──────────────────────────────
async def test_scenario_i():
    async with AsyncSessionLocal() as db:
        officer, officer_ctx = await get_test_officer(db)
        session = await create_chat_session(db, str(officer.id))
        session_id = str(session.id)
        chat_history = []
        await upload_fixtures(db, officer, session_id)

    # Process stream on direct correction
    chunks = []
    async with AsyncSessionLocal() as db:
        async for chunk in process_chat_stream("I don't think the files have the same service codes.", session_id, officer_ctx, db, chat_history):
            chunks.append(chunk.decode('utf-8'))
        
    full_text = "".join(chunks)
    has_stream_table = "| Comparison |" in full_text
    has_stream_codes = "0153" in full_text and "0154" in full_text and "0178" in full_text

    passed = has_stream_table and has_stream_codes
    record("Scenario I (Streaming Endpoint)", passed, f"Streamed table: {has_stream_table}, Streamed codes: {has_stream_codes}")

async def main():
    print("=" * 70)
    print("RUNNING RECHECK & CLARIFICATION COMPREHENSIVE REGRESSION SUITE")
    print("=" * 70)
    await test_scenario_a()
    await test_scenario_b()
    await test_scenario_c()
    await test_scenario_d()
    await test_scenario_e()
    await test_scenario_f()
    await test_scenario_g()
    await test_scenario_h()
    await test_scenario_i()

    print("=" * 70)
    total = len(test_results)
    passed_count = sum(1 for r in test_results if r["passed"])
    print(f"SUMMARY: {passed_count}/{total} PASSED")
    print("=" * 70)
    if passed_count < total:
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
