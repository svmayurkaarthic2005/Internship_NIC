"""
reranker.py — Hybrid Retrieval, Reciprocal Rank Fusion (RRF), and Passage Reranking
=====================================================================================
Combines vector similarity search and PostgreSQL lexical/full-text search for the
Sub Inspector Surveyor (SIS) Chatbot reference knowledge corpus.

Features:
  1. Reciprocal Rank Fusion (RRF) for combining multi-retriever rank lists.
  2. Query normalization with exact domain terminology and Tanglish expansion.
  3. Feature-based passage reranking (exact code match, keyword density, section relevance,
     authoritative manual preference, and recency).
  4. Near-duplicate passage suppression (deduplication over chunk overlap).
  5. Minimum relevance thresholding to prevent hallucination on out-of-domain queries.
  6. Standardized source citation formatting.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.config import settings
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Key Tamil Nadu survey domain terms to preserve across normalization
REVENUE_DOMAIN_TERMS = {
    "isd": "0154",
    "nisd": "0153",
    "0154": "isd",
    "0153": "nisd",
    "patta": "patta",
    "chitta": "chitta",
    "adangal": "adangal",
    "fmb": "fmb",
    "tslr": "tslr",
    "field visit": "field_visit",
    "field inspection": "field_inspection",
    "sub-division": "sub_division",
    "subdivision": "sub_division",
    "joint owner": "joint_owner",
    "joint owners": "joint_owner",
    "sale deed": "sale_deed",
    "encumbrance": "encumbrance",
    "tahsildar": "tahsildar",
    "dis": "dis",
    "sd": "sd",
    "sis": "sis",
    "csc": "csc",
    "sub-registrar": "sub_registrar",
    "sub registrar": "sub_registrar",
    # Tamil script forms
    "பட்டா": "patta",
    "சிட்டா": "chitta",
    "அடங்கல்": "adangal",
    "புலத்தணிக்கை": "field_visit",
    "உட்பிரிவு": "sub_division",
    "கிரையப் பத்திரம்": "sale_deed",
    "வட்டாட்சியர்": "tahsildar",
}

# Tanglish colloquial phrasing -> domain expansion
TANGLISH_EXPANSIONS = {
    "patta maarudhal": "patta transfer 0153 0154",
    "patta marudhal": "patta transfer 0153 0154",
    "adangal eduppadhu": "adangal extract",
    "field visit eppo": "field visit scheduling timeline",
    "field visit eppadi": "field visit procedure inspection",
    "evvalavu naal": "timeline days working days sla",
    "evvalavu fees": "fees charge cost amount",
    "yaar approve pannuva": "approving authority tahsildar workflow",
    "sub division eppadi": "sub-division procedure 0154",
    "reject aana": "rejection reasons appeal workflow",
}


def normalize_query_for_rag(query: str, language: str = "en") -> Dict[str, Any]:
    """
    Clean and normalize user query for retrieval while preserving revenue terminology.

    Extracts:
      - `normalized_query`: sanitized query text
      - `service_code`: extracted service code (e.g. "0154" or "0153") if present
      - `domain_terms`: set of detected domain keywords
      - `expanded_terms`: list of synonym/expansion terms for Tanglish / Tamil queries
    """
    if not query:
        return {
            "normalized_query": "",
            "service_code": None,
            "domain_terms": set(),
            "expanded_terms": [],
            "keywords": [],
        }

    raw = query.strip()
    lowered = raw.lower()

    # Detect service codes
    service_code: Optional[str] = None
    if "0154" in lowered or re.search(r"\bisd\b", lowered):
        service_code = "0154"
    elif "0153" in lowered or re.search(r"\bnisd\b", lowered):
        service_code = "0153"

    # Identify domain terms
    detected_terms: Set[str] = set()
    for term, canonical in REVENUE_DOMAIN_TERMS.items():
        if term in lowered:
            detected_terms.add(canonical)

    # Tanglish expansions
    expanded: List[str] = []
    for phrase, exp in TANGLISH_EXPANSIONS.items():
        if phrase in lowered:
            expanded.append(exp)

    # Clean characters but keep alphanumeric, hyphens, Tamil Unicode range
    # U+0B80-U+0BFF is Tamil Unicode block
    cleaned = re.sub(r"[^\w\s\-\u0B80-\u0BFF]", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Tokenize keywords (dropping common stop words)
    stop_words = {
        "what", "is", "the", "of", "and", "in", "to", "for", "a", "an", "how", "can",
        "do", "does", "are", "tell", "me", "about", "show", "give", "details", "please",
        "என்ன", "எப்படி", "எப்போது", "யார்", "எங்கு", "விவரம்",
    }
    tokens = [t for t in re.split(r"[\s\-_]+", cleaned.lower()) if t and t not in stop_words]

    return {
        "normalized_query": cleaned,
        "service_code": service_code,
        "domain_terms": detected_terms,
        "expanded_terms": expanded,
        "keywords": tokens,
    }


def _passage_key(candidate: Dict[str, Any]) -> str:
    """Generate a deterministic deduplication key for a retrieved chunk."""
    meta = candidate.get("metadata") or {}
    chunk_id = candidate.get("chunk_id") or meta.get("chunk_id")
    if chunk_id:
        return str(chunk_id)

    doc_name = meta.get("document_name") or meta.get("source") or "unknown"
    page = meta.get("page_number") or meta.get("page") or 0
    section = meta.get("section") or ""
    content_sample = (candidate.get("content") or "")[:120].strip()
    return f"{doc_name}:{page}:{section}:{hashlib.md5(content_sample.encode('utf-8', errors='ignore')).hexdigest()[:8]}"


def reciprocal_rank_fusion(
    ranking_lists: List[List[Dict[str, Any]]],
    k: int = 60,
    weights: Optional[List[float]] = None,
) -> List[Dict[str, Any]]:
    """
    Fuse multiple ranked candidate lists using Reciprocal Rank Fusion (RRF).

    Formula:
        RRF_score(d) = sum_i( weight_i / (k + rank_i(d)) )

    where rank is 1-based index in the ranking list.

    Args:
        ranking_lists: List of ranked candidate lists (e.g. [vector_results, lexical_results]).
        k: Smoothing parameter (standard default is 60).
        weights: Optional weights for each ranking list (default: 1.0 for each list).

    Returns:
        Consolidated list of candidate dicts with added:
          - "rrf_score": float
          - "retrieval_sources": list of retriever names
          - "rank_breakdown": dict of per-retriever 1-based ranks
    """
    if not ranking_lists:
        return []

    if weights is None or len(weights) != len(ranking_lists):
        weights = [1.0] * len(ranking_lists)

    scores: Dict[str, float] = {}
    doc_map: Dict[str, Dict[str, Any]] = {}
    sources_map: Dict[str, List[str]] = {}
    rank_map: Dict[str, Dict[str, int]] = {}

    for list_idx, rank_list in enumerate(ranking_lists):
        w = weights[list_idx]
        retriever_name = "vector" if list_idx == 0 else f"lexical_{list_idx}"
        if list_idx == 1:
            retriever_name = "lexical"

        for rank_zero, doc in enumerate(rank_list):
            key = _passage_key(doc)
            rank_one = rank_zero + 1

            if key not in doc_map:
                # Shallow copy to avoid mutating original
                doc_map[key] = dict(doc)
                sources_map[key] = []
                rank_map[key] = {}

            # Override or merge metadata if necessary
            current_retriever = doc.get("retrieval") or retriever_name
            if current_retriever not in sources_map[key]:
                sources_map[key].append(current_retriever)
            rank_map[key][current_retriever] = rank_one

            # Accumulate RRF score
            contribution = w / (k + rank_one)
            scores[key] = scores.get(key, 0.0) + contribution

    # Build fused results
    fused_results: List[Dict[str, Any]] = []
    for key, doc in doc_map.items():
        doc["rrf_score"] = round(scores[key], 6)
        doc["retrieval_sources"] = sources_map[key]
        doc["rank_breakdown"] = rank_map[key]
        fused_results.append(doc)

    # Sort descending by fused score
    fused_results.sort(key=lambda d: d["rrf_score"], reverse=True)
    return fused_results


def _token_jaccard(text1: str, text2: str) -> float:
    """Calculate token-level Jaccard similarity between two texts."""
    s1 = set(re.findall(r"\w+", text1.lower()))
    s2 = set(re.findall(r"\w+", text2.lower()))
    if not s1 or not s2:
        return 0.0
    intersection = len(s1 & s2)
    union = len(s1 | s2)
    return intersection / union if union else 0.0


def format_citation(metadata: Dict[str, Any]) -> str:
    """
    Format a standardized source citation string from chunk metadata.

    Example:
      [Source: Workflow Guide, Section: ISD Workflow, Page: 2]
    """
    if not metadata:
        return "[Source: SIS Knowledge Base]"

    source_name = metadata.get("document_name") or metadata.get("source") or "Government Manual"
    # Format document name nicely
    clean_name = source_name.replace(".txt", "").replace(".pdf", "").replace(".docx", "").replace("_", " ").title()

    section = metadata.get("section")
    page = metadata.get("page_number") or metadata.get("page")
    eff_date = metadata.get("effective_date")

    parts = [f"Source: {clean_name}"]
    if section and not section.startswith("chunk_"):
        clean_sec = section.strip("= ").title()
        parts.append(f"Section: {clean_sec}")
    if page and int(page) > 0:
        parts.append(f"Page: {page}")
    if eff_date:
        parts.append(f"Effective: {eff_date}")

    return f"[{', '.join(parts)}]"


def rerank_passages(
    query: str,
    candidates: List[Dict[str, Any]],
    language: str = "en",
    top_k: int = 4,
    min_score: float = 0.02,
) -> List[Dict[str, Any]]:
    """
    Rerank candidate passages with exact domain match boosting, deduplication,
    and authoritative manual preference.

    Scoring formula:
        Final_Score = RRF_Score * (1.0 + code_boost + keyword_coverage_boost + section_boost + authority_boost)

    Args:
        query: Original user query.
        candidates: Fused candidate list from RRF or retriever.
        language: Query language ("en", "ta", "tanglish").
        top_k: Maximum number of top passages to return.
        min_score: Minimum required score; passages below this are pruned.

    Returns:
        Top deduplicated passages with computed 'relevance_score' and formatted 'citation'.
    """
    if not candidates:
        return []

    norm_info = normalize_query_for_rag(query, language)
    query_keywords = set(norm_info["keywords"])
    target_service_code = norm_info["service_code"]

    scored_candidates: List[Dict[str, Any]] = []

    for cand in candidates:
        content = (cand.get("content") or "").strip()
        if not content:
            continue

        meta = cand.get("metadata") or {}
        base_score = float(cand.get("rrf_score") or (1.0 - float(cand.get("distance", 0.5))))

        multiplier = 1.0

        content_lower = content.lower()
        section_lower = (meta.get("section") or "").lower()
        source_lower = (meta.get("document_name") or meta.get("source") or "").lower()

        # 1. Exact Service Code Boost (0154 / 0153)
        if target_service_code:
            code_in_content = target_service_code in content_lower
            code_in_meta = meta.get("service_code") == target_service_code or target_service_code in source_lower
            if code_in_content or code_in_meta:
                multiplier += 0.35
            else:
                # Penalty if candidate explicitly discusses a conflicting service code
                conflicting = "0153" if target_service_code == "0154" else "0154"
                if conflicting in content_lower and target_service_code not in content_lower:
                    multiplier *= 0.70

        # 2. Domain Keyword Coverage Boost
        cand_tokens = set(re.findall(r"\w+", content_lower))
        if query_keywords:
            overlap = len(query_keywords & cand_tokens)
            coverage = overlap / len(query_keywords)
            multiplier += coverage * 0.25

        # 3. Section and Title Match Boost
        if any(kw in section_lower or kw in source_lower for kw in query_keywords if len(kw) > 3):
            multiplier += 0.15

        # 4. Authoritative Manual Preference
        category = (meta.get("category") or "").lower()
        if category in ("workflow", "survey_rules", "regulations", "official_manual"):
            multiplier += 0.10

        # 5. Language Alignment
        doc_lang = (meta.get("language") or "en").lower()
        if language == "ta" and doc_lang in ("ta", "tamil", "bilingual"):
            multiplier += 0.10
        elif language == "en" and doc_lang in ("en", "english", "bilingual"):
            multiplier += 0.05

        final_score = base_score * multiplier
        cand_copy = dict(cand)
        cand_copy["relevance_score"] = round(final_score, 6)
        cand_copy["citation"] = format_citation(meta)
        scored_candidates.append(cand_copy)

    # Sort descending by final score
    scored_candidates.sort(key=lambda c: c["relevance_score"], reverse=True)

    # Deduplicate overlapping chunk boundaries
    deduped: List[Dict[str, Any]] = []
    for cand in scored_candidates:
        if cand["relevance_score"] < min_score:
            continue

        # Check overlap against already chosen passages
        is_duplicate = False
        cand_content = cand.get("content", "")
        for accepted in deduped:
            accepted_content = accepted.get("content", "")
            # If two chunks have > 75% token overlap, drop the lower-ranked one
            if _token_jaccard(cand_content, accepted_content) > 0.75:
                is_duplicate = True
                break

        if not is_duplicate:
            deduped.append(cand)
            if len(deduped) >= top_k:
                break

    logger.info(
        f"Reranker: {len(candidates)} candidates -> {len(deduped)} top passages "
        f"(top score: {deduped[0]['relevance_score'] if deduped else 0.0:.4f}, min_score={min_score})"
    )
    return deduped

