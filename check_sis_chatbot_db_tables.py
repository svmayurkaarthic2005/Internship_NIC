"""Check if sis_chatbot_db has all required tables for the chatbot app"""
import asyncio
import sys
from pathlib import Path

import asyncpg

# The report prints check marks; a Windows console defaults to cp1252, which
# cannot encode them. Same guard as backend/main.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend" / "sample_db"))
from dbconn import conn_params

async def check_required_tables():
    print("=" * 100)
    print("CHECKING REQUIRED TABLES IN sis_chatbot_db")
    print("=" * 100)
    
    conn = await asyncpg.connect(**conn_params('sis_chatbot_db'))
    
    # Required tables for the chatbot app to work
    required_tables = [
        # Authentication
        ("sis_officers", "For user authentication and authorization"),
        
        # Core application data
        ("applications", "Main applications table"),
        ("applicants", "Applicant information"),
        
        # Geography
        ("district_unicode", "District master (TAMILNILAM dump; is the app's District)"),
        ("taluk", "Taluk master (TAMILNILAM dump; is the app's Taluk)"),
        ("towns", "Town master data"),
        ("wards", "Ward master data"),
        ("blocks", "Block master data"),
        
        # Survey data
        ("survey_numbers", "Survey number records"),
        ("sub_divisions", "Sub-division records"),
        ("owners", "Owner records"),
        
        # Supporting tables
        ("officer_jurisdictions", "Officer jurisdiction mappings"),
        ("field_visits", "Field visit records"),
        ("workflow_history", "Application workflow history"),
        ("application_documents", "Document tracking"),
        
        # Optional but useful
        ("chat_sessions", "Chat session tracking"),
        ("chat_messages", "Chat message history"),
    ]
    
    print("\n1. CHECKING CORE TABLES")
    print("-" * 100)
    
    missing_tables = []
    existing_tables = []
    
    for table_name, description in required_tables:
        try:
            count = await conn.fetchval(f'SELECT COUNT(*) FROM "{table_name}"')
            cols = await conn.fetch(f"""
                SELECT COUNT(*) as col_count
                FROM information_schema.columns
                WHERE table_name = '{table_name}'
            """)
            col_count = cols[0]['col_count']
            
            print(f"✅ {table_name:30s} - {count:,} rows, {col_count} columns - {description}")
            existing_tables.append(table_name)
        except Exception as e:
            print(f"❌ {table_name:30s} - MISSING - {description}")
            missing_tables.append((table_name, description))
    
    # Check for CSV-specific tables
    print("\n2. CSV-SPECIFIC TABLES (Additional data)")
    print("-" * 100)
    
    csv_tables = [
        "urban_application_log",
        "application_workflow_action",
        "isd_transfer_application_info",
        "nisd_transfer_application_info",
        "urban_parcel_register",
    ]
    
    for table_name in csv_tables:
        try:
            count = await conn.fetchval(f'SELECT COUNT(*) FROM "{table_name}"')
            print(f"📊 {table_name:40s} - {count:,} rows")
        except:
            print(f"⚠️  {table_name:40s} - Not found")
    
    await conn.close()
    
    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)
    
    print(f"\n✅ Tables found:    {len(existing_tables)}/{len(required_tables)}")
    print(f"❌ Tables missing:  {len(missing_tables)}/{len(required_tables)}")
    
    if missing_tables:
        print(f"\n⚠️  MISSING CRITICAL TABLES:")
        for table_name, description in missing_tables:
            print(f"   - {table_name}: {description}")
        print(f"\n❌ App will NOT work without these tables!")
        print(f"\nRECOMMENDATION:")
        print(f"   Run seed.py to create missing tables in sis_chatbot_db:")
        print(f"   python backend/seed.py")
    else:
        print(f"\n✅ All required tables exist in sis_chatbot_db")
        print(f"   App should work with this database!")
    
    return len(missing_tables) == 0

if __name__ == '__main__':
    success = asyncio.run(check_required_tables())
    exit(0 if success else 1)
