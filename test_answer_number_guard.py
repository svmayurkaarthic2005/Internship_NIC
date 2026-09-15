"""Answers may not carry a number the register does not.

Three fixes, one test:

* the LLM number guard -- a stated total is made to agree with the count the
  query returned, and an application number the query result does not contain
  is removed rather than corrected (there is no way to tell which real file a
  wrong number was meant to be).
* an application-type scope is a register question -- "show my ISD
  applications" is the officer's whole ISD history, not the ISD files still
  sitting at the SIS desk today.
* "applicant details" asks about the people, not about the files.

    python test_answer_number_guard.py            # everything (DB, no LLM)
    python test_answer_number_guard.py --routing  # the guard only, no database
"""
import asyncio
import logging
import sys

sys.path.insert(0, ".")

import backend.database as _bd  # noqa: E402

_bd.AsyncSessionLocal.kw["bind"].echo = False
for _n in ("sqlalchemy.engine", "sqlalchemy"):
    logging.getLogger(_n).setLevel(logging.WARNING)

from sqlalchemy import select, text  # noqa: E402

from backend.database import AsyncSessionLocal  # noqa: E402
from backend.dependencies import get_officer_jurisdiction_ids  # noqa: E402
from backend.models import SISOfficer  # noqa: E402
from backend.schemas import OfficerContext  # noqa: E402
from backend.services import postgres  # noqa: E402
from backend.services.chatbot import (  # noqa: E402
    _asks_applicant_focus,
    _reconcile_count_claims,
    _scrub_unverified_app_numbers,
    _verified_app_numbers,
    _verify_answer_numbers,
    create_chat_session,
    process_chat,
)
from backend.services.rag import (  # noqa: E402
    extract_submission_channels,
    parse_intent,
)

FAILURES = []


def check(ok, label, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"    {detail}" if detail else ""))
    if not ok:
        FAILURES.append(f"{label}: {detail}")


REAL = "2026/0154/28/001280"
FAKE = "2026/0154/28/999999"
SD = {"count": 9, "applications": [{"application_number": REAL}]}


