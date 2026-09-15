"""
Check if database tables and CSV files mention joint owners
"""
import os
import asyncio
import asyncpg
from dotenv import load_dotenv
from pathlib import Path
import csv

load_dotenv()

async def check_joint_owners_in_db():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("❌ DATABASE_URL not found in .env")
        return
    
    db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    
    try:
        conn = await asyncpg.connect(db_url)
        
        print("=" * 80)
        print("CHECKING FOR JOINT OWNER FIELDS IN DATABASE")
        print("=" * 80)
        
        # Search for columns with "joint", "owner", "ownership" keywords
        query = """
        SELECT 
            table_name,
            column_name,
            data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
        AND (
            column_name ILIKE '%joint%'
            OR column_name ILIKE '%owner%'
            OR column_name ILIKE '%ownership%'
            OR column_name ILIKE '%co_owner%'
            OR column_name ILIKE '%co-owner%'
        )
        ORDER BY table_name, ordinal_position;
        """
        
        cols = await conn.fetch(query)
        
        if cols:
            print(f"\n✅ Found {len(cols)} owner-related columns:")
            print("-" * 80)
            
            current_table = None
            for col in cols:
                if col['table_name'] != current_table:
                    current_table = col['table_name']
                    print(f"\n📋 Table: {current_table}")
                
                print(f"   • {col['column_name']} ({col['data_type']})")
                
                # Get sample data for joint/ownership columns
                if 'joint' in col['column_name'].lower() or 'ownership' in col['column_name'].lower():
                    try:
                        sample_query = f"""
                        SELECT "{col['column_name']}", COUNT(*) as count
                        FROM {col['table_name']}
                        WHERE "{col['column_name']}" IS NOT NULL
                        GROUP BY "{col['column_name']}"
                        LIMIT 5;
                        """
                        samples = await conn.fetch(sample_query)
                        if samples:
                            print(f"      Sample values:")
                            for s in samples:
                                print(f"         '{s[col['column_name']]}' ({s['count']} records)")
                    except:
                        pass
        
        # Check owner tables specifically
        print("\n" + "=" * 80)
        print("OWNER TABLES ANALYSIS:")
        print("=" * 80)
        
        owner_tables = [
            'nisd_transfer_igrs_owner',
            'nisd_transfer_new_owner', 
            'nisd_transfer_old_owner',
            'nisd_transfer_return_owner',
            'survey_ownership',
            'owners'
        ]
        
        for table in owner_tables:
            check_query = f"""
            SELECT COUNT(*) as total_rows
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = '{table}';
            """
            exists = await conn.fetchval(check_query)
            
            if exists:
                count_query = f"SELECT COUNT(*) FROM {table}"
                count = await conn.fetchval(count_query)
                
                # Check if multiple owners per application
                if 'application_id' in [c['column_name'] for c in cols if c['table_name'] == table]:
                    multi_query = f"""
                    SELECT application_id, COUNT(*) as owner_count
                    FROM {table}
                    GROUP BY application_id
                    HAVING COUNT(*) > 1
                    LIMIT 5;
                    """
                    multi = await conn.fetch(multi_query)
                    
                    print(f"\n📋 {table}: {count} rows")
                    if multi:
                        print(f"   ✅ MULTIPLE OWNERS FOUND!")
                        print(f"   Applications with multiple owners:")
                        for m in multi:
                            print(f"      {m['application_id']}: {m['owner_count']} owners (JOINT OWNERSHIP)")
                    else:
                        print(f"   ❌ No applications with multiple owners")
        
        await conn.close()
        
    except Exception as e:
        print(f"❌ Database Error: {e}")
        import traceback
        traceback.print_exc()

def check_joint_owners_in_csv():
    print("\n" + "=" * 80)
    print("CHECKING FOR JOINT OWNER FIELDS IN CSV FILES")
    print("=" * 80)
    
    csv_dir = Path("backend/sample_table")
    
    if not csv_dir.exists():
        print(f"❌ Directory not found: {csv_dir}")
        return
    
    owner_csv_files = [
        'full_field_patta_transfer_igrs_owner_demo.csv',
        'full_field_patta_transfer_new_owner_demo.csv',
        'full_field_patta_transfer_old_owner_demo.csv',
        'full_field_patta_transfer_return_owner_demo.csv'
    ]
    
    for csv_file in owner_csv_files:
        csv_path = csv_dir / csv_file
        if not csv_path.exists():
            continue
        
        print(f"\n📂 {csv_file}")
        
        with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            
            # Check for joint/ownership columns
            joint_cols = [col for col in reader.fieldnames if 'joint' in col.lower() or 'ownership' in col.lower()]
            
            if joint_cols:
                print(f"   ✅ Joint owner columns found:")
                for col in joint_cols:
                    print(f"      • {col}")
            else:
                print(f"   ❌ No explicit 'joint' columns")
            
            # Check if owner_no column exists (indicates multiple owners)
            if 'owner_no' in reader.fieldnames or 'owner_number' in reader.fieldnames:
                print(f"   ✅ owner_no/owner_number column exists (indicates multiple owners possible)")
                
                # Count applications with multiple owners
                app_owners = {}
                for row in reader:
                    app_id = row.get('application_id', '')
                    if app_id:
                        app_owners[app_id] = app_owners.get(app_id, 0) + 1
                
                multi_owner_apps = {app: count for app, count in app_owners.items() if count > 1}
                
                if multi_owner_apps:
                    print(f"   ✅ JOINT OWNERS FOUND!")
                    print(f"   Applications with multiple owners: {len(multi_owner_apps)}")
                    print(f"   Sample applications with joint owners:")
                    for app, count in list(multi_owner_apps.items())[:5]:
                        print(f"      {app}: {count} owners")
                else:
                    print(f"   ❌ No applications with multiple owners")

async def main():
    print("🔍 COMPREHENSIVE SEARCH FOR JOINT OWNER DATA")
    print("=" * 80)
    
    await check_joint_owners_in_db()
    check_joint_owners_in_csv()
    
    print("\n" + "=" * 80)
    print("SUMMARY:")
    print("=" * 80)
    print("\nJoint owners are identified by:")
    print("  • Multiple rows with same application_id in owner tables")
    print("  • owner_no or owner_number field indicating sequence (1, 2, 3...)")
    print("  • ownership_share field showing percentage ownership")

if __name__ == "__main__":
    asyncio.run(main())
