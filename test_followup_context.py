"""Implicit follow-ups: no pronoun, no application number, no scope.

Covers the reference-context layer added in
`backend/services/followup_context.py` plus its two orchestration halves
(`_load_followup_context` / `_scoped_list_answer` in chatbot.py and
`get_applications_by_numbers` in postgres.py).

    python test_followup_context.py              # everything (DB, no LLM)
    python test_followup_context.py --routing    # classification only, no DB

What is asserted, and why each case is here:

  * singular follow-ups that name only a FIELD ("what is the applicant name?",
    "which ward?") resolve to the application from the previous turn;
  * a follow-up about the FIELD VISIT ("when was it scheduled?") is answered
    about the visit -- this used to return the application's submission date,
    because "field visit" was in the previous turn and the field map sends
    every "when" to submission_date;
  * "when was approved?" answers from the workflow hop that closed the file,
    not "not on record";
  * list follow-ups ("which is oldest?", "how many are approved?", "show only
    NISD", "what is the total fee?") are answered over EXACTLY the rows shown,
    re-read from the database, and every figure matches independent SQL;
  * a singular follow-up against a multi-row list is AMBIGUOUS and asks;
  * a follow-up with no context at all asks;
  * a fresh question is never treated as a follow-up;
  * carrying an application number forward does NOT bypass jurisdiction.
"""
from __future__ import annotations

import asyncio
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

from sqlalchemy import func, select

from backend.database import AsyncSessionLocal
from backend.models import Application, Block, SISOfficer, SurveyNumber, Ward
from backend.schemas import OfficerContext
from backend.services import followup_context as fctx
from backend.services.auth_service import get_officer_jurisdiction_ids
from backend.services.chatbot import process_chat
from backend.services.postgres import get_applications_by_numbers

ROUTING_ONLY = "--routing" in sys.argv

