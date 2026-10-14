"""
Anti-hallucination answer verification/scrubbing, count/result-limit formatting, reformat-request handling, scoped-list-answer rendering, and the status/stage yes-no renderers.

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
from .chat_smalltalk import _wfh_fmt_dt
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
from .chat_shared import (
    _CHANNEL_LABELS,
    _LIFECYCLE_STATUS_RE,
    _LIMITABLE_LIST_INTENTS,
    _OWN_SUBJECT_RE,
    _asked_registration_detail,
    _asks_transfer_party,
    _carried_list_channel,
    _carried_scope_label,
    _contentless_message,
)
from .chat_smalltalk import _last_assistant_message
from backend.utils.logger import get_logger

logger = get_logger(__name__)



# Application-type detection patterns.
# Service codes are only meaningful as standalone digit runs — the (?<!\d)/(?!\d)
# guards stop a serial number like "000153" from being read as the NISD code.
# The official service names ("Not Involving Subdivision" / "Involving
# Subdivision", from tamilnilam_urban_services_and_districts.txt) are also
# recognised, not just the ISD/NISD abbreviation or the numeric code: an
# officer asking "show involving subdivision applications" or "not involving
# subdivision applications" by the full name got neither -- both silently
# matched no type at all and fell back to the unscoped pending queue, the same
# "an answer that reads as complete but answered a different question"
# failure this file documents elsewhere. The negative lookbehind on the ISD
# pattern is required: "not involving subdivision" contains "involving
# subdivision" as a literal substring, so without it every NISD-by-name
# question would ALSO match ISD.
_SERVICE_CODE_NISD_RE = re.compile(
    r'\bnisd\b|(?<!\d)0153(?!\d)|\bnot\s+involving\s+sub[\s-]?division\b')
_SERVICE_CODE_ISD_RE = re.compile(
    r'\bisd\b|(?<!\d)0154(?!\d)|(?<!not\s)\binvolving\s+sub[\s-]?division\b')
_SORT_RESET = contextvars.ContextVar("sort_reset", default=False)


# "display application without CAN number" -- a CAN is assigned to every
# application when it is filed (verified: 0 of 209 applications carry a
# NULL or empty can_number; CAN_LENGTHS is enforced at projection time), so
# this question always has the same answer. Unrecognised, "without CAN
# number" fell to the column-PROJECTION machinery, which has no concept of
# negation -- it read "CAN number" as a requested COLUMN and silently
# dropped the word "without", returning a table of applications that all
# had a CAN number, the opposite of what was asked.
_MISSING_CAN_RE = re.compile(
    # "number"/"no" is required after "can" everywhere (not optional) -- a
    # bare "without ... can" also matches the modal verb ("without it, can
    # we proceed?"), which "without CAN number" cannot be confused with.
    r"\bwithout\s+(?:a\s+|the\s+|their\s+)?can\s*(?:number|no\.?)\b"
    r"|\b(?:no|missing|lacking)\s+can\s*(?:number|no\.?)\b"
    r"|\bcan\s*(?:number|no\.?)\s+(?:is\s+)?missing\b"
    r"|\b(?:don'?t|doesn'?t)\s+have\s+(?:a\s+)?can\s*(?:number|no\.?)\b"
    r"|CAN\s+இல்லாத|CAN\s+எண்\s+இல்லாத",
    re.IGNORECASE,
)


def _asks_missing_can_number(message: str) -> bool:
    return bool(_MISSING_CAN_RE.search(message or ""))


def _missing_can_number_answer(language: str) -> str:
    is_tamil = language in ("ta", "tanglish")
    if is_tamil:
        return ("எதுவும் இல்லை — பதிவேட்டில் உள்ள ஒவ்வொரு விண்ணப்பமும் CAN எண்ணைக் "
                "கொண்டுள்ளது. விண்ணப்பம் தாக்கல் செய்யப்படும்போதே அது வழங்கப்படுகிறது, "
                "எந்த விண்ணப்பத்திலும் இது இல்லாமல் இருக்காது.")
    return ("None — every application in the register carries a CAN number. "
            "It is assigned when the application is filed, so it is never "
            "absent, whichever channel the application came through.")


# "unscheduled" is asked for in more shapes than the word itself. The exact
# substring missed "unschedule", "un-scheduled", "not yet scheduled" and
# "yet to be scheduled" -- and because every one of those still contains
# "schedule", they fell through to the scheduled branch and were answered
# "No field visits scheduled" while the officer held unscheduled visits.
# Matched on the stem, and on the negated forms, ahead of that branch.
_UNSCHEDULED_RE = re.compile(
    r"(?:\bun[\s_-]?schedul)"
    r"|(?:\bnot\s+(?:yet\s+)?(?:been\s+)?schedul)"
    r"|(?:\byet\s+to\s+(?:be\s+)?schedul)"
    r"|(?:\bwithout\s+(?:a\s+)?schedul)"
    r"|(?:\bno\s+(?:visit\s+)?(?:date|schedul))"
)
_UNSCHEDULED_TA = ("திட்டமிடப்படாத", "திட்டமிடவில்லை", "ஒதுக்கப்படாத")


def _asked_unscheduled(message_lower: str, exclude_range: bool = False) -> bool:
    """True when the officer is asking for visits with no date booked yet."""
    if any(w in message_lower for w in _UNSCHEDULED_TA):
        return True
    m = _UNSCHEDULED_RE.search(message_lower)
    if not m:
        return False
    # "field visits not scheduled between X and Y" is a date exclusion, not a
    # status question -- the existing carve-out, kept.
    if exclude_range and m.group(0).startswith("not"):
        return False
    return True


# ── Completed vs still-to-be-made field visits ──────────────────────────────
# "completed" used to be matched as a bare substring, and "unCOMPLETEd" /
# "inCOMPLETE" / "not COMPLETED" all contain it. A request for the visits still
# outstanding was therefore answered with the ones already finished -- the exact
# opposite set, stated with full confidence. Same trap CLAUDE.md records for
# "how" inside "sHOW", so both cues are token-bounded and the NEGATIVE one is
# tested first: every open-visit phrasing contains a completed-visit cue, and
# none of the completed phrasings contains a negation.
_OPEN_VISIT_RE = re.compile(
    # "unvisited" is the word an officer actually reaches for, and leaving it
    # out sent the question to the unfiltered summary, which answered with
    # every visit -- finished ones included. Note "still to visit": that is the
    # wording the summary line itself uses ("3 field visit(s) — 2 completed,
    # 1 still to visit"), so the assistant has to understand the words it chose
    # itself. Both spellings of the participle are accepted throughout, since
    # "yet to visit" and "yet to be visited" are the same request.
    r"\bun[\s-]?(?:visited|completed|finished)\b"
    r"|\bincomplete(?:d)?\b"
    r"|\bnever\s+(?:visited|completed|finished|done)\b"
    r"|\bnot\s+(?:yet\s+)?(?:visited|visit|completed|complete|finished|done)\b"
    r"|\byet\s+to\s+(?:be\s+)?(?:visit|visited|complete|completed|finish|finished|do|done)\b"
    r"|\bstill\s+to\s+(?:be\s+)?(?:visit|visited|done|completed)\b"
    r"|\bto\s+be\s+visited\b"
    r"|\bstill\s+(?:pending|open|outstanding)\b"
    r"|\boutstanding\b|\bremaining\b|\bremain\b|\bpending\b|\bopen\b"
    r"|முடிக்கப்படாத|முடியாத|பார்வையிடப்படாத|நிலுவையில்",
    re.IGNORECASE,
)
_COMPLETED_VISIT_RE = re.compile(
    r"\bcompleted\b|\bcomplete\b|\bfinished\b|\bdone\b|\bover\b|\bclosed\b"
    r"|முடிந்த|முடிக்கப்பட்ட",
    re.IGNORECASE,
)


def _asked_open_visits(message_lower: str) -> bool:
    """True when the officer wants the visits still to be made."""
    return bool(_OPEN_VISIT_RE.search(message_lower or ""))


def _asked_completed_visits(message_lower: str) -> bool:
    """True when the officer wants the visits already finished.

    A negation wins outright: "not completed" names the completed state only to
    exclude it.
    """
    text = message_lower or ""
    if _asked_open_visits(text):
        return False
    return bool(_COMPLETED_VISIT_RE.search(text))


def _extract_app_types(message_lower: str, intent: str = None):
    """
    Extract one or more application types from a query message.

    Returns:
        - A list  ["ISD", "NISD"] when both types are mentioned
        - A single string "ISD" / "NISD" when exactly one type
        - None when no specific type mentioned (all types)
    """
    # Negated type extraction removed to avoid assuming a closed-world schema
    # where "not X" implies the remaining limited set of types.

    types = []
    # \bisd\b matches "isd" but NOT "nisd" (no word-boundary before i in nisd).
    # Service codes must be standalone digit runs: "2026/0153/02/000002" counts,
    # but the serial "000153" inside 2026/0154/02/000153 must NOT read as NISD.
    has_nisd  = bool(_SERVICE_CODE_NISD_RE.search(message_lower))
    has_isd   = bool(_SERVICE_CODE_ISD_RE.search(message_lower))

    if has_nisd:
        types.append("NISD")
    if has_isd:
        types.append("ISD")

    if len(types) == 0:
        if intent == "both_applications":
            return ["ISD", "NISD"]
        elif intent == "isd_applications":
            return "ISD"
        elif intent == "nisd_applications":
            return "NISD"
        return None
    if len(types) == 1:
        return types[0]
    return types  # list for multi-type IN query



# How a submission channel is named in a list label. The stored values are
# terse codes; "All Applications" gave the officer no sign that a channel
# filter had been applied at all.
_CHANNEL_LABELS = {
    "CSC": "CSC",
    "citizen": "Citizen Portal",
    "sub_registrar": "Sub-Registrar",
}

# How each channel is worded inside a sentence, rather than as a label.
_CHANNEL_PHRASES = {
    "CSC": ("from CSC", "CSC வழியாக"),
    "sub_registrar": ("Sub-Registrar referrals", "சார்-பதிவாளர் பரிந்துரைகள்"),
    "citizen": ("filed by the citizen", "குடிமகனால் தாக்கல் செய்யப்பட்டவை"),
}


def _channel_scope_label(channels) -> str:
    """The channel adjective for a list label -- " CSC", " CSC & Sub-Registrar".

    Empty when no channel was named, and empty again when EVERY channel was:
    "applications from all channels" narrows nothing, so calling the list
    "All-Channel Applications" would suggest a filter that is not there, and an
    empty result really would mean an empty jurisdiction. What the scope does
    buy in that case is the suppression of the current-stage pin, which is in
    get_officer_applications, not in the wording.
    """
    chans = list(channels or []) if isinstance(channels, (list, tuple, set)) else [channels]
    chans = [c for c in chans if c]
    if not chans or len(set(chans)) >= len(_CHANNEL_LABELS):
        return ""
    return " " + " & ".join(_CHANNEL_LABELS.get(c, str(c)) for c in chans)


def _channel_breakdown_note(channels, rows: list, is_tamil: bool,
                            rejected_counts: Optional[dict] = None) -> Optional[str]:
    """How many of the listed applications came through each channel asked for.

    A combined list is unreadable without it: "34 applications" over a CSC +
    Sub-Registrar request says nothing about the split, and a channel the
    officer named that returned NOTHING has to be said out loud -- otherwise a
    list of 34 CSC files silently answers a question that also asked for the
    Sub-Registrar ones.

    The figures are counted off the rows the query returned, so they are as
    verified as the total beside them.
    """
    chans = [c for c in (list(channels or []) if isinstance(channels, (list, tuple, set))
                         else [channels]) if c]
    if len(set(chans)) < 2:
        return None
    counts = {c: 0 for c in chans}
    for row in rows or []:
        ch = row.get("submission_channel")
        if ch in counts:
            counts[ch] += 1
    total = sum(counts.values())
    idx = 1 if is_tamil else 0
    rejected = rejected_counts or {}
    bits, notes = [], []
    for c in dict.fromkeys(chans):
        phrase = _CHANNEL_PHRASES.get(c, (c, c))[idx]
        if counts[c]:
            bits.append(f"{counts[c]} {phrase}")
            continue
        bits.append(f"{phrase} எதுவும் இல்லை" if is_tamil else f"none {phrase}")
        # A channel that returned nothing because its only files were
        # REJECTED is not a channel with nothing in it. Rejected applications
        # stay out of lists unless asked for, and saying so is the difference
        # between a filtered list and a false one.
        n = rejected.get(c) or 0
        if n:
            label = _CHANNEL_LABELS.get(c, str(c))
            notes.append(
                f"{label}: {n} நிராகரிக்கப்பட்டது" if is_tamil
                else f"{n} {label} application{'s' if n != 1 else ''} "
                     f"{'were' if n != 1 else 'was'} rejected")
    if is_tamil:
        text = f"மொத்தம் {total} — " + ", ".join(bits) + "."
        if notes:
            text += (" (" + "; ".join(notes)
                     + " — நிராகரிக்கப்பட்டவை கேட்கும் வரை பட்டியலில் வராது.)")
        return text
    text = f"{total} in total — " + ", ".join(bits) + "."
    if notes:
        text += (" (" + "; ".join(notes)
                 + " — rejected files are left out of lists unless you ask for them.)")
    return text


_STATUS_BREAKDOWN_EN = {"approved": "approved (completed)", "pending": "pending",
                        "in_progress": "in progress", "rejected": "rejected",
                        "escalated": "escalated"}
_STATUS_BREAKDOWN_TA = {"approved": "அங்கீகரிக்கப்பட்டவை", "pending": "நிலுவையில்",
                        "in_progress": "செயலில்", "rejected": "நிராகரிக்கப்பட்டவை",
                        "escalated": "மேல்முறையீட்டில்"}


def _format_count_intro(structured_data: dict, language: str, message: str) -> str:
    """The count sentence, plus a per-status split when the question named several
    statuses ("NISD pending and completed counts"): one merged total answers
    neither of the two counts that were asked for."""
    text = _format_count_intro_raw(structured_data, language, message)
    rows = (structured_data or {}).get("applications") if isinstance(structured_data, dict) else None
    label = str((structured_data or {}).get("query_type") or "") if isinstance(structured_data, dict) else ""
    _low = label.lower()
    _type_only = (bool(re.search(r"\b(?:nisd|isd)\b", _low))
                  and not re.search(r"approved|rejected|pending|progress|escalated|overdue", _low))
    if not rows or ("&" not in label and not _type_only):
        return text
    from collections import Counter
    counts = Counter((r.get("status") or "").lower() for r in rows if isinstance(r, dict))
    # The statuses the question named, in the order named -- a status with none is
    # stated as 0, since "0 pending" is half of what "pending and completed" asks.
    _low_label = label.lower()
    named = [s for s in ("approved", "pending", "in_progress", "rejected", "escalated")
             if s.replace("_", " ") in _low_label]
    order = named if len(named) >= 2 else [s for s, _n in counts.most_common()]
    if _type_only and "&" not in label:
        order = [s for s in ("approved", "pending", "in_progress", "escalated", "rejected") if counts.get(s)]
    if len(order) < 2:
        return text
    ta = language in ("ta", "tanglish")
    words = _STATUS_BREAKDOWN_TA if ta else _STATUS_BREAKDOWN_EN
    parts = ", ".join(f"{counts.get(s, 0)} {words.get(s, s)}" for s in order)
    return text.rstrip(".") + f" — {parts}."


def _format_count_intro_raw(structured_data: dict, language: str, message: str) -> str:
    """
    Format clean, natural English and Tamil intro sentence when the user asks for application counts.
    """
    if not structured_data:
        return ""
    count = structured_data.get("count", len(structured_data.get("applications", [])))
    qtype_raw = structured_data.get("query_type", "applications")
    is_tamil = language in ("ta", "tanglish")

    # A zero-count query_type the caller already explained -- `empty_note`,
    # set by get_overdue_applications() (why "overdue" is 0: the 15-working-
    # day deadline, not the same thing as a visit being past its scheduled
    # date) and by the channel listings (CLAUDE.md: "An empty channel list
    # says why it is empty") -- was only ever rendered by the LIST path. A
    # count-shaped phrasing of the exact same question ("how many overdue
    # applications do I have") skipped this generic formatter straight past
    # it to the bare "There are no X applications", so "which applications
    # are overdue" and "how many overdue applications do I have" gave two
    # different answers to the same question -- the second one dropping the
    # explanation entirely. English only: `empty_note` carries no Tamil form.
    if count == 0 and not is_tamil and structured_data.get("empty_note"):
        return structured_data["empty_note"]

    import re as _re
    # "All" carries no information, so drop it: "All NISD & ISD Applications
    # (...)" -> "NISD & ISD Applications (...)". The status adjective is a
    # different matter and is kept -- stripping "Approved" made "how many
    # approved applications do I have" answer "There are 34 applications in
    # your jurisdiction", which reads as the whole queue rather than the
    # filtered count.
    label = _re.sub(r'^All\s+', '', qtype_raw, flags=_re.IGNORECASE)
    _STATUS_TA = {"approved": "அங்கீகரிக்கப்பட்ட",
                  "rejected": "நிராகரிக்கப்பட்ட",
                  "pending": "நிலுவையில் உள்ள",
                  # "Overdue Applications" / "Non-Overdue ... Applications" --
                  # missing here, so "எத்தனை காலதாமதமான விண்ணப்பங்கள்" answered
                  # "உங்கள் அதிகார வரம்பில் Overdue விண்ணப்பங்கள் எதுவும் இல்லை",
                  # the one English word left standing in an otherwise-Tamil
                  # sentence.
                  "non-overdue": "காலதாமதமாகாத",
                  "overdue": "காலதாமதமான"}

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
    # "Approved applications", not "Approved Applications", mid-sentence. A
    # multi-status label carries a second one after the "&" ("Approved &
    # Pending"), which the leading-anchor rule alone left capitalised --
    # "51 approved & Pending NISD applications".
    label_en = _re.sub(r'(?:^|(?<=& ))(Approved|Rejected|Pending|In Progress|Escalated)\b',
                       lambda m: m.group(1).lower(), label_en)
    label_ta = label.replace("Applications", "விண்ணப்பங்கள்").replace("Application", "விண்ணப்பம்")
    for _st_en, _st_ta in _STATUS_TA.items():
        label_ta = _re.sub(rf'^{_st_en}\b', _st_ta, label_ta, flags=_re.IGNORECASE)
    # "Received Today (date)" / "Received Yesterday (date)" / "Received Day
    # Before Yesterday (date)" / "Received on <date>" -- a query_type phrase
    # built for "how many applications were assigned/received today", not
    # anchored at the start of the label (an "assigned today" count still
    # says "விண்ணப்பங்கள் Received Today (...) எதுவும் இல்லை" without this),
    # so it needs a substring replace rather than the `^` prefix rule above.
    label_ta = _re.sub(r'\bReceived Today\b', 'இன்று பெறப்பட்டவை', label_ta, flags=_re.IGNORECASE)
    label_ta = _re.sub(r'\bReceived Yesterday\b', 'நேற்று பெறப்பட்டவை', label_ta, flags=_re.IGNORECASE)
    label_ta = _re.sub(r'\bReceived Day Before Yesterday\b', 'நேற்றுமுன்று பெறப்பட்டவை',
                       label_ta, flags=_re.IGNORECASE)
    label_ta = _re.sub(r'\bReceived on\b', 'பெறப்பட்டது:', label_ta, flags=_re.IGNORECASE)

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
_LIMITABLE_LIST_KEYS = ("applications", "field_visits", "visits")


def _apply_result_limit(structured_data, message: str, intent: str):
    """Cut a listing to the slice the officer asked for.

    The cap is applied AFTER the query, over the rows the officer would
    otherwise have been shown, so it never changes which rows qualify -- only
    how many are printed. The full total is kept in ``result_limit`` so
    the answer can say "the first 2 of 64" or "2nd result of 64".

    end values:
      'head'     -- first n rows  ("first 3", "top 5")
      'tail'     -- last n rows   ("last 3", "latest 5")
      'head_nth' -- single row at position n-1  ("2nd newest", "3rd oldest")

    Returns the same dict, unchanged, whenever no cap was asked for.
    """
    if not isinstance(structured_data, dict) or intent not in _LIMITABLE_LIST_INTENTS:
        return structured_data
    if _is_count_only_query(message):
        return structured_data

    sel = extract_row_selection(message)
    if sel and sel[0] in ("rows", "parity"):
        for key in ("applications",):
            rows = structured_data.get(key)
            if not isinstance(rows, list) or not rows:
                continue
            total = len(rows)
            if sel[0] == "parity":
                picked = rows[1::2] if sel[1] == "even" else rows[0::2]
                label = f"{sel[1]} rows"
            else:
                picked = [rows[i - 1] for i in sel[1] if 0 < i <= total]
                label = "rows " + ", ".join(str(i) for i in sel[1])
            structured_data[key] = picked
            structured_data["count"] = len(picked)
            structured_data["result_limit"] = {"n": len(picked), "end": "rows", "total": total, "label": label,
                                               "out_of_range": not picked}
        return structured_data
    parsed = (sel[1], "head_nth") if sel else extract_result_limit(message)
    if not parsed:
        return structured_data
    n, end = parsed

    for key in _LIMITABLE_LIST_KEYS:
        rows = structured_data.get(key)
        if not isinstance(rows, list) or not rows:
            continue
        total = len(rows)

        if end == "head_nth":
            # Ordinal pick: "2nd newest", "3rd oldest".
            # rows is already sorted by extract_sort_order; take item at index n-1.
            idx = n - 1
            if idx >= total or idx < 0:
                # Nth item doesn't exist in the result set.
                structured_data[key] = []
                structured_data["result_limit"] = {
                    "n": n, "end": "head_nth", "total": total,
                    "out_of_range": True,
                }
            else:
                structured_data[key] = rows[idx: idx + 1]
                if isinstance(structured_data.get("count"), int):
                    structured_data["count"] = 1
                structured_data["result_limit"] = {
                    "n": n, "end": "head_nth", "total": total,
                }
        else:
            if total <= n:
                # Asking for the first 5 of 3 is answered by the 3.
                continue
            structured_data[key] = rows[:n] if end == "head" else rows[-n:]
            if isinstance(structured_data.get("count"), int):
                structured_data["count"] = n
            structured_data["result_limit"] = {"n": n, "end": end, "total": total}
    return structured_data


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
        "எத்தனை", "எண்ணிக்கை", "தொகை", "மொத்த எண்ணிக்கை", "மொத்தம்", "எவ்வளவு",
        # Tanglish: "evlo application iruku", "ethana file"
        "evlo", "evvalavu", "ethana", "ethanai", "ethane"
    ]
    has_count_trigger = any(kw in msg for kw in _count_triggers) or bool(re.search(r'\b(?:no|no\.|nos|nos\.|number|num|count)\s+of\b', msg))
    # "how mny not approved applications" -- a dropped letter in "many" (or
    # "count"/"total") matched none of the exact phrases above, so a plainly
    # count-shaped question rendered as the full table instead of the count
    # sentence. The rest of this file typo-corrects "many" at the same
    # 1-edit budget (rag.py's `_VERB_TYPO_TARGETS`); this reaches the answer
    # from the raw message before that correction runs, so it is repeated
    # here rather than relied on.
    if not has_count_trigger:
        _count_tokens = extract_tokens(msg)
        has_count_trigger = any(
            is_token_typo_match(tok, "many", max_edits=1)
            or is_token_typo_match(tok, "count")
            or is_token_typo_match(tok, "total")
            for tok in _count_tokens
        )

    if has_count_trigger and not has_list_word:
        return True
    if has_count_trigger and has_list_word and any(w in msg for w in ["count", "total", "how many", "எத்தனை", "எண்ணிக்கை"]):
        return True
    return False


# ── Format follow-ups ("in table", "as bullets", "in short") ────────────────
# A bare "in table" names no subject: parse_intent lands on general_query and
# the LLM, having no idea which table is meant, asks the officer what they
# want. The subject is the answer already on screen, so the whole family of
# format follow-ups is handled here as pure presentation — the previous
# assistant turn is re-rendered, never re-fetched and never re-worded.
#
# Format key -> the words that ask for it. Longest match wins, so "bullet
# points" resolves to bullets rather than to points' own entry.
_REFORMAT_WORDS = {
    "table": [
        "table", "tabular", "grid",
        "அட்டவணை", "அட்டவணையாக", "அட்டவணையில்", "attavanai", "attavanaiyaaga",
    ],
    "numbered": [
        "numbered list", "numbered", "number list", "numbering", "steps",
        "step by step", "1 2 3", "எண்ணிட்ட", "ennitta",
    ],
    "bullets": [
        "bullet points", "bullet point", "bullets", "bullet",
        "list", "listed", "points", "point form", "point wise", "pointwise",
        "பட்டியல்", "பட்டியலாக", "puLLigal", "pattiyal", "pattiyalaaga",
    ],
    "prose": [
        "paragraph", "sentence", "sentences", "prose", "plain text",
        "plain english", "words", "one line", "single line", "oneline",
        "வாக்கியம்", "பத்தி", "vaakkiyam", "patthi",
    ],
    "short": [
        "short", "shorter", "brief", "briefly", "summary", "summarise",
        "summarize", "concise", "crisp", "tldr", "in a nutshell",
        "சுருக்கமாக", "surukkamaaga", "surukkama",
    ],
    "json": ["json", "json format", "key value", "key-value"],
}
# Words that may surround a format request without changing what it asks for.
_REFORMAT_FILLER = {
    "in", "as", "a", "an", "the", "into", "to", "of", "it", "this", "that",
    "them", "those", "these", "all", "same", "above", "previous", "last",
    "show", "me", "give", "put", "make", "convert", "display", "render",
    "rewrite", "write", "print", "reply", "answer", "response", "say", "tell",
    "please", "pls", "plz", "kindly", "again", "now", "only", "just", "can",
    "you", "could", "would", "form", "format", "formatted", "view", "layout",
    "style", "mode", "way", "and", "or", "with",
    "la", "la?", "podu", "kaattu", "kaatu", "kaamikka", "venum", "kudu",
    "ஆக", "இல்", "காட்டு", "போடு", "வேண்டும்", "கொடு",
}

_REFORMAT_ROW_PATTERNS = [
    # "• **Application Type**: ISD"  (the multi-field detail answer)
    re.compile(r"^\s*(?:[•\-\*]|\d+[.)])\s*\*\*(?P<label>[^*]+?)\*\*\s*[:：]\s*(?P<value>.*)$"),
    # "• Application Type: ISD"
    re.compile(r"^\s*(?:[•\-\*]|\d+[.)])\s*(?P<label>[^:：]{1,60}?)\s*[:：]\s*(?P<value>.*)$"),
    # "**Status**: pending" with no bullet
    re.compile(r"^\s*\*\*(?P<label>[^*]+?)\*\*\s*[:：]\s*(?P<value>.*)$"),
]
# "The Application Type for 2026/0154/28/001167 is: ISD" — single-field answer.
_REFORMAT_SENTENCE_RE = re.compile(
    r"^\s*(?:the\s+)?(?P<label>[A-Za-z][A-Za-z ()./]{1,40}?)\s+for\s+\S+\s+is\s*[:：]?\s*(?P<value>.+?)\.?\s*$",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _detect_reformat_request(message: str) -> Optional[str]:
    """The format a bare follow-up asks for, or None if it is a real query.

    Everything outside the format word must be filler, so "pending
    applications in a table" stays a query and goes down the normal path.
    """
    m = (message or "").strip().lower().rstrip("?!.")
    if not m or len(m.split()) > 6:
        return None
    hits = [
        (word, fmt)
        for fmt, words in _REFORMAT_WORDS.items()
        for word in words
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", m)
    ]
    if not hits:
        return None
    # Longest phrase wins: "bullet points" is bullets, not points.
    word, fmt = max(hits, key=lambda h: len(h[0]))
    leftover = m
    for w, _ in sorted(hits, key=lambda h: len(h[0]), reverse=True):
        leftover = leftover.replace(w, " ")
    if not all(tok in _REFORMAT_FILLER for tok in leftover.split()):
        return None
    return fmt


def _strip_tags(fragment: str) -> str:
    text = _TAG_RE.sub(" ", fragment or "")
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        text = text.replace(entity, char)
    return re.sub(r"\s+", " ", text).strip()


def _parse_previous_answer(prev: str) -> dict:
    """Previous answer -> {caption, headers, rows}. Empty rows means unusable.

    Handles the three shapes an answer actually arrives in: an HTML table from
    build_html_response, a bullet/numbered field list, and the single-field
    sentence. Rows are lists of cells so a multi-column listing survives the
    round trip.
    """
    caption, headers, rows = "", [], []
    if not prev:
        return {"caption": "", "headers": [], "rows": []}

    if "<table" in prev.lower():
        intro = re.search(r"<div[^>]*class=['\"]table-intro['\"][^>]*>(.*?)</div>",
                          prev, re.IGNORECASE | re.DOTALL)
        if intro:
            caption = _strip_tags(intro.group(1)).rstrip(":：").strip()
        table = re.search(r"<table.*?</table>", prev, re.IGNORECASE | re.DOTALL)
        body = table.group(0) if table else prev
        headers = [_strip_tags(c) for c in
                   re.findall(r"<th[^>]*>(.*?)</th>", body, re.IGNORECASE | re.DOTALL)]
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.IGNORECASE | re.DOTALL):
            cells = [_strip_tags(c) for c in
                     re.findall(r"<td[^>]*>(.*?)</td>", tr, re.IGNORECASE | re.DOTALL)]
            if cells:
                rows.append(cells)
        return {"caption": caption, "headers": headers, "rows": rows}

    for raw_line in prev.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matched = False
        for pat in _REFORMAT_ROW_PATTERNS:
            mt = pat.match(line)
            if mt:
                rows.append([mt.group("label").strip(), mt.group("value").strip()])
                matched = True
                break
        if matched:
            continue
        if not rows and not caption:
            sent = _REFORMAT_SENTENCE_RE.match(line)
            if sent:
                rows.append([sent.group("label").strip().title(),
                             sent.group("value").strip()])
                continue
            # A trailing-colon line above the bullets is the answer's header.
            if line.endswith(":") or line.endswith("："):
                caption = line.rstrip(":：").strip()
    return {"caption": caption, "headers": headers, "rows": rows}


def _render_reformatted(parsed: dict, fmt: str, language: str) -> str:
    """Render a parsed previous answer in the requested format."""
    rows = parsed.get("rows") or []
    if not rows:
        return ""
    is_ta = language in ("ta", "tanglish")
    caption = parsed.get("caption") or ""
    headers = parsed.get("headers") or []
    pairs = [(r[0], r[1] if len(r) > 1 else "") for r in rows] if all(
        len(r) <= 2 for r in rows) else []

    def _cells(row):
        return [c if c not in (None, "") else "-" for c in row]

    if fmt == "table":
        from html import escape as _esc
        if headers:
            head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
        else:
            head = (f"<th>{'விவரம்' if is_ta else 'Field'}</th>"
                    f"<th>{'மதிப்பு' if is_ta else 'Value'}</th>")
        width = max(len(headers), max(len(r) for r in rows))
        body = ""
        for row in rows:
            cells = _cells(row) + ["-"] * (width - len(row))
            first = f"<td><strong>{_esc(cells[0])}</strong></td>" if not headers else f"<td>{_esc(cells[0])}</td>"
            body += "<tr>" + first + "".join(f"<td>{_esc(c)}</td>" for c in cells[1:]) + "</tr>"
        intro = f"<div class='table-intro'>{_esc(caption)}</div>" if caption else ""
        return (intro + "<table class='data-table'><thead><tr>" + head
                + f"</tr></thead><tbody>{body}</tbody></table>")

    if fmt in ("bullets", "numbered"):
        lines = [f"{caption}:"] if caption else []
        for i, row in enumerate(rows, start=1):
            marker = f"{i}." if fmt == "numbered" else "•"
            cells = _cells(row)
            if pairs:
                lines.append(f"{marker} **{cells[0]}**: {cells[1] if len(cells) > 1 else '-'}")
            elif headers and len(headers) == len(cells):
                joined = ", ".join(f"{h}: {c}" for h, c in zip(headers, cells))
                lines.append(f"{marker} {joined}")
            else:
                lines.append(f"{marker} " + " | ".join(cells))
        return "\n".join(lines)

    if fmt == "json":
        import json as _json
        if pairs:
            payload = {re.sub(r"\W+", "_", l.strip().lower()).strip("_"): v
                       for l, v in pairs}
        else:
            keys = headers or [f"col_{i+1}" for i in range(max(len(r) for r in rows))]
            payload = [dict(zip(keys, _cells(r))) for r in rows]
        return "```json\n" + _json.dumps(payload, indent=2, ensure_ascii=False) + "\n```"

    if fmt == "short":
        parts = [f"{l}: {v or '-'}" for l, v in pairs] if pairs else \
                [" ".join(_cells(r)) for r in rows]
        head = f"{caption} — " if caption else ""
        return head + "; ".join(parts) + "."

    # prose
    if pairs:
        joiner = " மற்றும் " if is_ta else " and "
        clauses = [f"{l.lower()} is {v or 'not recorded'}" for l, v in pairs] if not is_ta \
            else [f"{l} {v or 'பதிவு இல்லை'}" for l, v in pairs]
        body = ", ".join(clauses[:-1])
        body = (body + joiner + clauses[-1]) if len(clauses) > 1 else clauses[0]
    else:
        body = "; ".join(" ".join(_cells(r)) for r in rows)
    lead = f"{caption}: " if caption else ""
    return f"{lead}{body}."


def _reformat_previous_answer(chat_history: list, fmt: str, language: str) -> str:
    """The previous assistant answer in `fmt`, or "" if it cannot be reshaped."""
    prev = _last_assistant_message(chat_history)
    rendered = _render_reformatted(_parse_previous_answer(prev), fmt, language)
    if rendered:
        return rendered
    # Nothing tabular/bulleted was found to reshape. If the previous answer
    # was already a single plain-prose line (no HTML table, no bullet/field
    # rows) and prose or a short form is what's being asked for, it already
    # IS that -- "summarise briefly" / "in a sentence" right after a one-line
    # service-code answer used to report "it wasn't a set of fields" instead
    # of just repeating the sentence it already was.
    if fmt in ("prose", "short") and prev and "<table" not in prev.lower():
        return prev.strip()
    return ""


_REFORMAT_NAMES = {
    "en": {"table": "a table", "bullets": "bullet points", "numbered": "a numbered list",
           "prose": "a sentence", "short": "a short form", "json": "JSON"},
    "ta": {"table": "அட்டவணையாக", "bullets": "புள்ளிகளாக", "numbered": "எண்ணிட்ட பட்டியலாக",
           "prose": "வாக்கியமாக", "short": "சுருக்கமாக", "json": "JSON ஆக"},
}


def _cannot_reformat_reply(fmt: str, language: str, had_previous: bool) -> str:
    is_ta = language in ("ta", "tanglish")
    name = _REFORMAT_NAMES["ta" if is_ta else "en"].get(fmt, fmt)
    if is_ta:
        if had_previous:
            return (f"என் கடைசி பதிலை {name} மாற்ற முடியவில்லை — அது தனித்தனி "
                    "விவரங்களாக இல்லை. எந்த விவரங்கள் வேண்டும் என்று கேளுங்கள், "
                    "அதை அப்படியே காட்டுகிறேன்.")
        return (f"{name} காட்ட முந்தைய பதில் எதுவும் இல்லை. "
                "முதலில் என்ன விவரம் வேண்டும் என்று கேளுங்கள்.")
    if had_previous:
        return (f"I can't reshape my last reply into {name} — it wasn't a set of "
                "fields. Ask me for the details you want and I'll show them "
                f"in {name}.")
    return (f"There is no previous answer to show as {name} yet. "
            f"Ask for the details you want and I'll present them in {name}.")


# ── Bare date-scope follow-ups ("last month") ────────────────────────────────
def _last_user_message(chat_history: list, skip_bare_scopes: bool = True) -> str:
    """
    The officer's previous question. The frontend snapshots chat_history BEFORE
    storing the current message, so the last user entry is the prior turn.

    Bare periods are skipped by default: in "…approved this week" → "last
    month" → "what about June?", the question being re-scoped each time is the
    first one, not the re-scope that preceded it.

    A contentless turn ("no", "ok", "thanks") is skipped too -- it answered no
    question of its own, so it must not become "the previous question" for
    whatever comes after it. Without this, "explain ISD" → "no" (declining the
    bot's own "ask for more detail?" prompt) → "explain more detailed" lost
    the ISD topic: the intervening "no" became the thing being re-explained.
    """
    for h in reversed(chat_history or []):
        if (h.get("role") or "user") != "user":
            continue
        content = (h.get("content") or "").strip()
        if not content:
            continue
        if skip_bare_scopes and is_bare_date_scope(content):
            continue
        if _contentless_message(content) is not None:
            continue
        return content
    return ""


def _last_subject_message(chat_history: list) -> str:
    """The last user message that names what it is about ("show applications",
    "how many field visits"); a bare "how many" between it and a period follow-up
    ("last month") must not become the question that period re-scopes."""
    for h in reversed(chat_history or []):
        if (h.get("role") or "user") != "user":
            continue
        content = (h.get("content") or "").strip()
        if content and not is_bare_date_scope(content) and _OWN_SUBJECT_RE.search(content):
            return content
    return _last_user_message(chat_history)


def _resolve_prev_intent(chat_history: list) -> Optional[str]:
    """
    Intent of the previous turn.

    The client sends back only role/content/language/timestamp, so there is no
    intent field to read (the `[intent:…]` marker below is honoured if a client
    ever supplies one). Re-parsing the previous user message is what actually
    recovers it, and parse_intent is pure string work — no DB, no LLM.
    """
    for h in reversed(chat_history or []):
        if (h.get("role") or "user") == "assistant":
            marker = re.search(r'\[intent:([\w_]+)\]', h.get("content") or "")
            if marker:
                return marker.group(1)
            if h.get("intent"):
                return h.get("intent")
            break
    prev_user = _last_user_message(chat_history)
    return parse_intent(prev_user) if prev_user else None


def _rescope_followup_message(message: str, chat_history: list) -> str:
    """
    Fold a bare period ("last month", "what about June?") back into the
    question it re-scopes.

    "how many applications did I approve this week" followed by "lastt month"
    becomes "how many applications did I approve last month", so the previous
    turn's status/type/geography filters survive the follow-up instead of the
    message being answered as a contentless general query.

    Returns `message` unchanged when it is not a bare date follow-up.
    """
    if not message or not is_bare_date_scope(message):
        return message
    prev_intent = _resolve_prev_intent(chat_history)
    if prev_intent not in DATE_SCOPED_INTENTS:
        _subject = _last_subject_message(chat_history)
        prev_intent = parse_intent(_subject) if _subject else None
        if prev_intent not in DATE_SCOPED_INTENTS:
            return message
    prev_question = strip_date_scope_phrases(_last_subject_message(chat_history))
    scope = extract_date_scope_fragment(message) or message.strip()
    if not prev_question:
        return message
    return f"{prev_question} {scope}"


def _named_lifecycle_status(text: str) -> Optional[str]:
    m = _LIFECYCLE_STATUS_RE.search(text or "")
    if not m:
        return None
    word = re.sub(r"[\s-]+", "_", m.group(1).lower())
    return {"approve": "approved", "reject": "rejected"}.get(word, word)


# "is 2026/.../046461 approved?" / "has it been rejected?" -- a yes/no about an
# EXPLICIT application's status. `_asks_for_specific_detail` only recognises
# wh-openers (what/which/who/...), so this fell past it, past every dedicated
# field handler, and into the generic "Here are the details for X" card -- a
# confident non-answer to a question that wanted exactly one word. The
# equivalent follow-up ("is it approved?" with no number, against the record
# left from a previous turn) already got a real Yes/No via `_number_qa`; this
# gives the explicit-number case the same answer.
_STATUS_YES_NO_OPEN_RE = re.compile(
    r"^\s*(?:so\s+)?(?:is|was|has|have|had|does|did"
    r"|isn'?t|wasn'?t|hasn'?t|doesn'?t|didn'?t)\b",
    re.IGNORECASE,
)


def _status_yes_no_question(message: str) -> Optional[str]:
    """The lifecycle status an explicit yes/no question names, or None."""
    if not message or not _STATUS_YES_NO_OPEN_RE.search(message):
        return None
    return _named_lifecycle_status(message)


def _cb():
    """Lazy back-reference to chatbot.py (avoids the circular import this
    module -> chatbot.py already has at module-load time)."""
    from backend.services import chatbot
    return chatbot


def _render_status_role_answer(structured_data: dict, asked_status: str, asked_stage: str, language: str) -> str:
    """"has the ZDT approved X?" names BOTH a status (approved) and a role
    (ZDT/TAHSILDAR) -- answering from status alone said "Yes, it is approved"
    on an ISD file the Tahsildar/ZDT never even reached (ISD closes at SD;
    only NISD files ever reach TAHSILDAR -- see CLAUDE.md), misattributing the
    SD's decision to a role that took no part in it. `decision_stage` /
    `decision_by` (the workflow hop that actually closed the file) settle who
    really decided it, never the asked role by default."""
    app_no = structured_data.get("application_number", "N/A")
    actual_status = (structured_data.get("status") or "").lower()
    decision_desk = (structured_data.get("decision_desk") or "").upper()
    asked_label = _cb()._STAGE_LABELS.get(asked_stage, asked_stage)
    is_tamil = language in ("ta", "tanglish")
    if not decision_desk:
        # still open -- no desk has decided it yet, so no role "approved" or "rejected" it
        if is_tamil:
            return f"இல்லை — {app_no} இன்னும் முடிவு செய்யப்படவில்லை; {asked_label} இன்னும் அதை அங்கீகரிக்கவோ நிராகரிக்கவோ இல்லை."
        return f"No — {app_no} has not yet been decided; {asked_label} has not approved or rejected it."
    if decision_desk != asked_stage:
        decided_label = _cb()._STAGE_LABELS.get(decision_desk, decision_desk)
        if is_tamil:
            return (f"இல்லை — {asked_label} இந்த விண்ணப்பத்தை முடிவு செய்யவில்லை; "
                    f"{decided_label} ({actual_status or 'not recorded'}) முடிவு செய்தது, {app_no}-க்கு.")
        return (f"No — {asked_label} did not decide {app_no}; it was decided by {decided_label} "
                f"({actual_status or 'not recorded'}).")
    match = actual_status == asked_status
    lead = "Yes" if match else "No"
    if is_tamil:
        lead = "ஆம்" if match else "இல்லை"
        return f"{lead} — {app_no} {asked_label} மூலம் {actual_status or 'not recorded'} நிலையில் முடிவு செய்யப்பட்டது."
    return f"{lead} — {app_no} was decided by {asked_label}, {actual_status or 'not recorded'}."


def _render_status_yes_no_answer(structured_data: dict, asked_status: str, language: str) -> str:
    app_no = structured_data.get("application_number", "N/A")
    actual = (structured_data.get("status") or "").lower()
    match = bool(actual) and asked_status == actual
    actual_label = actual.replace("_", " ") or "not recorded"
    asked_label = asked_status.replace("_", " ")
    if language in ("ta", "tanglish"):
        lead = "ஆம்" if match else "இல்லை"
        if match:
            return f"{lead} — {app_no} {actual_label} நிலையில் உள்ளது."
        return f"{lead} — {app_no} {actual_label} நிலையில் உள்ளது, {asked_label} அல்ல."
    lead = "Yes" if match else "No"
    if match:
        return f"{lead} — {app_no} is {actual_label}."
    return f"{lead} — {app_no} is {actual_label}, not {asked_label}."


# "is it with SIS?" / "is it currently with SD?" -- a yes/no about which DESK
# holds the file, distinct from `_status_yes_no_question` (approved/pending/
# rejected is the lifecycle status; SIS/SD/DIS/Tahsildar is the workflow
# stage). "is it rejected?" is still read as the status question -- checked
# first at the call site -- since REJECTED is also a valid stage value and the
# officer's own word ("rejected") is the status one, not the desk name.
_STAGE_NAME_MAP = (
    ("TAHSILDAR", r"\btahsildar\b|\bzdt\b|\bzonal\s+(?:level\s+)?tahsildar\b"),
    ("DIS", r"\bdis\b|\bdeputy\s+inspector\b"),
    ("SD", r"\bsd\b|\bsenior\s+draughtsman\b|\bdraughtsman\b"),
    ("SIS", r"\bsis\b|\bsub\s+inspector\b"),
    ("COMPLETED", r"\bcompleted\b|\bclosed\b"),
)


def _named_stage(text: str) -> Optional[str]:
    t = text or ""
    for code, pattern in _STAGE_NAME_MAP:
        if re.search(pattern, t, re.IGNORECASE):
            return code
    return None


def _stage_yes_no_question(message: str) -> Optional[str]:
    """The desk/stage an explicit yes/no question names, or None."""
    if not message or not _STATUS_YES_NO_OPEN_RE.search(message):
        return None
    return _named_stage(message)


_DECISION_DESK_LABEL = {
    "SIS": "the Sub Inspector Surveyor (SIS)", "SD": "the Senior Draughtsman (SD)",
    "DIS": "the Deputy Inspector Surveyor (DIS)", "TAHSILDAR": "the Zonal Level Tahsildar",
}


def _render_status_role_answer(structured_data: dict, asked_status: str, asked_role: str, language: str) -> str:
    """"has the ZDT approved X?" -- a yes/no question naming BOTH a status and a
    role/desk. Answered from `decision_desk` (the FROM-stage of the workflow hop
    that actually closed the file), never from the overall status alone -- an
    ISD file closed at SD (see CLAUDE.md's documented workflow chains) was never
    touched by the Tahsildar, even though its overall status reads "approved"."""
    app_no = structured_data.get("application_number", "N/A")
    desk = structured_data.get("decision_desk")
    actual_status = (structured_data.get("status") or "").lower()
    is_tamil = language in ("ta", "tanglish")
    if not desk:
        lead = "இல்லை" if is_tamil else "No"
        tail = f"{app_no} இன்னும் முடிவு செய்யப்படவில்லை." if is_tamil else f"{app_no} has not yet been decided."
        return f"{lead} — {tail}"
    desk_label = _DECISION_DESK_LABEL.get(desk, desk)
    role_matches = desk == asked_role
    status_matches = bool(actual_status) and actual_status == asked_status
    if role_matches and status_matches:
        lead, tail = "Yes", f"{app_no} was {actual_status} by {desk_label}."
    elif role_matches:
        lead = "No"
        tail = f"{app_no} was {actual_status or 'not recorded'} (not {asked_status}) by {desk_label}."
    else:
        asked_label = _DECISION_DESK_LABEL.get(asked_role, asked_role)
        lead = "No"
        tail = f"{app_no} was decided by {desk_label}, not {asked_label}."
    if is_tamil:
        lead = "ஆம்" if lead == "Yes" else "இல்லை"
    return f"{lead} — {tail}"


def _is_stage_ever_question(message: str) -> bool:
    return bool(_cb()._STAGE_EVER_PHRASE_RE.search(message or ""))


def _render_stage_ever_answer(hops: Optional[list], app_no: str, asked_stage: str,
                               current_stage: str, language: str) -> str:
    is_tamil = language in ("ta", "tanglish")
    stage_names = {
        "SIS": "Sub Inspector Surveyor (SIS)", "SD": "Senior Draughtsman (SD)",
        "DIS": "Deputy Inspector Surveyor (DIS)", "TAHSILDAR": "the Zonal Level Tahsildar",
        "COMPLETED": "completed", "REJECTED": "rejected",
    }
    asked_label = stage_names.get(asked_stage, asked_stage)
    hop = next((h for h in (hops or []) if str(h.get("to_stage") or "").upper() == asked_stage), None)
    if hop is not None:
        when = _wfh_fmt_dt(hop["performed_at"]) or "an unrecorded date"
        if is_tamil:
            return f"ஆம் — {app_no} {asked_label}-க்கு {when} அன்று அனுப்பப்பட்டது."
        return f"Yes — {app_no} was referred to {asked_label}, on {when}."
    cur_label = stage_names.get((current_stage or "").upper(), current_stage or "not recorded")
    if is_tamil:
        return f"இல்லை — {app_no} {asked_label}-க்கு இன்னும் அனுப்பப்படவில்லை; தற்போது {cur_label} நிலையில் உள்ளது."
    return f"No — {app_no} has not been referred to {asked_label}; it is currently with {cur_label}."


_STAGE_DISPLAY_NAMES = {
    "SIS": "Sub Inspector Surveyor (SIS)", "SD": "Senior Draughtsman (SD)",
    "DIS": "Deputy Inspector Surveyor (DIS)", "TAHSILDAR": "the Zonal Level Tahsildar",
    "COMPLETED": "completed", "REJECTED": "rejected",
}


def _render_stage_yes_no_answer(structured_data: dict, asked_stage: str, language: str) -> str:
    app_no = structured_data.get("application_number", "N/A")
    actual = (structured_data.get("stage") or "").upper()
    match = bool(actual) and asked_stage == actual
    actual_label = _STAGE_DISPLAY_NAMES.get(actual, actual or "not recorded")
    if language in ("ta", "tanglish"):
        lead = "ஆம்" if match else "இல்லை"
        return f"{lead} — {app_no} தற்போது {actual_label} நிலையில் உள்ளது."
    lead = "Yes" if match else "No"
    return f"{lead} — {app_no} is currently with {actual_label}."


# "has this case been REFERRED/FORWARDED/SENT to the Tahsildar?" asks about the
# file's HISTORY -- did it ever reach that desk -- not where it sits right now.
# Distinct from the plain "is it CURRENTLY with X" / "is it with X right now"
# phrasing above, which stays a current-location question on purpose.
_STAGE_EVER_RE = re.compile(
    r"\b(?:referred|forwarded|sent|escalated|passed|moved|routed)\b.{0,15}?\b(?:to|through)\b"
    r"|\bhas\s+it\s+(?:ever\s+)?reached\b|\breached\s+(?:the\s+)?(?:tahsildar|sd|dis|sis|senior\s+draughtsman)\b"
    r"|\bpassed\s+through\b",
    re.IGNORECASE,
)
_STAGE_CURRENT_RE = re.compile(
    r"\bcurrently\s+with\b|\bwith\b.{0,15}?\bright\s+now\b|\bat\s+the\s+moment\b", re.IGNORECASE)


def _is_stage_ever_question(message: str) -> bool:
    """True for "has this been referred/sent/forwarded to X", "has it reached X",
    "was it passed through X" -- a question about the file's HISTORY. False for
    "is it currently with X" / "is it with X right now", which stay the plain
    current-location question `_render_stage_yes_no_answer` already answers."""
    msg = message or ""
    if _STAGE_CURRENT_RE.search(msg):
        return False
    return bool(_STAGE_EVER_RE.search(msg))


def _render_stage_ever_answer(hops: list, app_no: str, asked_stage: str,
                              current_stage: str, language: str) -> str:
    """"has this case been referred to the Tahsildar?" -- answered from
    workflow_history (did any hop's to_stage ever equal the asked stage), not
    from the CURRENT stage alone. A file closed at TAHSILDAR still answers Yes
    even once its current stage has moved on to the terminal COMPLETED/REJECTED."""
    reached = any((h.get("to_stage") or "").upper() == asked_stage for h in (hops or []))
    asked_label = _STAGE_DISPLAY_NAMES.get(asked_stage, asked_stage)
    current_label = _STAGE_DISPLAY_NAMES.get((current_stage or "").upper(),
                                             current_stage or "not recorded")
    is_tamil = language in ("ta", "tanglish")
    if reached:
        lead = "ஆம்" if is_tamil else "Yes"
        tail = (f"{app_no} {asked_label} நிலையை அடைந்தது." if is_tamil
                else f"{app_no} has been referred to {asked_label}.")
    else:
        lead = "இல்லை" if is_tamil else "No"
        tail = (f"{app_no} {asked_label} நிலையை அடையவில்லை; தற்போது {current_label} நிலையில் உள்ளது." if is_tamil
                else f"{app_no} has not been referred to {asked_label}; it is currently with {current_label}.")
    return f"{lead} — {tail}"


# "how many approved ISD applications do I have?" -> "how many approved NISD?"
# must not fold into a message that carries "ISD" and be read as an ISD+NISD
# listing. NISD is checked before ISD ("nisd" contains "isd").
_APP_TYPE_RE = re.compile(r"\b(nisd|isd)\b", re.IGNORECASE)


def _named_app_type(text: str) -> Optional[str]:
    m = _APP_TYPE_RE.search(text or "")
    return m.group(1).upper() if m else None


_YEAR_MONTH_RE = re.compile(
    r"\b20\d\d\b|\b(?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\b", re.IGNORECASE)


def _named_periods(text: str) -> set:
    return {m.group(0).lower() for m in _YEAR_MONTH_RE.finditer(text or "")}


_TYPE_CODE_WORD = {"0153": "NISD", "0154": "ISD"}
_TYPE_TYPOS = {"nsid": "NISD", "nids": "NISD", "nisdd": "NISD", "nissd": "NISD",
               "iisd": "ISD", "isdd": "ISD", "sid": "ISD",}
_BARE_TYPE_FILLER = frozenset({"back", "to", "again", "um", "kum", "ku", "na", "naa", "ah", "aa", "pathi", "patri", "pathiyum",
                               "ok", "then", "and", "what", "about", "how", "for", "the", "is", "in",
                               "என்ன", "பற்றி", "பத்தி", "ஆ", "இது"})


def _bare_type_word(message: str):
    """"nisd", "NISD?", "nisd ku", "what about nisd", "0153" -> the canonical type,
    when that is ALL the message says. None otherwise."""
    tokens = [t for t in re.findall(r"[0-9A-Za-z\u0B80-\u0BFF]+", message or "")]
    if not tokens or len(tokens) > 4:
        return None
    found = None
    for t in tokens:
        low = t.lower()
        if low in ("nisd", "isd"):
            canon = low.upper()
        elif low in _TYPE_CODE_WORD:
            canon = _TYPE_CODE_WORD[low]
        elif low in _TYPE_TYPOS:
            canon = _TYPE_TYPOS[low]
        elif low in _BARE_TYPE_FILLER or t in _BARE_TYPE_FILLER:
            continue
        elif len(low) >= 3 and fctx.correct_spelling(low).lower() in ("nisd", "isd"):
            canon = fctx.correct_spelling(low).upper()
        else:
            return None
        if found and found != canon:
            return None
        found = canon
    return found


# ── "their wards", "the status of each" -- one field down the listed column ──
_PROJECT_LABELS = {
    "ward": ("ward", "வார்டு"),
    "block": ("block", "பிளாக்"),
    "status": ("status", "நிலை"),
    "stage": ("stage", "கட்டம்"),
    "application_type": ("type", "வகை"),
    "survey_no": ("survey number", "புல எண்"),
    "can_number": ("CAN number", "கணக்கெண்"),
    "submission_channel": ("submission channel", "சமர்ப்பிப்பு வழி"),
    "applicant_name": ("applicant", "விண்ணப்பதாரர்"),
    "applicant_mobile": ("mobile", "மொபைல்"),
    "fee_amount": ("fee", "கட்டணம்"),
    "submission_date": ("submission date", "சமர்ப்பித்த தேதி"),
    "last_updated_date": ("last updated", "கடைசியாக புதுப்பிக்கப்பட்டது"),
    "igrs_form6_number": ("IGRS Form 6 number", "IGRS படிவம் 6 எண்"),
    "district": ("district", "மாவட்டம்"),
    "taluk": ("taluk", "தாலுகா"),
    "town": ("town", "நகரம்"),
    "subdivisions": ("sub-divisions", "உட்பிரிவுகள்"),
}


def _project_row_value(row: dict, key: str) -> str:
    if key == "ward":
        v = row.get("ward_number")
        return f"ward {v}" if v not in (None, "", "N/A") else "—"
    if key == "block":
        v = row.get("block_number")
        return f"block {v}" if v not in (None, "", "N/A") else "—"
    if key in ("district", "taluk", "town"):
        v = row.get(f"{key}_name")
        return str(v) if v not in (None, "", "N/A") else "—"
    if key == "submission_channel":
        v = row.get("submission_channel")
        return _CHANNEL_LABELS.get(v, str(v)) if v else "—"
    if key == "fee_amount":
        v = row.get("fee_amount")
        return _money(float(v)) if v not in (None, "") else "—"
    if key == "application_type":
        return row.get("type") or "—"
    v = row.get(key)
    return str(v) if v not in (None, "", "N/A") else "—"


def _project_field_answer(rows: list, keys, total: int, is_tamil: bool) -> str:
    """One or more fields for every row the officer was shown, as the same
    inline `<table class='data-table'>` every other listing in the app
    renders -- a bullet list here used to be the one place in the whole
    pipeline that answered a field question as plain text instead of a
    table, and it could not show more than one field at a time either.

    `keys` is a single field key (backward-compatible with the one-field
    call sites), a list of them ("application no with type and status"), or
    an empty list -- "application no only" -- meaning just the row key, no
    other column.
    """
    if isinstance(keys, str):
        keys = [keys]
    if not rows:
        return ("அந்த விண்ணப்பங்கள் எதுவும் இப்போது படிக்கக் கிடைக்கவில்லை."
                if is_tamil else
                "None of those applications are available to read right now.")
    from html import escape as _esc
    headers = [(_PROJECT_LABELS.get(k, (k.replace("_", " "), k))[1 if is_tamil else 0]).title()
               for k in keys]
    head_html = ("<th>" + ("விண்ணப்ப எண்" if is_tamil else "Application No.") + "</th>"
                 + "".join(f"<th>{_esc(h)}</th>" for h in headers))
    body_html = []
    for r in rows:
        app_no = str(r.get("application_number") or "—")
        cell0 = (f"<td><a href='javascript:void(0)' class='app-table-link' "
                 f"onclick=\"window.handleAppClick('{_esc(app_no)}')\" "
                 f"style='color:#2563eb;text-decoration:underline;cursor:pointer;"
                 f"font-weight:600;'>{_esc(app_no)}</a></td>")
        cells = "".join(f"<td>{_esc(_project_row_value(r, k))}</td>" for k in keys)
        body_html.append(f"<tr>{cell0}{cells}</tr>")
    intro = (f"அந்த {len(rows)} விண்ணப்பங்கள்:" if is_tamil
             else f"Found {len(rows)} application(s):")
    # A column of dashes for IGRS Form 6 Number is data-correct for CSC /
    # citizen files (only a Sub-Registrar referral is ever given one) but
    # reads as broken without the reason, the same gap `_igrs_over_list_note`
    # closes for the yes/no phrasing of the same question.
    igrs_note = ""
    if "igrs_form6_number" in keys:
        with_igrs = sum(1 for r in rows if r.get("submission_channel") == "sub_registrar")
        if with_igrs < len(rows):
            if with_igrs == 0:
                igrs_note = (
                    (f"<div class='table-intro'>இந்த {len(rows)} விண்ணப்பங்களில் எதற்கும் "
                     f"IGRS படிவம் 6 எண் இல்லை — சார்பதிவாளர் பரிந்துரைக்கு மட்டுமே அந்த எண் "
                     f"வழங்கப்படும், எனவே '—' சரியான மதிப்பே.</div>") if is_tamil else
                    (f"<div class='table-intro'>None of these {len(rows)} applications has an "
                     f"IGRS Form 6 number recorded. Only a Sub-Registrar referral is given one, "
                     f"so '—' here is correct, not missing data.</div>"))
            elif is_tamil:
                igrs_note = (
                    f"<div class='table-intro'>{with_igrs} / {len(rows)} மட்டுமே "
                    f"IGRS படிவம் 6 எண் கொண்டுள்ளன — சார்பதிவாளர் பரிந்துரையாக "
                    f"வந்தவை மட்டும். மற்றவை (CSC / குடிமகன்) அந்த எண்ணைப் "
                    f"பெறுவதில்லை, எனவே '—' சரியான மதிப்பே.</div>")
            else:
                igrs_note = (
                    f"<div class='table-intro'>Only {with_igrs} of {len(rows)} carry an "
                    f"IGRS Form 6 number — the Sub-Registrar referrals. The rest "
                    f"(CSC / citizen) are never given one, so '—' there is correct, "
                    f"not missing data.</div>")
    return (f"{igrs_note}<div class='table-intro'>{intro}</div>"
            f"<table class='data-table'><thead><tr>{head_html}</tr></thead>"
            f"<tbody>{''.join(body_html)}</tbody></table>")


def _scoped_list_answer(sd: dict, resolution, message: str, language: str) -> str:
    """Answer an aggregate/refine follow-up over the exact rows already shown.

    Every number here comes from `get_applications_by_numbers`, which re-read
    those applications under the officer's jurisdiction -- nothing is counted
    from the previous answer's text.
    """
    is_tamil = language in ("ta", "tanglish")
    rows = sd.get("applications") or []
    count = sd.get("count", len(rows))
    total = len(resolution.application_numbers)
    # Spelling-corrected, because every branch below is a keyword match and the
    # officer has already been understood well enough to get here. "which one is
    # the oldst" reached this function as a resolved follow-up, missed the
    # `\boldest\b` test by one letter, and fell through to the generic count --
    # which answered "20 of those 20 CSC application(s) are matching", a
    # sentence that is not an answer to any question.
    msg_lower = fctx.correct_spelling(message or "").lower()

    # "comparing extent" / "which has the biggest area" -- extent/area is not
    # a column `get_applications_by_numbers` carries for a register-wide
    # listing (it lives only in each service code's own record: the F-Line /
    # TSLR / sub-division extract, read per application). Without this the
    # generic fallback at the end of this function said only "There are N
    # application(s) in that list" -- true, but not an answer to what was
    # asked, and gave no hint that the figure simply isn't tracked at this
    # level.
    if re.search(r"\bextent\b|\barea\b|\bசதுர\b|\bபரப்பளவு\b", msg_lower) and not re.search(r"\bjurisdiction\b|\bward\b|\bblock\b", msg_lower):
        return (f"அந்த {total} விண்ணப்பங்களுக்கும் பரப்பளவு இங்கு பட்டியலில் இல்லை -- "
                f"ஒவ்வொரு விண்ணப்பத்தின் சொந்த பதிவில் (F-Line / TSLR / உட்பிரிவு) உள்ளது. "
                f"ஒரு விண்ணப்ப எண்ணைக் கொடுத்தால் அதன் பரப்பளவைக் காட்டுவேன்." if is_tamil
                else f"Extent/area is not a column this register-wide listing carries for any of "
                     f"those {total} application(s) -- it lives in each one's own record (F-Line / "
                     f"TSLR / sub-division). Name one application number and I can give its extent.")

    # "sort by date" / "newest first" -- the same rows, re-ordered (the rows in
    # `sd` already are). Say what order they are in; a bare count is no answer.
    if _SORT_RESET.get() and sd.get("sort_by"):
        return (f"அந்த {count} விண்ணப்பங்கள் அசல் வரிசையில் — கீழே காட்டப்பட்டுள்ளன." if is_tamil
                else f"Sorting removed -- those {count} application(s) are back in their original order "
                     f"(oldest submission first) -- shown below.")
    if sd.get("sort_by") and fctx.is_sort_fragment(message):
        _field_en = {"submission_date": "submission date", "application_number": "application number",
                     "status": "status", "application_type": "type", "priority": "priority",
                     "ward_number": "ward", "block_number": "block", "survey_no": "survey number",
                     "fee_amount": "fee", "applicant_name": "applicant name"}.get(sd["sort_by"], "submission date")
        _field_ta = {"submission_date": "சமர்ப்பித்த தேதி", "application_number": "விண்ணப்ப எண்",
                     "status": "நிலை", "application_type": "வகை", "priority": "முன்னுரிமை",
                     "ward_number": "வார்டு", "block_number": "பிளாக்", "survey_no": "சர்வே எண்",
                     "fee_amount": "கட்டணம்", "applicant_name": "விண்ணப்பதாரர் பெயர்"}.get(sd["sort_by"], "தேதி")
        _desc = (sd.get("sort_dir") or "asc") == "desc"
        if is_tamil:
            return (f"அந்த {count} விண்ணப்பங்கள் {_field_ta} படி "
                    f"{'இறங்கு' if _desc else 'ஏறு'} வரிசையில் — கீழே காட்டப்பட்டுள்ளன.")
        _dir_en = (("newest first" if sd["sort_by"] == "submission_date" else "descending") if _desc
                   else ("oldest first" if sd["sort_by"] == "submission_date" else "ascending"))
        return f"Those {count} application(s) sorted by {_field_en}, {_dir_en} — shown below."

    # "order columns as district, taluk, ward, block" / "show district first"
    # -- the same {count} rows, just with their location columns in the order
    # asked for. `_build_table_data` reads the order back off this same
    # `message` via `_requested_loc_column_order()` and sends it to the
    # frontend as `loc_column_order`; this only has to say what happened,
    # since the rows themselves are unchanged.
    if fctx.is_column_reorder_fragment(message):
        return (f"அந்த {count} விண்ணப்பங்கள் — புதிய நெடுவரிசை வரிசையில் கீழே காட்டப்பட்டுள்ளன." if is_tamil
                else f"Those {count} application(s) — columns reordered, shown below.")

    # "remove row 2" / "exclude 2026/.../001167" -- a row was just dropped
    # from the carried list. The status/type aggregate logic further down
    # exists for "show only NISD" ("how many/which are now in view"), and it
    # tried to answer this the same way -- filtering the rows by a status or
    # type the officer never named -- which is what turned "remove row 2"
    # over a 2-row list into "None of those 0 application(s) are pending"
    # for a table that still had one row left. A plain count of what is left
    # says exactly what happened and nothing it cannot verify.
    _dropped_n = getattr(resolution, "rows_dropped", 0)
    if _dropped_n:
        if not rows:
            return ("அது நீக்கப்பட்டது; பட்டியலில் வேறு விண்ணப்பங்கள் இல்லை."
                     if is_tamil else
                     f"Removed {_dropped_n}. Nothing is left in the list.")
        if is_tamil:
            return f"{_dropped_n} நீக்கப்பட்டது — {total} மீதமுள்ளன."
        return (f"Removed {_dropped_n}, {total} left"
                + (":" if total else "."))

    # "show more applications" / "less kami" -- there is no row cap on any
    # listing in this app, so "more" has nothing left to reveal and "less"
    # named no criterion to narrow by. Said plainly, over the re-read rows,
    # rather than falling through to the document-search fallback, which
    # answered "No documents found with the query 'less'" -- a
    # developer-facing sentence for a request that named an application list.
    if getattr(resolution, "vague_count", False):
        if is_tamil:
            return f"இது முழுமையான பட்டியல் — {total} விண்ணப்பங்கள். எத்தனை வேண்டும் எனக் கூறவும், அல்லது நிலை/வகை/வார்டு வாரியாக குறுக்கவும்."
        return (f"That's the complete list — {total} application(s). Say how "
                f"many you want (e.g. \"first 5\"), or narrow it by status, "
                f"type or ward.")

    # A field question that landed on SEVERAL rows ("what is the applicant
    # name?" after a two-row listing). `resolve()` used to ask which one was
    # meant; it answers all of them now, and this renders the column.
    _per_row = getattr(resolution, "per_row_field", None)
    if _per_row:
        return _project_field_answer(rows, _per_row, total, is_tamil)

    # "not along application no" / "without the survey number" -- application
    # number, survey number and sub-division number identify the ROW; a table
    # that dropped one would no longer say which application a line is about.
    # Refused in words, checked before field_projections() ever gets to
    # narrow or drop a column, so the request is neither silently carried out
    # (a table with no way to tell rows apart) nor silently ignored (the
    # table comes back unchanged with no explanation).
    _refused_field = fctx.excluded_mandatory_field(message)
    if _refused_field:
        if is_tamil:
            return (f"{_refused_field} விண்ணப்பத்தை அடையாளம் காட்டுவதால், அதை "
                    f"அட்டவணையிலிருந்து நீக்க முடியாது -- எப்போதும் காட்டப்படும்.")
        return (f"The {_refused_field} identifies the row, so it can't be left "
                f"out of the table -- it is always shown.")

    # "their wards", "list the CAN numbers", "application no with type and
    # status", "application no only" -- one or more fields for each carried
    # row, from the re-read data. `None` means this was not a per-row
    # projection follow-up at all (falls through below); `[]` means
    # explicitly the row key alone.
    # "along" builds on what the OFFICER was just shown, not always the plain
    # listing's columns -- "along district" then "along taluk" is meant to add
    # up, not each restart from the bare table. `resolution.context` is the
    # prior turn's own context object, mutated below so the next hop sees it.
    _prior_ctx = getattr(resolution, "context", None)
    _along_base = (_prior_ctx.filters.get("projected_fields")
                   if _prior_ctx and _prior_ctx.filters else None)
    _projs = fctx.field_projections(message, along_base=_along_base)
    if _projs is not None:
        if _prior_ctx is not None:
            _prior_ctx.filters["projected_fields"] = _projs
        return _project_field_answer(rows, _projs, total, is_tamil)

    # "show their details" over a set too large to render. `resolve` hands those
    # here as an ordinary aggregate, and the generic count below would answer
    # "30 of those 30 application(s) are matching" -- a sentence that is not an
    # answer to the question asked. Say what is true and what to do instead.
    if fctx.wants_full_details(message) and total > fctx.MAX_PER_ROW_ANSWER:
        if is_tamil:
            return (f"அந்த {total} விண்ணப்பங்களின் முழு விவரங்களை ஒரே பதிலில் "
                    f"காட்ட முடியாது. \"முதல் 10\" எனக் கேளுங்கள், அல்லது ஒரு "
                    f"விண்ணப்ப எண்ணைத் தரவும்.")
        return (f"That is {total} applications — too many to show in full at "
                f"once. Ask for \"the first 10\", narrow the list (by status, "
                f"type or ward), or give one application number.")

    bits = []
    if resolution.status:
        bits.append(resolution.status.replace("_", " "))
    if resolution.application_type:
        bits.append(resolution.application_type)
    label = " ".join(bits)

    # "which is oldest / newest" -- settled on submission date, from the rows.
    # "which one is OLDER" (comparative, not superlative) asks the identical
    # question when the carried list has exactly two rows -- the natural
    # phrasing after "show details for A and B" -- so it shares the same
    # branch rather than falling through to the generic count below, which
    # answered nonsense like "2 of those 2 ... are matching".
    # "its latest recorded WORKFLOW EVENT" (after narrowing to one application)
    # is a question about that file's own workflow history, not about which
    # row in the carried list has the most recent SUBMISSION date -- but
    # "latest" alone matched the newest-superlative branch below regardless,
    # so a narrowed one-row list answered "the most recent of those 1 is X,
    # submitted <submission date>" to a question that never asked about
    # submission dates at all. Both superlative branches stand aside when the
    # message itself names the workflow/transition/event, leaving the
    # application-aware workflow handlers (reached later in the pipeline) to
    # answer instead.
    _WORKFLOW_EVENT_WORDS_RE = re.compile(r"\bworkflow\b|\btransition\b|\bworkflow\s+event\b", re.IGNORECASE)
    dated = [r for r in rows if r.get("submission_date")]
    if dated and not _WORKFLOW_EVENT_WORDS_RE.search(msg_lower) and re.search(
            r"\boldest\b|\bearliest\b|\bolder\b|\bpazhusu\b|\bpazha(?:i)?y(?:a|adhu|athu)\b|பழைய",
            msg_lower):
        pick = min(dated, key=lambda r: r["submission_date"])
        tied = [r["application_number"] for r in dated if r["submission_date"] == pick["submission_date"]]
        # Narrowing sd["applications"] to just `pick` here (so a later pronoun
        # "it" resolves to it) was tried and reverted: it made a CHAINED
        # superlative ("which is oldest?" then "what about the newest?") pick
        # the newest of the just-narrowed one-row set instead of the original
        # list, since both a pronoun and a second superlative read the same
        # saved context with no way to tell which one is wanted. Left as a
        # known gap: "it" after a pick still asks which application is meant,
        # which is a real (if less common) shortfall.
        if len(tied) > 1:
            return (f"{len(tied)} விண்ணப்பங்கள் ஒரே நாளில் ({pick['submission_date']}) சமர்ப்பிக்கப்பட்டன: "
                    f"{', '.join(tied)}." if is_tamil else
                    f"{len(tied)} of those {total} share the oldest submission date "
                    f"({pick['submission_date']}): {', '.join(tied)}.")
        return (f"{pick['application_number']} — சமர்ப்பிக்கப்பட்டது "
                f"{pick['submission_date']}." if is_tamil else
                f"The oldest of those {total} is {pick['application_number']}, "
                f"submitted {pick['submission_date']}.")
    if dated and not _WORKFLOW_EVENT_WORDS_RE.search(msg_lower) and re.search(
            r"\bnewest\b|\blatest\b|\bnewer\b|\bmore\s+recent\b|\bmost\s+recent\b"
            r"|\bputhusu\b|\bpudhusu\b|\bpu(?:th|dh)iy(?:a|adhu|athu)\b|சமீபத்திய|புதிய", msg_lower):
        pick = max(dated, key=lambda r: r["submission_date"])
        tied = [r["application_number"] for r in dated if r["submission_date"] == pick["submission_date"]]
        # See the oldest branch above: narrowing here was tried and reverted
        # for the same chained-superlative reason.
        if len(tied) > 1:
            return (f"{len(tied)} விண்ணப்பங்கள் ஒரே நாளில் ({pick['submission_date']}) சமர்ப்பிக்கப்பட்டன: "
                    f"{', '.join(tied)}." if is_tamil else
                    f"{len(tied)} of those {total} share the most recent submission date "
                    f"({pick['submission_date']}): {', '.join(tied)}.")
        return (f"{pick['application_number']} — சமர்ப்பிக்கப்பட்டது "
                f"{pick['submission_date']}." if is_tamil else
                f"The most recent of those {total} is {pick['application_number']}, "
                f"submitted {pick['submission_date']}.")

    # "what is the total fee" over the listed set.
    if re.search(r"\bfee\b|\bamount\b|\bcharge\b|\bkattanam\b|கட்டண", msg_lower):
        with_fee = [r for r in rows if r.get("fee_amount") not in (None, "")]
        total_fee = sum(float(r["fee_amount"]) for r in with_fee)
        if is_tamil:
            return (f"அந்த {total} விண்ணப்பங்களில் {len(with_fee)}-க்கு கட்டணப் "
                    f"பதிவு உள்ளது; மொத்தம் {_money(total_fee)}.")
        return (f"{_money(total_fee)} across {len(with_fee)} of those {total} "
                f"application(s) that carry a fee record.")

    # "how many are not approved?" / "how many are not ISD?" -- a negated
    # status/type. `resolution.status` / `.application_type` are never set
    # for these (see `followup_context.refinement`'s own docstring), so
    # without this the generic count branch below fell through with no
    # filter at all and answered "3 of those 3 ... are matching" -- the
    # whole carried set, presented as if it answered a question about the
    # ones that are NOT that status/type. `status_excluded` /
    # `type_excluded` carry the negated value through, so the count here is
    # the actual complement, verified from the same re-read rows.
    _status_excl = getattr(resolution, "status_excluded", None)
    _type_excl = getattr(resolution, "type_excluded", None)
    if _status_excl or _type_excl:
        if _status_excl:
            _kept = [r for r in rows if (r.get("status") or "").lower() != _status_excl]
            _word = _status_excl
        else:
            _kept = [r for r in rows if (r.get("type") or "") != _type_excl]
            _word = _type_excl
        n = len(_kept)
        # The text below answers "N of those TOTAL are not X" correctly, but
        # `sd["applications"]` (mutated here, since `sd` is the same dict the
        # caller saves as follow-up context) used to stay the full unfiltered
        # TOTAL rows -- so a THIRD turn ("which is oldest?") silently
        # re-expanded back to the whole carried list and could name a file of
        # the very type/status just excluded as the answer.
        sd["applications"] = _kept
        sd["count"] = n
        if is_tamil:
            if n == 0:
                return f"அந்த {total} விண்ணப்பங்கள் அனைத்தும் {_word}."
            return f"அந்த {total} விண்ணப்பங்களில் {n} {_word} அல்லாதவை."
        verb = "is" if n == 1 else "are"
        if n == 0:
            return f"All {total} of those application(s) are {_word}."
        return f"{n} of those {total} application(s) {verb} not {_word}."

    # "how many of them are from CSC?" / "show only the Sub-Registrar ones" --
    # the submission channel is counted over the carried rows here, because
    # get_applications_by_numbers() takes no channel filter. `submission_channel`
    # is on every row, so this is as verified as the status / type counts.
    # Without this branch a channel follow-up matched no status/type word, fell
    # through to the generic count below and answered "57 of those 57 ... are
    # matching" -- the whole carried set, relabelled, when only 25 were CSC.
    channel = getattr(resolution, "submission_channel", None)
    if channel:
        n = sum(1 for r in rows if (r.get("submission_channel") or "") == channel)
        _refine = resolution.kind == fctx.FOLLOWUP_LIST_REFINE
        if _refine:
            # A REFINE ("from sro", "only sro") narrows the list, not just
            # states a count -- without this, the sentence said "N of those
            # M ... shown below" but the table underneath (and the context
            # carried into the NEXT follow-up) still held all M unfiltered
            # rows, so "from sro" -> "from isd" silently dropped the SRO scope
            # and answered ISD over the whole original set instead of over
            # the SRO subset the officer had just narrowed to.
            _matched = [r for r in rows if (r.get("submission_channel") or "") == channel]
            sd["applications"] = _matched
            sd["count"] = len(_matched)
            rows = _matched
        if is_tamil:
            _ta_ch = {"CSC": "CSC / சேவை மையம்",
                      "sub_registrar": "சார்-பதிவாளர்",
                      "citizen": "குடிமகன்"}.get(channel, channel)
            if n == 0:
                return (f"அந்த {total} விண்ணப்பங்களில் {_ta_ch} வழியாக "
                        f"வந்தவை எதுவும் இல்லை.")
            return (f"அந்த {total} விண்ணப்பங்களில் {n} {_ta_ch} வழியாக வந்தவை"
                    + (" — கீழே காட்டப்பட்டுள்ளன." if _refine else "."))
        _phrase = {"CSC": "from CSC",
                   "sub_registrar": "Sub-Registrar referrals",
                   "citizen": "filed by the citizen"}.get(channel, f"from {channel}")
        verb = "is" if n == 1 else "are"
        if n == 0:
            return f"None of those {total} application(s) are {_phrase}."
        return (f"{n} of those {total} application(s) {verb} {_phrase}"
                + (" — shown below." if _refine else "."))

    # "which one is not scheduled?" / "which ones are overdue?" -- the officer
    # wants the applications, so name them; a bare "1 of those 1 is matching"
    # states nothing. The state is read from the re-queried rows.
    _low_msg = (message or "").lower()
    if re.search(r"\bwhich\b|எது|எவை|\bedhu\b", _low_msg):
        _picked, _what_en, _what_ta = None, "", ""
        if re.search(r"\b(?:not\s+(?:yet\s+)?scheduled|unscheduled|un-scheduled|"
                     r"without\s+(?:a\s+)?(?:field\s+)?visit)\b", _low_msg):
            _picked = [r for r in rows if r.get("field_visit_status") == "unscheduled"]
            _what_en, _what_ta = "awaiting a field-visit date", "கள ஆய்வு தேதிக்காக காத்திருக்கின்றன"
        elif re.search(r"\bscheduled\b", _low_msg):
            _picked = [r for r in rows if r.get("field_visit_status") == "scheduled"]
            _what_en, _what_ta = "scheduled for a field visit", "கள ஆய்வுக்கு திட்டமிடப்பட்டுள்ளன"
        elif re.search(r"\boverdue\b|தாமத", _low_msg):
            _picked = [r for r in rows if r.get("is_overdue")]
            _what_en, _what_ta = "overdue", "காலதாமதமானவை"
        if _picked is not None:
            nums = ", ".join(r["application_number"] for r in _picked)
            if is_tamil:
                return (f"அந்த {total} விண்ணப்பங்களில் {_what_ta}: {nums}." if _picked
                        else f"அந்த {total} விண்ணப்பங்களில் எதுவும் {_what_ta} இல்லை.")
            if not _picked:
                return f"None of those {total} application(s) is {_what_en}."
            verb = "is" if len(_picked) == 1 else "are"
            return f"{len(_picked)} of those {total} application(s) {verb} {_what_en}: {nums}."

    # Otherwise it is a count over the shown set. A "show only ..." asks to see
    # them, so it is worded as a listing (the table rides along beside it); a
    # "how many ..." is worded as the count it asked for.
    if is_tamil:
        # Without a Tamil label this read as "அந்த 34 விண்ணப்பங்களில் 34 ." --
        # a number, a space and a full stop -- because `label` is built from
        # the English status/type words and is empty on a bare count.
        _ta_label = {"approved": "அங்கீகரிக்கப்பட்டவை",
                     "rejected": "நிராகரிக்கப்பட்டவை",
                     "pending": "நிலுவையில் உள்ளவை"}.get(resolution.status or "", label)
        if resolution.application_type:
            _ta_label = f"{resolution.application_type} {_ta_label}".strip() if _ta_label else f"{resolution.application_type} வகை"
        if count == 0:
            return (f"அந்த {total} விண்ணப்பங்களில் "
                    f"{_ta_label or 'பொருந்துபவை'} எதுவும் இல்லை.")
        if _ta_label:
            return f"அந்த {total} விண்ணப்பங்களில் {count} {_ta_label}."
        return f"அந்தப் பட்டியலில் {count} விண்ணப்பங்கள் உள்ளன."
    if count == 0:
        return (f"None of those {total} application(s) are "
                f"{label or 'a match'}.")
    scope_word = _carried_scope_label(
        rows,
        skip_type=bool(resolution.application_type),
        carried_channel=_carried_list_channel(getattr(resolution, "context", None)),
        narrowed=bool(resolution.status or resolution.application_type
                      or getattr(resolution, "submission_channel", None)),
    )
    of_those = f"of those {total} {scope_word} application(s)".replace("  ", " ")
    if resolution.kind == fctx.FOLLOWUP_LIST_REFINE:
        return (f"{count} {of_those} "
                f"{'is' if count == 1 else 'are'} {label or 'matching'} — "
                f"shown below.")
    if not label and not (resolution.status or resolution.application_type
                          or getattr(resolution, "submission_channel", None)):
        _scope = f"{scope_word} " if scope_word else ""
        return f"There {'is' if count == 1 else 'are'} {count} {_scope}application(s) in that list."
    return (f"{count} {of_those} "
            f"{'is' if count == 1 else 'are'} {label or 'matching'}.")


# Owner sub-fields of the natham-chitta owner row. A bare follow-up naming one
# of these ("what is his aadhaar?", "who is the father?", "the ownership
# share?") after a survey-ownership / joint-owner answer carries no owner
# keyword, so the rewritten "<q> survey 5" routes to survey_detail and the owner
# column is never reached -- see _apply_followup_resolution.
_OWNER_SUBFIELD_RE = re.compile(
    r"\b(?:father|relative|relation(?:ship)?|husband|wife|spouse|parent|"
    r"aadhaar|aadhar|gender|sex|ownership\s+share|share|co-?owner|"
    r"mobile|phone|contact\s+number|address|residence)\b"
    r"|தந்தை|கணவ|மனைவி|உறவு|ஆதார|பங்கு|பாலின|முகவரி|தொலைபேசி|கைபேசி"
    r"|thanthai|kanavan|manaivi|uravu|pangu|paalinam|mugavari|tholaipaesi",
    re.IGNORECASE,
)


def _asks_owner_subfield(message: str) -> bool:
    return bool(_OWNER_SUBFIELD_RE.search((message or "").lower()))


def _multi_details_message(numbers: list) -> str:
    """Rewrite "details of both" into the question that already works.

    "show details for A and B" is a shape `application_status` has handled
    since long before this layer existed: `extract_application_numbers` finds
    every number, each is read through `get_application_detail(officer=...)`,
    and the existing multi-application renderer prints one card per file. So
    the whole mechanism here is naming the files the officer was just shown --
    the same rewrite-and-let-the-handler-answer rule as
    `_apply_followup_resolution`, and no more of a grant than that one: every
    number is re-checked downstream against the officer's jurisdiction.
    """
    return "show details for " + " and ".join(numbers)


# Ordinal words to strip when a previous question is reused for a later row:
# "the name of the FIRST applicant" reused for row 2 must not still say first.
_ORDINAL_STRIP_RE = re.compile(
    r"\b(?:the\s+)?(?:first|second|third|fourth|fifth|sixth|seventh|eighth|"
    r"ninth|tenth|last|final|\d{1,3}(?:st|nd|rd|th))\b",
    re.IGNORECASE,
)


def _prev_field_question(prev_user_message: str) -> Optional[str]:
    """The previous question, ready to be re-asked about another row.

    Only when it actually named a field -- a bare ordinal after "show my
    applications" means the record, not a field of it. The previous question's
    own ordinal is removed so it does not fight the new one, and any
    application number it carried goes too, or the rewritten question would
    name two.
    """
    text = (prev_user_message or "").strip()
    if not text or not fctx.names_a_field(text):
        return None
    text = re.sub(r"\b\d{4}/\d{3,4}/\d{1,3}/\d+\b", " ", text)
    text = _ORDINAL_STRIP_RE.sub(" ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ?.,:;")
    # Left with nothing but filler ("what is the?"), it is no use as a question.
    return text if len(text.split()) >= 2 else None


def _apply_followup_resolution(message: str, resolution,
                               prev_user_message: str = "") -> str:
    """Rewrite a bare singular follow-up into a self-contained question.

    Appending the resolved application number is deliberately the whole
    mechanism: every downstream handler already knows how to answer a question
    that names its application, and every one of them re-fetches it through the
    officer-scoped query functions. Nothing here grants access -- it only says
    which record the officer meant.

    "field visit" is appended for a visit question so the field-visit answer
    fires: that phrase was in the *previous* turn, and without it "when was it
    scheduled?" fell through to the field map and was answered with the
    application's submission date.

    A survey-context follow-up ("who are the joint owners?", "what is the
    ownership share?" after a survey-ownership answer) carries no application
    number; the carried survey number is named instead, so `survey_owners` /
    `survey_detail` resolves it rather than asking the officer to retype it.
    """
    if resolution.application_number:
        num = resolution.application_number
        # The previous answer was about one side of a patta transfer. A bare
        # "what is their gender?" / "and the father's name?" after it must stay
        # on that party -- name it so the transfer-party pre-empt fires instead
        # of the field map answering about the applicant.
        _tp_ctx = getattr(resolution, "context", None)
        _tp_side = (_tp_ctx.filters or {}).get("transfer_party") if _tp_ctx else None
        if _tp_side and not _asks_transfer_party(message):
            _lead = "previous owner" if _tp_side == "previous" else "new owner"
            return f"{_lead} {message.strip()} {num}"
        # The previous answer was a registration detail ("registered at Melur
        # on …"). A *bare* "when?" / "where?" / "and why?" after it means the
        # deed, not the filing date -- rewrite to the canonical question. Only
        # when the fragment is purely interrogative; "and the order remarks?"
        # names its own field and is left to the field map.
        _reg_kind = (_tp_ctx.filters or {}).get("reg_detail") if _tp_ctx else None
        if _reg_kind and not _asked_registration_detail(message.lower()):
            _bare = re.match(
                r"^(?:and\s+|so\s+|ok\s+|but\s+)?(?:the\s+)?(?:what\s+about\s+)?"
                r"(when|where|why|what\s+date|which\s+date|which\s+place|"
                r"எப்போ\w*|எங்க\w*|ஏன்|eppo\w*|enga\w*|yen)\s*\??\s*$",
                message.strip().lower())
            if _bare:
                w = _bare.group(1)
                if w.startswith(("when", "what date", "which date", "எப்போ", "eppo")):
                    return f"when was the deed for {num} registered"
                if w.startswith(("where", "which place", "எங்க", "enga")):
                    return f"where was the deed for {num} registered"
                if w.startswith(("why", "ஏன்", "yen")):
                    return f"why was {num} transferred"
                _canon = {"place": f"where was the deed for {num} registered",
                          "date": f"when was the deed for {num} registered",
                          "reason": f"why was {num} transferred",
                          "docnum": f"registration document number of {num}"}
                return _canon.get(_reg_kind, f"{message.strip()} {num}")
        if fctx.is_what_is_it(message):
            return f"details of {num}"
        if resolution.about_visit and "field visit" not in message.lower():
            return f"{message.strip()} field visit {num}"
        # A bare positional reference -- "the 2nd one", "what abt the first
        # one" -- names no field: the officer wants that record. Concatenating
        # the interrogative fragment ("what abt the 2nd one? <num>") makes
        # application_status read it as a pointed field question and open with
        # "I could not find that particular detail". Rewrite it to a plain
        # request instead. Only when the message carries none of the field /
        # event cues, so "what is the area of the 2nd one?" still keeps its
        # field word and is answered from the field map.
        if fctx.mentions_ordinal(message) and not fctx.names_a_field(message):
            # ...unless the question BEFORE it named one. "what is the name of
            # the first applicant" followed by a bare "second" is still asking
            # for a name -- the officer is moving down the list, not changing
            # the question. It was answered with the whole record instead,
            # which buries the one field they asked for in twenty they did not.
            # The previous question's words are reused with the new number, and
            # its own ordinal stripped so two do not collide.
            _prev_field_q = _prev_field_question(prev_user_message)
            if _prev_field_q:
                return f"{_prev_field_q} {num}"
            # "display first row in field visit" wants that row's VISIT, not its card
            if resolution.about_visit:
                return f"field visit of {num}"
            return f"details of {num}"
        return f"{message.strip()} {num}"

    ctx = getattr(resolution, "context", None)
    if (resolution.entity == fctx.ENTITY_SURVEY and ctx and ctx.survey_numbers
            and not re.search(r"survey\s*(?:no\.?|number)?\s*\d|கணக்கெண்|சர்வே\s*\d",
                              message.lower())):
        base = message.strip()
        # The previous answer was about this survey's owners (ctx.intent). A bare
        # owner sub-field follow-up carries no owner keyword, so "<q> survey 5"
        # would route to survey_detail and never reach the owner columns. Name
        # the owner too -- parse_intent then lands on survey_owners and
        # _owner_detail_line surfaces the field.
        if (ctx.intent in ("survey_owners", "joint_owner_check")
                and _asks_owner_subfield(message)
                and not re.search(r"\bowners?\b|\bownership\b|உரிமையாளர்",
                                  message.lower())):
            base = f"{base} owner"
        return f"{base} survey {ctx.survey_numbers[0]}"
    return message
