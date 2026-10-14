import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from sqlalchemy import create_engine, text
from backend.config import settings

engine = create_engine(settings.SYNC_DATABASE_URL)
with engine.connect() as conn:
    print('full_field_patta_transfer_application_information remarks:')
    q = text("SELECT DISTINCT remarks, count(*) FROM full_field_patta_transfer_application_information WHERE remarks IS NOT NULL GROUP BY remarks")
    for r in conn.execute(q):
        print(f"  {r[0]}: {r[1]}")

    print('\nsub_div_patta_transfer_application_information_urban remarks:')
    q = text("SELECT DISTINCT remarks, count(*) FROM sub_div_patta_transfer_application_information_urban WHERE remarks IS NOT NULL GROUP BY remarks")
    for r in conn.execute(q):
        print(f"  {r[0]}: {r[1]}")
