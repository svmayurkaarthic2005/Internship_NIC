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
from backend.utils.fuzzy import (
    extract_month_from_text,
    extract_tokens,
    is_qwerty_first_letter_typo,
    is_token_typo_match,
    match_phrase,
    normalize_relative_date_tokens,
    normalize_text,
)

logger = get_logger(__name__)

# `\b` is unreliable around Tamil: a word ending in a virama-marked bare
# consonant (its trailing ் combining mark is not `\w`) never closes the
# boundary, so `\bகணக்கெண்\b` matches neither "கணக்கெண் 5" nor "என் கணக்கெண்".
# Lookaround against the Tamil block (஀-௿) is a boundary that works both
# ways -- it still refuses to match a stem inside a longer word.
_TA_NB, _TA_NA = r'(?<![஀-௿])', r'(?![஀-௿])'

# Initialize Ollama LLM
# num_predict controls the maximum number of output tokens Ollama will generate.
# Without it, Ollama defaults to a very small cap (num_predict=128 on many
# builds), which cut off answers mid-sentence for anything longer than a short
# reply. num_ctx is raised alongside it so the larger prompt (conversation
# history + structured data + document context) doesn't get silently truncated
# on the input side either.
llm = ChatOllama(
    model=settings.LLM_MODEL,
    base_url=settings.OLLAMA_BASE_URL,
    # Keep the model loaded between questions: an idle unload cost a 15 s reload
    # on the next document question.
    keep_alive=settings.LLM_KEEP_ALIVE,
    temperature=0.1,
    num_predict=settings.LLM_NUM_PREDICT,
    num_ctx=settings.LLM_NUM_CTX
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
        "varum", "seri", "romba", "nalla", "panno", "kuduthu",
        "venuma", "venduma", "thevaiya", "ennanu", "nandri", "nanri",
        "kaatu", "kami", "kaami", "elam", "ellam", "ellaa", "ellaam", "sollu", "avatroda", "evlo", "yaru", "yaaru"
    }
    words = set(re.findall(r'\b[a-zA-Z]+\b', text.lower()))
    if words.intersection(tanglish_words):
        return "tanglish"
    # A slipped letter in a Tanglish word ("ena", "edgu", "solu", "kaatuu") must not
    # flip the reply to English.
    for w in words:
        if len(w) >= 4 and any(len(t) >= 4 and is_token_typo_match(w, t) for t in tanglish_words):
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
                filtered = similarity_search(query, n_results=n_results, where_filter=where_filter)
                # Always also run an unfiltered search and merge. A Tamil-only
                # document is a legitimate answer to an English question about it
                # (and vice versa), but cross-lingual embedding similarity is
                # weak enough that the language-matched English chunks otherwise
                # crowd it out. Merge keeps the best of both, filtered first.
                unfiltered = similarity_search(query, n_results=n_results)
                seen, merged = set(), []
                for r in filtered + unfiltered:
                    key = (r.get("content") or "")[:80]
                    if key not in seen:
                        seen.add(key)
                        merged.append(r)
                results = merged[:n_results]
                if not filtered:
                    logger.warning(
                        f"Language filter {where_filter} returned 0 results for query "
                        f"'{query[:60]}'; using unfiltered results."
                    )
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


_GEO_SCOPE_RE = re.compile(
    r"\bin ((?:Ward|Block)\s+\S+(?:\s+/\s+(?:Ward|Block)\s+\S+)*)")


def _geo_scope_label(query_type: str) -> str:
    """" in Block 0015" when the query_type carries a ward/block scope.

    Every list intro says which ward or block it covers, for the same reason a
    month-scoped one names the month: without it the officer cannot tell a
    scoped count from the whole queue.
    """
    m = _GEO_SCOPE_RE.search(query_type or "")
    return f" in {m.group(1)}" if m else ""


# "who is handling ward 102", "which officer covers block 0015", "how many
# wards are there", "list the wards in my town".
_WARD_DIRECTORY_RE = re.compile(
    r"\b(?:who|which\s+officer|whose)\b[^?]*\b(?:handl\w*|in\s+charge|charge\s+of|cover\w*|"
    r"responsible|assigned\s+to|posted|looks?\s+after|manag\w*)\b[^?]*\b(?:ward|block)\b"
    r"|\b(?:ward|block)\b[^?]*\b(?:who|which\s+officer)\b[^?]*\b(?:handl\w*|in\s+charge|cover\w*|"
    r"responsible|assigned|posted|looks?\s+after)\b"
    r"|\bhow\s+many\s+(?:wards?|blocks?)\b(?![^?]*\bapplication)"
    r"|\b(?:list|show|name)\s+(?:me\s+)?(?:all\s+|the\s+|my\s+)*(?:wards?|blocks?)\b"
    r"(?![^?]*\b(?:application|survey|pending|overdue)\b)"
    r"|\bhow\s+many\s+officers?\b"
    # Singular name lookup: "what is the name of ward 102", "ward 102 name",
    # "what is ward 2 called". The name IS the one ward-master field the
    # projection keeps, and `get_ward_directory(ward_number=...)` returns it;
    # without this the question fell to `application_status` and was answered
    # with "give me an application number". Requires a ward/block number so an
    # application-scoped "name" question is untouched.
    r"|\bname\s+of\s+(?:the\s+)?(?:ward|block)\s+(?:no\.?\s*|number\s*)?[A-Za-z]?\d"
    r"|\b(?:ward|block)\s+(?:no\.?\s*|number\s*)?[A-Za-z]?\d+\s*(?:'s)?\s+(?:name|called|named)\b"
    r"|\bwhat\s+is\s+(?:the\s+)?(?:ward|block)\s+[A-Za-z]?\d+\s+(?:called|named)\b"
    r"|(?:வார்டு|பிளாக்)\s*\d+\s*(?:இன்|the)?\s*பெயர்"
    r"|யார்[^?]*(?:வார்டு|பிளாக்)"
    r"|எத்தனை\s*(?:வார்டு|பிளாக்)",
    re.IGNORECASE,
)


# "which block has the most applications", "applications per block", "block
# wise count", "how many applications in each ward".
_BLOCK_BREAKDOWN_RE = re.compile(
    r"\b(?:which|what)\s+(?:block|ward)\b[^?]*\b(?:most|least|highest|lowest|maximum|minimum|max|min)\b"
    r"|\b(?:most|least|highest|lowest)\b[^?]*\bin\s+(?:which|what)\s+(?:block|ward)\b"
    r"|\b(?:per|each|every|by|wise)\s*[-\s]?(?:block|ward)\b"
    r"|\b(?:block|ward)\s*[-\s]?wise\b"
    r"|\bbreak\s*down\b[^?]*\b(?:block|ward)\b"
    # No closing boundary on பிளாக்/வார்டு -- Tamil case suffixes (locative
    # "பிளாக்கில்" = "in block") attach directly with no separator, the same
    # substring convention the rest of this file already uses for these two
    # stems (e.g. _BARE_GEO_SCOPE_RE below).
    rf"|{_TA_NB}எந்த{_TA_NA}\s+(?:பிளாக்|வார்டு)",
    re.IGNORECASE,
)


# "block 0015", "in ward 2", "வார்டு 2" -- a scope and nothing else.
_BARE_GEO_SCOPE_RE = re.compile(
    r"(?:in|for|from|at|under)?\s*"
    r"(?:block|ward|பிளாக்|வார்டு)\s*(?:no\.?|number|எண்)?\s*[:\-#]?\s*"
    r"[A-Za-z]?\d+\s*[?.!]*",
    re.IGNORECASE,
)


def _proj_has(alts: str, text: str) -> bool:
    """Column-projection cue test: same as `re.search(r'\\b(alts)\\b', text)`,
    but Tamil-safe per alternative.

    A single shared `\\b(...)\\b` around a mixed English/Tamil OR-list never
    closes next to a Tamil vowel sign or virama (see `_word_bounds` below), so
    every Tamil alternative in these lists was dead: an officer asking "பெயர்,
    நிலை, தேதி காட்டு" ("show name, status, date") for a specific column
    layout got the untouched default table back, silently. Each alternative
    gets its own correct boundary here instead of one shared `\\b`.
    """
    for alt in alts.split("|"):
        left, right = _word_bounds(alt)
        if re.search(f"{left}{alt}{right}", text, re.IGNORECASE):
            return True
    return False


# How a submission channel reads in a table cell.
_CHANNEL_CELL = {"CSC": "CSC", "sub_registrar": "Sub-Registrar",
                 "citizen": "Citizen"}


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

    # Strip conversational fillers that confuse field keyword extraction 
    # (e.g. "can you" matching the "can" column).
    uq = re.sub(r'\b(?:can|could|would|will)\s+(?:you|we|i|someone|anyone|u)\b', ' ', uq)
    uq = re.sub(r'\b(?:please|pls|plz|kindly)\b', ' ', uq)

    # Officer wording for fields the schema names differently. Normalising here
    # means both the projection gate below and the column picks see them:
    # "what is the position of <app>" is a status question, and "when did we
    # receive <app>" is a submission-date question. Without this the record was
    # fetched and then reported as "I could not find that particular detail".
    uq = re.sub(r'\b(position|standing|where it stands|where does it stand)\b', 'status', uq)
    uq = re.sub(r'\b(receive|received|receipt|came in|filed on)\b', 'submitted', uq)
    uq = re.sub(r'\b(pattadar|pattadars|patta holder)\b', 'patta', uq)

    # A geography word carrying a value is a FILTER, not a requested column:
    # "show applications for block 0015" asks for the block's applications in
    # the normal table, while "show application no and block" asks for the block
    # column. Without this, the filter counted as a field request and the answer
    # came back as a two-column "Application No. | Block" table with every other
    # detail dropped.
    uq = re.sub(
        r'\b(?:in|for|from|of|at|under)?\s*'
        r'(?:block|ward|taluk|town|district)\s*'
        r'(?:no\.?|number|code)?\s*'
        r'(?:\d+[a-z]?|this|that|my|same|current)\b',
        ' ', uq)
    # The same for a named area: "in taluk thoothukudi" / "in thoothukudi taluk".
    uq = re.sub(r'\b(?:in|for|from|at|under)\s+(?:the\s+)?'
                r'(?:taluk|town|district)\s+[a-z]+\b', ' ', uq)
    uq = re.sub(r'\b(?:in|for|from|at|under)\s+(?:the\s+)?'
                r'[a-z]+\s+(?:taluk|town|district)\b', ' ', uq)

    # Strip channel and source filters
    uq = re.sub(
        r'\b(?:submitted|received|filed|created|made|sent|came in)?\s*'
        r'(?:from|by|through|via|at)\s*'
        r'(?:sro|csc|citizen|igrs|online)\b',
        ' ', uq)
    
    # Strip application type filters
    uq = re.sub(r'\b(?:of\s+)?(?:type\s+)?(?:isd|nisd|merge)\b', ' ', uq)
    
    # Strip status filters
    uq = re.sub(r'\b(?:with\s+)?(?:status\s+)?(?:pending|approved|rejected|in progress|completed|escalated)\b', ' ', uq)
    
    # Strip date scope filters. This leaves hanging verbs (e.g. "submitted today" -> "submitted") 
    # so we also strip the verb if it was modifying a date scope.
    _before_date = uq
    uq = strip_date_scope_phrases(uq)
    if uq != _before_date:
        uq = re.sub(r'\b(?:submitted|received|filed|created|made|sent|came in)\b(?=\s*$)', '', uq)

    # Clean message to remove trailing punctuation
    uq = re.sub(r'[\.\?,!]+$', '', uq.strip())

    # ── Explicit bail-out: bare "show/list/display applications" with no specific field terms ──
    # These are generic listing intents, not column-projection requests.
    _bare_list_pattern = re.compile(
        r'^\s*(show|list|display|view|get|give|provide)?\s*'
        r'(all\s+)?(my\s+)?(the\s+|these\s+|those\s+)?(pending\s+|in.?progress\s+|overdue\s+)?'
        r'(applications?|apps?)\s*$'
    )
    if _bare_list_pattern.match(uq):
        return None

    has_display_verb = _proj_has(r'display|show|list|select|view|give|get|provide|format|columns?|fields?', uq)
    has_only = _proj_has(r'only|alone|மட்டும்', uq)
    has_with_fields = _proj_has(r'with|having|along with|உடன்', uq)
    has_and_status = bool(re.search(r'\b(and|n|&|\+)\s+(status|type|date|name|stage|mobile|address|survey|block|ward|channel|source)\b', uq)) or "n n status" in uq
    has_specific_fields = bool(re.search(r'\b(application no|app no)\s+(and|n|with)\s+(status|type|date|name|stage|isd|nisd|merge)\b', uq))

    # Specific non-application field keywords (exclude generic app/no/number words)
    _specific_field_kws = [
        "applicant", "name", "mobile", "address", "type", "survey", "subdivision",
        "status", "stage", "date", "submitted", "block", "ward", "taluk", "town", "district",
        "patta", "sale deed", "reason", "priority", "can", "service code", "taluk code",
        "area", "sqm", "sq", "square", "channel", "source"
    ]
    # Check if multiple recognized schema field names are present
    field_hits = sum(1 for kw in _specific_field_kws
                     if re.search(r'\b' + re.escape(kw) + r'\b', uq))

    # has_display_verb alone is NOT sufficient — it must be paired with at least one
    # specific field keyword to count as a projection request.
    # (Prevents "show applications" / "list apps" from being treated as projection)
    display_verb_with_specific_field = has_display_verb and field_hits >= 1

    # "which ward is <app> in", "what is the status of <app>", "when was <app>
    # submitted" name exactly one field and no display verb. They are the most
    # common way an officer asks about a file, so a question shape plus one
    # field keyword counts as a projection.
    _is_question = bool(re.match(r'^\s*(what|which|when|who|whose|where|is|was|has|have|does|did)\b', uq))
    single_field_question = _is_question and field_hits >= 1

    is_projection = (
        display_verb_with_specific_field
        or has_only
        or has_with_fields
        or has_and_status
        or has_specific_fields
        or single_field_question
        or (field_hits >= 2)
    )
    if not is_projection:
        return None

    cols = []
    has_app_no = _proj_has(r'app|apps|application|applications|application no|application number|app no|app number|no|number|விண்ணப்ப எண்', uq)
    if has_app_no or not any(k in uq for k in ["name", "status", "type", "stage", "date", "block"]) and not re.search(r'\b0\d{3}\b', uq):
        cols.append("application_no")
        
    if _proj_has(r'applicant name|applicant\'s name|applicant|applicants|பெயர்|peyar', uq) or (
        bool(re.search(r'\bname\b', uq)) and not re.search(r'\b(district name|taluk name|town name)\b', uq)
    ):
        cols.append("applicant_name")
    if _proj_has(r'mobile|phone|contact|cell|தொலைபேசி|கைபேசி', uq):
        cols.append("mobile")
    if _proj_has(r'address|addr|முகவரி', uq):
        cols.append("address")
    if _proj_has(r'type|application type|types|isd|nisd|merge|வகை', uq):
        cols.append("type")
    if _proj_has(r'survey|survey no|survey number|surveys|கணக்கெண்', uq):
        cols.append("survey_no")
    if _proj_has(r'subdivision|subdivisions|sub-division|sub-divisions|sub\s*division|sub\s*divisions|subdivision_number|current_subdivision_number|உட்பிரிவு', uq):
        cols.append("subdivisions")
    if _proj_has(r'area|area sq|area sqm|total area|merge area|sqm|sq\.m|sq m|sq ft|square|பரப்பளவு|சதுர மீட்டர்', uq):
        cols.append("area_sqm")
    if _proj_has(r'status|current status|statuses|நிலை', uq):
        cols.append("status")
    if _proj_has(r'stage|current stage|stages|workflow|workflow_state|workflow state|கட்டம்', uq):
        cols.append("stage")
    if _proj_has(r'overdue days|days overdue|காலதாமத நாட்கள்', uq):
        cols.append("overdue_days")
    if _proj_has(r'submitted|submission date|submission|date|dates|application date|application_date|தேதி|நாள்', uq) \
            and not _proj_has(r'update|updated|last_updated', uq) \
            and not re.search(r'submission\s+(?:channel|source|mode|route)', uq):
        cols.append("submitted")
    if _proj_has(r'block code|block_code', uq):
        cols.append("block_code")
    elif _proj_has(r'block|blocks|தொகுதி', uq):
        cols.append("block")
    if _proj_has(r'ward code|ward_code', uq):
        cols.append("ward_code")
    elif _proj_has(r'ward|wards|வார்டு', uq):
        cols.append("ward")
    if _proj_has(r'town|towns|urban_unit_code|நகரம்', uq):
        cols.append("town")
    if _proj_has(r'taluk code|taluk_code', uq):
        cols.append("taluk_code")
    elif _proj_has(r'taluk|taluks|தாலுகா', uq):
        cols.append("taluk")
    if _proj_has(r'district|districts|district_code|மாவட்டம்', uq):
        cols.append("district")
    if _proj_has(r'patta|patta no|patta number|patta_number|பட்டா', uq):
        cols.append("patta_no")
    if _proj_has(r'sale deed|sale deed no|sale deed number|பத்திரம்|கிரய பத்திரம்', uq):
        cols.append("sale_deed_no")
    if _proj_has(r'sale deed registered|deed status|deed registered', uq):
        cols.append("sale_deed_reg")
    if _proj_has(r'reason|declared reason|declared_reason|காரணம்', uq):
        cols.append("declared_reason")
    if _proj_has(r'field visit date|visit date|inspection date|field_visit_date|ஆய்வு தேதி', uq):
        cols.append("field_visit_date")
    if _proj_has(r'priority|முன்னுரிமை', uq):
        cols.append("priority")
    if _proj_has(r'notes|remarks|குறிப்பு', uq):
        cols.append("notes")
    if _proj_has(r'can|can no|can number|can_number', uq):
        cols.append("can_no")
    if _proj_has(r'service code|service_code', uq):
        cols.append("service_code")
    if _proj_has(r'submission channel|channel|channels|source|sources|வழி', uq):
        cols.append("channel")
    if not cols:
        return None
    if "application_no" not in cols:
        cols.insert(0, "application_no")
    return cols


def _get_projected_field_visit_columns(user_query: str):
    uq = user_query.lower()
    has_only = _proj_has(r'only|alone|மட்டும்', uq)
    has_with_fields = _proj_has(r'with|having|along with|உடன்', uq)
    has_and_field = bool(re.search(r'\b(and|n|&|\+)\s+(status|type|date|scheduled date|name|mobile|address|survey|block)\b', uq))
    
    is_projection = has_only or has_with_fields or has_and_field
    if not is_projection:
        return None
        
    cols = []
    if _proj_has(r'app|apps|application|applications|application number|app no|no|number|விண்ணப்ப எண்', uq):
        cols.append("application_number")
    if _proj_has(r'applicant name|applicant\'s name|applicant|applicants|பெயர்', uq) or (
        bool(re.search(r'\bname\b', uq)) and not re.search(r'\b(district name|taluk name|town name)\b', uq)
    ):
        cols.append("applicant_name")
    if _proj_has(r'mobile|phone|contact|cell|தொலைபேசி', uq):
        cols.append("mobile")
    if _proj_has(r'address|addr|முகவரி', uq):
        cols.append("address")
    if _proj_has(r'survey|survey no|survey number|கணக்கெண்', uq):
        cols.append("survey_no")
    if _proj_has(r'block|தொகுதி', uq):
        cols.append("block")
    if _proj_has(r'type|isd|nisd|merge|வகை', uq):
        cols.append("type")
    if _proj_has(r'status|நிலை', uq):
        cols.append("status")
    if _proj_has(r'scheduled date|visit date|date|dates|scheduled|தேதி', uq):
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


def _money(val: Any) -> str:
    """Render a rupee amount the way the fee register does: ₹600.00."""
    if val is None or val == "":
        return "N/A"
    try:
        return f"\u20b9{float(val):,.2f}"
    except (TypeError, ValueError):
        return _e(val)


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


# ─────────────────────────────────────────────────────────────────────────────
# Jurisdiction focus
# ─────────────────────────────────────────────────────────────────────────────
# "What is my district?" and "what is my jurisdiction?" are different questions.
# Both route to the jurisdiction_summary intent (they need the same lookup), but
# the first one wants a single value back, not the whole seven-row card. The
# focus is the one jurisdiction level the officer actually named; when they name
# none, or more than one, or ask for the jurisdiction/area as a whole, there is
# no focus and the full summary is the right answer.
_JUR_FOCUS_WORDS = {
    "district": ["district", "districts", "dist", "மாவட்டம்", "மாவட்டங்கள்",
                 "மாவட்டம்தான்", "maavattam", "mavattam"],
    "taluk":    ["taluk", "taluks", "taluka", "taluq", "thaluk", "thaluka",
                 "தாலுகா", "தாலுக்கா", "தாலூகா"],
    "town":     ["town", "towns", "நகரம்", "நகரங்கள்"],
    "ward":     ["ward", "wards", "வார்டு", "வார்டுகள்"],
    "block":    ["block", "blocks", "பிளாக்", "தொகுதி", "தொகுதிகள்"],
}
# Naming the jurisdiction (or the summary) as a whole overrides any level word:
# "what wards are in my jurisdiction" wants the card, not just the ward row.
_JUR_WHOLE_WORDS = ["jurisdiction", "summary", "coverage", "overview",
                    "assigned area", "my area", "அதிகார வரம்பு", "சுருக்கம்"]


# ─────────────────────────────────────────────────────────────────────────────
# Sort order
# ─────────────────────────────────────────────────────────────────────────────
# "display applications in ascending order" / "newest first" / "sort by
# application number descending". Returned as (field, direction) and applied in
# SQL by get_officer_applications, so the order is the database's, not a
# re-shuffle of one page of rows.
_SORT_ASC_WORDS = [
    "ascending", "ascend", "asc order", " asc", "oldest first", "oldest",
    "earliest first", "earliest", "old to new", "increasing", "low to high",
    "smallest first", "a to z", "chronological",
    # "old application" / "old apps" -- the bare adjective in front of a known
    # noun is safely scoped; checked with a word boundary in extract_sort_order.
    " old ", "old application", "old app",
    "ஏறுவரிசை", "ஏறு வரிசை", "பழைய",
]
_SORT_DESC_WORDS = [
    "descending", "descend", "desc order", " desc", "newest first", "newest",
    "latest first", "latest", "most recent", "recent first", "new to old",
    "decreasing", "high to low", "largest first", "z to a",
    "reverse order", "reverse chronological",
    # "new application" / "new apps" -- same scoping as "old" above.
    "new application", "new app",
    "இறங்குவரிசை", "இறங்கு வரிசை", "புதிய",
]
_SORT_FIELDS = [
    ("application_number", ["application number", "application no", "app number",
                            "app no", "file number", "application id", "app id",
                            "application_number", "விண்ணப்ப எண்"]),
    ("priority", ["priority", "prioritised", "prioritized", "urgency", "urgent",
                  "முன்னுரிமை", "அவசர"]),
    ("submission_date", ["submission date", "submitted", "submission", "date",
                         "submitted date", "submission_date", "submitted data",
                         "filed", "received", "தேதி", "சமர்ப்பி"]),
    ("status", ["status", "நிலை"]),
    ("application_type", ["type", "வகை"]),
    ("ward_number", ["ward", "வார்டு"]),
    ("block_number", ["block", "பிளாக்"]),
    ("survey_no", ["survey", "சர்வே"]),
    ("fee_amount", ["fee", "amount", "கட்டணம்"]),
    ("applicant_name", ["applicant", "name", "பெயர்"]),
]


# "list applications by priority", "show them by submitted date" -- a naming of
# the key with no "sort"/"order" verb in sight. Restricted to the sortable
# fields so "submitted by CSC" and "rejected by the ZDT" are not read as sorts.
_SORT_BY_FIELD_RE = re.compile(
    r"\bby\s+(?:the\s+)?(?:priority|urgency|status|type|date|submission\s*date|"
    r"submitted\s*date|submission|submitted|application\s*(?:number|no\.?)|"
    r"app\s*(?:number|no\.?))\b"
)


# ─────────────────────────────────────────────────────────────────────────────
# "show the first two applications" -- a cap on how many rows are wanted
# ─────────────────────────────────────────────────────────────────────────────
# A listing request may name how much of the list it wants. Without this the
# cap was simply dropped: "show the first two approved applications" answered
# "Found 64 application(s)" and printed all 64 -- the request read back as
# though it had been honoured, which is the failure this file guards against
# everywhere else.
#
# "first" and "last" name an END of the list, not a sort: the order is
# whatever the listing already uses (oldest submission first by default, or
# whatever extract_sort_order asked for), and "last N" is the final N rows of
# THAT order. Naming a sort as well still works -- "the latest 3" sorts
# descending and then takes the first 3, which is the same three rows.
_LIMIT_HEAD_WORDS = r"first|top|initial|earliest|starting|mudhal|muthal|மு‌தல்|முதல்"
_LIMIT_TAIL_WORDS = (r"last|latest|newest|final|bottom|most\s+recent|recent|"
                     r"kadaisi|கடைசி|சமீபத்திய|இறுதி")
_LIMIT_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "couple": 2, "few": 3,
    "onnu": 1, "rendu": 2, "moonu": 3, "moonru": 3, "naalu": 4, "anju": 5,
    "ainthu": 5, "aaru": 6, "pathu": 10,
    "ஒன்று": 1, "இரண்டு": 2, "மூன்று": 3, "நான்கு": 4, "ஐந்து": 5,
    "ஆறு": 6, "பத்து": 10,
}
_LIMIT_NUM_RE = "|".join(sorted(_LIMIT_NUMBER_WORDS, key=len, reverse=True))

# Tamil is bounded by lookarounds, never by ``\b``. A Tamil word ends in a
# combining vowel sign or a virama, neither of which is a word character, so
# ``\b`` finds no boundary after "இரண்டு" and the match fails -- the same trap
# CLAUDE.md documents for the comparison parser and the follow-up layer.
_TA = r"\u0B80-\u0BFF"
_LB = rf"(?<![\w{_TA}])"
_RB = rf"(?![\w{_TA}])"

# "first two applications", "top 5", "முதல் இரண்டு"
_LIMIT_HEAD_RE = re.compile(
    rf"{_LB}(?:{_LIMIT_HEAD_WORDS}){_RB}\s*(?:of\s+)?(?:the\s+)?"
    rf"(?P<n>\d{{1,3}}|{_LIMIT_NUM_RE}){_RB}",
    re.IGNORECASE)
_LIMIT_TAIL_RE = re.compile(
    rf"{_LB}(?:{_LIMIT_TAIL_WORDS}){_RB}\s*(?:of\s+)?(?:the\s+)?"
    rf"(?P<n>\d{{1,3}}|{_LIMIT_NUM_RE}){_RB}",
    re.IGNORECASE)
# "the 3 most recent", "2 latest" -- the count leads instead of following.
_LIMIT_TAIL_REV_RE = re.compile(
    rf"{_LB}(?P<n>\d{{1,3}}|{_LIMIT_NUM_RE})\s+(?:{_LIMIT_TAIL_WORDS}){_RB}",
    re.IGNORECASE)

# "last 2 months", "first 3 days", "top 5 wards" -- the count belongs to a
# period or to some other thing entirely, and capping the row count there
# would answer a question nobody asked. A unit right after the number
# disqualifies the match.
_LIMIT_NOT_ROWS_RE = re.compile(
    r"^\s*(?:days?|weeks?|months?|years?|quarters?|hours?|minutes?|working\s+days?|"
    r"wards?|blocks?|towns?|taluks?|districts?|surveys?|survey\s+numbers?|"
    r"subdivisions?|sub-divisions?|owners?|officers?|streets?|pattas?|"
    r"நாட்க\w*|வார\w*|மாத\w*|ஆண்டு\w*|வார்டு\w*)",
    re.IGNORECASE)


# Singular superlative + known application noun: "newest application",
# "newest completed application", "oldest rejected app" etc.
# Up to 3 modifier words (status, type, channel) are allowed between the
# superlative and the noun so the officer can scope by status or type.
# Numbered patterns ("newest 3") and ordinal patterns ("2nd newest") are
# checked first in extract_result_limit, so they are never swallowed here.
_MODIFIER_WORDS = (
    r"approved|rejected|completed|pending|in.progress|escalated|"
    r"isd|nisd|merge|overdue|active|all|my|the|an?|new|old"
)
_SINGULAR_TAIL_RE = re.compile(
    rf"{_LB}(?:newest|latest|most\s+recent|recent){_RB}"
    rf"(?:\s+(?:{_MODIFIER_WORDS})){{0,3}}"
    rf"\s+(?:the\s+|my\s+)?(?:application|app|file|record|field\s+visit|visit|inspection|\u0bb5\u0bbf\u0ba3\u0bcd\u0ba3\u0baa\u0bcd\u0baa\u0bae\u0bcd|\u0b95\u0bb3\u0bcd\u0020\u0b86\u0baf\u0bcd\u0bb5\u0bc1)\b",
    re.IGNORECASE)
_SINGULAR_HEAD_RE = re.compile(
    rf"{_LB}(?:oldest|earliest){_RB}"
    rf"(?:\s+(?:{_MODIFIER_WORDS})){{0,3}}"
    rf"\s+(?:the\s+|my\s+)?(?:application|app|file|record|field\s+visit|visit|inspection|\u0bb5\u0bbf\u0ba3\u0bcd\u0ba3\u0baa\u0bcd\u0baa\u0bae\u0bcd|\u0b95\u0bb3\u0bcd\u0020\u0b86\u0baf\u0bcd\u0bb5\u0bc1)\b",
    re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────────────────
# Ordinal position queries: "2nd newest", "3rd oldest", "second latest"
# ─────────────────────────────────────────────────────────────────────────────
# The officer wants the Nth item in the sorted list, not the first/last N.
# Return (n, "head_nth") so _apply_result_limit takes rows[n-1:n] after
# the sort that extract_sort_order already applied.
_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5,
    "6th": 6, "7th": 7, "8th": 8, "9th": 9, "10th": 10,
    # Tanglish
    "mudhalvadhu": 1, "irandavadhu": 2, "moondravadhu": 3, "naanvadhu": 4,
    "ainjvadhu": 5,
    # Tamil
    "முதலாவது": 1, "இரண்டாவது": 2, "மூன்றாவது": 3, "நான்காவது": 4, "ஐந்தாவது": 5,
}
_ORDINAL_NUM_RE = "|".join(sorted(_ORDINAL_WORDS, key=len, reverse=True))
_TA_ORDINAL = r"\u0B80-\u0BFF"
_OLB = rf"(?<![\w{_TA_ORDINAL}])"
_ORB = rf"(?![\w{_TA_ORDINAL}])"

# "2nd newest application", "second latest completed app", "3rd most recent rejected"
# "newest field visit", "2nd oldest completed visit"
_ORDINAL_TAIL_RE = re.compile(
    rf"{_OLB}(?P<ord>{_ORDINAL_NUM_RE}){_ORB}\s+"
    rf"(?:newest|latest|most\s+recent|recent)"
    rf"(?:\s+(?:{_MODIFIER_WORDS})){{0,3}}"
    rf"(?:\s+(?:the\s+|my\s+)?(?:application|app|file|record|field\s+visit|visit|inspection|\u0bb5\u0bbf\u0ba3\u0bcd\u0ba3\u0baa\u0bcd\u0baa\u0bae\u0bcd|\u0b95\u0bb3\u0bcd\u0020\u0b86\u0baf\u0bcd\u0bb5\u0bc1))?",
    re.IGNORECASE)
# "2nd oldest application", "third earliest rejected app", "3rd oldest ISD"
# "oldest field visit", "2nd oldest completed inspection"
_ORDINAL_HEAD_RE = re.compile(
    rf"{_OLB}(?P<ord>{_ORDINAL_NUM_RE}){_ORB}\s+"
    rf"(?:oldest|earliest)"
    rf"(?:\s+(?:{_MODIFIER_WORDS})){{0,3}}"
    rf"(?:\s+(?:the\s+|my\s+)?(?:application|app|file|record|field\s+visit|visit|inspection|\u0bb5\u0bbf\u0ba3\u0bcd\u0ba3\u0baa\u0bcd\u0baa\u0bae\u0bcd|\u0b95\u0bb3\u0bcd\u0020\u0b86\u0baf\u0bcd\u0bb5\u0bc1))?",
    re.IGNORECASE)


def _ordinal_suffix(n: int) -> str:
    """1 → '1st', 2 → '2nd', 3 → '3rd', 4 → '4th' …"""
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{('th', 'st', 'nd', 'rd', 'th', 'th', 'th', 'th', 'th', 'th')[n % 10]}"


_POS_ORD = rf"(?:{_ORDINAL_NUM_RE}|\d{{1,3}}(?:st|nd|rd|th))"
_POS_TYPEWORDS = r"(?:nisd|isd|merge|pending|approved|rejected|completed|overdue|open|csc|citizen|sro|sub\s*registrar|my|the|of)"
_POS_NOUN = r"(?:row|rows|entry|entries|record|records|application|applications|app|apps|file|files)"
_POS_ONE_RE = re.compile(
    rf"{_OLB}(?P<o>{_POS_ORD}){_ORB}\s+(?:{_POS_TYPEWORDS}\s+){{0,3}}{_POS_NOUN}\b", re.IGNORECASE)
_POS_PAIR_RE = re.compile(
    rf"{_OLB}(?P<a>{_POS_ORD}){_ORB}\s*(?:,|and|&)\s*(?:the\s+)?(?P<b>{_POS_ORD}){_ORB}"
    rf"\s+(?:{_POS_TYPEWORDS}\s+){{0,3}}{_POS_NOUN}\b", re.IGNORECASE)
_POS_ROWN_RE = re.compile(r"\brow\s+(?:no\.?\s*|number\s+|#)?(?P<n>\d{1,3})\b", re.IGNORECASE)
_POS_RANGE_RE = re.compile(r"\brows?\s+(?P<a>\d{1,3})\s*(?:to|-|through)\s*(?P<b>\d{1,3})\b", re.IGNORECASE)
_POS_PARITY_RE = re.compile(r"\b(?P<p>even|odd)(?:\s+numbered)?\s+(?:rows?|entries|applications)\b", re.IGNORECASE)


def _pos_value(raw: str) -> int:
    raw = raw.lower()
    if raw in _ORDINAL_WORDS:
        return _ORDINAL_WORDS[raw]
    m = re.match(r"\d+", raw)
    return int(m.group(0)) if m else 0


def extract_row_selection(message: str):
    """Which rows of a listing the officer named by position, or None.

    ("nth", n)           "display 2nd row of nisd applications", "row 3", "the 5th application"
    ("rows", [a, b])     "the 2nd and 3rd nisd applications", "rows 2 to 4"
    ("parity", "even")   "even rows of nisd applications"

    A position is 1-based and counts the rows as listed. 0 or past the end is a real
    request with no answer, so the caller says so instead of printing everything."""
    try:
        from backend.services import followup_context as _fc
        message = _fc.correct_spelling(message or "")
    except Exception:
        pass
    msg = normalize_text(message)
    if not msg:
        return None
    m = _POS_PARITY_RE.search(msg)
    if m:
        return ("parity", m.group("p").lower())
    m = _POS_RANGE_RE.search(msg)
    if m:
        a, b = int(m.group("a")), int(m.group("b"))
        if 0 < a <= b and b - a < 100:
            return ("rows", list(range(a, b + 1)))
    m = _POS_PAIR_RE.search(msg)
    if m:
        return ("rows", [_pos_value(m.group("a")), _pos_value(m.group("b"))])
    m = _POS_ONE_RE.search(msg) or _POS_ROWN_RE.search(msg)
    if m:
        raw = m.groupdict().get("o") or m.groupdict().get("n")
        return ("nth", _pos_value(raw))
    return None


def extract_result_limit(message: str) -> Optional[Tuple[int, str]]:
    """(n, "head"|"tail"|"head_nth") when the officer asked for part of a list.

    None when the whole list was asked for, which leaves every existing
    listing untouched.
    - "first N" / "top N"  → (n, "head")     — first n rows of current order
    - "last N" / "latest N" → (n, "tail")    — last n rows of current order
    - "2nd newest"          → (2, "head_nth") — Nth item after desc sort
    - "3rd oldest"          → (3, "head_nth") — Nth item after asc sort
    - "newest application"  → (1, "tail")     — single newest
    - "oldest application"  → (1, "head")     — single oldest
    """
    msg = normalize_text(message)
    if not msg:
        return None

    # ── Ordinal + superlative: "2nd newest", "3rd oldest" ───────────────────
    # Check these BEFORE the numbered head/tail patterns so "2nd newest"
    # is not swallowed by a stray head-count match.
    for rx, _ in ((_ORDINAL_TAIL_RE, "tail_nth"), (_ORDINAL_HEAD_RE, "head_nth")):
        m = rx.search(msg)
        if m:
            raw = m.group("ord")
            n = _ORDINAL_WORDS.get(raw.lower(), 0)
            if n > 0:
                return n, "head_nth"   # always head_nth: sort handles direction

    # ── "first N" / "last N" ────────────────────────────────────────────────
    for rx, end in ((_LIMIT_HEAD_RE, "head"),
                    (_LIMIT_TAIL_RE, "tail"),
                    (_LIMIT_TAIL_REV_RE, "tail")):
        for m in rx.finditer(msg):
            if _LIMIT_NOT_ROWS_RE.match(msg[m.end():]):
                continue                      # "last 2 months" is a period
            raw = m.group("n")
            n = int(raw) if raw.isdigit() else _LIMIT_NUMBER_WORDS.get(raw.lower(), 0)
            if n > 0:
                return n, end

    # ── Singular superlative: "newest application", "oldest app" → exactly 1 ─
    # Return head_nth (not head/tail) so we always take rows[0] after the sort.
    # "newest application" triggers sort-desc + head_nth → rows[0] = newest ✓
    # "oldest application" triggers sort-asc  + head_nth → rows[0] = oldest ✓
    if _SINGULAR_TAIL_RE.search(msg):
        return 1, "head_nth"
    if _SINGULAR_HEAD_RE.search(msg):
        return 1, "head_nth"

    return None


def extract_sort_order(message: str) -> Optional[Tuple[str, str]]:
    """(field, "asc"|"desc") for a listing the officer asked to be ordered.

    None when no order was asked for, which leaves the caller's default in
    place. The direction is what identifies the request -- naming a field alone
    ("sorted by application number") is read as ascending, the conventional
    reading of "sorted by X".
    """
    msg = normalize_text(message)
    if not msg:
        return None

    direction = None
    if any(w in msg for w in _SORT_DESC_WORDS):
        direction = "desc"
    elif any(w in msg for w in _SORT_ASC_WORDS):
        direction = "asc"
    _explicit_direction = direction is not None

    _named_sort = any(w in msg for w in [
        "sort", "sorted", "sort by", "order by", "ordered by", "arrange",
        "arranged", "in order of", "in order", "date order", "id order",
        "number order", "varisai", "வரிசைப்படுத்து", "வரிசையில்",
    ]) or bool(_SORT_BY_FIELD_RE.search(msg))
    if direction is None:
        if not _named_sort:
            return None
        direction = "asc"

    field = "submission_date"
    for name, keywords in _SORT_FIELDS:
        if any(kw in msg for kw in keywords):
            field = name
            break

    # "sorted by priority" with no direction means the urgent work first --
    # ascending would bury it, which is never what the request means.
    if field == "priority" and not _explicit_direction:
        direction = "desc"
    return field, direction


