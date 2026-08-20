"""
RAG (Retrieval-Augmented Generation) pipeline for SIS Chatbot

Jurisdiction hierarchy: District → Taluk → Town → Ward → Block → Survey Number → Sub-Division

Components:
  detect_language()             — Tamil / Tanglish / English detection
  get_rag_context()             — pgvector semantic retrieval (blocking)
  get_rag_context_async()       — awaitable wrapper; use this from coroutines
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
import asyncio
import calendar
import re

from backend.config import settings, DISTRICT_CODE_MAP, DISTRICT_NAME_MAP
from backend.services.pgvector_store import similarity_search
from backend.utils.logger import get_logger
from backend.utils.fuzzy import extract_tokens, is_token_typo_match, match_phrase, normalize_text

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
        "ta"        — predominantly Tamil (>50 % Tamil chars) or explicit Tamil request
        "tanglish"  — mixed Tamil + English, Tamil chars present, or Romanized Tanglish keywords
        "en"        — pure English / no Tamil
    """
    if not text:
        return "en"

    # Explicit request for Tamil output in English or Tamil script.
    # The bare word "tamil" is NOT enough — "Tamil Nadu" appears in ordinary
    # English queries (district/taluk names) and must not flip the whole
    # response to Tamil. Require a request verb/preposition around it.
    # Drop the place name first so "applications in Tamil Nadu" stays English.
    lowered = re.sub(r'\btamil\s*nadu\b|\btn\s+state\b', ' ', text.lower())
    _tamil_request = (
        re.search(r'\b(in|into|reply|answer|respond|speak|say|translate|write)\b'
                  r'[^.?!]{0,15}\btami[lz]h?\b', lowered)
        or re.search(r'\btami[lz]h?\s*(il|la|le)\b', lowered)
        or 'தமிழில்' in text or 'தமிழ்ல' in text
    )
    if _tamil_request:
        return "ta"

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


async def get_rag_context_async(query: str, language: str = "en", n_results: int = 5) -> str:
    """
    Async wrapper around get_rag_context().

    similarity_search() embeds the query over HTTP and queries PostgreSQL through
    psycopg2 — both fully blocking. Calling it directly from the chat coroutines
    stalled the whole event loop for the duration of the retrieval, so run it on
    a worker thread instead.
    """
    return await asyncio.to_thread(get_rag_context, query, language, n_results)


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
# NOTE: the single definition of _e() lives below (near _app_link). It must not be
# duplicated — a second definition silently overrode this one and changed the
# rendering of None from "-" to "".

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


def _get_projected_application_columns(user_query: str):
    """
    If the user explicitly asked for specific columns (e.g. 'with application no and type only',
    'show application no with isd n n status', 'application no and status', 'with status'),
    return the list of requested column keys.
    Otherwise return None to render default full table.

    NOTE: bare commands like "show applications", "list applications", "display apps" must NOT
    trigger projection — they want the full default table, not a single-column view.
    """
    uq = user_query.lower()

    # ── Explicit bail-out: bare "show/list/display applications" with no specific field terms ──
    # These are generic listing intents, not column-projection requests.
    _bare_list_pattern = re.compile(
        r'^\s*(show|list|display|view|get|give|provide)?\s*'
        r'(all\s+)?(my\s+)?(pending\s+|in.?progress\s+|overdue\s+)?'
        r'(applications?|apps?)\s*$'
    )
    if _bare_list_pattern.match(uq.strip()):
        return None

    has_display_verb = bool(re.search(r'\b(display|show|list|select|view|give|get|provide|format|columns?|fields?)\b', uq))
    has_only = bool(re.search(r'\b(only|alone|மட்டும்)\b', uq))
    has_with_fields = bool(re.search(r'\b(with|having|along with|உடன்)\b', uq))
    has_and_status = bool(re.search(r'\b(and|n|&|\+)\s+(status|type|date|name|stage|mobile|email|address|survey|block|ward)\b', uq)) or "n n status" in uq
    has_specific_fields = bool(re.search(r'\b(application no|app no)\s+(and|n|with)\s+(status|type|date|name|stage|isd|nisd|merge)\b', uq))

    # Specific non-application field keywords (exclude generic app/no/number words)
    _specific_field_kws = [
        "applicant", "name", "mobile", "email", "address", "type", "survey", "subdivision",
        "status", "stage", "date", "submitted", "block", "ward", "taluk", "town", "district",
        "patta", "sale deed", "reason", "priority", "can", "service code", "taluk code",
        "area", "sqm", "sq", "square"
    ]
    # Check if multiple recognized schema field names are present
    field_hits = sum(1 for kw in _specific_field_kws
                     if re.search(r'\b' + re.escape(kw) + r'\b', uq))

    # has_display_verb alone is NOT sufficient — it must be paired with at least one
    # specific field keyword to count as a projection request.
    # (Prevents "show applications" / "list apps" from being treated as projection)
    display_verb_with_specific_field = has_display_verb and field_hits >= 1

    is_projection = (
        display_verb_with_specific_field
        or has_only
        or has_with_fields
        or has_and_status
        or has_specific_fields
        or (field_hits >= 2)
    )
    if not is_projection:
        return None

    cols = []
    has_app_no = bool(re.search(r'\b(app|apps|application|applications|application no|application number|app no|app number|no|number|விண்ணப்ப எண்)\b', uq))
    if has_app_no or not any(k in uq for k in ["name", "status", "type", "isd", "nisd", "merge", "stage", "date", "block"]):
        cols.append("application_no")
        
    if bool(re.search(r'\b(applicant name|applicant\'s name|applicant|applicants|பெயர்|peyar)\b', uq)) or (
        bool(re.search(r'\bname\b', uq)) and not re.search(r'\b(district name|taluk name|town name)\b', uq)
    ):
        cols.append("applicant_name")
    if bool(re.search(r'\b(mobile|phone|contact|cell|தொலைபேசி|கைபேசி)\b', uq)):
        cols.append("mobile")
    if bool(re.search(r'\b(email|mail|emails|மின்னஞ்சல்)\b', uq)):
        cols.append("email")
    if bool(re.search(r'\b(address|addr|முகவரி)\b', uq)) and not bool(re.search(r'\b(ip\s*address|ip_address)\b', uq)):
        cols.append("address")
    if bool(re.search(r'\b(ip|ip address|ip_address|ip முகவரி)\b', uq)):
        cols.append("ip_address")
    if bool(re.search(r'\b(type|application type|types|isd|nisd|merge|வகை)\b', uq)):
        cols.append("type")
    if bool(re.search(r'\b(survey|survey no|survey number|surveys|கணக்கெண்)\b', uq)):
        cols.append("survey_no")
    if bool(re.search(r'\b(subdivision|subdivisions|sub-division|sub-divisions|sub\s*division|sub\s*divisions|subdivision_number|current_subdivision_number|உட்பிரிவு)\b', uq)):
        cols.append("subdivisions")
    if bool(re.search(r'\b(area|area sq|area sqm|total area|merge area|sqm|sq\.m|sq m|sq ft|square|பரப்பளவு|சதுர மீட்டர்)\b', uq)):
        cols.append("area_sqm")
    if bool(re.search(r'\b(status|current status|statuses|நிலை)\b', uq)):
        cols.append("status")
    if bool(re.search(r'\b(stage|current stage|stages|workflow|workflow_state|workflow state|கட்டம்)\b', uq)):
        cols.append("stage")
    if bool(re.search(r'\b(overdue days|days overdue|காலதாமத நாட்கள்)\b', uq)):
        cols.append("overdue_days")
    if bool(re.search(r'\b(submitted|submission date|submission|date|dates|application date|application_date|தேதி|நாள்)\b', uq)) and not bool(re.search(r'\b(update|updated|last_updated)\b', uq)):
        cols.append("submitted")
    if bool(re.search(r'\b(block code|block_code)\b', uq)):
        cols.append("block_code")
    elif bool(re.search(r'\b(block|blocks|தொகுதி)\b', uq)):
        cols.append("block")
    if bool(re.search(r'\b(ward code|ward_code)\b', uq)):
        cols.append("ward_code")
    elif bool(re.search(r'\b(ward|wards|வார்டு)\b', uq)):
        cols.append("ward")
    if bool(re.search(r'\b(town|towns|urban_unit_code|நகரம்)\b', uq)):
        cols.append("town")
    if bool(re.search(r'\b(taluk code|taluk_code)\b', uq)):
        cols.append("taluk_code")
    elif bool(re.search(r'\b(taluk|taluks|தாலுகா)\b', uq)):
        cols.append("taluk")
    if bool(re.search(r'\b(district|districts|district_code|மாவட்டம்)\b', uq)):
        cols.append("district")
    if bool(re.search(r'\b(patta|patta no|patta number|patta_number|பட்டா)\b', uq)):
        cols.append("patta_no")
    if bool(re.search(r'\b(sale deed|sale deed no|sale deed number|பத்திரம்|கிரய பத்திரம்)\b', uq)):
        cols.append("sale_deed_no")
    if bool(re.search(r'\b(sale deed registered|deed status|deed registered)\b', uq)):
        cols.append("sale_deed_reg")
    if bool(re.search(r'\b(reason|declared reason|declared_reason|காரணம்)\b', uq)):
        cols.append("declared_reason")
    if bool(re.search(r'\b(field visit date|visit date|inspection date|field_visit_date|ஆய்வு தேதி)\b', uq)):
        cols.append("field_visit_date")
    if bool(re.search(r'\b(priority|முன்னுரிமை)\b', uq)):
        cols.append("priority")
    if bool(re.search(r'\b(notes|remarks|குறிப்பு)\b', uq)):
        cols.append("notes")
    if bool(re.search(r'\b(can|can no|can number|can_number)\b', uq)):
        cols.append("can_no")
    if bool(re.search(r'\b(service code|service_code)\b', uq)):
        cols.append("service_code")
    if bool(re.search(r'\b(igrs|form 6|form6|form_6|igrs_form6_number)\b', uq)):
        cols.append("igrs_form6")
    if bool(re.search(r'\b(auto mutation|auto_mutation|auto_mutated|is_auto_mutated)\b', uq)):
        cols.append("auto_mutation")
    if bool(re.search(r'\b(camp|camp_flag|camp_code|camp_correction_id)\b', uq)):
        cols.append("camp")
    if bool(re.search(r'\b(charge|charges|fee|fees|service charge|csc_service_charge|government_service_charge|government fee|கட்டணம்)\b', uq)):
        cols.append("charges")

    if not cols:
        return None
    if "application_no" not in cols:
        cols.insert(0, "application_no")
    return cols


