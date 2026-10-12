"""Desk recommendations, remarks and submit dates of ONE application, from the
`correction_modification_flow` table (SIS -> ZDT -> SD -> DIS -> Tahsildar -> RDO ->
Collector/DRO -> COSS -> CLA), plus its reason code, govt / private change flags and
SIS / DIS certificates.

That table carries only the correction / modification service codes (0158-0169); an
ISD / NISD file has no row in it, and the answer says so instead of inventing a
chain. Typos, Tamil and Tanglish are understood. Every value is read from the row;
blank is "not recorded". The caller checks access first. No LLM."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.utils.fuzzy import is_token_typo_match
from backend.utils.logger import get_logger

logger = get_logger(__name__)
class _Found:
    """A match-like holder: `.group(0)` is the application number."""
    def __init__(self, value):
        self.value = value

    def group(self, i=0):
        return self.value


class _LastAppNo:
    """`search` returns the LAST application number in a text: an answer ends with "[<its application>]",
    while a value quoted earlier in it (a merge id, say) is some other file."""
    _rx = re.compile(r"\d{4}/\d{3,4}/\d{1,3}/\d+")

    _tail = re.compile(r"\[(\d{4}/\d{3,4}/\d{1,3}/\d+)\]\s*$")
    _head = re.compile(r"^\W*(\d{4}/\d{3,4}/\d{1,3}/\d+)")

    def search(self, text):
        """The application an answer is ABOUT: its trailing "[number]", else the number it opens with,
        else the last one named (other numbers inside it are values or other files)."""
        text = re.sub(r"<[^>]+>", " ", text or "")  # an answer stored as markup opens with its tags
        for rx in (self._tail, self._head):
            m = rx.search(text.strip())
            if m:
                return _Found(m.group(1))
        ms = list(self._rx.finditer(text))
        if len({m.group(0) for m in ms}) >= 3:
            return None  # a listing: it is about none of its rows in particular
        return _Found(ms[0].group(0)) if ms else None  # the subject comes first; later numbers are quoted values

    def findall(self, text):
        return self._rx.findall(text or "")

    def sub(self, repl, text):
        return self._rx.sub(repl, text or "")


_APP_NO = _LastAppNo()

# desk key -> (label, column prefix, order in the chain)
DESKS = {
    "sis": ("SIS", "sis"), "zdt": ("ZDT", "zdt"), "sd": ("SD", "sd"), "dis": ("DIS", "dis"),
    "thl": ("Tahsildar", "thl"), "rdo": ("RDO", "rdo"), "collr_dro": ("Collector / DRO", "collr_dro"),
    "dro": ("DRO", "dro"), "coss": ("COSS", "coss"), "cla": ("CLA", "cla"),
}
_CHAIN = ["sis", "zdt", "sd", "dis", "thl", "rdo", "collr_dro", "dro", "coss", "cla"]

_TAMIL = [
    ("பரிந்துரை", " rec "), ("குறிப்புரை", " remarks "), ("குறிப்பு", " remarks "), ("கருத்து", " remarks "),
    ("சமர்ப்பி", " submit "), ("தேதி", " date "), ("காரணக் குறியீடு", " reason code "), ("காரணம்", " reason "),
    ("சான்றிதழ்", " certificate "), ("திருத்தம்", " correction "), ("மாற்றம்", " change "),
    ("மாற்றக்", " change "), ("கொடி", " flag "), ("தனியார்", " priv "), ("அரசு", " govt "), ("ஒப்பிடு", " compare "), ("நிலை", " status "),
    ("வேறுபாடு", " different "), ("வேலைப்பாய்வு", " workflow "), ("விண்ணப்ப", " application "),
    ("அதிகாரி", " officers "), ("முதலில்", " first "), ("கடைசியாக", " last "), ("விடுபட்ட", " missing "),
    ("டஹசில்தார்", " thl "), ("தாசில்தார்", " thl "),
]
_TANGLISH = [
    (r"parindhurai|parinthurai|parindurai", "rec"), (r"kurippurai|kurippu", "remarks"),
    (r"samarpi\w*", "submit"), (r"thethi|theti", "date"), (r"karanam", "reason"),
    (r"saandrithaz\w*|sandrithaz\w*", "certificate"), (r"thiruthham", "correction"),
    (r"maatram|matram", "change"), (r"oppidu|oppidhu", "compare"), (r"verupaadu", "different"),
    (r"enna|enn|evlo|yaar|yaaru|irukka|irukkaa", ""),
]
_STOP = frozenset("""what is the a an for of on in to me tell give show please pls can you which whose
there any this that its it be are was were do does did also with about from by as at has have had get
need want know see how whether if then both and or by their they them those these""".split())

_VOCAB = ["recommendation", "remarks", "submission", "certificate", "correction", "modification",
          "workflow", "compare", "different", "missing", "officers", "private", "status", "application",
          "tahsildar", "collector", "stage", "stages", "complete", "reason", "change", "flags"]


def _canon(tok: str) -> str:
    t = tok
    if t in ("sis", "zdt", "sd", "dis", "rdo", "dro", "coss", "cla"):
        return t
    if t in ("thl", "tahsildar", "tahsildhar", "tehsildar", "thasildar"):
        return "thl"
    if t in ("collr", "collector", "colr", "collecter", "collctr", "coll"):
        return "collr"
    if t in ("govt", "gov", "government", "govnt"):
        return "govt"
    if t in ("priv", "private", "pvt", "privte", "privat"):
        return "priv"
    if t in ("cert", "certs", "certificates", "certificate", "certifcate", "certficate", "certificte"):
        return "certificate"
    if re.match(r"^rec+o?m+[eao]?n?d?[a-z]*$", t) and len(t) >= 5 or re.match(r"^reco?m+e?nd", t):
        return "rec"
    if t.startswith(("remark", "remrk", "rmark", "comment")):
        return "remarks"
    if t.startswith(("submi", "submis", "sbmi")):
        return "submit"
    if t in ("dt", "date", "dates", "when", "time"):
        return "date"
    if t in ("stauts", "satus", "statuss", "status"):
        return "status"
    if t in ("diffrent", "differnt", "diferent", "different", "differ", "difference", "differences", "differs"):
        return "different"
    if t in ("modifcation", "modification", "modificaton", "modifications", "modify", "correction",
             "corrections", "corection", "corrction"):
        return "correction"
    if t in ("flag", "flags"):
        return "flag"
    if t in ("reccomendations", "recomendations"):
        return "rec"
    if t.isascii() and t.isalpha() and len(t) >= 6:
        hits = {w for w in _VOCAB if is_token_typo_match(t, w)}
        if len(hits) == 1:
            h = next(iter(hits))
            return {"recommendation": "rec", "submission": "submit", "modification": "correction",
                    "tahsildar": "thl", "collector": "collr", "stages": "stage", "officers": "officers"}.get(h, h)
    return t


def normalise(message: str) -> str:
    t = _APP_NO.sub(" ", message or "").lower()
    for ta, en in _TAMIL:
        t = t.replace(ta, en)
    for rx, en in _TANGLISH:
        t = re.sub(rx, f" {en} ", t)
    t = re.sub(r"[?!.:()\"',;/&]", " ", t)
    toks = [_canon(x) for x in re.findall(r"[a-z0-9]+|[^\sa-z0-9]+", t)]
    return re.sub(r"\s+", " ", " ".join(toks)).strip()


def _desks(toks: List[str]) -> List[str]:
    out: List[str] = []
    for i, w in enumerate(toks):
        k = None
        if w == "collr":
            k = "collr_dro"
        elif w == "dro":
            k = "dro" if not (i and toks[i - 1] == "collr") else None
        elif w in DESKS:
            k = w
        if k and k not in out:
            out.append(k)
    return out


class Q:
    """What a message asks of the flow record."""
    def __init__(self):
        self.desks: List[str] = []
        self.fields: List[str] = []     # rec / remarks / date
        self.mode: Optional[str] = None  # compare / given / workflow / order / missing / remarks_all
        self.extra: List[str] = []      # reason / govt / priv / certificate

    def __bool__(self):
        return bool(self.fields or self.mode or self.extra)


def parse(message: str) -> Q:
    if "everyfield" in (message or "").lower() and "correction flow" in (message or "").lower():  # "which fields are blank": every column, the blanks are picked out afterwards
        q = Q()
        q.desks = list(DESKS)
        q.fields = ["rec", "remarks", "date"]
        return q
    q = Q()
    t = normalise(message)
    toks = t.split()
    ws = set(toks)
    q.desks = _desks(toks)
    desk_words = ws & ({"sis", "zdt", "sd", "dis", "rdo", "dro", "coss", "cla", "thl", "collr"})

    if "reason" in ws and ("code" in ws or "correction" in ws):
        q.extra.append("reason")
    if "govt" in ws and ("change" in ws or "flag" in ws):
        q.extra.append("govt")
    if "priv" in ws and ("change" in ws or "flag" in ws):
        q.extra.append("priv")
    if "change" in ws and "flag" in ws and not ({"govt", "priv"} & ws):
        q.extra += ["govt", "priv"]
    if "certificate" in ws and (desk_words & {"sis", "dis"} or "issued" in ws):
        q.extra.append("certificate")
    if "reason" in ws and "correction" in ws and "reason" not in q.extra:
        q.extra.append("reason")

    has_rec = "rec" in ws or ("status" in ws and desk_words)
    has_rem = "remarks" in ws
    has_date = "submit" in ws or ("date" in ws and desk_words)
    if has_rec:
        q.fields.append("rec")
    if has_rem:
        q.fields.append("remarks")
    if has_date:
        q.fields.append("date")

    if ws & {"compare"} or ("same" in ws and "different" in ws) or "different" in ws and has_rec:
        if has_rec or desk_words:
            q.mode = "compare"
    elif "workflow" in ws and (desk_words or has_rec):
        q.mode = "workflow"
    elif ("first" in ws or "last" in ws) and ("submit" in ws) and ({"stage", "stages"} & ws or desk_words):
        q.mode = "order"
    elif "missing" in ws and (has_rec or has_rem):
        q.mode = "missing"
    elif ws & {"who", "which", "whom"} and has_rec and not desk_words and not ws & {"not", "missing", "never"}:
        q.mode = "given"  # "who recommended it", "which desks recommended it"
    elif "officers" in ws and (has_rec or has_rem):
        q.mode = "remarks_all" if (has_rem and not has_rec) else "given"
    # a field with no desk and no mode ("when was it submitted", "remarks") is the general handlers'
    if not q.mode and not q.desks and q.fields and not q.extra:
        q.fields = []
    if not q.mode and q.desks and not q.fields and not q.extra:
        q.desks = []
    if q.extra and not q.mode:
        q.desks = q.desks if (q.fields or "certificate" in q.extra) else []
    return q


def claims(message: str) -> bool:
    return bool(parse(message))


# ── data ────────────────────────────────────────────────────────────────────
async def load(db, app: str) -> Optional[Dict[str, Any]]:
    from sqlalchemy import text
    try:
        async with db.begin_nested():
            r = (await db.execute(text("SELECT * FROM correction_modification_flow WHERE appl_id = :a LIMIT 1"),
                                  {"a": app})).mappings().first()
            return dict(r) if r else None
    except Exception as exc:
        logger.warning(f"cmflow_qa: could not read the flow table ({type(exc).__name__})")
        return None


def _v(x) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    return None if s in ("", "-") or s.lower() in ("none", "null") else s


def _t(ta: bool, en: str, tamil: str) -> str:
    return tamil if ta else en


def _nr(ta: bool) -> str:
    return _t(ta, "not recorded", "பதிவு செய்யப்படவில்லை")


def _rec(x, ta: bool) -> str:
    x = _v(x)
    if not x:
        return _nr(ta)
    return {"Y": _t(ta, "Recommended (Y)", "பரிந்துரைத்தது (Y)"),
            "N": _t(ta, "Not recommended (N)", "பரிந்துரைக்கவில்லை (N)")}.get(x.upper(), x)


def _date(x, ta: bool) -> str:
    x = _v(x)
    return x[:10] if x else _nr(ta)


def _flag(x, ta: bool) -> str:
    x = _v(x)
    return {"Y": _t(ta, "Yes (Y)", "ஆம் (Y)"), "N": _t(ta, "No (N)", "இல்லை (N)")}.get((x or "").upper(), x or _nr(ta))


def _label(d: str) -> str:
    return DESKS[d][0]


def _no_row(app: str, kind: Dict[str, Any], ta: bool) -> str:
    code = (kind or {}).get("service_code") or ""
    return _t(
        ta,
        f"No correction / modification workflow record is held for application {app}"
        + (f" (service code {code})" if code else "")
        + ". The desk-wise recommendation chain (SIS, ZDT, SD, DIS, Tahsildar, RDO, Collector/DRO, COSS, CLA) "
          "is recorded only for the correction / modification services (0158-0169), so nothing about it is "
          "recorded for this file.",
        f"விண்ணப்பம் {app}"
        + (f" (சேவை குறியீடு {code})" if code else "")
        + "-க்கு திருத்தம் / மாற்றம் பணிப்பாய்வு பதிவு இல்லை. மேசை வாரியான பரிந்துரை வரிசை "
          "(SIS, ZDT, SD, DIS, தாசில்தார், RDO, Collector/DRO, COSS, CLA) 0158-0169 சேவைகளுக்கு மட்டுமே "
          "பதிவாகும்; இந்த கோப்புக்கு எதுவும் பதிவில்லை.")


def _desk_line(d: str, row, fields: List[str], ta: bool) -> str:
    p = DESKS[d][1]
    parts = []
    if "rec" in fields:
        parts.append(_t(ta, "recommendation", "பரிந்துரை") + ": " + _rec(row.get(f"{p}_rec"), ta))
    if "remarks" in fields:
        parts.append(_t(ta, "remarks", "குறிப்புரை") + ": " + (_v(row.get(f"{p}_remarks")) or _nr(ta)))
    if "date" in fields:
        parts.append(_t(ta, "submitted on", "சமர்ப்பித்த தேதி") + ": " + _date(row.get(f"{p}_submit_date"), ta))
    return f"{_label(d)} — " + "; ".join(parts)


def _held(row, d: str) -> bool:
    p = DESKS[d][1]
    return any(_v(row.get(f"{p}_{s}")) for s in ("rec", "remarks", "submit_date"))


def answer(message: str, q: Q, app: str, row: Optional[Dict[str, Any]], kind: Dict[str, Any], ta: bool) -> str:
    if not row:
        return _no_row(app, kind, ta)
    head = f"{app}: "
    lines: List[str] = []
    if q.mode == "compare":
        ds = q.desks or [d for d in _CHAIN if _held(row, d)]
        recs = {d: _rec(row.get(f"{DESKS[d][1]}_rec"), ta) for d in ds}
        lines += [f"{_label(d)}: {recs[d]}" for d in ds]
        known = {v for v in recs.values() if v != _nr(ta)}
        if len(known) <= 1 and known:
            lines.append(_t(ta, "All the desks that recorded a recommendation gave the same one.",
                            "பரிந்துரை பதிவு செய்த அனைத்து மேசைகளும் ஒரே பரிந்துரையை அளித்தன."))
        elif len(known) > 1:
            lines.append(_t(ta, "The desks differ in their recommendations.", "மேசைகளின் பரிந்துரைகள் வேறுபடுகின்றன."))
        miss = [_label(d) for d in ds if recs[d] == _nr(ta)]
        if miss:
            lines.append(_t(ta, "No recommendation recorded by: " + ", ".join(miss),
                            "பரிந்துரை பதிவில்லை: " + ", ".join(miss)))
    elif q.mode in ("given", "remarks_all"):
        f = ["rec", "remarks"] if q.mode == "given" else ["remarks"]
        for d in _CHAIN:
            p = DESKS[d][1]
            if (q.mode == "given" and _v(row.get(f"{p}_rec"))) or (q.mode == "remarks_all" and _v(row.get(f"{p}_remarks"))):
                lines.append(_desk_line(d, row, f, ta))
        if not lines:
            lines.append(_t(ta, "No desk has recorded one.", "எந்த மேசையும் பதிவு செய்யவில்லை."))
    elif q.mode == "workflow":
        ds = _CHAIN
        if q.desks and len(q.desks) >= 2:
            a, b = _CHAIN.index(q.desks[0]), _CHAIN.index(q.desks[-1])
            ds = _CHAIN[min(a, b):max(a, b) + 1]
        for d in ds:
            lines.append(_desk_line(d, row, ["rec", "date"], ta) if _held(row, d)
                         else f"{_label(d)} — " + _t(ta, "no action recorded", "நடவடிக்கை பதிவில்லை"))
    elif q.mode == "order":
        dated = sorted(((_date(row.get(f"{DESKS[d][1]}_submit_date"), ta), d) for d in _CHAIN
                        if _v(row.get(f"{DESKS[d][1]}_submit_date"))))
        if not dated:
            lines.append(_t(ta, "No desk submit date is recorded.", "எந்த மேசையின் சமர்ப்பிப்பு தேதியும் பதிவில்லை."))
        else:
            lines.append(_t(ta, f"First submitted: {_label(dated[0][1])} on {dated[0][0]}",
                            f"முதலில் சமர்ப்பித்தது: {_label(dated[0][1])} — {dated[0][0]}"))
            lines.append(_t(ta, f"Last submitted: {_label(dated[-1][1])} on {dated[-1][0]}",
                            f"கடைசியாக சமர்ப்பித்தது: {_label(dated[-1][1])} — {dated[-1][0]}"))
    elif q.mode == "missing":
        miss = [_label(d) for d in _CHAIN if _held(row, d) and not _v(row.get(f"{DESKS[d][1]}_rec"))]
        none_ = [_label(d) for d in _CHAIN if not _held(row, d)]
        lines.append(_t(ta, "Desks that acted but recorded no recommendation: " + (", ".join(miss) or "none"),
                        "நடவடிக்கை எடுத்தும் பரிந்துரை பதிவில்லாத மேசைகள்: " + (", ".join(miss) or "இல்லை")))
        lines.append(_t(ta, "Desks with no entry at all: " + (", ".join(none_) or "none"),
                        "எந்த பதிவும் இல்லாத மேசைகள்: " + (", ".join(none_) or "இல்லை")))
    else:
        for d in (q.desks if q.fields else []):
            lines.append(_desk_line(d, row, q.fields, ta))
    for e in q.extra:
        if e == "reason":
            rc = _v(row.get("reason_code"))
            lines.append(_t(ta, "Reason code: ", "காரணக் குறியீடு: ") + (rc or _nr(ta)) +
                         (_t(ta, " (the register does not define what this code means)", " (குறியீட்டின் பொருள் பதிவேட்டில் இல்லை)") if rc else ""))
        elif e == "govt":
            lines.append(_t(ta, "Govt change flag: ", "அரசு மாற்றக் கொடி: ") + _flag(row.get("govt_change_flag"), ta))
        elif e == "priv":
            lines.append(_t(ta, "Private change flag: ", "தனியார் மாற்றக் கொடி: ") + _flag(row.get("priv_change_flag"), ta))
        elif e == "certificate":
            ds = [d for d in q.desks if d in ("sis", "dis")] or ["sis", "dis"]
            for d in ds:
                lines.append(_t(ta, f"Certificate by {_label(d)}: ", f"{_label(d)} சான்றிதழ்: ") + _flag(row.get(f"certificate_{d}"), ta))
    if not lines:
        return ""
    if len(lines) == 1:
        return f"{lines[0]}  [{app}]"
    return head + "\n" + "\n".join("• " + l for l in lines)
