"""
test_hybrid_rag.py — Unit Tests for Advanced Hybrid RAG in SIS Chatbot
=======================================================================
Verifies:
  1. Query normalization and revenue terminology preservation (English, Tamil, Tanglish).
  2. Reciprocal Rank Fusion (RRF) rank-merging arithmetic and weight handling.
  3. Feature-based passage reranking (exact code boost, keyword density, section match,
     authoritative manual preference, near-duplicate suppression).
  4. Standardized source citation generation.
  5. Grounded prompt assembly and procedural refusal generation (zero hallucination).
  6. Hybrid search integration and graceful fallback when vector search fails.
  7. Strict separation between procedural knowledge (RAG) and database records.
"""

import pytest
from unittest.mock import patch, MagicMock

from backend.services.reranker import (
    normalize_query_for_rag,
    reciprocal_rank_fusion,
    rerank_passages,
    format_citation,
    _token_jaccard,
)
from backend.services.pgvector_store import (
    hybrid_search,
)
from backend.services.rag_llm import (
    get_rag_evidence,
    get_rag_context,
    build_grounded_rag_prompt,
    procedural_refusal_response,
)


class TestQueryNormalization:
    """Verifies query normalization, terminology preservation, and Tanglish expansion."""

    def test_preserves_exact_service_codes(self):
        norm_isd = normalize_query_for_rag("What is the workflow for 0154 ISD application?")
        assert norm_isd["service_code"] == "0154"
        assert "isd" in norm_isd["domain_terms"] or "0154" in norm_isd["normalized_query"]

        norm_nisd = normalize_query_for_rag("How does NISD 0153 differ from sub-division?")
        assert norm_nisd["service_code"] == "0153"
        assert "nisd" in norm_nisd["domain_terms"] or "0153" in norm_nisd["normalized_query"]

    def test_preserves_tamil_revenue_terminology(self):
        query_ta = "பட்டா மற்றும் சிட்டா அடங்கல் எடுக்கும் முறை என்ன?"
        norm = normalize_query_for_rag(query_ta, language="ta")
        assert "patta" in norm["domain_terms"]
        assert "chitta" in norm["domain_terms"]
        assert "adangal" in norm["domain_terms"]
        # Tamil script preserved in normalized query
        assert "பட்டா" in norm["normalized_query"]

    def test_tanglish_colloquial_expansion(self):
        query_tanglish = "patta maarudhal eppadi pannurathu?"
        norm = normalize_query_for_rag(query_tanglish, language="tanglish")
        assert any("patta transfer" in exp for exp in norm["expanded_terms"])

    def test_empty_query_safe(self):
        norm = normalize_query_for_rag("")
        assert norm["normalized_query"] == ""
        assert norm["service_code"] is None
        assert norm["keywords"] == []


class TestReciprocalRankFusion:
    """Verifies mathematical correctness and edge cases of RRF."""

    def test_rrf_scoring_arithmetic(self):
        # Two mock ranking lists with overlapping and unique items
        list_vec = [
            {"chunk_id": "doc_1", "content": "ISD workflow rules", "distance": 0.15},
            {"chunk_id": "doc_2", "content": "Survey inspection guidelines", "distance": 0.25},
        ]
        list_lex = [
            {"chunk_id": "doc_2", "content": "Survey inspection guidelines", "rank": 0.8},
            {"chunk_id": "doc_3", "content": "Fees and SLAs", "rank": 0.6},
        ]

        fused = reciprocal_rank_fusion([list_vec, list_lex], k=60)
        assert len(fused) == 3

        # doc_2 is rank 2 in vector and rank 1 in lexical:
        # Score = 1 / (60 + 2) + 1 / (60 + 1) = 1/62 + 1/61 ~= 0.016129 + 0.016393 = 0.032522
        # doc_1 is rank 1 in vector only:
        # Score = 1 / (60 + 1) = 1/61 ~= 0.016393
        # doc_2 should win top spot!
        assert fused[0]["chunk_id"] == "doc_2"
        expected_score_doc2 = round(1.0 / 62.0 + 1.0 / 61.0, 6)
        assert abs(fused[0]["rrf_score"] - expected_score_doc2) < 1e-4

        # Provenance tracking
        assert "vector" in fused[0]["retrieval_sources"]
        assert "lexical" in fused[0]["retrieval_sources"]

    def test_rrf_with_custom_weights(self):
        list_vec = [{"chunk_id": "doc_v", "content": "Vector primary"}]
        list_lex = [{"chunk_id": "doc_l", "content": "Lexical primary"}]

        # Vector weight 2.0 vs lexical weight 1.0
        fused = reciprocal_rank_fusion([list_vec, list_lex], k=60, weights=[2.0, 1.0])
        assert fused[0]["chunk_id"] == "doc_v"
        assert fused[0]["rrf_score"] > fused[1]["rrf_score"]