def _get_projected_field_visit_columns(user_query: str):
    uq = user_query.lower()
    has_only = bool(re.search(r'\b(only|alone|மட்டும்)\b', uq))
    has_with_fields = bool(re.search(r'\b(with|having|along with|உடன்)\b', uq))
    has_and_field = bool(re.search(r'\b(and|n|&|\+)\s+(status|type|date|scheduled date|name|mobile|email|address|survey|block)\b', uq))
    
    is_projection = has_only or has_with_fields or has_and_field
    if not is_projection:
        return None
        
    cols = []
    if bool(re.search(r'\b(app|apps|application|applications|application number|app no|no|number|விண்ணப்ப எண்)\b', uq)):
        cols.append("application_number")
    if bool(re.search(r'\b(applicant name|applicant\'s name|applicant|applicants|பெயர்)\b', uq)) or (
        bool(re.search(r'\bname\b', uq)) and not re.search(r'\b(district name|taluk name|town name)\b', uq)
    ):
        cols.append("applicant_name")
    if bool(re.search(r'\b(mobile|phone|contact|cell|தொலைபேசி)\b', uq)):
        cols.append("mobile")
    if bool(re.search(r'\b(email|mail|மின்னஞ்சல்)\b', uq)):
        cols.append("email")
    if bool(re.search(r'\b(address|addr|முகவரி)\b', uq)):
        cols.append("address")
    if bool(re.search(r'\b(survey|survey no|survey number|கணக்கெண்)\b', uq)):
        cols.append("survey_no")
    if bool(re.search(r'\b(block|தொகுதி)\b', uq)):
        cols.append("block")
    if bool(re.search(r'\b(type|isd|nisd|merge|வகை)\b', uq)):
        cols.append("type")
    if bool(re.search(r'\b(status|நிலை)\b', uq)):
        cols.append("status")
    if bool(re.search(r'\b(scheduled date|visit date|date|dates|scheduled|தேதி)\b', uq)):
        cols.append("scheduled_date")
    if not cols:
        return None
    if "application_number" not in cols:
        cols.insert(0, "application_number")
    return cols


def _e(val: Any) -> str:
    """HTML escape helper"""
    if val is None:
        return ""
    return escape(str(val))


def _app_link(app_no: Any) -> str:
    """Render application number as interactive clickable link/chip"""
    if not app_no:
        return "N/A"
    raw = str(app_no).strip()
    clean = _e(raw)
    # The onclick argument sits in a JS string inside an HTML attribute, so HTML
    # escaping alone is not enough: `&#x27;` is decoded back to `'` before the JS
    # is parsed and would break out of the string literal. Restrict the JS
    # argument to the characters an application number can legally contain.
    js_arg = re.sub(r'[^A-Za-z0-9/\-_]', '', raw)
    return f"<a href='javascript:void(0)' class='app-table-link' onclick=\"window.handleAppClick('{js_arg}')\" style='color:#2563eb;text-decoration:underline;cursor:pointer;font-weight:600;'>{clean}</a>"