_APP_NO_RE = re.compile(r"\b\d{4}/\d{3,4}/\d{1,3}/\d+\b")

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    _results.append((bool(ok), label, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n        {detail}" if detail else ""))
    return bool(ok)


def section(title: str) -> None:
    print(f"\n── {title} ──")


def plain(html: str, limit: int = 240) -> str:
    return re.sub(r"<[^>]+>", " ", html or "").replace("\n", " ")[:limit].strip()


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


# ─────────────────────────────────────────────────────────────────────────────
# 1. Classification — pure, no DB
# ─────────────────────────────────────────────────────────────────────────────
CLASSIFY_CASES = [
    # follow-ups with no pronoun and no subject of their own
    ("what is the applicant name?", fctx.FOLLOWUP_SINGULAR),
    ("which ward?", fctx.FOLLOWUP_SINGULAR),
    ("when was it scheduled?", fctx.FOLLOWUP_SINGULAR),
    ("is field visit scheduled?", fctx.FOLLOWUP_SINGULAR),
    ("when was approved?", fctx.FOLLOWUP_SINGULAR),
    ("what is the status?", fctx.FOLLOWUP_SINGULAR),
    ("which is oldest?", fctx.FOLLOWUP_LIST_AGGREGATE),
    ("how many are approved?", fctx.FOLLOWUP_LIST_AGGREGATE),
    ("what is the total fee?", fctx.FOLLOWUP_LIST_AGGREGATE),
    # A plural pronoun on a LIST-shaped question is claimed here too: the
    # stored list answers it over exactly the rows the officer saw, whereas
    # _rescope_list_followup merges it with the previous user message -- which
    # in a chain of follow-ups is another fragment, and the scope resets to the
    # whole desk. That path stays the fallback when no context is stored.
    ("how many of them are approved", fctx.FOLLOWUP_LIST_AGGREGATE),
    ("which of them are ISD", fctx.FOLLOWUP_LIST_AGGREGATE),
    # Plural, but not list-shaped: still left to the existing path, which
    # answers it with _igrs_over_list_note.
    ("do they have an IGRS number", fctx.FOLLOWUP_NONE),
    ("show only NISD", fctx.FOLLOWUP_LIST_REFINE),
    ("just the overdue ones", fctx.FOLLOWUP_LIST_REFINE),
    # picking a row of the table by position -- "what abt 2nd one?" used to
    # classify as NONE and fall through to the agent layer, which timed the
    # stream out on a question it could not ground. ANY position, not just 1-5.
    ("what abt 2nd one ?", fctx.FOLLOWUP_SINGULAR),
    ("the second one", fctx.FOLLOWUP_SINGULAR),
    ("tell me about the first one", fctx.FOLLOWUP_SINGULAR),
    ("what about the last one", fctx.FOLLOWUP_SINGULAR),
    ("the 7th one", fctx.FOLLOWUP_SINGULAR),
    ("row 10", fctx.FOLLOWUP_SINGULAR),
    ("number 12", fctx.FOLLOWUP_SINGULAR),
    ("show me the eleventh one", fctx.FOLLOWUP_SINGULAR),
    ("7வது", fctx.FOLLOWUP_SINGULAR),
    ("ஏழாவது", fctx.FOLLOWUP_SINGULAR),
    ("7vadhu one", fctx.FOLLOWUP_SINGULAR),
    # ...but a bare "last <noun>" is still a question in its own right
    ("what about last month", fctx.FOLLOWUP_NONE),
    ("which is the first to be approved", fctx.FOLLOWUP_LIST_AGGREGATE),
    # one field down the listed column -- a list question over the shown rows
    ("their wards", fctx.FOLLOWUP_LIST_AGGREGATE),
    ("the status of each", fctx.FOLLOWUP_LIST_AGGREGATE),
    ("the CAN number column", fctx.FOLLOWUP_LIST_AGGREGATE),
    ("list the survey numbers", fctx.FOLLOWUP_LIST_AGGREGATE),
    # fresh questions that must NEVER be folded into the previous turn
    ("show my pending ISD applications", fctx.FOLLOWUP_NONE),
    ("how many ISD applications do I have", fctx.FOLLOWUP_NONE),
    ("status of 2026/0154/28/001280", fctx.FOLLOWUP_NONE),
    ("what is my workload", fctx.FOLLOWUP_NONE),
    ("show pending applications", fctx.FOLLOWUP_NONE),
    ("which wards do I cover", fctx.FOLLOWUP_NONE),
    # a long sentence is a question in its own right, cue words notwithstanding
    ("how many applications were filed in June across the whole ward last year",
     fctx.FOLLOWUP_NONE),

    # ---- Tamil ----------------------------------------------------------
    # "…என்ன?" ("what is…?") is the common shape. It used to classify as a
    # fresh question because _OWN_SUBJECT_RE matched "என்" INSIDE "என்ன":
    # the virama (்) is not a word character, so \b found a boundary there.
    ("விண்ணப்பதாரர் பெயர் என்ன?", fctx.FOLLOWUP_SINGULAR),
    ("நிலை என்ன?", fctx.FOLLOWUP_SINGULAR),
    ("எந்த வார்டு?", fctx.FOLLOWUP_SINGULAR),
    ("எப்போது அங்கீகரிக்கப்பட்டது?", fctx.FOLLOWUP_SINGULAR),
    ("கள ஆய்வு எப்போது திட்டமிடப்பட்டது?", fctx.FOLLOWUP_SINGULAR),
    ("எது பழையது?", fctx.FOLLOWUP_LIST_AGGREGATE),
    ("எத்தனை அங்கீகரிக்கப்பட்டவை?", fctx.FOLLOWUP_LIST_AGGREGATE),
    ("மொத்த கட்டணம் என்ன?", fctx.FOLLOWUP_LIST_AGGREGATE),
    ("NISD மட்டும் காட்டு", fctx.FOLLOWUP_LIST_REFINE),
    # ...and a Tamil question that names its own subject stays a question.
    ("எனது நிலுவை விண்ணப்பங்களைக் காட்டு", fctx.FOLLOWUP_NONE),

    # ---- Tanglish (Tamil in Roman script), as officers actually type ----
    ("applicant name enna?", fctx.FOLLOWUP_SINGULAR),
    ("status enna?", fctx.FOLLOWUP_SINGULAR),
    ("edhu ward?", fctx.FOLLOWUP_SINGULAR),
    ("eppo approve pannaanga?", fctx.FOLLOWUP_SINGULAR),
    ("field visit eppo schedule pannirukku?", fctx.FOLLOWUP_SINGULAR),
    ("edhu pazhusu?", fctx.FOLLOWUP_LIST_AGGREGATE),
    # "evlo" has to outrank the "approved" field cue: this is a count over the
    # list, not a question about one record.
    ("evlo approved?", fctx.FOLLOWUP_LIST_AGGREGATE),
    ("total fee evvalavu?", fctx.FOLLOWUP_LIST_AGGREGATE),
    ("NISD mattum kaattu", fctx.FOLLOWUP_LIST_REFINE),
    ("enaku evlo pending applications iruku", fctx.FOLLOWUP_NONE),
]

VISIT_CASES = [
    ("when was it scheduled?", True),
    ("கள ஆய்வு எப்போது திட்டமிடப்பட்டது?", True),
    ("field visit eppo schedule pannirukku?", True),
    ("விண்ணப்பதாரர் பெயர் என்ன?", False),
    ("is field visit scheduled?", True),
    ("when was the inspection?", True),
    ("what is the applicant name?", False),
    ("when was approved?", False),
]


def test_classification() -> None:
    section("1. Follow-up classification (pure)")
    for message, expected in CLASSIFY_CASES:
        got = fctx.classify(message)
        check(got == expected, f"classify: {message!r}", f"-> {got} (want {expected})")

    section("2. Visit-vs-application cue")
    for message, expected in VISIT_CASES:
        got = fctx.asks_about_visit(message)
        check(got == expected, f"asks_about_visit: {message!r}", f"-> {got}")

    section("3. Refinement extraction")
    for message, status, app_type, channel in [
        ("show only NISD", None, "NISD", None),
        ("how many are approved?", "approved", None, None),
        ("just the ISD ones", None, "ISD", None),
        ("only rejected", "rejected", None, None),
        ("NISD மட்டும் காட்டு", None, "NISD", None),
        ("எத்தனை அங்கீகரிக்கப்பட்டவை?", "approved", None, None),
        ("evlo approved?", "approved", None, None),
        ("which is oldest?", None, None, None),
        # Submission channel is a refinement too -- "how many of them are from
        # CSC?" used to match no status/type word and get answered "N of N are
        # matching" over the whole carried set.
        ("how many of them are from CSC?", None, None, "CSC"),
        ("how many are from the sub registrar?", None, None, "sub_registrar"),
        ("show only the e-sevai ones", None, None, "CSC"),
        ("which came through the SRO?", None, None, "sub_registrar"),
        ("how many of them are approved?", "approved", None, None),
    ]:
        got = fctx.refinement(message)
        ok = (got["status"] == status and got["application_type"] == app_type
              and got.get("submission_channel") == channel)
        check(ok, f"refinement: {message!r}", f"-> {got}")


def test_context_record() -> None:
    section("4. Context record round-trip")
    ctx = fctx.build_context("application_status", {
        "application_number": "2026/0154/28/001280", "status": "pending"})
    check(ctx is not None and ctx.entity == fctx.ENTITY_APPLICATION
          and ctx.single_application == "2026/0154/28/001280",
          "an application answer records the application", str(ctx))

    ctx = fctx.build_context("fv_deadline_check", {
        "application_number": "2026/0154/28/001280",
        "query_type": "Field Visit Details"})
    check(ctx is not None and ctx.entity == fctx.ENTITY_FIELD_VISIT,
          "a field-visit answer records the visit as the entity", str(ctx.entity))

    ctx = fctx.build_context("pending_applications", {
        "count": 2, "query_type": "Pending",
        "applications": [{"application_number": "A/1"}, {"application_number": "B/2"}]})
    check(ctx is not None and ctx.entity == fctx.ENTITY_APPLICATION_LIST
          and ctx.application_numbers == ["A/1", "B/2"],
          "a list answer records every row's number", str(ctx.application_numbers))

    # A refusal must leave nothing behind: the next turn must not be able to
    # pick up a number the officer was just told they cannot see.
    for refusal in ({"found": False, "application_number": "X/1"},
                    {"accessible": False, "application_number": "X/1"}):
        check(fctx.build_context("application_status", refusal) is None,
              "a refusal records no referent", str(refusal))

    ctx = fctx.build_context("pending_applications", {
        "applications": [{"application_number": "A/1"}]})
    restored = fctx.FollowupContext.from_json(ctx.to_json())
    check(restored is not None and restored.application_numbers == ["A/1"],
          "the record survives a JSON round-trip")
    check(fctx.FollowupContext.from_json({"version": 999, "entity": "application"}) is None,
          "a record from another version is ignored, not half-read")
    check(fctx.FollowupContext.from_json({"entity": "nonsense", "version": 1}) is None,
          "an unknown entity is ignored")

    section("5. Resolution refuses to guess")
    res = fctx.resolve("what is the applicant name?", None, "en")
    check(not res.resolved and not res.ambiguous,
          "no context at all -> stand aside, let the existing handler ask",
          str(res.kind))

    many = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION_LIST,
                                application_numbers=["A/1", "B/2", "C/3"])
    res = fctx.resolve("what is the applicant name?", many, "en")
    check(res.ambiguous and "3 applications" in (res.clarification or ""),
          "a singular question against 3 rows -> ask, never pick one",
          res.clarification)

    for lang in ("ta", "tanglish"):
        res = fctx.resolve("what is the applicant name?", many, lang)
        check(res.ambiguous and any("஀" <= ch <= "௿" for ch in res.clarification or ""),
              f"the clarification is Tamil for a {lang} turn", res.clarification)
    res = fctx.resolve("what is the applicant name?", many, "en")
    check(res.ambiguous and not any("஀" <= ch <= "௿" for ch in res.clarification or ""),
          "...and English for an English turn", res.clarification)

    one = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION_LIST,
                               application_numbers=["A/1"])
    res = fctx.resolve("what is the applicant name?", one, "en")
    check(res.resolved and res.application_number == "A/1",
          "a singular question against a one-row list is unambiguous")

    res = fctx.resolve("how many are approved?", many, "en")
    check(res.resolved and res.application_numbers == ["A/1", "B/2", "C/3"]
          and res.status == "approved",
          "an aggregate carries the whole set plus the status asked for")

    res = fctx.resolve("show my pending ISD applications", many, "en")
    check(not res.resolved and not res.ambiguous,
          "a fresh question is left alone entirely")

    # A bare aggregate naming a lifecycle status the carried listing is filtered
    # AWAY from is a fresh count, not a refinement: "show my approved
    # applications" -> "how many have been rejected?" must not answer "none of
    # those 52". Guard only when there is no plural pointer.
    approved_list = fctx.FollowupContext(
        entity=fctx.ENTITY_APPLICATION_LIST, application_numbers=["A/1", "B/2"],
        query_type="Approved Applications")
    res = fctx.resolve("how many have been rejected?", approved_list, "en")
    check(not res.resolved and not res.ambiguous,
          "a status-contradicting aggregate is left to route fresh")
    res = fctx.resolve("how many of them are rejected?", approved_list, "en")
    check(res.resolved and res.status == "rejected",
          "...but an explicit plural pointer keeps the scope")
    res = fctx.resolve("how many are approved?", approved_list, "en")
    check(res.resolved and res.status == "approved",
          "the same status as the listing still scopes")
    res = fctx.resolve("how many are ISD?", approved_list, "en")
    check(res.resolved and res.application_type == "ISD",
          "a bare type refinement over a status listing still scopes (trivial 0)")

    # A contradicting TYPE, but only when the fragment also names a status so it
    # routes fresh cleanly. "how many approved ISD ...?" -> "how many approved
    # NISD?" must not answer "none of those 2".
    isd_list = fctx.FollowupContext(
        entity=fctx.ENTITY_APPLICATION_LIST, application_numbers=["A/1", "B/2"],
        query_type="Approved ISD Applications")
    res = fctx.resolve("how many approved NISD?", isd_list, "en")
    check(not res.resolved and not res.ambiguous,
          "a status+type-contradicting aggregate routes fresh")
    res = fctx.resolve("how many NISD?", isd_list, "en")
    check(res.resolved,
          "...but a bare type contradiction (no status) still scopes")

    # A PERIOD the carried listing does not name -> fresh jurisdiction count.
    res = fctx.resolve("how many did I approve in 2024?", isd_list, "en")
    check(not res.resolved and not res.ambiguous,
          "an aggregate naming an unshared year routes fresh")
    june_list = fctx.FollowupContext(
        entity=fctx.ENTITY_APPLICATION_LIST, application_numbers=["A/1", "B/2"],
        query_type="All Applications in June 2026")
    res = fctx.resolve("how many are approved?", june_list, "en")
    check(res.resolved, "a listing already scoped to June still takes 'approved'")
    res = fctx.resolve("how many were filed in June?", june_list, "en")
    check(res.resolved, "...and a follow-up naming that same June still scopes")

    # "what abt 2nd one?" -- the officer pointing at a row of the table by
    # position. This used to classify as NONE and fall through to the agent
    # layer, which then timed the stream out on a question it could not ground.
    res = fctx.resolve("what abt 2nd one ?", many, "en")
    check(res.resolved and res.application_number == "B/2",
          "an ordinal picks the row it names, not a clarification", res.application_number)
    res = fctx.resolve("what about the last one", many, "en")
    check(res.resolved and res.application_number == "C/3",
          "'the last one' picks the final row shown", res.application_number)

    # Any Nth, not just 1-5.
    many7 = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION_LIST,
                                 application_numbers=[f"N/{i}" for i in range(1, 8)])
    res = fctx.resolve("row 6", many7, "en")
    check(res.resolved and res.application_number == "N/6",
          "'row 6' picks the sixth carried row", res.application_number)
    res = fctx.resolve("ஏழாவது", many7, "ta")
    check(res.resolved and res.application_number == "N/7",
          "the Tamil ordinal picks the seventh row", res.application_number)

    # A position past the end of the list -> ask, naming how many rows there were.
    res = fctx.resolve("the 9th one", many, "en")
    check(res.ambiguous and "only 3" in (res.clarification or ""),
          "'the 9th one' of a 3-row list asks, stating the real count",
          res.clarification)
    res = fctx.resolve("the 9th one", many, "ta")
    check(res.ambiguous and any("஀" <= ch <= "௿" for ch in res.clarification or ""),
          "...and asks in Tamil for a Tamil turn", res.clarification)

    # Field projection -> the whole set is carried, answered as a column.
    res = fctx.resolve("their wards", many, "en")
    check(res.resolved and res.entity == fctx.ENTITY_APPLICATION_LIST
          and res.application_numbers == ["A/1", "B/2", "C/3"],
          "'their wards' carries the whole shown set for a column answer")

    # ── A RUN of rows: "show both", "the first two", "last three" ────────────
    # `ordinal_pick` answers "the 2nd one" -- one row. An officer looking at a
    # ten-row table asks for the top of it just as often, and at a two-row one
    # says "both". Neither was understood: "first two" matched the word "first"
    # and answered about row 1 alone, and "show both applications" tripped the
    # own-subject gate on "applications" so it was not a follow-up at all --
    # both were answered by re-running the officer's whole open queue.
    ten = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION_LIST,
                               application_numbers=[f"T/{i}" for i in range(1, 11)])
    pair = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION_LIST,
                                application_numbers=["P/1", "P/2"])

    res = fctx.resolve("show both applications", pair, "en")
    check(res.resolved and res.full_details
          and res.application_numbers == ["P/1", "P/2"],
          "'show both applications' shows the two rows, not a count of them",
          str(res.application_numbers))
    res = fctx.resolve("show first two applications", ten, "en")
    check(res.resolved and res.full_details
          and res.application_numbers == ["T/1", "T/2"],
          "'the first two' of a ten-row list is rows 1-2, not row 1",
          str(res.application_numbers))
    res = fctx.resolve("show me the first 2 applications details", ten, "en")
    check(res.resolved and res.application_numbers == ["T/1", "T/2"],
          "...and a digit count reads the same as the word",
          str(res.application_numbers))
    res = fctx.resolve("show last two applications", ten, "en")
    check(res.resolved and res.application_numbers == ["T/9", "T/10"],
          "'the last two' takes them off the end, in the order shown",
          str(res.application_numbers))
    res = fctx.resolve("first three", ten, "en")
    check(res.resolved and res.application_numbers == ["T/1", "T/2", "T/3"],
          "'first three' carries three rows")

    # "both" says how many there are as well as which. Against a list that does
    # not hold exactly two it is not a slice, and must not be guessed at.
    check(fctx.slice_pick("show both", [f"T/{i}" for i in range(1, 11)]) is None,
          "'both' against a ten-row list is not a slice")
    # A single row stays `ordinal_pick`'s job -- answering it here would turn
    # "the first one" into a list.
    check(fctx.slice_pick("the first one", [f"T/{i}" for i in range(1, 11)]) is None,
          "'the first one' is still a single pick, not a run")
    res = fctx.resolve("what abt 2nd one ?", ten, "en")
    check(res.resolved and res.application_number == "T/2",
          "...and a plain ordinal still picks exactly one row")

    # "show me all of them" / "list them all" -- the whole table, as records.
    for msg in ("show me all of them", "list them all"):
        res = fctx.resolve(msg, ten, "en")
        check(res.resolved and res.full_details
              and res.application_numbers == [f"T/{i}" for i in range(1, 11)],
              f"{msg!r} shows every carried row", str(len(res.application_numbers)))
    # ...but only when the officer asked to SEE them. The same rows with a
    # question attached is a question: answering it with ten records instead of
    # a yes/no would be a wall of text in place of a word.
    res = fctx.resolve("are all of them approved?", ten, "en")
    check(res.resolved and not res.full_details,
          "'are all of them approved?' stays a question, not ten records")
    # Past the per-row ceiling a wall of records is worse than the count, and
    # truncating to ten while the officer asked for ALL would be simply wrong.
    big = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION_LIST,
                               application_numbers=[f"B/{i}" for i in range(1, 31)])
    check(fctx.slice_pick("show me all of them", big.application_numbers) is None,
          "'all of them' over a 30-row list is left to the count, never truncated")

    # Alternate rows, by the position each row was shown at.
    res = fctx.resolve("show even rows", ten, "en")
    check(res.resolved and res.application_numbers == ["T/2", "T/4", "T/6", "T/8", "T/10"],
          "'even rows' takes positions 2, 4, 6 ...", str(res.application_numbers))
    res = fctx.resolve("odd rows", ten, "en")
    check(res.resolved and res.application_numbers == ["T/1", "T/3", "T/5", "T/7", "T/9"],
          "'odd rows' takes positions 1, 3, 5 ...", str(res.application_numbers))
    res = fctx.resolve("alternate rows", ten, "en")
    check(res.resolved and res.application_numbers == ["T/1", "T/3", "T/5", "T/7", "T/9"],
          "'alternate rows' starts at the first row")

    # ── A TYPO'd subject noun is still a subject ────────────────────────────
    # The own-subject gate matched its nouns exactly while the rest of the
    # pipeline matches keywords through `is_token_typo_match`. So "applciaiton
    # from csc" -- a plain listing request with two letters transposed -- failed
    # the gate, was taken for a bare follow-up (the word "csc" is a field cue),
    # and came back as "That answer covered 24 applications. Which one do you
    # mean?": a clarification in place of the list, for a question that named
    # its own subject perfectly clearly.
    listing24 = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION_LIST,
                                     application_numbers=[f"X/{i}" for i in range(1, 25)])
    for typo in ("applciaiton from csc", "aplications from csc",
                 "applicatoin from csc"):
        res = fctx.resolve(typo, listing24, "en")
        check(not res.ambiguous,
              f"{typo!r} is a fresh listing request, not a 'which one?'",
              (res.clarification or "")[:60])
    # `applicant` is the trap: close enough to `application` to fall inside the
    # edit budget, and a different word entirely. "what is the applicant name?"
    # is the canonical singular follow-up this module exists to resolve.
    for real_word in ("what is the applicant name?", "the applicant mobile",
                      "applicant address"):
        check(not fctx._has_own_subject(real_word),
              f"{real_word!r} is not read as a typo of 'application'")
    check(fctx.resolve("what is the applicant name?", listing24, "en").kind
          != fctx.FOLLOWUP_NONE,
          "...so an applicant-name follow-up still resolves against the list")

    # ── "i need all" ────────────────────────────────────────────────────────
    # What an officer types when the assistant has just asked "which one do you
    # mean?". It fell to the LLM and came back with a COUNT ("There are 30
    # applications from CSC.") in place of the list -- and a count that
    # disagreed with the 24 rows the clarification had just named.
    for msg in ("i need all", "all", "all please", "i want all of them",
                "give me all"):
        res = fctx.resolve(msg, ten, "en")
        check(res.resolved and res.full_details
              and len(res.application_numbers) == 10,
              f"{msg!r} shows the rows, not a count of them",
              f"resolved={res.resolved} rows={len(res.application_numbers)}")
    # A bare "all" can only mean the rows in view. "show all applications" names
    # the register, and stays a fresh question.
    check(not fctx.resolve("show all applications", ten, "en").resolved,
          "'show all applications' still asks the register, not the rows shown")

    # ── "show their details" / "show details" ───────────────────────────────
    # `resolve` has had a branch for a details request over the listed rows
    # since "details of both", but `classify` could never reach it: none of
    # these carries an aggregate word, an ordinal or a field cue, so it called
    # them NONE and the turn fell through to a handler with no application
    # number, which asked for one. Nine of the ten ways an officer writes this
    # failed. The tenth worked by accident -- "can I get the details" contains
    # the field cue "can".
    for msg in ("show their details", "show details", "show the details",
                "give me the details", "details", "full details",
                "show me their full details", "can i get the full details"):
        res = fctx.resolve(msg, ten, "en")
        check(res.resolved and res.full_details
              and len(res.application_numbers) == 10,
              f"{msg!r} shows the records behind the rows",
              f"resolved={res.resolved} full={res.full_details} "
              f"n={len(res.application_numbers)} kind={fctx.classify(msg)}")
    # Naming a subject still wins -- these are complete requests, not
    # continuations.
    for msg in ("show details of 2026/0154/28/001167",
                "show details of my pending applications"):
        check(fctx.classify(msg) == fctx.FOLLOWUP_NONE,
              f"{msg!r} names its own subject and stays a fresh question",
              fctx.classify(msg))

    # ── A bare ordinal keeps the field the question before it asked for ─────
    # "what is the name of the first applicant" then "second" is the officer
    # moving down the list, not changing the question -- it was answered with
    # the whole record, burying the one field they asked for in twenty they did
    # not. A bare ordinal after a plain LISTING still means that record, which
    # is the distinction the rewrite has to keep.
    from backend.services.chatbot import _prev_field_question as _pfq
    for prev, want_in in (("what is the name of first applicant", "name"),
                          ("what is the ward of the first one", "ward"),
                          ("what is the applicant mobile of the 2nd one", "mobile")):
        got = _pfq(prev)
        check(got is not None and want_in in (got or "").lower(),
              f"a later row re-asks {want_in!r} from {prev!r}", str(got))
        check(got is not None and not re.search(
            r"\b(first|second|2nd|the 2nd)\b", got, re.I),
            "...with the old ordinal stripped", str(got))
    # The previous question's application number must not travel with it, or
    # the rewritten question names two.
    got = _pfq("what is the name of 2026/0154/28/001167")
    check(got is not None and "2026/0154/28/001167" not in got,
          "...and its application number stripped", str(got))
    # A previous turn that named no field gives nothing to carry: a bare
    # ordinal after "show my applications" means the record.
    for prev in ("show my applications", "show applications from csc", ""):
        check(_pfq(prev) is None,
              f"{prev!r} leaves no field to carry", str(_pfq(prev)))

    # ── A misspelled follow-up is still that follow-up ──────────────────────
    # Officers type fast, and a follow-up is the worst place to be brittle about
    # it: the message is a fragment, so one wrong letter is the whole signal
    # gone. Measured against a ten-row listing, only 9 of these 29 shapes
    # survived a single typo before the correction pass went in -- "what is the
    # wrad", "the secnd one", "thier wards" and "the staus of each" stopped
    # being follow-ups at all and fell through to the LLM.
    #
    # The test is a COMPARISON, not a list of expected verdicts: whatever the
    # module does with the correct spelling, it must do with the typo. That way
    # the check keeps its meaning as the surrounding behaviour changes.
    def _shape(msg):
        r = fctx.resolve(msg, ten, "en")
        return (fctx.classify(msg), r.resolved, r.ambiguous, r.full_details,
                r.per_row_field, len(r.application_numbers), r.application_number)

    typo_pairs = [
        ("what is the applicant name", "what is the aplicant nmae"),
        ("what is the ward", "what is the wrad"),
        ("when was it approved", "when was it aproved"),
        ("what is the survey number", "what is the survy numbr"),
        ("what is the status", "what is the staus"),
        ("what is the address", "what is the adress"),
        ("how many are approved", "how many are aproved"),
        ("which is the oldest", "which is the oldst"),
        ("which took the longest", "which took the longst"),
        ("what is the total fee", "what is the totl fee"),
        ("show only nisd", "show only nsid"),
        ("just the approved ones", "just the aproved ones"),
        ("the second one", "the secnd one"),
        ("the first one", "the frist one"),
        ("show first two", "show frist two"),
        ("show both applications", "show both aplications"),
        ("details of both", "detials of both"),
        ("show me all of them", "show me all of thm"),
        ("their wards", "thier wards"),
        ("the status of each", "the staus of each"),
        ("list the survey numbers", "list the survy numbers"),
        ("when was it scheduled", "when was it schedled"),
        ("is the field visit scheduled", "is the feild visit scheduled"),
    ]
    for good, bad in typo_pairs:
        check(_shape(good) == _shape(bad),
              f"{bad!r} behaves like {good!r}",
              f"{_shape(good)} vs {_shape(bad)}")

    # The correction must not invent meaning that was not typed. `applicant` and
    # `application` are one short edit apart and decide whether a message is a
    # follow-up at all, so neither may be "corrected" into the other; and a word
    # that is nobody's cue must survive untouched.
    check(fctx.correct_spelling("what is the applicant name")
          == "what is the applicant name",
          "a correctly-spelled message is returned unchanged")
    check("application" not in fctx.correct_spelling("the applicant address"),
          "'applicant' is never rewritten to 'application'")
    for untouched in ("i need all", "send it", "give me that", "i want this"):
        check(fctx.correct_spelling(untouched) == untouched,
              f"{untouched!r} survives the correction pass unchanged",
              fctx.correct_spelling(untouched))
    # Tamil is matched exactly on purpose -- single glyphs carry too much
    # meaning for an edit budget -- so Tamil text must pass through verbatim.
    for tamil in ("பரப்பளவு என்ன?", "எது பழையது?", "விண்ணப்பங்கள் எத்தனை?"):
        check(fctx.correct_spelling(tamil) == tamil,
              "Tamil passes through the correction pass verbatim", tamil)


