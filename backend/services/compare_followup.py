"""Comparative follow-ups: "compare them", "which is older?", "which is higher?", "by how much",
"compare with NISD" -- messages that name no sides. The sides are the two applications on
screen, or the last two things (types, statuses, channels, wards) the officer asked about; the
message is re-asked as a self-contained comparison ("compare ISD and NISD"), which the
comparison handler answers from the register. With no two sides to be found it becomes a
bare "compare", which asks what to compare -- never a guess.
"""
import re
from typing import List, Optional

from backend.services import neg_scope
from backend.utils.fuzzy import is_token_typo_match

_APP_NO = re.compile(r"\d{4}/\d{3,4}/\d{1,3}/\d+")
_T = "[஀-௿]*"
_ASPECT = {
    "older": rf"older|oldest|earlier|pazhaya\w*|pazhusu|பழைய{_T}|முந்தைய{_T}",
    "newer": rf"newer|newest|later|latest|puthu\w*|புதிய{_T}|பின்னைய{_T}",
    "longer": rf"longer|slower|took\s+longer|நீண்ட{_T}|அதிக\s*நேரம்",
    "faster": rf"faster|quicker|shorter",
    "more": rf"more|higher|bigger|larger|greater|highest|most|adhig\w*|athig\w*|perusu|அதிக{_T}|பெரிய{_T}",
    "less": rf"less|fewer|lower|smaller|kammi|kuraiv\w*|குறைவ{_T}|சிறிய{_T}",
}
_ANY = "|".join(f"(?:{v})" for v in _ASPECT.values())
_TRIGGER = re.compile(
    rf"^(?:(?:and|so|ok|okay|now)\s+)?(?:"
    rf"(?:please\s+)?compare(?:\s+(?:them|these|those|both|it|the\s+two|the\s+2|the\s+results?))?(?:\s+(?:two|both))?"
    rf"|(?:which|what|edhu|ethu|எது)\s+(?:one\s+)?(?:is\s+|was\s+|are\s+)?(?:{_ANY})(?:\s+(?:of\s+)?(?:them|these|those|the\s+two|both))?"
    rf"|(?:by\s+)?how\s+much(?:\s+(?:more|less|higher|lower|older|newer))?(?:\s+is\s+it)?"
    rf"|(?:what(?:'s|\s+is)\s+)?the\s+difference(?:\s+between\s+(?:them|the\s+two|both))?"
    rf"|(?:evlo|எவ்வளவு)\s+(?:difference|வித்தியாசம்|adhigam|அதிகம்)"
    rf"|(?:rendaiyum|இரண்டையும்)?\s*(?:compare\s+pannu|oppidu|ஒப்பிடு(?:ங்கள்)?)"
    rf")\s*[?.!]*$", re.IGNORECASE)
_WITH = re.compile(
    r"^(?:and\s+)?(?:please\s+)?(?:compare|check|show|what\s+about)?\s*(?:it\s+|this\s+|that\s+|them\s+)?"
    r"(?:with|to|against|vs\.?|versus)\s+(.+?)\s*[?.!]*$", re.IGNORECASE)


def _aspect(message: str) -> Optional[str]:
    for name, pat in _ASPECT.items():
        if re.search(rf"(?<!\w)(?:{pat})", message, re.IGNORECASE):
            return name
    return None


def _values(message: str) -> List:
    if neg_scope.parse(message)[0]:
        return []                       # an exclusion is not a side
    return neg_scope._terms(message)


def _sides(message: str, history: List[str]):
    """The last two distinct values of one kind, oldest first."""
    seq = []                            # chronological (kind, value)
    for h in history[-6:]:
        seq.extend(_values(h))
    seq.extend(_values(message))
    if not seq:
        return None
    kind = seq[-1][0]
    out: List[str] = []
    for k, v in reversed(seq):
        if k == kind and v not in out:
            out.append(v)
        if len(out) == 2:
            break
    if len(out) < 2:
        return None
    return kind, list(reversed(out))


