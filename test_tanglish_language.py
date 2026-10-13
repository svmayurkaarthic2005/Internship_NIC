"""Regression tests for Romanized Tamil (Tanglish) language detection and replies.

Run from the repository root:
    python test_tanglish_language.py

These are routing/helper-level tests; they do not query PostgreSQL or call Ollama.
The imports still require the project's normal Python dependencies and local settings.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from backend.services.rag import detect_language, parse_intent
from backend.services.chatbot import (
    _clarification_reply,
    _scope_reply,
    _workflow_steps_topic,
    _is_today_date_question,
    _today_date_answer,
    _CURRENT_CLOCK_OVERRIDE,
)


_results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((name, bool(condition), detail))
    print(("ok   " if condition else "FAIL ") + name + (f" -- {detail}" if detail and not condition else ""))


def has_tamil_script(text: str) -> bool:
    return bool(re.search(r"[\u0B80-\u0BFF]", text or ""))


# Common wording, including the exact phrase from the reported screenshot.
TANGLISH_INPUTS = (
    "inki enda nal",
    "inki endha naal",
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


# Tanglish turn receives natural Tamil in Tamil script (mandatory policy).
clar_ta = _clarification_reply("ta")
clar_en = _clarification_reply("en")
clar_tanglish = _clarification_reply("tanglish")
check("Tanglish clarification is returned in Tamil script",
      has_tamil_script(clar_tanglish), clar_tanglish)
check("Tanglish clarification contains helpful guidance in Tamil",
      "விண்ணப்ப" in clar_tanglish,
      clar_tanglish)
check("Tamil clarification remains in Tamil script", has_tamil_script(clar_ta), clar_ta)
check("English clarification remains in English", "I'm not sure" in clar_en, clar_en)


scope_tanglish = _scope_reply("tanglish")
scope_tanglish_capability = _scope_reply("tanglish", capability=True)
check("out-of-scope Tanglish reply is in Tamil script",
      has_tamil_script(scope_tanglish) and "நான்" in scope_tanglish,
      scope_tanglish)
check("capability reply in Tanglish avoids inventing a sample application ID and is in Tamil script",
      has_tamil_script(scope_tanglish_capability)
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


# ── Date & Weekday Recognition Tests (Deterministic, Offline) ────────────────

DATE_RECOGNITION_POSITIVE = (
    "inki enda nal",
    "inki endha naal",
    "inniki enna naal",
    "inniku enna naal",
    "innaiku enna date",
    "innaikku enna date",
    "innaikku enna thethi",
    "inniki enna kizhamai",
    "innaiki enna thethi",
    "todays date",
    "today's date",
    "today’s date",
    "what day is today",
    "what is today's date",
    "what is todays date",
    "what is today’s date",
    "what is the correct date today?",
    "இன்றைய தேதி",
    "இன்று என்ன தேதி",
    "இன்று தேதி என்ன",
    "இன்றைய நாள்",
    "இன்று என்ன நாள்",
    "இன்று என்ன கிழமை",
    "இன்று கிழமை என்ன",
    "date today",
    "current date",
)

for q in DATE_RECOGNITION_POSITIVE:
    check(f"recognises date question: {q!r}", _is_today_date_question(q), f"query={q!r}")

DATE_RECOGNITION_NEGATIVE = (
    "enna date",
    "endha naal",
    "application date enna?",
    "field visit date enna?",
    "what is the submission date of 2026/0154/28/001280",
    "what is the date of 2026/0154/28/001280",
    "2026/0154/28/001280 date",
    "what is the correct date for this application?",
    "submission date enna",
    "inspection date",
    "patta transfer date",
    "0154 field visit date",
    "விண்ணப்ப தேதி என்ன",
    "களப்பணி தேதி",
    "weather today",
    "how many applications today",
)

for q in DATE_RECOGNITION_NEGATIVE:
    check(f"rejects non-current date question: {q!r}", not _is_today_date_question(q), f"query={q!r}")

# Fixed IST Timestamp: Tuesday, 13 October 2026, 14:30:00 IST (UTC+05:30)
ist_tz = timezone(timedelta(hours=5, minutes=30))
mock_noon = datetime(2026, 10, 13, 14, 30, tzinfo=ist_tz)
token = _CURRENT_CLOCK_OVERRIDE.set(mock_noon)
try:
    ans_tanglish_date = _today_date_answer("tanglish", "innaiku enna date")
    check("Tanglish date query returns calendar date in Tamil script",
          has_tamil_script(ans_tanglish_date) and "இன்றைய தேதி" in ans_tanglish_date and "13 அக்டோபர் 2026" in ans_tanglish_date,
          ans_tanglish_date)

    ans_tanglish_weekday = _today_date_answer("tanglish", "inniki enna kizhamai")
    check("Tanglish weekday query returns weekday in Tamil script without calendar date",
          has_tamil_script(ans_tanglish_weekday) and "செவ்வாய்க்கிழமை" in ans_tanglish_weekday and "2026" not in ans_tanglish_weekday,
          ans_tanglish_weekday)

    ans_tanglish_both = _today_date_answer("tanglish", "inki enda nal")
    check("Tanglish 'naal' returns both weekday and calendar date in Tamil script",
          has_tamil_script(ans_tanglish_both) and "செவ்வாய்க்கிழமை" in ans_tanglish_both and "13 அக்டோபர் 2026" in ans_tanglish_both,
          ans_tanglish_both)

    ans_tamil_both = _today_date_answer("ta", "இன்று என்ன நாள்")
    check("Tamil 'naal' returns Tamil script weekday and date",
          has_tamil_script(ans_tamil_both) and "செவ்வாய்க்கிழமை" in ans_tamil_both and "13 அக்டோபர் 2026" in ans_tamil_both,
          ans_tamil_both)

    ans_en_date = _today_date_answer("en", "what is today’s date")
    check("English apostrophe variant returns English date",
          "Today's date is 13 October 2026 (2026-10-13)" in ans_en_date,
          ans_en_date)

    ans_en_correct = _today_date_answer("en", "what is the correct date today?")
    check("English 'correct date today' query returns English date",
          "Today's date is 13 October 2026 (2026-10-13)" in ans_en_correct,
          ans_en_correct)
finally:
    _CURRENT_CLOCK_OVERRIDE.reset(token)

# Midnight rollover check in IST
mock_before_midnight = datetime(2026, 10, 12, 23, 59, 50, tzinfo=ist_tz)
mock_after_midnight = datetime(2026, 10, 13, 0, 0, 10, tzinfo=ist_tz)

tok_bm = _CURRENT_CLOCK_OVERRIDE.set(mock_before_midnight)
try:
    ans_before = _today_date_answer("en", "what day is today")
finally:
    _CURRENT_CLOCK_OVERRIDE.reset(tok_bm)

tok_am = _CURRENT_CLOCK_OVERRIDE.set(mock_after_midnight)
try:
    ans_after = _today_date_answer("en", "what day is today")
finally:
    _CURRENT_CLOCK_OVERRIDE.reset(tok_am)

check("IST clock rollover: Monday before midnight", "Today is Monday." == ans_before, ans_before)
check("IST clock rollover: Tuesday after midnight", "Today is Tuesday." == ans_after, ans_after)


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
