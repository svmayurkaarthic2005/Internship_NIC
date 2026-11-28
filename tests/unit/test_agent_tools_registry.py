"""
Comprehensive unit test suite for the Professional SIS Tool Layer (Phase 4).
Verifies all 14 core tool capabilities, Pydantic argument validation,
PII redaction, structured evidence shapes, and jurisdiction enforcement.
"""

import pytest
from datetime import datetime, timezone
from backend.services.chatbot.agent_tools import (
    TOOLS,
    TOOLS_BY_NAME,
    ToolContext,
    ToolSpec,
    ToolArgumentError,
    ToolAuthorizationError,
    execute_tool,
    validate_args,
    mask_pii_record,
    format_tool_evidence,
    ListApplicationsArgs,
    CompareApplicationsArgs,
    OldestNewestApplicationsArgs,
    DurationAnalysisArgs,
    ServiceCodeInfoArgs,
)
from backend.schemas import OfficerContext


# ─────────────────────────────────────────────────────────────────────────────
# 3. Live Tool Capabilities with Database Session
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_tool_get_my_jurisdiction(test_officer: OfficerContext, db_session):
    ctx = await ToolContext.create(db_session, test_officer)
    result = await execute_tool(ctx, "get_my_jurisdiction", {})
    
    assert result.get("success") is True
    assert result.get("operation") == "get_my_jurisdiction"
    assert result.get("records", {}).get("officer_name") == test_officer.name
    assert result.get("records", {}).get("employee_id") == test_officer.employee_id
    assert "provenance" in result


@pytest.mark.asyncio
async def test_tool_service_code_info(test_officer: OfficerContext, db_session):
    ctx = await ToolContext.create(db_session, test_officer)
    
    # Check ISD (0154)
    res_isd = await execute_tool(ctx, "get_service_code_info", {"service_type": "ISD"})
    assert res_isd.get("success") is True
    records = res_isd.get("records", {})
    assert records.get("service_code") == "0154"
    assert records.get("involves_subdivision") is True
    assert records.get("requires_field_inspection") is True

    # Check NISD (0153)
    res_nisd = await execute_tool(ctx, "get_service_code_info", {"service_code": "0153"})
    assert res_nisd.get("success") is True
    rec_nisd = res_nisd.get("records", {})
    assert rec_nisd.get("service_code") == "0153"
    assert rec_nisd.get("involves_subdivision") is False
    assert rec_nisd.get("requires_field_inspection") is False


@pytest.mark.asyncio
async def test_tool_list_applications_and_pii_masking(test_officer: OfficerContext, db_session):
    ctx = await ToolContext.create(db_session, test_officer)
    result = await execute_tool(ctx, "list_applications", {"status": "pending"})

    assert result.get("success") is True
    assert "applications" in result
    apps = result.get("applications", [])
    assert isinstance(apps, list)
    assert len(apps) > 0

    # Ensure PII is not leaked in applications listing
    first_app = apps[0]
    assert "application_number" in first_app
    assert "applicant_mobile" not in first_app or "X" in str(first_app.get("applicant_mobile", ""))
    assert "applicant_aadhaar" not in first_app or "X" in str(first_app.get("applicant_aadhaar", ""))


@pytest.mark.asyncio
async def test_tool_application_status_lookup(test_officer: OfficerContext, db_session):
    ctx = await ToolContext.create(db_session, test_officer)
    # First get a valid application from list
    list_res = await execute_tool(ctx, "list_applications", {})
    apps = list_res.get("applications", [])
    assert len(apps) > 0
    test_app_no = apps[0]["application_number"]

    # Test get_application_status
    status_res = await execute_tool(ctx, "get_application_status", {"application_number": test_app_no})
    assert status_res.get("success") is True
    assert status_res.get("found") is True
    assert status_res.get("application_number") == test_app_no
    assert "status" in status_res
    assert "current_stage" in status_res
    assert "provenance" in status_res


