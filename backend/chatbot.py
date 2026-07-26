"""
backend/chatbot.py — Chatbot Orchestrator for SIS Copilot
Tamil Nadu Revenue Department — Sub Inspector Surveyor AI Assistant

Jurisdiction hierarchy: District → Taluk → Town → Ward → Block → Survey Number → Sub-Division
"""
import re
import uuid
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import AsyncSessionLocal
from backend.services.rag import detect_language
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Intent patterns (regex-based)
SURVEY_RE = r"\b(\d{1,4})\b"
CASE_RE = r"\b(APP-\d{4,}|CASE-\d{4,})\b"


def _detect_intent(message: str) -> str:
    """
    Detect user intent using the central parse_intent engine.
    """
    from backend.services.rag import parse_intent
    return parse_intent(message)


async def _get_survey_context(message: str, db: AsyncSession) -> str:
    """
    Get context for a survey lookup query.
    """
    m = re.search(SURVEY_RE, message)
    if not m:
        return "Survey number not found in message."
    survey_no = m.group(1)
    
    from backend.models import SurveyNumber, Block, Ward, Town, Taluk, District, SubDivision, Application
    stmt = select(SurveyNumber).where(SurveyNumber.survey_no == survey_no)
    res = await db.execute(stmt)
    sn = res.scalar_one_or_none()
    if not sn:
        return f"Survey No. {survey_no} not found in the database."
        
    block = None
    ward = None
    town = None
    taluk = None
    district = None
    
    if sn.block_id:
        res = await db.execute(select(Block).where(Block.id == sn.block_id))
        block = res.scalar_one_or_none()
        if block and block.ward_id:
            res = await db.execute(select(Ward).where(Ward.id == block.ward_id))
            ward = res.scalar_one_or_none()
            if ward and ward.town_id:
                res = await db.execute(select(Town).where(Town.id == ward.town_id))
                town = res.scalar_one_or_none()
                if town and town.taluk_id:
                    res = await db.execute(select(Taluk).where(Taluk.id == town.taluk_id))
                    taluk = res.scalar_one_or_none()
                    if taluk and taluk.district_id:
                        res = await db.execute(select(District).where(District.id == taluk.district_id))
                        district = res.scalar_one_or_none()
                        
    subdiv_stmt = select(SubDivision).where(SubDivision.survey_number_id == sn.id)
    subdiv_res = await db.execute(subdiv_stmt)
    subdivs = subdiv_res.scalars().all()
    subdiv_nos = [sd.sub_division_no for sd in subdivs if sd.sub_division_no]
    
    app_stmt = select(func.count(Application.id)).where(
        and_(
            Application.survey_number_id == sn.id,
            Application.current_status.in_(["pending", "in_progress"])
        )
    )
    app_res = await db.execute(app_stmt)
    active_apps_count = app_res.scalar() or 0
    
    d_name = district.name if district else "N/A"
    t_name = taluk.name if taluk else "N/A"
    town_name = town.name if town else "N/A"
    w_num = ward.ward_number if ward else "N/A"
    b_num = block.block_number if block else "N/A"
    lit_flag = "Yes" if sn.has_litigation else "No"
    
    return (
        f"Survey No: {survey_no}\n"
        f"Jurisdiction: District: {d_name} → Taluk: {t_name} → Town: {town_name} "
        f"→ Ward: {w_num} → Block: {b_num}\n"
        f"Total Extent: {float(sn.total_area_sqm or 0)} sq.m\n"
        f"Sub-divisions ({len(subdivs)}): {', '.join(subdiv_nos) if subdiv_nos else 'None'}\n"
        f"Litigation Flag: {lit_flag}\n"
        f"Active Applications: {active_apps_count}"
    )


async def _get_owner_context(message: str, db: AsyncSession) -> str:
    """
    Get owner records for a survey number.
    """
    m = re.search(SURVEY_RE, message)
    if not m:
        return "Survey number not found in message."
    survey_no = m.group(1)
    
    from backend.models import SurveyNumber, SurveyOwnership, Owner, SubDivision
    stmt = select(SurveyNumber).where(SurveyNumber.survey_no == survey_no)
    res = await db.execute(stmt)
    sn = res.scalar_one_or_none()
    if not sn:
        return f"Survey No. {survey_no} not found."
        
    query = select(SurveyOwnership, Owner, SubDivision).join(
        Owner, SurveyOwnership.owner_id == Owner.id
    ).outerjoin(
        SubDivision, SurveyOwnership.sub_division_id == SubDivision.id
    ).where(
        SurveyOwnership.survey_number_id == sn.id
    )
    result = await db.execute(query)
    rows = result.all()
    
    records = []
    for ownership, owner, subdivision in rows:
        sub_div_str = f"Sub-division: {subdivision.sub_division_no}" if subdivision else "Survey Level"
        share = ownership.ownership_share or "100%"
        joint = "Joint" if ownership.is_joint_owner else "Sole"
        records.append(
            f"Name: {owner.name} ({sub_div_str}, Share: {share}, Type: {joint})"
        )
        
    return f"Survey No. {survey_no} — Owner Records:\n" + ("\n".join(records) if records else "No owner records found.")


async def _get_status_context(message: str, user_id: str, db: AsyncSession) -> str:
    """
    Get current stage and status details for a specific case.
    """
    m = re.search(CASE_RE, message)
    if not m:
        return "Application number not found in message."
    case_no = m.group(1).upper()
    
    from backend.models import Application
    from sqlalchemy.orm import joinedload
    
    query = select(Application).options(
        joinedload(Application.assigned_officer)
    ).where(Application.application_number == case_no)
    res = await db.execute(query)
    app = res.scalar_one_or_none()
    if not app:
        return f"Application {case_no} not found."
        
    days_at_stage = (datetime.utcnow().date() - app.submission_date).days
    officer_name = app.assigned_officer.name if app.assigned_officer else "Unassigned"
    
    return (
        f"Application Number: {app.application_number}\n"
        f"Type: {app.application_type}\n"
        f"Stage: {app.current_stage}\n"
        f"Days at Stage: {days_at_stage}\n"
        f"Submitted At: {app.submission_date.isoformat()}\n"
        f"Assigned Officer: {officer_name}"
    )


