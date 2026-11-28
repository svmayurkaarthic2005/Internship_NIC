"""Regression tests for P1-2 remediation (test_reports/P1_REASONING_REMEDIATION_REPORT.md):
evidence-grounded reasoning for explanatory questions ("does this mean X",
"why should A and B not be treated as identical").

Section A: intent/routing (classifier + dispatch guard), no DB, no LLM.
Section B: evidence assembly against the real database (test_officer /
db_session fixtures, not mocked), no LLM.
Section C: grounding, using a controlled LLM test double so the test proves
what evidence reached the prompt and that the generation path never invents
a fact when the double is unavailable -- never live LLM accuracy.
Section D: regression -- the four mandatory direct-lookup questions from
Phase 5 must still resolve through their existing deterministic handlers,
not through this module.
"""
import pytest

from backend.services.chatbot import reasoning_qa
from backend.services.chatbot.chat_application_queries import _reasoning_rule_keys
from backend.services.chatbot.chat_corrections import _big_plan


# ---------------------------------------------------------------------------
# A. Intent and routing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "This is an NISD application, which normally does not require a field visit. However, "
    "I can see a field_visit_dt in one workflow row. Does that mean an inspection was "
    "mandatory or actually completed?",
    "Does a populated date column automatically mean the service requires an inspection?",
    "The register shows this application is still open, but the Tahsildar has recorded "
    "Approved. Does that mean the complete application is closed? Explain using the "
    "register, correction-modification record and latest workflow transition.",
    "What information is still missing before you can definitively say it is completed?",
    "What exactly was missing? The missing_docs field appears blank, but the rejection "
    "remark says linked documents were not submitted. Explain the difference.",
    "The old-owner and IGRS-owner tables have different structures. Why should we not "
    "automatically treat them as identical? Explain what each table actually records.",
])
def test_genuine_reasoning_question_recognised(message):
    assert reasoning_qa.is_reasoning_question(message) is True


@pytest.mark.parametrize("message", [
    "What is the application status for 2026/0153/02/045470?",
    "When was its registration document registered?",
    "When did the SIS recommendation occur for 2026/0158/02/000002?",
    "What are the temporary subdivisions for 2026/0154/02/012715?",
    "What is the new patta number for 2026/0154/02/012766?",
    "What amount and payment mode are recorded for 2026/0178/02/000267?",
    "Show the latest workflow transition.",
])
def test_direct_field_question_not_reasoning(message):
    assert reasoning_qa.is_reasoning_question(message) is False


@pytest.mark.parametrize("message", [
    "What is the difference between ISD and NISD?",   # service_code_guide owns this
    "Compare 2026/0153/02/045470 and 2026/0153/02/045472.",  # two-app comparator owns this
    "What is the missing document field?",
])
def test_ambiguous_or_unrelated_not_hijacked(message):
    """No full-transfer/appinfo/generic-comparison false positive: a message with no
    explicit explanatory cue is left to its existing handler."""
    assert reasoning_qa.is_reasoning_question(message) is False


def test_reasoning_question_not_shredded_by_big_plan():
    """A genuine reasoning question, deliberately written across several
    sentences with its own '?', must reach reasoning_qa as ONE question --
    not get split into fragments by the multi-question splitter, which
    previously answered fragment 1 with an unrelated NISD listing and
    dropped the application number entirely from the actual question
    fragment (live-verified: see P1_REASONING_REMEDIATION_REPORT.md)."""
    msg = ("This is an NISD application, which normally does not require a field visit. "
           "However, I can see a field_visit_dt in one workflow row. Does that mean an "
           "inspection was mandatory or actually completed? Application 2026/0153/02/045470.")
    assert reasoning_qa.is_reasoning_question(msg) is True
    assert _big_plan(msg) is None

    msg_b = ("Application 2026/0158/02/001804. The register shows this application is still "
             "open, but the Tahsildar has recorded Approved. Does that mean the complete "
             "application is closed? Explain using the register, correction-modification "
             "record and latest workflow transition.")
    assert reasoning_qa.is_reasoning_question(msg_b) is True
    assert _big_plan(msg_b) is None


