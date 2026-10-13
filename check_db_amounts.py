import asyncio
from backend.database import AsyncSessionLocal
from sqlalchemy import text

async def check():
    async with AsyncSessionLocal() as session:
        print("=== applications fee_amount ===")
        res = await session.execute(text("""
            SELECT application_type, fee_amount, COUNT(*) 
            FROM applications 
            GROUP BY application_type, fee_amount 
            ORDER BY application_type, fee_amount;
        """))
        for r in res.fetchall():
            print(f"  {r[0]}: fee_amount={r[1]}, count={r[2]}")

        print("\n=== applications sample fee records ===")
        res = await session.execute(text("""
            SELECT application_number, application_type, fee_amount, challan_number, payment_mode
            FROM applications 
            WHERE fee_amount IS NOT NULL 
            LIMIT 10;
        """))
        for r in res.fetchall():
            print(f"  {r[0]} ({r[1]}): fee_amount={r[2]}, challan={r[3]}, mode={r[4]}")

        for tbl in [
            'full_field_patta_transfer_application_information',
            'sub_div_patta_transfer_application_information_urban',
            'f_line_service_application_information_urban',
            'tslr_sketch_urban'
        ]:
            print(f"\n=== {tbl}.amount ===")
            try:
                res = await session.execute(text(f"""
                    SELECT amount, COUNT(*) FROM {tbl} GROUP BY amount;
                """))
                for r in res.fetchall():
                    print(f"  amount={r[0]}, count={r[1]}")
            except Exception as e:
                print(f"  error: {e}")

asyncio.run(check())
