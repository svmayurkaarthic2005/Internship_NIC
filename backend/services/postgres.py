"""
PostgreSQL query service for structured data retrieval
"""
from sqlalchemy import select, func, and_, or_, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, List, Optional
from datetime import date, datetime, timedelta
from uuid import UUID

from backend.models import (
    Application, SISOfficer, SurveyNumber, SubDivision,
    Owner, SurveyOwnership, FieldVisit, WorkflowHistory,
    ApplicationSubDivision, Block, Ward, Town, Taluk, District,
    OfficerJurisdiction, PattaTransfer
)
from backend.schemas import OfficerContext
from backend.utils.logger import get_logger

logger = get_logger(__name__)


async def get_jurisdiction_filter(db: AsyncSession, officer: OfficerContext):
    """
    Build jurisdiction filter conditions for queries based on officer's assigned jurisdiction.
    
    Returns a list of SQLAlchemy filter conditions that can be used in queries.
    """
    try:
        # Get officer's jurisdictions
        jurisdiction_query = select(OfficerJurisdiction).where(
            OfficerJurisdiction.officer_id == officer.officer_id
        )
        result = await db.execute(jurisdiction_query)
        jurisdictions = result.scalars().all()
        
        if not jurisdictions:
            logger.warning(f"No jurisdictions found for officer {officer.officer_id}")
            return []
        
        # Build filter conditions based on jurisdiction type
        filters = []
        
        for jurisdiction in jurisdictions:
            if jurisdiction.jurisdiction_type == "district" and jurisdiction.district_id:
                # Officer has entire district - filter by district
                filters.append(District.id == jurisdiction.district_id)
                
            elif jurisdiction.jurisdiction_type == "taluk" and jurisdiction.taluk_id:
                # Officer has entire taluk - filter by taluk
                filters.append(Taluk.id == jurisdiction.taluk_id)
                
            elif jurisdiction.jurisdiction_type == "town" and jurisdiction.town_id:
                # Officer has entire town - filter by town
                filters.append(Town.id == jurisdiction.town_id)
                
            elif jurisdiction.jurisdiction_type == "ward" and jurisdiction.ward_id:
                # Officer has specific ward - filter by ward
                filters.append(Ward.id == jurisdiction.ward_id)
                
            elif jurisdiction.jurisdiction_type == "block" and jurisdiction.block_id:
                # Officer has specific block - filter by block
                filters.append(Block.id == jurisdiction.block_id)
        
        return filters
        
    except Exception as e:
        logger.error(f"Error building jurisdiction filter: {e}")
        return []


async def get_officer_applications(
    db: AsyncSession,
    officer: OfficerContext,
    status: Optional[Any] = None,
    application_type: Optional[str] = None,
    submission_year: Optional[int] = None,
    submission_month: Optional[int] = None
) -> Dict[str, Any]:
    """
    Get applications assigned to officer with optional filters.
    Filters by assigned_officer_id (direct assignment) AND current_stage
    so only applications in the officer's stage are returned.
    """
    if not officer or not officer.officer_id:
        logger.error("Invalid officer context provided to get_officer_applications")
        return {"count": 0, "applications": [], "error": "Invalid officer context"}
    
    try:
        from sqlalchemy.orm import selectinload

        # Get the officer's stage (SIS/DIS/SD/Tahsildar)
        officer_stage = officer.officer_stage
        
        where_clauses = [Application.assigned_officer_id == officer.officer_id]
        is_historical = (
            (isinstance(status, list) and any(s in ["approved", "rejected", "closed"] for s in status))
            or (isinstance(status, str) and status in ["approved", "rejected", "closed"])
        )
        if not is_historical:
            where_clauses.append(Application.current_stage == officer_stage)

        query = select(Application).options(
            selectinload(Application.survey_number).selectinload(SurveyNumber.block).selectinload(Block.ward).selectinload(Ward.town).selectinload(Town.taluk).selectinload(Taluk.district),
            selectinload(Application.application_sub_divisions).selectinload(ApplicationSubDivision.sub_division)
        ).where(and_(*where_clauses))

        if status:
            if isinstance(status, list):
                query = query.where(Application.current_status.in_(status))
            else:
                query = query.where(Application.current_status == status)
        
        if application_type:
            query = query.where(Application.application_type == application_type)
        
        # Filter by submission year if provided
        if submission_year:
            from sqlalchemy import extract
            query = query.where(extract('year', Application.submission_date) == submission_year)
        
        # Filter by submission month if provided
        if submission_month:
            from sqlalchemy import extract
            query = query.where(extract('month', Application.submission_date) == submission_month)
        
        result = await db.execute(query)
        applications = result.scalars().all()
        
        app_rows = []
        for app in applications:
            sn = app.survey_number
            block = sn.block if sn else None
            ward = block.ward if block else None
            town = ward.town if ward else None
            
            # VALIDATION: Log missing relationships for debugging
            if not sn:
                logger.warning(f"Application {app.application_number} missing survey_number relationship")
            elif not block:
                logger.warning(f"Survey {sn.survey_no} (app {app.application_number}) missing block relationship")
            elif not ward:
                logger.warning(f"Block {block.block_number} (app {app.application_number}) missing ward relationship")
            elif not town:
                logger.warning(f"Ward {ward.ward_number} (app {app.application_number}) missing town relationship")
            
            taluk = town.taluk if town else None
            district = taluk.district if taluk else None
            jur_dict = {
                "district": district.name if district else "N/A",
                "taluk": taluk.name if taluk else "N/A",
                "town": town.name if town else "N/A",
                "ward": ward.ward_number if ward else "N/A",
                "block": block.block_number if block else "N/A"
            }
            
            # Build merge-specific fields if this is a MERGE application
            if app.application_type == "MERGE":
                subdivisions_being_merged = []
                total_area = 0.0
                for assoc in app.application_sub_divisions:
                    sd = assoc.sub_division
                    if sd:
                        raw_area = assoc.proposed_area_sqm or sd.area_sqm
                        area = float(raw_area) if raw_area else None
                        if area:
                            total_area += area
                        subdivisions_being_merged.append({
                            "sub_division_no": sd.sub_division_no,
                            "area_sqm": area
                        })
                
                app_rows.append({
                    "application_number": app.application_number,
                    "type": app.application_type,
                    "status": app.current_status,
                    "stage": app.current_stage,
                    "submission_date": app.submission_date.isoformat() if app.submission_date else None,
                    "is_overdue": app.is_overdue,
                    "survey_no": sn.survey_no if sn else "N/A",
                    "subdivisions_being_merged": subdivisions_being_merged,
                    "total_merge_area_sqm": total_area if total_area > 0 else None,
                    "district_name": district.name if district else "N/A",
                    "taluk_name": taluk.name if taluk else "N/A",
                    "town_name": town.name if town else "N/A",
                    "ward_number": ward.ward_number if ward else "N/A",
                    "block_number": block.block_number if block else "N/A",
                    "jurisdiction": jur_dict
                })
            else:
                app_rows.append({
                    "application_number": app.application_number,
                    "type": app.application_type,
                    "status": app.current_status,
                    "stage": app.current_stage,
                    "submission_date": app.submission_date.isoformat() if app.submission_date else None,
                    "is_overdue": app.is_overdue,
                    "district_name": district.name if district else "N/A",
                    "taluk_name": taluk.name if taluk else "N/A",
                    "town_name": town.name if town else "N/A",
                    "ward_number": ward.ward_number if ward else "N/A",
                    "block_number": block.block_number if block else "N/A",
                    "jurisdiction": jur_dict,
                    "included_subdivisions": ", ".join([
                        assoc.sub_division.sub_division_no
                        for assoc in app.application_sub_divisions
                        if assoc.sub_division and assoc.sub_division.sub_division_no
                    ]) or "None"
                })
        
        return {
            "count": len(app_rows),
            "applications": app_rows,
            "jurisdiction_type": officer.jurisdiction_type
        }
    except Exception as e:
        logger.error(f"Error getting officer applications: {e}")
        return {"count": 0, "applications": [], "error": str(e)}


