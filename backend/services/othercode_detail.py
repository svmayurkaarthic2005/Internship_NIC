"""The full detail card of an application under a TAMILNILAM service code other than
ISD / NISD (0156 ... 0178), which has no row in `applications`.

Assembled from the extracts that do carry it, all keyed by `appl_id`: the register entry,
the application log, the workflow log, `correction_modification_flow`,
`uareg_modification` / `uchitta_natham_modification` (the parcel and owners being modified)
and, for F-Line (0178), the application-information extract. A section with no row is
left out; a blank column is "not recorded". The caller has already checked jurisdiction
through the register. Attachment bodies (base64) are never read."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.utils.logger import get_logger

logger = get_logger(__name__)

_ROLE = {"1": "Applicant / CSC", "44": "SIS", "42": "SIS", "41": "SIS", "8": "SD (Senior Draughtsman)",
         "12": "DIS", "16": "ZDT / Tahsildar", "59": "ZDT / DRO", "53": "ZDT / DRO", "0": "Closed", "43": "Intake (role 43)"}
_DESKS = [("sis", "SIS"), ("zdt", "ZDT"), ("sd", "SD"), ("dis", "DIS"), ("thl", "Tahsildar"), ("rdo", "RDO"),
          ("collr_dro", "Collector/DRO"), ("dro", "DRO"), ("coss", "COSS"), ("cla", "CLA")]


async def _one(db, sql: str, app: str) -> Optional[Dict[str, Any]]:
    from sqlalchemy import text
    try:
        async with db.begin_nested():
            r = (await db.execute(text(sql), {"a": app})).mappings().first()
            return dict(r) if r else None
    except Exception as exc:
        logger.warning(f"othercode_detail: {type(exc).__name__}")
        return None


async def _all(db, sql: str, app: str) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    try:
        async with db.begin_nested():
            return [dict(r) for r in (await db.execute(text(sql), {"a": app})).mappings().all()]
    except Exception as exc:
        logger.warning(f"othercode_detail: {type(exc).__name__}")
        return []


def _v(x) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    return None if s in ("", "-", "None", "null") else s


def _extent(u1, u2, u3) -> Optional[str]:
    try:
        ha, ar, sq = float(u1 or 0), float(u2 or 0), float(u3 or 0)
    except (TypeError, ValueError):
        return None
    if not (ha or ar or sq):
        return None
    total = 10000 * ha + 100 * ar + sq
    return f"{ha:g} ha {ar:g} ares {sq:g} sq.m = {total:g} sq.m"


async def card(db, app: str, reg: Dict[str, Any], ta: bool) -> str:
    def t(en, tamil):
        return tamil if ta else en

    nr = t("not recorded", "பதிவு செய்யப்படவில்லை")
    L: List[str] = [
        f"{app}: " + t("Service ", "சேவை ") + f"{reg.get('service_code')} - {reg.get('type')}",
        t("Status: ", "நிலை: ") + f"{reg.get('status')} ({reg.get('stage')})",
        t("Submitted: ", "சமர்ப்பித்த தேதி: ") + f"{reg.get('submission_date')}",
        t("Survey / sub-division: ", "சர்வே / உட்பிரிவு: ") + f"{reg.get('survey_no')} / {reg.get('sub_division_no') or '0'}",
        t("Ward / block: ", "வார்டு / பிளாக்: ") + f"{reg.get('ward_number')} / {reg.get('block_number')}",
        t("Channel: ", "சமர்ப்பிப்பு வழி: ") + f"{reg.get('submission_channel') or '-'}",
    ]
    log = await _one(db, "SELECT patta_no, update_dt, can, user_id FROM appl_log_urban WHERE appl_id = :a LIMIT 1", app)
    if log:
        if _v(log.get("patta_no")):
            L.append(t("Patta no: ", "பட்டா எண்: ") + str(log["patta_no"]))
        if _v(log.get("can")):
            L.append("CAN: " + str(log["can"]))
        if _v(log.get("update_dt")):
            L.append(t("Last updated: ", "கடைசியாக புதுப்பித்தது: ") + str(log["update_dt"])[:16])

    info = await _one(db, "SELECT * FROM f_line_service_application_information_urban WHERE appl_id = :a LIMIT 1", app)
    if info:
        L.append("")
        L.append(t("Applicant (F-Line application):", "விண்ணப்பதாரர் (F-Line):"))
        for k, en, ta_ in (("appl_name", "Name", "பெயர்"), ("appl_address_cur", "Address", "முகவரி"),
                           ("mobile_no", "Mobile", "கைபேசி"), ("appl_status", "Application status", "விண்ணப்ப நிலை"),
                           ("challan_no", "Challan no", "சலான் எண்"), ("challan_date", "Challan date", "சலான் தேதி"),
                           ("paymentmode", "Payment mode", "செலுத்தும் முறை"), ("amount", "Amount", "தொகை"),
                           ("proposed_field_visit_date", "Proposed field visit", "முன்மொழிந்த கள ஆய்வு"),
                           ("proposed_remarks", "Proposed remarks", "முன்மொழிந்த குறிப்பு")):
            if _v(info.get(k)):
                L.append(f"  • {t(en, ta_)}: {info[k]}")

    flow = await _one(db, "SELECT * FROM correction_modification_flow WHERE appl_id = :a LIMIT 1", app)
    if flow:
        L.append("")
        L.append(t("Correction / modification workflow:", "திருத்தம் / மாற்றம் பணிப்பாய்வு:"))
        L.append("  • " + t("Reason code: ", "காரணக் குறியீடு: ") + (_v(flow.get("reason_code")) or nr))
        for p, lab in _DESKS:
            rec, rem, dt = _v(flow.get(f"{p}_rec")), _v(flow.get(f"{p}_remarks")), _v(flow.get(f"{p}_submit_date"))
            if rec or rem or dt:
                L.append(f"  • {lab}: " + {"Y": "Recommended", "N": "Not recommended"}.get(rec or "", rec or nr)
                         + (f" — {rem[:80]}" if rem else "") + (f" ({dt[:10]})" if dt else ""))
        flags = [f"{lab} {flow.get(k)}" for k, lab in (("govt_change_flag", "govt change"), ("priv_change_flag", "private change"))
                 if _v(flow.get(k))]
        if flags:
            L.append("  • " + ", ".join(flags))
        if _v(flow.get("certificate_sis")) or _v(flow.get("certificate_dis")):
            L.append("  • " + t("Certificates: ", "சான்றிதழ்கள்: ") +
                     f"SIS {flow.get('certificate_sis') or '-'}, DIS {flow.get('certificate_dis') or '-'}")

    wf = await _all(db, "SELECT slno, action_from, action_to, action_date, recommend, remarks FROM application_workflow "
                        "WHERE appl_id = :a ORDER BY slno", app)
    if wf:
        L.append("")
        L.append(t("Workflow steps:", "பணிப்பாய்வு படிகள்:"))
        for w in wf:
            a, b = _ROLE.get(str(w["action_from"]), t(f"role {w['action_from']} (a desk the register does not name)", f"role {w['action_from']} (பதிவேடு பெயரிடாத மேசை)")), \
                   _ROLE.get(str(w["action_to"]), t(f"role {w['action_to']} (a desk the register does not name)", f"role {w['action_to']} (பதிவேடு பெயரிடாத மேசை)"))
            rem = _v(w.get("remarks"))
            L.append(f"  {w['slno']}. {a} → {b}  {w['action_date'] or ''}"
                     + (f"  [{w['recommend']}]" if _v(w.get("recommend")) else "") + (f"  {rem[:70]}" if rem else ""))

    par = await _all(db, "SELECT * FROM uareg_modification WHERE appl_id = :a ORDER BY slno", app)
    if par:
        L.append("")
        L.append(t("Parcel being modified:", "மாற்றப்படும் நிலப்பகுதி:"))
    for r in par[:4]:
        L.append("  • " + t("Survey / sub-division: ", "சர்வே / உட்பிரிவு: ") + f"{r.get('survey_no')} / {r.get('subdiv_no')}"
                 + (f"  (old survey {r['osurvey_no']})" if _v(r.get("osurvey_no")) else ""))
        if _v(r.get("patta_no")):
            L.append("  • " + t("Patta no: ", "பட்டா எண்: ") + str(r["patta_no"]))
        ex = _extent(r.get("ext_u1"), r.get("ext_u2"), r.get("ext_u3"))
        if ex:
            L.append("  • " + t("Extent: ", "பரப்பளவு: ") + ex)
        for k, en in (("land_type", "Land type code"), ("land_use", "Land use code"), ("soil_type_p", "Primary soil code"),
                      ("tax_hect", "Tax per hectare"), ("operation", "Modify operation")):
            if _v(r.get(k)):
                L.append(f"  • {en}: {r[k]}")
        rm = _v(r.get("remarks1")) or _v(r.get("remarks"))
        if rm:
            L.append("  • " + t("Remarks: ", "குறிப்புகள்: ") + rm[:160])
    own = await _all(db, "SELECT owner, relative, reltn, sex FROM uchitta_natham_modification WHERE appl_id = :a ORDER BY slno", app)
    if own:
        L.append("")
        L.append(t("Owner(s) in the modification:", "மாற்றத்தில் உள்ள உரிமையாளர்(கள்):"))
        for o in own[:6]:
            L.append(f"  • {o.get('owner') or nr}" + (f" ({o['relative']})" if _v(o.get("relative")) else ""))
    tslr = await _all(db, "SELECT survey_no, sub_div_no, old_patta_no, type_ecate, ext_u1, ext_u2, ext_u3, challan_no, challan_date, paymentmode, amount "
                          "FROM tslr_sketch_urban WHERE appl_id = :a ORDER BY slno", app)
    if tslr:
        L.append("")
        L.append(t("TSLR sketch record(s):", "TSLR வரைபட பதிவு(கள்):"))
        for r in tslr[:4]:
            ex = _extent(r.get("ext_u1"), r.get("ext_u2"), r.get("ext_u3"))
            L.append(f"  \u2022 {t('survey / sub-division', 'சர்வே / உட்பிரிவு')} {r.get('survey_no')} / {r.get('sub_div_no')}"
                     + (f", {t('old patta', 'பழைய பட்டா')} {r['old_patta_no']}" if _v(r.get("old_patta_no")) else "")
                     + (f", {ex}" if ex else "")
                     + (f", {t('challan', 'சலான்')} {r['challan_no']} ({r.get('challan_date')}, {r.get('paymentmode')}, {r.get('amount')})" if _v(r.get("challan_no")) else ""))
    att = await _all(db, "SELECT attachmentname FROM file_attachment WHERE appl_id = :a", app)
    if att:
        L.append("")
        L.append(t("Attachments: ", "இணைப்புகள்: ") + ", ".join(sorted({a["attachmentname"] for a in att if a.get("attachmentname")})))
    return "\n".join(L)


# the names the ISD / NISD workflow history uses for the same roles
_HIST = {"1": "Submission", "44": "Sub Inspector Surveyor", "42": "Sub Inspector Surveyor", "41": "Sub Inspector Surveyor",
         "43": "Intake", "8": "Senior Draughtsman", "12": "Deputy Inspector Surveyor", "16": "Zonal Level Tahsildar",
         "59": "ZDT / DRO", "53": "ZDT / DRO", "0": "Completed"}


def _hist(role: str, ta: bool) -> str:
    """_HIST.get(role), or the same "(a code the register does not define)" flag every
    other undefined code in this module gets, instead of a bare, unexplained "role 25"
    that reads like a name the register actually has."""
    name = _HIST.get(role)
    if name:
        return name
    return (f"role {role} (a desk the register does not name)" if not ta
            else f"role {role} (பதிவேடு பெயரிடாத மேசை)")


async def stage_reached(app: str, reg: Dict[str, Any], ta: bool) -> str:
    """"has it reached the completed stage?" / "is it at the final stage?" -- a
    yes/no against the register's own coarse `stage` field (Open / Completed),
    not the detailed workflow desk `current_stage()` reports. A file whose
    stage is Completed may still be either approved or rejected, so the status
    is named alongside the verdict rather than left implied by it."""
    stage = str((reg or {}).get("stage") or "")
    status = str((reg or {}).get("status") or "")
    reached = stage.strip().lower() == "completed"
    if ta:
        verdict = "ஆம்" if reached else "இல்லை"
        return f"{verdict} — {app} தற்போது '{stage}' நிலையில் உள்ளது (நிலை: {status})."
    verdict = "Yes" if reached else "No"
    return f"{verdict} -- {app} is currently at the '{stage}' stage (status: {status})."


async def current_stage(db, app: str, reg: Dict[str, Any], ta: bool) -> Optional[str]:
    """"current stage?" / "where is it" / "which desk": the file's present position only, not the
    step-by-step log `workflow()` gives. A bare stage question used to get the full "Workflow history
    for X (N step(s)): ..." card even when nothing was asked about the history -- answered, but not the
    question that was asked."""
    wf = await _all(db, "SELECT action_from, action_to FROM application_workflow WHERE appl_id = :a "
                         "ORDER BY slno DESC LIMIT 1", app)
    if not wf:
        return None
    to_ = str(wf[0]["action_to"])
    status = str((reg or {}).get("status") or "")
    if to_ == "0":
        return (f"{app} முடிந்தது — {status}." if ta else f"{app} is closed -- {status}.")
    desk = _hist(to_, ta)
    stage = (reg or {}).get("stage")
    return (f"தற்போதைய நிலை: {desk} (நிலை {status})." if ta
            else f"Current stage: {desk} (status {status}" + (f", {stage}" if stage else "") + ").")


async def workflow(db, app: str, reg: Dict[str, Any], ta: bool) -> Optional[str]:
    """The movement of a file under another service code, in the same shape as an ISD / NISD file's workflow history:
    "Workflow history for X (N step(s)):" then one dated line per step, then where the file is now."""
    wf = await _all(db, "SELECT slno, action_from, action_to, action_date, recommend, remarks FROM application_workflow "
                        "WHERE appl_id = :a ORDER BY slno", app)
    if not wf:
        return None
    L = [f"Workflow history for {app} ({len(wf)} step(s)):" if not ta else f"{app}-ன் பணிப்பாய்வு வரலாறு ({len(wf)} படி):"]
    for w in wf:
        a = _hist(str(w["action_from"]), ta)
        b = _hist(str(w["action_to"]), ta)
        rem = _v(w.get("remarks"))
        rec = _v(w.get("recommend"))
        L.append(f"  {w['slno']}. {w['action_date'] or ''} — {a} → {b}"
                 + ((" [recommended]" if not ta else " [பரிந்துரை]") if rec == "Y" else (" [not recommended]" if rec == "N" else ""))
                 + (f' · "{rem[:80]}"' if rem else ""))
    last = str(wf[-1]["action_to"])
    status = str((reg or {}).get("status") or "")
    if last == "0":
        L.append(("Closed — " if not ta else "முடிந்தது — ") + status)
    else:
        L.append((f"Currently at: {_hist(last, ta)} (status {status}, stage {(reg or {}).get('stage')})." if not ta
                  else f"தற்போது: {_hist(last, ta)} (நிலை {status})."))
    return "\n".join(L)


async def attachments(db, app: str, ta: bool) -> str:
    """The documents attached to a file, from the source `file_attachment` extract (any service code).
    `application_documents` is synthesised at build time (pending => nothing uploaded), so it told an
    officer that a pending file with six attachments was missing its sale deed. Bodies are never read."""
    rows = await _all(db, "SELECT attachmentname, verifieddoc FROM file_attachment WHERE appl_id = :a ORDER BY row_id", app)
    names: Dict[str, bool] = {}
    for r in rows:
        n = (r.get("attachmentname") or "").strip()
        if n:
            names[n] = names.get(n, False) or str(r.get("verifieddoc") or "").lower() == "true"
    if not names:
        return (f"{app}-க்கு இணைக்கப்பட்ட ஆவணம் எதுவும் பதிவில் இல்லை." if ta
                else f"No document attachment is recorded for {app}.")
    nver = sum(names.values())
    head = (f"{app}-க்கு இணைக்கப்பட்ட ஆவணங்கள் ({len(names)}):" if ta
            else f"Documents attached to {app} ({len(names)}):")
    lines = [f"  • {n}" + ((" (சரிபார்க்கப்பட்டது)" if ta else " (verified)") if v else "") for n, v in names.items()]
    tail = ((f"{nver} சரிபார்க்கப்பட்டதாகக் குறிக்கப்பட்டுள்ளன; மற்றவற்றுக்கு சரிபார்ப்பு குறி இல்லை." if nver
             else "எந்த ஆவணமும் சரிபார்க்கப்பட்டதாகக் குறிக்கப்படவில்லை.") if ta else
            (f"{nver} marked verified; the rest carry no verification mark." if nver
             else "None is marked verified in the register."))
    return "\n".join([head, *lines, tail])


async def decider(db, app: str, reg: Dict[str, Any], ta: bool) -> Optional[str]:
    """"who approved / rejected it": the register holds desks, not officer names -- say which desk closed the file and when."""
    wf = await _all(db, "SELECT slno, action_from, action_to, action_date, remarks FROM application_workflow WHERE appl_id = :a ORDER BY slno", app)
    status = str((reg or {}).get("status") or "")
    if not wf:
        return None
    last = wf[-1]
    desk = _hist(str(last["action_from"]), ta) if str(last["action_to"]) == "0" else None
    if status in ("approved", "rejected") and desk:
        rem = _v(last.get("remarks"))
        return (f"{app} was {status} on {last['action_date']} at the {desk} desk" + (f' (remark: "{rem[:80]}")' if rem else "") +
                ". The register holds desks, not officer names, so I cannot say which person did it.") if not ta else (
            f"{app} {last['action_date']} அன்று {desk} மேசையில் {status}. பதிவேட்டில் அலுவலர் பெயர்கள் இல்லை.")
    where = _hist(str(last["action_to"]), ta)
    return f"{app} is not decided yet ({status}); it is at the {where} desk. Officer names are not held in the register."