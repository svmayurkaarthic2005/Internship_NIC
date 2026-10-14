# -*- coding: utf-8 -*-
"""Regression for Phase 4.4 of the chatbot defect audit: `_TA_TANGLISH_MUTATION_RE`
only matched a BARE Tamil verb stem anchored to the end of the message
("நீக்கு$") -- the polite imperative an officer actually types ("நீக்குங்கள்",
"அங்கீகரிக்கவும்", "மாற்றவும்") carries a suffix the old pattern never
accounted for, so every one of them went completely undetected by this
classifier. "முடக்கு" (freeze a ward/block) and its reversal "திற" had no
Tamil-script form at all, only the English words in `_MUTATION_VERBS`.

NOTE: this classifier feeds the early-guard REFUSAL message, not the actual
database write path -- the independent read-only guard (contextvar +
SQLAlchemy listeners, see CLAUDE.md "The chatbot cannot change the data")
is what makes an unauthorized write impossible even when this classifier
misses a phrasing, exactly per this audit's own Phase 6 instruction not to
rely on the classifier alone for security. This fix is about giving the
officer the honest, informative refusal instead of letting the phrase fall
through to the general pipeline.

No DB, no LLM.
"""
import sys

sys.path.insert(0, r"C:\proj\nic_internship")
from backend.services.chatbot import chat_smalltalk as cs

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


MUTATIONS = [
    "வார்டு 102 முடக்கு", "வார்டு 102 முடக்கவும்",
    "விண்ணப்பத்தை நீக்குங்கள்", "பதிவை மாற்றவும்",
    "அங்கீகரிக்கவும்", "ரத்து செய்யவும்", "மாற்றுங்கள்",
    "maathunga", "mudakkavum",
]

# "neekkunga" is deliberately NOT included here: it is the same Tamil verb
# stem ("நீக்கு") as a bare chat-transcript "clear" command, already
# resolved (correctly, and before this fix) in favour of that safe,
# reversible reading by `_contentless_message`'s clear-command detection,
# which runs before this classifier is ever reached. Known, accepted
# ambiguity -- not something this fix changes or should change.

HARMLESS_QUESTIONS = [
    "வார்டு 102 முடக்கப்பட்டதா?",
    "நீக்குவது எப்படி?",
]


def test_polite_tamil_and_tanglish_mutations_are_detected():
    for m in MUTATIONS:
        check(f"{m!r} is detected as a mutation request", cs._is_mutation_request(m))


def test_questions_about_the_same_verbs_are_not_misread():
    for m in HARMLESS_QUESTIONS:
        check(f"{m!r} (a question) is NOT treated as a mutation request", not cs._is_mutation_request(m))


if __name__ == "__main__":
    test_polite_tamil_and_tanglish_mutations_are_detected()
    test_questions_about_the_same_verbs_are_not_misread()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