# ─────────────────────────────────────────────────────────────────────────────
# Database-backed behaviour
# ─────────────────────────────────────────────────────────────────────────────
async def test_scoped_query(db, officer, foreign_number) -> None:
    section("6. Scoped re-query is authorization-safe")
    own = (await db.execute(
        select(Application.application_number)
        .where(Application.assigned_officer_id == officer.officer_id)
        .limit(3))).scalars().all()

    expected_own = (await db.execute(
        select(func.count()).select_from(Application)
        .where(Application.application_number.in_(list(own)),
               Application.current_status != "rejected"))).scalar_one()
    res = await get_applications_by_numbers(db, officer, list(own))
    check(res.get("error") is None and res.get("count") == expected_own,
          f"the officer's own applications come back ({expected_own} of "
          f"{len(own)} once rejected files are excluded)",
          f"count={res.get('count')} err={res.get('error')}")

    if foreign_number:
        res = await get_applications_by_numbers(db, officer, [foreign_number])
        rows = res.get("applications") or []
        check(res.get("count") == 0 and not rows,
              f"a number from outside the jurisdiction ({foreign_number}) is dropped, "
              f"not returned", f"count={res.get('count')} dropped={res.get('dropped')}")
        # And mixed in with legitimate ones it is still dropped.
        res = await get_applications_by_numbers(db, officer, list(own) + [foreign_number])
        returned = {r["application_number"] for r in (res.get("applications") or [])}
        check(foreign_number not in returned,
              "a foreign number smuggled into a carried set is still dropped",
              f"returned={len(returned)}")

    # Rejected files stay out of an operational list unless asked for.
    rejected = (await db.execute(
        select(Application.application_number)
        .where(Application.assigned_officer_id == officer.officer_id,
               Application.current_status == "rejected").limit(2))).scalars().all()
    if rejected:
        res = await get_applications_by_numbers(db, officer, list(rejected))
        check(res.get("count") == 0,
              "rejected applications stay out unless the status asks for them",
              f"count={res.get('count')}")
        res = await get_applications_by_numbers(db, officer, list(rejected),
                                                status="rejected")
        check(res.get("count") == len(rejected),
              "...and come back when they are asked for by name",
              f"count={res.get('count')}")


