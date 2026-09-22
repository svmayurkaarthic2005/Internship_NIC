""""You are wrong" / "check again" / "are you sure" / "there should be 40".

A pushback is neither a question nor a correction the assistant can take on faith: the
register is the only authority, so the earlier question is asked again and the answer is
compared with the one given. If it is the same, that is said (and no figure is changed to
please the officer); if it differs, that is said too. Nothing here reads the register --
chatbot.py runs the question and passes the two answers to `note()`.
"""
import re
from typing import Optional

from backend.utils.fuzzy import is_token_typo_match

_BASE = ("wrong", "incorrect", "again", "check", "recheck", "verify", "mistake")
_ASK = re.compile(
    r"\b(?:show|list|display|how\s+many|what\s+is|which|why|where|status|count|details?|kaattu|kaatu|kaami|sollu|evlo)\b|"
    r"காட்டு|எத்தனை|\d{4}/\d{3,4}/", re.IGNORECASE)
_PUSH = re.compile(
    r"\b(?:wrong|incorrect|mistake|mistaken|recheck|re-check|check\s+again|verify\s+again|try\s+again|look\s+again|"
    r"are\s+you\s+sure|you\s+sure|not\s+(?:right|correct|true|accurate)|"
    r"that\s+can'?t\s+be\s+(?:right|true)|doesn'?t\s+look\s+right|"
    r"thapp?u|thavar?u|thappa|sariyilla|sariyalla|thirumba\s+(?:paa?r\w*|check)|marubadiyum|marupadiyum|"
    r"mari\s+check|mendum\s+check|urudhi(?:yaa|ya)?)\b"
    r"|\b(?:really|sure)\s*\?"
    r"|தவறு|தப்பு|சரியில்லை|சரியல்ல|மீண்டும்\s*(?:சரி\s*பார்|பார்|சோதி)|உறுதி(?:யா)?|திரும்ப\s*(?:பார்|சரி)",
    re.IGNORECASE)
_ASSERT = re.compile(
    r"\b(?:there\s+should\s+be|i\s+think\s+there\s+(?:are|is)|i\s+thought\s+there|it\s+should\s+be|must\s+be|"
    r"more\s+than\s+that|less\s+than\s+that|fewer\s+than\s+that|not\s+that\s+many|nearly\s+\d+)\b"
    r"|இன்னும்\s*அதிக|இதைவிட", re.IGNORECASE)
_FIX = {"rong": "wrong", "wrng": "wrong", "wrog": "wrong", "wron": "wrong", "chek": "check", "chk": "check",
        "agn": "again", "agan": "again", "agin": "again", "aagain": "again", "cheque": "check"}


def _fix(text: str) -> str:
    out = []
    for tok in re.findall(r"[A-Za-z']+|[^A-Za-z']+", text):
        low = tok.lower()
        if low in _FIX:
            out.append(_FIX[low])
        elif low.isalpha() and len(low) >= 5:
            out.append(next((b for b in _BASE if is_token_typo_match(low, b)), tok))
        else:
            out.append(tok)
    return "".join(out)


def is_pushback(message: str) -> bool:
    """A short message that only disputes or asks to re-check the last answer."""
    text = (message or "").strip()
    if not text or len(text.split()) > 14 or _ASK.search(text):
        return False
    fixed = _fix(text)
    return bool(_PUSH.search(fixed) or _ASSERT.search(fixed))


def asserted_figure(message: str) -> Optional[str]:
    m = re.search(r"\b(\d{1,5})\b", message or "")
    return m.group(1) if m and _ASSERT.search(_fix(message)) else None


_NUM = re.compile(r"\d{4}/\d{3,4}/\d{1,3}/\d+")


def _signature(html: str):
    text = re.sub(r"<[^>]+>", " ", html or "")
    apps = sorted(set(_NUM.findall(text)))
    nums = re.findall(r"\b\d+\b", _NUM.sub(" ", text))[:4]
    return apps, nums


def same_answer(before: str, after: str) -> bool:
    return _signature(before) == _signature(after)


def nothing_to_check(ta: bool) -> str:
    return ("சரிபார்க்க முந்தைய பதில் இந்த உரையாடலில் இல்லை. கேள்வியைக் கேளுங்கள் — பதிவேட்டில் இருந்து பார்த்துச் சொல்கிறேன்."
            if ta else
            "There is no earlier answer in this conversation to check. Ask the question and I'll read it from the register.")


def note(question: str, same: Optional[bool], ta: bool, figure: Optional[str] = None) -> str:
    """The sentence placed above the re-run answer. `same` is None when there was no earlier
    answer to compare with."""
    import html
    q = html.escape(question)
    if ta:
        head = (f"உங்கள் கேள்வியை (\"{q}\") பதிவேட்டில் மீண்டும் சரிபார்த்தேன். "
                + ("பதில் முன்பு சொன்னதே — கீழே உள்ளது. எந்தப் பகுதி தவறு என்று தோன்றுகிறதோ அதைச் சொல்லுங்கள். " if same else
                   "இப்போது பதிவேடு கீழ்க்கண்டதைக் காட்டுகிறது; முந்தைய பதிலிலிருந்து வேறுபடுகிறது. " if same is False else
                   "பதிவேடு காட்டுவது கீழே. "))
        if figure:
            head += (f"{figure} என்ற எண்ணை நான் பதிவேட்டில் இருந்து மட்டுமே சொல்ல முடியும்; வேறு எண் எதிர்பார்த்தால் "
                     "எந்த வடிகட்டி (நிலை, வகை, வார்டு, காலம்) வேறுபடுகிறது என்று சொல்லுங்கள். ")
        return f"<div class='recheck-note'>{head}</div>"
    head = (f"I asked the register again: \"{q}\". "
            + ("The answer is the same as before, shown below. If a particular part looks wrong, tell me which one. " if same else
               "The register now gives the answer below, which differs from my earlier one. " if same is False else
               "The register gives the answer below. "))
    if figure:
        head += (f"I can only report what the register holds, so I have not changed the figure to {figure}; "
                 "if you expect a different one, tell me which filter differs (status, type, ward or period). ")
    return f"<div class='recheck-note'>{head}</div>"