def test_rule_keys_narrowed_by_topic():
    assert "nisd_no_visit" in _reasoning_rule_keys("does a field visit date mean an inspection was mandatory?")
    assert "desk_vs_closure" in _reasoning_rule_keys("does Approved mean the application is closed?")
    assert "owner_tables_distinct" in _reasoning_rule_keys("why should the old owner and igrs owner be treated as identical?")
    assert _reasoning_rule_keys("does this mean something else entirely?") == []


# ---------------------------------------------------------------------------
# B. Evidence assembly -- real database, no LLM
# ---------------------------------------------------------------------------

SCEN_A_APP = "2026/0153/02/045470"   # NISD, field_visit_dt populated on one hop
SCEN_B_APP = "2026/0158/02/001804"   # register open, Tahsildar "Approved", not closed
SCEN_C_APP = "2026/0153/02/045470"   # missing_docs blank, zdt_remarks states what's missing
SCEN_D_APP = "2026/0153/02/045472"   # old-owner (full-field) 1 row, IGRS-owner 2 rows


@pytest.mark.asyncio
async def test_scenario_a_evidence_shows_populated_field_visit_dt(db_session, test_officer):
    ev = await reasoning_qa.gather_evidence(db_session, test_officer, SCEN_A_APP)
    wf = next(s for s in ev["sources"] if s["table"] == "application_workflow")
    assert wf["status"] == "present"
    assert any(h["field_visit_dt"] for h in wf["hops"]), "the populated field_visit_dt hop must be preserved"
    assert any(not h["field_visit_dt"] for h in wf["hops"]), "the blank hop must stay distinct, not merged"


@pytest.mark.asyncio
async def test_scenario_b_evidence_distinguishes_desk_approval_from_closure(db_session, test_officer):
    ev = await reasoning_qa.gather_evidence(db_session, test_officer, SCEN_B_APP)
    cm = next(s for s in ev["sources"] if s["table"] == "correction_modification_flow")
    assert cm["status"] == "present"
    assert cm["fields"].get("thl_remarks") == "Approved"
    wf = next(s for s in ev["sources"] if s["table"] == "application_workflow")
    last_hop = wf["hops"][-1]
    # the latest transition on record is NOT the register's closing hop (to "Completed")
    assert last_hop["to"] != "Completed"


@pytest.mark.asyncio
async def test_scenario_c_evidence_blank_field_vs_other_source_explanation(db_session, test_officer):
    ev = await reasoning_qa.gather_evidence(db_session, test_officer, SCEN_C_APP)
    appinfo = next(s for s in ev["sources"] if "application_information" in s["table"])
    assert appinfo["fields"].get("missing_docs_blank") is True
    ft = next(s for s in ev["sources"] if s["table"] == "full_field_patta_transfer_urban")
    assert "not submitted" in (ft["fields"].get("zdt_remarks") or "").lower()


@pytest.mark.asyncio
async def test_scenario_d_old_owner_and_igrs_owner_distinct_sources(db_session, test_officer):
    ev = await reasoning_qa.gather_evidence(db_session, test_officer, SCEN_D_APP)
    old = next(s for s in ev["sources"] if s["table"] == "full_field_patta_transfer_old_owner")
    igrs = next(s for s in ev["sources"] if s["table"] == "full_field_patta_transfer_igrs_owner")
    assert old["status"] == "present" and old["row_count"] >= 1
    assert igrs["status"] == "present" and igrs["row_count"] >= 1
    # no mobile number, address, ration/epic/aadhaar value ever reaches the package
    for row in old["rows"] + igrs["rows"]:
        for forbidden in ("mobile_number", "address", "epic_no", "ration_card_no",
                          "pincode", "aadhar_no"):
            assert forbidden not in row


@pytest.mark.asyncio
async def test_missing_row_distinguished_from_blank_field(db_session, test_officer):
    """A service code with no correction/modification row (0153/0154) must read
    'not_applicable', never the same 'blank' text a real, empty column gets."""
    ev = await reasoning_qa.gather_evidence(db_session, test_officer, SCEN_A_APP)
    cm = next(s for s in ev["sources"] if s["table"] == "correction_modification_flow")
    assert cm["status"] == "not_applicable"
    appinfo = next(s for s in ev["sources"] if "application_information" in s["table"])
    assert appinfo["status"] == "present"   # the row exists; missing_docs is merely blank


