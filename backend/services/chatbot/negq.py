"""Negated questions about ONE application's records ("is there no new owner record", "which fields are
not recorded", "which fields did not change", "doesn't the patta match", "who has not recommended it",
"tslr sketch illaya", "natham patta match aagala").

A record module answers a positive question, so the negation is taken out of the message (`rewrite`) and the
answer is shaped afterwards (`shape`): a field list is cut to the blank / same / different lines, a yes/no is
led by its polarity ("Correct, there is none" / "No, there is one"). Lists of applications ("not ISD",
"not rejected") belong to neg_scope and are never touched here. No LLM."""
from __future__ import annotations

import contextvars
import re
from typing import Optional, Tuple

KIND: contextvars.ContextVar = contextvars.ContextVar("negq_kind", default=None)
HYP: contextvars.ContextVar = contextvars.ContextVar("negq_hyp", default=False)
_HYP_LEAD = re.compile(
    r"^\s*(?:(?:what|how)\s+(?:will|would|could|can)?\s*(?:happens?|happen|about)?\s*if|what\s+if|suppose|assume|assuming|in\s+case|what\s+(?:will|would)\s+happen\s+if|oruvela|oruvelai|ஒருவேளை|if)\b\s*", re.IGNORECASE)
_HYP_TAIL = re.compile(
    r"\s*(?:,?\s*then\s+what|what\s+happens?|what\s+next|what\s+will\s+happen|enna\s+aagum|enna\s+aaguthu|(?:என்றால்|na|naa)\s+என்ன\s+ஆகும்|(?:na|naa)\s+enna\s+aagum|என்ன\s+ஆகும்|என்னாகும்|என்ன\s+நடக்கும்)\s*[?.]*$", re.IGNORECASE)
HYP_NOTE = ("(What-if: this assistant cannot change or predict records. This is what the register holds now.)",
            "(ஒருவேளை: பதிவுகளை மாற்றவோ முன்கூட்டி கணிக்கவோ இயலாது. பதிவேட்டில் தற்போது உள்ளது இதுவே.)")

_APP = re.compile(r"\d{4}/\d{3,4}/\d{1,3}/\d+")
_TA_NEG = "இல்லையா|இல்லயா|இல்லை|இல்லாத|படவில்லை|ஆகவில்லை|செய்யவில்லை|வில்லை"
_NEG = re.compile(
    r"\b(?:isn'?t|aren'?t|doesn'?t|don'?t|didn'?t|hasn'?t|haven'?t|wasn'?t|weren'?t|not|no|never|without|"
    r"illa(?:\s*(?:ah|ya|h))?|illaya|illai|illaiya|ilaya|illatha|illadha|aagala(?:ya)?|aagale|aagalai|"
    r"pannalana|pannalaye|pannala|panala|seyyalana|seyyala|seiyala|tharala|kidaikala|kedaikala|varala)\b|n't\b|" + _TA_NEG, re.IGNORECASE)
# words that mean another layer owns the question (lists, visits, documents, status / type / channel scopes)
_OTHER = re.compile(
    r"\b(?:applications|apps|files|visits?|scheduled|overdue|pending|approved|rejected|isd|nisd|ward|block|uploaded|chat|pdf|csv|"
    r"month|documents?|docs?|uploads?|enclosures?|certificates?)\b|விண்ணப்பங்கள்|கள\s*ஆய்வு|ஆவண", re.IGNORECASE)
_BLANK = re.compile(
    r"not\s+(?:been\s+|yet\s+)*(?:recorded|filled|entered|available|updated|captured|provided|stored|held|given|mentioned)|"
    r"\bun-?recorded\b|\bunfilled\b|\bblank\b|\bempty\b|\bmissing\b|\bnull\b|\bvacant\b|"
    r"பதிவு\s*செய்யப்படாத|பதிவில்லாத|காலியாக|காலியான|விடுபட்ட|நிரப்பப்படாத|\b(?:kaali|illatha|illadha)\b", re.IGNORECASE)
_GENERIC = re.compile(r"\b(?:fields?|details?|columns?|information|info|data|items?|values?|entries|anything|everything|something|which|what|list)\b|"
                      r"எந்த|என்னென்ன|விவரங்கள்|புலங்கள்", re.IGNORECASE)
