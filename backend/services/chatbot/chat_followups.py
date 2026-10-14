"""
Follow-up/conversation-context resolution: recall detection, acronym/fullform lookups, recent-answer/listing context, geo-scope, extract-app-number-from-context, the followup_context loading and list-carry-forward machinery.

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
from .chat_response_formatting import (
    _bare_type_word,
    _last_subject_message,
    _last_user_message,
    _named_app_type,
    _named_lifecycle_status,
    _named_periods,
    _parse_previous_answer,
    _resolve_prev_intent,
    _strip_tags,
)
from .chat_shared import (
    _ADD_COLUMNS_RE,
    _ANY_APP_NUMBER_RE,
    _DISTRICT_NAME_RE,
    _DISTRICT_NAME_TA_RE,
    _FULLFORMS,
    _FULLFORM_BARE_RE,
    _FV_STATUS_FRAGMENT_RE,
    _LIFECYCLE_STATUS_RE,
    _LIST_FOLLOWUP_INTENTS,
    _OPINION_RE,
    _OWN_SUBJECT_RE,
    _SORT_OVERRIDE,
    _build_jurisdiction_summary,
    _clear_info_topic,
    _cm_strong,
    _contentless_message,
    _corpus_blob,
    _fullform_term,
    _has_application_back_reference,
    _has_domain_evidence,
    _officer_turns,
    _parse_negation,
    _tokens,
)
from .chat_smalltalk import (
    _igrs_can_rule_answer,
    _igrs_can_rule_topic,
    _is_capability_question,
    _is_mutation_request,
    _is_out_of_scope,
    _scrub_llm_answer,
    _special_scope_kind,
    _unknown_message_kind,
)
from backend.utils.logger import get_logger

logger = get_logger(__name__)




# "workflow history" / "timeline" asks for the stage-by-stage log. The bare word
# "workflow" does NOT: "what is the current workflow state?" is a question about
# the application's present stage, and treating it as a history request replaced
# the application's fields with a history list, so every field answered "N/A"
# even though the database held the values.
_WORKFLOW_HISTORY_RE = re.compile(
    r'\btimeline\b|\bhistory\b|\bworkflow\s+(?:history|log|trail|timeline)\b'
    r'|\b(?:who|which\s+officer)\s+(?:acted|processed|handled|worked|forwarded|actioned)\b'
    r'|\ball\s+(?:the\s+)?(?:steps|hops|actions|stages)\b'
    # the log asked other ways: "what happened to X", "what action was taken", "who updated it",
    # "which stage did it move from and to", "latest workflow action", and slips of "workflow"
    r'|\bshow\s+(?:the\s+|me\s+)?wo?rk\s*fl\w*\b|\bwo?rk\s*fl\w*\s+(?:kaatunga|kaattu|kattu)\b'
    r'|\bwhat\s+happen\w*\s+to\b|\b(?:what|which)\s+actio\w*\s+(?:was|were|has\s+been|have\s+been)\s+taken\b'
    r'|\bactio\w*\s+(?:taken|eduthanga)\b|\bwho\s+updat\w*\s+(?:the\s+)?wo?rk\s*fl\w*\b'
    r'|\bmove[ds]?\s+from\s+and\s+to\b|\b(?:latest|last|recent)\s+wo?rk\s*fl\w*\s+actio\w*\b'
    # Tamil: \b word-boundaries do not work around Tamil combining vowel signs,
    # so match these as plain substrings.
    r'|வரலாறு|பணிப்பாய்வு\s*வரலாறு|படிகள்|வொர்க்ஃப்ளோ|எடுக்கப்பட்டது',
    re.IGNORECASE,
)


def _wants_workflow_history(message: str) -> bool:
    """True only when the officer asked for the stage-by-stage log, not the current stage."""
    return bool(_WORKFLOW_HISTORY_RE.search(message or ""))


# A request to see the record ("show application X", "give me the details") versus
# a question about one particular attribute ("what is the SLA deadline for X?").
# Named distinctly from the _DETAIL_REQUEST_RE above (a fuller-explanation
# follow-up, "explain in detail") -- the two used to share a name, so this one
# silently overwrote that one at import time and every phrase in
# _DETAIL_REQUEST_PHRASES other than what _is_detail_request's own typo-match
# fallback happened to also cover ("explain", "elaborate", "detailed",
# "detail") was dead: "briefly", "describe", "tell me more", "in depth" and
# the rest matched nothing at all.
_SHOW_RECORD_RE = re.compile(
    r'\b(?:show|display|give|get|fetch|open|view|tell)\b.*\b(?:details?|record|application|info|information)\b'
    r'|\bdetails?\s+(?:of|for)\b|\babout\b',
    re.IGNORECASE,
)
# `\b` is unreliable around Tamil: a word ending in a virama-marked bare
# consonant (யார்/எப்போது's trailing ் combining mark is not `\w`) never closes
# the boundary, so `\bயார்\b` / `\bஎப்போது\b` matched no input at all --
# neither "யார் இது" nor "இது யார்". Lookaround against the Tamil block
# (஀-௿) is a boundary that actually works both ways: it still refuses to
# match a stem inside a longer word (என்ன inside என்னிடம்).
_TA_NB, _TA_NA = r'(?<![஀-௿])', r'(?![஀-௿])'
_QUESTION_RE = re.compile(
    r'\b(?:what|which|when|where|who|whose|how|why)\b'
    rf'|{_TA_NB}என்ன{_TA_NA}|{_TA_NB}எந்த{_TA_NA}|{_TA_NB}யார்{_TA_NA}|{_TA_NB}எப்போது{_TA_NA}',
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
    if _SHOW_RECORD_RE.search(message):
        return False
    return bool(_QUESTION_RE.search(message))


# ── The LLM may not invent a number ─────────────────────────────────────────
# Every figure in an answer is supposed to come from the database, but the two
# LLM paths (the agent's answer pass and the plain RAG prompt) are handed the
# query result as context and can still paraphrase it wrongly -- a transposed
# sequence number in an application number, or a total that disagrees with the
# count the query returned. Both are read as fact by the officer, so both are
# checked against the structured data after the model has written, and neither
# check costs a database round trip.


def _verified_app_numbers(structured_data) -> set:
    """Every application number the query result actually carries.

    The whole payload is walked rather than a fixed list of keys: the wider the
    set of numbers known to be real, the fewer true numbers are mistaken for
    invented ones. An empty set means the turn fetched no application records,
    and the scrub below then stands aside -- there is nothing to check against.
    """
    known: set = set()
    stack = [structured_data]
    visited = 0
    while stack and visited < 20000:
        node = stack.pop()
        visited += 1
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, (list, tuple, set)):
            stack.extend(node)
        elif isinstance(node, str):
            for m in _ANY_APP_NUMBER_RE.finditer(node):
                known.add(m.group(0).upper())
    return known


_UNVERIFIED_APP_MARKER = "[an application number I could not verify]"


def _scrub_unverified_app_numbers(text: str, known: set) -> Tuple[str, List[str]]:
    """Replace application numbers the query result does not contain.

    Removing the number is deliberate rather than correcting it: there is no way
    to tell which real application a wrong number was meant to be, and guessing
    one would be the same failure again.
    """
    if not text or not known:
        return text, []
    removed: List[str] = []

    def _sub(m):
        token = m.group(0)
        if token.upper() in known:
            return token
        if token not in removed:
            removed.append(token)
        return _UNVERIFIED_APP_MARKER

    return _ANY_APP_NUMBER_RE.sub(_sub, text), removed


# "you have 7 applications", "there are 12 pending applications", "found 3
# overdue applications" -- a claim about the SIZE of the result. Only a total
# claim is rewritten; a bare number elsewhere in the answer (a year, an area, a
# day count) is left alone.
_COUNT_CLAIM_RE = re.compile(
    r"(?P<lead>\b(?:you\s+(?:currently\s+)?have|you'?ve\s+got|there\s+are|there\s+is|"
    r"i\s+found|we\s+found|found|a\s+total\s+of|total\s+of|totall?ing)\s+)"
    r"(?P<num>\d{1,6})"
    r"(?P<tail>(?:\s+[A-Za-z][\w-]*){0,3}?\s+applications?\b)",
    re.IGNORECASE,
)


def _reconcile_count_claims(text: str, structured_data) -> Tuple[str, bool]:
    """Make a stated total agree with the count the query returned.

    A sentence that already states the database figure somewhere is left
    untouched, which is what keeps a legitimate part-of-the-whole sentence --
    "3 of your 9 applications are approved" -- from having its 3 rewritten to 9.
    """
    if not text or not isinstance(structured_data, dict):
        return text, False
    count = structured_data.get("count")
    if not isinstance(count, int) or isinstance(count, bool):
        return text, False
    parts = re.split(r"([.!?\n]+\s*)", text)
    changed = False
    for i, part in enumerate(parts):
        if i % 2 or not part:
            continue
        if str(count) in re.findall(r"\d{1,6}", part):
            continue
        rewritten = _COUNT_CLAIM_RE.sub(
            lambda m: f"{m.group('lead')}{count}{m.group('tail')}", part)
        if rewritten != part:
            parts[i] = rewritten
            changed = True
    return "".join(parts), changed


_AMOUNT_RE = re.compile(r"(?:Rs\.?|INR|\u20b9)\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)


def _scrub_unverified_amounts(text: str, structured_data):
    """Drop any sentence stating a rupee amount the query result does not carry.
    A fee is a register value; the model recalls one from training ("Rs. 400")."""
    if not _AMOUNT_RE.search(text or ""):
        return text, []
    try:
        payload = json.dumps(structured_data, default=str, ensure_ascii=False) if structured_data else ""
    except Exception:
        payload = str(structured_data or "")
    known = set()
    for n in re.findall(r"\d[\d,]*(?:\.\d+)?", payload):
        try:
            known.add(float(n.replace(",", "")))
        except ValueError:
            pass
    removed, kept = [], []
    for sent in re.split(r"(?<!Rs.)(?<!INR.)(?<=[.!?])\s+|\n+", text):
        bad = [m for m in _AMOUNT_RE.findall(sent) if float(m.replace(",", "")) not in known]
        if bad:
            removed += bad
        elif sent.strip():
            kept.append(sent)
    return " ".join(kept), removed


_TIME_URL_RE = re.compile(
    r"\b\d{1,2}(?::\d{2})?\s?(?:AM|PM|a\.m\.|p\.m\.)|\b\d{1,2}:\d{2}\b|https?://\S+|www\.\S+",
    re.IGNORECASE)


def _scrub_unverified_specifics(text: str, structured_data):
    """Drop sentences stating a clock time or web address that is in neither the
    query result nor the department documents ("office hours are 9:00 AM to 5:00
    PM" -- said with confidence, recorded nowhere)."""
    if not _TIME_URL_RE.search(text or ""):
        return text, []
    try:
        payload = json.dumps(structured_data, default=str, ensure_ascii=False).lower() if structured_data else ""
    except Exception:
        payload = ""
    blob = _corpus_blob()
    removed, kept = [], []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
        bad = [m.group(0) for m in _TIME_URL_RE.finditer(sent)
               if m.group(0).lower() not in payload and m.group(0).lower() not in blob]
        if bad:
            removed += bad
        elif sent.strip():
            kept.append(sent)
    return " ".join(kept), removed
_FULLFORM_TERM_RE = re.compile(
    r"\b(igrs|sro|csc|can|sla|sis|sd|dis|dsc|zdt|isd|nisd|tahsildar|fmb)\b", re.IGNORECASE)


def _fullform_followup(message: str, chat_history) -> str:
    """"full form" with no term after an answer about IGRS / SRO / CSC ... -> "full form of IGRS"."""
    if not _FULLFORM_BARE_RE.match((message or "").strip()):
        return message
    for m in reversed(chat_history or []):
        if m.get("role") == "user":
            t = _FULLFORM_TERM_RE.findall(m.get("content") or "")
            if t:
                return f"full form of {t[-1]}"
    return message


# "full form of irgs" -> "IGRS = Inspector General of Registration and
# Stamps." -> "in detailed" -- a one-line fullform answer saves NO follow-up
# context at all (it is static text, nothing to carry), so this fragment had
# nothing of its own to resolve against. It either reached back to whatever
# application LIST happened to be on screen from several turns earlier and
# answered with THAT record's details, or -- with no list in view either --
# fell to general_query, where the LLM invented an unrelated answer about
# application workflow stages. Neither is a detail of IGRS.
_DETAIL_FOLLOWUP_RE = re.compile(
    r"^(?:and\s+|so\s+|ok\s+|please\s+|pls\s+)*(?:(?:in|with)\s+)?"
    r"(?:more\s+)?detail(?:ed|s)?\s*[?.!]*$"
    r"|^(?:explain|elaborate|tell\s+me)\s+(?:it\s+|this\s+|that\s+)?more\s*[?.!]*$"
    r"|^more\s+(?:info|information|details?)\s*[?.!]*$"
    r"|^in\s+depth\s*[?.!]*$",
    re.IGNORECASE)
# Only these three fullform terms have a real, verified deterministic article
# behind the one-line definition (from land_rules.txt, via the SRO/IGRS/CAN
# rule engine). Anything else says so honestly rather than inventing length.
_FULLFORM_DETAIL_TOPIC = {"igrs": "igrs_who", "sro": "sro_what", "can": "can_what"}


def _fullform_detail_term(message: str, chat_history):
    """The acronym a bare "in detail" follow-up means, from the most recent
    "full form of X" exchange in the history -- or None when this message is
    not that shape at all.

    Must be the actual "full form of X" / "what does X stand for" SHAPE
    (`_fullform_term`, via `_FULLFORM_ASK_RE`), not merely a message that
    happens to CONTAIN the acronym as a token. "What is ISD?" names "ISD" too,
    but it is a `service_code_lookup` question, not a fullform ask -- that
    richer handler already has its own full, documented detailed answer
    (`describe_service_code(..., brief=False)`), and "in detailed" right
    after it was being hijacked here first, well before intent routing ever
    ran, and answered with this module's terse one-liner instead.
    """
    if not _DETAIL_FOLLOWUP_RE.match((message or "").strip()):
        return None
    for m in reversed(chat_history or []):
        if m.get("role") != "user":
            continue
        # The stored turn keeps what the officer actually typed ("full form
        # of irgs"), typos and all -- the correction that let the ORIGINAL
        # turn answer correctly only ever touched that turn's own working
        # `message` variable, never the history. Re-applying the same fixer
        # here is what let this function find "igrs" in "irgs" at all.
        _hist_text = _fragment_typo_fix(m.get("content") or "") or (m.get("content") or "")
        term = _fullform_term(_hist_text)
        if term:
            return term
        # The nearest user turn was not a fullform ask (whatever else it
        # named) -- this "in detail" belongs to whatever THAT turn actually
        # was, not to some earlier fullform question several turns back.
        return None
    return None


def _fullform_detail_reply(term: str, language: str) -> str:
    topic = _FULLFORM_DETAIL_TOPIC.get(term)
    if topic:
        return _igrs_can_rule_answer(topic, language)
    is_tamil = language in ("ta", "tanglish")
    full = _FULLFORMS.get(term)
    short = full[1 if is_tamil else 0] if full else ""
    if is_tamil:
        return (f"{short} இதற்கு மேல் விரிவான விளக்கம் SIS ஆவணங்களில் இல்லை.")
    return f"{short} I don't have a longer explanation of it beyond that in the SIS documents."


def _pipes_to_table(text: str) -> str:
    """Rows a model wrote as `A | B | C` lines (or a markdown table) become a real table, the same
    markup every register answer uses; the officer otherwise read a wall of pipe characters."""
    if not text or text.count("|") < 4 or "<table" in text:
        return text
    import html as _html
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        if line.count("|") >= 2 and not line.lstrip().startswith("<"):
            j, block = i, []
            while j < len(lines) and (lines[j].count("|") >= 2 or re.fullmatch(r"[\s|:\-]*", lines[j]) and lines[j].strip()):
                block.append(lines[j])
                j += 1
            # a header written on one line is followed by rows on the next lines; a lone "|" line is a row break
            block = [b for b in block if re.search(r"[^\s|:\-]", b)]
            rows = [[c.strip() for c in b.strip().strip("|").split("|")] for b in block]
            if len(rows) >= 2 and len(rows[0]) >= 2:
                width = len(rows[0])
                head = "".join(f"<th>{_html.escape(c)}</th>" for c in rows[0])
                body = "".join("<tr>" + "".join(f"<td>{_html.escape(c)}</td>" for c in (r + [''] * width)[:width]) + "</tr>"
                               for r in rows[1:])
                out.append(f"<table class='data-table'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
                i = j
                continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _scrub_unverified_counts(text: str, structured_data):
    """"You have 2 open applications": a total claim whose figure is nowhere in the data the answer was written from
    (the model guessed it) is dropped, never corrected -- there is no telling which count was meant."""
    if not text or not _COUNT_CLAIM_RE.search(text):
        return text, []
    try:
        payload = json.dumps(structured_data, default=str, ensure_ascii=False) if structured_data else ""
    except Exception:
        payload = str(structured_data or "")
    known = set(re.findall(r"\d{1,6}", payload))
    if isinstance(structured_data, dict):
        for v in structured_data.values():
            if isinstance(v, (list, tuple)):
                known.add(str(len(v)))
    removed, kept = [], []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
        m = _COUNT_CLAIM_RE.search(sent)
        if m and m.group("num") not in known:
            removed.append(m.group("num"))
        elif sent.strip():
            kept.append(sent)
    return " ".join(kept), removed


def _verify_answer_numbers(text: str, structured_data, intent: str = "") -> Tuple[str, str]:
    """Check an LLM-written answer against the data it was written from.

    Returns the corrected text and a note for the officer -- empty when nothing
    needed correcting, which is the ordinary case. The note is added rather than
    the correction being made silently: an answer that had to be corrected is
    one to read with care.
    """
    text = _scrub_llm_answer(text)
    if not text:
        return text, ""
    corrected, changed = _reconcile_count_claims(text, structured_data)
    corrected, removed = _scrub_unverified_app_numbers(
        corrected, _verified_app_numbers(structured_data))
    corrected, counts_removed = _scrub_unverified_counts(corrected, structured_data)
    if counts_removed:
        logger.warning(f"LLM answer stated a count not in the query result (intent={intent}); removed")
        if not corrected.strip():
            corrected = "I could not verify a count for that from the register."
    corrected, amounts_removed = _scrub_unverified_amounts(corrected, structured_data)
    corrected, specifics_removed = _scrub_unverified_specifics(corrected, structured_data)
    amounts_removed = list(amounts_removed) + list(specifics_removed)
    notes = []
    if amounts_removed and not corrected.strip():
        corrected = "I do not have that amount on record."
    if amounts_removed:
        logger.warning(f"LLM answer stated {len(amounts_removed)} amount(s) not in the query "
                       f"result (intent={intent}); removed")
        notes.append("Note: a detail (amount, time or web address) that is not in the register or the documents was removed. Fees are read from "
                     "the register -- ask \"what is the fee for ISD\".")
    if changed:
        logger.warning(f"LLM answer stated a total that disagreed with the query count "
                       f"(intent={intent}); corrected from the database")
    if removed:
        logger.warning(f"LLM answer named {len(removed)} application number(s) not in the "
                       f"query result (intent={intent}); removed")
        notes.append(
            "Note: part of that answer named an application number that is not in "
            "your register, so it was removed. Use the application numbers in the "
            "table above.")
    return _pipes_to_table(corrected), (" " + " ".join(notes) if notes else "")


# Expanding a listing into per-application detail tables costs one query per
# application, so a very long list is capped. The count is reported back with
# the answer rather than silently trimmed.
_MAX_LISTED_DETAILS = 15


async def _expand_listed_details(db, session_id, chat_history, message, officer):
    """Resolve "details of both / all three / every one of them" into details.

    Returns (structured_data, app_number): structured_data when the reference
    covers several applications, app_number when it resolves to exactly one, and
    (None, None) when the previous answer listed nothing to point at.
    """
    listed = await _app_numbers_from_last_listing(db, session_id, chat_history)
    if not listed:
        # Nothing was listed to point back at -- the previous answer found no
        # applications, or the reference reaches past the history we still hold.
        # Ask which application is meant; "not found" would be a claim about an
        # application the officer never named.
        return {"found": False, "no_listing_to_expand": True,
                "query_type": "Application Status"}, None

    # "details of all three" when four were listed means the first three; a
    # count larger than the list ("all five" over a list of two) is just the
    # whole list, not an error.
    wanted = requested_listing_count(message)
    if wanted and wanted < len(listed):
        listed = listed[:wanted]

    total = len(listed)
    shown = listed[:_MAX_LISTED_DETAILS]
    if len(shown) == 1:
        return None, shown[0]

    details = [await get_application_detail(db, an, officer=officer) for an in shown]
    
    # Isolate applicant-specific fields if requested
    msg_lower = (message or "").lower()
    if "applicant" in msg_lower or "விண்ணப்பதாரர்" in msg_lower:
        filtered_details = []
        for d in details:
            if not d:
                continue
            filtered = {
                "application_number": d.get("application_number"),
                "applicant_name": d.get("applicant_name"),
                "fathers_name": d.get("fathers_name"),
                "mobile": d.get("mobile"),
                "address": d.get("address"),
            }
            # Only include non-None values
            filtered_details.append({k: v for k, v in filtered.items() if v is not None})
        details = filtered_details

    return {
        "multi_applications": details,
        "listed_total": total,
        "query_type": "Application Status",
    }, None


# Assistant turns whose application numbers the officer never chose: the example
# inside "please specify the application number", the number just reported as not
# found or out of jurisdiction, the "did you mean" suggestion list. A back-
# reference resolved to one of those answers about an application nobody asked
# for -- exactly what _officer_turns exists to prevent -- so these turns are
# skipped even when the assistant turn IS allowed to supply the referent.
_QUOTED_BACK_ASSISTANT_MARKERS = (
    "please specify the application number", "for example", "example:",
    "no application exactly matches", "similar applications",
    "please reply with the full application number",
    "outside your assigned", "you do not have access",
    "not sure which one you mean", "please tell me which application",
    "could not be found", "no application found", "not found",
    "எடுத்துக்காட்டாக", "குறிப்பிடவும்", "ஒத்த விண்ணப்பங்கள்", "கிடைக்கவில்லை",
    "endha application", "match aagala", "full-a type pannunga",
)


def _is_quoted_back_answer(content: str) -> bool:
    """True when an assistant turn only quotes numbers back (prompt/refusal).

    A rendered table is never one of those -- it is data the officer asked for,
    and a cell that happens to read "not found" must not make the whole table
    unreferenceable.
    """
    low = (content or "").lower()
    if "<table" in low:
        return False
    return any(mark in low for mark in _QUOTED_BACK_ASSISTANT_MARKERS)


# The columns a back-reference can land on, matched against the table's own
# header text (and, in a two-column detail table, against the row label). Kept
# wide enough to cover every heading build_html_response emits for these two --
# "Application No.", "Application Number", "APPLICATION NO.", the Tamil labels.
_APP_COLUMN_HINTS = ("application no", "application number", "app no", "app number",
                     "விண்ணப்ப எண்", "vinnappa")
_SURVEY_COLUMN_HINTS = ("survey no", "survey number", "கணக்கெண்", "புல எண்", "survey")

# "Survey No. 1363", "survey number 1363/1A", "கணக்கெண் 1363" -- how an answer
# names a survey outside a table column.
_SURVEY_LABEL_RE = re.compile(
    r"(?:survey\s*(?:no\.?|number)?|கணக்கெண்|புல\s*எண்)\s*[:\-]?\s*"
    r"(\d{1,5}(?:/[0-9A-Za-z]+)?)",
    re.IGNORECASE,
)


def _column_values(parsed: dict, hints: tuple) -> List[str]:
    """The cells of the column a back-reference names, in the order shown.

    Handles both listing tables (a matching header) and the two-column detail
    table build_html_response renders for a single record (a matching row
    label). Row order is the order the officer sees, so "the second one" means
    the second row here too.
    """
    rows = [r for r in (parsed.get("rows") or []) if r]
    if not rows:
        return []
    headers = [(h or "").lower() for h in (parsed.get("headers") or [])]

    def _clean(vals):
        out: List[str] = []
        for v in vals:
            v = (v or "").strip()
            if not v or v in ("-", "—") or v.lower() in ("n/a", "na", "none"):
                continue
            if v not in out:
                out.append(v)
        return out

    idx = next((i for i, h in enumerate(headers) if any(k in h for k in hints)), None)
    if idx is not None:
        return _clean(r[idx] if idx < len(r) else "" for r in rows)

    # Field/Value detail table: the identifier sits in the second cell of the
    # row whose label matches.
    if not headers or len(headers) <= 2:
        return _clean(r[1] for r in rows
                      if len(r) > 1 and any(k in (r[0] or "").lower() for k in hints))
    return []


async def _recent_answers(db, session_id, chat_history=None, depth: int = 5) -> List[str]:
    """Assistant answers, most recent first, that could carry a referent.

    The frontend sends the rendered answer as history, but a table answer can
    reach us with the table payload stripped, so the session's stored messages
    are the fallback -- those always hold the rendered HTML.
    """
    out: List[str] = []
    for msg in reversed(chat_history or []):
        if not isinstance(msg, dict):
            continue
        if str(msg.get("role", "")).lower() not in ("assistant", "ai", "bot"):
            continue
        content = msg.get("content", "") or ""
        if content and not _is_quoted_back_answer(content):
            out.append(content)
        if len(out) >= depth:
            return out

    if db is None or not session_id:
        return out
    try:
        rows = (await db.execute(
            select(ChatMessage.content)
            .where(and_(ChatMessage.session_id == session_id,
                        ChatMessage.role == "assistant",
                        messages_since_session_start(session_id)))
            .order_by(ChatMessage.created_at.desc())
            .limit(depth)
        )).scalars().all()
    except Exception:  # pragma: no cover - history is a convenience, never fatal
        logger.warning("could not read session history for a back-reference")
        return out
    for content in rows:
        if content and not _is_quoted_back_answer(content):
            out.append(content)
    return out


async def _survey_refs_from_last_listing(db, session_id, chat_history=None,
                                         limit: int = 50) -> List[str]:
    """Survey numbers from the most recent answer that showed any.

    The application-number column is not the only thing an officer points back
    at: every table that carries a Survey No. column -- the parcel listing, the
    field-visit queue, an application detail card -- is a list they can say
    "who owns the second one?" about.
    """
    for content in await _recent_answers(db, session_id, chat_history):
        found = _column_values(_parse_previous_answer(content), _SURVEY_COLUMN_HINTS)
        if found:
            return found[:limit]
        # Not every answer puts the survey in a column: the survey-detail card
        # names it in the caption ("Survey No. 1363") above a sub-division
        # table, and a one-line answer names it in the sentence.
        labelled: List[str] = []
        for m in _SURVEY_LABEL_RE.finditer(_strip_tags(content)):
            v = m.group(1)
            if v not in labelled:
                labelled.append(v)
        if labelled:
            return labelled[:limit]
    return []


async def _app_numbers_from_last_listing(db, session_id, chat_history=None,
                                         limit: int = 50) -> List[str]:
    """Every application number in the most recent answer that listed any.

    _officer_turns deliberately refuses to take an application number out of an
    assistant turn, because a lone number quoted back by the bot is not one the
    officer chose. This is the one case where the assistant turn IS the
    reference: the officer said "both of them" about a list they can see, and
    the whole list is taken, not one arbitrary entry from it.

    The application-number column is read first so the order matches the rows on
    screen; a plain scan of the text is the fallback for answers that name an
    application in prose rather than in a table.
    """
    def _numbers_in(text: str) -> List[str]:
        seen: List[str] = []
        for m in _ANY_APP_NUMBER_RE.finditer(text or ""):
            v = m.group(0).upper()
            if v not in seen:
                seen.append(v)
        return seen

    for content in await _recent_answers(db, session_id, chat_history):
        column = [v.upper() for v in
                  _column_values(_parse_previous_answer(content), _APP_COLUMN_HINTS)
                  if _ANY_APP_NUMBER_RE.search(v)]
        if column:
            return column[:limit]
        found = _numbers_in(content)
        if found:
            return found[:limit]
    return []


# "the first one", "2nd application", "the last one" pick a single entry out of
# the list the officer is looking at. Ordinals are the only positional reference
# that is unambiguous against a rendered table, so nothing else is guessed at.
_ORDINAL_PATTERNS = (
    (r"\b(?:1st|first)\b|முதல்|mudhal", 1),
    (r"\b(?:2nd|second)\b|இரண்டாவது|rendaavadhu", 2),
    (r"\b(?:3rd|third)\b|மூன்றாவது|moonraavadhu", 3),
    (r"\b(?:4th|fourth)\b|நான்காவது", 4),
    (r"\b(?:5th|fifth)\b|ஐந்தாவது", 5),
)
_LAST_ORDINAL_RE = re.compile(r"\b(?:last|final)\b|கடைசி|kadaisi", re.IGNORECASE)


def _pick_from_listing(message: str, listed: List[str]) -> Optional[str]:
    """Resolve "the second one" against the list the previous answer showed.

    Returns None when the message names no position, or names one the list does
    not have -- the caller then asks rather than guessing.
    """
    if not listed:
        return None
    msg = (message or "").lower()
    if _LAST_ORDINAL_RE.search(msg):
        return listed[-1]
    for pattern, idx in _ORDINAL_PATTERNS:
        if re.search(pattern, msg, re.IGNORECASE) and idx <= len(listed):
            return listed[idx - 1]
    return None


# The officer points back at a survey the same way they point back at an
# application: by naming it ("this survey number"), by position ("the second
# one"), or with a bare pronoun after a single-row answer.
_SURVEY_REFERENCE_PATTERNS = (
    "this survey", "that survey", "the survey", "same survey", "above survey",
    "this parcel", "that parcel", "the parcel", "this survey number",
    "இந்த கணக்கெண்", "அந்த கணக்கெண்", "இந்த புல எண்", "இதே கணக்கெண்",
    "indha survey", "andha survey", "idhe survey",
)
_SURVEY_REFERENCE_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(p) for p in _SURVEY_REFERENCE_PATTERNS) + r")(?!\w)",
    re.IGNORECASE,
)


