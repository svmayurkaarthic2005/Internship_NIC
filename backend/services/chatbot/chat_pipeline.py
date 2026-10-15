"""
Shared stages of the chat pipeline, used by BOTH process_chat (non-streaming)
and process_chat_stream (streaming) in chatbot.py.

A stage here decides WHAT to answer; it never delivers the answer. Delivery
differs between the two pipelines and stays in chatbot.py: the non-streaming
path saves the turn and then returns a response dict, the streaming path
emits an SSE frame first and saves afterwards. Keeping the decision pure
(no database, no session, no LLM) is what lets one implementation serve both
without changing either pipeline's ordering, frames or persisted rows.
"""
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from . import neg_scope as _neg_scope
from .chat_corrections import (
    _is_bare_language_instruction,
    _language_preference_reply,
)
from .chat_service_codes import (
    _named_unsupported_service_code,
    _unsupported_service_code_answer,
)
from .chat_shared import (
    _contentless_message,
    _sd_parse,
    is_prohibit_listing,
    _prohibit_listing_reply,
)
from .chat_smalltalk import (
    _contentless_reply,
    _is_capability_question,
    _is_mutation_request,
    _is_out_of_scope,
    _mutation_refusal,
    _scope_reply,
    _special_scope_kind,
    _special_scope_reply,
    _unknown_message_kind,
    _unknown_message_reply,
)


class _Unset:
    """Marks a stage input that was not bound in the caller (see _bound)."""
    __slots__ = ()

    def __repr__(self):
        return "_UNSET"


_UNSET = _Unset()


def _bound(caller_locals: dict, names: Tuple[str, ...]) -> dict:
    """The subset of `names` the caller has actually bound.

    A pipeline local that is only assigned on some paths is passed this way:
    the stage receives it only if it exists, and deletes the parameter
    otherwise, so reading it inside the stage raises the same
    UnboundLocalError it would have raised in the original function.
    """
    return {n: caller_locals[n] for n in names if n in caller_locals}


@dataclass
class StageOutcome:
    """What a pipeline stage hands back to its orchestrator.

    `bound` holds the pipeline locals the stage assigned (only those it
    actually assigned -- the caller rebinds exactly these). `early` means the
    stage produced the turn's final response and the caller must return
    `response` unchanged.
    """
    bound: dict = field(default_factory=dict)
    response: Any = None
    early: bool = False


@dataclass
class EarlyReply:
    """A deterministic answer decided before intent routing.

    `log` is the message the caller writes (the streaming caller appends its
    own " (stream)" marker, exactly as the two pipelines always have)."""
    text: str
    intent: str
    action: Optional[str] = None
    save: bool = True            # a "clear" turn is never written to the transcript
    clear_session: bool = False  # caller marks the session cleared before delivering
    log: Optional[str] = None
    log_args: Tuple = ()
    stream_log_suffix: bool = True  # streaming path appends " (stream)" (two guards never did)


def early_guard_reply(message: str, language: str, chat_history: Optional[List[dict]],
                      raw_message: Optional[str], has_followup_ctx: bool,
                      ) -> Tuple[Optional[EarlyReply], Optional[str]]:
    """The guard chain both pipelines run before intent routing, in their
    original order: contentless message -> mutation request -> bare language
    instruction -> same-message filter contradiction -> unsupported service
    code -> unanswerable message -> capability / out-of-scope.

    Returns (reply, special_scope_kind). `special_scope_kind` is returned even
    when there is no reply, because the caller's praise/complaint branch right
    after this chain reads it. It is None whenever the chain stopped before
    computing it, which is exactly when the caller has already returned.
    """
    blank_kind = _contentless_message(message)
    if blank_kind:
        return EarlyReply(
            text=_contentless_reply(blank_kind, language, message, chat_history=chat_history),
            intent=("clear_chat" if blank_kind == "clear"
                    else "clarification" if blank_kind in ("qmark_clarify", "punct_clarify")
                    else "small_talk"),
            action="clear_chat" if blank_kind == "clear" else None,
            save=blank_kind != "clear",
            clear_session=blank_kind == "clear",
        ), None

    if _is_mutation_request(message):
        return EarlyReply(
            text=_mutation_refusal(language), intent="read_only_refusal",
            log="Refused a mutation request; the assistant is read-only",
            stream_log_suffix=False,
        ), None

    if _is_bare_language_instruction(message):
        return EarlyReply(
            text=_language_preference_reply(language), intent="language_preference",
            log="Bare language-preference instruction; answered deterministically",
        ), None

    contradiction = _neg_scope.detect_contradiction(raw_message)
    if contradiction:
        return EarlyReply(
            text=contradiction, intent="clarification",
            log="Same-message filter contradiction: %r", log_args=(raw_message,),
            stream_log_suffix=False,
        ), None

    unsupported_code = _named_unsupported_service_code(message)
    if unsupported_code:
        return EarlyReply(
            text=_unsupported_service_code_answer(unsupported_code, language in ("ta", "tanglish")),
            intent="service_code_lookup",
            log="Named an unsupported service code '%s'; answered deterministically",
            log_args=(unsupported_code,),
        ), None

    if is_prohibit_listing(message):
        return EarlyReply(
            text=_prohibit_listing_reply(language),
            intent="prohibit_listing",
            log="Explicit negative instruction not to show applications; acknowledged without query",
            stream_log_suffix=False,
        ), None

    unk_kind = _unknown_message_kind(message)
    if unk_kind == "bare_ref" and has_followup_ctx:
        unk_kind = None  # "the last one" / "that one" with a list in view is a follow-up
    if unk_kind:
        return EarlyReply(
            text=_unknown_message_reply(unk_kind, language), intent="unknown_message",
            log="Answered an unanswerable message (%s) deterministically", log_args=(unk_kind,),
        ), None

    # "good job" / "not satisfied" (praise / complaint) are carved out of this
    # deterministic gate and left to the caller's direct LLM reply.
    ssk = _special_scope_kind(message)
    if not _sd_parse(message, chat_history) and (
            _is_capability_question(message) or _is_out_of_scope(message)
            or bool(ssk and ssk not in ("praise", "complaint"))):
        capability = _is_capability_question(message)
        return EarlyReply(
            text=(_special_scope_reply(message, language) if ssk and not capability
                  else _scope_reply(language, capability=capability)),
            intent=("capabilities" if capability else
                    {"identity": "assistant_identity", "time": "time_now"}.get(ssk, "out_of_scope")),
            log="Answered a %s question with the scope summary",
            log_args=("capability" if capability else "out-of-scope",),
        ), ssk

    return None, ssk
