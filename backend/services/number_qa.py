"""Number questions over the officer's own rows: totals, percentages, fractions, ratios, "by type /
per month" breakdowns, "how many X and how many Y", and a bare "how many approved".

Every figure is a count of database rows handed in by the caller (all of the officer's applications,
and -- for a follow-up -- exactly the rows on screen); nothing is read from an earlier answer's text.
"""
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

from backend.services import neg_scope
from backend.utils.fuzzy import is_token_typo_match

_MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]
_APP_NO = re.compile(r"\d{4}/\d{3,4}/\d{1,3}/\d+")
_PCT = re.compile(r"\b(?:percent(?:age)?|pct|fraction|proportion|share)\b|%|சதவீத\w*|சதவிகித\w*", re.IGNORECASE)
_RATIO = re.compile(r"\bratio\b|விகிதம்", re.IGNORECASE)
_COUNT = re.compile(r"\b(?:how\s+many|count|number|total|evlo|evvalavu|hw\s+many)\b|எத்தனை|எவ்வளவு", re.IGNORECASE)
_BREAK = re.compile(r"\b(?:by|per|each|every|wise)\s*[-\s]?(type|status|channel|ward|month|year)s?\b"
                    r"|\b(type|status|channel|ward|month|year)s?[-\s]?wise\b", re.IGNORECASE)
_NOUN = re.compile(r"\b(?:applications?|aplications?|apps?|files?)\b|விண்ணப்ப", re.IGNORECASE)
_ALL_WORDS = re.compile(r"\b(?:in\s+total|total|overall|altogether|in\s+all|do\s+i\s+have|i\s+have|have\s+i|are\s+there|iruku|irukku)\b"
                        r"|உள்ளன|உள்ளது", re.IGNORECASE)
_SORT = re.compile(r"\b(?:sort(?:ed|ing)?|order(?:ed)?\s+by|arrange\w*|rank\w*|group\s+the\s+list)\b", re.IGNORECASE)
_SPLIT = re.compile(r"\b(?:breakdown|break\s+down|summary|distribution|split|grouped?)\b", re.IGNORECASE)
_PRONOUN = re.compile(r"\b(?:them|those|these|athula|ithula|idhula|athil|ithil)\b|அதில்|இதில்", re.IGNORECASE)
_LABEL = {"approved": "approved", "rejected": "rejected", "pending": "pending", "in_progress": "in progress",
          "escalated": "escalated", "CSC": "CSC", "sub_registrar": "Sub-Registrar", "citizen": "citizen"}
_LABEL_TA = dict(neg_scope._LABEL_TA)
_LABEL_TA.update({"sub_registrar": "சார்-பதிவாளர்", "CSC": "CSC", "citizen": "குடிமகன்"})
_TOTAL = re.compile(
    r"(?:please\s+)?(?:how\s+many|hw\s+many|count(?:\s+of)?|number\s+of|no\.?\s+of|total(?:\s+number\s+of)?|evlo|எத்தனை)\s+"
    r"(?:my\s+|all\s+|the\s+)?(?:applications?|aplications?|apps?|விண்ணப்பங்கள்)"
    r"(?:\s+(?:do\s+i\s+h\w{1,3}|i\s+h\w{1,3}|have\s+i\s+got|are\s+there|in\s+total|altogether|overall|iruku|irukku|"
    r"உள்ளன|உள்ளது|உள்ளனவா))?\s*[?.!]*", re.IGNORECASE)
_BARE = re.compile(
    r"(?:please\s+)?(?:how\s+many|hw\s+many|count(?:\s+of)?|number\s+of|no\.?\s+of|evlo|எத்தனை)\s+[\w\s\-,&]+?\s*[?.!]*", re.IGNORECASE)


def _status(r) -> str:
    return str(r.get("status") or "").lower().replace(" ", "_")


def _type(r) -> str:
    return str(r.get("type") or r.get("application_type") or "").upper()


