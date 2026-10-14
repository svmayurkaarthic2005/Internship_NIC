import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from sqlalchemy import create_engine, text
from backend.config import settings

engine = create_engine(settings.SYNC_DATABASE_URL)
with engine.connect() as conn:
    print('Columns in sub_div_patta_transfer_application_information_urban:')
    cols = [r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'sub_div_patta_transfer_application_information_urban'"))]
    print(cols)

    r = conn.execute(text("SELECT * FROM sub_div_patta_transfer_application_information_urban LIMIT 2")).fetchall()
    for row in r:
        print(dict(zip(cols, row)))
