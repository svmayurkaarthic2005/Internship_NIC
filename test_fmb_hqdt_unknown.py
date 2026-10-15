"""Regression: "FMB" and "HQDT" are no longer treated as recognised SIS
terms with a specific definition. HQDT had no backing glossary/full-form
entry at all despite being marked "known", so "what is HQDT?" fell through
to the LLM uncaught -- the exact hallucination risk the honest-refusal path
exists to prevent. FMB's specific "not part of this register" explanation
was removed by request, so it now gets the same generic refusal as any
other unrecognised term. ZDT itself (a genuinely defined, in-use term) is
confirmed unaffected.

No DB, no LLM.
"""
import sys

sys.path.insert(0, r"C:\proj\nic_internship")
from backend.services.chatbot import chat_smalltalk as cs

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


def test_hqdt_is_flagged_unknown():
    check("'what is HQDT?' is flagged unknown (was falling through to the LLM)",
          cs._unknown_term("what is HQDT?") == "hqdt")


def test_fmb_is_flagged_unknown():
    check("'what is FMB?' is flagged unknown (specific explanation removed by request)",
          cs._unknown_term("what is FMB?") == "fmb")


def test_hqdt_fullform_still_refused_as_before():
    from backend.services.chatbot import chat_shared as csh
    check("'full form of HQDT' still correctly refused (unchanged)",
          csh._unknown_fullform("full form of HQDT") == "hqdt")


def test_fmb_fullform_now_refused_too():
    from backend.services.chatbot import chat_shared as csh
    check("'full form of FMB' now also refused (was a specific expansion before)",
          csh._unknown_fullform("full form of FMB") == "fmb")


def test_zdt_is_unaffected():
    check("'what is ZDT?' still resolves to the glossary entry (unaffected)",
          cs._workflow_steps_topic("what is ZDT?", []) == "GLOSS:zdt")
    from backend.services.chatbot import chat_shared as csh
    check("'full form of ZDT' still resolves (unaffected)",
          csh._fullform_term("full form of ZDT") == "zdt")


def test_bare_fmb_hqdt_not_misread_as_gibberish():
    # The separate gibberish/out-of-scope exemptions are untouched -- a bare
    # "FMB"/"HQDT" message must not be dismissed as keyboard-mash either.
    check("'FMB' alone is not gibberish", not cs._is_gibberish("FMB"))
    check("'HQDT' alone is not gibberish", not cs._is_gibberish("HQDT"))


if __name__ == "__main__":
    test_hqdt_is_flagged_unknown()
    test_fmb_is_flagged_unknown()
    test_hqdt_fullform_still_refused_as_before()
    test_fmb_fullform_now_refused_too()
    test_zdt_is_unaffected()
    test_bare_fmb_hqdt_not_misread_as_gibberish()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
