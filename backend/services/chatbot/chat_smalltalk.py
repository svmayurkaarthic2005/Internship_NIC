"""
Greetings, acknowledgements, out-of-scope/capability questions, unknown-message handling, sentiment, fee lookup text, and the other small deterministic 'this message asks nothing domain-specific' replies.

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
from .chat_shared import (
    _ADD_COLUMNS_RE,
    _ANY_APP_NUMBER_RE,
    _BARE_REF_RE,
    _COLW,
    _COL_ADD_RE,
    _COL_DROP_RE,
    _COL_LIST,
    _DISTRICT_NAME_RE,
    _DOMAIN_TERMS,
    _FULLFORMS,
    _FULLFORM_BARE_RE,
    _OPINION_RE,
    _TRIM_CHARS,
    _asks_for_channel_list,
    _channels_defined,
    _clear_info_topic,
    _cm_strong,
    _contentless_message,
    _corpus_blob,
    _fullform_term,
    _has_application_back_reference,
    _is_explain_request,
    _is_rule_shaped,
    _period_from_message,
    _unknown_fullform,
)
from backend.utils.logger import get_logger

logger = get_logger(__name__)




# ─────────────────────────────────────────────────────────────────────────────
# Requests to CHANGE something
#
# The assistant reads the register and never writes it -- `readonly_guard`
# enforces that at the database session, so the data is safe whatever is asked.
# What the guard cannot stop is the assistant *claiming* to have done it. Asked
# to "clear the knowledge base and re-ingest", llama3.1:8b replied "The
# knowledge base has been cleared." Nothing was cleared, and the officer had no
# way to know that: a false report of a destructive action is its own integrity
# failure, and arguably worse than the action, because it is believed.
#
# So a mutation request is answered deterministically, before any handler or
# the agent can improvise one.
#
# The match is deliberately narrow: the verb must be the FIRST content word,
# after an optional politeness prefix. That is how an imperative is shaped, and
# it is what keeps "which applications were approved" (a question about the
# past) and "when was it last updated" (a field lookup) out of here. Catching a
# real question by mistake would be a worse failure than missing an imperative,
# which merely falls through to the ordinary read-only path.
_MUTATION_VERBS = (
    "delete", "remove", "drop", "truncate", "clear", "wipe", "erase", "purge",
    "reset", "update", "change", "modify", "edit", "overwrite", "rewrite",
    "approve", "reject", "assign", "insert", "add", "create", "register",
    "re-ingest", "reingest", "ingest", "rebuild", "reload", "restore",
    # Workflow actions on a town-survey record the assistant cannot perform:
    # freezing / revoking a ward or block (service code 0163) and its reversal.
    "freeze", "unfreeze", "revoke",
    # Creating or moving a field visit is a change to the register too. The
    # assistant answered "schedule a field visit" with a LISTING of the visits
    # already booked -- an imperative answered with a report, which reads as
    # though something was done.
    "schedule", "reschedule", "book", "arrange", "postpone", "cancel",
    "நீக்கு", "அழிக்க", "மாற்று", "திருத்து", "புதுப்பி", "முடக்கு",
)

# An imperative names its object by NUMBER as often as by noun:
# "approve 2026/0153/28/001854" carries the verb and the record but not the
# word "application", so the object test below missed it entirely and the turn
# fell through to the ordinary read path.
_MUTATION_OBJECT_RE = re.compile(
    r"\b\d{4}\s*/\s*0?\d{3,4}\s*/\s*\d{1,3}\s*/\s*\d{4,6}\b"
    r"|\bsurvey\s*(?:no\.?|number)?\s*\d+", re.IGNORECASE)

# Things in the department's record. A mutation verb aimed at anything else
# ("add a note for me") is not this.
_MUTATION_OBJECTS = (
    "application", "applications", "record", "records", "data", "database",
    "db", "table", "tables", "row", "rows", "register", "registry",
    "knowledge base", "knowledgebase", "corpus", "document", "documents",
    "embedding", "embeddings", "vector", "field visit", "field visits", "visit",
    "survey", "patta", "owner", "owners", "status", "workflow", "subdivision",
    "sub-division", "applicant", "officer", "jurisdiction", "everything", "all",
    # Geography levels -- "freeze ward 102", "revoke block 0015", "change the
    # town name". The verb-first gate above is what keeps ordinary "which ward"
    # / "how many blocks" questions out.
    "ward", "wards", "block", "blocks", "town", "towns", "taluk", "village",
    "parcel", "parcels",
    "விண்ணப்ப", "பதிவ", "தரவ", "ஆவண", "வார்டு", "பிளாக்", "நகர",
)

_POLITE_PREFIX = re.compile(
    r"^(?:please\s+|pls\s+|kindly\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|"
    r"i\s+want\s+you\s+to\s+|i\s+need\s+you\s+to\s+|you\s+should\s+|"
    r"go\s+ahead\s+and\s+|now\s+)+",
    re.IGNORECASE,
)


# Verb-final Tamil / Tanglish imperatives: "delete pannu", "approve pannunga",
# "விண்ணப்பத்தை அங்கீகரி", "ரத்து செய்", "எல்லாத்தையும் நீக்கு".
_TA_TANGLISH_MUTATION_RE = re.compile(
    r"\b(?:delete|remove|approve|reject|cancel|update|change|modify|edit|forward|escalate|assign)\s+"
    r"(?:pannu|panu|pannunga|pannidu|panidu|seiyyu|seyyu|pannungo)\b"
    r"|(?:அங்கீகரி|நிராகரி|ரத்து\s*செய்|நீக்கு|அழி|மாற்று|திருத்து|புதுப்பி|முன்னனுப்பு)\s*[.!?]*$",
    re.IGNORECASE)

# "add the subdivision number after the survey number" / "put block before
# ward" -- a column REORDER/insert request, the same column vocabulary as
# `_ADD_COLUMNS_RE` but with a position instead of a plain list. Named here so
# `_is_mutation_request` can stand aside for it the same way it already does
# for "add ward column": without this, "subdivision"/"survey" (real words in
# `_MUTATION_OBJECTS`, for an actual land-record subdivision/survey) made the
# add-verb-plus-object test fire, and reordering a column on screen was
# refused as "I cannot change the records".
_ADD_REORDER_COLUMN_RE = re.compile(
    r"^(?:also\s+)?(?:add|include|put|move)\s+(?:the\s+)?(?:ward|block|district|taluk|town|status|stage|"
    r"type|survey(?:\s+number)?|sub[\s-]?division(?:\s+number)?|fee|name|applicant(?:\s+name)?|mobile|phone|"
    r"address|date|channel|can|patta)s?\s+(?:before|after)\s+(?:the\s+)?(?:ward|block|district|taluk|town|"
    r"status|stage|type|survey(?:\s+number)?|sub[\s-]?division(?:\s+number)?|fee|name|applicant(?:\s+name)?|"
    r"mobile|phone|address|date|channel|can|patta)s?\s*[.!?]*$", re.IGNORECASE)


def _is_mutation_request(message: str) -> bool:
    """True when the officer is telling the assistant to CHANGE the record."""
    if _clear_info_topic(message):
        return False  # "if I wipe the chat, will my applications go?" asks; it changes nothing
    # "clear all", "erase everything", "delete all messages" are wipe commands
    # for the transcript, and they also read as mutation verbs aimed at
    # "everything". They are answered by the clear path, which runs first in
    # both chat paths -- but nothing should depend on that ordering, so they
    # are excluded here too.
    if _contentless_message(message) == "clear":
        return False
    text = _POLITE_PREFIX.sub("", (message or "").strip().lower()).strip()
    if not text:
        return False
    # Tanglish "cancel ana applications" / "cancel aana" = "the cancelled ones".
    if re.search(r"\bcancel\s+(?:ana|aana|aagi|aana)\b", text):
        return False
    # "add ward column" / "remove the block column" re-shape the table on screen;
    # they change no record.
    if re.search(r"\bcolumns?\b", text):
        return False
    # "add ward and taluk" -- column names only, no number or name of a record --
    # is the same request without the word "column".
    if _ADD_COLUMNS_RE.fullmatch(text):
        return False
    if _ADD_REORDER_COLUMN_RE.match(text):
        return False
    if _TA_TANGLISH_MUTATION_RE.search(text):
        return True
    first = re.split(r"[\s,]+", text)[0].strip(_TRIM_CHARS)
    if first not in _MUTATION_VERBS:
        return False
    # "reject it" / "approve this" / "delete that" -- the object is a pointer to
    # the record just discussed.
    if first in _MUTATION_VERBS and re.fullmatch(
            rf"{first}\s+(?:it|this|that|them|these|those|the\s+same|this\s+one|that\s+one)\s*[.!?]*", text):
        return True
    return (any(obj in text for obj in _MUTATION_OBJECTS)
            or bool(_MUTATION_OBJECT_RE.search(text)))


def _mutation_refusal(language: str) -> str:
    """Say plainly what this assistant does and does not do.

    Naming who *can* make the change matters as much as the refusal: an officer
    who asked has a real task, and "I cannot" without "here is where you can"
    just moves the problem.
    """
    if language in ("ta", "tanglish"):
        return (
            "என்னால் பதிவுகளைப் படிக்க மட்டுமே முடியும் — மாற்ற முடியாது. "
            "விண்ணப்பங்கள், கள ஆய்வுகள், பட்டா விவரங்கள் அல்லது அறிவுத் தளத்தில் "
            "நான் எந்த மாற்றமும் செய்யவில்லை, செய்யவும் மாட்டேன்.\n\n"
            "ஒரு விண்ணப்பத்தை அங்கீகரிக்க, நிராகரிக்க அல்லது முன்னனுப்ப "
            "TAMILNILAM இணையதளத்தைப் பயன்படுத்தவும். "
            "இதற்கிடையில், நான் எந்தத் தகவலையும் காட்ட முடியும் — "
            "நிலை, கள ஆய்வுகள், நிலுவைப் பணிச்சுமை, கட்டணங்கள்."
        )
    return (
        "I can only read the records — I cannot change them. Nothing has been "
        "modified, deleted or reset, and I have no way to do so: applications, "
        "field visits, patta details and the reference documents are read-only "
        "to this assistant.\n\n"
        "To approve, reject or forward an application, use the TAMILNILAM "
        "portal. I can show you anything you need in the meantime — a status, "
        "your field visits, your pending workload, or the fee position."
    )


# `greeting` covers hello AND goodbye -- `parse_intent` has no separate
# `farewell` intent, whatever CLAUDE.md's intent list says -- so the handler
# has to tell them apart. Before this, "bye", "goodbye", "see you" and
# "poitu varen" were all answered "Hello! ... What can I assist you with
# today?", and "nandri" (the Tamil thanks an officer types in Latin script)
# missed the thanks branch, which tested only for "நன்றி" / "thanks" / "thank".
_THANKS_CUES = ("நன்றி", "thanks", "thanx", "thank", "nandri", "nanri")
_FAREWELL_CUES = ("bye", "byee", "goodbye", "gudbye", "see you", "see u",
                  "farewell", "good night", "gud night", "poitu varen",
                  "poyitu varen", "poitu varren", "take care", "signing off",
                  "போய் வர்றேன்", "போய்வர்றேன்", "சென்று வருகிறேன்", "இரவு வணக்கம்")


def _greeting_is_thanks(msg_lower: str) -> bool:
    return any(c in msg_lower for c in _THANKS_CUES)


def _greeting_is_farewell(msg_lower: str) -> bool:
    return any(c in msg_lower for c in _FAREWELL_CUES)


_GREETING_LLM_TIMEOUT = 6.0   # a greeting never holds the officer: slower than this falls to the fixed text
_GREETING_KIND_DESC = {
    "greeting": "greeting",
    "morning": "good-morning greeting",
    "evening": "good-evening greeting",
    "smalltalk": "friendly small-talk question",
    "thanks": "message of thanks",
    "farewell": "farewell",
}


def _greeting_kind(msg_lower: str, semantic_kind=None) -> str:
    if _greeting_is_farewell(msg_lower):
        return "farewell"
    if _greeting_is_thanks(msg_lower):
        return "thanks"
    if "காலை" in msg_lower or "morning" in msg_lower:
        return "morning"
    if "மாலை" in msg_lower or "evening" in msg_lower:
        return "evening"
    return semantic_kind or "greeting"


_TIME_PERIOD_WORDS = {
    "morning": ("morning", "காலை"),
    "afternoon": ("afternoon", "மதியம்"),
    "evening": ("evening", "மாலை"),
    "night": ("good night", "night", "இரவு"),
}


def _greeting_reply_ok(text: str, period: Optional[str] = None) -> bool:
    """A generated greeting must carry no figure (so it cannot state a count,
    date, fee or application number) and no link, and stay short.

    An instruction is a request, not a guarantee -- telling the model the
    real time of day stops it guessing in the common case, but a reply that
    names a DIFFERENT period anyway (the model ignoring the instruction, same
    as it ignores "do not invent a figure" often enough that the digit check
    above exists) is rejected here rather than trusted, the same verify-
    don't-just-instruct rule the rest of this codebase follows for figures.
    """
    if not text or len(text) > 450:
        return False
    if re.search(r"\d|https?:|www\.", text):
        return False
    if period:
        low = text.lower()
        # "night" has no valid opener form at all ("good night" is a
        # farewell), so every period's words are rejected during it --
        # including its own -- rather than only the other three.
        _wrong = _TIME_PERIOD_WORDS if period == "night" else {
            k: v for k, v in _TIME_PERIOD_WORDS.items() if k != period}
        for words in _wrong.values():
            if any(w in low for w in words):
                return False
    return True


_IST = timezone(timedelta(hours=5, minutes=30))


def _ist_time_period() -> str:
    """Morning/afternoon/evening/night by the real clock, India Standard
    Time (UTC+5:30, fixed -- India has no DST, so a static offset is exact,
    and avoids depending on the `tzdata` package this Windows Python install
    does not have)."""
    hour = datetime.now(_IST).hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


async def _llm_greeting(message: str, kind: str, is_tamil: bool):
    """The greeting / thanks / farewell written by the model, or None to make the
    caller use its fixed text. Conversational only: no register data is involved,
    and the reply is rejected if it contains any digit."""
    from backend.services import rag as _rag
    quoted = re.sub(r"\s+", " ", (message or "")).strip()[:120].replace('"', "'")
    lang_rule = ("Write the reply in Tamil script." if is_tamil
                 else "Write the reply in English.")
    # The model has no clock of its own, so a free-floating "Good morning!"
    # was pure style guessing -- it said that at 1:36 AM just as readily as
    # at 9 AM, because nothing in the prompt ever told it what time it
    # actually is. Telling it the real IST period and forbidding any other
    # guess fixes that instead of leaving it to chance.
    period = _ist_time_period()
    if period == "night":
        # "Good night" is a sign-off in English (and in Tamil), not an
        # opener -- saying it to a message that STARTS the conversation
        # ("hi", "namaskaram") reads as the assistant ending the chat before
        # it begins. Morning/afternoon/evening have no such asymmetry, so
        # only this one period is told to stay time-neutral instead.
        time_rule = (
            "It is currently late at night in India. Do NOT say \"good night\" or "
            "name any time of day -- that is a farewell, not a greeting to start "
            "a conversation with. Just greet them plainly."
        )
    else:
        time_rule = (
            f"It is currently {period} in India. If your greeting mentions a time of "
            f"day (good morning/afternoon/evening), it MUST say {period} -- "
            f"never guess a different one."
        )
    prompt = (
        "You are the SIS AI Assistant for Sub Inspector Surveyor officers of the "
        "Tamil Nadu Survey Department.\n"
        f'The officer sent a {_GREETING_KIND_DESC.get(kind, "greeting")}: "{quoted}"\n'
        "Reply in one or two short, friendly sentences. " + lang_rule + " " + time_rule + " You may "
        "use one emoji. Mention briefly that you can help with survey applications, "
        "application status, field visits and workflow rules. Do not state or invent "
        "any application number, count, date, fee or status. Ask at most one question.\n"
        "REPLY:"
    )
    try:
        bound = _rag.llm.bind(options={
            "num_ctx": 2048, "temperature": 0.5, "num_predict": 80,
        })
        reply = await asyncio.wait_for(bound.ainvoke(prompt), timeout=_GREETING_LLM_TIMEOUT)
        text = (getattr(reply, "content", "") or "").strip().strip('"').strip()
    except Exception as exc:
        logger.warning(f"LLM greeting unavailable, using fixed text: {exc}")
        return None
    if not _greeting_reply_ok(text, period):
        logger.warning("LLM greeting rejected (empty, long, or carried a figure); using fixed text")
        return None
    return text


def _unidentified_number_answer(message: str, is_tamil: bool = False) -> str:
    """Say that a number is not recognised, instead of deciding what it is.

    "what is 0015?" names a number and nothing else. It is not a service code
    (only the values in SIS_URBAN_SERVICES are), and nothing else in the message
    says what it is meant to be — so the honest answer is that the number was
    not recognised, with the shapes it could have been. Guessing "service code"
    here is the same failure as letting the LLM answer it.
    """
    m = re.search(r'\d{1,6}', message or "")
    num = m.group(0) if m else (message or "").strip()
    from backend.utils.helpers import SIS_URBAN_SERVICES as _SUS
    _lo, _hi = min(_SUS), max(_SUS)
    if is_tamil:
        return (
            f"'{num}' எதைக் குறிக்கிறது என்று எனக்குத் தெரியவில்லை.\n"
            f"இது நகர்ப்புற சேவை குறியீடு அல்ல (அவை {_lo} முதல் {_hi} வரை), "
            f"விண்ணப்ப எண்ணும் அல்ல (அவை YYYY/0154/28/NNNNNN வடிவில் இருக்கும்).\n"
            f"வார்டு, தொகுதி அல்லது கணக்கெண் என்றால் அதைக் குறிப்பிடுங்கள் — "
            f"எ.கா. 'வார்டு {num}', 'தொகுதி {num}', 'கணக்கெண் {num}'."
        )
    return (
        f"I could not identify '{num}' — I do not know what it refers to.\n"
        f"It is not a TAMILNILAM urban service code (those run {_lo}-{_hi}), and "
        f"it is not an application number (those are shaped YYYY/0154/28/NNNNNN).\n"
        f"If you meant a ward, block or survey number, please say which — for "
        f"example \"ward {num}\", \"block {num}\" or \"survey {num}\"."
    )


async def _fee_lookup_data(db, officer, message: str, is_tamil: bool = False) -> dict:
    """Recorded fee for the type / channel the officer named, from the register and service_fees table."""
    from backend.services.rag import extract_submission_channels
    from backend.services.postgres import get_service_code_fees
    low = message.lower()
    types = []
    if re.search(r"\bnisd\b|\b0153\b", low):
        types.append("NISD")
    if re.search(r"(?<![a-z])isd\b|\b0154\b", low):
        types.append("ISD")
    chans = extract_submission_channels(message)
    channel = chans[0] if len(chans) == 1 else None
    groups = []
    for t in (types or ["ISD", "NISD"]):
        d = (await get_recent_fees(db, officer, application_type=t, channel=channel))["fee_lookup"]
        if d.get("total_applications"):
            groups.append(d)

    # Specific service code fee lookup from live service_fees table
    code_match = re.search(r"\b01[5-9]\d\b", low)
    code_fee_info = None
    if code_match and db:
        c_code = code_match.group(0)
        c_fees = await get_service_code_fees(db, [c_code])
        if c_code in c_fees:
            code_fee_info = c_fees[c_code]

    data = {"fee_lookup": {"groups": groups, "channel": channel,
                           "asked_types": types, "code_fee_info": code_fee_info}, "query_type": "Recorded Fee"}
    data["fee_lookup"]["summary"] = _fee_lookup_text(data, is_tamil)
    return data


def _fee_lookup_text(structured_data, is_tamil: bool = False) -> str:
    """Deterministic sentence(s) for a fee lookup -- every figure is grounded in the database."""
    fl = (structured_data or {}).get("fee_lookup", {})
    groups = fl.get("groups", [])
    who = fl.get("channel")
    code_fee = fl.get("code_fee_info")
    lines = []

    if code_fee:
        c_name = code_fee.get("service_name", "")
        c_sc = code_fee.get("service_code", "")
        c_disp = code_fee.get("fee_display", "")
        c_total = code_fee.get("total_fee", "")
        c_sla = code_fee.get("sla_days_ta" if is_tamil else "sla_days", "")
        if is_tamil:
            lines.append(f"சேவை {c_sc} ({c_name}): தரவுத்தளத்தின்படி சட்டப்பூர்வ கட்டணம் {c_disp} (மொத்தம் {c_total}), SLA கால அளவு {c_sla}.")
        else:
            lines.append(f"Service {c_sc} ({c_name}): Statutory fee as recorded in database (`service_fees` table) is {c_disp} (Total {c_total}), SLA {c_sla}.")

    if not groups and not code_fee:
        return ("உங்கள் அதிகார எல்லையில் பொருந்தும் விண்ணப்பங்கள் இல்லை, எனவே பதிவான கட்டணம் இல்லை."
                if is_tamil else
                "No matching applications in your jurisdiction, so no fee is on record.")

    for g in groups:
        name = g["application_type"] + (f" ({who})" if who else "")
        if not g["latest"]:
            lines.append(
                (f"{name}: எந்த விண்ணப்பத்திலும் கட்டணம் பதிவாகவில்லை." if is_tamil
                 else f"{name}: none of the {g['total_applications']} application(s) has a fee recorded."))
            continue
        vals = sorted({x["fee_amount"] for x in g["latest"]})
        amt = ", ".join(f"\u20b9{v:,.2f}" for v in vals)
        newest = g["latest"][0]
        if is_tamil:
            t = (f"{name}: பதிவேட்டின்படி சமீபத்திய {len(g['latest'])} கட்டணப் பதிவுகளில் {amt} "
                 f"(புதியது {newest['application_number']}, {newest['submission_date']}).")
            if g["newer_without_fee"]:
                t += f" அதைவிடப் புதிய {g['newer_without_fee']} விண்ணப்பங்களில் கட்டணம் பதிவாகவில்லை."
        else:
            t = (f"{name}: as recorded, the newest {len(g['latest'])} files with a fee show {amt} "
                 f"(latest {newest['application_number']}, {newest['submission_date']}).")
            if g["newer_without_fee"]:
                t += f" {g['newer_without_fee']} newer file(s) have no fee recorded."
        lines.append(t)
    lines.append("கட்டணம் மாற்றப்படலாம்; இவை தரவுத்தளத்தில் உள்ள தொகைகள்." if is_tamil
                 else "Fees can be revised dynamically; these are the amounts recorded in the database tables.")
    return "\n".join(lines)


_CLEAR_INFO_TEXT = {
    "effect": ("Clearing removes this conversation from your screen and this browser and starts a new, empty one. "
               "It changes nothing in the register: no application, field visit or other record is touched. "
               "I won't remember what was said before it.",
               "உரையாடலை அழித்தால் இந்த உரையாடல் உங்கள் திரையிலிருந்தும் இந்த உலாவியிலிருந்தும் நீங்கி, புதிய வெற்று உரையாடல் தொடங்கும். "
               "பதிவேட்டில் எதுவும் மாறாது — எந்த விண்ணப்பமும், கள ஆய்வும் பிற பதிவும் தொடப்படாது. அதற்கு முன் பேசியது எனக்கு நினைவில் இருக்காது."),
    "data": ("No. Clearing only removes the chat. Your applications, field visits and all other records in the register stay exactly as they are "
             "- I can only read them, never change or delete them.",
             "இல்லை. அழிப்பது உரையாடலை மட்டுமே. உங்கள் விண்ணப்பங்கள், கள ஆய்வுகள் மற்றும் பதிவேட்டின் அனைத்து பதிவுகளும் அப்படியே இருக்கும் — "
             "நான் அவற்றைப் படிக்க மட்டுமே முடியும், மாற்றவோ அழிக்கவோ முடியாது."),
    "recover": ("No. Once a conversation is cleared, the earlier messages won't come back on your screen and I can't bring them back. "
                "Just ask again - I'll answer from the register.",
                "இல்லை. உரையாடல் அழிக்கப்பட்டால் முந்தைய செய்திகள் திரையில் மீண்டும் வராது; அவற்றை என்னால் மீட்க முடியாது. "
                "மீண்டும் கேளுங்கள் — பதிவேட்டிலிருந்து பதில் சொல்கிறேன்."),
    "remember": ("No. After a clear I start with nothing from the earlier chat, so \"it\", \"the 2nd one\" or \"how many of them\" will "
                 "have nothing to point at until you ask for a list again. I won't remember anything said before.",
                 "இல்லை. அழித்த பிறகு முந்தைய உரையாடலிலிருந்து எதுவும் என்னிடம் இருக்காது; எனவே \"அது\", \"2-வது\" போன்றவை எதையும் குறிக்காது — "
                 "மீண்டும் பட்டியல் கேட்க வேண்டும். முன்பு பேசியது எனக்கு நினைவில் இருக்காது."),
    "how": ("Type \"clear\" (or \"clear chat\", \"new chat\", \"delete the conversation\"), or press New Chat at the top of the page.",
            "\"clear\" (அல்லது \"clear chat\", \"new chat\", \"உரையாடலை அழி\") என்று தட்டச்சு செய்யுங்கள், அல்லது பக்கத்தின் மேலே உள்ள New Chat-ஐ அழுத்துங்கள்."),
}


def _col_names(text: str) -> list:
    return [re.sub(r"\s+", " ", w).strip().lower() for w in re.findall(_COLW, text or "", re.IGNORECASE)]


def _fv_cols_parse(text: str):
    """(columns added, columns taken away) by a visit-table request."""
    low = (text or "").strip().lower().rstrip("?.! ")
    m = _COL_DROP_RE.fullmatch(low)
    if m:
        return [], _col_names(m.group(1))
    m = _COL_ADD_RE.fullmatch(low)
    if m:
        return _col_names(m.group(1)), []
    return [], []


def _fv_cols_parse_loose(text: str):
    """Columns named anywhere in a visit-table question: "with district and no block" -> (['district'], ['block'])."""
    adds, drops = [], []
    low = (text or "").lower()
    for m in re.finditer(rf"(?:no|not|without|hide|exclude|remove|drop|skip)\s+(?:the\s+)?(?:along\s+)?({_COL_LIST})", low):
        drops += _col_names(m.group(1))
    for m in re.finditer(rf"(?:along\s+with|along|with|add|include)\s+(?:the\s+)?({_COL_LIST})", low):
        adds += [c for c in _col_names(m.group(1)) if c not in drops]
    return adds, drops


def _fv_cols_merge(prior, adds, drops) -> dict:
    add = [c for c in (prior or {}).get("add", [])]
    drop = [c for c in (prior or {}).get("drop", [])]
    for c in adds:
        if c in drop:
            drop.remove(c)
        elif c not in add:
            add.append(c)
    for c in drops:
        if c in add:
            add.remove(c)
        elif c not in drop:
            drop.append(c)
    return {"add": add, "drop": drop}


def _fv_cols_message(state: dict) -> str:
    """The visit-table request that draws exactly these columns."""
    out = "show field visits"
    if state.get("add"):
        out += " along with " + ", ".join(state["add"])
    for d in state.get("drop", []):
        out += f" no {d}"
    return out



_NO_WORDS = frozenset({"no", "nope", "nah", "illa", "illai", "இல்லை", "nvm", "never mind",
                       "இல்ல பரவாயில்ல", "apdi illa", "appadi illa", "illa apdi illa", "அப்படி இல்லை"})
_YES_WORDS = frozenset({"yes", "yeah", "yep", "yup", "ya", "yaa", "correct", "exactly",
                        "true", "aama", "ama", "aamam", "ஆம்", "ஆமா", "சரிதான்"})


def _contentless_reply(kind: str, language: str, message: str = "", chat_history: list = None) -> str:
    """What to say to a message that asked nothing.

    A clear is carried out rather than described, so its text is only the
    confirmation the officer sees for a moment before the transcript is
    replaced by a fresh welcome.
    """
    is_tamil = language in ("ta", "tanglish")
    if kind in ("qmark_clarify", "punct_clarify"):
        has_assistant_context = bool(chat_history and any(
            isinstance(m, dict) and str(m.get("role", "")).lower() in ("assistant", "ai", "bot")
            for m in chat_history
        ))
        if has_assistant_context:
            return ("முந்தைய பதிலில் எந்தப் பகுதியை விளக்க வேண்டும்? நீங்கள் எதை அறிய விரும்புகிறீர்கள் என்று சொல்லுங்கள்."
                    if is_tamil
                    else "Which part would you like clarification on? Tell me what you'd like me to explain or check.")
        return ("நீங்கள் எதை அறிய விரும்புகிறீர்கள்? ஒரு விண்ணப்ப எண், நில அளவை எண் அல்லது உங்கள் கள ஆய்வுகள் பற்றிக் கேளுங்கள்."
                if is_tamil
                else "What would you like to know? Give me an application number, a survey number, or ask about your field visits.")
    if kind == "clear":
        return ("உரையாடல் அழிக்கப்பட்டது." if is_tamil
                else "Conversation cleared.")
    if kind == "session_command":
        if is_tamil:
            return ("வெளியேற, மேலே வலதுபுறம் உள்ள \"Logout\" பொத்தானை அழுத்தவும். "
                    "உரையாடலை அழிக்க \"clear\" என்று தட்டச்சு செய்யுங்கள்.")
        return ("To sign out, use the \"Logout\" button at the top right. To wipe "
                "this conversation, just type \"clear\".")
    if kind == "clear_unclear":
        if is_tamil:
            return ("இதை புரிந்துகொள்ள முடியவில்லை. உரையாடலை அழிக்க \"clear\" அல்லது "
                    "\"clear chat\" என்று தட்டச்சு செய்யுங்கள்.")
        return ("I didn't catch what that meant. To wipe this conversation, type "
                "\"clear\" or \"clear chat\".")
    if kind == "dismiss":
        return ("மன்னிக்கவும். நான் தவறாகச் சொல்லியிருந்தால், நீங்கள் எதைத் தேடுகிறீர்கள் என்று சொல்லுங்கள் -- "
                "விண்ணப்ப எண், நில அளவை எண் அல்லது கள ஆய்வு." if is_tamil
                else "Sorry about that. If I got something wrong, tell me what you were looking for -- an "
                     "application number, a survey number or your field visits -- and I'll try again.")
    # A yes / no is an answer to a question -- but nothing is left pending
    # here, so agreeing ("Right.") would pretend it was understood.
    word = (message or "").strip().strip(_TRIM_CHARS).strip().lower()
    if word in _NO_WORDS:
        return ("சரி, பரவாயில்லை. தேவைப்படும்போது விண்ணப்ப எண், நில அளவை எண் அல்லது கள ஆய்வு பற்றி கேளுங்கள்." if is_tamil
                else "No problem. Ask me whenever you need an application, a survey number or your field visits.")
    if word in _YES_WORDS:
        return ("எதைப் பற்றி என்று சொல்லுங்கள் -- விண்ணப்ப எண், நில அளவை எண் அல்லது கள ஆய்வு?" if is_tamil
                else "What would you like to know? Give me an application number, a survey number, or ask about your field visits.")
    if is_tamil:
        return ("சரி. விண்ணப்ப நிலை, நில அளவை எண்கள், கள ஆய்வுகள் அல்லது உங்கள் "
                "நிலுவைப் பணிச்சுமை பற்றி எதையும் கேளுங்கள்.")
    return ("Right. Ask me whenever you need an application's status, a survey "
            "number, your field visits, or your pending workload.")


# ── Out-of-scope questions ──────────────────────────────────────────────────
# "what's the weather", "write me a poem", "who is the prime minister", "what is
# 2+2". These route to `general_query` -> the agent / RAG fallback, where
# llama3.1:8b answers them at length, off-topic, as if that were the job. The
# deterministic guard below turns them away with one line naming what the
# assistant IS for. It is deliberately HIGH-PRECISION: it fires only when the
# message BOTH matches an unmistakably non-SIS pattern AND carries no survey /
# application / land vocabulary at all. A domain question phrased oddly falls
# through untouched -- catching a real question is a worse failure than letting
# an odd off-topic one reach the model.
_OUT_OF_SCOPE_CUES = (
    "say something", "what is love", "meaning of life", "sing a ", "sing me", "sing song", "tell me a story", "riddle",
    "chief minister", "prime minister", "who is the president", "who is the governor",
    "who is the collector", "who is the mla", "my salary", "salary slip", "leave balance",
    "bitcoin", "stock price", "share price", "cricket score", "movie", "song lyrics",
    # Weather — generic and variant phrasings
    "weather", "temperature today", "forecast", "rain today",
    "will it rain", "is it going to rain", "humidity today",
    "carry an umbrella", "will it be sunny", "is it cold today",
    "weather update", "weather report",
    # Sports & entertainment results
    "cricket", "football", "match score", "who won", "ipl", "world cup",
    "which team won", "who scored the most runs", "football match result",
    "ipl season", "cricket schedule", "match score now",
    # Food & cooking
    "recipe", "how to cook", "how do i cook", "how do i make",
    "biryani", "restaurant near", "best restaurant",
    "where can i eat", "eat good food",
    # Jokes, poems, creative writing
    "tell me a joke", "write a joke", "tell a joke", "make me laugh",
    "sing a song", "sing me", "write a song", "write me a song",
    "write me a poem", "write a poem", "compose a poem", "write a story",
    "write me a story", "write a short story",
    "write an essay", "write me an essay",
    "can you rap",
    # Coding & tech tasks unrelated to SIS
    "write a python", "write code", "write me code", "debug this code",
    "write me a sql query", "how do i use git", "explain docker",
    "root an android", "root my phone", "best smartphone", "smartphone under",
    "which laptop should i buy", "best laptop under",
    # Non-SIS email/letter drafting
    "write me an email", "draft an email", "write an email to my",
    "help me write a resignation", "write a formal letter for bank",
    # General knowledge, geography, politics
    "capital of", "who is the president of", "who is the prime minister of",
    "prime minister of india", "president of india", "president of the united",
    "who wrote the national anthem", "election date", "next election",
    "state elections", "who won the election", "election results",
    "when did india", "independence day", "gain independence", "independence",
    "meaning of life",
    "who created the universe", "how old is the earth",
    "population of india", "population of the",
    # Entertainment
    "recommend a movie", "recommend a book", "suggest a movie", "good movie",
    "bollywood movie", "tell me about the avengers",
    "what book should i read",
    # Translation to foreign (non-SIS) languages only — NOT to Tamil/English
    # Keep narrow: 'translate ... to tamil' and 'translate ... to english'
    # are valid domain requests (translate survey remarks).
    " to french", " to spanish", " to hindi", " in french",
    "in french", "in spanish", "in german", " to japanese",
    "say cheers in", "say goodbye in", "what does bonjour mean",
    "how to say thank you in japanese",
    # Science & math
    "quantum physics", "theory of relativity", "explain quantum",
    "square root of", "what is e=mc", "what is calculus",
    "explain machine learning", "what is artificial intelligence",
    "explain deep learning", "explain docker",
    "solve this equation",
    "what is 2 + 2", "what is 2+2", "what's 2+2",
    # Finance & crypto
    "stock price", "bitcoin", "cryptocurrency", "lottery number",
    "sensex today", "nifty 50", "what is ethereum", "buy bitcoin",
    "winning lottery",
    # Personal / health / astrology
    "horoscope", "zodiac", "your favourite", "your favorite",
    "relationship advice", "how do i lose weight", "best diet for",
    "what medicine should i take", "recommend a doctor",
    # Romanised Tamil off-topic (Tanglish but clearly non-domain)
    "vananilai", "samayal sei", "joke sollu", "kavithai",
    "padal paadu", "tiraippadam parinturai",
    # Tamil script off-topic: weather, cricket, poem/song, joke, cooking.
    # கிரிக்கெட் / க்ரிக்கெட் are both attested spellings of "cricket" --
    # officers type either depending on their keyboard's transliteration.
    "வானிலை", "கிரிக்கெட்", "க்ரிக்கெட்", "கவிதை", "பாடல் பாடு", "நகைச்சுவை",
    "சமையல்", "ஜோக்", "திரைப்படம் பரிந்துரை", "தலைநகரம்",
)


_BARE_ARITHMETIC_RE = re.compile(r'\d+\s*\+\s*\d+|\d+\s*(?:x|\*|times|plus|minus|multiplied\s+by|divided\s+by)\s*\d+', re.IGNORECASE)


def _is_out_of_scope(message: str) -> bool:
    m = (message or "").lower()
    if not m.strip():
        return False
    if any(t in m for t in _DOMAIN_TERMS):
        return False
    # A bare sum ("2+2 evlo", "2+2 எவ்வளவு") without the "what is" wrapper the
    # literal cues above require. Only "+" -- "-" and "/" collide with survey
    # references (5/4A) and date ranges too often to trust as arithmetic.
    if _BARE_ARITHMETIC_RE.search(m):
        return True
    return any(c in m for c in _OUT_OF_SCOPE_CUES)


# "who are you", "what can you do", "what can you help me with" -- a question
# about the assistant itself. Answered with the same capability summary rather
# than sent to the model (where "what can you do" was mis-parsing as an
# application_status lookup on the word "can").
_CAPABILITY_CUES = (
    "what can u do", "what can you do for me", "use of this chatbot", "use of this bot",
    "purpose of this chatbot", "what is this chatbot", "what is this bot",
    "who is this", "what is this", "who are you", "what are you", "what can you do", "what do you do",
    "what can you help", "how can you help", "what can i ask",
    "what do you know", "what is your purpose", "what are your capabilities",
    "tell me about yourself", "about yourself", "how do you work", "how does this work",
    "என்ன உதவி", "உங்கள் நோக்கம்", "உன்னைப் பத்தி", "எப்படி வேலை செய்கிறாய்",
    "help me understand what you",
    "tell me what you do", "what kind of questions can",
    "what topics do you cover", "what topics do you know",
    # Texting-shorthand ("u"/"r" for "you"/"are") -- "who are u", "who r u",
    # "what r u", "wat are u". Kept as their own cues rather than folded into
    # the phrases above so "you"/"your" elsewhere is untouched.
    "who are u", "who r u", "what r u", "wat r u", "wat are u",
    "enna panna mudiyum", "yenna panna mudiyum", "unnala enna", "enna help",
    "enna ellam panna mudiyum", "nee yaaru", "நீ யார்", "என்ன செய்ய முடியும்",
    "who r you", "what r you",
    # "are you SIS?" / "are you a human?" -- a yes/no question about what the
    # assistant itself IS, not the "who/what are you" open shape above.
    # Left uncaught, this reached the LLM, which answered "Yes, I am SIS" --
    # a chatbot claiming to hold the human Sub Inspector Surveyor's post,
    # which is exactly the false personification the deterministic identity
    # handlers elsewhere in this file exist to prevent an officer's OWN
    # identity from getting (see `_is_officer_identity_question`). The
    # assistant is software that helps an SIS officer; it is not one.
    "are you sis", "are u sis", "r u sis", "are you a sis", "are you an sis",
    "are you a human", "are u human", "are you a bot", "are you a robot",
    "are you real", "are you an ai", "are you ai",
    "neenga sis ah", "nee sis ah", "sis ah nee",
    "நீங்கள் sis ஆ", "நீங்க sis தானா", "நீங்கள் மனிதரா",
    # Tanglish / romanised Tamil capability questions
    "neenga yaar", "neenga enna", "enna kettukkalaam", "enna ketta",
    "enna seyya mudiyum", "yaru nee", "nee yaru",
    # "what are you saying?" / "what r u saying" -- an officer questioning
    # the assistant's own last reply, in words rather than "huh?"/"repeat".
    "what are you saying", "what r u saying", "what are u saying",
    "enna solra", "enna sollura", "nee enna solra",
    "நீங்கள் என்ன சொல்கிறீர்கள்", "என்ன சொல்றீங்க",
    # Tamil script
    "நீங்கள் யார்", "என்ன செய்ய முடியும்", "எதைக் கேட்கலாம்",
    "நீங்க யாரு", "யார் நீங்க",
)

# "who is this?" / "what is this?" -- the bare, whole-message shape only.
# Unlike every other capability cue this cannot be a plain substring test:
# "this" is common enough that "what is this application's status" CONTAINS
# the literal text "what is this", and answering that with the capability
# blurb instead of the application's status would be a worse failure than
# missing the self-identity question it is meant to catch. Anchored to the
# whole message (after trimming punctuation), the way `followup_context.
# classify()`'s own idiom exemption for the same phrase is.
_IDENTITY_THIS_RE = re.compile(
    r"^(?:who|what)\s+(?:is\s+)?this\??$|^இது\s*(?:யார்|என்ன)\??$"
    r"|^(?:idhu|ithu)\s+(?:yaaru|enna)\??$",
    re.IGNORECASE,
)


_UNSUPPORTED_SCRIPT_RE = re.compile(
    "[\u0900-\u097F\u0980-\u09FF\u0A00-\u0A7F\u0A80-\u0AFF\u0B00-\u0B7F"
    "\u0C00-\u0D7F\u0600-\u06FF\u0400-\u04FF\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]")
_KEYBOARD_MASH = ("asdf", "sdfg", "dfgh", "fghj", "ghjk", "hjkl", "qwer", "wert", "erty",
                  "rtyu", "tyui", "yuio", "uiop", "zxcv", "xcvb", "cvbn", "vbnm", "kjhk",
                  "jhgf", "lkjh", "poiu", "mnbv")
_FORECAST_RE = re.compile(
    r"\b(?:predict|predicted|prediction|forecast|projection|projected)\b"
    r"|\bwill\s+(?:come|arrive|be\s+(?:received|filed|submitted|approved|rejected|completed|decided|overdue|pending))\b"
    r"|\bgoing\s+to\s+(?:come|arrive)\b|\bexpected\s+(?:applications|workload|load)\b",
    re.IGNORECASE)
_VAGUE_ALL_RE = re.compile(
    r"^\s*(?:please\s+)?(?:(?:give|show|send|get|list|tell|dump)\s+(?:me\s+)?)?"
    r"(?:all|everything|entire|whole)(?:\s+(?:the\s+)?(?:data|records?|info|information|"
    r"details|database|db))?\s*[.!?]*\s*$", re.IGNORECASE)
_HELP_WORDS = frozenset({"help", "help me", "help please", "please help", "menu", "options",
                         "commands", "what now", "start", "?", "hi help", "need help"})


def _is_gibberish(message: str) -> bool:
    text = message or ""
    if re.search(r"\d|[^\x00-\x7F]", text):
        return False
    words = re.findall(r"[A-Za-z]+", text)
    long_words = [w for w in words if len(w) >= 3]
    if not long_words or not any(len(w) >= 4 for w in long_words):
        return False

    def mash(w: str) -> bool:
        lw = w.lower()
        if len(lw) < 4:
            return False
        if any(k in lw for k in _KEYBOARD_MASH):
            return True
        return sum(ch in "aeiouy" for ch in lw) / len(lw) < 0.15
    return all(mash(w) for w in long_words)


def _unknown_message_kind(message: str):
    """A message no SIS question can be made of: another script, keyboard mash, a
    forecast request, "give me everything", a bare "help", or a bare "it" with
    nothing to point at. None for everything else."""
    text = (message or "").strip()
    if not text:
        return None
    low = text.lower()
    if _UNSUPPORTED_SCRIPT_RE.search(text):
        return "script"
    if low.rstrip("?!. ") in _HELP_WORDS:
        return "help"
    if _is_gibberish(text) or _unrecognised_word_message(text):
        return "gibberish"
    if _VAGUE_ALL_RE.match(text):
        return "vague_all"
    if _BARE_REF_RE.match(text) or fctx.is_deictic_field(text):
        return "bare_ref"
    if (re.search(r"\b(?:indha|intha|inda|antha|this|that)\s+(?:file|application|vinnappam|app)\b", low)
            and not re.search(r"\d{4}/\d{3,4}/", text) and len(text.split()) <= 14
            and re.search(r"\b(?:aagala|aagalai|aagave|pola|problem|issue|stuck|not\s+(?:working|processed|moving)|"
                          r"work\s+aagala|process\s+aagala|enna\s+panradhu|enna\s+pannalam)\b", low)):
        return "bare_ref"
    if _FORECAST_RE.search(text) and not re.search(r"\d{4}/\d{3,4}/", text):
        return "forecast"
    return None


def _unknown_message_reply(kind: str, language: str) -> str:
    ta = language in ("ta", "tanglish")
    if kind == "script":
        return ("நான் ஆங்கிலம், தமிழ் மற்றும் Tanglish-இல் பதிலளிக்க முடியும். தயவுசெய்து அவற்றில் ஒன்றில் "
                "மீண்டும் கேளுங்கள்.\nI can reply in English, Tamil and Tanglish. Please ask again in one of them.")
    if kind == "help":
        return _scope_reply(language, capability=True)
    if kind == "gibberish":
        return ("அந்தச் செய்தி எனக்குப் புரியவில்லை. விண்ணப்ப நிலை, கணக்கெண், கள ஆய்வு, பணிச்சுமை, கட்டணம் அல்லது "
                "சேவை குறியீடு பற்றிக் கேளுங்கள்." if ta else
                "I could not understand that message. You can ask about an application (status, applicant, "
                "documents), a survey number, your field visits, your workload, fees or a service code.")
    if kind == "vague_all":
        return ("இது மிகவும் பொதுவான கேள்வி. 'எனது அனைத்து விண்ணப்பங்கள்', 'நிலுவை விண்ணப்பங்கள்' அல்லது "
                "'எனது பணிச்சுமை' என்று கேளுங்கள்." if ta else
                "That is too broad to answer in one go. Try: 'show all my applications', 'my pending "
                "applications', 'how many ISD applications' or 'my workload'.")
    if kind == "bare_ref":
        return ("எதைக் குறிப்பிடுகிறீர்கள் என்று தெரியவில்லை — முன்னால் காட்டப்பட்ட விண்ணப்பம் எதுவும் இல்லை. முதலில் "
                "ஒரு பட்டியலைக் கேளுங்கள் ('எனது நிலுவை விண்ணப்பங்கள்') அல்லது விண்ணப்ப எண்ணைத் தாருங்கள்." if ta else
                "I don't have an application or list in view to refer to. Ask for a list first (for example "
                "'my pending applications') or give an application number.")
    if kind == "forecast":
        return ("பதிவில் உள்ளதை மட்டுமே என்னால் சொல்ல முடியும் — எதிர்கால விண்ணப்பங்கள் அல்லது பணிச்சுமையை "
                "முன்கணிக்க முடியாது. தற்போதைய நிலைக்கு 'எனது பணிச்சுமை' அல்லது 'நிலுவை விண்ணப்பங்கள்' என்று கேளுங்கள்." if ta else
                "I can only report what is on record — I can't predict future applications or workload. For the "
                "current position ask 'my workload' or 'my pending applications'.")
    return _scope_reply(language)


_LLM_LEAK_RE = re.compile(
    r"you are the sis assistant|tool results?\s*:|officer'?s question|the officer asked"
    r"|\btool results?\b|\bthe tool\b|\bthe officer (?:cannot|can only|is not|does not|asked)\b"
    r"|survey officer access", re.IGNORECASE)


def _scrub_llm_answer(text: str) -> str:
    """An LLM-written answer that repeats its own prompt or talks about "the tool"
    or "the officer" is an internal failure, not an answer. Replaced by the plain
    statement of what is true: it was not found."""
    if not text or not _LLM_LEAK_RE.search(text):
        return text
    if re.search(r"[\u0B80-\u0BFF]", text):
        return ("இதை பதிவேட்டிலோ குறிப்பு ஆவணங்களிலோ என்னால் கண்டறிய முடியவில்லை. கேள்வியை மாற்றிக் கேளுங்கள் "
                "அல்லது விண்ணப்ப எண், கணக்கெண், வார்டு அல்லது சேவை குறியீடு பற்றிக் கேளுங்கள்.")
    return ("I could not find that in the register or the reference documents. Try rephrasing, or ask "
            "about an application number, a survey number, a ward or a service code.")


_OTHER_OFFICER_RE = re.compile(
    r"\b(?:all|other|every|another|different|both)\s+(?:the\s+)?(?:sis\s+)?officers?\b"
    r"|\bcompare\s+(?:me|my\s+\w+)\s+(?:with|to|against)\b"
    r"|\bofficer[\s-]*wise\b|\bofficers?\s+comparison\b|\b(?:other|another)\s+sis\b"
    r"|\bby\s+(?:mr|mrs|ms|dr|shri|thiru|smt|sri)\.?\s+\w+"
    r"|மற்ற\s+அதிகாரி|எல்லா\s+அதிகாரி|மற்ற\s+ஊழியர்",
    re.IGNORECASE)


def _is_other_officer_request(message: str) -> bool:
    return bool(_OTHER_OFFICER_RE.search(message or ""))


_DAY_MONTH_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:\s*,?\s*((?:19|20)\d{2}))?\b",
    re.IGNORECASE)
_DAYS_IN_MONTH = {"jan": 31, "feb": 29, "mar": 31, "apr": 30, "may": 31, "jun": 30,
                  "jul": 31, "aug": 31, "sep": 30, "oct": 31, "nov": 30, "dec": 31}


def _invalid_calendar_date(message: str):
    """("31", "February") for a day that month never has, else None."""
    for m in _DAY_MONTH_RE.finditer(message or ""):
        day, mon, year = int(m.group(1)), m.group(2).lower(), m.group(3)
        limit = _DAYS_IN_MONTH[mon]
        if mon == "feb" and year and calendar.monthrange(int(year), 2)[1] == 28:
            limit = 28
        if day < 1 or day > limit:
            return m.group(1), calendar.month_name[list(_DAYS_IN_MONTH).index(mon) + 1]
    return None


_DATE_NARRATIVE_EVENT_RE = re.compile(
    r"\b(?:happened|happens|happening|occurr?ed|took\s+place|went\s+on|"
    r"going\s+on|any\s+activity|\bactivity\b)\b|"
    r"நடந்தது|நடந்ததா|nadandh\w*|nadakk\w*",
    re.IGNORECASE)
_DATE_NARRATIVE_WHEN_RE = re.compile(
    r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|"
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\b|"
    r"\bthat\s+day\b|\bthis\s+day\b|\btoday\b|\bthat\s+date\b|\byesterday\b|"
    r"அந்த\s*நாள்|இன்றைய\s*தினம்|அன்று|இன்று|andha\s*naal|anr?u\b|andaiku",
    re.IGNORECASE)


def _bare_date_narrative(message: str) -> bool:
    """"what happened on <date>?" / "any activity that day?" -- the register has
    no "everything that happened on a calendar date" table, so this used to
    fall to general_query and the LLM invented one: a specific application
    number, a workflow step, an "officer's note", none of it grounded in a
    real query result. Refused with a concrete alternative instead of guessed.

    Deliberately narrow: it only fires when NEITHER an application number NOR
    a back-reference to one is in the message (a real, resolvable file keeps
    its own answer path -- "what happened to it on 2026-08-05" is a workflow
    question, not this), and only when nothing else in parse_intent's ~60
    rules already claimed the message (a genuine, supported date query --
    "applications submitted on 2026-08-05", "field visits on 2026-08-05" --
    resolves to its own intent and is routed and answered as before, untouched)."""
    m = message or ""
    if not (_DATE_NARRATIVE_EVENT_RE.search(m) and _DATE_NARRATIVE_WHEN_RE.search(m)):
        return False
    if extract_application_number(m) or _has_application_back_reference(m):
        return False
    return parse_intent(m) == "general_query"


_UNHELD_RE = re.compile(
    r"\boffice\s+(?:hours|timings?|time|open|opens|closing|address|phone|number)\b"
    r"|\bwhat\s+time\s+does\s+the\s+office\b|\bworking\s+hours\b|\bholidays?\b"
    r"|\bhelp\s*line\b|\btoll[\s-]?free\b|\bstamp\s+duty\b|\bregistration\s+fees?\b"
    r"|\bcollector\b|\bpopulation\b|\blast\s+date\s+(?:to|for)\s+apply\b|\bapplication\s+deadline\b|அலுவலக\s+நேரம்|விடுமுறை|முத்திரை\s+கட்டணம்",
    re.IGNORECASE)


async def _dc_special(db, officer, message: str, matched_dist, language: str):
    """District / taluk code questions the plain name lookup cannot answer: the
    codes of every district, the officer's own district, and taluk codes (read from
    the TAMILNILAM taluk master, never recalled)."""
    from sqlalchemy import text
    from backend.config import DISTRICT_CODE_MAP, DISTRICT_NAME_MAP
    low = (message or "").lower()
    is_ta = language in ("ta", "tanglish")
    if re.search(r"\btaluks?\b|தாலுகா", low) and re.search(r"code|குறியீடு", low):
        d_code = matched_dist[1] if matched_dist else None
        if not d_code:
            m = _DISTRICT_NAME_RE.search((getattr(officer, "jurisdiction_name", "") or "").lower())
            d_code = DISTRICT_NAME_MAP[m.group(1).lower()] if m else None
        if not d_code:
            from backend.models import Ward, Town, Taluk, District
            d_code = (await db.execute(
                select(District.district_code).select_from(Ward)
                .join(Town, Ward.town_id == Town.id).join(Taluk, Town.taluk_id == Taluk.id)
                .join(District, Taluk.district_id == District.id)
                .where(Ward.id.in_(officer.jurisdiction_ids)).limit(1))).scalar()
        if not d_code:
            return None
        rows = (await db.execute(text("SELECT taluk_code, taluk_ename FROM taluk WHERE district_code = :d ORDER BY taluk_code"),
                                 {"d": d_code})).all()
        if not rows:
            return None
        listing = "; ".join(f"{c} = {re.sub(r'--.*$', '', n or '').strip()}" for c, n in rows)
        head = "தாலுகா குறியீடுகள்" if is_ta else "Taluk codes"
        return f"{head} ({DISTRICT_CODE_MAP.get(d_code, d_code)} / {d_code}): {listing}."
    if matched_dist:
        return None
    if re.search(r"district\s+codes|codes\s+of\s+(?:all\s+)?districts|all\s+district", low):
        listing = "; ".join(f"{name} {code}" for code, name in sorted(DISTRICT_CODE_MAP.items()))
        return ("மாவட்ட குறியீடுகள்: " if is_ta else "District codes: ") + listing + "."
    if re.search(r"\bdistrict\s+code\b|மாவட்ட\s*குறியீடு", low):
        m = _DISTRICT_NAME_RE.search((getattr(officer, "jurisdiction_name", "") or "").lower())
        code = DISTRICT_NAME_MAP[m.group(1).lower()] if m else None
        if not code:
            from backend.models import Ward, Town, Taluk, District
            code = (await db.execute(
                select(District.district_code).select_from(Ward)
                .join(Town, Ward.town_id == Town.id).join(Taluk, Town.taluk_id == Taluk.id)
                .join(District, Taluk.district_id == District.id)
                .where(Ward.id.in_(officer.jurisdiction_ids)).limit(1))).scalar()
        if code:
            name = DISTRICT_CODE_MAP.get(code, code)
            return (f"உங்கள் மாவட்டம் {name}; குறியீடு {code}." if is_ta
                    else f"Your district is {name}; its district code is {code}.")
    return None


def _opinion_reply(language: str) -> str:
    if language in ("ta", "tanglish"):
        return ("நான் ஒரு SIS AI உதவியாளர் — யாரைப் பற்றியும் எனக்குக் கருத்தோ உணர்வோ இல்லை. உங்கள் பதவி, அதிகார வரம்பு, "
                "நிலுவை விண்ணப்பங்கள், நீங்கள் முடித்த பணிகள் போன்றவற்றைப் பதிவேட்டில் இருந்து காட்ட முடியும் — எதைப் பார்க்க வேண்டும்?")
    return ("I'm the SIS AI Assistant, so I don't form opinions or feelings about people. What I can do is show you what the "
            "register holds about your work -- your jurisdiction, pending applications, completed files or field visits. "
            "Which of those would you like?")


_ASSISTANT_IDENTITY_RE = re.compile(
    r"\b(?:what(?:'s|\s+is|s)?\s+(?:your|ur|yr)\s+(?:name|role|job|purpose|function|work|use|task)"
    r"|who\s+(?:made|created|built|developed|trained|designed|owns|programmed)\s+(?:you|u)\b"
    r"|who\s+(?:is|are)\s+(?:your|ur)\s+(?:creator|developer|owner|maker)"
    r"|(?:which|what)\s+(?:(?:llm|ai|language)\s+)?(?:model|llm|ai|gpt|engine|technology|company)\s+(?:are|r|is|does|do)\s+(?:you|u|this|used|using|powers?|behind|running)"
    r"|(?:which|what)\s+(?:llm|ai)\b.*\b(?:use|using|used|powers?|behind)\b|\bwhat\s+(?:powers|runs)\s+you\b"
    r"|(?:are|r)\s+(?:you|u)\s+(?:chat\s*gpt|gpt|gemini|claude|llama|openai|real|human|a\s+human|a\s+person|a\s+robot)"
    r"|what\s+(?:are|r)\s+(?:you|u)\b|(?:tell\s+me\s+)?about\s+(?:yourself|urself)|introduce\s+yourself"
    r"|what\s+should\s+i\s+call\s+you|உன்\s+பெயர்|உங்கள்\s+பெயர்|நீ\s+யார்|உன்\s+வேலை|உங்கள்\s+வேலை"
    r"|\b(?:un|unga|ungal|onn?oda)\s+(?:peyar|per|name|role|velai)\b|\bnee\s+(?:yaaru|yaru|enna\s+panra)\b)",
    re.IGNORECASE)
_TIME_NOW_RE = re.compile(
    r"^\s*(?:what\s+is\s+the\s+time(?:\s+now)?|what(?:'s|s)\s+the\s+time|what\s+time\s+is\s+it(?:\s+now)?|current\s+time|time\s+now|tell\s+me\s+the\s+time)\s*[?.!]*\s*$",
    re.IGNORECASE)


def _identity_reply(message: str, language: str) -> str:
    low = (message or "").lower()
    ta = language in ("ta", "tanglish")
    if re.search(r"name|call\s+you|பெயர்|peyar|\bper\b", low):
        return ("நான் தமிழ்நாடு நில அளவைத் துறையின் SIS AI உதவியாளர்." if ta
                else "I'm the SIS AI Assistant for the Tamil Nadu Survey Department.")
    if re.search(r"made|created|built|developed|trained|designed|owns|programmed|creator|developer|owner|maker", low):
        return ("நான் நில அளவைத் துறையின் SIS உதவியாளராக உருவாக்கப்பட்டவன்; என்னை உருவாக்கியவர்களைப் பற்றிய விவரம் என்னிடம் இல்லை." if ta
                else "I'm the SIS AI Assistant built for the Tamil Nadu Survey Department. I don't hold details about who developed me.")
    if re.search(r"model|llm|gpt|gemini|claude|llama|openai|engine|technology|company", low):
        return ("நான் SIS AI உதவியாளர். என் சொற்களை ஒரு மொழி மாதிரி எழுதுகிறது; ஆனால் எல்லா எண்களும் விவரங்களும் துறையின் பதிவேட்டிலிருந்தே வருகின்றன." if ta
                else "I'm the SIS AI Assistant. A language model writes my wording, but every figure, date and status comes from the department's register, not from the model.")
    if re.search(r"human|person|robot|real", low):
        return ("நான் SIS AI உதவியாளர் — பதிவேட்டையும் துறை ஆவணங்களையும் படிக்கும் நிரல், மனிதன் அல்ல." if ta
                else "I'm the SIS AI Assistant -- a program that reads the department's register and documents, not a person.")
    return _scope_reply(language, capability=True)


def _time_now_reply(language: str) -> str:
    now = datetime.now()
    stamp = now.strftime("%H:%M on %d %B %Y")
    return (f"சர்வர் நேரம் {stamp}." if language in ("ta", "tanglish")
            else f"The server time is {stamp}.")


def _age_sla_answer(app_no: str, sd: dict, today, is_tamil: bool) -> str:
    """How long a file has been open, against the field-visit deadline and the
    type's own service SLA -- each named as what it is (see backend/utils/sla.py)."""
    from backend.utils import sla
    sub = date.fromisoformat(str(sd.get("submission_date"))[:10])
    fv = sd.get("field_visit") or {}
    done = None
    if str(fv.get("status", "")).lower() == "completed" and fv.get("scheduled_date"):
        done = date.fromisoformat(str(fv["scheduled_date"])[:10])
    return sla.age_statement(app_no, sd.get("type"), str(sd.get("status", "")).lower(), sub, today, done, is_tamil)


