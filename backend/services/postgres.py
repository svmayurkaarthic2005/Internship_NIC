"""
PostgreSQL query service for structured data retrieval
"""
import re

from sqlalchemy import select, func, and_, or_, desc, TIMESTAMP, false, case, text
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, List, Optional, Tuple, Union
from datetime import date, datetime, timedelta, timezone

# India has no DST, so a fixed UTC+5:30 offset is the whole rule, permanently --
# not a simplification. Every timestamp column is stored as UTC (CLAUDE.md's own
# convention); this is the only place with a time-of-day in its display, so it is
# the only place a UTC value read straight off the row silently showed the wrong
# wall-clock time to an officer (5h30m early) instead of failing loudly.
IST = timezone(timedelta(hours=5, minutes=30))

from backend.models import (
    Application, SISOfficer, SurveyNumber, SubDivision,
    Owner, SurveyOwnership, FieldVisit, WorkflowHistory,
    ApplicationSubDivision, Block, Ward, Town, Taluk, District,
    OfficerJurisdiction, PattaTransfer, Applicant, ServiceRegisterEntry,
    ServiceFee
)
from backend.schemas import OfficerContext
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# workflow_guide.txt: "Active statuses: pending, in_progress, escalated".
# These were listed as just pending+in_progress in the queries below, so an
# escalated application -- the ones that most need attention -- dropped out of
# the workload and visit counts while still appearing in the overdue list, and
# the two answers disagreed with each other.
ACTIVE_STATUSES = ["pending", "in_progress", "escalated"]

# District code → canonical English name (from helpers.TAMIL_NADU_DISTRICTS).
# Used as a guaranteed fallback when the ORM taluk.district relationship is None
# (can happen when the session evicts the object before the attribute is accessed).
from backend.utils.helpers import TAMIL_NADU_DISTRICTS as _TN_DISTRICTS

def _district_name_from_app_number(app_number: str) -> str:
    """Extract district name from application number.

    Handles both segment orderings found in the data:
      YYYY/SVCCODE/DISTCODE/SEQ  e.g. '2026/0154/28/001167'
      YYYY/DISTCODE/SVCCODE/SEQ  e.g. '2026/28/0154/001167'
    Returns empty string if the code is not in the master list.
    """
    try:
        parts = str(app_number or "").split("/")
        if len(parts) >= 4:
            # Determine which segment is the district code by checking which
            # of parts[2] / parts[1] is a known district code (numeric, 1-3 digits).
            for idx in (2, 1):
                raw = parts[idx].strip()
                if raw.isdigit():
                    code = raw.zfill(2)
                    name = _TN_DISTRICTS.get(code, {}).get("name", "")
                    if name:
                        return name
    except Exception as ex:
        logger.warning(f"district lookup failed for {app_number!r}: {ex}")
    return ""


def _resolve_jurisdiction(app, town, taluk, district, block, ward) -> dict:
    """Build jur_dict with a guaranteed district fallback from the application number.

    The ORM selectinload chain (Town→Taluk→District) occasionally delivers a
    None district when the session expires objects between the query and the
    attribute access.  The application number always carries the district code
    as its third segment, so we use that as a reliable fallback.
    """
    district_name = (district.name if district else None) or \
                    _district_name_from_app_number(app.application_number)
    taluk_name    = taluk.name if taluk else ""
    town_name     = town.name  if town  else ""
    ward_num      = ward.ward_number   if ward  else "N/A"
    block_num     = block.block_number if block else "N/A"
    return {
        "district": district_name or "N/A",
        "taluk":    taluk_name    or "N/A",
        "town":     town_name     or "N/A",
        "ward":     ward_num,
        "block":    block_num,
    }


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


async def get_all_service_code_applications(db: AsyncSession, officer: OfficerContext,
                                            service_code: Optional[str] = None,
                                            exclude_service_code: Optional[str] = None,
                                            submission_channel: Optional[Any] = None,
                                            status: Optional[Any] = None,
                                            start_date: Optional[date] = None,
                                            end_date: Optional[date] = None,
                                            date_ranges: Optional[List[Tuple[date, date]]] = None,
                                            exclude_date_range: bool = False,
                                            submission_year: Optional[int] = None,
                                            submission_month: Optional[int] = None,
                                            ward_number: Optional[str] = None,
                                            block_number: Optional[str] = None,
                                            sort_by: Optional[str] = None,
                                            sort_dir: Optional[str] = None,
                                            hide_rejected: bool = False) -> Dict[str, Any]:
    """Every application across every TAMILNILAM service code in the officer's
    wards -- not just the ISD/NISD ones `Application` tracks in full.

    "Show all applications" answered from `get_officer_applications()` alone
    means "all of my ISD/NISD patta transfers", because that is the only
    service this register runs a workflow for (`ck_application_type` admits
    nothing else). An officer asking across every service code (0158
    Modification, 0159 Addition, 0178 F-Line, ...) was getting that same
    narrower answer with no indication anything was left out. This reads the
    flat `service_register_entries` table instead: no workflow stage, no
    field-visit tracking, no applicant detail -- just application number,
    service code (with its official name), a coarse status, and the
    submission date. An ISD/NISD row appears here too, so this is a superset
    of the officer's own patta-transfer queue, not a separate list.

    Takes the same period / ward / block / channel / sort scopes
    `get_officer_applications()` does, so a question like "pending applications
    in June" or "approved applications in ward 102" can be answered over every
    service code rather than silently narrowing to ISD/NISD. All service codes
    are treated alike here: no code is singled out.
    """
    from backend.utils.helpers import SIS_URBAN_SERVICES
    try:
        if not officer or not officer.jurisdiction_ids:
            return {"count": 0, "applications": [], "query_type": "All Service Code Applications",
                    "error": "Invalid officer context"}

        _where = [ServiceRegisterEntry.ward_id.in_(officer.jurisdiction_ids)]
        if service_code:
            _where.append(ServiceRegisterEntry.service_code == service_code)
        elif exclude_service_code:
            # "applications that are not service code 0158" -- excludes the
            # one named code, every other code stays in. Mutually exclusive
            # with `service_code` by construction (the caller resolves a
            # negated code to ONE of the two, never both).
            _where.append(ServiceRegisterEntry.service_code != exclude_service_code)
        if submission_channel:
            _chans = (list(submission_channel) if isinstance(submission_channel, (list, tuple, set))
                      else [submission_channel])
            _where.append(ServiceRegisterEntry.submission_channel.in_(_chans))

        # Period scopes, same precedence as get_officer_applications():
        # explicit segments, then a start/end range, then year / month.
        _sd = ServiceRegisterEntry.submission_date
        if date_ranges:
            _in_any = or_(*[and_(_sd >= _s, _sd <= _e) for _s, _e in date_ranges])
            _where.append(~_in_any if exclude_date_range else _in_any)
        elif start_date and end_date:
            _where.append(or_(_sd < start_date, _sd > end_date) if exclude_date_range
                          else and_(_sd >= start_date, _sd <= end_date))
        elif start_date:
            _where.append(_sd >= start_date)
        elif end_date:
            _where.append(_sd <= end_date)
        else:
            if submission_year:
                _where.append(func.extract("year", _sd) == submission_year)
            if submission_month:
                _where.append(func.extract("month", _sd) == submission_month)
        _where_no_status = list(_where)
        if status:
            # This table's status is coarse (approved/pending/rejected only --
            # no in_progress/escalated split), unlike Application's state machine.
            _where.append(ServiceRegisterEntry.status.in_(status) if isinstance(status, list)
                           else ServiceRegisterEntry.status == status)
        elif hide_rejected:
            # The standing rule: a rejected file is not part of an operational
            # list unless it was asked for. The count is reported so the list
            # never reads as complete when it is not.
            _where.append(ServiceRegisterEntry.status != "rejected")
        result = await db.execute(
            select(ServiceRegisterEntry)
            .options(selectinload(ServiceRegisterEntry.ward)
                     .selectinload(Ward.town).selectinload(Town.taluk).selectinload(Taluk.district),
                     selectinload(ServiceRegisterEntry.block))
            .where(and_(*_where))
            .order_by(ServiceRegisterEntry.submission_date.asc(),
                      ServiceRegisterEntry.application_number.asc())
        )
        rows = result.scalars().all()

        # Ward / block narrowing, matched as loosely as the listing queries do
        # so "ward 21" finds "021" and "block 15" finds "0015".
        if ward_number:
            rows = [r for r in rows if r.ward and str(ward_number).lower() in str(r.ward.ward_number or "").lower()]
        if block_number:
            rows = [r for r in rows if r.block and str(block_number).lower() in str(r.block.block_number or "").lower()]

        # The rejected-exclusion count must be scoped the SAME way the listing itself was --
        # ward/block included. This used to be computed from `_where_no_status` alone, which
        # carries the jurisdiction/service/channel/date filters but NOT ward_number/block_number
        # (those are narrowed in Python above, after the SQL query runs), so a ward- or
        # block-scoped listing reported the officer's WHOLE jurisdiction's rejected count
        # instead of the rejected count within that ward/block -- e.g. "24 rejected in ward 001"
        # came back as the jurisdiction-wide "26". Re-running the same rejected-only query
        # through the identical loose ward/block filter keeps the two numbers consistent.
        rejected_hidden = 0
        if hide_rejected and not status:
            _rej_result = await db.execute(
                select(ServiceRegisterEntry)
                .options(selectinload(ServiceRegisterEntry.ward), selectinload(ServiceRegisterEntry.block))
                .where(and_(*_where_no_status, ServiceRegisterEntry.status == "rejected"))
            )
            _rej_rows = _rej_result.scalars().all()
            if ward_number:
                _rej_rows = [r for r in _rej_rows if r.ward and str(ward_number).lower() in str(r.ward.ward_number or "").lower()]
            if block_number:
                _rej_rows = [r for r in _rej_rows if r.block and str(block_number).lower() in str(r.block.block_number or "").lower()]
            rejected_hidden = len(_rej_rows)

        _sort_keys = {
            "application_number": lambda r: r.application_number or "",
            "survey_no": lambda r: (len(str(r.survey_no or "")), str(r.survey_no or "")),
            "status": lambda r: r.status or "",
            "ward_number": lambda r: str(r.ward.ward_number if r.ward else ""),
            "block_number": lambda r: str(r.block.block_number if r.block else ""),
        }
        if sort_by in _sort_keys:
            rows = sorted(rows, key=_sort_keys[sort_by], reverse=(sort_dir == "desc"))
        elif sort_dir == "desc" and sort_by in (None, "submission_date"):
            rows = list(reversed(rows))   # default order is oldest submission first

        apps = []
        by_service: Dict[str, int] = {}
        by_channel: Dict[str, int] = {}
        for r in rows:
            info = SIS_URBAN_SERVICES.get(r.service_code, {})
            town = r.ward.town if r.ward else None
            taluk = town.taluk if town else None
            district = taluk.district if taluk else None
            apps.append({
                "application_number": r.application_number,
                "application_type": {"0153": "NISD", "0154": "ISD"}.get(r.service_code),
                "service_code": r.service_code,
                "service_name": info.get("name", "Unknown service"),
                "survey_no": r.survey_no,
                "subdivision_no": r.subdivision_no,
                "status": r.status,
                "stage": r.stage,
                "submission_channel": r.submission_channel,
                "submission_date": r.submission_date.isoformat() if r.submission_date else None,
                "ward_number": r.ward.ward_number if r.ward else "N/A",
                "ward_name": (r.ward.ward_name if r.ward else None) or "N/A",
                "block_number": r.block.block_number if r.block else "N/A",
                "block_name": (r.block.block_name if r.block else None) or "N/A",
                # "along with taluk/town/district" -- these were missing
                # entirely (the eager-load above stopped at ward/block), so
                # every register-wide listing's taluk/town/district column
                # showed a blank placeholder regardless of the real value.
                "town_name": (town.name if town else None) or "N/A",
                "taluk_name": (taluk.name if taluk else None) or "N/A",
                "district_name": (district.name if district else None) or "N/A",
            })
            by_service[r.service_code] = by_service.get(r.service_code, 0) + 1
            if r.submission_channel:
                by_channel[r.submission_channel] = by_channel.get(r.submission_channel, 0) + 1

        _svc_name = SIS_URBAN_SERVICES.get(service_code, {}).get("name") if service_code else None
        _excl_name = SIS_URBAN_SERVICES.get(exclude_service_code, {}).get("name") if exclude_service_code else None
        _chan_label = {"CSC": "CSC", "citizen": "citizen portal", "sub_registrar": "Sub-Registrar"}
        _chan_list = (list(submission_channel) if isinstance(submission_channel, (list, tuple, set))
                      else ([submission_channel] if submission_channel else []))
        _chan_name = " / ".join(_chan_label.get(c, c) for c in _chan_list) or None
        _qtype = (f"{service_code} {_svc_name} Applications" if _svc_name
                  else f"Excluding {exclude_service_code} {_excl_name} Applications" if _excl_name
                  else f"{_chan_name} Applications" if _chan_name
                  else "All Service Code Applications")
        _empty_what = _svc_name or service_code or _chan_name
        return {
            "count": len(apps),
            "applications": apps,
            "by_service_code": by_service,
            "by_channel": by_channel,
            "query_type": _qtype,
            "service_code_filter": service_code,
            "service_code_excluded": exclude_service_code,
            "channel_filter": submission_channel,
            "status_filter": status,
            "ward_filter": ward_number,
            "block_filter": block_number,
            "rejected_hidden": rejected_hidden,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "empty_note": (None if apps else (
                f"No {_empty_what} applications found in your jurisdiction."
                if _empty_what else
                "No applications of any service code found in your jurisdiction.")),
        }
    except Exception as e:
        logger.error(f"Error in get_all_service_code_applications: {e}")
        return {"count": 0, "applications": [], "query_type": "All Service Code Applications",
                "error": str(e)}


def split_survey_reference(value: Optional[str]) -> Tuple[str, Optional[str]]:
    """Split a survey reference into its base survey number and sub-division tail.

    "1355/1B12" -> ("1355", "1B12");  "1355" -> ("1355", None).
    The tail is what the user asked about; every lookup resolves the parcel by
    the base and then narrows to the tail, rather than dropping the tail.
    """
    if not value:
        return "", None
    raw = str(value).strip()
    if '/' not in raw:
        return raw, None
    base, tail = raw.split('/', 1)
    base = base.strip()
    tail = tail.strip()
    return base, (tail or None)


def normalise_subdivision(value: Optional[str]) -> str:
    """Comparison form for a sub-division number: no spaces, upper case.

    The register carries "35/2 O", so a user typing "35/2O" must still match.
    """
    if not value:
        return ""
    return re.sub(r'\s+', '', str(value)).upper()


def subdivision_matches(sub_division_no: Optional[str], base: str, tail: Optional[str]) -> bool:
    """True when a stored sub_division_no ("1355/1B12") is the one asked for."""
    if not tail:
        return True
    stored = normalise_subdivision(sub_division_no)
    return stored in (normalise_subdivision(tail),
                      normalise_subdivision(f"{base}/{tail}"))


def application_subdivision_list(app) -> List[str]:
    """The sub-division numbers this application is actually about.

    An ISD file names its proposed splits in application_sub_divisions; every
    other file carries the sub-division being transferred on its patta_transfer
    row. Neither is "every sub-division of the parcel" -- falling back to those
    made NISD 2022/0153/28/000984 list all 93 sub-divisions of survey 1355 when
    the application concerns 1355/2B alone.

    Returns the tail of each number ("2B") when it names no survey of its own,
    so the caller re-joins it to the application's base survey number for
    display -- and the WHOLE "survey/tail" string when it already carries one,
    because 14 of 209 applications span more than one survey number (an
    application spans several parcels -- CLAUDE.md), and a sub-division there
    can belong to a different survey than the application's own. Stripping it
    down to a bare tail and re-prefixing with the wrong survey invented a
    sub-division number ("1355/0") that exists nowhere in the register, for
    the second parcel of 2026/0154/28/001167 -- 1363/0.
    """
    subdiv_list: List[str] = []

    def _add(value, keep_survey=False):
        if not value:
            return
        tail = str(value).strip()
        if not keep_survey and '/' in tail:
            tail = tail.split('/')[-1]
        if tail and tail not in subdiv_list:
            subdiv_list.append(tail)

    for assoc in (getattr(app, "application_sub_divisions", None) or []):
        # proposed/temporary_sub_division_no are bare (or "{tail}/T{seq}",
        # never a survey number) -- but the sub_divisions fallback is the same
        # fully-qualified "survey/tail" string patta_transfers uses below.
        if assoc.proposed_sub_division_no:
            _add(assoc.proposed_sub_division_no)
        elif assoc.sub_division:
            _add(assoc.sub_division.sub_division_no, keep_survey=True)
    if not subdiv_list:
        for transfer in (getattr(app, "patta_transfers", None) or []):
            _add(transfer.sub_division.sub_division_no if transfer.sub_division else None,
                 keep_survey=True)
    return subdiv_list


def format_survey_with_subdivisions(survey_no: Optional[str], subdiv_list: List[str]) -> str:
    """Format survey number alongside its sub-division numbers (e.g. 155/1A, 155/1B or 154/1)."""
    if not subdiv_list:
        return "-"
    base_survey = str(survey_no).strip() if survey_no and str(survey_no).upper() not in ("N/A", "NONE", "") else ""
    formatted = []
    for s in subdiv_list:
        if not s or str(s).strip() in ("None", "N/A", "-", ""):
            continue
        s_str = str(s).strip()
        if '/' in s_str:
            formatted.append(s_str)
        elif base_survey:
            formatted.append(f"{base_survey}/{s_str}")
        else:
            formatted.append(s_str)
    if formatted:
        return ", ".join(formatted)
    return "-"


