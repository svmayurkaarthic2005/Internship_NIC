"""
Evaluation Metrics Calculator for SIS Chatbot.
Computes all 10 required evaluation metrics for Phase 1 baseline assessment.
"""
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class MetricResult(BaseModel):
    """Result for an individual evaluation metric."""
    name: str
    score: float  # Percentage 0.0 - 100.0
    passed: int
    total: int
    details: List[Dict[str, Any]] = Field(default_factory=list)


class CaseExecutionResult(BaseModel):
    """Execution output of an individual test case."""
    test_id: str
    category: str
    question: str
    detected_language: str
    language_match: bool
    parsed_intent: Optional[str] = None
    intent_match: Optional[bool] = None
    extracted_entities: Dict[str, Any] = Field(default_factory=dict)
    entity_match: Optional[bool] = None
    selected_tool: Optional[str] = None
    tool_match: Optional[bool] = None
    tool_args: Optional[Dict[str, Any]] = None
    tool_args_valid: Optional[bool] = None
    followup_resolved: Optional[bool] = None
    followup_match: Optional[bool] = None
    db_verified: Optional[bool] = None
    retrieval_verified: Optional[bool] = None
    unauthorized_rejection_verified: Optional[bool] = None
    unsupported_claim_detected: Optional[bool] = None
    latency_ms: float = 0.0
    errors: List[str] = Field(default_factory=list)
    raw_output: Optional[str] = None


class BenchmarkSummary(BaseModel):
    """Aggregate benchmark results across the evaluation dataset."""
    total_cases: int
    passed_cases: int
    overall_pass_rate: float
    average_latency_ms: float
    
    # 10 Automated Core Metrics
    intent_accuracy: MetricResult
    entity_extraction_accuracy: MetricResult
    tool_selection_accuracy: MetricResult
    tool_argument_accuracy: MetricResult
    followup_resolution_accuracy: MetricResult
    database_factual_accuracy: MetricResult
    document_retrieval_quality: MetricResult
    unauthorized_rejection_rate: MetricResult
    unsupported_claims_avoidance_rate: MetricResult
    response_latency: MetricResult
    
    category_summary: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    case_results: List[CaseExecutionResult] = Field(default_factory=list)


