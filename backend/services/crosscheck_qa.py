"""Cross-checks of ONE patta-transfer application against its other records: the
application-information extract (applicant, payment, verification), the chitta old-owner
record, the temp sub-division record, the register and the application record.

Every verdict is computed from the rows (never narrated), says which side is blank, and
is careful about what a difference means: a patta-transfer applicant is usually the NEW
owner, so an applicant that differs from the old owner is expected, not an error. Names in
different scripts are compared through the deterministic transliteration. Typos, Tamil and
Tanglish are understood. The caller checks access first. No LLM."""
from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from backend.services import appinfo_qa, oldowner_qa
from backend.utils.translit import script_of, tamil_to_latin

_ALIAS = {
    "compair": "compare", "comparing": "compare", "compared": "compare", "comparison": "compare",
    "mismatch": "mismatch", "mismatches": "mismatch", "mismatched": "mismatch", "differences": "difference",
    "different": "difference", "differ": "difference", "differs": "difference", "difference": "difference",
    "diffrence": "difference", "consistant": "consistent", "consistent": "consistent", "consistency": "consistent",
    "matches": "match", "matching": "match", "matched": "match", "manualy": "manual", "manually": "manual",
    "indicates": "indicate", "indicated": "indicate", "indication": "indicate", "means": "mean",
    "associated": "linked", "linked": "linked", "completd": "completed", "timline": "timeline",
    "chita": "chitta", "chittaa": "chitta", "important": "important", "avalable": "available",
}
_TAMIL = [("விண்ணப்பதாரர்", " applicant "), ("பெயர்", " name "), ("பெயரை", " name "), ("ஒப்பிடு", " compare "), ("பொருந்து", " match "), ("வேறுபாடு", " difference "), ("முரண்", " mismatch "),
          ("பழைய உரிமையாளர்", " old owner "), ("உரிமையாளர்", " owner "), ("சிட்டா", " chitta "),
          ("நிலை", " status "), ("முகவரி", " address "), ("சலான்", " challan "), ("தானியங்கி", " automutated "),
          ("இணைப்பு", " merge "), ("நிலைத்தன்மை", " consistent "), ("சரிபார்", " verify ")]


def normalise(message: str) -> str:
    t = message or ""
    for ta, en in _TAMIL:
        t = t.replace(ta, en)
    t = re.sub(r"\bpatta\s*transfer\b", " pattatransfer ", t, flags=re.IGNORECASE)
    t = appinfo_qa.normalise(t)
    return " ".join(_ALIAS.get(w, w) for w in t.split())


def parse(message: str) -> Optional[str]:
    """The cross-check asked for, or None."""
    ws = set(normalise(message).split())
    cmp_ = bool(ws & {"compare", "match", "mismatch", "difference", "consistent", "same"})
    has_owner = bool(ws & {"owner", "chitta"})
    if "mismatch" in ws or ("difference" in ws and ws & {"pattatransfer", "application", "record"}) or \
            ("important" in ws and "difference" in ws):
        if has_owner:
            return "all_diffs"
    if cmp_ and has_owner and ws & {"applicant", "pattatransfer", "name", "father", "mother", "dob"}:
        return "applicant_owner"
    if cmp_ and "status" in ws and ws & {"correction", "flow", "modification"}:
        return "status_flow"
    if "address" in ws and (cmp_ or ws & {"other", "records"}) and ws & {"other", "records"}:
        return "address_other"
    if "challan" in ws and (cmp_ or "timeline" in ws) and ws & {"timeline", "submission", "date"}:
        return "challan_timeline"
    if "consistent" in ws and ws & {"physical", "field", "visit", "proposed"}:
        return "consistent_fv"
    if "physical" in ws and ws & {"before", "after", "visit", "field"}:
        return "phys_before_fv"
    if "missing" in ws and "documents" in ws and ws & {"submitted", "though", "even"}:
        return "missing_vs_submitted"
    if "owner" in ws and "correction" in ws and ws & {"indicate", "changed", "change", "reason"} and "reason" in ws:
        return "ownercorr_meaning"
    if "automutated" in ws and ws & {"manual", "processed"}:
        return "automut_manual"
    if "merge" in ws and ws & {"indicate", "mean", "associated", "linked"} and "what" in ws and ("indicate" in ws or "mean" in ws):
        return "merge_meaning"
    if "payment" in ws and (cmp_ or "missing" in ws) and ws & {"missing", "records", "record"}:
        return "payment_missing"
    return None


