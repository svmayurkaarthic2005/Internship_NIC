"""
Controlled domain tools for the LLM agent layer.

The agent never sees the database. It sees the controlled set of domain-level tools
declared here, each of which is a thin wrapper over a function in
`backend.services.postgres` (or pgvector retrieval).

Rules enforced across all tools:
  1. The officer is not a parameter. Auth context is bound out-of-band.
  2. Authorization is enforced through postgres jurisdiction filters.
  3. Geography arguments (ward, block) are strictly verified against the officer's jurisdiction.
  4. Arguments are validated using strict Pydantic schemas.
  5. Personal identifiable information (PII) is masked/redacted.
  6. Results are returned as structured evidence packets.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
    ValidationError as PydanticValidationError,
)
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
    """Base for anything a tool refuses to do."""


class ToolArgumentError(ToolError):
    """The model called a tool with arguments that are wrong or forbidden."""


class ToolAuthorizationError(ToolError):
    """The call is well-formed but asks about geography outside the officer's jurisdiction."""


# Forbidden parameters: choosing whose data to read is never allowed
_FORBIDDEN_ARGS = frozenset({
    "officer", "officer_id", "officer_name", "employee_id", "email",
    "assigned_officer_id", "user", "user_id", "jurisdiction",
    "jurisdiction_id", "jurisdiction_ids", "jurisdiction_type",
    "district", "district_id", "district_code", "taluk_id", "town_id",
    "ward_id", "block_id", "db", "session", "sql", "query_sql", "where",
})

_ABSENT_TOKENS = frozenset({
    "none", "null", "nil", "n/a", "na", "undefined",
    "not specified", "unspecified", "any", "all", ""
})

_CLAMPED_ARGS = frozenset({("search_documents", "max_results")})
_APP_NUMBER_SHAPE_RE = re.compile(r"^\d{4}/")
_STATUS_ENUM = ["pending", "in_progress", "escalated", "approved", "rejected"]
_CHANNEL_ENUM = ["CSC", "citizen", "sub_registrar"]


