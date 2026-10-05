"""cmflow_qa (desk recommendations / flags) and appinfo_qa (challan, amount, proposed visit ...)
question parsing -- typos, Tamil, Tanglish. No DB, no LLM.  python test_record_qa.py"""
from backend.services import appinfo_qa as ai, cmflow_qa as cm, fline_qa as fl, merge_qa as mg, crosscheck_qa as xc, igrs_owner_qa as ig, newowner_qa as nw, fulltransfer_qa as ft, sdattach_qa as sa, tslr_qa as tq, uamod_qa as um, natham_qa as na

CM = [
    ("What is the reason code for applcation 2026/0158/02/001804?", [], None, ["reason"]),
    ("What is the ZDT recomendation and remarks?", ["zdt"], None, []),
    ("What is the collr DRO reccomendation for this applcation?", ["collr_dro"], None, []),
    ("What is the ZDT stauts and submision date?", ["zdt"], None, []),
    ("Compare the SIS, ZDT, SD, DIS and RDO reccomendations", ["sis", "zdt", "sd", "dis", "rdo"], "compare", []),
    ("Which officers have given a recomendation and what did they recomend?", [], "given", []),
    ("Show the complete workflow from SIS to RDO", ["sis", "rdo"], "workflow", []),
    ("Which stage was submited first and which stage was submited last?", [], "order", []),
    ("Are there any stages where the recomendation is missing?", [], "missing", []),
    ("What are the govt and private change flags?", [], None, ["govt", "priv"]),
    ("Were any certificates issued by SIS or DIS?", ["sis", "dis"], None, ["certificate"]),
    ("அரசு மாற்றக் கொடி என்ன?", [], None, ["govt"]),
    ("rdo parindhurai enna", ["rdo"], None, []),
    ("when was the application submitted", [], None, []),
]
AI = [
    ("What is the challan no?", [ai.CHALLAN_NO]), ("What is the challan date and paymant mode?", [ai.CHALLAN_DATE, ai.PAYMODE]),
    ("How much is the applcation amount?", [ai.AMOUNT]), ("What is the applcation stauts code?", [ai.STATUS_CODE]),
    ("Is there any reject reason code for this applcation?", [ai.REJECT]),
    ("What is the proposed field visit date?", [ai.PFV_DATE]), ("What are the proposed remarks for the field visit?", [ai.PFV_REMARKS]),
    ("Are there any missing documants?", [ai.MISSING]), ("What is the SRO for this application?", [ai.SRO]),
    ("What is the CSC name for this applcation?", [ai.CSC]), ("What is the permenant adress of the applcant?", [ai.PERM]),
    ("What is the applcant mother name?", [ai.MOTHER]), ("What is the applcant gender and DOB?", [ai.GENDER, ai.DOB]),
    ("தாயார் பெயர் என்ன?", [ai.MOTHER]), ("challan number enna", [ai.CHALLAN_NO]),
    ("Is the barcod flag enabled?", [ai.BARCODE]), ("Is the applcation encertifyed?", [ai.ENCERT]),
    ("Is physical verification completd?", [ai.VERIFYPHYS]), ("When were the documants sent and when were they receved?", [ai.SENT, ai.RECEIVED]),
    ("What is the RTR str detial?", [ai.RTR]), ("Is there any owner corection reason?", [ai.OWNERCORR]),
    ("Is this applcation automautated?", [ai.AUTOMUT]), ("Is there a merge applid linked?", [ai.MERGE]),
    ("What is the latest upadte date and remarks?", [ai.UPDATE]), ("What documants are enclosd with this applcation?", [ai.ENCL]),
    ("what is the merge service code", []),
    ("what is the applicant name", []), ("show my pending applications", []),
]
FL = [
    ("What is the transaction stauts of the F-line applcation?", [fl.TXN]),
    ("How many total boundries are recorded?", [fl.BOUND]),
    ("Is a sktech file available for this applcation?", [fl.SKETCH]),
    ("Is the sktech_file_dis available?", [fl.SKETCH_DIS]),
    ("What is the SIS reccomendation?", [("sis", "rec")]),
    ("What is the DIS reason for the decision?", [("dis", "reason")]),
    ("Which SIS user verified the applcation?", [("sis", "verify")] and [("sis", "date"), ("sis", "user")]),
    ("When is this applcation schedulded?", [fl.SCHED]),
    ("What type of applcant submitted this F-line applcation?", [fl.APPTYPE]),
    ("எல்லைகள் எத்தனை?", [fl.BOUND]),
]
MG = [
    ("which applications are merged with 2026/0154/02/011886?", ("reverse", "2026/0154/02/011886")),
    ("which applications have a merge id?", ("overview", None)),
    ("how many merged applications do I have?", ("overview", None)),
    ("with which application is it merged?", None), ("is it merged with any application?", None),
    ("what is the merge id of 2026/0154/02/013642", None), ("what is the merge service code", None),
    ("can I merge two applications", None), ("show my pending applications", None),
]
XC = [
    ("Compare the applcant name and father name with the chitta old owner record.", "applicant_owner"),
    ("Does the patta transfer applcant match the old owner in the chitta record?", "applicant_owner"),
    ("Compare the applcation stauts with the correction/modifcation flow stauts.", "status_flow"),
    ("Does the applcant adress match the adress available in the other applcation records?", "address_other"),
    ("Compare the challan date and payment details with the application submission timeline.", "challan_timeline"),
    ("Was physical verification completed before the field visit date?", "phys_before_fv"),
    ("Are there any missing documants even though the applcation has been submitted?", "missing_vs_submitted"),
    ("Does the owner corection reason indicate that an owner detail was changed?", "ownercorr_meaning"),
    ("Is the applcation automautated or manually processed?", "automut_manual"),
    ("Is there a merge applid associated with this applcation, and what does it indicate?", "merge_meaning"),
    ("Find any mismatch between the patta transfer applcation and chitta owner information.", "all_diffs"),
    ("Check whether the applcation stauts, physical verification status and proposed field visit information are consistant.", "consistent_fv"),
    ("Compare the payment information with the applcation records and identify any missing fields.", "payment_missing"),
    ("Give me all important differences between this patta transfer record and the old owner record.", "all_diffs"),
    ("பழைய உரிமையாளர் பதிவுடன் விண்ணப்பதாரர் பெயரை ஒப்பிடு", "applicant_owner"),
    ("what is the applicant name", None), ("show my pending applications", None),
]
IG = [
    ("What is the owner nam in IGRS?", "field", [ig.OWNER]), ("What is the owners english name?", "field", [ig.ENGLISH]),
    ("What is the patta numbr for this IGRS owner record?", "field", [ig.PATTA]), ("What is the door no of the owner?", "field", [ig.DOOR]),
    ("What is the IGRS form 6 no?", "field", [ig.IGRSNO]), ("What is the EPIC no for this owner?", "field", [ig.EPIC]),
    ("What is the owners aadhar no?", "field", [ig.AADHAAR]), ("Is there an owner image avalable for this record?", "field", [ig.IMAGE]),
    ("Does the IGRS patta no match the patta no in the chitta record?", "compare", [ig.PATTA]),
    ("Is the IGRS owner the same person as the chitta old owner?", "compare", []),
    ("show my pending applications", None, []),
]
NW = [
    ("Who is the new ower?", "field", [nw.OWNER]), ("What is the relitive name of the new owner?", "field", [nw.RELATIVE]),
    ("What is the new owners extant?", "field", [nw.EXTENT]), ("What is the UDS detials for the new owner?", "field", [nw.UDS]),
    ("What is the status of the new owner record?", "field", [nw.STATUS]), ("Is the new owner male or female?", "field", [nw.SEX]),
    ("What is the survay no for the new owner?", "field", [nw.SURVEY]), ("what is UDS?", "uds_def", []),
    ("UDS என்றால் என்ன?", "uds_def", []), ("uds enna", "uds_def", []),
    ("Compare the new owner share with the old owner share.", "compare", [nw.SHARE]),
    ("Show only the fields that changed from the old owner to the new owner.", "compare", []),
    ("Is this new owner the same person as the old owner, or are they diffrent?", "compare", []),
    ("Who is the old ower?", "field", [nw.OWNER]), ("Who is the old owners relitive?", "field", [nw.RELATIVE]),
    ("What is the old owners UDS?", "field", [nw.UDS]), ("What is the old owners aadhar no?", "field", [nw.AADHAAR]),
    ("What is the applcation no linked to this old owner?", "field", [nw.APPLINK]),
    ("Compare the old owner and new owner for applcation X.", "compare", []),
    ("Which fields stayed the same between the old owner and new owner?", "compare", []),
    ("Is the patta transfer actually changing the owner, or is it the same person?", "compare", []),
    ("Are there any inconsistant details between old owner and new owner records?", "compare", []),
    ("பழைய உரிமையாளர் யார்?", "field", [nw.OWNER]),
    ("show my pending applications", None, []),
]
RET = [
    ("What is the return ower nam?", "return_field"), ("Why was the ower record retunred?", "return_field"),
    ("What is the update date of the return owner record?", "return_field"), ("Is the retunred owner male or female?", "return_field"),
    ("Compare the return owner with the old owner.", "return_compare"), ("Is the retunred owner the same person as the old owner?", "return_compare"),
    ("Has any owner information changed by the time the record was returned?", "return_compare"),
    ("திருப்பப்பட்ட உரிமையாளர் யார்?", "return_field"), ("returned owner enna", "return_field"),
    ("show my returned applications", None),
]
FT = [
    ("What is the regstration document no?", "field", [ft.REGDOC]), ("What is the reson for the patta transfer?", "field", [ft.REASON]),
    ("Where was the registation done?", "field", [ft.REGPLACE]), ("What is the registation year?", "field", [ft.REGYEAR]),
    ("What is the ZDT patta no?", "field", [ft.ZDTPATTA]), ("What is the tranaction stauts?", "field", [ft.TXNSTATUS]),
    ("What is the ZDT order no and order date?", "field", [ft.ZDTORDER]), ("Who issued the sucession certificate?", "field", [ft.SUCCISS]),
    ("What is the purchased area and its unit?", "field", [ft.PURCHASED]), ("Is this a direct tranfer?", "field", [ft.DIRECT]),
    ("What is the land category code?", "field", [ft.LANDCAT]), ("Compare the old total tax and new total tax.", "compare", None),
    ("Does the ZDT patta no match the new owner patta no?", "compare", None),
    ("Does the registration document number agree with the transfer reason?", "compare", None),
    ("Identify any diffrences between the full-field transfer record and the new-owner record.", "compare", None),
    ("show my pending applications", None, None),
]
SA = [
    ("Are there any SD attachements for applcation 2026/0154/02/015395?", "exists"), ("How many attachements are there for this applcation?", "count"),
    ("What files are attatched to applcation 2026/0154/02/015395?", "list"), ("What is the file name for the SD attachement?", "filename"),
    ("What is the survay sub divison no linked to the SD attachement?", "survey"), ("What is the sl no of the SD attachement?", "slno"),
    ("Are there multiple attachements for this applcation?", "multiple"), ("Are there any duplicate attachements for this applcation?", "duplicate"),
    ("Give me the complete SD attachement detials for this applcation.", "full"),
    ("Does every SD attachement have a file and survey sub divison number?", "file_present"),
    ("Does the survey sub divison no in the SD attachement match the F-line record?", "compare"),
    ("Are all attachements associated with applcation 2026/0154/02/015395?", "list"),
    ("SD இணைப்புகள் எத்தனை?", "count"),
    ("what is in the document I uploaded", None), ("show my pending applications", None), ("what is an attachment", None),
]
TQ = [
    ("What is the survay no in the TSLR sktech?", "field", [tq.SURVEY]), ("What is the old patta numbr in the TSLR sktech?", "field", [tq.OLDPATTA]),
    ("What is the land typ code in the TSLR?", "field", [tq.LANDTYPE]), ("What is the type ecate for this TSLR record?", "field", [tq.ECATE]),
    ("What is the extant u1 u2 and u3 in the TSLR sktech?", "field", [tq.EXTENT]), ("When was the TSLR sktech submited?", "field", [tq.SUBMIT]),
    ("What is the challan numbr for the TSLR sktech?", "field", [tq.CHALLANNO]), ("What is the ammount paid for the TSLR sktech?", "field", [tq.AMOUNT]),
    ("Are there any TSLR sktech records for applcation X?", "exists", []), ("How many TSLR sktech records are linked to this applcation?", "count", []),
    ("Does the TSLR survay no match the F-line survay no?", "compare", None), ("Is the TSLR record consistant with the land information used by SIS?", "compare", None),
    ("show my pending applications", None, None), ("what is a sketch", None, None),
]
UM = [
    ("What operashun was performed for this modifcation?", "field", ["operation"]), ("What was the old survay no and old sub divison no in the modifcation?", "field", ["old_survey"]),
    ("What is the irrigation source for this modification?", "field", ["irr"]), ("What is the soil class in the modifcation?", "field", ["soil_class"]),
    ("What are the remaks for this modification?", "field", ["remarks"]), ("What is the existing patta no in the modifcation?", "field", ["existing_patta"]),
    ("What is the form 6, form 7 and form 8 numbrs of the modification?", "field", ["forms"]), ("What is the municipal tax?", "field", ["municipal"]),
    ("What is the COSS block code?", "field", ["coss"]), ("Has the door no changed during the modification?", "compare", ["door"]),
    ("Compare ext u1 u2 u3 before and after the modification.", "compare", ["extent"]), ("Which fields remained unchanged in the modification?", "compare", []),
    ("Compare the modification status with the correction workflow status.", "status_flow", []),
    ("show my pending applications", None, None), ("modify my profile", None, None),
]
NA = [
    ("Who is the Natham ower?", "field", ["owner"]), ("What is the Natham owners relitive name?", "field", ["relative"]),
    ("What is the Natham ration card no?", "field", ["ration"]), ("What is the Natham S patta no?", "field", ["spatta"]),
    ("What operashun was performed in the Natham modification?", "field", ["operation"]),
    ("Give me the complete Natham Chitta modifcation detials", "full", []),
    ("Does the Natham owner name match the IGRS owner name?", "compare", None),
    ("Does the S patta no correspond to the patta information in the other records in Natham?", "compare", None),
    ("நத்தம் உரிமையாளர் யார்?", "field", ["owner"]),
    ("who is the owner", None, None), ("show my pending applications", None, None),
]
bad = []
for m, mode, keys in NA:
    pp = na.parse(m)
    got = pp["mode"] if pp else None
    if got != mode or (keys is not None and pp and pp["keys"] != keys):
        bad.append((m, got, pp and pp["keys"]))
