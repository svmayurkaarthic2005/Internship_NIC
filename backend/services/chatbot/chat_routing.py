"""
The '_X_turn' dispatch family (recheck/register-gap/howto/stage-def/calc/stats/answer/old-codes/merge) plus _cm_strong, the core record-module claim gate.

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
from .chat_followups import _fragment_typo_fix
from .chat_service_codes import (
    _all_service_code_comparison_answer,
    _build_service_code_comparison_response,
    _service_code_lookup_answer,
)
from .chat_shared import _contentless_message
from .chat_smalltalk import (
    _is_merge_question,
    _is_mutation_request,
    _no_merge_type_answer,
    _sentiment_kind,
)
from .chat_untracked_fields import _untracked_geo_master_field_answer
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# A few callees still live in chatbot.py (the processing pipelines, save_chat_messages,
# and functions kept there so tests can patch their collaborators). chatbot.py imports
# this module, so they are reached through the module object, imported at call time --
# importing it at module load (by name or as a module) would be a circular import.
def _cb():
    from backend.services import chatbot
    return chatbot





_SAVE_AS = contextvars.ContextVar("save_as", default=None)


async def _recheck_turn(message, session_id, officer, db, chat_history):
    """Check earlier question if message is a pushback."""
    if not _recheck.is_pushback(message):
        return None
    # If the message is about uploaded files or an attachment comparison, let the attachment / negative-correction routing handle it!
    low = (message or "").lower()
    if (any(w in low for w in ("file", "files", "document", "documents", "csv", "attachment", "attachments", "upload", "uploaded", "கோப்பு", "ஆவணம்"))
            or re.search(r"\bfiles?\s+la\b", low)):
        return None
    hist = chat_history or []
    lang = detect_language(_fragment_typo_fix(message))
    ta = lang in ("ta", "tanglish")
    idx = None
    for i in range(len(hist) - 1, -1, -1):
        m = hist[i]
        c = (m.get("content") or "")
        if (m.get("role") == "user" and not _recheck.is_pushback(c) and not _contentless_message(c)
                and not _sentiment_kind(c) and parse_intent(c) != "greeting"):
            idx = i
            break
    if idx is None:
        if _sentiment_kind(message) and not hist:
            return None                      # a bare complaint keeps its own reply
        text = _recheck.nothing_to_check(ta)
        return {"response": text, "language": lang, "intent": "recheck", "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(), "context_used": False,
                "response_time_ms": 0, "table_data": None}
    prev_q = hist[idx].get("content") or ""
    old = hist[idx + 1].get("content") if idx + 1 < len(hist) and hist[idx + 1].get("role") == "assistant" else None
    _SAVE_AS.set(message)
    try:
        r = await _cb()._process_chat_impl(prev_q, session_id, officer, db, hist[:idx])
    finally:
        _SAVE_AS.set(None)
    same = _recheck.same_answer(old, r.get("response") or "") if old else None
    out = dict(r)
    out["response"] = _recheck.note(prev_q, same, ta, _recheck.asserted_figure(message)) + (r.get("response") or "")
    out["intent"] = "recheck"
    out["language"] = lang
    return out


_ALL_STATUSES_LIST = ["approved", "pending", "in_progress", "escalated", "rejected"]


_GAP_NOT_IN_RE = re.compile(
    r"\b(?:applications?|aplications?|files?)\b.{0,30}\b(?:not|missing|absent|unavailable)\b|\b(?:missing|absent|unavailable)\s+(?:applications?|files?)\b"
    r"|\bwh\w+\s+(?:applications?|files?)\s+(?:is|are)?\s*(?:missing|not\s+(?:present|available|found|there|recorded|registered|in))\b", re.IGNORECASE)
_GAP_PLACE_RE = re.compile(r"\b(?:in|on|from|present\s+in|available\s+in)\s+(?:the\s+|my\s+)?(?:sis|system|register|database|db|records?)\b|\b(?:present|available|found|there)\b|\bmissing\b", re.IGNORECASE)
_GAP_BARE_RE = re.compile(r"^\s*(?:what|which)\s+(?:is|are)\s+(?:not\s+(?:present|available|found|there)|missing)\s*[?.!]*$", re.IGNORECASE)
_GAP_OUTSIDE_RE = re.compile(r"\bnot\s+in\s+my\s+(?:jurisdiction|ward|block|area)\b|\bout\w*side\s+(?:of\s+)?(?:my\s+)?(?:ass?ig\w*\s+)?"
                             r"(?:jur\w*|wards?|blocks?|area)\b", re.IGNORECASE)
_GAP_STAGE_RE = re.compile(r"\b(?:not|no\s+longer|moved)\b.{0,14}\b(?:at|with|on|from|out\s+of)\s+(?:the\s+|my\s+)?(?:sis(?:\s+(?:stage|desk|level))?|my\s+desk|desk)\b|\bnot\s+at\s+sis\b", re.IGNORECASE)


async def _register_gap_turn(message, session_id, officer, db, chat_history):
    """"what application is not present in SIS": the register can only list what it holds. Applications no longer
    at the SIS desk are counted by stage. None for any other message."""
    text = (message or "").strip()
    if not text or len(text.split()) > 14 or re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", text):
        return None
    stage_q = bool(_GAP_STAGE_RE.search(text))
    outside_q = bool(_GAP_OUTSIDE_RE.search(text))
    gap_q = (bool(_GAP_BARE_RE.match(text)) or (bool(_GAP_NOT_IN_RE.search(text)) and bool(_GAP_PLACE_RE.search(text)))) and not outside_q
    if not (stage_q or gap_q or outside_q):
        return None
    lang = detect_language(_fragment_typo_fix(text) or text)
    ta = lang in ("ta", "tanglish")
    if outside_q:
        reply = ("Applications outside your jurisdiction are not visible to me -- I only read the wards and blocks you are posted to, "
                 "and a file outside them is refused, not listed. Ask \"show jurisdiction summary\" to see what you hold." if not ta else
                 "உங்கள் அதிகார வரம்புக்கு வெளியே உள்ள விண்ணப்பங்கள் எனக்குத் தெரியாது — நீங்கள் நியமிக்கப்பட்ட வார்டுகள் / தொகுதிகளை மட்டுமே படிப்பேன்; "
                 "வெளியே உள்ள கோப்பு மறுக்கப்படும், பட்டியலிடப்படாது. உங்கள் வரம்பைக் காண \"jurisdiction summary\" எனக் கேளுங்கள்.")
        await _cb().save_chat_messages(db=db, session_id=session_id, user_message=message, assistant_message=reply, language=lang,
                                 response_time_ms=0, officer_id=officer.officer_id if officer else None, structured_context=None)
        return {"response": reply, "language": lang, "intent": "register_gap", "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(), "context_used": False, "response_time_ms": 0, "table_data": None}
    rows = (await get_officer_applications(db, officer, status=_ALL_STATUSES_LIST)).get("applications") or []
    total = len(rows)
    if stage_q:
        left = [r for r in rows if str(r.get("stage") or "").upper() != "SIS"]
        from collections import Counter as _C
        by = _C(f"{str(r.get('stage') or '').upper()}" for r in left)
        parts = ", ".join(f"{n} at {k}" for k, n in by.most_common())
        reply = (f"{len(left)} of your {total} applications are no longer at the SIS desk — {parts}. "
                 "Ask \"show completed applications\" or \"show rejected applications\" to list them." if not ta else
                 f"உங்கள் {total} விண்ணப்பங்களில் {len(left)} இனி SIS மேசையில் இல்லை — {parts}. "
                 "\"முடிந்த விண்ணப்பங்கள்\" அல்லது \"நிராகரிக்கப்பட்ட விண்ணப்பங்கள்\" எனக் கேளுங்கள்.")
    else:
        reply = (f"I can only list applications that are recorded in your SIS register ({total} for your jurisdiction) — an application "
                 "that is not in it cannot be listed, and I will not guess one. To check a particular file, give its application number "
                 "and I will say whether it is in your jurisdiction. If you mean files that have already left your desk, ask "
                 "\"applications not at SIS stage\"." if not ta else
                 f"உங்கள் SIS பதிவேட்டில் உள்ள விண்ணப்பங்களை ({total}) மட்டுமே பட்டியலிட முடியும் — பதிவேட்டில் இல்லாதவற்றைப் பட்டியலிட முடியாது, "
                 "யூகிக்கவும் மாட்டேன். ஒரு குறிப்பிட்ட கோப்பைச் சரிபார்க்க அதன் விண்ணப்ப எண்ணைத் தரவும்; அது உங்கள் அதிகார வரம்பில் உள்ளதா என்று சொல்கிறேன். "
                 "மேசையை விட்டுச் சென்றவற்றைக் கேட்க \"SIS கட்டத்தில் இல்லாத விண்ணப்பங்கள்\" எனக் கேளுங்கள்.")
    await _cb().save_chat_messages(db=db, session_id=session_id, user_message=message, assistant_message=reply, language=lang,
                             response_time_ms=0, officer_id=officer.officer_id if officer else None,
                             structured_context=None)
    return {"response": reply, "language": lang, "intent": "register_gap", "sources": [],
            "timestamp": datetime.now(timezone.utc).isoformat(), "context_used": False,
            "response_time_ms": 0, "table_data": None}


_STAGE_DEF_RE = re.compile(
    r"\b(?:what|whats|what's|meaning|explain|define)\b.{0,25}\b(?:open|completed?)\b.{0,12}\bstage\b"
    r"|\bstage\b.{0,12}\b(?:open|completed?)\b.{0,20}\b(?:mean|means|meaning|what)\b"
    r"|\bwhat\s+does\s+(?:the\s+)?(?:stage\s+)?[\"']?(?:open|completed)[\"']?\s+(?:stage\s+)?mean"
    r"|(?:open|completed?)\s+stage\s+(?:என்றால்|endral|enna)|(?:ஓபன்|திறந்த)\s*நிலை", re.IGNORECASE)


async def _howto_turn(message, session_id, officer, db, chat_history):
    """Policy / how-to questions with no application in them: answered from the reference documents (howto_qa)."""
    text = (message or "").strip()
    _oid = getattr(officer, "officer_id", None) or getattr(officer, "id", None)
    _is_att = (await attachment_qa.attachment_topic_active(db, session_id)
               or await attachment_qa.any_active_documents(db, _oid, session_id))
    if _is_att:
        return None
    if (re.search(r"\b(?:first|second|third|fourth|fifth|last|top)\s+(?:one|application|file)\b|\b(?:1st|2nd|3rd|4th|5th)\b", text, re.IGNORECASE)
            and not extract_application_number(text) and len(text.split()) <= 8
            and not any(re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", str(m.get("content") if isinstance(m, dict) else m))
                        for m in (chat_history or [])[-8:])):
        # "show details of the first one" with nothing on screen: say so instead of "not sure what you mean"
        lang = detect_language(text)
        reply = ("இந்த உரையாடலில் இதுவரை எதுவும் காட்டப்படவில்லை; எந்த விண்ணப்பம் என்று தெரியவில்லை. விண்ணப்ப எண்ணைத் தரவும் அல்லது முதலில் பட்டியல் கேளுங்கள் (\"my pending applications\")."
                 if lang in ("ta", "tanglish") else
                 "Nothing has been shown in this conversation yet, so I do not know which one you mean. Give an application number "
                 "(for example 2026/0154/28/001197), or ask for a list first (\"my pending applications\").")
        await _cb().save_chat_messages(db=db, session_id=session_id, user_message=message, assistant_message=reply, language=lang,
                                 response_time_ms=0, officer_id=officer.officer_id if officer else None, structured_context=None)
        return {"response": reply, "language": lang, "intent": "followup_clarification", "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(), "context_used": False, "response_time_ms": 0, "table_data": None}
    _last = next((re.sub(r"<[^>]+>", " ", m.get("content") or "") for m in reversed(chat_history or [])
                  if isinstance(m, dict) and str(m.get("role", "")).lower() in ("assistant", "ai", "bot")), "")
    hit = _howto_qa.answer(text, False, _last) if text else None
    if not hit:
        return None
    topic = hit[0]
    lang = detect_language(text)
    ta = lang in ("ta", "tanglish")
    reply = _howto_qa.answer(text, ta, _last)[1]
    if topic == "fee_other":
        reply = await _howto_qa.fline_fee(db, ta)
    elif topic == "survey_multi":
        from backend.services.postgres import get_all_service_code_applications
        rows = ((await get_all_service_code_applications(db, officer, hide_rejected=False)) or {}).get("applications") or []
        reply = _howto_qa.survey_multi(rows, ta)
    elif topic == "flagged":
        from backend.services.postgres import get_flagged_surveys
        _kind = "litigation" if re.search(r"litigation|court|stay|வழக்கு", text, re.IGNORECASE) else "encroachment"
        reply = _howto_qa.flagged(await get_flagged_surveys(db, officer, _kind), _kind, ta)
    elif topic == "by_reason":
        from backend.services.postgres import get_applications_by_reason
        _rs = _howto_qa.reason_of(text)
        reply = _howto_qa.by_reason(await get_applications_by_reason(db, officer, _rs), (_rs or ["?"])[0], ta) if _rs else None
    elif topic == "repeat":
        from backend.services.postgres import get_repeat_applicants
        reply = _howto_qa.repeat_applicants(await get_repeat_applicants(db, officer), ta)
    elif topic == "dups":
        from backend.services.postgres import get_all_service_code_applications
        rows = ((await get_all_service_code_applications(db, officer, hide_rejected=False)) or {}).get("applications") or []
        reply = _howto_qa.duplicates(rows, ta)
    if not reply:
        return None
    await _cb().save_chat_messages(db=db, session_id=session_id, user_message=message, assistant_message=reply, language=lang,
                             response_time_ms=0, officer_id=officer.officer_id if officer else None, structured_context=None)
    return {"response": reply, "language": lang, "intent": "howto_answer", "sources": [],
            "timestamp": datetime.now(timezone.utc).isoformat(), "context_used": False, "response_time_ms": 0, "table_data": None}


async def _stage_def_turn(message, session_id, officer, db, chat_history):
    """"what is open stage": the register's stage column has two values, Open and Completed (not the ISD / NISD desks)."""
    text = (message or "").strip()
    if not text or len(text.split()) > 10 or extract_application_number(text) or not _STAGE_DEF_RE.search(text):
        return None
    lang = detect_language(text)
    ta = lang in ("ta", "tanglish")
    from backend.services.postgres import get_all_service_code_applications
    rows = ((await get_all_service_code_applications(db, officer, hide_rejected=False)) or {}).get("applications") or []
    op = sum(1 for x in rows if str(x.get("stage")) == "Open")
    done = sum(1 for x in rows if str(x.get("stage")) == "Completed")
    if ta:
        reply = (f"Stage என்பது பதிவேட்டில் இரண்டு மதிப்புகள் மட்டுமே: **Open** = இன்னும் செயலாக்கத்தில் உள்ள விண்ணப்பம் (நிலை: நிலுவை), "
                 f"**Completed** = முடிவு எடுக்கப்பட்டது (அங்கீகரிக்கப்பட்டது அல்லது நிராகரிக்கப்பட்டது). உங்கள் வரம்பில்: Open {op}, Completed {done}. "
                 "ISD / NISD கோப்புகளின் SIS, SD, DIS, Tahsildar மேசைகள் தனி விவரம் (அவை கோப்பு விவரத்தில் உள்ளன).")
    else:
        reply = (f"In the register a service-code application has just two stages: **Open** = still being processed (status pending) and "
                 f"**Completed** = decided (approved or rejected). In your jurisdiction: {op} Open, {done} Completed. "
                 "It is not a desk: the SIS / SD / DIS / Tahsildar stage of an ISD or NISD file is shown in that file's own details.")
    await _cb().save_chat_messages(db=db, session_id=session_id, user_message=message, assistant_message=reply, language=lang,
                             response_time_ms=0, officer_id=officer.officer_id if officer else None, structured_context=None)
    return {"response": reply, "language": lang, "intent": "stage_definition", "sources": [],
            "timestamp": datetime.now(timezone.utc).isoformat(), "context_used": False, "response_time_ms": 0, "table_data": None}