# ─────────────────────────────────────────────────────────────────────────────
# "details of both / all three / all of them"
# ─────────────────────────────────────────────────────────────────────────────
# A follow-up that points at the list the previous answer produced. It names no
# application number, so the numbers have to come from that answer -- and it may
# name any count, not just two ("both", "all three", "the 5 applications",
# "every one of them"). Shared by parse_intent (routing) and chatbot.py (which
# resolves the numbers), so the two can never disagree about what qualifies.
_LISTED_DETAIL_WORDS = [
    "detail", "details", "more info", "more information", "information about",
    "expand", "elaborate", "break down", "breakdown", "full record",
    "விவரம்", "விவரங்கள்", "முழு விவரம்",
]
_LISTED_BACKREF_WORDS = [
    "both", "these", "those", "them", "above", "listed", "the two",
    "each of", "all of", "either", "shown", "just showed", "you showed",
    "every one", "each one", "each application", "every application",
    "இரண்டு", "இரண்டும்", "அவை", "மேலே", "ஒவ்வொரு",
]
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "ஒன்று": 1, "இரண்டு": 2, "மூன்று": 3, "நான்கு": 4, "ஐந்து": 5,
}
_COUNT_WORD_RE = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_ITEM_NOUN_RE = r"(?:applications?|apps?|files?|விண்ணப்ப\w*)"
# "all three applications", "the 5 applications", "all the applications"
_LISTED_COUNT_RES = [
    re.compile(rf"\b(?:all\s+|the\s+|first\s+|top\s+)*(?P<n>{_COUNT_WORD_RE}|\d{{1,2}})\s+"
               rf"(?:of\s+(?:the\s+|them|these|those)\s*)?{_ITEM_NOUN_RE}\b"),
    # "all three of them", "the 5 of them", "first two of them"
    re.compile(rf"\b(?:all\s+|the\s+|first\s+|top\s+)*(?P<n>{_COUNT_WORD_RE}|\d{{1,2}})\s+of\s+(?:them|these|those)\b"),
]
# "all the applications", "every application" -- a count-less "all of them"
_LISTED_ALL_RE = re.compile(rf"\b(?:all|every|each)\s+(?:the\s+|of\s+the\s+)?{_ITEM_NOUN_RE}\b")


# "show the details", "full details please", "details of it" -- a details
# request carrying no subject of its own. Deliberately a fullmatch: the moment
# the message names anything else ("details of the owner", "details of both the
# applications", "survey details"), a more specific rule owns it.
_BARE_DETAILS_RE = re.compile(
    r"(?:please\s+|pls\s+|can\s+you\s+|could\s+you\s+|i\s+want\s+(?:to\s+see\s+)?)*"
    r"(?:show|give|display|list|tell|get|open|view|see|share|send)?\s*"
    r"(?:me\s+)?(?:the\s+|its\s+|it's\s+|his\s+|her\s+|their\s+|"
    r"full\s+|more\s+|complete\s+|other\s+|remaining\s+|all\s+)*"
    r"detail(?:s)?"
    r"(?:\s+(?:of|for|about|on)\s+(?:it|this|that|the\s+(?:application|app|one)|"
    r"this\s+(?:application|app|one)|that\s+(?:application|app|one)))?"
    r"\s*[?.!]*"
    r"|(?:விவரம்|விவரங்கள்|விவரங்களை)\s*(?:காட்டு|காண்பி|தருக|கொடு|வேண்டும்)?\s*[?.!]*",
    re.IGNORECASE,
)


# An application named by pointing rather than by number: "this application",
# "the first one", "is it ...". Used where a question about ONE application
# would otherwise be read as a request for a list.
_APP_BACKREF_RE = re.compile(
    r"\b(?:this|that|the|same)\s+(?:application|app|one)\b"
    r"|\b(?:first|second|third|fourth|fifth|last)\s+one\b"
    r"|\bis\s+it\b|\bits\b|\bthis\s+one\b|\bthat\s+one\b"
    r"|\bஇந்த\s+விண்ணப்ப\w*|\bஅந்த\s+விண்ணப்ப\w*"
    # Tanglish demonstratives: "itha application ISD ah illa NISD ah" and the
    # bare "ithu ISD ah illa NISD ah" (no noun at all -- Tanglish drops it the
    # way English "is IT isd or nisd" does) were falling to "both_applications"
    # and returning an unrelated listing table instead of answering about the
    # one file meant, because only the English/Tamil-script forms were here.
    r"|\b(?:itha|intha|andha)\s+(?:application|app)\b"
    r"|\b(?:ithu|idhu|athu|adhu)\b",
    re.IGNORECASE,
)


def requested_listing_count(message: str) -> Optional[int]:
    """The count the officer named -- 3 for "details of all three applications".

    None when they named none ("details of all of them"), which means every
    application the previous answer listed.
    """
    msg = normalize_text(message)
    for rx in _LISTED_COUNT_RES:
        m = rx.search(msg)
        if m:
            tok = m.group("n")
            return _NUMBER_WORDS.get(tok, int(tok) if tok.isdigit() else None)
    return None


def wants_details_of_listed(message: str) -> bool:
    """True for "show details of both / all three / every one of them".

    Takes a details word AND a reference back to the previous list: a details
    word alone carries its own number ("details of 2026/0154/28/001167"), and a
    back-reference alone is not a request for details ("close both of them").
    """
    msg = normalize_text(message)
    if not any(w in msg for w in _LISTED_DETAIL_WORDS):
        return False
    if any(w in msg for w in _LISTED_BACKREF_WORDS):
        return True
    if _LISTED_ALL_RE.search(msg):
        return True
    return requested_listing_count(message) is not None


def detect_jurisdiction_focus(message: str) -> Optional[str]:
    """Return the single jurisdiction level the message asks about, else None.

    Typo-tolerant on the same terms as parse_intent: "distict", "thaluk",
    "jurisdication" are all matched, so a misspelling never silently downgrades
    a focused question into the full card.
    """
    msg = normalize_text(message)
    if not msg:
        return None
    words = extract_tokens(msg)
    if not words:
        return None

    def _hit(keyword: str) -> bool:
        kw = normalize_text(keyword)
        kw_tokens = extract_tokens(kw)
        if len(kw_tokens) != 1:
            return match_phrase(words, kw)
        kw = kw_tokens[0]
        if kw in words:
            return True
        if len(kw) < 5 or any('\u0B80' <= c <= '\u0BFF' for c in kw):
            return False
        return any(len(w) >= 5 and is_token_typo_match(w, kw, min_ratio=0.75)
                   for w in words)

    # Also check for severely mangled "jurisdiction" via SequenceMatcher --
    # the same approach as _jurisdiction_fuzzy_match in parse_intent.
    _whole_hit = any(_hit(w) for w in _JUR_WHOLE_WORDS)
    if not _whole_hit:
        for w in words:
            if len(w) >= 8 and w[0] == 'j':
                if SequenceMatcher(None, w, "jurisdiction").ratio() >= 0.75:
                    _whole_hit = True
                    break
    if _whole_hit:
        return None

    matched = [level for level, kws in _JUR_FOCUS_WORDS.items()
               if any(_hit(kw) for kw in kws)]
    return matched[0] if len(matched) == 1 else None


_TA_EXTRA_TH = {
    "Channel": "வழி", "Fee": "கட்டணம்", "Total Fee": "மொத்த கட்டணம்", "With Fee": "கட்டணத்துடன்",
    "Payment mode": "செலுத்தும் முறை", "Payment Mode": "செலுத்தும் முறை", "Submitted": "சமர்ப்பிக்கப்பட்டது",
    "Rejected On": "நிராகரிக்கப்பட்ட தேதி", "Rejected By": "நிராகரித்தவர்", "Reason": "காரணம்",
    "Resubmitted": "மறு சமர்ப்பிப்பு", "Status": "நிலை", "Stage": "கட்டம்", "Type": "வகை",
    "Application No.": "விண்ணப்ப எண்", "Application Number": "விண்ணப்ப எண்", "Application Type": "விண்ணப்ப வகை",
    "Applicant": "விண்ணப்பதாரர்", "Applicant Name": "விண்ணப்பதாரரின் பெயர்", "Mobile": "தொலைபேசி",
    "Address": "முகவரி", "Applications": "விண்ணப்பங்கள்", "Service Code": "சேவை குறியீடு",
    "SLA": "கால வரம்பு", "Key Aspect": "முக்கிய அம்சம்", "Details": "விவரங்கள்", "OVERDUE DAYS": "தாமத நாட்கள்",
    "Field": "புலம்", "Value": "மதிப்பு", "Metric": "அளவீடு", "Count": "எண்ணிக்கை", "Ward": "வார்டு",
    "Block": "தொகுதி", "Survey No.": "கணக்கெண்", "Survey Number": "கணக்கெண்", "Sub-Divisions": "உட்பிரிவுகள்",
    "Survey & Processing Workflow": "சர்வே மற்றும் செயலாக்க பணிப்பாய்வு", "Scheduled Date": "திட்டமிட்ட தேதி",
    "Days Pending": "நிலுவை நாட்கள்", "Submission Date": "சமர்ப்பித்த தேதி", "Town": "நகரம்", "Taluk": "தாலுகா",
    "District": "மாவட்டம்", "Area (sq.m)": "பரப்பளவு (ச.மீ)", "Priority": "முன்னுரிமை",
}
_TH_RE = re.compile(r"<th>([^<]*)</th>")


def build_html_response(structured_data: Dict[str, Any], language: str = "en", query: str = "") -> str:
    """The HTML answer; for Tamil / Tanglish every table header that is still English is
    translated (the branches that hard-code a header do not consult the label table)."""
    html = _build_html_response_core(structured_data, language, query)
    if language in ("ta", "tanglish") and isinstance(html, str) and "<th>" in html:
        html = _TH_RE.sub(lambda m: f"<th>{_TA_EXTRA_TH.get(m.group(1).strip(), m.group(1))}</th>", html)
    return html


def _build_html_response_core(structured_data: Dict[str, Any], language: str = "en", query: str = "") -> str:
    """
    Build a clean HTML response directly from structured DB data,
    bypassing the LLM entirely for table-based queries.

    All DB values are HTML-escaped to prevent XSS / broken markup.
    Intro text uses <div> (not <p>) — browsers auto-close <p> before
    block-level elements like <table>, which hides the <thead>.

    Args:
        structured_data: Database query results
        language: Detected language ("en", "ta", or "tanglish") for table labels
        query: User message/question to dynamically include requested columns (e.g. applicant name, mobile)

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

    req_mobile = any(w in user_query for w in [
        "mobile", "phone", "contact", "cell", "தொலைபேசி", "கைபேசி", "tholaipaesi"
    ]) or bool(re.search(r'\b(mobile|phone|contact|cell|phone\s*no|mobile\s*no)\b', user_query))

    req_address = any(w in user_query for w in [
        "address", "addr", "முகவரி", "mugavari", "virivu"
    ]) or bool(re.search(r'\b(address|addr)\b', user_query))

    # A list covering more than one submission channel has to show which row
    # came through which -- "CSC and Sub-Registrar applications" rendered as one
    # undifferentiated table answers half the question. Set by chatbot.py when
    # the officer named several channels, or asked for the channel outright.
    req_channel = bool(structured_data.get("show_channel_column")) or bool(
        re.search(r'\bsubmission\s+channels?\b|\bchannels?\b|\bsources?\b|வழி', user_query))

    extra_th = ""
    if req_name:
        extra_th += f"<th>Applicant Name</th>"
    if req_mobile:
        extra_th += f"<th>Mobile</th>"
    if req_address:
        extra_th += f"<th>Address</th>"
    # Deliberately NOT part of extra_th: the field-visit tables below share
    # extra_th and emit no channel cell, and a header with no cell under it
    # shifts every column after it. Only the two application tables render it.
    channel_th = "<th>Channel</th>" if req_channel else ""

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
            "address": "Address",
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
            "mobile": "தொலைபேசி",
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

    # Column headers follow the officer's language. (They used to be pinned to English
    # on the strength of a client-side translation map, `colTranslations`, that does
    # not exist -- so a Tamil answer sat above an English table.) Nothing in the
    # frontend keys on these header strings for HTML tables built here.
    lang = "ta" if language in ("ta", "tanglish") else "en"
    t = labels[lang]
    # The narrative sentences OUTSIDE the table (the "Found N application(s)"
    # intro, sort/scope notes, "Details for X") are a different concern from
    # the header-compat rule above, but several of them were already written
    # against `lang` before that rule pinned it to "en" -- `if lang != "ta"`
    # branches a few hundred lines below that could never actually fire. Use
    # the officer's REAL language for narrative text; `lang`/`t` stay for
    # headers and cell values only.
    is_ta = language in ("ta", "tanglish")

    logger.info(f"build_html_response: language={language!r} -> lang={lang!r}, keys={list(structured_data.keys())}")

    # ── Litigation check ─────────────────────────────────────────────
    # This is a one-sentence narrative answer, not a data table the frontend
    # keys off of, so unlike the table sections below it can honour the
    # officer's actual language instead of the lang="en" override.
    if structured_data.get("query_type") == "Litigation Check":
        survey = _e(structured_data.get("survey_no"))
        flagged = structured_data.get("litigation_flag")
        is_tamil = language in ("ta", "tanglish")
        if flagged:
            return (f"<div>⚠️ கணக்கெண் <strong>{survey}</strong> மீது வழக்கு உள்ளது என "
                    f"கொடியிடப்பட்டுள்ளது. தொடர்வதற்கு முன் நீதிமன்ற நிலையை சரிபார்க்கவும்.</div>"
                    if is_tamil else
                    f"<div>⚠️ Survey No. <strong>{survey}</strong> is flagged for "
                    f"litigation. Verify the court status before proceeding.</div>")
        return (f"<div>கணக்கெண் <strong>{survey}</strong> மீது வழக்கு எதுவும் பதிவு "
                f"செய்யப்படவில்லை.</div>"
                if is_tamil else
                f"<div>No litigation is recorded against Survey No. "
                f"<strong>{survey}</strong>.</div>")

    # ── Next available sub-division number ───────────────────────────
    if structured_data.get("query_type") == "Next Sub-division Number":
        survey = _e(structured_data.get("survey_no"))
        nxt = _e(structured_data.get("next_available"))
        highest = _e(structured_data.get("highest_existing"))
        count = _e(structured_data.get("existing_count"))
        amb = ""
        _others = structured_data.get("other_locations") or []
        if _others:
            _where = ", ".join(
                f"ward {_e(loc.get('ward'))} / block {_e(loc.get('block'))}"
                for loc in _others
            )
            amb = (
                f"<div class='table-intro'>Note: Survey No. "
                f"{_e(structured_data.get('base_survey_no'))} also exists in {_where}, "
                f"each with its own sub-division sequence. The number below is for the "
                f"first parcel only.</div>"
            )
        return (
            amb
            + f"<div class='table-intro'>Next available sub-division for Survey No. "
            f"<strong>{survey}</strong>: <strong>{nxt}</strong></div>"
            "<table class='data-table'><tbody>"
            f"<tr><td><strong>Existing sub-divisions</strong></td><td>{count}</td></tr>"
            f"<tr><td><strong>Highest in use</strong></td><td>{highest}</td></tr>"
            f"<tr><td><strong>Next available</strong></td><td>{nxt}</td></tr>"
            "</tbody></table>"
        )

    # ── Rejection history ────────────────────────────────────────────
    # Same gap as the jurisdiction summary had: the reasons were resolved into
    # structured_data but nothing rendered them, so "why was X rejected?"
    # answered "Here are the rejection history results."
    if structured_data.get("query_type") == "Rejection History":
        app_no = _e(structured_data.get("application_number"))
        rejections = structured_data.get("rejections") or []
        _is_ta_rej = language in ("ta", "tanglish")
        if not rejections:
            if _is_ta_rej:
                return (f"<div>விண்ணப்பம் <strong>{app_no}</strong>-க்கு "
                        f"நிராகரிப்பு எதுவும் பதிவில் இல்லை.</div>")
            return (f"<div>No rejection is recorded for application "
                    f"<strong>{app_no}</strong>.</div>")
        rows = "".join(
            "<tr>"
            f"<td>{_e((r.get('rejected_at') or '')[:10])}</td>"
            f"<td>{_e(r.get('source'))}</td>"
            f"<td>{_e(r.get('reason_text'))}</td>"
            f"<td>{_e((r.get('resubmitted_at') or '-')[:10])}</td>"
            "</tr>"
            for r in rejections
        )
        # Table headers stay English on purpose -- table_renderer.js already
        # translates this exact column set client-side for Tamil display.
        if _is_ta_rej:
            intro = (f"<div class='table-intro'>விண்ணப்பம் <strong>{app_no}</strong> "
                    f"{len(rejections)} முறை நிராகரிக்கப்பட்டது:</div>")
        else:
            intro = (f"<div class='table-intro'>Application <strong>{app_no}</strong> was "
                     f"rejected {len(rejections)} time(s):</div>")
        return (
            intro +
            "<table class='data-table'><thead><tr>"
            "<th>Rejected On</th><th>Rejected By</th><th>Reason</th>"
            "<th>Resubmitted</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )

    # ── Jurisdiction summary ─────────────────────────────────────────
    # An officer asking "what is my jurisdiction?" should get the actual area
    # back. There was no renderer for this shape, so the caller fell through to
    # a filler sentence ("Here are the jurisdiction summary results.") while the
    # district, ward and counts sat unused in structured_data.
    _jur = structured_data.get("jurisdiction")
    if (structured_data.get("query_type") == "Jurisdiction Summary"
            and isinstance(_jur, dict) and "district" in _jur):
        # These row labels are literal display text, not JSON keys the
        # frontend's status-badge logic parses (that only keys off
        # 'Status'/'Stage' values, neither of which appears in this table),
        # so unlike the lang="en" table sections elsewhere they can be
        # localized safely.
        _jur_is_tamil = language in ("ta", "tanglish")
        _rl = {
            "district": "மாவட்டம்", "taluk": "தாலுகா", "town": "நகரம்",
            "wards": "வார்டு(கள்)", "blocks": "தொகுதி(கள்)",
            "surveys": "சர்வே எண்கள்", "active": "செயலில் உள்ள விண்ணப்பங்கள்",
            "title": "உங்கள் அதிகார வரம்பு",
        } if _jur_is_tamil else {
            "district": "District", "taluk": "Taluk", "town": "Town",
            "wards": "Ward(s)", "blocks": "Block(s)",
            "surveys": "Survey numbers", "active": "Active applications",
            "title": "Your jurisdiction",
        }
        _district = _jur.get("district") or {}
        _taluk = _jur.get("taluk") or {}

        # A focused question ("what is my taluk?") gets the one value it asked
        # for. Only the full "what is my jurisdiction?" gets the whole card.
        _focus = _jur.get("focus")
        if _focus:
            _town_names = [t["name"] for t in (_jur.get("towns") or []) if t.get("name")]
            _ward_nums = [w["ward_number"] for t in (_jur.get("towns") or [])
                          for w in (t.get("wards") or []) if w.get("ward_number")]
            _block_nums = []
            for t in _jur.get("towns") or []:
                for w in t.get("wards") or []:
                    for b in w.get("blocks") or []:
                        if b.get("block_number") and b["block_number"] not in _block_nums:
                            _block_nums.append(b["block_number"])

            _dname = _district.get("name")
            _dcode = _district.get("code")
            _tcode = _taluk.get("code")

            # "what is my taluk code?" / "give me the numeric code of my taluk"
            # asks for the code, not the name. Only district and taluk carry a
            # distinct code here -- ward / block already answer with their
            # number. Falls through to the name answer when no code is on record.
            _wants_code = bool(re.search(r'\bcodes?\b|குறியீ', (query or "").lower()))
            if _wants_code and _focus in ("district", "taluk"):
                _cv = _dcode if _focus == "district" else _tcode
                if _cv and _cv != "N/A":
                    if _jur_is_tamil:
                        _tal = "மாவட்ட" if _focus == "district" else "தாலுகா"
                        return (f"<div class='table-intro'>உங்கள் {_tal} குறியீடு: "
                                f"<strong>{_e(_cv)}</strong></div>")
                    return (f"<div class='table-intro'>Your {_focus} code is "
                            f"<strong>{_e(_cv)}</strong>.</div>")

            _focus_values = {
                "district": ([f"{_dname} ({_dcode})" if _dcode and _dcode != "N/A"
                              else _dname] if _dname and _dname != "N/A" else []),
                "taluk": ([_taluk["name"]] if _taluk.get("name")
                          and _taluk["name"] != "N/A" else []),
                "town": _town_names,
                "ward": _ward_nums,
                "block": _block_nums,
            }
            _vals = _focus_values.get(_focus) or []
            _focus_labels = {
                "district": ("district", "districts", "மாவட்டம்"),
                "taluk": ("taluk", "taluks", "தாலுகா"),
                "town": ("town", "towns", "நகரம்"),
                "ward": ("ward", "wards", "வார்டு"),
                "block": ("block", "blocks", "தொகுதி"),
            }
            _sing, _plur, _ta_label = _focus_labels[_focus]
            if _vals:
                _joined = ", ".join(_e(v) for v in _vals)
                if _jur_is_tamil:
                    return (f"<div class='table-intro'>உங்கள் {_ta_label}: "
                            f"<strong>{_joined}</strong></div>")
                _verb = "is" if len(_vals) == 1 else "are"
                _noun = _sing if len(_vals) == 1 else _plur
                return (f"<div class='table-intro'>Your {_noun} {_verb} "
                        f"<strong>{_joined}</strong>.</div>")
            if _jur_is_tamil:
                return (f"<div class='table-intro'>உங்களுக்கு {_ta_label} "
                        f"ஒதுக்கப்படவில்லை.</div>")
            return (f"<div class='table-intro'>No {_sing} is assigned to you.</div>")

        rows = []
        if _district.get("name"):
            code = _district.get("code")
            rows.append((_rl["district"], _e(_district["name"])
                         + (f" ({_e(code)})" if code else "")))
        if _taluk.get("name"):
            rows.append((_rl["taluk"], _e(_taluk["name"])))
        for town in _jur.get("towns") or []:
            if town.get("name"):
                rows.append((_rl["town"], _e(town["name"])))
            wards = [w for w in (town.get("wards") or []) if w.get("ward_number")]
            if wards:
                rows.append((_rl["wards"], ", ".join(_e(w["ward_number"]) for w in wards)))
            blocks = [b for w in (town.get("wards") or [])
                      for b in (w.get("blocks") or []) if b.get("block_number")]
            if blocks:
                rows.append((_rl["blocks"], ", ".join(_e(b["block_number"]) for b in blocks)))
        if _jur.get("survey_count") is not None:
            rows.append((_rl["surveys"], _e(_jur["survey_count"])))
        if _jur.get("active_applications") is not None:
            rows.append((_rl["active"], _e(_jur["active_applications"])))
        if rows:
            body = "".join(f"<tr><td><strong>{k}</strong></td><td>{v}</td></tr>"
                           for k, v in rows)
            return (f"<div class='table-intro'><strong>{_rl['title']}</strong></div>"
                    f"<table class='data-table'><tbody>{body}</tbody></table>")

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

    # ── CAN carried by one application ──────────────────────────────
    if "can_details" in structured_data and isinstance(structured_data["can_details"], dict):
        cd = structured_data["can_details"]
        if cd.get("found"):
            rows_ = [
                ("Application", _e(cd.get("application_number"))),
                ("CAN Number", f"<code>{_e(cd.get('can_number'))}</code>"),
            ]
            # a file with no CAN of its own -- "0 digits" is noise; on the
            # Sub-Registrar route the Form 6 number identifies it
            if cd.get("digits"):
                rows_.append(("Digits", _e(cd.get("digits"))))
            elif cd.get("igrs_form6_number"):
                rows_.append(("IGRS Form 6", f"<code>{_e(cd['igrs_form6_number'])}</code>"))
            rows_.append(("Submission Channel", _e(cd.get("channel"))))
            if cd.get("applicant_name"):
                rows_.append(("Applicant", _e(cd["applicant_name"])))
            if cd.get("submission_date"):
                rows_.append(("Submitted On", _e(cd["submission_date"])))
            body = "".join(f"<tr><td><strong>{k}</strong></td><td>{v}</td></tr>" for k, v in rows_)
            return ("<div class='table-intro'><strong>CAN details</strong></div>"
                    f"<table class='data-table'><tbody>{body}</tbody></table>")
        _asked = cd.get("application_number") or cd.get("can_number")
        return ("<div class='table-intro'>No application carrying "
                f"<strong>{_e(_asked)}</strong> was found in your jurisdiction.</div>")

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
            # The row that stops the guide being read as "length = channel".
            # It was computed into the payload and then never rendered, so the
            # one fact an officer needs from this table was the one it left out.
            + (f"<tr><td><strong>Number format</strong></td><td>{_e(can.get('number_format'))}</td></tr>"
               if can.get('number_format') else "")
            + f"<tr><td><strong>Role in Patta Transfer</strong></td><td>{_e(can.get('role_in_patta_transfer'))}</td></tr>"
            f"<tr><td><strong>CSC Service Charge</strong></td><td>{_e(can.get('csc_charges'))}</td></tr>"
            f"<tr><td><strong>Supported Service Codes</strong></td><td><code>{_e(can.get('service_codes_linked'))}</code></td></tr>"
            f"</tbody>"
            f"</table>"
        )

    # ── Recorded fee on the newest applications of a type / channel ──
    if "fee_lookup" in structured_data and isinstance(structured_data["fee_lookup"], dict):
        parts = []
        _sum = structured_data["fee_lookup"].get("summary")
        if _sum:
            parts.append("<div class='table-intro'>" + _e(_sum).replace("\n", "<br>") + "</div>")
        for g in structured_data["fee_lookup"].get("groups", []):
            if not g.get("latest"):
                continue
            label = _e(g.get("application_type") or "All types") + (
                f" / {_e(g['channel'])}" if g.get("channel") else "")
            rows = "".join(
                f"<tr><td>{_e(x.get('application_number'))}</td>"
                f"<td>{_e(x.get('channel'))}</td>"
                f"<td>{_e(x.get('submission_date'))}</td>"
                f"<td>{_money(x.get('fee_amount'))}</td>"
                f"<td>{_e(x.get('payment_mode') or 'not recorded')}</td></tr>"
                for x in g["latest"])
            parts.append(
                f"<div class='table-intro'><strong>Newest {label} files with a fee recorded</strong></div>"
                "<table class='data-table'><thead><tr><th>Application No.</th><th>Channel</th>"
                "<th>Submitted</th><th>Fee</th><th>Payment mode</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>")
        return "".join(parts)

    # ── Fee collection summary (aggregate over the officer's applications) ──
    if "fee_summary" in structured_data and isinstance(structured_data["fee_summary"], dict):
        fs = structured_data["fee_summary"]
        scope_bits = []
        if fs.get("application_type"):
            scope_bits.append(_e(fs["application_type"]))
        if fs.get("start_date") and fs.get("end_date"):
            scope_bits.append(f"{_e(fs['start_date'])} to {_e(fs['end_date'])}")
        scope = f" ({', '.join(scope_bits)})" if scope_bits else ""

        if not fs.get("total_applications"):
            return (f"<div class='table-intro'>No applications{scope} in your "
                    "jurisdiction, so there is no fee record to total.</div>")

        head = (
            f"<div class='table-intro'><strong>Fee collected{scope}:</strong> "
            f"{_money(fs.get('total_fee'))} across "
            f"{_e(fs.get('with_fee'))} of {_e(fs.get('total_applications'))} "
            f"application(s) that carry a fee record "
            f"({_e(fs.get('without_fee'))} carry none).</div>"
        )
        type_rows = "".join(
            f"<tr><td>{_e(r.get('application_type'))}</td>"
            f"<td>{_e(r.get('applications'))}</td>"
            f"<td>{_e(r.get('with_fee'))}</td>"
            f"<td>{_money(r.get('total_fee'))}</td></tr>"
            for r in fs.get("by_type", [])
        )
        mode_rows = "".join(
            f"<tr><td>{_e(r.get('payment_mode'))}</td>"
            f"<td>{_e(r.get('applications'))}</td>"
            f"<td>{_money(r.get('total_fee'))}</td></tr>"
            for r in fs.get("by_payment_mode", [])
        )
        return (
            head
            + "<table class='data-table'><thead><tr><th>Type</th>"
              "<th>Applications</th><th>With Fee</th><th>Total Fee</th></tr></thead>"
              f"<tbody>{type_rows}</tbody></table>"
            + "<div class='table-intro'><strong>By payment mode</strong></div>"
              "<table class='data-table'><thead><tr><th>Payment Mode</th>"
              "<th>Applications</th><th>Total Fee</th></tr></thead>"
              f"<tbody>{mode_rows}</tbody></table>"
            + f"<div class='table-intro'><small>Challan number recorded on "
              f"{_e(fs.get('with_challan'))} application(s). "
              f"{_money(fs.get('rejected_fee'))} of the total came in on files "
              f"that were later rejected — the fee is paid at submission, so it "
              f"is counted here.</small></div>"
        )

    # ── Service Codes Workflow & Fee Comparison (0153 / 0154 / 0155) ──
    if "service_codes" in structured_data and isinstance(structured_data["service_codes"], list):
        items = structured_data["service_codes"]
        rows = "".join(
            f"<tr>"
            f"<td><span style='background: #e0f2fe; color: #0369a1; padding: 2px 6px; border-radius: 4px; font-weight: bold;'>{_e(sc.get('service_code'))}</span></td>"
            f"<td><strong>{_e(sc.get('type'))}</strong><br><small style='color: #6b7280;'>{_e(sc.get('tamil_name'))}</small></td>"
            f"<td>{_e(sc.get('sla_days'))}</td>"
            f"<td>{_e(sc.get('workflow'))}</td>"
            f"</tr>"
            for sc in items
        )
        return (
            f"<div class='table-intro'><strong>Tamil Nadu Land Administration — Service Codes Guide:</strong></div>"
            f"<table class='data-table'>"
            f"<thead><tr>"
            f"<th>Service Code</th><th>Application Type</th><th>SLA</th><th>Survey & Processing Workflow</th>"
            f"</tr></thead>"
            f"<tbody>{rows}</tbody>"
            f"</table>"
            f"<div class='table-intro'><small>Fees are not listed here: they are "
            f"taken from the register and can be revised. Ask \"what is the fee for ISD\".</small></div>"
        )

    # ── Applications (regular + merge) ──────────────────────────────
    if "applications" in structured_data and isinstance(structured_data["applications"], list):
        applications = structured_data["applications"]
        count = structured_data.get("count", len(applications))

        # "show the first two" -- the rows were capped after the query. The
        # count stated is the REAL total, with the cap named beside it: saying
        # "Found 2 application(s)" to an officer holding 64 is the same failure
        # as the current-stage pin this file already documents.
        _rlimit = structured_data.get("result_limit") or {}
        _rlimit_note = ""
        if _rlimit.get("total"):
            _rl_n = _rlimit.get("n", len(applications))
            _rl_end = _rlimit.get("end", "head")
            _rl_total = _rlimit["total"]
            if _rl_end == "head_nth":
                # Ordinal / singular pick: show the real count as "1 of N" so
                # the officer knows the pool size, but count stays 1 (the row shown).
                count = 1
                _sort_dir_hint = structured_data.get("sort_dir", "")
                if _rl_n == 1:
                    if _sort_dir_hint == "desc" or any(
                            w in query.lower() for w in ("newest", "latest", "recent", "new app")):
                        _rlimit_note = (f" — newest of {_rl_total}" if not is_ta
                                        else f" — {_rl_total} இல் புதியது")
                    else:
                        _rlimit_note = (f" — oldest of {_rl_total}" if not is_ta
                                        else f" — {_rl_total} இல் பழையது")
                else:
                    _ord = _ordinal_suffix(_rl_n)
                    _rlimit_note = (f" — {_ord} of {_rl_total}" if not is_ta
                                    else f" — {_rl_total} இல் {_rl_n}-வது")
            elif _rl_end == "rows":
                count = len(applications)
                _rlimit_note = f" — {_rlimit.get('label', 'selected rows')} of {_rl_total}"
            else:
                count = _rl_total
                if _rl_end == "tail":
                    _rlimit_note = (f" — showing the last {_rl_n}" if not is_ta
                                    else f" — கடைசி {_rl_n} மட்டும்")
                else:
                    _rlimit_note = (f" — showing the first {_rl_n}" if not is_ta
                                    else f" — முதல் {_rl_n} மட்டும்")

        if not applications:
            qtype_name = structured_data.get("query_type")
            _rl_oor = _rlimit.get("out_of_range")
            _rl_oor_n = _rlimit.get("n", 0)
            _rl_oor_total = _rlimit.get("total", 0)
            if _rl_oor and _rl_oor_total > 0:
                # "3rd oldest" when only 2 applications exist
                _ord_str = _ordinal_suffix(_rl_oor_n)
                if is_ta:
                    _oor_msg = (f"{_rl_oor_n}-வது பதிவு கிடைக்கவில்லை — "
                                f"மொத்தம் {_rl_oor_total} விண்ணப்பங்கள் மட்டுமே உள்ளன.")
                else:
                    _oor_msg = (f"There is no {_ord_str} application — "
                                f"only {_rl_oor_total} application(s) exist in this list.")
                return f"<div class='table-intro'>{_e(_oor_msg)}</div>"
            note = structured_data.get("empty_note")
            note_html = f" {_e(note)}" if note else ""
            if qtype_name:
                return (f"<div class='table-intro'><strong>{qtype_name}</strong>: "
                        f"No applications found.{note_html}</div>")
            return f"<div>{t['no_records_found']}{note_html}</div>"

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
            intro_msg = f"Details for <strong>{app_no_title}</strong>:" if not is_ta else f"<strong>{app_no_title}</strong> விவரங்கள்:"
        elif min_days_overdue:
            intro_msg = ((f"<strong>{count}</strong> விண்ணப்பங்கள் {min_days_overdue}+ நாட்களுக்கு "
                          f"காலதாமதமாக உள்ளன{_geo_scope_label(qtype_name)}:") if is_ta else
                         (f"{t['found']} <strong>{count}</strong> application(s) overdue by "
                          f"{min_days_overdue}+ days{_geo_scope_label(qtype_name)}:"))
        elif is_overdue_query:
            intro_msg = ((f"<strong>{count}</strong> காலதாமதமான விண்ணப்பங்கள் "
                          f"கிடைத்தன{_geo_scope_label(qtype_name)}:") if is_ta else
                         (f"{t['found']} <strong>{count}</strong> overdue application(s)"
                          f"{_geo_scope_label(qtype_name)}:"))
        elif "Non-Overdue" in qtype_name:
            intro_msg = ((f"<strong>{count}</strong> காலதாமதமாகாத விண்ணப்பங்கள் "
                          f"கிடைத்தன{_geo_scope_label(qtype_name)}:") if is_ta else
                         (f"{t['found']} <strong>{count}</strong> non-overdue application(s)"
                          f"{_geo_scope_label(qtype_name)}:"))
        else:
            # A month-scoped list must say which month it covers — "Found 2
            # applications" alone leaves the officer guessing whether the
            # filter was applied at all.
            # The scope runs to the end of the label so multi-month unions
            # ("March 2026 & May 2026") survive intact; a session note is
            # parenthesised and does not belong in the intro.
            _scope = re.search(
                r"\bin ((?:January|February|March|April|May|June|July|August|"
                r"September|October|November|December)\b[^(]*)", qtype_name)
            # A ward/block-scoped list says which one, for the same reason a
            # month-scoped one does: "Found 2 applications" alone leaves the
            # officer guessing whether the filter was applied.
            if not _scope:
                _scope = re.search(r"\bin ((?:Ward|Block)\s+\S+(?:\s+/\s+(?:Ward|Block)\s+\S+)*)",
                                   qtype_name)
            if _scope:
                _scope_txt = _scope.group(1).strip()
                _scope_str = (f" in {_scope_txt}" if not is_ta
                              else f" ({_scope_txt})")
            else:
                _scope_str = ""
            if is_ta:
                intro_msg = f"<strong>{count}</strong> விண்ணப்பங்கள் கிடைத்தன{_scope_str}:"
            else:
                intro_msg = f"{t['found']} <strong>{count}</strong> {t['applications']}{_scope_str}:"

        if _rlimit_note and intro_msg.endswith(":"):
            intro_msg = intro_msg[:-1] + _rlimit_note + ":"

        # Name the order when one was asked for, so the officer can see the
        # request was honoured rather than having to infer it from the rows.
        # Skip this for head_nth picks: "newest of 2" / "2nd of 5" already
        # communicates both the sort direction and the position; appending
        # ", sorted by submission date (descending)" is redundant and noisy.
        _sort_by = structured_data.get("sort_by")
        _sort_dir = structured_data.get("sort_dir")
        _is_ordinal_pick = (_rlimit.get("end") == "head_nth")
        if _sort_dir and not _is_ordinal_pick and intro_msg.endswith(":"):
            _field_label = {
                "submission_date": ("submission date", "சமர்ப்பித்த தேதி"),
                "application_number": ("application number", "விண்ணப்ப எண்"),
                "priority": ("priority", "முன்னுரிமை"),
                "status": ("status", "நிலை"),
                "application_type": ("type", "வகை"),
                "ward_number": ("ward", "வார்டு"),
                "block_number": ("block", "பிளாக்"),
                "survey_no": ("survey number", "சர்வே எண்"),
                "fee_amount": ("fee", "கட்டணம்"),
                "applicant_name": ("applicant name", "விண்ணப்பதாரர் பெயர்"),
            }.get(_sort_by or "submission_date", ("submission date", "சமர்ப்பித்த தேதி"))
            _desc = str(_sort_dir).lower() == "desc"
            if is_ta:
                _dir_label = "இறங்கு வரிசையில்" if _desc else "ஏறு வரிசையில்"
                _sort_str = f" ({_field_label[1]} — {_dir_label})"
            else:
                _dir_label = "descending" if _desc else "ascending"
                _sort_str = f", sorted by {_field_label[0]} ({_dir_label})"
            intro_msg = intro_msg[:-1] + _sort_str + ":"

        # A question the list itself answers -- "do they have an IGRS number?"
        # -- gets its answer in words above the table. The table alone is not
        # an answer to a yes/no.
        _lead = structured_data.get("lead_note")
        if _lead:
            intro_msg = f"{_e(_lead)}<br>{intro_msg}"

        projected_cols = _get_projected_application_columns(user_query)
        if projected_cols:
            col_labels = {
                "application_no": t["application_no"],
                "applicant_name": "Applicant Name",
                "mobile": "Mobile",
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
                "service_code": "Service Code",
                "channel": "Channel",
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
                def _loc(a, b): return a if (a and a != 'N/A') else (b or 'N/A')
                loc_map = {
                    "district": _loc(jur.get('district'), app.get('district_name')),
                    "taluk": _loc(jur.get('taluk'), app.get('taluk_name')),
                    "town": _loc(jur.get('town'), app.get('town_name')),
                    "ward": _loc(jur.get('ward'), app.get('ward_number')),
                    "block": _loc(jur.get('block'), app.get('block_number'))
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
                    # ward_code/block_code: prefer the code actually stored on the row.
                    # loc_map holds the human-readable name and is only a last resort.
                    "ward_code": _e(app.get('ward_code') or loc_map['ward'] or 'N/A'),
                    "block": _e(loc_map["block"]),
                    "block_code": _e(app.get('block_code') or loc_map['block'] or 'N/A'),
                    "patta_no": _e(app.get('patta_number') or app.get('patta_no') or 'N/A'),
                    "sale_deed_no": _e(app.get('sale_deed_number') or 'N/A'),
                    "sale_deed_reg": _e(deed_reg),
                    "declared_reason": _reason(app.get('declared_reason')),
                    "field_visit_date": _e(app.get('field_visit_date') or 'Unscheduled'),
                    "priority": _e(priority_val),
                    "notes": _e(app.get('notes') or 'N/A'),
                    "can_no": _e(app.get('can_number') or 'N/A'),
                    "service_code": _e(srv_code),
                    "channel": _e(_CHANNEL_CELL.get(app.get('submission_channel'),
                                                    app.get('submission_channel') or 'N/A')),
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
                
                def _loc(a, b): return a if (a and a != 'N/A') else (b or 'N/A')
                loc_map = {
                    "district": _loc(jur.get('district'), app.get('district_name')),
                    "taluk": _loc(jur.get('taluk'), app.get('taluk_name')),
                    "town": _loc(jur.get('town'), app.get('town_name')),
                    "ward": _loc(jur.get('ward'), app.get('ward_number')),
                    "block": _loc(jur.get('block'), app.get('block_number'))
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
                if req_address:
                    extra_td += f"<td>{_e(app.get('applicant_address') or 'N/A')}</td>"
                if req_channel:
                    extra_td += f"<td>{_e(_CHANNEL_CELL.get(app.get('submission_channel'), app.get('submission_channel') or 'N/A'))}</td>"

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
                f"<th>{t['application_no']}</th>{extra_th}{channel_th}<th>{t['type']}</th><th>{t['survey_no']}</th><th>{t['subdivisions']}</th>"
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
                
                def _loc(a, b): return a if (a and a != 'N/A') else (b or 'N/A')
                loc_map = {
                    "district": _loc(jur.get('district'), app.get('district_name')),
                    "taluk": _loc(jur.get('taluk'), app.get('taluk_name')),
                    "town": _loc(jur.get('town'), app.get('town_name')),
                    "ward": _loc(jur.get('ward'), app.get('ward_number')),
                    "block": _loc(jur.get('block'), app.get('block_number'))
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
                if req_address:
                    extra_td += f"<td>{_e(app.get('applicant_address') or 'N/A')}</td>"
                if req_channel:
                    extra_td += f"<td>{_e(_CHANNEL_CELL.get(app.get('submission_channel'), app.get('submission_channel') or 'N/A'))}</td>"

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
                f"<th>{t['application_no']}</th>{extra_th}{channel_th}<th>{t['type']}</th><th>{t['survey_no']}</th><th>{t['subdivisions']}</th>"
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
        # The parcel a bare survey number resolves to is not always unique, and a
        # named sub-division may not exist -- say both instead of quietly
        # answering about a different parcel or the whole parcel.
        notes = ""
        other_locations = structured_data.get("other_locations") or []
        if other_locations:
            where = ", ".join(
                f"ward {_e(loc.get('ward'))} / block {_e(loc.get('block'))}"
                for loc in other_locations
            )
            jur_here = f"{_e(jur.get('ward'))} / {_e(jur.get('block'))}"
            notes += (
                f"<div class='table-intro'>Note: Survey No. "
                f"{_e(structured_data.get('base_survey_no'))} also exists in {where}. "
                f"Shown below is the parcel in {jur_here}.</div>"
            )
        if structured_data.get("requested_sub_division") and \
                structured_data.get("sub_division_found") is False:
            notes += (
                f"<div class='table-intro'>Note: Sub-division "
                f"<strong>{_e(structured_data.get('requested_sub_division'))}</strong> "
                f"is not on record for this parcel. All its sub-divisions are listed "
                f"below.</div>"
            )

        return (
            notes
            + f"<div class='table-intro'><strong>{t['survey_no']} {_e(structured_data['survey_no'])}</strong></div>"
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
        
        # This block's OWN prose (the count sentence and the "none scheduled"
        # line) used to be pinned to the `lang="en"` forced above the table
        # headers -- the headers must stay English for the frontend's
        # status-badge JS, but that requirement has nothing to do with this
        # narrative text. A Tamil "நாளை என்ன கள ஆய்வு உள்ளது" got back an
        # all-English "Field Visits for Tomorrow (...): No field visits
        # scheduled." `_is_ta` is the OFFICER'S actual language, not the
        # header-compat override.
        _is_ta = language in ("ta", "tanglish")
        # "show my first 2 field visits" -- the rows were capped after the
        # query, so the total stated must still be the officer's real total.
        _fv_limit = structured_data.get("result_limit") or {}
        _fv_total = _fv_limit.get("total") or len(field_visits)
        if to_be_visited_count is not None and structured_data.get("to_be_visited_only"):
            count_info = (f"பார்வையிட வேண்டிய <strong>{to_be_visited_count}</strong> கள ஆய்வு(கள்) கிடைத்தன"
                          if _is_ta else
                          f"Found <strong>{to_be_visited_count}</strong> field visit(s) needed to be visited")
        else:
            count_info = (f"<strong>{_fv_total}</strong> கள ஆய்வு(கள்) கிடைத்தன" if _is_ta else
                          f"Found <strong>{_fv_total}</strong> field visit(s)")
            # A bare total is the one number an officer can misread. Most of a
            # working officer's visits are finished, so "13 field visits" over a
            # table whose top rows all say "Completed" invites the reading that
            # 13 are outstanding. Say what the 13 are made of, from the counts
            # the query already returned -- never recomputed from the page.
            _done = structured_data.get("completed_count")
            _todo = structured_data.get("to_be_visited_count")
            _late = structured_data.get("overdue_count") or 0
            if not structured_data.get("status_filter") and _done is not None and _todo is not None \
                    and (_done + _todo) == _fv_total and _done and _todo:
                if _is_ta:
                    _late_note = f", {_late} தாமதமானவை" if _late else ""
                    count_info += (f" — <strong>{_done}</strong> முடிந்தவை, "
                                   f"<strong>{_todo}</strong> இன்னும் பார்வையிட வேண்டியவை{_late_note}")
                else:
                    _late_note = f", {_late} overdue" if _late else ""
                    count_info += (f" — <strong>{_done}</strong> completed, "
                                   f"<strong>{_todo}</strong> still to visit{_late_note}")

        if _fv_limit.get("total"):
            _fv_n = _fv_limit.get("n", len(field_visits))
            _fv_end = _fv_limit.get("end", "head")
            _fv_pool = _fv_limit["total"]
            if _fv_end == "head_nth":
                # Ordinal / singular pick: "newest field visit", "2nd oldest visit"
                _is_fv_desc = any(w in query.lower() for w in ("newest", "latest", "recent"))
                if _fv_n == 1:
                    _fv_note = (f" — newest of {_fv_pool}" if _is_fv_desc
                                else f" — oldest of {_fv_pool}")
                    if _is_ta:
                        _fv_note = (f" — {_fv_pool} இல் புதியது" if _is_fv_desc
                                    else f" — {_fv_pool} இல் பழையது")
                else:
                    _ord_fv = _ordinal_suffix(_fv_n)
                    _fv_note = (f" — {_ord_fv} of {_fv_pool}" if not _is_ta
                                else f" — {_fv_pool} இல் {_fv_n}-வது")
                # If out of range, show nothing useful was found
                if _fv_limit.get("out_of_range"):
                    _ord_fv = _ordinal_suffix(_fv_n)
                    _oor = (f"There is no {_ord_fv} field visit — only {_fv_pool} exist."
                            if not _is_ta
                            else f"{_fv_n}-வது கள ஆய்வு இல்லை — மொத்தம் {_fv_pool} மட்டுமே உள்ளன.")
                    return f"<div class='table-intro'>{_e(_oor)}</div>"
                count_info += _fv_note
            elif _fv_end == "tail":
                count_info += (f" — கடைசி {_fv_n} மட்டும்" if _is_ta
                               else f" — showing the last {_fv_n}")
            else:
                count_info += (f" — முதல் {_fv_n} மட்டும்" if _is_ta
                               else f" — showing the first {_fv_n}")

        if not field_visits:
            # The handler may know WHY the list is empty and what is true
            # instead ("nothing is past its date, but 1 has never been
            # booked"). A generic "No field visits scheduled." under the
            # heading "Overdue Field Visits" answers a different question from
            # the one asked and reads as "nothing to do".
            _none_msg = structured_data.get("empty_message") or (
                "எந்த கள ஆய்வும் திட்டமிடப்படவில்லை." if _is_ta
                else "No field visits scheduled.")
            return f"<div class='table-intro'><strong>{query_type}</strong>{date_info}: {_none_msg}</div>"
        
        # "along with district / ward / taluk / town" -- geography columns for the visit table
        _fvq = (query or "").lower()
        _fv_add = bool(re.search(r"\b(along|with|add|adding|also|include|including|plus)\b", _fvq))
        _geo_cols = [(k, lbl) for k, lbl in (("district", t["district"]), ("taluk", t["taluk"]),
                                             ("town", t["town"]), ("ward_number", t["ward"]))
                     if _fv_add and re.search(r"\b" + k.split("_")[0] + r"s?\b", _fvq)]
        extra_th += "".join(f"<th>{lbl}</th>" for _k, lbl in _geo_cols)
        # "no type" / "without status" / "hide block": a default column can be taken away
        _drop_words = {"type": "type", "status": "status", "block": "block", "survey": "survey",
                       "subdivision": "subdiv", "subdivisions": "subdiv", "sub-division": "subdiv",
                       "sub-divisions": "subdiv", "date": "sched", "scheduled": "sched"}
        _fv_drop = {_drop_words[w.replace(" ", "-") if "sub" in w else w] for w in re.findall(
            r"\b(?:no|without|not|hide|exclude|remove|drop|skip)\s+(?:the\s+)?(?:along\s+)?"
            r"(type|status|block|survey|sub[\s-]?divisions?|scheduled|date)\b", _fvq)
            if (w.replace(" ", "-") if "sub" in w else w) in _drop_words}
        _fv_keep = [k for k in ("survey", "subdiv", "block", "type", "status", "sched") if k not in _fv_drop]
        rows = ""
        for fv in field_visits:
            extra_td = ""
            if req_name:
                extra_td += f"<td>{_e(fv.get('applicant_name') or 'N/A')}</td>"
            if req_mobile:
                extra_td += f"<td>{_e(fv.get('applicant_mobile') or 'N/A')}</td>"
            if req_address:
                extra_td += f"<td>{_e(fv.get('applicant_address') or 'N/A')}</td>"
            for _k, _lbl in _geo_cols:
                extra_td += f"<td>{_e(fv.get(_k) or 'N/A')}</td>"

            subdiv_val = fv.get('subdivisions') or fv.get('sub_division_no') or '-'
            cells = {
                "survey": f"<td>{_e(fv.get('raw_survey_no') or fv.get('survey_no') or 'N/A')}</td>",
                "subdiv": f"<td>{_e(subdiv_val)}</td>",
                "block": f"<td>{_e(fv.get('block_number'))}</td>",
                "type": f"<td>{_e(fv.get('application_type'))}</td>",
                "status": f"<td>{_status(fv.get('status'), lang)}{' ⚠️' if fv.get('is_overdue') else ''}</td>",
                "sched": f"<td>{_e(fv.get('field_visit_date') or 'Not Scheduled')}</td>",
            }
            rows += (f"<tr><td>{_e(fv.get('application_number'))}</td>{extra_td}"
                     + "".join(cells[k] for k in _fv_keep) + "</tr>")

        heads = {"survey": t['survey_no'], "subdiv": t['subdivisions'], "block": t['block'],
                 "type": t['type'], "status": t['status'], "sched": t['scheduled_date']}
        return (
            f"<div class='table-intro'><strong>{query_type}</strong>{date_info}: {count_info}</div>"
            f"<table class='data-table'>"
            f"<thead><tr>"
            f"<th>{t['application_no']}</th>{extra_th}" + "".join(f"<th>{heads[k]}</th>" for k in _fv_keep) +
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
        "address", "முகவரி",
        "status", "நிலை",
        "stage", "கட்டம்",
        "date", "தேதி",
        "survey number", "கணக்கெண்",
    ]
    
    # A request that explicitly asks for depth ("explain each stage", "list all
    # the steps", "walk me through", "in detail") must NOT be forced into the
    # 1-2 sentence direct-answer mode just because it also contains a field word
    # like "status" or "stage".
    wants_detail = any(w in query_lower for w in [
        "explain", "describe", "detail", "in detail", "elaborate", "walk me",
        "walk through", "step by step", "step-by-step", "all the steps",
        "each step", "each stage", "break down", "breakdown", "list all",
        "everything about", "full ", "comprehensive", "விளக்கு", "விவரி",
        "விரிவாக", "படிப்படியாக", "அனைத்து படி",
    ])
    is_specific_field_query = (not wants_detail) and \
                              any(kw in query_lower for kw in field_query_keywords) and \
                              any(w in query_lower for w in ["what", "என்ன", "யார்", "who", "எது", "which", "எப்போது", "when"])
    
    # When the caller bypassed the HTML table path (interrogative queries),
    # we inject a focused instruction so the LLM directly answers the question
    # instead of producing a generic summary.
    direct_answer_instruction = ""
    if (direct_answer or is_specific_field_query) and not wants_detail:
        direct_answer_instruction = """
