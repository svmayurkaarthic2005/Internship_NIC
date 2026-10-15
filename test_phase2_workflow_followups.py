"""Targeted regression test suite for Phase 2: Workflow-history and follow-up defects.

Covers:
2.1 Workflow stage alias ordering (_WFH_STAGE_ALIASES and _wfh_find_stage)
2.2 First-action workflow-history questions (_workflow_history_answer, _WFH_FIRST_WHO_RE, etc.)
2.3 False-premise workflow verification (_workflow_steps_topic & _workflow_steps_answer)
2.4 Field-visit column follow-ups (_fv_cols_parse_loose, _COLW with owner, word boundaries)
2.5 Complaints vs contextual recheck routing (_recheck.is_pushback, mutation guard)
"""
import sys
import re
import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

total = 0
bad = []

def check(name, condition, detail=""):
    global total
    total += 1
    if condition:
        print(f"ok   {name}")
    else:
        bad.append((name, detail))
        print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))


# ─────────────────────────────────────────────────────────────────────────────
# 2.1 Workflow stage alias ordering
# ─────────────────────────────────────────────────────────────────────────────
from backend.services.chatbot.chat_smalltalk import _wfh_find_stage, _WFH_AFTER_RE, _WFH_TRANSITION_FROM_RE

check("2.1: 'Deputy Inspector Surveyor' resolves to DIS, not SIS",
      _wfh_find_stage("Deputy Inspector Surveyor") == "DIS",
      _wfh_find_stage("Deputy Inspector Surveyor"))

check("2.1: 'Sub Inspector Surveyor' resolves to SIS",
      _wfh_find_stage("Sub Inspector Surveyor") == "SIS",
      _wfh_find_stage("Sub Inspector Surveyor"))

check("2.1: 'Senior Draughtsman' resolves to SD",
      _wfh_find_stage("Senior Draughtsman") == "SD",
      _wfh_find_stage("Senior Draughtsman"))

check("2.1: 'surveyor' alone still resolves to SIS",
      _wfh_find_stage("surveyor") == "SIS",
      _wfh_find_stage("surveyor"))

check("2.1: Tamil 'துணை ஆய்வாளர்' resolves to DIS",
      _wfh_find_stage("துணை ஆய்வாளர்") == "DIS",
      _wfh_find_stage("துணை ஆய்வாளர்"))

check("2.1: Tamil 'நில அளவையர்' resolves to SIS",
      _wfh_find_stage("நில அளவையர்") == "SIS",
      _wfh_find_stage("நில அளவையர்"))

# Stage extracted after "after"
after_q = "What happened after SD when SIS forwarded it?"
after_m = re.search(r"\b(?:immediately\s+)?after\s+([a-zA-Z\s\u0B80-\u0BFF]+)", after_q, re.I)
words = after_m.group(1).strip().split() if after_m else []
stage_after = None
for n_w in (3, 2, 1):
    if len(words) >= n_w:
        stage_after = _wfh_find_stage(" ".join(words[:n_w]))
        if stage_after:
            break
check("2.1: 'What happened after SD when SIS forwarded it?' extracts SD, not SIS",
      stage_after == "SD", stage_after)

# Stage extracted for transition "moved from DIS to SD"
trans_q = "When did it move from DIS to SD?"
m_from_to = re.search(r"\bmoved?\s+from\s+([a-zA-Z\s\u0B80-\u0BFF]+?)\s+to\s+([a-zA-Z\s\u0B80-\u0BFF]+)", trans_q, re.I)
from_st = _wfh_find_stage(m_from_to.group(1)) if m_from_to else None
to_st = _wfh_find_stage(m_from_to.group(2)) if m_from_to else None
check("2.1: 'When did it move from DIS to SD?' extracts from=DIS, to=SD",
      from_st == "DIS" and to_st == "SD", f"from={from_st}, to={to_st}")


# ─────────────────────────────────────────────────────────────────────────────
# 2.2 First-action workflow-history questions
# ─────────────────────────────────────────────────────────────────────────────
import asyncio
from unittest.mock import AsyncMock, patch

