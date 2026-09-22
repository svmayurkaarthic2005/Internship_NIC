"""
Controlled domain tools for the LLM agent layer.

The agent never sees the database. It sees the small set of domain-level tools
declared here, each of which is a thin wrapper over a function that already
exists in `backend.services.postgres` (or over pgvector retrieval). Three rules
hold for every tool in this module, and the tests in
`test_agent_tools.py` / `test_agent_layer.py` exist to keep them holding:

  1. **The officer is not a parameter.** `ToolContext` carries the
     authenticated `OfficerContext` and the `AsyncSession`; it is bound by
     `chatbot.py` from the JWT-derived officer and passed to the handler out of
     band. No tool schema declares an officer, jurisdiction, or database
     argument, so there is nothing for the model to set. If a model emits one
     anyway -- prompt injection, or simple confusion -- `validate_args` raises
     rather than silently dropping it, because an officer-shaped argument is
     evidence the turn is trying to act as somebody else.

  2. **Authorization is the existing mechanism, not a new one.** Every handler
     forwards `ctx.officer` into the same `postgres.py` function the
     deterministic handlers call, which goes through
     `get_jurisdiction_filter()`. There is no second permission system here and
     no tool that takes free SQL.

  3. **Geography the model names is a filter, never a grant.** A `ward_number`
     or `block_number` in a tool call is checked against the wards and blocks
     the officer actually holds *before* the query runs. Asking about a ward
     the officer does not hold is refused by name, rather than being passed
     down to return a quietly empty list.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models import Block, OfficerJurisdiction
from backend.schemas import OfficerContext
from backend.services import postgres as pg
from backend.utils.logger import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────────────
class ToolError(Exception):
    """Base for anything a tool refuses to do. Reported back to the model as a
    tool result, not raised out of the agent loop -- the model is expected to
    read the refusal and either fix the call or tell the officer."""


class ToolArgumentError(ToolError):
    """The model called a tool with arguments that are wrong or forbidden."""


class ToolAuthorizationError(ToolError):
    """The call is well-formed but asks about geography outside the officer's
    jurisdiction. Never downgraded to an empty result."""


# Arguments that would amount to the model choosing whose data to read. These
# are refused loudly rather than dropped: see rule 1 above.
_FORBIDDEN_ARGS = frozenset({
    "officer", "officer_id", "officer_name", "employee_id", "email",
    "assigned_officer_id", "user", "user_id", "jurisdiction",
    "jurisdiction_id", "jurisdiction_ids", "jurisdiction_type",
    "district", "district_id", "district_code", "taluk_id", "town_id",
    "ward_id", "block_id", "db", "session", "sql", "query_sql", "where",
})


# ─────────────────────────────────────────────────────────────────────────────
# Tool context — the authenticated half of every call
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ToolContext:
    """What the caller supplies and the model cannot.

    `wards` / `blocks` are the officer's own geography, read once per turn and
    reused by every geography check in that turn.
    """

    db: AsyncSession
    officer: OfficerContext
    wards: List[str] = field(default_factory=list)
    blocks: List[str] = field(default_factory=list)
    calls: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    async def create(cls, db: AsyncSession, officer: OfficerContext) -> "ToolContext":
        wards, blocks = await _officer_geography(db, officer)
        return cls(db=db, officer=officer, wards=wards, blocks=blocks)


async def _officer_geography(db: AsyncSession, officer: OfficerContext):
    """The ward and block numbers the officer actually holds.

    Same shape as `chatbot._officer_geography`, read from the same
    `officer_jurisdictions` rows; kept here so the tool layer has no import
    dependency on the orchestrator it is called from.
    """
    wards: List[str] = []
    blocks: List[str] = []
    try:
        rows = (await db.execute(
            select(OfficerJurisdiction)
            .options(selectinload(OfficerJurisdiction.ward),
                     selectinload(OfficerJurisdiction.block))
            .where(OfficerJurisdiction.officer_id == officer.officer_id)
        )).scalars().all()
        ward_ids = [j.ward_id for j in rows if j.ward_id]
        for j in rows:
            if j.ward and j.ward.ward_number and j.ward.ward_number not in wards:
                wards.append(j.ward.ward_number)
            if j.block and j.block.block_number and j.block.block_number not in blocks:
                blocks.append(j.block.block_number)
        if ward_ids and not blocks:
            for blk in (await db.execute(
                    select(Block).where(Block.ward_id.in_(ward_ids)))).scalars().all():
                if blk.block_number and blk.block_number not in blocks:
                    blocks.append(blk.block_number)
    except Exception:  # pragma: no cover - convenience lookup
        logger.warning("agent_tools: could not read the officer's own ward/block")
    return wards, blocks


# ─────────────────────────────────────────────────────────────────────────────
# Tool specification
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]          # JSON schema, object type
    handler: Callable[..., Any]         # async (ctx, **args) -> dict

    def schema(self) -> Dict[str, Any]:
        """OpenAI / Ollama function-calling shape, as `bind_tools` expects."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _obj(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