async def _converse(db, officer, turns):
    """Run a conversation, returning (question, result) for each turn."""
    sid = str(uuid.uuid4())
    history: list = []
    out = []
    for q in turns:
        r = await process_chat(q, sid, officer, db, list(history))
        out.append((q, r))
        history.append({"role": "user", "content": q})
        history.append({"role": "assistant", "content": r.get("response")})
    return out


async def test_singular_followups(db, officer, app_number) -> None:
    section("7. Singular follow-ups (no pronoun, no number)")
    detail = (await db.execute(
        select(Application).where(Application.application_number == app_number)
    )).scalars().first()

    convo = await _converse(db, officer, [
        f"give details for {app_number}", "what is the applicant name?"])
    answer = plain(convo[1][1].get("response"))
    check(app_number in answer,
          "'what is the applicant name?' resolves the prior application", answer)

    convo = await _converse(db, officer, [
        f"give details for {app_number}", "which ward?"])
    answer = plain(convo[1][1].get("response"))
    check(app_number in answer, "'which ward?' resolves the prior application", answer)

    # The case that used to answer with the submission date.
    convo = await _converse(db, officer, [
        f"show the field visit for {app_number}", "when was it scheduled?"])
    answer = plain(convo[1][1].get("response"))
    submitted = detail.submission_date.isoformat() if detail.submission_date else "@@"
    check("field visit" in answer.lower() and submitted not in answer,
          "'when was it scheduled?' answers about the VISIT, not the filing date",
          answer)

    # The case that used to say "not on record".
    approved = (await db.execute(
        select(Application.application_number)
        .where(Application.assigned_officer_id == officer.officer_id,
               Application.current_status == "approved").limit(1))).scalars().first()
    if approved:
        from backend.services.postgres import get_application_detail
        expected = (await get_application_detail(db, approved, officer)).get("decision_date")
        convo = await _converse(db, officer, [
            f"status of {approved}", "when was approved?"])
        answer = plain(convo[1][1].get("response"))
        check(bool(expected) and str(expected) in answer,
              f"'when was approved?' gives the decision date ({expected})", answer)


