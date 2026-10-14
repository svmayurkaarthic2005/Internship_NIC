"""Policy / how-to questions with no application in them ("what documents are needed for a patta transfer", "explain the
15 working day rule", "is it allowed to approve without a field visit", "how is a sub-division sketch prepared", "steps
from application to patta order"). Each answer restates what backend/documents (workflow_guide.txt, land_rules.txt)
say, no more: where the documents are silent the answer says so. English, Tamil and Tanglish. No LLM, no database --
`answer()` is a lookup; the few live questions (F-Line fee amounts, duplicate surveys) are separate async helpers."""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

_APP = re.compile(r"\d{4}/\d{3,4}/\d{1,3}/\d+")
F = re.IGNORECASE

_ISD_DOCS = ("1. Sale Deed (registered with the Sub-Registrar; original scan; buyer, seller, survey number and area)\n"
             "2. Encumbrance Certificate (clear title for the past 13 years; valid 3 months from issue)\n"
             "3. Survey Sketch (existing sketch from revenue records, from the Tahsildar office)\n"
             "4. Photo ID (Aadhaar mandatory plus one more ID: driving licence, voter ID or passport; buyer and seller)\n"
             "5. Photographs (current property photographs, boundary stones if applicable; at least 4 angles)")
_NISD_DOCS = ("1. Sale Deed (registered with the Sub-Registrar; original scan; must match the survey number)\n"
              "2. Encumbrance Certificate (clear title for the past 13 years; valid 3 months)\n"
              "3. Photo ID (Aadhaar of buyer and seller plus one more government ID)\n"
              "4. Patta Copy (current patta showing the previous owner)")

