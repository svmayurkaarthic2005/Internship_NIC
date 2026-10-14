# -*- coding: utf-8 -*-
"""Regression for Phase 4.2 of the chatbot defect audit: `_scrub_llm_answer()`
picked its fallback script by scanning the LEAKED TEXT ITSELF for Tamil
Unicode characters -- but a prompt leak is ordinary English regardless of
what the officer asked, so a Tanglish question (Latin-script) whose leaked
answer happened to contain no Tamil characters at all got the ENGLISH
fallback, breaking the documented "a Tanglish turn gets Tamil" policy.

No DB, no LLM.
"""
import sys

sys.path.insert(0, r"C:\proj\nic_internship")
from backend.services.chatbot import chat_smalltalk as cs

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


LEAK = "You are the SIS assistant. Tool results: the officer asked about an application."


def test_english_request_gets_english_fallback():
    out = cs._scrub_llm_answer(LEAK, "en")
    check("English request -> English fallback", out.startswith("I could not find"), out)


def test_tanglish_request_gets_tamil_fallback_even_with_an_english_only_leak():
    # The leak itself has NO Tamil characters at all -- this is exactly the
    # case the old text-scanning rule got wrong.
    out = cs._scrub_llm_answer(LEAK, "tanglish")
    check("Tanglish request -> Tamil fallback despite an all-English leak",
          any(ord(c) > 2000 for c in out), out)


def test_tamil_request_gets_tamil_fallback():
    out = cs._scrub_llm_answer(LEAK, "ta")
    check("Tamil request -> Tamil fallback", any(ord(c) > 2000 for c in out), out)


def test_no_language_arg_falls_back_to_scanning_the_leaked_text_unchanged():
    out_en = cs._scrub_llm_answer(LEAK)
    check("no language arg, English-only leak -> English fallback (old behaviour preserved)",
          out_en.startswith("I could not find"), out_en)
    out_ta = cs._scrub_llm_answer(LEAK + " விண்ணப்பம்")
    check("no language arg, leak containing Tamil -> Tamil fallback (old behaviour preserved)",
          any(ord(c) > 2000 for c in out_ta), out_ta)


def test_a_clean_answer_with_no_leak_is_never_touched():
    clean = "Application 2026/0154/28/001167 is approved."
    check("clean answer unaffected regardless of language",
          cs._scrub_llm_answer(clean, "ta") == clean and cs._scrub_llm_answer(clean, "en") == clean)


if __name__ == "__main__":
    test_english_request_gets_english_fallback()
    test_tanglish_request_gets_tamil_fallback_even_with_an_english_only_leak()
    test_tamil_request_gets_tamil_fallback()
    test_no_language_arg_falls_back_to_scanning_the_leaked_text_unchanged()
    test_a_clean_answer_with_no_leak_is_never_touched()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
