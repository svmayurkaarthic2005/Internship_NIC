"""
Check login credentials in sis_chatbot_db
"""
import asyncio
import sys
sys.path.insert(0, '.')

# The output carries emoji and Tamil officer names; a Windows console defaults
# to cp1252 and cannot encode either. Same guard as backend/main.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer, OfficerJurisdiction, District, Taluk, Town, Ward, Block
from sqlalchemy import select


async def check_credentials():
    async with AsyncSessionLocal() as db:
        # Get all active officers with their jurisdictions
        result = await db.execute(
            select(SISOfficer)
            .where(SISOfficer.is_active == True)
            .order_by(SISOfficer.employee_id)
        )
        officers = result.scalars().all()
        
        print("\n" + "=" * 120)
        print("SIS CHATBOT DATABASE - LOGIN CREDENTIALS")
        print("=" * 120)
        print(f"Database: sis_chatbot_db")
        print(f"Total Active Officers: {len(officers)}")
        print(f"Default Password: Test@1234 (for all test accounts)")
        print("=" * 120)
        
        for officer in officers:
            # Get jurisdiction details
            juris_result = await db.execute(
                select(OfficerJurisdiction)
                .where(OfficerJurisdiction.officer_id == officer.id)
            )
            jurisdiction = juris_result.scalar_one_or_none()
            
            print(f"\n📧 Email: {officer.email}")
            print(f"   Password: Test@1234")
            print(f"   Name: {officer.name} ({officer.name_tamil})")
            print(f"   Employee ID: {officer.employee_id}")
            print(f"   Designation: {officer.designation}")
            print(f"   Mobile: {officer.mobile}")
            
            if jurisdiction:
                print(f"   Jurisdiction Type: {jurisdiction.jurisdiction_type.upper()}")
                
                if jurisdiction.district_id:
                    dist_result = await db.execute(
                        select(District).where(District.id == jurisdiction.district_id)
                    )
                    district = dist_result.scalar_one_or_none()
                    if district:
                        print(f"   District: {district.name} ({district.district_code})")
                
                if jurisdiction.taluk_id:
                    taluk_result = await db.execute(
                        select(Taluk).where(Taluk.id == jurisdiction.taluk_id)
                    )
                    taluk = taluk_result.scalar_one_or_none()
                    if taluk:
                        print(f"   Taluk: {taluk.name}")
                
                if jurisdiction.town_id:
                    town_result = await db.execute(
                        select(Town).where(Town.id == jurisdiction.town_id)
                    )
                    town = town_result.scalar_one_or_none()
                    if town:
                        print(f"   Town: {town.name}")
                
                if jurisdiction.ward_id:
                    ward_result = await db.execute(
                        select(Ward).where(Ward.id == jurisdiction.ward_id)
                    )
                    ward = ward_result.scalar_one_or_none()
                    if ward:
                        print(f"   Ward: {ward.ward_name} ({ward.ward_number})")
                
                if jurisdiction.block_id:
                    block_result = await db.execute(
                        select(Block).where(Block.id == jurisdiction.block_id)
                    )
                    block = block_result.scalar_one_or_none()
                    if block:
                        print(f"   Block: {block.block_name} ({block.block_number})")
        
        print("\n" + "=" * 120)
        print("HOW TO LOGIN:")
        print("1. Start frontend: python serve_frontend.py")
        print("2. Open: http://localhost:3000/login.html")
        print("3. Use any email above with password: Test@1234")
        print("=" * 120 + "\n")


if __name__ == "__main__":
    asyncio.run(check_credentials())
