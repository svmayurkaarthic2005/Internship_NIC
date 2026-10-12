"""Regression test for a role-attribution hallucination found live:

"has the ZDT approved <ISD app>?" answered "Yes" purely from the application's
overall status, ignoring that the Zonal Level Tahsildar (ZDT) never reaches an
ISD file at all -- ISD closes at Senior Draughtsman (SD); only NISD files ever
reach the TAHSILDAR stage (see CLAUDE.md's documented workflow chains). The
chatbot confidently attributed an SD decision to a role that took no part in
it, exactly the kind of fabricated-workflow-action failure the project's own
audit notes warn against.

Two separate code paths shared the same defect:
1. `_status_yes_no_question` / `_render_status_yes_no_answer` (the
   `structured_data`-driven application-detail path) answered from `status`
   alone whenever the message matched a yes/no opener + a status word,
   silently dropping any named role. Fixed by adding
   `_render_status_role_answer`, which checks the recorded `decision_desk`
   (the FROM-stage of the workflow hop that actually closed the file) against
   the asked role, and is now tried first whenever a message names both a
   status and a role/stage.
2. `_status_yes_no` (a second, independent register-row-based yes/no
   answerer, reached via a different call site) had the identical blind spot
   and no decision-desk evidence to attribute correctly with. Fixed by adding
   role/stage words to its existing `_YN_SKIP` exclusion list, so it declines
   instead of guessing when a role is named.

No DB or LLM needed for these checks -- they exercise the pure functions
directly with synthetic inputs.
"""
import backend.services.chatbot as cb

bad = []
total = 0


def check(label, cond, detail=None):
    global total
    total += 1
    if not cond:
        bad.append((label, detail))
    print(("ok  " if cond else "FAIL") + f" {label}")


# ---- _status_yes_no (the register-row path) must decline on a named role ----
check('"has the ZDT approved X?" declines (None) -- no decision-desk evidence here',
      cb._status_yes_no("has the ZDT approved this application?", "X", "approved", "COMPLETED", False) is None)
check('"has the tahsildar approved X?" declines (None)',
      cb._status_yes_no("has the tahsildar approved this application?", "X", "approved", "COMPLETED", False) is None)
check('"has the SD rejected X?" declines (None)',
      cb._status_yes_no("has the SD rejected this application?", "X", "rejected", "COMPLETED", False) is None)
check('a bare status question (no role named) still answers directly',
      cb._status_yes_no("has this application been approved?", "X", "approved", "COMPLETED", False) is not None)

# ---- _status_yes_no_question / _stage_yes_no_question both fire on a role+status message ----
_msg = "has the ZDT approved 2026/0154/02/013710?"
check('"_status_yes_no_question" still recognises the status word',
      cb._status_yes_no_question(_msg) == "approved")
check('"_stage_yes_no_question" still recognises the named role',
      cb._stage_yes_no_question(_msg) == "TAHSILDAR")

# ---- _render_status_role_answer: the actual attribution logic ----
# An ISD file closed by SD (decision_desk="SD"), status approved -- ZDT never touched it.
_isd_closed_by_sd = {"application_number": "2026/0154/02/013710", "status": "approved", "decision_desk": "SD"}
_ans = cb._render_status_role_answer(_isd_closed_by_sd, "approved", "TAHSILDAR", "en")
check('ISD file closed by SD: "has ZDT approved it" says No and names SD, not "Yes, approved"',
      _ans.startswith("No") and "Senior Draughtsman" in _ans, _ans)

# A NISD file genuinely closed by TAHSILDAR, status approved -- ZDT DID decide it.
_nisd_closed_by_tahsildar = {"application_number": "2026/0153/02/049763", "status": "approved", "decision_desk": "TAHSILDAR"}
_ans2 = cb._render_status_role_answer(_nisd_closed_by_tahsildar, "approved", "TAHSILDAR", "en")
check('NISD file closed by TAHSILDAR: "has ZDT approved it" correctly says Yes',
      _ans2.startswith("Yes") and "Zonal Level Tahsildar" in _ans2, _ans2)

# Same file, asked about the WRONG status (rejected) -- TAHSILDAR did decide it, but not that way.
_ans3 = cb._render_status_role_answer(_nisd_closed_by_tahsildar, "rejected", "TAHSILDAR", "en")
check('NISD file closed by TAHSILDAR (approved), asked "has Tahsildar REJECTED it": '
      "says No but still correctly names Tahsildar as the decider",
      _ans3.startswith("No") and "Zonal Level Tahsildar" in _ans3 and "approved" in _ans3, _ans3)

# A file not yet decided at all (decision_desk empty) -- no role has approved/rejected it.
_open_file = {"application_number": "2026/0154/02/099999", "status": "pending", "decision_desk": None}
_ans4 = cb._render_status_role_answer(_open_file, "approved", "TAHSILDAR", "en")
check('an open (undecided) file: "has Tahsildar approved it" says No, not yet decided',
      _ans4.startswith("No") and "not yet been decided" in _ans4, _ans4)

print(f"{total - len(bad)}/{total} ok")
import sys
sys.exit(1 if bad else 0)
