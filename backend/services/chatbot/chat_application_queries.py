"""
Application-number validation, last-application and visit-plan rendering, resolve_application_reference (the core application-resolution gate), fuzzy field matching, ISD processing, jurisdiction/ward/workload answers, comparison building, subdivision answers, and register listing.

Extracted from backend/services/chatbot.py (SIS_Chatbot_Modularity_Refactor,
chatbot.py >=15,000-line reduction pass) with no change in behavior -- every
function body below is copied verbatim from the original module. chatbot.py
re-exports the names it still needs so existing imports and monkeypatch
targets keep working unchanged.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional, List, Tuple, Sequence
from datetime import datetime, timedelta, date, timezone
import asyncio
import contextvars
import time
import difflib
import re
import json
import dataclasses
from pathlib import Path
import functools
import uuid
import calendar
from backend.config import (
    DISTRICT_NAME_MAP, DISTRICT_TAMIL_NAME_MAP, DISTRICT_CODE_TO_TAMIL_NAME, settings,
)
from backend.schemas import OfficerContext
from . import attachment_qa
from .agent import AgentUnavailable, run_agent, run_agent_stream
from backend.services.readonly_guard import chat_turn
from . import neg_scope as _neg_scope
from . import negq as _negq
from . import calc_qa as _calc_qa
from . import howto_qa as _howto_qa
from . import card_html as _card_html
from . import unheld_qa as _unheld_qa
from . import recheck as _recheck
from . import number_qa as _number_qa
from . import stats_qa as _stats_qa
from . import compare_followup as _compare_followup
from . import qualifier_guard as _qualifier_guard
from backend.services import followup_context as fctx
from . import semantic_intent
from . import subdivision_qa as _subdivision_qa
from . import oldowner_qa as _oldowner_qa
from . import cmflow_qa as _cmflow_qa
from . import appinfo_qa as _appinfo_qa
from . import fline_qa as _fline_qa
from . import merge_qa as _merge_qa
from . import crosscheck_qa as _crosscheck_qa
from . import igrs_owner_qa as _igrs_owner_qa
from . import newowner_qa as _newowner_qa
from . import fulltransfer_qa as _fulltransfer_qa
from . import sdattach_qa as _sdattach_qa
from . import tslr_qa as _tslr_qa
from . import uamod_qa as _uamod_qa
from . import natham_qa as _natham_qa
from . import othercode_detail as _othercode_detail
from backend.services.rag import (
    detect_language,
    is_short_reply,
    get_prior_turn_language,
    detect_jurisdiction_focus,
    wants_details_of_listed,
    requested_listing_count,
    extract_sort_order,
    extract_row_selection,
    extract_result_limit,
    get_rag_context_async,
    build_prompt,
    build_html_response,
    _money,
    call_llama,
    call_llama_stream,
    paraphrase_facts_naturally,
    parse_intent,
    parse_last_application_query,
    parse_visit_plan_query,
    extract_survey_number,
    extract_application_number,
    extract_application_numbers,
    extract_ward_number,
    extract_block_number,
    extract_town_name,
    extract_taluk_name,
    extract_date_range,
    extract_month_scopes,
    format_month_scopes,
    extract_submission_channels,
    ambiguous_channel_clarification,
    _get_projected_application_columns,
    is_bare_date_scope,
    strip_date_scope_phrases,
    extract_date_scope_fragment,
    DATE_SCOPED_INTENTS,
)
from backend.services.postgres import (
    get_all_service_code_applications,
    get_can_details,
    get_last_application,
    get_visit_plan,
    get_applications_by_block,
    get_ward_directory,
    get_pending_applications,
    count_rejected_by_channel,
    get_overdue_applications,
    get_officer_workload,
    get_officer_workload_applications,
    get_application_detail,
    get_survey_detail,
    get_survey_owners,
    get_unscheduled_visits,
    get_applications_by_numbers,
    get_field_visits,
    get_next_subdivision_number,
    check_survey_application_lock,
    get_jurisdiction_filter,
    get_ward_surveys,
    get_all_surveys_in_jurisdiction,
    get_officer_applications,
    get_fee_summary,
    get_recent_fees,
    get_field_presence,
    _application_jurisdiction_subquery,
    _field_visit_jurisdiction_subquery,
)
from backend.models import (
    ChatSession, ChatMessage, Application, SurveyNumber, Block, Ward, Town, Taluk,
    FieldVisit, ApplicationDocument, WorkflowHistory, ApplicationSubDivision,
    OfficerJurisdiction, District, SISOfficer, messages_since_session_start
)
from backend.utils.helpers import service_code_one_liner
from backend.utils.logger import get_logger
from backend.utils import translit
from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import selectinload
from backend.utils.fuzzy import (
    extract_month_from_text,
    extract_tokens,
    is_token_typo_match,
    match_phrase,
    normalize_text,
    resolve_unique_entry,
)
from .chat_corrections import (
    _is_ordinal_visit_reference,
    _oo_exists_parse,
    _status_yes_no,
)
from .chat_followups import (
    _app_numbers_from_last_listing,
    _extract_app_number_from_context,
    _geo_scope,
    _pick_from_listing,
    _wants_workflow_history,
)
from .chat_shared import (
    _LIMITABLE_LIST_INTENTS,
    _MONTH_NAMES,
    _SORT_OVERRIDE,
    _VALID_APP_NUMBER_RE,
    _app_numbers_in_recent_context,
    _cm_strong,
    _excluded_service_code_filter,
    _explicit_status_request,
    _has_application_back_reference,
    _is_bare_concept_definition,
    _is_register_wide_listing,
    _na_parse,
    _owner_ctx_compare,
    _period_from_message,
    _render_all_service_code_answer,
    _requested_service_code_filter,
    _requested_status_filter_for_register,
    _resolve_month_year,
    _sd_parse,
    _tslr_parse,
    _um_parse,
    _whole_month_label,
    _year_match,
    detect_offered_app_number_token,
    extract_month_from_query,
)
from .chat_smalltalk import _workflow_history_answer
from backend.utils.logger import get_logger

logger = get_logger(__name__)


_RAW_MESSAGE = contextvars.ContextVar("raw_message", default=None)


# Asked whenever an application-specific question arrives without an application
# number. Substituting an arbitrary application here would answer a question the
# officer never asked, so every such branch prompts instead.
ASK_FOR_APP_NUMBER = {
    "en": "Please specify the application number you are asking about — for example 2026/0154/28/NNNNNN.",
    "ta": "எந்த விண்ணப்பம் என்பதைக் குறிப்பிடவும் — எடுத்துக்காட்டாக 2026/0154/28/NNNNNN.",
    "tanglish": "எந்த விண்ணப்பம் என்பதைக் குறிப்பிடவும் — எடுத்துக்காட்டாக 2026/0154/28/NNNNNN.",
}

# Deliberately loose — only consulted after _VALID_APP_NUMBER_RE has already
# failed, so anything it catches is by definition not a valid number.
# Group 1: an APP-prefixed token (self-evidently an application number attempt).
# Group 2: a TYPE-prefixed token.
# Group 3: a bare digits-and-slashes token — needs a cue word to count.
_APP_NUMBER_ATTEMPT_RE = re.compile(
    r'\b(APP[\-_/]*\d[\w\-/]*)'
    r'|\b((?:ISD|NISD)/[\w/]+)'
    r'|(\b\d{2,4}(?:/\d{1,6})+\b)',
    re.IGNORECASE,
)

# A date typed as 12/05/2026, or a span like 2024/2025, must never be reported as
# a malformed application number — those are normal, supported queries.
_DATE_LIKE_RE = re.compile(r'^\d{1,2}/\d{1,2}(/\d{2,4})?$|^\d{4}/\d{1,2}$|^\d{4}/\d{4}$')

# A single-slash bare token only reads as a truncated application number when the
# second segment looks like a zero-padded service/district code (0153, 0154, 031…).
# That keeps "2026/0153" but rejects "2024/2025".
_TRUNCATED_APP_RE = re.compile(r'^\d{4}/0\d{3}$')

_APP_NUMBER_CUE_WORDS = ("app", "application", "appl", "விண்ணப்ப", "vinnappa")

# Intents that are not themselves application-specific but naturally continue a
# conversation about one: "what about the owner?" right after an application was
# shown means that application's survey. Broader list intents stay out of this
# set so a general query is never silently narrowed to the last application.
_CONTEXT_CONTINUATION_INTENTS = {"survey_owners"}


# Intents that can only ever be about a single named application, so when no
# number can be resolved the right move is to ask rather than guess.
_SINGLE_APP_INTENTS = {
    "application_status", "check_documents", "check_sale_deed", "sale_deed_check",
    "is_nisd_or_isd", "isd_processing", "rejection_info", "submission_channel_check",
}

# Intents whose answer is about one specific application, so a malformed number
# makes the whole request unanswerable.
_APP_NUMBER_INTENTS = {
    "application_status", "check_documents", "check_sale_deed", "sale_deed_check",
    "is_nisd_or_isd", "joint_owner_check", "isd_processing",
    "submission_channel_check",
    "escalation_check", "litigation_check", "rejection_info",
    "sd_additional_info", "sd_encroachment_check", "sd_sketch_readiness",
    "sd_forward_check", "sd_remarks",
    "fv_date_select", "fv_nearby_pending", "fv_deadline_check",
}


def detect_invalid_app_number(message: str) -> Optional[str]:
    """
    Return the offending token when the officer clearly tried to give an
    application number but used a format we cannot parse, else None.

    Returns None when the message contains a valid number, or contains nothing
    that looks like an attempt at one.
    """
    if not message:
        return None
    if _VALID_APP_NUMBER_RE.search(message):
        return None

    _lower = message.lower()
    has_cue = any(w in _lower for w in _APP_NUMBER_CUE_WORDS)

    offered = detect_offered_app_number_token(message)
    if offered:
        return offered

    for m in _APP_NUMBER_ATTEMPT_RE.finditer(message):
        app_tok, type_tok, bare_tok = m.group(1), m.group(2), m.group(3)
        token = app_tok or type_tok or bare_tok
        if not token:
            continue
        token = token.strip().rstrip("/-_")
        if bare_tok and not app_tok and not type_tok:
            # A bare number only counts as an attempt when the officer said
            # "application" somewhere, and never when it reads as a date or span.
            if not has_cue:
                continue
            # A single-slash token is ambiguous: keep it only in the
            # YYYY/service-code shape, otherwise treat it as a date or a span.
            if not _TRUNCATED_APP_RE.match(token):
                if _DATE_LIKE_RE.match(token) or token.count("/") < 2:
                    continue
        return token
    return None


def build_invalid_app_number_message(token: str, language: str) -> str:
    """Localised 'that application number is not valid' message."""
    if language in ("ta", "tanglish"):
        return (
            f"நீங்கள் கொடுத்த விண்ணப்ப எண் **{token}** சரியான வடிவத்தில் இல்லை.\n\n"
            "சரியான வடிவம்:\n"
            "  • YYYY/சேவைக்குறியீடு/மாவட்டக்குறியீடு/வரிசை எண் — எ.கா. 2026/0154/28/NNNNNN\n\n"
            "சரியான விண்ணப்ப எண்ணுடன் மீண்டும் கேட்கவும்."
        )
    return (
        f"The application number you entered — **{token}** — is not in a valid format.\n\n"
        "The valid application number format is:\n"
        "  • YYYY/SERVICE_CODE/DISTRICT_CODE/SEQUENCE — e.g. 2026/0154/28/NNNNNN\n\n"
        "Please re-enter your question with a valid application number."
    )


async def _not_found_or_register(db, officer, structured_data: Dict[str, Any], language: str) -> str:
    """The not-found message -- unless the number is in the officer's register under another service
    code, which `applications` (ISD / NISD only) cannot see. "does not exist in the database" was said
    of a 0165 file the officer holds."""
    no = (structured_data or {}).get("searched_number") or (structured_data or {}).get("application_number")
    if no:
        rows = ((await get_applications_by_numbers(db, officer, [no], include_rejected=True)) or {}).get("applications") or []
        if rows:
            ta = language in ("ta", "tanglish")
            return _card_html.card(_register_card(no, rows[0], ta), ta)
    return build_app_not_found_message(structured_data, language)


def build_app_not_found_message(structured_data: Dict[str, Any], language: str) -> str:
    """
    Localised 'no such application' message, including the suggestions
    get_application_detail() attaches when it finds near-matches.
    """
    searched = structured_data.get("searched_number") or structured_data.get("application_number") or ""
    suggestions = structured_data.get("suggestions") or []

    if language in ("ta", "tanglish"):
        head = (
            f"விண்ணப்ப எண் **{searched}** தரவுத்தளத்தில் இல்லை. "
            "விண்ணப்ப எண்ணைச் சரிபார்த்து மீண்டும் உள்ளிடவும்."
            if searched else
            "நீங்கள் கேட்ட விண்ணப்பம் கிடைக்கவில்லை."
        )
        lead = "\n\nஉங்கள் அதிகார எல்லையில் உள்ள சமீபத்திய விண்ணப்பங்கள்:"
    else:
        head = (
            f"Application **{searched}** does not exist in the database. "
            "Please check the application number and try again."
            if searched else
            "The application you asked for was not found."
        )
        lead = "\n\nRecent applications in your jurisdiction:"

    if not suggestions:
        return head

    lines = [
        f"  • {s.get('application_number')} — {s.get('type') or 'N/A'}, "
        f"{(s.get('status') or 'N/A').capitalize()}, {s.get('applicant_name') or 'N/A'}"
        for s in suggestions[:6]
    ]
    return head + lead + "\n" + "\n".join(lines)


def build_app_forbidden_message(app_number: str, officer, language: str) -> str:
    """Localised 'that application is outside your jurisdiction' message.

    Deliberately reveals nothing about the application beyond the fact that the
    officer may not see it — no status, no applicant, no geography.
    """
    level = getattr(officer, "jurisdiction_type", "") or "assigned"
    name = getattr(officer, "jurisdiction_name", "") or "your jurisdiction"
    if language in ("ta", "tanglish"):
        return (
            f"விண்ணப்ப எண் **{app_number}** உங்கள் அதிகார எல்லைக்கு ({name}) வெளியே உள்ளது.\n\n"
            "அதன் விவரங்களைப் பார்க்க உங்களுக்கு அனுமதி இல்லை."
        )
    return (
        f"Application **{app_number}** is outside your assigned {level} jurisdiction ({name}).\n\n"
        "You do not have access to its details."
    )


def build_app_confirm_message(app_number: str, suggestions: list, language: str) -> str:
    """Ask the officer to confirm one of the near matches. Never picks one for them."""
    lines = [
        f"  • {c.get('application_number')} — {c.get('type') or 'N/A'}, "
        f"{(c.get('status') or 'N/A').capitalize()}, {c.get('applicant_name') or 'N/A'}"
        for c in suggestions[:6]
    ]
    if language in ("ta", "tanglish"):
        head = (
            f"**{app_number}** என்ற எண்ணில் சரியான விண்ணப்பம் எதுவும் இல்லை. "
            "ஒத்த விண்ணப்பங்கள்:"
        )
        tail = "\n\nஇவற்றில் எந்த விண்ணப்ப எண் என்பதை முழுமையாக உள்ளிடவும்."
    else:
        head = f"No application exactly matches **{app_number}**. Similar applications:"
        tail = "\n\nPlease reply with the full application number you want."
    return head + "\n" + "\n".join(lines) + tail


def build_sale_deed_direct_answer(structured_data: Dict[str, Any], message: str, language: str) -> Optional[str]:
    """
    Answer a sale-deed-number / registration-status question straight from
    structured_data, in Python. check_sale_deed/sale_deed_check never had a
    deterministic path (unlike application_status), so a natural-language ask
    like "sale deed number?" / "பத்திர எண் என்ன?" went to the LLM with the
    whole application record and it sometimes echoed application_number back
    as if it were the deed number -- exactly the hallucination CLAUDE.md's
    "never let the LLM generate ... application numbers" rule exists to
    prevent, just for a different field. Returns None to fall through to the
    existing generic handling when the message isn't clearly asking for one
    of these two facts.
    """
    if not structured_data or not structured_data.get("found", True):
        return None
    msg = message.lower()
    is_tamil = language in ("ta", "tanglish")
    asks_number = any(w in msg for w in [
        "deed number", "deed no", "sale deed number", "sale deed no",
        "பத்திர எண்", "கிரய பத்திர எண்"])
    asks_registered = any(w in msg for w in [
        "registered", "registration status", "is the sale deed",
        "பதிவு செய்யப்பட்ட", "பதிவு ஆனதா", "பதிவானதா"])
    if not asks_number and not asks_registered:
        return None
    deed_no = structured_data.get("sale_deed_number")
    registered = structured_data.get("sale_deed_registered")
    app_no = structured_data.get("application_number") or ""
    if asks_number and not asks_registered:
        if deed_no:
            return f"பத்திர எண்: {deed_no}." if is_tamil else f"Sale deed number: {deed_no}."
        return (f"{app_no} க்கு விற்பனை பத்திர எண் பதிவு செய்யப்படவில்லை." if is_tamil
                else f"No sale deed number is recorded for {app_no}.")
    if registered:
        text = "விற்பனை பத்திரம் பதிவு செய்யப்பட்டுள்ளது." if is_tamil else "Yes, the sale deed is registered."
        if deed_no:
            text += f" பத்திரம் எண்: {deed_no}." if is_tamil else f" Deed number: {deed_no}."
        return text
    return "இல்லை, விற்பனை பத்திரம் இன்னும் பதிவு செய்யப்படவில்லை." if is_tamil else "No, the sale deed is not registered yet."


def build_ambiguous_reference_message(candidates: list, language: str) -> str:
    """
    The officer said "it" after a turn that named several applications. Rather
    than picking one, list them and ask which is meant.
    """
    listed = "\n".join(f"  • {c}" for c in candidates[:6])
    if language in ("ta", "tanglish"):
        return (
            "நீங்கள் குறிப்பிடுவது எந்த விண்ணப்பம் என்பது தெளிவாக இல்லை. "
            "முந்தைய செய்தியில் இவை இடம்பெற்றுள்ளன:\n" + listed +
            "\n\nஎந்த விண்ணப்ப எண் என்பதைக் குறிப்பிடவும்."
        )
    return (
        "Your previous message mentioned more than one application, so I am not "
        "sure which one you mean:\n" + listed +
        "\n\nPlease tell me which application number you want."
    )


# ── "my last / previous application" ─────────────────────────────────────────
# Status words as the answer should say them back.
_LAST_APP_STATUS_LABELS = {
    "approved": "approved",
    "rejected": "rejected",
    "pending": "pending",
    "in_progress": "in progress",
    "escalated": "escalated",
}
_LAST_APP_STATUS_LABELS_TA = {
    "approved": "ஒப்புதல் அளிக்கப்பட்ட",
    "rejected": "நிராகரிக்கப்பட்ட",
    "pending": "நிலுவையில் உள்ள",
    "in_progress": "நடப்பில் உள்ள",
    "escalated": "மேல்முறையீடு செய்யப்பட்ட",
}


def _visit_plan_window_label(sd: dict) -> str:
    """"tomorrow", "between X and Y" -- named the way it was asked."""
    start, end = sd.get("window_start"), sd.get("window_end")
    if not start and not end:
        return ""
    today = date.fromisoformat(sd["today"])
    if start == end:
        day = date.fromisoformat(start)
        if day == today:
            return f"today ({start})"
        if day == today + timedelta(days=1):
            return f"tomorrow ({start})"
        if day == today + timedelta(days=2):
            return f"the day after tomorrow ({start})"
        return f"on {start}"
    return f"between {start} and {end}"


def _visit_row_html(entry: dict, kind: str) -> str:
    """One <tr> for the visit-plan tables below. `kind` picks the column set:
    "scheduled"/"overdue" show the scheduled date (+ overdue days for
    "overdue"); "awaiting" shows how long the application has been waiting.
    Sub-Division No. is `get_visit_plan()`'s "sub_division_display" -- already
    resolved there per-application (every proposed sub-division number an ISD
    file carries, joined; "Not applicable" for a non-ISD type; "Not recorded"
    for an ISD file with none yet), never guessed here.
    Real HTML, not a plain-text line a generic plain-text-to-card heuristic
    could misparse -- see _render_visit_plan_answer for why that matters."""
    from html import escape as _e
    app_no = _e(entry.get("application_number") or "N/A")
    survey = _e(str(entry.get("survey_no") or "N/A"))
    subdiv = _e(entry.get("sub_division_display") or "Not recorded")
    ward = _e(str(entry.get("ward_number") or "N/A"))
    block = _e(str(entry.get("block_number") or "N/A"))
    if kind in ("scheduled", "overdue"):
        sched = _e(entry.get("scheduled_date") or "N/A")
        if kind == "overdue":
            days_late = entry.get("days_late") or 0
            return (f"<tr><td>{app_no}</td><td>{survey}</td><td>{subdiv}</td><td>{sched}</td>"
                     f"<td>{days_late} day{'s' if days_late != 1 else ''}</td>"
                     f"<td>{ward}</td><td>{block}</td></tr>")
        return f"<tr><td>{app_no}</td><td>{survey}</td><td>{subdiv}</td><td>{sched}</td><td>{ward}</td><td>{block}</td></tr>"
    # awaiting
    days = entry.get("days_pending")
    waiting = f"{days} day{'s' if days != 1 else ''}" if days is not None else "N/A"
    return f"<tr><td>{app_no}</td><td>{survey}</td><td>{subdiv}</td><td>{waiting}</td><td>{ward}</td><td>{block}</td></tr>"


def _visit_table_html(entries: list, kind: str) -> str:
    """The <table> that wraps `_visit_row_html` rows, one column header set
    per `kind` -- see `_visit_row_html`."""
    if kind == "overdue":
        head = "<tr><th>Application No.</th><th>Survey No.</th><th>Sub-Division No.</th><th>Scheduled Date</th><th>Overdue</th><th>Ward</th><th>Block</th></tr>"
    elif kind == "scheduled":
        head = "<tr><th>Application No.</th><th>Survey No.</th><th>Sub-Division No.</th><th>Scheduled Date</th><th>Ward</th><th>Block</th></tr>"
    else:
        head = "<tr><th>Application No.</th><th>Survey No.</th><th>Sub-Division No.</th><th>Waiting</th><th>Ward</th><th>Block</th></tr>"
    rows = "".join(_visit_row_html(e, kind) for e in entries)
    return (f"<div class='data-table-container'><table class='data-table'>"
            f"<thead>{head}</thead><tbody>{rows}</tbody></table></div>")


def _render_visit_plan_answer(sd: dict, language: str) -> str:
    """Deterministic answer for "which application should I visit, and where?"
    -- a PRIORITIZATION request, not a literal calendar lookup. An empty
    calendar is the normal case in this data, so "nothing is scheduled" can
    never be the whole answer, and must never be the HEADING either when
    there is real overdue/awaiting work to report: that reads as "nothing to
    do", which is the opposite of what an empty calendar plus outstanding
    work means. What the officer needs is the overdue visits first (most
    overdue first) and then the longest-waiting applications with no visit
    booked at all, each with the ward and block to go to -- in clearly
    separate, explicitly-titled sections, never folded into one generic list.

    Returned as real HTML (not plain bullet text) so the frontend's generic
    plain-text-to-card heuristic (and its server-side twin, card_html.card())
    never runs on it at all: both treat any non-bulleted "Label: value" line
    as a data row, which turned the trailing "Where: ward 001 (10)." summary
    line into a phantom extra record -- a badge reading "11" when only 10
    applications (3 overdue + 7 awaiting) were ever shown. Building the HTML
    directly, with the record counts taken from the same lists that are
    rendered, is what keeps the badge and the table in sync by construction
    rather than by a text heuristic guessing right.
    """
    from html import escape as _e
    if sd.get("error"):
        return "Sorry, I could not read your field visit plan just now."

    window = _visit_plan_window_label(sd)
    scheduled = sd.get("scheduled") or []
    overdue = sd.get("overdue") or []
    awaiting = sd.get("awaiting") or []
    # Belt and suspenders: get_visit_plan() already keeps these disjoint (an
    # application with a booked FieldVisit row is excluded from `awaiting`),
    # but an application is never shown -- or counted -- twice here even if
    # that upstream invariant is ever loosened.
    _overdue_nums = {e.get("application_number") for e in overdue}
    awaiting = [e for e in awaiting if e.get("application_number") not in _overdue_nums]

    focus = sd.get("focus")
    scope = " / ".join(part for part in (
        f"ward {sd['asked_ward']}" if sd.get("asked_ward") else "",
        f"block {sd['asked_block']}" if sd.get("asked_block") else "",
    ) if part)
    scope_suffix = f" in {scope}" if scope else ""

    # "for tomorrow" but "between X and Y" -- the label already carries the
    # preposition it needs for a range.
    window_phrase = (f"for {window}" if window and not window.startswith("between")
                     else window)

    parts = []

    # ---- Upcoming calendar: visits actually booked, inside the window asked
    # about if one was named. Kept separate from overdue/awaiting below so a
    # pure "what's on my calendar this week" question is never answered with
    # overdue visits or unscheduled applications folded into its own list of
    # "upcoming" events. ----
    if scheduled:
        head = f"{len(scheduled)} field visit(s) already scheduled{scope_suffix}" + (f" {window_phrase}" if window else "")
        parts.append(f"<div class='table-intro'>{_e(head)}:</div>" + _visit_table_html(scheduled, "scheduled"))
    elif window:
        parts.append(f"<div class='table-intro'>{_e(f'Nothing is scheduled{scope_suffix} {window_phrase}.')}</div>")
    elif overdue or awaiting:
        # The prioritization heading this whole fix exists for: the calendar
        # genuinely has nothing booked, but that is not the same as "nothing
        # to do" -- say so, and say how many applications need a visit.
        parts.append(
            f"<div class='table-intro'><strong>Field Visit Prioritization{_e(scope_suffix)}</strong> "
            f"-- nothing is booked on the calendar, but {len(overdue) + len(awaiting)} "
            f"application(s) need a field visit.</div>")
    else:
        parts.append(f"<div class='table-intro'>{_e(f'You have no upcoming field visits on the calendar{scope_suffix}.')}</div>")

    if overdue:
        parts.append(f"<div class='table-intro'><strong>Overdue scheduled visits</strong> -- "
                      f"{len(overdue)} visit(s) already past their scheduled date, most overdue first "
                      f"-- do these first:</div>" + _visit_table_html(overdue, "overdue"))

    if awaiting:
        parts.append(f"<div class='table-intro'><strong>Applications awaiting a field visit</strong> -- "
                      f"{len(awaiting)} application(s), longest-waiting first:</div>"
                      + _visit_table_html(awaiting, "awaiting"))
    elif not overdue and not scheduled:
        parts.append("<div class='table-intro'>No ISD application on your desk is waiting for a visit "
                     "(NISD applications do not need one).</div>")

    # "In which block?" -- answer the where directly rather than leaving it to be
    # read off the list. Plain HTML text, never a bulleted/labelled line a
    # plain-text heuristic elsewhere could mistake for its own table row.
    blocks, wards = sd.get("blocks") or {}, sd.get("wards") or {}
    if blocks or wards:
        def _top(counts, noun):
            ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            return ", ".join(f"{noun} {_e(str(name))} ({count})" for name, count in ordered)
        if focus == "ward" and wards:
            parts.append(f"<div class='table-intro'>Where: {_top(wards, 'ward')}.</div>")
        elif blocks:
            where_line = _top(blocks, "block")
            if wards:
                where_line += f" — {_top(wards, 'ward')}"
            parts.append(f"<div class='table-intro'>Where: {where_line}.</div>")

    return "".join(parts)


async def _fetch_last_application(db, message: str, officer, ward_number=None,
                                  block_number=None) -> dict:
    """Resolve "my last / previous application" into one application's record.

    A status word can mean two different things. "My last approved application"
    filters the officer's history down to approved files and takes the most
    recent of those. "Is my previous application approved?" asks about the most
    recent application whatever its status, and wants a yes or no -- answering it
    with the most recent *approved* file would confirm something that was never
    true. parse_last_application_query() tells the two apart; when it is the
    second, the application that was actually asked for is fetched first and the
    most recent match for the named status is looked up alongside it, so the
    answer can say what the real status is and still point at the file the
    officer probably wanted.
    """
    request = parse_last_application_query(message) or {}
    status = request.get("status")
    app_type = request.get("app_type")
    yes_no = bool(request.get("yes_no"))

    if yes_no:
        structured = await get_last_application(
            db, officer, status=None, application_type=app_type,
            ward_number=ward_number, block_number=block_number)
        structured = dict(structured)
        if structured.get("found") and structured.get("status") != status:
            alternative = await get_last_application(
                db, officer, status=status, application_type=app_type,
                ward_number=ward_number, block_number=block_number)
            structured["alternative"] = alternative if alternative.get("found") else None
    else:
        structured = dict(await get_last_application(
            db, officer, status=status, application_type=app_type,
            ward_number=ward_number, block_number=block_number))

    structured["yes_no"] = yes_no
    structured["asked_status"] = status
    structured["asked_type"] = app_type
    structured["asked_field"] = request.get("field")
    structured["asked_ward"] = ward_number
    structured["asked_block"] = block_number
    structured["query_type"] = "Most Recent Application"
    return structured


def _last_app_one_liner(sd: dict, language: str) -> str:
    """The one-sentence description of an application, shared by both branches."""
    app_no = sd.get("application_number", "N/A")
    app_type = sd.get("type") or "N/A"
    survey = sd.get("survey_no") or "N/A"
    subdiv = sd.get("subdivision_number")
    if subdiv and subdiv not in ("None", "N/A"):
        survey = f"{survey}/{subdiv}" if "/" not in str(survey) else survey
    applicant = sd.get("applicant_name") or "N/A"
    acted = (sd.get("last_action_at") or "")[:10] or sd.get("submission_date") or "N/A"
    if language in ("ta", "tanglish"):
        return (f"{app_no} ({app_type}, கணக்கெண் {survey}, விண்ணப்பதாரர் {applicant}) — "
                f"கடைசி நடவடிக்கை {acted}")
    return (f"{app_no} ({app_type}, survey {survey}, applicant {applicant}) — "
            f"last action {acted}")


# The fields an officer asks for in the same breath as the reference itself --
# "what was the area of my last approved application". Values come out of the
# get_application_detail() payload the lookup already returned, so no second
# query runs and the number in the answer is the number in the record.
_LAST_APP_FIELD_LABELS = {
    "area": ("Area", "பரப்பளவு"),
    "applicant": ("Applicant", "விண்ணப்பதாரர்"),
    "mobile": ("Mobile", "தொலைபேசி"),
    "address": ("Address", "முகவரி"),
    "fee": ("Fee", "கட்டணம்"),
    "patta": ("Patta number", "பட்டா எண்"),
    "can": ("CAN number", "CAN எண்"),
    "subdivision": ("Sub-divisions", "உட்பிரிவுகள்"),
    "survey": ("Survey number", "கணக்கெண்"),
    "deed": ("Sale deed", "கிரய பத்திரம்"),
    "reason": ("Declared reason", "அறிவிக்கப்பட்ட காரணம்"),
    # A noun, not a participle: the label is read as "<label> of your most
    # recent application", where "Submitted on" produced "Submitted on of your
    # most recent approved application".
    "submission_date": ("Submission date", "சமர்ப்பித்த தேதி"),
    "decision_date": ("Decision date", "முடிவு தேதி"),
    "channel": ("Submission channel", "சமர்ப்பிப்பு வழி"),
}


def _last_app_field_value(sd: dict, field: str) -> Optional[str]:
    """The asked-for field as one printable string, or None when unrecorded.

    An application carries three different areas -- the parcel's total, the
    total of the sub-divisions proposed on it, and the parcel's own total. `area_sqm` already resolves that precedence in
    get_application_detail(); the parcel total is added alongside it when the
    two differ, because "the area of the application" and "the area of the
    survey number" are not the same figure and an officer checking one against
    the other needs both.
    """
    def _sqm(value):
        try:
            return f"{float(value):,.2f} sq.m"
        except (TypeError, ValueError):
            return None

    if field == "area":
        survey_no = sd.get("survey_no", "N/A")
        survey_total = _sqm(sd.get("survey_total_area_sqm"))
        proposed = _sqm(sd.get("proposed_total_area_sqm"))
        primary = _sqm(sd.get("area_sqm")) or proposed or survey_total
        if not primary:
            return None
        # Where the figure came from matters: an application that has no
        # sub-division areas of its own reports the parcel's total, and saying
        # so keeps it from reading as an area the application itself set.
        if proposed and primary == proposed:
            count = sd.get("proposed_sub_divisions_count") or 0
            note = f"across {count} proposed sub-division(s)" if count else "across the proposed sub-divisions"
            if survey_total and survey_total != primary:
                note += f"; survey {survey_no} totals {survey_total}"
        else:
            note = (f"the total area of survey {survey_no} — the application "
                    f"records no separate sub-division area")
        return f"{primary} ({note})"

    if field == "applicant":
        return sd.get("applicant_name")
    if field == "mobile":
        return sd.get("applicant_mobile")
    if field == "address":
        return sd.get("applicant_address") or sd.get("applicant_permanent_address")
    if field == "fee":
        amount = sd.get("fee_amount")
        if amount is None:
            return None
        parts = [f"₹{float(amount):,.2f}"]
        if sd.get("payment_mode"):
            parts.append(f"paid by {sd['payment_mode']}")
        if sd.get("challan_number"):
            parts.append(f"challan {sd['challan_number']}")
        return ", ".join(parts)
    if field == "patta":
        return sd.get("new_patta_number") or sd.get("patta_number")
    if field == "can":
        return sd.get("can_number")
    if field == "subdivision":
        proposed = sd.get("proposed_sub_divisions") or []
        if proposed:
            return ", ".join(
                str(p.get("proposed_sub_division_no")) for p in proposed
                if isinstance(p, dict) and p.get("proposed_sub_division_no"))
        included = sd.get("included_subdivisions")
        return None if included in (None, "", "None") else included
    if field == "survey":
        survey = sd.get("survey_no")
        if not survey:
            return None
        included = sd.get("included_subdivisions")
        if included and included not in ("None", "N/A"):
            return f"{survey} (sub-divisions {included})"
        return str(survey)
    if field == "deed":
        deed = sd.get("sale_deed_number")
        if not deed:
            return None
        return f"{deed} ({'registered' if sd.get('sale_deed_registered') else 'not registered'})"
    if field == "reason":
        return sd.get("declared_reason")
    if field == "submission_date":
        return sd.get("submission_date")
    if field == "decision_date":
        # The date the file was approved or rejected, from the workflow hop
        # that closed it. Open files have none, and the submission date is not
        # a substitute -- returning None lets the caller say so.
        day = sd.get("decision_date")
        if not day:
            return None
        verb = "rejected" if (sd.get("status") or "").lower() == "rejected" else "approved"
        by = sd.get("decision_by")
        return f"{day} ({verb}{f' by {by}' if by else ''})"
    if field == "channel":
        return sd.get("source_name") or sd.get("submission_channel")
    return None


def _render_last_application_answer(sd: dict, language: str) -> str:
    """Deterministic answer for "my last / previous application" questions."""
    is_tamil = language in ("ta", "tanglish")
    asked_status = sd.get("asked_status")
    asked_type = sd.get("asked_type")
    labels = _LAST_APP_STATUS_LABELS_TA if is_tamil else _LAST_APP_STATUS_LABELS
    status_label = labels.get(asked_status, asked_status or "")
    type_label = f"{asked_type} " if asked_type else ""

    # The geography is part of the question ("my last approved application in
    # block 0015"), so the answer names it -- otherwise a scoped "none on
    # record" reads as though the officer had no approved application at all.
    _geo_bits = []
    if sd.get("asked_ward"):
        _geo_bits.append(f"Ward {sd['asked_ward']}")
    if sd.get("asked_block"):
        _geo_bits.append(f"Block {sd['asked_block']}")
    geo_label = (f" in {' / '.join(_geo_bits)}" if _geo_bits else "")
    geo_label_ta = (f"{' / '.join(_geo_bits)}-இல் " if _geo_bits else "")

    if not sd.get("found"):
        if sd.get("error"):
            return ("Sorry, I could not read your application history just now."
                    if not is_tamil else
                    "உங்கள் விண்ணப்ப வரலாற்றை இப்போது படிக்க முடியவில்லை.")
        if is_tamil:
            if asked_status:
                return f"உங்கள் பதிவில் {geo_label_ta}{status_label} {type_label}விண்ணப்பம் எதுவும் இல்லை."
            return f"உங்கள் பெயரில் {geo_label_ta}விண்ணப்பம் எதுவும் பதிவாகவில்லை."
        if asked_status:
            return f"You have no {status_label} {type_label}applications{geo_label} on record."
        return f"You have no {type_label}applications{geo_label} on record."

    current_status = labels.get(sd.get("status"), sd.get("status") or "N/A")
    stage = sd.get("stage") or "N/A"
    one_liner = _last_app_one_liner(sd, language)

    # "What was the area of my last approved application?" -- lead with the
    # field that was asked for, then name the application it came from so the
    # figure can be checked against the right file.
    asked_field = sd.get("asked_field")
    if asked_field and not sd.get("yes_no"):
        label_en, label_ta = _LAST_APP_FIELD_LABELS.get(
            asked_field, (asked_field.replace("_", " ").capitalize(), asked_field))
        value = _last_app_field_value(sd, asked_field)
        which = (f"{status_label} {type_label}".strip() + " ") if asked_status else type_label
        # An open file has no decision date, and "Decision date is not
        # recorded" reads as a lost value rather than as the state of the file.
        _undecided = (asked_field == "decision_date" and not value
                      and (sd.get("status") or "").lower() not in ("approved", "rejected"))
        if _undecided:
            _st = labels.get(sd.get("status"), sd.get("status") or "open")
            if is_tamil:
                return (f"உங்கள் கடைசி {which}விண்ணப்பம் இன்னும் முடிவு செய்யப்படவில்லை "
                        f"(நிலை: {_st}). அது {one_liner}.")
            return (f"Your most recent {which}application has not been decided yet "
                    f"(status: {_st}). That is {one_liner}.")
        if is_tamil:
            head = (f"உங்கள் கடைசி {which}விண்ணப்பத்தின் {label_ta}: {value}."
                    if value else
                    f"உங்கள் கடைசி {which}விண்ணப்பத்தில் {label_ta} பதிவு செய்யப்படவில்லை.")
            return f"{head} அது {one_liner}."
        head = (f"{label_en} of your most recent {which}application: {value}."
                if value else
                f"{label_en} is not recorded on your most recent {which}application.")
        answer = f"{head} That is {one_liner}."
        if asked_field == "reason" and sd.get("status") == "rejected":
            answer += (f" For why it was rejected, ask: why was "
                       f"{sd.get('application_number', 'it')} rejected?")
        return answer

    if sd.get("yes_no"):
        # "Is my previous application approved?" — a yes or a no about THIS file.
        if sd.get("status") == asked_status:
            if is_tamil:
                return f"ஆம் — உங்கள் கடைசி விண்ணப்பம் {one_liner} {status_label} நிலையில் உள்ளது."
            return f"Yes — your most recent application is {status_label}: {one_liner}."
        alternative = sd.get("alternative")
        if is_tamil:
            answer = (f"இல்லை — உங்கள் கடைசி விண்ணப்பம் {one_liner} தற்போது "
                      f"{current_status} ({stage} கட்டம்).")
            if alternative:
                answer += (f" உங்கள் கடைசி {status_label} விண்ணப்பம்: "
                           f"{_last_app_one_liner(alternative, language)}.")
            elif asked_status:
                answer += f" {status_label} விண்ணப்பம் எதுவும் உங்கள் பதிவில் இல்லை."
            return answer
        answer = (f"No — your most recent application is {current_status} "
                  f"(stage: {stage}): {one_liner}.")
        if alternative:
            answer += (f" Your most recent {status_label} application is "
                       f"{_last_app_one_liner(alternative, language)}.")
        elif asked_status:
            answer += f" You have no {status_label} {type_label}applications on record."
        return answer

    if is_tamil:
        prefix = (f"உங்கள் கடைசி {geo_label_ta}{status_label} {type_label}விண்ணப்பம்"
                  if asked_status else f"உங்கள் கடைசி {geo_label_ta}{type_label}விண்ணப்பம்")
        answer = f"{prefix}: {one_liner}."
        if not asked_status:
            answer += f" நிலை: {current_status} ({stage} கட்டம்)."
        return answer
    prefix = (f"Your most recent {status_label} {type_label}application{geo_label} is"
              if asked_status else f"Your most recent {type_label}application{geo_label} is")
    answer = f"{prefix} {one_liner}."
    if not asked_status:
        answer += f" Status: {current_status} (stage: {stage})."
    answer += " Ask me anything about it — applicant, documents, survey, or workflow."
    return answer


_REG_FIELD_CUES: List[Tuple[str, re.Pattern, re.Pattern]] = [
    # Checked before the single-field "ward" / "block" cues below so a
    # message naming BOTH ("ward and block", "ward/block") still gets the
    # combined line, not just one of them.
    ("ward_block", re.compile(r"\bward\s*(?:and|/|&)\s*block\b|\bblock\s*(?:and|/|&)\s*ward\b", re.I),
     re.compile(r"வார்டு.{0,15}(?:மற்றும்|/|&).{0,15}பிளாக்")),
    # "which ward is it in?" / "which block is it?" used to share one
    # combined "ward_block" topic, so asking for just the block repeated the
    # ward too -- the same full sentence twice for two different questions.
    ("ward", re.compile(r"\b(?:entha|endha|which)\s+warr?d\b|\bwarr?d\s+la\b|\bward\s+(?:number|no)\b|"
                         # "what is its ward?" / "what ward is it in?" -- the plain phrasing, not just
                         # "which ward" / "ward number"; without this it fell through to the full card.
                         r"\bwhat\s+(?:is\s+)?(?:its\s+)?warr?d\b|\bwarr?d\s+is\s+it\s+in\b", re.I),
     re.compile(r"எந்த\s*வார்டு")),
    ("block", re.compile(r"\b(?:entha|endha|which)\s+bl?o?c?k\b|\bblook\b|\bblcok\b|\bblock\s+(?:number|no)\b|"
                          r"\bwhat\s+(?:is\s+)?(?:its\s+)?block\b|\bblock\s+is\s+it\s+in\b", re.I),
     re.compile(r"எந்த\s*பிளாக்")),
    ("channel", re.compile(r"\b(?:entha|enna)\s+(?:chan\w*|so?u?rc\w*)\b|\bmoolama\b|\bhow\s+was\b.{0,30}\bsubmit\w*|\bsubmis?sion\s+(?:source|sorce)\b|\bso?u?rc\w*\s+name\b|\bhow\s+did\s+it\s+reach\b|\bhow\s+was\s+it\s+submitted\b|\bwhich\s+channel\b|\bsubmission\s+channel\b|\bhow\s+did\s+it\s+(?:come|arrive)\b|\bcame\s+(?:in\s+)?through\b|\bwhat\s+(?:is\s+the\s+)?(?:source|channel)\b", re.I),
     re.compile(r"எந்த\s*மூலம்|எவ்வாறு\s*வந்தது|மூலம்\s*சமர்ப்பிக்கப்பட்டது")),
    ("submitted_date", re.compile(r"\bwhen\s+was\b.{0,30}\bsubmit\w*|\bsubmis+io?n?\s+d[ae]te\b|\bdate\s+it\s+was\s+lodged\b|\bwhen\s+was\s+it\s+(?:submitted|lodged|filed)\b|\bsubmission\s+d[ae]te\b|\bdate\s+(?:lodged|filed|submitted)\b|\bwhen\s+(?:did\s+(?:it|this)\s+reach|was\s+this\s+filed)\b|"
                                    # "on what date was X submitted?" / "what date was X filed?" -- the
                                    # same question as "when was X submitted", just led with "what date"
                                    # instead of "when". Matched identically whether X is a real
                                    # application number, "it", or an unfilled "[APP_ID]"-shaped
                                    # placeholder: the gate resolves WHICH file this is about from the
                                    # number or from context before this cue list is ever reached, so
                                    # this regex only has to recognise the QUESTION shape.
                                    r"\bwhat\s+date\b.{0,40}\b(?:submit\w*|lodged|filed)\b|"
                                    # Tanglish ("eppo submit pannanga?") and the typo'd "dte" ("submit
                                    # dte?", "submision dte") -- "submis+io?n?\s+d[ae]te" above already
                                    # needs the word "date"/"dte" right after "submission"; a bare
                                    # "eppo ... submit" or "submit ... pannanga" carries neither.
                                    r"\beppo\b.{0,25}\bsubmit\w*\b|\bsubmit\w*\b.{0,15}\bpannanga\b", re.I),
     re.compile(r"சமர்ப்பிக்கப்பட்ட\s*தேதி|எப்போது\s*சமர்ப்பிக்கப்பட்டது|தாக்கல்\s*செய்த\s*தேதி")),
    # "field visit date" / "field vist date" (typo) on a register-only file -- field visits are an
    # ISD-only (0154) concept (sla.py: FIELD_VISIT_TYPES = ("ISD",)), so every other service code,
    # NISD included, has none. Without this topic the question fell through all the way to the full
    # details card, same failure class as overdue_flag / priority_flag above.
    ("field_visit_date", re.compile(r"\bfield[\s-]?vis\w*\s*(?:date|dte)\b|\bvisit\s*(?:date|dte)\b", re.I),
     re.compile(r"கள\s*ஆய்வு\s*தேதி")),
    # "last update date", "when was it (last) updated" -- a DIFFERENT column
    # from submitted_date (appl_log_urban.update_dt, not the submission date).
    # This used to have no cue at all: the question instead reached
    # `_record_field_turn`'s `get_application_detail()` lookup earlier in the
    # pipeline, which only ever looks in `applications` (ISD/NISD) -- so on a
    # register-only file it reported "application ... was not found" even
    # though the file plainly exists and was just discussed.
    ("last_updated", re.compile(r"\b(?:last\s+)?updat\w*\s+date\b|\bwhen\s+was\s+it\s+(?:last\s+)?updat\w*\b|"
                                 r"\bdate\s+(?:it\s+was\s+)?(?:last\s+)?updat\w*\b|\bwhat\s+date\b.{0,30}\bupdat\w*\b", re.I),
     re.compile(r"கடைசியாக\s*புதுப்பிக்கப்பட்ட\s*தேதி|புதுப்பிப்பு\s*தேதி")),
    # "status of <app number>" is handled separately in _register_only_field_answer
    # (the app number isn't known when this module-level table is built).
    ("status", re.compile(r"\bstatu[sz]?\s+enna\b|\boda\s+statu|\bwhat\s+is\s+(?:its|the)\s+status\b|\bcurrent\s+status\b|\bstatus\s+of\s+it\b", re.I),
     re.compile(r"நிலை\s*என்ன")),
    ("survey", re.compile(r"\bsurv\w*\s+(?:no|number|numbr)\b|\bwhat\s+survey\b|\bsurvey\s+(?:number|no)\b|\bwhich\s+survey\b", re.I),
     re.compile(r"எந்த\s*கணக்கெண்|கணக்கெண்\s*என்ன")),
    ("stage", re.compile(r"\b(?:entha|endha)\s+st\w*ge?\b|\bipo\s+entha\b|\bcurrent\s+st\w*ge\b|\bwhat\s+stage\b|\bwhich\s+(?:stage|desk)\b|\bwhere\s+is\s+it\s+now\b", re.I),
     re.compile(r"எந்த\s*கட்டம்|இப்போது\s*எங்கே")),
    # "which service code under?" / "what service code is this?" asks about THIS
    # file's own code -- it used to carry no cue at all, so it fell through to the
    # full details card like every other unhandled register-only follow-up.
    ("service_code", re.compile(r"\b(?:which|what)\s+service\s*(?:code|coad|cod)\b|\bservice\s*code\s+(?:under|is\s+(?:it|this))\b|"
                                 r"\bwhich\s+code\s+(?:is\s+it\s+)?under\b|\bunder\s+which\s+(?:service\s*)?code\b|\bwhat\s+code\s+is\s+(?:it|this)\s+under\b", re.I),
     re.compile(r"எந்த\s*சேவை\s*குறியீடு|சேவை\s*குறியீடு\s*என்ன")),
    # "what is the service name" / "which service is this" asks for the
    # official name (e.g. "F-Line (Urban Demarcation)"), not the bare code --
    # same gap as service_code, same full-card fallback it used to fall into.
    ("service_name", re.compile(r"\b(?:what|which)\s+(?:is\s+(?:the\s+)?)?service\s*(?:name|type)\b|\bservice\s*(?:name|type)\s+is\s+(?:it|this)\b|"
                                 r"\bwhich\s+service\s+is\s+(?:it|this)\b|\bwhat\s+service\s+is\s+(?:it|this)\b", re.I),
     re.compile(r"சேவை\s*பெயர்\s*என்ன|எந்த\s*சேவை")),
    # "what is the submission IP" fell through to the full details card too --
    # the card doesn't even show an IP row, so that answer never actually
    # answered the question asked.
    ("submission_ip", re.compile(r"\bsubmission\s*ip\b|\bip\s*(?:address|addres)\b|\bsubmitted\s+from\s+(?:which\s+)?ip\b|\bwhat\s+ip\b", re.I),
     re.compile(r"ஐபி\s*முகவரி|சமர்ப்பிப்பு\s*ஐபி")),
    # "marked overdue?" / "marked as priority?" -- both are applications.is_overdue
    # / applications.priority, columns this register-only row does not carry at
    # all (no field-visit deadline tracking, no priority flag for anything but
    # ISD/NISD). Without this, `parse_intent` reads "overdue"/"priority" as the
    # LIST intents (overdue_applications / highest_priority_applications), the
    # gate below treats those as application-specific, resolves the file from
    # context anyway, and fell straight to the full 16-field card -- a listing
    # keyword hijacking a single-file yes/no question.
    ("overdue_flag", re.compile(r"\bmarked\s*(?:as\s+)?overdue\b|\bis\s+it\s+overdue\b|\boverdue\b", re.I),
     re.compile(r"காலதாமத")),
    ("priority_flag", re.compile(r"\bmarked\s*(?:as\s+)?(?:high\s+)?priority\b|\bis\s+it\s+(?:a\s+)?priority\b|\bhigh\s+priority\b|\burgent\b", re.I),
     re.compile(r"முன்னுரிமை")),
    # "reached the completed stage?" / "has it reached completion?" / "is it at
    # the final stage?" -- a yes/no against this row's own coarse `stage`
    # (Open/Completed), not a request to see anything else.
    ("reached_stage", re.compile(r"\breach(?:ed|ing)?\s+(?:the\s+)?(?:completed?|completion|closed|final|last)\s*(?:stage)?\b|"
                                  r"\bis\s+it\s+(?:at\s+|in\s+)?(?:the\s+)?(?:completed?|closed|final)\s+stage\b", re.I),
     re.compile(r"முடிந்த\s*கட்டம்|இறுதி\s*கட்டம்")),
    # "relative name of the owner" / "relation of the owner" -- the flat register
    # row (ServiceRegisterEntry) carries no owner/applicant detail at all; that is
    # a fact about this row, read deterministically here rather than falling
    # through to the full workflow card (which doesn't answer the question either
    # -- most register-only service codes have no owner table to join in the
    # first place). Reached only when a more specific module (natham_qa, uamod_qa,
    # fline_qa, ...) hasn't already claimed the message for a service code that
    # DOES carry owner rows.
    ("owner_relative", re.compile(r"\b(?:relative|relation(?:ship)?)\b.{0,20}\bowner\b|\bowner\b.{0,20}\b(?:relative|relation(?:ship)?)\b|"
                                   r"\bowner'?s?\s+(?:name|relative|relation)\b", re.I),
     re.compile(r"உரிமையாளர்.{0,15}(?:உறவினர்|உறவு)|(?:உறவினர்|உறவு).{0,15}உரிமையாளர்")),
]


def _register_only_field_answer(message: str, row: Dict[str, Any], app_number: str, is_tamil: bool) -> Optional[str]:
    """A register-only file (any service code but 0153/0154) has no per-field
    handler the way ISD/NISD does -- every follow-up ("its ward and block?",
    "how did it reach us", "date it was lodged") fell through to the SAME full
    40-field workflow card every time, because `resolve_application_reference`
    had only two shapes for this path: the specific record modules (cmflow_qa
    etc, gated by `_cm_strong`) or the whole card. A short follow-up matches
    neither, so the full card -- unchanged -- answered a question about one
    field. This is the missing short answer in between, read straight off the
    row `get_applications_by_numbers` already fetched, no extra query."""
    msg = (message or "").strip()
    if not msg:
        return None
    _yn = _status_yes_no(msg.lower(), app_number, row.get("status"), row.get("stage"), is_tamil)
    if _yn:
        return _yn
    _asked_ch = [c for c, rx in (("CSC", r"\bcsc\b|common\s+service"), ("citizen", r"\bcit\w*z\w*n\b|\bportal\b"),
                                 ("sub_registrar", r"\bsub[\s-]?regi?st\w*|\bsro\b|\bregistrar\b")) if re.search(rx, msg, re.IGNORECASE)]
    if _asked_ch and re.search(r"\b(?:submit\w*|came|come|through|throug|via|channel)\b", msg, re.IGNORECASE):
        ch = row.get("submission_channel") or ""
        hit = ch in _asked_ch
        lab = {"CSC": "a CSC counter", "citizen": "the citizen portal", "sub_registrar": "the Sub-Registrar"}.get(ch, ch or "an unrecorded channel")
        return (f"{'ஆம்' if hit else 'இல்லை'} — {app_number} {ch or '-'} வழியாக வந்தது." if is_tamil
                else f"{'Yes' if hit else 'No'} — {app_number} came in through {lab}.")
    # "status of 2026/0178/02/000309" -- an officer naming the file directly
    # instead of saying "it"/"the" matches none of the "status" alternatives
    # in _REG_FIELD_CUES (all of them require a pronoun/determiner), so the
    # question fell through all the way to the full _othercode_detail.card()
    # -- the same fallback this whole cue table exists to avoid. Checked here
    # (not in the static table) because app_number is only known per-call.
    if re.search(r"\bstatus\b", msg, re.IGNORECASE) and app_number and app_number in msg:
        st, stg = row.get("status") or "not recorded", row.get("stage") or "not recorded"
        return (f"{app_number} நிலை: {st} (கட்டம்: {stg})." if is_tamil
                else f"{app_number} is {st} (stage: {stg}).")
    for topic, en_re, ta_re in _REG_FIELD_CUES:
        if not (en_re.search(msg) or ta_re.search(msg)):
            continue
        if topic == "ward_block":
            w, b = row.get("ward_number") or "N/A", row.get("block_number") or "N/A"
            return (f"{app_number} வார்டு {w}, பிளாக் {b}-ல் உள்ளது."
                    if is_tamil else f"{app_number} is in ward {w}, block {b}.")
        if topic == "ward":
            w = row.get("ward_number") or "N/A"
            return (f"{app_number} வார்டு {w}-ல் உள்ளது." if is_tamil
                    else f"{app_number} is in ward {w}.")
        if topic == "block":
            b = row.get("block_number") or "N/A"
            return (f"{app_number} பிளாக் {b}-ல் உள்ளது." if is_tamil
                    else f"{app_number} is in block {b}.")
        if topic == "channel":
            ch = row.get("submission_channel") or "not recorded"
            return (f"{app_number} {ch} வழியாக வந்தது." if is_tamil
                    else f"{app_number} came in through {ch}.")
        if topic == "submitted_date":
            d = row.get("submission_date") or "not recorded"
            return (f"{app_number} {d} அன்று சமர்ப்பிக்கப்பட்டது." if is_tamil
                    else f"{app_number} was submitted on {d}.")
        if topic == "field_visit_date":
            # Same rule `overdue_flag` above states for this row: field visits
            # are scheduled only for ISD (0154); every other service code,
            # NISD included, has none -- not a gap in what this register
            # tracks, a fact about which files get a field visit at all.
            code = row.get("service_code") or "this service code"
            return (f"{app_number}-க்கு கள ஆய்வு பதிவு இல்லை -- கள ஆய்வு ISD (0154) விண்ணப்பங்களுக்கு மட்டுமே "
                    f"நடத்தப்படும், சேவை குறியீடு {code}-க்கு அல்ல." if is_tamil else
                    f"No field-visit record is held for {app_number} -- field visits are scheduled only for "
                    f"ISD (0154) applications, not for service code {code}.")
        if topic == "last_updated":
            d = row.get("last_updated_date")
            if not d:
                return (f"{app_number}-க்கு கடைசி புதுப்பிப்பு தேதி பதிவில் இல்லை." if is_tamil
                        else f"{app_number}'s last update date is not recorded.")
            return (f"{app_number} கடைசியாக {d} அன்று புதுப்பிக்கப்பட்டது." if is_tamil
                    else f"{app_number} was last updated on {d}.")
        if topic == "status":
            st, stg = row.get("status") or "not recorded", row.get("stage") or "not recorded"
            return (f"{app_number} நிலை: {st} (கட்டம்: {stg})." if is_tamil
                    else f"{app_number} is {st} (stage: {stg}).")
        if topic == "survey":
            sv, sd = row.get("survey_no") or "N/A", row.get("sub_division_no") or "-"
            return (f"{app_number} கணக்கெண் {sv}, உட்பிரிவு {sd}." if is_tamil
                    else f"{app_number} is for survey {sv}, sub-division {sd}.")
        if topic == "stage":
            stg = row.get("stage") or "not recorded"
            return (f"{app_number} தற்போது {stg} கட்டத்தில் உள்ளது." if is_tamil
                    else f"{app_number} is currently at the {stg} stage.")
        if topic == "service_code":
            code = row.get("service_code") or "not recorded"
            name = row.get("type") or ""
            return (f"{app_number} சேவை குறியீடு {code}" + (f" ({name})" if name and name != code else "") + "-ன் கீழ் வருகிறது." if is_tamil
                    else f"{app_number} is under service code {code}" + (f" ({name})" if name and name != code else "") + ".")
        if topic == "service_name":
            code = row.get("service_code") or ""
            name = row.get("type") or "not recorded"
            return (f"{app_number}-ன் சேவை: {name}" + (f" ({code})" if code else "") + "." if is_tamil
                    else f"{app_number}'s service is {name}" + (f" ({code})" if code else "") + ".")
        if topic == "submission_ip":
            ip = row.get("submission_ip") or (("பதிவு செய்யப்படவில்லை") if is_tamil else "not recorded")
            return (f"{app_number}-ன் சமர்ப்பிப்பு IP: {ip}." if is_tamil
                    else f"{app_number}'s submission IP is {ip}.")
        if topic == "overdue_flag":
            # Not a code-support gap: "overdue" is specifically the ISD field-visit
            # deadline (sla.py: FIELD_VISIT_TYPES = ("ISD",)) -- it does not apply to
            # NISD either, because NISD has no field visit. Worded so an officer
            # reads "this rule doesn't apply here", not "this code isn't handled".
            st, stg = row.get("status") or "not recorded", row.get("stage") or "not recorded"
            return (f"'காலதாமதம்' (Overdue) {app_number}-க்குப் பொருந்தாது -- இது ISD கள ஆய்வு காலக்கெடு விதி; "
                    f"NISD-க்கும் இந்தச் சேவைக்கும் கள ஆய்வே இல்லை. இந்த சேவைக்கு நிலை மற்றும் கட்டம் மட்டும் பதிவு: "
                    f"{st} ({stg})." if is_tamil else
                    f"'Overdue' does not apply to {app_number} -- it is specifically the ISD field-visit "
                    f"deadline, and this service has no field-visit requirement at all (the same as NISD). "
                    f"{app_number}'s own register entry carries status and stage: {st} ({stg}).")
        if topic == "priority_flag":
            # Likewise: the priority flag is an officer-set urgency marker kept on
            # the ISD/NISD desk queue (applications.priority_flag), not a thing
            # every service code either has or lacks support for.
            st, stg = row.get("status") or "not recorded", row.get("stage") or "not recorded"
            return (f"'முன்னுரிமை' குறி {app_number}-க்குப் பதிவு செய்யப்படவில்லை -- இது ISD/NISD மேசை வரிசையில் "
                    f"மட்டும் வைக்கப்படும் அதிகாரியின் குறி. இந்த சேவைக்கு நிலை மற்றும் கட்டம் மட்டும் பதிவு: "
                    f"{st} ({stg})." if is_tamil else
                    f"A priority flag is not kept for {app_number} -- it is an officer-set urgency marker on "
                    f"the ISD/NISD desk queue specifically. {app_number}'s own register entry carries status "
                    f"and stage: {st} ({stg}).")
        if topic == "reached_stage":
            stg = str(row.get("stage") or "")
            st = row.get("status") or "not recorded"
            reached = stg.strip().lower() == "completed"
            verdict = "ஆம்" if (reached and is_tamil) else "இல்லை" if is_tamil else ("Yes" if reached else "No")
            return (f"{verdict} — {app_number} தற்போது '{stg}' நிலையில் உள்ளது (நிலை: {st})." if is_tamil
                    else f"{verdict} -- {app_number} is currently at the '{stg}' stage (status: {st}).")
        if topic == "owner_relative":
            code = row.get("service_code") or "this service code"
            return (f"{app_number}-ன் பதிவேட்டு வரிசை உரிமையாளர் / உறவினர் விவரங்களை வைத்திருக்கவில்லை -- "
                    f"சேவை குறியீடு {code} பதிவில் விண்ணப்பக்காரர் / உரிமையாளர் பட்டியல் இல்லை. வைத்திருப்பவை: "
                    f"நிலை, கட்டம், கணக்கெண், வார்டு / பிளாக், சமர்ப்பிப்பு வழி மற்றும் தேதி." if is_tamil else
                    f"The register row for {app_number} does not carry owner / relative detail -- service code "
                    f"{code} has no applicant or owner table behind it in this register. What is held: status, "
                    f"stage, survey number, ward / block, submission channel and date.")
    return None


async def resolve_application_reference(
    db,
    message: str,
    chat_history: list,
    officer,
    intent: str,
    session_id: str = None,
) -> Dict[str, Any]:
    """
    Single gate every application-specific request passes through before any
    handler, RAG lookup or LLM call runs.

    The rules it enforces, in order:
      * a number that cannot be parsed is reported as a format error;
      * a well-formed number that does not exist is reported as not found —
        never silently swapped for a near match;
      * a number that exists but sits outside the officer's jurisdiction is
        refused without disclosing anything about it;
      * near matches are offered as suggestions the officer must confirm;
      * a number recovered from an earlier turn is re-validated here, so a
        follow-up can only ever reuse an application already proven valid and
        accessible.

    Verdicts: ok | invalid_format | not_found | forbidden | needs_confirmation | none
    """
    from backend.services.postgres import lookup_application_access

    app_specific = intent in _APP_NUMBER_INTENTS

    # A token explicitly offered as an application number ("show application
    # INVALID-999") is malformed whatever the intent classifier decided.
    offered_bad = detect_offered_app_number_token(message)
    if offered_bad:
        return {"verdict": "invalid_format", "token": offered_bad}

    # The looser scan is only *reported* for application-specific intents; for
    # list/report intents a stray "12/05/2026" is a date, not a typo.
    if app_specific:
        bad_token = detect_invalid_app_number(message)
        if bad_token:
            return {"verdict": "invalid_format", "token": bad_token}

    # Existence and jurisdiction are checked whenever a number is present at
    # all, whatever the intent — otherwise an intent outside _APP_NUMBER_INTENTS
    # becomes a way around the jurisdiction check.
    app_number = extract_application_number(message)
    from_context = False

    # "my previous application" points at the officer's history, not at whatever
    # is on screen. The wording ("prev application", "last app") is also a
    # back-reference pattern, so without this the gate would resolve it against
    # the previous turn -- and refuse as ambiguous whenever that turn listed
    # several applications. The handler finds the right file in the database.
    if intent == "last_application" and not app_number:
        return {"verdict": "none"}

    # "is it okay to change the inspection date on my own?" -- "it" is a dummy
    # subject; the question is about the process, so there is no application to
    # resolve and nothing to ask "which one" about.
    if (not app_number and re.match(
            r"^\s*(?:is|are)\s+it\s+(?:okay|ok|allowed|possible|permitted|fine|alright)\b",
            message or "", re.IGNORECASE)):
        return {"verdict": "none"}

    # "now show details of both the applications" refers to every application in
    # the previous answer, not to one. There is no single referent to resolve
    # here, so the gate steps aside and the handler expands the whole list --
    # each number in it still goes through get_application_detail() with the
    # officer, so jurisdiction is enforced per application exactly as before.
    if not app_number and wants_details_of_listed(message):
        return {"verdict": "none"}

    if not app_number and _cm_strong(message, chat_history):
        # a record question with no number: the application the last answer was about (an answer
        # ends with "[number]"; a number quoted earlier in it, such as a merge id, is another file)
        for _m in reversed((chat_history or [])[-14:]):
            _mm = _cmflow_qa._APP_NO.search((_m.get("content") if isinstance(_m, dict) else str(_m)) or "")
            if _mm:
                app_number = _mm.group(0)
                from_context = True
                break

    if not app_number:
        # Context is consulted for application-specific intents, and for any
        # other intent when the officer explicitly pointed back ("show ITS field
        # visit", "what about THE OWNER of that application"). Without the second
        # case a follow-up silently widens into an unfiltered list query.
        back_reference = _has_application_back_reference(message)
        
        # If the intent is a generic list query, "the application" is too weak
        # to trigger a context resolution halt. We require a stronger pronoun.
        if back_reference and intent in _LIMITABLE_LIST_INTENTS:
            _strong = re.sub(r'(?<!\w)the\s+app(?:lication)?(?!\w)', '', message, flags=re.IGNORECASE)
            if not _has_application_back_reference(_strong):
                back_reference = False

        if not app_specific and not back_reference and intent not in _CONTEXT_CONTINUATION_INTENTS:
            return {"verdict": "none"}

        candidates, blocked = _app_numbers_in_recent_context(chat_history)
        if blocked:
            # The officer's most recent attempt to name an application was
            # malformed; ask for a valid one instead of silently falling back
            # to an application discussed before that attempt.
            return {"verdict": "ask_number"}
        if len(candidates) > 1:
            # The turn being referred back to named several applications, so
            # "it" genuinely has no single referent. Asking beats guessing.
            return {"verdict": "ambiguous_context", "suggestions": candidates}

        app_number = _extract_app_number_from_context(
            message, chat_history, allow_implicit_continuation=True
        )

        if not app_number and back_reference:
            # The officer's own turns name no application, but the previous
            # answer put one on screen. Only ever consulted when the officer
            # actually pointed back: a continuation intent alone ("who owns
            # survey 20") is a fresh question, and resolving it against the last
            # listing turned it into "which application do you mean?". "How do you tell THIS application is
            # from a CSC?" right after a one-row table points at that row --
            # asking for a number the officer can see in front of them is the
            # wrong answer. Quoted-back numbers (examples, "not found",
            # suggestions) are excluded by _is_quoted_back_answer, and whatever
            # is picked here is still re-validated below for existence and
            # jurisdiction like any typed number.
            listed = await _app_numbers_from_last_listing(db, session_id, chat_history)
            if listed:
                picked = _pick_from_listing(message, listed)
                if picked:
                    app_number = picked
                elif len(listed) == 1:
                    app_number = listed[0]
                else:
                    # Several rows on screen and no position named -- "it" has
                    # no single referent, so ask which instead of taking the
                    # first row.
                    return {"verdict": "ambiguous_context", "suggestions": listed}

        if not app_number and _cm_strong(message, chat_history):
            # a record question names no application: the one the conversation is about, from
            # either side of the last turns (an answer carries it as "[number]")
            for _m in reversed((chat_history or [])[-14:]):
                _mm = _cmflow_qa._APP_NO.search((_m.get("content") if isinstance(_m, dict) else str(_m)) or "")
                if _mm:
                    app_number = _mm.group(0)
                    break

        from_context = bool(app_number)

    if not app_number:
        if intent in _SINGLE_APP_INTENTS:
            return {"verdict": "ask_number"}
        return {"verdict": "none"}

    # Any other TAMILNILAM service code (0158 ...) lives in the register, not in `applications`.
    if app_number.count("/") >= 3 and app_number.split("/")[1] not in ("0153", "0154"):
        # a survey question carried along by an older application is not about that file
        # ("144 ku litigation iruka?" names survey 144 the Tanglish way)
        if from_context and re.search(r"\bsurvey\b[^0-9]{0,12}\d|\bsy\.?\s*\d|^\s*\d{1,4}(?:/\w+)?\s*(?:(?:ku|kku)\b|க்கு)", message or "", re.IGNORECASE):
            return {"verdict": "none"}
        _reg = await get_applications_by_numbers(db, officer, [app_number], include_rejected=True)
        if (_reg or {}).get("applications"):
            _m0 = message or ""
            if (_cm_strong(message, chat_history) or _WF_CUE.search(_m0) or _DECIDER_CUE.search(_m0)
                    # record questions the early hook answers for any service code: the applicant of an
                    # F-Line file, the attached documents, a 0165 file's full-field transfer row
                    or _appinfo_qa.applicant_keys(_m0) or (_DOCS_CUE.search(_m0) and not _DOCS_SKIP.search(_m0))
                    or (app_number.split("/")[1] == "0165" and _fulltransfer_qa.claims(_m0))):
                return {"verdict": "ok", "app_number": app_number, "from_context": from_context}
            _row0 = _reg["applications"][0]
            _is_ta0 = detect_language(message or "") in ("ta", "tanglish")
            _field0 = _register_only_field_answer(message, _row0, app_number, _is_ta0)
            if _field0:
                return {"verdict": "register_only", "app_number": app_number, "row": _row0, "text": _field0}
            try:
                _txt0 = _card_html.card(await _othercode_detail.card(db, app_number, _row0, _is_ta0), _is_ta0)
            except Exception:
                _txt0 = None
            return {"verdict": "register_only", "app_number": app_number, "row": _row0, "text": _txt0}
        else:
            from sqlalchemy import text as _sqltext
            _exists = (await db.execute(_sqltext("SELECT 1 FROM service_register_entries WHERE application_number = :a LIMIT 1"),
                                        {"a": app_number})).first()
            if _exists:
                return {"verdict": "forbidden", "app_number": app_number}

    facts = await lookup_application_access(db, app_number, officer)

    if facts["exists"] and facts["accessible"]:
        return {"verdict": "ok", "app_number": app_number, "from_context": from_context}

    # Anything below is a refusal. A number recovered from an earlier turn has
    # now failed re-validation, so it was never "previously confirmed as valid
    # and accessible" and must not be reused. Ask which application they mean
    # rather than answering about the stale one.
    if from_context:
        return {"verdict": "ask_number", "app_number": app_number}

    if facts["exists"] and not facts["accessible"]:
        return {"verdict": "forbidden", "app_number": app_number}

    if facts["candidates"]:
        return {
            "verdict": "needs_confirmation",
            "app_number": app_number,
            "suggestions": facts["candidates"],
        }

    return {
        "verdict": "not_found",
        "app_number": app_number,
        "suggestions": facts["recent"],
    }


def _register_card(app_no: str, r: Dict[str, Any], ta: bool) -> str:
    """The register row of an application under a service code other than ISD / NISD."""
    sub = r.get("sub_division_no")
    lines = [
        f"{app_no}: " + ("சேவை " if ta else "Service ") + f"{r.get('service_code')} - {r.get('type')}",
        ("நிலை: " if ta else "Status: ") + f"{r.get('status')} ({r.get('stage')})",
        ("சமர்ப்பித்த தேதி: " if ta else "Submitted: ") + f"{r.get('submission_date')}",
        ("சர்வே எண்: " if ta else "Survey no: ") + f"{r.get('survey_no')}" + (f" / {sub}" if sub not in (None, "", "0") else ""),
        ("வார்டு / பிளாக்: " if ta else "Ward / block: ") + f"{r.get('ward_number')} / {r.get('block_number')}",
        ("சமர்ப்பிப்பு வழி: " if ta else "Channel: ") + f"{r.get('submission_channel') or '-'}",
    ]
    return "\n".join(lines)


def build_application_gate_message(resolution: Dict[str, Any], officer, language: str) -> str:
    """Render the officer-facing text for a refusing verdict from resolve_application_reference()."""
    verdict = resolution.get("verdict")
    if verdict == "invalid_format":
        return build_invalid_app_number_message(resolution["token"], language)
    if verdict == "forbidden":
        return build_app_forbidden_message(resolution["app_number"], officer, language)
    if verdict == "needs_confirmation":
        return build_app_confirm_message(resolution["app_number"], resolution["suggestions"], language)
    if verdict == "register_only":
        return resolution.get("text") or _card_html.card(_register_card(resolution["app_number"], resolution["row"], language in ("ta", "tanglish")), language in ("ta", "tanglish"))
    if verdict == "ask_number":
        return ASK_FOR_APP_NUMBER[language if language in ASK_FOR_APP_NUMBER else "en"]
    if verdict == "ambiguous_context":
        return build_ambiguous_reference_message(resolution.get("suggestions") or [], language)
    if verdict == "not_found":
        return build_app_not_found_message(
            {
                "searched_number": resolution["app_number"],
                "suggestions": resolution.get("suggestions") or [],
            },
            language,
        )
    return ""


def _sorted_keywords(keywords: Dict[str, tuple]) -> List[tuple]:
    """Keyword items, longest first, so specific phrases win over generic words."""
    return sorted(keywords.items(), key=lambda x: len(x[0]), reverse=True)


def _split_keywords(sorted_kws: List[tuple]) -> tuple:
    """
    Split keywords into phrases and single words by TOKEN count, not by spaces —
    "sub-division" is a two-token phrase even though it has no space.

    Returns (phrases, singles) where:
      phrases = [(original_kw, first_token, field_key, field_label), ...]
      singles = {normalized_kw: (field_key, field_label, original_kw)}
    Duplicate single-word keys keep the first (longest) spelling.
    """
    phrases: List[tuple] = []
    singles: Dict[str, tuple] = {}
    for kw, (field_key, field_label) in sorted_kws:
        parts = extract_tokens(kw)
        if not parts:
            continue
        if len(parts) > 1:
            phrases.append((kw, parts[0], field_key, field_label))
        else:
            singles.setdefault(parts[0], (field_key, field_label, kw))
    return phrases, singles


def _typo_candidates(singles: Dict[str, tuple]) -> Dict[str, str]:
    """
    {normalized keyword: field_key} for keywords long enough to typo-match safely.
    Collapsing to field_key means several spellings of the SAME field are not
    mistaken for an ambiguity.
    """
    return {k: v[0] for k, v in singles.items() if len(k) >= 4}


def _resolve_typo_field(
    token: str,
    typo_kws: Dict[str, str],
    singles: Dict[str, tuple],
    threshold: float,
) -> Optional[tuple]:
    """
    Resolve a token to a single field via typo matching, refusing to guess when
    the token is equally close to keywords belonging to different fields
    (e.g. 'mail' vs 'mall').
    """
    entry = resolve_unique_entry(token, typo_kws, min_ratio=threshold, keys_normalized=True)
    if entry is None:
        return None
    return singles[entry[0]]


def _fuzzy_match_keywords(message_lower: str, keywords: Dict[str, tuple], threshold: float = 0.75) -> Optional[tuple]:
    """
    Match field keywords with priority for multi-word phrases, word-boundary precision, and typo tolerance.

    Precision order: multi-word phrase → exact token → unambiguous typo.
    """
    if not message_lower or not keywords:
        return None

    msg_tokens = extract_tokens(message_lower)
    if not msg_tokens:
        return None

    phrases, singles = _split_keywords(_sorted_keywords(keywords))
    token_set = set(msg_tokens)

    # Pass 1: Multi-word phrase matching (longest phrase first), token-boundary strict
    for kw, first_tok, field_key, field_label in phrases:
        if match_phrase(msg_tokens, kw):
            return (field_key, field_label, kw)

    # Pass 2: Exact token match (longest keyword first, as before)
    for kw_norm, entry in singles.items():
        if kw_norm in token_set:
            return entry

    # Pass 3: Unambiguous typo match for tokens with length >= 4
    typo_kws = _typo_candidates(singles)
    for token in msg_tokens:
        if len(token) < 4 or token in singles:
            continue
        entry = _resolve_typo_field(token, typo_kws, singles, threshold)
        if entry:
            logger.info(f"Fuzzy matched token '{token}' to keyword '{entry[2]}'")
            return entry

    return None


def _fuzzy_match_all_fields(message_lower: str, keywords: Dict[str, tuple], threshold: float = 0.75) -> List[tuple]:
    """
    Find ALL matched unique fields in the message (e.g. for combined multi-field queries).
    Returns list of (field_key, field_label, matched_kw), preserving order of appearance in message.
    """
    if not message_lower or not keywords:
        return []

    msg_norm = normalize_text(message_lower)
    msg_tokens = extract_tokens(msg_norm)
    if not msg_tokens:
        return []

    seen_fields = set()
    matches = []
    big = len(msg_norm) + 1  # sorts unlocatable matches last instead of first

    def _pos(needle: str) -> int:
        idx = msg_norm.find(needle)
        return idx if idx >= 0 else big

    phrases, singles = _split_keywords(_sorted_keywords(keywords))
    token_set = set(msg_tokens)

    # Pass 1: Multi-word phrase matching, token-boundary strict.
    # Position is anchored on the phrase's first token, because the raw keyword
    # may use a different separator than the message ("sub-division" vs "sub division").
    for kw, first_tok, field_key, field_label in phrases:
        if field_key not in seen_fields and match_phrase(msg_tokens, kw):
            seen_fields.add(field_key)
            matches.append((field_key, field_label, kw, _pos(first_tok)))

    # Pass 2: Exact token match
    for kw_norm, (field_key, field_label, orig_kw) in singles.items():
        if kw_norm in token_set and field_key not in seen_fields:
            seen_fields.add(field_key)
            matches.append((field_key, field_label, orig_kw, _pos(kw_norm)))

    # Pass 3: Unambiguous typo match for tokens with length >= 4
    typo_kws = _typo_candidates(singles)
    for token in msg_tokens:
        if len(token) < 4 or token in singles:
            continue  # exact keywords were already handled above
        entry = _resolve_typo_field(token, typo_kws, singles, threshold)
        if entry and entry[0] not in seen_fields:
            seen_fields.add(entry[0])
            matches.append((entry[0], entry[1], entry[2], _pos(token)))

    matches.sort(key=lambda x: x[3])
    return [(m[0], m[1], m[2]) for m in matches]


def build_isd_processing_answer(message: str, structured_data: Dict[str, Any], app_number: str) -> str:
    """
    Deterministic answer text for the isd_processing intent.

    Shared by process_chat and process_chat_stream so the two paths cannot
    drift apart on ISD sub-division wording again.
    """
    if not structured_data or not structured_data.get("found", True):
        return structured_data.get("message", "Application not found.") if structured_data else "Application not found."

    proposed = structured_data.get("proposed_sub_divisions", [])
    survey_no = structured_data.get("survey_no", "N/A")
    survey_area = structured_data.get("survey_total_area_sqm")
    proposed_area = structured_data.get("proposed_total_area_sqm")
    area_match = structured_data.get("area_match")
    patta_count = structured_data.get("patta_transfers_count", 0)
    app_number = app_number or structured_data.get("application_number", "")
    answer = ""

    message_lower_isd = message.lower()

    # Q0 – new owners of the proposed sub-divisions
    if any(w in message_lower_isd for w in ["owner", "who gets", "who will own",
                                            "who get", "உரிமையாளர்", "யாருக்கு"]):
        def _sd_key(no):
            m = re.match(r"(\d+)", str(no) or "")
            return (int(m.group(1)) if m else 10**6, str(no))
        rows = sorted(
            ((p["proposed_sub_division_no"], p.get("owners") or []) for p in proposed),
            key=lambda t: _sd_key(t[0]),
        )
        if not any(o for _, o in rows):
            return (f"No new sub-division owners are recorded for application "
                    f"{app_number} (Survey {survey_no}).")

        # "who gets sub-division 3 of X" -- scope to the named sub-division.
        # Require a digit-led token so "sub-division owners" / "sub-divisions of"
        # do not capture "owners" / "of".
        want = None
        m = re.search(r"sub[\s-]*division\s+(\d[\w/]*)|உட்பிரிவு\s+(\d[\w/]*)",
                      message_lower_isd)
        if m:
            want = (m.group(1) or m.group(2) or "").lower()
        if want:
            rows = [(no, o) for no, o in rows if str(no).lower() == want
                    or str(no).lower().split("/")[0] == want]
            if not rows:
                return (f"Application {app_number} has no proposed sub-division "
                        f"'{want}'.")

        def _fmt(o):
            rel = o.get("relationship")
            rn = o.get("relative_name")
            g = o.get("gender")
            extra = []
            if rel and rn:
                extra.append(f"{rel} {rn}")
            if "gender" in message_lower_isd and g:
                extra.append({"M": "male", "F": "female"}.get(g, g))
            return o["name"] + (f" ({', '.join(extra)})" if extra else "")

        total = sum(len(o) for _, o in rows)
        if "how many" in message_lower_isd or "count" in message_lower_isd:
            per = ", ".join(f"{no}: {len(o)}" for no, o in rows if o)
            return (f"Application {app_number} (Survey {survey_no}) has {total} new "
                    f"owner(s) across {sum(1 for _, o in rows if o)} sub-division(s) — {per}.")

        lines = [f"New sub-division owners for {app_number} (Survey {survey_no}):"]
        for sd_no, owners in rows:
            if not owners:
                continue
            lines.append(f"  • {sd_no}: {', '.join(_fmt(o) for o in owners)}")
        return "\n".join(lines)

    # Q1 / Q4 – proposed sub-divisions (count or list)
    if "proposed" in message_lower_isd:
        count = len(proposed)
        if count == 0:
            answer = f"No proposed sub-divisions found for application {app_number} under Survey {survey_no}."
        elif "how many" in message_lower_isd:
            answer = f"{count} sub-division(s) are proposed under Survey No. {survey_no}: {', '.join(p['proposed_sub_division_no'] for p in proposed)}."
        else:
            lines = [f"  • {p['proposed_sub_division_no']} — {int(p['proposed_area_sqm'])} sq.m — {p['status'].capitalize()}" for p in proposed if p.get("proposed_area_sqm")]
            answer = f"Proposed sub-divisions for {app_number} (Survey {survey_no}):\n" + "\n".join(lines)

    # Q2 – retrieve application status by sub-division
    elif "status" in message_lower_isd and ("retrieve" in message_lower_isd or "by sub" in message_lower_isd):
        if proposed:
            status_parts = [f"{p['proposed_sub_division_no']} – {p['status'].capitalize()}" for p in proposed]
            answer = "Application status by sub-division: " + ", ".join(status_parts) + "."
        else:
            answer = f"No sub-division status found for application {app_number}."

    # Q3 – patta transfer count
    elif any(w in message_lower_isd for w in ["patta transfer", "transfer order"]):
        if patta_count > 0:
            answer = f"{patta_count} patta transfer order(s) will be generated — one per approved sub-division under {app_number}."
        else:
            answer = f"No patta transfer orders found for application {app_number}. They are generated after approval."

    # Q5 – assigned sub-division numbers
    elif "assigned" in message_lower_isd and any(w in message_lower_isd for w in ["number", "numbers"]):
        approved = [p for p in proposed if p.get("status") == "approved"]
        if approved:
            nums = ", ".join(p["proposed_sub_division_no"] for p in approved)
            answer = f"Assigned sub-division numbers for approved {app_number}: {nums}."
        else:
            answer = "Sub-division numbers are assigned after the Senior Draughtsman (SD) approves the subdivision sketch."

    # Q6 – area comparison
    elif any(w in message_lower_isd for w in ["compare", "original"]) and "area" in message_lower_isd:
        if survey_area and proposed_area:
            diff = abs(survey_area - proposed_area)
            if area_match:
                match_str = "✅ Areas match — no discrepancy."
            else:
                match_str = f"⚠ Mismatch! Difference: {diff:,.2f} sq.m. Please verify the manually entered sub-division areas."
            answer = (
                f"Original Survey {survey_no} area: {survey_area:,.2f} sq.m\n"
                f"Total proposed sub-division area: {proposed_area:,.2f} sq.m\n"
                f"{match_str}"
            )
        elif survey_area and not proposed_area:
            answer = (
                f"Survey {survey_no} original area: {survey_area:,.2f} sq.m. "
                f"However, no sub-division area data is available for {app_number} — "
                f"the proposed sub-division areas may not have been entered yet."
            )
        else:
            answer = f"Area data not available for application {app_number}."

    # Q7 – latest action per sub-division
    elif any(w in message_lower_isd for w in ["latest action", "action taken", "each sub-division", "each subdivision"]):
        if proposed:
            parts = [f"{p['proposed_sub_division_no']} – {p['status'].capitalize()}" for p in proposed]
            answer = "Latest status for each sub-division: " + ", ".join(parts) + "."
        else:
            answer = f"No sub-division action data found for application {app_number}."

    else:
        # Generic fallback — show full proposed list
        if proposed:
            lines = [f"  • {p['proposed_sub_division_no']} — {p['status'].capitalize()}" for p in proposed]
            answer = f"Sub-divisions for {app_number} (Survey {survey_no}):\n" + "\n".join(lines)
        else:
            answer = f"No ISD processing data found for application {app_number}."

    return answer


# Natham-chitta owner rows sometimes carry a placeholder instead of a real
# name ("no name in the pure register"). It is not an owner -- keep it out of
# joint-owner answers.
_OWNER_NAME_PLACEHOLDERS = {
    "தூய பதிவேட்டில் பெயர் இல்லை",
    "no name in pure register",
    "-", "", "n/a", "na", "nil",
}


def _summarize_joint_owners(owners_data: dict) -> dict:
    """Collapse a get_survey_owners() result into a joint-ownership summary.

    Joint ownership is a per-sub-division (per-patta) fact, so this groups the
    joint holders by sub-division, drops placeholder names, and de-duplicates
    the same person recurring across many sub-divisions of one survey number.
    """
    owners = owners_data.get("owners", []) or []
    by_sd: dict = {}
    unique_names: list = []
    seen: set = set()
    for o in owners:
        if not o.get("is_joint_owner"):
            continue
        name = (o.get("name") or "").strip()
        if name.lower() in _OWNER_NAME_PLACEHOLDERS or name in _OWNER_NAME_PLACEHOLDERS:
            continue
        sd = o.get("sub_division") or "Survey Level"
        by_sd.setdefault(sd, [])
        if name not in by_sd[sd]:
            by_sd[sd].append(name)
        if name not in seen:
            seen.add(name)
            unique_names.append(name)
    return {
        "joint_owner_names": unique_names,
        "joint_subdivisions": by_sd,
        "joint_subdivision_count": len(by_sd),
        "total_owners": len(owners),
        "matched_wards": owners_data.get("matched_wards", []),
        "matched_parcels": owners_data.get("matched_parcels", 1),
        "requested_sub_division": owners_data.get("requested_sub_division"),
        "sub_division_found": owners_data.get("sub_division_found"),
    }


def _render_ward_directory(sd: dict, language: str) -> str:
    """Who holds which ward, and how many wards/blocks the town has.

    Directory information only -- ward numbers, block numbers and the officer
    posted to each. No application, applicant, survey or owner data appears
    here, which is why it is answerable for wards other than the officer's own.
    """
    is_tamil = language in ("ta", "tanglish")
    wards = (sd or {}).get("wards") or []
    asked = (sd or {}).get("asked_ward")
    if not wards:
        if asked:
            return (f"வார்டு {asked} பதிவில் இல்லை." if is_tamil
                    else f"There is no ward {asked} on record.")
        return ("வார்டு விவரங்கள் எதுவும் இல்லை." if is_tamil
                else "No ward records are available.")

    from html import escape as _esc
    own = {str(w) for w in ((sd or {}).get("own_wards") or [])}

    if asked and len(wards) == 1:
        w = wards[0]
        who = ", ".join(f"{o['name']} ({o.get('designation') or 'SIS'})"
                        for o in w["officers"]) or ("பதிவு இல்லை" if is_tamil else "no officer posted")
        blocks = ", ".join(w["blocks"]) or "-"
        _num = str(w["ward_number"])
        # The projection keeps the ward's name (TAMILNILAM `ward_ename` master
        # column) -- surface it, since "what is the name of ward 102" is a
        # common ask, but only when it says more than the bare number.
        _wn = str(w.get("ward_name") or "").strip()
        _named = bool(_wn) and _wn.lower().replace("-", "").replace(" ", "") \
            not in (f"ward{_num}", f"ward{_num.lstrip('0')}")
        if is_tamil:
            _nm = f" (\"{_wn}\")" if _named else ""
            return (f"வார்டு {_num}{_nm} — {who}. பிளாக்: {blocks}."
                    + ("  (இது உங்கள் வார்டு.)" if _num in own else ""))
        mine = " This is your ward." if _num in own else \
               " Its data is not visible to you; ask that officer for anything about it."
        _nm = f", \"{_wn}\"," if _named else ""
        return (f"Ward {_num}{_nm} is handled by {who}. Block(s): {blocks}.{mine}")

    total_w = (sd or {}).get("total_wards", len(wards))
    total_b = (sd or {}).get("total_blocks", 0)
    town = (sd or {}).get("town")
    where = f" in {town}" if town and not is_tamil else ""
    lead = (f"{town or 'இந்த நகரில்'} {total_w} வார்டுகள், {total_b} பிளாக்குகள் உள்ளன."
            if is_tamil else
            f"There are {total_w} wards and {total_b} blocks{where}.")

    headers = (["வார்டு", "பிளாக்", "அலுவலர்"] if is_tamil
               else ["Ward", "Block(s)", "Officer"])
    body = ""
    for w in wards:
        who = ", ".join(o["name"] for o in w["officers"]) or "-"
        label = str(w["ward_number"])
        if label in own:
            label += " (yours)" if not is_tamil else " (உங்களது)"
        body += ("<tr>"
                 f"<td>{_esc(label)}</td>"
                 f"<td>{_esc(', '.join(w['blocks']) or '-')}</td>"
                 f"<td>{_esc(who)}</td></tr>")
    caption = "வார்டு பட்டியல்" if is_tamil else "Wards and the officers holding them"
    return (f"{lead}<div class='table-intro'>{_esc(caption)}</div>"
            "<table class='data-table'><thead><tr>"
            + "".join(f"<th>{_esc(h)}</th>" for h in headers)
            + f"</tr></thead><tbody>{body}</tbody></table>")


def _render_applications_by_block(sd: dict, language: str) -> str:
    """Per-block (or per-ward) counts as a small table, biggest first.

    Deterministic like every other count answer -- the numbers come straight
    from the group-by, never from the LLM.
    """
    is_tamil = language in ("ta", "tanglish")
    groups = (sd or {}).get("groups") or []
    by_ward = (sd or {}).get("group_by") == "ward"
    if not groups:
        return ("உங்கள் அதிகார வரம்பில் விண்ணப்பங்கள் எதுவும் இல்லை."
                if is_tamil else "You have no applications on record.")

    from html import escape as _esc
    head_scope = ("வார்டு" if by_ward else "பிளாக்") if is_tamil else ("Ward" if by_ward else "Block")
    if is_tamil:
        headers = [head_scope, "மொத்தம்", "நிலுவை", "அங்கீகரிக்கப்பட்டது", "நிராகரிக்கப்பட்டது"]
        caption = f"{head_scope} வாரியான விண்ணப்பங்கள்"
    else:
        headers = [head_scope, "Total (open + approved)", "Open", "Approved", "Rejected"]
        caption = f"Applications by {head_scope.lower()}"
        if not by_ward:
            headers.insert(0, "Ward")

    body = ""
    for g in groups:
        cells = []
        if not by_ward and not is_tamil:
            cells.append(_esc(str(g.get("ward") or "-")))
        cells += [
            _esc(str((g.get("block") if not by_ward else g.get("ward")) or "-")),
            str(g.get("total", 0)), str(g.get("open", 0)),
            str(g.get("approved", 0)), str(g.get("rejected", 0)),
        ]
        body += "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"

    top = groups[0]
    label = (top.get("ward") if by_ward else top.get("block")) or "-"
    if is_tamil:
        lead = f"{head_scope} {label} — {top.get('total', 0)} விண்ணப்பங்கள் (அதிகபட்சம்)."
    elif len(groups) == 1:
        # "All 54 of your applications" would be a false total -- rejected files
        # are counted separately, exactly as the listing queries exclude them.
        _rej = top.get("rejected", 0)
        lead = (f"All of your applications are in {head_scope.lower()} {label} — "
                f"{top.get('total', 0)} active ({top.get('open', 0)} open, "
                f"{top.get('approved', 0)} approved)"
                + (f", plus {_rej} rejected." if _rej else "."))
    else:
        lead = (f"{head_scope} {label} has the most — {top.get('total', 0)} "
                f"applications of {sum(g.get('total', 0) for g in groups)} in your jurisdiction.")

    return (f"{lead}<div class='table-intro'>{_esc(caption)}</div>"
            "<table class='data-table'><thead><tr>"
            + "".join(f"<th>{_esc(h)}</th>" for h in headers)
            + f"</tr></thead><tbody>{body}</tbody></table>")


# The same lock, asked as a state rather than as a permission: "is there any
# active application on survey 24?", "is survey 5 locked?". The finding is
# identical; only the yes and the no swap places. Leading such a question with
# "Yes -- survey 24 has no active application" says the opposite of the
# sentence it introduces.
_LOCK_STATE_RE = re.compile(
    r"\b(?:is|are|does|do|has|have)\b[^.?]{0,40}"
    r"\b(?:active|live|open|ongoing|pending|existing|current)\b"
    r"|\b(?:locked|blocked)\b"
    r"|செயலில்\s*உள்ள|நிலுவையில்\s*உள்ள",
    re.IGNORECASE,
)


def _asks_lock_state(message: str) -> bool:
    """True when the question asks whether something is live, not whether to file."""
    msg = message or ""
    if re.search(r"\bcan\s+(?:i|we|he|she|they|the\s+owner)\b|\bapply\b|\bfile\s+a\b"
                 r"|\bsubmit\b|விண்ணப்பிக்க", msg, re.IGNORECASE):
        return False
    return bool(_LOCK_STATE_RE.search(msg))


def _render_workload_answer(structured_data: dict, language: str) -> str:
    """"What is my workload?" -- every service code reported alike, in one flat
    breakdown, the same style get_all_service_code_applications() already uses
    ("found N across every service code: 0153 (9), 0154 (10), 0156 (1), ...").

    This used to read "N active ISD/NISD application(s)... plus M registered
    under other service codes" -- correct numbers, but ISD/NISD was still the
    headline and every other code an afterthought tacked on after it, the
    same imbalance CLAUDE.md's "every service code is treated alike" rule
    exists to rule out everywhere else in the app.
    """
    is_tamil = language in ("ta", "tanglish")
    sd = structured_data or {}
    overdue = sd.get("overdue", 0)
    grand_total = sd.get("total_active_all_codes", sd.get("total_active", 0))
    by_code = sd.get("by_service_code") or {}
    if not by_code:
        # Fallback for a caller that never ran the per-code query.
        by_code = {"0153": sd.get("NISD", 0), "0154": sd.get("ISD", 0)}
    breakdown = ", ".join(f"{code} ({n})" for code, n in sorted(by_code.items()) if n)
    if is_tamil:
        return (f"உங்கள் பணிச்சுமை: எல்லா சேவை குறியீடுகளிலும் மொத்தம் {grand_total} "
                f"செயலில் உள்ள விண்ணப்பங்கள்"
                + (f" ({breakdown})" if breakdown else "")
                + f". தாமதமான ISD கோப்புகள்: {overdue}.")
    return (f"Your workload: {grand_total} active application(s) across every service code"
            + (f" ({breakdown})" if breakdown else "")
            + f". Overdue ISD files: {overdue}.")


def _render_can_apply_answer(structured_data: dict, language: str,
                             message: str = "") -> str:
    """Answer "can another application be filed on this survey number?".

    A survey number takes one application at a time -- the sub-division does not
    get its own slot, because the parcel's record is what is being worked on.
    """
    is_tamil = language in ("ta", "tanglish")
    state_q = _asks_lock_state(message)
    if not structured_data or not structured_data.get("found"):
        return (structured_data or {}).get(
            "message",
            "தயவுசெய்து கணக்கெண்ணை குறிப்பிடவும்." if is_tamil else
            "Please tell me which survey number you mean, e.g. \"can I apply on survey 1355?\"")

    survey = structured_data.get("base_survey_no") or structured_data.get("survey_no")
    blocking = structured_data.get("blocking_applications") or []
    asked_sub = structured_data.get("requested_sub_division")

    if structured_data.get("can_apply"):
        if is_tamil:
            txt = (f"{'இல்லை' if state_q else 'ஆம்'} — கணக்கெண் {survey}-இல் தற்போது "
                   f"செயலில் உள்ள விண்ணப்பம் எதுவும் இல்லை. புதிய விண்ணப்பம் அளிக்கலாம்.")
        else:
            txt = (f"{'No' if state_q else 'Yes'} — survey number {survey} has no "
                   f"active application, so a new one can be filed.")
    elif structured_data.get("register_only") and not blocking:
        # The blocker is a pending non-ISD/NISD application (checked via
        # service_register_entries, which carries no sub-division/stage
        # detail the way `Application` does), not one of the rows `blocking`
        # normally holds -- render straight from the flat message instead of
        # iterating an empty list, which used to print "already has 0 active
        # application(s)" right under a correct "No".
        _reg_app_no = structured_data.get("existing_application_number", "")
        if is_tamil:
            txt = (f"{'ஆம்' if state_q else 'இல்லை'} — விண்ணப்பம் {_reg_app_no} ஏற்கனவே இந்த "
                   f"கணக்கெண்ணில் நிலுவையில் உள்ளது. அது அங்கீகரிக்கப்படும்/நிராகரிக்கப்படும் "
                   f"வரை, எந்த உட்பிரிவிலும் அடுத்த விண்ணப்பம் அளிக்க முடியாது.")
        else:
            txt = (structured_data.get("message")
                   or f"No — survey number {survey} already has an active application.")
    else:
        lines = []
        for b in blocking:
            sd = ", ".join(f"{survey}/{x}" for x in (b.get("sub_divisions") or [])) or "-"
            lines.append(
                f"  • {b['application_number']} ({b.get('type')}, {b.get('status')}"
                f"{', ' + b['stage'] if b.get('stage') else ''}) on {sd}")
        if is_tamil:
            head = (f"{'ஆம்' if state_q else 'இல்லை'} — கணக்கெண் {survey}-இல் {len(blocking)} விண்ணப்பம்(கள்) "
                    f"ஏற்கனவே செயலில் உள்ளன. ஒரு கணக்கெண்ணில் ஒரு நேரத்தில் ஒரு விண்ணப்பம் "
                    f"மட்டுமே — வேறு உட்பிரிவாக இருந்தாலும் பொருந்தாது:")
            tail = "இவை அங்கீகரிக்கப்பட்ட/நிராகரிக்கப்பட்ட பிறகே அடுத்த விண்ணப்பம் அளிக்க முடியும்."
        else:
            head = (f"{'Yes' if state_q else 'No'} — survey number {survey} already has {len(blocking)} active "
                    f"application(s). A survey number takes one application at a time, and "
                    f"that holds across sub-divisions:")
            tail = ("The next application can be filed only once these are approved "
                    "or rejected.")
        txt = "\n".join([head] + lines + ["", tail])

    if asked_sub and not structured_data.get("can_apply"):
        txt += ("\n" + (f"(நீங்கள் கேட்டது {asked_sub} — உட்பிரிவு தனியாக கணக்கிடப்படாது.)"
                        if is_tamil else
                        f"(You asked about {asked_sub}; the sub-division does not get "
                        f"a separate slot.)"))

    others = structured_data.get("other_locations") or []
    if others:
        where = ", ".join(f"ward {o.get('ward')} / block {o.get('block')}" for o in others)
        txt += ("\n" + (f"(கணக்கெண் {survey} {where}-இலும் உள்ளது; சரிபார்க்கப்பட்டது "
                        f"வார்டு {structured_data.get('ward')}.)"
                        if is_tamil else
                        f"(Survey {survey} also exists in {where}; this answer is for the "
                        f"parcel in ward {structured_data.get('ward')}, "
                        f"block {structured_data.get('block')}.)"))
    return txt


_FV_ASK = re.compile(r"\b(?:notes?|remarks?|observations?|encro\w*|area\s+verif\w*|verif\w*\s+(?:the\s+)?area|cancel\w*|"
                     r"re-?schedul\w*|overdue)\b", re.IGNORECASE)


def _fv_detail_answer(msg: str, sd: dict, app_no: str, ta: bool) -> Optional[str]:
    """One field-visit fact of one application: notes, encroachment, area verified, cancelled, rescheduled,
    overdue -- read from the visit row. These were answered with the workflow remarks, the plot-area card,
    "Yes -- completed" (to "was it cancelled?") or "ask the Tahsildar" (to "was it rescheduled?")."""
    m = (msg or "").lower()
    if not re.search(r"vis\w*t|feild|encro|area\s+verif", m) or not _FV_ASK.search(m):
        return None
    fv = sd.get("field_visit") or {}
    typ = (sd.get("type") or "").upper()
    if not fv:
        if typ == "NISD":
            return (f"{app_no} NISD — கள ஆய்வு தேவையில்லை, எனவே பதிவு இல்லை." if ta
                    else f"{app_no} is NISD -- it needs no field visit, so none is recorded.")
        return f"{app_no}-க்கு கள ஆய்வு பதிவு இல்லை." if ta else f"No field visit is recorded for {app_no}."
    st = str(fv.get("status") or "").lower()
    sched = str(fv.get("scheduled_date") or "")[:10] or None
    act = str(fv.get("actual_date") or "")[:10] or None
    state = (f"completed on {act or sched}" if st == "completed" else f"{st or 'recorded'}" + (f" for {sched}" if sched else ""))
    if re.search(r"encro", m):
        found, notes = fv.get("encroachment_found"), (fv.get("encroachment_notes") or "").strip()
        if ta:
            return (f"{app_no}: கள ஆய்வில் ஆக்கிரமிப்பு {'கண்டறியப்பட்டது' if found else 'பதிவாகவில்லை'}"
                    + (f" — {notes}" if notes else "") + ".")
        return (f"{'Yes' if found else 'No'} — {'encroachment was recorded' if found else 'no encroachment is recorded'} "
                f"at the field visit for {app_no} (visit {state})" + (f"; notes: {notes}" if notes else "; no encroachment notes") + ".")
    if re.search(r"area", m):
        v = fv.get("area_verified")
        return ((f"{app_no}: பரப்பளவு சரிபார்ப்பு — {'ஆம்' if v else 'இல்லை'} (கள ஆய்வு {state})." if ta else
                 f"{'Yes' if v else 'No'} — the area was {'verified' if v else 'not marked verified'} at the field visit for {app_no} (visit {state})."))
    if re.search(r"cancel", m):
        c = st == "cancelled"
        return (f"{'ஆம்' if c else 'இல்லை'} — {app_no} கள ஆய்வு {state}." if ta
                else f"{'Yes' if c else 'No'} — the field visit for {app_no} was {'cancelled' if c else 'not cancelled'}; it is {state}.")
    if re.search(r"schedul", m):
        return (f"{app_no}: மறு திட்டமிடல் வரலாறு பதிவேட்டில் இல்லை; ஒரு தேதி மட்டுமே உள்ளது — கள ஆய்வு {state}." if ta else
                f"No reschedule is recorded for {app_no}: the register keeps one visit date per application, no reschedule "
                f"history. The visit is {state}.")
    if re.search(r"overdue", m):
        done = st == "completed" or str(sd.get("status") or "").lower() in ("approved", "rejected")
        if done:
            return (f"இல்லை — {app_no} கள ஆய்வு {state}." if ta else f"No — the field visit for {app_no} is {state}, so it is not overdue.")
        return None              # an open visit: the overdue / SLA answer below works it out
    notes = (fv.get("visit_notes") or "").strip()
    return ((f"{app_no} கள ஆய்வு குறிப்புகள்: {notes}" if notes else f"{app_no}: கள ஆய்வு குறிப்பு பதிவில்லை (கள ஆய்வு {state}).") if ta else
            (f"Field visit notes for {app_no}: {notes} (visit {state})." if notes
             else f"No field visit notes are recorded for {app_no} (visit {state})."))


def _field_visit_answer(sd: dict, app_no: str, is_tamil: bool) -> str:
    """Answer "is a field visit scheduled?" for one application.

    A bare "Field Visit Scheduled: No" reads as a missing value rather than an
    answer, so say it in words -- and when a visit is booked, give the date and
    how it sits against today, which is what the officer is really asking.
    """
    from datetime import date as _d
    fv = sd.get("field_visit") or {}
    date_str = fv.get("scheduled_date") or sd.get("field_visit_date")
    scheduled = bool(sd.get("field_visit_scheduled")) or bool(date_str)
    app_type = (sd.get("type") or "").upper()
    # the next turn ("is it completed?", "when?") is about the visit, not the file
    sd["_asked_field_visit"] = True

    # NISD is a straight patta transfer of the whole survey number: no field visit.
    if app_type == "NISD" and not scheduled:
        return (f"விண்ணப்பம் {app_no} NISD வகை — NISD விண்ணப்பத்திற்கு கள ஆய்வு தேவையில்லை."
                if is_tamil else
                f"{app_no} is an NISD application -- NISD needs no field visit "
                f"(only ISD do).")

    if not scheduled:
        if is_tamil:
            msg = f"இல்லை — விண்ணப்பம் {app_no}-க்கு கள ஆய்வு இதுவரை திட்டமிடப்படவில்லை."
            if app_type == "ISD":
                msg += " (ISD விண்ணப்பத்திற்கு கள ஆய்வு கட்டாயம்.)"
            return msg
        return f"No — no field visit is scheduled for {app_no} yet."

    day = str(date_str)[:10] if date_str else None
    status = (fv.get("status") or "").lower()
    when = ""
    if day:
        try:
            delta = (_d.fromisoformat(day) - _d.today()).days
            if delta > 0:
                when = f" ({delta} நாட்களில்)" if is_tamil else f" (in {delta} day{'s' if delta != 1 else ''})"
            elif delta == 0:
                when = " (இன்று)" if is_tamil else " (today)"
            elif status not in ("completed", "done"):
                when = (f" ({-delta} நாட்கள் தாமதம்)" if is_tamil
                        else f" ({-delta} day{'s' if delta != -1 else ''} overdue)")
        except ValueError:
            pass
    if day and status in ("completed", "done"):
        return (f"ஆம் — விண்ணப்பம் {app_no}-க்கான கள ஆய்வு {day} அன்று முடிக்கப்பட்டது." if is_tamil
                else f"Yes — the field visit for {app_no} was completed on {day}.")
    if is_tamil:
        return (f"ஆம் — விண்ணப்பம் {app_no}-க்கு கள ஆய்வு {day} அன்று திட்டமிடப்பட்டுள்ளது{when}."
                if day else f"ஆம் — விண்ணப்பம் {app_no}-க்கு கள ஆய்வு திட்டமிடப்பட்டுள்ளது, தேதி பதிவாகவில்லை.")
    return (f"Yes — a field visit for {app_no} is scheduled for {day}{when}."
            if day else f"Yes — a field visit is booked for {app_no}, but no date is recorded.")


# Words that make "when" a question about the DECISION rather than the filing.
# "when was X approved" used to answer with the submission date, because the
# field map sends every "when" / "date" to submission_date.
_DECISION_WORDS = (
    "approved", "approval", "approve", "sanctioned", "cleared",
    "rejected", "rejection", "reject", "refused",
    "closed", "closure", "disposed", "disposal", "decided", "decision",
    "signed", "order generated",
    "அங்கீகரிக்கப்பட்ட", "அங்கீகார", "நிராகரிக்கப்பட்ட", "நிராகரிப்பு",
    "முடிக்கப்பட்ட", "முடிந்த",
)
_WHEN_WORDS = (
    "when", "date", "எப்போது", "தேதி", "eppodhu", "eppo", "thethi", "thedhi",
    "how long", "எத்தனை நாட்கள்",
)


# "How long did X take", "how many days to approve" -- a duration question
# about one application. It carries no decision word, so the gate below needs
# its own test; without it the question reached the overdue/pending handlers,
# which answered "it is NOT overdue" to "how many days did it take to approve".
_DURATION_RE = re.compile(
    r"\bhow\s+long\b|\bhow\s+many\s+days\b|\bhow\s+much\s+time\b"
    r"|\btime\s+taken\b|\bturnaround\b|\bprocessing\s+time\b"
    r"|எத்தனை\s*நாட்கள்|எவ்வளவு\s*காலம்|எத்தனை\s*நாள்|எவ்வளவு\s*நாள்|எவ்வளவு\s*நேரம்"
    r"|\b(?:evlo|evvalavu|evalo|ethana|ethanai)\s+(?:naal|naat?kal|naatkal|kaalam|time)\b",
    re.IGNORECASE,
)


def _asked_decision_date(message_lower: str) -> bool:
    """True for "when was X approved / rejected / closed", not for "is X approved".

    Also true for "how long did X take", which asks for the same two dates and
    the span between them.
    """
    if _DURATION_RE.search(message_lower):
        return True
    return (any(w in message_lower for w in _WHEN_WORDS)
            and any(w in message_lower for w in _DECISION_WORDS))


def _decision_date_answer(sd: dict, app_no: str, is_tamil: bool) -> str:
    """Answer "when was this approved / rejected?" for one application.

    The date comes from the workflow hop that closed the file
    (`decision_date` in get_application_detail), never from the submission
    date -- answering the filing date here is a wrong answer, not a vague one.
    """
    from datetime import date as _d
    status = (sd.get("status") or "").lower()
    day = sd.get("decision_date")

    if not day:
        # Still open: there is no decision date to give, and saying so beats
        # handing back the submission date.
        # `_stage_labels` is built inside the two answer sites, not at module
        # level, so name the desk here rather than reaching for it.
        _desks = {"SIS": "the Sub Inspector Surveyor",
                  "SD": "the Senior Draughtsman",
                  "DIS": "the Deputy Inspector Surveyor",
                  "TAHSILDAR": "the Zonal Level Tahsildar"}
        stage = (sd.get("stage") or "").upper()
        where = _desks.get(stage, stage) if stage else None
        if is_tamil:
            msg = f"விண்ணப்பம் {app_no} இன்னும் முடிவு செய்யப்படவில்லை (நிலை: {status or 'திறந்துள்ளது'})."
            return msg + (f" தற்போது: {where}." if where else "")
        msg = f"{app_no} has not been decided yet (status: {status or 'open'})."
        return msg + (f" It is currently at: {where}." if where else "")

    verb_en = "rejected" if status == "rejected" else "approved"
    verb_ta = "நிராகரிக்கப்பட்டது" if status == "rejected" else "அங்கீகரிக்கப்பட்டது"
    asked_duration = bool(_DURATION_RE.search(sd.get("_asked") or ""))
    by = sd.get("decision_by")
    submitted = str(sd.get("submission_date") or "")[:10]
    took = ""
    if submitted:
        try:
            days = (_d.fromisoformat(day) - _d.fromisoformat(submitted)).days
            took = (f" சமர்ப்பித்த {submitted} முதல் {days} நாட்கள்."
                    if is_tamil else
                    f" That is {days} day{'s' if days != 1 else ''} after it was filed on {submitted}.")
        except ValueError:
            pass
    if asked_duration and submitted:
        # The officer asked how long, so lead with the number of days.
        try:
            days = (_d.fromisoformat(day) - _d.fromisoformat(submitted)).days
        except ValueError:
            days = None
        if days is not None:
            if is_tamil:
                return (f"விண்ணப்பம் {app_no} {days} நாட்கள் எடுத்தது — "
                        f"{submitted} அன்று சமர்ப்பிக்கப்பட்டு {day} அன்று {verb_ta}"
                        + (f" ({by})." if by else "."))
            return (f"{app_no} took {days} day{'s' if days != 1 else ''} — filed "
                    f"{submitted}, {verb_en} {day}"
                    + (f" by {by}." if by else "."))
    if is_tamil:
        return (f"விண்ணப்பம் {app_no} {day} அன்று {verb_ta}"
                + (f" ({by})." if by else ".") + took)
    return (f"{app_no} was {verb_en} on {day}"
            + (f" by {by}." if by else ".") + took)


# "street name" carries the exact token "name", which the generic field map
# (a bare "name" -> Applicant Name) matched on its own -- so "what is the
# street name for <app>" answered with the APPLICANT'S name, not the street,
# a false trigger that reads as confident but wrong data. There is no
# separately-tracked street-name column; the street is written as free text
# inside the applicant's address (e.g. "...Tmb Colony 5th Street,...",
# "...தெற்கு தெரு..."), so the honest answer gives that address rather than
# inventing a parsed street value. Checked ahead of the field map, same as
# the decision-date / untracked-field guards above it.
_STREET_NAME_CUES = (
    "street name", "name of the street", "name of street", "road name",
    "name of the road", "which street", "what street",
    "தெரு பெயர்", "தெருவின் பெயர்", "எந்த தெரு",
)


def _asked_street_name(message_lower: str) -> bool:
    return any(c in message_lower for c in _STREET_NAME_CUES)


def _street_name_answer(sd: dict, app_no: str, is_tamil: bool) -> str:
    address = sd.get("applicant_address")
    if not address or address == "N/A":
        if is_tamil:
            return (f"விண்ணப்பம் {app_no}-க்கு தனி தெரு பெயர் புலம் இல்லை, "
                     f"மேலும் விண்ணப்பதாரரின் முகவரியும் பதிவில் இல்லை.")
        return (f"There is no separate street-name field for {app_no}, and no "
                f"applicant address is on record either.")
    if is_tamil:
        return (f"விண்ணப்பம் {app_no}-க்கு தனியாக 'தெரு பெயர்' புலம் பதிவில் "
                f"இல்லை -- தெரு, முகவரியின் ஒரு பகுதியாக மட்டுமே பதிவுசெய்யப்படும். "
                f"விண்ணப்பதாரரின் முகவரி: {address}")
    return (f"There is no separate 'street name' field on record for {app_no} "
            f"-- the street is written as part of the applicant's address, not "
            f"held on its own. Applicant's address: {address}")


# "first name" / "last name" / "surname" carry the exact token "name" too, so
# they were also swallowed by the same bare "name" -> Applicant Name match --
# not wrong data this time (the full name IS the only name on record), but a
# silent non-answer to the actual question: the register never split it, and
# the label said "Applicant Name" as if the split had happened anyway.
_NAME_PART_CUES = (
    "first name", "given name", "forename",
    "last name", "surname", "family name",
    "முதல் பெயர்", "முதலாம் பெயர்", "கடைசி பெயர்", "குடும்பப் பெயர்",
)


def _asked_name_part(message_lower: str) -> bool:
    return any(c in message_lower for c in _NAME_PART_CUES)


def _name_part_answer(sd: dict, app_no: str, is_tamil: bool) -> str:
    name = sd.get("applicant_name")
    if not name or name == "N/A":
        if is_tamil:
            return f"விண்ணப்பம் {app_no}-க்கு விண்ணப்பதாரர் பெயர் பதிவில் இல்லை."
        return f"No applicant name is on record for {app_no}."
    if is_tamil:
        return (f"பதிவு விண்ணப்பதாரர் பெயரை முதல்/கடைசி எனப் பிரிக்காமல் ஒரே "
                f"முழுப் பெயராகவே வைத்திருக்கிறது: {name}")
    return (f"The register holds the applicant's name as a single full name, "
            f"not split into first/last: {name}")


# "which day was X filed on" answered "I could not find that particular
# detail" -- the field map has no key for "day"/"weekday", even though the
# weekday is a one-line derivation from the submission_date already on
# record. "was X filed on a weekend" fell to the generic details dump,
# ignoring the yes/no actually asked. Both are now answered deterministically
# from the same date the field map already exposes -- no LLM guesses a
# weekday (a class of arithmetic it is unreliable at).
_DAY_OF_WEEK_CUES = (
    "day of the week", "day of week", "which day", "what day",
    "weekday", "week day", "weekend", "week end",
    "என்ன கிழமை", "எந்த கிழமை", "எந்த நாள்", "எந்த வாரநாள்", "வார இறுதி",
)
_MONTH_ASKED_CUES = ("what month", "which month", "என்ன மாதம்", "எந்த மாதம்")
_WEEKDAY_NAMES_EN = ("monday", "tuesday", "wednesday", "thursday", "friday",
                     "saturday", "sunday")
_WEEKDAY_NAMES_TA = {
    "திங்கள்": "monday", "செவ்வாய்": "tuesday", "புதன்": "wednesday",
    "வியாழன்": "thursday", "வெள்ளி": "friday", "சனி": "saturday",
    "ஞாயிறு": "sunday",
}
_WEEKDAY_LABEL_TA = {
    "monday": "திங்கள்", "tuesday": "செவ்வாய்", "wednesday": "புதன்",
    "thursday": "வியாழன்", "friday": "வெள்ளி", "saturday": "சனி",
    "sunday": "ஞாயிறு",
}


def _asked_day_of_week(message_lower: str) -> bool:
    if any(c in message_lower for c in _DAY_OF_WEEK_CUES):
        return True
    # "was X filed on a Monday?" names the weekday directly, with no "day"/
    # "week" word at all in the sentence.
    if any(d in message_lower for d in _WEEKDAY_NAMES_EN):
        return True
    return any(ta in message_lower for ta in _WEEKDAY_NAMES_TA)


def _asked_month_name(message_lower: str) -> bool:
    return any(c in message_lower for c in _MONTH_ASKED_CUES)


def _day_of_week_answer(sd: dict, app_no: str, message_lower: str,
                        is_tamil: bool) -> str:
    """Which day of the week (and whether it was a weekend) a date fell on.

    Targets the DECISION date if the question names approval/rejection
    (reusing `_DECISION_WORDS`, the same signal `_asked_decision_date` uses),
    the submission date otherwise -- an open application has no decision date
    to ask about in the first place.
    """
    from datetime import date as _d
    wants_decision = any(w in message_lower for w in _DECISION_WORDS)
    field_label_en, field_label_ta = "submitted", "சமர்ப்பிக்கப்பட்ட"
    day_str = sd.get("submission_date")
    if wants_decision:
        day_str = sd.get("decision_date")
        status = (sd.get("status") or "").lower()
        verb_en = "rejected" if status == "rejected" else "approved"
        field_label_en = verb_en
        field_label_ta = "நிராகரிக்கப்பட்ட" if status == "rejected" else "அங்கீகரிக்கப்பட்ட"
        if not day_str:
            if is_tamil:
                return f"விண்ணப்பம் {app_no} இன்னும் முடிவு செய்யப்படவில்லை, எனவே அந்தத் தேதி இல்லை."
            return f"{app_no} has not been decided yet, so there is no such date to check."
    if not day_str:
        if is_tamil:
            return f"விண்ணப்பம் {app_no}-க்கு {field_label_ta} தேதி பதிவில் இல்லை."
        return f"No {field_label_en} date is on record for {app_no}."

    try:
        parsed = _d.fromisoformat(str(day_str)[:10])
    except ValueError:
        if is_tamil:
            return f"விண்ணப்பம் {app_no}-க்கான தேதியை என்னால் படிக்க முடியவில்லை."
        return f"I could not parse the date on record for {app_no}."

    weekday_en = parsed.strftime("%A")
    is_weekend = parsed.weekday() >= 5

    # "was it filed on a Monday?" names a specific weekday -- answer yes/no.
    named_day = next((d for d in _WEEKDAY_NAMES_EN if d in message_lower), None)
    if not named_day:
        for ta_name, en_name in _WEEKDAY_NAMES_TA.items():
            if ta_name in message_lower:
                named_day = en_name
                break
    if named_day:
        matches = named_day == weekday_en.lower()
        if is_tamil:
            ta_named = _WEEKDAY_LABEL_TA.get(named_day, named_day)
            ta_actual = _WEEKDAY_LABEL_TA.get(weekday_en.lower(), weekday_en)
            verdict = "ஆம்" if matches else "இல்லை"
            return (f"{verdict}. விண்ணப்பம் {app_no} {field_label_ta}து "
                    f"{parsed.isoformat()} ({ta_actual}கிழமை) அன்று"
                    + ("." if matches else f", {ta_named}கிழமை அன்று அல்ல."))
        verdict = "Yes" if matches else "No"
        return (f"{verdict}. {app_no} was {field_label_en} on "
                f"{parsed.isoformat()} ({weekday_en})"
                + ("." if matches else f", not a {named_day.capitalize()}."))

    if "weekend" in message_lower or "week end" in message_lower or "வார இறுதி" in message_lower:
        if is_tamil:
            verdict = "ஆம், இது வார இறுதி நாள்" if is_weekend else "இல்லை, இது வார நாள்"
            return (f"{verdict} — விண்ணப்பம் {app_no} {parsed.isoformat()} "
                    f"({_WEEKDAY_LABEL_TA.get(weekday_en.lower(), weekday_en)}கிழமை) அன்று {field_label_ta}து.")
        verdict = "Yes, that was a weekend" if is_weekend else "No, that was a weekday"
        return (f"{verdict} — {app_no} was {field_label_en} on "
                f"{parsed.isoformat()} ({weekday_en}).")

    if is_tamil:
        ta_day = _WEEKDAY_LABEL_TA.get(weekday_en.lower(), weekday_en)
        return (f"விண்ணப்பம் {app_no} {parsed.isoformat()} அன்று {field_label_ta}து "
                f"— அது ஒரு {ta_day}கிழமை.")
    return (f"{app_no} was {field_label_en} on {parsed.isoformat()} — that "
            f"was a {weekday_en}.")


def _month_name_answer(sd: dict, app_no: str, message_lower: str,
                       is_tamil: bool) -> str:
    """"What month was X submitted/approved in?" -- names the month rather
    than handing back the whole date and leaving the officer to read it off."""
    from datetime import date as _d
    wants_decision = any(w in message_lower for w in _DECISION_WORDS)
    day_str = sd.get("decision_date") if wants_decision else sd.get("submission_date")
    field_label_en = ("rejected" if (sd.get("status") or "").lower() == "rejected"
                      else "approved") if wants_decision else "submitted"
    field_label_ta = (("நிராகரிக்கப்பட்ட" if (sd.get("status") or "").lower() == "rejected"
                       else "அங்கீகரிக்கப்பட்ட") if wants_decision else "சமர்ப்பிக்கப்பட்ட")
    if not day_str:
        if wants_decision:
            if is_tamil:
                return f"விண்ணப்பம் {app_no} இன்னும் முடிவு செய்யப்படவில்லை."
            return f"{app_no} has not been decided yet."
        if is_tamil:
            return f"விண்ணப்பம் {app_no}-க்கு {field_label_ta} தேதி பதிவில் இல்லை."
        return f"No {field_label_en} date is on record for {app_no}."
    try:
        parsed = _d.fromisoformat(str(day_str)[:10])
    except ValueError:
        if is_tamil:
            return f"விண்ணப்பம் {app_no}-க்கான தேதியை என்னால் படிக்க முடியவில்லை."
        return f"I could not parse the date on record for {app_no}."
    if is_tamil:
        return (f"விண்ணப்பம் {app_no} {parsed.strftime('%B %Y')}-ல் {field_label_ta}து "
                f"({parsed.isoformat()}).")
    return (f"{app_no} was {field_label_en} in {parsed.strftime('%B %Y')} "
            f"({parsed.isoformat()}).")


async def _build_comparison(db, officer, message: str, is_tamil: bool = False) -> dict:
    """Fetch both sides of a comparison and settle the question that was asked."""
    from backend.services.postgres import get_application_detail, get_comparison
    from backend.services.rag import parse_comparison_query

    _CMP_WANTS_NEWER.set(bool(re.search(r"\b(?:newer|newest|latest|recent|later)\b|puthu|புதி|பின்னை", message or "", re.IGNORECASE)))
    spec = parse_comparison_query(message)
    if not spec:
        return {"found": False, "message": (
            "எதை ஒப்பிட வேண்டும் என்று சொல்லுங்கள் — இரண்டு விண்ணப்ப எண்கள், அல்லது இரண்டு வகை / நிலை / வழி / வார்டு "
            "(எ.கா. \"ISD vs NISD\")." if is_tamil else
            "Tell me the two things to compare -- two application numbers, or two types, statuses, channels or wards "
            "(for example \"ISD vs NISD\").")}

    if spec["kind"] != "applications":
        if spec["kind"] == "ward" and spec.get("sides"):
            from backend.services.postgres import _officer_ward_numbers
            held = {str(w).zfill(3) for w in await _officer_ward_numbers(db, officer)}
            outside = [w for w in spec["sides"] if str(w).zfill(3) not in held]
            if outside:
                # a count of zero would read as a fact about a ward the officer cannot see
                ours = ", ".join(sorted(held)) or "-"
                names = ", ".join(outside)
                return {"found": False, "message": (
                    f"வார்டு {names} உங்கள் அதிகார வரம்பிற்கு வெளியே உள்ளது; நீங்கள் பார்க்கக்கூடிய வார்டுகள்: {ours}."
                    if is_tamil else
                    f"Ward {names} is outside your assigned jurisdiction, so I cannot compare it. "
                    f"The wards you hold: {ours}.")}
        scope, note = _comparison_scope(message, spec, is_tamil)
        if scope:
            spec = {**spec, "scope": scope}
        data = await get_comparison(db, officer, spec)
        data.setdefault("kind", spec["kind"])
        if scope and data.get("found"):
            data["scope_note"] = note
        # A decision date exists only for ISD/NISD files (the register carries
        # none for the other service codes), so a duration answer covers those
        # two services. Say so, or "your 19 approved applications" reads as the
        # officer's whole approved set when it is 78.
        if data.get("found") and (data.get("kind") in ("average_duration", "superlative")
                                  or data.get("aspect") == "duration"):
            _dn = ("முடிவு தேதி ISD / NISD கோப்புகளுக்கு மட்டுமே பதிவாகிறது; மற்ற சேவை குறியீடுகள் இதில் சேர்க்கப்படவில்லை."
                   if is_tamil else
                   "Only ISD and NISD files carry a recorded decision date, so this covers those two "
                   "services; the other service codes in the register are not included.")
            data["scope_note"] = f"{data['scope_note']} {_dn}" if data.get("scope_note") else _dn
        return data

    async def _side(number: str) -> dict:
        detail = await get_application_detail(db, number, officer)
        if not detail or not detail.get("found"):
            return {"application_number": number, "found": False}
        days = None
        if detail.get("decision_date") and detail.get("submission_date"):
            try:
                days = (date.fromisoformat(detail["decision_date"])
                        - date.fromisoformat(detail["submission_date"])).days
            except ValueError:
                days = None
        return {
            "found": True,
            "application_number": detail.get("application_number", number),
            "type": detail.get("type"), "status": detail.get("status"),
            "submission_date": detail.get("submission_date"),
            "decision_date": detail.get("decision_date"),
            "days_to_decide": days,
            "survey_no": detail.get("survey_no"),
            "ward_number": detail.get("ward_number"),
            "submission_channel": detail.get("submission_channel"),
            "area_sqm": detail.get("area_sqm"),
            "fee_amount": detail.get("fee_amount"),
        }

    left, right = await _side(spec["left"]), await _side(spec["right"])
    missing = [s["application_number"] for s in (left, right) if not s.get("found")]
    if missing:
        if is_tamil:
            return {"found": False, "kind": "applications",
                    "message": (f"{' மற்றும் '.join(missing)} படிக்க முடியவில்லை -- "
                                f"எண்ணைச் சரிபார்க்கவும், அல்லது அது உங்கள் அதிகார "
                                f"வரம்பிற்கு வெளியே இருக்கலாம்.")}
        return {"found": False, "kind": "applications",
                "message": (f"I could not read {' and '.join(missing)} -- check the "
                            f"number, or it may be outside your jurisdiction.")}

    # The verdict: answer the question that was actually asked. Was
    # hardcoded English regardless of the officer's language -- a Tamil
    # "compare 2026/... and 2026/..." got an all-English verdict line under
    # an otherwise Tamil field table.
    verdict = None
    aspect = spec.get("aspect")
    _and = " மற்றும் " if is_tamil else " and "
    if aspect == "duration":
        if left["days_to_decide"] is None or right["days_to_decide"] is None:
            still_open = [s["application_number"] for s in (left, right)
                          if s["days_to_decide"] is None]
            if is_tamil:
                verdict = (f"{_and.join(still_open)} இன்னும் முடிவு செய்யப்படவில்லை, "
                           f"எனவே ஒப்பிட நேரம் இல்லை.")
            else:
                verdict = (f"{' and '.join(still_open)} has not been decided yet, so "
                           f"there is no time to compare against.")
        elif left["days_to_decide"] == right["days_to_decide"]:
            verdict = (f"இரண்டும் {left['days_to_decide']} நாட்கள் எடுத்தன." if is_tamil
                      else f"Both took {left['days_to_decide']} days.")
        else:
            slow = max(left, right, key=lambda s: s["days_to_decide"])
            fast = min(left, right, key=lambda s: s["days_to_decide"])
            if is_tamil:
                verdict = (f"**{slow['application_number']}** அதிக நேரம் எடுத்தது: "
                           f"{slow['days_to_decide']} நாட்கள், {fast['days_to_decide']} "
                           f"நாட்களுக்கு எதிராக -- வேறுபாடு "
                           f"{slow['days_to_decide'] - fast['days_to_decide']} நாட்கள்.")
            else:
                verdict = (f"**{slow['application_number']}** took longer: "
                           f"{slow['days_to_decide']} days against "
                           f"{fast['days_to_decide']}, a difference of "
                           f"{slow['days_to_decide'] - fast['days_to_decide']} days.")
    elif aspect == "age":
        if left["submission_date"] == right["submission_date"]:
            verdict = (f"இரண்டும் {left['submission_date']} அன்று சமர்ப்பிக்கப்பட்டன."
                      if is_tamil else
                      f"Both were filed on {left['submission_date']}.")
        else:
            older = min(left, right, key=lambda s: s["submission_date"])
            newer = max(left, right, key=lambda s: s["submission_date"])
            try:
                gap = (date.fromisoformat(newer["submission_date"])
                       - date.fromisoformat(older["submission_date"])).days
                gap_txt = (f", {gap} நாட்கள் முன்பு" if is_tamil else f", {gap} days earlier")
            except ValueError:
                gap_txt = ""
            if _CMP_WANTS_NEWER.get():           # "which is newer" names the newer one
                if is_tamil:
                    verdict = (f"**{newer['application_number']}** புதியது: "
                               f"{newer['submission_date']} அன்று சமர்ப்பிக்கப்பட்டது, "
                               f"{older['submission_date']}க்கு எதிராக{gap_txt.replace('முன்பு', 'பின்பு')}.")
                else:
                    verdict = (f"**{newer['application_number']}** is newer: filed "
                               f"{newer['submission_date']} against "
                               f"{older['submission_date']}{gap_txt.replace('earlier', 'later')}.")
            elif is_tamil:
                verdict = (f"**{older['application_number']}** பழையது: "
                           f"{older['submission_date']} அன்று சமர்ப்பிக்கப்பட்டது, "
                           f"{newer['submission_date']}க்கு எதிராக{gap_txt}.")
            else:
                verdict = (f"**{older['application_number']}** is older: filed "
                           f"{older['submission_date']} against "
                           f"{newer['submission_date']}{gap_txt}.")
    elif aspect in ("area", "fee"):
        key = "area_sqm" if aspect == "area" else "fee_amount"
        lv, rv = left.get(key), right.get(key)
        _aspect_ta = "பரப்பளவு" if aspect == "area" else "கட்டணம்"
        if lv is None or rv is None:
            verdict = (f"{_aspect_ta} இரண்டிலும் பதிவு செய்யப்படவில்லை, எனவே "
                      f"ஒப்பிட முடியாது." if is_tamil else
                      f"The {aspect} is not recorded on both, so they cannot be compared.")
        elif lv == rv:
            verdict = (f"{_aspect_ta}-ல் இரண்டும் சமம்: {lv}." if is_tamil else
                      f"Both are the same on {aspect}: {lv}.")
        else:
            bigger = max(left, right, key=lambda s: s[key])
            smaller = min(left, right, key=lambda s: s[key])
            if is_tamil:
                verdict = (f"**{bigger['application_number']}** {_aspect_ta}-ல் "
                           f"பெரியது: {bigger[key]}, {smaller[key]}க்கு எதிராக.")
            else:
                verdict = (f"**{bigger['application_number']}** is larger on {aspect}: "
                           f"{bigger[key]} against {smaller[key]}.")

    return {"found": True, "kind": "applications", "aspect": aspect,
            "left": left, "right": right, "verdict": verdict}


def _compare_label(kind: str, value: str, is_tamil: bool = False) -> str:
    """How a comparison side is named in the answer."""
    if is_tamil:
        if kind == "channel":
            return {"CSC": "CSC / சேவை மையம்", "sub_registrar": "சார்-பதிவாளர்",
                    "citizen": "குடிமகன்"}.get(value, value)
        if kind == "status":
            return {"approved": "அங்கீகரிக்கப்பட்டது", "rejected": "நிராகரிக்கப்பட்டது",
                    "pending": "நிலுவையில் உள்ளது", "in_progress": "செயல்பாட்டில் உள்ளது",
                    "escalated": "மேல்நிலைப்படுத்தப்பட்டது"}.get(value, value)
        if kind == "type":
            return {"ISD": "ISD", "NISD": "NISD"}.get(value, value)
        return value
    if kind == "ward":
        return value
    if kind == "channel":
        return {"CSC": "CSC", "sub_registrar": "Sub-Registrar",
                "citizen": "Citizen portal"}.get(value, value)
    if kind == "status":
        return {"in_progress": "in progress"}.get(value, value)
    return value


_CMP_WANTS_NEWER = contextvars.ContextVar("cmp_wants_newer", default=False)


def _comparison_scope(message: str, spec: dict, is_tamil: bool):
    """({status_in, status_out, channel_out, type_out}, sentence) for "ISD vs NISD but not
    rejected" / "ISD vs NISD approved only" -- the counts are taken over what was asked."""
    kind = spec.get("kind")
    ex = {k: set(v) for k, v in _neg_scope.parse(message)[0].items()}
    for k, v in (_neg_scope.EXCLUDE.get() or {}).items():   # taken out of the message earlier this turn
        if k in ("status", "type", "channel"):
            ex.setdefault(k, set()).update(v)
    sides = {str(x) for x in (spec.get("sides") or [])}
    scope, words = {}, []
    if kind != "status" and ex.get("status"):
        scope["status_out"] = sorted(ex["status"])
    if kind != "channel" and ex.get("channel"):
        scope["channel_out"] = sorted(ex["channel"])
    if kind != "type" and ex.get("type"):
        scope["type_out"] = sorted(ex["type"])
    if kind != "status" and not scope.get("status_out"):
        st = fctx.refinement(message).get("status")
        if st and st not in sides:
            scope["status_in"] = [st]
    if not scope:
        return None, ""
    lab = lambda k, v: _compare_label(k, v, is_tamil) if k != "type" else v
    if scope.get("status_in"):
        words.append(("மட்டும்: " if is_tamil else "") + ", ".join(lab("status", v) for v in scope["status_in"])
                     + ("" if is_tamil else " only"))
    out = ([lab("status", v) for v in scope.get("status_out", [])] + [lab("channel", v) for v in scope.get("channel_out", [])]
           + list(scope.get("type_out", [])))
    if out:
        words.append((", ".join(out) + " தவிர") if is_tamil else "excluding " + ", ".join(out))
    return scope, (("எண்ணப்பட்டவை — " if is_tamil else "Counted over: ") + "; ".join(words) + ".")