IMPORTANT — DIRECT ANSWER MODE:
The user asked a specific question about a particular field or detail. Answer ONLY that question using the
structured data provided below. Do NOT summarise all fields. Do NOT produce a table. 
Write 1-2 plain sentences that directly answer what was asked.

Examples of GOOD direct answers. The <angle brackets> are placeholders: fill them ONLY from the
structured data below, and if that data is missing, say you need the application number instead.
- Q: "What is the applicant name?" / "விண்ணப்பதாரர் பெயர் என்ன?"
  A: "The applicant name for <application number> is <name>." / "<application number> விண்ணப்பதாரர் பெயர் <name>."

- Q: "Which sub-divisions are included?"
  A: "Merge application <application number> includes <n> sub-divisions: <list them with their areas>."

- Q: "What is the status?" / "நிலை என்ன?"
  A: "The application status is Pending." / "விண்ணப்பம் நிலுவையில் உள்ளது."

Examples of BAD answers (do NOT do this):
  "Here are the details for <application number>. Type: MERGE, Status: Approved, Applicant Name: John..."
  — and NEVER copy a placeholder or an example value into an answer as if it were real data.
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
6. ALWAYS use the correct definitions and expansions: ISD = **Involving Sub-Division** (0154, creates new
   sub-divisions), NISD = **Not Involving Sub-Division** (0153, transfer only, creates none), MERGE = 0155
   (combines sub-divisions). ISD is NEVER "Individual Sub-Division" and NISD is NEVER "Non-Individual".
7. NEVER mention "RAG context", "database", "knowledge base", "system data" or any technical terms in responses to users.
8. NEVER invent an application number, or describe/guess its format ("APP-2024-000001" and similar are all
   fabricated — the real format is YEAR/SERVICE_CODE/DISTRICT_CODE/SERIAL_NUMBER, e.g. 2026/0154/28/001167).
   If asked for an example or the format, say you don't have a specific one to give and that they should
   check the officer dashboard, rather than making one up.
9. If no application data is supplied below, you do NOT know any application's applicant, dates, status or
   field visit. Say which application number you need; never answer with details for an application you
   were not given.
10. If the user asks an unrelated question (e.g. about SEO, general technology, politics) or if the query contains unreadable typos or gibberish, simply reply that you cannot understand the question or that it is outside the SIS domain. Do NOT try to answer unrelated topics.

When answering questions:
- Use natural, conversational language
- Present information as if you know it directly, not as if you're reading from a database
- If you don't have information, say "I don't have that information" not "The database doesn't contain..."
- Example GOOD: "Application 2026/0154/28/001167 is currently at the Sub Inspector Surveyor stage."
  (that number is an illustration of the FORMAT only — never repeat it in an answer)
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
            role = msg.get("role") or "user"
            content = msg.get("content", "") or ""
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
        response = await asyncio.wait_for(
            llm.ainvoke(prompt), timeout=settings.LLM_TIMEOUT_SECONDS)
        response_text = response.content if hasattr(response, "content") else str(response)
        response_text = response_text.strip()
        logger.info(f"LLM response generated ({len(response_text)} chars)")
        return response_text
    except asyncio.TimeoutError:
        logger.error(f"LLM call timed out after {settings.LLM_TIMEOUT_SECONDS}s")
        return ("This is taking longer than expected to answer. Please try again, "
                "or narrow the question.")
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


# ── Comparison questions ─────────────────────────────────────────────────────
# "which is older, A or B", "ISD vs NISD", "CSC compared to the Sub-Registrar",
# "which application took the longest to approve". Each of these used to be
# answered with a plain list -- "compare ISD and NISD" returned the one pending
# ISD file -- or fell through to the LLM, which has no counts to compare.

# Everything below matches on TOKENS, not raw substrings, and every token is
# compared exactly or as a typo within `is_token_typo_match`'s shared edit
# budget -- the same rule the rest of parse_intent uses. Officers type
# "compair", "nsid", "approvd" and "longst"; a comparison parser built on bare
# regex answered none of those.

# The cue that a comparison is being asked for at all.
_COMPARE_CUE_WORDS = (
    "compare", "compares", "compared", "comparing", "comparison", "comparisons",
    "vs", "versus", "verses", "against", "difference", "differences",
    "differ", "differs", "more", "fewer", "less", "higher", "lower",
    "better", "worse", "bigger", "smaller",
    # A comparative is itself the cue: "do ISD take LONGER than NISD",
    # "is CSC FASTER than the Sub-Registrar". Harmless on its own -- a cue
    # with no sides and no superlative yields no comparison.
    "longer", "shorter", "slower", "faster", "quicker", "older", "newer",
    # Tanglish, as officers type it
    "oppidu", "oppitu", "ottidu", "adhigam", "athigam", "kammi", "edhu", "ethu",
)
# Tamil is matched as a substring: the script carries meaning per glyph, so
# is_token_typo_match refuses it a typo budget and inflection makes token
# equality unreliable.
_COMPARE_CUE_TA = ("ஒப்பிட", "ஒப்பீட", "அதிக", "குறைவ", "வித்தியாச", "எது")
# "which is/was/has/took ..." is a comparison question without any cue word.
_COMPARE_WHICH_RE = re.compile(
    r"\bwh?[iy]ch\b|\bwich\b|\bwhcih\b", re.IGNORECASE)
_COMPARE_WHICH_VERB = ("is", "was", "has", "have", "had", "took", "takes",
                       "get", "gets", "got")

# The quality being compared. Order matters: "took longer to approve" is a
# duration question, not an approval question.
_COMPARE_ASPECT_WORDS = (
    ("duration", ("longer", "longest", "slower", "slowest", "faster", "fastest",
                  "quicker", "quickest", "turnaround", "delay", "delayed",
                  "delays", "duration", "speed")),
    # "last" is deliberately absent: it is one edit from "least" and its own
    # meaning ("my last application") belongs to another intent entirely.
    ("age", ("older", "oldest", "newer", "newest", "latest", "earlier",
             "earliest", "recent", "first")),
    ("area", ("area", "extent", "acreage")),
    ("fee", ("fee", "fees", "charge", "charges", "cost", "costs", "costlier")),
    ("count", ("many", "count", "number", "total")),
)
_COMPARE_ASPECT_PHRASES = (
    ("duration", ("how long", "processing time", "time taken", "took time",
                  "least time", "most time", "more time", "less time",
                  "time to approve", "time to decide", "time to clear",
                  "approval time", "decision time")),
    ("age", ("filed first", "submitted first")),
)
_COMPARE_ASPECT_TA = (("duration", ("நாட்கள்", "காலம்", "தாமத")),
                      ("age", ("பழைய", "சமீபத்திய")),
                      ("area", ("பரப்பளவு",)),
                      ("fee", ("கட்டணம்",)))

_SUPERLATIVE_WORDS = ("longest", "shortest", "fastest", "slowest", "oldest",
                      "newest", "most", "least", "quickest", "biggest",
                      "largest", "smallest", "maximum", "minimum",
                      "busiest", "quietest", "heaviest", "lightest")
_SUPERLATIVE_MIN_WORDS = ("shortest", "fastest", "quickest", "least",
                          "smallest", "newest", "minimum", "quietest",
                          "lightest")
# A bare superlative + a duration-shaped word ("fastest", "longest") is also
# ordinary English trivia -- "fastest animal on earth", "longest river in the
# world". Without evidence that the question is actually about the officer's
# own applications, the no-sides superlative branch below answered those as a
# comparison over the register. Every real test case names one of these.
_COMPARE_SUPERLATIVE_DOMAIN = (
    "application", "applications", "app", "apps", "file", "files",
    "approve", "approved", "approval", "approving",
    "reject", "rejected", "rejection", "rejecting", "cancel", "cancelled", "canceling",
    "turnaround", "processing", "decide", "decided", "decision",
    "clear", "cleared", "close", "closed",
    "விண்ணப்ப", "கோப்பு",
)

# The sides. A short code gets no typo budget (`_max_edits_for` gives 3-letter
# targets 0 edits), which is exactly right: "isd" must never absorb "nisd".
_COMPARE_TYPES = (("ISD", ("isd", "0154")), ("NISD", ("nisd", "0153")),
                  ("MERGE", ("merge", "merges", "merged", "0155")))
_COMPARE_STATUSES = (("pending", ("pending",)),
                     ("approved", ("approved", "approve", "completed", "cleared")),
                     ("rejected", ("rejected", "reject", "refused", "cancelled", "cancel")),
                     ("in_progress", ("progress",)))
_COMPARE_CHANNELS = (("CSC", ("csc",)),
                     ("sub_registrar", ("sro", "igrs", "registrar", "subregistrar")),
                     ("citizen", ("citizen", "portal")))
_COMPARE_STATUS_TA = (("pending", ("நிலுவை",)),
                      ("approved", ("அங்கீகரி",)),
                      ("rejected", ("நிராகரி",)))


# Words that carry their own meaning and must never be absorbed as a typo of a
# comparison keyword. "last" is one edit from "least": left unguarded it made
# "what about last month" a superlative and "the least time" an age question.
_NEVER_TYPO = frozenset({"last", "late", "list", "most", "post", "are"})


def _tok_index(tokens: list, vocabulary, max_edits: Optional[int] = None) -> Optional[int]:
    """Position of the first token matching `vocabulary`, exactly or as a typo.

    `max_edits` overrides the length-based budget. It is raised to 2 only for
    the comparison cue words, which are long and distinctive: "compair" is two
    substitutions from "compare", and the default budget of 1 for a seven
    letter word left that spelling -- a common one -- unmatched.
    """
    for i, token in enumerate(tokens):
        exact_only = token in _NEVER_TYPO
        for word in vocabulary:
            if token == word:
                return i
            if exact_only:
                continue
            budget = max_edits
            if budget is not None and len(word) < 7:
                budget = None       # keep short words strict
            if is_token_typo_match(token, word, max_edits=budget):
                return i
    return None


def _compare_aspect(tokens: list, msg: str) -> Optional[str]:
    # Phrases first. "least time" is a duration, but "least" on its own is one
    # edit from "last", so word matching alone read it as an age question and
    # the superlative -- which only measures duration -- refused the question.
    for name, phrases in _COMPARE_ASPECT_PHRASES:
        if any(match_phrase(tokens, phrase) for phrase in phrases):
            return name
    for name, words in _COMPARE_ASPECT_WORDS:
        if _tok_index(tokens, words) is not None:
            return name
    for name, needles in _COMPARE_ASPECT_TA:
        if any(n in msg for n in needles):
            return name
    return None


def _compare_sides(tokens: list, msg: str, table, ta_table=()) -> list:
    """The members of `table` this message names, in the order they appear."""
    hits = []
    for name, words in table:
        idx = _tok_index(tokens, words)
        if idx is not None:
            hits.append((idx, name))
    found = {name for _i, name in hits}
    for name, needles in ta_table:
        if name in found:
            continue
        for needle in needles:
            if needle in msg:
                hits.append((msg.index(needle) + 1000, name))
                break
    return [name for _i, name in sorted(hits)]


def _has_compare_cue(tokens: list, msg: str) -> bool:
    if _tok_index(tokens, _COMPARE_CUE_WORDS, max_edits=2) is not None:
        return True
    if any(n in msg for n in _COMPARE_CUE_TA):
        return True
    # "which is older", "which took longer" -- the cue is the construction.
    if _COMPARE_WHICH_RE.search(msg):
        return _tok_index(tokens, _COMPARE_WHICH_VERB) is not None
    return False


# Two periods, kept apart. `extract_month_scopes` deliberately MERGES
# contiguous months into one segment -- right for "June and July applications",
# exactly wrong for "compare June and July", where the whole point is the two
# sides staying separate.
_PERIOD_YEAR_PHRASES = [
    (r"(?:last|previous|prev|past)\s+year\b", 1),
    (r"(?:this|current|present)\s+year\b", 0),
    (r"(?:கடந்த|சென்ற|முந்தைய)\s*(?:ஆண்டு|வருடம்)", 1),
    (r"இந்த\s*(?:ஆண்டு|வருடம்)", 0),
]


def _compare_periods(message: str) -> list:
    """The periods this message names, in the order named, never merged.

    Returns [(label, start, end), ...] -- at least two entries for a period
    comparison, or [] when the message names fewer than two.
    """
    cleaned = normalize_relative_date_tokens(clean_message(message).lower())
    today = date.today()
    found = []          # (position in the message, label, start, end)

    scan = cleaned
    for pattern, back in _RELATIVE_MONTH_PHRASES:
        for m in re.finditer(pattern, scan):
            year, month = _shift_month(today, back)
            start, end = _month_bounds(year, month)
            found.append((m.start(), f"{_MONTH_LABELS[month]} {year}", start, end))
        scan = re.sub(pattern, lambda mo: " " * len(mo.group(0)), scan)

    for pattern, back in _PERIOD_YEAR_PHRASES:
        for m in re.finditer(pattern, scan):
            year = today.year - back
            found.append((m.start(), str(year),
                          date(year, 1, 1), date(year, 12, 31)))
        scan = re.sub(pattern, lambda mo: " " * len(mo.group(0)), scan)

    for name, num in _MONTH_NAME_MAP.items():
        consumed = []
        for m in re.finditer(_month_name_re(name) + r"(?:\s+(\d{4}))?", scan):
            if re.match(r"\s+\d{1,2}\b(?!\d)", scan[m.end():]):
                continue      # "June 25" is a date, not a month
            year = int(m.group(1)) if m.group(1) else resolve_month_year(num, today)
            start, end = _month_bounds(year, num)
            found.append((m.start(), f"{_MONTH_LABELS[num]} {year}", start, end))
            consumed.append(m.span())
        # Blank out "march 2025" whole, or the bare-year pass below counts its
        # year a second time and "compare march 2025 and march 2026" comes back
        # with four periods instead of two.
        for lo, hi in reversed(consumed):
            scan = scan[:lo] + " " * (hi - lo) + scan[hi:]

    # Two bare years side by side -- "2025 vs 2026".
    for m in re.finditer(r"\b(20\d{2})\b", scan):
        year = int(m.group(1))
        found.append((m.start(), str(year), date(year, 1, 1), date(year, 12, 31)))

    seen, periods = set(), []
    for _pos, label, start, end in sorted(found):
        if (start, end) in seen:
            continue
        seen.add((start, end))
        periods.append((label, start, end))
    return periods if len(periods) >= 2 else []


def parse_comparison_query(message: str) -> Optional[dict]:
    """What two things the officer is comparing, or None if it is not a comparison.

    Shapes, in the order they are tried:

      applications  two application numbers  -- "which is older, A or B"
      type/status/channel  two groups        -- "ISD vs NISD", "CSC or SRO"
      superlative   one extreme over the set -- "which took the longest"

    Matching is token-based and typo-tolerant throughout, so "compair",
    "nsid", "approvd" and "longst" all land where they should.
    """
    raw = (message or "").strip()
    if not raw:
        return None
    numbers = re.findall(r"\d{4}/\d{3,4}/\d{1,3}/\d+", raw)
    # "which service code is 0153?", "difference between service code 0153 and
    # 0154" -- a question about the code table, not a count of the officer's
    # files. 0153 / 0154 are exactly the NISD / ISD tokens the group table
    # reads, so "which service code is 0153" was answered "ISD 13 vs NISD 76".
    # It belongs to service_code_lookup / service_code_guide. An application
    # number in the message is a genuine file comparison and is kept.
    if not numbers and re.search(r"\bservice\s+codes?\b", raw, re.IGNORECASE):
        return None
    # An application number carries its own service code, and 0153 / 0154 are
    # exactly the tokens the type table looks for: left in, "compare
    # 2026/0153/28/001190 and 9999" was answered as ISD vs NISD. Strip the
    # numbers before reading group sides out of the words around them.
    msg = normalize_text(re.sub(r"\d{4}/\d{3,4}/\d{1,3}/\d+", " ", raw))
    tokens = extract_tokens(msg)
    if not tokens:
        return None
    aspect = _compare_aspect(tokens, msg)
    has_cue = _has_compare_cue(tokens, msg)

    # Two named applications is a comparison whatever words surround them.
    if len(numbers) >= 2:
        if numbers[0] == numbers[1]:
            return None          # "compare A and A" is not a comparison
        if not (has_cue or aspect):
            return None
        return {"kind": "applications", "left": numbers[0], "right": numbers[1],
                "aspect": aspect}

    # Two periods -- "compare this month and last month", "2025 vs 2026".
    # Checked before the group tables so "compare June and July" is not read as
    # a bare cue with no sides, and after the two-number case so an application
    # number's year never becomes a period.
    #
    # "how many" alone sets aspect=="count" (it is the ordinary count word,
    # not a comparison), so "how many applications BETWEEN March and June"
    # matched this branch on "many" alone and compared March against June as
    # two separate points -- "March 2026 4, June 2026 3 ... ahead by 1" --
    # instead of counting the inclusive range, which is what "between ... and"
    # / "from ... to" always means for a month span (see `_RANGE_CONNECTOR_RE`
    # just above `extract_month_scopes`). A real compare cue ("compare",
    # "vs", "difference", ...) still wins even inside a range-shaped
    # sentence; only the bare aspect=="count" trigger is guarded.
    # a bare "how many ... in jan and feb 2025" is the two months TOGETHER; comparing them needs a real cue
    if has_cue:
        # An application number's own year must not become a period -- a
        # follow-up that had "2026/0153/28/001720" appended to it (the
        # resolved reference from a prior "last application" answer) read
        # its embedded "2026" as a second period to compare against "this
        # month", the same trap the numbers-stripped `msg` above exists to
        # avoid for the type/status tables. Strip the number here too.
        periods = _compare_periods(
            re.sub(r"\d{4}/\d{3,4}/\d{1,3}/\d+", " ", raw))
        if periods:
            return {"kind": "period", "aspect": "count",
                    "periods": [{"label": p[0], "start": p[1].isoformat(),
                                 "end": p[2].isoformat()} for p in periods],
                    "left": periods[0][0], "right": periods[1][0]}

    # A group comparison is a comparison of COUNTS -- "ISD vs NISD" means how
    # many of each. "Is there a fee difference between ISD and NISD" asks what
    # the two service codes charge, which the service-code guide answers from
    # the fee schedule; counting the officer's files would not answer it.
    # A group comparison is about COUNTS ("ISD vs NISD") or about TIME ("do
    # ISD take longer than NISD"). It is never about the fee: "is there a fee
    # difference between ISD and NISD" asks the fee schedule, which the
    # service-code guide answers, and counting the officer's files would not
    # answer it.
    if aspect in (None, "count", "duration"):
        for kind, table, ta_table in (("type", _COMPARE_TYPES, ()),
                                      ("status", _COMPARE_STATUSES, _COMPARE_STATUS_TA),
                                      ("channel", _COMPARE_CHANNELS, ())):
            sides = _compare_sides(tokens, msg, table, ta_table)
            if len(sides) >= 2 and has_cue:
                # "ISD vs NISD vs MERGE" names three; reporting only the first
                # two would silently drop the one the officer asked about last.
                return {"kind": kind, "left": sides[0], "right": sides[1],
                        "sides": sides,
                        "aspect": "duration" if aspect == "duration" else "count"}

    # Wards: "compare ward 102 and ward 103", "which ward has more".
    # "which ward is 2022/0153/28/000468 in" names one application and asks for
    # a field of it -- not a comparison -- so an application number rules this
    # branch out entirely, and the wordless form needs a quantity word rather
    # than the generic "which ... is" cue.
    # "வார்டு 102 மற்றும் வார்டு 103 ஒப்பிடு" carried no English "ward" token at
    # all, so this stayed empty and the whole comparison fell through to
    # general_query -- which then answered from its own head about "block
    # numbers", a topic nobody asked about. Tamil is matched as a substring,
    # never with \b (the virama trap documented throughout this file).
    _wards = [] if numbers else (
        re.findall(r"\bward\s*(\d{1,4})\b", msg) + re.findall(r"வார்டு\s*(\d{1,4})", msg))
    _quantity = _tok_index(tokens, ("more", "most", "fewer", "fewest", "least",
                                    "busiest", "quietest", "highest", "lowest",
                                    "compare", "vs", "versus", "than")) is not None
    if _wards and (len(_wards) >= 2 or (has_cue and _quantity)):
        seen_w, ordered = set(), []
        for w in _wards:
            key = w.zfill(3)
            if key not in seen_w:
                seen_w.add(key)
                ordered.append(key)
        if len(ordered) >= 2:
            return {"kind": "ward", "left": ordered[0], "right": ordered[1],
                    "sides": ordered, "aspect": "count"}
        return {"kind": "ward", "left": None, "right": None, "aspect": "count"}
    if (not numbers and has_cue and _quantity
            and _tok_index(tokens, ("ward", "wards")) is not None):
        return {"kind": "ward", "left": None, "right": None, "aspect": "count"}

    # "which month had the most applications" -- the busiest month. "month"
    # was checked as an English token only, so "எந்த மாதம் அதிக விண்ணப்பங்கள்
    # வந்தது" and the Tanglish "which month la athigama applications
    # vanthuchu" both missed this branch entirely and fell through to a bare
    # applications listing -- a table of 2 rows, not an answer to "which
    # month". Tanglish "matham" is added as a literal token candidate (not a
    # typo of "month" -- the edit distance is too large for `_tok_index`'s
    # budget); the Tamil-script word and the அதிக/குறைவ/மிக superlative
    # marker are matched as substrings, the same pattern already used for the
    # duration superlative a few lines below.
    _month_word = (_tok_index(tokens, ("month", "months", "matham", "madham")) is not None
                  or "மாதம்" in msg)
    _ta_quantity = any(n in msg for n in ("அதிக", "குறைவ", "மிக"))
    # "which application/file was approved this month" is a single-application
    # recency question with "this month" as a date filter, not a request to
    # compare months. `has_cue` fires from the generic "which ... was" shape
    # (_COMPARE_WHICH_RE), which is right for "which month had the most" but
    # wrongly claims any "which application ..." sentence that happens to
    # mention "month" too. Only let it count here when "which" actually asks
    # about the month, not about an application/file.
    _which_asks_month = bool(re.search(
        r"\bwh?[iy]ch\b(?:\s+\w+){0,2}\s+month\b", msg))
    if not numbers and _month_word and (
            _tok_index(tokens, _SUPERLATIVE_WORDS) is not None
            or _ta_quantity or (has_cue and _which_asks_month)):
        return {"kind": "month", "aspect": "count",
                "extreme": ("min" if (_tok_index(tokens, _SUPERLATIVE_MIN_WORDS) is not None
                                       or "குறைவ" in msg)
                            else "max")}

    # "which application took the longest to approve" -- one extreme, no sides.
    # Only duration: "my oldest pending application" and "my last approved one"
    # already have handlers that answer them well, and stealing those here
    # would be a regression dressed as a feature.
    # Tamil marks the superlative with அதிக / குறைவ rather than an -est form.
    _ta_superlative = any(n in msg for n in ("அதிக", "குறைவ", "மிக"))
    if (_tok_index(tokens, _SUPERLATIVE_WORDS) is not None or _ta_superlative) \
            and aspect == "duration" \
            and (_tok_index(tokens, _COMPARE_SUPERLATIVE_DOMAIN) is not None
                 or any(t in msg for t in _COMPARE_SUPERLATIVE_DOMAIN)):
        # "which has been PENDING the longest" measures how long an open file
        # has been waiting, not how long a decided one took -- pending_longest
        # answers that, and this superlative has no decision date to measure.
        if _tok_index(tokens, ("pending", "waiting", "open", "unresolved")) is not None \
                or "நிலுவை" in msg:
            return None
        # "longest to APPROVE" is a question about approvals, so a rejected
        # file that sat longer is not the answer to it.
        status = None
        if _tok_index(tokens, ("approve", "approved", "approval", "cleared",
                               "clearance")) is not None:
            status = "approved"
        elif _tok_index(tokens, ("reject", "rejected", "rejection", "cancel", "cancelled", "canceling")) is not None:
            status = "rejected"
        _min = (_tok_index(tokens, _SUPERLATIVE_MIN_WORDS) is not None
                or "குறைவ" in msg)
        return {"kind": "superlative", "aspect": aspect, "status": status,
                "extreme": "min" if _min else "max"}

    # "what is the average time to approve" -- one number over the whole desk.
    if aspect == "duration" and _tok_index(tokens, ("average", "avg", "mean",
                                                    "typical", "usually")) is not None:
        status = "approved" if _tok_index(tokens, ("approve", "approved",
                                                   "approval")) is not None else None
        return {"kind": "average_duration", "aspect": "duration", "status": status}

    # "which channel has more applications" -- the sides are implied.
    # An application number rules this out: "which channel is 2026/0153/28/001190"
    # names one file and asks for its channel field, not a count each way -- the
    # same guard the ward branch above applies. A singular back-reference does
    # the same: "which channel was IT filed through?" is a follow-up field
    # question about the file in view, not a jurisdiction-wide breakdown --
    # unless a real quantity word ("more", "most", "compare", "vs") is present.
    _backref = _tok_index(tokens, ("it", "its", "this", "that")) is not None
    _quantity_cmp = _tok_index(tokens, (
        "more", "most", "fewer", "fewest", "least", "busiest", "quietest",
        "highest", "lowest", "compare", "vs", "versus", "than", "each")) is not None
    _implied_ok = has_cue and not (_backref and not _quantity_cmp)
    # "how many applications from each channel", "channel-wise breakdown",
    # "split by source" -- a request for the count of every channel at once.
    # These carry no comparison cue ("how many" alone only sets aspect=count),
    # so without this they were read as a listing scoped to all three channels
    # and answered with a 209-row table where a three-line breakdown was asked
    # for. The phrasing is matched tightly -- the grouping word has to sit on
    # the channel noun itself -- so "what is the channel of each application"
    # is left alone.
    _per_channel = bool(re.search(
        r"\b(?:each|per|every|by|across)\s+(?:submission\s+)?(?:channel|source|route|mode)s?\b"
        r"|\b(?:channel|source)s?[\s-]?wise\b"
        r"|\bbreak\s?downs?\b[^.?!]{0,24}\b(?:channel|source)s?\b"
        r"|\b(?:channel|source)s?\b[^.?!]{0,16}\bbreak\s?downs?\b", msg))
    if not numbers and (_implied_ok or _per_channel) \
            and _tok_index(tokens, ("channel", "channels", "source", "sources")) is not None:
        return {"kind": "channel", "left": None, "right": None, "aspect": "count"}
    if not numbers and _implied_ok and _tok_index(tokens, ("type", "types")) is not None \
            and _tok_index(tokens, ("application", "applications")) is not None:
        return {"kind": "type", "left": None, "right": None, "aspect": "count"}
    return None