_SAME = re.compile(
    r"(?:did\s*n'?t|did\s+not|not|never|hasn'?t|haven'?t|no)\s+(?:been\s+)?(?:change[d]?|modif\w+|updated)|\bunchanged\b|"
    r"\bno\s+change|\bnot\s+differ\w*|மாறவில்லை|மாறாத|\bmaar(?:alai|ala|alaya)\b", re.IGNORECASE)
_DIFFER = re.compile(
    r"(?:not|n't|never)\s+(?:be\s+|been\s+)?(?:the\s+)?(?:match\w*|equal|same|identical|consistent|tally\w*|similar|agree\w*)|"
    r"\bmismatch\w*|\b(?:match|same|ottu?|oththu\w*)\s+(?:aagala|aagale|illa\w*|aagalai)\b|"
    r"ஒத்துப்போகவில்லை|பொருந்தவில்லை|ஒரே\s*மாதிரி\s*இல்லை|(?:isn'?t|aren'?t|wasn'?t)\b.{0,40}\b(?:same|equal|identical|match\w*|consistent)",
    re.IGNORECASE)
_MISSREC = re.compile(
    r"(?:not|n't|yet\s+to|never)\s+(?:yet\s+)?(?:recommend\w*|submit\w*|act(?:ed)?|given\s+(?:a\s+)?recommendation)\b|"
    r"\bno\s+recommendation|\brecommend\w*\s+(?:aagala|pannala|seyyala|illa\w*)|பரிந்துரை\s*(?:செய்யப்படவில்லை|இல்லை)",
    re.IGNORECASE)
_DIFFER_TA = re.compile(r"ஒத்துப்போகவில்லை(?:யா)?|பொருந்தவில்லை(?:யா)?|ஒரே\s*மாதிரி\s*இல்லை(?:யா)?")
_EXIST = re.compile(
    r"\b(?:is|are)\s+there\s+no\b|\bisn'?t\s+there\b|\b(?:does|do)(?:n'?t|\s+not)\s+(?:it|this|the\s+\w+)\s+have\b|"
    r"\bhas\s+no\b|\bhave\s+no\b|\bwithout\b|^\s*no\s+\w|\bnot\s+(?:there|available|present|held|recorded|set|filled|given|paid|"
    r"submitted|mentioned|exist\w*|found)\b|\b(?:illa(?:\s*(?:ah|ya|h))?|illaya|illai|ilaya)\s*\??\s*$|இல்லையா|இல்லை|படவில்லை",
    re.IGNORECASE)
_MODS = (  # words that name the record, in the form the modules' own parsers read
    (r"natham", "natham"), (r"tslr|sketch\s+record", "tslr"), (r"igrs", "igrs"), (r"f[\s-]?line", "f-line"),
    (r"\bsd\b|draughtsman", "sd attachment"), (r"returned\s+owner|return\s+owner", "returned owner"),
    (r"new\s+owner|புதிய\s+உரிமையாளர்", "new owner"), (r"old\s+owner|chitta|பழைய\s+உரிமையாளர்", "old owner"),
    (r"parcel|uareg|survey\s+register", "parcel modification"),
    (r"correction|flow|desk|recommend", "correction flow"), (r"full[\s-]?field|transfer|zdt", "full field transfer"),
)
_HEADS = (("Natham chitta modification", "natham"), ("TSLR sketch", "tslr"), ("IGRS", "igrs"), ("F-Line", "f-line"),
          ("(new owners)", "new owner"), ("new vs old", "new owner"), ("SD attachment", "sd attachment"),
          ("ZDT patta", "full field transfer"), ("parcel-modification", "parcel modification"),
          ("Old-owner record", "old owner"), ("old-owner (chitta)", "old owner"), ("(returned owners)", "returned owner"))