def _requested_channel_filter(message: str) -> Optional[str]:
    """The one submission channel named in a bare listing request -- "citizen
    applications", "CSC applications" -- across every service code, not just
    the officer's ISD/NISD queue. "my CSC applications" keeps the existing
    pending_applications channel routing, since `parse_intent`'s rule for
    this already excludes "my"; this helper only runs when that rule fired.
    """
    channels = extract_submission_channels(message)
    return channels[0] if len(channels) == 1 else None


_HOOK_APP = __import__("contextvars").ContextVar("_HOOK_APP", default=None)


_WF_CUE = re.compile(
    r"\bwork[\s-]?flow\b|\bmovement\b|\btrail\b|\bwho\s+(?:has|is\s+holding)\s+it\b|\bwhere\s+is\s+(?:it|this|the\s+file|the\s+application)\b|"
    r"\bcurrent(?:ly)?\s+(?:desk|stage|at|with)\b|\bwhich\s+desk\b|\bpending\s+with\b|பணிப்பாய்வு|இப்போது\s*(?:யாரிடம்|எங்கே)|"
    r"\bwo?rk\s*fl\w*\b|\bwhat\s+happen\w*\s+to\b|\bactio\w*\s+(?:was\s+|were\s+)?(?:taken|eduthanga)\b|\bwho\s+updat\w*\b|"
    r"\bmove[ds]?\s+from\b|\breach(?:ed|ing)?\s+(?:the\s+)?(?:completed?|completion|closed|final|last)\b|"
    r"\bis\s+it\s+(?:at\s+|in\s+)?(?:the\s+)?(?:completed?|closed|final)\s+stage\b|"
    r"வொர்க்ஃப்ளோ|ஆக்ஷன்|ஆக்சன்", re.IGNORECASE)

