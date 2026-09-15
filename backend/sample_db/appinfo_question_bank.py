"""
Question bank for the NISD / ISD application-info extract columns
(nisd_transfer_application_info / isd_transfer_application_info, and the
overlapping urban_application_log columns).

`build_app_tables.py` projects a subset of these into the ORM `applications` /
`applicants` rows; the rest stay in layer 1 only and the chatbot has no data
for them -- for those the correct answer is a deterministic "not held in your
register", never an LLM guess.

`build_bank()` returns Question records; `--txt` emits the flat numbered form.
Nothing here touches the DB or the LLM.

Columns covered:
  application_id, district_code, taluk_code, town_code, ward_code, block_code,
  can_number, applicant_name, current_address, mobile_number,
  application_status, remarks, last_updated_datetime, permanent_address,
  mother_name, father_name, date_of_birth, gender, occupation,
  enclosure_details, barcode_flag, first_page_document, reverse_page_document,
  last_page_document, enclosure_certificate, proposed_field_visit_date,
  proposed_remarks, missing_documents, physical_verification_status,
  document_sent_date, document_received_date, challan_number, challan_date,
  treasury_name, bank_name, bank_branch, payment_mode, payment_amount,
  igrs_form6_number, return_status, owner_correction_reason,
  merged_application_id, auto_mutated_flag, application_status_code,
  rejection_reason_code, sro_code, relative_mobile_number
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product

APPS = [
    "2022/0153/28/000254", "2022/0154/28/000156", "2026/0154/28/001280",
    "2026/0153/28/001190", "2026/0153/28/001876", "2025/0154/28/000286",
    "2026/0155/28/000010", "2023/0153/28/000571", "2024/0154/28/002252",
    "2026/0154/28/001197",
]


@dataclass(frozen=True)
class Question:
    text_en: str
    column: str
    kind: str           # single | combo | followup | rule
    text_ta: str = ""
    handled_by: str = ""   # field_map | intent | not_projected
    follow_of: str = ""


# handled_by: how the chatbot is expected to answer it
#   field_map      -> a deterministic field lookup returns the value
#   intent         -> a dedicated intent handler answers it
#   not_projected  -> deterministic "not held in your SIS register" (no LLM)
FIELDS = {
    "application_id":            ("field_map", [
        ("What is the application number for {a}?", "{a} விண்ணப்ப எண் என்ன?"),
        ("Confirm application {a} exists in my ward", "{a} என் வார்டில் உள்ளதா என உறுதிப்படுத்து")]),
    "district_code":             ("field_map", [
        ("What is the district code on {a}?", "{a} மாவட்ட குறியீடு என்ன?"),
        ("Which district code does {a} carry?", "{a} எந்த மாவட்ட குறியீட்டைக் கொண்டுள்ளது?")]),
    "taluk_code":                ("field_map", [
        ("What is the taluk code on {a}?", "{a} தாலுகா குறியீடு என்ன?"),
        ("taluk code {a}", "{a} taluk code enna")]),
    "town_code":                 ("field_map", [
        ("What is the town code (urban unit code) for {a}?", "{a}-க்கான நகர குறியீடு (நகர்ப்புற அலகு குறியீடு) என்ன?"),
        ("town code {a}", "{a} town code enna"),
        ("Which urban unit code is {a} under?", "{a} எந்த நகர்ப்புற அலகு குறியீட்டில் உள்ளது?")]),
    "ward_code":                 ("field_map", [
        ("What is the ward code on {a}?", "{a} வார்டு குறியீடு என்ன?"),
        ("Which ward is {a} in?", "{a} எந்த வார்டில் உள்ளது?")]),
    "block_code":                ("field_map", [
        ("What is the block code on {a}?", "{a} தொகுதி குறியீடு என்ன?"),
        ("Which block does {a} belong to?", "{a} எந்த தொகுதியைச் சேர்ந்தது?")]),
    "can_number":                ("field_map", [
        ("What is the CAN number on {a}?", "{a} CAN எண் என்ன?"),
        ("Give me just the CAN number for {a}", "{a}-க்கான CAN எண்ணை மட்டும் தரவும்"),
        ("can number {a}", "{a} can number enna")]),
    "applicant_name":            ("field_map", [
        ("Who is the applicant on {a}?", "{a}-ல் விண்ணப்பதாரர் யார்?"),
        ("What is the applicant name for {a}?", "{a} விண்ணப்பதாரர் பெயர் என்ன?"),
        ("{a} peyar yaaru", "{a} விண்ணப்பதாரர் பெயர்")]),
    "current_address":           ("field_map", [
        ("What is the current address of the applicant on {a}?", "{a} விண்ணப்பதாரரின் தற்போதைய முகவரி என்ன?"),
        ("Show the address for {a}", "{a}-க்கான முகவரியைக் காட்டு")]),
    "mobile_number":             ("field_map", [
        ("What is the mobile number on {a}?", "{a} கைபேசி எண் என்ன?"),
        ("Give the applicant's phone number for {a}", "{a} விண்ணப்பதாரரின் தொலைபேசி எண்ணைத் தரவும்"),
        ("mobile number {a}", "{a} mobile number enna")]),
    "application_status":        ("field_map", [
        ("What is the status of {a}?", "{a}-ன் நிலை என்ன?"),
        ("Is {a} approved, rejected or still open?", "{a} அங்கீகரிக்கப்பட்டதா, நிராகரிக்கப்பட்டதா, திறந்துள்ளதா?"),
        ("{a} nilai enna", "{a} நிலை என்ன")]),
    "last_updated_datetime":     ("field_map", [
        ("When was {a} last updated?", "{a} கடைசியாக எப்போது புதுப்பிக்கப்பட்டது?"),
        ("last update on {a}", "{a} கடைசி புதுப்பிப்பு")]),
    "permanent_address":         ("field_map", [
        ("What is the permanent address of the applicant on {a}?", "{a} விண்ணப்பதாரரின் நிரந்தர முகவரி என்ன?"),
        ("permanent address {a}", "{a} nirandara mugavari")]),
    "mother_name":               ("field_map", [
        ("What is the mother's name of the applicant on {a}?", "{a} விண்ணப்பதாரரின் தாய் பெயர் என்ன?"),
        ("mother name {a}", "{a} thaai peyar")]),
    "father_name":               ("field_map", [
        ("What is the father's name of the applicant on {a}?", "{a} விண்ணப்பதாரரின் தந்தை பெயர் என்ன?"),
        ("father name {a}", "{a} thandhai peyar")]),
    "date_of_birth":             ("field_map", [
        ("What is the date of birth of the applicant on {a}?", "{a} விண்ணப்பதாரரின் பிறந்த தேதி என்ன?"),
        ("dob {a}", "{a} pirandha thedhi")]),
    "gender":                    ("field_map", [
        ("What is the gender of the applicant on {a}?", "{a} விண்ணப்பதாரரின் பாலினம் என்ன?"),
        ("gender {a}", "{a} paalinam enna")]),
    "occupation":                ("field_map", [
        ("What is the occupation of the applicant on {a}?", "{a} விண்ணப்பதாரரின் தொழில் என்ன?"),
        ("What does the applicant on {a} do for a living?", "{a} விண்ணப்பதாரர் என்ன வேலை செய்கிறார்?"),
        ("occupation {a}", "{a} thozhil enna")]),
    "challan_number":            ("field_map", [
        ("What is the challan number on {a}?", "{a} சலான் எண் என்ன?"),
        ("challan number {a}", "{a} challan number enna")]),
    "payment_mode":              ("field_map", [
        ("What was the payment mode on {a}?", "{a} கட்டண முறை என்ன?"),
        ("How was the fee paid for {a}?", "{a}-க்கான கட்டணம் எப்படி செலுத்தப்பட்டது?")]),
    "payment_amount":            ("field_map", [
        ("What is the payment amount on {a}?", "{a} செலுத்திய தொகை என்ன?"),
        ("How much was the fee for {a}?", "{a}-க்கான கட்டணம் எவ்வளவு?"),
        ("payment amount {a}", "{a} kattanam evvalavu")]),
    "igrs_form6_number":         ("field_map", [
        ("What is the IGRS Form 6 number on {a}?", "{a} IGRS படிவம் 6 எண் என்ன?"),
        ("Does {a} have an IGRS number?", "{a}-க்கு IGRS எண் உள்ளதா?"),
        ("igrs {a}", "{a} igrs number enna")]),
    "merged_application_id":     ("field_map", [
        ("What is the merged application id on {a}?", "{a}-ன் இணைக்கப்பட்ட விண்ணப்ப எண் என்ன?"),
        ("Which application is {a} merged with?", "{a} எந்த விண்ணப்பத்துடன் இணைக்கப்பட்டது?")]),
    "proposed_field_visit_date": ("field_map", [
        ("What is the proposed field visit date for {a}?", "{a}-க்கான முன்மொழியப்பட்ட கள ஆய்வு தேதி என்ன?"),
        ("When is the field inspection proposed for {a}?", "{a}-க்கான கள சோதனை எப்போது முன்மொழியப்பட்டுள்ளது?")]),

    # ── intent-handled ────────────────────────────────────────────────
    "missing_documents":         ("intent", [
        ("What documents are missing on {a}?", "{a}-ல் என்ன ஆவணங்கள் விடுபட்டுள்ளன?"),
        ("Does {a} have all required documents?", "{a}-க்கு தேவையான அனைத்து ஆவணங்களும் உள்ளதா?"),
        ("List the missing documents for {a}", "{a}-க்கான விடுபட்ட ஆவணங்களைப் பட்டியலிடு")]),
    "remarks":                   ("intent", [
        ("What are the remarks recorded on {a}?", "{a}-ல் பதிவு செய்யப்பட்ட குறிப்புகள் என்ன?"),
        ("Show the workflow remarks for {a}", "{a}-க்கான பணிப்பாய்வு குறிப்புகளைக் காட்டு")]),

    # ── NOT projected -> deterministic "not held" ─────────────────────
    "enclosure_details":         ("not_projected", [
        ("What are the enclosure details on {a}?", "{a}-ன் இணைப்பு விவரங்கள் என்ன?"),
        ("List the enclosures attached to {a}", "{a}-உடன் இணைக்கப்பட்ட இணைப்புகளைப் பட்டியலிடு")]),
    "enclosure_certificate":     ("not_projected", [
        ("Is there an enclosure certificate on {a}?", "{a}-ல் இணைப்பு சான்றிதழ் உள்ளதா?"),
        ("Show the enclosure certificate for {a}", "{a}-க்கான இணைப்பு சான்றிதழைக் காட்டு")]),
    "barcode_flag":              ("not_projected", [
        ("What is the barcode flag on {a}?", "{a}-ன் பார்கோடு கொடி என்ன?"),
        ("Is the barcode flag set on {a}?", "{a}-ல் பார்கோடு கொடி அமைக்கப்பட்டுள்ளதா?")]),
    "first_page_document":       ("not_projected", [
        ("Show the first page document of {a}", "{a}-ன் முதல் பக்க ஆவணத்தைக் காட்டு"),
        ("Do you have the scanned first page for {a}?", "{a}-க்கான ஸ்கேன் செய்யப்பட்ட முதல் பக்கம் உள்ளதா?")]),
    "reverse_page_document":     ("not_projected", [
        ("Show the reverse page document of {a}", "{a}-ன் பின்பக்க ஆவணத்தைக் காட்டு")]),
    "last_page_document":        ("not_projected", [
        ("Show the last page document of {a}", "{a}-ன் கடைசி பக்க ஆவணத்தைக் காட்டு"),
        ("Give me the scan copy of {a}", "{a}-ன் ஸ்கேன் நகலைத் தரவும்")]),
    "proposed_remarks":          ("not_projected", [
        ("What are the proposed remarks on {a}?", "{a}-ன் முன்மொழியப்பட்ட குறிப்புகள் என்ன?"),
        ("Show the proposal remarks for {a}", "{a}-க்கான முன்மொழிவு குறிப்புகளைக் காட்டு")]),
    "physical_verification_status": ("not_projected", [
        ("What is the physical verification status of {a}?", "{a}-ன் நேரடி சரிபார்ப்பு நிலை என்ன?"),
        ("Has {a} been physically verified?", "{a} நேரில் சரிபார்க்கப்பட்டதா?")]),
    "document_sent_date":        ("not_projected", [
        ("When were the documents sent for {a}?", "{a}-க்கான ஆவணங்கள் எப்போது அனுப்பப்பட்டன?"),
        ("What is the document sent date on {a}?", "{a}-ன் ஆவணம் அனுப்பிய தேதி என்ன?"),
        ("dispatch date {a}", "{a} dispatch date eppo")]),
    "document_received_date":    ("not_projected", [
        ("When were the documents received for {a}?", "{a}-க்கான ஆவணங்கள் எப்போது பெறப்பட்டன?"),
        ("What is the document received date on {a}?", "{a}-ன் ஆவணம் பெறப்பட்ட தேதி என்ன?")]),
    "challan_date":              ("not_projected", [
        ("What is the challan date on {a}?", "{a}-ன் சலான் தேதி என்ன?"),
        ("On what date was the challan for {a} paid?", "{a}-க்கான சலான் எந்த தேதியில் செலுத்தப்பட்டது?"),
        ("challan date {a}", "{a} challan date enna")]),
    "treasury_name":             ("not_projected", [
        ("Which treasury received the payment for {a}?", "{a}-க்கான கட்டணத்தை எந்த கருவூலம் பெற்றது?"),
        ("What is the treasury name on {a}?", "{a}-ன் கருவூலப் பெயர் என்ன?")]),
    "bank_name":                 ("not_projected", [
        ("Which bank was the fee for {a} paid through?", "{a}-க்கான கட்டணம் எந்த வங்கி வழியாக செலுத்தப்பட்டது?"),
        ("What is the bank name on {a}?", "{a}-ன் வங்கிப் பெயர் என்ன?")]),
    "bank_branch":               ("not_projected", [
        ("What is the bank branch on {a}?", "{a}-ன் வங்கி கிளை என்ன?"),
        ("Which branch processed the payment for {a}?", "{a}-க்கான கட்டணத்தை எந்த கிளை செயலாக்கியது?")]),
    "return_status":             ("not_projected", [
        ("What is the return status of {a}?", "{a}-ன் திரும்பிய நிலை என்ன?"),
        ("Was {a} returned to the applicant?", "{a} விண்ணப்பதாரருக்கு திருப்பி அனுப்பப்பட்டதா?")]),
    "owner_correction_reason":   ("not_projected", [
        ("What is the owner correction reason on {a}?", "{a}-ன் உரிமையாளர் திருத்த காரணம் என்ன?"),
        ("Why was an owner name correction filed on {a}?", "{a}-ல் உரிமையாளர் பெயர் திருத்தம் ஏன் தாக்கல் செய்யப்பட்டது?")]),
    "auto_mutated_flag":         ("not_projected", [
        ("Is the auto mutated flag set on {a}?", "{a}-ல் தானியங்கி மாற்ற கொடி அமைக்கப்பட்டுள்ளதா?"),
        ("What is the auto mutation flag on {a}?", "{a}-ன் தானியங்கி மாற்ற கொடி என்ன?")]),
    # The register stores the status WORD and the rejection reason TEXT; the
    # numeric codes and the appinfo relative-mobile column are not projected.
    "application_status_code":    ("not_projected", [
        ("What is the application status code on {a}?", "{a}-ன் விண்ணப்ப நிலை குறியீடு என்ன?"),
        ("Give me the numeric status code for {a}", "{a}-க்கான எண் நிலை குறியீட்டைத் தரவும்")]),
    "rejection_reason_code":      ("not_projected", [
        ("What is the rejection reason code on {a}?", "{a}-ன் நிராகரிப்பு காரண குறியீடு என்ன?"),
        ("Which reason code was recorded for {a}?", "{a}-க்கு எந்த காரண குறியீடு பதிவு செய்யப்பட்டது?")]),
    "sro_code":                   ("not_projected", [
        ("What is the SRO code on {a}?", "{a}-ன் SRO குறியீடு என்ன?"),
        ("Which Sub-Registrar Office code does {a} carry?", "{a} எந்த சார்பதிவாளர் அலுவலக குறியீட்டைக் கொண்டுள்ளது?")]),
    "relative_mobile_number":     ("not_projected", [
        ("What is the relative's mobile number on {a}?", "{a}-ன் உறவினர் கைபேசி எண் என்ன?"),
        ("Give the guardian's phone number for {a}", "{a}-க்கான பாதுகாவலரின் தொலைபேசி எண்ணைத் தரவும்")]),
}

COMBOS = [
    ("Show the applicant name, father's name and mother's name on {a}",
     "{a}-ல் விண்ணப்பதாரர் பெயர், தந்தை பெயர், தாய் பெயரைக் காட்டு",
     ["applicant_name", "father_name", "mother_name"]),
    ("What are the date of birth, gender and occupation of the applicant on {a}?",
     "{a} விண்ணப்பதாரரின் பிறந்த தேதி, பாலினம், தொழில் என்ன?",
     ["date_of_birth", "gender", "occupation"]),
    ("Give the district code, taluk code, town code, ward code and block code for {a}",
     "{a}-க்கான மாவட்ட, தாலுகா, நகர, வார்டு, தொகுதி குறியீடுகளைத் தரவும்",
     ["district_code", "taluk_code", "town_code", "ward_code", "block_code"]),
    ("Show the challan number, challan date, payment mode and payment amount on {a}",
     "{a}-ல் சலான் எண், சலான் தேதி, கட்டண முறை, செலுத்திய தொகையைக் காட்டு",
     ["challan_number", "challan_date", "payment_mode", "payment_amount"]),
    ("What is the treasury name, bank name and bank branch on {a}?",
     "{a}-ன் கருவூலப் பெயர், வங்கிப் பெயர், வங்கி கிளை என்ன?",
     ["treasury_name", "bank_name", "bank_branch"]),
    ("Show the current address and permanent address of the applicant on {a}",
     "{a} விண்ணப்பதாரரின் தற்போதைய முகவரி மற்றும் நிரந்தர முகவரியைக் காட்டு",
     ["current_address", "permanent_address"]),
    ("What is the CAN number and the IGRS Form 6 number on {a}?",
     "{a}-ன் CAN எண் மற்றும் IGRS படிவம் 6 எண் என்ன?",
     ["can_number", "igrs_form6_number"]),
    ("Show the document sent date, document received date and physical verification status on {a}",
     "{a}-ல் ஆவணம் அனுப்பிய தேதி, பெறப்பட்ட தேதி, நேரடி சரிபார்ப்பு நிலையைக் காட்டு",
     ["document_sent_date", "document_received_date", "physical_verification_status"]),
    ("What are the enclosure details and the barcode flag on {a}?",
     "{a}-ன் இணைப்பு விவரங்கள் மற்றும் பார்கோடு கொடி என்ன?",
     ["enclosure_details", "barcode_flag"]),
]

FOLLOWUP_CHAINS = [
    {
        "seed_en": "What is the status of {a}?",
        "seed_ta": "{a}-ன் நிலை என்ன?",
        "turns": [
            ("Who is the applicant?", "விண்ணப்பதாரர் யார்?", "applicant_name"),
            ("What is their mobile number?", "அவரது கைபேசி எண் என்ன?", "mobile_number"),
            ("And their occupation?", "அவரது தொழில்?", "occupation"),
            ("What is the permanent address?", "நிரந்தர முகவரி என்ன?", "permanent_address"),
            ("When was it last updated?", "அது கடைசியாக எப்போது புதுப்பிக்கப்பட்டது?", "last_updated_datetime"),
        ],
    },
    {
        "seed_en": "Show me application {a}",
        "seed_ta": "{a} விண்ணப்பத்தைக் காட்டு",
        "turns": [
            ("What is the challan number?", "சலான் எண் என்ன?", "challan_number"),
            ("What was the challan date?", "சலான் தேதி என்ன?", "challan_date"),
            ("Which bank?", "எந்த வங்கி?", "bank_name"),
            ("And which treasury?", "எந்த கருவூலம்?", "treasury_name"),
            ("How much was paid?", "எவ்வளவு செலுத்தப்பட்டது?", "payment_amount"),
        ],
    },
    {
        "seed_en": "What district and taluk is {a} in?",
        "seed_ta": "{a} எந்த மாவட்டம் மற்றும் தாலுகாவில் உள்ளது?",
        "turns": [
            ("What about the town code?", "நகர குறியீடு பற்றி என்ன?", "town_code"),
            ("And the ward code?", "வார்டு குறியீடு?", "ward_code"),
            ("And the block code?", "தொகுதி குறியீடு?", "block_code"),
        ],
    },
    {
        "seed_en": "Does {a} have all required documents?",
        "seed_ta": "{a}-க்கு தேவையான அனைத்து ஆவணங்களும் உள்ளதா?",
        "turns": [
            ("What is missing?", "எது விடுபட்டுள்ளது?", "missing_documents"),
            ("When were the documents received?", "ஆவணங்கள் எப்போது பெறப்பட்டன?", "document_received_date"),
            ("What is the enclosure certificate?", "இணைப்பு சான்றிதழ் என்ன?", "enclosure_certificate"),
            ("Has it been physically verified?", "அது நேரில் சரிபார்க்கப்பட்டதா?", "physical_verification_status"),
        ],
    },
    {
        "seed_en": "Show my pending applications",
        "seed_ta": "என் நிலுவையில் உள்ள விண்ணப்பங்களைக் காட்டு",
        "turns": [
            ("Which one was updated most recently?", "எது மிக சமீபத்தில் புதுப்பிக்கப்பட்டது?", "last_updated_datetime"),
            ("How many of them are still open?", "அவற்றில் எத்தனை இன்னும் திறந்துள்ளன?", "application_status"),
            ("Which of them have an IGRS number?", "அவற்றில் எவற்றுக்கு IGRS எண் உள்ளது?", "igrs_form6_number"),
            ("Do any of them have proposed remarks?", "அவற்றில் ஏதேனும் முன்மொழியப்பட்ட குறிப்புகள் உள்ளதா?", "proposed_remarks"),
        ],
    },
]

RULE_QUESTIONS = [
    ("Does your register store the applicant's occupation?", "உங்கள் பதிவேட்டில் விண்ணப்பதாரரின் தொழில் சேமிக்கப்படுகிறதா?", "occupation"),
    ("Do you keep the bank and treasury the challan was paid at?", "சலான் செலுத்திய வங்கி மற்றும் கருவூலத்தை நீங்கள் வைத்திருக்கிறீர்களா?", "bank_name"),
    ("Are the scanned application pages available to you?", "ஸ்கேன் செய்யப்பட்ட விண்ணப்பப் பக்கங்கள் உங்களிடம் உள்ளதா?", "first_page_document"),
    ("What does the auto mutated flag mean?", "தானியங்கி மாற்ற கொடி என்றால் என்ன?", "auto_mutated_flag"),
    ("What is the difference between challan date and challan number?", "சலான் தேதிக்கும் சலான் எண்ணுக்கும் என்ன வித்தியாசம்?", "challan_date"),
    ("What is a CAN number, and does its length tell me the channel?", "CAN எண் என்றால் என்ன, அதன் நீளம் சேனலைச் சொல்லுமா?", "can_number"),
    ("Which channel carries an IGRS Form 6 number?", "எந்த சேனல் IGRS படிவம் 6 எண்ணைக் கொண்டுள்ளது?", "igrs_form6_number"),
    ("What does an absent IGRS Form 6 number mean?", "IGRS படிவம் 6 எண் இல்லாதது என்ன அர்த்தம்?", "igrs_form6_number"),
    ("What is a return status on a transfer application?", "மாற்று விண்ணப்பத்தில் திரும்பிய நிலை என்றால் என்ன?", "return_status"),
    ("What is physical verification?", "நேரடி சரிபார்ப்பு என்றால் என்ன?", "physical_verification_status"),
]


def build_bank() -> list[Question]:
    out: list[Question] = []
    for col, (handled, templates) in FIELDS.items():
        for (en, ta), a in product(templates, APPS):
            out.append(Question(text_en=en.format(a=a), text_ta=ta.format(a=a),
                                column=col, kind="single", handled_by=handled))
    for en, ta, cols in COMBOS:
        for a in APPS:
            hb = ("field_map" if all(FIELDS[c][0] == "field_map" for c in cols)
                  else "not_projected" if all(FIELDS[c][0] == "not_projected" for c in cols)
                  else "mixed")
            out.append(Question(text_en=en.format(a=a), text_ta=ta.format(a=a),
                                column="+".join(cols), kind="combo", handled_by=hb))
    for ci, chain in enumerate(FOLLOWUP_CHAINS):
        for a in APPS:
            s_en, s_ta = chain["seed_en"].format(a=a), chain["seed_ta"].format(a=a)
            out.append(Question(text_en=s_en, text_ta=s_ta, column="(seed)",
                                kind="followup", follow_of="", handled_by=f"chain{ci}"))
            for (fen, fta, col) in chain["turns"]:
                out.append(Question(text_en=fen, text_ta=fta, column=col,
                                    kind="followup", handled_by=FIELDS.get(col, ("?", []))[0],
                                    follow_of=s_en))
    for en, ta, col in RULE_QUESTIONS:
        out.append(Question(text_en=en, text_ta=ta, column=col, kind="rule",
                            handled_by=FIELDS.get(col, ("rule", []))[0]))
    return out


def render_txt() -> str:
    bank = build_bank()
    W = "=" * 72
    L = [W, f"SIS CHATBOT - NISD/ISD application-info FIELD QUESTIONS ({len(bank)})", W,
         "Generated by backend/sample_db/appinfo_question_bank.py", "",
         "Each item: English line, then Tamil / Tanglish line.",
         "[not_projected] marks a column the chatbot has no data for -- the answer",
         "must be a deterministic 'not held in your register', never an LLM guess.",
         "[intent] is answered by a dedicated handler (check_documents / workflow).",
         "FOLLOW-UP turns depend on the nearest preceding seed; ask them in order,",
         "without repeating the application number.", ""]
    n = 0
    by_kind: dict[str, list[Question]] = {}
    for q in bank:
        by_kind.setdefault(q.kind, []).append(q)
    titles = {"single": "SECTION 1 - ONE FIELD, ONE APPLICATION",
              "combo": "SECTION 2 - SEVERAL FIELDS IN ONE BREATH",
              "followup": "SECTION 3 - PREVIOUS-MESSAGE REFERENCE CHAINS",
              "rule": "SECTION 4 - ASKED AS A RULE (no application named)"}
    for kind in ("single", "combo", "followup", "rule"):
        qs = by_kind.get(kind, [])
        L += ["", W, f"{titles[kind]}  ({len(qs)})", W]
        last = None
        for q in qs:
            if kind == "single" and q.column != last:
                L += ["", f"--- {q.column}  [{q.handled_by}] ---"]
                last = q.column
            n += 1
            pfx = f"{n}."
            if kind == "followup" and q.follow_of:
                pfx = f"{n}. (follow-up of {q.follow_of!r})"
            elif kind == "followup":
                pfx = f"{n}. SEED >>"
            tag = f"  [{q.handled_by}]" if kind in ("combo", "rule") else ""
            L.append(f"{pfx} {q.text_en}{tag}")
            if q.text_ta:
                L.append(f"    {q.text_ta}")
        L.append("")
    L += [W, "COVERAGE", W]
    cols = {}
    for q in bank:
        for c in q.column.split("+"):
            if c == "(seed)":
                continue
            cols.setdefault(c, [0, q.handled_by])
            cols[c][0] += 1
    for c in sorted(cols):
        cnt, hb = cols[c]
        hb = FIELDS.get(c, (hb, []))[0]
        L.append(f"  {c:<30} {cnt:>4}   {hb}")
    npj = sum(1 for q in bank if q.handled_by == "not_projected")
    L.append(f"\n  TOTAL {len(bank)} questions ({npj} target a not_projected column)")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    import sys
    if "--txt" in sys.argv:
        sys.stdout.write(render_txt())
    else:
        b = build_bank()
        from collections import Counter
        print(f"{len(b)} questions;", dict(Counter(q.kind for q in b)))
        print("by handling:", dict(Counter(q.handled_by for q in b)))