EN = {
    "field_visit": (
        "A field visit (field / site inspection) is the on-site verification an SIS officer does at the actual parcel before an "
        "application can move forward. At the visit you: locate the survey number and sub-division on the ground; measure the actual "
        "boundaries and area and compare them with the patta and sale deed; mark the corners with GPS and check boundary stones; check "
        "for encroachment or occupation that does not match the record; obtain the signatures of the applicant and all joint owners; "
        "take photographs and prepare field notes and a rough sketch. Rules: mandatory for every ISD (0154); not needed for NISD (0153) "
        "unless an issue is flagged; schedule it within 15 working days of submission; only the Tahsildar can approve a change of the "
        "visit date. Your notes and photographs are the basis of the Senior Draughtsman's sketch and the DIS review."),
    "rule15": (
        "The 15 working day rule: for an ISD application the field visit must be scheduled within 15 working days of submission "
        "(Monday to Friday; the register has no holiday calendar). If the visit is not completed by then the file is marked overdue: it "
        "gets a red flag on your dashboard, you receive escalation notices, it counts in your performance metrics and you must justify "
        "the delay. It applies to ISD only; NISD has no field visit. It is a different clock from the service SLA (NISD 15-20, ISD "
        "30-35 working days from submission to completion)."),
    "pending_vs_overdue": (
        "No, they are not the same thing. 'Pending' is a STATUS -- one of pending / in_progress / escalated / approved / rejected -- "
        "it just means the file is still open. 'Overdue' is a separate flag that only applies to an ISD file whose field visit has not "
        "been completed within 15 working days of submission; NISD has no field visit, so an NISD file can never be overdue. A pending "
        "file is usually NOT overdue -- it only becomes overdue once that 15-working-day clock runs out. And a visit being past its own "
        "SCHEDULED date is a different, earlier state again, not the same as the file itself being overdue. Ask \"show my pending "
        "applications\" for the status, or \"show my overdue applications\" for the deadline flag -- they can give very different counts."),
    "noshow": (
        "The reference documents do not describe a separate procedure for an applicant who does not turn up. What they do say: the "
        "visit must still be completed within 15 working days of submission or the file goes overdue and you must justify the delay; "
        "any change of the visit date needs the Tahsildar's approval (only the Tahsildar may approve it); and the applicant's and joint "
        "owners' signatures are collected at the visit."),
    "approve_without_visit": (
        "ISD (0154): no -- the field visit is mandatory for every ISD application. NISD (0153): it is a document-only verification, so "
        "no visit is required unless a specific issue is flagged. Note also that the SIS officer does not approve: an ISD file goes "
        "SIS -> Senior Draughtsman (sketch) -> DIS review -> Tahsildar's digital signature; an NISD file goes SIS -> Zonal Deputy "
        "Tahsildar, who holds the DSC key and approves. This assistant only reads the register; approvals are done in the TAMILNILAM portal."),
    "docs": ("Documents for a patta transfer.\nNISD (0153, no new sub-division):\n" + _NISD_DOCS +
             "\nISD (0154, involving sub-division):\n" + _ISD_DOCS),
    "sketch": (
        "How the sub-division sketch is prepared (ISD): after your field visit you submit your findings, with your field notes and rough "
        "sketch, to the Senior Draughtsman (SD). The SD prepares the detailed survey sketch with the new sub-division numbers and checks "
        "the area: the sum of all sub-divisions must equal the original survey area (numbering such as 145 -> 145/1A, 145/1B, 145/2A). "
        "The SD handoff typically takes 7-10 working days. The DIS then reviews the field report and sketch and assigns temporary "
        "numbers {existing sub-division}/T{sequence} (e.g. 3/T1, 3/T2); on approval each gets its final number (3/T1 -> 3, 3/T2 -> 4)."),
    "steps_nisd": (
        "NISD (0153) from application to patta order: 1) application through CSC, Sub-Registrar or the citizen portal; 2) SIS verifies "
        "Sale Deed, EC and Photo ID, checks the survey number, area and any dispute or litigation, and forwards to the Zonal Deputy "
        "Tahsildar (about 5 working days; no field visit unless flagged); 3) the Zonal Deputy Tahsildar checks revenue arrears and the "
        "sale deed registration, and with the DSC key approves and generates the patta transfer order, or rejects with reasons (about 3 "
        "working days); 4) ownership is transferred and land records and property tax records are updated; 5) the citizen downloads the "
        "patta from the portal. Total about 15-20 working days."),
    "steps_isd": (
        "ISD (0154) from application to patta order: 1) application through CSC, Sub-Registrar or the citizen portal with all documents; "
        "2) assigned to the SIS officer of the ward; 3) SIS field visit within 15 working days (mandatory); 4) Senior Draughtsman "
        "prepares the sub-division sketch (7-10 working days); 5) DIS reviews, assigns temporary numbers and approves or rejects (about 5 "
        "working days), final numbers on approval; 6) Tahsildar applies the digital signature (about 3 working days); 7) the patta transfer "
        "order is generated and land records are updated. Total about 30-35 working days."),
    "speed_up": (
        "From the workflow rules, what moves a file along: schedule the ISD field visit early (it must be within 15 working days or "
        "the file goes overdue); verify the documents at once -- missing Sale Deed, EC or ID is the first rejection reason; check for "
        "encroachment, litigation, revenue arrears and an area mismatch before forwarding, since these are the common rejection reasons; "
        "hand the field findings to the Senior Draughtsman promptly (that handoff takes 7-10 working days). To see which of your files "
        "to take first, ask: which application should I field visit next?"),
    "note": (
        "Draft note (fill in the details; I cannot send messages):\n\n"
        "Dear [Applicant name],\n"
        "Your application [application number] for [patta transfer / sub-division] cannot be processed yet because the documents "
        "submitted are incomplete. Please submit the following, as applicable:\n"
        "NISD: Sale Deed, Encumbrance Certificate, Photo ID (Aadhaar plus one more ID) of buyer and seller, current Patta copy.\n"
        "ISD: Sale Deed, Encumbrance Certificate, existing Survey Sketch, Photo ID of buyer and seller, at least 4 photographs of the "
        "property.\n"
        "Please upload them at the earliest so that the application can move to the next stage.\n"
        "Sub Inspector Surveyor, [Ward / Block]"),
    "tahsildar_time": (
        "The workflow guide gives the Tahsildar's digital sign-off about 3 working days (for ISD the final signature; for NISD the "
        "review and approval). I do not have a measured average for the Tahsildar desk; for the actual movement of one file ask: "
        "workflow history of [application number]."),
    "aadhaar_all": (
        "I do not show Aadhaar numbers, for any owner or applicant -- only whether one is recorded. For an owner's recorded details of "
        "one application ask, for example: new owner details of [application number]."),
    "litigation_implies": (
        "What a litigation flag means for applications, from the rules: ongoing court cases, disputed ownership claims and stay "
        "orders are a listed rejection reason (Litigation Flag); when verifying an NISD file the officer checks there is no pending "
        "dispute or litigation; and a sub-division or transfer must not proceed past an active court stay. Only one application can be "
        "active on a survey number at a time, so a pending file also blocks new filings on that survey. To see whether a survey "
        "carries a litigation flag ask, for example: is there litigation on survey 120?"),
    "rejection_reasons": (
        "Common rejection reasons (workflow guide): 1) incomplete documents -- a missing, invalid or expired Sale Deed, EC or ID, or "
        "documents not properly attested; 2) area mismatch -- the sale deed area differs from the survey records or the sub-division "
        "areas do not add up to the original; 3) encroachment found at the field visit, boundary disputes or unauthorised construction; "
        "4) litigation flag -- an ongoing court case, disputed ownership or a stay order; 5) revenue arrears -- pending property tax or dues "
        "on the survey number; 6) invalid survey number -- it does not exist, belongs to another jurisdiction or is already sub-divided. "
        "After a rejection the applicant has 30 days to resubmit (up to 3 resubmissions). For why one file was rejected ask: why was "
        "[application number] rejected?"),
    "officer_names": (
        "The register holds desks and roles, not officer names: I cannot say who your district officer, Tahsildar, DIS or Senior "
        "Draughtsman is. What I can show is your own posting (ask: my jurisdiction) and which desk a file is at (ask: workflow history "
        "of [application number])."),
    "fee_other": None,   # built live (needs the register)
    "dups": None,        # built live
    "repeat": None,      # built live
    "survey_multi": None,
    "flagged": None,
    "by_reason": None,
}
TA = {
    "field_visit": (
        "கள ஆய்வு (Field visit) என்பது ஒரு விண்ணப்பம் அடுத்த கட்டத்துக்குச் செல்லும் முன் SIS அலுவலர் நிலத்தில் நேரில் செய்யும் சரிபார்ப்பு. "
        "நீங்கள்: புல எண், உட்பிரிவை நிலத்தில் கண்டறிவது; எல்லைகள், பரப்பை அளந்து பட்டா, கிரய ஆவணத்துடன் ஒப்பிடுவது; GPS மூலம் மூலைகளைக் குறிப்பது; "
        "ஆக்கிரமிப்பு உள்ளதா எனப் பார்ப்பது; விண்ணப்பதாரர், கூட்டு உரிமையாளர்களின் கையொப்பம் பெறுவது; புகைப்படம், குறிப்புகள், தோராய வரைபடம் தயாரிப்பது. "
        "விதிகள்: ISD (0154)-க்கு கட்டாயம்; NISD (0153)-க்கு தேவையில்லை (சிக்கல் இருந்தால் தவிர); சமர்ப்பித்த 15 வேலை நாட்களுக்குள் திட்டமிட வேண்டும்; "
        "தேதி மாற்ற தாசில்தார் ஒப்புதல் மட்டுமே."),
    "rule15": (
        "15 வேலை நாள் விதி: ISD விண்ணப்பத்தின் கள ஆய்வை சமர்ப்பித்த 15 வேலை நாட்களுக்குள் (திங்கள்-வெள்ளி) திட்டமிட வேண்டும். அதற்குள் முடியாவிட்டால் "
        "கோப்பு 'overdue' ஆகும்: டாஷ்போர்டில் சிவப்புக் குறி, எஸ்கலேஷன் அறிவிப்பு, செயல்திறன் அளவீட்டில் பாதிப்பு; தாமதத்துக்கு காரணம் தர வேண்டும். "
        "இது ISD-க்கு மட்டும். சேவை SLA (NISD 15-20, ISD 30-35 வேலை நாட்கள்) தனி கணக்கு."),
    "pending_vs_overdue": (
        "இல்லை, இவை ஒன்றல்ல. 'Pending' என்பது ஒரு நிலை (status) -- pending / in_progress / escalated / approved / rejected ஆகியவற்றில் ஒன்று; "
        "கோப்பு இன்னும் திறந்திருக்கிறது என்று மட்டும் பொருள். 'Overdue' என்பது தனி கொடி -- ISD கோப்பின் கள ஆய்வு 15 வேலை நாட்களுக்குள் முடிக்கப்படாவிட்டால் "
        "மட்டுமே பொருந்தும்; NISD-க்கு கள ஆய்வு இல்லை என்பதால் அது ஒருபோதும் overdue ஆகாது. pending கோப்பு பொதுவாக overdue ஆகாது -- 15 நாள் கெடு "
        "முடிந்த பின்னரே overdue ஆகும். ஆய்வுக்கு நிர்ணயிக்கப்பட்ட தேதியை தாண்டியது என்பதும் வேறு, முந்தைய நிலை."),
    "noshow": (
        "விண்ணப்பதாரர் வராவிட்டால் என்ன செய்ய வேண்டும் என்று ஆவணங்களில் தனி நடைமுறை இல்லை. உள்ளவை: 15 வேலை நாட்களுக்குள் ஆய்வை முடிக்க வேண்டும் "
        "(இல்லையெனில் overdue, காரணம் தர வேண்டும்); ஆய்வு தேதி மாற்ற தாசில்தாரின் ஒப்புதல் தேவை; விண்ணப்பதாரர், கூட்டு உரிமையாளர் கையொப்பம் ஆய்வின்போது பெறப்படும்."),
    "approve_without_visit": (
        "ISD (0154): இல்லை -- கள ஆய்வு கட்டாயம். NISD (0153): ஆவண சரிபார்ப்பு மட்டுமே; சிக்கல் இருந்தால் தவிர ஆய்வு தேவையில்லை. மேலும் SIS அலுவலர் அனுமதி வழங்குவதில்லை: "
        "ISD: SIS -> உதவி வரைவாளர் (SD) -> DIS -> தாசில்தார் டிஜிட்டல் கையொப்பம்; NISD: SIS -> மண்டல தாசில்தார் (DSC வைத்திருப்பவர்) அனுமதிப்பார். "
        "இந்த உதவியாளர் பதிவுகளை படிக்க மட்டுமே; அனுமதி TAMILNILAM போர்ட்டலில்."),
    "docs": ("பட்டா மாற்றத்துக்கான ஆவணங்கள்.\nNISD (0153): 1) பதிவு செய்யப்பட்ட கிரய ஆவணம் 2) வில்லங்கச் சான்று (13 ஆண்டுகள், 3 மாதம் செல்லுபடி) "
             "3) புகைப்பட அடையாளம் (ஆதார் + ஒரு அரசு அடையாளம்; வாங்குபவர், விற்பவர்) 4) தற்போதைய பட்டா நகல்.\n"
             "ISD (0154): 1) கிரய ஆவணம் 2) வில்லங்கச் சான்று 3) தற்போதைய நில அளவை வரைபடம் 4) புகைப்பட அடையாளம் (ஆதார் கட்டாயம் + ஒன்று) "
             "5) சொத்தின் புகைப்படங்கள் (குறைந்தது 4 கோணங்கள்)."),
    "sketch": (
        "உட்பிரிவு வரைபடம் (ISD): கள ஆய்வுக்குப் பின் குறிப்புகள், தோராய வரைபடத்துடன் உதவி வரைவாளருக்கு (SD) அனுப்புவீர்கள். SD புதிய உட்பிரிவு எண்களுடன் "
        "விரிவான வரைபடம் தயாரித்து, அனைத்து உட்பிரிவுகளின் பரப்பு கூட்டுத்தொகை மூல புல பரப்புக்குச் சமமா எனச் சரிபார்ப்பார் (145 -> 145/1A, 145/1B...). "
        "SD கட்டம் பொதுவாக 7-10 வேலை நாட்கள். பின்னர் DIS மதிப்பாய்வு செய்து {உள்ள உட்பிரிவு}/T{வரிசை} (எ.கா 3/T1) தற்காலிக எண் வழங்குவார்; "
        "ஒப்புதலில் இறுதி எண் கிடைக்கும்."),
    "steps_nisd": (
        "NISD (0153) படிகள்: 1) CSC / பதிவாளர் / குடிமகன் போர்ட்டலில் விண்ணப்பம் 2) SIS ஆவண சரிபார்ப்பு (சுமார் 5 வேலை நாட்கள்; ஆய்வு இல்லை) "
        "3) மண்டல தாசில்தார் DSC மூலம் அனுமதித்து பட்டா மாற்ற ஆணை (சுமார் 3 நாட்கள்) 4) உரிமை மாற்றம், நில பதிவு புதுப்பிப்பு 5) குடிமகன் பட்டா பதிவிறக்கம். மொத்தம் சுமார் 15-20 வேலை நாட்கள்."),
    "steps_isd": (
        "ISD (0154) படிகள்: 1) விண்ணப்பம் 2) SIS ஒதுக்கீடு 3) 15 வேலை நாட்களுக்குள் கள ஆய்வு (கட்டாயம்) 4) SD உட்பிரிவு வரைபடம் (7-10 நாட்கள்) "
        "5) DIS மதிப்பாய்வு, தற்காலிக எண், ஒப்புதல் (சுமார் 5 நாட்கள்) 6) தாசில்தார் டிஜிட்டல் கையொப்பம் (சுமார் 3 நாட்கள்) 7) பட்டா மாற்ற ஆணை. மொத்தம் சுமார் 30-35 வேலை நாட்கள்."),
    "speed_up": (
        "விதிகளின்படி: ISD கள ஆய்வை முன்கூட்டியே திட்டமிடுங்கள் (15 வேலை நாட்கள்); ஆவணங்களை உடனே சரிபாருங்கள்; ஆக்கிரமிப்பு, வழக்கு, வரி நிலுவை, பரப்பு பொருத்தமின்மை "
        "ஆகியவற்றை அனுப்பும் முன் சரிபாருங்கள்; SD-க்கு விரைவாக ஒப்படையுங்கள். எதை முதலில் செய்வது என அறிய: 'அடுத்து எந்த விண்ணப்பத்துக்கு கள ஆய்வு செய்ய வேண்டும்?' எனக் கேளுங்கள்."),
    "litigation_implies": (
        "வழக்கு குறியீடு இருந்தால்: நடப்பு நீதிமன்ற வழக்கு, உரிமை தகராறு, தடை உத்தரவு ஆகியவை நிராகரிப்புக்கான காரணங்கள்; NISD சரிபார்ப்பில் நிலுவை "
        "தகராறு / வழக்கு இல்லை என உறுதி செய்ய வேண்டும்; நடப்பு தடை உத்தரவைத் தாண்டி உட்பிரிவு / மாற்றம் செய்யக்கூடாது. ஒரு புல எண்ணில் ஒரே நேரத்தில் "
        "ஒரு விண்ணப்பம் மட்டுமே செயலில் இருக்கலாம். எ.கா: survey 120-ல் வழக்கு உள்ளதா?"),
    "rejection_reasons": (
        "பொதுவான நிராகரிப்பு காரணங்கள்: 1) முழுமையற்ற / செல்லாத ஆவணங்கள் 2) பரப்பு பொருத்தமின்மை 3) ஆக்கிரமிப்பு, எல்லை தகராறு 4) வழக்கு / தடை உத்தரவு "
        "5) வரி நிலுவை 6) தவறான / வேறு அதிகார எல்லை புல எண். நிராகரிப்புக்குப் பின் 30 நாட்களுக்குள் மீண்டும் சமர்ப்பிக்கலாம் (அதிகபட்சம் 3 முறை)."),
    "officer_names": (
        "பதிவேட்டில் மேசைகள் / பொறுப்புகள் மட்டுமே உள்ளன, அலுவலர் பெயர்கள் இல்லை: மாவட்ட அதிகாரி, தாசில்தார் யார் என்று சொல்ல இயலாது. உங்கள் பணியிடம் "
        "அறிய: my jurisdiction."),
    "note": None,
    "tahsildar_time": (
        "பணிப்பாய்வு வழிகாட்டியின்படி தாசில்தார் டிஜிட்டல் கையொப்பம் / ஒப்புதல் சுமார் 3 வேலை நாட்கள். தாசில்தார் மேசையின் சராசரி நேரம் என்னிடம் இல்லை; "
        "ஒரு கோப்பின் இயக்கத்துக்கு: workflow history of [விண்ணப்ப எண்]."),
    "aadhaar_all": ("ஆதார் எண்களை நான் காட்டுவதில்லை -- ஆதார் பதிவாகியுள்ளதா என்பதை மட்டுமே சொல்வேன். ஒரு விண்ணப்பத்தின் உரிமையாளர் விவரத்துக்கு: new owner details of [விண்ணப்ப எண்]."),
}

