import sys, os
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from sqlalchemy import create_engine, text
from backend.config import settings

engine = create_engine(settings.SYNC_DATABASE_URL)
with engine.connect() as conn:
    print('=== Connected to SISchatbot successfully! ===')
    
    # 1. Inspect Distinct transitions in application_workflow_demo for ISD vs NISD
    print('\n--- application_workflow_demo transitions by service_code ---')
    try:
        q = text("""
            SELECT 
                SUBSTRING(appl_id FROM 6 FOR 4) as sc,
                action_from, action_to, action_status, count(*)
            FROM application_workflow_demo
            GROUP BY sc, action_from, action_to, action_status
            ORDER BY sc, count(*) DESC
        """)
        for r in conn.execute(q).fetchall():
            print(f"SC={r[0]}: {r[1]} -> {r[2]} (status={r[3]}): {r[4]}")
    except Exception as e:
        print('Error querying application_workflow_demo:', e)

    # 2. Inspect Distinct remarks in application_workflow_demo
    print('\n--- Distinct remarks in application_workflow_demo ---')
    try:
        q = text("""
            SELECT DISTINCT action_from, action_to, remarks, count(*)
            FROM application_workflow_demo
            WHERE remarks IS NOT NULL AND remarks != '' AND remarks != '-'
            GROUP BY action_from, action_to, remarks
            ORDER BY count(*) DESC LIMIT 30
        """)
        for r in conn.execute(q).fetchall():
            print(f"{r[0]} -> {r[1]}: {r[2]} ({r[3]})")
    except Exception as e:
        print('Error querying remarks:', e)

    # 3. Inspect application_status and transfer extract wording in appl_log_urban_demo
    print('\n--- appl_log_urban_demo application_status wording ---')
    try:
        q = text("""
            SELECT service_code, application_status, workflow_state, count(*)
            FROM appl_log_urban_demo
            GROUP BY service_code, application_status, workflow_state
            ORDER BY service_code, application_status
        """)
        for r in conn.execute(q).fetchall():
            print(f"SC={r[0]}, status={r[1]}, state={r[2]}: {r[3]}")
    except Exception as e:
        print('Error:', e)

    # 4. Check workflow_history table
    print('\n--- workflow_history transitions by service_code ---')
    try:
        q = text("""
            SELECT 
                a.service_code,
                wh.from_stage, wh.to_stage, count(*)
            FROM workflow_history wh
            JOIN applications a ON wh.application_id = a.id
            GROUP BY a.service_code, wh.from_stage, wh.to_stage
            ORDER BY a.service_code, count(*) DESC
        """)
        for r in conn.execute(q).fetchall():
            print(f"SC={r[0]}: {r[1]} -> {r[2]}: {r[3]}")
    except Exception as e:
        print('Error:', e)

    # 5. Check patta_transfers columns and tahsildar columns
    print('\n--- patta_transfers columns mentioning tahsildar/zdt ---')
    try:
        q = text("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'patta_transfers'
        """)
        for r in conn.execute(q).fetchall():
            if any(k in r[0].lower() for k in ['tahsildar', 'zdt', 'officer', 'sign', 'approval', 'role', 'status']):
                print(f"patta_transfers column: {r[0]} ({r[1]})")
    except Exception as e:
        print('Error:', e)

    # 6. Check full_field_patta_transfer_urban_demo and sub_div_patta_transfer_urban_demo
    print('\n--- transfer demo table columns ---')
    for tbl in ['full_field_patta_transfer_urban_demo', 'sub_div_patta_transfer_urban_demo']:
        try:
            q = text(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{tbl}'")
            cols = [r[0] for r in conn.execute(q).fetchall()]
            rel = [c for c in cols if any(k in c.lower() for k in ['tahsildar', 'zdt', 'sign', 'officer', 'status', 'approval', 'stage', 'role'])]
            print(f"{tbl} relevant columns: {rel}")
        except Exception as e:
            print(f"Error on {tbl}:", e)
