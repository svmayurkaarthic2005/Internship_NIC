"""Regression for Phase 3.5 of the chatbot defect audit: `_contentless_reply`'s
pending-question detection used `chat_history[-1]["content"].strip().endswith("?")`,
so a genuine pending question followed by a parenthetical example or a trailing
quote mark ("...applications? (for example \"applications from CSC\")", or a
question ending in a closing quote) was NOT recognised as a pending question,
and a bare "yes"/"no" after it got the plain "nothing is pending" reply instead
of the "I don't have the earlier question in view" reply.

No DB, no LLM.
"""
import sys

sys.path.insert(0, r"C:\proj\nic_internship")
from backend.services.chatbot import chat_smalltalk as cs

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


PENDING_PHRASINGS = [
    "Do you want to see ISD or NISD applications?",
    'Do you want to see ISD or NISD applications? (for example "applications from CSC")',
    'Did you mean "applications from CSC"?',
    "இன்னும் விவரம் வேண்டுமா?",
    "இன்னும் விவரம் வேண்டுமா? (எ.கா. \"CSC விண்ணப்பங்கள்\")",
]

NOT_PENDING = [
    "Here are 3 pending applications shown above.",
    "Showing record for survey number 125.",
]


def test_pending_question_detected_despite_trailing_decoration():
    for content in PENDING_PHRASINGS:
        hist = [{"role": "assistant", "content": content}]
        resp = cs._contentless_reply("ack", "en", "yes", chat_history=hist)
        check(f"'yes' after {content!r} is treated as answering a pending question",
              "earlier question" in resp.lower() or "கைவசம்" in resp,
              resp)


def test_non_question_history_not_treated_as_pending():
    for content in NOT_PENDING:
        hist = [{"role": "assistant", "content": content}]
        resp = cs._contentless_reply("ack", "en", "yes", chat_history=hist)
        check(f"'yes' after non-question {content!r} is NOT treated as a pending answer",
              "earlier question" not in resp.lower() and "கைவசம்" not in resp,
              resp)


if __name__ == "__main__":
    test_pending_question_detected_despite_trailing_decoration()
    test_non_question_history_not_treated_as_pending()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
