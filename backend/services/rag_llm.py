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
from backend.services.pgvector_store import similarity_search
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


async def call_llama(prompt: str) -> str:
    """Call Llama model via Ollama (non-streaming)."""
    try:
        logger.info("Calling Ollama LLM...")
        async with llm_gate.llm_slot():
            response = await asyncio.wait_for(
                _llm().ainvoke(prompt), timeout=settings.LLM_TIMEOUT_SECONDS)
        response_text = response.content if hasattr(response, "content") else str(response)
        response_text = response_text.strip()
        logger.info(f"LLM response generated ({len(response_text)} chars)")
        return response_text
    except llm_gate.LLMBusy:
        return _BUSY_TEXT
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
        # The slot is held for the whole stream and released when the stream ends, stalls, or
        # the client goes away (cancellation); guarded_stream closes the connection either way.
        async with llm_gate.llm_slot():
            async for chunk in llm_gate.guarded_stream(lambda: _llm().astream(prompt)):
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
