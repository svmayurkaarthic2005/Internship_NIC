"""
The LLM tool-calling layer.

Where this sits
---------------
`chatbot.py` resolves ~60 intents deterministically and answers most of them
from Python with numbers taken straight from the database. Those handlers are
the trusted path and this module does not touch them. It runs at exactly one
place: the point where the pipeline had given up and was about to hand
llama3.1:8b a free-text prompt with whatever RAG context happened to be
retrieved. That call could not look anything up, so it answered general
questions plausibly and specific ones wrongly.

The agent replaces that single call with a bounded tool-calling loop over the
authorized tools in `agent_tools.py`, and then generates the answer from what
those tools returned. If anything in the loop fails -- Ollama down, the model
emitting nothing usable, the whole thing running long -- `AgentUnavailable` is
raised and the caller runs the original prompt, so the fallback path never gets
worse than it was.

What keeps it honest
--------------------
* Authorization is not the model's job. The officer is bound into `ToolContext`
  before the loop starts; no tool schema has an officer argument; every query
  goes through the existing `get_jurisdiction_filter()`. See `agent_tools`.
* Counts, application numbers, names, dates and statuses come from tool
  results. The answer prompt says so, and the deterministic handlers upstream
  already cover the high-traffic numeric questions, so the agent is not the
  only thing standing between an officer and an invented count.
* The loop is bounded in rounds and in wall-clock time.

Two passes, deliberately
------------------------
The loop picks and runs tools; a second, tool-free call writes the answer from
their results. It costs one extra generation, and buys three things: the answer
can be streamed token by token (a tool-bound call cannot be, since its first
token may be a tool call), the answer prompt can state the grounding rules
without competing with the tool-selection instructions, and a model that
finishes its tool round with an empty message still produces an answer.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.schemas import OfficerContext
from backend.services import rag
from backend.services.agent_tools import ToolContext, execute_tool, tool_schemas
from backend.utils.logger import get_logger

logger = get_logger(__name__)


class AgentUnavailable(Exception):
    """The agent could not produce grounded evidence. The caller must fall back
    to the pre-existing prompt path rather than surfacing this."""


@dataclass
class AgentEvidence:
    """What the tool loop found, and what it did to find it."""

    observations: List[Dict[str, Any]] = field(default_factory=list)
    calls: List[Dict[str, Any]] = field(default_factory=list)
    rounds: int = 0
    model_reply: str = ""          # what the model said when it stopped calling tools

    @property
    def used_tools(self) -> List[str]:
        return [c["tool"] for c in self.calls]

    @property
    def grounded(self) -> bool:
        return any(c.get("ok") for c in self.calls)


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────
# The tool-selection prompt deliberately does NOT name the officer's ward or
# block. It used to, and llama3.1:8b copied that string straight back out as a
# tool argument -- `count_applications(block_number="ward 102")` on the
# question "how many applications do I have?", which the geography check then
# (correctly) refused, costing the officer their answer. Telling the model not
# to do it was not enough; the fix is to not put the value in front of it. Any
# turn that genuinely needs the officer's geography calls get_my_jurisdiction.
_TOOL_SYSTEM = """You are the SIS assistant for the Tamil Nadu Survey Department. \
You are helping {officer_name}, {designation}.

Your job in this step is ONLY to decide which tools to call. Do not write the \
final answer yet.