@pytest.mark.asyncio
async def test_tool_compare_applications(test_officer: OfficerContext, db_session):
    ctx = await ToolContext.create(db_session, test_officer)
    list_res = await execute_tool(ctx, "list_applications", {})
    apps = list_res.get("applications", [])
    assert len(apps) >= 2, "Need at least 2 applications for comparison test"
    app1 = apps[0]["application_number"]
    app2 = apps[1]["application_number"]

    compare_res = await execute_tool(ctx, "compare_applications", {"application_numbers": [app1, app2]})
    assert compare_res.get("success") is True
    assert compare_res.get("compared_count") == 2
    assert "highlights" in compare_res
    matrix = compare_res.get("comparison_matrix", [])
    assert len(matrix) == 2
    assert matrix[0]["application_number"] == app1
    assert matrix[1]["application_number"] == app2


@pytest.mark.asyncio
async def test_tool_oldest_newest_applications(test_officer: OfficerContext, db_session):
    ctx = await ToolContext.create(db_session, test_officer)
    
    # Oldest
    oldest_res = await execute_tool(ctx, "get_oldest_newest_applications", {"order": "oldest", "limit": 1})
    assert oldest_res.get("success") is True
    assert oldest_res.get("order") == "oldest"
    assert oldest_res.get("oldest_application_number") is not None

    # Newest
    newest_res = await execute_tool(ctx, "get_oldest_newest_applications", {"order": "newest", "limit": 1})
    assert newest_res.get("success") is True
    assert newest_res.get("order") == "newest"
    assert newest_res.get("newest_application_number") is not None


@pytest.mark.asyncio
async def test_tool_duration_analysis(test_officer: OfficerContext, db_session):
    ctx = await ToolContext.create(db_session, test_officer)
    duration_res = await execute_tool(ctx, "analyze_application_duration", {})
    
    assert duration_res.get("success") is True
    assert "average_days_pending" in duration_res
    assert "median_days_pending" in duration_res
    assert "max_days_pending" in duration_res
    assert "age_distribution" in duration_res
    assert "under_15_days" in duration_res["age_distribution"]


@pytest.mark.asyncio
async def test_tool_unscheduled_inspections(test_officer: OfficerContext, db_session):
    ctx = await ToolContext.create(db_session, test_officer)
    unscheduled_res = await execute_tool(ctx, "get_unscheduled_inspections", {})
    
    assert unscheduled_res.get("success") is True
    assert "unscheduled_count" in unscheduled_res
    assert isinstance(unscheduled_res.get("records"), list)


@pytest.mark.asyncio
async def test_tool_jurisdiction_boundary_enforcement(
    test_officer: OfficerContext,
    db_session
):
    import uuid
    ambattur_ctx = await ToolContext.create(db_session, test_officer)
    foreign_officer = OfficerContext(
        officer_id=uuid.uuid4(),
        employee_id="SIS-999",
        name="Foreign Surveyor",
        email="foreign@sis.tn.gov.in",
        designation="Sub Inspector Surveyor",
        role="SURVEYOR",
        officer_stage="SIS",
        jurisdiction_type="ward",
        jurisdiction_name="Foreign Ward",
        jurisdiction_ids=[uuid.uuid4()]
    )
    foreign_ctx = await ToolContext.create(db_session, foreign_officer)

    # Get valid Ambattur ISD application
    list_res = await execute_tool(ambattur_ctx, "list_applications", {"application_type": "ISD"})
    apps = list_res.get("applications", [])
    assert len(apps) > 0
    app_no = apps[0]["application_number"]

    # Foreign officer (outside jurisdiction) tries to read status -> outside_jurisdiction refusal
    foreign_status = await execute_tool(foreign_ctx, "get_application_status", {"application_number": app_no})
    assert foreign_status.get("found") is False
    assert foreign_status.get("reason") == "outside_jurisdiction"

    # Foreign officer tries to read workflow history -> refused
    foreign_wf = await execute_tool(foreign_ctx, "get_workflow_history", {"application_number": app_no})
    assert foreign_wf.get("found") is False
    assert foreign_wf.get("reason") == "outside_jurisdiction"