# "has it reached the completed stage?" / "did it reach completion?" / "is it
# at the final stage?" -- a yes/no against the register's own coarse `stage`
# (Open / Completed), not a request to see anything else. Without this it had
# no handler at all: `_WF_CUE` above did not recognise "reached", so the
# question never reached this module and fell through to the full 16-field
# details card instead of the one-line answer it asked for.
_STAGE_REACHED_RE = re.compile(
    r"\breach(?:ed|ing)?\s+(?:the\s+)?(?:completed?|completion|closed|final|last)\s*(?:stage)?\b|"
    r"\bis\s+it\s+(?:at\s+|in\s+)?(?:the\s+)?(?:completed?|closed|final)\s+stage\b",
    re.IGNORECASE)

# "current stage?" / "where is it" / "which desk" / "who has it" / "pending
# with" ask for the file's PRESENT position only -- `_WF_CUE` also matches
# "workflow history", "what happened to", "action taken" and "who updated",
# which ask for the step-by-step log instead. Both used to get the exact same
# full "Workflow history for X (N step(s)): ..." card: a bare "current
# stage?" answered, but not the question that was actually asked.
_WF_STAGE_ONLY_RE = re.compile(
    r"\bcurrent(?:ly)?\s+(?:desk|stage|at|with)\b|\bwhich\s+desk\b|\bwho\s+(?:has|is\s+holding)\s+it\b|"
    # a pronoun-less "current stage?" / "where is it" is rewritten by
    # `unheld_qa.where_is()` into the canonical "where is {app} in the
    # workflow" before this function ever sees it, so the pronoun forms below
    # (it/this/the file/the application) never actually arrive here -- the
    # app-number form must be matched too, or every rewritten stage-only
    # question falls through to the full workflow-history branch instead.
    r"\bwhere\s+is\s+(?:it|this|the\s+file|the\s+application|\S+\s+in\s+the\s+workflow)\b|"
    r"\bpending\s+with\b|இப்போது\s*(?:யாரிடம்|எங்கே)",
    re.IGNORECASE)


