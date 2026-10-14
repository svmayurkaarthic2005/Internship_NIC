"""Regression test for SW_CTX_6_MULTI_RECORDS_NEXT / SW_CTX_6_MULTI_RECORDS_ATHU
(SIS_Intent_Detection_Failure_Cases.jsonl).

Two independent root causes, both with 3 applications carried in a
FollowupContext (ENTITY_APPLICATION_LIST):

1. "athu" (Tanglish for அது/"that/it") was missing from `_BARE_DEICTIC_RE` --
   only "adhu" (the other common Tanglish spelling, "dh" vs "th") and the
   Tamil-script அது/இது were recognised. classify("athu") returned
   FOLLOWUP_NONE, so resolve() stood aside at its very first check and the
   turn fell through every deterministic handler to the LLM fallback.

2. Bare "next" over a just-shown list of 3 applications (no single-row
   cursor established) deliberately means "next PAGE", not "pick row 2" --
   `resolve()` already computed the right answer (a `Resolution` with
   kind=FOLLOWUP_LIST_REFINE, resolved=True, ambiguous=False, empty
   application_numbers, and a clarification stating all 3 were already
   shown), but no code in orchestrator.py's two pipelines ever read that
   clarification: the `ambiguous` branch didn't fire (ambiguous=False) and
   the "resolved" branches below it require at least one application
   number. The clarification was computed and then silently discarded, and
   the turn fell through to the LLM fallback with the same mocked response.

No DB, no LLM for the classify()/resolve() checks themselves.
"""
import sys

from backend.services import followup_context as fctx

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


CTX = fctx.FollowupContext(
    entity=fctx.ENTITY_APPLICATION_LIST,
    application_numbers=["2026/0154/28/001167", "2026/0154/28/001168", "2026/0154/28/001169"],
    query_type="Pending Applications",
    filters={"status": "pending"},
)


def test_athu_is_recognised_as_a_bare_deictic():
    for word in ("athu", "adhu", "ithu", "idhu"):
        check(f"classify({word!r}) is a singular follow-up", fctx.classify(word) == fctx.FOLLOWUP_SINGULAR)

    r = fctx.resolve("athu", CTX, "en")
    check("resolve('athu', ...) is ambiguous over the 3 carried applications",
          r.ambiguous and set(CTX.application_numbers) <= set(_numbers_in(r.clarification)),
          r.clarification)


def test_next_exhausting_pagination_produces_a_clarification_not_silence():
    r = fctx.resolve("next", CTX, "en")
    check("resolve('next', ...) is resolved (not NONE)", r.kind != fctx.FOLLOWUP_NONE and r.resolved)
    check("...with no rows left to page to", not r.application_numbers and not r.application_number)
    check("...and a real clarification, not silence", bool(r.clarification), repr(r.clarification))
    check("...naming all 3 applications as already shown",
          "3" in (r.clarification or "") and "already" in (r.clarification or "").lower(),
          r.clarification)
    # The exact combination orchestrator.py's fix depends on: resolved,
    # not ambiguous, no rows, but a clarification is set.
    check("...resolved=True, ambiguous=False (the combination the fix handles)",
          r.resolved and not r.ambiguous)


def _numbers_in(text):
    import re
    return re.findall(r"\d{4}/\d{3,4}/\d{1,3}/\d+", text or "")


if __name__ == "__main__":
    test_athu_is_recognised_as_a_bare_deictic()
    test_next_exhausting_pagination_produces_a_clarification_not_silence()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
