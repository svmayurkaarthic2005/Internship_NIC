"""Regression: procedural role/step questions about the ISD/NISD workflow
that never say the word "workflow"/"steps"/"process" must still be answered
from the documented chain -- not misrouted to an application-specific
handler (which then asks for an application number, or says "not found",
for a question that names no application at all).

Root cause (backend/services/chatbot.py, `_workflow_steps_topic`):
`_WORKFLOW_STEPS_WORD_RE` gated the ENTIRE function on the literal word
"workflow"/"steps"/"process"/"procedure" appearing in the message. A
realistic officer phrasing of the exact same question --
"who prepares the subdivision sketch?", "what is the DIS's responsibility
after SD prepares the sketch?", "who carries out the final digital
sign-off?" -- carries none of those words, so the function returned None
for every one of them and the message fell through to `general_query`.
Reproduced live against the real chatbot (not assumed): these questions
were answered "Please specify the application number you are asking
about...", "Application not found.", or a full, unrelated 29-row
`isd_applications` listing table -- each a confident non-answer to a
question that named no application at all.

Fix: a new branch recognises a "who"/"which role"/"responsib*" question
that names a STEP NOUN unique to one chain (field verification, the
subdivision sketch, temporary subdivision numbers -- ISD only) or a STEP
ROLE unique to one chain (SD/DIS -- ISD only; ZDT -- NISD only), with its
own narrower listing-exclusion (the general `_WORKFLOW_LISTING_RE` blocks
the bare word "application", which this kind of question uses naturally
without asking for a listing at all).

No DB, no LLM -- this is pure classifier logic.
Run: python test_workflow_role_questions.py
"""
import sys

from backend.services.chatbot import _workflow_steps_topic

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


# ---- the exact questions that were confirmed broken, now resolve to the right chain ----
ISD_QUESTIONS = (
    "Who is responsible for conducting the field verification of an ISD application?",
    "After SIS completes its verification, which role prepares the subdivision sketch?",
    "What is the DIS's responsibility after the SD prepares the sketch?",
    "Who carries out the final digital sign-off for an ISD application?",
    "Who assigns temporary subdivision numbers to the proposed parcels?",
    "which role prepares the sketch",
    "who reviews the sketch and assigns temporary numbers",
)
for q in ISD_QUESTIONS:
    check(f'"{q}" resolves to the ISD chain', _workflow_steps_topic(q) == "ISD",
          _workflow_steps_topic(q))

NISD_QUESTIONS = (
    "In the NISD workflow, which role receives the verified report directly from SIS?",
    "who carries out the final digital sign-off for an NISD application?",
)
for q in NISD_QUESTIONS:
    check(f'"{q}" resolves to the NISD chain', _workflow_steps_topic(q) == "NISD",
          _workflow_steps_topic(q))

# ---- listing questions that merely mention a role/step word are UNTOUCHED ----
LISTING_QUESTIONS = (
    "show my pending DIS applications",
    "how many DIS applications are pending",
    "list my SD applications",
    "show applications for service code 0158",
    "my pending applications",
)
for q in LISTING_QUESTIONS:
    check(f'"{q}" is still NOT claimed as a workflow-role question (stays a listing)',
          _workflow_steps_topic(q) is None, _workflow_steps_topic(q))

# ---- a genuinely ambiguous role question (no chain-specific signal) is left alone ----
AMBIGUOUS_QUESTIONS = (
    "Which service codes correspond to ISD and NISD?",
    "Who approved it?",
)
for q in AMBIGUOUS_QUESTIONS:
    check(f'"{q}" is left to the ordinary pipeline (no unique chain signal to act on here)',
          _workflow_steps_topic(q) is None, _workflow_steps_topic(q))

# "ZDT handles ISD and DIS handles NISD, correct?" is a FALSE premise (DIS
# exists only in the ISD chain, ZDT only in NISD), not a genuinely ambiguous
# question -- see test_isdnisd_premise_questions.py for the full story. It
# deliberately routes to the side-by-side "ALL" comparison instead of being
# left alone, so the backwards claim is visibly wrong from the answer's own
# structure rather than silently agreed with by the LLM fallback.
check('"ZDT handles ISD and DIS handles NISD, correct?" routes to the '
      "self-correcting side-by-side comparison, not silence",
      _workflow_steps_topic("ZDT handles ISD and DIS handles NISD, correct?") == "ALL")

# ---- an application-number-bearing question is still a field lookup, never a role question ----
check('a message naming an application number is never claimed as a role question',
      _workflow_steps_topic("who prepares the sketch for 2026/0154/02/012547?") is None)

# ---- Tanglish "<role> role enna" word order, found sweeping jargon/role questions ----
# `_ROLE_RE` only matched English order ("what is the role of X" / "role of
# X"); `_GLOSSARY_TA_RE` only matched the TERM immediately followed by the
# question particle, with no "role" word in between. "zdt role enna" /
# "tahsildar role enna?" matched neither, so they fell through this function
# entirely to `rag.parse_intent`'s generic "short message naming a field
# keyword" fallback -- "role" is ALSO a real database field (a workflow
# actor's role id on one application), so a bare 3-word message was read as
# a question about that field on whatever application happened to be in
# view and answered `application_status` nonsense instead of the glossary
# definition. Fixed with `_ROLE_TA_RE`.
ROLE_TANGLISH_QUESTIONS = (
    ("zdt role enna", "zdt"),
    ("tahsildar role enna?", "tahsildar"),
    ("sis role enna", "sis"),
    ("dis role enna?", "dis"),
    ("sd role enna", "sd"),
)
for q, term in ROLE_TANGLISH_QUESTIONS:
    check(f'"{q}" resolves to the "{term}" glossary definition',
          _workflow_steps_topic(q) == f"GLOSS:{term}", _workflow_steps_topic(q))

print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
