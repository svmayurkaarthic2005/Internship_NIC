import sys, os
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from sqlalchemy import create_engine, text
from backend.config import settings

engine = create_engine(settings.SYNC_DATABASE_URL)
with engine.connect() as conn:
    print('Columns in appl_log_urban:')
    res = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'appl_log_urban'"))
    cols = [r[0] for r in res]
    print(cols)
    print('\nSample 3 rows of appl_log_urban:')
    res = conn.execute(text("SELECT * FROM appl_log_urban LIMIT 3"))
    for r in res:
        print(dict(zip(cols, r)))
