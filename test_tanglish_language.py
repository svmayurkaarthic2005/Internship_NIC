"""Regression tests for Romanized Tamil (Tanglish) language detection and replies.

Run from the repository root:
    python test_tanglish_language.py

These are routing/helper-level tests; they do not query PostgreSQL or call Ollama.
The imports still require the project's normal Python dependencies and local settings.
"""
from __future__ import annotations

import re
import sys

from backend.services.rag import detect_language, parse_intent
from backend.services.chatbot import (
    _clarification_reply,
    _scope_reply,
    _workflow_steps_topic,
)


_results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((name, bool(condition), detail))
    print(("ok   " if condition else "FAIL ") + name + (f" -- {detail}" if detail and not condition else ""))


def has_tamil_script(text: str) -> bool:
    return bool(re.search(r"[\u0B80-\u0BFF]", text or ""))


# Common wording, including the exact phrase from the reported screenshot.
TANGLISH_INPUTS = (
    "inki endha maal",
    "inga endha mall?",
    "enakku pending applications evlo irukku?",
    "0154 ku field visit venuma?",
    "zdt role enna?",
    "tahsildar role enna?",
    "indha application status enna?",
    "application status sollunga",
    "ennoda pending files kaatu",
    "ward 12 la applications evlo?",
    "survey 145 ku patta number enna?",
    "ISD and NISD difference enna?",
    "enakku status theriyanum",
    "application irukka?",
    "ithellam explain pannunga",
    "pending application list kudunga",
    "application number enna",
    "unga workload evlo",
    "ithu ISD ah illa NISD ah?",
    "field visit date maatha Tahsildar kitta kekkanuma?",
    "indha maasam pending files kaatu",
    "entha application reject aachu?",
    "application-a check panni sollunga",
    "romba pending irukku",
    "enakku application number theriyala",
)

for query in TANGLISH_INPUTS:
    check(f"detects Tanglish: {query!r}", detect_language(query) == "tanglish",
          f"detected={detect_language(query)!r}")


# Typo-tolerant cases use distinctive words, not common English near-neighbours.
TANGLISH_TYPO_INPUTS = (
    "sollnga status",
    "pannnga application status",
    "theriyalla enakku status",
    "kudnga pending applications",
    "vanakkkam",
)
for query in TANGLISH_TYPO_INPUTS:
    check(f"detects misspelled Tanglish: {query!r}", detect_language(query) == "tanglish",
          f"detected={detect_language(query)!r}")


# Guard against classifying ordinary English as Tanglish via fuzzy matching.
ENGLISH_INPUTS = (
    "What is the status of my application?",
    "Which ward has the most applications?",
    "Today I need the latest district status in India.",
    "Applications submitted in Tamil Nadu",
    "Show pending applications for service code 0154.",
    "The application is still pending.",
    "I need help with the ward number.",
)
for query in ENGLISH_INPUTS:
    check(f"keeps English as English: {query!r}", detect_language(query) == "en",
          f"detected={detect_language(query)!r}")


check("Tamil-script message remains Tamil",
      detect_language("எனது விண்ணப்பங்கள் எத்தனை?") == "ta",
      detect_language("எனது விண்ணப்பங்கள் எத்தனை?"))
check("Tamil + English mixed-script message is recognised as Tanglish",
      detect_language("application நிலை enna") == "tanglish",
      detect_language("application நிலை enna"))


# The exact failure in the screenshot fell through to this clarification. A
# Tanglish turn must receive Latin-script Tanglish, not English or Tamil script.
clar_ta = _clarification_reply("ta")
clar_en = _clarification_reply("en")
clar_tanglish = _clarification_reply("tanglish")
check("Tanglish clarification is not accidentally returned in Tamil script",
      not has_tamil_script(clar_tanglish), clar_tanglish)
check("Tanglish clarification contains a helpful SIS-domain prompt",
      "application" in clar_tanglish.lower() and "workflow" in clar_tanglish.lower(),
      clar_tanglish)
check("Tamil clarification remains in Tamil script", has_tamil_script(clar_ta), clar_ta)
check("English clarification remains in English", "I'm not sure" in clar_en, clar_en)


scope_tanglish = _scope_reply("tanglish")
scope_tanglish_capability = _scope_reply("tanglish", capability=True)
check("out-of-scope Tanglish reply is in Latin-script Tanglish",
      not has_tamil_script(scope_tanglish) and "Naan" in scope_tanglish,
      scope_tanglish)
check("capability reply in Tanglish avoids inventing a sample application ID",
      not has_tamil_script(scope_tanglish_capability)
      and not re.search(r"\b20\d{2}/\d{3,4}/\d{1,3}/\d+\b", scope_tanglish_capability),
      scope_tanglish_capability)


# Ensure the language fix does not replace domain routing or the recent role-term fix.
check("'0154 ku field visit venuma?' reaches the service-code definition handler",
      parse_intent("0154 ku field visit venuma?") == "service_code_lookup",
      parse_intent("0154 ku field visit venuma?"))
check("'zdt role enna' routes to the ZDT glossary entry",
      _workflow_steps_topic("zdt role enna") == "GLOSS:zdt",
      str(_workflow_steps_topic("zdt role enna")))
check("'tahsildar role enna' routes to the Tahsildar glossary entry",
      _workflow_steps_topic("tahsildar role enna") == "GLOSS:tahsildar",
      str(_workflow_steps_topic("tahsildar role enna")))


def main() -> None:
    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} passed")
    if passed != total:
        print("\nFailures:")
        for name, ok, detail in _results:
            if not ok:
                print(f" - {name}: {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
