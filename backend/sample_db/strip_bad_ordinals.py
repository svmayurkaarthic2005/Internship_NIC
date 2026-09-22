# ==============================================================================
# Catches every "the first one" / "the second one" / "the last one" follow-up
# example whose answer names a DIFFERENT application than the one actually at
# that position in the preceding listing (the officer-reported bug: list shows
# 2022/0154/28/000000 first, "the first one" answer returns 2022/0153/28/000000,
# which was actually row 2).
#
# Verified purely from the two turns already recorded in each example -- parse
# the application-number column out of the previous assistant table (in
# on-screen order, deduplicated the same way chatbot.py's _column_values
# does), resolve which ordinal the follow-up names, and check the final
# answer's application number actually matches that position.
# ==============================================================================

import json
import re
import sys
from pathlib import Path

APP_NUMBER_RE = re.compile(r"\b\d{4}/\d{4}/\d{2}/\d{6}\b")

_ORDINAL_PATTERNS = (
    (re.compile(r"\b(?:1st|first)\b|முதல்|mudhal", re.IGNORECASE), 1),
    (re.compile(r"\b(?:2nd|second)\b|இரண்டாவது|rendaavadhu", re.IGNORECASE), 2),
    (re.compile(r"\b(?:3rd|third)\b|மூன்றாவது|moonraavadhu", re.IGNORECASE), 3),
    (re.compile(r"\b(?:4th|fourth)\b", re.IGNORECASE), 4),
    (re.compile(r"\b(?:5th|fifth)\b", re.IGNORECASE), 5),
)
_LAST_ORDINAL_RE = re.compile(r"\b(?:last|final)\b", re.IGNORECASE)


def app_numbers_in_order(text: str) -> list[str]:
    seen = []
    for m in APP_NUMBER_RE.finditer(text or ""):
        v = m.group(0).upper()
        if v not in seen:
            seen.append(v)
    return seen


def resolve_ordinal(message: str, listed: list[str]) -> str | None:
    if not listed:
        return None
    msg = (message or "").lower()
    if _LAST_ORDINAL_RE.search(msg):
        return listed[-1]
    for pattern, idx in _ORDINAL_PATTERNS:
        if pattern.search(msg) and idx <= len(listed):
            return listed[idx - 1]
    return None


def check(ex: dict) -> tuple[bool, str]:
    """Returns (is_bad, reason). is_bad=True means this example should be dropped."""
    msgs = ex.get("messages", [])
    if len(msgs) < 4:
        return False, ""
    # last user turn and the assistant turn right before it
    last_user = None
    prev_assistant = None
    final_assistant = msgs[-1]["content"] if msgs[-1]["role"] == "assistant" else None
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i]["role"] == "user" and last_user is None:
            last_user = msgs[i]["content"]
        elif msgs[i]["role"] == "assistant" and last_user is not None and prev_assistant is None:
            prev_assistant = msgs[i]["content"]
            break
    if not last_user or not prev_assistant or not final_assistant:
        return False, ""

    if _LAST_ORDINAL_RE.search(last_user.lower()):
        # The stored table text is truncated ("... N more row(s) omitted"),
        # so "the last one" can't be verified against it -- the real answer
        # resolves against the full, untruncated list. Not checkable here.
        return False, ""

    listed = app_numbers_in_order(prev_assistant)
    if len(listed) < 2:
        return False, ""  # nothing ambiguous to get wrong

    expected = resolve_ordinal(last_user, listed)
    if not expected:
        return False, ""  # this turn isn't an ordinal reference at all

    final_numbers = app_numbers_in_order(final_assistant)
    if not final_numbers:
        return False, ""  # answer names no application number -- not this bug class

    if final_numbers[0] != expected:
        return True, f"expected {expected}, answer names {final_numbers[0]}"
    return False, ""


def clean(path: Path) -> tuple[int, int]:
    kept = []
    dropped = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            is_bad, reason = check(d)
            if is_bad:
                dropped += 1
                continue
            kept.append(d)
    path.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in kept),
                     encoding="utf-8")
    return len(kept), dropped


if __name__ == "__main__":
    for name in sys.argv[1:]:
        p = Path(name)
        kept, dropped = clean(p)
        print(f"{p}: kept {kept}, dropped {dropped} wrong-ordinal-reference examples")
