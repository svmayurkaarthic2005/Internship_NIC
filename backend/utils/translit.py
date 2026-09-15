"""Deterministic, dependency-free Tamil <-> Latin transliteration for names.

Why this exists
---------------
Applicant names in `sis_chatbot_db` are stored in exactly one script -- whatever
the TAMILNILAM extract carried. Roughly 120 are in Latin ("Archana",
"A Saminathan"), 50 in Tamil ("வேல்முருகன்"), 38 mixed ("R அர்ச்சனா"). The
`applicants` table has no `name_tamil` column, and the `owners` table's
`name_tamil` is just a copy of `name`. So an officer who asks
"விண்ணப்பதாரர் பெயர் என்ன?" about an English-script name got back Latin text,
and "what is the applicant name?" about a Tamil-script name got back Tamil
script -- neither readable in the officer's working script.

This module does a phonetic script conversion so the answer can carry a
readable form ALONGSIDE the name of record. It never claims to be the name:
callers label the output "transliteration" / "ஒலிபெயர்ப்பு". Tamil is an
abugida and highly phonetic, so Tamil -> Latin is reliable for names;
Latin -> Tamil is best-effort (English spelling is not phonetic) and is always
presented as approximate.

Production notes
----------------
* Every public function is pure, side-effect free and safe to call from async
  code. None of them raise: bad input returns the input unchanged.
* `annotate_name()` is the only function callers need. It is conservative --
  it adds the transliteration only when it is confident the conversion
  produced real target-script text, and never replaces the name of record.
* No external library: Tamil romanisation schemes (ISO 15919, ITRANS) are all
  table-driven, and a name-friendly popular scheme (single letters for long
  vowels, "zh" for ழ, intervocalic softening) reads better to an SIS officer
  than strict ISO diacritics.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal, Optional

__all__ = [
    "script_of", "tamil_to_latin", "latin_to_tamil", "transliterate",
    "annotate_name", "wants_script",
]

# A name longer than this is not a name -- skip rather than churn over it.
_MAX_NAME_LEN = 120

_TA_LO, _TA_HI = "஀", "௿"

# ── Tamil -> Latin ─────────────────────────────────────────────────────────
# Independent vowels. Single letters for the long vowels -- a name reads as
# "Raman", "Velan", not "Raaman", "Vaelan".
_TA_VOWEL = {
    "அ": "a", "ஆ": "a", "இ": "i", "ஈ": "i",
    "உ": "u", "ஊ": "u", "எ": "e", "ஏ": "e",
    "ஐ": "ai", "ஒ": "o", "ஓ": "o", "ஔ": "au",
}
# Dependent vowel signs (matras). Absence of a sign == the inherent 'a'.
_TA_SIGN = {
    "ா": "a", "ி": "i", "ீ": "i", "ு": "u",
    "ூ": "u", "ெ": "e", "ே": "e", "ை": "ai",
    "ொ": "o", "ோ": "o", "ௌ": "au",
}
_TA_PULLI = "்"      # virama: strips the inherent vowel
_TA_AYTHAM = "ஃ"
# Consonants -> their sound WITHOUT the inherent vowel.
_TA_CONS = {
    "க": "k", "ங": "ng", "ச": "s", "ஜ": "j",
    "ஞ": "gn", "ட": "t", "ண": "n", "த": "th",
    "ந": "n", "ன": "n", "ப": "p", "ம": "m",
    "ய": "y", "ர": "r", "ற": "r", "ல": "l",
    "ள": "l", "ழ": "zh", "வ": "v", "ஶ": "sh",
    "ஷ": "sh", "ஸ": "s", "ஹ": "h",
}


def _has_tamil(s: str) -> bool:
    return any(_TA_LO <= c <= _TA_HI for c in s)


def _has_latin(s: str) -> bool:
    return any("a" <= c <= "z" or "A" <= c <= "Z" for c in s)


def script_of(s: Optional[str]) -> Literal["tamil", "latin", "mixed", "other"]:
    """Which script `s` is written in. "other" for empty / digits / symbols."""
    if not s or not isinstance(s, str):
        return "other"
    ta, la = _has_tamil(s), _has_latin(s)
    if ta and la:
        return "mixed"
    if ta:
        return "tamil"
    if la:
        return "latin"
    return "other"


def tamil_to_latin(s: Optional[str]) -> str:
    """Phonetic romanisation of Tamil script. Latin / punctuation pass through.
    Never raises: on unexpected input the argument is returned unchanged."""
    if not s or not isinstance(s, str):
        return s or ""
    if len(s) > _MAX_NAME_LEN:
        return s
    try:
        s = unicodedata.normalize("NFC", s)
        out: list[str] = []
        i, n = 0, len(s)
        while i < n:
            ch = s[i]
            if ch in _TA_CONS:
                base = _TA_CONS[ch]
                nxt = s[i + 1] if i + 1 < n else ""
                if nxt == _TA_PULLI:
                    out.append(base)
                    i += 2
                    continue
                if nxt in _TA_SIGN:
                    out.append(base + _TA_SIGN[nxt])
                    i += 2
                    continue
                out.append(base + "a")          # inherent vowel
                i += 1
                continue
            if ch in _TA_VOWEL:
                out.append(_TA_VOWEL[ch])
                i += 1
                continue
            if ch == _TA_AYTHAM:
                out.append("h")
                i += 1
                continue
            if ch == _TA_PULLI or ch in _TA_SIGN:
                i += 1                           # stray sign, no consonant
                continue
            out.append(ch)                       # space, ".", digit, Latin
            i += 1
        text = "".join(out)
        # Cluster tidy-ups so a name reads the way it is written on an EC:
        #   த்த -> "thth" -> "th"  (Karththik -> Karthik)
        #   ங்க -> "ngk"  -> "ng"  (Thangkavel -> Thangavel)
        for a, b in (("thth", "th"), ("chch", "ch"), ("ngk", "ng"), ("ngg", "ng"),
                     ("nn", "n"), ("ll", "l"), ("mm", "m"), ("pp", "p"),
                     ("tt", "t"), ("kk", "k"), ("ss", "s")):
            text = text.replace(a, b)
        # Intervocalic softening -- the single feature that most affects how a
        # Tamil name reads in Latin: முருகன் is "Murugan", not "Murukan".
        text = re.sub(r"(?<=[aeiou])k(?=[aeiou])", "g", text)
        text = re.sub(r"\s+", " ", text).strip()
        return " ".join(w[:1].upper() + w[1:] if w else w for w in text.split(" "))
    except Exception:  # pragma: no cover - transliteration must never break a turn
        return s


# ── Latin -> Tamil (best effort) ──────────────────────────────────────────
# Ordered longest-first so digraphs win over single letters.
_LAT_SEQ = [
    ("zh", "ழ"), ("ng", "ங"), ("ny", "ஞ"),
    ("th", "த"), ("dh", "த"), ("sh", "ஷ"), ("ch", "ச"),
    ("ph", "ப"), ("kh", "க"), ("gh", "க"),
    ("aa", "ா"), ("ee", "ீ"), ("ii", "ீ"), ("oo", "ூ"),
    ("uu", "ூ"), ("ai", "ை"), ("au", "ௌ"),
    ("a", "\x01"), ("e", "\x05"), ("i", "\x02"), ("o", "\x06"), ("u", "\x03"),
    ("k", "க"), ("g", "க"), ("c", "க"), ("j", "ஜ"),
    ("t", "ட"), ("d", "ட"), ("n", "ன"), ("p", "ப"),
    ("b", "ப"), ("m", "ம"), ("y", "ய"), ("r", "ர"),
    ("l", "ல"), ("v", "வ"), ("w", "வ"), ("s", "ஸ"),
    ("h", "ஹ"), ("f", "ப"), ("q", "க"), ("x", "க்ஸ"),
    ("z", "ஸ"),
]
# Vowel placeholders (control chars, so they never collide with real text).
_V_A, _V_I, _V_U, _V_AA, _V_E, _V_O = "\x01", "\x02", "\x03", "ா", "\x05", "\x06"
_LAT_INDEP = {  # placeholder -> independent vowel (word start / after a vowel)
    _V_A: "அ", "ா": "ஆ", _V_I: "இ", "ீ": "ஈ", _V_U: "உ", "ூ": "ஊ",
    _V_E: "எ", _V_O: "ஒ", "ை": "ஐ", "ௌ": "ஔ",
}
_LAT_DEP = {  # placeholder -> dependent sign on the previous consonant
    _V_A: "", "ா": "ா", _V_I: "ி", "ீ": "ீ", _V_U: "ு", "ூ": "ூ",
    _V_E: "ெ", _V_O: "ொ", "ை": "ை", "ௌ": "ௌ",
}
_VOWEL_PLACEHOLDERS = frozenset(_LAT_INDEP)


def latin_to_tamil(s: Optional[str]) -> str:
    """Best-effort Tamil rendering of a Latin-script name. Approximate: English
    spelling is not phonetic. Tamil / punctuation pass through unchanged.
    Never raises: on unexpected input the argument is returned unchanged."""
    if not s or not isinstance(s, str):
        return s or ""
    if len(s) > _MAX_NAME_LEN:
        return s
    try:
        out: list[str] = []
        for word in re.split(r"(\s+)", s):
            if not word.strip():
                out.append(word)
                continue
            low = word.lower()
            # English-spelling tidy-ups before the phonetic pass.
            low = low.replace("ngh", "ng").replace("tch", "ch")
            low = re.sub(r"gh\b", "g", low)
            low = re.sub(r"ph\b", "f", low)
            low = re.sub(r"y\b", "i", low)          # Mary -> mari, Nancy -> nanci
            toks: list[str] = []
            i = 0
            while i < len(low):
                for seq, rep in _LAT_SEQ:
                    if low.startswith(seq, i):
                        toks.append(rep)
                        i += len(seq)
                        break
                else:
                    toks.append(low[i])             # digit / punctuation / unknown
                    i += 1
            buf: list[str] = []
            prev_was_cons = False
            for t in toks:
                if t in _VOWEL_PLACEHOLDERS:
                    buf.append(_LAT_DEP[t] if prev_was_cons else _LAT_INDEP[t])
                    prev_was_cons = False
                elif t and _TA_LO <= t[0] <= _TA_HI:
                    if prev_was_cons:
                        buf.append(_TA_PULLI)
                    buf.append(t)
                    prev_was_cons = True
                else:
                    if prev_was_cons:
                        buf.append(_TA_PULLI)
                        prev_was_cons = False
                    buf.append(t)
            if prev_was_cons:
                buf.append(_TA_PULLI)
            out.append(unicodedata.normalize("NFC", "".join(buf)))
        return "".join(out)
    except Exception:  # pragma: no cover - transliteration must never break a turn
        return s


# ── caller-facing helpers ────────────────────────────────────────────────
def transliterate(s: Optional[str], target: Literal["tamil", "latin"]) -> str:
    """Convert `s` to `target` script if it isn't already there."""
    if not s or not isinstance(s, str):
        return s or ""
    src = script_of(s)
    if target == "latin":
        return tamil_to_latin(s) if src in ("tamil", "mixed") else s
    return latin_to_tamil(s) if src in ("latin", "mixed") else s