# The canonical workflow_history answer for an ISD/NISD application -- the register-only path
# already has this shape (othercode_detail.workflow() / current_stage()); this is its equivalent
# for the `applications` + `workflow_history` ORM tables, which previously had no dedicated
# handler at all, so every one of these questions fell through to the generic details card or
# (worse) to an unrelated handler whose cue happened to overlap a word in the question.
_WFH_STAGE_LABELS = {"SIS": "SIS (Sub Inspector Surveyor)", "SD": "SD (Senior Draughtsman)",
                      "DIS": "DIS (Deputy Inspector Surveyor)", "TAHSILDAR": "Tahsildar",
                      "COMPLETED": "Completed", "REJECTED": "Rejected"}
_WFH_STAGE_ALIASES = {"sis": "SIS", "sub inspector": "SIS", "surveyor": "SIS", "sd": "SD",
                       "senior draughtsman": "SD", "draughtsman": "SD", "dis": "DIS",
                       "deputy inspector": "DIS", "tahsildar": "TAHSILDAR", "zdt": "TAHSILDAR",
                       "completed": "COMPLETED", "complete": "COMPLETED",
                       "rejected": "REJECTED", "reject": "REJECTED"}
# Ordered narrowest-first so "final workflow transition" isn't swallowed by a looser cue, and so
# this gate only opens for genuinely workflow-shaped questions -- a bare "transition" or
# "verification" elsewhere in the message must never land here or in an unrelated service handler.
_WFH_COUNT_RE = re.compile(r"\bhow\s+many\s+workflow\s+(?:entr\w+|events?|actions?|steps?)\b|"
                           r"\bworkflow\s+(?:entr\w+|events?|actions?|steps?)\s+(?:exist|are\s+there)\b|"
                           r"\bnumber\s+of\s+workflow\s+(?:entr\w+|events?|actions?|steps?)\b|"
                           r"\bworkflow\s+(?:entr\w+|events?|actions?|steps?)\s+count\b", re.I)