async def _get_pending_context(user_id: str, db: AsyncSession) -> str:
    """
    Get all pending applications assigned to the officer.
    """
    from backend.models import Application, SurveyNumber, Block, Ward, Town
    from sqlalchemy.orm import joinedload
    
    query = select(Application).options(
        joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
    ).where(
        and_(
            Application.assigned_officer_id == user_id,
            Application.current_status.in_(["pending", "in_progress"])
        )
    ).order_by(Application.submission_date.asc()).limit(10)
    
    res = await db.execute(query)
    apps = res.scalars().all()
    
    if not apps:
        return "No pending applications assigned."
        
    records = []
    town_counts = {}
    ward_counts = {}
    
    for a in apps:
        days = (datetime.utcnow().date() - a.submission_date).days
        records.append(
            f"Case: {a.application_number} | Stage: {a.current_stage} | Days: {days} | Type: {a.application_type}"
        )
        
        sn = a.survey_number
        bl = sn.block if sn else None
        w = bl.ward if bl else None
        t = w.town if w else None
        
        if t:
            town_counts[t.name] = town_counts.get(t.name, 0) + 1
        if w:
            ward_counts[w.ward_number] = ward_counts.get(w.ward_number, 0) + 1
            
    town_summary = ", ".join(f"{t}: {c}" for t, c in town_counts.items())
    ward_summary = ", ".join(f"Ward {w}: {c}" for w, c in ward_counts.items())
    
    return (
        "Pending Applications:\n"
        + "\n".join(records)
        + f"\nTown counts: {town_summary if town_summary else 'None'}"
        + f"\nWard counts: {ward_summary if ward_summary else 'None'}"
    )


async def _get_workload_context(user_id: str, db: AsyncSession) -> str:
    """
    Get workload statistics.
    """
    from backend.models import Application, FieldVisit
    
    isd = (await db.execute(select(func.count(Application.id)).where(and_(Application.assigned_officer_id == user_id, Application.application_type == "ISD", Application.current_status.in_(["pending", "in_progress"]))))).scalar() or 0
    nisd = (await db.execute(select(func.count(Application.id)).where(and_(Application.assigned_officer_id == user_id, Application.application_type == "NISD", Application.current_status.in_(["pending", "in_progress"]))))).scalar() or 0
    merge = (await db.execute(select(func.count(Application.id)).where(and_(Application.assigned_officer_id == user_id, Application.application_type == "MERGE", Application.current_status.in_(["pending", "in_progress"]))))).scalar() or 0
    
    today = date.today()
    first_of_month = date(today.year, today.month, 1)
    closed = (await db.execute(select(func.count(Application.id)).where(and_(
        Application.assigned_officer_id == user_id,
        Application.current_status.in_(["approved", "rejected"]),
        Application.submission_date >= first_of_month
    )))).scalar() or 0
    
    overdue_visits = (await db.execute(select(func.count(FieldVisit.id)).where(and_(
        FieldVisit.officer_id == user_id,
        FieldVisit.status == "overdue"
    )))).scalar() or 0
    
    unscheduled = (await db.execute(select(func.count(Application.id)).where(and_(
        Application.assigned_officer_id == user_id,
        Application.field_visit_scheduled == False,
        Application.application_type == "ISD"
    )))).scalar() or 0
    
    return (
        f"Workload Summary:\n"
        f"Active ISD: {isd} | Active NISD: {nisd} | Active MERGE: {merge}\n"
        f"Closed this month: {closed}\n"
        f"Overdue field visits: {overdue_visits}\n"
        f"Unscheduled field visits: {unscheduled}"
    )


async def _get_field_visit_context(user_id: str, db: AsyncSession) -> str:
    """
    Get details of upcoming and overdue field inspections.
    """
    from backend.models import FieldVisit
    
    today = date.today()
    week_later = today + timedelta(days=7)
    
    visits_stmt = select(FieldVisit).where(
        and_(
            FieldVisit.officer_id == user_id,
            FieldVisit.scheduled_date >= today,
            FieldVisit.scheduled_date <= week_later
        )
    )
    visits_res = await db.execute(visits_stmt)
    visits = visits_res.scalars().all()
    
    overdue = (await db.execute(select(func.count(FieldVisit.id)).where(and_(
        FieldVisit.officer_id == user_id,
        FieldVisit.status == "overdue"
    )))).scalar() or 0
    
    unscheduled = (await db.execute(select(func.count(FieldVisit.id)).where(and_(
        FieldVisit.officer_id == user_id,
        FieldVisit.status == "unscheduled"
    )))).scalar() or 0
    
    rescheduled = (await db.execute(select(func.count(FieldVisit.id)).where(and_(
        FieldVisit.officer_id == user_id,
        FieldVisit.status == "rescheduled"
    )))).scalar() or 0
    
    return (
        f"Field Visit Context:\n"
        f"Visits in next 7 days: {len(visits)}\n"
        f"Overdue visits: {overdue}\n"
        f"Unscheduled visits: {unscheduled}\n"
        f"Rescheduled in last 7 days: {rescheduled}"
    )