Rules:
- Any question about applications, surveys, field visits, workload, fees or \
dates MUST be answered from a tool call. You have no memory of this \
department's records and must never guess an application number, a count, a \
name, a status or a date.
- Questions about rules, procedure, definitions, service codes or timelines go \
to search_documents.
- A question with several parts needs several tool calls. Make them all.
- The officer is already authenticated and every tool is ALREADY scoped to \
their jurisdiction. Never pass an officer, district or jurisdiction argument to \
a tool; you cannot choose whose data is read.
- Pass ONLY the filters the officer actually asked for. Never narrow a question \
on your own: "which applications are overdue?" takes no type and no status, and \
adding one produces an answer that is true of your filter and false of the \
question.
- Do NOT pass ward_number or block_number unless the officer typed a specific \
ward or block number in their own question. Their jurisdiction is applied for \
them automatically. "How many applications do I have?" takes no ward and no \
block. To report which wards or blocks they cover, call get_my_jurisdiction.
- If a tool is REFUSED (a ward or block outside the jurisdiction) or its query \
FAILED, do not retry it with the same arguments and do not work around it. Stop \
and let the answer report it.
- If a tool result says "internal_error", the arguments were malformed. That is \
your mistake, not a refusal: call the same tool again with them corrected, \
dropping any argument the officer did not actually ask for. Never report it to \
the officer.
- If the question is small talk, or is fully answered by the conversation so \
far, call no tool at all.
- Resolve pronouns ("it", "them", "that one") from the conversation before \
choosing arguments."""

_ANSWER_SYSTEM = """You are the SIS assistant for the Tamil Nadu Survey Department, \
answering {officer_name} ({designation}, {jurisdiction_type} {jurisdiction_name}).

Write the final answer now, from the TOOL RESULTS below and the conversation.