def _has_survey_back_reference(message: str) -> bool:
    """True when the officer points back at a survey shown earlier."""
    msg = message or ""
    return bool(_SURVEY_REFERENCE_RE.search(msg)) or _has_application_back_reference(msg)


async def _survey_ref(db, session_id, message: str, chat_history: list = None) -> Optional[str]:
    """The survey number this message is about, typed or pointed at.

    extract_survey_number() first, so an explicit number always wins. Otherwise
    the survey column of the last table the officer was shown -- by position
    when they named one ("who owns the second one?"), or the only row when the
    table had one. Several rows and no position is genuinely ambiguous, so it
    resolves to nothing and the branch asks, exactly as it did before.
    """
    survey_no = extract_survey_number(message)
    if survey_no:
        return survey_no
    if not _has_survey_back_reference(message):
        return None
    listed = await _survey_refs_from_last_listing(db, session_id, chat_history)
    if not listed:
        return None
    picked = _pick_from_listing(message, listed)
    if picked:
        logger.info(f"Resolved survey reference to '{picked}' from the last table shown")
        return picked
    if len(listed) == 1:
        logger.info(f"Resolved survey reference to '{listed[0]}' from the last table shown")
        return listed[0]
    return None


async def _survey_number_for_application(db, officer, app_number: str) -> Optional[str]:
    """The survey number an application sits on, or None.

    `_UNTRACKED_PARCEL_FIELDS` questions ("what is the municipal tax / soil
    type / old survey number ...") are asked about a survey number, but an
    officer names the application just as naturally ("... for
    2022/0154/28/000156?"). `get_application_detail` already resolves and
    authorizes that application; reusing it here is what lets the parcel-field
    answer fire on the application-scoped phrasing instead of falling through
    to a generic non-answer.
    """
    try:
        detail = await get_application_detail(db, app_number, officer=officer)
    except Exception:
        return None
    if not detail or not detail.get("found", True):
        return None
    return detail.get("survey_no") or detail.get("survey_number")


