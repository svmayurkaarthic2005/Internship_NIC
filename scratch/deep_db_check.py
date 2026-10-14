import sys, os
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from sqlalchemy import create_engine, text
from backend.config import settings

engine = create_engine(settings.SYNC_DATABASE_URL)
with engine.connect() as conn:
    print('=== DETAILED DATABASE CHECK FOR ISD AND NISD ===')

    # Check distinct action_from, action_to, and any associated text/comments in all tables
    print('\n1. Raw application_workflow rows for NISD (sample 10):')
    q = text("""
        SELECT appl_id, action_from, action_to, action_date, remarks, action_status, update_by
        FROM application_workflow
        WHERE appl_id LIKE '%/0153/%'
        ORDER BY appl_id, slno
        LIMIT 25
    """)
    for r in conn.execute(q):
        print(f"  {r[0]} | from={r[1]} -> to={r[2]} | date={r[3]} | status={r[5]} | by={r[6]} | rem={r[4]}")

    print('\n2. Raw application_workflow rows for ISD (sample 10):')
    q = text("""
        SELECT appl_id, action_from, action_to, action_date, remarks, action_status, update_by
        FROM application_workflow
        WHERE appl_id LIKE '%/0154/%'
        ORDER BY appl_id, slno
        LIMIT 25
    """)
    for r in conn.execute(q):
        print(f"  {r[0]} | from={r[1]} -> to={r[2]} | date={r[3]} | status={r[5]} | by={r[6]} | rem={r[4]}")

    print('\n3. In appl_log_urban, check what descriptions / text appear for 0153 vs 0154:')
    q = text("""
        SELECT service_code, application_status, workflow_state, count(*)
        FROM appl_log_urban
        GROUP BY service_code, application_status, workflow_state
        ORDER BY service_code, application_status
    """)
    for r in conn.execute(q):
        print(f"  SC={r[0]} status={r[1]} state={r[2]}: count={r[3]}")

    print('\n4. Check if there are any mentions of Tahsildar or ZDT in full_field_patta_transfer_urban or sub_div_patta_transfer_urban:')
    for tbl in ['full_field_patta_transfer_urban', 'sub_div_patta_transfer_urban']:
        try:
            q = text(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{tbl}'")
            cols = [r[0] for r in conn.execute(q)]
            date_cols = [c for c in cols if 'date' in c or 'by' in c or 'dt' in c or 'status' in c or 'officer' in c]
            print(f"  {tbl} relevant columns: {date_cols}")
        except Exception as e:
            print(e)

    print('\n5. Check patta_transfers in ORM:')
    q = text("SELECT count(*), application_type, current_status FROM patta_transfers pt JOIN applications a ON pt.application_id = a.id GROUP BY application_type, current_status")
    for r in conn.execute(q):
        print(f"  patta_transfers: type={r[1]}, status={r[2]}: {r[0]}")

    q = text("SELECT tahsildar_signature_date, count(*) FROM patta_transfers GROUP BY (tahsildar_signature_date IS NOT NULL) count(*)")
    # let's just see non-null signature dates
    q = text("SELECT a.application_type, count(pt.tahsildar_signature_date), count(*) FROM patta_transfers pt JOIN applications a ON pt.application_id = a.id GROUP BY a.application_type")
    for r in conn.execute(q):
        print(f"  patta_transfers {r[0]}: signature_dates={r[1]} / total={r[2]}")

