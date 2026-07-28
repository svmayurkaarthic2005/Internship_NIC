"""
RAG (Retrieval-Augmented Generation) pipeline for SIS Chatbot

Jurisdiction hierarchy: District → Taluk → Town → Ward → Block → Survey Number → Sub-Division

Components:
  detect_language()             — Tamil / Tanglish / English detection
  get_rag_context()             — pgvector semantic retrieval
  format_structured_data_for_llm() — Plain-text DB summary for LLM fallback
  build_html_response()         — Direct HTML builder (bypasses LLM for table queries)
  build_prompt()                — LLM prompt assembly (only when HTML path returns "")
  call_llama()                  — Synchronous Ollama LLM call
  call_llama_stream()           — Async streaming Ollama LLM call
  parse_intent()                — Keyword + fuzzy intent routing
  clean_message()               — Strip list prefixes
  extract_*()                   — Entity extraction helpers
"""

from langchain_ollama import ChatOllama
from typing import Dict, Any, Optional, Tuple, List
from html import escape
from difflib import SequenceMatcher
from datetime import date, datetime, timedelta
import calendar
import re

from backend.config import settings, DISTRICT_CODE_MAP, DISTRICT_NAME_MAP
from backend.services.pgvector_store import similarity_search
from backend.utils.logger import get_logger
from backend.utils.fuzzy import extract_tokens, is_token_typo_match, normalize_text

logger = get_logger(__name__)

# Initialize Ollama LLM
llm = ChatOllama(
    model=settings.LLM_MODEL,
    base_url=settings.OLLAMA_BASE_URL,
    temperature=0.1
)


# ─────────────────────────────────────────────────────────────────────────────
# detect_language
#   - Default to "tanglish" (no pgvector filter) when content is mixed.
#   - Pure-Tamil threshold at 50% so "Survey 145 எங்கே உள்ளது?"
#     is correctly classified as tanglish, not ta.
# ─────────────────────────────────────────────────────────────────────────────
def detect_language(text: str) -> str:
    """
    Detect language of input text using heuristics.

    Returns:
        "ta"        — predominantly Tamil (>50 % Tamil chars)
        "tanglish"  — mixed Tamil + English, Tamil chars present, or Romanized Tanglish keywords
        "en"        — pure English / no Tamil
    """
    if not text:
        return "en"

    tamil_chars = len(re.findall(r'[\u0B80-\u0BFF]', text))
    total_chars = len(text.strip())

    if total_chars == 0:
        return "en"

    tamil_pct = (tamil_chars / total_chars) * 100

    if tamil_pct > 50:
        return "ta"
    elif tamil_chars > 0:
        # Any Tamil script present alongside English → tanglish
        return "tanglish"

    # Check for Romanized Tanglish keywords
    tanglish_words = {
        "enna", "eppadi", "engay", "yenge", "yenga", "eppo", "vanakkam",
        "sollunga", "pannunga", "solla", "kaattu", "irukku", "irukkanga",
        "kudunga", "yaaru", "evvalavu", "edhu", "edhukku", "aama", "illai",
        "varum", "seri", "romba", "nalla", "panno", "kuduthu"
    }
    words = set(re.findall(r'\b[a-zA-Z]+\b', text.lower()))
    if words.intersection(tanglish_words):
        return "tanglish"

    return "en"


# ─────────────────────────────────────────────────────────────────────────────
# get_rag_context — improved filter-failure logging
# ─────────────────────────────────────────────────────────────────────────────
def get_rag_context(query: str, language: str = "en", n_results: int = 5) -> str:
    """
    Retrieve relevant context from pgvector (knowledge_embeddings) based on query.

    Language filter is applied for pure "en" or "ta"; tanglish searches
    both collections (no filter).
    """
    try:
        where_filter = None
        if language == "ta":
            where_filter = {"language": "tamil"}
        elif language == "en":
            where_filter = {"language": "english"}
        # tanglish → no filter (retrieves from both language documents)

        results = []
        if where_filter:
            try:
                results = similarity_search(query, n_results=n_results, where_filter=where_filter)
                if not results:
                    logger.warning(
                        f"Language filter {where_filter} returned 0 results for query "
                        f"'{query[:60]}'. pgvector may be missing '{language}' metadata on "
                        f"some documents. Retrying without filter."
                    )
                    results = similarity_search(query, n_results=n_results)
            except Exception as filter_err:
                logger.warning(
                    f"Similarity search with filter {where_filter} raised an error: {filter_err}. "
                    f"Retrying without filter."
                )
                results = similarity_search(query, n_results=n_results)
        else:
            results = similarity_search(query, n_results=n_results)

        if not results:
            logger.warning(f"No RAG context found (even without filter) for query: '{query[:60]}'")
            return ""

        context_parts = []
        for i, result in enumerate(results, 1):
            content = result["content"]
            metadata = result.get("metadata", {})
            doc_name = metadata.get("document_name", "Unknown")
            context_parts.append(f"[Source {i}: {doc_name}]\n{content}\n")

        context = "\n---\n".join(context_parts)
        logger.info(f"Retrieved {len(results)} context chunks for query")
        return context

    except Exception as e:
        logger.error(f"Error retrieving RAG context: {e}")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# format_structured_data_for_llm
