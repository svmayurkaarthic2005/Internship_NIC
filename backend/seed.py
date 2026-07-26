"""
Seed script for SIS Chatbot Portal
Populates the database with dummy test data
Run: python -m backend.seed
"""
import asyncio
import sys
from datetime import date, datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Add parent directory to path
sys.path.insert(0, '.')

from backend.config import settings
from backend.models import (
    Base, District, Taluk, Town, Ward, Block,
    SurveyNumber, SubDivision, Owner, SurveyOwnership,
    SISOfficer, OfficerJurisdiction, Applicant, Application,
    ApplicationSubDivision, ApplicationDocument, WorkflowHistory,
    FieldVisit, PattaTransfer, Notification
)
from backend.services.auth_service import get_password_hash


async def seed_database():
    """Main seed function"""
    print("[SEED] Starting database seeding...")

    # All dates are relative to today so data always looks realistic
    today = date.today()

    # Import engine from database.py to use Windows-compatible configuration
    from backend.database import engine
    
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("[OK] Database tables created")
    
    # Create session
    AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with AsyncSessionLocal() as db:
        # ========== GEOGRAPHY ==========
        print("[GEO] Seeding geography data...")
        
        # District: Chennai (Code: CHN)
        chennai = District(name="Chennai", district_code="CHN")
        db.add(chennai)
        await db.flush()
        
        # Taluks (Code: District-Taluk)
        ambattur = Taluk(district_id=chennai.id, name="Ambattur", taluk_code="CHN-AMB")
        tambaram = Taluk(district_id=chennai.id, name="Tambaram", taluk_code="CHN-TAM")
        db.add_all([ambattur, tambaram])
        await db.flush()
        
        # Towns (Code: District-Taluk-Town)
        ambattur_town = Town(taluk_id=ambattur.id, name="Ambattur", town_code="AMB-T01")
        tambaram_town = Town(taluk_id=tambaram.id, name="Tambaram", town_code="TAM-T01")
        db.add_all([ambattur_town, tambaram_town])
        await db.flush()
        
        # Wards (Code: Ward number only)
        ward_12 = Ward(town_id=ambattur_town.id, ward_number="12", ward_name="Ward 12")
        ward_15 = Ward(town_id=ambattur_town.id, ward_number="15", ward_name="Ward 15")
        ward_5 = Ward(town_id=tambaram_town.id, ward_number="5", ward_name="Ward 5")
        db.add_all([ward_12, ward_15, ward_5])
        await db.flush()
        
        # Blocks (Code: Block identifier)
        block_b1 = Block(ward_id=ward_12.id, block_number="B1", block_name="Block B1")
        block_b2 = Block(ward_id=ward_12.id, block_number="B2", block_name="Block B2")
        block_b3 = Block(ward_id=ward_15.id, block_number="B3", block_name="Block B3")
        block_b10 = Block(ward_id=ward_5.id, block_number="B10", block_name="Block B10")
        db.add_all([block_b1, block_b2, block_b3, block_b10])
        await db.flush()
        
        print("[OK] Geography data seeded")
        
        # ========== SURVEY NUMBERS & SUB-DIVISIONS ==========
        # NOTE: Each survey number is dedicated to at most 1 active application (synchronous processing rule).
        print("[SURVEY] Seeding survey numbers and sub-divisions...")
        
        # --- Block B1 Surveys (Ambattur Ward 12 / Block B1) ---
        survey_145 = SurveyNumber(block_id=block_b1.id, survey_no="145", total_area_sqm=1950.00, land_type="residential", patta_number="P-145-2020")
        survey_146 = SurveyNumber(block_id=block_b1.id, survey_no="146", total_area_sqm=1200.00, land_type="agricultural", patta_number="P-146-2019")
        survey_147 = SurveyNumber(block_id=block_b1.id, survey_no="147", total_area_sqm=1550.00, land_type="residential", patta_number="P-147-2021")
        survey_148 = SurveyNumber(block_id=block_b1.id, survey_no="148", total_area_sqm=1500.00, land_type="residential", patta_number="P-148-2021")
        survey_149 = SurveyNumber(block_id=block_b1.id, survey_no="149", total_area_sqm=1000.00, land_type="residential", patta_number="P-149-2022")
        survey_150 = SurveyNumber(block_id=block_b1.id, survey_no="150", total_area_sqm=1200.00, land_type="commercial", patta_number="P-150-2023")
        survey_151 = SurveyNumber(block_id=block_b1.id, survey_no="151", total_area_sqm=850.00, land_type="residential", patta_number="P-151-2022")
        survey_152 = SurveyNumber(block_id=block_b1.id, survey_no="152", total_area_sqm=2000.00, land_type="agricultural", patta_number="P-152-2020")
        survey_153 = SurveyNumber(block_id=block_b1.id, survey_no="153", total_area_sqm=700.00, land_type="residential", patta_number="P-153-2021")

        db.add_all([survey_145, survey_146, survey_147, survey_148, survey_149, survey_150, survey_151, survey_152, survey_153])
        await db.flush()
        
        # Sub-divisions for Block B1
        sub_145_1a = SubDivision(survey_number_id=survey_145.id, sub_division_no="145/1A", area_sqm=600.00, status="active")
        sub_145_1b = SubDivision(survey_number_id=survey_145.id, sub_division_no="145/1B", area_sqm=700.00, status="active")
        sub_145_1c = SubDivision(survey_number_id=survey_145.id, sub_division_no="145/1C", area_sqm=650.00, status="active")

        sub_146_1 = SubDivision(survey_number_id=survey_146.id, sub_division_no="146/1", area_sqm=400.00, status="active")
        sub_146_2 = SubDivision(survey_number_id=survey_146.id, sub_division_no="146/2", area_sqm=400.00, status="active")
        sub_146_3 = SubDivision(survey_number_id=survey_146.id, sub_division_no="146/3", area_sqm=400.00, status="active")

        sub_147_1 = SubDivision(survey_number_id=survey_147.id, sub_division_no="147/1", area_sqm=800.00, status="active")
        sub_147_2 = SubDivision(survey_number_id=survey_147.id, sub_division_no="147/2", area_sqm=750.00, status="active")

        sub_148_1a = SubDivision(survey_number_id=survey_148.id, sub_division_no="148/1A", area_sqm=500.00, status="active")
        sub_148_1b = SubDivision(survey_number_id=survey_148.id, sub_division_no="148/1B", area_sqm=600.00, status="active")
        sub_148_1c = SubDivision(survey_number_id=survey_148.id, sub_division_no="148/1C", area_sqm=400.00, status="active")

        sub_149_1 = SubDivision(survey_number_id=survey_149.id, sub_division_no="149/1", area_sqm=450.00, status="active")
        sub_149_2 = SubDivision(survey_number_id=survey_149.id, sub_division_no="149/2", area_sqm=550.00, status="active")

        sub_150_1a = SubDivision(survey_number_id=survey_150.id, sub_division_no="150/1A", area_sqm=350.00, status="active")
        sub_150_1b = SubDivision(survey_number_id=survey_150.id, sub_division_no="150/1B", area_sqm=400.00, status="active")
        sub_150_1c = SubDivision(survey_number_id=survey_150.id, sub_division_no="150/1C", area_sqm=450.00, status="active")

        sub_151_1 = SubDivision(survey_number_id=survey_151.id, sub_division_no="151/1", area_sqm=850.00, status="active")

        sub_152_1 = SubDivision(survey_number_id=survey_152.id, sub_division_no="152/1", area_sqm=1000.00, status="active")
        sub_152_2 = SubDivision(survey_number_id=survey_152.id, sub_division_no="152/2", area_sqm=1000.00, status="active")

        sub_153_1 = SubDivision(survey_number_id=survey_153.id, sub_division_no="153/1", area_sqm=700.00, status="active")

        db.add_all([
            sub_145_1a, sub_145_1b, sub_145_1c,
            sub_146_1, sub_146_2, sub_146_3,
            sub_147_1, sub_147_2,
            sub_148_1a, sub_148_1b, sub_148_1c,
            sub_149_1, sub_149_2,
            sub_150_1a, sub_150_1b, sub_150_1c,
            sub_151_1, sub_152_1, sub_152_2, sub_153_1
        ])
        await db.flush()
        
        # --- Block B2 & B3 Surveys (Ambattur Ward 12 / 15) ---
        survey_200 = SurveyNumber(block_id=block_b2.id, survey_no="200", total_area_sqm=980.00, land_type="commercial", patta_number="P-200-2022")
        survey_201 = SurveyNumber(block_id=block_b2.id, survey_no="201", total_area_sqm=950.00, land_type="residential", patta_number="P-201-2020")
        survey_300 = SurveyNumber(block_id=block_b3.id, survey_no="300", total_area_sqm=1800.00, land_type="agricultural", patta_number="P-300-2018")
        survey_301 = SurveyNumber(block_id=block_b3.id, survey_no="301", total_area_sqm=1500.00, land_type="residential", patta_number="P-301-2021")
        survey_302 = SurveyNumber(block_id=block_b3.id, survey_no="302", total_area_sqm=1200.00, land_type="commercial", patta_number="P-302-2022")

        db.add_all([survey_200, survey_201, survey_300, survey_301, survey_302])
        await db.flush()
        
        sub_200_1 = SubDivision(survey_number_id=survey_200.id, sub_division_no="200/1", area_sqm=500.00, status="active")
        sub_200_2 = SubDivision(survey_number_id=survey_200.id, sub_division_no="200/2", area_sqm=480.00, status="active")
        sub_201_1 = SubDivision(survey_number_id=survey_201.id, sub_division_no="201/1", area_sqm=450.00, status="active")
        sub_201_2 = SubDivision(survey_number_id=survey_201.id, sub_division_no="201/2", area_sqm=500.00, status="active")
        sub_300_1 = SubDivision(survey_number_id=survey_300.id, sub_division_no="300/1", area_sqm=900.00, status="active")
        sub_300_2 = SubDivision(survey_number_id=survey_300.id, sub_division_no="300/2", area_sqm=900.00, status="active")
        sub_301_1 = SubDivision(survey_number_id=survey_301.id, sub_division_no="301/1", area_sqm=750.00, status="active")
        sub_301_2 = SubDivision(survey_number_id=survey_301.id, sub_division_no="301/2", area_sqm=750.00, status="active")
        sub_302_1 = SubDivision(survey_number_id=survey_302.id, sub_division_no="302/1", area_sqm=600.00, status="active")
        sub_302_2 = SubDivision(survey_number_id=survey_302.id, sub_division_no="302/2", area_sqm=600.00, status="active")

        db.add_all([
            sub_200_1, sub_200_2, sub_201_1, sub_201_2,
            sub_300_1, sub_300_2, sub_301_1, sub_301_2,
            sub_302_1, sub_302_2
        ])
        await db.flush()
        
        # --- Block B10 Surveys (Tambaram Ward 5) ---
        survey_500 = SurveyNumber(block_id=block_b10.id, survey_no="500", total_area_sqm=2200.00, land_type="residential", patta_number="P-500-2019")
        survey_501 = SurveyNumber(block_id=block_b10.id, survey_no="501", total_area_sqm=1900.00, land_type="agricultural", patta_number="P-501-2020")
        survey_502 = SurveyNumber(block_id=block_b10.id, survey_no="502", total_area_sqm=1600.00, land_type="commercial", patta_number="P-502-2022")

        db.add_all([survey_500, survey_501, survey_502])
        await db.flush()

        sub_500_1 = SubDivision(survey_number_id=survey_500.id, sub_division_no="500/1", area_sqm=1100.00, status="active")
        sub_500_2 = SubDivision(survey_number_id=survey_500.id, sub_division_no="500/2", area_sqm=1100.00, status="active")
        sub_501_1 = SubDivision(survey_number_id=survey_501.id, sub_division_no="501/1", area_sqm=950.00, status="active")
        sub_501_2 = SubDivision(survey_number_id=survey_501.id, sub_division_no="501/2", area_sqm=950.00, status="active")
        sub_502_1 = SubDivision(survey_number_id=survey_502.id, sub_division_no="502/1", area_sqm=800.00, status="active")
        sub_502_2 = SubDivision(survey_number_id=survey_502.id, sub_division_no="502/2", area_sqm=800.00, status="active")

        db.add_all([sub_500_1, sub_500_2, sub_501_1, sub_501_2, sub_502_1, sub_502_2])
        await db.flush()
        
        print("[OK] Survey numbers seeded")
        
        # ========== OWNERS ==========
        print("[OWNERS] Seeding owners...")
        
        owner_1 = Owner(name="Murugan Rajan", name_tamil="முருகன் ராஜன்", father_name="Rajan Pillai", aadhaar_last4="4521", mobile="9876543210", address="No 12, Gandhi Street, Ambattur, Chennai")
        owner_2 = Owner(name="Kavitha Selvi", name_tamil="கவிதா செல்வி", father_name="Selvaraj Kumar", aadhaar_last4="7823", mobile="9123456789", address="No 45, Nehru Nagar, Ambattur, Chennai")
        owner_3 = Owner(name="Suresh Babu", name_tamil="சுரேஷ் பாபு", father_name="Babu Naidu", aadhaar_last4="9012", mobile="8765432109", address="No 67, Anna Salai, Ambattur, Chennai")
        owner_4 = Owner(name="Ramya Suresh", name_tamil="ரம்யா சுரேஷ்", father_name="Sundaram Raja", aadhaar_last4="3456", mobile="7654321098", address="No 67, Anna Salai, Ambattur, Chennai")
        owner_5 = Owner(name="Bala Krishnan", name_tamil="பாலா கிருஷ்ணன்", father_name="Krishnan Iyer", aadhaar_last4="6789", mobile="9988776655", address="No 89, Periyar Street, Ambattur, Chennai")
        owner_6 = Owner(name="Anbu Chelvan", name_tamil="அன்பு செல்வன்", father_name="Chelvan Mudaliar", aadhaar_last4="2345", mobile="8877665544", address="No 23, Kamarajar Road, Ambattur, Chennai")
        owner_7 = Owner(name="Balaji Raman", name_tamil="பாலாஜி ராமன்", father_name="Ramanathan V", aadhaar_last4="1122", mobile="9840112233", address="No 14, MTH Road, Ambattur, Chennai")
        owner_8 = Owner(name="Karthik Raj", name_tamil="கார்த்திக் ராஜ்", father_name="Rajagopal K", aadhaar_last4="3344", mobile="9840223344", address="No 56, Redhills Road, Ambattur, Chennai")

        db.add_all([owner_1, owner_2, owner_3, owner_4, owner_5, owner_6, owner_7, owner_8])
        await db.flush()
        
        # Survey ownerships
        ownership_1a = SurveyOwnership(survey_number_id=survey_145.id, sub_division_id=sub_145_1a.id, owner_id=owner_1.id, ownership_share=100.00, ownership_type="sole", effective_from=date(2020, 1, 15))
        ownership_1b = SurveyOwnership(survey_number_id=survey_145.id, sub_division_id=sub_145_1b.id, owner_id=owner_2.id, ownership_share=100.00, ownership_type="sole", effective_from=date(2020, 1, 15))
        ownership_1c_1 = SurveyOwnership(survey_number_id=survey_145.id, sub_division_id=sub_145_1c.id, owner_id=owner_3.id, ownership_share=50.00, is_joint_owner=True, ownership_type="joint", effective_from=date(2020, 1, 15))
        ownership_1c_2 = SurveyOwnership(survey_number_id=survey_145.id, sub_division_id=sub_145_1c.id, owner_id=owner_4.id, ownership_share=50.00, is_joint_owner=True, ownership_type="joint", effective_from=date(2020, 1, 15))
        
        ownership_2 = SurveyOwnership(survey_number_id=survey_146.id, owner_id=owner_2.id, ownership_share=100.00, ownership_type="sole", effective_from=date(2019, 5, 20))
        
        ownership_3 = SurveyOwnership(survey_number_id=survey_147.id, sub_division_id=sub_147_1.id, owner_id=owner_3.id, ownership_share=50.00, is_joint_owner=True, ownership_type="joint", effective_from=date(2021, 3, 10))
        ownership_4 = SurveyOwnership(survey_number_id=survey_147.id, sub_division_id=sub_147_1.id, owner_id=owner_4.id, ownership_share=50.00, is_joint_owner=True, ownership_type="joint", effective_from=date(2021, 3, 10))
        ownership_5 = SurveyOwnership(survey_number_id=survey_147.id, sub_division_id=sub_147_2.id, owner_id=owner_5.id, ownership_share=100.00, ownership_type="sole", effective_from=date(2021, 3, 10))

        ownership_48 = SurveyOwnership(survey_number_id=survey_148.id, owner_id=owner_3.id, ownership_share=100.00, ownership_type="sole", effective_from=date(2021, 1, 1))
        ownership_49 = SurveyOwnership(survey_number_id=survey_149.id, owner_id=owner_7.id, ownership_share=100.00, ownership_type="sole", effective_from=date(2022, 2, 1))
        ownership_50 = SurveyOwnership(survey_number_id=survey_150.id, owner_id=owner_8.id, ownership_share=100.00, ownership_type="sole", effective_from=date(2023, 3, 1))

        db.add_all([
            ownership_1a, ownership_1b, ownership_1c_1, ownership_1c_2,
            ownership_2, ownership_3, ownership_4, ownership_5,
            ownership_48, ownership_49, ownership_50
        ])
        await db.flush()
        
        print("[OK] Owners seeded")
        
        # ========== SIS OFFICERS ==========
        print("[OFFICERS] Seeding SIS officers...")
        
        officer_1 = SISOfficer(
            employee_id="SIS-001", name="Arjun Kumar", name_tamil="அர்ஜுன் குமார்",
            email="arjun.kumar@sis.tn.gov.in", password_hash=get_password_hash("Test@1234"),
            mobile="9876501234", designation="Sub Inspector Surveyor", is_active=True
        )
        officer_2 = SISOfficer(
            employee_id="SIS-002", name="Priya Devi", name_tamil="பிரியா தேவி",
            email="priya.devi@sis.tn.gov.in", password_hash=get_password_hash("Test@1234"),
            mobile="9123450678", designation="Sub Inspector Surveyor", is_active=True
        )
        officer_3 = SISOfficer(
            employee_id="SIS-003", name="Ramesh Babu", name_tamil="ரமேஷ் பாபு",
            email="ramesh.babu@sis.tn.gov.in", password_hash=get_password_hash("Test@1234"),
            mobile="8765401234", designation="Sub Inspector Surveyor", is_active=True
        )
        officer_4 = SISOfficer(
            employee_id="SIS-004", name="Lakshmi Narayanan", name_tamil="லட்சுமி நாராயணன்",
            email="lakshmi.narayanan@sis.tn.gov.in", password_hash=get_password_hash("Test@1234"),
            mobile="9988776655", designation="District Sub Inspector Surveyor", is_active=True
        )
        
        db.add_all([officer_1, officer_2, officer_3, officer_4])
        await db.flush()
        
        # Officer jurisdictions
        juris_1 = OfficerJurisdiction(
            officer_id=officer_1.id, jurisdiction_type="block",
            district_id=chennai.id, taluk_id=ambattur.id, town_id=ambattur_town.id,
            ward_id=ward_12.id, block_id=block_b1.id
        )
        juris_2 = OfficerJurisdiction(
            officer_id=officer_2.id, jurisdiction_type="ward",
            district_id=chennai.id, taluk_id=ambattur.id, town_id=ambattur_town.id,
            ward_id=ward_15.id
        )
        juris_3 = OfficerJurisdiction(
            officer_id=officer_3.id, jurisdiction_type="taluk",
            district_id=chennai.id, taluk_id=tambaram.id
        )
        juris_4 = OfficerJurisdiction(
            officer_id=officer_4.id, jurisdiction_type="district",
            district_id=chennai.id
        )
        
        db.add_all([juris_1, juris_2, juris_3, juris_4])
        await db.flush()
        
        print("[OK] SIS officers seeded")
        
        # ========== APPLICANTS & APPLICATIONS ==========
        print("[APPS] Seeding applications (Ensuring 1 application per Survey Number)...")
        
        applicants = []
        for i in range(1, 20):
            applicant = Applicant(
                name=f"Applicant {i}",
                mobile=f"9{i:09d}",
                email=f"applicant{i}@email.com",
                aadhaar_last4=f"{i:04d}",
                address=f"No {i*5}, Gandhi Road, Ambattur, Chennai"
            )
            applicants.append(applicant)
            db.add(applicant)
        await db.flush()
        
        # Application 1: ISD, pending, field visit scheduled (Survey 145)
        app_1 = Application(
            application_number="APP-2024-000001",
            application_type="ISD",
            applicant_id=applicants[0].id,
            survey_number_id=survey_145.id,
            assigned_officer_id=officer_1.id,
            submission_channel="CSC",
            submission_date=today - timedelta(days=5),
            sale_deed_number="SD-2025-1001",
            sale_deed_registered=True,
            declared_reason="sale",
            current_stage="SIS",
            current_status="pending",
            field_visit_date=today + timedelta(days=3),
            field_visit_scheduled=True,
            is_overdue=False,
            priority_flag=False,
            notes="New sub-division requested for Survey 145"
        )

        # Application 2: NISD, forwarded to SD (Survey 146)
        app_2 = Application(
            application_number="APP-2024-000002",
            application_type="NISD",
            applicant_id=applicants[1].id,
            survey_number_id=survey_146.id,
            assigned_officer_id=officer_1.id,
            submission_channel="citizen",
            submission_date=today - timedelta(days=12),
            sale_deed_number="SD-2025-1002",
            sale_deed_registered=True,
            declared_reason="inheritance",
            current_stage="SD",
            current_status="in_progress",
            field_visit_scheduled=False,
            is_overdue=False,
            priority_flag=False,
            notes="Patta transfer without sub-division"
        )

        # Application 3: ISD, at DIS, overdue (Survey 147)
        app_3 = Application(
            application_number="APP-2024-000003",
            application_type="ISD",
            applicant_id=applicants[2].id,
            survey_number_id=survey_147.id,
            assigned_officer_id=officer_1.id,
            submission_channel="sub_registrar",
            submission_date=today - timedelta(days=25),
            sale_deed_number="SD-2025-1003",
            sale_deed_registered=True,
            declared_reason="partition",
            current_stage="DIS",
            current_status="pending",
            field_visit_scheduled=False,
            is_overdue=True,
            priority_flag=True,
            notes="Pending at DIS for 20+ days"
        )

        # Application 4: MERGE, completed (Survey 148)
        app_4 = Application(
            application_number="APP-2024-000004",
            application_type="MERGE",
            applicant_id=applicants[3].id,
            survey_number_id=survey_148.id,
            assigned_officer_id=officer_1.id,
            submission_channel="CSC",
            submission_date=today - timedelta(days=45),
            sale_deed_number="SD-2025-1004",
            sale_deed_registered=True,
            declared_reason="sale",
            current_stage="COMPLETED",
            current_status="approved",
            field_visit_scheduled=False,
            is_overdue=False,
            priority_flag=False,
            notes="Merge of 148/1A (500m²), 148/1B (600m²), 148/1C (400m²) into Survey 148 (Total: 1500m²)"
        )

        # Application 5: ISD, pending at SIS (Survey 200)
        app_5 = Application(
            application_number="APP-2024-000005",
            application_type="ISD",
            applicant_id=applicants[4].id,
            survey_number_id=survey_200.id,
            assigned_officer_id=officer_2.id,
            submission_channel="citizen",
            submission_date=today - timedelta(days=8),
            declared_reason="sale",
            current_stage="SIS",
            current_status="pending",
            field_visit_scheduled=False,
            is_overdue=False,
            priority_flag=False,
            notes="Field visit not yet scheduled"
        )

        # Application 6: NISD, rejected by SD (Survey 201)
        app_6 = Application(
            application_number="APP-2024-000006",
            application_type="NISD",
            applicant_id=applicants[5].id,
            survey_number_id=survey_201.id,
            assigned_officer_id=officer_2.id,
            submission_channel="CSC",
            submission_date=today - timedelta(days=18),
            declared_reason="gift_deed",
            current_stage="REJECTED",
            current_status="rejected",
            field_visit_scheduled=False,
            is_overdue=False,
            priority_flag=False,
            notes="Rejected due to boundary mismatch"
        )

        # Application 7: ISD, pending at SIS (Survey 300)
        app_7 = Application(
            application_number="APP-2024-000007",
            application_type="ISD",
            applicant_id=applicants[6].id,
            survey_number_id=survey_300.id,
            assigned_officer_id=officer_2.id,
            submission_channel="citizen",
            submission_date=today - timedelta(days=6),
            declared_reason="sale",
            current_stage="SIS",
            current_status="pending",
            field_visit_scheduled=False,
            is_overdue=False,
            priority_flag=False
        )

        # Application 8: ISD, pending at SD, high priority (Survey 301)
        app_8 = Application(
            application_number="APP-2024-000008",
            application_type="ISD",
            applicant_id=applicants[7].id,
            survey_number_id=survey_301.id,
            assigned_officer_id=officer_2.id,
            submission_channel="CSC",
            submission_date=today - timedelta(days=10),
            declared_reason="partition",
            current_stage="SD",
            current_status="pending",
            field_visit_scheduled=False,
            is_overdue=False,
            priority_flag=True
        )

        # Application 9: NISD, completed (Survey 500)
        app_9 = Application(
            application_number="APP-2024-000009",
            application_type="NISD",
            applicant_id=applicants[8].id,
            survey_number_id=survey_500.id,
            assigned_officer_id=officer_3.id,
            submission_channel="sub_registrar",
            submission_date=today - timedelta(days=30),
            declared_reason="inheritance",
            current_stage="COMPLETED",
            current_status="approved",
            field_visit_scheduled=False,
            is_overdue=False,
            priority_flag=False
        )

        # Application 10: ISD, pending at Tahsildar (Survey 501)
        app_10 = Application(
            application_number="APP-2024-000010",
            application_type="ISD",
            applicant_id=applicants[9].id,
            survey_number_id=survey_501.id,
            assigned_officer_id=officer_3.id,
            submission_channel="CSC",
            submission_date=today - timedelta(days=20),
            declared_reason="sale",
            current_stage="TAHSILDAR",
            current_status="pending",
            field_visit_scheduled=False,
            is_overdue=False,
            priority_flag=False
        )

        # Application 11: MERGE, pending at SIS (Survey 149)
        app_11 = Application(
            application_number="APP-2024-000011",
            application_type="MERGE",
            applicant_id=applicants[10].id,
            survey_number_id=survey_149.id,
            assigned_officer_id=officer_1.id,
            submission_channel="CSC",
            submission_date=today - timedelta(days=7),
            sale_deed_number="SD-2025-1011",
            sale_deed_registered=True,
            declared_reason="sale",
            current_stage="SIS",
            current_status="pending",
            field_visit_scheduled=False,
            is_overdue=False,
            priority_flag=False,
            notes="Merge of 149/1 (450m²) and 149/2 (550m²) into Survey 149 (Total: 1000m²)"
        )

        # Application 12: MERGE, in_progress at SD (Survey 150)
        app_12 = Application(
            application_number="APP-2024-000012",
            application_type="MERGE",
            applicant_id=applicants[11].id,
            survey_number_id=survey_150.id,
            assigned_officer_id=officer_1.id,
            submission_channel="citizen",
            submission_date=today - timedelta(days=14),
            sale_deed_number="SD-2025-1012",
            sale_deed_registered=True,
            declared_reason="partition",
            current_stage="SD",
            current_status="in_progress",
            field_visit_scheduled=False,
            is_overdue=False,
            priority_flag=False,
            notes="Merge of 150/1A (350m²), 150/1B (400m²), 150/1C (450m²) into Survey 150 (Total: 1200m²)"
        )

        # Application 13: NISD, pending at SIS (Survey 151)
        app_13 = Application(
            application_number="APP-2024-000013",
            application_type="NISD",
            applicant_id=applicants[12].id,
            survey_number_id=survey_151.id,
            assigned_officer_id=officer_1.id,
            submission_channel="sub_registrar",
            submission_date=today - timedelta(days=4),
            sale_deed_number="SD-2025-1013",
            sale_deed_registered=True,
            declared_reason="gift_deed",
            current_stage="SIS",
            current_status="pending",
            field_visit_scheduled=False,
            is_overdue=False,
            priority_flag=False
        )

        # Application 14: ISD, in_progress at SD (Survey 152)
        app_14 = Application(
            application_number="APP-2024-000014",
            application_type="ISD",
            applicant_id=applicants[13].id,
            survey_number_id=survey_152.id,
            assigned_officer_id=officer_1.id,
            submission_channel="CSC",
            submission_date=today - timedelta(days=16),
            sale_deed_number="SD-2025-1014",
            sale_deed_registered=True,
            declared_reason="sale",
            current_stage="SD",
            current_status="in_progress",
            field_visit_scheduled=False,
            is_overdue=False,
            priority_flag=False
        )

        # Application 15: NISD, pending at Tahsildar (Survey 153)
        app_15 = Application(
            application_number="APP-2024-000015",
            application_type="NISD",
            applicant_id=applicants[14].id,
            survey_number_id=survey_153.id,
            assigned_officer_id=officer_1.id,
            submission_channel="citizen",
            submission_date=today - timedelta(days=22),
            sale_deed_number="SD-2025-1015",
            sale_deed_registered=True,
            declared_reason="inheritance",
            current_stage="TAHSILDAR",
            current_status="pending",
            field_visit_scheduled=False,
            is_overdue=False,
            priority_flag=False
        )

        # Application 16: MERGE, in_progress at DIS (Survey 302)
        app_16 = Application(
            application_number="APP-2024-000016",
            application_type="MERGE",
            applicant_id=applicants[15].id,
            survey_number_id=survey_302.id,
            assigned_officer_id=officer_2.id,
            submission_channel="CSC",
            submission_date=today - timedelta(days=18),
            sale_deed_number="SD-2025-1016",
            sale_deed_registered=True,
            declared_reason="sale",
            current_stage="DIS",
            current_status="in_progress",
            field_visit_scheduled=False,
            is_overdue=False,
            priority_flag=False,
            notes="Merge of 302/1 (600m²) and 302/2 (600m²) into Survey 302 (Total: 1200m²)"
        )

        # Application 17: MERGE, completed (Survey 502)
        app_17 = Application(
            application_number="APP-2024-000017",
            application_type="MERGE",
            applicant_id=applicants[16].id,
            survey_number_id=survey_502.id,
            assigned_officer_id=officer_3.id,
            submission_channel="CSC",
            submission_date=today - timedelta(days=35),
            sale_deed_number="SD-2025-1017",
            sale_deed_registered=True,
            declared_reason="sale",
            current_stage="COMPLETED",
            current_status="approved",
            field_visit_scheduled=False,
            is_overdue=False,
            priority_flag=False,
            notes="Merge of 502/1 (800m²) and 502/2 (800m²) into Survey 502 (Total: 1600m²)"
        )

        # ========== OVERDUE ISD APPLICATIONS FOR ALL OFFICERS ==========
        
        # Officer 1 (Arjun Kumar - Block B1): Overdue ISD
        app_18 = Application(
            application_number="APP-2024-000018",
            application_type="ISD",
            applicant_id=applicants[17].id,
            survey_number_id=survey_145.id,
            assigned_officer_id=officer_1.id,
            submission_channel="citizen",
            submission_date=today - timedelta(days=22),
            sale_deed_number="SD-2025-1018",
            sale_deed_registered=True,
            declared_reason="sale",
            current_stage="SIS",
            current_status="pending",
            field_visit_scheduled=True,
            field_visit_date=today - timedelta(days=5),  # Overdue by 5 days
            is_overdue=True,
            priority_flag=True,
            notes="ISD application - Field visit overdue by 5 days"
        )

        # Officer 2 (Priya Devi - Ward 15): Overdue ISD
        app_19 = Application(
            application_number="APP-2024-000019",
            application_type="ISD",
            applicant_id=applicants[18].id,
            survey_number_id=survey_300.id,
            assigned_officer_id=officer_2.id,
            submission_channel="CSC",
            submission_date=today - timedelta(days=28),
            sale_deed_number="SD-2025-1019",
            sale_deed_registered=True,
            declared_reason="partition",
            current_stage="SIS",
            current_status="pending",
            field_visit_scheduled=True,
            field_visit_date=today - timedelta(days=8),  # Overdue by 8 days
            is_overdue=True,
            priority_flag=True,
            notes="ISD application - Field visit overdue by 8 days"
        )

        # Officer 3 (Ramesh Babu - Tambaram Taluk): Overdue ISD
        app_20 = Application(
            application_number="APP-2024-000020",
            application_type="ISD",
            applicant_id=applicants[0].id,
            survey_number_id=survey_500.id,
            assigned_officer_id=officer_3.id,
            submission_channel="sub_registrar",
            submission_date=today - timedelta(days=20),
            sale_deed_number="SD-2025-1020",
            sale_deed_registered=True,
            declared_reason="sale",
            current_stage="SIS",
            current_status="pending",
            field_visit_scheduled=True,
            field_visit_date=today - timedelta(days=3),  # Overdue by 3 days
            is_overdue=True,
            priority_flag=True,
            notes="ISD application - Field visit overdue by 3 days"
        )

        # Officer 4 (Lakshmi Narayanan - District): Overdue ISD
        app_21 = Application(
            application_number="APP-2024-000021",
            application_type="ISD",
            applicant_id=applicants[1].id,
            survey_number_id=survey_146.id,
            assigned_officer_id=officer_4.id,
            submission_channel="citizen",
            submission_date=today - timedelta(days=30),
            sale_deed_number="SD-2025-1021",
            sale_deed_registered=True,
            declared_reason="inheritance",
            current_stage="SIS",
            current_status="pending",
            field_visit_scheduled=True,
            field_visit_date=today - timedelta(days=10),  # Overdue by 10 days
            is_overdue=True,
            priority_flag=True,
            notes="ISD application - Field visit overdue by 10 days"
        )

        # ========== OVERDUE MERGE APPLICATIONS FOR ALL OFFICERS ==========

        # Officer 1 (Arjun Kumar - Block B1): Overdue MERGE
        app_22 = Application(
            application_number="APP-2024-000022",
            application_type="MERGE",
            applicant_id=applicants[2].id,
            survey_number_id=survey_148.id,
            assigned_officer_id=officer_1.id,
            submission_channel="CSC",
            submission_date=today - timedelta(days=26),
            sale_deed_number="SD-2025-1022",
            sale_deed_registered=True,
            declared_reason="sale",
            current_stage="SIS",
            current_status="pending",
            field_visit_scheduled=True,
            field_visit_date=today - timedelta(days=7),  # Overdue by 7 days
            is_overdue=True,
            priority_flag=True,
            notes="MERGE application - Field visit overdue by 7 days"
        )

        # Officer 2 (Priya Devi - Ward 15): Overdue MERGE
        app_23 = Application(
            application_number="APP-2024-000023",
            application_type="MERGE",
            applicant_id=applicants[3].id,
            survey_number_id=survey_301.id,
            assigned_officer_id=officer_2.id,
            submission_channel="citizen",
            submission_date=today - timedelta(days=24),
            sale_deed_number="SD-2025-1023",
            sale_deed_registered=True,
            declared_reason="partition",
            current_stage="SIS",
            current_status="pending",
            field_visit_scheduled=True,
            field_visit_date=today - timedelta(days=6),  # Overdue by 6 days
            is_overdue=True,
            priority_flag=True,
            notes="MERGE application - Field visit overdue by 6 days"
        )

        # Officer 3 (Ramesh Babu - Tambaram Taluk): Overdue MERGE
        app_24 = Application(
            application_number="APP-2024-000024",
            application_type="MERGE",
            applicant_id=applicants[4].id,
            survey_number_id=survey_501.id,
            assigned_officer_id=officer_3.id,
            submission_channel="CSC",
            submission_date=today - timedelta(days=27),
            sale_deed_number="SD-2025-1024",
            sale_deed_registered=True,
            declared_reason="sale",
            current_stage="SIS",
            current_status="pending",
            field_visit_scheduled=True,
            field_visit_date=today - timedelta(days=9),  # Overdue by 9 days
            is_overdue=True,
            priority_flag=True,
            notes="MERGE application - Field visit overdue by 9 days"
        )

        # Officer 4 (Lakshmi Narayanan - District): Overdue MERGE
        app_25 = Application(
            application_number="APP-2024-000025",
            application_type="MERGE",
            applicant_id=applicants[5].id,
            survey_number_id=survey_149.id,
            assigned_officer_id=officer_4.id,
            submission_channel="sub_registrar",
            submission_date=today - timedelta(days=32),
            sale_deed_number="SD-2025-1025",
            sale_deed_registered=True,
            declared_reason="sale",
            current_stage="SIS",
            current_status="pending",
            field_visit_scheduled=True,
            field_visit_date=today - timedelta(days=12),  # Overdue by 12 days
            is_overdue=True,
            priority_flag=True,
            notes="MERGE application - Field visit overdue by 12 days"
        )

        # ========== 2025 APPLICATIONS ==========
        # Create some applications with 2025 submission dates
        from datetime import datetime
        
        # Officer 1 (Arjun Kumar - Block B1): 2025 ISD application
        app_26 = Application(
            application_number="APP-2025-000001",
            application_type="ISD",
            applicant_id=applicants[6].id,
            survey_number_id=survey_145.id,
            assigned_officer_id=officer_1.id,
            submission_channel="citizen",
            submission_date=date(2025, 1, 15),
            sale_deed_number="SD-2025-2001",
            sale_deed_registered=True,
            declared_reason="sale",
            current_stage="SIS",
            current_status="pending",
            field_visit_scheduled=False,
            is_overdue=True,  # Over a year old - definitely overdue
            priority_flag=True,
            notes="2025 ISD application - Block B1"
        )

        # Officer 1 (Arjun Kumar - Block B1): 2025 NISD application
        app_27 = Application(
            application_number="APP-2025-000002",
            application_type="NISD",
            applicant_id=applicants[7].id,
            survey_number_id=survey_146.id,
            assigned_officer_id=officer_1.id,
            submission_channel="CSC",
            submission_date=date(2025, 2, 10),
            sale_deed_number="SD-2025-2002",
            sale_deed_registered=True,
            declared_reason="inheritance",
            current_stage="SIS",
            current_status="pending",
            field_visit_scheduled=False,
            is_overdue=True,  # Over a year old - definitely overdue
            priority_flag=True,
            notes="2025 NISD application - Block B1"
        )

        # Officer 2 (Priya Devi - Ward 15): 2025 MERGE application
        app_28 = Application(
            application_number="APP-2025-000003",
            application_type="MERGE",
            applicant_id=applicants[8].id,
            survey_number_id=survey_301.id,
            assigned_officer_id=officer_2.id,
            submission_channel="sub_registrar",
            submission_date=date(2025, 3, 5),
            sale_deed_number="SD-2025-2003",
            sale_deed_registered=True,
            declared_reason="sale",
            current_stage="SIS",
            current_status="in_progress",
            field_visit_scheduled=True,
            field_visit_date=date(2025, 3, 20),
            is_overdue=True,  # Over a year old - definitely overdue
            priority_flag=True,
            notes="2025 MERGE application - Ward 15"
        )

        # Officer 3 (Ramesh Babu - Tambaram): 2025 ISD application
        app_29 = Application(
            application_number="APP-2025-000004",
            application_type="ISD",
            applicant_id=applicants[9].id,
            survey_number_id=survey_500.id,
            assigned_officer_id=officer_3.id,
            submission_channel="citizen",
            submission_date=date(2025, 1, 25),
            sale_deed_number="SD-2025-2004",
            sale_deed_registered=True,
            declared_reason="partition",
            current_stage="SD",
            current_status="in_progress",
            field_visit_scheduled=False,
            is_overdue=True,  # Over a year old - definitely overdue
            priority_flag=True,
            notes="2025 ISD application - Tambaram Taluk"
        )

        # Officer 4 (Lakshmi - District): 2025 MERGE application
        app_30 = Application(
            application_number="APP-2025-000005",
            application_type="MERGE",
            applicant_id=applicants[10].id,
            survey_number_id=survey_148.id,
            assigned_officer_id=officer_4.id,
            submission_channel="CSC",
            submission_date=date(2025, 2, 20),
            sale_deed_number="SD-2025-2005",
            sale_deed_registered=True,
            declared_reason="sale",
            current_stage="SIS",
            current_status="pending",
            field_visit_scheduled=False,
            is_overdue=True,  # Over a year old - definitely overdue
            priority_flag=True,
            notes="2025 MERGE application - District level"
        )

        db.add_all([
            app_1, app_2, app_3, app_4, app_5, app_6, app_7, app_8, app_9, app_10,
            app_11, app_12, app_13, app_14, app_15, app_16, app_17,
            app_18, app_19, app_20, app_21, app_22, app_23, app_24, app_25,
            app_26, app_27, app_28, app_29, app_30
        ])
        await db.flush()
        print("[OK] Applications seeded (30 applications: 25 from 2024 + 5 from 2025)")
        
        # ========== APPLICATION SUBDIVISIONS ==========
        print("[SUBDIVISIONS] Seeding application sub-divisions...")
        
        # Subdivisions for ISD App 1 (Survey 145)
        asd_1_1 = ApplicationSubDivision(application_id=app_1.id, sub_division_id=sub_145_1a.id, proposed_area_sqm=600.00)
        asd_1_2 = ApplicationSubDivision(application_id=app_1.id, sub_division_id=sub_145_1b.id, proposed_area_sqm=700.00)

        # Subdivisions for ISD App 3 (Survey 147)
        asd_3_1 = ApplicationSubDivision(application_id=app_3.id, sub_division_id=sub_147_1.id, proposed_area_sqm=800.00)
        asd_3_2 = ApplicationSubDivision(application_id=app_3.id, sub_division_id=sub_147_2.id, proposed_area_sqm=750.00)

        # MERGE App 4 (Survey 148): 148/1A (500), 148/1B (600), 148/1C (400)
        asd_4_1 = ApplicationSubDivision(application_id=app_4.id, sub_division_id=sub_148_1a.id, proposed_area_sqm=500.00)
        asd_4_2 = ApplicationSubDivision(application_id=app_4.id, sub_division_id=sub_148_1b.id, proposed_area_sqm=600.00)
        asd_4_3 = ApplicationSubDivision(application_id=app_4.id, sub_division_id=sub_148_1c.id, proposed_area_sqm=400.00)

        # MERGE App 11 (Survey 149): 149/1 (450), 149/2 (550)
        asd_11_1 = ApplicationSubDivision(application_id=app_11.id, sub_division_id=sub_149_1.id, proposed_area_sqm=450.00)
        asd_11_2 = ApplicationSubDivision(application_id=app_11.id, sub_division_id=sub_149_2.id, proposed_area_sqm=550.00)

        # MERGE App 12 (Survey 150): 150/1A (350), 150/1B (400), 150/1C (450)
        asd_12_1 = ApplicationSubDivision(application_id=app_12.id, sub_division_id=sub_150_1a.id, proposed_area_sqm=350.00)
        asd_12_2 = ApplicationSubDivision(application_id=app_12.id, sub_division_id=sub_150_1b.id, proposed_area_sqm=400.00)
        asd_12_3 = ApplicationSubDivision(application_id=app_12.id, sub_division_id=sub_150_1c.id, proposed_area_sqm=450.00)

        # ISD App 14 (Survey 152): 152/1 (1000), 152/2 (1000)
        asd_14_1 = ApplicationSubDivision(application_id=app_14.id, sub_division_id=sub_152_1.id, proposed_area_sqm=1000.00)
        asd_14_2 = ApplicationSubDivision(application_id=app_14.id, sub_division_id=sub_152_2.id, proposed_area_sqm=1000.00)

        # MERGE App 16 (Survey 302): 302/1 (600), 302/2 (600)
        asd_16_1 = ApplicationSubDivision(application_id=app_16.id, sub_division_id=sub_302_1.id, proposed_area_sqm=600.00)
        asd_16_2 = ApplicationSubDivision(application_id=app_16.id, sub_division_id=sub_302_2.id, proposed_area_sqm=600.00)

        # MERGE App 17 (Survey 502): 502/1 (800), 502/2 (800)
        asd_17_1 = ApplicationSubDivision(application_id=app_17.id, sub_division_id=sub_502_1.id, proposed_area_sqm=800.00)
        asd_17_2 = ApplicationSubDivision(application_id=app_17.id, sub_division_id=sub_502_2.id, proposed_area_sqm=800.00)

        # Overdue MERGE applications subdivisions
        asd_22_1 = ApplicationSubDivision(application_id=app_22.id, sub_division_id=sub_148_1a.id, proposed_area_sqm=500.00)
        asd_22_2 = ApplicationSubDivision(application_id=app_22.id, sub_division_id=sub_148_1b.id, proposed_area_sqm=600.00)
        asd_22_3 = ApplicationSubDivision(application_id=app_22.id, sub_division_id=sub_148_1c.id, proposed_area_sqm=400.00)

        asd_23_1 = ApplicationSubDivision(application_id=app_23.id, sub_division_id=sub_301_1.id, proposed_area_sqm=700.00)
        asd_23_2 = ApplicationSubDivision(application_id=app_23.id, sub_division_id=sub_301_2.id, proposed_area_sqm=600.00)

        asd_24_1 = ApplicationSubDivision(application_id=app_24.id, sub_division_id=sub_501_1.id, proposed_area_sqm=600.00)
        asd_24_2 = ApplicationSubDivision(application_id=app_24.id, sub_division_id=sub_501_2.id, proposed_area_sqm=700.00)

        asd_25_1 = ApplicationSubDivision(application_id=app_25.id, sub_division_id=sub_149_1.id, proposed_area_sqm=450.00)
        asd_25_2 = ApplicationSubDivision(application_id=app_25.id, sub_division_id=sub_149_2.id, proposed_area_sqm=550.00)

        db.add_all([
            asd_1_1, asd_1_2,
            asd_3_1, asd_3_2,
            asd_4_1, asd_4_2, asd_4_3,
            asd_11_1, asd_11_2,
            asd_12_1, asd_12_2, asd_12_3,
            asd_14_1, asd_14_2,
            asd_16_1, asd_16_2,
            asd_17_1, asd_17_2,
            # Overdue MERGE applications
            asd_22_1, asd_22_2, asd_22_3,
            asd_23_1, asd_23_2,
            asd_24_1, asd_24_2,
            asd_25_1, asd_25_2
        ])
        await db.flush()
        print("[OK] Application sub-divisions seeded")
        
        # ========== WORKFLOW HISTORY ==========
        print("[WORKFLOW] Seeding workflow history...")
        
        wf_1 = WorkflowHistory(
            application_id=app_1.id, from_stage=None, to_stage="SIS", action="APPLICATION_SUBMITTED",
            performed_by_officer_id=None, remarks="Application submitted via CSC",
            performed_at=datetime.combine(today - timedelta(days=5), datetime.min.time().replace(hour=10, minute=30))
        )
        wf_2 = WorkflowHistory(
            application_id=app_2.id, from_stage=None, to_stage="SIS", action="APPLICATION_SUBMITTED",
            performed_by_officer_id=None, remarks="Application submitted by citizen",
            performed_at=datetime.combine(today - timedelta(days=12), datetime.min.time().replace(hour=9, minute=0))
        )
        wf_3 = WorkflowHistory(
            application_id=app_2.id, from_stage="SIS", to_stage="SD", action="FORWARDED_TO_SD",
            performed_by_officer_id=officer_1.id, remarks="Field verification completed, forwarding to SD",
            performed_at=datetime.combine(today - timedelta(days=8), datetime.min.time().replace(hour=14, minute=30))
        )
        wf_4 = WorkflowHistory(
            application_id=app_4.id, from_stage=None, to_stage="SIS", action="APPLICATION_SUBMITTED",
            performed_by_officer_id=None, remarks="MERGE application submitted via CSC",
            performed_at=datetime.combine(today - timedelta(days=45), datetime.min.time().replace(hour=11, minute=0))
        )
        wf_5 = WorkflowHistory(
            application_id=app_4.id, from_stage="SIS", to_stage="COMPLETED", action="APPROVED",
            performed_by_officer_id=officer_1.id, remarks="Sub-division merge verified and approved. New patta issued.",
            performed_at=datetime.combine(today - timedelta(days=35), datetime.min.time().replace(hour=16, minute=20))
        )
        wf_6 = WorkflowHistory(
            application_id=app_6.id, from_stage="SD", to_stage="REJECTED", action="REJECTED",
            performed_by_officer_id=officer_2.id, remarks="Rejected by SD",
            rejection_reason="Boundary mismatch detected in field verification",
            performed_at=datetime.combine(today - timedelta(days=10), datetime.min.time().replace(hour=11, minute=15))
        )
        wf_7 = WorkflowHistory(
            application_id=app_11.id, from_stage=None, to_stage="SIS", action="APPLICATION_SUBMITTED",
            performed_by_officer_id=None, remarks="MERGE application submitted for Survey 149/1 and 149/2",
            performed_at=datetime.combine(today - timedelta(days=7), datetime.min.time().replace(hour=10, minute=0))
        )
        wf_8 = WorkflowHistory(
            application_id=app_12.id, from_stage="SIS", to_stage="SD", action="FORWARDED_TO_SD",
            performed_by_officer_id=officer_1.id, remarks="Field visit verified, merge plan forwarded to Sub Divisional Officer",
            performed_at=datetime.combine(today - timedelta(days=5), datetime.min.time().replace(hour=15, minute=0))
        )
        
        db.add_all([wf_1, wf_2, wf_3, wf_4, wf_5, wf_6, wf_7, wf_8])
        await db.flush()
        print("[OK] Workflow history seeded")
        
        # ========== FIELD VISITS ==========
        print("[VISITS] Seeding field visits...")
        
        fv_1 = FieldVisit(application_id=app_1.id, officer_id=officer_1.id, scheduled_date=today + timedelta(days=3), status="scheduled", visit_notes=None)
        fv_2 = FieldVisit(application_id=app_3.id, officer_id=officer_1.id, scheduled_date=today - timedelta(days=5), status="overdue", visit_notes="Field visit not completed on scheduled date")
        fv_3 = FieldVisit(application_id=app_5.id, officer_id=officer_2.id, status="unscheduled")
        fv_4 = FieldVisit(application_id=app_11.id, officer_id=officer_1.id, status="unscheduled")
        fv_5 = FieldVisit(application_id=app_12.id, officer_id=officer_1.id, scheduled_date=today - timedelta(days=6), status="completed", visit_notes="Sub-division boundary markers physically verified on ground")

        # Overdue field visits for new ISD applications
        fv_6 = FieldVisit(application_id=app_18.id, officer_id=officer_1.id, scheduled_date=today - timedelta(days=5), status="overdue", visit_notes="ISD - Overdue field visit")
        fv_7 = FieldVisit(application_id=app_19.id, officer_id=officer_2.id, scheduled_date=today - timedelta(days=8), status="overdue", visit_notes="ISD - Overdue field visit")
        fv_8 = FieldVisit(application_id=app_20.id, officer_id=officer_3.id, scheduled_date=today - timedelta(days=3), status="overdue", visit_notes="ISD - Overdue field visit")
        fv_9 = FieldVisit(application_id=app_21.id, officer_id=officer_4.id, scheduled_date=today - timedelta(days=10), status="overdue", visit_notes="ISD - Overdue field visit")

        # Overdue field visits for new MERGE applications
        fv_10 = FieldVisit(application_id=app_22.id, officer_id=officer_1.id, scheduled_date=today - timedelta(days=7), status="overdue", visit_notes="MERGE - Overdue field visit")
        fv_11 = FieldVisit(application_id=app_23.id, officer_id=officer_2.id, scheduled_date=today - timedelta(days=6), status="overdue", visit_notes="MERGE - Overdue field visit")
        fv_12 = FieldVisit(application_id=app_24.id, officer_id=officer_3.id, scheduled_date=today - timedelta(days=9), status="overdue", visit_notes="MERGE - Overdue field visit")
        fv_13 = FieldVisit(application_id=app_25.id, officer_id=officer_4.id, scheduled_date=today - timedelta(days=12), status="overdue", visit_notes="MERGE - Overdue field visit")

        db.add_all([fv_1, fv_2, fv_3, fv_4, fv_5, fv_6, fv_7, fv_8, fv_9, fv_10, fv_11, fv_12, fv_13])
        await db.flush()
        print("[OK] Field visits seeded (13 visits including 8 overdue for all officers)")
        
        # ========== APPLICATION DOCUMENTS ==========
        print("[DOCS] Seeding documents for MERGE, NISD, and ISD applications...")
        
        docs = [
            # App 1 (ISD)
            ApplicationDocument(application_id=app_1.id, document_type="Sale Deed", document_name="sale_deed_145.pdf", is_uploaded=True, is_verified=True, uploaded_at=datetime.combine(today - timedelta(days=5), datetime.min.time().replace(hour=10, minute=35))),
            ApplicationDocument(application_id=app_1.id, document_type="Encumbrance Certificate", document_name="ec_145.pdf", is_uploaded=True, is_verified=False, uploaded_at=datetime.combine(today - timedelta(days=5), datetime.min.time().replace(hour=10, minute=37))),
            ApplicationDocument(application_id=app_1.id, document_type="Sketch", document_name=None, is_uploaded=False, is_verified=False),
            
            # App 2 (NISD)
            ApplicationDocument(application_id=app_2.id, document_type="Sale Deed", document_name="sale_deed_146.pdf", is_uploaded=True, is_verified=True, uploaded_at=datetime.combine(today - timedelta(days=12), datetime.min.time().replace(hour=9, minute=15))),
            ApplicationDocument(application_id=app_2.id, document_type="Patta Copy", document_name="patta_146.pdf", is_uploaded=True, is_verified=True, uploaded_at=datetime.combine(today - timedelta(days=12), datetime.min.time().replace(hour=9, minute=18))),
            ApplicationDocument(application_id=app_2.id, document_type="Encumbrance Certificate", document_name=None, is_uploaded=False, is_verified=False),

            # App 3 (ISD)
            ApplicationDocument(application_id=app_3.id, document_type="Partition Deed", document_name="partition_deed_147.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_3.id, document_type="Encumbrance Certificate", document_name="ec_147.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_3.id, document_type="Sketch", document_name="sketch_147.pdf", is_uploaded=True, is_verified=False),

            # App 4 (MERGE)
            ApplicationDocument(application_id=app_4.id, document_type="Joint Consent Letter", document_name="joint_consent_148.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_4.id, document_type="Sale Deed", document_name="sale_deed_148.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_4.id, document_type="Encumbrance Certificate", document_name="ec_148.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_4.id, document_type="Merge Layout Plan", document_name="merge_plan_148.pdf", is_uploaded=True, is_verified=True),

            # App 11 (MERGE)
            ApplicationDocument(application_id=app_11.id, document_type="Joint Consent Letter", document_name="joint_consent_149.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_11.id, document_type="Sale Deed", document_name="sale_deed_149.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_11.id, document_type="Encumbrance Certificate", document_name="ec_149.pdf", is_uploaded=True, is_verified=False),
            ApplicationDocument(application_id=app_11.id, document_type="Merge Layout Plan", document_name="merge_plan_149.pdf", is_uploaded=True, is_verified=True),

            # App 12 (MERGE)
            ApplicationDocument(application_id=app_12.id, document_type="Joint Consent Letter", document_name="joint_consent_150.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_12.id, document_type="Sale Deed", document_name="sale_deed_150.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_12.id, document_type="Encumbrance Certificate", document_name="ec_150.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_12.id, document_type="Merge Layout Plan", document_name="merge_plan_150.pdf", is_uploaded=True, is_verified=True),

            # App 13 (NISD)
            ApplicationDocument(application_id=app_13.id, document_type="Gift Deed", document_name="gift_deed_151.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_13.id, document_type="Encumbrance Certificate", document_name="ec_151.pdf", is_uploaded=True, is_verified=False),

            # App 14 (ISD)
            ApplicationDocument(application_id=app_14.id, document_type="Sale Deed", document_name="sale_deed_152.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_14.id, document_type="Encumbrance Certificate", document_name="ec_152.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_14.id, document_type="Sketch", document_name="sketch_152.pdf", is_uploaded=True, is_verified=True),

            # App 15 (NISD)
            ApplicationDocument(application_id=app_15.id, document_type="Inheritance Document", document_name="inheritance_153.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_15.id, document_type="Legal Heir Certificate", document_name="legal_heir_153.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_15.id, document_type="Encumbrance Certificate", document_name="ec_153.pdf", is_uploaded=True, is_verified=True),

            # App 16 (MERGE)
            ApplicationDocument(application_id=app_16.id, document_type="Joint Consent Letter", document_name="joint_consent_302.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_16.id, document_type="Sale Deed", document_name="sale_deed_302.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_16.id, document_type="Merge Layout Plan", document_name="merge_plan_302.pdf", is_uploaded=True, is_verified=True),

            # App 17 (MERGE)
            ApplicationDocument(application_id=app_17.id, document_type="Joint Consent Letter", document_name="joint_consent_502.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_17.id, document_type="Sale Deed", document_name="sale_deed_502.pdf", is_uploaded=True, is_verified=True),
            ApplicationDocument(application_id=app_17.id, document_type="Encumbrance Certificate", document_name="ec_502.pdf", is_uploaded=True, is_verified=True)
        ]
        db.add_all(docs)
        await db.flush()
        print("[OK] Documents seeded")
        
        await db.commit()
    
    await engine.dispose()
    print("[DONE] Database seeding completed successfully!")
    print("\n[NOTE] Test Credentials:")
    print("   Email: arjun.kumar@sis.tn.gov.in | Password: Test@1234")
    print("   Email: priya.devi@sis.tn.gov.in | Password: Test@1234")
    print("   Email: ramesh.babu@sis.tn.gov.in | Password: Test@1234")


if __name__ == "__main__":
    asyncio.run(seed_database())