async def _other_code_workflow(db, officer, message, gate_app, chat_history, ta):
    """"workflow history of <0178 file>", "where is it": a file under another service code has no ISD / NISD detail
    card to fall back on, and the whole details card is not the workflow. Same shape as the ISD / NISD history --
    unless the message asks only for the current stage/position, which gets the short answer instead."""
    if not _WF_CUE.search(message or "") or len((message or "").split()) > 14:
        return None
    app = extract_application_number(message) or gate_app or _unheld_qa.context_app(chat_history)
    if not app or app.count("/") < 3 or app.split("/")[1] in ("0153", "0154"):
        return None
    reg = await get_applications_by_numbers(db, officer, [app], include_rejected=True)
    rows = (reg or {}).get("applications") or []
    if not rows:
        return None
    if _STAGE_REACHED_RE.search(message or ""):
        text = await _othercode_detail.stage_reached(app, rows[0], ta)
    elif _WF_STAGE_ONLY_RE.search(message or "") and not _wants_workflow_history(message):
        text = await _othercode_detail.current_stage(db, app, rows[0], ta)
    else:
        text = await _othercode_detail.workflow(db, app, rows[0], ta)
    if text:
        _HOOK_APP.set(app)
    return text


async def _single_app_workflow_answer(db, officer, message, app_no, chat_history, is_tamil) -> Optional[str]:
    """A workflow-event question ("what is ITS latest recorded workflow
    event?") that narrowed to exactly ONE carried application: try the
    ISD/NISD history answer, then the any-service-code one, so the officer
    gets THIS file's own recorded workflow instead of `_scoped_list_answer`'s
    generic "N matching application(s)" sentence -- which is what a
    workflow-shaped question over a one-row carried list used to get,
    because nothing between the follow-up resolver and the list renderer
    ever tried a per-application workflow lookup for it."""
    if not app_no:
        return None
    text = await _workflow_history_answer(db, officer, message, app_no, is_tamil)
    if text:
        return text
    return await _other_code_workflow(db, officer, message, app_no, chat_history, is_tamil)


