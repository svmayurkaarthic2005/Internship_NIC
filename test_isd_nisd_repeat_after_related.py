"""Regression test for DJ_ISD_VAR_3_AFTER_RELATED / DJ_NISD_VAR_3_AFTER_RELATED
(SIS_Intent_Detection_Failure_Cases.jsonl).

A bare "ISD" after a prior turn that already discussed ISD ("Tell me about
ISD" / "ISD service code 0154 involves subdivision.") was rewritten by
`_type_swap_followup()` into a verbatim repeat of the prior user message
("Tell me about ISD" substituted with itself -- a no-op), and THAT phrasing
never reached `service_code_lookup` because `_defn_verb` only recognised
"what is X" / "define X" / etc., not "tell me about X". The genuine swap
case ("NISD" after the same ISD history) hit the identical gap from the
other side: it rewrote correctly to "Tell me about NISD", but that phrasing
hit the same missing trigger.

Two independent fixes, verified here:
  1. `_type_swap_followup()` (chat_followups.py) no longer reproduces a
     same-type repeat verbatim; it asks "what is ISD" directly.
  2. `_defn_verb` in rag.py's `_parse_intent_core()` now also recognises
     "tell me about X" / "tell me more about X" as a definition verb --
     which is what actually fixes the NISD (genuine swap) case, and is also
     a real improvement for a fresh "tell me about ISD" with no history.

No DB, no LLM for the parse_intent / rewrite checks. The live pipeline
checks need the DB. `python test_isd_nisd_repeat_after_related.py --no-db`.
"""
import asyncio
import sys

from backend.services.rag import parse_intent
from backend.services.chatbot.chat_followups import _type_swap_followup

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


def test_defn_verb_recognises_tell_me_about():
    check("'tell me about ISD' -> service_code_lookup",
          parse_intent("tell me about ISD") == "service_code_lookup")
    check("'tell me about NISD' -> service_code_lookup",
          parse_intent("tell me about NISD") == "service_code_lookup")
    check("'tell me more about ISD' -> service_code_lookup",
          parse_intent("tell me more about ISD") == "service_code_lookup")
    # Guard clauses must still exclude the officer's own files.
    check("'tell me about my pending applications' is NOT a definition",
          parse_intent("tell me about my pending applications") != "service_code_lookup")
    check("'tell me about survey 5' is NOT a definition",
          parse_intent("tell me about survey 5") != "service_code_lookup")


async def test_type_swap_same_type_is_not_a_verbatim_repeat():
    hist = [{"role": "user", "content": "Tell me about ISD"},
            {"role": "assistant", "content": "ISD service code 0154 involves subdivision."}]
    same_type = await _type_swap_followup(None, None, hist, "ISD")
    check("same-type repeat ('ISD' after ISD) asks directly, not a verbatim re-send",
          same_type == "what is ISD", same_type)
    swap = await _type_swap_followup(None, None, hist, "NISD")
    check("genuine swap ('NISD' after ISD) rewrites to the other type",
          swap == "Tell me about NISD", swap)


def run_no_db():
    test_defn_verb_recognises_tell_me_about()
    asyncio.run(test_type_swap_same_type_is_not_a_verbatim_repeat())


if __name__ == "__main__":
    run_no_db()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
