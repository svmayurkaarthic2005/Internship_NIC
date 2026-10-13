"""Regression test for a CONFIRMED, live-UI-reproduced, now-FIXED defect
(QA audit 2026-10-13, upload pass; fixed 2026-10-13, failure id
ATTACH-ROUTE-001): a bare domain-vocabulary question about an attached file
("survey number", "subdivision number") was classified by parse_intent() as
the deterministic SIS database intent survey_detail / survey_owners EVEN
THOUGH THE MESSAGE NAMES NO CONCRETE NUMBER -- which meant the attachment
Q&A layer (targets_attachment() in chatbot.py, which only claims a turn when
the intent is general_query or the message explicitly names a file) never
got a chance to answer from an uploaded document.

Reproduced live through the actual rendered chatbot UI
(http://localhost:3000/chatbot.html), officer marun_chn@sis.tn.gov.in, with
sis_extraction_test_en.txt attached (it contains "Synthetic Survey Number:
777" and "Synthetic Sub-division Number: 777/9"):

  "List every survey number mentioned."
     -> before the fix: "No records found." (intent: survey_detail)
     -> after the fix: routed to general_query, which the attachment layer
        answers correctly ("777").

  "What subdivision number belongs to the first record?"
     -> before the fix: "Which survey number or application should I look
        up the ownership for?" then "Survey Ownership -- No records found."
        (intent: survey_owners)
     -> after the fix: routed to general_query the same way.

  Control, same session: "In the uploaded file, what is the synthetic survey
  number?" -> correctly answered "The synthetic survey number is 777."
  (intent: general_query), proving the attachment pipeline itself was always
  fine -- only the intent classification was the problem.

ROOT CAUSE: rag.py's parse_intent() has a catch-all rule (the deterministic
keyword routing every other SIS question also relies on) that assigns
survey_detail/survey_owners to any message carrying "survey"/"subdivision" +
"number"-shaped words, trusting a downstream handler (_survey_ref() in
chatbot.py) to resolve an actual number from the message or from context.
When nothing resolves, the deterministic DB handler answered "No records
found" without ever considering an attached file.

THE FIX IS DELIBERATELY NOT IN parse_intent() -- rag.py is a pure, stateless
function reused by every caller in the app, and parse_intent()'s classification
here is CORRECT for the common case (a genuine numberless follow-up about an
application already in view, which a different, existing mechanism already
resolves via chat history -- see `_followup_is_self_contained()`'s own
documented carve-out for the identical shape). Narrowing parse_intent()
itself would have been the keyword-only workaround this fix explicitly
needed to avoid. Instead, `_is_unresolved_survey_question()` in chatbot.py
is a new, narrow, SESSION-AWARE override: when intent is survey_detail /
survey_owners, the message carries no digit, and nothing in the officer's
follow-up context names a survey or application either, the turn is
reclassified to general_query -- but only when an attachment is actually
active for the session (checked separately by the caller via
`attachment_qa.any_active_documents()`, which needs a database and so is not
exercised by this file). A session with nothing uploaded, or a message that
genuinely does resolve (a digit, or an application already in view), is
completely untouched.

This file checks the pure, DB-free half of the fix:
`_is_unresolved_survey_question()` itself, and confirms parse_intent() is
UNCHANGED (still returns survey_detail/survey_owners for these phrasings --
that is correct and expected; the fix lives one layer up, not here).

See SIS_Upload_Failure_Cases.jsonl / ATTACH-ROUTE-001 for the full live-UI
reproduction and SIS_Upload_Bug_Fix_Report.md for the before/after evidence.

Run: python SIS_Upload_Regression_Tests/test_attachment_intent_routing_gap.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.services.rag import parse_intent  # noqa: E402
from backend.services.chatbot import _is_unresolved_survey_question  # noqa: E402
from backend.services import followup_context as fctx  # noqa: E402

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


# ---------------------------------------------------------------------
# 0. parse_intent() itself is UNCHANGED -- this is correct, not the bug.
# ---------------------------------------------------------------------
check('parse_intent() still classifies "List every survey number mentioned." '
      "as survey_detail (unchanged -- the fix is one layer up)",
      parse_intent("List every survey number mentioned.") == "survey_detail")
check('parse_intent() still classifies "What subdivision number belongs to '
      'the first record?" as survey_owners (unchanged)',
      parse_intent("What subdivision number belongs to the first record?") == "survey_owners")

# ---------------------------------------------------------------------
# 1. The confirmed, now-fixed failure: a bare, numberless, context-free
# survey_detail/survey_owners question is recognised for redirect.
# ---------------------------------------------------------------------
NUMBERLESS_QUESTIONS = (
    ("survey_detail", "List every survey number mentioned."),
    ("survey_owners", "What subdivision number belongs to the first record?"),
    ("survey_detail", "What survey number is mentioned in this document?"),
)
for intent, msg in NUMBERLESS_QUESTIONS:
    check(f'_is_unresolved_survey_question({intent!r}, "{msg}", None) is True '
          "(nothing to resolve -- the caller may now redirect if an attachment is active)",
          _is_unresolved_survey_question(intent, msg, None) is True)

# ---------------------------------------------------------------------
# 2. Regression safeguards: a real, numbered question is never touched.
# ---------------------------------------------------------------------
check('a numbered survey question is NOT flagged as unresolved',
      _is_unresolved_survey_question("survey_detail", "What is the status of survey number 5?", None) is False)
check('a numbered sub-division owner question is NOT flagged as unresolved',
      _is_unresolved_survey_question("survey_owners", "Who owns survey 120/1?", None) is False)
check('an unrelated intent is never flagged, however the message reads',
      _is_unresolved_survey_question("application_status", "List every survey number mentioned.", None) is False)

# ---------------------------------------------------------------------
# 3. Regression safeguard: an application/survey already in view (a
# genuinely resolvable follow-up) is never touched, matching
# `_followup_is_self_contained()`'s own documented carve-out for this
# exact numberless shape.
# ---------------------------------------------------------------------
_ctx_with_application = fctx.FollowupContext(
    entity=fctx.ENTITY_APPLICATION, application_numbers=["2026/0154/28/001280"])
check('a numberless survey_detail question with an application already in '
      "view is NOT flagged (a real, resolvable follow-up)",
      _is_unresolved_survey_question(
          "survey_detail", "What is the survey number?", _ctx_with_application) is False)

_ctx_with_survey = fctx.FollowupContext(entity=fctx.ENTITY_SURVEY, survey_numbers=["777"])
check('a numberless survey_owners question with a survey already in view '
      "is NOT flagged",
      _is_unresolved_survey_question(
          "survey_owners", "Who owns this?", _ctx_with_survey) is False)

_ctx_with_list = fctx.FollowupContext(
    entity=fctx.ENTITY_APPLICATION_LIST, application_numbers=["2026/0154/28/001280"])
check("a single-application list in view counts as resolvable too",
      _is_unresolved_survey_question(
          "survey_detail", "What is the survey number?", _ctx_with_list) is False)

_ctx_with_multi_list = fctx.FollowupContext(
    entity=fctx.ENTITY_APPLICATION_LIST,
    application_numbers=["2026/0154/28/001280", "2026/0154/28/001281"])
check("a MULTI-application list in view is genuinely ambiguous, so it is "
      "still flagged as unresolved (the standing rule: never infer over "
      "more than one referent)",
      _is_unresolved_survey_question(
          "survey_detail", "What is the survey number?", _ctx_with_multi_list) is True)

print(f"\n{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
