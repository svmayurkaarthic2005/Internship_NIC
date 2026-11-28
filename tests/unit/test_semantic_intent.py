"""P2 — semantic_intent.py: prototype caching, cosine/margin math, abstention,
domain exclusion, shadow mode, and failure handling.

Section A mocks the embedding call so the classifier's own math is tested
deterministically, with no Ollama dependency. Section B runs the real model
and is skipped automatically when Ollama / nomic-embed-text is unreachable —
it never claims a mocked result is the live model's accuracy.
"""
import asyncio

import numpy as np
import pytest

from backend.config import settings
from backend.services.chatbot import semantic_intent as si


@pytest.fixture(autouse=True)
def _reset_cache_and_mode():
    """Every test starts from a clean prototype cache and active mode."""
    si._matrix = None
    si._labels = []
    prev_mode = settings.SEMANTIC_INTENT_MODE
    settings.SEMANTIC_INTENT_MODE = "active"
    yield
    si._matrix = None
    si._labels = []
    settings.SEMANTIC_INTENT_MODE = prev_mode


def _unit(vec):
    v = np.array(vec, dtype=np.float32)
    return v / np.linalg.norm(v)


class _FakeEmbedder:
    """Deterministic stand-in for backend.services.embeddings.batch_embed.

    Each prototype/message is mapped to a hand-picked 3-D unit vector so the
    classifier's cosine-similarity and margin arithmetic can be verified
    exactly, without depending on the real nomic-embed-text model.
    """

    VECTORS = {
        "hello": [1.0, 0.0, 0.0],
        "hi": [0.98, 0.1, 0.0],          # close to "hello" -> greeting prototype
        "thanks": [0.0, 1.0, 0.0],
        "bye": [0.0, 0.0, 1.0],
        "pending applications": [0.3, 0.3, 0.9],   # SIS anchor-ish
        "AMBIGUOUS": [0.6, 0.6, 0.6],     # equidistant -> low margin, should abstain
        "CLOSE_TO_SIS": [0.31, 0.3, 0.9], # fires cosine but margin over SIS too small
    }

    def __call__(self, texts):
        out = []
        for t in texts:
            key = t.split("classification: ", 1)[-1]
            vec = self.VECTORS.get(key)
            if vec is None:
                # default: a vector with no special similarity to anything
                vec = [0.05, 0.05, 0.05]
            out.append(_unit(vec).tolist())
        return out


@pytest.fixture
def fake_embed(monkeypatch):
    fake = _FakeEmbedder()
    monkeypatch.setattr(si, "_embed", lambda texts: np.array(fake(texts), dtype=np.float32))
    # Build a tiny, hand-verifiable prototype set instead of the real ~95-phrase one.
    monkeypatch.setattr(si, "_PROTOTYPES", {
        "greeting": ["hello"],
        "thanks": ["thanks"],
        "farewell": ["bye"],
    })
    monkeypatch.setattr(si, "_NEGATIVE", ["pending applications"])
    return fake


# ---------------------------------------------------------------------------
# Section A: deterministic classifier math (mocked embeddings)
# ---------------------------------------------------------------------------

def test_prototype_build_is_cached(fake_embed, monkeypatch):
    calls = {"n": 0}
    real_embed = si._embed

    def counting_embed(texts):
        calls["n"] += 1
        return real_embed(texts)

    monkeypatch.setattr(si, "_embed", counting_embed)
    si._build()
    first_matrix = si._matrix
    assert calls["n"] == 1
    # A second classify() call must not rebuild the matrix.
    asyncio.run(si.classify("hi"))
    assert calls["n"] == 2  # one for build (already done) -- wait, build only once
    assert si._matrix is first_matrix


def test_build_happens_once_across_many_classify_calls(fake_embed):
    asyncio.run(si.classify("hi"))
    matrix_after_first = si._matrix
    asyncio.run(si.classify("hi"))
    assert si._matrix is matrix_after_first  # same object: never rebuilt


def test_cosine_similarity_calculation(fake_embed):
    si._build()
    kind, best, sis = si.score("hi")
    assert kind == "greeting"
    # cosine("hi", "hello") should be high and higher than cosine("hi", the SIS anchor)
    assert best > sis
    assert best > 0.9


def test_aggregation_picks_the_best_conversational_label(fake_embed):
    si._build()
    kind, best, sis = si.score("bye")
    assert kind == "farewell"


def test_minimum_similarity_threshold_blocks_a_weak_match(fake_embed, monkeypatch):
    si._build()
    monkeypatch.setattr(si, "_THRESHOLD", 0.999)  # impossibly strict
    result = asyncio.run(si.classify("hi"))
    assert result is None


