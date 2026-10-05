"""TSLR sketch records of ONE application (`tslr_sketch_urban`, service code 0156): survey / sub-division,
old patta, door, type (e-category), land type code, extent, submit time, challan / payment and the CAN.

Only a 0156 file carries these rows (22 rows, 11 files). Cross-checks against F-Line, full-field transfer, chitta
or application-information records say so when that record does not exist for a 0156 file, and compare against
the register entry (survey, sub-division, ward, block) that does. Typos, Tamil and Tanglish are understood. The
caller checks access. No LLM."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.services.crosscheck_qa import _v
from backend.utils.fuzzy import is_token_typo_match
from backend.utils.logger import get_logger

logger = get_logger(__name__)

_TAMIL = [("வரைபடம்", " sketch "), ("சர்வே", " survey "), ("உட்பிரிவு", " subdivision "), ("பட்டா", " patta "), ("கதவு எண்", " door number "),
          ("நில வகை", " land type "), ("பரப்பளவு", " extent "), ("சலான்", " challan "), ("தொகை", " amount "), ("எண்", " number "),
          ("ஒப்பிடு", " compare "), ("பொருந்து", " match "), ("வேறுபாடு", " difference "), ("தேதி", " date "), ("கருவூலம்", " treasury "),
          ("வங்கி", " bank "), ("எத்தனை", " howmany ")]
_EXACT = {
    "sktech": "sketch", "sketh": "sketch", "skech": "sketch", "survay": "survey", "servey": "survey", "divison": "division",
    "extant": "extent", "extend": "extent", "submited": "submitted", "submision": "submission", "paymant": "payment", "ammount": "amount",
    "amout": "amount", "challn": "challan", "chalan": "challan", "numbr": "number", "no": "number", "num": "number", "applcation": "application",
    "aplication": "application", "records": "record", "ecate": "ecate", "e-cate": "ecate", "mismach": "mismatch", "mismaches": "mismatch",
    "mismatches": "mismatch", "differnces": "difference", "differences": "difference", "diffrence": "difference", "consistant": "consistent",
    "conflicting": "conflict", "conflict": "conflict", "agree": "match", "matches": "match", "same": "same", "tslr": "tslr", "u1": "u1", "u2": "u2", "u3": "u3",
    "catagory": "category", "typ": "type", "category": "category", "paid": "paid", "taluk": "taluk", "town": "town", "ward": "ward", "block": "block", "district": "district",
}
_VOCAB = ["sketch", "survey", "extent", "submitted", "payment", "amount", "challan", "application", "consistent", "difference", "mismatch", "treasury"]


def _canon(tok: str) -> str:
    if tok in _EXACT:
        return _EXACT[tok]
    if tok.isascii() and tok.isalpha() and len(tok) >= 6:
        hits = {w for w in _VOCAB if is_token_typo_match(tok, w)}
        if len(hits) == 1:
            return next(iter(hits))
    return tok


def normalise(message: str) -> str:
    t = (message or "").lower()
    for ta, en in _TAMIL:
        t = t.replace(ta, en)
    t = re.sub(r"\bsub[\s\-_.]*d[a-z]*v[a-z]*", " subdivision ", t)
    t = re.sub(r"\bf[\s\-_]*line\b", " fline ", t)
    t = re.sub(r"\bfull[\s\-]*field\b", " fullfield ", t)
    t = re.sub(r"\btype[\s_]*e[\s\-_]*cate\b", " ecate ", t)
    t = re.sub(r"[?!.:()\"',;&]", " ", t).replace("/", " ")
    return re.sub(r"\s+", " ", " ".join(_canon(x) for x in re.findall(r"[a-z0-9]+|[^\sa-z0-9]+", t))).strip()


(SURVEY, SUBDIV, OLDPATTA, DOOR, LANDTYPE, ECATE, EXTENT, SUBMIT, CHALLANNO, CHALLANDATE, PAYMODE, TREASURY, AMOUNT, CAN, APPNO, GEO) = (
    "survey", "subdiv", "old_patta", "door", "land_type", "ecate", "extent", "submit", "challan_no", "challan_date", "paymode",
    "treasury", "amount", "can", "appno", "geo")


def parse(message: str) -> Optional[Dict[str, Any]]:
    if "everyfield" in (message or "").lower() and "tslr" in (message or "").lower():  # "which fields are blank": every column, the blanks are picked out afterwards
        return {"mode": "field", "keys": [SURVEY, SUBDIV, OLDPATTA, DOOR, LANDTYPE, ECATE, EXTENT, SUBMIT, CHALLANNO, CHALLANDATE,
                                         PAYMODE, TREASURY, AMOUNT, CAN], "ws": {"tslr"}}
    ws = set(normalise(message).split())
    if "tslr" not in ws:
        return None
    keys: List[str] = []

    def add(k):
        if k not in keys:
            keys.append(k)

    if "survey" in ws:
        add(SURVEY)
    if "subdivision" in ws:
        add(SUBDIV)
    if "old" in ws and "patta" in ws:
        add(OLDPATTA)
    if "door" in ws:
        add(DOOR)
    if "land" in ws and "type" in ws:
        add(LANDTYPE)
    if "ecate" in ws:
        add(ECATE)
    if ws & {"extent", "u1", "u2", "u3"}:
        add(EXTENT)
    if ws & {"submitted", "submission", "submit"}:
        add(SUBMIT)
    if "challan" in ws and ("number" in ws):
        add(CHALLANNO)
    if "challan" in ws and "date" in ws:
        add(CHALLANDATE)
    if "payment" in ws and ("mode" in ws or ws & {"date"} and False) or "paymentmode" in ws:
        add(PAYMODE)
    if "payment" in ws and PAYMODE not in keys and "challan" not in ws:
        for k in (CHALLANNO, PAYMODE, AMOUNT):
            add(k)
    if "treasury" in ws or "bank" in ws:
        add(TREASURY)
    if "amount" in ws or "paid" in ws:
        add(AMOUNT)
    if "can" in ws and ws & {"linked", "record"}:
        add(CAN)
    if "application" in ws and ws & {"linked", "number"} and "tslr" in ws and not ws & {"compare", "match", "same", "howmany", "how", "many"}:
        add(APPNO)
    if ws & {"district", "taluk", "town", "ward", "block"}:
        add(GEO)
    mode = None
    if ws & {"compare", "match", "mismatch", "difference", "consistent", "conflict", "same"}:
        mode = "compare"
    elif "howmany" in ws or ("how" in ws and "many" in ws):
        mode = "count"
    elif not keys and ws & {"any", "are"}:
        mode = "exists"
    elif keys:
        mode = "field"
    else:
        mode = "exists"
    return {"mode": mode, "keys": keys, "ws": ws}


def claims(message: str) -> bool:
    return parse(message) is not None


_SQL = ("SELECT survey_no, sub_div_no, old_patta_no, door_no, type_ecate, land_type_code, ext_u1, ext_u2, ext_u3, submit_dt, challan_no, challan_date, "
        "treasury, bank, bank_branch, paymentmode, amount, can, district_code, taluk_code, town_code, ward_code, block_code "
        "FROM tslr_sketch_urban WHERE appl_id = :a ORDER BY slno, row_id")


async def load(db, app: str) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    try:
        async with db.begin_nested():
            return [dict(r) for r in (await db.execute(text(_SQL), {"a": app})).mappings().all()]
    except Exception as exc:
        logger.warning(f"tslr_qa: could not read tslr_sketch_urban ({type(exc).__name__})")
        return []


def _t(ta, en, tamil):
    return tamil if ta else en


def _ext(r: Dict[str, Any]) -> Optional[str]:
    try:
        ha, ar, sq = (float(r.get(k) or 0) for k in ("ext_u1", "ext_u2", "ext_u3"))
    except (TypeError, ValueError):
        return None
    return f"{ha:g} ha {ar:g} ares {sq:g} sq.m = {10000 * ha + 100 * ar + sq:g} sq.m"


def _eq(a, b) -> str:
    a, b = _v(a), _v(b)
    if not a or not b:
        return "n/a"
    return "match" if str(a).strip().lower() == str(b).strip().lower() else "differs"


_SYM = {"match": "✔", "differs": "✘", "n/a": "–"}


def answer(p: Dict[str, Any], app: str, rows: List[Dict[str, Any]], reg: Optional[Dict[str, Any]], ta: bool) -> str:
    nr = _t(ta, "not recorded", "பதிவில்லை")
    if not rows:
        return _t(ta, f"No TSLR sketch record is held for application {app}. Only a TSLR sketch application (service code 0156) carries one.",
                  f"விண்ணப்பம் {app}-க்கு TSLR வரைபட பதிவு இல்லை. TSLR வரைபட விண்ணப்பத்துக்கு (சேவை குறியீடு 0156) மட்டுமே அது இருக்கும்.")
    n = len(rows)
    mode, keys, ws = p["mode"], p["keys"], p["ws"]
    if mode == "exists":
        return _t(ta, f"Yes -- {n} TSLR sketch record(s) are held for {app}.", f"ஆம் -- {app}-க்கு {n} TSLR வரைபட பதிவு(கள்) உள்ளன.")
    if mode == "count":
        return _t(ta, f"{n} TSLR sketch record(s) for {app}.", f"{app}-க்கு {n} TSLR வரைபட பதிவு(கள்).")
    L: List[str] = []
    if mode == "field":
        for i, r in enumerate(rows, 1):
            tag = f"[{i}] " if n > 1 else ""
            for k in keys:
                if k == SURVEY:
                    L.append(tag + _t(ta, "Survey no: ", "சர்வே எண்: ") + (_v(r.get("survey_no")) or nr))
                elif k == SUBDIV:
                    L.append(tag + _t(ta, "Sub-division no: ", "உட்பிரிவு எண்: ") + (_v(r.get("sub_div_no")) or nr))
                elif k == OLDPATTA:
                    L.append(tag + _t(ta, "Old patta no: ", "பழைய பட்டா எண்: ") + (_v(r.get("old_patta_no")) or nr))
                elif k == DOOR:
                    L.append(tag + _t(ta, "Door no: ", "கதவு எண்: ") + (_v(r.get("door_no")) or nr))
                elif k == LANDTYPE:
                    L.append(tag + _t(ta, "Land type code: ", "நில வகை குறியீடு: ") + (_v(r.get("land_type_code")) or nr) + _t(ta, " (a code the register does not define)", " (பதிவேடு வரையறுக்காத குறியீடு)"))
                elif k == ECATE:
                    L.append(tag + _t(ta, "Type (e-category): ", "வகை (e-category): ") + (_v(r.get("type_ecate")) or nr))
                elif k == EXTENT:
                    L.append(tag + _t(ta, "Extent (u1 ha / u2 ares / u3 sq.m): ", "பரப்பளவு (u1 ஹெ / u2 ஏர்ஸ் / u3 ச.மீ): ") +
                             f"{_v(r.get('ext_u1')) or '0'} / {_v(r.get('ext_u2')) or '0'} / {_v(r.get('ext_u3')) or '0'}  =  {_ext(r) or nr}")
                elif k == SUBMIT:
                    L.append(tag + _t(ta, "Submitted: ", "சமர்ப்பித்தது: ") + ((_v(r.get("submit_dt")) or nr)[:19]))
                elif k == CHALLANNO:
                    L.append(tag + _t(ta, "Challan no: ", "சலான் எண்: ") + (_v(r.get("challan_no")) or nr))
                elif k == CHALLANDATE:
                    L.append(tag + _t(ta, "Challan date: ", "சலான் தேதி: ") + (_v(r.get("challan_date")) or nr))
                elif k == PAYMODE:
                    L.append(tag + _t(ta, "Payment mode: ", "செலுத்தும் முறை: ") + (_v(r.get("paymentmode")) or nr))
                elif k == TREASURY:
                    L.append(tag + _t(ta, "Treasury / bank / branch: ", "கருவூலம் / வங்கி / கிளை: ") +
                             f"{_v(r.get('treasury')) or nr} / {_v(r.get('bank')) or nr} / {_v(r.get('bank_branch')) or nr}")
                elif k == AMOUNT:
                    L.append(tag + _t(ta, "Amount: ", "தொகை: ") + (_v(r.get("amount")) or nr))
                elif k == CAN:
                    L.append(tag + "CAN: " + (_v(r.get("can")) or nr))
                elif k == APPNO:
                    L.append(tag + _t(ta, "Application no: ", "விண்ணப்ப எண்: ") + app)
                elif k == GEO:
                    L.append(tag + _t(ta, "District / taluk / town / ward / block: ", "மாவட்டம் / வட்டம் / நகரம் / வார்டு / பிளாக்: ") +
                             " / ".join(_v(r.get(c)) or nr for c in ("district_code", "taluk_code", "town_code", "ward_code", "block_code")))
        L = list(dict.fromkeys(L))
        return L[0] + f"  [{app}]" if len(L) == 1 else f"{app} (TSLR sketch):\n" + "\n".join("• " + x for x in L)
    # compare
    reg = reg or {}
    for who, label in (("fline", "F-Line"), ("fullfield", "full-field patta transfer"), ("chitta", "chitta"), ("appinfo", "application-information")):
        if who in ws or (who == "fullfield" and "transfer" in ws):
            L.append(_t(ta, f"No {label} record is held for this application (a TSLR sketch file has none), so nothing can be compared with it.",
                        f"இந்த விண்ணப்பத்துக்கு {label} பதிவு இல்லை; ஒப்பிட எதுவும் இல்லை."))
    want_all = not ws & {"survey", "subdivision", "patta", "door", "extent", "challan", "application", "district", "taluk", "town", "ward", "block"}
    for i, r in enumerate(rows, 1):
        tag = f"[{i}] " if n > 1 else ""
        if want_all or ws & {"survey", "subdivision"}:
            for lab, a, b in (("Survey no", r.get("survey_no"), reg.get("survey_no")), ("Sub-division no", r.get("sub_div_no"), reg.get("subdivision_no"))):
                v = _eq(a, b)
                L.append(tag + f"{_SYM[v]} {lab} (TSLR vs register entry): {_v(a) or nr}  |  {_v(b) or nr}")
        if want_all or ws & {"district", "taluk", "town", "ward", "block"}:
            for lab, a, b in (("Ward", r.get("ward_code"), reg.get("ward_number")), ("Block", r.get("block_code"), reg.get("block_number"))):
                v = _eq(a, b)
                L.append(tag + f"{_SYM[v]} {lab} code (TSLR vs register entry): {_v(a) or nr}  |  {_v(b) or nr}")
        if "challan" in ws or "application" in ws and ws & {"number", "same", "match"}:
            if "challan" in ws:
                L.append(tag + _t(ta, f"– challan {_v(r.get('challan_no')) or nr} dated {_v(r.get('challan_date')) or nr}, {_v(r.get('paymentmode')) or nr}, amount {_v(r.get('amount')) or nr}; "
                                      "the register entry carries no challan or payment details to compare with",
                                  "– பதிவேட்டில் ஒப்பிட சலான் / கட்டண விவரம் இல்லை"))
        if want_all or ws & {"extent"}:
            L.append(tag + _t(ta, f"– extent {_ext(r) or nr}; no F-Line or other land record exists for this file to compare it with",
                              f"– பரப்பளவு {_ext(r) or nr}; ஒப்பிட வேறு நில பதிவு இல்லை"))
    if ws & {"application", "same"} and ("number" in ws or "belong" in ws or "same" in ws) and "application" in ws:
        L.append(_t(ta, f"Yes -- the TSLR record(s) carry the application id {app}, the same as the main application.", f"ஆம் -- TSLR பதிவு(கள்) விண்ணப்ப எண் {app}-ஐயே கொண்டுள்ளன."))
    L = list(dict.fromkeys(x for x in L if x))
    L.append(_t(ta, "(✔ match, ✘ differs, – not comparable)", "(✔ பொருந்தும், ✘ வேறுபடும், – ஒப்பிட இயலாது)"))
    return f"{app}:\n" + "\n".join("• " + x for x in L)
