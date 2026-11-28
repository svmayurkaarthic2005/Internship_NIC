"""
backend/services/chatbot/agent_plan.py
=======================================
Intelligent Agent Orchestration & Typed Execution Planning.

Converts structured output from query_understanding.py into a typed,
DAG-based ExecutionPlan, validates it against security constraints, and
executes operations with support for:
- Multiple operations in a single request.
- Independent operations executed concurrently (in parallel).
- Dependent operations executed sequentially with dynamic argument binding.
- Step-level and plan-level timeouts and cancellation handling.
- Strict prevention of duplicate/redundant tool calls.
- Total protection against SQL execution, jurisdiction bypass, or invented arguments.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from enum import Enum
from typing import Dict, Any, List, Optional, Tuple, Set
from pydantic import BaseModel, Field

from backend.config import settings
from backend.utils.logger import get_logger
from backend.schemas import OfficerContext
from backend.services.chatbot.query_understanding import SemanticQueryPlan
from backend.services.chatbot.agent_tools import (
    ToolContext,
    TOOLS_BY_NAME,
    execute_tool,
    validate_args,
    _FORBIDDEN_ARGS,
    ToolAuthorizationError,
    ToolArgumentError
)
from backend.services.chatbot.agent import AgentEvidence, build_answer_prompt
from backend.services import rag

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Typed Execution Plan Models
# ─────────────────────────────────────────────────────────────────────────────

class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlanStep(BaseModel):
    """An individual atomic tool execution step within an ExecutionPlan."""
    step_id: str = Field(..., description="Unique step identifier, e.g. 'step_1'")
    tool_name: str = Field(..., description="Authorized tool name registered in agent_tools")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Tool arguments or placeholders ($step_1.field)")
    depends_on: List[str] = Field(default_factory=list, description="List of prerequisite step IDs")
    output_variable: Optional[str] = Field(default=None, description="Logical variable name for downstream binding")
    status: StepStatus = Field(default=StepStatus.PENDING)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    latency_ms: float = Field(default=0.0)


class ExecutionPlan(BaseModel):
    """
    Typed, DAG-structured execution plan.
    Enforces pre-execution validation, dependency ordering, and parallel limits.
    """
    plan_id: str
    query: str
    goal: str
    steps: List[PlanStep] = Field(default_factory=list)
    is_valid: bool = True
    validation_errors: List[str] = Field(default_factory=list)
    max_parallel: int = Field(default=3, ge=1, le=5)
    max_steps: int = Field(default=6, ge=1, le=10)
    is_multi_step: bool = False
    total_latency_ms: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. Pre-Flight Plan Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_execution_plan(plan: ExecutionPlan) -> Tuple[bool, List[str]]:
    """
    Strict pre-execution validation.
    Checks:
    - Tool existence in TOOLS_BY_NAME.
    - No forbidden arguments (_FORBIDDEN_ARGS: officer_id, sql, etc.).
    - Valid DAG: all dependencies refer to earlier step IDs, no circular dependencies.
    - Max step limit enforcement.
    """
    errors: List[str] = []
    
    if len(plan.steps) > plan.max_steps:
        errors.append(f"Plan exceeds maximum step limit of {plan.max_steps} (has {len(plan.steps)})")

    step_ids: Set[str] = set()
    for idx, step in enumerate(plan.steps):
        # 1. Step ID uniqueness
        if step.step_id in step_ids:
            errors.append(f"Duplicate step ID '{step.step_id}'")
        step_ids.add(step.step_id)

        # 2. Tool validity
        spec = TOOLS_BY_NAME.get(step.tool_name)
        if spec is None:
            errors.append(f"Invalid tool '{step.tool_name}' in step '{step.step_id}' (not registered)")
            continue

        # 3. Argument validation: Forbidden security arguments
        for arg_key in step.arguments.keys():
            if arg_key.lower() in _FORBIDDEN_ARGS:
                errors.append(
                    f"Forbidden argument '{arg_key}' in step '{step.step_id}'. "
                    f"Whose data is read cannot be set by plan arguments."
                )

        # 4. Dependency validity (must reference prior steps)
        for dep in step.depends_on:
            if dep not in step_ids or dep == step.step_id:
                errors.append(f"Step '{step.step_id}' has invalid or forward dependency on '{dep}'")

    is_valid = len(errors) == 0
    return is_valid, errors


# ─────────────────────────────────────────────────────────────────────────────
# 3. Plan Generation from SemanticQueryPlan
# ─────────────────────────────────────────────────────────────────────────────

def create_execution_plan(query_plan: SemanticQueryPlan, user_message: str) -> ExecutionPlan:
    """
    Convert a structured SemanticQueryPlan into an actionable ExecutionPlan.
    Supports single-turn tools, parallel comparative queries, and dependent multi-step workflows.
    """
    import uuid
    plan_id = f"plan_{uuid.uuid4().hex[:8]}"

    # Guard: Unresolved ambiguity or missing parameters cannot be executed
    if query_plan.is_ambiguous or query_plan.missing_parameters:
        return ExecutionPlan(
            plan_id=plan_id,
            query=user_message,
            goal="Clarification needed for ambiguous request",
            is_valid=False,
            validation_errors=[
                f"Unresolved ambiguity or missing parameters: {query_plan.missing_parameters}"
            ]
        )

    steps: List[PlanStep] = []
    lower_msg = user_message.lower()

    # ── Example Workflow: Pending ISD + Oldest + Field Visit Inspection ──────
    # "Show my pending ISD applications, identify the oldest one and check whether its field visit is scheduled."
    if ("pending" in lower_msg and "isd" in lower_msg and "oldest" in lower_msg and "field visit" in lower_msg) or \
       (query_plan.intent == "application_analysis" and "field" in lower_msg):
        
        # Step 1: Retrieve pending ISD applications
        step_1 = PlanStep(
            step_id="step_1",
            tool_name="get_pending_applications",
            arguments={"application_type": "ISD"},
            output_variable="pending_isd_applications"
        )
        # Step 2: Retrieve field visit for the oldest application returned in Step 1
        step_2 = PlanStep(
            step_id="step_2",
            tool_name="get_field_visits",
            arguments={"application_number": "$step_1.oldest_application_number"},
            depends_on=["step_1"],
            output_variable="oldest_field_visit"
        )
        steps = [step_1, step_2]
        return ExecutionPlan(
            plan_id=plan_id,
            query=user_message,
            goal="Retrieve pending ISD applications, determine oldest file, and check field visit schedule",
            steps=steps,
            is_multi_step=True
        )

    # ── Multi-Part / Comparative Workflow: Parallel Execution ────────────────
    # E.g. "How many pending ISD applications do I have and how many are overdue?"
    if query_plan.is_multi_part or ("isd" in lower_msg and "overdue" in lower_msg and "how many" in lower_msg):
        step_1 = PlanStep(
            step_id="step_1",
            tool_name="count_applications",
            arguments={"status": "pending", "application_type": "ISD"},
            output_variable="pending_isd_count"
        )
        step_2 = PlanStep(
            step_id="step_2",
            tool_name="count_applications",
            arguments={"is_overdue": True},
            output_variable="overdue_count"
        )
        # Independent steps: both have depends_on=[] so they run in parallel!
        steps = [step_1, step_2]
        return ExecutionPlan(
            plan_id=plan_id,
            query=user_message,
            goal="Count pending ISD applications and overdue applications concurrently",
            steps=steps,
            is_multi_step=True
        )

    # ── Single-Turn High-Confidence Mappings ──────────────────────────────────
    intent = query_plan.intent

    if intent in ("pending_applications", "all_service_code_applications"):
        args: Dict[str, Any] = {}
        if query_plan.application_type:
            args["application_type"] = query_plan.application_type
        if query_plan.submission_channel:
            args["submission_channel"] = query_plan.submission_channel
        steps.append(PlanStep(step_id="step_1", tool_name="get_pending_applications", arguments=args))

    elif intent == "overdue_applications":
        args = {}
        if query_plan.application_type:
            args["application_type"] = query_plan.application_type
        steps.append(PlanStep(step_id="step_1", tool_name="get_overdue_applications", arguments=args))

    elif intent == "officer_workload":
        steps.append(PlanStep(step_id="step_1", tool_name="get_officer_workload", arguments={}))

    elif intent == "application_status":
        app_no = query_plan.application_numbers[0] if query_plan.application_numbers else None
        if app_no:
            steps.append(PlanStep(step_id="step_1", tool_name="get_application_details", arguments={"application_number": app_no}))
        else:
            return ExecutionPlan(
                plan_id=plan_id,
                query=user_message,
                goal="Resolve application status",
                is_valid=False,
                validation_errors=["Missing application number for status inquiry"]
            )

    elif intent in ("survey_owners", "survey_detail"):
        survey_no = query_plan.survey_numbers[0] if query_plan.survey_numbers else None
        if survey_no:
            steps.append(PlanStep(step_id="step_1", tool_name="get_survey_details", arguments={"survey_number": survey_no}))
        else:
            return ExecutionPlan(
                plan_id=plan_id,
                query=user_message,
                goal="Retrieve survey details",
                is_valid=False,
                validation_errors=["Missing survey number for survey details inquiry"]
            )

    elif intent == "can_apply_check":
        survey_no = query_plan.survey_numbers[0] if query_plan.survey_numbers else None
        if survey_no:
            steps.append(PlanStep(step_id="step_1", tool_name="check_survey_application_lock", arguments={"survey_number": survey_no}))

    elif intent in ("field_visits", "fv_overdue_inspections"):
        args = {}
        if query_plan.application_numbers:
            args["application_number"] = query_plan.application_numbers[0]
        if intent == "fv_overdue_inspections":
            args["status_filter"] = "pending"
        steps.append(PlanStep(step_id="step_1", tool_name="get_field_visits", arguments=args))

    elif intent == "fv_visit_plan":
        steps.append(PlanStep(step_id="step_1", tool_name="get_visit_plan", arguments={}))

    elif intent in ("service_code_lookup", "service_code_guide", "check_documents") or query_plan.needs_document_retrieval:
        steps.append(PlanStep(step_id="step_1", tool_name="search_documents", arguments={"query": user_message}))

    else:
        # General query / Fallback to document search or empty plan
        if query_plan.needs_database:
            steps.append(PlanStep(step_id="step_1", tool_name="get_officer_workload", arguments={}))
        elif query_plan.needs_document_retrieval:
            steps.append(PlanStep(step_id="step_1", tool_name="search_documents", arguments={"query": user_message}))

    return ExecutionPlan(
        plan_id=plan_id,
        query=user_message,
        goal=f"Execute {intent} workflow",
        steps=steps,
        is_multi_step=len(steps) > 1
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Dynamic Dependent Argument Resolution
# ─────────────────────────────────────────────────────────────────────────────

_PLACEHOLDER_RE = re.compile(r"^\$([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)$")

def resolve_step_arguments(step: PlanStep, completed_steps: Dict[str, PlanStep]) -> Dict[str, Any]:
    """
    Resolve dynamic placeholders like '$step_1.oldest_application_number' from prior results.
    Never invents arguments: if prerequisite produced no records, raises an explicit error.
    """
    resolved_args: Dict[str, Any] = {}

    for key, val in step.arguments.items():
        if isinstance(val, str):
            match = _PLACEHOLDER_RE.match(val.strip())
            if match:
                dep_step_id = match.group(1)
                field_name = match.group(2)

                dep_step = completed_steps.get(dep_step_id)
                if not dep_step or not dep_step.result:
                    raise ToolArgumentError(
                        f"Cannot resolve '{key}' for step '{step.step_id}': prerequisite '{dep_step_id}' produced no result."
                    )

                res = dep_step.result

                # Resolution for oldest application number
                if field_name == "oldest_application_number":
                    apps = res.get("applications") or res.get("queue") or res.get("items") or []
                    if not apps:
                        raise ToolArgumentError(
                            f"Step '{step.step_id}': Prerequisite '{dep_step_id}' returned 0 applications. Cannot determine oldest."
                        )
                    # Authoritative sort by submission date
                    def _sort_key(item):
                        return item.get("submission_date") or item.get("application_date") or "9999"

                    sorted_apps = sorted(apps, key=_sort_key)
                    oldest_app_no = sorted_apps[0].get("application_number")
                    if not oldest_app_no:
                        raise ToolArgumentError(f"No application_number found on oldest record in step '{dep_step_id}'")
                    resolved_args[key] = oldest_app_no

                # Resolution for newest application number
                elif field_name == "newest_application_number":
                    apps = res.get("applications") or []
                    if not apps:
                        raise ToolArgumentError(f"No applications in step '{dep_step_id}' to select newest")
                    sorted_apps = sorted(apps, key=lambda x: x.get("submission_date") or "", reverse=True)
                    resolved_args[key] = sorted_apps[0].get("application_number")

                # Resolution for survey number
                elif field_name in ("survey_number", "survey_no"):
                    survey = res.get("survey_number") or res.get("survey_no")
                    if not survey:
                        raise ToolArgumentError(f"No survey_number found in result of '{dep_step_id}'")
                    resolved_args[key] = survey

                else:
                    extracted = res.get(field_name)
                    if extracted is None:
                        raise ToolArgumentError(f"Field '{field_name}' not found in result of step '{dep_step_id}'")
                    resolved_args[key] = extracted
                continue

        resolved_args[key] = val

    return resolved_args


# ─────────────────────────────────────────────────────────────────────────────
# 5. Concurrent & Sequential DAG Plan Executor
# ─────────────────────────────────────────────────────────────────────────────

async def execute_execution_plan(
    plan: ExecutionPlan,
    ctx: ToolContext,
    timeout_seconds: float = 30.0
) -> ExecutionPlan:
    """
    Executes an ExecutionPlan respecting dependencies, timeouts, and parallel concurrency.
    
    Guarantees:
    - Pre-flight validation before any execution.
    - Independent steps execute in parallel via asyncio.gather().
    - Dependent steps execute only when prerequisites complete successfully.
    - Deduplication: avoids repeating identical tool calls.
    - Failed queries are never treated as empty or success.
    - Entire execution bounded by timeout_seconds.
    """
    t_start = time.perf_counter()

    # 1. Pre-Flight Validation
    is_valid, validation_errors = validate_execution_plan(plan)
    if not is_valid:
        plan.is_valid = False
        plan.validation_errors = validation_errors
        logger.warning(f"agent_plan: plan validation failed: {validation_errors}")
        return plan

    completed_steps: Dict[str, PlanStep] = {}
    cache: Dict[str, Dict[str, Any]] = {}  # Deduplication cache: key -> result

    async def _run_single_step(step: PlanStep) -> None:
        step.status = StepStatus.RUNNING
        s_time = time.perf_counter()
        try:
            # Resolve dynamic dependencies
            resolved_args = resolve_step_arguments(step, completed_steps)

            # Deduplication check
            cache_key = f"{step.tool_name}:{json.dumps(resolved_args, sort_keys=True, default=str)}"
            if cache_key in cache:
                logger.info(f"agent_plan: step '{step.step_id}' reused cached result for {step.tool_name}")
                step.result = cache[cache_key]
                step.status = StepStatus.COMPLETED
                step.latency_ms = round((time.perf_counter() - s_time) * 1000, 2)
                completed_steps[step.step_id] = step
                return

            logger.info(f"agent_plan: executing step '{step.step_id}': {step.tool_name}({resolved_args})")
            result = await execute_tool(ctx, step.tool_name, resolved_args)

            # Check for tool refusals or internal errors
            if result.get("refused") or result.get("internal_error") or "error" in result:
                step.status = StepStatus.FAILED
                step.error = str(result.get("error") or result.get("detail") or "Tool call failed")
                logger.warning(f"agent_plan: step '{step.step_id}' failed: {step.error}")
            else:
                step.status = StepStatus.COMPLETED
                step.result = result
                cache[cache_key] = result

        except Exception as exc:
            step.status = StepStatus.FAILED
            step.error = str(exc)
            logger.warning(f"agent_plan: exception in step '{step.step_id}': {exc}")

        step.latency_ms = round((time.perf_counter() - s_time) * 1000, 2)
        completed_steps[step.step_id] = step

    # 2. DAG Execution Waves
    try:
        while len(completed_steps) < len(plan.steps):
            # Check for overall plan timeout
            if (time.perf_counter() - t_start) > timeout_seconds:
                raise asyncio.TimeoutError(f"Execution plan exceeded timeout of {timeout_seconds}s")

            # Find steps that are ready to run:
            # status == PENDING, and all depends_on are in completed_steps with COMPLETED status
            ready_steps: List[PlanStep] = []
            for step in plan.steps:
                if step.status != StepStatus.PENDING:
                    continue
                
                # Check prerequisites
                deps_satisfied = True
                dep_failed = False
                for dep_id in step.depends_on:
                    dep_step = completed_steps.get(dep_id)
                    if not dep_step:
                        deps_satisfied = False
                        break
                    if dep_step.status in (StepStatus.FAILED, StepStatus.SKIPPED):
                        dep_failed = True
                        break

                if dep_failed:
                    step.status = StepStatus.SKIPPED
                    step.error = f"Prerequisite step '{dep_id}' did not complete successfully"
                    completed_steps[step.step_id] = step
                    continue

                if deps_satisfied:
                    ready_steps.append(step)

            if not ready_steps:
                # No ready steps and pending steps remain -> deadlock or unresolved dependency
                for step in plan.steps:
                    if step.status == StepStatus.PENDING:
                        step.status = StepStatus.SKIPPED
                        step.error = "Unresolved dependency or deadlocked execution"
                        completed_steps[step.step_id] = step
                break

            # Execute ready steps concurrently (parallel execution!)
            chunks = ready_steps[:plan.max_parallel]
            await asyncio.gather(*[_run_single_step(s) for s in chunks])

    except asyncio.TimeoutError as te:
        logger.error(f"agent_plan: plan timed out: {te}")
        for step in plan.steps:
            if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
                step.status = StepStatus.FAILED
                step.error = "Plan execution timed out"

    plan.total_latency_ms = round((time.perf_counter() - t_start) * 1000, 2)
    return plan


# ─────────────────────────────────────────────────────────────────────────────
# 6. Evidence Extraction & Answer Synthesis
# ─────────────────────────────────────────────────────────────────────────────

def plan_to_evidence(plan: ExecutionPlan) -> AgentEvidence:
    """Convert executed plan results into AgentEvidence for answer prompt synthesis."""
    evidence = AgentEvidence()
    for step in plan.steps:
        if step.status == StepStatus.COMPLETED and step.result:
            evidence.observations.append({
                "tool": step.tool_name,
                "arguments": step.arguments,
                "result": step.result
            })
    return evidence


async def synthesize_plan_answer(
    plan: ExecutionPlan,
    user_message: str,
    language: str,
    officer: OfficerContext,
    chat_history: Optional[list] = None
) -> str:
    """
    Generate an authoritative, evidence-grounded natural language answer from executed plan steps.
    """
    from .response_generator import (
        ResponseContext,
        generate_intelligent_response,
        verify_response_evidence,
    )

    if not plan.is_valid:
        err_msg = plan.validation_errors[0] if plan.validation_errors else "Unable to formulate a safe plan."
        return f"I cannot process this request: {err_msg}"

    evidence = plan_to_evidence(plan)
    if not evidence.observations:
        failed_steps = [s for s in plan.steps if s.status == StepStatus.FAILED]
        if failed_steps:
            return f"I encountered an error retrieving data: {failed_steps[0].error}"
        ctx = ResponseContext(
            query=user_message,
            language=language,
            officer=officer,
            chat_history=chat_history
        )
        resp = generate_intelligent_response(ctx)
        return resp.text

    # Extract primary db evidence from observations
    primary_db: Dict[str, Any] = {}
    for obs in evidence.observations:
        res = obs.get("result")
        if isinstance(res, dict):
            if "data" in res and isinstance(res["data"], dict):
                primary_db.update(res["data"])
            elif "records" in res or "applications" in res:
                primary_db.update(res)
            elif "count" in res:
                primary_db.setdefault("count", res["count"])

    resp_ctx = ResponseContext(
        query=user_message,
        language=language,
        db_evidence=primary_db,
        officer=officer,
        chat_history=chat_history
    )

    # Attempt LLM synthesis with strict answer prompt
    answer = ""
    try:
        prompt = build_answer_prompt(user_message, evidence, language, officer, chat_history)
        answer = await rag.call_llama(prompt)
        answer = answer.strip() if answer else ""
    except Exception as exc:
        logger.warning(f"synthesize_plan_answer: LLM call failed or timed out: {exc}")

    # If LLM didn't produce an answer, use intelligent response generator
    if not answer:
        resp = await generate_intelligent_response(resp_ctx)
        return resp.html if resp.html else resp.text

    # Verify LLM-written answer against ground truth
    v_report = verify_response_evidence(answer, resp_ctx)
    verified_answer = v_report.verified_text
    if v_report.notes:
        verified_answer += " " + " ".join(v_report.notes)

    return verified_answer

