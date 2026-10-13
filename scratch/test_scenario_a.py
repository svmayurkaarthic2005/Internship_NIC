import asyncio
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.abspath("."))

from backend.database import AsyncSessionLocal
from sqlalchemy import select
from backend.models import SISOfficer
from backend.services import attachment_store, doc_extract
from backend.services.chatbot import process_chat, create_chat_session
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

async def run():
    async with AsyncSessionLocal() as db:
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

        session = await create_chat_session(db, str(officer.id))
        session_id = str(session.id)
        print(f"Created session {session_id}")

        # Prior non-attachment claim: Turn -1: "What is service code 0153?"
        chat_history = []
        res_prior = await process_chat(
            message="What is service code 0153?",
            session_id=session_id,
            officer=officer_ctx,
            db=db,
            chat_history=chat_history
        )
        chat_history.append({"role": "user", "content": "What is service code 0153?"})
        chat_history.append({"role": "assistant", "content": res_prior.get("response")})
        print("Prior Turn Response:", res_prior.get("response")[:60], "...")

        # Upload files
        extracted_a = doc_extract.extract(".csv", CSV_A)
        doc_a = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=session_id,
            filename="file_a.csv", ext=".csv", mime_type="text/csv",
            raw=CSV_A, extracted=extracted_a
        )
        extracted_b = doc_extract.extract(".csv", CSV_B)
        doc_b = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=session_id,
            filename="file_b.csv", ext=".csv", mime_type="text/csv",
            raw=CSV_B, extracted=extracted_b
        )
        print(f"Uploaded {doc_a.filename} and {doc_b.filename}")

    # Turn 0: "Compare the service codes in both files"
    async with AsyncSessionLocal() as db:
        res0 = await process_chat(
            message="Compare the service codes in both files",
            session_id=session_id,
            officer=officer_ctx,
            db=db,
            chat_history=chat_history
        )
        print("\n--- TURN 0 RESPONSE ---")
        print("Response:\n", res0.get("response"))
        chat_history.append({"role": "user", "content": "Compare the service codes in both files"})
        chat_history.append({"role": "assistant", "content": res0.get("response")})

    # Turn 1: "I don't think they are the same."
    async with AsyncSessionLocal() as db:
        res1 = await process_chat(
            message="I don't think they are the same.",
            session_id=session_id,
            officer=officer_ctx,
            db=db,
            chat_history=chat_history
        )
        print("\n--- TURN 1 RESPONSE (Expected clarification question) ---")
        print("Response:\n", res1.get("response"))
        chat_history.append({"role": "user", "content": "I don't think they are the same."})
        chat_history.append({"role": "assistant", "content": res1.get("response")})

    # Turn 2: "I don't think the files have the same service codes."
    async with AsyncSessionLocal() as db:
        res2 = await process_chat(
            message="I don't think the files have the same service codes.",
            session_id=session_id,
            officer=officer_ctx,
            db=db,
            chat_history=chat_history
        )
        print("\n--- TURN 2 RESPONSE (Expected recheck comparison table) ---")
        print("Response:\n", res2.get("response"))
        chat_history.append({"role": "user", "content": "I don't think the files have the same service codes."})
        chat_history.append({"role": "assistant", "content": res2.get("response")})

asyncio.run(run())
