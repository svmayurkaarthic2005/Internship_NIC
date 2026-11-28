"""P3.3 completion -- deterministic, database-backed workflow history for one application.

Live DB facts for 2026/0153/02/045470 (workflow_history, stored values; source update_dt agrees):
  2026-08-29 13:33  Submission -> SIS
  2026-09-10 19:35  SIS -> TAHSILDAR (Zonal Deputy Tahsildar for NISD)
  2026-09-13 21:01  TAHSILDAR -> REJECTED   remark "[N/06]//NO LINK ..."
Everything is the real pipeline on the live DB except where a test says "mocked".
"""
import uuid

import pytest

from backend.schemas import OfficerContext
from backend.services.chatbot import howto_qa
from backend.services.chatbot.chat_smalltalk import _workflow_history_answer, _wfh_fmt_dt
from backend.services.chatbot.orchestrator import process_chat, create_chat_session

A = "2026/0153/02/045470"
B = "2026/0153/02/045472"
STALE = [
    {"role": "user", "content": "Compare 2026/0154/02/012766 and 2026/0154/02/999999."},
    {"role": "assistant", "content": "**2026/0154/02/012766** vs **2026/0154/02/999999**"},
]
FOCUS_A = [f"Compare {A} and {B}.", "What about the other one?", "Switch back to the first application."]


async def _run(db, officer, turns, client=None):
    sid = str((await create_chat_session(db, str(officer.officer_id))).id)
    outs = []
    for m in turns:
        outs.append(await process_chat(m, sid, officer, db, list(client) if client else None))
    return outs


def _r(out):
    return out.get("response") or ""


@pytest.mark.asyncio
@pytest.mark.parametrize("client", [None, STALE])
@pytest.mark.parametrize("question", [
    "Show its workflow history.",
    "Show this application's previous stages.",
    "Show the events recorded for this application.",
])
async def test_full_log_for_focus_is_deterministic_and_ordered(db_session, test_officer, question, client):
    final = _r((await _run(db_session, test_officer, FOCUS_A + [question], client))[-1])
    assert final.startswith(f"Workflow history for {A} (3 step(s))"), question
    i1, i2, i3 = final.index("2026-08-29"), final.index("2026-09-10"), final.index("2026-09-13")
    assert i1 < i2 < i3                                   # stored order kept
    assert "NO LINK" in final                             # recorded remark carried
    assert B not in final and "012766" not in final


@pytest.mark.asyncio
async def test_before_rejection_lists_the_recorded_preceding_events(db_session, test_officer):
    final = _r((await _run(db_session, test_officer, FOCUS_A + ["What happened before it was rejected?"], STALE))[-1])
    assert A in final
    assert "immediately before" in final.lower()          # P2 contract kept
    assert "2026-08-29 13:33" in final and "2026-09-10 19:35" in final
    assert "Submission" in final and "Zonal Deputy Tahsildar" in final
    assert "field visit" not in final.lower()             # NISD has none; nothing invented


@pytest.mark.asyncio
async def test_when_did_it_move_to_tahsildar(db_session, test_officer):
    final = _r((await _run(db_session, test_officer, FOCUS_A + ["When did it move to the Tahsildar stage?"]))[-1])
    assert final.startswith(f"{A} moved to the Zonal Deputy Tahsildar stage on 2026-09-10 19:35")


@pytest.mark.asyncio
async def test_explicit_b_resolves_b_over_focus_a(db_session, test_officer):
    final = _r((await _run(db_session, test_officer, FOCUS_A + [f"Show the workflow history of {B}."], STALE))[-1])
    assert f"Workflow history for {B}" in final and A not in final


@pytest.mark.asyncio
async def test_never_reached_stage_is_stated_not_invented(db_session, test_officer):
    out = await _workflow_history_answer(db_session, test_officer, "When did it move to the SD stage?", A, False)
    assert out == f"{A}'s recorded workflow never reached the SD (Senior Draughtsman) stage." or "never reached" in out


def test_timestamps_are_the_stored_values_not_shifted():
    from datetime import datetime, timezone
    assert _wfh_fmt_dt(datetime(2026, 9, 10, 19, 35, 14, tzinfo=timezone.utc)) == "2026-09-10 19:35"


@pytest.mark.parametrize("q,topic", [
    ("How does the workflow for an NISD application work?", "steps_nisd"),
    ("Explain the NISD workflow.", "steps_nisd"),
    ("What is the workflow for an ISD application?", "steps_isd"),
])
def test_procedural_workflow_questions_stay_on_howto(q, topic):
    assert howto_qa.match(q) == topic


@pytest.mark.parametrize("q", [
    "Show its workflow history.",
    "What is the workflow status of my NISD applications?",
    f"Explain the workflow of {A}.",
])
def test_data_workflow_questions_are_not_howto(q):
    assert howto_qa.match(q) is None


@pytest.mark.asyncio
async def test_procedural_question_answered_by_howto_live(db_session, test_officer):
    out = (await _run(db_session, test_officer, ["How does the workflow for an NISD application work?"]))[-1]
    assert out["intent"].startswith("howto")
    assert "NISD (0153)" in _r(out)


# ---- empty vs inaccessible ------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_history_is_reported_as_empty(db_session, test_officer, monkeypatch):
    # MOCKED: no seeded ISD/NISD file has zero workflow rows, so the data function is stubbed
    from backend.services import postgres

    async def _empty(db, officer, app):
        return []
    monkeypatch.setattr(postgres, "get_workflow_history", _empty)
    out = await _workflow_history_answer(db_session, test_officer, "Show its workflow history.", A, False)
    assert out == f"No workflow history is recorded for {A}."


@pytest.mark.asyncio
async def test_inaccessible_application_returns_no_record(db_session):
    # synthetic officer with no jurisdiction rows (all 3 seeded officers can see A); real access path
    nobody = OfficerContext(officer_id=uuid.uuid4(), employee_id="X", name="X", email="x@example.invalid",
                            designation="SIS", jurisdiction_type="ward", jurisdiction_name="none",
                            jurisdiction_ids=[])
    out = await _workflow_history_answer(db_session, nobody, "Show its workflow history.", A, False)
    assert out is None          # never a log, never "no history" -- the gate's refusal answers it