_WFH_FIRST_RE = re.compile(r"\bfirst\s+workflow\s+(?:action|transition)\b|\bfirst\s+transition\b", re.I)
_WFH_WHO_RE = re.compile(r"\bwho\s+(?:performed|did|made|took|handled)\b.{0,25}\b(?:action|workflow)\b|"
                         r"\bwhich\s+desk\s+(?:performed|did|handled)\b", re.I)
_WFH_REMARK_RE = re.compile(r"\b(?:latest|last)\s+workflow\s+remark\b|\bworkflow\s+remark\b", re.I)
_WFH_RECOMMEND_RE = re.compile(r"\bsis\s+recommendation\b|\brecommendation\s+record\w*\b|\brecommendation\s+for\b", re.I)
_WFH_BEFORE_RE = re.compile(r"\b(?:action|stage|transition)\b.{0,15}\bimmediately\s+before\b|"
                            r"\bimmediately\s+before\s+the\s+current\s+stage\b|\bbefore\s+the\s+current\s+stage\b", re.I)
_WFH_AFTER_RE = re.compile(r"\b(?:action|stage|transition)\b.{0,15}\bimmediately\s+after\b|\bimmediately\s+after\s+\w+\b", re.I)
_WFH_FINAL_RE = re.compile(r"\bfinal\s+workflow\s+transition\b|\bfinal\s+transition\b|\blast\s+transition\b", re.I)
_WFH_LATEST_RE = re.compile(r"\blatest\s+workflow\s+action\b|\bmost\s+recent\s+workflow\s+action\b|"
                            r"\blast\s+workflow\s+action\b|\bmost\s+recent\s+workflow\s+transition\b|"
                            # Bare "what was the latest/last/most recent action?" with no
                            # "workflow" -- a follow-up inside a conversation that has already
                            # opened on this file's workflow history (the only context in which
                            # this function is reached at all; see _WFH_CUE below). Without this,
                            # the bare follow-up form fell through to a generic "could not find
                            # that particular detail" card.
                            r"\b(?:latest|last|most\s+recent)\s+action\b", re.I)
_WFH_TRANSITION_FROM_RE = re.compile(r"\bmoved?\s+from\s+\w+(?:\s+\w+)?\s+to\s+(?:another\s+stage|\w+)\b", re.I)
_WFH_REACHED_DATE_RE = re.compile(r"\breach(?:ed)?\s+(?:its\s+|the\s+)?current\s+stage\b", re.I)
_WFH_CUE = re.compile(
    r"\bworkflow\s+(?:action|entr\w+|event\w*|remark|transition|history)\b|\bfinal\s+(?:workflow\s+)?transition\b|"
    r"\bfirst\s+(?:workflow\s+)?transition\b|\bimmediately\s+(?:before|after)\b|"
    r"\bmoved?\s+from\s+\w+(?:\s+\w+)?\s+to\b|\bsis\s+recommendation\b|\brecommendation\s+record\w*\b|"
    r"\breach(?:ed)?\s+(?:its\s+|the\s+)?current\s+stage\b|"
    r"\bwho\s+(?:performed|did|made|took|handled)\b.{0,25}\b(?:action|workflow)\b|"
    # Bare "latest/last/most recent action" -- see _WFH_LATEST_RE above for why this is
    # safe to open the gate for: it is only ever reached once application_status has
    # already resolved one file, so "action" here always means a workflow step.
    r"\b(?:latest|last|most\s+recent)\s+action\b", re.I)


def _wfh_find_stage(text: str) -> Optional[str]:
    t = (text or "").lower()
    for alias, stage in _WFH_STAGE_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", t):
            return stage
    return None


def _wfh_desk_label(stage) -> str:
    s = str(stage or "").upper()
    return _WFH_STAGE_LABELS.get(s, s) or "not recorded"


def _wfh_fmt_dt(dt) -> Optional[str]:
    if not dt:
        return None
    local = dt.astimezone(_IST) if dt.tzinfo else dt
    return local.strftime("%Y-%m-%d %H:%M")


async def _workflow_history_answer(db, officer, message: str, app_no: str, is_tamil: bool) -> Optional[str]:
    """The canonical answer for a workflow_history question about ONE ISD/NISD application --
    first/latest action, who/which desk, the latest remark, a transition before/after a named
    stage, the final transition, the date the current stage was reached, and the entry count.
    Returns None (never a wrong record) when the question is not workflow-shaped at all, so every
    other handler is untouched; callers only reach here already knowing `app_no` is this officer's."""
    m = message or ""
    if not _WFH_CUE.search(m):
        return None
    from backend.services.postgres import get_workflow_history
    hops = await get_workflow_history(db, officer, app_no)
    if hops is None:
        return None
    if not hops:
        return (f"{app_no}-க்கு பணிப்பாய்வு பதிவு இல்லை." if is_tamil else f"No workflow history is recorded for {app_no}.")

    if _WFH_COUNT_RE.search(m):
        n = len(hops)
        return (f"{app_no}-க்கு {n} பணிப்பாய்வு பதிவுகள் உள்ளன." if is_tamil
                else f"{app_no} has {n} workflow entr{'y' if n == 1 else 'ies'}.")

    if _WFH_FIRST_RE.search(m):
        h = hops[0]
        return (f"{app_no}-ன் முதல் பணிப்பாய்வு நடவடிக்கை: {_wfh_desk_label(h['to_stage'])}, {_wfh_fmt_dt(h['performed_at'])}." if is_tamil
                else f"{app_no}'s first workflow action: -> {_wfh_desk_label(h['to_stage'])} on {_wfh_fmt_dt(h['performed_at'])}.")

    # Checked before _WFH_FINAL_RE/_WFH_LATEST_RE: "who performed the LAST action?"
    # contains "last action", which (deliberately) also matches _WFH_LATEST_RE's bare
    # follow-up alternative -- but the question asks WHO, not WHEN, so that must win.
    if _WFH_WHO_RE.search(m):
        h = hops[-1]
        desk = _wfh_desk_label(h["from_stage"] or h["to_stage"])
        return (f"{app_no}-ன் கடைசி நடவடிக்கையை {desk} மேசை செய்தது ({_wfh_fmt_dt(h['performed_at'])}). பதிவேட்டில் அலுவலர் பெயர் இல்லை, மேசை மட்டும் பதிவாகியுள்ளது." if is_tamil
                else f"The {desk} desk performed {app_no}'s most recent workflow action, on {_wfh_fmt_dt(h['performed_at'])}. The register records desks, not individual officer names.")

    if _WFH_FINAL_RE.search(m) or _WFH_LATEST_RE.search(m):
        h = hops[-1]
        return (f"{app_no}-ன் கடைசி பணிப்பாய்வு மாற்றம்: {_wfh_desk_label(h['from_stage'])} -> {_wfh_desk_label(h['to_stage'])}, {_wfh_fmt_dt(h['performed_at'])}." if is_tamil
                else f"{app_no}'s most recent workflow transition: {_wfh_desk_label(h['from_stage'])} -> {_wfh_desk_label(h['to_stage'])} on {_wfh_fmt_dt(h['performed_at'])}.")

    if _WFH_REMARK_RE.search(m):
        for h in reversed(hops):
            if h["remarks"]:
                return (f"{app_no}-ன் கடைசி பணிப்பாய்வு குறிப்பு: \"{h['remarks']}\" ({_wfh_fmt_dt(h['performed_at'])})." if is_tamil
                        else f"{app_no}'s latest workflow remark: \"{h['remarks']}\" (on {_wfh_fmt_dt(h['performed_at'])}).")
        return (f"{app_no}-ன் எந்த பணிப்பாய்வு படியிலும் குறிப்பு பதிவு செய்யப்படவில்லை." if is_tamil
                else f"No remark is recorded on any of {app_no}'s workflow steps.")

    if _WFH_RECOMMEND_RE.search(m):
        # workflow_history has no dedicated recommendation flag for an ISD/NISD file -- only
        # free-text remarks and, on the closing hop, a rejection reason. Say so plainly instead
        # of redirecting to the NISD-only full-field-transfer SIS-recommendation column, which
        # does not exist for this file's service code at all.
        for h in reversed(hops):
            note = h["remarks"] or h["rejection_reason"]
            if note:
                return (f"{app_no}-ன் பணிப்பாய்வு பதிவில் தனி 'பரிந்துரை' புலம் இல்லை; கடைசி குறிப்பு: \"{note}\"." if is_tamil
                        else f"{app_no}'s workflow_history has no dedicated recommendation field; its latest note is: \"{note}\".")
        return (f"{app_no}-ன் எந்த பணிப்பாய்வு படியிலும் பரிந்துரை பதிவு செய்யப்படவில்லை." if is_tamil
                else f"No recommendation is recorded on any of {app_no}'s workflow steps.")

    if _WFH_BEFORE_RE.search(m):
        if len(hops) < 2:
            return (f"{app_no} இன்னும் ஒரே பணிப்பாய்வு படியில்தான் உள்ளது; முந்தைய நடவடிக்கை இல்லை." if is_tamil
                    else f"{app_no} has only one workflow step so far; there is no action before it.")
        h = hops[-2]
        return (f"தற்போதைய கட்டத்திற்கு முன் இருந்த மேசை: {_wfh_desk_label(h['to_stage'])} ({_wfh_fmt_dt(h['performed_at'])})." if is_tamil
                else f"Immediately before the current stage: {_wfh_desk_label(h['to_stage'])}, on {_wfh_fmt_dt(h['performed_at'])}.")

    if _WFH_AFTER_RE.search(m):
        stage = _wfh_find_stage(m)
        if stage:
            for h in hops:
                if str(h["from_stage"] or "").upper() == stage:
                    return (f"{stage}-க்குப் பிறகு: {_wfh_desk_label(h['to_stage'])} ({_wfh_fmt_dt(h['performed_at'])})." if is_tamil
                            else f"Immediately after {stage}: -> {_wfh_desk_label(h['to_stage'])}, on {_wfh_fmt_dt(h['performed_at'])}.")
            return (f"{app_no}-ன் பணிப்பாய்வில் {stage} கட்டம் இல்லை." if is_tamil
                    else f"{app_no}'s workflow history never passed through {stage}.")

    if _WFH_TRANSITION_FROM_RE.search(m):
        stage = _wfh_find_stage(m) or "SIS"
        for h in hops:
            if str(h["from_stage"] or "").upper() == stage and str(h["to_stage"] or "").upper() != stage:
                return (f"{stage}-இலிருந்து {_wfh_desk_label(h['to_stage'])}-க்கு {_wfh_fmt_dt(h['performed_at'])} அன்று நகர்ந்தது." if is_tamil
                        else f"{app_no} moved from {stage} to {_wfh_desk_label(h['to_stage'])} on {_wfh_fmt_dt(h['performed_at'])}.")
        return (f"{app_no} இன்னும் {stage} கட்டத்தை விட்டு நகரவில்லை." if is_tamil
                else f"{app_no} has not yet moved on from {stage}.")

    if _WFH_REACHED_DATE_RE.search(m):
        h = hops[-1]
        return (f"{app_no} தற்போதைய கட்டத்தை ({_wfh_desk_label(h['to_stage'])}) அடைந்த தேதி: {_wfh_fmt_dt(h['performed_at'])}." if is_tamil
                else f"{app_no} reached its current stage ({_wfh_desk_label(h['to_stage'])}) on {_wfh_fmt_dt(h['performed_at'])}.")

    return None


_PRAISE_RE = re.compile(
    r"\b(?:good(?!\s*(?:morning|afternoon|evening|night|day))|great|nice|excellent|awesome|amazing|fantastic|wonderful|superb|perfect|brilliant|super|smart|neat|cool)"
    r"\s*(?:job|work|bot|answer|answers|response|one|help|stuff)?\b"
    r"|\bwell\s+done\b|\bthank(?:s|\s+you)\s+(?:so\s+much|a\s+lot|a\s+ton)\b|\bappreciate\b|\bimpressive\b"
    r"|\byou(?:'re|\s+are|\s+r)\s+(?:great|awesome|amazing|the\s+best|smart|helpful|good|nice|brilliant|super)\b"
    r"|\bvery\s+helpful\b|\bso\s+helpful\b|\bkeep\s+it\s+up\b|\bbravo\b|\bkudos\b|\blove\s+(?:it|this|you)\b"
    r"|\bromba\s+(?:nalla|nalla\s+irukku|super)\b|\bnalla\s+iru+k+u\b|\bsupera\s+iru+k+u\b|\bsemma\b|\bsema\b|\barumai\b|\bkalakkitta\b"
    r"|நல்லா\s+இருக்கு|நல்லா\s+இருக்கிறது|நன்றாக\s+இருக்கிறது|அருமை|சூப்பர்|மிகவும்\s+உதவியாக|பாராட்டு|சிறப்பு|கலக்கிட்ட|மிக\s+நன்றி"
    # The positive counterpart to the "not satisfied with your performance"
    # complaint fix below -- "I'm satisfied/happy with your performance" named
    # no "good/great/..." word, so it matched nothing here and fell through
    # to general_query the same way the complaint did.
    r"|\b(?:satisfied|happy|pleased)\s+(?:with\s+)?(?:your|ur)\s+(?:performance|service|work|answer|answers|response)\b",
    re.IGNORECASE)
_COMPLAINT_RE = re.compile(
    r"\b(?:you(?:'re|\s+are|\s+r)|u\s+r|ur)\s+(?:useless|stupid|bad|wrong|the\s+worst|dumb|not\s+helpful|a\s+waste|rubbish)\b"
    r"|\bthis\s+is\s+(?:wrong|useless|rubbish|bad|not\s+correct)\b|\b(?:wrong|incorrect)\s+answer\b|\bnot\s+correct\b|\bthat(?:'s|\s+is)\s+wrong\b"
    r"|\bnee\s+waste\b|\bwaste\s+bot\b|\buseless\s+bot\b|\bbakwas\b|\bthapp?u\b|\bsari(?:yilla|illa)\b|தவறு|தப்பு|சரியில்லை|சரியல்ல|பயனற்ற|வேஸ்ட்"
    # "I'm not satisfied with your performance" -- feedback ABOUT the
    # assistant, no different in kind from "you are useless" above, just
    # politer. Missing this specific phrasing, the message fell through to
    # general_query, whose RAG retrieval matched "performance" against the
    # escalation passage in workflow_guide.txt ("Officer performance review
    # triggered") and confidently explained Level 1 escalation rules -- a
    # specific, correct-sounding answer to a question nobody asked, which
    # reads as though it addressed the complaint when it did not. Anchored to
    # "your" (the assistant) so "my performance" -- already claimed above by
    # `completion_rate`, about the OFFICER's own completion rate -- is
    # untouched.
    r"|\b(?:not\s+satisfied|unhappy|disappointed|dissatisfied)\s+(?:with\s+)?(?:your|ur)\s+(?:performance|service|work|answer|answers|response)\b"
    r"|\byour\s+performance\s+(?:is\s+)?(?:bad|poor|not\s+good|disappointing)\b",
    re.IGNORECASE)
_SENTIMENT_ASK_RE = re.compile(
    r"\b(?:show|list|display|how\s+many|what\s+is|status|count|details?|kaattu|kaatu|sollu|evlo)\b|காட்டு|எத்தனை|\d{4}/", re.IGNORECASE)


def _sentiment_kind(message: str):
    """"good job" / "romba nalla irukku" (praise) or "this is wrong" / "nee waste"
    (complaint) -- short, and asking nothing. Left to the model these were answered
    with a greeting or from imagination."""
    text = (message or "").strip()
    if not text or len(text.split()) > 10 or _SENTIMENT_ASK_RE.search(text):
        return None
    if _COMPLAINT_RE.search(text):
        return "complaint"
    if _PRAISE_RE.search(text):
        return "praise"
    return None


def _sentiment_reply(kind: str, message: str, language: str) -> str:
    ta = language in ("ta", "tanglish")
    if kind == "praise":
        opts_ta = ["நன்றி! உதவியாக இருந்ததில் மகிழ்ச்சி. வேறு எதைச் சரிபார்க்க வேண்டும்?",
                   "மிக்க நன்றி! உங்கள் அடுத்த கேள்வியைக் கேளுங்கள் — பதிவேட்டில் இருந்து பார்த்துச் சொல்கிறேன்."]
        opts_en = ["Thank you — glad it helps. What else can I check for you?",
                   "Thanks! Ask me the next one — a status, a list, a field visit or a rule — and I'll read it from the register."]
        opts = opts_ta if ta else opts_en
    else:
        opts = (["மன்னிக்கவும், அது சரியாக இல்லை போல. எந்த பதில் தவறு என்று சொல்லுங்கள் (விண்ணப்ப எண் அல்லது கேள்வி) — பதிவேட்டுடன் மீண்டும் சரிபார்க்கிறேன்."]
                if ta else
                ["Sorry that was not right. Tell me which answer was wrong — the application number or the question — and I'll check it against the register again."])
    return opts[sum(map(ord, message or "")) % len(opts)]


def _unrecognised_word_message(text: str) -> bool:
    """One or two long tokens that are no word this system, its documents or its spelling
    corrector knows -- keyboard slips like "withwiky". Not sent to the model, which
    invents a meaning (and, with a refusal earlier in the chat, its ward)."""
    if not text or re.search(r"[0-9\u0B80-\u0BFF/]", text):
        return False
    toks = re.findall(r"[a-z]+", text.lower())
    if not toks or len(toks) > 2 or len(" ".join(toks)) != len(text.strip()):
        return False
    try:
        known = fctx._known_words() | fctx._vocab()
        if fctx.correct_spelling(text.lower()) != text.lower():
            return False
        tri = _trigram_set()
    except Exception:
        return False
    flagged = False
    for t in toks:
        if t in known:
            return False
        if len(t) >= 6:
            grams = [f"^{t}$"[i:i + 3] for i in range(len(t))]
            if sum(1 for g in grams if g not in tri) / len(grams) >= 0.6:
                flagged = True
    return flagged


_TRIGRAMS = None


def _trigram_set():
    """Letter triples of every word the department documents and this module's
    vocabulary use: a token made mostly of triples none of them contain is a slip of
    the keyboard, not a word."""
    global _TRIGRAMS
    if _TRIGRAMS is None:
        words = set(re.findall(r"[a-z]+", _corpus_blob()))
        words |= {w for w in (fctx._known_words() | fctx._vocab()) if w.isascii() and w.isalpha()}
        _TRIGRAMS = {f"^{w}$"[i:i + 3] for w in words for i in range(len(w))}
    return _TRIGRAMS


_COMPARE_ASK = ("Tell me the two things to compare -- two application numbers, or two types, statuses, channels or wards (for example \"ISD vs NISD\").",
                "எதை ஒப்பிட வேண்டும் என்று சொல்லுங்கள் — இரண்டு விண்ணப்ப எண்கள், அல்லது இரண்டு வகை / நிலை / வழி / வார்டு (எ.கா. \"ISD vs NISD\").")