# (topic, English cue, Tamil / Tanglish cue). A cue must name the thing asked about, so data questions are left alone.
_CUES: List[Tuple[str, str, str]] = [
    ("aadhaar_all", r"\b(?:aadhaar|aadhar)\b.{0,30}\b(?:all|every|each)\b|\b(?:all|every)\b.{0,30}\b(?:aadhaar|aadhar)\b|\b(?:aadhaar|aadhar)\s+numbers?\s+of\b",
     r"ஆதார்.{0,25}(?:அனைத்து|எல்லா)"),
    ("approve_without_visit", r"\b(?:approve|approval|sanction)\w*\b.{0,40}\bwithout\b.{0,25}\bfield\s*visit|\bwithout\b.{0,25}\bfield\s*visit.{0,30}\b(?:approve|allowed|possible)|"
                              r"\b(?:isd|nisd|involving\s+sub[\s-]?division)\b.{0,30}\b(?:need|needs|require|requires|mandatory|compulsory)\b.{0,20}\b(?:field|site)\s*(?:visit|inspection)|"
                              r"\b(?:field|site)\s*(?:visit|inspection)\b.{0,25}\b(?:need|needs|required|mandatory|compulsory)\b.{0,20}\b(?:isd|nisd)\b",
     r"கள\s*ஆய்வு\s*இல்லாமல்|field\s*visit\s*illama|visit\s*illama|"
     r"(?:isd|nisd|உட்பிரிவு|involving\s*sub[\s-]?division).{0,30}(?:site\s*visit|field\s*visit|கள\s*ஆய்வு).{0,15}(?:pannanuma|venuma|theva|அவசியமா|தேவையா)"),
    ("pending_vs_overdue", r"\bis\b.{0,10}\bpending\b.{0,20}\b(?:same\s+as|equal\s+to|the\s+same)\b.{0,10}\boverdue\b|"
                          r"\bis\b.{0,10}\boverdue\b.{0,20}\b(?:same\s+as|equal\s+to|the\s+same)\b.{0,10}\bpending\b|"
                          r"\bdifference\b.{0,15}\bbetween\b.{0,10}\bpending\b.{0,10}\b(?:and|&)\b.{0,10}\boverdue\b|"
                          r"\bdifference\b.{0,15}\bbetween\b.{0,10}\boverdue\b.{0,10}\b(?:and|&)\b.{0,10}\bpending\b|"
                          r"\bpending\b.{0,8}\bvs\.?\b.{0,8}\boverdue\b|\boverdue\b.{0,8}\bvs\.?\b.{0,8}\bpending\b",
     r"pending.{0,15}(?:overdue|தாமத).{0,15}ஒன்றுதானா|pending.{0,10}overdue.{0,10}same\s*a|"
     r"pending.{0,15}overdue.{0,15}(?:vithyasam|vittiyasam|difference)"),
    ("rule15", r"\b15[\s-]*(?:working[\s-]*)?day\b.{0,30}\brule\b|\brule\b.{0,20}\b15[\s-]*(?:working[\s-]*)?days?\b|\bexplain\b.{0,25}\b15\s*(?:working\s*)?days?\b",
     r"15\s*(?:வேலை\s*)?நாள்?\s*விதி|15\s*(?:naal|day)\s*(?:vidhi|rule)"),
    ("noshow", r"\b(?:applicant|owner|party)\b.{0,30}\b(?:does\s*n[o']?t|do\s*not|not|fails?\s+to)\b.{0,15}\b(?:turn|show|come|attend|available)\b|\bno[\s-]?show\b",
     r"(?:விண்ணப்பதாரர்|உரிமையாளர்).{0,25}(?:வராவிட்டால்|வரவில்லை)|(?:applicant|owner)\s+(?:varala|varaama|vara\s*maattaanga)"),
    ("tahsildar_time", r"\b(?:how\s+(?:many\s+days|long)|average|avg)\b.{0,30}\btahsildar\b.{0,25}\b(?:sign|take|approve|time)|\btahsildar\b.{0,30}\b(?:take|takes)\b.{0,15}\b(?:to\s+sign|days)",
     r"தாசில்தார்.{0,25}(?:எத்தனை\s*நாள்|சராசரி)"),
    ("sketch", r"\b(?:sub[\s-]?division|subdiv)\s+(?:sketch|drawing|plan)\b.{0,40}\b(?:prepared|made|created|drawn|prepare|make)\b|\b(?:how|explain)\b.{0,30}\b(?:sub[\s-]?division\s+)?sketch\b.{0,25}\b(?:prepared|made|created|work)|"
               r"\bsketch\b.{0,40}\b(?:after|once|then|next)\b.{0,30}\b(?:senior\s+draughtsman|\bsd\b)|\bwhat\s+happens\b.{0,40}\bsketch\b|"
               # "how are sub-division numbers assigned", "does the temporary
               # number become the final one" -- the same numbering fact this
               # topic already states (145 -> 145/1A, 145/1B; T-number ->
               # final on approval), just asked without the word "sketch".
               r"\bhow\s+(?:are|is)\b.{0,30}\bsub[\s-]?division\b.{0,20}\b(?:numbers?|assigned|numbered)\b|"
               r"\bsub[\s-]?division\b.{0,20}\bnumbers?\b.{0,20}\bassigned\b|"
               r"\b(?:does|do)\b.{0,10}\bthe\b.{0,20}\btemporary\b.{0,20}\b(?:number|subdivision)\b.{0,20}\bfinal\b|"
               r"\btemporary\s+numbers?\b.{0,20}\bbecome\b.{0,20}\bfinal\b",
     r"உட்பிரிவு\s*வரைபடம்|sub\s*division\s*sketch\s*(?:epdi|eppadi|ready)|sketch\s*(?:epdi|eppadi)"),
    ("steps_nisd", r"\bsteps?\b.{0,40}\bnisd\b|\bnisd\b.{0,40}\bsteps?\b|\bnisd\b.{0,25}\b(?:process|procedure|workflow)\b.{0,20}\b(?:explain|steps|from)\b",
     r"NISD.{0,25}(?:படிகள்|படி)"),
    ("steps_isd", r"\bsteps?\b.{0,40}\bisd\b|\bisd\b.{0,40}\bsteps?\b(?!.*\bnisd\b)", r"ISD.{0,25}(?:படிகள்|படி)"),
    ("docs", r"\b(?:documents?|docs)\b.{0,25}\b(?:needed|required|necessary|to\s+submit|do\s+i\s+need|for)\b.{0,30}\b(?:patta|transfer|sub[\s-]?division|isd|nisd)\b|"
             r"\bwhat\b.{0,15}\b(?:documents?|docs)\b.{0,20}\b(?:needed|required)\b",
     r"(?:ஆவணங்கள்?|documents?)\s*(?:என்ன\s*)?(?:தேவை|வேண்டும்|vendum|venum|thevai)|patta\s*transfer\s*ku\s*enna\s*documents?"),
    ("field_visit", r"\b(?:what\s+is|what's|explain|define|meaning\s+of)\b.{0,25}\bfield\s*(?:visit|inspection)\b|\bfield\s*visit\b.{0,25}\b(?:involve|involves|mean|means|meaning|check|checks)\b|"
                    r"\bwhat\b.{0,20}\bshould\s+(?:i|you|we|an?\s+officer)\s+check\b.{0,30}\bfield\s*(?:visit|inspection)\b",
     r"கள\s*ஆய்வு\s*என்றால்|field\s*visit\s*(?:endral|enna\s*nu)"),
    ("speed_up", r"\b(?:speed\s*up|expedite|clear\s+faster|fast(?:er)?)\b.{0,25}\b(?:pending|files|applications)\b|\bhow\s+(?:can|do)\s+i\s+(?:clear|finish)\b.{0,20}\bpending\b",
     r"pending\s*files?\s*(?:vegama|vegamaa|speed)"),
    ("note", r"\b(?:write|draft|compose)\b.{0,30}\b(?:note|letter|message|mail|email)\b.{0,40}\b(?:applicant|owner)\b", r"(?:கடிதம்|குறிப்பு).{0,20}(?:எழுது|தயார்)"),
    ("litigation_implies", r"\b(?:litigation|court\s+case|stay\s+order|legal\s+dispute)\b.{0,50}\b(?:imply|implies|mean|means|effect|impact|consequence|affect)\b.{0,30}\b(?:applications?|transfer|processing|filings?)\b",
     r"வழக்கு.{0,30}(?:என்ன\s*அர்த்தம்|பாதிக்க|விளைவு)"),
    ("rejection_reasons", r"\b(?:common|usual|main|typical|frequent|possible)\s+(?:rejection\s+)?reasons?\b.{0,25}\breject|\b(?:rejection\s+reasons?|reasons?\s+for\s+rejection)\b|"
                          r"\bwhy\s+(?:are|were|do|get|is)\s+(?:most\s+|the\s+)?(?:applications?|files)\b.{0,20}\breject|"
                          r"\bwhat\s+kind\s+of\b.{0,30}\b(?:issues?|problems?)\b.{0,30}\b(?:can|could|might)\b.{0,20}\b(?:block|stop|prevent|delay|reject)\b",
     r"நிராகரிப்பு\s*காரணங்கள்|நிராகரிக்கப்பட்ட.{0,25}ஏன்|reject\w*\s+(?:enna\s+)?reason"),
    ("officer_names", r"\bwho\s+is\s+(?:my|the)\s+(?:district\s+officer|collector|tahsildar|dis|sd|senior\s+draughtsman|supervisor|superior|boss|reporting\s+officer)\b|"
                      r"\b(?:tahsildar|collector|district\s+officer)\b.{0,12}\b(?:of\s+my\s+(?:ward|block|district|taluk))\b|\bmy\s+(?:supervisor|superior|boss|reporting\s+officer)\b",
     r"(?:மாவட்ட\s*அதிகாரி|தாசில்தார்).{0,12}யார்"),
    ("survey_multi", r"\b(?:which|what|list|show)\b.{0,25}\bsurvey\s*(?:numbers?|nos?)\b.{0,30}\b(?:multiple|more\s+than\s+one|several|many|repeat\w*)\b.{0,15}\bapplications?\b",
     r"(?:ஒன்றுக்கு\s*மேற்பட்ட).{0,20}(?:சர்வே|புல)"),
    ("flagged", r"\b(?:which|what|list|show)\b.{0,25}\b(?:applications?|surveys?|survey\s+numbers?|files)\b.{0,25}\b(?:have|with|has|having|under|carry|carrying)\b.{0,12}\b(encroachment|litigation|court\s+case|stay\s+order)\b",
     r"(?:ஆக்கிரமிப்பு|வழக்கு).{0,20}(?:உள்ள|உள்ளவை).{0,15}(?:விண்ணப்ப|சர்வே)"),
    ("by_reason", r"\b(?:applications?|files)\b.{0,20}\b(?:for|with|under|of)\s+(?:a\s+|the\s+)?(sale|settlement|release|gift|partition|inheritance|court\s+order)(?:\s+deed)?\b(?:\s+reason)?",
     r"(?:கிரய|தான|விடுவிப்பு).{0,15}(?:விண்ணப்ப)"),
    ("fee_other", r"\b(?:fee|fees|charge|charges)\b.{0,30}\b(?:f[\s-]?line|0178|tslr|0156|0158|modification|correction)\b|\b(?:f[\s-]?line|0178|tslr|0156)\b.{0,30}\b(?:fee|fees|charge|charges)\b",
     r"(?:F-?Line|0178).{0,20}(?:கட்டணம்)"),
    ("repeat", r"\bapplicants?\b.{0,40}\b(?:more\s+than\s+one|multiple|several|repeat\w*|many|2\s+or\s+more)\b.{0,20}\b(?:applications?|files)\b|\b(?:same|repeat)\s+applicants?\b",
     r"(?:ஒன்றுக்கு\s*மேற்பட்ட|பல).{0,20}விண்ணப்ப"),
    ("dups", r"\b(?:suspicious|duplicate[ds]?|duplicated|anything\s+unusual|unusual|irregular)\b.{0,40}\b(?:applications?|files|pending)\b|\bapplications?\b.{0,30}\b(?:look|looks)\s+(?:suspicious|duplicated)",
     r"சந்தேகத்துக்கிடமான|இரட்டை"),
]
_CUES = [(t, re.compile(e, F), re.compile(a, F)) for t, e, a in _CUES]
_STATIC = {k for k, v in EN.items() if v}


