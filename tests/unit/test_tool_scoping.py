"""
Dynamic tool scoping (`select_tool_names`), `tool_schemas(names=)` and the
execution-level allowlist enforced by `execute_tool(..., allowed=)`.

Tool-selection tests need no database (pure text -> name-list logic); the
allowlist-enforcement tests run a real tool call, so they use the same
`test_officer` / `db_session` fixtures the rest of the agent-tools suite does.
"""
import pytest

from backend.services.chatbot.agent_tools import (
    TOOLS,
    TOOLS_BY_NAME,
    TOOL_GROUPS,
    ToolContext,
    execute_tool,
    select_tool_names,
    tool_schemas,
)
from backend.schemas import OfficerContext


# ─────────────────────────────────────────────────────────────────────────────
# select_tool_names(): category coverage
# ─────────────────────────────────────────────────────────────────────────────
def _assert_registered(names):
    for n in names:
        assert n in TOOLS_BY_NAME, f"{n!r} is not a registered tool"


@pytest.mark.parametrize("message,must_include", [
    ("show my pending applications", {"list_applications", "get_pending_applications"}),
    ("how many applications do I have", {"count_applications"}),
    ("what is the status of 2026/0154/28/000001", {"get_application_status"}),
    ("which applications are overdue", {"get_overdue_applications"}),
    ("who is the owner of survey 1355", {"get_survey_ownership", "get_survey_details"}),
    ("details of survey number 77/3B", {"get_survey_details"}),
    ("schedule a field visit for next week", {"get_field_visits", "get_visit_plan"}),
    ("what is the fee for an ISD application", {"get_fee_summary", "get_service_code_info"}),
    ("what is my jurisdiction", {"get_my_jurisdiction"}),
    ("what documents are needed for a patta transfer", {"search_documents"}),
])
def test_select_tool_names_category_coverage(message, must_include):
    selected = select_tool_names(message)
    _assert_registered(selected)
    assert must_include.issubset(set(selected)), (message, selected)


def test_select_tool_names_tamil_application_question():
    selected = select_tool_names("எனது விண்ணப்பங்கள் நிலுவையில் உள்ளதா")
    _assert_registered(selected)
    assert "list_applications" in selected
    assert "get_pending_applications" in selected


def test_select_tool_names_tanglish_field_visit_question():
    selected = select_tool_names("field visit eppo irukku")
    _assert_registered(selected)
    assert "get_field_visits" in selected or "get_visit_plan" in selected


def test_select_tool_names_short_contextual_followup_uses_history():
    history = [
        {"role": "user", "content": "what is the fee for application 2026/0154/28/000001"},
        {"role": "assistant", "content": "The fee is Rs 500."},
    ]
    selected = select_tool_names("and for ward 002?", chat_history=history)
    _assert_registered(selected)
    assert "get_fee_summary" in selected


def test_select_tool_names_multi_category_question_keeps_both():
    # Names a survey AND asks about field-visit scheduling in one breath --
    # both parts' tools must survive, never traded off against each other.
    selected = select_tool_names("schedule a field visit for survey 1355 and check its ownership")
    _assert_registered(selected)
    assert "get_field_visits" in selected or "get_visit_plan" in selected
    assert "get_survey_ownership" in selected or "get_survey_details" in selected


def test_select_tool_names_unrecognized_query_falls_back_to_default():
    selected = select_tool_names("asdkjaslkdj random nonsense")
    _assert_registered(selected)
    assert 5 <= len(selected) <= 8


@pytest.mark.parametrize("message", [
    "show my pending applications",
    "who is the owner of survey 1355",
    "schedule a field visit",
    "what is my jurisdiction",
    "asdkjaslkdj random nonsense",
])
def test_select_tool_names_normal_queries_are_5_to_8_tools_no_duplicates(message):
    selected = select_tool_names(message)
    assert len(selected) == len(set(selected)), "duplicate tool in selection"
    assert 5 <= len(selected) <= 8, (message, selected)


def test_select_tool_names_genuinely_multi_category_may_exceed_eight():
    # "fee for an ISD application" is genuinely two intents at once (the
    # applications group's own "application" trigger, plus the fees group's
    # "fee"/"ISD") -- the 5-8 target is for a typical single-intent
    # question, never a ceiling the selector truncates a real multi-intent
    # match down to. Both parts' tools must still be present.
    selected = select_tool_names("what is the fee for an ISD application")
    assert len(selected) == len(set(selected))
    assert "get_fee_summary" in selected
    assert "list_applications" in selected


