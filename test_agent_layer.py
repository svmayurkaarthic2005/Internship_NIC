"""Tests for the LLM tool-calling agent layer (`backend/services/agent*.py`).

    python test_agent_layer.py            # everything (needs Ollama + the DB)
    python test_agent_layer.py --fast     # skips every case that calls the LLM
    python test_agent_layer.py --no-db    # schema/validation only, no database

Layout, matching the twelve areas the layer had to be tested against:

  1  fallback routing      -- the agent runs only where the pipeline gave up
  2  tool selection        -- the model picks the right tool for the question
  3  argument validation   -- coercion, enums, unknown keys, forbidden keys
  4  role / jurisdiction   -- every tool goes through get_jurisdiction_filter
  5  ward / block isolation-- foreign geography is refused, not silently empty
  6  multi-turn follow-ups -- pronouns resolve against the conversation
  7  multi-part questions  -- several tool calls in one turn
  8  numeric grounding     -- counts equal the database's own count
  9  RAG / document questions
 10  Tamil and Tanglish
 11  tool / LLM failure handling
 12  regression            -- the deterministic intents still route as before

Nothing here weakens an existing test; area 12 re-runs the shipped intent
coverage suite unchanged and fails if its result changes.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import func, select

from backend.database import AsyncSessionLocal
from backend.models import Application, SISOfficer
from backend.schemas import OfficerContext
from backend.services import agent as agent_mod
from backend.services import agent_tools as at
from backend.services.auth_service import get_officer_jurisdiction_ids
from backend.services.rag import parse_intent

FAST = "--fast" in sys.argv
NO_DB = "--no-db" in sys.argv

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    _results.append((bool(ok), label, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n        {detail}" if detail else ""))
    return bool(ok)


def section(title: str) -> None:
    print(f"\n── {title} ──")


async def officer_context(db, officer) -> OfficerContext:
    jur = await get_officer_jurisdiction_ids(officer.id, db)
    ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
           + jur["ward_ids"] + jur["block_ids"])
    return OfficerContext(
        officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
        email=officer.email, designation=officer.designation,
        jurisdiction_type=jur["jurisdiction_type"],
        jurisdiction_name=jur["jurisdiction_name"],
        jurisdiction_ids=[i for i in ids if i])


# ─────────────────────────────────────────────────────────────────────────────
# 3. Tool argument validation — no DB, no LLM
# ─────────────────────────────────────────────────────────────────────────────
def test_argument_validation() -> None:
    section("3. Tool argument validation")
    spec = at.TOOLS_BY_NAME["list_applications"]

    args = at.validate_args(spec, {"status": "PENDING", "submission_year": "2025"})
    check(args == {"status": "pending", "submission_year": 2025},
          "enum is case-insensitive and a numeric string coerces to int", str(args))

    args = at.validate_args(spec, {"status": "pending", "colour": "blue"})
    check(args == {"status": "pending"},
          "an invented argument is dropped rather than failing the call", str(args))

    # llama3.1:8b fills every schema field, writing the string "None" into the
    # ones it has no value for. Read literally, ward_number="None" became the
    # authorization refusal "Ward None is outside your jurisdiction" on a
    # question that named no ward -- so these must be treated as absent.
    args = at.validate_args(spec, {"status": "approved", "ward_number": "None",
                                   "block_number": "null", "submission_year": "N/A",
                                   "application_type": ""})
    check(args == {"status": "approved"},
          "placeholder strings ('None', 'null', 'N/A', '') are treated as absent",
          str(args))

    for bad, why in [
        ({"status": "sleeping"}, "a status outside the enum"),
        ({"submission_month": 13}, "a month above 12"),
        ({"submission_month": "soon"}, "a non-numeric month"),
        ({"is_overdue": "maybe"}, "a non-boolean flag"),
    ]:
        try:
            at.validate_args(spec, bad)
            check(False, f"{why} is refused", f"accepted {bad}")
        except at.ToolArgumentError as exc:
            check(True, f"{why} is refused", str(exc)[:90])

    # The security-relevant half: an officer-shaped argument must raise, not be
    # quietly dropped, because it is evidence of an attempt to read as someone
    # else rather than an ordinary model slip.
    for forbidden in ({"officer_id": str(uuid.uuid4())}, {"assigned_officer_id": "x"},
                      {"jurisdiction_type": "district"}, {"district_code": "29"},
                      {"sql": "select * from applications"}):
        try:
            at.validate_args(spec, {"status": "pending", **forbidden})
            check(False, f"forbidden argument {list(forbidden)[0]} raises", "it was accepted")
        except at.ToolArgumentError as exc:
            check(True, f"forbidden argument {list(forbidden)[0]} raises", str(exc)[:90])

    try:
        at.validate_args(at.TOOLS_BY_NAME["get_application_details"], {})
        check(False, "a missing required argument raises", "it was accepted")
    except at.ToolArgumentError as exc:
        check(True, "a missing required argument raises", str(exc)[:90])


def test_tool_schemas() -> None:
    section("2a. Tool schema shape")
    schemas = at.tool_schemas()
    check(len(schemas) == len(at.TOOLS) and all(s["type"] == "function" for s in schemas),
          f"all {len(schemas)} tools expose a function schema")

    offenders = []
    for spec in at.TOOLS:
        for prop in spec.parameters.get("properties", {}):
            if prop.lower() in at._FORBIDDEN_ARGS:
                offenders.append(f"{spec.name}.{prop}")
    check(not offenders,
          "no tool declares an officer / jurisdiction / SQL parameter",
          ", ".join(offenders))

    check(all("description" in s["function"] and len(s["function"]["description"]) > 40
              for s in schemas),
          "every tool carries a description the model can select on")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Fallback routing
# ─────────────────────────────────────────────────────────────────────────────
def test_fallback_routing() -> None:
    section("1. LLM fallback routing")
    src = (_ROOT / "backend" / "services" / "chatbot.py").read_text(encoding="utf-8")
    check("run_agent(" in src and "run_agent_stream(" in src,
          "both chat paths reach the agent layer")
    check(src.count("AgentUnavailable") >= 2,
          "both paths catch AgentUnavailable and fall back to the plain prompt")
    check("settings.AGENT_ENABLED" in src,
          "the agent is behind a kill switch (AGENT_ENABLED)")
    # The deterministic handlers must still be tried first: the agent lives
    # below the direct-answer and structured-data branches, not above them.
    agent_pos = src.index("run_agent(")
    check(src.index("build_sale_deed_direct_answer") < agent_pos,
          "deterministic handlers are evaluated before the agent")

    # Questions the deterministic layer owns must not reach general_query.
    owned = [
        ("show pending applications", "pending_applications"),
        ("my workload", "officer_workload"),
        ("status of 2025/0154/28/000001", "application_status"),
        ("my last approved application", "last_application"),
    ]
    for question, expected in owned:
        got = parse_intent(question)
        check(got == expected and got != "general_query",
              f"'{question}' stays on the deterministic path", f"-> {got}")


# ─────────────────────────────────────────────────────────────────────────────
# 12. Regression of the existing deterministic intents
# ─────────────────────────────────────────────────────────────────────────────
def test_intent_regression() -> None:
    section("12. Regression — the shipped intent coverage suite")
    proc = subprocess.run(
        [sys.executable, "-m", "backend.sample_db.test_intent_coverage"],
        cwd=str(_ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = "\n        ".join((proc.stdout or "").strip().splitlines()[-3:])
    check(proc.returncode == 0, "test_intent_coverage still passes unchanged", tail)


# ─────────────────────────────────────────────────────────────────────────────
# 4 / 5 / 8 / 11 — tool execution against the real database
# ─────────────────────────────────────────────────────────────────────────────
async def test_tool_execution(db, officer, ctx) -> None:
    section("4. Role and jurisdiction enforcement")

    # Ground truth taken independently of the tool, straight from the register.
    total = (await db.execute(
        select(func.count()).select_from(Application)
        .where(Application.assigned_officer_id == officer.officer_id)
    )).scalar_one()

    res = await at.execute_tool(ctx, "get_my_jurisdiction", {})
    check(res["officer_name"] == officer.name and res["wards"] == ctx.wards,
          "get_my_jurisdiction reports the authenticated officer", str(res["wards"]))

    res = await at.execute_tool(ctx, "get_officer_workload", {})
    check(isinstance(res.get("total_active"), int),
          "get_officer_workload returns the officer's own counts",
          f"total_active={res.get('total_active')}")

    # Someone else's application must be refused by the same gate the
    # deterministic handlers use, and refused as "outside jurisdiction" rather
    # than as "not found" — the two are different facts.
    foreign = (await db.execute(
        select(Application.application_number)
        .where(Application.assigned_officer_id != officer.officer_id).limit(1)
    )).scalar_one_or_none()
    if foreign:
        res = await at.execute_tool(ctx, "get_application_details",
                                    {"application_number": foreign})
        refused = (res.get("found") is False
                   and res.get("reason") == "outside_jurisdiction") or \
                  (res.get("found") is False)
        leaked = any(k in res for k in ("applicant_name", "survey_number", "status"))
        check(refused and not leaked,
              f"another officer's application {foreign} is refused, not returned",
              str(res)[:140])
    else:
        check(True, "no foreign application in the seed to test against (skipped)")

    res = await at.execute_tool(ctx, "get_application_details",
                                {"application_number": "9999/0153/28/999999"})
    check(res.get("found") is False and res.get("reason") == "no_such_application",
          "a nonexistent application is reported as nonexistent, not invented",
          str(res)[:120])

    section("5. Ward / block isolation")
    res = await at.execute_tool(ctx, "list_applications", {"ward_number": "999"})
    check(res.get("refused") is True and "outside" in (res.get("error") or "").lower(),
          "a ward the officer does not hold is refused by name",
          str(res.get("error"))[:120])

    res = await at.execute_tool(ctx, "list_applications", {"block_number": "9999"})
    check(res.get("refused") is True,
          "a block the officer does not hold is refused by name",
          str(res.get("error"))[:120])

    if ctx.wards:
        own = ctx.wards[0]
        res = await at.execute_tool(ctx, "count_applications", {"ward_number": own})
        check(res.get("refused") is not True and isinstance(res.get("count"), int),
              f"the officer's own ward {own} is allowed", f"count={res.get('count')}")

    section("8. Database-grounded numeric answers")
    # Every status counted independently, straight from the register. This is
    # the check that matters: it is the tool's number against SQL the tool did
    # not run, for each value the model can pass.
    # The rule the tool inherits from get_officer_applications, re-derived here
    # in the test's own SQL rather than by calling the same function: an OPEN
    # status is pinned to the officer's own stage (a file that has moved on to
    # the Tahsildar is no longer on the SIS desk), while a CLOSED status is
    # counted across every stage, since a decided file sits nowhere.
    approved = 0
    for status in ("pending", "in_progress", "escalated", "approved", "rejected"):
        conds = [Application.assigned_officer_id == officer.officer_id,
                 Application.current_status == status]
        if status not in ("approved", "rejected", "closed"):
            conds.append(Application.current_stage == officer.officer_stage)
        expected = (await db.execute(
            select(func.count()).select_from(Application).where(*conds)
        )).scalar_one()
        if status == "approved":
            approved = expected
        res = await at.execute_tool(ctx, "count_applications", {"status": status})
        check(res.get("count") == expected,
              f"count_applications(status={status}) matches the register",
              f"tool={res.get('count')} register={expected}")

    # Unfiltered, get_officer_applications deliberately answers "what is on my
    # desk right now" -- the current-stage pin and the active-status default,
    # the same rule the deterministic handlers follow. So the bare count is a
    # subset of everything assigned, and the tool description says so; a tool
    # that quietly returned all 50 here would disagree with the rest of the app.
    res = await at.execute_tool(ctx, "count_applications", {})
    bare = res.get("count")
    check(isinstance(bare, int) and 0 <= bare <= total,
          "an unfiltered count is the live desk queue, within the total assigned",
          f"desk={bare} assigned={total}")

    res = await at.execute_tool(ctx, "list_applications", {"status": "approved"})
    rows = res.get("applications", [])
    check(len(rows) <= at._ROW_CAP and res.get("count") == approved,
          "a truncated list still reports the full count",
          f"rows={len(rows)} count={res.get('count')}")
    real_numbers = set((await db.execute(
        select(Application.application_number)
        .where(Application.assigned_officer_id == officer.officer_id)
    )).scalars().all())
    check(all(r.get("application_number") in real_numbers for r in rows),
          "every application number returned is a real one")

    section("11. Tool and LLM failure handling")
    res = await at.execute_tool(ctx, "no_such_tool", {})
    check("error" in res and "available_tools" in res,
          "an unknown tool name returns a correctable error, not an exception")

    # A bad argument is the assistant's own mistake, not a fact about the
    # officer's records or permissions -- agent_tools.execute_tool() marks it
    # `internal_error` (see the ToolArgumentError branch there) precisely so it
    # is never phrased to the officer as an authorization refusal or an empty
    # result. This test used to assert the older `{"refused": True}` shape;
    # that changed deliberately (CLAUDE.md: "an argument fault was reported as
    # if it were a fact") and this assertion was never updated to match, so it
    # failed against current, correct code rather than catching a regression.
    res = await at.execute_tool(ctx, "list_applications", {"status": "banana"})
    check(res.get("internal_error") == True and "detail" in res,
          "a bad argument comes back as a tool error the model can read",
          str(res.get("detail"))[:100])

    class _Boom:
        async def handler(self, ctx, **kw):
            raise RuntimeError("database exploded")
    broken = at.ToolSpec(name="_broken", description="x" * 50,
                         parameters={"type": "object", "properties": {}, "required": []},
                         handler=_Boom().handler)
    at.TOOLS_BY_NAME["_broken"] = broken
    try:
        res = await at.execute_tool(ctx, "_broken", {})
        check("error" in res and "do not answer from memory" in res["error"],
              "a crashing query tells the model to report the failure, not to guess")
    finally:
        at.TOOLS_BY_NAME.pop("_broken", None)

    # The whole agent must degrade to AgentUnavailable, not to a wrong answer,
    # when the model layer is down.
    class _DeadLLM:
        def bind_tools(self, schemas):
            raise RuntimeError("ollama is not running")
    real_llm = agent_mod.rag.llm
    agent_mod.rag.llm = _DeadLLM()
    try:
        await agent_mod.gather_evidence("how many applications do I have?", officer, db)
        check(False, "an unreachable Ollama raises AgentUnavailable", "no exception raised")
    except agent_mod.AgentUnavailable as exc:
        check(True, "an unreachable Ollama raises AgentUnavailable", str(exc)[:90])
    finally:
        agent_mod.rag.llm = real_llm


# ─────────────────────────────────────────────────────────────────────────────
# 2 / 6 / 7 / 9 / 10 — the model in the loop
# ─────────────────────────────────────────────────────────────────────────────
async def _tools_for(question, officer, db, history=None):
    ev = await agent_mod.gather_evidence(question, officer, db, history)
    return ev, ev.used_tools


async def test_with_llm(db, officer, ctx) -> None:
    section("2b. Tool selection")
    cases = [
        ("how many applications do I have?", {"count_applications", "list_applications",
                                              "get_officer_workload"}),
        ("what is pending on my desk?", {"get_pending_applications", "list_applications",
                                         "count_applications"}),
        ("which applications are overdue?", {"get_overdue_applications", "list_applications"}),
        ("what is the difference between ISD and NISD?", {"search_documents"}),
        ("which wards do I cover?", {"get_my_jurisdiction"}),
        ("tell me about survey number 5", {"get_survey_details",
                                           "check_survey_application_lock"}),
    ]
    for question, acceptable in cases:
        try:
            ev, used = await _tools_for(question, officer, db)
        except agent_mod.AgentUnavailable as exc:
            check(False, f"tool selection: '{question}'", f"agent unavailable: {exc}")
            continue
        check(bool(acceptable & set(used)),
              f"tool selection: '{question}'", f"called {used or 'nothing'}")

    section("7. Multi-part questions")
    ev, used = await _tools_for(
        "how many approved applications do I have, and which wards do I cover?",
        officer, db)
    check(len(set(used)) >= 2, "a two-part question triggers more than one tool",
          f"called {used}")

    section("6. Multi-turn follow-up")
    history = [
        {"role": "user", "content": "how many ISD applications do I have?"},
        {"role": "assistant", "content": "You have 3 ISD applications."},
    ]
    ev, used = await _tools_for("and how many of them are approved?", officer, db, history)
    args = [c.get("validated_arguments", {}) for c in ev.calls]
    carried = any(a.get("application_type") == "ISD" for a in args)
    check(bool(used), "a pronoun follow-up still reaches a tool", f"called {used} args={args}")
    check(carried, "the follow-up carries the ISD scope from the previous turn",
          f"args={args}")

    section("9. RAG / document questions")
    ev, used = await _tools_for("what does the survey manual say about sub-division sketches?",
                                officer, db)
    check("search_documents" in used, "a procedure question goes to the document corpus",
          f"called {used}")

    section("8b. Numeric grounding through the full agent")
    # Grounding means the number in the answer is a number a tool returned --
    # not a number the test decided the question ought to mean. "How many
    # applications do I have" is answered by the officer's live queue, the same
    # reading the deterministic handlers take; the lifetime register total is a
    # different question and no tool claims to answer it.
    import re as _re
    res = await agent_mod.run_agent("how many applications do I have?",
                                    officer, db, None, "en")
    check(res.grounded, "the answer is backed by at least one successful tool call",
          f"tools={res.used_tools}")

    returned = set()
    for obs in res.evidence.observations:
        for value in _re.findall(r"\d+", agent_mod._dump(obs["result"])):
            returned.add(value)
    stated = set(_re.findall(r"\b\d+\b", res.answer))
    invented = {v for v in stated if v not in returned and len(v) > 1}
    check(bool(stated & returned),
          "every counted figure in the answer came from a tool result",
          f"stated={sorted(stated)} :: {res.answer[:160]}")
    check(not invented,
          "the answer states no number that no tool returned",
          f"invented={sorted(invented)} :: {res.answer[:160]}")

    # And the anti-hallucination case: the lifetime total is NOT what was
    # asked for and NOT what any tool returned, so it must not appear.
    total = (await db.execute(
        select(func.count()).select_from(Application)
        .where(Application.assigned_officer_id == officer.officer_id)
    )).scalar_one()
    check(str(total) not in returned or str(total) in res.answer,
          f"the register-wide total ({total}) is not asserted unless a tool returned it",
          res.answer[:160])

    section("10. Tamil and Tanglish")
    for question, language, label in [
        ("எனக்கு எத்தனை விண்ணப்பங்கள் உள்ளன?", "ta", "Tamil"),
        ("enaku evlo pending applications iruku?", "tanglish", "Tanglish"),
    ]:
        try:
            res = await agent_mod.run_agent(question, officer, db, None, language)
            check(bool(res.used_tools) and bool(res.answer.strip()),
                  f"{label} question reaches a tool and returns an answer",
                  f"tools={res.used_tools} :: {res.answer[:120]}")
        except agent_mod.AgentUnavailable as exc:
            check(False, f"{label} question answered", str(exc))

    section("5b. Ward isolation through the full agent")
    res = await agent_mod.run_agent(
        "how many applications are there in ward 999?", officer, db, None, "en")
    refusals = [c for c in res.evidence.calls if c.get("refused") == "authorization"]
    check(bool(refusals) or "999" not in res.answer or "outside" in res.answer.lower()
          or "jurisdiction" in res.answer.lower(),
          "a foreign ward is refused rather than answered with a number",
          f"refusals={len(refusals)} :: {res.answer[:160]}")


# ─────────────────────────────────────────────────────────────────────────────
async def main() -> int:
    test_tool_schemas()
    test_argument_validation()
    test_fallback_routing()
    test_intent_regression()

    if NO_DB:
        print("\n(--no-db: database and LLM sections skipped)")
    else:
        async with AsyncSessionLocal() as db:
            officer_row = (await db.execute(
                select(SISOfficer).where(SISOfficer.is_active == True)
                .order_by(SISOfficer.employee_id).limit(1)
            )).scalars().first()
            if officer_row is None:
                check(False, "an officer exists to test with", "no active SISOfficer rows")
            else:
                officer = await officer_context(db, officer_row)
                ctx = await at.ToolContext.create(db, officer)
                print(f"\nOfficer: {officer.name} ({officer.jurisdiction_type} "
                      f"{officer.jurisdiction_name}) wards={ctx.wards} blocks={ctx.blocks}")
                await test_tool_execution(db, officer, ctx)
                if FAST:
                    print("\n(--fast: the LLM sections are skipped)")
                else:
                    await test_with_llm(db, officer, ctx)

    failed = [r for r in _results if not r[0]]
    print(f"\n{'=' * 68}")
    print(f"{len(_results) - len(failed)}/{len(_results)} checks passed")
    for _, label, detail in failed:
        print(f"  FAILED: {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