for m, mode, keys in UM:
    pp = um.parse(m)
    got = pp["mode"] if pp else None
    if got != mode or (keys is not None and pp and pp["keys"] != keys):
        bad.append((m, got, pp and pp["keys"]))
for m, mode, keys in TQ:
    pp = tq.parse(m)
    got = pp["mode"] if pp else None
    if got != mode or (keys is not None and pp and pp["keys"] != keys):
        bad.append((m, got, pp and pp["keys"]))
for m, want in SA:
    pp = sa.parse(m)
    got = pp["mode"] if pp else None
    if got != want:
        bad.append((m, got, want))
for m, kind, keys in FT:
    pp = ft.parse(m)
    if pp["kind"] != kind or (keys is not None and pp["keys"] != keys):
        bad.append((m, pp["kind"], pp["keys"]))
for m, kind in RET:
    if nw.parse(m)["kind"] != kind:
        bad.append((m, nw.parse(m)["kind"], kind))
# populated-record rendering (the live table is empty, so exercise the code with rows)
_rr = [{"owner_name": "பாலாஜி", "relative_name": "சுப்பையா", "patta_no": "1841", "share": "1/2", "extent": "5", "uds": None, "status": "R",
        "status_owner": "N", "update_dt": "2026-09-10 10:00:00", "sex": "M", "has_aadhaar": False, "has_photo": False,
        "ward_code": "001", "block_code": "0012"}]