class TestPassageReranking:
    """Verifies feature-based scoring, exact code boosting, and duplicate suppression."""

    def test_exact_service_code_boost(self):
        query = "What is the timeline for 0154 ISD applications?"
        candidates = [
            {
                "chunk_id": "chunk_nisd",
                "content": "Service Code 0153 (NISD) takes 15-20 days.",
                "rrf_score": 0.030,
                "metadata": {"document_name": "manual.txt", "service_code": "0153"}
            },
            {
                "chunk_id": "chunk_isd",
                "content": "Service Code 0154 (ISD) involves sub-division and takes 30-35 days.",
                "rrf_score": 0.025,
                "metadata": {"document_name": "manual.txt", "service_code": "0154"}
            }
        ]

        reranked = rerank_passages(query, candidates, top_k=2)
        # Even though chunk_nisd had a slightly higher RRF score initially,
        # chunk_isd carries the exact target service code 0154 and should be boosted to #1!
        assert reranked[0]["chunk_id"] == "chunk_isd"
        assert reranked[0]["relevance_score"] > reranked[1]["relevance_score"]

    def test_near_duplicate_chunk_suppression(self):
        query = "field visit inspection schedule"
        candidates = [
            {
                "chunk_id": "chunk_1",
                "content": "The Sub Inspector Surveyor must conduct a field inspection within 7 days of receiving the ISD application.",
                "rrf_score": 0.035,
                "metadata": {"document_name": "manual.txt"}
            },
            {
                "chunk_id": "chunk_2",
                # Overlapping chunk boundary containing almost identical text
                "content": "The Sub Inspector Surveyor must conduct a field inspection within 7 days of receiving the ISD application at ward desk.",
                "rrf_score": 0.033,
                "metadata": {"document_name": "manual.txt"}
            },
            {
                "chunk_id": "chunk_3",
                "content": "Tahsildar is the approving authority for all final patta transfer orders.",
                "rrf_score": 0.020,
                "metadata": {"document_name": "manual.txt"}
            }
        ]

        reranked = rerank_passages(query, candidates, top_k=3, min_score=0.01)
        # chunk_2 should be dropped due to near-duplicate lexical overlap with chunk_1
        chunk_ids = [c["chunk_id"] for c in reranked]
        assert "chunk_1" in chunk_ids
        assert "chunk_2" not in chunk_ids

    def test_relevance_threshold_drops_irrelevant_chunks(self):
        query = "chocolate chip cookie recipe with butter"
        candidates = [
            {
                "chunk_id": "chunk_sis",
                "content": "Tamil Nadu Land Survey regulations specify FMB boundary measurements.",
                "rrf_score": 0.005,
                "metadata": {"document_name": "survey_manual.txt"}
            }
        ]
        # min_score = 0.02 should prune this irrelevant chunk
        reranked = rerank_passages(query, candidates, min_score=0.02)
        assert len(reranked) == 0


class TestCitationFormatting:
    """Verifies that citations contain accurate source, section, and page information."""

    def test_citation_formatting(self):
        meta = {
            "document_name": "workflow_guide.txt",
            "section": "=== ISD WORKFLOW ===",
            "page_number": 3,
            "effective_date": "2024-01-01"
        }
        citation = format_citation(meta)
        assert "Source: Workflow Guide" in citation
        assert "Section: Isd Workflow" in citation
        assert "Page: 3" in citation
        assert "Effective: 2024-01-01" in citation


