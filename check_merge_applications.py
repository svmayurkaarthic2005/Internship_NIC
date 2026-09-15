"""Check for MERGE applications in the database and CSV files"""
import asyncio
from backend.database import get_db
from sqlalchemy import select, func
from backend.models import Application
import os
import csv

async def check_db_merge():
    """Check database for MERGE applications"""
    print("="*80)
    print("Checking Database for MERGE Applications")
    print("="*80)
    
    async for db in get_db():
        try:
            # Count by application type
            query = select(
                Application.application_type, 
                func.count()
            ).group_by(Application.application_type)
            
            result = await db.execute(query)
            
            print("\nApplication Types in Database:")
            total = 0
            merge_count = 0
            for app_type, count in result.fetchall():
                print(f"  {app_type}: {count}")
                total += count
                if app_type == "MERGE":
                    merge_count = count
            
            print(f"\nTotal Applications: {total}")
            print(f"MERGE Applications: {merge_count}")
            
            # If MERGE exists, show some examples
            if merge_count > 0:
                print("\nSample MERGE Applications:")
                query = select(Application).where(
                    Application.application_type == "MERGE"
                ).limit(5)
                result = await db.execute(query)
                apps = result.scalars().all()
                
                for app in apps:
                    print(f"  - {app.application_number} | Status: {app.current_status}")
            
        finally:
            await db.close()
            break

def check_csv_merge():
    """Check CSV files for MERGE applications"""
    print("\n" + "="*80)
    print("Checking CSV Files for MERGE Applications")
    print("="*80)
    
    csv_dir = "backend/sample_table"
    merge_files = []
    
    for filename in os.listdir(csv_dir):
        if filename.endswith('.csv'):
            filepath = os.path.join(csv_dir, filename)
            
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                    
                    # Check for MERGE-related columns
                    merge_count = 0
                    for row in rows:
                        # Check service_code column
                        if 'service_code' in row and row['service_code'] == '0155':
                            merge_count += 1
                        # Check application_type column
                        elif 'application_type' in row and 'MERGE' in row.get('application_type', '').upper():
                            merge_count += 1
                    
                    if merge_count > 0:
                        merge_files.append((filename, merge_count, len(rows)))
                        
            except Exception as e:
                continue
    
    if merge_files:
        print("\nCSV Files Containing MERGE Applications:")
        for filename, merge_count, total in merge_files:
            print(f"  {filename}: {merge_count}/{total} rows")
    else:
        print("\n❌ No MERGE applications found in CSV files")
        print("   Note: MERGE applications use service code 0155")

if __name__ == "__main__":
    asyncio.run(check_db_merge())
    check_csv_merge()
    
    print("\n" + "="*80)
    print("Summary:")
    print("  MERGE = Subdivision Merger (service code 0155)")
    print("  Used when multiple survey numbers are being merged into one")
    print("="*80)
