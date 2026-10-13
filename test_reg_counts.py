import asyncio, sys
sys.stdout.reconfigure(encoding='utf-8')
from backend.database import AsyncSessionLocal
from sqlalchemy import text

async def check():
    async with AsyncSessionLocal() as session:
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
            breakdown = []
            if r[2] > 0: breakdown.append(f"{r[2]} appr")
            if r[3] > 0: breakdown.append(f"{r[3]} pend")
            if r[4] > 0: breakdown.append(f"{r[4]} rej")
            bd_str = f" ({', '.join(breakdown)})" if breakdown else ""
            print(f"{r[0]}: {r[1]} files in Register{bd_str}")

asyncio.run(check())