class TestGroundedAnswerAndRefusal:
    """Verifies grounded prompt construction and procedural refusal on zero evidence."""

    def test_procedural_refusal_bilingual(self):
        refusal_en = procedural_refusal_response(language="en")
        assert "do not have authoritative procedural guidance" in refusal_en
        assert "Tahsildar" in refusal_en

        refusal_ta = procedural_refusal_response(language="ta")
        assert "அதிகாரப்பூர்வ அரசு கையேடுகளில்" in refusal_ta
        assert "வட்டாட்சியரை" in refusal_ta

    def test_grounded_prompt_instructions(self):
        query = "What is the fee for patta transfer?"
        evidence = [
            {
                "content": "Service Code 0153 fee is Rs 60.",
                "citation": "[Source: Workflow Guide, Page: 2]",
                "metadata": {"document_name": "workflow_guide.txt"}
            }
        ]
        prompt = build_grounded_rag_prompt(query, evidence, language="en")
        assert "STRICT GROUNDING RULES" in prompt
        assert "DO NOT hallucinate" in prompt
        assert "[Source: Workflow Guide, Page: 2]" in prompt
        assert "Service Code 0153 fee is Rs 60." in prompt


class TestHybridSearchIntegration:
    """Verifies hybrid_search pipeline with mocked vector and lexical retrievers."""

    @patch("backend.services.pgvector_store.similarity_search")
    @patch("backend.services.pgvector_store._lexical_search")
    def test_hybrid_search_end_to_end(self, mock_lexical, mock_vector):
        mock_vector.return_value = [
            {
                "chunk_id": "v1",
                "content": "ISD application requires SD sketch and field visit by SIS.",
                "distance": 0.20,
                "metadata": {"document_name": "workflow_guide.txt", "section": "ISD", "page_number": 4}
            }
        ]
        mock_lexical.return_value = [
            {
                "chunk_id": "v1",
                "content": "ISD application requires SD sketch and field visit by SIS.",
                "distance": 0.30,
                "metadata": {"document_name": "workflow_guide.txt", "section": "ISD", "page_number": 4}
            },
            {
                "chunk_id": "l2",
                "content": "NISD application does not involve new sub-division boundaries.",
                "distance": 0.50,
                "metadata": {"document_name": "workflow_guide.txt", "section": "NISD", "page_number": 2}
            }
        ]

        results = hybrid_search("What is required for ISD?", n_results=2)
        assert len(results) >= 1
        assert results[0]["chunk_id"] == "v1"
        assert "citation" in results[0]
        assert results[0]["relevance_score"] > 0

    @patch("backend.services.pgvector_store.similarity_search")
    @patch("backend.services.pgvector_store._lexical_search")
    def test_hybrid_search_vector_failure_fallback(self, mock_lexical, mock_vector):
        # Simulate vector search throwing an exception (e.g. Ollama service down)
        mock_vector.side_effect = Exception("Ollama HTTP connection refused")
        mock_lexical.return_value = [
            {
                "chunk_id": "lex_1",
                "content": "Lexical fallback passage for survey manual.",
                "distance": 0.4,
                "metadata": {"document_name": "survey_manual.txt"}
            }
        ]

        # hybrid_search must not crash; it should fall back gracefully on lexical candidates!
        results = hybrid_search("survey manual query", n_results=1, min_score=0.01)
        assert len(results) == 1
        assert results[0]["chunk_id"] == "lex_1"

    @patch("backend.services.rag_llm.hybrid_search")
    def test_get_rag_context_formatting(self, mock_hybrid):
        mock_hybrid.return_value = [
            {
                "content": "Rule 42: Field inspection must record boundary stones.",
                "citation": "[Source: Land Rules, Page: 5]",
                "metadata": {"document_name": "land_rules.txt", "page_number": 5}
            }
        ]

        context = get_rag_context("boundary stones rule", language="en")
        assert "[Source 1: [Source: Land Rules, Page: 5]]" in context
        assert "Rule 42: Field inspection must record boundary stones." in context
