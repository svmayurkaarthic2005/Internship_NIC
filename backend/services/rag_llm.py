"""
LLM transport and pgvector retrieval for the SIS chatbot.

Pulled out of rag.py (which still owns intent parsing, prompt assembly and
entity extraction) because this piece has no dependency on any of that: it
only talks to Ollama and to pgvector_store.similarity_search(). rag.py keeps
re-exporting these names so `from backend.services.rag import call_llama`
(used throughout backend.services.chatbot and the test suites) is unchanged.
"""

from typing import Dict, Any, Optional, Tuple, List
import asyncio

from langchain_ollama import ChatOllama

from backend.config import settings
from backend.services import llm_gate
from backend.services.pgvector_store import similarity_search, hybrid_search
from backend.services.reranker import format_citation
from backend.services.security import sanitize_prompt_input
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def _llm():
    """Lazy back-reference to backend.services.rag's `llm` attribute.

    Tests swap the Ollama client by assigning `rag.llm = <fake>` (e.g.
    test_llm_gate.py); a bare `llm.ainvoke(...)` here would read THIS
    module's own global instead and miss the swap. Same pattern as
    chat_routing.py's `_cb()`.
    """
    from backend.services import rag
    return rag.llm


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
    num_ctx=settings.LLM_NUM_CTX,
    # Give up on a dead connection instead of waiting for ever.
    client_kwargs={"timeout": settings.LLM_HTTP_READ_TIMEOUT},
)

_BUSY_TEXT = ("The assistant is busy answering other questions right now. "
              "Please try again in a moment.")
_STALLED_TEXT = ("The model stopped responding, so this reply was cut short. "
                 "Please ask again.")