async def test_list_followups(db, officer) -> None:
    section("8. List follow-ups scoped to the rows already shown")
    first = await _converse(db, officer, ["show my approved applications"])
    shown = first[0][1]
    listed = [r["application_number"]
              for r in ((shown.get("table_data") or {}).get("applications") or [])]

    approved_total = (await db.execute(
        select(func.count()).select_from(Application)
        .where(Application.assigned_officer_id == officer.officer_id,
               Application.current_status == "approved"))).scalar_one()

    convo = await _converse(db, officer, ["show my approved applications", "which is oldest?"])
    answer = plain(convo[1][1].get("response"))
    oldest = (await db.execute(
        select(Application.application_number)
        .where(Application.assigned_officer_id == officer.officer_id,
               Application.current_status == "approved")
        .order_by(Application.submission_date.asc()).limit(1))).scalars().first()
    check(convo[1][1].get("intent") == "followup_list",
          "'which is oldest?' is answered from the carried list", answer)
    check(oldest and oldest in answer,
          f"...and names the register's oldest ({oldest})", answer)

    convo = await _converse(db, officer, ["show my approved applications",
                                          "what is the total fee?"])
    answer = plain(convo[1][1].get("response"))
    fee_sum = (await db.execute(
        select(func.sum(Application.fee_amount))
        .where(Application.assigned_officer_id == officer.officer_id,
               Application.current_status == "approved"))).scalar_one() or 0
    check(f"{float(fee_sum):,.2f}" in answer,
          f"'what is the total fee?' totals only the listed set (₹{float(fee_sum):,.2f})",
          answer)

    convo = await _converse(db, officer, ["show applications filed in June", "show only NISD"])
    answer = plain(convo[1][1].get("response"))
    # The rows the officer actually saw, read off the rendered first answer --
    # independent of how table_data happens to be plumbed.
    june_rows = _APP_NO_RE.findall(convo[0][1].get("response") or "")
    nisd = await get_applications_by_numbers(db, officer, june_rows, application_type="NISD")
    check(convo[1][1].get("intent") == "followup_list",
          "'show only NISD' is answered from the carried June list", answer)
    check(bool(june_rows) and str(nisd.get("count")) in answer,
          f"...and counts only the {len(june_rows)} June row(s) "
          f"({nisd.get('count')} NISD)", answer)

    section("9. Ambiguity is asked about, never guessed")
    convo = await _converse(db, officer, ["show my approved applications",
                                          "what is the applicant name?"])
    answer = plain(convo[1][1].get("response"))
    check(convo[1][1].get("intent") == "followup_clarification"
          and str(approved_total) in answer,
          "a singular question against a multi-row list asks which one", answer)

    # With no context at all this layer stands aside; the existing handler
    # asks for the number. What matters is that the officer is asked and that
    # no application is invented -- not which layer does the asking.
    convo = await _converse(db, officer, ["what is the applicant name?"])
    answer = plain(convo[0][1].get("response"))
    asked = "specify" in answer.lower() or "which application" in answer.lower()
    invented = _APP_NO_RE.findall(answer)
    check(asked and not [n for n in invented if "0154/02" not in n],
          "a follow-up with no conversation at all asks, and invents nothing",
          answer)

    section("10. Fresh questions are not folded into the previous turn")
    convo = await _converse(db, officer, ["show my approved applications",
                                          "how many ISD applications do I have"])
    check(convo[1][1].get("intent") not in ("followup_list", "followup_clarification"),
          "'how many ISD applications do I have' routes as its own question",
          f"intent={convo[1][1].get('intent')}")


