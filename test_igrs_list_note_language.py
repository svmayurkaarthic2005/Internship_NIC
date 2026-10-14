# -*- coding: utf-8 -*-
"""Regression for Phase 4.3 of the chatbot defect audit: `_igrs_over_list_note()`
always answered "do these carry an IGRS Form 6 number?" in English, even when
the question (and the surrounding table) was in Tamil/Tanglish --
`_IGRS_QUESTION_RE` already matches the Tamil phrasing ("படிவம் 6"), so the
trigger fired, but the note text itself ignored `language` entirely.

No DB, no LLM.
"""
import sys

sys.path.insert(0, r"C:\proj\nic_internship")
from backend.services.chatbot import chat_smalltalk as cs

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


ALL_SRO = [{"submission_channel": "sub_registrar"}] * 3
NONE_SRO = [{"submission_channel": "csc"}] * 3
MIXED = [{"submission_channel": "sub_registrar"}] * 2 + [{"submission_channel": "csc"}]


def test_tamil_question_gets_tamil_note_all_three_counts():
    for apps, label in ((ALL_SRO, "all"), (NONE_SRO, "none"), (MIXED, "mixed")):
        note = cs._igrs_over_list_note("இவைகளுக்கு IGRS படிவம் 6 எண் இருக்கிறதா?", apps, "ta")
        check(f"Tamil question + {label} IGRS case returns Tamil text",
              note and any(ord(c) > 2000 for c in note), note)


def test_english_question_still_gets_english_note():
    note = cs._igrs_over_list_note("do these have an IGRS number?", ALL_SRO, "en")
    check("English question still gets the English note",
          note and note.startswith("Yes"), note)


def test_default_language_is_english_backward_compatible():
    # Old call sites that never pass `language` must behave exactly as before.
    note = cs._igrs_over_list_note("do these have an IGRS number?", ALL_SRO)
    check("no language arg -> defaults to English (unchanged)", note and note.startswith("Yes"), note)


if __name__ == "__main__":
    test_tamil_question_gets_tamil_note_all_three_counts()
    test_english_question_still_gets_english_note()
    test_default_language_is_english_backward_compatible()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
