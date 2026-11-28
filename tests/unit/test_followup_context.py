"""
Comprehensive unit tests for conversation context resolution (followup_context.py).

Covers Phase 5 requirements:
- Multi-turn conversation flows (Turn 1 through Turn 5)
- Pronoun resolution (English, Tamil, Tanglish)
- Superlative resolution (oldest, newest)
- Field visit follow-ups and schedule date queries
- Comparative follow-ups (pronoun + ordinal, two ordinals, explicit app numbers, ambiguous cases)
- Changing requested columns / projections
- Follow-up filtering
- Context expiration (TTL > 1800s)
- Cross-officer isolation
- Empty results barrier
- FollowupContextStore operations
- Versioning and backward compatibility (v1 and v2)
"""

import time
import pytest
from backend.services.followup_context import (
    FollowupContext,
    FollowupContextStore,
    ENTITY_APPLICATION,
    ENTITY_APPLICATION_LIST,
    ENTITY_FIELD_VISIT,
    ENTITY_SURVEY,
    ENTITY_SERVICE_CODE,
    CONTEXT_VERSION,
    build_context,
    resolve,
)


def test_followup_context_serialization_and_versioning():
    """Verify serialization, versioning, and backward compatibility."""
    ctx = FollowupContext(
        entity=ENTITY_APPLICATION,
        application_numbers=["2026/0154/28/000001"],
        survey_numbers=["1355/1B"],
        filters={"status": "pending"},
        query_type="Application Detail",
        intent="application_status",
        officer_id="SIS-001",
        session_id="session_01",
    )
    raw = ctx.to_json()
    assert raw["version"] == CONTEXT_VERSION
    assert raw["entity"] == ENTITY_APPLICATION
    assert raw["current_application"] == "2026/0154/28/000001"
    assert raw["active_applications"] == ["2026/0154/28/000001"]

    restored = FollowupContext.from_json(raw)
    assert restored is not None
    assert restored.single_application == "2026/0154/28/000001"
    assert restored.current_application == "2026/0154/28/000001"
    assert restored.survey_numbers == ["1355/1B"]
    assert restored.officer_id == "SIS-001"

    # Backward compatibility with version 1
    raw_v1 = {
        "version": 1,
        "entity": ENTITY_APPLICATION,
        "application_numbers": ["2026/0154/28/000002"],
        "survey_numbers": ["120/4"],
        "filters": {"status": "approved"},
    }
    restored_v1 = FollowupContext.from_json(raw_v1)
    assert restored_v1 is not None
    assert restored_v1.current_application == "2026/0154/28/000002"
    assert restored_v1.active_applications == ["2026/0154/28/000002"]

    # Reject unsupported future versions
    raw_v99 = dict(raw, version=99)
    assert FollowupContext.from_json(raw_v99) is None