Rules:
- Every number, application number, name, date, status, survey number and count \
must come from the tool results verbatim. Never adjust, round, re-derive or \
supplement them from your own knowledge. If a value is not in the results, say \
it is not on record.
- If a tool result is an error or a refusal, tell the officer plainly what could \
not be retrieved or what is outside their jurisdiction. Never fill the gap with \
a plausible answer.
- Never name a tool, an argument, a parameter, a limit or an internal message in \
your answer. The officer is a survey officer, not an operator of this system. A \
result marked "internal_error" must not be described at all -- answer from the \
other results, or say the information could not be retrieved just now.
- A refusal is NOT an absence of records. If a ward or block was refused as \
outside the officer's jurisdiction, say they cannot see it -- never say there \
are none there, and never report a count of zero for it. You do not know what \
is there.
- Report each figure as the thing the tool actually called it. A count of OPEN \
files is not a count of approved ones; if the results do not contain what was \
asked for, say so rather than relabelling the nearest number.
- If a tool was called with filters, the number it returned covers ONLY those \
filters -- say which. "No overdue ISD applications" is honest; "no overdue \
applications", when only ISD was counted, is not.
- You can only READ. Never say that anything was changed, deleted, cleared, \
reset, updated, approved, rejected, scheduled or re-ingested -- you have no \
tool that does any of those and none of it has happened. Asked to change \
something, say plainly that you can only read the records and point to the \
TAMILNILAM portal. Reporting an action that did not occur is worse than \
refusing it.
- Answer only what was asked, in a few sentences. No preamble, no invented \
next steps, no markdown tables.
- {language_rule}"""

# Sent once, and only when the first round picked no tool at all.
_NO_TOOL_NUDGE = (
    "You called no tool. If answering that question requires ANY record from "
    "the department's data -- a count, an application, a survey number, a date, "
    "a status, a workload, a fee -- or any rule from the reference documents, "
    "call the tools you need now, one per part of the question. You cannot "
    "answer from memory. But if it needs no data at all -- a greeting, a thank "
    "you, an acknowledgement, or something the conversation above already "
    "answered -- reply again without calling a tool. Do not call a tool just to "
    "have called one."
)

_LANGUAGE_RULES = {
    "ta": "Reply entirely in Tamil.",
    "tanglish": "Reply in Tanglish -- Tamil written in Roman script, the way the "
                "officer wrote. Keep application numbers and codes as they are.",
    "en": "Reply in English.",
}


def _officer_bits(officer: OfficerContext) -> Dict[str, str]:
    return {
        "officer_name": getattr(officer, "name", "the officer"),
        "designation": getattr(officer, "designation", None) or "SIS Officer",
        "jurisdiction_type": getattr(officer, "jurisdiction_type", "") or "",
        "jurisdiction_name": getattr(officer, "jurisdiction_name", "") or "",
    }


def _history_messages(chat_history: Optional[list], depth: int = 6) -> List[Any]:
    """Recent turns as chat messages, so follow-ups resolve their references.

    Only the last few turns: llama3.1:8b starts choosing tools by what it saw
    earlier rather than by what was just asked when the transcript grows.
    """
    out: List[Any] = []
    for turn in (chat_history or [])[-depth:]:
        role = (turn.get("role") or "").lower()
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        content = content[:1500]
        if role in ("user", "human", "officer"):
            out.append(HumanMessage(content=content))
        elif role in ("assistant", "ai", "bot"):
            out.append(AIMessage(content=content))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# The tool-selection loop
# ─────────────────────────────────────────────────────────────────────────────
async def gather_evidence(
    message: str,
    officer: OfficerContext,
    db: AsyncSession,
    chat_history: Optional[list] = None,
    max_iterations: Optional[int] = None,
) -> AgentEvidence:
    """Let the model choose tools, run them under the officer's authority, and
    return what they said. Raises `AgentUnavailable` if the model layer itself
    is unusable."""
    rounds_allowed = max_iterations or settings.AGENT_MAX_ITERATIONS
    try:
        bound = rag.llm.bind_tools(tool_schemas())
    except Exception as exc:  # pragma: no cover - depends on langchain version
        raise AgentUnavailable(f"tool binding unsupported: {exc}") from exc

    ctx = await ToolContext.create(db, officer)
    evidence = AgentEvidence()

    messages: List[Any] = [
        SystemMessage(content=_TOOL_SYSTEM.format(**_officer_bits(officer))),
        *_history_messages(chat_history),
        HumanMessage(content=message),
    ]
    # A tool call already made, keyed by name + arguments. llama3.1:8b will
    # happily ask the same question three times when the first answer is an
    # empty list, spending the whole round budget to learn nothing; replaying
    # the result with an instruction to stop costs one string instead of one
    # database round trip and one generation.
    seen: Dict[str, str] = {}
    nudged = False

    for round_no in range(1, rounds_allowed + 1):
        evidence.rounds = round_no
        try:
            reply = await bound.ainvoke(messages)
        except Exception as exc:
            if round_no == 1:
                raise AgentUnavailable(f"LLM tool call failed: {exc}") from exc
            # Later rounds: keep the tool results already gathered.
            logger.warning(f"agent: round {round_no} failed, answering from what we have: {exc}")
            break

        tool_calls = list(getattr(reply, "tool_calls", None) or [])
        if not tool_calls:
            evidence.model_reply = (getattr(reply, "content", "") or "").strip()
            # An 8B model sometimes answers a data question straight out of its
            # own head on the first round -- which is precisely the failure this
            # layer exists to remove. It is given exactly one nudge; if it still
            # wants no tool, the question genuinely needed none (small talk, or
            # something the conversation already answered), and the answer
            # prompt forbids stating any record on a turn with no evidence.
            if round_no == 1 and not evidence.observations and not nudged:
                nudged = True
                messages.append(reply)
                messages.append(HumanMessage(content=_NO_TOOL_NUDGE))
                continue
            break

        messages.append(reply)
        for call in tool_calls:
            name = call.get("name")
            args = call.get("args") or {}
            key = f"{name}:{_dump(args, 500)}"
            if key in seen:
                logger.info(f"agent: {name}({args}) was already called; replaying it")
                messages.append(ToolMessage(
                    content=(f"{seen[key]}\n\n(This is the same call you already "
                             f"made. The result has not changed. Do not call it "
                             f"again -- write the answer now.)"),
                    tool_call_id=call.get("id") or name,
                ))
                continue
            logger.info(f"agent: calling {name}({args})")
            result = await execute_tool(ctx, name, args)
            dumped = _dump(result)
            seen[key] = dumped
            evidence.observations.append({"tool": name, "arguments": args, "result": result})
            messages.append(ToolMessage(
                content=dumped,
                tool_call_id=call.get("id") or name,
            ))
    else:
        logger.info(f"agent: hit the {rounds_allowed}-round cap; answering from tool results")

    evidence.calls = ctx.calls

    # Every tool call in this turn was malformed and none succeeded. There is
    # nothing to write an answer from, and what the model would write from it
    # is a description of our own bad arguments -- which is how "the
    # max_results value of 10 exceeds the maximum allowed of 8" reached an
    # officer who had typed one word. Hand the turn to the plain prompt
    # instead. Raised here, before any chunk is streamed, so the caller can
    # still fall back cleanly.
    if not evidence.grounded and any(
            c.get("refused") == "arguments" for c in evidence.calls):
        raise AgentUnavailable(
            "every tool call was rejected on its arguments; no evidence gathered")

    return evidence


def _dump(value: Any, limit: int = 6000) -> str:
    try:
        text = json.dumps(value, default=str, ensure_ascii=False)
    except Exception:  # pragma: no cover
        text = str(value)
    if len(text) > limit:
        text = text[:limit] + " …(truncated)"
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Answer generation
# ─────────────────────────────────────────────────────────────────────────────
def build_answer_prompt(
    message: str,
    evidence: AgentEvidence,
    language: str,
    officer: OfficerContext,
    chat_history: Optional[list] = None,
) -> str:
    bits = _officer_bits(officer)
    bits["language_rule"] = _LANGUAGE_RULES.get(language, _LANGUAGE_RULES["en"])
    parts = [_ANSWER_SYSTEM.format(**bits)]

    history = _history_messages(chat_history, depth=4)
    if history:
        lines = []
        for m in history:
            who = "Officer" if isinstance(m, HumanMessage) else "Assistant"
            lines.append(f"{who}: {m.content}")
        parts.append("CONVERSATION SO FAR:\n" + "\n".join(lines))

    if evidence.observations:
        blocks = []
        for obs in evidence.observations:
            blocks.append(
                f"- {obs['tool']}({_dump(obs['arguments'], 400)}) returned:\n"
                f"  {_dump(obs['result'])}")
        parts.append("TOOL RESULTS:\n" + "\n".join(blocks))
    else:
        parts.append(
            "TOOL RESULTS:\n(no tool was needed for this turn -- answer from the "
            "conversation only, and do not state any record, count or "
            "application number.)")

    parts.append(f"OFFICER'S QUESTION:\n{message}")
    parts.append("ANSWER:")
    return "\n\n".join(parts)


@dataclass
class AgentResult:
    answer: str
    evidence: AgentEvidence

    @property
    def used_tools(self) -> List[str]:
        return self.evidence.used_tools

    @property
    def grounded(self) -> bool:
        return self.evidence.grounded


async def run_agent(
    message: str,
    officer: OfficerContext,
    db: AsyncSession,
    chat_history: Optional[list] = None,
    language: str = "en",
) -> AgentResult:
    """Non-streaming agent turn. Raises `AgentUnavailable` if it could not run."""
    evidence = await asyncio.wait_for(
        gather_evidence(message, officer, db, chat_history),
        timeout=settings.AGENT_TIMEOUT_SECONDS,
    )
    prompt = build_answer_prompt(message, evidence, language, officer, chat_history)
    answer = (await rag.call_llama(prompt)).strip()
    if not answer:
        # An empty synthesis with evidence in hand is recoverable; without
        # evidence there is nothing to recover from.
        if not evidence.observations:
            raise AgentUnavailable("agent produced no answer and gathered no evidence")
        answer = evidence.model_reply or "I could not phrase an answer from the records retrieved."
    return AgentResult(answer=answer, evidence=evidence)


async def run_agent_stream(
    message: str,
    officer: OfficerContext,
    db: AsyncSession,
    chat_history: Optional[list] = None,
    language: str = "en",
) -> AsyncIterator[str]:
    """Streaming agent turn.

    The tool loop runs to completion first -- it cannot be streamed, since its
    output may be a tool call rather than prose -- and only the answer is
    streamed. `AgentUnavailable` is raised before the first chunk is yielded,
    so a caller that has not yet written to the SSE stream can still fall back
    cleanly.
    """
    evidence = await asyncio.wait_for(
        gather_evidence(message, officer, db, chat_history),
        timeout=settings.AGENT_TIMEOUT_SECONDS,
    )
    prompt = build_answer_prompt(message, evidence, language, officer, chat_history)
    logger.info(f"agent: streaming answer grounded in {evidence.used_tools or 'no tools'}")
    async for chunk in rag.call_llama_stream(prompt):
        yield chunk