# "display applications ftom sri" -- a one-word source the register has no meaning for. Ignoring
# the word answered with the officer's whole desk queue, which reads as the answer to "from sri".
_FROM_WORD_RE = re.compile(
    r"^\s*(?:please\s+)?(?:display|show|list|give|get|find|see|need)\s+(?:me\s+)?(?:all\s+|my\s+)?"
    r"(?:applications?|apps?|aplications?|files?|cases)\s+(?:from|ftom|form|frm|fom|fro|by|via)\s+(?:the\s+)?([a-z][a-z\-]*)\s*[?.!]*\s*$",
    re.IGNORECASE)
_FROM_KNOWN = frozenset({
    "csc", "cscs", "sro", "sros", "sub", "registrar", "subregistrar", "citizen", "citizens", "portal", "sevai", "esevai",
    "igrs", "common", "service", "centre", "center", "counter", "office", "me", "my", "us", "today", "yesterday",
    "tomorrow", "last", "this", "next", "week", "month", "year", "date", "now", "then", "isd", "nisd",
    "pending", "approved", "rejected", "ward", "block", "taluk", "town", "district", "survey", "sub-registrar",
    "morning", "afternoon", "evening", "yesterdays", "todays", "tamilnilam", "thoothukudi", "tuticorin"})


def _unknown_from_word(message: str):
    m = _FROM_WORD_RE.match(message or "")
    if not m:
        return None
    w = m.group(1).lower()
    if w in _FROM_KNOWN or len(w) > 7 or w.isdigit():
        return None
    try:
        if extract_month_from_text(w):
            return None
    except Exception:
        pass
    # A typo of a real channel word ("cixtien" for "citizen") is not an
    # unknown source -- it only looked like one here because this guard's own
    # check above is exact-membership-only. `extract_submission_channels`
    # already carries real typo tolerance for exactly this (including the
    # phonetic "sitizen" and dropped-leading-letter variants), so a message it
    # can already resolve is not unknown; re-deriving a second, narrower check
    # here missed those same slips and refused a perfectly readable request.
    if extract_submission_channels(message):
        return None
    return w


def _unknown_from_reply(word: str, language: str) -> str:
    def _near(a: str, b: str) -> bool:
        if a == b or abs(len(a) - len(b)) > 1:
            return a == b
        return sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b)) <= 1
    guess = next((n for w, n in (("sro", "Sub-Registrar (SRO)"), ("csc", "CSC")) if _near(word, w)), None)
    if language in ("ta", "tanglish"):
        hint = f" {guess} என்று சொல்ல வந்தீர்களா?" if guess else ""
        return (f"\"{word}\" என்பது எந்த மூலம் / வழி என்று எனக்குத் தெரியவில்லை.{hint} விண்ணப்பங்கள் CSC, சார்-பதிவாளர் (SRO) "
                "அல்லது குடிமகன் வழியாக வரும்; இவற்றில் எதைக் காட்ட வேண்டும்? (எ.கா. \"CSC விண்ணப்பங்கள்\")")
    hint = f" Did you mean {guess}?" if guess else ""
    return (f"I don't recognise \"{word}\" as a source of applications.{hint} Applications come through a CSC counter, the "
            "Sub-Registrar (SRO) or the citizen portal -- which of these should I show? (for example \"applications from CSC\")")


# A bare "yes" right after "I don't recognise 'sto' ... Did you mean
# Sub-Registrar (SRO)? ... which of these should I show?" used to be answered
# as a fresh, contentless message ("What would you like to know? Give me an
# application number...") -- the clarification carried no memory of what it
# had just offered, so confirming it lost the thread entirely. The guessed
# channel is read back out of the clarification's OWN text (the same way
# `_LastAppNo`/`_unheld_qa.context_app()` scan chat_history directly,
# rather than needing a new piece of stored structured context) and the
# officer's "yes" is rewritten into the request it confirmed, before intent
# parsing ever sees it.
_AFFIRMATIVE_RE = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|ya|sure|correct|right|ok|okay|that'?s\s+right|that\s+one|confirm(?:ed)?)\s*[.!]*\s*$|"
    r"^\s*(?:ஆம்|ஆமாம்|சரி|அது\s*தான்)\s*[.!]*\s*$",
    re.IGNORECASE)
_UNKNOWN_FROM_GUESS_RE = re.compile(
    r"Did you mean (Sub-Registrar \(SRO\)|CSC)\?|(Sub-Registrar \(SRO\)|CSC)\s*என்று சொல்ல வந்தீர்களா",
    re.IGNORECASE)
_GUESS_TO_CHANNEL = {"sub-registrar (sro)": "sub_registrar", "csc": "csc"}

# The "cn number" clarification ("Did you mean the CAN number?") has the same
# yes/no shape as the channel guess above, and the same gap: a bare "yes"
# carried no memory of what it confirmed and fell to the contentless fallback.
_CN_CONFIRM_RE = re.compile(r"Did you mean the CAN number|CAN\s*எண்.{0,10}வேண்டுமா", re.IGNORECASE)


def _resolve_unknown_from_confirmation(message: str, chat_history: list) -> Optional[str]:
    """"yes" (or its Tamil equivalents) right after the "unknown source" or "cn number"
    clarification -- the request it confirmed, or None when this message is not that confirmation."""
    if not _AFFIRMATIVE_RE.match(message or ""):
        return None
    last_assistant = next((
        (m.get("content") or "") if isinstance(m, dict) else str(m)
        for m in reversed(chat_history or [])
        if isinstance(m, dict) and str(m.get("role", "")).lower() in ("assistant", "ai", "bot")
    ), "")
    if _CN_CONFIRM_RE.search(last_assistant):
        return "CAN number"
    m = _UNKNOWN_FROM_GUESS_RE.search(last_assistant)
    if not m:
        return None
    guess = next((g for g in m.groups() if g), "").strip().lower()
    channel = _GUESS_TO_CHANNEL.get(guess)
    return f"applications from {channel}" if channel else None


# "include rejected too" right after a channel-scoped listing ("applications from SRO")
# had two separate bugs. First, `followup_context.classify()` read "include ... too" as a
# generic singular/confirmation fragment (the same pattern "is that all?" matches) and the
# turn was answered "Which one do you mean?" instead of ever reaching the register query.
# Second, even a phrasing that DID reach `all_service_code_applications` on its own ("rejected
# applications too", "show rejected applications") re-ran the listing with no channel at all --
# "26 rejected across every service code" instead of the 6 that are actually from Sub-Registrar
# -- because `_register_listing()` only ever reads the channel out of the CURRENT message, and
# none of these phrasings name one. Both are fixed the same way `_resolve_unknown_from_
# confirmation` resolves "yes": read the channel back out of the PREVIOUS listing's own
# rendered text ("... application(s) from Sub-Registrar ...") and splice it into the rewrite.
_REJECTED_TOO_RE = re.compile(
    # "reject"/"rejects"/"rejected" all accepted -- an officer typing fast drops the
    # "-ed" ("include reject too") as often as not, and the status word itself is
    # spelled out in full ("rejected") only in the rewritten text this produces.
    r"\binclude\s+(?:the\s+)?reject(?:ed|s)?\b|\breject(?:ed|s)?\s+(?:application(?:s)?\s+)?too\b|"
    r"\balso\s+(?:show|include)\s+(?:the\s+)?reject(?:ed|s)?\b|\bwith\s+reject(?:ed|s)?\s+(?:ones?|applications?)?\b|"
    r"\b(?:ask\s+for|show|list|display|give)\s+(?:the\s+)?reject(?:ed|s)?\s+(?:applications?|ones?)\b",
    re.IGNORECASE)
_LISTING_CHANNEL_TEXT_RE = re.compile(r"\bfrom\s+(Sub-Registrar|CSC|citizen portal)\b", re.IGNORECASE)
_CHANNEL_TEXT_TO_CODE = {"sub-registrar": "sub_registrar", "csc": "csc", "citizen portal": "citizen"}


def _resolve_rejected_widen(message: str, chat_history: list) -> Optional[str]:
    """"include rejected too" / "rejected applications too" -- the request it widens to,
    carrying forward whatever channel the previous listing was scoped to. None when this
    message is not that widening request."""
    if not _REJECTED_TOO_RE.search(message or ""):
        return None
    # "Show rejected applications in ward 005." names its OWN fresh ward --
    # a complete, self-contained listing request, not a continuation phrase
    # asking to widen whatever was already on screen. `_REJECTED_TOO_RE`'s
    # "show rejected applications" alternative has no end anchor, so it
    # matched the first few words of this sentence too and the rewrite threw
    # the rest away: "'Show rejected applications in ward 005.' -> 'all
    # pending approved rejected applications'" -- the officer's own ward
    # silently replaced by the PREVIOUS turn's scope (or none at all). A
    # widen phrase never names a ward/block/service code/year of its own, so
    # any of those is proof this is a fresh request instead.
    if re.search(r"\bward\s*\d|\bblock\s*\d|\b(?:service\s*code\s*)?0\d{3}\b|\b(?:19|20)\d{2}\b",
                 message or "", re.IGNORECASE):
        return None
    last_assistant = next((
        (m.get("content") or "") if isinstance(m, dict) else str(m)
        for m in reversed(chat_history or [])
        if isinstance(m, dict) and str(m.get("role", "")).lower() in ("assistant", "ai", "bot")
    ), "")
    # "include rejected too" asks to ADD rejected back to whatever was
    # already being shown, not to narrow down to ONLY rejected -- this used
    # to rewrite straight to "rejected applications", so an officer who had
    # just excluded rejected files was shown 24 rejected-only applications
    # for a question that meant "show me everything, rejected included" (the
    # whole register). Naming every register status together collapses to
    # "no filter" in `_requested_status_filter_for_register`, the correct
    # effect of un-excluding one of them.
    m = _LISTING_CHANNEL_TEXT_RE.search(last_assistant)
    if not m:
        return "all pending approved rejected applications"
    code = _CHANNEL_TEXT_TO_CODE.get(m.group(1).strip().lower())
    return (f"all pending approved rejected applications from {code}" if code
            else "all pending approved rejected applications")


_QUALIFIER_LIST_INTENTS = frozenset({"pending_applications", "isd_applications", "nisd_applications",
                                     "both_applications", "all_service_code_applications"})


def _unknown_qualifier(message: str):
    """The unrecognised words of a list request ("show applications in xyz"), or []."""
    words = _qualifier_guard.unknown_words(message)
    if words and parse_intent(message) in _QUALIFIER_LIST_INTENTS:
        return words
    _num = _unknown_number_qualifier(message)
    if _num and parse_intent(message) in _QUALIFIER_LIST_INTENTS:
        return [_num]
    return []


# "show isd application in 19" -- `qualifier_guard.unknown_words()` only ever
# tokenises ALPHABETIC words (its own regex explicitly skips digits), so a
# bare, unlabelled NUMBER that matches no ward, block, year or date was never
# even a candidate for that guard, and this shape fell through it entirely:
# silently dropped, the type filter alone still applied, and "in 19" was
# never acted on OR mentioned -- 6 unfiltered ISD applications came back as
# if the question had been answered in full. Narrow on purpose: the number
# must sit directly after in/at/for/from with nothing else around it (no
# "ward"/"block"/"survey" naming what it is -- those already resolve
# correctly elsewhere), 1-3 digits (a 4-digit run is a plausible year and
# already handled by the date-period machinery), and not immediately
# followed by a unit word that would make it a duration, not a locator.
_BARE_NUMBER_QUALIFIER_RE = re.compile(
    r"\b(?:in|at|for|from)\s+(\d{1,3})\b(?!\s*(?:am|pm|day|days|month|months|year|years|"
    r"hr|hrs|hour|hours|min|mins|minute|minutes|%|percent|st|nd|rd|th))",
    re.IGNORECASE)


def _unknown_number_qualifier(message: str) -> Optional[str]:
    text = message or ""
    if not _qualifier_guard.listing_shaped(text) or _ANY_APP_NUMBER_RE.search(text):
        return None
    m = _BARE_NUMBER_QUALIFIER_RE.search(text)
    if not m:
        return None
    num = m.group(1)
    # Already resolved elsewhere (a year, a month-scoped period, a ward/block
    # named explicitly a few words earlier) -- this guard only ever fires on
    # what NOTHING already claimed.
    if extract_date_range(text) != (None, None) or _period_from_message(text)[0] is not None:
        return None
    if re.search(rf"\b(?:ward|block|survey|page|row|rows|top|first|last)\s+\D{{0,3}}{re.escape(num)}\b", text, re.IGNORECASE):
        return None
    return num


def _unknown_qualifier_reply(words, language: str) -> str:
    named = ", ".join(f'"{w}"' for w in words[:3])
    if language in ("ta", "tanglish"):
        return (f"{named} என்பதை வைத்து விண்ணப்பங்களை வடிகட்ட எனக்குத் தெரியவில்லை, எனவே பட்டியலைக் காட்டவில்லை. நிலை "
                "(நிலுவை / அங்கீகரிக்கப்பட்ட / நிராகரிக்கப்பட்ட), வகை (ISD / NISD), வழி (CSC / SRO / குடிமகன்), வார்டு, "
                "தொகுதி, மாதம் / ஆண்டு அல்லது காலதாமதம் மூலம் வடிகட்டலாம் — எதைப் பயன்படுத்த வேண்டும்?")
    return (f"I don't know how to filter applications by {named}, so I have not shown a list that ignores it. "
            "I can filter by status (pending, approved, rejected), type (ISD, NISD), channel (CSC, SRO, citizen), "
            "ward or block, month or year, or overdue -- which should I use?")


_CN_NUMBER_RE = re.compile(r"\bcn\s*(?:number|numbr|nmber|no\b|எண்)", re.IGNORECASE)

_TERM_ASK_RES = (
    re.compile(r"^(?:please\s+)?(?:what\s+(?:is|are|does|do)|what'?s|whats|define|explain|meaning\s+of|"
               r"tell\s+me\s+(?:about|the\s+meaning\s+of))\s+(?:a\s+|an\s+|the\s+)?([a-z]{5,})"
               r"(?:\s+(?:mean|means|meaning))?\s*[?.!]*$", re.IGNORECASE),
    re.compile(r"^([a-z]{5,})\s+(?:meaning|means|definition|enna|endral\s+enna|na\s+enna|artham)\s*[?.!]*$",
               re.IGNORECASE),
)
_SUPERSEDED_FAMILY = ("supersed", "supercede", "superced")
_CORPUS_WORDS: Optional[frozenset] = None


def _corpus_words() -> frozenset:
    global _CORPUS_WORDS
    if _CORPUS_WORDS is None:
        _CORPUS_WORDS = frozenset(re.findall(r"[a-z]{5,}", _corpus_blob().lower()))
    return _CORPUS_WORDS


# "explain X" where X describes HOW to explain (briefly, further, again, ...)
# rather than naming a term -- a continuation of the previous answer, not
# a request to define a new word. Without this, "explain briefly" after
# "what is ISD?" matched `_TERM_ASK_RES`'s "explain <word>" shape and
# answered '"briefly" is not a term in the SIS documents', instead of
# re-explaining ISD.
_EXPLAIN_STYLE_WORDS = frozenset((
    "briefly", "further", "again", "differently", "simply", "plainly",
    "clearly", "shortly", "quickly", "slowly", "easily", "properly",
    "simpler", "shorter", "clearer", "concisely",
))


def _unknown_term(message: str) -> Optional[str]:
    """The word in "what is X" / "X meaning" when X is not an SIS term (nor a typo of one)."""
    text = (message or "").strip()
    match = next((m for m in (rx.match(text) for rx in _TERM_ASK_RES) if m), None)
    if not match:
        return None
    word = match.group(1).lower()
    if word in _EXPLAIN_STYLE_WORDS:
        return None
    known = set(_GLOSSARY) | set(_corpus_words())
    shares_stem = any(k.startswith(word[:6]) or word.startswith(k[:6]) for k in known if len(k) >= 6)
    if word in known or shares_stem or difflib.get_close_matches(word, list(known), n=1, cutoff=0.8):
        return None
    if parse_intent(message) != "general_query" or _is_out_of_scope(message):
        return None
    return word


def _unknown_term_reply(word: str, language: str) -> str:
    ta = language in ("ta", "tanglish")
    if any(word.startswith(p) for p in _SUPERSEDED_FAMILY):
        return ("\"superseded\" SIS ஆவணங்களில் வரையறுக்கப்பட்ட சொல் அல்ல. இந்த உதவியாளரின் பதில்களில் அது "
                "புதிய குறியீட்டால் மாற்றப்பட்ட பழைய குறியீட்டைக் குறிக்கிறது — புவியியல் மாஸ்டரில் உள்ள பழைய "
                "தாலுகா / நகர / பிளாக் குறியீடுகள்; பதிவேட்டில் அவை இல்லை. \"old block code\" என்று கேளுங்கள்."
                if ta else
                "\"Superseded\" is not defined as a term in the SIS documents. Where it appears in this "
                "assistant's answers it means an old code that a newer one has replaced -- the old taluk / "
                "town / block codes in the geography master, which the register does not hold. Ask for the "
                "\"old block code\" to see that answer.")
    return (f"\"{word}\" SIS ஆவணங்களில் உள்ள சொல் அல்ல, எனவே அதற்கு நான் பொருள் யூகிக்க மாட்டேன். "
            "ISD, NISD, patta, sub-division, field visit, overdue, CAN, IGRS போன்ற SIS சொற்களை விளக்க முடியும்."
            if ta else
            f"\"{word}\" is not a term in the SIS documents, so I will not guess a meaning for it. I can "
            "explain SIS terms such as ISD, NISD, patta, sub-division, field visit, overdue, CAN or IGRS.")


# A whole-answer OUTPUT FORMAT this app's response architecture does not
# render -- JSON-only, CSV, a bare Markdown table, "paste into Excel" -- as
# opposed to a column/sort/group change over the carried listing table,
# which `followup_context.field_projections()` and friends already handle.
# Left unclaimed, a pure "return valid JSON only" / "return CSV" message
# names no application, survey, field or filter word, so it fell straight
# to `general_query` -> the agent layer, which (having nothing to call a
# tool for) either timed out after the full `AGENT_TIMEOUT_SECONDS` wait or
# wrote its own answer-system-prompt back as a nonsensical, SELF-REFERENTIAL
# refusal ("You are not the SIS assistant for the officer who asked the
# question...") -- the exact "copies its own system prompt back" failure
# CLAUDE.md already documents for tool ARGUMENTS, happening here for the
# final answer text instead, on a message with nothing for the model to
# ground an answer in at all. Answered honestly and instantly instead: this
# architecture renders a table (with column/sort/group control), not JSON
# or CSV -- a real, stated limitation, not a guess at what the data is.
_FORMAT_UNSUPPORTED_RE = re.compile(
    r"^\s*return\s+(?:valid\s+|only\s+|the\s+(?:result|data|answer)\s+(?:as|in)\s+)*json\b"
    r"|^\s*return\s+(?:the\s+)?(?:result\s+|data\s+)?(?:as\s+|in\s+)?csv\b"
    r"|^\s*return\s+(?:a\s+)?markdown\s+table\b"
    r"|\bformat\s+i\s+can\s+paste\s+into\s+excel\b"
    r"|^\s*(?:convert|change)\s+(?:it|this|the\s+(?:answer|output|result))\s+(?:into|to)\s+(?:valid\s+)?json\b",
    re.IGNORECASE)


def _format_unsupported_reply(language: str) -> str:
    if language in ("ta", "tanglish"):
        return ("இந்த உதவியாளர் பதில்களை அட்டவணையாக (நிரல்களைச் சேர்க்க/அகற்ற/வரிசைப்படுத்த முடியும்) "
                "தருகிறது -- JSON, CSV அல்லது வெற்று Markdown அட்டவணையாக இன்னும் தர முடியாது.")
    return ("This assistant renders answers as a table -- you can add, remove, reorder, sort or "
            "group its columns -- but it cannot return raw JSON, CSV or a bare Markdown table yet.")


def _special_scope_kind(message: str):
    if _cm_strong(message) or any(isinstance(k, tuple) for k in _fline_qa.parse(message)["keys"]):
        return None
    if _FORMAT_UNSUPPORTED_RE.search((message or "").strip()):
        return "format_unsupported"
    if _fullform_term(message):
        return "fullform"
    if _FULLFORM_BARE_RE.match((message or "").strip()):
        return "fullform_ask"          # "full form" with no term in the conversation to expand
    if _unknown_fullform(message):
        return "unknown_fullform"
    if _CN_NUMBER_RE.search(message or ""):
        return "cn_number"             # "cn number" -- not a term; ask, never guess it is the CAN
    if _unknown_term(message):
        return "unknown_term"
    if _unknown_from_word(message):
        return "unknown_from"
    if _unknown_qualifier(message):
        return "unknown_qualifier"
    if _OPINION_RE.search(message or "") and not re.search(r"\d{4}/\d{3,4}/\d+/\d+", message or ""):
        return "opinion"
    if (message or "").strip().lower().rstrip("?.! ") == "compare":
        return "compare_ask"          # nothing to compare yet
    if _clear_info_topic(message):
        return "clear_info"
    if _sentiment_kind(message):
        return _sentiment_kind(message)
    if _ASSISTANT_IDENTITY_RE.search(message or "") and not re.search(r"\d{4}/\d{3,4}/\d+/\d+", message or ""):
        return "identity"
    if _TIME_NOW_RE.match(message or ""):
        return "time"
    if _is_other_officer_request(message):
        return "officer"
    if _UNHELD_RE.search(message or "") and not re.search(r"\d{4}/\d{3,4}/\d+/\d+", message or ""):
        return "unheld"
    if _invalid_calendar_date(message):
        return "date"
    if _bare_date_narrative(message):
        return "bare_date_narrative"
    return None


def _special_scope_reply(message: str, language: str) -> str:
    if _special_scope_kind(message) == "format_unsupported":
        return _format_unsupported_reply(language)
    if _special_scope_kind(message) == "fullform_ask":
        return ("எந்தச் சொல்லின் விரிவாக்கம் வேண்டும்? (எ.கா. \"IGRS full form\", \"SRO full form\")" if language in ("ta", "tanglish")
                else "The full form of which term? For example \"full form of IGRS\" or \"full form of SRO\".")
    if _special_scope_kind(message) == "fullform":
        return _FULLFORMS[_fullform_term(message)][1 if language in ("ta", "tanglish") else 0]
    if _special_scope_kind(message) == "unknown_fullform":
        w = _unknown_fullform(message).upper()
        return (f"{w}-இன் விரிவாக்கம் SIS ஆவணங்களில் இல்லை, எனவே யூகிக்க மாட்டேன். அதன் பொருளை துறை ஆவணங்களில் உள்ளபடி மட்டுமே சொல்வேன் "
                "(எ.கா. IGRS, SRO, CSC, CAN, SLA, DSC, SD, DIS)." if language in ("ta", "tanglish") else
                f"I do not have the full form of {w} in the SIS documents, so I will not guess. I can give the ones the department "
                "documents define -- for example IGRS, SRO, CSC, CAN, SLA, DSC, SD, DIS.")
    if _special_scope_kind(message) == "unknown_term":
        return _unknown_term_reply(_unknown_term(message), language)
    if _special_scope_kind(message) == "cn_number":
        return ("'cn' என்பது எனக்குத் தெரிந்த சொல் அல்ல. CAN எண் (Citizen Access Number) வேண்டுமா? "
                "\"CAN எண்\" என்று கேளுங்கள், தருகிறேன்." if language in ("ta", "tanglish") else
                "I do not recognise \"cn\". Did you mean the CAN number (Citizen Access Number)? "
                "Ask for \"CAN number\" and I will give it.")
    if _special_scope_kind(message) == "unknown_qualifier":
        return _unknown_qualifier_reply(_unknown_qualifier(message), language)
    if _special_scope_kind(message) == "unknown_from":
        return _unknown_from_reply(_unknown_from_word(message), language)
    if _special_scope_kind(message) == "opinion":
        return _opinion_reply(language)
    if _special_scope_kind(message) == "compare_ask":
        return _COMPARE_ASK[1 if language in ("ta", "tanglish") else 0]
    if _special_scope_kind(message) == "clear_info":
        ta = (language in ("ta", "tanglish") or bool(re.search(r"[\u0B80-\u0BFF]", message or ""))
              or bool(re.search(r"\b(?:pann\w*|panr\w*|thirumba|kedaikk\w*|kidaikk\w*|aagum|nadakkum|azhi\w*|poidum\w*|eppadi|epdi|neekk?u\w*)\b",
                                (message or "").lower())))
        return _CLEAR_INFO_TEXT[_clear_info_topic(message)][1 if ta else 0]
    if _special_scope_kind(message) in ("praise", "complaint"):
        return _sentiment_reply(_special_scope_kind(message), message, language)
    if _special_scope_kind(message) == "identity":
        return _identity_reply(message, language)
    if _special_scope_kind(message) == "time":
        return _time_now_reply(language)
    if _special_scope_kind(message) == "unheld":
        if language in ("ta", "tanglish"):
            return ("அது SIS பதிவேட்டிலோ துறை ஆவணங்களிலோ என்னிடம் இல்லை — அலுவலக நேரம், விடுமுறை, "
                    "உதவி எண், முத்திரைக் கட்டணம் போன்றவற்றுக்கு அலுவலகத்தையோ TAMILNILAM தளத்தையோ பார்க்கவும்.")
        return ("That is not in the SIS register or the department documents I hold — for office "
                "hours, holidays, helpline numbers or stamp duty, check with the office or the "
                "TAMILNILAM portal.")
    if _special_scope_kind(message) == "date":
        d, mon = _invalid_calendar_date(message)
        if language in ("ta", "tanglish"):
            return f"{mon} மாதத்தில் {d}-ஆம் தேதி இல்லை. சரியான தேதியைக் கொடுங்கள்."
        return f"{d} {mon} is not a valid date — that month does not have a day {d}. Give a valid date and I will look it up."
    if _special_scope_kind(message) == "bare_date_narrative":
        if language in ("ta", "tanglish"):
            return ("ஒரு தேதி மட்டும் போதாது -- ஒரு நாளில் நடந்த அனைத்தையும் பதிவு செய்யும் பொதுவான பட்டியல் "
                    "என்னிடம் இல்லை. விண்ணப்ப எண்ணைக் கொடுங்கள், அல்லது என்ன வகை தகவல் வேண்டும் என்று சொல்லுங்கள் -- "
                    "அந்தத் தேதியில் சமர்ப்பிக்கப்பட்ட விண்ணப்பங்கள், பணிப்பாய்வு நடவடிக்கைகள், அல்லது கள ஆய்வுகள்.")
        return ("A date alone isn't enough -- I don't have a general log of everything that happened on "
                "one day. Give me an application number, or tell me what kind of record you want: "
                "applications submitted on that date, workflow actions on a specific file, or field visits.")
    return _other_officer_reply(language)


