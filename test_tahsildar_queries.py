"""
Test script to verify Tahsildar (ZDT/HQDT) data and queries
"""
import asyncio
import sys
from sqlalchemy import select, func, and_, desc
from backend.database import get_db
from backend.models import WorkflowHistory, Application

# These suites print Tamil. On Windows the console is cp1252 and the
# first Tamil character raises UnicodeEncodeError, which killed the run
# before any result was reported. Same guard the other suites carry.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

async def test_tahsildar_data():
    """Test Tahsildar-related data in database"""
    print("=" * 70)
    print("TAHSILDAR (ZDT/HQDT) DATA VERIFICATION")
    print("=" * 70)
    
    async for db in get_db():
        try:
            # 1. Check workflows involving Tahsildar stage
            print("\n1. WORKFLOW HISTORY - TAHSILDAR STAGE")
            print("-" * 70)
            
            # Applications forwarded TO Tahsildar
            to_tahsildar = await db.execute(
                select(func.count()).select_from(WorkflowHistory)
                .where(WorkflowHistory.to_stage == 'TAHSILDAR')
            )
            print(f"   Applications forwarded TO Tahsildar: {to_tahsildar.scalar()}")
            
            # Applications processed FROM Tahsildar
            from_tahsildar = await db.execute(
                select(func.count()).select_from(WorkflowHistory)
                .where(WorkflowHistory.from_stage == 'TAHSILDAR')
            )
            print(f"   Applications processed FROM Tahsildar: {from_tahsildar.scalar()}")
            
            # 2. Check applications at Tahsildar stage
            print("\n2. CURRENT APPLICATIONS AT TAHSILDAR STAGE")
            print("-" * 70)
            
            at_tahsildar = await db.execute(
                select(func.count()).select_from(Application)
                .where(Application.current_stage == 'TAHSILDAR')
            )
            print(f"   Applications currently at Tahsildar: {at_tahsildar.scalar()}")
            
            # 3. Check approved vs rejected by Tahsildar
            print("\n3. TAHSILDAR DECISIONS")
            print("-" * 70)
            
            approved = await db.execute(
                select(func.count()).select_from(Application)
                .where(and_(
                    Application.current_status == 'approved',
                    Application.current_stage == 'TAHSILDAR'
                ))
            )
            print(f"   Approved by Tahsildar: {approved.scalar()}")
            
            rejected = await db.execute(
                select(func.count()).select_from(Application)
                .where(and_(
                    Application.current_status == 'rejected',
                    Application.current_stage == 'TAHSILDAR'
                ))
            )
            print(f"   Rejected by Tahsildar: {rejected.scalar()}")
            
            # 4. Sample applications with Tahsildar workflow
            print("\n4. SAMPLE APPLICATIONS WITH TAHSILDAR WORKFLOW")
            print("-" * 70)
            
            sample_workflows = await db.execute(
                select(WorkflowHistory, Application.application_number)
                .join(Application, WorkflowHistory.application_id == Application.id)
                .where(or_(
                    WorkflowHistory.to_stage == 'TAHSILDAR',
                    WorkflowHistory.from_stage == 'TAHSILDAR'
                ))
                .order_by(WorkflowHistory.performed_at.desc())
                .limit(5)
            )
            workflows = sample_workflows.all()
            
            for wf, app_num in workflows:
                direction = "TO" if wf.to_stage == 'TAHSILDAR' else "FROM"
                print(f"   {app_num} - {direction} Tahsildar - {wf.performed_at.date() if wf.performed_at else 'N/A'} - Action: {wf.action or 'N/A'}")
            
            # 5. Check stage transitions involving Tahsildar
            print("\n5. STAGE TRANSITIONS - TAHSILDAR WORKFLOW")
            print("-" * 70)
            
            # Before Tahsildar (which stage sends to Tahsildar?)
            before_tahsildar = await db.execute(
                select(
                    WorkflowHistory.from_stage,
                    func.count().label('count')
                )
                .where(WorkflowHistory.to_stage == 'TAHSILDAR')
                .group_by(WorkflowHistory.from_stage)
                .order_by(desc('count'))
            )
            print("   Stages that forward TO Tahsildar:")
            for row in before_tahsildar:
                from_stage = row[0] or "INITIAL"
                print(f"      {from_stage} → TAHSILDAR: {row[1]} times")
            
            # After Tahsildar (where does Tahsildar send?)
            after_tahsildar = await db.execute(
                select(
                    WorkflowHistory.to_stage,
                    func.count().label('count')
                )
                .where(WorkflowHistory.from_stage == 'TAHSILDAR')
                .group_by(WorkflowHistory.to_stage)
                .order_by(desc('count'))
            )
            print("\n   Tahsildar forwards TO stages:")
            for row in after_tahsildar:
                to_stage = row[0] or "CLOSED"
                print(f"      TAHSILDAR → {to_stage}: {row[1]} times")
            
            print("\n" + "=" * 70)
            print("VERIFICATION COMPLETE")
            print("=" * 70)
            
        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()
        finally:
            break

# Add missing import
from sqlalchemy import or_

if __name__ == "__main__":
    asyncio.run(test_tahsildar_data())
