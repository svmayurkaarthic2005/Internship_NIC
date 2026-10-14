"""Regression for Phase 5.4 of the chatbot defect audit: `_llm_greeting`'s
night-time "do not say good night" restriction applied unconditionally,
even when `kind == "farewell"` -- the officer is actually signing off, so
telling the model not to say "good night" and then rejecting any "good
night" it wrote back anyway forced the generic fixed farewell text instead
of the officer's own time-appropriate sign-off.

No DB, no LLM: this exercises the pure validator `_greeting_reply_ok`
directly with `period=None` (the farewell path) vs `period="night"` (the
opener path), the exact distinction the fix makes.
"""
import sys

sys.path.insert(0, r"C:\proj\nic_internship")
from backend.services.chatbot import chat_smalltalk as cs

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


def test_opener_at_night_still_rejects_good_night():
    # Unchanged behaviour: an OPENER must never say "good night" (it would
    # read as ending a conversation that hasn't started).
    check("'Good night! How can I help?' (opener, night) is rejected",
          not cs._greeting_reply_ok("Good night! How can I help?", "night"))


def test_farewell_at_night_may_say_good_night():
    # The fix: a FAREWELL reply is validated with period=None (what
    # `_llm_greeting` now passes when kind == "farewell"), so "good night"
    # in a genuine sign-off is no longer rejected.
    check("'Good night! Take care.' (farewell, period=None) is accepted",
          cs._greeting_reply_ok("Good night! Take care.", None))


def test_farewell_kind_routes_to_period_none_in_llm_greeting():
    import inspect
    src = inspect.getsource(cs._llm_greeting)
    check("_llm_greeting passes period=None to _greeting_reply_ok when kind is farewell",
          "None if is_farewell else period" in src, src)


def test_night_time_rule_distinguishes_farewell_from_opener():
    import inspect
    src = inspect.getsource(cs._llm_greeting)
    check("_llm_greeting's night branch is conditioned on is_farewell",
          'period == "night" and not is_farewell' in src and 'period == "night" and is_farewell' in src)


if __name__ == "__main__":
    test_opener_at_night_still_rejects_good_night()
    test_farewell_at_night_may_say_good_night()
    test_farewell_kind_routes_to_period_none_in_llm_greeting()
    test_night_time_rule_distinguishes_farewell_from_opener()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
