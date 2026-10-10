"""The application-number false-positive routing bug: a fresh, non-application
question ("what is TSLR?") must never be forced into `application_status` by
the `_cm_strong()` / `_bare_app_status()` override in `process_chat()` /
`process_chat_stream()`, which otherwise sends it straight into the
application-number gate ("Please specify the application number..."). A
genuinely application-specific question -- an explicit number, or a
back-reference with one in view -- must still be claimed, since that is what
lets a follow-up like "what is the challan date of this application?" work at
all.

Three layers are exercised here, matching the three places a false positive
could hide:
  1. `_cm_strong()` itself (its own DEFN_SHAPE_RE guard)
  2. `_app_evidence_in_scope()` (the call-site belt-and-suspenders guard)
  3. the combined override `(_cm_strong_hit or _bare_status_hit) and _app_evidence`
     -- the exact boolean both chat paths evaluate

No DB, no LLM:  python test_cm_strong_routing.py
"""
import sys

from backend.services import chatbot as cb
from backend.services.rag import parse_intent

APP = "2026/0156/02/000314"
HIST_WITH_APP = [
    {"role": "user", "content": f"show details of {APP}"},
    {"role": "assistant", "content": f"TSLR record for {APP}: survey 5, sub-division 2..."},
]

# (message, chat_history) -- fresh, non-application questions that must NOT
# be forced into application_status (the bug: these used to trigger
# ASK_FOR_APP_NUMBER with no number to give).
FRESH_NOT_CLAIMED = [
    ("what is TSLR?", []),
    # The context-leakage bug: an application shown earlier must not turn a
    # bare, context-free definition question into a question about that file.
    ("what is TSLR?", HIST_WITH_APP),
    ("what is an F-Line?", HIST_WITH_APP),
    # A register-wide LISTING right after a specific application was shown --
    # a different shape from a bare "what is X", same stale-application bug.
    ("list TSLR applications", HIST_WITH_APP),
    ("show my sro applications", HIST_WITH_APP),
    ("show all applications in fline", HIST_WITH_APP),
    ("show 0164 applications", HIST_WITH_APP),
    ("what is sd attachment?", HIST_WITH_APP),
    ("what is parcel modification?", HIST_WITH_APP),
    ("what is uareg modification?", HIST_WITH_APP),
    ("what is a merge id?", HIST_WITH_APP),
    ("what is a challan?", HIST_WITH_APP),
    ("what is a transfer order?", HIST_WITH_APP),
    ("what is door number?", HIST_WITH_APP),
    ("what is old patta?", HIST_WITH_APP),
    ("what is F-Line?", []),
    ("what does TSLR mean?", []),
    ("explain TSLR", []),
    ("what is the full form of TSLR?", []),
    ("what is a natham chitta modification?", []),
    ("what is correction modification flow?", []),
    ("explain new owner record", []),
    ("define old owner", []),
    ("what is the service code for F-Line?", []),
    ("how many applications are pending in ward 001?", []),
    ("what is the SLA for ISD?", []),
    ("what is the difference between ISD and NISD?", []),
    ("explain the workflow for NISD", []),
    ("how many applications were approved this year?", []),
    ("what documents are required for a patta transfer?", []),
    ("give me a summary of ward 001", []),
    # the exact scenarios named in the follow-up bug report
    ("what is the role of sub registrar?", []),
    ("what does SRO mean?", []),
    ("what is service code 0164?", []),
    ("what is an F-Line?", []),
    ("how does field visit scheduling work?", []),
    ("what is the procedure for 0164?", []),
    ("how many applications are pending?", []),
    ("which services require field visit?", []),
]

# Genuine application-specific questions the _cm_strong/_bare_app_status
# override must still claim. (An explicit number like "what is the status of
# <APP>?" is excluded here -- parse_intent already lands it on
# application_status on its own, so the override is never what resolves it;
# that case is exercised separately below, against the EFFECTIVE intent.)
GENUINE_CLAIMED = [
    (f"what is the TSLR status of {APP}?", []),
    ("what is the challan date of this application?", HIST_WITH_APP),
    ("explain the TSLR fields of this application", []),  # back-reference in the message itself
    ("what is the SIS recommendation?", HIST_WITH_APP),    # bare field follow-up, app in recent history
]

# Genuine application-specific questions where the EFFECTIVE intent (parse_intent
# on its own, or the override on top of it) must end up as application_status --
# whichever of the two actually resolves it.
GENUINE_EFFECTIVE = [
    (f"what is the status of {APP}?", []),
]

# Context-follow-up chain: a listing/record is shown, then several bare
# follow-ups in a row must all keep resolving to the SAME application.
FOLLOWUP_CHAIN = [
    "what is its ward?",
    "and the submission date?",
    "what about the channel?",
]