async def get_officer_applications(
    db: AsyncSession,
    officer: OfficerContext,
    status: Optional[Any] = None,
    application_type: Optional[Any] = None,
    submission_year: Optional[int] = None,
    submission_month: Optional[int] = None,
    taluk_name: Optional[str] = None,
    ward_number: Optional[str] = None,
    block_number: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    is_overdue: Optional[bool] = None,
    stage: Optional[str] = None,
    exclude_date_range: bool = False,
    submission_channel: Optional[Any] = None,   # 'CSC' | 'citizen' | 'sub_registrar', or a list of them
    date_ranges: Optional[List[Tuple[date, date]]] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get applications assigned to officer with optional filters.

    `date_ranges` holds disjoint (start, end) segments for months asked for as a
    union -- "March and May" must not drag April in with them. It takes
    precedence over start_date/end_date, which the caller still sets to the
    envelope of the segments for labelling.
    Filters by the officer's jurisdiction (ward/block/taluk/town/district) AND,
    by default, current_stage so only applications in the officer's stage are
    returned.
    """
    if not officer or not officer.officer_id:
        logger.error("Invalid officer context provided to get_officer_applications")
        return {"count": 0, "applications": [], "error": "Invalid officer context"}
    
    try:
        from sqlalchemy.orm import selectinload

        # Get the officer's stage (SIS/DIS/SD/Tahsildar)
        officer_stage = stage or officer.officer_stage or "SIS"
        
        # Scoped by jurisdiction (ward/block/taluk/town/district), the same way
        # every other listing in this register is scoped (service_register_entries,
        # the overdue query, the workload query) -- not by `assigned_officer_id`.
        # The live data (built by sample_db_new/build_app_tables_new.py) assigns
        # every one of the 57 seeded applications to a single officer regardless
        # of ward, so filtering on assigned_officer_id alone answered "No
        # applications found" to every other officer in the same jurisdiction
        # and silently returned every application, unscoped, to the one it
        # happened to name -- the ISD/NISD listing was broken for 2 of the 3
        # seeded officers. District officers already used this jurisdiction
        # subquery; it is now the only path.
        where_clauses = []
        jurisdiction_filters = await get_jurisdiction_filter(db, officer)
        if jurisdiction_filters:
            where_clauses.append(Application.id.in_(_application_jurisdiction_subquery(jurisdiction_filters)))

        is_historical = (
            (isinstance(status, list) and any(s in ["approved", "rejected", "closed"] for s in status))
            or (isinstance(status, str) and status in ["approved", "rejected", "closed"])
            # "show in progress applications" names a status that has usually left the SIS desk
            # (the file sits with the Tahsildar): a register question, unlike the default queue
            or (bool(status) and set([status] if isinstance(status, str) else status) != set(ACTIVE_STATUSES)
                and any(s in ("in_progress", "escalated") for s in ([status] if isinstance(status, str) else status)))
        )
        # A question that names a period -- "applications in June 2025",
        # "between X and Y", "last month" -- asks what the register holds for
        # that period, not what is on the officer's desk right now. Pinning it
        # to the current stage answered 0 for every past month, because an
        # application from then has long since left the SIS desk.
        has_date_filter = any(v is not None for v in
                              (start_date, end_date, submission_year, submission_month))
        # A channel question -- "show applications from CSC" -- is the same kind
        # of question as a period one: it asks what the register holds for that
        # channel, not what is sitting on the officer's desk today. Pinned to
        # the current stage it answered "No applications found" to an officer
        # holding 30 CSC files, because all but one had already left the desk.
        #
        # An application-type scope -- "show ISD applications" -- is equally a
        # register question: the officer wants their full ISD history, not only
        # the ISD files still sitting on their desk right now. Without this the
        # list was stage-pinned and returned fewer applications than expected.
        has_scope_filter = has_date_filter or bool(submission_channel) or bool(application_type)
        if not is_historical and not has_scope_filter:
            where_clauses.append(Application.current_stage == officer_stage)
        elif has_scope_filter and not is_historical and not status and not application_type:
            # Looking across every stage, so the standing rule applies: rejected
            # applications stay out of lists unless they were asked for.
            # An explicit type ("show ISD applications") is the exception: the
            # officer wants the type's whole record, so its count must agree
            # with "total applications".
            where_clauses.append(Application.current_status != "rejected")

        query = select(Application).options(
            selectinload(Application.survey_number).selectinload(SurveyNumber.block).selectinload(Block.ward).selectinload(Ward.town).selectinload(Town.taluk).selectinload(Taluk.district),
            selectinload(Application.survey_number).selectinload(SurveyNumber.sub_divisions),
            selectinload(Application.application_sub_divisions).selectinload(ApplicationSubDivision.sub_division),
            selectinload(Application.patta_transfers).selectinload(PattaTransfer.sub_division),
            selectinload(Application.applicant),
            # for "when were they last updated" over a list -- same rule as the
            # single-application card: the most recent workflow hop, not the
            # build-time updated_at column (see get_application_detail).
            selectinload(Application.workflow_history),
        ).where(and_(True, *where_clauses))

        if is_overdue is not None:
            query = query.where(Application.is_overdue == is_overdue)

        if status:
            if isinstance(status, list):
                query = query.where(Application.current_status.in_(status))
            else:
                query = query.where(Application.current_status == status)
        
        if application_type:
            if isinstance(application_type, list):
                query = query.where(Application.application_type.in_(application_type))
            else:
                query = query.where(Application.application_type == application_type)

        # Filter by submission channel (CSC / citizen / sub_registrar).
        # A list is a UNION, the same shape application_type takes: "CSC and
        # Sub-Registrar applications" asks for both sets in one list, and "all
        # channels" is that union over every channel the register holds.
        if submission_channel:
            _channels = (list(submission_channel)
                         if isinstance(submission_channel, (list, tuple, set))
                         else [submission_channel])
            query = query.where(Application.submission_channel.in_(_channels))
        
        # Filter by date range if provided (takes precedence over year/month)
        if date_ranges:
            _segments = [
                and_(Application.submission_date >= _s, Application.submission_date <= _e)
                for _s, _e in date_ranges
            ]
            _in_any_segment = or_(*_segments)
            query = query.where(~_in_any_segment if exclude_date_range else _in_any_segment)
        elif start_date and end_date:
            if exclude_date_range:
                # NOT BETWEEN: applications OUTSIDE the given range
                query = query.where(
                    or_(
                        Application.submission_date < start_date,
                        Application.submission_date > end_date
                    )
                )
            else:
                query = query.where(
                    and_(
                        Application.submission_date >= start_date,
                        Application.submission_date <= end_date
                    )
                )
        elif start_date:
            query = query.where(Application.submission_date >= start_date)
        elif end_date:
            query = query.where(Application.submission_date <= end_date)
        else:
            # Filter by submission year if provided (only when no date range)
            if submission_year:
                from sqlalchemy import extract
                query = query.where(extract('year', Application.submission_date) == submission_year)
            
            # Filter by submission month if provided
            if submission_month:
                from sqlalchemy import extract
                query = query.where(extract('month', Application.submission_date) == submission_month)

        # Ordering happens in SQL, so "ascending"/"descending" reorders the whole
        # result set rather than the rows that happened to come back first. The
        # default -- oldest submission first, the order the queue is worked in --
        # is stated explicitly because a query with no ORDER BY returns rows in
        # whatever order the plan produces, and that changes as the table does.
        _descending = (sort_dir or "asc").lower() == "desc"
        if (sort_by or "") == "priority":
            # Priority is not one column: an application is urgent because it
            # carries the priority flag, or because it is already overdue. Both
            # are ordered together, and files of equal priority keep queue order
            # (oldest submission first) so the list stays workable.
            _priority_cols = [Application.priority_flag, Application.is_overdue]
            _order = [c.desc() if _descending else c.asc() for c in _priority_cols]
            _order += [Application.submission_date.asc(),
                       Application.application_number.asc()]
            query = query.order_by(*_order)
        else:
            # Ward / block / survey / applicant live on joined tables; correlated
            # scalar subqueries order by them without disturbing the filter above.
            _survey_of = SurveyNumber.id == Application.survey_number_id
            _sort_columns = {
                "submission_date": Application.submission_date,
                "application_number": Application.application_number,
                "status": Application.current_status,
                "application_type": Application.application_type,
                "fee_amount": Application.fee_amount,
                "ward_number": select(Ward.ward_number)
                    .join(Block, Block.ward_id == Ward.id)
                    .join(SurveyNumber, SurveyNumber.block_id == Block.id)
                    .where(_survey_of).scalar_subquery(),
                "block_number": select(Block.block_number)
                    .join(SurveyNumber, SurveyNumber.block_id == Block.id)
                    .where(_survey_of).scalar_subquery(),
                "survey_no": select(SurveyNumber.survey_no).where(_survey_of).scalar_subquery(),
                "applicant_name": select(func.lower(Applicant.name))
                    .where(Applicant.id == Application.applicant_id).scalar_subquery(),
            }
            _sort_col = _sort_columns.get(sort_by or "submission_date",
                                          Application.submission_date)
            if sort_by == "survey_no":
                # 9 before 10: shorter numbers first, then lexical ("35" < "35A").
                _sort_col = func.lpad(_sort_col, 8, "0")
            if _descending:
                query = query.order_by(_sort_col.desc(),
                                       Application.application_number.desc())
            else:
                query = query.order_by(_sort_col.asc(),
                                       Application.application_number.asc())

        result = await db.execute(query)
        applications = result.scalars().all()
        
        app_rows = []
        for app in applications:
            sn = app.survey_number
            block = sn.block if sn else None
            ward = block.ward if block else None
            town = ward.town if ward else None
            applicant = app.applicant if app else None
            # "when were they last updated" over this list -- same rule as the
            # single-application card (get_application_detail): the most recent
            # workflow hop, converted from the stored UTC to IST for display.
            _hops = [w.performed_at for w in (app.workflow_history or []) if w.performed_at]
            _lu = (max(_hops) if _hops else app.updated_at)
            last_updated_date = _lu.astimezone(IST).strftime("%Y-%m-%d %H:%M") if _lu else None

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
            jur_dict = _resolve_jurisdiction(app, town, taluk, district, block, ward)
            
            # Extract all sub-division numbers for this application
            # The sub-divisions THIS application is about -- never the parcel's
            # whole set (see application_subdivision_list).
            subdiv_list = application_subdivision_list(app)

            base_survey_no = sn.survey_no if sn else "N/A"
            subdivisions_str = format_survey_with_subdivisions(base_survey_no, subdiv_list)

            app_rows.append({
                "application_number": app.application_number,
                "type": app.application_type,
                "status": app.current_status,
                "stage": app.current_stage,
                "submission_date": app.submission_date.isoformat() if app.submission_date else None,
                "last_updated_date": last_updated_date,
                "is_overdue": app.is_overdue,
                "submission_channel": app.submission_channel,
                # Carried so a listing that shows a CAN or IGRS column has
                # something to put in it. Without these the renderer read
                # a key that was never set and printed "N/A" for every
                # row -- a false statement about data that is on record,
                # which is worse than leaving the column out.
                "can_number": app.can_number,
                "igrs_form6_number": app.igrs_form6_number,
                "survey_no": base_survey_no,
                "raw_survey_no": base_survey_no,
                "subdivisions": subdivisions_str,
                "sub_division_no": subdivisions_str,
                "district_name": jur_dict["district"],
                "taluk_name":    jur_dict["taluk"],
                "town_name":     jur_dict["town"],
                "ward_number":   ward.ward_number if ward else "N/A",
                "block_number":  block.block_number if block else "N/A",
                "jurisdiction": jur_dict,
                "applicant_name": applicant.name if applicant else "N/A",
                "applicant_mobile": applicant.mobile if applicant else "N/A",
                "applicant_address": applicant.address if applicant else "N/A",
                "included_subdivisions": ", ".join(subdiv_list) or "None"
            })
        
        # Optional geography post-filtering
        if taluk_name:
            app_rows = [r for r in app_rows if taluk_name.lower() in r.get("taluk_name", "").lower()]
        if ward_number:
            app_rows = [r for r in app_rows if str(ward_number).lower() in r.get("ward_number", "").lower()]
        if block_number:
            app_rows = [r for r in app_rows if str(block_number).lower() in r.get("block_number", "").lower()]

        # An empty list is ambiguous when rejected files were filtered out of
        # it: "no citizen applications" reads as "none came in that way", when
        # in fact the officer's only citizen file was rejected. Say which it is.
        empty_note = None
        if not app_rows and not is_historical and not status:
            rejected_query = select(func.count()).select_from(Application).where(
                and_(True, *[c for c in where_clauses
                             if c is not None and "current_status" not in str(c)
                             and "current_stage" not in str(c)]),
                Application.current_status == "rejected")
            # The same period the empty list was scoped to: without it the note
            # counted rejected files from every year ("14 matching ... rejected"
            # under a question about 1900).
            if date_ranges:
                _segs = or_(*[and_(Application.submission_date >= _s, Application.submission_date <= _e)
                              for _s, _e in date_ranges])
                rejected_query = rejected_query.where(~_segs if exclude_date_range else _segs)
            elif start_date and end_date:
                _in_range = and_(Application.submission_date >= start_date,
                                 Application.submission_date <= end_date)
                rejected_query = rejected_query.where(~_in_range if exclude_date_range else _in_range)
            elif start_date:
                rejected_query = rejected_query.where(Application.submission_date >= start_date)
            elif end_date:
                rejected_query = rejected_query.where(Application.submission_date <= end_date)
            else:
                from sqlalchemy import extract as _extract
                if submission_year:
                    rejected_query = rejected_query.where(
                        _extract('year', Application.submission_date) == submission_year)
                if submission_month:
                    rejected_query = rejected_query.where(
                        _extract('month', Application.submission_date) == submission_month)
            if submission_channel:
                rejected_query = rejected_query.where(
                    Application.submission_channel.in_(
                        list(submission_channel)
                        if isinstance(submission_channel, (list, tuple, set))
                        else [submission_channel]))
            if application_type:
                rejected_query = rejected_query.where(
                    Application.application_type.in_(
                        application_type if isinstance(application_type, list)
                        else [application_type]))
            n_rejected = await db.scalar(rejected_query) or 0
            if n_rejected:
                empty_note = (
                    f"{n_rejected} matching application"
                    f"{'s were' if n_rejected != 1 else ' was'} rejected, and "
                    f"rejected files are left out of lists unless you ask for "
                    f"them. Ask for rejected applications to see "
                    f"{'them' if n_rejected != 1 else 'it'}.")
            elif submission_channel:
                # Nothing came in through that channel, and nothing was hidden
                # by the rejected rule either. A bare "No applications found"
                # leaves the officer unable to tell an empty channel from a
                # broken filter, so the register answers the question it
                # actually raises: then where DID my applications come from?
                _asked = (list(submission_channel)
                          if isinstance(submission_channel, (list, tuple, set))
                          else [submission_channel])
                _mix_rows = (await db.execute(
                    select(Application.submission_channel, func.count())
                    .where(and_(True, *[c for c in where_clauses
                                        if c is not None and "current_status" not in str(c)
                                        and "current_stage" not in str(c)]),
                           Application.current_status != "rejected")
                    .group_by(Application.submission_channel))).all()
                _labels = {"CSC": "a CSC counter",
                           "citizen": "the citizen portal",
                           "sub_registrar": "the Sub-Registrar"}
                _held = [(c, n) for c, n in _mix_rows if c and c not in _asked and n]
                _asked_names = " or ".join(_labels.get(c, str(c)) for c in _asked)
                empty_note = f"No application in your jurisdiction came in through {_asked_names}."
                if _held:
                    _held.sort(key=lambda r: (-r[1], str(r[0])))
                    empty_note += (" Your applications came from "
                                   + ", ".join(f"{n} through {_labels.get(c, str(c))}"
                                               for c, n in _held) + ".")

        return {
            "count": len(app_rows),
            "applications": app_rows,
            "jurisdiction_type": officer.jurisdiction_type,
            # Why the list is empty, when it is empty for a reason worth saying.
            "empty_note": empty_note,
            # Echoed back so the answer can name the order it applied.
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        }
    except Exception as e:
        logger.error(f"Error getting officer applications: {e}")
        return {"count": 0, "applications": [], "error": str(e)}


async def get_comparison(db: AsyncSession, officer: OfficerContext,
                         spec: Dict[str, Any]) -> Dict[str, Any]:
    """Both sides of a comparison question, counted straight from the register.

    `spec` comes from rag.parse_comparison_query(). Counts never pass through
    the LLM: a comparison whose numbers are invented is worse than no answer.
    Rejected applications are included here -- "pending versus approved" is a
    question about the whole register, not about the working queue.
    """
    if not officer or not officer.officer_id:
        return {"found": False, "error": "Invalid officer context"}

    kind = spec.get("kind")
    # Jurisdiction-scoped, like every other query here -- see the note on
    # `get_officer_applications` for why `assigned_officer_id` cannot be used.
    _jf = await get_jurisdiction_filter(db, officer)
    base = [Application.id.in_(_application_jurisdiction_subquery(_jf))] if _jf else [false()]
    scope = spec.get("scope") or {}       # "ISD vs NISD but not rejected" / "... approved only"
    if scope.get("status_in"):
        base.append(Application.current_status.in_(scope["status_in"]))
    if scope.get("status_out"):
        base.append(Application.current_status.notin_(scope["status_out"]))
    if scope.get("channel_out"):
        base.append(Application.submission_channel.notin_(scope["channel_out"]))
    if scope.get("type_out"):
        base.append(Application.application_type.notin_(scope["type_out"]))

    # ---- counts over EVERY service code ------------------------------------
    # `applications` holds only ISD/NISD, so a count taken from it answers
    # "pending versus approved" with 17 and 19 while the register holds 47 and
    # 78. Counting questions (status, channel, ward, period, month, "which type
    # has more") are answered from the register across every service code, over
    # the officer's whole jurisdiction like the listings, not just the files
    # assigned to them. Questions that need a decision date (how long, which
    # took longest) or name ISD and NISD explicitly keep the ISD/NISD path
    # below -- the register carries neither.
    _named = [n for n in (spec.get("sides") or [spec.get("left"), spec.get("right")]) if n]
    _coarse = {"pending", "approved", "rejected"}
    _scope_statuses = set(scope.get("status_in") or []) | set(scope.get("status_out") or [])
    _register_ok = (
        spec.get("aspect") != "duration"
        and not (_scope_statuses - _coarse)
        and (kind in ("channel", "ward", "period", "month")
             or (kind == "status" and all(n in _coarse for n in _named))
             or (kind == "type" and not (spec.get("left") and spec.get("right")))))
    if _register_ok:
        from backend.utils.helpers import SIS_URBAN_SERVICES
        SRE = ServiceRegisterEntry
        rbase = [SRE.ward_id.in_(officer.jurisdiction_ids or [])]
        if scope.get("status_in"):
            rbase.append(SRE.status.in_(scope["status_in"]))
        if scope.get("status_out"):
            rbase.append(SRE.status.notin_(scope["status_out"]))
        if scope.get("channel_out"):
            rbase.append(SRE.submission_channel.notin_(scope["channel_out"]))
        if scope.get("type_out"):
            _codes_out = [c for t, c in (("ISD", "0154"), ("NISD", "0153")) if t in scope["type_out"]]
            if _codes_out:
                rbase.append(SRE.service_code.notin_(_codes_out))

        if kind in ("status", "channel", "type"):
            col = {"status": SRE.status, "channel": SRE.submission_channel,
                   "type": SRE.service_code}[kind]
            if _named and kind != "type":
                sides = []
                for name in _named:
                    n = await db.scalar(select(func.count()).select_from(SRE)
                                        .where(and_(*rbase, col == name))) or 0
                    sides.append({"label": name, "count": n})
            else:
                grouped = (await db.execute(
                    select(col, func.count()).where(and_(*rbase)).group_by(col)
                    .order_by(func.count().desc(), col))).all()
                sides = [{"label": ((f"{r[0]} {(SIS_URBAN_SERVICES.get(r[0], {}) or {}).get('name', '')}".strip())
                                    if kind == "type" else r[0]), "count": r[1]}
                         for r in grouped if r[0]]
            return {"found": True, "kind": kind, "aspect": "count", "sides": sides,
                    "total": sum(x["count"] for x in sides), "all_service_codes": True}

        if kind == "ward":
            counts = {r[0]: r[1] for r in (await db.execute(
                select(Ward.ward_number, func.count()).select_from(SRE)
                .join(Ward, SRE.ward_id == Ward.id)
                .where(and_(*rbase)).group_by(Ward.ward_number))).all()}
            wn = [n for n in (spec.get("sides") or [spec.get("left"), spec.get("right")]) if n]
            if wn:
                sides = [{"label": f"Ward {n}", "count": counts.get(n, 0)} for n in wn]
            else:
                sides = [{"label": f"Ward {w}", "count": c}
                         for w, c in sorted(counts.items(), key=lambda kv: -kv[1])]
            return {"found": True, "kind": kind, "aspect": "count", "sides": sides,
                    "total": sum(x["count"] for x in sides), "all_service_codes": True}

        if kind == "period":
            sides = []
            for period in spec.get("periods", []):
                start = date.fromisoformat(period["start"])
                end = date.fromisoformat(period["end"])
                n = await db.scalar(select(func.count()).select_from(SRE).where(
                    and_(*rbase, SRE.submission_date >= start, SRE.submission_date <= end))) or 0
                sides.append({"label": period["label"], "count": n,
                              "start": period["start"], "end": period["end"]})
            return {"found": True, "kind": kind, "aspect": "count", "sides": sides,
                    "total": sum(x["count"] for x in sides), "all_service_codes": True}

        if kind == "month":
            _y = func.extract("year", SRE.submission_date)
            _m = func.extract("month", SRE.submission_date)
            _MN = ["", "January", "February", "March", "April", "May", "June", "July",
                   "August", "September", "October", "November", "December"]
            rows = [(f"{_MN[int(r[1])]} {int(r[0])}", r[2]) for r in (await db.execute(
                select(_y, _m, func.count()).where(and_(*rbase, SRE.submission_date.isnot(None)))
                .group_by(_y, _m))).all()]
            if not rows:
                return {"found": False, "kind": kind,
                        "message": "No application in your jurisdiction carries a filing date."}
            ordered = sorted(rows, key=lambda r: (-r[1], r[0])) \
                if spec.get("extreme") != "min" else sorted(rows, key=lambda r: (r[1], r[0]))
            top = ordered[0]
            return {"found": True, "kind": kind, "aspect": "count",
                    "extreme": spec.get("extreme", "max"), "label": top[0], "count": top[1],
                    "months": len(rows), "all_service_codes": True,
                    "sides": [{"label": r[0], "count": r[1]} for r in ordered[:3]]}

    async def _count(**filters) -> int:
        q = select(func.count()).select_from(Application).where(and_(*base))
        for column, value in filters.items():
            q = q.where(getattr(Application, column) == value)
        return await db.scalar(q) or 0

    async def _decided_rows(extra=None):
        """(days-to-decide, Application) for every file that has been decided."""
        decided = (
            select(WorkflowHistory.application_id.label("app_id"),
                   func.max(WorkflowHistory.performed_at).label("decided_at"))
            .where(WorkflowHistory.to_stage.in_(("COMPLETED", "REJECTED")))
            .group_by(WorkflowHistory.application_id).subquery()
        )
        q = (select(Application, decided.c.decided_at)
             .join(decided, decided.c.app_id == Application.id)
             .where(and_(*base)))
        if extra is not None:
            q = q.where(extra)
        out = []
        for app, decided_at in (await db.execute(q)).all():
            if app.submission_date and decided_at:
                out.append(((decided_at.date() - app.submission_date).days, app))
        return out

    def _mean(values):
        return round(sum(values) / len(values), 1) if values else None

    if kind in ("type", "status", "channel"):
        column = {"type": "application_type", "status": "current_status",
                  "channel": "submission_channel"}[kind]
        left, right = spec.get("left"), spec.get("right")

        if spec.get("aspect") == "duration":
            # "Do ISD take longer than NISD" is a question about time, not
            # volume: answer it with the mean days from filing to decision on
            # each side, and say how many files each mean rests on.
            rows = await _decided_rows()
            buckets: Dict[str, List[int]] = {}
            for days, app in rows:
                buckets.setdefault(getattr(app, column) or "", []).append(days)
            named = spec.get("sides") or [n for n in (left, right) if n]
            sides = [{"label": name, "days": _mean(buckets.get(name, [])),
                      "population": len(buckets.get(name, []))}
                     for name in named]
            return {"found": True, "kind": kind, "aspect": "duration",
                    "sides": sides}
        if not left or not right:
            # "which channel has more applications" -- the sides are implied,
            # so report every value the register holds.
            rows = (await db.execute(
                select(getattr(Application, column), func.count())
                .where(and_(*base)).group_by(getattr(Application, column))
                .order_by(func.count().desc())
            )).all()
            sides = [{"label": r[0], "count": r[1]} for r in rows if r[0]]
        else:
            named = spec.get("sides") or [left, right]
            sides = [{"label": name, "count": await _count(**{column: name})}
                     for name in named]
        return {"found": True, "kind": kind, "aspect": "count", "sides": sides,
                "total": sum(s["count"] for s in sides)}

    if kind == "ward":
        # The ward lives two joins away, so this one cannot use _count().
        q = (select(Ward.ward_number, func.count())
             .select_from(Application)
             .join(SurveyNumber, Application.survey_number_id == SurveyNumber.id)
             .join(Block, SurveyNumber.block_id == Block.id)
             .join(Ward, Block.ward_id == Ward.id)
             .where(and_(*base)).group_by(Ward.ward_number))
        counts = {r[0]: r[1] for r in (await db.execute(q)).all()}
        named = spec.get("sides") or [spec.get("left"), spec.get("right")]
        named = [n for n in named if n]
        if named:
            sides = [{"label": f"Ward {n}", "count": counts.get(n, 0)} for n in named]
        else:
            sides = [{"label": f"Ward {w}", "count": c}
                     for w, c in sorted(counts.items(), key=lambda kv: -kv[1])]
        return {"found": True, "kind": kind, "aspect": "count", "sides": sides,
                "total": sum(s["count"] for s in sides)}

    if kind == "month":
        # Grouped on extracted year/month rather than to_char: the format
        # string binds as a parameter, and Postgres will not match a
        # parameterised expression in SELECT to the one in GROUP BY.
        _y = func.extract("year", Application.submission_date)
        _m = func.extract("month", Application.submission_date)
        _MONTHS = ["", "January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November", "December"]
        rows = [(f"{_MONTHS[int(r[1])]} {int(r[0])}", r[2]) for r in (await db.execute(
            select(_y, _m, func.count())
            .where(and_(*base), Application.submission_date.isnot(None))
            .group_by(_y, _m))).all()]
        if not rows:
            return {"found": False, "kind": kind,
                    "message": "No application on your desk carries a filing date."}
        # Ties break on the month label so the same question names the same
        # month every time it is asked.
        ordered = sorted(rows, key=lambda r: (-r[1], r[0])) \
            if spec.get("extreme") != "min" else sorted(rows, key=lambda r: (r[1], r[0]))
        top = ordered[0]
        return {"found": True, "kind": kind, "aspect": "count",
                "extreme": spec.get("extreme", "max"),
                "label": top[0], "count": top[1],
                "months": len(rows),
                "sides": [{"label": r[0], "count": r[1]} for r in ordered[:3]]}

    if kind == "average_duration":
        extra = (Application.current_status == spec["status"]) if spec.get("status") else None
        rows = await _decided_rows(extra)
        if not rows:
            return {"found": False, "kind": kind,
                    "message": "No decided application has both a filing and a decision date."}
        days = sorted(d for d, _a in rows)
        return {"found": True, "kind": kind, "aspect": "duration",
                "status": spec.get("status"), "population": len(days),
                "average_days": _mean(days), "median_days": days[len(days) // 2],
                "fastest_days": days[0], "slowest_days": days[-1]}

    if kind == "period":
        # Counted on submission_date: "this month versus last month" asks how
        # much came IN, which is the question an officer planning a week has.
        sides = []
        for period in spec.get("periods", []):
            start = date.fromisoformat(period["start"])
            end = date.fromisoformat(period["end"])
            count = await db.scalar(
                select(func.count()).select_from(Application).where(
                    and_(*base), Application.submission_date >= start,
                    Application.submission_date <= end)) or 0
            sides.append({"label": period["label"], "count": count,
                          "start": period["start"], "end": period["end"]})
        return {"found": True, "kind": kind, "aspect": "count", "sides": sides,
                "total": sum(s["count"] for s in sides)}

    if kind == "superlative":
        # Time from filing to the hop that closed the file.
        decided = (
            select(WorkflowHistory.application_id.label("app_id"),
                   func.max(WorkflowHistory.performed_at).label("decided_at"))
            .where(WorkflowHistory.to_stage.in_(("COMPLETED", "REJECTED")))
            .group_by(WorkflowHistory.application_id).subquery()
        )
        q = (select(Application, decided.c.decided_at)
             .join(decided, decided.c.app_id == Application.id)
             .where(and_(*base)))
        if spec.get("status"):
            q = q.where(Application.current_status == spec["status"])
        rows = (await db.execute(q)).all()
        ranked = []
        for app, decided_at in rows:
            if not app.submission_date or not decided_at:
                continue
            ranked.append(((decided_at.date() - app.submission_date).days, app,
                           decided_at.date()))
        if not ranked:
            _which = f"{spec['status']} " if spec.get("status") else "completed "
            return {"found": False, "kind": kind,
                    "message": (f"No {_which}application on your desk has both a "
                                f"filing and a decision date to measure.")}
        # Two files often take the same number of days. Sorting on the days
        # alone left the winner to whatever order the rows came back in, so the
        # same question could name a different application each time it was
        # asked; the application number breaks the tie the same way every run.
        want_max = spec.get("extreme") != "min"
        ranked.sort(key=lambda r: (-r[0] if want_max else r[0], r[1].application_number))
        days, app, decided_on = ranked[0]
        tied = [r[1].application_number for r in ranked
                if r[0] == days and r[1].application_number != app.application_number]
        return {
            "tied_with": tied,
            "found": True, "kind": kind, "aspect": "duration",
            "extreme": spec.get("extreme", "max"),
            "application_number": app.application_number,
            "application_type": app.application_type,
            "status": app.current_status,
            "submission_date": app.submission_date.isoformat(),
            "decision_date": decided_on.isoformat(),
            "days": days,
            "population": len(ranked),
            "population_status": spec.get("status"),
            "median_days": sorted(r[0] for r in ranked)[len(ranked) // 2],
        }

    return {"found": False, "error": f"Unsupported comparison: {kind}"}


async def count_rejected_by_channel(db: AsyncSession, officer: OfficerContext,
                                    channels) -> Dict[str, int]:
    """How many REJECTED applications the officer holds in each named channel.

    A channel the officer asked for that returns no rows is ambiguous: "none
    from the citizen portal" reads as "none ever came in that way", when the
    only citizen file may simply have been rejected -- and rejected files stay
    out of lists unless they are asked for. This is the same distinction
    `empty_note` draws for a wholly empty list, drawn per channel so it
    survives inside a combined one.
    """
    chans = [c for c in (list(channels) if isinstance(channels, (list, tuple, set))
                         else [channels]) if c]
    if not chans or not officer or not officer.officer_id:
        return {}
    try:
        _jf = await get_jurisdiction_filter(db, officer)
        if not _jf:
            return {}
        rows = (await db.execute(
            select(Application.submission_channel, func.count())
            .where(Application.id.in_(_application_jurisdiction_subquery(_jf)),
                   Application.current_status == "rejected",
                   Application.submission_channel.in_(chans))
            .group_by(Application.submission_channel)
        )).all()
        return {channel: count for channel, count in rows}
    except Exception as e:
        logger.error(f"Error counting rejected applications by channel: {e}")
        return {}


async def get_pending_applications(
    db: AsyncSession,
    officer: OfficerContext,
    application_type: Optional[Any] = None,
    status: Optional[Any] = None,
    submission_year: Optional[int] = None,
    submission_month: Optional[int] = None,
    taluk_name: Optional[str] = None,
    ward_number: Optional[str] = None,
    block_number: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    is_overdue: Optional[bool] = None,
    stage: Optional[str] = None,
    exclude_date_range: bool = False,
    submission_channel: Optional[Any] = None,   # 'CSC' | 'citizen' | 'sub_registrar', or a list of them
    date_ranges: Optional[List[Tuple[date, date]]] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get applications for officer with optional status, year, month, date-range, and geography filters.
    By default, returns every active application (pending, in_progress and
    escalated -- see ACTIVE_STATUSES). Escalated used to be left out here while
    the workload summary and the overdue list counted it, so the officer was
    shown three different totals for the same queue.
    When a date range (start_date/end_date) is provided, show all statuses within that range.
    """
    # A channel scopes the question the way a period does -- "show applications
    # from CSC" asks the register, not the open queue -- so it suppresses the
    # active-status default too. Without this the officer's 30 CSC files came
    # back as the single one that was still in progress.
    #
    # An application type scopes it the same way: "show my ISD applications"
    # asks the officer's whole ISD history, not the ISD files still open today.
    # Left in, the active-status default answered 2 to an officer holding 9 ISD
    # files and 1 to one holding 58 NISD -- the same failure as the channel one,
    # and the counterpart of has_scope_filter in get_officer_applications, which
    # already stops pinning the current stage for an explicit type.
    if (status is None and not submission_year and not start_date and not end_date
            and not date_ranges and is_overdue is None and not submission_channel
            and not application_type):
        status = list(ACTIVE_STATUSES)
    return await get_officer_applications(
        db, officer,
        status=status,
        application_type=application_type,
        submission_year=submission_year,
        submission_month=submission_month,
        taluk_name=taluk_name,
        ward_number=ward_number,
        block_number=block_number,
        start_date=start_date,
        end_date=end_date,
        is_overdue=is_overdue,
        stage=stage,
        exclude_date_range=exclude_date_range,
        submission_channel=submission_channel,
        date_ranges=date_ranges,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


async def get_overdue_applications(
    db: AsyncSession,
    officer: OfficerContext,
    application_type: Optional[str] = None,
    min_days_overdue: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    ward_number: Optional[str] = None,
    block_number: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get overdue applications within officer's jurisdiction AND stage

    `ward_number` / `block_number` narrow the queue to one ward or block, the
    same way get_officer_applications does -- "overdue applications in block
    0015" used to ignore the block and answer for the whole jurisdiction.
    """
    try:
        # Get jurisdiction filters
        jurisdiction_filters = await get_jurisdiction_filter(db, officer)
        
        if not jurisdiction_filters:
            return {"count": 0, "applications": [], "message": "No jurisdiction assigned"}
        
        # Get the officer's stage
        officer_stage = officer.officer_stage
        
        query = select(Application).options(
            selectinload(Application.survey_number).selectinload(SurveyNumber.block).selectinload(Block.ward).selectinload(Ward.town).selectinload(Town.taluk).selectinload(Taluk.district),
            selectinload(Application.survey_number).selectinload(SurveyNumber.sub_divisions),
            selectinload(Application.application_sub_divisions).selectinload(ApplicationSubDivision.sub_division),
            selectinload(Application.patta_transfers).selectinload(PattaTransfer.sub_division),
            selectinload(Application.applicant)
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
                Application.current_stage == officer_stage,  # CRITICAL FIX: Filter by stage
                Application.current_status != 'rejected',  # Exclude rejected apps (ghost data)
                Application.is_overdue == True
            )
        )
        
        if application_type:
            if application_type == "ISD":
                query = query.where(Application.application_type.in_(["ISD"]))
            else:
                query = query.where(Application.application_type == application_type)
        if start_date:
            query = query.where(Application.submission_date >= start_date)
        if end_date:
            query = query.where(Application.submission_date <= end_date)
            
        result = await db.execute(query)
        applications = result.scalars().all()
        
        today = datetime.now().date()
        app_rows = []
        for app in applications:
            sn = app.survey_number
            block = sn.block if sn else None
            ward = block.ward if block else None
            town = ward.town if ward else None
            taluk = town.taluk if town else None
            district = taluk.district if taluk else None
            applicant = app.applicant if app else None
            jur_dict = _resolve_jurisdiction(app, town, taluk, district, block, ward)

            # "Overdue" is the field-visit deadline -- more than 15 WORKING days
            # (Mon-Fri) since submission with no visit completed (sla.py,
            # field_visit_overdue()) -- a different clock from a visit's own
            # scheduled date. This used to compute "days overdue" from
            # `field_visit_date` (the SCHEDULED date) when it was in the past,
            # which is the other clock CLAUDE.md documents as NOT the same
            # thing, and fell back to raw calendar days (today - submission).days
            # - 15 otherwise -- both working-day-based rules everywhere else in
            # the app (sla.py's `working_days_between`) use Mon-Fri counting, so
            # a submission 20 calendar days back with a weekend in it was
            # reporting a different "days overdue" figure than the rule that
            # decided the file belonged in this list at all.
            if app.submission_date:
                from backend.utils.sla import working_days_between, FIELD_VISIT_DEADLINE_WD
                calc_days_overdue = max(1, working_days_between(app.submission_date, today) - FIELD_VISIT_DEADLINE_WD)
            else:
                calc_days_overdue = 1

            if min_days_overdue is not None and calc_days_overdue < min_days_overdue:
                continue

            # The sub-divisions THIS application is about -- never the parcel's
            # whole set (see application_subdivision_list).
            subdiv_list = application_subdivision_list(app)

            base_survey_no = sn.survey_no if sn else "N/A"
            subdivisions_str = format_survey_with_subdivisions(base_survey_no, subdiv_list)

            app_rows.append({
                "application_number": app.application_number,
                "type": app.application_type,
                "status": app.current_status,
                "stage": app.current_stage,
                "submission_date": app.submission_date.isoformat() if app.submission_date else None,
                "days_overdue": calc_days_overdue,
                "is_overdue": True,
                "survey_no": base_survey_no,
                "raw_survey_no": base_survey_no,
                "subdivisions": subdivisions_str,
                "sub_division_no": subdivisions_str,
                "district_name": jur_dict["district"],
                "taluk_name":    jur_dict["taluk"],
                "town_name":     jur_dict["town"],
                "ward_number":   ward.ward_number if ward else "N/A",
                "block_number":  block.block_number if block else "N/A",
                "jurisdiction": jur_dict,
                "applicant_name": applicant.name if applicant else "N/A",
                "applicant_mobile": applicant.mobile if applicant else "N/A",
                "applicant_address": applicant.address if applicant else "N/A",
                "included_subdivisions": ", ".join(subdiv_list) or "None"
            })

        # Geography narrowing, matched the same loose way as the listing
        # queries so "block 15" finds block "0015".
        def _geo_hit(row, key, wanted):
            value = str((row.get("jurisdiction") or {}).get(key) or "")
            return str(wanted).lower() in value.lower()

        if ward_number:
            app_rows = [r for r in app_rows if _geo_hit(r, "ward", ward_number)]
        if block_number:
            app_rows = [r for r in app_rows if _geo_hit(r, "block", block_number)]

        out = {
            "count": len(app_rows),
            "jurisdiction_type": officer.jurisdiction_type,
            "min_days_overdue": min_days_overdue,
            "applications": app_rows
        }
        if not app_rows and not (application_type or min_days_overdue or start_date or end_date or ward_number or block_number):
            out["empty_note"] = await _no_overdue_note(db, jurisdiction_filters, officer_stage, today, officer.officer_id)
        return out
    except Exception as e:
        logger.error(f"Error getting overdue applications: {e}")
        return {"count": 0, "applications": [], "error": str(e)}


async def _no_overdue_note(db, jurisdiction_filters, officer_stage, today, officer_id) -> str:
    """Why the overdue list is empty, in the documented terms: "overdue" is the 15-working-day field-visit
    deadline (backend/utils/sla.py), which is NOT the same as a visit being past its scheduled date."""
    from backend.utils.sla import working_days_between, FIELD_VISIT_DEADLINE_WD
    rows = (await db.execute(
        select(Application.submission_date, Application.field_visit_date).join(
            SurveyNumber, Application.survey_number_id == SurveyNumber.id
        ).join(Block, SurveyNumber.block_id == Block.id).join(Ward, Block.ward_id == Ward.id)
        .join(Town, Ward.town_id == Town.id).join(Taluk, Town.taluk_id == Taluk.id)
        .join(District, Taluk.district_id == District.id)
        .where(and_(or_(*jurisdiction_filters), Application.current_stage == officer_stage,
                    Application.application_type == "ISD",
                    Application.current_status.in_(["pending", "in_progress", "escalated"]))))).all()
    if not rows:
        return "None is past the 15-working-day field-visit deadline (you have no open ISD file)."
    oldest = min((r[0] for r in rows if r[0]), default=None)
    age = working_days_between(oldest, today) if oldest else 0
    # "Late" visits counted from FIELD_VISITS, scoped to the field visits THIS
    # officer is actually assigned (`FieldVisit.officer_id`), not every
    # application in a ward several officers can share. This used to count
    # `Application.field_visit_date < today` under only the ward/stage filter
    # above, so an ISD file assigned to a DIFFERENT officer in the same
    # shared ward was still counted here -- three officers covering the same
    # ward 001 were each told "3 field visits are past their scheduled date",
    # when all three visits were actually booked to only one of them (the
    # other two genuinely have zero). `get_overdue_field_visits`-style
    # scoping (officer_id + status in scheduled/rescheduled/overdue) is the
    # one fv_overdue_inspections already uses; this now matches it so the two
    # answers to the same question agree.
    late_stmt = (
        select(FieldVisit.id).join(Application, FieldVisit.application_id == Application.id)
        .where(FieldVisit.officer_id == officer_id, FieldVisit.scheduled_date < today,
               FieldVisit.status.in_(["scheduled", "rescheduled", "overdue"]))
    )
    late = len((await db.execute(late_stmt)).all())
    note = (f"None is past the {FIELD_VISIT_DEADLINE_WD}-working-day field-visit deadline "
            f"(your oldest open ISD file is {age} working days old).")
    if late:
        note += f" {late} field visit(s) are past their scheduled date though -- ask which application to field visit next."
    return note


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
        
        query = select(Application).options(
            selectinload(Application.survey_number).selectinload(SurveyNumber.block).selectinload(Block.ward).selectinload(Ward.town).selectinload(Town.taluk).selectinload(Taluk.district)
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
                query = query.where(Application.application_type.in_(["ISD"]))
            else:
                query = query.where(Application.application_type == application_type)
            
        result = await db.execute(query)
        applications = result.scalars().all()
        
        app_rows = []
        for app in applications:
            sn = app.survey_number
            block = sn.block if sn else None
            ward = block.ward if block else None
            town = ward.town if ward else None
            taluk = town.taluk if town else None
            district = taluk.district if taluk else None
            jur_dict = _resolve_jurisdiction(app, town, taluk, district, block, ward)
            app_rows.append({
                "application_number": app.application_number,
                "type": app.application_type,
                "status": app.current_status,
                "stage": app.current_stage,
                "submission_date": app.submission_date.isoformat() if app.submission_date else None,
                "is_overdue": app.is_overdue,
                "priority_flag": app.priority_flag,
                "field_visit_scheduled": app.field_visit_scheduled,
                "field_visit_date": app.field_visit_date.isoformat() if app.field_visit_date else None,
                "days_pending": (datetime.now().date() - app.submission_date).days if app.submission_date else 0,
                "district_name": jur_dict["district"],
                "taluk_name":    jur_dict["taluk"],
                "town_name":     jur_dict["town"],
                "ward_number":   ward.ward_number if ward else "N/A",
                "block_number":  block.block_number if block else "N/A",
                "jurisdiction": jur_dict
            })

        return {
            "count": len(app_rows),
            "jurisdiction_type": officer.jurisdiction_type,
            "applications": app_rows
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
            return {"total_active": 0, "ISD": 0, "NISD": 0, "overdue": 0, "message": "No jurisdiction assigned"}
        
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
                    Application.current_status.in_(ACTIVE_STATUSES)
                )
            )
        )
        
        nisd_count = await db.execute(
            base_query.where(
                and_(
                    Application.application_type == "NISD",
                    Application.current_status.in_(ACTIVE_STATUSES)
                )
            )
        )
        
        overdue_count = await db.execute(
            base_query.where(
                and_(
                    Application.is_overdue == True,
                    Application.current_status.in_(ACTIVE_STATUSES)
                )
            )
        )
        
        # Count unscheduled field visits
        unscheduled_count = await db.execute(
            base_query.where(
                and_(
                    Application.field_visit_scheduled == False,
                    Application.current_status.in_(ACTIVE_STATUSES)
                )
            )
        )
        
        isd_val = isd_count.scalar() or 0
        nisd_val = nisd_count.scalar() or 0
        overdue_val = overdue_count.scalar() or 0
        unscheduled_val = unscheduled_count.scalar() or 0

        # "My workload" used to mean only ISD/NISD, because `Application`
        # (ck_application_type) is the only table with a real workflow and
        # admits nothing else. But the officer's actual desk carries every
        # TAMILNILAM service code (0158 Modification, 0165 TSLR Owner Name
        # Correction, 0178 F-Line, ...), already answered elsewhere via
        # `service_register_entries` (get_all_service_code_applications,
        # "pending applications" widened to all codes) -- "what is my
        # workload" just never looked there, so an officer with 44 open
        # files across every code read "17 active applications" as their
        # whole desk.
        #
        # Every code is counted the same way and reported in ONE flat
        # breakdown -- not "N ISD/NISD... plus M other codes", which still
        # read as ISD/NISD being the real workload and everything else an
        # afterthought. This is the same per-code breakdown style
        # get_all_service_code_applications() already renders ("found N
        # across every service code: 0153 (9), 0154 (10), 0156 (1), ...").
        # Other codes' status is coarse (approved/rejected/pending only, no
        # in_progress/escalated split), so "pending" is their active state.
        by_code_rows = (await db.execute(
            select(ServiceRegisterEntry.service_code, func.count()).where(
                and_(
                    ServiceRegisterEntry.ward_id.in_(officer.jurisdiction_ids or []),
                    ServiceRegisterEntry.service_code.notin_(["0153", "0154"]),
                    ServiceRegisterEntry.status == "pending",
                )
            ).group_by(ServiceRegisterEntry.service_code)
        )).all()
        by_service_code = {"0153": nisd_val, "0154": isd_val}
        for code, n in by_code_rows:
            by_service_code[code] = n
        other_codes_val = sum(n for code, n in by_service_code.items() if code not in ("0153", "0154"))

        return {
            "total_active": isd_val + nisd_val,
            "ISD": isd_val,
            "NISD": nisd_val,
            "overdue": overdue_val,
            "unscheduled_visits": unscheduled_val,
            "other_codes_active": other_codes_val,
            "total_active_all_codes": isd_val + nisd_val + other_codes_val,
            "by_service_code": by_service_code,
        }
    except Exception as e:
        logger.error(f"Error getting officer workload: {e}")
        return {"total_active": 0, "error": str(e)}


async def get_officer_workload_applications(db: AsyncSession, officer: OfficerContext) -> Dict[str, Any]:
    """The individual applications behind `get_officer_workload()`'s counts.

    "What is my workload?" -> "show" used to fail ("I do not know which
    application or list you mean") because the workload answer is pure
    aggregate counts -- no application numbers were ever returned for a
    follow-up to resolve against. Re-running a GENERIC "pending applications"
    query for "show" would not do: that query (`get_all_service_code_applications`,
    via `service_register_entries`) is never stage-pinned for ANY code, while
    the workload's own ISD/NISD count IS pinned to the officer's current
    desk (`Application.current_stage == officer_stage`) -- so it would show
    more ISD/NISD files than the workload just counted, a scope mismatch the
    officer has no way to notice.

    This reruns the SAME two business rules `get_officer_workload()` counts
    by, row-for-row: ISD/NISD files at the officer's own stage with an
    active status (`get_officer_applications(status=ACTIVE_STATUSES)`), plus
    every other service code's pending register rows (the same query
    `get_officer_workload()`'s `by_code_rows` runs). The combined count
    always equals `total_active_all_codes`.
    """
    from backend.utils.helpers import SIS_URBAN_SERVICES
    try:
        if not officer or not officer.jurisdiction_ids:
            return {"count": 0, "applications": [], "query_type": "Workload Applications"}

        isd_nisd = await get_officer_applications(db, officer, status=list(ACTIVE_STATUSES))
        apps: List[Dict[str, Any]] = []
        for a in isd_nisd.get("applications", []):
            code = {"ISD": "0154", "NISD": "0153"}.get(a.get("type"))
            apps.append({
                "application_number": a.get("application_number"),
                "application_type": a.get("type"),
                "service_code": code,
                "service_name": SIS_URBAN_SERVICES.get(code, {}).get("name", a.get("type")),
                "survey_no": a.get("survey_no"),
                "subdivision_no": a.get("sub_division_no"),
                "status": a.get("status"),
                "stage": a.get("stage"),
                "submission_channel": a.get("submission_channel"),
                "submission_date": a.get("submission_date"),
                "ward_number": a.get("ward_number"),
                "ward_name": "N/A",
                "block_number": a.get("block_number"),
                "block_name": "N/A",
                "town_name": a.get("town_name"),
                "taluk_name": a.get("taluk_name"),
                "district_name": a.get("district_name"),
            })

        other = await get_all_service_code_applications(db, officer, status="pending")
        apps.extend(r for r in other.get("applications", [])
                    if r.get("service_code") not in ("0153", "0154"))

        return {
            "count": len(apps),
            "applications": apps,
            "query_type": "Workload Applications",
            "empty_note": None if apps else "No active applications found in your jurisdiction.",
        }
    except Exception as e:
        logger.error(f"Error getting officer workload applications: {e}")
        return {"count": 0, "applications": [], "query_type": "Workload Applications", "error": str(e)}


def _application_jurisdiction_subquery(jurisdiction_filters):
    """Subquery of application ids whose survey number falls inside the officer's jurisdiction."""
    return (
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


def _field_visit_jurisdiction_subquery(jurisdiction_filters):
    """Subquery of field-visit ids whose application's survey number falls inside the
    officer's jurisdiction. See `get_field_visits` for why this replaces a strict
    `FieldVisit.officer_id` match: the live seed data assigns every field visit to
    one officer regardless of ward, so filtering on officer_id alone answered "no
    field visits" to every other officer sharing that ward."""
    return (
        select(FieldVisit.id)
        .join(Application, FieldVisit.application_id == Application.id)
        .join(SurveyNumber, Application.survey_number_id == SurveyNumber.id)
        .join(Block, SurveyNumber.block_id == Block.id)
        .join(Ward, Block.ward_id == Ward.id)
        .join(Town, Ward.town_id == Town.id)
        .join(Taluk, Town.taluk_id == Taluk.id)
        .join(District, Taluk.district_id == District.id)
        .where(or_(*jurisdiction_filters))
        .scalar_subquery()
    )


async def lookup_application_access(
    db: AsyncSession,
    application_number: str,
    officer: OfficerContext
) -> Dict[str, Any]:
    """
    Establish the facts needed to decide whether an officer may be told anything
    about `application_number`. Pure lookup: it never normalises the number, never
    picks a near match, and never returns data belonging to another jurisdiction.

    Returns:
        exists       — the number matches a real application, anywhere in the DB
        accessible   — that application is inside the officer's jurisdiction
        candidates   — jurisdiction-scoped near matches, offered only as suggestions
        recent       — jurisdiction-scoped recent applications, for orientation
    """
    from sqlalchemy.orm import joinedload

    number = (application_number or "").strip()
    result: Dict[str, Any] = {
        "searched_number": number,
        "exists": False,
        "accessible": False,
        "candidates": [],
        "recent": [],
    }
    if not number:
        return result

    jurisdiction_filters = await get_jurisdiction_filter(db, officer) if officer else []

    # 1. Does this exact number exist at all? Answered without any jurisdiction
    #    filter so we can tell "no such application" apart from "not yours".
    exists_row = (await db.execute(
        select(Application.id).where(Application.application_number == number)
    )).first()
    result["exists"] = exists_row is not None

    if result["exists"]:
        if not jurisdiction_filters:
            # No jurisdiction rows configured — deny rather than leak.
            result["accessible"] = False
            return result
        allowed = (await db.execute(
            select(Application.id)
            .where(Application.application_number == number)
            .where(Application.id.in_(_application_jurisdiction_subquery(jurisdiction_filters)))
        )).first()
        result["accessible"] = allowed is not None
        return result

    # 2. No exact match. Collect *suggestions only* — never auto-selected.
    #
    # Both sources are searched, not just `Application` (ISD/NISD, codes
    # 0153/0154 only). A mistyped or fabricated number under any OTHER
    # TAMILNILAM service code -- "2026/0178/02/000950" naming no real F-Line
    # file -- used to fall straight through to these suggestions with no
    # match of its own kind at all, and the officer was offered unrelated
    # ISD/NISD applications to choose from, as though their file could only
    # ever be one of those two codes. `service_register_entries` covers
    # every code the register tracks (see `ServiceRegisterEntry`'s own
    # docstring); merging its near-matches in means a near-miss on the
    # SAME service code is offered first, and the generic recent-activity
    # list is no longer ISD/NISD-only either.
    def _summarise(rows):
        return [
            {
                "application_number": r.application_number,
                "type": r.application_type,
                "status": r.current_status,
                "stage": r.current_stage,
                "applicant_name": r.applicant.name if r.applicant else "N/A",
            }
            for r in rows
        ]

    def _summarise_register(rows):
        return [
            {
                "application_number": r.application_number,
                "type": r.service_code,
                "status": r.status,
                "stage": r.stage,
                "applicant_name": "N/A",
            }
            for r in rows
        ]

    _ward_ids = list(getattr(officer, "jurisdiction_ids", None) or []) if officer else []

    prefix = number.rstrip("/")
    # Each source is capped at HALF the total, not the full 6 -- `Application`
    # alone easily fills all 6 slots on its own (it is never short of rows),
    # which left no room for a single `service_register_entries` match to
    # ever appear even though the merge above looks like it should: filling
    # one list to the cap FIRST and only then extending it with the other
    # is a no-op if the first list was never short of rows.
    _PER_SOURCE = 3
    candidates: List[Dict[str, Any]] = []
    if prefix:
        cand_q = (
            select(Application)
            .options(joinedload(Application.applicant))
            .where(Application.application_number.ilike(f"{prefix}%"))
            .limit(_PER_SOURCE)
        )
        if jurisdiction_filters:
            cand_q = cand_q.where(Application.id.in_(_application_jurisdiction_subquery(jurisdiction_filters)))
        candidates.extend(_summarise((await db.execute(cand_q)).scalars().all()))
        if _ward_ids:
            reg_cand_q = (
                select(ServiceRegisterEntry)
                .where(ServiceRegisterEntry.application_number.ilike(f"{prefix}%"),
                       ServiceRegisterEntry.ward_id.in_(_ward_ids))
                .limit(_PER_SOURCE)
            )
            candidates.extend(_summarise_register((await db.execute(reg_cand_q)).scalars().all()))
    result["candidates"] = candidates[:6]

    recent_q = (
        select(Application)
        .options(joinedload(Application.applicant))
        .order_by(Application.created_at.desc())
        .limit(_PER_SOURCE)
    )
    if jurisdiction_filters:
        recent_q = recent_q.where(Application.id.in_(_application_jurisdiction_subquery(jurisdiction_filters)))
    recent = _summarise((await db.execute(recent_q)).scalars().all())
    if _ward_ids:
        reg_recent_q = (
            select(ServiceRegisterEntry)
            .where(ServiceRegisterEntry.ward_id.in_(_ward_ids))
            .order_by(ServiceRegisterEntry.created_at.desc())
            .limit(_PER_SOURCE)
        )
        recent.extend(_summarise_register((await db.execute(reg_recent_q)).scalars().all()))
    result["recent"] = recent[:6]

    return result


async def get_can_details(
    db: AsyncSession,
    officer: OfficerContext = None,
    application_number: Optional[str] = None,
    can_number: Optional[str] = None,
) -> Dict[str, Any]:
    """
    The CAN carried by one application, looked up either by application number or
    by the CAN itself.

    The channel is read off `submission_channel` rather than re-derived here.
    The CAN's length names the counter that issued it, not the channel: 15
    digits is an CSC counter, 12 is the TN portal. A Sub-Registrar referral
    carries a 12-digit CAN and an IGRS Form 6 number equal to it (see
    backend/sample_db/identifiers.py).
    """
    if not application_number and not can_number:
        return {"found": False}
    try:
        from sqlalchemy.orm import joinedload
        query = select(Application).options(joinedload(Application.applicant))
        if application_number:
            query = query.where(Application.application_number == application_number)
        else:
            query = query.where(Application.can_number == can_number)
        if officer:
            filters = await get_jurisdiction_filter(db, officer)
            if filters:
                query = query.where(Application.id.in_(_application_jurisdiction_subquery(filters)))
        app = (await db.execute(query)).unique().scalars().first()
        if not app:
            return {"found": False, "application_number": application_number,
                    "can_number": can_number}
        can = app.can_number or ""
        channel = {
            "CSC": "Common Service Centre",
            "citizen": "Citizen portal (filed by the citizen)",
            "sub_registrar": "Sub-Registrar referral (IGRS Form 6)",
        }.get(app.submission_channel, app.submission_channel or "unknown")
        return {
            "found": True,
            "application_number": app.application_number,
            "can_number": can or "not recorded",
            "digits": len(can) if can else 0,
            "channel": channel,
            "igrs_form6_number": app.igrs_form6_number,
            "submission_channel": app.submission_channel,
            "submission_source_name": app.submission_source_name,
            "submission_camp_flag": app.submission_camp_flag,
            "applicant_name": app.applicant.name if app.applicant else None,
            "submission_date": app.submission_date.isoformat() if app.submission_date else None,
        }
    except Exception as exc:                                  # noqa: BLE001
        logger.error(f"Error in get_can_details: {exc}")
        return {"found": False, "error": str(exc)}


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
            selectinload(Application.assigned_officer),
            selectinload(Application.survey_number).selectinload(SurveyNumber.block).selectinload(Block.ward).selectinload(Ward.town).selectinload(Town.taluk).selectinload(Taluk.district),
            selectinload(Application.application_sub_divisions).joinedload(ApplicationSubDivision.sub_division),
            selectinload(Application.application_sub_divisions).selectinload(ApplicationSubDivision.owners),
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
            # No exact match. We deliberately do NOT normalise the number
            # (zero-padding segments) and do NOT auto-select the closest match:
            # silently answering about a different application is worse than
            # saying we could not find this one. Near matches are offered as
            # suggestions the officer must confirm explicitly.
            #
            # Both `applications` (ISD/NISD, codes 0153/0154 only) and
            # `service_register_entries` (every other code: 0158, 0164, 0178
            # F-Line, ...) are searched -- a mistyped or fabricated number
            # under any other code used to get only ISD/NISD "did you mean"
            # suggestions, which named a kind of file the officer never
            # asked about. See the matching fix and its longer comment in
            # `lookup_application_access()`.
            def _reg_ward_ids():
                return list(getattr(officer, "jurisdiction_ids", None) or []) if officer else []

            def _summarise_register(rows):
                return [
                    {
                        "application_number": r.application_number,
                        "type": r.service_code,
                        "status": r.status,
                        "stage": r.stage,
                        "applicant_name": "N/A",
                    }
                    for r in rows
                ]

            suggestions = []

            # Each source capped at HALF the total -- see the matching
            # comment in `lookup_application_access()`: `Application` alone
            # is never short of 6 rows, so filling it to the cap first and
            # only then adding register rows left no room for the second
            # source to ever actually appear.
            _PER_SOURCE = 3
            prefix = application_number.strip().rstrip("/")
            if prefix:
                prefix_query = select(Application).options(
                    joinedload(Application.applicant)
                ).where(Application.application_number.ilike(f"{prefix}%")).limit(_PER_SOURCE)
                if officer and jurisdiction_filters:
                    prefix_query = prefix_query.where(Application.id.in_(jurisdiction_subquery))
                prefix_apps = (await db.execute(prefix_query)).scalars().all()
                suggestions = [
                    {
                        "application_number": pa.application_number,
                        "type": pa.application_type,
                        "status": pa.current_status,
                        "stage": pa.current_stage,
                        "applicant_name": pa.applicant.name if pa.applicant else "N/A"
                    }
                    for pa in prefix_apps
                ]
                _ward_ids = _reg_ward_ids()
                if _ward_ids:
                    reg_prefix_query = (
                        select(ServiceRegisterEntry)
                        .where(ServiceRegisterEntry.application_number.ilike(f"{prefix}%"),
                               ServiceRegisterEntry.ward_id.in_(_ward_ids))
                        .limit(_PER_SOURCE)
                    )
                    suggestions.extend(_summarise_register(
                        (await db.execute(reg_prefix_query)).scalars().all()))
                suggestions = suggestions[:6]

            if suggestions:
                return {
                    "found": False,
                    "needs_confirmation": True,
                    "message": (
                        f"Application {application_number} was not found. "
                        f"Found {len(suggestions)} similar application(s) — please confirm which one you mean."
                    ),
                    "suggestions": suggestions,
                    "searched_number": application_number,
                    "query_type": "Application Suggestions"
                }

            # Nothing similar — show recent applications in the officer's
            # jurisdiction purely for orientation.
            recent_query = select(Application).options(
                joinedload(Application.applicant)
            ).order_by(Application.created_at.desc()).limit(_PER_SOURCE)
            if officer and jurisdiction_filters:
                recent_query = recent_query.where(Application.id.in_(jurisdiction_subquery))
            recent_apps = (await db.execute(recent_query)).scalars().all()
            recent_suggestions = [
                {
                    "application_number": ra.application_number,
                    "type": ra.application_type,
                    "status": ra.current_status,
                    "stage": ra.current_stage,
                    "applicant_name": ra.applicant.name if ra.applicant else "N/A"
                }
                for ra in recent_apps
            ]
            _ward_ids = _reg_ward_ids()
            if _ward_ids:
                reg_recent_query = (
                    select(ServiceRegisterEntry)
                    .where(ServiceRegisterEntry.ward_id.in_(_ward_ids))
                    .order_by(ServiceRegisterEntry.created_at.desc())
                    .limit(_PER_SOURCE)
                )
                recent_suggestions.extend(_summarise_register(
                    (await db.execute(reg_recent_query)).scalars().all()))
            return {
                "found": False,
                "message": f"Application {application_number} not found.",
                "suggestions": recent_suggestions[:6],
                "searched_number": application_number,
                "query_type": "Application Suggestions"
            }

        # Compile proposed sub-divisions with full detail
        sub_divisions_list = []
        proposed_sub_divisions = []
        for assoc in app.application_sub_divisions:
            sub_div_no = assoc.proposed_sub_division_no or (
                assoc.sub_division.sub_division_no if assoc.sub_division else None
            )
            if sub_div_no:
                sub_divisions_list.append(sub_div_no)
                _sd_owners = sorted(
                    (assoc.owners or []),
                    key=lambda o: (o.owner_no if o.owner_no is not None else 999),
                )
                proposed_sub_divisions.append({
                    "proposed_sub_division_no": sub_div_no,
                    # The provisional "3/T1" number the file runs under. Equal to
                    # proposed_sub_division_no while no final number exists.
                    "temporary_sub_division_no": assoc.temporary_sub_division_no,
                    "final_sub_division_no": (
                        sub_div_no if sub_div_no != assoc.temporary_sub_division_no else None),
                    "proposed_area_sqm": (
                        float(assoc.proposed_area_sqm) if assoc.proposed_area_sqm
                        else (float(assoc.sub_division.area_sqm) if assoc.sub_division and assoc.sub_division.area_sqm else None)
                    ),
                    "status": assoc.status or "pending",
                    "owners": [
                        {
                            "owner_no": o.owner_no,
                            "name": o.name or o.name_tamil,
                            "name_tamil": o.name_tamil,
                            "relationship": o.relationship_type,
                            "relative_name": o.relative_name,
                            "ownership_share": o.ownership_share,
                            "gender": o.gender,
                        }
                        for o in _sd_owners
                    ],
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

        # Count patta transfers for this application. Eager-load both owner
        # sides -- "who is the new owner / previous owner of <app>" is a
        # bread-and-butter question and reading pt.new_owner / pt.previous_owner
        # lazily inside async context throws.
        pt_query = (
            select(PattaTransfer)
            .where(PattaTransfer.application_id == app.id)
            .options(
                selectinload(PattaTransfer.previous_owner),
                selectinload(PattaTransfer.new_owner),
                selectinload(PattaTransfer.sub_division),
            )
        )
        pt_result = await db.execute(pt_query)
        patta_transfers = pt_result.scalars().all()

        def _party(o):
            """The columns the SIS owner register actually projects for a
            transfer party. relationship / ownership_share / extent / UDS /
            photo live only in the un-projected nisd_transfer_*_owner extract,
            so they are not claimed here."""
            if o is None:
                return None
            return {
                "name": o.name or o.name_tamil,
                "name_tamil": o.name_tamil,
                "name_english": o.name if (o.name and o.name != o.name_tamil) else None,
                "relative_name": o.father_name,
                "aadhaar_last4": o.aadhaar_last4,
                "gender": o.gender,
            }

        transfer_parties = [
            {
                "sub_division_no": pt.sub_division.sub_division_no if pt.sub_division else None,
                "new_patta_number": pt.new_patta_number,
                "status": pt.status,
                "previous_owner": _party(pt.previous_owner),
                "new_owner": _party(pt.new_owner),
            }
            for pt in patta_transfers
        ]
        _first_prev = next((p["previous_owner"] for p in transfer_parties if p["previous_owner"]), None)
        _first_new = next((p["new_owner"] for p in transfer_parties if p["new_owner"]), None)

        # Survey totals for area comparison
        survey_total_area = float(app.survey_number.total_area_sqm) if app.survey_number and app.survey_number.total_area_sqm else None
        # Sum proposed area — prefer explicit proposed_area_sqm; fall back to sub_division.area_sqm
        proposed_total_area = None
        if proposed_sub_divisions:
            areas = [sd["proposed_area_sqm"] for sd in proposed_sub_divisions if sd["proposed_area_sqm"] is not None]
            if areas:
                proposed_total_area = sum(areas)

        # Field visit: most recent entry from FieldVisit table
        # "Last updated" for an application = the timestamp of its most recent
        # workflow hop (build_app_tables.py stamps updated_at once at build time,
        # so that column is not meaningful here). Falls back to updated_at.
        last_updated = await db.scalar(
            select(func.max(WorkflowHistory.performed_at)).where(
                WorkflowHistory.application_id == app.id
            )
        )
        _lu = last_updated or app.updated_at
        # Stored (and read back by asyncpg) as UTC; converted to IST before display
        # -- an officer asking "when was this last updated" means Tamil Nadu wall-clock
        # time, and a bare .strftime() here showed the UTC value 5h30m early.
        last_updated_iso = _lu.astimezone(IST).strftime("%Y-%m-%d %H:%M") if _lu else None

        # When the file was decided -- approved or rejected. This is the
        # `performed_at` of the hop that closed the chain, which is the only
        # trustworthy source: patta_transfers.tahsildar_signature_date is
        # missing on 13 completed applications and contradicts the workflow on
        # 21 more (some carry deed dates as old as 2013).
        decision_hop = (await db.execute(
            select(WorkflowHistory)
            .where(WorkflowHistory.application_id == app.id,
                   WorkflowHistory.to_stage.in_(("COMPLETED", "REJECTED")))
            .order_by(WorkflowHistory.performed_at.desc()).limit(1)
        )).scalars().first()
        decision_date = decision_hop.performed_at.date().isoformat() if decision_hop else None
        decision_stage = decision_hop.to_stage if decision_hop else None
        # The DESK that actually made the call -- the FROM-stage of the hop that
        # closed the file (SD for a seeded ISD file, TAHSILDAR for NISD), not the
        # terminal to_stage (COMPLETED/REJECTED) `decision_stage` already carries.
        # "has the ZDT approved X?" on an ISD file (closed at SD; ZDT never
        # reaches one -- see CLAUDE.md's documented workflow chains) must be
        # answered from THIS, never from `application_status` alone.
        decision_desk = decision_hop.from_stage if decision_hop else None
        decision_by = None
        if decision_hop is not None and decision_hop.performed_by_officer_id:
            decision_by = await db.scalar(
                select(SISOfficer.name).where(
                    SISOfficer.id == decision_hop.performed_by_officer_id)
            )

        field_visit_info = None
        if app.field_visits:
            latest_visit = max(app.field_visits, key=lambda v: v.created_at)
            field_visit_info = {
                "status": latest_visit.status,
                "scheduled_date": latest_visit.scheduled_date.isoformat() if latest_visit.scheduled_date else None,
                "actual_date": latest_visit.actual_date.isoformat() if latest_visit.actual_date else None,
                "encroachment_found": latest_visit.encroachment_found,
                "encroachment_notes": latest_visit.encroachment_notes,
                "area_verified": latest_visit.area_verified,
                "visit_notes": latest_visit.visit_notes,
            }

        # Survey & geography hierarchy
        sn = app.survey_number
        block = sn.block if sn else None
        ward = block.ward if block else None
        town = ward.town if ward else None
        taluk = town.taluk if town else None
        district = taluk.district if taluk else None
        jur_dict = _resolve_jurisdiction(app, town, taluk, district, block, ward)
        # Use name variants for the detail view (ward name / block name preferred)
        if ward:
            jur_dict["ward"] = ward.ward_name or f"Ward {ward.ward_number}"
        if block:
            jur_dict["block"] = block.block_name or f"Block {block.block_number}"

        return {
            "found": True,
            "application_number": app.application_number,
            "type": app.application_type,
            "status": app.current_status,
            "stage": app.current_stage,
            "submission_date": app.submission_date.isoformat(),
            "submission_channel": app.submission_channel,
            # what the channel was derived from, so the answer can show
            # its working when the officer asks "how do you know?"
            "submission_source_name": app.submission_source_name,
            "submission_ip": app.submission_ip,
            "submission_camp_flag": app.submission_camp_flag,
            "is_overdue": app.is_overdue,
            "priority_flag": app.priority_flag,
            "district_name": jur_dict["district"],
            "taluk_name":    jur_dict["taluk"],
            "town_name":     jur_dict["town"],
            "ward_number":   ward.ward_number if ward else "N/A",
            "block_number":  block.block_number if block else "N/A",
            # Flat, parallel to district_name/taluk_name above -- so a "what
            # ward is it in" follow-up can prefer the name the same way, instead
            # of reading ward_number (the code, "002") straight off the row.
            "ward_name":     jur_dict["ward"],
            "block_name":    jur_dict["block"],
            "jurisdiction": jur_dict,
            # Applicant
            "applicant_name": app.applicant.name if app.applicant else None,
            "applicant_mobile": app.applicant.mobile if app.applicant else None,
            "applicant_address": app.applicant.address if app.applicant else None,
            "applicant_permanent_address": app.applicant.permanent_address if app.applicant else None,
            "applicant_father_name": app.applicant.father_name if app.applicant else None,
            "applicant_mother_name": app.applicant.mother_name if app.applicant else None,
            "applicant_dob": app.applicant.date_of_birth.isoformat() if (app.applicant and app.applicant.date_of_birth) else None,
            "applicant_gender": app.applicant.gender if app.applicant else None,
            "applicant_occupation": app.applicant.occupation if app.applicant else None,
            "fee_amount": float(app.fee_amount) if app.fee_amount is not None else None,
            "challan_number": app.challan_number,
            "payment_mode": app.payment_mode,
            "igrs_form6_number": app.igrs_form6_number,
            "merged_application_id": app.merged_application_id,
            "can_number": app.can_number,
            "applicant": {
                "name": app.applicant.name,
                "mobile": app.applicant.mobile,
                "address": app.applicant.address
            } if app.applicant else None,
            # Sub-divisions
            "included_subdivisions": ", ".join(sub_divisions_list) if sub_divisions_list else None,
            "proposed_sub_divisions": proposed_sub_divisions,
            "proposed_sub_divisions_count": len(proposed_sub_divisions),
            # Patta transfers
            "patta_transfers_count": len(patta_transfers),
            "patta_transfers": [
                {"transfer_order_number": pt.transfer_order_number,
                 "new_patta_number": pt.new_patta_number, "status": pt.status,
                 "signed_by": pt.signed_by,
                 "transfer_date": pt.transfer_date.isoformat() if pt.transfer_date else None,
                 "signature_date": pt.tahsildar_signature_date.isoformat() if pt.tahsildar_signature_date else None,
                 "transfer_reason": pt.transfer_reason,
                 "transfer_type": pt.transfer_type,
                 "registration_place": pt.registration_place,
                 "registration_date": pt.registration_date.isoformat() if pt.registration_date else None,
                 "old_patta_number": pt.old_patta_number,
                 "order_number": pt.order_number or pt.transfer_order_number,
                 "order_date": pt.order_date.isoformat() if pt.order_date else None,
                 "order_remarks": pt.order_remarks,
                 "sis_recommendation": pt.sis_recommendation,
                 "sis_remarks": pt.sis_remarks,
                 "sis_recommendation_reason": pt.sis_recommendation_reason}
                for pt in patta_transfers
            ],
            # First non-null across the transfer(s) -- the registration /
            # transfer-order detail the SIS verifies a mutation against. Kept
            # None (not the submission date) when the file carries no transfer,
            # so "when was the deed registered?" is never answered with the
            # filing date.
            "transfer_date": next((pt.transfer_date.isoformat() for pt in patta_transfers if pt.transfer_date), None),
            "transfer_reason": next((pt.transfer_reason for pt in patta_transfers if pt.transfer_reason), None),
            "transfer_type": next((pt.transfer_type for pt in patta_transfers if pt.transfer_type), None),
            "registration_place": next((pt.registration_place for pt in patta_transfers if pt.registration_place), None),
            "registration_date": next((pt.registration_date.isoformat() for pt in patta_transfers if pt.registration_date), None),
            "old_patta_number": next((pt.old_patta_number for pt in patta_transfers if pt.old_patta_number), None),
            "transfer_order_number": next((pt.order_number or pt.transfer_order_number for pt in patta_transfers if (pt.order_number or pt.transfer_order_number)), None),
            "order_date": next((pt.order_date.isoformat() for pt in patta_transfers if pt.order_date), None),
            "order_remarks": next((pt.order_remarks for pt in patta_transfers if pt.order_remarks), None),
            "sis_recommendation": next((pt.sis_recommendation for pt in patta_transfers if pt.sis_recommendation), None),
            "sis_remarks": next((pt.sis_remarks for pt in patta_transfers if pt.sis_remarks), None),
            "sis_recommendation_reason": next((pt.sis_recommendation_reason for pt in patta_transfers if pt.sis_recommendation_reason), None),
            # Transfer parties (transferor -> transferee). Answers "who is the
            # new owner / previous owner of <app>", their relative name,
            # Aadhaar and gender -- from patta_transfers.previous_owner /
            # new_owner, the only owner identities this projection carries for a
            # patta transfer.
            "transfer_parties": transfer_parties,
            "new_owner": _first_new,
            "previous_owner": _first_prev,
            "new_owner_name": (_first_new or {}).get("name"),
            "previous_owner_name": (_first_prev or {}).get("name"),
            "new_patta_number": next(
                (pt.new_patta_number for pt in patta_transfers if pt.new_patta_number), None),
            "patta_signed_by": next(
                (pt.signed_by for pt in patta_transfers if pt.signed_by), None),
            "patta_signature_date": next(
                (pt.tahsildar_signature_date.isoformat() for pt in patta_transfers
                 if pt.tahsildar_signature_date), None),
            "survey_no": app.survey_number.survey_no if app.survey_number else "N/A",
            "survey_number": app.survey_number.survey_no if app.survey_number else "N/A",
            # Land classification of the parcel -- carried on the survey number,
            # not on the application row. Answers "what is the land type of X".
            "land_type": (app.survey_number.land_type
                          if app.survey_number and app.survey_number.land_type else None),
            "subdivision_number": ", ".join(sub_divisions_list) if sub_divisions_list else None,
            "current_subdivision_number": (proposed_sub_divisions[0]["proposed_sub_division_no"] if (proposed_sub_divisions and isinstance(proposed_sub_divisions[0], dict)) else (sub_divisions_list[0] if sub_divisions_list else None)),
            "temporary_subdivision_number": ", ".join(
                s["temporary_sub_division_no"] for s in proposed_sub_divisions
                if s.get("temporary_sub_division_no")) or None,
            "final_subdivision_number": ", ".join(
                s["final_sub_division_no"] for s in proposed_sub_divisions
                if s.get("final_sub_division_no")) or None,
            "patta_number": app.survey_number.patta_number if app.survey_number else None,
            "serial_number": int(app.application_number.split('/')[-1]) if '/' in app.application_number and app.application_number.split('/')[-1].isdigit() else None,
            "application_id": app.application_number,
            "user_id": app.assigned_officer.employee_id if app.assigned_officer else None,
            "role_id": app.assigned_officer.designation if app.assigned_officer else None,
            # "who is handling it?" is one of the plainest questions an officer
            # asks, and the record answered "I could not find that particular
            # detail" -- the assigned officer was loaded here and exposed only
            # as an employee_id under the name "User ID", which answers a
            # different question. The desk the file currently sits at is half
            # the answer, so it is carried alongside the person.
            "assigned_officer_name": (app.assigned_officer.name
                                      if app.assigned_officer else None),
            "assigned_officer": (
                f"{app.assigned_officer.name} "
                f"({app.assigned_officer.designation or 'SIS'}, "
                f"{app.assigned_officer.employee_id}) — currently at the "
                f"{app.current_stage or 'SIS'} desk"
                if app.assigned_officer else None),
            "service_code": app.application_number.split('/')[1] if '/' in app.application_number else ("0154" if app.application_type == "ISD" else "0153"),
            "district_code": district.district_code if (district and hasattr(district, 'district_code') and district.district_code) else (app.application_number.split('/')[2] if '/' in app.application_number else None),
            "taluk_code": taluk.taluk_code if (taluk and hasattr(taluk, 'taluk_code') and taluk.taluk_code) else None,
            # Urban jurisdiction: the extracts carry a town/urban-unit code, not a village code.
            "village_code": None,
            "urban_unit_code": town.town_code if town else None,
            # Codes are shown under "Ward Code" / "Block Code", so emit the stored code
            # verbatim ("002", "0015") — the human-readable names live in `jurisdiction`.
            "ward_code": ward.ward_number if ward else None,
            "block_code": block.block_number if block else None,
            "application_date": app.submission_date.isoformat(),
            "application_status": app.current_status,
            "workflow_state": app.current_stage,
            # Constant across the extract -- every projected row's
            # department_code is "01".
            "department_code": "01",
            "last_updated_datetime": last_updated_iso,
            # The date the application was approved or rejected. None while the
            # file is still open -- never fall back to the submission date.
            "decision_date": decision_date,
            "decision_stage": decision_stage,
            "decision_desk": decision_desk,
            "decision_by": decision_by,
            "approval_date": decision_date if app.current_status == "approved" else None,
            "rejection_date": decision_date if app.current_status == "rejected" else None,
            "source_code": app.submission_channel or None,
            "source_name": "Common Service Center (CSC)" if app.submission_channel == "CSC" else ("Citizen Portal" if app.submission_channel == "citizen" else ("Sub Registrar Office (IGRS)" if app.submission_channel == "sub_registrar" else None)),
            "survey_total_area_sqm": survey_total_area,
            "proposed_total_area_sqm": proposed_total_area,
            "area_sqm": proposed_total_area if proposed_total_area else survey_total_area,
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
    Handles subdivisions like "145/1A" by resolving base survey "145" and then
    narrowing the answer to that one sub-division.
    """
    try:
        base_survey_no, requested_sub = split_survey_reference(survey_no)

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
        rows = result.all()

        if not rows:
            return {"found": False, "message": f"Survey number {survey_no} not found or not accessible"}

        # A bare survey number is NOT unique -- 36 of them recur in more than one
        # ward (survey "35" sits in both ward 102 and ward 103). .first() picked
        # one at random and said nothing; order the matches and report the rest.
        rows = sorted(rows, key=lambda r: (r[2].ward_number or "", r[1].block_number or ""))
        survey, block, ward, town, taluk, district = rows[0]
        other_locations = [
            {"ward": r[2].ward_number, "block": r[1].block_number}
            for r in rows[1:]
        ]

        # Get sub-divisions
        subdiv_query = select(SubDivision).where(
            and_(
                SubDivision.survey_number_id == survey.id,
                SubDivision.status == "active"
            )
        ).order_by(SubDivision.sub_division_no)
        subdiv_result = await db.execute(subdiv_query)
        subdivisions = subdiv_result.scalars().all()

        # When the user named a sub-division ("1355/1B12"), answer about that one
        # -- not about all 93 sub-divisions of parcel 1355.
        sub_division_found = None
        if requested_sub:
            narrowed = [sd for sd in subdivisions
                        if subdivision_matches(sd.sub_division_no, survey.survey_no, requested_sub)]
            sub_division_found = bool(narrowed)
            if narrowed:
                subdivisions = narrowed

        return {
            "found": True,
            "survey_no": survey_no,
            "base_survey_no": survey.survey_no,
            "requested_sub_division": (f"{survey.survey_no}/{requested_sub}"
                                       if requested_sub else None),
            "sub_division_found": sub_division_found,
            "matched_parcels": len(rows),
            "other_locations": other_locations,
            "total_area_sqm": float(survey.total_area_sqm),
            "land_type": survey.land_type,
            "patta_number": survey.patta_number,
            "has_encroachment": survey.has_encroachment,
            "has_litigation": survey.has_litigation,
            "jurisdiction": {
                "district": district.name if district else "N/A",
                "taluk": taluk.name if taluk else "N/A",
                "town": town.name if town else "N/A",
                "ward": (ward.ward_name or f"Ward {ward.ward_number}") if ward else "N/A",
                "block": (block.block_name or f"Block {block.block_number}") if block else "N/A"
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
    Handles subdivisions like "145/1A" by resolving base survey "145" and then
    narrowing to that sub-division's owners.
    """
    try:
        base_survey_no, requested_sub = split_survey_reference(survey_no)

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
        rows = survey_result.all()

        if not rows:
            return {"found": False, "message": f"Survey number {survey_no} not found or not accessible"}

        # A bare survey number is NOT unique -- the same number recurs in
        # different wards (survey "35" exists in ward 102 and ward 103). Taking
        # .first() silently answered for one ward only. Resolve every matching
        # parcel, tag each owner with its ward/block, and report how many parcels
        # matched so the caller can flag the ambiguity.
        survey_ids = [r[0].id for r in rows]
        ward_by_survey_id = {
            r[0].id: {"ward": r[2].ward_name, "ward_no": r[2].ward_number,
                      "block": r[1].block_name, "block_no": r[1].block_number}
            for r in rows
        }
        matched_wards = sorted({
            f"{v['ward']} ({v['ward_no']})" for v in ward_by_survey_id.values()
        })

        # Get all ownerships (both survey-level and sub-division-level)
        from backend.models import SubDivision
        ownership_query = select(SurveyOwnership, Owner, SubDivision).join(
            Owner, SurveyOwnership.owner_id == Owner.id
        ).outerjoin(
            SubDivision, SurveyOwnership.sub_division_id == SubDivision.id
        ).where(
            SurveyOwnership.survey_number_id.in_(survey_ids)
        # Owner name breaks ties: ordering by sub-division alone leaves survey-level
        # owners (all NULL sub-division) in whatever order the scan returns, so the
        # same question answered twice listed the same owners in a different order.
        ).order_by(SubDivision.sub_division_no, Owner.name)

        result = await db.execute(ownership_query)
        ownerships = result.all()

        def _row(ownership, owner, subdivision):
            w = ward_by_survey_id.get(ownership.survey_number_id, {})
            return {
                "name": owner.name,
                "name_tamil": owner.name_tamil,
                # Owner columns carried across from the natham-chitta owner row
                # (uchitta_natham -> owners). "who is the father of
                # the owner of survey 5", "the owner's aadhaar / gender /
                # address" have real answers here; without these keys they fell
                # through to the LLM. `mobile` / `address` are blank for every
                # owner in this extract, but the key is present so the renderer
                # can say "not recorded" rather than ignore the question.
                "relative_name": owner.father_name,
                # s/o, w/o, d/o -- how relative_name relates to the owner
                # (uchitta_natham.relationship_code). None where the
                # extract left it as code 0.
                "relationship": owner.relationship_type,
                "aadhaar_last4": owner.aadhaar_last4,
                "gender": owner.gender,
                "mobile": owner.mobile,
                "address": owner.address,
                "sub_division": subdivision.sub_division_no if subdivision else "Survey Level",
                "ward": w.get("ward"),
                "ward_number": w.get("ward_no"),
                "block_number": w.get("block_no"),
                "ownership_share": float(ownership.ownership_share) if ownership.ownership_share else None,
                "ownership_type": ownership.ownership_type,
                "is_joint_owner": ownership.is_joint_owner
            }

        # Group by sub-division (None = survey-level). A survey-level owner holds
        # the whole parcel, so they stay in the answer even when one sub-division
        # was named. Matching is whitespace- and case-insensitive, so "35/2O"
        # finds the register's "35/2 O".
        owners_list = []
        for ownership, owner, subdivision in ownerships:
            sd_no = subdivision.sub_division_no if subdivision else None
            if requested_sub and sd_no and not subdivision_matches(
                    sd_no, base_survey_no, requested_sub):
                continue
            owners_list.append(_row(ownership, owner, subdivision))

        # Nothing carries that sub-division -- fall back to the whole parcel, but
        # say so rather than passing it off as the sub-division's ownership.
        sub_division_found = None
        if requested_sub:
            sub_division_found = any(
                r["sub_division"] != "Survey Level" for r in owners_list)
            if not owners_list and ownerships:
                owners_list = [_row(o, ow, sd) for o, ow, sd in ownerships]

        return {
            "found": True,
            "survey_no": survey_no,
            "base_survey_no": base_survey_no,
            "requested_sub_division": (f"{base_survey_no}/{requested_sub}"
                                       if requested_sub else None),
            "sub_division_found": sub_division_found,
            "owners": owners_list,
            "matched_parcels": len(survey_ids),
            "matched_wards": matched_wards,
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
            return {"count": 0, "visits": [], "message": "No jurisdiction assigned"}

        query = select(Application).options(
            selectinload(Application.survey_number),
            selectinload(Application.applicant)
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
                Application.application_type.in_(["ISD"]),
                Application.field_visit_scheduled == False,
                # No current_stage pin — the jurisdiction filter already scopes
                # the result. The stage pin caused 0 results whenever ISD files
                # moved past the SIS desk to DIS or Tahsildar.
                Application.current_status.in_(ACTIVE_STATUSES)
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
                    "is_overdue": app.is_overdue,
                    "applicant_name": app.applicant.name if app.applicant else "N/A",
                    "applicant_mobile": app.applicant.mobile if app.applicant else "N/A",
                    "applicant_address": app.applicant.address if app.applicant else "N/A"
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
    to_be_visited_only: bool = False,
    application_type: Optional[List[str]] = None,
    exclude_date_range: bool = False,
    application_number: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get field visits for the officer with optional status, date range (start_date to end_date),
    to-be-visited filtering, and application type filtering (supports multiple types via list).
    `application_number` narrows the result to one application — used when the officer
    asks about "its field visit" after an application has been confirmed in the
    conversation, so the answer is that visit rather than the whole jurisdiction list.
    If exclude_date_range=True the date filter is inverted: visits OUTSIDE [start_date, end_date]
    are returned (i.e. scheduled_date < start_date OR scheduled_date > end_date).
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
            joinedload(FieldVisit.application).joinedload(Application.survey_number).joinedload(SurveyNumber.block),
            joinedload(FieldVisit.application).joinedload(Application.survey_number).joinedload(SurveyNumber.sub_divisions),
            joinedload(FieldVisit.application).joinedload(Application.application_sub_divisions).joinedload(ApplicationSubDivision.sub_division),
            joinedload(FieldVisit.application).joinedload(Application.patta_transfers).joinedload(PattaTransfer.sub_division),
            joinedload(FieldVisit.application).joinedload(Application.applicant)
        ).join(
            Application, FieldVisit.application_id == Application.id
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
                # Jurisdiction-scoped (ward/block/taluk/town/district), like every
                # other listing in this register -- not a strict FieldVisit.officer_id
                # match. The live seed data (sample_db_new) assigns every one of its
                # 29 field visits to a single officer regardless of ward, the same
                # gap `get_officer_applications` had with `assigned_officer_id`: two
                # of the three seeded officers share ward 001 with that one officer
                # and got "No field visits scheduled" for a ward that genuinely has
                # them. A `FieldVisit.officer_id` column exists for a finer per-
                # officer assignment the data does not actually carry here; if a
                # future seed populates it correctly per ward-sharing officer, add
                # it back as a tie-breaker rather than the sole filter, so two
                # officers on one ward do not each claim the other's visit count.
                #
                # NO current-stage pin either. A field visit is a record of work
                # the officer DID; it does not stop being one when the file
                # moves on to the Tahsildar. Pinning to the SIS desk answered
                # "how many field visits do I have?" with 1 to an officer who
                # had 22, and the single surviving row read as a random
                # application. In the seeded data the pin was exactly
                # equivalent to "the visit is not completed" -- every
                # scheduled/unscheduled visit sits at SIS and every completed
                # one has left -- so it filtered out the officer's whole
                # history and bought nothing. Questions that really are about
                # the desk pass `to_be_visited_only`, which says so directly.
                Application.current_status != 'rejected',  # Exclude field visits for rejected applications
                or_(*jur_conditions) if jur_conditions else False
            )
        )
        
        if application_number:
            query = query.where(Application.application_number == application_number)

        if status_filter:
            query = query.where(FieldVisit.status == status_filter)

        if to_be_visited_only:
            query = query.where(FieldVisit.status.in_(["scheduled", "rescheduled", "pending", "overdue", "unscheduled"]))

        if exclude_date_range and start_date and end_date:
            # Return visits whose scheduled date falls OUTSIDE [start_date, end_date]
            from sqlalchemy import or_
            query = query.where(
                or_(
                    FieldVisit.scheduled_date < start_date,
                    FieldVisit.scheduled_date > end_date
                )
            )
        else:
            if start_date:
                query = query.where(FieldVisit.scheduled_date >= start_date)

            if end_date:
                query = query.where(FieldVisit.scheduled_date <= end_date)
            
        if application_type:
            # Support both single string and list of types
            types_list = application_type if isinstance(application_type, list) else [application_type]
            types_upper = [t.upper() for t in types_list]
            if len(types_upper) == 1:
                query = query.where(Application.application_type == types_upper[0])
            else:
                query = query.where(Application.application_type.in_(types_upper))

        query = query.order_by(FieldVisit.scheduled_date.asc())

        result = await db.execute(query)
        visits = result.scalars().unique().all()
        
        # ward / town / taluk / district of each visit's block, for "along with district"
        _block_ids = {v.application.survey_number.block_id for v in visits
                      if v.application and v.application.survey_number}
        _geo = {}
        if _block_ids:
            for bid, wno, tname, kname, dname in (await db.execute(
                    select(Block.id, Ward.ward_number, Town.name, Taluk.name, District.name)
                    .join(Ward, Block.ward_id == Ward.id).join(Town, Ward.town_id == Town.id)
                    .join(Taluk, Town.taluk_id == Taluk.id).join(District, Taluk.district_id == District.id)
                    .where(Block.id.in_(_block_ids)))).all():
                _geo[bid] = {"ward_number": wno, "town": tname, "taluk": kname, "district": dname}

        field_visits = []
        to_be_visited_count = 0
        completed_count = 0
        overdue_count = 0

        for visit in visits:
            app = visit.application
            applicant = app.applicant if app else None
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

            # Extract all sub-division numbers for this field visit
            subdiv_list = application_subdivision_list(app) if app else []

            base_survey_no = survey.survey_no if survey else "N/A"
            subdivisions_str = format_survey_with_subdivisions(base_survey_no, subdiv_list)

            field_visits.append({
                "application_number": app.application_number if app else "N/A",
                "survey_no": base_survey_no,
                "raw_survey_no": base_survey_no,
                "subdivisions": subdivisions_str,
                "sub_division_no": subdivisions_str,
                "block_number": block.block_number if block else None,
                **_geo.get(survey.block_id if survey else None, {}),
                "application_type": app.application_type if app else "N/A",
                "status": visit.status,
                "field_visit_date": visit.scheduled_date.isoformat() if visit.scheduled_date else None,
                "is_overdue": is_overdue,
                "applicant_name": applicant.name if applicant else "N/A",
                "applicant_mobile": applicant.mobile if applicant else "N/A",
                "applicant_address": applicant.address if applicant else "N/A"
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
    Handles subdivisions like "145/1A" by resolving base survey "145" -- the next
    number is always allocated on the parent parcel.
    """
    try:
        base_survey_no, _requested_sub = split_survey_reference(survey_no)

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
        rows = survey_result.all()

        if not rows:
            return {"found": False, "message": f"Survey number {survey_no} not found or not accessible"}

        # The same survey number recurs in more than one ward, and each parcel
        # runs its own sub-division sequence -- pick deterministically and report
        # the other parcels rather than allocating off an arbitrary one.
        rows = sorted(rows, key=lambda r: (r[2].ward_number or "", r[1].block_number or ""))
        survey = rows[0][0]
        other_locations = [
            {"ward": r[2].ward_number, "block": r[1].block_number}
            for r in rows[1:]
        ]

        # Get existing sub-divisions
        subdiv_query = select(SubDivision).where(
            SubDivision.survey_number_id == survey.id
        ).order_by(desc(SubDivision.sub_division_no))
        
        result = await db.execute(subdiv_query)
        subdivisions = result.scalars().all()
        
        # Always build the next number off the *resolved* base survey, otherwise an input
        # like "145/1A" would produce "145/1A/2".
        resolved_base = survey.survey_no

        highest_existing = "None"
        if not subdivisions:
            next_no = f"{resolved_base}/1"
        else:
            # Take the highest numeric prefix already in use and increment it.
            # Counting rows is wrong when the sequence has gaps (1, 3 -> would return 3),
            # and the string ORDER BY puts "9" above "10".
            highest_seq = 0
            highest_existing = subdivisions[0].sub_division_no
            for sd in subdivisions:
                _, tail = split_survey_reference(sd.sub_division_no)
                m = re.match(r'\d+', tail or "")
                if m and int(m.group(0)) > highest_seq:
                    highest_seq = int(m.group(0))
                    highest_existing = sd.sub_division_no
            next_no = f"{resolved_base}/{highest_seq + 1}"

        return {
            "found": True,
            "survey_no": survey_no,
            "base_survey_no": survey.survey_no,
            "matched_parcels": len(rows),
            "other_locations": other_locations,
            "existing_count": len(subdivisions),
            "highest_existing": highest_existing,
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
            block_key = block.block_name or f"Block {block.block_number}"
            
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
                "district": district.name if district else "N/A",
                "taluk": taluk.name if taluk else "N/A",
                "town": town.name if town else "N/A",
                "ward": ward.ward_name or f"Ward {ward.ward_number}",
                "ward_number": ward.ward_number
            },
            "total_surveys": len(rows),
            "surveys_by_block": surveys_by_block
        }
        
    except Exception as e:
        logger.error(f"Error getting ward surveys: {e}")
        return {"found": False, "error": str(e)}




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


ACTIVE_STATUSES = ("pending", "in_progress", "escalated")


async def check_existing_pending_application_for_survey(
    db: AsyncSession,
    survey_number_id: Any,
    survey_no: Optional[str] = None,
    ward_id: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Check whether an active application already blocks this parcel.

    Mutation on a survey number is a synchronous process: while one application
    on it is live, no other application may be filed -- and that holds across
    sub-divisions, because the whole parcel's record is being worked on. So the
    lock is keyed on the survey number, never on the sub-division.
    """
    try:
        query = select(Application).where(
            and_(
                Application.survey_number_id == survey_number_id,
                Application.current_status.in_(ACTIVE_STATUSES)
            )
        ).options(
            selectinload(Application.patta_transfers).selectinload(PattaTransfer.sub_division),
            selectinload(Application.application_sub_divisions).selectinload(ApplicationSubDivision.sub_division),
        ).order_by(Application.submission_date)
        res = await db.execute(query)
        blocking = res.scalars().all()

        if not blocking:
            # `Application` (ck_application_type) only ever admits 0153/0154 --
            # the lock itself is documented to hold across EVERY service code
            # ("mutation on a survey number is a synchronous process"), but
            # this only ever checked the ISD/NISD table, so a survey with a
            # genuinely pending 0158/0165/0178/... application (real, active
            # work on the same parcel) was reported "no active application,
            # a new one can be filed" -- the wrong answer, and the one kind of
            # wrong answer this lock exists to prevent. `service_register_entries`
            # carries survey_no + ward for every code; other service codes'
            # status is coarse (approved/rejected/pending only), so "pending"
            # is the active state there.
            if survey_no:
                reg_q = select(ServiceRegisterEntry).where(
                    and_(
                        ServiceRegisterEntry.survey_no == survey_no,
                        ServiceRegisterEntry.service_code.notin_(["0153", "0154"]),
                        ServiceRegisterEntry.status == "pending",
                    )
                )
                if ward_id:
                    reg_q = reg_q.where(ServiceRegisterEntry.ward_id == ward_id)
                reg_blocking = (await db.execute(reg_q)).scalars().all()
                if reg_blocking:
                    from backend.utils.helpers import SIS_URBAN_SERVICES as _SVC
                    first = reg_blocking[0]
                    _name = (_SVC.get(first.service_code, {}) or {}).get("name") or first.service_code
                    return {
                        "can_apply": False,
                        "blocking_applications": [],
                        "existing_application_number": first.application_number,
                        "status": first.status,
                        "stage": first.stage,
                        "register_only": True,
                        "message": (f"Application {first.application_number} (service {first.service_code}, "
                                    f"{_name}) is already pending on this survey number. Another application "
                                    f"cannot be submitted -- on any sub-division -- until it is approved or rejected."),
                    }
            return {"can_apply": True, "blocking_applications": [],
                    "message": "No active application for this survey number."}

        rows = []
        for app in blocking:
            subdivs = application_subdivision_list(app)
            rows.append({
                "application_number": app.application_number,
                "type": app.application_type,
                "status": app.current_status,
                "stage": app.current_stage,
                "submission_date": app.submission_date.isoformat() if app.submission_date else None,
                "sub_divisions": subdivs,
            })
        first = rows[0]
        return {
            "can_apply": False,
            "blocking_applications": rows,
            # kept for callers that only want the first blocker
            "existing_application_number": first["application_number"],
            "status": first["status"],
            "stage": first["stage"],
            "message": (f"Application {first['application_number']} is already active on this "
                        f"survey number. Another application cannot be submitted -- on any "
                        f"sub-division -- until it is approved or rejected."),
        }
    except Exception as e:
        logger.error(f"Error checking pending application for survey: {e}")
        return {"can_apply": True, "error": str(e)}


async def check_survey_application_lock(
    db: AsyncSession,
    survey_no: str,
    officer: OfficerContext = None
) -> Dict[str, Any]:
    """Answer "can another application be filed on this survey number?" from a
    survey reference the officer typed ("5", "5/4A", "1355/1B12").

    The sub-division in the reference is echoed back but never narrows the
    check: the lock is held by the survey number as a whole.
    """
    try:
        base_survey_no, requested_sub = split_survey_reference(survey_no)

        query = select(SurveyNumber, Block, Ward).join(
            Block, SurveyNumber.block_id == Block.id
        ).join(
            Ward, Block.ward_id == Ward.id
        ).where(
            or_(SurveyNumber.survey_no == survey_no,
                SurveyNumber.survey_no == base_survey_no)
        )
        if officer:
            jurisdiction_filters = await get_jurisdiction_filter(db, officer)
            if jurisdiction_filters:
                query = query.where(or_(*jurisdiction_filters))

        rows = (await db.execute(query)).all()
        if not rows:
            return {"found": False,
                    "message": f"Survey number {survey_no} not found or not accessible"}

        # The same survey number recurs in more than one ward, and each parcel
        # locks separately -- so every matching parcel has to be checked, not
        # just one. Checking only the alphabetically-first ward (as this used
        # to) answered "yes, you can apply" for survey 5 from its ward-102
        # parcel while its ward-103 parcel had three active applications
        # blocking it: a real "no" reported as a "yes". A future taluk- or
        # district-level officer (today's three test officers are all
        # ward-level, so this was invisible to them) would see it as soon as
        # the same survey number recurred inside their own jurisdiction.
        rows = sorted(rows, key=lambda r: (r[2].ward_number or "", r[1].block_number or ""))

        per_location = []
        locked = None
        for r_survey, r_block, r_ward in rows:
            loc_result = await check_existing_pending_application_for_survey(
                db, r_survey.id, survey_no=r_survey.survey_no, ward_id=r_ward.id)
            per_location.append({
                "survey": r_survey, "block": r_block, "ward": r_ward,
                "result": loc_result,
            })
            if not loc_result.get("can_apply", True) and locked is None:
                locked = per_location[-1]

        primary = locked or per_location[0]
        survey, block, ward = primary["survey"], primary["block"], primary["ward"]
        result = primary["result"]
        result.update({
            "found": True,
            "survey_no": survey_no,
            "base_survey_no": survey.survey_no,
            "requested_sub_division": (f"{survey.survey_no}/{requested_sub}"
                                       if requested_sub else None),
            "ward": ward.ward_number,
            "block": block.block_number,
            "matched_parcels": len(rows),
            "other_locations": [
                {"ward": p["ward"].ward_number, "block": p["block"].block_number,
                 "can_apply": p["result"].get("can_apply", True)}
                for p in per_location if p is not primary
            ],
        })
        return result
    except Exception as e:
        logger.error(f"Error checking survey application lock: {e}")
        return {"found": False, "error": str(e)}


async def get_digital_signature_details(
    db: AsyncSession,
    officer: OfficerContext,
    patta_number: Optional[str] = None,
    survey_number: Optional[str] = None,
    subdivision_number: Optional[str] = None,
    signed_by: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Query digital signature details from CSV data stored in database.
    
    NOTE: Digital signatures in TAMILNILAM extracts are JSON metadata only,
    not cryptographic PKCS#7 signatures. The nic_digital_signature column exists
    but is currently unpopulated.
    
    Parameters:
    - patta_number: Filter by specific patta number
    - survey_number: Filter by survey number
    - subdivision_number: Filter by subdivision number
    - signed_by: Filter by officer username (e.g., 'tut_ramyadevi')
    - start_date: Filter signatures created on or after this date
    - end_date: Filter signatures created on or before this date
    """
    try:
        # Digital signature data is in the CSV extracts, not ORM models yet
        # For now, return explanation that this is a planned feature
        result = {
            "found": False,
            "digital_signature_status": "not_implemented",
            "explanation": (
                "Digital signature data exists in the TAMILNILAM Urban Revenue extracts "
                "(uaregmap_ds.csv and uchitta_nathammap_ds.csv) but is not yet "
                "loaded into the application's PostgreSQL database. "
                "\n\n"
                "Available data in extracts:\n"
                "- 1,036 parcel signature records (uaregmap_ds.csv)\n"
                "- 439 owner signature records (uchitta_nathammap_ds.csv)\n"
                "- Fields: signed_datetime, signed_by_username, signature_content (JSON metadata)\n"
                "- Note: nic_digital_signature column exists but is empty (awaiting actual PKCS#7 signatures)\n"
                "\n\n"
                "To enable this feature, the CSV signature data needs to be ingested into "
                "the database with appropriate ORM models for patta_signature and natham_signature tables."
            ),
            "query_type": "Digital Signature Query",
            "filters_requested": {
                "patta_number": patta_number,
                "survey_number": survey_number,
                "subdivision_number": subdivision_number,
                "signed_by": signed_by,
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None,
            },
            "recommendation": (
                "This feature can be implemented by:\n"
                "1. Creating PattaSignature and NathamSignature ORM models\n"
                "2. Adding ingest logic to seed.py to load signature CSV data\n"
                "3. Querying the new tables here in postgres.py\n"
                "4. Adding digital_signature_check intent to rag.py"
            )
        }
        
        logger.info(f"Digital signature query requested: patta={patta_number}, survey={survey_number}, signed_by={signed_by}")
        return result
        
    except Exception as e:
        logger.error(f"Error querying digital signature details: {e}")
        return {
            "found": False,
            "error": str(e),
            "query_type": "Digital Signature Query"
        }


async def get_fee_summary(
    db: AsyncSession,
    officer: OfficerContext,
    application_type: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Aggregate the fee record (fee_amount / payment_mode) over the applications
    in the officer's jurisdiction.

    Unlike the listing queries this does NOT drop rejected applications: the
    fee is paid at submission, so money that came in on a file that was later
    rejected was still collected, and leaving it out makes the total disagree
    with the challan register. The rejected share is reported separately.

    Only part of the extract carries a fee record; `without_fee` counts the
    applications where `fee_amount` is NULL so the total is never read as
    "every application paid".
    """
    try:
        jurisdiction_filters = await get_jurisdiction_filter(db, officer)
        if not jurisdiction_filters:
            return {
                "fee_summary": {"total_applications": 0, "with_fee": 0},
                "query_type": "Fee Collection Summary",
                "message": "No jurisdiction assigned",
            }

        conditions = [Application.id.in_(_application_jurisdiction_subquery(jurisdiction_filters))]
        if application_type:
            conditions.append(Application.application_type == application_type.upper())
        if start_date:
            conditions.append(Application.submission_date >= start_date)
        if end_date:
            conditions.append(Application.submission_date <= end_date)

        totals = (await db.execute(
            select(
                func.count(Application.id),
                func.count(Application.fee_amount),
                func.coalesce(func.sum(Application.fee_amount), 0),
                func.min(Application.fee_amount),
                func.max(Application.fee_amount),
            ).where(and_(*conditions))
        )).one()
        total_apps, with_fee, total_fee, min_fee, max_fee = totals

        rejected_fee = (await db.execute(
            select(func.coalesce(func.sum(Application.fee_amount), 0))
            .where(and_(*conditions, Application.current_status == "rejected"))
        )).scalar()

        by_type = [
            {
                "application_type": row[0],
                "applications": row[1],
                "with_fee": row[2],
                "total_fee": float(row[3] or 0),
            }
            for row in (await db.execute(
                select(
                    Application.application_type,
                    func.count(Application.id),
                    func.count(Application.fee_amount),
                    func.coalesce(func.sum(Application.fee_amount), 0),
                )
                .where(and_(*conditions))
                .group_by(Application.application_type)
                .order_by(Application.application_type)
            ))
        ]

        by_mode = [
            {
                "payment_mode": row[0] or "not recorded",
                "applications": row[1],
                "total_fee": float(row[2] or 0),
            }
            for row in (await db.execute(
                select(
                    Application.payment_mode,
                    func.count(Application.id),
                    func.coalesce(func.sum(Application.fee_amount), 0),
                )
                .where(and_(*conditions))
                .group_by(Application.payment_mode)
                .order_by(desc(func.count(Application.id)))
            ))
        ]

        with_challan = (await db.execute(
            select(func.count(Application.challan_number)).where(and_(*conditions))
        )).scalar()

        # Fee is recorded on ISD/NISD files only. The applications under the
        # other service codes live in the register, which holds no fee, so they
        # can neither be summed nor called "free" -- they are counted so the
        # answer can say how much of the officer's work this total does not cover.
        other_codes = 0
        if not application_type:
            _o = [ServiceRegisterEntry.ward_id.in_(officer.jurisdiction_ids or []),
                  ServiceRegisterEntry.service_code.notin_(["0153", "0154"])]
            if start_date:
                _o.append(ServiceRegisterEntry.submission_date >= start_date)
            if end_date:
                _o.append(ServiceRegisterEntry.submission_date <= end_date)
            other_codes = await db.scalar(
                select(func.count()).select_from(ServiceRegisterEntry).where(and_(*_o))) or 0

        return {
            "fee_summary": {
                "other_service_code_applications": other_codes,
                "total_applications": total_apps or 0,
                "with_fee": with_fee or 0,
                "without_fee": (total_apps or 0) - (with_fee or 0),
                "with_challan": with_challan or 0,
                "total_fee": float(total_fee or 0),
                "min_fee": float(min_fee) if min_fee is not None else None,
                "max_fee": float(max_fee) if max_fee is not None else None,
                "rejected_fee": float(rejected_fee or 0),
                "by_type": by_type,
                "by_payment_mode": by_mode,
                "application_type": application_type.upper() if application_type else None,
                "start_date": str(start_date) if start_date else None,
                "end_date": str(end_date) if end_date else None,
            },
            "query_type": "Fee Collection Summary",
        }
    except Exception as e:
        logger.error(f"Error getting fee summary: {e}")
        return {"fee_summary": {"total_applications": 0, "with_fee": 0}, "error": str(e),
                "query_type": "Fee Collection Summary"}


async def get_recent_fees(
    db: AsyncSession,
    officer: OfficerContext,
    application_type: Optional[str] = None,
    channel: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """The fee actually recorded on the newest applications of a type / channel.

    Fees are revised, so "what is the fee for ISD" is answered from the newest
    files that carry a fee record, never from a fixed schedule. Rejected files
    are kept (the fee is paid at submission). `newer_without_fee` counts files
    filed after the newest fee-bearing one, so an empty recent record is said
    rather than silently skipped.
    """
    try:
        jurisdiction_filters = await get_jurisdiction_filter(db, officer)
        result = {"application_type": application_type.upper() if application_type else None,
                  "channel": channel, "latest": [], "total_with_fee": 0,
                  "newer_without_fee": 0, "total_applications": 0}
        if not jurisdiction_filters:
            return {"fee_lookup": result, "query_type": "Recorded Fee"}

        conditions = [Application.id.in_(_application_jurisdiction_subquery(jurisdiction_filters))]
        if application_type:
            conditions.append(Application.application_type == application_type.upper())
        if channel:
            conditions.append(Application.submission_channel == channel)

        result["total_applications"] = (await db.execute(
            select(func.count(Application.id)).where(and_(*conditions)))).scalar() or 0
        with_fee = and_(*conditions, Application.fee_amount.is_not(None))
        result["total_with_fee"] = (await db.execute(
            select(func.count(Application.id)).where(with_fee))).scalar() or 0

        rows = (await db.execute(
            select(Application.application_number, Application.application_type,
                   Application.submission_channel, Application.submission_date,
                   Application.fee_amount, Application.payment_mode)
            .where(with_fee)
            .order_by(desc(Application.submission_date), desc(Application.application_number))
            .limit(limit)
        )).all()
        result["latest"] = [
            {"application_number": r[0], "application_type": r[1], "channel": r[2],
             "submission_date": str(r[3])[:10] if r[3] else None,
             "fee_amount": float(r[4]), "payment_mode": r[5]}
            for r in rows
        ]
        if rows and rows[0][3] is not None:
            result["newer_without_fee"] = (await db.execute(
                select(func.count(Application.id)).where(
                    and_(*conditions, Application.fee_amount.is_(None),
                         Application.submission_date > rows[0][3]))
            )).scalar() or 0
        elif not rows:
            result["newer_without_fee"] = result["total_applications"]
        return {"fee_lookup": result, "query_type": "Recorded Fee"}
    except Exception as e:
        logger.error(f"Error getting recent fees: {e}")
        return {"fee_lookup": {"latest": [], "total_with_fee": 0, "total_applications": 0,
                               "newer_without_fee": 0}, "error": str(e),
                "query_type": "Recorded Fee"}


async def get_ward_directory(
    db: AsyncSession,
    officer: OfficerContext,
    ward_number: Optional[str] = None,
) -> Dict[str, Any]:
    """Who holds which ward, and how many wards/blocks the town has.

    This is directory information -- the shape of the office and who to ask --
    not another ward's caseload. It carries no application, applicant, survey or
    owner data, so a ward officer may see it for wards other than their own,
    exactly as they would read a posting list on the wall.
    """
    try:
        rows = (await db.execute(
            select(Ward, Block, Town)
            .join(Block, Block.ward_id == Ward.id)
            .join(Town, Ward.town_id == Town.id)
            .order_by(Ward.ward_number, Block.block_number)
        )).all()

        holders: Dict[Any, list] = {}
        for jur, off in (await db.execute(
            select(OfficerJurisdiction, SISOfficer)
            .join(SISOfficer, OfficerJurisdiction.officer_id == SISOfficer.id)
            .where(SISOfficer.is_active == True)  # noqa: E712 - SQL boolean
        )).all():
            if jur.ward_id:
                names = holders.setdefault(jur.ward_id, [])
                entry = {"name": off.name, "designation": off.designation,
                         "employee_id": off.employee_id}
                if entry not in names:
                    names.append(entry)

        wards: Dict[Any, Dict[str, Any]] = {}
        town_name = None
        for ward, block, town in rows:
            town_name = town_name or (town.name if town else None)
            entry = wards.setdefault(ward.id, {
                "ward_number": ward.ward_number,
                "ward_name": ward.ward_name,
                "town": town.name if town else None,
                "blocks": [],
                "officers": holders.get(ward.id, []),
            })
            if block.block_number and block.block_number not in entry["blocks"]:
                entry["blocks"].append(block.block_number)

        ward_list = sorted(wards.values(), key=lambda w: str(w["ward_number"] or ""))
        if ward_number:
            ward_list = [w for w in ward_list
                         if str(w["ward_number"] or "").lstrip("0") == str(ward_number).lstrip("0")]

        return {
            "found": bool(ward_list),
            "town": town_name,
            "asked_ward": ward_number,
            "wards": ward_list,
            "total_wards": len(wards),
            "total_blocks": sum(len(w["blocks"]) for w in wards.values()),
            "own_wards": [w for w in (
                [str(x) for x in (await _officer_ward_numbers(db, officer))])],
        }
    except Exception as e:
        logger.error(f"Error building the ward directory: {e}")
        return {"found": False, "error": str(e)}


async def _officer_ward_numbers(db: AsyncSession, officer: OfficerContext) -> list:
    """The ward numbers this officer holds -- used only to mark "yours" in the directory."""
    try:
        rows = (await db.execute(
            select(Ward.ward_number)
            .join(OfficerJurisdiction, OfficerJurisdiction.ward_id == Ward.id)
            .where(OfficerJurisdiction.officer_id == officer.officer_id)
        )).scalars().all()
        return [r for r in rows if r]
    except Exception:  # pragma: no cover
        return []


async def get_applications_by_block(
    db: AsyncSession,
    officer: OfficerContext,
    group_by: str = "block",
) -> Dict[str, Any]:
    """Applications per block (or per ward) inside the officer's jurisdiction.

    "Which block has the most applications?" is a question about the shape of
    the queue, not a request for the queue itself -- answering it with the list
    of applications leaves the officer to count the rows.

    Counts are broken out by status so the answer can say what the total is made
    of; `rejected` is reported separately and never folded into the total, for
    the same reason the listing queries exclude it.
    """
    try:
        # Counted from the register, across every service code and over the
        # officer's whole jurisdiction -- the same basis as the listings. This
        # used to count only the ISD/NISD files assigned to the officer, so the
        # per-block totals summed to 38 while the listing showed 152.
        rows = (await db.execute(
            select(
                Ward.ward_number,
                Block.block_number,
                ServiceRegisterEntry.status,
                func.count(ServiceRegisterEntry.id),
            )
            .select_from(ServiceRegisterEntry)
            .join(Block, ServiceRegisterEntry.block_id == Block.id)
            .join(Ward, ServiceRegisterEntry.ward_id == Ward.id)
            .where(ServiceRegisterEntry.ward_id.in_(officer.jurisdiction_ids or []))
            .group_by(Ward.ward_number, Block.block_number, ServiceRegisterEntry.status)
        )).all()
    except Exception as e:
        logger.error(f"Error grouping applications by {group_by}: {e}")
        return {"count": 0, "groups": [], "error": str(e)}

    buckets: Dict[Any, Dict[str, Any]] = {}
    for ward_no, block_no, status, n in rows:
        key = (ward_no, block_no) if group_by == "block" else (ward_no,)
        entry = buckets.setdefault(key, {
            "ward": ward_no, "block": block_no if group_by == "block" else None,
            "total": 0, "open": 0, "approved": 0, "rejected": 0,
        })
        entry[status if status in ("approved", "rejected") else "open"] += n
        if status != "rejected":
            entry["total"] += n

    groups = sorted(buckets.values(), key=lambda g: (-g["total"], str(g["block"] or ""), str(g["ward"] or "")))
    return {
        "count": len(groups),
        "groups": groups,
        "group_by": group_by,
        "jurisdiction_type": officer.jurisdiction_type,
    }


async def get_applications_by_numbers(
    db: AsyncSession,
    officer: OfficerContext,
    application_numbers: List[str],
    status: Optional[str] = None,
    application_type: Optional[str] = None,
    include_rejected: bool = False,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Re-read an explicit set of applications the officer was just shown.

    This is what a follow-up about "them" is answered from. "Show my pending
    ISD applications" then "which one is oldest?" must compare only the rows
    that were on screen -- re-running the original listing would silently pick
    up anything filed since, and running an unscoped query would answer about
    the whole register. So the follow-up carries the application numbers
    forward and they are read back here.

    Carrying numbers forward is not a way around the access rules. The numbers
    came out of this officer's own session, and every one of them is re-checked
    against `get_jurisdiction_filter()` here rather than trusted: a number that
    is no longer inside the officer's jurisdiction is dropped from the result
    and counted in `dropped`, never returned. `status` / `application_type`
    narrow the set further, for "how many of them are approved" and "show only
    the NISD ones".

    Rejected applications stay out unless `status` asks for them, the same
    standing rule the other listings follow -- UNLESS `include_rejected` is
    set, for the one case that rule doesn't fit: the carried list came from
    an unscoped "show all applications" that legitimately included rejected
    rows in the first place. A field-projection follow-up ("show along
    district") re-reading that same set with the default rule silently
    dropped every rejected row that had been on screen -- 70 became 54 for
    no reason the officer asked for. The caller decides this from whether
    the ORIGINAL context carried a status filter of its own.
    """
    numbers = [str(n).strip().upper() for n in (application_numbers or []) if str(n).strip()]
    if not officer or not officer.officer_id:
        return {"count": 0, "applications": [], "error": "Invalid officer context"}
    if not numbers:
        return {"count": 0, "applications": [], "requested": 0, "dropped": 0}

    try:
        from sqlalchemy.orm import selectinload

        jurisdiction_filters = await get_jurisdiction_filter(db, officer)
        if not jurisdiction_filters:
            return {"count": 0, "applications": [], "requested": len(numbers),
                    "dropped": len(numbers), "error": "No jurisdiction assigned"}

        query = (
            select(Application)
            .options(
                selectinload(Application.applicant),
                selectinload(Application.survey_number)
                .selectinload(SurveyNumber.block)
                .selectinload(Block.ward)
                .selectinload(Ward.town)
                .selectinload(Town.taluk)
                .selectinload(Taluk.district),
                selectinload(Application.application_sub_divisions)
                .selectinload(ApplicationSubDivision.sub_division),
                # application_subdivision_list() falls back to the patta
                # transfer row for a non-ISD file; without this it lazy-loads
                # and the whole query dies under asyncio.
                selectinload(Application.patta_transfers)
                .selectinload(PattaTransfer.sub_division),
            )
            .where(Application.application_number.in_(numbers))
            .where(Application.id.in_(_application_jurisdiction_subquery(jurisdiction_filters)))
        )
        if status:
            query = query.where(Application.current_status == status)
        elif not include_rejected:
            # The standing rule: a rejected file is not part of an operational
            # list unless it was asked for by name.
            query = query.where(Application.current_status != "rejected")
        if application_type:
            query = query.where(Application.application_type == application_type)

        rows = (await db.execute(query)).scalars().unique().all()

        # The date a file was decided is the `performed_at` of the workflow hop
        # that closed it -- `applications` carries no decision-date column (see
        # CLAUDE.md, "When a completed application was decided"). It is read
        # here so a follow-up like "which took the longest?" can be answered
        # from the rows on screen instead of falling to the model, which
        # answered 14 days where the real longest was 63.
        _decided: Dict[Any, Any] = {}
        if rows:
            _ids = [a.id for a in rows]
            _dec = await db.execute(
                select(WorkflowHistory.application_id,
                       func.max(WorkflowHistory.performed_at))
                .where(WorkflowHistory.application_id.in_(_ids),
                       WorkflowHistory.to_stage.in_(["COMPLETED", "REJECTED"]))
                .group_by(WorkflowHistory.application_id))
            _decided = {aid: ts for aid, ts in _dec.all()}

        # "when were they last updated" over this carried list -- same rule
        # as the single-application card and get_officer_applications(): the
        # most recent hop of ANY kind (not just a closing one), converted from
        # the stored UTC to IST for display.
        _last_updated: Dict[Any, Any] = {}
        if rows:
            _lu_q = await db.execute(
                select(WorkflowHistory.application_id, func.max(WorkflowHistory.performed_at))
                .where(WorkflowHistory.application_id.in_(_ids))
                .group_by(WorkflowHistory.application_id))
            _last_updated = {aid: ts for aid, ts in _lu_q.all()}

        # The field-visit state of each file, from the visits themselves -- not the
        # Application.field_visit_scheduled flag -- so "which one is not scheduled?"
        # is answered from the same rows the unscheduled-visit list is.
        _fv_state: Dict[Any, str] = {}
        if rows:
            for _aid, _st in (await db.execute(
                    select(FieldVisit.application_id, FieldVisit.status)
                    .where(FieldVisit.application_id.in_([a.id for a in rows])))).all():
                _prev = _fv_state.get(_aid)
                _rank = {"scheduled": 3, "unscheduled": 2, "completed": 1}
                if _prev is None or _rank.get(_st, 0) > _rank.get(_prev, 0):
                    _fv_state[_aid] = _st

        # Preserve the order the officer saw, so "the second one" still means
        # the second row of the answer above.
        position = {n: i for i, n in enumerate(numbers)}
        rows = sorted(rows, key=lambda a: position.get((a.application_number or "").upper(), 10**6))

        app_rows: List[Dict[str, Any]] = []
        for app in rows:
            survey = app.survey_number
            block = survey.block if survey else None
            ward = block.ward if block else None
            town = ward.town if ward else None
            taluk = town.taluk if town else None
            district = taluk.district if taluk else None
            applicant = app.applicant
            subdiv_list = application_subdivision_list(app)
            base_survey_no = survey.survey_no if survey else "N/A"
            app_rows.append({
                "application_number": app.application_number,
                "type": app.application_type,
                "status": app.current_status,
                "stage": app.current_stage,
                "submission_date": app.submission_date.isoformat() if app.submission_date else None,
                "last_updated_date": (
                    (_last_updated.get(app.id) or app.updated_at).astimezone(IST).strftime("%Y-%m-%d %H:%M")
                    if (_last_updated.get(app.id) or app.updated_at) else None),
                "is_overdue": app.is_overdue,
                "priority_flag": app.priority_flag,
                "field_visit_status": _fv_state.get(app.id),
                "submission_channel": app.submission_channel,
                "can_number": app.can_number,
                "igrs_form6_number": app.igrs_form6_number,
                "fee_amount": float(app.fee_amount) if app.fee_amount is not None else None,
                "payment_mode": app.payment_mode,
                "survey_no": base_survey_no,
                "raw_survey_no": base_survey_no,
                "subdivisions": ", ".join(subdiv_list) or "-",
                "sub_division_no": ", ".join(subdiv_list) or "-",
                "district_name": (district.name if district else None) or _district_name_from_app_number(app.application_number) or "N/A",
                "taluk_name":    taluk.name if taluk else "N/A",
                "town_name":     town.name  if town  else "N/A",
                "ward_number":   ward.ward_number   if ward  else "N/A",
                "block_number":  block.block_number if block else "N/A",
                "applicant_name": applicant.name if applicant else "N/A",
                "applicant_mobile": applicant.mobile if applicant else "N/A",
                "applicant_address": applicant.address if applicant else "N/A",
                "included_subdivisions": ", ".join(subdiv_list) or "-",
            })
            _dec_ts = _decided.get(app.id)
            app_rows[-1]["decision_date"] = (
                _dec_ts.date().isoformat() if _dec_ts else None)
            app_rows[-1]["days_to_decide"] = (
                (_dec_ts.date() - app.submission_date).days
                if _dec_ts and app.submission_date else None)

        # Applications under the other service codes (0158, 0159, 0178, ...)
        # live only in the flat register, not in `applications`. A list that
        # spanned every service code carries their numbers too, so they are read
        # back here -- otherwise a follow-up ("how many of them are approved?")
        # would answer over the ISD/NISD subset while the officer was looking at
        # the whole list. Fields the register does not hold are left empty, never
        # invented, and the caller already reports how many rows carry each one.
        _have = {(r["application_number"] or "").upper() for r in app_rows}
        _missing = [n for n in numbers if n not in _have]
        if _missing and not application_type:
            # appl_log_urban.update_dt is the same "last updated" column the
            # single-application register-only card already reads
            # (othercode_detail.card()); it was never projected into this list
            # path, so "last_updated_date" was hardcoded None below and any
            # "when was it last updated" question on one of these files reported
            # no update time, not because the register has none, but because
            # this function never asked for it.
            from sqlalchemy import text as _sqltext
            _upd_rows = (await db.execute(
                _sqltext("SELECT appl_id, update_dt FROM appl_log_urban WHERE appl_id = ANY(:nums)"),
                {"nums": _missing})).all()
            # raw text() query, no ORM type processing: update_dt comes back as
            # whatever string the column holds, not a datetime object.
            _last_updated_reg = {n: str(d)[:16] for n, d in _upd_rows if d}
            _rq = (select(ServiceRegisterEntry)
                   .options(selectinload(ServiceRegisterEntry.ward)
                            .selectinload(Ward.town).selectinload(Town.taluk).selectinload(Taluk.district),
                            selectinload(ServiceRegisterEntry.block))
                   .where(ServiceRegisterEntry.application_number.in_(_missing))
                   .where(ServiceRegisterEntry.ward_id.in_(officer.jurisdiction_ids or [])))
            if status:
                _rq = _rq.where(ServiceRegisterEntry.status == status)
            elif not include_rejected:
                _rq = _rq.where(ServiceRegisterEntry.status != "rejected")
            from backend.utils.helpers import SIS_URBAN_SERVICES as _SVC
            for r in (await db.execute(_rq)).scalars().all():
                _nm = (_SVC.get(r.service_code, {}) or {}).get("name") or r.service_code
                _town = r.ward.town if r.ward else None
                _taluk = _town.taluk if _town else None
                _district = _taluk.district if _taluk else None
                app_rows.append({
                    "application_number": r.application_number,
                    "type": _nm,
                    "service_code": r.service_code,
                    "register_only": True,
                    "status": r.status,
                    "stage": r.stage,
                    "submission_date": r.submission_date.isoformat() if r.submission_date else None,
                    "last_updated_date": _last_updated_reg.get(r.application_number),
                    "is_overdue": None,
                    "priority_flag": None,
                    "field_visit_status": None,
                    "submission_channel": r.submission_channel,
                    "submission_ip": r.submission_ip,
                    "can_number": None,
                    "igrs_form6_number": None,
                    "fee_amount": None,
                    "payment_mode": None,
                    "survey_no": r.survey_no or "N/A",
                    "raw_survey_no": r.survey_no or "N/A",
                    "subdivisions": r.subdivision_no or "-",
                    "sub_division_no": r.subdivision_no or "-",
                    "district_name": (_district.name if _district else None)
                                      or _district_name_from_app_number(r.application_number) or "N/A",
                    "taluk_name": (_taluk.name if _taluk else None) or "N/A",
                    "town_name": (_town.name if _town else None) or "N/A",
                    "ward_number": r.ward.ward_number if r.ward else "N/A",
                    "block_number": r.block.block_number if r.block else "N/A",
                    "applicant_name": "N/A",
                    "applicant_mobile": "N/A",
                    "applicant_address": "N/A",
                    "included_subdivisions": r.subdivision_no or "-",
                    "decision_date": None,
                    "days_to_decide": None,
                })
            # keep the order the officer saw, as for the rows above
            app_rows.sort(key=lambda r: position.get((r["application_number"] or "").upper(), 10**6))

        if sort_by:
            _key = {"application_number": "application_number", "status": "status",
                    "application_type": "type", "ward_number": "ward_number",
                    "block_number": "block_number", "survey_no": "survey_no",
                    "fee_amount": "fee_amount", "applicant_name": "applicant_name",
                    }.get(sort_by, "submission_date")
            _desc = (sort_dir or "asc").lower() == "desc"
            if sort_by == "priority":
                app_rows.sort(key=lambda r: (bool(r.get("priority_flag")), bool(r.get("is_overdue"))), reverse=_desc)
            else:
                def _sk(r):
                    v = r.get(_key)
                    if isinstance(v, (int, float)):
                        return (0, v, "", r["application_number"])
                    v = str(v or "")
                    return (1, 0, (v.zfill(8) if _key == "survey_no" else v.lower()), r["application_number"])
                app_rows.sort(key=_sk, reverse=_desc)

        # A carried list is re-queried here with whatever status mix it already
        # had -- often mostly "approved" once a file has left the officer's
        # desk. The table renderer falls back to "Pending Applications" when
        # nothing names the list, which then asserts a status most of the rows
        # don't have. Name it from what was actually asked for, else from what
        # came back.
        if status:
            _query_type = f"{status.capitalize()} Applications"
        elif application_type:
            _query_type = f"{application_type} Applications"
        else:
            _statuses = {r["status"] for r in app_rows if r.get("status")}
            _query_type = (f"{_statuses.pop().capitalize()} Applications"
                            if len(_statuses) == 1 else "Applications")

        return {
            "count": len(app_rows),
            "applications": app_rows,
            "requested": len(numbers),
            "dropped": len(numbers) - len(app_rows),
            "jurisdiction_type": officer.jurisdiction_type,
            "query_type": _query_type,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        }
    except Exception as e:
        logger.error(f"Error reading applications by number: {e}")
        return {"count": 0, "applications": [], "error": str(e)}


async def get_last_application(
    db: AsyncSession,
    officer: OfficerContext,
    status: Optional[str] = None,
    application_type: Optional[str] = None,
    ward_number: Optional[str] = None,
    block_number: Optional[str] = None,
) -> Dict[str, Any]:
    """The officer's most recent application, optionally restricted to a status.

    "My previous application" means the file most recently acted on, not the one
    most recently submitted: a 2022 application decided last week is more recent
    to the officer than a 2026 application still sitting untouched. Recency is
    therefore the latest `workflow_history.performed_at` for the application,
    falling back to its submission date when it carries no workflow rows.

    `status` ("approved" / "rejected" / ...) and `application_type`
    ("ISD" / "NISD") narrow the search; with neither, the single most
    recent application of any kind is returned. The result is the full
    `get_application_detail` payload plus `last_action_at` and the filters that
    produced it, so follow-up questions about that application have everything
    they need.
    """
    if not officer or not officer.officer_id:
        logger.error("Invalid officer context provided to get_last_application")
        return {"found": False, "error": "Invalid officer context"}

    try:
        where_clauses = []
        # Jurisdiction-scoped for every officer -- see the note on
        # `get_officer_applications` for why `assigned_officer_id` cannot be used,
        # and why `.survey_number_id.in_(...)` here (comparing a SurveyNumber id
        # against a subquery of Application ids) never matched for a district
        # officer either.
        jurisdiction_filters = await get_jurisdiction_filter(db, officer)
        if jurisdiction_filters:
            where_clauses.append(
                Application.id.in_(_application_jurisdiction_subquery(jurisdiction_filters))
            )

        if status:
            where_clauses.append(Application.current_status == status)
        if application_type:
            where_clauses.append(Application.application_type == application_type)

        # "my last approved application in block 0015" -- the geography is part
        # of the question, so it has to narrow the search rather than be dropped
        # and the most recent application anywhere returned in its place.
        if ward_number or block_number:
            geo = (
                select(SurveyNumber.id)
                .join(Block, SurveyNumber.block_id == Block.id)
                .join(Ward, Block.ward_id == Ward.id)
            )
            geo_conds = []
            if block_number:
                geo_conds.append(Block.block_number.ilike(f"%{block_number}%"))
            if ward_number:
                geo_conds.append(Ward.ward_number.ilike(f"%{ward_number}%"))
            where_clauses.append(Application.survey_number_id.in_(geo.where(and_(*geo_conds))))

        last_action = (
            select(
                WorkflowHistory.application_id.label("application_id"),
                func.max(WorkflowHistory.performed_at).label("last_action_at"),
            )
            .group_by(WorkflowHistory.application_id)
            .subquery()
        )

        # COALESCE keeps an application with no workflow rows in the running,
        # ordered by its submission date instead of dropping out of the ranking.
        recency = func.coalesce(
            last_action.c.last_action_at,
            func.cast(Application.submission_date, TIMESTAMP(timezone=True)),
        )

        query = (
            select(Application.application_number, last_action.c.last_action_at)
            .outerjoin(last_action, last_action.c.application_id == Application.id)
            .where(and_(True, *where_clauses))
            .order_by(recency.desc(), Application.submission_date.desc(),
                      Application.application_number.desc())
            .limit(1)
        )

        row = (await db.execute(query)).first()
        if not row:
            return {
                "found": False,
                "no_match": True,
                "asked_status": status,
                "asked_type": application_type,
                "asked_ward": ward_number,
                "asked_block": block_number,
            }

        app_number, last_action_at = row
        detail = await get_application_detail(db, app_number, officer=officer)
        detail["last_action_at"] = last_action_at.isoformat() if last_action_at else None
        detail["asked_status"] = status
        detail["asked_type"] = application_type
        detail["asked_ward"] = ward_number
        detail["asked_block"] = block_number
        return detail

    except Exception as e:
        logger.error(f"Error getting last application: {e}")
        return {"found": False, "error": str(e)}


async def get_visit_plan(
    db: AsyncSession,
    officer: OfficerContext,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    ward_number: Optional[str] = None,
    block_number: Optional[str] = None,
) -> Dict[str, Any]:
    """What the officer should go and inspect, and where.

    "Which application should I field visit tomorrow?" is a planning question,
    not a listing: answering it with the visits already on the calendar leaves
    an officer with an empty calendar being told "none", which is true and
    useless. So three sets come back together:

      * `scheduled`  -- visits already booked inside the window asked about;
      * `overdue`    -- visits whose scheduled date has passed and that were
                        never completed, which outrank anything new;
      * `awaiting`   -- active ISD applications on the officer's desk
                        with no visit booked at all, oldest first, since those
                        are what the free day should be spent on.

    `blocks` and `wards` count the recommended work by location, because the
    officer plans a day around one part of the town, not around one file.
    NISD applications never appear: their workflow has no field visit.
    """
    if not officer or not officer.officer_id:
        logger.error("Invalid officer context provided to get_visit_plan")
        return {"scheduled": [], "overdue": [], "awaiting": [], "error": "Invalid officer context"}

    try:
        today = date.today()

        # Jurisdiction-scoped -- see the note on `get_officer_applications` for
        # why `assigned_officer_id` cannot be used.
        jurisdiction_filters = await get_jurisdiction_filter(db, officer)
        if not jurisdiction_filters:
            return {"scheduled": [], "overdue": [], "awaiting": [], "error": "No jurisdiction assigned"}

        geography = (
            select(
                Application.id.label("application_id"),
                Application.application_number,
                Application.application_type,
                Application.current_status,
                Application.submission_date,
                Application.is_overdue,
                Application.field_visit_scheduled,
                SurveyNumber.survey_no,
                Block.block_number,
                Ward.ward_number,
                Town.name.label("town_name"),
            )
            .join(SurveyNumber, Application.survey_number_id == SurveyNumber.id)
            .join(Block, SurveyNumber.block_id == Block.id)
            .join(Ward, Block.ward_id == Ward.id)
            .join(Town, Ward.town_id == Town.id)
            .where(or_(*jurisdiction_filters))
        )
        # "which applications in block 0015 need a visit" narrows the plan to
        # one part of the officer's own jurisdiction; it never widens it.
        if ward_number:
            geography = geography.where(Ward.ward_number == str(ward_number))
        if block_number:
            geography = geography.where(Block.block_number == str(block_number))

        # One subquery, used by both halves of the plan, so a visit row and an
        # awaiting row carry exactly the same geography columns.
        geo = geography.subquery()

        visit_query = (
            select(FieldVisit,
                   geo.c.application_number, geo.c.application_type, geo.c.survey_no,
                   geo.c.block_number, geo.c.ward_number, geo.c.town_name)
            .select_from(FieldVisit)
            .join(geo, geo.c.application_id == FieldVisit.application_id)
            .where(FieldVisit.status.in_(["scheduled", "rescheduled"]))
        )
        visit_rows = (await db.execute(visit_query)).all()

        # A row holds the FieldVisit first and then the geography columns, which
        # Row exposes by name.
        scheduled, overdue = [], []
        for row in visit_rows:
            visit = row[0]
            entry = {
                "application_id": visit.application_id,
                "application_number": row.application_number,
                "type": row.application_type,
                "survey_no": row.survey_no,
                "block_number": row.block_number,
                "ward_number": row.ward_number,
                "town_name": row.town_name,
                "scheduled_date": visit.scheduled_date.isoformat() if visit.scheduled_date else None,
                "status": visit.status,
                "days_late": (today - visit.scheduled_date).days if visit.scheduled_date else None,
            }
            if visit.scheduled_date and visit.scheduled_date < today:
                overdue.append(entry)
            elif (visit.scheduled_date and start_date and end_date
                  and start_date <= visit.scheduled_date <= end_date):
                scheduled.append(entry)
            elif visit.scheduled_date and not (start_date or end_date):
                scheduled.append(entry)

        scheduled.sort(key=lambda e: e["scheduled_date"] or "")
        overdue.sort(key=lambda e: e["days_late"] or 0, reverse=True)

        # Awaiting: an ISD file still on the officer's desk with no visit
        # booked. field_visit_scheduled is the application's own flag; a visit
        # row left 'unscheduled' means the same thing, so neither is trusted
        # alone.
        booked = {
            row.application_id for row in
            (await db.execute(
                select(FieldVisit.application_id)
                .where(FieldVisit.status.in_(["scheduled", "rescheduled", "completed"]))
            )).all()
        }
        awaiting_rows = (await db.execute(
            geography.where(and_(
                Application.application_type.in_(["ISD"]),
                Application.current_status.in_(ACTIVE_STATUSES),
            ))
        )).all()

        awaiting = []
        for row in awaiting_rows:
            if row.application_id in booked or row.field_visit_scheduled:
                continue
            awaiting.append({
                "application_id": row.application_id,
                "application_number": row.application_number,
                "type": row.application_type,
                "status": row.current_status,
                "survey_no": row.survey_no,
                "block_number": row.block_number,
                "ward_number": row.ward_number,
                "town_name": row.town_name,
                "submission_date": row.submission_date.isoformat() if row.submission_date else None,
                "days_pending": (today - row.submission_date).days if row.submission_date else None,
                "is_overdue": bool(row.is_overdue),
            })
        # Overdue files first, then the longest-waiting -- the order the day
        # should actually be worked in.
        awaiting.sort(key=lambda e: (not e["is_overdue"], -(e["days_pending"] or 0)))

        # Sub-division number(s) -- an ISD parcel can carry more than one
        # proposed sub-division row (areg_temp_subdivclub, one per plot the
        # survey is split into). A single flat JOIN onto the geography
        # subquery above would multiply a one-row application into several
        # visit-plan rows for the same file, so this is a SEPARATE aggregate
        # query keyed by application_id, attached in Python -- every
        # application still appears exactly once, however many sub-divisions
        # it has, and every one of its numbers is shown rather than an
        # arbitrary first row. Same source field `get_application_detail()`
        # already treats as authoritative: proposed_sub_division_no, falling
        # back to the linked SubDivision's own number when that column is
        # blank (areg_temp_subdivclub rows that never got a proposed number).
        all_ids = [e["application_id"] for e in (scheduled + overdue + awaiting)]
        sub_div_map: Dict[Any, List[str]] = {}
        if all_ids:
            sd_rows = (await db.execute(
                select(ApplicationSubDivision.application_id,
                       ApplicationSubDivision.proposed_sub_division_no,
                       SubDivision.sub_division_no)
                .outerjoin(SubDivision, ApplicationSubDivision.sub_division_id == SubDivision.id)
                .where(ApplicationSubDivision.application_id.in_(all_ids))
            )).all()
            for sd_row in sd_rows:
                no = sd_row.proposed_sub_division_no or sd_row.sub_division_no
                if no:
                    sub_div_map.setdefault(sd_row.application_id, [])
                    if no not in sub_div_map[sd_row.application_id]:
                        sub_div_map[sd_row.application_id].append(no)
        for entry in (scheduled + overdue + awaiting):
            # NISD never carries a sub-division at all (0153 = Not Involving
            # Sub-Division) -- "not applicable" states a fact about the
            # service, never guessed as a missing value. An ISD file with no
            # row recorded yet is the genuinely missing case.
            if entry.get("type") != "ISD":
                entry["sub_division_display"] = "Not applicable"
            else:
                nos = sub_div_map.get(entry["application_id"]) or []
                entry["sub_division_display"] = ", ".join(nos) if nos else "Not recorded"

        recommended = overdue + awaiting
        blocks: Dict[str, int] = {}
        wards: Dict[str, int] = {}
        for entry in recommended:
            if entry.get("block_number"):
                blocks[entry["block_number"]] = blocks.get(entry["block_number"], 0) + 1
            if entry.get("ward_number"):
                wards[entry["ward_number"]] = wards.get(entry["ward_number"], 0) + 1

        return {
            "scheduled": scheduled,
            "overdue": overdue,
            "awaiting": awaiting,
            "blocks": blocks,
            "wards": wards,
            "window_start": start_date.isoformat() if start_date else None,
            "window_end": end_date.isoformat() if end_date else None,
            "today": today.isoformat(),
        }

    except Exception as e:
        logger.error(f"Error building visit plan: {e}")
        return {"scheduled": [], "overdue": [], "awaiting": [], "error": str(e)}


async def get_ip_comparison(
    db: AsyncSession,
    officer: OfficerContext,
    application_numbers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Which applications were submitted from the same IP address.

    With `application_numbers` the named files are compared (rejected ones too --
    they were asked for by name); without, every non-rejected application in the
    officer's jurisdiction is grouped. Numbers outside the jurisdiction are
    counted in `dropped`, never returned. Returns `groups` (IPs carried by two or
    more files, largest first) and `singles` (file -> IP for the rest).
    """
    numbers = [str(n).strip().upper() for n in (application_numbers or []) if str(n).strip()]
    filters = await get_jurisdiction_filter(db, officer)
    if not filters:
        return {"error": "No jurisdiction assigned", "groups": [], "singles": {}, "count": 0,
                "dropped": len(numbers), "no_ip": []}
    q = (select(Application.application_number, Application.submission_ip)
         .where(Application.id.in_(_application_jurisdiction_subquery(filters))))
    if numbers:
        q = q.where(Application.application_number.in_(numbers))
    else:
        q = q.where(Application.current_status != "rejected")
    rows = list((await db.execute(q.order_by(Application.application_number))).all())

    # ISD/NISD (0153/0154) is only part of the register -- service_register_entries carries
    # submission_ip for every other code too, so a comparison naming (or spanning) a 0158 /
    # 0169 / F-Line / ... file is not silently limited to the two codes `Application` tracks.
    sq = (select(ServiceRegisterEntry.application_number, ServiceRegisterEntry.submission_ip)
          .where(ServiceRegisterEntry.service_code.notin_(["0153", "0154"]),
                 ServiceRegisterEntry.ward_id.in_(officer.jurisdiction_ids or [])))
    if numbers:
        sq = sq.where(ServiceRegisterEntry.application_number.in_(numbers))
    else:
        sq = sq.where(ServiceRegisterEntry.status != "rejected")
    rows += list((await db.execute(sq.order_by(ServiceRegisterEntry.application_number))).all())

    by_ip: Dict[str, List[str]] = {}
    no_ip: List[str] = []
    for num, ip in rows:
        if ip and ip.strip():
            by_ip.setdefault(ip.strip(), []).append(num)
        else:
            no_ip.append(num)
    groups = sorted(((ip, a) for ip, a in by_ip.items() if len(a) > 1), key=lambda g: (-len(g[1]), g[0]))
    singles = {a[0]: ip for ip, a in by_ip.items() if len(a) == 1}
    found = {n for n, _ in rows}
    return {"count": len(rows), "groups": groups, "singles": singles, "no_ip": no_ip,
            "dropped": len([n for n in numbers if n not in found]), "requested": len(numbers)}


_NO_REMARK = {"", "-", "--", "---", "na", "n/a", "nil", "none", "null"}


def _real_remark(text) -> Optional[str]:
    t = (text or "").strip()
    return None if t.lower() in _NO_REMARK else t


async def get_workflow_remarks(
    db: AsyncSession,
    officer: OfficerContext,
    application_numbers: List[str],
) -> Dict[str, Any]:
    """The remarks written on each application's workflow steps, and the reason a rejected file gives.

    Only applications inside the officer's jurisdiction are returned; the rest are counted in `dropped`,
    never described. A placeholder ("-", blank) is not a remark. `apps` keeps the order asked in.
    """
    numbers = [str(n).strip().upper() for n in (application_numbers or []) if str(n).strip()]
    if not officer or not officer.officer_id or not numbers:
        return {"apps": [], "requested": len(numbers), "dropped": len(numbers)}
    filters = await get_jurisdiction_filter(db, officer)
    if not filters:
        return {"apps": [], "requested": len(numbers), "dropped": len(numbers)}
    rows = (await db.execute(
        select(Application.application_number, Application.current_status, Application.application_type,
               WorkflowHistory.from_stage, WorkflowHistory.to_stage, WorkflowHistory.remarks,
               WorkflowHistory.rejection_reason, WorkflowHistory.performed_at)
        .select_from(Application)
        .outerjoin(WorkflowHistory, WorkflowHistory.application_id == Application.id)
        .where(Application.application_number.in_(numbers))
        .where(Application.id.in_(_application_jurisdiction_subquery(filters)))
        .order_by(Application.application_number, WorkflowHistory.performed_at)
    )).all()
    by: Dict[str, Dict[str, Any]] = {}
    for num, status, atype, frm, to, rem, rej, at in rows:
        rec = by.setdefault(num, {"application_number": num, "status": status, "type": atype,
                                  "hops": [], "reason": None})
        remark, reason = _real_remark(rem), _real_remark(rej)
        if frm or to:
            rec["hops"].append({"desk": frm or to, "to": to, "remark": remark,
                                "date": at.date().isoformat() if at else None})
        if reason:
            rec["reason"] = reason
        elif status == "rejected" and to == "REJECTED" and remark:
            rec["reason"] = remark          # the closing hop's own words
    ordered = [by[n] for n in numbers if n in by]
    return {"apps": ordered, "requested": len(numbers), "dropped": len(numbers) - len(ordered)}


async def get_workflow_history(
    db: AsyncSession,
    officer: OfficerContext,
    application_number: str,
) -> Optional[List[Dict[str, Any]]]:
    """The full, ordered workflow_history for ONE ISD/NISD application -- the canonical source for
    every "first/latest/Nth action", "who/which desk", "transition before/after X", "how many
    entries" and "date current stage was reached" question. `chatbot._workflow_history_answer()`
    is the only caller; it answers every sub-question from this one ordered list rather than each
    re-deriving its own query. Returns None if the application does not exist or is outside the
    officer's jurisdiction (never a distinguishable-from-empty list, so a refusal and a
    zero-hop file are never confused)."""
    jurisdiction_filters = await get_jurisdiction_filter(db, officer)
    if not jurisdiction_filters:
        return None
    exists = (await db.execute(
        select(Application.id)
        .where(Application.application_number == application_number)
        .where(Application.id.in_(_application_jurisdiction_subquery(jurisdiction_filters)))
    )).scalar_one_or_none()
    if not exists:
        return None
    rows = (await db.execute(
        select(WorkflowHistory.from_stage, WorkflowHistory.to_stage, WorkflowHistory.action,
               WorkflowHistory.remarks, WorkflowHistory.rejection_reason, WorkflowHistory.performed_at)
        .where(WorkflowHistory.application_id == exists)
        .order_by(WorkflowHistory.performed_at.asc())
    )).all()
    return [{"from_stage": frm, "to_stage": to, "action": act,
             "remarks": _real_remark(rem), "rejection_reason": _real_remark(rej),
             "performed_at": at} for frm, to, act, rem, rej, at in rows]


async def get_applications_by_submission_ip(
    db: AsyncSession,
    officer: OfficerContext,
    application_number: str,
    same: bool,
) -> Optional[Dict[str, Any]]:
    """Other ISD/NISD applications sharing (same=True) or differing from (same=False) the given
    application's submission_ip -- always excluding the named application itself, always scoped to
    the officer's own jurisdiction, and always excluding rows with no recorded IP (an unknown IP is
    neither "the same" nor "different" -- it is simply not comparable). Returns None if the named
    application itself does not exist or is outside the officer's jurisdiction."""
    jurisdiction_filters = await get_jurisdiction_filter(db, officer)
    if not jurisdiction_filters:
        return None
    base = (
        select(Application.id, Application.submission_ip)
        .where(Application.application_number == application_number)
        .where(Application.id.in_(_application_jurisdiction_subquery(jurisdiction_filters)))
    )
    row = (await db.execute(base)).first()
    if not row or not row.submission_ip:
        return {"ip": None, "applications": []} if row else None
    ip = row.submission_ip
    q = (
        select(Application.application_number, Application.submission_ip)
        .where(Application.id.in_(_application_jurisdiction_subquery(jurisdiction_filters)))
        .where(Application.application_number != application_number)
        .where(Application.submission_ip.isnot(None))
        .where(Application.submission_ip == ip if same else Application.submission_ip != ip)
        .order_by(Application.application_number)
    )
    rows = (await db.execute(q)).all()
    return {"ip": ip, "applications": [{"application_number": n, "submission_ip": i} for n, i in rows]}


async def get_repeat_applicants(db: AsyncSession, officer: OfficerContext) -> List[Dict[str, Any]]:
    """Applicants (same name AND mobile) with more than one ISD / NISD application in the officer's jurisdiction.
    The register keeps one applicant row per application, so a person is recognised by name + mobile. Read-only."""
    jurisdiction_filters = await get_jurisdiction_filter(db, officer)
    if not jurisdiction_filters:
        return []
    q = (select(Applicant.name, Applicant.mobile, Application.application_number, Application.current_status)
         .join(Application, Application.applicant_id == Applicant.id)
         .where(Application.id.in_(_application_jurisdiction_subquery(jurisdiction_filters))))
    by: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
    for name, mobile, num, status in (await db.execute(q)).all():
        if name and mobile:
            by.setdefault((str(name).strip().lower(), str(mobile).strip()), []).append((name, num, status))
    out = [{"name": v[0][0], "applications": [(n, st) for _x, n, st in v]} for v in by.values() if len(v) > 1]
    return sorted(out, key=lambda r: -len(r["applications"]))


async def get_analytics_rows(db: AsyncSession, officer: OfficerContext) -> List[Dict[str, Any]]:
    """One lean row per application in the officer's jurisdiction, every status: the columns the
    year / month / fee / percentage / turnaround questions are computed from. Read-only."""
    jurisdiction_filters = await get_jurisdiction_filter(db, officer)
    if not jurisdiction_filters:
        return []
    decided = (
        select(WorkflowHistory.application_id.label("app_id"),
               func.max(WorkflowHistory.performed_at).label("decided_at"))
        .where(WorkflowHistory.to_stage.in_(("COMPLETED", "REJECTED")))
        .group_by(WorkflowHistory.application_id).subquery()
    )
    q = (select(Application.application_number, Application.application_type, Application.current_status,
                Application.submission_channel, Application.submission_date, Application.fee_amount,
                decided.c.decided_at)
         .outerjoin(decided, decided.c.app_id == Application.id)
         .where(Application.id.in_(_application_jurisdiction_subquery(jurisdiction_filters))))
    out = []
    for num, typ, status, channel, sub, fee, decided_at in (await db.execute(q)).all():
        days = (decided_at.date() - sub).days if (decided_at and sub) else None
        out.append({"application_number": num, "type": typ, "status": status, "submission_channel": channel,
                    "submission_date": sub, "fee": float(fee) if fee is not None else None,
                    "decided_on": decided_at.date() if decided_at else None, "days_to_decide": days})

    # The other service codes (0156, 0158, 0159, 0178, ...) live only in the
    # register. Their rows are added so a total, a per-month or per-year count
    # or a percentage covers every service code, exactly as the listings do. The
    # register holds no fee and no decision date, so those stay empty here and
    # the fee / turnaround answers continue to rest on the files that have them.
    from backend.utils.helpers import SIS_URBAN_SERVICES
    _have = {r["application_number"] for r in out}
    for r in (await get_all_service_code_applications(db, officer)).get("applications") or []:
        if r["application_number"] in _have:
            continue
        out.append({"application_number": r["application_number"],
                    "type": (SIS_URBAN_SERVICES.get(r["service_code"], {}) or {}).get("name") or r["service_code"],
                    "service_code": r["service_code"], "status": r["status"],
                    "submission_channel": r["submission_channel"],
                    "submission_date": date.fromisoformat(r["submission_date"]) if r.get("submission_date") else None,
                    "fee": None, "decided_on": None, "days_to_decide": None})
    return out


async def get_flagged_surveys(db: AsyncSession, officer: OfficerContext, kind: str) -> Dict[str, Any]:
    """Survey numbers in the officer's jurisdiction flagged for encroachment or litigation, with their ISD / NISD applications."""
    jurisdiction_filters = await get_jurisdiction_filter(db, officer)
    if not jurisdiction_filters:
        return {"surveys": []}
    flag = SurveyNumber.has_encroachment if kind == "encroachment" else SurveyNumber.has_litigation
    q = (select(SurveyNumber.survey_no, Application.application_number, Application.current_status)
         .join(Application, Application.survey_number_id == SurveyNumber.id)
         .where(flag.is_(True), Application.id.in_(_application_jurisdiction_subquery(jurisdiction_filters))))
    by: Dict[str, List[Tuple[str, str]]] = {}
    for sv, num, st in (await db.execute(q)).all():
        by.setdefault(str(sv), []).append((num, st))
    return {"surveys": [{"survey_no": k, "applications": v} for k, v in sorted(by.items())]}


# "Show only those with a subdivision recorded" / "...without a scheduled
# field visit" / "...with payment recorded" / "...with workflow remarks" --
# a per-application PRESENCE check over an explicit, already-known list
# (never a register-wide search), grounded in the canonical ORM relationship
# for each field rather than the legacy CSV-shaped extract tables. Only
# fields with an unambiguous, schema-backed answer are here; a field this
# register has no clean per-application column for is not guessed at.
#
# Three states, not two: True (recorded), False (applicable but not
# recorded), None (not applicable to this application at all -- NISD has no
# subdivision concept and no field visit; a register-only service code has
# no `Application` row, hence no fee/workflow columns either). Collapsing
# "not applicable" into "not recorded" would tell an officer an NISD file is
# "missing" a subdivision it was never going to have.
FIELD_PRESENCE_KEYS = ("subdivision", "field_visit_scheduled", "payment", "workflow_remarks")


async def get_field_presence(
    db: AsyncSession,
    officer: OfficerContext,
    application_numbers: List[str],
    field_key: str,
) -> Dict[str, Optional[bool]]:
    """{application_number: True | False | None} for `field_key`, scoped to
    the officer's jurisdiction and to exactly the numbers given -- a number
    outside the jurisdiction, or not found at all, is left out of the
    returned dict entirely (the caller treats a missing key as "cannot
    check", never as False)."""
    numbers = [str(n).strip().upper() for n in (application_numbers or []) if str(n).strip()]
    if not numbers or field_key not in FIELD_PRESENCE_KEYS:
        return {}
    jurisdiction_filters = await get_jurisdiction_filter(db, officer)
    if not jurisdiction_filters:
        return {}

    base = (select(Application.id, Application.application_number, Application.application_type)
            .where(Application.application_number.in_(numbers),
                   Application.id.in_(_application_jurisdiction_subquery(jurisdiction_filters))))
    rows = (await db.execute(base)).all()
    out: Dict[str, Optional[bool]] = {}

    if field_key == "subdivision":
        # Only an ISD file carries sub-divisions at all (CLAUDE.md:
        # "An ISD parcel carries two sub-division numbers... projected into
        # application_sub_divisions"); NISD is "not applicable", never "missing".
        isd_ids = [r.id for r in rows if r.application_type == "ISD"]
        has_ids = set()
        if isd_ids:
            q = select(ApplicationSubDivision.application_id).where(
                ApplicationSubDivision.application_id.in_(isd_ids)).distinct()
            has_ids = {row[0] for row in (await db.execute(q)).all()}
        for r in rows:
            out[r.application_number] = (r.id in has_ids) if r.application_type == "ISD" else None

    elif field_key == "field_visit_scheduled":
        # NISD never has a field visit (document verification only) --
        # "not applicable", not "unscheduled".
        isd_ids = {r.id: r.application_number for r in rows if r.application_type == "ISD"}
        if isd_ids:
            q = select(Application.id, Application.field_visit_scheduled).where(
                Application.id.in_(list(isd_ids.keys())))
            for app_id, scheduled in (await db.execute(q)).all():
                out[isd_ids[app_id]] = bool(scheduled)
        for r in rows:
            if r.application_type != "ISD":
                out[r.application_number] = None

    elif field_key == "payment":
        # fee_amount / challan_number are populated "from the NISD/ISD
        # application-info extracts" (models.py) -- an ISD/NISD-only field.
        # A number outside ISD/NISD (a register-only service code, which has
        # no `Application` row at all) never reaches `rows` in the first
        # place, so it is correctly left out of `out` -- "cannot check",
        # which the caller already treats as "not applicable" when it
        # names no key here.
        ids = {r.id: r.application_number for r in rows}
        if ids:
            q = select(Application.id, Application.challan_number, Application.fee_amount).where(
                Application.id.in_(list(ids.keys())))
            for app_id, challan, fee in (await db.execute(q)).all():
                out[ids[app_id]] = bool(challan) or bool(fee)

    elif field_key == "workflow_remarks":
        ids = {r.id: r.application_number for r in rows}
        if ids:
            q = select(WorkflowHistory.application_id).where(
                WorkflowHistory.application_id.in_(list(ids.keys())),
                WorkflowHistory.remarks.isnot(None),
                func.length(func.trim(WorkflowHistory.remarks)) > 0,
            ).distinct()
            with_remarks = {row[0] for row in (await db.execute(q)).all()}
            for app_id, num in ids.items():
                out[num] = app_id in with_remarks

    # Every number still missing from `out` is either genuinely outside the
    # officer's jurisdiction/nonexistent, or it exists but is not ISD/NISD --
    # `Application` (ck_application_type) only ever admits 0153/0154, so a
    # register-only service code (0158, 0156, ...) never reached `rows`
    # above at all. The two cases read very differently to an officer:
    # "not applicable" (this field has no meaning for that file's service)
    # versus "could not be checked" (something is actually unresolved). A
    # one-row-per-application existence check against the register --
    # covering every service code, unlike `Application` -- tells them apart;
    # the register is used ONLY for this existence/jurisdiction check, never
    # as the source of the field's own VALUE (that stays the canonical ORM
    # relationships above, never the raw extract columns).
    _missing = [n for n in numbers if n not in out]
    if _missing and officer.jurisdiction_ids:
        _reg_q = select(ServiceRegisterEntry.application_number).where(
            ServiceRegisterEntry.application_number.in_(_missing),
            ServiceRegisterEntry.ward_id.in_(officer.jurisdiction_ids))
        for (num,) in (await db.execute(_reg_q)).all():
            out[num] = None

    return out


async def get_applications_by_reason(db: AsyncSession, officer: OfficerContext, reasons: List[str]) -> List[Tuple[str, str, str]]:
    """ISD / NISD applications whose declared reason (sale, settlement, release, ...) is one of `reasons`, in the officer's jurisdiction."""
    jurisdiction_filters = await get_jurisdiction_filter(db, officer)
    if not jurisdiction_filters:
        return []
    q = (select(Application.application_number, Application.declared_reason, Application.current_status)
         .where(func.lower(Application.declared_reason).in_([r.lower() for r in reasons]),
                Application.id.in_(_application_jurisdiction_subquery(jurisdiction_filters)))
         .order_by(Application.submission_date.desc()))
    return [(n, r, s) for n, r, s in (await db.execute(q)).all()]


async def get_service_code_fees(
    db: AsyncSession,
    service_codes: Optional[Union[str, List[str]]] = None
) -> Union[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """Query live statutory and counter service fees from the database (service_fees table).
    Grounds fee lookups in real-time database state, allowing dynamic updates as G.O.s change.
    If service_codes is a single string (e.g. '0153'), returns the fee dict for that code.
    If service_codes is None or a list, returns a mapping of code -> fee dict.
    """
    single_code = service_codes if isinstance(service_codes, str) else None
    codes_filter = [service_codes] if isinstance(service_codes, str) else service_codes

    fees: Dict[str, Dict[str, Any]] = {}

    # 1. Statutory baseline defaults from departmental rules (land_rules.txt)
    from backend.utils.helpers import SIS_SERVICE_CODE_DETAIL
    for code, info in SIS_SERVICE_CODE_DETAIL.items():
        if codes_filter and code not in codes_filter:
            continue
        g = info.get("govt_fee")
        c = info.get("csc_fee")
        if g or c:
            combined_parts = []
            if g:
                combined_parts.append(f"Govt {g}")
            if c:
                combined_parts.append(f"CSC {c}")
            fees[code] = {
                "service_code": code,
                "service_name": info.get("name", ""),
                "govt_fee": g,
                "csc_fee": c,
                "total_fee": None,
                "fee_display": ", ".join(combined_parts),
                "fee_summary": ", ".join(combined_parts),
                "source": "statutory_guideline"
            }

    # 2. Query live database table (service_fees)
    try:
        stmt = select(ServiceFee)
        if codes_filter:
            stmt = stmt.where(ServiceFee.service_code.in_(codes_filter))
        result = await db.execute(stmt)
        for row in result.scalars().all():
            g_fee = float(row.govt_fee) if row.govt_fee is not None else 0.0
            c_fee = float(row.csc_fee) if row.csc_fee is not None else 0.0
            t_fee = float(row.total_fee) if row.total_fee is not None else (g_fee + c_fee)
            fee_str_parts = []
            if row.govt_fee is not None:
                fee_str_parts.append(f"Govt ₹{g_fee:.2f}")
            if row.csc_fee is not None:
                fee_str_parts.append(f"CSC ₹{c_fee:.2f}")
            disp = ", ".join(fee_str_parts) if fee_str_parts else "Not documented in departmental guidelines"

            fees[row.service_code] = {
                "service_code": row.service_code,
                "service_name": row.service_name,
                "govt_fee": f"₹{g_fee:.2f}" if row.govt_fee is not None else None,
                "csc_fee": f"₹{c_fee:.2f}" if row.csc_fee is not None else None,
                "total_fee": f"₹{t_fee:.2f}" if row.total_fee is not None else None,
                "govt_fee_num": g_fee,
                "csc_fee_num": c_fee,
                "total_fee_num": t_fee,
                "fee_display": disp,
                "fee_summary": disp,
                "sla_days": row.sla_days or "Not documented",
                "sla_days_ta": row.sla_days_ta or "ஆவணப்படுத்தப்படவில்லை",
                "remarks": row.remarks,
                "source": "database_service_fees_table"
            }
    except Exception as e:
        logger.error(f"Error fetching service code fees from database: {e}")

    if single_code:
        return fees.get(single_code, {
            "service_code": single_code,
            "govt_fee": None,
            "csc_fee": None,
            "fee_display": "Not documented in departmental guidelines",
            "fee_summary": "Not documented in departmental guidelines",
            "source": "not_documented"
        })
    return fees


async def get_all_service_fees(
    db: AsyncSession
) -> Dict[str, Dict[str, Any]]:
    """Dynamically query all service fees from the database service_fees table."""
    res = await get_service_code_fees(db, service_codes=None)
    return res if isinstance(res, dict) else {}


async def get_service_register_summary_counts(
    db: AsyncSession
) -> Dict[str, Any]:
    """Query live application counts per service code from service_register_entries.
    Returns per-code breakdown (total, approved, pending, rejected) and catalog summary.
    """
    try:
        stmt = select(
            ServiceRegisterEntry.service_code,
            func.count().label("total"),
            func.count(case((ServiceRegisterEntry.status.in_(["approved", "completed"]), 1))).label("approved"),
            func.count(case((ServiceRegisterEntry.status.in_(["pending", "open"]), 1))).label("pending"),
            func.count(case((ServiceRegisterEntry.status == "rejected", 1))).label("rejected")
        ).group_by(ServiceRegisterEntry.service_code).order_by(ServiceRegisterEntry.service_code)
        
        result = await db.execute(stmt)
        rows = result.all()
        by_code = {}
        total_records = 0
        for r in rows:
            sc, tot, appr, pend, rej = r[0], r[1], r[2], r[3], r[4]
            total_records += tot
            breakdown = []
            if appr > 0:
                breakdown.append(f"{appr} appr")
            if pend > 0:
                breakdown.append(f"{pend} pend")
            if rej > 0:
                breakdown.append(f"{rej} rej")
            bd_str = f" ({', '.join(breakdown)})" if breakdown else ""
            by_code[sc] = {
                "total": tot,
                "approved": appr,
                "pending": pend,
                "rejected": rej,
                "display": f"{tot} files in Register{bd_str}"
            }
        return {
            "by_code": by_code,
            "total_records": total_records,
            "codes_with_records_count": len(by_code),
            "total_codes_count": 29
        }
    except Exception as e:
        logger.error(f"Error fetching service register counts: {e}")
        return {"by_code": {}, "total_records": 0, "codes_with_records_count": 0, "total_codes_count": 29}


async def get_service_register_counts_by_code(
    db: AsyncSession
) -> Dict[str, Dict[str, Any]]:
    """Return live application counts by service code from service_register_entries.
    Keys are service codes ('0153', '0154', '0178', etc.).
    Values contain 'total', 'approved', 'pending', 'rejected', and 'display'.
    """
    summary = await get_service_register_summary_counts(db)
    return summary.get("by_code", {})