_IMPLY = re.compile(r"\bwhat\s+(?:does|do|would)\s+(?:that|this|it)\s+(?:imply|mean)\b.{0,25}\b(?:applications?|new|filings?)\b", F)
_SURVEY_NO = re.compile(r"\bsurvey\s*(?:no\.?|number)?\s*\d|\bsy\.?\s*\d", F)


def match(message: str, history_text: str = "") -> Optional[str]:
    text = message or ""
    if _APP.search(text) or _SURVEY_NO.search(text) or len(text.split()) > 24:
        return None                  # a number in the message makes it a data question for the register
    if history_text and _IMPLY.search(text) and re.search(r"litigation|court|stay\s+order", history_text, F):
        return "litigation_implies"   # "...and what does that imply for new applications", right after a litigation answer
    for topic, en, ta in _CUES:
        if en.search(text) or ta.search(text):
            if topic == "field_visit" and _FV_FIELD.search(text):
                continue              # "what is the (proposed) field visit date" asks for a value, not what a visit is
            return topic
    return None


_FV_FIELD = re.compile(r"\bproposed\b|\bfield\s*visit\w*\s+(?:date|dt|status|statuz|notes?|remarks?|schedul\w*|time|day|record|details?)\b"
                       r"|\b(?:date|status|notes|remarks)\s+(?:of|for)\s+(?:the\s+)?field\s*visit", F)


