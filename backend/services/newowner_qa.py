"""NEW-owner records of ONE application (`full_field_patta_transfer_new_owner`, the owners a
full-field patta transfer (NISD) is giving the land to) and their comparison with the OLD-owner
records (`full_field_patta_transfer_old_owner` for NISD, `chitta_temp_old_owner` for ISD), the IGRS
owners, the F-Line record, the sub-division record and the application.

UDS is explained: Undivided Share -- the share of land that is held in common (for example the land
under a block of flats) and goes with a flat or plot; it is a recorded measure per owner, not a
parcel of its own. Every value comes from the rows; a blank column is "not recorded", an owner
record the file does not have is said so. Aadhaar is never shown. Typos, Tamil and Tanglish are
understood. The caller checks access. No LLM."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.services import igrs_owner_qa as ig
from backend.services.crosscheck_qa import _name_cmp, _row, _v
from backend.utils.logger import get_logger

logger = get_logger(__name__)

UDS_EN = ("UDS stands for Undivided Share: the proportion of land held in common that belongs to an owner "
          "-- for example the share of the land under a block of flats that goes with one flat -- recorded as a measure "
          "of land (extent) or a fraction. It is not a separate parcel, so it is read together with the owner's share and extent.")
UDS_TA = ("UDS என்பது பிரிக்கப்படாத பங்கு (Undivided Share): பலர் பொதுவாக வைத்திருக்கும் நிலத்தில் ஒரு உரிமையாளருக்கு உரிய "
          "விகிதம் -- எடுத்துக்காட்டாக அடுக்குமாடி குடியிருப்பின் கீழ் உள்ள நிலத்தில் ஒரு வீட்டுக்கு உரிய பங்கு -- இது பரப்பளவாகவோ "
          "பின்னமாகவோ பதிவாகும். இது தனி நிலத்துண்டு அல்ல; உரிமையாளரின் பங்கு மற்றும் பரப்பளவுடன் சேர்த்தே பார்க்கப்படும்.")

_TAMIL = [("திருப்பப்பட்ட", " return "), ("திரும்பிய", " return "), ("யார்", " who "), ("என்றால்", " meaning "), ("புதிய உரிமையாளர்", " new owner "), ("பிரிக்கப்படாத பங்கு", " uds "), ("பிரிபடா பாகம்", " uds "),
          ("பழைய உரிமையாளர்", " old owner "), ("உரிமையாளர்", " owner "), ("பரப்பளவு", " extent "), ("நிலை", " status "),
          ("சர்வே", " survey "), ("உட்பிரிவு", " subdivision "), ("மாவட்டம்", " district "), ("வட்டம்", " taluk "),
          ("வார்டு", " ward "), ("ஒப்பிடு", " compare "), ("வேறுபாடு", " difference "), ("பங்கு", " share "),
          ("பட்டா", " patta "), ("உறவினர்", " relative "), ("உறவு", " relation "), ("பெயர்", " name "), ("எண்", " number ")]
_ALIAS = {
    "ower": "owner", "owners": "owner", "relitive": "relative", "relitives": "relative", "extant": "extent",
    "extend": "extent", "survay": "survey", "servey": "survey", "divison": "division", "detials": "details",
    "uds": "uds", "u.d.s": "uds", "photo": "photo", "photos": "photo", "avalable": "available", "stauts": "status",
    "udsvalue": "uds", "agree": "match", "agrees": "match", "matches": "match", "mismach": "mismatch",
    "consistant": "consistent", "diffrent": "difference", "different": "difference", "differences": "difference",
    "chitta": "old", "existing": "old", "previous": "old", "previou": "old", "prev": "old", "former": "old", "earlier": "old",
    "changed": "change", "changes": "change", "same": "same",
    "relashionship": "relation", "relationship": "relation", "relashion": "relation", "applcation": "application",
    "numbr": "number", "no": "number", "nam": "name", "aadhar": "aadhaar", "adress": "address",
    "return": "return", "returned": "return", "retunred": "return", "retuned": "return", "retruned": "return",
    "returnd": "return", "thirumbiya": "return", "thirumbina": "return", "returns": "return", "yaaru": "who", "yar": "who", "yaar": "who", "pangu": "share", "pazhaya": "old", "palaya": "old", "puthiya": "new",
    "pudhiya": "new", "urimaiyalar": "owner", "urimaiyaalar": "owner", "extend": "extent", "parappalavu": "extent",
}


def normalise(message: str) -> str:
    t = re.sub(r"\d{4}/\d{3,4}/\d{1,3}/\d+", " ", (message or "").lower())
    for ta, en in _TAMIL:
        t = t.replace(ta, en)
    t = re.sub(r"\bf[\s\-_]*line\b", " fline ", t)
    t = re.sub(r"\bsub[\s\-_.]*d[a-z]*v[a-z]*", " subdivision ", t)
    t = re.sub(r"\bpatta\s*transfer\b", " pattatransfer ", t)
    t = re.sub(r"[?!.:()\"',;/&]", " ", t)
    return re.sub(r"\s+", " ", " ".join(_ALIAS.get(w, w) for w in re.findall(r"[a-z0-9]+|[^\sa-z0-9]+", t))).strip()


OWNER, ENGLISH, RELATION, RELATIVE, RELATIVE_EN, EXTENT, PATTA, OWNERNUM, RELNUM = (
    "owner_name", "owner_ename", "relation", "relative_name", "relative_ename", "extent", "patta_no", "owner_num", "rel_num")
APPLINK = "application_link"
UPDATED, REASON, OVERALL = "update_dt", "reason", "overall_status"
STATUS, UDS, PHOTO, SHARE, AADHAAR, SEX, SURVEY, SUBDIV, GEO, MOBILE = (
    "status_owner", "uds", "photo", "share", "aadhar_no", "sex", "survey_no", "sub_div_no", "geo", "mobile_number")


def parse(message: str) -> Dict[str, Any]:
    t = normalise(message)
    ws = set(t.split())
    keys: List[str] = []

    def add(k):
        if k not in keys:
            keys.append(k)

    new = "new" in ws and "owner" in ws
    ret = "return" in ws and "owner" in ws
    if ret:
        new = False
    old_only = "old" in ws and "owner" in ws and "new" not in ws
    uds_def = "uds" in ws and bool(ws & {"what", "meaning", "mean", "define", "explain", "means", "enna", "stand"}) and \
        not ws & {"new", "owner", "application", "value", "extent", "agree", "match"}
    if "english" in ws and "name" in ws and "relative" not in ws:
        add(ENGLISH)
    if "owner" in ws and "name" in ws and ENGLISH not in keys and "relative" not in ws:
        add(OWNER)
    if "who" in ws and "owner" in ws and "name" not in ws and "relative" not in ws:
        add(OWNER)
    if "relation" in ws:
        add(RELATION)
    if "relative" in ws and "english" in ws:
        add(RELATIVE_EN)
    elif "relative" in ws and "number" not in ws:
        add(RELATIVE)
    if "extent" in ws:
        add(EXTENT)
    if "patta" in ws and "number" in ws:
        add(PATTA)
    if "owner" in ws and "number" in ws and not ws & {"relative", "patta", "mobile", "survey", "subdivision", "ward", "block", "taluk", "district", "town", "aadhaar", "application"}:
        add(OWNERNUM)
    if "relative" in ws and "number" in ws:
        add(RELNUM)
    if "status" in ws and ("owner" in ws or "record" in ws) and "application" not in ws:
        add(STATUS)
    if "uds" in ws:
        add(UDS)
    if "photo" in ws or ("image" in ws):
        add(PHOTO)
    if "share" in ws:
        add(SHARE)
    if "aadhaar" in ws:
        add(AADHAAR)
    if ("male" in ws or "female" in ws or "sex" in ws or "gender" in ws):
        add(SEX)
    if "survey" in ws:
        add(SURVEY)
    if "subdivision" in ws and "new" not in ws or ("subdivision" in ws and "new" in ws and "owner" in ws and "record" not in ws):
        add(SUBDIV)
    if ws & {"district", "taluk", "town", "ward", "block"}:
        add(GEO)
    if "complete" in ws and "details" in ws and "owner" in ws:
        for k in (OWNER, ENGLISH, RELATION, RELATIVE, RELATIVE_EN, EXTENT, PATTA, OWNERNUM, RELNUM, STATUS, UDS, SHARE, SEX, AADHAAR, GEO):
            add(k)
    if "application" in ws and ws & {"linked", "link", "belongs", "belong"} and "owner" in ws:
        add(APPLINK)
    if ret and ("update" in ws or "updated" in ws) and ("date" in ws or "when" in ws or "record" in ws):
        add(UPDATED)
    if ret and ws & {"why", "reason"}:
        add(REASON)
    if ret and "status" in ws and "overall" in ws:
        add(OVERALL)
    if ret and ws & {"location", "survey"} and ws & {"linked", "details", "location"}:
        add(GEO)
    if "mobile" in ws:
        add(MOBILE)

    if old_only and not keys and not ws & {"survey", "compare", "same", "difference", "change"} and len(ws) <= 6:
        add(OWNER)  # "and the old owner?" asks who it is
    cmp_words = ws & {"compare", "match", "mismatch", "consistent", "difference", "change", "same", "different", "diffrent", "agree",
                      "inconsistent", "inconsistant", "stay", "stayed", "unchanged", "changing"}
    targets = set()
    if "igrs" in ws:
        targets.add("igrs")
    if "old" in ws:
        targets.add("old")
    if "fline" in ws:
        targets.add("fline")
    if "subdivision" in ws and ("record" in ws or "table" in ws or "area" in ws):
        targets.add("subdiv")
    if "application" in ws or "pattatransfer" in ws:
        targets.add("application")
    if "extent" in ws and "area" in ws:
        targets.add("subdiv")
    kind = None
    if ret and not uds_def:
        cmp_r = ws & {"compare", "match", "mismatch", "consistent", "difference", "change", "same", "different", "diffrent", "agree",
                      "mismatches", "inconsistent", "inconsistant", "diffrent"}
        kind = "return_compare" if cmp_r else "return_field"
    elif uds_def:
        kind = "uds_def"
    elif "owner" in ws and ("changing" in ws or ("same" in ws and "person" in ws)) and not ws & {"igrs", "chitta"}:
        kind = "compare"
    elif new and cmp_words or ("uds" in ws and cmp_words) or ("new" in ws and "old" in ws and cmp_words) or \
            ("change" in ws and "old" in ws):
        kind = "compare"
    elif old_only and "survey" in ws and not new:
        kind = None  # "old owner of survey 5": a survey question, not a record of this application
    elif new or old_only and keys or keys and (ws & {"uds"}):
        kind = "field"
    elif keys:
        kind = "field_generic"  # an owner field without the word "new": the caller decides by which owner rows exist
    return {"kind": kind, "keys": keys, "targets": targets, "ws": ws, "new": new, "old_only": old_only}


def claims_strong(message: str) -> bool:
    """Names a new owner, compares, or defines UDS -- steers the turn. A bare owner field is only a soft claim."""
    return parse(message)["kind"] in ("compare", "field", "return_field", "return_compare")


def claims(message: str) -> bool:
    p = parse(message)
    if p["kind"] in ("uds_def", "compare", "return_field", "return_compare"):
        return True
    if p["kind"] == "field":
        return True
    # a generic owner field only when it plainly speaks of an owner
    return p["kind"] == "field_generic" and ("owner" in p["ws"] or bool(set(p["keys"]) & {RELATIVE, RELATIVE_EN, RELNUM, RELATION, EXTENT, SHARE, UDS, SEX, PHOTO}))


_OLD_NISD = ("SELECT owner_name, owner_ename, relation, relative_name, relative_ename, extent, patta_no, relation_code, owner_num, rel_num, "
             "district_code, taluk_code, town_code, "
             "status_owner, uds, share, (aadhar_no IS NOT NULL AND aadhar_no <> '') AS has_aadhaar, sex, ward_code, block_code "
             "FROM full_field_patta_transfer_old_owner WHERE appl_id = :a ORDER BY owner_num, row_id")
_NEW = ("SELECT owner_name, owner_ename, relation, relative_name, relative_ename, extent, patta_no, relation_code, owner_num, rel_num, "
        "status_owner, uds, share, (aadhar_no IS NOT NULL AND aadhar_no <> '') AS has_aadhaar, sex, survey_no, sub_div_no, "
        "district_code, taluk_code, town_code, ward_code, block_code, mobile_number, "
        "((photo IS NOT NULL AND photo <> '') OR (owner_img IS NOT NULL AND owner_img NOT IN ('', '-'))) AS has_photo "
        "FROM full_field_patta_transfer_new_owner WHERE appl_id = :a ORDER BY owner_num, row_id")


async def _rows(db, sql: str, app: str) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    try:
        async with db.begin_nested():
            return [dict(r) for r in (await db.execute(text(sql), {"a": app})).mappings().all()]
    except Exception as exc:
        logger.warning(f"newowner_qa: read failed ({type(exc).__name__})")
        return []


async def load_new(db, app: str) -> List[Dict[str, Any]]:
    return await _rows(db, _NEW, app)


_RET = ("SELECT owner_name, owner_ename, relation, relative_name, relative_ename, extent, patta_no, relation_code, owner_num, rel_num, "
        "status_owner, status, update_dt, uds, share, (aadhar_no IS NOT NULL AND aadhar_no <> '') AS has_aadhaar, sex, "
        "district_code, taluk_code, town_code, ward_code, block_code, mobile_number, "
        "((photo IS NOT NULL AND photo <> '') OR (owner_img IS NOT NULL AND owner_img NOT IN ('', '-'))) AS has_photo "
        "FROM full_field_patta_transfer_return_owner WHERE appl_id = :a ORDER BY owner_num, row_id")


async def load_return(db, app: str) -> List[Dict[str, Any]]:
    return await _rows(db, _RET, app)


async def count_return(db) -> int:
    from sqlalchemy import text
    try:
        async with db.begin_nested():
            return int((await db.execute(text("SELECT count(*) FROM full_field_patta_transfer_return_owner"))).scalar() or 0)
    except Exception:
        return 0


async def load_old_nisd(db, app: str) -> List[Dict[str, Any]]:
    return await _rows(db, _OLD_NISD, app)


async def load_old(db, app: str) -> List[Dict[str, Any]]:
    rows = await _rows(db, _OLD_NISD, app)
    if rows:
        return rows
    from backend.services import oldowner_qa
    chitta, _ = await oldowner_qa.load(db, app)
    return [{"owner_name": c.get("owner_name"), "owner_ename": c.get("owner_ename"), "relation": c.get("relation"),
             "relative_name": c.get("relative_name"), "relative_ename": c.get("relative_ename"),
             "extent": None, "patta_no": c.get("existing_patta_no") or c.get("patta_no"), "relation_code": c.get("relation_code"),
             "owner_num": c.get("owner_num"), "status_owner": c.get("status"), "uds": None, "share": c.get("share"),
             "has_aadhaar": bool(_v(c.get("aadhar_no"))), "sex": c.get("sex"), "survey_no": c.get("survey_no"),
             "sub_div_no": c.get("subdiv_no")} for c in chitta]


def _t(ta, en, tamil):
    return tamil if ta else en


def _nr(ta):
    return _t(ta, "not recorded", "பதிவு செய்யப்படவில்லை")


_LABEL = {
    OWNER: ("New owner", "புதிய உரிமையாளர்"), ENGLISH: ("Owner English name", "உரிமையாளர் ஆங்கில பெயர்"),
    RELATION: ("Relationship", "உறவு முறை"), RELATIVE: ("Relative name", "உறவினர் பெயர்"),
    RELATIVE_EN: ("Relative English name", "உறவினர் ஆங்கில பெயர்"), EXTENT: ("Extent", "பரப்பளவு"),
    PATTA: ("Patta no", "பட்டா எண்"), OWNERNUM: ("Owner no", "உரிமையாளர் எண்"), RELNUM: ("Relative no", "உறவினர் எண்"),
    STATUS: ("Owner record status code", "உரிமையாளர் பதிவு நிலை குறியீடு"), UDS: ("UDS (undivided share)", "UDS (பிரிக்கப்படாத பங்கு)"),
    SHARE: ("Share", "பங்கு"), SURVEY: ("Survey no", "சர்வே எண்"), SUBDIV: ("Sub-division no", "உட்பிரிவு எண்"),
    MOBILE: ("Mobile", "கைபேசி"),
}


def answer_fields(keys: List[str], app: str, rows: List[Dict[str, Any]], ta: bool, old: bool = False) -> str:
    if not rows:
        # the extract carries these rows for some full-field transfers only (some 0153 NISD and 0165 files),
        # so "only an NISD file has one" was wrong both ways -- said even about an NISD file without rows
        return _t(ta, f"No {'old' if old else 'new'}-owner record is held for application {app}. The full-field transfer "
                      f"owner records exist only for some NISD (0153) and owner-name-correction (0165) files.",
                  f"விண்ணப்பம் {app}-க்கு {'பழைய' if old else 'புதிய'} உரிமையாளர் பதிவு இல்லை. இந்த பதிவுகள் சில NISD (0153) "
                  f"மற்றும் 0165 கோப்புகளுக்கு மட்டுமே உள்ளன.")
    nr = _nr(ta)
    out: List[str] = []
    dup = len({str(x.get("owner_num")) for x in rows}) < len(rows)
    for idx, r in enumerate(rows, 1):
        ls: List[str] = []
        for k in keys:
            if k == APPLINK:
                ls.append(_t(ta, "Application: ", "விண்ணப்பம்: ") + app)
            elif k in _LABEL:
                val = _v(r.get(k))
                if k == RELATION and _v(r.get("relation_code")):
                    val = f"{val or nr} (code {r['relation_code']})" if val else f"code {r['relation_code']}"
                if k == STATUS and val:
                    val += _t(ta, " (the register does not define this code)", " (குறியீட்டின் பொருள் பதிவேட்டில் இல்லை)")
                if k == UDS and not val:
                    val = nr + _t(ta, " — blank in this record", " — இந்த பதிவில் காலி")
                lab = _t(ta, *_LABEL[k])
                if old and k == OWNER:
                    lab = _t(ta, "Old owner", "பழைய உரிமையாளர்")
                ls.append(f"{lab}: {val or nr}")
            elif k == AADHAAR:
                ls.append(_t(ta, "Aadhaar: ", "ஆதார்: ") + (_t(ta, "recorded (not shown by this assistant)", "பதிவாகியுள்ளது (இங்கு காட்டப்படாது)") if r.get("has_aadhaar") else nr))
            elif k == PHOTO:
                ls.append(_t(ta, "Owner photo: ", "உரிமையாளர் புகைப்படம்: ") + (_t(ta, "attached", "இணைக்கப்பட்டுள்ளது") if r.get("has_photo") else _t(ta, "not attached", "இணைக்கப்படவில்லை")))
            elif k == SEX:
                sx = _v(r.get("sex"))
                ls.append(_t(ta, "Sex: ", "பாலினம்: ") + ({"M": _t(ta, "Male", "ஆண்"), "F": _t(ta, "Female", "பெண்")}.get((sx or "").upper(), sx or nr)))
            elif k == GEO:
                ls.append(_t(ta, "District / taluk / town / ward / block: ", "மாவட்டம் / வட்டம் / நகரம் / வார்டு / பிளாக்: ") +
                          " / ".join(_v(r.get(c)) or nr for c in ("district_code", "taluk_code", "town_code", "ward_code", "block_code")))
        out.append((f"{idx} ({_t(ta, 'sub-division', 'உட்பிரிவு')} {r.get('sub_div_no')})" if dup and _v(r.get("sub_div_no")) else (idx if dup else r.get("owner_num")), ls))
    if len(out) == 1 or all(len(l) == 1 for _, l in out) and len(out) == 1:
        ls = out[0][1]
        return f"{ls[0]}  [{app}]" if len(ls) == 1 else f"{app} ({'old' if old else 'new'} owner):\n" + "\n".join("• " + x for x in ls)
    lines: List[str] = []
    for n, ls in out:
        lines.append(_t(ta, f"{'Old' if old else 'New'} owner {n}:", f"{'பழைய' if old else 'புதிய'} உரிமையாளர் {n}:"))
        lines += ["   " + x for x in ls]
    return f"{app} ({'old' if old else 'new'} owners):\n" + "\n".join(lines)


def _eq(a, b) -> str:
    a, b = _v(a), _v(b)
    if not a or not b:
        return "n/a"
    return "match" if str(a).strip().lower() == str(b).strip().lower() else "differs"


async def answer_compare(p: Dict[str, Any], app: str, new: List[Dict[str, Any]], old: List[Dict[str, Any]],
                         igrs_rows: List[Dict[str, Any]], fline: Optional[Dict[str, Any]], subs: List[Dict[str, Any]],
                         detail: Dict[str, Any], ta: bool) -> str:
    if not new:
        return answer_fields([], app, [], ta)
    nr = _nr(ta)
    ws, tg = p["ws"], p["targets"]
    topic = ws & {"name", "patta", "survey", "subdivision", "share", "extent", "uds", "relation", "relative", "mobile", "aadhaar",
                  "status", "person", "district", "taluk", "town", "ward", "block", "actually"}
    all_ = not topic
    L: List[str] = []

    def want(*k):
        return all_ or bool(ws & set(k))

    want_old = "old" in tg or not (tg - {"application"})
    for i, n in enumerate(new):
        tag = (f"[{i + 1}" + (f", {_t(ta, 'sub-division', 'உட்பிரிவு')} {n.get('sub_div_no')}" if _v(n.get("sub_div_no")) else "") + "] ") if len(new) > 1 else ""
        # pair by position when the owner numbers repeat (both rows of a split carry owner no 1)
        o = (old[i] if len(old) == len(new) else next((x for x in old if str(x.get("owner_num")) == str(n.get("owner_num"))), old[0] if old else None)) if old else None
        ig_ = ig_row = next((x for x in igrs_rows if str(x.get("own_num")) == str(n.get("owner_num"))), igrs_rows[0] if igrs_rows else None)
        if "change" in ws or "stay" in ws or "stayed" in ws or want("name", "person", "actually"):
            if want_old:
                if o:
                    L.append(tag + _row(_t(ta, "Name (new vs old owner)", "பெயர் (புதிய vs பழைய உரிமையாளர்)"), n.get("owner_name") or n.get("owner_ename"),
                                        o.get("owner_name") or o.get("owner_ename"),
                                        _name_cmp(n.get("owner_name") or n.get("owner_ename"), o.get("owner_name") or o.get("owner_ename")), nr))
                else:
                    L.append(tag + _t(ta, "– no old-owner record to compare the name with", "– பெயரை ஒப்பிட பழைய உரிமையாளர் பதிவு இல்லை"))
            if "igrs" in tg:
                if ig_row:
                    L.append(tag + _row(_t(ta, "Name (new owner vs IGRS owner)", "பெயர் (புதிய உரிமையாளர் vs IGRS)"), n.get("owner_name"),
                                        ig_row.get("owner") or ig_row.get("owner_english"),
                                        _name_cmp(n.get("owner_name"), ig_row.get("owner") or ig_row.get("owner_english")), nr))
        if want("relation", "relative") and o and want_old:
            L.append(tag + _row(_t(ta, "Relative name (new vs old)", "உறவினர் பெயர் (புதிய vs பழைய)"), n.get("relative_name"), o.get("relative_name"),
                                _name_cmp(n.get("relative_name"), o.get("relative_name")), nr))
            L.append(tag + _row(_t(ta, "Relation code (new vs old)", "உறவு குறியீடு (புதிய vs பழைய)"), n.get("relation_code"), o.get("relation_code"),
                                _eq(n.get("relation_code"), o.get("relation_code")), nr))
        if want("relation", "relative") and "igrs" in tg and ig_row:
            L.append(tag + _row(_t(ta, "Relative (new vs IGRS)", "உறவினர் (புதிய vs IGRS)"), n.get("relative_name"), ig_row.get("relative"),
                                _name_cmp(n.get("relative_name"), ig_row.get("relative")), nr))
            L.append(tag + _row(_t(ta, "Relation code (new vs IGRS)", "உறவு குறியீடு (புதிய vs IGRS)"), n.get("relation_code"), ig_row.get("reltn"),
                                _eq(n.get("relation_code"), ig_row.get("reltn")), nr))
        if want("patta") and (o or "fline" in tg):
            if o:
                L.append(tag + _row(_t(ta, "Patta no (new vs old)", "பட்டா எண் (புதிய vs பழைய)"), n.get("patta_no"), o.get("patta_no"),
                                    _eq(n.get("patta_no"), o.get("patta_no")), nr))
            if "fline" in tg and fline:
                L.append(tag + _row(_t(ta, "Patta no (new vs F-Line)", "பட்டா எண் (புதிய vs F-Line)"), n.get("patta_no"), fline.get("patta_no"),
                                    _eq(n.get("patta_no"), fline.get("patta_no")), nr))
        if want("survey", "subdivision"):
            if "fline" in tg or "fline" in ws:
                dsn0 = detail.get("survey_number")
                L.append(tag + _row(_t(ta, "Survey no (new owner vs application)", "சர்வே எண் (புதிய உரிமையாளர் vs விண்ணப்பம்)"), n.get("survey_no"), dsn0, _eq(n.get("survey_no"), dsn0), nr))
                if fline:
                    L.append(tag + _row(_t(ta, "Survey no (new owner vs F-Line)", "சர்வே எண் (புதிய உரிமையாளர் vs F-Line)"), n.get("survey_no"), fline.get("survey_no"),
                                        _eq(n.get("survey_no"), fline.get("survey_no")), nr))
                else:
                    L.append(tag + _t(ta, "– no F-Line record is held for this application (only F-Line, 0178, applications have one)",
                                      "– இந்த விண்ணப்பத்துக்கு F-Line பதிவு இல்லை (0178 விண்ணப்பங்களுக்கு மட்டுமே)"))
            if "subdiv" in tg or ("subdivision" in ws and "record" in ws):
                if subs:
                    L.append(tag + _row(_t(ta, "Sub-division no (new owner vs record)", "உட்பிரிவு எண் (புதிய உரிமையாளர் vs பதிவு)"), n.get("sub_div_no"),
                                        ", ".join(str(s.get("temp_sub_div_no")) for s in subs), "n/a", nr))
                else:
                    L.append(tag + _t(ta, "– no temp sub-division record is held for this application (only ISD files have one)",
                                      "– இந்த விண்ணப்பத்துக்கு உட்பிரிவு பதிவு இல்லை (ISD கோப்புகளுக்கு மட்டுமே)"))
            if not (tg & {"fline", "subdiv"}) and "fline" not in ws:
                dsn = detail.get("survey_number")
                L.append(tag + _row(_t(ta, "Survey no (new owner vs application)", "சர்வே எண் (புதிய உரிமையாளர் vs விண்ணப்பம்)"), n.get("survey_no"), dsn, _eq(n.get("survey_no"), dsn), nr))
        if want("share") and o:
            L.append(tag + _row(_t(ta, "Share (new vs old)", "பங்கு (புதிய vs பழைய)"), n.get("share"), o.get("share"), _eq(n.get("share"), o.get("share")), nr))
        if want("extent", "uds"):
            if "subdiv" in tg:
                tot = sum(float(s.get("area_hec") or 0) * 10000 + float(s.get("area_ares") or 0) * 100 + float(s.get("area_sqmtr") or 0) for s in subs) if subs else None
                L.append(tag + _t(ta, f"– Extent {_v(n.get('extent')) or nr}; sub-division area " + (f"{tot:g} sq.m" if tot else "no sub-division record held for this file"),
                                  f"– பரப்பளவு {_v(n.get('extent')) or nr}; உட்பிரிவு பரப்பு " + (f"{tot:g} ச.மீ" if tot else "பதிவு இல்லை")))
            if o:
                L.append(tag + _row(_t(ta, "Extent (new vs old)", "பரப்பளவு (புதிய vs பழைய)"), n.get("extent"), o.get("extent"), _eq(n.get("extent"), o.get("extent")), nr))
                L.append(tag + _row(_t(ta, "UDS (new vs old)", "UDS (புதிய vs பழைய)"), n.get("uds"), o.get("uds"), _eq(n.get("uds"), o.get("uds")), nr))
            if "uds" in ws and not _v(n.get("uds")) and not _v(n.get("extent")):
                L.append(tag + _t(ta, "– both the UDS and the extent are blank in the new-owner record, so they cannot be checked against each other",
                                  "– புதிய உரிமையாளர் பதிவில் UDS மற்றும் பரப்பளவு இரண்டும் காலி; ஒன்றுடன் ஒன்று சரிபார்க்க முடியாது"))
        if want("aadhaar") and o:
            L.append(tag + _t(ta, f"– Aadhaar: new owner {'recorded' if n.get('has_aadhaar') else nr}; old owner {'recorded' if o.get('has_aadhaar') else nr}"
                                  " (the numbers are never shown here, so whether they are the same cannot be said)",
                              f"– ஆதார்: புதிய {'பதிவு' if n.get('has_aadhaar') else 'இல்லை'}; பழைய {'பதிவு' if o.get('has_aadhaar') else 'இல்லை'}"))
        if want("mobile"):
            L.append(tag + _t(ta, f"– Mobile: new owner {_v(n.get('mobile_number')) or nr}; the old-owner table has no mobile column",
                              f"– கைபேசி: புதிய உரிமையாளர் {_v(n.get('mobile_number')) or nr}; பழைய உரிமையாளர் பதிவில் அந்த புலம் இல்லை"))
        if want("district", "taluk", "town", "ward", "block") and o:
            for ck, lab in (("district_code", "District"), ("taluk_code", "Taluk"), ("town_code", "Town"), ("ward_code", "Ward"), ("block_code", "Block")):
                if all_ or lab.lower() in ws:
                    L.append(tag + _row(_t(ta, f"{lab} code (new vs old)", f"{lab} குறியீடு (புதிய vs பழைய)"), n.get(ck), o.get(ck), _eq(n.get(ck), o.get(ck)), nr))
        if want("status") and o:
            L.append(tag + _row(_t(ta, "Owner record status code (new vs old)", "உரிமையாளர் பதிவு நிலை குறியீடு (புதிய vs பழைய)"),
                                n.get("status_owner"), o.get("status_owner"), _eq(n.get("status_owner"), o.get("status_owner")), nr))
        if want("status"):
            L.append(tag + _t(ta, f"Owner record status code: {_v(n.get('status_owner')) or nr}; application status: {detail.get('status')} (stage {detail.get('stage')}). "
                                  "The register does not define the owner status code, so the two cannot be reconciled beyond showing both.",
                              f"உரிமையாளர் பதிவு நிலை குறியீடு: {_v(n.get('status_owner')) or nr}; விண்ணப்ப நிலை: {detail.get('status')}. குறியீட்டின் பொருள் பதிவேட்டில் இல்லை."))
        if "change" in ws and o:
            for k, lab in ((OWNER, "Name"), (RELATIVE, "Relative"), (RELATION, "Relation"), (PATTA, "Patta no"), (SHARE, "Share"), (EXTENT, "Extent"), (UDS, "UDS"), (SEX, "Sex")):
                a, b = _v(n.get(k)), _v(o.get(k))
                if a and b and a.strip().lower() != b.strip().lower() and not (k in (OWNER, RELATIVE) and _name_cmp(a, b) == "match"):
                    L.append(tag + f"✘ {lab}: {b} → {a}")
    if "igrs" in tg and not igrs_rows:
        L.append(_t(ta, "No IGRS owner record is held for this application, so there is nothing to compare the new owner with.",
                    "இந்த விண்ணப்பத்துக்கு IGRS உரிமையாளர் பதிவு இல்லை; புதிய உரிமையாளரை ஒப்பிட எதுவும் இல்லை."))
    if want_old and not old:
        L.append(_t(ta, "No old-owner record is held for this application.", "இந்த விண்ணப்பத்துக்கு பழைய உரிமையாளர் பதிவு இல்லை."))
    if "change" in ws and not any(x.startswith(("✘", "[")) and "✘" in x for x in L):
        L.append(_t(ta, "No field changed between the old and the new owner records.", "பழைய மற்றும் புதிய உரிமையாளர் பதிவுகளுக்கிடையே எந்த புலமும் மாறவில்லை."))
    if ws & {"same", "different", "difference"} and want("name", "person") or "same" in ws:
        L.append(_t(ta, "Note: in a patta transfer the new owner normally differs from the old owner.", "குறிப்பு: பட்டா மாற்றத்தில் புதிய உரிமையாளர் பொதுவாக பழைய உரிமையாளரிடமிருந்து வேறுபடுவார்."))
    seen = set()
    L = [x for x in L if x and not (x in seen or seen.add(x))]
    if ws & {"inconsistent", "inconsistant"}:
        bad = [x for x in L if "✘" in x[:30]]
        L = bad or [_t(ta, "No inconsistent detail was found between the old and the new owner records (blank fields cannot be compared).",
                       "பழைய, புதிய உரிமையாளர் பதிவுகளுக்கிடையே முரணான விவரம் இல்லை (காலி புலங்களை ஒப்பிட இயலாது).")]
    if ws & {"stay", "stayed", "unchanged"}:
        same = [x for x in L if "✔" in x[:30]]
        L = same or [_t(ta, "No compared field is identical between the old and the new owner.",
                        "ஒப்பிட்ட எந்த புலமும் பழைய, புதிய உரிமையாளருக்கு ஒன்றாக இல்லை.")]
    if "change" in ws:
        L = [x for x in L if "✘" in x[:8] or x.startswith("[") and "✘" in x[:30] or "→" in x or not x.startswith(("✔", "◐", "–", "[", "Owner", "Name", "Relative", "Relation", "Patta", "Survey", "Share", "Extent", "UDS", "Mobile", "Aadhaar"))]
        L = [x for x in L if "→" in x or x.startswith(("No ", "Note", "No field", "பழைய", "இந்த", "குறிப்பு"))] or L
    if not L:
        return ""
    L.append(_t(ta, "(✔ match, ◐ similar, ✘ differs, – not comparable)", "(✔ பொருந்தும், ◐ ஒத்தது, ✘ வேறுபடும், – ஒப்பிட இயலாது)"))
    return f"{app}:\n" + "\n".join("• " + x for x in L)


_RLABEL = dict(_LABEL)
_RLABEL[OWNER] = ("Returned owner", "திருப்பப்பட்ட உரிமையாளர்")


def _no_return(app: str, total: int, ta: bool) -> str:
    return _t(ta, f"No returned-owner record is held for application {app}"
                  + (" (the returned-owner table holds no records for any application at present)." if total == 0 else
                     ". Only an application that was returned carries one."),
              f"விண்ணப்பம் {app}-க்கு திருப்பப்பட்ட உரிமையாளர் பதிவு இல்லை"
              + (" (திருப்பப்பட்ட உரிமையாளர் அட்டவணையில் தற்போது எந்த விண்ணப்பத்துக்கும் பதிவு இல்லை)." if total == 0 else "."))


def answer_return_fields(keys: List[str], app: str, rows: List[Dict[str, Any]], total: int, info: Optional[Dict[str, Any]],
                         detail: Dict[str, Any], ta: bool) -> str:
    if not rows:
        extra = ""
        if REASON in keys:
            rem = _v((info or {}).get("remarks"))
            extra = _t(ta, " The returned-owner record has no reason column; the application's own remarks"
                           + (f" read: {rem}" if rem else " are blank") + ".",
                       " திருப்பப்பட்ட உரிமையாளர் பதிவில் காரணப் புலம் இல்லை; விண்ணப்பக் குறிப்புகள்" + (f": {rem}" if rem else " காலி") + ".")
        if OVERALL in keys or (STATUS in keys and OVERALL in keys):
            extra += _t(ta, f" Overall application status: {detail.get('status')} (stage {detail.get('stage')}).",
                        f" ஒட்டுமொத்த விண்ணப்ப நிலை: {detail.get('status')} ({detail.get('stage')}).")
        return _no_return(app, total, ta) + extra
    nr = _nr(ta)
    out: List[str] = []
    for idx, r_ in enumerate(rows, 1):
        ls: List[str] = []
        for k in keys:
            if k == UPDATED:
                ls.append(_t(ta, "Last updated: ", "கடைசியாக புதுப்பித்தது: ") + ((_v(r_.get("update_dt")) or nr)[:19]))
            elif k == REASON:
                ls.append(_t(ta, "Reason: the returned-owner record has no reason column", "காரணம்: திருப்பப்பட்ட பதிவில் காரணப் புலம் இல்லை"))
            elif k == OVERALL:
                ls.append(_t(ta, f"Record status: {_v(r_.get('status')) or nr}; owner status code: {_v(r_.get('status_owner')) or nr}; "
                                 f"application status: {detail.get('status')}", f"பதிவு நிலை: {_v(r_.get('status')) or nr}; விண்ணப்ப நிலை: {detail.get('status')}"))
            elif k == STATUS:
                ls.append(_t(ta, "Owner status code: ", "உரிமையாளர் நிலை குறியீடு: ") + (_v(r_.get("status_owner")) or nr)
                          + _t(ta, f"; record status: {_v(r_.get('status')) or nr}", f"; பதிவு நிலை: {_v(r_.get('status')) or nr}"))
            elif k in _RLABEL:
                ls.append(f"{_t(ta, *_RLABEL[k])}: {_v(r_.get(k)) or nr}")
            elif k == AADHAAR:
                ls.append(_t(ta, "Aadhaar: ", "ஆதார்: ") + (_t(ta, "recorded (not shown)", "பதிவாகியுள்ளது (காட்டப்படாது)") if r_.get("has_aadhaar") else nr))
            elif k == PHOTO:
                ls.append(_t(ta, "Owner photo: ", "உரிமையாளர் புகைப்படம்: ") + (_t(ta, "attached", "இணைக்கப்பட்டுள்ளது") if r_.get("has_photo") else _t(ta, "not attached", "இணைக்கப்படவில்லை")))
            elif k == SEX:
                sx = _v(r_.get("sex"))
                ls.append(_t(ta, "Sex: ", "பாலினம்: ") + ({"M": _t(ta, "Male", "ஆண்"), "F": _t(ta, "Female", "பெண்")}.get((sx or "").upper(), sx or nr)))
            elif k == GEO:
                ls.append(_t(ta, "District / taluk / town / ward / block: ", "மாவட்டம் / வட்டம் / நகரம் / வார்டு / பிளாக்: ")
                          + " / ".join(_v(r_.get(c)) or nr for c in ("district_code", "taluk_code", "town_code", "ward_code", "block_code")))
        out.append((idx, ls))
    if len(out) == 1 and len(out[0][1]) == 1:
        return f"{out[0][1][0]}  [{app}]"
    lines: List[str] = []
    for n, ls in out:
        lines.append(_t(ta, f"Returned owner {n}:", f"திருப்பப்பட்ட உரிமையாளர் {n}:"))
        lines += ["   " + x for x in ls]
    return f"{app} (returned owners):\n" + "\n".join(lines)


def answer_return_compare(p: Dict[str, Any], app: str, rows: List[Dict[str, Any]], total: int, new: List[Dict[str, Any]],
                          old: List[Dict[str, Any]], igrs_rows: List[Dict[str, Any]], detail: Dict[str, Any], ta: bool) -> str:
    if not rows:
        return _no_return(app, total, ta) + _t(ta, " There is nothing to compare.", " ஒப்பிட எதுவும் இல்லை.")
    nr = _nr(ta)
    ws = p["ws"]
    tg = []
    if ws & {"old", "chitta"} or not ws & {"new", "igrs", "application"}:
        tg.append(("old", old, _t(ta, "old owner", "பழைய உரிமையாளர்")))
    if "new" in ws or "across" in ws:
        tg.append(("new", new, _t(ta, "new owner", "புதிய உரிமையாளர்")))
    if "igrs" in ws:
        tg.append(("igrs", igrs_rows, "IGRS"))
    L: List[str] = []
    for i, rr in enumerate(rows):
        tag = f"[{i + 1}] " if len(rows) > 1 else ""
        for key, other, lab in tg:
            o = (other[i] if len(other) == len(rows) else (other[0] if other else None)) if other else None
            if not o:
                L.append(tag + _t(ta, f"– no {lab} record to compare with", f"– ஒப்பிட {lab} பதிவு இல்லை"))
                continue
            nm = o.get("owner_name") or o.get("owner") or o.get("owner_english")
            L.append(tag + _row(_t(ta, f"Name (returned vs {lab})", f"பெயர் (திருப்பப்பட்டது vs {lab})"), rr.get("owner_name"), nm, _name_cmp(rr.get("owner_name"), nm), nr))
            L.append(tag + _row(_t(ta, f"Patta no (returned vs {lab})", f"பட்டா எண் (திருப்பப்பட்டது vs {lab})"), rr.get("patta_no"),
                                o.get("patta_no"), _eq(rr.get("patta_no"), o.get("patta_no")), nr))
            for k, lb in (("share", "Share"), ("extent", "Extent"), ("uds", "UDS")):
                if k in o:
                    L.append(tag + _row(f"{lb} (returned vs {lab})", rr.get(k), o.get(k), _eq(rr.get(k), o.get(k)), nr))
            rel = o.get("relative_name") or o.get("relative")
            L.append(tag + _row(_t(ta, f"Relative (returned vs {lab})", f"உறவினர் (திருப்பப்பட்டது vs {lab})"), rr.get("relative_name"), rel, _name_cmp(rr.get("relative_name"), rel), nr))
    L.append(_t(ta, "(✔ match, ◐ similar, ✘ differs, – not comparable)", "(✔ பொருந்தும், ◐ ஒத்தது, ✘ வேறுபடும், – ஒப்பிட இயலாது)"))
    return f"{app}:\n" + "\n".join("• " + x for x in L)