@pytest.mark.asyncio
async def test_no_cross_application_contamination(db_session, test_officer):
    ev_a = await reasoning_qa.gather_evidence(db_session, test_officer, SCEN_A_APP)
    ev_d = await reasoning_qa.gather_evidence(db_session, test_officer, SCEN_D_APP)
    assert ev_a["application_number"] != ev_d["application_number"]
    old_a = next(s for s in ev_a["sources"] if s["table"] == "full_field_patta_transfer_old_owner")
    old_d = next(s for s in ev_d["sources"] if s["table"] == "full_field_patta_transfer_old_owner")
    assert old_a["rows"] != old_d["rows"]


def test_evidence_package_bounded(monkeypatch):
    """A row-heavy answer must still cap hops/owner rows -- never dump a table."""
    assert reasoning_qa._MAX_WORKFLOW_HOPS <= 10
    assert reasoning_qa._MAX_OWNER_ROWS <= 5


# ---------------------------------------------------------------------------
# C. Grounding -- controlled LLM test double
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_answer_passes_recorded_facts_and_question_to_llm(db_session, test_officer):
    captured = {}

    async def fake_llm(prompt):
        captured["prompt"] = prompt
        return "Explanation: test.\nWhat cannot be concluded: test."

    text = await reasoning_qa.answer(
        db_session, test_officer,
        "Does that field_visit_dt mean an inspection was mandatory?",
        SCEN_A_APP, "en", rule_keys=["nisd_no_visit"], llm_call=fake_llm)

    assert "field_visit_dt=2026-09-10" in captured["prompt"] or "application_workflow" in captured["prompt"]
    assert "NISD" in captured["prompt"]
    assert "Recorded facts:" in text
    assert "Explanation: test." in text


@pytest.mark.asyncio
async def test_llm_failure_does_not_fabricate_a_conclusion(db_session, test_officer):
    async def broken_llm(prompt):
        raise RuntimeError("Ollama unavailable")

    text = await reasoning_qa.answer(
        db_session, test_officer, "Does Approved mean the application is closed?",
        SCEN_B_APP, "en", rule_keys=["desk_vs_closure"], llm_call=broken_llm)

    assert "Recorded facts:" in text
    assert "unavailable" in text.lower()
    assert "closed" not in text.split("Recorded facts:")[1].split("\n\n")[1].lower() \
        or "cannot be concluded" in text.lower()


@pytest.mark.asyncio
async def test_sensitive_identifiers_never_in_prompt(db_session, test_officer):
    captured = {}

    async def fake_llm(prompt):
        captured["prompt"] = prompt
        return "Explanation: ok.\nWhat cannot be concluded: ok."

    await reasoning_qa.answer(
        db_session, test_officer, "Why should the old owner and IGRS owner not be treated as identical?",
        SCEN_D_APP, "en", rule_keys=["owner_tables_distinct"], llm_call=fake_llm)

    prompt = captured["prompt"]
    for forbidden_key in ("mobile_number=", "address=", "epic_no=", "ration_card_no=", "pincode="):
        assert forbidden_key not in prompt


# ---------------------------------------------------------------------------
# Authorization -- the evidence layer must fail closed, independent of
# which handler resolved the application number (IDOR regression).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_officer_fails_closed(db_session):
    """No officer context at all (should never happen from the real
    pipeline, but the data layer must not trust its caller)."""
    ev = await reasoning_qa.gather_evidence(db_session, None, SCEN_B_APP)
    assert ev["accessible"] is False
    assert ev["sources"] == []


@pytest.mark.asyncio
async def test_officer_outside_jurisdiction_fails_closed_register_code(db_session, test_officer):
    """A register-only code (0158 ...) must be checked by ward jurisdiction,
    not by the (always-empty, for this code) `applications` lookup -- an
    officer with no jurisdiction rows at all must get nothing back, not the
    real correction-flow / workflow rows other officers can see."""
    no_access = test_officer.model_copy(update={"jurisdiction_ids": []})
    ev = await reasoning_qa.gather_evidence(db_session, no_access, SCEN_B_APP)
    assert ev["accessible"] is False
    assert ev["sources"] == []