# ─────────────────────────────────────────────────────────────────────────────
# parse_intent — specific patterns before broad ones
#   "show me survey 145"  → survey_detail  (not all_surveys_in_jurisdiction)
#   "show all surveys"    → all_surveys_in_jurisdiction
# ─────────────────────────────────────────────────────────────────────────────
# Sub-topics that remain meaningful when an application number is present. Only
# these may override the "message contains an application number -> status" rule;
# anything else keeps the existing generic behaviour.
_APP_SUBTOPIC_INTENTS = {
    "check_documents", "check_sale_deed", "sale_deed_check", "is_nisd_or_isd",
    "submission_channel_check", "can_apply_check",
    "merge_info", "isd_processing", "litigation_check", "rejection_info",
    "escalation_check", "joint_owner_check", "survey_owners",
    "field_visits", "fv_deadline_check", "fv_date_select", "fv_nearby_pending",
    "fv_reschedule_availability", "fv_change_date", "fv_scheduled_this_week",
    "sd_additional_info", "sd_encroachment_check", "sd_sketch_readiness",
    "sd_forward_check", "sd_remarks",
}

_APP_NUMBER_STRIP_RE = re.compile(
    r'\b\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+\b'
    r'|\b\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+\b'
    r'|\b(?:ISD|NISD|MERGE)/\w+/\d+/\d+\b'
    r'|\bAPP-\d+-\d+\b'
    r'|\b20\d{2}/[\w]+/[\w]+/\d+\b',
    re.IGNORECASE,
)


def _classify_application_subtopic(message: str, prev_intent: str = None):
    """
    Re-classify `message` with its application number removed, returning the
    result only when it is a recognised sub-topic (documents, sale deed, field
    visit, deadline, ...). Returns None otherwise, so the caller falls back to
    the generic application_status behaviour.
    """
    stripped = _APP_NUMBER_STRIP_RE.sub(" ", message or "").strip()
    if not stripped:
        return None
    try:
        # app_scoped=True: the number was there before we stripped it, so intent
        # rules that gate on "mentions an application" should still fire.
        candidate = parse_intent(stripped, prev_intent, app_scoped=True)
    except RecursionError:  # pragma: no cover - defensive
        return None
    if candidate not in _APP_SUBTOPIC_INTENTS:
        return None
    # "what is the survey number and existing patta number of X" is a
    # per-application FIELD lookup, not an ownership question -- but the bare
    # token "patta" sits in the owner-keyword list ("pattadar" == patta-holder),
    # so the number-stripped re-classification lands on survey_owners and the
    # field question is answered with an owner table. Honour survey_owners here
    # only when the sentence carries a genuine ownership word; otherwise fall
    # back to application_status, whose field map answers "survey number" /
    # "patta number" directly.
    if candidate == "survey_owners" and not (
        re.search(r"\b(owners?|ownership|owns|owned|co-?owner|joint\s+owner|"
                  r"pattadar|belongs?\s+to|who\s+holds|whose)\b",
                  stripped, re.IGNORECASE)
        or any(w in stripped for w in ("உரிமையாள", "சொந்தக்கார", "பட்டாதாரர்",
                                       "கூட்டுரிமை"))
    ):
        return None
    # "group survey / sub-division / patta number" name the `urban_application_log`
    # group_* columns, which the ORM projection drops. Routed as survey_detail /
    # survey_owners the deterministic "not in register" handler never sees them
    # and the officer got "No records found". Fall back to application_status,
    # whose _asked_untracked_source_field answers them.
    if candidate in ("survey_detail", "survey_owners") and re.search(
            r"\bgroup\b|குழு", stripped, re.IGNORECASE):
        return None
    return candidate


# Intents whose answer is scoped by a period, and which a bare date follow-up
# ("last month") therefore re-runs against the new period. Anything not listed
# here answers the same regardless of date, so inheriting it would be noise.
DATE_SCOPED_INTENTS = frozenset({
    "pending_applications", "isd_applications", "nisd_applications",
    "both_applications", "merge_applications", "overdue_applications",
    "town_applications", "block_applications", "applications_by_block",
    "active_applications_taluks",
    "highest_priority_applications", "assigned_today", "immediate_action",
    "officer_workload", "workload_by_type", "completion_rate",
    "pending_longest", "fee_summary",
    "field_visits", "fv_between_dates", "fv_scheduled_this_week",
    "fv_overdue_inspections", "fv_unassigned_awaiting", "fv_recently_rescheduled",
    "awaiting_field_visit", "escalation_check",
})


# ── "my last / previous application" ────────────────────────────────────────
# The officer refers to a file by recency instead of by number: "what is my
# prev application", "was my last application approved", "which application did
# I reject last". None of these carry an application number, so without this
# the question fell through to application_status, which then asked for the
# number the officer was trying to avoid typing.
_LAST_APP_RECENCY = (
    r"(?:last|latest|previous|prev|most\s+recent|recently|kadaisi|munthaiya)"
)
_LAST_APP_NOUN = r"(?:applications?|appls?|apps?|file|case|vinnappam)"
# "last month", "last week", "last 7 days" scope a period; they are not a
# reference to one application, and the date-scoped intents own them.
_LAST_APP_PERIOD = re.compile(
    r"\b(?:last|past|previous|prev|recent)\s+"
    r"(?:\d+\s+)?(?:day|days|week|weeks|month|months|year|years|yr|yrs|quarter|fortnight|"
    r"few|couple|several)\b",
    re.IGNORECASE,
)
# "the latest action on X", "last updated", "my last field visit" are about a
# field of an application, not about which application is the most recent one.
_LAST_APP_OTHER_NOUN = re.compile(
    r"\b(?:last|latest|previous|prev|most\s+recent|recent)\s+"
    r"(?:\w+\s+){0,1}?"
    r"(?:action|actions|update|updated|updates|status|stage|visit|visits|"
    r"inspection|inspections|remark|remarks|note|notes|entry|entries|"
    r"login|message|question|reply|owner|survey)\b",
    re.IGNORECASE,
)
# "my previous 2 applications", "the last three files" ask for a list, not for
# the one most recent application.
_LAST_APP_COUNTED = re.compile(
    rf"\b{_LAST_APP_RECENCY}\s+(?:\d+|two|three|four|five|several|few)\s+",
    re.IGNORECASE,
)
_LAST_APP_PATTERNS = (
    # "my last application", "the most recent app I handled", "prev application"
    re.compile(rf"\b{_LAST_APP_RECENCY}\s+(?:\w+\s+){{0,2}}?{_LAST_APP_NOUN}\b",
               re.IGNORECASE),
    # "the application I approved last", "which app did I reject most recently"
    re.compile(rf"\b{_LAST_APP_NOUN}\b(?:\s+\w+){{0,4}}\s+{_LAST_APP_RECENCY}\b",
               re.IGNORECASE),
)
# Tamil words carry a trailing virama that \b does not treat as a word end, so
# the Tamil forms are matched as plain substrings rather than by regex.
_LAST_APP_TA_RECENCY = ("கடைசி", "கடைசியாக", "முந்தைய", "சமீபத்திய", "முன்னைய")
_LAST_APP_TA_NOUN = ("விண்ணப்ப",)
_LAST_APP_STATUS_WORDS = (
    ("approved", ("approved", "approve", "accepted", "accept", "cleared", "sanctioned")),
    ("rejected", ("rejected", "reject", "denied", "declined", "refused", "cancelled", "cancel", "canceling")),
    ("pending", ("pending",)),
    ("in_progress", ("in progress", "in-progress", "ongoing")),
)
_LAST_APP_TA_STATUS = (
    ("approved", ("ஒப்புதல்", "அங்கீகரி", "அனுமதி")),
    ("rejected", ("நிராகரி",)),
    ("pending", ("நிலுவை",)),
)
# "is my previous application approved?" asks about the latest application and
# wants a yes/no, while "my last approved application" asks for the latest
# application that is approved. Both mention a status; the opening verb and the
# position of the status word together separate them -- see below.
_LAST_APP_YES_NO = re.compile(
    r"^\s*(?:so\s+)?(?:is|was|has|have|had|did|does|isn'?t|wasn'?t|"
    r"what\s+is|whats|what'?s|tell\s+me\s+(?:if|whether)|check\s+(?:if|whether))\b",
    re.IGNORECASE,
)
_LAST_APP_NOUN_RE = re.compile(rf"\b{_LAST_APP_NOUN}\b", re.IGNORECASE)

# One field of that application, asked for in the same breath: "what was the
# area of my last approved application". The key is what chatbot.py reads out of
# the application record; the order matters, since "total area of the survey"
# names the area, not the survey number.
_LAST_APP_FIELD_SPECS = (
    ("area", r"\barea\b|\bextent\b|\bsq\.?\s*m(?:tr|eter|etre)?s?\b|\bsquare\s+met|பரப்பளவு"),
    ("applicant", r"\bapplicant\b|\bapplicant'?s\b|\bwho\s+(?:is|was)\b|\bname\b|பெயர்"),
    ("mobile", r"\bmobile\b|\bphone\b|\bcontact\s+(?:no|number)\b|தொலைபேசி"),
    ("address", r"\baddress\b|முகவரி"),
    ("fee", r"\bfee\b|\bfees\b|\bchallan\b|\bpayment\b|\bpaid\b|\bcharge\b|கட்டணம்"),
    ("patta", r"\bpatta\b|பட்டா"),
    ("can", r"\bcan\s*(?:number|no\.?|id)\b"),
    ("subdivision", r"\bsub[\s-]?divisions?\b|\bsubdiv\b|உட்பிரிவு"),
    ("survey", r"\bsurvey\b|\bs\.?\s?no\b|கணக்கெண்"),
    ("deed", r"\b(?:sale\s+)?deed\b|\bregistered\b"),
    ("reason", r"\breason\b|\bwhy\b|காரணம்"),
    ("submission_date", r"\bsubmitted\b|\bsubmission\s+date\b|\bfiled\s+on\b|\bwhen\s+was\b|சமர்ப்பி"),
    # "when was my last application APPROVED" asks for the decision date, not
    # the filing date. Listed after submission_date and picked out by the
    # override below, so that "when was my last approved application
    # SUBMITTED" -- which names both -- still answers with the filing date.
    ("decision_date",
     r"\b(?:approval|rejection|closure|decision|disposal)\s+date\b"
     r"|\b(?:approved|rejected|closed|disposed|signed)\s+on\b"
     r"|அங்கீகரி|நிராகரி"),
    ("channel", r"\bchannel\b|\bcsc\b|\bsub[\s-]?registrar\b|\bwhere\s+was\s+it\s+(?:filed|submitted)\b"),
)
# The filing named explicitly -- these keep "when ... submitted" on the
# submission date even when a status word also appears in the question.
_LAST_APP_FILED_RE = re.compile(
    r"\bsubmitted\b|\bsubmission\b|\bfiled\b|\bapplied\b|\breceived\b|சமர்ப்பி",
    re.IGNORECASE)
# The decision named as the thing being dated.
_LAST_APP_DECIDED_RE = re.compile(
    r"\bapproved\b|\bapproval\b|\brejected\b|\brejection\b|\bclosed\b"
    r"|\bclosure\b|\bdisposed\b|\bdecided\b|\bdecision\b|\bsigned\b"
    r"|அங்கீகரி|நிராகரி",
    re.IGNORECASE)

