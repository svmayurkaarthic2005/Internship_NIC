"""
Question bank for the `application_workflow_action` columns.

The SIS chatbot projects the CSV-shaped `application_workflow_action` table
(layer 1) into `workflow_history` + `field_visits` + a handful of
`applications` columns (layer 2). Only layer 2 is queryable by the chatbot.

This module enumerates questions an SIS officer could plausibly type about
every column of that source table, so the routing / answer paths can be
exercised against all 21 fields -- including the ones that are NOT projected
(annual_income, review_flag, received_flag, auto_recommendation_flag,
auto_recommendation_remarks, recommendation_status, ip_address, action_status),
where the correct behaviour is a deterministic "not held in your register"
answer rather than an LLM guess.

`build_bank()` returns a list of Question records. Nothing here touches the
database or the LLM. `render_txt()` emits the flat numbered form used by the
other `test_questions_*.txt` files.

Columns covered (application_workflow_action):
  serial_number, application_id, district_code, taluk_code, village_code,
  action_from_role_id, action_to_role_id, action_date, remarks, action_status,
  last_updated_datetime, updated_by_user, workflow_state, recommendation_status,
  field_visit_date, received_flag, annual_income, review_flag, ip_address,
  auto_recommendation_flag, auto_recommendation_remarks
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

# ── Sample identifiers that exist in the seeded sis_chatbot_db ────────────────
# (kept in sync with CLAUDE.md / the other test_questions_*.txt files)
APPS = {
    "nisd_approved": "2022/0153/28/000254",
    "isd_a":         "2022/0154/28/000156",
    "isd_b":         "2026/0154/28/001280",
    "isd_c":         "2026/0154/28/001281",
    "nisd_b":        "2026/0153/28/001190",
    "nisd_c":        "2026/0153/28/001876",
    "isd_fee":       "2025/0154/28/000286",
    "merge":         "2026/0155/28/000010",
    "nisd_d":        "2023/0153/28/000571",
    "isd_d":         "2024/0154/28/002252",
    "isd_e":         "2026/0154/28/001197",
}
APP_LIST = list(APPS.values())
SURVEY = "5"
WARD = "103"


@dataclass(frozen=True)
class Question:
    text_en: str
    column: str                 # which workflow_action column it probes
    kind: str                   # single | combo | followup | rule | list | tamil
    text_ta: str = ""           # Tamil / Tanglish rendering (optional)
    projected: bool = True      # is this column visible to the chatbot at all?
    follow_of: str = ""         # for kind == followup: the turn it depends on
    note: str = ""


# ── Per-column question templates ────────────────────────────────────────────
# {a} = an application number.  Each entry: (english, tamil/tanglish)
TEMPLATES = {
    # ---- projected: reachable via _field_map / workflow-history / decision-date
    "application_id": [
        ("What is the application number for {a}?", "{a} விண்ணப்ப எண் என்ன?"),
        ("Confirm the application id {a}", "{a} விண்ணப்ப ஐடி சரிபார்"),
        ("Is {a} a valid application in my ward?", "{a} என் வார்டில் உள்ள விண்ணப்பமா?"),
        ("Pull up application {a}", "{a} விண்ணப்பத்தை எடு"),
        ("{a} details", "{a} vivaram"),
    ],
    "serial_number": [
        ("What is the serial number of {a}?", "{a}-ன் வரிசை எண் என்ன?"),
        ("Give me the running serial for application {a}", "{a} விண்ணப்பத்தின் வரிசை எண்ணைத் தரவும்"),
        ("serial number {a}", "{a} வரிசை எண்"),
    ],
    "district_code": [
        ("What is the district code for {a}?", "{a} மாவட்ட குறியீடு என்ன?"),
        ("Which district code does {a} belong to?", "{a} எந்த மாவட்ட குறியீட்டைச் சேர்ந்தது?"),
        ("district code of {a}", "{a} மாவட்ட குறியீடு"),
    ],
    "taluk_code": [
        ("What is the taluk code for {a}?", "{a} தாலுகா குறியீடு என்ன?"),
        ("Give the taluk code on application {a}", "{a} விண்ணப்பத்தின் தாலுகா குறியீட்டைத் தரவும்"),
        ("taluk code {a}", "{a} taluk code enna"),
        ("Under which taluk code is {a} filed?", "{a} எந்த தாலுகா குறியீட்டில் தாக்கல் செய்யப்பட்டது?"),
    ],
    "village_code": [
        ("What is the village code for {a}?", "{a} கிராம குறியீடு என்ன?"),
        ("Which village code is recorded on {a}?", "{a}-ல் பதிவு செய்யப்பட்ட கிராம குறியீடு எது?"),
        ("village code {a}", "{a} கிராம குறியீடு"),
    ],
    "action_from_role_id": [
        ("Which desk did {a} come from at the last hop?", "கடைசி நகர்வில் {a} எந்த மேசையிலிருந்து வந்தது?"),
        ("What is the from-role on the latest workflow action for {a}?", "{a}-ன் சமீபத்திய பணிப்பாய்வு நடவடிக்கையின் அனுப்பிய பங்கு என்ன?"),
        ("Who forwarded {a} to its current stage?", "{a}-ஐ தற்போதைய நிலைக்கு யார் அனுப்பினார்?"),
    ],
    "action_to_role_id": [
        ("Which stage is {a} at now?", "{a} இப்போது எந்த நிலையில் உள்ளது?"),
        ("What is the to-role on the last workflow action for {a}?", "{a}-ன் கடைசி பணிப்பாய்வு நடவடிக்கையின் பெறும் பங்கு என்ன?"),
        ("Where has {a} been forwarded to?", "{a} எங்கே அனுப்பப்பட்டுள்ளது?"),
    ],
    "action_date": [
        ("On what date was the last action taken on {a}?", "{a}-ல் கடைசி நடவடிக்கை எந்த தேதியில் எடுக்கப்பட்டது?"),
        ("When was {a} last actioned in the workflow?", "பணிப்பாய்வில் {a} கடைசியாக எப்போது நடவடிக்கை எடுக்கப்பட்டது?"),
        ("What is the action date on the latest hop of {a}?", "{a}-ன் சமீபத்திய நகர்வின் நடவடிக்கை தேதி என்ன?"),
        ("action date {a}", "{a} action date eppo"),
    ],
    "remarks": [
        ("What are the remarks recorded on {a}?", "{a}-ல் பதிவு செய்யப்பட்ட குறிப்புகள் என்ன?"),
        ("Show the workflow remarks for {a}", "{a}-க்கான பணிப்பாய்வு குறிப்புகளைக் காட்டு"),
        ("Any comments recorded on application {a}?", "{a} விண்ணப்பத்தில் ஏதேனும் கருத்துகள் பதிவாகியுள்ளதா?"),
        ("What did the officer note on {a}?", "{a}-ல் அலுவலர் என்ன குறிப்பிட்டார்?"),
    ],
    "action_status": [
        ("What is the action status on the latest hop of {a}?", "{a}-ன் சமீபத்திய நகர்வின் நடவடிக்கை நிலை என்ன?"),
        ("Is the last workflow action on {a} complete or pending?", "{a}-ன் கடைசி பணிப்பாய்வு நடவடிக்கை முடிந்ததா நிலுவையிலா?"),
        ("action status {a}", "{a} action status enna"),
        ("Show the action_status value stored for {a}", "{a}-க்கு சேமிக்கப்பட்ட action_status மதிப்பைக் காட்டு"),
    ],
    "last_updated_datetime": [
        ("When was {a} last updated?", "{a} கடைசியாக எப்போது புதுப்பிக்கப்பட்டது?"),
        ("What is the last updated datetime for {a}?", "{a}-ன் கடைசியாக புதுப்பிக்கப்பட்ட தேதி நேரம் என்ன?"),
        ("last update on {a}", "{a} கடைசி புதுப்பிப்பு"),
    ],
    "updated_by_user": [
        ("Who last updated {a}?", "{a}-ஐ கடைசியாக யார் புதுப்பித்தார்?"),
        ("Which user made the last change on {a}?", "{a}-ல் கடைசி மாற்றத்தை எந்த பயனர் செய்தார்?"),
        ("Who processed application {a}?", "{a} விண்ணப்பத்தை யார் செயலாக்கினார்?"),
    ],
    "workflow_state": [
        ("What is the workflow state of {a}?", "{a}-ன் பணிப்பாய்வு நிலை என்ன?"),
        ("Is the workflow chain for {a} open or closed?", "{a}-ன் பணிப்பாய்வு சங்கிலி திறந்ததா மூடியதா?"),
        ("workflow state {a}", "{a} பணிப்பாய்வு நிலை"),
    ],
    "field_visit_date": [
        ("What is the field visit date for {a}?", "{a}-க்கான கள ஆய்வு தேதி என்ன?"),
        ("When is the site inspection for {a}?", "{a}-க்கான கள சோதனை எப்போது?"),
        ("Is a field visit scheduled for {a}?", "{a}-க்கு கள ஆய்வு திட்டமிடப்பட்டுள்ளதா?"),
        ("field visit date {a}", "{a} கள ஆய்வு தேதி"),
    ],
    # ---- NOT projected: chatbot has no data. Correct answer = deterministic
    #      "not held in your register" (never an LLM guess).
    "recommendation_status": [
        ("What is the recommendation status on {a}?", "{a}-ன் பரிந்துரை நிலை என்ன?"),
        ("Has {a} been recommended for approval?", "{a} அங்கீகாரத்திற்கு பரிந்துரைக்கப்பட்டதா?"),
        ("recommendation status of {a}", "{a} பரிந்துரை நிலை"),
    ],
    "received_flag": [
        ("What is the received flag on {a}?", "{a}-ன் பெறப்பட்ட கொடி என்ன?"),
        ("Is the received flag set on application {a}?", "{a} விண்ணப்பத்தில் பெறப்பட்ட கொடி அமைக்கப்பட்டுள்ளதா?"),
        ("received flag {a}", "{a} received flag enna"),
        ("Has the receiving desk acknowledged {a}?", "பெறும் மேசை {a}-ஐ ஒப்புக்கொண்டதா?"),
    ],
    "annual_income": [
        ("What is the annual income recorded on {a}?", "{a}-ல் பதிவு செய்யப்பட்ட ஆண்டு வருமானம் என்ன?"),
        ("What annual income did the applicant of {a} declare?", "{a} விண்ணப்பதாரர் அறிவித்த ஆண்டு வருமானம் என்ன?"),
        ("annual income {a}", "{a} ஆண்டு வருமானம்"),
    ],
    "review_flag": [
        ("What is the review flag on {a}?", "{a}-ன் மறுஆய்வு கொடி என்ன?"),
        ("Is {a} flagged for review?", "{a} மறுஆய்வுக்கு குறிக்கப்பட்டுள்ளதா?"),
        ("review flag {a}", "{a} review flag enna"),
        ("Does {a} need a second review?", "{a}-க்கு இரண்டாவது மறுஆய்வு தேவையா?"),
    ],
    "ip_address": [
        ("From which IP address was {a} submitted?", "{a} எந்த IP முகவரியிலிருந்து சமர்ப்பிக்கப்பட்டது?"),
        ("What is the IP address logged on {a}?", "{a}-ல் பதிவான IP முகவரி என்ன?"),
        ("ip address {a}", "{a} ip address enna"),
        ("Show the submitting client IP for {a}", "{a}-க்கான சமர்ப்பிப்பு கிளையண்ட் IP-ஐக் காட்டு"),
    ],
    "auto_recommendation_flag": [
        ("Was an auto recommendation raised on {a}?", "{a}-ல் தானியங்கி பரிந்துரை எழுப்பப்பட்டதா?"),
        ("What is the auto recommendation flag on {a}?", "{a}-ன் தானியங்கி பரிந்துரை கொடி என்ன?"),
        ("auto recommendation flag {a}", "{a} auto recommendation flag enna"),
        ("Did the system auto-recommend {a}?", "கணினி {a}-ஐ தானாக பரிந்துரைத்ததா?"),
    ],
    "auto_recommendation_remarks": [
        ("What are the auto recommendation remarks on {a}?", "{a}-ன் தானியங்கி பரிந்துரை குறிப்புகள் என்ன?"),
        ("Show the system recommendation note for {a}", "{a}-க்கான கணினி பரிந்துரை குறிப்பைக் காட்டு"),
        ("auto recommendation remarks {a}", "{a} auto recommendation remarks enna"),
    ],
}

NOT_PROJECTED = {
    "recommendation_status", "received_flag", "annual_income", "review_flag",
    "ip_address", "auto_recommendation_flag", "auto_recommendation_remarks",
    "action_status",
}

# ── Combination shapes (two/three fields in one breath) ──────────────────────
COMBOS = [
    ("Show the from-role, to-role and remarks on the last hop of {a}",
     "{a}-ன் கடைசி நகர்வின் அனுப்பிய பங்கு, பெறும் பங்கு மற்றும் குறிப்புகளைக் காட்டு",
     ["action_from_role_id", "action_to_role_id", "remarks"]),
    ("Who last updated {a} and when?",
     "{a}-ஐ கடைசியாக யார், எப்போது புதுப்பித்தார்?",
     ["updated_by_user", "last_updated_datetime"]),
    ("What is the workflow state and the last updated datetime for {a}?",
     "{a}-ன் பணிப்பாய்வு நிலை மற்றும் கடைசி புதுப்பிப்பு தேதி என்ன?",
     ["workflow_state", "last_updated_datetime"]),
    ("Give the district code, taluk code and village code for {a}",
     "{a}-க்கான மாவட்ட, தாலுகா மற்றும் கிராம குறியீடுகளைத் தரவும்",
     ["district_code", "taluk_code", "village_code"]),
    ("What is the field visit date and the recommendation status on {a}?",
     "{a}-ன் கள ஆய்வு தேதி மற்றும் பரிந்துரை நிலை என்ன?",
     ["field_visit_date", "recommendation_status"]),
    ("Show the serial number and workflow state for {a}",
     "{a}-ன் வரிசை எண் மற்றும் பணிப்பாய்வு நிலையைக் காட்டு",
     ["serial_number", "workflow_state"]),
    ("What are the remarks and the action status on {a}?",
     "{a}-ன் குறிப்புகள் மற்றும் நடவடிக்கை நிலை என்ன?",
     ["remarks", "action_status"]),
    ("Annual income and review flag for {a}?",
     "{a}-க்கான ஆண்டு வருமானம் மற்றும் மறுஆய்வு கொடி?",
     ["annual_income", "review_flag"]),
]

# ── Follow-up reference chains (prev-message resolution) ─────────────────────
# Each chain: a first turn that fixes a referent, then pronoun-less / pronoun
# follow-ups the bot must resolve WITHOUT the officer repeating the number.
FOLLOWUP_CHAINS = [
    {
        "seed_en": "Show the workflow history for {a}",
        "seed_ta": "{a}-க்கான பணிப்பாய்வு வரலாற்றைக் காட்டு",
        "turns": [
            ("Who did the last hop?", "கடைசி நகர்வை யார் செய்தார்?", "updated_by_user"),
            ("When was that step?", "அந்த படி எப்போது?", "action_date"),
            ("What were the remarks on it?", "அதில் குறிப்புகள் என்ன?", "remarks"),
            ("Which stage did it move to?", "அது எந்த நிலைக்கு நகர்ந்தது?", "action_to_role_id"),
        ],
    },
    {
        "seed_en": "What is the status of {a}?",
        "seed_ta": "{a}-ன் நிலை என்ன?",
        "turns": [
            ("When was it last updated?", "அது கடைசியாக எப்போது புதுப்பிக்கப்பட்டது?", "last_updated_datetime"),
            ("Who updated it?", "யார் புதுப்பித்தார்?", "updated_by_user"),
            ("What is its workflow state?", "அதன் பணிப்பாய்வு நிலை என்ன?", "workflow_state"),
            ("Is it flagged for review?", "அது மறுஆய்வுக்கு குறிக்கப்பட்டுள்ளதா?", "review_flag"),
            ("What is its recommendation status?", "அதன் பரிந்துரை நிலை என்ன?", "recommendation_status"),
        ],
    },
    {
        "seed_en": "Is a field visit scheduled for {a}?",
        "seed_ta": "{a}-க்கு கள ஆய்வு திட்டமிடப்பட்டுள்ளதா?",
        "turns": [
            ("When was it scheduled?", "அது எப்போது திட்டமிடப்பட்டது?", "field_visit_date"),
            ("What date exactly?", "சரியாக எந்த தேதி?", "field_visit_date"),
            ("Who is it assigned to?", "அது யாருக்கு ஒதுக்கப்பட்டுள்ளது?", "updated_by_user"),
        ],
    },
    {
        "seed_en": "Show my pending applications",
        "seed_ta": "என் நிலுவையில் உள்ள விண்ணப்பங்களைக் காட்டு",
        "turns": [
            ("Which one was updated most recently?", "எது மிக சமீபத்தில் புதுப்பிக்கப்பட்டது?", "last_updated_datetime"),
            ("How many of them are still at the SIS desk?", "அவற்றில் எத்தனை இன்னும் SIS மேசையில் உள்ளன?", "action_to_role_id"),
            ("Which of them have a field visit date?", "அவற்றில் எவற்றுக்கு கள ஆய்வு தேதி உள்ளது?", "field_visit_date"),
            ("Do any of them have review remarks?", "அவற்றில் ஏதேனும் மறுஆய்வு குறிப்புகள் உள்ளதா?", "remarks"),
        ],
    },
    {
        "seed_en": "Show the workflow history for {a}",
        "seed_ta": "{a}-க்கான பணிப்பாய்வு வரலாற்றைக் காட்டு",
        "turns": [
            ("What is the annual income on it?", "அதில் ஆண்டு வருமானம் என்ன?", "annual_income"),
            ("Which IP address filed it?", "எந்த IP முகவரி அதைத் தாக்கல் செய்தது?", "ip_address"),
            ("Was there an auto recommendation?", "தானியங்கி பரிந்துரை இருந்ததா?", "auto_recommendation_flag"),
        ],
    },
]

# ── Rule-shaped questions (asked AS a rule, not about one file) ──────────────
RULE_QUESTIONS = [
    ("What does the recommendation status field mean?", "பரிந்துரை நிலை புலம் என்றால் என்ன?", "recommendation_status"),
    ("Does your register track the applicant's annual income?", "உங்கள் பதிவேட்டில் விண்ணப்பதாரரின் ஆண்டு வருமானம் பதிவாகிறதா?", "annual_income"),
    ("Is the submitting IP address available to you?", "சமர்ப்பிக்கும் IP முகவரி உங்களிடம் உள்ளதா?", "ip_address"),
    ("What is the difference between action_date and last_updated_datetime?",
     "action_date மற்றும் last_updated_datetime இடையே என்ன வித்தியாசம்?", "last_updated_datetime"),
    ("What are the workflow role ids and which stage is each?",
     "பணிப்பாய்வு பங்கு ஐடிகள் எவை, ஒவ்வொன்றும் எந்த நிலை?", "action_from_role_id"),
    ("Which workflow_state values mean the file is closed?",
     "எந்த workflow_state மதிப்புகள் கோப்பு மூடப்பட்டதைக் குறிக்கின்றன?", "workflow_state"),
    ("Do NISD applications carry a field visit date?", "NISD விண்ணப்பங்களுக்கு கள ஆய்வு தேதி உண்டா?", "field_visit_date"),
    ("What is the review flag used for?", "மறுஆய்வு கொடி எதற்குப் பயன்படுகிறது?", "review_flag"),
    ("What is an auto recommendation?", "தானியங்கி பரிந்துரை என்றால் என்ன?", "auto_recommendation_flag"),
    ("If there are no remarks on a hop, what does that mean?",
     "ஒரு நகர்வில் குறிப்புகள் இல்லையென்றால் அது என்ன அர்த்தம்?", "remarks"),
]

# ── List / aggregate shapes over a whole queue ──────────────────────────────
LIST_QUESTIONS = [
    ("List my applications with their last updated datetime", "என் விண்ணப்பங்களை கடைசி புதுப்பிப்பு தேதியுடன் பட்டியலிடு", "last_updated_datetime"),
    ("Which of my applications were updated today?", "என் விண்ணப்பங்களில் எவை இன்று புதுப்பிக்கப்பட்டன?", "last_updated_datetime"),
    ("Show every application still in workflow state P", "இன்னும் P நிலையில் உள்ள ஒவ்வொரு விண்ணப்பத்தையும் காட்டு", "workflow_state"),
    ("List applications with a field visit date this week", "இந்த வாரம் கள ஆய்வு தேதி உள்ள விண்ணப்பங்களை பட்டியலிடு", "field_visit_date"),
    ("Which applications have no remarks recorded?", "எந்த விண்ணப்பங்களில் குறிப்புகள் பதிவாகவில்லை?", "remarks"),
    ("How many of my applications are recommended for approval?", "என் விண்ணப்பங்களில் எத்தனை அங்கீகாரத்திற்கு பரிந்துரைக்கப்பட்டுள்ளன?", "recommendation_status"),
    ("List the applications I last updated, most recent first", "நான் கடைசியாக புதுப்பித்த விண்ணப்பங்களை, சமீபத்தியது முதலில் பட்டியலிடு", "updated_by_user"),
    ("Which applications were actioned from the SD desk?", "SD மேசையிலிருந்து எந்த விண்ணப்பங்கள் நடவடிக்கை எடுக்கப்பட்டன?", "action_from_role_id"),
]


def build_bank() -> list[Question]:
    out: list[Question] = []

    # 1. single-field questions, every column x every template x sample apps
    for col, templates in TEMPLATES.items():
        projected = col not in NOT_PROJECTED
        for (en, ta), a in product(templates, APP_LIST):
            out.append(Question(
                text_en=en.format(a=a), text_ta=ta.format(a=a),
                column=col, kind="single", projected=projected))

    # 2. combination questions
    for en, ta, cols in COMBOS:
        for a in APP_LIST:
            out.append(Question(
                text_en=en.format(a=a), text_ta=ta.format(a=a),
                column="+".join(cols), kind="combo",
                projected=all(c not in NOT_PROJECTED for c in cols)))

    # 3. follow-up reference chains
    for ci, chain in enumerate(FOLLOWUP_CHAINS):
        for a in APP_LIST:
            seed_en = chain["seed_en"].format(a=a)
            seed_ta = chain["seed_ta"].format(a=a)
            out.append(Question(text_en=seed_en, text_ta=seed_ta,
                                column="(seed)", kind="followup",
                                follow_of="", note=f"chain {ci}"))
            for (fen, fta, col) in chain["turns"]:
                out.append(Question(
                    text_en=fen, text_ta=fta, column=col, kind="followup",
                    projected=col not in NOT_PROJECTED,
                    follow_of=seed_en, note=f"chain {ci}"))

    # 4. rule-shaped
    for en, ta, col in RULE_QUESTIONS:
        out.append(Question(text_en=en, text_ta=ta, column=col, kind="rule",
                            projected=col not in NOT_PROJECTED))

    # 5. list / aggregate
    for en, ta, col in LIST_QUESTIONS:
        out.append(Question(text_en=en, text_ta=ta, column=col, kind="list",
                            projected=col not in NOT_PROJECTED))

    return out


def render_txt() -> str:
    bank = build_bank()
    lines = []
    W = "=" * 70
    lines.append(W)
    lines.append(f"SIS CHATBOT - application_workflow_action FIELD QUESTIONS ({len(bank)})")
    lines.append(W)
    lines.append("Generated by backend/sample_db/workflow_action_question_bank.py")
    lines.append("")
    lines.append("Each item: English line, then Tamil / Tanglish line.")
    lines.append("[NOT-PROJECTED] marks a column the chatbot has no data for -- the")
    lines.append("correct answer is a deterministic 'not held in your register',")
    lines.append("never an LLM guess.")
    lines.append("FOLLOW-UP items depend on the nearest preceding seed turn; ask them")
    lines.append("in order in one conversation without repeating the application no.")
    lines.append("")

    n = 0
    by_kind: dict[str, list[Question]] = {}
    for q in bank:
        by_kind.setdefault(q.kind, []).append(q)

    kind_titles = {
        "single": "SECTION 1 - ONE FIELD, ONE APPLICATION",
        "combo": "SECTION 2 - SEVERAL FIELDS IN ONE BREATH",
        "followup": "SECTION 3 - PREVIOUS-MESSAGE REFERENCE CHAINS",
        "rule": "SECTION 4 - ASKED AS A RULE (no application named)",
        "list": "SECTION 5 - LIST / AGGREGATE OVER A QUEUE",
    }
    for kind in ("single", "combo", "followup", "rule", "list"):
        qs = by_kind.get(kind, [])
        lines.append("")
        lines.append(W)
        lines.append(f"{kind_titles[kind]}  ({len(qs)})")
        lines.append(W)
        last_col = None
        for q in qs:
            if kind == "single" and q.column != last_col:
                tag = " [NOT-PROJECTED]" if not q.projected else ""
                lines.append("")
                lines.append(f"--- column: {q.column}{tag} ---")
                last_col = q.column
            n += 1
            prefix = f"{n}."
            if kind == "followup" and q.follow_of:
                prefix = f"{n}.  (follow-up of: {q.follow_of!r})"
            elif kind == "followup":
                prefix = f"{n}.  SEED >>"
            lines.append(f"{prefix} {q.text_en}")
            if q.text_ta:
                lines.append(f"    {q.text_ta}")
        lines.append("")

    lines.append(W)
    lines.append("COVERAGE SUMMARY")
    lines.append(W)
    cols = sorted({c for q in bank for c in q.column.split("+") if c not in ("(seed)",)})
    for c in cols:
        cnt = sum(1 for q in bank if c in q.column.split("+"))
        tag = "  NOT-PROJECTED" if c in NOT_PROJECTED else ""
        lines.append(f"  {c:<28} {cnt:>4} question(s){tag}")
    lines.append("")
    lines.append(f"  TOTAL {len(bank)} questions "
                 f"({sum(1 for q in bank if not q.projected)} target a not-projected column)")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import sys
    if "--txt" in sys.argv:
        sys.stdout.write(render_txt())
    else:
        b = build_bank()
        print(f"{len(b)} questions")
        from collections import Counter
        print("by kind:", dict(Counter(q.kind for q in b)))
        print("not projected:", sum(1 for q in b if not q.projected))