# Geography the officer names or points at. "block" alone is a filter word in
# English, Tamil and Tanglish, and an officer looking at a table says "this
# block" far more often than they retype the number.
_WARD_WORD_RE = re.compile(r"\bwards?\b|வார்டு|வார்ட்|vaard", re.IGNORECASE)
_BLOCK_WORD_RE = re.compile(r"\bblocks?\b|பிளாக்|ப்ளாக்|block", re.IGNORECASE)
# The value has to sit next to the word: "ward 102", "வார்டு 2", "block no. 15".
_WARD_VALUE_RE = re.compile(
    r"(?:\bwards?|வார்டு|வார்ட்)\s*(?:no\.?|number|எண்)?\s*[:\-#]?\s*(\d{1,4})\b",
    re.IGNORECASE)
_BLOCK_VALUE_RE = re.compile(
    r"(?:\bblocks?|பிளாக்|ப்ளாக்)\s*(?:no\.?|number|எண்)?\s*[:\-#]?\s*([A-Za-z]?\d{1,4})\b",
    re.IGNORECASE)
_WARD_COLUMN_HINTS = ("ward", "வார்டு")
_BLOCK_COLUMN_HINTS = ("block", "பிளாக்")


async def _geo_scope(db, session_id, message: str, chat_history=None, officer=None):
    """(ward_number, block_number) for this message.

    A named number wins. "this block" / "in the same ward" carries none, so the
    value comes from the column of the table the officer is looking at, and
    failing that from the officer's own jurisdiction when it holds exactly one.
    Returning None for both leaves every caller exactly as it was.
    """
    msg = message or ""
    # Keyword-anchored only. extract_ward_number()/extract_block_number() fall
    # back to "the first number anywhere in the message", which turns "how many
    # applications did I approve in 2024 in my ward" into ward 2024 -- an empty
    # filter, and a jurisdiction denial once the scope is checked.
    _w = _WARD_VALUE_RE.search(msg)
    _b = _BLOCK_VALUE_RE.search(msg)
    ward = _w.group(1) if _w else None
    block = _b.group(1).upper() if _b else None

    needs_ward = bool(_WARD_WORD_RE.search(msg)) and not ward
    needs_block = bool(_BLOCK_WORD_RE.search(msg)) and not block
    if not (needs_ward or needs_block):
        return ward, block

    for content in await _recent_answers(db, session_id, chat_history):
        parsed = _parse_previous_answer(content)
        if needs_ward and not ward:
            vals = _column_values(parsed, _WARD_COLUMN_HINTS)
            if len(set(vals)) == 1:
                ward = vals[0]
        if needs_block and not block:
            vals = _column_values(parsed, _BLOCK_COLUMN_HINTS)
            if len(set(vals)) == 1:
                block = vals[0]
        if (ward or not needs_ward) and (block or not needs_block):
            break

    if officer is not None and ((needs_ward and not ward) or (needs_block and not block)):
        own = await _officer_geography(db, officer)
        if needs_ward and not ward and len(own["wards"]) == 1:
            ward = own["wards"][0]
        if needs_block and not block and len(own["blocks"]) == 1:
            block = own["blocks"][0]
    return ward, block


def _same_geo_code(a, b) -> bool:
    """0015 == 15 == "Block 15". Codes are stored zero-padded; officers type them
    either way, and the listing filters already match them loosely."""
    a, b = str(a or "").strip().lower(), str(b or "").strip().lower()
    if not a or not b:
        return False
    if a.isdigit() and b.isdigit():
        return int(a) == int(b)
    return a == b or a in b or b in a


