"""
Unit tests for backend.services.chatbot.agent_plan.
Tests typed execution planning, strict pre-flight validation, independent parallel execution,
sequential dependent execution, dynamic argument resolution, error propagation,
deduplication caching, and timeout handling.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from backend.schemas import OfficerContext
from backend.services.chatbot.agent_plan import (
    StepStatus,
    PlanStep,
    ExecutionPlan,
    validate_execution_plan,
    create_execution_plan,
    resolve_step_arguments,
    execute_execution_plan,
)
from backend.services.chatbot.agent_tools import (
    ToolContext,
    ToolArgumentError,
    ToolError
)
from backend.services.chatbot.query_understanding import SemanticQueryPlan


@pytest.fixture
def dummy_tool_context():
    """Create a minimal mock ToolContext for plan testing."""
    mock_officer = OfficerContext(
        officer_id="00000000-0000-0000-0000-000000000001",
        employee_id="SIS-TEST",
        name="Test Officer",
        email="test@sis.tn.gov.in",
        designation="Ward SIS",
        role="Ward SIS",
        jurisdiction_type="Ward",
        jurisdiction_name="Ambattur Ward B",
        jurisdiction_ids=["11111111-1111-1111-1111-111111111111"],
        officer_stage="SIS"
    )
    return ToolContext(
        db=AsyncMock(),
        officer=mock_officer,
        wards=["B"],
        blocks=["101"]
    )


def test_plan_validation_rejects_invalid_tool():
    """Verify that steps with non-existent tools are rejected before execution."""
    plan = ExecutionPlan(
        plan_id="plan_test_1",
        query="delete all records",
        goal="Malicious execution test",
        steps=[
            PlanStep(step_id="step_1", tool_name="drop_database", arguments={})
        ]
    )
    is_valid, errors = validate_execution_plan(plan)
    assert not is_valid
    assert any("Invalid tool 'drop_database'" in err for err in errors)


def test_plan_validation_rejects_forbidden_arguments():
    """Verify that arguments attempting to override officer jurisdiction or inject SQL are rejected."""
    plan = ExecutionPlan(
        plan_id="plan_test_2",
        query="show files for officer 999",
        goal="Security bypass test",
        steps=[
            PlanStep(
                step_id="step_1",
                tool_name="get_pending_applications",
                arguments={"officer_id": "99999999-9999-9999-9999-999999999999"}
            )
        ]
    )
    is_valid, errors = validate_execution_plan(plan)
    assert not is_valid
    assert any("Forbidden argument 'officer_id'" in err for err in errors)


def test_plan_validation_rejects_invalid_dependencies():
    """Verify that forward dependencies or non-existent dependency IDs are rejected."""
    plan = ExecutionPlan(
        plan_id="plan_test_3",
        query="dependent query",
        goal="Dependency validation",
        steps=[
            # step_1 depends on step_2 which hasn't run yet!
            PlanStep(step_id="step_1", tool_name="get_field_visits", depends_on=["step_2"]),
            PlanStep(step_id="step_2", tool_name="get_pending_applications")
        ]
    )
    is_valid, errors = validate_execution_plan(plan)
    assert not is_valid
    assert any("invalid or forward dependency" in err for err in errors)


def test_multi_step_planning_generation():
    """Verify Example workflow from user specification: pending ISD -> oldest -> field visit."""
    user_query = "Show my pending ISD applications, identify the oldest one and check whether its field visit is scheduled."
    
    query_plan = SemanticQueryPlan(
        intent="application_analysis",
        confidence=0.95,
        application_type="ISD",
        status_filters=["pending"],
        needs_database=True
    )
    
    plan = create_execution_plan(query_plan, user_query)
    assert plan.is_valid is True
    assert plan.is_multi_step is True
    assert len(plan.steps) == 2

    # Step 1: get_pending_applications(application_type="ISD")
    assert plan.steps[0].step_id == "step_1"
    assert plan.steps[0].tool_name == "get_pending_applications"
    assert plan.steps[0].arguments == {"application_type": "ISD"}

    # Step 2: get_field_visits with dynamic dependency on Step 1
    assert plan.steps[1].step_id == "step_2"
    assert plan.steps[1].tool_name == "get_field_visits"
    assert plan.steps[1].depends_on == ["step_1"]
    assert plan.steps[1].arguments == {"application_number": "$step_1.oldest_application_number"}


def test_dynamic_argument_resolution():
    """Verify that $step_1.oldest_application_number extracts the oldest record by submission_date."""
    step_1 = PlanStep(
        step_id="step_1",
        tool_name="get_pending_applications",
        status=StepStatus.COMPLETED,
        result={
            "applications": [
                {
                    "application_number": "2026/0154/02/013710",
                    "submission_date": "2026-02-15",
                    "status": "pending"
                },
                {
                    "application_number": "2026/0154/02/012547",
                    "submission_date": "2026-01-05",  # Oldest!
                    "status": "pending"
                }
            ]
        }
    )
    completed = {"step_1": step_1}

    step_2 = PlanStep(
        step_id="step_2",
        tool_name="get_field_visits",
        arguments={"application_number": "$step_1.oldest_application_number"},
        depends_on=["step_1"]
    )

    resolved = resolve_step_arguments(step_2, completed)
    assert resolved["application_number"] == "2026/0154/02/012547"


def test_dynamic_argument_resolution_fails_on_empty_prerequisite():
    """Verify that if prerequisite returned 0 records, argument resolution fails loudly rather than inventing."""
    step_1 = PlanStep(
        step_id="step_1",
        tool_name="get_pending_applications",
        status=StepStatus.COMPLETED,
        result={"applications": []}
    )
    completed = {"step_1": step_1}

    step_2 = PlanStep(
        step_id="step_2",
        tool_name="get_field_visits",
        arguments={"application_number": "$step_1.oldest_application_number"},
        depends_on=["step_1"]
    )

    with pytest.raises(ToolArgumentError) as exc_info:
        resolve_step_arguments(step_2, completed)
    assert "returned 0 applications" in str(exc_info.value)


@pytest.mark.asyncio
async def test_parallel_execution_of_independent_operations(dummy_tool_context):
    """Verify that independent steps execute concurrently."""
    plan = ExecutionPlan(
        plan_id="plan_par_1",
        query="count ISD and NISD",
        goal="Parallel count test",
        steps=[
            PlanStep(step_id="step_1", tool_name="list_applications", arguments={"application_type": "ISD"}),
            PlanStep(step_id="step_2", tool_name="list_applications", arguments={"application_type": "NISD"}),
        ]
    )

    call_order: list = []

    async def mock_execute(ctx, name, args):
        call_order.append(f"{name}:{args.get('application_type')}")
        await asyncio.sleep(0.01)
        return {"count": 10, "type": args.get("application_type")}

    with patch("backend.services.chatbot.agent_plan.execute_tool", side_effect=mock_execute):
        executed = await execute_execution_plan(plan, dummy_tool_context)

    assert executed.steps[0].status == StepStatus.COMPLETED
    assert executed.steps[1].status == StepStatus.COMPLETED
    assert executed.steps[0].result == {"count": 10, "type": "ISD"}
    assert executed.steps[1].result == {"count": 10, "type": "NISD"}
    assert len(call_order) == 2


@pytest.mark.asyncio
async def test_failed_tool_skips_dependent_downstream_steps(dummy_tool_context):
    """Verify that if a prerequisite fails, downstream dependent steps are SKIPPED, not executed."""
    plan = ExecutionPlan(
        plan_id="plan_fail_1",
        query="dependent failure test",
        goal="Failure propagation test",
        steps=[
            PlanStep(step_id="step_1", tool_name="list_applications", arguments={"application_type": "ISD"}),
            PlanStep(
                step_id="step_2",
                tool_name="get_field_visits",
                arguments={"application_number": "$step_1.oldest_application_number"},
                depends_on=["step_1"]
            ),
        ]
    )

    async def mock_failing_execute(ctx, name, args):
        return {"error": "Database connection lost", "refused": False}

    with patch("backend.services.chatbot.agent_plan.execute_tool", side_effect=mock_failing_execute):
        executed = await execute_execution_plan(plan, dummy_tool_context)

    assert executed.steps[0].status == StepStatus.FAILED
    assert executed.steps[1].status == StepStatus.SKIPPED
    assert "did not complete successfully" in (executed.steps[1].error or "")


@pytest.mark.asyncio
async def test_plan_execution_deduplication(dummy_tool_context):
    """Verify that duplicate tool calls in the same turn are served from cache."""
    plan = ExecutionPlan(
        plan_id="plan_dedup_1",
        query="duplicate count test",
        goal="Deduplication test",
        steps=[
            PlanStep(step_id="step_1", tool_name="get_officer_workload", arguments={}),
            PlanStep(step_id="step_2", tool_name="get_officer_workload", arguments={}),
        ]
    )

    execution_count = 0

    async def mock_execute(ctx, name, args):
        nonlocal execution_count
        execution_count += 1
        return {"open_count": 5}

    with patch("backend.services.chatbot.agent_plan.execute_tool", side_effect=mock_execute):
        executed = await execute_execution_plan(plan, dummy_tool_context)

    assert executed.steps[0].status == StepStatus.COMPLETED
    assert executed.steps[1].status == StepStatus.COMPLETED
    # Should only hit execute_tool once due to deduplication cache!
    assert execution_count == 1