def calculate_metrics(results: List[CaseExecutionResult]) -> BenchmarkSummary:
    """Aggregate and calculate all 10 metrics from raw test execution results."""
    total_cases = len(results)
    
    # 1. Intent Accuracy
    intent_tested = [r for r in results if r.intent_match is not None]
    intent_passed = [r for r in intent_tested if r.intent_match is True]
    intent_metric = MetricResult(
        name="Intent Accuracy",
        score=round((len(intent_passed) / len(intent_tested) * 100.0), 2) if intent_tested else 100.0,
        passed=len(intent_passed),
        total=len(intent_tested),
        details=[{"id": r.test_id, "parsed": r.parsed_intent} for r in intent_tested if not r.intent_match]
    )

    # 2. Entity Extraction Accuracy
    entity_tested = [r for r in results if r.entity_match is not None]
    entity_passed = [r for r in entity_tested if r.entity_match is True]
    entity_metric = MetricResult(
        name="Entity Extraction Accuracy",
        score=round((len(entity_passed) / len(entity_tested) * 100.0), 2) if entity_tested else 100.0,
        passed=len(entity_passed),
        total=len(entity_tested),
        details=[{"id": r.test_id, "extracted": r.extracted_entities} for r in entity_tested if not r.entity_match]
    )

    # 3. Tool Selection Accuracy
    tool_tested = [r for r in results if r.tool_match is not None]
    tool_passed = [r for r in tool_tested if r.tool_match is True]
    tool_metric = MetricResult(
        name="Tool Selection Accuracy",
        score=round((len(tool_passed) / len(tool_tested) * 100.0), 2) if tool_tested else 100.0,
        passed=len(tool_passed),
        total=len(tool_tested),
        details=[{"id": r.test_id, "selected": r.selected_tool} for r in tool_tested if not r.tool_match]
    )

    # 4. Tool Argument Accuracy
    arg_tested = [r for r in results if r.tool_args_valid is not None]
    arg_passed = [r for r in arg_tested if r.tool_args_valid is True]
    arg_metric = MetricResult(
        name="Tool Argument Accuracy",
        score=round((len(arg_passed) / len(arg_tested) * 100.0), 2) if arg_tested else 100.0,
        passed=len(arg_passed),
        total=len(arg_tested),
        details=[{"id": r.test_id, "args": r.tool_args} for r in arg_tested if not r.tool_args_valid]
    )

    # 5. Follow-up Resolution Accuracy
    followup_tested = [r for r in results if r.followup_match is not None]
    followup_passed = [r for r in followup_tested if r.followup_match is True]
    followup_metric = MetricResult(
        name="Follow-up Resolution Accuracy",
        score=round((len(followup_passed) / len(followup_tested) * 100.0), 2) if followup_tested else 100.0,
        passed=len(followup_passed),
        total=len(followup_tested),
        details=[{"id": r.test_id, "resolved": r.followup_resolved} for r in followup_tested if not r.followup_match]
    )

    # 6. Database Factual Accuracy
    db_tested = [r for r in results if r.db_verified is not None]
    db_passed = [r for r in db_tested if r.db_verified is True]
    db_metric = MetricResult(
        name="Database Factual Accuracy",
        score=round((len(db_passed) / len(db_tested) * 100.0), 2) if db_tested else 100.0,
        passed=len(db_passed),
        total=len(db_tested),
        details=[{"id": r.test_id, "errors": r.errors} for r in db_tested if not r.db_verified]
    )

    # 7. Document Retrieval Quality
    retrieval_tested = [r for r in results if r.retrieval_verified is not None]
    retrieval_passed = [r for r in retrieval_tested if r.retrieval_verified is True]
    retrieval_metric = MetricResult(
        name="Document Retrieval Quality",
        score=round((len(retrieval_passed) / len(retrieval_tested) * 100.0), 2) if retrieval_tested else 100.0,
        passed=len(retrieval_passed),
        total=len(retrieval_tested),
        details=[{"id": r.test_id, "errors": r.errors} for r in retrieval_tested if not r.retrieval_verified]
    )

    # 8. Unauthorized Access Rejection Rate
    unauth_tested = [r for r in results if r.unauthorized_rejection_verified is not None]
    unauth_passed = [r for r in unauth_tested if r.unauthorized_rejection_verified is True]
    unauth_metric = MetricResult(
        name="Unauthorized Access Rejection Rate",
        score=round((len(unauth_passed) / len(unauth_tested) * 100.0), 2) if unauth_tested else 100.0,
        passed=len(unauth_passed),
        total=len(unauth_tested),
        details=[{"id": r.test_id, "errors": r.errors} for r in unauth_tested if not r.unauthorized_rejection_verified]
    )

    # 9. Unsupported Factual Claims Avoidance Rate (100% means zero unsupported claims hallucinated)
    unsupp_tested = [r for r in results if r.unsupported_claim_detected is not None]
    unsupp_passed = [r for r in unsupp_tested if r.unsupported_claim_detected is False]
    unsupp_metric = MetricResult(
        name="Unsupported Factual Claims Avoidance",
        score=round((len(unsupp_passed) / len(unsupp_tested) * 100.0), 2) if unsupp_tested else 100.0,
        passed=len(unsupp_passed),
        total=len(unsupp_tested),
        details=[{"id": r.test_id, "errors": r.errors} for r in unsupp_tested if r.unsupported_claim_detected is True]
    )

    # 10. Response Latency
    latencies = [r.latency_ms for r in results if r.latency_ms > 0]
    avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
    latency_passed = [lat for lat in latencies if lat < 3000.0]  # Standard SLA threshold: 3000ms
    latency_metric = MetricResult(
        name="Response Latency (<3000ms)",
        score=round((len(latency_passed) / len(latencies) * 100.0), 2) if latencies else 100.0,
        passed=len(latency_passed),
        total=len(latencies),
        details=[{"average_latency_ms": avg_latency, "max_latency_ms": max(latencies) if latencies else 0.0}]
    )

    # Category Breakdown
    cat_summary: Dict[str, Dict[str, Any]] = {}
    for r in results:
        if r.category not in cat_summary:
            cat_summary[r.category] = {"total": 0, "passed": 0, "latencies": []}
        cat_summary[r.category]["total"] += 1
        cat_summary[r.category]["latencies"].append(r.latency_ms)
        # Check overall case pass (no errors, language matches, and key matches hold)
        case_passed = (
            r.language_match and
            (r.intent_match is None or r.intent_match is True) and
            (r.entity_match is None or r.entity_match is True) and
            (r.tool_match is None or r.tool_match is True) and
            (r.followup_match is None or r.followup_match is True) and
            (r.db_verified is None or r.db_verified is True) and
            (r.retrieval_verified is None or r.retrieval_verified is True) and
            (r.unauthorized_rejection_verified is None or r.unauthorized_rejection_verified is True) and
            (r.unsupported_claim_detected is None or r.unsupported_claim_detected is False)
        )
        if case_passed:
            cat_summary[r.category]["passed"] += 1

    for cat, val in cat_summary.items():
        val["pass_rate"] = round((val["passed"] / val["total"] * 100.0), 2)
        val["avg_latency_ms"] = round(sum(val["latencies"]) / len(val["latencies"]), 2) if val["latencies"] else 0.0

    passed_cases = sum(1 for v in cat_summary.values() for _ in range(v["passed"]))
    overall_pass_rate = round((passed_cases / total_cases * 100.0), 2) if total_cases > 0 else 0.0

    return BenchmarkSummary(
        total_cases=total_cases,
        passed_cases=passed_cases,
        overall_pass_rate=overall_pass_rate,
        average_latency_ms=avg_latency,
        intent_accuracy=intent_metric,
        entity_extraction_accuracy=entity_metric,
        tool_selection_accuracy=tool_metric,
        tool_argument_accuracy=arg_metric,
        followup_resolution_accuracy=followup_metric,
        database_factual_accuracy=db_metric,
        document_retrieval_quality=retrieval_metric,
        unauthorized_rejection_rate=unauth_metric,
        unsupported_claims_avoidance_rate=unsupp_metric,
        response_latency=latency_metric,
        category_summary=cat_summary,
        case_results=results
    )