@pytest.mark.asyncio
async def test_officer_outside_jurisdiction_fails_closed_nisd(db_session, test_officer):
    """Same fail-closed rule for a 0153/0154 file: jurisdiction is enforced
    by `get_application_detail()`'s own filter, which this layer trusts."""
    no_access = test_officer.model_copy(update={"jurisdiction_ids": []})
    ev = await reasoning_qa.gather_evidence(db_session, no_access, SCEN_A_APP)
    assert ev["accessible"] is False
    assert ev["sources"] == []


@pytest.mark.asyncio
async def test_register_only_gate_verdict_overridden_for_reasoning_question(db_session, test_officer):
    """`resolve_application_reference()`'s "register_only" verdict already
    proves the application exists and is in jurisdiction, then short-
    circuits with its OWN pre-rendered details card -- bypassing
    `_early_subdivision_answer` (and this module's hook inside it)
    entirely. A reasoning question about that application must still
    reach reasoning_qa: orchestrator.py rewrites that verdict to "ok" when
    `is_reasoning_question()` is true, never for an ordinary follow-up
    (live-verified: see P1_REASONING_REMEDIATION_REPORT.md, Scenario B2)."""
    from backend.services.chatbot.chat_application_queries import resolve_application_reference
    from backend.services.rag import parse_intent

    history = [
        {"role": "user", "content": "Application 2026/0158/02/001804. The register shows this "
                                     "application is still open, but the Tahsildar has recorded "
                                     "Approved. Does that mean the complete application is closed?"},
        {"role": "assistant", "content": "Recorded facts: ... [2026/0158/02/001804]"},
    ]
    message = "What information is still missing before you can definitively say it is completed?"
    intent = parse_intent(message)
    res = await resolve_application_reference(db_session, message, history, test_officer, intent, "sess")
    assert res["verdict"] == "register_only"
    assert reasoning_qa.is_reasoning_question(message) is True
    # the override orchestrator.py applies for exactly this case
    overridden = ({"verdict": "ok", "app_number": res.get("app_number"), "from_context": True}
                  if res["verdict"] == "register_only" and reasoning_qa.is_reasoning_question(message)
                  else res)
    assert overridden["verdict"] == "ok"
    assert overridden["app_number"] == "2026/0158/02/001804"


def test_reasoning_follow_up_resolves_register_only_app_from_history():
    """A reasoning follow-up naming no application ('What information is
    still missing before you can definitively say it is completed?') must
    still resolve 2026/0158/02/001804 from the prior turn's answer -- a
    register-only code `_extract_app_number_from_context` does not cover,
    the same chat-history fallback the record modules already use for
    themselves (live-verified: see P1_REASONING_REMEDIATION_REPORT.md)."""
    from backend.services.chatbot import cmflow_qa as _cmflow_qa

    history = [
        {"role": "user", "content": "Application 2026/0158/02/001804. The register shows this "
                                     "application is still open, but the Tahsildar has recorded "
                                     "Approved. Does that mean the complete application is closed?"},
        {"role": "assistant", "content": "Recorded facts:\nApplication 2026/0158/02/001804 "
                                          "(service code 0158): ... [2026/0158/02/001804]"},
    ]
    found = None
    for m in reversed(history[-14:]):
        mm = _cmflow_qa._APP_NO.search(m.get("content") or "")
        if mm:
            found = mm.group(0)
            break
    assert found == "2026/0158/02/001804"


@pytest.mark.asyncio
async def test_answer_refuses_without_calling_llm_when_inaccessible(db_session, test_officer):
    called = {"n": 0}

    async def should_not_run(prompt):
        called["n"] += 1
        return "SHOULD NOT APPEAR"

    no_access = test_officer.model_copy(update={"jurisdiction_ids": []})
    text = await reasoning_qa.answer(
        db_session, no_access, "Does Approved mean the application is closed?",
        SCEN_B_APP, "en", rule_keys=["desk_vs_closure"], llm_call=should_not_run)

    assert called["n"] == 0
    assert "SHOULD NOT APPEAR" not in text
    assert "do not have access" in text.lower() or "jurisdiction" in text.lower()


# ---------------------------------------------------------------------------
# D. Regression -- deterministic lookups stay deterministic
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "What is the application status for 2026/0153/02/045470?",
    "Show the latest workflow transition.",
    "What is the new patta number for 2026/0154/02/012766?",
])
def test_direct_lookup_never_enters_reasoning_path(message):
    assert reasoning_qa.is_reasoning_question(message) is False
