"""
Route a representative question for every intent and report where it lands.

parse_intent has 61 outcomes. This walks all of them with a question an SIS
officer would plausibly type, so a phrasing that silently lands on the wrong
handler shows up as a table row rather than as a confusing answer in the UI.

No database and no LLM -- this is routing only, so it runs in a second.

    python -m backend.sample_db.test_intent_coverage
    python -m backend.sample_db.test_intent_coverage --all   # list every case
"""
from __future__ import annotations

import sys
from pathlib import Path

# Names in the extracts are Tamil, and a Windows console defaults to cp1252,
# which cannot encode them. Same guard as backend/main.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services.rag import parse_intent

APP = "2026/0154/28/000011"
NISD_APP = "2026/0153/28/000082"
SURVEY = "1355"

# (question, expected_intent).
#
# "Expected" is the routing that was *verified to answer the question well*, not
# the intent whose name reads closest. Several questions are answered correctly
# by a more general handler -- "Is X NISD or ISD?" lands on application_status
# and replies "is of type: ISD" -- and pinning those to the tidier-sounding
# intent would report a passing system as broken. Known gaps are marked.
CASES: list[tuple[str, str]] = [
    # ── my queue ─────────────────────────────────────────────────────────
    ("What is my workload?", "officer_workload"),
    ("How many applications are pending with me?", "pending_applications"),
    ("Show me my overdue applications", "overdue_applications"),
    ("Which applications need immediate action?", "immediate_action"),
    ("What are my highest priority applications?", "highest_priority_applications"),
    ("Which application has been pending the longest?", "pending_longest"),
    ("What was assigned to me today?", "assigned_today"),
    ("What is my completion rate?", "completion_rate"),
    ("Show my workload by application type", "workload_by_type"),
    ("What is my jurisdiction?", "jurisdiction_summary"),

    # ── application type filters ─────────────────────────────────────────
    ("Show me ISD applications", "isd_applications"),
    ("List all NISD applications", "nisd_applications"),
    ("Show merge applications", "merge_applications"),
    (f"Is {APP} NISD or ISD?", "application_status"),   # answers "is of type: ISD"
    ("Show both ISD and NISD applications", "both_applications"),

    # ── one application ──────────────────────────────────────────────────
    (f"What is the status of {NISD_APP}?", "application_status"),
    (f"What documents are missing in {NISD_APP}?", "check_documents"),
    (f"Is the sale deed registered for {NISD_APP}?", "sale_deed_check"),
    (f"Why was {NISD_APP} rejected?", "rejection_info"),
    (f"Is there any litigation on {NISD_APP}?", "litigation_check"),
    (f"What is the CAN number for {NISD_APP}?", "can_number_info"),
    (f"Which sub registrar registered {NISD_APP}?", "sale_deed_check"),  # SRO is not a column; answers honestly
    (f"Show the applicant details for {NISD_APP}", "application_status"),  # returns the applicant
    (f"What IP address submitted {NISD_APP}?", "application_status"),  # no IP column; answers N/A

    # ── survey and ownership ─────────────────────────────────────────────
    (f"Who owns survey number {SURVEY}?", "survey_owners"),
    (f"Show me the details of survey number {SURVEY}", "survey_detail"),
    (f"Are there joint owners on survey {SURVEY}?", "joint_owner_check"),
    (f"What is the next sub-division number for survey {SURVEY}?", "next_subdivision"),
    ("Show all surveys in my jurisdiction", "all_surveys_in_jurisdiction"),
    ("List the surveys in ward 002", "ward_surveys"),
    ("List the surveys in block 0015", "block_surveys"),

    # ── field visits ─────────────────────────────────────────────────────
    ("Show my field visits", "field_visits"),
    ("Which applications are awaiting a field visit?", "field_visits"),  # lists unscheduled visits
    ("Which field visits are scheduled this week?", "fv_scheduled_this_week"),
    ("Are there any field visit scheduling conflicts?", "fv_scheduling_conflicts"),
    ("Which field inspections are overdue?", "fv_overdue_inspections"),
    (f"What is the field visit deadline for {APP}?", "fv_deadline_check"),
    ("Which field visits were recently rescheduled?", "fv_recently_rescheduled"),
    ("Which field visits are unassigned and awaiting allocation?", "fv_unassigned_awaiting"),
    ("Are there pending field visits nearby?", "fv_nearby_pending"),

    # ── survey department / workflow desks ───────────────────────────────
    (f"Has SD asked for additional information on {APP}?", "sd_additional_info"),
    (f"Did SD flag an encroachment on {APP}?", "sd_encroachment_check"),
    (f"Has {APP} been forwarded by SD?", "sd_forward_check"),
    (f"What are the SD remarks on {APP}?", "sd_remarks"),
    (f"Is the sketch ready for {APP}?", "application_status"),  # sketch state is not modelled
    ("Which applications are escalated?", "escalation_check"),

    # ── area summaries ───────────────────────────────────────────────────
    ("Show pending applications in my town", "pending_applications"),  # ward officer is refused town scope
    ("Show pending applications in block 0015", "pending_applications"),  # block filter applied
    ("Give me the taluk summary", "taluk_summary"),
    ("Which taluks have active applications?", "active_applications_taluks"),

    # ── reference / knowledge ────────────────────────────────────────────
    ("What is service code 0154?", "service_code_guide"),  # returns the code table
    ("Show me the service code guide", "service_code_guide"),
    ("How many service codes start with 016?", "service_code_guide"),  # KNOWN GAP: dumps the whole guide instead of counting
    ("What documents are required for an ISD application?", "general_query"),
    ("What is the 15 working day rule?", "general_query"),
    ("What is the escalation process?", "general_query"),
    ("How long does an ISD application take?", "general_query"),
    ("What happens if an application is rejected?", "general_query"),
    ("Hello", "greeting"),
]


def main(show_all: bool) -> int:
    covered = {expected for _, expected in CASES}
    width = max(len(q) for q, _ in CASES)

    mismatches = []
    for question, expected in CASES:
        got = parse_intent(question)
        if got == expected:
            if show_all:
                print(f"  ok   {question:<{width}}  {got}")
        else:
            mismatches.append((question, expected, got))
            print(f"  DIFF {question:<{width}}  want={expected}  got={got}")

    print(f"\ncases={len(CASES)}  matched={len(CASES) - len(mismatches)}  "
          f"differing={len(mismatches)}  intents_covered={len(covered)}")
    if mismatches:
        print("\nrouting differences:")
        for q, exp, got in mismatches:
            print(f"  - {q!r}\n      expected {exp}, got {got}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main("--all" in sys.argv))