def build_html_response(structured_data: Dict[str, Any], language: str = "en", query: str = "") -> str:
    """
    Build a clean HTML response directly from structured DB data,
    bypassing the LLM entirely for table-based queries.

    All DB values are HTML-escaped to prevent XSS / broken markup.
    Intro text uses <div> (not <p>) — browsers auto-close <p> before
    block-level elements like <table>, which hides the <thead>.

    Args:
        structured_data: Database query results
        language: Detected language ("en", "ta", or "tanglish") for table labels
        query: User message/question to dynamically include requested columns (e.g. applicant name, email)

    Returns an HTML string, or "" if no structured data to display
    (caller should then fall back to the LLM).
    """
    if not structured_data:
        return ""

    # ── Suggestions for un-found applications / misspellings ─────────
    if structured_data.get("suggestions"):
        searched = _e(structured_data.get("searched_number") or "")
        suggestions = structured_data.get("suggestions", [])
        sug_rows = ""
        for s in suggestions:
            app_no = s.get("application_number")
            app_type = s.get("type", "N/A")
            status = s.get("status", "pending")
            stage = s.get("stage", "SIS")
            applicant = s.get("applicant_name", "N/A")
            sug_rows += (
                f"<tr>"
                f"<td>{_app_link(app_no)}</td>"
                f"<td>{_e(app_type)}</td>"
                f"<td>{_status(status, 'en')}</td>"
                f"<td>{_e(stage)}</td>"
                f"<td>{_e(applicant)}</td>"
                f"</tr>"
            )
        msg_header = f"Application <strong>{searched}</strong> was not found." if searched else "Application not found."
        return (
            f"<div class='table-intro' style='color:#b91c1c;margin-bottom:8px;'>⚠️ {msg_header} Did you mean one of the following applications?</div>"
            f"<table class='data-table'>"
            f"<thead><tr><th>Application Number</th><th>Type</th><th>Status</th><th>Stage</th><th>Applicant</th></tr></thead>"
            f"<tbody>{sug_rows}</tbody>"
            f"</table>"
        )

    if not structured_data.get("found", True):
        return ""

    # Detect user-requested specific extra columns
    user_query = (query or structured_data.get("user_query") or structured_data.get("query") or "").lower()
    req_name = any(w in user_query for w in [
        "applicant name", "applicant's name", "applicant", "application name", 
        "applicant_name", "விண்ணப்பதாரர்", "பெயர்", "peyar"
    ]) or bool(re.search(r'\b(name|names|applicant|applicants)\b', user_query))

    req_email = any(w in user_query for w in [
        "email", "e-mail", "mail", "emails", "மின்னஞ்சல்", "minnanjal"
    ]) or bool(re.search(r'\b(email|emails|mail|e-mail)\b', user_query))

    req_mobile = any(w in user_query for w in [
        "mobile", "phone", "contact", "cell", "தொலைபேசி", "கைபேசி", "tholaipaesi"
    ]) or bool(re.search(r'\b(mobile|phone|contact|cell|phone\s*no|mobile\s*no)\b', user_query))

    req_address = any(w in user_query for w in [
        "address", "addr", "முகவரி", "mugavari", "virivu"
    ]) or bool(re.search(r'\b(address|addr)\b', user_query))

    extra_th = ""
    if req_name:
        extra_th += f"<th>Applicant Name</th>"
    if req_mobile:
        extra_th += f"<th>Mobile</th>"
    if req_email:
        extra_th += f"<th>Email</th>"
    if req_address:
        extra_th += f"<th>Address</th>"

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

    # ── IP Address / Submission Channel Analysis ─────────────────────
    if "ip_analysis" in structured_data and isinstance(structured_data["ip_analysis"], list):
        items = structured_data["ip_analysis"]
        total_ips = structured_data.get("total_unique_ips", len(items))
        total_apps = structured_data.get("total_applications", 0)
        
        rows = "".join(
            f"<tr>"
            f"<td><code>{_e(item.get('ip_address'))}</code></td>"
            f"<td style='font-weight: bold; text-align: center;'>{item.get('submission_count')}</td>"
            f"<td>{_e(item.get('source'))}</td>"
            f"<td>{', '.join(_app_link(app_no) for app_no in item.get('applications', [])[:5])}{' ...' if len(item.get('applications', [])) > 5 else ''}</td>"
            f"<td><span style='color: #059669; font-weight: 500;'>{_e(item.get('status'))}</span></td>"
            f"</tr>"
            for item in items
        )
        return (
            f"<div class='table-intro'>Found <strong>{total_apps}</strong> application(s) across <strong>{total_ips}</strong> IP address(es):</div>"
            f"<table class='data-table'>"
            f"<thead><tr>"
            f"<th>IP Address</th><th>Submissions</th><th>Submission Source</th><th>Applications</th><th>Audit / Legitimacy Status</th>"
            f"</tr></thead>"
            f"<tbody>{rows}</tbody>"
            f"</table>"
            f"<div style='margin-top: 10px; font-size: 13px; color: #4b5563; background: #f9fafb; border-left: 4px solid #3b82f6; padding: 8px 12px; border-radius: 4px;'>"
            f"💡 <strong>IP Analysis Note:</strong> Applications submitted via Common Service Centers (CSC) share the CSC's IP address. Applications submitted directly by citizens have individual IPs. <em>IP address data is recorded only when captured at submission time.</em>"
            f"</div>"
        )

    # ── CAN Number & CSC Assignment Guide ───────────────────────────
    if "can_summary" in structured_data and isinstance(structured_data["can_summary"], dict):
        can = structured_data["can_summary"]
        return (
            f"<div class='table-intro'><strong>Citizen Access Number (CAN) & CSC Assignment Details:</strong></div>"
            f"<table class='data-table'>"
            f"<thead><tr><th>Key Aspect</th><th>Details</th></tr></thead>"
            f"<tbody>"
            f"<tr><td><strong>Assigned By</strong></td><td>{_e(can.get('assigned_by'))}</td></tr>"
            f"<tr><td><strong>What is CAN?</strong></td><td>{_e(can.get('description'))}</td></tr>"
            f"<tr><td><strong>Role in Patta Transfer</strong></td><td>{_e(can.get('role_in_patta_transfer'))}</td></tr>"
            f"<tr><td><strong>CSC Service Charge</strong></td><td>{_e(can.get('csc_charges'))}</td></tr>"
            f"<tr><td><strong>Supported Service Codes</strong></td><td><code>{_e(can.get('service_codes_linked'))}</code></td></tr>"
            f"</tbody>"
            f"</table>"
        )

    # ── Service Codes Workflow & Fee Comparison (0153 / 0154 / 0155) ──
    if "service_codes" in structured_data and isinstance(structured_data["service_codes"], list):
        items = structured_data["service_codes"]
        rows = "".join(
            f"<tr>"
            f"<td><span style='background: #e0f2fe; color: #0369a1; padding: 2px 6px; border-radius: 4px; font-weight: bold;'>{_e(sc.get('service_code'))}</span></td>"
            f"<td><strong>{_e(sc.get('type'))}</strong><br><small style='color: #6b7280;'>{_e(sc.get('tamil_name'))}</small></td>"
            f"<td>{_e(sc.get('govt_fee'))}</td>"
            f"<td>{_e(sc.get('csc_fee'))}</td>"
            f"<td>{_e(sc.get('sla_days'))}</td>"
            f"<td>{_e(sc.get('workflow'))}</td>"
            f"</tr>"
            for sc in items
        )
        return (
            f"<div class='table-intro'><strong>Tamil Nadu Land Administration — Service Codes Guide:</strong></div>"
            f"<table class='data-table'>"
            f"<thead><tr>"
            f"<th>Service Code</th><th>Application Type</th><th>Govt Fee</th><th>CSC Fee</th><th>SLA</th><th>Survey & Processing Workflow</th>"
            f"</tr></thead>"
            f"<tbody>{rows}</tbody>"
            f"</table>"
        )

    # ── Applications (regular + merge) ──────────────────────────────
    if "applications" in structured_data and isinstance(structured_data["applications"], list):
        applications = structured_data["applications"]
        count = structured_data.get("count", len(applications))

        if not applications:
            qtype_name = structured_data.get("query_type")
            if qtype_name:
                return f"<div class='table-intro'><strong>{qtype_name}</strong>: No applications found.</div>"
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
        is_merge = bool(applications) and all(app.get("type") == "MERGE" for app in applications)
        qtype_name = structured_data.get("query_type", "")
        is_overdue_query = "Overdue" in qtype_name and "Non-Overdue" not in qtype_name
        has_overdue_col = any(app.get("days_overdue") is not None for app in applications) or is_overdue_query

        overdue_th = "<th>OVERDUE DAYS</th>" if has_overdue_col else ""
        min_days_overdue = structured_data.get("min_days_overdue")
        # Only show "Details for X" title when this was an explicit single-application lookup
        # (i.e. query_type starts with "Application "), NOT for generic pending_applications
        # queries that happen to return only 1 result.
        if count == 1 and qtype_name.startswith("Application "):
            app_no_title = qtype_name.replace("Application ", "").strip()
            intro_msg = f"Details for <strong>{app_no_title}</strong>:" if lang != "ta" else f"<strong>{app_no_title}</strong> விவரங்கள்:"
        elif min_days_overdue:
            intro_msg = f"{t['found']} <strong>{count}</strong> application(s) overdue by {min_days_overdue}+ days:"
        elif is_overdue_query:
            intro_msg = f"{t['found']} <strong>{count}</strong> overdue application(s):"
        elif "Non-Overdue" in qtype_name:
            intro_msg = f"{t['found']} <strong>{count}</strong> non-overdue application(s):"
        else:
            intro_msg = f"{t['found']} <strong>{count}</strong> {t['applications']}:"

        projected_cols = _get_projected_application_columns(user_query)
        if projected_cols:
            col_labels = {
                "application_no": t["application_no"],
                "applicant_name": "Applicant Name",
                "mobile": "Mobile",
                "email": "Email",
                "address": "Address",
                "type": t["type"],
                "survey_no": t["survey_no"],
                "subdivisions": t["subdivisions"],
                "area_sqm": t["area_sqm"],
                "status": t["status"],
                "stage": t["stage"],
                "overdue_days": "OVERDUE DAYS",
                "submitted": t["submitted"],
                "district": t["district"],
                "taluk": t["taluk"],
                "taluk_code": "Taluk Code",
                "town": t["town"],
                "ward": t["ward"],
                "ward_code": "Ward Code",
                "block": t["block"],
                "block_code": "Block Code",
                "patta_no": "Patta No.",
                "sale_deed_no": "Sale Deed No.",
                "sale_deed_reg": "Deed Registered",
                "declared_reason": "Declared Reason",
                "field_visit_date": "Field Visit Date",
                "priority": "Priority",
                "notes": "Notes / Remarks",
                "can_no": "CAN Number",
                "ip_address": "IP Address",
                "service_code": "Service Code",
                "igrs_form6": "IGRS Form 6",
                "auto_mutation": "Auto Mutated",
                "camp": "Camp Code",
                "charges": "Fees / Charges"
            }
            th_html = "".join(f"<th>{col_labels.get(c, c.title())}</th>" for c in projected_cols)
            rows = ""
            for app in applications:
                overdue = " ⚠️" if app.get("is_overdue") else ""
                jur = app.get("jurisdiction", {})
                subdivisions = app.get("subdivisions_being_merged", [])
                subdiv_list = app.get("subdivisions") or app.get("sub_division_no") or (", ".join(sd["sub_division_no"] for sd in subdivisions) if subdivisions else "-")
                raw_area = app.get('total_merge_area_sqm') or app.get('survey_total_area_sqm') or app.get('total_area_sqm') or app.get('area_sqm')
                merge_area = f"{float(raw_area):.2f}" if raw_area is not None else 'N/A'
                loc_map = {
                    "district": jur.get('district') or app.get('district_name') or 'N/A',
                    "taluk": jur.get('taluk') or app.get('taluk_name') or 'N/A',
                    "town": jur.get('town') or app.get('town_name') or 'N/A',
                    "ward": jur.get('ward') or app.get('ward_number') or 'N/A',
                    "block": jur.get('block') or app.get('block_number') or 'N/A'
                }
                deed_reg = "Yes" if app.get('sale_deed_registered') else "No"
                priority_val = "High" if app.get('priority_flag') else "Normal"
                
                # Derive service code from app type if missing
                app_type = app.get('type') or ('MERGE' if is_merge else 'ISD')
                srv_code = "0154" if app_type == "ISD" else ("0155" if app_type == "MERGE" else "0153")
                app_num = str(app.get('application_number') or '')
                parts = app_num.split('/')
                if len(parts) >= 2 and parts[1].isdigit():
                    srv_code = parts[1]

                cell_map = {
                    "application_no": _app_link(app.get('application_number')),
                    "applicant_name": _e(app.get('applicant_name') or 'N/A'),
                    "mobile": _e(app.get('applicant_mobile') or 'N/A'),
                    "email": _e(app.get('applicant_email') or 'N/A'),
                    "address": _e(app.get('applicant_address') or 'N/A'),
                    "type": _e(app_type),
                    "survey_no": _e(app.get('survey_no') or 'N/A'),
                    "subdivisions": _e(subdiv_list),
                    "area_sqm": _e(merge_area),
                    "status": f"{_status(app.get('status'), lang)}{overdue}",
                    "stage": _e(app.get('stage') or 'N/A'),
                    "overdue_days": f"<span style='color: #c53030; font-weight: bold;'>⚠️ {app.get('days_overdue')} days</span>" if app.get('days_overdue') is not None else "-",
                    "submitted": _e(app.get('submission_date') or 'N/A'),
                    "district": _e(loc_map["district"]),
                    "taluk": _e(loc_map["taluk"]),
                    # taluk_code: only show if actually stored; never fabricate
                    "taluk_code": _e(app.get('taluk_code') or 'N/A'),
                    "town": _e(loc_map["town"]),
                    "ward": _e(loc_map["ward"]),
                    "ward_code": _e(f"Ward {loc_map['ward']}" if loc_map['ward'] and loc_map['ward'] != 'N/A' and not str(loc_map['ward']).lower().startswith('ward') else (str(loc_map['ward']) if loc_map['ward'] and loc_map['ward'] != 'N/A' else 'N/A')),
                    "block": _e(loc_map["block"]),
                    "block_code": _e(f"Block {loc_map['block']}" if loc_map['block'] and loc_map['block'] != 'N/A' and not str(loc_map['block']).lower().startswith('block') else (str(loc_map['block']) if loc_map['block'] and loc_map['block'] != 'N/A' else 'N/A')),
                    "patta_no": _e(app.get('patta_number') or app.get('patta_no') or 'N/A'),
                    "sale_deed_no": _e(app.get('sale_deed_number') or 'N/A'),
                    "sale_deed_reg": _e(deed_reg),
                    "declared_reason": _reason(app.get('declared_reason')),
                    "field_visit_date": _e(app.get('field_visit_date') or 'Unscheduled'),
                    "priority": _e(priority_val),
                    "notes": _e(app.get('notes') or 'N/A'),
                    # Fields that must NEVER be fabricated — show N/A if not in DB
                    "can_no": _e(app.get('can_number') or 'N/A'),
                    "ip_address": _e(app.get('ip_address') or 'N/A'),
                    "service_code": _e(srv_code),
                    "igrs_form6": _e(app.get('igrs_form6_number') or 'N/A'),
                    "auto_mutation": _e("Yes" if app.get('is_auto_mutated') else ("No" if app.get('is_auto_mutated') is not None else 'N/A')),
                    "camp": _e(app.get('camp_code') or 'N/A'),
                    "charges": _e(app.get('csc_service_charge') or 'N/A'),
                }
                td_html = "".join(f"<td>{cell_map.get(c, '-')}</td>" for c in projected_cols)
                rows += f"<tr>{td_html}</tr>"

            return (
                f"<div class='table-intro'>{intro_msg}</div>"
                f"<table class='data-table'>"
                f"<thead><tr>{th_html}</tr></thead>"
                f"<tbody>{rows}</tbody>"
                f"</table>"
            )

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
                
                overdue_td = ""
                if has_overdue_col:
                    ov_days = app.get("days_overdue")
                    if ov_days is not None:
                        overdue_td = f"<td><span style='color: #c53030; font-weight: bold;'>⚠️ {ov_days} days</span></td>"
                    else:
                        overdue_td = "<td>-</td>"

                extra_td = ""
                if req_name:
                    extra_td += f"<td>{_e(app.get('applicant_name') or 'N/A')}</td>"
                if req_mobile:
                    extra_td += f"<td>{_e(app.get('applicant_mobile') or 'N/A')}</td>"
                if req_email:
                    extra_td += f"<td>{_e(app.get('applicant_email') or 'N/A')}</td>"
                if req_address:
                    extra_td += f"<td>{_e(app.get('applicant_address') or 'N/A')}</td>"

                rows += (
                    f"<tr>"
                    f"<td>{_app_link(app.get('application_number'))}</td>"
                    f"{extra_td}"
                    f"<td>{_e(app.get('type') or 'MERGE')}</td>"
                    f"<td>{_e(app.get('survey_no'))}</td>"
                    f"<td>{subdiv_list}</td>"
                    f"<td>{merge_area}</td>"
                    f"<td>{_status(app.get('status'), lang)}{overdue}</td>"
                    f"<td>{_e(app.get('stage'))}</td>"
                    f"{overdue_td}"
                    f"<td>{_e(app.get('submission_date'))}</td>"
                    f"{loc_td}"
                    f"</tr>"
                )
            return (
                f"<div class='table-intro'>{intro_msg}</div>"
                f"<table class='data-table'>"
                f"<thead><tr>"
                f"<th>{t['application_no']}</th>{extra_th}<th>{t['type']}</th><th>{t['survey_no']}</th><th>{t['subdivisions']}</th>"
                f"<th>{t['area_sqm']}</th><th>{t['status']}</th><th>{t['stage']}</th>{overdue_th}<th>{t['submitted']}</th>"
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

                overdue_td = ""
                if has_overdue_col:
                    ov_days = app.get("days_overdue")
                    if ov_days is not None:
                        overdue_td = f"<td><span style='color: #c53030; font-weight: bold;'>⚠️ {ov_days} days</span></td>"
                    else:
                        overdue_td = "<td>-</td>"

                extra_td = ""
                if req_name:
                    extra_td += f"<td>{_e(app.get('applicant_name') or 'N/A')}</td>"
                if req_mobile:
                    extra_td += f"<td>{_e(app.get('applicant_mobile') or 'N/A')}</td>"
                if req_email:
                    extra_td += f"<td>{_e(app.get('applicant_email') or 'N/A')}</td>"
                if req_address:
                    extra_td += f"<td>{_e(app.get('applicant_address') or 'N/A')}</td>"

                rows += (
                    f"<tr>"
                    f"<td>{_app_link(app.get('application_number'))}</td>"
                    f"{extra_td}"
                    f"<td>{_e(app.get('type'))}</td>"
                    f"<td>{_e(app.get('raw_survey_no') or app.get('survey_no') or 'N/A')}</td>"
                    f"<td>{_e(app.get('subdivisions') or app.get('sub_division_no') or app.get('included_subdivisions') or '-')}</td>"
                    f"<td>{_status(app.get('status'), lang)}{overdue}</td>"
                    f"<td>{_e(app.get('stage'))}</td>"
                    f"{overdue_td}"
                    f"<td>{_e(app.get('submission_date'))}</td>"
                    f"{loc_td}"
                    f"</tr>"
                )
            return (
                f"<div class='table-intro'>{intro_msg}</div>"
                f"<table class='data-table'>"
                f"<thead><tr>"
                f"<th>{t['application_no']}</th>{extra_th}<th>{t['type']}</th><th>{t['survey_no']}</th><th>{t['subdivisions']}</th>"
                f"<th>{t['status']}</th><th>{t['stage']}</th>{overdue_th}<th>{t['submitted']}</th>"
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
            f"<td>{(sd.get('area_sqm') or 0):.2f}</td>"
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
            f"<li>{t['total_area']}: <strong>{(structured_data.get('total_area_sqm') or 0):.2f} sq.m</strong></li>"
            f"<li>{t['land_type']}: {_e(structured_data.get('land_type'))}</li>"
            f"<li>{t['patta_number']}: {_e(structured_data.get('patta_number'))}</li>"
            f"<li>{t['jurisdiction']}: {_e(jur.get('district'))} &rarr; {_e(jur.get('taluk'))} &rarr; "
            f"{_e(jur.get('town'))} &rarr; {_e(jur.get('ward'))} &rarr; {_e(jur.get('block'))}</li>"
            f"</ul>"
            f"<div class='table-intro'>{t['subdivisions']} ({structured_data.get('sub_divisions_count', 0)}):</div>"
            f"{subdiv_table}"
        )

    # ── Multiple application details ─────────────────────────────────
    # NOTE: multi_applications is intentionally NOT handled here.
    # It is rendered via _build_table_data → multi_tables → table_renderer.js
    # so that both apps use the same rich "Application & Applicant Details" format.

    # ── Single application detail ────────────────────────────────────
    if "application" in structured_data and isinstance(structured_data["application"], dict):
        app = structured_data["application"]
        applicant = app.get("applicant") or {}
        field_visit = app.get("field_visit") or {}
        overdue_flag = " ⚠️" if app.get("is_overdue") else ""
        priority_flag = " (High Priority)" if app.get("is_priority") else ""

        merge_info_html = ""
        if app.get("type") == "MERGE":
            subdiv_list = ", ".join(sd["sub_division_no"] for sd in app.get("subdivisions_being_merged", [])) or "-"
            total_merge_area = f"{app.get('total_merge_area_sqm'):.2f} sq.m" if app.get('total_merge_area_sqm') else "N/A"
            num_subdivs = app.get("number_of_subdivisions", len(app.get("subdivisions_being_merged", [])))
            merge_info_html = (
                f"<tr><td><strong>{t['survey_number']}</strong></td><td>{_e(app.get('survey_no'))}</td></tr>"
                f"<tr><td><strong>{t['subdivisions_being_merged']}</strong></td><td>{_e(subdiv_list)}</td></tr>"
                f"<tr><td><strong>{t['number_of_subdivisions']}</strong></td><td>{num_subdivs}</td></tr>"
                f"<tr><td><strong>{t['total_merge_area']}</strong></td><td>{total_merge_area}</td></tr>"
            )

        # Optional extra fields (declared reason, sale deed, patta, land type, submitted via)
        optional_rows = ""
        if app.get("declared_reason"):
            optional_rows += f"<tr><td><strong>{t['declared_reason']}</strong></td><td>{_reason(app.get('declared_reason'))}</td></tr>"
        if app.get("sale_deed_number"):
            optional_rows += f"<tr><td><strong>{t['sale_deed_number']}</strong></td><td>{_e(app.get('sale_deed_number'))}</td></tr>"
        if app.get("sale_deed_registered") is not None:
            optional_rows += f"<tr><td><strong>{t['sale_deed_registered']}</strong></td><td>{t['yes'] if app.get('sale_deed_registered') else t['no']}</td></tr>"
        if app.get("patta_number"):
            optional_rows += f"<tr><td><strong>{t['patta_number']}</strong></td><td>{_e(app.get('patta_number'))}</td></tr>"
        if app.get("land_type"):
            optional_rows += f"<tr><td><strong>{t['land_type']}</strong></td><td>{_e(app.get('land_type'))}</td></tr>"
        if app.get("submitted_via"):
            channel_label = t.get(app.get("submitted_via"), app.get("submitted_via"))
            optional_rows += f"<tr><td><strong>{t['submitted_via']}</strong></td><td>{_e(channel_label)}</td></tr>"

        # Applicant contact details
        applicant_rows = ""
        if applicant:
            if applicant.get("name"):
                applicant_rows += f"<tr><td><strong>{t['applicant_name']}</strong></td><td>{_e(applicant.get('name'))}</td></tr>"
            if applicant.get("mobile"):
                applicant_rows += f"<tr><td><strong>{t['mobile']}</strong></td><td>{_e(applicant.get('mobile'))}</td></tr>"
            if applicant.get("email"):
                applicant_rows += f"<tr><td><strong>{t['email']}</strong></td><td>{_e(applicant.get('email'))}</td></tr>"
            if applicant.get("address"):
                applicant_rows += f"<tr><td><strong>{t['address']}</strong></td><td>{_e(applicant.get('address'))}</td></tr>"

        # Field visit section
        field_visit_rows = ""
        if field_visit:
            fv = field_visit
            field_visit_rows += f"<tr><td><strong>{t['field_visit_status']}</strong></td><td>{_status(fv.get('status'), lang)}</td></tr>"
            if fv.get("scheduled_date"):
                field_visit_rows += f"<tr><td><strong>{t['scheduled_date']}</strong></td><td>{_e(fv.get('scheduled_date'))}</td></tr>"
            if fv.get("actual_visit_date"):
                field_visit_rows += f"<tr><td><strong>{t['actual_visit_date']}</strong></td><td>{_e(fv.get('actual_visit_date'))}</td></tr>"
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
        
        rows = ""
        for v in visits:
            extra_td = ""
            if req_name:
                extra_td += f"<td>{_e(v.get('applicant_name') or 'N/A')}</td>"
            if req_mobile:
                extra_td += f"<td>{_e(v.get('applicant_mobile') or 'N/A')}</td>"
            if req_email:
                extra_td += f"<td>{_e(v.get('applicant_email') or 'N/A')}</td>"
            if req_address:
                extra_td += f"<td>{_e(v.get('applicant_address') or 'N/A')}</td>"

            rows += (
                f"<tr>"
                f"<td>{_e(v.get('application_number'))}</td>"
                f"{extra_td}"
                f"<td>{_e(v.get('type'))}</td>"
                f"<td>{_status(v.get('status'), lang)}</td>"
                f"<td>{_e(v.get('stage'))}</td>"
                f"<td>{_e(v.get('submission_date'))}</td>"
                f"<td>{_e(v.get('days_since_submission'))} {'days' if lang == 'en' else 'நாட்கள்'}</td>"
                f"</tr>"
            )
        return (
            f"<div class='table-intro'>{t['found']} <strong>{len(visits)}</strong> "
            f"{t['unscheduled_field_visits']}:</div>"
            f"<table class='data-table'>"
            f"<thead><tr>"
            f"<th>{t['application_no']}</th>{extra_th}<th>{t['type']}</th><th>{t['status']}</th>"
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
        
        if start_date and end_date:
            if str(start_date) in query_type:
                date_info = ""
            elif start_date == end_date:
                date_info = f" ({start_date})"
            else:
                date_info = f" ({start_date} to {end_date})"
        else:
            date_info = ""
        
        if to_be_visited_count is not None and structured_data.get("to_be_visited_only"):
            count_info = f"Found <strong>{to_be_visited_count}</strong> field visit(s) needed to be visited"
        else:
            count_info = f"Found <strong>{len(field_visits)}</strong> field visit(s)"

        if not field_visits:
            return f"<div class='table-intro'><strong>{query_type}</strong>{date_info}: No field visits scheduled.</div>"
        
        rows = ""
        for fv in field_visits:
            extra_td = ""
            if req_name:
                extra_td += f"<td>{_e(fv.get('applicant_name') or 'N/A')}</td>"
            if req_mobile:
                extra_td += f"<td>{_e(fv.get('applicant_mobile') or 'N/A')}</td>"
            if req_email:
                extra_td += f"<td>{_e(fv.get('applicant_email') or 'N/A')}</td>"
            if req_address:
                extra_td += f"<td>{_e(fv.get('applicant_address') or 'N/A')}</td>"

            subdiv_val = fv.get('subdivisions') or fv.get('sub_division_no') or '-'
            rows += (
                f"<tr>"
                f"<td>{_e(fv.get('application_number'))}</td>"
                f"{extra_td}"
                f"<td>{_e(fv.get('raw_survey_no') or fv.get('survey_no') or 'N/A')}</td>"
                f"<td>{_e(subdiv_val)}</td>"
                f"<td>{_e(fv.get('block_number'))}</td>"
                f"<td>{_e(fv.get('application_type'))}</td>"
                f"<td>{_status(fv.get('status'), lang)}{' ⚠️' if fv.get('is_overdue') else ''}</td>"
                f"<td>{_e(fv.get('field_visit_date') or 'Not Scheduled')}</td>"
                f"</tr>"
            )
        
        return (
            f"<div class='table-intro'><strong>{query_type}</strong>{date_info}: {count_info}</div>"
            f"<table class='data-table'>"
            f"<thead><tr>"
            f"<th>{t['application_no']}</th>{extra_th}<th>{t['survey_no']}</th><th>{t['subdivisions']}</th><th>{t['block']}</th>"
            f"<th>{t['type']}</th><th>{t['status']}</th><th>{t['scheduled_date']}</th>"
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
- FIELD VISIT DATE CHANGES: If an SIS officer asks about changing, modifying, or rescheduling the date of a field visit, clearly state that they should ask the Tahsildar about field visit date change (as the Tahsildar is the approving authority).
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
        response = await llm.ainvoke(prompt)
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
def parse_intent(message: str, prev_intent: str = None) -> str:
    """
    Parse user intent from message using exact token-boundary and production edit-distance matching.
    Supports English, Tamil, and Tanglish. Deterministic identifiers use exact token bounds;
    natural language questions without deterministic keywords gracefully fall through to 'general_query'
    for semantic LLM / RAG processing.

    Args:
        message:     The current user message.
        prev_intent: Intent of the immediately preceding turn (used for context-aware
                     disambiguation of follow-up filter phrases like "in merge").
    """
    # Strip leading list-item prefixes like "1.", "2)", "a-" etc.
    message = re.sub(r'^\s*\d+[\.\)\-]\s*', '', message)
    message = re.sub(r'^\s*[a-zA-Z][\.\)\-]\s*', '', message)

    msg = normalize_text(message)
    words = extract_tokens(msg)

    def fuzzy_match(keyword: str, threshold: float = 0.75) -> bool:
        """
        Token-boundary matching: exact token match or edit-distance typo match on tokens.
        Does NOT match arbitrary substrings across token boundaries.

        Multi-word keywords ("sub division", "work load", "over-due") are matched
        as a contiguous token run, so they are separator-agnostic but never match
        on a single stray word.
        """
        kw_norm = normalize_text(keyword)
        if not kw_norm or not words:
            return False

        kw_tokens = extract_tokens(kw_norm)
        if len(kw_tokens) != 1:
            # Phrase keyword \u2014 exact contiguous token-run match only
            return match_phrase(words, kw_norm)
        kw_norm = kw_tokens[0]

        # Exact token match (word boundary strict)
        if kw_norm in words:
            return True

        # For tokens length < 5 or containing Tamil Unicode, require exact token match
        # to avoid 4-letter cross-collisions like 'date' matching 'late'
        if len(kw_norm) < 5 or any('\u0B80' <= c <= '\u0BFF' for c in kw_norm):
            return False

        # Edit distance typo match against individual words.
        # min_ratio enforces the caller's confidence threshold instead of
        # relying on the edit budget alone.
        for word in words:
            if len(word) >= 5 and is_token_typo_match(word, kw_norm, min_ratio=threshold):
                return True

        return False

    def has(keywords: list) -> bool:
        return any(fuzzy_match(kw) for kw in keywords)

    # ── keyword sets (English + Tamil + Transliterated Tamil Script + Tanglish) ──
    ta_survey      = ["கணக்கெண்", "கணக்கு", "நில அளவை", "நிலஅளவை", "சர்வே", "சர்வேக்கள்", "சர்வே எண்", "சர்வே எண்கள்", "சார்வே",
                      "survey", "surveys", "survay", "suvery", "surveynumber", "survey no", "kanakken", "kanakku"]
    ta_subdivision = ["உட்பிரிவு", "உட்பிரிவுகள்", "உட்பிரிவினை", "சப்டிவிஷன்", "சப் டிவிஷன்", "சப்டிவிஷன்கள்", "சப்டிவிஸன்",
                      "subdivision", "subdivisions", "sub-division", "sub-divisions", "subdiv", "utpirivu", "utpirivugal"]
    ta_show        = ["காட்டு", "காண்பி", "பட்டியல்", "அனைத்தும்", "காட்டவும்", "காண்பிக்கவும்", "காட்டுங்க", "காட்டுப்பா", "ஷோ", "லிஸ்ட்",
                      "show", "list", "all", "my", "display", "get", "view", "fetch", "kaattu", "kaatuvom"]
    ta_owner       = ["உரிமையாளர்", "சொந்தக்காரர்", "கூட்டுரிமையாளர்", "உரிமையாளர்கள்", "உரிமையாளரின்",
                      "owner", "owners", "ownership", "patta", "pattadar", "urimaiyalar", "urimayalar"]
    ta_pending     = ["நிலுவை", "நிலுவையில்", "நிலுவையிலுள்ள", "காத்திருக்கும்", "பெண்டிங்", "பெண்டிங்ஸ்", "பெண்டிங்கில்", "பெண்டிங்க்", "பென்டிங்",
                      "pending", "waiting", "pendig", "pendng", "uncompleted", "niluvai", "niluvaiyil"]
    ta_overdue     = ["காலதாமத", "காலதாமதமான", "தாமதம்", "தாமதமான", "காலக்கெடு கடந்த", "ஓவர்டியூ", "ஓவர் டியூ", "ஓவர்ட்யூ", "ஓவர்டியு", "ஓவர்டியூஸ்", "ஒவர்டியூ", "ஓவர்ட்யு", "ஓவர்",
                      "overdue", "late", "delayed", "overdew", "overdu", "over-due", "kaalathaamadha", "kaalathamadhamaana", "thamadham"]
    ta_field_visit = ["கள ஆய்வு", "களஆய்வு", "வருகை", "களப் பார்வை", "பீல்டு விசிட்", "பீடு விசிட்", "பீல்ட் விசிட்", "பீல்ட்", "விசிட்", "ஆய்வு",
                      "field", "visit", "visits", "scheduling", "schedule", "feild", "inspection", "kala aaivu"]
    ta_workload    = ["பணிச்சுமை", "வேலைச்சுமை", "ஒர்க்லோடு", "வொர்க்லோடு", "வர்க்லோட்", "வர்க் லோடு",
                      "workload", "worklod", "work load", "panichumai", "velaichumai"]
    ta_application = ["விண்ணப்பம்", "விண்ணப்பங்கள்", "விண்ணப்பங்களை", "விண்ணப்பங்களின்",
                      "ஆப்ளிகேஷன்", "ஆப்ளிகேஷன்ஸ்", "ஆப்ளிகேஷன்கள்", "அப்ளிகேஷன்", "அப்ளிகேஷன்ஸ்", "அப்ளிகேஷன்கள்", "ஆப்ளிகேஷனை", "ஆப்ளிகேஷன்களை",
                      "application", "applications", "aplications", "aplication", "app", "appl", "apps", "vinnappam", "vinnappangal"]
    ta_merge       = ["இணைப்பு", "இணைக்க", "இணைக்கப்பட்ட", "இணைப்பு விண்ணப்பம்", "இணைப்பு விண்ணப்பங்கள்", "இணைத்தல்", "மெர்ஜ்", "மெர்ஜிங்",
                      "merge", "merging", "merged", "merg"]
    ta_status      = ["நிலை", "தற்போதைய நிலை", "ஸ்டேட்டஸ்", "ஸ்டேடஸ்", "ஸ்டேட்ஸ்",
                      "status", "statuss", "staus", "stage", "nilai"]
    ta_area        = ["பரப்பளவு", "பரப்பு", "area", "arrea"]
    ta_ward        = ["வார்டு", "ward", "wards"]
    ta_block       = ["தொகுதி", "block", "blocks"]
    ta_next        = ["அடுத்த", "கிடைக்கும்", "next", "available", "nxt"]
    ta_detail      = ["விவரம்", "விவரங்கள்", "விவரங்களை", "detail", "details", "info",
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
    # "sd" must be matched as a whole word — a bare substring test also fires on
    # "isd", "nisd" and "wednesday", hijacking ordinary typed-application queries.
    if re.search(r'\bsd\b', msg):
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

    # ── Context-aware type-filter follow-up detection (PRODUCTION) ───────────
    # Handles short follow-up messages like:
    #   "in merge", "only isd", "merge only", "isd n nisd", "both merge and isd"
    # These are NOT new application-list queries — they refine the previous result.
    #
    # Production rules (two independent paths, either is sufficient):
    #
    # PATH A — Structural pure-filter: message matches one of the canonical
    #   filter patterns regardless of previous intent.
    #   Examples: "in merge", "only isd", "isd n nisd", "merge only"
    #
    # PATH B — Context-aware: prev_intent was a field-visit intent AND message
    #   contains a type keyword with NO strong application-query signal.
    #
    # All paths guard against strong application-query signals so that
    # "show merge applications" / "list isd" still reach merge/isd_applications.
    #
    # Word-boundary regex is used throughout to prevent substring false-positives
    # (e.g. "in" matches "n", "stands" matches "and").

    _TYPE_RE  = re.compile(r'\b(isd|nisd|merge|merg|merger|merging)\b', re.IGNORECASE)
    _JOINER_RE = re.compile(r'\b(and|n|or|&)\b', re.IGNORECASE)
    # "Strong application-query" signals — if any of these are present as whole
    # words we should NOT redirect to field_visits.
    _STRONG_APP_RE = re.compile(
        r'\b(application|applications|app|apps|pending|show|list|display|fetch|give|'
        r'get|find|all|view|detail|summary|count|total|how many|number of|'
        r'காட்டு|காண்பி|பட்டியல்|விண்ணப்பம்|விண்ணப்பங்கள்)\b',
        re.IGNORECASE
    )
    # Canonical filter patterns (anchor-to-end regex on normalized msg)
    _PURE_FILTER_RE = re.compile(
        r'^(?:in|only|for|filter|show only|just|of type|type)?\s*'
        r'(?:isd|nisd|merge|merg|merger|merging)'
        r'(?:\s+(?:and|n|or|&)\s+(?:isd|nisd|merge|merg|merger|merging))*'
        r'\s*(?:only|type|types|applications?|apps?)?\s*$',
        re.IGNORECASE
    )
    _FV_INTENTS = {
        "field_visits", "fv_between_dates", "fv_scheduled_this_week",
        "fv_overdue_inspections", "fv_unassigned_awaiting", "fv_recently_rescheduled",
        "fv_nearby_pending", "fv_change_date", "fv_reschedule_availability",
    }

    _has_type_kw   = bool(_TYPE_RE.search(msg))
    _has_strong_app = bool(_STRONG_APP_RE.search(msg))
    _is_pure_filter = bool(_PURE_FILTER_RE.match(msg.strip()))
    _prev_was_fv   = (prev_intent in _FV_INTENTS)

    if _has_type_kw and not _has_strong_app and (_is_pure_filter or _prev_was_fv):
        return "field_visits"

    # Field visit specific workflow intents (check before general field_visits)
    # Match if message contains field visit keywords (as phrase OR separate words)

    _has_app_pattern = bool(re.search(r'\b\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+\b|\b\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+\b|\b(?:ISD|NISD|MERGE)/\w+/\d+/\d+\b|\bAPP-\d+-\d+\b', message, re.IGNORECASE))

    _has_fv_terms = any(w in msg for w in [
        "field visit", "field visits", "inspection", "inspections", "fv", "visit date",
        "கள ஆய்வு", "களஆய்வு", "வருகை", "கள பார்வை"
    ]) or (("field" in msg or "visit" in msg or "inspection" in msg) and not any(w in msg for w in ["application", "applications", "survey number"]))

    has_field_visit_keywords = (_has_fv_terms or (
        any(w in msg for w in [
            "schedule", "calendar", "deadline", "15-working-day", "15 working day", "15-day",
            "திட்டமிட", "திட்டமிடப்பட", "திட்டமிடப்படாத", "காலக்கெடு", "கடந்து விட்டதா", "கடந்துவிட்டதா"
        ]) and not any(w in msg for w in ["application", "applications", "isd", "nisd", "merge"])
    )) and not _has_app_pattern
    
    if has_field_visit_keywords:
        # Check for field visit date change / reschedule questions (authority: Tahsildar)
        if any(w in msg for w in [
            "change of date", "change date", "date change", "modify date", "date modification",
            "postpone date", "change the date", "how to change date", "how do i change date",
            "can i change date", "who to ask", "whom to ask", "change field visit date",
            "change of field visit date", "field visit date change", "reschedule date",
            "reschedule field visit date", "change inspection date", "inspection date change",
            "change visit date", "visit date change", "date of field visit change",
            "change of date of field visit", "how to change field visit date",
            "can i change field visit date", "whom should i ask", "who should i ask",
            "who changes field visit date", "change visit", "modify visit",
            # Tamil
            "தேதி மாற்றம்", "தேதியை மாற்ற", "தேதி மாற்றுவது", "தேதியை மாற்று", "மறுதேதி",
            "தேதி மாற்ற முடியுமா", "தேதி தள்ளிவைக்க", "தேதியை மாற்றலாமா", "தேதி மாற்றத்திற்கு",
            "கள ஆய்வு தேதி மாற்றம்", "கள ஆய்வு தேதியை மாற்ற", "தேதி மாற்ற",
            # Tanglish
            "date maatha", "date mathuradhu", "date change panna", "date maathalama",
            "yaarukitta kekkanum", "yaaridam kekka vendum"
        ]) or (
            any(w in msg for w in ["change", "modify", "postpone", "மாற்ற", "மாற்றம்", "maatha"]) and
            any(w in msg for w in ["date", "தேதி", "day"])
        ):
            return "fv_change_date"

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
        
        _is_negated_overdue = any(w in msg for w in [
            "not overdue", "non overdue", "non-overdue", "on time", "not late",
            "not delayed", "within sla", "தாமதமில்லாத", "தாமதம் இல்லாத", "காலதாமதமாகாத"
        ])
        
        # --- Specific overdue field visit patterns (check FIRST) ---
        if not _is_negated_overdue and any(ph in msg for ph in [
            "show overdue field", "show overdue visit", "show overdue visits",
            "list overdue field", "list overdue visits", "overdue field visits list",
            "கால தாமதமான கள ஆய்வு பட்டியல்", "தாமதமான கள ஆய்வு பட்டியல்"
        ]):
            return "fv_overdue_inspections"
        
        _is_overdue = any(w in msg for w in ["overdue", "late", "delayed", "காலதாமதமான"])
        _is_field_visit = any(w in msg for w in ["field visit", "field visits", "visit", "visits", "inspection", 
                                                   "கள ஆய்வு", "கள்ஆய்வு", "ஆய்வு"])
        _is_list_action = any(w in msg for w in ["show", "list", "all", "display", "fetch", "get", "பட்டியல்", "காட்டு"])
        
        if _is_overdue and _is_field_visit and not _is_negated_overdue and _is_list_action and not any(w in msg for w in ["?", "what", "is", "என்ன"]):
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

        # If has field visit terms, return field_visits
        return "field_visits"

    # 1a. Standalone Tamil/English unassigned field visit check
    # Catches pure-Tamil queries that may not have English field-visit trigger words
    if any(w in msg for w in ["திட்டமிடப்படாத", "திட்டமிடப்படவில்லை", "கால அட்டவணை இல்லாத",
                               "நிலுவையில் உள்ள கள ஆய்வு"]):
        return "fv_unassigned_awaiting"

    # 1b. Standalone date change query check
    if any(w in msg for w in [
        "field visit date change", "change of date of field visit", "change date of field visit",
        "change field visit date", "how to change field visit date", "can i change field visit date",
        "reschedule field visit date", "who to ask about field visit date", "whom to ask about field visit date",
        "who should i ask about field visit date", "who to ask about date change", "whom to ask about date change",
        "who should i ask about date change", "who changes field visit date",
        "கள ஆய்வு தேதி மாற்றம்", "கள ஆய்வு தேதியை மாற்ற", "தேதி மாற்றம் யாரிடம் கேட்க வேண்டும்"
    ]):
        return "fv_change_date"

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

    # 0. Specialized Analysis Intents: IP Address, CAN Info, Service Code Guide
    if ("same ip" in msg or "same ip address" in msg or "duplicate ip" in msg or 
        ("ip address" in msg and any(w in msg for w in ["same", "duplicate", "multiple", "any", "share", "common", "applicant", "applicants"])) or
        ("ip" in msg and "same" in msg)):
        return "ip_address_analysis"

    if ("can number" in msg or "can no" in msg or "can id" in msg) and any(w in msg for w in ["assigned", "assign", "csc", "how", "what is", "generated", "assignment", "who assigns"]):
        return "can_number_info"

    if (("service code" in msg or "service codes" in msg or "diff service" in msg or "different service" in msg or "service code handling" in msg) and 
        any(w in msg for w in ["how", "handle", "handling", "diff", "different", "difference", "explain", "what are", "guide", "summary", "0153", "0154", "0155"])):
        return "service_code_guide"

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
    if re.search(r'\b\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+\b|\b\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+\b|\b(?:ISD|NISD|MERGE)/\w+/\d+/\d+\b|\bAPP-\d+-\d+\b', message, re.IGNORECASE):
        return "application_status"

    # 2-priority. Highest priority — check EARLY to avoid application_status false match
    # "Show high priority applications" has "show" + "applications" + "priority"
    # Priority check must come BEFORE application_status field check (which includes "show" in interrogatives)
    if "priority" in msg and ("week" in msg or "highest" in msg or "high" in msg or "show" in msg or "list" in msg):
        return "highest_priority_applications"
    # Tamil: முன்னுரிமை, முன்னதாய, அவசர
    if any(w in msg for w in ["முன்னுரிமை", "முன்னதாய", "அவசர", "அவசரமான"]) and any(w in msg for w in ["உயர்ந்த", "அதிக", "இந்த வாரம்", "காட்டு", "பட்டியல்", "விண்ணப்பம்", "விண்ணப்பங்கள்"]):
        return "highest_priority_applications"

    # ── High Priority & List Queries (overdue, pending, merge, subdivision, survey, field visits) ──
    # MUST come before generic single-field interrogative checks (like application_status)
    _is_negated_overdue = any(w in msg for w in [
        "not overdue", "non overdue", "non-overdue", "on time", "not late",
        "not delayed", "within sla", "தாமதமில்லாத", "தாமதம் இல்லாத", "காலதாமதமாகாத"
    ])
    _is_interrogative_or_specific = any(w in msg for w in ["what", "where", "who", "which", "is it", "is this", "tell me", "give me", "காரணம்", "பெயர்", "முகவரி", "நிலை", "reason", "stage"])
    if has(ta_overdue) and not _is_negated_overdue and not _is_interrogative_or_specific:
        return "overdue_applications"

    if has(ta_pending) and (has(ta_application) or has(ta_show)):
        return "pending_applications"

    _has_nisd_w  = bool(re.search(r'\b(nisd|0153)\b', msg))
    _has_isd_w   = bool(re.search(r'\b(isd|0154)\b', msg))
    _has_merge_w = bool(re.search(r'\bmerge?\b', msg))
    _has_list_or_app = (
        has(ta_application) or has(ta_show) or
        any(w in msg for w in ["show", "list", "display", "view", "get", "fetch", "all", "application", "applications", "no", "number", "காட்டு", "பட்டியல்"])
    )

    if sum([_has_nisd_w, _has_isd_w, _has_merge_w]) >= 2:
        return "both_applications"

    if _has_merge_w and _has_list_or_app:
        return "merge_applications"

    if _has_nisd_w and _has_list_or_app:
        return "nisd_applications"

    if _has_isd_w and _has_list_or_app:
        return "isd_applications"

    if any(w in msg for w in ["applications", "application", "app", "apps", "விண்ணப்பங்கள்", "விண்ணப்பங்களை", "விண்ணப்பம்", "விண்ணப்பத்தை", "ஆப்ளிகேஷன்"]):
        if _has_merge_w:
            return "merge_applications"
        elif _has_nisd_w:
            return "nisd_applications"
        elif _has_isd_w:
            return "isd_applications"
        elif has(ta_overdue) and not _is_negated_overdue:
            return "overdue_applications"
        elif has(ta_pending):
            return "pending_applications"
        elif any(w in msg for w in ["today", "yesterday", "day before", "tomorrow", "day after", "morning", "afternoon", "evening", "am", "pm", "new", "received", "submitted", "இன்று", "நேற்று", "நாளை", "காலை", "பிற்பகல்", "மதியம்"]):
            return "pending_applications"
        elif any(w in msg for w in ["count", "number", "how many", "total", "no of", "no.", "எண்ணிக்கை", "மொத்தம்", "எத்தனை"]) or \
             any(w in msg for w in ["between", "from", "year", "during", "week", "month", "2025", "2026", "இடையே", "வரை"]):
            return "pending_applications"   # date-range / count query → fetch all in range
        elif any(w in msg for w in ["show", "list", "display", "view", "get", "fetch", "all", "with", "காட்டு", "பட்டியல்", "உடன்"]):
            return "pending_applications"

    # 2a. Field-specific queries (name, address, mobile, email, survey no, etc.) → application_status
    # These queries ask about specific applicant/application fields
    _field_keywords = [
        # English
        "name", "address", "mobile", "phone", "email", "status", "stage", "type",
        "applicant", "contact", "priority", "aadhaar", "date", "submission",
        "serial", "serial number", "serial_number", "can", "can number", "can_number",
        "patta", "patta number", "patta_number", "subdivision", "subdivision number",
        "current subdivision", "current_subdivision_number", "renewal", "renewal number",
        "renewal_number", "parent", "parent application", "parent_application_id",
        "role", "role id", "role_id", "user", "user id", "user_id", "department",
        "department_code", "service", "service_code", "district_code", "taluk_code",
        "village_code", "urban_unit_code", "ward_code", "block_code", "ward", "block",
        "charge", "charges", "fee", "fees", "cost", "csc_service_charge", "csc charge",
        "government_service_charge", "government charge", "ip", "ip address", "ip_address", "dispatch",
        "dispatch_date", "received", "received_date", "last updated", "last_updated_datetime",
        "generated_datetime", "source", "source_code", "source_name", "return_status",
        "workflow_state", "auto_mutated", "auto_mutated_flag", "is_auto_mutated",
        "igrs_auto_mutation_flag", "camp", "camp_flag", "camp_code", "camp_correction_id",
        "igrs", "form 6", "form6", "form 6 number", "igrs form 6", "igrs form 6 number", "igrs_form6_number",
        "reason", "declared reason", "declared_reason", "purpose", "survey number", "survey no", "survey_no",
        # Tamil
        "பெயர்", "நாமாகும்", "நாமம்", "முகவரி", "தொலைபேசி", "மின்னஞ்சல்",
        "நிலை", "கட்டம்", "வகை", "விண்ணப்பதாரர்", "தேதி", "வரிசை எண்",
        "பட்டா எண்", "உட்பிரிவு எண்", "கணக்கெண்", "சர்வே எண்", "கட்டணம்",
        "புதுப்பித்தல் எண்", "பயனர் ஐடி", "பங்கு ஐடி", "முகாம்", "படிவம் 6", "காரணம்",
        # Tanglish
        "peyar", "mugavari", "tholaipaesi", "minnanjal", "nilai", "kattam", "varisai en", "kaaranam"
    ]
    _interrogative = ["what", "which", "who", "where", "when", "give", "tell", "show",
                      "என்ன", "எந்த", "யார்", "எங்கே", "எப்போது", "காட்டு", "சொல்"]
    has_field = any(kw in msg for kw in _field_keywords)
    has_interrogative = any(kw in msg for kw in _interrogative)
    has_application_context = any(w in msg for w in ["application", "applicant", "விண்ணப்பம்", "விண்ணப்பங்கள்", "விண்ணப்பங்களை", "விண்ணப்பதாரர்", "ஆப்ளிகேஷன்"])
    _is_list_query = (
        any(w in msg for w in [
            "field visit", "field visits", "visits", "inspection", "inspections", "கள ஆய்வு",
            "applications", "விண்ணப்பங்கள்", "விண்ணப்பங்களை", "surveys", "கணக்கெண்கள்", "list", "all"
        ]) and any(w in msg for w in ["show", "list", "display", "get all", "fetch all", "all", "காட்டு", "பட்டியல்"])
        and not any(w in msg for w in ["what", "which", "who", "when", "என்ன", "எந்த", "யார்", "எப்போது"])
    )
    # Survey detail queries without application context -> survey_details
    _is_survey_query = bool(re.search(r'\bsurvey\s*\d+\b|\bகணக்கெண்\s*\d+\b', msg, re.IGNORECASE)) or (has(ta_survey) and not has_application_context and not _has_app_pattern)
    # "next available sub-division for survey 145" asks for the next number, not a
    # survey detail dump — the more specific intent wins.
    _wants_next_subdivision = has(ta_next) and (has(ta_subdivision) or "subdivision" in msg or "subdivisions" in msg)
    if _is_survey_query and _wants_next_subdivision:
        return "next_subdivision"
    if _is_survey_query and (has(ta_subdivision) or has(ta_detail) or has(ta_show) or has_interrogative or "subdivision" in msg or "subdivisions" in msg):
        return "survey_detail"

    # Route to application_status if asking about a specific field + has interrogative OR mentions application OR is a short field follow-up, but not a list query
    if has_field and (has_interrogative or has_application_context or len(words) <= 4 or prev_intent == "application_status") and not has(ta_overdue) and not has(ta_pending) and not _is_list_query:
        return "application_status"

    if has(ta_subdivision) and (has(ta_application) or has(ta_show)):
        # Sub-division applications are ISD applications (ISD = Involving Sub-Division).
        return "isd_applications"

    if has(ta_survey) and (has(ta_show) or has(ta_detail) or "surveys" in msg or "எண்கள்" in msg or ("number" in msg and not any(w in msg for w in ["applications", "விண்ணப்பங்கள்", "விண்ணப்பங்களை"]))):
        return "survey_detail"

    if has(ta_workload):
        return "officer_workload"

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

    # 2c. Specific overdue duration / status queries for an application or context reference
    # "how many days overdue", "how many days over due of prev application", "is it overdue"
    # Should route to application_status to calculate exact overdue days for that app/field visit.
    _asking_days_overdue = any(p in msg for p in [
        "how many days overdue", "how many days over due", "days overdue", "days over due",
        "how long overdue", "how many days late", "how overdue", "how many days delayed",
        "overdue of prev", "overdue of previous", "overdue for this", "overdue for that",
        "overdue of application", "overdue for application", "is it overdue", "is this overdue",
        "how many days", "how many day"
    ]) or (
        has(ta_overdue) and (
            has_interrogative or
            has_application_context or
            bool(re.search(r'\b(prev|previous|this|that|same|last|the|it)\b', msg))
        ) and not any(w in msg for w in ["all", "list", "show overdue", "display overdue", "overdue applications", "overdue list", "overdue count"])
    )
    if _asking_days_overdue:
        return "application_status"

    # 2d. Specific pending duration / status query for a specific application or context reference
    # "APP-2024-000022 how long it is pending", "how long is this app pending", "how many days pending"
    _asking_pending_duration = (
        any(p in msg for p in [
            "how long", "how many days pending", "pending for how long",
            "how long pending", "how long it is pending", "how long is it pending",
            "how many days since", "days pending", "duration pending",
            "எவ்வளவு நாள்", "எத்தனை நாள்", "எவ்வளவு நாட்கள்", "எத்தனை நாட்கள்",
            "நாள் ஆச்சு", "நாட்கள் ஆச்சு", "நிலுவை காலம்"
        ]) or (
            has(ta_pending) and (
                bool(re.search(r'(\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+|\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+|APP-\d{4}-\d{6}|(ISD|NISD|MERGE)/\w+/\d+/\d+)', msg, re.IGNORECASE)) or
                bool(re.search(r'\b(this|that|prev|previous|same|last|it)\b', msg, re.IGNORECASE))
            ) and any(w in msg for w in ["how long", "how many", "duration", "days", "since", "எவ்வளவு", "எத்தனை", "நாள்", "நாட்கள்"])
        )
    )
    if _asking_pending_duration:
        return "application_status"

    # 3. Overdue applications list
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

    # 16. Definition queries for NISD vs ISD & Service Codes (0153 / 0154)
    # Catch questions asking for definitions/explanations (e.g. "what is service code 0153?", "what does NISD mean?")
    _has_def_kw = any(w in msg for w in [
        "what", "என்ன", "difference", "வேறுபாடு", "mean", "stand for",
        "explain", "விளக்கம்", "define", "definition", "meaning"
    ])
    _has_explicit_list_action = bool(re.search(
        r'\b(show|list|display|view|fetch|get|give|count|how many|காட்டு|காண்பி|பட்டியல்)\b',
        msg, re.IGNORECASE
    ))
    if _has_def_kw and not _has_explicit_list_action and any(w in msg for w in ["0153", "0154", "service code", "service_code", "nisd", "isd", "merge"]):
        return "general_query"  # Force RAG retrieval for definition queries

    # 16b. Typed application lists — MUST come before the is_nisd_or_isd trap below.
    # "display nisd", "show isd", "show applications for code 0153", "show 0154 apps" → typed list.
    _has_action_16 = bool(re.search(
        r'\b(show|list|display|view|count|how many|number|no of|num|total|compare|both|all|get|fetch|give|find|details|summary|versus|vs|applications|application|app|apps)\b'
        r'|\band\b'   # "and" as a full word only
        r'|\bn\b',    # "n" as a standalone word ("isd n nisd")
        msg, re.IGNORECASE
    )) or any(w in msg for w in [
        "காட்டு", "காண்பி", "பட்டியல்", "எத்தனை", "எண்ணிக்கை", "தொகை", "ஒப்பிடு", "இரண்டும்", "விண்ணப்பம்", "விண்ணப்பங்கள்"
    ])
    if _has_action_16:
        # Use word-boundary regex so "nisd", "0153", "isd", "0154" match accurately
        _has_nisd_word  = bool(re.search(r'\b(nisd|0153)\b', msg))
        _has_isd_word   = bool(re.search(r'\b(isd|0154)\b', msg))
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

    # Catch prefix-based service code count/list queries BEFORE general_query fallback.
    # e.g. "how many service code in 161", "list codes starting with 015", "service codes in 016"
    _has_svc_kw = any(w in msg for w in ["service code", "service_code", "service codes", "கோட்", "சேவை குறியீடு"])
    _has_count_or_list_kw = bool(re.search(
        r'\b(how many|count|list|all|show|total|in|starting|start|begin|prefix|range|within|under|for)\b',
        msg, re.IGNORECASE
    ))
    _prefix_match = re.search(r'\b(0?1[5-9][0-9]|0?[0-9]{3})\b', msg)
    if _has_svc_kw and _has_count_or_list_kw and _prefix_match:
        return "service_code_lookup"

    # Catch remaining standalone service code / definition questions without action words
    if any(w in msg for w in ["0153", "0154", "service code", "service_code"]):
        return "general_query"

    # is_nisd_or_isd only fires for queries about a SPECIFIC application (e.g. "is 2026/0153/31/000001 nisd or isd?")
    # Guard: requires explicit application number pattern or application reference words
    _has_app_ref = bool(re.search(r'(\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+|\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+|APP-\d{4}-\d{6}|(ISD|NISD|MERGE)/\w+/\d+/\d+)', msg, re.IGNORECASE)) or any(
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
        is_geo_query    = any(w in msg for w in ["ambattur", "mylapore", "guindy", "egmore", "tambaram", "velachery", "chennai"])
        is_action_query = any(w in msg for w in ["how many", "howmuch", "show", "list",
                                                   "display", "view", "pending", "active",
                                                   "assigned", "count", "are there", "there are",
                                                   "no of", "number of", "num of", "count of", "total",
                                                   "காட்டு", "காண்பி", "பட்டியல்",
                                                   # Tamil: எத்தனை, காண்பி
                                                   "எத்தனை", "உள்ளன"])
        
        # Year/Month/Taluk + applications = pending_applications (e.g., "no of application in ambattur", "2026 applications")
        if is_app_query and (is_year_query or is_month_query or is_geo_query) and not has(ta_ward):
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
        return "survey_detail"

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
    """Extract application number (e.g. 2026/0153/31/000001, 2026/31/0153/000001, ISD/W1/2024/0001, or APP-2024-000015)."""
    cleaned = clean_message(message)
    app_match = re.search(
        r'\b\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+\b|\b\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+\b|\b(?:ISD|NISD|MERGE)/\w+/\d+/\d+\b|\bAPP-\d+-\d+\b',
        cleaned, re.IGNORECASE
    )
    if app_match:
        return app_match.group(0).upper()
    return None


def extract_application_numbers(message: str) -> List[str]:
    """Extract ALL application numbers from a message (supports multi-app queries like 'show details for A and B').
    Returns a list of unique application numbers in the order they appear."""
    cleaned = clean_message(message)
    matches = re.findall(
        r'\b\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+\b|\b\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+\b|\b(?:ISD|NISD|MERGE)/\w+/\d+/\d+\b|\bAPP-\d+-\d+\b',
        cleaned, re.IGNORECASE
    )
    seen = set()
    result = []
    for m in matches:
        upper = m.upper()
        if upper not in seen:
            seen.add(upper)
            result.append(upper)
    return result


def extract_taluk_name(message: str) -> Optional[str]:
    """Extract Taluk name (e.g. Ambattur, Mylapore, Guindy, Egmore, Tambaram, Velachery) from message."""
    if not message:
        return None
    cleaned = clean_message(str(message))
    known_taluks = ["ambattur", "mylapore", "guindy", "egmore", "tambaram", "velachery"]
    for tlk in known_taluks:
        if tlk in cleaned.lower():
            return tlk.capitalize()

    # Avoid matching schema column names or generic phrases
    if re.search(r'\btaluk\s+(?:code|name|no|number|details|information|level|list|data)\b', cleaned, re.IGNORECASE):
        return None

    match = re.search(r'\btaluk\s+(?:of\s+)?([A-Za-z\s]+?)(?:\s+taluk)?\b', cleaned, re.IGNORECASE)
    if match:
        val = match.group(1).strip().capitalize()
        if val.lower() not in ["code", "name", "no", "number", "details", "info", "level"]:
            return val

    match = re.search(r'([A-Za-z\s]+?)\s+taluk\b', cleaned, re.IGNORECASE)
    if match:
        val = match.group(1).strip().capitalize()
        if val.lower() not in ["code", "name", "no", "number", "details", "info", "level"]:
            return val

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


def extract_district_name(message: str) -> Optional[str]:
    """
    Extract district name from message, resolving district codes (01-38) to district names.
    District is the top of the jurisdiction hierarchy.
    """
    cleaned = clean_message(message)

    # 1. Check for explicit district code references like "district code 02", "district 02".
    # The "district" keyword is REQUIRED — without it every bare number 1-38 in the
    # message (e.g. "survey 14") was resolved to a district name.
    code_match = re.search(r'\bdistrict\s+(?:code\s+)?0*([1-9]|[1-3][0-8])\b', cleaned, re.IGNORECASE)
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

    # 3. Relative multi-word phrases (check longest first)
    if any(w in cleaned for w in ["day before yesterday", "day before ystd", "day before", "முந்தாநாள்", "நேற்று முன்நாள்", "நேற்றுக்கு முன்நாள்"]):
        d_prev = today - timedelta(days=2)
        return d_prev, d_prev

    if any(w in cleaned for w in ["day after tomorrow", "day after tommorrow", "day after tmrw", "நாளை மறுநாள்"]):
        d_after = today + timedelta(days=2)
        return d_after, d_after

    if any(w in cleaned for w in ["tomorrow", "tommorrow", "tmrw", "நாளை"]):
        tmrw = today + timedelta(days=1)
        return tmrw, tmrw

    if any(w in cleaned for w in ["today", "tonight", "இன்று"]):
        return today, today

    if any(w in cleaned for w in ["yesterday", "நேற்று"]):
        ystd = today - timedelta(days=1)
        return ystd, ystd

    if any(w in cleaned for w in ["morning", "afternoon", "evening", "காலை", "பிற்பகல்", "மதியம்", "மாலை"]):
        return today, today

    if any(w in cleaned for w in ["this week", "இந்த வாரம்"]):
        start_w = today - timedelta(days=today.weekday())
        end_w = start_w + timedelta(days=6)
        return start_w, end_w

    if any(w in cleaned for w in ["next week", "அடுத்த வாரம்"]):
        start_w = today + timedelta(days=(7 - today.weekday()))
        end_w = start_w + timedelta(days=6)
        return start_w, end_w

    if any(w in cleaned for w in ["last week", "past week", "கடந்த வாரம்", "சென்ற வாரம்"]):
        start_w = today - timedelta(days=today.weekday() + 7)
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

    if any(w in cleaned for w in ["last month", "past month", "கடந்த மாதம்", "சென்ற மாதம்"]):
        if today.month == 1:
            start_m = date(today.year - 1, 12, 1)
        else:
            start_m = date(today.year, today.month - 1, 1)
        _, last_day = calendar.monthrange(start_m.year, start_m.month)
        end_m = start_m.replace(day=last_day)
        return start_m, end_m

    # 4. Weekday names (e.g. "on Monday", "this Friday", "next Tuesday", "வெள்ளிக்கிழமை")
    weekday_map = {
        "monday": 0, "mon": 0, "திங்கள்": 0, "திங்கட்கிழமை": 0,
        "tuesday": 1, "tue": 1, "செவ்வாய்": 1, "செவ்வாய்க்கிழமை": 1,
        "wednesday": 2, "wed": 2, "புதன்": 2, "புதன்கிழமை": 2,
        "thursday": 3, "thu": 3, "வியாழன்": 3, "வியாழக்கிழமை": 3,
        "friday": 4, "fri": 4, "வெள்ளி": 4, "வெள்ளிக்கிழமை": 4,
        "saturday": 5, "sat": 5, "சனி": 5, "சனிக்கிழமை": 5,
        "sunday": 6, "sun": 6, "ஞாயிறு": 6, "ஞாயிற்றுக்கிழமை": 6
    }
    for day_name, day_idx in weekday_map.items():
        if re.search(r'\b(?:on\s+|this\s+|next\s+)?' + re.escape(day_name) + r'\b', cleaned):
            days_ahead = (day_idx - today.weekday()) % 7
            if "next" in cleaned and days_ahead == 0:
                days_ahead = 7
            elif "next" in cleaned:
                days_ahead += 7
            target_date = today + timedelta(days=days_ahead)
            return target_date, target_date

    # 5. Natural date phrases like "25th August", "August 25", "25 Aug 2026", "ஆகஸ்ட் 25"
    month_name_map = {
        "january": 1, "jan": 1, "ஜனவரி": 1,
        "february": 2, "feb": 2, "பிப்ரவரி": 2,
        "march": 3, "mar": 3, "மார்ச்": 3,
        "april": 4, "apr": 4, "ஏப்ரல்": 4,
        "may": 5, "மே": 5,
        "june": 6, "jun": 6, "ஜூன்": 6,
        "july": 7, "jul": 7, "ஜூலை": 7,
        "august": 8, "aug": 8, "ஆகஸ்ட்": 8, "ஆக": 8,
        "september": 9, "sep": 9, "செப்டம்பர்": 9,
        "october": 10, "oct": 10, "அக்டோபர்": 10,
        "november": 11, "nov": 11, "நவம்பர்": 11,
        "december": 12, "dec": 12, "டிசம்பர்": 12
    }
    for m_name, m_num in month_name_map.items():
        m1 = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+' + re.escape(m_name) + r'(?:\s+(\d{4}))?\b', cleaned)
        if m1:
            day_val = int(m1.group(1))
            year_val = int(m1.group(2)) if m1.group(2) else today.year
            try:
                d_val = date(year_val, m_num, day_val)
                return d_val, d_val
            except ValueError:
                pass

        m2 = re.search(r'\b' + re.escape(m_name) + r'\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?\b', cleaned)
        if m2:
            day_val = int(m2.group(1))
            year_val = int(m2.group(2)) if m2.group(2) else today.year
            try:
                d_val = date(year_val, m_num, day_val)
                return d_val, d_val
            except ValueError:
                pass

    # 6. Relative N days
    days_match = re.search(r'\b(?:next|coming|last|past)\s+(\d+)\s+days?\b', cleaned)
    if days_match:
        n_days = int(days_match.group(1))
        if any(w in cleaned for w in ["last", "past"]):
            return today - timedelta(days=n_days), today
        else:
            return today, today + timedelta(days=n_days)

    # 7. Year-range: "between 2025 and 2026", "2025 n 2026", "from 2025 to 2026",
    #                "2025 to 2026", "2025-2026"
    year_range_match = re.search(
        r'\b(20\d{2})\s*(?:to|and|n|&|[-–])\s*(20\d{2})\b', cleaned
    )
    if year_range_match:
        y1 = int(year_range_match.group(1))
        y2 = int(year_range_match.group(2))
        start_y, end_y = min(y1, y2), max(y1, y2)
        _, last_day = calendar.monthrange(end_y, 12)
        return date(start_y, 1, 1), date(end_y, 12, last_day)

    return None, None

