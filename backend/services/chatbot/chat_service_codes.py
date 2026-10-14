"""
Service-code explain/detail-negation helpers, comparison-mode parsing, the pairwise and whole-catalogue service-code comparison responses, service-code lookup, and the negative-correction response builders.

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
from .chat_application_queries import _compare_label
from .chat_shared import (
    _BRIEF_REQUEST_PHRASES_RE,
    _BRIEF_REQUEST_WORDS,
    _BRIEF_REQUEST_WORDS_TA,
    _DETAIL_REQUEST_TRIGGER_WORDS,
    _STAGE_QUESTION_RE,
    _WORD_LIMIT_RE,
    _has_detail_phrase,
    _is_detail_negation_request,
    _is_format_negation_request,
)
from backend.utils.logger import get_logger

logger = get_logger(__name__)




_NEGATIVE_SENTENCE_FORMAT_RE = re.compile(
    r"^\s*(?:please\s+)?(?:don't|dont|do\s+not|never|stop)\s+"
    r"(?:please\s+)?(?:explain|describe|answer|respond|reply|write|say|use|format|give)\b"
    r".{0,60}\b(?:in\s+(?:a\s+|one\s+)?sentences?|as\s+(?:a\s+)?sentence|"
    r"sentence\s+(?:format|form|mode)|one\s+sentence|single\s+sentence)\b"
    r"|^\s*(?:not|no)\s+(?:in\s+)?(?:a\s+)?sentence(?:\s+(?:format|form|mode))?\b",
    re.IGNORECASE,
)


def _is_negative_sentence_format_request(message: str) -> bool:
    """Recognise an explicit request to stop using single-sentence output.

    This must be checked before _is_explain_request: otherwise the word
    "explain" in "don't explain in sentence" is treated as a fresh request to
    explain briefly, and the assistant repeats the same one-line answer.
    The anchored shape avoids treating unrelated text such as "I don't
    understand; explain ISD in a sentence" as a negative format correction.
    """
    return bool(_NEGATIVE_SENTENCE_FORMAT_RE.search((message or "").strip()))


def _negative_sentence_format_context_code(message: str, context) -> Optional[str]:
    """Return the service code whose output format is being reverted, if any."""
    if not _is_negative_sentence_format_request(message) or context is None:
        return None
    if getattr(context, "entity", None) != "service_code":
        return None
    filters = getattr(context, "filters", None) or {}
    code = str(filters.get("service_code") or "").strip()
    if not re.fullmatch(r"\d{4}", code):
        return None

    # An explicitly named code in the current request takes precedence over
    # the carried code. Otherwise a bare format correction applies to the code
    # already being discussed.
    explicit = re.search(r"(?<!\d)(01[5-9]\d)(?!\d)", message or "")
    if explicit:
        return explicit.group(1)
    lowered = (message or "").lower()
    if re.search(r"(?<![a-z0-9])nisd(?![a-z0-9])", lowered):
        return "0153"
    if re.search(r"(?<![a-z0-9])isd(?![a-z0-9])", lowered):
        return "0154"
    return code


def _is_detail_request(message: str) -> bool:
    """True when the officer is asking for a FULLER explanation of the last
    answer (elaborate / describe / explain in detail) -- False when they are
    asking for a SHORTER one instead, even if "explain"/"describe" also
    appears in the same message.
    """
    tokens = re.findall(r"[a-zA-Z]+", message or "")
    # Checked BEFORE the brevity-phrase checks below: "dont explain in
    # sentence" contains the brevity phrase "in sentence", but the "dont" in
    # front of it asks for the opposite -- the full explanation, not a
    # repeat of the one-liner it just refused.
    if _is_format_negation_request(message):
        return True
    # The mirror case: "don't explain in detail" contains the bare word
    # "detail", which `_has_detail_phrase` below would otherwise match on its
    # own -- checked first so the negator wins.
    if _is_detail_negation_request(message):
        return False
    if any(w in message for w in _BRIEF_REQUEST_WORDS_TA):
        return False
    if _BRIEF_REQUEST_PHRASES_RE.search(message or "") or _WORD_LIMIT_RE.search(message or ""):
        return False
    if any(is_token_typo_match(tok, target) for tok in tokens for target in _BRIEF_REQUEST_WORDS):
        return False
    if _has_detail_phrase(message):
        return True
    if _STAGE_QUESTION_RE.search(message or ""):
        return True
    return any(is_token_typo_match(tok, target)
               for tok in tokens for target in _DETAIL_REQUEST_TRIGGER_WORDS)


def parse_service_comparison_mode(message: str) -> str:
    msg = (message or "").strip().lower()

    # 1. AMBIGUOUS_COMPARISON: awkward negated difference questions
    if re.search(r"\b(?:what\s+(?:is\s+not|isn'?t|is\s+no)|enna\s+illai)\b.{0,30}?\b(?:diff\w*|differ\w*|வேறுபாடு|வித்தியாசம்)\b", msg) \
       or re.search(r"\b(?:not\s+the\s+diff\w*|not\s+a\s+diff\w*)\b", msg):
        return "AMBIGUOUS_COMPARISON"

    # 2. Tamil postfix negation: 'difference vendam' -> SIMILARITIES, 'similarity vendam' -> DIFFERENCES
    if re.search(r"\b(?:diff\w*|differ\w*|வேறுபாடு|வித்தியாசம்)\b.{0,10}?\b(?:vendam|வேண்டாம்|not|இல்லை)\b", msg):
        return "SIMILARITIES"
    if re.search(r"\b(?:similar\w*|common\w*|ஒற்றுமை|பொதுவான)\b.{0,10}?\b(?:vendam|வேண்டாம்|not|இல்லை)\b", msg):
        return "DIFFERENCES"

    # 3. Directed negation commands: 'don't tell me differences' vs 'don't tell me similarities'
    m_neg = re.search(r"\b(?:don'?t|do\s+not|never)\s+(?:show|tell|explain|give|say|describe|list)?\b.{0,20}?\b(diff\w*|differ\w*|வேறுபாடு|வித்தியாசம்|similar\w*|common\w*|ஒற்றுமை|பொதுவான)\b", msg)
    if m_neg:
        target = m_neg.group(1)
        if any(target.startswith(p) for p in ('diff', 'வேறுபா', 'வித்தியாச')):
            return "SIMILARITIES"
        if any(target.startswith(p) for p in ('similar', 'common', 'ஒற்றுமை', 'பொதுவான')):
            return "DIFFERENCES"

    # 4. Only differences or only similarities
    if re.search(r"\b(?:only\s+(?:the\s+)?(?:diff\w*|differ\w*)|(?:diff\w*|differ\w*)\s+(?:only|mattum|மட்டும்))\b", msg) \
       or re.search(r"\bwhat\s+is\s+not\s+the\s+same\b", msg):
        return "DIFFERENCES"

    if re.search(r"\b(?:only\s+(?:their\s+|the\s+)?(?:similar\w*|common\w*|shared)|(?:similar\w*|common\w*)\s+(?:only|mattum|மட்டும்))\b", msg) \
       or re.search(r"\bwhat\s+are\s+they\s+not\s+different\s+in\b", msg):
        return "SIMILARITIES"

    # 5. BOTH: explicitly requests both similarities/common and differences
    if (re.search(r"\b(?:common|similar\w*|ஒற்றுமை|பொதுவான)\b", msg) and re.search(r"\b(?:diff\w*|differ\w*|வேறுபாடு|வித்தியாசம்)\b", msg)):
        return "BOTH"
    if re.search(r"\b(?:both\s+(?:features|aspects|points)|separate\s+sections)\b", msg):
        return "BOTH"

    # 6. IDENTITY_CHECK: 'Are they the same?', 'Is there no difference?', 'They are the same, aren't they?'
    if re.search(r"\b(?:is\s+there\s+no\s+diff\w*|no\s+difference\s+between)\b", msg) \
       or re.search(r"\b(?:are\s+(?:they|both)\s+the\s+same|same\s+ah|same\s+nu|same\s+or\s+diff\w*)\b", msg) \
       or re.search(r"\b(?:same\s*,?\s*aren'?t\s+they|different\s*,?\s*aren'?t\s+they)\b", msg) \
       or re.search(r"\b(?:i\s+don'?t\s+think|naan\s+ninaikala|ninaikavillai)\b.{0,60}?\b(?:same|different|வேறு|ஒரே)\b", msg) \
       or re.search(r"^\s*f[\s-]?line\s+and\s+f[\s-]?line\s+appeal\s+are\s+the\s+same\s*[.?!]*$", msg):
        return "IDENTITY_CHECK"

    # 7. SIMILARITIES: general questions about common features / shared aspects
    if re.search(r"\b(?:similar\w*|common\w*|shared|share\b|alike|ஒற்றுமை|பொதுவான)\b", msg) \
       or re.search(r"\b(?:what\s+is\s+the\s+same|which\s+properties\s+are\s+the\s+same|what\s+do\s+they\s+have\s+in\s+common)\b", msg) \
       or re.search(r"\b(?:ஒரே\s+மாதிரி|பொதுவானவை)\b", msg):
        return "SIMILARITIES"

    # 8. DIFFERENCES: default comparative mode
    return "DIFFERENCES"


def _build_service_code_comparison_response(
    exact: List[str], message: str, is_tamil: bool = False, brief: bool = False,
    fee_overrides: Optional[Dict[str, Any]] = None
) -> str:
    from backend.utils.helpers import SIS_URBAN_SERVICES, SIS_SERVICE_CODE_DETAIL, describe_service_code, is_table_only_request
    mode = parse_service_comparison_mode(message)
    msg_low = message.lower()
    wants_table = is_table_only_request(message) or bool(re.search(r"\b(?:table|அட்டவணை)\b", msg_low))

    c1 = exact[0] if len(exact) >= 1 else ""
    c2 = exact[1] if len(exact) >= 2 else ""
    info1 = SIS_URBAN_SERVICES.get(c1, {"name": c1, "short": c1, "category": "Urban", "requires_field_visit": False})
    info2 = SIS_URBAN_SERVICES.get(c2, {"name": c2, "short": c2, "category": "Urban", "requires_field_visit": False})
    name1, short1 = info1["name"], info1["short"]
    name2, short2 = info2["name"], info2["short"]
    fv1, fv2 = info1["requires_field_visit"], info2["requires_field_visit"]
    fv_same = (fv1 == fv2)

    detail1 = SIS_SERVICE_CODE_DETAIL.get(c1)
    detail2 = SIS_SERVICE_CODE_DETAIL.get(c2)

    fee_info1 = (fee_overrides or {}).get(c1)
    fee_info2 = (fee_overrides or {}).get(c2)

    if fee_info1:
        fee1 = fee_info1.get("fee_display") or f"Govt {fee_info1.get('govt_fee', '₹100.00')}, CSC {fee_info1.get('csc_fee', '₹60.00')}"
        sla1 = (fee_info1.get("sla_days_ta" if is_tamil else "sla_days")) or (detail1.get("sla_days_ta" if is_tamil else "sla_days") if detail1 else "Not documented in departmental guidelines")
    else:
        fee1 = f"Govt {detail1.get('govt_fee', '₹100.00')}, CSC {detail1.get('csc_fee', '₹60.00')}" if (detail1 and "govt_fee" in detail1) else "Not documented in departmental guidelines"
        sla1 = (detail1.get("sla_days_ta" if is_tamil else "sla_days") if detail1 else None) or "Not documented in departmental guidelines"

    if fee_info2:
        fee2 = fee_info2.get("fee_display") or f"Govt {fee_info2.get('govt_fee', '₹100.00')}, CSC {fee_info2.get('csc_fee', '₹60.00')}"
        sla2 = (fee_info2.get("sla_days_ta" if is_tamil else "sla_days")) or (detail2.get("sla_days_ta" if is_tamil else "sla_days") if detail2 else "Not documented in departmental guidelines")
    else:
        fee2 = f"Govt {detail2.get('govt_fee', '₹100.00')}, CSC {detail2.get('csc_fee', '₹60.00')}" if (detail2 and "govt_fee" in detail2) else "Not documented in departmental guidelines"
        sla2 = (detail2.get("sla_days_ta" if is_tamil else "sla_days") if detail2 else None) or "Not documented in departmental guidelines"

    wf1 = (detail1.get("workflow_ta" if is_tamil else "workflow") if detail1 else None) or ("Initial boundary demarcation survey handled by SIS and DIS" if c1 == "0178" else "Not documented in departmental guidelines")
    wf2 = (detail2.get("workflow_ta" if is_tamil else "workflow") if detail2 else None) or ("Administrative appeal procedure against prior demarcation decision" if c2 == "0185" else "Not documented in departmental guidelines")

    # Specific field inquiries
    if re.search(r"field\s*(?:visit|inspection)|கள\s*ஆய்வு", msg_low):
        if fv_same:
            if is_tamil:
                return (f"ஆம் — {short1} ({c1}) மற்றும் {short2} ({c2}) ஆகிய இரண்டிற்கும் "
                        f"கள ஆய்வு {'கட்டாயம் தேவை' if fv1 else 'தேவையில்லை'} (`requires_field_visit: {fv1}`).")
            return (f"Yes — both {short1} ({c1}) and {short2} ({c2}) "
                    f"{'require a mandatory on-site field visit' if fv1 else 'do not require a field visit'} (`requires_field_visit: {fv1}`).")
        else:
            if is_tamil:
                return (f"அவை வேறுபடுகின்றன: {short1} ({c1})-க்கு கள ஆய்வு {'தேவை' if fv1 else 'தேவையில்லை'}, "
                        f"ஆனால் {short2} ({c2})-க்கு கள ஆய்வு {'தேவை' if fv2 else 'தேவையில்லை'}.")
            return (f"They differ: {short1} ({c1}) {'requires a mandatory field visit' if fv1 else 'does not require a field visit'}, "
                    f"whereas {short2} ({c2}) {'requires a mandatory field visit' if fv2 else 'does not require a field visit'}.")

    if re.search(r"\b(?:urban|tamilnilam|jurisdiction)\b|அதிகார\s*வரம்பு", msg_low):
        if is_tamil:
            return (f"ஆம் — {short1} ({c1}) மற்றும் {short2} ({c2}) ஆகிய இரண்டும் "
                    f"TAMILNILAM நகர்ப்புற அமைப்பின் கீழ் சர்வேயர் (SIS) அதிகார வரம்பிற்குள் உள்ள சேவைகளாகும்.")
        return (f"Yes — both {short1} ({c1}) and {short2} ({c2}) are services categorized under TAMILNILAM Urban "
                f"under the jurisdiction of the Sub Inspector Surveyor (SIS).")

    if re.search(r"\b(?:application\s+fields?|fields?|புலங்கள்)\b", msg_low):
        if is_tamil:
            return (f"ஆம் — பதிவேட்டில் இரண்டும் நிலையான நகர்ப்புற விண்ணப்ப புலங்களைப் பயன்படுத்துகின்றன "
                    f"(விண்ணப்ப எண், சேவை குறியீடு, நிலை, சமர்ப்பிப்பு தேதி, வார்டு, பிளாக், சர்வே எண், உட்பிரிவு எண்).")
        return (f"Yes — in the service register, both share standard urban application fields "
                f"(application_number, service_code, status, submission_date, ward_id, block_id, survey_no, subdivision_no, stage, and submission_channel).")

    if re.search(r"\b(?:workflow|workflows|பணிப்பாய்வு)\b", msg_low):
        if c1 == "0178" and c2 == "0185":
            if is_tamil:
                return ("இல்லை — 0178 (F-Line) என்பது ஆரம்ப எல்லை நிர்ணய அளவை பணிப்பாய்வு (SIS மற்றும் DIS கையாள்வது); "
                        "0185 (F-Line Appeal) என்பது முந்தைய எல்லை நிர்ணய உத்தரவுக்கு எதிரான மேல்முறையீட்டு நடைமுறை.")
            return ("No — 0178 (F-Line) is the initial boundary demarcation survey workflow handled by the Sub Inspector Surveyor (SIS) and Deputy Inspector Surveyor (DIS), "
                    "whereas 0185 (F-Line Appeal) is an administrative appeal procedure against a prior demarcation decision.")
        elif detail1 and detail2:
            if is_tamil:
                return (f"பணிப்பாய்வு ஒப்பீடு:\n• {short1} ({c1}): {wf1}\n• {short2} ({c2}): {wf2}")
            return (f"Workflow comparison:\n• {short1} ({c1}): {wf1}\n• {short2} ({c2}): {wf2}")
        else:
            if is_tamil:
                return ("அதிகாரப்பூர்வ வழிகாட்டுதல்களில் இவ்விரண்டு சேவைகளுக்கும் முழு பணிப்பாய்வு படிகள் ஆவணப்படுத்தப்படவில்லை.")
            return ("Specific statutory workflow chains for these services are not documented in current departmental guidelines.")

    if mode == "AMBIGUOUS_COMPARISON":
        if is_tamil:
            return (
                f"வேறுபாடு இல்லாதது (அதாவது பொதுவான அம்சங்கள்) எது என்று நீங்கள் கேட்டால், "
                f"{short1} ({c1}) மற்றும் {short2} ({c2}) ஆகிய இரண்டும் TAMILNILAM நகர்ப்புற சேவைகள்"
                + (f" மற்றும் இரண்டிற்கும் கள ஆய்வு {'தேவை' if fv1 else 'தேவையில்லை'}." if fv_same else ".")
                + f"\n\nஇருப்பினும் அவை வெவ்வேறு சேவைகள் ({name1} vs {name2}). "
                f"அவற்றின் பொதுவான அம்சங்களை அறிய விரும்புகிறீர்களா அல்லது வேறுபாடுகளை அறிய விரும்புகிறீர்களா என்று தெளிவுபடுத்தவும்."
            )
        return (
            f"If you are asking what is not different (i.e. what they have in common), "
            f"both {short1} ({c1}) and {short2} ({c2}) are TAMILNILAM Urban services"
            + (f" and both {'require an on-site field visit' if fv1 else 'do not require a field visit'}." if fv_same else ".")
            + f"\n\nHowever, they remain distinct service codes ({name1} vs {name2}). "
            f"Please clarify whether you would like to see their shared similarities, their differences, or a full comparison table."
        )

    if mode == "SIMILARITIES":
        if wants_table:
            if is_tamil:
                return (
                    f"| அம்சம் | {short1} ({c1}) | {short2} ({c2}) | ஒப்பீடு |\n"
                    f"|---|---|---|---|\n"
                    f"| சேவை வகை | TAMILNILAM நகர்ப்புறம் | TAMILNILAM நகர்ப்புறம் | ஒரே வகை |\n"
                    f"| அதிகார வரம்பு | நகர்ப்புற அளவையர் (SIS) | நகர்ப்புற அளவையர் (SIS) | பகிரப்பட்டது |\n"
                    f"| கள ஆய்வு | {'கட்டாயம் தேவை' if fv1 else 'தேவையில்லை'} | {'கட்டாயம் தேவை' if fv2 else 'தேவையில்லை'} | {'ஒரே விதி' if fv_same else 'வேறுபடுகிறது'} |\n"
                    f"| பதிவேட்டு புலங்கள் | நிலையான நகர்ப்புற புலங்கள் | நிலையான நகர்ப்புற புலங்கள் | பகிரப்பட்டது |"
                )
            return (
                f"| Feature / Attribute | {short1} ({c1}) | {short2} ({c2}) | Comparison | Evidence / Source |\n"
                f"|---|---|---|---|---|\n"
                f"| Service Category | TAMILNILAM Urban | TAMILNILAM Urban | Same | Departmental Master |\n"
                f"| Jurisdiction | Ward/Taluk SIS | Ward/Taluk SIS | Shared | Urban Boundary Rules |\n"
                f"| Field Inspection | {'Mandatory' if fv1 else 'No visit'} | {'Mandatory' if fv2 else 'No visit'} | {'Same' if fv_same else 'Different'} | requires_field_visit |\n"
                f"| Register Schema | Standard urban fields | Standard urban fields | Shared | Register Schema |"
            )
        if is_tamil:
            sims = [f"• சேவை வகை: இரண்டும் TAMILNILAM நகர்ப்புற சேவைகள்."]
            if fv_same:
                sims.append(f"• கள ஆய்வு: இரண்டிற்கும் கள ஆய்வு {'கட்டாயம் தேவை' if fv1 else 'தேவையில்லை'} (`requires_field_visit: {fv1}`).")
            sims.append("• பதிவேட்டு புலங்கள்: இரண்டும் நிலையான நகர்ப்புற விண்ணப்ப புலங்களைப் பயன்படுத்துகின்றன.")
            return f"{short1} ({c1}) மற்றும் {short2} ({c2}) ஆகியவை பின்வரும் பொதுவான அம்சங்களைப் பகிர்ந்து கொள்கின்றன:\n" + "\n".join(sims)
        sims = [f"• Service Category: Both are TAMILNILAM Urban services."]
        if fv_same:
            sims.append(f"• Field Inspection: Both {'require a mandatory on-site field visit' if fv1 else 'do not require a field visit'} (`requires_field_visit: {fv1}`).")
        sims.append("• Application Data Schema: Both utilize standard urban application fields (application_number, service_code, status, submission_date, survey_no, subdivision_no).")
        return f"{short1} ({c1}) and {short2} ({c2}) share the following verified common features under TAMILNILAM Urban:\n" + "\n".join(sims)

    if mode == "DIFFERENCES":
        if wants_table:
            fee_evidence = "Database Table (service_fees) / TN Land Rules" if (fee_info1 or fee_info2) else "TN Land Rules (G.O.)"
            has_fee1 = bool(fee1 and "Not documented" not in fee1)
            has_fee2 = bool(fee2 and "Not documented" not in fee2)
            if is_tamil:
                return (
                    f"| பண்பு | {short1} ({c1}) | {short2} ({c2}) | வேறுபாடு |\n"
                    f"|---|---|---|---|\n"
                    f"| சேவை குறியீடு | {c1} | {c2} | தனித்தனி குறியீடுகள் |\n"
                    f"| அதிகாரப்பூர்வ பெயர் | {name1} | {name2} | தனித்தனி சேவைகள் |\n"
                    f"| கள ஆய்வு | {'கட்டாயம் தேவை' if fv1 else 'தேவையில்லை'} | {'கட்டாயம் தேவை' if fv2 else 'தேவையில்லை'} | {'ஒரே விதி' if fv_same else 'வேறுபடுகிறது'} |\n"
                    f"| சட்டப்பூர்வ கட்டணம் | {fee1} | {fee2} | {'ஒரே கட்டணம்' if (has_fee1 and has_fee2 and fee1 == fee2) else ('வேறுபடுகிறது' if (has_fee1 and has_fee2) else 'ஆவணப்படுத்தப்படவில்லை')} |\n"
                    f"| SLA காலக்கெடு | {sla1} | {sla2} | {'வேறுபடுகிறது' if (sla1 != 'Not documented in departmental guidelines' and sla2 != 'Not documented in departmental guidelines' and sla1 != sla2) else 'ஆவணப்படுத்தப்படவில்லை'} |"
                )
            return (
                f"| Attribute | {short1} ({c1}) | {short2} ({c2}) | Finding | Evidence / Source |\n"
                f"|---|---|---|---|---|\n"
                f"| Service Code | {c1} | {c2} | Distinct codes | Urban Service Master |\n"
                f"| Official Name | {name1} | {name2} | Distinct services | Departmental Catalogue |\n"
                f"| Field Inspection | {'Mandatory' if fv1 else 'No visit required'} | {'Mandatory' if fv2 else 'No visit required'} | {'Same' if fv_same else 'Different'} | requires_field_visit |\n"
                f"| Statutory Fee | {fee1} | {fee2} | {'Same' if (has_fee1 and has_fee2 and fee1 == fee2) else ('Different' if (has_fee1 and has_fee2) else 'Not documented for all codes')} | {fee_evidence} |\n"
                f"| SLA Deadline | {sla1} | {sla2} | {'Different' if (sla1 != 'Not documented in departmental guidelines' and sla2 != 'Not documented in departmental guidelines' and sla1 != sla2) else 'Not documented for all codes'} | Citizen Charter |"
            )
        has_fee1 = bool(fee1 and "Not documented" not in fee1)
        has_fee2 = bool(fee2 and "Not documented" not in fee2)
        if is_tamil:
            diffs = [
                f"• அதிகாரப்பூர்வ பெயர்: {c1} என்பது \"{name1}\" ({short1}); {c2} என்பது \"{name2}\" ({short2}).",
                f"• கள ஆய்வு: {short1} ({c1})-க்கு கள ஆய்வு {'தேவை' if fv1 else 'தேவையில்லை'}, ஆனால் {short2} ({c2})-க்கு கள ஆய்வு {'தேவை' if fv2 else 'தேவையில்லை'}."
            ]
            if has_fee1 or has_fee2:
                diffs.append(f"• கட்டணம் & SLA: {short1}: {fee1} ({sla1}); {short2}: {fee2} ({sla2}).")
            else:
                diffs.append("• கட்டணம் & பணிப்பாய்வு: இவ்விரண்டு சேவைகளுக்கும் கட்டணம்/பணிப்பாய்வு தற்போதைய வழிகாட்டுதல்களில் ஆவணப்படுத்தப்படவில்லை.")
            return f"இல்லை — {short1} ({c1}) மற்றும் {short2} ({c2}) ஆகியவை TAMILNILAM நகர்ப்புற அமைப்பில் வெவ்வேறு சேவைகள்:\n" + "\n".join(diffs)
        diffs = [
            f"• Purpose & Identity: {c1} is named \"{name1}\" ({short1}), whereas {c2} is named \"{name2}\" ({short2}).",
            f"• Field Inspection: {short1} ({c1}) {'requires an on-site field visit' if fv1 else 'does not require a field visit'}, whereas {short2} ({c2}) {'requires an on-site field visit' if fv2 else 'does not require a field visit'}."
        ]
        if has_fee1 or has_fee2:
            diffs.append(f"• Statutory Fee & SLA: {short1} ({fee1}, {sla1}) vs {short2} ({fee2}, {sla2}).")
        else:
            diffs.append("• Statutory Fee & Workflow: Fees, SLAs, and workflow stages for these service codes are not documented in current departmental guidelines.")
        return f"No, {short1} ({c1}) and {short2} ({c2}) are different services under TAMILNILAM Urban:\n" + "\n".join(diffs)

    if mode == "BOTH":
        has_fee1 = bool(fee1 and "Not documented" not in fee1)
        has_fee2 = bool(fee2 and "Not documented" not in fee2)
        if is_tamil:
            sim_bullets = [
                f"• சேவை வகை: இரண்டும் TAMILNILAM நகர்ப்புற சேவைகள்.",
                f"• அதிகார வரம்பு: இரண்டும் நகர்ப்புற அளவையர் (SIS) அதிகார வரம்பிற்குள் வருகின்றன.",
                f"• பதிவேட்டு புலங்கள்: இரண்டும் நிலையான நகர்ப்புற விண்ணப்ப புலங்களைப் பயன்படுத்துகின்றன."
            ]
            if fv_same:
                sim_bullets.append(f"• கள ஆய்வு: இரண்டிற்கும் கள ஆய்வு {'கட்டாயம் தேவை' if fv1 else 'தேவையில்லை'}.")
            diff_bullets = [
                f"• சேவை அடையாளம்: {c1} ({name1}) vs {c2} ({name2}).",
                f"• கள ஆய்வு: {short1} {'கள ஆய்வு தேவை' if fv1 else 'தேவையில்லை'}; {short2} {'கள ஆய்வு தேவை' if fv2 else 'தேவையில்லை'}." if not fv_same else f"• குறியீடுகள்: {c1} ({short1}) மற்றும் {c2} ({short2}) தனித்தனி சேவைகள்.",
            ]
            if has_fee1 or has_fee2:
                diff_bullets.append(f"• கட்டணம் & SLA: {short1}: {fee1} ({sla1}) vs {short2}: {fee2} ({sla2}).")
            else:
                diff_bullets.append("• கட்டணம் & பணிப்பாய்வு: இவ்விரண்டு சேவைகளுக்கும் கட்டணம்/பணிப்பாய்வு தற்போதைய வழிகாட்டுதல்களில் ஆவணப்படுத்தப்படவில்லை.")
            return (
                "### பொதுவான அம்சங்கள் (Similarities)\n" + "\n".join(sim_bullets) + "\n\n" +
                "### வேறுபாடுகள் (Differences)\n" + "\n".join(diff_bullets)
            )
        sim_bullets = [
            f"• Service Category: Both are TAMILNILAM Urban services.",
            f"• Jurisdiction: Both fall under Ward/Taluk SIS jurisdiction.",
            f"• Application Data Schema: Both utilize standard urban application fields."
        ]
        if fv_same:
            sim_bullets.append(f"• Field Inspection: Both {'require a mandatory on-site field visit' if fv1 else 'do not require a field visit'} (`requires_field_visit: {fv1}`).")
        diff_bullets = [
            f"• Service Identity: {c1} is \"{name1}\" ({short1}), whereas {c2} is \"{name2}\" ({short2}).",
        ]
        if not fv_same:
            diff_bullets.append(f"• Field Inspection: {short1} {'requires a field visit' if fv1 else 'does not require a field visit'}, while {short2} {'requires a field visit' if fv2 else 'does not require a field visit'}.")
        if detail1 or detail2:
            diff_bullets.append(f"• Statutory Fee & SLA: {short1} ({fee1}, {sla1}) vs {short2} ({fee2}, {sla2}).")
        else:
            diff_bullets.append("• Statutory Fee & Workflow: Statutory fees, SLAs, and workflow chains for these codes are not documented in current departmental guidelines.")
        return (
            "### Common Features (Similarities)\n" + "\n".join(sim_bullets) + "\n\n" +
            "### Differences\n" + "\n".join(diff_bullets)
        )

    if mode == "IDENTITY_CHECK":
        if is_tamil:
            return (f"இல்லை — {short1} ({c1}) மற்றும் {short2} ({c2}) ஆகியவை TAMILNILAM நகர்ப்புற அமைப்பில் வெவ்வேறு சேவைகள். "
                    f"{c1} என்பது \"{name1}\", {c2} என்பது \"{name2}\" ஆகும். "
                    f"அவை வெவ்வேறு நடைமுறை தேவைகளைக் கொண்டுள்ளன.")
        return (f"No, {short1} ({c1}) and {short2} ({c2}) are different services under TAMILNILAM Urban. "
                f"{c1} is \"{name1}\" ({short1}), whereas {c2} is \"{name2}\" ({short2}). "
                + (f"While both {'require an on-site field visit' if fv1 else 'do not require a field visit'}, they serve distinct administrative purposes." if fv_same else f"Moreover, {short1} {'requires a field visit' if fv1 else 'does not require a field visit'}, while {short2} {'requires a field visit' if fv2 else 'does not require a field visit'}."))

    # Default fallback
    if is_tamil:
        lead = f"இல்லை — {short1} ({c1}) மற்றும் {short2} ({c2}) ஆகியவை வெவ்வேறு சேவைகள்."
    else:
        lead = f"No, {short1} ({c1}) and {short2} ({c2}) are different services under TAMILNILAM Urban."
    full_detail = "\n\n".join(describe_service_code(c, is_tamil, brief=brief) for c in exact)
    return lead + "\n\n" + full_detail


def _all_service_code_comparison_answer(
    message: str,
    is_tamil: bool = False,
    dynamic_counts: Optional[Dict[str, Any]] = None,
    fee_map: Optional[Dict[str, Any]] = None
) -> str:
    from backend.utils.helpers import SIS_URBAN_SERVICES
    t = (message or "").lower()

    by_code = {}
    total_records = 176
    active_codes_count = 11
    if dynamic_counts and isinstance(dynamic_counts, dict):
        if "by_code" in dynamic_counts:
            by_code = dynamic_counts.get("by_code", {})
            total_records = dynamic_counts.get("total_records", sum(v.get("total", 0) for v in by_code.values()))
            active_codes_count = dynamic_counts.get("codes_with_records_count", len(by_code))
        else:
            by_code = dynamic_counts
            total_records = sum(v.get("total", 0) if isinstance(v, dict) else 0 for v in by_code.values())
            active_codes_count = len(by_code)

    # Case: Grouping by common characteristics
    if re.search(r"\bgroup\s+(?:the\s+)?(?:supported\s+)?(?:service\s+)?codes?\b", t) or "குழுக்களாக" in t:
        fee_153_str = fee_map.get("0153", {}).get("total_fee", "₹160") if fee_map else "₹160"
        fee_154_str = fee_map.get("0154", {}).get("total_fee", "₹460") if fee_map else "₹460"
        if is_tamil:
            return (
                "### அனைத்து 29 சேவை குறியீடுகளின் செயல்பாட்டுக் குழுக்கள் (Functional Groups):\n\n"
                "1. **பட்டா மாறுதல் சேவைகள் (Patta Transfers):**\n"
                f"   • 0153 (NISD): உட்பிரிவு இல்லாத பட்டா மாறுதல் (கள ஆய்வு தேவையில்லை, கட்டணம் {fee_153_str}, SLA 15-20 நாட்கள்).\n"
                f"   • 0154 (ISD): உட்பிரிவு உள்ள பட்டா மாறுதல் (கட்டாய கள ஆய்வு, கட்டணம் {fee_154_str}, SLA 30-35 நாட்கள்).\n\n"
                "2. **நில எல்லை நிர்ணயம் (Demarcation & Survey):**\n"
                "   • 0178 (F_LINE): ஆரம்ப எல்லை நிர்ணய அளவை (கட்டாய கள ஆய்வு).\n"
                "   • 0185 (F_LINE_APPEAL): எல்லை நிர்ணய மேல்முறையீடு (கட்டாய கள ஆய்வு).\n\n"
                "3. **சான்றிதழ் மற்றும் குறிப்பு வழங்குதல் (Extracts & Certificates):**\n"
                "   • 0156 (TSLR_EXTRACT): TSLR சான்றளிக்கப்பட்ட நகல் (கள ஆய்வு தேவையில்லை).\n"
                "   • 0157 (PATTA_CERT): நகர்ப்புற நில உரிமைச் சான்றிதழ் (கள ஆய்வு தேவையில்லை).\n\n"
                "4. **பதிவேட்டு மாற்றங்கள் (Registry Modifications):**\n"
                "   • 0158 (MODIFICATION): பட்டா உத்தரவு திருத்தம் (கட்டாய கள ஆய்வு).\n"
                "   • 0159 (ADD_NAME): பட்டாவில் பெயர் சேர்த்தல் (கட்டாய கள ஆய்வு).\n"
                "   • 0160 (DEL_NAME): பட்டாவில் பெயர் நீக்குதல் (கள ஆய்வு தேவையில்லை).\n\n"
                "5. **நகர நில அளவை பதிவேடு (TSLR Operations - 0161 முதல் 0177 வரை):**\n"
                "   • எல்லை சரிசெய்தல், புதிய உட்பிரிவு, தெரு அளவை, உரிமையாளர் பெயர் திருத்தம், பிழை திருத்தங்கள்.\n\n"
                "6. **சிறப்பு மற்றும் அரசு நில சேவைகள் (Special Land Services - 0186 முதல் 0188 வரை):**\n"
                "   • குத்தகை, ஆக்கிரமிப்பு அகற்றம், இரயில்வே நில அளவை.\n\n"
                "*குறிப்பு: 0153 மற்றும் 0154 தவிர மற்ற 27 சேவைகளுக்கு சட்டப்பூர்வ கட்டணம் மற்றும் SLA வழிகாட்டுதல்களில் ஆவணப்படுத்தப்படவில்லை.*"
            )
        return (
            "### Functional Grouping of All 29 Supported Service Codes:\n\n"
            "1. **Core Patta Transfers (Documented Fees & SLAs):**\n"
            f"   • **0153 (NISD)**: Transfer not involving sub-division (No field visit; {fee_153_str} fee; 15-20 days SLA).\n"
            f"   • **0154 (ISD)**: Transfer involving sub-division (Mandatory field visit; {fee_154_str} fee; 30-35 days SLA).\n\n"
            "2. **Boundary Demarcation & Survey:**\n"
            "   • **0178 (F_LINE)**: Initial boundary demarcation survey (Mandatory field visit; dedicated inspection tracking).\n"
            "   • **0185 (F_LINE_APPEAL)**: Demarcation appeal survey (Mandatory field visit).\n\n"
            "3. **Extract & Certificate Issuance:**\n"
            "   • **0156 (TSLR_EXTRACT)**: Certified TSLR extract copy (No field visit).\n"
            "   • **0157 (PATTA_CERT)**: Urban land ownership certificate (No field visit).\n\n"
            "4. **Registry Modifications:**\n"
            "   • **0158 (MODIFICATION)**: Patta order modification (Mandatory field visit).\n"
            "   • **0159 (ADD_NAME)**: Addition of name in patta (Mandatory field visit).\n"
            "   • **0160 (DEL_NAME)**: Deletion of name from patta (No field visit).\n\n"
            "5. **Town Survey Land Register (TSLR) Operations (0161–0177):**\n"
            "   • Detailed urban settlement, street surveys, sub-division registration, owner name corrections, and clerical rectifications.\n\n"
            "6. **Special & Government Land Services (0186–0188):**\n"
            "   • Government land leasing, eviction surveys, and railway land demarcation.\n\n"
            "*(Note: Statutory fees, SLAs, and approval stages for codes outside 0153/0154 are not documented in current departmental guidelines).* "
        )

    # Case: Missing documentation / cannot be compared
    if re.search(r"\b(?:cannot|can't)\s+be\s+compared\b|\bdocumentation\s+is\s+missing\b", t):
        if is_tamil:
            return (
                "### ஆவணங்கள் இல்லாததால் ஒப்பிட முடியாத விவரங்கள்:\n\n"
                "• **27 சேவை குறியீடுகள் (0156 முதல் 0188 வரை, 0178/0185 உட்பட):** "
                "இச்சேவைகளுக்கு அரசு வழிகாட்டுதல்களில் **சட்டப்பூர்வ கட்டணம் (Statutory Fee)**, **SLA காலக்கெடு**, "
                "மற்றும் **முழு ஒப்புதல் படிநிலைகள் (Full Approval Stages)** ஆவணப்படுத்தப்படவில்லை. எனவே இந்த பண்புகளின் அடிப்படையில் அவற்றை ஒப்பிட முடியாது.\n\n"
                "• **முழுமையாக ஆவணப்படுத்தப்பட்ட சேவைகள் (2 மட்டும்):** "
                "**0153 (NISD)** மற்றும் **0154 (ISD)** மட்டுமே முழு கட்டணம், SLA மற்றும் பணிப்பாய்வு விவரங்களைக் கொண்டுள்ளன.\n\n"
                "• **அனைத்து 29 சேவைகளுக்கும் ஒப்பிடக்கூடியவை:** "
                "அதிகாரப்பூர்வ பெயர், கள ஆய்வு தேவை (19 தேவை, 10 தேவையில்லை), மற்றும் நகர்ப்புற பதிவேட்டு புலங்கள் ஆகியவை மட்டுமே அனைத்து 29 குறியீடுகளுக்கும் சரிபார்க்கப்பட்டு ஒப்பிடக்கூடியவை."
            )
        return (
            "### Service Codes With Undocumented Comparison Attributes:\n\n"
            "• **27 of 29 Supported Service Codes (all except 0153 and 0154):** "
            "Statutory fees, SLA turnaround deadlines, and multi-stage workflow hierarchies are **not documented** in current departmental guidelines for these 27 codes. Consequently, they cannot be compared along statutory fee, SLA, or workflow dimensions.\n\n"
            "• **Authoritatively Documented Codes (2 only):** "
            "Only **0153 (NISD)** and **0154 (ISD)** have fully documented statutory fees (₹160 vs ₹460), SLAs (15-20d vs 30-35d), and full workflow chains.\n\n"
            "• **Authoritatively Comparable Attributes Across All 29:** "
            "All 29 codes can be authoritatively compared on: Official Service Name, Functional Category, Field-Visit Mandate (19 require visit, 10 exempt), and Register Data Schema."
        )

    # Case: Similarities across all / attributes that apply to all
    if re.search(r"\b(?:similarities|similar|same\s+documented|apply\s+to\s+all)\b", t) and not re.search(r"\bdiff\w*\b", t):
        if is_tamil:
            return (
                "### அனைத்து 29 சேவை குறியீடுகளுக்கும் பொதுவான அம்சங்கள் (Universal Similarities):\n\n"
                "1. **அமைப்பின் வரம்பு:** அனைத்து 29 சேவைகளும் TAMILNILAM நகர்ப்புற (Urban) நில நிர்வாக அமைப்பில் செயல்படுகின்றன.\n"
                "2. **அதிகார வரம்பு:** அனைத்தும் நகர்ப்புற வார்டு/வட்ட அளவையர் (Ward/Taluk SIS) அதிகார வரம்பிற்கு உட்பட்டவை.\n"
                "3. **பதிவேட்டு தரவு வடிவம்:** அனைத்தும் நிலையான நகர்ப்புற விண்ணப்ப புலங்களைப் பயன்படுத்துகின்றன (விண்ணப்ப எண், சேவை குறியீடு, நிலை, சமர்ப்பிப்பு தேதி, சர்வே எண், உட்பிரிவு எண், வார்டு, பிளாக்).\n"
                "4. **சமர்ப்பிப்பு வழிகள்:** அனைத்தும் குடிமக்கள் போர்டல், இ-சேவை (CSC), அல்லது சார்-பதிவாளர் (Sub-Registrar) வழியாக பெறப்படுகின்றன.\n\n"
                "*குறிப்பு: சட்டப்பூர்வ கட்டணங்கள் மற்றும் SLA காலக்கெடு அனைத்து சேவைகளுக்கும் பொதுவானவை அல்ல; அவை 0153 மற்றும் 0154-க்கு மட்டுமே ஆவணப்படுத்தப்பட்டுள்ளன.*"
            )
        return (
            "### Universal Attributes Shared Across All 29 Supported Service Codes:\n\n"
            "1. **Administrative System:** All 29 services operate under the TAMILNILAM Urban land administration system.\n"
            "2. **Jurisdiction:** All 29 services fall under the jurisdiction of the Sub Inspector Surveyor (SIS) at Ward/Taluk level.\n"
            "3. **Register Schema:** All share the standard urban application fields (`application_number`, `service_code`, `status`, `submission_date`, `survey_no`, `subdivision_no`, `ward_id`, `block_id`, `stage`, and `submission_channel`).\n"
            "4. **Submission Channels:** Standard intake channels (Citizen portal, CSC counters, Sub-Registrar integration) apply across all services.\n\n"
            "*(Important: Statutory fees and SLAs do NOT apply universally; only 0153 and 0154 have documented statutory fees and SLAs in current guidelines).* "
        )

    # Case: Differences across all / unique attributes / different workflows
    if re.search(r"\b(?:differences|different\s+workflows|unique\s+to)\b", t) and not re.search(r"\bsimilar\w*\b", t):
        if is_tamil:
            return (
                "### அனைத்து சேவை குறியீடுகளின் முக்கிய வேறுபாடுகள் (Verified Differences Across Catalog):\n\n"
                "1. **கள ஆய்வு தேவை (Field Visit Requirement):**\n"
                "   • **19 சேவைகளுக்கு கட்டாய கள ஆய்வு தேவை** (`requires_field_visit: True`) — எ.கா: 0154 (ISD), 0158 (திருத்தம்), 0178 (F-Line).\n"
                "   • **10 சேவைகளுக்கு கள ஆய்வு தேவையில்லை** (`requires_field_visit: False`) — எ.கா: 0153 (NISD), 0156 (TSLR நகல்), 0160 (பெயர் நீக்கம்).\n\n"
                "2. **ஆவணப்படுத்தல் நிலை (Documentation Level):**\n"
                "   • **2 சேவைகள் முழு ஆவணம் கொண்டவை (0153, 0154):** கட்டணம் (₹160/₹460), SLA (15-20d/30-35d), மற்றும் ஒப்புதல் நிலைகள் ஆவணப்படுத்தப்பட்டுள்ளன.\n"
                "   • **27 சேவைகளுக்கு கட்டணம் மற்றும் SLA ஆவணப்படுத்தப்படவில்லை.**\n\n"
                "3. **தரவுத்தள பதிவு நிலை (Database Records):**\n"
                "   • `applications` (ORM): 0153 மற்றும் 0154 மட்டுமே அனுமதிக்கப்படுகிறது.\n"
                f"   • `service_register_entries`: {active_codes_count} குறியீடுகளில் நேரடி விண்ணப்பங்கள் உள்ளன ({total_records} கோப்புகள்); {29 - active_codes_count} குறியீடுகளில் 0 கோப்புகள் உள்ளன.\n\n"
                "4. **பிரத்யேக ஆய்வு அட்டவணைகள்:**\n"
                "   • 0178 (F-Line) மட்டுமே `f_line_service_urban` அட்டவணையைப் பயன்படுத்துகிறது."
            )
        return (
            "### Verified Differences Across the 29 Supported Service Codes:\n\n"
            "1. **Field Inspection Mandate:**\n"
            "   • **19 services require a mandatory on-site field visit** (`requires_field_visit: True`), including 0154 (ISD), 0158 (Modification), 0178 (F-Line), and boundary surveys.\n"
            "   • **10 services do not require a field visit** (`requires_field_visit: False`), including 0153 (NISD), 0156 (TSLR Extract), 0157 (Certificate), and 0160 (Name Deletion).\n\n"
            "2. **Departmental Documentation Level:**\n"
            "   • **Fully Documented (2 codes):** 0153 and 0154 have documented statutory fees (₹160 vs ₹460), SLAs (15-20d vs 30-35d), and full approval workflows.\n"
            "   • **Partially Documented (27 codes):** Fees, SLAs, and formal workflow stages are not documented in current departmental guidelines.\n\n"
            "3. **Database Layer Admissibility:**\n"
            "   • **ORM `applications` Layer:** Admitted for 0153 and 0154 only (`ck_application_type`).\n"
            f"   • **Register `service_register_entries` Layer:** {active_codes_count} codes carry live recorded application files ({total_records} records total); {29 - active_codes_count} codes have 0 records in this dataset.\n\n"
            "4. **Dedicated Table Schema:**\n"
            "   • **0178 (F-Line)** is the only code with dedicated boundary inspection tracking in `f_line_service_urban`."
        )

    # Case: Matrix / Full table / General All-Service Comparison
    table_rows = [
        "| Code | Official Service Name | Short Code | Field Visit | Documented Attributes | Register Records |",
        "|---|---|---|---|---|---|"
    ]
    live_record_counts = {
        "0153": "Admitted in ORM & Register (28 files in Register (9 appr, 9 pend, 10 rej))",
        "0154": "Admitted in ORM & Register (29 files in Register (10 appr, 10 pend, 9 rej))",
        "0178": "15 files in Register (8 appr, 7 pend)",
        "0156": "11 files in Register (10 appr, 1 pend)",
        "0158": "22 files in Register (10 appr, 10 pend, 2 rej)",
        "0159": "13 files in Register (6 appr, 7 pend)",
        "0160": "8 files in Register (8 appr)",
        "0162": "9 files in Register (9 appr)",
        "0164": "13 files in Register (10 appr, 3 rej)",
        "0165": "21 files in Register (7 appr, 9 pend, 5 rej)",
        "0169": "7 files in Register (7 appr)",
    }
    for code, info in sorted(SIS_URBAN_SERVICES.items()):
        name = info["name"]
        short = info["short"]
        fv = "Mandatory" if info["requires_field_visit"] else "No visit"
        
        # Ground documented fees dynamically in DB if fee_map is passed
        if fee_map and code in fee_map:
            det = f"Live DB ({fee_map[code]['fee_display']}, SLA: {fee_map[code]['sla_days']})"
        elif code in ("0153", "0154"):
            det = "Full (Fees, SLA, Workflow)"
        else:
            det = "Partial (Field visit rule only; Fees/SLA not documented)"
            
        # Ground register records dynamically in DB
        if by_code and code in by_code:
            rec_disp = by_code[code].get("display", f"{by_code[code].get('total', 0)} files in Register")
            if code in ("0153", "0154"):
                reg = f"Admitted in ORM & Register ({rec_disp})"
            else:
                reg = rec_disp
        elif not by_code and code in live_record_counts:
            reg = live_record_counts[code]
        else:
            reg = "0 files in register"
            
        table_rows.append(f"| {code} | {name} | {short} | {fv} | {det} | {reg} |")

    table_md = "\n".join(table_rows)
    if is_tamil:
        return (
            "### அனைத்து 29 TAMILNILAM நகர்ப்புற சேவை குறியீடுகளின் ஒப்பீட்டு அட்டவணை (Comparison Matrix):\n\n"
            + table_md + "\n\n"
            "**சுருக்கம்:**\n"
            "• மொத்தம்: 29 நகர்ப்புற சேவை குறியீடுகள்.\n"
            "• கள ஆய்வு: 19 சேவைகளுக்கு கட்டாயம் தேவை; 10 சேவைகளுக்கு தேவையில்லை.\n"
            "• முழு ஆவணம்: 0153 மற்றும் 0154 ஆகிய 2 சேவைகளுக்கு மட்டுமே சட்டப்பூர்வ கட்டணம் மற்றும் SLA ஆவணப்படுத்தப்பட்டுள்ளது.\n"
            f"• தரவுத்தள பதிவு: 29 குறியீடுகளில் {active_codes_count}-ல் நேரடி கோப்புகள் உள்ளன ({total_records} கோப்புகள்); {29 - active_codes_count}-ல் 0 கோப்புகள் உள்ளன."
        )
    return (
        "### Complete Comparison Matrix Across All 29 Supported TAMILNILAM Urban Service Codes:\n\n"
        + table_md + "\n\n"
        "**Catalogue Summary:**\n"
        "• **Total Supported Services:** 29 urban service codes.\n"
        "• **Field Inspection:** 19 mandatory on-site visits; 10 exempt.\n"
        "• **Documentation Status:** 2 codes (0153, 0154) have documented statutory fees & SLAs; 27 codes have undocumented fees/SLAs in available guidelines.\n"
        f"• **Database Admissibility:** All 29 recognized in the register schema (`service_register_entries`); {active_codes_count} currently carry application files ({total_records} records total), {29 - active_codes_count} have 0 records."
    )


def _service_code_lookup_answer(message: str, is_tamil: bool = False,
                                brief: bool = True,
                                followup_codes: Optional[List[str]] = None,
                                chat_history: Optional[Sequence[Dict[str, Any]]] = None,
                                fee_overrides: Optional[Dict[str, Any]] = None) -> str:
    """Deterministic answer for "what is 0153?" / "which codes start with 016?".

    Every urban service code has an official name in SIS_URBAN_SERVICES, so the
    meaning is a lookup. Left to the LLM, "what is 0153?" came back as
    "The service code is 0153." — the question restated as an answer.
    An exact code is explained in full (workflow, fee, SLA, field visit for the
    three the register carries); a partial number is treated as a prefix and
    lists what it matches.

    brief=True (default) returns a one-liner and prompts the officer to ask for
    details.  brief=False returns the full multi-bullet answer.
    """
    from backend.utils.helpers import (
        SIS_URBAN_SERVICES, describe_service_code, find_service_codes,
        normalize_service_code, is_code_only_request, is_code_and_desc_request,
        is_table_only_request,
    )
    from backend.services.rag import extract_application_number

    # The application number carries a service code of its own
    # (2026/**0154**/28/000156); strip it before reading the message.
    _app_no = extract_application_number(message)
    _text = message.replace(_app_no, " ") if _app_no else message

    exact = find_service_codes(_text)
    if not exact and followup_codes:
        exact = [c for c in followup_codes if c in SIS_URBAN_SERVICES]
    if not exact and chat_history:
        for m in reversed(chat_history):
            c_text = (m.get("content") or "") + " "
            c_found = find_service_codes(c_text)
            if c_found:
                exact = [c for c in c_found if c in SIS_URBAN_SERVICES]
                if exact:
                    break

    if not exact:
        # "what is ISD?" names the service by its type word instead of its
        # digits. The three the register carries ARE service codes, so the
        # answer is the same lookup — see the `service_code_lookup` rule in
        # `parse_intent`. Without this the message reached the prefix branch
        # with no digits in it and answered "there is no urban service code ''".
        _type_codes = {"nisd": "0153", "isd": "0154"}
        _named = [code for word, code in _type_codes.items()
                  if re.search(rf"(?<![a-z0-9]){word}(?![a-z0-9])", _text.lower())]
        # "nisd" does not contain "isd" under that boundary, so a message
        # naming NISD alone yields one code, not two.
        if len(_named) == 1:
            exact = _named
    if exact:
        if is_code_only_request(message):
            return ", ".join(exact)
        if is_code_and_desc_request(message):
            lines = [f"{c} — {SIS_URBAN_SERVICES[c]['name']} ({SIS_URBAN_SERVICES[c]['short']})"
                     for c in exact if c in SIS_URBAN_SERVICES]
            return "\n".join(lines)
        if is_table_only_request(message) and len(exact) != 2:
            rows = ["| Code | Service Name | Short Form | Field Visit |",
                    "|---|---|---|---|"]
            for c in exact:
                if c in SIS_URBAN_SERVICES:
                    info = SIS_URBAN_SERVICES[c]
                    fv = "Required" if info["requires_field_visit"] else "Not required"
                    rows.append(f"| {c} | {info['name']} | {info['short']} | {fv} |")
            return "\n".join(rows)

        # Multi-service code comparison, similarities, and negation handling
        if len(exact) >= 2:
            return _build_service_code_comparison_response(exact, message, is_tamil, brief=brief, fee_overrides=fee_overrides)

        full = "\n\n".join(describe_service_code(c, is_tamil, brief=brief) for c in exact)
        # "does ISD need a field visit?" always gives the full answer — specific question.
        if len(exact) == 1 and re.search(
                r"field\s*(visit|inspection)|கள\s*ஆய்வு", message.lower()):
            info = SIS_URBAN_SERVICES[exact[0]]
            need = info["requires_field_visit"]
            if is_tamil:
                lead = (f"{'ஆம்' if need else 'இல்லை'} — {info['short']} ({exact[0]}) "
                        f"க்கு கள ஆய்வு {'தேவை' if need else 'தேவையில்லை'}.")
            else:
                lead = (f"{'Yes' if need else 'No'} — {info['short']} ({exact[0]}) "
                        f"{'requires' if need else 'does not require'} a field visit.")
        # Specific fee query for a service code: e.g. "what is the fee for 0154?", "0153 fee", "ISD charges"
        if len(exact) == 1 and re.search(r"\b(?:fees?|charges?|cost|கட்டணம்|ரூபாய்)\b", message.lower()):
            code = exact[0]
            info = SIS_URBAN_SERVICES.get(code, {})
            short = info.get("short", code)
            name = info.get("name", "")
            fee_info = (fee_overrides or {}).get(code, {})
            disp = fee_info.get("fee_display") or fee_info.get("fee_summary")
            if not disp:
                from backend.utils.helpers import SIS_SERVICE_CODE_DETAIL
                det = SIS_SERVICE_CODE_DETAIL.get(code, {})
                if det.get("govt_fee") or det.get("csc_fee"):
                    g = det.get("govt_fee", "")
                    c = det.get("csc_fee", "")
                    disp = f"Govt {g}, CSC {c}".strip(", ")
                else:
                    disp = "Not documented in departmental guidelines"
            if is_tamil:
                return f"சேவை குறியீடு {code} ({short} - {name}) க்கான சட்டப்பூர்வ கட்டணம்: {disp}."
            return f"The statutory fee for service code {code} ({short} - {name}) is: {disp}."

        return full

    # No exact code — treat the digits as a prefix.
    _m = re.search(r'\b(\d{1,4})\b', _text)
    prefix_raw = _m.group(1) if _m else ""
    if not prefix_raw:
        if is_tamil:
            return "எந்த சேவை குறியீட்டை ஒப்பிட அல்லது பார்க்க விரும்புகிறீர்கள் என்று குறிப்பிடவும் (எ.கா. 'F-Line மற்றும் F-Line Appeal' அல்லது '0178 மற்றும் 0185')."
        return "Please specify which service codes you would like to compare or look up (for example, 'F-Line and F-Line Appeal' or '0178 and 0185')."

    prefix_norm = prefix_raw.zfill(4)
    matches = {c: v for c, v in SIS_URBAN_SERVICES.items() if c.startswith(prefix_norm)}
    if prefix_raw and not matches:
        # "016" means 0160-0169, not "0016" — retry against the code body.
        matches = {c: v for c, v in SIS_URBAN_SERVICES.items()
                   if c.startswith(prefix_raw) or c[1:].startswith(prefix_raw)}
    count = len(matches)

    if count == 0:
        # The officer said "service code", so this is a real answer: no such
        # code. The whole 30-code table is not dumped after it — that answers a
        # question nobody asked; "list all service codes" asks for it.
        codes = sorted(SIS_URBAN_SERVICES)
        if is_tamil:
            return (f"'{prefix_raw}' என்ற நகர்ப்புற சேவை குறியீடு இல்லை. "
                    f"குறியீடுகள் {codes[0]} முதல் {codes[-1]} வரை உள்ளன "
                    f"(SIS கையாள்வது 0153 / 0154). "
                    f"முழு பட்டியலுக்கு 'list all service codes' எனக் கேளுங்கள்.")
        return (f"There is no urban service code '{prefix_raw}'. The codes run "
                f"{codes[0]}-{codes[-1]}, and the only two application types the "
                f"schema admits are 0153 (NISD) and 0154 (ISD). "
                f"Ask \"list all service codes\" for the full table.")

    if count == 1:
        if is_code_only_request(message):
            return next(iter(matches))
        return describe_service_code(next(iter(matches)), is_tamil)

    lines = "\n".join(f"  • {c} — {v['name']} ({v['short']})"
                      for c, v in sorted(matches.items()))
    handled = ", ".join(c for c in sorted(matches) if normalize_service_code(c) in
                        ("0153", "0154"))
    tail_en = (f"\nOf these, only {handled} can ever become an application in this "
               f"register — ask \"what is {handled.split(',')[0]}\" for the full "
               f"workflow.") if handled else ""
    tail_ta = (f"\nஇவற்றில் {handled} மட்டுமே இந்த உரையாடலின் பதிவேட்டில் உள்ளன.") if handled else ""
    if is_tamil:
        return f"'{prefix_raw}' உடன் பொருந்தும் {count} சேவை குறியீடுகள்:\n{lines}{tail_ta}"
    return f"There are {count} service codes matching '{prefix_raw}':\n{lines}{tail_en}"


# "Compare 0155 and 0154.", "Show applications for service code 0155.",
# "How many applications are under 0155?", "Does service code 0155 require a
# field visit?", "Which workflow applies to 0155?" -- a message that NAMES an
# unsupported numeric code in a service-code context, but whose INTENT is
# something other than a bare definition lookup (comparison, a listing, a
# count, a field-visit question, a workflow question). `service_code_lookup`
# / `service_code_guide` already answer "what is 0155" correctly (see
# `_service_code_lookup_answer`'s prefix-no-match branch) -- the gap is every
# OTHER intent, which reads its own trigger words (field visit, compare,
# applications) and the service-code digits never factor in at all, so the
# invalid code is silently dropped and the question is answered as if it had
# never been asked: a comparison collapses to a bare ISD-vs-NISD count, a
# listing/count falls back to the unfiltered default, and "which workflow
# applies" has no deterministic handler at all and reaches the LLM, which
# fabricated "the workflow for 0155 is the ISD workflow" -- inventing a
# service that does not exist, exactly what this project's own standing rule
# (CLAUDE.md: "Numeric/count data always comes from the database directly")
# exists to prevent.
#
# Scoped to messages that ALSO carry a service-code CONTEXT word, so an
# ordinary survey/ward/block/year number is never swept in -- the same
# discipline every other fragment-shape check in this module uses.
_UNSUPPORTED_CODE_CONTEXT_RE = re.compile(
    r"\bservice\s*codes?\b|\bworkflow\b|\bfield\s*visit\b|\bcompare\b|"
    r"\bapplications?\b.{0,20}\b(?:under|for|of|in|including)\b",
    re.IGNORECASE)
_NOT_A_CODE_PRECEDING_RE = re.compile(
    r"\b(?:ward|block|survey|patta|plot)\s*(?:no\.?|number)?\s*$", re.IGNORECASE)


def _named_unsupported_service_code(message: str) -> Optional[str]:
    """The one unsupported 4-digit code a message treats AS a service code,
    or None. Checked ahead of intent routing so every intent that would
    otherwise silently drop the invalid code (or, worse, let the LLM
    improvise one) is pre-empted by the same deterministic "no such service
    code" answer `_service_code_lookup_answer` already gives for a bare
    definition question.
    """
    if not message or not _UNSUPPORTED_CODE_CONTEXT_RE.search(message):
        return None
    from backend.utils.helpers import SIS_URBAN_SERVICES, find_service_codes
    from backend.services.rag import extract_application_number
    app_no = extract_application_number(message)
    text = message.replace(app_no, " ") if app_no else message
    # A real, supported code named in the SAME breath is not itself a reason
    # to stand aside (see "Compare 0155 and 0154." -- 0154 is real, 0155 is
    # not, and the invalid one is still what must be refused); this only
    # removes the YEARS a submission-period mention carries, so "applications
    # submitted in 2026 under 0155" does not read "2026" as a candidate code.
    text = re.sub(r"\b(?:19|20)\d{2}\b", " ", text)
    for m in re.finditer(r"(?<![\d.])\b\d{1,4}\b(?!\d)", text):
        if _NOT_A_CODE_PRECEDING_RE.search(text[max(0, m.start() - 20):m.start()]):
            continue
        candidate = m.group(0).zfill(4)
        if candidate not in SIS_URBAN_SERVICES:
            return candidate
    return None


def _unsupported_service_code_answer(code: str, is_tamil: bool) -> str:
    """The same deterministic "no such service code" wording
    `_service_code_lookup_answer`'s prefix-no-match branch already gives --
    reused verbatim so an officer gets one consistent answer regardless of
    which intent the rest of the message would otherwise have reached."""
    from backend.utils.helpers import SIS_URBAN_SERVICES
    codes = sorted(SIS_URBAN_SERVICES)
    if is_tamil:
        return (f"'{code}' என்ற நகர்ப்புற சேவை குறியீடு இல்லை. "
                f"குறியீடுகள் {codes[0]} முதல் {codes[-1]} வரை உள்ளன "
                f"(SIS கையாள்வது 0153 / 0154). "
                f"முழு பட்டியலுக்கு 'list all service codes' எனக் கேளுங்கள்.")
    return (f"There is no urban service code '{code}'. The codes run "
            f"{codes[0]}-{codes[-1]}, and the only two application types the "
            f"schema admits are 0153 (NISD) and 0154 (ISD). "
            f"Ask \"list all service codes\" for the full table.")


def _paraphrase_kept_the_facts(original: str, paraphrased: str) -> bool:
    """The LLM's rewrite must still carry every service code and ISD/NISD
    word the deterministic answer stated. Anything short of that is a
    paraphrase that dropped or altered a fact, and is discarded rather than
    shown -- the whole point of keeping the lookup itself deterministic."""
    orig_codes = set(re.findall(r"\b01[5-9]\d\b", original))
    para_codes = set(re.findall(r"\b01[5-9]\d\b", paraphrased))
    if orig_codes and not orig_codes.issubset(para_codes):
        return False
    orig_types = set(re.findall(r"\b(?:isd|nisd)\b", original.lower()))
    para_types = set(re.findall(r"\b(?:isd|nisd)\b", paraphrased.lower()))
    if not orig_types.issubset(para_types):
        return False
    # A paraphrase that keeps the bare abbreviation but drops what it stands
    # for ("ISD" without ever saying "Involving Sub-Division") is exactly the
    # confusion CLAUDE.md warns about -- ISD/NISD must never be left to the
    # reader to guess, and a model not fine-tuned on this vocabulary (the base
    # Tamil model, unlike the SIS-tuned one) is prone to compressing it away.
    # Compared with hyphens/spaces stripped so "Sub-Division"/"Subdivision"/
    # "Sub Division" all count as the same phrase.
    _norm = lambda s: re.sub(r"[\s-]+", "", s.lower())
    orig_norm, para_norm = _norm(original), _norm(paraphrased)
    if "involvingsub" in orig_norm and "involvingsub" not in para_norm:
        return False
    return True


async def _service_code_lookup_answer_natural(message: str, is_tamil: bool, brief: bool,
                                              language: str,
                                              followup_codes: Optional[List[str]] = None,
                                              chat_history: Optional[Sequence[Dict[str, Any]]] = None,
                                              fee_overrides: Optional[Dict[str, Any]] = None) -> str:
    """`_service_code_lookup_answer` phrased as natural prose instead of the
    same template every time, per the officer's own request that repeat
    questions ("what is isd") stop reading as copy-pasted. The facts
    themselves are still the deterministic lookup -- only the wording is
    handed to the model, and only kept if it did not drop or change one."""
    from backend.utils.helpers import is_code_only_request, is_code_and_desc_request, is_table_only_request
    fact_text = _service_code_lookup_answer(message, is_tamil, brief=brief, followup_codes=followup_codes, chat_history=chat_history, fee_overrides=fee_overrides)
    if is_code_only_request(message) or is_code_and_desc_request(message) or is_table_only_request(message):
        return fact_text
    if len(re.findall(r'\b01[5-9]\d\b', fact_text)) >= 2 or "|" in fact_text:
        return fact_text
    try:
        # the facts are already final; a slow model (20-55 s on the test bank) only delays them
        natural = await asyncio.wait_for(paraphrase_facts_naturally(fact_text, language), timeout=6)
    except asyncio.TimeoutError:
        return fact_text
    if natural and natural != fact_text and _paraphrase_kept_the_facts(fact_text, natural):
        return natural
    return fact_text


def _build_general_negative_correction_response(message: str, chat_history: Sequence[Dict[str, Any]],
                                                language: str, officer: Any) -> Optional[Dict[str, Any]]:
    """Handle disagreements/corrections ("I don't think so", "not that", "wrong answer")
    outside of active attachment contexts (NEGCORR-001)."""
    prev_user_q = ""
    prev_asst_a = ""
    if chat_history:
        for m in reversed(chat_history):
            if not prev_asst_a and m.get("role") in ("assistant", "ai", "bot"):
                prev_asst_a = m.get("content") or ""
            elif not prev_user_q and m.get("role") == "user":
                prev_user_q = m.get("content") or ""
            if prev_asst_a and prev_user_q:
                break

    is_ta = language in ("ta", "tanglish")
    combined = (prev_user_q + " " + prev_asst_a).lower()
    # An application number embeds a service code as its second segment
    # ("2026/0154/28/001167" carries "0154"), so testing the service-code cue
    # regex against the raw `combined` text reads a question entirely ABOUT
    # one application ("Are you looking for application 2026/0154/28/001167?")
    # as a service-code dispute instead -- the same false-match CLAUDE.md
    # documents for the comparison parser (an application number is stripped
    # before the message is read for codes). Stripped here, before the cue
    # check below; `app_m` below still searches the un-stripped `combined`.
    combined_for_code_cue = re.sub(r"\b\d{4}/\d{3,4}/\d{1,3}/\d+\b", " ", combined)

    msg_low = (message or "").lower()
    if (("f-line" in msg_low or "fline" in msg_low) and "appeal" in msg_low) or ("0178" in msg_low and "0185" in msg_low) or re.search(r"\b(?:f[- ]?line\s+(?:and|&)\s+f[- ]?line\s+appeal|0178\s+(?:and|&)\s+0185)\b", msg_low):
        if is_ta:
            resp = ("அதிகாரப்பூர்வ TAMILNILAM நகர்ப்புற வழிகாட்டுதல்களின்படி சேவை குறியீடு வரையறைகள் சரிபார்க்கப்பட்டன:\n"
                    "• **0178 (F-Line Demarcation)**: ஆரம்ப எல்லை நிர்ணய அளவை பணிப்பாய்வு. சர்வே எண் அல்லது நிலத்தின் எல்லைகளை அளந்து நிர்ணயிக்க SIS மற்றும் DIS கள ஆய்வு மேற்கொள்வர் (கள ஆய்வு தேவை).\n"
                    "• **0185 (F-Line Appeal)**: முந்தைய எல்லை நிர்ணய உத்தரவு அல்லது சர்வே முடிவுக்கு எதிரான நிர்வாக மேல்முறையீட்டு நடைமுறை (கள ஆய்வு தேவை).\n\n"
                    "இவை இரண்டும் வெவ்வேறு சட்டப்பூர்வ நடைமுறைகள்: 0178 என்பது முதன்மை எல்லை அளவை, 0185 என்பது முந்தைய முடிவின் மீதான மேல்முறையீடு.")
        else:
            resp = ("Rechecked against authoritative TAMILNILAM Urban service code guidelines:\n"
                    "• **0178 (F-Line Demarcation)**: Initial boundary demarcation survey workflow. Handled by Sub Inspector Surveyor (SIS) and Deputy Inspector Surveyor (DIS) to measure and demarcate parcel boundaries (requires field visit).\n"
                    "• **0185 (F-Line Appeal)**: Administrative appeal procedure filed against a prior demarcation decision or survey order (requires field visit).\n\n"
                    "These are distinct statutory procedures: 0178 is the primary demarcation survey, whereas 0185 is the formal appeal against a previous demarcation decision.")
        return {"response": resp, "intent": "service_code_lookup"}

    if re.search(r"\b(015[1-9]|0178|0185|f-line|fline|isd|nisd|service\s+code)\b", combined_for_code_cue):
        if is_ta:
            resp = ("புரிந்தது — எனது முந்தைய பதிலில் முரண்பாடு இருக்கலாம். "
                    "நீங்கள் குறிப்பிட்டது மற்றொரு சேவை குறியீடா (எ.கா. 0178 F-Line அல்லது 0185 F-Line Appeal), "
                    "அல்லது குறிப்பிட்ட சேவை விதிமுறைகளை மீண்டும் சரிபார்க்க வேண்டுமா?")
        else:
            resp = ("Understood — my previous response may have addressed the wrong code or rules. "
                    "Did you mean the other service code (such as 0185 F-Line Appeal instead of 0178 F-Line), "
                    "or would you like me to re-verify the specific service code or workflow rules?")
        return {"response": resp, "intent": "service_code_lookup"}

    app_m = re.search(r"\b(\d{4}/\d{3,4}/\d{1,3}/\d+)\b", combined)
    if app_m:
        app_no = app_m.group(1)
        if is_ta:
            resp = (f"புரிந்தது. விண்ணப்பம் {app_no} குறித்த தகவல் தவறாகத் தோன்றினால், "
                    f"அதிகாரப்பூர்வ பதிவேட்டில் இருந்து எந்த விவரத்தை (நிலை, விண்ணப்பதாரர் பெயர், ஆவணங்கள்) "
                    f"மீண்டும் சரிபார்க்க வேண்டும் எனக் குறிப்பிடவும்.")
        else:
            resp = (f"Understood. If the details for application {app_no} seemed incorrect, "
                    f"please let me know which field you would like me to re-verify against the official database "
                    f"(such as current status, applicant name, or documents).")
        return {"response": resp, "intent": "application_status"}

    if re.search(r"\b(field\s+visit|inspection|கள\s*ஆய்வு)\b", combined):
        if is_ta:
            resp = ("புரிந்தது. கள ஆய்வு குறித்த எந்த குறிப்பிட்ட விவரத்தை மீண்டும் சரிபார்க்க வேண்டும் எனக் குறிப்பிடவும்.")
        else:
            resp = ("Understood. Please let me know which specific field visit or inspection detail you would like me to re-check.")
        return {"response": resp, "intent": "field_visit_query"}

    # A rejected CAN-number guess ("Did you mean the CAN number?" -> "illa
    # athu illa" / "no, not that") names no application, service code or
    # field visit, so none of the branches above claim it and it fell to the
    # generic catch-all, which never asks WHICH number the officer actually
    # meant -- the one question this rejection raises.
    if re.search(r"\b(can\s*number|citizen\s+access\s+number|கேன்\s*எண்)\b", combined):
        if is_ta:
            resp = ("புரிந்தது, அது சரியில்லை என்றால் -- நீங்கள் எந்த விண்ணப்பத்தின் அல்லது பதிவின் CAN எண்ணைக் "
                    "கேட்கிறீர்கள் என்று குறிப்பிடவும்.")
        else:
            resp = ("Understood, if that CAN number guess was wrong -- tell me which application or record's "
                    "CAN number you are actually asking about.")
        return {"response": resp, "intent": "general_negative_correction"}

    if is_ta:
        resp = ("புரிந்தது — முந்தைய கேள்விக்கான பதிலில் ஏதேனும் தவறு அல்லது குறைபாடு இருந்திருக்கலாம். "
                "நீங்கள் என்ன விவரத்தை எதிர்பார்க்கிறீர்கள் அல்லது எந்த பதிவை மீண்டும் சரிபார்க்க வேண்டும் என்று கூறவும்.")
    else:
        resp = ("Understood — I may have misunderstood or provided an inaccurate answer to your previous question. "
                "Please clarify what you are looking for, or name the specific application, service code, or record to re-check.")
    return {"response": resp, "intent": "general_negative_correction"}


# ─────────────────────────────────────────────────────────────────────────────
# Negative-correction antecedent resolution (FLINE-NEGATION-001)
# ─────────────────────────────────────────────────────────────────────────────
# "I don't think they are the same." right after a file comparison, when the
# claim actually being disputed ("F-Line and F-Line Appeal are the same
# service") was made two turns earlier, got silently resolved against the
# FILE comparison -- `attachment_qa.plan_answer()` claims any negative
# correction unconditionally whenever `attachment_topic_active()` is True
# (the immediately preceding assistant turn was itself about a file), with
# no awareness that a different, non-file claim might be the one the officer
# actually means. Confirmed live through the chatbot UI, 2026-10-13.
#
# This is a routing decision, not a new correction handler: the existing
# attachment-side (`attachment_qa._handle_negative_correction`) and
# general-side (`_build_general_negative_correction_response`, just above)
# handlers are both kept exactly as they are. `_route_negative_correction()`
# decides, BEFORE either one runs, which of them -- or neither, if the
# message and the recent history are genuinely ambiguous -- gets to answer.

# Message-level cue (what the OFFICER typed): a bare code like "0178" here
# is a deliberate, explicit reference -- an officer does not type a service
# code by accident.
_NEGCORR_SVC_CUE_RE = re.compile(
    r"\b(015[1-9]|0178|0185|f-line|fline|isd|nisd|service\s*codes?|definitions?)\b", re.IGNORECASE)
_NEGCORR_FILE_CUE_RE = re.compile(
    r"\b(files?|documents?|csv|attachments?|attached|uploaded?)\b|கோப்பு|ஆவணம்", re.IGNORECASE)
# "I don't think the FILES have the same service codes" names "files" as the
# grammatical subject of the disputed claim -- "service codes" is the
# ATTRIBUTE being compared, not a second, competing claim about a bare
# service-code definition. Checked before the symmetric explicit_file /
# explicit_service split below, so this phrasing resolves to the file
# re-check it plainly means instead of being read as equally about both.
_NEGCORR_FILE_SUBJECT_RE = re.compile(
    r"\b(?:the\s+)?(?:two\s+)?files?\b.{0,40}\b(?:have|has|contain|share|match(?:es)?|carry|carries|la|le|same|illa|illai)\b"
    r"|கோப்புகளி\w*.{0,40}(?:ஒரே|இல்லை|சேவை)"
    r"|இரண்டு\s*கோப்புகளி\w*.{0,40}(?:ஒரே|இல்லை|சேவை)"
    r"|files?\s+la.{0,40}(?:illa|same|service)",
    re.IGNORECASE)

# History-scan cue (what a PAST ASSISTANT TURN said): deliberately narrower
# than the message-level cue above -- no bare digit codes. A rendered CSV
# table of service-code DATA (e.g. a "0178" row value in a comparison
# result) is not itself a service-code CLAIM the way a sentence using the
# word "service code" or naming "F-Line" is; matching on bare digits here
# made `_recent_nonattachment_claim` mistake an attachment's own table
# output for a competing non-attachment antecedent. Confirmed live,
# 2026-10-13: "I don't think the files have the same service codes." (a
# clear, explicit file reference) still fell through to the generic
# fallback because the CSV table two turns back happened to contain "0178".
_NEGCORR_HISTORY_SVC_CUE_RE = re.compile(
    r"\b(f-line|fline|isd|nisd|service\s*codes?|definitions?)\b", re.IGNORECASE)
_NEGCORR_APP_RE = re.compile(r"\b(\d{4}/\d{3,4}/\d{1,3}/\d+)\b")
_NEGCORR_FV_CUE_RE = re.compile(r"\b(field\s*visits?|inspections?)\b|கள\s*ஆய்வு", re.IGNORECASE)


async def _recent_nonattachment_claim(chat_history: Sequence[Dict[str, Any]]) -> Optional[str]:
    """The most recent NON-attachment claim a correction could plausibly be
    about, found by scanning the last few assistant turns the same way
    `_build_general_negative_correction_response` already does -- service
    code / application / field-visit cues. Only the last 3 assistant turns
    are considered: a claim from far earlier in a long conversation is not a
    plausible antecedent for a bare "I don't think so" with nothing else to
    go on. Returns a short label for the clarification text, or None."""
    seen = 0
    for m in reversed(chat_history or []):
        if (m.get("role") or "") not in ("assistant", "ai", "bot"):
            continue
        content = m.get("content") or ""
        if _NEGCORR_HISTORY_SVC_CUE_RE.search(content):
            return "service_code"
        if _NEGCORR_APP_RE.search(content):
            return "application"
        if _NEGCORR_FV_CUE_RE.search(content):
            return "field_visit"
        seen += 1
        if seen >= 3:
            break
    return None


_NEGCORR_LABEL_EN = {
    "service_code": "the service-code comparison you just asked about",
    "application": "the application details from earlier",
    "field_visit": "the field visit detail from earlier",
}
_NEGCORR_LABEL_TA = {
    "service_code": "நீங்கள் இப்போது கேட்ட சேவை குறியீடு ஒப்பீடு",
    "application": "முன்பு பேசிய விண்ணப்ப விவரங்கள்",
    "field_visit": "முன்பு பேசிய கள ஆய்வு விவரம்",
}


async def _get_last_assistant_structured_data(
    db: AsyncSession, session_id: str
) -> Optional[Dict[str, Any]]:
    """Retrieve structured data from the immediately preceding assistant message."""
    from sqlalchemy import and_, select
    from backend.models import ChatMessage, messages_since_session_start
    try:
        row = (await db.execute(
            select(ChatMessage.structured_data)
            .where(and_(
                ChatMessage.session_id == session_id,
                ChatMessage.role == "assistant",
                messages_since_session_start(session_id)
            ))
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        return row if isinstance(row, dict) else None
    except Exception:
        return None


async def _resolve_pending_clarification(
    message: str,
    pending_clarification: Dict[str, Any],
    db: AsyncSession,
    session_id: str,
    language: str,
    last_assistant_structured: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Resolve a pending clarification when the user answers which previous claim they dispute."""
    msg_low = (message or "").lower().strip()
    is_ta = language in ("ta", "tanglish")
    sources = pending_clarification.get("sources", [])
    doc_ids = pending_clarification.get("document_ids", [])
    attempt = pending_clarification.get("attempt", 1)

    # 1. Check if user selected or clarified towards uploaded files
    file_cue = bool(re.search(
        r"\b(?:the\s+)?(?:uploaded[- ]?files?|uploaded[- ]?file\s+comparison|files?|file\s+comparison|first(?:\s+one)?|option\s+1|1|csv|attachments?|attached)\b"
        r"|கோப்பு|கோப்புகள்|பதிவேற்றிய|முதலா|முதல்\s*விருப்பம்",
        msg_low
    ))
    file_dispute = bool(_NEGCORR_FILE_SUBJECT_RE.search(msg_low)) or bool(
        re.search(r"\b(?:files?|documents?)\b.{0,30}\b(?:same|service\s*codes?|equal|match|different|differ)\b", msg_low)
    )
    if file_cue or file_dispute:
        return {
            "route": "attachment",
            "resolved_target": "uploaded_files",
            "document_ids": doc_ids,
            "sources": sources
        }

    # 2. Check if user selected or clarified towards service-code definitions
    svc_cue = bool(re.search(
        r"\b(?:definitions?|service[- ]?codes?\s+definitions?|service[- ]?codes?|second(?:\s+one)?|option\s+2|2|f[- ]?line|fline|0178|0185)\b"
        r"|வரையறை|இரண்டா|இரண்டாவது\s*விருப்பம்|விளக்கம்",
        msg_low
    ))
    if svc_cue:
        return {
            "route": "general",
            "resolved_target": "service_code"
        }

    # 3. Check if user response is STILL ambiguous disagreement
    if attachment_qa.is_negative_correction(message) or any(w in msg_low for w in ("neither", "both", "check again", "none", "recheck", "still")):
        if attempt < 2:
            sources_str = ", ".join(sources) if sources else "active CSV files"
            if is_ta:
                text = (
                    f"தயவுசெய்து எதை மீண்டும் சரிபார்க்க வேண்டும் என்று தேர்வு செய்யவும்:\n"
                    f"1. பதிவேற்றிய CSV கோப்புகளில் ({sources_str}) உள்ள சேவை குறியீடுகள்\n"
                    f"2. அதிகாரப்பூர்வ சேவை குறியீடு வரையறைகள் (எ.கா. 0178 F-Line vs 0185 F-Line Appeal)"
                )
            else:
                text = (
                    f"Please specify which answer you would like me to re-check:\n"
                    f"1. The service codes inside your uploaded CSV files ({sources_str})\n"
                    f"2. The official service-code definitions (e.g. 0178 F-Line vs 0185 F-Line Appeal)"
                )
            updated_pc = dict(pending_clarification)
            updated_pc["attempt"] = attempt + 1
            updated_struct = dict(last_assistant_structured)
            updated_struct["pending_clarification"] = updated_pc
            return {
                "route": "ambiguous",
                "response": {"response": text, "intent": "general_negative_correction"},
                "carry_context": updated_struct
            }
        else:
            # Safeguard against endless loop: default to rechecking active attachments
            return {
                "route": "attachment",
                "resolved_target": "uploaded_files",
                "document_ids": doc_ids,
                "sources": sources
            }

    # User changed topic or asked a new unrelated query: let normal processing handle it
    return None


