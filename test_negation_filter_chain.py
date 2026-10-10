"""Multi-turn filter-negation chain over a register-wide listing
(all_service_code_applications), executed in a SHARED session the way an
officer actually uses it -- not merely checked one message at a time.

Sequence: "Show applications in ward 001." -> "Exclude rejected
applications." -> "Include rejected applications too." -> "Now show
applications that are not pending." -> "Do not exclude rejected
applications." -> "Show only approved applications instead."

Three confirmed defects, found by running this exact chain live against the
real DB and comparing the ward/status words in each answer against the turn
before it (never hardcoded expected counts -- the whole point is that these
reconcile with WHATEVER the live register currently holds):

1. A bare status-filter follow-up ("Exclude rejected applications.") named
   no ward of its own, and `_register_listing()`'s `_geo_scope()` only
   infers a ward from the previous table when the CURRENT message uses the
   word "ward" at all -- so the ward 001 scope from the turn before was
   silently dropped and the answer widened to the whole jurisdiction.
   Fixed by passing `_followup_ctx` into `_register_listing()` and falling
   back to its carried `ward_number`/`block_number` when the carried
   context is itself a register-wide listing.
2. "Include rejected applications too." carries no negation cue at all, so
   it was read as a fresh request for ONLY rejected applications instead of
   adding rejected back to what was already being shown. Two separate
   pre-existing handlers had this bug: `chatbot._resolve_rejected_widen()`
   (a dedicated "widen" rewriter) and the general status-word scan in
   `_register_listing()` / `_explicit_status_request()`. Both now rewrite to
   "all pending approved rejected applications", which collapses to "no
   status filter, rejected not hidden" the same way an explicit "all
   applications" already does.
3. "Do not exclude rejected applications." (a double negative) carries the
   substring "exclude rejected", which `followup_context._NEG_VERB_RE`
   matched regardless of the "do not" in front of it -- so the double
   negative was read as the single negative it contains, the literal
   opposite of what was asked. `followup_context._UNNEGATE_RE` now consumes
   "do not exclude X" / "include X too" BEFORE `_NEG_VERB_RE` ever sees it.

No DB, no LLM for the unit-level checks; the live-DB section needs the DB
(no LLM either way -- this whole path is deterministic).

python test_negation_filter_chain.py
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid

from backend.services import followup_context as fc
from backend.services import chatbot as cb

_results = []


def check(name, ok, detail=""):
    _results.append((name, bool(ok), detail))
    print(("ok  " if ok else "FAIL") + f" {name}" + ("" if ok else f" -- {detail}"))


def test_negation_normalise_unit():
    """Fast, no-DB checks of the text-level rewrite rules."""
    check("negation_normalise: 'exclude rejected' still -> 'not rejected' (unchanged behaviour)",
          fc.negation_normalise("Exclude rejected applications.") == "not rejected applications.")
    check("negation_normalise: 'include rejected too' -> all three statuses named",
          fc.negation_normalise("Include rejected applications too.")
          == "all pending approved rejected applications too.")
    check("negation_normalise: 'do not exclude rejected' (double negative) -> all three statuses named, "
          "NOT 'not rejected'",
          fc.negation_normalise("Do not exclude rejected applications.")
          == "all pending approved rejected applications.")
    check("negation_normalise: 'add rejected back' -> all three statuses named",
          fc.negation_normalise("Add rejected back.") == "all pending approved rejected.")
    # A genuine fresh "don't show rejected" (no un-negate shape) must still exclude it, unchanged.
    check("negation_normalise: a genuine fresh negation is not swallowed by the un-negate rule",
          fc.negation_normalise("Don't show rejected applications.") == "not rejected applications.")


def test_resolve_rejected_widen_unit():
    check("_resolve_rejected_widen: 'include rejected too' widens to ALL statuses, not rejected-only",
          cb._resolve_rejected_widen("include rejected too", [])
          == "all pending approved rejected applications")
    check("_resolve_rejected_widen: carries a channel scope forward when the previous listing named one",
          cb._resolve_rejected_widen(
              "include rejected too",
              [{"role": "assistant", "content": "Found 10 applications from Sub-Registrar"}])
          == "all pending approved rejected applications from sub_registrar")
    check("_resolve_rejected_widen: a message with no 'rejected too' shape returns None",
          cb._resolve_rejected_widen("show pending applications", []) is None)


def _count(resp: str) -> int:
    m = re.search(r"Found\s*<strong>(\d+)</strong>|Found\s*\|?\s*(\d+)\s*\|?\s*", resp)
    if not m:
        return -1
    return int(m.group(1) or m.group(2))


def _has_ward(resp: str, ward: str) -> bool:
    return f"Ward {ward}" in resp or f"ward {ward}" in resp


async def live_main():
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import SISOfficer
    from backend.schemas import OfficerContext
    from backend.services.auth_service import get_officer_jurisdiction_ids

    async with AsyncSessionLocal() as db:
        res = await db.execute(select(SISOfficer).where(SISOfficer.email == "marun_chn@sis.tn.gov.in"))
        officer = res.scalar_one()
        jur = await get_officer_jurisdiction_ids(officer.id, db)
        ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"] + jur["ward_ids"] + jur["block_ids"])
        ctx = OfficerContext(
            officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
            email=officer.email, designation=officer.designation,
            jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
            jurisdiction_ids=[i for i in ids if i])

    async def ask(msg, hist, sid):
        async with AsyncSessionLocal() as db:
            r = await cb.process_chat(db=db, message=msg, officer=ctx, chat_history=hist, session_id=sid)
        resp = (r.get("response") if isinstance(r, dict) else r) or ""
        hist.append({"role": "user", "content": msg})
        hist.append({"role": "assistant", "content": resp})
        return resp

    sid, hist = str(uuid.uuid4()), []
    r1 = await ask("Show applications in ward 001.", hist, sid)
    n1 = _count(r1)
    check("live DB. A1 baseline listing resolves (ward 001, some count > 0)",
          n1 > 0 and _has_ward(r1, "001"), r1[:200])

    r2 = await ask("Exclude rejected applications.", hist, sid)
    n2 = _count(r2)
    check("live DB. A2 'exclude rejected' KEEPS the ward 001 scope from A1",
          _has_ward(r2, "001"), r2[:200])
    check("live DB. A2 count is >= A1 (excluding rejected widens status, never narrows below the open queue)",
          n2 >= n1, f"n1={n1} n2={n2}")

    r3 = await ask("Include rejected applications too.", hist, sid)
    n3 = _count(r3)
    check("live DB. A3 'include rejected too' widens to the FULL set (>= A2), never narrows to rejected-only",
          n3 >= n2, f"n2={n2} n3={n3}; {r3[:200]}")
    check("live DB. A3 keeps the ward 001 scope", _has_ward(r3, "001"), r3[:200])

    r5 = await ask("Now show applications that are not pending.", hist, sid)
    r6 = await ask("Do not exclude rejected applications.", hist, sid)
    n6 = _count(r6)
    check("live DB. A5 'do not exclude rejected' (double negative) resolves to the SAME full count as A3, "
          "not the single-negative 'not rejected' count",
          n6 == n3, f"n3={n3} n6={n6}; {r6[:200]}")
    check("live DB. A5 keeps the ward 001 scope", _has_ward(r6, "001"), r6[:200])


def main() -> None:
    test_negation_normalise_unit()
    test_resolve_rejected_widen_unit()
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
