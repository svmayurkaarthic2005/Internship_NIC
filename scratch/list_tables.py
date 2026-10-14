import sys, os
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from sqlalchemy import create_engine, text
from backend.config import settings

engine = create_engine(settings.SYNC_DATABASE_URL)
with engine.connect() as conn:
    res = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name"))
    tables = [r[0] for r in res.fetchall()]
    print('Tables in SISchatbot database:', len(tables))
    for t in tables:
        print(' -', t)
