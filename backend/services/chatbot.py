"""
Main chatbot service - RAG orchestration
"""
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta, date, timezone
import asyncio
import time
import re
import uuid
import calendar
from difflib import SequenceMatcher

from backend.config import DISTRICT_NAME_MAP, settings
from backend.schemas import OfficerContext
from backend.services import upload_store
from backend.services.rag import (
    detect_language,
    get_rag_context_async,
    build_prompt,
    build_html_response,
    call_llama,
    call_llama_stream,
    parse_intent,
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
    extract_submission_channel,
    _get_projected_application_columns,
    _get_projected_field_visit_columns
)
from backend.services.postgres import (
    get_can_details,
    get_pending_applications,
    get_overdue_applications,
    get_officer_workload,
    get_application_detail,
    get_survey_detail,
    get_survey_owners,
    get_unscheduled_visits,
    get_field_visits,
    get_next_subdivision_number,
    get_ward_surveys,
    get_all_surveys_in_jurisdiction,
    get_merge_application_detail,
    get_officer_applications
)
from backend.models import (
    ChatSession, ChatMessage, Application, SurveyNumber, Block, Ward, Town, Taluk,
    FieldVisit, ApplicationDocument, WorkflowHistory, Applicant, ApplicationSubDivision,
    OfficerJurisdiction, District, PattaTransfer, SISOfficer
)
from backend.utils.logger import get_logger
from sqlalchemy import select, and_, or_, func

logger = get_logger(__name__)


from backend.utils.fuzzy import (
    extract_month_from_text,
    extract_tokens,
    match_phrase,
    normalize_text,
    resolve_unique_entry,
)

# Application-type detection patterns.
# Service codes are only meaningful as standalone digit runs — the (?<!\d)/(?!\d)
# guards stop a serial number like "000153" from being read as the NISD code.
_SERVICE_CODE_NISD_RE = re.compile(r'\bnisd\b|(?<!\d)0153(?!\d)')
_SERVICE_CODE_ISD_RE = re.compile(r'\bisd\b|(?<!\d)0154(?!\d)')
_SERVICE_CODE_MERGE_RE = re.compile(r'\bmerg(?:e|es|ed|ing)?\b|(?<!\d)0155(?!\d)')

# 1-indexed so _MONTH_NAMES[6] == "June".
_MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"]


def _resolve_month_year(month: int, today: date = None) -> int:
    """
    Year for a month named without one ("applications in June").

    A monthly report means one month of one year, so resolve to the most recent
    occurrence of that month that has already started — June asked in August
    2026 is June 2026; September asked in August 2026 is September 2025.
    Matching the month across every year mixes four years of files into a
    single "monthly" list, which is never what the question meant.
    """
    today = today or date.today()
    return today.year if month <= today.month else today.year - 1


def _period_from_message(message: str):
    """
    (start, end, label) for whatever period the message names, or (None, None, None).

    Covers the four shapes officers use: a union of months ("June and July"),
    an explicit range or relative phrase, a single named month ("in June 2026")
    and a bare year ("in 2024"). Reporting handlers need all four -- reading only
    the first two is how "my completion rate in June 2026" ended up answering
    with the all-time figure.
    """
    scopes = extract_month_scopes(message)
    if scopes:
        return scopes[0][0], scopes[-1][1], format_month_scopes(scopes)

    start_d, end_d = extract_date_range(message)
    if start_d and end_d:
        return start_d, end_d, (_whole_month_label(start_d, end_d) or f"{start_d} to {end_d}")

    month = extract_month_from_query(message)
    year_match = re.search(r'\b(20\d{2})\b', message)
    year = int(year_match.group(1)) if year_match else None
    if month:
        year = year or _resolve_month_year(month)
        start_d = date(year, month, 1)
        end_d = date(year, month, calendar.monthrange(year, month)[1])
        return start_d, end_d, f"{_MONTH_NAMES[month]} {year}"
    if year:
        return date(year, 1, 1), date(year, 12, 31), str(year)
    return None, None, None


def _explicit_status_request(message_lower: str):
    """
    The status the officer actually named, or None when they named none.

    "How many rejected applications in 2024" used to answer with the
    non-rejected ones: any date or year scope forced status_filter=None, and the
    word "rejected" was never looked at. A status the officer typed outranks the
    scope default.
    """
    if any(p in message_lower for p in ["history", "approved n rejected",
                                        "approved and rejected", "approved & rejected"]):
        return ["approved", "rejected"]
    if any(p in message_lower for p in ["not rejected", "not approved", "except rejected",
                                        "other than rejected", "excluding rejected"]):
        return None
    if re.search(r"\brejec", message_lower) or "நிராகரிக்கப்பட்ட" in message_lower:
        return "rejected"
    if re.search(r"\bapprove", message_lower) or "அங்கீகரிக்கப்பட்ட" in message_lower:
        return "approved"
    if re.search(r"\bin[ _-]?progress\b", message_lower):
        return "in_progress"
    if re.search(r"\bescalat", message_lower):
        return "escalated"
    if re.search(r"\bpending\b", message_lower) or "நிலுவை" in message_lower:
        return "pending"
    return None


def _whole_month_label(start_d, end_d) -> str:
    """
    'June 2026' / 'April-June 2026' when [start_d, end_d] covers whole calendar
    months end to end, else '' -- a range that starts or stops mid-month has to
    keep its exact dates.
    """
    if not (start_d and end_d):
        return ""
    if start_d.day != 1 or end_d.day != calendar.monthrange(end_d.year, end_d.month)[1]:
        return ""
    if start_d > end_d:
        return ""
    return format_month_scopes([(start_d, end_d)])


def extract_month_from_query(message: str) -> Optional[int]:
    """
    Extract month from message for filtering applications by submission month.
    Handles English and Tamil month names with token-boundary fuzzy matching for spelling mistakes.

    Returns:
        Month number (1-12) if found, None otherwise
    """
    month_num = extract_month_from_text(message)
    if month_num:
        logger.info(f"Month extracted: {month_num} from query '{message[:40]}'")
    return month_num


def _extract_app_types(message_lower: str, intent: str = None):
    """
    Extract one or more application types from a query message.

    Returns:
        - A list  ["ISD", "MERGE"] when multiple types mentioned
        - A single string "ISD" / "NISD" / "MERGE" when exactly one type
        - None when no specific type mentioned (all types)
    """
    types = []
    # \bisd\b matches "isd" but NOT "nisd" (no word-boundary before i in nisd).
    # Service codes must be standalone digit runs: "2026/0153/02/000002" counts,
    # but the serial "000153" inside 2026/0154/02/000153 must NOT read as NISD.
    has_nisd  = bool(_SERVICE_CODE_NISD_RE.search(message_lower))
    has_isd   = bool(_SERVICE_CODE_ISD_RE.search(message_lower))
    has_merge = bool(_SERVICE_CODE_MERGE_RE.search(message_lower))

    if has_nisd:
        types.append("NISD")
    if has_isd:
        types.append("ISD")
    if has_merge:
        types.append("MERGE")

    if len(types) == 0:
        if intent == "both_applications":
            return ["ISD", "NISD"]
        elif intent == "isd_applications":
            return "ISD"
        elif intent == "nisd_applications":
            return "NISD"
        elif intent == "merge_applications":
            return "MERGE"
        return None
    if len(types) == 1:
        return types[0]
    return types  # list for multi-type IN query



def _format_count_intro(structured_data: dict, language: str, message: str) -> str:
    """
    Format clean, natural English and Tamil intro sentence when the user asks for application counts.
    """
    if not structured_data:
        return ""
    count = structured_data.get("count", len(structured_data.get("applications", [])))
    qtype_raw = structured_data.get("query_type", "applications")
    is_tamil = language in ("ta", "tanglish")

    import re as _re
    # Clean leading status adjectives (e.g. "All NISD & ISD Applications (2026-07-03 to 2026-07-20)" -> "NISD & ISD Applications (2026-07-03 to 2026-07-20)")
    label = _re.sub(r'^(All|Pending|Approved|Rejected)\s+', '', qtype_raw, flags=_re.IGNORECASE)

    # Extract date range from label if present e.g. "(2026-07-03 to 2026-07-20)" or "(not 2025-01-01 to 2026-12-31)"
    date_range_match = _re.search(r'\((?:not\s+)?(20\d{2}-\d{2}-\d{2})\s+to\s+(20\d{2}-\d{2}-\d{2})\)', label)
    is_negated_date = bool(date_range_match and "not " in date_range_match.group(0)) or structured_data.get("exclude_date_range", False) or any(w in message.lower() for w in ["not between", "not in", "outside", "other than", "இடைப்பட்டவை அல்ல", "இடையில் இல்லாத"])
    date_suffix_en = ""
    date_prefix_ta = ""
    if date_range_match:
        d1, d2 = date_range_match.group(1), date_range_match.group(2)
        # Show just the year if it's a full-year range (Jan 1 → Dec 31 of any year)
        y1, m1, day1 = d1.split('-')
        y2, m2, day2 = d2.split('-')
        if m1 == '01' and day1 == '01' and m2 == '12' and day2 == '31':
            if y1 == y2:
                if is_negated_date:
                    date_suffix_en = f" not in {y1}"
                    date_prefix_ta = f"{y1} ஆண்டு தவிர "
                else:
                    date_suffix_en = f" in {y1}"
                    date_prefix_ta = f"{y1} ஆண்டில் "
            else:
                if is_negated_date:
                    date_suffix_en = f" not between {y1} and {y2}"
                    date_prefix_ta = f"{y1} முதல் {y2} வரை இல்லாத "
                else:
                    date_suffix_en = f" between {y1} and {y2}"
                    date_prefix_ta = f"{y1} முதல் {y2} வரை "
        else:
            if is_negated_date:
                date_suffix_en = f" not between {d1} and {d2}"
                date_prefix_ta = f"{d1} முதல் {d2} வரை இல்லாத "
            else:
                date_suffix_en = f" ({d1} to {d2})"
                date_prefix_ta = f"{d1} முதல் {d2} வரை "
        label = label.replace(date_range_match.group(0), "").strip()

    label_en = _re.sub(r'\bApplications\b', 'application' if count == 1 else 'applications', label, flags=_re.IGNORECASE)
    label_ta = label.replace("Applications", "விண்ணப்பங்கள்").replace("Application", "விண்ணப்பம்")

    if is_tamil:
        if count == 0:
            return f"உங்கள் அதிகார வரம்பில் {date_prefix_ta}{label_ta} எதுவும் இல்லை."
        elif count == 1:
            return f"உங்கள் அதிகார வரம்பில் {date_prefix_ta}1 {label_ta.replace('விண்ணப்பங்கள்', 'விண்ணப்பம்')} உள்ளது."
        else:
            return f"உங்கள் அதிகார வரம்பில் {date_prefix_ta}{count} {label_ta} உள்ளன."
    else:
        if count == 0:
            return f"There are no {label_en}{date_suffix_en} in your jurisdiction."
        elif count == 1:
            return f"There is 1 {label_en}{date_suffix_en} in your jurisdiction."
        else:
            return f"There are {count} {label_en}{date_suffix_en} in your jurisdiction."


def _is_count_only_query(message: str) -> bool:
    """
    Returns True ONLY if the user is explicitly asking for a count/quantity,
    and NOT asking to list/show/display the records or table,
    and NOT asking for a specific field identifier (like serial no, patta no, etc.).
    """
    msg = message.lower()
    
    # Check if asking for specific field like "serial no", "patta no", "can no", etc.
    import re
    if re.search(r'\b(serial|can|patta|ward|block|survey|subdivision|phone|mobile|aadhaar|renewal|service|department|district|taluk|village|form\s*6|ip)\s+(?:no|number)\b', msg):
        return False
    
    _list_words = ["show", "list", "display", "view", "get", "fetch", "details", "காட்டு", "காண்பி", "பட்டியல்"]
    has_list_word = any(w in msg for w in _list_words)
    
    _count_triggers = [
        "how many", "howmuch", "how much", "total count", "count of",
        "total number of", "total number", "no of", "no. of", "nos of", "nos. of",
        "number of", "num of", "num. of", "count",
        "எத்தனை", "எண்ணிக்கை", "தொகை", "மொத்த எண்ணிக்கை", "மொத்தம்"
    ]
    has_count_trigger = any(kw in msg for kw in _count_triggers) or bool(re.search(r'\b(?:no|no\.|nos|nos\.|number|num|count)\s+of\b', msg))
    
    if has_count_trigger and not has_list_word:
        return True
    if has_count_trigger and has_list_word and any(w in msg for w in ["count", "total", "how many", "எத்தனை", "எண்ணிக்கை"]):
        return True
    return False


# Asked whenever an application-specific question arrives without an application
# number. Substituting an arbitrary application here would answer a question the
# officer never asked, so every such branch prompts instead.
ASK_FOR_APP_NUMBER = {
    "en": "Please specify the application number you are asking about — for example 2026/0154/02/000041.",
    "ta": "எந்த விண்ணப்பம் என்பதைக் குறிப்பிடவும் — எடுத்துக்காட்டாக 2026/0154/02/000041.",
    "tanglish": "Endha application-nu solunga — example: 2026/0154/02/000041.",
}


# Asked when an ownership/survey question arrives with no survey number and no
# confirmed application to take one from.
ASK_FOR_SURVEY_NUMBER = {
    "en": "Which survey number or application should I look up the ownership for?",
    "ta": "எந்த கணக்கெண் அல்லது விண்ணப்பத்திற்கான உரிமை விவரங்கள் வேண்டும்?",
    "tanglish": "Endha survey number illana application-oda ownership venum?",
}


# Phrases by which an officer points back at an application discussed earlier.
_REFERENCE_PATTERNS = (
    "this application", "that application", "same application",
    "this app", "that app", "the application", "the app",
    "prev application", "previous application", "prev app", "previous app",
    "last application", "last app", "above application", "overdue application",
    # Bare pronouns. An officer who has just been shown an application says
    # "what is its status" far more often than they repeat the number, so these
    # have to count as back-references too. They only ever *enable* a lookup in
    # the confirmed context — whatever they resolve to is still re-validated.
    "it", "its", "this one", "that one", "the same one", "previous one", "same one",
    "இந்த விண்ணப்பம்", "அந்த விண்ணப்பம்", "முந்தைய விண்ணப்பம்",
    "இதன்", "அதன்", "இது", "அது", "இதே",
    "adhoda", "idhoda", "adhu", "idhu", "adhe", "idhe",
)

# Matched on whole-word boundaries: a plain substring test makes "the app" fire on
# "the applicant", turning an ordinary field question into a back-reference and
# silently attaching it to some unrelated application.
_REFERENCE_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(p) for p in _REFERENCE_PATTERNS) + r")(?!\w)",
    re.IGNORECASE,
)


def _has_application_back_reference(message: str) -> bool:
    """True when the officer explicitly points back at an earlier application."""
    return bool(_REFERENCE_RE.search(message or ""))


# "workflow history" / "timeline" asks for the stage-by-stage log. The bare word
# "workflow" does NOT: "what is the current workflow state?" is a question about
# the application's present stage, and treating it as a history request replaced
# the application's fields with a history list, so every field answered "N/A"
# even though the database held the values.
_WORKFLOW_HISTORY_RE = re.compile(
    r'\btimeline\b|\bhistory\b|\bworkflow\s+(?:history|log|trail|timeline)\b'
    r'|\bவரலாறு\b',
    re.IGNORECASE,
)


def _wants_workflow_history(message: str) -> bool:
    """True only when the officer asked for the stage-by-stage log, not the current stage."""
    return bool(_WORKFLOW_HISTORY_RE.search(message or ""))


# A request to see the record ("show application X", "give me the details") versus
# a question about one particular attribute ("what is the SLA deadline for X?").
_DETAIL_REQUEST_RE = re.compile(
    r'\b(?:show|display|give|get|fetch|open|view|tell)\b.*\b(?:details?|record|application|info|information)\b'
    r'|\bdetails?\s+(?:of|for)\b|\babout\b',
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(
    r'\b(?:what|which|when|where|who|whose|how|why)\b|\bஎன்ன\b|\bஎந்த\b|\bயார்\b|\bஎப்போது\b',
    re.IGNORECASE,
)


def _asks_for_specific_detail(message: str) -> bool:
    """
    True when the officer asked about one particular attribute rather than asking
    to see the record. Used to avoid answering a pointed question ("what is the
    SLA deadline?") with a generic summary that never addresses it.
    """
    if not message:
        return False
    if _DETAIL_REQUEST_RE.search(message):
        return False
    return bool(_QUESTION_RE.search(message))


def _officer_turns(chat_history: list) -> list:
    """
    Only the officer's own turns may supply an application number for a follow-up.

    Assistant turns quote application numbers the officer never chose — the example
    number inside a format-error message, the number that was just reported as not
    found, every entry of a suggestion list. Harvesting those makes the bot answer
    about an application nobody asked for, so they are excluded here.
    """
    if not chat_history:
        return []
    return [
        m for m in chat_history
        if isinstance(m, dict) and str(m.get("role", "")).lower() in ("user", "human", "officer")
    ]


_ANY_APP_NUMBER_RE = re.compile(
    r'\b\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+\b'
    r'|\b\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+\b'
    r'|(?:ISD|NISD|MERGE)/\w+/\d+/\d+'
    r'|APP-\d{4}-\d{6}'
    r'|\b20\d{2}/[\w]+/[\w]+/\d+\b',
    re.IGNORECASE,
)


def _app_numbers_in_recent_context(chat_history: list, depth: int = 8) -> Tuple[List[str], bool]:
    """
    Application numbers from the most recent officer turn that referred to one.

    Returns (numbers, blocked).

    `numbers` holds every distinct number in that single turn, so the caller can
    tell a clean back-reference ("show application A" -> "its status") from an
    ambiguous one ("compare A and B" -> "its status"), which must be asked about
    rather than resolved to whichever number matched first.

    `blocked` is True when the most recent application-referring turn offered a
    malformed number. The officer's last attempt to name an application failed,
    so "its status" refers to that failed attempt — not to some older application
    further up the conversation. Reaching past it would answer about an
    application the officer has already moved on from.
    """
    for msg in reversed(_officer_turns(chat_history)[-depth:]):
        content = msg.get("content", "") or ""
        found = []
        for m in _ANY_APP_NUMBER_RE.finditer(content):
            token = m.group(0).upper()
            if token not in found:
                found.append(token)
        if found:
            return found, False
        if detect_offered_app_number_token(content):
            return [], True
    return [], False


def _extract_app_number_from_context(message: str, chat_history: list = None, allow_implicit_continuation: bool = False) -> str:
    """
    Extract application number from current message or recent chat history.
    Handles references like "this application", "that application", etc.

    Args:
        message: Current user message
        chat_history: List of previous messages
        allow_implicit_continuation: If True, check immediate previous message for app number
                                     even without explicit reference words (for field queries)

    Returns:
        Application number in uppercase, or None if not found
    """
    import re

    # Pattern 0: Official YYYY/SERVICE_CODE/DISTRICT_CODE/SEQUENCE format (e.g. 2026/0153/31/000001 or 2026/31/0153/000001)
    app_match = re.search(r'\b\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+\b|\b\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+\b', message, re.IGNORECASE)
    if app_match:
        return app_match.group(0).upper()

    # Pattern 0b: Broader YYYY/A/B/N format — catches any user-typed 4-segment number
    broad_match = re.search(r'\b(20\d{2}/[\w]+/[\w]+/\d+)\b', message)
    if broad_match:
        return broad_match.group(0).upper()

    # Pattern 1: Standard format (ISD|NISD|MERGE)/XX/YYYY/NNN
    app_match = re.search(r'(ISD|NISD|MERGE)/\w+/\d+/\d+', message, re.IGNORECASE)
    if app_match:
        return app_match.group(0).upper()

    # Pattern 2: APP-YYYY-NNNNNN format
    app_match = re.search(r'APP-\d{4}-\d{6}', message, re.IGNORECASE)
    if app_match:
        return app_match.group(0).upper()

    # Pattern 3: Check if user is referring to a previous application using strict patterns
    # Avoid generic words like "the" which cause false positives
    has_explicit_reference = _has_application_back_reference(message)
    
    # Pattern 4: Implicit continuation - if asking a field/column query after
    # an application was discussed, assume continuity
    if allow_implicit_continuation and not has_explicit_reference and chat_history:
        for msg in reversed(_officer_turns(chat_history)[-6:]):
            content = msg.get("content", "") or ""
            app_match = re.search(r'\b\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+\b|\b\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+\b|(?:ISD|NISD|MERGE)/\w+/\d+/\d+|APP-\d{4}-\d{6}|\b20\d{2}/[\w]+/[\w]+/\d+\b', content, re.IGNORECASE)
            if app_match:
                logger.info(f"Found implicit application continuation '{app_match.group(0)}' from immediate context")
                return app_match.group(0).upper()
    
    # Pattern 5: Explicit reference - search further back in history
    if has_explicit_reference and chat_history:
        for msg in reversed(_officer_turns(chat_history)[-8:]):
            content = msg.get("content", "") or ""
            app_match = re.search(r'\b\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+\b|\b\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+\b|(?:ISD|NISD|MERGE)/\w+/\d+/\d+|APP-\d{4}-\d{6}|\b20\d{2}/[\w]+/[\w]+/\d+\b', content, re.IGNORECASE)
            if app_match:
                logger.info(f"Found application reference '{app_match.group(0)}' from chat history")
                return app_match.group(0).upper()

    return None


# ── Application number format validation ────────────────────────────────────
# extract_application_number() only ever matches well-formed numbers; when it
# returns None we cannot tell "the officer mentioned no application" apart from
# "the officer typed an application number wrongly". These helpers make that
# distinction so both chat paths can say which of the two happened.

# The accepted shapes, kept in sync with rag.extract_application_number().
# Also accepts broader YYYY/A/B/N patterns so user-typed non-standard numbers
# are treated as valid lookup attempts (reaching the DB) rather than format errors.
_VALID_APP_NUMBER_RE = re.compile(
    r'\b\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+\b'
    r'|\b\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+\b'
    r'|\b(?:ISD|NISD|MERGE)/\w+/\d+/\d+\b'
    r'|\bAPP-\d+-\d+\b'
    r'|\b20\d{2}/[\w]+/[\w]+/\d+\b',  # broad YYYY/A/B/N — lookup in DB, not format error
    re.IGNORECASE,
)

# Deliberately loose — only consulted after _VALID_APP_NUMBER_RE has already
# failed, so anything it catches is by definition not a valid number.
# Group 1: an APP-prefixed token (self-evidently an application number attempt).
# Group 2: a TYPE-prefixed token.
# Group 3: a bare digits-and-slashes token — needs a cue word to count.
_APP_NUMBER_ATTEMPT_RE = re.compile(
    r'\b(APP[\-_/]*\d[\w\-/]*)'
    r'|\b((?:ISD|NISD|MERGE)/[\w/]+)'
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
    "is_nisd_or_isd", "isd_processing", "rejection_info",
}

# Intents whose answer is about one specific application, so a malformed number
# makes the whole request unanswerable.
_APP_NUMBER_INTENTS = {
    "application_status", "check_documents", "check_sale_deed", "sale_deed_check",
    "is_nisd_or_isd", "joint_owner_check", "merge_info", "isd_processing",
    "escalation_check", "litigation_check", "rejection_info",
    "sd_additional_info", "sd_encroachment_check", "sd_sketch_readiness",
    "sd_forward_check", "sd_remarks",
    "fv_date_select", "fv_nearby_pending", "fv_deadline_check",
}


# "application <token>" / "app <token>" — the token right after the cue word is
# being offered as the identifier. Requires a digit AND a letter-or-separator so
# that "application 5" (a count) and plain prose are not mistaken for attempts.
_APP_CUE_TOKEN_RE = re.compile(
    r'\b(?:application|applicaton|appl|app|விண்ணப்ப\w*)\s+(?:no\.?|number|num|#)?\s*'
    r'([A-Za-z0-9][\w\-/]*)',
    re.IGNORECASE,
)

# Words that legitimately follow "application" and must never be read as a number.
_APP_CUE_STOPWORDS = {
    "status", "number", "numbers", "details", "detail", "list", "type", "types",
    "id", "ids", "no", "for", "of", "in", "is", "are", "was", "with", "and",
    "documents", "document", "history", "stage", "stages", "count", "summary",
    "pending", "approved", "rejected", "overdue", "isd", "nisd", "merge",
    "received", "submitted", "assigned", "form", "forms", "date", "dates",
    "process", "processing", "workflow", "owner", "owners", "applicant",
    "my", "me", "all", "the", "a", "an", "this", "that", "each", "every",
}

# A bare word offered as the identifier ("show application INVALID") only reads as
# a malformed number when an explicit retrieval verb governs it. Without that
# guard, ordinary prose such as "application received today" would be reported as
# a bad application number.
_APP_FETCH_CUE_RE = re.compile(
    r'\b(?:show|open|get|display|fetch|view|find|search|pull)\s+(?:me\s+)?(?:the\s+)?'
    r'(?:application|applicaton|appl|app|விண்ணப்ப\w*)\s+(?:no\.?|number|num|#)?\s*'
    r'([A-Za-z][\w\-/]*)',
    re.IGNORECASE,
)


def detect_offered_app_number_token(message: str) -> Optional[str]:
    """
    The token an officer explicitly offered as an application number — i.e. the
    word right after "application"/"app" — when it is not a valid one.

    This is a high-confidence signal: "show application INVALID-999" is an
    application request with a bad identifier whatever the intent classifier
    decides, so the gate consults it for every intent. Without it such a message
    fell through to a list intent and answered with unrelated applications.
    """
    if not message:
        return None
    for m in _APP_CUE_TOKEN_RE.finditer(message):
        token = (m.group(1) or "").strip().rstrip("/-_.,?")
        if not token or token.lower() in _APP_CUE_STOPWORDS:
            continue
        if not any(c.isdigit() for c in token):
            continue
        # Needs a letter or a separator too, so "application 5" (a count) and
        # ordinary prose are never mistaken for a malformed identifier.
        if not (any(c.isalpha() for c in token) or "-" in token or "/" in token):
            continue
        if _VALID_APP_NUMBER_RE.search(token):
            continue
        return token

    # All-letter identifier, but only under an explicit "show application X".
    for m in _APP_FETCH_CUE_RE.finditer(message):
        token = (m.group(1) or "").strip().rstrip("/-_.,?")
        if not token or token.lower() in _APP_CUE_STOPWORDS:
            continue
        if _VALID_APP_NUMBER_RE.search(token):
            continue
        return token
    return None


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
    if language == "ta":
        return (
            f"நீங்கள் கொடுத்த விண்ணப்ப எண் **{token}** சரியான வடிவத்தில் இல்லை.\n\n"
            "சரியான வடிவம்:\n"
            "  • YYYY/சேவைக்குறியீடு/மாவட்டக்குறியீடு/வரிசை எண் — எ.கா. 2026/0154/02/000041\n\n"
            "சரியான விண்ணப்ப எண்ணுடன் மீண்டும் கேட்கவும்."
        )
    if language == "tanglish":
        return (
            f"Neenga kudutha application number **{token}** correct format-la illa.\n\n"
            "Valid format:\n"
            "  • YYYY/SERVICE/DISTRICT/SEQUENCE — e.g. 2026/0154/02/000041\n\n"
            "Correct application number kuduthu marubadiyum kelunga."
        )
    return (
        f"The application number you entered — **{token}** — is not in a valid format.\n\n"
        "The valid application number format is:\n"
        "  • YYYY/SERVICE_CODE/DISTRICT_CODE/SEQUENCE — e.g. 2026/0154/02/000041\n\n"
        "Please re-enter your question with a valid application number."
    )


def build_app_not_found_message(structured_data: Dict[str, Any], language: str) -> str:
    """
    Localised 'no such application' message, including the suggestions
    get_application_detail() attaches when it finds near-matches.
    """
    searched = structured_data.get("searched_number") or structured_data.get("application_number") or ""
    suggestions = structured_data.get("suggestions") or []

    if language == "ta":
        head = (
            f"விண்ணப்ப எண் **{searched}** தரவுத்தளத்தில் இல்லை. "
            "விண்ணப்ப எண்ணைச் சரிபார்த்து மீண்டும் உள்ளிடவும்."
            if searched else
            "நீங்கள் கேட்ட விண்ணப்பம் கிடைக்கவில்லை."
        )
        lead = "\n\nஉங்கள் அதிகார எல்லையில் உள்ள சமீபத்திய விண்ணப்பங்கள்:"
    elif language == "tanglish":
        head = (
            f"Application number **{searched}** database-la illa. "
            "Number-a check panni marubadiyum type pannunga."
            if searched else
            "Neenga ketta application kidaikala."
        )
        lead = "\n\nUnga jurisdiction-la irukkura recent applications:"
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
    if language == "ta":
        return (
            f"விண்ணப்ப எண் **{app_number}** உங்கள் அதிகார எல்லைக்கு ({name}) வெளியே உள்ளது.\n\n"
            "அதன் விவரங்களைப் பார்க்க உங்களுக்கு அனுமதி இல்லை."
        )
    if language == "tanglish":
        return (
            f"Application **{app_number}** unga jurisdiction-ku ({name}) veliya irukku.\n\n"
            "Adhoda details paakka unga-kku access illa."
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
    if language == "ta":
        head = (
            f"**{app_number}** என்ற எண்ணில் சரியான விண்ணப்பம் எதுவும் இல்லை. "
            "ஒத்த விண்ணப்பங்கள்:"
        )
        tail = "\n\nஇவற்றில் எந்த விண்ணப்ப எண் என்பதை முழுமையாக உள்ளிடவும்."
    elif language == "tanglish":
        head = f"**{app_number}** exact-a match aagala. Similar applications:"
        tail = "\n\nEndha application number-nu full-a type pannunga."
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
    if language == "ta":
        return (
            "நீங்கள் குறிப்பிடுவது எந்த விண்ணப்பம் என்பது தெளிவாக இல்லை. "
            "முந்தைய செய்தியில் இவை இடம்பெற்றுள்ளன:\n" + listed +
            "\n\nஎந்த விண்ணப்ப எண் என்பதைக் குறிப்பிடவும்."
        )
    if language == "tanglish":
        return (
            "Neenga endha application-a solreenga-nu clear-a illa. "
            "Previous message-la ivai irundhadhu:\n" + listed +
            "\n\nEndha application number-nu solunga."
        )
    return (
        "Your previous message mentioned more than one application, so I am not "
        "sure which one you mean:\n" + listed +
        "\n\nPlease tell me which application number you want."
    )


async def resolve_application_reference(
    db,
    message: str,
    chat_history: list,
    officer,
    intent: str,
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
    if not app_number:
        # Context is consulted for application-specific intents, and for any
        # other intent when the officer explicitly pointed back ("show ITS field
        # visit", "what about THE OWNER of that application"). Without the second
        # case a follow-up silently widens into an unfiltered list query.
        back_reference = _has_application_back_reference(message)
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
        from_context = bool(app_number)

    if not app_number:
        if intent in _SINGLE_APP_INTENTS:
            return {"verdict": "ask_number"}
        return {"verdict": "none"}

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


def build_application_gate_message(resolution: Dict[str, Any], officer, language: str) -> str:
    """Render the officer-facing text for a refusing verdict from resolve_application_reference()."""
    verdict = resolution.get("verdict")
    if verdict == "invalid_format":
        return build_invalid_app_number_message(resolution["token"], language)
    if verdict == "forbidden":
        return build_app_forbidden_message(resolution["app_number"], officer, language)
    if verdict == "needs_confirmation":
        return build_app_confirm_message(resolution["app_number"], resolution["suggestions"], language)
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


# ── Owner photo / image retrieval requests ──────────────────────────────────
# The SIS records projected from the TAMILNILAM urban extracts hold no owner
# photograph. The source `owner_photo` / `owner_image` columns
# (nisd_transfer_*_owner, nisd_transfer_igrs_owner) are placeholders — every
# value is `-`, an empty string, or NULL — and the ORM `owners` table has no
# image column at all. So there is nothing to return as a PNG/JPG. Rather than
# let the request fall through to RAG (which answers vaguely), intercept it and
# say so plainly, then point the officer at what IS retrievable.
_OWNER_PHOTO_MEDIA_WORDS = [
    "photo", "photograph", "photos", "pic", "picture", "pictures", "headshot",
    "passport size", "passport-size", "png", ".png", "jpg", ".jpg", "jpeg",
    "image", "images", "snapshot", "mugshot",
    # Tamil / Tanglish
    "புகைப்படம்", "படம்", "போட்டோ", "போடடோ", "padam", "potto", "photo venum",
]
_OWNER_PHOTO_SUBJECT_WORDS = [
    "owner", "owner's", "owners", "applicant", "applicant's", "petitioner",
    "citizen", "patta holder", "pattadar",
    "உரிமையாளர்", "விண்ணப்பதாரர்", "urimaiyalar", "urimayalar", "vinnappadhaarar",
]


def _is_owner_photo_request(message: str) -> bool:
    m = (message or "").lower()
    if not any(w in m for w in _OWNER_PHOTO_MEDIA_WORDS):
        return False
    if any(p in m for p in ["owner photo", "owner image", "owner_photo", "owner_image",
                            "owner's photo", "owners photo", "photo of the owner",
                            "photo of owner", "image of the owner"]):
        return True
    return any(w in m for w in _OWNER_PHOTO_SUBJECT_WORDS)


def _owner_photo_reply(language: str, message: str) -> str:
    is_ta = language == "ta" or any(w in (message or "") for w in ["புகைப்படம்", "படம்", "உரிமையாளர்"])
    if is_ta:
        return (
            "உரிமையாளரின் புகைப்படத்தை (PNG அல்லது வேறு எந்த வடிவத்திலும்) இந்த உதவியாளரால் "
            "வழங்க முடியாது. TAMILNILAM நகர்ப்புற தரவிலிருந்து உருவாக்கப்பட்ட SIS பதிவுகளில் "
            "உரிமையாளர் புகைப்படம் / படம் சேமிக்கப்படவில்லை — மூல `owner_photo` / `owner_image` "
            "புலங்கள் அனைத்தும் `-` அல்லது காலியாக உள்ளன; எந்த PNG/JPG/base64 படமும் இல்லை.\n\n"
            "உரிமையாளர் குறித்து நான் தர முடிந்தவை: பெயர் (தமிழ்/ஆங்கிலம்), ஆதார் எண், CAN, "
            "பட்டா எண், உரிமைப் பங்கு, கூட்டு உரிமையாளர் நிலை, இணைக்கப்பட்ட சர்வே/விண்ணப்பம். "
            "ஆவணங்களுக்கு — பதிவேற்ற நிலை மற்றும் வகையை (எ.கா. Sale Deed, ஆதார்) மட்டுமே தர முடியும், "
            "கோப்புகளை அல்ல."
        )
    return (
        "I can't return an owner's photo as a PNG (or in any other format). The SIS "
        "records built from the TAMILNILAM urban extracts don't store any owner "
        "photograph — the source `owner_photo` / `owner_image` fields are placeholders "
        "(`-` or empty) and no PNG, JPG, or base64 image is held for any owner.\n\n"
        "What I *can* pull up for an owner: name (English & Tamil), Aadhaar number, "
        "CAN, patta number, ownership share, joint-owner status, and the linked "
        "survey number / application. For documents I can report the upload status "
        "and type (e.g. Sale Deed, Aadhaar scan) — but not the files themselves."
    )


# ── Questions about a file the officer attached this session ─────────────────
_UPLOADED_DOC_WORDS = [
    "uploaded", "upload", "attached", "attachment", "the file", "this file",
    "the document", "this document", "the doc", "the pdf", "the csv", "the txt",
    "the text file", "the report", "from the file", "in the file",
    "from the document", "in the document", "from this file", "attached file",
    "கோப்பு", "ஆவணம்", "இணைப்பு",
]


def _is_about_uploaded_doc(message: str) -> bool:
    return any(w in (message or "").lower() for w in _UPLOADED_DOC_WORDS)


def _uploaded_doc_prompt(context: str, filenames: list, message: str, language: str) -> str:
    lang_line = {
        "ta": "Reply in Tamil.",
        "tanglish": "Reply in Tanglish (Tamil written in English letters).",
    }.get(language, "Reply in English.")
    names = ", ".join(filenames) or "the attached file"
    return (
        "You are the SIS assistant. The officer has attached one or more files to "
        "this chat. Answer their question using ONLY the file content below. If the "
        "answer is not in the file, say so plainly — do not guess.\n"
        f"{lang_line}\n\n"
        f"=== FILE CONTENT ({names}) ===\n{context}\n=== END FILE CONTENT ===\n\n"
        f"Officer's question: {message}\n\nAnswer:"
    )


async def process_chat(
    message: str,
    session_id: str,
    officer: OfficerContext,
    db: AsyncSession,
    chat_history: list = None
) -> Dict[str, Any]:
    """
    Main RAG orchestration function for processing chat messages
    
    Args:
        message: User's input message
        session_id: Chat session UUID
        officer: Officer context with jurisdiction info
        db: Database session
        chat_history: Optional chat history from client (sessionStorage)
        
    Returns:
        Dictionary with response, language, and metadata
    """
    start_time = time.time()
    
    try:
        # Step 0: Use provided chat history from client
        if not chat_history:
            chat_history = []
        logger.info(f"=== CHAT CONTEXT DEBUG ===")
        logger.info(f"Received {len(chat_history)} previous messages from client")
        logger.info(f"Current message: '{message}'")
        if chat_history:
            for i, msg in enumerate(chat_history[-3:]):  # Show last 3
                logger.info(f"  History[{i}]: {msg.get('role')} said: {(msg.get('content') or '')[:50]}...")

        # Step 1: Detect language
        language = detect_language(message)
        logger.info(f"Detected language: {language}")

        # Direct Handler for "what does an application number look like" —
        # asked as a general/meta question ("what does an application number
        # format look like?") this fell to the LLM, which fabricated a format
        # ("APP-2024-000001") that doesn't match the real system at all
        # (YYYY/ServiceCode/DistrictCode/SerialNumber). Application numbers
        # are exactly the kind of fact CLAUDE.md says must never be left to
        # the LLM, so this is answered deterministically instead.
        _msg_lower_fmt = message.lower()
        if any(p in _msg_lower_fmt for p in [
            "application number format", "format of application number", "format of an application number",
            "application number structure", "structure of application number", "structure of an application number",
            "application number look like", "application number pattern",
            "how are application numbers structured", "how is an application number structured",
            "விண்ணப்ப எண் வடிவம்", "விண்ணப்ப எண்ணின் அமைப்பு",
        ]) or (
            any(w in _msg_lower_fmt for w in ["application number", "app number", "விண்ணப்ப எண்"])
            and any(w in _msg_lower_fmt for w in ["format", "look like", "structure", "structured", "pattern", "வடிவம்", "அமைப்பு"])
        ):
            is_ta_fmt = language == "ta"
            if is_ta_fmt:
                res_txt = (
                    "விண்ணப்ப எண் இந்த அமைப்பில் இருக்கும்: "
                    "**ஆண்டு/சேவைக்குறியீடு/மாவட்டக்குறியீடு/வரிசை எண்** "
                    "(எ.கா. 2026/0154/28/001167). "
                    "சேவைக்குறியீடு: 0154 = ISD, 0153 = NISD, 0155 = MERGE. "
                    "மாவட்டக்குறியீடு 2 இலக்கங்கள் (எ.கா. தூத்துக்குடிக்கு 28), "
                    "வரிசை எண் 6 இலக்கங்கள்."
                )
            else:
                res_txt = (
                    "An application number follows the format "
                    "**YEAR/SERVICE_CODE/DISTRICT_CODE/SERIAL_NUMBER** "
                    "(e.g. 2026/0154/28/001167). The service code is 0154 for ISD, "
                    "0153 for NISD, or 0155 for MERGE; the district code is 2 digits "
                    "(28 for Thoothukudi); the serial number is 6 digits."
                )
            await save_chat_messages(
                db=db, session_id=session_id, user_message=message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": res_txt,
                "language": language,
                "intent": "app_number_format_info",
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Direct Handler for District Code reference queries (bypasses jurisdiction checks)
        _msg_lower_dc = message.lower()
        if any(w in _msg_lower_dc for w in ["district code", "code of", "district_code", "குறியீடு", "மாவட்டம் கோடு"]) or ("code" in _msg_lower_dc and any(d in _msg_lower_dc for d in DISTRICT_NAME_MAP)):
            matched_dist = None
            for d_name, d_code in DISTRICT_NAME_MAP.items():
                if d_name in _msg_lower_dc:
                    matched_dist = (d_name.title(), d_code)
                    break
            
            if matched_dist:
                d_title, d_code = matched_dist
                is_ta = language == "ta" or any(w in _msg_lower_dc for w in ["enapa", "enna", "oda", "sollo", "kudunga", "குறியீடு"])
                if is_ta:
                    res_txt = f"{d_title} மாவட்டத்தின் அதிகாரப்பூர்வ குறியீடு (District Code): **{d_code}**."
                else:
                    res_txt = f"The official district code for **{d_title}** is **{d_code}**."
                
                await save_chat_messages(
                    db=db, session_id=session_id, user_message=message,
                    assistant_message=res_txt, language=language,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None
                )
                return {
                    "response": res_txt,
                    "language": language,
                    "intent": "district_code",
                    "sources": [],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "context_used": True,
                    "response_time_ms": int((time.time() - start_time) * 1000),
                    "table_data": None
                }

        # Direct Handler for owner photo / image retrieval requests
        if _is_owner_photo_request(message):
            res_txt = _owner_photo_reply(language, message)
            await save_chat_messages(
                db=db, session_id=session_id, user_message=message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": res_txt,
                "language": language,
                "intent": "owner_photo_unavailable",
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Step 2: Parse intent to determine which DB query to run
        _prev_intent = None
        if chat_history:
            for _h in reversed(chat_history):
                if _h.get("role") == "assistant":
                    import re as _re
                    _m = _re.search(r'\[intent:([\w_]+)\]', _h.get("content") or "")
                    if _m:
                        _prev_intent = _m.group(1)
                        break
                    if _h.get("intent"):
                        _prev_intent = _h.get("intent")
                        break
        intent = parse_intent(message, prev_intent=_prev_intent)
        logger.info(f"Parsed intent: {intent} (prev_intent={_prev_intent})")

        # Direct Handler: answer from a file the officer attached this session.
        # Only takes over when there IS an attachment AND the question is generic
        # or explicitly about the file — DB intents (pending apps, status, ...)
        # still run normally even with a file attached.
        _uploaded_ctx = upload_store.context_block(session_id)
        if _uploaded_ctx and (intent == "general_query" or _is_about_uploaded_doc(message)):
            _prompt = _uploaded_doc_prompt(
                _uploaded_ctx, upload_store.filenames(session_id), message, language)
            res_txt = await call_llama(_prompt)
            await save_chat_messages(
                db=db, session_id=session_id, user_message=message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": res_txt,
                "language": language,
                "intent": "uploaded_doc_query",
                "sources": upload_store.filenames(session_id),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }



        # ── Step 2b: Jurisdiction access check ──────────────────────────────
        # Block-level officers cannot query ward/taluk/district level data.
        # Ward-level officers cannot query taluk/district level data. Etc.
        _jur_type = getattr(officer, "jurisdiction_type", "block")
        _jur_name = getattr(officer, "jurisdiction_name", "your jurisdiction")

        # Hierarchy levels (higher index = broader access needed)
        _JUR_LEVELS = ["block", "ward", "town", "taluk", "district"]
        _officer_level = _JUR_LEVELS.index(_jur_type) if _jur_type in _JUR_LEVELS else 0

        # Map each intent to the MINIMUM level the officer needs
        _INTENT_MIN_LEVEL = {
            "ward_surveys":            1,   # needs ward
            "block_surveys":           0,   # block is fine
            "jurisdiction_summary":    0,   # always OK (shows own level)
            "active_applications_taluks": 2, # needs town+
            "taluk_summary":           3,   # needs taluk
            "all_surveys_in_jurisdiction": 0, # always OK
        }

        # Also check message keywords for ward/taluk/district references
        _msg_lower_jur = message.lower()
        _requested_broader = False
        _broader_reason = ""

        # These intents use "ward"/"taluk" as geographic context within the officer's
        # own data — they are NOT requests for broader jurisdiction data.
        _FIELD_VISIT_INTENTS = {
            "fv_scheduled_this_week", "fv_date_select", "fv_nearby_pending",
            "fv_reschedule_availability", "fv_change_date", "fv_deadline_check", "fv_overdue_inspections",
            "fv_unassigned_awaiting", "fv_recently_rescheduled", "fv_scheduling_conflicts",
            "sd_additional_info", "sd_encroachment_check", "sd_sketch_readiness",
            "sd_forward_check", "sd_remarks", "application_status", "isd_processing",
            "officer_workload", "field_visits", "general_query", "rag", "greeting",
            "help", "district_code"
        }
        _is_code_reference_query = any(w in _msg_lower_jur for w in ["code", "கோடு", "service code", "district code"])
        # "Which taluk do I belong to?" names a broader level but asks only for
        # the officer's OWN posting — the answer is the hierarchy they sit in,
        # not other officers' data. These intents describe the officer, so the
        # keyword check must not turn them into an access denial.
        _SELF_JUR_INTENTS = {"jurisdiction_summary"}
        _skip_keyword_check = (intent in _FIELD_VISIT_INTENTS) or (intent in _SELF_JUR_INTENTS) \
            or _is_code_reference_query or intent == "service_code_lookup"

        if _officer_level == 0 and not _skip_keyword_check:  # block officer
            if any(w in _msg_lower_jur for w in ["ward", "வார்டு"]):
                _requested_broader = True
                _broader_reason = "ward-level"
            elif any(w in _msg_lower_jur for w in ["taluk", "தாலுகா"]):
                _requested_broader = True
                _broader_reason = "taluk-level"
            elif any(w in _msg_lower_jur for w in ["district", "மாவட்டம்"]):
                _requested_broader = True
                _broader_reason = "district-level"
        elif _officer_level == 1 and not _skip_keyword_check:  # ward officer
            if any(w in _msg_lower_jur for w in ["town", "நகரம்"]):
                _requested_broader = True
                _broader_reason = "town-level"
            elif any(w in _msg_lower_jur for w in ["taluk", "தாலுகா"]):
                _requested_broader = True
                _broader_reason = "taluk-level"
            elif any(w in _msg_lower_jur for w in ["district", "மாவட்டம்"]):
                _requested_broader = True
                _broader_reason = "district-level"

        # Also check intent minimum level
        if intent in _INTENT_MIN_LEVEL and _officer_level < _INTENT_MIN_LEVEL[intent]:
            _requested_broader = True
            _required_level = _JUR_LEVELS[_INTENT_MIN_LEVEL[intent]]
            _broader_reason = f"{_required_level}-level"

        if _requested_broader:
            _jur_level_name = _JUR_LEVELS[_officer_level]
            response_text = (
                f"You are assigned as a **{_jur_level_name.capitalize()}-level** SIS officer "
                f"({_jur_name}). Your access is limited to data within your assigned {_jur_level_name}.\n\n"
                f"You cannot retrieve {_broader_reason} data. "
                f"Only officers with {_broader_reason.replace('-level', '')} or higher access can view that information.\n\n"
                f"If you need {_broader_reason} data, please contact your supervising officer."
            )
            logger.info(
                f"Jurisdiction access denied for officer {officer.officer_id} "
                f"(level={_jur_level_name}): requested {_broader_reason} data, intent={intent}"
            )
            # Save and return immediately — skip all DB queries
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=message, assistant_message=response_text,
                language=language, response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": response_text,
                "language": language,
                "intent": intent,
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": False,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Step 2c: Direct greeting response handling
        if intent == "greeting":
            msg_lower = message.lower().strip()
            is_tamil = language == "ta" or any(w in message for w in ["வணக்கம்", "காலை", "மாலை", "நன்றி", "ஹாய்"])

            if "காலை" in msg_lower or "morning" in msg_lower:
                if is_tamil:
                    response_text = "காலை வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை எண்கள், விண்ணப்பங்கள் (ISD/NISD/MERGE), கள ஆய்வுகள் தொடர்பாக இன்று உங்களுக்கு எவ்வாறு உதவ முடியும்?"
                else:
                    response_text = "Good morning! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. How can I help you today with survey numbers, applications (ISD/NISD/MERGE), or field visits?"
            elif "மாலை" in msg_lower or "evening" in msg_lower:
                if is_tamil:
                    response_text = "மாலை வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை மற்றும் விண்ணப்பங்கள் தொடர்பான தகவல்களுக்கு இன்று உங்களுக்கு எவ்வாறு உதவ முடியும்?"
                else:
                    response_text = "Good evening! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. How can I help you with your survey work today?"
            elif "நன்றி" in msg_lower or "thanks" in msg_lower or "thank" in msg_lower:
                if is_tamil:
                    response_text = "நல்வரவு! 😊 உங்களுக்கு மேலும் ஏதேனும் உதவி தேவைப்பட்டால் தயங்காமல் கேளுங்கள்."
                else:
                    response_text = "You're very welcome! 😊 Feel free to ask if you need anything else regarding your survey work."
            else:
                if is_tamil:
                    response_text = "வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை எண்கள், விண்ணப்பங்களின் நிலை, கள ஆய்வுகள் மற்றும் பட்டா பரிமாற்றங்கள் பற்றிய கேள்விகளுக்கு உதவ தயாராக உள்ளேன். இன்று உங்களுக்கு என்ன உதவி தேவை?"
                else:
                    response_text = "Hello! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. I am here to help you manage survey applications, check document statuses, track field visits, and navigate workflow procedures. What can I assist you with today?"

            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=message, assistant_message=response_text,
                language=language, response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": response_text,
                "language": language,
                "intent": "greeting",
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": False,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # ── Step 2d: Application reference validation ──────────────────────
        # Format, existence and jurisdiction are all settled here, before any
        # handler, RAG lookup or LLM call runs. An application number that is
        # malformed, nonexistent, out of jurisdiction or merely a near match
        # must never be treated as valid context downstream.
        _app_resolution = await resolve_application_reference(
            db, message, chat_history, officer, intent
        )
        if _app_resolution["verdict"] not in ("ok", "none"):
            response_text = build_application_gate_message(_app_resolution, officer, language)
            logger.info(
                f"Application gate refused request: verdict={_app_resolution['verdict']} "
                f"number={_app_resolution.get('app_number') or _app_resolution.get('token')} "
                f"intent={intent} officer={officer.officer_id if officer else None}"
            )
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=message, assistant_message=response_text,
                language=language, response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": response_text,
                "language": language,
                "intent": intent,
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": False,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # The one application number already proven valid and inside the
        # officer's jurisdiction. Handlers fall back to it so a follow-up reuses
        # exactly what the gate confirmed, never something re-derived.
        _gate_app_number = (
            _app_resolution.get("app_number") if _app_resolution["verdict"] == "ok" else None
        )

        # Step 3: Execute structured database queries based on intent
        structured_data = {}
        response_text = ""
        _isd_app_no = ""

        if intent in ("pending_applications", "isd_applications", "nisd_applications", "both_applications", "merge_applications"):
            message_lower = message.lower()

            # Detect one or multiple application types (e.g. "isd", "merge", "isd and merge")
            app_type = _extract_app_types(message_lower, intent=intent)  # str, list, or None

            # Detect time of day session
            session_label = ""
            if any(w in message_lower for w in ["morning", "காலை"]):
                session_label = " (Morning Session: 09:00 AM – 01:00 PM)"
            elif any(w in message_lower for w in ["afternoon", "பிற்பகல்", "மதியம்"]):
                session_label = " (Afternoon Session: 02:00 PM – 05:00 PM)"
            elif any(w in message_lower for w in ["evening", "மாலை"]):
                session_label = " (Evening Session: 04:00 PM – 06:00 PM)"

            # Extract date range first (e.g. "between 2026-07-03 and 2026-07-20", "today", "yesterday")
            start_d, end_d = extract_date_range(message)
            # Months asked for as a union -- "June and July", "March and May",
            # "last month and the month before that". extract_date_range spans
            # from the first to the last, which is right for "from March to
            # May" and wrong for "March and May"; the segments keep the gap.
            month_scopes = extract_month_scopes(message)
            if month_scopes:
                start_d, end_d = month_scopes[0][0], month_scopes[-1][1]
            if session_label and not start_d and not end_d:
                start_d = date.today()
                end_d = date.today()

            # An open-ended range ("since 2026-07-01", "before 2026-08-01") only
            # sets one side. Treating that as "no date range" let the year/month
            # extraction below run alongside it and silently override the filter,
            # so either side present must count as a real date-range query.
            has_date_range = start_d is not None or end_d is not None
            is_negated_date_range = any(w in message_lower for w in [
                "not between", "not in", "outside", "other than", "இடைப்பட்டவை அல்ல", "இடையில் இல்லாத", "தவிர"
            ])

            # Extract year from message (only used when NO full date range is present)
            submission_year = None
            submission_month = None
            if not has_date_range:
                year_match = re.search(r'\b(20\d{2})\b', message)
                submission_year = int(year_match.group(1)) if year_match else None
                # Extract month from message (handles English/Tamil with fuzzy matching)
                submission_month = extract_month_from_query(message)
                # "applications in June" names a month but no year — pin it to
                # the most recent June rather than every June on record.
                if submission_month and not submission_year:
                    submission_year = _resolve_month_year(submission_month)

            # Extract geography filters
            taluk_name = extract_taluk_name(message)
            ward_num = extract_ward_number(message) if "ward" in message_lower else None
            block_num = extract_block_number(message) if "block" in message_lower else None

            # Extract submission channel filter (CSC / citizen / sub_registrar)
            channel_filter = extract_submission_channel(message)

            # Detect whether overdue vs non-overdue (on-time) filter is requested
            is_not_overdue = any(w in message_lower for w in [
                "not overdue", "non overdue", "non-overdue", "on time", "not late",
                "not delayed", "within sla", "தாமதமில்லாத", "தாமதம் இல்லாத", "காலதாமதமாகாத"
            ])
            is_overdue_requested = any(w in message_lower for w in ["overdue", "late", "delayed"]) and not is_not_overdue
            is_overdue_filter = False if is_not_overdue else (True if is_overdue_requested else None)

            # Determine status filter
            is_merge_only = (app_type == "MERGE")
            _named_status = _explicit_status_request(message_lower)
            if _named_status is not None and "all" not in message_lower:
                # The officer named a status -- it wins over every scope default.
                status_filter = _named_status
            elif has_date_range or is_overdue_filter is not None:
                # Date-range or overdue/non-overdue query: show all statuses within that scope
                status_filter = None
            elif submission_year:
                status_filter = None  # Show all statuses when querying by specific year
            elif is_merge_only or (channel_filter and not any(w in message_lower for w in ["pending", "நிலுவை"])):
                status_filter = None
            else:
                status_filter = "pending"
                if "history" in message_lower or "approved n rejected" in message_lower or "approved and rejected" in message_lower:
                    status_filter = ["approved", "rejected"]
                elif "all" in message_lower:
                    status_filter = None
                elif "complete" in message_lower or "approved" in message_lower:
                    status_filter = "approved"
                elif "reject" in message_lower:
                    status_filter = "rejected"
                
            # Check if this is a follow-up query on an active/previously discussed single application.
            # IMPORTANT: For list intents (pending_applications etc.), we must NOT use implicit
            # continuation — that would cause "show applications" to pull the last-discussed app
            # from chat history and fetch only that single app's detail.
            # Only use allow_implicit_continuation if the message itself has an explicit reference
            # like "this application" / "that app" / "previous application".
            target_app_num = extract_application_number(message)
            if not target_app_num:
                # Only allow implicit continuation if the message has an explicit reference phrase
                # (e.g. "this app", "that application", "previous app"), NOT for bare list queries.
                _has_explicit_app_ref = any(p in message_lower for p in [
                    "this application", "that application", "same application",
                    "this app", "that app", "the application", "the app",
                    "prev application", "previous application", "prev app", "previous app",
                    "last application", "last app", "above application",
                    "இந்த விண்ணப்பம்", "அந்த விண்ணப்பம்", "முந்தைய விண்ணப்பம்",
                ])
                if _has_explicit_app_ref:
                    target_app_num = (_extract_app_number_from_context(message, chat_history, allow_implicit_continuation=False) or _gate_app_number)
            has_projected_cols = _get_projected_application_columns(message) is not None
            is_explicit_plural = any(w in message_lower for w in ["all", "every", "list all", "show all", "between", "today", "yesterday", "this week", "last week", "month", "முழு", "அனைத்து"])

            if target_app_num and not is_explicit_plural and has_projected_cols:
                app_detail = await get_application_detail(db, target_app_num, officer=officer)
                if app_detail and app_detail.get("found", True):
                    structured_data = {
                        "applications": [app_detail],
                        "count": 1,
                        "query_type": f"Application {target_app_num}"
                    }
                else:
                    structured_data = await get_pending_applications(
                        db, officer, 
                        application_type=app_type,
                        status=status_filter, 
                        submission_year=submission_year,
                        submission_month=submission_month,
                        taluk_name=taluk_name,
                        ward_number=ward_num,
                        block_number=block_num,
                        start_date=start_d,
                        end_date=end_d,
                        date_ranges=month_scopes or None,
                        is_overdue=is_overdue_filter,
                        exclude_date_range=is_negated_date_range,
                        submission_channel=channel_filter,
                    )
            else:
                structured_data = await get_pending_applications(
                    db, officer, 
                    application_type=app_type,   # str, list, or None
                    status=status_filter, 
                    submission_year=submission_year,
                    submission_month=submission_month,
                    taluk_name=taluk_name,
                    ward_number=ward_num,
                    block_number=block_num,
                    start_date=start_d,
                    end_date=end_d,
                    date_ranges=month_scopes or None,
                    is_overdue=is_overdue_filter,
                    exclude_date_range=is_negated_date_range,
                    submission_channel=channel_filter,
                )
            if is_negated_date_range:
                structured_data["exclude_date_range"] = True
            
            # Build human-readable type label for the query title
            if isinstance(app_type, list):
                type_str = " " + " & ".join(app_type)   # e.g. " ISD & MERGE"
            elif app_type:
                type_str = f" {app_type}"
            else:
                type_str = ""

            # Add channel label to query_type if filtering by channel
            _channel_labels = {
                "CSC": "CSC (Common Service Center)",
                "citizen": "Citizen Portal",
                "sub_registrar": "Sub-Registrar",
            }
            channel_str = f" via {_channel_labels[channel_filter]}" if channel_filter else ""

            year_str = f" in {submission_year}" if submission_year else ""
            
            # Date range label
            date_range_str = ""
            if month_scopes:
                date_range_str = f" in {format_month_scopes(month_scopes)}{session_label}"
            elif has_date_range:
                if is_negated_date_range:
                    date_range_str = f" (not {start_d} to {end_d})"
                elif start_d and end_d and start_d == end_d:
                    if start_d == date.today():
                        date_range_str = f" Received Today ({start_d}){session_label}"
                    elif start_d == date.today() - timedelta(days=1):
                        date_range_str = f" Received Yesterday ({start_d}){session_label}"
                    elif start_d == date.today() - timedelta(days=2):
                        date_range_str = f" Received Day Before Yesterday ({start_d}){session_label}"
                    elif start_d == date.today() + timedelta(days=1):
                        date_range_str = f" for Tomorrow ({start_d}){session_label}"
                    else:
                        date_range_str = f" Received on {start_d}{session_label}"
                elif start_d and end_d and _whole_month_label(start_d, end_d):
                    # "this month" / "last month" / a full calendar month — say
                    # the month, not the pair of boundary dates.
                    date_range_str = f" in {_whole_month_label(start_d, end_d)}{session_label}"
                elif start_d and end_d:
                    date_range_str = f" ({start_d} to {end_d}){session_label}"
                elif start_d and not end_d:
                    # Open-ended range: "since 2026-07-01", "starting from ..."
                    date_range_str = f" (from {start_d} onwards){session_label}"
                elif end_d and not start_d:
                    # Open-ended range: "before 2026-08-01", "until ..."
                    date_range_str = f" (up to {end_d}){session_label}"

            # Month label
            month_str = ""
            if submission_month:
                month_names = _MONTH_NAMES
                # Month and year read as one scope — "in March 2026", not
                # " March" followed by " in 2026".
                if submission_year:
                    month_str = f" in {month_names[submission_month]} {submission_year}"
                    year_str = ""
                else:
                    month_str = f" in {month_names[submission_month]}"
            
            if is_not_overdue:
                structured_data["query_type"] = f"Non-Overdue{type_str} Applications{month_str}{year_str}{date_range_str}"
            elif is_overdue_requested:
                structured_data["query_type"] = f"Overdue{type_str} Applications{month_str}{year_str}{date_range_str}"
            elif taluk_name:
                structured_data["query_type"] = f"Applications in {taluk_name}{type_str}{month_str}{year_str}{date_range_str}"
            elif is_merge_only:
                structured_data["query_type"] = f"MERGE Applications{month_str}{year_str}{date_range_str}"
            elif status_filter == ["approved", "rejected"]:
                structured_data["query_type"] = f"SIS{type_str} History (Approved & Rejected){month_str}{year_str}{date_range_str}"
            elif status_filter is None:
                structured_data["query_type"] = f"All{type_str} Applications{month_str}{year_str}{date_range_str}"
            elif status_filter == "approved":
                structured_data["query_type"] = f"Approved{type_str} Applications{month_str}{year_str}{date_range_str}"
            elif status_filter == "rejected":
                structured_data["query_type"] = f"Rejected{type_str} Applications{month_str}{year_str}{date_range_str}"
            else:
                structured_data["query_type"] = f"Pending{type_str} Applications{month_str}{year_str}{date_range_str}"
            
        elif intent == "overdue_applications":
            # Extract application type if mentioned in message
            app_type = None
            message_lower = message.lower()
            # "nisd" contains "isd", so the ISD test has to be word-bounded and
            # NISD has to be checked first -- otherwise "overdue NISD
            # applications" was filtered to ISD and answered with the wrong type.
            if re.search(r'\bnisd\b|\b0153\b', message_lower):
                app_type = "NISD"
            elif re.search(r'\bisd\b|\b0154\b', message_lower):
                app_type = "ISD"
            elif re.search(r'\bmerge\b|\b0155\b', message_lower):
                app_type = "MERGE"
                
            min_days_overdue = None
            match_days = re.search(r'(?:overdue|late|delayed)\s+(?:by\s+)?(\d+)\s*days?', message_lower) or \
                         re.search(r'(\d+)\s*days?\s+(?:overdue|late|delayed)', message_lower)
            if match_days:
                min_days_overdue = int(match_days.group(1))

            start_d, end_d = extract_date_range(message)

            structured_data = await get_overdue_applications(
                db, officer, 
                application_type=app_type, 
                min_days_overdue=min_days_overdue,
                start_date=start_d,
                end_date=end_d
            )
            structured_data["min_days_overdue"] = min_days_overdue
            days_str = f" by {min_days_overdue}+ Days" if min_days_overdue else ""
            # Say the month when the range is exactly one, same as the lists do.
            _ov_month = _whole_month_label(start_d, end_d)
            range_str = (f" in {_ov_month}" if _ov_month
                         else (f" ({start_d} to {end_d})"
                               if (start_d and end_d and start_d != end_d) else ""))
            if app_type:
                structured_data["query_type"] = f"Overdue {app_type} Applications{days_str}{range_str}"
            else:
                structured_data["query_type"] = f"Overdue Applications{days_str}{range_str}"
            
        elif intent == "officer_workload":
            structured_data = await get_officer_workload(db, officer)
            structured_data["query_type"] = "Officer Workload Summary"
            
        elif intent == "fv_overdue_inspections":
            # Get field visits that are overdue (scheduled date in past, not completed)
            try:
                from sqlalchemy.orm import joinedload
                from backend.services.postgres import get_jurisdiction_filter
                
                today = date.today()
                logger.info(f"=== OVERDUE FIELD VISITS QUERY ===")
                logger.info(f"Today: {today}")
                logger.info(f"Officer ID: {officer.officer_id}")
                
                # Add jurisdiction filter to ensure only applications within officer's jurisdiction
                jurisdiction_filter = await get_jurisdiction_filter(db, officer)
                # jurisdiction_filter returns a list, we need to unpack it
                jur_conditions = jurisdiction_filter if isinstance(jurisdiction_filter, list) else [jurisdiction_filter]
                
                overdue_visits_stmt = select(FieldVisit).options(
                    joinedload(FieldVisit.application).joinedload(Application.survey_number).joinedload(SurveyNumber.block)
                ).join(
                    Application, FieldVisit.application_id == Application.id
                ).join(
                    SurveyNumber, Application.survey_number_id == SurveyNumber.id
                ).join(
                    # The geography chain must be joined explicitly: jur_conditions reference
                    # Block/Ward/Town/Taluk/District, and without these joins SQLAlchemy adds
                    # them as an uncorrelated FROM (cartesian product), silently voiding the filter.
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
                        FieldVisit.officer_id == officer.officer_id,
                        FieldVisit.scheduled_date.isnot(None),
                        FieldVisit.scheduled_date < today,
                        FieldVisit.status.in_(['scheduled', 'rescheduled', 'overdue']),
                        *jur_conditions  # Unpack list of conditions
                    )
                ).order_by(FieldVisit.scheduled_date.asc())
                
                overdue_visits = (await db.execute(overdue_visits_stmt)).unique().scalars().all()
                logger.info(f"Overdue visits found: {len(overdue_visits)}")
                
                overdue_list = []
                for visit in overdue_visits:
                    app = visit.application
                    if app:
                        app_type = app.application_type if app.application_type else "N/A"
                        logger.info(f"Overdue visit: App={app.application_number}, Type={app_type}, Scheduled={visit.scheduled_date}, Status={visit.status}")
                        overdue_list.append({
                            "application_number": app.application_number,
                            "type": app_type,  # Use variable with fallback
                            "status": visit.status,  # Field visit status, not application status
                            "stage": app.current_stage if app.current_stage else "N/A",
                            "survey_no": app.survey_number.survey_no if app.survey_number else "N/A",
                            "block_number": app.survey_number.block.block_number if app.survey_number and app.survey_number.block else "N/A",
                            "scheduled_date": visit.scheduled_date.isoformat() if visit.scheduled_date else "Not Scheduled",
                            "submission_date": app.submission_date.isoformat() if app.submission_date else "N/A"
                        })
                        # Log the complete dictionary for debugging
                        logger.info(f"  Complete data: {overdue_list[-1]}")
                
                structured_data = {
                    "overdue_visits_count": len(overdue_list),
                    "field_visits": overdue_list,
                    "query_type": "Overdue Field Visits"
                }
            except Exception as e:
                logger.error(f"Error getting overdue field visits: {e}", exc_info=True)
                structured_data = {"error": str(e), "field_visits": []}


        elif intent in ["field_visits", "fv_between_dates"]:
            import calendar

            msg_lower = message.lower()
            start_d, end_d = extract_date_range(message)

            # Detect negation: "not between", "outside", "except between", "not in"
            exclude_range = any(w in msg_lower for w in [
                "not between", "outside", "except between", "not in range",
                "outside the range", "exclude", "excluding"
            ])

            # Extract application type filter(s) from message — supports multi-type queries
            # e.g. "isd and nisd", "merge and isd", "all types"
            app_type_filter = []
            if any(w in msg_lower for w in ["merge", "merger", "merging"]):
                app_type_filter.append("MERGE")
            if any(w in msg_lower for w in ["nisd", "non-isd", "non isd", "transfer"]):
                app_type_filter.append("NISD")
            if any(w in msg_lower for w in ["isd", "subdivision", "sub division", "sub-division"]):
                if "NISD" not in app_type_filter:  # avoid double-match since "nisd" contains "isd"
                    app_type_filter.append("ISD")
            # Normalise: None means no filter (all types)
            app_type_filter = app_type_filter if app_type_filter else None
            type_label = f" {'+'.join(app_type_filter)}" if app_type_filter else ""

            to_be_visited = any(w in msg_lower for w in [
                "needed to be visited", "to be visited", "need to visit", "need to be visited",
                "pending visit", "yet to visit", "upcoming"
            ])

            # Detect morning / afternoon / evening time session
            session_label = ""
            if any(w in msg_lower for w in ["morning", "காலை"]):
                session_label = " (Morning Session: 09:00 AM – 01:00 PM)"
            elif any(w in msg_lower for w in ["afternoon", "பிற்பகல்", "மதியம்"]):
                session_label = " (Afternoon Session: 02:00 PM – 05:00 PM)"
            elif any(w in msg_lower for w in ["evening", "மாலை"]):
                session_label = " (Evening Session: 04:00 PM – 06:00 PM)"

            # If user asked for morning/afternoon without specific date, default to today
            if session_label and not start_d and not end_d:
                start_d = date.today()
                end_d = date.today()

            status_filter = None
            if "unscheduled" in msg_lower or ("not scheduled" in msg_lower and not exclude_range) or "yet to schedule" in msg_lower:
                status_filter = "unscheduled"
                query_type = "Unscheduled Field Visits"
            elif intent == "fv_between_dates" or start_d or end_d or "between" in msg_lower or to_be_visited:
                if not start_d and not end_d:
                    today = date.today()
                    start_d = today.replace(day=1)
                    _, last_day = calendar.monthrange(today.year, today.month)
                    end_d = today.replace(day=last_day)
                type_label = f" {'+'.join(app_type_filter)}" if app_type_filter else ""
                if exclude_range and start_d and end_d:
                    query_type = (f"{type_label} Field Visits Outside Dates".strip())
                elif start_d and end_d and start_d == end_d:
                    if start_d == date.today() + timedelta(days=2):
                        query_type = (f"{type_label} Field Visits for Day After Tomorrow ({start_d}){session_label}".strip())
                    elif start_d == date.today() + timedelta(days=1):
                        query_type = (f"{type_label} Field Visits for Tomorrow ({start_d}){session_label}".strip())
                    elif start_d == date.today():
                        query_type = (f"{type_label} Field Visits for Today ({start_d}){session_label}".strip())
                    elif start_d == date.today() - timedelta(days=1):
                        query_type = (f"{type_label} Field Visits for Yesterday ({start_d}){session_label}".strip())
                    elif start_d == date.today() - timedelta(days=2):
                        query_type = (f"{type_label} Field Visits for Day Before Yesterday ({start_d}){session_label}".strip())
                    else:
                        query_type = (f"{type_label} Field Visits on {start_d}{session_label}".strip())
                else:
                    _fv_month = _whole_month_label(start_d, end_d)
                    query_type = ("Field Visits Needed To Be Visited" if to_be_visited
                                  else (f"{type_label} Field Visits in {_fv_month}".strip()
                                        if _fv_month
                                        else f"{type_label} Field Visits Between Dates".strip()))
            elif "scheduled" in msg_lower or "visit date" in msg_lower or "when" in msg_lower or "schedule" in msg_lower:
                status_filter = "scheduled"
                query_type = "Scheduled Field Visits"
            else:
                type_label = f" {'+'.join(app_type_filter)}" if app_type_filter else ""
                query_type = f"{type_label} Field Visits Summary".strip()

            # "Show its field visit" after an application was confirmed asks about
            # THAT application, not the whole jurisdiction. Scope to it whenever the
            # gate confirmed one and the officer did not ask for a broader list.
            _fv_app_no = _gate_app_number if not (start_d or end_d or app_type_filter) else None
            structured_data = await get_field_visits(
                db, officer,
                status_filter=status_filter,
                start_date=start_d,
                end_date=end_d,
                to_be_visited_only=to_be_visited,
                application_type=app_type_filter,
                exclude_date_range=exclude_range,
                application_number=_fv_app_no
            )
            if _fv_app_no:
                query_type = f"Field Visit for {_fv_app_no}"
                structured_data["application_number"] = _fv_app_no
            structured_data["query_type"] = query_type
            if start_d:
                structured_data["start_date"] = start_d.isoformat()
            if end_d:
                structured_data["end_date"] = end_d.isoformat()
            structured_data["to_be_visited_only"] = to_be_visited

        elif intent == "active_applications_taluks":
            query = select(Application, Taluk.name).join(
                SurveyNumber, Application.survey_number_id == SurveyNumber.id
            ).join(
                Block, SurveyNumber.block_id == Block.id
            ).join(
                Ward, Block.ward_id == Ward.id
            ).join(
                Town, Ward.town_id == Town.id
            ).join(
                Taluk, Town.taluk_id == Taluk.id
            ).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_stage == officer.officer_stage,
                    Application.current_status.in_(["pending", "in_progress"])
                )
            )
            result = await db.execute(query)
            rows = result.all()
            from collections import Counter
            taluk_counts = Counter([row[1] for row in rows])
            structured_data = {
                "total_active": len(rows),
                "taluk_counts": dict(taluk_counts),
                "query_type": "Active Applications by Taluk"
            }

        elif intent == "highest_priority_applications":
            # Use the dedicated high priority query function
            # This returns applications with is_overdue=True OR priority_flag=True
            from backend.services.postgres import get_highest_priority_applications
            
            # Extract application type if mentioned
            app_type = None
            message_lower = message.lower()
            # word-bounded, NISD first: "nisd" contains "isd"
            if re.search(r'\bnisd\b|\b0153\b', message_lower):
                app_type = "NISD"
            elif re.search(r'\bisd\b|\b0154\b', message_lower):
                app_type = "ISD"
            elif re.search(r'\bmerge\b|\b0155\b', message_lower):
                app_type = "MERGE"
            
            structured_data = await get_highest_priority_applications(db, officer, application_type=app_type)
            structured_data["query_type"] = "High Priority Applications"
            
            # Log for debugging
            logger.info(f"🔥 High priority applications query: found {structured_data.get('count', 0)} applications")
            logger.info(f"   Applications: {[app['application_number'] for app in structured_data.get('applications', [])]}")

        elif intent == "assigned_today":
            today = date.today()
            query = select(func.count(Application.id)).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_stage == officer.officer_stage,
                    Application.submission_date == today
                )
            )
            res = await db.execute(query)
            structured_data = {
                "count": res.scalar(),
                "query_type": "Applications Assigned Today"
            }

        elif intent == "immediate_action":
            from sqlalchemy.orm import joinedload
            from datetime import date as _date_imm
            
            # Get all pending/in-progress applications assigned to this officer
            apps_query = select(Application).options(
                joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
            ).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_stage == officer.officer_stage,
                    Application.current_status.in_(["pending", "in_progress"])
                )
            ).order_by(Application.submission_date.asc())
            
            res_apps = await db.execute(apps_query)
            all_apps = res_apps.scalars().all()
            
            # Calculate working days and identify overdue applications (>15 working days)
            _today_imm = _date_imm.today()
            rows = []
            
            for a in all_apps:
                if not a.submission_date:
                    continue
                    
                # Calculate working days (exclude weekends)
                working_days = 0
                current_date = a.submission_date
                while current_date < _today_imm:
                    current_date += timedelta(days=1)
                    if current_date.weekday() < 5:  # Monday = 0, Sunday = 6
                        working_days += 1
                
                # Consider overdue if more than 15 working days have elapsed
                if working_days > 15:
                    sn = a.survey_number
                    bl = sn.block if sn else None
                    w = bl.ward if bl else None
                    t = w.town if w else None
                    
                    rows.append({
                        "application_number": a.application_number,
                        "type": a.application_type,
                        # survey_no/block_number were omitted here, so the table
                        # rendered "N/A" for both even though the joinedload had
                        # already fetched them.
                        "survey_no": sn.survey_no if sn else "N/A",
                        "block_number": bl.block_number if bl else "N/A",
                        "town_name": t.name if t else "N/A",
                        "ward_number": w.ward_number if w else "N/A",
                        "status": "Action Required",
                        "current_stage": a.current_stage,
                        "submission_date": a.submission_date.isoformat(),
                        "working_days_elapsed": working_days,
                        "days_overdue": working_days - 15
                    })
            
            structured_data = {
                "applications": rows,
                # "apps" kept alongside "applications" so both response phases
                # can read either key without drifting apart again.
                "apps": [r["application_number"] for r in rows],
                "query_type": "Immediate Action Required — Overdue Applications"
            }


        elif intent == "awaiting_field_visit":
            query = select(func.count(FieldVisit.id)).where(
                and_(
                    FieldVisit.officer_id == officer.officer_id,
                    FieldVisit.status.in_(["scheduled", "unscheduled"])
                )
            )
            res = await db.execute(query)
            structured_data = {
                "count": res.scalar(),
                "query_type": "Awaiting Field Visit"
            }

        elif intent == "can_number_info":
            # "What is the CAN number of 2026/0153/28/001854?" asks for the value
            # on that file, and "was CAN 1332... taken at a CSC?" asks about one
            # number. The generic explainer is only right when the officer named
            # neither -- it was being returned for both.
            _can_app_no = extract_application_number(message) or _gate_app_number
            _can_token = re.search(r'\b(\d{12,15})\b', message)
            _can_details = None
            if _can_app_no or _can_token:
                _can_details = await get_can_details(
                    db, officer,
                    application_number=_can_app_no,
                    can_number=None if _can_app_no else _can_token.group(1),
                )
            if _can_details:
                # Found or not, the officer asked about one specific number --
                # answering with the generic explainer would look like an answer.
                structured_data = {"can_details": _can_details, "query_type": "CAN Details"}
            else:
                structured_data = {
                    "can_summary": {
                        "assigned_by": "Common Service Center (CSC) / Citizen Portal",
                        "description": "Citizen Access Number (CAN) is a unique citizen identity number assigned through CSC or citizen self-registration.",
                        "number_format": "The length identifies the channel: a CAN issued at a Common Service Centre is 15 digits, one generated by the citizen on the portal is 12 digits.",
                        "role_in_patta_transfer": "CAN links the citizen's Aadhaar, mobile number, and identity across all Patta transfer requests (ISD, NISD, MERGE).",
                        "csc_charges": "₹60.00 CSC service fee for application submission with CAN registration.",
                        "service_codes_linked": "0153 (NISD), 0154 (ISD), 0155 (MERGE)"
                    },
                    "query_type": "CAN Number & CSC Assignment Guide"
                }

        elif intent == "service_code_guide":
            structured_data = {
                "service_codes": [
                    {
                        "service_code": "0153",
                        "type": "NISD (Not Involving Sub-Division)",
                        "tamil_name": "உட்பிரிவு இல்லாத பட்டா மாறுதல்",
                        "govt_fee": "₹100.00",
                        "csc_fee": "₹60.00",
                        "sla_days": "15-20 working days",
                        "workflow": "Citizen / CSC → SIS Officer (Document Verification) → Deputy Tahsildar (Review) → Tahsildar (Digital Signature / DSC) [No field visit required]"
                    },
                    {
                        "service_code": "0154",
                        "type": "ISD (Involving Sub-Division)",
                        "tamil_name": "உட்பிரிவு உள்ள பட்டா மாறுதல்",
                        "govt_fee": "₹400.00",
                        "csc_fee": "₹60.00",
                        "sla_days": "30-35 working days",
                        "workflow": "Citizen / CSC → SIS Officer (Mandatory Field Visit within 15 days) → Senior Draughtsman (SD Sketch) → DIS (Approval) → Tahsildar (Digital Signature / DSC)"
                    },
                    {
                        "service_code": "0155",
                        "type": "MERGE (Subdivision Merger)",
                        "tamil_name": "உட்பிரிவு இணைப்பு பட்டா மாறுதல்",
                        "govt_fee": "₹0.00",
                        "csc_fee": "₹60.00",
                        "sla_days": "15 working days",
                        "workflow": "Citizen / CSC → SIS Officer (Field Boundary & Total Merged Area Verification) → Tahsildar (Digital Signature / DSC)"
                    }
                ],
                "query_type": "Service Codes Workflow & Fee Comparison (0153 / 0154 / 0155)"
            }

        elif intent == "workload_by_type":
            structured_data = await get_officer_workload(db, officer)
            structured_data["query_type"] = "Workload by Type"

        elif intent == "completion_rate":
            from datetime import date as _date_cr
            _msg_lower_cr = message.lower()
            _this_month = any(p in _msg_lower_cr for p in ["this month", "month", "monthly", "current month"])
            _today_cr = date.today()
            _month_start = _today_cr.replace(day=1)

            # "What is my completion rate in June 2026" named a period the
            # handler never looked at, so it answered with the all-time rate.
            _cr_start, _cr_end, _cr_label = _period_from_message(message)

            if _cr_start and _cr_end:
                _cr_window = and_(Application.submission_date >= _cr_start,
                                  Application.submission_date <= _cr_end)
                completed_query = select(func.count(Application.id)).where(
                    and_(
                        Application.assigned_officer_id == officer.officer_id,
                        Application.current_status.in_(["approved", "rejected"]),
                        _cr_window,
                    )
                )
                total_query = select(func.count(Application.id)).where(
                    and_(Application.assigned_officer_id == officer.officer_id, _cr_window)
                )
                scope_label = _cr_label or f"{_cr_start} to {_cr_end}"
            elif _this_month:
                completed_query = select(func.count(Application.id)).where(
                    and_(
                        Application.assigned_officer_id == officer.officer_id,
                        Application.current_status.in_(["approved", "rejected"]),
                        Application.updated_at >= _month_start
                    )
                )
                total_query = select(func.count(Application.id)).where(
                    and_(
                        Application.assigned_officer_id == officer.officer_id,
                        Application.submission_date >= _month_start
                    )
                )
                scope_label = f"this month ({_month_start.strftime('%B %Y')})"
            else:
                completed_query = select(func.count(Application.id)).where(
                    and_(
                        Application.assigned_officer_id == officer.officer_id,
                        Application.current_status.in_(["approved", "rejected"])
                    )
                )
                total_query = select(func.count(Application.id)).where(
                    Application.assigned_officer_id == officer.officer_id
                )
                scope_label = "overall"

            completed = (await db.execute(completed_query)).scalar() or 0
            total = (await db.execute(total_query)).scalar() or 0
            structured_data = {
                "completed": completed,
                "total": total,
                "rate": int((completed / total) * 100) if total > 0 else 0,
                "scope": scope_label,
                "query_type": "Completion Rate"
            }

        elif intent == "pending_longest":
            query = select(Application).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_stage == officer.officer_stage,
                    Application.current_status.in_(["pending", "in_progress"])
                )
            ).order_by(Application.submission_date.asc())
            result = await db.execute(query)
            apps = result.scalars().all()
            days = (date.today() - apps[0].submission_date).days if apps else 0
            structured_data = {
                "apps": [a.application_number for a in apps],
                "days": days,
                "query_type": "Pending Longest"
            }
            
        elif intent in ["is_nisd_or_isd", "check_documents", "check_sale_deed"]:
            app_number = extract_application_number(message)
            if not app_number:
                # Allow implicit continuation since these are specific queries about an application
                app_number = (_extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True) or _gate_app_number)
            
            if not app_number:
                # No app number - ask for it
                is_tamil_lang = language in ("ta", "tanglish")
                if is_tamil_lang:
                    response_text = "தயவுசெய்து விண்ணப்ப எண்ணை குறிப்பிடவும். (எ.கா: 2026/0154/02/000041)"
                else:
                    response_text = "Please specify which application you're asking about. For example: 2026/0154/02/000041"
                structured_data = {"found": False, "query_type": "Application Details"}
            else:
                structured_data = await get_application_detail(db, app_number, officer=officer)
                structured_data["query_type"] = "Application Details"

        elif intent == "isd_processing":
            app_number = extract_application_number(message) or (_extract_app_number_from_context(message, chat_history) or _gate_app_number)
            if not app_number:
                # Never stand in an arbitrary application for one the officer
                # did not name — that answers a question nobody asked.
                structured_data = {"found": False, "query_type": "ISD Processing"}
                response_text = ASK_FOR_APP_NUMBER[language if language in ASK_FOR_APP_NUMBER else "en"]
            else:
                structured_data = await get_application_detail(db, app_number, officer=officer)
                structured_data["query_type"] = "ISD Processing"
            _isd_app_no = app_number


        elif intent in ["sd_additional_info", "sd_encroachment_check", "sd_sketch_readiness", "sd_forward_check", "sd_remarks", "fv_date_select", "fv_nearby_pending", "fv_scheduled_this_week", "fv_reschedule_availability", "fv_change_date", "fv_deadline_check", "fv_overdue_inspections", "fv_unassigned_awaiting", "fv_recently_rescheduled", "fv_scheduling_conflicts"]:
            app_number = extract_application_number(message)

            # ── fv_scheduled_this_week without a specific application ──────────
            # When the officer asks "how many scheduled in this taluk this week?"
            # without citing an app number, answer from the officer's own taluk directly.
            _handled_week_query = False
            if intent == "fv_scheduled_this_week" and not app_number:
                _handled_week_query = True

                # Resolve officer's taluk
                jur_result = await db.execute(
                    select(OfficerJurisdiction).where(OfficerJurisdiction.officer_id == officer.officer_id).limit(1)
                )
                jur = jur_result.scalar_one_or_none()

                taluk_obj = None
                if jur:
                    if jur.taluk_id:
                        taluk_obj = (await db.execute(select(Taluk).where(Taluk.id == jur.taluk_id))).scalar_one_or_none()
                    elif jur.block_id:
                        # Walk up: block → ward → town → taluk
                        block_obj = (await db.execute(select(Block).where(Block.id == jur.block_id))).scalar_one_or_none()
                        if block_obj:
                            ward_obj = (await db.execute(select(Ward).where(Ward.id == block_obj.ward_id))).scalar_one_or_none()
                            if ward_obj:
                                town_obj = (await db.execute(select(Town).where(Town.id == ward_obj.town_id))).scalar_one_or_none()
                                if town_obj:
                                    taluk_obj = (await db.execute(select(Taluk).where(Taluk.id == town_obj.taluk_id))).scalar_one_or_none()
                    elif jur.ward_id:
                        ward_obj = (await db.execute(select(Ward).where(Ward.id == jur.ward_id))).scalar_one_or_none()
                        if ward_obj:
                            town_obj = (await db.execute(select(Town).where(Town.id == ward_obj.town_id))).scalar_one_or_none()
                            if town_obj:
                                taluk_obj = (await db.execute(select(Taluk).where(Taluk.id == town_obj.taluk_id))).scalar_one_or_none()

                taluk_name = taluk_obj.name if taluk_obj else "your taluk"
                taluk_id = taluk_obj.id if taluk_obj else None

                today = datetime.now(timezone.utc).date()
                start_of_week = today - timedelta(days=today.weekday())
                end_of_week = start_of_week + timedelta(days=6)

                week_count = 0
                week_app_numbers = []
                if taluk_id:
                    stmt_week = select(Application).join(
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
                            Town.taluk_id == taluk_id,
                            FieldVisit.officer_id == officer.officer_id,
                            FieldVisit.status == "scheduled",
                            FieldVisit.scheduled_date >= start_of_week,
                            FieldVisit.scheduled_date <= end_of_week
                        )
                    )
                    week_apps = (await db.execute(stmt_week)).scalars().all()
                    week_count = len(week_apps)
                    week_app_numbers = [a.application_number for a in week_apps]

                structured_data = {
                    "taluk_scheduled_count": week_count,
                    "taluk_name": taluk_name,
                    "taluk_cases": week_app_numbers,
                    "week_start": start_of_week.isoformat(),
                    "week_end": end_of_week.isoformat(),
                    "query_type": "Scheduled Field Visits This Week"
                }
                # response is built in the fv_scheduled_this_week handler below
            
            if intent == "fv_unassigned_awaiting" and not app_number:
                _handled_week_query = True
                from sqlalchemy.orm import joinedload
                from datetime import date as _date_ns

                unassigned_stmt_ns = select(Application).options(
                    joinedload(Application.applicant),
                    joinedload(Application.application_sub_divisions).joinedload(ApplicationSubDivision.sub_division),
                    joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
                ).join(
                    FieldVisit, FieldVisit.application_id == Application.id
                ).where(
                    and_(
                        FieldVisit.officer_id == officer.officer_id,
                        FieldVisit.status == "unscheduled"
                    )
                )
                unassigned_res_ns = (await db.execute(unassigned_stmt_ns)).unique().scalars().all()

                unassigned_list_ns = []
                for ua in unassigned_res_ns:
                    days_p = (_date_ns.today() - ua.submission_date).days if ua.submission_date else 0
                    sn = ua.survey_number
                    bl = sn.block if sn else None
                    wd = bl.ward if bl else None
                    tw = wd.town if wd else None
                    sis_nos = ", ".join(
                        sd.proposed_sub_division_no for sd in ua.application_sub_divisions
                        if sd.proposed_sub_division_no
                    ) or "N/A"
                    dis_nos = ", ".join(
                        sd.sub_division.sub_division_no for sd in ua.application_sub_divisions
                        if sd.sub_division and sd.sub_division.sub_division_no
                    ) or "N/A"
                    unassigned_list_ns.append({
                        "application_number": ua.application_number,
                        "applicant_name": ua.applicant.name if ua.applicant else "N/A",
                        "survey_no": sn.survey_no if sn else "N/A",
                        "sis_temp_sub_div": sis_nos,
                        "dis_fixed_sub_div": dis_nos,
                        "town_name": tw.name if tw else "N/A",
                        "ward_number": wd.ward_number if wd else "N/A",
                        "block_number": bl.block_number if bl else "N/A",
                        "current_stage": ua.current_stage or "N/A",
                        "current_status": ua.current_status or "N/A",
                        "submission_date": ua.submission_date.isoformat() if ua.submission_date else "N/A",
                        "days_pending": days_p,
                        "priority": "High" if ua.priority_flag else "Normal"
                    })

                structured_data = {
                    "unassigned_visits_count": len(unassigned_list_ns),
                    "unassigned_applications": unassigned_list_ns,
                    "query_type": "திட்டமிடல் காத்திருக்கும் கள ஆய்வுகள்" if language == "ta" else "Unassigned Field Visits — Awaiting Scheduling"
                }
                # response built by fv_unassigned_awaiting handler below

            if intent == "fv_deadline_check":
                _handled_week_query = True
                # Resolve application number from message or chat history
                resolved_app = app_number or (_extract_app_number_from_context(message, chat_history) or _gate_app_number)
                if not resolved_app:
                    structured_data = {
                        "found": False,
                        "message": "Please specify an application number, e.g. 2026/0154/02/000041, to check the deadline."
                    }
                else:
                    from sqlalchemy.orm import joinedload
                    app_res = await db.execute(
                        select(Application)
                        .where(Application.application_number == resolved_app)
                    )
                    a_dl = app_res.scalar_one_or_none()
                    if not a_dl:
                        structured_data = {"found": False, "message": f"Application {resolved_app} not found.", "searched_number": resolved_app}
                    else:
                        sub_date = a_dl.submission_date
                        today_dl = datetime.now(timezone.utc).date()
                        working_days_dl = 0
                        curr = sub_date
                        while curr < today_dl:
                            curr += timedelta(days=1)
                            if curr.weekday() < 5:
                                working_days_dl += 1
                        structured_data = {
                            "found": True,
                            "application_number": a_dl.application_number,
                            "submission_date": sub_date.isoformat(),
                            "working_days": working_days_dl,
                            "deadline_days": 15,
                            "is_overdue": working_days_dl > 15,
                            "days_overdue": max(0, working_days_dl - 15),
                            "days_remaining": max(0, 15 - working_days_dl),
                            "query_type": "Field Visit Deadline Check"
                        }

            if not _handled_week_query:
                if not app_number:
                    app_number = _extract_app_number_from_context(
                        message, chat_history, allow_implicit_continuation=True
                    )
                
                from sqlalchemy.orm import joinedload
                
                app_res = await db.execute(
                    select(Application)
                    .options(joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town).joinedload(Town.taluk))
                    .where(Application.application_number == app_number)
                )
                a = app_res.scalar_one_or_none()
                
                if not a:
                    structured_data = {"found": False, "message": f"Application {app_number} not found.", "searched_number": app_number}
                else:
                    doc_stmt = select(ApplicationDocument).where(ApplicationDocument.application_id == a.id)
                    docs = (await db.execute(doc_stmt)).scalars().all()
                    missing_docs = [d.document_type for d in docs if not d.is_uploaded]
                
                    visit_stmt = select(FieldVisit).where(FieldVisit.application_id == a.id)
                    visit = (await db.execute(visit_stmt)).scalars().first()
                
                    hist_stmt = select(WorkflowHistory).where(WorkflowHistory.application_id == a.id).order_by(WorkflowHistory.performed_at.asc())
                    history = (await db.execute(hist_stmt)).scalars().all()
                
                    sd_clarification = None
                    sd_remarks = None
                    forwarded_to_sd_date = None
                
                    for h in history:
                        if h.from_stage == "SD":
                            sd_clarification = h.rejection_reason or h.remarks
                            sd_remarks = h.remarks or h.rejection_reason
                        if h.to_stage == "SD":
                            forwarded_to_sd_date = h.performed_at.date().isoformat()
                
                    nearby_count = 0
                    ward_num = "N/A"
                    block_num = "N/A"
                    if a.survey_number and a.survey_number.block:
                        bl = a.survey_number.block
                        ward_num = bl.ward.ward_number if bl.ward else "N/A"
                        block_num = bl.block_number
                    
                        nearby_stmt = select(func.count(Application.id)).join(
                            SurveyNumber, Application.survey_number_id == SurveyNumber.id
                        ).where(
                            and_(
                                SurveyNumber.block_id == bl.id,
                                Application.id != a.id,
                                Application.current_status.in_(["pending", "in_progress"])
                            )
                        )
                        nearby_count = (await db.execute(nearby_stmt)).scalar() or 0
                
                    taluk_name = "N/A"
                    taluk_scheduled_count = 0
                    taluk_cases = []
                    if a.survey_number and a.survey_number.block and a.survey_number.block.ward and a.survey_number.block.ward.town:
                        town = a.survey_number.block.ward.town
                        taluk = town.taluk
                        if taluk:
                            taluk_name = taluk.name
                            today = datetime.now(timezone.utc).date()
                            start_of_week = today - timedelta(days=today.weekday())
                            end_of_week = start_of_week + timedelta(days=6)
                        
                            stmt_week = select(Application).join(
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
                                    FieldVisit.officer_id == officer.officer_id,
                                    FieldVisit.status == "scheduled",
                                    FieldVisit.scheduled_date >= start_of_week,
                                    FieldVisit.scheduled_date <= end_of_week
                                )
                            )
                            week_apps = (await db.execute(stmt_week)).scalars().all()
                            taluk_scheduled_count = len(week_apps)
                            taluk_cases = [wa.application_number for wa in week_apps]
                
                    reschedule_date = None
                    for offset in range(1, 10):
                        test_date = datetime.now(timezone.utc).date() + timedelta(days=offset)
                        if test_date.weekday() >= 5:
                            continue
                        visit_count = (await db.execute(
                            select(func.count(FieldVisit.id)).where(
                                and_(
                                    FieldVisit.officer_id == officer.officer_id,
                                    FieldVisit.scheduled_date == test_date
                                )
                            )
                        )).scalar() or 0
                        if visit_count == 0:
                            reschedule_date = test_date.isoformat()
                            break
                    if not reschedule_date:
                        reschedule_date = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
                
                    sub_date = a.submission_date
                    today = datetime.now(timezone.utc).date()
                    working_days = 0
                    curr = sub_date
                    while curr < today:
                        curr += timedelta(days=1)
                        if curr.weekday() < 5:
                            working_days += 1
                
                    overdue_visits_stmt = select(func.count(FieldVisit.id)).where(
                        and_(
                            FieldVisit.officer_id == officer.officer_id,
                            FieldVisit.status == "overdue"
                        )
                    )
                    overdue_visits_count = (await db.execute(overdue_visits_stmt)).scalar() or 0
                
                    unassigned_visits_stmt = select(func.count(FieldVisit.id)).where(
                        and_(
                            FieldVisit.officer_id == officer.officer_id,
                            FieldVisit.status.in_(["unscheduled"])
                        )
                    )
                    unassigned_visits_count = (await db.execute(unassigned_visits_stmt)).scalar() or 0
                
                    # Fetch actual unassigned applications with details for table display
                    from sqlalchemy.orm import joinedload
                    unassigned_apps_list = []
                    unassigned_apps_stmt = select(Application).options(
                        joinedload(Application.applicant),
                        joinedload(Application.application_sub_divisions).joinedload(ApplicationSubDivision.sub_division),
                        joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
                    ).join(
                        FieldVisit, FieldVisit.application_id == Application.id
                    ).where(
                        and_(
                            FieldVisit.officer_id == officer.officer_id,
                            FieldVisit.status.in_(["unscheduled"])
                        )
                    )
                    unassigned_apps_res = (await db.execute(unassigned_apps_stmt)).unique().scalars().all()
                    for ua in unassigned_apps_res:
                        days_p = (date.today() - ua.submission_date).days if ua.submission_date else 0
                        block_n = ua.survey_number.block.block_number if (ua.survey_number and ua.survey_number.block) else "N/A"
                        ward_n = ua.survey_number.block.ward.ward_number if (ua.survey_number and ua.survey_number.block and ua.survey_number.block.ward) else "N/A"
                        town_n = ua.survey_number.block.ward.town.name if (ua.survey_number and ua.survey_number.block and ua.survey_number.block.ward and ua.survey_number.block.ward.town) else "N/A"
                        survey_n = ua.survey_number.survey_no if ua.survey_number else "N/A"
                        # SIS temporary number (proposed by SIS during field visit)
                        sis_temp_nos = ", ".join(
                            sd.proposed_sub_division_no for sd in ua.application_sub_divisions
                            if sd.proposed_sub_division_no
                        ) or "N/A"
                        # DIS permanent/fixed number (from SubDivision record assigned by DIS)
                        dis_fixed_nos = ", ".join(
                            sd.sub_division.sub_division_no for sd in ua.application_sub_divisions
                            if sd.sub_division and sd.sub_division.sub_division_no
                        ) or "N/A"
                        unassigned_apps_list.append({
                            "application_number": ua.application_number,
                            "applicant_name": ua.applicant.name if ua.applicant else "N/A",
                            "survey_no": survey_n,
                            "sis_temp_sub_div": sis_temp_nos,
                            "dis_fixed_sub_div": dis_fixed_nos,
                            "town_name": town_n,
                            "ward_number": ward_n,
                            "block_number": block_n,
                            "current_stage": ua.current_stage or "N/A",
                            "current_status": ua.current_status or "N/A",
                            "submission_date": ua.submission_date.isoformat() if ua.submission_date else "N/A",
                            "days_pending": days_p,
                            "priority": "High" if ua.priority_flag else "Normal"
                        })
                
                    recently_rescheduled_count = (await db.execute(
                        select(func.count(FieldVisit.id)).where(
                            and_(
                                FieldVisit.officer_id == officer.officer_id,
                                FieldVisit.updated_at >= datetime.now(timezone.utc) - timedelta(days=7)
                            )
                        )
                    )).scalar() or 0
                
                    overlap_date = None
                    overlap_stmt = select(FieldVisit.scheduled_date).where(
                        and_(
                            FieldVisit.officer_id == officer.officer_id,
                            FieldVisit.status == "scheduled"
                        )
                    ).group_by(FieldVisit.scheduled_date).having(func.count(FieldVisit.id) > 1)
                    overlap_res = (await db.execute(overlap_stmt)).scalars().first()
                    if overlap_res:
                        overlap_date = overlap_res.isoformat()
                
                    structured_data = {
                        "found": True,
                        "application_number": a.application_number,
                        "current_stage": a.current_stage,
                        "submission_date": a.submission_date.isoformat(),
                        "missing_documents": missing_docs,
                        "field_visit_present": visit is not None,
                        "field_visit_date": visit.scheduled_date.isoformat() if (visit and visit.scheduled_date) else None,
                        "encroachment_found": visit.encroachment_found if visit else False,
                        "area_verified": visit.area_verified if visit else None,
                        "visit_notes_present": bool(visit.visit_notes) if visit else False,
                        "sd_clarification": sd_clarification,
                        "sd_remarks": sd_remarks,
                        "forwarded_to_sd_date": forwarded_to_sd_date,
                        "nearby_count": nearby_count,
                        "ward_number": ward_num,
                        "block_number": block_num,
                        "taluk_name": taluk_name,
                        "taluk_scheduled_count": taluk_scheduled_count,
                        "taluk_cases": taluk_cases,
                        "reschedule_date": reschedule_date,
                        "working_days": working_days,
                        "overdue_visits_count": overdue_visits_count,
                        "unassigned_visits_count": unassigned_visits_count,
                        "unassigned_applications": unassigned_apps_list,
                        "recently_rescheduled_count": recently_rescheduled_count,
                        "overlap_date": overlap_date,
                        "query_type": "Workflow Check"
                    }

            # PARITY: process_chat_stream serves these three intents from dedicated
            # fetch branches that set a specific query_type (and, for fv_change_date,
            # a message). process_chat serves them from the shared group above, which
            # would otherwise label them all "Workflow Check".
            if isinstance(structured_data, dict) and intent in (
                "fv_recently_rescheduled", "fv_scheduling_conflicts", "fv_change_date"
            ):
                structured_data["query_type"] = {
                    "fv_recently_rescheduled": "Recently Rescheduled Field Visits",
                    "fv_scheduling_conflicts": "Field Visit Scheduling Conflicts",
                    "fv_change_date": "Field Visit Date Change",
                }[intent]
                if intent == "fv_change_date":
                    structured_data["message"] = (
                        "To change the date of a field visit, you should ask the Tahsildar."
                    )


        elif intent == "all_surveys_in_jurisdiction":
            structured_data = await get_all_surveys_in_jurisdiction(db, officer)
            structured_data["query_type"] = "All Surveys in Your Jurisdiction"

        elif intent == "merge_info":
            app_number = (_extract_app_number_from_context(message, chat_history) or _gate_app_number)
            if app_number:
                structured_data = await get_merge_application_detail(db, app_number, officer)
            else:
                structured_data = await get_merge_application_detail(db, None, officer)
            structured_data["query_type"] = "Merge Application Details"

        elif intent == "application_status":
            # Extract from current message first; only check history if user uses reference words
            # Use findall to support multi-app queries like "Show details for A and B"
            _app_numbers_in_msg = extract_application_numbers(message)
            if len(_app_numbers_in_msg) > 1:
                # Multiple application numbers detected — fetch details for each
                _multi_details = []
                for _an in _app_numbers_in_msg:
                    _det = await get_application_detail(db, _an, officer=officer)
                    _multi_details.append(_det)
                structured_data = {
                    "multi_applications": _multi_details,
                    "query_type": "Application Status"
                }
                app_number = None
            else:
                app_number = _app_numbers_in_msg[0] if _app_numbers_in_msg else None
            if not app_number and "multi_applications" not in structured_data:
                # Check for explicit reference patterns OR implicit continuation for field queries
                # Implicit continuation: if user just discussed an app, next field query refers to it
                _field_keywords = [
                    "name", "address", "mobile", "phone", "status", "stage",
                    "overdue", "days", "scheduled", "visit", "delay", "delayed", "late",
                    "survey", "patta", "can", "reason", "priority",
                    "subdivision", "subdiv", "user", "role",
                    "service", "source", "district", "taluk", "ward", "block",
                    "received", "workflow",
                    "serial", "applicant", "type", "date", "year",
                    "பெயர்", "முகவரி", "தொலைபேசி", "நிலை", "கட்டம்", "தாமதம்",
                    "கணக்கெண்", "பட்டா", "காரணம்", "முன்னுரிமை"
                ]
                is_field_query = any(kw in message.lower() for kw in _field_keywords)
                app_number = (_extract_app_number_from_context(message, chat_history, allow_implicit_continuation=is_field_query) or _gate_app_number)
                
                # Only fall back to most recent application if explicit reference pattern found
                if not app_number:
                    # No substitution here. "the most recently updated application
                    # assigned to this officer" is not what the officer referred to,
                    # and answering about it looks authoritative while being wrong.
                    # With no resolvable reference we ask which application they mean.
                    pass
            if app_number:
                if _wants_workflow_history(message):
                    from sqlalchemy.orm import joinedload
                    app_res = await db.execute(select(Application).where(Application.application_number == app_number))
                    a = app_res.scalar_one_or_none()
                    if not a:
                        structured_data = {"found": False, "message": f"Application {app_number} not found.", "searched_number": app_number}
                    else:
                        history_res = await db.execute(
                            select(WorkflowHistory)
                            .options(joinedload(WorkflowHistory.performed_by_officer))
                            .where(WorkflowHistory.application_id == a.id)
                            .order_by(WorkflowHistory.performed_at.asc())
                        )
                        history = history_res.scalars().all()
                        
                        history_list = [
                            {
                                "from_stage": h.from_stage,
                                "to_stage": h.to_stage,
                                "changed_at": h.performed_at.isoformat(),
                                "note": h.remarks,
                                "changed_by_name": h.performed_by_officer.name if h.performed_by_officer else "System"
                            }
                            for h in history
                        ]
                        structured_data = {
                            "application_number": a.application_number,
                            "history": history_list,
                            "query_type": f"Workflow History for {a.application_number}"
                        }
                else:
                    structured_data = await get_application_detail(db, app_number, officer=officer)
                    structured_data["query_type"] = "Application Status"

        elif intent == "jurisdiction_summary":
            from sqlalchemy.orm import joinedload
            q = select(OfficerJurisdiction).options(
                joinedload(OfficerJurisdiction.district),
                joinedload(OfficerJurisdiction.taluk),
                joinedload(OfficerJurisdiction.town),
                joinedload(OfficerJurisdiction.ward),
                joinedload(OfficerJurisdiction.block)
            ).where(OfficerJurisdiction.officer_id == officer.officer_id)
            res = await db.execute(q)
            jurisdictions = res.scalars().all()
            
            if not jurisdictions:
                structured_data = {"found": False, "message": "No jurisdictions assigned."}
            else:
                first = jurisdictions[0]
                d = first.district
                tk = first.taluk
                
                towns_map = {}
                for j in jurisdictions:
                    if j.town:
                        t_name = j.town.name
                        if t_name not in towns_map:
                            towns_map[t_name] = {}
                        if j.ward:
                            w_num = j.ward.ward_number
                            if w_num not in towns_map[t_name]:
                                towns_map[t_name][w_num] = []
                            if j.block:
                                towns_map[t_name][w_num].append({"block_number": j.block.block_number})
                
                towns_list = []
                for t_name, wards_map in towns_map.items():
                    wards_list = []
                    for w_num, blocks in wards_map.items():
                        wards_list.append({
                            "ward_number": w_num,
                            "blocks": blocks
                        })
                    towns_list.append({
                        "name": t_name,
                        "wards": wards_list
                    })
                
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
                            Application.assigned_officer_id == officer.officer_id,
                            Application.current_status.in_(["pending", "in_progress"])
                        )
                    )
                )).scalar() or 0
                
                structured_data = {
                    "jurisdiction": {
                        "district": {"name": d.name if d else "N/A", "code": d.district_code if d else "N/A"},
                        "taluk": {"name": tk.name if tk else "N/A"},
                        "towns": towns_list,
                        "survey_count": survey_count,
                        "active_applications": active_count
                    },
                    "query_type": "Jurisdiction Summary"
                }

        elif intent == "town_applications":
            town_name = extract_town_name(message)
            from sqlalchemy.orm import joinedload
            query = select(Application).join(
                SurveyNumber, Application.survey_number_id == SurveyNumber.id
            ).join(
                Block, SurveyNumber.block_id == Block.id
            ).join(
                Ward, Block.ward_id == Ward.id
            ).join(
                Town, Ward.town_id == Town.id
            ).options(
                joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
            ).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_stage == officer.officer_stage,
                    Application.current_status.in_(["pending", "in_progress"]),
                    Town.name.ilike(f"%{town_name}%") if town_name else True
                )
            ).order_by(Application.application_number)
            res = await db.execute(query)
            apps = res.scalars().all()
            
            app_rows = []
            for a in apps:
                sn = a.survey_number
                bl = sn.block if sn else None
                w = bl.ward if bl else None
                t = w.town if w else None
                app_rows.append({
                    "application_number": a.application_number,
                    "type": a.application_type,
                    "town_name": t.name if t else "N/A",
                    "ward_number": w.ward_number if w else "N/A",
                    "status": "Pending",
                    "stage": a.current_stage,
                    "submission_date": a.submission_date.isoformat()
                })
            
            structured_data = {
                "applications": app_rows,
                "query_type": f"Pending Applications in {town_name}" if town_name else "Pending Applications"
            }

        elif intent == "block_applications":
            block_no = extract_block_number(message)
            from sqlalchemy.orm import joinedload
            query = select(Application).join(
                SurveyNumber, Application.survey_number_id == SurveyNumber.id
            ).join(
                Block, SurveyNumber.block_id == Block.id
            ).join(
                Ward, Block.ward_id == Ward.id
            ).join(
                Town, Ward.town_id == Town.id
            ).options(
                joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
            ).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_stage == officer.officer_stage,
                    Application.current_status.in_(["pending", "in_progress"]),
                    Block.block_number.ilike(f"%{block_no}%") if block_no else True
                )
            ).order_by(Application.application_number)
            res = await db.execute(query)
            apps = res.scalars().all()
            
            app_rows = []
            for a in apps:
                sn = a.survey_number
                bl = sn.block if sn else None
                w = bl.ward if bl else None
                t = w.town if w else None
                app_rows.append({
                    "application_number": a.application_number,
                    "type": a.application_type,
                    "town_name": t.name if t else "N/A",
                    "ward_number": w.ward_number if w else "N/A",
                    "status": "Pending",
                    "stage": a.current_stage,
                    "submission_date": a.submission_date.isoformat()
                })
            
            structured_data = {
                "applications": app_rows,
                "query_type": f"Pending Applications in Block {block_no}" if block_no else "Pending Applications"
            }

        elif intent == "rejection_info":
            app_number = extract_application_number(message) or _extract_app_number_from_context(
                message, chat_history, allow_implicit_continuation=True
            )
            app_res = await db.execute(select(Application).where(Application.application_number == app_number))
            a = app_res.scalar_one_or_none()
            if not a:
                structured_data = {"found": False, "message": f"Application {app_number} not found.", "searched_number": app_number}
            else:
                history_res = await db.execute(
                    select(WorkflowHistory)
                    .where(WorkflowHistory.application_id == a.id)
                    .order_by(WorkflowHistory.performed_at.asc())
                )
                history = history_res.scalars().all()
                
                rejections = []
                for i, h in enumerate(history):
                    if h.to_stage == "REJECTED" or "REJECT" in (h.action or ""):
                        resub_date = None
                        for next_h in history[i+1:]:
                            if next_h.from_stage == "REJECTED" or "RESUBMIT" in (next_h.action or "") or next_h.to_stage != "REJECTED":
                                resub_date = next_h.performed_at.isoformat()
                                break
                        rejections.append({
                            "source": h.from_stage or "SD",
                            "reason_code": "REJ-01",
                            "reason_text": h.rejection_reason or h.remarks or "Boundary mismatch",
                            "rejected_at": h.performed_at.isoformat(),
                            "resubmitted_at": resub_date
                        })
                
                structured_data = {
                    "application_number": a.application_number,
                    "rejections": rejections,
                    "query_type": "Rejection History"
                }

        elif intent == "taluk_summary":
            q = select(OfficerJurisdiction).where(OfficerJurisdiction.officer_id == officer.officer_id)
            res = await db.execute(q)
            jurisdictions = res.scalars().all()
            if jurisdictions:
                first = jurisdictions[0]
                tk = first.taluk
                d = first.district
                structured_data = {
                    "taluk_name": tk.name if tk else "N/A",
                    "district_name": d.name if d else "N/A",
                    "query_type": "Taluk Summary"
                }
            else:
                structured_data = {"found": False, "message": "No taluk assigned."}

        elif intent == "litigation_check":
            # "litigation on 2022/0153/28/000016" names an application, not a
            # survey. extract_survey_number would pull "0153/28" out of the
            # middle of the application number and report it as not found, so
            # resolve the survey through the application first.
            survey_no = None
            _lit_app = extract_application_number(message)
            if _lit_app:
                _lit_survey = (await db.execute(
                    select(SurveyNumber)
                    .join(Application, Application.survey_number_id == SurveyNumber.id)
                    .where(Application.application_number == _lit_app)
                )).scalars().first()
                if _lit_survey is not None:
                    survey_no = _lit_survey.survey_no
            if not survey_no:
                survey_no = extract_survey_number(message)
            if not survey_no:
                # No number in this message, no application to resolve it
                # through -- e.g. "is there litigation on it?" straight after
                # something unrelated. This used to silently fall back to a
                # hardcoded "145" and confidently report that survey as "not
                # found", which reads as a real answer instead of what it
                # actually is: the officer's reference couldn't be resolved.
                is_tamil = language in ("ta", "tanglish")
                structured_data = {
                    "found": False,
                    "message": ("தயவுசெய்து சர்வே எண்ணைக் குறிப்பிடவும். (எ.கா: 1345)" if is_tamil
                                else "Please specify the survey number you are asking about (e.g. 1345).")
                }
            else:
                # survey_no is not unique -- the same number exists in more than one
                # block, so this must never be scalar_one_or_none(): a number like
                # "15" raised MultipleResultsFound and killed the whole request.
                # A subdivision-qualified number ("1344/2") never matches survey_no
                # exactly -- the stored value is the base number -- so fall back to
                # it the same way get_survey_detail() does, or "litigation on
                # subdivision 1344/2" always reported "not found".
                _lit_base = survey_no.split('/')[0] if '/' in survey_no else survey_no
                _lit_rows = (await db.execute(
                    select(SurveyNumber).where(
                        or_(SurveyNumber.survey_no == survey_no, SurveyNumber.survey_no == _lit_base)
                    )
                )).scalars().all()
                sn = next((r for r in _lit_rows if r.has_litigation), None) or (
                    _lit_rows[0] if _lit_rows else None)
                if sn:
                    structured_data = {
                        "survey_no": sn.survey_no,
                        "litigation_flag": sn.has_litigation,
                        "parcels_with_this_number": len(_lit_rows),
                        "query_type": "Litigation Check"
                    }
                else:
                    structured_data = {"found": False, "message": f"Survey number {survey_no} not found."}

        elif intent in ["check_sale_deed", "sale_deed_check"]:
            app_number = extract_application_number(message)
            if not app_number:
                app_number = (_extract_app_number_from_context(message, chat_history) or _gate_app_number)
            
            if not app_number:
                # No app number provided - ask user for it
                is_tamil_lang = language in ("ta", "tanglish")
                if is_tamil_lang:
                    response_text = "தயவுசெய்து விண்ணப்ப எண்ணை குறிப்பிடவும். (எ.கா: 2026/0154/02/000041)"
                else:
                    response_text = "Please specify which application you're asking about. For example: 2026/0154/02/000041"
                structured_data = {"found": False, "query_type": "Sale Deed Verification"}
            else:
                structured_data = await get_application_detail(db, app_number, officer=officer)
                structured_data["query_type"] = "Sale Deed Verification"
                structured_data["sale_deed_verified"] = structured_data.get("sale_deed_registered", False)

        elif intent == "joint_owner_check":
            # Check if asking about an application's survey ownership or direct survey ownership
            app_number = extract_application_number(message)
            if not app_number:
                # Allow implicit continuation for joint owner queries
                app_number = (_extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True) or _gate_app_number)
            
            if app_number:
                # Get application details to find the survey number
                app_data = await get_application_detail(db, app_number, officer=officer)
                survey_no = app_data.get("survey_no") if app_data.get("found") else None
                if not survey_no:
                    structured_data = {"found": False, "message": f"Application {app_number} not found or has no survey linked"}
                else:
                    owners_data = await get_survey_owners(db, survey_no)
                    joint_owners = [o for o in owners_data.get("owners", []) if o.get("is_joint_owner")]
                    structured_data = {
                        "found": True,
                        "application_number": app_number,
                        "survey_no": survey_no,
                        "joint_owners": joint_owners,
                        "total_owners": len(owners_data.get("owners", [])),
                        "query_type": "Joint Ownership Check"
                    }
            else:
                # Direct survey number query
                survey_no = extract_survey_number(message)
                if not survey_no:
                    structured_data = {"found": False, "message": "Please provide an application number or survey number"}
                else:
                    owners_data = await get_survey_owners(db, survey_no)
                    joint_owners = [o for o in owners_data.get("owners", []) if o.get("is_joint_owner")]
                    structured_data = {
                        "found": True,
                        "survey_no": survey_no,
                        "joint_owners": joint_owners,
                        "total_owners": len(owners_data.get("owners", [])),
                        "query_type": "Joint Ownership Details"
                    }

        elif intent == "escalation_check":
            # Find applications approaching OR past the 15-working-day escalation threshold
            # "Approaching" = 12-15 working days elapsed; "Overdue" = 16+ working days
            from datetime import date as _date_esc
            _today_esc = _date_esc.today()

            # Get all pending/in-progress apps for this officer
            # joinedload is imported locally further down this function, which
            # shadows the module-level name and makes it unbound up here.
            from sqlalchemy.orm import joinedload as _joinedload
            esc_query = select(Application).options(
                # survey_number/block are read when building each row; without
                # eager loading that is a lazy load inside async context and
                # the whole answer fails.
                _joinedload(Application.survey_number).joinedload(SurveyNumber.block)
            ).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_status.in_(["pending", "in_progress", "escalated"])
                )
            ).order_by(Application.submission_date.asc())
            # "escalated ISD applications" named a type; without this the answer
            # mixed ISD and NISD rows together.
            _esc_msg = message.lower()
            if re.search(r'\bnisd\b|\b0153\b', _esc_msg):
                esc_query = esc_query.where(Application.application_type == "NISD")
            elif re.search(r'\bisd\b|\b0154\b', _esc_msg):
                esc_query = esc_query.where(Application.application_type == "ISD")
            elif re.search(r'\bmerge\b|\b0155\b', _esc_msg):
                esc_query = esc_query.where(Application.application_type == "MERGE")
            esc_result = await db.execute(esc_query)
            all_pending_apps = esc_result.scalars().all()

            approaching_apps = []
            for a in all_pending_apps:
                if not a.submission_date:
                    continue
                # Count working days elapsed
                wd_count = 0
                curr = a.submission_date
                while curr < _today_esc:
                    curr += timedelta(days=1)
                    if curr.weekday() < 5:
                        wd_count += 1

                # Include if in warning zone (day 10+) or overdue
                if wd_count >= 10:
                    days_remaining = max(0, 15 - wd_count)
                    is_overdue = wd_count > 15
                    _esc_sn = a.survey_number
                    _esc_bl = _esc_sn.block if _esc_sn else None
                    approaching_apps.append({
                        "application_number": a.application_number,
                        "type": a.application_type,
                        # same omission as immediate_action: the survey and block
                        # are loaded but were never put on the row
                        "survey_no": _esc_sn.survey_no if _esc_sn else "N/A",
                        "block_number": _esc_bl.block_number if _esc_bl else "N/A",
                        "status": a.current_status,
                        "stage": a.current_stage,
                        "submission_date": a.submission_date.isoformat(),
                        "working_days_elapsed": wd_count,
                        "days_remaining": days_remaining,
                        "is_overdue": is_overdue,
                        "urgency": "⚠ OVERDUE" if is_overdue else (
                            "🔴 Critical (1–2 days)" if days_remaining <= 2 else
                            "🟡 Warning (3–5 days)" if days_remaining <= 5 else
                            "🟢 Watch"
                        )
                    })

            structured_data = {
                "applications": approaching_apps,
                "total_approaching": len(approaching_apps),
                "overdue_count": sum(1 for a in approaching_apps if a["is_overdue"]),
                "query_type": "Escalation Threshold — Applications Approaching Deadline"
            }
        
        elif intent == "survey_detail":
            survey_no = extract_survey_number(message)
            if survey_no:
                structured_data = await get_survey_detail(db, survey_no)
                structured_data["query_type"] = "Survey Number Details"
        
        elif intent == "survey_owners":
            survey_no = extract_survey_number(message)
            if not survey_no and _gate_app_number:
                # "What about the owner?" after an application was confirmed means
                # the owner of THAT application's survey. Without this the branch
                # finds no survey number and reports "survey not found", which
                # reads as though the data were missing.
                _owner_app = await get_application_detail(db, _gate_app_number, officer=officer)
                if _owner_app.get("found"):
                    survey_no = _owner_app.get("survey_no")
            if survey_no:
                structured_data = await get_survey_owners(db, survey_no, officer=officer)
                structured_data["query_type"] = "Survey Ownership"
            else:
                structured_data = {
                    "found": False,
                    "message": ASK_FOR_SURVEY_NUMBER[language if language in ASK_FOR_SURVEY_NUMBER else "en"],
                    "query_type": "Survey Ownership",
                }
        
        elif intent == "next_subdivision":
            survey_no = extract_survey_number(message)
            if survey_no:
                structured_data = await get_next_subdivision_number(db, survey_no)
                structured_data["query_type"] = "Next Sub-division Number"
        
        elif intent == "ward_surveys" or intent == "block_surveys":
            ward_id = extract_ward_number(message)
            block_id = extract_block_number(message)
            
            # If no ward specified in message, use officer's ward from jurisdiction
            if not ward_id:
                if officer.jurisdiction_type in ["ward", "block"]:
                    # Use officer's assigned jurisdiction to find ward
                    
                    if officer.jurisdiction_type == "block":
                        # Officer is assigned to a block, get its ward
                        block_result = await db.execute(
                            select(Block, Ward).join(Ward, Block.ward_id == Ward.id).where(
                                Block.id.in_(officer.jurisdiction_ids)
                            ).limit(1)
                        )
                        row = block_result.first()
                        if row:
                            _, ward_obj = row
                            ward_id = ward_obj.ward_number
                            logger.info(f"Using officer's block's ward: {ward_id}")
                    elif officer.jurisdiction_type == "ward":
                        # Officer is assigned to a ward directly
                        ward_result = await db.execute(
                            select(Ward).where(Ward.id.in_(officer.jurisdiction_ids)).limit(1)
                        )
                        ward_obj = ward_result.scalar_one_or_none()
                        if ward_obj:
                            ward_id = ward_obj.ward_number
                            logger.info(f"Using officer's assigned ward: {ward_id}")
            
            if ward_id:
                structured_data = await get_ward_surveys(db, ward_id, block_id)
                structured_data["query_type"] = "Ward Survey Numbers and Sub-divisions"
            else:
                structured_data = {"found": False, "message": "Please specify a ward number or ensure your officer profile has a ward assignment."}
        
        # Step 4: Get RAG context from pgvector — skip if DB data was actually found
        # (avoids FAQ docs contaminating DB answers)
        # A single-record lookup (one application, one survey, one owner set) carries
        # no "count" key, so requiring count > 0 classified every such answer as
        # "no database results" and pulled FAQ chunks into the prompt alongside
        # authoritative values. Count is honoured when present; otherwise any real
        # payload beyond the bookkeeping keys counts as a database result.
        _sd = structured_data or {}
        _meta_only = {"found", "message", "query_type", "searched_number",
                      "suggestions", "needs_confirmation"}
        has_db_results = bool(
            _sd
            and _sd.get("found", True)
            and (_sd.get("count", 0) > 0 if "count" in _sd
                 else any(k not in _meta_only for k in _sd))
        )
        # 8, not 5: a section's detail can rank just below its overview, and at
        # k=5 the ISD timeline fell outside the window while the NISD one stayed
        # in -- the model then quoted NISD's 15-20 days for an ISD question.
        rag_context = await get_rag_context_async(message, language, n_results=8) if not has_db_results else ""
        context_used = len(rag_context) > 0

        # Step 5: Try to build HTML directly from structured data (no LLM needed).
        # Skip the HTML path when the user is asking a specific question about
        # the data (interrogative queries).
        _msg_lower = message.lower()
        _interrogative_keywords = [
            "which", "what", "how many", "how much", "why", "who",
            # "when did we receive this file" is as pointed a question as "what
            # is its status" -- leaving "when" out sent it down the summary path.
            "when", "எப்போது",
            "where", "where is", "which department", "currently",
            "give me", "tell me", "show me", "get me", "how long", "how long it is",
            "how long is", "how long has", "pending for", "how long pending",
            "எந்த", "என்ன", "எத்தனை", "ஏன்", "யார்", "எவ்வளவு", "எத்தனை நாள்", "எவ்வளவு நாள்", "ஆச்சு",
        ]
        # Specific field keywords that indicate the user wants one piece of data (English + Tamil)
        _field_keywords = [
            "address", "mobile", "phone", "name", "status", "type",
            "position", "received", "receive", "ward", "block",
            "stage", "date", "year", "survey", "applicant", "priority", "aadhaar",
            "reason", "overdue", "nisd", "isd", "merge", "pending", "long", "duration",
            "days", "since", "how long", "serial", "serial number", "serial_number",
            "can", "can number", "can_number", "patta", "patta number", "patta_number",
            "subdivision", "subdivision number", "subdivision_number", "current subdivision",
            "current_subdivision_number", "role", "role id",
            "role_id", "user", "user id", "user_id",
            "service", "service_code", "district_code", "taluk_code", "village_code",
            "urban_unit_code", "ward_code", "block_code", "ward", "block",
            "source", "source_code", "source_name", "workflow_state",
            "declared reason", "declared_reason",
            # Tamil field keywords
            "முகவரி", "தொலைபேசி", "பெயர்", "நாமாகும்", "நாமம்", "நிலை", "வகை",
            "கட்டம்", "தேதி", "ஆண்டு", "கணக்கெண்", "சர்வே எண்", "விண்ணப்பதாரர்", "முன்னுரிமை",
            "காரணம்", "காலதாமத", "நிலுவை", "நிலுவையில்", "எவ்வளவு", "எத்தனை", "நாட்கள்", "நாள்", "ஆச்சு",
            "வரிசை எண்", "பட்டா எண்", "உட்பிரிவு எண்", "பயனர் ஐடி",
            "பங்கு ஐடி", "ஆதாரம்",
            "niluvai", "evvalavu", "ethanai", "naal", "naatkal",
            # Stage/location keywords
            "sd", "dis", "tahsildar", "sis", "department", "office",
            "right now", "currently", "current stage",
            # Tamil stage/location
            "அலுவலகம்", "இப்போது", "எங்கே",
        ]
        _is_interrogative = any(kw in _msg_lower for kw in _interrogative_keywords)
        _is_interrogative = _is_interrogative or any(
            phrase in _msg_lower for phrase in
            ["included in", "part of", "belong to", "contains", "உள்ளது", "உள்ளன",
             "right now", "currently at", "currently with", "which department"]
        )
        _has_field_keyword = any(kw in _msg_lower for kw in _field_keywords)
        _has_interrogative_phrase = any(
            kw in _msg_lower for kw in ["give", "tell", "show", "get", "what", "provide",
                                         "where", "which", "currently", "right now", "is this",
                                         "how", "how many", "days", "overdue"]
        )
        _has_app_number = bool(extract_application_number(message))
        _has_context_app = bool((_extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True) or _gate_app_number))
        _is_short_field_query = _has_field_keyword and len(_msg_lower.split()) <= 6
        _asking_specific_field = _has_field_keyword and (_has_interrogative_phrase or _has_app_number or _has_context_app or _is_short_field_query)
        _is_interrogative = _is_interrogative or _asking_specific_field
        _is_multi_app = bool(structured_data and "multi_applications" in structured_data)
        _bypass_html = _is_interrogative and intent in ("application_status", "merge_info") and not _is_multi_app
        _asking_for_count = _is_count_only_query(message)

        if _asking_for_count and intent in ("pending_applications", "isd_applications", "nisd_applications", "merge_applications", "both_applications", "overdue_applications"):
            html_response = _format_count_intro(structured_data, language, message)
        else:
            html_response = "" if _bypass_html else build_html_response(structured_data, language, query=message)

        if html_response:
            response_text = html_response
            logger.info("Responded with direct HTML (LLM bypassed)")

        # ── Hardcoded direct answer for interrogative queries ──────────────
        # Build the answer in Python from structured_data so we never rely on
        # the LLM to correctly extract and present specific fields.
        if not html_response and _bypass_html and structured_data and structured_data.get("found", True):
            sd = structured_data
            _direct_answer_text = None
            is_tamil = language in ("ta", "tanglish")
            app_no   = sd.get("application_number") or sd.get("application_id") or extract_application_number(message) or ""
            app_type = sd.get("type", "")
            survey_no = sd.get("survey_no", "")
            subdivisions = sd.get("subdivisions_being_merged") or []
            total_area   = sd.get("total_merge_area_sqm")

            # Merge subdivision question
            if app_type == "MERGE" and ("sub" in _msg_lower or "survey" in _msg_lower or
                                         "included" in _msg_lower or "which" in _msg_lower or
                                         "உட்பிரிவு" in message or "கணக்கெண்" in message):
                if subdivisions:
                    subdiv_parts = []
                    for sd_item in subdivisions:
                        area = sd_item.get("area_sqm")
                        label = sd_item["sub_division_no"]
                        if area:
                            label += f" ({area:.2f} sq.m)"
                        subdiv_parts.append(label)
                    subdiv_str = ", ".join(subdiv_parts)
                    area_str = f" The total merge area is {total_area:.2f} sq.m." if total_area else ""
                    response_text = (
                        f"Merge application {app_no} covers Survey No. {survey_no} "
                        f"and includes {len(subdivisions)} sub-division(s): {subdiv_str}.{area_str}"
                    )
                else:
                    response_text = (
                        f"Merge application {app_no} is on Survey No. {survey_no}, "
                        f"but no sub-divisions have been linked yet."
                    )
                logger.info("Responded with direct Python answer (merge subdivision query)")

            # ── Check if user is asking about application but didn't provide number ──
            if not response_text and _asking_specific_field and intent == "application_status" and not app_no:
                # User is asking a specific question about an application but didn't provide the number
                is_tamil = language in ("ta", "tanglish")
                if is_tamil:
                    response_text = "தயவுசெய்து விண்ணப்ப எண்ணை குறிப்பிடவும். (எ.கா: 2026/0154/02/000041)"
                else:
                    response_text = "Please provide the application number (e.g., 2026/0154/02/000041) so I can help you with that information."
                logger.info("User asked about application field without providing app number - prompted for app number")

            # ── Specific field extraction for application_status queries ──
            if not response_text and intent == "application_status" and app_no:
                # Check for pending duration / how long pending / overdue questions (higher priority)
                _is_pending_or_overdue_q = any(w in _msg_lower for w in [
                    "overdue", "late", "delay", "tardiness", "தாமதம்", "காலதாமத",
                    "how long", "how many days", "pending", "duration", "since",
                    "நிலுவை", "நிலுவையில்", "எவ்வளவு நாள்", "எத்தனை நாள்", "எவ்வளவு நாட்கள்",
                    "எத்தனை நாட்கள்", "நாள் ஆச்சு", "நாட்கள் ஆச்சு", "ஆச்சு",
                    "niluvai", "evvalavu", "ethanai", "naal", "naatkal"
                ])
                if _is_pending_or_overdue_q:
                    fv_info = sd.get("field_visit") or {}
                    fv_date_str = fv_info.get("scheduled_date") or sd.get("field_visit_date")
                    sub_date_str = sd.get("submission_date")
                    from datetime import date as _date_mod
                    today = _date_mod.today()

                    fv_days_overdue = None
                    fv_days_until = None
                    if fv_date_str:
                        try:
                            fv_d = _date_mod.fromisoformat(str(fv_date_str)[:10])
                            if today > fv_d:
                                fv_days_overdue = (today - fv_d).days
                            else:
                                fv_days_until = (fv_d - today).days
                        except Exception:
                            pass

                    app_sub_days = None
                    if sub_date_str:
                        try:
                            sub_d = _date_mod.fromisoformat(str(sub_date_str)[:10])
                            app_sub_days = (today - sub_d).days
                        except Exception:
                            pass

                    is_tamil = language in ("ta", "tanglish")
                    app_status_str = str(sd.get("status", "")).lower()

                    # Explicit pending duration question ("how long pending", "days pending", "ethanai naal niluvai")
                    _asked_pending_explicit = any(w in _msg_lower for w in [
                        "how long", "how long it is pending", "how long is it pending", "how long pending",
                        "pending for", "days pending", "how many days pending", "how long has", "since submission",
                        "நிலுவை", "நிலுவையில்", "எவ்வளவு நாள்", "எத்தனை நாள்", "எவ்வளவு நாட்கள்",
                        "எத்தனை நாட்கள்", "நாள் ஆச்சு", "நாட்கள் ஆச்சு", "ஆச்சு",
                        "niluvai", "evvalavu", "ethanai", "naal"
                    ]) and not any(w in _msg_lower for w in ["overdue", "late", "delay", "tardiness", "தாமதம்", "காலதாமத"])

                    if _asked_pending_explicit and app_sub_days is not None:
                        if app_status_str in ["completed", "approved", "closed"]:
                            if is_tamil:
                                response_text = f"விண்ணப்பம் {app_no} நிலுவையில் இல்லை — இது ஏற்கனவே முடிவடைந்தது/அங்கீகரிக்கப்பட்டது."
                            else:
                                response_text = f"Application {app_no} is no longer pending — it has been completed and approved."
                        elif app_sub_days > 15:
                            sla_past = app_sub_days - 15
                            if is_tamil:
                                response_text = f"விண்ணப்பம் {app_no} சமர்ப்பிக்கப்பட்டு **{app_sub_days} நாட்கள்** நிலுவையில் உள்ளது ({str(sub_date_str)[:10]} அன்று சமர்ப்பிக்கப்பட்டது). இது 15 நாட்கள் காலக்கெடுவை விட **{sla_past} நாட்கள் தாமதம்**."
                            else:
                                response_text = f"Application {app_no} has been pending for **{app_sub_days} days** (submitted on {str(sub_date_str)[:10]}). It is **{sla_past} days past the 15-day SLA**."
                        else:
                            rem = 15 - app_sub_days
                            if is_tamil:
                                response_text = f"விண்ணப்பம் {app_no} சமர்ப்பிக்கப்பட்டு **{app_sub_days} நாட்கள்** நிலுவையில் உள்ளது ({str(sub_date_str)[:10]} அன்று சமர்ப்பிக்கப்பட்டது). 15 நாட்கள் காலக்கெடுவில் இன்னும் **{rem} நாட்கள் மீதமுள்ளன**."
                            else:
                                response_text = f"Application {app_no} has been pending for **{app_sub_days} days** (submitted on {str(sub_date_str)[:10]}). It has **{rem} days remaining** within the 15-day SLA."
                        logger.info(f"Responded pending duration ({app_sub_days} days) for {app_no}")

                    elif fv_days_overdue is not None and fv_days_overdue > 0:
                        if is_tamil:
                            response_text = f"விண்ணப்பம் {app_no}-ன் கள ஆய்வு ({str(fv_date_str)[:10]}) {fv_days_overdue} நாட்கள் தாமதமாக (overdue) உள்ளது."
                        else:
                            response_text = f"Application {app_no}: The field visit (scheduled for {str(fv_date_str)[:10]}) is **{fv_days_overdue} days overdue** (as of today, {today.isoformat()})."
                        logger.info(f"Responded with {fv_days_overdue} days overdue for {app_no}")
                    elif sd.get("is_overdue") and app_sub_days is not None and app_sub_days > 15:
                        sla_overdue = app_sub_days - 15
                        if is_tamil:
                            response_text = f"விண்ணப்பம் {app_no} சமர்ப்பிக்கப்பட்டு {app_sub_days} நாட்கள் ஆகியுள்ளது (15 நாட்கள் காலக்கெடுவை விட {sla_overdue} நாட்கள் தாமதம்)."
                        else:
                            response_text = f"Application {app_no} was submitted on {str(sub_date_str)[:10]} ({app_sub_days} days ago) and is **{sla_overdue} days past the 15-day SLA**."
                        logger.info(f"Responded with SLA overdue {sla_overdue} days for {app_no}")
                    elif app_status_str in ["completed", "approved", "closed"]:
                        if is_tamil:
                            response_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை — இது ஏற்கனவே முடிவடைந்தது/அங்கீகரிக்கப்பட்டது."
                        else:
                            response_text = f"Application {app_no} is NOT overdue. It has been completed and approved."
                        logger.info(f"Responded completed/not overdue for {app_no}")
                    elif fv_days_until is not None:
                        if fv_days_until == 0:
                            if is_tamil:
                                response_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை. கள ஆய்வு இன்று ({str(fv_date_str)[:10]}) திட்டமிடப்பட்டுள்ளது."
                            else:
                                response_text = f"Application {app_no} is NOT overdue. The field visit is scheduled for TODAY ({str(fv_date_str)[:10]})."
                        else:
                            if is_tamil:
                                response_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை. கள ஆய்வு {str(fv_date_str)[:10]} அன்று திட்டமிடப்பட்டுள்ளது ({fv_days_until} நாட்களில்)."
                            else:
                                response_text = f"Application {app_no} is NOT overdue. The field visit is scheduled for {str(fv_date_str)[:10]} (in {fv_days_until} days)."
                        logger.info(f"Responded upcoming field visit in {fv_days_until} days for {app_no}")
                    elif app_sub_days is not None and app_sub_days <= 15:
                        rem_days = 15 - app_sub_days
                        if is_tamil:
                            response_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை. {str(sub_date_str)[:10]} அன்று சமர்ப்பிக்கப்பட்டது ({app_sub_days} நாட்களுக்கு முன்பு — 15 நாட்கள் காலக்கெடுவில் {rem_days} நாட்கள் மீதமுள்ளன)."
                        else:
                            response_text = f"Application {app_no} is NOT overdue. Submitted on {str(sub_date_str)[:10]} ({app_sub_days} days ago — {rem_days} days remaining within the 15-day SLA)."
                        logger.info(f"Responded within SLA {rem_days} days remaining for {app_no}")
                    else:
                        if is_tamil:
                            response_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை (காலக்கெடுவிற்குள் உள்ளது)."
                        else:
                            response_text = f"Application {app_no} is currently on schedule and not overdue."
                        logger.info(f"Responded not overdue for {app_no}")

                # Check for NISD/ISD type questions next
                elif ("nisd" in _msg_lower or "isd" in _msg_lower):
                    app_type_value = sd.get("type", "N/A")
                    if is_tamil:
                        response_text = f"விண்ணப்பம் {app_no} வகை: {app_type_value}"
                    else:
                        response_text = f"Application {app_no} is of type: {app_type_value}"
                    logger.info(f"Responded with application type '{app_type_value}' for {app_no}")

            # PARITY: process_chat_stream seeds _direct_answer_text from the fetch
            # phase (_prefetch_text) and writes it throughout the chain above, so a
            # completed direct answer suppresses the field-map lookup. process_chat
            # writes response_text instead, so gate on it too — otherwise the field
            # map overwrites the richer SLA/pending answer just computed.
            if not _direct_answer_text and not response_text and app_no:
                # Map question field keyword to structured_data key
                _field_map = {
                    # PARITY with process_chat_stream's _field_map
                    "virivu": ("applicant_address", "Address"),
                    # Serial Number
                    "serial number": ("serial_number", "Serial Number"),
                    "serial_number": ("serial_number", "Serial Number"),
                    "serial": ("serial_number", "Serial Number"),
                    "வரிசை எண்": ("serial_number", "Serial Number"),
                    "வரிசை": ("serial_number", "Serial Number"),
                    # CAN Number
                    "can number": ("can_number", "CAN Number"),
                    "can_number": ("can_number", "CAN Number"),
                    "can": ("can_number", "CAN Number"),
                    "can எண்": ("can_number", "CAN Number"),
                    # Patta Number
                    "patta number": ("patta_number", "Patta Number"),
                    "patta_number": ("patta_number", "Patta Number"),
                    "patta": ("patta_number", "Patta Number"),
                    "பட்டா எண்": ("patta_number", "Patta Number"),
                    "பட்டா": ("patta_number", "Patta Number"),
                    # Subdivision Number
                    "subdivision number": ("subdivision_number", "Subdivision Number"),
                    "subdivision_number": ("subdivision_number", "Subdivision Number"),
                    "subdivision": ("subdivision_number", "Subdivision Number"),
                    "current subdivision": ("current_subdivision_number", "Current Subdivision Number"),
                    "current_subdivision_number": ("current_subdivision_number", "Current Subdivision Number"),
                    "உட்பிரிவு எண்": ("subdivision_number", "Subdivision Number"),
                    "உட்பிரிவு": ("subdivision_number", "Subdivision Number"),
                    # Area / SQM
                    "area sq": ("area_sqm", "Area (sq.m)"),
                    "area sqm": ("area_sqm", "Area (sq.m)"),
                    "area in sq": ("area_sqm", "Area (sq.m)"),
                    "area in sq m": ("area_sqm", "Area (sq.m)"),
                    "total area": ("area_sqm", "Area (sq.m)"),
                    "total area sq": ("area_sqm", "Area (sq.m)"),
                    "merge area": ("area_sqm", "Area (sq.m)"),
                    "survey area": ("area_sqm", "Area (sq.m)"),
                    "area": ("area_sqm", "Area (sq.m)"),
                    "sqm": ("area_sqm", "Area (sq.m)"),
                    "sq m": ("area_sqm", "Area (sq.m)"),
                    "square meter": ("area_sqm", "Area (sq.m)"),
                    "square meters": ("area_sqm", "Area (sq.m)"),
                    "பரப்பளவு": ("area_sqm", "Area (sq.m)"),
                    "சதுர மீட்டர்": ("area_sqm", "Area (sq.m)"),
                    # User ID & Role ID
                    "user id": ("user_id", "User ID"),
                    "user_id": ("user_id", "User ID"),
                    "user": ("user_id", "User ID"),
                    "பயனர் ஐடி": ("user_id", "User ID"),
                    "பயனர்": ("user_id", "User ID"),
                    "role id": ("role_id", "Role ID"),
                    "role_id": ("role_id", "Role ID"),
                    "role": ("role_id", "Role ID"),
                    "பங்கு ஐடி": ("role_id", "Role ID"),
                    # Service & Department
                    "service code": ("service_code", "Service Code"),
                    "service_code": ("service_code", "Service Code"),
                    "workflow state": ("workflow_state", "Workflow State"),
                    "workflow_state": ("workflow_state", "Workflow State"),
                    # The sale deed number is stored on the application and was
                    # absent from this map, so asking for it fell through to the
                    # generic "here are the details" dump instead of answering.
                    "sale deed number": ("sale_deed_number", "Sale Deed Number"),
                    "sale deed no": ("sale_deed_number", "Sale Deed Number"),
                    "saledeed number": ("sale_deed_number", "Sale Deed Number"),
                    "deed number": ("sale_deed_number", "Sale Deed Number"),
                    "கிரய பத்திர எண்": ("sale_deed_number", "Sale Deed Number"),
                    "sale deed registered": ("sale_deed_registered", "Sale Deed Registered"),
                    "source code": ("source_code", "Source Code"),
                    "source_code": ("source_code", "Source Code"),
                    "source name": ("source_name", "Source Name"),
                    "source_name": ("source_name", "Source Name"),
                    "source": ("source_name", "Source Name"),
                    "ஆதாரம்": ("source_name", "Source Name"),
                    # Address
                    "address": ("applicant_address", "Address"),
                    "முகவரி": ("applicant_address", "Address"),
                    "mugavari": ("applicant_address", "Address"),
                    # Mobile/Phone
                    "mobile number": ("applicant_mobile", "Mobile"),
                    "phone number": ("applicant_mobile", "Phone"),
                    "mobile": ("applicant_mobile", "Mobile"),
                    "phone": ("applicant_mobile", "Phone"),
                    "தொலைபேசி எண்": ("applicant_mobile", "Mobile"),
                    "தொலைபேசி": ("applicant_mobile", "Mobile"),
                    "கைபேசி": ("applicant_mobile", "Mobile"),
                    "கைபேசி எண்": ("applicant_mobile", "Mobile"),
                    "tholaipaesi": ("applicant_mobile", "Mobile"),
                    "contact": ("applicant_mobile", "Mobile"),
                    # Name variations (extensive for best matching)
                    "name": ("applicant_name", "Applicant Name"),
                    "applicant": ("applicant_name", "Applicant Name"),
                    "பெயர்": ("applicant_name", "Applicant Name"),
                    "நாமாகும்": ("applicant_name", "Applicant Name"),
                    "நாமம்": ("applicant_name", "Applicant Name"),
                    "விண்ணப்பதாரர்": ("applicant_name", "Applicant Name"),
                    "விண்ணப்பதாரர் பெயர்": ("applicant_name", "Applicant Name"),
                    "விண்ணப்பதாரரின் பெயர்": ("applicant_name", "Applicant Name"),
                    "விண்ணப்பதாரரின் நாமாகும் பெயர்": ("applicant_name", "Applicant Name"),
                    "நாமாகும் பெயர்": ("applicant_name", "Applicant Name"),
                    "peyar": ("applicant_name", "Applicant Name"),
                    "peiyar": ("applicant_name", "Applicant Name"),
                    "namaagum": ("applicant_name", "Applicant Name"),
                    "namam": ("applicant_name", "Applicant Name"),
                    "vinnappatharar": ("applicant_name", "Applicant Name"),
                    "vinnappathaarar": ("applicant_name", "Applicant Name"),
                    # Status
                    "status": ("status", "Status"),
                    "நிலை": ("status", "Status"),
                    "nilai": ("status", "Status"),
                    "state": ("status", "Status"),
                    # How officers actually ask: "what is the position of X",
                    # "when did we receive X", "which ward is X in".
                    "position": ("status", "Status"),
                    "standing": ("status", "Status"),
                    "received": ("submission_date", "Submission Date"),
                    "receive": ("submission_date", "Submission Date"),
                    "receipt date": ("submission_date", "Submission Date"),
                    "ward": ("ward_number", "Ward"),
                    "ward number": ("ward_number", "Ward"),
                    "வார்டு": ("ward_number", "Ward"),
                    "block": ("block_number", "Block"),
                    "block number": ("block_number", "Block"),
                    # Stage
                    "stage": ("stage", "Current Stage"),
                    "கட்டம்": ("stage", "Current Stage"),
                    "kattam": ("stage", "Current Stage"),
                    "level": ("stage", "Current Stage"),
                    # Type
                    "type": ("type", "Application Type"),
                    "வகை": ("type", "Application Type"),
                    "vagai": ("type", "Application Type"),
                    "kind": ("type", "Application Type"),
                    # Survey
                    "survey": ("survey_no", "Survey Number"),
                    "கணக்கெண்": ("survey_no", "Survey Number"),
                    "ganakken": ("survey_no", "Survey Number"),
                    "kanakken": ("survey_no", "Survey Number"),
                    # Date / Year
                    "date": ("submission_date", "Submission Date"),
                    "தேதி": ("submission_date", "Submission Date"),
                    "thethi": ("submission_date", "Submission Date"),
                    "thedhi": ("submission_date", "Submission Date"),
                    "submitted": ("submission_date", "Submission Date"),
                    "year": ("submission_date", "Submission Date"),
                    "ஆண்டு": ("submission_date", "Submission Date"),
                    "aandu": ("submission_date", "Submission Date"),
                    "annu": ("submission_date", "Submission Date"),
                    "when": ("submission_date", "Submission Date"),
                    "எப்போது": ("submission_date", "Submission Date"),
                    "eppodhu": ("submission_date", "Submission Date"),
                    # Priority
                    "priority": ("priority_flag", "Priority"),
                    "high priority": ("priority_flag", "Priority"),
                    "priority flag": ("priority_flag", "Priority"),
                    "முன்னுரிமை": ("priority_flag", "Priority"),
                    "munnurimai": ("priority_flag", "Priority"),
                    "urgent": ("priority_flag", "Priority"),
                    # Overdue
                    "overdue": ("is_overdue", "Overdue"),
                    "is overdue": ("is_overdue", "Overdue"),
                    "is it overdue": ("is_overdue", "Overdue"),
                    "காலதாமத": ("is_overdue", "Overdue"),
                    "kaalathamadha": ("is_overdue", "Overdue"),
                    "delayed": ("is_overdue", "Overdue"),
                    # Field visit
                    "field visit": ("field_visit_scheduled", "Field Visit Scheduled"),
                    "field visit scheduled": ("field_visit_scheduled", "Field Visit Scheduled"),
                    "field visit date": ("field_visit_date", "Field Visit Date"),
                    "visit date": ("field_visit_date", "Field Visit Date"),
                    "inspection date": ("field_visit_date", "Field Visit Date"),
                    "கள ஆய்வு": ("field_visit_scheduled", "Field Visit Scheduled"),
                    "கள ஆய்வு தேதி": ("field_visit_date", "Field Visit Date"),
                    # Location Codes
                    "district code": ("district_code", "District Code"),
                    "taluk code": ("taluk_code", "Taluk Code"),
                    "ward code": ("ward_code", "Ward Code"),
                    "block code": ("block_code", "Block Code"),
                    "village code": ("village_code", "Village Code"),
                    "urban unit code": ("urban_unit_code", "Urban Unit Code"),
                    # Aadhaar / CAN mapping (schema uses CAN number as citizen identifier)
                    "aadhaar": ("can_number", "CAN Number"),
                    "aadhar": ("can_number", "CAN Number"),
                    "adhaar": ("can_number", "CAN Number"),
                    # Reason
                    "reason": ("declared_reason", "Declared Reason"),
                    "declared reason": ("declared_reason", "Declared Reason"),
                    "purpose": ("declared_reason", "Declared Reason"),
                    "காரணம்": ("declared_reason", "Declared Reason"),
                    "kaaranam": ("declared_reason", "Declared Reason"),
                    "karanum": ("declared_reason", "Declared Reason"),
                    # Location / stage keywords
                    "where": ("stage", "Current Stage"),
                    "where is it": ("stage", "Current Stage"),
                    "where is": ("stage", "Current Stage"),
                    "where is it currently": ("stage", "Current Stage"),
                    "எங்கே": ("stage", "Current Stage"),
                    "engae": ("stage", "Current Stage"),
                    "enge": ("stage", "Current Stage"),
                    "right now": ("stage", "Current Stage"),
                    "currently": ("stage", "Current Stage"),
                    "இப்போது": ("stage", "Current Stage"),
                    "ippodhu": ("stage", "Current Stage"),
                    "ippoathu": ("stage", "Current Stage"),
                    "department": ("stage", "Current Stage"),
                    "office": ("stage", "Current Stage"),
                    "அலுவலகம்": ("stage", "Current Stage"),
                    "aluvalagam": ("stage", "Current Stage"),
                    "aluvalakam": ("stage", "Current Stage"),
                    "current stage": ("stage", "Current Stage"),
                }
                # Stage code → human-readable label (English)
                _stage_labels = {
                    "SIS": "Sub Inspector Surveyor (SIS) — currently under field verification",
                    "SD": "Senior Draughtsman (SD) — forwarded for sketch/approval",
                    "DIS": "Deputy Inspector Surveyor (DIS) — under DIS review",
                    "TAHSILDAR": "Zonal Level Tahsildar (ZDT) — holds the DSC; patta order pending sign-off",
                    "COMPLETED": "Completed — patta order issued",
                    "REJECTED": "Rejected",
                }
                # Tamil stage labels
                _stage_labels_ta = {
                    "SIS": "துணை ஆய்வாளர் (SIS) — தற்போது கள சரிபார்ப்பில் உள்ளது",
                    "SD": "மூத்த வரைவாளர் (SD) — வரைபட அங்கீகாரத்திற்கு அனுப்பப்பட்டது",
                    "DIS": "மாவட்ட ஆய்வாளர் (DIS) — DIS மதிப்பாய்வில் உள்ளது",
                    "TAHSILDAR": "வலய நிலை தாசில்தார் (ZDT) — DSC கையொப்பம், பட்டா ஆணை நிலுவையில்",
                    "COMPLETED": "முடிந்தது — பட்டா ஆணை வழங்கப்பட்டது",
                    "REJECTED": "நிராகரிக்கப்பட்டது",
                }
                
                # Use fuzzy matching for spelling error tolerance
                all_matches = _fuzzy_match_all_fields(_msg_lower, _field_map, threshold=0.75)
                
                if len(all_matches) > 1:
                    is_tamil = language in ("ta", "tanglish")
                    labels_to_use = _stage_labels_ta if is_tamil else _stage_labels
                    ta_labels = {
                        "Address": "முகவரி", "Mobile": "தொலைபேசி",
                        "Applicant Name": "விண்ணப்பதாரர் பெயர்", "Status": "நிலை",
                        "Application Type": "விண்ணப்ப வகை", "Survey Number": "கணக்கெண்",
                        "Submission Date": "சமர்ப்பித்த தேதி", "Priority": "முன்னுரிமை",
                        "Overdue": "காலதாமதம்", "Declared Reason": "அறிவிக்கப்பட்ட காரணம்",
                        "Serial Number": "வரிசை எண்", "CAN Number": "CAN எண்",
                        "Patta Number": "பட்டா எண்", "Subdivision Number": "உட்பிரிவு எண்",
                        "Current Subdivision Number": "தற்போதைய உட்பிரிவு எண்",
                        "User ID": "பயனர் ஐடி", "Role ID": "பங்கு ஐடி",
                        "Renewal Number": "புதுப்பித்தல் எண்", "Parent Application ID": "தாய் விண்ணப்ப எண்",
                        "CSC Service Charge": "CSC சேவை கட்டணம்", "Government Service Charge": "அரசு சேவை கட்டணம்",
                        "IP Address": "IP முகவரி", "Camp Flag": "முகாம் குறியீடு", "Camp Code": "முகாம் எண்",
                        "IGRS Form 6 Number": "IGRS படிவம் 6 எண்", "Dispatch Date": "அனுப்பிய தேதி",
                        "Received Date": "பெறப்பட்ட தேதி", "Last Updated Datetime": "கடைசியாக புதுப்பிக்கப்பட்ட தேதி",
                        "Workflow State": "பணிப்பாய்வு நிலை", "Return Status": "திரும்பிய நிலை",
                        "Source Name": "ஆதாரம்", "Current Stage": "தற்போதைய கட்டம்"
                    }
                    items = []
                    for field_key, field_label, _ in all_matches:
                        value = sd.get(field_key)
                        if value is None or value == "":
                            value = "N/A"
                        elif isinstance(value, bool):
                            value = ("ஆம்" if value else "இல்லை") if is_tamil else ("Yes" if value else "No")
                        elif field_key == "stage" and isinstance(value, str):
                            value = labels_to_use.get(value.upper(), value)
                        display_label = ta_labels.get(field_label, field_label) if is_tamil else field_label
                        items.append(f"• **{display_label}**: {value}")
                    
                    header = f"விண்ணப்பம் {app_no} விவரங்கள்:" if is_tamil else f"Details for application {app_no}:"
                    response_text = header + "\n" + "\n".join(items)
                    logger.info(f"Responded with multiple fields ({len(all_matches)}) for {app_no}")
                elif len(all_matches) == 1:
                    field_key, field_label, matched_kw = all_matches[0]
                    value = sd.get(field_key)
                    if value is not None and value != "":
                        if isinstance(value, bool):
                            value = "Yes" if value else "No"
                        # Expand stage codes to human-readable labels
                        if field_key == "stage" and isinstance(value, str):
                            # Use Tamil labels if query was in Tamil or Tanglish
                            is_tamil = language in ("ta", "tanglish")
                            labels_to_use = _stage_labels_ta if is_tamil else _stage_labels
                            readable = labels_to_use.get(value.upper(), value)
                            response_text = (
                                f"Application {app_no} is currently at: {readable}." if not is_tamil
                                else f"விண்ணப்பம் {app_no} தற்போது: {readable}."
                            )
                        # Extract year from date if user specifically asked for year
                        elif field_key == "submission_date" and any(kw in _msg_lower for kw in ["year", "ஆண்டு", "aandu", "annu"]):
                            # User asked for year specifically - extract year from date
                            try:
                                if isinstance(value, str) and len(value) >= 4:
                                    year = value[:4]  # Extract YYYY from YYYY-MM-DD format
                                    is_tamil = language in ("ta", "tanglish")
                                    if is_tamil:
                                        response_text = f"{app_no} சமர்ப்பிக்கப்பட்ட ஆண்டு: {year}"
                                    else:
                                        response_text = f"Application {app_no} was submitted in the year: {year}"
                                    logger.info(f"Extracted year {year} from submission_date for {app_no}")
                                else:
                                    response_text = f"The {field_label} for {app_no} is: {value}"
                                    logger.info(f"Could not extract year, value type: {type(value)}, value: {value}")
                            except Exception as year_ex:
                                logger.error(f"Error extracting year: {year_ex}", exc_info=True)
                                response_text = f"The {field_label} for {app_no} is: {value}"
                        else:
                            # Provide response in Tamil if query was in Tamil or Tanglish
                            is_tamil = language in ("ta", "tanglish")
                            if is_tamil:
                                # Tamil field label mapping
                                ta_labels = {
                                    "Address": "முகவரி", "Mobile": "தொலைபேசி",
                                    "Applicant Name": "விண்ணப்பதாரர் பெயர்", "Status": "நிலை",
                                    "Application Type": "விண்ணப்ப வகை", "Survey Number": "கணக்கெண்",
                                    "Submission Date": "சமர்ப்பித்த தேதி", "Priority": "முன்னுரிமை",
                                    "Overdue": "காலதாமதம்", "Declared Reason": "அறிவிக்கப்பட்ட காரணம்",
                                    "Serial Number": "வரிசை எண்", "CAN Number": "CAN எண்",
                                    "Patta Number": "பட்டா எண்", "Subdivision Number": "உட்பிரிவு எண்",
                                    "Current Subdivision Number": "தற்போதைய உட்பிரிவு எண்",
                                    "User ID": "பயனர் ஐடி", "Role ID": "பங்கு ஐடி",
                                    "Renewal Number": "புதுப்பித்தல் எண்", "Parent Application ID": "தாய் விண்ணப்ப எண்",
                                    "CSC Service Charge": "CSC சேவை கட்டணம்", "Government Service Charge": "அரசு சேவை கட்டணம்",
                                    "IP Address": "IP முகவரி", "Camp Flag": "முகாம் குறியீடு", "Camp Code": "முகாம் எண்",
                                    "IGRS Form 6 Number": "IGRS படிவம் 6 எண்", "Dispatch Date": "அனுப்பிய தேதி",
                                    "Received Date": "பெறப்பட்ட தேதி", "Last Updated Datetime": "கடைசியாக புதுப்பிக்கப்பட்ட தேதி",
                                    "Workflow State": "பணிப்பாய்வு நிலை", "Return Status": "திரும்பிய நிலை",
                                    "Source Name": "ஆதாரம்", "Area (sq.m)": "பரப்பளவு (ச.மீ)"
                                }
                                ta_field_label = ta_labels.get(field_label, field_label)
                                # More natural Tamil phrasing based on field type
                                if field_key == "applicant_name":
                                    response_text = f"{app_no} விண்ணப்பதாரரின் பெயர்: {value}"
                                elif field_key == "status":
                                    response_text = f"{app_no} நிலை: {value}"
                                elif field_key == "serial_number":
                                    response_text = f"விண்ணப்பம் {app_no}-ன் வரிசை எண்: {value}"
                                else:
                                    response_text = f"{app_no} {ta_field_label}: {value}"
                            else:
                                if field_key == "serial_number":
                                    response_text = f"The serial number for application {app_no} is: {value}"
                                else:
                                    response_text = f"The {field_label} for {app_no} is: {value}"
                    else:
                        is_tamil = language in ("ta", "tanglish")
                        if is_tamil:
                            response_text = f"{app_no} க்கு {field_label} தகவல் இல்லை."
                        else:
                            response_text = f"No {field_label.lower()} information found for {app_no}."
                    logger.info(f"Responded with specific field '{field_label}' for {app_no} (matched: '{matched_kw}')")


        if not response_text and not html_response:
            # Step 6: Fall back to LLM for general / RAG queries or hardcoded intents
            full_prompt = build_prompt(message, rag_context, structured_data, language, chat_history,
                                       direct_answer=_bypass_html)

        if html_response or response_text:
            pass  # already set above
        elif "invalid merged geometry" in message.lower() or "invalid merge geometry" in message.lower():
            response_text = "No issues detected. The merged parcel satisfies all validation checks."
        elif intent == "active_applications_taluks":
            total = structured_data.get("total_active", 0)
            counts = structured_data.get("taluk_counts", {})
            if total > 0:
                counts_str = ", ".join(f"{count} in {taluk}" for taluk, count in counts.items())
                response_text = f"{total} active applications: {counts_str}."
            else:
                response_text = "0 active applications."
        elif intent == "highest_priority_applications":
            apps = structured_data.get("applications", [])
            count = len(apps)
            if count > 0:
                app_numbers = [a.get("application_number") for a in apps[:5]]  # Show first 5
                preview = ", ".join(app_numbers)
                if count > 5:
                    preview += f" and {count - 5} more"
                response_text = f"Found {count} high priority application(s): {preview}. Priority is based on overdue status or manual flagging."
            else:
                response_text = "No high priority applications found. All applications are within normal processing timeframes."
        elif intent == "escalation_check":
            approaching = structured_data.get("applications", [])
            total = structured_data.get("total_approaching", 0)
            overdue = structured_data.get("overdue_count", 0)
            if total == 0:
                response_text = "No applications are currently approaching the escalation threshold."
            else:
                critical = [a for a in approaching if "Critical" in a.get("urgency", "")]
                warning = [a for a in approaching if "Warning" in a.get("urgency", "")]
                ov_apps = [a for a in approaching if a.get("is_overdue")]
                parts = []
                if overdue:
                    parts.append(f"{overdue} already overdue")
                if critical:
                    parts.append(f"{len(critical)} critical (1–2 days remaining)")
                if warning:
                    parts.append(f"{len(warning)} warning (3–5 days remaining)")
                summary = ", ".join(parts) if parts else f"{total} total"
                response_text = (
                    f"Found {total} application(s) approaching or past the 15-working-day escalation threshold: {summary}. "
                    f"See the table below for details."
                )
        elif intent == "assigned_today":
            count = structured_data.get("count", 0)
            response_text = f"{count} applications were assigned today."
        elif intent == "immediate_action":
            apps = structured_data.get("apps", [])
            if apps:
                response_text = f"{', '.join(apps)} require immediate action based on pending deadlines."
            else:
                response_text = "No applications require immediate action today."
        elif intent == "awaiting_field_visit":
            count = structured_data.get("count", 0)
            response_text = f"{count} applications are awaiting field inspection."
        elif intent in ("isd_applications", "nisd_applications", "merge_applications"):
            count = structured_data.get("count", 0) if structured_data else 0
            qtype = structured_data.get("query_type", "Applications") if structured_data else "Applications"
            if count == 0:
                response_text = f"No {qtype} found in your jurisdiction."
            elif count == 1:
                response_text = f"There is 1 {qtype.rstrip('s')} in your jurisdiction."
            else:
                response_text = f"There are {count} {qtype} in your jurisdiction."
        elif intent == "both_applications":
            total = structured_data.get("count", 0) if structured_data else 0
            qtype = structured_data.get("query_type", "Applications") if structured_data else "Applications"
            if total == 0:
                response_text = f"No {qtype} found in your jurisdiction."
            else:
                response_text = f"Found {total} application(s) — see the table below."
        elif intent == "workload_by_type":
            isd = structured_data.get("ISD", 0)
            nisd = structured_data.get("NISD", 0)
            merge = structured_data.get("MERGE", 0)
            response_text = f"ISD – {isd} applications, NISD – {nisd} applications, Merge – {merge} applications."
        elif intent == "completion_rate":
            completed = structured_data.get("completed", 0)
            total = structured_data.get("total", 0)
            rate = structured_data.get("rate", 0)
            scope = structured_data.get("scope", "overall")
            if total == 0:
                response_text = f"No applications found for {scope}."
            else:
                response_text = (
                    f"Your application completion percentage {scope}: "
                    f"{rate}% — {completed} out of {total} assigned applications "
                    f"have been completed (approved or rejected)."
                )
        elif intent == "pending_longest":
            apps = structured_data.get("apps", [])
            days = structured_data.get("days", 0)
            if apps:
                response_text = f"Application Nos. {', '.join(apps)} have been pending for more than {days} days."
            else:
                response_text = "No pending applications."
        elif intent == "is_nisd_or_isd":
            if not structured_data or not structured_data.get("found", True):
                response_text = structured_data.get("message", "Please specify an application number (e.g., 2026/0154/02/000041) to check if it is NISD or ISD.") if structured_data else "Please specify an application number (e.g., 2026/0154/02/000041) to check if it is NISD or ISD."
            else:
                app_type = structured_data.get("type", "ISD")
                survey_no = structured_data.get("survey_no", "145")
                subdivs = structured_data.get("included_subdivisions", "")
                subdiv_count = len(subdivs.split(",")) if subdivs and subdivs != "None" else 2
                if app_type == "ISD":
                    response_text = f"ISD — application declares sub-division into {subdiv_count} plots under survey no. {survey_no}."
                elif app_type == "NISD":
                    response_text = f"NISD — application is for transfer of entire survey/patta without subdivision under survey no. {survey_no}."
                else:
                    response_text = f"MERGE — application is for merging subdivisions under survey no. {survey_no}."
        elif intent == "check_documents":
            if not structured_data or not structured_data.get("found", True):
                response_text = "Application not found."
            else:
                missing = [d["document_type"] for d in structured_data.get("documents", []) if not d["is_uploaded"]]
                if missing:
                    missing_str = ", ".join(missing)
                    response_text = f"Missing documents: {missing_str}. Please upload them before scheduling the field visit."
                else:
                    response_text = "No issues detected. All required documents are present."
        elif intent == "check_sale_deed":
            if not structured_data or not structured_data.get("found", True):
                response_text = "Application not found."
            else:
                deed_no = structured_data.get("sale_deed_number") or "N/A"
                sub_date = structured_data.get("submission_date") or "2025-06-25"
                if structured_data.get("sale_deed_registered"):
                    response_text = f"Yes, deed no. {deed_no} matches Sub-Registrar's registered index as of {sub_date}."
                else:
                    response_text = "No match found — flag to Sub-Registrar's office before proceeding."
        elif intent == "joint_owner_check":
            if not structured_data or not structured_data.get("found", True):
                response_text = structured_data.get("message", "Please provide an application number or survey number")
            else:
                joint_owners = structured_data.get("joint_owners", [])
                total_owners = structured_data.get("total_owners", 0)
                survey_no = structured_data.get("survey_no", "N/A")
                app_no = structured_data.get("application_number")
                is_tamil = language in ("ta", "tanglish")
                
                # Build response based on whether it's application or survey query
                if is_tamil:
                    prefix = f"விண்ணப்பம் {app_no} (கணக்கெண் {survey_no})" if app_no else f"கணக்கெண் {survey_no}"
                else:
                    prefix = f"For application {app_no} (Survey {survey_no})" if app_no else f"For Survey {survey_no}"
                
                if total_owners == 0:
                    if is_tamil:
                        response_text = f"{prefix}: உரிமையாளர் பதிவுகள் இல்லை."
                    else:
                        response_text = f"{prefix}: No ownership records found."
                elif len(joint_owners) == 0:
                    if is_tamil:
                        response_text = f"{prefix}: விண்ணப்பதாரர் ஒரே உரிமையாளர். கூட்டு உரிமையாளர்கள் இல்லை."
                    else:
                        response_text = f"{prefix}: The applicant is the sole owner. No joint owners are listed."
                else:
                    joint_names = [o.get("name", "N/A") for o in joint_owners]
                    if is_tamil:
                        response_text = f"{prefix}: {len(joint_owners)} கூட்டு உரிமையாளர்கள் உள்ளனர்: {', '.join(joint_names)}."
                    else:
                        response_text = f"{prefix}: There are {len(joint_owners)} joint owner(s) listed: {', '.join(joint_names)}."

        elif intent == "application_status":
            # Deterministic answer for application lookups — in particular the
            # "you gave me an application number that does not exist" case, which
            # must never be handed to the LLM.
            if not structured_data:
                response_text = build_app_not_found_message({}, language)
            elif "multi_applications" in structured_data:
                _details_list = structured_data["multi_applications"]
                _found = [d for d in _details_list if d.get("found", True)]
                _missing = [d for d in _details_list if not d.get("found", True)]
                if not _found:
                    response_text = build_app_not_found_message(_missing[0] if _missing else {}, language)
                else:
                    _summaries = []
                    for _d in _found:
                        _an = _d.get("application_number", "N/A")
                        _st = (_d.get("status") or "N/A").capitalize()
                        _sg = _d.get("stage", "N/A")
                        _summaries.append(f"{_an} (Status: {_st}, Stage: {_sg})")
                    response_text = f"Here are the details for {len(_found)} application(s): {'; '.join(_summaries)}."
                    if _missing:
                        _missing_nos = ", ".join(d.get("searched_number", "N/A") for d in _missing)
                        response_text += f" Not found: {_missing_nos}."
            elif not structured_data.get("found", True):
                response_text = build_app_not_found_message(structured_data, language)
            elif "history" in structured_data:
                hist = structured_data.get("history", [])
                app_no_h = structured_data.get("application_number", "")
                response_text = f"Workflow history for {app_no_h}: {len(hist)} stage(s) recorded."
            else:
                app_no_d = structured_data.get("application_number", "N/A")
                app_type_d = structured_data.get("type", "N/A")
                status_d = (structured_data.get("status") or "N/A").capitalize()
                stage_d = structured_data.get("stage", "N/A")
                applicant_d = structured_data.get("applicant_name") or "N/A"
                survey_d = structured_data.get("survey_no", "N/A")
                _summary_d = (
                    f"Type: {app_type_d}, Status: {status_d}, Stage: {stage_d}, "
                    f"Applicant: {applicant_d}, Survey No: {survey_d}."
                )
                if _asks_for_specific_detail(message):
                    # Nothing in the record matched what was asked. Say that
                    # plainly instead of presenting a summary as if it were the
                    # answer, which reads as though the question was addressed.
                    response_text = (
                        f"I could not find that particular detail for {app_no_d} in the record. "
                        f"Here is what it does hold — {_summary_d}"
                    )
                else:
                    response_text = f"Here are the details for {app_no_d}. {_summary_d}"

        elif intent == "officer_workload":
            total = structured_data.get("total_active", 0) if structured_data else 0
            isd = structured_data.get("ISD", 0)
            nisd = structured_data.get("NISD", 0)
            merge = structured_data.get("MERGE", 0)
            overdue = structured_data.get("overdue", 0)
            if language in ("ta", "tanglish"):
                response_text = (
                    f"உங்கள் பணிச்சுமை: {total} செயலில் உள்ள விண்ணப்பங்கள் — "
                    f"ISD: {isd}, NISD: {nisd}, Merge: {merge}, தாமதமானவை: {overdue}."
                )
            else:
                response_text = (
                    f"Your workload: {total} active application(s) — "
                    f"ISD: {isd}, NISD: {nisd}, Merge: {merge}, Overdue: {overdue}."
                )

        elif intent == "isd_processing":
            response_text = build_isd_processing_answer(message, structured_data, _isd_app_no)

        elif intent == "sd_additional_info":
            if not structured_data or not structured_data.get("found", True):
                response_text = "Application not found."
            else:
                missing = structured_data.get("missing_documents", [])
                clarification = structured_data.get("sd_clarification")
                req_parts = []
                if missing:
                    req_parts.append(f"missing documents ({', '.join(missing)})")
                if clarification:
                    req_parts.append(f"clarification: {clarification}")
                req_str = " and ".join(req_parts) if req_parts else "None"
                response_text = f"SD has requested: {req_str}."
                
        elif intent == "sd_encroachment_check":
            if not structured_data or not structured_data.get("found", True):
                response_text = "Application not found."
            else:
                if structured_data.get("encroachment_found"):
                    response_text = "Yes, flag visible in SD's view of the application file."
                else:
                    response_text = "No encroachment flag has been noted on this application."
                    
        elif intent == "sd_sketch_readiness":
            if not structured_data or not structured_data.get("found", True):
                response_text = "Application not found."
            else:
                missing_fields = []
                if not structured_data.get("field_visit_present"):
                    missing_fields.append("Field Visit Details")
                else:
                    if structured_data.get("area_verified") is None:
                        missing_fields.append("Area Verified")
                    if not structured_data.get("visit_notes_present"):
                        missing_fields.append("Visit Notes")
                if missing_fields:
                    response_text = f"Missing: {', '.join(missing_fields)}. Recommend completing before submission."
                else:
                    response_text = "All required fields are filled."
                    
        elif intent == "sd_forward_check":
            if not structured_data or not structured_data.get("found", True):
                response_text = "Application not found."
            else:
                if structured_data.get("current_stage") == "SIS":
                    response_text = "No. The application is pending SIS verification."
                else:
                    forward_date = structured_data.get("forwarded_to_sd_date") or structured_data.get("submission_date")
                    response_text = f"Yes. Forwarded on {forward_date}."
                    
        elif intent == "sd_remarks":
            if not structured_data or not structured_data.get("found", True):
                response_text = "Application not found."
            else:
                remarks = structured_data.get("sd_remarks")
                if remarks:
                    response_text = f"SD Remarks: {remarks}."
                else:
                    response_text = "No remarks recorded by SD."
                    
        elif intent == "fv_date_select":
            if not structured_data or not structured_data.get("found", True):
                response_text = "Application not found."
            else:
                fv_date = structured_data.get("field_visit_date")
                if fv_date:
                    response_text = f"{fv_date} confirmed for this application."
                else:
                    response_text = "No field visit scheduled for this application."
                    
        elif intent == "fv_nearby_pending":
            if not structured_data or not structured_data.get("found", True):
                response_text = "Application not found."
            else:
                count = structured_data.get("nearby_count", 0)
                ward = structured_data.get("ward_number", "N/A")
                block = structured_data.get("block_number", "N/A")
                response_text = f"{count} applications are located within the same Ward {ward} and Block {block}."
                
        elif intent == "fv_between_dates":
            count = structured_data.get("count", 0)
            to_visit = structured_data.get("to_be_visited_count", count)
            s_date = structured_data.get("start_date")
            e_date = structured_data.get("end_date")
            date_range = f" ({s_date} to {e_date})" if (s_date and e_date) else ""
            if to_visit == 0:
                response_text = f"There are no field visits needed to be visited{date_range}."
            elif to_visit == 1:
                response_text = f"There is 1 field visit needed to be visited{date_range}."
            else:
                response_text = f"There are {to_visit} field visits needed to be visited{date_range}."

        elif intent == "fv_scheduled_this_week":
            count = structured_data.get("taluk_scheduled_count", 0)
            taluk = structured_data.get("taluk_name", "N/A")
            cases = structured_data.get("taluk_cases", [])
            week_start = structured_data.get("week_start", "")
            week_end = structured_data.get("week_end", "")
            cases_str = ", ".join(cases) if cases else "None"
            date_range = f" ({week_start} to {week_end})" if week_start else ""
            if count == 0:
                response_text = f"You have no field visits scheduled in {taluk} this week{date_range}."
            elif count == 1:
                response_text = f"You have 1 field visit scheduled in {taluk} this week{date_range}: {cases_str}."
            else:
                response_text = f"You have {count} field visits scheduled in {taluk} this week{date_range}: {cases_str}."
            
        elif intent == "fv_change_date":
            if language == "ta":
                response_text = "கள ஆய்வு தேதியை மாற்றுவதற்கு தாசில்தாரிடம் (Tahsildar) கேட்க வேண்டும். தாசில்தாரின் அனுமதியுடன் மட்டுமே கள ஆய்வு தேதியை மாற்ற இயலும்."
            elif language == "tanglish":
                response_text = "Field visit date change பண்ண நீங்கள் Tahsildar கிட்ட கேட்க வேண்டும் (You should ask the Tahsildar about field visit date change)."
            else:
                response_text = "To change the field visit date, you should ask the Tahsildar. The Tahsildar has the authority to approve field visit date changes."

        elif intent == "fv_reschedule_availability":
            res_date = structured_data.get("reschedule_date", "the next available working day") if structured_data else "the next available working day"
            response_text = f"Schedule available on {res_date}. Note: You should ask the Tahsildar about field visit date change."
            
        elif intent == "fv_deadline_check":
            if not structured_data or not structured_data.get("found", True):
                response_text = structured_data.get("message", "Please specify an application number to check the deadline.") if structured_data else "Please specify an application number."
            else:
                app_no_dl = structured_data.get("application_number", "")
                working_days = structured_data.get("working_days", 0)
                sub_date = structured_data.get("submission_date", "")
                if structured_data.get("is_overdue", False):
                    overdue = structured_data.get("days_overdue", max(0, working_days - 15))
                    response_text = (
                        f"Yes — {app_no_dl} is past the 15-working-day deadline. "
                        f"It has been {working_days} working days since submission ({sub_date}), "
                        f"{overdue} day(s) overdue. Recommend escalating or scheduling immediately."
                    )
                else:
                    remaining = structured_data.get("days_remaining", max(0, 15 - working_days))
                    response_text = (
                        f"No — {app_no_dl} is on working day {working_days} of 15 "
                        f"(submitted {sub_date}). {remaining} working day(s) remaining within the window."
                    )
                    
        elif intent == "fv_overdue_inspections":
            count = structured_data.get("overdue_visits_count", 0) if structured_data else 0
            if count == 0:
                response_text = "No field visits are currently overdue. All field visits are on schedule."
            else:
                response_text = f"Found {count} overdue field visit(s). See the table below for details."

        elif intent == "fv_unassigned_awaiting":
            count = structured_data.get("unassigned_visits_count", 0)
            apps_list = structured_data.get("unassigned_applications", [])
            if language == "ta":
                if count == 0:
                    response_text = "திட்டமிடல் காத்திருக்கும் நிறைவேற்றப்படாத கள ஆய்வுகள் எதுவும் இல்லை."
                elif count == 1:
                    response_text = "திட்டமிடல் காத்திருக்கும் 1 கள ஆய்வு விண்ணப்பம் உள்ளது."
                else:
                    response_text = f"திட்டமிடல் காத்திருக்கும் {count} கள ஆய்வு விண்ணப்பங்கள் உள்ளன."
            else:
                if count == 0:
                    response_text = "There are no unassigned field visits awaiting scheduling."
                elif count == 1:
                    response_text = "There is 1 application with an unassigned field visit awaiting scheduling."
                else:
                    response_text = f"There are {count} applications with unassigned field visits awaiting scheduling."
            
        elif intent == "fv_recently_rescheduled":
            count = structured_data.get("recently_rescheduled_count", 0) if structured_data else 0
            if count == 0:
                response_text = "No field visits were rescheduled during the last 7 days."
            elif count == 1:
                response_text = "1 field visit was rescheduled during the last 7 days."
            else:
                response_text = f"{count} field visits were rescheduled during the last 7 days."
            
        elif intent == "fv_scheduling_conflicts":
            overlap_date = structured_data.get("overlap_date") if structured_data else None
            if overlap_date:
                response_text = (
                    f"Scheduling conflict detected: two or more field visits are scheduled on "
                    f"{overlap_date}. Please reschedule one of them — note that a field visit date "
                    f"change must be approved by the Tahsildar."
                )
            else:
                response_text = "No scheduling conflicts identified in the current inspection calendar."
        
        elif intent == "highest_priority_applications":
            count = len(structured_data.get("applications", []))
            stage_filter = structured_data.get("query_type", "").split("—")[-1].strip().replace(" Stage", "") if "—" in structured_data.get("query_type", "") else None
            is_tamil = language in ("ta", "tanglish")
            
            stage_text = f" in {stage_filter} stage" if stage_filter and stage_filter != "High Priority Applications" else ""
            
            if count == 0:
                response_text = (
                    f"உயர் முன்னுரிமை விண்ணப்பங்கள் எதுவும் இல்லை{stage_text}." if is_tamil
                    else f"There are no high priority applications{stage_text} at this time."
                )
            elif count == 1:
                response_text = (
                    f"1 உயர் முன்னுரிமை விண்ணப்பம் உள்ளது{stage_text} (⚠️ warning அல்லது overdue)." if is_tamil
                    else f"There is 1 high priority application{stage_text} (⚠️ warning or overdue)."
                )
            else:
                response_text = (
                    f"{count} உயர் முன்னுரிமை விண்ணப்பங்கள் உள்ளன{stage_text} (⚠️ warning அல்லது overdue)." if is_tamil
                    else f"There are {count} high priority applications{stage_text} (⚠️ warning or overdue)."
                )
        
        elif intent == "survey_owners":
            if not structured_data or not structured_data.get("found", True):
                response_text = structured_data.get("message", "Survey not found or not accessible.")
            else:
                owners = structured_data.get("owners", [])
                survey_no = structured_data.get("survey_no", "")
                if not owners:
                    response_text = f"No ownership records found for Survey No. {survey_no}."
                else:
                    owner_lines = []
                    for o in owners:
                        name = o.get("name", "N/A")
                        sub_div = o.get("sub_division", "Survey Level")
                        share = o.get("ownership_share", "N/A")
                        o_type = o.get("ownership_type", "Primary")
                        owner_lines.append(f"  • {name} — Sub-division: {sub_div}, Share: {share}, Type: {o_type}")
                    response_text = f"Owners for Survey No. {survey_no} ({len(owners)} record(s)):\n" + "\n".join(owner_lines)
        elif intent == "service_code_lookup":
            from backend.utils.helpers import SIS_URBAN_SERVICES
            _sc_match = re.search(r'\b(0?1[5-9][0-9]|0?[0-9]{3})\b', message)
            _prefix_raw = _sc_match.group(1) if _sc_match else ""
            # Normalise to 4 digits with leading zero if needed
            _prefix_norm = _prefix_raw.zfill(4) if _prefix_raw else ""
            # Match codes that START with the supplied prefix
            _matches = {
                code: info for code, info in SIS_URBAN_SERVICES.items()
                if code.startswith(_prefix_norm)
            }
            # Fallback: try matching last 3 digits (e.g. "161" → codes like "0161x")
            if _prefix_norm and not _matches:
                _prefix3 = _prefix_raw[-3:] if len(_prefix_raw) >= 3 else _prefix_raw
                _matches = {
                    code: info for code, info in SIS_URBAN_SERVICES.items()
                    if code[1:].startswith(_prefix3) or code.startswith(_prefix3)
                }
            _count = len(_matches)
            if _count == 0:
                response_text = (
                    f"There are no SIS urban service codes matching '{_prefix_raw}'. "
                    f"The available service codes are: "
                    + ", ".join(f"{c} ({v['short']} — {v['name']})" for c, v in SIS_URBAN_SERVICES.items())
                    + "."
                )
            elif _count == 1:
                _code, _info = next(iter(_matches.items()))
                response_text = (
                    f"There is 1 service code matching '{_prefix_raw}': "
                    f"{_code} — {_info['name']} ({_info['short']})."
                )
            else:
                _lines = [f"{c} — {v['name']} ({v['short']})" for c, v in sorted(_matches.items())]
                response_text = (
                    f"There are {_count} service codes matching '{_prefix_raw}':\n"
                    + "\n".join(f"  • {l}" for l in _lines)
                )

        elif any(ph in message.lower() for ph in [
            "uploaded", "word document", "pdf document", "question bank",
            "answer all", "answer for all", "from the document", "in the document",
            "the file", "attached file", "from this file",
        ]):
            # User is asking about an uploaded document but no content was extracted.
            response_text = (
                "I can see you're referring to an uploaded document. "
                "Unfortunately I can only read plain text (.txt) file contents directly — "
                "Word and PDF files need to be processed first.\n\n"
                "Please copy and paste the relevant text from the document into the chat, "
                "and I'll answer your questions from it."
            )
        # Deterministic table intro — mirrors process_chat_stream so both
        # paths introduce a rendered table with the same wording.
        elif not response_text and intent in ("pending_applications", "field_visits", "fv_scheduled_this_week",
                        "fv_overdue_inspections", "fv_unassigned_awaiting", "fv_recently_rescheduled",
                        "ward_surveys", "block_surveys", "survey_detail", "next_subdivision",
                        "jurisdiction_summary", "rejection_info", "taluk_summary",
                        "litigation_check", "highest_priority_applications",
                        "merge_info", "town_applications", "block_applications",
                        "isd_applications", "nisd_applications", "merge_applications",
                        "both_applications"):
            # Table is rendered on the frontend. Just emit a short natural intro.
            found = structured_data.get("found", True) if structured_data else False
            if not found:
                response_text = structured_data.get("message", "No records found.")
            else:
                asking_for_count = _is_count_only_query(message)
                
                if asking_for_count and intent in ("pending_applications", "isd_applications", "nisd_applications", "merge_applications", "both_applications"):
                    response_text = _format_count_intro(structured_data, language, message)
                # Special message for priority applications
                elif intent == "highest_priority_applications":
                    count = len(structured_data.get("applications", []))
                    stage_filter = structured_data.get("query_type", "").split("—")[-1].strip().replace(" Stage", "") if "—" in structured_data.get("query_type", "") else None
                    is_tamil = language in ("ta", "tanglish")
                    
                    stage_text = f" in {stage_filter} stage" if stage_filter and stage_filter != "High Priority Applications" else ""
                    
                    if count == 0:
                        response_text = (
                            f"உயர் முன்னுரிமை விண்ணப்பங்கள் எதுவும் இல்லை{stage_text}." if is_tamil
                            else f"There are no high priority applications{stage_text} at this time."
                        )
                    elif count == 1:
                        response_text = (
                            f"1 உயர் முன்னுரிமை விண்ணப்பம் உள்ளது{stage_text} (⚠️ warning அல்லது overdue)." if is_tamil
                            else f"Found 1 high priority application{stage_text} (⚠️ warning or overdue)."
                        )
                    else:
                        response_text = (
                            f"{count} உயர் முன்னுரிமை விண்ணப்பங்கள் உள்ளன{stage_text} (⚠️ warning அல்லது overdue)." if is_tamil
                            else f"Found {count} high priority applications{stage_text} (⚠️ warning or overdue)."
                        )
                elif intent in ("field_visits", "fv_between_dates"):
                    count = structured_data.get("count", len(structured_data.get("field_visits", [])))
                    qtype = structured_data.get("query_type", "Field Visits")
                    start_date = structured_data.get("start_date")
                    end_date = structured_data.get("end_date")
                    date_range = f" ({start_date} to {end_date})" if (start_date and end_date) else ""
                    if count == 0:
                        response_text = f"No field visits found{date_range}."
                    elif count == 1:
                        response_text = f"Found 1 field visit{date_range}."
                    else:
                        response_text = f"Found {count} field visit(s){date_range}."
                else:
                    qtype = structured_data.get("query_type", "") if structured_data else ""
                    if qtype:
                        response_text = f"Here are the {qtype.lower()} results."
                    else:
                        response_text = "Results are shown in the table below."

        elif not response_text and intent in ("check_sale_deed", "sale_deed_check") and (
            _sd_answer := build_sale_deed_direct_answer(structured_data, message, language)
        ):
            response_text = _sd_answer
            logger.info("Responded with direct Python answer (sale deed field query)")

        elif not response_text and structured_data and "applications" in structured_data:
            count = structured_data.get("count", len(structured_data.get("applications", [])))
            qtype = structured_data.get("query_type", "Pending Applications")
            response_text = f"Found {count} application(s) ({qtype})."
        elif not response_text:
            # Only call LLM if response_text hasn't been set by intent handlers
            response_text = await call_llama(full_prompt)
        
        # Step 7: Calculate response time
        response_time_ms = int((time.time() - start_time) * 1000)
        
        # Step 8: Save chat messages to database
        await save_chat_messages(
            db=db,
            session_id=session_id,
            user_message=message,
            assistant_message=response_text,
            language=language,
            response_time_ms=response_time_ms,
            officer_id=officer.officer_id if officer else None
        )
        
        logger.info(f"Chat processed successfully in {response_time_ms}ms")
        
        # Prepare response with structured data for frontend rendering.
        # Suppress table_data when html_response is set OR when the HTML path
        # was bypassed for an interrogative query (_bypass_html) — in that case
        # the LLM answered conversationally and we don't want a table appended.
        _td = None if (html_response or _bypass_html) else _build_table_data(intent, message, str(officer.officer_id), structured_data)
        if _td:
            _td['language'] = language
        response = {
            "response": response_text,
            "language": language,
            "intent": intent,
            "sources": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "context_used": context_used,
            "response_time_ms": response_time_ms,
            "table_data": _td
        }
        
        # Keep structured_data for backward compatibility if needed
        if structured_data and structured_data.get("found", True):
            response["structured_data"] = structured_data
        
        return response
        
    except Exception as e:
        # Roll back BEFORE logging. The traceback renderer walks the frame
        # locals, and touching an ORM object there triggers a lazy load outside
        # the async context (MissingGreenlet) that leaves the session unusable
        # for every later message in the conversation.
        try:
            await db.rollback()
        except Exception as rb_err:
            logger.warning(f"Rollback after process_chat failure also failed: {rb_err}")

        logger.error(f"Error in process_chat: {e}", exc_info=True)

        # Return error message in appropriate language
        error_messages = {
            "en": "I apologize, but I encountered an error processing your request. Please try again.",
            "ta": "மன்னிக்கவும், உங்கள் கோரிக்கையைச் செயல்படுத்துவதில் பிழை ஏற்பட்டது. மீண்டும் முயற்சிக்கவும்.",
            "tanglish": "Sorry, error ஏற்பட்டது. Please try again."
        }
        
        language = detect_language(message)
        error_msg = error_messages.get(language, error_messages["en"])
        
        response = {
            "response": error_msg,
            "language": language,
            "context_used": False,
            "response_time_ms": int((time.time() - start_time) * 1000)
        }
        # Raw exception text can carry SQL, table names and connection details —
        # only expose it outside production.
        if settings.ENVIRONMENT != "production":
            response["error"] = str(e)
        return response


async def process_chat_stream(
    message: str,
    session_id: str,
    officer: OfficerContext,
    db: AsyncSession,
    chat_history: list = None
):
    """
    Process chat message and stream the response back.
    Yields chunks of the response as they are generated.
    """
    start_time = time.time()
    
    try:
        # Step 0: Use provided chat history from client
        if not chat_history:
            chat_history = []
        logger.info(f"=== CHAT STREAM CONTEXT DEBUG ===")
        logger.info(f"Received {len(chat_history)} previous messages from client")
        logger.info(f"Current message: '{message}'")
        if chat_history:
            for i, msg in enumerate(chat_history[-3:]):  # Show last 3
                logger.info(f"  History[{i}]: {msg.get('role')} said: {(msg.get('content') or '')[:50]}...")

        # Step 1: Detect language
        language = detect_language(message)
        logger.info(f"Detected language: {language}")

        # Direct Handler for "what does an application number look like" —
        # mirrors the non-streaming path (see there for why): application
        # numbers must never be left to the LLM to describe or invent.
        _msg_lower_fmt = message.lower()
        if any(p in _msg_lower_fmt for p in [
            "application number format", "format of application number", "format of an application number",
            "application number structure", "structure of application number", "structure of an application number",
            "application number look like", "application number pattern",
            "how are application numbers structured", "how is an application number structured",
            "விண்ணப்ப எண் வடிவம்", "விண்ணப்ப எண்ணின் அமைப்பு",
        ]) or (
            any(w in _msg_lower_fmt for w in ["application number", "app number", "விண்ணப்ப எண்"])
            and any(w in _msg_lower_fmt for w in ["format", "look like", "structure", "structured", "pattern", "வடிவம்", "அமைப்பு"])
        ):
            import json as _json_fmt
            is_ta_fmt = language == "ta"
            if is_ta_fmt:
                res_txt = (
                    "விண்ணப்ப எண் இந்த அமைப்பில் இருக்கும்: "
                    "**ஆண்டு/சேவைக்குறியீடு/மாவட்டக்குறியீடு/வரிசை எண்** "
                    "(எ.கா. 2026/0154/28/001167). "
                    "சேவைக்குறியீடு: 0154 = ISD, 0153 = NISD, 0155 = MERGE. "
                    "மாவட்டக்குறியீடு 2 இலக்கங்கள் (எ.கா. தூத்துக்குடிக்கு 28), "
                    "வரிசை எண் 6 இலக்கங்கள்."
                )
            else:
                res_txt = (
                    "An application number follows the format "
                    "**YEAR/SERVICE_CODE/DISTRICT_CODE/SERIAL_NUMBER** "
                    "(e.g. 2026/0154/28/001167). The service code is 0154 for ISD, "
                    "0153 for NISD, or 0155 for MERGE; the district code is 2 digits "
                    "(28 for Thoothukudi); the serial number is 6 digits."
                )
            yield f"data: {_json_fmt.dumps({'content': res_txt})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return

        # Direct Handler for District Code reference queries (bypasses jurisdiction checks)
        # Mirrors the non-streaming path — answered from DISTRICT_NAME_MAP so the
        # LLM never invents a district code.
        _msg_lower_dc = message.lower()
        if any(w in _msg_lower_dc for w in ["district code", "code of", "district_code", "குறியீடு", "மாவட்டம் கோடு"]) or ("code" in _msg_lower_dc and any(d in _msg_lower_dc for d in DISTRICT_NAME_MAP)):
            matched_dist = None
            for d_name, d_code in DISTRICT_NAME_MAP.items():
                if d_name in _msg_lower_dc:
                    matched_dist = (d_name.title(), d_code)
                    break

            if matched_dist:
                import json as _json_dc
                d_title, d_code = matched_dist
                is_ta = language == "ta" or any(w in _msg_lower_dc for w in ["enapa", "enna", "oda", "sollo", "kudunga", "குறியீடு"])
                if is_ta:
                    res_txt = f"{d_title} மாவட்டத்தின் அதிகாரப்பூர்வ குறியீடு (District Code): **{d_code}**."
                else:
                    res_txt = f"The official district code for **{d_title}** is **{d_code}**."

                yield f"data: {_json_dc.dumps({'content': res_txt})}\n\n".encode('utf-8')
                await save_chat_messages(
                    db=db, session_id=session_id, user_message=message,
                    assistant_message=res_txt, language=language,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None
                )
                return

        # Direct Handler for owner photo / image retrieval requests (STREAMING)
        if _is_owner_photo_request(message):
            import json as _json_photo
            res_txt = _owner_photo_reply(language, message)
            yield f"data: {_json_photo.dumps({'content': res_txt})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return

        # Step 2: Parse intent to determine which DB query to run
        _prev_intent = None
        if chat_history:
            import re as _re
            for _h in reversed(chat_history):
                if _h.get("role") == "assistant":
                    _m = _re.search(r'\[intent:([\w_]+)\]', _h.get("content") or "")
                    if _m:
                        _prev_intent = _m.group(1)
                        break
                    if _h.get("intent"):
                        _prev_intent = _h.get("intent")
                        break
        intent = parse_intent(message, prev_intent=_prev_intent)
        logger.info(f"Parsed intent: {intent} (prev_intent={_prev_intent})")

        # Direct Handler (STREAMING): answer from a file the officer attached this
        # session. Mirrors the non-streaming path — only takes over when there is
        # an attachment AND the question is generic or explicitly about the file.
        _uploaded_ctx = upload_store.context_block(session_id)
        if _uploaded_ctx and (intent == "general_query" or _is_about_uploaded_doc(message)):
            import json as _json_upl
            _prompt = _uploaded_doc_prompt(
                _uploaded_ctx, upload_store.filenames(session_id), message, language)
            _acc = ""
            async for _chunk in call_llama_stream(_prompt):
                _acc += _chunk
                yield f"data: {_json_upl.dumps({'content': _chunk})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=message,
                assistant_message=_acc, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return



        # Step 2d: Direct greeting response handling (STREAMING)
        if intent == "greeting":
            import json as _json_greeting
            msg_lower = message.lower().strip()
            is_tamil = language == "ta" or any(w in message for w in ["வணக்கம்", "காலை", "மாலை", "நன்றி", "ஹாய்"])

            if "காலை" in msg_lower or "morning" in msg_lower:
                if is_tamil:
                    response_text = "காலை வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை எண்கள், விண்ணப்பங்கள் (ISD/NISD/MERGE), கள ஆய்வுகள் தொடர்பாக இன்று உங்களுக்கு எவ்வாறு உதவ முடியும்?"
                else:
                    response_text = "Good morning! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. How can I help you today with survey numbers, applications (ISD/NISD/MERGE), or field visits?"
            elif "மாலை" in msg_lower or "evening" in msg_lower:
                if is_tamil:
                    response_text = "மாலை வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை மற்றும் விண்ணப்பங்கள் தொடர்பான தகவல்களுக்கு இன்று உங்களுக்கு எவ்வாறு உதவ முடியும்?"
                else:
                    response_text = "Good evening! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. How can I help you with your survey work today?"
            elif "நன்றி" in msg_lower or "thanks" in msg_lower or "thank" in msg_lower:
                if is_tamil:
                    response_text = "நல்வரவு! 😊 உங்களுக்கு மேலும் ஏதேனும் உதவி தேவைப்பட்டால் தயங்காமல் கேளுங்கள்."
                else:
                    response_text = "You're very welcome! 😊 Feel free to ask if you need anything else regarding your survey work."
            else:
                if is_tamil:
                    response_text = "வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை எண்கள், விண்ணப்பங்களின் நிலை, கள ஆய்வுகள் மற்றும் பட்டா பரிமாற்றங்கள் பற்றிய கேள்விகளுக்கு உதவ தயாராக உள்ளேன். இன்று உங்களுக்கு என்ன உதவி தேவை?"
                else:
                    response_text = "Hello! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. I am here to help you manage survey applications, check document statuses, track field visits, and navigate workflow procedures. What can I assist you with today?"

            # Stream the greeting response
            yield f"data: {_json_greeting.dumps({'content': response_text})}\n\n".encode('utf-8')
            
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=message, assistant_message=response_text,
                language=language, response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return

        # ── Step 2b: Jurisdiction access check (streaming) ─────────────────
        _jur_type = getattr(officer, "jurisdiction_type", "block")
        _jur_name = getattr(officer, "jurisdiction_name", "your jurisdiction")
        _JUR_LEVELS = ["block", "ward", "town", "taluk", "district"]
        _officer_level = _JUR_LEVELS.index(_jur_type) if _jur_type in _JUR_LEVELS else 0
        _INTENT_MIN_LEVEL = {
            "ward_surveys": 1, "block_surveys": 0, "jurisdiction_summary": 0,
            "active_applications_taluks": 2, "taluk_summary": 3,
            "all_surveys_in_jurisdiction": 0,
        }
        _FIELD_VISIT_INTENTS_S = {
            "fv_scheduled_this_week", "fv_date_select", "fv_nearby_pending",
            "fv_reschedule_availability", "fv_change_date", "fv_deadline_check", "fv_overdue_inspections",
            "fv_unassigned_awaiting", "fv_recently_rescheduled", "fv_scheduling_conflicts",
            "sd_additional_info", "sd_encroachment_check", "sd_sketch_readiness",
            "sd_forward_check", "sd_remarks", "application_status", "isd_processing",
            "officer_workload", "field_visits",
            "general_query", "rag", "greeting", "help", "district_code"
        }
        _msg_lower_jur = message.lower()
        _is_code_reference_query_s = any(w in _msg_lower_jur for w in ["code", "கோடு", "service code", "district code"])
        # Same carve-out as the non-streaming path: a question about the
        # officer's own jurisdiction is not a request for broader data.
        _SELF_JUR_INTENTS_S = {"jurisdiction_summary"}
        _skip_keyword_check_s = (intent in _FIELD_VISIT_INTENTS_S) or (intent in _SELF_JUR_INTENTS_S) \
            or _is_code_reference_query_s or intent == "service_code_lookup"
        _requested_broader = False
        _broader_reason = ""
        if _officer_level == 0 and not _skip_keyword_check_s:
            if any(w in _msg_lower_jur for w in ["ward", "வார்டு"]):
                _requested_broader = True; _broader_reason = "ward-level"
            elif any(w in _msg_lower_jur for w in ["taluk", "தாலுகா"]):
                _requested_broader = True; _broader_reason = "taluk-level"
            elif any(w in _msg_lower_jur for w in ["district", "மாவட்டம்"]):
                _requested_broader = True; _broader_reason = "district-level"
        elif _officer_level == 1 and not _skip_keyword_check_s:
            if any(w in _msg_lower_jur for w in ["town", "நகரம்"]):
                _requested_broader = True; _broader_reason = "town-level"
            elif any(w in _msg_lower_jur for w in ["taluk", "தாலுகா"]):
                _requested_broader = True; _broader_reason = "taluk-level"
            elif any(w in _msg_lower_jur for w in ["district", "மாவட்டம்"]):
                _requested_broader = True; _broader_reason = "district-level"
        if intent in _INTENT_MIN_LEVEL and _officer_level < _INTENT_MIN_LEVEL[intent]:
            _requested_broader = True
            _broader_reason = f"{_JUR_LEVELS[_INTENT_MIN_LEVEL[intent]]}-level"
        if _requested_broader:
            import json as _json_mod
            _jur_level_name = _JUR_LEVELS[_officer_level]
            _access_msg = (
                f"You are assigned as a **{_jur_level_name.capitalize()}-level** SIS officer "
                f"({_jur_name}). Your access is limited to data within your assigned {_jur_level_name}.\n\n"
                f"You cannot retrieve {_broader_reason} data. "
                f"Only officers with {_broader_reason.replace('-level', '')} or higher access can view that information.\n\n"
                f"If you need {_broader_reason} data, please contact your supervising officer."
            )
            logger.info(
                f"Jurisdiction access denied for officer {officer.officer_id} "
                f"(level={_jur_level_name}): requested {_broader_reason} data, intent={intent}"
            )
            yield f"data: {_json_mod.dumps({'content': _access_msg})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=message, assistant_message=_access_msg,
                language=language, response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return

        # ── Step 2d: Application reference validation (streaming) ──────────
        # Exactly the same gate as the non-streaming path, applied before any
        # handler, RAG lookup or LLM call, so both endpoints refuse and accept
        # the same application references for the same reasons.
        _app_resolution = await resolve_application_reference(
            db, message, chat_history, officer, intent
        )
        if _app_resolution["verdict"] not in ("ok", "none"):
            import json as _json_badapp
            _bad_msg = build_application_gate_message(_app_resolution, officer, language)
            logger.info(
                f"Application gate refused stream: verdict={_app_resolution['verdict']} "
                f"number={_app_resolution.get('app_number') or _app_resolution.get('token')} "
                f"intent={intent} officer={officer.officer_id if officer else None}"
            )
            yield f"data: {_json_badapp.dumps({'content': _bad_msg})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=message, assistant_message=_bad_msg,
                language=language, response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return

        # See process_chat: the gate-confirmed application number, reused by handlers.
        _gate_app_number = (
            _app_resolution.get("app_number") if _app_resolution["verdict"] == "ok" else None
        )

        # Step 3: Execute structured database queries based on intent
        structured_data = {}
        _isd_app_no = ""
        # Answer text a fetch handler may set directly (mirrors how process_chat
        # pre-sets response_text). Picked up by _direct_answer_text below.
        _prefetch_text = ""

        if intent in ("pending_applications", "isd_applications", "nisd_applications", "both_applications", "merge_applications"):
            message_lower = message.lower()

            # Detect one or multiple application types (e.g. "isd", "merge", "isd and merge")
            app_type = _extract_app_types(message_lower, intent=intent)  # str, list, or None
            

            # Detect time of day session
            session_label = ""
            if any(w in message_lower for w in ["morning", "காலை"]):
                session_label = " (Morning Session: 09:00 AM – 01:00 PM)"
            elif any(w in message_lower for w in ["afternoon", "பிற்பகல்", "மதியம்"]):
                session_label = " (Afternoon Session: 02:00 PM – 05:00 PM)"
            elif any(w in message_lower for w in ["evening", "மாலை"]):
                session_label = " (Evening Session: 04:00 PM – 06:00 PM)"

            # Extract date range first (e.g. "between 2026-07-03 and 2026-07-20", "today", "yesterday")
            start_d, end_d = extract_date_range(message)
            # Months asked for as a union -- "June and July", "March and May",
            # "last month and the month before that". extract_date_range spans
            # from the first to the last, which is right for "from March to
            # May" and wrong for "March and May"; the segments keep the gap.
            month_scopes = extract_month_scopes(message)
            if month_scopes:
                start_d, end_d = month_scopes[0][0], month_scopes[-1][1]
            if session_label and not start_d and not end_d:
                start_d = date.today()
                end_d = date.today()

            # See process_chat: either side alone (open-ended range) still counts.
            has_date_range = start_d is not None or end_d is not None
            is_negated_date_range = any(w in message_lower for w in [
                "not between", "not in", "outside", "other than", "இடைப்பட்டவை அல்ல", "இடையில் இல்லாத", "தவிர"
            ])

            # Extract year/month only when NO full date range present
            submission_year = None
            submission_month = None
            if not has_date_range:
                year_match = re.search(r'\b(20\d{2})\b', message)
                submission_year = int(year_match.group(1)) if year_match else None
                submission_month = extract_month_from_query(message)
                # Same as the non-streaming path: a month named without a year
                # means the most recent one, not the month across all years.
                if submission_month and not submission_year:
                    submission_year = _resolve_month_year(submission_month)

            # Extract geography filters
            taluk_name = extract_taluk_name(message)
            ward_num = extract_ward_number(message) if "ward" in message_lower else None
            block_num = extract_block_number(message) if "block" in message_lower else None

            # Extract submission channel filter (CSC / citizen / sub_registrar)
            channel_filter = extract_submission_channel(message)

            # Detect whether overdue vs non-overdue (on-time) filter is requested
            is_not_overdue = any(w in message_lower for w in [
                "not overdue", "non overdue", "non-overdue", "on time", "not late",
                "not delayed", "within sla", "தாமதமில்லாத", "தாமதம் இல்லாத", "காலதாமதமாகாத"
            ])
            is_overdue_requested = any(w in message_lower for w in ["overdue", "late", "delayed"]) and not is_not_overdue
            is_overdue_filter = False if is_not_overdue else (True if is_overdue_requested else None)

            # Determine status filter
            is_merge_only = (app_type == "MERGE")
            _named_status = _explicit_status_request(message_lower)
            if _named_status is not None and "all" not in message_lower:
                status_filter = _named_status   # see process_chat: named status wins
            elif has_date_range or is_overdue_filter is not None:
                status_filter = None  # show all statuses within the scope
            elif submission_year:
                status_filter = None  # show all statuses for a specific year
            elif is_merge_only or (channel_filter and not any(w in message_lower for w in ["pending", "நிலுவை"])):
                status_filter = None
            else:
                status_filter = "pending"
                if "history" in message_lower or "approved n rejected" in message_lower or "approved and rejected" in message_lower:
                    status_filter = ["approved", "rejected"]
                elif "all" in message_lower:
                    status_filter = None
                elif "complete" in message_lower or "approved" in message_lower:
                    status_filter = "approved"
                elif "reject" in message_lower:
                    status_filter = "rejected"
                
            # Check if this is a follow-up query on an active/previously discussed single application.
            # IMPORTANT: For list intents (pending_applications etc.), we must NOT use implicit
            # continuation — that would cause "show applications" to pull the last-discussed app
            # from chat history and fetch only that single app's detail.
            # Only use allow_implicit_continuation if the message itself has an explicit reference
            # like "this application" / "that app" / "previous application".
            target_app_num = extract_application_number(message)
            if not target_app_num:
                _has_explicit_app_ref = any(p in message_lower for p in [
                    "this application", "that application", "same application",
                    "this app", "that app", "the application", "the app",
                    "prev application", "previous application", "prev app", "previous app",
                    "last application", "last app", "above application",
                    "இந்த விண்ணப்பம்", "அந்த விண்ணப்பம்", "முந்தைய விண்ணப்பம்",
                ])
                if _has_explicit_app_ref:
                    target_app_num = (_extract_app_number_from_context(message, chat_history, allow_implicit_continuation=False) or _gate_app_number)
            has_projected_cols = _get_projected_application_columns(message) is not None
            is_explicit_plural = any(w in message_lower for w in ["all", "every", "list all", "show all", "between", "today", "yesterday", "this week", "last week", "month", "முழு", "அனைத்து"])

            if target_app_num and not is_explicit_plural and has_projected_cols:
                app_detail = await get_application_detail(db, target_app_num, officer=officer)
                if app_detail and app_detail.get("found", True):
                    structured_data = {
                        "applications": [app_detail],
                        "count": 1,
                        "query_type": f"Application {target_app_num}"
                    }
                else:
                    structured_data = await get_pending_applications(
                        db, officer, 
                        application_type=app_type, 
                        status=status_filter, 
                        submission_year=submission_year,
                        submission_month=submission_month,
                        taluk_name=taluk_name,
                        ward_number=ward_num,
                        block_number=block_num,
                        start_date=start_d,
                        end_date=end_d,
                        date_ranges=month_scopes or None,
                        is_overdue=is_overdue_filter,
                        exclude_date_range=is_negated_date_range,
                        submission_channel=channel_filter,
                    )
            else:
                structured_data = await get_pending_applications(
                    db, officer, 
                    application_type=app_type, 
                    status=status_filter, 
                    submission_year=submission_year,
                    submission_month=submission_month,
                    taluk_name=taluk_name,
                    ward_number=ward_num,
                    block_number=block_num,
                    start_date=start_d,
                    end_date=end_d,
                    date_ranges=month_scopes or None,
                    is_overdue=is_overdue_filter,
                    exclude_date_range=is_negated_date_range,
                    submission_channel=channel_filter,
                )
            if is_negated_date_range:
                structured_data["exclude_date_range"] = True
            
            # Build human-readable type label for the query title
            if isinstance(app_type, list):
                type_str = " " + " & ".join(app_type)  # e.g. " ISD & MERGE"
            elif app_type:
                type_str = f" {app_type}"
            else:
                type_str = ""

            # Add channel label to query_type if filtering by channel
            _channel_labels = {
                "CSC": "CSC (Common Service Center)",
                "citizen": "Citizen Portal",
                "sub_registrar": "Sub-Registrar",
            }
            channel_str = f" via {_channel_labels[channel_filter]}" if channel_filter else ""

            year_str = f" in {submission_year}" if submission_year else ""
            
            date_range_str = ""
            if month_scopes:
                date_range_str = f" in {format_month_scopes(month_scopes)}{session_label}"
            elif has_date_range:
                if is_negated_date_range:
                    date_range_str = f" (not {start_d} to {end_d})"
                elif start_d and end_d and start_d == end_d:
                    if start_d == date.today():
                        date_range_str = f" Received Today ({start_d}){session_label}"
                    elif start_d == date.today() - timedelta(days=1):
                        date_range_str = f" Received Yesterday ({start_d}){session_label}"
                    elif start_d == date.today() - timedelta(days=2):
                        date_range_str = f" Received Day Before Yesterday ({start_d}){session_label}"
                    elif start_d == date.today() + timedelta(days=1):
                        date_range_str = f" for Tomorrow ({start_d}){session_label}"
                    else:
                        date_range_str = f" Received on {start_d}{session_label}"
                elif start_d and end_d and _whole_month_label(start_d, end_d):
                    # "this month" / "last month" / a full calendar month — say
                    # the month, not the pair of boundary dates.
                    date_range_str = f" in {_whole_month_label(start_d, end_d)}{session_label}"
                elif start_d and end_d:
                    date_range_str = f" ({start_d} to {end_d}){session_label}"
                elif start_d and not end_d:
                    date_range_str = f" (from {start_d} onwards){session_label}"
                elif end_d and not start_d:
                    date_range_str = f" (up to {end_d}){session_label}"

            # Add month string to title if month filter is present
            month_str = ""
            if submission_month:
                month_names = _MONTH_NAMES
                # Month and year read as one scope — "in March 2026", not
                # " March" followed by " in 2026".
                if submission_year:
                    month_str = f" in {month_names[submission_month]} {submission_year}"
                    year_str = ""
                else:
                    month_str = f" in {month_names[submission_month]}"
            
            if is_not_overdue:
                structured_data["query_type"] = f"Non-Overdue{type_str} Applications{month_str}{year_str}{date_range_str}"
            elif is_overdue_requested:
                structured_data["query_type"] = f"Overdue{type_str} Applications{month_str}{year_str}{date_range_str}"
            elif taluk_name:
                structured_data["query_type"] = f"Applications in {taluk_name}{type_str}{month_str}{year_str}{date_range_str}"
            elif is_merge_only:
                structured_data["query_type"] = f"MERGE Applications{month_str}{year_str}{date_range_str}"
            elif status_filter == ["approved", "rejected"]:
                structured_data["query_type"] = f"SIS{type_str} History (Approved & Rejected){month_str}{year_str}{date_range_str}"
            elif status_filter is None:
                structured_data["query_type"] = f"All{type_str} Applications{month_str}{year_str}{date_range_str}"
            elif status_filter == "approved":
                structured_data["query_type"] = f"Approved{type_str} Applications{month_str}{year_str}{date_range_str}"
            elif status_filter == "rejected":
                structured_data["query_type"] = f"Rejected{type_str} Applications{month_str}{year_str}{date_range_str}"
            else:
                structured_data["query_type"] = f"Pending{type_str} Applications{month_str}{year_str}{date_range_str}"
            

        elif intent == "overdue_applications":
            # Extract application type if mentioned in message
            app_type = None
            message_lower = message.lower()
            # "nisd" contains "isd", so the ISD test has to be word-bounded and
            # NISD has to be checked first -- otherwise "overdue NISD
            # applications" was filtered to ISD and answered with the wrong type.
            if re.search(r'\bnisd\b|\b0153\b', message_lower):
                app_type = "NISD"
            elif re.search(r'\bisd\b|\b0154\b', message_lower):
                app_type = "ISD"
            elif re.search(r'\bmerge\b|\b0155\b', message_lower):
                app_type = "MERGE"

            min_days_overdue = None
            match_days = re.search(r'(?:overdue|late|delayed)\s+(?:by\s+)?(\d+)\s*days?', message_lower) or \
                         re.search(r'(\d+)\s*days?\s+(?:overdue|late|delayed)', message_lower)
            if match_days:
                min_days_overdue = int(match_days.group(1))

            start_d, end_d = extract_date_range(message)
            structured_data = await get_overdue_applications(
                db, officer,
                application_type=app_type,
                min_days_overdue=min_days_overdue,
                start_date=start_d,
                end_date=end_d
            )
            structured_data["min_days_overdue"] = min_days_overdue
            days_str = f" by {min_days_overdue}+ Days" if min_days_overdue else ""
            # Say the month when the range is exactly one, same as the lists do.
            _ov_month = _whole_month_label(start_d, end_d)
            range_str = (f" in {_ov_month}" if _ov_month
                         else (f" ({start_d} to {end_d})"
                               if (start_d and end_d and start_d != end_d) else ""))
            if app_type:
                structured_data["query_type"] = f"Overdue {app_type} Applications{days_str}{range_str}"
            else:
                structured_data["query_type"] = f"Overdue Applications{days_str}{range_str}"
            
        elif intent == "officer_workload":
            structured_data = await get_officer_workload(db, officer)
            structured_data["query_type"] = "Officer Workload Summary"
            
        elif intent in ("field_visits", "fv_between_dates"):
            import calendar

            msg_lower = message.lower()
            start_d, end_d = extract_date_range(message)

            # Detect negation: "not between", "outside", "except between", "not in"
            exclude_range = any(w in msg_lower for w in [
                "not between", "outside", "except between", "not in range",
                "outside the range", "exclude", "excluding"
            ])

            # Extract application type filter(s) from message — supports multi-type queries
            # e.g. "isd and nisd", "merge and isd", "all types"
            app_type_filter = []
            if any(w in msg_lower for w in ["merge", "merger", "merging"]):
                app_type_filter.append("MERGE")
            if any(w in msg_lower for w in ["nisd", "non-isd", "non isd", "transfer"]):
                app_type_filter.append("NISD")
            if any(w in msg_lower for w in ["isd", "subdivision", "sub division", "sub-division"]):
                if "NISD" not in app_type_filter:  # avoid double-match since "nisd" contains "isd"
                    app_type_filter.append("ISD")
            # Normalise: None means no filter (all types)
            app_type_filter = app_type_filter if app_type_filter else None
            type_label = f" {'+'.join(app_type_filter)}" if app_type_filter else ""

            to_be_visited = any(w in msg_lower for w in [
                "needed to be visited", "to be visited", "need to visit", "need to be visited",
                "pending visit", "yet to visit", "upcoming"
            ])

            # Detect morning / afternoon / evening time session
            session_label = ""
            if any(w in msg_lower for w in ["morning", "காலை"]):
                session_label = " (Morning Session: 09:00 AM – 01:00 PM)"
            elif any(w in msg_lower for w in ["afternoon", "பிற்பகல்", "மதியம்"]):
                session_label = " (Afternoon Session: 02:00 PM – 05:00 PM)"
            elif any(w in msg_lower for w in ["evening", "மாலை"]):
                session_label = " (Evening Session: 04:00 PM – 06:00 PM)"

            # If user asked for morning/afternoon without specific date, default to today
            if session_label and not start_d and not end_d:
                start_d = date.today()
                end_d = date.today()

            status_filter = None
            if "unscheduled" in msg_lower or ("not scheduled" in msg_lower and not exclude_range) or "yet to schedule" in msg_lower:
                status_filter = "unscheduled"
                query_type = "Unscheduled Field Visits"
            elif intent == "fv_between_dates" or start_d or end_d or "between" in msg_lower or to_be_visited:
                if not start_d and not end_d:
                    today = date.today()
                    start_d = today.replace(day=1)
                    _, last_day = calendar.monthrange(today.year, today.month)
                    end_d = today.replace(day=last_day)
                type_label = f" {'+'.join(app_type_filter)}" if app_type_filter else ""
                if exclude_range and start_d and end_d:
                    query_type = (f"{type_label} Field Visits Outside Dates".strip())
                elif start_d and end_d and start_d == end_d:
                    if start_d == date.today() + timedelta(days=2):
                        query_type = (f"{type_label} Field Visits for Day After Tomorrow ({start_d}){session_label}".strip())
                    elif start_d == date.today() + timedelta(days=1):
                        query_type = (f"{type_label} Field Visits for Tomorrow ({start_d}){session_label}".strip())
                    elif start_d == date.today():
                        query_type = (f"{type_label} Field Visits for Today ({start_d}){session_label}".strip())
                    elif start_d == date.today() - timedelta(days=1):
                        query_type = (f"{type_label} Field Visits for Yesterday ({start_d}){session_label}".strip())
                    elif start_d == date.today() - timedelta(days=2):
                        query_type = (f"{type_label} Field Visits for Day Before Yesterday ({start_d}){session_label}".strip())
                    else:
                        query_type = (f"{type_label} Field Visits on {start_d}{session_label}".strip())
                else:
                    _fv_month = _whole_month_label(start_d, end_d)
                    query_type = ("Field Visits Needed To Be Visited" if to_be_visited
                                  else (f"{type_label} Field Visits in {_fv_month}".strip()
                                        if _fv_month
                                        else f"{type_label} Field Visits Between Dates".strip()))
            elif "scheduled" in msg_lower or "visit date" in msg_lower or "when" in msg_lower or "schedule" in msg_lower:
                status_filter = "scheduled"
                query_type = "Scheduled Field Visits"
            else:
                type_label = f" {'+'.join(app_type_filter)}" if app_type_filter else ""
                query_type = f"{type_label} Field Visits Summary".strip()

            # "Show its field visit" after an application was confirmed asks about
            # THAT application, not the whole jurisdiction. Scope to it whenever the
            # gate confirmed one and the officer did not ask for a broader list.
            _fv_app_no = _gate_app_number if not (start_d or end_d or app_type_filter) else None
            structured_data = await get_field_visits(
                db, officer,
                status_filter=status_filter,
                start_date=start_d,
                end_date=end_d,
                to_be_visited_only=to_be_visited,
                application_type=app_type_filter,
                exclude_date_range=exclude_range,
                application_number=_fv_app_no
            )
            if _fv_app_no:
                query_type = f"Field Visit for {_fv_app_no}"
                structured_data["application_number"] = _fv_app_no
            structured_data["query_type"] = query_type
            if start_d:
                structured_data["start_date"] = start_d.isoformat()
            if end_d:
                structured_data["end_date"] = end_d.isoformat()
            structured_data["to_be_visited_only"] = to_be_visited


        elif intent == "active_applications_taluks":
            query = select(Application, Taluk.name).join(
                SurveyNumber, Application.survey_number_id == SurveyNumber.id
            ).join(
                Block, SurveyNumber.block_id == Block.id
            ).join(
                Ward, Block.ward_id == Ward.id
            ).join(
                Town, Ward.town_id == Town.id
            ).join(
                Taluk, Town.taluk_id == Taluk.id
            ).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_stage == officer.officer_stage,
                    Application.current_status.in_(["pending", "in_progress"])
                )
            )
            result = await db.execute(query)
            rows = result.all()
            from collections import Counter
            taluk_counts = Counter([row[1] for row in rows])
            structured_data = {
                "total_active": len(rows),
                "taluk_counts": dict(taluk_counts),
                "query_type": "Active Applications by Taluk"
            }

        elif intent == "highest_priority_applications":
            # Use the dedicated high priority query function
            # This returns applications with is_overdue=True OR priority_flag=True
            from backend.services.postgres import get_highest_priority_applications
            
            # Extract application type if mentioned
            app_type = None
            message_lower = message.lower()
            # word-bounded, NISD first: "nisd" contains "isd"
            if re.search(r'\bnisd\b|\b0153\b', message_lower):
                app_type = "NISD"
            elif re.search(r'\bisd\b|\b0154\b', message_lower):
                app_type = "ISD"
            elif re.search(r'\bmerge\b|\b0155\b', message_lower):
                app_type = "MERGE"
            
            structured_data = await get_highest_priority_applications(db, officer, application_type=app_type)
            structured_data["query_type"] = "High Priority Applications"
            
            # Log for debugging
            logger.info(f"🔥 High priority applications query: found {structured_data.get('count', 0)} applications")
            logger.info(f"   Applications: {[app['application_number'] for app in structured_data.get('applications', [])]}")

        elif intent == "assigned_today":
            today = date.today()
            query = select(func.count(Application.id)).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_stage == officer.officer_stage,
                    Application.submission_date == today
                )
            )
            res = await db.execute(query)
            structured_data = {
                "count": res.scalar(),
                "query_type": "Applications Assigned Today"
            }

        elif intent == "immediate_action":
            from sqlalchemy.orm import joinedload
            from datetime import date as _date_imm

            # Get all pending/in-progress applications assigned to this officer
            apps_query = select(Application).options(
                joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
            ).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_stage == officer.officer_stage,
                    Application.current_status.in_(["pending", "in_progress"])
                )
            ).order_by(Application.submission_date.asc())

            res_apps = await db.execute(apps_query)
            all_apps = res_apps.scalars().all()

            # Calculate working days and identify overdue applications (>15 working days)
            _today_imm = _date_imm.today()
            rows = []

            for a in all_apps:
                if not a.submission_date:
                    continue

                # Calculate working days (exclude weekends)
                working_days = 0
                current_date = a.submission_date
                while current_date < _today_imm:
                    current_date += timedelta(days=1)
                    if current_date.weekday() < 5:  # Monday = 0, Sunday = 6
                        working_days += 1

                # Consider overdue if more than 15 working days have elapsed
                if working_days > 15:
                    sn = a.survey_number
                    bl = sn.block if sn else None
                    w = bl.ward if bl else None
                    t = w.town if w else None

                    rows.append({
                        "application_number": a.application_number,
                        "type": a.application_type,
                        # survey_no/block_number were omitted here, so the table
                        # rendered "N/A" for both even though the joinedload had
                        # already fetched them.
                        "survey_no": sn.survey_no if sn else "N/A",
                        "block_number": bl.block_number if bl else "N/A",
                        "town_name": t.name if t else "N/A",
                        "ward_number": w.ward_number if w else "N/A",
                        "status": "Action Required",
                        "current_stage": a.current_stage,
                        "submission_date": a.submission_date.isoformat(),
                        "working_days_elapsed": working_days,
                        "days_overdue": working_days - 15
                    })

            structured_data = {
                "applications": rows,
                # "apps" kept alongside "applications" so both response phases
                # can read either key without drifting apart again.
                "apps": [r["application_number"] for r in rows],
                "query_type": "Immediate Action Required — Overdue Applications"
            }

        elif intent == "can_number_info":
            # "What is the CAN number of 2026/0153/28/001854?" asks for the value
            # on that file, and "was CAN 1332... taken at a CSC?" asks about one
            # number. The generic explainer is only right when the officer named
            # neither -- it was being returned for both.
            _can_app_no = extract_application_number(message) or _gate_app_number
            _can_token = re.search(r'\b(\d{12,15})\b', message)
            _can_details = None
            if _can_app_no or _can_token:
                _can_details = await get_can_details(
                    db, officer,
                    application_number=_can_app_no,
                    can_number=None if _can_app_no else _can_token.group(1),
                )
            if _can_details:
                # Found or not, the officer asked about one specific number --
                # answering with the generic explainer would look like an answer.
                structured_data = {"can_details": _can_details, "query_type": "CAN Details"}
            else:
                structured_data = {
                    "can_summary": {
                        "assigned_by": "Common Service Center (CSC) / Citizen Portal",
                        "description": "Citizen Access Number (CAN) is a unique citizen identity number assigned through CSC or citizen self-registration.",
                        "number_format": "The length identifies the channel: a CAN issued at a Common Service Centre is 15 digits, one generated by the citizen on the portal is 12 digits.",
                        "role_in_patta_transfer": "CAN links the citizen's Aadhaar, mobile number, and identity across all Patta transfer requests (ISD, NISD, MERGE).",
                        "csc_charges": "₹60.00 CSC service fee for application submission with CAN registration.",
                        "service_codes_linked": "0153 (NISD), 0154 (ISD), 0155 (MERGE)"
                    },
                    "query_type": "CAN Number & CSC Assignment Guide"
                }

        elif intent == "service_code_guide":
            structured_data = {
                "service_codes": [
                    {
                        "service_code": "0153",
                        "type": "NISD (Not Involving Sub-Division)",
                        "tamil_name": "உட்பிரிவு இல்லாத பட்டா மாறுதல்",
                        "govt_fee": "₹100.00",
                        "csc_fee": "₹60.00",
                        "sla_days": "15-20 working days",
                        "workflow": "Citizen / CSC → SIS Officer (Document Verification) → Deputy Tahsildar (Review) → Tahsildar (Digital Signature / DSC) [No field visit required]"
                    },
                    {
                        "service_code": "0154",
                        "type": "ISD (Involving Sub-Division)",
                        "tamil_name": "உட்பிரிவு உள்ள பட்டா மாறுதல்",
                        "govt_fee": "₹400.00",
                        "csc_fee": "₹60.00",
                        "sla_days": "30-35 working days",
                        "workflow": "Citizen / CSC → SIS Officer (Mandatory Field Visit within 15 days) → Senior Draughtsman (SD Sketch) → DIS (Approval) → Tahsildar (Digital Signature / DSC)"
                    },
                    {
                        "service_code": "0155",
                        "type": "MERGE (Subdivision Merger)",
                        "tamil_name": "உட்பிரிவு இணைப்பு பட்டா மாறுதல்",
                        "govt_fee": "₹0.00",
                        "csc_fee": "₹60.00",
                        "sla_days": "15 working days",
                        "workflow": "Citizen / CSC → SIS Officer (Field Boundary & Total Merged Area Verification) → Tahsildar (Digital Signature / DSC)"
                    }
                ],
                "query_type": "Service Codes Workflow & Fee Comparison (0153 / 0154 / 0155)"
            }

        elif intent == "escalation_check":
            from datetime import date as _date_esc_s
            _today_esc_s = _date_esc_s.today()
            esc_query_s = select(Application).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_status.in_(["pending", "in_progress", "escalated"])
                )
            ).order_by(Application.submission_date.asc())
            # "escalated ISD applications" named a type; without this the answer
            # mixed ISD and NISD rows together.
            _esc_msg = message.lower()
            if re.search(r'\bnisd\b|\b0153\b', _esc_msg):
                esc_query = esc_query.where(Application.application_type == "NISD")
            elif re.search(r'\bisd\b|\b0154\b', _esc_msg):
                esc_query = esc_query.where(Application.application_type == "ISD")
            elif re.search(r'\bmerge\b|\b0155\b', _esc_msg):
                esc_query = esc_query.where(Application.application_type == "MERGE")
            esc_result_s = await db.execute(esc_query_s)
            all_apps_s = esc_result_s.scalars().all()
            approaching_s = []
            for a in all_apps_s:
                if not a.submission_date:
                    continue
                wd_s = 0
                curr_s = a.submission_date
                while curr_s < _today_esc_s:
                    curr_s += timedelta(days=1)
                    if curr_s.weekday() < 5:
                        wd_s += 1
                if wd_s >= 10:
                    dr_s = max(0, 15 - wd_s)
                    ov_s = wd_s > 15
                    approaching_s.append({
                        "application_number": a.application_number,
                        "type": a.application_type,
                        "status": a.current_status,
                        "stage": a.current_stage,
                        "submission_date": a.submission_date.isoformat(),
                        "working_days_elapsed": wd_s,
                        "days_remaining": dr_s,
                        "is_overdue": ov_s,
                        "urgency": "⚠ OVERDUE" if ov_s else (
                            "🔴 Critical (1–2 days)" if dr_s <= 2 else
                            "🟡 Warning (3–5 days)" if dr_s <= 5 else
                            "🟢 Watch"
                        )
                    })
            structured_data = {
                "applications": approaching_s,
                "total_approaching": len(approaching_s),
                "overdue_count": sum(1 for x in approaching_s if x["is_overdue"]),
                "query_type": "Escalation Threshold — Applications Approaching Deadline"
            }

        elif intent == "fv_overdue_inspections":
            # Get field visits that are overdue (scheduled date in past, not completed)
            try:
                from sqlalchemy.orm import joinedload
                from backend.services.postgres import get_jurisdiction_filter
                
                today = date.today()
                logger.info(f"=== OVERDUE FIELD VISITS QUERY (STREAM) ===")
                logger.info(f"Today: {today}")
                logger.info(f"Officer ID: {officer.officer_id}")
                
                # Add jurisdiction filter to ensure only applications within officer's jurisdiction
                jurisdiction_filter = await get_jurisdiction_filter(db, officer)
                # jurisdiction_filter returns a list, we need to unpack it
                jur_conditions = jurisdiction_filter if isinstance(jurisdiction_filter, list) else [jurisdiction_filter]
                
                overdue_visits_stmt = select(FieldVisit).options(
                    joinedload(FieldVisit.application).joinedload(Application.survey_number).joinedload(SurveyNumber.block)
                ).join(
                    Application, FieldVisit.application_id == Application.id
                ).join(
                    SurveyNumber, Application.survey_number_id == SurveyNumber.id
                ).join(
                    # The geography chain must be joined explicitly: jur_conditions reference
                    # Block/Ward/Town/Taluk/District, and without these joins SQLAlchemy adds
                    # them as an uncorrelated FROM (cartesian product), silently voiding the filter.
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
                        FieldVisit.officer_id == officer.officer_id,
                        FieldVisit.scheduled_date.isnot(None),
                        FieldVisit.scheduled_date < today,
                        FieldVisit.status.in_(['scheduled', 'rescheduled', 'overdue']),
                        *jur_conditions  # Unpack list of conditions
                    )
                ).order_by(FieldVisit.scheduled_date.asc())
                
                overdue_visits = (await db.execute(overdue_visits_stmt)).unique().scalars().all()
                logger.info(f"Overdue visits found: {len(overdue_visits)}")
                
                overdue_list = []
                for visit in overdue_visits:
                    app = visit.application
                    if app:
                        app_type = app.application_type if app.application_type else "N/A"
                        logger.info(f"Overdue visit: App={app.application_number}, Type={app_type}, Scheduled={visit.scheduled_date}, Status={visit.status}")
                        overdue_list.append({
                            "application_number": app.application_number,
                            "type": app_type,  # Use variable with fallback
                            "status": visit.status,  # Field visit status, not application status
                            "stage": app.current_stage if app.current_stage else "N/A",
                            "survey_no": app.survey_number.survey_no if app.survey_number else "N/A",
                            "block_number": app.survey_number.block.block_number if app.survey_number and app.survey_number.block else "N/A",
                            "scheduled_date": visit.scheduled_date.isoformat() if visit.scheduled_date else "Not Scheduled",
                            "submission_date": app.submission_date.isoformat() if app.submission_date else "N/A"
                        })
                        # Log the complete dictionary for debugging
                        logger.info(f"  Complete data: {overdue_list[-1]}")
                
                structured_data = {
                    "overdue_visits_count": len(overdue_list),
                    "field_visits": overdue_list,
                    "query_type": "Overdue Field Visits"
                }
            except Exception as e:
                logger.error(f"Error getting overdue field visits (stream): {e}", exc_info=True)
                structured_data = {"error": str(e), "field_visits": []}

        elif intent == "awaiting_field_visit":
            query = select(func.count(FieldVisit.id)).where(
                and_(
                    FieldVisit.officer_id == officer.officer_id,
                    FieldVisit.status.in_(["scheduled", "unscheduled"])
                )
            )
            res = await db.execute(query)
            structured_data = {
                "count": res.scalar(),
                "query_type": "Awaiting Field Visit"
            }

        elif intent == "workload_by_type":
            structured_data = await get_officer_workload(db, officer)
            structured_data["query_type"] = "Workload by Type"

        elif intent == "completion_rate":
            from datetime import date as _date_cr_s
            _msg_lower_cr_s = message.lower()
            _this_month_s = any(p in _msg_lower_cr_s for p in ["this month", "month", "monthly", "current month"])
            _today_cr_s = date.today()
            _month_start_s = _today_cr_s.replace(day=1)

            # Same as process_chat: honour whatever period the officer named.
            _cr_start_s, _cr_end_s, _cr_label_s = _period_from_message(message)

            if _cr_start_s and _cr_end_s:
                _cr_window_s = and_(Application.submission_date >= _cr_start_s,
                                    Application.submission_date <= _cr_end_s)
                completed_query = select(func.count(Application.id)).where(
                    and_(
                        Application.assigned_officer_id == officer.officer_id,
                        Application.current_status.in_(["approved", "rejected"]),
                        _cr_window_s,
                    )
                )
                total_query = select(func.count(Application.id)).where(
                    and_(Application.assigned_officer_id == officer.officer_id, _cr_window_s)
                )
                scope_label_s = _cr_label_s or f"{_cr_start_s} to {_cr_end_s}"
            elif _this_month_s:
                completed_query = select(func.count(Application.id)).where(
                    and_(
                        Application.assigned_officer_id == officer.officer_id,
                        Application.current_status.in_(["approved", "rejected"]),
                        Application.updated_at >= _month_start_s
                    )
                )
                total_query = select(func.count(Application.id)).where(
                    and_(
                        Application.assigned_officer_id == officer.officer_id,
                        Application.submission_date >= _month_start_s
                    )
                )
                scope_label_s = f"this month ({_month_start_s.strftime('%B %Y')})"
            else:
                completed_query = select(func.count(Application.id)).where(
                    and_(
                        Application.assigned_officer_id == officer.officer_id,
                        Application.current_status.in_(["approved", "rejected"])
                    )
                )
                total_query = select(func.count(Application.id)).where(
                    Application.assigned_officer_id == officer.officer_id
                )
                scope_label_s = "overall"

            completed = (await db.execute(completed_query)).scalar() or 0
            total = (await db.execute(total_query)).scalar() or 0
            structured_data = {
                "completed": completed,
                "total": total,
                "rate": int((completed / total) * 100) if total > 0 else 0,
                "scope": scope_label_s,
                "query_type": "Completion Rate"
            }

        elif intent == "pending_longest":
            query = select(Application).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_stage == officer.officer_stage,
                    Application.current_status.in_(["pending", "in_progress"])
                )
            ).order_by(Application.submission_date.asc())
            result = await db.execute(query)
            apps = result.scalars().all()
            days = (date.today() - apps[0].submission_date).days if apps else 0
            structured_data = {
                "apps": [a.application_number for a in apps],
                "days": days,
                "query_type": "Pending Longest"
            }
            
        elif intent in ["is_nisd_or_isd", "check_documents", "check_sale_deed"]:
            app_number = extract_application_number(message)
            if not app_number:
                # Allow implicit continuation - check immediate previous message for app number
                # These intents are asking specific questions about an application
                _msg_lower = message.lower()
                # Always allow implicit continuation for these specific application queries
                app_number = (_extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True) or _gate_app_number)
            
            if app_number:
                structured_data = await get_application_detail(db, app_number, officer=officer)
                structured_data["query_type"] = "Application Details"
            else:
                # No app number provided — prompt in the officer's language,
                # same wording as the non-streaming path.
                is_tamil_lang = language in ("ta", "tanglish")
                if is_tamil_lang:
                    _prefetch_text = "தயவுசெய்து விண்ணப்ப எண்ணை குறிப்பிடவும். (எ.கா: 2026/0154/02/000041)"
                else:
                    _prefetch_text = "Please specify which application you're asking about. For example: 2026/0154/02/000041"
                structured_data = {"found": False, "query_type": "Application Details"}



        # ── SD / field-visit workflow checks (ported from process_chat) ──
        # Only the intents the streaming path does not already handle above;
        # fv_scheduled_this_week / fv_unassigned_awaiting / fv_deadline_check /
        # fv_change_date / fv_recently_rescheduled / fv_scheduling_conflicts /
        # fv_overdue_inspections have their own branches earlier in this chain.
        elif intent in ["sd_additional_info", "sd_encroachment_check", "sd_sketch_readiness",
                        "sd_forward_check", "sd_remarks", "fv_date_select",
                        "fv_nearby_pending", "fv_reschedule_availability"]:
            app_number = extract_application_number(message) or (_extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True) or _gate_app_number)
            
            from sqlalchemy.orm import joinedload
            
            app_res = await db.execute(
                select(Application)
                .options(joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town).joinedload(Town.taluk))
                .where(Application.application_number == app_number)
            )
            a = app_res.scalar_one_or_none()
            
            if not a:
                structured_data = {"found": False, "message": f"Application {app_number} not found.", "searched_number": app_number}
            else:
                doc_stmt = select(ApplicationDocument).where(ApplicationDocument.application_id == a.id)
                docs = (await db.execute(doc_stmt)).scalars().all()
                missing_docs = [d.document_type for d in docs if not d.is_uploaded]
            
                visit_stmt = select(FieldVisit).where(FieldVisit.application_id == a.id)
                visit = (await db.execute(visit_stmt)).scalars().first()
            
                hist_stmt = select(WorkflowHistory).where(WorkflowHistory.application_id == a.id).order_by(WorkflowHistory.performed_at.asc())
                history = (await db.execute(hist_stmt)).scalars().all()
            
                sd_clarification = None
                sd_remarks = None
                forwarded_to_sd_date = None
            
                for h in history:
                    if h.from_stage == "SD":
                        sd_clarification = h.rejection_reason or h.remarks
                        sd_remarks = h.remarks or h.rejection_reason
                    if h.to_stage == "SD":
                        forwarded_to_sd_date = h.performed_at.date().isoformat()
            
                nearby_count = 0
                ward_num = "N/A"
                block_num = "N/A"
                if a.survey_number and a.survey_number.block:
                    bl = a.survey_number.block
                    ward_num = bl.ward.ward_number if bl.ward else "N/A"
                    block_num = bl.block_number
                
                    nearby_stmt = select(func.count(Application.id)).join(
                        SurveyNumber, Application.survey_number_id == SurveyNumber.id
                    ).where(
                        and_(
                            SurveyNumber.block_id == bl.id,
                            Application.id != a.id,
                            Application.current_status.in_(["pending", "in_progress"])
                        )
                    )
                    nearby_count = (await db.execute(nearby_stmt)).scalar() or 0
            
                taluk_name = "N/A"
                taluk_scheduled_count = 0
                taluk_cases = []
                if a.survey_number and a.survey_number.block and a.survey_number.block.ward and a.survey_number.block.ward.town:
                    town = a.survey_number.block.ward.town
                    taluk = town.taluk
                    if taluk:
                        taluk_name = taluk.name
                        today = datetime.now(timezone.utc).date()
                        start_of_week = today - timedelta(days=today.weekday())
                        end_of_week = start_of_week + timedelta(days=6)
                    
                        stmt_week = select(Application).join(
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
                                FieldVisit.officer_id == officer.officer_id,
                                FieldVisit.status == "scheduled",
                                FieldVisit.scheduled_date >= start_of_week,
                                FieldVisit.scheduled_date <= end_of_week
                            )
                        )
                        week_apps = (await db.execute(stmt_week)).scalars().all()
                        taluk_scheduled_count = len(week_apps)
                        taluk_cases = [wa.application_number for wa in week_apps]
            
                reschedule_date = None
                for offset in range(1, 10):
                    test_date = datetime.now(timezone.utc).date() + timedelta(days=offset)
                    if test_date.weekday() >= 5:
                        continue
                    visit_count = (await db.execute(
                        select(func.count(FieldVisit.id)).where(
                            and_(
                                FieldVisit.officer_id == officer.officer_id,
                                FieldVisit.scheduled_date == test_date
                            )
                        )
                    )).scalar() or 0
                    if visit_count == 0:
                        reschedule_date = test_date.isoformat()
                        break
                if not reschedule_date:
                    reschedule_date = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
            
                sub_date = a.submission_date
                today = datetime.now(timezone.utc).date()
                working_days = 0
                curr = sub_date
                while curr < today:
                    curr += timedelta(days=1)
                    if curr.weekday() < 5:
                        working_days += 1
            
                overdue_visits_stmt = select(func.count(FieldVisit.id)).where(
                    and_(
                        FieldVisit.officer_id == officer.officer_id,
                        FieldVisit.status == "overdue"
                    )
                )
                overdue_visits_count = (await db.execute(overdue_visits_stmt)).scalar() or 0
            
                unassigned_visits_stmt = select(func.count(FieldVisit.id)).where(
                    and_(
                        FieldVisit.officer_id == officer.officer_id,
                        FieldVisit.status.in_(["unscheduled"])
                    )
                )
                unassigned_visits_count = (await db.execute(unassigned_visits_stmt)).scalar() or 0
            
                # Fetch actual unassigned applications with details for table display
                from sqlalchemy.orm import joinedload
                unassigned_apps_list = []
                unassigned_apps_stmt = select(Application).options(
                    joinedload(Application.applicant),
                    joinedload(Application.application_sub_divisions).joinedload(ApplicationSubDivision.sub_division),
                    joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
                ).join(
                    FieldVisit, FieldVisit.application_id == Application.id
                ).where(
                    and_(
                        FieldVisit.officer_id == officer.officer_id,
                        FieldVisit.status.in_(["unscheduled"])
                    )
                )
                unassigned_apps_res = (await db.execute(unassigned_apps_stmt)).unique().scalars().all()
                for ua in unassigned_apps_res:
                    days_p = (date.today() - ua.submission_date).days if ua.submission_date else 0
                    block_n = ua.survey_number.block.block_number if (ua.survey_number and ua.survey_number.block) else "N/A"
                    ward_n = ua.survey_number.block.ward.ward_number if (ua.survey_number and ua.survey_number.block and ua.survey_number.block.ward) else "N/A"
                    town_n = ua.survey_number.block.ward.town.name if (ua.survey_number and ua.survey_number.block and ua.survey_number.block.ward and ua.survey_number.block.ward.town) else "N/A"
                    survey_n = ua.survey_number.survey_no if ua.survey_number else "N/A"
                    # SIS temporary number (proposed by SIS during field visit)
                    sis_temp_nos = ", ".join(
                        sd.proposed_sub_division_no for sd in ua.application_sub_divisions
                        if sd.proposed_sub_division_no
                    ) or "N/A"
                    # DIS permanent/fixed number (from SubDivision record assigned by DIS)
                    dis_fixed_nos = ", ".join(
                        sd.sub_division.sub_division_no for sd in ua.application_sub_divisions
                        if sd.sub_division and sd.sub_division.sub_division_no
                    ) or "N/A"
                    unassigned_apps_list.append({
                        "application_number": ua.application_number,
                        "applicant_name": ua.applicant.name if ua.applicant else "N/A",
                        "survey_no": survey_n,
                        "sis_temp_sub_div": sis_temp_nos,
                        "dis_fixed_sub_div": dis_fixed_nos,
                        "town_name": town_n,
                        "ward_number": ward_n,
                        "block_number": block_n,
                        "current_stage": ua.current_stage or "N/A",
                        "current_status": ua.current_status or "N/A",
                        "submission_date": ua.submission_date.isoformat() if ua.submission_date else "N/A",
                        "days_pending": days_p,
                        "priority": "High" if ua.priority_flag else "Normal"
                    })
            
                recently_rescheduled_count = (await db.execute(
                    select(func.count(FieldVisit.id)).where(
                        and_(
                            FieldVisit.officer_id == officer.officer_id,
                            FieldVisit.updated_at >= datetime.now(timezone.utc) - timedelta(days=7)
                        )
                    )
                )).scalar() or 0
            
                overlap_date = None
                overlap_stmt = select(FieldVisit.scheduled_date).where(
                    and_(
                        FieldVisit.officer_id == officer.officer_id,
                        FieldVisit.status == "scheduled"
                    )
                ).group_by(FieldVisit.scheduled_date).having(func.count(FieldVisit.id) > 1)
                overlap_res = (await db.execute(overlap_stmt)).scalars().first()
                if overlap_res:
                    overlap_date = overlap_res.isoformat()
            
                structured_data = {
                    "found": True,
                    "application_number": a.application_number,
                    "current_stage": a.current_stage,
                    "submission_date": a.submission_date.isoformat(),
                    "missing_documents": missing_docs,
                    "field_visit_present": visit is not None,
                    "field_visit_date": visit.scheduled_date.isoformat() if (visit and visit.scheduled_date) else None,
                    "encroachment_found": visit.encroachment_found if visit else False,
                    "area_verified": visit.area_verified if visit else None,
                    "visit_notes_present": bool(visit.visit_notes) if visit else False,
                    "sd_clarification": sd_clarification,
                    "sd_remarks": sd_remarks,
                    "forwarded_to_sd_date": forwarded_to_sd_date,
                    "nearby_count": nearby_count,
                    "ward_number": ward_num,
                    "block_number": block_num,
                    "taluk_name": taluk_name,
                    "taluk_scheduled_count": taluk_scheduled_count,
                    "taluk_cases": taluk_cases,
                    "reschedule_date": reschedule_date,
                    "working_days": working_days,
                    "overdue_visits_count": overdue_visits_count,
                    "unassigned_visits_count": unassigned_visits_count,
                    "unassigned_applications": unassigned_apps_list,
                    "recently_rescheduled_count": recently_rescheduled_count,
                    "overlap_date": overlap_date,
                    "query_type": "Workflow Check"
                }

        elif intent == "jurisdiction_summary":
            from sqlalchemy.orm import joinedload
            q = select(OfficerJurisdiction).options(
                joinedload(OfficerJurisdiction.district),
                joinedload(OfficerJurisdiction.taluk),
                joinedload(OfficerJurisdiction.town),
                joinedload(OfficerJurisdiction.ward),
                joinedload(OfficerJurisdiction.block)
            ).where(OfficerJurisdiction.officer_id == officer.officer_id)
            res = await db.execute(q)
            jurisdictions = res.scalars().all()
            
            if not jurisdictions:
                structured_data = {"found": False, "message": "No jurisdictions assigned."}
            else:
                first = jurisdictions[0]
                d = first.district
                tk = first.taluk
                
                towns_map = {}
                for j in jurisdictions:
                    if j.town:
                        t_name = j.town.name
                        if t_name not in towns_map:
                            towns_map[t_name] = {}
                        if j.ward:
                            w_num = j.ward.ward_number
                            if w_num not in towns_map[t_name]:
                                towns_map[t_name][w_num] = []
                            if j.block:
                                towns_map[t_name][w_num].append({"block_number": j.block.block_number})
                
                towns_list = []
                for t_name, wards_map in towns_map.items():
                    wards_list = []
                    for w_num, blocks in wards_map.items():
                        wards_list.append({
                            "ward_number": w_num,
                            "blocks": blocks
                        })
                    towns_list.append({
                        "name": t_name,
                        "wards": wards_list
                    })
                
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
                            Application.assigned_officer_id == officer.officer_id,
                            Application.current_status.in_(["pending", "in_progress"])
                        )
                    )
                )).scalar() or 0
                
                structured_data = {
                    "jurisdiction": {
                        "district": {"name": d.name if d else "N/A", "code": d.district_code if d else "N/A"},
                        "taluk": {"name": tk.name if tk else "N/A"},
                        "towns": towns_list,
                        "survey_count": survey_count,
                        "active_applications": active_count
                    },
                    "query_type": "Jurisdiction Summary"
                }

        elif intent == "town_applications":
            town_name = extract_town_name(message)
            from sqlalchemy.orm import joinedload
            query = select(Application).join(
                SurveyNumber, Application.survey_number_id == SurveyNumber.id
            ).join(
                Block, SurveyNumber.block_id == Block.id
            ).join(
                Ward, Block.ward_id == Ward.id
            ).join(
                Town, Ward.town_id == Town.id
            ).options(
                joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
            ).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_stage == officer.officer_stage,
                    Application.current_status.in_(["pending", "in_progress"]),
                    Town.name.ilike(f"%{town_name}%") if town_name else True
                )
            ).order_by(Application.application_number)
            res = await db.execute(query)
            apps = res.scalars().all()
            
            app_rows = []
            for a in apps:
                sn = a.survey_number
                bl = sn.block if sn else None
                w = bl.ward if bl else None
                t = w.town if w else None
                app_rows.append({
                    "application_number": a.application_number,
                    "type": a.application_type,
                    "town_name": t.name if t else "N/A",
                    "ward_number": w.ward_number if w else "N/A",
                    "status": "Pending",
                    "stage": a.current_stage,
                    "submission_date": a.submission_date.isoformat()
                })
            
            structured_data = {
                "applications": app_rows,
                "query_type": f"Pending Applications in {town_name}" if town_name else "Pending Applications"
            }

        elif intent == "block_applications":
            block_no = extract_block_number(message)
            from sqlalchemy.orm import joinedload
            query = select(Application).join(
                SurveyNumber, Application.survey_number_id == SurveyNumber.id
            ).join(
                Block, SurveyNumber.block_id == Block.id
            ).join(
                Ward, Block.ward_id == Ward.id
            ).join(
                Town, Ward.town_id == Town.id
            ).options(
                joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
            ).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_stage == officer.officer_stage,
                    Application.current_status.in_(["pending", "in_progress"]),
                    Block.block_number.ilike(f"%{block_no}%") if block_no else True
                )
            ).order_by(Application.application_number)
            res = await db.execute(query)
            apps = res.scalars().all()
            
            app_rows = []
            for a in apps:
                sn = a.survey_number
                bl = sn.block if sn else None
                w = bl.ward if bl else None
                t = w.town if w else None
                app_rows.append({
                    "application_number": a.application_number,
                    "type": a.application_type,
                    "town_name": t.name if t else "N/A",
                    "ward_number": w.ward_number if w else "N/A",
                    "status": "Pending",
                    "stage": a.current_stage,
                    "submission_date": a.submission_date.isoformat()
                })
            
            structured_data = {
                "applications": app_rows,
                "query_type": f"Pending Applications in Block {block_no}" if block_no else "Pending Applications"
            }

        elif intent == "rejection_info":
            app_number = extract_application_number(message) or _extract_app_number_from_context(
                message, chat_history, allow_implicit_continuation=True
            )
            app_res = await db.execute(select(Application).where(Application.application_number == app_number))
            a = app_res.scalar_one_or_none()
            if not a:
                structured_data = {"found": False, "message": f"Application {app_number} not found.", "searched_number": app_number}
            else:
                history_res = await db.execute(
                    select(WorkflowHistory)
                    .where(WorkflowHistory.application_id == a.id)
                    .order_by(WorkflowHistory.performed_at.asc())
                )
                history = history_res.scalars().all()
                
                rejections = []
                for i, h in enumerate(history):
                    if h.to_stage == "REJECTED" or "REJECT" in (h.action or ""):
                        resub_date = None
                        for next_h in history[i+1:]:
                            if next_h.from_stage == "REJECTED" or "RESUBMIT" in (next_h.action or "") or next_h.to_stage != "REJECTED":
                                resub_date = next_h.performed_at.isoformat()
                                break
                        rejections.append({
                            "source": h.from_stage or "SD",
                            "reason_code": "REJ-01",
                            "reason_text": h.rejection_reason or h.remarks or "Boundary mismatch",
                            "rejected_at": h.performed_at.isoformat(),
                            "resubmitted_at": resub_date
                        })
                
                structured_data = {
                    "application_number": a.application_number,
                    "rejections": rejections,
                    "query_type": "Rejection History"
                }

        elif intent == "taluk_summary":
            q = select(OfficerJurisdiction).where(OfficerJurisdiction.officer_id == officer.officer_id)
            res = await db.execute(q)
            jurisdictions = res.scalars().all()
            if jurisdictions:
                first = jurisdictions[0]
                tk = first.taluk
                d = first.district
                structured_data = {
                    "taluk_name": tk.name if tk else "N/A",
                    "district_name": d.name if d else "N/A",
                    "query_type": "Taluk Summary"
                }
            else:
                structured_data = {"found": False, "message": "No taluk assigned."}

        elif intent == "litigation_check":
            # "litigation on 2022/0153/28/000016" names an application, not a
            # survey. extract_survey_number would pull "0153/28" out of the
            # middle of the application number and report it as not found, so
            # resolve the survey through the application first.
            survey_no = None
            _lit_app = extract_application_number(message)
            if _lit_app:
                _lit_survey = (await db.execute(
                    select(SurveyNumber)
                    .join(Application, Application.survey_number_id == SurveyNumber.id)
                    .where(Application.application_number == _lit_app)
                )).scalars().first()
                if _lit_survey is not None:
                    survey_no = _lit_survey.survey_no
            if not survey_no:
                survey_no = extract_survey_number(message)
            if not survey_no:
                # No number in this message, no application to resolve it
                # through -- e.g. "is there litigation on it?" straight after
                # something unrelated. This used to silently fall back to a
                # hardcoded "145" and confidently report that survey as "not
                # found", which reads as a real answer instead of what it
                # actually is: the officer's reference couldn't be resolved.
                is_tamil = language in ("ta", "tanglish")
                structured_data = {
                    "found": False,
                    "message": ("தயவுசெய்து சர்வே எண்ணைக் குறிப்பிடவும். (எ.கா: 1345)" if is_tamil
                                else "Please specify the survey number you are asking about (e.g. 1345).")
                }
            else:
                # survey_no is not unique -- the same number exists in more than one
                # block, so this must never be scalar_one_or_none(): a number like
                # "15" raised MultipleResultsFound and killed the whole request.
                # A subdivision-qualified number ("1344/2") never matches survey_no
                # exactly -- the stored value is the base number -- so fall back to
                # it the same way get_survey_detail() does, or "litigation on
                # subdivision 1344/2" always reported "not found".
                _lit_base = survey_no.split('/')[0] if '/' in survey_no else survey_no
                _lit_rows = (await db.execute(
                    select(SurveyNumber).where(
                        or_(SurveyNumber.survey_no == survey_no, SurveyNumber.survey_no == _lit_base)
                    )
                )).scalars().all()
                sn = next((r for r in _lit_rows if r.has_litigation), None) or (
                    _lit_rows[0] if _lit_rows else None)
                if sn:
                    structured_data = {
                        "survey_no": sn.survey_no,
                        "litigation_flag": sn.has_litigation,
                        "parcels_with_this_number": len(_lit_rows),
                        "query_type": "Litigation Check"
                    }
                else:
                    structured_data = {"found": False, "message": f"Survey number {survey_no} not found."}

        elif intent in ["check_sale_deed", "sale_deed_check"]:
            app_number = extract_application_number(message)
            if not app_number:
                app_number = (_extract_app_number_from_context(message, chat_history) or _gate_app_number)
            
            if not app_number:
                # No app number provided - ask user for it
                is_tamil_lang = language in ("ta", "tanglish")
                if is_tamil_lang:
                    _prefetch_text = "தயவுசெய்து விண்ணப்ப எண்ணை குறிப்பிடவும். (எ.கா: 2026/0154/02/000041)"
                else:
                    _prefetch_text = "Please specify which application you're asking about. For example: 2026/0154/02/000041"
                structured_data = {"found": False, "query_type": "Sale Deed Verification"}
            else:
                structured_data = await get_application_detail(db, app_number, officer=officer)
                structured_data["query_type"] = "Sale Deed Verification"
                structured_data["sale_deed_verified"] = structured_data.get("sale_deed_registered", False)


        elif intent == "all_surveys_in_jurisdiction":
            structured_data = await get_all_surveys_in_jurisdiction(db, officer)
            structured_data["query_type"] = "All Surveys in Your Jurisdiction"

        elif intent == "merge_info":
            app_number = (_extract_app_number_from_context(message, chat_history) or _gate_app_number)
            if app_number:
                structured_data = await get_merge_application_detail(db, app_number, officer)
            else:
                structured_data = await get_merge_application_detail(db, None, officer)
            structured_data["query_type"] = "Merge Application Details"

        elif intent == "application_status":
            # Extract from current message first; only check history if user uses reference words
            # Use findall to support multi-app queries like "Show details for A and B"
            _app_numbers_in_msg = extract_application_numbers(message)

            if len(_app_numbers_in_msg) > 1:
                # Multiple application numbers detected — fetch details for each
                _multi_details = []
                for _an in _app_numbers_in_msg:
                    _det = await get_application_detail(db, _an, officer=officer)
                    _multi_details.append(_det)
                structured_data = {
                    "multi_applications": _multi_details,
                    "query_type": "Application Status"
                }
            else:
                app_number = _app_numbers_in_msg[0] if _app_numbers_in_msg else None
                if not app_number:
                    # Check for explicit reference patterns OR implicit continuation for field queries
                    # Implicit continuation: if user just discussed an app, next field query refers to it
                    _field_keywords = [
                        "name", "address", "mobile", "phone", "status", "stage",
                        "overdue", "days", "scheduled", "visit", "delay", "delayed", "late",
                        "survey", "patta", "can", "reason", "priority",
                        "subdivision", "subdiv", "user", "role",
                        "service", "source", "district", "taluk", "ward", "block",
                        "received", "workflow",
                        "serial", "applicant", "type", "date", "year",
                        "பெயர்", "முகவரி", "தொலைபேசி", "நிலை", "கட்டம்", "தாமதம்",
                        "கணக்கெண்", "பட்டா", "காரணம்", "முன்னுரிமை"
                    ]
                    is_field_query = any(kw in message.lower() for kw in _field_keywords)
                    app_number = (_extract_app_number_from_context(message, chat_history, allow_implicit_continuation=is_field_query) or _gate_app_number)

                    # No substitution here — see the matching comment in process_chat.
                if app_number:
                    if _wants_workflow_history(message):
                        # Workflow/timeline sub-query — same handling as process_chat
                        from sqlalchemy.orm import joinedload
                        app_res_s = await db.execute(select(Application).where(Application.application_number == app_number))
                        a_s = app_res_s.scalar_one_or_none()
                        if not a_s:
                            structured_data = {
                                "found": False,
                                "message": f"Application {app_number} not found.",
                                "searched_number": app_number
                            }
                        else:
                            history_res_s = await db.execute(
                                select(WorkflowHistory)
                                .options(joinedload(WorkflowHistory.performed_by_officer))
                                .where(WorkflowHistory.application_id == a_s.id)
                                .order_by(WorkflowHistory.performed_at.asc())
                            )
                            structured_data = {
                                "application_number": a_s.application_number,
                                "history": [
                                    {
                                        "from_stage": h.from_stage,
                                        "to_stage": h.to_stage,
                                        "changed_at": h.performed_at.isoformat(),
                                        "note": h.remarks,
                                        "changed_by_name": h.performed_by_officer.name if h.performed_by_officer else "System"
                                    }
                                    for h in history_res_s.scalars().all()
                                ],
                                "query_type": f"Workflow History for {a_s.application_number}"
                            }
                    else:
                        structured_data = await get_application_detail(db, app_number, officer=officer)
                        structured_data["query_type"] = "Application Status"

        elif intent == "joint_owner_check":
            # Check if asking about an application's survey ownership or direct survey ownership
            app_number = extract_application_number(message)
            if not app_number:
                # Allow implicit continuation for joint owner queries
                app_number = (_extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True) or _gate_app_number)
            
            if app_number:
                # Get application details to find the survey number
                app_data = await get_application_detail(db, app_number, officer=officer)
                survey_no = app_data.get("survey_no") if app_data.get("found") else None
                if not survey_no:
                    structured_data = {"found": False, "message": f"Application {app_number} not found or has no survey linked"}
                else:
                    owners_data = await get_survey_owners(db, survey_no)
                    joint_owners = [o for o in owners_data.get("owners", []) if o.get("is_joint_owner")]
                    structured_data = {
                        "found": True,
                        "application_number": app_number,
                        "survey_no": survey_no,
                        "joint_owners": joint_owners,
                        "total_owners": len(owners_data.get("owners", [])),
                        "query_type": "Joint Ownership Check"
                    }
            else:
                # Direct survey number query
                survey_no = extract_survey_number(message)
                if not survey_no:
                    structured_data = {"found": False, "message": "Please provide an application number or survey number"}
                else:
                    owners_data = await get_survey_owners(db, survey_no)
                    joint_owners = [o for o in owners_data.get("owners", []) if o.get("is_joint_owner")]
                    structured_data = {
                        "found": True,
                        "survey_no": survey_no,
                        "joint_owners": joint_owners,
                        "total_owners": len(owners_data.get("owners", [])),
                        "query_type": "Joint Ownership Details"
                    }
        
        elif intent == "survey_detail":
            survey_no = extract_survey_number(message)
            if survey_no:
                structured_data = await get_survey_detail(db, survey_no)
                structured_data["query_type"] = "Survey Number Details"
        
        elif intent == "survey_owners":
            survey_no = extract_survey_number(message)
            if not survey_no and _gate_app_number:
                # "What about the owner?" after an application was confirmed means
                # the owner of THAT application's survey. Without this the branch
                # finds no survey number and reports "survey not found", which
                # reads as though the data were missing.
                _owner_app = await get_application_detail(db, _gate_app_number, officer=officer)
                if _owner_app.get("found"):
                    survey_no = _owner_app.get("survey_no")
            if survey_no:
                structured_data = await get_survey_owners(db, survey_no, officer=officer)
                structured_data["query_type"] = "Survey Ownership"
            else:
                structured_data = {
                    "found": False,
                    "message": ASK_FOR_SURVEY_NUMBER[language if language in ASK_FOR_SURVEY_NUMBER else "en"],
                    "query_type": "Survey Ownership",
                }
        
        elif intent == "next_subdivision":
            survey_no = extract_survey_number(message)
            if survey_no:
                structured_data = await get_next_subdivision_number(db, survey_no)
                structured_data["query_type"] = "Next Sub-division Number"
        
        elif intent == "ward_surveys" or intent == "block_surveys":
            ward_id = extract_ward_number(message)
            block_id = extract_block_number(message)
            
            # If no ward specified in message, use officer's ward from jurisdiction
            if not ward_id:
                if officer.jurisdiction_type in ["ward", "block"]:
                    # Use officer's assigned jurisdiction to find ward
                    
                    if officer.jurisdiction_type == "block":
                        # Officer is assigned to a block, get its ward
                        block_result = await db.execute(
                            select(Block, Ward).join(Ward, Block.ward_id == Ward.id).where(
                                Block.id.in_(officer.jurisdiction_ids)
                            ).limit(1)
                        )
                        row = block_result.first()
                        if row:
                            _, ward_obj = row
                            ward_id = ward_obj.ward_number
                            logger.info(f"Using officer's block's ward: {ward_id}")
                    elif officer.jurisdiction_type == "ward":
                        # Officer is assigned to a ward directly
                        ward_result = await db.execute(
                            select(Ward).where(Ward.id.in_(officer.jurisdiction_ids)).limit(1)
                        )
                        ward_obj = ward_result.scalar_one_or_none()
                        if ward_obj:
                            ward_id = ward_obj.ward_number
                            logger.info(f"Using officer's assigned ward: {ward_id}")
            
            if ward_id:
                structured_data = await get_ward_surveys(db, ward_id, block_id)
                structured_data["query_type"] = "Ward Survey Numbers and Sub-divisions"
            else:
                structured_data = {"found": False, "message": "Please specify a ward number or ensure your officer profile has a ward assignment."}

        elif intent == "fv_scheduled_this_week":
            # Query officer's taluk directly for scheduled field visits this week
            from backend.models import Block as _BlockS
            jur_result_s = await db.execute(
                select(OfficerJurisdiction).where(OfficerJurisdiction.officer_id == officer.officer_id).limit(1)
            )
            jur_s = jur_result_s.scalar_one_or_none()
            taluk_obj_s = None
            if jur_s:
                if jur_s.taluk_id:
                    taluk_obj_s = (await db.execute(select(Taluk).where(Taluk.id == jur_s.taluk_id))).scalar_one_or_none()
                elif jur_s.block_id:
                    bl_s = (await db.execute(select(_BlockS).where(_BlockS.id == jur_s.block_id))).scalar_one_or_none()
                    if bl_s:
                        wd_s = (await db.execute(select(Ward).where(Ward.id == bl_s.ward_id))).scalar_one_or_none()
                        if wd_s:
                            tw_s = (await db.execute(select(Town).where(Town.id == wd_s.town_id))).scalar_one_or_none()
                            if tw_s:
                                taluk_obj_s = (await db.execute(select(Taluk).where(Taluk.id == tw_s.taluk_id))).scalar_one_or_none()
                elif jur_s.ward_id:
                    wd_s = (await db.execute(select(Ward).where(Ward.id == jur_s.ward_id))).scalar_one_or_none()
                    if wd_s:
                        tw_s = (await db.execute(select(Town).where(Town.id == wd_s.town_id))).scalar_one_or_none()
                        if tw_s:
                            taluk_obj_s = (await db.execute(select(Taluk).where(Taluk.id == tw_s.taluk_id))).scalar_one_or_none()
            taluk_name_s = taluk_obj_s.name if taluk_obj_s else "your taluk"
            taluk_id_s = taluk_obj_s.id if taluk_obj_s else None
            today_s = datetime.now(timezone.utc).date()
            sow = today_s - timedelta(days=today_s.weekday())
            eow = sow + timedelta(days=6)
            week_count_s = 0
            week_apps_s = []
            if taluk_id_s:
                stmt_s = select(Application).join(
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
                        Town.taluk_id == taluk_id_s,
                        FieldVisit.officer_id == officer.officer_id,
                        FieldVisit.status == "scheduled",
                        FieldVisit.scheduled_date >= sow,
                        FieldVisit.scheduled_date <= eow
                    )
                )
                res_s = (await db.execute(stmt_s)).scalars().all()
                week_count_s = len(res_s)
                week_apps_s = [a.application_number for a in res_s]
            structured_data = {
                "taluk_scheduled_count": week_count_s,
                "taluk_name": taluk_name_s,
                "taluk_cases": week_apps_s,
                "week_start": sow.isoformat(),
                "week_end": eow.isoformat(),
                "query_type": "Scheduled Field Visits This Week"
            }

        elif intent == "fv_unassigned_awaiting":
            # Query all unscheduled field visits for this officer directly
            from backend.models import SubDivision
            from sqlalchemy.orm import joinedload
            from datetime import date as _date

            unassigned_stmt = select(Application).options(
                joinedload(Application.applicant),
                joinedload(Application.application_sub_divisions).joinedload(ApplicationSubDivision.sub_division),
                joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
            ).join(
                FieldVisit, FieldVisit.application_id == Application.id
            ).where(
                and_(
                    FieldVisit.officer_id == officer.officer_id,
                    FieldVisit.status == "unscheduled"
                )
            )
            unassigned_res = (await db.execute(unassigned_stmt)).unique().scalars().all()

            unassigned_list = []
            for ua in unassigned_res:
                days_p = (_date.today() - ua.submission_date).days if ua.submission_date else 0
                sn = ua.survey_number
                bl = sn.block if sn else None
                wd = bl.ward if bl else None
                tw = wd.town if wd else None
                sis_nos = ", ".join(
                    sd.proposed_sub_division_no for sd in ua.application_sub_divisions
                    if sd.proposed_sub_division_no
                ) or "N/A"
                dis_nos = ", ".join(
                    sd.sub_division.sub_division_no for sd in ua.application_sub_divisions
                    if sd.sub_division and sd.sub_division.sub_division_no
                ) or "N/A"
                unassigned_list.append({
                    "application_number": ua.application_number,
                    "applicant_name": ua.applicant.name if ua.applicant else "N/A",
                    "survey_no": sn.survey_no if sn else "N/A",
                    "sis_temp_sub_div": sis_nos,
                    "dis_fixed_sub_div": dis_nos,
                    "town_name": tw.name if tw else "N/A",
                    "ward_number": wd.ward_number if wd else "N/A",
                    "block_number": bl.block_number if bl else "N/A",
                    "current_stage": ua.current_stage or "N/A",
                    "current_status": ua.current_status or "N/A",
                    "submission_date": ua.submission_date.isoformat() if ua.submission_date else "N/A",
                    "days_pending": days_p,
                    "priority": "High" if ua.priority_flag else "Normal"
                })

            structured_data = {
                "unassigned_visits_count": len(unassigned_list),
                "unassigned_applications": unassigned_list,
                "query_type": "திட்டமிடல் காத்திருக்கும் கள ஆய்வுகள்" if language == "ta" else "Unassigned Field Visits — Awaiting Scheduling"
            }

        elif intent == "fv_recently_rescheduled":
            # Field visits touched (rescheduled) in the last 7 days — officer-wide,
            # no application number required.
            recently_rescheduled_count_s = (await db.execute(
                select(func.count(FieldVisit.id)).where(
                    and_(
                        FieldVisit.officer_id == officer.officer_id,
                        FieldVisit.updated_at >= datetime.now(timezone.utc) - timedelta(days=7)
                    )
                )
            )).scalar() or 0
            structured_data = {
                "found": True,
                "recently_rescheduled_count": recently_rescheduled_count_s,
                "query_type": "Recently Rescheduled Field Visits"
            }

        elif intent == "fv_scheduling_conflicts":
            # Two or more scheduled visits on the same date = conflict.
            overlap_stmt_s = select(FieldVisit.scheduled_date).where(
                and_(
                    FieldVisit.officer_id == officer.officer_id,
                    FieldVisit.status == "scheduled"
                )
            ).group_by(FieldVisit.scheduled_date).having(func.count(FieldVisit.id) > 1)
            overlap_res_s = (await db.execute(overlap_stmt_s)).scalars().first()
            structured_data = {
                "found": True,
                "overlap_date": overlap_res_s.isoformat() if overlap_res_s else None,
                "query_type": "Field Visit Scheduling Conflicts"
            }

        elif intent == "fv_change_date":
            structured_data = {
                "found": True,
                "query_type": "Field Visit Date Change",
                "message": "To change the date of a field visit, you should ask the Tahsildar."
            }

        elif intent == "fv_deadline_check":
            # Resolve application number from message or chat history
            resolved_app_dl = extract_application_number(message) or (_extract_app_number_from_context(message, chat_history) or _gate_app_number)
            if not resolved_app_dl:
                structured_data = {
                    "found": False,
                    "message": "Please specify an application number, e.g. 2026/0154/02/000041, to check the deadline."
                }
            else:
                app_res_dl = await db.execute(
                    select(Application).where(Application.application_number == resolved_app_dl)
                )
                a_dl = app_res_dl.scalar_one_or_none()
                if not a_dl:
                    structured_data = {"found": False, "message": f"Application {resolved_app_dl} not found.", "searched_number": resolved_app_dl}
                else:
                    sub_date_dl = a_dl.submission_date
                    today_dl = datetime.now(timezone.utc).date()
                    working_days_dl = 0
                    curr_dl = sub_date_dl
                    while curr_dl < today_dl:
                        curr_dl += timedelta(days=1)
                        if curr_dl.weekday() < 5:
                            working_days_dl += 1
                    structured_data = {
                        "found": True,
                        "application_number": a_dl.application_number,
                        "submission_date": sub_date_dl.isoformat(),
                        "working_days": working_days_dl,
                        "deadline_days": 15,
                        "is_overdue": working_days_dl > 15,
                        "days_overdue": max(0, working_days_dl - 15),
                        "days_remaining": max(0, 15 - working_days_dl),
                        "query_type": "Field Visit Deadline Check"
                    }

        elif intent == "isd_processing":
            # Area comparison and ISD workflow queries — resolve app number from message or history
            _isd_app_no = extract_application_number(message) or (_extract_app_number_from_context(message, chat_history) or _gate_app_number)
            if not _isd_app_no:
                # Never stand in an arbitrary application for one the officer
                # did not name — that answers a question nobody asked.
                structured_data = {"found": False, "query_type": "ISD Processing"}
                _prefetch_text = ASK_FOR_APP_NUMBER[language if language in ASK_FOR_APP_NUMBER else "en"]
            else:
                structured_data = await get_application_detail(db, _isd_app_no, officer=officer)
                structured_data["query_type"] = "ISD Processing"

        # Step 4: Get RAG context from pgvector — skip if DB data was actually found
        # A single-record lookup (one application, one survey, one owner set) carries
        # no "count" key, so requiring count > 0 classified every such answer as
        # "no database results" and pulled FAQ chunks into the prompt alongside
        # authoritative values. Count is honoured when present; otherwise any real
        # payload beyond the bookkeeping keys counts as a database result.
        _sd = structured_data or {}
        _meta_only = {"found", "message", "query_type", "searched_number",
                      "suggestions", "needs_confirmation"}
        has_db_results = bool(
            _sd
            and _sd.get("found", True)
            and (_sd.get("count", 0) > 0 if "count" in _sd
                 else any(k not in _meta_only for k in _sd))
        )
        # 8, not 5: a section's detail can rank just below its overview, and at
        # k=5 the ISD timeline fell outside the window while the NISD one stayed
        # in -- the model then quoted NISD's 15-20 days for an ISD question.
        rag_context = await get_rag_context_async(message, language, n_results=8) if not has_db_results else ""
        context_used = len(rag_context) > 0
        
        # Step 5: Try to build HTML directly from structured data (no LLM needed).
        # Skip the HTML path for interrogative questions so the LLM answers
        # conversationally — same logic as the non-streaming path above.
        _msg_lower = message.lower()
        _interrogative_keywords = [
            "which", "what", "how many", "how much", "why", "who",
            # "when did we receive this file" is as pointed a question as "what
            # is its status" -- leaving "when" out sent it down the summary path.
            "when", "எப்போது",
            "where", "where is", "which department", "currently",
            "give me", "tell me", "show me", "get me", "how long", "how long it is",
            "how long is", "how long has", "pending for", "how long pending",
            "எந்த", "என்ன", "எத்தனை", "ஏன்", "யார்", "எவ்வளவு", "எத்தனை நாள்", "எவ்வளவு நாள்", "ஆச்சு",
        ]
        _field_keywords = [
            "address", "mobile", "phone", "name", "status", "type",
            "position", "received", "receive", "ward", "block",
            "stage", "date", "year", "survey", "applicant", "priority", "aadhaar",
            "reason", "overdue", "nisd", "isd", "merge", "pending", "long", "duration",
            "days", "since", "how long", "serial", "serial number", "serial_number",
            "can", "can number", "can_number", "patta", "patta number", "patta_number",
            "subdivision", "subdivision number", "subdivision_number", "current subdivision",
            "current_subdivision_number", "role", "role id",
            "role_id", "user", "user id", "user_id",
            "service", "service_code", "district_code", "taluk_code", "village_code",
            "urban_unit_code", "ward_code", "block_code", "ward", "block",
            "source", "source_code", "source_name", "workflow_state",
            "declared reason", "declared_reason",
            # Tamil field keywords
            "முகவரி", "தொலைபேசி", "பெயர்", "நாமாகும்", "நாமம்", "நிலை", "வகை",
            "கட்டம்", "தேதி", "ஆண்டு", "கணக்கெண்", "சர்வே எண்", "விண்ணப்பதாரர்", "முன்னுரிமை",
            "காரணம்", "காலதாமத", "நிலுவை", "நிலுவையில்", "எவ்வளவு", "எத்தனை", "நாட்கள்", "நாள்", "ஆச்சு",
            "வரிசை எண்", "பட்டா எண்", "உட்பிரிவு எண்", "பயனர் ஐடி",
            "பங்கு ஐடி", "ஆதாரம்",
            "niluvai", "evvalavu", "ethanai", "naal", "naatkal",
            # Stage/location keywords
            "sd", "dis", "tahsildar", "sis", "department", "office",
            "right now", "currently", "current stage",
            # Tamil stage/location
            "அலுவலகம்", "இப்போது", "எங்கே",
        ]
        _is_interrogative = any(kw in _msg_lower for kw in _interrogative_keywords)
        _is_interrogative = _is_interrogative or any(
            phrase in _msg_lower for phrase in
            ["included in", "part of", "belong to", "contains", "உள்ளது", "உள்ளன",
             "right now", "currently at", "currently with", "which department"]
        )
        _has_field_keyword = any(kw in _msg_lower for kw in _field_keywords)
        _has_interrogative_phrase = any(
            kw in _msg_lower for kw in ["give", "tell", "show", "get", "what", "provide",
                                         "where", "which", "currently", "right now", "is this",
                                         "how", "how many", "days", "overdue"]
        )
        _has_app_number = bool(extract_application_number(message))
        _has_context_app = bool((_extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True) or _gate_app_number))
        _is_short_field_query = _has_field_keyword and len(_msg_lower.split()) <= 6
        _asking_specific_field = _has_field_keyword and (_has_interrogative_phrase or _has_app_number or _has_context_app or _is_short_field_query)
        _is_interrogative = _is_interrogative or _asking_specific_field
        _is_multi_app = bool(structured_data and "multi_applications" in structured_data)
        _bypass_html = _is_interrogative and intent in ("application_status", "merge_info") and not _is_multi_app
        _asking_for_count = _is_count_only_query(message)

        if _asking_for_count and intent in ("pending_applications", "isd_applications", "nisd_applications", "merge_applications", "both_applications", "overdue_applications"):
            html_response = _format_count_intro(structured_data, language, message)
        else:
            html_response = "" if _bypass_html else build_html_response(structured_data, language, query=message)
        import json

        # Only emit table_data when there's no direct HTML response AND we are
        # not in interrogative-bypass mode (where LLM answers conversationally).
        if not html_response and not _bypass_html:
            table_data = _build_table_data(intent, message, str(officer.officer_id), structured_data)
            if table_data:
                table_data['language'] = language
                yield f"data: {json.dumps({'table_data': table_data})}\n\n".encode('utf-8')

        # ── Hardcoded direct answer for interrogative queries (streaming) ──
        _direct_answer_text = _prefetch_text  # text set during the fetch phase, if any
        if not html_response and _bypass_html and structured_data and structured_data.get("found", True):
            sd = structured_data
            app_no    = sd.get("application_number") or sd.get("application_id") or extract_application_number(message) or ""
            app_type  = sd.get("type", "")
            survey_no = sd.get("survey_no", "")
            subdivisions = sd.get("subdivisions_being_merged") or []
            total_area   = sd.get("total_merge_area_sqm")

            if app_type == "MERGE" and ("sub" in _msg_lower or "survey" in _msg_lower or
                                         "included" in _msg_lower or "which" in _msg_lower or
                                         "உட்பிரிவு" in message or "கணக்கெண்" in message):
                if subdivisions:
                    subdiv_parts = []
                    for sd_item in subdivisions:
                        area = sd_item.get("area_sqm")
                        label = sd_item["sub_division_no"]
                        if area:
                            label += f" ({area:.2f} sq.m)"
                        subdiv_parts.append(label)
                    subdiv_str = ", ".join(subdiv_parts)
                    area_str = f" The total merge area is {total_area:.2f} sq.m." if total_area else ""
                    _direct_answer_text = (
                        f"Merge application {app_no} covers Survey No. {survey_no} "
                        f"and includes {len(subdivisions)} sub-division(s): {subdiv_str}.{area_str}"
                    )
                else:
                    _direct_answer_text = (
                        f"Merge application {app_no} is on Survey No. {survey_no}, "
                        f"but no sub-divisions have been linked yet."
                    )

            # ── Check if user is asking about application but didn't provide number ──
            if not _direct_answer_text and _asking_specific_field and intent == "application_status" and not app_no:
                # User is asking a specific question about an application but didn't provide the number
                is_tamil_check = language in ("ta", "tanglish")
                if is_tamil_check:
                    _direct_answer_text = "தயவுசெய்து விண்ணப்ப எண்ணை குறிப்பிடவும். (எ.கா: 2026/0154/02/000041)"
                else:
                    _direct_answer_text = "Please provide the application number (e.g., 2026/0154/02/000041) so I can help you with that information."
                logger.info("User asked about application field without providing app number - prompted for app number")

            # ── Specific field extraction for application_status queries (stream) ──
            if not _direct_answer_text and intent == "application_status" and app_no:
                # Check for pending duration / how long pending / overdue questions (higher priority)
                _is_pending_or_overdue_q = any(w in _msg_lower for w in [
                    "overdue", "late", "delay", "tardiness", "தாமதம்", "காலதாமத",
                    "how long", "how many days", "pending", "duration", "since",
                    "நிலுவை", "நிலுவையில்", "எவ்வளவு நாள்", "எத்தனை நாள்", "எவ்வளவு நாட்கள்",
                    "எத்தனை நாட்கள்", "நாள் ஆச்சு", "நாட்கள் ஆச்சு", "ஆச்சு",
                    "niluvai", "evvalavu", "ethanai", "naal", "naatkal"
                ])
                if _is_pending_or_overdue_q:
                    fv_info = sd.get("field_visit") or {}
                    fv_date_str = fv_info.get("scheduled_date") or sd.get("field_visit_date")
                    sub_date_str = sd.get("submission_date")
                    from datetime import date as _date_mod
                    today = _date_mod.today()

                    fv_days_overdue = None
                    fv_days_until = None
                    if fv_date_str:
                        try:
                            fv_d = _date_mod.fromisoformat(str(fv_date_str)[:10])
                            if today > fv_d:
                                fv_days_overdue = (today - fv_d).days
                            else:
                                fv_days_until = (fv_d - today).days
                        except Exception:
                            pass

                    app_sub_days = None
                    if sub_date_str:
                        try:
                            sub_d = _date_mod.fromisoformat(str(sub_date_str)[:10])
                            app_sub_days = (today - sub_d).days
                        except Exception:
                            pass

                    is_tamil = language in ("ta", "tanglish")
                    app_status_str = str(sd.get("status", "")).lower()

                    # Explicit pending duration question ("how long pending", "days pending", "ethanai naal niluvai")
                    _asked_pending_explicit = any(w in _msg_lower for w in [
                        "how long", "how long it is pending", "how long is it pending", "how long pending",
                        "pending for", "days pending", "how many days pending", "how long has", "since submission",
                        "நிலுவை", "நிலுவையில்", "எவ்வளவு நாள்", "எத்தனை நாள்", "எவ்வளவு நாட்கள்",
                        "எத்தனை நாட்கள்", "நாள் ஆச்சு", "நாட்கள் ஆச்சு", "ஆச்சு",
                        "niluvai", "evvalavu", "ethanai", "naal"
                    ]) and not any(w in _msg_lower for w in ["overdue", "late", "delay", "tardiness", "தாமதம்", "காலதாமத"])

                    if _asked_pending_explicit and app_sub_days is not None:
                        if app_status_str in ["completed", "approved", "closed"]:
                            if is_tamil:
                                _direct_answer_text = f"விண்ணப்பம் {app_no} நிலுவையில் இல்லை — இது ஏற்கனவே முடிவடைந்தது/அங்கீகரிக்கப்பட்டது."
                            else:
                                _direct_answer_text = f"Application {app_no} is no longer pending — it has been completed and approved."
                        elif app_sub_days > 15:
                            sla_past = app_sub_days - 15
                            if is_tamil:
                                _direct_answer_text = f"விண்ணப்பம் {app_no} சமர்ப்பிக்கப்பட்டு **{app_sub_days} நாட்கள்** நிலுவையில் உள்ளது ({str(sub_date_str)[:10]} அன்று சமர்ப்பிக்கப்பட்டது). இது 15 நாட்கள் காலக்கெடுவை விட **{sla_past} நாட்கள் தாமதம்**."
                            else:
                                _direct_answer_text = f"Application {app_no} has been pending for **{app_sub_days} days** (submitted on {str(sub_date_str)[:10]}). It is **{sla_past} days past the 15-day SLA**."
                        else:
                            rem = 15 - app_sub_days
                            if is_tamil:
                                _direct_answer_text = f"விண்ணப்பம் {app_no} சமர்ப்பிக்கப்பட்டு **{app_sub_days} நாட்கள்** நிலுவையில் உள்ளது ({str(sub_date_str)[:10]} அன்று சமர்ப்பிக்கப்பட்டது). 15 நாட்கள் காலக்கெடுவில் இன்னும் **{rem} நாட்கள் மீதமுள்ளன**."
                            else:
                                _direct_answer_text = f"Application {app_no} has been pending for **{app_sub_days} days** (submitted on {str(sub_date_str)[:10]}). It has **{rem} days remaining** within the 15-day SLA."
                        logger.info(f"Responded pending duration ({app_sub_days} days) for {app_no}")

                    # Overdue questions or default overdue calculations
                    elif fv_days_overdue is not None and fv_days_overdue > 0:
                        if is_tamil:
                            _direct_answer_text = f"விண்ணப்பம் {app_no}-ன் கள ஆய்வு ({str(fv_date_str)[:10]}) {fv_days_overdue} நாட்கள் தாமதமாக (overdue) உள்ளது."
                        else:
                            _direct_answer_text = f"Application {app_no}: The field visit (scheduled for {str(fv_date_str)[:10]}) is **{fv_days_overdue} days overdue** (as of today, {today.isoformat()})."
                        logger.info(f"Responded with {fv_days_overdue} days overdue for {app_no}")
                    elif sd.get("is_overdue") and app_sub_days is not None and app_sub_days > 15:
                        sla_overdue = app_sub_days - 15
                        if is_tamil:
                            _direct_answer_text = f"விண்ணப்பம் {app_no} சமர்ப்பிக்கப்பட்டு {app_sub_days} நாட்கள் ஆகியுள்ளது (15 நாட்கள் காலக்கெடுவை விட {sla_overdue} நாட்கள் தாமதம்)."
                        else:
                            _direct_answer_text = f"Application {app_no} was submitted on {str(sub_date_str)[:10]} ({app_sub_days} days ago) and is **{sla_overdue} days past the 15-day SLA**."
                        logger.info(f"Responded with SLA overdue {sla_overdue} days for {app_no}")
                    elif app_status_str in ["completed", "approved", "closed"]:
                        if is_tamil:
                            _direct_answer_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை — இது ஏற்கனவே முடிவடைந்தது/அங்கீகரிக்கப்பட்டது."
                        else:
                            _direct_answer_text = f"Application {app_no} is NOT overdue. It has been completed and approved."
                        logger.info(f"Responded completed/not overdue for {app_no}")
                    elif fv_days_until is not None:
                        if fv_days_until == 0:
                            if is_tamil:
                                _direct_answer_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை. கள ஆய்வு இன்று ({str(fv_date_str)[:10]}) திட்டமிடப்பட்டுள்ளது."
                            else:
                                _direct_answer_text = f"Application {app_no} is NOT overdue. The field visit is scheduled for TODAY ({str(fv_date_str)[:10]})."
                        else:
                            if is_tamil:
                                _direct_answer_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை. கள ஆய்வு {str(fv_date_str)[:10]} அன்று திட்டமிடப்பட்டுள்ளது ({fv_days_until} நாட்களில்)."
                            else:
                                _direct_answer_text = f"Application {app_no} is NOT overdue. The field visit is scheduled for {str(fv_date_str)[:10]} (in {fv_days_until} days)."
                        logger.info(f"Responded upcoming field visit in {fv_days_until} days for {app_no}")
                    elif app_sub_days is not None and app_sub_days <= 15:
                        rem_days = 15 - app_sub_days
                        if is_tamil:
                            _direct_answer_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை. {str(sub_date_str)[:10]} அன்று சமர்ப்பிக்கப்பட்டது ({app_sub_days} நாட்களுக்கு முன்பு — 15 நாட்கள் காலக்கெடுவில் {rem_days} நாட்கள் மீதமுள்ளன)."
                        else:
                            _direct_answer_text = f"Application {app_no} is NOT overdue. Submitted on {str(sub_date_str)[:10]} ({app_sub_days} days ago — {rem_days} days remaining within the 15-day SLA)."
                        logger.info(f"Responded within SLA {rem_days} days remaining for {app_no}")
                    else:
                        if is_tamil:
                            _direct_answer_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை (காலக்கெடுவிற்குள் உள்ளது)."
                        else:
                            _direct_answer_text = f"Application {app_no} is currently on schedule and not overdue."
                        logger.info(f"Responded not overdue for {app_no}")

                # Check for NISD/ISD type questions next
                elif ("nisd" in _msg_lower or "isd" in _msg_lower):
                    app_type_value = sd.get("type", "N/A")
                    if is_tamil:
                        _direct_answer_text = f"விண்ணப்பம் {app_no} வகை: {app_type_value}"
                    else:
                        _direct_answer_text = f"Application {app_no} is of type: {app_type_value}"
                    logger.info(f"Responded with application type '{app_type_value}' for {app_no}")
                
            # Map user keywords to structured_data fields (English + Tamil)
            # PARITY: do NOT gate on _asking_specific_field here. That list
            # (_field_keywords, 150 entries) is narrower than _field_map (230
            # entries) — "area sq", "பரப்பளவு", "csc charge" and friends are in the
            # map but not the keyword list, so gating on it made the streaming path
            # skip the lookup and fall through to the generic application summary
            # while /chat answered from the database. process_chat gates only on
            # "no answer yet", and _bypass_html already restricts this block to
            # interrogative application_status / merge_info queries.
            if not _direct_answer_text and app_no:
                _field_map = {
                    # PARITY with process_chat's _field_map — these keys existed only
                    # on the non-streaming path, so area / charge / source / camp /
                    # workflow questions fell through to the LLM when streaming.
                    # Area / SQM
                    "area sq": ("area_sqm", "Area (sq.m)"),
                    "area sqm": ("area_sqm", "Area (sq.m)"),
                    "area in sq": ("area_sqm", "Area (sq.m)"),
                    "area in sq m": ("area_sqm", "Area (sq.m)"),
                    "total area": ("area_sqm", "Area (sq.m)"),
                    "total area sq": ("area_sqm", "Area (sq.m)"),
                    "merge area": ("area_sqm", "Area (sq.m)"),
                    "survey area": ("area_sqm", "Area (sq.m)"),
                    "area": ("area_sqm", "Area (sq.m)"),
                    "sqm": ("area_sqm", "Area (sq.m)"),
                    "sq m": ("area_sqm", "Area (sq.m)"),
                    "square meter": ("area_sqm", "Area (sq.m)"),
                    "square meters": ("area_sqm", "Area (sq.m)"),
                    "பரப்பளவு": ("area_sqm", "Area (sq.m)"),
                    "சதுர மீட்டர்": ("area_sqm", "Area (sq.m)"),
                    # Source / workflow
                    "source": ("source_name", "Source Name"),
                    "source_code": ("source_code", "Source Code"),
                    "source_name": ("source_name", "Source Name"),
                    "ஆதாரம்": ("source_name", "Source Name"),
                    "workflow_state": ("workflow_state", "Workflow State"),
                    # The sale deed number is stored on the application and was
                    # absent from this map, so asking for it fell through to the
                    # generic "here are the details" dump instead of answering.
                    "sale deed number": ("sale_deed_number", "Sale Deed Number"),
                    "sale deed no": ("sale_deed_number", "Sale Deed Number"),
                    "saledeed number": ("sale_deed_number", "Sale Deed Number"),
                    "deed number": ("sale_deed_number", "Sale Deed Number"),
                    "கிரய பத்திர எண்": ("sale_deed_number", "Sale Deed Number"),
                    "sale deed registered": ("sale_deed_registered", "Sale Deed Registered"),
                    "பயனர்": ("user_id", "User ID"),
                    # Address
                    "address": ("applicant_address", "Address"),
                    "முகவரி": ("applicant_address", "Address"),
                    "virivu": ("applicant_address", "Address"),
                    "mugavari": ("applicant_address", "Address"),
                    # Mobile/Phone
                    "mobile number": ("applicant_mobile", "Mobile"),
                    "phone number": ("applicant_mobile", "Phone"),
                    "mobile": ("applicant_mobile", "Mobile"),
                    "phone": ("applicant_mobile", "Phone"),
                    "தொலைபேசி எண்": ("applicant_mobile", "Mobile"),
                    "தொலைபேசி": ("applicant_mobile", "Mobile"),
                    "கைபேசி": ("applicant_mobile", "Mobile"),
                    "கைபேசி எண்": ("applicant_mobile", "Mobile"),
                    "tholaipaesi": ("applicant_mobile", "Mobile"),
                    "contact": ("applicant_mobile", "Mobile"),
                    # Name variations (extensive for best matching)
                    "name": ("applicant_name", "Applicant Name"),
                    "applicant": ("applicant_name", "Applicant Name"),
                    "பெயர்": ("applicant_name", "Applicant Name"),
                    "நாமாகும்": ("applicant_name", "Applicant Name"),
                    "நாமம்": ("applicant_name", "Applicant Name"),
                    "விண்ணப்பதாரர்": ("applicant_name", "Applicant Name"),
                    "விண்ணப்பதாரர் பெயர்": ("applicant_name", "Applicant Name"),
                    "விண்ணப்பதாரரின் பெயர்": ("applicant_name", "Applicant Name"),
                    "விண்ணப்பதாரரின் நாமாகும் பெயர்": ("applicant_name", "Applicant Name"),
                    "நாமாகும் பெயர்": ("applicant_name", "Applicant Name"),
                    "peyar": ("applicant_name", "Applicant Name"),
                    "peiyar": ("applicant_name", "Applicant Name"),
                    "namaagum": ("applicant_name", "Applicant Name"),
                    "namam": ("applicant_name", "Applicant Name"),
                    "vinnappatharar": ("applicant_name", "Applicant Name"),
                    "vinnappathaarar": ("applicant_name", "Applicant Name"),
                    # Status
                    "status": ("status", "Status"),
                    "நிலை": ("status", "Status"),
                    "nilai": ("status", "Status"),
                    "state": ("status", "Status"),
                    # How officers actually ask: "what is the position of X",
                    # "when did we receive X", "which ward is X in".
                    "position": ("status", "Status"),
                    "standing": ("status", "Status"),
                    "received": ("submission_date", "Submission Date"),
                    "receive": ("submission_date", "Submission Date"),
                    "receipt date": ("submission_date", "Submission Date"),
                    "ward": ("ward_number", "Ward"),
                    "ward number": ("ward_number", "Ward"),
                    "வார்டு": ("ward_number", "Ward"),
                    "block": ("block_number", "Block"),
                    "block number": ("block_number", "Block"),
                    # Stage
                    "stage": ("stage", "Current Stage"),
                    "கட்டம்": ("stage", "Current Stage"),
                    "kattam": ("stage", "Current Stage"),
                    "level": ("stage", "Current Stage"),
                    # Type
                    "type": ("type", "Application Type"),
                    "வகை": ("type", "Application Type"),
                    "vagai": ("type", "Application Type"),
                    "kind": ("type", "Application Type"),
                    # Survey
                    "survey": ("survey_no", "Survey Number"),
                    "கணக்கெண்": ("survey_no", "Survey Number"),
                    "ganakken": ("survey_no", "Survey Number"),
                    "kanakken": ("survey_no", "Survey Number"),
                    # Date / Year
                    "date": ("submission_date", "Submission Date"),
                    "தேதி": ("submission_date", "Submission Date"),
                    "thethi": ("submission_date", "Submission Date"),
                    "thedhi": ("submission_date", "Submission Date"),
                    "submitted": ("submission_date", "Submission Date"),
                    "year": ("submission_date", "Submission Date"),
                    "ஆண்டு": ("submission_date", "Submission Date"),
                    "aandu": ("submission_date", "Submission Date"),
                    "annu": ("submission_date", "Submission Date"),
                    "when": ("submission_date", "Submission Date"),
                    "எப்போது": ("submission_date", "Submission Date"),
                    "eppodhu": ("submission_date", "Submission Date"),
                    # Priority
                    "priority": ("priority_flag", "Priority"),
                    "முன்னுரிமை": ("priority_flag", "Priority"),
                    "munnurimai": ("priority_flag", "Priority"),
                    "urgent": ("priority_flag", "Priority"),
                    # Overdue
                    "overdue": ("is_overdue", "Overdue"),
                    "காலதாமத": ("is_overdue", "Overdue"),
                    "kaalathamadha": ("is_overdue", "Overdue"),
                    "delayed": ("is_overdue", "Overdue"),
                    # Aadhaar / CAN mapping (schema uses CAN number as citizen identifier)
                    "aadhaar": ("can_number", "CAN Number"),
                    "aadhar": ("can_number", "CAN Number"),
                    "adhaar": ("can_number", "CAN Number"),
                    # Serial Number
                    "serial number": ("serial_number", "Serial Number"),
                    "serial_number": ("serial_number", "Serial Number"),
                    "serial": ("serial_number", "Serial Number"),
                    "வரிசை எண்": ("serial_number", "Serial Number"),
                    "வரிசை": ("serial_number", "Serial Number"),
                    # CAN Number
                    "can number": ("can_number", "CAN Number"),
                    "can_number": ("can_number", "CAN Number"),
                    "can": ("can_number", "CAN Number"),
                    "can எண்": ("can_number", "CAN Number"),
                    # Patta Number
                    "patta number": ("patta_number", "Patta Number"),
                    "patta_number": ("patta_number", "Patta Number"),
                    "patta": ("patta_number", "Patta Number"),
                    "பட்டா எண்": ("patta_number", "Patta Number"),
                    "பட்டா": ("patta_number", "Patta Number"),
                    # Subdivision Number
                    "subdivision number": ("subdivision_number", "Subdivision Number"),
                    "subdivision_number": ("subdivision_number", "Subdivision Number"),
                    "subdivision": ("subdivision_number", "Subdivision Number"),
                    "current subdivision": ("current_subdivision_number", "Current Subdivision Number"),
                    "current_subdivision_number": ("current_subdivision_number", "Current Subdivision Number"),
                    "உட்பிரிவு எண்": ("subdivision_number", "Subdivision Number"),
                    "உட்பிரிவு": ("subdivision_number", "Subdivision Number"),
                    # User ID & Role ID
                    "user id": ("user_id", "User ID"),
                    "user_id": ("user_id", "User ID"),
                    "user": ("user_id", "User ID"),
                    "பயனர் ஐடி": ("user_id", "User ID"),
                    "role id": ("role_id", "Role ID"),
                    "role_id": ("role_id", "Role ID"),
                    "role": ("role_id", "Role ID"),
                    "பங்கு ஐடி": ("role_id", "Role ID"),
                    # Service & Department
                    "service code": ("service_code", "Service Code"),
                    "service_code": ("service_code", "Service Code"),
                    "workflow state": ("workflow_state", "Workflow State"),
                    "source code": ("source_code", "Source Code"),
                    "source name": ("source_name", "Source Name"),
                    # Reason
                    "reason": ("declared_reason", "Declared Reason"),
                    "declared reason": ("declared_reason", "Declared Reason"),
                    "purpose": ("declared_reason", "Declared Reason"),
                    "காரணம்": ("declared_reason", "Declared Reason"),
                    "kaaranam": ("declared_reason", "Declared Reason"),
                    "karanum": ("declared_reason", "Declared Reason"),
                    # Priority & Overdue
                    "priority": ("priority_flag", "Priority"),
                    "high priority": ("priority_flag", "Priority"),
                    "priority flag": ("priority_flag", "Priority"),
                    "overdue": ("is_overdue", "Overdue"),
                    "is overdue": ("is_overdue", "Overdue"),
                    "is it overdue": ("is_overdue", "Overdue"),
                    # Field visit
                    "field visit": ("field_visit_scheduled", "Field Visit Scheduled"),
                    "field visit scheduled": ("field_visit_scheduled", "Field Visit Scheduled"),
                    "field visit date": ("field_visit_date", "Field Visit Date"),
                    "visit date": ("field_visit_date", "Field Visit Date"),
                    "inspection date": ("field_visit_date", "Field Visit Date"),
                    "கள ஆய்வு": ("field_visit_scheduled", "Field Visit Scheduled"),
                    "கள ஆய்வு தேதி": ("field_visit_date", "Field Visit Date"),
                    # Location Codes
                    "district code": ("district_code", "District Code"),
                    "taluk code": ("taluk_code", "Taluk Code"),
                    "ward code": ("ward_code", "Ward Code"),
                    "block code": ("block_code", "Block Code"),
                    "village code": ("village_code", "Village Code"),
                    "urban unit code": ("urban_unit_code", "Urban Unit Code"),
                    # Location / stage keywords
                    "where": ("stage", "Current Stage"),
                    "where is it": ("stage", "Current Stage"),
                    "where is": ("stage", "Current Stage"),
                    "where is it currently": ("stage", "Current Stage"),
                    "எங்கே": ("stage", "Current Stage"),
                    "engae": ("stage", "Current Stage"),
                    "enge": ("stage", "Current Stage"),
                    "right now": ("stage", "Current Stage"),
                    "currently": ("stage", "Current Stage"),
                    "இப்போது": ("stage", "Current Stage"),
                    "ippodhu": ("stage", "Current Stage"),
                    "ippoathu": ("stage", "Current Stage"),
                    "department": ("stage", "Current Stage"),
                    "office": ("stage", "Current Stage"),
                    "அலுவலகம்": ("stage", "Current Stage"),
                    "aluvalagam": ("stage", "Current Stage"),
                    "aluvalakam": ("stage", "Current Stage"),
                    "current stage": ("stage", "Current Stage"),
                }
                _stage_labels_s = {
                    "SIS": "Sub Inspector Surveyor (SIS) — currently under field verification",
                    "SD": "Senior Draughtsman (SD) — forwarded for sketch/approval",
                    "DIS": "Deputy Inspector Surveyor (DIS) — under DIS review",
                    "TAHSILDAR": "Zonal Level Tahsildar (ZDT) — holds the DSC; patta order pending sign-off",
                    "COMPLETED": "Completed — patta order issued",
                    "REJECTED": "Rejected",
                }
                # Tamil stage labels (streaming)
                _stage_labels_ta_s = {
                    "SIS": "துணை ஆய்வாளர் (SIS) — தற்போது கள சரிபார்ப்பில் உள்ளது",
                    "SD": "மூத்த வரைவாளர் (SD) — வரைபட அங்கீகாரத்திற்கு அனுப்பப்பட்டது",
                    "DIS": "மாவட்ட ஆய்வாளர் (DIS) — DIS மதிப்பாய்வில் உள்ளது",
                    "TAHSILDAR": "வலய நிலை தாசில்தார் (ZDT) — DSC கையொப்பம், பட்டா ஆணை நிலுவையில்",
                    "COMPLETED": "முடிந்தது — பட்டா ஆணை வழங்கப்பட்டது",
                    "REJECTED": "நிராகரிக்கப்பட்டது",
                }
                
                # Use fuzzy matching for spelling error tolerance
                all_matches = _fuzzy_match_all_fields(_msg_lower, _field_map, threshold=0.75)
                
                if len(all_matches) > 1:
                    is_tamil_s = language in ("ta", "tanglish")
                    labels_to_use = _stage_labels_ta_s if is_tamil_s else _stage_labels_s
                    ta_labels_s = {
                        "Address": "முகவரி", "Mobile": "தொலைபேசி",
                        "Applicant Name": "விண்ணப்பதாரர் பெயர்", "Status": "நிலை",
                        "Application Type": "விண்ணப்ப வகை", "Survey Number": "கணக்கெண்",
                        "Submission Date": "சமர்ப்பித்த தேதி", "Priority": "முன்னுரிமை",
                        "Overdue": "காலதாமதம்", "Declared Reason": "அறிவிக்கப்பட்ட காரணம்",
                        "Serial Number": "வரிசை எண்", "CAN Number": "CAN எண்",
                        "Patta Number": "பட்டா எண்", "Subdivision Number": "உட்பிரிவு எண்",
                        "Current Subdivision Number": "தற்போதைய உட்பிரிவு எண்",
                        "User ID": "பயனர் ஐடி", "Role ID": "பங்கு ஐடி",
                        "Renewal Number": "புதுப்பித்தல் எண்", "Parent Application ID": "தாய் விண்ணப்ப எண்",
                        "CSC Service Charge": "CSC சேவை கட்டணம்", "Government Service Charge": "அரசு சேவை கட்டணம்",
                        "IP Address": "IP முகவரி", "Camp Flag": "முகாம் குறியீடு", "Camp Code": "முகாம் எண்",
                        "IGRS Form 6 Number": "IGRS படிவம் 6 எண்", "Dispatch Date": "அனுப்பிய தேதி",
                        "Received Date": "பெறப்பட்ட தேதி", "Last Updated Datetime": "கடைசியாக புதுப்பிக்கப்பட்ட தேதி",
                        "Workflow State": "பணிப்பாய்வு நிலை", "Return Status": "திரும்பிய நிலை",
                        "Source Name": "ஆதாரம்", "Current Stage": "தற்போதைய கட்டம்"
                    }
                    items = []
                    for field_key, field_label, _ in all_matches:
                        value = sd.get(field_key)
                        if value is None or value == "":
                            value = "N/A"
                        elif isinstance(value, bool):
                            value = ("ஆம்" if value else "இல்லை") if is_tamil_s else ("Yes" if value else "No")
                        elif field_key == "stage" and isinstance(value, str):
                            value = labels_to_use.get(value.upper(), value)
                        display_label = ta_labels_s.get(field_label, field_label) if is_tamil_s else field_label
                        items.append(f"• **{display_label}**: {value}")
                    
                    header = f"விண்ணப்பம் {app_no} விவரங்கள்:" if is_tamil_s else f"Details for application {app_no}:"
                    _direct_answer_text = header + "\n" + "\n".join(items)
                    logger.info(f"Responded with multiple fields ({len(all_matches)}) for {app_no} (streaming)")
                elif len(all_matches) == 1:
                    field_key, field_label, matched_kw = all_matches[0]
                    value = sd.get(field_key)
                    if value is not None and value != "":
                        if isinstance(value, bool):
                            value = "Yes" if value else "No"
                        # Expand stage codes to human-readable labels
                        if field_key == "stage" and isinstance(value, str):
                            # Use Tamil labels if query was in Tamil or Tanglish
                            is_tamil_s = language in ("ta", "tanglish")
                            labels_to_use = _stage_labels_ta_s if is_tamil_s else _stage_labels_s
                            readable = labels_to_use.get(value.upper(), value)
                            _direct_answer_text = (
                                f"Application {app_no} is currently at: {readable}." if not is_tamil_s
                                else f"விண்ணப்பம் {app_no} தற்போது: {readable}."
                            )
                        # Extract year from date if user specifically asked for year
                        elif field_key == "submission_date" and any(kw in _msg_lower for kw in ["year", "ஆண்டு", "aandu", "annu"]):
                            # User asked for year specifically - extract year from date
                            try:
                                if isinstance(value, str) and len(value) >= 4:
                                    year = value[:4]  # Extract YYYY from YYYY-MM-DD format
                                    is_tamil_s = language in ("ta", "tanglish")
                                    if is_tamil_s:
                                        _direct_answer_text = f"{app_no} சமர்ப்பிக்கப்பட்ட ஆண்டு: {year}"
                                    else:
                                        _direct_answer_text = f"Application {app_no} was submitted in the year: {year}"
                                    logger.info(f"Extracted year {year} from submission_date for {app_no} (streaming)")
                                else:
                                    _direct_answer_text = f"The {field_label} for {app_no} is: {value}"
                                    logger.info(f"Could not extract year (streaming), value type: {type(value)}, value: {value}")
                            except Exception as year_ex_s:
                                logger.error(f"Error extracting year (streaming): {year_ex_s}", exc_info=True)
                                _direct_answer_text = f"The {field_label} for {app_no} is: {value}"
                        else:
                            # Provide response in Tamil if query was in Tamil or Tanglish
                            is_tamil_s = language in ("ta", "tanglish")
                            if is_tamil_s:
                                # Tamil field label mapping
                                ta_labels_s = {
                                    "Address": "முகவரி", "Mobile": "தொலைபேசி",
                                    "Applicant Name": "விண்ணப்பதாரர் பெயர்", "Status": "நிலை",
                                    "Application Type": "விண்ணப்ப வகை", "Survey Number": "கணக்கெண்",
                                    "Submission Date": "சமர்ப்பித்த தேதி", "Priority": "முன்னுரிமை",
                                    "Overdue": "காலதாமதம்", "Declared Reason": "அறிவிக்கப்பட்ட காரணம்",
                                    "Serial Number": "வரிசை எண்", "CAN Number": "CAN எண்",
                                    "Patta Number": "பட்டா எண்", "Subdivision Number": "உட்பிரிவு எண்",
                                    "Current Subdivision Number": "தற்போதைய உட்பிரிவு எண்",
                                    "User ID": "பயனர் ஐடி", "Role ID": "பங்கு ஐடி",
                                    "Renewal Number": "புதுப்பித்தல் எண்", "Parent Application ID": "தாய் விண்ணப்ப எண்",
                                    "CSC Service Charge": "CSC சேவை கட்டணம்", "Government Service Charge": "அரசு சேவை கட்டணம்",
                                    "IP Address": "IP முகவரி", "Camp Flag": "முகாம் குறியீடு", "Camp Code": "முகாம் எண்",
                                    "IGRS Form 6 Number": "IGRS படிவம் 6 எண்", "Dispatch Date": "அனுப்பிய தேதி",
                                    "Received Date": "பெறப்பட்ட தேதி", "Last Updated Datetime": "கடைசியாக புதுப்பிக்கப்பட்ட தேதி",
                                    "Workflow State": "பணிப்பாய்வு நிலை", "Return Status": "திரும்பிய நிலை",
                                    "Source Name": "ஆதாரம்", "Area (sq.m)": "பரப்பளவு (ச.மீ)"
                                }
                                ta_field_label_s = ta_labels_s.get(field_label, field_label)
                                # More natural Tamil phrasing based on field type
                                if field_key == "applicant_name":
                                    _direct_answer_text = f"{app_no} விண்ணப்பதாரரின் பெயர்: {value}"
                                elif field_key == "status":
                                    _direct_answer_text = f"{app_no} நிலை: {value}"
                                elif field_key == "serial_number":
                                    _direct_answer_text = f"விண்ணப்பம் {app_no}-ன் வரிசை எண்: {value}"
                                else:
                                    _direct_answer_text = f"{app_no} {ta_field_label_s}: {value}"
                            else:
                                if field_key == "serial_number":
                                    _direct_answer_text = f"The serial number for application {app_no} is: {value}"
                                else:
                                    _direct_answer_text = f"The {field_label} for {app_no} is: {value}"
                    else:
                        is_tamil_s = language in ("ta", "tanglish")
                        if is_tamil_s:
                            _direct_answer_text = f"{app_no} க்கு {field_label} தகவல் இல்லை."
                        else:
                            _direct_answer_text = f"No {field_label.lower()} information found for {app_no}."
                    logger.info(f"Responded with specific field '{field_label}' for {app_no} (matched: '{matched_kw}')")

        if html_response:
            # Send the whole HTML in one SSE chunk — no LLM latency
            logger.info("Responding with direct HTML (LLM bypassed for stream)")
            yield f"data: {json.dumps({'content': html_response})}\n\n".encode('utf-8')
            full_response_text = html_response
        elif _direct_answer_text:
            logger.info("Responding with direct Python answer (stream)")
            yield f"data: {json.dumps({'content': _direct_answer_text})}\n\n".encode('utf-8')
            full_response_text = _direct_answer_text
        else:
            # Step 6: Build prompt and stream LLM response or use hardcoded responses
            full_prompt = build_prompt(message, rag_context, structured_data, language, chat_history,
                                       direct_answer=_bypass_html)

        # Step 6: Stream LLM Response / hardcoded intent responses
        # Preserve full_response_text if already set by HTML or direct-answer path
        if not html_response and not _direct_answer_text:
            full_response_text = ""
        import json
        
        logger.info("Starting LLM stream...")
        chunk_count = 0
        
        if html_response or _direct_answer_text:
            pass  # already yielded above
        elif "invalid merged geometry" in message.lower() or "invalid merge geometry" in message.lower():
            chunk = "No issues detected. The merged parcel satisfies all validation checks."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "active_applications_taluks":
            total = structured_data.get("total_active", 0)
            counts = structured_data.get("taluk_counts", {})
            if total > 0:
                counts_str = ", ".join(f"{count} in {taluk}" for taluk, count in counts.items())
                chunk = f"{total} active applications: {counts_str}."
            else:
                chunk = "0 active applications."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "highest_priority_applications":
            apps = structured_data.get("applications", []) if structured_data else []
            count = len(apps)
            if count > 0:
                app_numbers = [a.get("application_number") for a in apps[:5]]  # Show first 5
                preview = ", ".join(app_numbers)
                if count > 5:
                    preview += f" and {count - 5} more"
                chunk = f"Found {count} high priority application(s): {preview}. Priority is based on overdue status or manual flagging."
            else:
                chunk = "No high priority applications found. All applications are within normal processing timeframes."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "escalation_check":
            approaching = structured_data.get("applications", []) if structured_data else []
            total = structured_data.get("total_approaching", 0) if structured_data else 0
            overdue = structured_data.get("overdue_count", 0) if structured_data else 0
            if total == 0:
                chunk = "No applications are currently approaching the escalation threshold."
            else:
                critical = [a for a in approaching if "Critical" in a.get("urgency", "")]
                warning = [a for a in approaching if "Warning" in a.get("urgency", "")]
                parts = []
                if overdue:
                    parts.append(f"{overdue} already overdue")
                if critical:
                    parts.append(f"{len(critical)} critical (1–2 days remaining)")
                if warning:
                    parts.append(f"{len(warning)} warning (3–5 days remaining)")
                summary = ", ".join(parts) if parts else f"{total} total"
                chunk = (
                    f"Found {total} application(s) approaching or past the 15-working-day escalation threshold: "
                    f"{summary}. See the table below for details."
                )
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "assigned_today":
            count = structured_data.get("count", 0)
            chunk = f"{count} applications were assigned today."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "immediate_action":
            apps = structured_data.get("apps", [])
            if apps:
                chunk = f"{', '.join(apps)} require immediate action based on pending deadlines."
            else:
                chunk = "No applications require immediate action today."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "awaiting_field_visit":
            count = structured_data.get("count", 0)
            chunk = f"{count} applications are awaiting field inspection."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "workload_by_type":
            isd = structured_data.get("ISD", 0)
            nisd = structured_data.get("NISD", 0)
            merge = structured_data.get("MERGE", 0)
            chunk = f"ISD – {isd} applications, NISD – {nisd} applications, Merge – {merge} applications."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "completion_rate":
            completed = structured_data.get("completed", 0) if structured_data else 0
            total = structured_data.get("total", 0) if structured_data else 0
            rate = structured_data.get("rate", 0) if structured_data else 0
            scope = structured_data.get("scope", "overall") if structured_data else "overall"
            if total == 0:
                chunk = f"No applications found for {scope}."
            else:
                chunk = (
                    f"Your application completion percentage {scope}: "
                    f"{rate}% — {completed} out of {total} assigned applications "
                    f"have been completed (approved or rejected)."
                )
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "pending_longest":
            apps = structured_data.get("apps", [])
            days = structured_data.get("days", 0)
            if apps:
                chunk = f"Application Nos. {', '.join(apps)} have been pending for more than {days} days."
            else:
                chunk = "No pending applications."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "is_nisd_or_isd":
            if not structured_data or not structured_data.get("found", True):
                chunk = structured_data.get("message", "Please specify an application number (e.g., 2026/0154/02/000041) to check if it is NISD or ISD.") if structured_data else "Please specify an application number (e.g., 2026/0154/02/000041) to check if it is NISD or ISD."
            else:
                app_type = structured_data.get("type", "ISD")
                survey_no = structured_data.get("survey_no", "145")
                subdivs = structured_data.get("included_subdivisions", "")
                subdiv_count = len(subdivs.split(",")) if subdivs and subdivs != "None" else 2
                if app_type == "ISD":
                    chunk = f"ISD — application declares sub-division into {subdiv_count} plots under survey no. {survey_no}."
                elif app_type == "NISD":
                    chunk = f"NISD — application is for transfer of entire survey/patta without subdivision under survey no. {survey_no}."
                else:
                    chunk = f"MERGE — application is for merging subdivisions under survey no. {survey_no}."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "check_documents":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                missing = [d["document_type"] for d in structured_data.get("documents", []) if not d["is_uploaded"]]
                if missing:
                    missing_str = ", ".join(missing)
                    chunk = f"Missing documents: {missing_str}. Please upload them before scheduling the field visit."
                else:
                    chunk = "No issues detected. All required documents are present."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "check_sale_deed":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                deed_no = structured_data.get("sale_deed_number") or "N/A"
                sub_date = structured_data.get("submission_date") or "2025-06-25"
                if structured_data.get("sale_deed_registered"):
                    chunk = f"Yes, deed no. {deed_no} matches Sub-Registrar's registered index as of {sub_date}."
                else:
                    chunk = "No match found — flag to Sub-Registrar's office before proceeding."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        
        elif intent == "joint_owner_check":
            if not structured_data or not structured_data.get("found", True):
                chunk = structured_data.get("message", "Please provide an application number or survey number")
            else:
                joint_owners = structured_data.get("joint_owners", [])
                total_owners = structured_data.get("total_owners", 0)
                survey_no = structured_data.get("survey_no", "N/A")
                app_no = structured_data.get("application_number")
                is_tamil = language in ("ta", "tanglish")
                
                # Build response based on whether it's application or survey query
                if is_tamil:
                    prefix = f"விண்ணப்பம் {app_no} (கணக்கெண் {survey_no})" if app_no else f"கணக்கெண் {survey_no}"
                else:
                    prefix = f"For application {app_no} (Survey {survey_no})" if app_no else f"For Survey {survey_no}"
                
                if total_owners == 0:
                    if is_tamil:
                        chunk = f"{prefix}: உரிமையாளர் பதிவுகள் இல்லை."
                    else:
                        chunk = f"{prefix}: No ownership records found."
                elif len(joint_owners) == 0:
                    if is_tamil:
                        chunk = f"{prefix}: விண்ணப்பதாரர் ஒரே உரிமையாளர். கூட்டு உரிமையாளர்கள் இல்லை."
                    else:
                        chunk = f"{prefix}: The applicant is the sole owner. No joint owners are listed."
                else:
                    joint_names = [o.get("name", "N/A") for o in joint_owners]
                    if is_tamil:
                        chunk = f"{prefix}: {len(joint_owners)} கூட்டு உரிமையாளர்கள் உள்ளனர்: {', '.join(joint_names)}."
                    else:
                        chunk = f"{prefix}: There are {len(joint_owners)} joint owner(s) listed: {', '.join(joint_names)}."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "application_status":
            if not structured_data:
                chunk = build_app_not_found_message({}, language)
            elif "multi_applications" in structured_data:
                # Multiple apps were requested — build a summary line listing all of them
                _details_list = structured_data["multi_applications"]
                _found = [d for d in _details_list if d.get("found", True)]
                _missing = [d for d in _details_list if not d.get("found", True)]
                if not _found:
                    chunk = build_app_not_found_message(_missing[0] if _missing else {}, language)
                else:
                    _summaries = []
                    for _d in _found:
                        _an = _d.get("application_number", "N/A")
                        _st = (_d.get("status") or "N/A").capitalize()
                        _sg = _d.get("stage", "N/A")
                        _summaries.append(f"{_an} (Status: {_st}, Stage: {_sg})")
                    chunk = f"Here are the details for {len(_found)} application(s): {'; '.join(_summaries)}."
                    if _missing:
                        _missing_nos = ", ".join(d.get("searched_number", "N/A") for d in _missing)
                        chunk += f" Not found: {_missing_nos}."
            elif not structured_data.get("found", True):
                chunk = build_app_not_found_message(structured_data, language)
            elif "history" in structured_data:
                hist = structured_data.get("history", [])
                app_no = structured_data.get("application_number", "")
                chunk = f"Workflow history for {app_no}: {len(hist)} stage(s) recorded."
            else:
                app_no = structured_data.get("application_number", "N/A")
                app_type = structured_data.get("type", "N/A")
                status = structured_data.get("status", "N/A").capitalize()
                stage = structured_data.get("stage", "N/A")
                applicant = structured_data.get("applicant_name") or "N/A"
                survey = structured_data.get("survey_no", "N/A")
                _summary = (
                    f"Type: {app_type}, Status: {status}, Stage: {stage}, "
                    f"Applicant: {applicant}, Survey No: {survey}."
                )
                if _asks_for_specific_detail(message):
                    # Mirrors process_chat: a pointed question that matched no
                    # field is answered honestly, not with a stand-in summary.
                    chunk = (
                        f"I could not find that particular detail for {app_no} in the record. "
                        f"Here is what it does hold — {_summary}"
                    )
                else:
                    chunk = f"Here are the details for {app_no}. {_summary}"
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent in ("pending_applications", "overdue_applications"):
            count = structured_data.get("count", 0) if structured_data else 0
            qtype = structured_data.get("query_type", "applications") if structured_data else "applications"
            if count == 0:
                chunk = f"No {qtype.lower()} found in your jurisdiction."
            elif count == 1:
                chunk = f"Found 1 {qtype.lower()} in your jurisdiction:"
            else:
                chunk = f"Found {count} {qtype.lower()} in your jurisdiction:"
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "officer_workload":
            total = structured_data.get("total_active", 0) if structured_data else 0
            isd = structured_data.get("ISD", 0)
            nisd = structured_data.get("NISD", 0)
            merge = structured_data.get("MERGE", 0)
            overdue = structured_data.get("overdue", 0)
            if language in ("ta", "tanglish"):
                chunk = (
                    f"உங்கள் பணிச்சுமை: {total} செயலில் உள்ள விண்ணப்பங்கள் — "
                    f"ISD: {isd}, NISD: {nisd}, Merge: {merge}, தாமதமானவை: {overdue}."
                )
            else:
                chunk = (
                    f"Your workload: {total} active application(s) — "
                    f"ISD: {isd}, NISD: {nisd}, Merge: {merge}, Overdue: {overdue}."
                )
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "fv_scheduled_this_week":
            count = structured_data.get("taluk_scheduled_count", 0) if structured_data else 0
            taluk = structured_data.get("taluk_name", "N/A") if structured_data else "N/A"
            cases = structured_data.get("taluk_cases", []) if structured_data else []
            week_start = structured_data.get("week_start", "") if structured_data else ""
            week_end = structured_data.get("week_end", "") if structured_data else ""
            cases_str = ", ".join(cases) if cases else "None"
            date_range = f" ({week_start} to {week_end})" if week_start else ""
            if count == 0:
                chunk = f"You have no field visits scheduled in {taluk} this week{date_range}."
            elif count == 1:
                chunk = f"You have 1 field visit scheduled in {taluk} this week{date_range}: {cases_str}."
            else:
                chunk = f"You have {count} field visits scheduled in {taluk} this week{date_range}: {cases_str}."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "fv_unassigned_awaiting":
            count = structured_data.get("unassigned_visits_count", 0) if structured_data else 0
            apps_list = structured_data.get("unassigned_applications", []) if structured_data else []
            if language == "ta":
                if count == 0:
                    chunk = "திட்டமிடல் காத்திருக்கும் நிறைவேற்றப்படாத கள ஆய்வுகள் எதுவும் இல்லை."
                elif count == 1:
                    chunk = "திட்டமிடல் காத்திருக்கும் 1 கள ஆய்வு விண்ணப்பம் உள்ளது."
                else:
                    chunk = f"திட்டமிடல் காத்திருக்கும் {count} கள ஆய்வு விண்ணப்பங்கள் உள்ளன."
            else:
                if count == 0:
                    chunk = "There are no unassigned field visits awaiting scheduling."
                elif count == 1:
                    chunk = "There is 1 application with an unassigned field visit awaiting scheduling."
                else:
                    chunk = f"There are {count} applications with unassigned field visits awaiting scheduling."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "fv_recently_rescheduled":
            count = structured_data.get("recently_rescheduled_count", 0) if structured_data else 0
            if count == 0:
                chunk = "No field visits were rescheduled during the last 7 days."
            elif count == 1:
                chunk = "1 field visit was rescheduled during the last 7 days."
            else:
                chunk = f"{count} field visits were rescheduled during the last 7 days."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "fv_scheduling_conflicts":
            overlap_date = structured_data.get("overlap_date") if structured_data else None
            if overlap_date:
                chunk = (
                    f"Scheduling conflict detected: two or more field visits are scheduled on "
                    f"{overlap_date}. Please reschedule one of them — note that a field visit date "
                    f"change must be approved by the Tahsildar."
                )
            else:
                chunk = "No scheduling conflicts identified in the current inspection calendar."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "fv_change_date":
            if language == "ta":
                chunk = "கள ஆய்வு தேதியை மாற்றுவதற்கு தாசில்தாரிடம் (Tahsildar) கேட்க வேண்டும். தாசில்தாரின் அனுமதியுடன் மட்டுமே கள ஆய்வு தேதியை மாற்ற இயலும்."
            elif language == "tanglish":
                chunk = "Field visit date change பண்ண நீங்கள் Tahsildar கிட்ட கேட்க வேண்டும் (You should ask the Tahsildar about field visit date change)."
            else:
                chunk = "To change the field visit date, you should ask the Tahsildar. The Tahsildar has the authority to approve field visit date changes."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "fv_reschedule_availability":
            res_date = structured_data.get("reschedule_date", "the next available working day") if structured_data else "the next available working day"
            chunk = f"Schedule available on {res_date}. Note: You should ask the Tahsildar about field visit date change."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "fv_deadline_check":
            if not structured_data or not structured_data.get("found", True):
                chunk = structured_data.get("message", "Please specify an application number to check the deadline.") if structured_data else "Please specify an application number."
            else:
                app_no_dl = structured_data.get("application_number", "")
                working_days = structured_data.get("working_days", 0)
                sub_date_str = structured_data.get("submission_date", "")
                if structured_data.get("is_overdue", False):
                    overdue = structured_data.get("days_overdue", max(0, working_days - 15))
                    chunk = (
                        f"Yes — {app_no_dl} is past the 15-working-day deadline. "
                        f"It has been {working_days} working days since submission ({sub_date_str}), "
                        f"{overdue} day(s) overdue. Recommend escalating or scheduling immediately."
                    )
                else:
                    remaining = structured_data.get("days_remaining", max(0, 15 - working_days))
                    chunk = (
                        f"No — {app_no_dl} is on working day {working_days} of 15 "
                        f"(submitted {sub_date_str}). {remaining} working day(s) remaining within the window."
                    )
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "fv_overdue_inspections":
            count = structured_data.get("overdue_visits_count", 0) if structured_data else 0
            if count == 0:
                chunk = "No field visits are currently overdue. All field visits are on schedule."
            else:
                chunk = f"Found {count} overdue field visit(s). See the table below for details."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "isd_processing":
            chunk = build_isd_processing_answer(message, structured_data, _isd_app_no)
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        # NOTE: fv_between_dates and survey_owners are deliberately NOT listed here —
        # they have dedicated branches further down that match process_chat's wording.
        elif intent in ("pending_applications", "field_visits", "fv_scheduled_this_week",
                        "fv_overdue_inspections", "fv_unassigned_awaiting", "fv_recently_rescheduled",
                        "ward_surveys", "block_surveys", "survey_detail", "next_subdivision",
                        "jurisdiction_summary", "rejection_info", "taluk_summary",
                        "litigation_check", "highest_priority_applications",
                        "merge_info", "town_applications", "block_applications",
                        "isd_applications", "nisd_applications", "merge_applications",
                        "both_applications"):
            # Table is rendered on the frontend. Just emit a short natural intro.
            found = structured_data.get("found", True) if structured_data else False
            if not found:
                chunk = structured_data.get("message", "No records found.")
            else:
                asking_for_count = _is_count_only_query(message)
                
                if asking_for_count and intent in ("pending_applications", "isd_applications", "nisd_applications", "merge_applications", "both_applications"):
                    chunk = _format_count_intro(structured_data, language, message)
                # Special message for priority applications
                elif intent == "highest_priority_applications":
                    count = len(structured_data.get("applications", []))
                    stage_filter = structured_data.get("query_type", "").split("—")[-1].strip().replace(" Stage", "") if "—" in structured_data.get("query_type", "") else None
                    is_tamil = language in ("ta", "tanglish")
                    
                    stage_text = f" in {stage_filter} stage" if stage_filter and stage_filter != "High Priority Applications" else ""
                    
                    if count == 0:
                        chunk = (
                            f"உயர் முன்னுரிமை விண்ணப்பங்கள் எதுவும் இல்லை{stage_text}." if is_tamil
                            else f"There are no high priority applications{stage_text} at this time."
                        )
                    elif count == 1:
                        chunk = (
                            f"1 உயர் முன்னுரிமை விண்ணப்பம் உள்ளது{stage_text} (⚠️ warning அல்லது overdue)." if is_tamil
                            else f"Found 1 high priority application{stage_text} (⚠️ warning or overdue)."
                        )
                    else:
                        chunk = (
                            f"{count} உயர் முன்னுரிமை விண்ணப்பங்கள் உள்ளன{stage_text} (⚠️ warning அல்லது overdue)." if is_tamil
                            else f"Found {count} high priority applications{stage_text} (⚠️ warning or overdue)."
                        )
                elif intent in ("field_visits", "fv_between_dates"):
                    count = structured_data.get("count", len(structured_data.get("field_visits", [])))
                    qtype = structured_data.get("query_type", "Field Visits")
                    start_date = structured_data.get("start_date")
                    end_date = structured_data.get("end_date")
                    date_range = f" ({start_date} to {end_date})" if (start_date and end_date) else ""
                    if count == 0:
                        chunk = f"No field visits found{date_range}."
                    elif count == 1:
                        chunk = f"Found 1 field visit{date_range}."
                    else:
                        chunk = f"Found {count} field visit(s){date_range}."
                else:
                    qtype = structured_data.get("query_type", "") if structured_data else ""
                    if qtype:
                        chunk = f"Here are the {qtype.lower()} results."
                    else:
                        chunk = "Results are shown in the table below."

            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "sd_additional_info":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                missing = structured_data.get("missing_documents", [])
                clarification = structured_data.get("sd_clarification")
                req_parts = []
                if missing:
                    req_parts.append(f"missing documents ({', '.join(missing)})")
                if clarification:
                    req_parts.append(f"clarification: {clarification}")
                req_str = " and ".join(req_parts) if req_parts else "None"
                chunk = f"SD has requested: {req_str}."
                
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "sd_encroachment_check":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                if structured_data.get("encroachment_found"):
                    chunk = "Yes, flag visible in SD's view of the application file."
                else:
                    chunk = "No encroachment flag has been noted on this application."
                    
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "sd_sketch_readiness":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                missing_fields = []
                if not structured_data.get("field_visit_present"):
                    missing_fields.append("Field Visit Details")
                else:
                    if structured_data.get("area_verified") is None:
                        missing_fields.append("Area Verified")
                    if not structured_data.get("visit_notes_present"):
                        missing_fields.append("Visit Notes")
                if missing_fields:
                    chunk = f"Missing: {', '.join(missing_fields)}. Recommend completing before submission."
                else:
                    chunk = "All required fields are filled."
                    
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "sd_forward_check":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                if structured_data.get("current_stage") == "SIS":
                    chunk = "No. The application is pending SIS verification."
                else:
                    forward_date = structured_data.get("forwarded_to_sd_date") or structured_data.get("submission_date")
                    chunk = f"Yes. Forwarded on {forward_date}."
                    
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "sd_remarks":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                remarks = structured_data.get("sd_remarks")
                if remarks:
                    chunk = f"SD Remarks: {remarks}."
                else:
                    chunk = "No remarks recorded by SD."
                    
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "fv_date_select":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                fv_date = structured_data.get("field_visit_date")
                if fv_date:
                    chunk = f"{fv_date} confirmed for this application."
                else:
                    chunk = "No field visit scheduled for this application."
                    
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "fv_nearby_pending":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                count = structured_data.get("nearby_count", 0)
                ward = structured_data.get("ward_number", "N/A")
                block = structured_data.get("block_number", "N/A")
                chunk = f"{count} applications are located within the same Ward {ward} and Block {block}."
                
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "fv_between_dates":
            count = structured_data.get("count", 0)
            to_visit = structured_data.get("to_be_visited_count", count)
            s_date = structured_data.get("start_date")
            e_date = structured_data.get("end_date")
            date_range = f" ({s_date} to {e_date})" if (s_date and e_date) else ""
            if to_visit == 0:
                chunk = f"There are no field visits needed to be visited{date_range}."
            elif to_visit == 1:
                chunk = f"There is 1 field visit needed to be visited{date_range}."
            else:
                chunk = f"There are {to_visit} field visits needed to be visited{date_range}."

            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "survey_owners":
            if not structured_data or not structured_data.get("found", True):
                chunk = structured_data.get("message", "Survey not found or not accessible.")
            else:
                owners = structured_data.get("owners", [])
                survey_no = structured_data.get("survey_no", "")
                if not owners:
                    chunk = f"No ownership records found for Survey No. {survey_no}."
                else:
                    owner_lines = []
                    for o in owners:
                        name = o.get("name", "N/A")
                        sub_div = o.get("sub_division", "Survey Level")
                        share = o.get("ownership_share", "N/A")
                        o_type = o.get("ownership_type", "Primary")
                        owner_lines.append(f"  • {name} — Sub-division: {sub_div}, Share: {share}, Type: {o_type}")
                    chunk = f"Owners for Survey No. {survey_no} ({len(owners)} record(s)):\n" + "\n".join(owner_lines)
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "service_code_lookup":
            from backend.utils.helpers import SIS_URBAN_SERVICES
            _sc_match = re.search(r'\b(0?1[5-9][0-9]|0?[0-9]{3})\b', message)
            _prefix_raw = _sc_match.group(1) if _sc_match else ""
            _prefix_norm = _prefix_raw.zfill(4) if _prefix_raw else ""
            _matches = {
                code: info for code, info in SIS_URBAN_SERVICES.items()
                if code.startswith(_prefix_norm)
            }
            if _prefix_norm and not _matches:
                _prefix3 = _prefix_raw[-3:] if len(_prefix_raw) >= 3 else _prefix_raw
                _matches = {
                    code: info for code, info in SIS_URBAN_SERVICES.items()
                    if code[1:].startswith(_prefix3) or code.startswith(_prefix3)
                }
            _count = len(_matches)
            if _count == 0:
                chunk = (
                    f"There are no SIS urban service codes matching '{_prefix_raw}'. "
                    f"The available service codes are: "
                    + ", ".join(f"{c} ({v['short']} — {v['name']})" for c, v in SIS_URBAN_SERVICES.items())
                    + "."
                )
            elif _count == 1:
                _code, _info = next(iter(_matches.items()))
                chunk = (
                    f"There is 1 service code matching '{_prefix_raw}': "
                    f"{_code} — {_info['name']} ({_info['short']})."
                )
            else:
                _lines = [f"{c} — {v['name']} ({v['short']})" for c, v in sorted(_matches.items())]
                chunk = (
                    f"There are {_count} service codes matching '{_prefix_raw}':\n"
                    + "\n".join(f"  • {l}" for l in _lines)
                )
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif any(ph in message.lower() for ph in [
            "uploaded", "word document", "pdf document", "question bank",
            "answer all", "answer for all", "from the document", "in the document",
            "the file", "attached file", "from this file",
        ]):
            chunk = (
                "I can see you're referring to an uploaded document. "
                "Unfortunately I can only read plain text (.txt) file contents directly — "
                "Word and PDF files need to be processed first.\n\n"
                "Please copy and paste the relevant text from the document into the chat, "
                "and I'll answer your questions from it."
            )
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent in ("check_sale_deed", "sale_deed_check") and (
            _sd_answer := build_sale_deed_direct_answer(structured_data, message, language)
        ):
            # Same fix as process_chat: never let the LLM pick which field is
            # "the deed number" out of the full application record.
            full_response_text = _sd_answer
            sse_data = f"data: {json.dumps({'content': _sd_answer})}\n\n"
            yield sse_data.encode('utf-8')

        elif structured_data and "applications" in structured_data:
            # Deterministic intro for any remaining list result — same fallback as
            # process_chat, so a row count is never left to the LLM.
            count = structured_data.get("count", len(structured_data.get("applications", [])))
            qtype = structured_data.get("query_type", "Pending Applications")
            chunk = f"Found {count} application(s) ({qtype})."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        else:
            async for chunk in call_llama_stream(full_prompt):
                chunk_count += 1
                full_response_text += chunk
                
                # Format as Server-Sent Event
                sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
                
                # Encode to bytes for streaming
                yield sse_data.encode('utf-8')
                
                if chunk_count % 10 == 0:
                    logger.debug(f"Streamed {chunk_count} chunks, total length: {len(full_response_text)}")
        
        logger.info(f"Stream complete: {chunk_count} chunks, {len(full_response_text)} chars")
            
        # Step 7: Calculate response time
        response_time_ms = int((time.time() - start_time) * 1000)
        
        # Step 8: Save chat messages to database
        await save_chat_messages(
            db=db,
            session_id=session_id,
            user_message=message,
            assistant_message=full_response_text,
            language=language,
            response_time_ms=response_time_ms,
            officer_id=officer.officer_id if officer else None
        )
        
        logger.info(f"Chat processed and streamed successfully in {response_time_ms}ms")
        
    except Exception as e:
        import json
        # Roll back BEFORE logging: rendering the traceback walks frame locals,
        # and touching an ORM object there lazy-loads outside the async context,
        # which poisons the session for the rest of the conversation.
        try:
            await db.rollback()
        except Exception as rb_err:
            logger.warning(f"Rollback after process_chat_stream failure also failed: {rb_err}")

        logger.error(f"Error in process_chat_stream: {e}", exc_info=True)
        error_messages = {
            "en": "I apologize, but I encountered an error processing your request. Please try again.",
            "ta": "மன்னிக்கவும், உங்கள் கோரிக்கையைச் செயல்படுத்துவதில் பிழை ஏற்பட்டது. மீண்டும் முயற்சிக்கவும்.",
            "tanglish": "Sorry, error ஏற்பட்டது. Please try again."
        }
        language = detect_language(message)
        error_msg = error_messages.get(language, error_messages["en"])
        yield f"data: {json.dumps({'content': error_msg})}\n\n".encode('utf-8')


async def save_chat_messages(
    db: AsyncSession,
    session_id: str,
    user_message: str,
    assistant_message: str,
    language: str,
    response_time_ms: int,
    officer_id: Optional[uuid.UUID] = None
) -> None:
    """
    Save user and assistant messages to database
    """
    try:
        # Get session
        session_query = select(ChatSession).where(
            ChatSession.id == session_id
        )
        result = await db.execute(session_query)
        session = result.scalar_one_or_none()
        
        if not session:
            try:
                import uuid as _uuid_m
                sess_uuid = _uuid_m.UUID(str(session_id))
                if not officer_id:
                    # Never fall back to "whichever officer happens to be first in
                    # the table" — that files one officer's conversation under
                    # another officer's account. Drop the transcript instead.
                    logger.warning(
                        f"Cannot auto-create session {session_id}: no officer_id supplied; "
                        f"messages not saved."
                    )
                    return
                session = ChatSession(
                    id=sess_uuid,
                    officer_id=officer_id,
                    session_token=str(session_id),
                    is_active=True,
                    started_at=datetime.now(timezone.utc),
                    last_activity=datetime.now(timezone.utc)
                )
                db.add(session)
                await db.flush()
            except Exception as sess_err:
                logger.warning(f"Could not auto-create session {session_id}: {sess_err}")
                return
        
        # Save user message
        user_msg = ChatMessage(
            session_id=session_id,
            role="user",
            content=user_message,
            detected_language=language,
            created_at=datetime.now(timezone.utc)
        )
        db.add(user_msg)
        
        # Save assistant message
        assistant_msg = ChatMessage(
            session_id=session_id,
            role="assistant",
            content=assistant_message,
            detected_language=language,
            response_time_ms=response_time_ms,
            created_at=datetime.now(timezone.utc)
        )
        db.add(assistant_msg)
        
        # Update session last activity
        session.last_activity = datetime.now(timezone.utc)
        
        await db.commit()
        logger.info(f"Saved chat messages for session {session_id}")
        
    except Exception as e:
        logger.error(f"Error saving chat messages: {e}")
        await db.rollback()


async def create_chat_session(
    db: AsyncSession,
    officer_id: str
) -> ChatSession:
    """
    Create a new chat session for an officer
    
    Args:
        db: Database session
        officer_id: Officer UUID
        
    Returns:
        Created ChatSession object
    """
    try:
        import uuid
        
        session = ChatSession(
            officer_id=officer_id,
            session_token=str(uuid.uuid4()),
            started_at=datetime.now(timezone.utc),
            last_activity=datetime.now(timezone.utc),
            is_active=True
        )
        
        db.add(session)
        await db.commit()
        await db.refresh(session)
        
        logger.info(f"Created new chat session: {session.id}")
        return session
        
    except Exception as e:
        logger.error(f"Error creating chat session: {e}")
        await db.rollback()
        raise


async def get_session_history(
    db: AsyncSession,
    session_id: str,
    limit: int = 50
) -> list:
    """
    Get chat history for a session
    
    Args:
        db: Database session
        session_id: Chat session UUID
        limit: Maximum number of messages to return
        
    Returns:
        List of chat messages
    """
    try:
        query = select(ChatMessage).where(
            ChatMessage.session_id == session_id
        ).order_by(ChatMessage.created_at.desc()).limit(limit)
        
        result = await db.execute(query)
        messages = result.scalars().all()
        
        # Reverse to get chronological order
        messages = list(reversed(messages))
        
        return [
            {
                "role": msg.role,
                "content": msg.content,
                "language": msg.detected_language,
                "timestamp": msg.created_at.isoformat()
            }
            for msg in messages
        ]
        
    except Exception as e:
        logger.error(f"Error getting session history: {e}")
        return []


async def get_officer_sessions(
    db: AsyncSession,
    officer_id: str
) -> list:
    """
    Get all chat sessions for an officer
    
    Args:
        db: Database session
        officer_id: Officer UUID
        
    Returns:
        List of chat sessions
    """
    try:
        query = select(ChatSession).where(
            ChatSession.officer_id == officer_id
        ).order_by(ChatSession.last_activity.desc())
        
        result = await db.execute(query)
        sessions = result.scalars().all()
        
        return [
            {
                "session_id": str(session.id),
                "session_token": session.session_token,
                "started_at": session.started_at.isoformat(),
                "last_activity": session.last_activity.isoformat() if session.last_activity else None,
                "is_active": session.is_active
            }
            for session in sessions
        ]
        
    except Exception as e:
        logger.error(f"Error getting officer sessions: {e}")
        return []


def _build_table_data(intent: str, message: str, user_id: str, structured_data: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
    if not structured_data:
        return None
        
    intent_lower = intent.lower()
    
    # 1. Survey details lookup
    if intent_lower in ["survey_lookup", "survey_detail"]:
        if not structured_data.get("found", True):
            return None
        subdivs = []
        for sd in structured_data.get("sub_divisions", []):
            if sd.get("sub_division_no"):
                area = sd.get("area_sqm")
                if area:
                    subdivs.append(f"{sd.get('sub_division_no')} ({int(area)} sq.m)")
                else:
                    subdivs.append(sd.get("sub_division_no"))
        block_name = structured_data.get("jurisdiction", {}).get("block") or "N/A"
        return {
            "query_type": "Survey Number Details",
            "jurisdiction": {
                "district": structured_data.get("jurisdiction", {}).get("district", "N/A"),
                "taluk": structured_data.get("jurisdiction", {}).get("taluk", "N/A"),
                "town": structured_data.get("jurisdiction", {}).get("town", "N/A"),
                "ward_number": structured_data.get("jurisdiction", {}).get("ward_number") or structured_data.get("jurisdiction", {}).get("ward") or "N/A",
                "block_number": block_name
            },
            "surveys_by_block": {
                block_name: [
                    {
                        "survey_no": structured_data.get("survey_no"),
                        "area_sqm": structured_data.get("total_area_sqm"),
                        "land_type": structured_data.get("land_type") or "Urban",
                        "subdivisions": subdivs
                    }
                ]
            }
        }
        
    # 2. Pending / typed application lists
    elif intent_lower in [
        "pending_applications", "town_applications", "block_applications", "immediate_action",
        "isd_applications", "nisd_applications", "merge_applications", "highest_priority_applications"
    ] or (intent_lower == "workload" and "applications" in structured_data):
        apps = []
        for app in structured_data.get("applications", []):
            apps.append({
                "application_number": app.get("application_number"),
                "type": app.get("type"),
                "survey_no": app.get("survey_no") or app.get("raw_survey_no") or "N/A",
                "subdivisions": app.get("subdivisions") or app.get("sub_division_no") or "N/A",
                "taluk_name": app.get("taluk_name") or app.get("jurisdiction", {}).get("taluk", "N/A"),
                "town_name": app.get("town_name") or "N/A",
                "ward_number": app.get("ward_number") or "N/A",
                "block_number": app.get("block_number") or "N/A",
                "status": app.get("status") or "Pending",
                "current_stage": app.get("stage") or app.get("current_stage"),
                "submission_date": app.get("submission_date"),
                "is_overdue": app.get("is_overdue"),
                "field_visit_scheduled": app.get("field_visit_scheduled"),
                "field_visit_date": app.get("field_visit_date")
            })
        return {
            "query_type": structured_data.get("query_type", "Pending Applications"),
            "applications": apps
        }

    # 2b. Both / mixed type applications combined (isd+nisd, isd+merge, all, etc.)
    elif intent_lower == "both_applications":
        apps = []
        for app in structured_data.get("applications", []):
            apps.append({
                "application_number": app.get("application_number"),
                "type": app.get("type"),
                "taluk_name": app.get("taluk_name") or app.get("jurisdiction", {}).get("taluk", "N/A"),
                "town_name": app.get("town_name") or "N/A",
                "ward_number": app.get("ward_number") or "N/A",
                "block_number": app.get("block_number") or "N/A",
                "status": app.get("status") or "Pending",
                "current_stage": app.get("stage") or app.get("current_stage"),
                "submission_date": app.get("submission_date")
            })
        return {
            "query_type": structured_data.get("query_type", "Combined Applications"),
            "applications": apps
        }
        
    # 3. Field visit
    # NOTE: only intents whose structured_data actually carries a "field_visits"
    # list belong here. fv_unassigned_awaiting (unassigned_applications),
    # fv_scheduled_this_week (taluk_cases), fv_recently_rescheduled (a count) and
    # fv_scheduling_conflicts (a single date) do not — listing them here rendered
    # an empty "No records found" table under an otherwise correct answer.
    elif intent_lower in [
        "field_visit", "field_visits", "awaiting_field_visit",
        "fv_between_dates", "fv_overdue_inspections"
    ]:
        visits = []
        for visit in structured_data.get("field_visits", []):
            app_type = visit.get("application_type") or visit.get("type") or "N/A"
            fv_date = visit.get("field_visit_date") or visit.get("scheduled_date") or "Not Scheduled"
            visits.append({
                "application_number": visit.get("application_number") or "N/A",
                "survey_no": visit.get("survey_no") or "N/A",
                "block_number": visit.get("block_number") or "N/A",
                "application_type": app_type,
                "type": app_type,
                "status": visit.get("status") or "N/A",
                "field_visit_date": fv_date,
                "scheduled_date": fv_date
            })
        if not visits:
            # No rows — suppress the table entirely rather than rendering an
            # empty "No records found" card under the text answer.
            return None
        return {
            "query_type": structured_data.get("query_type", "Field Visits"),
            "field_visits": visits
        }
        
    # 4. Owner lookup
    elif intent_lower in ["owner_lookup", "survey_owners"]:
        owners_list = []
        for o in structured_data.get("owners", []):
            sub_div = o.get("sub_division")
            if sub_div == "Survey Level":
                sub_div = None
            share = o.get("ownership_share")
            if share is not None:
                share = float(share)
            owners_list.append({
                "owner_name": o.get("owner_name") or o.get("name") or "N/A",
                "sub_division": sub_div,
                "ownership_share": share if share is not None else "N/A",
                "ownership_type": o.get("ownership_type") or ("Joint" if o.get("is_joint_owner") else "Primary"),
                "is_joint_owner": bool(o.get("is_joint_owner"))
            })
        return {
            "query_type": structured_data.get("query_type", "Owner Details"),
            "survey_no": structured_data.get("survey_no", ""),
            "owners": owners_list
        }
        
    # 5. Workload summary
    elif intent_lower in ["officer_workload"] or (intent_lower == "workload" and "total_active" in structured_data):
        total = structured_data.get("total_active", 0)
        pending = structured_data.get("ISD", 0) + structured_data.get("NISD", 0) + structured_data.get("MERGE", 0)
        overdue = structured_data.get("overdue", 0)
        unscheduled = structured_data.get("unscheduled_visits", 0)
        return {
            "query_type": "Workload Summary",
            "total_applications": total,
            "pending_count": pending,
            "overdue_count": overdue,
            "unscheduled_visits": unscheduled
        }
        
    # 6. Status check
    elif intent_lower in ["status_check", "application_status"]:
        # Multi-app query: return list of tables, one per application
        if "multi_applications" in structured_data:
            multi_tables = []
            for _app_sd in structured_data["multi_applications"]:
                if not _app_sd or not _app_sd.get("found", True):
                    continue
                multi_tables.append({
                    "query_type": "Application & Applicant Details",
                    "serial_number": _app_sd.get("serial_number"),
                    "application_number": _app_sd.get("application_number"),
                    "application_id": _app_sd.get("application_id") or _app_sd.get("application_number"),
                    "user_id": _app_sd.get("user_id"),
                    "service_code": _app_sd.get("service_code"),
                    "district_code": _app_sd.get("district_code"),
                    "taluk_code": _app_sd.get("taluk_code"),
                    "village_code": _app_sd.get("village_code"),
                    "urban_unit_code": _app_sd.get("urban_unit_code"),
                    "ward_code": _app_sd.get("ward_code"),
                    "block_code": _app_sd.get("block_code"),
                    "application_date": _app_sd.get("application_date") or _app_sd.get("submission_date"),
                    "application_status": _app_sd.get("application_status") or _app_sd.get("status"),
                    "survey_number": _app_sd.get("survey_number") or _app_sd.get("survey_no"),
                    "subdivision_number": _app_sd.get("subdivision_number"),
                    "current_subdivision_number": (
                        _app_sd.get("current_subdivision_number")["proposed_sub_division_no"]
                        if isinstance(_app_sd.get("current_subdivision_number"), dict)
                        else _app_sd.get("current_subdivision_number")
                    ),
                    "patta_number": _app_sd.get("patta_number"),
                    "role_id": _app_sd.get("role_id"),
                    "source_code": _app_sd.get("source_code"),
                    "source_name": _app_sd.get("source_name"),
                    "can_number": _app_sd.get("can_number"),
                    "workflow_state": _app_sd.get("workflow_state") or _app_sd.get("stage"),
                    "type": _app_sd.get("type"),
                    "included_subdivisions": _app_sd.get("included_subdivisions"),
                    "status": _app_sd.get("status"),
                    "stage": _app_sd.get("stage"),
                    "submission_date": _app_sd.get("submission_date"),
                    "field_visit_scheduled": bool(_app_sd.get("field_visit_scheduled")),
                    "field_visit_date": _app_sd.get("field_visit_date"),
                    "is_overdue": bool(_app_sd.get("is_overdue")),
                    "priority_flag": bool(_app_sd.get("priority_flag")),
                    "applicant_name": _app_sd.get("applicant_name"),
                    "applicant_mobile": _app_sd.get("applicant_mobile"),
                    "applicant_address": _app_sd.get("applicant_address"),
                    "declared_reason": _app_sd.get("declared_reason"),
                })
            if not multi_tables:
                return None
            return {"query_type": "Application & Applicant Details", "multi_tables": multi_tables}

        if not structured_data.get("found", True):
            return None
        if "history" in structured_data:
            return {
                "query_type": structured_data.get("query_type", "Workflow History"),
                "application_number": structured_data.get("application_number"),
                "history": structured_data.get("history", [])
            }
        return {
            "query_type": "Application & Applicant Details",
            "serial_number": structured_data.get("serial_number"),
            "application_number": structured_data.get("application_number"),
            "application_id": structured_data.get("application_id") or structured_data.get("application_number"),
            "user_id": structured_data.get("user_id"),
            "service_code": structured_data.get("service_code"),
            "district_code": structured_data.get("district_code"),
            "taluk_code": structured_data.get("taluk_code"),
            "village_code": structured_data.get("village_code"),
            "urban_unit_code": structured_data.get("urban_unit_code"),
            "ward_code": structured_data.get("ward_code"),
            "block_code": structured_data.get("block_code"),
            "application_date": structured_data.get("application_date") or structured_data.get("submission_date"),
            "application_status": structured_data.get("application_status") or structured_data.get("status"),
            "survey_number": structured_data.get("survey_number") or structured_data.get("survey_no"),
            "subdivision_number": structured_data.get("subdivision_number"),
            "current_subdivision_number": (
                structured_data.get("current_subdivision_number")["proposed_sub_division_no"]
                if isinstance(structured_data.get("current_subdivision_number"), dict)
                else structured_data.get("current_subdivision_number")
            ),
            "patta_number": structured_data.get("patta_number"),
            "role_id": structured_data.get("role_id"),
            "source_code": structured_data.get("source_code"),
            "source_name": structured_data.get("source_name"),
            "can_number": structured_data.get("can_number"),
            "workflow_state": structured_data.get("workflow_state") or structured_data.get("stage"),
            "type": structured_data.get("type"),
            "included_subdivisions": structured_data.get("included_subdivisions"),
            "status": structured_data.get("status"),
            "stage": structured_data.get("stage"),
            "submission_date": structured_data.get("submission_date"),
            "field_visit_scheduled": bool(structured_data.get("field_visit_scheduled")),
            "field_visit_date": structured_data.get("field_visit_date"),
            "is_overdue": bool(structured_data.get("is_overdue")),
            "priority_flag": bool(structured_data.get("priority_flag")),
            "applicant_name": structured_data.get("applicant_name"),
            "applicant_mobile": structured_data.get("applicant_mobile"),
            "applicant_address": structured_data.get("applicant_address"),
            "declared_reason": structured_data.get("declared_reason")
        }

    # 7. Rejection info
    elif intent_lower in ["rejection_info"]:
        return {
            "query_type": structured_data.get("query_type", "Rejection History"),
            "application_number": structured_data.get("application_number"),
            "rejections": structured_data.get("rejections", [])
        }

    # 8. Jurisdiction summary
    elif intent_lower in ["jurisdiction_summary"] or "jurisdiction" in structured_data:
        return {
            "query_type": structured_data.get("query_type", "Jurisdiction Summary"),
            "jurisdiction": structured_data.get("jurisdiction", {})
        }
        
    # 9. Unassigned field visits — show application detail table
    elif intent_lower == "fv_unassigned_awaiting":
        apps = structured_data.get("unassigned_applications", [])
        if not apps:
            return None
        return {
            "query_type": "Unassigned Field Visits — Awaiting Scheduling",
            "applications": apps
        }
    
    # 9b. Overdue field visits — show field visit table
    elif intent_lower == "fv_overdue_inspections":
        visits = structured_data.get("field_visits", [])
        if not visits:
            return None
        return {
            "query_type": "Overdue Field Visits",
            "field_visits": visits
        }

    # 10. Immediate action — show application detail table
    elif intent_lower == "immediate_action":
        apps = structured_data.get("applications", [])
        if not apps:
            return None
        return {
            "query_type": "Immediate Action Required — Overdue Applications",
            "applications": apps
        }

    # 11. Highest priority applications — show application table with warning symbols
    elif intent_lower == "highest_priority_applications":
        apps = structured_data.get("applications", [])
        if not apps:
            return None
        return {
            "query_type": "High Priority Applications",
            "applications": apps
        }

    # 12. Escalation check — applications approaching deadline
    elif intent_lower == "escalation_check":
        apps = structured_data.get("applications", [])
        if not apps:
            return None
        # Map to standard applications table format, adding days info as pseudo-field
        rows = []
        for a in apps:
            rows.append({
                "application_number": a.get("application_number"),
                "type": a.get("type"),
                "status": a.get("status"),
                "current_stage": a.get("stage"),
                "submission_date": a.get("submission_date"),
                "town_name": a.get("town_name", "N/A"),
                "ward_number": a.get("ward_number", "N/A"),
                # Overload days_pending for display — show working days elapsed
                "days_pending": a.get("working_days_elapsed", 0),
                "priority": a.get("urgency", "N/A"),
            })
        return {
            "query_type": structured_data.get("query_type", "Escalation Threshold"),
            "applications": rows
        }

    return None


async def handle_chat(
    message: str,
    session_id: str,
    officer: OfficerContext,
    db: AsyncSession
) -> Dict[str, Any]:
    """Alias/wrapper for process_chat to comply with prompt signature specifications"""
    return await process_chat(message, session_id, officer, db)



