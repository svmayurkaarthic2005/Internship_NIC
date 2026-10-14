"""Regression test for _AFFIRMATIVE_RE missing Tanglish affirmatives
(SIS_Intent_Detection_Failure_Cases.jsonl: SW_CTX_7_CONFIRM_GUESS_AAMA/AMA).

"aama"/"ama" are how officers type the Tamil affirmative (ஆம்/ஆமாம்) on a
Latin keyboard. _resolve_unknown_from_confirmation() already resolved "yes"
and "correct" against a pending "Did you mean the CAN number?" clarification
via _AFFIRMATIVE_RE, but that regex only recognised the Tamil SCRIPT spelling
(ஆம்/ஆமாம்), not the Tanglish one -- so "aama"/"ama" fell through to the
generic contentless acknowledgement instead of resolving the pending
question. No DB, no LLM.
"""
import sys

from backend.services.chatbot.chat_smalltalk import _resolve_unknown_from_confirmation

CAN_HISTORY = [
    {"role": "user", "content": "Check cn number"},
    {"role": "assistant", "content": "Did you mean the CAN number?"},
]
CHANNEL_HISTORY = [
    {"role": "user", "content": "applications from sto"},
    {"role": "assistant", "content": "I don't recognise \"sto\" as a source of applications. Did you mean "
                                      "Sub-Registrar (SRO)? Applications come through a CSC counter, the "
                                      "Sub-Registrar (SRO) or the citizen portal -- which of these should I "
                                      "show? (for example \"applications from CSC\")"},
]

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


for word in ("aama", "ama", "yes", "correct", "seri", "AAMA", "Ama "):
    check(f"'{word}' resolves the pending CAN confirmation",
          _resolve_unknown_from_confirmation(word, CAN_HISTORY) == "CAN number",
          repr(_resolve_unknown_from_confirmation(word, CAN_HISTORY)))

for word in ("aama", "ama"):
    check(f"'{word}' resolves the pending channel-guess confirmation",
          _resolve_unknown_from_confirmation(word, CHANNEL_HISTORY) == "applications from sub_registrar",
          repr(_resolve_unknown_from_confirmation(word, CHANNEL_HISTORY)))

# Negative: "aama" with no pending confirmation in history resolves nothing.
check("'aama' with unrelated history resolves nothing",
      _resolve_unknown_from_confirmation("aama", [{"role": "assistant", "content": "How do I log out?"}]) is None)

# Negative: a real new question that happens to start with the letters is untouched.
check("'ama' is not falsely matched inside a longer message",
      _resolve_unknown_from_confirmation("amazing work, show my pending applications", CAN_HISTORY) is None)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