async def _foreign_geo_denial(db, officer, ward, block, language: str):
    """Refusal text when the message names a ward/block the officer does not hold.

    A ward SIS officer may talk about "ward", but only their own: naming another
    ward is a request for someone else's data. Answering it with "no
    applications found" was both misleading and a disclosure -- it confirmed the
    ward exists and implied it is empty. District-level officers are exempt;
    their jurisdiction filter already covers every ward under them.
    """
    if not (ward or block):
        return None
    if getattr(officer, "jurisdiction_type", "") in ("town", "taluk", "district"):
        return None
    own = await _officer_geography(db, officer)
    is_tamil = language in ("ta", "tanglish")
    _ward_w = "வார்டு" if is_tamil else "Ward"
    _block_w = "தொகுதி" if is_tamil else "Block"
    foreign = []
    if ward and own["wards"] and not any(_same_geo_code(ward, w) for w in own["wards"]):
        foreign.append(f"{_ward_w} {ward}")
    if block and own["blocks"] and not any(_same_geo_code(block, b) for b in own["blocks"]):
        foreign.append(f"{_block_w} {block}")
    if not foreign:
        return None

    _and_w = "மற்றும்" if is_tamil else "and"
    mine = ", ".join(f"{_ward_w} {w}" for w in own["wards"]) or (
        "உங்கள் ஒதுக்கப்பட்ட பகுதி" if is_tamil else "your assigned area")
    if own["blocks"]:
        if is_tamil:
            mine += " (தொகுதி " + ", ".join(own["blocks"]) + ")"
        else:
            mine += " (block" + ("s " if len(own["blocks"]) > 1 else " ") + ", ".join(own["blocks"]) + ")"
    named = f" {_and_w} ".join(foreign)
    if is_tamil:
        return (f"{named} உங்கள் அதிகார எல்லைக்கு வெளியே உள்ளது. "
                f"நீங்கள் {mine} தரவை மட்டுமே பார்க்க முடியும்.")
    return (f"{named} is outside your assigned jurisdiction. "
            f"You can only see data for {mine}.\n\n"
            f"For another ward, please ask the officer who holds it or your supervising officer.")


async def _officer_geography(db, officer) -> Dict[str, list]:
    """The ward and block numbers the officer actually holds."""
    wards: List[str] = []
    blocks: List[str] = []
    try:
        rows = (await db.execute(
            select(OfficerJurisdiction)
            .options(selectinload(OfficerJurisdiction.ward),
                     selectinload(OfficerJurisdiction.block))
            .where(OfficerJurisdiction.officer_id == officer.officer_id)
        )).scalars().all()
        ward_ids = [j.ward_id for j in rows if j.ward_id]
        for j in rows:
            if j.ward and j.ward.ward_number and j.ward.ward_number not in wards:
                wards.append(j.ward.ward_number)
            if j.block and j.block.block_number and j.block.block_number not in blocks:
                blocks.append(j.block.block_number)
        if ward_ids and not blocks:
            for blk in (await db.execute(
                    select(Block).where(Block.ward_id.in_(ward_ids)))).scalars().all():
                if blk.block_number and blk.block_number not in blocks:
                    blocks.append(blk.block_number)
    except Exception:  # pragma: no cover - jurisdiction lookup is a convenience
        logger.warning("could not read the officer's own ward/block for a bare geography reference")
    return {"wards": wards, "blocks": blocks}


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
    app_match = re.search(r'\b\d{4}/(?:0153|0154)/\d{1,3}/\d+\b|\b\d{4}/\d{1,3}/(?:0153|0154)/\d+\b', message, re.IGNORECASE)
    if app_match:
        return app_match.group(0).upper()

    # Pattern 0b: Broader YYYY/A/B/N format — catches any user-typed 4-segment number
    broad_match = re.search(r'\b(20\d{2}/[\w]+/[\w]+/\d+)\b', message)
    if broad_match:
        return broad_match.group(0).upper()

    # Pattern 1: Standard format (ISD|NISD)/XX/YYYY/NNN
    app_match = re.search(r'(ISD|NISD)/\w+/\d+/\d+', message, re.IGNORECASE)
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
            app_match = re.search(r'\b\d{4}/(?:0153|0154)/\d{1,3}/\d+\b|\b\d{4}/\d{1,3}/(?:0153|0154)/\d+\b|(?:ISD|NISD)/\w+/\d+/\d+|APP-\d{4}-\d{6}|\b20\d{2}/[\w]+/[\w]+/\d+\b', content, re.IGNORECASE)
            if app_match:
                logger.info(f"Found implicit application continuation '{app_match.group(0)}' from immediate context")
                return app_match.group(0).upper()
    
    # Pattern 5: Explicit reference - search further back in history
    if has_explicit_reference and chat_history:
        for msg in reversed(_officer_turns(chat_history)[-8:]):
            content = msg.get("content", "") or ""
            app_match = re.search(r'\b\d{4}/(?:0153|0154)/\d{1,3}/\d+\b|\b\d{4}/\d{1,3}/(?:0153|0154)/\d+\b|(?:ISD|NISD)/\w+/\d+/\d+|APP-\d{4}-\d{6}|\b20\d{2}/[\w]+/[\w]+/\d+\b', content, re.IGNORECASE)
            if app_match:
                logger.info(f"Found application reference '{app_match.group(0)}' from chat history")
                return app_match.group(0).upper()

    return None


# ── "What was my previous message?" ──────────────────────────────────────────
# Meta questions about the conversation itself. Left to the LLM these get
# answered wrongly — it echoes the question just asked instead of the turn
# before it — so they are answered directly from the chat history the client
# sent, which is the only reliable record of what was said.
_RECALL_SUBJECT_WORDS = [
    "message", "msg", "question", "asked", "ask", "said", "say", "typed",
    "wrote", "query", "prompt", "chat", "conversation", "convo", "talk",
    "talked", "discuss", "discussed", "told", "spoke",
    "செய்தி", "கேள்வி", "சொன்ன", "கேட்ட", "உரையாடல", "பேசிய",
]
_RECALL_QUALIFIER_WORDS = [
    "previous", "prev", "last", "earlier", "before", "above", "first",
    # "remember" / "recall" ask for the conversation itself even with no
    # previous/last/earlier alongside them -- "do you remember our chat" and
    # "can you recall our conversation" carry no other qualifier word.
    "remember", "recall", "recollect",
    "முந்தைய", "கடந்த", "முன்பு", "மேலே", "முதல்", "நினைவு", "கடைசி",
]


# Data questions that merely contain a recall-ish word ("when was it updated
# lastly", "last field visit date") are about the applications, not about the
# conversation, so they must not be swallowed here.
_RECALL_DATA_WORDS = [
    "application", "app", "apps", "survey", "update", "updated", "updation",
    "status", "visit", "sketch", "document", "owner", "patta", "deed",
    # A field named alongside "the previous question" makes it a data question
    # about that application, not a question about the conversation: "what is
    # the CAN number of the previous question?" was answered with "Your
    # previous message was: ..." because none of these were listed.
    "can", "aadhaar", "mobile", "name", "address", "ward", "block", "fee",
    "amount", "channel", "date", "subdivision", "igrs", "reason", "stage",
    "district", "taluk", "town", "applicant", "area", "type", "serial",
    "service", "renewal", "camp", "charge",
    "விண்ணப்ப", "புல எண", "நிலை", "பெயர்", "வார்டு", "கட்டணம்",
    "மாவட்ட", "தாலுகா", "நகர", "விண்ணப்பதாரர்",
]


def _is_conversation_recall(message: str) -> bool:
    if _clear_info_topic(message):
        return False  # "will you remember anything if I clear the chat?" is about clearing
    m = (message or "").lower().strip()
    if not m or len(m.split()) > 12:
        return False
    # Whole-token matching only: "lastly" is not "last", "asking" is not "ask"
    # in the sense meant here, and substring matching mistook
    # "when it was updated lastly" for a question about the chat itself.
    toks = _tokens(m)

    def _hit(words):
        # Tamil agglutinates, so its stems stay substring matches; English is
        # matched whole-token.
        return any(
            (w in m) if not w.isascii() else (w in toks)
            for w in words
        )

    if not _hit(_RECALL_QUALIFIER_WORDS):
        return False
    if not _hit(_RECALL_SUBJECT_WORDS):
        return False
    # "status of my previous application" is about data, not the conversation.
    # "can" alone is the CAN-number signal ("what is the CAN number of the
    # previous question?"), but "can you recall our earlier conversation?"
    # uses the same word as the auxiliary verb -- strip "can you"/"can i"/
    # "can we" before checking so the auxiliary use never masquerades as a
    # CAN-number question.
    data_toks = _tokens(re.sub(r"\bcan\s+(you|i|we)\b", " ", m))
    if any((w in m) if not w.isascii() else (w in data_toks) for w in _RECALL_DATA_WORDS):
        return False
    return True


def _conversation_recall_reply(message: str, chat_history: list, language: str) -> str:
    """Answer a recall question from chat_history (client-sent, oldest first).

    The current message is NOT part of chat_history — the frontend snapshots the
    history before storing it — so the last user entry is genuinely the previous
    turn.
    """
    m = (message or "").lower()
    is_ta = language in ("ta", "tanglish") or any(w in (message or "") for w in ["செய்தி", "கேள்வி", "சொன்ன"])
    about_assistant = any(w in m for w in ["you say", "you said", "your last", "your previous",
                                           "your reply", "your response", "you tell", "you told"])
    want_role = "assistant" if about_assistant else "user"

    texts = [
        (h.get("content") or "").strip()
        for h in (chat_history or [])
        if (h.get("role") or "user") == want_role and (h.get("content") or "").strip()
    ]
    # "first message" asks for the other end of the conversation.
    wants_first = "first" in m or "முதல்" in m
    if not texts:
        if is_ta:
            return "இந்த உரையாடலில் அதற்கு முந்தைய செய்தி எதுவும் இல்லை — இதுதான் முதல் செய்தி."
        return "There is no earlier message in this conversation — this is the first one."

    picked = texts[0] if wants_first else texts[-1]
    if len(picked) > 400:
        picked = picked[:400] + "…"

    if want_role == "assistant":
        if is_ta:
            return f'நான் முன்பு சொன்னது: "{picked}"'
        return f'My previous reply was: "{picked}"'
    if is_ta:
        return f'உங்கள் முந்தைய செய்தி: "{picked}"'
    label = "first" if wants_first else "previous"
    return f'Your {label} message was: "{picked}"'


# ── "Who am I?" — the officer's own identity ─────────────────────────────────
# The officer is authenticated: their name, designation, employee id and
# jurisdiction are all on the request. Letting this fall through to the LLM
# produced "You are a Sub Inspector Surveyor (SIS) officer of Tamil Nadu" —
# true of every user of the system and of nobody in particular. It is answered
# here from the officer's own record instead.
_IDENTITY_PATTERNS = [
    r"\bwho\s+am\s+i\b", r"\bwho\s+i\s+am\b", r"\bwhoami\b",
    r"\bwho\s+are\s+you\s+talking\s+to\b",
    r"\bmy\s+name\b", r"\bmy\s+full\s+name\b", r"\bname\s+of\s+mine\b",
    # Typo-tolerant: "muy name", "myu name", "mi name", "mu name"
    r"\b(?:muy|myu|mi|mu)\s+name\b",
    r"\bmy\s+(?:designation|post|posting|rank|title|position|role)\b",
    r"\bmy\s+(?:employee|emp|staff|officer)\s*(?:id|number|code)\b",
    # "my user id" / "my user_id" — officer asking for their own login ID
    r"\bmy\s+(?:user[_\s]*id|login[_\s]*id|login)\b",
    r"\b(?:muy|myu|mi|mu)\s+(?:user[_\s]*id|login[_\s]*id)\b",
    r"\bmy\s+(?:profile|details|detail|info|information|credentials|account)\b",
    r"\babout\s+me\b", r"\bmy\s+identity\b",
    r"\bwhich\s+officer\s+am\s+i\b", r"\bam\s+i\s+(?:a|an|the)\s+sis\b",
    r"\bwhat\s+am\s+i\b",
    # Tamil
    r"நான்\s*யார்", r"நான்\s*யாரு", r"என்\s*பெயர்", r"எனது\s*பெயர்",
    r"என்\s*பதவி", r"எனது\s*பதவி", r"என்\s*விவரம்", r"எனது\s*விவரங்கள்",
    # Tanglish
    r"\bnaan\s*yaar\b", r"\bnaan\s*yar\b", r"\bnaan\s*yaaru\b",
    r"\ben\s*peyar\b", r"\ben\s*per\b", r"\bennoda\s*per\b",
    r"\ben\s*padhavi\b", r"\ben\s*pathavi\b",
]
_IDENTITY_RE = re.compile("|".join(_IDENTITY_PATTERNS), re.IGNORECASE)
# "role id" is an application field, and a named application means the question
# is about that file's officer, not about the officer asking.
_IDENTITY_BLOCKERS = ["role id", "role_id", "roleid"]
# "user id" / "user_id" are blocked only when the officer does NOT pair them
# with "my" — a bare "what is the user id" is an application field question,
# but "my user_id" / "what is my user id" is an identity question.
_IDENTITY_USER_ID_TERMS = ["user id", "user_id", "userid"]