async def test_ordinal_completed_followups(db, officer) -> None:
    """Picking a row by position out of a completed-application listing.

    Every expectation is recomputed from the register, so this still means
    something after a reseed. The bug this covers: "what abt 2nd one?" used to
    classify as nothing, fall through to the agent layer, and time the stream
    out (`AbortError: BodyStreamBuffer was aborted`).
    """
    section("10b. Ordinal follow-ups on completed applications")

    for status in ("approved", "rejected"):
        convo = await _converse(db, officer, [f"show my {status} applications"])
        # The rendered table names each row's application number twice (once in
        # the onclick handler, once as the link text). Read the numbers off the
        # tag-stripped text and de-duplicate, preserving order, so `shown` is
        # the list of rows the officer actually saw.
        _seen: set[str] = set()
        shown = [n for n in _APP_NO_RE.findall(plain(convo[0][1].get("response"), 100000))
                 if not (n in _seen or _seen.add(n))]
        if len(shown) < 2:
            check(True, f"(skipped {status}: officer has < 2 shown)", str(shown))
            continue

        # "the 2nd one" must resolve to exactly the second row that was shown,
        # and the register must agree it has that status.
        target = shown[1]
        db_status = (await db.execute(
            select(Application.current_status)
            .where(Application.application_number == target))).scalar_one_or_none()
        convo = await _converse(db, officer, [
            f"show my {status} applications", "what abt the 2nd one?"])
        answer = plain(convo[1][1].get("response"), 400)
        check(target in answer,
              f"'the 2nd one' of the {status} list resolves to the 2nd shown "
              f"row ({target})", answer)
        check(db_status == status,
              f"...and the register confirms {target} is {status} "
              f"(db={db_status})")

        # A position past the end -> a clarification that states the real count,
        # never a guessed row and never a crash.
        convo = await _converse(db, officer, [
            f"show my {status} applications", "show me the 99th one"])
        answer = plain(convo[1][1].get("response"), 400)
        check(convo[1][1].get("intent") == "followup_clarification"
              and str(len(shown)) in answer
              and not [n for n in _APP_NO_RE.findall(answer) if n not in shown],
              f"'the 99th one' of a {len(shown)}-row {status} list asks and "
              f"invents no number", answer)

        # "their wards" -> one line per shown row, each ward matching the DB.
        convo = await _converse(db, officer, [
            f"show my {status} applications", "their wards"])
        # No truncation: the column answer names one row per shown application,
        # and the check below looks for every one of them.
        answer = plain(convo[1][1].get("response"), 100000)
        check(convo[1][1].get("intent") == "followup_list",
              f"'their wards' after the {status} list is a scoped column answer",
              answer[:1200])
        ok = True
        for num in shown:
            ward = (await db.execute(
                select(Ward.ward_number).select_from(Application)
                .join(SurveyNumber, Application.survey_number_id == SurveyNumber.id)
                .join(Block, SurveyNumber.block_id == Block.id)
                .join(Ward, Block.ward_id == Ward.id)
                .where(Application.application_number == num))).scalar_one_or_none()
            if ward and (num not in answer or str(ward) not in answer):
                ok = False
        check(ok, f"...and every shown row's ward matches the register", answer)


