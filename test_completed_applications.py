"""Completed-application questions: block, applied date, decision date, status.

A "completed" application is one whose `current_status` is `approved` or
`rejected` (the workflow guide's "completed statuses"). This suite checks the
three things an officer asks about such a file:

  * which block / ward it came from,
  * when it was applied for,
  * when it was approved or rejected, and what its status is.

Ground truth is taken straight from the ORM layer, never from the answer text:

  block / ward   survey_numbers -> blocks -> wards
  applied date   applications.submission_date
  decision date  performed_at of the terminal workflow_history hop, i.e. the
                 one whose to_stage is COMPLETED or REJECTED. Every completed
                 application in the seed has exactly one.
                 patta_transfers.tahsildar_signature_date is NOT usable here:
                 13 completed files carry none, and 21 carry a date that
                 disagrees with the workflow chain (some as early as 2013).

    python test_completed_applications.py            # routing + data + answers
    python test_completed_applications.py --routing  # routing only, no database
    python test_completed_applications.py --data     # routing + data, no answers

No LLM is needed: every intent exercised here renders deterministically.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import structlog
from sqlalchemy import func, select

from backend.database import AsyncSessionLocal
from backend.models import (Application, Block, SISOfficer, SurveyNumber,
                            Ward, WorkflowHistory)
from backend.schemas import OfficerContext
from backend.services.auth_service import get_officer_jurisdiction_ids
from backend.services.chatbot import process_chat, process_chat_stream
from backend.services.chatbot import detect_invalid_app_number
from backend.services.postgres import get_application_detail
from backend.services.rag import parse_intent

# The engine is created with echo=True in development, and echo forces the
# statement log through an InstanceLogger that ignores the logger's own level.
# Only a global disable quiets it, or the SQL drowns the report out.
logging.disable(logging.INFO)
# chatbot.py logs every step through structlog, which prints straight to stdout
# and so is not covered by the disable above.
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))

COMPLETED = ("approved", "rejected")
TERMINAL_STAGES = ("COMPLETED", "REJECTED")


# ── 1. Routing ────────────────────────────────────────────────────────────
# (question, expected intent). {APP} is substituted with a real completed
# application number before the answer layer runs; routing does not need one,
# so a literal placeholder number keeps this layer database-free.
_APP = "2022/0153/28/000468"
_REJ = "2022/0154/28/000156"

ROUTING_CASES = [
    # listing / counting completed work
    ("show completed applications", "pending_applications"),
    ("list my completed applications", "pending_applications"),
    ("show my approved applications", "pending_applications"),
    ("list my rejected applications", "pending_applications"),
    ("how many approved applications do i have", "pending_applications"),
    ("how many rejected applications do i have", "pending_applications"),
    # A channel filter is a listing too. "show application from csc" used to be
    # rejected outright, because "from" was read as a botched application
    # number: "The application number you entered -- from -- is not in a
    # valid format."
    ("show application from csc", "pending_applications"),
    ("show applications from sub registrar", "pending_applications"),
    ("show applications from citizen", "pending_applications"),
    # The plainest per-application channel question. With a number in the
    # middle it used to miss the channel handler and answer "could not find
    # that detail".
    ("how was 2022/0153/28/001487 submitted", "submission_channel_check"),
    ("where was 2022/0153/28/001487 filed", "submission_channel_check"),
    # Follow-ups that point back at the previous message. These named an EVENT
    # rather than a field noun, so the field-keyword rule missed them and they
    # fell through to the LLM -- while "which block is it from", asked in the
    # same breath, resolved the same reference perfectly well.
    ("when was it approved", "application_status"),
    ("when was it rejected", "application_status"),
    ("when was it submitted", "application_status"),
    ("on what date was it approved", "application_status"),
    # Plural follow-ups narrow or count the list already on screen.
    ("how many of them are approved", "pending_applications"),
    ("which of them are isd", "pending_applications"),
    ("do they have igrs number", "pending_applications"),
    ("which block are they from", "pending_applications"),
    # Either route answers this correctly: `pending_applications` counts the
    # approved files, `completion_rate` reports the same number as a ratio.
    ("how many applications have i completed",
     {"completion_rate", "pending_applications"}),
    ("what is my completion rate", "completion_rate"),
    ("முடிக்கப்பட்ட விண்ணப்பங்கள்", "completion_rate"),
    # block / ward of the completed work
    ("which block are my completed applications from", "pending_applications"),
    ("show my approved applications with the block", "pending_applications"),
    # one named application
    (f"which block is {_APP} from", "application_status"),
    (f"which ward is {_APP} in", "application_status"),
    (f"when was {_APP} applied", "application_status"),
    (f"when was {_APP} submitted", "application_status"),
    (f"what is the application date of {_APP}", "application_status"),
    (f"when was {_APP} approved", "application_status"),
    (f"what is the approval date of {_APP}", "application_status"),
    (f"when was {_REJ} rejected", "application_status"),
    (f"what is the status of {_APP}", "application_status"),
    (f"is {_APP} approved", "application_status"),
    (f"what stage is {_APP} at", "application_status"),
    # "my last approved application" belongs to its own intent, not to the
    # per-application lookup above.
    ("when was my last approved application submitted", "last_application"),
    ("when was my last approved application approved", "last_application"),
]


# A word after "application" is only a botched identifier when it could be one.
# (message, the token that must be reported, or None for "no complaint")
INVALID_NUMBER_CASES = [
    ("show application from csc", None),
    ("show applications from citizen", None),
    ("show application from CSC", None),
    ("show application from ward 102", None),
    ("show my approved applications", None),
    # a real attempt at a number is still caught
    ("show application 2026-0154-28", "2026-0154-28"),
    ("show me application xyz-123", "xyz-123"),
]


def run_routing() -> int:
    failures = 0
    print("── Routing ──")
    for question, expected in ROUTING_CASES:
        got = parse_intent(question)
        allowed = expected if isinstance(expected, set) else {expected}
        ok = got in allowed
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {question}\n        -> {got}"
              f"{'' if ok else f' (expected {expected})'}")

    print("── Routing: a filter word is not a botched application number ──")
    for message, want in INVALID_NUMBER_CASES:
        got = detect_invalid_app_number(message)
        ok = got == want
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {message}\n        -> {got!r}"
              f"{'' if ok else f' (expected {want!r})'}")
    return failures


# ── 2. Data ───────────────────────────────────────────────────────────────
async def decision_hop(db, app: Application):
    """The workflow hop that closed the file, or None if the chain never ended."""
    return (await db.execute(
        select(WorkflowHistory)
        .where(WorkflowHistory.application_id == app.id,
               WorkflowHistory.to_stage.in_(TERMINAL_STAGES))
        .order_by(WorkflowHistory.performed_at.desc()).limit(1)
    )).scalars().first()


async def run_data(db) -> tuple[int, dict]:
    """Check the shape of the completed set, and return one file of each kind."""
    failures = 0
    print("\n── Data ──")

    rows = (await db.execute(
        select(Application, Block, Ward)
        .join(SurveyNumber, Application.survey_number_id == SurveyNumber.id)
        .join(Block, SurveyNumber.block_id == Block.id)
        .join(Ward, Block.ward_id == Ward.id)
        .where(Application.current_status.in_(COMPLETED))
    )).all()
    total = len(rows)
    print(f"  completed applications: {total}")
    if not total:
        print("  FAIL  no completed applications — run the seed first")
        return 1, {}

    # (a) every completed file names a block and a ward
    missing_geo = [a.application_number for a, b, w in rows
                   if not b.block_number or not w.ward_number]
    ok = not missing_geo
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  every completed file names a block and ward"
          f"{'' if ok else f' — missing on {missing_geo[:5]}'}")

    # (b) every completed file has an applied date, and it is not in the future
    from datetime import date
    bad_sub = [a.application_number for a, _, _ in rows
               if not a.submission_date or a.submission_date > date.today()]
    ok = not bad_sub
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  every completed file has a sane applied date"
          f"{'' if ok else f' — bad on {bad_sub[:5]}'}")

    # (c) every completed file has exactly one terminal hop, its stage agrees
    #     with the status, and it is not earlier than the applied date
    no_hop, wrong_stage, before_applied = [], [], []
    for app, _b, _w in rows:
        hop = await decision_hop(db, app)
        if hop is None:
            no_hop.append(app.application_number)
            continue
        expected_stage = "COMPLETED" if app.current_status == "approved" else "REJECTED"
        if hop.to_stage != expected_stage:
            wrong_stage.append((app.application_number, app.current_status, hop.to_stage))
        if hop.performed_at.date() < app.submission_date:
            before_applied.append(app.application_number)
    for label, bad in (("every completed file has a terminal workflow hop", no_hop),
                       ("terminal stage agrees with the status", wrong_stage),
                       ("decision date is not before the applied date", before_applied)):
        ok = not bad
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label}"
              f"{'' if ok else f' — {len(bad)} bad, e.g. {bad[:3]}'}")

    # (d) the block/status breakdown the officer would be shown
    from collections import Counter
    breakdown = Counter((b.block_number, w.ward_number, a.current_status)
                        for a, b, w in rows)
    print("  breakdown (block, ward, status):")
    for key in sorted(breakdown):
        print(f"      {key} -> {breakdown[key]}")

    return failures, {}


# ── 3. Answers ────────────────────────────────────────────────────────────
async def officer_context(db, officer) -> OfficerContext:
    jur = await get_officer_jurisdiction_ids(officer.id, db)
    ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
           + jur["ward_ids"] + jur["block_ids"])
    return OfficerContext(
        officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
        email=officer.email, designation=officer.designation,
        jurisdiction_type=jur["jurisdiction_type"],
        jurisdiction_name=jur["jurisdiction_name"],
        jurisdiction_ids=[i for i in ids if i])


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


async def run_answers(db) -> int:
    failures = 0
    print("\n── Answers ──")

    officer_row = (await db.execute(
        select(SISOfficer).where(SISOfficer.is_active.is_(True))
        .order_by(SISOfficer.employee_id).limit(1)
    )).scalars().first()
    if not officer_row:
        print("  FAIL  no officer in the database — run the seed first")
        return 1
    officer = await officer_context(db, officer_row)
    print(f"  officer {officer.name} ({officer.employee_id}), {officer.jurisdiction_name}")

    async def pick(status):
        return (await db.execute(
            select(Application)
            .where(Application.assigned_officer_id == officer_row.id,
                   Application.current_status == status)
            .order_by(Application.submission_date.desc()).limit(1)
        )).scalars().first()

    approved, rejected = await pick("approved"), await pick("rejected")
    if not approved or not rejected:
        print("  FAIL  the officer has no approved and rejected pair to test with")
        return 1

    counts_sub = (await db.execute(
        select(func.count()).select_from(Application).where(
            Application.assigned_officer_id == officer_row.id,
            Application.submission_channel == "sub_registrar",
            Application.current_status != "rejected"))).scalar() or 0

    counts = dict((await db.execute(
        select(Application.current_status, func.count())
        .where(Application.assigned_officer_id == officer_row.id)
        .group_by(Application.current_status)
    )).all())

    geo = {}
    for app in (approved, rejected):
        b, w = (await db.execute(
            select(Block, Ward)
            .join(SurveyNumber, SurveyNumber.block_id == Block.id)
            .join(Ward, Block.ward_id == Ward.id)
            .where(SurveyNumber.id == app.survey_number_id)
        )).first()
        geo[app.application_number] = (b.block_number, w.ward_number,
                                       (await decision_hop(db, app)).performed_at.date())

    a_no, r_no = approved.application_number, rejected.application_number
    a_block, a_ward, a_decided = geo[a_no]
    r_block, r_ward, r_decided = geo[r_no]

    # (question, must-contain substrings, intent it must reach)
    CASES = [
        (f"which block is {a_no} from", [a_block], "application_status"),
        (f"which ward is {a_no} in", [a_ward], "application_status"),
        (f"when was {a_no} applied", [approved.submission_date.isoformat()],
         "application_status"),
        (f"when was {a_no} submitted", [approved.submission_date.isoformat()],
         "application_status"),
        (f"what is the status of {a_no}", ["approved"], "application_status"),
        (f"what is the status of {r_no}", ["rejected"], "application_status"),
        (f"list my rejected applications", [str(counts.get("rejected", 0))],
         "pending_applications"),
        (f"how many approved applications do i have", [str(counts.get("approved", 0))],
         "pending_applications"),
        # The decision date. These are the cases that currently fail: the
        # answer returns the SUBMISSION date for "when was it approved".
        (f"when was {a_no} approved", [a_decided.isoformat()], "application_status"),
        (f"what is the approval date of {a_no}", [a_decided.isoformat()],
         "application_status"),
        (f"when was {r_no} rejected", [r_decided.isoformat()], "application_status"),
    ]

    session_id = str(uuid.uuid4())
    for question, needles, expected_intent in CASES:
        result = await process_chat(db=db, message=question, officer=officer,
                                    session_id=session_id, chat_history=[])
        answer = _strip_html(result.get("response") or "")
        low = answer.lower()
        missing = [n for n in needles if n.lower() not in low]
        ok = result.get("intent") == expected_intent and not missing
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {question}")
        print(f"        intent={result.get('intent')} "
              f"{'' if ok else f'missing={missing} '}"
              f"answer={' '.join(answer.split())[:160]}")

    # Channel provenance. An IGRS Form 6 number exists only where the
    # Sub-Registrar's IGRS raised the mutation off a registered deed, so it is
    # present on every sub_registrar file and on no other -- checked here
    # against the register, then against the answer.
    from backend.models import Application as _App
    chan_rows = dict((await db.execute(
        select(_App.submission_channel, func.count(_App.igrs_form6_number))
        .group_by(_App.submission_channel))).all())
    chan_totals = dict((await db.execute(
        select(_App.submission_channel, func.count())
        .group_by(_App.submission_channel))).all())
    for channel, total in sorted(chan_totals.items()):
        with_igrs = chan_rows.get(channel, 0)
        want = total if channel == "sub_registrar" else 0
        ok = with_igrs == want
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {channel}: {with_igrs}/{total} carry an "
              f"IGRS Form 6 number (expected {want})")

    async def pick_channel(channel):
        return (await db.execute(
            select(Application).where(
                Application.assigned_officer_id == officer_row.id,
                Application.submission_channel == channel).limit(1))).scalars().first()

    sub_app, csc_app = await pick_channel("sub_registrar"), await pick_channel("CSC")
    if sub_app and csc_app:
        CHANNEL_CASES = [
            # the number itself
            (f"what is the igrs form 6 number of {sub_app.application_number}",
             [sub_app.igrs_form6_number], None),
            # and, on a CSC file, why there is none -- "no information found"
            # reads as a hole in the record rather than as the rule
            (f"does {csc_app.application_number} have an igrs form 6 number",
             ["no igrs form 6 number", "sub-registrar"], None),
            (f"how was {sub_app.application_number} submitted",
             ["sub-registrar"], "submission_channel_check"),
        ]
        for question, needles, want_intent in CHANNEL_CASES:
            result = await process_chat(db=db, message=question, officer=officer,
                                        session_id=session_id, chat_history=[])
            answer = _strip_html(result.get("response") or "").lower()
            missing = [n for n in needles if str(n).lower() not in answer]
            ok = not missing and (want_intent is None
                                  or result.get("intent") == want_intent)
            failures += 0 if ok else 1
            print(f"  {'PASS' if ok else 'FAIL'}  {question}")
            print(f"        intent={result.get('intent')} "
                  f"{'' if ok else f'missing={missing} '}"
                  f"answer={' '.join(answer.split())[:150]}")

    # An empty list must say why it is empty when the reason is the standing
    # rejected-exclusion rule, or "no applications found" reads as "none exist".
    # Only one officer in the seed holds a citizen-portal file and it is
    # rejected, so this check goes to whoever owns it rather than to the
    # officer the rest of the suite runs as.
    citizen_owner_id = (await db.execute(
        select(Application.assigned_officer_id).where(
            Application.submission_channel == "citizen",
            Application.current_status == "rejected").limit(1))).scalar()
    citizen_rejected = 0
    if citizen_owner_id:
        citizen_rejected = (await db.execute(
            select(func.count()).select_from(Application).where(
                Application.assigned_officer_id == citizen_owner_id,
                Application.submission_channel == "citizen",
                Application.current_status == "rejected"))).scalar() or 0
    if citizen_rejected:
        citizen_officer = await officer_context(db, (await db.execute(
            select(SISOfficer).where(SISOfficer.id == citizen_owner_id))).scalars().first())
        result = await process_chat(db=db, message="show applications from citizen",
                                    officer=citizen_officer, session_id=session_id,
                                    chat_history=[])
        answer = _strip_html(result.get("response") or "").lower()
        ok = "rejected" in answer and "citizen" in answer
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  empty citizen list explains the "
              f"{citizen_rejected} rejected file(s)")
        print(f"        answer={' '.join(answer.split())[:200]}")

    # ── Follow-ups: the officer refers to the previous message ────────────
    # Each turn is fed the history of the ones before it, exactly as the
    # frontend does. A follow-up must resolve against that history rather than
    # fall through to the LLM or answer from an unrelated default queue.
    print("  -- follow-ups --")
    FOLLOW_UPS = [
        # (opening question, follow-up, what the answer must contain)
        (f"what is the status of {a_no}", "when was it approved",
         [a_no, a_decided.isoformat()]),
        (f"what is the status of {r_no}", "when was it rejected",
         [r_no, r_decided.isoformat()]),
        (f"what is the status of {a_no}", "when was it submitted",
         [a_no, approved.submission_date.isoformat()]),
        ("my last approved application", "when was it approved",
         [a_decided.isoformat()]),
        # A plural follow-up inherits the filters of the question it points
        # back at: this used to answer with the default open queue instead.
        ("show applications from sub registrar", "do they have igrs number",
         ["yes", f"all {counts_sub}", "igrs form 6"]),
        # ...and the same question over a set that has none.
        ("show application from csc", "do they have igrs number",
         ["no", "none of these", "sub-registrar referral"]),
        # A follow-up with no pronoun at all still continues the list: this
        # used to reach the LLM, which had no list to count.
        ("show application from csc", "how many are approved",
         ["approved", "csc"]),
        ("show application from csc", "how many of them are approved",
         ["approved", "csc"]),
        ("show my rejected applications", "which of them are isd",
         ["isd"]),
    ]
    # A fresh question after a list must NOT be folded into it.
    NOT_FOLLOW_UPS = [
        ("show application from csc", "what is the status of " + a_no, a_no),
        ("show application from csc", "how many isd applications do i have", "isd"),
    ]
    for opening, follow_up, needles in FOLLOW_UPS:
        convo_id = str(uuid.uuid4())
        first = await process_chat(db=db, message=opening, officer=officer,
                                   session_id=convo_id, chat_history=[])
        history = [{"role": "user", "content": opening},
                   {"role": "assistant", "content": first.get("response") or "",
                    "intent": first.get("intent")}]
        result = await process_chat(db=db, message=follow_up, officer=officer,
                                    session_id=convo_id, chat_history=history)
        answer = _strip_html(result.get("response") or "").lower()
        missing = [n for n in needles if str(n).lower() not in answer]
        # Falling back to the LLM is the failure this checks for: the handlers
        # answer these deterministically, so an unresolved reference shows up
        # as general_query.
        ok = not missing and result.get("intent") != "general_query"
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  \"{opening}\" -> \"{follow_up}\"")
        print(f"        intent={result.get('intent')} "
              f"{'' if ok else f'missing={missing} '}"
              f"answer={' '.join(answer.split())[:150]}")

    for opening, follow_up, needle in NOT_FOLLOW_UPS:
        convo_id = str(uuid.uuid4())
        first = await process_chat(db=db, message=opening, officer=officer,
                                   session_id=convo_id, chat_history=[])
        history = [{"role": "user", "content": opening},
                   {"role": "assistant", "content": first.get("response") or "",
                    "intent": first.get("intent")}]
        result = await process_chat(db=db, message=follow_up, officer=officer,
                                    session_id=convo_id, chat_history=history)
        answer = _strip_html(result.get("response") or "").lower()
        # The giveaway that it was wrongly folded is the previous question's
        # channel leaking into an answer that never mentioned it.
        ok = needle.lower() in answer and "csc" not in answer
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  not a follow-up: \"{opening}\" -> "
              f"\"{follow_up}\"")
        print(f"        intent={result.get('intent')} "
              f"answer={' '.join(answer.split())[:130]}")

    # The decision-date gate exists twice -- once in process_chat, once in
    # process_chat_stream -- and the frontend only ever sees the streaming one.
    # Check that the two agree.
    for question, want in ((f"when was {a_no} approved", a_decided.isoformat()),
                           (f"when was {r_no} rejected", r_decided.isoformat())):
        parts = []
        async for raw in process_chat_stream(db=db, message=question, officer=officer,
                                             session_id=session_id, chat_history=[]):
            for line in raw.decode("utf-8").splitlines():
                if line.startswith("data: "):
                    payload = json.loads(line[6:])
                    if payload.get("content"):
                        parts.append(payload["content"])
        answer = _strip_html("".join(parts))
        ok = want in answer
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  [stream] {question}")
        print(f"        answer={' '.join(answer.split())[:160]}")

    # A named completed file must also expose its geography and dates through
    # the detail lookup the answers are built on.
    detail = await get_application_detail(db, a_no, officer)
    for key, want in (("block_code", a_block), ("ward_code", a_ward),
                      ("submission_date", approved.submission_date.isoformat()),
                      ("application_status", "approved")):
        ok = str(detail.get(key)) == str(want)
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  detail[{key}] = {detail.get(key)!r}"
              f"{'' if ok else f' (expected {want!r})'}")
    # There is no decision-date key at all today; assert the one it should have.
    ok = "decision_date" in detail or "approval_date" in detail
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  detail carries a decision/approval date "
          f"(expected {a_decided.isoformat()})")

    return failures


async def main_async(layers: set[str]) -> int:
    failures = 0
    if "routing" in layers:
        failures += run_routing()
    if layers & {"data", "answers"}:
        async with AsyncSessionLocal() as db:
            if "data" in layers:
                f, _ = await run_data(db)
                failures += f
            if "answers" in layers:
                failures += await run_answers(db)
    return failures


def main() -> int:
    args = set(sys.argv[1:])
    if "--routing" in args:
        layers = {"routing"}
    elif "--data" in args:
        layers = {"routing", "data"}
    else:
        layers = {"routing", "data", "answers"}
    failures = asyncio.run(main_async(layers))
    print(f"\n{'ALL PASS' if not failures else f'{failures} FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