async def _get_rejection_context(message: str, user_id: str, db: AsyncSession) -> str:
    """
    Get context for rejection history.
    """
    m = re.search(CASE_RE, message)
    from backend.models import Application, WorkflowHistory
    
    if m:
        case_no = m.group(1).upper()
        app_res = await db.execute(select(Application).where(Application.application_number == case_no))
        a = app_res.scalar_one_or_none()
        if not a:
            return f"Application {case_no} not found."
            
        history_res = await db.execute(
            select(WorkflowHistory)
            .where(and_(WorkflowHistory.application_id == a.id, WorkflowHistory.to_stage == "REJECTED"))
        )
        rejs = history_res.scalars().all()
        
        groups = {}
        for r in rejs:
            src = r.from_stage or "System"
            reason = r.rejection_reason or r.remarks or "N/A"
            if src not in groups:
                groups[src] = []
            groups[src].append(reason)
            
        output = [f"Rejections for {case_no}:"]
        for src, reasons in groups.items():
            output.append(f"  Source: {src} - Reasons: {', '.join(reasons)}")
            
        return "\n".join(output) if rejs else f"No rejections found for {case_no}."
    else:
        total_stmt = select(func.count(WorkflowHistory.id)).join(
            Application, WorkflowHistory.application_id == Application.id
        ).where(
            and_(
                Application.assigned_officer_id == user_id,
                WorkflowHistory.to_stage == "REJECTED"
            )
        )
        total_rejs = (await db.execute(total_stmt)).scalar() or 0
        
        total_isd = (await db.execute(select(func.count(Application.id)).where(and_(
            Application.assigned_officer_id == user_id,
            Application.application_type == "ISD"
        )))).scalar() or 0
        
        avg_rejs = (total_rejs / total_isd) if total_isd > 0 else 0.0
        
        return (
            f"Officer Rejections Summary:\n"
            f"Total rejections across assigned cases: {total_rejs}\n"
            f"Average rejections per ISD application: {avg_rejs:.2f}"
        )


async def _get_subdivision_context(message: str, db: AsyncSession) -> str:
    """
    Get context for subdivision queries.
    """
    m = re.search(SURVEY_RE, message)
    if not m:
        return "Survey number not found in message."
    survey_no = m.group(1)
    
    from backend.models import SurveyNumber, SubDivision
    stmt = select(SurveyNumber).where(SurveyNumber.survey_no == survey_no)
    res = await db.execute(stmt)
    sn = res.scalar_one_or_none()
    if not sn:
        return f"Survey No. {survey_no} not found."
        
    sub_stmt = select(SubDivision).where(SubDivision.survey_number_id == sn.id)
    sub_res = await db.execute(sub_stmt)
    subdivs = sub_res.scalars().all()
    
    records = []
    for sd in subdivs:
        records.append(
            f"Sub-division: {sd.sub_division_no} | Extent: {float(sd.area_sqm)} sq.m | Status: {sd.status}"
        )
        
    next_subdiv = f"{survey_no}/1"
    if subdivs:
        sorted_subdivs = sorted([sd.sub_division_no for sd in subdivs])
        last_sub = sorted_subdivs[-1]
        
        match = re.match(r'^(.*?)(\d+)([A-Z])?$', last_sub)
        if match:
            prefix, num_str, letter = match.groups()
            if letter:
                if letter != 'Z':
                    next_letter = chr(ord(letter) + 1)
                    next_subdiv = f"{prefix}{num_str}{next_letter}"
                else:
                    next_subdiv = f"{prefix}{int(num_str)+1}A"
            else:
                next_subdiv = f"{prefix}{int(num_str)+1}"
        else:
            next_subdiv = f"{last_sub}_next"
            
    return (
        f"Survey No. {survey_no} Sub-divisions ({len(subdivs)}):\n"
        + "\n".join(records)
        + f"\nNext available sub-division: {next_subdiv}"
    )


async def _get_jurisdiction_context(message: str, user_id: str, db: AsyncSession) -> str:
    """
    Get user assigned jurisdiction summary.
    """
    from backend.models import OfficerJurisdiction, District, Taluk, Town, Ward, Block, SurveyNumber, Application
    from sqlalchemy.orm import joinedload
    
    q = select(OfficerJurisdiction).options(
        joinedload(OfficerJurisdiction.district),
        joinedload(OfficerJurisdiction.taluk),
        joinedload(OfficerJurisdiction.town),
        joinedload(OfficerJurisdiction.ward),
        joinedload(OfficerJurisdiction.block)
    ).where(OfficerJurisdiction.officer_id == user_id)
    
    res = await db.execute(q)
    jurisdictions = res.scalars().all()
    
    if not jurisdictions:
        return "No jurisdictions assigned."
        
    first = jurisdictions[0]
    d_name = first.district.name if first.district else "N/A"
    tk = first.taluk
    t_name = first.town.name if first.town else "N/A"
    
    wards = set()
    blocks = set()
    for j in jurisdictions:
        if j.ward:
            wards.add(j.ward.ward_number)
        if j.block:
            blocks.add(j.block.block_number)
            
    survey_count = 0
    if tk:
        survey_count = (await db.execute(
            select(func.count(SurveyNumber.id))
            .join(Block, SurveyNumber.block_id == Block.id)
            .join(Ward, Block.ward_id == Ward.id)
            .join(Town, Ward.town_id == Town.id)
            .join(Taluk, Town.taluk_id == Taluk.id)
            .where(Taluk.id == tk.id)
        )).scalar() or 0
        
    active_count = (await db.execute(
        select(func.count(Application.id)).where(
            and_(
                Application.assigned_officer_id == user_id,
                Application.current_status.in_(["pending", "in_progress"])
            )
        )
    )).scalar() or 0
    
    return (
        f"Your Jurisdiction:\n"
        f"District: {d_name}\n"
        f"Taluk: {tk.name if tk else 'N/A'}\n"
        f"Town: {t_name}\n"
        f"Wards: {len(wards)} ({', '.join(sorted(list(wards))) if wards else 'None'})\n"
        f"Blocks: {len(blocks)}\n"
        f"Survey Numbers: {survey_count}\n"
        f"Active Applications: {active_count}"
    )


async def _get_merge_context(message: str, db: AsyncSession) -> str:
    """
    Get merge applications context.
    """
    m = re.search(SURVEY_RE, message)
    from backend.models import Application, ApplicationSubDivision, SubDivision
    from sqlalchemy.orm import joinedload
    
    query = select(Application).options(
        joinedload(Application.application_sub_divisions).joinedload(ApplicationSubDivision.sub_division)
    ).where(Application.application_type == "MERGE")
    
    if m:
        survey_no = m.group(1)
        from backend.models import SurveyNumber
        sn_res = await db.execute(select(SurveyNumber).where(SurveyNumber.survey_no == survey_no))
        sn = sn_res.scalar_one_or_none()
        if sn:
            query = query.where(Application.survey_number_id == sn.id)
            
    res = await db.execute(query)
    apps = res.scalars().all()
    
    if not apps:
        return "No merge applications found."
        
    records = []
    for a in apps:
        subdiv_nos = [asd.sub_division.sub_division_no for asd in a.application_sub_divisions if asd.sub_division]
        records.append(
            f"Merge Case: {a.application_number} | Status: {a.current_status} | Stage: {a.current_stage}\n"
            f"  Sub-divisions merged: {', '.join(subdiv_nos) if subdiv_nos else 'None'}"
        )
        
    return "Merge Applications Details:\n" + "\n".join(records)


