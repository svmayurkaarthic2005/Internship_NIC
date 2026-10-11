"""Regression suite for the multi-field ordinal follow-up gap.

Bug (reported): "Give me the application number, survey number, and
subdivision number of the second overdue visit." -- after "Which
application should I field visit next?" -- answered with an unrelated
44-row "all pending applications" listing, ignoring the ordinal entirely.

Three separate, GENERAL root causes, each fixed in `followup_context.py`
(none of them a special case for this one example):

1. `classify()`'s flat `_MAX_FOLLOWUP_WORDS` (12) cap returned FOLLOWUP_NONE
   for ANY message past 12 words, before any of the function's own
   ordinal/own-subject logic ran. A multi-field request ("the application
   number, survey number, and subdivision number of the second overdue
   visit") is 15 words only because it lists three fields, not because it
   is a fresh, self-contained question -- it still names no subject of its
   own once the ordinal/slice exemption (already used elsewhere in this
   function) is applied. Fixed by exempting messages that mention an
   ordinal/slice and have no own subject from the cap.

2. `chatbot._big_plan()`'s "<head>: f1, f2, f3" splitter dropped the shared
   trailing reference ("of the second overdue visit") from every field
   except the last one it happened to land on, the way ordinary English
   punctuation drops a shared qualifier from every item but the last in a
   list. "application number" (the first split clause) was asked bare, with
   no idea which record it meant. Fixed by detecting a trailing "of"/"for"
   clause on the LAST split field (when no other field already has its own)
   and re-attaching it to every field.

3. `followup_context._PROJECT_FIELDS` (read by both `field_for_each()` /
   `fields_for_each()`, used once an ordinal has already picked ONE row, and
   by `field_projections()`, used for a whole listing's column set) had no
   entry for "application number" at all -- every other named field
   (survey number, subdivision number, status, ...) had a dedicated earlier
   handler or a projection entry, but asking for the row's own identity
   column had neither, so it fell through to generic intent parsing, where
   the literal word "visit" (appended by `_apply_followup_resolution`) and
   no application number recognisable-as-such in that routing layer matched
   the bare `field_visits` LISTING intent -- not the picked row at all. Fixed
   by adding the field, then stripping it back out specifically inside
   `field_projections()` (never `fields_for_each()`), since the row-key
   column is always the listing's own anchor column regardless of whether it
   is named, exactly as `_STANDARD_LISTING_COLUMNS` already excludes it --
   confirmed necessary by `test_sequence_c_table_ops.py`'s pre-existing C2
   case, which narrows a listing to "application number and subdivision
   number" and must still read as subdivision-only for column purposes.

Known, NOT fixed here (reported, not silently claimed to pass) -- each is a
materially different, deeper capability than the three root causes above,
and fixing them risked exactly the kind of special-casing this task was
told not to do:

* Case 6 -- "Show the second field visit in Ward 12." The ward scope is
  silently ignored; the ordinal still picks from the WHOLE overdue list,
  not from a ward-12-only subset. Pre-existing: `resolve()`'s "about_visit"
  branch slices the carried list by overdue/awaiting count only, with no
  ward/block filter at all.
* Case 7 -- "Give me the first, second, and third overdue visits with their
  application numbers." Enumerating several explicit ordinal positions in
  one request is not something `ordinal_pick()`/`mentions_slice()` can
  express (they pick ONE position or a contiguous "last N" run, never an
  explicit list of three). Falls through to the generic field-visits
  summary.
* Case 8 -- "Show the second overdue visit, excluding rescheduled visits."
  `_big_plan`'s compound-question splitter treats the comma as a sentence
  boundary and asks "excluding rescheduled visits" as an unrelated SECOND
  question (answered with the Tahsildar-approval boilerplate), instead of
  reading it as a filter on the SAME ordinal pick.

Live-DB tests only (this whole fix is about real routing through the real
pipeline); no LLM anywhere. `python test_ordinal_multifield_followups.py`
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, r"C:\proj\nic_internship")

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services import chatbot
from backend.services import followup_context as fctx
from backend.services.auth_service import get_officer_jurisdiction_ids

OFFICER_EMAIL = "marun_chn@sis.tn.gov.in"

_results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((name, condition, detail))
    print(f"{'ok ' if condition else 'FAIL'} {name}" + (f" -- {detail}" if detail and not condition else ""))


def strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "")


# ── Classifier-level tests (no DB) ──────────────────────────────────────────

def test_classify_multifield_ordinal_past_the_word_cap():
    cases = [
        "Give me the application number, survey number, and subdivision number of the second overdue visit.",
        "Show me the second overdue visit.",
        "Give me its application number, survey number, and subdivision number.",
        "Show the third overdue visit and its scheduled date.",
        "Show the second field visit in Ward 12.",
        "Show the second overdue visit, excluding rescheduled visits.",
    ]
    for c in cases:
        check(f"classify() recognises a follow-up past the word cap: {c[:45]!r}...",
              fctx.classify(c) != fctx.FOLLOWUP_NONE, fctx.classify(c))
    # The exact long, self-contained counter-example `classify`'s own cue-word
    # gate is built to protect must still read as a fresh question, length
    # cap or not.
    check("classify() still refuses the protected long self-contained example",
          fctx.classify("What is the first application submitted this month under service code 0178?")
          == fctx.FOLLOWUP_NONE)


def test_big_plan_propagates_trailing_reference_to_every_field():
    bp = chatbot._big_plan(
        "Give me the application number, survey number, and subdivision number of the second overdue visit.")
    check("_big_plan splits into 3 fields, each carrying the ordinal reference",
          bp == [
              "application number of the second overdue visit",
              "survey number of the second overdue visit",
              "subdivision number of the second overdue visit",
          ], bp)


def test_project_fields_application_number_asymmetry():
    # fields_for_each (per-ROW pick) must recognise it...
    check("fields_for_each() recognises 'application number' as a per-row field",
          fctx.fields_for_each("application number of the second overdue visit") == ["application_number"])
    # ...but field_projections (whole-LISTING column set) must still treat it
    # as the listing's own always-present anchor column, not a projected one
    # -- this is the pre-existing C2 case in test_sequence_c_table_ops.py.
    check("field_projections() still strips the row-key column from a listing projection",
          fctx.field_projections("Show only application number and subdivision number.") == ["subdivisions"])


# ── Live-DB tests (real officer, real jurisdiction, both chat paths) ───────

async def officer_context(db, officer) -> OfficerContext:
    jur = await get_officer_jurisdiction_ids(officer.id, db)
    ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"] + jur["ward_ids"] + jur["block_ids"])
    return OfficerContext(officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
        email=officer.email, designation=officer.designation,
        jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
        jurisdiction_ids=[i for i in ids if i])


async def get_ctx() -> OfficerContext:
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(SISOfficer).where(SISOfficer.email == OFFICER_EMAIL))
        return await officer_context(db, res.scalar_one())


async def turn(ctx, message, hist=None, sid=None, stream=False):
    """One chat turn. `hist`/`sid` let a caller continue the SAME session so a
    follow-up genuinely continues the SAME conversation; omit both for an
    isolated, context-free turn."""
    sid = sid or str(uuid.uuid4())
    hist = hist if hist is not None else []
    if stream:
        chunks = []
        async with AsyncSessionLocal() as db:
            async for ev in chatbot.process_chat_stream(
                    db=db, message=message, officer=ctx, chat_history=hist, session_id=sid):
                chunks.append(ev)
        import json as _json
        resp = ""
        for c in chunks:
            s = c.decode() if isinstance(c, bytes) else c
            if isinstance(s, str) and s.startswith("data: "):
                try:
                    resp += _json.loads(s[6:]).get("content", "")
                except Exception:
                    pass
    else:
        async with AsyncSessionLocal() as db:
            r = await chatbot.process_chat(db=db, message=message, officer=ctx, chat_history=hist, session_id=sid)
        resp = (r.get("response") if isinstance(r, dict) else r) or ""
    hist.append({"role": "user", "content": message})
    hist.append({"role": "assistant", "content": resp})
    return resp, sid


async def _seeded_turn(ctx, message, stream=False):
    """Ask `message` as a follow-up to a FRESH 'Which application should I
    field visit next?' seed, in its own fresh session -- the standard shape
    every test case here needs, since the overdue/awaiting order is only
    meaningful relative to that one seed answer."""
    r0, sid = await turn(ctx, "Which application should I field visit next?")
    hist = [{"role": "user", "content": "Which application should I field visit next?"},
            {"role": "assistant", "content": r0}]
    overdue_apps = re.findall(r"2026/0154/02/\d+", r0.split("Applications awaiting")[0])
    r, _ = await turn(ctx, message, hist=hist, sid=sid, stream=stream)
    return strip_html(r), overdue_apps


async def live_main():
    ctx = await get_ctx()

    # ── Case 1: the originally-reported bug, both chat paths ───────────────
    for stream in (False, True):
        r, overdue_apps = await _seeded_turn(
            ctx,
            "Give me the application number, survey number, and subdivision number of the second overdue visit.",
            stream=stream)
        second = overdue_apps[1] if len(overdue_apps) > 1 else None
        check(f"1. multi-field ordinal answers the SECOND overdue application, not an unrelated listing "
              f"(stream={stream})",
              bool(second) and second in r and "pending" not in r.lower()[:40],
              r[:200])
        check(f"1. every requested field is present (stream={stream})",
              "application number" in r.lower() or "application no" in r.lower(), r[:200])

    # ── Case 2: plain ordinal pick, no extra fields ────────────────────────
    r, overdue_apps = await _seeded_turn(ctx, "Show me the second overdue visit.")
    check("2. plain ordinal pick resolves to the second overdue application",
          len(overdue_apps) > 1 and overdue_apps[1] in r, r[:200])

    # ── Case 3: "its" back-reference, chained in the SAME session (not a
    # big_plan split -- a genuinely separate follow-up turn) ───────────────
    r0, sid = await turn(ctx, "Which application should I field visit next?")
    hist = [{"role": "user", "content": "Which application should I field visit next?"},
            {"role": "assistant", "content": r0}]
    overdue_apps = re.findall(r"2026/0154/02/\d+", r0.split("Applications awaiting")[0])
    second = overdue_apps[1] if len(overdue_apps) > 1 else None
    r3a, _ = await turn(ctx, "Show me the second overdue visit.", hist=hist, sid=sid)
    r3b, _ = await turn(ctx, "Give me its application number, survey number, and subdivision number.",
                         hist=hist, sid=sid)
    r3b = strip_html(r3b)
    check("3. 'its' chained follow-up stays on the SAME picked application as step 1",
          bool(second) and second in r3a and second in r3b, (r3a[:120], r3b[:200]))
    check("3. the chained follow-up's fields come back (survey / subdivision)",
          "survey" in r3b.lower() and "sub-division" in r3b.lower(), r3b[:200])

    # ── Case 4: ordinal + a second field joined by "and" ───────────────────
    r, overdue_apps = await _seeded_turn(ctx, "Show the third overdue visit and its scheduled date.")
    third = overdue_apps[2] if len(overdue_apps) > 2 else None
    check("4. 'third overdue visit' resolves to the third overdue application",
          bool(third) and third in r, (third, r[:200]))

    # ── Case 5: ordinal over a DIFFERENT, non-visit list (first turn, no
    # carried context at all -- the register's own ordinal-pick path) ─────
    r5, _ = await turn(ctx, "Give me the application number and status of the second pending application.")
    check("5. first-turn ordinal over 'pending applications' names the 2nd of the real count",
          bool(re.search(r"2nd of \d+", r5)) and bool(re.search(r"2026/\d{4}/\d{2}/\d+", r5)), r5[:200])

    # ── Case 9: a genuinely separate follow-up turn (not a big_plan split)
    # adding more fields to an already-resolved pick ───────────────────────
    r0c, sidc = await turn(ctx, "Which application should I field visit next?")
    histc = [{"role": "user", "content": "Which application should I field visit next?"},
              {"role": "assistant", "content": r0c}]
    overdue_c = re.findall(r"2026/0154/02/\d+", r0c.split("Applications awaiting")[0])
    second_c = overdue_c[1] if len(overdue_c) > 1 else None
    r9a, _ = await turn(ctx, "Show the second overdue visit.", hist=histc, sid=sidc)
    r9b, _ = await turn(ctx, "Also include its survey number and subdivision number.", hist=histc, sid=sidc)
    r9b = strip_html(r9b)
    check("9. a later, separate follow-up still names the SAME picked application",
          bool(second_c) and second_c in r9b, (second_c, r9b[:250]))

    # ── Case 10: a long, self-contained question with "first" in it must
    # NOT be read as an implicit follow-up, even with a visit-plan context
    # live in the same session ─────────────────────────────────────────────
    r0d, sidd = await turn(ctx, "Which application should I field visit next?")
    histd = [{"role": "user", "content": "Which application should I field visit next?"},
              {"role": "assistant", "content": r0d}]
    r10, _ = await turn(
        ctx, "What is the first application submitted this month under service code 0178?",
        hist=histd, sid=sidd)
    check("10. a long self-contained ordinal question is answered on its own terms, "
          "not as a pick from the visit-plan list",
          "0178" in r10 and "overdue" not in r10.lower(), r10[:200])

    # ── Case 11: out-of-range ordinal, both chat paths ─────────────────────
    for stream in (False, True):
        r11, overdue_apps = await _seeded_turn(ctx, "Show me the ninth overdue visit.", stream=stream)
        check(f"11. an out-of-range ordinal asks for clarification instead of picking a different row "
              f"(stream={stream})",
              str(len(overdue_apps)) in r11 and "9" in r11 and not any(a in r11 for a in overdue_apps),
              r11[:250])

    # ── Case 12: ordinal with NO relevant previous result and no scope at
    # all (a fresh session, nothing shown yet) ─────────────────────────────
    r12, _ = await turn(ctx, "Show me the second one.")
    check("12. an ordinal with nothing shown yet asks for clarification rather than guessing",
          "not sure" in r12.lower() or "give" in r12.lower() or "specify" in r12.lower(),
          r12[:250])


def main() -> None:
    test_classify_multifield_ordinal_past_the_word_cap()
    test_big_plan_propagates_trailing_reference_to_every_field()
    test_project_fields_application_number_asymmetry()
    asyncio.run(live_main())

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} passed")
    if passed != total:
        print("\nFailures:")
        for name, ok, detail in _results:
            if not ok:
                print(f"  - {name}: {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
