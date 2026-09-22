"""
Question bank for workflow questions answered from `backend/documents/`.

These are the questions an SIS officer asks that the chatbot answers from the
RAG corpus (workflow_guide.txt, survey_manual.txt, faq_english.txt,
land_rules.txt, tamilnilam_urban_services_and_districts.txt,
sis_upload_checklist.txt, district_codes.txt) rather than from the database.

`build_bank()` returns Question records; `--txt` emits the flat numbered form.
Nothing here touches the DB or the LLM.

Each item carries `source` -- the corpus file whose text should ground the
answer -- and `expect`, a short note on the correct answer, so the output
doubles as an answer key when someone runs the questions through a live stack.

TRAP items name something the corpus deliberately does NOT contain (FMB, an
applicant e-mail, an `escalated` application in the seed data). The correct
behaviour is to say so, never to invent it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Question:
    text_en: str
    source: str
    topic: str
    expect: str = ""
    text_ta: str = ""
    kind: str = "single"     # single | followup | trap
    follow_of: str = ""


# (english, tamil/tanglish, source_file, expected-answer-note)
ITEMS: list[tuple] = [
    # ── ISD workflow chain ─────────────────────────────────────────────
    ("What are the steps in the ISD workflow?", "ISD பணிப்பாய்வின் படிகள் என்ன?",
     "workflow_guide.txt", "submission -> SIS field visit -> SD sketch -> DIS review -> Tahsildar DSC -> patta order", "isd_chain"),
    ("What is the full ISD approval chain?", "முழு ISD அங்கீகார சங்கிலி என்ன?",
     "tamilnilam_urban_services_and_districts.txt", "SIS -> SD -> DIS -> Tahsildar (DSC)", "isd_chain"),
    ("Who prepares the sub-division sketch for an ISD application?", "ISD விண்ணப்பத்திற்கு உட்பிரிவு வரைபடத்தை யார் தயாரிக்கிறார்?",
     "workflow_guide.txt", "the Senior Draughtsman (SD)", "isd_chain"),
    ("Who reviews the SD sketch and field report on an ISD file?", "ISD கோப்பில் SD வரைபடம் மற்றும் கள அறிக்கையை யார் மதிப்பாய்வு செய்கிறார்?",
     "workflow_guide.txt", "the Deputy Inspector Surveyor (DIS)", "isd_chain"),
    ("Who applies the digital signature on the patta transfer order?", "பட்டா மாற்ற ஆணையில் இலக்க கையொப்பத்தை யார் இடுகிறார்?",
     "workflow_guide.txt", "the Tahsildar, using the DSC", "isd_chain"),
    ("How long does an ISD application take end to end?", "ISD விண்ணப்பம் தொடக்கம் முதல் முடிவு வரை எவ்வளவு நேரம் எடுக்கும்?",
     "workflow_guide.txt", "approximately 30-35 working days", "isd_chain"),
    ("How many working days does SD handoff take?", "SD ஒப்படைப்பு எத்தனை வேலை நாட்கள் ஆகும்?",
     "workflow_guide.txt", "7-10 working days", "isd_chain"),
    ("How many working days for DIS approval on ISD?", "ISD-ல் DIS அங்கீகாரத்திற்கு எத்தனை வேலை நாட்கள்?",
     "workflow_guide.txt", "5 working days", "isd_chain"),
    ("How many working days for the Tahsildar DSC step?", "தாசில்தார் DSC படிக்கு எத்தனை வேலை நாட்கள்?",
     "workflow_guide.txt", "3 working days", "isd_chain"),

    # ── NISD workflow chain ────────────────────────────────────────────
    ("What are the steps in the NISD workflow?", "NISD பணிப்பாய்வின் படிகள் என்ன?",
     "workflow_guide.txt", "submission -> SIS document verification -> Zonal Level Tahsildar approval + DSC", "nisd_chain"),
    ("Does an NISD application need a field visit?", "NISD விண்ணப்பத்திற்கு கள ஆய்வு தேவையா?",
     "workflow_guide.txt", "no, unless a specific issue is flagged", "nisd_chain"),
    ("Is there an SD or DIS step for NISD?", "NISD-க்கு SD அல்லது DIS படி உண்டா?",
     "workflow_guide.txt", "no - NISD has no SD and no DIS step", "nisd_chain"),
    ("Who approves an NISD application?", "NISD விண்ணப்பத்தை யார் அங்கீகரிக்கிறார்?",
     "workflow_guide.txt", "the Zonal Level Tahsildar (ZDT), who holds the DSC key", "nisd_chain"),
    ("How long does an NISD application take?", "NISD விண்ணப்பம் எவ்வளவு நேரம் எடுக்கும்?",
     "workflow_guide.txt", "approximately 15-20 working days", "nisd_chain"),
    ("What documents does the SIS verify for an NISD file?", "NISD கோப்பிற்கு SIS எந்த ஆவணங்களைச் சரிபார்க்கிறார்?",
     "workflow_guide.txt", "Sale Deed, EC, Photo ID; checks survey number and area, no pending litigation", "nisd_chain"),
    ("nisd verification timeline enna", "NISD சரிபார்ப்பு கால அளவு என்ன?",
     "workflow_guide.txt", "5 working days", "nisd_chain"),

    # ── MERGE workflow ────────────────────────────────────────────────
    ("Which workflow chain does a MERGE application follow?", "MERGE விண்ணப்பம் எந்த பணிப்பாய்வு சங்கிலியைப் பின்பற்றுகிறது?",
     "workflow_guide.txt", "the ISD chain", "merge_chain"),
    ("How long does a merge application take?", "இணைப்பு விண்ணப்பம் எவ்வளவு நேரம் எடுக்கும்?",
     "survey_manual.txt", "approximately 25-30 working days", "merge_chain"),
    ("What is the SIS verification timeline for a merge?", "இணைப்பிற்கு SIS சரிபார்ப்பு கால அளவு என்ன?",
     "survey_manual.txt", "10 working days", "merge_chain"),
    ("Is a field visit mandatory for a merge application?", "இணைப்பு விண்ணப்பத்திற்கு கள ஆய்வு கட்டாயமா?",
     "survey_manual.txt", "yes, mandatory within 15 days", "merge_chain"),

    # ── Roles ────────────────────────────────────────────────────────
    ("What does SD stand for in this workflow?", "இந்த பணிப்பாய்வில் SD என்றால் என்ன?",
     "land_rules.txt", "Senior Draughtsman - NOT the Survey and Settlement Department", "roles"),
    ("What does DIS stand for?", "DIS என்றால் என்ன?",
     "faq_english.txt", "Deputy Inspector Surveyor", "roles"),
    ("What does ZDT stand for?", "ZDT என்றால் என்ன?",
     "workflow_guide.txt", "Zonal Level Tahsildar", "roles"),
    ("Who holds the DSC key?", "DSC விசையை யார் வைத்திருக்கிறார்?",
     "workflow_guide.txt", "the Tahsildar / Zonal Level Tahsildar", "roles"),
    ("In which jurisdiction do SIS officers operate?", "SIS அலுவலர்கள் எந்த அதிகார வரம்பில் செயல்படுகிறார்கள்?",
     "tamilnilam_urban_services_and_districts.txt", "urban jurisdictions only (municipalities & corporations)", "roles"),
    ("What are the core duties of an SIS officer?", "SIS அலுவலரின் முக்கிய கடமைகள் என்ன?",
     "tamilnilam_urban_services_and_districts.txt", "field inspection, cadastral verification, boundary/sub-division/encroachment checks, inspection reports", "roles"),

    # ── Field visit ──────────────────────────────────────────────────
    ("What is a field visit?", "கள ஆய்வு என்றால் என்ன?",
     "workflow_guide.txt", "on-site verification by the SIS at the actual parcel", "field_visit"),
    ("What does the officer check during a field visit?", "கள ஆய்வின் போது அலுவலர் என்ன சரிபார்க்கிறார்?",
     "faq_english.txt", "survey number/sub-division on ground, boundaries & area vs patta/deed, GPS corners, boundary stones, encroachment, applicant + joint-owner signatures", "field_visit"),
    ("Within how many working days must a field visit be scheduled?", "எத்தனை வேலை நாட்களுக்குள் கள ஆய்வு திட்டமிடப்பட வேண்டும்?",
     "workflow_guide.txt", "15 working days of submission", "field_visit"),
    ("What happens if the field visit is not done within 15 days?", "15 நாட்களுக்குள் கள ஆய்வு செய்யப்படவில்லை என்றால் என்ன ஆகும்?",
     "workflow_guide.txt", "the application is marked overdue", "field_visit"),
    ("Can an SIS officer change a field visit date on their own?", "ஒரு SIS அலுவலர் தானாகவே கள ஆய்வு தேதியை மாற்ற முடியுமா?",
     "faq_english.txt", "no - only the Tahsildar may approve a change or reschedule", "field_visit"),
    ("Whom should I ask to reschedule a field visit?", "கள ஆய்வை மறுதிட்டமிட யாரிடம் கேட்க வேண்டும்?",
     "faq_english.txt", "the Tahsildar", "field_visit"),
    ("Is a field visit mandatory for every ISD application?", "ஒவ்வொரு ISD விண்ணப்பத்திற்கும் கள ஆய்வு கட்டாயமா?",
     "workflow_guide.txt", "yes, mandatory for ISD (0154) and MERGE (0155)", "field_visit"),
    ("field visit yaar kitta permission kekkanum date maathanum", "கள ஆய்வு தேதி மாற்ற யாரிடம் அனுமதி கேட்க வேண்டும்?",
     "faq_english.txt", "the Tahsildar", "field_visit"),

    # ── Escalation ───────────────────────────────────────────────────
    ("What are the escalation levels for an overdue application?", "காலாவதியான விண்ணப்பத்திற்கான மேல்முறையீட்டு நிலைகள் என்ன?",
     "workflow_guide.txt", "L1 at 15 days overdue (SIS+DIS), L2 at 30 (Tahsildar), L3 at 45 (DRO)", "escalation"),
    ("At how many days overdue does Level 2 escalation trigger?", "எத்தனை நாட்கள் தாமதத்தில் நிலை 2 மேல்முறையீடு தூண்டப்படுகிறது?",
     "workflow_guide.txt", "30 days overdue", "escalation"),
    ("Who is notified at Level 3 escalation?", "நிலை 3 மேல்முறையீட்டில் யாருக்கு அறிவிக்கப்படுகிறது?",
     "workflow_guide.txt", "the District Revenue Officer (DRO)", "escalation"),
    ("What colour flag marks a Level 2 escalation?", "நிலை 2 மேல்முறையீட்டைக் குறிக்கும் கொடி நிறம் என்ன?",
     "workflow_guide.txt", "red flag", "escalation"),

    # ── Temporary subdivision numbers ────────────────────────────────
    ("What is a temporary subdivision number?", "தற்காலிக உட்பிரிவு எண் என்றால் என்ன?",
     "faq_english.txt", "provisional number {existing_subdiv}/T{seq} assigned by DIS on ISD/MERGE until final numbers", "temp_subdiv"),
    ("Who assigns temporary subdivision numbers?", "தற்காலிக உட்பிரிவு எண்களை யார் ஒதுக்குகிறார்?",
     "faq_english.txt", "the Deputy Inspector Surveyor (DIS), after the SD sketch", "temp_subdiv"),
    ("What does the temporary number format {subdiv}/T{seq} mean?", "{subdiv}/T{seq} வடிவம் என்ன அர்த்தம்?",
     "survey_manual.txt", "part before /T = existing subdivision being split (0 if none); T = temporary; number after T = sequence counter", "temp_subdiv"),
    ("How do temporary numbers become final subdivision numbers?", "தற்காலிக எண்கள் எப்படி இறுதி உட்பிரிவு எண்களாகின்றன?",
     "faq_english.txt", "on DIS approval; T1 usually keeps the existing number, T2/T3 get new sequential numbers", "temp_subdiv"),
    ("Do rejected applications get final subdivision numbers?", "நிராகரிக்கப்பட்ட விண்ணப்பங்களுக்கு இறுதி உட்பிரிவு எண்கள் கிடைக்குமா?",
     "survey_manual.txt", "no - rejected applications keep temporary numbers and never receive final numbers", "temp_subdiv"),
    ("Which application types get temporary subdivision numbers?", "எந்த விண்ணப்ப வகைகளுக்கு தற்காலிக உட்பிரிவு எண்கள் கிடைக்கும்?",
     "survey_manual.txt", "only ISD (0154) and MERGE (0155)", "temp_subdiv"),
    ("In example 3/T1 and 3/T2, what final numbers do they get?", "3/T1 மற்றும் 3/T2 எடுத்துக்காட்டில், அவை என்ன இறுதி எண்களைப் பெறுகின்றன?",
     "survey_manual.txt", "3/T1 -> 3 (retains existing), 3/T2 -> 4 (new)", "temp_subdiv"),

    # ── Area validation ─────────────────────────────────────────────
    ("What is the critical area validation rule for sub-divisions?", "உட்பிரிவுகளுக்கான முக்கிய பரப்பளவு சரிபார்ப்பு விதி என்ன?",
     "survey_manual.txt", "sum of all sub-division areas must equal the original survey area", "area_rule"),
    ("What area tolerance is allowed on survey measurements?", "சர்வே அளவீடுகளில் அனுமதிக்கப்படும் பரப்பளவு சகிப்புத்தன்மை என்ன?",
     "survey_manual.txt", "+/- 0.5%", "area_rule"),
    ("For a 1000 sq.m survey, what total sub-division range is acceptable?", "1000 ச.மீ சர்வேக்கு, ஏற்கத்தக்க மொத்த உட்பிரிவு வரம்பு என்ன?",
     "survey_manual.txt", "995-1005 sq.m", "area_rule"),
    ("What must the SIS never do about sub-division extents?", "உட்பிரிவு பரப்புகள் குறித்து SIS ஒருபோதும் என்ன செய்யக்கூடாது?",
     "land_rules.txt", "never approve a sub-division whose extents do not sum to the parent survey", "area_rule"),
    ("To how many decimal places is area recorded?", "பரப்பளவு எத்தனை தசம இடங்கள் வரை பதிவு செய்யப்படுகிறது?",
     "survey_manual.txt", "2 decimal places", "area_rule"),
    ("What is the minimum measurable urban parcel?", "குறைந்தபட்ச அளவிடக்கூடிய நகர்ப்புற நிலம் எவ்வளவு?",
     "land_rules.txt", "1 sq.m", "area_rule"),

    # ── Sub-division numbering convention ────────────────────────────
    ("How are first, second and third level sub-divisions numbered?", "முதல், இரண்டாம், மூன்றாம் நிலை உட்பிரிவுகள் எப்படி எண்ணப்படுகின்றன?",
     "survey_manual.txt", "L1 numbers (145/1), L2 letters (145/1A), L3 numbers again (145/1A1)", "numbering"),
    ("What is the maximum sub-division depth?", "அதிகபட்ச உட்பிரிவு ஆழம் என்ன?",
     "survey_manual.txt", "3 levels (discouraged beyond this)", "numbering"),
    ("Can a survey number be duplicated within a block?", "ஒரு தொகுதிக்குள் ஒரு சர்வே எண்ணை நகலெடுக்க முடியுமா?",
     "survey_manual.txt", "no - survey numbers are unique within a block", "numbering"),
    ("What happens beyond 3 levels of sub-division?", "3 நிலைகளுக்கு மேல் உட்பிரிவு செய்தால் என்ன ஆகும்?",
     "land_rules.txt", "the officer records a remark and refers to DIS", "numbering"),

    # ── Encroachment ────────────────────────────────────────────────
    ("What are the types of encroachment?", "ஆக்கிரமிப்பு வகைகள் என்ன?",
     "survey_manual.txt", "boundary, full, partial, public-space encroachment", "encroachment"),
    ("What are the steps in encroachment detection?", "ஆக்கிரமிப்பு கண்டறிதலின் படிகள் என்ன?",
     "survey_manual.txt", "document review, physical verification, boundary-stone check, neighbour verification, documentation, report", "encroachment"),
    ("What does an encroachment flag do to an application?", "ஆக்கிரமிப்பு கொடி விண்ணப்பத்திற்கு என்ன செய்கிறது?",
     "land_rules.txt", "blocks automatic approval, visible to SD, extends timeline by 15-30 days", "encroachment"),
    ("How is private-on-public encroachment treated?", "தனியார்-பொது ஆக்கிரமிப்பு எப்படி நடத்தப்படுகிறது?",
     "land_rules.txt", "reported to the local body; not approved until removed or regularised", "encroachment"),
    ("Is GPS mandatory during an ISD field visit?", "ISD கள ஆய்வின் போது GPS கட்டாயமா?",
     "land_rules.txt", "yes - GPS coordinates mandatory for every corner, datum WGS-84", "encroachment"),

    # ── Litigation ─────────────────────────────────────────────────
    ("What status does an application get when litigation is detected?", "வழக்கு கண்டறியப்பட்டால் விண்ணப்பம் என்ன நிலையைப் பெறுகிறது?",
     "survey_manual.txt", "'On Hold - Litigation'", "litigation"),
    ("Does the SLA clock run during a litigation hold?", "வழக்கு நிறுத்தத்தின் போது SLA கடிகாரம் இயங்குமா?",
     "land_rules.txt", "no - no SLA/timeline clock runs during a litigation hold", "litigation"),
    ("How can an application resume after a litigation hold?", "வழக்கு நிறுத்தத்திற்குப் பிறகு விண்ணப்பம் எப்படி மீண்டும் தொடங்கும்?",
     "survey_manual.txt", "stay vacated, judgment in applicant's favour, or registered settlement; fresh field visit may be needed", "litigation"),
    ("What litigation details must be recorded?", "என்ன வழக்கு விவரங்கள் பதிவு செய்யப்பட வேண்டும்?",
     "survey_manual.txt", "court name, case number, case type, parties, whether a stay order is active", "litigation"),

    # ── Merge eligibility ─────────────────────────────────────────
    ("What are the merge eligibility criteria?", "இணைப்பு தகுதி அளவுகோல்கள் என்ன?",
     "survey_manual.txt", "common ownership, geographic adjacency, same block/ward/town, same land classification, no encumbrance, clear title", "merge_rules"),
    ("Can surveys from different blocks be merged?", "வெவ்வேறு தொகுதிகளிலிருந்து சர்வேக்களை இணைக்க முடியுமா?",
     "survey_manual.txt", "no", "merge_rules"),
    ("Can surveys with pending litigation be merged?", "நிலுவையில் உள்ள வழக்குடன் சர்வேக்களை இணைக்க முடியுமா?",
     "survey_manual.txt", "no", "merge_rules"),
    ("How is the merged survey area calculated?", "இணைக்கப்பட்ட சர்வே பரப்பளவு எப்படி கணக்கிடப்படுகிறது?",
     "survey_manual.txt", "sum of all individual survey areas", "merge_rules"),
    ("Can two sub-divisions with different parent survey numbers be merged?", "வெவ்வேறு தாய் சர்வே எண்களைக் கொண்ட இரண்டு உட்பிரிவுகளை இணைக்க முடியுமா?",
     "survey_manual.txt", "no - first merge each back to its parent, then merge parents", "merge_rules"),

    # ── Required documents ───────────────────────────────────────
    ("What documents are required for an ISD application?", "ISD விண்ணப்பத்திற்கு என்ன ஆவணங்கள் தேவை?",
     "workflow_guide.txt", "Sale Deed, EC, Survey Sketch, Photo ID, Photographs", "documents"),
    ("What documents are required for an NISD application?", "NISD விண்ணப்பத்திற்கு என்ன ஆவணங்கள் தேவை?",
     "workflow_guide.txt", "Sale Deed, EC, Photo ID, Patta Copy", "documents"),
    ("How many years of clear title must the EC show?", "EC எத்தனை ஆண்டுகள் தெளிவான உரிமையைக் காட்ட வேண்டும்?",
     "workflow_guide.txt", "13 years", "documents"),
    ("For how long is an EC valid from its issue date?", "வழங்கிய தேதியிலிருந்து EC எவ்வளவு காலம் செல்லுபடியாகும்?",
     "workflow_guide.txt", "3 months", "documents"),
    ("How many property photographs are the minimum for ISD?", "ISD-க்கு குறைந்தபட்சம் எத்தனை சொத்து புகைப்படங்கள்?",
     "workflow_guide.txt", "minimum 4 photographs from different angles", "documents"),

    # ── Rejection & resubmission ─────────────────────────────────
    ("What are the common rejection reasons?", "பொதுவான நிராகரிப்பு காரணங்கள் என்ன?",
     "workflow_guide.txt", "incomplete documents, area mismatch, encroachment, litigation flag, revenue arrears, invalid survey number", "rejection"),
    ("How many days does an applicant have to resubmit after rejection?", "நிராகரிப்புக்குப் பிறகு மீண்டும் சமர்ப்பிக்க விண்ணப்பதாரருக்கு எத்தனை நாட்கள் உள்ளன?",
     "workflow_guide.txt", "30 days", "rejection"),
    ("How many resubmissions are allowed?", "எத்தனை மறுசமர்ப்பிப்புகள் அனுமதிக்கப்படுகின்றன?",
     "workflow_guide.txt", "maximum 3; after that a fresh application is required", "rejection"),
    ("What suffix does a resubmitted application number get?", "மறுசமர்ப்பித்த விண்ணப்ப எண் என்ன பின்னொட்டைப் பெறுகிறது?",
     "workflow_guide.txt", "-R1 (e.g. ISD/01/2024/001-R1)", "rejection"),
    ("What happens if an applicant does not resubmit within 30 days?", "விண்ணப்பதாரர் 30 நாட்களுக்குள் மீண்டும் சமர்ப்பிக்கவில்லை என்றால் என்ன ஆகும்?",
     "workflow_guide.txt", "the application is auto-closed", "rejection"),

    # ── Submission channels ─────────────────────────────────────
    ("How is the submission channel of an application decided?", "விண்ணப்பத்தின் சமர்ப்பிப்பு சேனல் எப்படி முடிவு செய்யப்படுகிறது?",
     "land_rules.txt", "from source_name + camp_flag only: '-' = sub_registrar; operator code + camp_flag 'P' = citizen; operator code + other = CSC", "channel"),
    ("Does the CAN number length tell me the submission channel?", "CAN எண் நீளம் சமர்ப்பிப்பு சேனலைச் சொல்லுமா?",
     "land_rules.txt", "no - length names the issuing counter (15 = CSC/133 series, 12 = TN portal), not the channel", "channel"),
    ("Should source_code be used to answer a channel question?", "சேனல் கேள்விக்கு பதிலளிக்க source_code பயன்படுத்த வேண்டுமா?",
     "land_rules.txt", "no - source_code (0/00/1/2/3) carries no channel meaning", "channel"),
    ("What does camp_flag 'P' mean?", "camp_flag 'P' என்றால் என்ன?",
     "land_rules.txt", "keyed in at a special revenue camp - a citizen submission", "channel"),
    ("Which channel carries an IGRS Form 6 number?", "எந்த சேனல் IGRS படிவம் 6 எண்ணைக் கொண்டுள்ளது?",
     "land_rules.txt", "only a Sub-Registrar (SRO) referral", "channel"),

    # ── IGRS / SRO ─────────────────────────────────────────────
    ("What is SRO?", "SRO என்றால் என்ன?",
     "land_rules.txt", "Sub-Registrar Office - where a sale deed is registered", "igrs"),
    ("What does an absent IGRS Form 6 number mean?", "IGRS படிவம் 6 எண் இல்லாதது என்ன அர்த்தம்?",
     "land_rules.txt", "the file did not come from the SRO; it was CSC or a revenue camp - not a gap in the record", "igrs"),
    ("What is a CAN number?", "CAN எண் என்றால் என்ன?",
     "land_rules.txt", "Citizen Access Number - the citizen's unique identity number quoted on an application", "igrs"),
    ("When an IGRS Form 6 number is present, what number equals it?", "IGRS படிவம் 6 எண் இருக்கும்போது, எந்த எண் அதற்கு சமம்?",
     "land_rules.txt", "the application's CAN number", "igrs"),
    ("What does igrs_auto_mutation_flag = Y mean?", "igrs_auto_mutation_flag = Y என்றால் என்ன?",
     "land_rules.txt", "mutation is expected to post automatically after registration; SIS still verifies extent and boundaries", "igrs"),

    # ── Service codes ─────────────────────────────────────────
    ("What is service code 0153?", "சேவை குறியீடு 0153 என்றால் என்ன?",
     "tamilnilam_urban_services_and_districts.txt", "NISD - Not Involving Sub-Division (direct patta transfer verification)", "service_codes"),
    ("What is service code 0154?", "சேவை குறியீடு 0154 என்றால் என்ன?",
     "tamilnilam_urban_services_and_districts.txt", "ISD - Involving Sub-Division (mandatory field inspection + sub-division sketch)", "service_codes"),
    ("What is service code 0155?", "சேவை குறியீடு 0155 என்றால் என்ன?",
     "tamilnilam_urban_services_and_districts.txt", "Merge Subdivisions", "service_codes"),
    ("What is service code 0188?", "சேவை குறியீடு 0188 என்றால் என்ன?",
     "tamilnilam_urban_services_and_districts.txt", "Natham Settlement", "service_codes"),
    ("What service code is F-Line urban demarcation?", "F-Line நகர்ப்புற எல்லை வரையறை எந்த சேவை குறியீடு?",
     "tamilnilam_urban_services_and_districts.txt", "0178", "service_codes"),
    ("What is the urban application number format?", "நகர்ப்புற விண்ணப்ப எண் வடிவம் என்ன?",
     "tamilnilam_urban_services_and_districts.txt", "YYYY / URBAN_SERVICE_CODE / DISTRICT_CODE / SEQUENCE", "service_codes"),

    # ── District codes (contradiction was fixed here) ─────────
    ("Which district is code 28?", "குறியீடு 28 எந்த மாவட்டம்?",
     "district_codes.txt", "Thoothukudi", "district_codes"),
    ("Which district is code 34?", "குறியீடு 34 எந்த மாவட்டம்?",
     "district_codes.txt", "Chengalpattu", "district_codes"),
    ("Which district is code 35?", "குறியீடு 35 எந்த மாவட்டம்?",
     "district_codes.txt", "Ranipet", "district_codes"),
    ("Which district is code 37?", "குறியீடு 37 எந்த மாவட்டம்?",
     "district_codes.txt", "Tenkasi", "district_codes"),
    ("Which district is code 02?", "குறியீடு 02 எந்த மாவட்டம்?",
     "district_codes.txt", "Chennai", "district_codes"),
    ("What district code does application 2026/0154/02/000001 belong to?", "விண்ணப்பம் 2026/0154/02/000001 எந்த மாவட்ட குறியீட்டைச் சேர்ந்தது?",
     "tamilnilam_urban_services_and_districts.txt", "02 - Chennai", "district_codes"),

    # ── Land type classification ─────────────────────────────
    ("What are the urban land type classification codes?", "நகர்ப்புற நில வகை வகைப்பாடு குறியீடுகள் என்ன?",
     "land_rules.txt", "1 residential, 2 commercial, 3 industrial, 4 agricultural, 5 govt poramboke, 6 institutional, 7 vacant", "land_type"),
    ("Can poramboke (type 5) land be sub-divided or transferred to a private owner?", "பொறம்போக்கு (வகை 5) நிலத்தை உட்பிரிக்க அல்லது தனியாருக்கு மாற்ற முடியுமா?",
     "land_rules.txt", "no - only through a settlement service code (0179/0183) with the sanctioning GO quoted", "land_type"),
    ("What is needed before a sub-division that changes land type?", "நில வகையை மாற்றும் உட்பிரிவுக்கு முன் என்ன தேவை?",
     "land_rules.txt", "reclassification before the ISD sketch is finalised", "land_type"),
    ("What is needed to transfer institutional (type 6) land?", "நிறுவன (வகை 6) நிலத்தை மாற்ற என்ன தேவை?",
     "land_rules.txt", "endorsement of the controlling authority (HR&CE, Wakf Board, Diocese, or trust) on record", "land_type"),

    # ── Legal basis ────────────────────────────────────────
    ("Which Act governs patta issue and mutation?", "பட்டா வழங்கல் மற்றும் மாற்றத்தை எந்த சட்டம் ஆளுகிறது?",
     "land_rules.txt", "Tamil Nadu Patta Pass Book Act, 1983 and Rules 1985", "legal"),
    ("Which Act governs survey demarcation and boundary marks?", "சர்வே எல்லை வரையறை மற்றும் எல்லை குறிகளை எந்த சட்டம் ஆளுகிறது?",
     "land_rules.txt", "Tamil Nadu Survey and Boundaries Act, 1923", "legal"),
    ("What does the TSLR Manual cover?", "TSLR கையேடு எதை உள்ளடக்கியது?",
     "land_rules.txt", "urban survey number structure, sub-division numbering, TSLR extract and sketch", "legal"),
    ("What are the things the SIS must NOT do?", "SIS செய்யக்கூடாதவை என்ன?",
     "land_rules.txt", "change a visit date without Tahsildar approval, approve extents that don't sum, transfer poramboke/institutional land without order, proceed past a stay, accept an untraceable sale deed", "legal"),

    # ── Revenue arrears ───────────────────────────────────
    ("Who confirms revenue arrears status before approval?", "அங்கீகாரத்திற்கு முன் வருவாய் நிலுவை நிலையை யார் உறுதிப்படுத்துகிறார்?",
     "land_rules.txt", "the Zonal Level Tahsildar (ZDT) during review", "arrears"),
    ("Are unpaid property dues a rejection reason?", "செலுத்தப்படாத சொத்து நிலுவைகள் நிராகரிப்புக் காரணமா?",
     "land_rules.txt", "yes - a documented rejection reason", "arrears"),

    # ── Upload checklist ─────────────────────────────────
    ("How should I name a file before uploading it to the SIS knowledge folder?", "SIS அறிவு கோப்புறையில் பதிவேற்றும் முன் ஒரு கோப்பை எப்படி பெயரிட வேண்டும்?",
     "sis_upload_checklist.txt", "application number, document type and date, e.g. 2025_0154_28_000012_field_note_2025-06-18.pdf", "upload"),
    ("What should a scanned PDF have so the chatbot can retrieve it?", "சாட்பாட் மீட்டெடுக்க ஸ்கேன் செய்யப்பட்ட PDF-ல் என்ன இருக்க வேண்டும்?",
     "sis_upload_checklist.txt", "OCR text", "upload"),
    ("What must a useful field-visit report record?", "பயனுள்ள கள ஆய்வு அறிக்கை என்ன பதிவு செய்ய வேண்டும்?",
     "sis_upload_checklist.txt", "officer name & employee ID, application/survey reference, observed extent, boundary points, GPS datum, documents seen, encroachment/litigation observations, photos, a clear recommendation", "upload"),
    ("When should a document be marked 'verified'?", "ஒரு ஆவணத்தை எப்போது 'verified' என குறிக்க வேண்டும்?",
     "sis_upload_checklist.txt", "only after the officer has checked the original documents", "upload"),

    # ── TRAP: not in the corpus ─────────────────────────
    ("What is the FMB book and page number for this workflow?", "இந்த பணிப்பாய்வுக்கான FMB புத்தகம் மற்றும் பக்க எண் என்ன?",
     "(none)", "TRAP - there is no FMB anywhere in this data; the assistant must say so, not invent one", "trap"),
    ("Where is the Field Measurement Book sketch stored?", "கள அளவீட்டு புத்தக வரைபடம் எங்கே சேமிக்கப்படுகிறது?",
     "(none)", "TRAP - FMB is not part of this department's data or workflow", "trap"),
    ("What is the applicant's e-mail address on this application?", "இந்த விண்ணப்பத்தில் விண்ணப்பதாரரின் மின்னஞ்சல் முகவரி என்ன?",
     "(none)", "TRAP - no e-mail address is stored for applicants, only a mobile number", "trap"),
    ("Show me an application that is currently in the 'escalated' status", "தற்போது 'escalated' நிலையில் உள்ள ஒரு விண்ணப்பத்தைக் காட்டு",
     "(none)", "TRAP - the seed data has no escalated application; escalated is a valid status but unused", "trap"),
    ("Which SIS officer signs the patta transfer order with the DSC?", "எந்த SIS அலுவலர் DSC உடன் பட்டா மாற்ற ஆணையில் கையொப்பமிடுகிறார்?",
     "workflow_guide.txt", "TRAP - the SIS does NOT sign; the Tahsildar holds the DSC and signs", "trap"),
    ("How many days does the SIS have to approve an ISD application after the field visit?", "கள ஆய்விற்குப் பிறகு ISD விண்ணப்பத்தை அங்கீகரிக்க SIS-க்கு எத்தனை நாட்கள் உள்ளன?",
     "workflow_guide.txt", "TRAP - the SIS does not approve ISD; approval is DIS (5 days) then Tahsildar DSC (3 days)", "trap"),
]

# ── Follow-up reference chains over corpus topics ─────────────────────
FOLLOWUP_CHAINS = [
    ("What are the steps in the ISD workflow?", "ISD பணிப்பாய்வின் படிகள் என்ன?", "workflow_guide.txt", [
        ("Who does the second step?", "இரண்டாவது படியை யார் செய்கிறார்?", "the SIS field visit / then SD prepares the sketch"),
        ("How long does that step take?", "அந்த படி எவ்வளவு நேரம் எடுக்கும்?", "SD handoff 7-10 working days"),
        ("And the step after it?", "அதற்குப் பிறகு உள்ள படி?", "DIS review, 5 working days"),
        ("Who signs at the end?", "இறுதியில் யார் கையொப்பமிடுகிறார்?", "the Tahsildar, with the DSC"),
    ]),
    ("Within how many working days must a field visit be scheduled?", "எத்தனை வேலை நாட்களுக்குள் கள ஆய்வு திட்டமிடப்பட வேண்டும்?", "workflow_guide.txt", [
        ("What if it slips past that?", "அது அதைத் தாண்டிச் சென்றால் என்ன?", "marked overdue"),
        ("Who can move the date?", "தேதியை யார் மாற்ற முடியும்?", "only the Tahsildar"),
        ("Does NISD have the same deadline?", "NISD-க்கு அதே காலக்கெடு உள்ளதா?", "no - NISD normally needs no field visit"),
    ]),
    ("What is a temporary subdivision number?", "தற்காலிக உட்பிரிவு எண் என்றால் என்ன?", "faq_english.txt", [
        ("Who assigns it?", "அதை யார் ஒதுக்குகிறார்?", "the DIS"),
        ("When does it become final?", "அது எப்போது இறுதியாகிறது?", "on DIS approval"),
        ("What about a rejected file?", "நிராகரிக்கப்பட்ட கோப்பு பற்றி என்ன?", "keeps the temporary number, never gets a final one"),
    ]),
    ("What does an absent IGRS Form 6 number mean?", "IGRS படிவம் 6 எண் இல்லாதது என்ன அர்த்தம்?", "land_rules.txt", [
        ("So is that a gap in the record?", "அது பதிவில் ஒரு இடைவெளியா?", "no - it is the rule for CSC and camp files"),
        ("Which channel always has one?", "எந்த சேனலுக்கு எப்போதும் ஒன்று உள்ளது?", "a Sub-Registrar referral"),
        ("Does a 12-digit CAN mean citizen then?", "12-இலக்க CAN என்றால் குடிமகனா?", "no - length names the counter, not the channel"),
    ]),
]


def build_bank() -> list[Question]:
    out: list[Question] = []
    for en, ta, src, exp, topic in ITEMS:
        kind = "trap" if topic == "trap" else "single"
        out.append(Question(text_en=en, text_ta=ta, source=src, topic=topic,
                            expect=exp, kind=kind))
    for si, (sen, sta, src, turns) in enumerate(FOLLOWUP_CHAINS):
        out.append(Question(text_en=sen, text_ta=sta, source=src,
                            topic=f"followup_{si}", expect="(seed turn)",
                            kind="followup"))
        for (fen, fta, exp) in turns:
            out.append(Question(text_en=fen, text_ta=fta, source=src,
                                topic=f"followup_{si}", expect=exp,
                                kind="followup", follow_of=sen))
    return out


def render_txt() -> str:
    bank = build_bank()
    W = "=" * 72
    L = [W, f"SIS CHATBOT - WORKFLOW QUESTIONS FROM backend/documents/ ({len(bank)})", W,
         "Generated by backend/sample_db/workflow_doc_question_bank.py", "",
         "Each item: English line, Tamil/Tanglish line, then [source] expected answer.",
         "TRAP items name something the corpus does NOT contain - the assistant",
         "must say so, never invent it.",
         "FOLLOW-UP turns depend on the nearest preceding seed; ask them in order.", ""]
    by_topic: dict[str, list[Question]] = {}
    for q in bank:
        by_topic.setdefault(q.topic, []).append(q)
    n = 0
    for topic, qs in by_topic.items():
        L += ["", W, f"TOPIC: {topic}  ({len(qs)})", W]
        for q in qs:
            n += 1
            tag = "  [TRAP]" if q.kind == "trap" else ""
            if q.kind == "followup" and q.follow_of:
                L.append(f"{n}. (follow-up of {q.follow_of!r}) {q.text_en}{tag}")
            elif q.kind == "followup":
                L.append(f"{n}. SEED >> {q.text_en}{tag}")
            else:
                L.append(f"{n}. {q.text_en}{tag}")
            if q.text_ta:
                L.append(f"   {q.text_ta}")
            L.append(f"   [{q.source}] {q.expect}")
        L.append("")
    L += [W, "TOPIC COVERAGE", W]
    for topic, qs in sorted(by_topic.items()):
        L.append(f"  {topic:<20} {len(qs):>3}")
    L.append(f"\n  TOTAL {len(bank)} questions "
             f"({sum(1 for q in bank if q.kind == 'trap')} traps, "
             f"{sum(1 for q in bank if q.kind == 'followup' and q.follow_of)} follow-up turns)")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    import sys
    if "--txt" in sys.argv:
        sys.stdout.write(render_txt())
    else:
        b = build_bank()
        from collections import Counter
        print(f"{len(b)} questions;", dict(Counter(q.kind for q in b)))
        srcs = Counter(q.source for q in b)
        for s, c in srcs.most_common():
            print(f"  {s:<45} {c}")
