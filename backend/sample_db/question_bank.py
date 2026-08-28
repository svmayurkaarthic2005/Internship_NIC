"""
Generate a large bank of questions an SIS officer could plausibly type.

Built by combining the dimensions real questions vary along -- application type,
status, time period, geography, field-visit state, phrasing -- plus the awkward
cases that break naive routing: negations ("which are NOT overdue"), Tamil,
and questions that are simply outside an SIS officer's remit.

Used by test_question_bank.py. Importing this module gives you `build_bank()`,
which returns a list of Question records; nothing here touches the database.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product

APP_ISD = "2026/0154/28/000011"
APP_NISD = "2026/0153/28/000082"
SURVEY = "1355"


@dataclass(frozen=True)
class Question:
    text: str
    group: str            # what family it belongs to
    expect: str | None    # expected intent, when there is one right answer
    note: str = ""


# ── dimensions ───────────────────────────────────────────────────────────
TYPES = [("ISD", "isd_applications"), ("NISD", "nisd_applications"),
         ("merge", "merge_applications")]

PERIODS = [
    "today", "yesterday", "this week", "last week", "this month", "last month",
    "the previous month", "this year", "last year", "the last 7 days",
    "the last 30 days", "January", "March", "this quarter", "the summer",
    "2025", "2026",
]

STATUSES = ["pending", "in progress", "approved", "rejected", "escalated",
            "overdue", "completed"]

SCOPES = ["in my ward", "in my block", "in my jurisdiction", "in ward 002",
          "in block 0015", "in my town", "in my taluk"]

LIST_VERBS = ["Show me", "List", "Give me", "Display", "How many are there of",
              "I want to see", "Fetch", "Can you list"]

FV_TOPICS = [
    # only "this week" has a dedicated intent; other periods fall to field_visits
    ("Which field visits are scheduled {p}?", None),
    ("Show field visits {p}", None),
    ("Which field visits are unscheduled?", None),
    ("Which field visits are overdue?", "fv_overdue_inspections"),
    ("Are there scheduling conflicts?", "fv_scheduling_conflicts"),
    ("Which field visits are unassigned?", "fv_unassigned_awaiting"),
    ("Which field visits were rescheduled recently?", "fv_recently_rescheduled"),
]

# The rule the officer must be told about, from workflow_guide.txt:
# only the Tahsildar may approve a field visit date change.
FV_DATE_CHANGE = [
    "Can I change the field visit date?",
    "I want to change the field visit date",
    "How do I reschedule a field visit?",
    "Can I postpone the field visit?",
    "Can I move the field visit to next week?",
    "Who approves a change of field visit date?",
    "Do I need permission to change the field visit date?",
    "Can I reschedule the inspection myself?",
    f"Can I change the field visit date for {APP_ISD}?",
    "I need to shift the field visit to another day",
    "May I preponed the field visit?",
    "Is it okay to change the inspection date on my own?",
]

# Questions phrased as a negative. Naive keyword routing reads "not overdue" as
# "overdue" and answers the opposite of what was asked.
NEGATIONS = [
    ("Which applications are not overdue?", "applications that are within the deadline"),
    ("Show applications that are not rejected", "excludes rejected"),
    ("Which applications have no field visit scheduled?", "unscheduled visits"),
    ("List applications without a sale deed", "missing sale deed"),
    ("Which surveys have no owner recorded?", "surveys lacking ownership"),
    ("Show me applications that are not ISD", "NISD or merge"),
    ("Which applications are still not approved?", "not yet approved"),
    ("Are there any applications with no documents uploaded?", "missing documents"),
    ("Which field visits have not been completed?", "incomplete visits"),
    ("Show applications that were never escalated", "not escalated"),
    ("Which applications do not have litigation?", "clear of litigation"),
    ("List surveys that are not sub-divided", "no sub-divisions"),
]

# Outside an SIS officer's remit. These must be declined, not answered.
OUT_OF_SCOPE = [
    "What is the capital of France?",
    "How do I cook biryani?",
    "Write me a poem about the sea",
    "What is the weather tomorrow?",
    "Who won the cricket match yesterday?",
    "Tell me a joke",
    "What is 456 multiplied by 789?",
    "Can you write Python code for me?",
    "What is the stock price of Reliance?",
    "Translate 'good morning' into French",
    "Who is the Prime Minister of India?",
    "Recommend a good movie",
    "What is my horoscope today?",
    "How do I apply for a passport?",
    "Book me a train ticket to Chennai",
]

# Tamil equivalents of the core questions.
TAMIL = [
    ("என் பணிச்சுமை என்ன?", "officer_workload"),
    ("நிலுவையில் உள்ள விண்ணப்பங்கள் எத்தனை?", "pending_applications"),
    ("காலதாமதமான விண்ணப்பங்களைக் காட்டு", "overdue_applications"),
    ("ISD விண்ணப்பங்களைக் காட்டு", "isd_applications"),
    ("NISD விண்ணப்பங்களைப் பட்டியலிடு", "nisd_applications"),
    ("என் அதிகார வரம்பு என்ன?", "jurisdiction_summary"),
    ("கள ஆய்வுகளைக் காட்டு", None),
    ("கள ஆய்வு தேதியை மாற்ற முடியுமா?", None),
    (f"விண்ணப்பம் {APP_NISD} நிலை என்ன?", "application_status"),
    # answers via joint_owner_check, which names the owners in Tamil
    ("சர்வே எண் 1355 உரிமையாளர் யார்?", "joint_owner_check"),
    ("ISD விண்ணப்பத்திற்கு என்ன ஆவணங்கள் தேவை?", "general_query"),
    ("வணக்கம்", "greeting"),
]

# Procedural questions answered from the documents.
WORKFLOW_KNOWLEDGE = [
    "What are the steps in the ISD workflow?",
    "What are the steps in the NISD workflow?",
    "What is the 15 working day rule?",
    "What is the escalation process?",
    "What happens at level 2 escalation?",
    "What happens at level 3 escalation?",
    "What documents are required for an ISD application?",
    "What documents are required for NISD?",
    "How long does an ISD application take?",
    "How long does a NISD application take?",
    "What happens if an application is rejected?",
    "How many resubmissions are allowed?",
    "How long does an applicant have to resubmit?",
    "What are the common rejection reasons?",
    "Can two applications be active on one survey number?",
    "Who applies the digital signature?",
    "What is a DSC?",
    "Who prepares the survey sketch?",
    "What does DIS verify?",
    "What is the sub-division numbering pattern?",
    "What is service code 0153?",
    "What is service code 0154?",
    "What is service code 0155?",
    "Which service codes does an SIS officer handle?",
    "What is TSLR?",
    "What does an SIS officer do?",
    "Which districts does the system cover?",
    "What is the application number format?",
]


def build_bank() -> list[Question]:
    bank: list[Question] = []

    # type x list-verb x scope -> a listing question
    for (label, intent), verb, scope in product(TYPES, LIST_VERBS, SCOPES):
        bank.append(Question(f"{verb} {label} applications {scope}",
                             "type+scope", intent))

    # type x period
    for (label, intent), period in product(TYPES, PERIODS):
        bank.append(Question(f"Show {label} applications from {period}",
                             "type+period", intent, f"must return only {label}"))
        bank.append(Question(f"How many {label} applications came in {period}?",
                             "type+period", intent, f"must return only {label}"))

    # type x status. No expected intent: "pending ISD applications" is answered
    # by the pending handler with the ISD filter applied, which is correct even
    # though the intent is named for the status. The filter itself is asserted
    # by test_question_bank's --filters pass.
    for (label, _intent), status in product(TYPES, STATUSES):
        bank.append(Question(f"Show {status} {label} applications",
                             "type+status", None, f"must return only {label}"))

    # type x status x scope
    for (label, _intent), status, scope in product(TYPES, STATUSES, SCOPES[:3]):
        bank.append(Question(f"List {status} {label} applications {scope}",
                             "type+status+scope", None, f"must return only {label}"))

    # status x period
    for status, period in product(STATUSES, PERIODS):
        bank.append(Question(f"How many {status} applications were there {period}?",
                             "status+period", None))

    # status x scope
    for status, scope in product(STATUSES, SCOPES):
        bank.append(Question(f"Show {status} applications {scope}",
                             "status+scope", None))

    # field visits x period
    for (template, intent), period in product(FV_TOPICS, PERIODS):
        bank.append(Question(template.replace("{p}", period), "field_visit", intent))

    for q in FV_DATE_CHANGE:
        bank.append(Question(q, "fv_date_change", None,
                             "must say the Tahsildar approves the change"))

    for q, note in NEGATIONS:
        bank.append(Question(q, "negation", None, note))

    for q in OUT_OF_SCOPE:
        bank.append(Question(q, "out_of_scope", None, "must be declined"))

    for q, intent in TAMIL:
        bank.append(Question(q, "tamil", intent))

    # These four route to service_code_guide, which returns the code table with
    # fees, SLA and workflow -- a better answer than free text, so that is the
    # expected routing rather than general_query.
    _service_code_answers = {
        "What is service code 0153?", "What is service code 0154?",
        "What is service code 0155?",
        "Which service codes does an SIS officer handle?",
    }
    for q in WORKFLOW_KNOWLEDGE:
        expect = "service_code_guide" if q in _service_code_answers else "general_query"
        bank.append(Question(q, "workflow_knowledge", expect))

    # one-application questions across many phrasings
    for app in (APP_ISD, APP_NISD):
        for template in [
            "What is the status of {a}?", "Show me details of {a}",
            "Who is the applicant for {a}?", "What documents are missing in {a}?",
            "Why was {a} rejected?", "Is the sale deed registered for {a}?",
            "Is there any litigation on {a}?", "Show workflow history of {a}",
            "What is the CAN number for {a}?", "Which stage is {a} at?",
            "When was {a} submitted?", "Is {a} overdue?",
            "What is the field visit date for {a}?", "Is {a} ISD or NISD?",
        ]:
            bank.append(Question(template.replace("{a}", app), "one_application", None))

    # survey questions
    for template in [
        "Who owns survey number {s}?", "Show me survey {s}",
        "What is the area of survey {s}?", "Are there joint owners on survey {s}?",
        "What is the next sub-division number for survey {s}?",
        "How many sub-divisions does survey {s} have?",
        "Which block is survey {s} in?", "Is survey {s} under litigation?",
    ]:
        bank.append(Question(template.replace("{s}", SURVEY), "survey", None))

    # my-queue phrasings
    for q in [
        "What is my workload?", "How many applications do I have?",
        "What is pending with me?", "Show my overdue applications",
        "What needs immediate action?", "What are my priorities?",
        "What was assigned to me today?", "What is my completion rate?",
        "Show my workload by type", "Which application is pending longest?",
        "What is my jurisdiction?", "How am I doing this month?",
    ]:
        bank.append(Question(q, "my_queue", None))

    # de-duplicate while keeping order
    seen, unique = set(), []
    for item in bank:
        if item.text not in seen:
            seen.add(item.text)
            unique.append(item)
    return unique


if __name__ == "__main__":
    bank = build_bank()
    from collections import Counter
    groups = Counter(q.group for q in bank)
    print(f"total unique questions: {len(bank)}")
    for group, n in groups.most_common():
        print(f"  {group:22s} {n}")