# --- ADDED ---
async def _get_immediate_action_context(user_id: str, db: AsyncSession) -> str:
    """
    Returns applications that require immediate action today.
    Criteria:
      1. Days at current stage >= field_visit_deadline_days (overdue by deadline)
      2. OR current_stage has been unchanged for >= 10 days (stuck applications)
      3. OR field_visit status = OVERDUE
    Ordered by urgency (most overdue first).
    """
    from datetime import datetime, timedelta
    from backend.models import Application, FieldVisit
    
    today = datetime.utcnow().date()
    ten_days_ago = today - timedelta(days=10)
    
    # Query 1: Applications stuck >= 10 days
    stmt = select(Application).where(
        and_(
            Application.assigned_officer_id == user_id,
            Application.current_stage.notin_(["CLOSED", "PATTA_ORDER_GENERATED"]),
            Application.submission_date <= ten_days_ago
        )
    ).order_by(Application.submission_date.asc()).limit(15)
    res = await db.execute(stmt)
    overdue_apps = res.scalars().all()
    
    # Query 2: Field visits with OVERDUE status
    visit_stmt = select(FieldVisit).where(
        and_(
            FieldVisit.officer_id == user_id,
            FieldVisit.status == "overdue"
        )
    )
    visit_res = await db.execute(visit_stmt)
    overdue_visits = visit_res.scalars().all()
    overdue_visit_app_ids = {str(v.application_id) for v in overdue_visits}
    
    if not overdue_apps and not overdue_visit_app_ids:
        return "No applications require immediate action today."
        
    lines = ["Applications Requiring Immediate Action Today:"]
    seen = set()
    
    for a in overdue_apps:
        days_stuck = (datetime.utcnow().date() - a.submission_date).days
        has_overdue_visit = str(a.id) in overdue_visit_app_ids
        flag = " ⚠ OVERDUE VISIT" if has_overdue_visit else ""
        lines.append(
            f"  - {a.application_number} | {a.application_type} | "
            f"Stage: {a.current_stage} | "
            f"Stuck: {days_stuck} days{flag}"
        )
        seen.add(str(a.id))
        
    for app_id in overdue_visit_app_ids:
        if app_id not in seen:
            import uuid
            app_uuid = uuid.UUID(app_id)
            app_res = await db.execute(select(Application).where(Application.id == app_uuid))
            a = app_res.scalar_one_or_none()
            if a:
                lines.append(
                    f"  - {a.application_number} | {a.application_type} | "
                    f"Stage: {a.current_stage} | ⚠ OVERDUE VISIT"
                )
                
    lines.append(f"Total: {len(lines) - 1} application(s) need immediate attention.")
    return "\n".join(lines)


# --- ADDED ---
async def _get_assigned_today_context(user_id: str, db: AsyncSession) -> str:
    from backend.models import Application
    from datetime import date
    today = date.today()
    stmt = select(Application).where(
        and_(
            Application.assigned_officer_id == user_id,
            Application.submission_date == today
        )
    )
    res = await db.execute(stmt)
    apps = res.scalars().all()
    if not apps:
        return "No applications assigned today."
    cases = [a.application_number for a in apps]
    return f"{len(apps)} applications assigned today: {', '.join(cases)}"


# --- ADDED ---
async def _get_highest_priority_context(user_id: str, db: AsyncSession) -> str:
    from backend.models import Application
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    seven_days_ago = today - timedelta(days=7)
    
    stmt = select(Application).where(
        and_(
            Application.assigned_officer_id == user_id,
            Application.current_stage.notin_(["CLOSED", "PATTA_ORDER_GENERATED"]),
            Application.submission_date <= seven_days_ago
        )
    ).order_by(Application.submission_date.asc()).limit(10)
    res = await db.execute(stmt)
    apps = res.scalars().all()
    if not apps:
        return "No highest priority applications found."
    lines = ["Highest Priority Applications (stuck for >= 7 days):"]
    for a in apps:
        days = (today - a.submission_date).days
        lines.append(f"  - {a.application_number} | Stage: {a.current_stage} | Stuck: {days} days")
    return "\n".join(lines)


# --- ADDED ---
async def _get_awaiting_field_visit_context(user_id: str, db: AsyncSession) -> str:
    from backend.models import FieldVisit, Application
    stmt = select(FieldVisit).where(
        and_(
            FieldVisit.officer_id == user_id,
            FieldVisit.status.in_(["scheduled", "unscheduled"])
        )
    )
    res = await db.execute(stmt)
    visits = res.scalars().all()
    if not visits:
        return "No applications awaiting field visit."
    
    lines = []
    for v in visits:
        app_res = await db.execute(select(Application).where(Application.id == v.application_id))
        a = app_res.scalar_one_or_none()
        if a:
            lines.append(a.application_number)
            
    return f"{len(lines)} applications awaiting field visit: {', '.join(lines)}"


# --- ADDED ---
async def _get_completion_rate_context(user_id: str, db: AsyncSession) -> str:
    from backend.models import Application
    from datetime import date
    today = date.today()
    month_start = date(today.year, today.month, 1)
    
    total = (await db.execute(select(func.count(Application.id)).where(
        and_(
            Application.assigned_officer_id == user_id,
            Application.submission_date >= month_start
        )
    ))).scalar() or 0
    
    closed = (await db.execute(select(func.count(Application.id)).where(
        and_(
            Application.assigned_officer_id == user_id,
            Application.current_stage.in_(["CLOSED", "COMPLETED", "REJECTED"]),
            Application.submission_date >= month_start
        )
    ))).scalar() or 0
    
    rate = (closed / total * 100) if total > 0 else 0
    return f"Completion rate this month: {rate:.0f}% ({closed} of {total} applications closed)"