def _looks_converted(conv: str, original: str, target: Literal["tamil", "latin"]) -> bool:
    """A conservative confidence gate: the conversion must have produced real
    target-script text, be non-empty, differ from the input, and not have blown
    up in length."""
    if not conv or conv == original:
        return False
    if len(conv) > 3 * max(len(original), 4):
        return False
    if target == "tamil":
        return _has_tamil(conv)
    return _has_latin(conv) and not _has_tamil(conv)


def annotate_name(name: Optional[str], want_tamil: bool) -> str:
    """The name of record, plus a labelled transliteration when it is not
    already in the script the officer is working in. Never replaces the name;
    never raises.

    want_tamil: the officer's turn is Tamil / Tanglish, or they asked "in Tamil".
    """
    if not name or not isinstance(name, str):
        return name or ""
    if len(name) > _MAX_NAME_LEN:
        return name
    # Needs enough letters to be a name worth transliterating -- an initial, a
    # code fragment or "N/A" is left exactly as stored.
    if sum(1 for c in name if c.isalpha()) < 3:
        return name
    try:
        src = script_of(name)
        if want_tamil:
            if src == "latin":
                conv = latin_to_tamil(name)
                if _looks_converted(conv, name, "tamil"):
                    return f"{name} (தமிழ் ஒலிபெயர்ப்பு ~ {conv})"
            elif src == "mixed":
                conv = transliterate(name, "tamil")
                if _looks_converted(conv, name, "tamil"):
                    return f"{name} (முழுவதும் தமிழில் ~ {conv})"
            return name
        # officer working in English / Latin
        if src == "tamil":
            conv = tamil_to_latin(name)
            if _looks_converted(conv, name, "latin"):
                return f"{name} (transliteration ~ {conv})"
        elif src == "mixed":
            conv = transliterate(name, "latin")
            if _looks_converted(conv, name, "latin"):
                return f"{name} (in Latin ~ {conv})"
        return name
    except Exception:  # pragma: no cover
        return name


_WANT_EN_CUES = (
    "in english", "in roman", "english name", "name in english", "roman script",
    "romanized", "romanised", "romanize", "romanise", "transliterate to english",
    "ஆங்கிலத்தில்", "ஆங்கிலத்தில", "ஆங்கில எழுத்தில்",
)
_WANT_TA_CUES = (
    "in tamil", "tamil name", "name in tamil", "tamil script", "in tamil script",
    "தமிழில்", "தமிழில", "தமிழ் எழுத்தில்", "தமிழ் பெயர்",
)


def wants_script(message_lower: Optional[str], language: Optional[str]) -> bool:
    """True == the answer's name(s) should be rendered / annotated in Tamil.

    Driven by the turn language, but an explicit "in English" / "in Tamil"
    (or the Tamil equivalents) in the message overrides it. "in English" wins
    over "in Tamil" if -- unusually -- both appear.
    """
    m = message_lower or ""
    if any(p in m for p in _WANT_EN_CUES):
        return False
    if any(p in m for p in _WANT_TA_CUES):
        return True
    return language in ("ta", "tanglish")