def answer(message: str, ta: bool = False, history_text: str = "") -> Optional[Tuple[str, str]]:
    """(topic, static answer) or None. Topics with live data come back as (topic, "") for the caller to fill."""
    topic = match(message, history_text)
    if not topic:
        return None
    if topic not in _STATIC:
        return topic, ""
    return topic, (TA.get(topic) or EN[topic]) if ta else EN[topic]


# ── live questions ──────────────────────────────────────────────────────────────────────────────────
async def fline_fee(db, ta: bool = False) -> str:
    from sqlalchemy import text as sql
    base = ("The reference documents give a fee only for the two codes the SIS works (0153 NISD: Rs 100 government fee plus Rs 60 CSC charge; "
            "0154 ISD: Rs 400 plus Rs 60) -- there is no fee schedule for F-Line (0178) or the other service codes. ")
    try:
        async with db.begin_nested():
            r = (await db.execute(sql("SELECT count(*), min(amount::numeric), max(amount::numeric) FROM f_line_service_application_information_urban "
                                      "WHERE amount ~ '^[0-9.]+$'"))).first()
    except Exception:
        r = None
    if r and r[0]:
        return base + f"The amounts actually paid on {r[0]} F-Line files in the register range from Rs {r[1]:g} to Rs {r[2]:g} (a challan amount, not a published fee)."
    return base + "The register holds no F-Line payment amount to quote."