_LAST_APP_FIELD_RES = tuple(
    (name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _LAST_APP_FIELD_SPECS
)


# ── "which application should I field visit tomorrow, and in which block?" ───
# A planning question, not a listing: the officer is asking what to do with a
# day, so the answer has to survive an empty calendar (see get_visit_plan).
_FV_PLAN_ACTION_RE = re.compile(
    r"\b(?:field\s*visits?|site\s*visits?|visits?|inspect|inspects|inspection|inspections)\b"
    r"|கள\s*ஆய்வு|களஆய்வு|வருகை",
    re.IGNORECASE,
)
# Questions that belong to a more specific field-visit intent: moving a date is
# the Tahsildar's call, and conflicts / allocation / deadlines each have their
# own handler. None of them is a planning question.
_FV_PLAN_EXCLUDE_RE = re.compile(
    r"\breschedul|\bchange\b|\bmodify\b|\bmove\b|\bpostpone|\bprepone|\bshift\b"
    r"|\bconflict|\bpermission\b|\bapprove[sd]?\b|\bunassigned\b|\ballocation\b"
    r"|\bdeadline\b|\brecently\b|\bcompleted\b|\bwho\s+(?:can|should|approves)\b"
    # "which date should I select for the field visit" picks a date for one
    # application -- fv_date_select's job, not a plan for the day.
    r"|\b(?:which|what)\s+date\b|\bselect\b|\bsuitable\s+date\b",
    re.IGNORECASE,
)
_FV_PLAN_CUES = (
    # "which application should I visit", "which block do I go to next"
    re.compile(
        r"\b(?:which|what|whose)\b[^?]{0,60}?"
        r"\b(?:applications?|apps?|files?|surveys?|parcels?|blocks?|wards?|streets?|areas?)\b"
        r"[^?]{0,60}?\b(?:visit|visits|inspect|inspection|go)\b",
        re.IGNORECASE),
    # "should I visit ...", "do I have to inspect ...", "where should I go"
    re.compile(r"\b(?:should|shud|shall|must|do|does|need\s+to|have\s+to|can)\s+i\b"
               r"[^?]{0,60}?\b(?:visit|inspect|go)\b", re.IGNORECASE),
    # "what is my next field visit", "next inspection"
    re.compile(r"\bnext\b[^?]{0,20}?\b(?:field\s*)?(?:visit|inspection)\b", re.IGNORECASE),
    # "plan my field visits for next week"
    re.compile(r"\bplan\b[^?]{0,40}?\b(?:visit|visits|inspection|inspections|day|week)\b",
               re.IGNORECASE),
    re.compile(r"\bwhere\s+(?:should|shud|do|must)\s+i\s+go\b", re.IGNORECASE),
    # "which applications are awaiting a field visit"
    re.compile(r"\bawait(?:ing)?\b[^?]{0,30}?\b(?:field\s*)?(?:visit|inspection)\b",
               re.IGNORECASE),
    # "what field visits do I have next week" -- the action word comes first, and
    # the day being asked about is what makes it a plan rather than a count
    # ("how many field visits do I have" is a listing and stays one).
    re.compile(r"\b(?:visits?|inspections?)\b[^?]{0,20}?\bdo\s+i\s+(?:have|got)\b"
               r"[^?]{0,25}?\b(?:tomorrow|next\s+day|next\s+week|coming\s+week|today|"
               r"this\s+week|next|coming)\b",
               re.IGNORECASE),
)
# "which block should I go to tomorrow" names no visit word at all; the location
# noun plus a going word is what makes it a visit-planning question.
_FV_PLAN_LOCATION_RE = re.compile(
    r"\b(?:blocks?|wards?|areas?|streets?)\b[^?]{0,40}?\b(?:go|visit|cover|work)\b"
    r"|\b(?:go|visit|cover|work)\b[^?]{0,40}?\b(?:blocks?|wards?|areas?|streets?)\b",
    re.IGNORECASE,
)


def parse_visit_plan_query(message: str):
    """Classify "what should I go and inspect?" questions.

    Returns None when the message is not one, else a dict with:
      focus -- 'block' / 'ward' when the officer asked where rather than which
               application, else None
    """
    if not message:
        return None
    msg = message.lower()
    if not (_FV_PLAN_ACTION_RE.search(msg) or _FV_PLAN_LOCATION_RE.search(msg)):
        return None
    if _FV_PLAN_EXCLUDE_RE.search(msg):
        return None
    if not any(cue.search(msg) for cue in _FV_PLAN_CUES):
        return None
    focus = None
    if re.search(r"\bblocks?\b", msg):
        focus = "block"
    elif re.search(r"\bwards?\b", msg):
        focus = "ward"
    return {"focus": focus}


def parse_last_application_query(message: str):
    """Classify a "my last/previous application" reference.

    Returns None when the message is not one, else a dict with:
      status      -- status the officer named ('approved', 'rejected', ...) or None
      app_type    -- 'ISD' / 'NISD' / 'MERGE' when named, else None
      field       -- the one field asked for ('area', 'applicant', ...) or None
      yes_no      -- True when the status word is a question about the most recent
                     application rather than a filter over the officer's history
    """
    if not message:
        return None
    msg = message.lower()
    if (_LAST_APP_PERIOD.search(msg) or _LAST_APP_OTHER_NOUN.search(msg)
            or _LAST_APP_COUNTED.search(msg)):
        return None
    matched = any(p.search(msg) for p in _LAST_APP_PATTERNS)
    if not matched:
        matched = (any(w in message for w in _LAST_APP_TA_RECENCY)
                   and any(w in message for w in _LAST_APP_TA_NOUN))
    if not matched:
        return None

    status = None
    status_at = None
    for name, words in _LAST_APP_STATUS_WORDS:
        for word in words:
            hit = re.search(rf"(?<!\w){re.escape(word)}(?!\w)", msg)
            if hit:
                status, status_at = name, hit.start()
                break
        if status:
            break
    if not status:
        for name, words in _LAST_APP_TA_STATUS:
            if any(w in message for w in words):
                status = name
                break

    app_type = None
    if re.search(r"(?<!\w)nisd(?!\w)", msg):
        app_type = "NISD"
    elif re.search(r"(?<!\w)isd(?!\w)", msg):
        app_type = "ISD"
    elif re.search(r"(?<!\w)merge[ds]?(?!\w)", msg):
        app_type = "MERGE"

    field = next((name for name, pattern in _LAST_APP_FIELD_RES if pattern.search(msg)), None)

    # "when was my last application approved / rejected / closed" reads as a
    # date question and so lands on submission_date, which is the wrong date --
    # the officer is asking when it was decided. Promote it, unless the
    # question also names the filing ("when was my last approved application
    # submitted"), where the filing date is what was asked for.
    if field == "submission_date" and not _LAST_APP_FILED_RE.search(msg) \
            and _LAST_APP_DECIDED_RE.search(msg):
        field = "decision_date"

    # A yes/no needs three things: the opening verb, a status word standing
    # AFTER the application it is asked about ("... application is approved"),
    # and no field in the question. Before the noun the status word is an
    # adjective choosing which application ("my last APPROVED application"), and
    # a question that names a field wants that field, not a yes or a no --
    # "what is the area of my last approved application" is neither.
    yes_no = False
    if status is not None and status_at is not None and field is None \
            and _LAST_APP_YES_NO.match(message.strip()):
        nouns = list(_LAST_APP_NOUN_RE.finditer(msg))
        yes_no = bool(nouns) and status_at > nouns[-1].start()

    return {
        "status": status,
        "app_type": app_type,
        "field": field,
        "yes_no": yes_no,
    }


# Intents that leave the conversation sitting on one application, so the next
# question is read as a follow-up about that file rather than a fresh topic.
APP_SCOPED_INTENTS = {
    "application_status", "check_documents", "check_sale_deed", "sale_deed_check",
    "is_nisd_or_isd", "joint_owner_check", "litigation_check",
    "isd_processing", "merge_info", "can_number_info", "last_application",
}


def parse_intent(message: str, prev_intent: str = None, app_scoped: bool = False) -> str:
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
    # Strip leading list-item prefixes like "1.", "2)", "a -" etc.
    #
    # A hyphen counts as a bullet only when a space follows it ("a - show my
    # files"). Without that rule this stripped the first letter off every
    # message beginning with a hyphenated word.
    message = re.sub(r'^\s*\d+[\.\)]\s*', '', message)
    message = re.sub(r'^\s*[a-zA-Z][\.\)]\s*', '', message)
    message = re.sub(r'^\s*(?:\d+|[a-zA-Z])\s*-\s+', '', message)

    msg = normalize_text(message)

    # "nisd table" / "display isd table" -- the type's applications, however the officer
    # names the view.
    _tt = re.fullmatch(r"\s*(?:display|show|give|list|get)?\s*(?:me\s+)?(?:the\s+|all\s+)?(nisd|isd|merge)\s+(?:table|tabel|list|data|records?)\s*[?.!]*\s*", msg)
    if _tt:
        return {"nisd": "nisd_applications", "isd": "isd_applications", "merge": "merge_applications"}[_tt.group(1)]

    _tc = re.search(r"\b(nisd|isd|merge)\b.*\b(?:evlo|evvalavu|ethana|ethanai|ethane)\b|\b(?:evlo|evvalavu|ethana|ethanai)\b.*\b(nisd|isd|merge)\b", msg)
    if _tc and not re.search(r"\bfee\b|\bsla\b|cost|price", msg):
        return {"nisd": "nisd_applications", "isd": "isd_applications", "merge": "merge_applications"}[(_tc.group(1) or _tc.group(2))]

    if re.search(r"\b(?:what\s+(?:are|is)\s+my\s+priorit(?:y|ies)|my\s+priorit(?:y|ies)|which\s+(?:are|is)\s+my\s+(?:top|urgent)|top\s+priorit(?:y|ies))\b", msg):
        return "highest_priority_applications"
    if re.search(r"\bhow\s+(?:am\s+i|are\s+we)\s+doing\b|\bhow\s+is\s+my\s+(?:performance|progress|work)\b|\bmy\s+performance\b", msg):
        return "completion_rate"

    # Bare reference-data questions: answered from the register / master tables,
    # never from a model's memory, and never by asking for an application number.
    if re.fullmatch(r"\s*(?:what\s+is|what's|tell\s+me)\s+the\s+(?:sla|processing\s+time|time\s+limit|turnaround)\s*[?.!]*\s*", msg):
        return "service_code_guide"

    # "show" itself has no typo tolerance anywhere in this function -- every
    # one of the ~60 intents below checks for it (or "list"/"give"/
    # "display") as a plain substring, so "shhoow applications" (2 edits --
    # an extra 'h' and an extra 'o'), "shwo applications" (a transposition)
    # and "sho applications" (a dropped letter) all matched NONE of them and
    # fell through to the slow LLM, which then narrated the two rows as
    # prose instead of returning the same deterministic table "show
    # applications" gives. Corrected once, here, before any of those checks
    # run, rather than teaching typo tolerance to each of them separately.
    # max_edits=2 (wider than these words' own 1-edit standard budget) is
    # deliberate: they are the single most-typed word in this whole
    # pipeline. But at that budget several real, plausible words land inside
    # it too -- "is my application slow" corrected to "...show", hijacking
    # an unrelated question into a listing; "what is my LAST application"
    # risked the same via "list". `_VERB_TYPO_NEVER` (measured against a
    # sweep of common English words, the same way `_NEVER_TYPO` a few
    # hundred lines down was built) is what a real word gets checked
    # against before this rewrites it -- the same "would rather miss a typo
    # than invent one" rule the rest of this module follows.
    # "many" rides the same mechanism at its OWN, narrower budget (1 edit,
    # not 2): at 2 edits "main"/"mine"/"mean" -- real words that show up in
    # ordinary sentences -- land inside it too. Even at 1 edit "man" still
    # does, hence its own exclusion; the value of catching "how mny
    # applications" outweighs that one unlikely collision in this domain.
    _VERB_TYPO_TARGETS = (("show", 2), ("list", 2), ("give", 2), ("display", 2),
                          ("many", 1))
    _VERB_TYPO_NEVER = frozenset({
        "last", "lost", "slow", "snow", "stow", "line", "live", "gate", "gave",
        "man",
    })
    msg = " ".join(
        next((v for v, budget in _VERB_TYPO_TARGETS
              if w != v and w not in _VERB_TYPO_NEVER
              and is_token_typo_match(w, v, max_edits=budget)), w)
        for w in msg.split()
    )
    words = extract_tokens(msg)

    # "merge" is 5 letters, long enough for the project's standard 1-edit
    # typo budget (`_max_edits_for`) -- unlike the 3-letter "isd", which
    # CLAUDE.md keeps typo-free on purpose so it can't absorb "nisd". A plain
    # `\bmerge?\b` regex caught a dropped trailing letter ("merg") but not a
    # transposition like "mrge", so "shw mrge applicatons" fell all the way
    # through to the slow LLM fallback instead of the deterministic listing.
    # The literal service code counts too -- symmetric with `_has_isd_w` /
    # `_has_nisd_w` a few hundred lines down, which both match `|0154` /
    # `|0153` alongside the word. Without it, "show 0155 applications" (typed
    # the same way "show 0154 applications" correctly works) fell through to
    # the generic pending-queue listing, silently discarding the "0155".
    # "erge" is a dropped LEADING letter, which the typo matcher's own
    # first-character guard refuses to cross on its own.
    _has_merge_token = any(
        is_token_typo_match(w, "merge") or is_qwerty_first_letter_typo(w, "merge")
        for w in words
    ) or bool(re.search(r'\b0155\b', msg)) or "erge" in words

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

    def exact_word(keyword: str) -> bool:
        """
        Token-boundary match with NO typo tolerance.

        Used by gates where a short keyword would otherwise be found inside a
        longer, unrelated word: "app" inside "applicant" and "am" inside "name"
        both routed "what is its applicant name?" into the list-query branch.
        Edit-distance is deliberately skipped here too, because "application" is
        within typo range of "applicant".
        """
        kw_norm = normalize_text(keyword)
        if not kw_norm or not words:
            return False
        kw_tokens = extract_tokens(kw_norm)
        if len(kw_tokens) != 1:
            return match_phrase(words, kw_norm)
        return kw_tokens[0] in words

    def has_exact(keywords: list) -> bool:
        return any(exact_word(kw) for kw in keywords)

    def _jurisdiction_fuzzy_match() -> bool:
        """Catch severely mangled spellings of 'jurisdiction' that exceed
        the standard 2-edit budget (e.g. 'julsdicton', 'jursidicton',
        'jurisidction').  SequenceMatcher ratio is more forgiving for
        distant misspellings of long, distinctive words — and 'jurisdiction'
        at 12 characters has no common English false-positive neighbour."""
        for w in words:
            if len(w) >= 8 and w[0] == 'j':
                ratio = SequenceMatcher(None, w, "jurisdiction").ratio()
                if ratio >= 0.75:
                    return True
        return False

    # ── keyword sets (English + Tamil + Transliterated Tamil Script + Tanglish) ──
    ta_survey      = ["கணக்கெண்", "கணக்கு", "நில அளவை", "நிலஅளவை", "சர்வே", "சர்வேக்கள்", "சர்வே எண்", "சர்வே எண்கள்", "சார்வே",
                      "survey", "surveys", "survay", "suvery", "surveynumber", "survey no", "kanakken", "kanakku"]
    ta_subdivision = ["உட்பிரிவு", "உட்பிரிவுகள்", "உட்பிரிவினை", "சப்டிவிஷன்", "சப் டிவிஷன்", "சப்டிவிஷன்கள்", "சப்டிவிஸன்",
                      "subdivision", "subdivisions", "sub-division", "sub-divisions", "subdiv", "utpirivu", "utpirivugal"]
    # Tamil is agglutinative: the same verb appears with different case and
    # imperative suffixes, and token matching only sees the exact form. The
    # suffixed variants below were missing, so "விண்ணப்பங்களைப் பட்டியலிடு"
    # (list the applications) matched nothing and fell through to general_query.
    ta_show        = ["காட்டு", "காண்பி", "பட்டியல்", "அனைத்தும்", "காட்டவும்", "காண்பிக்கவும்", "காட்டுங்க", "காட்டுப்பா", "ஷோ", "லிஸ்ட்",
                      "பட்டியலிடு", "பட்டியலிடவும்", "பட்டியலிட", "காண்பிக்க", "காட்ட", "தருக", "கொடு",
                      "show", "list", "all", "my", "display", "get", "view", "fetch", "kaattu", "kaatuvom"]
    ta_owner       = ["உரிமையாளர்", "சொந்தக்காரர்", "கூட்டுரிமையாளர்", "உரிமையாளர்கள்", "உரிமையாளரின்",
                      "owner", "owners", "ownership", "owns", "owned", "belongs",
                      # "pattadar" (patta-HOLDER) names the owner; bare "patta"
                      # is the patta NUMBER, a survey_detail field. Bare "patta"
                      # used to sit here too, so "what is the patta number of
                      # survey 5" -- or its Tanglish "survey 5 ku patta number
                      # enna" -- was routed to survey_owners and answered with
                      # the owner's name and share instead of the number asked.
                      "pattadar", "urimaiyalar", "urimayalar"]
    ta_pending     = ["நிலுவை", "நிலுவையில்", "நிலுவையிலுள்ள", "காத்திருக்கும்", "பெண்டிங்", "பெண்டிங்ஸ்", "பெண்டிங்கில்", "பெண்டிங்க்", "பென்டிங்",
                      "pending", "waiting", "pendig", "pendng", "uncompleted", "niluvai", "niluvaiyil"]
    ta_overdue     = ["காலதாமத", "காலதாமதமான", "தாமதம்", "தாமதமான", "காலக்கெடு கடந்த", "ஓவர்டியூ", "ஓவர் டியூ", "ஓவர்ட்யூ", "ஓவர்டியு", "ஓவர்டியூஸ்", "ஒவர்டியூ", "ஓவர்ட்யு", "ஓவர்",
                      "overdue", "late", "delayed", "overdew", "overdu", "over-due", "kaalathaamadha", "kaalathamadhamaana", "thamadham",
                      # natural synonyms officers use for "past the SLA" -- specific
                      # multi-word phrases so they never match on a stray word
                      "missed the deadline", "miss the deadline", "missed their deadline",
                      "missed the due date", "past the deadline", "past due date",
                      "past their due date", "past the due date", "past due",
                      "behind schedule", "overshot the deadline", "exceeded the deadline"]
    # Bare "field"/"visit"/"visits"/"schedule"/"scheduling" used to sit in this
    # list -- ordinary words that turn up in unrelated sentences ("best places
    # to VISIT in kerala", "SCHEDULE a call") and, via the plain catch-all
    # below, got answered as the officer's field-visit register. "field visit"
    # (and its close spellings) is kept as a phrase; scheduling-conflict
    # questions have their own dedicated regex and never needed the bare word.
    ta_field_visit = ["கள ஆய்வு", "களஆய்வு", "வருகை", "களப் பார்வை", "பீல்டு விசிட்", "பீடு விசிட்", "பீல்ட் விசிட்", "பீல்ட்", "விசிட்", "ஆய்வு",
                      "field visit", "field visits", "fieldvisit", "field-visit",
                      "site visit", "feild visit", "inspection", "kala aaivu"]
    ta_workload    = ["பணிச்சுமை", "வேலைச்சுமை", "ஒர்க்லோடு", "வொர்க்லோடு", "வர்க்லோட்", "வர்க் லோடு",
                      "workload", "worklod", "work load", "panichumai", "velaichumai"]
    ta_application = ["விண்ணப்பம்", "விண்ணப்பங்கள்", "விண்ணப்பங்களை", "விண்ணப்பங்களின்",
                      # case-suffixed forms that appear in ordinary phrasing
                      "விண்ணப்பங்களைக்", "விண்ணப்பங்களைப்", "விண்ணப்பங்களுக்கு",
                      "விண்ணப்பத்தை", "விண்ணப்பத்தின்", "விண்ணப்பத்திற்கு", "விண்ணப்பங்களும்",
                      "ஆப்ளிகேஷன்", "ஆப்ளிகேஷன்ஸ்", "ஆப்ளிகேஷன்கள்", "அப்ளிகேஷன்", "அப்ளிகேஷன்ஸ்", "அப்ளிகேஷன்கள்", "ஆப்ளிகேஷனை", "ஆப்ளிகேஷன்களை",
                      "application", "applications", "aplications", "aplication", "app", "appl", "apps", "vinnappam", "vinnappangal"]
    ta_merge       = ["இணைப்பு", "இணைக்க", "இணைக்கப்பட்ட", "இணைப்பு விண்ணப்பம்", "இணைப்பு விண்ணப்பங்கள்", "இணைத்தல்", "மெர்ஜ்", "மெர்ஜிங்",
                      "merge", "merging", "merged", "merg"]
    ta_status      = ["நிலை", "தற்போதைய நிலை", "ஸ்டேட்டஸ்", "ஸ்டேடஸ்", "ஸ்டேட்ஸ்",
                      "status", "statuss", "staus", "stage", "nilai"]
    ta_area        = ["பரப்பளவு", "பரப்பு", "area", "arrea"]
    ta_ward        = ["வார்டு", "ward", "wards", "wrd", "wad"]
    ta_block       = ["தொகுதி", "block", "blocks"]
    ta_next        = ["அடுத்த", "கிடைக்கும்", "next", "available", "nxt"]
    ta_detail      = ["விவரம்", "விவரங்கள்", "விவரங்களை", "detail", "details", "info",
                      "contain", "included", "which", "what", "how", "land"]

    # ── Greeting queries ──
    _greetings_exact = [
        "hi", "hello", "hey", "good morning", "good afternoon", "good evening",
        "vanakkam", "வணக்கம்", "காலை வணக்கம்", "மாலை வணக்கம்", "ஹாய்",
        "நன்றி", "thanks", "thank you", "how are you", "எப்படி இருக்கீங்க",
        "bye", "goodbye", "nandri", "eppadi irukeenga", "epo irukeenga",
        "nalla iruku", "நன்றி நண்பரே",
    ]
    clean_msg = msg.strip().rstrip("!.,")
    # A politeness/address wrapper ("சார் வணக்கம்", "sir hi") is still the same
    # greeting; the exact-match list above only ever saw the bare word. Only
    # leading/trailing tokens are stripped so a real multi-word phrase like
    # "good morning" is untouched in the middle.
    _greeting_filler = {"சார்", "சார", "sir", "bro", "anna", "please", "pls",
                         "plz", "kindly", "தயவு", "செய்து", "தயவுசெய்து",
                         "கொஞ்சம்",
                         # Tanglish casual address particles -- the same role
                         # "bro" already covers, just in Tamil. Without these,
                         "da", "pa", "di", "dee", "machi", "macha", "அண்ணா",
                         # "thanks da" left a bare "da" that was in neither
                         # the thanks/farewell/small-talk sets nor here,
                         # failed the "every word must be a greeting word"
                         # test, and fell to the LLM -- which then answered
                         # with the officer's own jurisdiction summary, a
                         # question nobody asked.
                         "டா", "பா", "டி"}
    _core_words = [w for w in clean_msg.split()
                   if w.lower().strip(",.!") not in _greeting_filler]
    _core_msg = " ".join(_core_words)
    # The exact list above only ever matched the bare word, so "hi there",
    # "hello there", "thanks a lot", "bye bye", "see you", "good night" and
    # "romba nandri" -- 25 of 46 ordinary openings and sign-offs -- fell to
    # general_query and were answered by llama3.1:8b, at 20-45 SECONDS each,
    # for a message with no question in it. Matching is token-based instead:
    # after the politeness filler is dropped, a message EVERY one of whose
    # words is a greeting, farewell or small-talk word is a greeting. A single
    # domain word ("hi, what is the status of 2026/...") leaves a token that is
    # in none of these sets, so the rule does not fire.
    _greet_words = {
        "hi", "hii", "hiii", "hiya", "helo", "hello", "hellow", "hey", "heyy",
        "good", "morning", "mornin", "gud", "afternoon", "evening", "night",
        "greetings", "namaste", "namaskaram", "vanakkam", "vanakam",
        "ஹாய்", "ஹலோ", "வணக்கம்", "காலை", "மாலை", "இரவு", "நமஸ்காரம்",
    }
    _bye_words = {
        "bye", "byee", "goodbye", "gudbye", "see", "later", "farewell",
        "thanks", "thanx", "thankyou", "thank", "nandri", "nanri", "poitu",
        "varen", "poyitu", "varren", "regards", "cheers",
        "நன்றி", "மிக்க", "போய்", "வர்றேன்", "வருகிறேன்", "சென்று",
    }
    _smalltalk_words = {
        "how", "are", "you", "u", "r", "doing", "is", "it", "going", "all",
        "well", "fine", "ok", "okay", "very", "much", "so", "lot", "lots",
        "a", "the", "there", "again", "dear", "my", "friend", "romba",
        "eppadi", "irukeenga", "irukinga", "epdi", "iruken", "nalla", "iruku",
        "எப்படி", "இருக்கீங்க", "இருக்கிறீர்கள்", "நண்பரே", "ரொம்ப", "சரி",
    }
    _greet_vocab = _greet_words | _bye_words | _smalltalk_words | _greeting_filler
    _greet_tokens = [t for t in re.findall(r"[0-9a-z\u0b80-\u0bff']+", clean_msg.lower())]
    _is_greet_phrase = bool(_greet_tokens) and len(_greet_tokens) <= 6 and all(
        t in _greet_vocab for t in _greet_tokens) and any(
        t in _greet_words or t in _bye_words for t in _greet_tokens)
    # A greeting PREFIX only makes the message a greeting when nothing but greeting
    # words follows it: "வணக்கம் எனது நிலுவை விண்ணப்பங்கள்" is the request, with
    # a greeting in front.
    def _startswith_greeting_only() -> bool:
        for _g in ("வணக்கம்", "காலை வணக்கம்", "மாலை வணக்கம்", "good morning", "good evening", "good afternoon"):
            if clean_msg.startswith(_g):
                _rest = re.findall(r"[0-9a-z஀-௿']+", clean_msg[len(_g):].lower())
                if all(t in _greet_vocab for t in _rest):
                    return True
        return False

    if (clean_msg in _greetings_exact or _core_msg in _greetings_exact or _is_greet_phrase or
        _startswith_greeting_only()) and len(words) <= 6:
        if not any(w in msg for w in ["app-", "application", "survey", "145", "146", "147", "148", "status", "stage", "விண்ணப்பம்", "கணக்கெண்"]):
            return "greeting"

    # ── Comparison questions ──
    # "which is older, A or B", "ISD vs NISD", "which took the longest to
    # approve". These have to win before the listing and per-application rules
    # below, which each answered half the question: "compare ISD and NISD"
    # returned the single pending ISD file, and two application numbers in one
    # message produced a two-row status dump that compared nothing.
    # "what is the difference between ISD and NISD" asks what the two words
    # MEAN. It was claimed by the comparison parser and answered "ISD 6,
    # NISD 64 (of 70 in your jurisdiction)" -- a count, to a definition
    # question, for an officer who may well have been asking which one to file.
    # `service_code_guide` prints both codes with their workflow, fee and SLA,
    # which is the answer. Narrow: the word "difference" (or its Tamil /
    # Tanglish equivalents), exactly the two type words, no counting word, and
    # no application number -- so "difference between <appA> and <appB>" and
    # "compare ISD and NISD" are both untouched.
    # "how do I file an ISD application" asks for the procedure, which the
    # documents hold; "isd application" alone read as the officer's ISD listing.
    if (re.search(r"^\s*(?:how|where)\s+(?:do|does|can|should|to)\s+(?:i|we|one|you|a\s+citizen)?\s*"
                  r"(?:file|apply|submit|raise|register|create|make|lodge)\b", msg)
            and not re.search(r"\d{4}/\d{3,4}/", msg)
            and not re.search(r"\b(?:show|list|my|count|how\s+many)\b", msg)):
        return "general_query"
    _defn_types = [w for w in ("nisd", "isd", "merge") if re.search(
        rf"(?<![a-z0-9]){w}(?![a-z0-9])", msg)]
    if (len(_defn_types) >= 2
            and re.search(r"\bdifference\b|\bdiffer\b|\bvs\.?\s+what\b"
                          r"|vithiyasam|vidhiyasam|வித்தியாச|வேறுபா", msg)
            and not re.search(r"\b(how many|count|total|my|show|list|which of|"
                              r"more|fewer|longer|faster|slower)\b", msg)
            and not re.search(r"\b(fee|fees|charge|charges|cost|price)\b|கட்டண", msg)
            and not re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", msg)
            and not re.search(r"விண்ணப்ப|எனது|எனக்கு|எத்தனை", msg)):
        return "service_code_guide"

    # "does ISD need a field visit?" / "ISD ku field visit venuma?" asks whether
    # the service REQUIRES a visit -- a fact about the service code, answered by
    # the lookup. It used to be read as the officer's own field-visit list. One
    # type word or code, a need/require cue, and none of the words that make it
    # a question about the officer's own files or schedule.
    _fv_types = [w for w in ("nisd", "isd", "merge") if re.search(
        rf"(?<![a-z0-9]){w}(?![a-z0-9])", msg)]
    _fv_codes = re.findall(r"(?<!\d)015[345](?!\d)", msg)
    if (len(_fv_types) + len(set(_fv_codes)) == 1
            and re.search(r"field\s*(visit|inspection)|கள\s*ஆய்வு|களஆய்வு", msg)
            and re.search(r"\b(need|needs|require|required|requires|mandatory|"
                          r"compulsory|necessary|must|venuma|venduma|venum|"
                          r"thevaya|thevaiya|thevai)\b|தேவையா|தேவை", msg)
            and not re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", msg)
            and not re.search(r"\b(my|show|list|how many|count|pending|scheduled|"
                              r"schedule|overdue|when|which|applications?|apps?|"
                              r"today|tomorrow|week)\b|விண்ணப்ப|எனது|எனக்கு", msg)):
        return "service_code_lookup"

    if parse_comparison_query(message):
        return "compare_applications"

    # "what is ISD?" / "what is NISD?" — the plainest domain question there is,
    # and it fell all the way to general_query, where llama3.1:8b answered
    # *"NISD stands for Not Involving Sub-Division ... The application number
    # format is NISD/DISTRICT_CODE/YEAR"* — a format this register has never
    # used. The three type words ARE service codes (0153 / 0154 / 0155) and
    # `SIS_URBAN_SERVICES` holds their official text, so this is a lookup for
    # the same reason "what is 0153?" is one.
    #
    # Narrow on purpose: a definition verb, exactly ONE type word, and none of
    # the listing / counting words that make it a question about the officer's
    # own files ("show my ISD applications", "how many ISD"). Two type words
    # are a comparison and were claimed above.
    _defn_verb = bool(re.search(
        r"(^|\b)(what\s+(is|are|does)|what's|whats|define|definition\s+of|"
        r"meaning\s+of|full\s+form\s+of|expand|stands?\s+for|explain)\b", msg)
        or re.search(r"(என்றால்\s*என்ன|endral\s*enna|nu\s*sonna\s*enna|"
                     r"என்பது\s*என்ன|vidhiyasam|vithiyasam|"
                     r"\b(na|naa|nu|nnu|nna)\s+(enna|ennanu)\b|"
                     r"(ன்னா|னா)\s*என்ன|என்னன்னு)", msg))
    _type_words = [w for w in ("nisd", "isd", "merge") if re.search(
        rf"(?<![a-z0-9]){w}(?![a-z0-9])", msg)]
    # "nisd" contains no "isd" under the boundary check above, so the two are
    # counted separately and "what is NISD" names exactly one.
    if (_defn_verb and len(_type_words) == 1
            and not re.search(r'\d{4}/\d{3,4}/\d{1,3}/\d+', msg)
            and not re.search(r"\b(show|list|my|how many|count|pending|approved|"
                              r"rejected|overdue|apps?|total|which\s+of)\b", msg)
            # "ISD applications" usually means the officer's own files; it is a
            # definition only in the singular article form "what is AN ISD
            # application".
            and (not re.search(r"applic", msg)
                 or re.search(r"\b(an?|the)\s+(isd|nisd|merge)\s+application\b", msg))
            # A fee / service-charge question belongs to the fee rule below,
            # which answers from the schedule table rather than the code's
            # description.
            and not re.search(r"\b(fee|fees|charge|charges|cost|costs|price|"
                              r"payment|rupees)\b|₹|கட்டண", msg)
            and not re.search(r"விண்ணப்ப|எனது|எனக்கு", msg)):
        return "service_code_lookup"


    # ── Bare date-scope follow-up ("last month", "what about June?") ──
    # The message names a period and nothing else, so it re-scopes the previous
    # question rather than asking a new one. Without this it fell through to
    # general_query and the LLM answered "I don't have information about last
    # month" while the numbers sat in the database.
    if prev_intent in DATE_SCOPED_INTENTS and is_bare_date_scope(message):
        return prev_intent

    # The message names a ward or block and nothing else ("block 0015", "in
    # ward 2"). Like a bare date scope it re-scopes the previous question, and
    # on its own it means "show me that block's applications" -- it used to
    # fall through to application_status, which answered by asking which
    # application number was meant.
    if _BARE_GEO_SCOPE_RE.fullmatch((message or "").strip()):
        return prev_intent if prev_intent in DATE_SCOPED_INTENTS else "pending_applications"

    # ── "show the details" about the application just discussed ────────────
    # A bare details request names no subject of its own, so it fell through to
    # general_query and the LLM retyped the record as a prose bullet list. It is
    # the same request as "show details for <number>": route it to
    # application_status, where the gate resolves which application is meant and
    # the detail card is rendered from the database.
    if _BARE_DETAILS_RE.fullmatch(msg.strip()) and prev_intent not in (
            "survey_detail", "survey_owners", "ward_surveys", "block_surveys"):
        return "application_status"

    # ── Document / file upload queries — catch before DB intent routing ──
    # Phrases that mean "I uploaded a file, help me with it" should never
    # be routed to survey/application DB queries.
    _doc_phrases = [
        "uploaded", "word document", "pdf document", "question bank",
        "answer all", "answer for all", "from the document", "in the document",
        "the file", "attached file", "from this file",
    ]
    # "the file" is the uploaded-document sense; "the files" is how an officer
    # says applications, so the phrase needs a token boundary, not a substring.
    if any(re.search(r"\b" + re.escape(ph) + r"\b(?!s)", msg) for ph in _doc_phrases):
        return "general_query"

    # A question that explicitly names a reference document (a circular, memo,
    # manual, policy, checklist, SOP...) is asking what that document says, not
    # for a DB record -- even when it also mentions a survey/ward/"application".
    # Without this, "which boundary stones on survey 42 ..." went to survey_detail
    # and "does circular 2026/07 apply to ward 102" went to field_visits, and the
    # ingested file was never consulted. Skip when a concrete application number
    # is present (that IS a DB lookup).
    if not re.search(r'\d{4}/\d{3,4}/\d{1,3}/\d+', msg) and re.search(
        r'\b(?:circular|memo|memorandum|guideline|guidelines|policy|policies|'
        r'sop|standard\s+operating\s+procedure|handbook|the\s+manual|'
        r'the\s+checklist|the\s+note|the\s+report\s+says|as\s+per\s+the|'
        r'according\s+to\s+the|what\s+does\s+the\s+\w+\s+say)\b'
        r'|\bboundary\s+stones?\b|\bstanding[-\s]water\b|\bmonsoon[-\s]?defer\w*'
        # Tamil: சுற்றறிக்கை (circular), நெறிமுறை/வழிகாட்டி (guideline),
        # கையேடு (handbook), எல்லைக் கல்/கற்கள் (boundary stone), பருவமழை (monsoon)
        r'|சுற்றறிக்கை|நெறிமுறை|வழிகாட்டி|கையேடு|எல்லைக்\s*க(?:ல்|ற்கள்)|பருவமழை',
        msg, re.IGNORECASE,
    ):
        return "general_query"

    # ── Required-document questions are procedural, not application lists ───
    # "what documents are required for an ISD application?" asks what the rules
    # demand (documents/workflow_guide.txt), not for a list of ISD applications
    # -- but the word "application" alone used to route it to isd_applications.
    # Only divert when no specific application is referenced and the question is
    # not about what is missing/uploaded on one.
    _doc_word = any(w in msg for w in [
        "document", "documents", "enclosure", "enclosures",
        "ஆவணம்", "ஆவணங்கள்"])
    # "submit"/"submitted" removed: "show the documents submitted for
    # 2026/..." is an application-specific check_documents question, not a
    # "what documents are required" procedural one -- but this block runs on
    # the app-number-stripped text too (via _classify_application_subtopic),
    # where the number is gone and "the application" wording never appears,
    # so "submit"/"submitted" alone was enough to misfire general_query.
    _needs_word = any(w in msg for w in [
        "required", "require", "requires", "need", "needed", "necessary",
        "mandatory", "must", "checklist", "attach",
        "தேவையான", "தேவை", "வேண்டும்"])
    _about_one_app = bool(re.search(r'\d{4}/\d{3,4}/\d{1,3}/\d+', msg)) or any(
        w in msg for w in [
            "missing", "uploaded", "upload", "verified", "pending document",
            "this application", "that application", "the application",
            "my application", "இல்லாத", "காணாத"])
    if _doc_word and _needs_word and not _about_one_app:
        return "general_query"

    # ── Specific questions the generic rules downstream would swallow ───────
    # Each of these has a dedicated intent further down that was unreachable:
    # a broader rule ("pending", "show ... survey", "<field> + interrogative")
    # matched first and answered a different question than the one asked.
    # ── Directory questions: who holds which ward, how many wards/blocks ────
    # Structure and postings, not another ward's caseload. A ward officer may
    # ask these about the whole town -- the answer carries no application,
    # applicant, survey or owner data.
    if _WARD_DIRECTORY_RE.search(msg):
        # "how many wards do I have" / "list my wards" ask for the officer's own posting.
        if re.search(r"\bmy\b|\bdo\s+i\b|\bhave\s+i\b|\bi\s+(?:have|hold|cover|handle)\b", msg):
            return "jurisdiction_summary"
        return "officer_directory"

    # ── "which block has the most applications?" ────────────────────────────
    # A question about the shape of the queue, not a request for the queue: the
    # generic list rules answered it with every application and left the
    # officer to count the rows themselves.
    # "plan my field visits for next week by block" ends in "by block" but asks
    # for a route, not for the shape of the queue.
    # An explicit application number rules this out entirely, the same way it
    # rules out the ward/month branches of the comparison parser below: "which
    # ward is <app>?" (or a follow-up resolved into "<question> <app>") asks
    # about ONE file, never the jurisdiction-wide breakdown -- even though the
    # bare question word ("which ward") is identical either way.
    if (_BLOCK_BREAKDOWN_RE.search(msg)
            and not re.search(r'\d{4}/\d{3,4}/\d{1,3}/\d+', msg)
            and not parse_visit_plan_query(message)):
        return "applications_by_block"

    if "pending" in msg and "longest" in msg:
        return "pending_longest"
    if "workload" in msg and "type" in msg:
        return "workload_by_type"

    _app_ref_early = bool(re.search(r'\d{4}/\d{3,4}/\d{1,3}/\d+', msg))
    if (_app_ref_early and re.search(r'\breject(?:ed|ion|s)?\b', msg)
            and re.search(r'\bwhy\b|\breason\b', msg)
            # "rejection reason CODE" asks for the numeric code, which the
            # register does not store -- let application_status' untracked-field
            # handler answer that. "why was X rejected" / "reason for rejection"
            # still land here.
            and not re.search(r'\b(?:reason|rejection)\s+code\b|\breason_code\b'
                              r'|\bcode\s+for\s+the\s+rejection\b', msg)):
        return "rejection_info"

    # ── Fee / service-charge / money questions ─────────────────────────────
    # "what is the service charge for an ISD application" is a question about
    # the fee schedule, but "isd"/"nisd" alone used to route it to the type
    # listing, and a bare "what is the CSC service charge" fell through to the
    # per-application field lookup and demanded an application number.
    # A question that names an application ("fee for 2025/0154/28/000286") is
    # left alone -- it is a field lookup and application_status answers it.
    _fee_word_strict = any(w in msg for w in [
        # "charge" only in its money sense -- a bare "charge" is also "who is
        # in charge of ward 102", which is not a fee question. The multi-word
        # phrases here are specific enough to SIS to fire with no extra
        # domain word; the bare words below need a question shape too (see
        # `_fee_bare_word` just under this).
        "service charge", "service charges", "csc charge",
        "csc charges", "govt charge", "government charge", "processing charge",
        "challan",
        # Tamil / Tanglish
        "கட்டணம்", "கட்டண", "கட்டணங்கள்", "சேவை கட்டணம்",
        "kattanam", "kattanangal",
    ]) or "₹" in message
    # A bare "fee"/"fees" is common enough in ordinary work sentences ("the
    # officer submitted the fee receipt yesterday") that treating it as
    # unambiguous SIS vocabulary dumped the whole fee-schedule table for a
    # sentence that asked no question at all. It still needs no OTHER domain
    # word (that is what makes it "bare"), but it does need to actually read
    # as a question or a request -- the same short/question-mark/interrogative
    # shape test used elsewhere in this file for exactly this failure mode.
    _fee_bare_words = ("fee" in msg or "fees" in msg)
    if _fee_bare_words:
        # A greeting / address in front ("hello what is the fee for ISD") is not
        # part of the question's shape.
        _fee_lead = {"hi", "hii", "hello", "hey", "vanakkam", "sir", "anna", "bro", "please",
                     "pls", "good", "morning", "afternoon", "evening", "thanks", "thank", "you"}
        _fee_msg_words = msg.split()
        while _fee_msg_words and _fee_msg_words[0].strip(".,!") in _fee_lead:
            _fee_msg_words = _fee_msg_words[1:]
        _fee_word_strict = _fee_word_strict or (
            len(_fee_msg_words) <= 6 or "?" in message
            or (_fee_msg_words and _fee_msg_words[0].strip(".,!") in (
                "what", "how", "is", "was", "are", "does", "do", "did",
                "show", "list", "give", "tell", "can", "could")))
    # Generic money words ("cost", "price", "payment", "money", "rupees",
    # "revenue") are also ordinary trivia ("flight ticket price to delhi",
    # "how do I lose weight" has none of these, but "what is the cost of a
    # flight" does) -- they need a domain word alongside them before this is
    # read as a question about the SIS fee schedule.
    _fee_money_words = [
        "cost", "costs", "price", "prices", "payment", "rupees", "money", "revenue",
        "charge", "charges", "tariff",
        "ரூபாய்", "பணம்", "panam", "rupaai", "விலை", "vilai", "vila",
    ]
    _fee_msg_tokens = re.findall(r"[a-z0-9\u0b80-\u0bff]+", msg)
    # "what is the price" / "isd cost" -- short money questions in this assistant
    # are about the fee schedule; left to the LLM they were answered from memory
    # ("The fee for service code 0154 is Rs. 400.00").
    _fee_short_money = (len(_fee_msg_tokens) <= 6 and any(w in _fee_msg_tokens for w in _fee_money_words)
                        and not any(w in msg for w in ("flight", "ticket", "gold", "petrol", "diesel", "share",
                                                       "stock", "movie", "bitcoin", "house", "land price",
                                                       "rent", "salary", "phone", "laptop", "car ")))
    _fee_word_generic = (any(w in msg for w in _fee_money_words) and any(w in msg for w in [
        "application", "applications", "csc",
        "service", "govt", "government", "registration", "mutation",
        "survey", "patta", "sub registrar", "sub-registrar", "tahsildar",
        "draughtsman", "ward", "block", "officer", "jurisdiction",
        "isd", "nisd", "merge", "0153", "0154", "0155", "subdivision", "sub-division",
        "விண்ணப்ப", "சேவை", "வார்டு",
    ])) or _fee_short_money or (
        bool(re.search(r"\b(?:isd|nisd|merge|015[345])\b", msg))
        and bool(re.search(r"\b(?:rate|rates|amount|how\s+much|pay|paid|to\s+pay)\b", msg))
        and not re.search(r"\bhow\s+many\b", msg))
    _fee_word = _fee_word_strict or _fee_word_generic
    if _fee_word and not _app_ref_early and not parse_last_application_query(message) and not any(
        p_ in msg for p_ in ["this application", "that application", "the application",
                             "this app", "that app", "same application",
                             "இந்த விண்ணப்ப", "அந்த விண்ணப்ப"]
    ):
        # "how much fee have I collected", "total fee collected this month",
        # "payment mode breakdown" -- an aggregate over the officer's own files.
        _fee_agg = any(w in msg for w in [
            "total", "sum", "collect", "collected", "collection", "collections",
            "received", "revenue",
            "breakdown", "break up", "how many paid", "statistics", "stats",
            "my applications", "my files", "so far",
            "மொத்த", "மொத்தம்", "வசூல்", "வசூலித்த",
            "motham", "mottham", "vasool",
        ])
        if _fee_agg:
            return "fee_summary"
        return "fee_lookup"

    # "now show details of both the applications" points at the list the last
    # answer produced. Without this it matched the listing keywords ("show",
    # "applications") and simply re-ran the listing -- the same table again,
    # never the details that were asked for. The numbers themselves are
    # resolved from the previous answer in chatbot.py.
    if wants_details_of_listed(msg) and not app_scoped:
        return "application_status"

    # "sort applications by date ascending", "arrange applications in descending
    # order" -- a listing with an order attached. The sort words were pulling
    # these into application_status ("by date") and general_query ("arrange"),
    # so the officer got a single-application prompt or an essay instead of the
    # ordered list. Only explicit ordering words count here: "latest"/"recent"
    # alone belong to whichever intent already owns them.
    _explicit_sort = any(w in msg for w in [
        "ascending", "descending", " asc", " desc", "sort", "sorted",
        "order by", "ordered by", "in order of", "arrange", "arranged",
        "oldest first", "newest first", "earliest first", "latest first",
        "ஏறுவரிசை", "இறங்குவரிசை", "வரிசைப்படுத்து",
    ])
    # A "by <field>" phrasing counts as an ordering request too ("list
    # applications by priority"), which is why the field regex is consulted
    # alongside the explicit order words.
    if ((_explicit_sort or _SORT_BY_FIELD_RE.search(msg))
            and extract_sort_order(msg) and has(ta_application)
            and not has(ta_survey) and not has(ta_field_visit)
            and not app_scoped
            and not re.search(r'\d{4}/\d{3,4}/\d{1,3}/\d+', msg)):
        return "pending_applications"

    # Plural "surveys" is a listing, not a lookup of one survey. Without this,
    # "surveys in ward 002" was read as survey number 002, and "show all
    # surveys" fell into survey_detail and answered "No records found".
    #
    # But a bare plural is also how a CONCEPTUAL question about surveys in
    # general gets phrased -- "under what conditions can two surveys be
    # merged" carries the word "surveys" and nothing else this rule checked
    # for, so it dumped the officer's whole 61-row survey listing for a
    # question that named no ward, no block and asked no listing verb at
    # all. Excluded when the message reads as asking about a RULE rather
    # than requesting a LIST -- the same distinction `service_code_guide`'s
    # own gates draw elsewhere in this file.
    if (re.search(r'\bsurveys\b', msg)
            and not re.search(r'\bsurvey\s*(?:no\.?|number)?\s*\d', msg)
            and not re.search(
                r'\bcondition|\brule|\bcriteria|\beligib|\bcan\s+\w+\s+surveys?\b'
                r'|\bhow\s+(?:do|does|can)\b|\bwhy\b',
                msg)):
        if re.search(r'\bward\b', msg):
            return "ward_surveys"
        if re.search(r'\bblock\b', msg):
            return "block_surveys"
        if not re.search(r'\bowners?\b|\bowns\b', msg):
            return "all_surveys_in_jurisdiction"

    # Same shape for the written rules themselves. "What is the 15 working day
    # rule?" is a question about policy (workflow_guide.txt), but it used to be
    # routed to fv_deadline_check, which can only answer for one application and
    # replied "please specify an application number". Only divert when no
    # application is referenced -- "what is the deadline for 2026/..." still
    # goes to the per-application check.
    _policy_word = any(w in msg for w in [
        "rule", "rules", "policy", "procedure", "process", "guideline",
        "guidelines", "sla", "விதி", "நடைமுறை"])
    _explainer = any(w in msg for w in [
        "what is", "what are", "whats", "explain", "describe", "tell me about",
        "how does", "how do", "how long", "what happens",
        "என்ன", "விளக்கு"])
    if _policy_word and _explainer and not _about_one_app:
        return "general_query"

    # "What is a field visit?" asks what the thing IS. It used to reach the
    # field-visit listing block below and come back with the officer's schedule
    # -- an answer to a question nobody asked. A definition question names a
    # domain concept, asks in explainer form, and refers to no data of the
    # officer's own; those three together send it to RAG, which is where every
    # other definition ("what is NISD?", "what is TSLR?") is already answered.
    _concept_terms = [
        "field visit", "field visits", "fieldvisit", "field-visit",
        "field inspection", "inspection", "site visit",
        "sub-division", "subdivision", "sub division", "merge", "mutation",
        "patta", "chitta", "natham", "tslr", "encroachment",
        "litigation", "escalation", "escalated", "sla", "adangal",
        "sale deed", "encumbrance certificate", "patta transfer",
        "digital signature", "dsc", "can number", "aadhaar",
        "sis", "isd", "nisd", "tahsildar", "draughtsman",
        "கள ஆய்வு", "களஆய்வு", "உட்பிரிவு", "பட்டா", "நத்தம்", "ஆக்கிரமிப்பு",
    ]
    # Explicit definition phrasing. "what is" alone is not enough -- "what is my
    # taluk" and "what is the status of ..." share it.
    _defines = any(p in msg for p in [
        "what is a ", "what is an ", "what is the meaning", "what does",
        "what do you mean", "define", "definition", "meaning of",
        "explain", "describe", "tell me about", "what are the",
        "என்றால் என்ன", "என்பது என்ன", "விளக்கு", "விளக்கம்",
    ]) or (
        bool(re.match(r'^\s*(?:what|whats|what\'s)\s+(?:is|are)\b', msg))
        # ... but "what is the field visit date" asked straight after looking at
        # an application is a follow-up about that file, not a request for a
        # definition. Treated as a definition it reached the LLM with no record
        # attached, which answered with an invented application.
        and prev_intent not in APP_SCOPED_INTENTS
    )
    # Anything that makes it a question about the officer's own records, or a
    # listing, is not a definition question.
    _own_data = bool(re.search(r'\bmy\b|\bmine\b|\bme\b|\bi\b', msg)) or any(
        w in msg for w in [
            "show", "list", "how many", "display", "pending", "overdue",
            "scheduled", "upcoming", "today", "tomorrow", "yesterday",
            "this week", "next week", "this month", "assigned",
            "காட்டு", "பட்டியல்", "நிலுவை", "இன்று", "நாளை",
        ])
    # "what are ISD applications", "what are my field visits", "what are the
    # NISD applications" ask for ROWS, not for a definition -- the plural
    # record noun is the tell, and a definition of the same thing is asked in
    # the singular with an article ("what is an ISD application", "what is a
    # field visit"). Without this, `_defines` claimed every "what are ..."
    # phrasing and sent it to the LLM: "what are isd applications" answered
    # with prose while "show isd applications" listed them -- one request,
    # two answers, depending only on the verb. Same failure as the SRO one in
    # `_igrs_can_rule_topic`.
    _asks_for_rows = bool(
        re.match(r"^\s*(?:what|whats|what's)\s+are\b", msg)
        and re.search(r"\b(?:applications|apps|files|cases|visits)\b"
                      r"|applic|விண்ணப்பங்கள்", msg)
        and not re.search(r"\b(?:a|an)\s", msg))
    # app_scoped: the caller stripped an application number before re-asking,
    # so this is a question about that one file ("what is the field visit
    # deadline for 2026/...") and not a request for a definition.
    # Any digit at all also disqualifies it -- "what is the next sub-division
    # number for survey 1355", "what is service code 0154" name a record.
    #
    # A name that resolves to one of the 30 official service codes disqualifies
    # it too, the same way a digit does: "what is natham settlement" / "what
    # is TSLR extract with sketch" are exactly "what is 0188" / "what is 0156"
    # spelled with the code's own name instead of its number, and every urban
    # code has an official text in SIS_URBAN_SERVICES to answer from -- the
    # same reasoning the (narrower, isd/nisd/merge-only) definition check above
    # already applies. Without this, "natham" and "tslr" being in
    # `_concept_terms` sent the question straight to the LLM before the
    # service-code lookup a few hundred lines down ever got a chance to see it.
    from backend.utils.helpers import find_service_codes as _early_svc_codes
    if (_defines and not _own_data and not _asks_for_rows
            and not _about_one_app and not app_scoped
            and any(t in msg for t in _concept_terms)
            and not re.search(r'\d', msg)
            and not _early_svc_codes(msg)):
        return "general_query"

    # workflow_guide.txt step 3: only the Tahsildar may approve a change to a
    # field visit date. Any phrasing that proposes moving a visit has to reach
    # that answer -- "can I postpone the field visit?" contains no word "date",
    # so it used to fall through and return a list of field visits instead of
    # telling the officer whose approval is needed.
    # "resheduled" (a dropped 'c') matched none of these substrings, so a
    # message asking about a field visit that "keeps getting resheduled"
    # fell through this whole block and was caught by the later, much
    # broader "escalat" substring check instead (it also said "escalate"),
    # returning a table of applications with mostly blank fields for a
    # question that wanted "ask the Tahsildar". Typo-tolerant on the longer,
    # more typo-prone words; the short common ones (move/shift/change/...)
    # stay exact, the same balance the rest of this module strikes.
    _wants_change = any(w in msg for w in [
        "move", "shift", "defer", "delay", "delayed", "delaying",
        "advance", "change", "changing", "changed", "modify", "alter",
        "மாற்ற", "மாற்றம்", "தள்ளிவைக்க", "maatha", "date change panna"]) or any(
        is_token_typo_match(tok, w) for tok in extract_tokens(msg)
        for w in ("postpone", "postponed", "postponing", "prepone", "preponed",
                  "reschedule", "rescheduled", "rescheduling", "shifting", "shifted")) or any(
        tok in ("shfit", "shfti", "sihft", "shiift", "shft") for tok in extract_tokens(msg))
    _about_visit = any(w in msg for w in [
        "field visit", "fieldvisit", "field-visit", "inspection", "visit",
        "கள ஆய்வு", "களஆய்வு", "ஆய்வு", "வருகை"])
    # "which visits were rescheduled recently" is a listing, not a request to move one
    _is_listing = any(w in msg for w in [
        "which", "list", "show", "how many", "were", "recently", "display",
        "எவை", "பட்டியல்", "காட்டு"])
    if _wants_change and _about_visit and not _is_listing:
        return "fv_change_date"

    # A scheduling conflict question does not always repeat "field visit", and
    # the field-visit block is only entered when it does -- so "are there
    # scheduling conflicts?" never reached its own handler.
    if "conflict" in msg and any(w in msg for w in ["schedul", "visit", "inspection", "calendar"]):
        return "fv_scheduling_conflicts"

    # "was the field visit for 2026/... rescheduled recently?" and "is
    # 2026/... unassigned and awaiting field visit scheduling?" name a specific
    # application, so has_field_visit_keywords below (gated on not
    # _has_app_pattern) never runs and these fell through to the generic
    # application_status default instead of answering the field-visit question
    # that was actually asked. Same fix as fv_change_date/fv_scheduling_conflicts
    # above: match app-agnostically, before the gate.
    if _about_visit and any(w in msg for w in [
        "recently rescheduled", "rescheduled recently", "rescheduled in the last",
        "rescheduled during",
        "சமீபத்தில் மாற்றப்பட்ட"
    ]):
        return "fv_recently_rescheduled"
    # "unscheduled" is the word officers actually use for this, and it was in
    # none of the cue lists -- every one of them spells the idea out ("no
    # schedule", "awaiting scheduling", "unassigned"). So "show unscheduled
    # applications" fell through to pending_applications and answered with the
    # officer's WHOLE open queue, and "show me unscheduled field visits" reached
    # the generic field_visits summary and answered with their whole visit
    # record, most of it completed -- the "answer is a superset of the question"
    # failure CLAUDE.md already records twice.
    #
    # Unlike the vaguer cues below it, the word names the thing precisely enough
    # to stand WITHOUT a field-visit word: an application is "unscheduled"
    # only in the sense of having no field visit booked. "reschedule" is
    # excluded because it shares the "schedul" stem while asking the opposite.
    if (re.search(r'\bunscheduled\b|\bun-scheduled\b|\bnot\s+scheduled\b'
                  r'|\byet\s+to\s+be\s+scheduled\b|\bnever\s+scheduled\b', msg)
            and not re.search(r'\breschedul', msg)):
        return "fv_unassigned_awaiting"
    # An open-visit request, phrased about VISITING rather than about the
    # visit record. Two gaps this closes, both found by comparing answers
    # against the register rather than by reading the routing:
    #   "show pending visits"    -> the word "pending" pulled it to the
    #                               applications queue, so an officer asking
    #                               which visits were outstanding was answered
    #                               with applications.
    #   "which are yet to visit" -> names no subject at all, matched no rule,
    #                               and fell to the LLM.
    # "yet to visit" can only be about visiting, so it needs no other subject;
    # the vaguer words (pending / outstanding / remaining) must name visits.
    # `_asked_open_visits` in chatbot.py then narrows the list to the ones
    # still to be made.
    #
    # It stands aside for the narrower field-visit intents below: "are there
    # pending field visits NEARBY?" is a location question (fv_nearby_pending)
    # that happens to contain "pending visits", and this rule sits ahead of it,
    # so without the guard the broader answer swallowed the specific one.
    _narrower_fv = re.search(
        r'\bnearby\b|\bclose\s+by\b|\bneighbou?rhood\b|\blocation\b'
        r'|\bsame\s+ward\b|\bconflicts?\b|\boverlap\b|\breschedul|\boverdue\b'
        r'|அருகில்|பக்கத்தில்|முரண்பாடு', msg)
    if not _narrower_fv and (
            re.search(r'\byet\s+to\s+(?:be\s+)?visit(?:ed)?\b|\bun[\s-]?visited\b'
                      r'|\bnot\s+(?:yet\s+)?visited\b'
                      r'|\bstill\s+to\s+(?:be\s+)?visit(?:ed)?\b', msg)
            or (re.search(r'\bvisits?\b|\binspections?\b', msg)
                and re.search(r'\bpending\b|\boutstanding\b|\bremaining\b', msg))):
        return "field_visits"
    if _about_visit and any(w in msg for w in [
        "unassigned", "not yet been assigned", "awaiting scheduling", "awaiting schedule",
        "no schedule", "without schedule",
        "திட்டமிடப்படாத", "திட்டமிடப்படவில்லை", "கால அட்டவணை இல்லாத"
    ]):
        return "fv_unassigned_awaiting"

    # Fixed procedural phrasings that name a process step, a rule or a format.
    # Each of these was landing on a data handler -- "what happens at level 2
    # escalation?" listed applications, "what is the application number format?"
    # listed pending work -- because the keyword ("escalation", "application")
    # matched before anything considered that the question was about the rules.
    _procedural_phrases = [
        "what happens at", "what happens if", "what happens when",
        "what happens after", "what happens next",
        "who prepares", "who approves", "who signs", "who verifies",
        "who issues", "who sanctions", "who is responsible",
        "who assigns", "who reviews", "who checks", "who confirms",
        "who authorizes", "who authorises",
        "who do i inform", "who should i inform", "whom do i inform",
        "who to inform", "whom to inform",
        "who do i contact", "who should i contact", "whom do i contact",
        "who to contact", "whom to contact",
        "who do i escalate", "who should i escalate", "escalate to if",
        "number format", "numbering pattern", "application number format",
        "common rejection", "rejection reasons", "reasons for rejection",
        "resubmission", "resubmissions are allowed", "how many resubmission",
        "can two applications", "allowed per survey", "one active application",
        "what does an sis", "what do sis officers",
    ]
    # Authority/permission questions phrased without a "who ...": "is this
    # entirely the SIS's own decision?", "can the Survey Department do X on
    # its own?", "does the ZDT need to be notified?" -- these ask about who
    # holds authority, not for a data listing, so they belong with the
    # procedural phrases above rather than falling through to a listing intent.
    _authority_phrase = any(w in msg for w in [
        "on its own", "own decision", "without anyone else's approval",
        "without anyone's approval", "without approval",
        "need to sign off", "needs to sign off",
        "need to be notified", "needs to be notified",
    ])
    # Gate on a literal application number, not on _about_one_app: the phrase
    # "the application" appears in "what is the application number format?",
    # which is a question about the format, not about any one application.
    if (any(ph in msg for ph in _procedural_phrases) or _authority_phrase) and not re.search(
            r'\d{4}/\d{3,4}/\d{1,3}/\d+', msg):
        return "general_query"

    if (re.search(r"\b(?:isd|nisd|merge)\b", msg) and re.search(r"\bsla\b|\bhow\s+long\b.*\btakes?\b|\btime\s+(?:limit|taken)\b", msg)
            and not extract_application_number(message) and not re.search(r"\b(?:been|pending|my)\b", msg)):
        return "service_code_lookup"
    if re.fullmatch(r"\s*(?:what\s+is|what's|tell\s+me)\s+the\s+(?:sla|processing\s+time|time\s+limit|turnaround)\s*[?.!]*\s*", msg):
        return "service_code_guide"
    if re.fullmatch(r"\s*(?:what\s+is\s+|what's\s+|tell\s+me\s+)?(?:the\s+|my\s+)?(?:district|taluk)\s+codes?\s*[?.!]*\s*", msg):
        return "district_code"
    # "How long does an ISD application take?" is an SLA question, answered by
    # the timelines in workflow_guide.txt, not by listing ISD applications.
    if any(w in msg for w in ["how long", "timeline", "time limit", "sla",
                              "எவ்வளவு நாள்"]) and not _about_one_app:
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
    #
    # Bare "escalat" (covering escalated/escalation/escalate) is deliberately
    # NOT enough on its own when the message also looks like an ordinary
    # status listing ("show"/"list"/"how many" + "applications"). "how
    # escalatd applications" -- a typo of "escalated", a STATUS -- used to
    # claim this branch and answer with the 3 applications approaching their
    # deadline: a real, grounded query result, just the wrong question.
    # "escalated" applications and applications "approaching the escalation
    # threshold" are different things; only the second is this feature.
    # `_explicit_status_request` already reads "escalated" as a status typo-
    # tolerantly, so falling through here sends it to the ordinary listing,
    # which correctly answers 0 -- the seeded register has no escalated
    # applications at all (see CLAUDE.md).
    _escalation_deadline_words = (
        "threshold", "approaching deadline", "deadline this week",
        "approaching threshold", "due this week", "close to deadline",
        "near deadline", "காலக்கெடு நெருங்கு", "காலக்கெடு அணுகு",
        "காலக்கெடு வரம்பு", "மேல்முறையீடு வரம்பு", "நெருங்கும் காலக்கெடு")
    # "எஸ்கலேஷன்" (the Tamil transliteration of "escalation") joins the bare
    # check below rather than the always-trigger list above -- it has
    # exactly the same "status vs. threshold-feature" ambiguity as English
    # bare "escalat", and unconditionally routing it to escalation_check
    # made "எஸ்கலேஷன் விண்ணப்பங்களைக் காட்டு" ("show escalation
    # applications") show the deadline table instead of answering the
    # status question the same way its English equivalent now does.
    _bare_escalat = "escalat" in msg or "எஸ்கலேஷன்" in msg
    # "applications" itself is the real signal, not a specific verb --
    # "how escalatd applications" (a grammatically broken typo, no "many")
    # still names applications, and any message that does is a status
    # question about them, not the standalone escalation-check feature.
    _looks_like_status_listing = bool(re.search(r"\bapplic|விண்ணப்ப", msg))
    if any(w in msg for w in _escalation_deadline_words) or (
            _bare_escalat and not _looks_like_status_listing):
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

    _has_app_pattern = bool(
        re.search(r'\b\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+\b|\b\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+\b|\b(?:ISD|NISD|MERGE)/\w+/\d+/\d+\b|\bAPP-\d+-\d+\b', message, re.IGNORECASE)
        or re.search(r'\b20\d{2}/[\w]+/[\w]+/\d+\b', message)  # broad YYYY/A/B/N fallback
    )

    # "How many applications need to be scheduled?" — scheduling phrasing is a
    # field-visit question even when it says "applications". The word used to push
    # these out of the field-visit block below, so they were answered as a plain
    # application count (which also counts NISD files, that never get a field visit).
    if not _has_app_pattern and (
        any(w in msg for w in [
            "need scheduling", "needs scheduling", "need to be scheduled",
            "needs to be scheduled", "yet to be scheduled", "not yet scheduled",
            "to be scheduled", "awaiting scheduling", "await scheduling",
            "remain to be scheduled", "still to be scheduled",
            # Tamil
            "திட்டமிட வேண்டிய", "திட்டமிடப்பட வேண்டிய",
        ])
        or (re.search(r'\b(?:need|needs|needed|yet|remaining|left|must)\b', msg)
            and re.search(r'\bschedul(?:e|ed|ing)\b', msg)
            and not re.search(r'\breschedul', msg)
            and not any(w in msg for w in ["this week", "already schedul", "conflict"]))
    ):
        return "fv_unassigned_awaiting"

    # Planning: "which application should I field visit tomorrow, and in which
    # block?" Placed ahead of the generic field-visit handling because the
    # question names an application or a block, which keeps it out of
    # _has_fv_terms below, and because the useful answer is what to go and do,
    # not the (often empty) calendar. A question about one named application is
    # left to the per-application intents.
    if not _has_app_pattern and parse_visit_plan_query(message):
        return "fv_visit_plan"

    # Bare "visit" used to be enough on its own here ("best places to VISIT in
    # kerala" has no application word either), so it answered ordinary trivia
    # with the officer's field-visit calendar. "field"/"inspection" are kept
    # bare -- they carry no everyday-English meaning against an SIS backdrop
    # the way "visit" does -- but "visit" itself now needs the word "field" or
    # "site" next to it.
    #
    # Bare "field" alone is still ordinary English far more often than it is
    # this domain's vocabulary -- "the field team is at the site today" and
    # "he works in the field" both carry it with no field-VISIT question in
    # sight, and neither names an application either, so the "not application"
    # guard above let both through and dumped the officer's whole visit
    # calendar for a sentence that asked nothing. A genuine bare-"field"
    # question is short, a real question, or opens with an interrogative /
    # imperative -- an ordinary declarative sentence is none of those, the
    # same shape test `followup_context.classify` uses for its own field-cue
    # words.
    _fv_words = msg.split()
    _looks_like_fv_question = (
        len(_fv_words) <= 5 or "?" in message
        or (_fv_words and _fv_words[0].strip(".,!") in (
            "what", "when", "where", "how", "which", "who", "why", "is",
            "was", "are", "does", "do", "did", "has", "have", "had", "can",
            "could", "would", "should", "will", "show", "list", "give",
            "tell", "any"))
    )
    _has_fv_terms = any(w in msg for w in [
        "field visit", "field visits", "inspection", "inspections", "fv", "visit date",
        "site visit", "site visits",
        "கள ஆய்வு", "களஆய்வு", "வருகை", "கள பார்வை"
    ]) or (("field" in msg or "inspection" in msg) and _looks_like_fv_question
           and not any(w in msg for w in ["application", "applications", "survey number"]))

    has_field_visit_keywords = (_has_fv_terms or (
        any(w in msg for w in [
            "schedule", "calendar", "deadline", "15-working-day", "15 working day", "15-day",
            "திட்டமிட", "திட்டமிடப்பட", "திட்டமிடப்படாத", "காலக்கெடு", "கடந்து விட்டதா", "கடந்துவிட்டதா"
        ]) and not any(w in msg for w in ["application", "applications", "type"])
    )) and not _has_app_pattern

    # ── Context-aware override: per-application field-visit field query ──
    # When the officer has just been viewing a specific application
    # (prev_intent == "application_status") and asks a simple value question
    # like "is field visit scheduled?", route to application_status so the
    # field map returns the value for *that* application.  Without this guard
    # the field-visit block below swallows the query and answers with a
    # generic "No field visits scheduled" listing.
    # Only override for simple value / yes-no queries — scheduling, listing,
    # rescheduling, and planning questions should still go to their fv_*
    # intents.
    _APP_SCOPED_INTENTS = APP_SCOPED_INTENTS
    _is_simple_fv_value_query = (
        has_field_visit_keywords
        and (prev_intent in _APP_SCOPED_INTENTS or app_scoped)
        and not _has_app_pattern
        and not any(w in msg for w in [
            "schedule for", "reschedule", "change date", "date change",
            "conflict", "this week", "between", "deadline", "15-day",
            "15 working day", "nearby", "overdue",
            "unassigned", "awaiting", "plan",
            "need scheduling", "needs scheduling",
            "show all", "list all", "display all", "all field",
            "how many field", "how many visit",
        ])
        # A follow-up value question is short, but not always 8 words short --
        # "what is the applicant name and the field visit date for this
        # application" is one, and at 8 words it used to fall through to the
        # generic field-visit listing (or to the LLM, which then invented an
        # application). The exclusion list above is what keeps scheduling,
        # planning and listing queries out, not the length.
        and len(msg.split()) <= 14
    )
    if _is_simple_fv_value_query:
        return "application_status"

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
        # "location" needs a word boundary: as a bare substring it also matches
        # "allocation", so "field visits unassigned and awaiting allocation"
        # was answered as a nearby-visits lookup and replied "Application not found".
        if (any(w in msg for w in ["nearby", "close by", "neighborhood",
                                   # Tamil: அருகில்
                                   "அருகில்", "பக்கத்தில்"])
            or re.search(r'\blocation\b', msg)) or \
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
        if any(w in msg for w in ["recently rescheduled", "rescheduled recently",
                                   "rescheduled in the last", "last 7 days", "rescheduled during",
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
        ]) or bool(re.search(
            r"\b(?:except|excluding|other\s+than|without|skip(?:ping)?)\b[\w\s]{0,15}\boverdue\b"
            r"|\boverdue\b[\w\s]{0,15}\b(?:excluded|not\s+included|left\s+out)\b"
            # Tamil/Tanglish "except" (தவிர/தவிர்த்து, thavira/thavirthu), either side of the
            # overdue word -- Tamil is matched as a substring, never \b (the virama trap).
            r"|thavir(?:thu|a)?[\w\s]{0,15}overdue|overdue[\w\s]{0,15}thavir(?:thu|a)?"
            r"|(?:தவிர்த்து|தவிர).{0,25}(?:தாமத|காலதாமத|ஓவர்)|(?:தாமத|காலதாமத|ஓவர்)\w*.{0,25}(?:தவிர்த்து|தவிர)",
            msg))

        # --- Specific overdue field visit patterns (check FIRST) ---
        if not _is_negated_overdue and any(ph in msg for ph in [
            "show overdue field", "show overdue visit", "show overdue visits",
            "list overdue field", "list overdue visits", "overdue field visits list",
            "கால தாமதமான கள ஆய்வு பட்டியல்", "தாமதமான கள ஆய்வு பட்டியல்"
        ]):
            return "fv_overdue_inspections"
        
        _is_overdue = any(w in msg for w in ["overdue", "delayed", "காலதாமதமான"]) or bool(re.search(r"\blate\b", msg))
        _is_field_visit = any(w in msg for w in ["field visit", "field visits", "visit", "visits", "inspection", 
                                                   "கள ஆய்வு", "கள்ஆய்வு", "ஆய்வு"])
        _is_list_action = any(w in msg for w in ["show", "list", "all", "display", "fetch", "get", "which", "how many", "count", "பட்டியல்", "காட்டு", "எத்தனை"])
        # A bare noun phrase IS the request. "overdue inspections" and
        # "overdue field visits" carry no list verb, so they fell through to
        # the generic `field_visits` summary and were answered with the
        # officer's WHOLE visit record -- 13 visits, 12 of them completed --
        # to a question that named one word: overdue. That is the same
        # "answer is a superset of the question" failure CLAUDE.md records for
        # "how many field visits are completed".
        _is_bare_phrase = len(msg.split()) <= 4

        # "?" used to disqualify this branch, so the natural phrasing
        # "Which field inspections are overdue?" fell through to the generic
        # field_visits summary and listed unscheduled visits instead.
        if (_is_overdue and _is_field_visit and not _is_negated_overdue
                and (_is_list_action or _is_bare_phrase)
                and not any(w in msg for w in ["what", "என்ன"])):
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
    # Same narrowing as the earlier escalation check in this function: a bare
    # "escalat" is not enough on its own when the message also looks like an
    # ordinary status listing -- see that check's own comment for why.
    _escalation_deadline_words_2 = (
        "threshold", "approaching deadline", "deadline this week",
        "approaching threshold", "due this week", "close to deadline",
        "near deadline", "காலக்கெடு நெருங்கு", "காலக்கெடு அணுகு",
        "காலக்கெடு வரம்பு", "மேல்முறையீடு வரம்பு", "நெருங்கும் காலக்கெடு")
    _bare_escalat_2 = "escalat" in msg or "எஸ்கலேஷன்" in msg
    _looks_like_status_listing_2 = bool(re.search(r"\bapplic|விண்ணப்ப", msg))
    if any(w in msg for w in _escalation_deadline_words_2) or (
            _bare_escalat_2 and not _looks_like_status_listing_2):
        return "escalation_check"

    if re.match(r"\s*(?:details\s+of\s+|show\s+|about\s+)?survey\s+(?:no|number)\.?\s*\d", msg) and not extract_application_number(message):
        return "survey_detail"
    if (re.search(r"\b(?:isd|nisd|merge)\b", msg) and re.search(r"\bsla\b|\bhow\s+long\b.*\btakes?\b|\btime\s+(?:limit|taken)\b", msg)
            and not extract_application_number(message) and not re.search(r"\b(?:been|pending|my)\b", msg)):
        return "service_code_lookup"
    if re.search(r"\b(?:isd|nisd|merge)\s+(?:meaning|means|full\s+form|expansion)\b|\b(?:meaning|full\s+form)\s+of\s+(?:isd|nisd|merge)\b", msg):
        return "service_code_lookup"
    if re.search(r"\bmy\s+(?:wards?|blocks?)\b|\bhow\s+many\s+(?:wards?|blocks?)\s+(?:do\s+i|have\s+i)\b", msg):
        return "jurisdiction_summary"
    if re.search(r"\bpending\s+(?:for|with|on)\s+me\b|\bon\s+my\s+desk\b|\bwaiting\s+for\s+me\b", msg):
        return "pending_applications"
    # "what are the applications present in SIS / in my queue" -- the desk queue, not an LLM guess
    if (re.match(r"\s*(?:what|which|list|show|display|give)\b", msg) and not extract_application_number(message)
            and re.search(r"\bapplic\w*", msg)
            and re.search(r"\b(?:pres\w{2,4}|availab\w+|exist\w*|there|in\s+(?:the\s+)?sis|in\s+(?:my\s+)?queue|at\s+sis|with\s+sis)\b", msg)
            and not re.search(r"\b(?:isd|nisd|merge|csc|sro|approved|rejected|overdue|pending|month|year|ward|block|survey|fee|how\s+many|count)\b|\d", msg)):
        return "pending_applications"
    if re.search(r"\bwhat\s+(?:should|do)\s+i\s+(?:do|need\s+to\s+do)\s+today\b|\bwhat\s+to\s+do\s+today\b|\bmy\s+tasks?\b"
                 r"|\btoday'?s\s+work\b", msg) or re.fullmatch(
            r"\s*(?:give\s+me\s+|show\s+me\s+)?(?:a\s+)?(?:summary|overview)(?:\s+of\s+my\s+(?:work|day|queue))?\s*[?.!]*", msg):
        return "officer_workload"
    if (re.search(r"\bhow\s+(?:many\s+days|long|much\s+time)\b.*\b(?:take|takes|for|does|will)\b", msg)
            and re.search(r"\b(?:isd|nisd|merge)\b|\bit\s+take", msg) and not extract_application_number(message)
            and not re.search(r"\b(?:pending|been)\b|\bmy\b", msg)):
        return "service_code_lookup"
    if re.fullmatch(r"\s*(?:what\s+is|what's)\s+the\s+(?:sla|processing\s+time|time\s+limit|turnaround)\s*[?.!]*\s*", msg):
        return "service_code_guide"
    if re.fullmatch(r"\s*(?:எனது|என்|என்னுடைய|எங்கள்)\s+(?:எல்லா\s+)?விண்ணப்பங்கள்(?:ை|ளை)?\s*[?.!]*\s*", msg) \
            or re.fullmatch(r"\s*(?:en|ennoda|enoda)\s+applications?\s*[?.!]*\s*", msg):
        return "pending_applications"

    # 1a2. Standalone Tamil application list — catch before FV outer block consumes காட்டு/பட்டியல்
    # "விண்ணப்பங்கள் பட்டியல் காட்டு" should be pending_applications not general_query
    # Exclude merge queries: "இணைப்பு விண்ணப்பங்கள் காட்டு" should still go to merge_applications
    # ALSO exclude specific field queries: "விண்ணப்பதாரர் பெயர் என்ன" should go to application_status
    if any(w in msg for w in ["விண்ணப்பங்கள்", "விண்ணப்பங்களும்", "விண்ணப்பம்", "விண்ணப்பங்கள"]) and \
       any(w in msg for w in ["காட்டு", "பட்டியல்", "காண்பி", "list", "show"]) and \
       not any(w in msg for w in ["கள ஆய்வு", "களஆய்வு", "வருகை", "field", "visit",
                                   "இணைப்பு", "இணைக்க", "merge",
                                   # a named type must reach its own handler:
                                   # "ISD விண்ணப்பங்களைக் காட்டு" was answered
                                   # as a generic pending list. "isd" also
                                   # covers "nisd" here, which is intended.
                                   "isd", "0153", "0154", "0155"]) and \
       not any(w in msg for w in ["பெயர்", "நாமாகும்", "நாமம்", "என்ன", "எது", "யார்", "எங்கே", "எப்போது",
                                   "தொலைபேசி", "மின்னஞ்சல்", "முகவரி", "நிலை", "கட்டம்"]) and \
       not any(w in msg for w in ["முன்னுரிமை", "முன்னதாய", "அதிக", "உயர்ந்த",
                                   "அவசர", "அவசரமான",
                                   "நிலுவை", "காலதாமதமான", "தாமதம்", "overdue", "priority"]):
        return "pending_applications"

    # 0-pre. "my last / previous application" -- resolved from the officer's own
    # history rather than from a number they typed. This runs before the CAN,
    # fee and service-code lookups below, which key on a bare word ("can
    # number", "fee") and would otherwise swallow the same question asked about
    # one specific file: "what is the fee on my last approved application" is
    # about that application, not about the fee schedule. Two things still win
    # over it -- an explicit application number, which is a lookup and not a
    # reference to history, and an ownership question, which joint_owner_check
    # answers properly from the ownership tables.
    if (not extract_application_number(message)
            and not any(w in msg for w in ("joint owner", "joint owners", "co-owner",
                                           "co owner", "sole owner", "multiple owner",
                                           "shared ownership"))
            and parse_last_application_query(message)):
        return "last_application"

    # 0. Specialized Analysis Intents: CAN Info, Service Code Guide
    # Token-bounded, not substring: "how" is inside "sHOW", so
    # "show my applications with their can numbers" -- a request for the
    # officer's own list -- was answered with the static CAN guide instead.
    # The same request phrased "list the can numbers of my applications"
    # routed to the listing, so one question had two different answers.
    _CAN_GUIDE_CUES = ("assigned", "assign", "csc", "how", "what is",
                       "generated", "assignment", "who assigns")
    if (("can number" in msg or "can no" in msg or "can id" in msg)
            and any(re.search(rf"\b{re.escape(w)}\b", msg) for w in _CAN_GUIDE_CUES)):
        return "can_number_info"

    # "was CAN 133280117766282 taken at a common service centre" — the officer
    # reads the number off the citizen's receipt, so the word "number" is absent.
    if re.search(r"\bcan\b\s*(?:number|no\.?|id)?\s*[:#-]?\s*\d{12,15}\b", msg):
        return "can_number_info"

    # Words that carry no subject of their own in "what is X?" — what is left
    # after removing them is what the officer actually asked about.
    _DEFN_FILLER_WORDS = {
        "what", "whats", "what's", "is", "was", "does", "do", "did", "are", "the",
        "a", "an", "this", "that", "it", "mean", "means", "meaning", "of", "for",
        "explain", "define", "definition", "stand", "stands", "tell", "me",
        "about", "please", "pls", "code", "number", "no", "num", "know",
        "enna", "endral", "artham", "vilakkam", "idhu", "adhu",
        "\u0b8e\u0ba9\u0bcd\u0ba9", "\u0b8e\u0ba9\u0bcd\u0bb1\u0bbe\u0bb2\u0bcd", "\u0bb5\u0bbf\u0bb3\u0b95\u0bcd\u0b95\u0bae\u0bcd", "\u0b85\u0bb0\u0bcd\u0ba4\u0bcd\u0ba4\u0bae\u0bcd", "\u0b8e\u0ba3\u0bcd", "\u0b87\u0ba4\u0bc1", "\u0b85\u0ba4\u0bc1",
    }

    # 15b. Urban service code questions — "what is 0153?", "what does 0161
    # mean?", "which service code is 2026/0154/28/000156?".
    # A bare code fell through to general_query and llama3.1:8b answered
    # "The service code is 0153." — restating the question. Every urban code
    # has an official name in SIS_URBAN_SERVICES, so this is a lookup, not a
    # generation. An application number in the same breath makes it a field
    # question about that file instead — and the number itself CARRIES a code
    # (2026/**0154**/28/000156), so the number is stripped before the codes in
    # the message are read, or every "difference between A and B" would look
    # like an ISD-vs-NISD definition question.
    from backend.utils.helpers import find_service_codes as _find_service_codes
    _svc_def_cue = any(w in msg for w in [
        "what is", "what's", "whats", "what does", "what are", "meaning", "mean",
        "means", "stand for", "stands for", "explain", "define", "definition",
        "which service", "which code", "what code", "full form", "expand",
        "என்ன", "என்றால்", "விளக்க", "அர்த்த", "enna", "endral", "artham",
        "vilakkam",
        # "X எதனைக் குறிக்கிறது?" / "...குறிக்கும்?" -- "what does X denote/
        # represent?", the Tamil verb for "means" rather than என்ன/என்றால்.
        # "சேவை குறியீடு (service_code) 0154 எதனைக் குறிக்கிறது?" carried none
        # of the cues above, missed service_code_lookup entirely, and fell to
        # the LLM agent -- once slow enough to help stall a whole test run.
        "குறிக்கிற", "குறிக்கும்", "குறிக்கிறதா",
    ])
    _svc_app_no = extract_application_number(message)
    _svc_msg_no_app = msg.replace(str(_svc_app_no).lower(), " ") if _svc_app_no else msg
    _svc_codes_named = _find_service_codes(_svc_msg_no_app)
    # A code found only by its NAME ("owner name" -> 0165) is a coincidence of
    # words when the message is about a record ("father name of the owner of
    # survey 5"), not a service question.
    if (_svc_codes_named and not re.search(r'\b\d{3,4}\b', _svc_msg_no_app)
            and not re.search(r'\bservice\b|\bcode\b', _svc_msg_no_app)
            and re.search(r'\b(?:survey|owner|owners|applicant|father|husband|his|her|their|of\s+the|of\s+survey)\b', _svc_msg_no_app)):
        _svc_codes_named = []
    _svc_named_kw = any(w in msg for w in ("service code", "service codes",
                                          "service_code", "சேவை குறியீடு"))

    # "show 0169 applications" / "show 0161 applications" -- an imperative,
    # not a "what is" question, so `_svc_def_cue` is False and this used to
    # fall straight through to the generic pending-queue listing, which
    # ignores any code it doesn't recognise and silently returned the
    # officer's whole desk under the "Pending Applications" label. Only
    # 0153/0154/0155 admit real rows (`ck_application_type` in the schema),
    # so a listing request naming any OTHER real code has the same honest
    # answer as "what is 0169" -- there is no register to list, and that is
    # a fact about the schema, the same rule CLAUDE.md already states for the
    # question form. isd/nisd/merge listings (0154/0153/0155) are handled
    # below and must not be caught here.
    if (not _svc_def_cue and not _svc_app_no and _svc_codes_named
            and all(c not in ("0153", "0154", "0155") for c in _svc_codes_named)):
        return "service_code_lookup"

    if _svc_def_cue and (_svc_codes_named or _svc_app_no or _svc_named_kw):
        if _svc_app_no and (_svc_named_kw or "which code" in msg or "what code" in msg):
            # "what is the service code of 2026/0154/28/000156" — a field on
            # that application, answered from the register.
            return "application_status"
        if not _svc_app_no and (_svc_codes_named or
                                (_svc_named_kw and re.search(r'\b\d{1,4}\b', msg))):
            # The officer said "service code", so a number that is not one is
            # answered as "there is no such service code" — a real answer.
            # BUT: "what are the NISD applications" / "what are ISD apps" asks
            # for a listing, not a definition — the type name happens to match
            # a service code. If the message also contains listing words, fall
            # through to pending_applications (handled ~20 lines below).
            _listing_words_here = any(
                w in msg for w in (
                    "applications", "apps", "show", "list", "display",
                    "my", "how many", "count", "total", "applic",
                    "விண்ணப்பங்கள்", "பட்டியல்", "காட்டு",
                ))
            if not _listing_words_here:
                return "service_code_lookup"

    # A number nobody labelled. "what is 0015?" is a ward or block number, or a
    # typo, or something this assistant has never heard of — what it is NOT is
    # a service code, and treating every 3-4 digit number as one is the same
    # guess the LLM was making. Answered by saying the number is not recognised
    # and naming what it could have been, rather than routed to general_query
    # where the model invents a meaning for it.
    if _svc_def_cue and not _svc_app_no and not _svc_named_kw:
        _bare_tokens = [t for t in re.findall(r"[0-9a-z\u0b80-\u0bff']+", msg)
                        if t not in _DEFN_FILLER_WORDS]
        if (len(_bare_tokens) == 1 and re.fullmatch(r'\d{1,6}', _bare_tokens[0])
                and not _find_service_codes(_bare_tokens[0])):
            return "unidentified_number"
    # "how many service codes start with 016", "list codes in 015" — a prefix
    # count, not a definition. It sits here rather than after the guide rule
    # below, which claimed it on the word "how" alone. A message that also
    # names applications is a listing question and is left to rule 16b.
    if (any(w in msg for w in ("service code", "service codes", "service_code",
                               "சேவை குறியீடு"))
            and not re.search(r'\bapplications?\b|\bapps?\b|விண்ணப்ப', msg)
            and re.search(r'\b(how many|count|list|all|show|total|starting|start|'
                          r'begin|prefix|range|within|under)\b', msg)
            and re.search(r'\b\d{3,4}\b', msg)):
        return "service_code_lookup"

    # A number typed on its own ("0153", "0015?") asks the same question with
    # the verb left out — a code if it is one, and otherwise a number this
    # assistant cannot place, which is said rather than guessed at.
    if not _svc_app_no and re.fullmatch(r'[\s?.]*\d{3,6}[\s?.]*', msg):
        return "service_code_lookup" if _find_service_codes(msg) else "unidentified_number"

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
    # Strict known-format match (service codes 0153/0154/0155 explicitly present)
    if re.search(r'\b\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+\b|\b\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+\b|\b(?:ISD|NISD|MERGE)/\w+/\d+/\d+\b|\bAPP-\d+-\d+\b', message, re.IGNORECASE):
        # This early return used to swallow every specific question that happened
        # to quote its application number: "what documents are missing for X?" and
        # "what is the SLA deadline for X?" were flattened to a generic status
        # lookup, which then answered with a summary instead of the thing asked.
        # Re-classify the same sentence with the number removed; if that yields a
        # genuine sub-topic, honour it. Stripping the number means this branch
        # cannot fire again, so the recursion is exactly one level deep.
        _subtopic = _classify_application_subtopic(message, prev_intent)
        if _subtopic:
            return _subtopic
        return "application_status"
    # Broader match: any YYYY/A/B/NNNN pattern (4 slash-separated segments, year first).
    # Catches user-typed numbers like 2026/054/02/00345 whose service/district codes
    # don't match the strict whitelist above — route to application_status so the DB
    # lookup runs and returns a proper "not found" message instead of LLM hallucination.
    _broad_app_match = re.search(r'\b(20\d{2})/([\w]+)/([\w]+)/(\d+)\b', message)
    if _broad_app_match:
        # Exclude date-like patterns: e.g. 2026/07/20/10 could be a date — require
        # at least one segment to be non-trivially long (>2 chars) or contain letters.
        _seg2, _seg3 = _broad_app_match.group(2), _broad_app_match.group(3)
        if len(_seg2) > 2 or len(_seg3) > 2 or not _seg2.isdigit() or not _seg3.isdigit():
            return "application_status"
        # Both segments are ≤2-digit numbers — ambiguous (could be a date); skip.


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
    ]) or bool(re.search(
        # the general negation words the rest of the app uses ("except", "without", …) over
        # "overdue" -- without this, "except overdue" / "excluding overdue" matched none of the
        # phrases above and routed to overdue_applications instead of excluding it.
        r"\b(?:except|excluding|other\s+than|without|skip(?:ping)?)\b[\w\s]{0,15}\boverdue\b"
        r"|\boverdue\b[\w\s]{0,15}\b(?:excluded|not\s+included|left\s+out)\b"
        # Tamil/Tanglish "except" (தவிர/தவிர்த்து, thavira/thavirthu), either side of the
        # overdue word -- Tamil is matched as a substring, never \b (the virama trap).
        r"|thavir(?:thu|a)?[\w\s]{0,15}overdue|overdue[\w\s]{0,15}thavir(?:thu|a)?"
        r"|(?:தவிர்த்து|தவிர).{0,25}(?:தாமத|காலதாமத|ஓவர்)|(?:தாமத|காலதாமத|ஓவர்)\w*.{0,25}(?:தவிர்த்து|தவிர)",
        msg))
    _is_interrogative_or_specific = any(w in msg for w in ["what", "where", "who", "which", "is it", "is this", "tell me", "give me", "காரணம்", "பெயர்", "முகவரி", "நிலை", "reason", "stage"])
    if has(ta_overdue) and not _is_negated_overdue and not _is_interrogative_or_specific:
        return "overdue_applications"

    # ── "Is anything live on this survey number?" ───────────────────────────
    # The same question as "can I apply", asked as a state rather than as a
    # permission. Both are answered by check_survey_application_lock, and both
    # must be, because the listing intents answer neither: "does survey 24 have
    # a pending application?" matched the pending rule below and listed the
    # officer's whole pending queue -- applications with nothing to do with
    # survey 24 -- while "is there any active application on survey 24?" fell
    # through to survey_detail and rendered a parcel card that never says yes
    # or no. A survey number must actually be named (an application number is
    # a question about that file, not about the parcel's lock).
    _survey_named = bool(re.search(
        r'\bsurvey\s*(?:no\.?|number|#)?\s*\d|\bகணக்கெண்\s*\d|\b\d{1,4}\s*/\s*\d+[a-z]?\b',
        msg, re.IGNORECASE))
    _lock_question = bool(re.search(
        r'\b(?:any|another|other|an)\b[^.?]{0,20}\b(?:active|live|open|ongoing|pending|existing|current)\b'
        r'[^.?]{0,20}\b(?:application|request|file|mutation)\b'
        r'|\b(?:active|live|open|ongoing|pending|existing)\s+(?:application|request|file|mutation)\b'
        r'|\b(?:locked|blocked)\b'
        r'|\bசெயலில்\s*உள்ள\s*விண்ணப்ப|\bநிலுவையில்\s*உள்ள\s*விண்ணப்ப',
        msg, re.IGNORECASE))
    if (_survey_named and _lock_question and not app_scoped
            and not re.search(r'\d{4}/\d{3,4}/\d{1,3}/\d+', msg)):
        return "can_apply_check"

    if has(ta_pending) and (has(ta_application) or has(ta_show)):
        return "pending_applications"

    _has_nisd_w  = bool(re.search(r'\b(nisd|nsid|nisdd|niisd|nidsd|ninsd|0153)\b', msg))
    _has_isd_w   = bool(re.search(r'\b(isd|0154)\b', msg))
    _has_merge_w = _has_merge_token
    _has_list_or_app = (
        has(ta_application) or has(ta_show) or
        any(w in msg for w in ["show", "list", "display", "view", "get", "fetch", "all", "application", "applications", "applic", "no", "number", "காட்டு", "பட்டியல்"])
    )

    if sum([_has_nisd_w, _has_isd_w, _has_merge_w]) >= 2:
        # "is 2026/0154/28/000001 nisd or isd?" is a question about ONE named
        # application, not a request for both type lists. app_scoped means the
        # number was stripped before this re-parse, so the reference is real.
        # A back-reference into the table just shown ("is this application isd
        # or nisd?", "is the first one isd or nisd?") is equally about one
        # application -- answering it with both type lists ignores the question.
        if app_scoped or _APP_BACKREF_RE.search(msg):
            return "is_nisd_or_isd"
        return "both_applications"

    if _has_merge_w and _has_list_or_app:
        return "merge_applications"

    if _has_nisd_w and _has_list_or_app:
        return "nisd_applications"

    if _has_isd_w and _has_list_or_app:
        return "isd_applications"

    # ── Can another application be filed on this survey number? ─────────────
    # Mutation on a parcel is synchronous: while one application on a survey
    # number is live, no other may be filed -- including on a different
    # sub-division, since the whole parcel's record is being worked on.
    _apply_question = any(p in msg for p in [
        "can i apply", "can we apply", "can he apply", "can she apply",
        "can they apply", "can the owner apply", "can another application",
        "another application be", "can a new application", "new application be",
        "can i file", "can i submit", "can i raise", "apply again",
        "can i put another", "second application", "one more application",
        "why can't i apply", "why cant i apply", "unable to apply",
        # "is survey 5 available for a new application" asks the same lock
        # question as "can I apply", phrased as availability of the parcel
        "available for a new", "available for another", "open for a new application",
        "free for a new application", "can a fresh application", "fresh application be filed",
        "விண்ணப்பிக்க முடியுமா", "மற்றொரு விண்ணப்பம்", "இன்னொரு விண்ணப்பம்",
    ])
    _survey_ref = bool(re.search(rf'\bsurvey\b|{_TA_NB}கணக்கெண்{_TA_NA}|\bsub[\s-]?division\b|\bsubdivision\b|\bஉட்பிரிவ',
                                 msg, re.IGNORECASE)) or bool(re.search(r'\b\d{1,4}/\d', msg))
    # "can I apply on the second one?" points at a row of the table just shown.
    # The survey it means is resolved later, from that table's Survey No.
    # column; without this the question fell through to general_query and the
    # LLM answered "yes, you can apply" with nothing behind it.
    _survey_ref = _survey_ref or bool(re.search(
        r'\b(?:this|that|the)\s+(?:one|parcel|land|patta)\b'
        r'|\b(?:first|second|third|fourth|fifth|last)\s+one\b'
        r'|\b(?:on|for|against)\s+(?:it|this|that)\b'
        r'|\bஇந்த\s+(?:நிலம்|புலம்)\b',
        msg, re.IGNORECASE))
    if _apply_question and _survey_ref:
        return "can_apply_check"

    # ── Which channel did THIS application come through? ────────────────────
    # "2022/0153/28/001484 is this from citizen or CSC" carries an application
    # number and the word "csc", so it fell through to the field-query rule and
    # answered with the full 21-field table instead of the one word asked for.
    # A channel question about a NAMED application gets a direct answer; the
    # count/list forms below are untouched.
    _app_ref_for_channel = bool(re.search(
        r'\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+|\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+'
        r'|APP-\d{4}-\d{6}|(?:ISD|NISD|MERGE)/\w+/\d+/\d+', msg, re.IGNORECASE
    )) or app_scoped or bool(re.search(
        # Token-bounded, and SINGULAR. These were plain substrings, so "the
        # application" matched inside "the applicationS": "what are the
        # applications from CSC" -- a listing request naming no file at all --
        # was read as a question about one application's channel and answered
        # "Please specify the application number you are asking about". A
        # plural noun is the opposite of a back-reference to one file.
        # \b does the work on its own: in "applications" the position after
        # "application" sits between two word characters, so no boundary
        # matches there.
        r"\bthis\s+app(?:lication)?\b|\bthat\s+app(?:lication)?\b"
        r"|\bsame\s+application\b|\bthe\s+application\b"
        r"|இந்த\s*விண்ணப்ப", msg))
    # "csc" / "citizen" name a channel outright. "sub registrar" does not --
    # "which sub registrar registered 2026/..." asks who registered the sale
    # deed -- so that route counts only alongside a submission phrase.
    _channel_named = any(w in msg for w in [
        "csc", "common service cent", "citizen",
    ])
    # Bare "source" is also half of "irrigation source" -- a real
    # urban_parcel_register field name (_UNTRACKED_PARCEL_FIELDS) -- so "what
    # is the irrigation source for <app>?" was swallowed whole into a channel
    # ("how did this application arrive") answer before the parcel-field
    # guard ever got a look at it. Excluded only in that one phrase; every
    # other "source" question (source_name, source_code, "came from") is
    # unaffected.
    _channel_asked = any(w in msg for w in [
        "channel", "source_name", "source_code",
        "submitted through", "submitted via", "submitted from", "submitted by",
        "who submitted", "how was it submitted", "how was this submitted",
        "filed by", "filed through", "came from", "received from",
        "referral", "referred by",
        # "how did it reach the office / reach us / come in" asks the route the
        # file took, not the desk it is sitting on -- without these the word
        # "office" pulls the question into the current-stage field lookup
        "reach the office", "reached the office", "reach our office",
        "reached our office", "how it was received",
        "எங்கிருந்து", "யார் சமர்ப்பித்தது", "சமர்ப்பித்த முறை",
    ]) or ("source" in msg and "irrigation" not in msg)
    # "how did <the application> reach / come / arrive" asks the route the file
    # took, not the desk it sits on. Without this the word "office" pulls the
    # question into the current-stage field lookup.
    # "how was 2022/0153/28/001487 submitted" is the plainest form of the
    # question, and the literal phrases above only cover "how was IT
    # submitted" -- with a number in the middle it fell through to the field
    # map, which has no channel field and answered "could not find that
    # detail".
    _channel_asked = _channel_asked or bool(
        re.search(r"how\s+(?:did|was|were)\b.{0,40}?"
                  r"\b(reach|arrive|come\s+in|get\s+here|get\s+to\s+us"
                  r"|submitted|filed|lodged)\b", msg))
    _channel_asked = _channel_asked or bool(
        re.search(r"\bwhere\s+(?:was|were)\b.{0,40}?\b(submitted|filed|lodged)\b", msg))
    _channel_question = _channel_named or _channel_asked
    # "what is the CAN number of X" is a CAN question, not a channel question --
    # can_number_info already claimed those above, so only the leftovers matter.
    _is_can_question = "can number" in msg or "can no" in msg or "can_number" in msg
    if _app_ref_for_channel and _channel_question and not _is_can_question:
        # Guard: if the message asks for a LISTING (plural applications from
        # multiple channels), it is not a single-app channel check even when
        # there is an app in scope (app_scoped=True). "what r the applications
        # from sro n csc" names TWO channels and the plural noun — route to
        # pending_applications so the channel filter is applied instead.
        _named_channels = extract_submission_channels(message)
        # `applications?` (note the `?`) matched the SINGULAR "application"
        # too, so it fired for exactly the back-reference `_app_ref_for_
        # channel` had just confirmed was singular ("this application" /
        # "that application") -- "how was this application submitted"
        # named one file, tripped this plural-listing guard anyway, and the
        # whole block was skipped, falling through to general_query for a
        # question the deterministic handler above it was built to answer.
        # Required the true plural here; "apps"/"app" stay ambiguous on
        # their own and are deliberately not matched at all.
        _plural_listing = bool(re.search(
            r"\bapplications\b|\bapps\b|\blist\b|\ball\b|\bshow\b|\bpresent\b"
            r"|\bwhat\s+r\b|\bwhat\s+are\b", msg))
        _multi_channel = len(_named_channels) >= 2
        if not (_plural_listing or _multi_channel):
            return "submission_channel_check"

    # "why is it CSC?", "how do you know it is a CSC application?", "explain in
    # detail how it is csc" -- a question about the basis for the channel of the
    # application already in view. It names no application number, so the rule
    # above misses it, and the word "csc" beside "application" then dragged it
    # into the channel LIST route below, which answered with a table of one row
    # instead of explaining anything. Requires a back-reference ("it", "this")
    # or a channel follow-up, so "how many csc applications" is untouched.
    _channel_why = bool(re.search(
        r"\bwhy\b|\bhow\s+(?:do|did|does|can)\s+(?:u|you|we)\b"
        r"|\bhow\s+is\s+it\b|\bhow\s+it\s+is\b|\bexplain\b"
        r"|\bon\s+what\s+basis\b|\breason(?:ing)?\b|\bjustif"
        r"|ஏன்|எப்படி|விளக்க", msg))
    _channel_listing = any(w in msg for w in [
        "how many", "count", "list", "total", "show all", "எண்ணிக்கை", "பட்டியல்",
    ])
    _channel_backref = bool(re.search(r"\b(?:it|this|that|its)\b|இந்த", msg))
    # "how do u judge A application whether it is sro or csc or citizen" --
    # a general question about the RULE, not about any one file. The "it"
    # here refers back to "a application" a few words earlier in the SAME
    # sentence, not to a file already in view, but `_channel_backref` cannot
    # tell those apart -- so this landed on submission_channel_check, which
    # has no application to check and asked for one: a wrong answer to a
    # question that named no file at all. An indefinite "a/an application"
    # is the generic case; "the/this/that application" (matched by
    # `_app_ref_for_channel` already) is the specific one, and only the
    # specific one is a per-file question.
    _generic_application = bool(re.search(r"\ban?\s+application\b", msg)) \
        and not bool(re.search(
            r"\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+|\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+"
            r"|APP-\d{4}-\d{6}|(?:ISD|NISD|MERGE)/\w+/\d+/\d+"
            r"|\bthis\s+app(?:lication)?\b|\bthat\s+app(?:lication)?\b"
            r"|\bsame\s+application\b|\bthe\s+application\b", msg, re.IGNORECASE))
    if (_channel_question and _channel_why and not _channel_listing
            and not _is_can_question and not _generic_application
            and (_app_ref_for_channel or _channel_backref
                 or prev_intent == "submission_channel_check")):
        return "submission_channel_check"

    # ── Submission-channel queries ──────────────────────────────────────────
    # "how many CSC applications", "list applications from sub registrar",
    # "citizen applications count", etc.
    # One vocabulary, shared with the extractor the chatbot then calls, so a
    # phrasing that routes here is a phrasing that scopes -- "CSC and SRO" is
    # not routed as a channel question and then filtered to CSC alone.
    _channel_detected = bool(extract_submission_channels(message)) or any(
        kw in msg for kw in ("igrs", "registrar", "citizen", "portal"))
    _listing_asked = any(w in msg for w in [
        "application", "applications", "app", "apps", "how many", "count", "number",
        "no of", "no.", "list", "show", "display", "view", "total",
        "விண்ணப்பம்", "விண்ணப்பங்கள்", "காட்டு", "பட்டியல்", "எண்ணிக்கை", "மொத்தம்",
    ])
    if not _listing_asked:
        # "csc aplications" -- the channel is named and the NOUN is misspelt.
        # The words above are matched as substrings, so one wrong letter in
        # "applications" dropped the whole question to the LLM even though the
        # channel had been read correctly. Same edit-distance rule as the rest
        # of parse_intent; the short words ("app", "no.") are left exact.
        _listing_asked = any(
            is_token_typo_match(tok, w)
            for tok in words
            for w in ("application", "applications", "display", "number",
                      "count", "total", "list", "விண்ணப்பங்கள்"))
    if not _listing_asked and _channel_detected:
        # The message is the channel's name and nothing else -- "csc", "sro",
        # "citizen". An officer typing that wants that channel's files; there
        # is nothing else in this domain it could ask for.
        _listing_asked = names_only_a_channel(message)
    if _channel_detected and _listing_asked:
        return "pending_applications"   # chatbot will extract the channel separately

    # An SIS officer says "file", not "application" -- "which files must I
    # clear", "what is on my desk", "how many files are with me". Treated as the
    # same noun here; the document-upload phrases ("the file", "attached file")
    # already returned general_query further up, so they cannot reach this.
    _case_file_word = has_exact([
        "file", "files", "கோப்பு", "கோப்புகள்", "கோப்புகளை", "கோப்புகளின்"])
    _desk_phrase = any(p in msg for p in [
        "on my desk", "my desk", "on my table", "my table", "with me",
        "worklist", "work list", "my queue", "pending queue", "what came in",
        # "assigned to me" is left out on purpose: "what was assigned to me
        # today" has its own assigned_today intent.
        "waiting for my action", "must i finish", "have to clear", "to dispose",
        "month end statement", "month end report", "monthly statement",
        "must i clear", "take up first", "on my plate",
        "என் மேசை", "என்னிடம்", "என் வேலை", "என்ன வேலை",
    ])
    if _desk_phrase and not has(ta_survey) and not has(ta_application) \
            and not re.search(r'\d{4}/\d{3,4}/\d{1,3}/\d+', msg):
        # "What is on my desk this month" names no noun at all -- without this
        # it fell through to the LLM, which cannot see the officer's queue.
        if has(ta_field_visit):
            return "field_visits"
        if has(ta_workload):
            return "officer_workload"
        return "pending_applications"

    # A singular back-reference ("this application", "that application", "my
    # application") names ONE specific application, not a set to list -- but
    # nothing below here checked for that, so "what is the status of this
    # application" (has(ta_status) matching "status") fell into the list
    # branch and dumped every pending application instead of resolving the
    # reference (or asking for the number when there's nothing to resolve).
    # Substring checks here matched the PLURAL too: "my application" is inside
    # "my applications", so "which of my applications are overdue" was read as a
    # singular back-reference and skipped the list branch. Anchor each phrase so
    # a trailing "s" (or any word char) disqualifies it.
    _is_singular_app_ref = bool(re.search(
        r'\b(?:this|that|same|my|the)\s+app(?:lication)?(?!\w)'
        r'|இந்த\s+விண்ணப்பம்|அந்த\s+விண்ணப்பம்', msg))

    # "what was approved last year", "what got rejected this month" -- a
    # status word plus a date scope, with no "application" noun at all to
    # trip the block below. Without this, these fell all the way through to
    # general_query and the LLM answered "I don't have information on
    # specific approvals" instead of listing the register's own record.
    _bare_status_word = any(
        is_token_typo_match(tok, w) for tok in words
        for w in ("approved", "approve", "rejected", "reject", "cancelled", "cancel",
                   "pending", "escalated")) or bool(re.search(
        r"\bin[ _-]?progres{1,2}\b", msg))
    _bare_date_scope = (bool(re.search(r"\b20\d{2}\b", msg)) or any(
        w in msg for w in ("last year", "this year", "previous year",
                            "last month", "this month", "previous month",
                            "between", "since"))
        or bool(re.search(r"\b(?:last|this|previous|prev|past)\s+yrs?\b", msg))
        or extract_month_from_text(msg))
    if (_bare_status_word and _bare_date_scope and not _is_singular_app_ref
            and not re.search(r'\d{4}/\d{3,4}/\d{1,3}/\d+', msg)):
        return "pending_applications"

    # "applicants" is here in the PLURAL only: "show the first two applicants"
    # is a request for the officer's list with the names on it, while the
    # singular "the applicant" names a field of one file and belongs to the
    # field lookup.
    if (has_exact(["applications", "application", "app", "apps", "applicants", "applic", "விண்ணப்பங்கள்", "விண்ணப்பங்களை", "விண்ணப்பம்", "விண்ணப்பத்தை", "ஆப்ளிகேஷன்"]) \
            or _case_file_word or "applic" in msg) and not _is_singular_app_ref:
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
        # "approved applications" / "rejected applications" / "escalated
        # applications" / "in progress applications" -- a bare status
        # adjective plus the noun, no verb at all. "pending applications"
        # already worked (`ta_pending` covers it); these four did not, and
        # fell all the way through this whole chain to the LLM -- which
        # then had no register to answer from. This is also what let a
        # NEGATED status ("not approved applications", "neither approved
        # nor rejected applications") reach the LLM too: `_explicit_status_
        # request` in chatbot.py already reads the negation correctly, but
        # only once the message is routed HERE for it to run on.
        elif (re.search(r"\bapprove", msg) or re.search(r"\bcompleted\b", msg)
              or re.search(r"\brejec", msg) or re.search(r"\bescalat", msg)
              or re.search(r"\bin[ _-]?progres{1,2}\b", msg)
              or any(is_token_typo_match(w, t) for w in words
                     for t in ("approved", "approve", "completed", "rejected", "cancelled", "cancel",
                               "reject", "escalated"))):
            return "pending_applications"
        elif has_exact(["today", "yesterday", "day before", "tomorrow", "day after", "morning", "afternoon", "evening", "am", "pm", "new", "received", "submitted", "இன்று", "இன்னிக்கு", "இனிக்கு", "நேற்று", "நேத்து", "நாளை", "காலை", "பிற்பகல்", "மதியம்"]):
            return "pending_applications"
        elif any(w in msg for w in ["count", "number", "how many", "total", "no of", "no.", "எண்ணிக்கை", "மொத்தம்", "எத்தனை"]) or \
             any(w in msg for w in ["between", "from", "year", "during", "week", "month", "இடையே", "வரை",
                                    "மாத", "மாதம்", "இந்த மாத", "கடந்த மாத", "முந்தைய மாத", "ஆண்டு", "வருடம்"]) or \
             re.search(r"\b20\d{2}\b", msg) or re.search(r"\byrs?\b", msg):
            # Any four-digit year, not just the two that were hard-coded here:
            # "give me rejected files in 2023" fell through to the LLM.
            return "pending_applications"   # date-range / count query → fetch all in range
        elif season_in_text(msg) or extract_month_from_text(msg):
            # "summer applications", "june applications" — a time scope with no
            # other verb. Without this they fell through to general_query and
            # the LLM answered that it had never heard of a summer application.
            return "pending_applications"
        elif any(w in msg for w in ["show", "list", "display", "view", "get", "fetch", "all", "with", "காட்டு", "பட்டியல்", "உடன்"]):
            return "pending_applications"
        elif any(w in msg for w in ["evlo", "eppadi", "iruku", "irukku", "kaattu", "kaami",
                                     # "kami" (single 'a') is the same word as
                                     # "kaami" -- Tanglish vowel length is
                                     # typed inconsistently, and the substring
                                     # match above has no typo budget at all,
                                     # so "applications kami" fell through to
                                     # the LLM, which had no application list
                                     # to answer from and invented a
                                     # jurisdiction refusal instead.
                                     "kami",
                                     "venum", "maasam", "innaiku", "inniku", "naalaikku",
                                     "poana", "indha", "list pannu", "kaatunga"]):
            # Tanglish as officers type it: "indha maasam files enna",
            # "approve aana files evlo".
            return "pending_applications"
        elif extract_result_limit(msg):
            # "first 2 applications", "the last 3 files" -- naming how much of
            # the list is wanted is itself a request for the list, verb or no
            # verb. Without this the count made the message look like nothing
            # the rules recognised and it went to the LLM.
            return "pending_applications"
        elif has(ta_ward) or has(ta_block) or has(ta_status) or \
                any(w in msg for w in ["நிராகரிக்கப்பட்ட", "அங்கீகரிக்கப்பட்ட", "நிலுவை",
                                        "versus", " vs ", "position"]):
            # A noun plus a filter and nothing else -- "வார்டு 002 விண்ணப்பங்கள்",
            # "pending versus approved position". Still a list request.
            return "pending_applications"

    # "What is the pending versus approved position this month", "கடந்த மாத
    # கணக்கு" — a report request that names no noun the rules above look for.
    # "What is my approval rate this month" is the completion-rate report.
    if any(w in msg for w in ["approval rate", "disposal rate", "clearance rate",
                              "success rate", "rejection rate", "completion rate"]):
        return "completion_rate"

    if ("pending" in msg and any(w in msg for w in ["versus", " vs ", "vs.", "against"])) or \
            (any(w in msg for w in ["கணக்கு", "கணக்கெடுப்பு"]) and
             any(w in msg for w in ["மாத", "மாதம்", "ஆண்டு", "வருடம்", "இன்று", "வார", "நிலுவை"])):
        return "pending_applications"

    # 1c. "Which taluk/ward/district/town/block do I belong to / am I assigned
    # to" — the officer is asking about their OWN jurisdiction. This must run
    # BEFORE the field-keyword routing below, because "ward" and "block" are
    # also listed there as application-record field names and would otherwise
    # steal this question into application_status (which then has no
    # application number to work with and falls through to the raw LLM).
    _self_jur_ref = any(w in msg for w in [
        "belong to", "am i assigned", "am i in", "i am in", "do i work in",
        "i belong", "assigned to", "i am assigned",
        # "என் வார்டு எது" — the officer asking which ward/taluk is theirs.
        "என் வார்டு", "எனது வார்டு", "என் தாலுகா", "எனது தாலுகா",
        "என் மாவட்டம்", "எனது மாவட்டம்", "என் பிளாக்", "எனது பிளாக்",
        "எனக்கு சொந்தமான", "நான் எந்த", "எனக்கு ஒதுக்கப்பட்ட",
    ])
    # "என்ன" / "enna" is how the question is actually asked in Tamil and
    # Tanglish ("என் மாவட்டம் என்ன"); without it the Tamil possessives above
    # matched but the question word did not, and the whole clause failed.
    _self_jur_which = any(w in msg for w in ["which", "what", "எந்த", "எது",
                                             "என்ன", "enna", "edhu", "ethu"])
    # The plainest forms of the question -- "what is my taluk", "what blocks do
    # I cover", "which wards am I responsible for" -- carry none of the phrases
    # above. They were falling to the field-keyword rule (which reads "ward" and
    # "block" as application-record fields) or to the raw LLM, which answered
    # "your jurisdiction includes the count of Taluks, Blocks, Wards ... view it
    # on the officer dashboard" instead of naming them.
    # "am I block level or ward level?" / "am I a block SIS or ward SIS?" self-
    # identify by level rather than asking "which ward is mine" -- no "my" and
    # no "which"/"what", so neither cue above caught them and they fell to
    # general_query, where an unavailable/hallucinating LLM either refuses or
    # invents a level instead of reading it off officer_jurisdictions.jurisdiction_type.
    # "am i" is bare on purpose: the outer `_jur_noun` check below still
    # requires an actual level word in the same message, so "am I overdue" or
    # "am I eligible" (no level noun) never reaches this branch.
    _self_jur_owned = bool(re.search(r'\bmy\b|\bmine\b|\bam i\b|\bi am\b', msg)) or any(p in msg for p in [
        "i cover", "do i cover", "am i responsible", "i am responsible",
        "i handle", "do i handle", "i am handling", "am i handling",
        "under me", "allotted to me", "allocated to me",
        "given to me", "i look after", "i oversee",
        # Tamil has no copula for "am I X" -- the question is formed by an "ஆ"
        # interrogative suffix on the noun itself ("அதிகாரியா" = officer + ஆ),
        # so bare "நான்" (I) is the only reliable cue; it is gated the same way.
        "நான்",
        "நான் கவனிக்கும்", "எனக்கு ஒதுக்கிய",
    ]) or bool(re.search(
        # The Tamil/Tanglish possessive in front of a jurisdiction noun --
        # "எனது மாவட்டம்", "en ward", "ennoda taluk". English "my block" was
        # already enough on its own; these are the same question.
        # "என்" is not \b-bounded like the ascii cues (virama isn't a \w
        # boundary), so a plain "என்" alternative also matches inside "என்ன"
        # ("what") -- "என்ன மாவட்டம் இது" ("what district is this") then read
        # as a self-jurisdiction question. (?!ன) blocks that one continuation
        # without needing a full word-boundary rewrite.
        r'(?:என்(?!ன)|எனது|எனக்கு|\bennoda\b|\benakku\b|\ben\b|\bnaan\b)\s*'
        r'(?:\w+\s+)?'
        r'(?:மாவட்ட|தாலுக|தாலூக|வார்ட|பிளாக்|நகர|பகுதி|'
        r'district|taluk|thaluk|ward|block|town|jurisdiction)',
        msg))
    # Typo-tolerant: "jurisdication" / "jurisdicton" / "distict" are the most
    # common misspellings an officer types, and a plain substring test misses
    # every one of them. has() does exact token matching for short words and
    # edit-distance matching for words of 5+ characters.
    _jur_noun = has([
        "taluk", "தாலுகா", "தாலுக்கா", "ward", "வார்டு",
        "district", "மாவட்டம்", "town", "நகரம்", "block", "பிளாக்",
        "jurisdiction", "அதிகார வரம்பு", "area", "பகுதி", "zone",
    ]) or _jurisdiction_fuzzy_match()
    # Anything naming a work item is a queue question, not a "where do I work"
    # question -- "my pending applications" must stay with its own intent.
    _work_item_noun = any(w in msg for w in [
        "application", "applications", "survey", "surveys", "file", "files",
        "visit", "visits", "inspection", "inspections", "workload", "pending",
        "overdue", "விண்ணப்ப", "கணக்கெண்", "கோப்பு", "கள ஆய்வு",
    ])
    if _jur_noun and (_self_jur_ref and _self_jur_which or _self_jur_owned) and \
       not _work_item_noun and \
       not has(ta_survey) and not has(ta_application) and \
       not re.search(r'\d{4}/\d{3,4}/\d{1,3}/\d+', msg):
        return "jurisdiction_summary"

    # A geo follow-up to a jurisdiction answer stays with jurisdiction_summary.
    # "what is my ward?" -> "which block is that in?" names a level and points
    # back at the previous turn ("that" / "it" / a leading "and"). Without this
    # guard the field-keyword rule below reads the bare "block" / "taluk" as an
    # application field and asks the officer for an application number they
    # never mentioned. Kept narrow: a back-reference, a single level focus, and
    # NOT a count question ("how many taluks does my district have?" is still
    # taluk_summary), a work-item queue question, or an explicit app number.
    if prev_intent == "jurisdiction_summary" and \
       not _work_item_noun and \
       not re.search(r'\d{4}/\d{3,4}/\d{1,3}/\d+', msg) and \
       not re.search(r'\bhow many\b|\bcount\b|\bnumber of\b|\blist\b|எத்தனை', msg) and \
       (re.search(r'\b(that|it|its|there|the same)\b', msg)
        or re.match(r'\s*(?:and|what about|how about)\b', msg)) and \
       detect_jurisdiction_focus(message):
        return "jurisdiction_summary"

    # 2a. Field-specific queries (name, address, mobile, survey no, etc.) → application_status
    # These queries ask about specific applicant/application fields
    _field_keywords = [
        # English
        "name", "address", "mobile", "phone", "status", "stage", "type",
        "applicant", "contact", "priority", "aadhaar", "date", "submission",
        # Bare "can" removed -- it's an ordinary modal verb ("can you tell me
        # the weather"), and the dedicated CAN-number regex a few hundred
        # lines up (\bcan\s*(?:number|no\.?|id)\b) already owns the real
        # question; this fallback only needs the phrase form.
        "serial", "serial number", "serial_number", "can number", "can_number",
        "patta", "patta number", "patta_number", "subdivision", "subdivision number",
        "current subdivision", "current_subdivision_number",
        "role", "role id", "role_id", "user", "user id", "user_id",
        "service", "service_code", "district_code", "taluk_code",
        "village_code", "urban_unit_code", "ward_code", "block_code", "ward", "block",
        "urban unit", "urban unit code", "district code", "taluk code",
        "village code", "ward code", "block code",
        "received", "source", "source_code", "source_name",
        "workflow_state",
        "reason", "declared reason", "declared_reason", "purpose", "survey number", "survey no", "survey_no",
        # Tamil
        "பெயர்", "நாமாகும்", "நாமம்", "முகவரி", "தொலைபேசி",
        "நிலை", "கட்டம்", "வகை", "விண்ணப்பதாரர்", "தேதி", "வரிசை எண்",
        "பட்டா எண்", "உட்பிரிவு எண்", "கணக்கெண்", "சர்வே எண்",
        "பயனர் ஐடி", "பங்கு ஐடி", "காரணம்",
        # Tanglish
        "peyar", "mugavari", "tholaipaesi", "nilai", "kattam", "varisai en", "kaaranam"
    ]
    _interrogative = ["what", "which", "who", "where", "when", "give", "tell", "show",
                      "என்ன", "எந்த", "யார்", "எங்கே", "எப்போது", "காட்டு", "சொல்"]

    def _kw_hit(kw: str) -> bool:
        # Tamil script keeps plain substring matching (agglutinative script,
        # a token-boundary check is unreliable across case suffixes). ASCII
        # keywords get a real word boundary -- without it "phone" matched
        # inside "telephone"/"smartphone" and routed unrelated trivia
        # questions ("who invented the telephone") into application_status.
        if any('\u0B80' <= c <= '\u0BFF' for c in kw):
            return kw in msg
        return re.search(r'\b' + re.escape(kw) + r'\b', msg) is not None

    has_field = any(_kw_hit(kw) for kw in _field_keywords)
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
    # A bare subdivision reference ("who owns subdivision 1349/1?") carries no
    # "survey" token at all, so it needs its own signal here -- otherwise it
    # fell through to application_status and demanded an application number.
    _is_survey_query = bool(re.search(r'\bsurvey\s*\d+\b|\bகணக்கெண்\s*\d+\b', msg, re.IGNORECASE)) or ((has(ta_survey) or has(ta_subdivision)) and not has_application_context and not _has_app_pattern)
    # "next available sub-division for survey 145" asks for the next number, not a
    # survey detail dump — the more specific intent wins.
    _wants_next_subdivision = has(ta_next) and (has(ta_subdivision) or "subdivision" in msg or "subdivisions" in msg)
    if _is_survey_query and _wants_next_subdivision:
        return "next_subdivision"
    # ISD per-application sub-division queries -- "proposed sub-divisions of X",
    # "new owners of the sub-divisions for X", "assigned sub-division numbers for
    # X", "latest action on each sub-division of X". These have to win before the
    # survey_owners / survey_detail / specific-field catches below, which
    # otherwise swallow anything containing "subdivision" (and misread
    # "sub-division numbers" as a single-field lookup, routing to
    # application_status with an empty answer). Gated on there being an
    # application in scope (inline number, "application" word, or the
    # application-subtopic re-classifier's app_scoped flag) so a bare
    # "who owns subdivision 1349/1?" still routes to survey_owners.
    _sd_kw = has(ta_subdivision) or "subdivision" in msg or "sub-division" in msg or "sub division" in msg
    if _sd_kw and (_has_app_pattern or has_application_context or app_scoped) and (
        "proposed" in msg or "முன்மொழியப்பட்ட" in msg
        or ("assigned" in msg and ("number" in msg or "numbers" in msg))
        or "latest action" in msg or "action taken" in msg
        or "each sub-division" in msg or "each subdivision" in msg
        or "patta transfer" in msg or "transfer order" in msg
        or "how many" in msg or "list" in msg or "show" in msg or "what are" in msg
        or "owner" in msg or "who gets" in msg or "who will own" in msg
        or "எத்தனை" in msg or "காட்டு" in msg or "பட்டியல்" in msg or "என்ன" in msg
        or "உரிமையாளர்" in msg
    ):
        return "isd_processing"
    # "Who is the owner of survey 155?" is an ownership question, not a request to
    # dump the survey record. The generic survey_detail rule below matches it too
    # (via the "who" interrogative), so the more specific intent has to win first.
    if _is_survey_query and has(ta_owner):
        return "survey_owners"
    # "compare original and proposed area for X" -- an ISD area-reconciliation
    # question that need not spell out "sub-division". Distinctive enough on its
    # own; the application-subtopic re-classifier has already confirmed an
    # application number was present.
    if ("area" in msg or "பரப்பளவு" in msg) and (
        "compare" in msg or "ஒப்பிடு" in msg or "ஒப்பீடு" in msg
        or ("original" in msg and "proposed" in msg)
        or ("அசல்" in msg and "முன்மொழியப்பட்ட" in msg)
    ):
        return "isd_processing"

    # "is there litigation on subdivision 1344/2?" carries ta_subdivision too,
    # so without this guard it was caught here before ever reaching the
    # litigation_check keyword match below and lost the litigation flag.
    _is_litigation_wording = any(w in msg for w in ["litigation", "court", "legal", "flagged", "case flag"])
    # "under what conditions can two surveys be merged" names no survey; it asks
    # for a rule, which the documents answer, not for a survey record ("No
    # records found").
    _is_rule_question = (not re.search(r"\d", msg)
                         and re.search(r"\b(conditions?|criteria|rules?|requirements?|eligib\w*)\b", msg))
    if (_is_rule_question and re.search(r"\bmerg", msg)
            and not re.search(r"\b(my|show|list|how many|pending|approved|rejected)\b", msg)):
        return "general_query"
    if _is_survey_query and not _is_litigation_wording and not _is_rule_question and (has(ta_subdivision) or has(ta_detail) or has(ta_show) or has_interrogative or "subdivision" in msg or "subdivisions" in msg):
        return "survey_detail"

    # A question about the workflow itself ("what happens after the SIS stage?",
    # "who approves the patta order?") is about the process, not about one
    # application. It mentions stage/approval words, which otherwise route it to
    # application_status and make the bot demand an application number for a
    # question that never had one. Send it to RAG instead.
    _is_process_question = bool(re.search(
        r'\bwhat\s+happens\b|\bwhat\s+comes\b|\bnext\s+stage\b|\bnext\s+step\b'
        r'|\bwho\s+(?:approves|signs|verifies|issues|sanctions)\b'
        r'|\bhow\s+does\s+the\s+\w+\s+work\b|\bwhat\s+is\s+the\s+(?:process|procedure|workflow)\b'
        r'|\bஎன்ன\s+நடக்கும்\b|\bஅடுத்த\s+கட்டம்\b',
        msg, re.IGNORECASE,
    )) and not _has_app_pattern
    if _is_process_question:
        return "general_query"

    # "when was it approved / rejected / submitted / closed" -- a follow-up
    # about the application already in view. It names an EVENT, not one of the
    # field nouns below, so the field-keyword rule never caught it and the
    # question fell through to the LLM -- even though "which block is it from",
    # asked in the same breath, resolved the same reference perfectly well.
    # Requires a back-reference, so "when was my last application approved"
    # stays with last_application and "how many approved applications do I
    # have" stays a count.
    _event_word = bool(re.search(
        r"\b(?:approved|approval|rejected|rejection|closed|closure|disposed|cancelled|cancel"
        r"|decided|decision|signed|submitted|submission|filed|lodged)\b", msg)
        or any(w in msg for w in ("அங்கீகரி", "நிராகரி", "சமர்ப்பி")))
    _when_word = bool(re.search(
        r"\b(?:when|what\s+date|which\s+date|on\s+what\s+date|how\s+long)\b", msg)
        or any(w in msg for w in ("எப்போது", "எந்த தேதி")))
    _back_ref = bool(re.search(
        r"\b(?:it|its|it's|this|that|the\s+application|the\s+file|the\s+same)\b", msg)
        or any(w in msg for w in ("இந்த", "அந்த")))
    if _event_word and _when_word and _back_ref and not _is_list_query:
        return "application_status"

    # "how many of them are approved", "which of them are ISD" -- a follow-up
    # that narrows or counts the list already on screen. The plural pronoun is
    # the whole signal: without it these fell through to the LLM, which has no
    # list to count. chatbot.py then reads the filters from the question this
    # one points back at (see _list_followup_scope).
    _plural_backref = bool(re.search(r"\b(?:they|them|those|these)\b", msg)
                           or any(w in msg for w in ("அவை", "அவற்ற")))
    _list_quality = (any(w in msg for w in (
        "how many", "which", "count", "list", "show", "any of",
        "எத்தனை", "எந்த", "பட்டியல்"))
        or _event_word or bool(re.search(r"\b(?:isd|nisd|merge|overdue|pending)\b", msg)))
    if _plural_backref and _list_quality:
        return "pending_applications"

    # Route to application_status if asking about a specific field + has interrogative OR mentions application OR is a short field follow-up, but not a list query
    if has_field and (has_interrogative or has_application_context or len(words) <= 4 or prev_intent == "application_status") and not has(ta_overdue) and not has(ta_pending) and not _is_list_query:
        return "application_status"

    if has(ta_subdivision) and (has(ta_application) or has(ta_show)):
        # Sub-division applications are ISD applications (ISD = Involving Sub-Division).
        return "isd_applications"

    if not _is_litigation_wording and not _is_rule_question and has(ta_survey) and (has(ta_show) or has(ta_detail) or "surveys" in msg or "எண்கள்" in msg or ("number" in msg and not any(w in msg for w in ["applications", "விண்ணப்பங்கள்", "விண்ணப்பங்களை"]))):
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
        # A days-overdue calculation only makes sense against ONE file: it needs
        # an application in context or a singular back-reference. A bare
        # interrogative ("what is overdue", "which are overdue") is a request
        # for the late LIST -- leaving `has_interrogative` here sent it to
        # application_status, which then just asks which application.
        has(ta_overdue) and (
            has_application_context or
            bool(re.search(r'\b(prev|previous|this|that|same|last|it)\b', msg))
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
                bool(re.search(r'(\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+|\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+|APP-\d{4}-\d{6}|(ISD|NISD|MERGE)/\w+/\d+/\d+|20\d{2}/[\w]+/[\w]+/\d+)', msg, re.IGNORECASE)) or
                bool(re.search(r'\b(this|that|prev|previous|same|last|it)\b', msg, re.IGNORECASE))
            ) and any(w in msg for w in ["how long", "how many", "duration", "days", "since", "எவ்வளவு", "எத்தனை", "நாள்", "நாட்கள்"])
        )
    )
    if _asking_pending_duration:
        return "application_status"

    # 3. Overdue applications list
    if has(ta_overdue) and not _is_negated_overdue:
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
    # Word-boundary match required here: a plain substring test on "isd" also
    # matches inside "jurisdiction" (jur-ISD-iction), so "what is my
    # jurisdiction" was being misread as an ISD definition question and routed
    # to general_query instead of jurisdiction_summary.
    if _has_def_kw and not _has_explicit_list_action and re.search(
        r'\b(?:0153|0154|service[ _]code|nisd|isd|merge)\b', msg, re.IGNORECASE
    ):
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
        _has_merge_word = _has_merge_token
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
    # "show the documents submitted for 2026/..." carries none of the original
    # trigger words, so it fell through to application_status and dropped the
    # document list entirely.
    if "document" in msg and any(w in msg for w in [
        "missing", "required", "all", "have", "submitted", "submit", "uploaded", "show", "list",
        # "check documents for this application" / "verify the documents" carry
        # no other trigger word and fell through to general_query.
        "check", "verify", "any", "which", "what",
        # "are documents CORRECT for X" / "is document verification DONE" /
        # "documents in order" / "documents complete" carried none of the
        # words above and fell through to the generic application_status
        # summary card, which never says a word about documents.
        "correct", "valid", "verification", "verified", "complete", "order", "ok", "fine",
    ]):
        return "check_documents"
    # Tamil: ஆவணங்கள் சரிபார் / சமர்ப்பிக்கப்பட்டனவா (were documents submitted) /
    # ஆவணங்கள் சரியா (are the documents correct)
    if any(w in msg for w in ["ஆவணங்கள்", "ஆவணம்"]) and any(w in msg for w in [
        "சரிபார்", "தேவையான", "இல்லாத", "காணாத", "சமர்ப்பி", "சரியா", "சரி"]):
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
    # "jurisdiction" goes through has() so misspellings ("jurisdication",
    # "jurisdicton", "jursdiction") still land here instead of falling through
    # to general_query and being answered by the raw LLM.
    if (has(["jurisdiction"]) or _jurisdiction_fuzzy_match() or
        any(w in msg for w in ["my area", "assigned area", "coverage",
                               # Tamil: எனது பகுதி, ஒதுக்கப்பட்ட பகுதி
                               # Bare, not "என் அதிகார வரம்பு": Tamil marks the
                               # possessive with either என் or எனது, and only
                               # the first was listed -- so "எனது அதிகார வரம்பு
                               # என்ன" fell through to the LLM, which answered
                               # with the officer's WARD name labelled as their
                               # district. The noun alone is unambiguous, and
                               # the survey/application guard below still
                               # keeps it off record lookups.
                               "எனது பகுதி", "ஒதுக்கப்பட்ட பகுதி", "அதிகார வரம்பு"])) and \
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
        is_type_query   = any(w in msg for w in ["isd", "nisd", "merge", "type"]) or bool(re.search(r'\b0\d{3}\b', msg))
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


