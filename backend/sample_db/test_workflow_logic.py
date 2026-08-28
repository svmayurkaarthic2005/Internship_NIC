"""
Check the SIS workflow rules hold in the data the chatbot answers from.

Answer-level tests catch a question routed to the wrong handler. These catch the
other failure: the routing is right, the sentence reads fine, and the underlying
data quietly contradicts the process -- an application approved while still
sitting at the SIS desk, a field visit on an NISD application, a workflow action
dated before the application was submitted. Every rule below is from
backend/documents/workflow_guide.txt or from the constraints in
backend/models.py.

    python -m backend.sample_db.test_workflow_logic
"""
from __future__ import annotations

import asyncio
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

from sqlalchemy import select, text

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.services import postgres

# Each rule: (label, why it matters, SQL returning the offending rows' count)
RULES: list[tuple[str, str, str]] = [
    # workflow_guide.txt states that only ONE active application is allowed per
    # survey number at a time. The extracts disprove it: ward 103 survey 5
    # subdivision 4A patta 60738 carries three live applications at once -- an
    # NISD and two ISDs, three different applicants, three different sale deeds,
    # filed two days apart. models.py sided with the extracts and declares a
    # plain index rather than a unique one, so the rule that can be checked is
    # the one that would catch a projection fault: concurrent applications on a
    # survey number have to be genuinely different filings, not one row
    # duplicated.
    ("concurrent applications on a survey are distinct filings",
     "a parcel can be under more than one live request at a time, but two of "
     "them sharing an applicant, a type and a sale deed would mean the "
     "projection duplicated a single application",
     """SELECT count(*) FROM (
            SELECT survey_number_id, applicant_id, application_type,
                   sale_deed_number
            FROM applications
            WHERE current_status IN ('pending','in_progress','escalated')
            GROUP BY 1, 2, 3, 4 HAVING count(*) > 1) x"""),

    ("approved applications are at the COMPLETED stage",
     "an approved application has finished the chain",
     "SELECT count(*) FROM applications "
     "WHERE current_status = 'approved' AND current_stage <> 'COMPLETED'"),

    ("rejected applications are at the REJECTED stage",
     "a rejected application cannot still be sitting at a desk",
     "SELECT count(*) FROM applications "
     "WHERE current_status = 'rejected' AND current_stage <> 'REJECTED'"),

    ("active applications are at a real desk",
     "an open application must be with SIS, SD, DIS or the Tahsildar",
     """SELECT count(*) FROM applications
        WHERE current_status IN ('pending','in_progress','escalated')
          AND current_stage NOT IN ('SIS','SD','DIS','TAHSILDAR')"""),

    ("field visits only on ISD applications",
     "workflow_guide.txt: the field visit is mandatory for ISD and not required "
     "for NISD unless an issue is flagged",
     """SELECT count(*) FROM field_visits f
        JOIN applications a ON a.id = f.application_id
        WHERE a.application_type <> 'ISD'"""),

    ("overdue flag only on active ISD applications",
     "the 15-working-day rule applies to ISD field visits, so a closed or NISD "
     "application cannot be overdue",
     """SELECT count(*) FROM applications
        WHERE is_overdue
          AND (application_type <> 'ISD'
               OR current_status NOT IN ('pending','in_progress','escalated'))"""),

    ("no workflow action before submission",
     "a desk cannot act on an application that has not been submitted",
     """SELECT count(*) FROM workflow_history w
        JOIN applications a ON a.id = w.application_id
        WHERE w.performed_at::date < a.submission_date"""),

    ("nothing dated in the future",
     "the register records what has happened, not what will",
     """SELECT (SELECT count(*) FROM applications WHERE submission_date > current_date)
             + (SELECT count(*) FROM workflow_history WHERE performed_at::date > current_date)
             + (SELECT count(*) FROM field_visits WHERE actual_date > current_date)"""),

    ("application number matches its type",
     "documents/tamilnilam_urban_services_and_districts.txt: 0153 is NISD, "
     "0154 ISD, 0155 merge, and the number carries the service code",
     """SELECT count(*) FROM applications
        WHERE NOT (
            (application_type = 'NISD' AND application_number LIKE '%/0153/%') OR
            (application_type = 'ISD'  AND application_number LIKE '%/0154/%') OR
            (application_type = 'MERGE' AND application_number LIKE '%/0155/%'))"""),

    ("application number year matches submission year",
     "the number is issued when the application is submitted",
     """SELECT count(*) FROM applications
        WHERE left(application_number, 4) <> to_char(submission_date, 'YYYY')"""),

    ("every application has an applicant, a survey and an officer",
     "these are NOT NULL in the model and every answer quotes them",
     """SELECT count(*) FROM applications
        WHERE applicant_id IS NULL OR survey_number_id IS NULL
           OR assigned_officer_id IS NULL"""),

    ("assigned officer covers the application's ward",
     "jurisdiction filtering is what stops one officer seeing another's work",
     """SELECT count(*) FROM applications a
        JOIN survey_numbers s ON s.id = a.survey_number_id
        JOIN blocks b   ON b.id = s.block_id
        JOIN officer_jurisdictions j ON j.officer_id = a.assigned_officer_id
        WHERE j.ward_id IS DISTINCT FROM b.ward_id"""),

    ("rejected applications record why",
     "workflow_guide.txt: the officer provides a detailed rejection reason, and "
     "the applicant is notified with it",
     """SELECT count(*) FROM applications a
        WHERE a.current_status = 'rejected'
          AND NOT EXISTS (SELECT 1 FROM workflow_history w
                          WHERE w.application_id = a.id
                            AND w.rejection_reason IS NOT NULL)"""),

    ("sub-division areas sum to the survey area",
     "workflow_guide.txt: SD verifies that the sum of all sub-divisions equals "
     "the original survey area",
     """SELECT count(*) FROM (
            SELECT s.id FROM survey_numbers s
            JOIN sub_divisions d ON d.survey_number_id = s.id
            GROUP BY s.id, s.total_area_sqm
            HAVING abs(sum(d.area_sqm) - s.total_area_sqm) > 0.05) x"""),

    ("no patta transfer left pending on a closed application",
     "transaction_status is '01' when the transfer went through and '02/NN' when "
     "it was refused; reading anything that is not '01' as pending told the "
     "officer that refused transfers were still in flight",
     """SELECT count(*) FROM patta_transfers p
        JOIN applications a ON a.id = p.application_id
        WHERE p.status = 'pending'
          AND a.current_status IN ('approved','rejected')"""),

    ("patta transfers point at a real application and survey",
     "a transfer order is issued against the application that produced it",
     """SELECT count(*) FROM patta_transfers p
        WHERE NOT EXISTS (SELECT 1 FROM applications a WHERE a.id = p.application_id)
           OR NOT EXISTS (SELECT 1 FROM survey_numbers s WHERE s.id = p.survey_number_id)"""),

    ("workflow stages never move backwards",
     "the chain runs SIS -> SD -> DIS -> TAHSILDAR; a hop back means the history "
     "was built wrong",
     """WITH ordered AS (
            SELECT application_id, to_stage, performed_at,
                   row_number() OVER (PARTITION BY application_id ORDER BY performed_at) AS n
            FROM workflow_history
            WHERE to_stage IN ('SIS','SD','DIS','TAHSILDAR')
        ), ranked AS (
            SELECT o.*, CASE to_stage WHEN 'SIS' THEN 1 WHEN 'SD' THEN 2
                                      WHEN 'DIS' THEN 3 ELSE 4 END AS rank
            FROM ordered o
        )
        SELECT count(*) FROM ranked a JOIN ranked b
          ON a.application_id = b.application_id AND b.n = a.n + 1
        WHERE b.rank < a.rank"""),
]