def test_select_tool_names_application_number_does_not_leak_into_fee_group():
    # "2026/0154/28/..." embeds "0154" -- must not be read as the fee group's
    # own service-code trigger on a question that is really about comparing
    # two applications, not about fees.
    selected = select_tool_names(
        "compare 2026/0154/28/000001 and 2026/0154/28/000002")
    assert "compare_applications" in selected
    assert "get_fee_summary" not in selected
    assert "get_service_code_info" not in selected


def test_tool_groups_only_names_registered_tools():
    for group, names in TOOL_GROUPS.items():
        for name in names:
            assert name in TOOLS_BY_NAME, f"group {group!r} names unregistered tool {name!r}"


# ─────────────────────────────────────────────────────────────────────────────
# tool_schemas(names=): backward compatibility + scoping
# ─────────────────────────────────────────────────────────────────────────────
def test_tool_schemas_no_args_returns_every_tool_unchanged():
    schemas = tool_schemas()
    assert len(schemas) == len(TOOLS)
    assert {s["function"]["name"] for s in schemas} == {t.name for t in TOOLS}


def test_tool_schemas_scoped_returns_only_requested_tools_in_registry_order():
    names = ["get_fee_summary", "list_applications", "get_my_jurisdiction"]
    schemas = tool_schemas(names)
    returned = [s["function"]["name"] for s in schemas]
    assert set(returned) == set(names)
    # registry order, not request order
    registry_order = [t.name for t in TOOLS if t.name in names]
    assert returned == registry_order


def test_tool_schemas_scoped_drops_unknown_name_without_raising():
    schemas = tool_schemas(["list_applications", "not_a_real_tool"])
    assert [s["function"]["name"] for s in schemas] == ["list_applications"]


# ─────────────────────────────────────────────────────────────────────────────
# execute_tool(..., allowed=): execution-level enforcement
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_execute_tool_legacy_call_without_allowlist_still_works(
        test_officer: OfficerContext, db_session):
    ctx = await ToolContext.create(db_session, test_officer)
    result = await execute_tool(ctx, "get_my_jurisdiction", {})
    assert result.get("success") is True


@pytest.mark.asyncio
async def test_execute_tool_rejects_call_outside_allowlist(
        test_officer: OfficerContext, db_session):
    ctx = await ToolContext.create(db_session, test_officer)
    result = await execute_tool(
        ctx, "get_fee_summary", {}, allowed=["get_my_jurisdiction"])
    assert result.get("refused") is True
    assert "error" in result


@pytest.mark.asyncio
async def test_execute_tool_allows_call_inside_allowlist_and_still_validates_args(
        test_officer: OfficerContext, db_session):
    ctx = await ToolContext.create(db_session, test_officer)
    # In the allowlist, but called with a forbidden argument -- the scope
    # check must never substitute for ordinary argument validation.
    result = await execute_tool(
        ctx, "list_applications", {"officer_id": "someone-else"},
        allowed=["list_applications"])
    assert result.get("refused") == "arguments" or result.get("internal_error")


@pytest.mark.asyncio
async def test_execute_tool_allowlist_recognises_tool_aliases(
        test_officer: OfficerContext, db_session):
    # "get_application_detail" (singular) is an alias for
    # "get_application_details" -- the allowlist is keyed on the canonical
    # name, so calling the alias must still be recognised as in-scope.
    ctx = await ToolContext.create(db_session, test_officer)
    result = await execute_tool(
        ctx, "get_application_detail", {"application_number": "2026/0154/28/000001"},
        allowed=["get_application_details"])
    assert result.get("refused") != "scope"


@pytest.mark.asyncio
async def test_execute_tool_allowlist_does_not_weaken_jurisdiction_check(
        test_officer: OfficerContext, secondary_officer: OfficerContext, db_session):
    # A ward the officer does not hold must still be refused for
    # authorization, exactly as before -- the allowlist only narrows WHICH
    # tools are offered, never widens what an allowed tool may return.
    ctx = await ToolContext.create(db_session, test_officer)
    other_ctx = await ToolContext.create(db_session, secondary_officer)
    foreign_wards = [w for w in other_ctx.wards if w not in ctx.wards]
    if not foreign_wards:
        pytest.skip("fixtures share no distinguishing ward to test against")
    result = await execute_tool(
        ctx, "list_applications", {"ward_number": foreign_wards[0]},
        allowed=["list_applications"])
    assert result.get("refused") is True