async def _calc_turn(message, session_id, officer, db, chat_history):
    """Calculation questions ("pending plus approved", "ratio of CSC to SRO", "which service code has the most",
    "47 plus 17") over the whole register -- every service code, not just ISD / NISD."""
    text = (message or "").strip()
    if not text or len(text.split()) > 30 or extract_application_number(text):
        return None
    text = _fragment_typo_fix(text) or text
    lang = detect_language(text)
    ta = lang in ("ta", "tanglish")
    from backend.utils.helpers import code_for_name
    reply = code_for_name(text, ta) or _calc_qa.arithmetic(text)
    if not reply and re.match(r"\s*what\s+(?:type\s+of\s+|kind\s+of\s+)?serv\w*\s+is\s+(?:code\s+)?0?1[5-9]\d\s*\??\s*$", text, re.IGNORECASE):
        # "what service is 0154?" -- "service" is also a field word, which sent it to a one-application lookup
        reply = _service_code_lookup_answer(text, ta, brief=True)
    if not reply:
        reply = _calc_qa.compare_codes_conceptual(text, ta)
    if not reply:
        from backend.services.rag import _is_all_service_code_comparison
        if _is_all_service_code_comparison(text):
            from backend.services.postgres import get_service_register_counts_by_code, get_all_service_fees
            _dynamic_counts = await get_service_register_counts_by_code(db) if db else None
            _fee_map = await get_all_service_fees(db) if db else None
            reply = _all_service_code_comparison_answer(text, ta, dynamic_counts=_dynamic_counts, fee_map=_fee_map)
    if not reply:
        from backend.utils.helpers import find_service_codes
        _explicit_codes = find_service_codes(text)
        _is_app_register_query = bool(re.search(
            r"\b(?:applications?|files?|records?|counts?|more\s+than|which\s+(?:one\s+)?has\s+more|விண்ணப்ப|கோப்புகள்)\b",
            text, re.IGNORECASE))
        if len(_explicit_codes) >= 2 and not _is_app_register_query:
            if re.search(r"\bdiffer\w*|\bcompa?r\w*|\bvs\.?\b|\bversus\b|\bsame\b|\bsimilar\w*|வேறுபா\w*|ஒப்பீ\w*|ஒற்றுமை\w*", text, re.IGNORECASE):
                from backend.services.postgres import get_all_service_fees
                _fee_map = await get_all_service_fees(db) if db else None
                reply = _build_service_code_comparison_response(_explicit_codes, text, ta, fee_overrides=_fee_map)
    if not reply and (_calc_qa._CC_COUNT.search(text) or _calc_qa._CMP_CODES.search(text)
                      or _calc_qa._CHANNEL_BREAKDOWN_RE.search(text) or _calc_qa._BLOCKS_IN_WARD_RE.search(text)
                      or _calc_qa._CODES_WITH_APPS_RE.search(text)):
        from backend.services.postgres import get_all_service_code_applications
        _ccrows = ((await get_all_service_code_applications(db, officer, hide_rejected=False)) or {}).get("applications") or [] if db else []
        from backend.utils.helpers import find_service_codes
        _explicit_codes = find_service_codes(text)
        _override_codes = None
        # Only fall back to chat_history if the current user text did NOT provide 2 codes
        if len(_explicit_codes) < 2 and chat_history:
            for _h in reversed(chat_history):
                _c_hist = find_service_codes(_h.get("content") or "")
                if len(_c_hist) >= 2:
                    _override_codes = _c_hist
                    break
        reply = (_calc_qa.compare_codes(text, _ccrows, ta, codes_override=_override_codes) or _calc_qa.channel_breakdown(text, _ccrows, ta)
                or _calc_qa.blocks_in_ward(text, _ccrows, ta) or _calc_qa.codes_with_applications(text, _ccrows, ta)
                or _calc_qa.code_counts(text, _ccrows, ta))
    if not reply:
        if not _calc_qa.looks_like_calc(text):
            return None
        from backend.services.postgres import get_all_service_code_applications
        rows = _ccrows if '_ccrows' in locals() and _ccrows is not None else (((await get_all_service_code_applications(db, officer, hide_rejected=False)) or {}).get("applications") or [] if db else [])
        reply = _calc_qa.date_extreme(text, rows, ta) or _calc_qa.answer(text, rows)
    if not reply:
        return None
    if db:
        await _cb().save_chat_messages(db=db, session_id=session_id, user_message=message, assistant_message=reply, language=lang,
                                 response_time_ms=0, officer_id=getattr(officer, "officer_id", None),
                                 structured_context=None)
    return {"response": reply, "language": lang, "intent": "calc_answer", "sources": [],
            "timestamp": datetime.now(timezone.utc).isoformat(), "context_used": False,
            "response_time_ms": 0, "table_data": None}


