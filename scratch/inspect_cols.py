import sys, os
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from sqlalchemy import create_engine, text
from backend.config import settings

engine = create_engine(settings.SYNC_DATABASE_URL)
with engine.connect() as conn:
    print('Columns in applications:')
    res = conn.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'applications'"))
    for r in res:
        print(f"  {r[0]} ({r[1]})")

    print('\nColumns in workflow_history:')
    res = conn.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'workflow_history'"))
    for r in res:
        print(f"  {r[0]} ({r[1]})")

    print('\nColumns in application_workflow:')
    res = conn.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'application_workflow'"))
    for r in res:
        print(f"  {r[0]} ({r[1]})")