def _other_officer_reply(language: str) -> str:
    if language in ("ta", "tanglish"):
        return ("நான் உங்கள் சொந்த அதிகார எல்லைக்குட்பட்ட பதிவுகளை மட்டுமே காட்ட முடியும் — "
                "மற்ற அதிகாரிகளின் விவரங்கள் அல்லது அதிகாரிகளுக்கிடையேயான ஒப்பீடு என்னிடம் இல்லை. "
                "உங்கள் விண்ணப்பங்கள், கள ஆய்வுகள் அல்லது பணிச்சுமை பற்றிக் கேளுங்கள்.")
    return ("I can only show records inside your own jurisdiction — I do not hold other "
            "officers' figures or a comparison between officers. Ask about your own "
            "applications, field visits or workload.")


def _is_capability_question(message: str) -> bool:
    m = (message or "").lower().strip().strip("?.! ")
    if _IDENTITY_THIS_RE.match(m):
        return True
    return any(c in m for c in _CAPABILITY_CUES) and len(m.split()) <= 12


def _scope_reply(language: str, capability: bool = False) -> str:
    """One line naming what the SIS assistant is for, adhering to the mandatory Tanglish-to-Tamil policy."""
    if language in ("ta", "tanglish"):
        body = (
            "நான் தமிழ்நாடு நில அளவைத் துறையின் SIS உதவியாளர். விண்ணப்ப நிலை, "
            "பணிப்பாய்வு, கள ஆய்வுகள், உரிமையாளர் / பட்டா விவரங்கள், உட்பிரிவுகள், "
            "உங்கள் அதிகார எல்லை ஆகியவற்றில் உதவ முடியும்.")
        if capability:
            return body + " என்ன தெரிந்து கொள்ள விரும்புகிறீர்கள்?"
        return body + " பொது தலைப்புகளில் உதவ முடியாது."
    body = (
        "I'm the SIS assistant for the Tamil Nadu Survey Department. I can help "
        "with applications of every service code and their status, the mutation workflow, field "
        "visits, owner and patta details, sub-divisions, and your jurisdiction.")
    if capability:
        return (body + " Try: 'my pending applications', 'F-Line applications', "
                "'approved plus rejected', 'which service code has the most', "
                "'my field visits this week', or 'what is service code 0154'. "
                "For an application-specific question, provide the application number you already have. "
                "I cover every service code, not only ISD / NISD. What would you like to check?")
    return body + " I can't help with general topics outside that."


def _channel_clarify_reply(language: str) -> str:
    """The message names something close to CSC or SRO but not clearly
    enough to act on. Asked deterministically -- no LLM call, so no risk of
    the guess/hang failure this guard exists to replace."""
    is_tamil = language in ("ta", "tanglish")
    if is_tamil:
        return ("எந்த வழி (channel) என்று தெளிவாக இல்லை — CSC (பொதுச் சேவை மையம்), "
                "SRO (துணை பதிவாளர்), அல்லது Citizen (குடிமகன்) என்று குறிப்பிடவும்.")
    return ("I'm not sure which channel you mean — please say CSC "
            "(Common Service Centre), SRO (Sub-Registrar), or Citizen.")


# "Do they have an IGRS number?" asked of a list. The table cannot answer a
# yes/no, and the channel settles it: an IGRS Form 6 number is raised only
# where the Sub-Registrar's IGRS built the mutation off a registered deed, so
# every sub_registrar file carries one and no CSC or citizen file does.
_IGRS_QUESTION_RE = re.compile(
    r"\bigrs\b|\bform\s*6\b|\bform6\b|படிவம்\s*6", re.IGNORECASE)
_SRO_RE = re.compile(r"\bsro\b|\bsub[\s-]?registrar\b", re.IGNORECASE)
# "who is the sub registrar", "what does the SRO do", "what is the role of the
# sub registrar", "sub registrar யார்" -- role questions about the office. None
# of them matched the generic rule shapes ("what is A ..." / "who gets"), so
# they fell through to `sale_deed_check`, which answered *"Please specify the
# application number you are asking about"* to a question that names no
# application and wants none.
_SRO_ROLE_RE = re.compile(
    r"\bwho\s+(?:is|are|handles?|does)\b|\bwhat\s+(?:is|are)\b"
    r"|\brole\s+of\b|\bwhat\s+does\b|\bmeaning\b|\bstands?\s+for\b"
    r"|\bexplain\b|\bfull\s+form\b",
    re.IGNORECASE,
)
# Meta words: they describe the SHAPE of an identifier, which only the rule
# has. See the use in `_igrs_can_rule_topic`.
_META_FIELD_RE = re.compile(
    r"\bformat\b|\blength\b|\bhow\s+long\b|\bhow\s+many\s+digits\b"
    r"|\bdigits?\b|\bstructure\b|\bpattern\b|\bmade\s+up\s+of\b"
    r"|\bcharacters\b|வடிவ|நீளம்|இலக்க",
    re.IGNORECASE,
)
_ABSENT_RE = re.compile(
    r"\babsent\b|\bmissing\b|\bempty\b|\bblank\b|\bno\s+igrs\b|\bnot\s+have\b"
    r"|\bwithout\b|\bdoes\s?n[o']?t\s+have\b|\bhas\s+no\b|\bnone\b|\bnil\b"
    r"|இல்ல",
    re.IGNORECASE,
)


# "on what basis / how do you decide / how do you know" -- a question about the
# derivation, not about the files it was applied to.
_CHANNEL_BASIS_RE = re.compile(
    r"\bon\s+what\s+bas(?:is|ed)\b|\bbased\s+on\s+what\b|\bwhat\s+bas(?:is|ed)\b"
    r"|\bhow\s+(?:do|did|does|can|could|would|will|are|is|r|u|you)\b[^.?!]{0,30}?"
    r"\b(?:decid|determin|know|classif|identif|work\s+out|tell|figure|judg|say|said|claim|conclud|prov|sure|"
    r"certain|confirm|verif|label|mark|treat|deriv|infer|categor|consider|assign|detect|recogni[sz]|justif)\w*"
    r"|\b(?:decid|determin|classif|identif)\w*\s+(?:on|from|by|based)\b"
    r"|\bhow\s+is\s+(?:it|this|the\s+channel|the\s+source)\b"
    r"|\bwhat\s+(?:decides|determines)\b|\bwhy\s+is\s+it\s+(?:csc|sro|citizen)\b"
    # "why are these SRO applications?", "what makes these SRO", "proof these are from SRO"
    r"|\bwhy\s+(?:are|is|do|does)\s+(?:th\w{2,4}|it|all|\d{4}/\S+)(?:\s|$)[^.?!]{0,40}?\b(?:sro|csc|citizen|sub[\s-]?registrar)\b"
    r"|\bwhat\s+makes?\s+(?:th\w{2,4}|it|\d{4}/\S+)(?:\s|$)[^.?!]{0,30}?\b(?:sro|csc|citizen|sub[\s-]?registrar)\b"
    r"|\b(?:proof|evidence|basis|reason|justification)\b[^.?!]{0,30}?\b(?:sro|csc|citizen|sub[\s-]?registrar)\b"
    # Tanglish: "ivai sro nu eppadi solreenga / theriyum / decide pannenga"
    r"|\beppadi\s+(?:solr\w*|sollr\w*|solring\w*|theriy\w*|therinj\w*|kandupidi\w*|mudivu\w*|decide\w*|confirm\w*)"
    r"|எப்படி\s*(?:தீர்மான|கண்டறி|தெரி|சொல்|முடிவு|உறுதி)|எதன்\s*அடிப்படை|எந்த\s*அடிப்படை|ஏன்\s+(?:இவை|இது)",
    re.IGNORECASE,
)
# The channels the question is deciding between.
_CHANNEL_NAMED_RE = re.compile(
    r"\bsro\b|\bsub[\s-]?registrar\b|\bcsc\b|\bcitizen\b"
    r"|\bchannel\b|\bsource\b|\bsubmission\s+(?:mode|route|channel)\b"
    r"|சார்-?பதிவாளர்|குடிமகன்|சேவை\s*மைய",
    re.IGNORECASE,
)


# One short definition per channel, used on its own or joined when several are
# asked about together.
_CHANNEL_DEFN_EN = {
    "sub_registrar": (
        "SRO is the Sub-Registrar Office, where a sale deed is registered. When "
        "a deed is registered there, its IGRS system raises the patta mutation "
        "off the registered document and issues an IGRS Form 6 number. An "
        "application reaching the SIS desk that way carries that number and no "
        "operator code."),
    "CSC": (
        "CSC is a Common Service Centre — the counter a citizen walks "
        "into to file the application. An operator keys it in under their own "
        "code, which is what the record shows as the source, and the counter "
        "issues a 15-digit CAN in the 133 series. There is no IGRS Form 6 "
        "number, because no registered deed raised it."),
    "citizen": (
        "A citizen submission is one the applicant made themselves — through "
        "the TN portal, or at a special revenue camp where the camp operator "
        "keys it in on their behalf (the record carries camp flag 'P'). Like a "
        "CSC filing it has no IGRS Form 6 number."),
}
_CHANNEL_DEFN_TA = {
    "sub_registrar": (
        "SRO என்பது சார்-பதிவாளர் அலுவலகம் (பதிவு அலுவலகம்) — கிரயப் பத்திரம் "
        "பதிவு செய்யப்படும் இடம். பத்திரம் பதிவானதும் அதன் IGRS அமைப்பு பட்டா "
        "மாற்றத்தை உருவாக்கி IGRS படிவம் 6 எண்ணை வழங்குகிறது."),
    "CSC": (
        "CSC என்பது பொது சேவை மையம் — குடிமகன் நேரில் சென்று "
        "விண்ணப்பிக்கும் இடம். ஆபரேட்டர் தனது குறியீட்டில் பதிவு செய்கிறார்; "
        "கவுண்டர் 15 இலக்க CAN (133 தொடர்) வழங்குகிறது. IGRS படிவம் 6 எண் "
        "இருக்காது."),
    "citizen": (
        "குடிமகன் விண்ணப்பம் என்பது விண்ணப்பதாரரே தாக்கல் செய்தது — தமிழ்நாடு "
        "இணையதளம் வழியாக, அல்லது வருவாய் முகாமில் (camp flag 'P'). இதற்கும் "
        "IGRS படிவம் 6 எண் இருக்காது."),
}


_WORKFLOW_STEPS_WORD_RE = re.compile(
    r"\bworkflow\b|\bworkflw\b|\bworflow\b|\bworkflo\b|\bworkfow\b"
    r"|\bsteps?\b|\bstges?\b|\bstages?\b|\bprocess\b|\bprocedure\b"
    r"|படிகள்|பணிப்பாய்வு|வழிமுறை|செயல்முறை"
    r"|\bworkflow\s+steps\b", re.IGNORECASE)
_WORKFLOW_ISD_ONLY_NOUN_RE = re.compile(
    r"\bfield\s+verif\w*|\bfield\s+visit\w*|\bsubdivision\s+sketch|\bsketch\b"
    r"|\btemporary\s+sub[\s-]?division", re.IGNORECASE)
_WORKFLOW_SHARED_NOUN_RE = re.compile(
    r"\bfinal\s+(?:digital\s+)?sign[\s-]?off|\bdsc\b|\bpatta\s+order|\bpatta\s+transfer",
    re.IGNORECASE)
_WORKFLOW_STEP_NOUN_RE = re.compile(
    _WORKFLOW_ISD_ONLY_NOUN_RE.pattern + "|" + _WORKFLOW_SHARED_NOUN_RE.pattern, re.IGNORECASE)
_WORKFLOW_STEP_ROLE_RE = re.compile(
    r"\bsd\b|\bdis\b|\bsenior\s+draughtsman|\bdeputy\s+inspector", re.IGNORECASE)
_WORKFLOW_LISTING_RE = re.compile(
    r"\bshow\b|\blist\b|\bdisplay\b|\bmy\b|\bpending\b|\bhow\s+many\b"
    # `\bapplic\b` only ever matched the bare word "applic", which nobody
    # types -- "applications"/"apps" never matched it, so "what r the
    # applications from isd workflow" fell through to the ISD workflow-steps
    # explainer instead of staying a listing. `applic\w*` matches the stem.
    r"|\bcount\b|\bapplic\w*\b|\bapps?\b|\bfiles?\b|காட்டு|பட்டியல்", re.IGNORECASE)

_WORKFLOW_STEPS_TEXT = {
    "ISD": {
        "en": (
            "ISD (service code 0154, Involving Sub-Division) -- the parcel is "
            "split, so the file needs a field inspection and a sub-division "
            "sketch:\n"
            "1. Application (CSC / citizen portal / Sub-Registrar referral)\n"
            "2. SIS -- Sub Inspector Surveyor: mandatory field inspection & "
            "cadastral verification\n"
            "3. SD -- Senior Draughtsman: prepares the sub-division sketch\n"
            "4. DIS -- Deputy Inspector Surveyor: reviews the sketch and field "
            "report, approves or rejects\n"
            "5. Tahsildar -- holds the DSC key; applies it to approve and "
            "generate the patta transfer order"),
        "ta": (
            "ISD (சேவை குறியீடு 0154, உட்பிரிவு உள்ளடக்கியது) — நிலம் பிரிக்கப்படுவதால் "
            "கள ஆய்வும் உட்பிரிவு வரைபடமும் தேவை:\n"
            "1. விண்ணப்பம் (CSC / குடிமகன் போர்ட்டல் / துணை பதிவாளர் பரிந்துரை)\n"
            "2. SIS — கள ஆய்வு மற்றும் சர்வே சரிபார்ப்பு\n"
            "3. SD (மூத்த வரைவாளர்) — உட்பிரிவு வரைபடம் தயாரிப்பு\n"
            "4. DIS (துணை ஆய்வு சர்வேயர்) — வரைபடம் மற்றும் கள அறிக்கையை சரிபார்த்து "
            "ஒப்புதல்/நிராகரிப்பு\n"
            "5. தாசில்தார் — DSC பயன்படுத்தி ஒப்புதல் மற்றும் பட்டா மாற்ற உத்தரவு"),
    },
    "NISD": {
        "en": (
            "NISD (service code 0153, Not Involving Sub-Division) -- a "
            "straight patta transfer of the whole survey number, shorter than "
            "ISD (no field visit, no SD sketch, no DIS):\n"
            "1. Application (CSC / citizen portal / Sub-Registrar referral)\n"
            "2. SIS -- Sub Inspector Surveyor: document verification only\n"
            "3. Zonal Level Tahsildar -- holds the DSC key; approves and "
            "generates the patta transfer order\n\n"
            "This one matches the register: 167 of 168 seeded NISD "
            "applications reach the Tahsildar stage."),
        "ta": (
            "NISD (சேவை குறியீடு 0153, உட்பிரிவு இல்லாதது) — முழு சர்வே எண்ணின் "
            "நேரடி பட்டா மாற்றம், ISD-ஐ விட குறுகியது (கள ஆய்வு இல்லை, SD வரைபடம் "
            "இல்லை, DIS இல்லை):\n"
            "1. விண்ணப்பம் (CSC / குடிமகன் போர்ட்டல் / துணை பதிவாளர் பரிந்துரை)\n"
            "2. SIS — ஆவண சரிபார்ப்பு மட்டும்\n"
            "3. மண்டல அளவு தாசில்தார் — DSC பயன்படுத்தி ஒப்புதல் மற்றும் பட்டா மாற்ற "
            "உத்தரவு\n\n"
            "இது பதிவேட்டுடன் பொருந்துகிறது: 168 NISD விண்ணப்பங்களில் 167 தாசில்தார் "
            "நிலையை அடைகின்றன."),
    },
}

# Condensed one-line versions for "briefly" / "in short" -- the full numbered
# chain in `_WORKFLOW_STEPS_TEXT` above answered "explain isd workflow
# briefly" with the same multi-line, 5-6 bullet text as a plain "explain isd
# workflow", ignoring the brevity word entirely (ask: _is_detail_request).
_WORKFLOW_STEPS_BRIEF_TEXT = {
    "ISD": {
        "en": ("ISD (0154): Application -> SIS field visit -> SD sub-division sketch "
               "-> DIS review -> Tahsildar signs the patta order."),
        "ta": ("ISD (0154): விண்ணப்பம் -> SIS கள ஆய்வு -> SD உட்பிரிவு வரைபடம் -> DIS "
               "மதிப்பாய்வு -> தாசில்தார் கையொப்பம்."),
    },
    "NISD": {
        "en": ("NISD (0153): Application -> SIS document verification -> Zonal Level "
               "Tahsildar signs the patta order. No field visit, no SD sketch."),
        "ta": ("NISD (0153): விண்ணப்பம் -> SIS ஆவண சரிபார்ப்பு -> மண்டல தாசில்தார் "
               "கையொப்பம். கள ஆய்வு/SD வரைபடம் இல்லை."),
    },
}

# A second, differently-worded full-detail version -- same facts, different
# sentences -- shown ONLY when the officer asks for the full chain twice in a
# row (repeating the exact `_WORKFLOW_STEPS_TEXT` sentence reads as the bot
# being stuck). A model rewrite was tried first, but this exact prompt
# measured 45-63s against this project's Tamil-tuned Ollama model in testing
# -- past every reasonable wait, and it silently fell back to the identical
# text anyway, which was the bug this is fixing. Hand-written text costs
# nothing and can never drift a fact, so it replaces the LLM call here.
_WORKFLOW_STEPS_ALT_TEXT = {
    "ISD": {
        "en": ("The ISD (0154) chain, since the parcel is being split: the application "
               "comes in through a CSC counter, the citizen portal, or a Sub-Registrar "
               "referral. From there the Sub Inspector Surveyor (SIS) carries out the "
               "mandatory field inspection and cadastral check. Next the Senior "
               "Draughtsman (SD) draws up the sub-division sketch. The Deputy Inspector "
               "Surveyor (DIS) then reviews both the sketch and the field report, and "
               "either approves or rejects it. The final step belongs to the Tahsildar, "
               "who holds the DSC key and uses it to approve the file and issue the "
               "patta transfer order."),
        "ta": ("ISD (0154) பணிப்பாய்வு, நிலம் பிரிக்கப்படுவதால்: விண்ணப்பம் CSC கவுண்டர், "
               "குடிமகன் போர்ட்டல் அல்லது துணை பதிவாளர் பரிந்துரை வழியாக வருகிறது. "
               "அதன் பிறகு SIS அதிகாரி கட்டாய கள ஆய்வையும் சர்வே சரிபார்ப்பையும் செய்கிறார். "
               "அடுத்து மூத்த வரைவாளர் (SD) உட்பிரிவு வரைபடத்தை தயாரிக்கிறார். "
               "துணை ஆய்வு சர்வேயர் (DIS) வரைபடத்தையும் கள அறிக்கையையும் மதிப்பாய்வு செய்து "
               "ஒப்புதல் அல்லது நிராகரிப்பு அளிக்கிறார். இறுதியாக தாசில்தார் DSC பயன்படுத்தி "
               "ஒப்புதல் அளித்து பட்டா மாற்ற உத்தரவை வழங்குகிறார்."),
    },
    "NISD": {
        "en": ("For NISD (0153) -- a straight patta transfer with no sub-division -- the "
               "application arrives through a CSC counter, the citizen portal, or a "
               "Sub-Registrar referral. The Sub Inspector Surveyor (SIS) checks the "
               "documents; there is no field visit and no SD sketch for this type. The "
               "file then goes to the Zonal Level Tahsildar, who holds the DSC key and "
               "uses it to approve the file and generate the patta transfer order."),
        "ta": ("NISD (0153) -- உட்பிரிவு இல்லாத நேரடி பட்டா மாற்றம் -- விண்ணப்பம் CSC கவுண்டர், "
               "குடிமகன் போர்ட்டல் அல்லது துணை பதிவாளர் பரிந்துரை வழியாக வருகிறது. "
               "SIS அதிகாரி ஆவணங்களை மட்டும் சரிபார்க்கிறார்; கள ஆய்வும் SD வரைபடமும் இல்லை. "
               "பிறகு கோப்பு மண்டல அளவு தாசில்தாரிடம் செல்கிறது, அவர் DSC பயன்படுத்தி "
               "ஒப்புதல் அளித்து பட்டா மாற்ற உத்தரவை உருவாக்குகிறார்."),
    },
}


def _workflow_steps_alt_text(topic: str, is_tamil: bool) -> Optional[str]:
    lang = "ta" if is_tamil else "en"
    if topic not in _WORKFLOW_STEPS_ALT_TEXT and topic != "ALL":
        return None
    if topic == "ALL":
        head = ("ஒவ்வொரு விண்ணப்ப வகைக்கும் பணிப்பாய்வு வேறு:\n\n" if is_tamil
                else "The workflow depends on the application type:\n\n")
        return head + "\n\n".join(_WORKFLOW_STEPS_ALT_TEXT[t][lang] for t in ("ISD", "NISD"))
    return _WORKFLOW_STEPS_ALT_TEXT[topic][lang]


_GLOSSARY_TERMS =(r"(sis|sd|dis|dsc|tahsildar|tahsildhar|tahshildar|tahsildr|tahsildaar|tasildar|thasildar|thasildhar"
                   r"|zdt|dro|patta|sub[\s-]?divisions?|fmb|draughtsman|draftsman"
                   r"|sub\s+inspect(?:or|er)(?:\s+survey(?:or|er))?"
                   r"|deputy\s+inspect(?:or|er)(?:\s+survey(?:or|er))?"
                   r"|senior\s+draughtsman|senior\s+draftsman"
                   r"|district\s+revenue\s+officer)")
_GLOSSARY_ALIAS = {"draughtsman": "sd", "draftsman": "sd", "seniordraughtsman": "sd", "seniordraftsman": "sd",
                   "subinspectorsurveyor": "sis", "deputyinspectorsurveyor": "dis",
                   "districtrevenueofficer": "dro",
                   # Common misspellings of "Tahsildar" -- the task's own
                   # named examples ("Tahshildar, Tahsildr") plus the close
                   # neighbours. All resolve to the same grounded glossary
                   # entry "tahsildhar" already does.
                   "tahshildar": "tahsildar", "tahsildr": "tahsildar", "tahsildaar": "tahsildar",
                   "tasildar": "tahsildar", "thasildar": "tahsildar", "thasildhar": "tahsildar"}
# "sub inspector", "sub inspecter surveyor", "deputy inspecter", "deputy
# inspector surveyer", ... -- every combination the widened regex above now
# matches, generated rather than hand-enumerated so a future misspelling
# added to the regex can't silently fall out of sync with this lookup.
for _prefix, _code in (("sub", "sis"), ("deputy", "dis")):
    for _insp in ("inspector", "inspecter"):
        for _surv in ("", "surveyor", "surveyer"):
            _GLOSSARY_ALIAS[f"{_prefix}{_insp}{_surv}"] = _code
_ROLE_RE = re.compile(
    r"^(?:please\s+)?(?:what\s+is\s+the\s+role\s+of|role\s+of|what\s+does\s+(?:a\s+|an\s+|the\s+)?)\s*(?:a\s+|an\s+|the\s+)?"
    + _GLOSSARY_TERMS + r"\s*(?:do|does)?\s*[?.!]*$", re.IGNORECASE)
# Tanglish word order puts the subject first and "role" before the question
# particle ("zdt role enna", "tahsildar role enna?") -- `_ROLE_RE` above is
# English-order only, and `_GLOSSARY_TA_RE` right below requires the question
# particle immediately after the TERM with no "role" in between, so neither
# matched this. It fell through to `rag.parse_intent`'s generic "short
# message naming a field keyword" fallback instead -- "role" is also a real
# database field name (a workflow actor's role id on one application), so a
# bare "zdt role enna" (3 words) was read as a question about THAT field on
# whatever application happened to be in view, and answered
# `application_status` nonsense instead of the glossary definition.
_ROLE_TA_RE = re.compile(
    r"^" + _GLOSSARY_TERMS + r"\s+(?:role|பங்கு|வேலை)\s*"
    r"(?:என்றால்\s+என்ன|endral\s+enna|na\s+enna|ah\s+enna|enna)?\s*[?.!]*$",
    re.IGNORECASE)
_AFTER_RE = re.compile(
    r"(?:what\s+happens|what\s+is\s+the\s+next\s+step|what\s+comes|next\s+step)\s+after\s+(?:the\s+)?(sis|sd|dis|tahsildar)\b", re.IGNORECASE)
_AFTER = {
    "sis": ("After SIS: an ISD file goes to the Senior Draughtsman (SD) for the sub-division sketch; a NISD file goes straight to the Zonal Level Tahsildar.",
            "SIS-க்குப் பிறகு: ISD கோப்பு உட்பிரிவு வரைபடத்திற்காக மூத்த வரைவாளரிடம் (SD) செல்லும்; NISD கோப்பு நேராக மண்டல நிலை தாசில்தாரிடம் செல்லும்."),
    "sd": ("After SD: the file goes to the Deputy Inspector Surveyor (DIS) to review the sketch and field report. In this register the seeded ISD files are closed at SD (SIS -> SD -> COMPLETED/REJECTED).",
           "SD-க்குப் பிறகு: வரைபடம் மற்றும் கள அறிக்கையை சரிபார்க்க கோப்பு DIS-க்குச் செல்லும். இந்தப் பதிவேட்டில் ISD கோப்புகள் SD-இலேயே முடிகின்றன."),
    "dis": ("After DIS: the Tahsildar applies the DSC to approve and generate the patta transfer order.",
            "DIS-க்குப் பிறகு: தாசில்தார் DSC மூலம் ஒப்புதல் அளித்து பட்டா மாறுதல் ஆணையை உருவாக்குவார்."),
    "tahsildar": ("After the Tahsildar signs with the DSC the order is generated and the file is completed (or rejected).",
                  "தாசில்தார் DSC மூலம் கையொப்பமிட்ட பிறகு ஆணை உருவாகி கோப்பு முடிவடையும் (அல்லது நிராகரிக்கப்படும்)."),
}
_GLOSSARY_RE = re.compile(
    r"^(?:please\s+)?(?:what\s+(?:is|are|does)|who\s+is|who's|whos|what's|whats|define|explain|meaning\s+of|full\s+form\s+of|expand)\s+"
    r"(?:a\s+|an\s+|the\s+)?" + _GLOSSARY_TERMS + r"\s*(?:stand\s+for|mean|means)?\s*[?.!]*$", re.IGNORECASE)
_GLOSSARY_TA_RE = re.compile(
    r"^" + _GLOSSARY_TERMS + r"\s+(?:என்றால்\s+என்ன|endral\s+enna|na\s+enna|ah\s+enna|enna)\s*[?.!]*$", re.IGNORECASE)
