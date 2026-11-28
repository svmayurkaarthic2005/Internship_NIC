"""GET /api/v1/applications/ -- was a stub returning [] for every officer; now the officer's own
register (every service code), jurisdiction-scoped. Also: channel_report.html no longer embeds data."""
import pathlib
import re

import pytest
from fastapi import HTTPException

from backend.dependencies import get_current_officer
from backend.routers import applications as appr
from backend.services.postgres import get_all_service_code_applications


async def _call(db, officer, **kw):
    args = dict(stage=None, status=None, application_type=None, skip=0, limit=100)
    args.update(kw)
    return (await appr.get_applications(db=db, current_officer=officer, **args)).data


@pytest.mark.asyncio
async def test_lists_the_officers_register_not_a_stub(db_session, test_officer):
    data = await _call(db_session, test_officer)
    expected = await get_all_service_code_applications(db_session, test_officer, hide_rejected=False)
    assert data["total"] == len(expected["applications"]) > 0
    assert len(data["applications"]) == min(100, data["total"])


@pytest.mark.asyncio
async def test_pagination_is_exact(db_session, test_officer):
    full = await _call(db_session, test_officer, limit=100)
    page2 = await _call(db_session, test_officer, skip=5, limit=5)
    assert [r["application_number"] for r in page2["applications"]] == \
           [r["application_number"] for r in full["applications"][5:10]]


@pytest.mark.asyncio
async def test_filters(db_session, test_officer):
    isd = await _call(db_session, test_officer, application_type="ISD")
    assert isd["applications"] and all(r["service_code"] == "0154" for r in isd["applications"])
    pend = await _call(db_session, test_officer, status="pending")
    assert all(r["status"] == "pending" for r in pend["applications"])
    done = await _call(db_session, test_officer, stage="Completed")
    assert all((r["stage"] or "").lower() == "completed" for r in done["applications"])


@pytest.mark.asyncio
async def test_bad_type_is_rejected(db_session, test_officer):
    with pytest.raises(HTTPException) as e:
        await _call(db_session, test_officer, application_type="FOO")
    assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_officers_see_only_their_own_jurisdiction(db_session, test_officer, secondary_officer):
    # each officer's endpoint result is exactly that officer's own scoped register -- it never widens
    for officer in (test_officer, secondary_officer):
        data = await _call(db_session, officer, limit=100)
        own = await get_all_service_code_applications(db_session, officer, hide_rejected=False)
        assert data["total"] == len(own["applications"])
        assert {r["application_number"] for r in data["applications"]} <= \
               {r["application_number"] for r in own["applications"]}
    # the two officers' wards differ (001/002/008 vs 001/005/009), so their registers must too
    a = await get_all_service_code_applications(db_session, test_officer, hide_rejected=False)
    b = await get_all_service_code_applications(db_session, secondary_officer, hide_rejected=False)
    assert {r["application_number"] for r in a["applications"]} != {r["application_number"] for r in b["applications"]}


def test_route_requires_an_authenticated_officer():
    route = next(r for r in appr.router.routes if r.path == "/api/v1/applications/" and "GET" in r.methods)
    assert any(d.call is get_current_officer for d in route.dependant.dependencies)


def test_channel_report_embeds_no_application_data():
    page = pathlib.Path("frontend/channel_report.html").read_text(encoding="utf-8")
    assert "const RAW = [" not in page
    assert not re.search(r"\b[6-9]\d{9}\b", page)                 # no mobile numbers
    assert not re.search(r"\b20\d\d/0\d{3}/\d\d/\d{6}\b", page)   # no application numbers
    assert "Authorization" in page and "login.html" in page