#   Simplified plain-text summary used ONLY when the LLM fallback is needed
#   (i.e. build_html_response returned "").
# ─────────────────────────────────────────────────────────────────────────────
def format_structured_data_for_llm(structured_data: Dict[str, Any]) -> str:
    """
    Produce a compact plain-text summary of structured_data for the LLM.
    Only used in the LLM fallback path. Does NOT reproduce full table logic —
    that lives exclusively in build_html_response.
    """
    if not structured_data:
        return ""

    lines = ["\n\nSTRUCTURED DATA FROM DATABASE:"]

    query_type = structured_data.get("query_type", "")
    if query_type:
        lines.append(f"Query Type: {query_type}")

    count = structured_data.get("count", 0)
    found = structured_data.get("found", True)

    if not found:
        lines.append("Result: No records found.")
        return "\n".join(lines)

    if count:
        lines.append(f"Total records: {count}")

    skip = {"query_type", "count", "found", "message", "surveys", "applications",
            "surveys_by_block", "sub_divisions", "visits", "workload"}

    for key, value in structured_data.items():
        if key not in skip and not isinstance(value, (list, dict)):
            lines.append(f"{key}: {value}")

    # Explicitly include merge-specific fields that the LLM needs for
    # conversational answers about subdivision contents.
    if structured_data.get("survey_no"):
        if "survey_no" not in str(lines):   # avoid duplicate if already added above
            lines.append(f"survey_no: {structured_data['survey_no']}")

    subdivisions = structured_data.get("subdivisions_being_merged") or []
    if subdivisions:
        lines.append(f"subdivisions_being_merged ({len(subdivisions)}):")
        for sd in subdivisions:
            area_str = f" — {sd['area_sqm']:.2f} sq.m" if sd.get("area_sqm") else ""
            lines.append(f"  • {sd.get('sub_division_no', 'N/A')}{area_str}")
        total = structured_data.get("total_merge_area_sqm")
        if total:
            lines.append(f"total_merge_area_sqm: {total:.2f}")

    if structured_data.get("message"):
        lines.append(f"Note: {structured_data['message']}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# build_html_response — HTML escaping on all DB values
# ─────────────────────────────────────────────────────────────────────────────
def _e(value) -> str:
    """Escape a value for safe HTML insertion."""
    return escape(str(value)) if value is not None else "-"


# Human-readable labels for DB status values — keyed by language
_STATUS_LABELS_EN = {
    "pending":     "Pending",
    "in_progress": "In Progress",
    "approved":    "Approved",
    "rejected":    "Rejected",
    "escalated":   "Escalated",
    "unscheduled": "Unscheduled",
    "scheduled":   "Scheduled",
    "completed":   "Completed",
    "overdue":     "Overdue",
    "rescheduled": "Rescheduled",
    "cancelled":   "Cancelled",
}

_STATUS_LABELS_TA = {
    "pending":     "நிலுவையில்",
    "in_progress": "செயலில்",
    "approved":    "அங்கீகரிக்கப்பட்டது",
    "rejected":    "நிராகரிக்கப்பட்டது",
    "escalated":   "மேல்முறையீடு",
    "unscheduled": "திட்டமிடப்படவில்லை",
    "scheduled":   "திட்டமிடப்பட்டது",
    "completed":   "முடிந்தது",
    "overdue":     "காலதாமதம்",
    "rescheduled": "மறு-திட்டமிடல்",
    "cancelled":   "ரத்து செய்யப்பட்டது",
}

# kept for backward-compat; build_html_response uses _status_lang() instead
_STATUS_LABELS = _STATUS_LABELS_EN


def _status(value, lang: str = "en") -> str:
    """Return a human-readable, HTML-escaped status label in the given language."""
    raw = str(value).lower() if value is not None else ""
    labels = _STATUS_LABELS_TA if lang == "ta" else _STATUS_LABELS_EN
    return escape(labels.get(raw, str(value) if value is not None else "-"))


# Human-readable labels for declared_reason enum values
_REASON_LABELS = {
    "sale":        "Sale",
    "inheritance": "Inheritance",
    "partition":   "Partition",
    "gift_deed":   "Gift Deed",
    "court_order": "Court Order",
    "government":  "Government Acquisition",
    "exchange":    "Exchange",
    "will":        "Will / Testament",
}


def _reason(value) -> str:
    """Return a human-readable, HTML-escaped declared_reason label."""
    if value is None:
        return "-"
    raw = str(value).lower().strip()
    return escape(_REASON_LABELS.get(raw, str(value).replace("_", " ").title()))


def build_html_response(structured_data: Dict[str, Any], language: str = "en") -> str:
    """
    Build a clean HTML response directly from structured DB data,
    bypassing the LLM entirely for table-based queries.

    All DB values are HTML-escaped to prevent XSS / broken markup.
    Intro text uses <div> (not <p>) — browsers auto-close <p> before
    block-level elements like <table>, which hides the <thead>.

    Args:
        structured_data: Database query results
        language: Detected language ("en", "ta", or "tanglish") for table labels

    Returns an HTML string, or "" if no structured data to display
    (caller should then fall back to the LLM).
    """
    if not structured_data or not structured_data.get("found", True):
        return ""

    # Tamil translations for table headers and labels
    labels = {
        "en": {
            "survey_no": "Survey No.", "subdivisions": "Sub-Divisions",
            "district": "District", "taluk": "Taluk", "town": "Town",
            "ward": "Ward", "block": "Block", "area_sqm": "Area (sq.m)",
            "application_no": "Application No.", "type": "Type",
            "status": "Status", "stage": "Stage", "submitted": "Submitted",
            "submission_date": "Submission Date", "days_pending": "Days Pending",
            "field": "Field", "value": "Value",
            "applicant_name": "Applicant Name", "mobile": "Mobile",
            "email": "Email", "address": "Address",
            "survey_number": "Survey Number", "submitted_via": "Submitted Via",
            "sale_deed_number": "Sale Deed Number",
            "sale_deed_registered": "Sale Deed Registered",
            "declared_reason": "Declared Reason",
            "subdivisions_being_merged": "Subdivisions Being Merged",
            "total_merge_area": "Total Merge Area",
            "number_of_subdivisions": "Number of Subdivisions",
            "field_visit_status": "Field Visit Status",
            "scheduled_date": "Scheduled Date",
            "actual_visit_date": "Actual Visit Date",
            "encroachment_found": "Encroachment Found",
            "area_verified": "Area Verified",
            "yes": "Yes", "no": "No", "found": "Found",
            "merge_applications": "merge application(s)",
            "applications": "application(s)",
            "surveys": "survey number(s) in your jurisdiction",
            "no_records_found": "No records found",
            "application_details": "Application Details",
            "here_are": "Here are the",
            "total_area": "Total Area", "land_type": "Land Type",
            "patta_number": "Patta Number", "jurisdiction": "Jurisdiction",
            "CSC": "Common Service Center (CSC)",
            "citizen": "Citizen (Direct)",
            "sub_registrar": "Sub-Registrar Referral",
            "workload_summary": "Your Workload Summary",
            "metric": "Metric", "count": "Count",
            "total_applications": "Total Applications",
            "pending_count": "Pending", "overdue_count": "Overdue",
            "unscheduled_visits": "Field Visits Unscheduled",
            "unscheduled_field_visits": "application(s) with no field visit scheduled",
        },
        "ta": {
            "survey_no": "கணக்கெண்", "subdivisions": "உட்பிரிவுகள்",
            "district": "மாவட்டம்", "taluk": "தாலுகா", "town": "நகரம்",
            "ward": "வார்டு", "block": "தொகுதி", "area_sqm": "பரப்பளவு (சதுர மீ)",
            "application_no": "விண்ணப்ப எண்", "type": "வகை",
            "status": "நிலை", "stage": "கட்டம்", "submitted": "சமர்ப்பிக்கப்பட்டது",
            "submission_date": "சமர்ப்பித்த தேதி",
            "days_pending": "நிலுவையில் உள்ள நாட்கள்",
            "field": "புலம்", "value": "மதிப்பு",
            "applicant_name": "விண்ணப்பதாரரின் பெயர்",
            "mobile": "தொலைபேசி", "email": "மின்னஞ்சல்",
            "address": "முகவரி", "survey_number": "கணக்கெண்",
            "submitted_via": "சமர்ப்பிக்கப்பட்ட முறை",
            "sale_deed_number": "விற்பனை பத்திர எண்",
            "sale_deed_registered": "விற்பனை பத்திரம் பதிவு செய்யப்பட்டது",
            "declared_reason": "அறிவிக்கப்பட்ட காரணம்",
            "subdivisions_being_merged": "இணைக்கப்படும் உட்பிரிவுகள்",
            "total_merge_area": "மொத்த இணைப்பு பரப்பளவு",
            "number_of_subdivisions": "உட்பிரிவுகளின் எண்ணிக்கை",
            "field_visit_status": "கள ஆய்வு நிலை",
            "scheduled_date": "திட்டமிடப்பட்ட தேதி",
            "actual_visit_date": "உண்மையான பார்வை தேதி",
            "encroachment_found": "ஆக்கிரமிப்பு கண்டறியப்பட்டது",
            "area_verified": "பரப்பளவு சரிபார்க்கப்பட்டது",
            "yes": "ஆம்", "no": "இல்லை", "found": "கண்டறியப்பட்டது",
            "merge_applications": "இணைப்பு விண்ணப்பங்கள்",
            "applications": "விண்ணப்பங்கள்",
            "surveys": "உங்கள் அதிகார வரம்பில் உள்ள கணக்கெண்கள்",
            "no_records_found": "பதிவுகள் எதுவும் இல்லை",
            "application_details": "விண்ணப்ப விவரங்கள்",
            "here_are": "இதோ",
            "total_area": "மொத்த பரப்பளவு", "land_type": "நில வகை",
            "patta_number": "பட்டா எண்", "jurisdiction": "அதிகார வரம்பு",
            "CSC": "பொது சேவை மையம் (CSC)",
            "citizen": "குடிமகன் (நேரடி)",
            "sub_registrar": "துணை பதிவாளர் பரிந்துரை",
            "workload_summary": "உங்கள் பணிச்சுமை சுருக்கம்",
            "metric": "அளவீடு", "count": "எண்ணிக்கை",
            "total_applications": "மொத்த விண்ணப்பங்கள்",
            "pending_count": "நிலுவையில்", "overdue_count": "தாமதமானது",
            "unscheduled_visits": "கள ஆய்வு திட்டமிடப்படவில்லை",
            "unscheduled_field_visits": "கள ஆய்வு திட்டமிடப்படாத விண்ணப்பங்கள்",
        },
    }

    # Always use English column headers regardless of the user's language.
    # Tamil/Tanglish users get English table headers so the frontend
    # status-badge logic (keyed on 'Status' / 'Stage') works correctly.
    lang = "en"
    t = labels[lang]

    logger.info(f"build_html_response: language={language!r} -> lang={lang!r}, keys={list(structured_data.keys())}")

    # ── Surveys in jurisdiction ──────────────────────────────────────
    if "surveys" in structured_data and isinstance(structured_data["surveys"], list):
        surveys = structured_data["surveys"]
        count = structured_data.get("count", len(surveys))

        if not surveys:
            msg = _e(structured_data.get("message", t["no_records_found"]))
            return f"<div>{msg}</div>"

        rows = "".join(
            f"<tr>"
            f"<td>{_e(s.get('survey_no'))}</td>"
            f"<td>{_e(s.get('subdivisions') or '-')}</td>"
            f"<td>{_e(s.get('district'))}</td>"
            f"<td>{_e(s.get('taluk'))}</td>"
            f"<td>{_e(s.get('town'))}</td>"
            f"<td>{_e(s.get('ward'))}</td>"
            f"<td>{_e(s.get('block'))}</td>"
            f"</tr>"
            for s in surveys
        )
        return (
            f"<div class='table-intro'>{t['here_are']} <strong>{count}</strong> "
            f"{t['surveys']}:</div>"
            f"<table class='data-table'>"
            f"<thead><tr>"
            f"<th>{t['survey_no']}</th><th>{t['subdivisions']}</th>"
            f"<th>{t['district']}</th><th>{t['taluk']}</th>"
            f"<th>{t['town']}</th><th>{t['ward']}</th><th>{t['block']}</th>"
            f"</tr></thead>"
            f"<tbody>{rows}</tbody>"
            f"</table>"
        )

    # ── Applications (regular + merge) ──────────────────────────────
    if "applications" in structured_data and isinstance(structured_data["applications"], list):
        applications = structured_data["applications"]
        count = structured_data.get("count", len(applications))

        if not applications:
            return f"<div>{t['no_records_found']}</div>"

        # Determine location columns based on officer jurisdiction level:
        # district -> district, taluk, town, ward, block
        # taluk    -> taluk, town, ward, block
        # town     -> town, ward, block
        # ward     -> ward, block
        # block    -> block (only)
        j_type = (structured_data.get("jurisdiction_type") or "block").lower()
        if j_type == "district":
            loc_keys = ["district", "taluk", "town", "ward", "block"]
        elif j_type == "taluk":
            loc_keys = ["taluk", "town", "ward", "block"]
        elif j_type == "town":
            loc_keys = ["town", "ward", "block"]
        elif j_type == "ward":
            loc_keys = ["ward", "block"]
        else:
            loc_keys = ["block"]

        loc_th = "".join(f"<th>{t[k]}</th>" for k in loc_keys)
        is_merge = bool(applications) and ("subdivisions_being_merged" in applications[0] or applications[0].get("type") == "MERGE")

        if is_merge:
            rows = ""
            for app in applications:
                jur = app.get("jurisdiction", {})
                subdivisions = app.get("subdivisions_being_merged", [])
                logger.debug(f"App {app.get('application_number')}: {len(subdivisions)} subdivisions")

                subdiv_list = ", ".join(sd["sub_division_no"] for sd in subdivisions) if subdivisions else "-"
                subdiv_list = _e(subdiv_list)

                merge_area = f"{app['total_merge_area_sqm']:.2f}" if app.get('total_merge_area_sqm') else 'N/A'
                overdue = " ⚠️" if app.get("is_overdue") else ""
                
                loc_map = {
                    "district": jur.get('district') or app.get('district_name') or 'N/A',
                    "taluk": jur.get('taluk') or app.get('taluk_name') or 'N/A',
                    "town": jur.get('town') or app.get('town_name') or 'N/A',
                    "ward": jur.get('ward') or app.get('ward_number') or 'N/A',
                    "block": jur.get('block') or app.get('block_number') or 'N/A'
                }
                loc_td = "".join(f"<td>{_e(loc_map[k])}</td>" for k in loc_keys)

                rows += (
                    f"<tr>"
                    f"<td>{_e(app.get('application_number'))}</td>"
                    f"<td>{_e(app.get('type') or 'MERGE')}</td>"
                    f"<td>{_e(app.get('survey_no'))}</td>"
                    f"<td>{subdiv_list}</td>"
                    f"<td>{merge_area}</td>"
                    f"<td>{_status(app.get('status'), lang)}{overdue}</td>"
                    f"<td>{_e(app.get('stage'))}</td>"
                    f"<td>{_e(app.get('submission_date'))}</td>"
                    f"{loc_td}"
                    f"</tr>"
                )
            return (
                f"<div class='table-intro'>{t['found']} <strong>{count}</strong> {t['merge_applications']}:</div>"
                f"<table class='data-table'>"
                f"<thead><tr>"
                f"<th>{t['application_no']}</th><th>{t['type']}</th><th>{t['survey_no']}</th><th>{t['subdivisions']}</th>"
                f"<th>{t['area_sqm']}</th><th>{t['status']}</th><th>{t['stage']}</th><th>{t['submitted']}</th>"
                f"{loc_th}"
                f"</tr></thead>"
                f"<tbody>{rows}</tbody>"
                f"</table>"
            )
        else:
            rows = ""
            for app in applications:
                overdue = " ⚠️" if app.get("is_overdue") else ""
                jur = app.get("jurisdiction", {})
                
                loc_map = {
                    "district": jur.get('district') or app.get('district_name') or 'N/A',
                    "taluk": jur.get('taluk') or app.get('taluk_name') or 'N/A',
                    "town": jur.get('town') or app.get('town_name') or 'N/A',
                    "ward": jur.get('ward') or app.get('ward_number') or 'N/A',
                    "block": jur.get('block') or app.get('block_number') or 'N/A'
                }
                loc_td = "".join(f"<td>{_e(loc_map[k])}</td>" for k in loc_keys)

                rows += (
                    f"<tr>"
                    f"<td>{_e(app.get('application_number'))}</td>"
                    f"<td>{_e(app.get('type'))}</td>"
                    f"<td>{_status(app.get('status'), lang)}{overdue}</td>"
                    f"<td>{_e(app.get('stage'))}</td>"
                    f"<td>{_e(app.get('submission_date'))}</td>"
                    f"{loc_td}"
                    f"</tr>"
                )
            return (
                f"<div class='table-intro'>{t['found']} <strong>{count}</strong> {t['applications']}:</div>"
                f"<table class='data-table'>"
                f"<thead><tr>"
                f"<th>{t['application_no']}</th><th>{t['type']}</th>"
                f"<th>{t['status']}</th><th>{t['stage']}</th><th>{t['submitted']}</th>"
                f"{loc_th}"
                f"</tr></thead>"
                f"<tbody>{rows}</tbody>"
                f"</table>"
            )

    # ── Ward/Block surveys ───────────────────────────────────────────
    if "surveys_by_block" in structured_data:
        jur = structured_data.get("jurisdiction", {})
        rows = ""
        for block_name, surveys in structured_data["surveys_by_block"].items():
            for s in surveys:
                subdiv = _e(", ".join(s.get("subdivisions", [])) or "-")
                rows += (
                    f"<tr>"
                    f"<td>{_e(s.get('survey_no'))}</td>"
                    f"<td>{subdiv}</td>"
                    f"<td>{_e(block_name)}</td>"
                    f"<td>{_e(jur.get('ward'))}</td>"
                    f"<td>{_e(jur.get('town'))}</td>"
                    f"<td>{_e(jur.get('taluk'))}</td>"
                    f"<td>{_e(jur.get('district'))}</td>"
                    f"</tr>"
                )
        if not rows:
            return f"<div>{t['no_records_found']}</div>"
        return (
            f"<div class='table-intro'>{t['surveys']}:</div>"
            f"<table class='data-table'>"
            f"<thead><tr>"
            f"<th>{t['survey_no']}</th><th>{t['subdivisions']}</th><th>{t['block']}</th>"
            f"<th>{t['ward']}</th><th>{t['town']}</th><th>{t['taluk']}</th><th>{t['district']}</th>"
            f"</tr></thead>"
            f"<tbody>{rows}</tbody>"
            f"</table>"
        )

    # ── Single survey detail ─────────────────────────────────────────
    if "survey_no" in structured_data and "sub_divisions" in structured_data:
        jur = structured_data.get("jurisdiction", {})
        rows = "".join(
            f"<tr>"
            f"<td>{_e(sd.get('sub_division_no'))}</td>"
            f"<td>{sd.get('area_sqm', 0):.2f}</td>"
            f"</tr>"
            for sd in structured_data.get("sub_divisions", [])
        )
        subdiv_table = (
            f"<table class='data-table'>"
            f"<thead><tr><th>{t['subdivisions']}</th><th>{t['area_sqm']}</th></tr></thead>"
            f"<tbody>{rows}</tbody>"
            f"</table>"
            if rows else f"<div>{t['no_records_found']}</div>"
        )
        return (
            f"<div class='table-intro'><strong>{t['survey_no']} {_e(structured_data['survey_no'])}</strong></div>"
            f"<ul>"
            f"<li>{t['total_area']}: <strong>{structured_data.get('total_area_sqm', 0):.2f} sq.m</strong></li>"
            f"<li>{t['land_type']}: {_e(structured_data.get('land_type'))}</li>"
            f"<li>{t['patta_number']}: {_e(structured_data.get('patta_number'))}</li>"
            f"<li>{t['jurisdiction']}: {_e(jur.get('district'))} &rarr; {_e(jur.get('taluk'))} &rarr; "
            f"{_e(jur.get('town'))} &rarr; {_e(jur.get('ward'))} &rarr; {_e(jur.get('block'))}</li>"
            f"</ul>"
            f"<div class='table-intro'>{t['subdivisions']} ({structured_data.get('sub_divisions_count', 0)}):</div>"
            f"{subdiv_table}"
        )

    # ── Single application detail ────────────────────────────────────
    if "application_number" in structured_data and structured_data.get("found", False) and "type" in structured_data:
        app = structured_data
        overdue_flag = " ⚠️ OVERDUE" if app.get("is_overdue") else ""
        priority_flag = " 🔴 PRIORITY" if app.get("priority_flag") else ""

        applicant_rows = ""
        if "applicant" in app and app["applicant"]:
            applicant = app["applicant"]
            applicant_rows = (
                f"<tr><td><strong>{t['applicant_name']}</strong></td><td>{_e(applicant.get('name'))}</td></tr>"
                f"<tr><td><strong>{t['mobile']}</strong></td><td>{_e(applicant.get('mobile'))}</td></tr>"
                f"<tr><td><strong>{t['email']}</strong></td><td>{_e(applicant.get('email') or 'N/A')}</td></tr>"
                f"<tr><td><strong>{t['address']}</strong></td><td>{_e(applicant.get('address') or 'N/A')}</td></tr>"
            )

        submission_channel = app.get("submission_channel")
        if submission_channel:
            submission_channel_display = t.get(submission_channel, _e(submission_channel))
        else:
            submission_channel_display = "Not specified" if lang == "en" else "குறிப்பிடப்படவில்லை"

        optional_rows = ""
        if submission_channel:
            optional_rows += f"<tr><td><strong>{t['submitted_via']}</strong></td><td>{submission_channel_display}</td></tr>"
        if app.get("sale_deed_number"):
            optional_rows += f"<tr><td><strong>{t['sale_deed_number']}</strong></td><td>{_e(app.get('sale_deed_number'))}</td></tr>"
            optional_rows += f"<tr><td><strong>{t['sale_deed_registered']}</strong></td><td>{t['yes'] if app.get('sale_deed_registered') else t['no']}</td></tr>"
        if app.get("declared_reason"):
            optional_rows += f"<tr><td><strong>{t['declared_reason']}</strong></td><td>{_reason(app.get('declared_reason'))}</td></tr>"

        # ── MERGE: build subdivisions block ──────────────────────────
        merge_info_html = ""
        if app.get("type") == "MERGE":
            subdivisions = app.get("subdivisions_being_merged") or []
            survey_no    = app.get("survey_no") or "-"
            if subdivisions:
                total_area = app.get("total_merge_area_sqm", 0)
                # Each sub-div on its own line with area
                subdiv_rows_html = "".join(
                    (
                        f"<div style='margin:3px 0;'>• {_e(sd['sub_division_no'])}"
                        f" — {sd['area_sqm']:.2f} sq.m</div>"
                    ) if sd.get('area_sqm') else (
                        f"<div style='margin:3px 0;'>• {_e(sd['sub_division_no'])}</div>"
                    )
                    for sd in subdivisions
                )
                total_area_str = f"{total_area:.2f} sq.m" if total_area else "-"
                merge_info_html = (
                    f"<tr><td colspan='2' style='background:#f0f8ff;padding:10px;"
                    f"border-left:4px solid #0066cc;'>"
                    f"<strong>📍 {t['survey_no']}:</strong> {_e(survey_no)}<br><br>"
                    f"<strong>📋 {t['subdivisions_being_merged']} ({len(subdivisions)}):</strong><br>"
                    f"{subdiv_rows_html}"
                    f"<br><strong>📊 {t['total_merge_area']}:</strong> {total_area_str}"
                    f"</td></tr>"
                )
            else:
                # No subdivisions linked yet — still show survey number
                merge_info_html = (
                    f"<tr><td><strong>📍 {t['survey_no']}</strong></td>"
                    f"<td>{_e(survey_no)}</td></tr>"
                    f"<tr><td><strong>{t['subdivisions_being_merged']}</strong></td>"
                    f"<td>Not yet assigned</td></tr>"
                )

        field_visit_rows = ""
        if "field_visit" in app and app["field_visit"]:
            fv = app["field_visit"]
            fv_status = _status(fv.get("status"), lang)
            field_visit_rows = f"<tr><td><strong>{t['field_visit_status']}</strong></td><td>{fv_status}</td></tr>"
            if fv.get("scheduled_date"):
                field_visit_rows += f"<tr><td><strong>{t['scheduled_date']}</strong></td><td>{_e(fv.get('scheduled_date'))}</td></tr>"
            if fv.get("actual_date"):
                field_visit_rows += f"<tr><td><strong>{t['actual_visit_date']}</strong></td><td>{_e(fv.get('actual_date'))}</td></tr>"
            if fv.get("status") == "completed":
                field_visit_rows += f"<tr><td><strong>{t['encroachment_found']}</strong></td><td>{t['yes'] if fv.get('encroachment_found') else t['no']}</td></tr>"
                field_visit_rows += f"<tr><td><strong>{t['area_verified']}</strong></td><td>{t['yes'] if fv.get('area_verified') else t['no']}</td></tr>"

        # For MERGE apps put the merge block (survey + subdivisions) FIRST,
        # then status/stage, then applicant contact, then optional rows.
        # For non-MERGE apps keep the original order.
        is_merge_app = app.get("type") == "MERGE"

        if is_merge_app:
            body_rows = (
                f"{merge_info_html}"
                f"<tr><td><strong>{t['type']}</strong></td><td>{_e(app.get('type'))}</td></tr>"
                f"<tr><td><strong>{t['status']}</strong></td><td>{_status(app.get('status'), lang)}</td></tr>"
                f"<tr><td><strong>{t['stage']}</strong></td><td>{_e(app.get('stage'))}</td></tr>"
                f"<tr><td><strong>{t['submission_date']}</strong></td><td>{_e(app.get('submission_date'))}</td></tr>"
                f"{optional_rows}"
                f"{applicant_rows}"
                f"{field_visit_rows}"
            )
        else:
            survey_row = (
                f"<tr><td><strong>{t['survey_number']}</strong></td><td>{_e(app.get('survey_no'))}</td></tr>"
                if app.get("survey_no") else ""
            )
            body_rows = (
                f"{applicant_rows}"
                f"<tr><td><strong>{t['type']}</strong></td><td>{_e(app.get('type'))}</td></tr>"
                f"<tr><td><strong>{t['status']}</strong></td><td>{_status(app.get('status'), lang)}</td></tr>"
                f"<tr><td><strong>{t['stage']}</strong></td><td>{_e(app.get('stage'))}</td></tr>"
                f"<tr><td><strong>{t['submission_date']}</strong></td><td>{_e(app.get('submission_date'))}</td></tr>"
                f"{survey_row}"
                f"{optional_rows}"
                f"{field_visit_rows}"
            )

        return (
            f"<div class='table-intro'><strong>{t['application_details']}: {_e(app['application_number'])}</strong>{overdue_flag}{priority_flag}</div>"
            f"<table class='data-table'>"
            f"<thead><tr><th>{t['field']}</th><th>{t['value']}</th></tr></thead>"
            f"<tbody>"
            f"{body_rows}"
            f"</tbody>"
            f"</table>"
        )

    # ── Officer workload ─────────────────────────────────────────────
    if "workload" in structured_data or "total_applications" in structured_data:
        d = structured_data
        return (
            f"<div class='table-intro'><strong>{t['workload_summary']}</strong></div>"
            f"<table class='data-table'>"
            f"<thead><tr><th>{t['metric']}</th><th>{t['count']}</th></tr></thead>"
            f"<tbody>"
            f"<tr><td>{t['total_applications']}</td><td>{_e(d.get('total_applications'))}</td></tr>"
            f"<tr><td>{t['pending_count']}</td><td>{_e(d.get('pending_count'))}</td></tr>"
            f"<tr><td>{t['overdue_count']}</td><td>{_e(d.get('overdue_count'))}</td></tr>"
            f"<tr><td>{t['unscheduled_visits']}</td><td>{_e(d.get('unscheduled_visits'))}</td></tr>"
            f"</tbody>"
            f"</table>"
        )

    # ── Unscheduled field visits ─────────────────────────────────────
    if "visits" in structured_data and isinstance(structured_data["visits"], list):
        visits = structured_data["visits"]
        if not visits:
            return f"<div>{t['no_records_found']}</div>"
        rows = "".join(
            f"<tr>"
            f"<td>{_e(v.get('application_number'))}</td>"
            f"<td>{_e(v.get('type'))}</td>"
            f"<td>{_status(v.get('status'), lang)}</td>"
            f"<td>{_e(v.get('stage'))}</td>"
            f"<td>{_e(v.get('submission_date'))}</td>"
            f"<td>{_e(v.get('days_since_submission'))} {'days' if lang == 'en' else 'நாட்கள்'}</td>"
            f"</tr>"
            for v in visits
        )
        return (
            f"<div class='table-intro'>{t['found']} <strong>{len(visits)}</strong> "
            f"{t['unscheduled_field_visits']}:</div>"
            f"<table class='data-table'>"
            f"<thead><tr>"
            f"<th>{t['application_no']}</th><th>{t['type']}</th><th>{t['status']}</th>"
            f"<th>{t['stage']}</th><th>{t['submitted']}</th><th>{t['days_pending']}</th>"
            f"</tr></thead>"
            f"<tbody>{rows}</tbody>"
            f"</table>"
        )

    # ── Field Visits (overdue, scheduled, all) ───────────────────────
    if "field_visits" in structured_data and isinstance(structured_data["field_visits"], list):
        field_visits = structured_data["field_visits"]
        query_type = structured_data.get("query_type", "Field Visits")
        start_date = structured_data.get("start_date")
        end_date = structured_data.get("end_date")
        to_be_visited_count = structured_data.get("to_be_visited_count")
        
        date_info = f" ({start_date} to {end_date})" if (start_date and end_date) else ""
        
        if to_be_visited_count is not None and structured_data.get("to_be_visited_only"):
            count_info = f"Found <strong>{to_be_visited_count}</strong> field visit(s) needed to be visited"
        else:
            count_info = f"Found <strong>{len(field_visits)}</strong> field visit(s)"

        if not field_visits:
            return f"<div class='table-intro'><strong>{query_type}</strong>{date_info}: No field visits found.</div>"
        
        rows = "".join(
            f"<tr>"
            f"<td>{_e(fv.get('application_number'))}</td>"
            f"<td>{_e(fv.get('survey_no'))}</td>"
            f"<td>{_e(fv.get('block_number'))}</td>"
            f"<td>{_e(fv.get('application_type'))}</td>"
            f"<td>{_status(fv.get('status'), lang)}{' ⚠️' if fv.get('is_overdue') else ''}</td>"
            f"<td>{_e(fv.get('field_visit_date') or 'Not Scheduled')}</td>"
            f"</tr>"
            for fv in field_visits
        )
        
        return (
            f"<div class='table-intro'><strong>{query_type}</strong>{date_info}: {count_info}</div>"
            f"<table class='data-table'>"
            f"<thead><tr>"
            f"<th>Application Number</th><th>Survey Number</th><th>Block</th>"
            f"<th>Type</th><th>Status</th><th>Scheduled Date</th>"
            f"</tr></thead>"
            f"<tbody>{rows}</tbody>"
            f"</table>"
        )

    # No matching handler — caller falls back to LLM
    return ""


def build_prompt(
    query: str,
    context: str,
    structured_data: Dict[str, Any],
    language: str,
    chat_history: list = None,
    direct_answer: bool = False
) -> str:
    """
    Build the LLM prompt. Only called when build_html_response returned "".
    structured_data is summarised as plain text (no duplicate table logic).

    Args:
        query: User's question
        context: Retrieved RAG context
        structured_data: Structured data from database queries
        language: Detected language ("en", "ta", or "tanglish")
        chat_history: List of previous messages for conversation context
        direct_answer: When True, instruct LLM to answer the question directly
                       from the data rather than presenting a generic summary.
    """
    language_instruction = {
        "en": "CRITICAL: You MUST respond in English language only.",
        "ta": "CRITICAL: You MUST respond in Tamil language only.",
        "tanglish": "CRITICAL: You MUST respond in the same mixed Tamil-English style (Tanglish) that the user used."
    }.get(language, "CRITICAL: You MUST respond in English language only.")
    
    # Auto-detect if this is a specific field query that needs direct answer
    query_lower = query.lower()
    field_query_keywords = [
        "name", "பெயர்", "applicant name", "விண்ணப்பதாரர் பெயர்",
        "mobile", "phone", "தொலைபேசி", "எண்",
        "email", "மின்னஞ்சல்",
        "address", "முகவரி",
        "status", "நிலை",
        "stage", "கட்டம்",
        "date", "தேதி",
        "survey number", "கணக்கெண்",
    ]
    
    is_specific_field_query = any(kw in query_lower for kw in field_query_keywords) and \
                              any(w in query_lower for w in ["what", "என்ன", "யார்", "who", "எது", "which", "எப்போது", "when"])
    
    # When the caller bypassed the HTML table path (interrogative queries),
    # we inject a focused instruction so the LLM directly answers the question
    # instead of producing a generic summary.
    direct_answer_instruction = ""
    if direct_answer or is_specific_field_query:
        direct_answer_instruction = """
IMPORTANT — DIRECT ANSWER MODE:
The user asked a specific question about a particular field or detail. Answer ONLY that question using the
structured data provided below. Do NOT summarise all fields. Do NOT produce a table. 
Write 1-2 plain sentences that directly answer what was asked.

Examples of GOOD direct answers:
- Q: "What is the applicant name?" / "விண்ணப்பதாரர் பெயர் என்ன?"
  A: "The applicant name for APP-2024-000001 is Applicant 1." / "APP-2024-000001 விண்ணப்பதாரர் பெயர் Applicant 1."

- Q: "Which sub-divisions are included?"
  A: "Merge application APP-2024-000004 includes 3 sub-divisions: 145/1A (600.00 sq.m), 145/1B (700.00 sq.m), and 145/1C (650.00 sq.m)."

- Q: "What is the status?" / "நிலை என்ன?"
  A: "The application status is Pending." / "விண்ணப்பம் நிலுவையில் உள்ளது."

Example of BAD answer (do NOT do this):
  "Here are the details for APP-2024-000004. Type: MERGE, Status: Approved, Applicant Name: John..."
"""

    system_instruction = f"""{language_instruction}{direct_answer_instruction}

You are a friendly AI assistant for Sub Inspector Surveyor (SIS) officers of Tamil Nadu.

Your responsibilities:
- Help SIS officers with surveys, applications, field visits, and workflow procedures.
- Provide accurate information using the database and knowledge base provided to you.
- NEVER invent, assume, or generate any data that is not explicitly provided in the system data.
- NEVER mention technical terms like "RAG", "context", "database", "system", or "knowledge base" to users.
- Be conversational and helpful, using natural language without exposing technical details.
- If asked about something you don't have information on, simply say you don't have that information.

CRITICAL APPLICATION TYPE DEFINITIONS:
1. **ISD (Involving Sub-Division)**: Service Code `0154` — Creates NEW sub-divisions when dividing land into smaller parcels
   - Example: Survey 145 (1000 sq.m) → 145/1 (600 sq.m) + 145/2 (400 sq.m)
   - Requires field visit and new sub-division numbering
   
2. **NISD (Not Involving Sub-Division)**: Service Code `0153` — Only transfers ownership, NO sub-divisions created
   - Survey number and boundaries remain unchanged
   - Only patta holder name changes
   
3. **MERGE**: Combines multiple sub-divisions or surveys into ONE survey number
   - Example: 145/1 (300 sq.m) + 145/2 (400 sq.m) → Survey 145 (700 sq.m)
   - Reduces the number of separate parcels
   - Lists which sub-divisions are being merged together

CONVERSATION CONTEXT:
- You have access to previous messages in this conversation.
- Use the conversation history to understand context and answer follow-up questions.
- If the user refers to something from a previous message (like "that application" or "the survey I mentioned"),
  use the conversation history to identify what they're referring to.
- Maintain conversation continuity while staying within SIS domain.

HANDLING DIFFERENT TYPES OF QUERIES:
1. **Greetings and casual conversation** (hi, hello, how are you, thanks, etc.):
   - Respond warmly and briefly
   - Remind them of what you can help with
2. **General questions about your capabilities**:
   - Explain what you can do clearly
   - Mention surveys, applications (ISD/NISD/MERGE), field visits, workflow procedures
3. **SIS-specific queries with no data found**:
   - Explain that you don't have that specific information
   - Suggest what they can ask about instead
4. **SIS-specific queries with data**:
   - If in DIRECT ANSWER MODE: answer only what was asked in plain sentences.
   - Otherwise: present the data clearly and be concise and professional.

STRICT DATA RULES:
1. DO NOT generate example tables, field descriptions, or placeholder data.
2. DO NOT explain what an ISD/NISD/MERGE application "contains" unless you have actual information.
3. DO NOT say "the following information is available for..." — only show actual data.
4. DO NOT use markdown tables (| --- |) — only plain text or HTML <table> tags.
5. If specific data is not available, acknowledge it naturally without mentioning technical systems.
6. ALWAYS use the correct definitions: ISD creates sub-divisions, NISD does not, MERGE combines sub-divisions.
7. NEVER mention "RAG context", "database", "knowledge base", "system data" or any technical terms in responses to users.

When answering questions:
- Use natural, conversational language
- Present information as if you know it directly, not as if you're reading from a database
- If you don't have information, say "I don't have that information" not "The database doesn't contain..."
- Example GOOD: "Application APP-2024-000001 is currently at the Sub Inspector Surveyor stage."
- Example BAD: "According to the RAG context, the application is at SIS stage."

When specific document knowledge IS provided:
- Answer questions about procedures, rules, and workflow naturally
- Present information conversationally without mentioning sources

When application data IS provided:
- In DIRECT ANSWER MODE: answer the specific question only, in plain prose.
- Otherwise: present it clearly and concisely."""

    # Build conversation history section
    history_section = ""
    if chat_history and len(chat_history) > 0:
        history_lines = ["\n\nCONVERSATION HISTORY (for context):"]
        for msg in chat_history[-10:]:  # Last 10 messages max
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if len(content) > 500:
                content = content[:500] + "... [truncated]"
            history_lines.append(f"{role.upper()}: {content}")
        history_section = "\n".join(history_lines)

    structured_section = format_structured_data_for_llm(structured_data)
    context_section = f"\n\nRELEVANT DOCUMENT CONTEXT:\n{context}" if context else ""

    return (
        f"{system_instruction}"
        f"{history_section}"
        f"{structured_section}"
        f"{context_section}"
        f"\n\nUSER QUESTION:\n{query}"
        f"\n\nASSISTANT RESPONSE:"
    )


async def call_llama(prompt: str) -> str:
    """Call Llama model via Ollama (non-streaming)."""
    try:
        logger.info("Calling Ollama LLM...")
        response = llm.invoke(prompt)
        response_text = response.content if hasattr(response, "content") else str(response)
        response_text = response_text.strip()
        logger.info(f"LLM response generated ({len(response_text)} chars)")
        return response_text
    except Exception as e:
        logger.error(f"Error calling LLM: {e}")
        return "I apologize, but I encountered an error processing your request. Please try again."


async def call_llama_stream(prompt: str):
    """Call Llama model via Ollama with streaming."""
    try:
        logger.info("Calling Ollama LLM with streaming...")
        chunk_count = 0
        total_content = ""
        async for chunk in llm.astream(prompt):
            chunk_count += 1
            content = chunk.content if hasattr(chunk, "content") else str(chunk)
            if content:
                total_content += content
                yield content
            if chunk_count % 10 == 0:
                logger.debug(f"Streamed {chunk_count} chunks, {len(total_content)} chars so far")
        logger.info(f"LLM streaming complete: {chunk_count} chunks, {len(total_content)} total chars")
        if not total_content:
            logger.error("WARNING: LLM returned empty response!")
            yield "I apologize, but I received an empty response. Please try again."
    except Exception as e:
        logger.error(f"Error in LLM stream: {e}", exc_info=True)
        yield "I apologize, but I encountered an error processing your request. Please try again."


# ─────────────────────────────────────────────────────────────────────────────
# parse_intent — specific patterns before broad ones
#   "show me survey 145"  → survey_detail  (not all_surveys_in_jurisdiction)
#   "show all surveys"    → all_surveys_in_jurisdiction
# ─────────────────────────────────────────────────────────────────────────────
def parse_intent(message: str) -> str:
    """
    Parse user intent from message using exact token-boundary and production edit-distance matching.
    Supports English, Tamil, and Tanglish. Deterministic identifiers use exact token bounds;
    natural language questions without deterministic keywords gracefully fall through to 'general_query'
    for semantic LLM / RAG processing.
    """
    # Strip leading list-item prefixes like "1.", "2)", "a-" etc.
    message = re.sub(r'^\s*\d+[\.\)\-]\s*', '', message)
    message = re.sub(r'^\s*[a-zA-Z][\.\)\-]\s*', '', message)

    msg = normalize_text(message)
    words = extract_tokens(msg)

    def fuzzy_match(keyword: str, threshold: float = 0.82) -> bool:
        """
        Token-boundary matching: exact token match or edit-distance typo match on tokens.
        Does NOT match arbitrary substrings across token boundaries.
        """
        kw_norm = normalize_text(keyword)
        if not kw_norm or not words:
            return False

        # Exact token match (word boundary strict)
        if kw_norm in words:
            return True

        # For tokens length < 4 or containing Tamil Unicode, require exact token match
        if len(kw_norm) < 4 or any('\u0B80' <= c <= '\u0BFF' for c in kw_norm):
            return False

        # Edit distance typo match against individual words
        for word in words:
            if len(word) >= 4 and is_token_typo_match(word, kw_norm):
                return True

        return False

    def has(keywords: list) -> bool:
        return any(fuzzy_match(kw) for kw in keywords)

    # ── keyword sets (English + Tamil) ───────────────────────────────
    ta_survey      = ["கணக்கெண்", "கணக்கு", "survey", "surveys", "survay", "suvery"]
    ta_subdivision = ["உட்பிரிவு", "உட்பிரிவுகள்", "subdivision", "subdivisions",
                      "sub-division", "sub-divisions", "subdiv"]
    ta_show        = ["காட்டு", "காண்பி", "பட்டியல்", "அனைத்தும்",
                      "show", "list", "all", "my", "display", "get"]
    ta_owner       = ["உரிமையாளர்", "சொந்தக்காரர்", "owner", "owners",
                      "ownership", "patta", "pattadar"]
    ta_pending     = ["நிலுவை", "நிலுவையில்", "காத்திருக்கும்",
                      "pending", "waiting", "pendig", "pendng"]
    ta_overdue     = ["காலதாமத", "காலதாமதமான", "தாமதம்",
                      "overdue", "late", "delayed", "overdew", "overdu"]
    ta_field_visit = ["கள ஆய்வு", "களஆய்வு", "வருகை",
                      "field", "visit", "visits", "scheduling", "schedule", "feild"]
    ta_workload    = ["பணிச்சுமை", "workload", "worklod", "work load"]
    ta_application = ["விண்ணப்பம்", "விண்ணப்பங்கள்",
                      "application", "applications", "aplications", "aplication",
                      "app", "appl", "apps"]
    ta_merge       = ["இணைப்பு", "இணைக்க", "இணைக்கப்பட்ட", "இணைப்பு விண்ணப்பம்",
                      "இணைப்பு விண்ணப்பங்கள்", "இணைத்தல்",
                      "merge", "merging", "merged", "merg"]
    ta_status      = ["நிலை", "status", "statuss", "staus"]
    ta_area        = ["பரப்பளவு", "பரப்பு", "area", "arrea"]
    ta_ward        = ["வார்டு", "ward", "wards"]
    ta_block       = ["தொகுதி", "block", "blocks"]
    ta_next        = ["அடுத்த", "கிடைக்கும்", "next", "available", "nxt"]
    ta_detail      = ["விவரம்", "விவரங்கள்", "detail", "details", "info",
                      "contain", "included", "which", "what", "how", "land"]

    # ── Greeting queries ──
    _greetings_exact = [
        "hi", "hello", "hey", "good morning", "good afternoon", "good evening",
        "vanakkam", "வணக்கம்", "காலை வணக்கம்", "மாலை வணக்கம்", "ஹாய்",
        "நன்றி", "thanks", "thank you", "how are you", "எப்படி இருக்கீங்க"
    ]
    clean_msg = msg.strip().rstrip("!.,")
    if (clean_msg in _greetings_exact or
        any(clean_msg.startswith(g) for g in ["வணக்கம்", "காலை வணக்கம்", "மாலை வணக்கம்", "good morning", "good evening", "good afternoon"])) and len(words) <= 5:
        if not any(w in msg for w in ["app-", "application", "survey", "145", "146", "147", "148", "status", "stage", "விண்ணப்பம்", "கணக்கெண்"]):
            return "greeting"

    # ── Document / file upload queries — catch before DB intent routing ──
    # Phrases that mean "I uploaded a file, help me with it" should never
    # be routed to survey/application DB queries.
    _doc_phrases = [
        "uploaded", "word document", "pdf document", "question bank",
        "answer all", "answer for all", "from the document", "in the document",
        "the file", "attached file", "from this file",
    ]
    if any(ph in msg for ph in _doc_phrases):
        return "general_query"
    if "sd" in msg:
        if any(w in msg for w in ["additional", "asking for", "requested", "information", "missing",
                                   # Tamil: கூடுதல் தகவல், கோரப்பட்டது
                                   "கூடுதல்", "கோரப்பட்டது", "தேவையான தகவல்"]):
            return "sd_additional_info"
        if any(w in msg for w in ["encroachment", "flag", "receive", "noted",
                                   # Tamil: ஆக்கிரமிப்பு
                                   "ஆக்கிரமிப்பு"]):
            return "sd_encroachment_check"
        if any(w in msg for w in ["complete", "sketch", "field data", "readiness",
                                   # Tamil: வரைபடம், தயாரிப்பு
                                   "வரைபடம்", "தயாரிப்பு", "முடிந்தது"]):
            return "sd_sketch_readiness"
        if any(w in msg for w in ["forward", "forwarded", "sent to",
                                   # Tamil: அனுப்பப்பட்டது
                                   "அனுப்பப்பட்டது", "அனுப்பியது", "பகிரப்பட்டது"]):
            return "sd_forward_check"
        if any(w in msg for w in ["remark", "remarks", "comment", "recorded",
                                   # Tamil: கருத்து, குறிப்பு
                                   "கருத்து", "குறிப்பு", "பதிவு"]):
            return "sd_remarks"

    # ── Escalation check BEFORE field-visit block ─────────────────────────────
    # Must come first so "காலக்கெடு வரம்பு விண்ணப்பங்கள்" → escalation_check
    # and NOT get consumed by the FV block's deadline inner check.
    if any(w in msg for w in ["escalat", "threshold", "approaching deadline",
                               "deadline this week", "approaching threshold",
                               "due this week", "close to deadline", "near deadline",
                               "எஸ்கலேஷன்", "காலக்கெடு நெருங்கு", "காலக்கெடு அணுகு",
                               "காலக்கெடு வரம்பு", "மேல்முறையீடு வரம்பு",
                               "நெருங்கும் காலக்கெடு"]):
        return "escalation_check"

    # Field visit specific workflow intents (check before general field_visits)
    # Match if message contains field visit keywords (as phrase OR separate words)
    has_field_visit_keywords = (
        any(w in msg for w in ["field visit", "inspection", "schedule", "calendar", "visit date",
                               "deadline", "15-working-day", "15 working day", "15-day", "working day",
                               # Tamil field visit keywords
                               "கள ஆய்வு", "களஆய்வு", "வருகை", "கள பார்வை",
                               # Tamil scheduling keywords
                               "திட்டமிட", "திட்டமிடப்பட", "திட்டமிடப்படாத",
                               # Tamil deadline keywords (outer trigger so inner checks fire)
                               "காலக்கெடு", "கடந்து விட்டதா", "கடந்துவிட்டதா",
                               "நேர வரம்பு", "15 நாட்கள்",
                               # Display/show keywords (Tamil + English)
                               "display", "காட்டு", "காண்பி", "பட்டியல்"])
        or (("field" in msg or "visit" in msg or "inspection" in msg) and 
            not any(w in msg for w in ["application", "survey number"]))
    )
    
    if has_field_visit_keywords:
        if any(w in msg for w in ["between date", "between dates", "between", "needed to be visited",
                                   "to be visited", "need to be visited", "visits between", "from date",
                                   "date range", "தேதிகளுக்கு இடையே", "தேதி வரம்பு"]):
            return "fv_between_dates"

        if any(w in msg for w in ["date did i select", "select for this", "what date", "which date",
                                   # Tamil: எந்த தேதி
                                   "எந்த தேதி", "தேர்ந்தெடுத்த தேதி"]):
            return "fv_date_select"
        if any(w in msg for w in ["nearby", "close by", "location", "neighborhood",
                                   # Tamil: அருகில்
                                   "அருகில்", "பக்கத்தில்"]) or \
           (any(w in msg for w in ["same ward", "அதே வார்டு"]) and 
            any(w in msg for w in ["field visit", "inspection", "கள ஆய்வு", "வருகை"])):
            return "fv_nearby_pending"
        if any(w in msg for w in ["already have scheduled", "scheduled in this", "scheduled this week",
                                   # Tamil: இந்த வாரம் திட்டமிடப்பட்டது
                                   "இந்த வாரம் திட்டமிடப்பட்ட", "இந்த வாரம்"]) or \
           ("scheduled" in msg and any(w in msg for w in ["this week", "this taluk", "in this",
                                                            "இந்த வாரம்", "இந்த தாலுகா"])):
            if "conflict" not in msg and "reschedule" not in msg and "overdue" not in msg and "unassigned" not in msg:
                return "fv_scheduled_this_week"
        if any(w in msg for w in ["recently rescheduled", "last 7 days", "rescheduled during",
                                   # Tamil: சமீபத்தில் மாற்றப்பட்டது
                                   "சமீபத்தில் மாற்றப்பட்ட", "கடந்த 7 நாட்கள்"]):
            return "fv_recently_rescheduled"
        if any(w in msg for w in ["reschedule", "availability", "rescheduling",
                                   # Tamil: மீண்டும் திட்டமிடு, கிடைப்பு நேரம்
                                   "மீண்டும் திட்டமிடு", "மீண்டும் திட்டமிட", "கிடைக்கும் நேரம்",
                                   # Tanglish: schedule பண்ண / செய்ய = reschedule intent
                                   "schedule பண்ண", "schedule செய்ய", "புதிய தேதி"]):
            return "fv_reschedule_availability"
        if any(w in msg for w in ["deadline", "15-working-day", "15 working day", "15-day",
                                   "past the", "already past", "exceeded the deadline", "within the window",
                                   "working day", "working-day", "day limit",
                                   # Tamil: காலக்கெடு, 15 நாட்கள், கடந்து விட்டதா
                                   "காலக்கெடு", "15 நாட்கள்", "கடந்து விட்டதா", "நேர வரம்பு",
                                   "கடந்துவிட்டதா", "கடந்து விட்டது", "கடந்துவிட்டது"]):
            return "fv_deadline_check"
        
        # --- ADDED: Specific overdue field visit patterns (check FIRST) ---
        if any(ph in msg for ph in [
            "overdue field", "field visit overdue", "field visits overdue",
            "overdue visit", "overdue visits",
            "overdue inspection", "past due visit", "missed visit",
            "show overdue field", "show overdue visit", "show overdue visits",
            "கால தாமதமான கள", "தாமதமான கள ஆய்வு"
        ]):
            return "fv_overdue_inspections"
        
        # --- CHANGED: Check for overdue FIELD VISITS before generic overdue ---
        _is_overdue = any(w in msg for w in ["overdue", "late", "delayed", "காலதாமதமான"])
        _is_field_visit = any(w in msg for w in ["field visit", "field visits", "visit", "visits", "inspection", 
                                                   "கள ஆய்வு", "கள்ஆய்வு", "ஆய்வு"])
        
        if _is_overdue and _is_field_visit:
            return "fv_overdue_inspections"
        # --- END ADDED ---
        
        if any(w in msg for w in ["unassigned", "not yet been assigned",
                                   "awaiting scheduling", "awaiting schedule",
                                   "no schedule", "without schedule",
                                   # Tamil — திட்டமிடப்படாத (unscheduled/unassigned)
                                   "திட்டமிடப்படாத", "திட்டமிடப்படவில்லை",
                                   "நிலுவையில் உள்ள கள ஆய்வு",
                                   "கால அட்டவணை இல்லாத"]):
            return "fv_unassigned_awaiting"
        if any(w in msg for w in ["conflict", "conflicts", "overlap",
                                   # Tamil: முரண்பாடு
                                   "முரண்பாடு", "மோதல்", "ஒன்றிணைவு"]):
            return "fv_scheduling_conflicts"

    # 1a. Standalone Tamil/English unassigned field visit check
    # Catches pure-Tamil queries that may not have English field-visit trigger words
    if any(w in msg for w in ["திட்டமிடப்படாத", "திட்டமிடப்படவில்லை", "கால அட்டவணை இல்லாத",
                               "நிலுவையில் உள்ள கள ஆய்வு"]):
        return "fv_unassigned_awaiting"

    # 3. Escalation — check BEFORE field-visit block so "காலக்கெடு வரம்பு" routes here
    if any(w in msg for w in ["escalat", "threshold", "approaching deadline",
                               "deadline this week", "approaching threshold",
                               "due this week", "close to deadline", "near deadline",
                               # Tamil: எஸ்கலேஷன், காலக்கெடு நெருங்ுகிறது
                               "எஸ்கலேஷன்", "காலக்கெடு நெருங்கு", "காலக்கெடு அணுகு",
                               "காலக்கெடு வரம்பு", "மேல்முறையீடு வரம்பு",
                               "நெருங்கும் காலக்கெடு"]):
        return "escalation_check"

    # 1a2. Standalone Tamil application list — catch before FV outer block consumes காட்டு/பட்டியல்
    # "விண்ணப்பங்கள் பட்டியல் காட்டு" should be pending_applications not general_query
    # Exclude merge queries: "இணைப்பு விண்ணப்பங்கள் காட்டு" should still go to merge_applications
    # ALSO exclude specific field queries: "விண்ணப்பதாரர் பெயர் என்ன" should go to application_status
    if any(w in msg for w in ["விண்ணப்பங்கள்", "விண்ணப்பங்களும்", "விண்ணப்பம்", "விண்ணப்பங்கள"]) and \
       any(w in msg for w in ["காட்டு", "பட்டியல்", "காண்பி", "list", "show"]) and \
       not any(w in msg for w in ["கள ஆய்வு", "களஆய்வு", "வருகை", "field", "visit",
                                   "இணைப்பு", "இணைக்க", "merge"]) and \
       not any(w in msg for w in ["பெயர்", "நாமாகும்", "நாமம்", "என்ன", "எது", "யார்", "எங்கே", "எப்போது",
                                   "தொலைபேசி", "மின்னஞ்சல்", "முகவரி", "நிலை", "கட்டம்"]) and \
       not any(w in msg for w in ["முன்னுரிமை", "முன்னதாய", "அதிக", "உயர்ந்த",
                                   "அவசர", "அவசரமான",
                                   "நிலுவை", "காலதாமதமான", "தாமதம்", "overdue", "priority"]):
        return "pending_applications"

    # 1. Joint owner check - MUST come before application_status to catch ownership questions
    # Otherwise "APP-2024-000001 is the applicant the sole owner?" → application_status (wrong)
    if any(w in msg for w in ["joint owner", "joint owners", "co-owner", "co owner",
                               "multiple owner", "shared ownership", "sole owner",
                               # Tamil
                               "கூட்டு உரிமையாளர்", "கூட்டுரிமையாளர்", "கூட்டு உரிமை",
                               "இணை உரிமையாளர்", "பல உரிமையாளர்",
                               "ஒரே உரிமையாளர்", "ஒற்றை உரிமையாளர்",
                               "உரிமையாளர்கள்", "உரிமையாளர்",
                               # Tanglish
                               "kootu urimaiyalar", "koottu", "sole urimaiyalar",
                               "urimaiyalar", "urimayalar"]):
        return "joint_owner_check"

    # 2. Application number pattern → application_status
    if re.search(r'\b(?:ISD|NISD|MERGE)/\w+/\d+/\d+\b|\bAPP-\d+-\d+\b', message, re.IGNORECASE):
        return "application_status"

    # 2-priority. Highest priority — check EARLY to avoid application_status false match
    # "Show high priority applications" has "show" + "applications" + "priority"
    # Priority check must come BEFORE application_status field check (which includes "show" in interrogatives)
    if "priority" in msg and ("week" in msg or "highest" in msg or "high" in msg or "show" in msg or "list" in msg):
        return "highest_priority_applications"
    # Tamil: முன்னுரிமை, முன்னதாய, அவசர
    if any(w in msg for w in ["முன்னுரிமை", "முன்னதாய", "அவசர", "அவசரமான"]) and any(w in msg for w in ["உயர்ந்த", "அதிக", "இந்த வாரம்", "காட்டு", "பட்டியல்", "விண்ணப்பம்", "விண்ணப்பங்கள்"]):
        return "highest_priority_applications"

    # 2a. Field-specific queries (name, address, mobile, email, etc.) → application_status
    # These queries ask about specific applicant/application fields
    _field_keywords = [
        # English
        "name", "address", "mobile", "phone", "email", "status", "stage", "type",
        "applicant", "contact", "priority", "aadhaar", "date", "submission",
        # Tamil
        "பெயர்", "நாமாகும்", "நாமம்", "முகவரி", "தொலைபேசி", "மின்னஞ்சல்",
        "நிலை", "கட்டம்", "வகை", "விண்ணப்பதாரர்", "தேதி",
        # Tanglish
        "peyar", "mugavari", "tholaipaesi", "minnanjal", "nilai", "kattam"
    ]
    _interrogative = ["what", "which", "who", "where", "when", "give", "tell", "show",
                      "என்ன", "எந்த", "யார்", "எங்கே", "எப்போது", "காட்டு", "சொல்"]
    has_field = any(kw in msg for kw in _field_keywords)
    has_interrogative = any(kw in msg for kw in _interrogative)
    has_application_context = any(w in msg for w in ["application", "applicant", "விண்ணப்பம்", "விண்ணப்பதாரர்"])
    # Route to application_status if asking about a field + has interrogative OR mentions application
    if has_field and (has_interrogative or has_application_context):
        return "application_status"

    # 2b. "Where is this application" / "which department" → application_status
    if any(p in msg for p in [
        "where is this application", "where is the application",
        "which department", "with sd", "with dis", "with tahsildar",
        "current stage", "what stage", "which stage", "which office",
        "application right now", "right now", "currently at", "currently with",
        # Tamil: விண்ணப்பம் இப்போது எங்கே, எந்த கட்டத்தில்
        "இந்த விண்ணப்பம் எங்கே", "எந்த நிலையில்",
        "இப்போது எங்கே", "எந்த அலுவலகத்தில்", "எந்த கட்டத்தில்",
        "எங்கே உள்ளது", "இப்போது யாரிடம்",
    ]) and any(w in msg for w in ["application", "விண்ணப்பம்", "sd", "dis", "tahsildar",
                                   "அலுவலகம்", "அலுவலகத்தில்", "கட்டம்", "உள்ளது"]):
        return "application_status"
    # Also catch: "application எந்த stage-ல் இருக்கு" style (Tanglish with stage keyword)
    if any(w in msg for w in ["application", "விண்ணப்பம்"]) and \
       any(w in msg for w in ["stage", "கட்டம்", "நிலை", "எங்கே", "அலுவலகம்", "அலுவலகத்தில்",
                               "யாரிடம்", "எந்த"]) and \
       not any(w in msg for w in ["pending", "overdue", "list", "show", "காட்டு", "பட்டியல்"]):
        return "application_status"
    # Pure Tamil location query without explicit "application" word
    if any(w in msg for w in ["எந்த அலுவலகத்தில்", "எந்த கட்டத்தில்", "இப்போது யாரிடம்",
                               "எங்கே உள்ளது"]):
        return "application_status"

    # 3. Overdue
    if has(ta_overdue):
        return "overdue_applications"

    # 4. (Escalation handled above before FV block)

    # 5. Litigation
    if any(w in msg for w in ["litigation", "court", "legal", "flagged", "case flag",
                               "வழக்கு", "நீதிமன்றம்", "சட்ட வழக்கு", "கோர்ட்"]):
        return "litigation_check"

    # 6. Sale deed
    if any(w in msg for w in ["sale deed", "deed number", "registered deed",
                               "sub-registrar", "sub registrar", "deed verified",
                               # Tamil: விற்பனை பத்திரம்
                               "விற்பனை பத்திரம்", "பத்திர எண்", "பதிவு செய்யப்பட்ட"]):
        return "sale_deed_check"

    # 7. Joint owners (moved earlier - see line ~1106)
    # Removed duplicate check - now handled before application_status

    # 8. Active applications by taluk
    if "active" in msg and "taluk" in msg:
        return "active_applications_taluks"
    # Tamil: செயலில் உள்ள விண்ணப்பங்கள்
    if any(w in msg for w in ["செயலில்", "சுறுசுறுப்பான"]) and "தாலுகா" in msg:
        return "active_applications_taluks"

    # 9. Assigned today
    if "assigned" in msg and "today" in msg:
        return "assigned_today"
    # Tamil: இன்று ஒதுக்கப்பட்டது
    if any(w in msg for w in ["இன்று", "இன்றைக்கு"]) and any(w in msg for w in ["ஒதுக்கப்பட்ட", "வழங்கப்பட்ட", "விண்ணப்பம்"]):
        return "assigned_today"

    # 10. Immediate action
    for kw1, kw2 in [
        ("immediate", "action"), ("urgent", ""), ("need attention", ""),
        ("action today", ""), ("critical", "application"),
        ("require action", ""), ("requires action", ""), ("deadline today", ""),
    ]:
        if kw1 in msg and (not kw2 or kw2 in msg):
            return "immediate_action"
    # Tamil: உடனடி நடவடிக்கை, அவசர
    if any(w in msg for w in ["உடனடி நடவடிக்கை", "உடனடியாக", "அவசர நடவடிக்கை", "கவனிக்க வேண்டிய"]):
        return "immediate_action"

    # 11. Awaiting field visit
    if "awaiting" in msg and ("visit" in msg or "inspection" in msg):
        return "awaiting_field_visit"
    # Tamil: கள ஆய்வு காத்திருக்கும்
    if any(w in msg for w in ["கள ஆய்வு காத்திருக்கும்", "கள ஆய்வு நிலுவை", "ஆய்வு காத்திருப்பு"]):
        return "awaiting_field_visit"

    # 12. Completion rate
    if any(p in msg for p in [
        "completion rate", "completion percentage", "completion percent",
        "percent complete", "percentage complete", "overall completion",
        "how many completed", "how many done", "how many finished",
        "completed this month", "finished this month", "done this month",
        "completed applications", "approved this month", "closed this month",
        # Tamil: முடிக்கப்பட்ட விண்ணப்பங்கள், நிறைவு விகிதம்
        "நிறைவு விகிதம்", "முடிக்கப்பட்ட விண்ணப்பங்கள்", "எத்தனை முடிந்தது",
        "இந்த மாதம் முடிந்த", "முடிந்த விண்ணப்பங்கள்"
    ]):
        return "completion_rate"

    # 13. Pending longest
    if "pending" in msg and "longest" in msg:
        return "pending_longest"
    # Tamil: நீண்ட காலமாக நிலுவையில்
    if any(w in msg for w in ["நீண்ட காலமாக", "மிக நீண்ட", "அதிக நாட்கள்"]) and has(ta_pending):
        return "pending_longest"

    # 14. Workload by type
    if "workload" in msg and "type" in msg:
        return "workload_by_type"
    # Tamil: வகை வாரியான பணிச்சுமை
    if any(w in msg for w in ["வகை வாரியான", "வகை அடிப்படையில்"]) and has(ta_workload):
        return "workload_by_type"

    # 15. Officer workload
    if has(ta_workload) or ("how many" in msg and "assigned" in msg):
        return "officer_workload"
    # Tamil: எனது பணிச்சுமை, பணி சுமை விவரம்
    if any(w in msg for w in ["எனது பணி", "என் பணிச்சுமை", "பணி விவரம்"]):
        return "officer_workload"

    # 16. NISD vs ISD & Service Codes (0153 / 0154) - catch ANY question about application type definitions or service codes
    if any(w in msg for w in ["0153", "0154", "service code", "service_code"]):
        return "general_query"

    if any(w in msg for w in ["nisd", "isd", "merge"]) and \
       any(w in msg for w in ["what", "என்ன", "difference", "வேறுபாடு", "mean", "stand for",
                               "explain", "விளக்கம்", "define", "definition", "code", "service"]):
        return "general_query"  # Force RAG retrieval for definition queries

    # 16b. Typed application lists — MUST come before the is_nisd_or_isd trap below.
    # "display nisd", "show isd", "compare both nisd n isd", "show isd n merg", "no of nisd applications" → typed list.
    # Guard: fire when there's an action, comparison, or count word
    _action_words_16 = [
        "show", "list", "display", "view", "count", "how many", "number", "no of", "no.", "no ", "no_of", "num", "total",
        "compare", "both", "all", "get", "fetch", "give", "find", "details", "summary", "versus", "vs", "n", "and",
        "காட்டு", "காண்பி", "பட்டியல்", "எத்தனை", "எண்ணிக்கை", "தொகை", "ஒப்பிடு", "இரண்டும்"
    ]
    _has_action_16 = any(w in msg for w in _action_words_16)
    if _has_action_16:
        # Use word-boundary regex so "nisd" as a substring doesn't falsely match "isd"
        _has_nisd_word  = bool(re.search(r'\bnisd\b', msg))
        _has_isd_word   = bool(re.search(r'\bisd\b', msg))
        _has_merge_word = bool(re.search(r'\bmerge?\b', msg))  # matches "merge" and "merg"
        _type_count = sum([_has_nisd_word, _has_isd_word, _has_merge_word])
        if _type_count >= 2:
            # Multiple types requested — combined intent
            return "both_applications"
        if _has_nisd_word:
            return "nisd_applications"
        if _has_isd_word:
            return "isd_applications"
        if _has_merge_word:
            return "merge_applications"

    # is_nisd_or_isd only fires for queries about a SPECIFIC application (e.g. "is APP-2024-000001 nisd or isd?")
    # Guard: requires explicit application number pattern or application reference words
    _has_app_ref = bool(re.search(r'(APP-\d{4}-\d{6}|(ISD|NISD|MERGE)/\w+/\d+/\d+)', msg, re.IGNORECASE)) or any(
        p in msg for p in ["this app", "that app", "this application", "that application", "same application", "the application"]
    )
    if bool(re.search(r'\bnisd\b', msg)) and bool(re.search(r'\bisd\b', msg)):
        if _has_app_ref:
            return "is_nisd_or_isd"
        return "both_applications"

    # 17. Check documents
    if "document" in msg and any(w in msg for w in ["missing", "required", "all", "have"]):
        return "check_documents"
    # Tamil: ஆவணங்கள் சரிபார்
    if any(w in msg for w in ["ஆவணங்கள்", "ஆவணம்"]) and any(w in msg for w in ["சரிபார்", "தேவையான", "இல்லாத", "காணாத"]):
        return "check_documents"

    # 18. Check sale deed (broader)
    if "deed" in msg or "sub-registrar" in msg:
        return "check_sale_deed"
    # Tamil: விற்பனை பத்திரம் (already in check 5 but catch broader deed queries)
    if any(w in msg for w in ["பத்திரம்", "துணை பதிவாளர்"]):
        return "check_sale_deed"

    # 19. Town applications
    if "town" in msg and any(w in msg for w in ["pending", "applications", "show", "list"]):
        return "town_applications"
    # Tamil: நகர விண்ணப்பங்கள்
    if "நகரம்" in msg and any(w in msg for w in ["நிலுவை", "விண்ணப்பங்கள்", "காட்டு", "பட்டியல்"]):
        return "town_applications"

    # 20. Block applications (not ward surveys)
    if has(ta_block) and any(w in msg for w in ["pending", "applications", "show", "list",
                                                  "நிலுவை", "விண்ணப்பங்கள்", "காட்டு"]):
        is_ward_surveys = has(ta_survey) and any(w in msg for w in ["show", "list", "all", "காட்டு"])
        if not is_ward_surveys:
            return "block_applications"

    # 21. Jurisdiction summary
    if any(w in msg for w in ["jurisdiction", "my area", "assigned area", "coverage",
                               "my jurisdiction",
                               # Tamil: எனது பகுதி, ஒதுக்கப்பட்ட பகுதி
                               "எனது பகுதி", "ஒதுக்கப்பட்ட பகுதி", "என் அதிகார வரம்பு"]) and \
       not has(ta_survey) and not has(ta_application):
        return "jurisdiction_summary"

    # ── ISD Processing queries (before survey_owners / pending_applications) ──
    _sd_kws = ["sub-division", "subdivision", "sub division",
               # Tamil: உட்பிரிவு
               "உட்பிரிவு", "உட்பிரிவுகள்"]
    if any(w in msg for w in _sd_kws) or "patta transfer" in msg or "பட்டா பரிமாற்றம்" in msg:
        if any(w in msg for w in ["patta transfer", "transfer order",
                                   "பட்டா பரிமாற்றம்", "பட்டா ஆணை"]):
            return "isd_processing"
        if any(w in msg for w in ["latest action", "action taken", "each sub-division", "each subdivision",
                                   "ஒவ்வொரு உட்பிரிவு", "கடைசி நடவடிக்கை"]):
            return "isd_processing"
        if "proposed" in msg or "முன்மொழியப்பட்ட" in msg:
            return "isd_processing"
        if "assigned" in msg and any(w in msg for w in ["number", "numbers",
                                                          "எண்கள்", "ஒதுக்கப்பட்ட எண்"]):
            return "isd_processing"
        if "status" in msg and ("retrieve" in msg or "by sub" in msg):
            return "isd_processing"
        if any(w in msg for w in ["compare", "original",
                                   "ஒப்பிடு", "அசல்", "ஒப்பீடு"]) and any(w in msg for w in ["area", "பரப்பளவு"]):
            return "isd_processing"

    # 22. Specific survey number + keyword ← after merge/isd checks
    if re.search(r'\b\d{1,4}(?:/\d{1,4}[A-Za-z]*)?\b', msg) and (
        has(ta_survey) or has(ta_area) or has(ta_subdivision)
    ):
        if has(ta_owner):   return "survey_owners"
        if has(ta_next):    return "next_subdivision"
        return "survey_detail"

    # 23. Survey owners
    if has(ta_owner):
        return "survey_owners"

    # 24. Ward / block scoped surveys
    if has(ta_survey) and has(ta_ward)  and has(ta_show): return "ward_surveys"
    if has(ta_survey) and has(ta_block) and has(ta_show): return "block_surveys"

    # 25. All surveys in jurisdiction
    if has(ta_survey) and has(ta_show) and not has(ta_ward) and not has(ta_block) and not has(ta_owner):
        return "all_surveys_in_jurisdiction"

    # 27. Typed application lists — check FIRST, before the generic pending catch-all
    # "display nisd", "show isd", "list merge" should return typed intents, not pending_applications
    _action_words = ["show", "list", "display", "view", "count", "how many",
                     "காட்டு", "காண்பி", "பட்டியல்", "எத்தனை"]
    _has_action = any(w in msg for w in _action_words)
    if fuzzy_match("isd") and fuzzy_match("nisd"):
        return "both_applications"
    if fuzzy_match("nisd") and (has(ta_show + ta_application) or _has_action):
        return "nisd_applications"
    if fuzzy_match("isd") and (has(ta_show + ta_application) or _has_action):
        return "isd_applications"

    # 26. Pending applications - ONLY for explicit list/show/count queries
    # Let comparison/explanation queries fall through to general_query
    # Guard: workflow/explanation queries should NOT be caught here
    is_explanation_query = any(w in msg for w in ["workflow", "step", "guide", "procedure",
                                                   "process", "work flow", "mean", "stand for",
                                                   "difference", "explain", "compare", "versus", "vs",
                                                   "between", "what is", "what are", "tell me about",
                                                   "என்றால்", "என்ன", "எது"])
    
    if not is_explanation_query and not has(ta_merge):
        is_type_query   = any(w in msg for w in ["isd", "nisd", "merge"])
        is_app_query    = has(ta_application)
        is_year_query   = bool(re.search(r'\b(20\d{2})\b', msg))  # Check for year like 2025, 2026
        is_month_query  = any(w in msg for w in ["january", "february", "march", "april", "may", "june",
                                                   "july", "august", "september", "october", "november", "december",
                                                   "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
                                                   "ஜனவரி", "பிப்ரவரி", "மார்ச்", "ஏப்ரல்", "மே", "ஜூன்", "ஜூலை"])
        is_action_query = any(w in msg for w in ["how many", "howmuch", "show", "list",
                                                   "display", "view", "pending", "active",
                                                   "assigned", "count", "are there", "there are",
                                                   "காட்டு", "காண்பி", "பட்டியல்",
                                                   # Tamil: எத்தனை, காண்பி
                                                   "எத்தனை", "உள்ளன"])
        
        # Year/Month + applications = pending_applications (e.g., "2026 applications", "july applications")
        if is_app_query and (is_year_query or is_month_query) and not has(ta_ward):
            return "pending_applications"
        
        # is_type_query + is_action_query only returns pending_applications when
        # there is ALSO an application keyword — otherwise typed-only queries like
        # "display nisd" (no "application" word) would be caught here instead of rule 27
        if is_type_query and is_action_query and is_app_query and not has(ta_ward):
            return "pending_applications"
        if is_app_query and (is_action_query or is_type_query) and not has(ta_ward):
            return "pending_applications"
        if ("show" in msg or "list" in msg or "display" in msg or "காட்டு" in msg or "பட்டியல்" in msg) and \
           ("all" in msg or "அனைத்தும்" in msg or "application" in msg or "app" in msg or "விண்ணப்பம்" in msg or "விண்ணப்பங்கள்" in msg) and \
           not has(ta_ward):
            return "pending_applications"
        # Tamil: விண்ணப்பங்கள் பட்டியல் / காட்டு alone also triggers pending_applications
        # BUT exclude ward-level queries for block officers (jurisdiction check will handle)
        if has(ta_application) and has(ta_show) and not has(ta_ward):
            return "pending_applications"
        if has(ta_pending) and not has(ta_ward):
            return "pending_applications"


    # 28. Field visits
    if has(ta_field_visit) and not has(ta_application):
        return "field_visits"

    # 29. Survey detail (keyword, no number)
    if has(ta_survey) and any(w in msg for w in ["number", "no", "detail",
                                                   "விவரம்", "விவரங்கள்", "தகவல்"]) \
       and not has(ta_show):
        return "survey_detail"

    # 30. Next subdivision
    if has(ta_subdivision) and has(ta_next):
        return "next_subdivision"

    # 31. Subdivision detail
    if has(ta_subdivision):
        if has(ta_survey) or any(c.isdigit() for c in msg):
            return "survey_detail"
        return "subdivision_detail"

    # 32. MERGE — detail check BEFORE list check to avoid false matches
    if has(ta_merge) and (has(ta_survey + ta_subdivision + ta_detail) or has(ta_area)):
        return "merge_info"
    if has(ta_merge) and has(ta_show + ta_application):
        return "merge_applications"
    if has(ta_merge):
        return "merge_info"

    # 33. Rejection
    if fuzzy_match("reject") or any(w in msg for w in ["நிராகரிப்பு", "நிராகரிக்கப்பட்டது",
                                                         "ஏன் நிராகரிக்கப்பட்டது", "நிராகரிப்பு காரணம்"]):
        return "rejection_info"

    # 34. Taluk summary
    if any(w in msg for w in ["taluk", "தாலுகா", "தாலுக்கா"]) and \
       any(w in msg for w in ["summary", "all", "total", "how many",
                               "சுருக்கம்", "மொத்தம்", "அனைத்தும்", "எத்தனை"]) and \
       not has(ta_application):
        return "taluk_summary"

    return "general_query"


# ─────────────────────────────────────────────────────────────────────────────
# Entity extraction helpers (from current production version)
# ─────────────────────────────────────────────────────────────────────────────

def clean_message(message: str) -> str:
    """Remove list prefixes (like '1. ', '2) ', 'a- ') at the beginning of the message."""
    if not message:
        return ""
    cleaned = re.sub(r'^\s*\d+[\.\)\-]\s*', '', message)
    cleaned = re.sub(r'^\s*[a-zA-Z][\.\)\-]\s*', '', cleaned)
    return cleaned.strip()


def extract_survey_number(message: str) -> Optional[str]:
    """
    Extract survey number from message, handling list prefixes and survey keywords.
    Supports formats: "145", "145/1A", "survey no 145", "survey 145/2B".
    """
    cleaned = clean_message(message)

    # Keyword match first (e.g. "survey 145", "survey no 145/1A")
    keyword_match = re.search(
        r'\bsurvey(?:\s+(?:no|num|number|nos|numbers)(?:\.|\b)?)?(?:\s*[:\-#])?\s*(\d{1,4}(?:/\d{1,2}[A-Z]?)?)\b',
        cleaned, re.IGNORECASE
    )
    if keyword_match:
        return keyword_match.group(1)

    # Survey number with subdivision pattern (e.g. 145/1A)
    slash_match = re.search(r'\b\d{1,4}/\d{1,2}[A-Z]?\b', cleaned)
    if slash_match:
        return slash_match.group(0)

    # Fallback to any 1-4 digit number
    fallback_match = re.search(r'\b\d{1,4}\b', cleaned)
    if fallback_match:
        return fallback_match.group(0)

    return None


def extract_application_number(message: str) -> Optional[str]:
    """Extract application number (e.g. ISD/W1/2024/0001 or APP-2024-000015)."""
    cleaned = clean_message(message)
    app_match = re.search(
        r'\b(?:ISD|NISD|MERGE)/\w+/\d+/\d+\b|\bAPP-\d+-\d+\b',
        cleaned, re.IGNORECASE
    )
    if app_match:
        return app_match.group(0).upper()
    return None


def extract_ward_number(message: str) -> Optional[str]:
    """
    Extract ward number from message.
    Hierarchy: District → Taluk → Town → Ward → Block
    """
    cleaned = clean_message(message)

    match = re.search(
        r'\bward\s*(?:no(?:\.|\b)?)?\s*[:\-#]?\s*(\d+)\b',
        cleaned, re.IGNORECASE
    )
    if match:
        return match.group(1)

    skip_keywords = r'\b(?:block|survey|district|taluk|town)\s*(?:no(?:\.|\b)?)?\s*[:\-#]?\s*$'
    for m in re.finditer(r'\b\d+\b', cleaned):
        preceding = cleaned[:m.start()].lower()
        if not re.search(skip_keywords, preceding):
            return m.group(0)
    return None


def extract_block_number(message: str) -> Optional[str]:
    """
    Extract block number from message.
    Block is BELOW Ward in hierarchy. May be alphanumeric (e.g. "3", "B4").
    """
    cleaned = clean_message(message)

    match = re.search(
        r'\bblock\s*(?:no(?:\.|\b)?)?\s*[:\-#]?\s*([A-Z]?\d+)\b',
        cleaned, re.IGNORECASE
    )
    if match:
        return match.group(1).upper()

    skip_keywords = r'\b(?:ward|survey|district|taluk|town)\s*(?:no(?:\.|\b)?)?\s*[:\-#]?\s*$'
    for m in re.finditer(r'\b([A-Z]?\d+)\b', cleaned, re.IGNORECASE):
        preceding = cleaned[:m.start()].lower()
        if not re.search(skip_keywords, preceding):
            return m.group(1).upper()
    return None


def extract_town_name(message: str) -> Optional[str]:
    """
    Extract town name from message.
    Town is between Taluk and Ward in hierarchy.
    """
    cleaned = clean_message(message)

    match = re.search(
        r'\btown\s+(?:of\s+)?([A-Za-z\s]+?)(?:\s+town)?\b',
        cleaned, re.IGNORECASE
    )
    if match:
        return match.group(1).strip()

    match = re.search(r'([A-Za-z\s]+?)\s+town\b', cleaned, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    return None


def extract_taluk_name(message: str) -> Optional[str]:
    """
    Extract taluk name from message.
    Taluk is directly below District in hierarchy.
    """
    cleaned = clean_message(message)

    match = re.search(
        r'\btaluk\s+(?:of\s+)?([A-Za-z\s]+?)(?:\s+taluk)?\b',
        cleaned, re.IGNORECASE
    )
    if match:
        return match.group(1).strip()

    match = re.search(r'([A-Za-z\s]+?)\s+taluk\b', cleaned, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    return None


def extract_district_name(message: str) -> Optional[str]:
    """
    Extract district name from message, resolving district codes (01-38) to district names.
    District is the top of the jurisdiction hierarchy.
    """
    cleaned = clean_message(message)

    # 1. Check for explicit district code references like "district code 02", "district 02", "code 02"
    code_match = re.search(r'\b(?:district\s+(?:code\s+)?)?0*([1-9]|[1-3][0-8])\b', cleaned, re.IGNORECASE)
    if code_match:
        num = int(code_match.group(1))
        code_str = f"{num:02d}"
        if code_str in DISTRICT_CODE_MAP:
            return DISTRICT_CODE_MAP[code_str]

    # 2. Check for district name patterns like "district of Chennai" or "Chennai district"
    match = re.search(
        r'\bdistrict\s+(?:of\s+)?([A-Za-z\s]+?)(?:\s+district)?\b',
        cleaned, re.IGNORECASE
    )
    if match:
        val = match.group(1).strip()
        val_lower = val.lower()
        if val_lower in DISTRICT_NAME_MAP:
            return DISTRICT_CODE_MAP[DISTRICT_NAME_MAP[val_lower]]
        return val

    match = re.search(r'([A-Za-z\s]+?)\s+district\b', cleaned, re.IGNORECASE)
    if match:
        val = match.group(1).strip()
        val_lower = val.lower()
        if val_lower in DISTRICT_NAME_MAP:
            return DISTRICT_CODE_MAP[DISTRICT_NAME_MAP[val_lower]]
        return val

    # 3. Direct district name lookup from 38 districts dictionary
    for d_name in DISTRICT_CODE_MAP.values():
        if re.search(r'\b' + re.escape(d_name) + r'\b', cleaned, re.IGNORECASE):
            return d_name

    return None


def extract_date_range(message: str) -> Tuple[Optional[date], Optional[date]]:
    """
    Extract start_date and end_date from natural language queries.
    Supports ISO dates (YYYY-MM-DD), standard dates (DD/MM/YYYY, DD-MM-YYYY),
    relative date phrases (this week, next week, this month, next month, next 7/15/30 days),
    and phrases like "between <date1> and <date2>", "from <date1> to <date2>".
    """
    cleaned = clean_message(message).lower()
    today = date.today()

    # 1. ISO format date pattern (YYYY-MM-DD)
    iso_dates = []
    for d in re.findall(r'\b\d{4}-\d{2}-\d{2}\b', cleaned):
        try:
            iso_dates.append(datetime.strptime(d, "%Y-%m-%d").date())
        except ValueError:
            pass
    if len(iso_dates) >= 2:
        iso_dates.sort()
        return iso_dates[0], iso_dates[-1]
    elif len(iso_dates) == 1:
        return iso_dates[0], iso_dates[0]

    # 2. DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY
    std_dates = []
    for m in re.finditer(r'\b(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})\b', cleaned):
        try:
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            std_dates.append(date(year, month, day))
        except ValueError:
            pass
    if len(std_dates) >= 2:
        std_dates.sort()
        return std_dates[0], std_dates[-1]
    elif len(std_dates) == 1:
        return std_dates[0], std_dates[0]

    # 3. Relative phrases
    if any(w in cleaned for w in ["this week", "இந்த வாரம்"]):
        start_w = today - timedelta(days=today.weekday())
        end_w = start_w + timedelta(days=6)
        return start_w, end_w

    if any(w in cleaned for w in ["next week", "அடுத்த வாரம்"]):
        start_w = today + timedelta(days=(7 - today.weekday()))
        end_w = start_w + timedelta(days=6)
        return start_w, end_w

    if any(w in cleaned for w in ["this month", "இந்த மாதம்"]):
        start_m = today.replace(day=1)
        _, last_day = calendar.monthrange(today.year, today.month)
        end_m = today.replace(day=last_day)
        return start_m, end_m

    if any(w in cleaned for w in ["next month", "அடுத்த மாதம்"]):
        if today.month == 12:
            start_m = date(today.year + 1, 1, 1)
        else:
            start_m = date(today.year, today.month + 1, 1)
        _, last_day = calendar.monthrange(start_m.year, start_m.month)
        end_m = start_m.replace(day=last_day)
        return start_m, end_m

    days_match = re.search(r'\b(?:next|coming|last|past)\s+(\d+)\s+days?\b', cleaned)
    if days_match:
        n_days = int(days_match.group(1))
        if any(w in cleaned for w in ["last", "past"]):
            return today - timedelta(days=n_days), today
        else:
            return today, today + timedelta(days=n_days)

    return None, None