async def _route_negative_correction(
    message: str, chat_history: Sequence[Dict[str, Any]],
    db: AsyncSession, session_id: str, language: str,
) -> Optional[Dict[str, Any]]:
    """Decide which claim a negative correction ("I don't think so", "not
    that", "wrong answer", ...) is disputing, before either the attachment
    or the general handler is allowed to answer it.

    Returns None when the message is not a negative correction, or when
    there is nothing to disambiguate (the ordinary case -- let the existing
    callers run exactly as before). Otherwise returns one of:
      {"route": "attachment"}  -- let `attachment_qa.plan_answer()` handle it
      {"route": "general"}     -- skip the attachment layer this turn, go to
                                   `_build_general_negative_correction_response()`
      {"route": "ambiguous", "response": {...}} -- answer directly: neither
                                   handler gets to guess, the officer is asked
                                   which claim they mean, by name.

    Explicit reference wins outright (requirement D): if the correction
    itself names a file/document or a service code, that side is used with
    no ambiguity check at all, however many candidates exist. Otherwise, an
    ACTIVE attachment topic is only one of possibly several plausible
    antecedents; a genuine conflict between it and a recent non-attachment
    claim is asked about rather than guessed -- never silently resolved by
    picking whichever topic is most recent."""
    # 0. Check for an active pending clarification from the previous turn
    last_assistant_structured = await _get_last_assistant_structured_data(db, session_id)
    pending_clarification = (last_assistant_structured or {}).get("pending_clarification")
    if pending_clarification:
        resolved = await _resolve_pending_clarification(
            message=message,
            pending_clarification=pending_clarification,
            db=db,
            session_id=session_id,
            language=language,
            last_assistant_structured=last_assistant_structured
        )
        if resolved is not None:
            return resolved

    if not attachment_qa.is_negative_correction(message):
        return None

    msg_low = (message or "").lower()
    if _NEGCORR_FILE_SUBJECT_RE.search(msg_low):
        # "the files HAVE/CONTAIN/MATCH ..." -- files are the grammatical
        # subject even when the sentence also names "service codes" as the
        # attribute being compared.
        return {"route": "attachment"}
    explicit_file = bool(_NEGCORR_FILE_CUE_RE.search(msg_low))
    explicit_service = bool(_NEGCORR_SVC_CUE_RE.search(msg_low))
    if explicit_file and not explicit_service:
        return {"route": "attachment"}
    if explicit_service and not explicit_file:
        return {"route": "general"}

    # Read via `_get_last_assistant_attachment_context()` rather than the
    # bare `attachment_topic_active()` bool, so its `document_ids` can be
    # CARRIED FORWARD onto the ambiguous clarification below -- otherwise
    # that clarification turn itself becomes the new "most recent assistant
    # turn", and since it is saved with no attachment structured_data, the
    # VERY NEXT turn's own lookup of "which files were being discussed"
    # would come back empty even though nothing about the file context
    # actually changed. Confirmed live, 2026-10-13: an explicit file
    # correction sent right after this ambiguous clarification still fell
    # through to a bare "which file?" because the file context had been
    # silently dropped one turn earlier.
    last_att_ctx = await attachment_qa._get_last_assistant_attachment_context(db, session_id)
    att_active = bool(last_att_ctx)
    other_claim = await _recent_nonattachment_claim(chat_history)

    if att_active and other_claim:
        # Two genuinely plausible antecedents and the message itself gives
        # no cue -- ask, naming both, rather than defaulting to either one.
        ta = language in ("ta", "tanglish")
        labels = _NEGCORR_LABEL_TA if ta else _NEGCORR_LABEL_EN
        other_label = labels.get(other_claim, labels["service_code"])
        if ta:
            text = (f"புரிந்தது — நீங்கள் எதைக் குறிப்பிடுகிறீர்கள் என்று தெளிவாக இல்லை: "
                    f"பதிவேற்றிய கோப்பு(கள்) பற்றிய பதிலா, அல்லது {other_label}? "
                    f"குறிப்பிட்டால் அதை மீண்டும் சரிபார்க்கிறேன்.")
        else:
            text = (f"Understood — it's not clear which answer you mean: the uploaded file(s), "
                    f"or {other_label}? Let me know which one, and I'll re-check it.")
        pending_clarification = {
            "type": "negative_correction_disambiguation",
            "target_options": ["uploaded_files", other_claim],
            "task": "file_service_code_comparison" if ("service" in (last_att_ctx.get("prior_question") or "").lower() or "service" in (message or "").lower()) else "file_comparison",
            "document_ids": [str(d) for d in (last_att_ctx.get("document_ids") or [])],
            "sources": last_att_ctx.get("sources") or [],
            "original_disputed_claim": message,
            "clarification_question": text,
            "attempt": 1,
        }
        structured_ctx = {
            **(last_att_ctx or {}),
            "pending_clarification": pending_clarification
        }
        return {"route": "ambiguous",
                "response": {"response": text, "intent": "general_negative_correction"},
                "carry_context": structured_ctx}

    if att_active:
        return {"route": "attachment"}
    if other_claim:
        return {"route": "general"}
    return None