_GLOSSARY = {
    "sis": ("SIS = Sub Inspector Surveyor -- the field surveyor who receives an application first. For ISD "
            "the SIS does the mandatory field inspection and boundary verification; for NISD the SIS verifies the documents only.",
            "SIS = Sub Inspector Surveyor (உதவி ஆய்வாளர் சர்வேயர்) — விண்ணப்பத்தை முதலில் பெறும் களப் பணியாளர். ISD-க்கு கட்டாய கள ஆய்வு; NISD-க்கு ஆவண சரிபார்ப்பு மட்டும்."),
    "sd": ("SD = Senior Draughtsman -- prepares the sub-division sketch for ISD files after the SIS field inspection. "
           "NISD files do not go to SD.",
           "SD = Senior Draughtsman (மூத்த வரைவாளர்) — SIS கள ஆய்வுக்குப் பிறகு ISD கோப்புகளுக்கு உட்பிரிவு வரைபடம் தயாரிப்பவர். NISD கோப்புகள் SD-க்கு செல்வதில்லை."),
    "dis": ("DIS = Deputy Inspector Surveyor -- reviews the sub-division sketch and the field report on an ISD file and approves or rejects it. "
            "NISD files skip DIS.",
            "DIS = Deputy Inspector Surveyor (துணை ஆய்வு சர்வேயர்) — ISD கோப்பின் வரைபடத்தையும் கள அறிக்கையையும் சரிபார்த்து ஒப்புதல் / நிராகரிப்பு. NISD-க்கு DIS இல்லை."),
    "dsc": ("DSC = Digital Signature Certificate -- the electronic signing key held by the Tahsildar. Applying it approves the file "
            "and generates the patta transfer order.",
            "DSC = Digital Signature Certificate (மின்னணு கையொப்பச் சான்று) — தாசில்தாரிடம் உள்ள கையொப்ப விசை; இதைப் பயன்படுத்தி கோப்பு ஒப்புதல் பெற்று பட்டா மாறுதல் ஆணை உருவாகும்."),
    "tahsildar": ("Tahsildar -- the revenue officer who holds the DSC key and gives the final approval; the order is generated when the "
                  "Tahsildar signs. For NISD this is the Zonal Level Tahsildar.",
                  "தாசில்தார் — DSC விசையை வைத்திருக்கும் வருவாய் அதிகாரி; இவரது இறுதி ஒப்புதலில் ஆணை உருவாகும். NISD-க்கு இது மண்டல நிலை தாசில்தார்."),
    "patta": ("Patta = the land-ownership record for a survey number. A patta transfer (mutation) moves that record to the new owner; "
              "this assistant reads those applications, it does not change them.",
              "பட்டா = சர்வே எண்ணுக்கான நில உரிமைப் பதிவு. பட்டா மாறுதல் என்பது அந்தப் பதிவை புதிய உரிமையாளருக்கு மாற்றுவது; இந்த உதவியாளர் படிக்க மட்டுமே செய்யும்."),
    "subdivision": ("Sub-division = a part of a survey number that gets its own number (e.g. 24/3). While an ISD file is open it "
                    "carries a temporary number such as 24/T1; the final number is assigned on approval.",
                    "உட்பிரிவு = சர்வே எண்ணின் ஒரு பகுதிக்கு வழங்கப்படும் தனி எண் (எ.கா. 24/3). கோப்பு திறந்திருக்கும்போது 24/T1 போன்ற தற்காலிக எண்; ஒப்புதலில் இறுதி எண்."),
    "fmb": ("FMB (Field Measurement Book) is not part of this register or workflow -- no FMB book, page or sketch is recorded here, "
            "so I cannot say anything about it from the records.",
            "FMB (Field Measurement Book) இந்தப் பதிவேட்டிலோ பணிப்பாய்விலோ இல்லை — FMB புத்தகம், பக்கம் அல்லது வரைபடம் பதிவாகவில்லை; எனவே பதிவுகளிலிருந்து எதுவும் சொல்ல முடியாது."),
}
_GLOSSARY["zdt"] = _GLOSSARY["tahsildhar"] = _GLOSSARY["tahsildar"]
# DRO (District Revenue Officer) is not part of the ordinary ISD/NISD
# processing chain at all -- it is named ONLY in the escalation section
# (workflow_guide.txt's "Level 3 Escalation (45 days overdue)"), which this
# codebase has no deterministic logic for (no escalation-level handler, no
# `current_stage` value for it, and CLAUDE.md records that no seeded
# application even carries "escalated" status). Asked without a deterministic
# answer, this reached the LLM fallback and got "The Tahsildar (Zonal Level
# Tahsildar) holds the DSC and approves the file" -- a fluent, confident
# answer about a DIFFERENT role entirely, to a question about DRO. Grounding
# it here the same way every other role is grounded closes that gap.
_GLOSSARY["dro"] = (
    "DRO = District Revenue Officer -- named only in the escalation section of the workflow guide, notified at "
    "Level 3 Escalation (45 working days overdue): an officer performance review is triggered and the application "
    "may be reassigned. The DRO is not part of the ordinary ISD or NISD processing chain and holds no approval or "
    "sign-off role there -- that stays with the Tahsildar / Zonal Level Tahsildar. This codebase has no "
    "deterministic escalation-level logic and no application in this register is marked \"escalated\".",
    "DRO = மாவட்ட வருவாய் அலுவலர் (District Revenue Officer) — பணிப்பாய்வு வழிகாட்டியின் தீவிரமடைதல் பிரிவில் மட்டுமே "
    "குறிப்பிடப்பட்டவர்; மூன்றாம் நிலை தீவிரமடைதலில் (45 வேலை நாட்கள் தாமதம்) அறிவிக்கப்படுவார். இது வழக்கமான ISD/NISD "
    "செயல்முறையின் ஒரு பகுதி அல்ல; ஒப்புதல் அதிகாரம் தாசில்தார் / மண்டல நிலை தாசில்தாரிடமே உள்ளது.")

# More terms an officer asks the meaning of. Each is stated from the department documents (land_rules,
# workflow_guide, faq) or CLAUDE.md -- no figure is quoted, so nothing here goes stale against the register.
# "define fee" used to be read as "add a fee column" over the list on screen.
_GLOSSARY.update({
    "fee": ("Fee = what an applicant pays for a service: a government fee (a challan payment) plus, when filed through a CSC "
            "counter, the CSC's own service charge. It is fixed per service code (NISD, ISD), not per file. Ask "
            "\"fee for ISD\" for the schedule, or \"fee of <application number>\" for what is recorded on a file.",
            "கட்டணம் = ஒரு சேவைக்கு விண்ணப்பதாரர் செலுத்துவது: அரசுக் கட்டணம் (செலான்) மற்றும் CSC மையம் வழியாக எனில் CSC-யின் சேவைக் கட்டணம். "
            "இது ஒவ்வொரு கோப்புக்கும் அல்ல, சேவைக் குறியீட்டுக்கு (NISD, ISD) நிர்ணயிக்கப்பட்டது. அட்டவணைக்கு \"ISD கட்டணம்\" எனக் கேளுங்கள்."),
    "sla": ("SLA = the service time limit for a file, in working days from submission to completion. It is a range per service "
            "code: a file is within it up to the lower figure, in the SLA window between the two, and past it only after the upper "
            "figure. Working days are Monday to Friday; the register has no holiday calendar. Ask \"how long has <application> been "
            "pending\" for a file's position.",
            "SLA = ஒரு கோப்புக்கான சேவை நேர வரம்பு — சமர்ப்பிப்பு முதல் முடிவு வரை வேலை நாட்களில். இது சேவைக் குறியீட்டுக்கு ஒரு வரம்பு: "
            "குறைந்த எண்ணுக்குள் 'வரம்பிற்குள்', இரண்டுக்கும் இடையில் 'SLA சாளரம்', அதிக எண்ணுக்குப் பிறகே 'மீறியது'. வேலை நாட்கள் திங்கள்–வெள்ளி; "
            "விடுமுறை நாட்காட்டி பதிவேட்டில் இல்லை."),
    "overdue": ("Overdue = an open ISD file whose field visit has not been completed more than 15 working days after "
                "submission. A completed visit stops the clock; NISD files have no field visit, so no such deadline. This is "
                "separate from the service SLA.",
                "காலதாமதம் (Overdue) = திறந்திருக்கும் ISD கோப்பில் சமர்ப்பித்த 15 வேலை நாட்களுக்குள் கள ஆய்வு முடிக்கப்படாதது. "
                "ஆய்வு முடிந்தால் கணக்கீடு நிற்கும்; NISD-க்கு கள ஆய்வு இல்லை, எனவே இந்த வரம்பு இல்லை. இது சேவை SLA-விலிருந்து வேறு."),
    "pending": ("Pending = the file is at the SIS desk waiting for the officer's action. (\"my pending applications\" lists them.)",
                "நிலுவை (Pending) = கோப்பு SIS மேசையில் அதிகாரியின் நடவடிக்கைக்காகக் காத்திருக்கிறது."),
    "inprogress": ("In progress = the file has left the SIS desk and is being processed at a later stage (for example with the "
                   "Tahsildar) but has not been decided.",
                   "செயல்பாட்டில் (In progress) = கோப்பு SIS மேசையை விட்டு அடுத்த கட்டத்தில் (எ.கா. தாசில்தார்) உள்ளது; இன்னும் முடிவாகவில்லை."),
    "approved": ("Approved (also called completed) = the file was decided in favour: the Tahsildar applied the DSC and the patta "
                 "transfer order was generated; its workflow ends at COMPLETED.",
                 "ஒப்புதல் (Approved / completed) = கோப்பு ஏற்கப்பட்டது: தாசில்தார் DSC பயன்படுத்தி பட்டா மாறுதல் ஆணை உருவானது; பணிப்பாய்வு COMPLETED-இல் முடியும்."),
    "rejected": ("Rejected = the file was decided against; its workflow ends at REJECTED. Rejected files stay out of ordinary "
                 "lists unless you ask for them.",
                 "நிராகரிப்பு (Rejected) = கோப்பு ஏற்கப்படவில்லை; பணிப்பாய்வு REJECTED-இல் முடியும். கேட்காவிட்டால் சாதாரண பட்டியல்களில் வராது."),
    "escalated": ("Escalated = a file raised to a higher level, for example after being overdue. No file in the current register "
                  "carries this status.",
                  "உயர்நிலைக்கு அனுப்பப்பட்டது (Escalated) = காலதாமதம் போன்ற காரணங்களால் உயர் மட்டத்திற்கு அனுப்பப்பட்ட கோப்பு. தற்போதைய பதிவேட்டில் எதுவும் இல்லை."),
    "fieldvisit": ("A field visit is the on-site verification the SIS carries out at the parcel: measuring the boundaries and area, "
                   "checking the boundary stones, noting any encroachment, and taking the signatures of the applicant and joint "
                   "owners. It is mandatory for ISD, must be scheduled within 15 working days of submission, and its date "
                   "can only be changed with the Tahsildar's approval. NISD files are verified from documents and normally need none.",
                   "கள ஆய்வு = நிலத்தில் SIS செய்யும் நேரடிச் சரிபார்ப்பு: எல்லைகள், பரப்பளவு அளவீடு, எல்லைக் கற்கள், ஆக்கிரமிப்பு, விண்ணப்பதாரர் மற்றும் "
                   "கூட்டு உரிமையாளர்களின் கையொப்பம். ISD-க்கு கட்டாயம்; சமர்ப்பித்த 15 வேலை நாட்களுக்குள் திட்டமிட வேண்டும்; தேதி மாற்ற தாசில்தார் ஒப்புதல் தேவை. "
                   "NISD ஆவணங்கள் மூலம் சரிபார்க்கப்படும்."),
    "litigation": ("Litigation = a court case or dispute recorded against a survey number. The system checks for litigation flags; if "
                   "one is found, confirm with the Tahsildar before proceeding with the field visit.",
                   "வழக்கு (Litigation) = சர்வே எண்ணின் மீது பதிவான நீதிமன்ற வழக்கு / தகராறு. வழக்குக் குறியீடு இருந்தால் கள ஆய்வுக்கு முன் தாசில்தாரிடம் உறுதிப்படுத்தவும்."),
    "encroachment": ("Encroachment = a building or use extending onto land that is not the owner's (for example onto a road, channel "
                     "or tank). The SIS records it during the field visit, and the flag is visible to the Senior Draughtsman.",
                     "ஆக்கிரமிப்பு = உரிமையாளருக்குரியதல்லாத நிலத்தில் (சாலை, கால்வாய், குளம்) கட்டிடம் / பயன்பாடு நீள்வது. கள ஆய்வின்போது SIS பதிவு செய்யும்; "
                     "அந்தக் குறியீடு மூத்த வரைவாளருக்குத் தெரியும்."),
    "survey": ("A survey number identifies a land parcel; a sub-division (for example 24/3) is a part of it with its own number. Each "
               "survey number carries a patta number and its owners.",
               "சர்வே எண் = ஒரு நிலப்பகுதியை அடையாளப்படுத்தும் எண்; உட்பிரிவு (எ.கா. 24/3) அதன் ஒரு பகுதி. ஒவ்வொரு சர்வே எண்ணுக்கும் பட்டா எண்ணும் உரிமையாளர்களும் உண்டு."),
    "ward": ("Ward = a division of a town; a block is part of a ward, and survey numbers sit in blocks. Your jurisdiction is the "
             "wards and blocks you are posted to.",
             "வார்டு = நகரின் ஒரு பிரிவு; தொகுதி (block) வார்டின் பகுதி; சர்வே எண்கள் தொகுதிகளில் உள்ளன. உங்கள் அதிகார வரம்பு நீங்கள் நியமிக்கப்பட்ட வார்டுகள் / தொகுதிகள்."),
    "block": ("Block = a part of a ward that holds survey numbers (block codes look like 0015). Geography runs district, taluk, "
              "town, ward, block, survey number.",
              "தொகுதி (Block) = வார்டின் ஒரு பகுதி; அதில் சர்வே எண்கள் உள்ளன (எ.கா. 0015). அமைப்பு: மாவட்டம், வட்டம், நகரம், வார்டு, தொகுதி, சர்வே எண்."),
    "jurisdiction": ("Jurisdiction = the wards and blocks you are posted to. You can see and ask about applications and survey numbers "
                     "there only; anything outside it is refused.",
                     "அதிகார வரம்பு = நீங்கள் நியமிக்கப்பட்ட வார்டுகள் / தொகுதிகள். அங்குள்ள விண்ணப்பங்களையும் சர்வே எண்களையும் மட்டுமே பார்க்க முடியும்; வெளியே உள்ளவை மறுக்கப்படும்."),
    "mutation": ("Mutation = changing the patta (land-ownership record) to the new owner after a transfer. An ISD / NISD "
                 "application asks for one; this assistant reads those applications and cannot change them.",
                 "பட்டா மாறுதல் (Mutation) = உரிமை மாற்றத்திற்குப் பிறகு பட்டாவை புதிய உரிமையாளர் பெயருக்கு மாற்றுவது. ISD / NISD விண்ணப்பம் இதைக் கோருகிறது; "
                 "இந்த உதவியாளர் படிக்க மட்டுமே செய்யும்."),
    "sketch": ("Sketch = the sub-division sketch the Senior Draughtsman (SD) prepares for an ISD file after the SIS "
               "field inspection.",
               "வரைபடம் (Sketch) = ISD கோப்புக்கு SIS கள ஆய்வுக்குப் பிறகு மூத்த வரைவாளர் (SD) தயாரிக்கும் உட்பிரிவு வரைபடம்."),
    "chitta": ("Natham chitta = the record of habitation (natham) land and its owners, held in the urban natham chitta.",
               "நத்தம் சிட்டா = நத்தம் (குடியிருப்பு) நிலம் மற்றும் அதன் உரிமையாளர்களின் பதிவு."),
    "priority": ("Priority = applications flagged for an approaching deadline or an earlier escalation are treated as highest "
                 "priority. Ask \"my priority applications\" for the list.",
                 "முன்னுரிமை = நெருங்கும் காலக்கெடு அல்லது முந்தைய உயர்நிலை அனுப்புதல் உள்ள விண்ணப்பங்கள் அதிக முன்னுரிமை. பட்டியலுக்கு \"முன்னுரிமை விண்ணப்பங்கள்\" எனக் கேளுங்கள்."),
    "can": ("CAN = Citizen Access Number, the unique identity number a citizen is issued and quotes on an application; every "
            "application carries one. Its length (15 or 12 digits) names the counter that issued it, not the submission channel.",
            "CAN = Citizen Access Number — குடிமகனுக்கு வழங்கப்படும் தனித்துவ அடையாள எண்; ஒவ்வொரு விண்ணப்பத்திலும் உண்டு. அதன் நீளம் (15 / 12 இலக்கம்) வழங்கிய கவுண்டரைக் குறிக்கும், சமர்ப்பிப்பு வழியை அல்ல."),
    "igrs": ("IGRS = Inspector General of Registration and Stamps -- the department that registers property documents and "
             "collects stamp duty through the Sub-Registrar Offices (SROs).",
             "IGRS = Inspector General of Registration and Stamps (பதிவுகள் மற்றும் முத்திரைகள் துறை) — சார்-பதிவாளர் அலுவலகங்கள் (SRO) மூலம் "
             "சொத்து ஆவணங்களைப் பதிவு செய்து முத்திரைத் தீர்வை வசூலிக்கும் துறை."),
    "igrsform6": ("IGRS Form 6 number = the number the Sub-Registrar's IGRS system issues when it raises a patta mutation off a "
                  "registered deed. Only Sub-Registrar (SRO) referrals carry one; CSC and citizen files never do, and that is the "
                  "rule, not a gap.",
                  "IGRS படிவம் 6 எண் = பதிவு செய்யப்பட்ட பத்திரத்திலிருந்து பட்டா மாறுதலை IGRS அமைப்பு உருவாக்கும்போது வழங்கும் எண். சார்-பதிவாளர் (SRO) "
                  "விண்ணப்பங்களுக்கு மட்டுமே; CSC / குடிமகன் கோப்புகளுக்கு இல்லை — அது விதி, குறை அல்ல."),
    "sro": ("SRO = Sub-Registrar Office, where a sale deed is registered. Its IGRS system raises the patta mutation off the "
            "registered deed and issues an IGRS Form 6 number; such a file reaches the SIS desk with that number and no operator code.",
            "SRO = Sub-Registrar Office (பதிவு அலுவலகம்). பத்திரம் பதிவானதும் அதன் IGRS அமைப்பு பட்டா மாறுதலை உருவாக்கி IGRS படிவம் 6 எண்ணை வழங்கும்."),
    "csc": ("CSC = Common Service Centre, the counter where an operator files an application for a citizen (a CSC charge applies "
            "at the counter). It is one of three submission channels: CSC, Sub-Registrar (SRO) and the citizen's own filing at a revenue camp.",
            "CSC = Common Service Centre — குடிமகன் சார்பாக ஆபரேட்டர் விண்ணப்பிக்கும் கவுண்டர் (கவுண்டர் கட்டணம் உண்டு). மூன்று வழிகளில் ஒன்று: CSC, சார்-பதிவாளர் (SRO), வருவாய் முகாமில் குடிமகன்."),
    "citizen": ("Citizen channel = an application the citizen files at a revenue camp (the camp flag is 'P'). It carries no IGRS "
                "Form 6 number.",
                "குடிமகன் வழி = வருவாய் முகாமில் குடிமகன் தாக்கல் செய்யும் விண்ணப்பம் (camp_flag 'P'). IGRS படிவம் 6 எண் இருக்காது."),
    "channel": ("Channel = how an application reached the SIS desk: through a CSC counter, as a Sub-Registrar (SRO) referral, or "
                "filed by a citizen at a revenue camp. It is derived from the record's source_name and camp_flag, never from the "
                "CAN number's length.",
                "வழி (Channel) = விண்ணப்பம் SIS மேசைக்கு வந்த விதம்: CSC கவுண்டர், சார்-பதிவாளர் (SRO) பரிந்துரை, அல்லது வருவாய் முகாமில் குடிமகன். "
                "இது பதிவின் source_name, camp_flag-இலிருந்து தீர்மானிக்கப்படும்; CAN நீளத்திலிருந்து அல்ல."),
    "stage": ("Stage = where a file sits in its workflow chain: SIS, SD, DIS or Tahsildar, ending at COMPLETED or REJECTED.",
              "கட்டம் (Stage) = கோப்பு பணிப்பாய்வில் இருக்கும் இடம்: SIS, SD, DIS அல்லது தாசில்தார்; COMPLETED / REJECTED-இல் முடியும்."),
    "status": ("Status = the outcome so far of a file: pending, in progress, approved (completed) or rejected. Stage is where it "
               "is; status is what has happened to it.",
               "நிலை (Status) = கோப்பின் இதுவரையான முடிவு: நிலுவை, செயல்பாட்டில், ஒப்புதல், நிராகரிப்பு. கட்டம் என்பது இருக்கும் இடம்; நிலை என்பது நடந்தது."),
    "application": ("An application is a request to transfer a patta: NISD (whole survey number), ISD (a part, involving "
                    "sub-division). Each has an application number like 2026/0154/28/001280.",
                    "விண்ணப்பம் = பட்டா மாறுதலுக்கான கோரிக்கை: NISD (முழு சர்வே எண்), ISD (ஒரு பகுதி, உட்பிரிவுடன்). "
                    "ஒவ்வொன்றுக்கும் 2026/0154/28/001280 போன்ற எண் உண்டு."),
})
# what "define <word>" reads as: the entry above (aliases), and the terms a bare "what is" may also claim
_GLOSSARY.update({"completed": _GLOSSARY["approved"], "complete": _GLOSSARY["approved"], "fees": _GLOSSARY["fee"],
                  "slas": _GLOSSARY["sla"], "surveynumber": _GLOSSARY["survey"], "surveynumbers": _GLOSSARY["survey"],
                  "wards": _GLOSSARY["ward"], "blocks": _GLOSSARY["block"], "fieldvisits": _GLOSSARY["fieldvisit"],
                  "fieldinspection": _GLOSSARY["fieldvisit"], "nathamchitta": _GLOSSARY["chitta"],
                  "subregistrar": _GLOSSARY["sro"], "escalation": _GLOSSARY["escalated"], "pendings": _GLOSSARY["pending"],
                  "applications": _GLOSSARY["application"], "inprogres": _GLOSSARY["inprogress"], "priorities": _GLOSSARY["priority"]})
_GLOSS_NOUNS = (r"(fees?|slas?|can|igrs|csc|sro|sub[\s-]?registrar|citizen|channels?|survey(?:\s+numbers?)?|wards?|blocks?|"
                r"jurisdiction|mutation|sketch|natham\s+chitta|chitta|litigation|encroachment|field\s+(?:visits?|inspection)|priority|priorities)")
# statuses read as a list request after a bare "what is" ("what is pending"), so only an explicit "define" / "meaning" claims them
_GLOSS_STATES = (r"(pending|approved|rejected|completed|complete|in[\s-]?progress|escalated|escalation|overdue|stage|status|application)")
_GLOSS_NOUN_RE = re.compile(
    r"^(?:please\s+)?(?:what\s+(?:is|are|does)|what's|whats|define|explain|meaning\s+of|what\s+is\s+meant\s+by|full\s+form\s+of|expand)\s+"
    r"(?:a\s+|an\s+)?" + _GLOSS_NOUNS + r"\s*(?:stand\s+for|mean|means)?\s*[?.!]*$", re.IGNORECASE)
_GLOSS_STATE_RE = re.compile(
    r"^(?:please\s+)?(?:define|explain|meaning\s+of|what\s+is\s+meant\s+by|what\s+does)\s+(?:a\s+|an\s+|the\s+)?" + _GLOSS_STATES + r"\s*(?:stand\s+for|mean|means)?\s*[?.!]*$", re.IGNORECASE)
_GLOSS_TRAIL_RE = re.compile(
    r"^" + _GLOSS_NOUNS.replace("(fees?", "(fees?", 1) + r"\s+(?:meaning|definition|means|mean)\s*[?.!]*$"
    r"|^" + _GLOSS_STATES + r"\s+(?:meaning|definition)\s*[?.!]*$", re.IGNORECASE)
_GLOSS_TA2_RE = re.compile(
    r"^(?:" + _GLOSS_NOUNS + r"|" + _GLOSS_STATES + r"|(கட்டணம்|காலதாமதம்|நிலுவை))\s*(?:என்றால்\s+என்ன|endral\s+enna|na\s+enna|ah\s+enna|enna|என்றால்|என்ன)\s*[?.!]*$", re.IGNORECASE)
_GLOSS_TA_WORD = {"கட்டணம்": "fee", "காலதாமதம்": "overdue", "நிலுவை": "pending"}
_WHO_APPROVES = {
    "ISD": ("For ISD the DIS reviews the sketch and field report and approves or rejects; the Tahsildar then applies the DSC to generate the order.",
            "ISD-க்கு DIS வரைபடத்தையும் கள அறிக்கையையும் சரிபார்த்து ஒப்புதல்/நிராகரிப்பு; பின்னர் தாசில்தார் DSC மூலம் ஆணை உருவாக்குவார்."),
    "NISD": ("For NISD there is no DIS: the SIS verifies the documents and the Zonal Level Tahsildar approves with the DSC and generates the order.",
             "NISD-க்கு DIS இல்லை: SIS ஆவணங்களை சரிபார்க்கும்; மண்டல நிலை தாசில்தார் DSC மூலம் ஒப்புதல் அளித்து ஆணை உருவாக்குவார்."),
}
_WORKFLOW_ALL_RE = re.compile(
    r"^\s*(?:please\s+)?(?:explain|describe|what(?:'s|\s+is|\s+are)?|how\s+(?:does|do|is)|tell\s+me|give\s+me|walk\s+me\s+through)"
    r"\b.*\b(?:work\s*flow|workflow|process|procedure|steps)\b", re.IGNORECASE)
# Tamil verbs ("விளக்கு", "விளக்கவும்") end in a dependent vowel sign or a
# virama, neither of which is `\w` -- the same trap CLAUDE.md documents
# elsewhere: a trailing `\b` right after one lands between two non-word
# characters (the sign and the following space), which is never a boundary,
# so folding them into `_WORKFLOW_ALL_RE`'s single `\b`-bounded group (as they
# used to be) meant neither Tamil verb ever matched anything. Checked as its
# own unbounded pattern instead.
_WORKFLOW_ALL_RE_TA = re.compile(
    r"^\s*(?:விளக்கு|விளக்கவும்).*(?:work\s*flow|workflow|process|procedure|steps|பணிப்பாய்வு|செயல்முறை)",
    re.IGNORECASE)


def _workflow_all_match(msg: str) -> bool:
    return bool(_WORKFLOW_ALL_RE.search(msg) or _WORKFLOW_ALL_RE_TA.search(msg))


_WORKFLOW_ALL_BLOCK_RE = re.compile(
    r"\bfield\s+visit\b|\bfee\b|\bsla\b|\bdocuments?\b|\bpending\b|\bprocessing\b|\bcsc\b|\bigrs\b|\bcan\b"
    r"|\bsurvey\b|\bwhy\b|\bhow\s+(?:do|can|should)\s+i\b", re.IGNORECASE)

_APP_NO_HISTORY_RE = re.compile(r'\d{4}/\d{3,4}/\d{1,3}/\d+')


def _recent_app_number_in_history(chat_history) -> bool:
    """Whether an application number was named in the last couple of turns.

    A quick, narrow check for guards that run before the real follow-up
    context is loaded (see its use above _workflow_steps_topic): it only
    needs to know THAT an application is in view, not which fields to carry
    forward, so a plain regex over the tail of the history is enough --
    no need to duplicate _load_followup_context's own resolution here.
    """
    for m in list(chat_history or [])[-4:]:
        if _APP_NO_HISTORY_RE.search(str(m.get("content") or "")):
            return True
    return False