async def get_pending_applications(
    db: AsyncSession,
    officer: OfficerContext,
    application_type: Optional[str] = None,
    status: Optional[Any] = None,
    submission_year: Optional[int] = None,
    submission_month: Optional[int] = None
) -> Dict[str, Any]:
    """
    Get applications for officer with optional status, year, and month filter.
    By default, returns both 'pending' and 'in_progress' applications.
    Pass a specific status string or list to filter differently.
    Pass submission_year to filter by year (e.g., 2025).
    Pass submission_month to filter by month (1-12).
    """
    if status is None and not submission_year:
        status = ["pending", "in_progress"]
    return await get_officer_applications(
        db, officer, 
        status=status, 
        application_type=application_type, 
        submission_year=submission_year,
        submission_month=submission_month
    )


async def get_overdue_applications(
    db: AsyncSession,
    officer: OfficerContext,
    application_type: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get overdue applications within officer's jurisdiction AND stage
    """
    try:
        # Get jurisdiction filters
        jurisdiction_filters = await get_jurisdiction_filter(db, officer)
        
        if not jurisdiction_filters:
            return {"count": 0, "applications": [], "message": "No jurisdiction assigned"}
        
        # Get the officer's stage
        officer_stage = officer.officer_stage
        
        query = select(Application).join(
            SurveyNumber, Application.survey_number_id == SurveyNumber.id
        ).join(
            Block, SurveyNumber.block_id == Block.id
        ).join(
            Ward, Block.ward_id == Ward.id
        ).join(
            Town, Ward.town_id == Town.id
        ).join(
            Taluk, Town.taluk_id == Taluk.id
        ).join(
            District, Taluk.district_id == District.id
        ).where(
            and_(
                or_(*jurisdiction_filters),
                Application.current_stage == officer_stage,  # CRITICAL FIX: Filter by stage
                Application.is_overdue == True
            )
        )
        
        if application_type:
            if application_type == "ISD":
                query = query.where(Application.application_type.in_(["ISD", "MERGE"]))
            else:
                query = query.where(Application.application_type == application_type)
            
        result = await db.execute(query)
        applications = result.scalars().all()
        
        return {
            "count": len(applications),
            "jurisdiction_type": officer.jurisdiction_type,
            "applications": [
                {
                    "application_number": app.application_number,
                    "type": app.application_type,
                    "status": app.current_status,
                    "stage": app.current_stage,
                    "submission_date": app.submission_date.isoformat(),
                    "days_overdue": (datetime.now().date() - app.submission_date).days - 15,
                    "is_overdue": True  # Explicitly mark as overdue for HTML formatter
                }
                for app in applications
            ]
        }
    except Exception as e:
        logger.error(f"Error getting overdue applications: {e}")
        return {"count": 0, "applications": [], "error": str(e)}


async def get_highest_priority_applications(
    db: AsyncSession,
    officer: OfficerContext,
    application_type: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get highest priority applications (overdue OR priority flagged) within officer's jurisdiction AND stage
    """
    try:
        # Get jurisdiction filters
        jurisdiction_filters = await get_jurisdiction_filter(db, officer)
        
        if not jurisdiction_filters:
            return {"count": 0, "applications": [], "message": "No jurisdiction assigned"}
        
        # Get the officer's stage
        officer_stage = officer.officer_stage
        
        query = select(Application).join(
            SurveyNumber, Application.survey_number_id == SurveyNumber.id
        ).join(
            Block, SurveyNumber.block_id == Block.id
        ).join(
            Ward, Block.ward_id == Ward.id
        ).join(
            Town, Ward.town_id == Town.id
        ).join(
            Taluk, Town.taluk_id == Taluk.id
        ).join(
            District, Taluk.district_id == District.id
        ).where(
            and_(
                or_(*jurisdiction_filters),
                Application.current_stage == officer_stage,
                Application.current_status != 'rejected',  # Exclude rejected apps
                or_(
                    Application.is_overdue == True,
                    Application.priority_flag == True
                )
            )
        )
        
        if application_type:
            if application_type == "ISD":
                query = query.where(Application.application_type.in_(["ISD", "MERGE"]))
            else:
                query = query.where(Application.application_type == application_type)
            
        result = await db.execute(query)
        applications = result.scalars().all()
        
        return {
            "count": len(applications),
            "jurisdiction_type": officer.jurisdiction_type,
            "applications": [
                {
                    "application_number": app.application_number,
                    "type": app.application_type,
                    "status": app.current_status,
                    "stage": app.current_stage,
                    "submission_date": app.submission_date.isoformat(),
                    "is_overdue": app.is_overdue,
                    "priority_flag": app.priority_flag,
                    "field_visit_scheduled": app.field_visit_scheduled,
                    "field_visit_date": app.field_visit_date.isoformat() if app.field_visit_date else None,
                    "days_pending": (datetime.now().date() - app.submission_date).days
                }
                for app in applications
            ]
        }
    except Exception as e:
        logger.error(f"Error getting highest priority applications: {e}")
        return {"count": 0, "applications": [], "error": str(e)}


async def get_officer_workload(
    db: AsyncSession,
    officer: OfficerContext
) -> Dict[str, Any]:
    """
    Get officer workload summary for their jurisdiction AND stage
    """
    try:
        # Get jurisdiction filters
        jurisdiction_filters = await get_jurisdiction_filter(db, officer)
        
        if not jurisdiction_filters:
            return {"total_active": 0, "ISD": 0, "NISD": 0, "MERGE": 0, "overdue": 0, "message": "No jurisdiction assigned"}
        
        # Get the officer's stage
        officer_stage = officer.officer_stage
        
        # Base query with jurisdiction joins AND stage filter
        base_query = select(func.count(Application.id)).join(
            SurveyNumber, Application.survey_number_id == SurveyNumber.id
        ).join(
            Block, SurveyNumber.block_id == Block.id
        ).join(
            Ward, Block.ward_id == Ward.id
        ).join(
            Town, Ward.town_id == Town.id
        ).join(
            Taluk, Town.taluk_id == Taluk.id
        ).join(
            District, Taluk.district_id == District.id
        ).where(
            and_(
                or_(*jurisdiction_filters),
                Application.current_stage == officer_stage  # CRITICAL FIX: Filter by stage
            )
        )
        
        # Count by application type
        isd_count = await db.execute(
            base_query.where(
                and_(
                    Application.application_type == "ISD",
                    Application.current_status.in_(["pending", "in_progress"])
                )
            )
        )
        
        nisd_count = await db.execute(
            base_query.where(
                and_(
                    Application.application_type == "NISD",
                    Application.current_status.in_(["pending", "in_progress"])
                )
            )
        )
        
        merge_count = await db.execute(
            base_query.where(
                and_(
                    Application.application_type == "MERGE",
                    Application.current_status.in_(["pending", "in_progress"])
                )
            )
        )
        
        overdue_count = await db.execute(
            base_query.where(
                Application.is_overdue == True
            )
        )
        
        # Count unscheduled field visits
        unscheduled_count = await db.execute(
            base_query.where(
                and_(
                    Application.field_visit_scheduled == False,
                    Application.current_status.in_(["pending", "in_progress"])
                )
            )
        )
        
        isd_val = isd_count.scalar() or 0
        nisd_val = nisd_count.scalar() or 0
        merge_val = merge_count.scalar() or 0
        overdue_val = overdue_count.scalar() or 0
        unscheduled_val = unscheduled_count.scalar() or 0
        
        return {
            "total_active": isd_val + nisd_val + merge_val,
            "ISD": isd_val,
            "NISD": nisd_val,
            "MERGE": merge_val,
            "overdue": overdue_val,
            "unscheduled_visits": unscheduled_val
        }
    except Exception as e:
        logger.error(f"Error getting officer workload: {e}")
        return {"total_active": 0, "error": str(e)}


async def get_application_detail(
    db: AsyncSession,
    application_number: str,
    officer: OfficerContext = None
) -> Dict[str, Any]:
    """
    Get detailed information about a specific application including applicant details and sub-divisions
    """
    try:
        from sqlalchemy.orm import joinedload, selectinload
        query = select(Application).options(
            joinedload(Application.applicant),
            selectinload(Application.survey_number),
            selectinload(Application.application_sub_divisions).joinedload(ApplicationSubDivision.sub_division),
            selectinload(Application.field_visits)
        ).where(
            Application.application_number == application_number
        )
        
        # If officer provided, verify jurisdiction access using a subquery
        # to avoid conflicting with selectinload on survey_number
        if officer:
            jurisdiction_filters = await get_jurisdiction_filter(db, officer)
            
            if jurisdiction_filters:
                # Use a subquery to check jurisdiction without interfering with eager loads
                jurisdiction_subquery = (
                    select(Application.id)
                    .join(SurveyNumber, Application.survey_number_id == SurveyNumber.id)
                    .join(Block, SurveyNumber.block_id == Block.id)
                    .join(Ward, Block.ward_id == Ward.id)
                    .join(Town, Ward.town_id == Town.id)
                    .join(Taluk, Town.taluk_id == Taluk.id)
                    .join(District, Taluk.district_id == District.id)
                    .where(or_(*jurisdiction_filters))
                    .scalar_subquery()
                )
                query = query.where(Application.id.in_(jurisdiction_subquery))
        
        result = await db.execute(query)
        app = result.scalar_one_or_none()
        
        if not app:
            return {"found": False, "message": f"Application {application_number} not found"}
            
        # Compile proposed sub-divisions with full detail
        sub_divisions_list = []
        proposed_sub_divisions = []
        for assoc in app.application_sub_divisions:
            sub_div_no = assoc.proposed_sub_division_no or (
                assoc.sub_division.sub_division_no if assoc.sub_division else None
            )
            if sub_div_no:
                sub_divisions_list.append(sub_div_no)
                proposed_sub_divisions.append({
                    "proposed_sub_division_no": sub_div_no,
                    "proposed_area_sqm": (
                        float(assoc.proposed_area_sqm) if assoc.proposed_area_sqm
                        else (float(assoc.sub_division.area_sqm) if assoc.sub_division and assoc.sub_division.area_sqm else None)
                    ),
                    "status": assoc.status or "pending",
                })

        # Fetch documents
        from backend.models import ApplicationDocument, PattaTransfer
        doc_query = select(ApplicationDocument).where(ApplicationDocument.application_id == app.id)
        doc_result = await db.execute(doc_query)
        docs = doc_result.scalars().all()

        documents_list = [
            {
                "document_type": d.document_type,
                "document_name": d.document_name,
                "is_uploaded": d.is_uploaded,
                "is_verified": d.is_verified
            }
            for d in docs
        ]

        # Count patta transfers for this application
        pt_query = select(PattaTransfer).where(PattaTransfer.application_id == app.id)
        pt_result = await db.execute(pt_query)
        patta_transfers = pt_result.scalars().all()

        # Survey totals for area comparison
        survey_total_area = float(app.survey_number.total_area_sqm) if app.survey_number and app.survey_number.total_area_sqm else None
        # Sum proposed area — prefer explicit proposed_area_sqm; fall back to sub_division.area_sqm
        proposed_total_area = None
        if proposed_sub_divisions:
            areas = [sd["proposed_area_sqm"] for sd in proposed_sub_divisions if sd["proposed_area_sqm"] is not None]
            if areas:
                proposed_total_area = sum(areas)

        # MERGE: build subdivisions_being_merged list
        subdivisions_being_merged = []
        total_merge_area = 0.0
        if app.application_type == "MERGE" and app.application_sub_divisions:
            for app_subdiv in app.application_sub_divisions:
                if app_subdiv.sub_division:
                    area = float(app_subdiv.sub_division.area_sqm) if app_subdiv.sub_division.area_sqm else None
                    subdivisions_being_merged.append({
                        "sub_division_no": app_subdiv.sub_division.sub_division_no,
                        "area_sqm": area,
                        "proposed_sub_division_no": app_subdiv.proposed_sub_division_no,
                        "status": app_subdiv.status
                    })
                    if area:
                        total_merge_area += area

        # Field visit: most recent entry from FieldVisit table
        field_visit_info = None
        if app.field_visits:
            latest_visit = max(app.field_visits, key=lambda v: v.created_at)
            field_visit_info = {
                "status": latest_visit.status,
                "scheduled_date": latest_visit.scheduled_date.isoformat() if latest_visit.scheduled_date else None,
                "actual_date": latest_visit.actual_date.isoformat() if latest_visit.actual_date else None,
                "encroachment_found": latest_visit.encroachment_found,
                "area_verified": latest_visit.area_verified
            }

        return {
            "found": True,
            "application_number": app.application_number,
            "type": app.application_type,
            "status": app.current_status,
            "stage": app.current_stage,
            "submission_date": app.submission_date.isoformat(),
            "submission_channel": app.submission_channel,
            "is_overdue": app.is_overdue,
            "priority_flag": app.priority_flag,
            # Applicant
            "applicant_name": app.applicant.name if app.applicant else None,
            "applicant_mobile": app.applicant.mobile if app.applicant else None,
            "applicant_email": app.applicant.email if app.applicant else None,
            "applicant_address": app.applicant.address if app.applicant else None,
            "applicant_aadhaar_last4": app.applicant.aadhaar_last4 if app.applicant else None,
            "applicant": {
                "name": app.applicant.name,
                "mobile": app.applicant.mobile,
                "email": app.applicant.email,
                "address": app.applicant.address
            } if app.applicant else None,
            # Sub-divisions
            "included_subdivisions": ", ".join(sub_divisions_list) if sub_divisions_list else "None",
            "proposed_sub_divisions": proposed_sub_divisions,
            "proposed_sub_divisions_count": len(proposed_sub_divisions),
            # MERGE-specific
            "subdivisions_being_merged": subdivisions_being_merged,
            "total_merge_area_sqm": total_merge_area if subdivisions_being_merged else None,
            # Patta transfers
            "patta_transfers_count": len(patta_transfers),
            "patta_transfers": [
                {"transfer_order_number": pt.transfer_order_number, "status": pt.status}
                for pt in patta_transfers
            ],
            # Survey
            "survey_no": app.survey_number.survey_no if app.survey_number else "N/A",
            "survey_total_area_sqm": survey_total_area,
            "proposed_total_area_sqm": proposed_total_area,
            "area_match": abs(survey_total_area - proposed_total_area) < 1.0 if (survey_total_area and proposed_total_area) else None,
            # Application details
            "declared_reason": app.declared_reason,
            "sale_deed_number": app.sale_deed_number,
            "sale_deed_registered": app.sale_deed_registered,
            "documents": documents_list,
            # Field visit
            "field_visit": field_visit_info,
            "field_visit_scheduled": app.field_visit_scheduled,
            "field_visit_date": app.field_visit_date.isoformat() if app.field_visit_date else None,
        }
        
    except Exception as e:
        logger.error(f"Error getting application detail: {e}")
        return {"found": False, "error": str(e)}


async def get_survey_detail(
    db: AsyncSession,
    survey_no: str,
    officer: OfficerContext = None
) -> Dict[str, Any]:
    """
    Get details about a survey number including full jurisdiction chain.
    If officer is provided, verifies they have jurisdiction access.
    Handles subdivisions like "145/1A" by resolving base survey "145".
    """
    try:
        base_survey_no = survey_no.split('/')[0] if '/' in survey_no else survey_no

        # Get survey with all related jurisdiction data
        query = select(
            SurveyNumber,
            Block,
            Ward,
            Town,
            Taluk,
            District
        ).join(
            Block, SurveyNumber.block_id == Block.id
        ).join(
            Ward, Block.ward_id == Ward.id
        ).join(
            Town, Ward.town_id == Town.id
        ).join(
            Taluk, Town.taluk_id == Taluk.id
        ).join(
            District, Taluk.district_id == District.id
        ).where(
            or_(
                SurveyNumber.survey_no == survey_no,
                SurveyNumber.survey_no == base_survey_no
            )
        )
        
        # If officer provided, verify jurisdiction access
        if officer:
            jurisdiction_filters = await get_jurisdiction_filter(db, officer)
            if jurisdiction_filters:
                query = query.where(or_(*jurisdiction_filters))
        
        result = await db.execute(query)
        row = result.first()
        
        if not row:
            return {"found": False, "message": f"Survey number {survey_no} not found or not accessible"}
        
        survey, block, ward, town, taluk, district = row
        
        # Get sub-divisions
        subdiv_query = select(SubDivision).where(
            and_(
                SubDivision.survey_number_id == survey.id,
                SubDivision.status == "active"
            )
        )
        subdiv_result = await db.execute(subdiv_query)
        subdivisions = subdiv_result.scalars().all()
        
        return {
            "found": True,
            "survey_no": survey_no,
            "base_survey_no": survey.survey_no,
            "total_area_sqm": float(survey.total_area_sqm),
            "land_type": survey.land_type,
            "patta_number": survey.patta_number,
            "has_encroachment": survey.has_encroachment,
            "has_litigation": survey.has_litigation,
            "jurisdiction": {
                "district": district.name,
                "taluk": taluk.name,
                "town": town.name,
                "ward": ward.ward_name,
                "block": block.block_name
            },
            "sub_divisions_count": len(subdivisions),
            "sub_divisions": [
                {
                    "sub_division_no": sd.sub_division_no,
                    "area_sqm": float(sd.area_sqm),
                    "status": sd.status
                }
                for sd in subdivisions
            ]
        }
    except Exception as e:
        logger.error(f"Error getting survey detail: {e}")
        return {"found": False, "error": str(e)}


async def get_survey_owners(
    db: AsyncSession,
    survey_no: str,
    officer: OfficerContext = None
) -> Dict[str, Any]:
    """
    Get ownership information for a survey number, including per-subdivision owners.
    Handles subdivisions like "145/1A" by resolving base survey "145".
    """
    try:
        base_survey_no = survey_no.split('/')[0] if '/' in survey_no else survey_no

        # First get survey with jurisdiction check
        survey_query = select(
            SurveyNumber,
            Block,
            Ward,
            Town,
            Taluk,
            District
        ).join(
            Block, SurveyNumber.block_id == Block.id
        ).join(
            Ward, Block.ward_id == Ward.id
        ).join(
            Town, Ward.town_id == Town.id
        ).join(
            Taluk, Town.taluk_id == Taluk.id
        ).join(
            District, Taluk.district_id == District.id
        ).where(
            or_(
                SurveyNumber.survey_no == survey_no,
                SurveyNumber.survey_no == base_survey_no
            )
        )
        
        # If officer provided, verify jurisdiction access
        if officer:
            jurisdiction_filters = await get_jurisdiction_filter(db, officer)
            if jurisdiction_filters:
                survey_query = survey_query.where(or_(*jurisdiction_filters))
        
        survey_result = await db.execute(survey_query)
        row = survey_result.first()
        
        if not row:
            return {"found": False, "message": f"Survey number {survey_no} not found or not accessible"}
        
        survey = row[0]
        
        # Get all ownerships (both survey-level and sub-division-level)
        from backend.models import SubDivision
        ownership_query = select(SurveyOwnership, Owner, SubDivision).join(
            Owner, SurveyOwnership.owner_id == Owner.id
        ).outerjoin(
            SubDivision, SurveyOwnership.sub_division_id == SubDivision.id
        ).where(
            SurveyOwnership.survey_number_id == survey.id
        ).order_by(SubDivision.sub_division_no)
        
        result = await db.execute(ownership_query)
        ownerships = result.all()
        
        # Group by sub-division (None = survey-level)
        owners_list = []
        for ownership, owner, subdivision in ownerships:
            sd_no = subdivision.sub_division_no if subdivision else "Survey Level"
            # Filter if a specific subdivision was requested
            if '/' in survey_no and sd_no != "Survey Level" and sd_no.lower() != survey_no.lower():
                continue
            owners_list.append({
                "name": owner.name,
                "name_tamil": owner.name_tamil,
                "sub_division": sd_no,
                "ownership_share": float(ownership.ownership_share) if ownership.ownership_share else None,
                "ownership_type": ownership.ownership_type,
                "is_joint_owner": ownership.is_joint_owner
            })
        
        # Fallback to all owners if filtering resulted in empty list
        if not owners_list and ownerships:
            for ownership, owner, subdivision in ownerships:
                owners_list.append({
                    "name": owner.name,
                    "name_tamil": owner.name_tamil,
                    "sub_division": subdivision.sub_division_no if subdivision else "Survey Level",
                    "ownership_share": float(ownership.ownership_share) if ownership.ownership_share else None,
                    "ownership_type": ownership.ownership_type,
                    "is_joint_owner": ownership.is_joint_owner
                })
        
        return {
            "found": True,
            "survey_no": survey_no,
            "owners": owners_list
        }
    except Exception as e:
        logger.error(f"Error getting survey owners: {e}")
        return {"found": False, "error": str(e)}


async def get_unscheduled_visits(
    db: AsyncSession,
    officer: OfficerContext
) -> Dict[str, Any]:
    """
    Get applications awaiting field visit scheduling within officer's jurisdiction
    """
    try:
        jurisdiction_filters = await get_jurisdiction_filter(db, officer)
        
        if not jurisdiction_filters:
            return {"count": 0, "applications": [], "message": "No jurisdiction assigned"}
        
        query = select(Application).options(
            selectinload(Application.survey_number)
        ).join(
            SurveyNumber, Application.survey_number_id == SurveyNumber.id
        ).join(
            Block, SurveyNumber.block_id == Block.id
        ).join(
            Ward, Block.ward_id == Ward.id
        ).join(
            Town, Ward.town_id == Town.id
        ).join(
            Taluk, Town.taluk_id == Taluk.id
        ).join(
            District, Taluk.district_id == District.id
        ).where(
            and_(
                or_(*jurisdiction_filters),
                Application.application_type == "ISD",
                Application.field_visit_scheduled == False,
                Application.current_stage == "SIS"
            )
        )
        
        result = await db.execute(query)
        applications = result.scalars().all()
        
        return {
            "count": len(applications),
            "visits": [
                {
                    "application_number": app.application_number,
                    "type": app.application_type,
                    "status": app.current_status,
                    "stage": app.current_stage,
                    "submission_date": app.submission_date.isoformat(),
                    "days_since_submission": (datetime.now().date() - app.submission_date).days,
                    "is_overdue": app.is_overdue
                }
                for app in applications
            ]
        }
    except Exception as e:
        logger.error(f"Error getting unscheduled visits: {e}")
        return {"count": 0, "visits": [], "error": str(e)}


async def get_field_visits(
    db: AsyncSession,
    officer: OfficerContext,
    status_filter: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    to_be_visited_only: bool = False
) -> Dict[str, Any]:
    """
    Get field visits for the officer with optional status, date range (start_date to end_date),
    and to-be-visited filtering.
    """
    try:
        from sqlalchemy.orm import joinedload
        from sqlalchemy import or_
        from datetime import date as _dt_date
        
        today = _dt_date.today()
        
        # Get officer's jurisdiction filter (block/ward/taluk)
        jurisdiction_filter = await get_jurisdiction_filter(db, officer)
        jur_conditions = jurisdiction_filter if isinstance(jurisdiction_filter, list) else [jurisdiction_filter]
        
        query = select(FieldVisit).options(
            joinedload(FieldVisit.application).joinedload(Application.survey_number).joinedload(SurveyNumber.block)
        ).join(
            Application, FieldVisit.application_id == Application.id
        ).join(
            SurveyNumber, Application.survey_number_id == SurveyNumber.id
        ).join(
            Block, SurveyNumber.block_id == Block.id
        ).where(
            and_(
                FieldVisit.officer_id == officer.officer_id,
                Application.current_stage == officer.officer_stage,
                Application.current_status != 'rejected',  # Exclude field visits for rejected applications
                *jur_conditions
            )
        )
        
        if status_filter:
            query = query.where(FieldVisit.status == status_filter)

        if to_be_visited_only:
            query = query.where(FieldVisit.status.in_(["scheduled", "rescheduled", "pending", "overdue", "unscheduled"]))

        if start_date:
            query = query.where(FieldVisit.scheduled_date >= start_date)

        if end_date:
            query = query.where(FieldVisit.scheduled_date <= end_date)
            
        query = query.order_by(FieldVisit.scheduled_date.asc())

        result = await db.execute(query)
        visits = result.scalars().all()
        
        field_visits = []
        to_be_visited_count = 0
        completed_count = 0
        overdue_count = 0

        for visit in visits:
            app = visit.application
            survey = app.survey_number if app else None
            block = survey.block if survey else None
            
            # Check if field visit is overdue (scheduled date in past and status is not completed)
            is_overdue = False
            if visit.scheduled_date and visit.status in ["scheduled", "rescheduled", "overdue"]:
                is_overdue = visit.scheduled_date < today
            
            if is_overdue or visit.status == "overdue":
                overdue_count += 1

            if visit.status in ["completed"]:
                completed_count += 1
            else:
                to_be_visited_count += 1

            field_visits.append({
                "application_number": app.application_number if app else "N/A",
                "survey_no": survey.survey_no if survey else "N/A",
                "block_number": block.block_number if block else None,
                "application_type": app.application_type if app else "N/A",
                "status": visit.status,
                "field_visit_date": visit.scheduled_date.isoformat() if visit.scheduled_date else None,
                "is_overdue": is_overdue
            })
        
        return {
            "count": len(field_visits),
            "to_be_visited_count": to_be_visited_count,
            "completed_count": completed_count,
            "overdue_count": overdue_count,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "field_visits": field_visits
        }
    except Exception as e:
        logger.error(f"Error getting field visits: {e}")
        return {"count": 0, "to_be_visited_count": 0, "completed_count": 0, "field_visits": [], "error": str(e)}


async def get_next_subdivision_number(
    db: AsyncSession,
    survey_no: str,
    officer: OfficerContext = None
) -> Dict[str, Any]:
    """
    Get the next available sub-division number for a survey.
    If officer is provided, verifies they have jurisdiction access.
    Handles subdivisions like "145/1A" by resolving base survey "145".
    """
    try:
        base_survey_no = survey_no.split('/')[0] if '/' in survey_no else survey_no

        # Get survey with jurisdiction check
        survey_query = select(
            SurveyNumber,
            Block,
            Ward,
            Town,
            Taluk,
            District
        ).join(
            Block, SurveyNumber.block_id == Block.id
        ).join(
            Ward, Block.ward_id == Ward.id
        ).join(
            Town, Ward.town_id == Town.id
        ).join(
            Taluk, Town.taluk_id == Taluk.id
        ).join(
            District, Taluk.district_id == District.id
        ).where(
            or_(
                SurveyNumber.survey_no == survey_no,
                SurveyNumber.survey_no == base_survey_no
            )
        )
        
        # If officer provided, verify jurisdiction access
        if officer:
            jurisdiction_filters = await get_jurisdiction_filter(db, officer)
            if jurisdiction_filters:
                survey_query = survey_query.where(or_(*jurisdiction_filters))
        
        survey_result = await db.execute(survey_query)
        row = survey_result.first()
        
        if not row:
            return {"found": False, "message": f"Survey number {survey_no} not found or not accessible"}
        
        survey = row[0]
        
        # Get existing sub-divisions
        subdiv_query = select(SubDivision).where(
            SubDivision.survey_number_id == survey.id
        ).order_by(desc(SubDivision.sub_division_no))
        
        result = await db.execute(subdiv_query)
        subdivisions = result.scalars().all()
        
        if not subdivisions:
            next_no = f"{survey_no}/1"
        else:
            highest = subdivisions[0].sub_division_no
            # Simple logic: extract number and increment
            # This is simplified - real logic would be more complex
            next_no = f"{survey_no}/{len(subdivisions) + 1}"
        
        return {
            "found": True,
            "survey_no": survey_no,
            "existing_count": len(subdivisions),
            "highest_existing": subdivisions[0].sub_division_no if subdivisions else "None",
            "next_available": next_no
        }
    except Exception as e:
        logger.error(f"Error getting next subdivision number: {e}")
        return {"found": False, "error": str(e)}


async def get_ward_surveys(
    db: AsyncSession,
    ward_identifier: str,
    block_identifier: str = None,
    officer: OfficerContext = None
) -> Dict[str, Any]:
    """
    Get all survey numbers and subdivisions within a ward (and optionally a specific block).
    If officer is provided, verifies they have jurisdiction access.
    
    Args:
        db: Database session
        ward_identifier: Ward number or name (e.g., "12", "Ward 12", "5")
        block_identifier: Optional block identifier (e.g., "B1", "Block B1")
        officer: Officer context for jurisdiction validation
        
    Returns:
        Dictionary with survey numbers grouped by block with their subdivisions
    """
    try:
        # Extract ward number from identifier
        import re
        ward_num_match = re.search(r'\d+', ward_identifier)
        ward_num = ward_num_match.group(0) if ward_num_match else ward_identifier
        
        # Build base query
        query = select(
            SurveyNumber,
            Block,
            Ward,
            Town,
            Taluk,
            District
        ).join(
            Block, SurveyNumber.block_id == Block.id
        ).join(
            Ward, Block.ward_id == Ward.id
        ).join(
            Town, Ward.town_id == Town.id
        ).join(
            Taluk, Town.taluk_id == Taluk.id
        ).join(
            District, Taluk.district_id == District.id
        ).where(
            Ward.ward_number == ward_num
        )
        
        # Add block filter if specified
        if block_identifier:
            block_num_match = re.search(r'[A-Z]?\d+', block_identifier.upper())
            block_num = block_num_match.group(0) if block_num_match else block_identifier
            query = query.where(Block.block_number == block_num)
        
        # If officer provided, verify jurisdiction access
        if officer:
            jurisdiction_filters = await get_jurisdiction_filter(db, officer)
            if jurisdiction_filters:
                query = query.where(or_(*jurisdiction_filters))
        
        result = await db.execute(query)
        rows = result.all()
        
        if not rows:
            message = f"No surveys found in Ward {ward_num}"
            if block_identifier:
                message += f", Block {block_identifier}"
            if officer:
                message += " or not accessible in your jurisdiction"
            return {"found": False, "message": message}
        
        # Get first row for jurisdiction info
        _, _, ward, town, taluk, district = rows[0]
        
        # Group surveys by block
        surveys_by_block = {}
        survey_ids = [row[0].id for row in rows]
        
        # Get all subdivisions for these surveys
        subdiv_query = select(SubDivision).where(
            and_(
                SubDivision.survey_number_id.in_(survey_ids),
                SubDivision.status == "active"
            )
        ).order_by(SubDivision.sub_division_no)
        
        subdiv_result = await db.execute(subdiv_query)
        all_subdivisions = subdiv_result.scalars().all()
        
        # Create a mapping of survey_id to subdivisions
        subdiv_map = {}
        for sd in all_subdivisions:
            if sd.survey_number_id not in subdiv_map:
                subdiv_map[sd.survey_number_id] = []
            subdiv_map[sd.survey_number_id].append(sd.sub_division_no)
        
        # Organize data
        for survey, block, _, _, _, _ in rows:
            block_key = block.block_name
            
            if block_key not in surveys_by_block:
                surveys_by_block[block_key] = []
            
            subdivisions = subdiv_map.get(survey.id, [])
            
            surveys_by_block[block_key].append({
                "survey_no": survey.survey_no,
                "area_sqm": float(survey.total_area_sqm),
                "land_type": survey.land_type,
                "patta_number": survey.patta_number,
                "subdivisions": subdivisions,
                "subdivision_count": len(subdivisions)
            })
        
        return {
            "found": True,
            "jurisdiction": {
                "district": district.name,
                "taluk": taluk.name,
                "town": town.name,
                "ward": ward.ward_name,
                "ward_number": ward.ward_number
            },
            "total_surveys": len(rows),
            "surveys_by_block": surveys_by_block
        }
        
    except Exception as e:
        logger.error(f"Error getting ward surveys: {e}")
        return {"found": False, "error": str(e)}



async def get_merge_application_detail(
    db: AsyncSession,
    application_number: str = None,
    officer: OfficerContext = None
) -> Dict[str, Any]:
    """
    Get detailed information about merge applications including survey numbers and areas.
    If application_number is not provided, returns all active merge applications in officer's jurisdiction.
    
    Args:
        db: Database session
        application_number: Optional specific merge application number
        officer: Officer context for jurisdiction validation
        
    Returns:
        Dictionary with merge application details including survey areas
    """
    try:
        # Base query for merge applications
        query = select(
            Application,
            SurveyNumber,
            Block,
            Ward,
            Town,
            Taluk,
            District
        ).join(
            SurveyNumber, Application.survey_number_id == SurveyNumber.id
        ).join(
            Block, SurveyNumber.block_id == Block.id
        ).join(
            Ward, Block.ward_id == Ward.id
        ).join(
            Town, Ward.town_id == Town.id
        ).join(
            Taluk, Town.taluk_id == Taluk.id
        ).join(
            District, Taluk.district_id == District.id
        ).where(
            Application.application_type == "MERGE"
        )
        
        # Filter by specific application number if provided
        if application_number:
            query = query.where(Application.application_number == application_number)
        
        # If officer provided, verify jurisdiction access
        if officer:
            jurisdiction_filters = await get_jurisdiction_filter(db, officer)
            if jurisdiction_filters:
                query = query.where(or_(*jurisdiction_filters))
            
            # Also filter by active status if not searching specific application
            if not application_number:
                query = query.where(
                    Application.current_status.in_(["pending", "in_progress"])
                )
        
        result = await db.execute(query)
        rows = result.all()
        
        if not rows:
            message = f"No merge applications found"
            if application_number:
                message = f"Merge application {application_number} not found or not accessible"
            return {"found": False, "count": 0, "applications": [], "message": message}
        
        # Collect survey IDs and application sub-divisions
        survey_ids = set()
        app_subdiv_map = {}
        
        for app, survey, _, _, _, _, _ in rows:
            survey_ids.add(survey.id)
            if app.id not in app_subdiv_map:
                app_subdiv_map[app.id] = {
                    "app": app,
                    "survey": survey,
                    "subdivisions": []
                }
        
        # Get all sub-divisions involved in these merge applications
        logger.info(f"=== MERGE SUBDIVISION DEBUG ===")
        logger.info(f"Looking for subdivisions for {len(app_subdiv_map)} application(s)")
        logger.info(f"Application IDs: {list(app_subdiv_map.keys())}")
        
        # First check if ApplicationSubDivision records exist at all
        check_query = select(ApplicationSubDivision).where(
            ApplicationSubDivision.application_id.in_(app_subdiv_map.keys())
        )
        check_result = await db.execute(check_query)
        check_app_subdivs = check_result.scalars().all()
        logger.info(f"Found {len(check_app_subdivs)} ApplicationSubDivision records (before join)")
        
        for asd in check_app_subdivs:
            logger.info(f"  ApplicationSubDivision: app_id={asd.application_id}, subdiv_id={asd.sub_division_id}")
        
        # Now try the join query
        subdiv_query = select(
            ApplicationSubDivision,
            SubDivision
        ).join(
            SubDivision, ApplicationSubDivision.sub_division_id == SubDivision.id
        ).where(
            ApplicationSubDivision.application_id.in_(app_subdiv_map.keys())
        )
        
        subdiv_result = await db.execute(subdiv_query)
        app_subdivisions = subdiv_result.all()
        
        logger.info(f"Found {len(app_subdivisions)} ApplicationSubDivision records (after join)")
        
        # Map subdivisions to applications
        for app_subdiv, subdiv in app_subdivisions:
            logger.info(f"  App {app_subdiv.application_id} -> Subdiv {subdiv.sub_division_no} ({subdiv.area_sqm} sq.m)")
            if app_subdiv.application_id in app_subdiv_map:
                area = float(subdiv.area_sqm) if subdiv.area_sqm else None
                app_subdiv_map[app_subdiv.application_id]["subdivisions"].append({
                    "sub_division_no": subdiv.sub_division_no,
                    "area_sqm": area,
                    "proposed_sub_division_no": app_subdiv.proposed_sub_division_no,
                    "status": app_subdiv.status
                })
        
        # Log final counts
        for app_id, data in app_subdiv_map.items():
            logger.info(f"Application {data['app'].application_number}: {len(data['subdivisions'])} subdivisions")
        
        # Build response
        applications = []
        for app_data in app_subdiv_map.values():
            app = app_data["app"]
            survey = app_data["survey"]
            subdivisions = app_data["subdivisions"]
            
            # Get jurisdiction for this application
            app_row = next((row for row in rows if row[0].id == app.id), None)
            if app_row:
                _, _, block, ward, town, taluk, district = app_row
                
                total_area = sum(sd["area_sqm"] for sd in subdivisions if sd.get("area_sqm"))
                
                applications.append({
                    "application_number": app.application_number,
                    "status": app.current_status,
                    "stage": app.current_stage,
                    "submission_date": app.submission_date.isoformat(),
                    "survey_no": survey.survey_no,
                    "survey_total_area_sqm": float(survey.total_area_sqm),
                    "subdivisions_being_merged": subdivisions,
                    "subdivision_count": len(subdivisions),
                    "total_merge_area_sqm": total_area,
                    "jurisdiction": {
                        "district": district.name,
                        "taluk": taluk.name,
                        "town": town.name,
                        "ward": ward.ward_name or f"Ward {ward.ward_number}",
                        "block": block.block_name or f"Block {block.block_number}"
                    },
                    "field_visit_scheduled": app.field_visit_scheduled,
                    "field_visit_date": app.field_visit_date.isoformat() if app.field_visit_date else None,
                    "is_overdue": app.is_overdue
                })
        
        return {
            "found": True,
            "count": len(applications),
            "applications": applications,
            "query_type": "Merge Application Details"
        }
        
    except Exception as e:
        logger.error(f"Error getting merge application detail: {e}", exc_info=True)
        return {"found": False, "count": 0, "applications": [], "error": str(e)}


async def get_all_surveys_in_jurisdiction(
    db: AsyncSession,
    officer: OfficerContext
) -> Dict[str, Any]:
    """
    Get all survey numbers within officer's jurisdiction with their subdivisions.
    Returns data formatted for HTML table display.
    
    Args:
        db: Database session
        officer: Officer context with jurisdiction info
        
    Returns:
        Dictionary with surveys and subdivisions in officer's jurisdiction
    """
    try:
        # Get jurisdiction filters
        jurisdiction_filters = await get_jurisdiction_filter(db, officer)
        
        if not jurisdiction_filters:
            return {
                "found": False,
                "count": 0,
                "surveys": [],
                "message": "No jurisdiction assigned"
            }
        
        # Query all surveys in jurisdiction with full geographic hierarchy
        query = select(
            SurveyNumber,
            Block,
            Ward,
            Town,
            Taluk,
            District
        ).join(
            Block, SurveyNumber.block_id == Block.id
        ).join(
            Ward, Block.ward_id == Ward.id
        ).join(
            Town, Ward.town_id == Town.id
        ).join(
            Taluk, Town.taluk_id == Taluk.id
        ).join(
            District, Taluk.district_id == District.id
        ).where(
            or_(*jurisdiction_filters)
        ).order_by(
            District.name,
            Taluk.name,
            Town.name,
            Ward.ward_number,
            Block.block_number,
            SurveyNumber.survey_no
        )
        
        result = await db.execute(query)
        rows = result.all()
        
        if not rows:
            return {
                "found": False,
                "count": 0,
                "surveys": [],
                "message": f"No surveys found in your jurisdiction ({officer.jurisdiction_name})"
            }
        
        # Get all survey IDs
        survey_ids = [row[0].id for row in rows]
        
        # Get all subdivisions for these surveys in one query
        subdiv_query = select(SubDivision).where(
            and_(
                SubDivision.survey_number_id.in_(survey_ids),
                SubDivision.status == "active"
            )
        ).order_by(SubDivision.sub_division_no)
        
        subdiv_result = await db.execute(subdiv_query)
        all_subdivisions = subdiv_result.scalars().all()
        
        # Create a mapping of survey_id to subdivisions
        subdiv_map = {}
        for sd in all_subdivisions:
            if sd.survey_number_id not in subdiv_map:
                subdiv_map[sd.survey_number_id] = []
            subdiv_map[sd.survey_number_id].append(sd.sub_division_no)
        
        # Build survey list with all details
        surveys = []
        for survey, block, ward, town, taluk, district in rows:
            subdivisions = subdiv_map.get(survey.id, [])
            
            surveys.append({
                "survey_no": survey.survey_no,
                "subdivisions": ", ".join(subdivisions) if subdivisions else "-",
                "subdivision_count": len(subdivisions),
                "district": district.name,
                "taluk": taluk.name,
                "town": town.name,
                "ward": ward.ward_name or f"Ward {ward.ward_number}",
                "block": block.block_name or f"Block {block.block_number}",
                "area_sqm": float(survey.total_area_sqm),
                "land_type": survey.land_type or "N/A",
                "patta_number": survey.patta_number or "N/A"
            })
        
        return {
            "found": True,
            "count": len(surveys),
            "surveys": surveys,
            "jurisdiction": {
                "type": officer.jurisdiction_type,
                "name": officer.jurisdiction_name
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting all surveys in jurisdiction: {e}")
        return {
            "found": False,
            "count": 0,
            "surveys": [],
            "error": str(e)
        }


async def check_existing_pending_application_for_survey(
    db: AsyncSession,
    survey_number_id: Any
) -> Dict[str, Any]:
    """
    Check if a pending/active application already exists for the given survey number.
    If one application of a survey number in a block is pending, another application cannot be applied.
    """
    try:
        query = select(Application).where(
            and_(
                Application.survey_number_id == survey_number_id,
                Application.current_status.in_(["pending", "in_progress"])
            )
        )
        res = await db.execute(query)
        existing_app = res.scalars().first()

        if existing_app:
            return {
                "can_apply": False,
                "existing_application_number": existing_app.application_number,
                "status": existing_app.current_status,
                "stage": existing_app.current_stage,
                "message": f"Application {existing_app.application_number} is already pending for this survey number. Another application cannot be submitted until the pending application is resolved."
            }
        return {"can_apply": True, "message": "No pending application for this survey number."}
    except Exception as e:
        logger.error(f"Error checking pending application for survey: {e}")
        return {"can_apply": True, "error": str(e)}
