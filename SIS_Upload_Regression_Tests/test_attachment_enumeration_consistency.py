"""Regression test for a CONFIRMED, live-UI-reproduced, now-FIXED defect
(QA pass 2026-10-13, "enumeration vs targeted lookup" consistency bug):

  "List every survey number mentioned." (a document holding exactly one
  survey number)                        -> "I could not find survey numbers
                                            in the uploaded document."
  "In the uploaded file, what is the
  synthetic survey number?" (same doc)  -> "The synthetic survey number is
                                            777."

The first defect (an LLM refusal on a single-value "list" question, plus a
hyphen-tokenisation gap blocking "subdivision number" retrieval) was found
and fixed in the previous pass -- see
test_attachment_single_entity_completion.py. Re-driving the SAME class of
question against a document that genuinely holds MORE THAN ONE survey
number (never exercised before -- no existing fixture had one) surfaced a
SECOND, deeper form of the same inconsistency, confirmed live:

  "List every survey number mentioned." (a document holding THREE survey
  numbers, 101/102/103, with 101 genuinely repeated)
     -> before this fix: "sis_multi_survey_test.txt contains 3 survey
        numbers: 101, 102, 103. Which one do you mean?" -- an explicit
        enumeration request was answered with a disambiguation QUESTION,
        never a list.
     -> after this fix: "The survey numbers mentioned are: 101, 102, 103.
        (101 (2x) mentioned more than once.)"

ROOT CAUSE: `_answer_from_selected()`'s "one entity, or ask which" block in
attachment_qa.py treated EVERY question matching `_asked_entity()`
identically, regardless of whether the question's own wording asked for ONE
value ("what is the survey number?" -- genuinely ambiguous on a multi-value
document, so asking which one is correct) or asked for ALL of them ("list
every survey number mentioned" -- enumeration, where asking "which one?" is
simply the wrong answer to the question that was asked).

FIX: a new `wants_enumeration()` checked BEFORE the existing len()>1 clarify
branch. When the officer's wording asks for all/every/find-all/how-many-
mentions and there is at least one matching value, every genuinely distinct
value is listed (in document order, deduplicated by definition of a
"genuine duplicate" the same way the existing single-value extraction
already does -- a survey number repeated because the SAME parcel is
mentioned twice, never a neighbouring parcel's own number), with a note
about which ones repeat; "including repeated occurrences" lists every
mention, duplicates included; "how many...mentions" states the total
occurrence count. The disambiguation ("which one do you mean?") path is
completely unchanged for a genuinely singular question against an
ambiguous document.

Reproduced live through the actual rendered chatbot UI / direct
`attachment_qa.plan_answer()` calls (both give the same answer -- the
backend logic is identical either way), officer marun_chn@sis.tn.gov.in,
against a new multi-value fixture built for this regression,
`sis_multi_survey_test.txt` (three survey numbers: 101, 102, 103; 101
genuinely repeated in Records A and C; a FOURTH mention, "Survey Number 999"
on Record D's SOUTH BOUNDARY, is a neighbouring parcel's number and must
never appear as one of this document's own). Ground truth for this fixture
is recorded in SIS_Upload_QA_Fixtures/ground_truth.json BEFORE this file
asked the chatbot anything about it.

See SIS_Upload_Extraction_Consistency_Fix_Report.md for the full
investigation, the complete phrasing matrix actually run, and what was
explicitly NOT run.

Run: python SIS_Upload_Regression_Tests/test_attachment_enumeration_consistency.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.services.attachment_qa import (  # noqa: E402
    wants_enumeration, entity_occurrences, distinct_entities,
    _enumerate_entities_answer, _asked_entity)
from backend.services.attachment_store import Evidence  # noqa: E402

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


HERE = os.path.dirname(os.path.abspath(__file__))
_FIXTURE_DIR = os.path.join(HERE, "..", "SIS_Upload_QA_Fixtures")
with open(os.path.join(_FIXTURE_DIR, "sis_multi_survey_test.txt"), encoding="utf-8") as f:
    MULTI_CONTENT = f.read()
with open(os.path.join(_FIXTURE_DIR, "ground_truth.json"), encoding="utf-8") as f:
    GROUND_TRUTH = json.load(f)

# ---------------------------------------------------------------------
# 0. The ground truth itself is read, not re-derived from the chatbot --
#    confirms the fixture matches what was independently recorded.
# ---------------------------------------------------------------------
GT = GROUND_TRUTH["sis_multi_survey_test.txt"]
check("ground truth for the multi-survey fixture names exactly 4 unique "
      "survey numbers, recorded before any chatbot answer was seen",
      GT["unique_survey_numbers"] == ["101", "102", "103", "104"])
check("ground truth records 101 as genuinely repeated (5 total mentions "
      "across 4 unique numbers)",
      GT["total_genuine_survey_number_mentions"] == 5
      and GT["survey_number_occurrences"]["101"] == 2)

EV = Evidence(document_id="doc-multi", filename="sis_multi_survey_test.txt",
              chunk_index=0, content=MULTI_CONTENT,
              citation="sis_multi_survey_test.txt", location={}, score=0.6)

# ---------------------------------------------------------------------
# 1. wants_enumeration() classification -- the English phrasing matrix
#    from the mega-task, English subset.
# ---------------------------------------------------------------------
ENUMERATION_PHRASINGS = (
    "List every survey number mentioned.",       # the exact original repro
    "List all survey numbers.",
    "Find all survey numbers in the uploaded file.",
    "List all unique survey numbers.",
    "Extract every survey number, including repeated occurrences.",
    "Are there any survey numbers in this document?",
    "Are there any more?",
    "How many survey-number mentions are there?",
)
for q in ENUMERATION_PHRASINGS:
    check(f'"{q}" is classified as an enumeration request', wants_enumeration(q))

SINGULAR_PHRASINGS = (
    "What is the synthetic survey number?",
    "What is the survey number in the second row?",
    "Give the page number for each survey number.",
)
for q in SINGULAR_PHRASINGS:
    check(f'"{q}" is NOT classified as an enumeration request (these ask '
          "about ONE thing, or a per-value projection, not 'give me "
          "everything')", not wants_enumeration(q))

# ---------------------------------------------------------------------
# 2. entity_occurrences() on the multi-value fixture: unique values, in
#    document order, with the TRUE occurrence count, and the neighbour's
#    boundary-reference survey number excluded.
# ---------------------------------------------------------------------
occ = entity_occurrences("survey number", [EV])
check("entity_occurrences finds exactly the 4 ground-truth unique survey "
      "numbers, in document order (101, 102, 103, 104)",
      [v for v, _c, _e in occ] == ["101", "102", "103", "104"], f"got {occ}")
check("101's occurrence count is 2 (Records A and C -- a genuine repeat of "
      "the same parcel)",
      dict((v, c) for v, c, _e in occ)["101"] == 2)
check("the neighbour's boundary-reference number (999) never appears as "
      "one of this document's own survey numbers",
      "999" not in [v for v, _c, _e in occ])
check("the total occurrence count across all values matches ground truth (5)",
      sum(c for _v, c, _e in occ) == GT["total_genuine_survey_number_mentions"])

# ---------------------------------------------------------------------
# 3. THE CONFIRMED, NOW-FIXED DEFECT: an enumeration request on a
#    multi-value document lists them -- it does NOT ask "which one?".
# ---------------------------------------------------------------------
answer = _enumerate_entities_answer("survey number", occ, "List every survey number mentioned.", "en")
check('"List every survey number mentioned." answers with all three '
      "values, never a disambiguation question",
      "101" in answer and "102" in answer and "103" in answer
      and "which one" not in answer.lower(), answer)
check("the default (no 'unique'/'repeated' cue) answer still NOTES that "
      "101 repeats, rather than silently hiding the repetition fact",
      "101" in answer and ("2" in answer or "twice" in answer.lower()), answer)

# "List all UNIQUE survey numbers." -- the repeat note must be suppressed
# when the officer explicitly asked for unique values only.
answer_unique = _enumerate_entities_answer(
    "survey number", occ, "List all unique survey numbers.", "en")
check('"List all unique survey numbers." states the 3 values without the '
      "repeat-count aside (the officer asked for unique, not a repeat "
      "count)", "101" in answer_unique and "×" not in answer_unique, answer_unique)

# "...including repeated occurrences" -- every mention, duplicates included.
answer_repeats = _enumerate_entities_answer(
    "survey number", occ, "Extract every survey number, including repeated occurrences.", "en")
check('"...including repeated occurrences" lists 101 TWICE (every mention, '
      "not just the unique set)",
      answer_repeats.count("101") >= 2, answer_repeats)

# "How many survey-number mentions are there?" -- the total occurrence count
# (5, ground truth) stated explicitly, distinct from the unique count (4).
answer_count = _enumerate_entities_answer(
    "survey number", occ, "How many survey-number mentions are there?", "en")
check('"How many...mentions..." states the TOTAL occurrence count (5), '
      "matching ground truth, as its own leading number -- not just "
      "coincidentally containing the digit from the unique-value count",
      answer_count.startswith("There are 5 "), answer_count)

# ---------------------------------------------------------------------
# 4. Regression safeguard: a genuinely SINGULAR question against the SAME
#    multi-value document still asks which one -- disambiguation is not
#    broken by teaching the system to recognise enumeration requests.
# ---------------------------------------------------------------------
check('"What is the synthetic survey number?" (no "list"/"all"/"every") '
      "is NOT an enumeration request even on the multi-value document",
      not wants_enumeration("What is the synthetic survey number?"))

# ---------------------------------------------------------------------
# 5. Regression safeguard: the ORIGINAL single-value fixture (777) behaves
#    identically whichever way the question is framed -- the bug's own
#    original repro, re-asserted here as a permanent regression case.
# ---------------------------------------------------------------------
with open(os.path.join(_FIXTURE_DIR, "sis_extraction_test_en.txt"), encoding="utf-8") as f:
    SINGLE_CONTENT = f.read()
EV_SINGLE = Evidence(document_id="doc-single", filename="sis_extraction_test_en.txt",
                     chunk_index=0, content=SINGLE_CONTENT,
                     citation="sis_extraction_test_en.txt", location={}, score=0.44)
occ_single = entity_occurrences("survey number", [EV_SINGLE])
check("the single-value fixture still yields exactly one occurrence of 777",
      occ_single == [("777", 1, EV_SINGLE)], f"got {occ_single}")
answer_single = _enumerate_entities_answer(
    "survey number", occ_single, "List every survey number mentioned.", "en")
check('"List every survey number mentioned." on the single-value fixture '
      "states 777, not a disambiguation question and not a refusal",
      "777" in answer_single and "which one" not in answer_single.lower(), answer_single)

# ---------------------------------------------------------------------
# 6. An unrelated numeric value must not be misclassified as a survey
#    number -- the neighbour-boundary exclusion is the existing mechanism;
#    confirm it is unaffected by entity_occurrences' refactor.
# ---------------------------------------------------------------------
EV_NEIGHBOUR_ONLY = Evidence(
    document_id="doc-n", filename="n.txt", chunk_index=0,
    content="Boundary: North - Town Survey Number 42, East - Road.",
    citation="n.txt", location={}, score=0.5)
occ_n = entity_occurrences("survey number", [EV_NEIGHBOUR_ONLY])
check("a survey number that appears ONLY as a boundary/neighbour reference "
      "is never reported as this document's own survey number",
      occ_n == [], f"got {occ_n}")

print(f"\n{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