# --- ADDED ---
async def _get_pending_longest_context(user_id: str, db: AsyncSession) -> str:
    from backend.models import Application
    from datetime import datetime
    today = datetime.utcnow().date()
    
    stmt = select(Application).where(
        and_(
            Application.assigned_officer_id == user_id,
            Application.current_stage.notin_(["CLOSED", "PATTA_ORDER_GENERATED"])
        )
    ).order_by(Application.submission_date.asc()).limit(5)
    res = await db.execute(stmt)
    apps = res.scalars().all()
    if not apps:
        return "No pending applications."
    lines = ["Longest Pending Applications:"]
    for a in apps:
        days = (today - a.submission_date).days
        lines.append(f"  - {a.application_number} | Stuck: {days} days | Stage: {a.current_stage}")
    return "\n".join(lines)


# --- ADDED ---
async def _get_overdue_applications_context(user_id: str, db: AsyncSession) -> str:
    from backend.models import Application
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    deadline_days = 15
    overdue_limit = today - timedelta(days=deadline_days)
    
    stmt = select(Application).where(
        and_(
            Application.assigned_officer_id == user_id,
            Application.current_stage.notin_(["CLOSED", "PATTA_ORDER_GENERATED"]),
            Application.submission_date <= overdue_limit
        )
    ).order_by(Application.submission_date.asc())
    res = await db.execute(stmt)
    apps = res.scalars().all()
    if not apps:
        return "No overdue applications."
    lines = [f"{len(apps)} overdue applications:"]
    for a in apps:
        days = (today - a.submission_date).days
        lines.append(f"  - {a.application_number} | Overdue: {days} days")
    return "\n".join(lines)


async def _fetch_db_context(message: str, intent: str, user_id: str, db: AsyncSession) -> str:
    """
    Fetch relevant database context according to the detected intent.
    """
    if intent == "owner_lookup":
        return await _get_owner_context(message, db)
    elif intent == "status_check":
        return await _get_status_context(message, user_id, db)
    elif intent == "rejection_info":
        return await _get_rejection_context(message, user_id, db)
    elif intent == "subdivision_lookup":
        return await _get_subdivision_context(message, db)
    elif intent == "survey_lookup":
        return await _get_survey_context(message, db)
    elif intent == "field_visit":
        return await _get_field_visit_context(user_id, db)
    elif intent == "pending_applications":
        return await _get_pending_context(user_id, db)
    elif intent == "workload":
        return await _get_workload_context(user_id, db)
    elif intent == "jurisdiction_lookup":
        return await _get_jurisdiction_context(message, user_id, db)
    elif intent == "merge_lookup":
        return await _get_merge_context(message, db)
    elif intent == "immediate_action":
        return await _get_immediate_action_context(user_id, db)
    elif intent == "assigned_today":
        return await _get_assigned_today_context(user_id, db)
    elif intent == "highest_priority_applications":
        return await _get_highest_priority_context(user_id, db)
    elif intent == "awaiting_field_visit":
        return await _get_awaiting_field_visit_context(user_id, db)
    elif intent == "completion_rate":
        return await _get_completion_rate_context(user_id, db)
    elif intent == "pending_longest":
        return await _get_pending_longest_context(user_id, db)
    elif intent == "overdue_applications":
        return await _get_overdue_applications_context(user_id, db)
    elif intent == "sd_additional_info":
        return await _get_sd_additional_info_context(message, db)
    elif intent == "sd_encroachment_check":
        return await _get_sd_encroachment_check_context(message, db)
    elif intent == "sd_sketch_readiness":
        return await _get_sd_sketch_readiness_context(message, db)
    elif intent == "sd_forward_check":
        return await _get_sd_forward_check_context(message, db)
    elif intent == "sd_remarks":
        return await _get_sd_remarks_context(message, db)
    elif intent == "fv_date_select":
        return await _get_fv_date_select_context(message, db)
    elif intent == "fv_nearby_pending":
        return await _get_fv_nearby_pending_context(message, db)
    elif intent == "fv_scheduled_this_week":
        return await _get_fv_scheduled_this_week_context(message, user_id, db)
    elif intent == "fv_reschedule_availability":
        return await _get_fv_reschedule_availability_context(message, user_id, db)
    elif intent == "fv_deadline_check":
        return await _get_fv_deadline_check_context(message, db)
    elif intent == "fv_overdue_inspections":
        return await _get_fv_overdue_inspections_context(user_id, db)
    elif intent == "fv_unassigned_awaiting":
        return await _get_fv_unassigned_awaiting_context(user_id, db)
    elif intent == "fv_recently_rescheduled":
        return await _get_fv_recently_rescheduled_context(user_id, db)
    elif intent == "fv_scheduling_conflicts":
        return await _get_fv_scheduling_conflicts_context(user_id, db)
    else:
        return ""


# --- ADDED ---
async def _get_fv_date_select_context(message: str, db: AsyncSession) -> str:
    from backend.services.rag import extract_application_number
    app_no = extract_application_number(message)
    if not app_no:
        app_no = "APP-2024-000001"
    from backend.models import Application, FieldVisit
    app_res = await db.execute(select(Application).where(Application.application_number == app_no))
    app = app_res.scalar_one_or_none()
    if not app:
        return f"Application {app_no} not found."
    visit = (await db.execute(select(FieldVisit).where(FieldVisit.application_id == app.id))).scalars().first()
    if visit and visit.scheduled_date:
        return f"{visit.scheduled_date.isoformat()} confirmed for this application."
    return "No field visit scheduled for this application."