def duplicates(rows: List[dict], ta: bool = False) -> str:
    """Survey numbers carrying more than one open application -- the one-active-application-per-survey rule."""
    by = {}
    for r in rows:
        if str(r.get("status")) == "pending" and r.get("survey_no") and str(r.get("service_code")) in ("0153", "0154"):
            by.setdefault((str(r.get("ward_number")) + "/" + str(r.get("block_number")), str(r["survey_no"])), []).append(r["application_number"])
    dup = {k: v for k, v in by.items() if len(v) > 1}
    rule = "The workflow guide allows one active ISD / NISD application per survey number at a time."
    if not dup:
        return f"No survey number has more than one open application, so nothing duplicated stands out. {rule}"
    lines = [f"{len(dup)} survey number(s) carry more than one open application ({rule[:-1].lower()}):"]
    for (ward, sv), apps in sorted(dup.items(), key=lambda kv: -len(kv[1]))[:12]:
        lines.append(f"  • survey {sv} (ward/block {ward}): " + ", ".join(apps))
    lines.append("That is what the register shows; it does not by itself mean a mistake -- check each file.")
    return "\n".join(lines)


def repeat_applicants(rows: List[dict], ta: bool = False) -> str:
    if not rows:
        return "No applicant (same name and mobile) has more than one ISD / NISD application in your jurisdiction."
    lines = [f"{len(rows)} applicant(s) have more than one ISD / NISD application (a person is matched by name and mobile):"]
    for r in rows[:15]:
        lines.append(f"  • {r['name']}: " + ", ".join(f"{n} ({st})" for n, st in r["applications"]))
    return "\n".join(lines)


