"""IGRS owner records of ONE application (`full_field_patta_transfer_igrs_owner`) and their
comparison with the chitta old-owner record and the patta-transfer application.

Only a Sub-Registrar (IGRS) referral carries these rows. For any other file an explicit IGRS
question is answered "no IGRS owner record held"; a field question without the word IGRS is
left to the other owner handlers when there is no row. Aadhaar is never shown. A blank column
is "not recorded". Typos, Tamil and Tanglish are understood. The caller checks access. No LLM."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.services import appinfo_qa
from backend.services.crosscheck_qa import _name_cmp, _row, _v
from backend.utils.logger import get_logger

logger = get_logger(__name__)

_TAMIL = [("யார்", " who "), ("ஐஜிஆர்எஸ்", " igrs "), ("உரிமையாளர்", " owner "), ("பட்டா", " patta "), ("ஆங்கில", " english "),
          ("பெயர்", " name "), ("வீட்டு எண்", " door "), ("கதவு", " door "), ("உறவினர்", " relative "), ("உறவு", " relation "),
          ("பங்கு", " share "), ("முகவரி", " address "), ("அஞ்சல்", " pincode "), ("ரேஷன்", " ration "),
          ("ஆதார்", " aadhaar "), ("கைபேசி", " mobile "), ("மொபைல்", " mobile "), ("பாலினம்", " sex "),
          ("தொழில்", " occupation "), ("புதுப்பி", " update "), ("படம்", " image "), ("ஒப்பிடு", " compare "),
          ("பொருந்து", " match "), ("வேறுபாடு", " difference "), ("சிட்டா", " chitta "), ("எண்", " number ")]
_EXACT = {
    "igrs": "igrs", "owners": "owner", "owner": "owner", "nam": "name", "adress": "address", "addres": "address",
    "numbr": "number", "no": "number", "num": "number", "nmbr": "number", "relashionship": "relation",
    "relationship": "relation", "relashion": "relation", "relative": "relative", "relatives": "relative",
    "assignmnt": "assignment", "assignment": "assignment", "assign": "assignment", "avalable": "available",
    "aadhar": "aadhaar", "aadhaar": "aadhaar", "adhar": "aadhaar", "epic": "epic", "voter": "epic", "sex": "sex",
    "gender": "sex", "updatd": "update", "updated": "update", "update": "update", "upadte": "update",
    "image": "image", "photo": "image", "img": "image", "ration": "ration", "pincode": "pincode", "pin": "pincode",
    "form6": "form6", "english": "english", "occupation": "occupation", "occ": "occupation", "door": "door",
    "mismach": "mismatch", "mismatch": "mismatch", "mismatches": "mismatch", "compair": "compare",
    "compare": "compare", "match": "match", "matches": "match", "same": "same", "consistant": "consistent",
    "consistent": "consistent", "conflicting": "conflict", "conflict": "conflict", "diffrent": "difference",
    "different": "difference", "difference": "difference", "differences": "difference", "detials": "details",
    "details": "details", "applcation": "application", "application": "application", "applcant": "applicant",
    "applicant": "applicant", "chitta": "chitta", "chita": "chitta", "verify": "verify", "share": "share",
    "patta": "patta", "pata": "patta", "mobile": "mobile", "user": "user", "person": "person", "belong": "belong",
}


def normalise(message: str) -> str:
    t = (message or "").lower()
    for ta, en in _TAMIL:
        t = t.replace(ta, en)
    t = re.sub(r"\bpatta\s*transfer\b", " pattatransfer ", t)
    t = re.sub(r"\bform[\s\-_]*6\b", " form6 ", t)
    t = re.sub(r"[?!.:()\"',;/&]", " ", t)
    return re.sub(r"\s+", " ", " ".join(_EXACT.get(w, w) for w in re.findall(r"[a-z0-9]+|[^\sa-z0-9]+", t))).strip()


OWNER, ENGLISH, PATTA, DOOR, RELATIVE, NUMBERS, SHARE, ADDRESS, IGRSNO, FORM6, RATION, EPIC, AADHAAR = (
    "owner", "owner_english", "patta_no", "door_no", "relative", "numbers", "share", "address", "igrs_form6_no",
    "form6_no", "ration_card_no", "epic_no", "aadhar_no")
MOBILE, SEX, OCC, UPDATED, USERNO, IMAGE = "mobile_number", "sex", "occ_cd", "updn_dt", "user_no", "owner_img"


def parse(message: str) -> Dict[str, Any]:
    """{"kind": "field" | "compare" | None, "keys": [...], "igrs": bool}"""
    if "everyfield" in (message or "").lower() and "igrs" in (message or "").lower():  # "which fields are blank": every column, the blanks are picked out afterwards
        return {"kind": "field", "keys": [OWNER, ENGLISH, PATTA, DOOR, RELATIVE, NUMBERS, SHARE, ADDRESS, IGRSNO, FORM6, RATION, EPIC, AADHAAR,
                                         MOBILE, SEX, OCC, UPDATED, USERNO], "igrs": True, "ws": set()}
    t = normalise(message)
    ws = set(t.split())
    igrs = "igrs" in ws
    keys: List[str] = []

    def add(k):
        if k not in keys:
            keys.append(k)

    if "who" in ws and "owner" in ws and "name" not in ws:
        add(OWNER)
    if "english" in ws and "name" in ws:
        add(ENGLISH)
    if "owner" in ws and "name" in ws and ENGLISH not in keys:
        add(OWNER)
    if "patta" in ws and "number" in ws:
        add(PATTA)
    if "door" in ws:
        add(DOOR)
    if "relative" in ws or "relation" in ws:
        add(RELATIVE)
    if "relative" in ws and "number" in ws and "owner" in ws:
        add(NUMBERS)
    if "share" in ws:
        add(SHARE)
    if "address" in ws or "pincode" in ws:
        add(ADDRESS)
    if igrs and "form6" in ws:
        add(IGRSNO)
    elif "form6" in ws and ws & {"assignment"}:
        add(FORM6)
        add("assign_no")
    if "ration" in ws:
        add(RATION)
    if "epic" in ws:
        add(EPIC)
    if "aadhaar" in ws:
        add(AADHAAR)
    if "mobile" in ws and "owner" in ws:
        add(MOBILE)
    if "sex" in ws and "owner" in ws:
        add(SEX)
    if "occupation" in ws and "owner" in ws:
        add(OCC)
    if "update" in ws and ("record" in ws or "owner" in ws) and "user" not in ws:
        add(UPDATED)
    if "user" in ws and "number" in ws:
        add(USERNO)
    if "image" in ws and "owner" in ws:
        add(IMAGE)
    if ws & {"compare", "match", "mismatch", "consistent", "conflict", "difference", "same", "verify", "belong"} and \
            (igrs or ws & {"chitta"} or "english" in ws) and (igrs or "owner" in ws):
        return {"kind": "compare", "keys": keys, "igrs": igrs, "ws": ws}
    if not keys:
        return {"kind": None, "keys": [], "igrs": igrs, "ws": ws}
    return {"kind": "field", "keys": keys, "igrs": igrs, "ws": ws}


def claims_strong(message: str) -> bool:
    """Plainly an IGRS-owner question (names IGRS, compares, or asks for a column only this table has) --
    strong enough to steer the turn; a bare "who is the owner" is only a soft claim."""
    p = parse(message)
    if p["kind"] == "compare":
        return True
    return p["kind"] == "field" and (p["igrs"] or bool(set(p["keys"]) & {ENGLISH, DOOR, RATION, EPIC, AADHAAR, OCC, USERNO, IMAGE, NUMBERS, FORM6}))


def claims(message: str) -> bool:
    p = parse(message)
    if p["kind"] == "compare":
        return True
    # a field question is this module's when it names IGRS or a column only this table has
    return p["kind"] == "field" and (p["igrs"] or "owner" in p["ws"]
                                     or bool(set(p["keys"]) & {ENGLISH, DOOR, RATION, EPIC, AADHAAR, OCC, USERNO, IMAGE, NUMBERS, FORM6}))


async def load(db, app: str) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    sql = ("SELECT door_no, patta_no, occ_cd, own_num, owner, owner_english, rel_num, relative, relative_english, reltn, share, "
           "address, form6_no, assign_no, ration_card_no, epic_no, pincode, updn_dt, user_no, (aadhar_no IS NOT NULL AND aadhar_no <> '') AS has_aadhaar, "
           "sex, igrs_form6_no, mobile_number, (owner_img IS NOT NULL AND owner_img <> '') AS has_img, ward_code, block_code "
           "FROM full_field_patta_transfer_igrs_owner WHERE appl_id = :a ORDER BY own_num, row_id")
    try:
        async with db.begin_nested():
            return [dict(r) for r in (await db.execute(text(sql), {"a": app})).mappings().all()]
    except Exception as exc:
        logger.warning(f"igrs_owner_qa: could not read the IGRS owner table ({type(exc).__name__})")
        return []


def _t(ta, en, tamil):
    return tamil if ta else en


def _no_row(app: str, ta: bool) -> str:
    return _t(ta, f"No IGRS owner record is held for application {app}. Only an application referred by the Sub-Registrar (IGRS) carries one.",
              f"விண்ணப்பம் {app}-க்கு IGRS உரிமையாளர் பதிவு இல்லை. Sub-Registrar (IGRS) பரிந்துரை விண்ணப்பத்துக்கு மட்டுமே அது இருக்கும்.")


_REL = {"4": "w/o (wife of)", "5": "s/o (son of)", "6": "d/o (daughter of)"}


def _field_lines(keys: List[str], r: Dict[str, Any], ta: bool) -> List[str]:
    nr = _t(ta, "not recorded", "பதிவு செய்யப்படவில்லை")
    out: List[str] = []
    for k in keys:
        if k == OWNER:
            out.append(_t(ta, "Owner (IGRS)", "உரிமையாளர் (IGRS)") + f": {_v(r.get('owner')) or nr}")
        elif k == ENGLISH:
            out.append(_t(ta, "Owner English name", "உரிமையாளர் ஆங்கில பெயர்") + f": {_v(r.get('owner_english')) or nr}")
        elif k == PATTA:
            out.append(_t(ta, "Patta no", "பட்டா எண்") + f": {_v(r.get('patta_no')) or nr}")
        elif k == DOOR:
            out.append(_t(ta, "Door no", "கதவு எண்") + f": {_v(r.get('door_no')) or nr}")
        elif k == RELATIVE:
            rc = _v(r.get("reltn"))
            out.append(_t(ta, "Relative / relationship", "உறவினர் / உறவு") + f": {_v(r.get('relative')) or nr}" +
                       (f" ({_REL.get(rc, 'code ' + rc)})" if rc else ""))
        elif k == NUMBERS:
            out.append(_t(ta, "Owner no / relative no", "உரிமையாளர் எண் / உறவினர் எண்") + f": {r.get('own_num') if r.get('own_num') is not None else nr} / {r.get('rel_num') if r.get('rel_num') is not None else nr}")
        elif k == SHARE:
            out.append(_t(ta, "Share", "பங்கு") + f": {_v(r.get('share')) or nr}")
        elif k == ADDRESS:
            out.append(_t(ta, "Address / pincode", "முகவரி / அஞ்சல் எண்") + f": {_v(r.get('address')) or nr} / {_v(r.get('pincode')) or nr}")
        elif k == IGRSNO:
            out.append(_t(ta, "IGRS form 6 no", "IGRS படிவம் 6 எண்") + f": {_v(r.get('igrs_form6_no')) or nr}")
        elif k == FORM6:
            out.append(_t(ta, "Form 6 no", "படிவம் 6 எண்") + f": {_v(r.get('form6_no')) or nr}")
        elif k == "assign_no":
            out.append(_t(ta, "Assignment no", "ஒதுக்கீடு எண்") + f": {_v(r.get('assign_no')) or nr}")
        elif k == RATION:
            out.append(_t(ta, "Ration card no", "ரேஷன் அட்டை எண்") + f": {_v(r.get('ration_card_no')) or nr}")
        elif k == EPIC:
            out.append(_t(ta, "EPIC (voter ID) no", "EPIC (வாக்காளர் அட்டை) எண்") + f": {_v(r.get('epic_no')) or nr}")
        elif k == AADHAAR:
            out.append(_t(ta, "Aadhaar: ", "ஆதார்: ") + (_t(ta, "recorded (not shown by this assistant)", "பதிவாகியுள்ளது (இங்கு காட்டப்படாது)") if r.get("has_aadhaar") else nr))
        elif k == MOBILE:
            out.append(_t(ta, "Mobile", "கைபேசி") + f": {_v(r.get('mobile_number')) or nr}")
        elif k == SEX:
            out.append(_t(ta, "Sex", "பாலினம்") + f": {_v(r.get('sex')) or nr}")
        elif k == OCC:
            out.append(_t(ta, "Occupation code", "தொழில் குறியீடு") + f": {_v(r.get('occ_cd')) or nr}")
        elif k == UPDATED:
            out.append(_t(ta, "Last updated", "கடைசியாக புதுப்பித்தது") + f": {(_v(r.get('updn_dt')) or nr)[:19]}")
        elif k == USERNO:
            out.append(_t(ta, "User no", "பயனர் எண்") + f": {_v(r.get('user_no')) or nr}")
        elif k == IMAGE:
            out.append(_t(ta, "Owner image: ", "உரிமையாளர் படம்: ") + (_t(ta, "available", "உள்ளது") if r.get("has_img") else _t(ta, "not attached", "இணைக்கப்படவில்லை")))
    return out


def answer_fields(keys: List[str], app: str, rows: List[Dict[str, Any]], ta: bool) -> str:
    if not rows:
        return _no_row(app, ta)
    lines: List[str] = []
    for r in rows:
        ls = _field_lines(keys, r, ta)
        if len(rows) > 1:
            lines.append(_t(ta, f"Owner {r.get('own_num')}:", f"உரிமையாளர் {r.get('own_num')}:"))
            lines += ["  " + x for x in ls]
        else:
            lines += ls
    if len(lines) == 1:
        return f"{lines[0]}  [{app}]"
    return f"{app} (IGRS):\n" + "\n".join("• " + l if not l.startswith("  ") and not l.endswith(":") else l for l in lines)


def _eq(a, b) -> str:
    a, b = _v(a), _v(b)
    if not a or not b:
        return "n/a"
    return "match" if str(a).strip().lower() == str(b).strip().lower() else "differs"


def answer_compare(p: Dict[str, Any], app: str, rows: List[Dict[str, Any]], chitta: List[Dict[str, Any]],
                   info: Optional[Dict[str, Any]], detail: Dict[str, Any], ta: bool) -> str:
    if not rows:
        return _no_row(app, ta)
    nr = _t(ta, "not recorded", "பதிவில்லை")
    ws = p["ws"]
    info = info or {}
    c = chitta[0] if chitta else None
    narrowed = ws & {"name", "patta", "form6", "share", "relative", "relation", "address", "pincode", "english", "person"}
    want = lambda *k: (not narrowed and "belong" not in ws) or bool(ws & set(k))
    only_chitta = bool(ws & {"chitta", "old"})
    only_app = bool(ws & {"applicant", "application", "pattatransfer"}) and not only_chitta
    if "belong" in ws:
        ids = ", ".join(str(r.get("own_num")) for r in rows)
        return _t(ta, f"Yes. The IGRS owner row(s) (owner no {ids}) carry the application id {app}, the same as this application; "
                      f"their form 6 no is {_v(rows[0].get('form6_no')) or nr}.",
                  f"ஆம். IGRS உரிமையாளர் பதிவுகள் (உரிமையாளர் எண் {ids}) இந்த விண்ணப்ப எண் {app}-ஐயே கொண்டுள்ளன; படிவம் 6 எண் {_v(rows[0].get('form6_no')) or nr}.")
    L: List[str] = []
    L.append(_t(ta, "Same application: the IGRS owner rows and this application carry the same application id (" + app + ").",
                "ஒரே விண்ணப்பம்: IGRS உரிமையாளர் பதிவுகளும் இந்த விண்ணப்பமும் ஒரே விண்ணப்ப எண்ணை (" + app + ") கொண்டுள்ளன.")) \
        if ws & {"belong", "same", "application"} and ("belong" in ws) else None
    for r in rows:
        tag = f"[{r.get('own_num')}] " if len(rows) > 1 else ""
        if want("name", "english", "person"):
            if c and not only_app:
                v = _name_cmp(r.get("owner_english") or r.get("owner"), c.get("owner_name") or c.get("owner_ename"))
                L.append(tag + _row(_t(ta, "IGRS owner vs chitta old owner", "IGRS உரிமையாளர் vs சிட்டா பழைய உரிமையாளர்"),
                                    r.get("owner_english") or r.get("owner"), c.get("owner_name") or c.get("owner_ename"), v, nr))
            av = _name_cmp(r.get("owner_english") or r.get("owner"), info.get("appl_name") or detail.get("applicant_name"))
            if not only_chitta:
              L.append(tag + _row(_t(ta, "IGRS owner vs patta-transfer applicant", "IGRS உரிமையாளர் vs பட்டா மாற்ற விண்ணப்பதாரர்"),
                                r.get("owner_english") or r.get("owner"), info.get("appl_name") or detail.get("applicant_name"), av, nr))
        if want("patta"):
            cp = c.get("existing_patta_no") if c else None
            L.append(tag + _row(_t(ta, "Patta no (IGRS vs chitta)", "பட்டா எண் (IGRS vs சிட்டா)"), r.get("patta_no"), cp, _eq(r.get("patta_no"), cp), nr))
            L.append(tag + _row(_t(ta, "Patta no (IGRS vs application)", "பட்டா எண் (IGRS vs விண்ணப்பம்)"), r.get("patta_no"),
                                detail.get("patta_number"), _eq(r.get("patta_no"), detail.get("patta_number")), nr))
        if want("form6"):
            L.append(tag + _row(_t(ta, "IGRS form 6 no (IGRS vs application)", "IGRS படிவம் 6 எண் (IGRS vs விண்ணப்பம்)"), r.get("igrs_form6_no"),
                                info.get("igrs_form6no") or detail.get("igrs_form6_number"),
                                _eq(r.get("igrs_form6_no"), info.get("igrs_form6no") or detail.get("igrs_form6_number")), nr))
            L.append(tag + _row(_t(ta, "Form 6 no vs application id", "படிவம் 6 எண் vs விண்ணப்ப எண்"), r.get("form6_no"), app, _eq(r.get("form6_no"), app), nr))
        if want("relative", "relation"):
            if c:
                L.append(tag + _row(_t(ta, "Relative name (IGRS vs chitta)", "உறவினர் பெயர் (IGRS vs சிட்டா)"), r.get("relative_english") or r.get("relative"),
                                    c.get("relative_name") or c.get("relative_ename"),
                                    _name_cmp(r.get("relative_english") or r.get("relative"), c.get("relative_name") or c.get("relative_ename")), nr))
                L.append(tag + _row(_t(ta, "Relation code (IGRS vs chitta)", "உறவு குறியீடு (IGRS vs சிட்டா)"), r.get("reltn"), c.get("relation_code"),
                                    _eq(r.get("reltn"), c.get("relation_code")), nr))
            else:
                L.append(tag + _t(ta, f"– Relative: IGRS {_v(r.get('relative')) or nr} (code {_v(r.get('reltn')) or nr}); no chitta record to compare with",
                                  f"– உறவினர்: IGRS {_v(r.get('relative')) or nr}; ஒப்பிட சிட்டா பதிவு இல்லை"))
        if want("share"):
            cs = c.get("share") if c else None
            L.append(tag + _row(_t(ta, "Share (IGRS vs chitta)", "பங்கு (IGRS vs சிட்டா)"), r.get("share"), cs, _eq(r.get("share"), cs), nr))
        if want("address", "pincode"):
            L.append(tag + _row(_t(ta, "IGRS address vs applicant address", "IGRS முகவரி vs விண்ணப்பதாரர் முகவரி"), r.get("address"),
                                info.get("appl_address_cur") or detail.get("applicant_address"),
                                _eq(r.get("address"), info.get("appl_address_cur") or detail.get("applicant_address")), nr))
            L.append(tag + _t(ta, f"– Pincode: IGRS {_v(r.get('pincode')) or nr}; the applicant record has no pincode column",
                              f"– அஞ்சல் எண்: IGRS {_v(r.get('pincode')) or nr}; விண்ணப்பதாரர் பதிவில் அந்த புலம் இல்லை"))
    if not c:
        L.append(_t(ta, "No chitta old-owner record is held for this application, so nothing can be compared with it.",
                    "இந்த விண்ணப்பத்துக்கு சிட்டா பழைய உரிமையாளர் பதிவு இல்லை; அதனுடன் ஒப்பிட முடியாது."))
    L = [x for x in L if x]
    bad = [x for x in L if x.startswith(("✘", "[")) and "✘" in x[:6]]
    L.append(_t(ta, "No differing values were found." if not any("✘" in x for x in L) else "Fields marked ✘ differ.",
                "வேறுபடும் மதிப்புகள் இல்லை." if not any("✘" in x for x in L) else "✘ குறியிட்டவை வேறுபடுகின்றன."))
    L.append(_t(ta, "(✔ match, ◐ similar, ✘ differs, – not comparable)", "(✔ பொருந்தும், ◐ ஒத்தது, ✘ வேறுபடும், – ஒப்பிட இயலாது)"))
    return f"{app}:\n" + "\n".join("• " + x for x in L)
