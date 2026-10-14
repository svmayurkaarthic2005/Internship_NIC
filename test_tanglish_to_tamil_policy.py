"""Comprehensive regression tests for the Mandatory Tanglish-to-Tamil response policy.

Requirement:
Whenever a user asks a question in Tanglish, the chatbot must answer in natural Tamil
written in Tamil script. It must not reply in Romanised Tanglish or default to English.
Necessary English technical terms, acronyms, and official identifiers must be retained.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from backend.services.rag import (
    detect_language,
    build_prompt,
    build_html_response,
)
from backend.services.chatbot.agent import _LANGUAGE_RULES
from backend.services.chatbot.attachment_qa import _LANG_LINE
from backend.services.chatbot import (
    _clarification_reply,
    _scope_reply,
    build_invalid_app_number_message,
    build_app_not_found_message,
    build_app_forbidden_message,
    build_app_confirm_message,
    build_ambiguous_reference_message,
    _today_date_answer,
    _CURRENT_CLOCK_OVERRIDE,
    is_short_reply,
    get_prior_turn_language,
)


_results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((name, bool(condition), detail))
    print(("ok   " if condition else "FAIL ") + name + (f" -- {detail}" if detail and not condition else ""))


def has_tamil_script(text: str) -> bool:
    return bool(re.search(r"[\u0B80-\u0BFF]", text or ""))


# ── 1. Language Detection & Classification ────────────────────────────────────

# Pure Tamil -> "ta"
check("Tamil script detected as 'ta'", detect_language("இன்றைய தேதி என்ன?") == "ta")
check("Tamil script pending query detected as 'ta'", detect_language("நிலுவையில் உள்ள விண்ணப்பங்கள்") == "ta")

# Pure Tanglish -> "tanglish"
check("Tanglish query detected as 'tanglish'", detect_language("inki enda nal") == "tanglish")
check("Tanglish status query detected as 'tanglish'", detect_language("indha application status enna?") == "tanglish")
check("Tanglish pending query detected as 'tanglish'", detect_language("enakku pending applications evlo irukku?") == "tanglish")

# Pure English -> "en"
check("English query detected as 'en'", detect_language("What is today's date?") == "en")
check("English status query detected as 'en'", detect_language("Show pending applications for ward 2") == "en")

# Mixed Tamil-English (primarily Tamil/Tanglish) -> "ta" or "tanglish"
check("Mixed Tamil-English detected as Tamil or Tanglish", detect_language("application நிலை enna") in ("ta", "tanglish"))
check("Mixed with English technical terms detected as Tamil/Tanglish",
      detect_language("ISD application விவரங்கள் என்ன?") in ("ta", "tanglish"))


# ── 2. LLM System Prompts Mandate Tamil Script for Tanglish ───────────────────

# In rag.py: build_prompt
prompt_tanglish = build_prompt(
    query="indha application status enna?",
    context="Application 2026/0154/28/001280 is pending.",
    structured_data={},
    language="tanglish"
)
check("build_prompt for Tanglish mandates Tamil script",
      "Tamil script" in prompt_tanglish and "Do not reply in Romanized Tamil" in prompt_tanglish)
check("build_prompt for Tanglish preserves official identifiers in Latin script",
      "Latin script" in prompt_tanglish or "English" in prompt_tanglish)

prompt_ta = build_prompt(
    query="விண்ணப்ப நிலை என்ன?",
    context="Application 2026/0154/28/001280 is pending.",
    structured_data={},
    language="ta"
)
check("build_prompt for Tamil mandates Tamil script", "Tamil" in prompt_ta)

prompt_en = build_prompt(
    query="What is the application status?",
    context="Application 2026/0154/28/001280 is pending.",
    structured_data={},
    language="en"
)
check("build_prompt for English mandates English", "English" in prompt_en)


# In agent.py: _LANGUAGE_RULES
check("agent.py _LANGUAGE_RULES['tanglish'] mandates Tamil script",
      "Tamil script" in _LANGUAGE_RULES["tanglish"] and "do not reply in roman script" in _LANGUAGE_RULES["tanglish"].lower())
check("agent.py _LANGUAGE_RULES['ta'] specifies Tamil",
      "Tamil" in _LANGUAGE_RULES["ta"])
check("agent.py _LANGUAGE_RULES['en'] specifies English",
      "English" in _LANGUAGE_RULES["en"])


# In attachment_qa.py: _LANG_LINE
check("attachment_qa.py _LANG_LINE['tanglish'] mandates Tamil script",
      "Tamil script" in _LANG_LINE["tanglish"])
check("attachment_qa.py _LANG_LINE['ta'] specifies Tamil",
      "Tamil" in _LANG_LINE["ta"])


# ── 3. RAG HTML Response Language Selection ───────────────────────────────────

sample_sd = {
    "applications": [
        {"application_number": "2026/0154/28/001280", "type": "ISD", "status": "pending", "stage": "SIS"}
    ]
}
html_resp_tanglish = build_html_response(sample_sd, language="tanglish")
check("build_html_response for Tanglish renders Tamil headers",
      has_tamil_script(html_resp_tanglish) and ("நிலை" in html_resp_tanglish or "விண்ணப்ப" in html_resp_tanglish))

html_resp_en = build_html_response(sample_sd, language="en")
check("build_html_response for English renders English headers",
      ("Status" in html_resp_en or "Application" in html_resp_en) and not has_tamil_script(html_resp_en))


# ── 4. Deterministic Bot Responses in Tamil Script for Tanglish ───────────────

# Clarification
clar_tanglish = _clarification_reply("tanglish")
check("Tanglish clarification is in Tamil script", has_tamil_script(clar_tanglish))
check("Tanglish clarification mentions 'விண்ணப்ப'", "விண்ணப்ப" in clar_tanglish)

# Scope / capability
scope_tanglish = _scope_reply("tanglish")
check("Tanglish out-of-scope reply is in Tamil script", has_tamil_script(scope_tanglish))
scope_cap_tanglish = _scope_reply("tanglish", capability=True)
check("Tanglish capability reply is in Tamil script", has_tamil_script(scope_cap_tanglish))

# Invalid application number
inv_tanglish = build_invalid_app_number_message("2026/INVALID", "tanglish")
check("Invalid app message for Tanglish is in Tamil script", has_tamil_script(inv_tanglish))
check("Invalid app message retains the token in Latin script", "2026/INVALID" in inv_tanglish)

# App not found
not_found_tanglish = build_app_not_found_message({"application_number": "2026/0154/28/009999"}, "tanglish")
check("App not found for Tanglish is in Tamil script", has_tamil_script(not_found_tanglish))
check("App not found retains the application number", "2026/0154/28/009999" in not_found_tanglish)

# App forbidden
class DummyOfficer:
    role = "ward_surveyor"
    ward = "Ward 2"
    taluk = "Thoothukudi"

forbidden_tanglish = build_app_forbidden_message("2026/0154/28/001280", DummyOfficer(), "tanglish")
check("Forbidden app message for Tanglish is in Tamil script", has_tamil_script(forbidden_tanglish))
check("Forbidden app message retains application number", "2026/0154/28/001280" in forbidden_tanglish)

# App confirmation
confirm_tanglish = build_app_confirm_message(
    "2026/0154/28/001280",
    [{"application_number": "2026/0154/28/001281", "type": "ISD", "status": "pending", "stage": "SIS"}],
    "tanglish"
)
check("App confirmation message for Tanglish is in Tamil script", has_tamil_script(confirm_tanglish))
check("App confirmation message retains suggestions", "2026/0154/28/001281" in confirm_tanglish)

# Ambiguous reference
ambig_tanglish = build_ambiguous_reference_message(["2026/0154/28/001280", "2026/0154/28/001281"], "tanglish")
check("Ambiguous reference message for Tanglish is in Tamil script", has_tamil_script(ambig_tanglish))
check("Ambiguous reference message retains candidates", "2026/0154/28/001280" in ambig_tanglish)


# ── 5. Date and Weekday Responses in Tamil Script for Tanglish ────────────────

ist_tz = timezone(timedelta(hours=5, minutes=30))
mock_dt = datetime(2026, 10, 13, 14, 30, tzinfo=ist_tz)
tok = _CURRENT_CLOCK_OVERRIDE.set(mock_dt)
try:
    ans_tanglish_date = _today_date_answer("tanglish", "innaiku enna date")
    check("Tanglish date query returns Tamil script 'இன்றைய தேதி'",
          has_tamil_script(ans_tanglish_date) and "இன்றைய தேதி" in ans_tanglish_date)
    check("Tanglish date query includes Tamil month name 'அக்டோபர்'",
          "அக்டோபர்" in ans_tanglish_date)

    ans_tanglish_weekday = _today_date_answer("tanglish", "inniki enna kizhamai")
    check("Tanglish weekday query returns Tamil script 'செவ்வாய்க்கிழமை'",
          has_tamil_script(ans_tanglish_weekday) and "செவ்வாய்க்கிழமை" in ans_tanglish_weekday)

    ans_tanglish_both = _today_date_answer("tanglish", "inki enda nal")
    check("Tanglish 'inki enda nal' returns Tamil script weekday and date",
          has_tamil_script(ans_tanglish_both) and "செவ்வாய்க்கிழமை" in ans_tanglish_both and "13 அக்டோபர் 2026" in ans_tanglish_both)
finally:
    _CURRENT_CLOCK_OVERRIDE.reset(tok)


# ── 6. Contextual Preservation: Short Replies Inheriting Prior Language ────────

check("Short reply 'no' is recognised", is_short_reply("no"))
check("Short reply 'illa' is recognised", is_short_reply("illa"))
check("Short reply 'apdi illa' is recognised", is_short_reply("apdi illa"))
check("Short reply 'illai' is recognised", is_short_reply("illai"))
check("Short reply 'yes' is recognised", is_short_reply("yes"))
check("Short reply 'ama' is recognised", is_short_reply("ama"))
check("Short reply 'sari' is recognised", is_short_reply("sari"))
check("Short reply 'ok' is recognised", is_short_reply("ok"))
check("Detailed question is NOT a short reply", not is_short_reply("application status sollunga"))

history_tanglish = [
    {"role": "user", "content": "inki enda nal"},
    {"role": "assistant", "content": "இன்று செவ்வாய்க்கிழமை, 13 அக்டோபர் 2026."},
]
check("Prior turn language extracted from Tanglish query",
      get_prior_turn_language(history_tanglish) == "tanglish")

history_tamil = [
    {"role": "user", "content": "விண்ணப்ப விவரங்கள் காட்டு"},
    {"role": "assistant", "content": "விண்ணப்ப விவரங்கள் கீழே உள்ளன."},
]
check("Prior turn language extracted from Tamil query",
      get_prior_turn_language(history_tamil) == "ta")

history_english = [
    {"role": "user", "content": "Show my pending applications"},
    {"role": "assistant", "content": "Here are your pending applications."},
]
check("Prior turn language extracted from English query",
      get_prior_turn_language(history_english) == "en")


# ── Summary ───────────────────────────────────────────────────────────────────

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