async def _stats_turn(message, session_id, officer, db, chat_history):
    """Per year / month / quarter breakdowns, averages, growth between years, percentages and rates, fee
    arithmetic and turnaround times, computed from the officer's rows. None for any other message."""
    text = (message or "").strip()
    if not text or len(text.split()) > 16 or not _stats_qa.looks_analytic(text):
        return None
    text = _fragment_typo_fix(text) or text
    lang = detect_language(text)
    ta = lang in ("ta", "tanglish")
    from backend.services.postgres import get_analytics_rows
    rows = await get_analytics_rows(db, officer)
    reply = _stats_qa.answer(text, rows, ta)
    if not reply:
        return None
    await _cb().save_chat_messages(db=db, session_id=session_id, user_message=message, assistant_message=reply, language=lang,
                             response_time_ms=0, officer_id=officer.officer_id if officer else None,
                             structured_context=None)
    return {"response": reply, "language": lang, "intent": "stats_answer", "sources": [],
            "timestamp": datetime.now(timezone.utc).isoformat(), "context_used": False,
            "response_time_ms": 0, "table_data": None}


_OLD_WORD_RE = re.compile(
    r"\b(?:old|olld|previous|prev|superseded|former|earlier|pazhaya|pazhaiya|pazhya|palaya|pazhay)\b"
    r"|old_|பழைய|பழய|முந்தைய|முன்னைய", re.IGNORECASE)