# A sub-division tail is NOT just "1" or "1A". The parcel register carries
# 1362/17, 1355/1A, 1355/1B12, 1361/1HL and 35/2 O -- a digit run followed by
# any number of letter+digit groups. The old `\d{1,2}[A-Z]?` matched only the
# first two shapes, so "1355/1B12" silently truncated to "1355" and the answer
# covered all 93 sub-divisions of the parcel instead of the one asked about.
# The trailing " O" form is admitted only as a single letter at a token
# boundary, so "1355/1B and 1361/1A" does not swallow the "and".
_SUBDIV_TAIL = r'\d{1,3}(?:[A-Za-z]{1,2}\d{0,3})*(?:\s[A-Za-z]\b)?'


def extract_survey_number(message: str) -> Optional[str]:
    """
    Extract survey number from message, handling list prefixes and survey keywords.
    Supports formats: "145", "145/1A", "145/1B12", "survey no 145", "survey 145/2B".
    """
    cleaned = clean_message(message)

    # An application number carries "0153/28" in its middle, and both the
    # slash pattern and the digit fallback below happily read that as a survey
    # number -- "are there joint pattadars in 2026/0153/28/001720" answered
    # "Survey number 0153/28 not found". Take application numbers out first;
    # the handlers that want the application already extract it separately.
    cleaned = re.sub(r'\b20\d{2}/\d{3,4}/\d{1,3}/\d+\b', ' ', cleaned)

    # Keyword match first (e.g. "survey 145", "survey no 145/1A")
    keyword_match = re.search(
        r'\bsurvey(?:\s+(?:no|num|number|nos|numbers)(?:\.|\b)?)?(?:\s*[:\-#])?\s*'
        r'(\d{1,6}(?:/' + _SUBDIV_TAIL + r')?)\b',
        cleaned, re.IGNORECASE
    )
    if keyword_match:
        return keyword_match.group(1)

    # Survey number with subdivision pattern (e.g. 145/1A)
    slash_match = re.search(r'\b\d{1,4}/' + _SUBDIV_TAIL + r'\b', cleaned, re.IGNORECASE)
    if slash_match:
        return slash_match.group(0)

    # Fallback to any 1-4 digit number
    fallback_match = re.search(r'\b\d{1,4}\b', cleaned)
    if fallback_match:
        return fallback_match.group(0)

    return None


