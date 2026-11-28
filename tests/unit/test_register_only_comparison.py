"""Two-application comparison for register-only service codes (0158 ...), live DB.

Bug (seen live): "Compare 2026/0158/02/000002 and 2026/0158/02/000003" returned only 000002's
register card, and "what about the other one?" repeated it. Causes: the gate answered the first
number's card before any comparison ran, the comparison's per-side reader covered only the
ISD/NISD `applications` table, and the register-only card saved its context without the previous
one, dropping the pair.
"""
import json

import pytest

from backend.services.chatbot.orchestrator import process_chat, process_chat_stream, create_chat_session

X = "2026/0158/02/000002"
Y = "2026/0158/02/000003"
ISD = "2026/0154/02/012766"
MISSING = "2026/0158/02/999999"


async def _run(db, officer, turns):
    sid = str((await create_chat_session(db, str(officer.officer_id))).id)
    return sid, [await process_chat(t, sid, officer, db, None) for t in turns]


def _r(o):
    return o.get("response") or ""


@pytest.mark.asyncio
async def test_compare_two_register_only_files_shows_both(db_session, test_officer):
    _, outs = await _run(db_session, test_officer, [f"Compare {X} and {Y}."])
    assert _r(outs[0]).startswith(f"**{X}** vs **{Y}**")


@pytest.mark.asyncio
async def test_pair_follow_ups_work_for_register_only_files(db_session, test_officer):
    _, outs = await _run(db_session, test_officer, [
        f"Compare {X} and {Y}.", "What about the other one?", "What is its status?", "Show both applications again."])
    assert Y in _r(outs[1]) and X not in _r(outs[1])
    assert _r(outs[2]).startswith(Y) and X not in _r(outs[2])
    assert _r(outs[3]).startswith(f"**{X}** vs **{Y}**")


@pytest.mark.asyncio
async def test_mixed_isd_and_register_only_comparison(db_session, test_officer):
    _, outs = await _run(db_session, test_officer, [f"Compare {X} and {ISD}."])
    assert _r(outs[0]).startswith(f"**{X}** vs **{ISD}**")


@pytest.mark.asyncio
async def test_missing_second_file_is_reported_not_collapsed(db_session, test_officer):
    _, outs = await _run(db_session, test_officer, [f"Compare {X} and {MISSING}."])
    final = _r(outs[0])
    assert "999999" in final and not final.startswith(f"**{X}** vs")


@pytest.mark.asyncio
async def test_single_register_only_file_still_gets_its_card(db_session, test_officer):
    _, outs = await _run(db_session, test_officer, [f"Show details of {X}."])
    assert X in _r(outs[0]) and Y not in _r(outs[0])


@pytest.mark.asyncio
async def test_stream_matches_non_stream(db_session, test_officer):
    q = f"Compare {X} and {Y}."
    _, outs = await _run(db_session, test_officer, [q])
    sid = str((await create_chat_session(db_session, str(test_officer.officer_id))).id)
    parts = []
    async for raw in process_chat_stream(q, sid, test_officer, db_session, None):
        for p in (raw.decode("utf-8") if isinstance(raw, bytes) else raw).split("\n"):
            if p.startswith("data: "):
                try:
                    parts.append(json.loads(p[6:]).get("content") or "")
                except (ValueError, AttributeError):
                    pass
    assert "".join(parts).strip() == _r(outs[0]).strip()
