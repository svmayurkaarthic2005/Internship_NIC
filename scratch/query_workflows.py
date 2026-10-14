import sys, os
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from sqlalchemy import create_engine, text
from backend.config import settings

engine = create_engine(settings.SYNC_DATABASE_URL)
with engine.connect() as conn:
    print('=== QUERYING SISchatbot DATABASE ===\n')

    # 1. Total applications by application_type
    print('--- Applications by application_type ---')
    res = conn.execute(text("SELECT application_type, current_status, count(*) FROM applications GROUP BY application_type, current_status ORDER BY application_type, count(*) DESC"))
    for r in res:
        print(f"Type {r[0]}, status {r[1]}: {r[2]}")

    # 2. Distinct workflow paths in workflow_history for NISD vs ISD
    print('\n--- workflow_history paths by application_type ---')
    q = text("""
        WITH paths AS (
            SELECT 
                a.id,
                a.application_number,
                a.application_type,
                string_agg(wh.from_stage || '->' || wh.to_stage, ', ' ORDER BY wh.performed_at) as path
            FROM applications a
            JOIN workflow_history wh ON a.id = wh.application_id
            GROUP BY a.id, a.application_number, a.application_type
        )
        SELECT application_type, path, count(*)
        FROM paths
        GROUP BY application_type, path
        ORDER BY application_type, count(*) DESC
    """)
    for r in conn.execute(q):
        print(f"Type {r[0]}: {r[1]} ({r[2]})")

    # 3. Raw layer transitions in application_workflow table for 0153 (NISD) vs 0154 (ISD)
    print('\n--- Raw application_workflow action_from -> action_to for 0153 (NISD) ---')
    q = text("""
        SELECT action_from, action_to, action_status, count(*)
        FROM application_workflow
        WHERE appl_id LIKE '%/0153/%'
        GROUP BY action_from, action_to, action_status
        ORDER BY count(*) DESC
    """)
    for r in conn.execute(q):
        print(f"  {r[0]} -> {r[1]} (status {r[2]}): {r[3]}")

    print('\n--- Raw application_workflow action_from -> action_to for 0154 (ISD) ---')
    q = text("""
        SELECT action_from, action_to, action_status, count(*)
        FROM application_workflow
        WHERE appl_id LIKE '%/0154/%'
        GROUP BY action_from, action_to, action_status
        ORDER BY count(*) DESC
    """)
    for r in conn.execute(q):
        print(f"  {r[0]} -> {r[1]} (status {r[2]}): {r[3]}")

    # 4. Check all distinct roles across all application_workflow rows
    print('\n--- Distinct action_from and action_to in application_workflow across all service codes ---')
    q = text("""
        SELECT DISTINCT action_from FROM application_workflow
        UNION
        SELECT DISTINCT action_to FROM application_workflow
        ORDER BY 1
    """)
    roles = [r[0] for r in conn.execute(q)]
    print("All distinct roles in application_workflow:", roles)

    # 5. Check what role codes mean in the database or remarks
    print('\n--- Distinct remarks in application_workflow for 0153 vs 0154 ---')
    q = text("""
        SELECT DISTINCT action_from, action_to, remarks, count(*) 
        FROM application_workflow 
        WHERE (appl_id LIKE '%/0153/%' OR appl_id LIKE '%/0154/%')
        AND remarks NOT IN ('', '-') 
        GROUP BY action_from, action_to, remarks 
        ORDER BY count(*) DESC LIMIT 30
    """)
    for r in conn.execute(q):
        print(f"  {r[0]} -> {r[1]}: '{r[2]}' ({r[3]})")

    # 6. Check update_by in application_workflow
    print('\n--- Distinct update_by for 0153 vs 0154 ---')
    q = text("""
        SELECT SUBSTRING(appl_id FROM 6 FOR 4) as sc, action_from, action_to, update_by, count(*)
        FROM application_workflow
        WHERE appl_id LIKE '%/0153/%' OR appl_id LIKE '%/0154/%'
        GROUP BY sc, action_from, action_to, update_by
        ORDER BY sc, count(*) DESC LIMIT 30
    """)
    for r in conn.execute(q):
        print(f"  SC {r[0]}: {r[1]} -> {r[2]} (by '{r[3]}'): {r[4]}")
