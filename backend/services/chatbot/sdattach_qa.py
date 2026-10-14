"""SD (Senior Draughtsman) attachments of ONE application (`sd_attachment`): the sub-division sketch
PDFs kept against an ISD file -- how many, which survey / sub-division each belongs to, whether a file
is present, whether two are identical, and how they line up with the application's survey and temp
sub-division records.

The attachment rows carry no file NAME, only the embedded PDF; this module reports its type and size,
never its contents, and says so when a file name is asked. A file with no row anywhere is "no SD
attachment held". Typos, Tamil and Tanglish are understood. The caller checks access. No LLM."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.utils.fuzzy import is_token_typo_match
from backend.utils.logger import get_logger

logger = get_logger(__name__)

_TAMIL = [("இணைப்புகள்", " attachment "), ("இணைப்பு", " attachment "), ("வரைபடம்", " sketch "), ("கோப்பு", " file "),
          ("உட்பிரிவு", " subdivision "), ("சர்வே", " survey "), ("எண்", " number "), ("எத்தனை", " howmany "),
          ("ஒப்பிடு", " compare "), ("பொருந்து", " match "), ("வேறுபாடு", " difference "), ("பெயர்", " name ")]
_EXACT = {
    "attachement": "attachment", "attachements": "attachment", "attachments": "attachment", "attachment": "attachment",
    "attatched": "attachment", "attached": "attachment", "atached": "attachment", "atachment": "attachment", "atachments": "attachment",
    "avalable": "available", "survay": "survey", "servey": "survey", "divison": "division", "detials": "details",
    "applcation": "application", "aplication": "application", "duplicte": "duplicate", "duplicates": "duplicate",
    "multipl": "multiple", "inconsistant": "inconsistent", "consistant": "consistent", "mismach": "mismatch",
    "mismatches": "mismatch", "diffrent": "different", "differences": "difference", "diffrence": "difference",
    "files": "file", "no": "number", "numbr": "number", "num": "number", "slno": "slno", "sl": "sl", "sd": "sd",
    "tslr": "fline", "agree": "match", "matches": "match", "same": "same", "belongs": "belong", "belong": "belong",
}
_VOCAB = ["attachment", "attached", "available", "duplicate", "multiple", "inconsistent", "mismatch", "subdivision", "application"]

_ORDINAL_WORDS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4, "fifth": 5, "5th": 5,
}
# negative = from the end: -1 is "last", -2 is "second [one] from the end" / "second-last"
_ORDINAL_FROM_END = {"last": -1}


def _canon(tok: str) -> str:
    if tok in _EXACT:
        return _EXACT[tok]
    if tok.isascii() and tok.isalpha() and len(tok) >= 7:
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
    t = re.sub(r"\bsl[\s.]*no\b", " slno ", t)
    t = re.sub(r"[?!.:()\"',;/&]", " ", t)
    return re.sub(r"\s+", " ", " ".join(_canon(x) for x in re.findall(r"[a-z0-9]+|[^\sa-z0-9]+", t))).strip()


_ATTACHMENT_DIGIT_RE = re.compile(r"\battachment\s*(?:number|no\.?|#)?\s*(\d{1,2})\b")


def _parse_ordinal(ws: set, message: str = "") -> Optional[int]:
    """1-based position ("second" -> 2), or a negative index from the end
    ("last" -> -1), or None when the message names no ordinal at all. Found
    live: "show the second attachment for X" was answered with the SAME full
    unordered list as a bare "show the attachments" -- "second" was silently
    dropped rather than resolved against the deterministic slno/row_id order
    `load()` already sorts by. Only a single plain ordinal is handled here
    (first/second/third/.../last, or "attachment number 3"); ranges
    ("attachments one to three") and exclusions ("all except the first") are
    a genuinely separate, larger slicing feature and are left unclaimed
    rather than answered wrong.

    The digit form is matched with a regex requiring the literal word
    "attachment" immediately before the number -- never a bare digit scanned
    out of `ws`, which would also catch the ward/district/sequence digits
    inside the application number itself (e.g. the "02" in
    "2026/0154/02/012547") as a false "second attachment".
    """
    for w, idx in _ORDINAL_WORDS.items():
        if w in ws:
            return idx
    for w, idx in _ORDINAL_FROM_END.items():
        if w in ws:
            return idx
    m = _ATTACHMENT_DIGIT_RE.search(message or "")
    if m:
        n = int(m.group(1))
        if 1 <= n <= 20:
            return n
    return None


def parse(message: str) -> Optional[Dict[str, Any]]:
    """None unless the message is about the SD attachments of an application."""
    ws = set(normalise(message).split())
    if "attachment" not in ws and not ("sd" in ws and ws & {"file", "sketch"}):
        return None
    if ws & {"upload", "uploaded", "uploading", "pdf", "docx", "csv"} and "sd" not in ws:
        return None  # a chat-uploaded file, not an SD attachment
    if "sd" not in ws and not ws & {"application", "applcation"} and not re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", message or ""):
        return None
    mode = None
    if ws & {"compare", "match", "mismatch", "different", "difference", "inconsistent", "consistent", "same", "belong"} or \
            ("linked" in ws and ws & {"same", "different"}):
        mode = "compare"
    elif ws & {"duplicate"}:
        mode = "duplicate"
    elif "howmany" in ws or ("how" in ws and "many" in ws) or ("number" in ws and "attachment" in ws and "slno" not in ws and "survey" not in ws and "of" in ws):
        mode = "count"
    elif "multiple" in ws:
        mode = "multiple"
    elif "complete" in ws and "details" in ws:
        mode = "full"
    elif "slno" in ws or ("sl" in ws and "number" in ws):
        mode = "slno"
    elif _parse_ordinal(ws, message) is not None:
        mode = "ordinal"
    elif "file" in ws and ws & {"available", "missing", "every"} and "name" not in ws:
        mode = "file_present"
    elif "survey" in ws or "subdivision" in ws:
        mode = "survey"
    elif ("name" in ws and "file" in ws) or "filename" in ws:
        mode = "filename"
    elif "file" in ws and ws & {"available", "missing", "every"}:
        mode = "file_present"
    elif ws & {"show", "list", "files", "file", "what", "which", "all"}:
        mode = "list"
    else:
        mode = "exists"
    return {"mode": mode, "ws": ws, "ordinal": _parse_ordinal(ws, message)}


def claims(message: str) -> bool:
    return parse(message) is not None


_SQL = ("SELECT slno, survey_subdiv_no, length(file) AS flen, md5(file) AS fhash, left(file, 30) AS fhead "
        "FROM sd_attachment WHERE appl_id = :a ORDER BY slno, row_id")


async def load(db, app: str) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    try:
        async with db.begin_nested():
            return [dict(r) for r in (await db.execute(text(_SQL), {"a": app})).mappings().all()]
    except Exception as exc:
        logger.warning(f"sdattach_qa: could not read sd_attachment ({type(exc).__name__})")
        return []


async def count_survey_records(db, app: str) -> int:
    from sqlalchemy import text
    try:
        async with db.begin_nested():
            return int((await db.execute(text("SELECT count(*) FROM areg_temp_subdivclub WHERE appl_id = :a"), {"a": app})).scalar() or 0)
    except Exception:
        return 0


def _t(ta, en, tamil):
    return tamil if ta else en


def _v(x) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    return None if s in ("", "-") or s.lower() in ("none", "null") else s


def _kind(head: Optional[str]) -> str:
    m = re.match(r"data:([\w/+.-]+);", head or "")
    return {"application/pdf": "PDF"}.get(m.group(1), m.group(1)) if m else "file"


def _size(flen: Optional[int]) -> str:
    n = int((flen or 0) * 3 / 4)  # base64 -> bytes
    return f"{n / 1024:.0f} KB" if n else "empty"


def _base(ss: Optional[str]) -> Optional[str]:
    return (ss or "").split("/")[0] or None


async def answer(p: Dict[str, Any], app: str, rows: List[Dict[str, Any]], detail: Dict[str, Any], subs: List[Dict[str, Any]],
                 other: Dict[str, bool], ta: bool) -> str:
    """`other` says which records exist for this application: {"fline": bool, "fullfield": bool}."""
    mode, ws = p["mode"], p["ws"]
    nr = _t(ta, "not recorded", "பதிவில்லை")
    if not rows:
        return _t(ta, f"No SD attachment is held for application {app}. Only some ISD files carry the Senior Draughtsman's sketch attachments.",
                  f"விண்ணப்பம் {app}-க்கு SD இணைப்பு இல்லை. சில ISD கோப்புகளுக்கு மட்டுமே Senior Draughtsman வரைபட இணைப்புகள் இருக்கும்.")
    n = len(rows)
    L: List[str] = []
    ss_vals = [_v(r.get("survey_subdiv_no")) for r in rows]
    hashes = [r.get("fhash") for r in rows]
    dup = len(set(hashes)) < len(hashes)
    if mode == "exists":
        L.append(_t(ta, f"Yes -- {n} SD attachment(s) are held for {app}.", f"ஆம் -- {app}-க்கு {n} SD இணைப்பு(கள்) உள்ளன."))
    elif mode == "count":
        L.append(_t(ta, f"{n} SD attachment(s) for {app}.", f"{app}-க்கு {n} SD இணைப்பு(கள்)."))
    elif mode == "multiple":
        L.append(_t(ta, f"{'Yes' if n > 1 else 'No'} -- {n} attachment(s)" + (" (slno " + ", ".join(str(r['slno']) for r in rows) + ")" if n > 1 else "") + ".",
                    f"{'ஆம்' if n > 1 else 'இல்லை'} -- {n} இணைப்பு(கள்)."))
    elif mode == "duplicate":
        if dup:
            groups: Dict[str, List[int]] = {}
            for r in rows:
                groups.setdefault(r["fhash"], []).append(r["slno"])
            d = [v for v in groups.values() if len(v) > 1]
            L.append(_t(ta, "Yes -- identical files: slno " + "; ".join(" = ".join(map(str, v)) for v in d), "ஆம் -- ஒரே கோப்புகள்: " + "; ".join(" = ".join(map(str, v)) for v in d)))
        else:
            L.append(_t(ta, f"No -- the {n} attachment(s) are all different files (compared by content).", f"இல்லை -- {n} இணைப்புகளும் வெவ்வேறு கோப்புகள் (உள்ளடக்கம் மூலம் ஒப்பிடப்பட்டது)."))
    elif mode == "slno":
        L.append(_t(ta, "SD attachment sl no: " + ", ".join(str(r["slno"]) for r in rows), "SD இணைப்பு வரிசை எண்: " + ", ".join(str(r["slno"]) for r in rows)))
    elif mode == "ordinal":
        # `rows` is already the deterministic order (slno, then row_id) --
        # the same order every other mode reports slno in, so "the second
        # one" means the same thing here as it does in "SD attachment sl no:
        # 1, 2" above.
        idx = p.get("ordinal")
        pos = idx - 1 if idx > 0 else n + idx
        if idx == 0 or pos < 0 or pos >= n:
            L.append(_t(
                ta,
                f"There {'is' if n == 1 else 'are'} only {n} SD attachment(s) for {app} "
                f"(slno {', '.join(str(r['slno']) for r in rows)}) -- no attachment at that position.",
                f"{app}-க்கு {n} SD இணைப்பு(கள்) மட்டுமே உள்ளன "
                f"(வரிசை எண் {', '.join(str(r['slno']) for r in rows)}) -- அந்த இடத்தில் இணைப்பு இல்லை."))
        else:
            r = rows[pos]
            ss = ss_vals[pos] or nr
            L.append(_t(
                ta,
                f"Attachment {pos + 1} of {n} (slno {r['slno']}) for {app}: "
                f"survey/sub-division {ss}, {_kind(r.get('fhead'))} file, {_size(r.get('flen'))}.",
                f"{app}-க்கு இணைப்பு {pos + 1} / {n} (வரிசை எண் {r['slno']}): "
                f"சர்வே/உட்பிரிவு {ss}, {_kind(r.get('fhead'))} கோப்பு, {_size(r.get('flen'))}."))
    elif mode == "survey":
        if "missing" in ws or "every" in ws:
            miss = [r["slno"] for r, s in zip(rows, ss_vals) if not s]
            L.append(_t(ta, "Survey / sub-division no is missing for slno " + ", ".join(map(str, miss)) if miss else
                        f"No -- all {n} attachment(s) carry a survey / sub-division no.",
                        "காணாத வரிசை எண்: " + ", ".join(map(str, miss)) if miss else f"இல்லை -- {n} இணைப்புகளிலும் சர்வே / உட்பிரிவு எண் உள்ளது."))
        else:
            for r, s in zip(rows, ss_vals):
                L.append(_t(ta, f"Attachment {r['slno']}: survey / sub-division {s or nr}", f"இணைப்பு {r['slno']}: சர்வே / உட்பிரிவு {s or nr}"))
    elif mode == "filename":
        L.append(_t(ta, "The SD attachment rows carry no file name -- only the embedded file itself: " +
                    "; ".join(f"slno {r['slno']}: {_kind(r.get('fhead'))}, {_size(r.get('flen'))}" for r in rows),
                    "SD இணைப்பு பதிவுகளில் கோப்பு பெயர் இல்லை -- கோப்பு மட்டுமே: " + "; ".join(f"{r['slno']}: {_kind(r.get('fhead'))}, {_size(r.get('flen'))}" for r in rows)))
    elif mode == "file_present":
        empty = [r["slno"] for r in rows if not r.get("flen")]
        L.append(_t(ta, f"Yes -- a file is present on all {n} attachment(s)" if not empty else "No file on slno " + ", ".join(map(str, empty)),
                    f"ஆம் -- {n} இணைப்புகளிலும் கோப்பு உள்ளது" if not empty else "கோப்பு இல்லாத வரிசை எண்: " + ", ".join(map(str, empty))))
        miss = [r["slno"] for r, s in zip(rows, ss_vals) if not s]
        L.append(_t(ta, "Survey / sub-division no: " + ("present on every attachment" if not miss else "missing for slno " + ", ".join(map(str, miss))),
                    "சர்வே / உட்பிரிவு எண்: " + ("அனைத்திலும் உள்ளது" if not miss else "காணாதவை: " + ", ".join(map(str, miss)))))
    elif mode in ("list", "full"):
        for r, s in zip(rows, ss_vals):
            L.append(_t(ta, f"slno {r['slno']}: {_kind(r.get('fhead'))} file, {_size(r.get('flen'))}, survey / sub-division {s or nr}",
                        f"வரிசை {r['slno']}: {_kind(r.get('fhead'))} கோப்பு, {_size(r.get('flen'))}, சர்வே / உட்பிரிவு {s or nr}"))
        if mode == "full":
            L.append(_t(ta, f"Duplicates: {'yes' if dup else 'none'}; the rows carry no file name.", f"நகல்: {'உண்டு' if dup else 'இல்லை'}; பதிவுகளில் கோப்பு பெயர் இல்லை."))
    else:  # compare
        survey_no = _v(detail.get("survey_number"))
        bases = {(_v(x.get("temp_sub_div_no")) or "").split("/")[0] for x in subs if _v(x.get("temp_sub_div_no"))}
        if ws & {"fline", "tslr"}:
            L.append(_t(ta, "No F-Line record is held for this application, so there is nothing to compare the attachments with." if not other.get("fline") else "An F-Line record exists, but F-Line applications carry no SD attachments.",
                        "இந்த விண்ணப்பத்துக்கு F-Line பதிவு இல்லை; ஒப்பிட எதுவும் இல்லை."))
        if "fullfield" in ws:
            L.append(_t(ta, "No full-field patta transfer record is held for this application (only NISD files have one, and SD attachments belong to ISD files)." if not other.get("fullfield") else "A full-field transfer record exists.",
                        "முழு புல பட்டா மாற்ற பதிவு இல்லை (NISD கோப்புகளுக்கு மட்டுமே; SD இணைப்புகள் ISD கோப்புகளுக்கு)."))
        if ("same" in ws and "application" in ws) or ("all" in ws and "associated" in ws):
            L.append(_t(ta, f"Yes -- all {n} attachment(s) are stored against the application id {app}, the same as the application record.",
                        f"ஆம் -- {n} இணைப்புகளும் விண்ணப்ப எண் {app}-உடன் சேமிக்கப்பட்டுள்ளன."))
        if not ws & {"fline", "fullfield"} or ws & {"survey", "subdivision", "mismatch", "inconsistent", "different", "consistent", "difference"}:
            for r, s in zip(rows, ss_vals):
                b = _base(s)
                sub_part = (s or "").split("/")[1] if s and "/" in s else None
                ok_sv = "–" if not (b and survey_no) else ("✔" if str(b) == str(survey_no) else "✘")
                ok_sd = "–" if not (sub_part and bases) else ("✔" if sub_part in bases else "✘")
                L.append(f"slno {r['slno']}: {s or nr} -- " + _t(ta, f"survey {ok_sv} (application survey {survey_no or nr}); sub-division {ok_sd} (temp sub-division base {', '.join(sorted(bases)) or nr})",
                                                              f"சர்வே {ok_sv} (விண்ணப்ப சர்வே {survey_no or nr}); உட்பிரிவு {ok_sd} ({', '.join(sorted(bases)) or nr})"))
            distinct = {s for s in ss_vals if s}
            L.append(_t(ta, f"Across the attachments the survey / sub-division no is {'consistent (' + next(iter(distinct)) + ')' if len(distinct) == 1 else 'NOT consistent: ' + ', '.join(sorted(distinct)) if distinct else 'not recorded'}.",
                        f"இணைப்புகளில் சர்வே / உட்பிரிவு எண் {'ஒன்றே (' + next(iter(distinct)) + ')' if len(distinct) == 1 else 'வேறுபடுகிறது: ' + ', '.join(sorted(distinct)) if distinct else 'பதிவில்லை'}."))
            miss_file = [r["slno"] for r in rows if not r.get("flen")]
            if "file" in ws or "name" in ws or "missing" in ws:
                L.append(_t(ta, "Missing file on slno " + ", ".join(map(str, miss_file)) if miss_file else "Every attachment has a file (the rows carry no file NAME).",
                            "கோப்பு இல்லாதவை: " + ", ".join(map(str, miss_file)) if miss_file else "அனைத்து இணைப்புகளிலும் கோப்பு உள்ளது."))
            if "howmany" in ws or "count" in ws or ("number" in ws and "records" in ws):
                nrec = other.get("survey_records") or 0
                L.append(_t(ta, f"{n} SD attachment(s) vs {nrec} temp sub-division record(s) for this application.", f"{n} SD இணைப்புகள் vs {nrec} உட்பிரிவு பதிவுகள்."))
        if "records" in ws and ("number" in ws or "count" in ws or "howmany" in ws) and not any(x.startswith("slno") for x in L):
            L.append(_t(ta, f"{n} SD attachment(s) vs {other.get('survey_records') or 0} temp sub-division record(s).", f"{n} SD இணைப்புகள் vs {other.get('survey_records') or 0} உட்பிரிவு பதிவுகள்."))
    L = [x for x in L if x]
    return L[0] + f"  [{app}]" if len(L) == 1 else f"{app} (SD attachments):\n" + "\n".join("• " + x for x in L)
