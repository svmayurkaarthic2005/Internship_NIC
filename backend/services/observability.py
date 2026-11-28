"""
Observability and Telemetry Engine for SIS Chatbot (Phase 8).

Tracks structured request metrics and failure diagnostics:
- Request ID & Session ID (privacy-safe hash)
- Officer ID & Jurisdiction
- Intent classification result
- Tool calls & tool execution duration
- Retrieval duration & LLM generation duration
- Total latency
- Failure category taxonomy
- Token usage & Model telemetry
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from contextvars import ContextVar

from backend.utils.logger import get_logger
from backend.services.security import hash_session_id

logger = get_logger("backend.services.observability")

# ContextVar for tracing request across async execution boundaries
current_request_id: ContextVar[str] = ContextVar("current_request_id", default="")


def _configured_model() -> str:
    """The model this process actually calls (settings.LLM_MODEL), not a hardcoded name."""
    from backend.config import settings
    return settings.LLM_MODEL


@dataclass
class RequestMetrics:
    """Telemetry context collected across a single turn."""
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id_hash: str = "none"
    officer_id: str = "anonymous"
    officer_role: str = ""
    jurisdiction_type: str = ""
    intent: Optional[str] = None
    language: str = "en"
    tools_called: List[str] = field(default_factory=list)
    tool_duration_ms: float = 0.0
    retrieval_duration_ms: float = 0.0
    llm_duration_ms: float = 0.0
    total_latency_ms: float = 0.0
    model_used: str = field(default_factory=_configured_model)
    token_usage: Dict[str, int] = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0})
    failure_category: Optional[str] = None
    error_message: Optional[str] = None
    start_time: float = field(default_factory=time.perf_counter)

    def record_tool_call(self, tool_name: str, duration_ms: float):
        self.tools_called.append(tool_name)
        self.tool_duration_ms += duration_ms

    def record_retrieval(self, duration_ms: float):
        self.retrieval_duration_ms += duration_ms

    def record_llm_call(self, duration_ms: float, tokens: Optional[Dict[str, int]] = None):
        self.llm_duration_ms += duration_ms
        if tokens:
            self.token_usage["prompt_tokens"] += tokens.get("prompt_tokens", 0)
            self.token_usage["completion_tokens"] += tokens.get("completion_tokens", 0)

    def mark_failure(self, category: str, error_msg: str):
        self.failure_category = category
        self.error_message = error_msg

    def finalize(self) -> Dict[str, Any]:
        """Compute final latency and produce telemetry payload."""
        if self.total_latency_ms == 0.0:
            self.total_latency_ms = round((time.perf_counter() - self.start_time) * 1000, 2)

        payload = {
            "telemetry": "request_summary",
            "request_id": self.request_id,
            "session_id_hash": self.session_id_hash,
            "officer_id": self.officer_id,
            "officer_role": self.officer_role,
            "intent": self.intent,
            "language": self.language,
            "tools_called": self.tools_called,
            "tool_duration_ms": round(self.tool_duration_ms, 2),
            "retrieval_duration_ms": round(self.retrieval_duration_ms, 2),
            "llm_duration_ms": round(self.llm_duration_ms, 2),
            "total_latency_ms": self.total_latency_ms,
            "model_used": self.model_used,
            "token_usage": self.token_usage,
            "failure_category": self.failure_category,
            "success": self.failure_category is None,
        }

        if self.failure_category:
            logger.warning("Turn finished with failure", extra=payload)
        else:
            logger.info("Turn telemetry recorded", extra=payload)

        return payload


def create_request_metrics(
    session_id: str,
    officer_id: Optional[str] = None,
    officer_role: Optional[str] = None,
    jurisdiction_type: Optional[str] = None
) -> RequestMetrics:
    """Factory to initialize metrics for an incoming request."""
    req_id = str(uuid.uuid4())
    current_request_id.set(req_id)
    return RequestMetrics(
        request_id=req_id,
        session_id_hash=hash_session_id(session_id),
        officer_id=str(officer_id) if officer_id else "anonymous",
        officer_role=officer_role or "",
        jurisdiction_type=jurisdiction_type or ""
    )


# The turn's metrics, set by the chat router (both endpoints). The recorders below are what the
# LLM gate, vector retrieval and the agent's tool executor call; outside a chat request (tests,
# scripts) there is no current metrics object and they do nothing.
current_metrics: ContextVar[Optional[RequestMetrics]] = ContextVar("current_metrics", default=None)


def record_llm(duration_ms: float, tokens: Optional[Dict[str, int]] = None) -> None:
    m = current_metrics.get()
    if m is not None:
        m.record_llm_call(duration_ms, tokens)


def record_retrieval(duration_ms: float) -> None:
    m = current_metrics.get()
    if m is not None:
        m.record_retrieval(duration_ms)


def record_tool(tool_name: str, duration_ms: float) -> None:
    m = current_metrics.get()
    if m is not None:
        m.record_tool_call(tool_name, duration_ms)
