"""
Test script to measure login performance before and after optimization.
"""
import asyncio
import time
from backend.database import get_db
from backend.services.auth_service import get_officer_jurisdiction_ids
from sqlalchemy import select
from backend.models import SISOfficer
import sys

# These suites print Tamil. On Windows the console is cp1252 and the
# first Tamil character raises UnicodeEncodeError, which killed the run
# before any result was reported. Same guard the other suites carry.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

async def test_jurisdiction_loading_performance():
    """Test the performance of jurisdiction loading"""
    print("="*80)
    print("Testing Login Performance - Jurisdiction Loading")
    print("="*80)
    
    async for db in get_db():
        try:
            # Get the first officer
            result = await db.execute(
                select(SISOfficer).where(SISOfficer.email == "csenthil@sis.tn.gov.in")
            )
            officer = result.scalar_one_or_none()
            
            if not officer:
                print("❌ Test officer not found in database")
                return
            
            print(f"\nOfficer: {officer.name} ({officer.email})")
            print(f"Employee ID: {officer.employee_id}")
            
            # Test jurisdiction loading with timing
            print("\n" + "-"*80)
            print("Loading jurisdiction data...")
            print("-"*80)
            
            start_time = time.time()
            jurisdiction_data = await get_officer_jurisdiction_ids(officer.id, db)
            end_time = time.time()
            
            elapsed_ms = (end_time - start_time) * 1000
            
            print(f"\n✅ Jurisdiction loaded in {elapsed_ms:.2f}ms")
            print(f"\nJurisdiction Details:")
            print(f"  Type: {jurisdiction_data['jurisdiction_type']}")
            print(f"  Name: {jurisdiction_data['jurisdiction_name']}")
            print(f"  District IDs: {len(jurisdiction_data['district_ids'])}")
            print(f"  Taluk IDs: {len(jurisdiction_data['taluk_ids'])}")
            print(f"  Town IDs: {len(jurisdiction_data['town_ids'])}")
            print(f"  Ward IDs: {len(jurisdiction_data['ward_ids'])}")
            print(f"  Block IDs: {len(jurisdiction_data['block_ids'])}")
            
            # Performance evaluation
            print("\n" + "="*80)
            print("Performance Evaluation:")
            print("="*80)
            
            if elapsed_ms < 100:
                print(f"🚀 EXCELLENT: {elapsed_ms:.2f}ms (< 100ms)")
                print("   Login should feel instant!")
            elif elapsed_ms < 300:
                print(f"✅ GOOD: {elapsed_ms:.2f}ms (< 300ms)")
                print("   Login is fast enough")
            elif elapsed_ms < 1000:
                print(f"⚠️  ACCEPTABLE: {elapsed_ms:.2f}ms (< 1 second)")
                print("   Login is noticeable but not too slow")
            else:
                print(f"❌ SLOW: {elapsed_ms:.2f}ms (> 1 second)")
                print("   Login feels sluggish - needs optimization")
            
            # Run multiple iterations to get average
            print("\n" + "-"*80)
            print("Running 5 iterations to get average performance...")
            print("-"*80)
            
            times = []
            for i in range(5):
                start = time.time()
                await get_officer_jurisdiction_ids(officer.id, db)
                elapsed = (time.time() - start) * 1000
                times.append(elapsed)
                print(f"  Iteration {i+1}: {elapsed:.2f}ms")
            
            avg_time = sum(times) / len(times)
            min_time = min(times)
            max_time = max(times)
            
            print(f"\n📊 Statistics:")
            print(f"  Average: {avg_time:.2f}ms")
            print(f"  Min: {min_time:.2f}ms")
            print(f"  Max: {max_time:.2f}ms")
            
            print("\n" + "="*80)
            if avg_time < 200:
                print("✅ LOGIN PERFORMANCE IS OPTIMIZED!")
            else:
                print("⚠️  Login could be faster - consider further optimization")
            print("="*80)
            
        finally:
            await db.close()
            break

if __name__ == "__main__":
    asyncio.run(test_jurisdiction_loading_performance())
