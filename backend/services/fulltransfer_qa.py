"""The full-field patta transfer record of ONE NISD application (`full_field_patta_transfer_urban`):
parcel (survey, sub-division, old patta, land type, extent), registration (document no, place, date),
reason, ZDT patta / order / remarks / transaction status, succession / legal-heir / court details,
purchased area, street and door, tax, SIS recommendation, group numbers, owner-correction reason,
transaction type, direct transfer and land category -- and its cross-checks against the new-owner and
old-owner records.

Only an NISD (0153) file has a row. A code the register does not define (transaction status, SIS reason,
land type code, transaction type, direct-transfer flag, owner-correction code) is shown raw and said to be
undefined. A blank column is "not recorded". Typos, Tamil and Tanglish are understood. The caller checks
access. No LLM."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.services.crosscheck_qa import _v
from backend.utils.fuzzy import is_token_typo_match
from backend.utils.logger import get_logger

logger = get_logger(__name__)

_TAMIL = [("பதிவு எண்", " registration number "), ("பதிவு", " registration "), ("சர்வே", " survey "), ("உட்பிரிவு", " subdivision "),
          ("பட்டா", " patta "), ("நில வகை", " land type "), ("பரப்பளவு", " extent "), ("காரணம்", " reason "), ("எண்", " number "),
          ("நீதிமன்ற", " court "), ("வாரிசு", " succession "), ("வரி", " tax "), ("ஒப்பிடு", " compare "), ("பொருந்து", " match "),
          ("வேறுபாடு", " difference "), ("பரிந்துரை", " rec "), ("குறிப்பு", " remarks "), ("நிலை", " status "), ("தேதி", " date "),
          ("கதவு எண்", " door number "), ("தெரு", " street ")]
_EXACT = {
    "survay": "survey", "servey": "survey", "divison": "division", "extant": "extent", "extend": "extent", "regstration": "registration",
    "registation": "registration", "registeration": "registration", "regi": "registration", "regn": "registration", "documant": "document",
    "docment": "document", "doc": "document", "reson": "reason", "tranaction": "transaction", "transction": "transaction", "stauts": "status",
    "upadte": "update", "updated": "update", "update": "update", "succession": "succession", "sucession": "succession", "succesion": "succession",
    "certifcate": "certificate", "certficate": "certificate", "certificate": "certificate", "tranfer": "transfer", "transfer": "transfer",
    "recomendation": "rec", "reccomendation": "rec", "recommendation": "rec", "reccomend": "rec", "applcation": "application", "aplication": "application",
    "numbr": "number", "no": "number", "nam": "name", "corection": "correction", "correction": "correction", "boundries": "boundary",
    "ares": "ares", "are": "are", "yr": "year", "chitta": "old", "existing": "old", "agree": "match", "agrees": "match", "matches": "match",
    "support": "support", "supports": "support", "mismach": "mismatch", "mismatches": "mismatch", "consistant": "consistent",
    "diffrences": "difference", "diffrence": "difference", "differences": "difference", "diffrent": "difference", "different": "difference",
    "changed": "change", "changes": "change", "comparing": "compare", "compare": "compare", "match": "match", "heir": "heir",
    "legal": "legal", "court": "court", "purchased": "purchased", "purchase": "purchased", "unit": "unit", "category": "category",
    "catagory": "category", "direct": "direct", "tranfer": "transfer", "regstered": "registration", "registered": "registration",
    "zdt": "zdt", "sis": "sis", "tax": "tax", "taxrate": "taxrate", "door": "door", "street": "street", "group": "group", "u1": "u1", "u2": "u2", "u3": "u3",
    "transfe": "transfer", "oder": "order", "ordr": "order", "remarkks": "remarks", "statuz": "status", "recomend": "rec", "recommen": "rec",
    "recommend": "rec", "recommended": "rec",
}
_VOCAB = ["registration", "document", "reason", "transaction", "certificate", "succession", "recommendation", "correction", "category",
          "purchased", "subdivision", "survey", "extent", "remarks", "status", "street", "boundary", "transfer"]


def _canon(tok: str) -> str:
    if tok in _EXACT:
        return _EXACT[tok]
    if tok.isascii() and tok.isalpha() and len(tok) >= 6:
        hits = {w for w in _VOCAB if is_token_typo_match(tok, w)}
        if len(hits) == 1:
            return {"recommendation": "rec"}.get(next(iter(hits)), next(iter(hits)))
    return tok


def normalise(message: str) -> str:
    t = (message or "").lower()
    for ta, en in _TAMIL:
        t = t.replace(ta, en)
    t = re.sub(r"\bsub[\s\-_.]*d[a-z]*v[a-z]*", " subdivision ", t)
    t = re.sub(r"\bfull[\s\-]*field\b", " fullfield ", t)
    t = re.sub(r"\bnew[\s\-]*owner\b", " newowner ", t)
    t = re.sub(r"\bold[\s\-]*owner\b", " oldowner ", t)
    t = re.sub(r"[?!.:()\"',;/&]", " ", t)
    return re.sub(r"\s+", " ", " ".join(_canon(x) for x in re.findall(r"[a-z0-9]+|[^\sa-z0-9]+", t))).strip()


(SURVEY, SUBDIV, OLDPATTA, LANDTYPE, EXTENT, EXTENT_ARES, REGDOC, REASON, REGPLACE, REGYEAR, REGDATE, ZDTPATTA, TXNSTATUS, ZDTORDER,
 ZDTREMARKS, SUBMIT, UPDATE, TAXRATE, SUCCNO, SUCCISS, LEGALHEIR, COURTNO, COURTDATE, COURTNAME, PURCHASED, TXNCODE, STREET, SISREC,
 SISREMARKS, SISREASON, DOOR, TOTTAX, GROUP, OWNERCORR, TRANSTYPE, DIRECT, LANDCAT) = (
    "survey", "subdiv", "old_patta", "land_type", "extent", "extent_ares", "regdoc", "reason", "regplace", "regyear", "regdate", "zdt_patta",
    "txn_status", "zdt_order", "zdt_remarks", "submit", "update", "taxrate", "succ_no", "succ_issuer", "legal_heir", "court_no", "court_date",
    "court_name", "purchased", "txn_code", "street", "sis_rec", "sis_remarks", "sis_reason", "door", "tot_tax", "group", "owner_corr",
    "trans_type", "direct", "land_cat")
DISTINCT = {OLDPATTA, REGDOC, REGPLACE, REGYEAR, REGDATE, ZDTPATTA, TXNSTATUS, ZDTORDER, ZDTREMARKS, SUCCNO, SUCCISS,
            LEGALHEIR, COURTNO, COURTDATE, COURTNAME, PURCHASED, TXNCODE, TOTTAX, TRANSTYPE, DIRECT, LANDCAT,
            # "sis" + "rec" together (SIS recommendation) is as unambiguous a
            # match as any of the above -- there is no other "SIS
            # recommendation"-shaped field this could be confused with. Without
            # it here, claims_strong() (used by _cm_strong's OR-gate) never
            # claimed a bare "what was the SIS recommendation?" follow-up, so a
            # question _cm_strong's own docstring says should resolve from
            # chat_history ("the reading must keep using chat_history the way
            # it always has") instead fell to general_query -- the LLM/agent
            # fallback, which on a field this register never populates for any
            # application, sometimes produced a confident but unhelpful answer
            # built from unrelated documents instead of "not recorded".
            SISREC,
            # Same gap, same fix, for "SIS remarks".
            SISREMARKS}


def parse(message: str) -> Dict[str, Any]:
    if "everyfield" in (message or "").lower() and "full field" in (message or "").lower():  # "which fields are blank": every column, the blanks are picked out afterwards
        _all = [SURVEY, SUBDIV, OLDPATTA, LANDTYPE, EXTENT, REGDOC, REASON, REGPLACE, REGYEAR, REGDATE, ZDTPATTA, TXNSTATUS, ZDTORDER,
                ZDTREMARKS, SUBMIT, UPDATE, TAXRATE, SUCCNO, SUCCISS, LEGALHEIR, COURTNO, COURTDATE, COURTNAME, PURCHASED, TXNCODE,
                STREET, SISREC, SISREMARKS, SISREASON, DOOR, TOTTAX, GROUP, OWNERCORR, TRANSTYPE, DIRECT, LANDCAT]
        return {"kind": "field", "keys": _all, "ws": set(), "distinct": True, "fullfield": True}
    t = normalise(message)
    ws = set(t.split())
    keys: List[str] = []

    def add(k):
        if k not in keys:
            keys.append(k)

    if "survey" in ws and "group" not in ws:
        add(SURVEY)
    if "subdivision" in ws and "group" not in ws:
        add(SUBDIV)
    if "old" in ws and "patta" in ws and ws & {"number"}:
        add(OLDPATTA)
    if "land" in ws and "type" in ws and "code" in ws:
        add(LANDTYPE)
    if ws & {"extent", "u1", "u2", "u3"} and "ares" not in ws:
        add(EXTENT)
    if "ares" in ws and ws & {"extent", "area"}:
        add(EXTENT_ARES)
    if "registration" in ws and "document" in ws:
        add(REGDOC)
    if "reason" in ws and ws & {"transfer", "patta"} and not ws & {"sis", "rec", "correction"}:
        add(REASON)
    if "registration" in ws and ws & {"where", "place"}:
        add(REGPLACE)
    if "registration" in ws and ws & {"year"}:
        add(REGYEAR)
    if "registration" in ws and ws & {"date", "when", "on"} and "year" not in ws:
        add(REGDATE)
    if "zdt" in ws and "patta" in ws:
        add(ZDTPATTA)
    if "transaction" in ws and "status" in ws:
        add(TXNSTATUS)
    if "zdt" in ws and "order" in ws:
        add(ZDTORDER)
    if "zdt" in ws and "remarks" in ws:
        add(ZDTREMARKS)
    # the officer's words for the same ZDT columns: "transfer order number / date", "order remarks",
    # "new patta" (the patta the order generates), "transfer status", "when was the transfer completed"
    if "order" in ws and "court" not in ws:
        add(ZDTREMARKS if "remarks" in ws else ZDTORDER)
    if "transfer" in ws and ws & {"completed", "complete", "done", "finished"}:
        add(ZDTORDER)
    if "new" in ws and "patta" in ws:
        add(ZDTPATTA)
    if "transfer" in ws and "status" in ws:
        add(TXNSTATUS)
    if "transfer" in ws and "type" in ws:
        add(TRANSTYPE)
    if "sis" in ws and "rec" in ws and "why" in ws:
        add(SISREASON)
    if "submit" in ws or ("application" in ws and "submitted" in ws) or "submission" in ws:
        add(SUBMIT)
    if "update" in ws and ("date" in ws or "last" in ws):
        add(UPDATE)
    if "tax" in ws and "rate" in ws:
        add(TAXRATE)
    if "succession" in ws and ws & {"number", "date"} and "issued" not in ws and "who" not in ws:
        add(SUCCNO)
    if "succession" in ws and ws & {"who", "issued", "issuer"}:
        add(SUCCISS)
    if "legal" in ws and "heir" in ws:
        add(LEGALHEIR)
    if "court" in ws and "order" in ws and ws & {"number", "no"} and "date" not in ws:
        add(COURTNO)
    if "court" in ws and ("date" in ws):
        add(COURTDATE)
    if "court" in ws and "name" in ws:
        add(COURTNAME)
    if "court" in ws and "order" in ws and not ws & {"number", "date", "name"}:
        add(COURTNO)
    if "purchased" in ws and ws & {"area", "unit"}:
        add(PURCHASED)
    if "transaction" in ws and "code" in ws:
        add(TXNCODE)
    if "street" in ws:
        add(STREET)
    if "sis" in ws and "rec" in ws and "reason" not in ws:
        add(SISREC)
    if "sis" in ws and "remarks" in ws:
        add(SISREMARKS)
    if "sis" in ws and "rec" in ws and "reason" in ws:
        add(SISREASON)
    if "door" in ws:
        add(DOOR)
    if "tax" in ws and "total" in ws:
        add(TOTTAX)
    if "group" in ws:
        add(GROUP)
    if "owner" in ws and "correction" in ws and "reason" in ws or ("correction" in ws and "reason" in ws):
        add(OWNERCORR)
    if "transaction" in ws and "type" in ws:
        add(TRANSTYPE)
    if "direct" in ws and "transfer" in ws:
        add(DIRECT)
    if "land" in ws and "category" in ws:
        add(LANDCAT)

    cmp_ = ws & {"compare", "match", "mismatch", "consistent", "difference", "change", "support"}
    fullfield = "fullfield" in ws
    kind = None
    if cmp_ and (set(keys) & DISTINCT or fullfield or ws & {"zdt", "purchased", "registration", "succession", "court", "street", "door", "group",
                                                         "direct", "correction", "newowner", "oldowner", "transaction"}):
        kind = "compare"
    elif keys:
        kind = "field"
    return {"kind": kind, "keys": keys, "ws": ws, "distinct": bool(set(keys) & DISTINCT), "fullfield": fullfield}


def claims_strong(message: str) -> bool:
    p = parse(message)
    return p["kind"] is not None and (p["distinct"] or p["kind"] == "compare" and (p["distinct"] or p["fullfield"]))


def claims(message: str) -> bool:
    return parse(message)["kind"] is not None


_SQL = ("SELECT survey_no, sub_div_no, old_patta_no, land_type_code, extent_u1, extent_u2, extent_u3, regi_doc_no, reason, regi_place, regi_on, "
        "zdt_patta_no, transaction_status, zdt_order_no, zdt_order_dt, zdt_remarks, update_dt, submit_date, extentares, taxrate, "
        "successioncertificateno, successioncertificatedate, successioncertificateissuedby, legalheircertificateplaceofissue, "
        "court_order_no, court_order_date, court_name, street_code, purchased_area, purchased_area_unit, transaction_code, oldstreet_code, "
        "sis_rec, sis_remarks, sis_rec_reason, new_door_no, new_tot_tax, old_door_no, old_tot_tax, group_survey_no, group_sub_div_no, "
        "group_patta_no, owner_correction_reason, trans_type, direct_transfer, land_cat_code, ward_code, block_code "
        "FROM full_field_patta_transfer_urban WHERE appl_id = :a ORDER BY row_id")


async def load(db, app: str) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    try:
        async with db.begin_nested():
            return [dict(r) for r in (await db.execute(text(_SQL), {"a": app})).mappings().all()]
    except Exception as exc:
        logger.warning(f"fulltransfer_qa: could not read the transfer table ({type(exc).__name__})")
        return []


def _t(ta, en, tamil):
    return tamil if ta else en


def _undef(ta):
    return _t(ta, " (a code the register does not define)", " (பதிவேடு வரையறுக்காத குறியீடு)")


def _extent(r: Dict[str, Any]) -> Optional[str]:
    try:
        ha, ar, sq = (float(r.get(k) or 0) for k in ("extent_u1", "extent_u2", "extent_u3"))
    except (TypeError, ValueError):
        return None
    return f"{ha:g} ha {ar:g} ares {sq:g} sq.m = {10000 * ha + 100 * ar + sq:g} sq.m"


def answer(keys: List[str], app: str, rows: List[Dict[str, Any]], ta: bool) -> str:
    if not rows:
        return _t(ta, f"No full-field patta transfer record is held for application {app}. Only an NISD (0153) file carries one.",
                  f"விண்ணப்பம் {app}-க்கு முழு புல பட்டா மாற்ற பதிவு இல்லை. NISD (0153) கோப்புக்கு மட்டுமே அது இருக்கும்.")
    r = rows[0]
    nr = _t(ta, "not recorded", "பதிவு செய்யப்படவில்லை")
    L: List[str] = []

    def g(k):
        return _v(r.get(k))

    for k in keys:
        if k == SURVEY:
            L.append(_t(ta, "Survey no", "சர்வே எண்") + f": {g('survey_no') or nr}")
        elif k == SUBDIV:
            L.append(_t(ta, "Sub-division no", "உட்பிரிவு எண்") + f": {g('sub_div_no') or nr}")
        elif k == OLDPATTA:
            L.append(_t(ta, "Old patta no", "பழைய பட்டா எண்") + f": {g('old_patta_no') or nr}")
        elif k == LANDTYPE:
            L.append(_t(ta, "Land type code", "நில வகை குறியீடு") + f": {g('land_type_code') or nr}" + (_undef(ta) if g("land_type_code") else ""))
        elif k == EXTENT:
            L.append(_t(ta, "Extent (u1 ha / u2 ares / u3 sq.m)", "பரப்பளவு (u1 ஹெ / u2 ஏர்ஸ் / u3 ச.மீ)") + ": " +
                     f"{g('extent_u1') or '0'} / {g('extent_u2') or '0'} / {g('extent_u3') or '0'}  =  {_extent(r) or nr}")
        elif k == EXTENT_ARES:
            try:
                ares = (float(r.get("extent_u1") or 0) * 100 + float(r.get("extent_u2") or 0) + float(r.get("extent_u3") or 0) / 100)
                L.append(_t(ta, "Extent in ares", "பரப்பளவு (ஏர்ஸில்)") + f": {ares:g}" + (_t(ta, "  (the extent-in-ares column itself is blank; this is computed from u1 / u2 / u3)", "  (ஏர்ஸ் புலம் காலி; u1 / u2 / u3-லிருந்து கணக்கிடப்பட்டது)") if not g("extentares") else ""))
            except (TypeError, ValueError):
                L.append(_t(ta, "Extent in ares: ", "ஏர்ஸ்: ") + nr)
        elif k == REGDOC:
            L.append(_t(ta, "Registration document no", "பதிவு ஆவண எண்") + f": {g('regi_doc_no') or nr}")
        elif k == REASON:
            L.append(_t(ta, "Reason for the transfer", "மாற்றத்துக்கான காரணம்") + f": {g('reason') or nr}")
        elif k == REGPLACE:
            L.append(_t(ta, "Registered at", "பதிவு செய்த இடம்") + f": {g('regi_place') or nr}")
        elif k == REGYEAR:
            m = re.search(r"(\d{4})", g("regi_on") or "")
            L.append(_t(ta, "Registration year", "பதிவு ஆண்டு") + f": {m.group(1) if m else nr}")
        elif k == REGDATE:
            L.append(_t(ta, "Registered on", "பதிவு தேதி") + f": {g('regi_on') or nr}")
        elif k == ZDTPATTA:
            L.append(_t(ta, "ZDT patta no", "ZDT பட்டா எண்") + f": {g('zdt_patta_no') or nr}")
        elif k == TXNSTATUS:
            L.append(_t(ta, "Transaction status", "பரிவர்த்தனை நிலை") + f": {g('transaction_status') or nr}" + (_undef(ta) if g("transaction_status") else ""))
        elif k == ZDTORDER:
            L.append(_t(ta, "ZDT order no / date", "ZDT ஆணை எண் / தேதி") + f": {g('zdt_order_no') or nr} / {g('zdt_order_dt') or nr}")
        elif k == ZDTREMARKS:
            L.append(_t(ta, "ZDT remarks", "ZDT குறிப்புகள்") + f": {g('zdt_remarks') or nr}")
        elif k == SUBMIT:
            L.append(_t(ta, "Submitted", "சமர்ப்பித்தது") + f": {(g('submit_date') or nr)[:19]}")
        elif k == UPDATE:
            L.append(_t(ta, "Last updated", "கடைசியாக புதுப்பித்தது") + f": {(g('update_dt') or nr)[:19]}")
        elif k == TAXRATE:
            L.append(_t(ta, "Tax rate", "வரி விகிதம்") + f": {g('taxrate') or nr}")
        elif k == SUCCNO:
            L.append(_t(ta, "Succession certificate no / date", "வாரிசு சான்றிதழ் எண் / தேதி") + f": {g('successioncertificateno') or nr} / {g('successioncertificatedate') or nr}")
        elif k == SUCCISS:
            L.append(_t(ta, "Succession certificate issued by", "வாரிசு சான்றிதழ் வழங்கியவர்") + f": {g('successioncertificateissuedby') or nr}")
        elif k == LEGALHEIR:
            L.append(_t(ta, "Legal-heir certificate place of issue", "சட்டபூர்வ வாரிசு சான்றிதழ் வழங்கிய இடம்") + f": {g('legalheircertificateplaceofissue') or nr}")
        elif k == COURTNO:
            L.append(_t(ta, "Court order no", "நீதிமன்ற ஆணை எண்") + f": {g('court_order_no') or nr}")
        elif k == COURTDATE:
            L.append(_t(ta, "Court order date", "நீதிமன்ற ஆணை தேதி") + f": {g('court_order_date') or nr}")
        elif k == COURTNAME:
            L.append(_t(ta, "Court name", "நீதிமன்றம்") + f": {g('court_name') or nr}")
        elif k == PURCHASED:
            L.append(_t(ta, "Purchased area / unit", "வாங்கிய பரப்பு / அலகு") + f": {g('purchased_area') or nr} / {g('purchased_area_unit') or nr}")
        elif k == TXNCODE:
            L.append(_t(ta, "Transaction code", "பரிவர்த்தனை குறியீடு") + f": {g('transaction_code') or nr}" + (_undef(ta) if g("transaction_code") else ""))
        elif k == STREET:
            L.append(_t(ta, "Old street code / current street code", "பழைய தெரு குறியீடு / தற்போதைய தெரு குறியீடு") + f": {g('oldstreet_code') or nr} / {g('street_code') or nr}")
        elif k == SISREC:
            rc = g("sis_rec")
            L.append(_t(ta, "SIS recommendation", "SIS பரிந்துரை") + ": " + ({"Y": "Recommended (Y)", "N": "Not recommended (N)"}.get((rc or "").upper(), rc or nr)))
        elif k == SISREMARKS:
            L.append(_t(ta, "SIS remarks", "SIS குறிப்புகள்") + f": {g('sis_remarks') or nr}")
        elif k == SISREASON:
            L.append(_t(ta, "SIS recommendation reason code", "SIS பரிந்துரை காரண குறியீடு") + f": {g('sis_rec_reason') or nr}" + (_undef(ta) if g("sis_rec_reason") else ""))
        elif k == DOOR:
            L.append(_t(ta, "Old door no / new door no", "பழைய கதவு எண் / புதிய கதவு எண்") + f": {g('old_door_no') or nr} / {g('new_door_no') or nr}")
        elif k == TOTTAX:
            L.append(_t(ta, "Old total tax / new total tax", "பழைய மொத்த வரி / புதிய மொத்த வரி") + f": {g('old_tot_tax') or nr} / {g('new_tot_tax') or nr}")
        elif k == GROUP:
            L.append(_t(ta, "Group survey / sub-division / patta no", "குழு சர்வே / உட்பிரிவு / பட்டா எண்") +
                     f": {g('group_survey_no') or nr} / {g('group_sub_div_no') or nr} / {g('group_patta_no') or nr}")
        elif k == OWNERCORR:
            L.append(_t(ta, "Owner correction reason code", "உரிமையாளர் திருத்த காரண குறியீடு") + f": {g('owner_correction_reason') or nr}" + (_undef(ta) if g("owner_correction_reason") else ""))
        elif k == TRANSTYPE:
            L.append(_t(ta, "Transaction type", "பரிவர்த்தனை வகை") + f": {g('trans_type') or nr}" + (_undef(ta) if g("trans_type") else ""))
        elif k == DIRECT:
            d = g("direct_transfer")
            L.append(_t(ta, "Direct transfer", "நேரடி மாற்றம்") + ": " + ({"Y": "Yes (Y)", "N": "No (N)"}.get((d or "").upper(), d or nr)))
        elif k == LANDCAT:
            L.append(_t(ta, "Land category code", "நில வகைப்பாடு குறியீடு") + f": {g('land_cat_code') or nr}" + (_undef(ta) if g("land_cat_code") else ""))
    if not L:
        return ""
    return f"{L[0]}  [{app}]" if len(L) == 1 else f"{app} (full-field transfer):\n" + "\n".join("• " + x for x in L)


def _eq(a, b) -> str:
    a, b = _v(a), _v(b)
    if not a or not b:
        return "n/a"
    return "match" if str(a).strip().lower() == str(b).strip().lower() else "differs"


_SYM = {"match": "✔", "differs": "✘", "n/a": "–"}


def _ln(lab: str, a, b, v: str, nr: str) -> str:
    return f"{_SYM[v]} {lab}: {_v(a) or nr}  |  {_v(b) or nr}"


def answer_compare(p: Dict[str, Any], app: str, rows: List[Dict[str, Any]], new: List[Dict[str, Any]], old: List[Dict[str, Any]],
                   subs: List[Dict[str, Any]], detail: Dict[str, Any], ta: bool) -> str:
    if not rows:
        return answer([], app, [], ta)
    r = rows[0]
    nr = _t(ta, "not recorded", "பதிவில்லை")
    ws = p["ws"]
    n0 = new[0] if new else None
    o0 = old[0] if old else None
    narrowed = ws & {"patta", "survey", "subdivision", "door", "tax", "purchased", "reason", "succession", "court", "rec", "group", "correction",
                     "direct", "street", "zdt"}
    all_ = not narrowed or p["fullfield"] or bool(ws & {"mismatch", "difference"} and not narrowed - {"patta", "survey", "zdt"}) or \
        bool({"mismatch"} & ws)

    def want(*k):
        return all_ or bool(ws & set(k))
    L: List[str] = []
    pat_old = all_ or bool(ws & {"oldowner", "old"})
    pat_zdt = all_ or bool(ws & {"zdt", "newowner"})
    if want("patta", "zdt") and (pat_old or pat_zdt):
        if o0 and pat_old:
            L.append(_ln(_t(ta, "Old patta (transfer) vs old owner's patta", "பழைய பட்டா (மாற்றம்) vs பழைய உரிமையாளர் பட்டா"), r.get("old_patta_no"), o0.get("patta_no"),
                         _eq(r.get("old_patta_no"), o0.get("patta_no")), nr))
        elif pat_old:
            L.append(_t(ta, "– no old-owner record to compare the old patta with", "– பழைய பட்டாவை ஒப்பிட பழைய உரிமையாளர் பதிவு இல்லை"))
        if n0 and pat_zdt:
            L.append(_ln(_t(ta, "ZDT patta vs new owner's patta", "ZDT பட்டா vs புதிய உரிமையாளர் பட்டா"), r.get("zdt_patta_no"), n0.get("patta_no"),
                         _eq(r.get("zdt_patta_no"), n0.get("patta_no")), nr))
        elif pat_zdt:
            L.append(_t(ta, "– no new-owner record to compare the ZDT patta with", "– ZDT பட்டாவை ஒப்பிட புதிய உரிமையாளர் பதிவு இல்லை"))
        if (all_ or ("old" in ws and "zdt" in ws)) and not ws & {"oldowner", "newowner"} or all_:
            L.append(_ln(_t(ta, "Old patta vs ZDT patta", "பழைய பட்டா vs ZDT பட்டா"), r.get("old_patta_no"), r.get("zdt_patta_no"),
                         _eq(r.get("old_patta_no"), r.get("zdt_patta_no")), nr))
        if not _v(r.get("zdt_patta_no")):
            L.append(_t(ta, "– the ZDT patta is not issued yet, so it cannot be compared", "– ZDT பட்டா இன்னும் வழங்கப்படவில்லை"))
    if all_ or ws & {"survey", "subdivision"}:
        if n0:
            L.append(_ln(_t(ta, "Survey no (transfer vs new owner)", "சர்வே எண் (மாற்றம் vs புதிய உரிமையாளர்)"), r.get("survey_no"), n0.get("survey_no"), _eq(r.get("survey_no"), n0.get("survey_no")), nr))
            L.append(_ln(_t(ta, "Sub-division no (transfer vs new owner)", "உட்பிரிவு எண் (மாற்றம் vs புதிய உரிமையாளர்)"), r.get("sub_div_no"), n0.get("sub_div_no"), _eq(r.get("sub_div_no"), n0.get("sub_div_no")), nr))
        else:
            L.append(_t(ta, "– no new-owner record to compare survey / sub-division with", "– ஒப்பிட புதிய உரிமையாளர் பதிவு இல்லை"))
    if all_ or ws & {"door", "street"}:
        L.append(_ln(_t(ta, "Old door vs new door", "பழைய கதவு எண் vs புதிய"), r.get("old_door_no"), r.get("new_door_no"), _eq(r.get("old_door_no"), r.get("new_door_no")), nr))
        L.append(_ln(_t(ta, "Old street vs current street code", "பழைய தெரு vs தற்போதைய தெரு குறியீடு"), r.get("oldstreet_code"), r.get("street_code"), _eq(r.get("oldstreet_code"), r.get("street_code")), nr))
    if want("tax"):
        a, b = _v(r.get("old_tot_tax")), _v(r.get("new_tot_tax"))
        L.append(_ln(_t(ta, "Old vs new total tax", "பழைய vs புதிய மொத்த வரி"), a, b, "n/a" if not (a and b) else ("match" if float(a) == float(b) else "differs"), nr))
        L.append(_t(ta, f"– tax rate: {_v(r.get('taxrate')) or nr}", f"– வரி விகிதம்: {_v(r.get('taxrate')) or nr}"))
        L.append(_t(ta, "The total tax is unchanged." if a and b and float(a) == float(b) else "", "மொத்த வரி மாறவில்லை." if a and b and float(a) == float(b) else ""))
    if want("purchased", "extent"):
        L.append(_t(ta, f"Purchased area {_v(r.get('purchased_area')) or nr} {_v(r.get('purchased_area_unit')) or ''}; extent of the application {_extent(r) or nr}. "
                    + ("They are in different units or one is blank, so they are not directly comparable." if not _v(r.get("purchased_area")) else "Compare after converting to the same unit."),
                    f"வாங்கிய பரப்பு {_v(r.get('purchased_area')) or nr}; விண்ணப்ப பரப்பளவு {_extent(r) or nr}."))
        try:
            pa = float(r.get("purchased_area") or 0)
            if pa and "square metre" in (r.get("purchased_area_unit") or "").lower():
                tot = 10000 * float(r.get("extent_u1") or 0) + 100 * float(r.get("extent_u2") or 0) + float(r.get("extent_u3") or 0)
                L.append(_ln(_t(ta, "Purchased area vs extent (sq.m)", "வாங்கிய பரப்பு vs பரப்பளவு (ச.மீ)"), f"{pa:g}", f"{tot:g}", "match" if abs(pa - tot) < 0.01 else "differs", nr))
        except (TypeError, ValueError):
            pass
    if all_ or ws & {"succession", "court", "registration"} or ("reason" in ws and "correction" not in ws):
        reason = _v(r.get("reason"))
        doc = _v(r.get("regi_doc_no"))
        succ = _v(r.get("successioncertificateno")) or _v(r.get("legalheircertificatep") ) or _v(r.get("legalheircertificateplaceofissue"))
        court = _v(r.get("court_order_no"))
        L.append(_t(ta, f"Reason: {reason or nr}; registration document: {doc or nr} ({_v(r.get('regi_place')) or nr}, {_v(r.get('regi_on')) or nr}); "
                        f"succession / legal-heir certificate: {succ or nr}; court order: {court or nr}.",
                    f"காரணம்: {reason or nr}; பதிவு ஆவணம்: {doc or nr}; வாரிசு சான்றிதழ்: {succ or nr}; நீதிமன்ற ஆணை: {court or nr}."))
        if reason and reason.lower().startswith(("sale", "settlement", "gift", "exchange", "partition")):
            L.append(_t(ta, f"A {reason.lower()} is a registered document, so a registration document number is expected -- " + ("it is recorded." if doc else "it is missing."),
                        f"{reason} பதிவு செய்யப்பட்ட ஆவணம்; பதிவு ஆவண எண் " + ("பதிவாகியுள்ளது." if doc else "இல்லை.")))
            if not (succ or court):
                L.append(_t(ta, "No succession, legal-heir or court-order details are recorded, which is consistent with a transfer by deed rather than by inheritance or court order.",
                            "வாரிசு / நீதிமன்ற விவரங்கள் இல்லை; இது ஆவணம் மூலமான மாற்றத்துக்கு பொருந்தும்."))
    if all_ or ws & {"rec", "transaction"}:
        rc = _v(r.get("sis_rec"))
        L.append(_t(ta, f"SIS recommendation: {rc or nr} (reason code {_v(r.get('sis_rec_reason')) or nr}); transaction status: {_v(r.get('transaction_status')) or nr}"
                        + (" -- the register does not define these codes, so no rule links them." if rc or _v(r.get("transaction_status")) else "."),
                    f"SIS பரிந்துரை: {rc or nr}; பரிவர்த்தனை நிலை: {_v(r.get('transaction_status')) or nr}."))
    if want("group"):
        grp = [_v(r.get(k)) for k in ("group_survey_no", "group_sub_div_no", "group_patta_no")]
        if not any(grp):
            L.append(_t(ta, "– the group survey / sub-division / patta numbers are blank (this file is not part of a grouped split), so there is nothing to match with the sub-division records",
                        "– குழு எண்கள் காலி; ஒப்பிட எதுவும் இல்லை"))
        elif subs:
            L.append(_ln(_t(ta, "Group survey vs sub-division record survey", "குழு சர்வே vs உட்பிரிவு பதிவு சர்வே"), grp[0], subs[0].get("survey_no"), _eq(grp[0], subs[0].get("survey_no")), nr))
        else:
            L.append(_t(ta, "– no temp sub-division record is held for this application (only ISD files have one)", "– உட்பிரிவு பதிவு இல்லை (ISD கோப்புகளுக்கு மட்டுமே)"))
    if want("correction"):
        oc = _v(r.get("owner_correction_reason"))
        L.append(_t(ta, f"Owner correction reason code: {oc or nr}" + (_undef(ta) if oc else "")
                    + (f"; new owner on record: {_v((n0 or {}).get('owner_name')) or nr} (patta {_v((n0 or {}).get('patta_no')) or nr})" if n0 else "; no new-owner record is held")
                    + (". A recorded code means an owner detail was corrected; what it was cannot be read from the code." if oc else "."),
                    f"உரிமையாளர் திருத்த காரண குறியீடு: {oc or nr}"))
    if want("direct"):
        L.append(_t(ta, f"Direct transfer flag: {_v(r.get('direct_transfer')) or nr}; transaction type: {_v(r.get('trans_type')) or nr}; reason: {_v(r.get('reason')) or nr}; transaction code: {_v(r.get('transaction_code')) or nr}"
                    + " (the register does not define the codes).", f"நேரடி மாற்றம்: {_v(r.get('direct_transfer')) or nr}; வகை: {_v(r.get('trans_type')) or nr}"))
    if p["fullfield"] and n0 and "difference" in ws or "mismatch" in ws:
        for k, lab in (("ward_code", "Ward"), ("block_code", "Block")):
            L.append(_ln(_t(ta, f"{lab} code (transfer vs new owner)", f"{lab} குறியீடு (மாற்றம் vs புதிய)"), r.get(k), (n0 or {}).get(k), _eq(r.get(k), (n0 or {}).get(k)), nr))
    L = [x for x in dict.fromkeys(x for x in L if x)]
    if not L:
        return ""
    L.append(_t(ta, "(✔ match, ✘ differs, – not comparable)", "(✔ பொருந்தும், ✘ வேறுபடும், – ஒப்பிட இயலாது)"))
    return f"{app}:\n" + "\n".join("• " + x for x in L)