_DOCS_CUE = re.compile(r"\bdocu?\w*|\batta?c?h?ments?\b|ஆவண|டாக்குமெண்ட்", re.IGNORECASE)
_DOCS_SKIP = re.compile(r"\bmissing\b|\bmising\b|\bsd\b|\bsketch\b|\bdraughtsman\b|\.(?:pdf|csv|docx|txt)\b|\b(?:this|the|that|my)\s+(?:uploaded\s+)?file\b|"
                        r"\brequired\s+for\b|\bneeded\s+for\b|\bwhat\s+documents\s+(?:are|do)\b(?!.*\d{4}/)", re.IGNORECASE)


async def _attached_documents(db, officer, message, gate_app, chat_history, ta):
    """"what documents were uploaded for X", "list all attachments for X", "which documents are verified":
    read from the source attachment extract for any service code. Missing-document questions stay with the
    application-information record; a chat upload ("this file", "report.pdf") stays with attachment_qa."""
    m = message or ""
    if not _DOCS_CUE.search(m) or _DOCS_SKIP.search(m) or len(m.split()) > 16:
        return None
    app = extract_application_number(m) or gate_app or _unheld_qa.context_app(chat_history)
    if not app or app.count("/") < 3:
        return None
    reg = await get_applications_by_numbers(db, officer, [app], include_rejected=True)
    if not (reg or {}).get("applications"):
        return None              # not this officer's file: the jurisdiction refusal answers it
    _HOOK_APP.set(app)
    return await _othercode_detail.attachments(db, app, ta)


