"""Check the district table and officer jurisdictions.

Districts and taluks live in the TAMILNILAM master tables
(`district_unicode`, `taluk`); the app's own `districts` / `taluks` were
dropped as duplicates. `app_uid` is what officer_jurisdictions points at.
"""
from sqlalchemy import create_engine, text
from backend.config import settings

# Create sync engine from SYNC_DATABASE_URL
engine = create_engine(settings.SYNC_DATABASE_URL, echo=False)

with engine.connect() as conn:
    print("=" * 80)
    print("DISTRICTS TABLE")
    print("=" * 80)
    result = conn.execute(text("SELECT district_name, district_code FROM district_unicode "
         "ORDER BY district_code"))
    for row in result:
        print(f"District: {row[0]:<30} Code: {row[1]}")
    
    print("\n" + "=" * 80)
    print("OFFICER JURISDICTIONS BY TYPE")
    print("=" * 80)
    
    # Count by jurisdiction type
    result = conn.execute(text("""
        SELECT jurisdiction_type, COUNT(*) as count
        FROM officer_jurisdictions
        GROUP BY jurisdiction_type
        ORDER BY jurisdiction_type
    """))
    
    print("\nJurisdiction Type Distribution:")
    for row in result:
        print(f"  {row[0]:<15}: {row[1]} officers")
    
    # Show detailed officer jurisdictions
    result = conn.execute(text("""
        SELECT 
            o.employee_id,
            o.name,
            oj.jurisdiction_type,
            d.district_name,
            d.district_code,
            t.taluk_ename as taluk_name,
            tw.name as town_name,
            w.ward_number,
            b.block_number
        FROM officer_jurisdictions oj
        JOIN sis_officers o ON o.id = oj.officer_id
        LEFT JOIN district_unicode d ON d.app_uid = oj.district_id
        LEFT JOIN taluk t ON t.app_uid = oj.taluk_id
        LEFT JOIN towns tw ON tw.id = oj.town_id
        LEFT JOIN wards w ON w.id = oj.ward_id
        LEFT JOIN blocks b ON b.id = oj.block_id
        ORDER BY o.employee_id
    """))
    
    print("\n" + "=" * 80)
    print("DETAILED OFFICER JURISDICTIONS")
    print("=" * 80)
    for row in result:
        print(f"\n{row[0]} - {row[1]}")
        print(f"  Type: {row[2]}")
        if row[3]:
            print(f"  District: {row[3]} ({row[4]})")
        if row[5]:
            print(f"  Taluk: {row[5]}")
        if row[6]:
            print(f"  Town: {row[6]}")
        if row[7]:
            print(f"  Ward: {row[7]}")
        if row[8]:
            print(f"  Block: {row[8]}")

print("\n" + "=" * 80)