async def test_2_2():
    from backend.services.chatbot.chat_smalltalk import _workflow_history_answer
    
    # Deterministic mock hops: Hop 0 is SIS -> SD, Hop 1 is SD -> DIS
    mock_hops = [
        {"from_stage": "SIS", "to_stage": "SD", "performed_at": datetime.datetime(2025, 1, 10, 10, 0), "remarks": "forwarded", "rejection_reason": None},
        {"from_stage": "SD", "to_stage": "DIS", "performed_at": datetime.datetime(2025, 1, 12, 14, 30), "remarks": "sketch ready", "rejection_reason": None},
    ]
    
    with patch("backend.services.postgres.get_workflow_history", new=AsyncMock(return_value=mock_hops)):
        # Who performed the first action
        ans_who_first = await _workflow_history_answer(None, None, "Who performed the first workflow action?", "2025/0154/28/000001", False)
        check("2.2: 'Who performed the first workflow action?' names SIS desk",
              "SIS" in ans_who_first and "first workflow action" in ans_who_first, ans_who_first)
        
        ans_who_first_bare = await _workflow_history_answer(None, None, "Who performed the first action?", "2025/0154/28/000001", False)
        check("2.2: 'Who performed the first action?' names SIS desk, not SD/DIS",
              "SIS" in ans_who_first_bare and "first workflow action" in ans_who_first_bare, ans_who_first_bare)

        # Who performed the most recent action
        ans_who_last = await _workflow_history_answer(None, None, "Who performed the most recent action?", "2025/0154/28/000001", False)
        check("2.2: 'Who performed the most recent action?' names SD desk (hops[-1] actor)",
              "SD" in ans_who_last and "most recent workflow action" in ans_who_last, ans_who_last)

        # What happened first
        ans_what_first = await _workflow_history_answer(None, None, "What happened first?", "2025/0154/28/000001", False)
        check("2.2: 'What happened first?' reports first transition",
              "SIS" in ans_what_first and "SD" in ans_what_first and "first workflow action" in ans_what_first, ans_what_first)

        # When did the first action happen
        ans_when_first = await _workflow_history_answer(None, None, "When did the first action happen?", "2025/0154/28/000001", False)
        check("2.2: 'When did the first action happen?' reports timestamp 2025-01-10",
              "2025-01-10" in ans_when_first, ans_when_first)

asyncio.run(test_2_2())


# ─────────────────────────────────────────────────────────────────────────────
# 2.3 False-premise workflow verification
# ─────────────────────────────────────────────────────────────────────────────
from backend.services.chatbot.chat_smalltalk import _workflow_steps_topic, _workflow_steps_answer

t_sd_nisd = _workflow_steps_topic("SD prepares the sketch for NISD, correct?")
check("2.3: 'SD prepares the sketch for NISD, correct?' topic is PREMISE_CORRECTION:SD_NISD",
      t_sd_nisd == "PREMISE_CORRECTION:SD_NISD", t_sd_nisd)
ans_sd_nisd = _workflow_steps_answer(t_sd_nisd, is_tamil=False)
check("2.3: Answer clarifies SD is for ISD only and NISD has no sketch",
      "No, that premise is incorrect" in ans_sd_nisd and "ISD (0154)" in ans_sd_nisd and "NISD (0153)" in ans_sd_nisd, ans_sd_nisd)

t_dis_nisd = _workflow_steps_topic("DIS reviews NISD applications, right?")
check("2.3: 'DIS reviews NISD applications, right?' topic is PREMISE_CORRECTION:DIS_NISD",
      t_dis_nisd == "PREMISE_CORRECTION:DIS_NISD", t_dis_nisd)
ans_dis_nisd = _workflow_steps_answer(t_dis_nisd, is_tamil=False)
check("2.3: Answer clarifies DIS is for ISD only and NISD skips DIS",
      "No, that premise is incorrect" in ans_dis_nisd and "DIS" in ans_dis_nisd and "ZDT" in ans_dis_nisd, ans_dis_nisd)

t_tah_cross = _workflow_steps_topic("Tahsildar handles NISD and DIS handles ISD, right?")
check("2.3: Cross-chain claim 'Tahsildar handles NISD and DIS handles ISD, right?' returns ALL",
      t_tah_cross == "ALL", t_tah_cross)

