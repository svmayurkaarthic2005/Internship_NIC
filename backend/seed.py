"""
Seed script for SIS Chatbot Portal
Populates the database with dummy test data.
Ensures that no two active/pending applications share the same survey number in the same block.

Run: python -m backend.seed
"""
import asyncio
import sys
from datetime import date, datetime, timedelta
from collections import Counter
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

    # Current date for relative seeding
    today = date(2026, 7, 29)

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
        print("[GEO] Seeding geography data for 38 districts...")
        
        all_districts_data = [
            ("01", "Tiruvallur"), ("02", "Chennai"), ("03", "Kancheepuram"), ("04", "Vellore"),
            ("05", "Dharmapuri"), ("06", "Tiruvannamalai"), ("07", "Viluppuram"), ("08", "Salem"),
            ("09", "Namakkal"), ("10", "Erode"), ("11", "Nilgiris"), ("12", "Coimbatore"),
            ("13", "Dindigul"), ("14", "Karur"), ("15", "Tiruchirappalli"), ("16", "Perambalur"),
            ("17", "Ariyalur"), ("18", "Cuddalore"), ("19", "Nagapattinam"), ("20", "Tiruvarur"),
            ("21", "Thanjavur"), ("22", "Pudukkottai"), ("23", "Sivagangai"), ("24", "Madurai"),
            ("25", "Theni"), ("26", "Virudhunagar"), ("27", "Ramanathapuram"), ("28", "Thoothukudi"),
            ("29", "Tirunelveli"), ("30", "Kanniyakumari"), ("31", "Krishnagiri"), ("32", "Tiruppur"),
            ("33", "Kallakurichi"), ("34", "Chengalpattu"), ("35", "Ranipet"), ("36", "Tirupathur"),
            ("37", "Tenkasi"), ("38", "Mayiladuthurai")
        ]
        
        district_objs = {}
        for d_code, d_name in all_districts_data:
            d_obj = District(name=d_name, district_code=d_code)
            db.add(d_obj)
            district_objs[d_code] = d_obj
        
        await db.flush()
        chennai = district_objs["02"]

        # Taluks
        ambattur = Taluk(district_id=chennai.id, name="Ambattur", taluk_code="CHN-AMB")
        tambaram = Taluk(district_id=chennai.id, name="Tambaram", taluk_code="CHN-TAM")
        db.add_all([ambattur, tambaram])
        await db.flush()
        
        # Towns
        ambattur_town = Town(taluk_id=ambattur.id, name="Ambattur", town_code="AMB-T01")
        tambaram_town = Town(taluk_id=tambaram.id, name="Tambaram", town_code="TAM-T01")
        db.add_all([ambattur_town, tambaram_town])
        await db.flush()
        
        # Wards
        ward_12 = Ward(town_id=ambattur_town.id, ward_number="12", ward_name="Ward 12")
        ward_15 = Ward(town_id=ambattur_town.id, ward_number="15", ward_name="Ward 15")
        ward_5 = Ward(town_id=tambaram_town.id, ward_number="5", ward_name="Ward 5")
        db.add_all([ward_12, ward_15, ward_5])
        await db.flush()
        
        # Blocks
        block_b1 = Block(ward_id=ward_12.id, block_number="B1", block_name="Block B1")
        block_b2 = Block(ward_id=ward_12.id, block_number="B2", block_name="Block B2")
        block_b3 = Block(ward_id=ward_15.id, block_number="B3", block_name="Block B3")
        block_b10 = Block(ward_id=ward_5.id, block_number="B10", block_name="Block B10")
        db.add_all([block_b1, block_b2, block_b3, block_b10])
        await db.flush()
        
        print("[OK] Geography data seeded")
        
        # ========== SURVEY NUMBERS & SUB-DIVISIONS ==========
        # NOTE: Each survey number is dedicated to at most 1 active application.
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
        survey_154 = SurveyNumber(block_id=block_b1.id, survey_no="154", total_area_sqm=1100.00, land_type="residential", patta_number="P-154-2022")
        survey_155 = SurveyNumber(block_id=block_b1.id, survey_no="155", total_area_sqm=1400.00, land_type="commercial", patta_number="P-155-2023")
        survey_156 = SurveyNumber(block_id=block_b1.id, survey_no="156", total_area_sqm=900.00, land_type="residential", patta_number="P-156-2022")
        survey_157 = SurveyNumber(block_id=block_b1.id, survey_no="157", total_area_sqm=750.00, land_type="agricultural", patta_number="P-157-2021")
        survey_158 = SurveyNumber(block_id=block_b1.id, survey_no="158", total_area_sqm=1300.00, land_type="residential", patta_number="P-158-2026")
        survey_159 = SurveyNumber(block_id=block_b1.id, survey_no="159", total_area_sqm=1600.00, land_type="commercial", patta_number="P-159-2026")
        survey_160 = SurveyNumber(block_id=block_b1.id, survey_no="160", total_area_sqm=800.00, land_type="residential", patta_number="P-160-2026")
        survey_161 = SurveyNumber(block_id=block_b1.id, survey_no="161", total_area_sqm=1050.00, land_type="residential", patta_number="P-161-2026")
        survey_162 = SurveyNumber(block_id=block_b1.id, survey_no="162", total_area_sqm=1750.00, land_type="commercial", patta_number="P-162-2026")

        db.add_all([
            survey_145, survey_146, survey_147, survey_148, survey_149, survey_150,
            survey_151, survey_152, survey_153, survey_154, survey_155, survey_156,
            survey_157, survey_158, survey_159, survey_160, survey_161, survey_162
        ])
        await db.flush()
        
        # Sub-divisions for Block B1
        sub_145_1a = SubDivision(survey_number_id=survey_145.id, sub_division_no="145/1A", area_sqm=600.00, status="active")
        sub_145_1b = SubDivision(survey_number_id=survey_145.id, sub_division_no="145/1B", area_sqm=700.00, status="active")
        sub_145_1c = SubDivision(survey_number_id=survey_145.id, sub_division_no="145/1C", area_sqm=650.00, status="active")

        sub_146_1 = SubDivision(survey_number_id=survey_146.id, sub_division_no="146/1", area_sqm=400.00, status="active")
        sub_146_2 = SubDivision(survey_number_id=survey_146.id, sub_division_no="146/2", area_sqm=400.00, status="active")

        sub_147_1 = SubDivision(survey_number_id=survey_147.id, sub_division_no="147/1", area_sqm=800.00, status="active")
        sub_147_2 = SubDivision(survey_number_id=survey_147.id, sub_division_no="147/2", area_sqm=750.00, status="active")

        sub_148_1a = SubDivision(survey_number_id=survey_148.id, sub_division_no="148/1A", area_sqm=500.00, status="active")
        sub_148_1b = SubDivision(survey_number_id=survey_148.id, sub_division_no="148/1B", area_sqm=600.00, status="active")
        sub_148_1c = SubDivision(survey_number_id=survey_148.id, sub_division_no="148/1C", area_sqm=400.00, status="active")

        sub_149_1 = SubDivision(survey_number_id=survey_149.id, sub_division_no="149/1", area_sqm=450.00, status="active")
        sub_149_2 = SubDivision(survey_number_id=survey_149.id, sub_division_no="149/2", area_sqm=550.00, status="active")

        sub_150_1a = SubDivision(survey_number_id=survey_150.id, sub_division_no="150/1A", area_sqm=350.00, status="active")
        sub_150_1b = SubDivision(survey_number_id=survey_150.id, sub_division_no="150/1B", area_sqm=400.00, status="active")

        sub_151_1 = SubDivision(survey_number_id=survey_151.id, sub_division_no="151/1", area_sqm=850.00, status="active")
        sub_152_1 = SubDivision(survey_number_id=survey_152.id, sub_division_no="152/1", area_sqm=1000.00, status="active")
        sub_153_1 = SubDivision(survey_number_id=survey_153.id, sub_division_no="153/1", area_sqm=700.00, status="active")
        sub_154_1 = SubDivision(survey_number_id=survey_154.id, sub_division_no="154/1", area_sqm=550.00, status="active")
        sub_154_2 = SubDivision(survey_number_id=survey_154.id, sub_division_no="154/2", area_sqm=550.00, status="active")
        sub_155_1a = SubDivision(survey_number_id=survey_155.id, sub_division_no="155/1A", area_sqm=700.00, status="active")
        sub_155_1b = SubDivision(survey_number_id=survey_155.id, sub_division_no="155/1B", area_sqm=700.00, status="active")
        sub_156_1 = SubDivision(survey_number_id=survey_156.id, sub_division_no="156/1", area_sqm=450.00, status="active")
        sub_157_1 = SubDivision(survey_number_id=survey_157.id, sub_division_no="157/1", area_sqm=750.00, status="active")
        sub_158_1 = SubDivision(survey_number_id=survey_158.id, sub_division_no="158/1", area_sqm=650.00, status="active")
        sub_158_2 = SubDivision(survey_number_id=survey_158.id, sub_division_no="158/2", area_sqm=650.00, status="active")
        sub_159_1a = SubDivision(survey_number_id=survey_159.id, sub_division_no="159/1A", area_sqm=800.00, status="active")
        sub_159_1b = SubDivision(survey_number_id=survey_159.id, sub_division_no="159/1B", area_sqm=800.00, status="active")
        sub_160_1 = SubDivision(survey_number_id=survey_160.id, sub_division_no="160/1", area_sqm=800.00, status="active")
        sub_161_1 = SubDivision(survey_number_id=survey_161.id, sub_division_no="161/1", area_sqm=525.00, status="active")
        sub_161_2 = SubDivision(survey_number_id=survey_161.id, sub_division_no="161/2", area_sqm=525.00, status="active")
        sub_162_1a = SubDivision(survey_number_id=survey_162.id, sub_division_no="162/1A", area_sqm=875.00, status="active")
        sub_162_1b = SubDivision(survey_number_id=survey_162.id, sub_division_no="162/1B", area_sqm=875.00, status="active")

        db.add_all([
            sub_145_1a, sub_145_1b, sub_145_1c, sub_146_1, sub_146_2, sub_147_1, sub_147_2,
            sub_148_1a, sub_148_1b, sub_148_1c, sub_149_1, sub_149_2, sub_150_1a, sub_150_1b,
            sub_151_1, sub_152_1, sub_153_1, sub_154_1, sub_154_2, sub_155_1a, sub_155_1b,
            sub_156_1, sub_157_1, sub_158_1, sub_158_2, sub_159_1a, sub_159_1b, sub_160_1,
            sub_161_1, sub_161_2, sub_162_1a, sub_162_1b
        ])
        await db.flush()

        # --- Block B2 & B3 Surveys (Ambattur Ward 12 / 15) ---
        survey_200 = SurveyNumber(block_id=block_b2.id, survey_no="200", total_area_sqm=980.00, land_type="commercial", patta_number="P-200-2022")
        survey_201 = SurveyNumber(block_id=block_b2.id, survey_no="201", total_area_sqm=950.00, land_type="residential", patta_number="P-201-2020")
        
        survey_300 = SurveyNumber(block_id=block_b3.id, survey_no="300", total_area_sqm=1800.00, land_type="agricultural", patta_number="P-300-2018")
        survey_301 = SurveyNumber(block_id=block_b3.id, survey_no="301", total_area_sqm=1500.00, land_type="residential", patta_number="P-301-2021")
        survey_302 = SurveyNumber(block_id=block_b3.id, survey_no="302", total_area_sqm=1200.00, land_type="commercial", patta_number="P-302-2022")
        survey_303 = SurveyNumber(block_id=block_b3.id, survey_no="303", total_area_sqm=1100.00, land_type="residential", patta_number="P-303-2023")
        survey_304 = SurveyNumber(block_id=block_b3.id, survey_no="304", total_area_sqm=1350.00, land_type="commercial", patta_number="P-304-2024")
        survey_305 = SurveyNumber(block_id=block_b3.id, survey_no="305", total_area_sqm=1250.00, land_type="residential", patta_number="P-305-2025")
        survey_306 = SurveyNumber(block_id=block_b3.id, survey_no="306", total_area_sqm=1450.00, land_type="residential", patta_number="P-306-2026")

        db.add_all([survey_200, survey_201, survey_300, survey_301, survey_302, survey_303, survey_304, survey_305, survey_306])
        await db.flush()

        sub_200_1 = SubDivision(survey_number_id=survey_200.id, sub_division_no="200/1", area_sqm=500.00, status="active")
        sub_201_1 = SubDivision(survey_number_id=survey_201.id, sub_division_no="201/1", area_sqm=450.00, status="active")
        sub_300_1 = SubDivision(survey_number_id=survey_300.id, sub_division_no="300/1", area_sqm=900.00, status="active")
        sub_301_1 = SubDivision(survey_number_id=survey_301.id, sub_division_no="301/1", area_sqm=750.00, status="active")
        sub_302_1 = SubDivision(survey_number_id=survey_302.id, sub_division_no="302/1", area_sqm=600.00, status="active")
        sub_303_1 = SubDivision(survey_number_id=survey_303.id, sub_division_no="303/1", area_sqm=550.00, status="active")
        sub_304_1a = SubDivision(survey_number_id=survey_304.id, sub_division_no="304/1A", area_sqm=675.00, status="active")
        sub_304_1b = SubDivision(survey_number_id=survey_304.id, sub_division_no="304/1B", area_sqm=675.00, status="active")
        sub_305_1a = SubDivision(survey_number_id=survey_305.id, sub_division_no="305/1A", area_sqm=625.00, status="active")
        sub_305_1b = SubDivision(survey_number_id=survey_305.id, sub_division_no="305/1B", area_sqm=625.00, status="active")
        sub_306_1 = SubDivision(survey_number_id=survey_306.id, sub_division_no="306/1", area_sqm=725.00, status="active")

        db.add_all([
            sub_200_1, sub_201_1, sub_300_1, sub_301_1, sub_302_1,
            sub_303_1, sub_304_1a, sub_304_1b, sub_305_1a, sub_305_1b, sub_306_1
        ])
        await db.flush()

        # --- Block B10 Surveys (Tambaram Ward 5) ---
        survey_500 = SurveyNumber(block_id=block_b10.id, survey_no="500", total_area_sqm=2200.00, land_type="residential", patta_number="P-500-2019")
        survey_501 = SurveyNumber(block_id=block_b10.id, survey_no="501", total_area_sqm=1900.00, land_type="agricultural", patta_number="P-501-2020")
        survey_502 = SurveyNumber(block_id=block_b10.id, survey_no="502", total_area_sqm=1600.00, land_type="commercial", patta_number="P-502-2022")
        survey_503 = SurveyNumber(block_id=block_b10.id, survey_no="503", total_area_sqm=1400.00, land_type="residential", patta_number="P-503-2023")
        survey_504 = SurveyNumber(block_id=block_b10.id, survey_no="504", total_area_sqm=1700.00, land_type="commercial", patta_number="P-504-2024")
        survey_505 = SurveyNumber(block_id=block_b10.id, survey_no="505", total_area_sqm=1500.00, land_type="residential", patta_number="P-505-2025")

        # Surveys for District Officer (Lakshmi) in Block B1
        survey_163 = SurveyNumber(block_id=block_b1.id, survey_no="163", total_area_sqm=1150.00, land_type="residential", patta_number="P-163-2025")
        survey_164 = SurveyNumber(block_id=block_b1.id, survey_no="164", total_area_sqm=1250.00, land_type="commercial", patta_number="P-164-2025")
        survey_165 = SurveyNumber(block_id=block_b1.id, survey_no="165", total_area_sqm=1350.00, land_type="residential", patta_number="P-165-2025")
        survey_166 = SurveyNumber(block_id=block_b1.id, survey_no="166", total_area_sqm=1450.00, land_type="commercial", patta_number="P-166-2026")

        db.add_all([survey_500, survey_501, survey_502, survey_503, survey_504, survey_505, survey_163, survey_164, survey_165, survey_166])
        await db.flush()

        sub_500_1 = SubDivision(survey_number_id=survey_500.id, sub_division_no="500/1", area_sqm=1100.00, status="active")
        sub_501_1 = SubDivision(survey_number_id=survey_501.id, sub_division_no="501/1", area_sqm=950.00, status="active")
        sub_502_1 = SubDivision(survey_number_id=survey_502.id, sub_division_no="502/1", area_sqm=800.00, status="active")
        sub_503_1 = SubDivision(survey_number_id=survey_503.id, sub_division_no="503/1", area_sqm=700.00, status="active")
        sub_504_1a = SubDivision(survey_number_id=survey_504.id, sub_division_no="504/1A", area_sqm=850.00, status="active")
        sub_504_1b = SubDivision(survey_number_id=survey_504.id, sub_division_no="504/1B", area_sqm=850.00, status="active")
        sub_505_1 = SubDivision(survey_number_id=survey_505.id, sub_division_no="505/1", area_sqm=750.00, status="active")

        sub_163_1 = SubDivision(survey_number_id=survey_163.id, sub_division_no="163/1", area_sqm=575.00, status="active")
        sub_164_1a = SubDivision(survey_number_id=survey_164.id, sub_division_no="164/1A", area_sqm=625.00, status="active")
        sub_164_1b = SubDivision(survey_number_id=survey_164.id, sub_division_no="164/1B", area_sqm=625.00, status="active")
        sub_165_1a = SubDivision(survey_number_id=survey_165.id, sub_division_no="165/1A", area_sqm=675.00, status="active")
        sub_165_1b = SubDivision(survey_number_id=survey_165.id, sub_division_no="165/1B", area_sqm=675.00, status="active")
        sub_166_1 = SubDivision(survey_number_id=survey_166.id, sub_division_no="166/1", area_sqm=725.00, status="active")

        db.add_all([
            sub_500_1, sub_501_1, sub_502_1, sub_503_1, sub_504_1a, sub_504_1b, sub_505_1,
            sub_163_1, sub_164_1a, sub_164_1b, sub_165_1a, sub_165_1b, sub_166_1
        ])
        await db.flush()
        
        print("[OK] Survey numbers seeded")
        
        # ========== OWNERS ==========
        print("[OWNERS] Seeding owners...")
        
        owners = []
        for i in range(1, 30):
            o = Owner(
                name=f"Owner {i}",
                name_tamil=f"உரிமையாளர் {i}",
                father_name=f"Father {i}",
                aadhaar_last4=f"{4000+i}",
                mobile=f"98400{i:05d}",
                address=f"No {i*3}, Main Street, Ambattur, Chennai"
            )
            owners.append(o)
            db.add(o)
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
        print("[APPS] Seeding applications with UNIQUE survey numbers per active application...")
        
        applicants = []
        for i in range(1, 35):
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
        
        # --- Officer 1 Applications (Block B1) ---
        # App 1: ISD, pending, field visit scheduled (Survey 145)
        app_1 = Application(
            application_number="APP-2024-000001", application_type="ISD", applicant_id=applicants[0].id,
            survey_number_id=survey_145.id, assigned_officer_id=officer_1.id, submission_channel="CSC",
            submission_date=today - timedelta(days=5), sale_deed_number="SD-2025-1001", sale_deed_registered=True,
            declared_reason="sale", current_stage="SIS", current_status="pending",
            field_visit_date=today + timedelta(days=3), field_visit_scheduled=True, is_overdue=False, priority_flag=False,
            notes="New sub-division requested for Survey 145"
        )
        # App 2: NISD, forwarded to SD (Survey 146)
        app_2 = Application(
            application_number="APP-2024-000002", application_type="NISD", applicant_id=applicants[1].id,
            survey_number_id=survey_146.id, assigned_officer_id=officer_1.id, submission_channel="citizen",
            submission_date=today - timedelta(days=12), sale_deed_number="SD-2025-1002", sale_deed_registered=True,
            declared_reason="inheritance", current_stage="SD", current_status="in_progress",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )
        # App 3: ISD, at DIS, overdue (Survey 147)
        app_3 = Application(
            application_number="APP-2024-000003", application_type="ISD", applicant_id=applicants[2].id,
            survey_number_id=survey_147.id, assigned_officer_id=officer_1.id, submission_channel="sub_registrar",
            submission_date=today - timedelta(days=25), sale_deed_number="SD-2025-1003", sale_deed_registered=True,
            declared_reason="partition", current_stage="DIS", current_status="pending",
            field_visit_scheduled=False, is_overdue=True, priority_flag=True
        )
        # App 4: MERGE, completed (Survey 148)
        app_4 = Application(
            application_number="APP-2024-000004", application_type="MERGE", applicant_id=applicants[3].id,
            survey_number_id=survey_148.id, assigned_officer_id=officer_1.id, submission_channel="CSC",
            submission_date=today - timedelta(days=45), sale_deed_number="SD-2025-1004", sale_deed_registered=True,
            declared_reason="sale", current_stage="COMPLETED", current_status="approved",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )
        # App 11: MERGE, pending at SIS (Survey 149)
        app_11 = Application(
            application_number="APP-2024-000011", application_type="MERGE", applicant_id=applicants[10].id,
            survey_number_id=survey_149.id, assigned_officer_id=officer_1.id, submission_channel="CSC",
            submission_date=today - timedelta(days=7), sale_deed_number="SD-2025-1011", sale_deed_registered=True,
            declared_reason="sale", current_stage="SIS", current_status="pending",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )
        # App 12: MERGE, in_progress at SD (Survey 150)
        app_12 = Application(
            application_number="APP-2024-000012", application_type="MERGE", applicant_id=applicants[11].id,
            survey_number_id=survey_150.id, assigned_officer_id=officer_1.id, submission_channel="citizen",
            submission_date=today - timedelta(days=14), sale_deed_number="SD-2025-1012", sale_deed_registered=True,
            declared_reason="partition", current_stage="SD", current_status="in_progress",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )
        # App 13: NISD, pending at SIS (Survey 151)
        app_13 = Application(
            application_number="APP-2024-000013", application_type="NISD", applicant_id=applicants[12].id,
            survey_number_id=survey_151.id, assigned_officer_id=officer_1.id, submission_channel="sub_registrar",
            submission_date=today - timedelta(days=4), sale_deed_number="SD-2025-1013", sale_deed_registered=True,
            declared_reason="gift_deed", current_stage="SIS", current_status="pending",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )
        # App 14: ISD, in_progress at SD (Survey 152)
        app_14 = Application(
            application_number="APP-2024-000014", application_type="ISD", applicant_id=applicants[13].id,
            survey_number_id=survey_152.id, assigned_officer_id=officer_1.id, submission_channel="CSC",
            submission_date=today - timedelta(days=16), sale_deed_number="SD-2025-1014", sale_deed_registered=True,
            declared_reason="sale", current_stage="SD", current_status="in_progress",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )
        # App 15: NISD, pending at Tahsildar (Survey 153)
        app_15 = Application(
            application_number="APP-2024-000015", application_type="NISD", applicant_id=applicants[14].id,
            survey_number_id=survey_153.id, assigned_officer_id=officer_1.id, submission_channel="citizen",
            submission_date=today - timedelta(days=22), sale_deed_number="SD-2025-1015", sale_deed_registered=True,
            declared_reason="inheritance", current_stage="TAHSILDAR", current_status="pending",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )
        # App 18: Overdue ISD (Survey 154 - dedicated to avoid conflict with Survey 145)
        app_18 = Application(
            application_number="APP-2024-000018", application_type="ISD", applicant_id=applicants[17].id,
            survey_number_id=survey_154.id, assigned_officer_id=officer_1.id, submission_channel="citizen",
            submission_date=today - timedelta(days=22), sale_deed_number="SD-2025-1018", sale_deed_registered=True,
            declared_reason="sale", current_stage="SIS", current_status="pending",
            field_visit_scheduled=True, field_visit_date=today - timedelta(days=5), is_overdue=True, priority_flag=True,
            notes="ISD application - Field visit overdue by 5 days"
        )
        # App 22: Overdue MERGE (Survey 155 - dedicated to avoid conflict with Survey 148)
        app_22 = Application(
            application_number="APP-2024-000022", application_type="MERGE", applicant_id=applicants[21].id,
            survey_number_id=survey_155.id, assigned_officer_id=officer_1.id, submission_channel="CSC",
            submission_date=today - timedelta(days=26), sale_deed_number="SD-2025-1022", sale_deed_registered=True,
            declared_reason="sale", current_stage="SIS", current_status="pending",
            field_visit_scheduled=True, field_visit_date=today - timedelta(days=7), is_overdue=True, priority_flag=True,
            notes="MERGE application - Field visit overdue by 7 days"
        )
        # App 26: 2025 ISD (Survey 156)
        app_26 = Application(
            application_number="APP-2025-000001", application_type="ISD", applicant_id=applicants[25].id,
            survey_number_id=survey_156.id, assigned_officer_id=officer_1.id, submission_channel="citizen",
            submission_date=date(2025, 1, 15), sale_deed_number="SD-2025-2001", sale_deed_registered=True,
            declared_reason="sale", current_stage="SIS", current_status="pending",
            field_visit_scheduled=False, is_overdue=True, priority_flag=True
        )
        # App 27: 2025 NISD (Survey 157)
        app_27 = Application(
            application_number="APP-2025-000002", application_type="NISD", applicant_id=applicants[26].id,
            survey_number_id=survey_157.id, assigned_officer_id=officer_1.id, submission_channel="CSC",
            submission_date=date(2025, 2, 10), sale_deed_number="SD-2025-2002", sale_deed_registered=True,
            declared_reason="inheritance", current_stage="SIS", current_status="pending",
            field_visit_scheduled=False, is_overdue=True, priority_flag=True
        )

        # --- Recent Applications Submitted up to Current Date (2026-07-29) ---
        # App 31: ISD submitted TODAY 2026-07-29 (Survey 158)
        app_31 = Application(
            application_number="APP-2026-000031", application_type="ISD", applicant_id=applicants[27].id,
            survey_number_id=survey_158.id, assigned_officer_id=officer_1.id, submission_channel="CSC",
            submission_date=today, sale_deed_number="SD-2026-3031", sale_deed_registered=True,
            declared_reason="sale", current_stage="SIS", current_status="pending",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False,
            notes="Submitted today 2026-07-29 via CSC"
        )
        # App 32: MERGE submitted TODAY 2026-07-29 (Survey 159)
        app_32 = Application(
            application_number="APP-2026-000032", application_type="MERGE", applicant_id=applicants[28].id,
            survey_number_id=survey_159.id, assigned_officer_id=officer_1.id, submission_channel="citizen",
            submission_date=today, sale_deed_number="SD-2026-3032", sale_deed_registered=True,
            declared_reason="partition", current_stage="SIS", current_status="pending",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False,
            notes="Submitted today 2026-07-29 - Merge of 159/1A and 159/1B"
        )
        # App 33: NISD submitted YESTERDAY 2026-07-28 (Survey 160)
        app_33 = Application(
            application_number="APP-2026-000033", application_type="NISD", applicant_id=applicants[29].id,
            survey_number_id=survey_160.id, assigned_officer_id=officer_1.id, submission_channel="sub_registrar",
            submission_date=today - timedelta(days=1), sale_deed_number="SD-2026-3033", sale_deed_registered=True,
            declared_reason="gift_deed", current_stage="SIS", current_status="pending",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )
        # App 34: ISD submitted 2026-07-25 (Survey 161)
        app_34 = Application(
            application_number="APP-2026-000034", application_type="ISD", applicant_id=applicants[30].id,
            survey_number_id=survey_161.id, assigned_officer_id=officer_1.id, submission_channel="CSC",
            submission_date=today - timedelta(days=4), sale_deed_number="SD-2026-3034", sale_deed_registered=True,
            declared_reason="sale", current_stage="SIS", current_status="pending",
            field_visit_scheduled=True, field_visit_date=today + timedelta(days=2), is_overdue=False, priority_flag=False
        )
        # App 35: MERGE submitted 2026-07-20 (Survey 162)
        app_35 = Application(
            application_number="APP-2026-000035", application_type="MERGE", applicant_id=applicants[31].id,
            survey_number_id=survey_162.id, assigned_officer_id=officer_1.id, submission_channel="citizen",
            submission_date=today - timedelta(days=9), sale_deed_number="SD-2026-3035", sale_deed_registered=True,
            declared_reason="sale", current_stage="SIS", current_status="pending",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )

        # --- Officer 2 Applications (Block B2 / B3) ---
        # App 5: ISD (Survey 200)
        app_5 = Application(
            application_number="APP-2024-000005", application_type="ISD", applicant_id=applicants[4].id,
            survey_number_id=survey_200.id, assigned_officer_id=officer_2.id, submission_channel="citizen",
            submission_date=today - timedelta(days=8), declared_reason="sale", current_stage="SIS", current_status="pending",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )
        # App 6: NISD, rejected (Survey 201)
        app_6 = Application(
            application_number="APP-2024-000006", application_type="NISD", applicant_id=applicants[5].id,
            survey_number_id=survey_201.id, assigned_officer_id=officer_2.id, submission_channel="CSC",
            submission_date=today - timedelta(days=18), declared_reason="gift_deed", current_stage="REJECTED", current_status="rejected",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )
        # App 7: ISD (Survey 300)
        app_7 = Application(
            application_number="APP-2024-000007", application_type="ISD", applicant_id=applicants[6].id,
            survey_number_id=survey_300.id, assigned_officer_id=officer_2.id, submission_channel="citizen",
            submission_date=today - timedelta(days=6), declared_reason="sale", current_stage="SIS", current_status="pending",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )
        # App 8: ISD at SD (Survey 301)
        app_8 = Application(
            application_number="APP-2024-000008", application_type="ISD", applicant_id=applicants[7].id,
            survey_number_id=survey_301.id, assigned_officer_id=officer_2.id, submission_channel="CSC",
            submission_date=today - timedelta(days=10), declared_reason="partition", current_stage="SD", current_status="pending",
            field_visit_scheduled=False, is_overdue=False, priority_flag=True
        )
        # App 16: MERGE at DIS (Survey 302)
        app_16 = Application(
            application_number="APP-2024-000016", application_type="MERGE", applicant_id=applicants[15].id,
            survey_number_id=survey_302.id, assigned_officer_id=officer_2.id, submission_channel="CSC",
            submission_date=today - timedelta(days=18), sale_deed_number="SD-2025-1016", sale_deed_registered=True,
            declared_reason="sale", current_stage="DIS", current_status="in_progress",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )
        # App 19: Overdue ISD (Survey 303)
        app_19 = Application(
            application_number="APP-2024-000019", application_type="ISD", applicant_id=applicants[18].id,
            survey_number_id=survey_303.id, assigned_officer_id=officer_2.id, submission_channel="CSC",
            submission_date=today - timedelta(days=28), sale_deed_number="SD-2025-1019", sale_deed_registered=True,
            declared_reason="partition", current_stage="SIS", current_status="pending",
            field_visit_scheduled=True, field_visit_date=today - timedelta(days=8), is_overdue=True, priority_flag=True
        )
        # App 23: Overdue MERGE (Survey 304)
        app_23 = Application(
            application_number="APP-2024-000023", application_type="MERGE", applicant_id=applicants[22].id,
            survey_number_id=survey_304.id, assigned_officer_id=officer_2.id, submission_channel="citizen",
            submission_date=today - timedelta(days=24), sale_deed_number="SD-2025-1023", sale_deed_registered=True,
            declared_reason="partition", current_stage="SIS", current_status="pending",
            field_visit_scheduled=True, field_visit_date=today - timedelta(days=6), is_overdue=True, priority_flag=True
        )
        # App 28: 2025 MERGE (Survey 305)
        app_28 = Application(
            application_number="APP-2025-000003", application_type="MERGE", applicant_id=applicants[8].id,
            survey_number_id=survey_305.id, assigned_officer_id=officer_2.id, submission_channel="sub_registrar",
            submission_date=date(2025, 3, 5), sale_deed_number="SD-2025-2003", sale_deed_registered=True,
            declared_reason="sale", current_stage="SIS", current_status="in_progress",
            field_visit_scheduled=True, field_visit_date=date(2025, 3, 20), is_overdue=True, priority_flag=True
        )
        # App 36: ISD submitted 2026-07-28 (Survey 306)
        app_36 = Application(
            application_number="APP-2026-000036", application_type="ISD", applicant_id=applicants[32].id,
            survey_number_id=survey_306.id, assigned_officer_id=officer_2.id, submission_channel="citizen",
            submission_date=today - timedelta(days=1), sale_deed_number="SD-2026-3036", sale_deed_registered=True,
            declared_reason="sale", current_stage="SIS", current_status="pending",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )

        # --- Officer 3 Applications (Tambaram Block B10) ---
        # App 9: NISD, completed (Survey 500)
        app_9 = Application(
            application_number="APP-2024-000009", application_type="NISD", applicant_id=applicants[8].id,
            survey_number_id=survey_500.id, assigned_officer_id=officer_3.id, submission_channel="sub_registrar",
            submission_date=today - timedelta(days=30), declared_reason="inheritance", current_stage="COMPLETED", current_status="approved",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )
        # App 10: ISD at Tahsildar (Survey 501)
        app_10 = Application(
            application_number="APP-2024-000010", application_type="ISD", applicant_id=applicants[9].id,
            survey_number_id=survey_501.id, assigned_officer_id=officer_3.id, submission_channel="CSC",
            submission_date=today - timedelta(days=20), declared_reason="sale", current_stage="TAHSILDAR", current_status="pending",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )
        # App 17: MERGE, completed (Survey 502)
        app_17 = Application(
            application_number="APP-2024-000017", application_type="MERGE", applicant_id=applicants[16].id,
            survey_number_id=survey_502.id, assigned_officer_id=officer_3.id, submission_channel="CSC",
            submission_date=today - timedelta(days=35), sale_deed_number="SD-2025-1017", sale_deed_registered=True,
            declared_reason="sale", current_stage="COMPLETED", current_status="approved",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )
        # App 20: Overdue ISD (Survey 503)
        app_20 = Application(
            application_number="APP-2024-000020", application_type="ISD", applicant_id=applicants[19].id,
            survey_number_id=survey_503.id, assigned_officer_id=officer_3.id, submission_channel="sub_registrar",
            submission_date=today - timedelta(days=20), sale_deed_number="SD-2025-1020", sale_deed_registered=True,
            declared_reason="sale", current_stage="SIS", current_status="pending",
            field_visit_scheduled=True, field_visit_date=today - timedelta(days=3), is_overdue=True, priority_flag=True
        )
        # App 24: Overdue MERGE (Survey 504)
        app_24 = Application(
            application_number="APP-2024-000024", application_type="MERGE", applicant_id=applicants[23].id,
            survey_number_id=survey_504.id, assigned_officer_id=officer_3.id, submission_channel="CSC",
            submission_date=today - timedelta(days=27), sale_deed_number="SD-2025-1024", sale_deed_registered=True,
            declared_reason="sale", current_stage="SIS", current_status="pending",
            field_visit_scheduled=True, field_visit_date=today - timedelta(days=9), is_overdue=True, priority_flag=True
        )
        # App 29: 2025 ISD (Survey 505)
        app_29 = Application(
            application_number="APP-2025-000004", application_type="ISD", applicant_id=applicants[9].id,
            survey_number_id=survey_505.id, assigned_officer_id=officer_3.id, submission_channel="citizen",
            submission_date=date(2025, 1, 25), sale_deed_number="SD-2025-2004", sale_deed_registered=True,
            declared_reason="partition", current_stage="SD", current_status="in_progress",
            field_visit_scheduled=False, is_overdue=True, priority_flag=True
        )

        # --- Officer 4 Applications (District Level) ---
        # App 21: Overdue ISD (Survey 163)
        app_21 = Application(
            application_number="APP-2024-000021", application_type="ISD", applicant_id=applicants[20].id,
            survey_number_id=survey_163.id, assigned_officer_id=officer_4.id, submission_channel="citizen",
            submission_date=today - timedelta(days=30), sale_deed_number="SD-2025-1021", sale_deed_registered=True,
            declared_reason="inheritance", current_stage="SIS", current_status="pending",
            field_visit_scheduled=True, field_visit_date=today - timedelta(days=10), is_overdue=True, priority_flag=True
        )
        # App 25: Overdue MERGE (Survey 164)
        app_25 = Application(
            application_number="APP-2024-000025", application_type="MERGE", applicant_id=applicants[24].id,
            survey_number_id=survey_164.id, assigned_officer_id=officer_4.id, submission_channel="sub_registrar",
            submission_date=today - timedelta(days=32), sale_deed_number="SD-2025-1025", sale_deed_registered=True,
            declared_reason="sale", current_stage="SIS", current_status="pending",
            field_visit_scheduled=True, field_visit_date=today - timedelta(days=12), is_overdue=True, priority_flag=True
        )
        # App 30: 2025 MERGE (Survey 165)
        app_30 = Application(
            application_number="APP-2025-000005", application_type="MERGE", applicant_id=applicants[10].id,
            survey_number_id=survey_165.id, assigned_officer_id=officer_4.id, submission_channel="CSC",
            submission_date=date(2025, 2, 20), sale_deed_number="SD-2025-2005", sale_deed_registered=True,
            declared_reason="sale", current_stage="SIS", current_status="pending",
            field_visit_scheduled=False, is_overdue=True, priority_flag=True
        )
        # App 37: ISD submitted TODAY 2026-07-29 (Survey 166)
        app_37 = Application(
            application_number="APP-2026-000037", application_type="ISD", applicant_id=applicants[33].id,
            survey_number_id=survey_166.id, assigned_officer_id=officer_4.id, submission_channel="CSC",
            submission_date=today, sale_deed_number="SD-2026-3037", sale_deed_registered=True,
            declared_reason="sale", current_stage="SIS", current_status="pending",
            field_visit_scheduled=False, is_overdue=False, priority_flag=False
        )

        all_apps = [
            app_1, app_2, app_3, app_4, app_5, app_6, app_7, app_8, app_9, app_10,
            app_11, app_12, app_13, app_14, app_15, app_16, app_17, app_18, app_19, app_20,
            app_21, app_22, app_23, app_24, app_25, app_26, app_27, app_28, app_29, app_30,
            app_31, app_32, app_33, app_34, app_35, app_36, app_37
        ]

        db.add_all(all_apps)
        await db.flush()

        # ========== VALIDATION: 1 Active Application per Survey Number ==========
        active_apps = [a for a in all_apps if a.current_status in ["pending", "in_progress"]]
        survey_block_counts = Counter((a.survey_number_id, a.survey_number.block_id) for a in active_apps)
        for (sn_id, blk_id), cnt in survey_block_counts.items():
            if cnt > 1:
                raise ValueError(f"[SEED ERROR] Survey ID {sn_id} in Block {blk_id} has {cnt} active applications!")

        print(f"[OK] Applications seeded ({len(all_apps)} applications total). Survey uniqueness validation PASSED!")

        # ========== APPLICATION SUBDIVISIONS ==========
        print("[SUBDIVISIONS] Seeding application sub-divisions...")
        
        asds = [
            # App 1
            ApplicationSubDivision(application_id=app_1.id, sub_division_id=sub_145_1a.id, proposed_area_sqm=600.00),
            ApplicationSubDivision(application_id=app_1.id, sub_division_id=sub_145_1b.id, proposed_area_sqm=700.00),
            # App 3
            ApplicationSubDivision(application_id=app_3.id, sub_division_id=sub_147_1.id, proposed_area_sqm=800.00),
            ApplicationSubDivision(application_id=app_3.id, sub_division_id=sub_147_2.id, proposed_area_sqm=750.00),
            # App 4
            ApplicationSubDivision(application_id=app_4.id, sub_division_id=sub_148_1a.id, proposed_area_sqm=500.00),
            ApplicationSubDivision(application_id=app_4.id, sub_division_id=sub_148_1b.id, proposed_area_sqm=600.00),
            ApplicationSubDivision(application_id=app_4.id, sub_division_id=sub_148_1c.id, proposed_area_sqm=400.00),
            # App 11
            ApplicationSubDivision(application_id=app_11.id, sub_division_id=sub_149_1.id, proposed_area_sqm=450.00),
            ApplicationSubDivision(application_id=app_11.id, sub_division_id=sub_149_2.id, proposed_area_sqm=550.00),
            # App 12
            ApplicationSubDivision(application_id=app_12.id, sub_division_id=sub_150_1a.id, proposed_area_sqm=350.00),
            ApplicationSubDivision(application_id=app_12.id, sub_division_id=sub_150_1b.id, proposed_area_sqm=400.00),
            # App 14
            ApplicationSubDivision(application_id=app_14.id, sub_division_id=sub_152_1.id, proposed_area_sqm=1000.00),
            # App 16
            ApplicationSubDivision(application_id=app_16.id, sub_division_id=sub_302_1.id, proposed_area_sqm=600.00),
            # App 17
            ApplicationSubDivision(application_id=app_17.id, sub_division_id=sub_502_1.id, proposed_area_sqm=800.00),
            # App 18
            ApplicationSubDivision(application_id=app_18.id, sub_division_id=sub_154_1.id, proposed_area_sqm=550.00),
            ApplicationSubDivision(application_id=app_18.id, sub_division_id=sub_154_2.id, proposed_area_sqm=550.00),
            # App 22
            ApplicationSubDivision(application_id=app_22.id, sub_division_id=sub_155_1a.id, proposed_area_sqm=700.00),
            ApplicationSubDivision(application_id=app_22.id, sub_division_id=sub_155_1b.id, proposed_area_sqm=700.00),
            # App 23
            ApplicationSubDivision(application_id=app_23.id, sub_division_id=sub_304_1a.id, proposed_area_sqm=675.00),
            ApplicationSubDivision(application_id=app_23.id, sub_division_id=sub_304_1b.id, proposed_area_sqm=675.00),
            # App 24
            ApplicationSubDivision(application_id=app_24.id, sub_division_id=sub_504_1a.id, proposed_area_sqm=850.00),
            ApplicationSubDivision(application_id=app_24.id, sub_division_id=sub_504_1b.id, proposed_area_sqm=850.00),
            # App 25
            ApplicationSubDivision(application_id=app_25.id, sub_division_id=sub_164_1a.id, proposed_area_sqm=625.00),
            ApplicationSubDivision(application_id=app_25.id, sub_division_id=sub_164_1b.id, proposed_area_sqm=625.00),
            # App 32
            ApplicationSubDivision(application_id=app_32.id, sub_division_id=sub_159_1a.id, proposed_area_sqm=800.00),
            ApplicationSubDivision(application_id=app_32.id, sub_division_id=sub_159_1b.id, proposed_area_sqm=800.00),
            # App 35
            ApplicationSubDivision(application_id=app_35.id, sub_division_id=sub_162_1a.id, proposed_area_sqm=875.00),
            ApplicationSubDivision(application_id=app_35.id, sub_division_id=sub_162_1b.id, proposed_area_sqm=875.00),
        ]
        db.add_all(asds)
        await db.flush()
        print("[OK] Application sub-divisions seeded")

        # ========== WORKFLOW HISTORY ==========
        print("[WORKFLOW] Seeding workflow history...")
        
        wf_history = [
            WorkflowHistory(
                application_id=app_1.id, from_stage=None, to_stage="SIS", action="APPLICATION_SUBMITTED",
                performed_by_officer_id=None, remarks="Application submitted via CSC",
                performed_at=datetime.combine(today - timedelta(days=5), datetime.min.time().replace(hour=10, minute=30))
            ),
            WorkflowHistory(
                application_id=app_2.id, from_stage="SIS", to_stage="SD", action="FORWARDED_TO_SD",
                performed_by_officer_id=officer_1.id, remarks="Field verification completed, forwarding to SD",
                performed_at=datetime.combine(today - timedelta(days=8), datetime.min.time().replace(hour=14, minute=30))
            ),
            WorkflowHistory(
                application_id=app_4.id, from_stage="SIS", to_stage="COMPLETED", action="APPROVED",
                performed_by_officer_id=officer_1.id, remarks="Sub-division merge verified and approved. New patta issued.",
                performed_at=datetime.combine(today - timedelta(days=35), datetime.min.time().replace(hour=16, minute=20))
            ),
            WorkflowHistory(
                application_id=app_6.id, from_stage="SD", to_stage="REJECTED", action="REJECTED",
                performed_by_officer_id=officer_2.id, remarks="Rejected by SD",
                rejection_reason="Boundary mismatch detected in field verification",
                performed_at=datetime.combine(today - timedelta(days=10), datetime.min.time().replace(hour=11, minute=15))
            ),
            WorkflowHistory(
                application_id=app_31.id, from_stage=None, to_stage="SIS", action="APPLICATION_SUBMITTED",
                performed_by_officer_id=None, remarks="Application submitted today via CSC",
                performed_at=datetime.combine(today, datetime.min.time().replace(hour=9, minute=15))
            ),
            WorkflowHistory(
                application_id=app_32.id, from_stage=None, to_stage="SIS", action="APPLICATION_SUBMITTED",
                performed_by_officer_id=None, remarks="MERGE application submitted today by citizen",
                performed_at=datetime.combine(today, datetime.min.time().replace(hour=10, minute=45))
            ),
        ]
        db.add_all(wf_history)
        await db.flush()
        print("[OK] Workflow history seeded")
        
        # ========== FIELD VISITS ==========
        print("[VISITS] Seeding field visits...")
        
        fvs = [
            FieldVisit(application_id=app_1.id, officer_id=officer_1.id, scheduled_date=today + timedelta(days=3), status="scheduled", visit_notes=None),
            FieldVisit(application_id=app_3.id, officer_id=officer_1.id, scheduled_date=today - timedelta(days=5), status="overdue", visit_notes="Field visit not completed on scheduled date"),
            FieldVisit(application_id=app_5.id, officer_id=officer_2.id, status="unscheduled"),
            FieldVisit(application_id=app_11.id, officer_id=officer_1.id, status="unscheduled"),
            FieldVisit(application_id=app_12.id, officer_id=officer_1.id, scheduled_date=today - timedelta(days=6), status="completed", visit_notes="Sub-division boundary markers physically verified on ground"),
            # Overdue field visits
            FieldVisit(application_id=app_18.id, officer_id=officer_1.id, scheduled_date=today - timedelta(days=5), status="overdue", visit_notes="ISD - Overdue field visit"),
            FieldVisit(application_id=app_19.id, officer_id=officer_2.id, scheduled_date=today - timedelta(days=8), status="overdue", visit_notes="ISD - Overdue field visit"),
            FieldVisit(application_id=app_20.id, officer_id=officer_3.id, scheduled_date=today - timedelta(days=3), status="overdue", visit_notes="ISD - Overdue field visit"),
            FieldVisit(application_id=app_21.id, officer_id=officer_4.id, scheduled_date=today - timedelta(days=10), status="overdue", visit_notes="ISD - Overdue field visit"),
            FieldVisit(application_id=app_22.id, officer_id=officer_1.id, scheduled_date=today - timedelta(days=7), status="overdue", visit_notes="MERGE - Overdue field visit"),
            FieldVisit(application_id=app_23.id, officer_id=officer_2.id, scheduled_date=today - timedelta(days=6), status="overdue", visit_notes="MERGE - Overdue field visit"),
            FieldVisit(application_id=app_24.id, officer_id=officer_3.id, scheduled_date=today - timedelta(days=9), status="overdue", visit_notes="MERGE - Overdue field visit"),
            FieldVisit(application_id=app_25.id, officer_id=officer_4.id, scheduled_date=today - timedelta(days=12), status="overdue", visit_notes="MERGE - Overdue field visit"),
            # Scheduled visit for recent app 34
            FieldVisit(application_id=app_34.id, officer_id=officer_1.id, scheduled_date=today + timedelta(days=2), status="scheduled", visit_notes="Upcoming field visit for App 34"),
        ]
        db.add_all(fvs)
        await db.flush()
        print(f"[OK] Field visits seeded ({len(fvs)} visits total)")
        
        # ========== APPLICATION DOCUMENTS ==========
        print("[DOCS] Seeding application documents...")
        
        docs = [
            # App 1 (ISD)
            ApplicationDocument(application_id=app_1.id, document_type="Sale Deed", document_name="sale_deed_145.pdf", is_uploaded=True, is_verified=True, uploaded_at=datetime.combine(today - timedelta(days=5), datetime.min.time().replace(hour=10, minute=35))),
            ApplicationDocument(application_id=app_1.id, document_type="Encumbrance Certificate", document_name="ec_145.pdf", is_uploaded=True, is_verified=False, uploaded_at=datetime.combine(today - timedelta(days=5), datetime.min.time().replace(hour=10, minute=37))),
            # App 31 (Today ISD)
            ApplicationDocument(application_id=app_31.id, document_type="Sale Deed", document_name="sale_deed_158.pdf", is_uploaded=True, is_verified=True, uploaded_at=datetime.combine(today, datetime.min.time().replace(hour=9, minute=20))),
            # App 32 (Today MERGE)
            ApplicationDocument(application_id=app_32.id, document_type="Merge Layout Plan", document_name="merge_plan_159.pdf", is_uploaded=True, is_verified=True, uploaded_at=datetime.combine(today, datetime.min.time().replace(hour=10, minute=50))),
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