_OLD_LEVEL_RE = re.compile(r"taluk|talk|taluq|thaluk|town|twon|block|blok|blck|தாலுகா|நகர|பிளாக்|தொகுதி", re.IGNORECASE)
_OLD_CODE_RE = re.compile(r"code|kuriyeedu|kuriyedu|kuri|குறியீடு", re.IGNORECASE)
_OLD_COLUMN_RE = re.compile(r"old_(?:taluk|town|block)_code", re.IGNORECASE)

_TAMIL_CHARS_RE = re.compile(r"[஀-௿]")


def _is_tamil_turn(message: str, language: str) -> bool:
    return language in ("ta", "tanglish") or bool(_TAMIL_CHARS_RE.search(message or ""))


async def _answer_turn(message, reply, intent, session_id, officer, db, language,
                       out_ctx=None, context_used=False):
    """Save a fixed reply and return it in the shape every other turn uses."""
    await _cb().save_chat_messages(db=db, session_id=session_id, user_message=message,
                             assistant_message=reply, language=language, response_time_ms=0,
                             officer_id=officer.officer_id if officer else None,
                             structured_context=out_ctx.to_json() if out_ctx else None)
    return {"response": reply, "language": language, "intent": intent, "sources": [],
            "timestamp": datetime.now(timezone.utc).isoformat(), "context_used": context_used,
            "response_time_ms": 0, "table_data": None}


