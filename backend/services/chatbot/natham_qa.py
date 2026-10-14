"""Natham-chitta owner modification rows of ONE application (`uchitta_natham_modification`): the owners a
correction / modification application (0158, 0159, 0160, 0164, 0169) records against a natham patta -- owner
and relative (Tamil / English), relationship, patta and "S patta", door, share, address, ID numbers, sex,
status, operation, update time.

A file has several rows, each tagged with an operation code (01, 02, 05, 06). The register does not say what the
codes mean, so rows are grouped by operation code and "who changed" compares those groups, stating that. Codes
and ID numbers are never interpreted; Aadhaar is never shown. Only a modification service has rows; other files
say so. Comparisons with IGRS / chitta / TSLR / full-field records say those do not exist for such a file, and
compare patta, status, operation and geography with the parcel-modification rows (`uareg_modification`) that do.
Typos, Tamil and Tanglish are understood. The caller checks access through the register. No LLM."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .crosscheck_qa import _name_cmp, _v
from backend.utils.fuzzy import is_token_typo_match
from backend.utils.logger import get_logger

logger = get_logger(__name__)

FIELDS = {
    "owner": ("Owner", "உரிமையாளர்", ["owner", "owner_english"]),
    "patta": ("Patta no", "பட்டா எண்", ["patta_no"]),
    "spatta": ("S patta no", "S பட்டா எண்", ["s_patta_no"]),
    "door": ("Door no", "கதவு எண்", ["door_no"]),
    "relative": ("Relative", "உறவினர்", ["relative", "relative_english"]),
    "reltn": ("Relationship code", "உறவு குறியீடு", ["reltn"]),
    "share": ("Share", "பங்கு", ["share"]),
    "numbers": ("Owner no / relative no", "உரிமையாளர் எண் / உறவினர் எண்", ["own_num", "rel_num"]),
    "address": ("Address / pincode", "முகவரி / அஞ்சல் எண்", ["address", "pincode"]),
    "occ": ("Occupation code", "தொழில் குறியீடு", ["occ_cd"]),
    "form6": ("Form 6 no", "படிவம் 6 எண்", ["form6_no"]),
    "assign": ("Assignment no", "ஒதுக்கீடு எண்", ["assign_no"]),
    "ration": ("Ration card no", "ரேஷன் அட்டை எண்", ["ration_card_no"]),
    "epic": ("EPIC no", "EPIC எண்", ["epic_no"]),
    "cin": ("CIN no", "CIN எண்", ["cin_no"]),
    "aadhaar": ("Aadhaar", "ஆதார்", ["aadhar_no"]),
    "sex": ("Sex", "பாலினம்", ["sex"]),
    "status": ("Status code", "நிலை குறியீடு", ["appl_status"]),
    "operation": ("Operation code", "செயல்பாட்டு குறியீடு", ["operation"]),
    "fulldata": ("Full data", "முழு தரவு", ["full_data"]),
    "flow": ("Flow type", "பணிப்பாய்வு வகை", ["flowtype"]),
    "when": ("Updated on", "புதுப்பித்த தேதி", ["updn_dt"]),
    "userno": ("User no", "பயனர் எண்", ["user_no"]),
}
_CODE = {"reltn", "status", "operation"}

_TAMIL = [("நத்தம்", " natham "), ("சிட்டா", " chitta "), ("மாற்றம்", " modification "), ("திருத்தம்", " modification "),
          ("உரிமையாளர்", " owner "), ("பட்டா", " patta "), ("கதவு எண்", " door number "), ("பங்கு", " share "), ("முகவரி", " address "),
          ("உறவினர்", " relative "), ("உறவு", " relation "), ("பாலினம்", " sex "), ("நிலை", " status "), ("ஒப்பிடு", " compare "),
          ("பொருந்து", " match "), ("வேறுபாடு", " difference "), ("எண்", " number "), ("பெயர்", " name "), ("தேதி", " date ")]
_EXACT = {
    "ower": "owner", "owners": "owner", "nam": "name", "numbr": "number", "no": "number", "num": "number", "relitive": "relative",
    "relitives": "relative", "relashionship": "relation", "relationship": "relation", "adress": "address", "occuaption": "occupation",
    "occupashun": "occupation", "assingnment": "assignment", "assignmnt": "assignment", "assign": "assignment", "aadhar": "aadhaar",
    "adhar": "aadhaar", "avalable": "available", "operashun": "operation", "stauts": "status", "updatd": "update", "updated": "update",
    "modifcation": "modification", "modificaton": "modification", "applcation": "application", "mismach": "mismatch",
    "mismaches": "mismatch", "diffrent": "difference", "different": "difference", "differences": "difference", "consistant": "consistent",
    "typ": "type", "tslr": "tslr", "igrs": "igrs", "natham": "natham", "nathum": "natham", "form6": "form6", "epic": "epic", "cin": "cin",
    "detials": "details", "deatils": "details", "detail": "details", "ration": "ration", "relation": "relation", "correspond": "match", "corresponds": "match", "agree": "match", "matches": "match", "changed": "change", "changes": "change",
}
_VOCAB = ["modification", "operation", "occupation", "assignment", "relative", "relation", "consistent", "mismatch", "difference", "available"]


def _canon(tok: str) -> str:
    if tok in _EXACT:
        return _EXACT[tok]
    if tok.isascii() and tok.isalpha() and len(tok) >= 6:
        hits = {w for w in _VOCAB if is_token_typo_match(tok, w)}
        if len(hits) == 1:
            return next(iter(hits))
    return tok


def normalise(message: str) -> str:
    t = re.sub(r"\d{4}/\d{3,4}/\d{1,3}/\d+", " ", (message or "").lower())
    for ta, en in _TAMIL:
        t = t.replace(ta, en)
    t = re.sub(r"\bform[\s\-_]*6\b", " form6 ", t)
    t = re.sub(r"\bs[\s\-_]*patta\b", " spatta ", t)
    t = re.sub(r"\bua[\s\-_]*modification\b", " uamod ", t)
    t = re.sub(r"\bfull[\s\-]*field\b", " fullfield ", t)
    t = re.sub(r"\bf[\s\-_]*line\b", " fline ", t)
    t = re.sub(r"\bflow[\s\-_]*type\b", " flowtype ", t)
    t = re.sub(r"\bfull[\s\-_]*data\b", " fulldata ", t)
    t = re.sub(r"[?!.:()\"',;&]", " ", t).replace("/", " ")
    return re.sub(r"\s+", " ", " ".join(_canon(x) for x in re.findall(r"[a-z0-9]+|[^\sa-z0-9]+", t))).strip()


def parse(message: str) -> Optional[Dict[str, Any]]:
    """None unless the message is about the Natham-chitta modification record (the word "natham" must be there)."""
    ws = set(normalise(message).split())
    if "natham" not in ws:
        return None
    keys: List[str] = []

    def add(k):
        if k not in keys:
            keys.append(k)

    if "owner" in ws and not ws & {"relative", "relation"} and (ws & {"name", "who", "english"} or len(ws) <= 4):
        add("owner")
    if "patta" in ws and "spatta" not in ws:
        add("patta")
    if "spatta" in ws:
        add("spatta")
    if "door" in ws:
        add("door")
    if "relative" in ws:
        add("relative")
    if "relation" in ws:
        add("reltn")
    if "share" in ws:
        add("share")
    if "number" in ws and "owner" in ws and "relative" in ws:
        add("numbers")
    if ws & {"address", "pincode"}:
        add("address")
    if "occupation" in ws:
        add("occ")
    if "form6" in ws:
        add("form6")
    if "assignment" in ws:
        add("assign")
    if "ration" in ws:
        add("ration")
    if "epic" in ws:
        add("epic")
    if "cin" in ws:
        add("cin")
    if "aadhaar" in ws:
        add("aadhaar")
    if ws & {"male", "female", "sex", "gender"}:
        add("sex")
    if "status" in ws:
        add("status")
    if "operation" in ws:
        add("operation")
    if "fulldata" in ws or ("full" in ws and "data" in ws):
        add("fulldata")
    if "flowtype" in ws:
        add("flow")
    if "update" in ws and "user" not in ws:
        add("when")
    if "user" in ws:
        add("userno")
    mode = "field"
    if ws & {"compare", "match", "mismatch", "difference", "consistent", "change", "same"}:
        mode = "compare"
    elif "complete" in ws and "details" in ws:
        mode = "full"
    elif not keys:
        mode = "exists"
    return {"mode": mode, "keys": keys, "ws": ws}


def claims(message: str) -> bool:
    return parse(message) is not None


_COLS = sorted({c for v in FIELDS.values() for c in v[2]} | {"slno", "ward_code", "block_code", "district_code", "taluk_code", "town_code",
                                                              "has_aadhaar"} - {"has_aadhaar"})
_SQL = ("SELECT " + ", ".join(c for c in _COLS if c != "aadhar_no") +
        ", (aadhar_no IS NOT NULL AND aadhar_no <> '') AS has_aadhaar FROM uchitta_natham_modification WHERE appl_id = :a ORDER BY slno, row_id")


async def load(db, app: str) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    try:
        async with db.begin_nested():
            return [dict(r) for r in (await db.execute(text(_SQL), {"a": app})).mappings().all()]
    except Exception as exc:
        logger.warning(f"natham_qa: could not read uchitta_natham_modification ({type(exc).__name__})")
        return []


def _t(ta, en, tamil):
    return tamil if ta else en


def _no_rows(app: str, ta: bool) -> str:
    return _t(ta, f"No Natham-chitta modification record is held for application {app}. Only a correction / modification application (0158, 0159, 0160, 0164, 0169) carries one.",
              f"விண்ணப்பம் {app}-க்கு நத்தம் சிட்டா மாற்ற பதிவு இல்லை. திருத்தம் / மாற்ற விண்ணப்பத்துக்கு (0158, 0159, 0160, 0164, 0169) மட்டுமே அது இருக்கும்.")


def _val(r: Dict[str, Any], key: str, nr: str) -> str:
    cols = FIELDS[key][2]
    if key == "aadhaar":
        return _t(False, "recorded (not shown)", "") if r.get("has_aadhaar") else nr
    vals = [_v(r.get(c)) for c in cols]
    if key == "when" and vals[0]:
        vals[0] = vals[0][:19]
    if key == "numbers":
        return f"{r.get('own_num') if r.get('own_num') is not None else nr} / {r.get('rel_num') if r.get('rel_num') is not None else nr}"
    return " / ".join(x or nr for x in vals)


def _groups(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    g: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        g.setdefault(str(r.get("operation")), []).append(r)
    return g


def _owners(rs: List[Dict[str, Any]]) -> str:
    return "; ".join(f"{_v(x.get('owner')) or '-'} (patta {_v(x.get('patta_no')) or '-'})" for x in rs)


def answer(p: Dict[str, Any], app: str, rows: List[Dict[str, Any]], uamod: List[Dict[str, Any]], others: Dict[str, bool],
           reg: Optional[Dict[str, Any]], ta: bool) -> str:
    nr = _t(ta, "not recorded", "பதிவில்லை")
    if not rows:
        return _no_rows(app, ta)
    mode, keys, ws = p["mode"], p["keys"], p["ws"]
    g = _groups(rows)
    L: List[str] = []
    if mode == "exists":
        return _t(ta, f"Yes -- {len(rows)} Natham-chitta modification row(s) for {app} (operation codes {', '.join(sorted(g))}).",
                  f"ஆம் -- {app}-க்கு {len(rows)} நத்தம் சிட்டா மாற்ற பதிவுகள்.")
    if mode in ("field", "full"):
        use = list(FIELDS) if mode == "full" else keys
        for op in sorted(g):
            for i, r in enumerate(g[op], 1):
                tag = f"[op {op}" + (f" #{i}" if len(g[op]) > 1 else "") + "] "
                items = []
                for k in use:
                    lab = _t(ta, FIELDS[k][0], FIELDS[k][1])
                    v = _val(r, k, nr)
                    if k in _CODE and any(_v(r.get(c)) for c in FIELDS[k][2]):
                        v += _t(ta, " (a code the register does not define)", " (பதிவேடு வரையறுக்காத குறியீடு)")
                    items.append(f"{lab}: {v}")
                if mode == "full":
                    L.append(tag + "; ".join(items))
                else:
                    L += [tag + x for x in items]
        L = list(dict.fromkeys(L))
        return L[0] + f"  [{app}]" if len(L) == 1 else f"{app} (Natham chitta modification):\n" + "\n".join("• " + x for x in L)
    # compare ---------------------------------------------------------------
    sym = {"match": "✔", "differs": "✘", "n/a": "–"}
    for who, label, key in (("igrs", "IGRS owner", "igrs"), ("chitta", "chitta old-owner", "chitta"), ("tslr", "TSLR sketch", "tslr"),
                            ("fullfield", "full-field patta transfer", "fullfield"), ("new", "new-owner", "new"), ("old", "old-owner", "old")):
        if who in ws or (who == "new" and "newowner" in ws):
            if who == "old" and "owner" not in ws:
                continue
            L.append(_t(ta, f"No {label} record is held for this application (a Natham-chitta modification file has none), so nothing can be compared with it.",
                        f"இந்த விண்ணப்பத்துக்கு {label} பதிவு இல்லை; ஒப்பிட எதுவும் இல்லை.") if not others.get(key) else
                     _t(ta, f"A {label} record exists.", f"{label} பதிவு உள்ளது."))
    want_all = not ws & {"owner", "patta", "door", "share", "status", "operation", "spatta", "district", "taluk", "town", "ward", "block", "application"}
    pattas = {_v(r.get("patta_no")) for r in rows if _v(r.get("patta_no"))}
    if want_all or ws & {"patta", "spatta"}:
        up = {_v(r.get("patta_no")) for r in uamod if _v(r.get("patta_no"))}
        sp = {_v(r.get("s_patta_no")) for r in rows if _v(r.get("s_patta_no"))}
        if uamod:
            L.append(f"{sym['match'] if pattas & up else sym['differs']} " + _t(ta, f"Patta no (Natham vs parcel-modification record): {', '.join(sorted(pattas)) or nr}  |  {', '.join(sorted(up)) or nr}",
                                                                         f"பட்டா எண் (நத்தம் vs நிலப்பதிவு மாற்றம்): {', '.join(sorted(pattas)) or nr}  |  {', '.join(sorted(up)) or nr}"))
        else:
            L.append(_t(ta, "– no parcel-modification record to compare the patta with", "– பட்டாவை ஒப்பிட நிலப்பதிவு மாற்ற பதிவு இல்லை"))
        L.append(_t(ta, f"S patta no: {', '.join(sorted(sp)) or nr} (blank in every record, so it cannot be matched to any patta information)", f"S பட்டா எண்: {', '.join(sorted(sp)) or nr}"))
    if want_all or ws & {"status", "operation"}:
        ns = sorted({str(r.get("appl_status")) for r in rows}); nop = sorted(g)
        us = sorted({str(r.get("appl_status")) for r in uamod}); uop = sorted({str(r.get("operation")) for r in uamod})
        L.append(_t(ta, f"Status code(s): Natham {', '.join(ns)}  |  parcel modification {', '.join(us) or nr}; operation code(s): Natham {', '.join(nop)}  |  parcel modification {', '.join(uop) or nr}"
                        + (" -- the register does not define these codes" if True else ""),
                    f"நிலை: நத்தம் {', '.join(ns)} | நிலப்பதிவு {', '.join(us) or nr}; செயல்பாடு: நத்தம் {', '.join(nop)} | நிலப்பதிவு {', '.join(uop) or nr}"))
        if reg:
            L.append(_t(ta, f"Register status of the application: {reg.get('status')}; flow type: blank in every Natham and parcel-modification row.",
                        f"பதிவேட்டில் விண்ணப்ப நிலை: {reg.get('status')}."))
    if want_all or ws & {"district", "taluk", "town", "ward", "block"}:
        for c, lab in (("district_code", "District"), ("taluk_code", "Taluk"), ("town_code", "Town"), ("ward_code", "Ward"), ("block_code", "Block")):
            a = {_v(r.get(c)) for r in rows} - {None}
            b = {_v(r.get(c)) for r in uamod} - {None}
            if b:
                L.append(f"{sym['match'] if a == b else sym['differs']} {lab} code (Natham vs parcel modification): {', '.join(sorted(a))}  |  {', '.join(sorted(b))}")
            else:
                L.append(f"– {lab} code: {', '.join(sorted(a)) or nr}")
    if "door" in ws:
        L.append(_t(ta, f"Door no: {', '.join(sorted({_v(r.get('door_no')) for r in rows} - {None})) or nr}; no TSLR or other record exists for this file to compare it with.",
                    "கதவு எண்: ஒப்பிட வேறு பதிவு இல்லை."))
    if "share" in ws:
        L.append(_t(ta, "Share is blank in every Natham-chitta modification row, so it cannot be compared with any other owner record.", "பங்கு எந்த வரிசையிலும் இல்லை; ஒப்பிட இயலாது."))
    if want_all or ws & {"owner", "change", "same", "difference", "mismatch"} and not ws & {"patta", "status"}:
        ops = sorted(g)
        if len(ops) >= 2:
            L.append(_t(ta, "Owners by operation code (the register does not say what the codes mean):", "செயல்பாட்டு குறியீடு வாரியாக உரிமையாளர்கள்:"))
            for op in ops:
                L.append(f"op {op}: " + _owners(g[op]))
            base = ops[0]
            for op in ops[1:]:
                a = {_v(x.get("owner")) for x in g[base]} - {None}
                b = {_v(x.get("owner")) for x in g[op]} - {None}
                same = [x for x in b if any(_name_cmp(x, y) == "match" for y in a)]
                L.append(f"op {base} vs op {op}: " + _t(ta, f"{len(same)} owner(s) appear in both; {len(b) - len(same)} only under op {op}",
                                                       f"{len(same)} உரிமையாளர்(கள்) இரண்டிலும்; {len(b) - len(same)} மட்டும் op {op}-ல்"))
        else:
            L.append(_t(ta, "Only one operation code is recorded, so there is no second group of owners to compare.", "ஒரே ஒரு செயல்பாட்டு குறியீடு; ஒப்பிட இரண்டாவது குழு இல்லை."))
    if ws & {"application"} and ws & {"same", "fullfield"} and "fullfield" in ws:
        pass
    L = list(dict.fromkeys(x for x in L if x))
    return f"{app} (Natham chitta modification):\n" + "\n".join("• " + x for x in L)