def _is_absent(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in _ABSENT_TOKENS


# ─────────────────────────────────────────────────────────────────────────────
# Tool Context
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ToolContext:
    """Authenticated context supplied by the server."""
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
    except Exception:
        logger.warning("agent_tools: could not read the officer's own ward/block")
    return wards, blocks


# ─────────────────────────────────────────────────────────────────────────────
# PII Protection & Structured Evidence Formatting
# ─────────────────────────────────────────────────────────────────────────────
def mask_pii_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Mask citizen personal identifiers to prevent sensitive data leakage."""
    if not isinstance(record, dict):
        return record
    sanitized = dict(record)
    
    # Mask mobile number
    for m_key in ("applicant_mobile", "mobile_number", "mobile"):
        if m_key in sanitized and sanitized[m_key]:
            mob = str(sanitized[m_key]).strip()
            if len(mob) >= 4:
                sanitized[m_key] = f"{'X' * (len(mob) - 4)}{mob[-4:]}"
            else:
                sanitized[m_key] = "XXXX"

    # Mask Aadhaar number
    for a_key in ("applicant_aadhaar", "aadhaar_number", "aadhaar"):
        if a_key in sanitized and sanitized[a_key]:
            aadh = str(sanitized[a_key]).strip()
            if len(aadh) >= 4:
                sanitized[a_key] = f"{'X' * (len(aadh) - 4)}{aadh[-4:]}"
            else:
                sanitized[a_key] = "XXXXXXXX"

    # Redact permanent address or full street address
    for addr_key in ("applicant_permanent_address", "permanent_address"):
        if addr_key in sanitized and sanitized[addr_key]:
            sanitized[addr_key] = "[REDACTED FOR PRIVACY]"

    # Redact Date of Birth
    for dob_key in ("applicant_dob", "dob"):
        if dob_key in sanitized and sanitized[dob_key]:
            sanitized[dob_key] = "[REDACTED]"

    return sanitized


def format_tool_evidence(
    operation: str,
    source: str,
    records: Any,
    count: Optional[int] = None,
    filters: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    officer: Optional[OfficerContext] = None,
) -> Dict[str, Any]:
    """Standardized evidence format across all tools."""
    success = error is None
    if count is None:
        if isinstance(records, list):
            count = len(records)
        elif isinstance(records, dict):
            count = 1 if records else 0
        else:
            count = 0

    evidence: Dict[str, Any] = {
        "success": success,
        "operation": operation,
        "source": source,
        "record_count": count,
        "filters": filters or {},
        "records": records,
        "error": error,
        "provenance": {
            "officer_employee_id": officer.employee_id if officer else None,
            "officer_name": officer.name if officer else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    }
    if extra:
        evidence.update(extra)
    return evidence


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas for Tool Arguments
# ─────────────────────────────────────────────────────────────────────────────
class ListApplicationsArgs(BaseModel):
    status: Optional[str] = Field(None, description="Status filter: pending, in_progress, escalated, approved, rejected")
    application_type: Optional[str] = Field(None, description="Application type: 'ISD' or 'NISD'")
    submission_channel: Optional[str] = Field(None, description="Channel: 'CSC', 'citizen', 'sub_registrar'")
    submission_year: Optional[int] = Field(None, ge=2000, le=2100, description="Submission year")
    submission_month: Optional[int] = Field(None, ge=1, le=12, description="Submission month (1-12)")
    is_overdue: Optional[bool] = Field(None, description="Whether overdue")
    ward_number: Optional[str] = Field(None, description="Ward number")
    block_number: Optional[str] = Field(None, description="Block number")
    limit: Optional[int] = Field(15, ge=1, le=100)
    offset: Optional[int] = Field(0, ge=0)

    @field_validator("status", mode="before")
    @classmethod
    def clean_status(cls, v: Any) -> Optional[str]:
        if v is None or _is_absent(v): return None
        s = str(v).lower().strip()
        if s in _STATUS_ENUM: return s
        raise ValueError(f"status must be one of {', '.join(_STATUS_ENUM)}; got {v!r}")

    @field_validator("submission_channel", mode="before")
    @classmethod
    def clean_channel(cls, v: Any) -> Optional[str]:
        if v is None or _is_absent(v): return None
        for ch in _CHANNEL_ENUM:
            if str(v).lower().strip() == ch.lower(): return ch
        raise ValueError(f"submission_channel must be one of {', '.join(_CHANNEL_ENUM)}; got {v!r}")


class CountApplicationsArgs(BaseModel):
    status: Optional[str] = None
    application_type: Optional[str] = None
    submission_channel: Optional[str] = None
    submission_year: Optional[int] = Field(None, ge=2000, le=2100)
    submission_month: Optional[int] = Field(None, ge=1, le=12)
    is_overdue: Optional[bool] = None
    ward_number: Optional[str] = None
    block_number: Optional[str] = None

    @field_validator("status", mode="before")
    @classmethod
    def clean_status(cls, v: Any) -> Optional[str]:
        if v is None or _is_absent(v): return None
        s = str(v).lower().strip()
        if s in _STATUS_ENUM: return s
        raise ValueError(f"status must be one of {', '.join(_STATUS_ENUM)}; got {v!r}")

    @field_validator("submission_channel", mode="before")
    @classmethod
    def clean_channel(cls, v: Any) -> Optional[str]:
        if v is None or _is_absent(v): return None
        for ch in _CHANNEL_ENUM:
            if str(v).lower().strip() == ch.lower(): return ch
        raise ValueError(f"submission_channel must be one of {', '.join(_CHANNEL_ENUM)}; got {v!r}")


class ApplicationNumberArgs(BaseModel):
    application_number: str = Field(..., description="Full application number, e.g. 2025/0154/28/000001")

    @field_validator("application_number", mode="before")
    @classmethod
    def validate_app_number(cls, v: Any) -> str:
        s = str(v).strip()
        if not _APP_NUMBER_SHAPE_RE.match(s):
            raise ValueError(
                f"application_number does not look like an application number, got {v!r}. "
                f"If this was meant to resolve a pronoun ('it', 'that one') to an "
                f"application, no such application is named in this conversation."
            )
        return s


class ApplicationDetailArgs(ApplicationNumberArgs):
    pass


class ApplicationStatusArgs(ApplicationNumberArgs):
    pass


class CompareApplicationsArgs(BaseModel):
    application_numbers: List[str] = Field(..., description="List of 2 to 5 application numbers to compare")

    @field_validator("application_numbers", mode="before")
    @classmethod
    def validate_numbers(cls, v: Any) -> List[str]:
        if isinstance(v, str):
            items = [item.strip() for item in v.split(",") if item.strip()]
        elif isinstance(v, (list, tuple)):
            items = [str(item).strip() for item in v if str(item).strip()]
        else:
            raise ValueError("application_numbers must be a list or comma-separated string")
        if len(items) < 2:
            raise ValueError("At least 2 application numbers are required for comparison")
        if len(items) > 5:
            raise ValueError("At most 5 application numbers can be compared at once")
        for num in items:
            if not _APP_NUMBER_SHAPE_RE.match(num):
                raise ValueError(f"'{num}' does not look like an application number")
        return items


class OldestNewestApplicationsArgs(BaseModel):
    order: str = Field("oldest", description="'oldest' or 'newest'")
    limit: int = Field(1, ge=1, le=10, description="How many applications to return")
    status: Optional[str] = Field(None, description="Status filter")
    application_type: Optional[str] = Field(None, description="Filter by type ('ISD' or 'NISD')")
    ward_number: Optional[str] = None
    block_number: Optional[str] = None

    @field_validator("order", mode="before")
    @classmethod
    def clean_order(cls, v: Any) -> str:
        s = str(v).lower().strip()
        if any(w in s for w in ("new", "recent", "latest")):
            return "newest"
        return "oldest"


class DurationAnalysisArgs(BaseModel):
    status: Optional[str] = None
    application_type: Optional[str] = None
    ward_number: Optional[str] = None
    block_number: Optional[str] = None


class FieldVisitsArgs(BaseModel):
    status_filter: Optional[str] = Field(None, description="'scheduled', 'completed', 'cancelled', 'pending'")
    application_number: Optional[str] = None
    to_be_visited_only: Optional[bool] = False
    limit: Optional[int] = Field(15, ge=1, le=100)

    @field_validator("status_filter", mode="before")
    @classmethod
    def clean_status(cls, v: Any) -> Optional[str]:
        if v is None or _is_absent(v): return None
        s = str(v).lower().strip()
        allowed = ("scheduled", "completed", "cancelled", "pending")
        if s in allowed: return s
        raise ValueError(f"status_filter must be one of {', '.join(allowed)}; got {v!r}")


class UnscheduledVisitsArgs(BaseModel):
    ward_number: Optional[str] = None
    block_number: Optional[str] = None


class SurveyDetailsArgs(BaseModel):
    survey_number: str = Field(..., description="Survey number or sub-division, e.g. '1355' or '1355/1B12'")

    @field_validator("survey_number", mode="before")
    @classmethod
    def validate_survey(cls, v: Any) -> str:
        s = str(v).strip()
        if not s or _is_absent(s):
            raise ValueError("survey_number must be specified")
        return s


class ServiceCodeInfoArgs(BaseModel):
    service_code: Optional[str] = None
    service_type: Optional[str] = None


class SearchDocumentsArgs(BaseModel):
    query: str = Field(..., min_length=1, description="What to look up, in English.")
    max_results: int = Field(4, ge=1, le=8, description="Maximum number of document passages")

    @field_validator("max_results", mode="before")
    @classmethod
    def clamp_max_results(cls, v: Any) -> int:
        try:
            val = int(v)
            if val < 1: return 1
            if val > 8: return 8
            return val
        except (ValueError, TypeError):
            return 4


class EmptyArgs(BaseModel):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Tool Specification
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[..., Any]
    args_schema: Optional[Type[BaseModel]] = None

    def schema(self) -> Dict[str, Any]:
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


_WARD_PROP = {"type": "string", "description": "Ward number to narrow to, e.g. '002'."}
_BLOCK_PROP = {"type": "string", "description": "Block number to narrow to, e.g. '0015'."}


# ─────────────────────────────────────────────────────────────────────────────
# Argument Validation & Coercion
# ─────────────────────────────────────────────────────────────────────────────
def validate_args(spec: ToolSpec, raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ToolArgumentError(f"{spec.name}: arguments must be an object, got {type(raw).__name__}")

    # Reject forbidden keys immediately
    for key in raw.keys():
        lowered = str(key).lower()
        if lowered in _FORBIDDEN_ARGS:
            raise ToolArgumentError(
                f"{spec.name}: '{key}' is not an argument of this tool. Whose data "
                f"is being read is fixed by the signed-in officer and cannot be set "
                f"by a tool call.")

    props: Dict[str, Any] = spec.parameters.get("properties", {})
    required: List[str] = spec.parameters.get("required", [])
    clean: Dict[str, Any] = {}

    for key, value in raw.items():
        if key not in props and (spec.args_schema is None or key not in spec.args_schema.model_fields):
            logger.info(f"agent_tools: dropping unknown argument '{key}' for {spec.name}")
            continue
        if value is None or _is_absent(value):
            continue
        clean[key] = value

    # If Pydantic schema is present, validate with it
    if spec.args_schema is not None:
        try:
            model_instance = spec.args_schema(**clean)
            validated = model_instance.model_dump(exclude_unset=False, exclude_none=True)
            for req in required:
                if req not in validated or validated[req] is None:
                    raise ToolArgumentError(f"{spec.name}: required argument '{req}' is missing.")
            return validated
        except (PydanticValidationError, ValueError) as exc:
            if hasattr(exc, "errors"):
                errs = exc.errors()
                if errs:
                    e = errs[0]
                    field_name = e.get("loc", ["argument"])[-1]
                    e_type = e.get("type", "")
                    e_msg = e.get("msg", "invalid")
                    if "less_than_equal" in e_type or "less_than" in e_type:
                        ctx_limit = e.get("ctx", {}).get("le") or e.get("ctx", {}).get("lt")
                        raise ToolArgumentError(f"{spec.name}: '{field_name}' must be at most {ctx_limit}, got {raw.get(field_name)}.")
                    elif "greater_than_equal" in e_type or "greater_than" in e_type:
                        ctx_limit = e.get("ctx", {}).get("ge") or e.get("ctx", {}).get("gt")
                        raise ToolArgumentError(f"{spec.name}: '{field_name}' must be at least {ctx_limit}, got {raw.get(field_name)}.")
                    else:
                        raise ToolArgumentError(f"{spec.name}: '{field_name}' {e_msg}")
            raise ToolArgumentError(f"{spec.name}: {exc}")

    # Fallback to manual coercion for tools without Pydantic schema
    for key, val in clean.items():
        if key in props:
            clean[key] = _coerce(spec.name, key, val, props[key])
    for key in required:
        if key not in clean:
            raise ToolArgumentError(f"{spec.name}: required argument '{key}' is missing.")
    return clean


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
            value = lo
        if hi is not None and value > hi:
            if not clampable:
                raise ToolArgumentError(f"{tool}: '{key}' must be at most {hi}, got {value}.")
            value = hi
        return value

    if want == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "yes", "1"): return True
        if text in ("false", "no", "0"): return False
        raise ToolArgumentError(f"{tool}: '{key}' must be true or false, got {value!r}.")

    text = str(value).strip()
    if key == "application_number" and not _APP_NUMBER_SHAPE_RE.match(text):
        raise ToolArgumentError(
            f"{tool}: '{key}' does not look like an application number, got {value!r}. "
            f"If this was meant to resolve a pronoun ('it', 'that one') to an "
            f"application, no such application is named in this conversation.")
    if enum:
        for option in enum:
            if text.lower() == str(option).lower():
                return option
        raise ToolArgumentError(f"{tool}: '{key}' must be one of {', '.join(map(str, enum))}; got {value!r}.")
    return text


def authorize_geography(ctx: ToolContext, args: Dict[str, Any]) -> None:
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
# Result Trimming & Slimming
# ─────────────────────────────────────────────────────────────────────────────
_ROW_CAP = 15
_APP_FIELDS = (
    "application_number", "type", "application_type", "service_code", "service_name",
    "status", "current_status", "current_stage", "stage",
    "submission_date", "ward_number", "block_number", "survey_number",
    "applicant_name", "days_pending", "is_overdue", "submission_channel"
)


def _slim_app(row: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = {k: row.get(k) for k in _APP_FIELDS if row.get(k) is not None}
    return mask_pii_record(cleaned)


def _slim_list(
    data: Dict[str, Any],
    key: str,
    slim=_slim_app,
    operation: str = "list",
    officer: Optional[OfficerContext] = None,
    filters: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = _ROW_CAP,
    offset: Optional[int] = 0,
) -> Dict[str, Any]:
    rows = data.get(key) or []
    count = data.get("count")
    if count is None:
        count = len(rows)
    cap = limit or _ROW_CAP
    start = offset or 0
    sliced = rows[start:start + cap]
    slimmed = [slim(r) for r in sliced]
    out: Dict[str, Any] = {
        "success": True,
        "operation": operation,
        "source": "database:applications",
        "record_count": count,
        "count": count,
        "query_type": data.get("query_type"),
        key: slimmed,
        "records": slimmed,
        "filters": filters or {},
        "provenance": {
            "officer_employee_id": officer.employee_id if officer else None,
            "officer_name": officer.name if officer else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    }
    if len(rows) > cap:
        out["note"] = f"{count} rows matched; showing up to {cap}. Report the count as {count}."
    for extra in ("message", "start_date", "end_date", "total_active"):
        if data.get(extra) is not None:
            out[extra] = data[extra]
    return {k: v for k, v in out.items() if v is not None}


def _register_wide(args: Dict[str, Any]) -> bool:
    return (not args.get("application_type") and args.get("is_overdue") is None
            and args.get("status") not in ("in_progress", "escalated"))


async def _register_applications(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    status = args.get("status")
    scoped = (args.get("submission_year") or args.get("submission_month") or args.get("submission_channel"))
    return await pg.get_all_service_code_applications(
        ctx.db, ctx.officer,
        submission_channel=args.get("submission_channel"),
        status=status or (None if scoped else "pending"),
        submission_year=args.get("submission_year"),
        submission_month=args.get("submission_month"),
        ward_number=args.get("ward_number"), block_number=args.get("block_number"),
        hide_rejected=status is None)


# ─────────────────────────────────────────────────────────────────────────────
# Handlers (Read-Only)
# ─────────────────────────────────────────────────────────────────────────────
async def _h_list_applications(ctx: ToolContext, **args) -> Dict[str, Any]:
    authorize_geography(ctx, args)
    limit = args.pop("limit", _ROW_CAP)
    offset = args.pop("offset", 0)
    if _register_wide(args):
        raw = await _register_applications(ctx, args)
        return _slim_list(raw, "applications", operation="list_applications", officer=ctx.officer, filters=args, limit=limit, offset=offset)
    data = await pg.get_officer_applications(ctx.db, ctx.officer, **args)
    return _slim_list(data, "applications", operation="list_applications", officer=ctx.officer, filters=args, limit=limit, offset=offset)


async def _h_count_applications(ctx: ToolContext, **args) -> Dict[str, Any]:
    authorize_geography(ctx, args)
    data = (await _register_applications(ctx, args) if _register_wide(args)
            else await pg.get_officer_applications(ctx.db, ctx.officer, **args))
    count = data.get("count")
    if count is None:
        count = len(data.get("applications") or [])
    return format_tool_evidence(
        operation="count_applications",
        source="database:applications",
        records=[],
        count=count,
        filters=args or {},
        extra={"count": count, "query_type": data.get("query_type")},
        officer=ctx.officer
    )


async def _h_pending_applications(ctx: ToolContext, **args) -> Dict[str, Any]:
    authorize_geography(ctx, args)
    data = await pg.get_pending_applications(ctx.db, ctx.officer, **args)
    return _slim_list(data, "applications", operation="get_pending_applications", officer=ctx.officer, filters=args)


async def _h_overdue_applications(ctx: ToolContext, **args) -> Dict[str, Any]:
    authorize_geography(ctx, args)
    data = await pg.get_overdue_applications(ctx.db, ctx.officer, **args)
    return _slim_list(data, "applications", operation="get_overdue_applications", officer=ctx.officer, filters=args)


async def _h_officer_workload(ctx: ToolContext, **args) -> Dict[str, Any]:
    workload = await pg.get_officer_workload(ctx.db, ctx.officer)
    return format_tool_evidence(
        operation="get_officer_workload",
        source="database:applications",
        records=workload,
        count=workload.get("total_active", 0),
        extra=workload,
        officer=ctx.officer
    )


async def _h_application_status(ctx: ToolContext, **args) -> Dict[str, Any]:
    """Fast status and stage lookup for a single application."""
    number = args["application_number"]
    access = await pg.lookup_application_access(ctx.db, number, ctx.officer)
    if access.get("exists"):
        if not access.get("accessible"):
            return format_tool_evidence(
                operation="get_application_status",
                source="database:applications",
                records=None,
                count=0,
                error="outside_jurisdiction",
                extra={
                    "found": False,
                    "reason": "outside_jurisdiction",
                    "application_number": number,
                    "message": f"Application {number} exists but is outside {ctx.officer.name}'s jurisdiction."
                },
                officer=ctx.officer
            )
        detail = await pg.get_application_detail(ctx.db, number, ctx.officer)
        status_summary = {
            "application_number": number,
            "status": detail.get("status") or detail.get("current_status"),
            "current_stage": detail.get("current_stage") or detail.get("stage"),
            "service_name": detail.get("service_name"),
            "service_code": detail.get("service_code"),
            "submission_date": detail.get("submission_date"),
            "days_pending": detail.get("days_pending"),
            "is_overdue": detail.get("is_overdue"),
            "last_updated": detail.get("last_updated") or detail.get("updated_at"),
        }
        return format_tool_evidence(
            operation="get_application_status",
            source="database:applications",
            records=status_summary,
            count=1,
            extra={
                "found": True,
                "application_number": number,
                "status": status_summary["status"],
                "current_stage": status_summary["current_stage"],
                "is_overdue": status_summary["is_overdue"],
                "days_pending": status_summary["days_pending"],
                "submission_date": status_summary["submission_date"]
            },
            officer=ctx.officer
        )

    # Check ServiceRegisterEntry for other service codes (e.g. 0158, 0159, 0178)
    from backend.models import ServiceRegisterEntry
    from backend.utils.helpers import SIS_URBAN_SERVICES
    sr_row = (await ctx.db.execute(
        select(ServiceRegisterEntry).where(ServiceRegisterEntry.application_number == number)
    )).scalar_one_or_none()
    if sr_row:
        is_accessible = bool(ctx.officer and ctx.officer.jurisdiction_ids and sr_row.ward_id in ctx.officer.jurisdiction_ids)
        if not is_accessible:
            return format_tool_evidence(
                operation="get_application_status",
                source="database:service_register",
                records=None,
                count=0,
                error="outside_jurisdiction",
                extra={
                    "found": False,
                    "reason": "outside_jurisdiction",
                    "application_number": number,
                    "message": f"Application {number} exists but is outside {ctx.officer.name}'s jurisdiction."
                },
                officer=ctx.officer
            )
        svc_name = SIS_URBAN_SERVICES.get(sr_row.service_code, f"Service {sr_row.service_code}")
        status_summary = {
            "application_number": number,
            "status": sr_row.status or "pending",
            "current_stage": sr_row.stage or "Open",
            "service_name": svc_name,
            "service_code": sr_row.service_code,
            "submission_date": sr_row.submission_date.isoformat() if sr_row.submission_date else None,
            "days_pending": None,
            "is_overdue": False,
            "last_updated": sr_row.updated_at.isoformat() if sr_row.updated_at else None,
        }
        return format_tool_evidence(
            operation="get_application_status",
            source="database:service_register",
            records=status_summary,
            count=1,
            extra={
                "found": True,
                "application_number": number,
                "status": status_summary["status"],
                "current_stage": status_summary["current_stage"],
                "is_overdue": status_summary["is_overdue"],
                "days_pending": status_summary["days_pending"],
                "submission_date": status_summary["submission_date"]
            },
            officer=ctx.officer
        )

    return format_tool_evidence(
        operation="get_application_status",
        source="database:applications",
        records=None,
        count=0,
        error="no_such_application",
        extra={
            "found": False,
            "reason": "no_such_application",
            "application_number": number,
            "suggestions": [c.get("application_number") for c in access.get("candidates", [])]
        },
        officer=ctx.officer
    )


async def _h_application_detail(ctx: ToolContext, **args) -> Dict[str, Any]:
    """Complete application detail record with citizen PII masked."""
    number = args["application_number"]
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
    sanitized = mask_pii_record(detail)
    sanitized["success"] = True
    sanitized["record_count"] = 1
    return sanitized


async def _h_compare_applications(ctx: ToolContext, **args) -> Dict[str, Any]:
    """Compare 2-5 applications side-by-side with jurisdiction checks."""
    app_numbers = args["application_numbers"]
    comparisons = []
    
    for app_no in app_numbers:
        access = await pg.lookup_application_access(ctx.db, app_no, ctx.officer)
        if not access.get("exists"):
            comparisons.append({
                "application_number": app_no,
                "found": False,
                "reason": "no_such_application",
            })
            continue
        if not access.get("accessible"):
            comparisons.append({
                "application_number": app_no,
                "found": False,
                "reason": "outside_jurisdiction",
                "message": f"Application {app_no} is outside jurisdiction."
            })
            continue
        
        detail = await pg.get_application_detail(ctx.db, app_no, ctx.officer)
        comparisons.append({
            "application_number": app_no,
            "found": True,
            "applicant_name": detail.get("applicant_name"),
            "service_code": detail.get("service_code"),
            "service_name": detail.get("service_name"),
            "application_type": detail.get("type") or detail.get("application_type"),
            "status": detail.get("status") or detail.get("current_status"),
            "current_stage": detail.get("current_stage") or detail.get("stage"),
            "submission_date": detail.get("submission_date"),
            "days_pending": detail.get("days_pending"),
            "is_overdue": detail.get("is_overdue"),
            "survey_number": detail.get("survey_number"),
            "ward_number": detail.get("ward_number"),
            "block_number": detail.get("block_number"),
        })

    valid_apps = [c for c in comparisons if c.get("found")]
    highlights = {}
    if len(valid_apps) >= 2:
        sorted_by_date = sorted(valid_apps, key=lambda x: x.get("submission_date") or "9999")
        highlights["oldest_file"] = sorted_by_date[0]["application_number"]
        highlights["newest_file"] = sorted_by_date[-1]["application_number"]
        highlights["overdue_files"] = [c["application_number"] for c in valid_apps if c.get("is_overdue")]

    return format_tool_evidence(
        operation="compare_applications",
        source="database:applications",
        records=comparisons,
        count=len(comparisons),
        extra={
            "compared_count": len(comparisons),
            "accessible_count": len(valid_apps),
            "highlights": highlights,
            "comparison_matrix": comparisons
        },
        officer=ctx.officer
    )


async def _h_oldest_newest_applications(ctx: ToolContext, **args) -> Dict[str, Any]:
    """Identify oldest or newest applications by submission date."""
    authorize_geography(ctx, args)
    order = args.get("order", "oldest")
    limit = args.get("limit", 1)
    status = args.get("status", "pending")
    app_type = args.get("application_type")

    if app_type or status in ("in_progress", "escalated"):
        data = await pg.get_officer_applications(
            ctx.db, ctx.officer,
            status=status,
            application_type=app_type,
            ward_number=args.get("ward_number"),
            block_number=args.get("block_number")
        )
    else:
        data = await pg.get_all_service_code_applications(
            ctx.db, ctx.officer,
            status=status,
            ward_number=args.get("ward_number"),
            block_number=args.get("block_number")
        )
    
    raw_apps = data.get("applications") or []
    if not raw_apps:
        return format_tool_evidence(
            operation="get_oldest_newest_applications",
            source="database:applications",
            records=[],
            count=0,
            filters=args,
            extra={
                "order": order,
                "applications": [],
                "oldest_application_number": None,
                "newest_application_number": None,
                "message": f"No applications found matching status='{status}'"
            },
            officer=ctx.officer
        )

    def _sort_key(item):
        return item.get("submission_date") or item.get("application_date") or "9999"

    reverse_sort = (order == "newest")
    sorted_apps = sorted(raw_apps, key=_sort_key, reverse=reverse_sort)
    top_matches = [_slim_app(app) for app in sorted_apps[:limit]]

    oldest_no = sorted_apps[0].get("application_number") if order == "oldest" else sorted(raw_apps, key=_sort_key)[0].get("application_number")
    newest_no = sorted_apps[0].get("application_number") if order == "newest" else sorted(raw_apps, key=_sort_key, reverse=True)[0].get("application_number")

    return format_tool_evidence(
        operation="get_oldest_newest_applications",
        source="database:applications",
        records=top_matches,
        count=len(top_matches),
        filters=args,
        extra={
            "order": order,
            "applications": top_matches,
            "oldest_application_number": oldest_no,
            "newest_application_number": newest_no,
            "selected_application": top_matches[0] if top_matches else None
        },
        officer=ctx.officer
    )


async def _h_duration_analysis(ctx: ToolContext, **args) -> Dict[str, Any]:
    """Analyze duration, turnaround times, and age distribution."""
    authorize_geography(ctx, args)
    rows = await pg.get_analytics_rows(ctx.db, ctx.officer)
    if not rows:
        return format_tool_evidence(
            operation="analyze_application_duration",
            source="database:analytics",
            records=[],
            count=0,
            extra={"message": "No applications found for duration analysis"},
            officer=ctx.officer
        )
    
    status_filter = args.get("status")
    type_filter = args.get("application_type")
    
    filtered_rows = rows
    if status_filter:
        filtered_rows = [r for r in filtered_rows if (r.get("current_status") or "").lower() == status_filter.lower()]
    if type_filter:
        filtered_rows = [r for r in filtered_rows if (r.get("application_type") or "").upper() == type_filter.upper()]
    
    if not filtered_rows:
        return format_tool_evidence(
            operation="analyze_application_duration",
            source="database:analytics",
            records=[],
            count=0,
            filters=args,
            extra={"message": "No applications matching the specified filters"},
            officer=ctx.officer
        )

    now = datetime.now(timezone.utc)
    days_list = []
    overdue_count = 0
    under_15 = 0
    between_15_30 = 0
    over_30 = 0

    for r in filtered_rows:
        days = r.get("days_pending")
        sub_date = r.get("submission_date") or r.get("created_at")
        if days is None and sub_date:
            try:
                sub_dt = datetime.fromisoformat(str(sub_date).replace("Z", "+00:00"))
                if sub_dt.tzinfo is None:
                    sub_dt = sub_dt.replace(tzinfo=timezone.utc)
                days = (now - sub_dt).days
            except Exception:
                days = 0
        if days is not None:
            days_list.append(days)
            if days > 30:
                over_30 += 1
            elif days >= 15:
                between_15_30 += 1
            else:
                under_15 += 1
        
        if r.get("is_overdue") or (days is not None and days > 30):
            overdue_count += 1

    total_count = len(filtered_rows)
    avg_days = round(sum(days_list) / len(days_list), 1) if days_list else 0
    sorted_days = sorted(days_list)
    median_days = sorted_days[len(sorted_days) // 2] if sorted_days else 0
    max_days = max(days_list) if days_list else 0

    summary = {
        "total_applications": total_count,
        "average_days_pending": avg_days,
        "median_days_pending": median_days,
        "max_days_pending": max_days,
        "overdue_count": overdue_count,
        "overdue_percentage": round((overdue_count / total_count * 100), 1) if total_count else 0.0,
        "age_distribution": {
            "under_15_days": under_15,
            "15_to_30_days": between_15_30,
            "over_30_days": over_30
        }
    }

    return format_tool_evidence(
        operation="analyze_application_duration",
        source="database:analytics",
        records=summary,
        count=total_count,
        filters=args,
        extra=summary,
        officer=ctx.officer
    )


async def _h_field_visits(ctx: ToolContext, **args) -> Dict[str, Any]:
    limit = args.pop("limit", _ROW_CAP)
    data = await pg.get_field_visits(ctx.db, ctx.officer, **args)
    return _slim_list(data, "field_visits", slim=lambda r: r, operation="get_field_visits", officer=ctx.officer, filters=args, limit=limit)


async def _h_unscheduled_inspections(ctx: ToolContext, **args) -> Dict[str, Any]:
    """Identify applications awaiting field inspection scheduling."""
    authorize_geography(ctx, args)
    data = await pg.get_unscheduled_visits(ctx.db, ctx.officer)
    visits = data.get("visits") or data.get("applications") or []
    
    ward = args.get("ward_number")
    block = args.get("block_number")
    if ward:
        visits = [v for v in visits if v.get("ward_number") == ward]
    if block:
        visits = [v for v in visits if v.get("block_number") == block]
        
    return format_tool_evidence(
        operation="get_unscheduled_inspections",
        source="database:field_visits",
        records=visits[:_ROW_CAP],
        count=len(visits),
        filters=args,
        extra={
            "unscheduled_count": len(visits),
            "applications": visits[:_ROW_CAP],
            "field_visits": visits[:_ROW_CAP]
        },
        officer=ctx.officer
    )


async def _h_visit_plan(ctx: ToolContext, **args) -> Dict[str, Any]:
    authorize_geography(ctx, args)
    return await pg.get_visit_plan(ctx.db, ctx.officer, **args)


async def _h_last_application(ctx: ToolContext, **args) -> Dict[str, Any]:
    authorize_geography(ctx, args)
    return await pg.get_last_application(ctx.db, ctx.officer, **args)


async def _h_survey_detail(ctx: ToolContext, **args) -> Dict[str, Any]:
    detail = await pg.get_survey_detail(ctx.db, args["survey_number"], ctx.officer)
    return detail


async def _h_survey_ownership(ctx: ToolContext, **args) -> Dict[str, Any]:
    """Ownership and joint-owners information for a survey number."""
    survey_no = args["survey_number"]
    data = await pg.get_survey_owners(ctx.db, survey_no, ctx.officer)
    if not data or data.get("status") == "error" or not data.get("owners"):
        detail = await pg.get_survey_detail(ctx.db, survey_no, ctx.officer)
        if detail and detail.get("owners"):
            data = {
                "survey_number": survey_no,
                "patta_number": detail.get("patta_number"),
                "owners": detail.get("owners"),
                "total_owners": len(detail.get("owners", []))
            }

    owners = data.get("owners") or []
    sanitized_owners = [mask_pii_record(o) for o in owners]
    
    return format_tool_evidence(
        operation="get_survey_ownership",
        source="database:survey_numbers",
        records=sanitized_owners,
        count=len(sanitized_owners),
        filters={"survey_number": survey_no},
        extra={
            "survey_number": survey_no,
            "patta_number": data.get("patta_number"),
            "total_owners": len(sanitized_owners),
            "owners": sanitized_owners,
            "subdivision_details": data.get("subdivisions")
        },
        officer=ctx.officer
    )


async def _h_survey_lock(ctx: ToolContext, **args) -> Dict[str, Any]:
    return await pg.check_survey_application_lock(ctx.db, args["survey_number"], ctx.officer)


async def _h_service_code_info(ctx: ToolContext, **args) -> Dict[str, Any]:
    """Look up statutory fee schedule and sub-division rules for service codes."""
    code = args.get("service_code")
    stype = args.get("service_type")
    
    if not code and stype:
        stype_upper = stype.upper()
        if "NISD" in stype_upper or "NOT INVOLVING" in stype_upper:
            code = "0153"
        elif "ISD" in stype_upper or "INVOLVING" in stype_upper:
            code = "0154"

    fees_data = await pg.get_service_code_fees(ctx.db, code)
    
    domain_knowledge = {
        "0153": {
            "service_name": "Patta Transfer - Not Involving Sub-Division (NISD)",
            "service_code": "0153",
            "involves_subdivision": False,
            "requires_field_inspection": False,
            "description": "Straight transfer of an entire survey parcel. Does not require sub-division sketch or field measurement."
        },
        "0154": {
            "service_name": "Patta Transfer - Involving Sub-Division (ISD)",
            "service_code": "0154",
            "involves_subdivision": True,
            "requires_field_inspection": True,
            "description": "Transfer involving parcel bifurcation/sub-division. Requires field inspection, survey measurement, and SD sketch preparation by SIS."
        },
        "0151": {
            "service_name": "Patta Transfer - Full/Direct Transfer",
            "service_code": "0151",
            "involves_subdivision": False,
            "requires_field_inspection": False,
            "description": "Direct mutation of revenue records without land division."
        }
    }
    
    result_data = {}
    if code:
        if isinstance(fees_data, dict) and code in fees_data:
            result_data = dict(fees_data[code])
        elif isinstance(fees_data, dict):
            result_data = dict(fees_data)
        if code in domain_knowledge:
            result_data.update(domain_knowledge[code])
    else:
        result_data = fees_data if isinstance(fees_data, dict) else {}
        for c, meta in domain_knowledge.items():
            if c in result_data:
                result_data[c].update(meta)

    return format_tool_evidence(
        operation="get_service_code_info",
        source="database:service_fees",
        records=result_data,
        count=1 if code else len(result_data),
        filters=args,
        extra={"service_code": code, "info": result_data},
        officer=ctx.officer
    )


async def _h_workflow_history(ctx: ToolContext, **args) -> Dict[str, Any]:
    """Ordered workflow history hops and officer remarks."""
    app_no = args["application_number"]
    access = await pg.lookup_application_access(ctx.db, app_no, ctx.officer)
    if not access.get("exists"):
        return format_tool_evidence(
            operation="get_workflow_history",
            source="database:workflow_history",
            records=[],
            count=0,
            error="no_such_application",
            extra={"found": False, "reason": "no_such_application", "application_number": app_no},
            officer=ctx.officer
        )
    if not access.get("accessible"):
        return format_tool_evidence(
            operation="get_workflow_history",
            source="database:workflow_history",
            records=[],
            count=0,
            error="outside_jurisdiction",
            extra={"found": False, "reason": "outside_jurisdiction", "application_number": app_no},
            officer=ctx.officer
        )
    
    hops = await pg.get_workflow_history(ctx.db, ctx.officer, app_no)
    remarks = await pg.get_workflow_remarks(ctx.db, app_no, ctx.officer)
    
    return format_tool_evidence(
        operation="get_workflow_history",
        source="database:workflow_history",
        records=hops or [],
        count=len(hops) if hops else 0,
        filters={"application_number": app_no},
        extra={
            "application_number": app_no,
            "total_hops": len(hops) if hops else 0,
            "workflow_history": hops or [],
            "remarks": remarks or []
        },
        officer=ctx.officer
    )


async def _h_fee_summary(ctx: ToolContext, **args) -> Dict[str, Any]:
    return await pg.get_fee_summary(ctx.db, ctx.officer, **args)


async def _h_jurisdiction(ctx: ToolContext, **args) -> Dict[str, Any]:
    jurisdiction_data = {
        "officer_name": ctx.officer.name,
        "employee_id": ctx.officer.employee_id,
        "designation": ctx.officer.designation,
        "stage": ctx.officer.officer_stage,
        "jurisdiction_type": ctx.officer.jurisdiction_type,
        "jurisdiction_name": ctx.officer.jurisdiction_name,
        "wards": ctx.wards,
        "blocks": ctx.blocks,
    }
    return format_tool_evidence(
        operation="get_my_jurisdiction",
        source="system:officer_jurisdiction",
        records=jurisdiction_data,
        count=1,
        extra=jurisdiction_data,
        officer=ctx.officer
    )


def _squeeze(text: str) -> str:
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]{2,}", " ", text)).strip()


async def _h_search_documents(ctx: ToolContext, **args) -> Dict[str, Any]:
    from backend.services.rag_llm import get_rag_evidence_async

    query = args["query"]
    n = args.get("max_results", 4)
    try:
        hits = await get_rag_evidence_async(query, "en", n, None)
    except Exception as exc:
        logger.error(f"agent_tools: document search failed: {exc}")
        return {"found": False, "error": "document search unavailable"}
    passages = [
        {
            "source": (h.get("metadata") or {}).get("document_name"),
            "citation": h.get("citation"),
            "content": _squeeze(h.get("content") or "")[:2200],
        }
        for h in hits
    ]
    return format_tool_evidence(
        operation="search_documents",
        source="vector_store:sis_documents",
        records=passages,
        count=len(passages),
        filters={"query": query, "max_results": n},
        extra={"found": bool(hits), "passages": passages},
        officer=ctx.officer
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool Registry (14 Core Capabilities + Specialized Aliases)
# ─────────────────────────────────────────────────────────────────────────────
TOOLS: List[ToolSpec] = [
    ToolSpec(
        name="list_applications",
        description=(
            "List the signed-in officer's applications across EVERY service code in the "
            "register (ISD, NISD, Modification, Addition, F-Line, ...), optionally "
            "filtered by status, submission channel, ward, block, or submission "
            "month/year. Use for 'show me...', 'which applications...'. Returns real "
            "rows; never invent application numbers. With NO filter it lists the open "
            "(pending) applications."),
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
        args_schema=ListApplicationsArgs,
    ),
    ToolSpec(
        name="count_applications",
        description=(
            "Count the signed-in officer's applications across EVERY service code in the "
            "register matching a filter, without listing them. Use for 'how many...'. "
            "The number in your answer must be the number this tool returns."),
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
        args_schema=CountApplicationsArgs,
    ),
    ToolSpec(
        name="get_pending_applications",
        description=(
            "The officer's working queue -- applications still open (pending, in "
            "progress, escalated) at their desk. Use for 'what is pending', 'my queue'."),
        parameters=_obj({
            "application_type": {"type": "string"},
            "submission_channel": {"type": "string", "enum": _CHANNEL_ENUM},
            "ward_number": _WARD_PROP,
            "block_number": _BLOCK_PROP,
        }),
        handler=_h_pending_applications,
        args_schema=ListApplicationsArgs,
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
            "applications are live at their desk right now, split by ISD/NISD, "
            "plus overdue count and pending field visits."),
        parameters=_obj({}),
        handler=_h_officer_workload,
        args_schema=EmptyArgs,
    ),
    ToolSpec(
        name="get_application_status",
        description=(
            "Fast status and stage lookup for an application number (e.g. '2025/0154/28/000001'). "
            "Returns current status, assigned desk/stage, days pending, overdue flag, and last update date."),
        parameters=_obj({
            "application_number": {"type": "string", "description": "Full application number"},
        }, required=["application_number"]),
        handler=_h_application_status,
        args_schema=ApplicationStatusArgs,
    ),
    ToolSpec(
        name="get_application_details",
        description=(
            "Full details on record for one application: status, stage, applicant name, survey "
            "number, dates, sub-divisions, channel. Masks citizen PII (Aadhaar/mobile). Refuses applications "
            "outside jurisdiction."),
        parameters=_obj({
            "application_number": {"type": "string", "description": "Full application number"},
        }, required=["application_number"]),
        handler=_h_application_detail,
        args_schema=ApplicationDetailArgs,
    ),
    ToolSpec(
        name="compare_applications",
        description=(
            "Compare 2 to 5 application numbers side-by-side: types, stages, submission dates, "
            "statuses, survey numbers, days pending, and overdue states. Checks jurisdiction for each."),
        parameters=_obj({
            "application_numbers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of 2 to 5 application numbers to compare"
            },
        }, required=["application_numbers"]),
        handler=_h_compare_applications,
        args_schema=CompareApplicationsArgs,
    ),
    ToolSpec(
        name="get_oldest_newest_applications",
        description=(
            "Identify the oldest or newest applications by submission date in the officer's jurisdiction, "
            "optionally filtered by status, application type, ward, or block."),
        parameters=_obj({
            "order": {"type": "string", "enum": ["oldest", "newest"], "description": "'oldest' or 'newest'"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            "status": {"type": "string", "enum": _STATUS_ENUM},
            "application_type": {"type": "string"},
            "ward_number": _WARD_PROP,
            "block_number": _BLOCK_PROP,
        }),
        handler=_h_oldest_newest_applications,
        args_schema=OldestNewestApplicationsArgs,
    ),
    ToolSpec(
        name="analyze_application_duration",
        description=(
            "Duration, turnaround, age distribution, and SLA breach analysis across applications in "
            "the officer's jurisdiction. Computes mean, median, max days pending, and age brackets."),
        parameters=_obj({
            "status": {"type": "string", "enum": _STATUS_ENUM},
            "application_type": {"type": "string"},
            "ward_number": _WARD_PROP,
            "block_number": _BLOCK_PROP,
        }),
        handler=_h_duration_analysis,
        args_schema=DurationAnalysisArgs,
    ),
    ToolSpec(
        name="get_field_visits",
        description=(
            "Field visits (inspections) for the officer, optionally for one application or filtered "
            "by status ('scheduled', 'completed', 'cancelled', 'pending')."),
        parameters=_obj({
            "status_filter": {"type": "string", "enum": ["scheduled", "completed", "cancelled", "pending"]},
            "application_number": {"type": "string"},
            "to_be_visited_only": {"type": "boolean"},
        }),
        handler=_h_field_visits,
        args_schema=FieldVisitsArgs,
    ),
    ToolSpec(
        name="get_unscheduled_inspections",
        description=(
            "Identify applications in the officer's jurisdiction that require field inspection but do not "
            "yet have a scheduled inspection date."),
        parameters=_obj({
            "ward_number": _WARD_PROP,
            "block_number": _BLOCK_PROP,
        }),
        handler=_h_unscheduled_inspections,
        args_schema=UnscheduledVisitsArgs,
    ),
    ToolSpec(
        name="get_survey_details",
        description=(
            "Details of a survey number or sub-division (e.g. '1355' or '1355/1B12'): ward, block, "
            "town, taluk, district, patta number, and extent."),
        parameters=_obj({
            "survey_number": {"type": "string", "description": "Survey number or sub-division"},
        }, required=["survey_number"]),
        handler=_h_survey_detail,
        args_schema=SurveyDetailsArgs,
    ),
    ToolSpec(
        name="get_survey_ownership",
        description=(
            "Ownership information for a survey parcel: patta number, total owners count, and list of "
            "joint owners with ownership shares."),
        parameters=_obj({
            "survey_number": {"type": "string", "description": "Survey number to check ownership for"},
        }, required=["survey_number"]),
        handler=_h_survey_ownership,
        args_schema=SurveyDetailsArgs,
    ),
    ToolSpec(
        name="check_survey_application_lock",
        description=(
            "Whether a new application may be filed on a survey number right now. One live application "
            "locks the whole survey number."),
        parameters=_obj({
            "survey_number": {"type": "string"},
        }, required=["survey_number"]),
        handler=_h_survey_lock,
        args_schema=SurveyDetailsArgs,
    ),
    ToolSpec(
        name="get_service_code_info",
        description=(
            "Information on survey service codes (0154 ISD, 0153 NISD, 0151, ...): statutory fees, "
            "whether sub-division is required, and inspection procedures."),
        parameters=_obj({
            "service_code": {"type": "string", "description": "Code e.g. '0154', '0153'"},
            "service_type": {"type": "string", "description": "Type e.g. 'ISD', 'NISD'"},
        }),
        handler=_h_service_code_info,
        args_schema=ServiceCodeInfoArgs,
    ),
    ToolSpec(
        name="get_workflow_history",
        description=(
            "Full chronological workflow history for an application: hops, desks, actions, "
            "performed_at dates, and officer remarks. Refuses applications outside jurisdiction."),
        parameters=_obj({
            "application_number": {"type": "string", "description": "Full application number"},
        }, required=["application_number"]),
        handler=_h_workflow_history,
        args_schema=ApplicationNumberArgs,
    ),
    ToolSpec(
        name="get_visit_plan",
        description=(
            "What the officer should go and inspect next: overdue visits first, then applications with "
            "no visit booked."),
        parameters=_obj({
            "ward_number": _WARD_PROP,
            "block_number": _BLOCK_PROP,
        }),
        handler=_h_visit_plan,
        args_schema=UnscheduledVisitsArgs,
    ),
    ToolSpec(
        name="get_last_application",
        description=(
            "The officer's most recently acted-on application, optionally restricted to a status or type."),
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
            "Aggregate of fees / charges collected across the officer's applications: total, mean, "
            "payment modes."),
        parameters=_obj({
            "application_type": {"type": "string"},
        }),
        handler=_h_fee_summary,
    ),
    ToolSpec(
        name="get_my_jurisdiction",
        description=(
            "Who the signed-in officer is and what they cover: name, designation, workflow stage, "
            "and assigned wards and blocks."),
        parameters=_obj({}),
        handler=_h_jurisdiction,
        args_schema=EmptyArgs,
    ),
    ToolSpec(
        name="search_documents",
        description=(
            "Search the SIS reference corpus -- survey manual, workflow guide, land rules, service-code "
            "and fee tables, FAQs."),
        parameters=_obj({
            "query": {"type": "string", "description": "What to look up, in English."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 8},
        }, required=["query"]),
        handler=_h_search_documents,
        args_schema=SearchDocumentsArgs,
    ),
]

# Build lookup table with singular/plural aliases
TOOLS_BY_NAME: Dict[str, ToolSpec] = {t.name: t for t in TOOLS}
TOOLS_BY_NAME["get_application_detail"] = TOOLS_BY_NAME["get_application_details"]
TOOLS_BY_NAME["get_survey_detail"] = TOOLS_BY_NAME["get_survey_details"]


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic tool scoping
# ─────────────────────────────────────────────────────────────────────────────
# `bind_tools()` used to hand llama3.1:8b all 22 schemas on every turn,
# regardless of what was asked. An 8B model's tool selection degrades sharply
# past 5-8 tools in one prompt (more schema to read, more ways to pick the
# wrong one or invent an argument) -- so a question that only ever needed
# `get_fee_summary` was choosing from the full list anyway. `select_tool_names()`
# narrows that to the handful of tools the message actually calls for, from a
# small set of keyword groups below; `TOOLS_BY_NAME` already has every real
# tool name, so an unmatched group name here would raise at import time
# rather than silently vanish at runtime.
#
# Deliberately self-contained (plain regexes, no import of `rag.py` / the
# deterministic intent parser): that parser is tuned to answer a question,
# this one only has to decide which tools MIGHT be relevant, so a false
# positive here costs nothing -- the tool is simply offered and the model
# does not call it -- while a false negative can starve the model of a tool
# it actually needed. Importing it would also reach back into the `chatbot`
# package from `agent_tools.py`, which nothing here currently does.
TOOL_GROUPS: Dict[str, Tuple[str, ...]] = {
    "applications": (
        "list_applications", "count_applications", "get_pending_applications",
        "get_overdue_applications", "get_application_status",
        "get_application_details", "get_last_application", "get_officer_workload",
    ),
    "application_analytics": (
        "compare_applications", "get_oldest_newest_applications",
        "analyze_application_duration", "get_workflow_history",
    ),
    "survey": (
        "get_survey_details", "get_survey_ownership", "check_survey_application_lock",
    ),
    "field_visits": (
        "get_field_visits", "get_unscheduled_inspections", "get_visit_plan",
    ),
    "fees": (
        "get_fee_summary", "get_service_code_info",
    ),
    "jurisdiction": (
        "get_my_jurisdiction",
    ),
    "documents": (
        "search_documents",
    ),
}
assert all(name in TOOLS_BY_NAME for names in TOOL_GROUPS.values() for name in names), (
    "TOOL_GROUPS names a tool agent_tools.py no longer registers")

# Offered when nothing in the message (or the short history checked for a
# bare follow-up) matches any group above -- a small, broadly useful default
# rather than either the full 22 or an empty list.
_DEFAULT_TOOL_NAMES: Tuple[str, ...] = (
    "list_applications", "count_applications", "get_application_status",
    "get_pending_applications", "get_my_jurisdiction", "search_documents",
)

_GROUP_PATTERNS: Dict[str, "re.Pattern[str]"] = {
    "applications": re.compile(
        r"\bapplication|\bapp\b|\bapps\b|\bpending\b|\boverdue\b|\bworkload\b|"
        r"\bqueue\b|\bdesk\b|\bstatus\b|how\s+many|\bapproved\b|\brejected\b|"
        r"in\s+progress|\bescalated\b|\blast\s+application\b|\bprevious\s+application\b|"
        r"விண்ணப்ப|நிலுவை|வேலைப்பளு", re.IGNORECASE),
    "application_analytics": re.compile(
        r"\bcompare\b|\bcomparison\b|\boldest\b|\bnewest\b|\blongest\b|\bfastest\b|"
        r"\bduration\b|\bturnaround\b|\baverage\s+time\b|\bworkflow\s+history\b|"
        r"\bwhere\s+is\s+it\b|which\s+desk|who\s+has\s+it|ஒப்பிட|பழைய", re.IGNORECASE),
    "survey": re.compile(
        r"\bsurvey\b|\bsub[\s-]?division\b|\bpatta\b|\bowner(?:ship)?\b|\bparcel\b|"
        r"\bencroachment\b|\block(?:ed)?\b|can\s+i\s+(?:apply|file)|"
        r"சர்வே|கணக்கெண்|பட்டா|உரிமையாளர்", re.IGNORECASE),
    "field_visits": re.compile(
        r"\bfield\s*visit\b|\binspection\b|\bschedul\w*\b|\bvisit\s+plan\b|"
        r"\bunscheduled\b|\bnext\s+visit\b|கள\s*ஆய்வு|field\s*visit\s*eppo|visit\s*eppo",
        re.IGNORECASE),
    "fees": re.compile(
        r"\bfee\b|\bfees\b|\bcharge\b|\bcharges\b|\bpayment\b|\bcost\b|\bchallan\b|"
        r"\bservice\s*code\b|\b015[0-9]\b|\bisd\b|\bnisd\b|கட்டணம்", re.IGNORECASE),
    "jurisdiction": re.compile(
        r"\bjurisdiction\b|\bmy\s+ward\b|\bmy\s+block\b|\bwhich\s+wards?\b|"
        r"\bposting\b|\bdesignation\b|who\s+am\s+i|எனது\s+வார்டு", re.IGNORECASE),
    "documents": re.compile(
        r"\brule\b|\brules\b|\bprocedure\b|\bhow\s+(?:do|can|to)\b|"
        r"\bdocument(?:s)?\b[^.?!]{0,20}\b(?:needed|required)\b|"
        r"\b(?:needed|required)\b[^.?!]{0,20}\bdocument(?:s)?\b|"
        r"\bguideline\b|\bpolicy\b|\bwhat\s+is\s+(?:the\s+)?sla\b|"
        r"விதி|நடைமுறை", re.IGNORECASE),
}

# A full application number ("2026/0154/28/000001") embeds its own service
# code -- stripped before group matching for the same reason CLAUDE.md
# documents for the comparison parser: left in, "compare 2026/0154/28/000001
# and ..." reads its "0154" as the fee/service-code group's own trigger
# (`\b015[0-9]\b`), not as part of the number it actually is.
_APP_NUMBER_IN_TEXT_RE = re.compile(r"\b\d{4}\s*/\s*\d{3,4}\s*/\s*\d{2}\s*/\s*\d{3,6}\b")


def _matching_groups(text: str) -> List[str]:
    stripped = _APP_NUMBER_IN_TEXT_RE.sub(" ", text or "")
    return [group for group, pattern in _GROUP_PATTERNS.items() if pattern.search(stripped)]


def _is_short_followup(message: str) -> bool:
    words = re.findall(r"[^\s]+", message or "")
    return len(words) <= 4


def select_tool_names(message: str, chat_history: Optional[list] = None) -> List[str]:
    """The tools worth offering the model for this one turn.

    Scans `message` for the keyword groups above and returns the union of
    their tools, in registry order. A short, pronoun-shaped follow-up ("and
    the fee?", "same for ward 002") rarely repeats the noun that named its
    own category, so a short message also pulls in whatever the last couple
    of turns matched -- the same reasoning `followup_context.py` already
    applies to the deterministic handlers, just for tool selection instead
    of intent routing. Nothing matched anywhere falls back to a small,
    broadly useful default rather than the full 22 or an empty list; a
    non-empty match under 5 tools is padded from that same default so an
    ordinary single-intent question still gets a workable set, without
    ever dropping a tool a matched group actually named."""
    groups = _matching_groups(message)

    if (not groups or _is_short_followup(message)) and chat_history:
        for turn in reversed(chat_history[-4:]):
            content = turn.get("content") if isinstance(turn, dict) else None
            if not content:
                continue
            groups.extend(g for g in _matching_groups(content) if g not in groups)
            if groups and not _is_short_followup(message):
                break

    seen: List[str] = []
    for group in groups:
        for name in TOOL_GROUPS[group]:
            if name not in seen:
                seen.append(name)

    if not seen:
        return list(_DEFAULT_TOOL_NAMES)

    if len(seen) < 5:
        for name in _DEFAULT_TOOL_NAMES:
            if name not in seen:
                seen.append(name)
            if len(seen) >= 5:
                break

    # Keep registry order rather than the (group, then default-padding) order
    # built above, so the model sees a stable ordering turn to turn.
    order = {t.name: i for i, t in enumerate(TOOLS)}
    seen.sort(key=lambda n: order.get(n, len(TOOLS)))
    return seen


def tool_schemas(names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """The tool list in the shape ChatOllama.bind_tools accepts.

    With no `names`, every registered tool (unchanged, backward-compatible).
    With `names`, only the tools in that set -- an unrecognised name is
    silently dropped rather than raised, since the caller's own
    `select_tool_names()` only ever produces real tool names and a stale
    allowlist (an old category referencing a renamed tool) is a smaller
    failure than refusing the whole turn over it. Registry order is kept
    regardless, so the model sees the same ordering it always has."""
    if names is None:
        return [t.schema() for t in TOOLS]
    wanted = set(names)
    return [t.schema() for t in TOOLS if t.name in wanted]


async def execute_tool(
    ctx: ToolContext,
    name: str,
    raw_args: Optional[Dict[str, Any]],
    allowed: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Timed entry point (request telemetry `tools_called` / `tool_duration_ms`); see `_execute_tool_impl`."""
    import time as _time
    from backend.services.observability import record_tool
    started = _time.perf_counter()
    try:
        return await _execute_tool_impl(ctx, name, raw_args, allowed)
    finally:
        record_tool(str(name), (_time.perf_counter() - started) * 1000)


async def _execute_tool_impl(
    ctx: ToolContext,
    name: str,
    raw_args: Optional[Dict[str, Any]],
    allowed: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Validate and run one tool call. Never raises for a bad call; reports refusal cleanly.

    `allowed`, when given, is this turn's tool-scoping allowlist (see
    `select_tool_names()`). Hiding a tool from the model's schema is not
    enough on its own -- a model can still emit a call for a tool it was
    never shown, so this is checked again here, at execution, independent
    of whatever `tool_schemas()` was bound with. This is a SCOPE check only:
    it narrows which of the already-authorized tools this turn may use, and
    is never a substitute for `ToolAuthorizationError`'s jurisdiction check
    below, which still runs for every allowed call exactly as before."""
    record: Dict[str, Any] = {"tool": name, "arguments": raw_args or {}}
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        record["error"] = f"unknown tool '{name}'"
        ctx.calls.append(record)
        return {"error": record["error"], "available_tools": sorted(TOOLS_BY_NAME)}
    if allowed is not None and name not in allowed and spec.name not in allowed:
        record["error"] = f"'{name}' is not available for this question"
        record["refused"] = "scope"
        ctx.calls.append(record)
        return {
            "error": record["error"],
            "refused": True,
            "available_tools": sorted(allowed),
            "instruction": (
                "This tool was not offered for this turn. Answer using only "
                "the tools actually available to you; do not mention this "
                "message or the tool's name to the officer."
            ),
        }
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
    except Exception as exc:
        logger.error(f"agent_tools: {name} failed: {exc}", exc_info=True)
        record["error"] = repr(exc)
        ctx.calls.append(record)
        return {"error": f"The {name} query failed. Tell the officer the data could "
                         f"not be retrieved; do not answer from memory."}