# ── Submission channels: one vocabulary, read once ──────────────────────────
# The three channels an application can arrive through, with every word an
# officer uses for each. ASCII keywords are matched on token boundaries so
# "sro" never matches inside another word; Tamil is matched as a SUBSTRING,
# never with \b -- the virama is not a word character, so a boundary is found
# mid-word (the same trap documented for the comparison parser and the
# follow-up layer).
#
# The order here is canonical, and is what "all channels" expands to.
SUBMISSION_CHANNELS = ("CSC", "sub_registrar", "citizen")

_CHANNEL_VOCAB = (
    ("CSC", (r"csc", r"cscs", r"common\s+service\s+cent(?:er|re)s?",
             r"service\s+cent(?:er|re)s?"),
     ("சேவை மைய",)),
    ("sub_registrar", (r"sub[\s_-]?registrars?(?:\s+office)?", r"sro", r"sros",
                       r"registrar(?:'s)?\s+office", r"igrs\s+referrals?",
                       # Bare "registrar(s)" -- `_CHANNEL_FUZZY_NEVER` excludes
                       # it from the fuzzy path on the assumption this exact
                       # pattern already covers it, which used to be true only
                       # when "sub" or "office" was also typed: "show
                       # registrar applications" named no channel at all and
                       # fell through to the whole desk queue.
                       r"registrars?"),
     ("சார்-பதிவாளர்", "சார் பதிவாளர்", "பதிவாளர் அலுவலக")),
    ("citizen", (r"citizen\s+portal", r"direct\s+citizen", r"self[\s-]?submitted",
                 r"self[\s-]?registered", r"tn\s+portal", r"online\s+portal"),
     ("குடிமகன்", "குடிமக்கள்")),
)

# "all channels", "every source", "all three routes" -- a request for every
# channel at once. It has to name the channel noun: "all applications" is not a
# channel scope, it is the whole register.
_ALL_CHANNELS_RE = re.compile(
    r"\b(?:all|every|each|any|3|three)\b[^.?!]{0,24}?"
    r"\b(?:channels?|sources?|routes?|modes?)\b"
    r"|\b(?:channels?|sources?|routes?|modes?)\b[^.?!]{0,12}?\b(?:all|every)\b",
    re.IGNORECASE)
_ALL_CHANNELS_TA = ("அனைத்து வழி", "எல்லா வழி", "அனைத்து மூல", "எல்லா மூல")


# Words a misspelling of which still names the channel, checked with the same
# edit-distance rule parse_intent uses (`_max_edits_for`: 4-7 chars one edit,
# 8+ two, 3 or fewer exact only). "citizn" and "sub registrer" are what
# officers actually type, and each used to name no channel at all -- so the
# question either fell to the LLM or, worse, was answered as an UNSCOPED
# listing with the misspelt word silently ignored.
#
# `csc` and `sro` are deliberately absent: three letters get no typo budget,
# the same rule that stops `isd` absorbing `nisd`.
# `citizen` is NOT here. A bare "citizen" is a channel only when the sentence
# is about where an application came from -- "citizen access number" is the CAN
# -- and that judgement lives in the guard inside extract_submission_channels.
# Matching it here would reach the caller ahead of the guard and make every CAN
# question a channel question.
_CHANNEL_FUZZY = (
    ("sub_registrar", ("registrar", "registrars")),
)

# Real words that sit inside the edit budget of a channel word and mean
# something else entirely. "register" is TWO edits from "registrar", which is
# exactly the budget an 8-letter target gets -- so "does your register store
# the applicant's occupation?" became a Sub-Registrar question, and one such
# question was re-routed from a field lookup to a channel LISTING. The same
# trap `_NEVER_TYPO` exists for elsewhere in this module.
_CHANNEL_FUZZY_NEVER = {
    "register", "registers", "registered", "registering", "registry",
    "registries", "registration", "registrations", "registrar",  # exact hits
    "registrars",                                                # use the regex
    "several", "serval",
}


# Tokens that are part of a channel's own name, and the politeness a message
# may carry around it. A message made of nothing else -- "csc", "sro",
# "citizen", "CSC please" -- names a channel and asks for nothing else, so
# it is that channel's listing. Left to fall through, "csc" reached the LLM,
# which is the one path in the pipeline that cannot look a channel up.
_CHANNEL_NAME_TOKENS = {
    "csc", "cscs", "sro", "sros", "citizen", "citizens",
    "sub", "registrar", "registrars", "office", "portal", "common", "service",
    "center", "centre", "centers", "centres", "counter", "counters",
}
_CHANNEL_ONLY_FILLER = {
    "my", "me", "the", "a", "an", "of", "from", "please", "pls", "kindly",
    "sir", "madam", "ok", "okay",
}


def names_only_a_channel(message: str) -> bool:
    """True when the message is a channel's name and nothing else.

    Typo-tolerant on the longer words ("common", "centre", "registrar") with
    the ordinary edit-distance rule (`is_token_typo_match`'s own budget
    already leaves the 3-letter codes CSC/SRO exact-only). Without this,
    replying "ommon Service Centre" to the assistant's own "CSC (Common
    Service Centre)" prompt -- a leading letter dropped, most likely by a
    partial copy-paste of that exact reply -- matched no listing word and no
    exact channel token, and asked for an application number.
    """
    tokens = [t for t in extract_tokens(message or "")
              if t not in _CHANNEL_ONLY_FILLER]
    if not tokens or len(tokens) > 4:
        return False

    def _close_enough(t: str) -> bool:
        if t in _CHANNEL_NAME_TOKENS:
            return True
        if any(is_token_typo_match(t, w) for w in _CHANNEL_NAME_TOKENS):
            return True
        # A dropped LEADING letter is a real edit but fails the typo
        # matcher's own first-character guard (the same guard that stops
        # "isd"->"nisd"), which a generic single-character truncation like
        # "ommon" for "common" needs to cross. Safe only inside this narrow,
        # closed 16-word vocabulary, where the outer <=4-token, all-must-
        # match gate already bounds the false-positive surface.
        return len(t) >= 4 and any(w[1:] == t for w in _CHANNEL_NAME_TOKENS)

    return all(_close_enough(t) for t in tokens)


def _channel_positions(message: str) -> List[Tuple[int, str]]:
    """(position, channel) for every channel this message names, message order."""
    msg = (message or "").lower()
    hits: List[Tuple[int, str]] = []
    matched = set()
    for channel, patterns, ta_needles in _CHANNEL_VOCAB:
        best = None
        for pattern in patterns:
            m = re.search(rf"\b(?:{pattern})\b", msg)
            if m and (best is None or m.start() < best):
                best = m.start()
        for needle in ta_needles:
            idx = msg.find(needle)
            if idx != -1 and (best is None or idx < best):
                best = idx
        if best is not None:
            matched.add(channel)
            hits.append((best, channel))

    # Only for a channel the exact vocabulary missed, so a correctly spelled
    # message never takes this path and nothing it already matched can move.
    for channel, words in _CHANNEL_FUZZY:
        if channel in matched:
            continue
        for token in extract_tokens(msg):
            if token in _CHANNEL_FUZZY_NEVER:
                continue
            # A dropped LEADING letter is a real edit but
            # fails `is_token_typo_match`'s own first-character guard, same as
            # `names_only_a_channel._close_enough` exists to cross for a bare
            # channel name -- this is the same truncation for "show <channel>
            # applications" phrasing.
            # A QWERTY-adjacent first letter is
            # a fat-finger substitution, not a random edit -- see
            # `is_qwerty_first_letter_typo`'s own docstring for why it is a
            # separate, narrower check rather than a loosened core guard.
            if (any(is_token_typo_match(token, w)
                    or (len(token) >= 4 and w[1:] == token)
                    or is_qwerty_first_letter_typo(token, w)
                    for w in words)):
                idx = msg.find(token)
                hits.append((idx if idx != -1 else len(msg), channel))
                break
    return sorted(hits)