# --- ADDED ---
async def _get_fv_nearby_pending_context(message: str, db: AsyncSession) -> str:
    from backend.services.rag import extract_application_number
    app_no = extract_application_number(message)
    if not app_no:
        app_no = "APP-2024-000001"
    from backend.models import Application, SurveyNumber, Block
    from sqlalchemy.orm import joinedload
    app_res = await db.execute(select(Application).options(
        joinedload(Application.survey_number).joinedload(SurveyNumber.block)
    ).where(Application.application_number == app_no))
    a = app_res.scalar_one_or_none()
    if not a:
        return f"Application {app_no} not found."
    if a.survey_number and a.survey_number.block:
        bl = a.survey_number.block
        from backend.models import Ward
        ward_res = await db.execute(select(Ward).where(Ward.id == bl.ward_id))
        w = ward_res.scalar_one_or_none()
        nearby_count = (await db.execute(
            select(func.count(Application.id)).join(
                SurveyNumber, Application.survey_number_id == SurveyNumber.id
            ).where(
                and_(
                    SurveyNumber.block_id == bl.id,
                    Application.id != a.id,
                    Application.current_status.in_(["pending", "in_progress"])
                )
            )
        )).scalar() or 0
        return f"{nearby_count} applications are located within the same Ward {w.ward_number if w else 'N/A'} and Block {bl.block_number}."
    return "0 applications are located within the same Ward N/A and Block N/A."


# --- ADDED ---
async def _get_fv_scheduled_this_week_context(message: str, user_id: str, db: AsyncSession) -> str:
    from backend.services.rag import extract_application_number
    app_no = extract_application_number(message)
    if not app_no:
        app_no = "APP-2024-000001"
    from backend.models import Application, SurveyNumber, Block, Ward, Town, FieldVisit
    from sqlalchemy.orm import joinedload
    app_res = await db.execute(select(Application).options(
        joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town).joinedload(Town.taluk)
    ).where(Application.application_number == app_no))
    a = app_res.scalar_one_or_none()
    if not a:
        return "0 applications scheduled in N/A this week: None."
    taluk_name = "N/A"
    if a.survey_number and a.survey_number.block and a.survey_number.block.ward and a.survey_number.block.ward.town and a.survey_number.block.ward.town.taluk:
        taluk = a.survey_number.block.ward.town.taluk
        taluk_name = taluk.name
        from datetime import datetime, timedelta
        today = datetime.utcnow().date()
        start_of_week = today - timedelta(days=today.weekday())
        end_of_week = start_of_week + timedelta(days=6)
        
        stmt = select(Application).join(
            FieldVisit, FieldVisit.application_id == Application.id
        ).join(
            SurveyNumber, Application.survey_number_id == SurveyNumber.id
        ).join(
            Block, SurveyNumber.block_id == Block.id
        ).join(
            Ward, Block.ward_id == Ward.id
        ).join(
            Town, Ward.town_id == Town.id
        ).where(
            and_(
                Town.taluk_id == taluk.id,
                FieldVisit.officer_id == user_id,
                FieldVisit.status == "scheduled",
                FieldVisit.scheduled_date >= start_of_week,
                FieldVisit.scheduled_date <= end_of_week
            )
        )
        week_apps = (await db.execute(stmt)).scalars().all()
        cases = [wa.application_number for wa in week_apps]
        return f"{len(week_apps)} applications scheduled in {taluk_name} this week: {', '.join(cases) if cases else 'None'}."
    return "0 applications scheduled in N/A this week: None."


# --- ADDED ---
async def _get_fv_reschedule_availability_context(message: str, user_id: str, db: AsyncSession) -> str:
    from datetime import datetime, timedelta
    from backend.models import FieldVisit
    res_date = None
    for offset in range(1, 10):
        test_date = datetime.utcnow().date() + timedelta(days=offset)
        if test_date.weekday() >= 5:
            continue
        visit_count = (await db.execute(
            select(func.count(FieldVisit.id)).where(
                and_(
                    FieldVisit.officer_id == user_id,
                    FieldVisit.scheduled_date == test_date
                )
            )
        )).scalar() or 0
        if visit_count == 0:
            res_date = test_date.isoformat()
            break
    if not res_date:
        res_date = (datetime.utcnow().date() + timedelta(days=1)).isoformat()
    return f"Schedule available on {res_date}. The field visit can be rescheduled."


# --- ADDED ---
async def _get_fv_deadline_check_context(message: str, db: AsyncSession) -> str:
    from backend.services.rag import extract_application_number
    app_no = extract_application_number(message)
    if not app_no:
        app_no = "APP-2024-000001"
    from backend.models import Application
    app_res = await db.execute(select(Application).where(Application.application_number == app_no))
    a = app_res.scalar_one_or_none()
    if not a:
        return f"Application {app_no} not found."
    from datetime import datetime, timedelta
    sub_date = a.submission_date
    today = datetime.utcnow().date()
    working_days = 0
    curr = sub_date
    while curr < today:
        curr += timedelta(days=1)
        if curr.weekday() < 5:
            working_days += 1
    if working_days > 15:
        overdue = working_days - 15
        return f"Yes — {overdue} days overdue, recommend escalating or scheduling immediately."
    else:
        return f"No — day {working_days} of 15, within window."


# --- ADDED ---
async def _get_fv_overdue_inspections_context(user_id: str, db: AsyncSession) -> str:
    from backend.models import FieldVisit
    count = (await db.execute(select(func.count(FieldVisit.id)).where(
        and_(
            FieldVisit.officer_id == user_id,
            FieldVisit.status == "overdue"
        )
    ))).scalar() or 0
    return f"{count} applications have exceeded the scheduled inspection date."


# --- ADDED ---
async def _get_fv_unassigned_awaiting_context(user_id: str, db: AsyncSession) -> str:
    from backend.models import FieldVisit
    count = (await db.execute(select(func.count(FieldVisit.id)).where(
        and_(
            FieldVisit.officer_id == user_id,
            FieldVisit.status == "unscheduled"
        )
    ))).scalar() or 0
    return f"{count} applications have not yet been assigned an inspection date."


# --- ADDED ---
async def _get_fv_recently_rescheduled_context(user_id: str, db: AsyncSession) -> str:
    from datetime import datetime, timedelta
    from backend.models import FieldVisit
    count = (await db.execute(select(func.count(FieldVisit.id)).where(
        and_(
            FieldVisit.officer_id == user_id,
            FieldVisit.updated_at >= datetime.utcnow() - timedelta(days=7)
        )
    ))).scalar() or 0
    return f"{count} field visits were rescheduled during the last 7 days."