# ── Sensitive-query guard: password / secret requests ────────────────────────
# Never reveal passwords. This check fires before every other handler so it
# cannot be bypassed by creative phrasing.
_SENSITIVE_RE = re.compile(
    # Only block security/account secrets — NOT India's postal 'pincode'.
    # "pin code" by itself is ambiguous (postal code vs. account PIN);
    # block it only when paired with a possessive ("my") or financial context
    # (atm, bank, otp, account). A bare "pincode of applicant" must pass through.
    r"\b(?:password|passcode|passwd|pass\s*word|secret\s*key|login\s*password"
    r"|my\s*pass|credentials?\s*password"
    r"|(?:my|atm|bank|account|otp|debit|credit)\s*pin(?:\s*code)?)\b",
    re.IGNORECASE,
)


def _is_sensitive_query(message: str) -> bool:
    """True when the message asks for a password or secret — must be refused."""
    return bool(_SENSITIVE_RE.search((message or "").strip()))


# ── "What is today's date?" — the calendar date, not an application field ────
# There was no handler for this at all. "date" is one of `_field_keywords`'
# per-application field names, so "what is todays date" fell into the same
# lookup as "what is the submission date" -- answered from whatever
# application happened to be in view, dated in the past, and presented as if
# it were today. An officer asking the day of the week got back the
# submission date of a file they had looked at minutes earlier. Answered here,
# from the server's own clock in Asia/Kolkata (IST), before any application-field lookup runs.

# Tamil lookaround boundaries: combining vowel signs and viramas fail with standard ASCII \b.
_TA_CHARS = r"\u0B80-\u0BFF"
_TA_LB = rf"(?<![\w{_TA_CHARS}])"
_TA_RB = rf"(?![\w{_TA_CHARS}])"

# Latin-script English and Tanglish components
_TODAY_PREFIX_PAT = r"(?:in+k+i|in+ik+[iu]|in+aik+[iu]|inr[ae]ik+[iu]|inru|indraikku|today)"
_QUESTION_WORD_PAT = r"(?:en(?:dh?|n)a|yen(?:dh?|n)a)"
_DATE_NOUN_PAT = r"(?:na+l[u]?|thethi|thathi|tethi|date|kizh?amai|kilamai|day)"

_TODAY_DATE_LATIN_RE = re.compile(
    r"\b(?:"
    # 1. English variations with apostrophes
    r"what(?:[\u0027\u2019]s|\s+is)?\s+today(?:[\u0027\u2019]s|s)?\s+(?:correct\s+)?(?:date|day)|"
    r"what\s+(?:date|day)\s+is\s+(?:it\s+)?today|"
    r"what\s+is\s+(?:the\s+)?(?:correct\s+)?(?:date|day)\s+today|"
    r"what\s+day\s+is\s+today|"
    r"today(?:[\u0027\u2019]s|s)?\s+(?:correct\s+)?(?:date|day)|"
    r"(?:correct\s+)?(?:date|day)\s+today|"
    r"current\s+date|"
    # 2. Tanglish: cue + question word + noun (e.g. inki enda nal, innaiku enna date, innaiki enna thethi)
    rf"{_TODAY_PREFIX_PAT}\s+{_QUESTION_WORD_PAT}\s+{_DATE_NOUN_PAT}|"
    # 3. Tanglish: cue + noun (+ optional question word) (e.g. today date enna, inniki thethi)
    rf"{_TODAY_PREFIX_PAT}\s+{_DATE_NOUN_PAT}(?:\s+{_QUESTION_WORD_PAT})?|"
    # 4. Tanglish: question word + noun + MANDATORY cue (e.g. enna date today, enda nal inki)
    rf"{_QUESTION_WORD_PAT}\s+{_DATE_NOUN_PAT}\s+{_TODAY_PREFIX_PAT}"
    r")\b",
    re.IGNORECASE,
)

_TODAY_DATE_TAMIL_RE = re.compile(
    rf"{_TA_LB}(?:"
    r"இன்றைய\s+(?:தேதி|நாள்|கிழமை)(?:\s+என்ன)?|"
    r"இன்று\s+என்ன\s+(?:தேதி|நாள்|கிழமை)|"
    r"இன்று\s+(?:தேதி|நாள்|கிழமை)\s+என்ன|"
    r"இன்றைய\s*தேதி|இன்று\s*என்ன\s*தேதி|இன்று\s*தேதி\s*என்ன"
    rf"){_TA_RB}",
    re.IGNORECASE,
)

_DATE_DOMAIN_BLOCK_RE = re.compile(
    r"\b(?:"
    r"applications?|appl|field\s*visits?|inspections?|"
    r"submissions?|submitted|registered|registrations?|deeds?|"
    r"pattas?|surveys?|sub-?divisions?|orders?|hearings?|ec|"
    r"for\s+(?:this|that|the)\s+application|"
    r"this\s+application|that\s+application"
    r")\b|"
    rf"{_TA_LB}(?:விண்ணப்ப\w*|களப்பணி\w*|பார்வை\w*|பதிவு\w*){_TA_RB}",
    re.IGNORECASE,
)

_ENGLISH_WEEKDAYS = {
    0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
    4: "Friday", 5: "Saturday", 6: "Sunday",
}

_ENGLISH_MONTHS = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}

_TAMIL_WEEKDAYS = {
    0: "திங்கட்கிழமை", 1: "செவ்வாய்க்கிழமை", 2: "புதன்கிழமை", 3: "வியாழக்கிழமை",
    4: "வெள்ளிக்கிழமை", 5: "சனிக்கிழமை", 6: "ஞாயிற்றுக்கிழமை",
}

_TAMIL_MONTHS = {
    1: "ஜனவரி", 2: "பிப்ரவரி", 3: "மார்ச்", 4: "ஏப்ரல்",
    5: "மே", 6: "ஜூன்", 7: "ஜூலை", 8: "ஆகஸ்ட்",
    9: "செப்டம்பர்", 10: "அக்டோபர்", 11: "நவம்பர்", 12: "டிசம்பர்",
}

_CURRENT_CLOCK_OVERRIDE: contextvars.ContextVar[Optional[datetime]] = contextvars.ContextVar(
    "_CURRENT_CLOCK_OVERRIDE", default=None
)


def _kolkata_timezone() -> timezone:
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        return ZoneInfo("Asia/Kolkata")
    except (ZoneInfoNotFoundError, FileNotFoundError, KeyError, ImportError):
        return timezone(timedelta(hours=5, minutes=30))


def _get_kolkata_now(clock: Optional[datetime] = None) -> datetime:
    if clock is not None:
        return clock
    override = _CURRENT_CLOCK_OVERRIDE.get()
    if override is not None:
        return override
    return datetime.now(_kolkata_timezone())


def _classify_date_facet(query: str) -> str:
    q = (query or "").lower()
    has_weekday = bool(re.search(
        r"\b(?:kizh?amai|kilamai|day)\b|கிழமை",
        q,
    ))
    has_date = bool(re.search(
        r"\b(?:thethi|thathi|tethi|date)\b|தேதி",
        q,
    ))
    has_naal = bool(re.search(
        r"\b(?:na+l[u]?)\b|நாள்",
        q,
    ))
    if has_naal or (has_weekday and has_date):
        return "both"
    if has_weekday:
        return "weekday"
    return "date"


def _is_today_date_question(message: str) -> bool:
    m = (message or "").strip()
    if not m or len(m.split()) > 10:
        return False
    if re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", m):
        return False
    if re.search(r"\b(?:0153|0154|0156|0157|0178)\b", m):
        return False
    if _DATE_DOMAIN_BLOCK_RE.search(m):
        return False
    return bool(_TODAY_DATE_LATIN_RE.search(m) or _TODAY_DATE_TAMIL_RE.search(m))


def _today_date_answer(language: str = "en", query_text: str = "", now_dt: Optional[datetime] = None) -> str:
    now = _get_kolkata_now(now_dt)
    today = now.date()
    iso_date = today.isoformat()
    weekday_idx = today.weekday()
    month_idx = today.month

    day_en = _ENGLISH_WEEKDAYS[weekday_idx]
    month_en = _ENGLISH_MONTHS[month_idx]
    formatted_en = f"{today.day} {month_en} {today.year}"

    facet = _classify_date_facet(query_text)

    if language in ("ta", "tanglish"):
        day_ta = _TAMIL_WEEKDAYS[weekday_idx]
        month_ta = _TAMIL_MONTHS[month_idx]
        formatted_ta = f"{today.day} {month_ta} {today.year}"
        if facet == "weekday":
            return f"இன்று {day_ta}."
        elif facet == "date":
            return f"இன்றைய தேதி: {formatted_ta} ({iso_date})."
        else:
            return f"இன்று {day_ta}, {formatted_ta} ({iso_date})."

    if facet == "weekday":
        return f"Today is {day_en}."
    elif facet == "date":
        return f"Today's date is {formatted_en} ({iso_date})."
    else:
        return f"Today is {day_en}, {formatted_en} ({iso_date})."


def _is_officer_identity_question(message: str) -> bool:
    m = (message or "").strip().lower()
    if not m or len(m.split()) > 10:
        return False
    if any(b in m for b in _IDENTITY_BLOCKERS):
        return False
    if _OPINION_RE.search(m):
        return False           # "what do you think about me" asks for an opinion, not who I am
    # Block "user id" / "user_id" only when NOT preceded by "my" (or "mu"/"muy"
    # typo variants) — a bare "user id" is an application field, but
    # "my user id" is the officer asking about their own login.
    _has_user_id = any(t in m for t in _IDENTITY_USER_ID_TERMS)
    if _has_user_id:
        # Only allow if the message pairs it with a first-person possessive
        if not re.search(r"\b(?:my|mu|muy|myu|mi)\s+user", m):
            return False
    if re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", m):
        return False
    return bool(_IDENTITY_RE.search(m))


_DESIGNATION_LONG = {
    "SIS": "Sub Inspector Surveyor (SIS)",
    "SD": "Senior Draughtsman (SD)",
    "DIS": "Deputy Inspector Surveyor (DIS)",
    "TAHSILDAR": "Zonal Level Tahsildar (ZDT)",
}
def _format_jurisdiction_line(jur: dict) -> str:
    """"Ward 002 (Block 0015), Thoothukudi town — Thoothukudi taluk, ... district (28)"."""
    if not jur:
        return ""
    parts = []
    for town in jur.get("towns") or []:
        for ward in town.get("wards") or []:
            blocks = ", ".join(b["block_number"] for b in (ward.get("blocks") or [])
                               if b.get("block_number"))
            ward_txt = f"Ward {ward.get('ward_number')}"
            if blocks:
                ward_txt += f" (Block {blocks})"
            parts.append(ward_txt)
        if town.get("name"):
            parts.append(f"{town['name']} town")
    taluk = (jur.get("taluk") or {}).get("name")
    if taluk and taluk != "N/A":
        parts.append(f"{taluk} taluk")
    district = jur.get("district") or {}
    if district.get("name") and district["name"] != "N/A":
        d_txt = district["name"] + " district"
        if district.get("code") and district["code"] != "N/A":
            d_txt += f" ({district['code']})"
        parts.append(d_txt)
    return ", ".join(parts)