_STRIP = (
    (r"\b(is|are)\s+there\s+no\b", r"\1 there"), (r"\bisn'?t\s+there\b", "is there"),
    (r"\b(does|do|did)\s+not\b", r"\1"), (r"\b(does|do|did)n'?t\b", r"\1"), (r"\bhas\s+no\b", "has"),
    (r"\bhave\s+no\b", "have"), (r"\b(is|are|was|were|has|have)n'?t\b", r"\1"), (r"\bwithout\b", ""),
    (r"\b(?:not|no|never)\b", ""),
    (r"\b(?:illa(?:\s*(?:ah|ya|h))?|illaya|illai|illaiya|ilaya|illatha|illadha)\b", ""),
    (_TA_NEG, ""),
)


def _hint(text: str, history) -> str:
    for rx, name in _MODS:
        if re.search(rx, text, re.IGNORECASE):
            return name
    for m in reversed(history or []):
        if isinstance(m, dict) and str(m.get("role", "")).lower() in ("assistant", "ai", "bot"):
            head = re.sub(r"<[^>]+>", " ", m.get("content") or "")
            if re.match(r"^\s*(?:No|Application)\b", head):
                return ""  # an absence answer names no record that is held
            return next((n for k, n in _HEADS if k in head), "")
    return ""


_REC = {  # word -> record; every word of an existence question must be one of these or a stop word
    "igrs": "igrs", "fline": "f-line", "tslr": "tslr", "natham": "natham", "chitta": "old owner", "uareg": "parcel modification",
    "parcel": "parcel modification", "correction": "correction flow", "flow": "correction flow", "desk": "correction flow",
    "desks": "correction flow", "attachment": "sd attachment", "attachments": "sd attachment", "transfer": "full field transfer",
    "fullfield": "full field transfer", "appinfo": "application information", "information": "application information",
}
_REC_PAIR = (("new", "owner", "new owner"), ("old", "owner", "old owner"), ("returned", "owner", "returned owner"),
             ("return", "owner", "returned owner"), ("full", "field", "full field transfer"),
             ("modification", "record", "parcel modification"), ("sketch", "record", "tslr"))
_LABEL = {"igrs": "IGRS owner", "f-line": "F-Line", "tslr": "TSLR sketch", "natham": "Natham-chitta modification",
          "old owner": "old-owner", "new owner": "new-owner", "returned owner": "returned-owner",
          "sd attachment": "SD attachment", "parcel modification": "parcel-modification",
          "correction flow": "correction / modification flow", "full field transfer": "full-field transfer",
          "application information": "application-information"}
_STOP = set("is are there a an any the does do did it this that have has had record records exist exists existing held available "
            "present recorded for of in file application app here also yet isnt arent doesnt dont didnt hasnt havent not no "
            "owner owners field sd line f sketch modification details detail data entry entries row rows illa illaya illai irukka "
            "irukkaa iruka undaa ah ya h".split())
_EXCUE = set("there have has exist exists held available present any record records illa illaya illai irukka irukkaa iruka".split())


def _existence(bare: str) -> Optional[str]:
    low = re.sub(r"f[\s-]?line", "fline", bare.lower())
    low = re.sub(r"n't", "nt", low)
    toks = re.findall(r"[a-z0-9]+", low)
    if not toks or len(toks) > 14 or not (set(toks) & _EXCUE):
        return None
    hint = None
    for a, b, h in _REC_PAIR:
        if a in toks and b in toks:
            hint = h
    if not hint and "sd" in toks and ("attachment" in toks or "attachments" in toks):
        hint = "sd attachment"
    for t in toks:
        if t in _REC:
            hint = hint or _REC[t]
    if not hint:
        return None
    known = set(_REC) | _STOP | {w for a, b, _ in _REC_PAIR for w in (a, b)}
    return hint if all(t in known for t in toks) else None


