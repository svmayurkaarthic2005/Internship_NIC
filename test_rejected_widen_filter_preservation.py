"""Regression for Phase 3.2 of the chatbot defect audit: "include rejected too"
right after a type/ward/block-scoped listing widened the STATUS filter but
silently dropped every other active constraint (application type, ward,
block), reverting to a register-wide listing.

Two independent root causes, both fixed:
1. `chat_dispatch.py` / `chat_dispatch_stream.py` never echoed the listing's
   own application_type/ward_number/block_number/submission_year/
   submission_month back onto `structured_data` as TOP-LEVEL keys, so
   `followup_context.build_context()` (which reads them as top-level keys,
   never a nested "filters" dict) always saved an empty `filters` dict.
2. `followup_context.negation_normalise()` runs on the message BEFORE
   `_resolve_rejected_widen()` is ever reached and already rewrites
   "include rejected too" down to the bare phrase "all pending approved
   rejected" -- `_resolve_rejected_widen`'s own trigger regex no longer
   matched that already-rewritten text, so the ward/type restoration it now
   performs from the saved FollowupContext was never reached at all.

No DB, no LLM for these checks (pure text/dataclass logic); live DB checks
are in the loop-level test suites run alongside this one.
"""
import sys

sys.path.insert(0, r"C:\proj\nic_internship")
from backend.services.chatbot import chat_smalltalk as cs
from backend.services import followup_context as fctx

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


def _ctx(filters):
    return fctx.FollowupContext(
        entity=fctx.ENTITY_APPLICATION_LIST, filters=filters,
        query_type="Pending ISD Applications in Ward 102", intent="pending_applications")


def test_raw_include_rejected_too_preserves_type_and_ward():
    ctx = _ctx({"status": "pending", "ward_number": "102", "application_type": "ISD"})
    r = cs._resolve_rejected_widen("Include rejected too.", [], ctx)
    check("'Include rejected too.' keeps ISD", r and "ISD" in r, r)
    check("'Include rejected too.' keeps ward 102", r and "ward 102" in r, r)


def test_already_normalised_text_still_resolves():
    # The exact text `negation_normalise()` produces, with no "include"/
    # "too" left in it at all -- this is the actual text `_resolve_rejected_
    # widen` receives in the real pipeline, since negation_normalise() runs
    # first.
    ctx = _ctx({"status": "pending", "ward_number": "102", "application_type": "ISD"})
    r = cs._resolve_rejected_widen("all pending approved rejected.", [], ctx)
    check("post-normalise text still resolves and keeps ISD", r and "ISD" in r, r)
    check("post-normalise text still resolves and keeps ward 102", r and "ward 102" in r, r)


def test_block_and_month_year_preserved():
    ctx = _ctx({"status": "pending", "application_type": "NISD", "block_number": "0032",
                "submission_month": 9, "submission_year": 2026})
    r = cs._resolve_rejected_widen("Include rejected applications too.", [], ctx)
    check("NISD + block 0032 + month/year preserved", r and all(s in r for s in ("NISD", "0032", "9", "2026")), r)


def test_channel_preserved_via_structured_filters():
    ctx = _ctx({"status": "pending", "submission_channel": "csc"})
    r = cs._resolve_rejected_widen("Include rejected too.", [], ctx)
    check("channel (CSC) preserved via structured filters", r and "CSC" in r, r)


def test_fresh_request_with_its_own_ward_is_not_treated_as_a_widen():
    ctx = _ctx({"status": "pending", "ward_number": "005", "application_type": "ISD"})
    r = cs._resolve_rejected_widen("Show rejected applications in ward 009.", [], ctx)
    check("a fresh request naming its OWN ward is left alone (returns None)", r is None, r)


def test_no_structured_context_falls_back_to_text_scrape():
    # No FollowupContext at all -- the old channel-scrape fallback still
    # works exactly as before this fix.
    hist = [{"role": "assistant", "content": "Found 6 application(s) from Sub-Registrar."}]
    r = cs._resolve_rejected_widen("Include rejected too.", hist, None)
    check("fallback with no structured context still recovers the channel from text",
          r == "all pending approved rejected applications from sub_registrar", r)


if __name__ == "__main__":
    test_raw_include_rejected_too_preserves_type_and_ward()
    test_already_normalised_text_still_resolves()
    test_block_and_month_year_preserved()
    test_channel_preserved_via_structured_filters()
    test_fresh_request_with_its_own_ward_is_not_treated_as_a_widen()
    test_no_structured_context_falls_back_to_text_scrape()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
