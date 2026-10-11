"""Regression for a hallucination bug: "what does TSLR mean?" correctly refused
("I do not have the full form of TSLR ... I will not guess") because TSLR is a
deliberately-unaliased abbreviation (it names 5 different service codes -- see
find_service_code_by_name in backend/utils/helpers.py). But "What is TSLR used
for?" -- the practical-meaning phrasing, not the "mean"/"stand for" wording the
_FULLFORM_ASK_RE guard recognised -- fell through the same guard to
general_query, where the LLM invented a specific, confident, wrong expansion
("TSLR = Town Survey Land Register"), contradicting the refusal the other
phrasing of the exact same question gave.

Fix: _FULLFORM_ASK_RE gained a "what is X used/meant for?" alternative, read
only by _unknown_fullform (the refusal path) -- never by _fullform_term (the
known-acronym one-liner), so a KNOWN term ("what is ISD used for?") is
untouched and still falls through to its own richer handler (service_code_lookup)
instead of being short-circuited into the terse fullform one-liner.

No DB, no LLM:  python test_unknown_fullform_usedfor.py
"""
import sys
from backend.services import chatbot as c

bad = []
total = 0


def check(name, got, want):
    global total
    total += 1
    if got != want:
        bad.append((name, got, want))
    else:
        print(f"ok   {name}")


# The actual repro: both phrasings of the same question about an unaliased
# abbreviation must refuse the same way.
check('"what does TSLR mean?" refuses', c._special_scope_kind("what does TSLR mean?"), "unknown_fullform")
check('"What is TSLR used for?" refuses too (was: general_query -> LLM hallucination)',
      c._special_scope_kind("What is TSLR used for?"), "unknown_fullform")
check('"what is TSLR meant for?" refuses too', c._special_scope_kind("what is TSLR meant for?"), "unknown_fullform")
check('lowercase/no "?" still refuses', c._special_scope_kind("what is tslr used for"), "unknown_fullform")

# Known acronyms must NOT be short-circuited by the new branch -- they keep
# falling through to their own existing handler (service_code_lookup, etc.),
# same as before this fix.
for term in ("ISD", "NISD", "CAN", "CSC", "SRO", "IGRS"):
    check(f'"what is {term} used for?" is untouched (not intercepted here)',
          c._special_scope_kind(f"what is {term} used for?"), None)

# The pre-existing phrasings this guard already handled must still work.
check('"full form of IGRS" unchanged', c._special_scope_kind("full form of IGRS"), "fullform")
check('"IGRS full form" unchanged', c._special_scope_kind("IGRS full form"), "fullform")
check('"what does ISD stand for?" unchanged', c._special_scope_kind("what does ISD stand for?"), "fullform")

for name, got, want in bad:
    print(f"FAIL {name}: got {got!r}, want {want!r}")
print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