def test_guard():
    print("[1] the numbers in an answer are checked against the data it came from")

    check(_verified_app_numbers(SD) == {REAL}, "every number in the payload is known")
    check(_verified_app_numbers({}) == set(), "an empty payload knows nothing")

    out, removed = _scrub_unverified_app_numbers(f"See {REAL} and {FAKE}.", {REAL})
    check(REAL in out and FAKE not in out and removed == [FAKE],
          "an invented application number is removed, a real one kept", out)

    out, removed = _scrub_unverified_app_numbers(f"See {FAKE}.", set())
    # Nothing to check against -- the guard stands aside rather than stripping
    # every number out of a corpus answer.
    check(out == f"See {FAKE}." and not removed,
          "with no records fetched the guard stands aside")

    out, changed = _reconcile_count_claims("You have 3 applications.", {"count": 9})
    check(changed and "9 applications" in out, "a wrong total is corrected", out)

    out, changed = _reconcile_count_claims("There are 9 applications.", {"count": 9})
    check(not changed, "a right total is left alone", out)

    # The sentence already states the database figure, so the 3 is a part of it,
    # not a claim about the whole.
    out, changed = _reconcile_count_claims("3 of your 9 applications are approved.",
                                           {"count": 9})
    check(not changed and out.startswith("3 of"),
          "a part-of-the-whole sentence is not rewritten", out)

    out, changed = _reconcile_count_claims("It was filed on 2026-06-30, 77 days ago.",
                                           {"count": 2})
    check(not changed, "a number that is not a total is left alone", out)

    out, changed = _reconcile_count_claims("You have 3 applications.", {})
    check(not changed, "no count in the payload -> nothing to reconcile", out)

    text_in = f"You have 3 applications, including {FAKE} and {REAL}."
    out, note = _verify_answer_numbers(text_in, SD, "general_query")
    check("9 applications" in out and FAKE not in out and REAL in out and note.strip(),
          "both checks run together, and the officer is told", out)

    out, note = _verify_answer_numbers(f"You have 9 applications, e.g. {REAL}.", SD)
    check(out == f"You have 9 applications, e.g. {REAL}." and not note,
          "a clean answer is returned unchanged and silently")

    print("\n[2] 'applicant' is what separates the people from the files")
    for msg in ("applicant details", "APPLICANT info", "விண்ணப்பதாரர் விவரம்"):
        check(_asks_applicant_focus(msg), f"{msg!r} is an applicant question")
    for msg in ("details of both", "show applications", ""):
        check(not _asks_applicant_focus(msg), f"{msg!r} is not")

    print("\n[3] a bare imperative still names a channel")
    for msg in ("show citizen", "list citizen", "citizen apps"):
        check(extract_submission_channels(msg) == ["citizen"], f"{msg!r} -> citizen")
    for msg in ("citizen access number", "show me the citizen access number"):
        check(extract_submission_channels(msg) == [], f"{msg!r} is the CAN, not a channel")

    print("\n[3a] every way an officer asks for a channel reaches the listing")
    _LIST_INTENTS = {"pending_applications", "isd_applications", "nisd_applications",
                     "merge_applications", "both_applications", "overdue_applications"}
    # A misspelt channel name, or a misspelt "applications", used to leave the
    # question with no channel at all -- so it either reached the LLM or, worse,
    # was answered as an UNSCOPED listing with the misspelt word ignored.
    for msg, want in (("csc aplications", "CSC"),
                      ("citizn applications", "citizen"),
                      ("show ctizen applications", "citizen"),
                      ("sub registrer applications", "sub_registrar"),
                      ("e-sevi applications", "CSC"),
                      ("applicaitons from csc", "CSC")):
        got_i, got_c = parse_intent(msg), extract_submission_channels(msg)
        check(got_i in _LIST_INTENTS and got_c == [want],
              f"{msg!r} -> {want} listing", f"{got_i} {got_c}")
    # A bare channel name asks for that channel's files; there is nothing else
    # in this domain it could mean.
    for msg, want in (("csc", "CSC"), ("sro", "sub_registrar"),
                      ("citizen", "citizen"), ("e-sevai", "CSC")):
        got_i, got_c = parse_intent(msg), extract_submission_channels(msg)
        check(got_i in _LIST_INTENTS and got_c == [want],
              f"bare {msg!r} -> {want} listing", f"{got_i} {got_c}")
    # ...but the typo tolerance must not swallow its neighbours. "register" is
    # two edits from "registrar" -- exactly the budget -- and re-routed four
    # register questions in the corpus before it was excluded by name.
    for msg in ("does your register store the applicant's occupation",
                "does your register track the applicant's annual income",
                "the deed was registered at Melur",
                "who registered this deed",
                "citizen access number", "what is a can number",
                "who is a citizen of india"):
        check(extract_submission_channels(msg) == [],
              f"{msg[:44]!r} names no channel", str(extract_submission_channels(msg)))

    print("\n[3b] a hyphenated first word is a word, not a list bullet")
    # "e-Sevai" is the department's name for the CSC counters. The leading
    # list-bullet stripper took the "e-" off it, and the leftover "sevai
    # applications" named no channel -- so a question with a deterministic
    # answer reached the LLM, which relabelled the officer's ISD list as
    # "9 e-sevai applications".
    for msg in ("e-sevai applications", "e-Sevai applications count",
                "how many e-sevai applications", "e-sevai apps"):
        check(parse_intent(msg) == "pending_applications"
              and extract_submission_channels(msg) == ["CSC"],
              f"{msg!r} is a CSC listing", parse_intent(msg))
    # A real list bullet is still stripped.
    for msg, want in (("a. show my applications", "pending_applications"),
                      ("b) how many pending applications", "pending_applications"),
                      ("1. list my applications", "pending_applications"),
                      ("a - show my applications", "pending_applications")):
        check(parse_intent(msg) == want, f"{msg!r} still loses its bullet",
              parse_intent(msg))


async def _ctx(db, officer):
    j = await get_officer_jurisdiction_ids(officer.id, db)
    return OfficerContext(
        officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
        email=officer.email, designation=officer.designation,
        jurisdiction_type=j["jurisdiction_type"], jurisdiction_name=j["jurisdiction_name"],
        jurisdiction_ids=(j["district_ids"] + j["taluk_ids"] + j["town_ids"]
                          + j["ward_ids"] + j["block_ids"]))