async def test_multilingual(db, officer, app_number) -> None:
    """The same follow-ups, asked in Tamil and in Tanglish.

    An officer switches script mid-conversation without thinking about it, so
    the previous turn being in English must not stop a Tamil follow-up from
    resolving -- the context is data, not text, and carries across scripts.
    """
    section("12. Tamil and Tanglish follow-ups")

    oldest = (await db.execute(
        select(Application.application_number)
        .where(Application.assigned_officer_id == officer.officer_id,
               Application.current_status == "approved")
        .order_by(Application.submission_date.asc()).limit(1))).scalars().first()
    fee_sum = (await db.execute(
        select(func.sum(Application.fee_amount))
        .where(Application.assigned_officer_id == officer.officer_id,
               Application.current_status == "approved"))).scalar_one() or 0

    # An English listing, then the follow-up in each script.
    for label, question in (("Tamil", "எது பழையது?"), ("Tanglish", "edhu pazhusu?")):
        convo = await _converse(db, officer, ["show my approved applications", question])
        answer = plain(convo[1][1].get("response"))
        check(convo[1][1].get("intent") == "followup_list" and oldest in answer,
              f"{label} 'which is oldest' names the register's oldest ({oldest})",
              answer)

    for label, question in (("Tamil", "மொத்த கட்டணம் என்ன?"),
                            ("Tanglish", "total fee evvalavu?")):
        convo = await _converse(db, officer, ["show my approved applications", question])
        answer = plain(convo[1][1].get("response"))
        check(f"{float(fee_sum):,.2f}" in answer,
              f"{label} 'total fee' totals only the listed set "
              f"(₹{float(fee_sum):,.2f})", answer)

    # Ambiguity must be raised in the officer's own script.
    for label, question, tamil_expected in (
            ("Tamil", "விண்ணப்பதாரர் பெயர் என்ன?", True),
            ("Tanglish", "applicant name enna?", True),
            ("English", "what is the applicant name?", False)):
        convo = await _converse(db, officer, ["show my approved applications", question])
        answer = plain(convo[1][1].get("response"))
        is_tamil_text = any("஀" <= ch <= "௿" for ch in answer)
        check(convo[1][1].get("intent") == "followup_clarification"
              and is_tamil_text == tamil_expected,
              f"{label} ambiguity is asked about in the officer's own script",
              answer[:120])

    # Per-application follow-ups, previous turn in English.
    for label, question in (("Tamil", "விண்ணப்பதாரர் பெயர் என்ன?"),
                            ("Tanglish", "applicant name enna?")):
        convo = await _converse(db, officer, [f"status of {app_number}", question])
        answer = plain(convo[1][1].get("response"))
        check(app_number in answer and "specify" not in answer.lower(),
              f"{label} field question resolves the prior application", answer)

    for label, question in (("Tamil", "எந்த வார்டு?"), ("Tanglish", "edhu ward?")):
        convo = await _converse(db, officer, [f"status of {app_number}", question])
        answer = plain(convo[1][1].get("response"))
        check(app_number in answer, f"{label} 'which ward?' resolves", answer)

    # A Tamil refinement keeps the scope of the English listing before it.
    convo = await _converse(db, officer, ["show my applications filed in June",
                                          "NISD மட்டும் காட்டு"])
    answer = plain(convo[1][1].get("response"))
    june_rows = _APP_NO_RE.findall(convo[0][1].get("response") or "")
    nisd = await get_applications_by_numbers(db, officer, june_rows,
                                             application_type="NISD")
    check(convo[1][1].get("intent") == "followup_list"
          and str(nisd.get("count")) in answer,
          f"Tamil 'NISD only' keeps the June scope ({nisd.get('count')} NISD)",
          answer)
    # The Tamil sentence must actually read -- a bare count used to render as
    # "அந்த 34 விண்ணப்பங்களில் 34 ." (a number, a space and a full stop).
    check(not re.search(r"\d\s+\.", answer) and "  " not in answer,
          "the Tamil sentence is well formed", answer)