async def check_rules(db) -> list[str]:
    failures = []
    for label, why, sql in RULES:
        offenders = (await db.execute(text(sql))).scalar()
        ok = not offenders
        print(f"  {'ok  ' if ok else 'FAIL'} {label:48s} offenders={offenders}")
        if not ok:
            print(f"        rule: {why}")
            failures.append(f"{label}: {offenders} rows violate it")
    return failures


async def check_answer_consistency(db) -> list[str]:
    """The numbers different answers quote must agree with each other."""
    failures = []
    officers = (await db.execute(
        select(SISOfficer).order_by(SISOfficer.employee_id))).scalars().all()
    from backend.sample_db.test_questions import build_context

    for officer in officers:
        ctx = await build_context(db, officer)
        workload = await postgres.get_officer_workload(db, ctx)
        pending = await postgres.get_pending_applications(db, ctx)
        overdue = await postgres.get_overdue_applications(db, ctx)

        total = workload.get("total_active", 0)
        by_type = sum(workload.get(k, 0) for k in ("ISD", "NISD", "MERGE"))
        checks = [
            ("workload total == sum by type", total == by_type, f"{total} vs {by_type}"),
            ("workload total == pending count", total == pending.get("count", 0),
             f"{total} vs {pending.get('count')}"),
            ("workload overdue == overdue count",
             workload.get("overdue", 0) == overdue.get("count", 0),
             f"{workload.get('overdue')} vs {overdue.get('count')}"),
        ]
        # The listing answers have to agree with the database as well, not just
        # with each other -- a filter that quietly drops or widens the ward is
        # invisible in a cross-check between two answers that share the bug.
        # An officer's queue is what is at their own desk, so the expected count
        # carries the same stage filter get_officer_applications applies.
        queue = """SELECT count(*) FROM applications a
                   JOIN survey_numbers s ON s.id = a.survey_number_id
                   JOIN blocks b ON b.id = s.block_id
                   JOIN officer_jurisdictions j ON j.officer_id = :o
                   WHERE b.ward_id = j.ward_id
                     AND a.current_status <> 'rejected'
                     AND a.current_stage = 'SIS' {extra}"""
        listings = [
            ("queue", {}, ""),
            ("queue pending", dict(status="pending"), "AND a.current_status = 'pending'"),
            ("queue ISD", dict(application_type="ISD"), "AND a.application_type = 'ISD'"),
            ("queue NISD", dict(application_type="NISD"), "AND a.application_type = 'NISD'"),
            ("queue MERGE", dict(application_type="MERGE"), "AND a.application_type = 'MERGE'"),
            ("queue overdue", dict(is_overdue=True), "AND a.is_overdue"),
        ]
        for label, kwargs, extra in listings:
            got = (await postgres.get_officer_applications(db, ctx, **kwargs)).get("count", 0)
            want = (await db.execute(text(queue.format(extra=extra)),
                                     {"o": officer.id})).scalar()
            checks.append((f"{label} == database", got == want, f"{got} vs {want}"))

        for label, ok, detail in checks:
            print(f"  {'ok  ' if ok else 'FAIL'} {officer.employee_id} {label:38s} {detail}")
            if not ok:
                failures.append(f"{officer.employee_id} {label}: {detail}")
    return failures


async def main() -> int:
    async with AsyncSessionLocal() as db:
        dbname = (await db.execute(text("SELECT current_database()"))).scalar()
        print(f"database: {dbname}\n")
        print("[1/2] workflow rules hold in the data")
        failures = await check_rules(db)
        print("\n[2/2] answers agree with each other")
        failures += await check_answer_consistency(db)

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1
    print("all workflow rules hold")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