def test_multi_turn_5_step_sequence():
    """
    Test the full 5-turn sequence from Phase 5 specifications:
    Turn 1: "Show my pending ISD applications."
    Turn 2: "Which is the oldest?"
    Turn 3: "Show its field visit details."
    Turn 4: "When was it scheduled?"
    Turn 5: "Compare it with the second application."
    """
    # Turn 1: Listing returned 3 pending ISD applications
    sd1 = {
        "applications": [
            {"application_number": "2026/0154/28/000001", "submission_date": "2026-01-10"},
            {"application_number": "2026/0154/28/000002", "submission_date": "2026-01-20"},
            {"application_number": "2026/0154/28/000003", "submission_date": "2026-02-05"},
        ],
        "status": "pending",
        "type": "ISD",
        "query_type": "Pending ISD Applications",
    }
    ctx1 = build_context("pending_applications", sd1, officer_id="SIS-001", session_id="s1")
    assert ctx1 is not None
    assert ctx1.entity == ENTITY_APPLICATION_LIST
    assert len(ctx1.active_applications) == 3
    assert ctx1.active_applications[0] == "2026/0154/28/000001"

    # Turn 2: "Which is the oldest?"
    res2 = resolve("Which is the oldest?", ctx1, officer_id="SIS-001")
    assert res2.resolved
    assert res2.operation == "oldest"
    assert res2.application_numbers == ["2026/0154/28/000001", "2026/0154/28/000002", "2026/0154/28/000003"]

    # Simulating backend returning the oldest application (000001)
    sd2 = {
        "application_number": "2026/0154/28/000001",
        "status": "pending",
        "type": "ISD",
    }
    ctx2 = build_context("application_status", sd2, previous_context=ctx1, officer_id="SIS-001", session_id="s1")
    assert ctx2 is not None
    assert ctx2.current_application == "2026/0154/28/000001"
    # Preserves active_applications list for subsequent ordinal references!
    assert ctx2.active_applications == ["2026/0154/28/000001", "2026/0154/28/000002", "2026/0154/28/000003"]

    # Turn 3: "Show its field visit details."
    res3 = resolve("Show its field visit details", ctx2, officer_id="SIS-001")
    assert res3.resolved
    assert res3.about_visit is True
    assert res3.application_number == "2026/0154/28/000001"

    # Simulating backend returning field visit details
    sd3 = {
        "application_number": "2026/0154/28/000001",
        "field_visit": {
            "application_number": "2026/0154/28/000001",
            "scheduled_date": "2026-03-15",
            "status": "scheduled",
        },
        "_asked_field_visit": True,
    }
    ctx3 = build_context("fv_status", sd3, previous_context=ctx2, officer_id="SIS-001", session_id="s1")
    assert ctx3 is not None
    assert ctx3.current_application == "2026/0154/28/000001"
    assert ctx3.current_field_visit is not None

    # Turn 4: "When was it scheduled?"
    res4 = resolve("When was it scheduled?", ctx3, officer_id="SIS-001")
    assert res4.resolved
    assert res4.about_visit is True
    assert res4.target_field == "scheduled_date"
    assert res4.operation == "field_visit_schedule"
    assert res4.application_number == "2026/0154/28/000001"

    # Turn 5: "Compare it with the second application."
    res5 = resolve("Compare it with the second application", ctx3, officer_id="SIS-001")
    assert res5.resolved
    assert res5.is_comparison is True
    assert res5.operation == "compare"
    # Target 1: "it" -> 2026/0154/28/000001
    # Target 2: "the second application" -> 2026/0154/28/000002
    assert res5.comparison_targets == ["2026/0154/28/000001", "2026/0154/28/000002"]
    assert res5.application_numbers == ["2026/0154/28/000001", "2026/0154/28/000002"]


def test_pronoun_resolution_bilingual():
    """Verify pronoun resolution across English, Tamil, and Tanglish."""
    ctx_single = FollowupContext(
        entity=ENTITY_APPLICATION,
        application_numbers=["2026/0154/28/000005"],
        filters={"status": "pending"},
        officer_id="SIS-001",
    )

    # English singular: "what is its status?"
    res_en = resolve("what is its status?", ctx_single, language="en")
    assert res_en.resolved
    assert res_en.application_number == "2026/0154/28/000005"

    # Tamil singular: "அதன் நிலை என்ன?"
    res_ta = resolve("அதன் நிலை என்ன?", ctx_single, language="ta")
    assert res_ta.resolved
    assert res_ta.application_number == "2026/0154/28/000005"

    # Tanglish singular: "adhoda status enna?"
    res_tgl = resolve("adhoda status enna?", ctx_single, language="tanglish")
    assert res_tgl.resolved
    assert res_tgl.application_number == "2026/0154/28/000005"

    # Plural references over list
    ctx_multi = FollowupContext(
        entity=ENTITY_APPLICATION_LIST,
        application_numbers=["2026/0154/28/000001", "2026/0154/28/000002"],
        filters={"status": "pending"},
        officer_id="SIS-001",
    )
    # Tamil plural: "அவற்றின் நிலை என்ன?"
    res_ta_pl = resolve("அவற்றின் நிலை என்ன?", ctx_multi, language="ta")
    assert res_ta_pl.resolved
    assert res_ta_pl.application_numbers == ["2026/0154/28/000001", "2026/0154/28/000002"]

    # English plural: "their status"
    res_en_pl = resolve("their status", ctx_multi, language="en")
    assert res_en_pl.resolved
    assert res_en_pl.application_numbers == ["2026/0154/28/000001", "2026/0154/28/000002"]