def _old_codes_question(message: str) -> bool:
    """Asks for the master's old taluk / town / block code, in any spelling or script."""
    low = (message or "").lower()
    if _is_mutation_request(low):
        return False
    if _OLD_COLUMN_RE.search(low):
        return True
    return bool(_OLD_WORD_RE.search(low) and _OLD_LEVEL_RE.search(low) and _OLD_CODE_RE.search(low))


async def _old_codes_turn(message, session_id, officer, db, chat_history):
    """Runs before the follow-up layers, which would read "old block code?" as the block column."""
    if not _old_codes_question(message):
        return None
    language = detect_language(message)
    reply = _untracked_geo_master_field_answer("old_codes", _is_tamil_turn(message, language))
    return await _answer_turn(message, reply, "untracked_field", session_id, officer, db, language)


async def _merge_turn(message, session_id, officer, db, chat_history):
    """Runs before every other guard turn and before intent routing on both chat
    paths. A "merge" mention must never reach `_number_turn` (which would count
    it as zero with no explanation), the qualifier guard (which would ask which
    real filter was meant), or general_query (which would hand the LLM the
    ISD/NISD context it *was* given and let it invent MERGE application
    numbers to answer with). See `_is_merge_question`."""
    if _newowner_qa.parse(message)["kind"] == "uds_def":
        language = detect_language(message)
        _utxt = _newowner_qa.UDS_TA if _is_tamil_turn(message, language) else _newowner_qa.UDS_EN
        return await _answer_turn(message, _utxt, "uds_definition", session_id, officer, db, language)
    if _merge_qa.parse(message):
        language = detect_language(message)
        _mres = await _merge_qa.answer(db, officer, message, _is_tamil_turn(message, language))
        if _mres:
            _mtxt, _mrows = _mres
            _mctx = fctx.build_context("followup_list", {"applications": _mrows}) if _mrows else None
            return await _answer_turn(message, _mtxt, "merge_links", session_id, officer, db, language, out_ctx=_mctx)
    if not _is_merge_question(message) or _appinfo_qa.claims(message):
        return None
    language = detect_language(message)
    reply = _no_merge_type_answer(_is_tamil_turn(message, language))
    return await _answer_turn(message, reply, "no_merge_type", session_id, officer, db, language)


