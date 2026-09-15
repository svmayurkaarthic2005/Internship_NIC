import asyncio
from backend.database import get_db
from sqlalchemy import text

async def test():
    db_gen = get_db()
    db = await db_gen.__anext__()
    res = await db.execute(text('SELECT a.application_number, p.name, p.mobile FROM applications a JOIN applicants p ON a.applicant_id = p.id WHERE a.can_number = ''202225473787'''))
    print(res.fetchall())
    await db_gen.aclose()

asyncio.run(test())