def _phrase(kind: str, value: str) -> str:
    if kind == "ward":
        return f"ward {value}"
    if kind == "channel":
        return {"sub_registrar": "Sub Registrar"}.get(value, value)
    return value.replace("_", " ")


def _terms_only(chunk: str) -> bool:
    """"NISD", "the Sub Registrar ones" -- the chunk is nothing but a scope."""
    rest = re.sub(r"\b(?:the|ones?|applications?|apps?|files|from|via|in|of)\b", " ", chunk, flags=re.IGNORECASE)
    for m in re.finditer(neg_scope._TERM, rest, re.IGNORECASE):
        rest = rest.replace(m.group(0), " ")
    return bool(neg_scope._terms(chunk)) and not re.search(r"[A-Za-z஀-௿]{2,}", rest)


_WORDS = ("which", "older", "newer", "faster", "slower", "longer", "higher", "lower", "more", "less", "compare",
          "difference", "bigger", "larger", "smaller", "much")
_SLIPS = {"mor": "more", "mre": "less", "les": "less", "whch": "which", "wich": "which", "whic": "which",
          "compair": "compare", "comapre": "compare"}


def _fix(text: str) -> str:
    out = []
    for tok in re.findall(r"[A-Za-z]+|[^A-Za-z]+", text):
        low = tok.lower()
        if low in _SLIPS:
            out.append(_SLIPS[low])
        elif low.isalpha() and low not in _WORDS and len(low) >= 4:
            out.append(next((w for w in _WORDS if is_token_typo_match(low, w)), tok))
        else:
            out.append(tok)
    return "".join(out)


def rewrite(message: str, app_numbers: List[str], history: List[str]) -> Optional[str]:
    """The self-contained comparison to ask instead of `message`, or None if it is no such
    follow-up. An unresolvable one returns "compare" (which asks what to compare)."""
    text = _fix((message or "").strip())
    if not text or len(text.split()) > 9 or _APP_NO.search(text):
        return None
    m_with = _WITH.match(text)
    trigger = _TRIGGER.match(text)
    if not (trigger or (m_with and _terms_only(m_with.group(1)))):
        return None
    aspect = _aspect(text)
    nums = list(dict.fromkeys(app_numbers or []))
    if len(nums) == 2 and not m_with:
        a, b = nums
        if aspect in ("older", "newer"):
            return f"which is {aspect} {a} or {b}"
        if aspect in ("longer", "faster"):
            return f"which took {'longer' if aspect == 'longer' else 'less time'} {a} or {b}"
        return f"compare {a} and {b}"
    sides = _sides(text, history)
    if sides:
        kind, (x, y) = sides
        x, y = _phrase(kind, x), _phrase(kind, y)
        if aspect in ("longer", "faster") and kind in ("type", "status", "channel"):
            return f"do {x} take longer than {y}" if aspect == "longer" else f"do {y} take longer than {x}"
        return f"compare {x} and {y}"
    if len(nums) > 2 and aspect in ("older", "newer"):
        return None                     # "which is oldest of the list" has its own handler
    return "compare"


_SWAP = re.compile(
    r"^(?:and|also|then|what\s+about|how\s+about)\s+(?:the\s+)?"
    r"(rejected|approved|pending|in[\s-]?progress|escalated|completed)(?:\s+ones)?\s*[?.!]*$", re.IGNORECASE)


def swap_status(message: str, history: List[str]) -> Optional[str]:
    """"how many approved" then "and rejected" -> "how many rejected": the last question that
    named a status, asked about the other one."""
    m = _SWAP.match((message or "").strip())
    if not m or not history:
        return None
    new = m.group(1)
    for prev in reversed(history):
        if neg_scope.parse(prev)[0] or len(prev.split()) > 14 or _SWAP.match(prev.strip()):
            continue
        hit = next((t for t in re.finditer(neg_scope._TERM, prev, re.IGNORECASE)
                    if (neg_scope._classify(t.group(0)) or ("", ""))[0] == "status"), None)
        if hit:
            return prev[:hit.start()] + new + prev[hit.end():]
        return None
    return None
