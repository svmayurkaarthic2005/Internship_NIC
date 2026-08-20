import asyncio
import sys
import uuid
import json

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services.chatbot import process_chat, process_chat_stream
from backend.services.auth_service import get_officer_jurisdiction_ids
from sqlalchemy import select

async def run_all_tests():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(SISOfficer).where(SISOfficer.id == 'de221b0e-41e6-4389-89a0-fd62e935d1cb'))
        officer_model = res.scalar_one()
        jurisdiction_data = await get_officer_jurisdiction_ids(officer_model.id, db)
        all_jur_ids = (
            jurisdiction_data['district_ids'] +
            jurisdiction_data['taluk_ids'] +
            jurisdiction_data['town_ids'] +
            jurisdiction_data['ward_ids'] +
            jurisdiction_data['block_ids']
        )
        officer = OfficerContext(
            officer_id=officer_model.id,
            employee_id=officer_model.employee_id,
            name=officer_model.name,
            name_tamil=officer_model.name_tamil,
            email=officer_model.email,
            designation=officer_model.designation,
            jurisdiction_type='block',
            jurisdiction_name='Block B1',
            jurisdiction_ids=all_jur_ids,
            district_ids=jurisdiction_data['district_ids'],
            taluk_ids=jurisdiction_data['taluk_ids'],
            town_ids=jurisdiction_data['town_ids'],
            ward_ids=jurisdiction_data['ward_ids'],
            block_ids=jurisdiction_data['block_ids'],
            officer_stage='SIS',
            is_active=officer_model.is_active
        )
        
        tests = [
            ('Test 1: Overdue Applications', 'overdue application', []),
            ('Test 2: Field Visit Tomorrow Morning', 'field visit tomorrow morning', []),
            ('Test 3: Today New Applications', 'today new application received', []),
            ('Test 4: Application Details Check', 'application 2026/0155/02/000022', []),
            ('Test 5: Multi-Turn Continuation (Area Sq & Taluk Code)', 'display Application Date Applicant Name Area sq Taluk Code', [
                {'role': 'user', 'content': 'application 2026/0155/02/000022'},
                {'role': 'assistant', 'content': 'Here are the details for 2026/0155/02/000022'}
            ]),
            ('Test 6: Direct Field Area Question', 'what is the area sq of 2026/0155/02/000022?', []),
            ('Test 7: Direct Field Mobile Question', 'what is the applicant mobile for 2026/0155/02/000022?', []),
            ('Test 8: Tamil Overdue Query', 'காலதாமதமான விண்ணப்பங்கள்', []),
            ('Test 9: Tamil Field Visit Query', 'நாளை கள ஆய்வு உள்ளதா', []),
            ('Test 10: Streaming Multi-Turn Follow-Up', 'display Application Date Applicant Mobile Taluk Code', [
                {'role': 'user', 'content': 'application 2026/0154/02/000007'},
                {'role': 'assistant', 'content': 'Details for 2026/0154/02/000007'}
            ])
        ]
        
        print('====================================================')
        print('      RUNNING COMPREHENSIVE SIS CHATBOT TEST SUITE   ')
        print('====================================================\n')
        
        passed = 0
        failed = 0
        
        for name, query, history in tests:
            print(f'>>> RUNNING: {name}')
            print(f'    Query: "{query}"')
            session_id = str(uuid.uuid4())
            try:
                if 'Streaming' in name:
                    chunks = []
                    async for chunk in process_chat_stream(
                        message=query,
                        session_id=session_id,
                        officer=officer,
                        db=db,
                        chat_history=history
                    ):
                        chunks.append(chunk.decode('utf-8'))
                    resp_text = ''.join(chunks)
                else:
                    res = await process_chat(
                        message=query,
                        session_id=session_id,
                        officer=officer,
                        db=db,
                        chat_history=history
                    )
                    resp_text = res.get('response', '')
                
                # Check for errors or crashes
                if 'error processing your request' in resp_text.lower() or 'unboundlocalerror' in resp_text.lower():
                    print(f'    ❌ FAILED: Error in response: {resp_text[:120]}')
                    failed += 1
                elif not resp_text.strip():
                    print(f'    ❌ FAILED: Empty response')
                    failed += 1
                else:
                    preview = resp_text.replace('\n', ' ')[:110]
                    print(f'    ✅ PASSED: {preview}...')
                    passed += 1
            except Exception as e:
                print(f'    ❌ EXCEPTION: {e}')
                failed += 1
            print()
            
        print('====================================================')
        print(f'  TOTAL: {len(tests)} | PASSED: {passed} | FAILED: {failed}')
        print('====================================================')

if __name__ == '__main__':
    asyncio.run(run_all_tests())
