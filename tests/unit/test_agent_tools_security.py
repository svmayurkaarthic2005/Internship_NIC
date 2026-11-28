"""
Unit tests for agent tools security, parameter validation, and geography authorization.
"""
import pytest
from backend.services.chatbot.agent_tools import (
    validate_args,
    authorize_geography,
    TOOLS,
    ToolArgumentError,
    ToolAuthorizationError,
    ToolContext,
    _FORBIDDEN_ARGS,
)
from backend.schemas import OfficerContext


def _get_tool_spec(name: str):
    spec = next((t for t in TOOLS if t.name == name), None)
    assert spec is not None, f"Tool {name} not found in TOOLS"
    return spec


def test_agent_tools_rejects_forbidden_arguments():
    spec = _get_tool_spec("list_applications")

    for forbidden in ["officer", "officer_id", "sql", "where", "jurisdiction", "district_id"]:
        with pytest.raises(ToolArgumentError) as exc_info:
            validate_args(spec, {forbidden: "malicious_value"})
        assert "Whose data is being read is fixed" in str(exc_info.value)


def test_agent_tools_rejects_invalid_application_number_shape():
    spec = _get_tool_spec("get_application_details")

    # Pronouns or arbitrary strings without YYYY/ prefix must be rejected
    with pytest.raises(ToolArgumentError) as exc_info:
        validate_args(spec, {"application_number": "it"})
    assert "does not look like an application number" in str(exc_info.value)

    with pytest.raises(ToolArgumentError):
        validate_args(spec, {"application_number": "that_one"})


def test_agent_tools_clamps_presentational_arguments():
    spec = _get_tool_spec("search_documents")

    # max_results exceeds maximum 8 -> should be clamped to 8 without error
    cleaned = validate_args(spec, {"query": "workflow", "max_results": 20})
    assert cleaned["max_results"] == 8

    cleaned_low = validate_args(spec, {"query": "workflow", "max_results": 0})
    assert cleaned_low["max_results"] == 1


def test_agent_tools_refuses_out_of_range_semantic_arguments():
    spec = _get_tool_spec("list_applications")

    # Month 13 is invalid and cannot be clamped
    with pytest.raises(ToolArgumentError) as exc_info:
        validate_args(spec, {"submission_month": 13})
    assert "must be at most 12" in str(exc_info.value)


def test_agent_tools_authorize_geography():
    import uuid
    dummy_officer = OfficerContext(
        officer_id=uuid.uuid4(),
        employee_id="SIS-001",
        name="Arun M",
        email="arun_m@sis.tn.gov.in",
        designation="Ward SIS",
        jurisdiction_type="ward",
        jurisdiction_name="Ward 081",
        jurisdiction_ids=[uuid.uuid4()],
    )
    ctx = ToolContext(
        db=None,
        officer=dummy_officer,
        wards=["081", "082"],
        blocks=["001", "002"]
    )

    # Authorized ward passes
    authorize_geography(ctx, {"ward_number": "081"})

    # Unauthorized ward raises ToolAuthorizationError
    with pytest.raises(ToolAuthorizationError) as exc_info:
        authorize_geography(ctx, {"ward_number": "099"})
    assert "Ward 099 is outside" in str(exc_info.value)
    assert "Wards held: 081, 082" in str(exc_info.value)

    # Unauthorized block raises ToolAuthorizationError
    with pytest.raises(ToolAuthorizationError) as exc_info:
        authorize_geography(ctx, {"block_number": "099"})
    assert "Block 099 is outside" in str(exc_info.value)
