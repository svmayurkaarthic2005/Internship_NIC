"""Regression tests for "SIS Chatbot -- Audit Equal Treatment and Coverage of
Every Service Code".

Canonical inventory (confirmed live, not assumed): `SIS_URBAN_SERVICES` in
`backend/utils/helpers.py` holds 29 TAMILNILAM urban codes (0153-0188, no
0155 -- see test_unsupported_service_code.py for that audit). Of those, 11
actually carry live rows in `service_register_entries` for this seed:
0153, 0154, 0156, 0158, 0159, 0160, 0162, 0164, 0165, 0169, 0178. The other
18 carry zero rows -- a data fact, not a defect, and `all_service_code_
applications` must still answer them truthfully ("No applications under X
found") rather than treating a zero count as "unsupported".

A broad live sweep across all 11 populated codes (define / list / count /
status-filter / ward-filter / compare / details / status / workflow /
field-visit-requirement / negation) found the architecture already handles
every code generically (no per-code branches, confirmed by reading
`_register_listing`, `calc_qa.compare_codes`, `othercode_detail.card/
workflow`, `_RANK` in calc_qa.py) -- EXCEPT for three confirmed defects, all
surfaced only by a MULTI-TURN conversation naming a non-ISD/NISD code (a
single-turn "show applications for service code 0158" already worked; the
bug was in what happened on the NEXT turn):

1. "Show applications for service code 0158." -> "Exclude rejected
   applications." silently dropped the 0158 scope and answered from EVERY
   service code instead (126 vs the correct 20) -- `_register_listing`
   inherited a carried ward/block from the prior listing's `followup_ctx.
   filters` on a bare continuation, but never did the same for
   `service_code`.

2. "Show applications for service code 0160." -> "Also show the block
   number." misrouted entirely to `officer_directory` ("There are 6 wards
   and 62 blocks in Ambattur...") -- proven NOT specific to the non-ISD code
   by reproducing the identical failure after "Show my pending ISD
   applications." too. Root cause, two layers deep:
     a. `_WARD_DIRECTORY_RE` in rag.py matched "show the block number" as a
        directory request (its negative lookahead excluded "application/
        survey/pending/overdue" but not "number"), so `parse_intent` returned
        `officer_directory`.
     b. `officer_directory` is in `fctx.SELF_CONTAINED_INTENTS`, so
        `chatbot._followup_is_self_contained()` took that as proof the
        message was a fresh, self-contained question and never even called
        `fctx.resolve()` -- the column-projection follow-up logic
        (`field_projections()`, already correctly returning `['block']`)
        never got a chance to run.
   A secondary, narrower gap rode along: `_PROJECT_TRIGGER_RE` (the trigger
   gate `field_projections()` itself uses) had no "also" alternative at all
   -- "along"/"add"/"include" were recognised, "also" was not.

3. "Which service code 0158 applications have no subdivision recorded?"
   (Mode B standalone field-presence, named code) silently dropped the
   "0158" scope and answered over the WHOLE register (126 applications)
   instead of the 20 under 0158 -- `_standalone_field_presence_answer`'s
   generic "register" branch never called `_requested_service_code_filter`
   the way `_register_listing` already does.

Fixing #3 exposed a FOURTH, pre-existing defect in `_render_field_presence`:
once it could reach a code (like 0158) for which the asked field (sub-
division) is NOT APPLICABLE to any row at all, the "nothing matched" wording
asserted "All of the ... applications have a subdivision recorded" in the
SAME sentence as "(20 not applicable for this field)" -- turning a NULL
(not-applicable) into a false positive assertion, exactly the violation
CLAUDE.md's domain rules forbid ("a NULL field must not be turned into a
positive or negative assertion without evidence").

Fixes (`backend/services/chatbot.py`, `backend/services/rag.py`):
  * `_register_listing`: carries `service_code` forward from `followup_ctx.
    filters` the same way ward/block already were, when the CURRENT message
    names no code of its own and the carried context is itself a
    register-wide listing. Also persists `service_code` onto `sd` so a LATER
    turn can read it back the same way (the DB-query-level
    `service_code_filter` key `get_all_service_code_applications` already
    sets was already enough for `build_context()` to carry it in `filters`,
    confirmed by reading `followup_context.build_context`, but the
    persistence was made explicit here too for clarity).
  * `rag._WARD_DIRECTORY_RE`: the "list/show/name wards/blocks" alternative
    now excludes "wards?/blocks?" immediately followed by "no./number" --
    that shape asks for the COLUMN value, never a directory.
  * `followup_context._PROJECT_TRIGGER_RE`: added "\\balso\\b" as a trigger,
    matching the existing "along"/"add"/"include" idiom (safe as a bare
    trigger: it only produces a result once an actual field needle is ALSO
    found).
  * `_standalone_field_presence_answer`: the "register" branch now calls
    `_requested_service_code_filter(message)` and passes it to
    `get_all_service_code_applications`, exactly like `_register_listing`
    does; the scope label names the code when one was given.
  * `_render_field_presence`: the "nothing matched" branch now computes
    `determinable = len(scope) - len(not_applicable) - len(unresolved)` and,
    when that is zero, says the field does not apply to any of them instead
    of asserting "all/none have it" over a count that includes rows the
    check never determined anything about.

A second pass over section 7's full paired-negation list ("show applications
that are NOT code X") found a fifth, more serious defect: "Show applications
that are not service code 0158." was answered with exactly 0158's own 20
applications -- the OPPOSITE of what was asked, not merely an omission.
`_requested_service_code_filter` read the bare digits with no awareness of
the "not" in front of them and applied the code as an INCLUDE filter.
`neg_scope.py` (the module that already handles negated status/type/channel)
has no concept of a service code at all. Fixed with a new, narrowly-scoped
`_excluded_service_code_filter()` (the same negation-cue vocabulary
`calc_qa._CC_OUT` already uses for count questions: not/except/excluding/
outside/other than/apart from) and a new `exclude_service_code` parameter
threaded through `get_all_service_code_applications` -> `_register_listing`
-> `_render_all_service_code_answer`, carried forward across a continuation
turn the same way an included code already was.

A sixth defect, also from section 7's list: "Show applications for service
code 0158 without a recorded subdivision." (the IMPERATIVE phrasing, not
"which...have") fell through BOTH field-presence modes -- Mode A
(`_field_presence_answer`) declines because there is no carried list yet on
a fresh query, and Mode B's parser only recognised the interrogative "which
<population> have/has" skeleton. The field was silently dropped and the
answer was the unfiltered 20-row listing. Fixed by widening
`parse_standalone_field_presence_request()` to also accept the imperative
shape (reusing `_PRESENCE_LISTING_RE`, the same shape-check Mode A's own
parser already uses for "show only those with/without X").

python test_servicecode_equal_treatment.py            # everything (DB, no LLM)
python test_servicecode_equal_treatment.py --routing  # unit-level only, no DB
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, r"C:\proj\nic_internship")

from sqlalchemy import select, func

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer, ServiceRegisterEntry, Ward
from backend.schemas import OfficerContext
from backend.services import chatbot
from backend.services import rag
from backend.services.chatbot import calc_qa
from backend.services import followup_context as fctx
from backend.services.auth_service import get_officer_jurisdiction_ids
from backend.utils.helpers import SIS_URBAN_SERVICES

OFFICER_EMAIL = "marun_chn@sis.tn.gov.in"   # wards 001 / 005 / 009

_results: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    _results.append((name, bool(ok), detail))
    print(("ok  " if ok else "FAIL") + f" {name}" + ("" if ok else f" -- {detail}"))


def strip_html(s, n=300):
    return re.sub(r"<[^>]+>", " | ", s or "")[:n]


# ---------------------------------------------------------------------------
# Unit-level: no DB, no LLM
# ---------------------------------------------------------------------------

def test_ward_directory_regex():
    # Defect 2a: a column-add follow-up must not be read as a directory request.
    for msg in ("Also show the block number.", "show the block number", "show ward no.",
                "Show the ward number."):
        got = rag.parse_intent(msg)
        check(f"{msg!r} does not route to officer_directory", got != "officer_directory", got)
    # Genuine directory questions, for an unrelated supported code (equal
    # treatment check: these must still work after the fix).
    for msg, want in (
        ("Show my wards.", "jurisdiction_summary"),
        ("how many wards do I have", "jurisdiction_summary"),
        ("list all blocks", "officer_directory"),
        ("which officer is in charge of ward 102", "officer_directory"),
    ):
        got = rag.parse_intent(msg)
        check(f"{msg!r} == {want!r} (unaffected)", got == want, got)


def test_project_trigger_also():
    check("'also show the block number' is recognised as a projection follow-up",
          fctx.field_projections("Also show the block number.") == ["block"],
          fctx.field_projections("Also show the block number."))
    check("'also show the ward number' is recognised too",
          fctx.field_projections("Also show the ward number.") == ["ward"],
          fctx.field_projections("Also show the ward number."))
    # A bare field name with no trigger at all is still not a projection
    # follow-up (the "also" addition must not turn into a universal catch-all).
    check("a plain fresh question naming no trigger is still None",
          fctx.field_projections("what is the survey number of 2026/0154/02/000001") is None,
          fctx.field_projections("what is the survey number of 2026/0154/02/000001"))


def test_followup_is_self_contained_no_longer_blocks_column_add():
    check("'Also show the block number.' is no longer treated as self-contained",
          not chatbot._followup_is_self_contained("Also show the block number."))
    # A real directory question IS still self-contained (equal-treatment /
    # no-regression check).
    check("'Show my wards.' is still self-contained",
          chatbot._followup_is_self_contained("Show my wards."))


def test_requested_service_code_filter_used_in_standalone_presence():
    # Defect 3, at the unit level: the message names a code that
    # `_requested_service_code_filter` must recognise (ISD/NISD excluded, as
    # for the ordinary register listing).
    check("'applications for service code 0158' names 0158",
          chatbot._requested_service_code_filter("which service code 0158 applications have no subdivision recorded") == "0158")
    check("ISD/NISD never read as a service-code FILTER here (own dedicated intents)",
          chatbot._requested_service_code_filter("which service code 0154 applications have no subdivision recorded") is None)


def test_field_visit_catalog_topic():
    # Defect 8: "which service codes require a field visit" answered ONLY
    # about ISD/NISD (2 of the 19 codes that actually require one) --
    # `_field_visit_rule_topic` is a hardcoded ISD/NISD-only topic that this
    # specific phrasing shape must not reach.
    check("'which service codes require a field visit' routes to the new catalog topic",
          chatbot._field_visit_rule_topic("which service codes require a field visit") == "fv_needed_catalog")
    check("the negative form routes to the catalog topic too",
          chatbot._field_visit_rule_topic("which service codes do not require a field visit") == "fv_not_needed_catalog")
    check("the ISD/NISD 'types of applications' framing is unaffected (still the old rule text)",
          chatbot._field_visit_rule_topic("which types of applications need a field visit") == "fv_needed")
    import backend.utils.helpers as _h
    _n_yes = sum(1 for v in _h.SIS_URBAN_SERVICES.values() if v.get("requires_field_visit"))
    _n_no = len(_h.SIS_URBAN_SERVICES) - _n_yes
    ans_yes = chatbot._igrs_can_rule_answer("fv_needed_catalog", "en")
    check("the catalog answer's count matches SIS_URBAN_SERVICES exactly",
          ans_yes.startswith(f"{_n_yes} service codes require"), ans_yes[:60])
    ans_no = chatbot._igrs_can_rule_answer("fv_not_needed_catalog", "en")
    check("the negative catalog answer's count matches too",
          ans_no.startswith(f"{_n_no} service codes do not require"), ans_no[:60])
    check("yes + no counts reconcile to the full catalog",
          _n_yes + _n_no == len(_h.SIS_URBAN_SERVICES))


def test_codes_with_applications():
    # Defect 9: "which services have non-zero counts" fell to the LLM
    # (general_query), which fabricated "there are 2 service codes in your
    # jurisdiction: 0153 and 0154" -- a register that actually has 11.
    for q in ("Which services have non-zero counts?", "which service codes have applications",
              "which service codes are in use"):
        check(f"{q!r} is recognised by the new deterministic trigger",
              bool(calc_qa._CODES_WITH_APPS_RE.search(q)), q)
    check("'which service codes have no applications' is recognised too",
          bool(calc_qa._CODES_WITH_APPS_RE.search("which service codes have no applications")))
    # Unit-level check of the function itself against synthetic rows.
    rows = [{"service_code": "0158"}, {"service_code": "0158"}, {"service_code": "0160"}]
    got = calc_qa.codes_with_applications("which service codes have applications", rows, False)
    check("codes_with_applications lists exactly the codes present in the rows",
          "0158" in got and "0160" in got and "0153" not in got, got)
    got_zero = calc_qa.codes_with_applications("which service codes have no applications", rows, False)
    check("the zero-count variant lists codes NOT present in the rows",
          "0158" not in got_zero and "0153" in got_zero, got_zero[:200])


def test_catalog_question_routing():
    # Defect 7: a bare catalog question ("what service codes are there")
    # used to fall through to the generic "what is <field>" fallback and
    # come back "Please specify the application number..." -- a register-
    # wide catalog question answered as if it named one unspecified file.
    for q in ("what service codes are there", "what service codes exist",
              "what service codes do you support", "which service codes are supported",
              "what service codes can I use", "which service codes do you have"):
        got = rag.parse_intent(q)
        check(f"{q!r} routes to the catalog (service_code_guide), not application_status",
              got == "service_code_guide", got)
    # No regression: the existing "all service codes" listing and the plain
    # code-table question keep their own, already-correct routing.
    for q, want in (("list all service codes", "all_service_code_applications"),
                    ("show all service codes", "all_service_code_applications"),
                    ("what are the service codes", "service_code_guide")):
        got = rag.parse_intent(q)
        check(f"{q!r} == {want!r} (unaffected)", got == want, got)


def test_excluded_service_code_filter():
    # Defect 5: a NEGATED code must never come back as an include filter.
    check("'not service code 0158' is recognised as an EXCLUSION",
          chatbot._excluded_service_code_filter("Show applications that are not service code 0158.") == "0158")
    check("'_requested_service_code_filter' refuses the same negated mention",
          chatbot._requested_service_code_filter("Show applications that are not service code 0158.") is None)
    check("'except service code 0164' is an exclusion",
          chatbot._excluded_service_code_filter("Show applications except service code 0164.") == "0164")
    check("a plain (non-negated) code is still an INCLUDE filter, not an exclusion",
          chatbot._requested_service_code_filter("Show applications for service code 0158.") == "0158"
          and chatbot._excluded_service_code_filter("Show applications for service code 0158.") is None)
    check("ISD/NISD are never returned by the exclusion helper either",
          chatbot._excluded_service_code_filter("Show applications that are not service code 0154.") is None)


def test_standalone_presence_imperative_shape():
    # Defect 6: the imperative phrasing ("show ... without X recorded"),
    # not just the interrogative "which ... have" skeleton.
    got = fctx.parse_standalone_field_presence_request(
        "Show applications for service code 0158 without a recorded subdivision.")
    check("imperative 'show applications ... without X recorded' is recognised (Mode B)",
          got == ("register", "subdivision", False), got)
    got2 = fctx.parse_standalone_field_presence_request(
        "Show applications awaiting a visit without a subdivision recorded.")
    check("the imperative shape still resolves the awaiting_visit scope correctly",
          got2 == ("awaiting_visit", "subdivision", False), got2)
    # No regression: the interrogative skeleton still works exactly as before.
    got3 = fctx.parse_standalone_field_presence_request(
        "Which service code 0158 applications have no subdivision recorded?")
    check("the original interrogative 'which ... have' skeleton is unaffected",
          got3 == ("register", "subdivision", False), got3)
    # Caught by test_sequence_c_table_ops.py as a live regression: a plain
    # COLUMN-SELECTION request ("show applications ... with <column list>")
    # trivially contains "show...applications" + a bare "with" + the word
    # "subdivision" (as a column NAME, not a presence check), and was first
    # misread as this same imperative shape. The "recorded"/"scheduled"
    # requirement in the fix is what tells the two apart.
    got4 = fctx.parse_standalone_field_presence_request(
        "Show applications in ward 001 as a table with application number, "
        "status, survey number, subdivision number, and submission date.")
    check("a column-selection request (no 'recorded'/'scheduled') is never misread as a presence filter",
          got4 is None, got4)


def test_routing() -> int:
    test_ward_directory_regex()
    test_project_trigger_also()
    test_followup_is_self_contained_no_longer_blocks_column_add()
    test_excluded_service_code_filter()
    test_standalone_presence_imperative_shape()
    test_catalog_question_routing()
    test_field_visit_catalog_topic()
    test_codes_with_applications()
    test_requested_service_code_filter_used_in_standalone_presence()
    bad = sum(1 for _, ok, _ in _results if not ok)
    print(f"\nrouting/unit: {len(_results) - bad}/{len(_results)} ok")
    return bad


# ---------------------------------------------------------------------------
# Live DB
# ---------------------------------------------------------------------------

async def get_ctx(email: str) -> OfficerContext:
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(SISOfficer).where(SISOfficer.email == email))
        officer = res.scalar_one()
        jur = await get_officer_jurisdiction_ids(officer.id, db)
        ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"] + jur["ward_ids"] + jur["block_ids"])
        return OfficerContext(
            officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
            email=officer.email, designation=officer.designation,
            jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
            jurisdiction_ids=[i for i in ids if i])


async def turn(ctx, msg, history=None, session_id=None):
    sid = session_id or str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        r = await chatbot.process_chat(db=db, message=msg, officer=ctx, chat_history=history or [], session_id=sid)
    text = (r.get("response") if isinstance(r, dict) else r) or ""
    intent = r.get("intent") if isinstance(r, dict) else None
    return text, intent, sid


async def turn_stream(ctx, msg, history=None, session_id=None):
    import json as _json
    sid = session_id or str(uuid.uuid4())
    chunks = []
    async with AsyncSessionLocal() as db:
        async for ev in chatbot.process_chat_stream(
                db=db, message=msg, officer=ctx, chat_history=history or [], session_id=sid):
            chunks.append(ev)
    resp = ""
    for c in chunks:
        s = c.decode() if isinstance(c, bytes) else c
        if isinstance(s, str) and s.startswith("data: "):
            try:
                resp += _json.loads(s[6:]).get("content", "")
            except Exception:
                pass
    return resp, sid


async def db_count(ward_numbers: list[str], service_code: str | None = None, hide_rejected=True) -> int:
    async with AsyncSessionLocal() as db:
        q = select(func.count()).select_from(ServiceRegisterEntry).join(
            Ward, ServiceRegisterEntry.ward_id == Ward.id).where(Ward.ward_number.in_(ward_numbers))
        if service_code:
            q = q.where(ServiceRegisterEntry.service_code == service_code)
        if hide_rejected:
            q = q.where(ServiceRegisterEntry.status != "rejected")
        return (await db.execute(q)).scalar_one()


async def live_main():
    ctx = await get_ctx(OFFICER_EMAIL)
    wards = ["001", "005", "009"]

    # -- 1. rejected-widen preserves the service_code scope (the original defect) --
    sid = str(uuid.uuid4())
    hist = []
    t1, i1, _ = await turn(ctx, "Show applications for service code 0158.", hist, sid)
    hist += [{"role": "user", "content": "Show applications for service code 0158."},
             {"role": "assistant", "content": t1}]
    t2, i2, _ = await turn(ctx, "Exclude rejected applications.", hist, sid)
    expected_158_open = await db_count(wards, "0158", hide_rejected=True)
    check("1a. 'Exclude rejected applications.' after a 0158 listing stays scoped to 0158",
          f"under 0158" in t2, strip_html(t2))
    check("1b. the excluded-rejected count matches the DB for 0158 alone (not the whole register)",
          re.search(rf"Found\s+{expected_158_open}\s", re.sub(r"<[^>]+>", " ", t2)) is not None,
          f"expected {expected_158_open}; got {strip_html(t2)}")
    hist += [{"role": "user", "content": "Exclude rejected applications."},
             {"role": "assistant", "content": t2}]
    t3, i3, _ = await turn(ctx, "Include rejected applications too.", hist, sid)
    expected_158_all = await db_count(wards, "0158", hide_rejected=False)
    check("1c. 'Include rejected too.' after that stays scoped to 0158 (not every code)",
          "under 0158" in t3, strip_html(t3))
    check("1d. the re-included count matches the DB total for 0158 alone",
          re.search(rf"Found\s+{expected_158_all}\s", re.sub(r'<[^>]+>', ' ', t3)) is not None,
          f"expected {expected_158_all}; got {strip_html(t3)}")

    # -- 1e. the SAME chain for an unrelated supported code (0164) --
    sid2 = str(uuid.uuid4())
    hist2 = []
    u1, _, _ = await turn(ctx, "Show applications for service code 0164.", hist2, sid2)
    hist2 += [{"role": "user", "content": "Show applications for service code 0164."},
              {"role": "assistant", "content": u1}]
    u2, _, _ = await turn(ctx, "Exclude rejected applications.", hist2, sid2)
    expected_164_open = await db_count(wards, "0164", hide_rejected=True)
    check("1e. the same chain holds for an unrelated code (0164)",
          "under 0164" in u2 and re.search(rf"Found\s+{expected_164_open}\s", re.sub(r'<[^>]+>', ' ', u2)) is not None,
          strip_html(u2))

    # -- 2. "also show <column>" column-add, for the affected code AND for ISD (unrelated, unaffected) --
    for label, first_q, code_word in (
            ("0160 (non-ISD)", "Show applications for service code 0160.", "under 0160"),
            ("ISD (unrelated code, sanity check)", "Show my pending ISD applications.", None)):
        sid3 = str(uuid.uuid4())
        hist3 = []
        v1, vi1, _ = await turn(ctx, first_q, hist3, sid3)
        hist3 += [{"role": "user", "content": first_q}, {"role": "assistant", "content": v1}]
        v2, vi2, _ = await turn(ctx, "Also show the block number.", hist3, sid3)
        check(f"2. 'also show the block number' after {label} adds the column, never officer_directory",
              vi2 != "officer_directory" and "Block" in v2, f"[intent={vi2}] {strip_html(v2)}")
        # Row count must be unchanged by adding a column.
        n1 = re.search(r"Found\s+(\d+)\s", re.sub(r"<[^>]+>", " ", v1))
        n2 = re.search(r"Found\s+(\d+)\s", re.sub(r"<[^>]+>", " ", v2))
        if n1 and n2:
            check(f"2b. adding a column does not change the row count for {label}",
                  n1.group(1) == n2.group(1), f"{n1.group(1)} -> {n2.group(1)}")

    # -- 3. standalone field-presence respects a named service code --
    expected_158_count = await db_count(wards, "0158", hide_rejected=True)
    r3, i3b, _ = await turn(ctx, "Which service code 0158 applications have no subdivision recorded?")
    check("3a. the answer names 0158, not the whole register",
          "0158" in r3, strip_html(r3))
    check("3b. the scoped count matches the DB count for 0158 alone",
          str(expected_158_count) in r3, f"expected {expected_158_count} in {strip_html(r3)}")
    check("3c. never asserts a false positive/negative over a not-applicable field",
          not re.search(r"All of the 0158.*have a subdivision recorded\. \(\d+ not applicable", r3),
          strip_html(r3))

    # -- 3d. the same for an unrelated code (0164, a different field) --
    expected_164_count = await db_count(wards, "0164", hide_rejected=True)
    r3d, _, _ = await turn(ctx, "Which service code 0164 applications have payment information recorded?")
    check("3d. standalone field-presence for an unrelated code (0164) also stays scoped",
          "0164" in r3d and str(expected_164_count) in r3d, strip_html(r3d))

    # -- 4. the "not applicable" wording fix doesn't break the ordinary
    #    (fully-applicable) case for a field-visit population --
    r4, _, _ = await turn(ctx, "Which applications awaiting a visit have no subdivision recorded?")
    check("4. a population where the field IS applicable still gets a real all/none assertion",
          re.search(r"(All|None) of the \d+ application\(s\) awaiting a visit have a subdivision recorded\.", r4) is not None,
          strip_html(r4))

    # -- 5. zero-record codes still answer truthfully, never "unsupported" --
    for code in ("0157", "0161", "0171"):
        name = SIS_URBAN_SERVICES[code]["name"]
        r5, i5, _ = await turn(ctx, f"Show applications for service code {code}.")
        check(f"5. zero-record code {code} ({name}) answers truthfully, not an error",
              f"No applications under {code}" in r5 and "jurisdiction" in r5, strip_html(r5))

    # -- 6. compare, ranking and definition still reconcile with the DB for
    #    a representative populated code (0162) --
    expected_162 = await db_count(wards, "0162", hide_rejected=False)
    r6, _, _ = await turn(ctx, "Compare 0162 and 0153.")
    check("6a. compare_codes names the real count for 0162",
          str(expected_162) in r6, strip_html(r6))
    r6b, _, _ = await turn(ctx, "Does service code 0162 require a field visit?")
    _wants_fv = bool(SIS_URBAN_SERVICES["0162"].get("requires_field_visit"))
    check("6b. field-visit requirement for 0162 matches the canonical metadata",
          ("No" if not _wants_fv else "Yes") in r6b.split()[0:1] or (("No —" in r6b) != _wants_fv),
          strip_html(r6b))

    # -- 7. streaming parity for the three fixed shapes --
    sid7 = str(uuid.uuid4())
    hist7 = []
    s1, sid7a = await turn_stream(ctx, "Show applications for service code 0158.", hist7, sid7)
    hist7 += [{"role": "user", "content": "Show applications for service code 0158."},
              {"role": "assistant", "content": s1}]
    s2, _ = await turn_stream(ctx, "Exclude rejected applications.", hist7, sid7)
    check("7a. streaming: rejected-widen keeps the 0158 scope too",
          "under 0158" in s2, strip_html(s2))
    s3, _ = await turn_stream(ctx, "Which service code 0164 applications have payment information recorded?")
    check("7b. streaming: standalone field-presence respects the named code",
          "0164" in s3, strip_html(s3))

    # -- 8. excluded service code: the opposite-answer defect, and its
    #    continuation chain, for the affected code and an unrelated one --
    expected_not_158 = await db_count(wards, None, hide_rejected=True) - expected_158_count
    r8, i8, _ = await turn(ctx, "Show applications that are not service code 0158.")
    check("8a. 'not service code 0158' excludes 0158 (not the opposite -- only 0158)",
          "0158" not in re.findall(r"under\s+(\d{4})", r8) and "excluding service code 0158" in r8,
          strip_html(r8))
    check("8b. the excluded-code total reconciles with the DB (every code except 0158)",
          str(expected_not_158) in r8, f"expected {expected_not_158} in {strip_html(r8)}")

    expected_not_164 = await db_count(wards, None, hide_rejected=True) - await db_count(wards, "0164", hide_rejected=True)
    r8c, _, _ = await turn(ctx, "Show applications except service code 0164.")
    check("8c. the same exclusion works for an unrelated code (0164)",
          str(expected_not_164) in r8c and "excluding service code 0164" in r8c, strip_html(r8c))

    sid8 = str(uuid.uuid4())
    hist8 = []
    r8d, _, _ = await turn(ctx, "Show applications that are not service code 0158.", [], sid8)
    hist8 += [{"role": "user", "content": "Show applications that are not service code 0158."},
              {"role": "assistant", "content": r8d}]
    r8e, _, _ = await turn(ctx, "Exclude rejected applications.", hist8, sid8)
    check("8d. the exclusion survives a continuation turn ('exclude rejected too')",
          "excluding service code 0158" in r8e, strip_html(r8e))

    # -- 9. the imperative field-presence phrasing, for the affected code
    #    and an unrelated one --
    r9, i9, _ = await turn(ctx, "Show applications for service code 0158 without a recorded subdivision.")
    check("9a. the imperative phrasing is recognised (field_presence_filter), not the raw listing",
          i9 == "field_presence_filter", i9)
    check("9b. it stays scoped to 0158, not the whole register",
          "0158" in r9 and str(expected_158_count) in r9, strip_html(r9))
    r9c, i9c, _ = await turn(ctx, "Show applications for service code 0164 with payment information recorded.")
    check("9c. the same imperative phrasing works for an unrelated code (0164)",
          i9c == "field_presence_filter" and "0164" in r9c, strip_html(r9c))

    # -- 10. the catalog-question defect, live --
    r10, i10, _ = await turn(ctx, "what service codes are there")
    check("10. 'what service codes are there' answers the catalog, never asks for an application number",
          i10 != "application_status" and "application number" not in r10.lower(), strip_html(r10))


def main() -> None:
    bad_routing = test_routing()
    if "--routing" in sys.argv:
        sys.exit(1 if bad_routing else 0)
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