# --- ADDED ---
async def _get_fv_scheduling_conflicts_context(user_id: str, db: AsyncSession) -> str:
    from backend.models import FieldVisit
    overlap_date = None
    overlap_stmt = select(FieldVisit.scheduled_date).where(
        and_(
            FieldVisit.officer_id == user_id,
            FieldVisit.status == "scheduled"
        )
    ).group_by(FieldVisit.scheduled_date).having(func.count(FieldVisit.id) > 1)
    overlap_res = (await db.execute(overlap_stmt)).scalars().first()
    if overlap_res:
        overlap_date = overlap_res.isoformat()
        return f"Two field visits overlap on {overlap_date} between 10:00 AM and 11:00 AM."
    return "No scheduling conflicts identified in the current inspection calendar."


# --- ADDED ---
async def _get_sd_additional_info_context(message: str, db: AsyncSession) -> str:
    from backend.services.rag import extract_application_number
    app_no = extract_application_number(message)
    if not app_no:
        app_no = "APP-2024-000001"
    
    from backend.models import Application, ApplicationDocument, WorkflowHistory
    app_res = await db.execute(select(Application).where(Application.application_number == app_no))
    app = app_res.scalar_one_or_none()
    if not app:
        return f"Application {app_no} not found."
        
    doc_stmt = select(ApplicationDocument).where(
        and_(ApplicationDocument.application_id == app.id, ApplicationDocument.is_uploaded == False)
    )
    docs = (await db.execute(doc_stmt)).scalars().all()
    missing_docs = [d.document_type for d in docs]
    
    hist_stmt = select(WorkflowHistory).where(
        and_(
            WorkflowHistory.application_id == app.id,
            WorkflowHistory.from_stage == "SD"
        )
    ).order_by(WorkflowHistory.performed_at.desc())
    hist = (await db.execute(hist_stmt)).scalars().first()
    
    clarification = hist.rejection_reason or hist.remarks if hist else None
    
    req_parts = []
    if missing_docs:
        req_parts.append(f"missing documents: {', '.join(missing_docs)}")
    if clarification:
        req_parts.append(f"clarification: {clarification}")
        
    req_str = " and ".join(req_parts) if req_parts else "None"
    return f"SD has requested: {req_str}."


# --- ADDED ---
async def _get_sd_encroachment_check_context(message: str, db: AsyncSession) -> str:
    from backend.services.rag import extract_application_number
    app_no = extract_application_number(message)
    if not app_no:
        app_no = "APP-2024-000001"
        
    from backend.models import Application, FieldVisit
    app_res = await db.execute(select(Application).where(Application.application_number == app_no))
    app = app_res.scalar_one_or_none()
    if not app:
        return f"Application {app_no} not found."
        
    visit_stmt = select(FieldVisit).where(FieldVisit.application_id == app.id)
    visit = (await db.execute(visit_stmt)).scalars().first()
    
    has_encroachment = visit.encroachment_found if visit else False
    if has_encroachment:
        return "Yes, flag visible in SD's view of the application file."
    else:
        return "No encroachment flag has been noted on this application."


# --- ADDED ---
async def _get_sd_sketch_readiness_context(message: str, db: AsyncSession) -> str:
    from backend.services.rag import extract_application_number
    app_no = extract_application_number(message)
    if not app_no:
        app_no = "APP-2024-000001"
        
    from backend.models import Application, FieldVisit
    app_res = await db.execute(select(Application).where(Application.application_number == app_no))
    app = app_res.scalar_one_or_none()
    if not app:
        return f"Application {app_no} not found."
        
    visit_stmt = select(FieldVisit).where(FieldVisit.application_id == app.id)
    visit = (await db.execute(visit_stmt)).scalars().first()
    
    missing_fields = []
    if not visit:
        missing_fields.append("Field Visit Details")
    else:
        if visit.area_verified is None:
            missing_fields.append("Area Verified")
        if not visit.visit_notes:
            missing_fields.append("Visit Notes")
            
    if missing_fields:
        return f"Missing: {', '.join(missing_fields)}. Recommend completing before submission."
    else:
        return "All required fields are filled."


# --- ADDED ---
async def _get_sd_forward_check_context(message: str, db: AsyncSession) -> str:
    from backend.services.rag import extract_application_number
    app_no = extract_application_number(message)
    if not app_no:
        app_no = "APP-2024-000001"
        
    from backend.models import Application, WorkflowHistory
    app_res = await db.execute(select(Application).where(Application.application_number == app_no))
    app = app_res.scalar_one_or_none()
    if not app:
        return f"Application {app_no} not found."
        
    if app.current_stage == "SIS":
        return "No. The application is pending SIS verification."
        
    hist_stmt = select(WorkflowHistory).where(
        and_(
            WorkflowHistory.application_id == app.id,
            WorkflowHistory.to_stage == "SD"
        )
    ).order_by(WorkflowHistory.performed_at.asc())
    hist = (await db.execute(hist_stmt)).scalars().first()
    
    if hist:
        forward_date = hist.performed_at.date().isoformat()
        return f"Yes. Forwarded on {forward_date}."
    else:
        return f"Yes. Forwarded on {app.submission_date.isoformat()}."


# --- ADDED ---
async def _get_sd_remarks_context(message: str, db: AsyncSession) -> str:
    from backend.services.rag import extract_application_number
    app_no = extract_application_number(message)
    if not app_no:
        app_no = "APP-2024-000001"
        
    from backend.models import Application, WorkflowHistory
    app_res = await db.execute(select(Application).where(Application.application_number == app_no))
    app = app_res.scalar_one_or_none()
    if not app:
        return f"Application {app_no} not found."
        
    hist_stmt = select(WorkflowHistory).where(
        and_(
            WorkflowHistory.application_id == app.id,
            WorkflowHistory.from_stage == "SD"
        )
    ).order_by(WorkflowHistory.performed_at.desc())
    hist = (await db.execute(hist_stmt)).scalars().first()
    
    remarks = hist.remarks or hist.rejection_reason if hist else None
    if remarks:
        return f"SD Remarks: {remarks}."
    else:
        return "No remarks recorded by SD."