def _match(r: dict, terms: List[Tuple[str, str]]) -> bool:
    """True when the row satisfies one value per kind named (values of one kind are alternatives)."""
    by: Dict[str, set] = {}
    for k, v in terms:
        by.setdefault(k, set()).add(v)
    for k, vals in by.items():
        got = {"status": _status(r), "type": _type(r), "channel": str(r.get("submission_channel") or ""),
               "ward": str(r.get("ward_number") or "").zfill(3)}[k]
        if got not in vals:
            return False
    return True


def _name(kind: str, value: str, ta: bool) -> str:
    if kind == "ward":
        return f"வார்டு {value}" if ta else f"ward {value}"
    return (_LABEL_TA if ta else _LABEL).get(value, value)


def _pct(n: int, total: int) -> str:
    return f"{n * 100 / total:.1f}%" if total else "0%"


def _uniform(rows: List[dict], asked: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """What every row on screen shares (type, channel, status), except what the question itself asks about."""
    out = []
    for kind, get in (("type", _type), ("channel", lambda r: str(r.get("submission_channel") or "")), ("status", _status)):
        vals = {get(r) for r in rows}
        if len(vals) == 1 and next(iter(vals)) and not any(k == kind for k, _v in asked):
            out.append((kind, next(iter(vals))))
    return out


def _scope_words(view: bool, total: int, ta: bool, rows: Optional[List[dict]] = None, asked=()) -> str:
    what = ""
    if view and rows:
        what = " ".join(_name(k, v, ta) for k, v in _uniform(rows, list(asked)))
    if ta:
        return f"அந்த {total} {what + ' ' if what else ''}விண்ணப்பங்களில்" if view else f"உங்கள் {total} விண்ணப்பங்களில்"
    return f"of those {total} {what + ' ' if what else ''}applications" if view else f"of your {total} applications"


def answer(message: str, rows_all: List[dict], rows_view: Optional[List[dict]], ta: bool,
           has_list: bool = False) -> Optional[str]:
    """The number answer, or None when the message is not one of these shapes."""
    text = (message or "").strip()
    if not text or len(text.split()) > 14 or _APP_NO.search(text) or neg_scope.parse(text)[0] or not rows_all:
        return None
    if re.search(r"\b(?:compare|vs|versus|difference|older|newer|longer)\b", text, re.IGNORECASE):
        return None
    if _stray_words(text):
        return None                      # "hello and how many are ISD": the rest is not this module's to drop
    terms = neg_scope._terms(text)
    named_noun = bool(_NOUN.search(text)) or any(is_token_typo_match(w, "applications") for w in re.findall(r"[a-z]{6,}", text.lower()))
    all_words = bool(_ALL_WORDS.search(text)) and not _PRONOUN.search(text)
    use_view = rows_view is not None and not named_noun and not all_words
    rows = rows_view if use_view else rows_all
    total = len(rows)
    scope = _scope_words(use_view, total, ta, rows, terms)

    if _PCT.search(text) and terms:                              # percentage / fraction
        n = sum(1 for r in rows if _match(r, terms))
        names = (" / " if ta else " or ").join(_name(k, v, ta) for k, v in terms)
        if ta:
            return f"{scope} {n} ({_pct(n, total)}) {names}."
        return f"{n} {scope} ({_pct(n, total)}) {'is' if n == 1 else 'are'} {names}."

    if _RATIO.search(text) and len({v for _k, v in terms}) >= 2:  # ratio A to B
        kind = terms[0][0]
        sides = list(dict.fromkeys(v for k, v in terms if k == kind))[:3]
        if len(sides) >= 2:
            counts = [sum(1 for r in rows if _match(r, [(kind, v)])) for v in sides]
            label = " : ".join(_name(kind, v, ta) for v in sides)
            pcts = " / ".join(_pct(c, sum(counts)) for c in counts)
            return f"{label} = {' : '.join(str(c) for c in counts)} ({pcts}) — {scope}."

    bm = _BREAK.search(text)                                     # "by type", "per month"
    if bm and not _SORT.search(text) and (_COUNT.search(text) or _SPLIT.search(text)):
        kind = (bm.group(1) or bm.group(2)).lower()
        if kind in ("month", "year"):
            cnt: Counter = Counter()
            for r in rows:
                d = str(r.get("submission_date") or "")[:10]
                if len(d) >= 7 and d[:4].isdigit():
                    cnt[d[:4] if kind == "year" else d[:7]] += 1
            if not cnt:
                return None
            items = [(k, cnt[k]) for k in sorted(cnt)]
            if kind == "month":
                items = [(f"{_MONTHS[int(k[5:7])]} {k[:4]}", n) for k, n in items]
        else:
            key = {"type": _type, "status": _status, "channel": lambda r: str(r.get("submission_channel") or ""),
                   "ward": lambda r: str(r.get("ward_number") or "").zfill(3)}[kind]
            cnt = Counter(key(r) for r in rows if key(r))
            items = [(_name(kind, k, ta), n) for k, n in cnt.most_common()]
        body = ", ".join(f"{k} {n}" for k, n in items)
        head = {"type": "வகைவாரியாக" if ta else "by type", "status": "நிலைவாரியாக" if ta else "by status",
                "channel": "வழிவாரியாக" if ta else "by channel", "ward": "வார்டுவாரியாக" if ta else "by ward",
                "month": "மாதவாரியாக" if ta else "by month", "year": "ஆண்டுவாரியாக" if ta else "by year"}[kind]
        return (f"விண்ணப்பங்கள் {head} — {body} (மொத்தம் {total})." if ta
                else f"Applications {head} — {body} (total {total}).")

    twice = len(re.findall(r"how\s+many|evlo|எத்தனை", text, re.IGNORECASE)) >= 2      # "how many NISD and how many ISD"
    if (use_view or twice) and _COUNT.search(text) and len({v for _k, v in terms}) >= 2 and len({k for k, _v in terms}) == 1:
        kind = terms[0][0]                                       # "how many NISD and how many ISD"
        vals = list(dict.fromkeys(v for _k, v in terms))
        parts = ", ".join(f"{sum(1 for r in rows if _match(r, [(kind, v)]))} {_name(kind, v, ta)}" for v in vals)
        return f"{scope[0].upper() + scope[1:]}: {parts}."

    if not terms and named_noun and not has_list and _TOTAL.fullmatch(text):   # unscoped total
        by = Counter(_status(r) for r in rows)
        order = [s for s in ("approved", "rejected", "pending", "in_progress", "escalated") if by.get(s)]
        parts = ", ".join(f"{by[s]} {_name('status', s, ta)}" for s in order)
        return (f"உங்கள் அதிகார வரம்பில் மொத்தம் {total} விண்ணப்பங்கள் — {parts}." if ta
                else f"You have {total} applications in total — {parts}.")

    if use_view and terms and _COUNT.search(text):     # "how many are rejected" / "ithula evlo rejected" over the rows on screen
        n = sum(1 for r in rows if _match(r, terms))
        names = (" / " if ta else " or ").join(_name(k, v, ta) for k, v in terms)
        return f"{scope} {n} {names}." if ta else f"{n} {scope} {'is' if n == 1 else 'are'} {names}."

    if (terms and all(k == "status" for k, _v in terms) and not has_list and not named_noun
            and _COUNT.search(text) and _BARE.fullmatch(text)):
        n = sum(1 for r in rows if _match(r, terms))                 # a bare "how many approved"
        names = (" / " if ta else " or ").join(_name(k, v, ta) for k, v in terms)
        return (f"{names} விண்ணப்பங்கள் {n} (மொத்தம் {total})." if ta
                else f"{n} {names} application{'s' if n != 1 else ''} (of {total} in total).")
    return None


# ── a bare scope word after an answer: "rejected ?", "approved?", "isd?", "rejected illaya" ──────────
_BARE_FILLER = re.compile(r"\b(?:is|it|are|they|them|those|these|ones?|the|so|also|then|ah|aa|a|illaya|illiya|illa|ya)\b|[?.!,]|ஆ|ா", re.IGNORECASE)


def bare_scope_terms(message: str) -> List[Tuple[str, str]]:
    """The one scope word (status / type / channel) a message is made of, or []."""
    text = (message or "").strip()
    if not text or len(text.split()) > 3 or _APP_NO.search(text) or neg_scope.parse(text)[0]:
        return []
    terms = neg_scope._terms(text)
    if len(terms) != 1 or terms[0][0] not in ("status", "type", "channel"):
        return []
    rest = text
    for m in re.finditer(neg_scope._TERM, text, re.IGNORECASE):
        rest = rest.replace(m.group(0), " ")
    rest = _BARE_FILLER.sub(" ", rest)
    return terms if not re.search(r"[A-Za-z\u0B80-\u0BFF]{2,}", rest) else []


_TYPE_CODE = {"ISD": "0154", "NISD": "0153", "MERGE": "0155"}


def bare_scope_answer(terms: List[Tuple[str, str]], rows_view: List[dict], ta: bool) -> Optional[str]:
    """Yes / no about the one application on screen, or a count over the list on screen."""
    if not terms or not rows_view:
        return None
    kind, val = terms[0]
    name = _name(kind, val, ta)
    if len(rows_view) == 1:
        r = rows_view[0]
        app = r.get("application_number")
        got = {"status": _status(r), "type": _type(r), "channel": str(r.get("submission_channel") or "")}[kind]
        yes = got == val
        actual = _name(kind, got, ta) if got else "-"
        if kind == "type" and got in _TYPE_CODE:
            actual += f" ({_TYPE_CODE[got]})"
        if ta:
            return (f"ஆம் — {app} {name} தான்." if yes else f"இல்லை — {app} {actual}; {name} அல்ல.")
        if kind == "channel":
            verb = "came through"
            return f"{'Yes' if yes else 'No'} — {app} {verb} {actual}" + ("." if yes else f", not {name}.")
        return f"{'Yes' if yes else 'No'} — {app} is {actual}" + ("." if yes else f", not {name}.")
    n = sum(1 for r in rows_view if _match(r, terms))
    total = len(rows_view)
    what = " ".join(_name(k, v, ta) for k, v in _uniform(rows_view, terms))
    if ta:
        return (f"அந்த {total} {what + ' ' if what else ''}விண்ணப்பங்களில் {n} {name}." if n
                else f"அந்த {total} {what + ' ' if what else ''}விண்ணப்பங்களில் {name} எதுவும் இல்லை.")
    sub = f"{what + ' ' if what else ''}applications"
    if n == 0:
        return f"None of those {total} {sub} are {name}." if kind == "status" else f"None of those {total} {sub} are {name}."
    return f"{n} of those {total} {sub} {'is' if n == 1 else 'are'} {name}. Say \"show only {name}\" to list {'it' if n == 1 else 'them'}."


_ALLOWED = frozenset("""
how many much what which who is are was were be do does did have has had there it its they them those these this that the a an of in
on at to for from by with and or nor but not no all any my me i we us you your our total count number no percent percentage pct fraction
proportion share ratio applications application aplications aplication apps app files file rows row shown show list display give tell
per each every wise only just also then now please pls kindly still yet already left remaining rest others other than more less most
least between year years month months week weeks day days by type types status statuses channel channels ward wards
evlo evvalavu enna athula ithula idhula athil ithil iruku irukku irukka ah aa um la ku kku ellam ella elam yaar edhu ethu
pending approved rejected completed complete overdue escalated progress inprogress isd nisd merge csc sro citizen registrar sub portal
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec hw mny r u hav hve
""".split())


def _stray_words(text: str) -> bool:
    """A Latin word that is neither part of a number question nor a slip of one ("hello", "xyz", "kaattu")."""
    from backend.services import qualifier_guard as _qg
    for tok in re.findall(r"(?<![\dA-Za-z])[A-Za-z][A-Za-z']*", text):
        w = tok.lower()
        if w in _ALLOWED or _APP_NO.search(w) or _qg._slip_of_known(w) and w not in ("hello",):
            continue
        return True
    return False
