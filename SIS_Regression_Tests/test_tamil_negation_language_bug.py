"""Regression suite for a CONFIRMED, UI-reproduced and now-FIXED defect
(QA audit 2026-10-13, fixed 2026-10-13): negating a Tamil-language
instruction ("don't explain in Tamil") used to still switch the chatbot's
response into Tamil script, because the request was read as a positive one.

Reproduced live, twice, through the actual rendered chatbot UI
(http://localhost:3000/chatbot.html), officer marun_chn@sis.tn.gov.in:

  1. "What is ISD?" -> "in detail" -> "dont explain in tamil"
     Expected: an English answer (the instruction is a prohibition).
     Actual (before the fix): a full Tamil-script ISD explanation.

  2. Fresh session, standalone: "Don't explain in Tamil."
     Expected: an English acknowledgement/clarification.
     Actual (before the fix): the generic "I don't understand" fallback,
     itself rendered in Tamil.

Positive control, same session, same UI: "Explain ISD in Tamil." correctly
returned a well-formed Tamil answer both before and after the fix -- only
the negated path was broken.

Root cause: `backend/services/rag.py`, `detect_language()`'s `_tamil_request`
regex matched a request verb/preposition ("in", "answer", "translate", ...)
within 15 characters of "tamil", with NO check anywhere for a negator in the
sentence. "dont explain **in** **Tamil**" satisfied it via the bare "in"
sitting directly in front of "Tamil" -- structurally identical to the
genuine positive phrasing "explain **in** Tamil" the pattern exists to
catch.

Fix: `_explicit_tamil_request()` in `rag.py` -- a small, LOCAL clause-aware
negation check (the same shape as `chatbot.py`'s `_is_format_negation_
request()`, re-implemented here rather than imported, since `chatbot.py`
imports FROM `rag.py` and importing back would be circular). The message is
split on clause boundaries (`;.!?` / but / however / although / though /
ஆனால்); a negator (`don't`/`dont`/`do not`/`never`/`not`/`no`) governs a
Tamil-request cue only when it sits in the SAME clause and BEFORE the cue,
within a bounded word gap. A clause with no un-negated cue contributes no
Tamil request; the character-percentage / Tanglish-keyword fallbacks below
it in `detect_language()` are completely untouched.

No DB, no LLM: `detect_language()` is a pure function.
Run: python SIS_Regression_Tests/test_tamil_negation_language_bug.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.services.rag import detect_language  # noqa: E402

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


# ---------------------------------------------------------------------
# 1. Positive Tamil requests -- must still select Tamil
# ---------------------------------------------------------------------
POSITIVE_TAMIL = (
    "Explain ISD in Tamil",
    "Answer in Tamil",
    "Translate this into Tamil",
    "Please respond in Tamil.",
    "explain in tamil",
    "ISD meaning Tamil la sollunga.",
)
for msg in POSITIVE_TAMIL:
    lang = detect_language(msg)
    check(f'"{msg}" selects Tamil (positive request)', lang == "ta",
          f"detect_language() returned {lang!r}")

# ---------------------------------------------------------------------
# 2. Negative Tamil requests -- the confirmed defect; must NOT select Tamil
# ---------------------------------------------------------------------
NEGATIVE_TAMIL = (
    "Don't explain in Tamil",
    "dont explain in tamil",
    "Do not answer in Tamil",
    "Never respond in Tamil",
    "Please don't translate this into Tamil",
    "I do not want the explanation in Tamil",
    "No Tamil, English only",
    "Don't answer in Tamil.",
    "I don't want a Tamil explanation.",
    "Not in Tamil, in English.",
)
for msg in NEGATIVE_TAMIL:
    lang = detect_language(msg)
    check(f'"{msg}" does NOT select Tamil (negated request)', lang != "ta",
          f"detect_language() returned {lang!r}")

# ---------------------------------------------------------------------
# 3. English preference and correction -- explicit English must be honoured
# ---------------------------------------------------------------------
ENGLISH_PREFERENCE = (
    "No, English only",
    "Please keep the answer in English",
    "Don't explain in Tamil; explain in English",
    "I asked for English, not Tamil",
)
for msg in ENGLISH_PREFERENCE:
    lang = detect_language(msg)
    check(f'"{msg}" selects English', lang not in ("ta",),
          f"detect_language() returned {lang!r}")

# ---------------------------------------------------------------------
# 4. Clause-boundary cases -- negation must stay scoped to its own clause
# ---------------------------------------------------------------------
check('"Explain ISD in Tamil, but don\'t give examples" still selects Tamil '
      "(the negation governs examples, a different clause, not the language)",
      detect_language("Explain ISD in Tamil, but don't give examples") == "ta")
check('"Don\'t explain in Tamil; explain in English" selects English '
      "(the second clause carries no Tamil request to cancel)",
      detect_language("Don't explain in Tamil; explain in English") != "ta")
check('"Explain in Tamil. Actually, don\'t use Tamil; English only." resolves to '
      "the LAST clause's instruction, English (final-instruction precedence)",
      detect_language("Explain in Tamil. Actually, don't use Tamil; English only.") != "ta")

# ---------------------------------------------------------------------
# 5. Regression safeguards -- existing behaviour must be untouched
# ---------------------------------------------------------------------
check('"Explain Tamil Nadu\'s urban survey system" is NOT a Tamil-language '
      "request (place name, pre-existing guard)",
      detect_language("Explain Tamil Nadu's urban survey system") != "ta")
check('"applications in Tamil Nadu" stays English (place name)',
      detect_language("show applications in Tamil Nadu") != "ta")
check("Tamil-script input is still detected as Tamil (character-percentage path, "
      "untouched by the explicit-request fix)",
      detect_language("தமிழில் விளக்கவும்.") == "ta")
check("Pure English with no Tamil mention stays English",
      detect_language("What is ISD?") == "en")
check("Tanglish keyword detection is untouched",
      detect_language("ISD endha wardu la irukku") == "tanglish")

print(f"\n{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