def _identity_facet(message: str) -> str:
    """Which part of themselves the officer asked about.

    "who am i" wants a name and a post, not a personnel file — answer the
    question asked and nothing else. Only an explicit "my profile" / "my
    details" opens the full card.
    """
    m = (message or "").lower()
    if any(w in m for w in ["profile", "details", "detail", "information", "info",
                            "credentials", "account", "விவரம்", "விவரங்கள்"]):
        return "profile"
    if any(w in m for w in ["employee id", "emp id", "employee number", "staff id",
                            "staff number", "officer id", "officer code", "emp no",
                            "பணியாளர் எண்"]):
        return "employee_id"
    # "my login id" / "my user id" → show the login email, not just the emp ID
    if any(w in m for w in ["user id", "user_id", "userid", "login id", "login"]):
        return "login_id"
    if any(w in m for w in ["designation", "post", "posting", "rank", "title",
                            "position", "role", "பதவி", "padhavi", "pathavi"]):
        return "designation"
    if any(w in m for w in ["name", "பெயர்", "peyar", "per", "peru"]):
        return "name"
    return "who"


async def _officer_identity_reply(db: AsyncSession, officer, language: str,
                                  facet: str = "who") -> str:
    """The officer's own identity, from their record — never from the LLM."""
    is_ta = language in ("ta", "tanglish")
    row = None
    try:
        row = (await db.execute(
            select(SISOfficer).where(SISOfficer.id == officer.officer_id)
        )).scalar_one_or_none()
    except Exception as ex:
        logger.warning(f"Identity lookup fell back to the token context: {ex}")

    name = (row.name if row and row.name else officer.name) or "—"
    name_ta = (row.name_tamil if row else None) or ""
    designation = (row.designation if row and row.designation
                   else officer.designation) or "Sub Inspector Surveyor"
    employee_id = (row.employee_id if row else None) or officer.employee_id or "—"
    stage = officer.officer_stage
    stage_long = _DESIGNATION_LONG.get(stage, designation)
    display = (f"{name_ta} ({name})" if name_ta else name) if is_ta else \
              (f"{name} ({name_ta})" if name_ta else name)

    if facet == "name":
        return (f"உங்கள் பெயர் **{display}**." if is_ta
                else f"You are **{display}**.")
    if facet == "designation":
        return (f"உங்கள் பதவி: **{stage_long}**." if is_ta
                else f"Your designation is **{stage_long}**.")
    if facet == "employee_id":
        return (f"உங்கள் பணியாளர் எண்: **{employee_id}**." if is_ta
                else f"Your employee ID is **{employee_id}**.")
    if facet == "login_id":
        # "my login id" / "my user id" — show the login email used to sign in.
        email = (row.email if row else None) or getattr(officer, "email", None) or "—"
        if is_ta:
            return (f"உங்கள் உள்நுழைவு ஐடி (Login ID) உங்கள் மின்னஞ்சல் ஆகும்: **{email}**.\n"
                    f"பணியாளர் எண்: **{employee_id}**.")
        return (f"Your login ID is your registered email: **{email}**.\n"
                f"Employee ID: **{employee_id}**.")

    if facet != "profile":
        # "Who am I?" — name and post, one line.
        return (f"நீங்கள் **{display}**, {stage_long}." if is_ta
                else f"You are **{display}**, {stage_long}.")

    # Only "my profile" / "my details" asks for the whole card.
    email = (row.email if row else None) or officer.email or "—"
    mobile = (row.mobile if row else None) or ""
    jur_line, open_count = "", None
    try:
        jur = (await _build_jurisdiction_summary(db, officer)).get("jurisdiction") or {}
        jur_line = _format_jurisdiction_line(jur)
        open_count = jur.get("active_applications")
    except Exception as ex:
        logger.warning(f"Identity jurisdiction lookup failed: {ex}")

    if is_ta:
        lines = [f"• **பெயர்**: {display}",
                 f"• **பதவி**: {stage_long}",
                 f"• **பணியாளர் எண்**: {employee_id}",
                 f"• **மின்னஞ்சல்**: {email}"]
        if mobile:
            lines.append(f"• **கைபேசி**: {mobile}")
        if jur_line:
            lines.append(f"• **அதிகார வரம்பு**: {jur_line}")
        if open_count is not None:
            lines.append(f"• **நிலுவை விண்ணப்பங்கள்**: {open_count}")
        return "உங்கள் விவரங்கள்:\n" + "\n".join(lines)

    lines = [f"• **Name**: {display}",
             f"• **Designation**: {stage_long}",
             f"• **Employee ID**: {employee_id}",
             f"• **Email**: {email}"]
    if mobile:
        lines.append(f"• **Mobile**: {mobile}")
    if jur_line:
        lines.append(f"• **Jurisdiction**: {jur_line}")
    if open_count is not None:
        lines.append(f"• **Open applications assigned to you**: {open_count}")
    return "Your profile:\n" + "\n".join(lines)

# A plural pronoun is proof on its own that the question points backwards.
_PLURAL_BACKREF_RE = re.compile(
    r"\b(?:they|them|their|those|these|the\s+same\s+ones?|all\s+of\s+them)\b"
    r"|அவை|அவற்றை|அவற்றின்",
    re.IGNORECASE,
)
# Without a pronoun, the fragment still reads as a continuation when it asks
# something a list answers -- "how many are approved", "which one is the
# latest".
_LIST_FOLLOWUP_CUE_RE = re.compile(
    r"\bhow\s+many\b|\bwhich\s+(?:one|ones|of|are)\b|\bany\s+of\b|\bwhat\s+about\b"
    r"|\bthe\s+two\b|\bboth\b"
    r"|எத்தனை|எந்தது|எவை",
    re.IGNORECASE,
)
_TYPE_TOKEN_RE = re.compile(r"(?<![a-z0-9])(nisd|isd)(?![a-z0-9])|(?<!\d)(015[34])(?!\d)", re.IGNORECASE)


_ONLY_TYPE_RE = re.compile(r"^\s*(?:only|just)\s+(\w+)\s*[?.!]*\s*$|^\s*(\w+)\s+(?:only|mattum|alone)\s*[?.!]*\s*$",
                           re.IGNORECASE)


def _only_type_after_empty_list(message: str, chat_history: list) -> str:
    """"only isd" right after a list that came back empty has no rows to narrow,
    so it re-asks that question for the type ("... in June 2025 ISD")."""
    m = _ONLY_TYPE_RE.match(message or "")
    canon = _bare_type_word(m.group(1) or m.group(2)) if m else None
    if not canon or not chat_history:
        return message
    last_answer = next((h.get("content") or "" for h in reversed(chat_history)
                        if (h.get("role") or "user") == "assistant"), "")
    if "no applications found" not in _flatten_plain(last_answer).lower():
        return message
    prev = _last_subject_message(chat_history)
    if not prev or _bare_type_word(prev) is not None:
        return message
    if _TYPE_TOKEN_RE.search(prev):
        return _TYPE_TOKEN_RE.sub(canon, prev)
    return f"{prev} {canon}"