_txt = nw.answer_return_fields([nw.OWNER, nw.PATTA, nw.UPDATED], "X", _rr, 1, None, {"status": "pending", "stage": "SIS"}, False)
if "பாலாஜி" not in _txt or "1841" not in _txt or "2026-09-10" not in _txt:
    bad.append(("return fields render", _txt, None))
_cmp = nw.answer_return_compare({"ws": {"compare", "old", "return", "owner"}}, "X", _rr, 1, [], [{"owner_name": "பாலாஜி", "patta_no": "1841", "share": "1/2", "relative_name": "சுப்பையா"}], [], {}, False)
if "✔ Name (returned vs old owner)" not in _cmp or "✔ Patta no" not in _cmp:
    bad.append(("return compare render", _cmp, None))
for m, kind, keys in NW:
    pp = nw.parse(m)
    if pp["kind"] != kind or (kind in ("field",) and pp["keys"] != keys):
        bad.append((m, pp["kind"], pp["keys"]))
for m, kind, keys in IG:
    pp = ig.parse(m)
    if pp["kind"] != kind or (kind and pp["keys"] != keys):
        bad.append((m, pp["kind"], pp["keys"]))
for m, want in XC:
    if xc.parse(m) != want:
        bad.append((m, xc.parse(m), want))
for m, want in MG:
    if mg.parse(m) != want:
        bad.append((m, mg.parse(m), want))
for m, want in FL:
    if fl.parse(m)["keys"] != want:
        bad.append((m, fl.parse(m)["keys"], want))
for m, desks, mode, extra in CM:
    q = cm.parse(m)
    if (q.desks, q.mode, q.extra) != (desks, mode, extra) and not (m.startswith("when was") and not q):
        bad.append((m, q.desks, q.mode, q.extra))
for m, want in AI:
    if ai.parse(m) != want:
        bad.append((m, ai.parse(m), want))
for b in bad:
    print("FAIL", b)
print(f"{len(CM) + len(AI) + len(FL) + len(MG) + len(XC) + len(IG) + len(NW) + len(RET) + len(FT) + len(SA) + len(TQ) + len(UM) + len(NA) + 2 - len(bad)}/{len(CM) + len(AI) + len(FL) + len(MG) + len(XC) + len(IG) + len(NW) + len(RET) + len(FT) + len(SA) + len(TQ) + len(UM) + len(NA) + 2} ok")
raise SystemExit(1 if bad else 0)