_STATUS_ENUM = ["pending", "in_progress", "escalated", "approved", "rejected"]
_CHANNEL_ENUM = ["CSC", "citizen", "sub_registrar"]

_WARD_PROP = {"type": "string",
              "description": "Ward number to narrow to, e.g. '002'. Must be a ward "
                             "the officer holds; any other ward is refused."}
_BLOCK_PROP = {"type": "string",
               "description": "Block number to narrow to, e.g. '0015'. Must be a "
                              "block the officer holds."}


# ─────────────────────────────────────────────────────────────────────────────
# Argument validation
# ─────────────────────────────────────────────────────────────────────────────
def validate_args(spec: ToolSpec, raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Coerce and check the model's arguments against the tool's schema.

    Unknown keys are dropped with a log line -- an 8B model routinely invents a
    plausible extra filter, and failing the whole call over it wastes a turn.
    A key in `_FORBIDDEN_ARGS` is the exception: that one raises.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ToolArgumentError(
            f"{spec.name}: arguments must be an object, got {type(raw).__name__}")

    props: Dict[str, Any] = spec.parameters.get("properties", {})
    required: List[str] = spec.parameters.get("required", [])
    clean: Dict[str, Any] = {}

    for key, value in raw.items():
        lowered = str(key).lower()
        if lowered in _FORBIDDEN_ARGS:
            raise ToolArgumentError(
                f"{spec.name}: '{key}' is not an argument of this tool. Whose data "
                f"is being read is fixed by the signed-in officer and cannot be set "
                f"by a tool call.")
        if key not in props:
            logger.info(f"agent_tools: dropping unknown argument '{key}' for {spec.name}")
            continue
        if value is None or _is_absent(value):
            continue
        clean[key] = _coerce(spec.name, key, value, props[key])

    for key in required:
        if key not in clean:
            raise ToolArgumentError(f"{spec.name}: required argument '{key}' is missing.")
    return clean


# llama3.1:8b fills in every field of a schema whether or not it has a value,
# writing the *string* "None" (or "null", "N/A") into the ones it has nothing
# for. Read literally those are values: `ward_number="None"` produced the
# authorization refusal "Ward None is outside your jurisdiction", which is a
# nonsense answer to a question that named no ward at all. They mean absent, so
# they are treated as absent.
_ABSENT_TOKENS = frozenset({"none", "null", "nil", "n/a", "na", "undefined",
                            "not specified", "unspecified", "any", "all"})


def _is_absent(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in _ABSENT_TOKENS | {""}


# Integer arguments come in two kinds, and an out-of-range value must be
# handled differently for each.
#
#   Semantic  -- `submission_month`, `submission_year`, `min_days_overdue`.
#                These decide WHICH records the answer is about. Silently
#                clamping month 13 to 12 would answer confidently about
#                December when nobody asked about December, so an out-of-range
#                value is refused and the model gets to correct itself.
#
#   Presentational -- `max_results`. This decides how many passages come back,
#                and nothing about what is true. Refusing it cost an officer a
#                whole turn: llama3.1:8b asked search_documents for 10 passages
#                where the ceiling is 8, the call was refused, and the refusal
#                became the answer on screen -- "the max_results value of 10
#                exceeds the maximum allowed of 8". Clamping is both correct
#                and invisible, so these are clamped.
_CLAMPED_ARGS = frozenset({("search_documents", "max_results")})


def _coerce(tool: str, key: str, value: Any, prop: Dict[str, Any]) -> Any:
    want = prop.get("type", "string")
    enum = prop.get("enum")

    if want == "integer":
        try:
            value = int(str(value).strip())
        except (TypeError, ValueError):
            raise ToolArgumentError(f"{tool}: '{key}' must be a whole number, got {value!r}.")
        lo, hi = prop.get("minimum"), prop.get("maximum")
        clampable = (tool, key) in _CLAMPED_ARGS
        if lo is not None and value < lo:
            if not clampable:
                raise ToolArgumentError(f"{tool}: '{key}' must be at least {lo}, got {value}.")
            logger.info(f"agent_tools: clamping {tool}.{key} {value} up to {lo}")
            value = lo
        if hi is not None and value > hi:
            if not clampable:
                raise ToolArgumentError(f"{tool}: '{key}' must be at most {hi}, got {value}.")
            logger.info(f"agent_tools: clamping {tool}.{key} {value} down to {hi}")
            value = hi
        return value

    if want == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "yes", "1"):
            return True
        if text in ("false", "no", "0"):
            return False
        raise ToolArgumentError(f"{tool}: '{key}' must be true or false, got {value!r}.")

    text = str(value).strip()
    if enum:
        # Case-insensitive, because llama3.1 writes "isd" and "Pending" freely.
        for option in enum:
            if text.lower() == str(option).lower():
                return option
        raise ToolArgumentError(
            f"{tool}: '{key}' must be one of {', '.join(map(str, enum))}; got {value!r}.")
    return text


def authorize_geography(ctx: ToolContext, args: Dict[str, Any]) -> None:
    """Refuse a ward or block the officer does not hold.

    The underlying query would already scope to the officer's jurisdiction, so
    a foreign ward returns nothing either way. The point of refusing by name is
    that "no applications in ward 999" reads as a fact about ward 999, and it
    is not one -- the officer simply cannot see it.
    """
    # The recovery hint matters as much as the refusal. A model that has just
    # copied the officer's own ward into block_number -- which is what an 8B
    # does when the ward appears in its system prompt -- needs to be told to
    # drop the filter, not just that this one was rejected.
    hint = ("If the officer did not name a ward or block, call the tool again "
            "with no ward_number and no block_number: their own jurisdiction is "
            "applied automatically.")
    ward = args.get("ward_number")
    if ward and ctx.wards and ward not in ctx.wards:
        raise ToolAuthorizationError(
            f"Ward {ward} is outside {ctx.officer.name}'s jurisdiction. "
            f"Wards held: {', '.join(ctx.wards)}. {hint}")
    block = args.get("block_number")
    if block and ctx.blocks and block not in ctx.blocks:
        raise ToolAuthorizationError(
            f"Block {block} is outside {ctx.officer.name}'s jurisdiction. "
            f"Blocks held: {', '.join(ctx.blocks)}. {hint}")


# ─────────────────────────────────────────────────────────────────────────────
# Result trimming
#   Tool results are read by an 8B model with a bounded context. Whole ORM
#   payloads (every application with every field) crowd out the question. Each
#   list tool therefore returns a count taken from the DB result -- never
#   len() of a truncated page -- plus a bounded sample of rows.
# ─────────────────────────────────────────────────────────────────────────────
_ROW_CAP = 15

_APP_FIELDS = ("application_number", "type", "status", "current_stage", "stage",
               "submission_date", "ward_number", "block_number", "survey_number",
               "applicant_name", "days_pending", "is_overdue", "submission_channel")


def _slim_app(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: row.get(k) for k in _APP_FIELDS if row.get(k) is not None}


def _slim_list(data: Dict[str, Any], key: str, slim=_slim_app) -> Dict[str, Any]:
    rows = data.get(key) or []
    count = data.get("count")
    if count is None:
        count = len(rows)
    out: Dict[str, Any] = {
        "count": count,
        "query_type": data.get("query_type"),
        key: [slim(r) for r in rows[:_ROW_CAP]],
    }
    if len(rows) > _ROW_CAP:
        out["note"] = (f"{count} rows matched; the first {_ROW_CAP} are shown. "
                       f"Report the count as {count}.")
    for extra in ("message", "start_date", "end_date", "total_active"):
        if data.get(extra) is not None:
            out[extra] = data[extra]
    return {k: v for k, v in out.items() if v is not None}


# ─────────────────────────────────────────────────────────────────────────────
# Handlers — each one forwards ctx.officer into the existing query function
# ─────────────────────────────────────────────────────────────────────────────
async def _h_list_applications(ctx: ToolContext, **args) -> Dict[str, Any]:
    authorize_geography(ctx, args)
    data = await pg.get_officer_applications(ctx.db, ctx.officer, **args)
    return _slim_list(data, "applications")


async def _h_count_applications(ctx: ToolContext, **args) -> Dict[str, Any]:
    authorize_geography(ctx, args)
    data = await pg.get_officer_applications(ctx.db, ctx.officer, **args)
    count = data.get("count")
    if count is None:
        count = len(data.get("applications") or [])
    return {"count": count, "filters_applied": args or "none",
            "query_type": data.get("query_type")}


async def _h_pending_applications(ctx: ToolContext, **args) -> Dict[str, Any]:
    authorize_geography(ctx, args)
    data = await pg.get_pending_applications(ctx.db, ctx.officer, **args)
    return _slim_list(data, "applications")


async def _h_overdue_applications(ctx: ToolContext, **args) -> Dict[str, Any]:
    authorize_geography(ctx, args)
    data = await pg.get_overdue_applications(ctx.db, ctx.officer, **args)
    return _slim_list(data, "applications")


async def _h_officer_workload(ctx: ToolContext, **args) -> Dict[str, Any]:
    return await pg.get_officer_workload(ctx.db, ctx.officer)


async def _h_application_detail(ctx: ToolContext, **args) -> Dict[str, Any]:
    number = args["application_number"]
    # The access gate the deterministic handlers use, for the same reason:
    # "no such application" and "not yours" are different answers, and neither
    # of them may be decided by the model.
    access = await pg.lookup_application_access(ctx.db, number, ctx.officer)
    if not access.get("exists"):
        return {"found": False, "reason": "no_such_application",
                "application_number": number,
                "suggestions": [c.get("application_number") for c in access.get("candidates", [])]}
    if not access.get("accessible"):
        return {"found": False, "reason": "outside_jurisdiction",
                "application_number": number,
                "message": (f"Application {number} exists but is outside "
                            f"{ctx.officer.name}'s jurisdiction. Do not report any of "
                            f"its details.")}
    detail = await pg.get_application_detail(ctx.db, number, ctx.officer)
    return detail


async def _h_survey_detail(ctx: ToolContext, **args) -> Dict[str, Any]:
    return await pg.get_survey_detail(ctx.db, args["survey_number"], ctx.officer)


async def _h_survey_lock(ctx: ToolContext, **args) -> Dict[str, Any]:
    return await pg.check_survey_application_lock(ctx.db, args["survey_number"], ctx.officer)


async def _h_field_visits(ctx: ToolContext, **args) -> Dict[str, Any]:
    data = await pg.get_field_visits(ctx.db, ctx.officer, **args)
    return _slim_list(data, "field_visits", slim=lambda r: r)


async def _h_visit_plan(ctx: ToolContext, **args) -> Dict[str, Any]:
    authorize_geography(ctx, args)
    return await pg.get_visit_plan(ctx.db, ctx.officer, **args)


async def _h_last_application(ctx: ToolContext, **args) -> Dict[str, Any]:
    authorize_geography(ctx, args)
    return await pg.get_last_application(ctx.db, ctx.officer, **args)


async def _h_fee_summary(ctx: ToolContext, **args) -> Dict[str, Any]:
    return await pg.get_fee_summary(ctx.db, ctx.officer, **args)


async def _h_jurisdiction(ctx: ToolContext, **args) -> Dict[str, Any]:
    return {
        "officer_name": ctx.officer.name,
        "employee_id": ctx.officer.employee_id,
        "designation": ctx.officer.designation,
        "stage": ctx.officer.officer_stage,
        "jurisdiction_type": ctx.officer.jurisdiction_type,
        "jurisdiction_name": ctx.officer.jurisdiction_name,
        "wards": ctx.wards,
        "blocks": ctx.blocks,
    }


def _squeeze(text: str) -> str:
    """Passage text without layout padding -- runs of spaces and blank lines cost
    prompt tokens (and CPU seconds) and carry no meaning."""
    import re as _re
    return _re.sub(r"\n\s*\n+", "\n", _re.sub(r"[ \t]{2,}", " ", text)).strip()


async def _h_search_documents(ctx: ToolContext, **args) -> Dict[str, Any]:
    """Reference knowledge -- rules, workflow, service codes, fee schedule.

    This is the only tool that does not touch the register, and the only one
    with no jurisdiction dimension: the corpus is public policy documentation,
    identical for every officer.
    """
    from backend.services.pgvector_store import similarity_search

    query = args["query"]
    n = args.get("max_results", 4)
    try:
        hits = await asyncio.to_thread(similarity_search, query, n, None)
    except Exception as exc:  # pragma: no cover - retrieval is best-effort
        logger.error(f"agent_tools: document search failed: {exc}")
        return {"found": False, "error": "document search unavailable"}
    return {
        "found": bool(hits),
        "passages": [
            {
                "source": (h.get("metadata") or {}).get("document_name"),
                "content": _squeeze(h.get("content") or "")[:2200],
            }
            for h in hits
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────
TOOLS: List[ToolSpec] = [
    ToolSpec(
        name="list_applications",
        description=(
            "List the signed-in officer's applications from the register, optionally "
            "filtered by status, type (ISD/NISD/MERGE), submission channel, ward, "
            "block, or submission month/year. Use for 'show me...', 'which "
            "applications...'. Returns real rows; never invent application numbers. "
            "Called with NO filter it returns only what is live at this officer's "
            "desk right now, not their whole history -- to search the register as a "
            "whole, always pass a status, type, channel or period."),
        parameters=_obj({
            "status": {"type": "string", "enum": _STATUS_ENUM},
            "application_type": {"type": "string"},
            "submission_channel": {"type": "string", "enum": _CHANNEL_ENUM},
            "submission_year": {"type": "integer", "minimum": 2000, "maximum": 2100},
            "submission_month": {"type": "integer", "minimum": 1, "maximum": 12},
            "is_overdue": {"type": "boolean"},
            "ward_number": _WARD_PROP,
            "block_number": _BLOCK_PROP,
        }),
        handler=_h_list_applications,
    ),
    ToolSpec(
        name="count_applications",
        description=(
            "Count the signed-in officer's applications matching a filter, without "
            "listing them. Use for 'how many...'. The number in your answer must be "
            "the number this tool returns. With NO filter it counts only what is "
            "live at this officer's desk; for a total across the register pass the "
            "status, type, channel or period being asked about, or call "
            "get_officer_workload for the workload summary."),
        parameters=_obj({
            "status": {"type": "string", "enum": _STATUS_ENUM},
            "application_type": {"type": "string"},
            "submission_channel": {"type": "string", "enum": _CHANNEL_ENUM},
            "submission_year": {"type": "integer", "minimum": 2000, "maximum": 2100},
            "submission_month": {"type": "integer", "minimum": 1, "maximum": 12},
            "is_overdue": {"type": "boolean"},
            "ward_number": _WARD_PROP,
            "block_number": _BLOCK_PROP,
        }),
        handler=_h_count_applications,
    ),
    ToolSpec(
        name="get_pending_applications",
        description=(
            "The officer's working queue -- applications still open (pending, in "
            "progress, escalated) at their desk. Use for 'what is pending', 'my "
            "queue', 'what do I have to do'."),
        parameters=_obj({
            "application_type": {"type": "string"},
            "submission_channel": {"type": "string", "enum": _CHANNEL_ENUM},
            "ward_number": _WARD_PROP,
            "block_number": _BLOCK_PROP,
        }),
        handler=_h_pending_applications,
    ),
    ToolSpec(
        name="get_overdue_applications",
        description=(
            "Applications past their service-level deadline in the officer's "
            "jurisdiction. Use for 'overdue', 'delayed', 'late', 'breaching SLA'."),
        parameters=_obj({
            "application_type": {"type": "string"},
            "min_days_overdue": {"type": "integer", "minimum": 0, "maximum": 3650},
            "ward_number": _WARD_PROP,
            "block_number": _BLOCK_PROP,
        }),
        handler=_h_overdue_applications,
    ),
    ToolSpec(
        name="get_officer_workload",
        description=(
            "Summary of the signed-in officer's CURRENT OPEN workload: how many "
            "applications are live at their desk right now, split by "
            "ISD/NISD/MERGE, plus how many are overdue and how many await a "
            "field visit. These are OPEN files only -- it does NOT count "
            "approved, rejected or otherwise decided applications, so never use "
            "it to answer 'how many approved/rejected'. For a count by status, "
            "use count_applications with that status."),
        parameters=_obj({}),
        handler=_h_officer_workload,
    ),
    ToolSpec(
        name="get_application_details",
        description=(
            "Everything on record for one application, given its full application "
            "number (e.g. '2025/0154/28/000001'): status, stage, applicant, survey "
            "number, dates, sub-divisions, channel. Refuses applications outside the "
            "officer's jurisdiction."),
        parameters=_obj({
            "application_number": {
                "type": "string",
                "description": "Full application number, e.g. 2025/0154/28/000001."},
        }, required=["application_number"]),
        handler=_h_application_detail,
    ),
    ToolSpec(
        name="get_survey_details",
        description=(
            "Details of a survey number or sub-division (e.g. '1355' or '1355/1B12'): "
            "its ward, block, town, taluk, district, patta number and owners."),
        parameters=_obj({
            "survey_number": {"type": "string",
                              "description": "Survey number, optionally with a "
                                             "sub-division tail, e.g. '1355/1B12'."},
        }, required=["survey_number"]),
        handler=_h_survey_detail,
    ),
    ToolSpec(
        name="check_survey_application_lock",
        description=(
            "Whether a new application may be filed on a survey number right now. "
            "Mutation is synchronous: one live application locks the whole survey "
            "number, across sub-divisions."),
        parameters=_obj({
            "survey_number": {"type": "string"},
        }, required=["survey_number"]),
        handler=_h_survey_lock,
    ),
    ToolSpec(
        name="get_field_visits",
        description=(
            "Field visits (inspections) for the officer, optionally for one "
            "application or filtered by status. Use for 'is a field visit "
            "scheduled', 'my inspections'."),
        parameters=_obj({
            "status_filter": {"type": "string",
                              "enum": ["scheduled", "completed", "cancelled", "pending"]},
            "application_number": {"type": "string"},
            "to_be_visited_only": {"type": "boolean"},
        }),
        handler=_h_field_visits,
    ),
    ToolSpec(
        name="get_visit_plan",
        description=(
            "What the officer should go and inspect next and where: overdue visits "
            "first, then applications with no visit booked, each with its ward and "
            "block. Use for 'which application should I field visit tomorrow'."),
        parameters=_obj({
            "ward_number": _WARD_PROP,
            "block_number": _BLOCK_PROP,
        }),
        handler=_h_visit_plan,
    ),
    ToolSpec(
        name="get_last_application",
        description=(
            "The officer's most recently acted-on application, optionally restricted "
            "to a status or type. Use for 'my last application', 'my previous "
            "approved application'."),
        parameters=_obj({
            "status": {"type": "string", "enum": _STATUS_ENUM},
            "application_type": {"type": "string"},
            "ward_number": _WARD_PROP,
            "block_number": _BLOCK_PROP,
        }),
        handler=_h_last_application,
    ),
    ToolSpec(
        name="get_fee_summary",
        description=(
            "Aggregate of fees / service charges collected across the officer's "
            "applications: total, mean, payment modes, and how many carry no fee "
            "record."),
        parameters=_obj({
            "application_type": {"type": "string"},
        }),
        handler=_h_fee_summary,
    ),
    ToolSpec(
        name="get_my_jurisdiction",
        description=(
            "Who the signed-in officer is and what they cover: name, designation, "
            "workflow stage, and the wards and blocks in their jurisdiction. Call "
            "this when the officer asks about themselves or about which wards they "
            "may query."),
        parameters=_obj({}),
        handler=_h_jurisdiction,
    ),
    ToolSpec(
        name="search_documents",
        description=(
            "Search the SIS reference corpus -- survey manual, workflow guide, land "
            "rules, service-code and fee tables, FAQs. Use for questions about rules, "
            "procedure, definitions and timelines rather than about specific "
            "applications. This is reference knowledge, not the live register."),
        parameters=_obj({
            "query": {"type": "string",
                      "description": "What to look up, in English."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 8},
        }, required=["query"]),
        handler=_h_search_documents,
    ),
]

TOOLS_BY_NAME: Dict[str, ToolSpec] = {t.name: t for t in TOOLS}


def tool_schemas() -> List[Dict[str, Any]]:
    """The tool list in the shape `ChatOllama.bind_tools` accepts."""
    return [t.schema() for t in TOOLS]


async def execute_tool(ctx: ToolContext, name: str, raw_args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate and run one tool call.

    Never raises for a bad call: the refusal is the result, so the model can
    read it and correct itself inside the same turn. `ctx.calls` records what
    happened, which is what the caller logs and what the tests assert on.
    """
    record: Dict[str, Any] = {"tool": name, "arguments": raw_args or {}}
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        record["error"] = f"unknown tool '{name}'"
        ctx.calls.append(record)
        return {"error": record["error"],
                "available_tools": sorted(TOOLS_BY_NAME)}
    try:
        args = validate_args(spec, raw_args)
        record["validated_arguments"] = args
        result = await spec.handler(ctx, **args)
        record["ok"] = True
        ctx.calls.append(record)
        return result
    except ToolAuthorizationError as exc:
        logger.warning(f"agent_tools: authorization refusal on {name}: {exc}")
        record["error"] = str(exc)
        record["refused"] = "authorization"
        ctx.calls.append(record)
        return {"error": str(exc), "refused": True}
    except ToolArgumentError as exc:
        # A malformed argument is the assistant's own fault, not a fact about
        # the officer's records or their permissions. It is handed back so the
        # model can correct itself inside the turn, and marked so it is never
        # described to the officer: an officer who reads "the max_results value
        # of 10 exceeds the maximum allowed of 8" has learnt nothing about
        # their work and everything about our plumbing.
        logger.warning(f"agent_tools: bad arguments for {name}: {exc}")
        record["error"] = str(exc)
        record["refused"] = "arguments"
        ctx.calls.append(record)
        return {
            "internal_error": True,
            "detail": str(exc),
            "instruction": (
                "This is a fault in how the tool was called, not a refusal and "
                "not an absence of records. Call the tool again with corrected "
                "arguments, dropping the offending one entirely if the officer "
                "did not ask for it. Never mention this tool, its arguments or "
                "this message in your answer to the officer."
            ),
        }
    except Exception as exc:  # a query blew up -- say so, do not invent
        logger.error(f"agent_tools: {name} failed: {exc}", exc_info=True)
        record["error"] = repr(exc)
        ctx.calls.append(record)
        return {"error": f"The {name} query failed. Tell the officer the data could "
                         f"not be retrieved; do not answer from memory."}