def _rewrite_neg(message: str, chat_history=None) -> Optional[str]:
    """The positive question for a negated record question, or None. Sets KIND for `shape`."""
    KIND.set(None)
    # "door no" is a number, not a negation: "no" counts only at the start or after there / has / have / with / any / and / is / are
    text = re.sub(r"(?<!^)(?<!there )(?<!has )(?<!have )(?<!with )(?<!any )(?<!and )(?<!is )(?<!are )(?<!, )\bno\b(?!\w)(?=[\s?.]*$|\s+(?:and|or|of|in|for|from|as|\W))", " number", message or "", flags=re.IGNORECASE)
    if not _APP.search(text) and not any(
            _APP.search((m.get("content") or "") if isinstance(m, dict) else str(m)) for m in (chat_history or [])[-8:]):
        return None  # a record question is about an application: one in the message or the conversation
    bare = _APP.sub(" ", text)
    app = _APP.search(text)
    tail = f" {app.group(0)}" if app else ""
    if not _OTHER.search(bare):
        rec = _existence(bare)
        if rec:
            neg = bool(_NEG.search(bare))
            KIND.set(("existrec_neg:" if neg else "existrec:") + rec)
            word = "complete details" if rec in ("natham", "new owner", "old owner", "sd attachment", "returned owner") else "everyfield"
            return f"{rec} {word}" + tail
    if not _NEG.search(text) and not _BLANK.search(text):
        return None
    if _OTHER.search(bare) or len(text.split()) > 22:
        return None
    kind = None
    if _MISSREC.search(bare):
        KIND.set("missrec")
        return "missing recommendation" + tail
    if _BLANK.search(bare) and _GENERIC.search(bare) and not re.search(r"\b(?:mobile|phone|aadhaar|aadhar)\b", bare, re.I):
        hint = _hint(bare, chat_history)
        KIND.set("blank")
        word = "complete details" if hint in ("natham", "new owner", "old owner", "sd attachment", "returned owner") else "everyfield"
        return f"{hint or 'application information'} {word}" + tail
    if _SAME.search(bare) and re.search(r"\b(?:fields?|details?|which|what)\b|எந்த", bare, re.I):
        hint = _hint(bare, chat_history)
        if not hint:
            return None
        KIND.set("same")
        return f"{hint} which fields changed{tail}".strip()
    if _DIFFER.search(bare):
        kind = "differ"
        out = _DIFFER_TA.sub(" match ", bare)
        if not re.search("same|differ|compar|change", out, re.IGNORECASE):
            out += " compare"
    elif _NEG.search(bare):
        if not _EXIST.search(bare) or not bare.isascii():
            return None  # Tamil-script negations are read by the modules themselves
        kind = "exist"
        out = bare
    else:
        return None
    for rx, rep in _STRIP:
        out = re.sub(rx, rep, out, flags=re.IGNORECASE)
    out = re.sub(r"\s+", " ", out).strip(" ?")
    if not out:
        return None
    KIND.set(kind)
    return (out + tail).strip()


_MARK = re.compile(r"^[\s•\-\[\]\d,a-z\- ]{0,40}?([✔✘◐–])")
_NR = re.compile(r"not recorded|blank|not held|பதிவு செய்யப்படவில்லை|பதிவில்லை|held only for", re.IGNORECASE)
_ABSENT = re.compile(r"not recorded|not held|\bno\b[^.\n]{0,50}(?:record|attachment|row)|is none|இல்லை|பதிவு செய்யப்படவில்லை|பதிவில்லை", re.IGNORECASE)


def _absent(text: str) -> bool:
    """A module's own "no such record" sentence: it opens the answer and stands alone."""
    lines = (text or "").strip().splitlines() or [""]
    return bool(re.match(r"^(?:No|There is no)\b", lines[0])) or (
        len(lines) == 1 and bool(re.search("இல்லை|பதிவில்லை", lines[0])))


def _record_present(text: str, kind: str, ta: bool) -> str:
    """"Is there an X record?": the module's own absence sentence, or one line saying it is held."""
    rec = kind.split(":", 1)[1]
    if _absent(text):
        return text
    app = _APP.search(text)
    where = f" {app.group(0)}" if app else ""
    neg = kind.startswith("existrec_neg") and not HYP.get()
    if ta:
        return f"{'இல்லை, ' if neg else 'ஆம், '}{_LABEL[rec]} பதிவு{where}-க்கு உள்ளது."
    return f"{'No, ' if neg else 'Yes, '}the {_LABEL[rec]} record is held for{where or ' this application'}."


