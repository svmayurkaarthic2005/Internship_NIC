"""Regression test for a same-message filter contradiction found live:

"exclude rejected applications, but include rejected ones too" silently
resolved to "excluding rejected" -- the "include ... too" half was never
acknowledged, exactly the "silently choose whichever filter was parsed last"
failure the project's audit notes explicitly warn against. `neg_scope.py`'s
exclusion-tracking only ever grows an exclusion set within one message (there
is no same-message "un-exclude"; that mechanism only works ACROSS turns via
`_unexclude`'s `prior_ex`), so a same-message contradiction had nothing to
catch it.

Fixed with `neg_scope.detect_contradiction()`: scans for an explicit
"include <value> ... too/also" phrase whose value is ALSO in the computed
exclusion set, and returns a clarification instead of a filtered listing.

Root-cause note: the first working version of the chatbot.py wiring checked
`_original_message`, which by that point in the pipeline had already been
rewritten by an earlier normalisation pass (`followup_context.py`'s
`_UNNEGATE_RE`, which turns "include X too" into the generic phrase "all
pending approved rejected", destroying which specific value was being
re-included). Fixed by checking `_RAW_MESSAGE.get()` -- the untouched
original text captured at the very top of `process_chat`/`process_chat_stream`
-- instead.

No DB or LLM needed for these checks.
"""
import backend.services.neg_scope as ns

bad = []
total = 0


def check(label, cond, detail=None):
    global total
    total += 1
    if not cond:
        bad.append((label, detail))
    print(("ok  " if cond else "FAIL") + f" {label}")


# ---- genuine same-message contradictions: must return a clarification ----
_contra_cases = [
    "exclude rejected applications, but include rejected ones too",
    "show all except rejected ones, include every rejected one too",
]
for m in _contra_cases:
    r = ns.detect_contradiction(m)
    check(f'"{m}" asks for clarification naming "rejected"',
          r is not None and "rejected" in r, r)

# ---- ordinary negation (no contradiction) must NOT be flagged ----
_clean_cases = [
    "exclude rejected applications",
    "show pending applications",
    "show applications that are not service code 0158",
    "include rejected applications",  # include alone, nothing excluded to conflict with
]
for m in _clean_cases:
    r = ns.detect_contradiction(m)
    check(f'"{m}" is NOT flagged as contradictory', r is None, r)

# ---- excluding one status while including a DIFFERENT status is not a contradiction ----
r = ns.detect_contradiction("exclude rejected applications, include pending ones too")
check('excluding "rejected" while including "pending" (different values) is not flagged',
      r is None, r)

# ---- an application-number-bearing message is left alone (same guard as rewrite()) ----
r = ns.detect_contradiction("exclude rejected, include rejected too for 2026/0154/28/001197")
check("a message naming an application number is never flagged here (single-record question, "
      "not a register-wide filter)", r is None, r)

print(f"{total - len(bad)}/{total} ok")
import sys
sys.exit(1 if bad else 0)
