"""
Quick test to check if password verification works correctly
"""
import asyncio
from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.services.auth_service import verify_password
import sys

# These suites print Tamil. On Windows the console is cp1252 and the
# first Tamil character raises UnicodeEncodeError, which killed the run
# before any result was reported. Same guard the other suites carry.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


async def test_login():
    """Test login credentials"""
    async with AsyncSessionLocal() as db:
        # Try to get the first officer
        result = await db.execute(
            select(SISOfficer).where(SISOfficer.email == "csenthil@sis.tn.gov.in")
        )
        officer = result.scalar_one_or_none()
        
        if not officer:
            print("❌ Officer not found!")
            return
        
        print(f"✅ Officer found: {officer.email}")
        print(f"   Name: {officer.name}")
        print(f"   Employee ID: {officer.employee_id}")
        print(f"   Is Active: {officer.is_active}")
        print(f"   Password Hash: {officer.password_hash[:50]}...")
        
        # Test password verification
        test_password = "Test@1234"
        is_valid = verify_password(test_password, officer.password_hash)
        
        if is_valid:
            print(f"✅ Password verification PASSED for '{test_password}'")
        else:
            print(f"❌ Password verification FAILED for '{test_password}'")
            
            # Try to verify with the hash function directly
            from passlib.context import CryptContext
            pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
            is_valid_direct = pwd_context.verify(test_password, officer.password_hash)
            
            if is_valid_direct:
                print("✅ Direct passlib verification PASSED")
            else:
                print("❌ Direct passlib verification FAILED")


if __name__ == "__main__":
    asyncio.run(test_login())
