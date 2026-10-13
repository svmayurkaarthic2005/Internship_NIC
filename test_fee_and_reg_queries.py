import asyncio, sys
sys.stdout.reconfigure(encoding='utf-8')
from backend.database import AsyncSessionLocal
from sqlalchemy import text, select
from backend.models import ServiceRegisterEntry

async def test_helpers():
    async with AsyncSessionLocal() as session:
        # Test fees
        res_fees = await session.execute(text("SELECT service_code, govt_fee, csc_fee, total_fee, sla_days, sla_days_ta FROM service_fees;"))
        fee_map = {}
        for r in res_fees.fetchall():
            fee_map[r[0]] = {
                "govt_fee": f"₹{r[1]:.2f}",
                "csc_fee": f"₹{r[2]:.2f}",
                "total_fee": f"₹{r[3]:.2f}" if r[3] is not None else f"₹{(r[1] + r[2]):.2f}",
                "sla_days": r[4],
                "sla_days_ta": r[5],
                "fee_display": f"Govt ₹{r[1]:.2f}, CSC ₹{r[2]:.2f}",
            }
        print("Dynamic Fee Map:", fee_map)

        # Test counts
        res_counts = await session.execute(text("""
            SELECT service_code, 
                   COUNT(*) as total,
                   COUNT(CASE WHEN status IN ('approved', 'completed') THEN 1 END) as approved,
                   COUNT(CASE WHEN status IN ('pending', 'open') THEN 1 END) as pending,
                   COUNT(CASE WHEN status = 'rejected' THEN 1 END) as rejected
            FROM service_register_entries
            GROUP BY service_code;
        """))
        reg_map = {}
        for r in res_counts.fetchall():
            bd = []
            if r[2] > 0: bd.append(f"{r[2]} appr")
            if r[3] > 0: bd.append(f"{r[3]} pend")
            if r[4] > 0: bd.append(f"{r[4]} rej")
            bd_str = f" ({', '.join(bd)})" if bd else ""
            reg_map[r[0]] = {
                "total": r[1],
                "approved": r[2],
                "pending": r[3],
                "rejected": r[4],
                "display": f"{r[1]} files in Register{bd_str}"
            }
        print("\nDynamic Register Counts (sample):")
        for k in ["0153", "0154", "0178", "0156"]:
            print(f"  {k}: {reg_map.get(k, {}).get('display', '0 files in register')}")

asyncio.run(test_helpers())