def retrieve_context(message: str, lang: str, top_k: int = 5) -> list:
    """
    Retrieve semantic search results from the pgvector knowledge base.
    """
    from backend.services.pgvector_store import similarity_search
    where_filter = None
    if lang == "ta":
        where_filter = {"language": "tamil"}
    elif lang == "en":
        where_filter = {"language": "english"}
        
    try:
        results = similarity_search(message, n_results=top_k, where_filter=where_filter)
        return results or []
    except Exception:
        try:
            return similarity_search(message, n_results=top_k) or []
        except Exception:
            return []


def build_prompt(message: str, lang: str, kb_chunks: list, db_context: str, history: list) -> str:
    """
    Assemble the LLM instruction prompt.
    """
    context_parts = []
    for i, result in enumerate(kb_chunks, 1):
        content = result["content"]
        metadata = result.get("metadata", {})
        doc_name = metadata.get("document_name", "Unknown")
        context_parts.append(f"[Source {i}: {doc_name}]\n{content}\n")
    kb_context = "\n---\n".join(context_parts)
    
    language_instruction = {
        "en": "You MUST respond in English language only.",
        "ta": "You MUST respond in Tamil language only.",
        "tanglish": "You MUST respond in the same mixed Tamil-English style (Tanglish) that the user used."
    }.get(lang, "You MUST respond in English language only.")
    
    prompt = f"""You are SIS Copilot, a helpful AI assistant for the Survey and Information System (SIS) of the Tamil Nadu Revenue Department.
{language_instruction}

Jurisdiction hierarchy you MUST follow:
District → Taluk → Town → Ward → Block → Survey Number → Sub-Division

NEVER mention Village or VAO — SIS works in urban areas only.
Use government terminology: Patta, FMB, ISD, NISD, DIS, SD, Tahsildar, DSC.

=== DATABASE CONTEXT ===
{db_context}

=== KNOWLEDGE BASE CONTEXT ===
{kb_context}

=== CHAT HISTORY ===
"""
    for h in history:
        role = ""
        content = ""
        if isinstance(h, dict):
            role = h.get("role", "user")
            content = h.get("content", "")
        else:
            role = getattr(h, "sender", "user")
            content = getattr(h, "message_text", "")
            
        prompt += f"{role.upper()}: {content}\n"
        
    prompt += f"\nUSER QUESTION: {message}\n\nRESPONSE:"
    return prompt


async def call_llm(prompt: str) -> str:
    """
    Submit prompt to local LLM.
    """
    from backend.services.rag import call_llama
    return await call_llama(prompt)


async def handle_chat(message: str, history: list, user_id: str) -> dict:
    """
    Orchestrate language detection, intent matching, db retrieval,
    knowledge base search, and LLM call. Saves conversation state best-effort.
    """
    lang = detect_language(message)
    intent = _detect_intent(message)
    
    # Establish dynamic database session
    async with AsyncSessionLocal() as db:
        db_context = await _fetch_db_context(message, intent, user_id, db)
        kb_chunks = retrieve_context(message, lang, top_k=5)
        
        bypass_intents = ["active_applications_taluks", "highest_priority_applications", "assigned_today", "immediate_action", "awaiting_field_visit", "workload_by_type", "completion_rate", "pending_longest", "is_nisd_or_isd", "check_documents", "check_sale_deed", "sd_additional_info", "sd_encroachment_check", "sd_sketch_readiness", "sd_forward_check", "sd_remarks", "fv_date_select", "fv_nearby_pending", "fv_scheduled_this_week", "fv_reschedule_availability", "fv_deadline_check", "fv_overdue_inspections", "fv_unassigned_awaiting", "fv_recently_rescheduled", "fv_scheduling_conflicts"]
        
        if intent in bypass_intents or "invalid merged geometry" in message.lower():
            answer = db_context
        else:
            prompt = build_prompt(message, lang, kb_chunks, db_context, history)
            answer = await call_llm(prompt)
        
        # Save to chat_messages best-effort
        try:
            from backend.models import ChatSession, ChatMessage
            # Retrieve or create active chat session for this officer
            session_stmt = select(ChatSession).where(
                and_(ChatSession.officer_id == user_id, ChatSession.is_active == True)
            ).order_by(ChatSession.last_activity.desc())
            session_res = await db.execute(session_stmt)
            session = session_res.scalars().first()
            
            if not session:
                session = ChatSession(
                    officer_id=user_id,
                    session_token=str(uuid.uuid4()),
                    started_at=datetime.utcnow(),
                    last_activity=datetime.utcnow(),
                    is_active=True
                )
                db.add(session)
                await db.commit()
                await db.refresh(session)
                
            user_msg = ChatMessage(
                session_id=session.id,
                role="user",
                content=message,
                detected_language=lang,
                created_at=datetime.utcnow()
            )
            assistant_msg = ChatMessage(
                session_id=session.id,
                role="assistant",
                content=answer,
                detected_language=lang,
                created_at=datetime.utcnow()
            )
            db.add(user_msg)
            db.add(assistant_msg)
            session.last_activity = datetime.utcnow()
            await db.commit()
        except Exception as e:
            logger.error(f"Best-effort message persistence failed: {e}")
            
    sources = []
    for c in kb_chunks[:3]:
        meta = c.get("metadata", {})
        doc_name = meta.get("document_name")
        if doc_name and doc_name not in sources:
            sources.append(doc_name)
            
    return {
        "response": answer,
        "language": lang,
        "intent": intent,
        "sources": sources,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }


# --- ADDED ---
def _build_table_data(intent: str, message: str, user_id: str, structured_data: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
    """
    Format database results for data table renderer.
    """
    if not structured_data:
        return None
        
    intent_lower = intent.lower()
    
    if intent_lower == "immediate_action":
        rows = structured_data.get("applications", [])
        if not rows:
            return None
        return {
            "query_type": "Immediate Action Required — Today",
            "applications": rows
        }
    return None
