import asyncio, sys
sys.path.insert(0, r'C:\proj\nic_internship')
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

from backend.models import SISOfficer, OfficerJurisdiction, Application, SurveyNumber, Block
from backend.schemas import OfficerContext
from backend.services.chatbot import process_chat, create_chat_session

DB_URL = "postgresql+asyncpg://postgres:Mayur%402005@127.0.0.1:5432/sis_chatbot_db"

async def main():
    engine = create_async_engine(DB_URL)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        officer = (await db.execute(select(SISOfficer).where(SISOfficer.email == "csenthil@sis.tn.gov.in"))).scalar_one()
        jur = (await db.execute(select(OfficerJurisdiction).where(OfficerJurisdiction.officer_id == officer.id))).scalar_one()
        apps = (await db.execute(
            select(Application)
            .join(SurveyNumber, Application.survey_number_id == SurveyNumber.id)
            .join(Block, SurveyNumber.block_id == Block.id)
            .where(Block.ward_id == jur.ward_id)
            .order_by(Application.submission_date.desc()).limit(3)
        )).scalars().all()
        an = apps[0].application_number
        ctx = OfficerContext(
            officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
            email=officer.email, designation="SIS Officer",
            jurisdiction_type=jur.jurisdiction_type, jurisdiction_name="test",
            jurisdiction_ids=[jur.ward_id] if jur.ward_id else [],
        )
        tests = [
            f"{an} இன் விற்பனை பத்திரம் சரிபார்க்கவும்",
            f"{an} எந்த சார்-பதிவாளர் அலுவலகத்தில் பதிவு செய்யப்பட்டது",
            f"{an} ஏன் நிராகரிக்கப்பட்டது",
            f"{an} இல் புதிய விண்ணப்பம் தாக்கல் செய்ய முடியுமா",
            f"{an} எந்த வழியில் சமர்ப்பிக்கப்பட்டது",
            f"{an} இன் வரலாறு காட்டு",
            "எஸ்கலேஷன் நிலையில் உள்ள விண்ணப்பங்கள் எத்தனை",
            "இன்று எத்தனை விண்ணப்பங்கள் ஒதுக்கப்பட்டன",
            f"{an} இன் CAN எண் வடிவம் என்ன",
        ]
        for i, q in enumerate(tests):
            session = await create_chat_session(db, str(officer.id))
            res = await process_chat(q, str(session.id), ctx, db, chat_history=[])
            print(f"\nQ{i}:", q)
            print("A:", (res.get("response") or "")[:280])
            print("intent:", res.get("intent"))

asyncio.run(main())
