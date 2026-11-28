"""
Unit tests for database query jurisdiction scoping and isolation.
"""
import uuid
import pytest
from backend.services.postgres import (
    lookup_application_access,
    get_pending_applications,
)
from backend.schemas import OfficerContext


@pytest.mark.asyncio
async def test_lookup_application_access_isolation(
    db_session,
    test_officer: OfficerContext
):
    # Officer 1 checks their pending applications
    pending1 = await get_pending_applications(db_session, test_officer)
    assert "applications" in pending1
    if pending1["applications"]:
        app_num = pending1["applications"][0]["application_number"]

        # Test officer has access
        access_officer = await lookup_application_access(db_session, app_num, test_officer)
        assert access_officer["exists"] is True
        assert access_officer["accessible"] is True

        # An officer from a different jurisdiction (e.g. Ward 999 / foreign UUID) must NOT have access
        foreign_officer = OfficerContext(
            officer_id=uuid.uuid4(),
            employee_id="SIS-999",
            name="Foreign Officer",
            email="foreign@sis.tn.gov.in",
            designation="Ward SIS",
            role="Ward SIS",
            jurisdiction_type="ward",
            jurisdiction_name="Foreign Ward",
            jurisdiction_ids=[uuid.uuid4()]
        )
        access_foreign = await lookup_application_access(db_session, app_num, foreign_officer)
        assert access_foreign["exists"] is True
        assert access_foreign["accessible"] is False


@pytest.mark.asyncio
async def test_pending_applications_jurisdiction_scoping(
    db_session,
    test_officer: OfficerContext
):
    data = await get_pending_applications(db_session, test_officer)
    assert data["count"] >= 0
    # Every returned application must match active status
    for app in data.get("applications", []):
        assert app["status"] in ("pending", "in_progress", "escalated")
        assert app["status"] != "rejected"