def _close_match(tokens, target: str, min_len: int, cutoff: float) -> bool:
    """True if any token is a near spelling of `target`."""
    return any(len(t) >= min_len and difflib.SequenceMatcher(None, t, target).ratio() >= cutoff
               for t in tokens)


_OWNER_SHARE_FOLLOWUP_RE = re.compile(
    r"^(?:(?:and|what about|show|tell me|the)\s+)?(?:their|its|the|his|her|each)?\s*(?:owner'?s?\s+)?"
    r"(?:shares?|percentage|பங்கு\w*)\s*\??$", re.IGNORECASE)


def _asked_ownership_field(message: str, has_owner_context: bool) -> Optional[str]:
    """"share" or "type" when the question is about ownership (ownership_share, typos, Tamil...)."""
    low = (message or "").lower()
    if not low.strip() or _is_mutation_request(low):
        return None
    words = re.findall(r"[a-z]+", low)
    about_ownership = _close_match(words, "ownership", 6, 0.8) or "உரிமை" in low
    about_share = (_close_match(words, "share", 4, 0.75) or "percent" in low or "%" in low
                   or "பங்கு" in low or "pangu" in low)
    about_type = (_close_match(words, "type", 3, 0.75) or "sole" in words or "joint" in words
                  or "வகை" in low or "vagai" in low)
    if about_ownership and about_share:
        return "share"
    if about_ownership and about_type:
        return "type"
    if has_owner_context and _OWNER_SHARE_FOLLOWUP_RE.match(low.strip()):
        return "share"
    return None


