import asyncio
from backend.database import AsyncSessionLocal
from sqlalchemy import text

async def check():
    async with AsyncSessionLocal() as session:
        print("=== appl_log_urban columns ===")
        res = await session.execute(text("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'appl_log_urban'
            ORDER BY ordinal_position;
        """))
        for r in res.fetchall():
            print(f"  {r[0]} ({r[1]})")

        print("\n=== appl_log_urban sample data ===")
        res = await session.execute(text("""
            SELECT * FROM appl_log_urban LIMIT 5;
        """))
        rows = res.fetchall()
        for r in rows:
            print(dict(r._mapping))

        print("\n=== Distinct charges in appl_log_urban ===")
        res = await session.execute(text("""
            SELECT govt_charge, csc_charge, COUNT(*) 
            FROM appl_log_urban 
            GROUP BY govt_charge, csc_charge;
        """))
        for r in res.fetchall():
            print(f"  govt_charge={r[0]}, csc_charge={r[1]}, count={r[2]}")

        print("\n=== service_register_entries columns & counts ===")
        res = await session.execute(text("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'service_register_entries'
            ORDER BY ordinal_position;
        """))
        for r in res.fetchall():
            print(f"  {r[0]} ({r[1]})")

        print("\n=== service_register_entries summary by service_code ===")
        res = await session.execute(text("""
            SELECT service_code, 
                   COUNT(*) as total,
                   COUNT(CASE WHEN status IN ('approved', 'completed') THEN 1 END) as approved,
                   COUNT(CASE WHEN status IN ('pending', 'open') THEN 1 END) as pending,
                   COUNT(CASE WHEN status = 'rejected' THEN 1 END) as rejected
            FROM service_register_entries
            GROUP BY service_code
            ORDER BY service_code;
        """))
        for r in res.fetchall():
            print(f"  code={r[0]}: total={r[1]}, approved={r[2]}, pending={r[3]}, rejected={r[4]}")

asyncio.run(check())