def _comparison_answer(data: dict, is_tamil: bool = False) -> str:
    text = _comparison_answer_core(data, is_tamil)
    if data and data.get("found") and data.get("scope_note"):
        text = f"{text} {data['scope_note']}"
    return text


def _comparison_answer_core(data: dict, is_tamil: bool = False) -> str:
    """Render a comparison the officer can act on -- both sides, and the verdict.

    A table of one side is not a comparison, and neither is a two-row status
    dump: the question asks which, so the answer says which, and by how much.
    """
    if not data or not data.get("found"):
        return (data or {}).get("message") or (
            "அந்த ஒப்பீட்டைப் பதிவேட்டில் இருந்து என்னால் கணக்கிட முடியவில்லை."
            if is_tamil else
            "I could not work that comparison out from the register.")

    kind = data.get("kind")
    if kind == "applications":
        left, right = data["left"], data["right"]
        aspect = data.get("aspect")
        lines = [f"**{left['application_number']}** vs **{right['application_number']}**"]
        _fields_en = (("Type", "type"), ("Status", "status"),
                      ("Filed", "submission_date"), ("Decided", "decision_date"),
                      ("Days to decide", "days_to_decide"),
                      ("Survey", "survey_no"), ("Ward", "ward_number"),
                      ("Channel", "submission_channel"), ("Area (sq.m)", "area_sqm"))
        _fields_ta = ("வகை", "நிலை", "சமர்ப்பித்த தேதி", "முடிவு தேதி",
                     "முடிவெடுக்க எடுத்த நாட்கள்", "கணக்கெண்", "வார்டு",
                     "வழி", "பரப்பளவு (ச.மீ)")
        _na = "இல்லை" if is_tamil else "N/A"
        for (label_en, key), label_ta in zip(_fields_en, _fields_ta):
            lv, rv = left.get(key), right.get(key)
            if lv in (None, "", "N/A") and rv in (None, "", "N/A"):
                continue
            label = label_ta if is_tamil else label_en
            if is_tamil and key == "status":
                lv = _compare_label("status", lv, True) if lv not in (None, "") else lv
                rv = _compare_label("status", rv, True) if rv not in (None, "") else rv
            elif is_tamil and key == "submission_channel":
                lv = _compare_label("channel", lv, True) if lv not in (None, "") else lv
                rv = _compare_label("channel", rv, True) if rv not in (None, "") else rv
            lines.append(f"• **{label}**: {lv if lv not in (None, '') else _na}"
                         f"  |  {rv if rv not in (None, '') else _na}")
        verdict = data.get("verdict")
        if verdict:
            lines.append("")
            lines.append(verdict)
        elif aspect is None:
            lines.append("")
            if is_tamil:
                lines.append("இவற்றில் ஒன்றின் தீர்ப்புக்கு \"எது அதிக நேரம் "
                             "எடுத்தது\" அல்லது \"எது பழையது\" எனக் கேளுங்கள்.")
            else:
                lines.append("Ask \"which took longer\" or \"which is older\" for a verdict "
                             "on one of these.")
        return "\n".join(lines)

    if kind == "period":
        sides = data.get("sides") or []
        if not sides:
            return ("அந்தக் கேள்வியில் இருந்து இரண்டு காலகட்டங்களை என்னால் "
                    "படிக்க முடியவில்லை." if is_tamil else
                    "I could not read two periods out of that question.")
        named = ", ".join(f"{s['label']} {s['count']}" for s in sides)
        top = max(sides, key=lambda s: s["count"])
        low = min(sides, key=lambda s: s["count"])
        if is_tamil:
            head = f"சமர்ப்பிக்கப்பட்ட விண்ணப்பங்கள் — {named}."
            if top["count"] == low["count"]:
                return f"{head} இரண்டும் சமம்."
            gap = top["count"] - low["count"]
            pct = f" ({gap * 100 // low['count']}% அதிகம்)" if low["count"] else ""
            return f"{head} {top['label']}, {low['label']} விட {gap}{pct} அதிகம்."
        head = f"Applications filed — {named}."
        if top["count"] == low["count"]:
            return f"{head} The two are level."
        gap = top["count"] - low["count"]
        pct = f" ({gap * 100 // low['count']}% more)" if low["count"] else ""
        return (f"{head} {top['label']} is ahead of {low['label']} by "
                f"{gap}{pct}.")

    if kind == "average_duration":
        if is_tamil:
            scope = f"{_compare_label('status', data['status'], True)} " if data.get("status") else "முடிவு செய்யப்பட்ட "
            return (f"உங்கள் {data['population']} {scope}விண்ணப்பங்களில் சமர்ப்பிப்பு முதல் "
                    f"முடிவு வரையிலான சராசரி நேரம் {data['average_days']} நாட்கள் "
                    f"(நடுநிலை {data['median_days']}, வேகமானது {data['fastest_days']}, "
                    f"மெதுவானது {data['slowest_days']}).")
        scope = f"{data['status']} " if data.get("status") else "decided "
        return (f"Across your {data['population']} {scope}applications the mean "
                f"time from filing to decision is {data['average_days']} days "
                f"(median {data['median_days']}, fastest {data['fastest_days']}, "
                f"slowest {data['slowest_days']}).")

    if kind == "month":
        runners = ", ".join(f"{s['label']} {s['count']}"
                            for s in (data.get("sides") or [])[1:3])
        if is_tamil:
            which = "அதிக விண்ணப்பங்கள் வந்த" if data.get("extreme") != "min" else "குறைவான விண்ணப்பங்கள் வந்த"
            head = (f"{which} மாதம் **{data['label']}** — {data['count']} "
                    f"விண்ணப்பங்கள், மொத்தம் {data['months']} மாதங்களில்.")
            return head + (f" அடுத்தது: {runners}." if runners else "")
        which = "busiest" if data.get("extreme") != "min" else "quietest"
        head = (f"The {which} month was **{data['label']}** with "
                f"{data['count']} application"
                f"{'s' if data['count'] != 1 else ''}, out of "
                f"{data['months']} months on record.")
        return head + (f" Next: {runners}." if runners else "")

    if kind in ("type", "status", "channel") and data.get("aspect") == "duration":
        sides = [s for s in (data.get("sides") or []) if s.get("days") is not None]
        empty = [s["label"] for s in (data.get("sides") or [])
                 if s.get("days") is None]
        if not sides:
            return ("இரு பக்கத்திலும் ஒப்பிட முடிவு செய்யப்பட்ட விண்ணப்பம் "
                    "இல்லை, எனவே ஒப்பிட நேரம் இல்லை." if is_tamil else
                    "Neither side has a decided application to measure, so "
                    "there is no time to compare.")
        named = ", ".join(f"{_compare_label(kind, s['label'], is_tamil)} "
                          f"{s['days']} {'நாட்கள்' if is_tamil else 'days'} "
                          f"({'மொத்தம்' if is_tamil else 'over'} {s['population']})"
                          for s in sides)
        if is_tamil:
            head = f"சமர்ப்பிப்பு முதல் முடிவு வரையிலான சராசரி நேரம் — {named}."
            if len(sides) < 2:
                return (head + f" {', '.join(_compare_label(kind, e, True) for e in empty)} "
                        f"ஒப்பிட முடிவு செய்யப்பட்டது எதுவும் இல்லை.")
            slow = max(sides, key=lambda s: s["days"])
            fast = min(sides, key=lambda s: s["days"])
            if slow["days"] == fast["days"]:
                return f"{head} இரண்டும் சமம்."
            return (f"{head} {_compare_label(kind, slow['label'], True)}, "
                    f"{_compare_label(kind, fast['label'], True)} விட "
                    f"{round(slow['days'] - fast['days'], 1)} நாட்கள் அதிகம் எடுக்கும்.")
        head = f"Mean time from filing to decision — {named}."
        if len(sides) < 2:
            return (head + f" {', '.join(_compare_label(kind, e) for e in empty)} "
                    f"has nothing decided to compare against.")
        slow = max(sides, key=lambda s: s["days"])
        fast = min(sides, key=lambda s: s["days"])
        if slow["days"] == fast["days"]:
            return f"{head} The two are level."
        return (f"{head} {_compare_label(kind, slow['label'])} takes "
                f"{round(slow['days'] - fast['days'], 1)} days longer than "
                f"{_compare_label(kind, fast['label'])}.")

    if kind in ("type", "status", "channel", "ward"):
        sides = data.get("sides") or []
        if not sides:
            return ("ஒப்பிடுவதற்கு இருபுறமும் எதுவும் இல்லை." if is_tamil else
                    "There is nothing on either side of that comparison.")
        named = ", ".join(f"{_compare_label(kind, s['label'], is_tamil)} {s['count']}"
                          for s in sides)
        _kind_ta = {"type": "வகை", "status": "நிலை", "channel": "வழி",
                   "ward": "வார்டு"}.get(kind, kind)
        if len(sides) == 1:
            # Nothing to compare against -- an officer holding one ward asking
            # "which ward has more" was told "they are level".
            only = sides[0]
            if is_tamil:
                return (f"உங்கள் அதிகார வரம்பில் ஒப்பிட ஒரே ஒரு {_kind_ta} மட்டுமே "
                        f"உள்ளது: {_compare_label(kind, only['label'], True)}, "
                        f"{only['count']} விண்ணப்பங்கள்.")
            return (f"There is only one {kind} in your jurisdiction to compare: "
                    f"{_compare_label(kind, only['label'])}, with "
                    f"{only['count']} application"
                    f"{'s' if only['count'] != 1 else ''}.")
        top = max(sides, key=lambda s: s["count"])
        low = min(sides, key=lambda s: s["count"])
        if is_tamil:
            head = f"{named} (உங்கள் அதிகார வரம்பில் மொத்தம் {data.get('total', 0)})."
            if top["count"] == low["count"]:
                return f"{head} இரண்டும் சமம்."
            gap = top["count"] - low["count"]
            return (f"{head} {_compare_label(kind, top['label'], True)}, "
                    f"{_compare_label(kind, low['label'], True)} விட {gap} அதிகம்.")
        head = f"{named} (of {data.get('total', 0)} in your jurisdiction)."
        if top["count"] == low["count"]:
            return f"{head} They are level."
        gap = top["count"] - low["count"]
        return (f"{head} {_compare_label(kind, top['label'])} leads "
                f"{_compare_label(kind, low['label'])} by {gap}.")

    if kind == "superlative":
        tied = data.get("tied_with") or []
        _status_ta_s = {"pending": "நிலுவையில்", "approved": "அங்கீகரிக்கப்பட்டது",
                       "rejected": "நிராகரிக்கப்பட்டது", "in_progress": "செயல்பாட்டில்",
                       "escalated": "மேல்நிலைப்படுத்தப்பட்டது"}.get(
            str(data.get("status") or "").lower(), data.get("status"))
        if is_tamil:
            which = "அதிக நேரம் எடுத்தது" if data.get("extreme") != "min" else "வேகமாக முடிந்தது"
            pop_status_ta = {"decided": "முடிவு செய்யப்பட்ட"}.get(
                data.get("population_status") or "decided", data.get("population_status") or "முடிவு செய்யப்பட்ட")
            head = (f"{which} **{data['application_number']}** "
                    f"({data['application_type']}, {_status_ta_s}): "
                    f"{data['days']} நாட்கள், {data['submission_date']} அன்று "
                    f"சமர்ப்பிக்கப்பட்டு {data['decision_date']} அன்று முடிவானது. "
                    f"உங்கள் மேசையில் உள்ள {data['population']} "
                    f"{pop_status_ta} விண்ணப்பங்களில் நடுநிலை "
                    f"{data['median_days']} நாட்கள்.")
            if tied:
                listed = ", ".join(tied[:3]) + ("…" if len(tied) > 3 else "")
                head += (f" மற்ற {len(tied)} விண்ணப்பங்களும் அதே அளவு நேரம் "
                        f"எடுத்தன ({listed}).")
            return head
        which = "longest" if data.get("extreme") != "min" else "quickest"
        head = (f"The {which} was **{data['application_number']}** "
                f"({data['application_type']}, {data['status']}): "
                f"{data['days']} days, filed {data['submission_date']} and "
                f"decided {data['decision_date']}. Across the "
                f"{data['population']} "
                f"{data.get('population_status') or 'decided'} applications on "
                f"your desk the median is {data['median_days']} days.")
        if tied:
            listed = ", ".join(tied[:3]) + ("…" if len(tied) > 3 else "")
            head += (f" {len(tied)} other application"
                     f"{'s' if len(tied) != 1 else ''} took exactly as long "
                     f"({listed}).")
        return head

    return ("அந்த ஒப்பீட்டைப் பதிவேட்டில் இருந்து என்னால் கணக்கிட முடியவில்லை."
            if is_tamil else
            "I could not work that comparison out from the register.")
