"""Regression test for a CONFIRMED, live-UI-reproduced, now-FIXED defect
(QA audit 2026-10-13, upload pass; fixed 2026-10-13, failure id
MULTIDOC-CONFLICT-001, severity Medium): with two CSVs attached sharing a
deliberately conflicting value, asking the chatbot to compare them directly
returned "I could not find this in the uploaded document." instead of
identifying the conflict -- no hallucination occurred (good, the system
failed safe), but a genuinely answerable question was refused.

Reproduced live through the actual rendered chatbot UI
(http://localhost:3000/chatbot.html), officer marun_chn@sis.tn.gov.in, with
sis_survey_records_en.csv (survey 805 = Pending) and
sis_survey_records_en_v2.csv (survey 805 = Rejected) both attached:

  "Across sis_survey_records_en.csv and sis_survey_records_en_v2.csv, is the
  status of survey number 805 consistent between the two files?"
     -> before the fix: "I could not find this in the uploaded document."
     -> after the fix: states the conflict, each value attributed to its
        own file (see the live-column-name caveat below).

ROOT CAUSE: `attachment_qa._answer_from_selected()`'s CSV branch is gated on
`if len(csv_docs) == 1:` -- confirmed directly by reading the source. With
two CSVs selected (and `mentions_filename()` correctly named both of them --
checked directly too, see test 0 below), that guard is simply never true,
so the deterministic, "computed never narrated" csv_ops engine never runs at
all, and the turn falls through to the same ungrounded retrieval+LLM path
that answers a single file's free-text questions -- which had no grounded
answer to find across two separate row sets it never combined, so it
refused.

CAVEAT ON THE ORIGINAL FIXTURE'S EXACT WORDING: the original QA pass's CSV
fixture used deliberately verbose, self-documenting column names
("synthetic_survey_no", "synthetic_status") for clarity as a fixture; csv_ops'
column matching requires the question to contain the HEADER's own text, so an
officer's ordinary phrasing ("survey number 805") never matched that specific
header name even for a SINGLE file, before or after this fix -- a separate,
pre-existing csv_ops limitation (fuzzy column-name matching), not part of this
defect, and out of this fix's authorized scope. This file's cases therefore
use realistic column names ("survey_no", "status"), which is what an officer's
own CSV would actually look like, to test the CONFIRMED fix (the len==1 guard)
in isolation from that separate, unfixed limitation.

THE FIX: `attachment_qa.py` gained an `elif len(csv_docs) >= 2:` branch next
to the existing single-document one, running the SAME `csv_ops.parse_csv_
question()` / `execute()` independently against each file's own rows (never
merged), and a new `render_multi_csv_result()` that states agreement or the
conflict, each value kept beside the filename it came from -- per the task's
explicit safeguard: "preserve the source identity of each extracted value...
report the conflict with appropriate provenance rather than silently
selecting or merging values."

This file tests `render_multi_csv_result()` directly (pure, no DB -- the
rendering logic the fix actually added) plus the real `csv_ops.parse_csv_
question()` / `execute()` calls that produce its inputs, mirroring exactly
what `_answer_from_selected()`'s new branch does. See
SIS_Upload_Failure_Cases.jsonl / MULTIDOC-CONFLICT-001 and
SIS_Upload_Bug_Fix_Report.md for the full live-UI reproduction and the
before/after evidence (including the UI re-test with the original, verbose
fixture columns, which still cannot resolve the exact original wording for
the separate, pre-existing reason explained above).

Run: python SIS_Upload_Regression_Tests/test_multidoc_conflict_gap.py
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.services import csv_ops  # noqa: E402
from backend.services.chatbot.attachment_qa import (  # noqa: E402
    mentions_filename, render_multi_csv_result)

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


def _ask(question, headers, rows):
    op = csv_ops.parse_csv_question(question, headers, rows)
    return csv_ops.execute(op, headers, rows) if op is not None else None


HEADERS = ["id", "survey_no", "status"]
FILE1 = SimpleNamespace(filename="file1.csv")
FILE2 = SimpleNamespace(filename="file2.csv")
FILE3 = SimpleNamespace(filename="file3.csv")

# ---------------------------------------------------------------------
# 0. File selection itself was never the problem -- confirmed directly.
# A question naming both filenames resolves both documents, not one.
# ---------------------------------------------------------------------
_hits = mentions_filename(
    "Across sis_survey_records_en.csv and sis_survey_records_en_v2.csv, is "
    "the status of survey number 805 consistent between the two files?",
    [SimpleNamespace(filename="sis_survey_records_en.csv"),
     SimpleNamespace(filename="sis_survey_records_en_v2.csv")])
check("mentions_filename() resolves BOTH named files from the original "
      "failing message (confirms the bug was not in file selection)",
      len(_hits) == 2)

# ---------------------------------------------------------------------
# 1. The confirmed, now-fixed failure shape: two files, a conflicting value.
# ---------------------------------------------------------------------
ROWS1 = [{"id": "1", "survey_no": "805", "status": "Pending"},
         {"id": "2", "survey_no": "900", "status": "Approved"}]
ROWS2 = [{"id": "1", "survey_no": "805", "status": "Rejected"},
         {"id": "2", "survey_no": "901", "status": "Pending"}]

for q in ("what is the status of survey 805",
          "status of survey 805",
          "what is the status where survey_no is 805"):
    res1, res2 = _ask(q, HEADERS, ROWS1), _ask(q, HEADERS, ROWS2)
    check(f'"{q}" is independently answerable against each file',
          res1 is not None and res2 is not None)
    if res1 is None or res2 is None:
        continue
    text, cites = render_multi_csv_result([(FILE1, res1), (FILE2, res2)], "en")
    check(f'"{q}" states the CONFLICT, not a refusal or a silently-picked value',
          "differ" in text.lower() or ("Pending" in text and "Rejected" in text))
    check(f'"{q}" names BOTH files in the conflict (provenance, never merged)',
          "file1.csv" in text and "file2.csv" in text)
    check(f'"{q}" carries a citation for each file',
          any("file1.csv" in c for c in cites) and any("file2.csv" in c for c in cites))

# ---------------------------------------------------------------------
# 2. Positive case: the SAME value in both files is stated as agreement,
# not flagged as a conflict that doesn't exist.
# ---------------------------------------------------------------------
ROWS2_AGREE = [{"id": "1", "survey_no": "805", "status": "Pending"}]
res1 = _ask("what is the status of survey 805", HEADERS, ROWS1)
res2_agree = _ask("what is the status of survey 805", HEADERS, ROWS2_AGREE)
text, cites = render_multi_csv_result([(FILE1, res1), (FILE2, res2_agree)], "en")
check('matching values across files are stated as agreement ("Yes")',
      text.lower().startswith("yes"))
check("the agreed value itself is still stated",
      "Pending" in text)
check('agreement case is NOT misreported as a difference',
      "differ" not in text.lower())

# ---------------------------------------------------------------------
# 3. Negative case: only ONE of the files answers the question (the value
# does not appear in the other) -- no comparison language is invented for
# a value that was never found in more than one place.
# ---------------------------------------------------------------------
# Two distinct values on purpose: a single-row (or single-distinct-value)
# file answers "what is the status" from its one constant value regardless
# of which survey number was asked -- a DIFFERENT, pre-existing csv_ops
# shortcut unrelated to this fix. Two different values here rules that out,
# so "805" genuinely matching nothing is what is actually being tested.
ROWS2_NO_MATCH = [{"id": "1", "survey_no": "999", "status": "Approved"},
                  {"id": "2", "survey_no": "111", "status": "Rejected"}]
res2_none = _ask("what is the status of survey 805", HEADERS, ROWS2_NO_MATCH)
check("a file with no matching row produces no csv_ops result at all "
      "(nothing for render_multi_csv_result to merge in)",
      res2_none is None or not res2_none.ok or not res2_none.value)

# ---------------------------------------------------------------------
# 4. Three files: the fix generalises past exactly two, each kept distinct.
# ---------------------------------------------------------------------
ROWS3 = [{"id": "1", "survey_no": "805", "status": "Approved"}]
res3 = _ask("what is the status of survey 805", HEADERS, ROWS3)
text3, cites3 = render_multi_csv_result(
    [(FILE1, res1), (FILE2, res2_agree), (FILE3, res3)], "en")
check("three files with two different values (Pending/Pending/Approved) "
      "report the conflict across all three, not just the first pair",
      "differ" in text3.lower() and "Approved" in text3 and "Pending" in text3)
check("all three filenames appear in the three-file conflict",
      all(f in text3 for f in ("file1.csv", "file2.csv", "file3.csv")))

# ---------------------------------------------------------------------
# 5. A non-comparable shape (a count) is rendered per file, never blended
# into one merged total -- the explicit "do not merge" safeguard.
# ---------------------------------------------------------------------
res_count1 = _ask("how many are pending", HEADERS, ROWS1)
res_count2 = _ask("how many are pending", HEADERS, ROWS2)
text_c, _ = render_multi_csv_result([(FILE1, res_count1), (FILE2, res_count2)], "en")
check("a count question renders each file's own figure separately",
      "file1.csv" in text_c and "file2.csv" in text_c)
check("a count question does NOT silently sum the two files into one total",
      str(res_count1.value) in text_c and str(res_count2.value) in text_c)

# ---------------------------------------------------------------------
# 6. Tamil rendering of the same conflict.
# ---------------------------------------------------------------------
text_ta, _ = render_multi_csv_result([(FILE1, res1), (FILE2, res2)], "ta")
check("Tamil rendering states the conflict in Tamil script",
      "வேறுபடு" in text_ta)
check("Tamil rendering still names both files",
      "file1.csv" in text_ta and "file2.csv" in text_ta)

print(f"\n{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
