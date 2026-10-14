import sys
sys.path.insert(0, '.')
from sqlalchemy import create_engine, text
from backend.config import settings

engine = create_engine(settings.SYNC_DATABASE_URL)
with engine.connect() as conn:
    print('full_field_patta_transfer_urban columns:')
    cols1 = [r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'full_field_patta_transfer_urban'"))]
    print(cols1)
    
    print('\nsub_div_patta_transfer_urban columns:')
    cols2 = [r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'sub_div_patta_transfer_urban'"))]
    print(cols2)

    # Let's see some sample rows
    print('\nSample row full_field_patta_transfer_urban:')
    r = conn.execute(text("SELECT * FROM full_field_patta_transfer_urban LIMIT 1")).fetchone()
    if r:
        print(dict(zip(cols1, r)))

    print('\nSample row sub_div_patta_transfer_urban:')
    r = conn.execute(text("SELECT * FROM sub_div_patta_transfer_urban LIMIT 1")).fetchone()
    if r:
        print(dict(zip(cols2, r)))
