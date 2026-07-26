"""
Quick verification script to check for duplicate application issues
Run this anytime to verify the system is clean
"""
import asyncio
import asyncpg


async def verify():
    conn = await asyncpg.connect(
        host="127.0.0.1",
        port=5432,
        user="postgres",
        password="Mayur@2005",
        database="sis_chatbot"
    )
    
    print("\n" + "=" * 70)
    print("DUPLICATE APPLICATION VERIFICATION")
    print("=" * 70 + "\n")
    
    all_good = True
    
    # Check 1: Multiple active apps per survey number
    duplicates = await conn.fetch("""
        SELECT 
            sn.survey_no,
            COUNT(*) as count,
            STRING_AGG(a.application_number, ', ') as apps
        FROM applications a
        JOIN survey_numbers sn ON a.survey_number_id = sn.id
        WHERE a.current_status IN ('pending', 'in_progress', 'escalated')
        GROUP BY sn.survey_no
        HAVING COUNT(*) > 1
    """)
    
    if duplicates:
        print("❌ ISSUE: Multiple active applications per survey number")
        for row in duplicates:
            print(f"   Survey {row['survey_no']}: {row['apps']}")
        all_good = False
    else:
        print("✅ No duplicate active applications per survey number")
    
    # Check 2: Field visits for rejected apps
    rejected_visits = await conn.fetchval("""
        SELECT COUNT(*)
        FROM field_visits fv
        JOIN applications a ON fv.application_id = a.id
        WHERE a.current_status = 'rejected'
        AND fv.status NOT IN ('cancelled', 'completed')
    """)
    
    if rejected_visits > 0:
        print(f"⚠️  WARNING: {rejected_visits} active field visits for rejected apps")
        all_good = False
    else:
        print("✅ No active field visits for rejected applications")
    
    # Check 3: Unique constraint exists
    constraint = await conn.fetchval("""
        SELECT COUNT(*)
        FROM pg_indexes
        WHERE tablename = 'applications'
        AND indexname = 'idx_unique_active_app_per_survey'
    """)
    
    if constraint == 0:
        print("❌ CRITICAL: Unique constraint missing!")
        all_good = False
    else:
        print("✅ Unique constraint exists (idx_unique_active_app_per_survey)")
    
    print("\n" + "=" * 70)
    if all_good:
        print("✅ ALL CHECKS PASSED - System is clean!")
    else:
        print("❌ ISSUES FOUND - Review the output above")
    print("=" * 70 + "\n")
    
    await conn.close()


if __name__ == "__main__":
    asyncio.run(verify())