_SAME_IP_CUE = re.compile(r"\b(?:same|identical|matching)\s+(?:submission\s+)?ip\b|\bshare\s+(?:the\s+)?(?:same\s+)?(?:submission\s+)?ip\b", re.IGNORECASE)
_DIFF_IP_CUE = re.compile(r"\bdifferent\s+(?:submission\s+)?ip\b", re.IGNORECASE)


async def _ip_comparison_answer(db, officer, message, gate_app, chat_history, ta):
    """"which applications share the same submission IP as X?" / "...have a different submission
    IP from X?" -- a dedicated comparison, scoped to this officer's own jurisdiction throughout
    (the SQL query itself filters by jurisdiction; nothing is checked only in prose), always
    excluding the named application, always excluding rows with no recorded IP. Previously
    unhandled: no module claimed either phrasing, so "same submission IP" fell to an unrelated
    owner-record comparison (the generic "compare" wording matched there first) and "different
    submission IP" just echoed the named application's own IP back without ever listing others."""
    same = bool(_SAME_IP_CUE.search(message or ""))
    diff = bool(_DIFF_IP_CUE.search(message or ""))
    if not (same or diff) or len((message or "").split()) > 20:
        return None
    app = extract_application_number(message) or gate_app or _unheld_qa.context_app(chat_history)
    if not app or app.count("/") < 3:
        return None
    from backend.services.postgres import get_applications_by_submission_ip
    data = await get_applications_by_submission_ip(db, officer, app, same=same)
    if data is None:
        return None
    _HOOK_APP.set(app)
    if not data.get("ip"):
        return (f"{app}-க்கான சமர்ப்பிப்பு IP பதிவில் இல்லை, ஒப்பிட முடியவில்லை." if ta
                else f"No submission IP is recorded for {app}, so it cannot be compared.")
    rows = data["applications"]
    which = "same" if same else "different"
    which_ta = "அதே" if same else "வேறு"
    if not rows:
        return (f"{app} ({data['ip']}) தவிர வேறு எந்த விண்ணப்பமும் {which_ta} IP-ஐ பகிரவில்லை." if ta
                else f"No other application in your jurisdiction has {'the same' if same else 'a different'} "
                     f"submission IP as {app} ({data['ip']}).")
    nums = [r["application_number"] for r in rows]
    shown = nums[:15]
    tail = f" and {len(nums) - 15} more" if len(nums) > 15 else ""
    if ta:
        return f"{app} ({data['ip']}) உடன் {which_ta} IP பகிரும் {len(nums)} விண்ணப்பங்கள்: {', '.join(shown)}{tail}."
    return (f"{len(nums)} application(s) have {'the same' if same else 'a different'} submission IP "
            f"{'as' if same else 'from'} {app} ({data['ip']}): {', '.join(shown)}{tail}.")


