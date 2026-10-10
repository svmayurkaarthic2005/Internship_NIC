"""F-Line (service code 0178) record of ONE application, from `f_line_service_urban`:
survey / sub-division / patta, land type and extent, govt / private code, transaction
status, boundaries, sketch availability, SIS and DIS recommendation / remarks / reason /
verify date / user, scheduled date, applicant type.

Only an F-Line application has a row. Asked about another file, an F-Line-only field
(transaction status, boundaries, sketch, SIS reason ...) is answered "no F-Line record
held"; the general fields (survey, patta, extent) are left to the general handlers.
Typos, Tamil and Tanglish are understood. Sketch bodies (base64) are never read."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.utils.fuzzy import is_token_typo_match
from backend.utils.logger import get_logger

logger = get_logger(__name__)
_APP_NO = re.compile(r"\d{4}/\d{3,4}/\d{1,3}/\d+")

_TAMIL = [
    ("பரிந்துரை", " rec "), ("குறிப்புரை", " remarks "), ("குறிப்பு", " remarks "), ("காரணம்", " reason "),
    ("சரிபார்", " verify "), ("தேதி", " date "), ("பயனர்", " user "), ("எல்லை", " boundary "),
    ("ஓவியம்", " sketch "), ("வரைபடம்", " sketch "), ("பரிவர்த்தனை", " transaction "), ("நிலை", " status "),
    ("நிலத்தின் வகை", " land type "), ("நில வகை", " land type "), ("பரப்பளவு", " extent "), ("பரப்பு", " extent "),
    ("அரசு", " govt "), ("தனியார்", " private "), ("சர்வே", " survey "), ("பட்டா", " patta "),
    ("உட்பிரிவு", " subdivision "), ("எண்", " number "), ("விண்ணப்பதாரர்", " applicant "),
]
_TANGLISH = [(r"parindhurai|parinthurai", "rec"), (r"kurippu", "remarks"), (r"karanam", "reason"),
             (r"ellai", "boundary"), (r"enna|enn|evlo|yaaru|irukka|irukkaa", "")]
_EXACT = {
    "fline": "fline", "f": "f", "line": "line", "applcation": "application", "aplication": "application",
    "applcant": "applicant", "sktech": "sketch", "sketh": "sketch", "skech": "sketch", "sketchfile": "sketch",
    "boundries": "boundary", "boundaries": "boundary", "boundary": "boundary", "bondaries": "boundary",
    "stauts": "status", "satus": "status", "reccomendation": "rec", "recomendation": "rec", "recommendation": "rec",
    "reccomendations": "rec", "verifed": "verify", "verified": "verify", "verify": "verify", "verification": "verify",
    "verfied": "verify", "username": "user", "user": "user", "schedulded": "schedule", "scheduled": "schedule",
    "schedule": "schedule", "scheduling": "schedule", "survay": "survey", "servey": "survey", "typ": "type",
    "govt": "govt", "gov": "govt", "government": "govt", "pri": "private", "priv": "private", "private": "private",
    "pvt": "private", "no": "number", "num": "number", "numbr": "number", "divison": "division", "sis": "sis", "dis": "dis",
    "remark": "remarks", "remraks": "remarks", "resaon": "reason", "reasn": "reason", "extnt": "extent",
}
_VOCAB = ["recommendation", "remarks", "reason", "verify", "verification", "boundary", "sketch", "transaction", "status",
          "applicant", "survey", "extent", "application", "schedule", "private", "government"]
# "transition" is two edits from "transaction" -- well within this module's 8+-letter fuzzy
# budget -- so "final workflow transition" canonicalised to "...transaction..." and this F-Line
# module claimed an ordinary ISD workflow question, answering "no F-Line record held" for a file
# that was never asked about F-Line at all. Same trap CLAUDE.md documents for "register"/
# "registrar" and "last"/"least" elsewhere in this codebase -- a real, distinct word that happens
# to sit inside another word's typo budget must be excluded explicitly.
_NEVER_TYPO = {"transition"}


def _canon(tok: str) -> str:
    if tok in _EXACT:
        return _EXACT[tok]
    if tok in _NEVER_TYPO:
        return tok
    if tok.isascii() and tok.isalpha() and len(tok) >= 6:
        hits = {w for w in _VOCAB if is_token_typo_match(tok, w)}
        if len(hits) == 1:
            h = next(iter(hits))
            # "verfication" / "verificatio" are slips of "verification", the same column as "verify"
            return {"recommendation": "rec", "government": "govt", "verification": "verify"}.get(h, h)
    return tok


def normalise(message: str) -> str:
    t = _APP_NO.sub(" ", message or "").lower()
    t = re.sub(r"\bf[\s\-_.]*line\b", " fline ", t)
    for ta, en in _TAMIL:
        t = t.replace(ta, en)
    for rx, en in _TANGLISH:
        t = re.sub(rx, f" {en} ", t)
    t = re.sub(r"[?!.:()\"',;/&]", " ", t)
    t = re.sub(r"\bsub[\s\-_.]*d[a-z]*v[a-z]*", " subdivision ", t)
    return re.sub(r"\s+", " ", " ".join(_canon(x) for x in re.findall(r"[a-z0-9]+|[^\sa-z0-9]+", t))).strip()


SURVEY, SUBDIV, PATTA, LAND_TYPE, EXTENT, GOVT = "survey", "subdiv", "patta", "land_type", "extent", "govt_pri"
TXN, BOUND, SKETCH, SKETCH_DIS, SCHED, APPTYPE, LT_CODE = (
    "transaction_status", "total_boundary", "sketch", "sketch_dis", "schedule", "applicant_type", "land_type_code")
SPECIFIC = {TXN, BOUND, SKETCH, SKETCH_DIS, SCHED, APPTYPE, LT_CODE}


def parse(message: str) -> Dict[str, Any]:
    if "everyfield" in (message or "").lower() and "f-line" in (message or "").lower():  # "which fields are blank": every column, the blanks are picked out afterwards
        return {"keys": [SURVEY, SUBDIV, PATTA, LAND_TYPE, EXTENT, GOVT, TXN, BOUND, SKETCH, SCHED, APPTYPE, LT_CODE]
                + [(d, k) for d in ("sis", "dis") for k in ("rec", "remarks", "reason", "date", "user")], "fline": True}
    """{"keys": [...], "fline": bool}. Desk fields are ("sis"|"dis", rec|remarks|reason|date|user)."""
    t = normalise(message)
    ws = set(t.split())
    keys: List[Any] = []
    fline = "fline" in ws

    def add(k):
        if k not in keys:
            keys.append(k)

    for d in ("sis", "dis"):
        if d in ws:
            if "rec" in ws:
                add((d, "rec"))
            if "remarks" in ws:
                add((d, "remarks"))
            if "reason" in ws:
                add((d, "reason"))
            if "verify" in ws or ("date" in ws and "schedule" not in ws):
                add((d, "date"))
            if "user" in ws or "who" in ws:
                add((d, "user"))
    # "who verified it?" / "what date was it verified?" name no desk at all -- an officer
    # does not always know (or need to say) whether SIS or DIS did the verifying. Without
    # this, those questions carried no key, `answer()` returned "", and the turn fell all
    # the way through to the generic application-details card -- the answer existed in the
    # row (sis_verify_date / sis_username) but was never looked at.
    if not ({"sis", "dis"} & ws):
        if "who" in ws and ("verify" in ws or fline):
            add(("auto", "user"))
        if ("verify" in ws or ("date" in ws and "schedule" not in ws and fline)) and "who" not in ws:
            add(("auto", "date"))
    if "sketch" in ws:
        if "dis" in ws and not ({"rec", "remarks", "reason", "user"} & ws):
            add(SKETCH_DIS)
        elif "sis" in ws and "verify" in ws and "date" not in ws:
            add(SKETCH)
        elif not ({"rec", "remarks", "reason", "user"} & ws):
            add(SKETCH)
    if "boundary" in ws:
        add(BOUND)
    if "transaction" in ws:
        add(TXN)
    if "schedule" in ws:
        add(SCHED)
    if "applicant" in ws and "type" in ws:
        add(APPTYPE)
    if "land" in ws and "type" in ws and "code" in ws:
        add(LT_CODE)
    elif "land" in ws and "type" in ws:
        add(LAND_TYPE)
    if "extent" in ws or {"u1", "u2", "u3"} & ws:
        add(EXTENT)
    if "govt" in ws or "private" in ws:
        add(GOVT)
    if True:
        if "survey" in ws:
            add(SURVEY)
        if "subdivision" in ws:
            add(SUBDIV)
        if "patta" in ws:
            add(PATTA)
    return {"keys": keys, "fline": fline}


def claims(message: str) -> bool:
    p = parse(message)
    # A bare mention of F-Line ("F-Line verification date") names no desk
    # (sis/dis), so `keys` stays empty -- claim it anyway, so a non-0178 file
    # gets the "no F-Line record" refusal instead of the generic details card.
    if p["fline"]:
        return True
    # "scheduled date" alone is ordinary field-visit talk ("visits past their scheduled date"), not an F-Line column
    return bool(p["keys"]) and any(k in SPECIFIC and k != SCHED for k in p["keys"])


def claims_for_fline_app(message: str) -> bool:
    """Any field of the F-Line record, when the application IS an F-Line one."""
    return bool(parse(message)["keys"])


async def load(db, app: str) -> Optional[Dict[str, Any]]:
    from sqlalchemy import text
    sql = ("SELECT survey_no, subdiv_no, patta_no, land_type, extent_u1, extent_u2, extent_u3, applicant_type, "
           "submit_date, govt_pri, transaction_status, total_boundary, (sketch_file IS NOT NULL AND sketch_file <> '') AS has_sketch, "
           "sis_rec, sis_remarks, sis_reason, sis_verify_date, sis_username, dis_rec, dis_remarks, dis_reason, dis_verify_date, "
           "dis_username, land_type_code, (sketch_file_dis IS NOT NULL AND sketch_file_dis <> '') AS has_sketch_dis, "
           "tobe_scheduled_date FROM f_line_service_urban WHERE appl_id = :a ORDER BY row_id")
    try:
        async with db.begin_nested():
            rows = [dict(r) for r in (await db.execute(text(sql), {"a": app})).mappings().all()]
    except Exception as exc:
        logger.warning(f"fline_qa: could not read f_line_service_urban ({type(exc).__name__})")
        return None
    if not rows:
        return None
    # One row per parcel (000892 has four). Desk columns (SIS / DIS, sketch, boundaries) sit on
    # whichever row carries them, so each takes the first non-blank value; the per-parcel columns
    # stay per parcel in "_parcels" -- one row read alone dropped every other parcel's patta.
    out = {k: next((r[k] for r in rows if _v(r.get(k)) or r.get(k) is True), rows[0].get(k)) for k in rows[0]}
    out["_parcels"] = rows
    return out


def _v(x) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    return None if s in ("", "-", "None", "null") else s


def _t(ta, en, tamil):
    return tamil if ta else en


def _per_parcel(row: Dict[str, Any], col: str, nr: str) -> str:
    """One value when every parcel agrees, else each parcel's value by sub-division."""
    parcels = row.get("_parcels") or [row]
    vals = [_v(p.get(col)) for p in parcels]
    if len({v for v in vals}) <= 1:
        return vals[0] or nr
    return "; ".join(f"{v or nr} (sub-div {_v(p.get('subdiv_no')) or '-'})" for p, v in zip(parcels, vals))