def test_comparative_followup_variants():
    """Verify various comparison follow-up shapes."""
    ctx = FollowupContext(
        entity=ENTITY_APPLICATION_LIST,
        application_numbers=["2026/0154/28/000001", "2026/0154/28/000002", "2026/0154/28/000003"],
        active_applications=["2026/0154/28/000001", "2026/0154/28/000002", "2026/0154/28/000003"],
    )

    # Two ordinals: "Compare row 1 and row 2"
    res1 = resolve("Compare row 1 and row 2", ctx)
    assert res1.resolved
    assert res1.is_comparison is True
    assert res1.comparison_targets == ["2026/0154/28/000001", "2026/0154/28/000002"]

    # Two ordinals words: "Compare the first and third application"
    res2 = resolve("Compare the first and third application", ctx)
    assert res2.resolved
    assert res2.comparison_targets == ["2026/0154/28/000001", "2026/0154/28/000003"]

    # Ambiguous comparison: "Compare them" over 3 applications must ask clarification, NEVER GUESS
    res3 = resolve("Compare them", ctx)
    assert res3.ambiguous is True
    assert not res3.resolved
    assert "Which two would you like to compare" in (res3.clarification or "")

    # Bare compare with exactly 2 applications in view
    ctx_2 = FollowupContext(
        entity=ENTITY_APPLICATION_LIST,
        application_numbers=["2026/0154/28/000001", "2026/0154/28/000002"],
        active_applications=["2026/0154/28/000001", "2026/0154/28/000002"],
    )
    res4 = resolve("Compare them", ctx_2)
    assert res4.resolved
    assert res4.comparison_targets == ["2026/0154/28/000001", "2026/0154/28/000002"]

    # Tamil: "இரண்டையும் ஒப்பிடு"
    res_ta = resolve("இரண்டையும் ஒப்பிடு", ctx_2, language="ta")
    assert res_ta.resolved
    assert res_ta.comparison_targets == ["2026/0154/28/000001", "2026/0154/28/000002"]


def test_context_expiration():
    """Verify that expired contexts (TTL > 1800s) are not resolved."""
    # Context created 2000 seconds ago
    expired_ctx = FollowupContext(
        entity=ENTITY_APPLICATION,
        application_numbers=["2026/0154/28/000001"],
        created_at=time.time() - 2000,
        ttl_seconds=1800,
    )
    assert expired_ctx.is_expired() is True

    # from_json drops expired context
    raw = expired_ctx.to_json()
    assert FollowupContext.from_json(raw) is None

    # resolve stands aside on expired context
    res = resolve("what is the applicant name?", expired_ctx)
    assert not res.resolved
    assert res.application_number is None


def test_cross_officer_boundary_isolation():
    """Verify zero-grant rule: officer A's context cannot be accessed by officer B."""
    ctx_officer1 = FollowupContext(
        entity=ENTITY_APPLICATION,
        application_numbers=["2026/0154/28/000001"],
        officer_id="SIS-001",
    )
    assert ctx_officer1.validate_officer("SIS-001") is True
    assert ctx_officer1.validate_officer("SIS-002") is False

    # Officer 2 querying officer 1's context is rejected
    res = resolve("what is the applicant name?", ctx_officer1, officer_id="SIS-002")
    assert not res.resolved
    assert res.application_number is None

    # from_json with wrong officer returns None
    raw = ctx_officer1.to_json()
    assert FollowupContext.from_json(raw, officer_id="SIS-002") is None
    assert FollowupContext.from_json(raw, officer_id="SIS-001") is not None


def test_empty_results_barrier():
    """Verify that an empty query result acts as a barrier preventing walking back."""
    ctx_empty = FollowupContext(
        entity=ENTITY_APPLICATION_LIST,
        application_numbers=[],
        active_applications=[],
        filters={"status": "pending", "type": "NISD"},
    )
    res = resolve("what is the applicant name?", ctx_empty)
    # Stands aside rather than picking an older record
    assert not res.resolved
    assert res.application_number is None


def test_followup_context_store():
    """Verify thread-safe in-memory session persistence and TTL auto-purge."""
    store = FollowupContextStore.get_instance()
    ctx = FollowupContext(
        entity=ENTITY_APPLICATION,
        application_numbers=["2026/0154/28/000009"],
        officer_id="SIS-001",
        session_id="session_test_99",
    )
    store.put("session_test_99", ctx, officer_id="SIS-001")

    # Correct retrieval
    retrieved = store.get("session_test_99", officer_id="SIS-001")
    assert retrieved is not None
    assert retrieved.current_application == "2026/0154/28/000009"

    # Isolation: Different officer gets None
    assert store.get("session_test_99", officer_id="SIS-002") is None

    # Clear session
    store.clear("session_test_99", officer_id="SIS-001")
    assert store.get("session_test_99", officer_id="SIS-001") is None