_DECIDER_CUE = re.compile(r"\bwho\s+(?:approved|rejected|signed|decided|closed|cleared|sanctioned)\b|\bwho\s+(?:is|was)\s+(?:the\s+)?(?:approver|approving\s+officer)\b", re.IGNORECASE)


async def _who_decided(db, officer, message, gate_app, chat_history, ta):
    if not _DECIDER_CUE.search(message or "") or len((message or "").split()) > 12:
        return None
    app = extract_application_number(message) or gate_app or _unheld_qa.context_app(chat_history)
    if not app or app.count("/") < 3:
        return None
    reg = await get_applications_by_numbers(db, officer, [app], include_rejected=True)
    rows = (reg or {}).get("applications") or []
    if not rows:
        return None
    text = await _othercode_detail.decider(db, app, rows[0], ta)
    if text:
        _HOOK_APP.set(app)
    return text


async def _early_subdivision_answer(db, officer, message, intent, gate_app, chat_history, language):
    """The record answer as the card every other answer uses (see card_html)."""
    ta = language in ("ta", "tanglish")
    _wd = await _who_decided(db, officer, message, gate_app, chat_history, ta)
    if _wd:
        return _wd
    _ipc = await _ip_comparison_answer(db, officer, message, gate_app, chat_history, ta)
    if _ipc:
        return _ipc
    _wf = await _other_code_workflow(db, officer, message, gate_app, chat_history, ta)
    if _wf:
        return _wf              # plain lines: the shape of an ISD / NISD file's workflow history
    _docs = await _attached_documents(db, officer, message, gate_app, chat_history, ta)
    if _docs:
        return _docs
    _nh = _unheld_qa.answer_app(message, chat_history, ta)
    if _nh:
        _HOOK_APP.set(_nh[1])
        return _card_html.card(_nh[0], ta)
    out = await _early_subdivision_answer_inner(db, officer, message, intent, gate_app, chat_history, language)
    if isinstance(out, str) and out and (_negq.KIND.get() or _negq.HYP.get()):
        out = _negq.lead(_negq.shape(out, ta), ta)
    return _card_html.card(out, ta) if isinstance(out, str) else out


_ABSENT_RECORDS = (  # (message cue, English sentence, Tamil sentence): records a file of another service code cannot carry
    (r"igrs", "No IGRS owner record is held for {app}. IGRS owner rows exist only for NISD (0153) files referred by a Sub-Registrar.",
     "{app}-க்கு IGRS உரிமையாளர் பதிவு இல்லை. Sub-Registrar பரிந்துரைத்த NISD (0153) கோப்புகளுக்கு மட்டுமே அது உண்டு."),
    (r"returned\s+owner|return\s+owner", "No returned-owner record is held for {app} (the returned-owner table holds no records for any application at present).",
     "{app}-க்கு திருப்பப்பட்ட உரிமையாளர் பதிவு இல்லை (அந்த அட்டவணையில் தற்போது எந்த பதிவும் இல்லை)."),
    (r"new\s+owner", "No new-owner record is held for {app}. Only a full-field patta transfer (NISD, 0153) carries one.",
     "{app}-க்கு புதிய உரிமையாளர் பதிவு இல்லை. முழு புல பட்டா மாற்றத்துக்கு (NISD, 0153) மட்டுமே அது இருக்கும்."),
    (r"old\s+owner|chitta", "No old-owner record is held for {app}. An ISD file carries a chitta old-owner record and an NISD file a full-field old-owner record.",
     "{app}-க்கு பழைய உரிமையாளர் பதிவு இல்லை. ISD கோப்புக்கு சிட்டா பதிவும் NISD கோப்புக்கு முழு புல பதிவும் உண்டு."),
    (r"full[\s-]*field|zdt", "No full-field patta transfer record is held for {app}. Only an NISD (0153) file carries one.",
     "{app}-க்கு முழு புல பட்டா மாற்ற பதிவு இல்லை. NISD (0153) கோப்புக்கு மட்டுமே அது இருக்கும்."),
)


async def _other_code_absent(db, officer, message, app_no, detail, ta):
    """A file of another service code (0158 ...) lives in the register only: the owner / transfer records of
    an ISD or NISD file do not exist for it. Said plainly, after the officer's access is checked."""
    if app_no.count("/") < 3 or app_no.split("/")[1] in ("0153", "0154") or (detail and detail.get("found") is not False):
        return None
    if _um_parse(message) or _na_parse(message) or _cmflow_qa.parse(message) or _tslr_parse(message) or _fline_qa.parse(message)["keys"]:
        return None
    for cue, en, tam in _ABSENT_RECORDS:
        if re.search(cue, message, re.IGNORECASE):
            reg = await get_applications_by_numbers(db, officer, [app_no], include_rejected=True)
            if (reg or {}).get("applications"):
                return (tam if ta else en).format(app=app_no)
            return None
    return None


async def _early_subdivision_answer_inner(db, officer, message, intent, gate_app, chat_history, language):
    """The sub-division record answer, taken BEFORE the "not held in your register"
    guards. Those guards name tax rate, plot, soil and group numbers as absent
    even though the plot rows hold them; this answers first when the message asks
    for such a field about one application. The application is the one named in
    the message, else the one the gate confirmed or the conversation is about --
    and never when the message names a different survey number, or the intent
    is not an application lookup (a survey or listing question keeps its handler)."""
    _HOOK_APP.set(None)
    # A bare channel fragment ("application from sro", "applications from
    # csc") with no application number the officer has named -- same guard,
    # same reasoning, as `_cm_strong`'s: `_appinfo_qa.parse()` below reads
    # "sro" as the SRO-name FIELD of one record regardless of listing shape,
    # and this function is called unconditionally, ahead of and independent
    # of `_cm_strong` -- so fixing `_cm_strong` alone left this path still
    # resolving "application from sro" to the SRO field of whichever
    # application a prior LISTING happened to mention first, instead of
    # leaving it for the channel-scoped listing intent to answer.
    if (not extract_application_number(message) and extract_submission_channels(message) and (
            _qualifier_guard.listing_shaped(message)
            or (not _app_numbers_in_recent_context(chat_history, depth=6)[0] and len((message or "").split()) <= 10))):
        # A listing-shaped message ("what are applications from SRO?") is never a field
        # question about a stale application, even when the officer named one earlier in
        # the conversation -- the shape of THIS message settles it, the way `_cm_strong`'s
        # equivalent guard now also does. Without the `listing_shaped` escape, a prior
        # "<app> details" turn made every later channel-scoped listing resolve to that
        # one file's SRO/CSC/channel field instead of the register-wide listing.
        return None
    attrs = _subdivision_qa.attributes(message)
    _oo_attrs = _oldowner_qa.attributes(message) or _oo_exists_parse(message, chat_history)
    _cm_q = _cmflow_qa.parse(message)
    _ai_keys = _appinfo_qa.parse(message)
    _apk = _appinfo_qa.applicant_keys(message)
    _fl = _fline_qa.parse(message)
    _xc = _crosscheck_qa.parse(message)
    _ig = _igrs_owner_qa.parse(message) if _igrs_owner_qa.claims(message) else None
    _ig_strong = _igrs_owner_qa.claims_strong(message)
    _sdp = _sd_parse(message, chat_history)
    _tsl = _tslr_parse(message, chat_history)
    _um = _um_parse(message, chat_history)
    _na = _na_parse(message, chat_history)
    _ft = _fulltransfer_qa.parse(message) if _fulltransfer_qa.claims(message) else None
    _ft_strong = _fulltransfer_qa.claims_strong(message)
    _nw = _newowner_qa.parse(message) if _newowner_qa.claims(message) else None
    if _nw is None and _owner_ctx_compare(message, chat_history):
        _nw = _newowner_qa.parse("compare the new owner and the old owner " + message)
    _nw_strong = _newowner_qa.claims_strong(message)
    if _nw and _nw["kind"] == "uds_def":
        return _newowner_qa.UDS_TA if language in ("ta", "tanglish") else _newowner_qa.UDS_EN
    if not _subdivision_qa.claims(attrs) and not _oo_attrs and not _cm_q and not _ai_keys and not _apk and not _fl["keys"] and not _fl.get("fline") and not _xc and not _ig and not _nw and not _ft and not _sdp and not _tsl and not _um and not _na:
        return None
    named = extract_application_number(message)
    if not named:
        # a back-reference ("this / intha / இந்த") also claims a register-wide
        # listing, follow-up or survey intent: the officer points at the open application
        _backref = re.search(r"\b(?:this|it|its|intha|indha|idhu|idhoda|ithu)\b|இந்த|இதன்|இது",
                             message, re.IGNORECASE)
        if not _oo_attrs and not _cm_q and not _ai_keys and not _apk and not _fl["keys"] and not _xc and not _ig_strong and not _nw_strong and not _ft_strong and not _sdp and not _tsl and not _um and not _na and intent not in ("application_status", "general_query", "survey_detail", "survey_owners") and not (
                _backref and intent in ("all_service_code_applications", "followup_list")):
            return None
        if re.search(r"\bsurvey\s*(?:no\.?|number)?\s*\d|\bsy\.?\s*\d", message, re.IGNORECASE):
            return None
    # "what is TSLR?" / "what is an F-Line?" / "list TSLR applications" / "show
    # my SRO applications" -- a bare definition OR a register-wide listing,
    # never a question about whichever application chat_history last named.
    # `_fline_qa.parse()` / `_cmflow_qa.parse()` / `_tslr_parse()` /
    # `_appinfo_qa.parse()` / `.applicant_keys()` / ... all read the bare
    # record-type or field word alone as a truthy claim regardless of
    # listing shape (it genuinely might be a real per-application question,
    # "what is the F-Line verify date?"), so the claim itself cannot tell the
    # two apart -- only the app-number resolution below, where implicit
    # continuation would otherwise silently reattach the conversation's last
    # application, can. Gated to the same narrow concept list
    # `_is_bare_concept_definition` already uses (plus the listing shape), so
    # an ordinary field question ("what is the SIS recommendation?") still
    # resolves through chat_history as before.
    _bare_defn = (_is_bare_concept_definition(message) or _is_register_wide_listing(message)
                  or _is_ordinal_visit_reference(message))
    app_no = named or gate_app or (None if _bare_defn else _extract_app_number_from_context(
        message, chat_history, allow_implicit_continuation=True))
    if not app_no and not _bare_defn and (_oo_attrs or _cm_q or _ai_keys or _apk or _fl["keys"] or _xc or _ig or _nw or _ft or _sdp or _tsl or _um or _na):
        for _m in reversed(chat_history or []):
            _mm = _oldowner_qa._APP_NO.search((_m.get("content") if isinstance(_m, dict) else str(_m)) or "")
            if _mm:
                app_no = _mm.group(0)
                break
    if not app_no:
        return None
    _HOOK_APP.set(app_no)
    detail = await get_application_detail(db, app_no, officer=officer)
    _absent_txt = await _other_code_absent(db, officer, message, app_no, detail, language in ("ta", "tanglish"))
    if _absent_txt:
        return _absent_txt
    if _na:
        _okn = bool(detail and detail.get("found") is not False)
        _regn = None
        if app_no.count("/") >= 3:
            _gn = await get_applications_by_numbers(db, officer, [app_no], include_rejected=True)
            _gnl = (_gn or {}).get("applications") or []
            _okn = _okn or bool(_gnl)
            if _gnl:
                _regn = {"status": _gnl[0].get("status")}
        if _okn:
            _narows = await _natham_qa.load(db, app_no)
            _naoth = {}
            if _na["mode"] == "compare" and _narows is not None:
                _naoth = {"igrs": bool(await _igrs_owner_qa.load(db, app_no)), "chitta": bool((await _oldowner_qa.load(db, app_no))[0]),
                          "tslr": bool(await _tslr_qa.load(db, app_no)), "fullfield": bool(await _fulltransfer_qa.load(db, app_no)),
                          "new": bool(await _newowner_qa.load_new(db, app_no)), "old": bool(await _newowner_qa.load_old_nisd(db, app_no))}
            return _natham_qa.answer(_na, app_no, _narows, await _uamod_qa.load(db, app_no), _naoth, _regn, language in ("ta", "tanglish"))
    if _um and (_um.get("explicit") or (app_no.count("/") >= 3 and app_no.split("/")[1] in ("0158", "0159", "0160", "0162", "0164", "0169"))):
        _oku = bool(detail and detail.get("found") is not False)
        _regu = None
        if app_no.count("/") >= 3:
            _gu = await get_applications_by_numbers(db, officer, [app_no], include_rejected=True)
            _gul = (_gu or {}).get("applications") or []
            _oku = _oku or bool(_gul)
            if _gul:
                _regu = {"status": _gul[0].get("status")}
        if _oku:
            return _uamod_qa.answer(_um, app_no, await _uamod_qa.load(db, app_no), _regu, language in ("ta", "tanglish"))
    if _tsl:
        _okt = bool(detail and detail.get("found") is not False)
        _regt = None
        if app_no.count("/") >= 3:
            _gt = await get_applications_by_numbers(db, officer, [app_no], include_rejected=True)
            _gl = (_gt or {}).get("applications") or []
            _okt = _okt or bool(_gl)
            if _gl:
                _regt = {"survey_no": _gl[0].get("survey_no"), "subdivision_no": _gl[0].get("sub_division_no"),
                         "ward_number": _gl[0].get("ward_number"), "block_number": _gl[0].get("block_number")}
        if _okt:
            return _tslr_qa.answer(_tsl, app_no, await _tslr_qa.load(db, app_no), _regt, language in ("ta", "tanglish"))
    if _sdp and detail and detail.get("found") is not False:
        _sdrows = await _sdattach_qa.load(db, app_no)
        _sdsubs = (await _oldowner_qa.load(db, app_no))[1]
        _sdother = {"fline": bool(await _fline_qa.load(db, app_no)), "fullfield": bool(await _fulltransfer_qa.load(db, app_no)),
                    "survey_records": len(_sdsubs)}
        return await _sdattach_qa.answer(_sdp, app_no, _sdrows, detail, _sdsubs, _sdother, language in ("ta", "tanglish"))
    _ft_ok = bool(detail and detail.get("found") is not False)
    if _ft and not _ft_ok and app_no.count("/") >= 3:
        # the full-field transfer extract also carries 0165 files, which are not in `applications`:
        # access through the register, as every other record module does
        _ft_ok = bool(((await get_applications_by_numbers(db, officer, [app_no], include_rejected=True)) or {}).get("applications"))
    if _ft and _ft_ok:
        _ftrows = await _fulltransfer_qa.load(db, app_no)
        _ftta = language in ("ta", "tanglish")
        # "SIS recommendation" claims this module on vocabulary alone (SISREC is in its
        # DISTINCT set), but full_field_patta_transfer_urban only ever has a row for NISD
        # (0153) -- an ISD (0154) file has none, is expected to have none, and answering
        # "no full-field patta transfer record" explains the wrong table instead of this
        # file's OWN workflow_history, which may genuinely have the note being asked for.
        # Try the canonical workflow-history answer first and only fall through to the
        # full-field-transfer explanation when that returns nothing.
        if not _ftrows and app_no.count("/") >= 3 and app_no.split("/")[1] == "0154":
            _wftxt = await _workflow_history_answer(db, officer, message, app_no, _ftta)
            if _wftxt:
                return _wftxt
        # Same trap for an F-Line (0178) file: "transaction status" is vocabulary this
        # module shares with fline_qa's own TXN field, and full_field_patta_transfer_urban
        # never has a row for a 0178 file either. fline_qa actually has this field.
        if not _ftrows and app_no.count("/") >= 3 and app_no.split("/")[1] == "0178" and _fl["keys"]:
            _frow_ft = await _fline_qa.load(db, app_no)
            _fltxt = _fline_qa.answer(_fl["keys"], app_no, _frow_ft, _ftta)
            if _fltxt:
                return _fltxt
        if _ft["kind"] == "compare":
            if _ftrows or _ft_strong:
                _ftxt = _fulltransfer_qa.answer_compare(_ft, app_no, _ftrows, await _newowner_qa.load_new(db, app_no),
                                                        await _newowner_qa.load_old(db, app_no), (await _oldowner_qa.load(db, app_no))[1],
                                                        detail, _ftta)
                if _ftxt:
                    return _ftxt
        elif _ftrows or _ft["distinct"]:
            _ftxt = _fulltransfer_qa.answer(_ft["keys"], app_no, _ftrows, _ftta)
            if _ftxt:
                return _ftxt
    _nwrows = []
    if _nw and detail and detail.get("found") is not False:
        _nwrows = await _newowner_qa.load_new(db, app_no)
    _ig_rows_pre = await _igrs_owner_qa.load(db, app_no) if (_ig and detail and detail.get("found") is not False) else []
    if _nw and _nw["kind"] in ("return_field", "return_compare") and detail and detail.get("found") is not False:
        _rta = language in ("ta", "tanglish")
        _rrows = await _newowner_qa.load_return(db, app_no)
        _rtotal = await _newowner_qa.count_return(db) if not _rrows else len(_rrows)
        if _nw["kind"] == "return_compare":
            return _newowner_qa.answer_return_compare(_nw, app_no, _rrows, _rtotal, await _newowner_qa.load_new(db, app_no),
                                                      await _newowner_qa.load_old(db, app_no), await _igrs_owner_qa.load(db, app_no),
                                                      detail, _rta)
        return _newowner_qa.answer_return_fields(_nw["keys"], app_no, _rrows, _rtotal, await _appinfo_qa.load(db, app_no), detail, _rta)
    _nw_old_only = bool(_nw and _nw.get("old_only") and _nw["kind"] == "field")
    if _nw_old_only and detail and detail.get("found") is not False:
        # an OLD-owner field of a full-field (NISD) transfer; an ISD file's old owner is the chitta record,
        # answered further down by oldowner_qa
        _nold_rows = await _newowner_qa.load_old_nisd(db, app_no)
        if _nold_rows:
            return _newowner_qa.answer_fields(_nw["keys"], app_no, _nold_rows, language in ("ta", "tanglish"), old=True)
    # "new owner" on an ISD (0154) file names the proposed sub-division owner
    # (chitta_temp_subdivclub -> application_sub_division_owners, answered by
    # subdivision_qa), never the full-field-transfer new owner this module
    # reads (full_field_patta_transfer_new_owner, NISD/0165 only). Without this,
    # an empty _nwrows on an ISD file was read as "no new-owner record is held
    # for this application" -- false: the record exists, just in the other
    # table, and subdivision_qa (below) would have answered it correctly.
    _nw_isd_defer = (_nw and not _nwrows and _nw["kind"] == "field" and app_no.count("/") >= 3
                     and app_no.split("/")[1] == "0154" and _subdivision_qa.claims(attrs))
    if _nw and detail and detail.get("found") is not False and not (_ig and _ig["igrs"]) and not _ig_rows_pre and not _nw_isd_defer             and (_nwrows or (_nw["kind"] in ("field", "compare") and not _nw_old_only)):
        _nta = language in ("ta", "tanglish")
        if _nw["kind"] == "compare":
            _nold = await _newowner_qa.load_old(db, app_no)
            _nig = await _igrs_owner_qa.load(db, app_no)
            _nfl = await _fline_qa.load(db, app_no)
            _nchit, _nsubs = await _oldowner_qa.load(db, app_no)
            _ntxt = await _newowner_qa.answer_compare(_nw, app_no, _nwrows, _nold, _nig, _nfl, _nsubs, detail, _nta)
        else:
            _ntxt = _newowner_qa.answer_fields(_nw["keys"], app_no, _nwrows, _nta)
        if _ntxt:
            return _ntxt
    if _ig and detail and detail.get("found") is not False:
        _igrows = _ig_rows_pre
        _igta = language in ("ta", "tanglish")
        if _ig["kind"] == "compare" and (_ig["igrs"] or _igrows):
            _igchitta, _ = await _oldowner_qa.load(db, app_no)
            _iginfo = await _appinfo_qa.load(db, app_no)
            return _igrs_owner_qa.answer_compare(_ig, app_no, _igrows, _igchitta, _iginfo, detail, _igta)
        if _igrows or _ig["igrs"] or (_igrs_owner_qa.IMAGE in _ig["keys"] and not _nwrows):
            return _igrs_owner_qa.answer_fields(_ig["keys"], app_no, _igrows, _igta)
    if _xc and detail and detail.get("found") is not False:
        _xinfo = await _appinfo_qa.load(db, app_no)
        _xown, _xsub = await _oldowner_qa.load(db, app_no)
        _xflow = await _cmflow_qa.load(db, app_no)
        _xtxt = await _crosscheck_qa.answer(_xc, message, app_no, db, officer, detail, _xinfo, _xown, _xsub, _xflow,
                                            language in ("ta", "tanglish"))
        if _xtxt:
            return _xtxt
    if (_fl["keys"] or _fl.get("fline")) and app_no.count("/") >= 3:
        _is_fl = app_no.split("/")[1] == "0178"
        if _is_fl or _fl.get("fline") or _fline_qa.claims(message):
            _okf = bool(detail and detail.get("found") is not False)
            if not _okf:
                _regf = await get_applications_by_numbers(db, officer, [app_no], include_rejected=True)
                _okf = bool((_regf or {}).get("applications"))
            if _okf:
                _frow = await _fline_qa.load(db, app_no) if _is_fl else None
                _ftxt = _fline_qa.answer(_fl["keys"], app_no, _frow, language in ("ta", "tanglish"))
                if _ftxt:
                    return _ftxt
    if not _ai_keys and _apk and app_no.count("/") >= 3 and app_no.split("/")[1] not in ("0153", "0154"):
        _ai_keys = _apk
    if _ai_keys:
        if detail and detail.get("found") is not False:
            _arow = await _appinfo_qa.load(db, app_no)
            if _arow is not None:
                return _appinfo_qa.answer(_ai_keys, app_no, _arow, detail, language in ("ta", "tanglish"))
        else:
            # a file under another service code: access is checked through the register, and
            # the record (an F-Line file has one) is read, never assumed absent
            _reg0 = await get_applications_by_numbers(db, officer, [app_no], include_rejected=True)
            _reg0l = (_reg0 or {}).get("applications") or []
            if _reg0l:
                _rd = {"status": _reg0l[0].get("status"), "submission_channel": _reg0l[0].get("submission_channel")}
                return _appinfo_qa.answer(_ai_keys, app_no, await _appinfo_qa.load(db, app_no), _rd,
                                          language in ("ta", "tanglish"))
    # "Tahsildar signature date" names the TAHSILDAR desk, which cmflow_qa.parse()
    # reads as a correction/modification recommendation question -- but that
    # table only ever covers 0158-0169, and an ISD/NISD file's real signature
    # date is patta_transfers.tahsildar_signature_date, a different record
    # entirely. Without this, cmflow_qa claimed it anyway and answered "no
    # correction/modification workflow record is held" for a file that was
    # never going to have one. See the matching guard in `_cm_strong`.
    if (_cm_q and app_no.count("/") >= 3 and app_no.split("/")[1] in ("0153", "0154")
            and re.search(r"\bsign\w*\s+date\b|\bdate\s+of\s+sign\w*\b", message or "", re.IGNORECASE)):
        _cm_q = None
    if _cm_q:
        _ok = bool(detail and detail.get("found") is not False)
        if not _ok:
            _reg = await get_applications_by_numbers(db, officer, [app_no], include_rejected=True)
            _ok = bool((_reg or {}).get("applications"))
        if _ok:
            _row = await _cmflow_qa.load(db, app_no)
            _svc = {"service_code": app_no.split("/")[1] if app_no.count("/") >= 3 else ""}
            if _row or not (_cm_q.desks == ["sis"] and not _cm_q.mode and not _cm_q.extra and detail
                            and detail.get("found") is not False):
                _txt = _cmflow_qa.answer(message, _cm_q, app_no, _row, _svc, language in ("ta", "tanglish"))
                if _txt:
                    return _txt
    if (_oo_attrs or _subdivision_qa.claims(attrs)) and not (detail and detail.get("found") is not False):
        # an application under another service code: no old-owner / sub-division plot rows to read
        _regx = await get_applications_by_numbers(db, officer, [app_no], include_rejected=True)
        if (_regx or {}).get("applications"):
            _ta = language in ("ta", "tanglish")
            _code = app_no.split("/")[1] if app_no.count("/") >= 3 else ""
            if _oo_attrs:
                _oo, _sub = await _oldowner_qa.load(db, app_no)
                return _oldowner_qa.answer(message, _oo_attrs, app_no, _oo, _sub, _ta)
            if not await _subdivision_qa.load_plots(db, app_no):
                return (f"விண்ணப்பம் {app_no} (சேவை {_code})-க்கு உட்பிரிவு மனை பதிவு இல்லை." if _ta else
                        f"No sub-division plot record is held for {app_no} (service code {_code}).")
    if _oo_attrs and detail and detail.get("found") is not False:
        _oo, _sub = await _oldowner_qa.load(db, app_no)
        return _oldowner_qa.answer(message, _oo_attrs, app_no, _oo, _sub, language in ("ta", "tanglish"))
    return await _subdivision_answer(db, officer, message, detail, app_no,
                                     language in ("ta", "tanglish"))