def _extent(p: Dict[str, Any]) -> Optional[str]:
    try:
        ha, ar, sq = (float(p.get(c) or 0) for c in ("extent_u1", "extent_u2", "extent_u3"))
    except (TypeError, ValueError):
        return None
    return f"{ha:g} ha {ar:g} ares {sq:g} sq.m = {10000 * ha + 100 * ar + sq:g} sq.m"


def _day(x) -> Optional[str]:
    x = _v(x)
    return x[:19].replace("T", " ") if x else None


def answer(keys: List[Any], app: str, row: Optional[Dict[str, Any]], ta: bool) -> str:
    if not row:
        return _t(ta, f"No F-Line (service code 0178) record is held for application {app}. F-Line fields "
                      "(transaction status, boundaries, sketch, SIS / DIS verification) exist only for F-Line applications.",
                  f"விண்ணப்பம் {app}-க்கு F-Line (சேவை குறியீடு 0178) பதிவு இல்லை. F-Line புலங்கள் "
                  "(பரிவர்த்தனை நிலை, எல்லைகள், ஓவியம், SIS / DIS சரிபார்ப்பு) F-Line விண்ணப்பங்களுக்கு மட்டுமே உள்ளன.")
    nr = _t(ta, "not recorded", "பதிவு செய்யப்படவில்லை")
    lines: List[str] = []
    for k in keys:
        if isinstance(k, tuple):
            d, f = k
            if d == "auto":
                # No desk named -- report whichever desk actually has this field recorded,
                # SIS first (the earlier verification step), DIS only if SIS has none.
                d = "sis" if _v(row.get(f"sis_{'verify_date' if f == 'date' else 'username'}")) else "dis"
            D = d.upper()
            val = {"rec": _v(row.get(f"{d}_rec")), "remarks": _v(row.get(f"{d}_remarks")), "reason": _v(row.get(f"{d}_reason")),
                   "date": _day(row.get(f"{d}_verify_date")), "user": _v(row.get(f"{d}_username"))}[f]
            lab = {"rec": _t(ta, "recommendation", "பரிந்துரை"), "remarks": _t(ta, "remarks", "குறிப்புரை"),
                   "reason": _t(ta, "reason", "காரணம்"), "date": _t(ta, "verified on", "சரிபார்த்த தேதி"),
                   "user": _t(ta, "verified by (user)", "சரிபார்த்தவர் (பயனர்)")}[f]
            if f == "rec" and val:
                val = {"Y": _t(ta, "Recommended (Y)", "பரிந்துரைத்தது (Y)"), "N": _t(ta, "Not recommended (N)", "பரிந்துரைக்கவில்லை (N)")}.get(val.upper(), val)
            lines.append(f"{D} {lab}: {val or nr}")
        elif k == SURVEY:
            # The survey number alone does not identify a parcel in this register --
            # the sub-division number always rides along with it (shown together as
            # "27 / 0" elsewhere), so a bare "what is the survey number" is answered
            # the same way instead of naming only half of the identifier.
            lines.append(_t(ta, "Survey no: ", "சர்வே எண்: ")
                         + f"{_per_parcel(row, 'survey_no', nr)} / {_per_parcel(row, 'subdiv_no', nr)}")
        elif k == SUBDIV:
            if SURVEY in keys:
                continue  # already shown combined with the survey number above
            lines.append(_t(ta, "Sub-division no: ", "உட்பிரிவு எண்: ") + _per_parcel(row, "subdiv_no", nr))
        elif k == PATTA:
            lines.append(_t(ta, "Patta no: ", "பட்டா எண்: ") + _per_parcel(row, "patta_no", nr))
        elif k == LAND_TYPE:
            lines.append(_t(ta, "Land type code: ", "நில வகை குறியீடு: ") + _per_parcel(row, "land_type", nr))
        elif k == LT_CODE:
            lines.append(_t(ta, "Land type code (record): ", "நில வகை குறியீடு (பதிவு): ") + (_v(row.get("land_type_code")) or nr)
                         + (_t(ta, f"; land type {row['land_type']}", f"; நில வகை {row['land_type']}") if _v(row.get("land_type")) else ""))
        elif k == EXTENT:
            parcels = row.get("_parcels") or [row]
            ext = [_extent(p) for p in parcels]
            if len(parcels) > 1:
                lines.append(_t(ta, "Extent: ", "பரப்பளவு: ") + "; ".join(
                    f"{_t(ta, 'sub-div', 'உட்பிரிவு')} {_v(p.get('subdiv_no')) or '-'}: {e or nr}" for p, e in zip(parcels, ext)))
            else:
                lines.append(_t(ta, "Extent: ", "பரப்பளவு: ") + (ext[0] or nr))
        elif k == GOVT:
            g = _v(row.get("govt_pri"))
            lines.append(_t(ta, "Govt / private code: ", "அரசு / தனியார் குறியீடு: ") + (g or nr) +
                         (_t(ta, " (the register does not say which code is govt and which private)",
                             " (எந்த குறியீடு அரசு, எது தனியார் என்பது பதிவேட்டில் இல்லை)") if g else ""))
        elif k == TXN:
            lines.append(_t(ta, "Transaction status: ", "பரிவர்த்தனை நிலை: ") + (_v(row.get("transaction_status")) or nr))
        elif k == BOUND:
            lines.append(_t(ta, "Total boundaries: ", "மொத்த எல்லைகள்: ") + (_v(row.get("total_boundary")) or nr))
        elif k == SKETCH:
            lines.append(_t(ta, "Sketch file: ", "ஓவிய கோப்பு: ") + (_t(ta, "available (attached; not displayed here)", "உள்ளது (இணைக்கப்பட்டுள்ளது; இங்கு காட்டப்படாது)")
                                                                   if row.get("has_sketch") else _t(ta, "not attached", "இணைக்கப்படவில்லை")))
        elif k == SKETCH_DIS:
            lines.append(_t(ta, "DIS sketch file: ", "DIS ஓவிய கோப்பு: ") + (_t(ta, "available", "உள்ளது") if row.get("has_sketch_dis")
                                                                           else _t(ta, "not attached", "இணைக்கப்படவில்லை")))
        elif k == SCHED:
            lines.append(_t(ta, "To be scheduled on: ", "திட்டமிடப்பட்ட தேதி: ") + (_day(row.get("tobe_scheduled_date")) or nr))
        elif k == APPTYPE:
            lines.append(_t(ta, "Applicant type: ", "விண்ணப்பதாரர் வகை: ") + (_v(row.get("applicant_type")) or nr))
    if not lines:
        return ""
    return f"{lines[0]}  [{app}]" if len(lines) == 1 else f"{app} (F-Line):\n" + "\n".join("• " + l for l in lines)