def _workflow_steps_topic(message: str, chat_history=None) -> Optional[str]:
    """"what are the ISD/NISD workflow steps" -- ISD or NISD, never both (a
    message naming both is a comparison, handled elsewhere), no listing verb
    (so "show my ISD applications" is untouched) and no application number
    (a question about one file's own stage, not the general chain).

    A documented, fixed fact -- exactly the kind of thing that must never be
    left to the LLM: asked this without a deterministic answer, it invented a
    workflow that skipped the SD (Senior Draughtsman) step entirely and added
    an unstated "24 hour" SLA. `_WORKFLOW_STEPS_WORD_RE` and the isd/nisd
    match both tolerate the typos and Tamil/Tanglish phrasings an officer
    actually types; "isd"/"nisd" themselves stay exact-only, the same rule
    CLAUDE.md documents everywhere else so "isd" can never absorb "nisd".
    """
    if not message:
        return None
    msg = message.lower()
    if re.search(r'\d{4}/\d{3,4}/\d{1,3}/\d+', msg):
        return None
    # "what is ITS latest recorded workflow event?" right after narrowing to one
    # application (e.g. "the second one" from a listing) names no ISD/NISD word
    # at all, so the generic "ALL" catch-all below answered with the bare
    # policy explanation ("The workflow depends on the application type: ISD
    # ... NISD ...") -- completely ignoring the file actually in view, which
    # may not even be ISD/NISD (a register-only code like 0158 has its own
    # application-aware workflow handler, `_other_code_workflow`, that never
    # got a turn). A pronoun ("its"/"it's"/"this"/"that") next to a
    # record-shaped word ("latest"/"last"/"recorded"/"recent"/"current") asks
    # about ONE file's own history, never the general chain -- stand aside
    # whenever an application was actually named recently, so the
    # application-aware handlers reached later in the pipeline get the turn.
    if (re.search(r"\b(?:its|it'?s|this|that)\b.{0,15}\b(?:latest|last|recorded|recent|current)\b"
                  r"|\b(?:latest|last|recorded|recent|current)\b.{0,15}\b(?:its|it'?s)\b", msg)
            and not re.search(r"\b(?:isd|nisd|0153|0154)\b", msg)
            and _recent_app_number_in_history(chat_history)):
        return None
    # A bare "explain in detail" / "explain briefly" right after a workflow
    # answer names no ISD/NISD/workflow word of its own -- every check below
    # would return None for it, the same gap CLAUDE.md documents for
    # service_code_lookup ("explain briefly" -> "explain in detail" still
    # meaning the code just explained). Read the topic off the PREVIOUS
    # assistant turn instead of re-deriving it from this message.
    if (chat_history and _is_explain_request(msg)
            and not re.search(r"\b(?:isd|nisd|0153|0154)\b", msg)
            and not re.search(r"work\s*fl\w*|procs?e?ss\w*|procedure|பணிப்பாய்வு|செயல்முறை", msg)):
        for _m in reversed(chat_history[-6:]):
            if not isinstance(_m, dict) or (_m.get("role") or "user") != "assistant":
                continue
            _content = (_m.get("content") or "")
            if not _content:
                continue
            if _content.startswith("ISD (") or "ISD (0154)" in _content[:30]:
                return "ISD"
            if _content.startswith("NISD (") or "NISD (0153)" in _content[:30]:
                return "NISD"
            if (_content.startswith("The workflow depends on the application type")
                    or _content.startswith("ஒவ்வொரு விண்ணப்ப வகைக்கும்")):
                return "ALL"
            break  # only the most recent assistant turn is a continuation
    if _fullform_term(msg):
        return None          # "full form of IGRS" is the one-line expansion (special scope), not the definition
    _g = (_GLOSSARY_RE.match(msg.strip()) or _GLOSSARY_TA_RE.match(msg.strip())
          or _ROLE_RE.match(msg.strip()) or _ROLE_TA_RE.match(msg.strip()))
    if _g:
        _k = re.sub(r"[\s-]", "", _g.group(1).lower())
        return "GLOSS:" + _GLOSSARY_ALIAS.get(_k, _k)
    _g2 = (_GLOSS_NOUN_RE.match(msg.strip()) or _GLOSS_STATE_RE.match(msg.strip()) or _GLOSS_TRAIL_RE.match(msg.strip())
           or _GLOSS_TA2_RE.match(msg.strip()))
    if _g2:
        _raw = next((g for g in _g2.groups() if g), "") or ""
        _raw = _GLOSS_TA_WORD.get(_raw, _raw)
        _k = re.sub(r"[\s-]", "", _raw.lower())
        if _k in _GLOSSARY:
            return "GLOSS:" + _k
    _after = _AFTER_RE.search(msg)
    if _after:
        return "AFTER:" + _after.group(1).lower()
    _next_step_q = bool(re.search(
        r"^(?:please\s+|and\s+|then\s+)?(?:what\s+comes\s+next|what\s+is\s+the\s+next\s+step|what\s+is\s+next|what\s+next|next\s+step|what\s+happens\s+next|continue\s+with\s+(?:the\s+)?next\s+steps?|adutha\s+step\s+enna|அடுத்த\s+படி\s+என்ன|அடுத்த\s+கட்டம்\s+என்ன|அடுத்து\s+என்ன)\b[?.!]*$",
        msg
    ))
    if _next_step_q:
        _last_text = ""
        if chat_history:
            for _m in reversed(chat_history[-4:]):
                if isinstance(_m, dict):
                    _c = (_m.get("content") or "").lower()
                    if _c:
                        _last_text += " " + _c
        if re.search(r"\b(?:dis|deputy\s+inspector)\b", _last_text):
            return "AFTER:dis"
        elif re.search(r"\b(?:sd|draughtsman|draftsman)\b", _last_text):
            return "AFTER:sd"
        elif re.search(r"\b(?:sis|sub\s+inspector|surveyor|isd|nisd|0153|0154)\b", _last_text):
            return "AFTER:sis"
        else:
            return "CLARIFY_WORKFLOW"
    _who = re.search(r"\bwho\s+(?:app?rov\w*|signs?|decides|gives\s+the\s+final)\b.*\b(isd|nisd)\b", msg)
    if _who:
        return "WHO:" + _who.group(1).upper()
    if (not re.search(r"\b(?:isd|nisd|0153|0154)\b", msg) and len(msg.split()) <= 6
            and re.search(r"work\s*fl\w*|procs?e?ss\w*|procedure|பணிப்பாய்வு|செயல்முறை", msg)
            # "விளக்க" is the STEM (explain), not the imperative "விளக்கு" --
            # "விளக்கு" is a substring of "விளக்குங்கள்" but NOT of the polite
            # "விளக்கவும்" (they diverge right after the shared stem: கு vs
            # கவும்), so "பணிப்பாய்வு விளக்கவும்" ("please explain the
            # workflow") carried none of the words this checked for and fell
            # through to the LLM, which then took 2+ minutes and timed out.
            # The stem is a substring of every conjugation, the same pattern
            # "சொல்" (not "சொல்லு") already uses right beside it.
            and re.search(r"explain|enna|sollu|pannu|what|how|tell|describe|விளக்க|என்ன|சொல்", msg)
            and not _WORKFLOW_ALL_BLOCK_RE.search(msg) and not _WORKFLOW_LISTING_RE.search(msg)):
        return "ALL"
    # A role/step question that never says "workflow"/"steps"/"process" at all
    # ("who prepares the subdivision sketch?", "what is the DIS's
    # responsibility after SD prepares the sketch?", "who carries out the
    # final digital sign-off?") names the chain just as surely as one that
    # does -- it names a STEP NOUN (sketch, temporary subdivision number,
    # field verification, digital sign-off, DSC/patta order) or a STEP ROLE
    # that exists in only one chain (SD/DIS: ISD only; ZDT: NISD only)
    # instead. Without this these fell through to `general_query` and were
    # either misrouted to an application-specific handler (which then asked
    # for an application number or said "not found", for a question that
    # named no application at all) or reached the LLM, which has hallucinated
    # a wrong role for exactly this kind of question before (see
    # `_workflow_steps_topic`'s own docstring). Gated on a real "who"/"which
    # role"/"responsib*" question shape and a short-ish sentence, so an
    # ordinary data question that happens to mention "DIS" or "sketch" in
    # passing (longer, differently shaped) is left alone.
    # A narrower listing-exclusion than `_WORKFLOW_LISTING_RE`: that one's
    # `\bapplic\w*\b` blocks the bare word "application", which a role
    # question ends up using naturally ("...of an ISD APPLICATION?") without
    # asking for a listing at all -- it is the LISTING VERBS that signal a
    # listing, not the noun every such question is implicitly about.
    if (not _WORKFLOW_STEPS_WORD_RE.search(msg) and len(msg.split()) <= 20
            and re.search(r"\bwho\b|\bwhich\s+role\b|\bresponsib\w*", msg)
            and not re.search(r"\bshow\b|\blist\b|\bdisplay\b|\bmy\b|\bpending\b"
                              r"|\bhow\s+many\b|\bcount\b|\bapps?\b|\bfiles?\b", msg)
            and (_WORKFLOW_STEP_NOUN_RE.search(msg) or _WORKFLOW_STEP_ROLE_RE.search(msg))):
        _has_isd = (bool(re.search(r'\b(isd|0154)\b', msg)) or bool(_WORKFLOW_STEP_ROLE_RE.search(msg))
                    or bool(_WORKFLOW_ISD_ONLY_NOUN_RE.search(msg)))
        _has_nisd = bool(re.search(r'\b(nisd|0153)\b', msg)) or bool(re.search(r'\bzdt\b', msg))
        if _has_isd and not _has_nisd:
            return "ISD"
        if _has_nisd and not _has_isd:
            return "NISD"
        # Both or neither is genuinely ambiguous (e.g. the question names no
        # service code and no role unique to one chain) -- fall through
        # rather than guess which chain was meant.

    # A claim to be VERIFIED ("ZDT handles ISD and DIS handles NISD,
    # correct?"), not a question -- naming a role from each chain plus a
    # verification tag at the end. Confirmed live: this used to reach
    # `general_query` (after the service-code/application-listing traps
    # above were fixed) and llama3.1:8b answered "Yes, that is correct" to a
    # claim that is backwards -- DIS exists only in the ISD chain, ZDT only
    # in NISD -- agreeing with the officer rather than correcting a false
    # premise. Routed to the "ALL" side-by-side comparison instead: it
    # states both chains' real roles in one answer, which is self-correcting
    # (DIS appears only under ISD, ZDT only under NISD) without this
    # function having to parse out and negate the specific claim made.
    if (re.search(r'\b(?:correct|right|isn\'?t\s+it|is\s+that\s+(?:so|right|correct))\s*\??\s*$',
                 msg.strip(), re.IGNORECASE)
            and _WORKFLOW_STEP_ROLE_RE.search(msg) and re.search(r'\bzdt\b', msg)):
        return "ALL"

    if not _WORKFLOW_STEPS_WORD_RE.search(msg):
        return None
    if _WORKFLOW_LISTING_RE.search(msg):
        return None
    has_isd = bool(re.search(r'\b(isd|0154)\b', msg))
    has_nisd = bool(re.search(r'\b(nisd|0153)\b', msg))
    if sum([has_isd, has_nisd]) >= 2:
        return "ALL" if len(msg.split()) <= 10 and _workflow_all_match(msg) else None
    if has_isd:
        return "ISD"
    if has_nisd:
        return "NISD"
    if (len(msg.split()) <= 9 and _workflow_all_match(msg)
            and not _WORKFLOW_ALL_BLOCK_RE.search(msg)):
        return "ALL"
    return None


def _workflow_steps_answer(topic: str, is_tamil: bool, brief: bool = False) -> str:
    lang = "ta" if is_tamil else "en"
    if topic == "CLARIFY_WORKFLOW":
        return ("Do you mean the ISD (0154) workflow (Involving Sub-Division) or NISD (0153) workflow (Not Involving Sub-Division)? Please specify which procedure you would like to know the next steps for."
                if not is_tamil else
                "ISD (0154) பணிப்பாய்வா அல்லது NISD (0153) பணிப்பாய்வா? எந்த நடைமுறையின் அடுத்த கட்டத்தை அறிய விரும்புகிறீர்கள் என்று குறிப்பிடவும்.")
    if topic.startswith("GLOSS:"):
        return _GLOSSARY[topic[6:]][1 if is_tamil else 0]
    if topic.startswith("AFTER:"):
        return _AFTER[topic[6:]][1 if is_tamil else 0]
    if topic.startswith("WHO:"):
        return _WHO_APPROVES[topic[4:]][1 if is_tamil else 0]
    # `topic == "ALL"` never matched a key of `_WORKFLOW_STEPS_BRIEF_TEXT`
    # itself (only "ISD"/"NISD" are keys there), so `brief` was silently
    # ignored for the combined ISD+NISD answer: "explain the workflow step by
    # step" -> "explain briefly" gave back the exact same full chain. Brief
    # now governs the ISD/NISD pieces it is built from instead of requiring
    # "ALL" itself to be a brief-text key.
    _src = _WORKFLOW_STEPS_BRIEF_TEXT if brief else _WORKFLOW_STEPS_TEXT
    if topic == "ALL":
        head = ("ஒவ்வொரு விண்ணப்ப வகைக்கும் பணிப்பாய்வு வேறு:\n\n" if is_tamil
                else "The workflow depends on the application type:\n\n")
        return head + "\n\n".join(_src[t][lang] for t in ("ISD", "NISD"))
    return _src.get(topic, _WORKFLOW_STEPS_TEXT.get(topic, {}))[lang]


def _last_assistant_message(chat_history) -> str:
    for _m in reversed(list(chat_history or [])[-4:]):
        if isinstance(_m, dict) and (_m.get("role") or "user") == "assistant":
            return _m.get("content") or ""
    return ""


async def _workflow_steps_answer_natural(topic: str, is_tamil: bool, brief: bool,
                                          language: str, chat_history=None) -> str:
    """`_workflow_steps_answer`, escalated or re-worded ONLY when it would
    otherwise repeat the immediately preceding assistant turn word-for-word
    (asking "briefly"/"in detail" twice in a row reads as the bot being
    stuck/robotic).

    A repeated BRIEF request gets the full chain instead -- asking again
    after already getting the short version means the short version wasn't
    enough, the same reading "explain in detail" gets on its own.

    A repeated FULL request (already as detailed as this topic gets) gets
    `_WORKFLOW_STEPS_ALT_TEXT` -- a second, hand-written phrasing of the exact
    same facts. An LLM rewrite was tried here first, grounded and validated
    the same way `_service_code_lookup_answer_natural` paraphrases a service
    code -- but THIS prompt (the full 5-step ISD/NISD chain, much longer than
    a service-code lookup) measured 45-63s against this project's Tamil-tuned
    Ollama model in testing: past any timeout worth making an officer wait
    on, and it silently fell back to the identical text anyway, which was the
    bug being fixed here. The hand-written alt text costs nothing and can
    never drift a fact."""
    fact_text = _workflow_steps_answer(topic, is_tamil, brief)
    _last = _last_assistant_message(chat_history)
    if fact_text != _last:
        return fact_text
    if brief:
        return _workflow_steps_answer(topic, is_tamil, brief=False)
    alt = _workflow_steps_alt_text(topic, is_tamil)
    if alt and alt != _last:
        return alt
    return fact_text


# ── MERGE is not a separate application type here ─────────────────────────
# No row in appl_log_urban is a merge application, and
# row in appl_log_urban ever uses it -- `applications.application_type`
# (`ck_application_type`) admits only ISD and NISD. A question about a MERGE
# application, a MERGE listing, or "isd vs merge" must say so directly and
# never reach general_query: past that point the LLM was handed the ISD/NISD
# context it *was* given and invented MERGE application numbers to answer
# with -- exactly the fabrication CLAUDE.md's "never let the LLM generate
# counts or application numbers" rule exists to stop. This check runs before
# the follow-up layer and before the qualifier guard's "I don't know how to
# filter by X" reply, so "merge" always gets a definitive answer, whatever
# turn it arrives on -- a fresh question, a follow-up fragment ("what about
# merge", "only merge"), or a comparison ("isd vs merge").
_MERGE_WORD_RE = re.compile(r'\bmerg(?:e|es|ed|ing)?\b', re.IGNORECASE)
# இணைப்பு / இணைக்க also mean "attachment" / "enclosure" elsewhere in this
# codebase (attachment_qa.py's upload cues, "இணைப்பு சான்றிதழ்" = enclosure
# certificate, "இணைப்பு விவரம்" = attachment details) -- and this check runs
# before every other handler, including the attachment pipeline. A bare
# "இணைப்பு" is therefore too wide; only count it near "விண்ணப்ப" (application),
# the same "merge APPLICATION" shape the English word requires implicitly
# by running inside an applications-only chatbot.
_MERGE_TA_RE = re.compile(
    r'(?:இணைப்பு|இணைக்க)[^.?!\n]{0,20}விண்ணப்ப|விண்ணப்ப[^.?!\n]{0,20}(?:இணைப்பு|இணைக்க)')

# "merged application" / "merge parent" / "parent application" / "merged
# with" / "merge group" are a real, still-existing field lookup --
# merged_application_id, the lead application of a group of ISD files filed
# together for one large split (see `_field_map` a few hundred lines down).
# They are NOT about the removed MERGE application type and must be excluded
# here, or a legitimate question about that field would be answered "there
# is no MERGE type" before the field-lookup code below ever saw it.
_MERGE_FIELD_LOOKUP_RE = re.compile(
    r'\bmerged\s+application\b|\bmerge\s+parent\b|\bparent\s+application\b'
    r'|\bmerged\s+with\b|\bmerge\s+group\b', re.IGNORECASE)

# "upload the merged PDF" describes a FILE, not the application type -- an
# attachment question with no application/type/code word in it is left to the
# attachment pipeline instead of being told there is no MERGE type.
_MERGE_UPLOAD_RE = re.compile(
    r'\b(upload|uploaded|attach|attached|attachment|pdf|scan|scanned)\b', re.IGNORECASE)
_MERGE_APP_CONTEXT_RE = re.compile(
    r'\bapplication|\bapp\b|\bisd\b|\bnisd\b|விண்ணப்ப', re.IGNORECASE)


_MERGE_FIELD_WORDING_RE = re.compile(
    r"\bmerg(?:e|ed)\s+(?:id|applid|applied|appl\s*id|application\s+id|number|no)\b|\bwith\s+which\s+application\b|"
    r"\bmerged\b|இணைக்கப்பட்ட\s+விண்ணப்ப", re.IGNORECASE)


def _is_merge_question(message: str) -> bool:
    msg = message or ""
    if (_MERGE_FIELD_WORDING_RE.search(msg)
            and not re.search(r"service\s*code|\bupload|\bpdf\b", msg, re.IGNORECASE)):
        return False  # "is <app> merged / with which application is <app> merged": a field of that file
    if _MERGE_FIELD_LOOKUP_RE.search(msg):
        return False
    if _MERGE_UPLOAD_RE.search(msg) and not _MERGE_APP_CONTEXT_RE.search(msg):
        return False
    if _MERGE_WORD_RE.search(msg) or _MERGE_TA_RE.search(msg):
        return True
    # A slipped letter ("merg", "mreg", "mearge") is still the same word --
    # the same typo tolerance the rest of parse_intent applies everywhere else.
    for w in re.findall(r"[a-zA-Z]+", msg):
        if len(w) >= 4 and is_token_typo_match(w, "merge"):
            return True
    return False


def _no_merge_type_answer(is_tamil: bool) -> str:
    if is_tamil:
        return ("இந்த பதிவேட்டில் இணைப்பு (MERGE) என்பது தனி சேவை அல்ல; ஒரு ISD விண்ணப்பத்தில் பதிவாகும் "
                "இணைப்பு எண் (merge id) மட்டுமே உள்ளது. எ.கா. \"<விண்ணப்ப எண்> merge id என்ன?\" என்று கேளுங்கள்.")
    return ("Merging is not a separate service in this register; it is recorded only as a merge id on an ISD "
            "application. Ask for example \"merge id of <application number>\", or \"which applications have a merge id\".")


# "what is not SRO", "what if it is not from the sub registrar", "non-SRO", "SRO illana", "SRO அல்லாத" --
# the negation is the question: what a file is when it did NOT come through the Sub-Registrar.
_NOT_SRO_RE = re.compile(
    r"\b(?:not|non|other\s+than|apart\s+from|besides|except|without)\s*[-\s]?(?:an?\s+|the\s+|from\s+|by\s+)*"
    r"(?:sro|sub[\s-]?registrar)\b"
    r"|\b(?:sro|sub[\s-]?registrar)\s+(?:illa\w*|alla\w*|vendam|thavira)\b"
    r"|(?:sro|சார்[\s-]?பதிவாளர்|பதிவாளர்)\s*(?:அல்லாத|இல்லாத|தவிர)", re.IGNORECASE)
_NOT_SRO_ASK_RE = re.compile(
    r"\bwh?at\b|\bmeans?\b|\bthen\b|\bif\b|\benna\b|என்ன|என்றால்", re.IGNORECASE)


_FV_RULE_ANSWERS = {
    "fv_needed": ("Field visits are needed for ISD (0154, Involving Sub-Division): the parcel is split, so the SIS must inspect it on site and the sub-division sketch follows. An open ISD file "
                  "whose visit is not completed more than 15 working days after submission is overdue. NISD (0153, Not "
                  "Involving Sub-Division) needs no field visit -- it is a document check only."),
    "fv_not_needed": ("NISD (0153, Not Involving Sub-Division) does not need a field visit: the whole survey number is "
                      "transferred, so the SIS only verifies documents. ISD (0154) does need one."),
    "fv_who": ("The Sub Inspector Surveyor (SIS) carries out the field visit -- the on-site check of boundaries, area, "
               "boundary stones and any encroachment -- for ISD files."),
    "fv_why": ("A field visit is needed when the parcel is split (ISD): the SIS must verify the "
               "boundaries and area on site before the Senior Draughtsman prepares the sub-division sketch. NISD files "
               "transfer the whole parcel, so they have none."),
}


_FV_RULE_TA = {
    "fv_needed": ("ISD (0154, உட்பிரிவுடன்) விண்ணப்பங்களுக்கு கள ஆய்வு தேவை: நிலம் பிரிக்கப்படுகிறது, "
                  "எனவே SIS நேரில் சென்று சரிபார்க்க வேண்டும்; அதன் பிறகே உட்பிரிவு வரைபடம். திறந்திருக்கும் ISD கோப்பில் சமர்ப்பித்த 15 வேலை நாட்களுக்குள் "
                  "ஆய்வு முடியாவிட்டால் அது காலதாமதம். NISD (0153) க்கு கள ஆய்வு தேவையில்லை — ஆவண சரிபார்ப்பு மட்டுமே."),
    "fv_not_needed": ("NISD (0153) க்கு கள ஆய்வு தேவையில்லை: முழு சர்வே எண்ணும் மாற்றப்படுகிறது, எனவே SIS ஆவணங்களை மட்டுமே சரிபார்க்கும். "
                      "ISD (0154) க்கு தேவை."),
    "fv_who": ("கள ஆய்வை உதவி ஆய்வாளர் சர்வேயர் (SIS) செய்கிறார் — எல்லைகள், பரப்பளவு, எல்லைக் கற்கள், ஆக்கிரமிப்பு ஆகியவற்றின் நேரடிச் சரிபார்ப்பு; "
               "ISD கோப்புகளுக்கு."),
    "fv_why": ("நிலம் பிரிக்கப்படும் (ISD) போது கள ஆய்வு தேவை: மூத்த வரைவாளர் உட்பிரிவு வரைபடம் தயாரிக்கும் முன் SIS எல்லைகளையும் "
               "பரப்பளவையும் நேரில் சரிபார்க்க வேண்டும். NISD முழு நிலத்தையும் மாற்றுவதால் அதற்கு இல்லை."),
}
_FV_TA_WORDS = r"(?:கள\s*ஆய்வு|kala\s*aaivu|kalaaivu)"


# A negation flips the answer only when it governs the field-visit requirement itself:
# "do not require a field visit", "need no field visit", "without a field visit",
# "field visit is not required". In "require a field visit without prior approval" or
# "... if no sketch is available" the negation belongs to another condition.
_FV_NEED_WORD = r"(?:need|requir|necessar|mandatory|compulsory)"
_FV_VISIT_WORD = r"field\s*[- ]?(?:visits?|inspections?)"
_FV_NEG_NEED_RE = re.compile(
    rf"\b(?:not|never|dont|doesnt|don't|doesn't|isn't|aren't|won't)\s+(?:\w+\s+)?{_FV_NEED_WORD}"
    rf"|\b{_FV_NEED_WORD}\w*\s+no\s+(?:\w+\s+)?{_FV_VISIT_WORD}"
    rf"|\bno\s+(?:need\b|{_FV_VISIT_WORD})"
    rf"|\bwithout\s+(?:a\s+|any\s+|the\s+)?{_FV_VISIT_WORD}"
    rf"|\b{_FV_VISIT_WORD}\s+(?:is\s+|are\s+)?(?:not|never)\s+(?:\w+\s+)?{_FV_NEED_WORD}")