_REASONS = {"sale": ["sale"], "settlement": ["settlement"], "release": ["release"], "gift": ["gift"], "partition": ["partition"],
            "inheritance": ["inheritance"], "court order": ["court order", "court"]}


def reason_of(message: str) -> List[str]:
    m = re.search(r"\b(sale|settlement|release|gift|partition|inheritance|court\s+order)\b", message or "", F)
    return _REASONS.get(re.sub(r"\s+", " ", m.group(1).lower()), []) if m else []


def survey_multi(rows: List[dict], ta: bool = False) -> str:
    """Survey numbers carrying more than one application of any status (register rows)."""
    by = {}
    for r in rows:
        if r.get("survey_no"):
            by.setdefault((str(r.get("ward_number")) + "/" + str(r.get("block_number")), str(r["survey_no"])), []).append((r["application_number"], r.get("status")))
    multi = {k: v for k, v in by.items() if len(v) > 1}
    if not multi:
        return "No survey number carries more than one application."
    lines = [f"{len(multi)} survey number(s) carry more than one application (any status):"]
    for (ward, sv), apps in sorted(multi.items(), key=lambda kv: -len(kv[1]))[:15]:
        lines.append(f"  • survey {sv} (ward/block {ward}): " + ", ".join(f"{n} ({st})" for n, st in apps))
    return "\n".join(lines)


def flagged(data: dict, kind: str, ta: bool = False) -> str:
    sv = data.get("surveys") or []
    if not sv:
        return f"No survey number in your jurisdiction is flagged for {kind} in the register, so no application is affected."
    lines = [f"{len(sv)} survey number(s) flagged for {kind}:"]
    for r in sv:
        lines.append(f"  • survey {r['survey_no']}: " + ", ".join(f"{n} ({st})" for n, st in r["applications"]))
    return "\n".join(lines)


def by_reason(rows: List[tuple], word: str, ta: bool = False) -> str:
    if not rows:
        return f"No ISD / NISD application in your jurisdiction carries the declared reason \"{word}\"."
    lines = [f"{len(rows)} ISD / NISD application(s) with declared reason {word}:"]
    for n, rs, st in rows[:20]:
        lines.append(f"  • {n} ({rs}, {st})")
    if len(rows) > 20:
        lines.append(f"  ... and {len(rows) - 20} more")
    lines.append("The declared reason is held for ISD / NISD files only.")
    return "\n".join(lines)