async def _subdivision_answer(db, officer, message: str, sd: dict, app_no: str, is_tamil: bool):
    """An answer about the sub-division plots of one application (temporary / new
    sub-division number, existing / new patta, area in hectare-ares-sq.m, land
    use, tax, soil, plot, group numbers, owners), or None when the message asks
    for none of them. `sd` is the application detail the caller already fetched
    under the officer's jurisdiction, so access has been checked before any plot
    row is read. See subdivision_qa."""
    attrs = _subdivision_qa.attributes(message)
    if not app_no or not sd or sd.get("found") is False or not _subdivision_qa.claims(attrs):
        return None
    plots = await _subdivision_qa.load_plots(db, app_no)
    owners, owner_rows = [], None
    if _subdivision_qa.OWNER in attrs:
        owners = await _subdivision_qa.load_owners(db, app_no)
        _survey = sd.get("survey_no") or sd.get("survey_number")
        owner_rows = await get_survey_owners(db, _survey, officer=officer) if _survey else None
    return _subdivision_qa.answer(attrs, app_no, sd, plots, owners, owner_rows, is_tamil)


async def _register_completion(db, officer, message: str):
    """Completion rate across every service code in the officer's jurisdiction.

    "What is my completion rate" counted only the ISD/NISD files assigned to the
    officer (38 of 57) while the register holds 105 completed of 152. A file is
    complete when it is approved or rejected, whatever its service code.
    """
    from backend.models import ServiceRegisterEntry as SRE
    ml = message.lower()
    is_this_month = any(p_ in ml for p_ in ["this month", "month", "monthly", "current month"])
    month_start = date.today().replace(day=1)
    period_start, period_end, period_label = _period_from_message(message)
    where = [SRE.ward_id.in_(officer.jurisdiction_ids or [])]
    done = SRE.status.in_(["approved", "rejected"])
    if period_start and period_end:
        win = and_(SRE.submission_date >= period_start, SRE.submission_date <= period_end)
        c_where, t_where = [*where, done, win], [*where, win]
        label = period_label or f"{period_start} to {period_end}"
    elif is_this_month:
        c_where = [*where, done, SRE.updated_at >= month_start]
        t_where = [*where, SRE.submission_date >= month_start]
        label = f"this month ({month_start.strftime('%B %Y')})"
    else:
        c_where, t_where, label = [*where, done], where, "overall"
    completed = await db.scalar(select(func.count()).select_from(SRE).where(and_(*c_where))) or 0
    total = await db.scalar(select(func.count()).select_from(SRE).where(and_(*t_where))) or 0
    return completed, total, label


async def _register_listing(db, officer, message: str, session_id, chat_history,
                            followup_ctx=None) -> dict:
    """An application listing across EVERY service code, scoped the way the
    ISD/NISD queue is: status, period, ward / block, channel, sort order.

    "Show my pending applications" used to answer from `applications`, which
    only holds ISD/NISD -- 17 of an officer's 53 pending files; the other
    service codes (0156, 0158, 0159, 0165, 0178, ...) were silently missing.
    This reads the flat register instead, so no service code is favoured, and
    reads the SAME scopes out of the message the ISD/NISD listing does so that
    "pending applications in June", "approved applications in ward 102" and
    "first 5 pending applications" all mean the same thing for every type. A
    question that names ISD/NISD, or something only they carry (overdue, field
    visits, per-file detail), never gets here -- see rag._is_register_wide_listing.
    """
    ml = message.lower()
    start_d, end_d = extract_date_range(message)
    month_scopes = extract_month_scopes(message)
    if month_scopes:
        start_d, end_d = month_scopes[0][0], month_scopes[-1][1]
    has_range = start_d is not None or end_d is not None
    negated = any(w in ml for w in ["not between", "not in", "outside", "other than",
                                    "இடைப்பட்டவை அல்ல", "தவிர"])
    year = month = None
    if not has_range:
        _ym = _year_match(message)
        year = int(_ym.group(1)) if _ym else None
        month = extract_month_from_query(message)
        if month and not year:
            year = _resolve_month_year(month)

    ward_num, block_num = await _geo_scope(db, session_id, message, chat_history, officer)
    # `_geo_scope()` only infers a ward from the PREVIOUS table when the
    # CURRENT message uses the word "ward" itself (just without a number) --
    # right for "how many in this ward", wrong for a bare filter-refinement
    # follow-up ("exclude rejected applications") that names no geography
    # word at all. That one silently widened from "ward 001" to the whole
    # jurisdiction. Only inherited when the carried context is ITSELF a
    # register-wide listing (never an unrelated prior topic), and never when
    # this message named its own ward/block.
    _carried_code = None
    _carried_excl_code = None
    if not ward_num and not block_num and followup_ctx is not None \
            and followup_ctx.intent == "all_service_code_applications":
        ward_num = ward_num or (followup_ctx.filters or {}).get("ward_number")
        block_num = block_num or (followup_ctx.filters or {}).get("block_number")
        _carried_code = (followup_ctx.filters or {}).get("service_code")
        _carried_excl_code = (followup_ctx.filters or {}).get("service_code_excluded")
    channels = extract_submission_channels(message) or None
    service_code = _requested_service_code_filter(message)
    excluded_service_code = _excluded_service_code_filter(message)
    if not service_code and not excluded_service_code and _carried_excl_code:
        excluded_service_code = _carried_excl_code
    # A bare continuation phrase ("exclude rejected applications", rewritten
    # by `_resolve_rejected_widen` to a fresh register-wide message that
    # names no service code of its own) used to drop whichever code the
    # prior turn was scoped to and silently widen to every code -- "show
    # applications for service code 0158" -> "exclude rejected applications"
    # answered from all 126 pending/approved applications across every
    # code, not the 20 under 0158. Same carry-forward rule as ward/block
    # above: only when THIS message names no code of its own and the carried
    # context is itself a register-wide listing.
    if not service_code and not excluded_service_code and _carried_code:
        service_code = _carried_code

    # Status. A named status always wins. With none named, a plain "show my
    # applications" is the open queue (pending), exactly as for ISD/NISD; a
    # period, a channel, a service code or an "all / total / new" word asks the
    # register instead and is not narrowed to pending.
    named = _explicit_status_request(ml)
    status = _requested_status_filter_for_register(message)
    # "Show pending applications for 0158." then "Exclude rejected
    # applications." -- rewritten upstream to "not rejected applications.",
    # which `_requested_status_filter_for_register` reads as the COMPLEMENT
    # of {rejected} across every status (pending/approved/in_progress/
    # escalated) -- correct in isolation, but this turn named no status of
    # its own; it only asked to drop rejected FROM THE LIST ALREADY ON
    # SCREEN. "Exclude rejected" after a pending-only chain should be a
    # no-op (rejected was never in it), not a broadening to every other
    # status too. When the carried chain's own starting status is a single
    # value already inside that complement, the officer's actual scope is
    # narrower than the negation alone implies -- intersect down to it
    # rather than handing back the wider complement.
    if (isinstance(status, list) and len(status) > 1
            and followup_ctx is not None and followup_ctx.intent == "all_service_code_applications"):
        _carried_status = (followup_ctx.filters or {}).get("base_query_filters", {}).get("status")
        if isinstance(_carried_status, str) and _carried_status in status:
            status = _carried_status
    wants_everything = bool(re.search(
        r"\b(?:all|every|total|new|recent|latest)\b|அனைத்து|மொத்த", ml))
    # neg_scope.rewrite() injects a literal "all" into a negated query ("applications
    # not from sro" -> "all application") just to keep it register-wide instead of
    # narrowed to the pending desk queue -- the officer never asked for "all". Taken
    # at face value that word also told get_all_service_code_applications() to stop
    # hiding rejected files, so "not from sro" silently included them while "from sro"
    # did not. When this listing is servicing a negation rewrite (_neg_scope.EXCLUDE
    # is still set -- cleared only later, by _neg_scope.apply()), the rejected-hiding
    # decision reads the officer's own typed words instead of the rewritten ones.
    wants_rejected_too = wants_everything
    if _neg_scope.EXCLUDE.get():
        wants_rejected_too = bool(re.search(
            r"\b(?:all|every|total|new|recent|latest)\b|அனைத்து|மொத்த", (_RAW_MESSAGE.get() or "").lower()))
    if named is None:
        if re.search(r"\bhistory\b", ml):
            status = ["approved", "rejected"]
        elif has_range or year or month or channels or service_code or excluded_service_code or wants_everything or (
                (ward_num or block_num) and re.search(r"\bhow\s+many\b|\bcount\b|\bnumber\s+of\b|எத்தனை", ml)):
            status = None   # a COUNT in a ward / block is over the register, not narrowed to the open queue
            # "Show pending applications for 0158." then "Exclude rejected
            # applications." -- the SECOND turn named no status of its own;
            # `wants_everything` is True only because `neg_scope.rewrite()`
            # injected the literal word "all" to keep this a register-wide
            # query rather than the narrower ISD/NISD queue (see the comment
            # on `wants_rejected_too` below). Taken at face value, that
            # widened "pending" (10 applications) into "every status except
            # rejected" (20) for a turn that only asked to drop rejected
            # files FROM THE LIST ALREADY ON SCREEN -- "exclude rejected"
            # after a pending-only chain should be a no-op (rejected was
            # never in it), not a broadening. Carrying the chain's own
            # starting status forward (same rule ward/block/service_code
            # already follow above) fixes it; a message that names its own
            # period/channel/code alongside the negation still correctly
            # reaches the register unrestricted, since those conditions on
            # their own already imply `wants_everything` with no rewrite
            # involved.
            if (_neg_scope.EXCLUDE.get() and not has_range and not year and not month
                    and not channels and not service_code and not excluded_service_code
                    and followup_ctx is not None and followup_ctx.intent == "all_service_code_applications"):
                _carried_status = (followup_ctx.filters or {}).get("base_query_filters", {}).get("status")
                if _carried_status:
                    status = _carried_status
        else:
            status = "pending"

    sort = _SORT_OVERRIDE.get() or extract_sort_order(message) or (None, None)
    sd = await get_all_service_code_applications(
        db, officer, service_code=service_code, exclude_service_code=excluded_service_code,
        submission_channel=channels,
        status=status, start_date=start_d, end_date=end_d,
        date_ranges=month_scopes or None, exclude_date_range=negated,
        submission_year=year, submission_month=month,
        ward_number=ward_num, block_number=block_num,
        sort_by=sort[0], sort_dir=sort[1],
        # The standing rule -- a rejected file is not in an operational list
        # unless asked for -- holds here too; "all"/"every" asks for it.
        hide_rejected=not wants_rejected_too)

    # Recorded so a follow-up ("only CSC", "which is oldest?") stays inside
    # this list's scope, the way the ISD/NISD listing records it.
    if channels:
        sd["submission_channels"] = list(channels)
        if len(channels) == 1:
            sd["submission_channel"] = channels[0]
    if ward_num:
        sd["ward_number"] = ward_num
    if block_num:
        sd["block_number"] = block_num
    if service_code:
        sd["service_code"] = service_code
    if month:
        sd["submission_month"] = month
    if year:
        sd["submission_year"] = year
    if has_range:
        if month_scopes:
            sd["period_label"] = format_month_scopes(month_scopes)
        elif start_d and end_d and _whole_month_label(start_d, end_d):
            sd["period_label"] = _whole_month_label(start_d, end_d)
        else:
            sd["period_label"] = f"{start_d or '...'} to {end_d or '...'}"
    elif month:
        sd["period_label"] = f"{_MONTH_NAMES[month]} {year}" if year else _MONTH_NAMES[month]
    elif year:
        sd["period_label"] = str(year)
    if negated and has_range:
        sd["exclude_date_range"] = True
    # "Restore the original filter." needs something stable to restore TO --
    # the scope this listing started from, before any negation/narrowing
    # follow-up changed it. Carried forward unchanged once set (the FIRST
    # register-wide listing in the chain records it; every later re-listing
    # in the SAME chain -- "not rejected", "include rejected too", a column
    # projection -- just repeats the one already carried), so "restore"
    # always means "back to the very first scope", not "back to one step
    # ago". See `_restore_original_filter_answer`.
    # Not gated on `followup_ctx.intent` -- a row-slicing / exclusion turn in
    # between ("show only the first five rows", "exclude the second row")
    # answers through `followup_list`, not `all_service_code_applications`,
    # but `_followup_out_context` already carries `base_query_filters`
    # through THOSE turns untouched (it is in the whitelist `build_context`
    # copies forward and is never one of the keys a scoped re-query sets).
    # Checking the intent here anyway (as an earlier version of this did)
    # meant any such turn landing between the original listing and the next
    # full re-listing silently reset the snapshot to whatever happened to be
    # active right then -- "include rejected too" after an "exclude the
    # second row" turn took its OWN unfiltered, ward-less scope as if it
    # were the original.
    _carried_base = (followup_ctx.filters or {}).get("base_query_filters") if followup_ctx is not None else None
    if not _carried_base:
        # The immediately-prior turn may itself have lost it -- a bare "show
        # row 3" with no field attached resolves to a single full-details
        # card (`application_status`), whose own saved context carries no
        # memory of the register listing it was picked from at all. Scan
        # back through the conversation the same way `_restore_original_
        # filter_answer` does, so a one-turn detour like that does not reset
        # the whole chain's snapshot to whatever this turn's own filters are.
        _carried_base = await _find_base_query_filters(db, session_id, officer.officer_id if officer else None)
    # A chain only continues when THIS query's own resolved scope agrees
    # with the carried snapshot -- "show rejected applications in ward 005"
    # after a ward-001 chain names a genuinely different ward, so it starts
    # a NEW chain instead of inheriting the old one. Without this, "restore
    # the original filter" several turns later would reach past the ward-005
    # listing the officer is actually working from and hand back the
    # obsolete ward-001 scope purely because it happens to sit earlier in
    # the conversation. Only compared when BOTH sides actually name a value
    # (an unscoped dimension on either side is not a conflict) -- an
    # inherited, unnamed ward_num that happens to already equal the
    # carried one is correctly not a boundary.
    if _carried_base and (
            (ward_num and _carried_base.get("ward_number") and ward_num != _carried_base["ward_number"])
            or (block_num and _carried_base.get("block_number") and block_num != _carried_base["block_number"])
            or (service_code and _carried_base.get("service_code") and service_code != _carried_base["service_code"])):
        _carried_base = None
    if _carried_base:
        sd["base_query_filters"] = _carried_base
    else:
        sd["base_query_filters"] = {
            "service_code": service_code,
            "submission_channel": list(channels) if channels else None,
            "status": status, "start_date": start_d, "end_date": end_d,
            "date_ranges": month_scopes or None, "exclude_date_range": negated,
            "submission_year": year, "submission_month": month,
            "ward_number": ward_num, "block_number": block_num,
            "hide_rejected": not wants_rejected_too,
        }
    return sd


# "Restore the original filter." / "restore the initial filter." -- back to
# the scope the register-listing CHAIN started from (`base_query_filters`,
# snapshotted once per chain just above and carried forward unchanged by
# every later re-listing in it). Deliberately narrow: only the explicit
# "restore ... filter" phrasing claims the turn, and only when a snapshot
# is actually on record -- there is no sense in which guessing a filter is
# better than asking, and before this fix the message named no field/row/
# column word at all, so it fell through every other follow-up layer to the
# LLM, which (per CLAUDE.md's own documented pattern for this) improvised an
# unrelated jurisdiction refusal from stale ward numbers in chat_history.
_RESTORE_FILTER_RE = re.compile(
    r"\brestore\b.{0,25}\b(?:original|initial|previous|earlier|first|default)\b.{0,15}\bfilter",
    re.IGNORECASE)


async def _find_base_query_filters(db, session_id, officer_id):
    """Scan recent turns of this session for the last `base_query_filters`
    snapshot on record, newest first.

    The immediate carried context is tried first by the caller; this is the
    fallback for a chain where the MOST RECENT turn does not carry it even
    though an earlier one in the same conversation does -- a bare "show row
    3" / "show the third row now" with no field attached resolves to a
    single full application card (`application_status`), whose own saved
    context is built from that answer alone and never mentions the register
    listing it was picked from at all. Reuses the exact windowed query
    `_load_followup_context` already runs, so this never reaches further
    back than an ordinary follow-up would.
    """
    if db is None or not session_id:
        return None
    try:
        _cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.FOLLOWUP_CONTEXT_TTL_MINUTES)
        rows = (await db.execute(
            select(ChatMessage.structured_data)
            .where(and_(ChatMessage.session_id == session_id,
                        ChatMessage.role == "assistant",
                        ChatMessage.created_at >= _cutoff,
                        messages_since_session_start(session_id)))
            .order_by(ChatMessage.created_at.desc())
            .limit(_FOLLOWUP_CONTEXT_WINDOW)
        )).scalars().all()
    except Exception:  # pragma: no cover - a convenience, never fatal
        return None
    for raw in rows:
        if isinstance(raw, dict) and (raw.get("filters") or {}).get("base_query_filters"):
            return raw["filters"]["base_query_filters"]
    return None


async def _restore_original_filter_answer(db, officer, message: str, ctx, language: str,
                                          session_id=None, officer_id=None):
    if not (ctx and _RESTORE_FILTER_RE.search(message or "")):
        return None
    is_tamil = language in ("ta", "tanglish")
    base = (ctx.filters or {}).get("base_query_filters") \
        or await _find_base_query_filters(db, session_id, officer_id)
    if not base:
        # Nothing stable on record for THIS conversation to go back to --
        # asking is the honest answer, not inventing one of the filters the
        # officer happened to type along the way. `sd=None` tells the caller
        # to keep the existing carried context exactly as it was.
        return (
            "இந்த உரையாடலில் மீட்டெடுக்க ஒரு பதிவு செய்யப்பட்ட அசல் வடிகட்டி இல்லை. "
            "எந்த வடிகட்டியை (நிலை, வார்டு, வழி, காலம்) மீட்டெடுக்க வேண்டும் என்று கூறவும்."
            if is_tamil else
            "There is no original filter on record for this conversation to restore -- "
            "say which filter (status, ward, channel, period) you want back, or ask "
            "for the listing again from scratch."
        ), None
    sd = await get_all_service_code_applications(db, officer, **base)
    sd["base_query_filters"] = base
    if base.get("ward_number"):
        sd["ward_number"] = base["ward_number"]
    if base.get("block_number"):
        sd["block_number"] = base["block_number"]
    body = _render_all_service_code_answer(sd, is_tamil=is_tamil)
    lead = ("அசல் வடிகட்டி மீட்டெடுக்கப்பட்டது -- வரிசை மற்றும் நெடுவரிசைகள் மாறவில்லை." if is_tamil
            else "Original filter restored -- sort order and column selection are unchanged.")
    return f"<div class='table-intro'>{lead}</div>{body}", sd


_DISTINCT_SERVICE_COUNT_RE = re.compile(
    r"\bhow\s+many\s+(?:service\s*codes?|services)\b|\bnumber\s+of\s+(?:service\s*codes?|services)\b|"
    r"எத்தனை\s*சேவை(?:\s*குறியீடு)?(?:கள்)?",
    re.IGNORECASE)


def _asks_distinct_service_count(message: str) -> bool:
    """"How many services currently have pending applications?" / "how many
    service codes have pending files?" counts SERVICES, not applications -- a
    different question from "how many applications are pending", which
    `_is_count_only_query` already answers correctly with the application
    total. The counted noun right after "how many" is "service(s)" / "service
    code(s)", never "application(s)", so this never claims a genuine
    application-count question such as "how many applications does 0158
    have"."""
    return bool(_DISTINCT_SERVICE_COUNT_RE.search(message or ""))


# How far back a follow-up may reach for its referent. This was 3, which is
# two exchanges: an officer who asked a listing, drilled into one row, checked
# a document and then said "show me the second one" had already lost the list.
# Conversations at a survey desk run longer than that -- the officer works a
# queue, dipping in and out of the same table for several minutes.
#
# Reaching further is safe because reaching is not the same as CLAIMING: the
# newest record still wins for an ordinary follow-up, an empty listing record
# still acts as a barrier (`resolve` stands aside on it rather than reading
# past it to a set the officer has moved on from), and every carried number is
# re-checked against the officer's jurisdiction before a field of it is shown.
# Only a positional or per-column follow-up ("the 2nd one", "their wards")
# searches the window for a list, which is exactly the case that needs it.
_FOLLOWUP_CONTEXT_WINDOW = 12