def extract_submission_channels(message: str) -> List[str]:
    """Every submission channel the officer named, in the order they named them.

    One channel, several, or all three -- "applications from CSC", "CSC and
    Sub-Registrar", "from all channels" are the same kind of question, differing
    only in how much of the register they scope. Returns [] when no channel is
    named, which leaves the ordinary desk queue in place.

    Naming several channels is a UNION, never a comparison: "CSC and SRO
    applications" asks to see both sets in one list. "CSC vs SRO" is a count
    each way, and is claimed earlier by parse_comparison_query.
    """
    msg = (message or "").lower()
    named = [channel for _pos, channel in _channel_positions(message)]

    # "all channels" / "every source" -- the whole register, said as a scope.
    # It is a scope rather than None precisely because a scope suppresses the
    # current-stage pin: left as None, "show applications from all channels"
    # answered with the handful still sitting on the officer's desk today.
    if not named and (_ALL_CHANNELS_RE.search(msg)
                      or any(n in msg for n in _ALL_CHANNELS_TA)):
        return list(SUBMISSION_CHANNELS)

    # A bare "citizen" is a channel only when the sentence is about where an
    # application came from -- "citizen access number" (the CAN) is not.
    #
    # "citizen" is 7 letters, so the ordinary budget is 1 edit -- too tight
    # for "citezin" (a transposed "ie"/"ei" pair 2 edits away), which named no
    # channel at all. max_edits=2 is safe here precisely because this is a
    # long, specific word: nothing else in the domain vocabulary sits within
    # 2 edits of it (checked against application/applications/citizenship/
    # sitting/listing/kitchen/citation), unlike the 3-letter codes CSC/SRO,
    # where the same budget catches half the English dictionary.
    #
    # "sitizen" / "sitisen" are how a Tanglish typist spells the SOUND of
    # "citizen" -- the English "c" here is pronounced /s/, so the first
    # letter itself drifts, which the typo matcher's own first-character
    # guard otherwise refuses to cross (the same guard that stops
    # "late"->"date"). Matched as a second, phonetic target rather than
    # loosening that guard globally -- checked clean against survey/submit/
    # stage/status/sub/sir/show/site/signature and the rest of the s-prefixed
    # domain vocabulary at the same budget.
    # "itizen" is a dropped LEADING letter -- a real edit that the typo
    # matcher's own first-character
    # guard refuses to cross regardless of max_edits, so it needs the same
    # narrow w[1:]==token allowance `_channel_positions`'s fuzzy loop uses.
    _tokens = extract_tokens(msg)
    _says_citizen = bool(re.search(r"\bcitizens?\b", msg)) or any(
        is_token_typo_match(tok, "citizen", max_edits=2)
        or is_token_typo_match(tok, "citizens", max_edits=2)
        or is_token_typo_match(tok, "sitizen", max_edits=2)
        or (len(tok) >= 4 and tok in ("itizen", "itizens"))
        or is_qwerty_first_letter_typo(tok, "citizen")
        or is_qwerty_first_letter_typo(tok, "citizens")
        for tok in _tokens)
    if "citizen" not in named and _says_citizen \
            and not re.search(r"\bcitizen\s+access\s+(?:number|no)\b", msg) \
            and (names_only_a_channel(message)
                 or any(w in msg for w in ("application", "applications", "submitted",
                                       "submit", "channel", "source", "from",
                                       "filed", "apps",
                                       # A bare imperative is a channel request
                                       # too: "show citizen" / "list citizen"
                                       # names no noun but asks for nothing else.
                                       "show", "list", "display", "give", "view"))):
        named.append("citizen")

    # An IGRS question IS a Sub-Registrar question. Only a Sub-Registrar
    # referral carries an igrs_form6_number -- 93 of 93, and none of the CSC or
    # citizen files -- so "which of my applications have an IGRS number" is
    # asking for that channel. Without this it fell through to the officer's
    # open desk queue and answered "all 1" to an officer holding 20 such files,
    # because the current-stage pin applies to an unscoped listing.
    #
    # Skipped when the message names an application: "does 2022/0154/28/000156
    # have an IGRS number" is a question about one file, not a scope, and
    # turning it into a channel filter would answer the wrong question. Skipped
    # too when a channel is already named, so "do the CSC ones have an IGRS
    # number" stays a question about the CSC set.
    if (not named
            and any(kw in msg for kw in ("igrs", "form 6", "form6", "படிவம் 6"))
            and not extract_application_number(message)):
        # "without an IGRS number" / "no IGRS number" / "not having an IGRS
        # number" is the OTHER two channels, not a plain substring match on
        # "igrs" landing on sub_registrar again -- only a Sub-Registrar
        # referral ever carries one, so asking for applications WITHOUT it
        # named the one channel that always has it, the literal opposite of
        # the question. "applications without igrs number" was answered
        # with the Sub-Registrar list.
        if re.search(r"\b(?:without|no|not\s+having|excluding|except)\b", msg):
            named.extend(c for c in SUBMISSION_CHANNELS if c != "sub_registrar")
        else:
            named.append("sub_registrar")

    # "neither CSC nor Sub-Registrar" named both channels the ordinary way --
    # the channel matcher has no idea "neither"/"nor" sit around them -- so
    # "applications neither from csc nor sub registrar" returned exactly the
    # two channels the officer excluded, the literal opposite of the
    # question (only 3 channels exist, so excluding 2 unambiguously means
    # the third). Inverted here rather than in the matcher itself, so every
    # existing positive-channel call site is untouched.
    if named and re.search(r"\bneither\b", msg) and re.search(r"\bnor\b", msg):
        named = [c for c in SUBMISSION_CHANNELS if c not in named]

    seen, ordered = set(), []
    for channel in named:
        if channel not in seen:
            seen.add(channel)
            ordered.append(channel)
    return ordered


# The short codes CSC/SRO get zero typo budget in the ordinary fuzzy match --
# `_max_edits_for` deliberately blocks it, the same rule that stops `isd`
# absorbing `nisd`. So "seo anupuna application shw panu" (Tanglish for "sro
# anuppina application-a kaattu") named no channel at all, and the message
# fell to the agent/LLM layer with nothing for it to look up -- which either
# hallucinated a channel or, on this test, hung for the full 90s timeout with
# no answer at all.
#
# An edit-distance budget loose enough to catch "scs" for "csc" (their real
# minimum distance is 2 -- they are not even anagrams, csc has two c's, scs
# has two s's) is also loose enough to catch ordinary English: "see", "say",
# "she", "set", "sir", "six", "son", "sun", "sit", "sat", "sky", "sea" all sit
# within 2 edits of "sro". A numeric threshold cannot separate a channel typo
# from a real word here, so this uses a curated list of the typos officers
# actually type instead -- just for the 3-letter codes that list
# deliberately excludes.
_CHANNEL_CLARIFY_VARIANTS = {
    "sro": ("seo", "sro's", "sto", "srp", "aro", "dro", "sr0"),
    "csc": ("scs", "ccs", "cxc", "vsc", "cdc", "c5c"),
}
_CHANNEL_LISTING_SHAPE_WORDS = (
    "application", "applications", "aplication", "aplications", "applction",
    "applictions", "apps", "app", "submitted", "submit", "channel", "source",
    "from", "filed", "list", "display", "give", "view", "panu",
    "pannu", "kaatu", "kaattu", "விண்ணப்ப",
)


def ambiguous_channel_clarification(message: str) -> bool:
    """True when the message reads as a channel request but names it as one
    of the known near-miss spellings of CSC/SRO -- close enough to be worth
    asking about, rather than guessing or falling through to the LLM.
    """
    msg = (message or "").lower()
    if not any(w in msg for w in _CHANNEL_LISTING_SHAPE_WORDS):
        return False
    # A message that already names a channel cleanly has nothing to clarify.
    if _channel_positions(message):
        return False
    tokens = set(extract_tokens(msg))
    return any(v in tokens for variants in _CHANNEL_CLARIFY_VARIANTS.values()
               for v in variants)


def extract_submission_channel(message: str) -> Optional[str]:
    """The single submission channel the user is asking about, or None.

    The first one named -- for the callers that answer about one file or one
    channel. A listing scope reads `extract_submission_channels()` instead, so
    that "CSC and Sub-Registrar" is not silently answered as CSC alone.

    Returns:
        'CSC'           — Common Service Center
        'citizen'       — Citizen self-submitted / portal / revenue camp
        'sub_registrar' — Sub-Registrar (IGRS) referral
        None            — no channel mentioned
    """
    channels = extract_submission_channels(message)
    return channels[0] if channels else None


def extract_application_number(message: str) -> Optional[str]:
    """Extract application number (e.g. 2026/0153/31/000001, 2026/31/0153/000001, ISD/W1/2024/0001, APP-2024-000015,
    or any YYYY/A/B/N format the user typed)."""
    cleaned = clean_message(message)
    # 1. Strict known-format match (known service codes)
    app_match = re.search(
        r'\b\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+\b|\b\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+\b|\b(?:ISD|NISD|MERGE)/\w+/\d+/\d+\b|\bAPP-\d+-\d+\b',
        cleaned, re.IGNORECASE
    )
    if app_match:
        return app_match.group(0).upper()
    # 2. Broader fallback: any YYYY/A/B/NNNN — so non-standard numbers still reach the DB
    broad_match = re.search(r'\b(20\d{2}/[\w]+/[\w]+/\d+)\b', cleaned)
    if broad_match:
        return broad_match.group(0).upper()
    return None


def extract_application_numbers(message: str) -> List[str]:
    """Extract ALL application numbers from a message (supports multi-app queries like 'show details for A and B').
    Returns a list of unique application numbers in the order they appear."""
    cleaned = clean_message(message)
    # Strict known-format matches first
    matches = re.findall(
        r'\b\d{4}/(?:0153|0154|0155)/\d{1,3}/\d+\b|\b\d{4}/\d{1,3}/(?:0153|0154|0155)/\d+\b|\b(?:ISD|NISD|MERGE)/\w+/\d+/\d+\b|\bAPP-\d+-\d+\b',
        cleaned, re.IGNORECASE
    )
    # Broader fallback: any YYYY/A/B/NNNN not already captured
    if not matches:
        matches = re.findall(r'\b20\d{2}/[\w]+/[\w]+/\d+\b', cleaned)
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


# `\b` cannot close on a Tamil month name: every one of them ends in a
# dependent vowel sign or a virama-marked consonant (மார்ச், ஏப்ரல், மே, ஜூன்,
# ...), neither of which is `\w`, so `\bமார்ச்\b` matches NOTHING -- not even
# "மார்ச்" on its own. Every Tamil month was therefore silently invisible to
# both `_compare_periods` and `extract_month_scopes`: "மார்ச் மற்றும் ஏப்ரல்
# விண்ணப்பங்களை ஒப்பிடு" ("compare March and April applications") found zero
# periods and fell through to the LLM, which then had to invent the
# comparison. `chatbot.py` already carries the fix for this exact trap
# (`_TA_NB`/`_TA_NA`, a lookaround against the Tamil Unicode block instead of
# `\b`) for `யார்`/`எப்போது`; the same pair is used here for month names.
_TA_NB, _TA_NA = r'(?<![஀-௿])', r'(?![஀-௿])'


def _word_bounds(name: str) -> Tuple[str, str]:
    """(left, right) boundary assertions for a whole-word match on `name`.

    `\\b` for an ASCII name; the Tamil-block lookaround for anything else, since
    `\\b` cannot close next to a Tamil vowel sign or virama (see note above).
    """
    if name.isascii():
        return r"\b", r"\b"
    return _TA_NB, _TA_NA


def _month_name_re(name: str) -> str:
    """Whole-word pattern for a month name, Tamil-safe (see note above)."""
    left, right = _word_bounds(name)
    return f"{left}{re.escape(name)}{right}"


# Month names in every form the officers type them, shared by the range parser
# and the multi-month scope parser below.
_MONTH_NAME_MAP = {
    "january": 1, "jan": 1, "ஜனவரி": 1,
    "february": 2, "feb": 2, "பிப்ரவரி": 2,
    "march": 3, "mar": 3, "மார்ச்": 3,
    "april": 4, "apr": 4, "ஏப்ரல்": 4,
    "may": 5, "மே": 5,
    "june": 6, "jun": 6, "ஜூன்": 6,
    "july": 7, "jul": 7, "ஜூலை": 7,
    "august": 8, "aug": 8, "ஆகஸ்ட்": 8,
    "september": 9, "sep": 9, "sept": 9, "செப்டம்பர்": 9,
    "october": 10, "oct": 10, "அக்டோபர்": 10,
    "november": 11, "nov": 11, "நவம்பர்": 11,
    "december": 12, "dec": 12, "டிசம்பர்": 12,
}

# Tamil Nadu seasons, the same spans the date-range parser uses: Summer
# Apr-Jun, Monsoon Oct-Nov, Winter Dec-Feb (which crosses the year boundary).
_SEASON_MONTHS = {
    "summer": (4, 5, 6),
    "monsoon": (10, 11),
    "winter": (12, 1, 2),
}
_SEASON_WORDS = {
    "summer": ("summer", "கோடை", "கோடைக்கால"),
    "monsoon": ("monsoon", "rainy season", "rainy", "மழைக்கால"),
    "winter": ("winter", "குளிர்கால", "மாரிக்கால"),
}


def season_in_text(text: str) -> List[str]:
    """Season names present in the text, in _SEASON_MONTHS order."""
    lowered = (text or "").lower()
    return [name for name, words in _SEASON_WORDS.items()
            if any(w in lowered for w in words)]


def _season_month_years(season: str, stated_year: Optional[int],
                        today: date) -> List[Tuple[int, int]]:
    """
    (year, month) pairs for a season. Without a stated year, resolve to the most
    recent occurrence that has already begun -- asked in August 2026, "winter"
    is December 2025-February 2026, not the December still ahead. A report of
    applications already submitted can only ever be about a season that started.
    """
    months = _SEASON_MONTHS[season]
    if stated_year is not None:
        base = stated_year
    else:
        base = today.year
        if date(base, months[0], 1) > today:
            base -= 1
    pairs = []
    year = base
    for idx, month in enumerate(months):
        # Winter rolls into the next year once it passes December.
        if idx > 0 and month < months[idx - 1]:
            year += 1
        pairs.append((year, month))
    return pairs


# "from June to August", "between March and May" name the two ends of one span.
# "June and July", "March, April and May" name the months themselves. Only the
# first kind may be collapsed into a single range — collapsing the second turns
# "March and May" into March-April-May and hands the officer a month they never
# asked for.
_RANGE_CONNECTOR_RE = re.compile(
    r"\bfrom\b[^.]*?\b(?:to|till|until|upto|up to|through|thru)\b"
    r"|\bbetween\b[^.]*?\band\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s*[-–—]\s*"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*(?:\s+\d{4})?\s+"
    r"(?:to|till|until|upto|through|thru)\s+"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b"
    r"|முதல்.*?வரை",
    re.IGNORECASE,
)

# Relative month phrases and how many months back they sit from the current one.
# Longest first: "month before last" must not be eaten by "last month".
_RELATIVE_MONTH_PHRASES = [
    (r"month before (?:the )?(?:last|previous|prev)\b", 2),
    (r"month before that\b", 2),
    (r"month before (?:this|the current)\b", 1),
    (r"(?:prev|previous)\s+(?:prev|previous)\s+month\b", 2),
    (r"(?:two|2)\s+months?\s+(?:ago|back|before|earlier)\b", 2),
    (r"(?:three|3)\s+months?\s+(?:ago|back|before|earlier)\b", 3),
    (r"(?:one|1)\s+months?\s+(?:ago|back|before|earlier)\b", 1),
    (r"(?:last|previous|prev|past|preceding)\s+month\b", 1),
    (r"(?:this|current|present)\s+month\b", 0),
    (r"கடந்த\s*கடந்த\s*மாதம்", 2),
    (r"(?:கடந்த|சென்ற|முந்தைய)\s*மாதம்", 1),
    (r"இந்த\s*மாதம்", 0),
]


def _month_bounds(year: int, month: int) -> Tuple[date, date]:
    """First and last day of the given calendar month."""
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _shift_month(anchor_date: date, months_back: int) -> Tuple[int, int]:
    """(year, month) sitting `months_back` months before anchor_date."""
    total = anchor_date.year * 12 + (anchor_date.month - 1) - months_back
    return total // 12, total % 12 + 1


def resolve_month_year(month: int, today: Optional[date] = None) -> int:
    """
    Year for a month named without one ("applications in June").

    A monthly report covers one month of one year, so resolve to the most
    recent occurrence that has already started: asked in August 2026, "June"
    is June 2026 and "September" is September 2025.
    """
    today = today or date.today()
    return today.year if month <= today.month else today.year - 1


def extract_month_scopes(message: str) -> List[Tuple[date, date]]:
    """
    Months named as a union rather than a span — "June and July", "March and
    May", "last month and the month before that", "both June & July".

    Returns one (start, end) segment per contiguous run of requested months,
    sorted, with adjacent months merged (June + July → a single June 1 – July 31
    segment). Returns [] when the message names fewer than two distinct months
    or phrases them as a range ("from June to August"), which
    :func:`extract_date_range` already resolves on its own.
    """
    # Typos in the relative phrases ('lastt month') are repaired first: the
    # phrase lists below are literal string tests, so one stray letter used to
    # drop the whole date scope silently.
    cleaned = normalize_relative_date_tokens(clean_message(message).lower())
    today = date.today()

    # An explicit span, a day-level date or a year range is somebody else's job.
    if _RANGE_CONNECTOR_RE.search(cleaned):
        return []
    if re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{4}", cleaned):
        return []
    if re.search(r"\b\d{1,2}(?:st|nd|rd|th)\b", cleaned):
        return []

    months: set = set()

    # Relative phrases, longest first, each consumed so it cannot match twice.
    scan = cleaned
    for pattern, back in _RELATIVE_MONTH_PHRASES:
        for _m in re.finditer(pattern, scan):
            months.add(_shift_month(today, back))
        scan = re.sub(pattern, " ", scan)

    # Seasons resolve to their months, so "summer and winter" unions cleanly
    # with anything else named alongside them.
    _stated_year = re.search(r"\b(20\d{2})\b", cleaned)
    for _season in season_in_text(cleaned):
        months.update(_season_month_years(
            _season, int(_stated_year.group(1)) if _stated_year else None, today))

    # Named months, each with its own year when one is written next to it.
    for name, num in _MONTH_NAME_MAP.items():
        for m in re.finditer(_month_name_re(name) + r"(?:\s+(\d{4}))?", cleaned):
            # A bare day next to the name ("June 25") is a date, not a month.
            if re.match(r"\s+\d{1,2}\b(?!\d)", cleaned[m.end():]):
                continue
            year = int(m.group(1)) if m.group(1) else resolve_month_year(num, today)
            months.add((year, num))

    if len(months) < 2:
        return []

    ordered = sorted(months)
    segments: List[Tuple[date, date]] = []
    seg_start, seg_end = _month_bounds(*ordered[0])
    for year, month in ordered[1:]:
        m_start, m_end = _month_bounds(year, month)
        # Contiguous months collapse into one segment.
        if m_start == seg_end + timedelta(days=1):
            seg_end = m_end
        else:
            segments.append((seg_start, seg_end))
            seg_start, seg_end = m_start, m_end
    segments.append((seg_start, seg_end))
    return segments


def format_month_scopes(segments: List[Tuple[date, date]]) -> str:
    """'June & July 2026', 'March 2026 & May 2026' — a label for the segments."""
    parts = []
    for start_d, end_d in segments:
        if (start_d.year, start_d.month) == (end_d.year, end_d.month):
            parts.append(f"{_MONTH_LABELS[start_d.month]} {start_d.year}")
        elif start_d.year == end_d.year:
            parts.append(f"{_MONTH_LABELS[start_d.month]}\u2013{_MONTH_LABELS[end_d.month]} {end_d.year}")
        else:
            parts.append(f"{_MONTH_LABELS[start_d.month]} {start_d.year}\u2013"
                         f"{_MONTH_LABELS[end_d.month]} {end_d.year}")
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " & " + parts[-1]


# ─────────────────────────────────────────────────────────────────────────────
# Bare date-scope follow-ups ("last month", "what about June?")
# ─────────────────────────────────────────────────────────────────────────────
# An officer who asks "how many applications did I approve this week" and then
# types just "last month" is re-scoping the SAME question. On its own that
# message carries no intent at all, so it used to fall through to
# general_query and the LLM answered "I don't have information about last
# month". These two helpers let the caller recognise the shape and re-run the
# previous question against the new period.
_DATE_SCOPE_PHRASE_RE = re.compile(
    # An optional leading connector is eaten with the phrase, so stripping
    # "in June" out of a sentence does not leave a dangling "in".
    r"(?:\b(?:in|on|during|for|since|from|between|by|till|until|upto|up\s+to|within)\s+)?"
    r"(?:"
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4}\b"
    r"|\bday\s+(?:before|after)\s+(?:yesterday|tomorrow)\b"
    r"|\b(?:today|tonight|yesterday|tomorrow)\b"
    r"|\bmonth\s+before\s+(?:the\s+)?(?:last|previous|prev|that|this)\b"
    r"|\b(?:this|last|past|previous|prev|next|current|preceding|coming|upcoming)"
    r"\s+(?:week|month|year|quarter|fortnight)\b"
    r"|\b(?:last|past|next|previous|coming)\s+\d{1,3}\s*(?:days?|weeks?|months?|years?)\b"
    r"|\b\d{1,3}\s*(?:days?|weeks?|months?|years?)\s+(?:ago|back|earlier|before)\b"
    r"|\b(?:january|february|march|april|may|june|july|august|september|october"
    r"|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\b"
    r"\s*(?:20\d{2})?"
    r"|\b(?:summer|winter|monsoon|rainy\s+season)\b"
    r"|\b20\d{2}\b"
    r"|(?:இன்று|இன்னிக்கு|இனிக்கு|நேற்று|நேத்து|நாளை|முந்தாநாள்|நாளை\s*மறுநாள்)"
    r"|(?:இந்த|கடந்த|சென்ற|முந்தைய|அடுத்த)\s*(?:வாரம்|மாதம்|ஆண்டு|வருடம்)"
    r")",
    re.IGNORECASE,
)

# Words that carry no question of their own — they only glue a follow-up to the
# turn before it. Anything left over after these are removed means the message
# asked something new, so it is NOT a bare re-scope.
_FOLLOWUP_FILLER = frozenset({
    "and", "or", "ok", "okay", "k", "what", "whats", "about", "how", "hows",
    "then", "also", "please", "pls", "plz", "the", "of", "so", "now",
    "instead", "same", "again", "too", "as", "well", "much", "many",
    "சரி", "என்ன", "பற்றி", "அப்போ", "அப்படியே", "மற்றும்",
})


def strip_date_scope_phrases(text: str) -> str:
    """The message with every date-scope phrase removed, whitespace collapsed."""
    if not text:
        return ""
    stripped = _DATE_SCOPE_PHRASE_RE.sub(" ", normalize_relative_date_tokens(text))
    return re.sub(r"\s{2,}", " ", stripped).strip()


def extract_date_scope_fragment(message: str) -> str:
    """
    Just the period named in the message — "what about June?" → "june".

    Used when a bare follow-up is folded back into the question it re-scopes,
    so the glue words ("what about") do not travel with it.
    """
    cleaned = normalize_relative_date_tokens(clean_message(message or "").lower())
    parts = [m.group(0).strip() for m in _DATE_SCOPE_PHRASE_RE.finditer(cleaned)]
    return " ".join(p for p in parts if p)


def is_bare_date_scope(message: str) -> bool:
    """
    True when the message is nothing but a period — "last month", "what about
    June?", "2024", "lastt month" — and therefore re-scopes the previous
    question instead of asking a new one.
    """
    if not message:
        return False
    cleaned = normalize_relative_date_tokens(clean_message(message).lower())
    if not cleaned or not _DATE_SCOPE_PHRASE_RE.search(cleaned):
        return False
    residue = _DATE_SCOPE_PHRASE_RE.sub(" ", cleaned)
    for token in extract_tokens(residue):
        if token in _FOLLOWUP_FILLER:
            continue
        # A misspelled month ("jne") survives the literal alternation above.
        if extract_month_from_text(token):
            continue
        return False
    return True


_MONTH_LABELS = ["", "January", "February", "March", "April", "May", "June",
                 "July", "August", "September", "October", "November", "December"]


_BOUND_RE = re.compile(
    r"\b(?P<kw>before|prior\s+to|earlier\s+than|until|till|upto|up\s+to|after|since|later\s+than|post)\s+"
    r"(?:(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+)?(?:(?P<mon>[a-z]{3,9})\s+)?(?P<year>(?:19|20)\d{2})\b")


def _written_period_bound(cleaned: str, today: date):
    """(start, end) for a one-sided written period, or None when not one."""
    m = _BOUND_RE.search(cleaned)
    if not m:
        return None
    year = int(m.group("year"))
    month = extract_month_from_text(m.group("mon") + " " + m.group("year")) if m.group("mon") else None
    if m.group("mon") and not month:
        return None
    day = int(m.group("day")) if m.group("day") else None
    try:
        if month and day:
            first = last = date(year, month, day)
        elif month:
            first, last = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
        else:
            first, last = date(year, 1, 1), date(year, 12, 31)
    except ValueError:
        return None
    kw = re.sub(r"\s+", " ", m.group("kw"))
    if kw in ("before", "prior to", "earlier than"):
        return None, first - timedelta(days=1)
    if kw in ("until", "till", "upto", "up to"):
        return None, last
    if kw == "since":
        return first, max(today, first)
    return last + timedelta(days=1), max(today, last + timedelta(days=1))


def extract_date_range(message: str) -> Tuple[Optional[date], Optional[date]]:
    """
    Extract start_date and end_date from natural language queries.
    Supports ISO dates (YYYY-MM-DD), standard dates (DD/MM/YYYY, DD-MM-YYYY),
    relative date phrases (this week, next week, this month, next month, next 7/15/30 days),
    and phrases like "between <date1> and <date2>", "from <date1> to <date2>".
    """
    # Typos in the relative phrases ('lastt month') are repaired first: the
    # phrase lists below are literal string tests, so one stray letter used to
    # drop the whole date scope silently.
    cleaned = normalize_relative_date_tokens(clean_message(message).lower())
    today = date.today()

    # 0. Open-ended ranges: "starting from <date>", "since <date>", "<date> onwards"
    # resolve to [date, today]; "before <date>", "until <date>", "up to <date>"
    # resolve to [None, date] so the caller only applies an upper bound. This
    # must run before the generic single/paired date scan below, which would
    # otherwise grab the lone date and collapse it into a single-day range,
    # losing the "since"/"before" direction entirely.
    #
    # Only applies when exactly one date-like token is in the message — "from
    # X to/till Y" already names both ends of a closed range, so treating the
    # second date as absent (open-ended) would silently drop it.
    _all_date_tokens = re.findall(
        r'\d{4}-\d{2}-\d{2}|\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4}', cleaned
    )
    _open_start_re = re.search(
        r'\b(?:starting from|start from|since|from)\s+(\d{4}-\d{2}-\d{2}|\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4})\b'
        r'|\b(\d{4}-\d{2}-\d{2}|\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4})\s+onwards\b',
        cleaned
    )
    if _open_start_re and len(_all_date_tokens) == 1:
        raw = _open_start_re.group(1) or _open_start_re.group(2)
        parsed = None
        try:
            if "-" in raw and len(raw.split("-")[0]) == 4:
                parsed = datetime.strptime(raw, "%Y-%m-%d").date()
            else:
                sep = next(s for s in ["/", "-", "."] if s in raw)
                d_, m_, y_ = (int(p) for p in raw.split(sep))
                parsed = date(y_, m_, d_)
        except (ValueError, StopIteration):
            parsed = None
        if parsed:
            return parsed, today

    _open_end_re = re.search(
        r'\b(?:before|until|up to|upto|till)\s+(\d{4}-\d{2}-\d{2}|\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4})\b',
        cleaned
    )
    if _open_end_re and len(_all_date_tokens) == 1:
        raw = _open_end_re.group(1)
        parsed = None
        try:
            if "-" in raw and len(raw.split("-")[0]) == 4:
                parsed = datetime.strptime(raw, "%Y-%m-%d").date()
            else:
                sep = next(s for s in ["/", "-", "."] if s in raw)
                d_, m_, y_ = (int(p) for p in raw.split(sep))
                parsed = date(y_, m_, d_)
        except (ValueError, StopIteration):
            parsed = None
        if parsed:
            return None, parsed

    # 0b. "before march 2025", "after 1 jan 2025", "since 2025", "until may 2025" --
    # a written-out period on one side. Before this the bound was dropped and the
    # question answered as if the period itself had been named ("before March"
    # listed March).
    if not _all_date_tokens:
        _bound = _written_period_bound(cleaned, today)
        if _bound is not None:
            return _bound

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

    # "the next day" / "next working day" is tomorrow said another way. Checked
    # with the tomorrow forms so a field-visit question scoped to it does not
    # silently widen into every visit on the officer's list.
    if any(w in cleaned for w in ["tomorrow", "tommorrow", "tmrw", "நாளை",
                                  "next day", "nextday", "following day",
                                  "next working day", "next work day"]):
        tmrw = today + timedelta(days=1)
        return tmrw, tmrw

    if any(w in cleaned for w in ["today", "tonight", "இன்று", "இன்னிக்கு", "இனிக்கு"]):
        return today, today

    # "நேத்து" is the colloquial, spoken-Tamil spelling of "yesterday" --
    # distinct from the formal "நேற்று" already handled, and at least as
    # common in what an officer actually types. Missing it meant the whole
    # date scope was silently dropped: "நேத்து எத்தனை விண்ணப்பம் வந்துச்சு"
    # answered with the officer's unscoped desk count, not yesterday's.
    if any(w in cleaned for w in ["yesterday", "நேற்று", "நேத்து"]):
        ystd = today - timedelta(days=1)
        return ystd, ystd

    if any(w in cleaned for w in ["morning", "afternoon", "evening", "காலை", "பிற்பகல்", "மதியம்", "மாலை"]):
        return today, today

    if any(w in cleaned for w in ["this week", "இந்த வாரம்"]):
        start_w = today - timedelta(days=today.weekday())
        end_w = start_w + timedelta(days=6)
        return start_w, end_w

    if any(w in cleaned for w in ["next week", "coming week", "following week",
                                  "அடுத்த வாரம்"]):
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

    if any(w in cleaned for w in [
        "last month", "past month", "previous month", "prev month", "preceding month",
        "கடந்த மாதம்", "சென்ற மாதம்", "முந்தைய மாதம்"
    ]):
        if today.month == 1:
            start_m = date(today.year - 1, 12, 1)
        else:
            start_m = date(today.year, today.month - 1, 1)
        _, last_day = calendar.monthrange(start_m.year, start_m.month)
        end_m = start_m.replace(day=last_day)
        return start_m, end_m

    # "this year" / "last year" / "previous year" -- the month block above has
    # always covered "this month" / "last month", but the year-scale sibling
    # had no branch at all, so "what was approved last year" carried no date
    # scope out of this function and fell through to the LLM with nothing to
    # look up. "yr" is aliased to "year" by normalize_relative_date_tokens
    # above, so it is covered here too.
    if any(w in cleaned for w in ["next year", "coming year", "அடுத்த ஆண்டு", "அடுத்த வருடம்"]):
        return date(today.year + 1, 1, 1), date(today.year + 1, 12, 31)

    if any(w in cleaned for w in ["this year", "current year", "இந்த ஆண்டு", "இந்த வருடம்"]):
        return date(today.year, 1, 1), date(today.year, 12, 31)

    if any(w in cleaned for w in [
        "last year", "past year", "previous year", "prev year", "preceding year",
        "கடந்த ஆண்டு", "சென்ற ஆண்டு", "முந்தைய ஆண்டு", "கடந்த வருடம்", "முந்தைய வருடம்"
    ]):
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)

    # 3b. Seasons (Tamil Nadu convention: Summer = Apr-Jun, Winter = Dec-Feb,
    # Monsoon/Rainy = Oct-Nov). Year defaults to current year unless a year is
    # explicitly mentioned in the message.
    year_in_msg = re.search(r'\b(20\d{2})\b', cleaned)
    season_year = int(year_in_msg.group(1)) if year_in_msg else today.year

    if any(w in cleaned for w in ["summer", "கோடை", "கோடைக்காலம்"]):
        return date(season_year, 4, 1), date(season_year, 6, 30)

    if any(w in cleaned for w in ["winter", "குளிர்காலம்", "மாரிக்காலம்"]):
        # Winter spans across the year boundary (Dec -> Feb). When no year is
        # explicitly given, treat it as "most recent winter" instead of always
        # assuming the current calendar year, so a query in Jan/Feb resolves to
        # the winter that is actually current rather than one that hasn't
        # started yet.
        if not year_in_msg:
            if today.month <= 2:
                start_w = date(today.year - 1, 12, 1)
                end_w = date(today.year, 2, 28 if not calendar.isleap(today.year) else 29)
            else:
                start_w = date(today.year, 12, 1)
                end_w = date(today.year + 1, 2, 28 if not calendar.isleap(today.year + 1) else 29)
        else:
            start_w = date(season_year, 12, 1)
            end_w = date(season_year + 1, 2, 28 if not calendar.isleap(season_year + 1) else 29)
        return start_w, end_w

    if any(w in cleaned for w in ["monsoon", "rainy season", "மழைக்காலம்"]):
        return date(season_year, 10, 1), date(season_year, 11, 30)

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
    
    found_weekdays = []
    for day_name, day_idx in weekday_map.items():
        _l, _r = _word_bounds(day_name)
        # Tamil day names (வெள்ளிக்கிழமை, ...) end in a vowel sign/virama, so
        # `\b` never closed -- same trap as the month names above.
        for m in re.finditer(_l + r'(?:on\s+|this\s+|next\s+|last\s+|past\s+)?' + re.escape(day_name) + _r, cleaned):
            context = cleaned[max(0, m.start()-15):m.end()]
            is_last = bool(re.search(r'\b(?:last|past|முந்தைய|கடந்த|சென்ற)\b', context))
            is_next = bool(re.search(r'\b(?:next|coming|அடுத்த)\b', context))
            
            target_date = None
            month_match = None
            year_match = None
            for m_name, m_num in month_name_map.items():
                match = re.search(_month_name_re(m_name) + r'(?:\s+(\d{4}))?', cleaned)
                if match:
                    month_match = m_num
                    year_match = int(match.group(1)) if match.group(1) else today.year
                    break
            
            if month_match:
                d = date(year_match, month_match, 1)
                days_ahead = (day_idx - d.weekday()) % 7
                target_date = d + timedelta(days=days_ahead)
            else:
                if is_last:
                    days_behind = (today.weekday() - day_idx) % 7
                    if days_behind == 0: days_behind = 7
                    target_date = today - timedelta(days=days_behind)
                elif is_next:
                    days_ahead = (day_idx - today.weekday()) % 7
                    if days_ahead == 0: days_ahead = 7
                    target_date = today + timedelta(days=days_ahead)
                else:
                    days_behind = (today.weekday() - day_idx) % 7
                    target_date = today - timedelta(days=days_behind)
            
            found_weekdays.append(target_date)

    if len(found_weekdays) >= 2:
        found_weekdays.sort()
        return found_weekdays[0], found_weekdays[-1]
    elif len(found_weekdays) == 1:
        return found_weekdays[0], found_weekdays[0]

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
    # Collect *every* written-out date, not just the first one. Returning on the
    # first match read "between June 1st and June 30th" as the single day June
    # 1st and silently dropped the end of the range -- the ISO and DD/MM/YYYY
    # branches above already gather both ends, and this one has to as well.
    found = []                # (position, month, day, year or None)
    for m_name, m_num in month_name_map.items():
        # The boundary next to the month name has to be Tamil-safe too (see
        # the note above _month_name_re): "25 ஆகஸ்ட்" never matched with a
        # trailing `\b` because ஆகஸ்ட் ends in a virama-marked consonant.
        _ml, _mr = _word_bounds(m_name)
        patterns = (
            r'\b(\d{1,2})(?:st|nd|rd|th)?\s+' + _ml + re.escape(m_name) + r'(?:\s+(\d{4}))?' + _mr,
            _ml + re.escape(m_name) + _mr + r'\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?\b',
        )
        for pattern in patterns:
            for m in re.finditer(pattern, cleaned):
                found.append((m.start(), m_num, int(m.group(1)),
                              int(m.group(2)) if m.group(2) else None))
    if found:
        # "from 25th July to 31st July 2025" states the year once, at the end.
        # Defaulting the yearless half to the current year would build a range
        # running backwards from 2025 to 2026, so a stated year carries.
        stated = [y for _, _, _, y in found if y]
        default_year = stated[0] if stated else today.year
        named_dates = []
        for _pos, m_num, day_val, year_val in found:
            try:
                named_dates.append(date(year_val or default_year, m_num, day_val))
            except ValueError:
                pass
        if len(named_dates) >= 2:
            return min(named_dates), max(named_dates)
        if len(named_dates) == 1:
            return named_dates[0], named_dates[0]

    # Whole-month ranges with no day in them -- "between June 2026 and August
    # 2026", "from January to March". A single month is left alone: the month /
    # year filters handle "applications in June 2026" on their own, and turning
    # it into a range here would take that path away from them.
    bare_months = []
    for m_name, m_num in month_name_map.items():
        for m in re.finditer(_month_name_re(m_name) + r'(?:\s+(\d{4}))?', cleaned):
            year_val = int(m.group(1)) if m.group(1) else None
            bare_months.append((m.start(), m_num, year_val))
    if len(bare_months) >= 2:
        bare_months.sort()
        # A year given on either end applies to both when the other has none.
        stated = [y for _, _, y in bare_months if y]
        first_pos, first_month, first_year = bare_months[0]
        last_pos, last_month, last_year = bare_months[-1]
        first_year = first_year or (stated[0] if stated else today.year)
        last_year = last_year or (stated[-1] if stated else today.year)
        start_m = date(first_year, first_month, 1)
        _, last_day = calendar.monthrange(last_year, last_month)
        end_m = date(last_year, last_month, last_day)
        if start_m <= end_m:
            return start_m, end_m

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

