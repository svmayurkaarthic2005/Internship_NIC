"""Parcel-register modification rows of ONE application (`uareg_modification`): what a correction /
modification application (service codes 0158, 0159, 0160, 0162, 0164, 0169) changes in the survey register --
survey / sub-division (and the old survey no), door, govt / private, land type and use, irrigation, tax, soil,
extent, patta numbers, remarks, approval file, reject reason, who updated it and when.

One application usually has two rows (operation codes 01 and 04; 0164 files more). The register does not say
which operation is the "before" and which the "after", so "what changed" compares the rows field by field and
states exactly that. Codes (operation, status, govt/private, land type, reject reason) are shown raw and said to
be undefined. A blank column is "not recorded". Only a modification service has rows; an ISD / NISD file says so.
Typos, Tamil and Tanglish are understood. The caller checks access through the register. No LLM."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.services.crosscheck_qa import _v
from backend.utils.fuzzy import is_token_typo_match
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# key -> (label en, label ta, [columns])
FIELDS: Dict[str, Any] = {
    "operation": ("Operation code", "செயல்பாட்டு குறியீடு", ["operation"]),
    "survey": ("Survey no / sub-division no", "சர்வே எண் / உட்பிரிவு எண்", ["survey_no", "subdiv_no"]),
    "old_survey": ("Old survey no / old sub-division no", "பழைய சர்வே எண் / உட்பிரிவு எண்", ["osurvey_no", "osubdiv_no"]),
    "door": ("Door no", "கதவு எண்", ["door_no"]),
    "govt_pri": ("Govt / private code", "அரசு / தனியார் குறியீடு", ["govt_pri"]),
    "land_type": ("Land type code", "நில வகை குறியீடு", ["land_type"]),
    "land_use": ("Land use code / descriptive land use", "நில பயன்பாடு குறியீடு / விளக்கம்", ["land_use", "descriptive_landuse"]),
    "irr": ("Irrigation source", "நீர்ப்பாசன ஆதாரம்", ["irr_source"]),
    "tax": ("Tax rate / tax per hectare / total tax", "வரி விகிதம் / ஹெக்டேருக்கு வரி / மொத்த வரி", ["tax_rate", "tax_hect", "tot_tax"]),
    "soil": ("Soil primary / secondary", "மண் முதன்மை / இரண்டாம் நிலை", ["soil_type_p", "soil_type_s"]),
    "soil_class": ("Soil class", "மண் வகுப்பு", ["soil_class"]),
    "extent": ("Extent unit / u1 / u2 / u3", "பரப்பளவு அலகு / u1 / u2 / u3", ["ext_unit", "ext_u1", "ext_u2", "ext_u3"]),
    "patta": ("Patta no", "பட்டா எண்", ["patta_no"]),
    "occ": ("Occupation code", "தொழில் குறியீடு", ["occ_cd"]),
    "remarks": ("Remarks", "குறிப்புகள்", ["remarks", "remarks1"]),
    "old_new_patta": ("Old patta / new patta", "பழைய பட்டா / புதிய பட்டா", ["oldpatta", "newpatta"]),
    "existing_patta": ("Existing patta no", "தற்போதுள்ள பட்டா எண்", ["existing_patta_no"]),
    "new_patta": ("New patta no", "புதிய பட்டா எண்", ["new_patta_no"]),
    "temp_subdiv": ("Temp sub-division no", "தற்காலிக உட்பிரிவு எண்", ["tempsubdiv_no"]),
    "forms": ("Form 6 / form 7 / form 8 no", "படிவம் 6 / 7 / 8 எண்", ["form6_no", "form7_no", "form8_no"]),
    "relinq_assign": ("Relinquishment no / assignment no", "கைவிடல் எண் / ஒதுக்கீடு எண்", ["relinq_no", "assign_no"]),
    "alien_aquis": ("Alienation no / acquisition no", "அந்நியமாக்கல் எண் / கையகப்படுத்தல் எண்", ["alien_no", "aquis_no"]),
    "assess": ("Assessed / cultivable", "மதிப்பிடப்பட்டது / பயிரிடத்தக்கது", ["assessed", "cultivable"]),
    "municipal": ("Municipal tax", "நகராட்சி வரி", ["municipal_tax"]),
    "approval": ("Approval file no / date", "ஒப்புதல் கோப்பு எண் / தேதி", ["approval_file_no", "approval_file_date"]),
    "status": ("Record status / application status code", "பதிவு நிலை / விண்ணப்ப நிலை குறியீடு", ["status", "appl_status"]),
    "reject": ("Reject reason code", "நிராகரிப்பு காரண குறியீடு", ["reject_reason_code"]),
    "who": ("Updated by", "புதுப்பித்தவர்", ["username"]),
    "when": ("Updated on", "புதுப்பித்த தேதி", ["updn_dt"]),
    "flow": ("Flow type", "பணிப்பாய்வு வகை", ["flowtype"]),
    "street": ("Citizen street name", "குடிமகன் தெரு பெயர்", ["street_name_citizen"]),
    "coss": ("COSS block code", "COSS பிளாக் குறியீடு", ["coss_block_code"]),
}
_CODE_KEYS = {"operation", "govt_pri", "land_type", "status", "reject"}

_TAMIL = [("மாற்றம்", " modification "), ("திருத்தம்", " modification "), ("சர்வே", " survey "), ("உட்பிரிவு", " subdivision "),
          ("பட்டா", " patta "), ("கதவு எண்", " door number "), ("நில வகை", " land type "), ("நில பயன்பாடு", " land use "),
          ("வரி", " tax "), ("மண்", " soil "), ("பரப்பளவு", " extent "), ("குறிப்பு", " remarks "), ("நிலை", " status "),
          ("ஒப்புதல்", " approval "), ("நிராகரி", " reject "), ("ஒப்பிடு", " compare "), ("பொருந்து", " match "), ("எண்", " number "),
          ("பழைய", " old "), ("புதிய", " new "), ("தேதி", " date "), ("காரணம்", " reason ")]
_EXACT = {
    "modifcation": "modification", "modificaton": "modification", "modifaction": "modification", "modificatn": "modification",
    "operashun": "operation", "operation": "operation", "survay": "survey", "servey": "survey", "divison": "division", "extant": "extent",
    "extend": "extent", "typ": "type", "numbr": "number", "no": "number", "num": "number", "applcation": "application",
    "aplication": "application", "remaks": "remarks", "remark": "remarks", "rejetced": "rejected", "rejcted": "rejected", "aquisition": "acquisition",
    "aquis": "acquisition", "alienation": "alienation", "relinq": "relinquishment", "relinquish": "relinquishment", "irigation": "irrigation",
    "municple": "municipal", "muncipal": "municipal", "stauts": "status", "upadte": "update", "updatd": "update", "updated": "update",
    "differnces": "difference", "differences": "difference", "diffrence": "difference", "consistant": "consistent", "mismach": "mismatch",
    "mismaches": "mismatch", "changed": "change", "changes": "change", "modified": "modified", "unchanged": "unchanged", "remained": "remained",
    "form7": "form7", "form8": "form8", "form6": "form6", "coss": "coss", "flowtype": "flowtype", "uds": "uds", "assessed": "assessed",
    "cultivable": "cultivable", "oldpatta": "oldpatta", "newpatta": "newpatta", "occupashun": "occupation", "occupation": "occupation",
    "ownr": "owner", "you1": "u1", "you2": "u2", "you3": "u3", "agree": "match", "matches": "match", "compair": "compare",
}
_VOCAB = ["modification", "operation", "irrigation", "municipal", "rejected", "acquisition", "relinquishment", "alienation",
          "occupation", "difference", "consistent", "mismatch", "extent", "remarks", "cultivable", "assessed"]


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
    t = re.sub(r"\btemp(?:orary)?[\s\-_]*sub[\s\-_]*d[a-z]*v[a-z]*", " tempsubdivision ", t)
    t = re.sub(r"\bform[\s\-_]*([678])\b", r" form\1 ", t)
    t = re.sub(r"\bexisting[\s\-_]*patta\b", " existingpatta ", t)
    t = re.sub(r"\bnew[\s\-_]*patta\b", " newpatta ", t)
    t = re.sub(r"\bold[\s\-_]*patta\b", " oldpatta ", t)
    t = re.sub(r"\bext[\s\-_]*(?:u|you)([123])\b", r" u\1 ", t)
    t = re.sub(r"[?!.:()\"',;&]", " ", t).replace("/", " ")
    return re.sub(r"\s+", " ", " ".join(_canon(x) for x in re.findall(r"[a-z0-9]+|[^\sa-z0-9]+", t))).strip()


def parse(message: str) -> Optional[Dict[str, Any]]:
    if "everyfield" in (message or "").lower() and "parcel" in (message or "").lower():  # "which fields are blank": every column, the blanks are picked out afterwards
        return {"mode": "field", "keys": list(FIELDS), "ws": {"modification"}, "explicit": True}
    """None unless the message asks about a parcel modification (the word "modification" -- or a column only this
    record carries -- must be there)."""
    ws = set(normalise(message).split())
    specific = bool(ws & {"operation", "irrigation", "municipal", "form6", "form7", "form8", "relinquishment", "alienation",
                          "acquisition", "coss", "flowtype", "oldpatta", "newpatta", "existingpatta", "tempsubdivision"})
    if "approval" in ws and "file" in ws:
        specific = True
    if "modified" in ws and ws & {"fields", "field"} and "application" in ws:
        ws = ws | {"modification"}
    if "modification" not in ws and not specific:
        return None
    keys: List[str] = []

    def add(k):
        if k not in keys:
            keys.append(k)

    if "operation" in ws:
        add("operation")
    if ws & {"survey"} and "old" in ws:
        add("old_survey")
    elif "survey" in ws or "subdivision" in ws:
        add("survey")
    if "old" in ws and "subdivision" in ws:
        add("old_survey")
    if "door" in ws:
        add("door")
    if ws & {"govt", "private", "government"}:
        add("govt_pri")
    if "land" in ws and "type" in ws:
        add("land_type")
    if "land" in ws and "use" in ws or "descriptive" in ws:
        add("land_use")
    if "irrigation" in ws:
        add("irr")
    if "tax" in ws and "municipal" not in ws:
        add("tax")
    if "soil" in ws and "class" not in ws:
        add("soil")
    if "soil" in ws and "class" in ws:
        add("soil_class")
    if ws & {"extent", "u1", "u2", "u3"}:
        add("extent")
    if "patta" in ws and not ws & {"oldpatta", "newpatta", "existingpatta"}:
        add("patta")
    if "occupation" in ws:
        add("occ")
    if "remarks" in ws:
        add("remarks")
    if ws & {"oldpatta", "newpatta"}:
        add("old_new_patta")
    if "existingpatta" in ws:
        add("existing_patta")
    if "new" in ws and "patta" in ws and "number" in ws:
        add("new_patta")
    if "tempsubdivision" in ws:
        add("temp_subdiv")
    if ws & {"form6", "form7", "form8"}:
        add("forms")
    if ws & {"relinquishment"} or ("assign" in ws):
        add("relinq_assign")
    if ws & {"alienation", "acquisition"}:
        add("alien_aquis")
    if ws & {"assessed", "cultivable"}:
        add("assess")
    if "municipal" in ws:
        add("municipal")
    if "approval" in ws and "file" in ws:
        add("approval")
    if "reject" in ws or "rejected" in ws:
        add("reject")
    if "status" in ws and not ws & {"correction", "workflow"} and "reject" not in ws and "rejected" not in ws:
        add("status")
    if "update" in ws and ws & {"who", "by"} or ("who" in ws and "update" in ws):
        add("who")
    elif "update" in ws and "date" in ws:
        add("when")
    if "flowtype" in ws or ("flow" in ws and "type" in ws):
        add("flow")
    if "street" in ws and "citizen" in ws:
        add("street")
    if "coss" in ws:
        add("coss")
    mode = "field"
    if ws & {"compare", "match", "mismatch", "difference", "change", "modified", "unchanged", "remained", "consistent", "exist"} or "changed" in ws:
        mode = "compare"
    if "status" in ws and ws & {"correction", "workflow"}:
        mode = "status_flow"
    if "exist" in ws and "approval" in ws:
        mode = "field"
    if not keys and mode == "field":
        mode = "exists"
    return {"mode": mode, "keys": keys, "ws": ws, "explicit": "modification" in ws}


def claims(message: str) -> bool:
    return parse(message) is not None


_COLS = sorted({c for v in FIELDS.values() for c in v[2]} | {"slno"})
_SQL = f"SELECT {', '.join(_COLS)} FROM uareg_modification WHERE appl_id = :a ORDER BY slno, row_id"


async def load(db, app: str) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    try:
        async with db.begin_nested():
            return [dict(r) for r in (await db.execute(text(_SQL), {"a": app})).mappings().all()]
    except Exception as exc:
        logger.warning(f"uamod_qa: could not read uareg_modification ({type(exc).__name__})")
        return []


def _t(ta, en, tamil):
    return tamil if ta else en


def _val(r: Dict[str, Any], key: str, nr: str) -> str:
    cols = FIELDS[key][2]
    vals = [_v(r.get(c)) for c in cols]
    if key == "extent":
        return "ext_unit " + (vals[0] or nr) + f"; u1 {vals[1] or '0'} / u2 {vals[2] or '0'} / u3 {vals[3] or '0'}"
    if key == "approval" and vals[1]:
        vals[1] = vals[1][:10]
    if key == "when" and vals[0]:
        vals[0] = vals[0][:19]
    return " / ".join(x or nr for x in vals)


def _no_rows(app: str, ta: bool) -> str:
    return _t(ta, f"No parcel-modification record is held for application {app}. Only a correction / modification application (0158, 0159, 0160, 0162, 0164, 0169) carries one.",
              f"விண்ணப்பம் {app}-க்கு நிலப்பதிவு மாற்ற பதிவு இல்லை. திருத்தம் / மாற்ற விண்ணப்பத்துக்கு (0158, 0159, 0160, 0162, 0164, 0169) மட்டுமே அது இருக்கும்.")


def _tag(r: Dict[str, Any], n: int) -> str:
    return f"[op {r.get('operation')}, slno {r.get('slno')}] " if n > 1 else ""


def answer(p: Dict[str, Any], app: str, rows: List[Dict[str, Any]], reg: Optional[Dict[str, Any]], ta: bool) -> str:
    nr = _t(ta, "not recorded", "பதிவில்லை")
    if not rows:
        return _no_rows(app, ta)
    n = len(rows)
    mode, keys, ws = p["mode"], p["keys"], p["ws"]
    L: List[str] = []
    if mode == "exists":
        return _t(ta, f"Yes -- {n} parcel-modification row(s) are held for {app} (operation codes {', '.join(sorted({str(r.get('operation')) for r in rows}))}).",
                  f"ஆம் -- {app}-க்கு {n} நிலப்பதிவு மாற்ற பதிவு(கள்) உள்ளன.")
    if mode == "status_flow":
        st = (reg or {}).get("status")
        L.append(_t(ta, f"Application status: {st}; modification row status code(s): {', '.join(sorted({str(r.get('appl_status')) for r in rows}))}, "
                        f"record status {', '.join(sorted({_v(r.get('status')) or nr for r in rows}))}.",
                    f"விண்ணப்ப நிலை: {st}."))
        L.append(_t(ta, "The correction / modification flow carries a desk chain (recommendations) but no status column of its own, so there is no flow status to compare; the status codes above are not defined by the register.",
                    "திருத்தம் / மாற்ற பணிப்பாய்வில் தனி நிலை புலம் இல்லை; ஒப்பிட நிலை இல்லை."))
        return f"{app}:\n" + "\n".join("• " + x for x in L)
    if mode == "field":
        for r in rows:
            tg = _tag(r, n)
            for k in keys:
                lab = _t(ta, FIELDS[k][0], FIELDS[k][1])
                v = _val(r, k, nr)
                if k in _CODE_KEYS and any(_v(r.get(c)) for c in FIELDS[k][2]):
                    v += _t(ta, " (a code the register does not define)", " (பதிவேடு வரையறுக்காத குறியீடு)")
                L.append(f"{tg}{lab}: {v}")
        L = list(dict.fromkeys(L))
        return L[0] + f"  [{app}]" if len(L) == 1 else f"{app} (parcel modification):\n" + "\n".join("• " + x for x in L)
    # compare ---------------------------------------------------------------
    sym = {"same": "✔", "diff": "✘", "na": "–"}
    ks = [k for k in keys if k in FIELDS] or [k for k in FIELDS if k not in ("operation", "when", "who", "status", "approval", "reject")]
    narrowed = bool(keys)
    # 1) the columns that name the old / new value on one row
    if "old_survey" in keys or ("survey" in keys and "old" in ws) or (not narrowed):
        for r in rows[:1]:
            a, b = _v(r.get("osurvey_no")), _v(r.get("survey_no"))
            L.append(f"{sym['na'] if not (a and b) else (sym['same'] if a == b else sym['diff'])} " +
                     _t(ta, f"Old survey no vs survey no: {a or nr}  |  {b or nr}", f"பழைய சர்வே vs சர்வே: {a or nr}  |  {b or nr}"))
            a, b = _v(r.get("osubdiv_no")), _v(r.get("subdiv_no"))
            L.append(f"{sym['na'] if not (a and b) else (sym['same'] if a == b else sym['diff'])} " +
                     _t(ta, f"Old sub-division no vs sub-division no: {a or nr}  |  {b or nr}", f"பழைய உட்பிரிவு vs உட்பிரிவு: {a or nr}  |  {b or nr}"))
    patta_only = bool(keys) and set(keys) <= {"old_new_patta", "existing_patta", "new_patta"}
    if "old_new_patta" in keys or "existing_patta" in keys or "new_patta" in keys:
        for r in rows[:1]:
            for lab, a, b in (("Existing patta no vs oldpatta", r.get("existing_patta_no"), r.get("oldpatta")),
                              ("New patta no vs newpatta", r.get("new_patta_no"), r.get("newpatta")),
                              ("oldpatta vs newpatta", r.get("oldpatta"), r.get("newpatta"))):
                a, b = _v(a), _v(b)
                L.append(f"{sym['na'] if not (a and b) else (sym['same'] if a == b else sym['diff'])} {lab}: {a or nr}  |  {b or nr}")
            L.append(_t(ta, f"Patta no {_v(rows[0].get('patta_no')) or nr}; existing patta no {_v(rows[0].get('existing_patta_no')) or nr}; new patta no {_v(rows[0].get('new_patta_no')) or nr}; "
                            f"temp sub-division {_v(rows[0].get('tempsubdiv_no')) or nr} (the oldpatta / newpatta columns are blank in every record).",
                        f"பட்டா {_v(rows[0].get('patta_no')) or nr}; புதிய பட்டா {_v(rows[0].get('new_patta_no')) or nr}."))
    # 2) row against row (operation 04 vs 01 ...)
    if n >= 2 and not patta_only:
        a, b = rows[0], rows[1]
        L.append(_t(ta, f"Comparing row op {a.get('operation')} (slno {a.get('slno')}) with row op {b.get('operation')} (slno {b.get('slno')}); the register does not say which is the before and which the after:",
                    f"வரிசை {a.get('operation')} மற்றும் {b.get('operation')} ஒப்பிடப்படுகிறது; எது முன், எது பின் என்று பதிவேடு கூறவில்லை:"))
        same, diff = [], []
        for k in ks:
            if k in ("old_new_patta",):
                continue
            va, vb = _val(a, k, nr), _val(b, k, nr)
            blank = all(not _v(a.get(c)) and not _v(b.get(c)) for c in FIELDS[k][2])
            if blank:
                continue
            (same if va == vb else diff).append((k, va, vb))
        want_same = bool(ws & {"unchanged", "remained"})
        want_diff = not want_same
        if want_diff:
            for k, va, vb in diff:
                L.append(f"✘ {_t(ta, FIELDS[k][0], FIELDS[k][1])}: {va}  |  {vb}")
            if not diff:
                L.append(_t(ta, "No compared field differs between the two rows.", "ஒப்பிட்ட எந்த புலமும் இரு வரிசைகளுக்கு இடையே மாறவில்லை."))
        if want_same or (ws & {"modified", "change"} is None):
            pass
        if want_same:
            L.append(_t(ta, "Unchanged: ", "மாறாதவை: ") + ", ".join(_t(ta, FIELDS[k][0], FIELDS[k][1]) for k, _, _ in same))
        elif ws & {"modified"}:
            L.append(_t(ta, "Unchanged: ", "மாறாதவை: ") + (", ".join(_t(ta, FIELDS[k][0], FIELDS[k][1]) for k, _, _ in same) or "-"))
    elif n < 2:
        L.append(_t(ta, "Only one parcel row is recorded for this application, so there is no second row to show a before / after difference against.",
                    "இந்த விண்ணப்பத்துக்கு ஒரே ஒரு நிலப்பதிவு வரிசை மட்டுமே உள்ளது; முன் / பின் வேறுபாடு காட்ட இரண்டாவது வரிசை இல்லை."))
    if ws & {"approval", "exist"} and "approval" in keys:
        for r in rows:
            L.append(_tag(r, n) + _t(ta, "Approval file: ", "ஒப்புதல் கோப்பு: ") + _val(r, "approval", nr))
    if "reject" in keys:
        for r in rows:
            L.append(_tag(r, n) + _t(ta, "Reject reason code: ", "நிராகரிப்பு காரண குறியீடு: ") + (_v(r.get("reject_reason_code")) or nr))
    L = list(dict.fromkeys(x for x in L if x))
    return f"{app}:\n" + "\n".join("• " + x for x in L)