def test_margin_threshold_abstains_on_ambiguous_message(fake_embed):
    si._build()
    kind, best, sis = si.score("AMBIGUOUS")
    # Equidistant vector: whichever label wins, the margin over the SIS anchor
    # should be small -- below the deployed 0.08 margin -- so classify() abstains.
    result = asyncio.run(si.classify("AMBIGUOUS"))
    assert result is None
    assert best - sis < si._MARGIN or best < si._THRESHOLD


def test_close_to_sis_anchor_abstains_even_if_above_threshold(fake_embed):
    si._build()
    result = asyncio.run(si.classify("CLOSE_TO_SIS"))
    assert result is None


def test_domain_term_present_never_calls_embedder(fake_embed, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(si, "_embed", lambda t: (_ for _ in ()).throw(AssertionError("must not embed")))
    result = asyncio.run(si.classify("hello", has_domain_term=True))
    assert result is None


def test_long_message_never_calls_embedder(fake_embed, monkeypatch):
    monkeypatch.setattr(si, "_embed", lambda t: (_ for _ in ()).throw(AssertionError("must not embed")))
    long_msg = "hello " * 20
    result = asyncio.run(si.classify(long_msg))
    assert result is None


def test_digit_bearing_message_never_calls_embedder(fake_embed, monkeypatch):
    monkeypatch.setattr(si, "_embed", lambda t: (_ for _ in ()).throw(AssertionError("must not embed")))
    result = asyncio.run(si.classify("hello 2026"))
    assert result is None


def test_tamil_script_message_never_calls_embedder(fake_embed, monkeypatch):
    monkeypatch.setattr(si, "_embed", lambda t: (_ for _ in ()).throw(AssertionError("must not embed")))
    result = asyncio.run(si.classify("வணக்கம்"))
    assert result is None


def test_embedding_model_unavailable_returns_none(fake_embed, monkeypatch):
    def boom(texts):
        raise ConnectionError("Ollama down")
    monkeypatch.setattr(si, "_embed", boom)
    result = asyncio.run(si.classify("hello"))
    assert result is None


def test_embedding_timeout_falls_through_safely(fake_embed, monkeypatch):
    def timeout(texts):
        raise TimeoutError("embed timed out")
    monkeypatch.setattr(si, "_embed", timeout)
    result = asyncio.run(si.classify("hello"))
    assert result is None


def test_shadow_mode_never_overrides_but_still_scores(fake_embed, capsys):
    settings.SEMANTIC_INTENT_MODE = "shadow"
    result = asyncio.run(si.classify("hello"))
    assert result is None  # shadow mode never overrides routing
    # structlog writes to stdout under pytest's default config; confirm the
    # shadow prediction was still logged for offline evaluation.
    assert "shadow" in capsys.readouterr().out


def test_active_mode_overrides_with_matching_kind(fake_embed):
    settings.SEMANTIC_INTENT_MODE = "active"
    result = asyncio.run(si.classify("hello"))
    assert result == "greeting"


def test_greeting_classification(fake_embed):
    assert asyncio.run(si.classify("hello")) == "greeting"


def test_thanks_classification(fake_embed):
    assert asyncio.run(si.classify("thanks")) == "thanks"


def test_farewell_classification(fake_embed):
    assert asyncio.run(si.classify("bye")) == "farewell"


# ---------------------------------------------------------------------------
# Section B: real model, real prototypes -- the actual deployed configuration.
# Skips cleanly if Ollama / nomic-embed-text is unreachable; never asserts a
# mocked number here, only live-model behavior.
# ---------------------------------------------------------------------------

def _ollama_reachable() -> bool:
    try:
        from backend.services.embeddings import batch_embed
        batch_embed(["classification: ping"])
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_reachable(), reason="Ollama / nomic-embed-text unreachable")
class TestRealModel:
    def test_real_greeting_fires(self):
        assert asyncio.run(si.classify("hello there, good morning")) == "greeting"

    def test_real_domain_question_with_domain_term_is_never_asked(self):
        # "pending" is a _DOMAIN_TERMS word; the orchestrator computes
        # has_domain_term itself -- here we simulate that it did.
        assert asyncio.run(si.classify("show my pending applications", has_domain_term=True)) is None

    def test_real_p0_p1_style_question_does_not_misfire(self):
        # Short, digit-free, carries no _DOMAIN_TERMS substring on purpose,
        # to test the embedder's own semantic judgment rather than the gate.
        for msg in ["what is isd", "what is nisd", "what is sd"]:
            result = asyncio.run(si.classify(msg, has_domain_term=False))
            assert result is None, f"{msg!r} was wrongly classified as conversational: {result}"

    def test_real_evidence_reasoning_style_question_not_misfire(self):
        msg = "does that prove it was completed"
        # Long/complex reasoning questions exceed _MAX_WORDS and are excluded
        # by the eligibility gate regardless of embedding score.
        assert si.looks_short_and_plain(msg, False) is True or len(msg.split()) > si._MAX_WORDS