# `_is_bare_concept_definition()` is the real root-cause fix: it is consulted
# independently by `_cm_strong()` AND by `_early_subdivision_answer_inner()`'s
# own app-number resolution (`_tsl` / `_fl` / `_cm_q` / ... all run through
# `_extract_app_number_from_context(..., allow_implicit_continuation=True)`,
# which `_cm_strong()` never touches at all) -- a message shaped like
# "what is an F-Line?" was claimed by `_fline_qa.parse()` but never even
# reached `_cm_strong()`'s claim chain, so a test against `_cm_strong()` alone
# would have missed that path entirely. (message, expected)
BARE_CONCEPT_DEFINITION = [
    ("what is TSLR?", True),
    ("what is an F-Line?", True),
    ("what does TSLR mean?", True),
    ("explain correction modification flow", True),
    ("what is sd attachment?", True),
    ("what is parcel modification?", True),
    ("what is uareg modification?", True),
    ("what is a merge id?", True),
    # The indefinite article ("a"/"an") -- found by sweeping every field name
    # across appinfo_qa / fulltransfer_qa: ALL of these were still claimed via
    # chat_history before `_INDEFINITE_DEFN_RE` was added.
    ("what is a challan?", True),
    ("what is a proposed visit?", True),
    ("what is a transfer order?", True),
    ("what is a reason code?", True),
    ("what is a transaction status?", True),
    ("what is a reject reason?", True),
    ("what is a barcode flag?", True),
    ("what is a direct transfer flag?", True),
    ("what does automutated mean?", True),  # "what does X mean" is unconditional
    # No article, but a field name specific enough to a record module that it
    # was added to _MODULE_CONCEPT_RE directly after the same sweep.
    ("what is physical verification?", True),
    ("what is owner correction reason?", True),
    # An ordinary field (fulltransfer_qa.REGPLACE), not a record type -- removed from
    # _MODULE_CONCEPT_RE because it contradicted this function's own docstring; it now
    # resolves via chat_history like "what is the SIS recommendation?" below.
    ("what is registration place?", False),
    ("what is land category?", True),
    ("what is legal heir?", True),
    ("what is purchased area?", True),
    ("what is old patta?", True),
    ("what is zdt patta?", True),
    ("what is door number?", True),
    ("what is coss block?", True),
    ("what is the SIS recommendation?", False),     # an ordinary field, not a record type
    ("what is the challan date?", False),
    (f"what is the TSLR status of {APP}?", False),  # names its own application
    ("what is its TSLR status?", False),            # back-reference pronoun
    ("what is the TSLR survey number for this application?", False),
    ("what is its SD attachment status?", False),   # back-reference pronoun
    ("what is the merge id of this application?", False),
]


def _override_fires(msg, hist) -> bool:
    """The exact boolean both chat paths evaluate before forcing
    intent = "application_status"."""
    cm = cb._cm_strong(msg, hist)
    bare = cb._bare_app_status(msg, hist)
    evidence = cb._app_evidence_in_scope(msg, hist)
    return (cm or bare) and evidence


def run():
    failures = []

    for msg, hist in FRESH_NOT_CLAIMED:
        if _override_fires(msg, hist):
            failures.append(f"FALSE POSITIVE: override fires for {msg!r} (should not)")

    for msg, hist in GENUINE_CLAIMED:
        if not _override_fires(msg, hist):
            failures.append(f"FALSE NEGATIVE: override does not fire for {msg!r} (should)")

    for msg, hist in GENUINE_EFFECTIVE:
        base_intent = parse_intent(msg, prev_intent=None)
        effective = "application_status" if _override_fires(msg, hist) else base_intent
        if effective != "application_status":
            failures.append(f"EFFECTIVE INTENT WRONG: {msg!r} -> {effective!r} (should be application_status)")

    # A growing follow-up chain: each turn adds to history, and the
    # back-reference/evidence scope must keep seeing the original application.
    hist = list(HIST_WITH_APP)
    for msg in FOLLOWUP_CHAIN:
        if not cb._app_evidence_in_scope(msg, hist):
            failures.append(f"FOLLOWUP CHAIN BROKEN at {msg!r}: no app evidence in scope")
        hist = hist + [{"role": "user", "content": msg},
                        {"role": "assistant", "content": f"... [{APP}]"}]

    # The gate itself: a fresh question must never resolve to application_status
    # via the override when parse_intent alone did not already land there.
    for msg, hist in FRESH_NOT_CLAIMED:
        base_intent = parse_intent(msg, prev_intent=None)
        overridden = "application_status" if _override_fires(msg, hist) else base_intent
        if overridden == "application_status" and base_intent != "application_status":
            failures.append(
                f"OVERRIDE STILL FIRES: {msg!r} parsed={base_intent!r} but override -> application_status")

    for msg, expected in BARE_CONCEPT_DEFINITION:
        got = cb._is_bare_concept_definition(msg)
        if got != expected:
            failures.append(
                f"_is_bare_concept_definition({msg!r}) = {got!r}, expected {expected!r}")

    if failures:
        print(f"{len(failures)} failure(s):")
        for f in failures:
            print(" -", f)
        sys.exit(1)
    print(f"{len(FRESH_NOT_CLAIMED)} fresh questions not claimed, "
          f"{len(GENUINE_CLAIMED)} genuine follow-ups still claimed, "
          f"{len(FOLLOWUP_CHAIN)}-turn follow-up chain intact, "
          f"{len(BARE_CONCEPT_DEFINITION)} concept-definition cases correct: ok")


if __name__ == "__main__":
    run()