def _asked_record_timestamp(message: str) -> Optional[str]:
    """"created", "updated", or "owner_ts" for created_at / updated_at style questions."""
    low = (message or "").lower()
    if _is_mutation_request(low):
        return None
    updated = re.search(r"updated_at|last\s+updat\w*|updat\w*\s+(?:date|on|time)|last\s+modif\w*"
                        r"|\b(?:when|wen|whn)\b.*\bupdat\w*|கடைசியாக.*புதுப்பி|புதுப்பிக்கப்பட்ட"
                        # "when was the last workflow action", "who acted ... and when",
                        # "when was it moved to the next stage" -- the same question as
                        # "when was it updated", just said in workflow terms. Without
                        # this it fell to the generic field map's bare "when" -> the
                        # SUBMISSION date, answering a workflow-action question with the
                        # filing date -- exactly the submission/action-date confusion
                        # this assistant must not make.
                        r"|\b(?:workflow|workflw|worflow|workflo|workfow)\s+act\w*|\bwho\s+act\w*"
                        r"|\bacted\s+on\b|\blast\s+act\w*|\baction\s+(?:date|taken|on)\b"
                        r"|\bmoved\s+to\s+(?:the\s+)?next\s+stage\b"
                        r"|\blatest\s+(?:workflow|workflw|worflow|workflo|workfow)\b", low)
    created = re.search(r"created_at|creat\w*\s+(?:date|on|time)|(?:date|time)\s+(?:of\s+)?creat\w*"
                        r"|\b(?:when|wen|whn)\b.*\bcreat\w*|எப்போது.*உருவாக்க|உருவாக்கப்பட்ட", low)
    if (not updated and _close_match(re.findall(r"[a-z]+", low), "updated", 6, 0.75)
            and re.search(r"\blast\b|\bwhen\b|\bdate\b|_at", low)):
        updated = True
    kind = "updated" if updated else ("created" if created else None)
    if kind and re.search(r"owner|ownership|உரிமை", low):
        return "owner_ts"
    return kind


def _ownership_field_answer(kind: str, survey_no, app_no, owners: list, ta: bool) -> str:
    count = len(owners)
    where = f"survey {survey_no}" + (f" (application {app_no})" if app_no else "")
    if kind == "type":
        sole = sum(1 for o in owners if str(o.get("ownership_type") or "").lower() == "sole")
        joint = sum(1 for o in owners if str(o.get("ownership_type") or "").lower() == "joint")
        other = f", {count - sole - joint} unspecified" if count - sole - joint else ""
        return (f"{where}: {count} உரிமையாளர் பதிவுகள் — {sole} தனி, {joint} கூட்டு." if ta else
                f"Ownership type for {where}: {count} owner record(s) — {sole} sole, {joint} joint{other}.")

    shares = [round(float(o["ownership_share"]), 2) for o in owners
              if o.get("ownership_share") not in (None, "", "N/A")]
    if not shares:
        return (f"{survey_no} க்கான உரிமைப் பங்கு பதிவேட்டில் இல்லை." if ta else
                f"No ownership share is recorded for {where}.")
    distinct = sorted(set(shares))
    if len(distinct) > 1:
        parts = ", ".join(f"{shares.count(s)} at {s:g}%" for s in distinct)
        return f"{where}: பங்குகள் — {parts}." if ta else f"Ownership share for {where}: {parts}."
    share = distinct[0]
    if count == 1:
        return (f"{where}: ஒரே உரிமையாளர் பதிவு; பங்கு {share:g}%." if ta else
                f"Ownership share for {where}: the one owner record carries a share of {share:g}%.")
    return (f"{where}: {count} உரிமையாளர் பதிவுகள்; ஒவ்வொன்றிலும் பங்கு {share:g}%. கூட்டு உரிமையாளர்களுக்கும் "
            f"பதிவேடு அதே எண்ணையே வைத்திருப்பதால், நிலம் எவ்வாறு பங்கிடப்படுகிறது என்பதை இது காட்டாது." if ta else
            f"Ownership share for {where}: the register holds {count} owner record(s), each with a share of "
            f"{share:g}%. The register stores the same figure against every owner, joint owners "
            f"included, so it does not show how the parcel is divided between them.")


