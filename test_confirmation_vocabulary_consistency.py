"""Regression for Phase 3.4 of the chatbot defect audit: the yes/no vocabulary
used by `_resolve_unknown_from_confirmation` (`_AFFIRMATIVE_RE`) and by
`_contentless_reply`'s pending-question-aware yes/no branch (`_YES_WORDS` /
`_NO_WORDS`) were two independently-maintained word lists with different,
non-overlapping gaps -- "seri" and "சரி" worked for one handler but not the
other, "சரிதான்" the exact reverse, "sure"/"right"/"okay" and "aamam"/"ஆமா"
were each missing from one side or the other. An officer answering the SAME
way to two different pending-confirmation shapes got inconsistent treatment
depending on which one happened to be pending.

No DB, no LLM.
"""
import sys

sys.path.insert(0, r"C:\proj\nic_internship")
from backend.services.chatbot import chat_smalltalk as cs

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


YES_WORDS_TO_CHECK = ["yes", "yeah", "correct", "sure", "right", "ok", "okay",
                      "aama", "ama", "aamam", "seri", "sari",
                      "ஆம்", "ஆமாம்", "ஆமா", "சரி", "சரிதான்"]
NO_WORDS_TO_CHECK = ["no", "nope", "illa", "illai", "இல்லை"]


def test_every_affirmative_word_is_also_a_yes_word():
    for w in YES_WORDS_TO_CHECK:
        check(f"{w!r} recognised by both _AFFIRMATIVE_RE and _YES_WORDS",
              bool(cs._AFFIRMATIVE_RE.match(w)) and w.lower() in cs._YES_WORDS)


def test_every_no_word_recognised_by_both():
    for w in NO_WORDS_TO_CHECK:
        check(f"{w!r} recognised by _NO_WORDS", w.lower() in cs._NO_WORDS)


def test_pending_question_reply_consistent_across_the_vocabulary():
    hist = [{"role": "assistant", "content": "Do you want to see ISD or NISD applications?"}]
    for w in YES_WORDS_TO_CHECK:
        resp = cs._contentless_reply("ack", "en" if all(ord(c) < 128 for c in w) else "ta", w, chat_history=hist)
        is_pending_aware = ("earlier question" in resp.lower()) or ("கைவசம்" in resp)
        check(f"'{w}' after a pending question gets the pending-aware reply, not the generic one",
              is_pending_aware, resp)


if __name__ == "__main__":
    test_every_affirmative_word_is_also_a_yes_word()
    test_every_no_word_recognised_by_both()
    test_pending_question_reply_consistent_across_the_vocabulary()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
