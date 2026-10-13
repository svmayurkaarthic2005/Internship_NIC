"""Regression test for a CONFIRMED, live-UI-reproduced, now-FIXED defect
found while verifying ATTACH-ROUTE-001 through the actual chatbot UI
(2026-10-13): fixing the routing half of ATTACH-ROUTE-001 (see
test_attachment_intent_routing_gap.py) got the officer's question to the
attachment answer layer, but the answer layer itself then refused both of
the bug's own original repro questions even though the right evidence was
retrieved and handed to the model:

  "List every survey number mentioned."
     -> evidence correctly retrieved (score 0.44, above the 0.4 floor),
        containing "Synthetic Survey Number: 777" -- the LLM (llama3.1:8b)
        still replied "I could not find survey numbers in the uploaded
        document." It read "list"/"every" as requiring more than one match
        and would not accept a single value as a complete answer.

  "What subdivision number belongs to the first record?"
     -> evidence retrieval itself returned NOTHING (lexical score 0.0)
        despite the document plainly containing "Sub-division Number:
        777/9". `_TOKEN_RE` in attachment_store.py keeps a hyphen as part
        of a token (needed for codes like sub-division numbers), so the
        question's "subdivision" (no hyphen) and the document's
        "Sub-division" (hyphenated) tokenised to two different strings and
        never matched at all.

TWO ROOT CAUSES, TWO FIXES:

1. A prompt-only instruction addition (in `build_prompt()`) telling the
   model a single value IS a complete "list of one" reduced but did not
   eliminate the refusal on llama3.1:8b -- prompt engineering alone was not
   reliable enough. The actual fix reuses EXISTING, already-tested
   infrastructure that was built for a different purpose and sitting one
   branch away from solving this: `_asked_entity()` / `distinct_entities()`
   / `_ENTITY_PATTERNS` already extract "survey number" / "application
   number" values deterministically via regex, and already use that
   extraction to ask "which one?" when there is more than one match
   (`plan_answer`'s "One entity, or ask which" block). Nothing used it when
   there was exactly ONE match -- that case fell through to the same
   unreliable LLM path. A new `len(values) == 1` branch answers that one,
   already-regex-verified value directly (`_single_entity_answer()`),
   bypassing the LLM's unreliable judgement about "list" wording entirely.
   `_ENTITY_PATTERNS` / `_ENTITY_QUESTION_WORDS` gained a new "subdivision
   number" entry (it previously only covered "survey number" and
   "application number"), and `distinct_entities()`'s single-branch rule
   restricting capture-group extraction to `entity == "survey number"` was
   generalised to any pattern that HAS a capture group, so the new
   subdivision pattern's group(1) value ("777/9") is extracted correctly
   instead of the whole raw match.

2. The hyphen-tokenisation mismatch is fixed with a new `_ALIASES` entry in
   attachment_store.py (`"subdivision"` / `"sub-division"` both mapping to
   the full `("subdivision", "sub-division", "sub division")` triple) --
   NOT a cross-script Tamil/English pair like the rest of that table, but
   reusing its exact mechanism for a plain English spelling-variant case,
   since `_TOKEN_RE` itself was deliberately not touched (it also has to
   keep hyphens in genuinely meaningful places, like survey sub-numbers).

Reproduced live through the actual rendered chatbot UI
(http://localhost:3000/chatbot.html), officer marun_chn@sis.tn.gov.in, with
sis_extraction_test_en.txt attached (one document, one session):

  "List every survey number mentioned."
     -> before this fix: "I could not find survey numbers in the uploaded
        document." (reproduced twice, consistently -- not a model-sampling
        fluke)
     -> after this fix: "The survey number mentioned is 777."

  "What subdivision number belongs to the first record?"
     -> before this fix: "I could not find this in the uploaded document."
     -> after this fix: "The subdivision number mentioned is 777/9."

This file checks the pure, DB-free half of both fixes (the entity-pattern
extraction and the lexical-alias scoring), plus regression safeguards for
the behaviour that must NOT change. See
SIS_Upload_Failure_Cases.jsonl / ATTACH-ROUTE-001 and
SIS_Upload_Bug_Fix_Report.md for the full live-UI before/after evidence.

Run: python SIS_Upload_Regression_Tests/test_attachment_single_entity_completion.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.services.attachment_qa import (  # noqa: E402
    _asked_entity, distinct_entities, _single_entity_answer)
from backend.services.attachment_store import (  # noqa: E402
    Evidence, _content_terms, _ALIASES)

bad = []
total = 0


def check(name, condition, detail=""):
    global total
    total += 1
    if condition:
        print(f"ok   {name}")
    else:
        bad.append((name, detail))
        print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))


EV = Evidence(
    document_id="doc-1", filename="sis_extraction_test_en.txt", chunk_index=0,
    content=("Synthetic Survey Number: 777\n"
             "Synthetic Sub-division Number: 777/9\n"
             "Synthetic Ward: 999"),
    citation="sis_extraction_test_en.txt", location={}, score=0.44)

# ---------------------------------------------------------------------
# 1. The confirmed, now-fixed failure: "list"-worded single-match survey
#    questions resolve via the entity-pattern path, never the LLM.
# ---------------------------------------------------------------------
LIST_WORDED_SURVEY_QUESTIONS = (
    "List every survey number mentioned.",   # the exact original repro
    "List the survey number mentioned.",
    "What is the survey number?",            # must still work (pre-existing)
)
for q in LIST_WORDED_SURVEY_QUESTIONS:
    entity = _asked_entity(q)
    check(f'"{q}" resolves to the "survey number" entity', entity == "survey number")
    values = distinct_entities(entity, [EV]) if entity else []
    check(f'  ...and extracts exactly one value (777) from the evidence',
          values == ["777"], f"got {values}")

check('_single_entity_answer("survey number", "777", "en") states the value '
      "plainly, matching what a bare 'list' question needs",
      _single_entity_answer("survey number", "777", "en") == "The survey number mentioned is 777.")

# ---------------------------------------------------------------------
# 2. The confirmed, now-fixed failure: "subdivision number" (previously
#    unrecognised at all) resolves through the same entity-pattern path.
# ---------------------------------------------------------------------
entity = _asked_entity("What subdivision number belongs to the first record?")
check('"What subdivision number belongs to the first record?" resolves to '
      'the "subdivision number" entity (previously: no entity at all)',
      entity == "subdivision number")
values = distinct_entities(entity, [EV]) if entity else []
check("  ...and extracts the full value including its /-suffix (777/9, "
      "not just the whole raw match text)",
      values == ["777/9"], f"got {values}")

for q in ("What is the sub-division number?", "What is the sub division number?"):
    e = _asked_entity(q)
    check(f'"{q}" (alternate spacing/hyphenation) also resolves to '
          '"subdivision number"', e == "subdivision number")

# ---------------------------------------------------------------------
# 3. The hyphen-tokenisation root cause, checked directly: "subdivision"
#    (question wording) must be able to find "Sub-division" (document
#    wording) as a lexical match -- this was lexical score 0.0 before.
# ---------------------------------------------------------------------
check('_ALIASES carries the subdivision/sub-division spelling-variant pair '
      "(both directions)",
      "subdivision" in _ALIASES and "sub-division" in _ALIASES)
check("the alias set is identical from either spelling (so whichever one "
      "the officer types, the OTHER one is still searched for)",
      set(_ALIASES["subdivision"]) == set(_ALIASES["sub-division"])
      and "sub-division" in _ALIASES["subdivision"]
      and "subdivision" in _ALIASES["sub-division"])

# ---------------------------------------------------------------------
# 4. Regression safeguard: a GENUINELY ambiguous (2+ values) survey-number
#    question must still ask which one, never silently pick the first or
#    answer them all as if "list" meant list them all unprompted.
# ---------------------------------------------------------------------
EV_TWO_SURVEYS = Evidence(
    document_id="doc-2", filename="two_surveys.txt", chunk_index=0,
    content="Survey Number: 101\nSurvey Number: 202", citation="two_surveys.txt",
    location={}, score=0.5)
entity = _asked_entity("What is the survey number?")
values = distinct_entities(entity, [EV_TWO_SURVEYS])
check("two distinct survey numbers in one document are BOTH found (not "
      "silently narrowed to one)", set(values) == {"101", "202"}, f"got {values}")

# ---------------------------------------------------------------------
# 5. Regression safeguard: an application-number question (the other
#    existing entity type, which has NO capture group) is completely
#    unaffected by generalising the capture-group rule in distinct_entities.
# ---------------------------------------------------------------------
EV_APP = Evidence(
    document_id="doc-3", filename="app.txt", chunk_index=0,
    content="Application Reference: 2026/0154/28/001280", citation="app.txt",
    location={}, score=0.5)
entity = _asked_entity("What is the application number?")
check('"application number" still resolves as its own entity', entity == "application number")
values = distinct_entities(entity, [EV_APP])
check("the application-number pattern (no capture group) still extracts "
      "the WHOLE matched number, unaffected by the subdivision-number "
      "capture-group generalisation",
      values == ["2026/0154/28/001280"], f"got {values}")

# ---------------------------------------------------------------------
# 6. Regression safeguard: an unrelated field question is never pulled
#    into the entity path at all.
# ---------------------------------------------------------------------
check('"what is the status?" names no registered entity (ward/status/etc. '
      "are not survey/application/subdivision numbers)",
      _asked_entity("what is the status?") is None)

print(f"\n{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