def _field_visit_rule_topic(msg: str) -> Optional[str]:
    """"which service needs a field visit", "who does the field visit", "why is a field visit needed" -- the
    rule, not the officer's own visit table. Nothing that mentions the officer's own files or a count."""
    if not re.search(rf"{_FV_VISIT_WORD}|{_FV_TA_WORDS}", msg) or re.search(
            r"\b(?:my|mine|i|me|list|show|display|how\s+many|count|scheduled|pending|overdue|completed|today|tomorrow|week|month)\b|\d", msg):
        return None
    # This whole topic only ever has an ISD/NISD answer baked in (_FV_RULE_ANSWERS).
    # "does TSLR Owner Name Correction need a field visit" matched the generic
    # "does ... need ... field visit" shape below and got the blanket ISD/NISD
    # rule text regardless -- the real answer for 0165 is "no", but the
    # officer was told about 0154/0153, a different code entirely. A message
    # that names a service code OTHER than ISD/NISD defers to the
    # per-code answer (service_code_lookup / describe_service_code), which
    # already states that code's own requires_field_visit flag correctly.
    from backend.utils.helpers import find_service_codes as _fvr_find_codes
    _fvr_codes = _fvr_find_codes(msg)
    if _fvr_codes and any(c not in ("0153", "0154") for c in _fvr_codes):
        return None
    need = r"(?:need\w*|requir\w*|necessar\w*|mandatory|compulsory|venum|vendum|vendam|thevai\w*|தேவை\w*|வேண்டும்|கட்டாயம்|kattayam)"
    # Tamil / Tanglish: "எந்த சேவைக்கு கள ஆய்வு தேவை", "evlo service ku field visit venum"
    if re.search(r"(?:எந்த|எது|evlo|edhu|ethu|yentha|entha)\s*(?:சேவை|வகை|விண்ணப்ப|service|type|application)", msg) and re.search(need, msg):
        return "fv_not_needed" if re.search(r"(?:தேவையில்லை|வேண்டாம்|vendam|thevai\s*illa|venda)", msg) else "fv_needed"
    # yes / no about the rule: "is a field visit needed for ISD", "do all applications need a field visit"
    if not re.search(r"\b(?:which|what)\b", msg) and re.search(r"\b(?:is|are|does|do|should|must|will)\b", msg) and re.search(need, msg) and re.search(
            r"\b(?:field\s*[- ]?visits?)\b", msg) and not re.search(r"\bnisd\b", msg):
        return "fv_needed"
    # "which SERVICE CODES require a field visit" names the whole 29-code
    # catalog by its own term, not the ISD/NISD "type" framing the rest of
    # this topic is built around -- it used to match the generic `kind`
    # check below and get the same two-sentence ISD/NISD-only prose, so a
    # question about every code's field-visit flag was answered about just
    # 2 of the 11 that actually carry one (0158, 0159, 0160, 0164, 0169,
    # 0178 need a visit too; 0156, 0162, 0165 do not) -- exactly the
    # hardcoded-allowlist omission this audit looks for. "type(s)"/"kind(s)
    # of application" keeps the existing ISD/NISD answer: "type" is
    # overloaded as ISD/NISD everywhere else in this app.
    if re.search(r"\b(?:which|what)\s+(?:service\s*)?codes?\b", msg):
        return "fv_not_needed_catalog" if _FV_NEG_NEED_RE.search(msg) else "fv_needed_catalog"
    kind = r"(?:services?|types?|application\s+types?|applications?\s+types?|kinds?\s+of\s+applications?|categor\w+)"
    if re.search(rf"\b(?:which|what)\s+{kind}\b", msg):
        return "fv_not_needed" if _FV_NEG_NEED_RE.search(msg) else "fv_needed"
    if re.search(r"\bwho\b.*\b(?:does|do|did|conducts?|carr(?:y|ies)\s+out|performs?|makes?|goes?)\b", msg):
        return "fv_who"
    _neg = _FV_NEG_NEED_RE.search(msg)
    if re.search(r"\bwhy\b.*\b(?:need\w*|required|require\w*|necessary|mandatory|must|compulsory)\b", msg):
        return "fv_not_needed" if _neg else "fv_why"
    return None


def _igrs_can_rule_topic(message: str, allow_bare_tamil_what: bool = True) -> Optional[str]:
    """Which IGRS/CAN rule the officer is asking about, if any.

    Returns None for anything naming an application -- that is a question about
    one file, answered from its record, not from the rule. `allow_bare_tamil_what`
    is False when the caller already has an application in view from context, so
    a bare "CAN எண் என்ன?" -- ambiguous between the definition and a follow-up --
    is left to the context-aware answer instead of claimed here.
    """
    msg = (message or "").lower()
    if not msg:
        return None
    if re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", msg):
        return None
    _fv = _field_visit_rule_topic(msg)
    if _fv:
        return _fv
    if _fullform_term(msg) and re.search(r"full\s*form|expansion|abbreviation", msg):
        return None        # "igrs full form", "sro full form": the expansion, not the Form 6 / channel rule
    # "what is IGRS" / "full form of IGRS" ask what IGRS is; the glossary answers that, then explains Form 6
    if (re.fullmatch(r"(?:please\s+)?(?:what\s+(?:is|are|does)|what's|whats|define|explain|meaning\s+of|full\s*form\s+of|expand)\s+"
                     r"(?:an?\s+|the\s+)?igrs\s*(?:stand\s+for|mean|means)?\s*[?.!]*", msg.strip())
            or re.fullmatch(r"igrs\s*(?:என்றால்\s*என்ன|endral\s+enna|na\s+enna|ah\s+enna|enna|என்றால்|என்ன)\s*[?.!]*", msg.strip())):
        return None
    # "what is the CSC fee" asks for a recorded amount, not what a CSC is.
    if re.search(r"\b(fee|fees|charge|charges|cost|price)\b|கட்டண", msg):
        return None

    # "on what basis are you deciding SRO or CSC or citizen?" -- a question
    # about the RULE, not about the register. It was answered with a table of
    # 54 applications and a channel breakdown: a correct summary of the data,
    # and no answer at all to what was asked. Same failure CLAUDE.md already
    # records for the IGRS rule -- "answering it with a table of applications
    # answers a question nobody asked".
    #
    # Needs BOTH halves: something asking how the decision is made, and the
    # channels it is made between. "show applications from CSC" carries the
    # second and not the first, and must stay a listing.
    if _CHANNEL_BASIS_RE.search(msg) and _CHANNEL_NAMED_RE.search(msg):
        _one = _channels_defined(msg)
        return "channel_basis:" + _one[0] if len(_one) == 1 else "channel_basis"

    # "what is SRO and CSC?" -- a definition question naming SEVERAL channels.
    # `sro_what` answered only about the Sub-Registrar and never mentioned CSC,
    # so half the question went unanswered with no sign that it had. The topic
    # carries the channels it found, so each one asked about gets explained.
    _named_channels = _channels_defined(msg)
    # A question that names applications wants those applications listed, so no
    # channel DEFINITION is claimed from it.
    _wants_list = _asks_for_channel_list(msg)
    if (not _wants_list and len(_named_channels) > 1
            and (_SRO_ROLE_RE.search(msg) or "யார்" in msg
                 or "என்றால்" in msg)):
        return "channel_defn:" + ",".join(_named_channels)

    if not _wants_list and _NOT_SRO_RE.search(msg) and _NOT_SRO_ASK_RE.search(msg):
        return "not_sro"
    # A role question about the Sub-Registrar / SRO -- see `_SRO_ROLE_RE`.
    if (not _wants_list and _SRO_RE.search(msg)
            and (_SRO_ROLE_RE.search(msg) or "யார்" in msg)):
        return "sro_what"
    # ...and the same question about either of the other two channels, which
    # had no topic at all: "what is CSC" fell through to the LLM.
    if (not _wants_list and len(_named_channels) == 1
            and (_SRO_ROLE_RE.search(msg) or "யார்" in msg
                 or "என்றால்" in msg)):
        return "channel_defn:" + _named_channels[0]

    has_igrs = ("igrs" in msg or "form 6" in msg or "form6" in msg or "படிவம் 6" in msg)
    # "can" is also the commonest modal in English, so a bare one is never
    # taken as the identifier. It counts only next to "number"/"no", spelled
    # out, or beside a digit-length phrase ("does a 15 digit CAN mean...").
    # "CAN எண்" is how the identifier is written in Tamil -- the field map had
    # the same gap (CLAUDE.md, "Tamil needs a bare `igrs` keyword").
    has_can = bool(re.search(r"\bcan\s+(?:number|no|num)\b|citizen\s+access"
                             r"|\bcan\s*எண்|குடிமகன்\s*அணுகல்", msg))
    _len_phrase = re.search(r"\b1[25]\s*-?\s*digits?\b", msg)
    if not has_can and _len_phrase and re.search(r"\bcan\b", msg):
        has_can = True
    # "what is CAN" names the identifier outright, with no "number" after it.
    if not has_can and re.search(r"\bwhat\s+(?:is|are)\s+can\b", msg):
        has_can = True

    # A record has no "format", "length" or "number of digits" -- only the rule
    # does. So a meta word beside CAN/IGRS makes this a rule question whatever
    # article it uses, and the article distinction `_RULE_DEFN_RE` guards
    # ("what is A can number" vs "what is THE can number") does not apply.
    # Without this, "what is the CAN number format" fell to general_query and
    # llama3.1:8b spent 111 SECONDS answering *"The CAN number format is not
    # mentioned in the search results. However ... survey numbers are unique
    # identifiers for land parcels"* -- about a different field entirely.
    if (has_can or has_igrs) and _META_FIELD_RE.search(msg):
        if has_igrs and has_can:
            return "igrs_equals_can"
        if has_can:
            # A yes/no shape ("does a 15-digit CAN mean CSC?") is a correction
            # and keeps can_length's "No."; a "what is the format/length"
            # question is the same fact stated forwards.
            # "can" is deliberately NOT a yes/no starter here: "CAN எண் வடிவம்
            # என்ன" begins with the identifier, not with a modal, and reading
            # it as one answered a "what is the format" question with "No."
            _yes_no = re.search(r"^\s*(?:does|do|is|are|would|will)\b"
                                r"|\bmean(?:s)?\b|\bஎன்று\s*அர்த்தமா", msg)
            return "can_length" if _yes_no else "can_format"
        return "igrs_who"

    if not _is_rule_shaped(msg, allow_bare_tamil_what=allow_bare_tamil_what):
        return None

    if has_igrs and _ABSENT_RE.search(msg):
        return "igrs_absent"
    if has_igrs and has_can:
        return "igrs_equals_can"
    if has_igrs:
        return "igrs_who"
    if has_can and (_len_phrase
                    or re.search(r"\bdigits?\b|\blength\b|\blong\b", msg)):
        return "can_length"
    if has_can:
        return "can_what"
    if _SRO_RE.search(msg) and not _wants_list:
        return "sro_what"
    return None


# How a channel was decided, in words an officer (or anyone) can follow: no field names, no
# table names, nothing about how the register is stored.
_CHANNEL_BASIS_EN = {
    "sub_registrar": (
        "These are Sub-Registrar (SRO) applications because of how they reached the office. When a "
        "sale deed is registered at the Sub-Registrar's office, the registration system sends the "
        "property-transfer (patta) request to the survey office by itself. Nobody at a CSC counter, "
        "and not the citizen, types it in -- it arrives on its own, straight from the registered "
        "deed. That is how the record marks it as an SRO file.\n"
        "One more sign backs this up: files that come this way carry a registration reference number "
        "(IGRS Form 6), and CSC or citizen files never do.\n"
        "Give me an application number and I will show what the record says for that file."),
    "CSC": (
        "A file is a CSC file when an operator at a Common Service Centre (CSC) counter typed it in "
        "on the citizen's behalf, and it was not entered at a special revenue camp. The record "
        "shows the operator who entered it -- Sub-Registrar files show none -- and a CSC file "
        "carries no registration reference number (IGRS Form 6).\n"
        "Give me an application number and I will show what the record says for that file."),
    "citizen": (
        "A file is marked as the citizen's own when it was filed at a special revenue camp. At a "
        "camp, an operator keys the application in while the citizen stands in front of them, so it "
        "counts as the citizen's own submission rather than a CSC counter's. The record flags it as "
        "a camp entry, and it carries no registration reference number (IGRS Form 6).\n"
        "Give me an application number and I will show what the record says for that file."),
    "": (
        "The channel is decided by how the application actually reached the office -- it is read "
        "from the record, not guessed:\n"
        "1. Sub-Registrar (SRO): nobody typed it in. When a sale deed is registered at the "
        "Sub-Registrar's office, the registration system sends the transfer request to the survey "
        "office by itself.\n"
        "2. Citizen: filed at a special revenue camp. An operator at the camp keys it in while the "
        "citizen stands in front of them, and the record is marked as a camp entry.\n"
        "3. CSC: every other file typed in by an operator at a Common Service Centre counter.\n"
        "The length of the CAN (citizen access) number plays no part in this -- it only says which "
        "counter issued the number, not which route the file took.\n"
        "Give me an application number and I will show what the record says for that file."),
}
_CHANNEL_BASIS_TA = {
    "sub_registrar": (
        "இவை சார்-பதிவாளர் (SRO) விண்ணப்பங்கள் -- அலுவலகத்துக்கு வந்த விதத்தால். சார்-பதிவாளர் "
        "அலுவலகத்தில் ஒரு விற்பனைப் பத்திரம் பதிவு செய்யப்பட்டதும், பதிவு அமைப்பே பட்டா மாற்றக் "
        "கோரிக்கையை நில அளவை அலுவலகத்துக்கு அனுப்பிவிடுகிறது. CSC மையத்திலோ, குடிமகனோ அதை "
        "உள்ளிடுவதில்லை -- பதிவு செய்யப்பட்ட பத்திரத்திலிருந்து தானாகவே வருகிறது. அதனால்தான் பதிவேடு "
        "அதை SRO கோப்பு என்று குறிக்கிறது.\n"
        "இன்னொரு அடையாளம்: இப்படி வரும் கோப்புகளுக்கு பதிவு குறிப்பு எண் (IGRS படிவம் 6) இருக்கும்; "
        "CSC அல்லது குடிமகன் கோப்புகளுக்கு ஒருபோதும் இருக்காது.\n"
        "ஒரு விண்ணப்ப எண்ணைத் தந்தால், அந்தக் கோப்பின் பதிவு என்ன சொல்கிறது என்று காட்டுகிறேன்."),
    "CSC": (
        "பொது சேவை மைய (CSC) கவுண்டரில் உள்ள ஆபரேட்டர் குடிமகன் சார்பாக விண்ணப்பத்தை உள்ளிட்டு, அது "
        "சிறப்பு வருவாய் முகாமில் உள்ளிடப்படாமல் இருந்தால் அது CSC கோப்பு. அதை உள்ளிட்ட ஆபரேட்டர் "
        "பதிவில் இருக்கும் (சார்-பதிவாளர் கோப்புகளில் இருக்காது); CSC கோப்புக்கு பதிவு குறிப்பு எண் "
        "(IGRS படிவம் 6) இருக்காது.\n"
        "ஒரு விண்ணப்ப எண்ணைத் தந்தால், அந்தக் கோப்பின் பதிவு என்ன சொல்கிறது என்று காட்டுகிறேன்."),
    "citizen": (
        "சிறப்பு வருவாய் முகாமில் தாக்கல் செய்யப்பட்ட கோப்பு குடிமகனின் சொந்த கோப்பாகக் குறிக்கப்படும். "
        "முகாமில் குடிமகன் முன்னால் நிற்க, ஆபரேட்டர் விண்ணப்பத்தை உள்ளிடுகிறார்; எனவே அது CSC "
        "கவுண்டரின் அல்ல, குடிமகனின் சொந்த சமர்ப்பிப்பு. பதிவு அதை முகாம் பதிவு என்று குறிக்கும்; "
        "பதிவு குறிப்பு எண் (IGRS படிவம் 6) இருக்காது.\n"
        "ஒரு விண்ணப்ப எண்ணைத் தந்தால், அந்தக் கோப்பின் பதிவு என்ன சொல்கிறது என்று காட்டுகிறேன்."),
    "": (
        "விண்ணப்பம் அலுவலகத்துக்கு உண்மையில் வந்த விதத்தை வைத்தே வழி தீர்மானிக்கப்படுகிறது -- பதிவிலிருந்து "
        "படிக்கப்படுகிறது, யூகம் அல்ல:\n"
        "1. சார்-பதிவாளர் (SRO): யாரும் உள்ளிடவில்லை. சார்-பதிவாளர் அலுவலகத்தில் பத்திரம் பதிவானதும் "
        "பதிவு அமைப்பே கோரிக்கையை அனுப்புகிறது.\n"
        "2. குடிமகன்: சிறப்பு வருவாய் முகாமில் தாக்கல்; ஆபரேட்டர் உள்ளிடுகிறார், பதிவு முகாம் பதிவாகக் குறிக்கப்படும்.\n"
        "3. CSC: மற்ற அனைத்தும் -- CSC கவுண்டரில் ஆபரேட்டர் உள்ளிட்டவை.\n"
        "CAN எண்ணின் நீளம் இதில் பங்கு வகிக்காது -- அது எண்ணை வழங்கிய கவுண்டரை மட்டுமே காட்டும்.\n"
        "ஒரு விண்ணப்ப எண்ணைத் தந்தால், அந்தக் கோப்பின் பதிவு என்ன சொல்கிறது என்று காட்டுகிறேன்."),
}


def _igrs_can_rule_answer(topic: str, language: str) -> str:
    """The rule, stated. No counts, so nothing here can go stale against data."""
    is_tamil = language in ("ta", "tanglish")
    if topic in ("fv_needed_catalog", "fv_not_needed_catalog"):
        # Computed from SIS_URBAN_SERVICES itself -- never a hardcoded
        # ISD/NISD-only list -- so a code added to the catalog later is
        # read from there, not forgotten here.
        from backend.utils.helpers import SIS_URBAN_SERVICES
        wants = topic == "fv_needed_catalog"
        codes = sorted(c for c, info in SIS_URBAN_SERVICES.items()
                       if bool(info.get("requires_field_visit")) == wants)
        listing = ", ".join(f"{c} ({SIS_URBAN_SERVICES[c]['name']})" for c in codes)
        if is_tamil:
            verb = "கள ஆய்வு தேவை" if wants else "கள ஆய்வு தேவையில்லை"
            return f"{len(codes)} சேவை குறியீடுகளுக்கு {verb}: {listing}."
        verb = "require a field visit" if wants else "do not require a field visit"
        return f"{len(codes)} service codes {verb}: {listing}."
    if topic in _FV_RULE_ANSWERS:
        return (_FV_RULE_TA if is_tamil else _FV_RULE_ANSWERS)[topic]

    # "what is SRO and CSC?" -- one definition per channel named, in the order
    # the vocabulary lists them, so asking about two gets two answers instead
    # of one and a silent omission.
    if topic.startswith("channel_defn:"):
        table = _CHANNEL_DEFN_TA if is_tamil else _CHANNEL_DEFN_EN
        parts = [table[c] for c in topic.split(":", 1)[1].split(",") if c in table]
        return "\n\n".join(parts)

    if topic.startswith("channel_basis"):
        return (_CHANNEL_BASIS_TA if is_tamil else _CHANNEL_BASIS_EN).get(topic.partition(":")[2], "")

    if topic == "not_sro":
        if is_tamil:
            return ("சார்-பதிவாளர் (SRO) வழியாக வராத விண்ணப்பம் CSC கவுண்டர் வழியாகவோ, வருவாய் முகாமில் குடிமகனாலோ தாக்கல் "
                    "செய்யப்பட்டது. அதற்கு IGRS படிவம் 6 எண் இருக்காது — அது குறை அல்ல, சார்-பதிவாளரிடம் பின்தொடர எதுவும் இல்லை. "
                    "இரண்டையும் வேறுபடுத்துவது `camp_flag`: 'P' எனில் குடிமகன் (வருவாய் முகாம்), மற்றவை CSC.")
        return ("A file that did not come through the Sub-Registrar (SRO) was filed at a CSC counter, or by a citizen at a "
                "revenue camp. It carries no IGRS Form 6 number -- that is the rule for those channels, not a gap, and there "
                "is nothing to chase with the Sub-Registrar. The two are told apart by `camp_flag`: 'P' is a citizen "
                "(revenue camp) file, anything else is CSC.")
    if is_tamil:
        return {
            "channel_basis": (
                "விண்ணப்ப பதிவேட்டில் உள்ள இரண்டு புலங்களிலிருந்து வழி "
                "தீர்மானிக்கப்படுகிறது — யூகம் அல்ல:\n"
                "1. `source_name` இடம்பெயர்ப்பு (`-`) எனில், எந்த ஆபரேட்டர் "
                "கணக்கும் அந்தக் கோப்பைத் தொடவில்லை — அது சார்-பதிவாளர் (SRO) "
                "பரிந்துரை; பதிவு செய்யப்பட்ட பத்திரத்திலிருந்து IGRS மாற்றத்தை "
                "உருவாக்கியுள்ளது.\n"
                "2. ஆபரேட்டர் உள்ள வரிசையில் `camp_flag = 'P'` எனில், அது "
                "வருவாய் முகாம் — குடிமகனின் சார்பாக பதிவு செய்யப்பட்டது, "
                "எனவே 'குடிமகன்'.\n"
                "3. மற்ற அனைத்தும் CSC கவுண்டர்.\n"
                "CAN எண்ணின் நீளம் இதில் பயன்படுத்தப்படவில்லை — அது எண்ணை "
                "வழங்கிய கவுண்டரைக் குறிக்கிறது, வழியை அல்ல. ஒரு குறிப்பிட்ட "
                "விண்ணப்பத்திற்கான காரணத்தைப் பார்க்க அதன் எண்ணைத் தரவும்."),
            "igrs_absent": (
                "IGRS படிவம் 6 எண்ணை பதிவு அலுவலகத்தின் (SRO) IGRS அமைப்பு மட்டுமே "
                "உருவாக்குகிறது — பதிவு செய்யப்பட்ட பத்திரத்திலிருந்து. எனவே IGRS எண் "
                "இல்லாத விண்ணப்பம் SRO-விலிருந்து வரவில்லை: அது CSC மையம் "
                "வழியாகவோ, வருவாய் முகாமில் குடிமகனாலோ தாக்கல் செய்யப்பட்டது. "
                "அந்த வழிகளுக்கு காலி புலம் விதிதான், பதிவில் உள்ள குறை அல்ல."),
            "igrs_who": (
                "பதிவு அலுவலக (SRO) பரிந்துரை விண்ணப்பங்கள் மட்டுமே IGRS படிவம் 6 "
                "எண்ணைப் பெறுகின்றன. CSC மற்றும் குடிமகன் விண்ணப்பங்களுக்கு "
                "எதுவும் இல்லை."),
            "igrs_equals_can": (
                "SRO பரிந்துரையில் IGRS படிவம் 6 எண்ணும் CAN எண்ணும் ஒன்றே — அது "
                "பதிவு செய்யப்பட்ட பத்திரத்தைக் குறிக்கிறது."),
            "can_length": (
                "இல்லை. CAN எண்ணின் நீளம் அதை வழங்கிய கவுண்டரைக் குறிக்கிறது, "
                "விண்ணப்ப வழியை அல்ல: 15 இலக்கம் (133 தொடர்) CSC கவுண்டர், "
                "12 இலக்கம் தமிழ்நாடு குடிமக்கள் இணையதளம். வழி source_name மற்றும் "
                "camp_flag-லிருந்து தனியாக கணிக்கப்படுகிறது."),
            "can_format": (
                "CAN எண் 12 அல்லது 15 இலக்கம். அந்த நீளம் எண்ணை வழங்கிய கவுண்டரைக் "
                "குறிக்கிறது — 15 இலக்கம் (133 தொடர்) CSC கவுண்டர், "
                "12 இலக்கம் தமிழ்நாடு குடிமக்கள் இணையதளம் — விண்ணப்ப வழியை அல்ல. "
                "வழி source_name மற்றும் camp_flag-லிருந்து தனியாகக் கணிக்கப்படுகிறது."),
            "can_what": (
                "CAN என்பது Citizen Access Number — குடிமகனுக்கு வழங்கப்படும் தனித்துவ "
                "அடையாள எண். அதன் நீளம் அதை வழங்கிய கவுண்டரைக் குறிக்கிறது: "
                "15 இலக்கம் CSC, 12 இலக்கம் தமிழ்நாடு இணையதளம்."),
            "sro_what": (
                "SRO என்பது Sub-Registrar Office (பதிவு அலுவலகம்). பத்திரம் பதிவு "
                "செய்யப்பட்டதும், அதன் IGRS அமைப்பு பட்டா மாற்ற விண்ணப்பத்தை "
                "தானாகவே உருவாக்கி IGRS படிவம் 6 எண்ணை வழங்குகிறது."),
        }.get(topic, "")
    return {
        "channel_basis": (
            "The channel is derived from two fields on the application log "
            "record, not inferred:\n"
            "1. If `source_name` is the placeholder `-`, no operator account "
            "touched the file — it came in from the Sub-Registrar (SRO), where "
            "IGRS raised the mutation off the registered deed.\n"
            "2. On an operator row, `camp_flag = 'P'` marks a special revenue "
            "camp. The camp operator keys the file in for the citizen standing "
            "in front of them, so the submission is the citizen's own.\n"
            "3. Every other operator row is a CSC counter.\n"
            "The CAN number's length plays no part in this — it names the "
            "counter that issued the number, not the route the file took. Give "
            "an application number and I will show the values it was decided "
            "from."),
        "igrs_absent": (
            "An IGRS Form 6 number is raised only by the Sub-Registrar Office's "
            "(SRO) IGRS system, off the registered deed. So an application with "
            "no IGRS number did not come from the SRO — it was filed at a CSC "
            "counter, or by the citizen at a revenue camp. For those two "
            "channels an empty IGRS field is the rule, not a gap in the record, "
            "and it is not something to chase up."),
        "igrs_who": (
            "Only Sub-Registrar (SRO) referrals carry an IGRS Form 6 number — it "
            "names the registered deed the mutation was raised from. CSC "
            "and citizen applications never have one."),
        "igrs_equals_can": (
            "On a Sub-Registrar referral they are the same number: the IGRS Form "
            "6 number equals that application's CAN, and it names the registered "
            "deed. On a CSC or citizen application there is no IGRS number at "
            "all, only a CAN."),
        "can_length": (
            "No. A CAN's length names the counter that ISSUED the number, not "
            "the channel the application came in through: 15 digits (133 series) "
            "from a CSC counter, 12 digits from the TN citizen portal. "
            "The channel is a separate fact, derived from source_name and "
            "camp_flag — a Sub-Registrar referral, for instance, carries a "
            "12-digit portal-issued CAN."),
        # "what is the CAN number format?" is the same fact stated forwards.
        # It shares can_length's content but not its opening "No.", which
        # answers a yes/no question and is a non-sequitur here.
        "can_format": (
            "A CAN is 12 or 15 digits. The length names the counter that issued "
            "it — 15 digits (133 series) from a CSC counter, 12 digits "
            "from the TN citizen portal — and not the channel the application "
            "came in through, which is a separate fact derived from source_name "
            "and camp_flag. A Sub-Registrar referral, for instance, carries a "
            "12-digit portal-issued CAN."),
        "can_what": (
            "CAN is the Citizen Access Number — the unique identity number a "
            "citizen is issued and quotes on an application. Its length names "
            "the counter that issued it: 15 digits (133 series) from a CSC "
            "counter, 12 digits from the TN citizen portal. It does not "
            "by itself tell you the submission channel."),
        "sro_what": (
            "SRO is the Sub-Registrar Office, where a sale deed is registered. "
            "When a deed is registered there, its IGRS system raises the patta "
            "mutation off the registered document and issues an IGRS Form 6 "
            "number. An application that reaches the SIS desk that way carries "
            "that number and no operator code; the officer's own checks on "
            "extent and boundaries still apply."),
    }.get(topic, "")


def _igrs_over_list_note(message: str, applications: list) -> Optional[str]:
    """One sentence answering "do these carry an IGRS Form 6 number?"."""
    if not applications or not _IGRS_QUESTION_RE.search(message or ""):
        return None
    channels = [a.get("submission_channel") for a in applications]
    with_igrs = sum(1 for c in channels if c == "sub_registrar")
    total = len(channels)
    if with_igrs == total:
        return (f"Yes — all {total} carry an IGRS Form 6 number: every one came "
                f"in as a Sub-Registrar referral, and the number names the "
                f"registered deed the mutation was raised from.")
    if with_igrs == 0:
        return (f"No — none of these {total} carries an IGRS Form 6 number. "
                f"Only a Sub-Registrar referral is given one.")
    return (f"{with_igrs} of these {total} carry an IGRS Form 6 number — the "
            f"Sub-Registrar referrals. The other {total - with_igrs} came in "
            f"over a counter, which is never given one.")
