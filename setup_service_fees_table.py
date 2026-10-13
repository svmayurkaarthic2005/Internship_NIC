import asyncio, sys
sys.stdout.reconfigure(encoding='utf-8')
from backend.database import AsyncSessionLocal
from sqlalchemy import text

async def setup_service_fees():
    async with AsyncSessionLocal() as session:
        # Create service_fees table if it doesn't exist
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS service_fees (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                service_code VARCHAR(10) UNIQUE NOT NULL,
                service_name VARCHAR(255) NOT NULL,
                govt_fee NUMERIC(10, 2) NOT NULL,
                csc_fee NUMERIC(10, 2) NOT NULL DEFAULT 60.00,
                total_fee NUMERIC(10, 2),
                sla_days VARCHAR(50),
                sla_days_ta VARCHAR(50),
                remarks TEXT,
                updated_at TIMESTAMPTZ DEFAULT now()
            );
        """))
        
        # Insert or update default official rates
        await session.execute(text("""
            INSERT INTO service_fees (service_code, service_name, govt_fee, csc_fee, total_fee, sla_days, sla_days_ta, remarks)
            VALUES 
                ('0153', 'Not Involving Sub-Division (NISD)', 100.00, 60.00, 160.00, '15-20 working days', '15-20 வேலை நாட்கள்', 'Standard Patta Transfer full field'),
                ('0154', 'Involving Sub-Division (ISD)', 400.00, 60.00, 460.00, '30-35 working days', '30-35 வேலை நாட்கள்', 'Patta Transfer with Sub-Division and mandatory field inspection'),
                ('0156', 'TSLR Extract Copy', 50.00, 60.00, 110.00, '7-10 working days', '7-10 வேலை நாட்கள்', 'Town Survey Land Register extract copy'),
                ('0178', 'F-Line Demarcation Survey', 800.00, 60.00, 860.00, '30 working days', '30 வேலை நாட்கள்', 'Boundary demarcation survey')
            ON CONFLICT (service_code) DO UPDATE SET
                service_name = EXCLUDED.service_name,
                govt_fee = EXCLUDED.govt_fee,
                csc_fee = EXCLUDED.csc_fee,
                total_fee = EXCLUDED.total_fee,
                sla_days = EXCLUDED.sla_days,
                sla_days_ta = EXCLUDED.sla_days_ta,
                updated_at = now();
        """))
        await session.commit()
        
        # Verify
        res = await session.execute(text("SELECT service_code, service_name, govt_fee, csc_fee, total_fee FROM service_fees ORDER BY service_code;"))
        print("Rows in service_fees:")
        for r in res.fetchall():
            print(f"  {r[0]}: Govt ₹{r[2]:.2f}, CSC ₹{r[3]:.2f} (Total ₹{r[4]:.2f}) - {r[1]}")

asyncio.run(setup_service_fees())