# ─────────────────────────────────────────────────────────────────────────────
# get_rag_context — improved filter-failure logging
# ─────────────────────────────────────────────────────────────────────────────
def get_rag_evidence(
    query: str,
    language: str = "en",
    n_results: int = 5,
    where_filter: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Retrieve top relevant, deduplicated, and reranked document passages.
    Uses hybrid search (vector + lexical + RRF + reranking) when enabled.
    """
    if not query or not query.strip():
        return []

    target_filter = dict(where_filter) if where_filter else None
    if not target_filter:
        if language == "ta":
            target_filter = {"language": "tamil"}
        elif language == "en":
            target_filter = {"language": "english"}
        # tanglish has no filter (searches across both)

    # 1. Try Advanced Hybrid Retrieval
    if getattr(settings, "RAG_HYBRID_ENABLED", True):
        try:
            hits = hybrid_search(
                query=query,
                n_results=n_results,
                where_filter=target_filter,
                language=language,
            )
            if hits:
                return hits
        except Exception as exc:
            logger.warning(f"get_rag_evidence: hybrid_search failed ({exc}), falling back to similarity_search")

    # 2. Fallback to Vector / Lexical similarity_search
    try:
        if target_filter:
            filtered = similarity_search(query, n_results=n_results, where_filter=target_filter)
            unfiltered = similarity_search(query, n_results=n_results)
            seen, pool = set(), []
            for r in filtered + unfiltered:
                key = (r.get("content") or "")[:80]
                if key not in seen:
                    seen.add(key)
                    pool.append(r)
            pool.sort(key=lambda r: r.get("distance", float("inf")))
            return pool[:n_results]
        return similarity_search(query, n_results=n_results)
    except Exception as exc:
        logger.error(f"get_rag_evidence: similarity_search fallback failed: {exc}")
        return []


async def get_rag_evidence_async(
    query: str,
    language: str = "en",
    n_results: int = 5,
    where_filter: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Async wrapper for get_rag_evidence."""
    return await asyncio.to_thread(get_rag_evidence, query, language, n_results, where_filter)


def procedural_refusal_response(language: str = "en") -> str:
    """Deterministic, transparent refusal when no authoritative documents meet relevance threshold."""
    if language in ("ta", "tanglish"):
        return (
            "கிடைக்கக்கூடிய அதிகாரப்பூர்வ அரசு கையேடுகளில் இந்த குறிப்பிட்ட நடைமுறைக்கான "
            "விவரங்கள் இல்லை. தயவுசெய்து சம்பந்தப்பட்ட அரசாணை (G.O.) அல்லது வட்டாட்சியரை (Tahsildar) அணுகவும்."
        )
    return (
        "I do not have authoritative procedural guidance on this specific query in the "
        "available government manuals. Please refer to the relevant Government Order or consult the Tahsildar."
    )


def build_grounded_rag_prompt(
    query: str,
    evidence_passages: List[Dict[str, Any]],
    language: str = "en",
) -> str:
    """
    Build a strictly grounded prompt for procedural revenue questions.
    Instructs the LLM to only answer from the provided verified passages and include citations.
    """
    lang_inst = (
        "CRITICAL: You MUST respond in English language only."
        if language == "en" else
        "CRITICAL: You MUST respond in natural Tamil language written in Tamil script only. Retain necessary English technical terms, acronyms, and official identifiers (such as ISD, NISD, 0154, 0153) in Latin script."
    )
    passages_text = []
    for idx, passage in enumerate(evidence_passages, 1):
        citation = passage.get("citation") or format_citation(passage.get("metadata", {}))
        passages_text.append(f"Passage {idx} {citation}:\n{passage.get('content', '').strip()}\n")
    context_str = "\n---\n".join(passages_text)

    system_instruction = f"""{lang_inst}

You are an expert AI assistant for Sub Inspector Surveyor (SIS) officers of Tamil Nadu.
You are answering an official procedural/knowledge question.

STRICT GROUNDING RULES:
1. Answer the question STRICTLY and ONLY using the verified government document passages provided below.
2. Cite the source document and section for your factual answer (e.g., "[Source: Workflow Guide, Section: ISD Workflow]").
3. DO NOT hallucinate, invent, or speculate on any fee, timeline (SLA), workflow step, approving authority, or legal regulation not present in the passages.
4. If the provided document passages do not contain the answer, state clearly: "The available official manuals do not provide specific guidance on this query. Please consult the Tahsildar or refer to the relevant Government Order."
5. Never invent application numbers, survey records, or applicant data. RAG is only for procedural rules, not database records."""

    return f"""{system_instruction}

VERIFIED DOCUMENT EXCERPTS:
{context_str}

USER QUESTION:
{query}

GROUNDED ASSISTANT RESPONSE:"""


def get_rag_context(query: str, language: str = "en", n_results: int = 5) -> str:
    """
    Retrieve relevant context from pgvector based on query.
    Formats passages with clear source citations.
    """
    try:
        results = get_rag_evidence(query=query, language=language, n_results=n_results)
        if not results:
            logger.warning(f"No RAG context found for query: '{query[:60]}'")
            return ""

        context_parts = []
        for i, result in enumerate(results, 1):
            content = result.get("content", "")
            metadata = result.get("metadata", {})
            citation = result.get("citation") or format_citation(metadata)
            context_parts.append(f"[Source {i}: {citation}]\n{content}\n")

        context = "\n---\n".join(context_parts)
        logger.info(f"Retrieved {len(results)} hybrid context chunks for query")
        return context

    except Exception as e:
        logger.error(f"Error retrieving RAG context: {e}")
        return ""


def _old_get_rag_context(query: str, language: str = "en", n_results: int = 5) -> str:
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
                # Each call is individually sorted by distance (lower = closer),
                # but concatenating two separately-sorted lists is not a merge --
                # a weak filtered match (distance 0.51) still landed ahead of a
                # strong unfiltered one (distance 0.15) purely by coming from the
                # first list. Dedupe, then sort the combined pool by its own
                # distance so "best of both" is actually the closest n_results.
                seen, pool = set(), []
                for r in filtered + unfiltered:
                    key = (r.get("content") or "")[:80]
                    if key not in seen:
                        seen.add(key)
                        pool.append(r)
                pool.sort(key=lambda r: r.get("distance", float("inf")))
                results = pool[:n_results]
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


async def paraphrase_facts_naturally(fact_text: str, language: str = "en", timeout: float = 45.0) -> str:
    """Rewrite an already fact-checked answer as natural prose, via the LLM.

    For definitional content (service code lookups, workflow explanations)
    the facts -- code, name, fee, SLA -- are never in question; only the
    template around them reads the same every time. This asks the model to
    restate exactly what it is given, in its own words, and explicitly
    forbids adding, dropping or changing any fact, number, code or name. If
    the model is slow, busy, or errors, `fact_text` is returned unchanged --
    a paraphrase failure must never cost correctness or block the turn, and
    the caller still verifies the numbers survived before using the result.
    """
    if not fact_text or not fact_text.strip():
        return fact_text
    lang_note = ("Reply in natural Tamil written in Tamil script. Do not reply in Roman script or English."
                 if language in ("ta", "tanglish") else
                 "Reply in English.")
    prompt = (
        "Rewrite the following facts as a short, natural, conversational answer for a "
        "government survey officer. Do not add, remove, invent, summarise away or change "
        "any fact, number, code, name, fee or date in it -- say exactly what is given, "
        f"just in different words. {lang_note}\n\nFacts:\n{fact_text}"
    )
    try:
        async with llm_gate.llm_slot():
            response = await asyncio.wait_for(_llm().ainvoke(prompt), timeout=timeout)
        text = (response.content if hasattr(response, "content") else str(response)).strip()
        return text or fact_text
    except Exception as e:
        logger.warning(f"paraphrase_facts_naturally failed, using deterministic text: {e}")
        return fact_text


MAX_LLM_RETRIES = 2
INITIAL_RETRY_DELAY = 0.5


async def call_llama(prompt: str) -> str:
    """Call Llama model via Ollama (non-streaming) with bounded retries and prompt sanitization."""
    clean_prompt = sanitize_prompt_input(prompt)
    for attempt in range(MAX_LLM_RETRIES + 1):
        try:
            logger.info(f"Calling Ollama LLM (attempt {attempt + 1}/{MAX_LLM_RETRIES + 1})...")
            async with llm_gate.llm_slot():
                response = await asyncio.wait_for(
                    _llm().ainvoke(clean_prompt), timeout=settings.LLM_TIMEOUT_SECONDS)
            response_text = response.content if hasattr(response, "content") else str(response)
            response_text = response_text.strip()
            logger.info(f"LLM response generated ({len(response_text)} chars)")
            _usage = getattr(response, "usage_metadata", None) or {}
            if _usage:   # duration is recorded by llm_slot; this adds Ollama's token counts
                from backend.services.observability import record_llm
                record_llm(0.0, {"prompt_tokens": int(_usage.get("input_tokens") or 0),
                                 "completion_tokens": int(_usage.get("output_tokens") or 0)})
            return response_text
        except llm_gate.LLMBusy:
            return _BUSY_TEXT
        except asyncio.TimeoutError:
            logger.error(f"LLM call timed out after {settings.LLM_TIMEOUT_SECONDS}s (attempt {attempt + 1})")
            if attempt < MAX_LLM_RETRIES:
                await asyncio.sleep(INITIAL_RETRY_DELAY * (2 ** attempt))
                continue
            return ("This is taking longer than expected to answer. Please try again, "
                    "or narrow the question.")
        except Exception as e:
            logger.error(f"Error calling LLM (attempt {attempt + 1}): {e}")
            err_str = str(e).lower()
            if attempt < MAX_LLM_RETRIES and any(term in err_str for term in ["connect", "timeout", "reset", "temporary", "busy"]):
                await asyncio.sleep(INITIAL_RETRY_DELAY * (2 ** attempt))
                continue
            break

    return "I apologize, but I encountered an error processing your request. Please try again."


async def call_llama_stream(prompt: str):
    """Call Llama model via Ollama with streaming and prompt sanitization."""
    clean_prompt = sanitize_prompt_input(prompt)
    try:
        logger.info("Calling Ollama LLM with streaming...")
        chunk_count = 0
        total_content = ""
        # The slot is held for the whole stream and released when the stream ends, stalls, or
        # the client goes away (cancellation); guarded_stream closes the connection either way.
        async with llm_gate.llm_slot():
            async for chunk in llm_gate.guarded_stream(lambda: _llm().astream(clean_prompt)):
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
    except llm_gate.LLMBusy:
        yield _BUSY_TEXT
    except llm_gate.LLMStalled as e:
        logger.error(f"LLM stream stalled ({e}) after {len(total_content)} chars")
        yield (" " if total_content else "") + _STALLED_TEXT
    except Exception as e:
        logger.error(f"Error in LLM stream: {e}", exc_info=True)
        yield "I apologize, but I encountered an error processing your request. Please try again."