def claims(message: str) -> bool:
    return parse(message) is not None


# ── helpers ─────────────────────────────────────────────────────────────────
def _v(x) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    return None if s in ("", "-", "...") or s.lower() in ("none", "null", "n/a", "not stated") else s


def _latin(s: str) -> str:
    s = s or ""
    if script_of(s) in ("tamil", "mixed"):
        s = tamil_to_latin(s)
    return re.sub(r"[^a-z ]+", "", s.lower().replace(".", " ")).strip()


def _name_cmp(a: Optional[str], b: Optional[str]) -> str:
    a, b = _v(a), _v(b)
    if not a or not b:
        return "n/a"
    la, lb = _latin(a), _latin(b)
    ta, tb = set(la.split()), set(lb.split())
    if la == lb or (ta and ta == tb):
        return "match"
    r = SequenceMatcher(None, la.replace(" ", ""), lb.replace(" ", "")).ratio()
    return "similar" if r >= 0.75 else "differs"


def _day(x) -> Optional[datetime]:
    x = _v(x)
    if not x:
        return None
    try:
        return datetime.strptime(x[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _t(ta, en, tamil):
    return tamil if ta else en


_SYM = {"match": "✔", "similar": "◐", "differs": "✘", "n/a": "–"}


def _row(label: str, a, b, verdict: str, nr: str) -> str:
    return f"{_SYM[verdict]} {label}: {_v(a) or nr}  |  {_v(b) or nr}"


async def other_with_same_address(db, officer, app: str, row: Dict[str, Any]) -> List[str]:
    """Other applications (in the officer's jurisdiction) filed with the same address or mobile."""
    from sqlalchemy import text
    from backend.services.postgres import get_applications_by_numbers
    addr = (_v(row.get("appl_address_cur")) or "").lower()
    mob = _v(row.get("mobile_no"))
    nums: List[str] = []
    for tbl in ("sub_div_patta_transfer_application_information_urban", "full_field_patta_transfer_application_information"):
        try:
            async with db.begin_nested():
                q = (f"SELECT appl_id, lower(appl_address_cur) = :a AS same_addr, mobile_no = :m AS same_mob FROM {tbl} "
                     "WHERE appl_id <> :id AND ((:a <> '' AND lower(appl_address_cur) = :a) OR (:m <> '' AND mobile_no = :m))")
                for r in (await db.execute(text(q), {"a": addr, "m": mob or "", "id": app})).all():
                    nums.append(r[0])
        except Exception:
            continue
    if not nums:
        return []
    got = await get_applications_by_numbers(db, officer, nums, include_rejected=True)
    return [a["application_number"] for a in (got or {}).get("applications") or []]


async def answer(kind: str, message: str, app: str, db, officer, detail: Dict[str, Any],
                 info: Optional[Dict[str, Any]], owners: List[Dict[str, Any]], subs: List[Dict[str, Any]],
                 flow: Optional[Dict[str, Any]], ta: bool) -> str:
    nr = _t(ta, "not recorded", "பதிவில்லை")
    L: List[str] = []
    head = f"{app}: "
    o = owners[0] if owners else None
    info = info or {}

    def need_owner():
        return _t(ta, f"No old-owner (chitta) record is held for {app}, so there is nothing to compare with.",
                  f"{app}-க்கு பழைய உரிமையாளர் (சிட்டா) பதிவு இல்லை; ஒப்பிட எதுவும் இல்லை.")

    if kind in ("applicant_owner", "all_diffs"):
        if not o:
            return need_owner()
        ws = set(normalise(message).split())
        want_all = kind == "all_diffs" or not (ws & {"name", "father", "mother", "dob"})
        if want_all or "name" in ws:
            L.append(_row(_t(ta, "Applicant name vs old owner", "விண்ணப்பதாரர் பெயர் vs பழைய உரிமையாளர்"),
                          info.get("appl_name") or detail.get("applicant_name"), o.get("owner_name") or o.get("owner_ename"),
                          _name_cmp(info.get("appl_name") or detail.get("applicant_name"), o.get("owner_name") or o.get("owner_ename")), nr))
        if want_all or "father" in ws:
            L.append(_row(_t(ta, "Applicant father name vs owner's relative", "தந்தை பெயர் vs உரிமையாளரின் உறவினர்"),
                          info.get("father_name") or detail.get("applicant_father_name"), o.get("relative_name") or o.get("relative_ename"),
                          _name_cmp(info.get("father_name") or detail.get("applicant_father_name"), o.get("relative_name") or o.get("relative_ename")), nr))
        if want_all or "mother" in ws:
            L.append(_t(ta, f"– Mother name: applicant {_v(info.get('mother_name')) or nr}; the old-owner record has no mother-name column",
                        f"– தாயார் பெயர்: விண்ணப்பதாரர் {_v(info.get('mother_name')) or nr}; பழைய உரிமையாளர் பதிவில் அந்த புலம் இல்லை"))
        if want_all or "dob" in ws:
            L.append(_t(ta, f"– DOB: applicant {_v(info.get('dob')) or _v(detail.get('applicant_dob')) or nr}; the old-owner record has no DOB column",
                        f"– பிறந்த தேதி: விண்ணப்பதாரர் {_v(info.get('dob')) or _v(detail.get('applicant_dob')) or nr}; பழைய உரிமையாளர் பதிவில் அந்த புலம் இல்லை"))
        if kind == "all_diffs":
            pn = detail.get("patta_number")
            L.append(_row(_t(ta, "Patta no (application vs owner record)", "பட்டா எண் (விண்ணப்பம் vs உரிமையாளர் பதிவு)"), pn,
                          o.get("existing_patta_no"), "n/a" if not (_v(pn) and _v(o.get("existing_patta_no"))) else
                          ("match" if str(pn).strip() == str(o.get("existing_patta_no")).strip() else "differs"), nr))
            sn = detail.get("survey_number")
            L.append(_row(_t(ta, "Survey no", "சர்வே எண்"), sn, o.get("survey_no"),
                          "n/a" if not (_v(sn) and _v(o.get("survey_no"))) else
                          ("match" if str(sn).strip() == str(o.get("survey_no")).strip() else "differs"), nr))
            for k, lab in (("ward_code", "Ward code"), ("block_code", "Block code")):
                a, b = info.get(k), o.get(k)
                L.append(_row(lab, a, b, "n/a" if not (_v(a) and _v(b)) else ("match" if str(a) == str(b) else "differs"), nr))
        L.append(_t(ta, "Note: a patta-transfer applicant is usually the new owner, so an applicant that differs from the old owner is expected.",
                    "குறிப்பு: பட்டா மாற்ற விண்ணப்பதாரர் பொதுவாக புதிய உரிமையாளர்; பழைய உரிமையாளரிடமிருந்து வேறுபடுவது எதிர்பார்க்கப்படுவதே."))
        L.append(_t(ta, "(✔ match, ◐ similar, ✘ differs, – not comparable)", "(✔ பொருந்தும், ◐ ஒத்தது, ✘ வேறுபடும், – ஒப்பிட இயலாது)"))
    elif kind == "status_flow":
        st = detail.get("status")
        if flow:
            L.append(_t(ta, f"Application status: {st}; correction / modification flow: the desk chain is recorded (reason code {_v(flow.get('reason_code')) or nr}) but the flow table carries no status column of its own.",
                        f"விண்ணப்ப நிலை: {st}; திருத்தம் / மாற்றம் பணிப்பாய்வு பதிவு உள்ளது, ஆனால் அதில் தனி நிலை புலம் இல்லை."))
        else:
            L.append(_t(ta, f"Application status: {st}. No correction / modification flow record is held for this application, so there is no flow status to compare with.",
                        f"விண்ணப்ப நிலை: {st}. இந்த விண்ணப்பத்துக்கு திருத்தம் / மாற்றம் பணிப்பாய்வு பதிவு இல்லை; ஒப்பிட நிலை இல்லை."))
        if info.get("appl_status"):
            L.append(_t(ta, f"Application-information status text: {info['appl_status']}", f"விண்ணப்பத் தகவல் நிலை: {info['appl_status']}"))
    elif kind == "address_other":
        same = await other_with_same_address(db, officer, app, info) if info else []
        cur, perm = _v(info.get("appl_address_cur")), _v(info.get("appl_address_perm"))
        L.append(_t(ta, f"Current address: {cur or nr}", f"தற்போதைய முகவரி: {cur or nr}"))
        if perm is not None or "appl_address_perm" in info:
            L.append(_t(ta, f"Permanent address: {perm or nr}", f"நிரந்தர முகவரி: {perm or nr}"))
        L.append(_t(ta, ("Other applications in your jurisdiction with the same address or mobile: " + ", ".join(same)) if same else
                    "No other application in your jurisdiction was filed with the same address or mobile number.",
                    ("அதே முகவரி / கைபேசியுடன் உங்கள் எல்லையில் உள்ள பிற விண்ணப்பங்கள்: " + ", ".join(same)) if same else
                    "அதே முகவரி அல்லது கைபேசி எண்ணுடன் உங்கள் எல்லையில் வேறு விண்ணப்பம் இல்லை."))
    elif kind in ("challan_timeline", "payment_missing"):
        cd, sd = _day(info.get("challan_date")), _day(detail.get("submission_date"))
        fields = [("challan_no", "Challan no"), ("challan_date", "Challan date"), ("treasury", "Treasury"), ("bank", "Bank"),
                  ("bank_branch", "Bank branch"), ("paymentmode", "Payment mode"), ("amount", "Amount")]
        present = [lab for k, lab in fields if k in info and _v(info.get(k))]
        absent = [lab for k, lab in fields if k in info and not _v(info.get(k))]
        absent_col = [lab for k, lab in fields if k not in info]
        if kind == "challan_timeline":
            L.append(_t(ta, f"Submitted: {detail.get('submission_date')}; challan date: {_v(info.get('challan_date')) or nr}",
                        f"சமர்ப்பித்தது: {detail.get('submission_date')}; சலான் தேதி: {_v(info.get('challan_date')) or nr}"))
            if cd and sd:
                d = (sd - cd).days
                L.append(_t(ta, "The challan is dated the same day as the submission." if d == 0 else
                            (f"The challan is dated {abs(d)} day(s) {'before' if d > 0 else 'after'} the submission."),
                            "சலான் தேதியும் சமர்ப்பிப்பு தேதியும் ஒன்றே." if d == 0 else
                            f"சலான் சமர்ப்பிப்புக்கு {abs(d)} நாள் {'முன்' if d > 0 else 'பின்'} தேதியிட்டது."))
            else:
                L.append(_t(ta, "The two dates cannot be compared: one of them is not recorded.", "ஒரு தேதி பதிவில்லாததால் ஒப்பிட முடியவில்லை."))
        L.append(_t(ta, "Payment details recorded: " + (", ".join(present) or "none"), "பதிவான கட்டண விவரங்கள்: " + (", ".join(present) or "இல்லை")))
        if absent:
            L.append(_t(ta, "Blank: " + ", ".join(absent), "காலி: " + ", ".join(absent)))
        if absent_col:
            L.append(_t(ta, "Not a column of this file's extract: " + ", ".join(absent_col), "இந்த கோப்பின் பதிவில் இல்லாத புலங்கள்: " + ", ".join(absent_col)))
        if detail.get("fee_amount") is not None and kind == "payment_missing":
            L.append(_t(ta, f"Fee on the application record: {detail.get('fee_amount')}", f"விண்ணப்ப பதிவில் கட்டணம்: {detail.get('fee_amount')}"))
    elif kind in ("phys_before_fv", "consistent_fv"):
        vp = _v(info.get("verifyphysical")) if "verifyphysical" in info else None
        fv = _v(info.get("proposed_field_visit_date"))
        L.append(_t(ta, f"Physical verification: {vp or nr}" + ("" if "verifyphysical" in info else " (this file's extract has no such column)"),
                    f"நேரடி சரிபார்ப்பு: {vp or nr}"))
        L.append(_t(ta, f"Proposed field visit date: {fv[:10] if fv else nr}", f"முன்மொழிந்த கள ஆய்வு தேதி: {fv[:10] if fv else nr}"))
        if kind == "consistent_fv":
            L.append(_t(ta, f"Application status: {detail.get('status')} (stage {detail.get('stage')})", f"விண்ணப்ப நிலை: {detail.get('status')} ({detail.get('stage')})"))
        L.append(_t(ta, "The register records no date for the physical verification, so whether it came before the visit cannot be determined."
                    if vp or "verifyphysical" in info else "Whether it came before the visit cannot be determined.",
                    "நேரடி சரிபார்ப்புக்கு தேதி பதிவில்லை; எனவே கள ஆய்வுக்கு முன் நடந்ததா என்று கூற இயலாது."))
    elif kind == "missing_vs_submitted":
        miss, enc = _v(info.get("missing_docs")), _v(info.get("enclosures"))
        L.append(_t(ta, f"Submitted on {detail.get('submission_date')}; status {detail.get('status')}.", f"{detail.get('submission_date')} அன்று சமர்ப்பிக்கப்பட்டது; நிலை {detail.get('status')}."))
        L.append(_t(ta, f"Missing documents: {miss or 'none recorded'}; enclosures submitted: {(enc or nr).rstrip('/')}",
                    f"விடுபட்ட ஆவணங்கள்: {miss or 'பதிவில்லை'}; இணைக்கப்பட்டவை: {(enc or nr).rstrip('/')}"))
    elif kind == "ownercorr_meaning":
        v = _v(info.get("owner_correction_reason")) if "owner_correction_reason" in info else None
        L.append(_t(ta, f"Owner correction reason: {v or nr}" + ("" if "owner_correction_reason" in info else " (held only for NISD files)"),
                    f"உரிமையாளர் திருத்த காரணம்: {v or nr}"))
        L.append(_t(ta, "A recorded reason would mean an owner detail was corrected; " + ("none is recorded, so nothing indicates a change." if not v else "one is recorded, so an owner detail was corrected."),
                    "காரணம் பதிவானால் உரிமையாளர் விவரம் திருத்தப்பட்டது என்று பொருள்; " + ("எதுவும் பதிவில்லை, எனவே மாற்றம் இல்லை." if not v else "பதிவு உள்ளது.")))
    elif kind == "automut_manual":
        v = _v(info.get("is_automutated")) if "is_automutated" in info else None
        L.append(_t(ta, f"Automutated flag: {v or nr}" + ("" if "is_automutated" in info else " (held only for NISD files)"), f"தானியங்கி பட்டா மாறுதல் கொடி: {v or nr}"))
        L.append(_t(ta, "Y means the transfer was automutated; otherwise the register does not say it was processed manually, only that no automutation is flagged.",
                    "Y என்றால் தானியங்கி மாறுதல்; இல்லையெனில் கைமுறை என்று பதிவேடு கூறவில்லை, தானியங்கி மாறுதல் குறிக்கப்படவில்லை என்பது மட்டுமே."))
    elif kind == "merge_meaning":
        m = _v(info.get("merge_applid")) or _v(detail.get("merged_application_id"))
        L.append(_t(ta, f"Merge application id: {m or nr}", f"இணைப்பு விண்ணப்ப எண்: {m or nr}"))
        L.append(_t(ta, "It names the lead application of a group of ISD files filed together for one large split; " + ("this file belongs to that group." if m else "this file is not part of such a group."),
                    "ஒரு பெரிய உட்பிரிவுக்காக சேர்ந்து தாக்கல் செய்யப்பட்ட ISD கோப்புகளின் முதன்மை விண்ணப்பத்தை இது குறிக்கிறது; " + ("இந்த கோப்பு அந்த குழுவில் உள்ளது." if m else "இந்த கோப்பு அத்தகைய குழுவில் இல்லை.")))
    if not L:
        return ""
    return head + "\n" + "\n".join("• " + l for l in L)
