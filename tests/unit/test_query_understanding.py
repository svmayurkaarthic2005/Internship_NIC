"""
Unit tests for backend.services.chatbot.query_understanding.
Tests Pydantic validation, authoritative identifier extraction, fast-path routing,
semantic interpretation in English, Tamil, and Tanglish, analytical reasoning,
multi-part decomposition, and fallback resilience.
"""

import pytest
from pydantic import ValidationError

from backend.services.chatbot.query_understanding import (
    SemanticQueryPlan,
    DateRangeFilter,
    AgeDurationFilter,
    SortingSpec,
    OperationSpec,
    extract_authoritative_identifiers,
    understand_query,
    _try_deterministic_fast_path
)


@pytest.fixture(autouse=True)
def mock_offline_llm(monkeypatch):
    import backend.services.rag_llm as rag_llm
    class FastFallbackLLM:
        def bind(self, *args, **kwargs):
            return self
        async def ainvoke(self, *args, **kwargs):
            raise RuntimeError("Ollama offline in test")
    monkeypatch.setattr(rag_llm, "llm", FastFallbackLLM())


def test_pydantic_schema_strict_validation():
    """Verify SemanticQueryPlan enforces valid types, bounds and defaults."""
    plan = SemanticQueryPlan(
        intent="pending_applications",
        confidence=0.95,
        response_language="en",
        status_filters=["pending"],
        age_filter=AgeDurationFilter(is_overdue=True, min_days=10),
        sorting=SortingSpec(field="date", order="desc")
    )
    assert plan.intent == "pending_applications"
    assert plan.confidence == 0.95
    assert plan.status_filters == ["pending"]
    assert plan.age_filter.min_days == 10
    assert plan.sorting.order == "desc"
    assert plan.needs_database is True
    assert plan.is_multi_part is False


def test_pydantic_schema_rejects_out_of_bounds():
    """Verify validation errors on negative days, invalid confidence, or bad months."""
    with pytest.raises(ValidationError):
        # Confidence must be between 0.0 and 1.0
        SemanticQueryPlan(intent="pending", confidence=1.5)

    with pytest.raises(ValidationError):
        # Month must be between 1 and 12
        DateRangeFilter(month=15)

    with pytest.raises(ValidationError):
        # min_days cannot be negative
        AgeDurationFilter(min_days=-5)


def test_extract_authoritative_identifiers():
    """Rule 9: Ensure application numbers and survey numbers are strictly parsed."""
    q1 = "What is the status of 2026/0154/02/013710?"
    ids1 = extract_authoritative_identifiers(q1)
    assert ids1["application_numbers"] == ["2026/0154/02/013710"]
    assert ids1["survey_numbers"] == []

    q2 = "Who owns survey 1355/1B12?"
    ids2 = extract_authoritative_identifiers(q2)
    assert ids2["survey_numbers"] == ["1355/1B12"]

    q3 = "Explain difference between 0153 and 0154 from CSC channel"
    ids3 = extract_authoritative_identifiers(q3)
    assert "0153" in ids3["service_codes"]
    assert "0154" in ids3["service_codes"]
    assert ids3["submission_channel"] == "CSC"


def test_deterministic_fast_path():
    """Verify fast-path resolves greetings and service code lookups instantly."""
    p1 = _try_deterministic_fast_path("வணக்கம்", "ta")
    assert p1 is not None
    assert p1.intent == "greeting"
    assert p1.response_language == "ta"
    assert p1.source == "deterministic_fast_path"

    p2 = _try_deterministic_fast_path("hello", "en")
    assert p2 is not None
    assert p2.intent == "greeting"

    p3 = _try_deterministic_fast_path("0153", "en")
    assert p3 is not None
    assert p3.intent == "service_code_lookup"
    assert p3.service_codes == ["0153"]


@pytest.mark.asyncio
async def test_analytical_urgency_interpretation(monkeypatch):
    """Verify Example query from specification: files pending long time needing urgent attention."""
    import backend.services.rag_llm as rag_llm
    class FastFallbackLLM:
        def bind(self, *args, **kwargs):
            return self
        async def ainvoke(self, *args, **kwargs):
            raise RuntimeError("Ollama offline in test")
    monkeypatch.setattr(rag_llm, "llm", FastFallbackLLM())

    query = "Show files pending for a long time and identify which ones need urgent attention."
    plan = await understand_query(query)
    
    assert plan.intent == "application_analysis"
    assert "pending" in plan.status_filters
    assert plan.age_filter is not None
    assert plan.age_filter.is_overdue is True
    assert plan.age_filter.urgency_tier == "urgent"
    assert plan.needs_database is True


@pytest.mark.asyncio
async def test_fallback_resilience_on_llm_failure(monkeypatch):
    """Rule 11: Verify that LLM unavailability falls back gracefully to deterministic parsing."""
    import backend.services.rag_llm as rag_llm
    
    # Mock LLM to raise an exception simulating offline Ollama
    class BrokenLLM:
        def bind(self, *args, **kwargs):
            return self
        async def ainvoke(self, *args, **kwargs):
            raise RuntimeError("Ollama connection refused (simulated)")

    monkeypatch.setattr(rag_llm, "llm", BrokenLLM())

    query = "Show my pending applications"
    plan = await understand_query(query)
    
    assert plan is not None
    assert plan.source == "fallback_regex"
    assert plan.intent in ("pending_applications", "all_service_code_applications")
    assert plan.response_language == "en"
    assert plan.needs_database is True


@pytest.mark.asyncio
async def test_tamil_and_tanglish_understanding():
    """Verify language preservation and intent mapping for Tamil and Tanglish."""
    # Tamil overdue query
    ta_q = "தாமதமான விண்ணப்பங்களின் பட்டியலைக் காட்டு"
    ta_plan = await understand_query(ta_q)
    assert ta_plan.response_language == "ta"
    assert ta_plan.intent in ("overdue_applications", "pending_applications")

    # Tanglish pending query
    tg_q = "pending list kaattu"
    tg_plan = await understand_query(tg_q)
    assert tg_plan.query_language == "tanglish"
    assert tg_plan.response_language in ("tanglish", "ta")
    assert tg_plan.intent in ("pending_applications", "all_service_code_applications")


def test_multipart_and_comparative_plan_structure():
    """Verify multi-part operation decomposition and comparative flags in SemanticQueryPlan."""
    op1 = OperationSpec(operation_type="count", target_entity="application", parameters={"type": "ISD", "status": "pending"})
    op2 = OperationSpec(operation_type="count", target_entity="application", parameters={"is_overdue": True})
    
    plan = SemanticQueryPlan(
        intent="officer_workload",
        confidence=0.9,
        is_multi_part=True,
        operations=[op1, op2],
        is_comparative=False
    )
    
    assert plan.is_multi_part is True
    assert len(plan.operations) == 2
    assert plan.operations[0].parameters["type"] == "ISD"
    assert plan.operations[1].parameters["is_overdue"] is True

    comp_plan = SemanticQueryPlan(
        intent="service_code_guide",
        confidence=0.95,
        is_comparative=True,
        service_codes=["0154", "0153"]
    )
    assert comp_plan.is_comparative is True
    assert "0154" in comp_plan.service_codes