def shape(text: str, ta: bool) -> str:
    kind = KIND.get()
    if not kind or not text:
        return text
    if kind.startswith("existrec"):
        out = _record_present(text, kind, ta)
        if out is text and kind.startswith("existrec_neg") and not HYP.get():
            return ("ஆம், " if ta else "Correct. ") + out
        return out
    lines = text.split("\n")
    if kind == "blank":
        keep = []
        for l in lines:
            if not _NR.search(l):
                continue
            if "; " in l:  # a row printed as "field: value; field: value; ...": keep the blank ones
                parts = [x.strip() for x in l.split("; ")]
                head = parts[0].split("] ", 1)
                prefix = head[0] + "] " if len(head) == 2 else ""
                parts[0] = head[-1]
                l = prefix + "; ".join(x for x in parts if _NR.search(x))
            keep.append(l)
        head = [l for l in lines[:1] if not _NR.search(l)]
        if not keep and _ABSENT.search(text[:300]):
            return text
        if not keep:
            return ("இந்த பதிவில் காலியான புலம் எதுவும் இல்லை." if ta else "No field of this record is blank.")
        return "\n".join(head + keep)
    if kind in ("same", "differ"):
        want = "✔" if kind == "same" else "✘◐"
        if kind == "differ" and sum(1 for l in lines if _MARK.match(l)) < 2:
            return text
        out, hit = [], 0
        for l in lines:
            m = _MARK.match(l)
            if m:
                if m.group(1) in want:
                    out.append(l)
                    hit += 1
            elif l is lines[0]:
                out.append(l)
        if kind == "differ" and not hit:
            return text
        if kind == "same" and not hit:
            notes = [l for l in lines[1:] if not _MARK.match(l) and not l.strip().startswith("(✔")]
            return ("எந்த புலமும் ஒரே மாதிரி இல்லை." if ta else "None of the fields stayed the same.") + "\n" + "\n".join(notes)
        return "\n".join(out)
    return text


def lead(text: str, ta: bool) -> str:
    """Polarity line for a negated yes / no (the answer itself is the positive one)."""
    if HYP.get():
        return HYP_NOTE[1 if ta else 0] + "\n" + text
    kind = KIND.get()
    head = (text or "")[:400]
    if kind == "exist" and re.match(r"^(?:Yes|No|ஆம்|இல்லை)(?!\w)", head):
        if head.startswith("Yes") or head.startswith("ஆம்"):
            return re.sub(r"^(?:Yes|ஆம்)\s*(?:--|—|,)?\s*", "இல்லை, " if ta else "No, ", text, count=1)
        return ("ஆம். " if ta else "Correct. ") + text
    if kind == "exist":
        gone = _absent(text) or bool(re.search(r"not recorded|not held|பதிவு செய்யப்படவில்லை", head[:200]))
        return (("ஆம், பதிவில்லை. " if gone else "இல்லை, பதிவாகியுள்ளது. ") if ta else ("Correct, nothing is recorded. " if gone else "No, it is recorded. ")) + text
    if kind == "differ":
        if ("✘" in text or "◐" in text) and "✔" not in text:
            return ("ஆம், ஒத்துப்போகவில்லை.\n" if ta else "Correct, they do not match.\n") + text
        if "✔" in text and "✘" not in text:
            return ("இல்லை, ஒத்துப்போகிறது.\n" if ta else "No, they match.\n") + text
    return text


def rewrite(message: str, chat_history=None) -> Optional[str]:
    """`_rewrite_neg` after a "what if ..." wrapper is taken off (HYP is set, so the answer says it is the record as it
    stands, not a prediction). With no application in sight a what-if is left to the ordinary handlers."""
    HYP.set(False)
    text = message or ""
    m = _HYP_LEAD.match(text) or re.match(r"^\s*ஒருவேளை\s*", text)
    if not m and _HYP_TAIL.search(text):  # "sis recommend pannalana enna aagum": the tail alone says it is a what-if
        m = re.match(r"^", text)
    if not m or len(text.split()) < 3 or not (_APP.search(text) or any(
            _APP.search((h.get("content") or "") if isinstance(h, dict) else str(h)) for h in (chat_history or [])[-8:])):
        return _rewrite_neg(message, chat_history)
    core = _HYP_TAIL.sub("", text[m.end():]).strip(" ?.,")
    HYP.set(True)
    out = _rewrite_neg(core, chat_history)
    return out if out is not None else core