async def test_against_db():
    async with AsyncSessionLocal() as db:
        officers = (await db.execute(select(SISOfficer))).scalars().all()

        print("\n[4] a type scope answers the register, not the desk")
        for officer in officers:
            ctx = await _ctx(db, officer)
            for app_type in ("ISD", "NISD"):
                got = (await postgres.get_pending_applications(
                    db, ctx, application_type=app_type)).get("count", 0)
                want = (await db.execute(text(
                    """SELECT count(*) FROM applications a
                       JOIN survey_numbers s ON s.id = a.survey_number_id
                       JOIN blocks b ON b.id = s.block_id
                       JOIN officer_jurisdictions j ON j.officer_id = :o
                       WHERE b.ward_id = j.ward_id
                         AND a.current_status <> 'rejected'
                         AND a.application_type = :t"""),
                    {"o": officer.id, "t": app_type})).scalar()
                check(got == want,
                      f"{officer.employee_id} {app_type} listing == register",
                      f"{got} vs {want}")

            # The unscoped queue is the officer's whole OPEN queue -- pending,
            # in_progress and escalated -- and deliberately spans stages: an
            # in_progress file has moved to another desk (SD / DIS / Tahsildar)
            # without ceasing to be unresolved. What it must NOT do is widen to
            # closed files, which is what the type scopes above cover.
            desk = (await postgres.get_pending_applications(db, ctx)).get("count", 0)
            want_desk = (await db.execute(text(
                """SELECT count(*) FROM applications a
                   WHERE a.assigned_officer_id = :o
                     AND a.current_status IN ('pending','in_progress','escalated')"""),
                {"o": officer.id})).scalar()
            check(desk == want_desk,
                  f"{officer.employee_id} unscoped queue is the open queue",
                  f"{desk} vs {want_desk}")
            closed = (await db.execute(text(
                """SELECT count(*) FROM applications a
                   WHERE a.assigned_officer_id = :o
                     AND a.current_status IN ('approved','rejected')"""),
                {"o": officer.id})).scalar()
            check(desk < closed or closed == 0,
                  f"{officer.employee_id} ...and does not include closed files",
                  f"open {desk}, closed {closed}")

        print("\n[5] a channel listing shows that channel's applications")
        for officer in officers:
            ctx = await _ctx(db, officer)
            for channel in ("CSC", "sub_registrar", "citizen"):
                got = await postgres.get_pending_applications(
                    db, ctx, submission_channel=channel)
                want = (await db.execute(text(
                    """SELECT count(*) FROM applications a
                       JOIN survey_numbers s ON s.id = a.survey_number_id
                       JOIN blocks b ON b.id = s.block_id
                       JOIN officer_jurisdictions j ON j.officer_id = :o
                       WHERE b.ward_id = j.ward_id
                         AND a.current_status <> 'rejected'
                         AND a.submission_channel = :c"""),
                    {"o": officer.id, "c": channel})).scalar()
                check(got.get("count", 0) == want,
                      f"{officer.employee_id} {channel} listing == register",
                      f"{got.get('count')} vs {want}")
                # An empty channel list has to say WHY it is empty -- a bare
                # "No applications found" reads as a broken filter.
                if not want:
                    note = got.get("empty_note") or ""
                    check(bool(note.strip()),
                          f"{officer.employee_id} empty {channel} list explains itself",
                          note[:80])

        print("\n[6] 'applicant details' after a listing answers about the applicants")
        officer = next(o for o in officers if o.email.startswith("muthulakshmis"))
        ctx = await _ctx(db, officer)

        session = await create_chat_session(db, str(officer.id))
        history = []
        for msg in ("show pending applications", "applicant details"):
            result = await process_chat(msg, str(session.id), ctx, db, history)
            answer = result.get("response") or ""
            history += [{"role": "user", "content": msg},
                        {"role": "assistant", "content": answer}]
        check(answer.lower().startswith("applicant details"),
              "the answer is about the applicants", answer[:90])
        check("Name:" in answer and "stage:" not in answer.lower(),
              "...and not the application card", answer[:90])

        session = await create_chat_session(db, str(officer.id))
        history = []
        for msg in ("show pending applications", "applicant details of the first one"):
            result = await process_chat(msg, str(session.id), ctx, db, history)
            answer = result.get("response") or ""
            listed = result.get("structured_data") or {}
            history += [{"role": "user", "content": msg},
                        {"role": "assistant", "content": answer}]
        check("multi_applications" not in listed,
              "an ordinal picks ONE row, it is not expanded to all of them",
              answer[:90])


def main():
    test_guard()
    if "--routing" not in sys.argv:
        asyncio.run(test_against_db())
    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
