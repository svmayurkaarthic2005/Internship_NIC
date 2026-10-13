import asyncio, sys, os
sys.path.insert(0, os.path.abspath('.'))
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

async def test():
    async with AsyncSessionLocal() as db:
        officer = (await db.execute(select(SISOfficer).where(SISOfficer.email == 'arun_m@sis.tn.gov.in'))).scalar_one()
        officer_ctx = OfficerContext(officer_id=officer.id, employee_id=officer.employee_id, name=officer.name, email=officer.email, designation=officer.designation, role=officer.designation, jurisdiction_type='ward', jurisdiction_name='Ambattur', jurisdiction_ids=[officer.id])
        session = await create_chat_session(db, str(officer.id))
        session_id = str(session.id)
        chat_history = []
        r_prior = await process_chat('What is service code 0153?', session_id, officer_ctx, db, chat_history)
        chat_history.append({'role': 'user', 'content': 'What is service code 0153?'})
        chat_history.append({'role': 'assistant', 'content': r_prior.get('response')})
        
        ext_a = doc_extract.extract('.csv', CSV_A)
        await attachment_store.save_document(db, officer.id, session_id, 'file_a.csv', '.csv', 'text/csv', CSV_A, ext_a)
        ext_b = doc_extract.extract('.csv', CSV_B)
        await attachment_store.save_document(db, officer.id, session_id, 'file_b.csv', '.csv', 'text/csv', CSV_B, ext_b)

    async with AsyncSessionLocal() as db:
        r0 = await process_chat('Compare the service codes in both files', session_id, officer_ctx, db, chat_history)
        chat_history.append({'role': 'user', 'content': 'Compare the service codes in both files'})
        chat_history.append({'role': 'assistant', 'content': r0.get('response')})

    async with AsyncSessionLocal() as db:
        r1 = await process_chat("I don't think they are the same.", session_id, officer_ctx, db, chat_history)
        print("R1 response:\n", r1.get('response'))

asyncio.run(test())
