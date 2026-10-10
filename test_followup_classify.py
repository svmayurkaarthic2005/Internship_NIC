"""followup_context.classify() must tell a fresh, self-contained question
apart from a short follow-up fragment. Several branches matched a follow-up
SHAPE (an ordinal word, a slice phrase, an exclude word, a column-reorder
trigger) as a plain substring with no length guard, so a long fresh sentence
that happened to contain one of those words was read as pointing at a row or
column on a table that might not even be on screen. Same bug class as the
`_REFINE_RE` fix documented inline in followup_context.py; this file is the
regression test that one did not get.

No DB, no LLM: python test_followup_classify.py
"""
import sys

from backend.services import followup_context as fc

# (message, expected classify() result) -- long, fresh, self-contained
# questions that must NOT be read as a follow-up fragment.
FRESH_NOT_FOLLOWUP = [
    ("What is the first application submitted this month under service code 0178?", "none"),
    ("Which officer handled the second application filed in ward 002 this year?", "none"),
    ("Show me the last two applications approved by the Tahsildar this week", "none"),
    ("excluding the first one which applications are pending in ward 002", "none"),
    ("other than the first application what documents are missing in ward 002", "none"),
    ("which applications are in ward 002 and block 005 before the deadline", "none"),
    ("how many applications in ward 002 were approved before block 005", "none"),
]

# Short genuine follow-up fragments that must keep working.
GENUINE_FOLLOWUP = [
    ("what about the first one?", "singular"),
    ("the 2nd one", "singular"),
    ("show the last two", "singular"),
    ("and the first application", "singular"),
    ("remove row 2", "refine"),
    ("exclude the first one", "refine"),
    ("drop 2026/0154/28/001167", "refine"),
    ("not the second one", "refine"),
]

COLUMN_REORDER = [
    ("show district before ward", True),
    ("show ward before block", True),
    ("put block before ward", True),
    ("show district first", True),
    ("order columns as district, taluk, ward, block", True),
    ("which applications are in ward 002 and block 005 before the deadline", False),
    ("how many applications in ward 002 were approved before block 005", False),
    ("excluding the first one which applications are pending in ward 002", False),
]


def run():
    failures = []

    for msg, expected in FRESH_NOT_FOLLOWUP + GENUINE_FOLLOWUP:
        got = fc.classify(msg)
        if got != expected:
            failures.append(f"classify({msg!r}) = {got!r}, expected {expected!r}")

    for msg, expected in COLUMN_REORDER:
        got = fc.is_column_reorder_fragment(msg)
        if got != expected:
            failures.append(f"is_column_reorder_fragment({msg!r}) = {got!r}, expected {expected!r}")

    if failures:
        print(f"{len(failures)} failure(s):")
        for f in failures:
            print(" -", f)
        sys.exit(1)
    print(f"{len(FRESH_NOT_FOLLOWUP)} fresh questions not read as a follow-up, "
          f"{len(GENUINE_FOLLOWUP)} genuine fragments still classified, "
          f"{len(COLUMN_REORDER)} column-reorder cases correct: ok")


if __name__ == "__main__":
    run()