t_ta_sd = _workflow_steps_topic("NISD-க்கு SD வரைபடம் தயாரிப்பார், சரியா?")
check("2.3: Tamil 'NISD-க்கு SD வரைபடம் தயாரிப்பார், சரியா?' detected",
      t_ta_sd == "PREMISE_CORRECTION:SD_NISD", t_ta_sd)


# ─────────────────────────────────────────────────────────────────────────────
# 2.4 Field-visit column follow-ups
# ─────────────────────────────────────────────────────────────────────────────
from backend.services.chatbot.chat_smalltalk import _col_names, _fv_cols_parse, _fv_cols_parse_loose
from backend.services.chatbot.chat_corrections import _fv_followup
from backend.services.followup_context import FollowupContext

# Check column word owner
check("2.4: _col_names('owner') recognises 'owner'",
      "owner" in _col_names("owner"), _col_names("owner"))

# Check embedded words do not trigger column matches
check("2.4: 'Can you show field visits with survey number' does NOT match 'can' as column",
      "can" not in _col_names("survey number"), _col_names("survey number"))
check("2.4: 'update forwarded prototype' matches no column words",
      _col_names("update forwarded prototype") == [], _col_names("update forwarded prototype"))

# Remove owner column
drop_res = _fv_cols_parse("remove the owner column")
check("2.4: 'remove the owner column' drops owner",
      drop_res == ([], ["owner"]), drop_res)

# Compound addition and removal
loose_res = _fv_cols_parse_loose("Show field visits without ward, and include block")
check("2.4: 'Show field visits without ward, and include block' drops ward and adds block",
      "ward" in loose_res[1] and "block" in loose_res[0], loose_res)

loose_res2 = _fv_cols_parse_loose("Include ward and block, but exclude status")
check("2.4: 'Include ward and block, but exclude status' adds ward+block, drops status",
      "ward" in loose_res2[0] and "block" in loose_res2[0] and "status" in loose_res2[1], loose_res2)

# Verify _fv_followup preserves scope and sets columns
dummy_ctx = FollowupContext(
    entity="application_list",
    application_numbers=["2025/0154/28/000001", "2025/0154/28/000002"],
    filters={"about_visit": True, "sort_by": "scheduled_date", "sort_dir": "asc"}
)
class DummyRes:
    resolved = False
    entity = "application_list"
    application_number = None

fv_msg, fv_scope = _fv_followup("Show field visits without ward, and include block", dummy_ctx, DummyRes())
check("2.4: _fv_followup preserves application scope",
      fv_scope == ["2025/0154/28/000001", "2025/0154/28/000002"], fv_scope)
check("2.4: _fv_followup output message incorporates columns",
      "block" in fv_msg and "ward" in fv_msg, fv_msg)


# ─────────────────────────────────────────────────────────────────────────────
# 2.5 Complaints that should trigger a correction or recheck
# ─────────────────────────────────────────────────────────────────────────────
from backend.services.chatbot.recheck import is_pushback

check("2.5: 'That is wrong.' matches pushback",
      is_pushback("That is wrong."), is_pushback("That is wrong."))
check("2.5: 'That answer is not correct.' matches pushback",
      is_pushback("That answer is not correct."), is_pushback("That answer is not correct."))
check("2.5: Tamil 'அது தவறு' matches pushback",
      is_pushback("அது தவறு"), is_pushback("அது தவறு"))
check("2.5: Tamil 'அது சரியில்லை' matches pushback",
      is_pushback("அது சரியில்லை"), is_pushback("அது சரியில்லை"))

# Verify mutation request guard in _recheck_turn
from backend.services.chatbot.chat_smalltalk import _is_mutation_request
check("2.5: Mutation request 'approve this application' recognised",
      _is_mutation_request("approve this application"), _is_mutation_request("approve this application"))


print(f"\nPhase 2 tests: {total - len(bad)}/{total} passed")
if bad:
    print("FAILED tests:")
    for b, d in bad:
        print(f"  - {b}: {d}")
    sys.exit(1)