def _flatten_plain(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")


async def _type_swap_followup(db, session_id, chat_history, message: str) -> str:
    """A bare type word after another question is that question about the other
    type: "should I go for a field visit for ISD?" then "nisd" asks the same about
    NISD. After a listing it filters the listing; with nothing to continue it asks
    what the type is. Never the field-visit summary it used to be parsed as."""
    canon = _bare_type_word(message)
    if not canon:
        return message
    # "what is ISD" / "define NISD" / "what is 0153" is a definition, not "and the other type?"
    if re.match(r"\s*(?:what\s+is|what\s+are|what's|whats|define|explain|meaning\s+of)\s+(?:a\s+|an\s+|the\s+)?\w+\s*\??\s*$",
                message or "", re.IGNORECASE):
        return message
    prev = _last_user_message(chat_history) or ""
    if prev and _bare_type_word(prev):
        # "show isd" -> "and nisd" -> "back to isd": the question being re-typed
        # is the last one that named a subject, not the previous type fragment.
        prev = next((h["content"].strip() for h in reversed(chat_history or [])
                     if (h.get("role") or "user") == "user" and (h.get("content") or "").strip()
                     and not _bare_type_word(h["content"]) and _OWN_SUBJECT_RE.search(h["content"])), prev)
    if not prev and session_id:
        try:
            row = (await db.execute(
                select(ChatMessage.content)
                .where(ChatMessage.session_id == session_id, ChatMessage.role == "user",
                       messages_since_session_start(session_id))
                .order_by(ChatMessage.created_at.desc()).limit(1))).first()
            prev = (row[0] if row else "") or ""
        except Exception:
            prev = ""
    prev = prev.strip()
    if prev and _bare_type_word(prev) is None and len(prev.split()) <= 16:
        _prev_type_match = _TYPE_TOKEN_RE.search(prev)
        if _prev_type_match and _bare_type_word(_prev_type_match.group(0)) == canon:
            # The prior question already named THIS type -- "tell me about ISD"
            # then bare "ISD" again is not a swap to the other type, it is the
            # same question re-typed. Substituting canon for canon reproduces
            # "tell me about ISD" verbatim, and that phrasing does not reach
            # service_code_lookup the way "what is ISD" does, so a genuine
            # repeat silently fell to the LLM fallback instead of the
            # deterministic definition. Asked directly instead.
            return f"what is {canon}"
        if _prev_type_match:
            return _TYPE_TOKEN_RE.sub(canon, prev)
        if re.search(r"\b(?:show|list|how\s+many|count|pending|approved|rejected|applications?|apps?|my)\b", prev, re.IGNORECASE):
            return f"{prev} {canon}"
    return f"what is {canon}"


def _fragment_typo_fix(message: str) -> str:
    """A short message shaped like a follow-up ("aplicant details", "how may are ISD",
    "which one is not sheduled", "பழயது எது", "blocs enna") with its slips corrected,
    so every later rule sees the words it matches on. Longer messages, and messages
    that are not follow-up shaped, are left exactly as typed."""
    if _contentless_message(message) == "clear":
        return message  # "wipe the conversation" must not be spell-corrected into "wife the ..."
    text = re.sub(r"^\s*(?:wat|wht|waht|whta)\b", "what", message or "", flags=re.IGNORECASE)
    _n_words = len((text or "").split())
    if not text or _n_words > 18:
        return text or message
    try:
        # a fragment (10 words or fewer) that already reads as a follow-up; longer messages must
        # earn their correction by turning into something recognised (below)
        shaped = _n_words <= 10 and (fctx.classify(text) != fctx.FOLLOWUP_NONE or _bare_type_word(text))
        fixed = fctx.correct_spelling(text)
        if not shaped:
            # A slip can hide the very words that make a message follow-up shaped, so the
            # corrected text is judged instead -- and used only if it turns out to be a
            # follow-up / negation / sort / visit-table request, or reaches a real intent
            # where the slip left "general_query". Anything else stays exactly as typed.
            if fixed == text or not _control_shaped(fixed, text):
                return text
    except Exception:
        return text
    return fixed or text


def _control_shaped(fixed: str, typed: str) -> bool:
    return bool(
        fctx.classify(fixed) != fctx.FOLLOWUP_NONE
        or fctx.normalise_sort_negation(fixed)[0] != fixed
        or fctx.negation_normalise(fixed) != fixed
        or _parse_negation(fixed)[0] or _parse_negation(fixed)[1]
        or _FV_STATUS_FRAGMENT_RE.fullmatch(fixed.strip().lower().rstrip("?.! "))
        or _ADD_COLUMNS_RE.fullmatch(fixed.strip().lower().rstrip("?.! "))
        or (parse_intent(fixed) != "general_query" and parse_intent(fixed) != parse_intent(typed))
        or (_igrs_can_rule_topic(fixed) and not _igrs_can_rule_topic(typed))
        # a domain question (SIS words or an application / survey number): the correction only ever
        # replaces a non-word by a cue word, so it is safe to use
        or _has_domain_evidence(fixed))


def _attachment_turn_is_not_about_a_file(message: str) -> bool:
    """Commands and messages with no question in them ("clear", "ok", "help", a
    keyboard mash, a request to change data, "who are you", "weather") are never
    about an uploaded file, so the attachment planner is not even asked."""
    if attachment_qa.is_negative_correction(message) or attachment_qa.is_project_relevance_request(message):
        return False
    return bool(_contentless_message(message) or _unknown_message_kind(message)
                or _is_mutation_request(message) or _is_capability_question(message)
                or _is_out_of_scope(message))


_TANGLISH_ALL_RE = re.compile(
    r"\b(?:ellam|ellaam|elam|ellaa|ella|ellarum|ellarukum|anaithu|anaithum)\b|எல்லா(?:ம்)?|அனைத்து(?:ம்)?", re.IGNORECASE)


def _all_word_to_english(message: str) -> str:
    """"elam application kami" / "ellaa applications kaattu" / "எல்லா விண்ணப்பங்கள்" ask
    for ALL the applications. The listing rules look for the word "all"; without this
    they never saw it and answered the pending queue instead."""
    if not message or not _TANGLISH_ALL_RE.search(message):
        return message
    return _TANGLISH_ALL_RE.sub("all", message)
_SORT_CLAUSE_RE = re.compile(
    r"\s*,?\s*(?:and\s+)?(?:sorted|sort|ordered|order|arranged|arrange)\s+(?:them\s+)?"
    r"(?:by|on|according\s+to)\s+(?:the\s+)?(?P<field>[a-z ]{2,30}?)"
    r"(?:\s+(?:in\s+)?(?:ascending|descending|asc|desc)(?:\s+order)?)?\s*[?.!]*\s*$",
    re.IGNORECASE)


def _take_sort_clause(message: str) -> str:
    """"show applications sorted by ward descending" -> the list request without
    its ordering clause, with the ordering kept aside for the query.

    Left in, the words of the clause are read as content: "by ward" became the
    applications-by-ward summary, "by fee" a fee lookup, "by date" a column list.
    A fresh listing and a "sort by ..." follow-up then order the same way."""
    _SORT_OVERRIDE.set(None)
    m = _SORT_CLAUSE_RE.search(message or "")
    if not m:
        return message
    head = message[:m.start()].strip()
    sort = extract_sort_order(message[m.start():])
    if not head or not sort or fctx.is_sort_fragment(message):
        return message
    _SORT_OVERRIDE.set(sort)
    return head


def _effective_previous_question(chat_history: list) -> str:
    """The last question with the latest bare period ("and 2025", "in june") that
    followed it applied -- history stores the fragments as typed, so the period
    the officer is actually in is otherwise lost."""
    latest = None
    for h in reversed(chat_history or []):
        if (h.get("role") or "user") != "user":
            continue
        content = (h.get("content") or "").strip()
        if not content:
            continue
        if is_bare_date_scope(content):
            if latest is None:
                latest = extract_date_scope_fragment(content) or content
            continue
        base = content
        if latest:
            base = f"{strip_date_scope_phrases(content)} {latest}".strip()
        return base
    return ""


def _rescope_list_followup(message: str, chat_history: list) -> str:
    """Fold a bare list follow-up back into the question it continues.

    "show applications from CSC" followed by "how many of them are approved"
    becomes "show applications from CSC how many of them are approved", so the
    channel / status / type scope of the first question survives into the
    second instead of resetting to the officer's whole open queue. Returns
    `message` unchanged when it is not a list follow-up.
    """
    if not message or not chat_history:
        return message
    if _names_a_file(message):
        # The message names an attached file, so it is about that file and not
        # a continuation of the previous listing. See `_names_a_file`.
        return message
    # These cues are matched against a spelling-corrected COPY, never against
    # the officer's own words: the corrected text decides whether this is a
    # follow-up, and the original is what travels on to the handlers, which
    # have vocabularies of their own. Without it a single slip ("how many of
    # thm are aproved") stopped looking like a follow-up at all and the scope of
    # the question it continued was silently dropped.
    probe = fctx.correct_spelling(message)
    has_pronoun = bool(_PLURAL_BACKREF_RE.search(probe))
    if not has_pronoun:
        if not _LIST_FOLLOWUP_CUE_RE.search(probe):
            return message
        # A fragment with its own subject is a new question, not a follow-up.
        if _OWN_SUBJECT_RE.search(probe) or len(message.split()) > 8:
            return message
        # A fragment naming a lifecycle status the previous message contradicts
        # ("show my rejected applications" -> "how many are approved?") is a
        # fresh count over the jurisdiction, not a filter of the rejected rows.
        _prev_msg = _last_user_message(chat_history) or ""
        _frag_status = _named_lifecycle_status(probe)
        if _frag_status:
            _prev_status = _named_lifecycle_status(fctx.correct_spelling(_prev_msg))
            if _prev_status and _prev_status != _frag_status:
                # "what about rejected" / "and rejected" swaps the status the
                # previous question named; a longer fragment is a fresh question.
                if len(message.split()) <= 3 and _resolve_prev_intent(chat_history) in _LIST_FOLLOWUP_INTENTS:
                    _prev_eff = _effective_previous_question(chat_history)
                    _pm = _LIFECYCLE_STATUS_RE.search(_prev_eff)
                    _fm = _LIFECYCLE_STATUS_RE.search(probe)
                    if _pm and _fm:
                        _stem = _pm.group(1).lower() in ("approve", "reject")
                        _rep = re.sub(r"ed$", "", _fm.group(1).lower()) if _stem else _fm.group(1)
                        return _prev_eff[:_pm.start()] + _rep + _prev_eff[_pm.end():]
                return message
        # Same for a contradicting application type ("...ISD..." -> "how many
        # approved NISD?") and for a period the previous message did not name
        # ("how many did I approve in 2024?" after an unscoped listing).
        _frag_type = _named_app_type(probe)
        if _frag_type:
            _prev_type = _named_app_type(_prev_msg)
            if _prev_type and _prev_type != _frag_type:
                return message
        _frag_periods = _named_periods(message)
        if _frag_periods and not (_frag_periods & _named_periods(_prev_msg)):
            return message
    if _resolve_prev_intent(chat_history) not in _LIST_FOLLOWUP_INTENTS:
        return message
    prev_question = _effective_previous_question(chat_history).strip()
    if not prev_question or prev_question.lower() == message.strip().lower():
        return message
    if _resolve_prev_intent(chat_history) == "field_visits" and not re.search(r"visit|ஆய்வு", message, re.IGNORECASE):
        return f"{message} field visits"
    return f"{prev_question} {message}"


# Questions about an attached file are routed by
# backend/services/attachment_qa.py — see plan_answer(). The old
# keyword gate and whole-file prompt that lived here are gone: an
# attachment is now answered from retrieved, cited evidence.


# ── Implicit follow-ups resolved against stored context ──────────────────────
# See backend/services/followup_context.py for why this exists. The three
# functions below are the orchestration half: load the record, answer a scoped
# list question from it, and rewrite a singular question so the rest of the
# pipeline sees a self-contained one.


def _followup_out_context(scoped_sd: dict, prior, row_peek: bool = False, last_peeked: str = None):
    """The context a list follow-up leaves for the turn after it, as JSON.

    `build_context` returns a dataclass; `structured_data` is a JSONB column.
    Passing the object straight through made every list follow-up fail its
    INSERT -- so a second follow-up in a row ("total fee?" then "how many are
    ISD?") found no record of the first and fell back to the officer's whole
    desk. The scoped set is preferred; the prior context is kept when the
    re-query narrowed to nothing, so the set stays in view.

    `row_peek=True` (an ordinal field lookup, "what is the subdivision number
    in the second row?") must NOT narrow the carried list to the one row it
    just read -- the table the officer is looking at has not changed, only
    the question did. The prior context is kept untouched, exactly as if
    this turn had never run, so "exclude the second row" right after it
    still counts positions against the same table the "second row" was
    named from.

    `last_peeked` records WHICH row that was, as `filters.last_peeked` on the
    otherwise-untouched prior context -- "What about the second one?" then
    "Give ITS application number, survey number and status." named a plain
    singular back-reference ("its"), not another ordinal/list operation, and
    with nothing remembering which row was just looked at, that "its"
    resolved against the whole table instead of row 2 alone. The table
    itself still has not changed (the comment above is still correct for
    "exclude the second row" and similar), so `application_numbers` is not
    touched -- only this one extra breadcrumb is added.
    """
    if row_peek:
        if not prior:
            return None
        if last_peeked:
            import dataclasses as _dc
            prior = _dc.replace(prior, filters={**(prior.filters or {}), "last_peeked": last_peeked})
        return prior.to_json()
    ctx = fctx.build_context("followup_list", scoped_sd)
    if ctx and ctx.application_numbers:
        # `get_applications_by_numbers` does not echo back what it was filtered
        # to, so the fresh ctx has an empty `query_type` / `filters`. Carry the
        # PRIOR listing's scope forward -- otherwise a chained follow-up
        # ("show my rejected applications" -> "which is most recent?" -> "how
        # many are ISD?") loses the "rejected" scope on the second hop and the
        # third re-queries with no status, dropping every rejected row.
        if prior is not None:
            if not ctx.query_type and getattr(prior, "query_type", None):
                ctx.query_type = prior.query_type
            merged = dict(getattr(prior, "filters", None) or {})
            merged.update(ctx.filters or {})
            ctx.filters = merged
        return ctx.to_json()
    return prior.to_json() if prior else (ctx.to_json() if ctx else None)


async def _mark_session_cleared(db, session_id, officer) -> None:
    """A typed "clear": nothing said before this moment may be continued. The frontend
    opens a fresh session, but an API client may keep the same one, so the session's
    start is moved to now. Nothing is deleted: the older rows stay in chat_messages
    for audit, and every reader of the transcript or its context filters them out
    through `messages_since_session_start()`."""
    try:
        sess = (await db.execute(select(ChatSession).where(ChatSession.id == session_id))).scalar_one_or_none()
        if sess is not None and officer is not None and str(sess.officer_id) == str(officer.officer_id):
            sess.started_at = datetime.now(timezone.utc)
            await db.commit()
    except Exception as exc:  # never fail a clear
        logger.warning(f"could not mark the session cleared: {exc}")
        try:
            await db.rollback()
        except Exception:
            pass


# A fragment of the upload endpoint's own confirmation text -- unique enough
# that a client-replayed transcript's last entry is recognisable as "the
# officer just attached a file", without needing the bot to tag the turn.
_UPLOAD_CONFIRM_RE = re.compile(
    r"attached this session|no selectable text found", re.IGNORECASE)


def _context_from_history(chat_history: list):
    """Fallback context derived from the history the client sent.

    The stored record is the better source, but it is not always there: the
    frontend keeps the transcript in sessionStorage and replays it, so a turn
    can arrive with a full conversation and a session whose rows have not been
    written (a new session id, or a save that failed). Falling back keeps those
    turns working instead of answering them with a clarification.

    Only the officer's OWN turns are read, and only when they name exactly one
    application -- the same rule `_officer_turns` already applies, for the same
    reason: a number the bot quoted back is not one the officer chose, and two
    different numbers in view is precisely the case that must be asked about
    rather than guessed.
    """
    hist = chat_history or []
    if hist:
        _last = hist[-1]
        _last_content = (_last.get("content") or "") if isinstance(_last, dict) else str(_last)
        _last_role = ((_last.get("role") if isinstance(_last, dict) else None) or "user")
        if _last_role != "user" and _UPLOAD_CONFIRM_RE.search(_last_content):
            # The immediately preceding turn was an upload confirmation -- the
            # conversation moved to that file. Mirrors the same-named guard in
            # `_load_followup_context`: an officer turn further back that named
            # an application is not what a bare "it" means right after an
            # upload, even though the client-replayed transcript still carries
            # it within the last few turns.
            return None
    numbers: List[str] = []
    for msg in reversed(_officer_turns(chat_history or [])[-6:]):
        for m in _ANY_APP_NUMBER_RE.finditer(msg.get("content") or ""):
            v = m.group(0).upper()
            if v not in numbers:
                numbers.append(v)
    if len(numbers) == 1:
        return fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION,
                                    application_numbers=numbers)
    return None