async def test_no_leak_across_officers(db, officer_a, officer_b, number_of_a) -> None:
    section("11. Context never crosses officers")
    # Officer B asks a bare follow-up in a brand-new session. There is no
    # context of their own, and none of A's may be visible.
    convo = await _converse(db, officer_b, ["what is the applicant name?"])
    answer = plain(convo[0][1].get("response"))
    check(number_of_a not in answer,
          f"{officer_b.name} sees nothing of {officer_a.name}'s application", answer)


async def test_details_over_listed(db, officer, other_number) -> None:
    section("12. \"Give me the details\" over the rows just listed")
    # Any officer whose pending desk holds a handful of files will do; the
    # first active officer's may be empty.
    listed: list = []
    for cand in (await db.execute(
            select(SISOfficer).where(SISOfficer.is_active == True))).scalars().all():
        n = (await db.execute(
            select(func.count()).select_from(Application)
            .where(Application.assigned_officer_id == cand.id,
                   Application.current_status.in_(["pending", "in_progress"])))).scalar_one()
        if 2 <= n <= 15:
            officer = await officer_context(db, cand)
            break
    else:
        check(True, "(skipped: no officer has 2-15 open applications)")
        return
    first = await _converse(db, officer, ["show my pending applications"])
    listed = [r["application_number"]
              for r in ((first[0][1].get("table_data") or {}).get("applications") or [])]
    if not 2 <= len(listed) <= 15:
        check(True, f"(skipped: the pending listing has {len(listed)} row(s))")
        return

    for ask in ("can i get the full details", "i need details of both the applications",
                "i need both"):
        convo = await _converse(db, officer, ["show my pending applications", ask])
        r = convo[1][1]
        answer = plain(r.get("response"))
        check(all(n in answer for n in listed),
              f"'{ask}' answers about every row listed", answer)
        check("which one do you mean" not in answer.lower(),
              f"...without asking which one", answer)
        tables = (r.get("table_data") or {}).get("multi_tables") or []
        check(len(tables) == len(listed),
              f"...and renders one detail table per row ({len(tables)})", answer)

    # A single application discussed EARLIER must not hijack a follow-up made
    # against a fresher listing -- the case that spent 85 seconds in the agent
    # layer describing a file the officer had moved on from.
    if other_number and other_number not in listed:
        convo = await _converse(db, officer, [
            f"show details for {other_number}",
            "show my pending applications",
            "can i get the full details"])
        answer = plain(convo[2][1].get("response"))
        check(other_number not in answer and all(n in answer for n in listed),
              f"a listing beats the single application from two turns back",
              answer)


async def main() -> int:
    test_classification()
    test_context_record()

    if ROUTING_ONLY:
        print("\n(--routing: database sections skipped)")
    else:
        async with AsyncSessionLocal() as db:
            officers = (await db.execute(
                select(SISOfficer).where(SISOfficer.is_active == True).limit(2)
            )).scalars().all()
            if not officers:
                check(False, "an officer exists to test with", "no active SISOfficer rows")
            else:
                # Both contexts are built BEFORE any chat runs: process_chat
                # commits, which expires the ORM rows, and touching one
                # afterwards lazy-loads outside the async greenlet.
                officer = await officer_context(db, officers[0])
                other = (await officer_context(db, officers[1])
                         if len(officers) > 1 else None)
                own = (await db.execute(
                    select(Application.application_number)
                    .where(Application.assigned_officer_id == officer.officer_id)
                    .limit(1))).scalars().first()
                foreign = (await db.execute(
                    select(Application.application_number)
                    .where(Application.assigned_officer_id != officer.officer_id)
                    .limit(1))).scalars().first()
                print(f"\nOfficer: {officer.name} ({officer.jurisdiction_type} "
                      f"{officer.jurisdiction_name}); sample application {own}")
                await test_scoped_query(db, officer, foreign)
                if own:
                    await test_singular_followups(db, officer, own)
                await test_list_followups(db, officer)
                await test_ordinal_completed_followups(db, officer)
                await test_details_over_listed(db, officer, own)
                if own:
                    await test_multilingual(db, officer, own)
                if other is not None and own:
                    await test_no_leak_across_officers(db, officer, other, own)

    failed = [r for r in _results if not r[0]]
    print(f"\n{'=' * 68}")
    print(f"{len(_results) - len(failed)}/{len(_results)} checks passed")
    for _, label, detail in failed:
        print(f"  FAILED: {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
