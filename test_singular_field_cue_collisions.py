"""Regression test for the _SINGULAR_FIELD_CUES substring-collision defect
(SIS_Intent_Detection_Failure_Cases.jsonl: COLLISION_CANCEL, COLLISION_CANDIDATE,
COLLISION_SCAN, COLLISION_BORDER, COLLISION_OUTDOOR, COLLISION_SYNTAX,
COLLISION_GOVERNMENT, SW_CTX_6_MULTI_RECORDS_CANCEL).

A short cue like "can" was matched as a bare substring, so an unrelated word
that merely CONTAINS it ("cancel", "candidate", "scan") was misclassified as
a singular follow-up asking for the CAN-number field. Fixed by requiring a
few specific, non-stem cues ("can", "tax", "door", "order", "govern") to
match as whole words, while every other cue (including deliberate inflection
stems like "schedul"/"cultivab"/"alienat" and the Tamil substrings) is left
untouched -- those still need substring matching to catch their own
inflected forms, and this test asserts they still do.

No DB, no LLM. `python test_singular_field_cue_collisions.py`.
"""
import sys

from backend.services.followup_context import classify, FOLLOWUP_SINGULAR, FOLLOWUP_NONE

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


# ── Previously-colliding words must NOT be read as a field follow-up ──────
for word in ("cancel", "candidate", "scan", "border", "outdoor", "syntax", "government",
             "mandate", "candid"):
    got = classify(word)
    check(f"'{word}' is not a field follow-up", got == FOLLOWUP_NONE, f"got {got}")

# ── The genuine cues they collided with must still work on their own ──────
for word, label in [
    ("can", "can"), ("can number", "can"),
    ("tax", "tax"), ("tax rate", "tax"),
    ("door", "door"), ("door number", "door"),
    ("order", "order"), ("the order remarks", "order"),
    ("date", "date"), ("what is the date", "date"), ("dates", "date (plural)"),
]:
    got = classify(word)
    check(f"'{word}' ({label}) still classifies as a field follow-up", got == FOLLOWUP_SINGULAR, f"got {got}")

# ── Deliberate inflection stems must be untouched (not switched to exact-word) ──
for word in ("scheduled", "cultivable", "alienated", "acquisition", "assessed"):
    got = classify(word)
    check(f"'{word}' (inflection stem) still classifies as a field follow-up", got == FOLLOWUP_SINGULAR, f"got {got}")

# ── A real sentence using the word "order"/"door"/"can"/"tax" as its own word still matches ──
for sentence in ("what is the door number", "what was the order remarks", "what is the can number",
                  "what is the tax rate"):
    got = classify(sentence)
    check(f"'{sentence}' still classifies as a field follow-up", got == FOLLOWUP_SINGULAR, f"got {got}")

# ── "can" is also the ordinary modal verb ("can you...", "I can...", "you
# can...", "we can...") and must NOT be read as the CAN-number field cue
# just because it sits next to its subject pronoun. Reproduces the
# live-pipeline bug where "can you help me" / "I can see it" answered
# "Which one do you mean?" to a sentence that named no field at all.
# Reuses `_strip_can_modal`, which `field_projection`'s needle matching
# already relies on for this exact collision -- scoped the same way, to the
# pronouns an officer actually directs at the assistant or themselves
# (you/i/we), not third-person "he/she/it/they can" sentences this chatbot
# has no occasion to receive. ──
for sentence in ("can you help me", "i can see it", "you can check it", "we can proceed"):
    got = classify(sentence)
    check(f"'{sentence}' (modal 'can') is not a field follow-up", got == FOLLOWUP_NONE, f"got {got}")

# ── ...but a real CAN-number question keeps working even when phrased with "can you" ──
for sentence in ("can you get the can number", "can i get the can number"):
    got = classify(sentence)
    check(f"'{sentence}' still resolves the CAN-number field", got == FOLLOWUP_SINGULAR, f"got {got}")

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
