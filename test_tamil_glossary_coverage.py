# -*- coding: utf-8 -*-
"""Regression for Phase 4.1 of the chatbot defect audit: Tamil-script
glossary questions ("பட்டா என்றால் என்ன?") fell through to the LLM because
only 3 of 9+ already-defined `_GLOSSARY` entries had a Tamil-script trigger
word in `_GLOSS_TA_WORD` / `_GLOSS_TA2_RE`. Every term added here already had
a full, reviewed English+Tamil definition in `_GLOSSARY` -- only the
Tamil-script trigger was missing. No new facts invented.

No DB, no LLM.
"""
import sys

sys.path.insert(0, r"C:\proj\nic_internship")
from backend.services.chatbot import chat_smalltalk as cs

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


CASES = [
    ("பட்டா என்றால் என்ன?", "GLOSS:patta"),
    ("தாசில்தார் என்றால் என்ன?", "GLOSS:tahsildar"),
    ("உட்பிரிவு என்றால் என்ன?", "GLOSS:subdivision"),
    ("கள ஆய்வு என்றால் என்ன?", "GLOSS:fieldvisit"),
    ("சர்வே எண் என்றால் என்ன?", "GLOSS:surveynumber"),
    ("நத்தம் சிட்டா என்றால் என்ன?", "GLOSS:nathamchitta"),
    ("கட்டணம் என்றால் என்ன?", "GLOSS:fee"),
    ("நிலுவை என்றால் என்ன?", "GLOSS:pending"),
]


def test_new_tamil_glossary_triggers_resolve_to_existing_entries():
    for q, expected in CASES:
        topic = cs._workflow_steps_topic(q, [])
        check(f"{q!r} -> {expected}", topic == expected, topic)
        # Every topic must actually resolve to real text, not an empty dict.
        if topic == expected:
            text = cs._workflow_steps_answer(topic, is_tamil=True)
            check(f"{q!r} answer is non-empty Tamil text", bool(text and text.strip()), repr(text))


def test_unrelated_sentences_containing_the_word_are_not_falsely_claimed():
    # These must NOT be read as glossary questions -- the trigger requires
    # the whole message to be "<noun> என்றால் என்ன?", not just contain the word.
    NOT_GLOSSARY = [
        "இந்த விண்ணப்பத்தின் பட்டா எண் என்ன?",       # asks for THIS application's patta number
        "தாசில்தாரிடம் அனுப்பப்பட்டதா?",              # "was it sent to the Tahsildar?"
        "சர்வே எண் 125 இன் விவரம் என்ன?",             # asks about survey number 125, not the concept
    ]
    for q in NOT_GLOSSARY:
        topic = cs._workflow_steps_topic(q, [])
        check(f"{q!r} is not misread as a bare glossary question", topic not in (
            "GLOSS:patta", "GLOSS:tahsildar", "GLOSS:surveynumber"), topic)


if __name__ == "__main__":
    test_new_tamil_glossary_triggers_resolve_to_existing_entries()
    test_unrelated_sentences_containing_the_word_are_not_falsely_claimed()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
