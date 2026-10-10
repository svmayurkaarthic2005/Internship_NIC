"""Old-owner record (chitta_temp_old_owner) questions about ONE application, and
the comparison of that record with the temp sub-division record
(areg_temp_subdivclub).

Understands typos ("applcation", "relashionship", "detials"), Tamil and Tanglish.
Every value comes from the two rows; a blank column is reported as "not recorded",
never guessed. Access is the caller's job: it passes an application detail it
already fetched under the officer's jurisdiction. No LLM."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from backend.utils.fuzzy import is_token_typo_match
from backend.utils.logger import get_logger

logger = get_logger(__name__)

_APP_NO = re.compile(r"\d{4}/\d{3,4}/\d{1,3}/\d+")

_TAMIL = [
    ("பழைய உரிமையாளர்", " old owner "), ("பழைய உரிமை", " old owner "), ("முந்தைய உரிமையாளர்", " old owner "),
    ("உரிமையாளர்", " owner "), ("சொந்தக்காரர்", " owner "), ("பட்டா", " patta "), ("சர்வே", " survey "),
    ("கணக்கெண்", " survey "), ("உட்பிரிவு", " subdivision "), ("புதிய", " new "), ("தற்போதுள்ள", " existing "),
    ("உறவு குறியீடு", " relation code "), ("உறவு", " relation "), ("தந்தை", " relative "), ("கணவர்", " relative "),
    ("உறவினர்", " relative "), ("மற்றும்", " and "),
    ("பங்கு", " share "), ("பரப்பளவு", " extent "), ("பரப்பு", " extent "), ("அளவு", " extent "),
    ("ஆணா", " male "), ("பெண்ணா", " female "), ("ஆண்", " male "), ("பெண்", " female "), ("பாலினம்", " sex "),
    ("ஒப்பிடு", " compare "), ("பொருந்து", " match "), ("வேறுபாடு", " different "), ("முரண்", " conflict "),
    ("சரிபார்", " verify "), ("நிலை", " status "), ("பெயர்", " name "), ("எண்", " number "),
    ("விவரங்கள்", " details "), ("விவரம்", " details "), ("முழு", " complete "), ("மாவட்ட", " district "),
    ("வட்ட", " taluk "), ("வார்டு", " ward "), ("பிளாக்", " block "), ("விண்ணப்ப", " application "),
]
_TANGLISH = [
    (r"pazhaya\s+urimaiyalar|palaya\s+urimaiyalar", "old owner"), (r"urimaiyalar|urimaiyaalar|sondhakkarar", "owner"),
    (r"pazhaya|palaya|pazhaiya", "old"), (r"puthiya|pudhiya", "new"), (r"thandhai|appa|kanavar", "relative"),
    (r"pangu", "share"), (r"parappalavu|parappu|alavu", "extent"), (r"oppidu|oppidhu|oppidavum", "compare"),
    (r"porundhu|porundhudha", "match"), (r"verupaadu|vithyasam", "different"), (r"aan\b", "male"), (r"pen\b", "female"),
    (r"enna|enn|evlo|yaaru|yar\b", ""),
]
_EXACT = {
    "no": "number", "nos": "number", "num": "number", "nmbr": "number", "numbr": "number", "nbr": "number",
    "husband": "relative", "father": "relative", "fathers": "relative", "relative": "relative",
    "relatives": "relative", "relation": "relation", "relationship": "relation", "relashionship": "relation",
    "relashion": "relation", "gender": "sex", "sex": "sex", "male": "male", "female": "female",
    "owners": "owner", "owner": "owner", "chitta": "chitta", "chita": "chitta", "chittaa": "chitta",
    "detail": "details", "details": "details", "detials": "details", "deatils": "details",
    "applcation": "application", "aplication": "application", "application": "application", "apln": "application",
    "sy": "survey", "recrd": "record", "recrds": "records", "rcord": "record", "recod": "record", "irukka": "", "survay": "survey", "servey": "survey", "pata": "patta", "pattaa": "patta",
    "u1": "u1", "u2": "u2", "u3": "u3", "you1": "u1", "you2": "u2", "you3": "u3", "avalable": "available", "avaiable": "available",
    "stauts": "status", "satus": "status", "sub": "sub",
    "mismatches": "mismatch", "mismatched": "mismatch", "matches": "match", "matching": "match",
    "matched": "match", "conflicting": "conflict", "conflicts": "conflict", "differs": "different",
    "difference": "different", "differences": "different", "differ": "different", "different": "different",
    "consistant": "consistent", "consistency": "consistent", "incorect": "incorrect", "wrong": "incorrect",
    "comparing": "compare", "compared": "compare", "comparison": "compare", "verified": "verify",
    "check": "verify", "same": "same", "equal": "same", "identical": "same",
}
_VOCAB = ["owner", "patta", "survey", "number", "relation", "relative", "share", "extent", "status",
          "details", "compare", "match", "consistent", "mismatch", "conflict", "different", "incorrect",
          "verify", "available", "application", "subdivision", "district", "taluk", "ward", "block",
          "town", "area", "chitta", "name", "female", "male", "code", "record", "table", "records"]
_STOP = frozenset("""what is the a an for of on in to me tell give show please pls can you which whose
there any this that its it be are was were do does did also with about from by as at has have had get
need want know see are how whether if then both""".split())
_SPLIT = re.compile(r"\s*(?:,|;|&|\band\b|\bor\b|\bplus\b|\bincluding\b|\bwith\b|\balso\b)\s*", re.IGNORECASE)


def _canon(tok: str) -> str:
    if tok in _EXACT:
        return _EXACT[tok]
    if not tok.isascii() or not tok.isalpha() or len(tok) < 5:
        return tok
    hits = {w for w in _VOCAB if is_token_typo_match(tok, w)}
    return next(iter(hits)) if len(hits) == 1 else tok


def normalise(message: str) -> str:
    t = _APP_NO.sub(" ", message or "").lower()
    for ta, en in _TAMIL:
        t = t.replace(ta, en)
    for rx, en in _TANGLISH:
        t = re.sub(rx, f" {en} ", t)
    t = re.sub(r"\bsub[\s\-_.]*d[a-z]*v[a-z]*", " subdivision ", t)
    t = re.sub(r"[?!.:()\"']", " ", t)
    t = t.replace("'s", "")
    toks = [_canon(x) for x in re.findall(r"[a-z0-9]+|[^\sa-z0-9]+", t)]
    return re.sub(r"\s+", " ", " ".join(toks)).strip()


# ── attributes ──────────────────────────────────────────────────────────────
NAME, PATTA, RELATIVE, RELATION, RELCODE = "name", "patta", "relative", "relation", "relation_code"
OWNERNUM, SHARE, EXTENT, SURVEYSUB, NEWSUB = "owner_no", "share", "extent", "survey_subdiv", "new_subdiv"
STATUS, SEX, AADHAAR, FULL, EXISTS = "status", "sex", "aadhaar", "full", "exists"
COMPARE = "compare"


def _clause(toks: Sequence[str], old: bool) -> List[str]:
    s = set(toks)
    if "relative" in s and "relation" not in s:
        return [RELATIVE]
    if "relation" in s and "code" in s:
        return [RELCODE]
    if "relation" in s:
        return [RELATION]
    if "share" in s:
        return [SHARE]
    if s & {"extent", "u1", "u2", "u3"}:
        return [EXTENT]
    if s & {"male", "female", "sex"}:
        return [SEX]
    if "aadhaar" in s or "aadhar" in s:
        return [AADHAAR]
    if "patta" in s:
        return [PATTA]
    if "subdivision" in s and "new" in s:
        return [NEWSUB]
    if "survey" in s or "subdivision" in s:
        return [SURVEYSUB]
    if "owner" in s and "number" in s:
        return [OWNERNUM]
    if "status" in s:
        return [STATUS]
    if "name" in s and ("owner" in s or old):
        return [NAME]
    if "owner" in s and old and not (s & {"details", "available", "record"}):
        return [NAME]
    return []


_COMPARE_WORDS = {"compare", "match", "consistent", "mismatch", "conflict", "different", "incorrect", "verify"}


def attributes(message: str) -> List[str]:
    """Fields asked about the old-owner record, or [COMPARE] for a comparison with
    the sub-division record. [] when the message is not about this record."""
    text = normalise(message)
    whole = text.split()
    ws = set(whole)
    if "applicant" in ws:
        return []
    # "share?" / "extent?" bare, right after an owner answer -- these are owner-row
    # fields with no application-level equivalent to fall back to (unlike "status?"
    # or "survey?", which correctly answer from the application itself when asked
    # bare), so without this they fell through to the generic application-details
    # card instead of continuing the owner record. newowner_qa's own soft-claim set
    # already treats EXTENT/SHARE the same way; this keeps the two modules consistent.
    ownerish = bool(ws & {"owner", "chitta", "u1", "u2", "u3", "relative", "relation", "share", "extent"}) or (
        "both" in ws and bool(ws & {"table", "tables", "record", "records"}))
    if not ownerish:
        return []
    old = "old" in ws or "chitta" in ws or "record" in ws
    if ws & _COMPARE_WORDS and (old or ws & {"subdivision", "table", "tables", "records", "both"}):
        return [COMPARE]
    if "same" in ws and ws & {"record", "records", "application"} and (
            ws & {"district", "taluk", "town", "ward", "block", "application"}):
        return [COMPARE]
    out: List[str] = []
    for c in _SPLIT.split(text):
        for a in _clause([w for w in c.split() if w not in _STOP], old):
            if a not in out:
                out.append(a)
    if "details" in ws and ws & {"available", "any"} and not out:
        out = [EXISTS]
    elif "details" in ws and ("complete" in ws or old) and not out:
        out = [FULL]
    # a bare "owner name" is the register-owner question the owner handler answers
    # more fully; only an old-owner / record wording is claimed here
    if out == [SURVEYSUB] and not old and "subdivision" not in ws:
        return []
    if out == [NAME] and not old:
        return []
    return out


def claims(attrs: Sequence[str]) -> bool:
    return bool(attrs)


# ── data ────────────────────────────────────────────────────────────────────
_OLD_SQL = "SELECT * FROM chitta_temp_old_owner WHERE appl_id = :app ORDER BY row_id"
_SUB_SQL = """SELECT temp_sub_div_no, new_sub_div_no, existing_patta_no, new_patta_no, survey_no,
       district_code, taluk_code, town_code, ward_code, block_code, status,
       area_hec, area_ares, area_sqmtr FROM areg_temp_subdivclub WHERE appl_id = :app ORDER BY row_id"""


async def _rows(db, sql: str, app: str) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    try:
        async with db.begin_nested():
            return [dict(r) for r in (await db.execute(text(sql), {"app": app})).mappings().all()]
    except Exception as exc:
        logger.warning(f"oldowner_qa: could not read rows ({type(exc).__name__})")
        return []


async def load(db, app: str):
    return await _rows(db, _OLD_SQL, app), await _rows(db, _SUB_SQL, app)


def _v(x: Any) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    return None if s in ("", "-", "...", "None", "null", "0.00") else s


def _t(ta: bool, en: str, tamil: str) -> str:
    return tamil if ta else en


def _sqm(p: Dict[str, Any]) -> float:
    def f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0
    return 10000 * f(p.get("area_hec")) + 100 * f(p.get("area_ares")) + f(p.get("area_sqmtr"))


def _nr(ta: bool) -> str:
    return _t(ta, "not recorded", "பதிவு செய்யப்படவில்லை")


# ── answers ─────────────────────────────────────────────────────────────────
def _field_lines(attrs, o: Dict[str, Any], subs, ta: bool) -> List[str]:
    nr = _nr(ta)

    def val(key, label_en, label_ta):
        return f"{_t(ta, label_en, label_ta)}: {_v(o.get(key)) or nr}"

    lines: List[str] = []
    for a in attrs:
        if a == NAME:
            lines.append(_t(ta, "Old owner", "பழைய உரிமையாளர்") + f": {_v(o.get('owner_name')) or _v(o.get('owner_ename')) or nr}")
        elif a == PATTA:
            lines.append(val("existing_patta_no", "Owner's patta no", "உரிமையாளர் பட்டா எண்") if _v(o.get("existing_patta_no")) or not _v(o.get("patta_no")) else val("patta_no", "Owner's patta no", "உரிமையாளர் பட்டா எண்"))
        elif a == RELATIVE:
            lines.append(val("relative_name", "Father / husband (relative) name", "தந்தை / கணவர் (உறவினர்) பெயர்")
                         if _v(o.get("relative_name")) or _v(o.get("relative_ename")) else
                         _t(ta, "Father / husband (relative) name", "தந்தை / கணவர் (உறவினர்) பெயர்") + f": {nr}")
        elif a == RELATION:
            lines.append(val("relation", "Relationship", "உறவு முறை"))
        elif a == RELCODE:
            lines.append(val("relation_code", "Relation code", "உறவு குறியீடு") +
                         (_t(ta, " (0 = no relation recorded)", " (0 = உறவு பதிவில்லை)") if str(o.get("relation_code")).strip() == "0" else ""))
        elif a == OWNERNUM:
            lines.append(val("owner_num", "Owner number", "உரிமையாளர் எண்"))
        elif a == SHARE:
            lines.append(val("share", "Owner's share", "உரிமையாளர் பங்கு"))
        elif a == EXTENT:
            ext = [_v(o.get(k)) for k in ("extent_u1", "extent_u2", "extent_u3")]
            if any(ext):
                lines.append(_t(ta, "Extent (u1 / u2 / u3)", "பரப்பளவு (u1 / u2 / u3)") + ": " + " / ".join(e or nr for e in ext))
            else:
                lines.append(_t(ta, "Extent u1 / u2 / u3: not recorded in the old-owner record",
                                "பரப்பளவு u1 / u2 / u3: பழைய உரிமையாளர் பதிவில் இல்லை"))
        elif a == SURVEYSUB:
            lines.append(_t(ta, "Survey no", "சர்வே எண்") + f": {_v(o.get('survey_no')) or nr};  " +
                         _t(ta, "Sub-division no", "உட்பிரிவு எண்") + f": {_v(o.get('subdiv_no')) or nr}")
        elif a == NEWSUB:
            lines.append(val("new_sub_div_no", "New sub-division no", "புதிய உட்பிரிவு எண்"))
        elif a == STATUS:
            st = _v(o.get("status"))
            lines.append(_t(ta, "Old-owner record status code", "பழைய உரிமையாளர் பதிவு நிலை குறியீடு") + f": {st or nr}" +
                         (_t(ta, " (a record code; the register does not define its meaning)", " (பதிவுக் குறியீடு; பொருள் பதிவேட்டில் இல்லை)") if st else ""))
        elif a == SEX:
            sx = _v(o.get("sex"))
            lines.append(_t(ta, "Gender", "பாலினம்") + ": " + ({"M": _t(ta, "Male", "ஆண்"), "F": _t(ta, "Female", "பெண்")}.get(sx or "", sx or nr)))
        elif a == AADHAAR:
            lines.append(_t(ta, "Aadhaar: not shown by this assistant", "ஆதார்: இந்த உதவியாளர் காட்டுவதில்லை") if _v(o.get("aadhar_no")) else
                         _t(ta, "Aadhaar: not recorded", "ஆதார்: பதிவில்லை"))
    return lines


def _full(o, subs, ta: bool) -> List[str]:
    return _field_lines([NAME, SURVEYSUB, PATTA, NEWSUB, OWNERNUM, RELATION, RELCODE, RELATIVE, SHARE, EXTENT, SEX, STATUS], o, subs, ta)


def _cmp_rows(o: Dict[str, Any], subs: List[Dict[str, Any]], ta: bool):
    """(label, owner value, sub-division value, verdict) -- verdict: match / differs / n/a."""
    rows = []
    nr = _nr(ta)

    def one(label, ov, sv_list, norm=lambda x: x):
        ov = _v(ov)
        svs = [_v(x) for x in sv_list]
        shown = ", ".join(sorted({x for x in svs if x})) or nr
        if not ov or not any(svs):
            rows.append((label, ov or nr, shown, "n/a"))
        elif all((x is None) or norm(x) == norm(ov) for x in svs) and all(svs):
            rows.append((label, ov, shown, "match"))
        elif any(x and norm(x) == norm(ov) for x in svs):
            rows.append((label, ov, shown, "partial"))
        else:
            rows.append((label, ov, shown, "differs"))

    one(_t(ta, "Survey no", "சர்வே எண்"), o.get("survey_no"), [s.get("survey_no") for s in subs])
    one(_t(ta, "Sub-division no (base of 1/T1)", "உட்பிரிவு எண் (1/T1-ன் அடிப்படை)"), o.get("subdiv_no"),
        [(_v(s.get("temp_sub_div_no")) or "").split("/")[0] for s in subs])
    one(_t(ta, "Existing patta no", "தற்போதுள்ள பட்டா எண்"), o.get("existing_patta_no"),
        [s.get("existing_patta_no") for s in subs])
    one(_t(ta, "New sub-division no", "புதிய உட்பிரிவு எண்"), o.get("new_sub_div_no"), [s.get("new_sub_div_no") for s in subs])
    one(_t(ta, "New patta no", "புதிய பட்டா எண்"), None, [s.get("new_patta_no") for s in subs])
    one(_t(ta, "Status code", "நிலை குறியீடு"), o.get("status"), [s.get("status") for s in subs])
    for k, lab in (("district_code", ("District", "மாவட்டம்")), ("taluk_code", ("Taluk", "வட்டம்")),
                   ("town_code", ("Town", "நகரம்")), ("ward_code", ("Ward", "வார்டு")),
                   ("block_code", ("Block", "பிளாக்"))):
        one(_t(ta, *lab) + _t(ta, " code", " குறியீடு"), o.get(k), [s.get(k) for s in subs])
    ext = [_v(o.get(k)) for k in ("extent_u1", "extent_u2", "extent_u3")]
    tot = sum(_sqm(s) for s in subs)
    rows.append((_t(ta, "Land extent / share vs area", "நில பரப்பு / பங்கு vs பரப்பளவு"),
                 " / ".join(e for e in ext if e) or _nr(ta) + (f" ({_t(ta, 'share', 'பங்கு')}: {_v(o.get('share')) or nr})"),
                 f"{tot:g} " + _t(ta, "sq.m", "ச.மீ") if tot else nr, "n/a"))
    return rows


_KEYMAP = {
    "survey": "Survey", "subdiv": "Sub-division", "patta": "Existing patta", "newsub": "New sub-division",
    "newpatta": "New patta", "status": "Status", "geo": "code", "extent": "Land extent",
}


def _wanted_cmp(message: str) -> set:
    t = normalise(message)
    ws = t.split()
    want = set()
    if "survey" in ws:
        want.add(0)
    if "subdivision number" in t:
        if "new subdivision number" in t:
            want.add(3)
        if re.search(r"(?<!new )subdivision number", t):
            want.add(1)
    if t.count("patta") > t.count("new patta"):
        want.add(2)
    if "new patta" in t:
        want.add(4)
    if "status" in ws:
        want.add(5)
    if set(ws) & {"district", "taluk", "town", "ward", "block"}:
        want.update({6, 7, 8, 9, 10})
    if set(ws) & {"extent", "area", "share"}:
        want.add(11)
    return want


def _compare(message: str, app: str, o, subs, ta: bool) -> str:
    rows = _cmp_rows(o, subs, ta)
    want = _wanted_cmp(message)
    ws = set(normalise(message).split())
    everything = not want or (ws & {"mismatch", "conflict", "incorrect", "different", "verify"} and len(want) < 2)
    sel = rows if everything else [r for i, r in enumerate(rows) if i in want]
    sym = {"match": "✔", "partial": "◐", "differs": "✘", "n/a": "–"}
    head = _t(ta, f"Old-owner record vs sub-division record for {app} (same application id in both):",
              f"{app} — பழைய உரிமையாளர் பதிவு vs உட்பிரிவு பதிவு (இரண்டிலும் ஒரே விண்ணப்ப எண்):")
    out = [head]
    for lab, ov, sv, verdict in sel:
        out.append(f"{sym[verdict]} {lab}: {ov}  |  {sv}")
    bad = [r for r in sel if r[3] == "differs"]
    part = [r for r in sel if r[3] == "partial"]
    na = [r for r in sel if r[3] == "n/a"]
    if bad:
        out.append(_t(ta, "Differences: " + "; ".join(r[0] for r in bad), "வேறுபாடுகள்: " + "; ".join(r[0] for r in bad)))
    else:
        out.append(_t(ta, f"No conflicting values found; {len(na)} field(s) could not be compared because one side is not recorded.",
                      f"முரணான மதிப்புகள் இல்லை; {len(na)} புலங்களை ஒப்பிட முடியவில்லை (ஒரு பக்கம் பதிவில்லை)."))
    if part:
        out.append(_t(ta, "Partly matching (some plots only): " + "; ".join(r[0] for r in part),
                      "பகுதியாக பொருந்துகிறது: " + "; ".join(r[0] for r in part)))
    if any(r[0] == _t(ta, "Status code", "நிலை குறியீடு") and r[3] == "differs" for r in sel):
        out.append(_t(ta, "Note: the two tables keep their own status codes (M / E); the register does not define them, so a different code is not by itself a data conflict.",
                      "குறிப்பு: இரு அட்டவணைகளும் தனித்தனி நிலை குறியீடுகளை வைத்துள்ளன; பதிவேடு அவற்றை வரையறுக்கவில்லை, எனவே வேறுபாடு தரவு முரண் ஆகாது."))
    out.append(_t(ta, "(✔ match, ✘ differs, ◐ some plots only, – not comparable)", "(✔ பொருந்தும், ✘ வேறுபடும், ◐ சில மனைகள், – ஒப்பிட இயலாது)"))
    return "\n".join(out)


def _owner_label(i: int, r: Dict[str, Any], ta: bool) -> str:
    name = _v(r.get("owner_name" if ta else "owner_ename")) or _v(r.get("owner_name")) or _v(r.get("owner_ename"))
    prefix = _t(ta, f"Owner {i}", f"உரிமையாளர் {i}")
    return f"{prefix} — {name}" if name else prefix


def _single_field_value(field_key: str, r: Dict[str, Any], subs, ta: bool) -> tuple[str, str]:
    """Returns (label, value) using the existing _field_lines() implementation."""
    nr = _nr(ta)
    fl = _field_lines([field_key], r, subs, ta)
    if not fl:
        return field_key, nr
    line = fl[0]
    colon_pos = line.find(": ")
    if colon_pos != -1:
        label = line[:colon_pos].strip()
        val = line[colon_pos + 2:].strip()
        if field_key == SURVEYSUB:
            label = _t(ta, "Survey / Sub-division no", "சர்வே / உட்பிரிவு எண்")
        return label, val
    return field_key, line.strip()


def answer(message: str, attrs: Sequence[str], app: str, owner_rows, subs, ta: bool) -> str:
    if not owner_rows:
        return _t(ta, f"No old-owner (chitta) record is held for application {app}.",
                  f"விண்ணப்பம் {app}-க்கு பழைய உரிமையாளர் (சிட்டா) பதிவு இல்லை.")
    o = owner_rows[0]
    if COMPARE in attrs:
        return _compare(message, app, o, subs, ta)
    if EXISTS in attrs:
        n = len(owner_rows)
        return _t(ta, f"Yes — {n} old-owner record(s) held for {app}: {_v(o.get('owner_name')) or '-'}.",
                  f"ஆம் — {app}-க்கு {n} பழைய உரிமையாளர் பதிவு உள்ளது: {_v(o.get('owner_name')) or '-'}.")

    multi = len(owner_rows) > 1
    is_full = FULL in attrs
    single_field = not is_full and len(attrs) == 1

    # Single-field query across multiple owners: emit "Owner i — Name: value" lines
    # under a single card header stating the requested field, avoiding repeating
    # the field label for each owner while keeping owner identity clear.
    if multi and single_field:
        field_attr = attrs[0]
        field_label, _ = _single_field_value(field_attr, owner_rows[0], subs, ta)
        head = _t(
            ta,
            f"Old-owner record for {app} — {field_label}:",
            f"{app} — பழைய உரிமையாளர் பதிவு — {field_label}:",
        )
        lines = []
        for idx, r in enumerate(owner_rows, 1):
            owner_label = _owner_label(idx, r, ta)
            _, val = _single_field_value(field_attr, r, subs, ta)
            lines.append(f"{owner_label}: {val}")
        return head + "\n" + "\n".join("• " + l for l in lines)

    # Multi-field or FULL query across multiple owners: group by owner section
    # to avoid repeating field labels without owner attribution.
    if multi:
        lines: List[str] = []
        for idx, r in enumerate(owner_rows, 1):
            owner_label = _owner_label(idx, r, ta)
            lines.append(f"{owner_label}:")
            row_lines = _full(r, subs, ta) if is_full else _field_lines(attrs, r, subs, ta)
            lines.extend("• " + l for l in row_lines)
            if idx < len(owner_rows):
                lines.append("")
        head = _t(ta, f"Old-owner record for {app}:", f"{app} — பழைய உரிமையாளர் பதிவு:")
        return head + "\n" + "\n".join(lines)

    # Single owner
    r = owner_rows[0]
    row_lines = _full(r, subs, ta) if is_full else _field_lines(attrs, r, subs, ta)
    if len(row_lines) == 1 and not is_full:
        return f"{row_lines[0]}  [{app}]"
    head = _t(ta, f"Old-owner record for {app}:", f"{app} — பழைய உரிமையாளர் பதிவு:")
    return head + "\n" + "\n".join("• " + l for l in row_lines)
