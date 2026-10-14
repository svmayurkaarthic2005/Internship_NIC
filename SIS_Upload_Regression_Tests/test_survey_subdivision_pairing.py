"""Regression tests for the requirement: whenever the SIS chatbot returns a
survey number from an uploaded document, it must also return the
corresponding subdivision number whenever that information is available in
the source -- never a bare, isolated survey number when its subdivision is
sitting right beside it in the document.

SCOPE OF THIS PASS: the attachment (uploaded-document) entity-extraction
path in backend/services/attachment_qa.py -- specifically
`entity_occurrences()` / `_enumerate_entities_answer()` /
`_single_entity_answer()`, the deterministic regex-based survey-number
answering built in the previous two QA passes (see
SIS_Upload_Bug_Fix_Report.md and
SIS_Upload_Extraction_Consistency_Fix_Report.md). This is the one path in
the codebase this session has full context on and can verify end to end; see
SIS_Survey_Subdivision_Pairing_Fix_Report.md for what else the requirement
touches (RAG/OCR/database paths) and what was and was not inspected there.

ROOT CAUSE (the actual gap this closes): `_enumerate_entities_answer()` /
`_single_entity_answer()` rendered a survey number as a bare value --
"The survey numbers mentioned are: 101, 102, 103." -- even when the SAME
evidence chunk plainly also carried each one's subdivision number
("Synthetic Sub-division Number: 101/1" sitting two lines below "Synthetic
Survey Number: 101"). The association was never computed at all; only the
survey-number regex was ever run for this question shape.

FIX: a new `survey_subdivision_pairs()` in attachment_qa.py associates each
survey-number occurrence with every subdivision-number mention in the text
BETWEEN it and the next survey-number occurrence in the same evidence chunk
(a bounded window, capped at 400 characters, so a subdivision mentioned much
later in a long chunk can never be wrongly attached to an earlier survey
number) -- reusing the SAME neighbour-context exclusion the survey-number
pattern itself already relies on, so a boundary-reference number is excluded
on both sides of the pairing. `_render_survey_pairs_answer()` renders the
result as prose (default), a table ("show...in a table"), or an occurrence
count ("how many...mentions"), always keeping the pairing. Wired into
`plan_answer()`'s existing "one entity, or ask which" block for BOTH the
single-value answer path and the multi-value enumeration path -- the
disambiguation ("which one do you mean?") path for a genuinely singular
question is completely unchanged.

Ground truth for both fixtures used here
(SIS_Upload_QA_Fixtures/ground_truth.json) was written independently of any
chatbot answer, before this fix (and, for the multi-subdivision case, before
Record E was even added to the fixture).

Run: python SIS_Upload_Regression_Tests/test_survey_subdivision_pairing.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.services.chatbot.attachment_qa import (  # noqa: E402
    survey_subdivision_pairs, _render_survey_pairs_answer,
    _render_survey_subdivision_table)
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
with open(os.path.join(_FIXTURE_DIR, "sis_extraction_test_en.txt"), encoding="utf-8") as f:
    SINGLE_CONTENT = f.read()
with open(os.path.join(_FIXTURE_DIR, "ground_truth.json"), encoding="utf-8") as f:
    GROUND_TRUTH = json.load(f)

GT = GROUND_TRUTH["sis_multi_survey_test.txt"]
EXPECTED_PAIRS = GT["survey_subdivision_pairs_expected"]

EV_MULTI = Evidence(document_id="doc-multi", filename="sis_multi_survey_test.txt",
                    chunk_index=0, content=MULTI_CONTENT,
                    citation="sis_multi_survey_test.txt", location={}, score=0.6)
EV_SINGLE = Evidence(document_id="doc-single", filename="sis_extraction_test_en.txt",
                     chunk_index=0, content=SINGLE_CONTENT,
                     citation="sis_extraction_test_en.txt", location={}, score=0.44)

# ---------------------------------------------------------------------
# 1. A survey number is returned with its known subdivision (req. #1).
# ---------------------------------------------------------------------
pairs_single = survey_subdivision_pairs([EV_SINGLE])
check("the single-value fixture pairs 777 with its subdivision 777/9",
      pairs_single == [("777", ["777/9"], 1, EV_SINGLE)], f"got {pairs_single}")

answer_single = _render_survey_pairs_answer(pairs_single, "What is the survey number?", "en")
check('a single-value survey-number answer states the subdivision, never '
      "just the bare survey number alone",
      "777" in answer_single and "777/9" in answer_single, answer_single)

# ---------------------------------------------------------------------
# 2. Multiple subdivisions under ONE survey number are ALL preserved, not
#    narrowed to one (req. #2). Ground truth: 104 has TWO subdivisions.
# ---------------------------------------------------------------------
pairs_multi = survey_subdivision_pairs([EV_MULTI])
by_survey = {s: subs for s, subs, _c, _e in pairs_multi}
check("ground truth records survey 104 with exactly two subdivisions",
      EXPECTED_PAIRS["104"] == ["104/1A", "104/1B"])
check("survey_subdivision_pairs() finds BOTH of 104's subdivisions, not "
      "just the first one retrieved",
      by_survey.get("104") == ["104/1A", "104/1B"], f"got {by_survey.get('104')}")

answer_104 = _render_survey_pairs_answer(pairs_multi, "List every survey number mentioned.", "en")
check("the rendered answer names BOTH of 104's subdivisions",
      "104/1A" in answer_104 and "104/1B" in answer_104, answer_104)

table_104 = _render_survey_subdivision_table(pairs_multi, "en")
check("the table rendering gives 104 its OWN TWO ROWS (one per subdivision), "
      "never collapsing them into one row",
      table_104.count("104 | 104/1A") == 1 and table_104.count("104 | 104/1B") == 1,
      table_104)

# ---------------------------------------------------------------------
# 3. Different survey numbers are never assigned each other's subdivisions
#    (req. #3) -- the core entity-association correctness check.
# ---------------------------------------------------------------------
for survey, expected_subs in EXPECTED_PAIRS.items():
    actual_subs = by_survey.get(survey)
    check(f"survey {survey} is paired with EXACTLY its own ground-truth "
          f"subdivisions {expected_subs}, no more and no fewer",
          actual_subs == expected_subs, f"got {actual_subs}")

check("101's subdivision (101/1) never appears attached to 102",
      "101/1" not in by_survey.get("102", []))
check("102's subdivision (102/4) never appears attached to 101",
      "102/4" not in by_survey.get("101", []))
check("104's subdivisions never leak onto 103 (103 genuinely has none)",
      by_survey.get("103") == [])

# ---------------------------------------------------------------------
# 4. Enumeration returns ALL supported pairs, not merely the first one
#    (req. #4) -- already covered by test_attachment_enumeration_consistency
#    .py's enumeration-vs-clarify fix; re-asserted here for the PAIRED
#    rendering specifically.
# ---------------------------------------------------------------------
check("the default enumeration answer names every one of the 4 ground-truth "
      "survey numbers, each with its pairing",
      all(s in answer_104 for s in ("101", "102", "103", "104")), answer_104)
check("103 (genuinely no subdivision) is explicitly marked, never silently "
      "dropped from the list",
      "not specified in source" in answer_104.lower(), answer_104)

# ---------------------------------------------------------------------
# 5. Missing subdivision numbers are flagged without fabrication (req. #5):
#    103 never borrows 101's, 102's or 104's subdivision.
# ---------------------------------------------------------------------
check("103's entry contains NONE of the other records' subdivision values",
      not any(sub in str(by_survey.get("103")) for sub in
              ("101/1", "102/4", "104/1A", "104/1B")))
table_103_row = [r for r in table_104.splitlines() if r.startswith("103 ")]
check("103's table row reads exactly 'Not specified in source', never a "
      "guessed value",
      table_103_row == ["103 | Not specified in source"], table_103_row)

# ---------------------------------------------------------------------
# 6. Table modifications (the explicit "show in a table" request) do not
#    remove either identifier column (req. #8 of the mega-task's list).
# ---------------------------------------------------------------------
check("the table has a header naming BOTH columns",
      table_104.splitlines()[0] == "Survey Number | Subdivision Number")
check("every data row in the table carries both a survey number and a "
      "subdivision value (even if that value is the explicit 'not "
      "specified' marker)",
      all(len(row.split(" | ")) == 2 for row in table_104.splitlines()[1:]),
      table_104)

# ---------------------------------------------------------------------
# 7. Regression safeguard: a genuinely ambiguous SINGULAR question is still
#    left to the disambiguation path -- this fix only changes what gets
#    STATED once a value is being answered, never when "which one?" is the
#    right question. (Covered in depth by
#    test_attachment_enumeration_consistency.py; spot-checked here too.)
# ---------------------------------------------------------------------
from backend.services.chatbot.attachment_qa import wants_enumeration  # noqa: E402
check('"What is the synthetic survey number?" on the multi-value document '
      "is still NOT an enumeration request (unchanged)",
      not wants_enumeration("What is the synthetic survey number?"))

# ---------------------------------------------------------------------
# 8. Occurrence-count rendering states pairs, not just a bare survey count.
# ---------------------------------------------------------------------
count_answer = _render_survey_pairs_answer(
    pairs_multi, "How many survey numbers and subdivisions are present?", "en")
check('"how many survey numbers and subdivisions" states both the unique '
      "survey count (4) and the total survey/subdivision PAIR count "
      "(5: 101, 102, 103-with-none, 104x2)",
      "4" in count_answer and "5" in count_answer, count_answer)

# ---------------------------------------------------------------------
# 9. Tamil rendering carries the pairing too (language must not drop data).
# ---------------------------------------------------------------------
answer_ta = _render_survey_pairs_answer(pairs_multi, "எல்லா survey numbers-ஐ காட்டு", "ta")
check("Tamil rendering still names every subdivision value (identifiers "
      "are never translated or altered)",
      all(v in answer_ta for v in ("101/1", "102/4", "104/1A", "104/1B")), answer_ta)

print(f"\n{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
