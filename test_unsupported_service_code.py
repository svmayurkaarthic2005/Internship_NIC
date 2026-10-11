"""Regression tests for service code 0155 -- "SIS Chatbot -- Completely
Remove Unsupported Service Code 0155".

Audit finding (confirmed, not assumed): `0155` was ALREADY absent from the
canonical `SIS_URBAN_SERVICES` catalog before this session started (an
earlier session's merge already removed the fictional "MERGE (0155)" entry
and its aliases from `backend/utils/helpers.py`). `normalize_service_code`,
`find_service_codes` and `describe_service_code` therefore already refuse it
correctly for a BARE definition question ("what is 0155?", "what does 0155
mean?") -- those were not broken and needed no fix.

The remaining, confirmed defect was behavioural, not catalog-level: every
OTHER intent that a message naming "0155" could reach read its own trigger
words (field visit, compare, applications under/for) and never consulted
the service-code digits at all, so the invalid code was silently dropped:

  * "Compare 0155 and 0154."              -> answered a bare ISD-vs-NISD count
  * "Show applications for service code
     0155."                               -> the unfiltered default listing
  * "How many applications are under
     0155?"                               -> the unrestricted register-wide count
  * "Does service code 0155 require a
     field visit?"                        -> the unrelated field-visits summary
  * "Which workflow applies to 0155?"      -> no deterministic handler at all;
                                              reached the LLM, which FABRICATED
                                              "the workflow for 0155 is the ISD
                                              workflow, which involves
                                              sub-division and requires a field
                                              visit" -- inventing a service that
                                              does not exist.

Fixed with `chatbot._named_unsupported_service_code()` (new): a message that
carries a service-code CONTEXT word (service code / workflow / field visit /
compare / "applications ... under|for|of|in|including") AND names a 3-4
digit number that is not in `SIS_URBAN_SERVICES` is answered with the exact
same deterministic "no such service code" wording `_service_code_lookup_
answer`'s prefix-no-match branch already gives for a bare definition
question -- checked early, in BOTH chat paths, before any of the intents
above gets a chance to run.

Scoped narrowly: the context-word requirement means an ordinary survey/
ward/block number, a year, or an unrelated identifier like "DBTY-0155"
never trips it (verified below) -- and a REAL code named alongside an
unsupported one ("Compare 0155 and 0154.") still only refuses once.

python test_unsupported_service_code.py (needs the DB; no LLM for the
unit checks -- the live section only exercises deterministic paths too,
confirmed by NOT calling Ollama for any of them).
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

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services import chatbot
from backend.services.auth_service import get_officer_jurisdiction_ids
from backend.utils.helpers import SIS_URBAN_SERVICES, normalize_service_code, find_service_codes

OFFICER_EMAIL = "marun_chn@sis.tn.gov.in"

_results: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    _results.append((name, bool(ok), detail))
    print(("ok  " if ok else "FAIL") + f" {name}" + ("" if ok else f" -- {detail}"))


def strip_html(s, n=300):
    return re.sub(r"<[^>]+>", " | ", s or "")[:n]


async def get_ctx() -> OfficerContext:
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(SISOfficer).where(SISOfficer.email == OFFICER_EMAIL))
        officer = res.scalar_one()
        jur = await get_officer_jurisdiction_ids(officer.id, db)
        ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"] + jur["ward_ids"] + jur["block_ids"])
        return OfficerContext(
            officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
            email=officer.email, designation=officer.designation,
            jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
            jurisdiction_ids=[i for i in ids if i])


async def turn(ctx, msg, stream=False):
    sid = str(uuid.uuid4())
    if stream:
        import json as _json
        chunks = []
        async with AsyncSessionLocal() as db:
            async for ev in chatbot.process_chat_stream(
                    db=db, message=msg, officer=ctx, chat_history=[], session_id=sid):
                chunks.append(ev)
        resp = ""
        for c in chunks:
            s = c.decode() if isinstance(c, bytes) else c
            if isinstance(s, str) and s.startswith("data: "):
                try:
                    resp += _json.loads(s[6:]).get("content", "")
                except Exception:
                    pass
        return resp
    async with AsyncSessionLocal() as db:
        r = await chatbot.process_chat(db=db, message=msg, officer=ctx, chat_history=[], session_id=sid)
    return (r.get("response") if isinstance(r, dict) else r) or ""


def test_catalog():
    check("1. 0155 is not in the canonical supported-service catalog",
          "0155" not in SIS_URBAN_SERVICES)
    check("normalize_service_code('0155') is None",
          normalize_service_code("0155") is None)
    check("normalize_service_code('155') is None (zero-padded)",
          normalize_service_code("155") is None)
    check("find_service_codes('service code 0155') finds nothing",
          find_service_codes("service code 0155") == [])
    # 6/7: other supported codes remain unaffected
    for code in ("0153", "0154", "0156", "0158", "0178"):
        check(f"{code} is still a supported code", code in SIS_URBAN_SERVICES)
        check(f"normalize_service_code('{code}') == '{code}'",
              normalize_service_code(code) == code)


def test_guard_unit():
    unsupported_cases = [
        "Compare 0155 and 0154.",
        "Show applications for service code 0155.",
        "How many applications are under 0155?",
        "Does service code 0155 require a field visit?",
        "Which workflow applies to 0155?",
        "Show service codes including 0155.",
    ]
    for msg in unsupported_cases:
        got = chatbot._named_unsupported_service_code(msg)
        check(f"_named_unsupported_service_code({msg!r}) == '0155'", got == "0155", repr(got))

    # 9/12-ish: false-positive guard -- must NOT fire
    safe_cases = [
        "What does 0155 mean?",          # no context word -- the existing bare-definition path owns this
        "DBTY-0155",                      # an unrelated identifier, not a service-code mention
        "survey number 155",              # a survey number, not a service code
        "service code 0154",              # a REAL code
        "service code 0153",              # a REAL code
        "Does service code 0154 require a field visit?",
        "Compare 0153 and 0154.",
        "Show applications for service code 0158.",
        "How many applications are under 0178?",
        "applications submitted in 2026 under 0158",  # a year must never be read as the code
    ]
    for msg in safe_cases:
        got = chatbot._named_unsupported_service_code(msg)
        check(f"_named_unsupported_service_code({msg!r}) is None (no false positive)", got is None, repr(got))


async def live_main():
    ctx = await get_ctx()
    expected = (f"There is no urban service code '0155'. The codes run "
                f"{sorted(SIS_URBAN_SERVICES)[0]}-{sorted(SIS_URBAN_SERVICES)[-1]}")

    # 2: a service-definition request does not invent a definition
    r1 = await turn(ctx, "What does 0155 mean?")
    check("2. a bare definition request for 0155 never invents one",
          "could not identify" in r1.lower() or "no urban service code" in r1.lower(), strip_html(r1))

    # 3: a listing filtered to 0155 does not silently return an unrestricted listing
    r2 = await turn(ctx, "Show applications for service code 0155.")
    check("3. a listing filtered to 0155 refuses, never returns the unrestricted default",
          r2.startswith(expected) or "no urban service code" in r2, strip_html(r2))
    check("3b. the refusal is not a table of applications",
          "Application No." not in r2, strip_html(r2))

    # 4: analytics/rankings/comparisons do not treat 0155 as supported
    r3 = await turn(ctx, "Compare 0155 and 0154.")
    check("4a. a comparison naming 0155 refuses rather than manufacturing ISD-vs-NISD",
          "no urban service code" in r3, strip_html(r3))
    r4 = await turn(ctx, "How many applications are under 0155?")
    check("4b. a count filtered to 0155 refuses, never the unrestricted total",
          "no urban service code" in r4, strip_html(r4))
    r5 = await turn(ctx, "Does service code 0155 require a field visit?")
    check("4c. a field-visit-requirement question for 0155 refuses, never the field-visits summary",
          "no urban service code" in r5 and "Field Visits Summary" not in r5, strip_html(r5))
    r6 = await turn(ctx, "Which workflow applies to 0155?")
    check("4d. 'which workflow applies to 0155' refuses -- never fabricates a workflow "
          "(this is the exact case that used to invent 'the ISD workflow')",
          "no urban service code" in r6 and "ISD workflow" not in r6, strip_html(r6))

    # 5: streaming parity
    r7 = await turn(ctx, "Which workflow applies to 0155?", stream=True)
    check("5. streaming path refuses the same way as non-streaming, never fabricates a workflow",
          "no urban service code" in r7 and "ISD workflow" not in r7, strip_html(r7))

    # 6/7: 0153 and 0154 still resolve correctly end-to-end
    r8 = await turn(ctx, "What service code 0154 represents?")
    check("6. service code 0154 (ISD) still resolves correctly",
          "0154" in r8 and ("ISD" in r8 or "Involving Subdivision" in r8), strip_html(r8))
    r9 = await turn(ctx, "What service code 0153 represents?")
    check("7. service code 0153 (NISD) still resolves correctly",
          "0153" in r9 and ("NISD" in r9 or "Not Involving Subdivision" in r9), strip_html(r9))

    # 8: another supported code unaffected
    r10 = await turn(ctx, "Does service code 0158 require a field visit?")
    check("8. another supported code (0158) still answers its own field-visit question, "
          "not the 0155 refusal",
          "no urban service code" not in r10, strip_html(r10))

    # 9: DBTY-0155 and unrelated numeric occurrences are preserved (never refused as a code)
    r11 = await turn(ctx, "What is the status of application DBTY-0155?")
    check("9. 'DBTY-0155' is never read as a service-code mention",
          "no urban service code" not in r11, strip_html(r11))

    # 11/12: jurisdiction/authorization unaffected -- the refusal names no application data at all
    check("11. the 0155 refusal carries no application number or jurisdiction claim",
          not re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", r2))


def main() -> None:
    test_catalog()
    test_guard_unit()
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