# A message that types out a file name ("in register.csv, what is the total
# fee?") names its own subject: that file. CLAUDE.md states the rule as
# "naming a file overrides everything", but the follow-up layer runs BEFORE
# `attachment_qa.plan_answer` and claimed such a turn first -- so
# "in register.csv, what is the total fee?" asked right after a listing was
# answered as a follow-up over those 52 applications ("₹1,600.00 across 51 of
# those 52"), from the register, with the named file never consulted. The
# extension list is the one the upload endpoint accepts.
_FILENAME_IN_MESSAGE_RE = re.compile(
    r"(?<![\w.-])[\w][\w.()-]{0,80}\.(?:txt|csv|pdf|docx|doc|xlsx|xls)\b",
    re.IGNORECASE)


def _names_a_file(message: str) -> bool:
    """Whether the officer typed something shaped like an attached file name."""
    return bool(_FILENAME_IN_MESSAGE_RE.search(message or ""))


# ── "give me the details" over the rows just listed ──────────────────────────
# A details request that names no application and no single field, made while a
# listing is still on screen, is about THOSE rows. Three different layers used
# to answer it as something else:
#
#   "i need details of both the applications" -> "2 of those 2 application(s)
#       are pending."            (the aggregate list answer -- a count, not the
#                                 details that were asked for)
#   "i need applicant details"  -> "That answer covered 2 applications ...
#                                   Which one do you mean?"  (the singular
#                                 resolution, asked over and over)
#   "can i get the full details" -> 85 SECONDS in the agent layer, describing an
#                                 application from three turns earlier, because
#                                 nothing claimed the turn at all.
#
# All three are answered here instead, by naming the carried rows so the
# ordinary multi-application detail path renders one detail table per row --
# exactly what "can i get those details" already did.
_CARRIED_DETAIL_WORDS = (
    "detail", "full record", "more info", "more information",
    "elaborate", "expand", "break down", "breakdown",
    "விவர", "vivara", "vivaram",
)


def _wants_carried_list_details(message: str, context) -> bool:
    """True for a bare details request against the listing still in view."""
    if not context or context.entity != fctx.ENTITY_APPLICATION_LIST:
        return False
    nums = context.application_numbers or []
    # A list longer than the detail cap is better answered by asking which one
    # than by fifteen tables. A single row is expanded the same way: "applicant
    # details" / "both" over one carried file is that file's details, not a count.
    if not (1 <= len(nums) <= _MAX_LISTED_DETAILS):
        return False
    text = (message or "").strip()
    if not text or _ANY_APP_NUMBER_RE.search(text):
        return False
    low = text.lower()
    # "சர்வே 146 விவரங்களைக் காட்டு", "details of survey 5", "details 2026-07-01 to ...":
    # a details word beside a subject of its own is a fresh question.
    if (fctx._has_own_subject(low) and not fctx._PLURAL_BACKREF_RE.search(low)
            and not fctx._BACKREF_RE.search(low)):
        return False
    # "ward details" / "block details" ask for that column of the listing.
    if re.search(r"\b(?:ward|block|district|taluk|town)\s+details?\b", low):
        return False
    if any(w in low or w in text for w in _CARRIED_DETAIL_WORDS):
        return True
    # "i need both" / "both of them please" -- a fragment that asks for the
    # whole list and names no quality to measure it by. The aggregate answer
    # for one of those reads "2 of those 2 application(s) are matching", which
    # states nothing; the records themselves are what was asked for.
    return bool(_BARE_WHOLE_LIST_RE.fullmatch(low.rstrip("?.! ")))


# "i need both", "give me all of them", "both please" -- the whole list, with
# no field and no aggregate word in sight. "all applications" is deliberately not
# here: it names its own subject, so it is a fresh request for every application.
_BARE_WHOLE_LIST_RE = re.compile(
    r"(?:please\s+|pls\s+|kindly\s+)?"
    r"(?:i\s+(?:need|want)|give\s+me|show\s+me|get\s+me|send\s+me|i\s+need\s+to\s+see)?\s*"
    r"(?:the\s+)?(?:both|all|everything|both\s+of\s+them|all\s+of\s+them|"
    r"both\s+applications?)"
    r"(?:\s+please)?",
    re.IGNORECASE,
)


# "applicant details", "applicant info", "who are the applicants" -- the
# officer is asking about the PEOPLE, not about the files. Answering with the
# application card (type / status / stage) answers a question nobody asked,
# and the word "applicant" is the only thing that tells the two apart.
_APPLICANT_FOCUS_WORDS = (
    "applicant", "applicants", "applicant's", "applicants'",
    "விண்ணப்பதாரர்", "vinnappadhaarar", "vinnappathaarar",
)


def _asks_applicant_focus(message: str) -> bool:
    """True when a details request is about the applicant rather than the file."""
    low = (message or "").lower()
    return any(w in low or w in (message or "") for w in _APPLICANT_FOCUS_WORDS)


_APPLICANT_FIELD_WORDS_RE = re.compile(
    r"\b(?:name|mobile|phone|number|address|gender|age|email|father|relative|"
    r"aadhaar|aadhar|occupation)\b|பெயர்|முகவரி|தொலைபேசி", re.IGNORECASE)
_DETAILS_WORD_RE = re.compile(r"\b(?:details?|info|information|profile)\b|விவரம்|விவரங்கள்",
                              re.IGNORECASE)


def _wants_applicant_card(message: str, numbers: list) -> bool:
    """"applicant details of <one application>": the whole applicant card, not one
    field. A named field ("applicant name of ...") stays a field lookup."""
    if len(numbers) != 1 or not _asks_applicant_focus(message):
        return False
    return bool(_DETAILS_WORD_RE.search(message or "")) and not _APPLICANT_FIELD_WORDS_RE.search(message or "")


def _applicant_card_text(found: list, is_tamil: bool = False) -> str:
    """The applicant name / mobile / address of each listed file, from the register."""
    na = "பதிவு இல்லை" if is_tamil else "not recorded"
    lines = []
    for d in found[:8]:
        def val(k):
            v = d.get(k)
            return na if v in (None, "", "N/A") else str(v)
        if is_tamil:
            lines.append(f"{d.get('application_number')}: {val('applicant_name')}, "
                         f"தொலைபேசி {val('applicant_mobile')}, முகவரி {val('applicant_address')}")
        else:
            lines.append(f"{d.get('application_number')}: {val('applicant_name')}, "
                         f"mobile {val('applicant_mobile')}, address {val('applicant_address')}")
    head = ("விண்ணப்பதாரர் விவரங்கள்:" if is_tamil else "Applicant details:")
    return head + "\n" + "\n".join(lines)


def _carried_details_message(context, message: str = "") -> str:
    """The self-contained request the follow-up stands for.

    Two things the officer said are carried through rather than dropped:

    * a position -- "details of the first one" means that one row, not all of
      them. `_wants_carried_list_details` claims the turn before the ordinal
      layer is reached, so without this the ordinal was silently discarded and
      every listed application was expanded.
    * "applicant" -- "applicant details" asks about the people on the files.
      The word is kept in the rewritten message so the field lookup downstream
      (single application) and `detail_focus` (several) both see it. A bare
      continuation ("both", "give me all of them") names no field of its own,
      so it inherits the focus the previous turn's own answer carried
      forward in `context.filters["detail_focus"]` -- without this, "applicant
      details" followed by "both" reverted to the application card (type /
      status / stage) instead of staying on the applicants.
    """
    nums = (context.application_numbers or [])[:_MAX_LISTED_DETAILS]
    picked = _pick_from_listing(message or "", nums)
    if picked:
        nums = [picked]
    wants_applicant = (_asks_applicant_focus(message)
                        or (context.filters or {}).get("detail_focus") == "applicant")
    lead = "applicant details" if wants_applicant else "show details"
    return f"{lead} for " + " and ".join(nums)


def _followup_is_self_contained(message: str) -> bool:
    """Whether `message` names its own subject and so is never a continuation.

    A thin wrapper over `SELF_CONTAINED_INTENTS` with two carve-outs. A bare
    "what is the survey number?" / "what is the group survey number?" /
    "சர்வே எண் என்ன?" parses as `survey_detail`, but it names no survey -- it is
    a follow-up about the application already in view, and treating it as
    self-contained sent it to the survey RAG path, which answered
    "No records found". A real standalone survey question carries a number.
    And a message naming a file is always self-contained, whatever it parses
    as -- see `_names_a_file`.
    """
    if _cm_strong(message) or any(isinstance(k, tuple) for k in _fline_qa.parse(message)["keys"]):
        return True  # a record question; the application gate finds its application
    if _names_a_file(message):
        return True
    # A NAMED district in a code question ("district code of Madurai") is complete.
    if ((_DISTRICT_NAME_RE.search((message or "").lower()) or _DISTRICT_NAME_TA_RE.search(message or ""))
            and re.search(r"code|குறியீடு|taluks?|wards?|blocks?|towns?", (message or "").lower())):
        return True
    # "remove row 2" drops a row from the table on screen -- a continuation, not a
    # request to change the register.
    if ((_is_mutation_request(message) and fctx.classify(message) != fctx.FOLLOWUP_LIST_REFINE)
            or _unknown_message_kind(message) not in (None, "bare_ref") or _contentless_message(message)):
        return True
    _lw = (message or "").lower().strip()
    if len(_lw.split()) >= 4 and re.match(
            r"(?:explain|describe|define|elaborate|why|how\s+(?:do|does|can|to|should)|"
            r"what\s+is\s+the\s+difference)\b", _lw) \
            and not re.match(r"why\s+(?:is|was|has)\s+(?:it|this|that)\b", _lw):
        return True
    # A general-rule question about the survey/sub-division SYSTEM, not a
    # data follow-up -- "how many sub-divisions already exist under A survey
    # number" (no number given, asking about the rule) opens with "how
    # many", which the generic-opener check above doesn't cover (it only
    # covers "how do/does/can/to/should"), and parse_intent now routes it to
    # general_query (rag.py's `_is_process_question`), a shape with no entry
    # in SELF_CONTAINED_INTENTS. Without this it was judged NOT
    # self-contained and treated as an orphan list follow-up -- "I do not
    # know which application or list you mean" -- for a question that never
    # depended on one. Same patterns as `_is_process_question`, kept in sync.
    if re.search(
        r'\balready\s+exist\b|\bhow\s+are\b.{0,30}\bassigned\b|\bwhat\s+counts\s+as\b'
        r'|\bdoes\s+(?:the|a)\b.{0,30}\bbecome\s+the\s+final\b|\bis\s+(?:isd|nisd)\s+a\b'
        r'|\bwhat\s+happens\b|\bwhat\s+comes\b|\bwhat\s+kind\s+of\b.{0,40}\b(?:can|could|might|would)\b'
        r'|\bwhat\s+should\s+(?:i|you|we|an?\s+officer)\s+check\b',
        _lw,
    ):
        return True
    # "weather in Chennai" after an application answer is a fresh (off-topic)
    # question; carrying the application onto it hid it from the scope guard.
    if _is_out_of_scope(message) or _is_capability_question(message) or bool(_special_scope_kind(message)):
        return True
    if _is_today_date_question(message):
        return True
    pi = parse_intent(message)
    if pi not in fctx.SELF_CONTAINED_INTENTS:
        return False
    if pi == "survey_detail" and not re.search(r"\d", message or ""):
        return False
    # A bare "what is the fee?" parses as `service_code_guide` on the word
    # "fee" alone and so counted as self-contained -- which meant that, with an
    # application on screen, the officer asking its fee got the static
    # 3-row service-code table instead of the amount on their file. It is only
    # a guide request when it names what it wants the guide FOR.
    if pi in ("service_code_guide", "fee_lookup") and not re.search(
            r"\b(isd|nisd|service\s*code|015[34])\b|சேவை\s*குறியீடு",
            (message or "").lower()):
        return False
    return True