def _timestamp_reply(stamp: str, app_no, detail: dict, ta: bool) -> str:
    if stamp == "created":
        filed = detail.get("submission_date")
        if not filed:
            return "சமர்ப்பித்த தேதி பதிவில் இல்லை." if ta else "Its filing date is not on record."
        return (f"{app_no} சமர்ப்பிக்கப்பட்ட (உருவாக்கப்பட்ட) தேதி: {filed}. பதிவேட்டில் தனி உருவாக்க நேரம் இல்லை." if ta else
                f"{app_no} was filed (created) on {filed}. The register keeps no separate creation "
                f"timestamp; the filing date is when the application was created.")
    updated = detail.get("last_updated_datetime")
    if not updated:
        return "கடைசி புதுப்பிப்பு பதிவில் இல்லை." if ta else "Its last update is not on record."
    return (f"{app_no} கடைசியாகப் புதுப்பிக்கப்பட்டது: {updated} (கடைசி பணிப்பாய்வு நடவடிக்கையின் நேரம்)." if ta else
            f"{app_no} was last updated on {updated} — the time of its latest workflow action.")


async def _answer_multi(plan, message, session_id, officer, db, chat_history):
    kind, what = plan
    if kind == "focus":
        if not what:
            lang = detect_language(message)
            text = ("நீங்கள் என்ன தெரிந்து கொள்ள விரும்புகிறீர்கள் என்று எனக்குப் புரியவில்லை — கேள்வியை ஒரு வரியில் சொல்லுங்கள் (எ.கா. \"எனது நிலுவை விண்ணப்பங்கள்\")."
                    if lang in ("ta", "tanglish") else
                    "I could not find a question in that message. Say what you want checked in one line — for example \"my pending applications\" or \"status of 2026/0154/28/001197\".")
            return {"response": text, "language": lang, "intent": "no_request_found", "sources": [],
                    "timestamp": datetime.now(timezone.utc).isoformat(), "context_used": False,
                    "response_time_ms": 0, "table_data": None}
        return await _cb()._process_chat_impl(what, session_id, officer, db, chat_history)
    results = []
    for clause in what:
        results.append((clause, await _cb()._process_chat_impl(clause, session_id, officer, db, chat_history)))
    body = []
    for n, (clause, r) in enumerate(results, 1):
        body.append(f"<div><strong>{n}. {__import__('html').escape(clause)}</strong></div>\n{r.get('response') or ''}")
    first = results[0][1]
    tables = [r.get("table_data") for _, r in results if r.get("table_data")]
    out = dict(first)
    out.update({"response": "\n\n".join(body), "intent": "multi_request",
                "table_data": tables[0] if len(tables) == 1 else None})
    return out


def _is_incomplete_answer(last_assistant_msg: str, user_prompt: str) -> bool:
    if not last_assistant_msg:
        return False
    if re.search(r"where\s+you\s+stopped|remaining\s+part|remaining\s+steps|resume|finish\s+the|நிறுத்திய\s*இடத்திலிருந்து|மீதி", user_prompt, re.I):
        return True
    m = last_assistant_msg.strip()
    if m.endswith("...") or m.endswith("…") or m.endswith(":") or m.endswith("-"):
        return True
    if re.search(r"\n\s*(?:[-*]|\d+\.)\s*$", m):
        return True
    if m.count("```") % 2 != 0:
        return True
    if len(m) > 100 and m[-1] not in (".", "!", "?", ":", ")", '"', "'", "*", ">", "”", "’"):
        return True
    return False
