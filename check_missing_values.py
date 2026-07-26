"""
Deep check for missing/NULL values across all critical database fields
"""
import asyncio
import asyncpg
from collections import defaultdict


async def check_missing_values():
    conn = await asyncpg.connect(
        host="127.0.0.1",
        port=5432,
        user="postgres",
        password="Mayur@2005",
        database="sis_chatbot"
    )
    
    print("=" * 80)
    print("COMPREHENSIVE MISSING VALUE ANALYSIS")
    print("=" * 80)
    print()
    
    issues = []
    
    # 1. APPLICATIONS TABLE
    print("1️⃣  APPLICATIONS TABLE")
    print("-" * 80)
    
    # Check for NULL critical fields in applications
    app_checks = {
        "application_number": "Application number is NULL",
        "application_type": "Application type is NULL",
        "current_status": "Current status is NULL",
        "current_stage": "Current stage is NULL",
        "submission_date": "Submission date is NULL",
        "assigned_officer_id": "Assigned officer is NULL",
        "survey_number_id": "Survey number is NULL",
        "applicant_id": "Applicant is NULL"
    }
    
    for field, description in app_checks.items():
        count = await conn.fetchval(f"""
            SELECT COUNT(*) 
            FROM applications 
            WHERE {field} IS NULL
        """)
        
        if count > 0:
            print(f"  ❌ {description}: {count} applications")
            issues.append(f"applications.{field}: {count} NULL values")
            
            # Get sample records
            samples = await conn.fetch(f"""
                SELECT application_number, current_status, application_type
                FROM applications
                WHERE {field} IS NULL
                LIMIT 3
            """)
            for s in samples:
                print(f"     Sample: {s['application_number']} ({s['application_type']}, {s['current_status']})")
        else:
            print(f"  ✅ {field}: No NULL values")
    
    print()
    
    # 2. SURVEY NUMBERS
    print("2️⃣  SURVEY_NUMBERS TABLE")
    print("-" * 80)
    
    survey_checks = {
        "survey_no": "Survey number is NULL",
        "block_id": "Block is NULL"
    }
    
    for field, description in survey_checks.items():
        count = await conn.fetchval(f"""
            SELECT COUNT(*) 
            FROM survey_numbers 
            WHERE {field} IS NULL
        """)
        
        if count > 0:
            print(f"  ❌ {description}: {count} survey numbers")
            issues.append(f"survey_numbers.{field}: {count} NULL values")
        else:
            print(f"  ✅ {field}: No NULL values")
    
    print()
    
    # 3. MERGE APPLICATIONS WITHOUT SUBDIVISIONS
    print("3️⃣  MERGE APPLICATIONS - SUBDIVISIONS")
    print("-" * 80)
    
    merge_without_subdivisions = await conn.fetch("""
        SELECT 
            a.application_number,
            a.current_status,
            a.application_type,
            sn.survey_no,
            COUNT(asd.id) as subdivision_count
        FROM applications a
        JOIN survey_numbers sn ON a.survey_number_id = sn.id
        LEFT JOIN application_sub_divisions asd ON a.id = asd.application_id
        WHERE a.application_type = 'MERGE'
        AND a.current_status NOT IN ('rejected', 'approved')
        GROUP BY a.application_number, a.current_status, a.application_type, sn.survey_no
        HAVING COUNT(asd.id) = 0
        ORDER BY a.application_number
    """)
    
    if merge_without_subdivisions:
        print(f"  ⚠️  Found {len(merge_without_subdivisions)} active MERGE apps without subdivisions:")
        for row in merge_without_subdivisions:
            print(f"     {row['application_number']} (Survey {row['survey_no']}, {row['current_status']})")
        issues.append(f"MERGE applications without subdivisions: {len(merge_without_subdivisions)}")
    else:
        print("  ✅ All active MERGE applications have subdivisions")
    
    print()
    
    # 4. FIELD VISITS
    print("4️⃣  FIELD_VISITS TABLE")
    print("-" * 80)
    
    field_visit_checks = {
        "application_id": "Application ID is NULL",
        "officer_id": "Officer ID is NULL",
        "status": "Status is NULL"
    }
    
    for field, description in field_visit_checks.items():
        count = await conn.fetchval(f"""
            SELECT COUNT(*) 
            FROM field_visits 
            WHERE {field} IS NULL
        """)
        
        if count > 0:
            print(f"  ❌ {description}: {count} field visits")
            issues.append(f"field_visits.{field}: {count} NULL values")
        else:
            print(f"  ✅ {field}: No NULL values")
    
    # Check for scheduled visits without dates
    scheduled_no_date = await conn.fetchval("""
        SELECT COUNT(*) 
        FROM field_visits 
        WHERE status IN ('scheduled', 'rescheduled')
        AND scheduled_date IS NULL
    """)
    
    if scheduled_no_date > 0:
        print(f"  ⚠️  Scheduled visits without date: {scheduled_no_date}")
        issues.append(f"field_visits: {scheduled_no_date} scheduled visits without date")
    else:
        print(f"  ✅ All scheduled visits have dates")
    
    print()
    
    # 5. BLOCKS, WARDS, TOWNS, TALUKS, DISTRICTS
    print("5️⃣  GEOGRAPHIC HIERARCHY")
    print("-" * 80)
    
    geo_checks = [
        ("blocks", "block_number", "Block number"),
        ("blocks", "ward_id", "Ward reference"),
        ("wards", "ward_number", "Ward number"),
        ("wards", "town_id", "Town reference"),
        ("towns", "name", "Town name"),
        ("towns", "taluk_id", "Taluk reference"),
        ("taluks", "name", "Taluk name"),
        ("taluks", "district_id", "District reference"),
        ("districts", "name", "District name")
    ]
    
    for table, field, description in geo_checks:
        count = await conn.fetchval(f"""
            SELECT COUNT(*) 
            FROM {table} 
            WHERE {field} IS NULL
        """)
        
        if count > 0:
            print(f"  ❌ {table}.{field} ({description}): {count} NULL values")
            issues.append(f"{table}.{field}: {count} NULL values")
        else:
            print(f"  ✅ {table}.{field}: No NULL values")
    
    print()
    
    # 6. SIS OFFICERS
    print("6️⃣  SIS_OFFICERS TABLE")
    print("-" * 80)
    
    officer_checks = {
        "name": "Officer name is NULL",
        "email": "Email is NULL",
        "employee_id": "Employee ID is NULL",
        "designation": "Designation is NULL"
    }
    
    for field, description in officer_checks.items():
        count = await conn.fetchval(f"""
            SELECT COUNT(*) 
            FROM sis_officers 
            WHERE {field} IS NULL
        """)
        
        if count > 0:
            print(f"  ❌ {description}: {count} officers")
            issues.append(f"sis_officers.{field}: {count} NULL values")
        else:
            print(f"  ✅ {field}: No NULL values")
    
    # Check for officers without jurisdiction
    no_jurisdiction = await conn.fetch("""
        SELECT o.name, o.email, o.designation
        FROM sis_officers o
        LEFT JOIN officer_jurisdictions oj ON o.id = oj.officer_id
        WHERE oj.id IS NULL
    """)
    
    if no_jurisdiction:
        print(f"  ⚠️  Officers without jurisdiction: {len(no_jurisdiction)}")
        for officer in no_jurisdiction:
            print(f"     {officer['name']} ({officer['designation']})")
        issues.append(f"Officers without jurisdiction: {len(no_jurisdiction)}")
    else:
        print(f"  ✅ All officers have jurisdiction assigned")
    
    # Check for officer jurisdictions with NULL type or references
    invalid_jurisdictions = await conn.fetch("""
        SELECT 
            o.name,
            oj.jurisdiction_type,
            oj.district_id,
            oj.taluk_id,
            oj.town_id,
            oj.ward_id,
            oj.block_id
        FROM officer_jurisdictions oj
        JOIN sis_officers o ON oj.officer_id = o.id
        WHERE 
            oj.jurisdiction_type IS NULL OR
            (oj.jurisdiction_type = 'district' AND oj.district_id IS NULL) OR
            (oj.jurisdiction_type = 'taluk' AND oj.taluk_id IS NULL) OR
            (oj.jurisdiction_type = 'town' AND oj.town_id IS NULL) OR
            (oj.jurisdiction_type = 'ward' AND oj.ward_id IS NULL) OR
            (oj.jurisdiction_type = 'block' AND oj.block_id IS NULL)
    """)
    
    if invalid_jurisdictions:
        print(f"  ⚠️  Jurisdictions with mismatched references: {len(invalid_jurisdictions)}")
        for j in invalid_jurisdictions:
            print(f"     {j['name']} - Type: {j['jurisdiction_type'] or 'NULL'}")
        issues.append(f"Invalid officer jurisdictions: {len(invalid_jurisdictions)}")
    else:
        print(f"  ✅ All officer jurisdictions have proper references")
    
    print()
    
    # 7. APPLICANTS
    print("7️⃣  APPLICANTS TABLE")
    print("-" * 80)
    
    applicant_checks = {
        "name": "Applicant name is NULL",
        "mobile": "Mobile number is NULL"
    }
    
    for field, description in applicant_checks.items():
        count = await conn.fetchval(f"""
            SELECT COUNT(*) 
            FROM applicants 
            WHERE {field} IS NULL
        """)
        
        if count > 0:
            print(f"  ❌ {description}: {count} applicants")
            issues.append(f"applicants.{field}: {count} NULL values")
            
            # Get sample
            samples = await conn.fetch(f"""
                SELECT id, name, mobile
                FROM applicants
                WHERE {field} IS NULL
                LIMIT 3
            """)
            for s in samples:
                print(f"     Sample: {s['name'] or 'N/A'} - {s['mobile'] or 'N/A'}")
        else:
            print(f"  ✅ {field}: No NULL values")
    
    print()
    
    # 8. CHECK FOR EMPTY STRINGS (not NULL, but empty)
    print("8️⃣  EMPTY STRING VALUES")
    print("-" * 80)
    
    empty_string_checks = [
        ("applications", "application_number", "Application number"),
        ("applicants", "name", "Applicant name"),
        ("applicants", "mobile", "Mobile number"),
        ("survey_numbers", "survey_no", "Survey number")
    ]
    
    for table, field, description in empty_string_checks:
        count = await conn.fetchval(f"""
            SELECT COUNT(*) 
            FROM {table} 
            WHERE {field} = '' OR TRIM({field}) = ''
        """)
        
        if count > 0:
            print(f"  ⚠️  {table}.{field} ({description}): {count} empty strings")
            issues.append(f"{table}.{field}: {count} empty strings")
        else:
            print(f"  ✅ {table}.{field}: No empty strings")
    
    print()
    
    # SUMMARY
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print()
    
    if issues:
        print(f"❌ Found {len(issues)} issue(s):")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")
    else:
        print("✅ No missing or NULL values found! Database is complete.")
    
    print()
    
    await conn.close()
    
    return issues


if __name__ == "__main__":
    asyncio.run(check_missing_values())
