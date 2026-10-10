"""Application-information fields of ONE application (challan, payment mode, amount,
status / reject-reason code, proposed field visit date and remarks, missing documents,
enclosures, SRO, CSC name, permanent address, parents, gender, DOB), read from the
application-information extract (`sub_div_patta_transfer_application_information_urban`
for ISD, `full_field_patta_transfer_application_information` for NISD).

Typos, Tamil and Tanglish are understood. A blank column is "not recorded" and is
explained when the rule explains it (no SRO on a CSC file); nothing is guessed.
The caller checks access first. No LLM."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.utils.fuzzy import is_token_typo_match
from backend.utils.logger import get_logger

logger = get_logger(__name__)
_APP_NO = re.compile(r"\d{4}/\d{3,4}/\d{1,3}/\d+")

_TAMIL = [
    ("சலான்", " challan "), ("தொகை", " amount "), ("தாயார்", " mother "), ("தாய்", " mother "), ("தந்தையார்", " father "), ("தந்தை", " father "),
    ("பாலினம்", " gender "), ("பிறந்த தேதி", " dob "), ("நிரந்தர", " permanent "), ("முகவரி", " address "),
    ("நிராகரிப்பு", " reject "), ("நிராகரி", " reject "), ("காரணம்", " reason "), ("ஆவணம்", " documents "),
    ("ஆவணங்கள்", " documents "), ("விடுபட்ட", " missing "), ("முன்மொழிந்த", " proposed "),
    ("கள ஆய்வு", " field visit "), ("தேதி", " date "), ("செலுத்தும் முறை", " payment mode "),
    ("கட்டண முறை", " payment mode "), ("குறியீடு", " code "), ("நிலை", " status "), ("குறிப்பு", " remarks "),
    ("இணைக்கப்பட்ட", " merged "), ("விண்ணப்ப", " application "), ("எண்", " number "), ("இணைப்பு", " enclosures "), ("தற்போதைய", " current "), ("கைபேசி", " mobile "), ("மொபைல்", " mobile "), ("தொழில்", " occupation "), ("வங்கி", " bank "),
]
_TANGLISH = [
    (r"thaai|thayar|amma", "mother"), (r"thandhai|thanthai|appa", "father"), (r"pirandha\s*thethi", "dob"),
    (r"thogai", "amount"), (r"mugavari", "address"), (r"niranthara|nirandhara", "permanent"),
    (r"aavanam|aavanangal", "documents"), (r"enna|enn|evlo|irukka|irukkaa|yaaru|yar\b", ""),
]
_EXACT = {
    "applcant": "applicant", "aplicant": "applicant", "applicnt": "applicant", "applcation": "application",
    "aplication": "application", "adress": "address", "addres": "address", "permenant": "permanent",
    "permanant": "permanent", "paymant": "payment", "paymnt": "payment", "pymt": "payment", "documants": "documents",
    "docs": "documents", "document": "documents", "documents": "documents", "doccuments": "documents",
    "stauts": "status", "upadte": "update", "udpate": "update", "recomendation": "rec",
    "mothers": "mother", "fathers": "father", "moher": "mother", "mohter": "mother", "fathr": "father",
    "birth": "dob", "dob": "dob", "gendr": "gender", "sex": "gender", "no": "number", "num": "number",
    "numbr": "number", "challn": "challan", "chalan": "challan", "chalaan": "challan", "chalann": "challan",
    "field": "field", "visit": "visit", "fv": "visit", "csc": "csc", "sro": "sro", "igrs": "igrs",
    "remark": "remarks", "remarks": "remarks", "remraks": "remarks", "enclosure": "enclosures",
    "enclosures": "enclosures", "enclosers": "enclosures", "bank": "bank", "bnk": "bank", "treasury": "treasury",
    "barcod": "barcode", "barcode": "barcode", "bar": "bar", "encertifyed": "encertify", "encertified": "encertify",
    "encertify": "encertify", "physical": "physical", "verification": "verification", "verifcation": "verification",
    "receved": "received", "recieved": "received", "receipt": "received", "received": "received", "sent": "sent",
    "sending": "sent", "rtr": "rtr", "automautated": "automutated", "automated": "automutated",
    "automutated": "automutated", "automutation": "automutated", "automatically": "automutated", "merge": "merge",
    "merged": "merged", "corection": "correction", "correction": "correction", "occupashun": "occupation",
    "occupation": "occupation", "latest": "latest", "update": "update", "updated": "update", "completd": "completed",
    "enclosd": "enclosures", "enclosed": "enclosures", "applid": "applid", "applied": "applid", "proposd": "proposed", "proposed": "proposed", "rejct": "reject", "rejected": "reject",
    "rejection": "reject", "mising": "missing", "misssing": "missing", "missng": "missing",
}
_VOCAB = ["applicant", "address", "permanent", "mother", "father", "gender", "challan", "payment", "amount",
          "proposed", "remarks", "missing", "documents", "enclosures", "reject", "reason", "status", "treasury",
          "occupation", "application", "number", "mobile"]


def _canon(tok: str) -> str:
    if tok in _EXACT:
        return _EXACT[tok]
    if tok.isascii() and tok.isalpha() and len(tok) >= 5:
        hits = {w for w in _VOCAB if is_token_typo_match(tok, w)}
        if len(hits) == 1:
            return next(iter(hits))
    return tok


def normalise(message: str) -> str:
    t = _APP_NO.sub(" ", message or "").lower()
    for ta, en in _TAMIL:
        t = t.replace(ta, en)
    for rx, en in _TANGLISH:
        t = re.sub(rx, f" {en} ", t)
    t = re.sub(r"[?!.:()\"',;/&]", " ", t)
    return re.sub(r"\s+", " ", " ".join(_canon(x) for x in re.findall(r"[a-z0-9]+|[^\sa-z0-9]+", t))).strip()


CHALLAN_NO, CHALLAN_DATE, PAYMODE, AMOUNT, STATUS_CODE, REJECT = (
    "challan_no", "challan_date", "paymentmode", "amount", "appl_status_code", "reject_reason_code")
PFV_DATE, PFV_REMARKS, MISSING, ENCL, SRO, CSC = (
    "proposed_field_visit_date", "proposed_remarks", "missing_docs", "enclosures", "sro", "csc_name")
BARCODE, ENCERT, VERIFYPHYS, SENT, RECEIVED, RTR, OWNERCORR, MERGE, AUTOMUT, UPDATE = (
    "barcode_flag", "encertify", "verifyphysical", "date_of_sending_document", "date_of_receipt_of_document",
    "rtr_str", "owner_correction_reason", "merge_applid", "is_automutated", "update_date")
MOBILE, CURADDR = "mobile_no", "appl_address_cur"
PERM, MOTHER, FATHER, GENDER, DOB, OCC, TREASURY = (
    "appl_address_perm", "mother_name", "father_name", "gender", "dob", "occupation", "treasury")


def parse(message: str) -> List[str]:
    if "everyfield" in (message or "").lower() and "application information" in (message or "").lower():  # "which fields are not recorded": every column, blanks picked out afterwards
        return list(_LABEL)
    t = normalise(message)
    ws = set(t.split())
    out: List[str] = []

    def add(k):
        if k not in out:
            out.append(k)

    if "challan" in ws:
        if "date" in ws:
            add(CHALLAN_DATE)
        if ws & {"payment", "mode"} and "payment" in ws:
            add(PAYMODE)
        if not out or "number" in ws or ("date" not in ws and "payment" not in ws):
            add(CHALLAN_NO)
    elif "payment" in ws and ("mode" in ws or "method" in ws):
        add(PAYMODE)
    if "amount" in ws:
        add(AMOUNT)
    if "status" in ws and "code" in ws and "reject" not in ws:
        add(STATUS_CODE)
    if "reject" in ws and ("reason" in ws or "code" in ws):
        add(REJECT)
    if "proposed" in ws and ws & {"field", "visit"}:
        add(PFV_REMARKS if "remarks" in ws else PFV_DATE)
        if "remarks" in ws and "date" in ws:
            add(PFV_DATE)
    elif "proposed" in ws and "remarks" in ws:
        add(PFV_REMARKS)
    if "missing" in ws and "documents" in ws:
        add(MISSING)
    if "enclosures" in ws:
        add(ENCL)
    if "mobile" in ws and ws & {"applicant", "application"}:
        add(MOBILE)
    if "address" in ws and "permanent" not in ws and ws & {"applicant", "current"}:
        add(CURADDR)
    if "sro" in ws and not (ws & {"channel", "igrs", "number", "no"}) or ("sro" in ws and "name" in ws):
        add(SRO)
    if "csc" in ws and ("name" in ws or "centre" in ws or "center" in ws):
        add(CSC)
    if "permanent" in ws and "address" in ws and "owner" not in ws:
        add(PERM)
    if ws & {"applicant", "application"} or (ws & {"mother", "father", "gender", "dob"} and not ws & {"owner", "relative"}):
        if "mother" in ws:
            add(MOTHER)
        if "father" in ws:
            add(FATHER)
        if "gender" in ws:
            add(GENDER)
        if "dob" in ws:
            add(DOB)
        if "occupation" in ws:
            add(OCC)
    if "treasury" in ws or ("bank" in ws and ws & {"branch", "brnch", "challan", "payment", "application"}):
        add(TREASURY)
    if "barcode" in ws:
        add(BARCODE)
    if "encertify" in ws:
        add(ENCERT)
    if "physical" in ws or ("verification" in ws and "physical" in ws):
        add(VERIFYPHYS)
    if "sent" in ws and "documents" in ws:
        add(SENT)
    if "received" in ws and "documents" in ws:
        add(RECEIVED)
    if "rtr" in ws:
        add(RTR)
    if "owner" in ws and "correction" in ws and "reason" in ws:
        add(OWNERCORR)
    if ("merge" in ws and ws & {"applid", "linked", "id"}) or ("merged" in ws and ws & {"with", "which", "application", "number"}):
        add(MERGE)
    if "automutated" in ws:
        add(AUTOMUT)
    if "update" in ws and "remarks" in ws:
        add(UPDATE)
    return out


def claims(message: str) -> bool:
    return bool(parse(message))


NAME, APPL_STATUS = "appl_name", "appl_status"


def applicant_keys(message: str) -> List[str]:
    """"applicant name" / "applicant status" -- answered from this record only for a file outside
    ISD / NISD (an F-Line file, say), whose applicant is held nowhere else. Kept out of parse()
    so ISD / NISD applicant questions stay with the main field handlers."""
    ws = set(normalise(message).split())
    if "applicant" not in ws:
        return []
    return [k for k, w in ((NAME, "name"), (APPL_STATUS, "status")) if w in ws]


_TABLES = {"0154": "sub_div_patta_transfer_application_information_urban",
           "0153": "full_field_patta_transfer_application_information",
           "0178": "f_line_service_application_information_urban"}
_KIND = {"0154": "ISD", "0153": "NISD", "0178": "F-Line"}


async def load(db, app: str) -> Optional[Dict[str, Any]]:
    from sqlalchemy import text
    code = app.split("/")[1] if app.count("/") >= 3 else ""
    for tbl in ([_TABLES[code]] if code in _TABLES else list(_TABLES.values())):
        try:
            async with db.begin_nested():
                r = (await db.execute(text(f"SELECT * FROM {tbl} WHERE appl_id = :a LIMIT 1"),
                                      {"a": app})).mappings().first()
            if r:
                return dict(r)
        except Exception as exc:
            logger.warning(f"appinfo_qa: could not read {tbl} ({type(exc).__name__})")
    return None


def _v(x) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    return None if s in ("", "-", "0", "None", "null", "0.00", "0.0") else s


def _t(ta, en, tamil):
    return tamil if ta else en


_LABEL = {
    CHALLAN_NO: ("Challan number", "சலான் எண்"), CHALLAN_DATE: ("Challan date", "சலான் தேதி"),
    PAYMODE: ("Payment mode", "செலுத்தும் முறை"), AMOUNT: ("Application amount", "விண்ணப்பத் தொகை"),
    STATUS_CODE: ("Application status code", "விண்ணப்ப நிலை குறியீடு"),
    REJECT: ("Reject reason code", "நிராகரிப்பு காரணக் குறியீடு"),
    PFV_DATE: ("Proposed field visit date", "முன்மொழிந்த கள ஆய்வு தேதி"),
    PFV_REMARKS: ("Proposed remarks for the field visit", "கள ஆய்வுக்கான முன்மொழிந்த குறிப்புகள்"),
    MISSING: ("Missing documents", "விடுபட்ட ஆவணங்கள்"), ENCL: ("Enclosures submitted", "இணைக்கப்பட்ட ஆவணங்கள்"),
    SRO: ("SRO", "SRO"), CSC: ("CSC name", "CSC பெயர்"),
    PERM: ("Permanent address", "நிரந்தர முகவரி"), MOTHER: ("Mother's name", "தாயார் பெயர்"),
    FATHER: ("Father's name", "தந்தை பெயர்"), GENDER: ("Gender", "பாலினம்"), DOB: ("Date of birth", "பிறந்த தேதி"),
    OCC: ("Occupation", "தொழில்"), MOBILE: ("Mobile", "கைபேசி"), CURADDR: ("Current address", "தற்போதைய முகவரி"), BARCODE: ("Barcode flag", "பார்கோடு கொடி"), ENCERT: ("Encertify", "Encertify"),
    VERIFYPHYS: ("Physical verification", "நேரடி சரிபார்ப்பு"), SENT: ("Documents sent on", "ஆவணங்கள் அனுப்பிய தேதி"),
    RECEIVED: ("Documents received on", "ஆவணங்கள் பெற்ற தேதி"), RTR: ("RTR str", "RTR str"),
    OWNERCORR: ("Owner correction reason", "உரிமையாளர் திருத்த காரணம்"), MERGE: ("Merged application id", "இணைக்கப்பட்ட விண்ணப்ப எண்"),
    AUTOMUT: ("Automutated", "தானியங்கி பட்டா மாறுதல்"), UPDATE: ("Last updated", "கடைசியாக புதுப்பித்தது"), TREASURY: ("Treasury / bank", "கருவூலம் / வங்கி"),
    NAME: ("Applicant name", "விண்ணப்பதாரர் பெயர்"), APPL_STATUS: ("Application status (as recorded)", "விண்ணப்ப நிலை (பதிவில்)"),
}


def answer(keys: List[str], app: str, row: Optional[Dict[str, Any]], detail: Dict[str, Any], ta: bool) -> str:
    if not row:
        return _t(ta, f"No application-information record is held for {app}.",
                  f"{app}-க்கு விண்ணப்பத் தகவல் பதிவு இல்லை.")
    nr = _t(ta, "not recorded", "பதிவு செய்யப்படவில்லை")
    status = (detail or {}).get("current_status") or (detail or {}).get("status") or ""
    channel = (detail or {}).get("submission_channel") or ""
    lines: List[str] = []
    kind = _KIND.get(app.split("/")[1] if app.count("/") >= 3 else "", "this")
    for k in keys:
        lab = _t(ta, *_LABEL[k])
        if k not in row:
            lines.append(f"{lab}: " + _t(ta, f"the {kind} application-information record has no such column",
                                         f"{kind} விண்ணப்பத் தகவல் பதிவில் அந்த புலம் இல்லை"))
            continue
        if k == UPDATE:
            ud, rm = _v(row.get("update_date")), _v(row.get("remarks"))
            lines.append(f"{lab}: {str(ud)[:19] if ud else nr}; " + _t(ta, "remarks", "குறிப்புகள்") + f": {rm or nr}")
            continue
        val = _v(row.get(k))
        if k == AUTOMUT and val:
            val = {"Y": _t(ta, "Yes (Y)", "ஆம் (Y)"), "N": _t(ta, "No (N)", "இல்லை (N)")}.get(val.upper(), val)
        if k == PFV_DATE and val:
            val = val[:10]
        if k == TREASURY:
            val = ", ".join(x for x in (_v(row.get("treasury")), _v(row.get("bank")), _v(row.get("bank_branch"))) if x) or None
        if val:
            lines.append(f"{lab}: {val}")
            continue
        note = ""
        if k == SRO:
            note = _t(ta, " — only a Sub-Registrar referral has an SRO" + (f"; this file came through the {channel} channel" if channel else ""),
                      " — Sub-Registrar பரிந்துரைக்கு மட்டுமே SRO இருக்கும்" + (f"; இந்த கோப்பு {channel} வழியாக வந்தது" if channel else ""))
        elif k == REJECT:
            note = _t(ta, f" — the application is {status}, not rejected" if status and status != "rejected" else "",
                      f" — விண்ணப்பம் {status} நிலையில் உள்ளது" if status and status != "rejected" else "")
        elif k == MISSING:
            enc = _v(row.get("enclosures"))
            lines.append(_t(ta, "Missing documents: none recorded", "விடுபட்ட ஆவணங்கள்: பதிவில்லை") +
                         (_t(ta, f" (enclosures submitted: {enc.rstrip('/')})", f" (இணைக்கப்பட்டவை: {enc.rstrip('/')})") if enc else ""))
            continue
        elif k == CSC:
            src = _v(row.get("source_name"))
            note = _t(ta, " — the CSC counter's name is not held in the register", " — CSC மையத்தின் பெயர் பதிவேட்டில் இல்லை")
        elif k in (CHALLAN_NO, CHALLAN_DATE, PAYMODE, AMOUNT, TREASURY):
            note = _t(ta, " — no payment details are held for this file", " — இந்த கோப்புக்கு கட்டண விவரம் பதிவில்லை")
        lines.append(f"{lab}: {nr}{note}")
    if len(lines) == 1:
        return f"{lines[0]}  [{app}]"
    return f"{app}:\n" + "\n".join("• " + l for l in lines)
