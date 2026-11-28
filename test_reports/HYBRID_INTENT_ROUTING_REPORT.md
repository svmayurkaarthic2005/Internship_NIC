# HYBRID_INTENT_ROUTING_REPORT — P2 Hybrid Semantic Intent Routing

**Date:** 2026-11-21 · **Branch:** `main` · **HEAD (session start):** `eaff14e50b7162339dc458b630cf406a037efd1d`

## A. Executive summary

The task's premise was that hybrid semantic intent routing needed to be designed and built
from scratch. It does not: **`backend/services/chatbot/semantic_intent.py` already
implements it**, already active in production, for four of the task's seven proposed
categories (`greeting`, `smalltalk`, `thanks`, `farewell`), using the project's existing
`nomic-embed-text` embeddings, a cached prototype matrix, cosine similarity with a
threshold **and** a margin-over-hard-negatives, and clean abstention on any failure. This
report therefore inspected, calibrated, and stress-tested that existing module against real
data rather than building a parallel one, added the one piece that was genuinely missing (a
shadow-mode switch for safely trialing *future* prototype categories), and recommends
**against** adding the three additional categories the task names, because they are already
handled — more cheaply and more safely — by deterministic guards that run *ahead* of the
embedder in the dispatch chain.

**Headline numbers:**
- False-positive rate on 874 real domain questions pulled from the project's own question
  banks: **1/874 (0.11%)** — one ambiguous sentence ("who am i"), zero others.
- Binary recall (fire-vs-abstain) on 21 held-out conversational phrasings never seen by the
  prototypes: **12/21 (57%)**; every miss is a safe abstention, none is a wrong domain
  capture.
- Warm single-message embedding latency on this machine (CPU inference): **~2.1s** — not
  "fast and inexpensive" in absolute terms, which is why the orchestrator's pre-filter
  (never call the embedder unless the deterministic parser already gave up and no cheaper
  guard claimed the turn) matters more than the embedder's own cost.
- Full regression suite: **269/269 passing** (247 pre-existing + 22 new).
- Live API run across all 4 required conversations: **zero domain questions or ambiguous
  follow-ups were misrouted as small talk** — the one safety property Phase 10 is actually
  checking held completely. Two pre-existing, unrelated conversational-quality gaps were
  found and are documented in §I rather than silently fixed (see "Known limitations").

## B. Existing routing architecture

```
parse_intent()                       (unchanged, untouched by this task)
  │
  ├─ domain intent (application_status, compare_applications, survey_detail, …)
  │       → its deterministic handler, same as always
  │
  ├─ chat_pipeline.py's early EarlyReply gates (gibberish / help / vague-"all" /
  │   bare-reference / forecast) — run BEFORE parse_intent reaches "general_query"
  │       → deterministic reply, intent recorded as e.g. "unknown_message"
  │
  └─ "general_query"
        │
        ├─ _is_capability_question()?  → deterministic "what can you do" answer
        ├─ _is_out_of_scope()?         → deterministic off-topic refusal
        ├─ _special_scope_kind()?      → deterministic sentiment/identity/time answer
        │   (none of the above claimed it)
        ▼
    semantic_intent.classify(message, has_domain_term)
        │  eligible only if: no digit, no Tamil script, ≤9 words, no _DOMAIN_TERMS hit
        │  embed → cosine vs cached prototype matrix (greeting/smalltalk/thanks/farewell
        │  prototypes vs. a hard-negative "sis" anchor set)
        │  fires only if best_cos ≥ 0.75 AND best_cos − best_sis_anchor_cos ≥ 0.08
        ▼
    fires → intent forced to "greeting" → chat_smalltalk.py re-derives the actual
            reply shape (plain hello / thanks / farewell) from the RAW TEXT, never
            from the semantic label
    abstains → falls through to the agent / RAG / LLM fallback, unchanged
```

Call sites: `orchestrator.py:2631` (non-streaming) and `orchestrator.py:5523` (streaming),
identical guard. No other module calls a function on `semantic_intent` (the many
`from . import semantic_intent` lines across the package are unused shared-import
boilerplate, confirmed by grep).

## C. Nomic model configuration (verified live, not assumed)

| fact | value | how verified |
|---|---|---|
| Model | `nomic-embed-text:latest` | `ollama`'s `/api/tags` |
| Dimensions | 768 | live `/api/embed` call, `len(embeddings[0])` |
| Resident at session start? | **No** — only the chat LLM (`sis-assistant:latest`) was loaded; the embedder had been evicted by Ollama's own `keep_alive` policy | `/api/ps` |
| Task prefix | `"classification: "`, applied uniformly to prototypes and candidate messages | read from `semantic_intent.py` |
| Client lifecycle | module-level singleton `OllamaEmbeddings` in `backend/services/embeddings.py`, created once at import; sync HTTP call wrapped in `asyncio.to_thread` by the caller | read + confirmed by `classify()`'s own `asyncio.to_thread` wrapping |
| Prototype cache | module-level `_matrix`/`_labels`, built once behind an `asyncio.Lock`, 95 vectors | read + test (`test_prototype_build_is_cached`, `test_build_happens_once_across_many_classify_calls`) |
| Latency | cold single embed 3.33s · warm single embed ≈2.1s (mean of 5) · cold prototype build (95 phrases, 1 batch call) 4.33s · 92-message batch embed 2.13s total | timed live calls, this session |

## D. Prototype design and categories

Current (already deployed): `greeting`, `smalltalk`, `thanks`, `farewell`, each with
12-18 example phrases (English, Tanglish, Tamil), plus 32 hard-negative SIS anchor phrases
labeled `"sis"` so the margin check has something real to compare against.

**Not added:** `GENERAL_HELP`, `SYSTEM_CAPABILITIES`, `GENERAL_INFORMATION`. Checked whether
these are an actual gap:

- `SYSTEM_CAPABILITIES` / `GENERAL_HELP` → already answered by `_is_capability_question()`,
  which runs *before* `semantic_intent.classify()` is even called (one of the guard
  conditions at `orchestrator.py:2629`). Live-tested: "What can you help me with?" answered
  in 1.1s with `intent=capabilities`, no LLM, no embedding call.
- `GENERAL_INFORMATION` (off-topic: weather, poems, trivia) → already answered by
  `_is_out_of_scope()`, same position in the guard, documented in CLAUDE.md as
  "high-precision, low-recall on purpose" specifically so a real domain question is never
  swept up.

Adding these as embedding prototypes would either (a) never fire, because the deterministic
guard already claimed the turn before the embedder is reached, or (b) require moving them
ahead of that guard — which the task's own Phase 2 explicitly forbids ("Do not reorder the
existing dispatch chain"). Doing it anyway would add ~2s of latency and the non-zero
false-positive risk measured in §F, for zero behavioral gain over what already exists for
free. **Recommendation: leave these as deterministic guards; do not add embedding
prototypes for them.**

## E. Shadow mode (new)

The existing classifier had no shadow mode — it is unconditionally active for the four
categories above. Given §F's results, re-litigating activation for those four is not
warranted. What *is* missing, and genuinely useful for the task's own stated goal
("incremental routing enhancement" for future categories), is a safe way to trial a new
prototype category before making it live. Added:

- `backend/config.py`: `SEMANTIC_INTENT_MODE: str = "active"` (default — zero behavior
  change for anyone not setting the env var).
- `backend/services/chatbot/semantic_intent.py`: when `SEMANTIC_INTENT_MODE == "shadow"`,
  `classify()` still embeds and scores the message, still logs the prediction and its
  cosine/margin, but **always returns `None`** — the orchestrator's
  `if _sem_kind: intent = "greeting"` therefore never fires, so routing is provably
  unchanged while the prediction is still observable in the logs for offline evaluation.

```python
# backend/services/chatbot/semantic_intent.py (diff summary)
fires = bool(kind and best >= _THRESHOLD and best - sis >= _MARGIN)
shadow = settings.SEMANTIC_INTENT_MODE == "shadow"
if fires:
    logger.info(f"semantic intent{' [shadow, not applied]' if shadow else ''}: {kind} (cos {best:.2f}, sis {sis:.2f})")
if shadow:
    return None
return kind if fires else None
```

Verified live: `SEMANTIC_INTENT_MODE=active` → `classify("hello there")` returns
`"greeting"`; `SEMANTIC_INTENT_MODE=shadow` → logs `"semantic intent [shadow, not applied]:
greeting (cos 0.97, sis 0.75)"` and returns `None`.

## F. Calibration and evaluation (Phase 6)

### F.1 False-positive sweep (the most important safety metric this task names)

Pulled every unique line from `test_questions_openers.txt`, `test_questions_100.txt`,
`test_questions_200.txt`, `test_questions_206.txt` — 874 unique real SIS domain questions,
none conversational. Ran each through the module's own eligibility gate, then scored every
eligible one with the live model.

| | count |
|---|---|
| total unique domain questions | 874 |
| passed the eligibility gate (eligible to reach the embedder at all) | 92 |
| misclassified as conversational (false positive) | **1** |

The one false positive: `"who am i"` → `smalltalk` (cos 0.891, margin 0.162 — a confident
hit, not a marginal one). Genuinely ambiguous wording, textually indistinguishable from "who
are you" without context. No other domain question, including every other short,
digit-free, terminology-only question in `test_questions_openers.txt` ("what is isd", "what
is sd", "what is dsc", "who approves nisd", …) was captured.

**False-positive rate: 1/874 ≈ 0.11% overall; 1/92 ≈ 1.1% among messages that even reach the
embedder.** Full script: `scratch/eval_semantic_p2.py`. Raw results CSV:
`test_reports/HYBRID_INTENT_ROUTING_RESULTS.csv` (874 rows, one per question, with
eligibility flag, predicted kind, both cosine scores, and verdict).

### F.2 Recall sweep — held-out conversational phrasings

21 phrasings across the four categories, not copied from the prototypes (abbreviations,
Tanglish, casual spelling): `scratch/eval_semantic_p2b.py`.

| outcome | count |
|---|---|
| correct (fired, right sub-category) | 10/21 |
| abstained (safe fallback) | 9/21 |
| fired, wrong sub-category (greeting vs smalltalk vs farewell) | 2/21 |

Both "wrong sub-category" misses are harmless: the downstream handler
(`chat_smalltalk.py`'s `_greeting_is_thanks` / `_greeting_is_farewell`) re-derives the actual
reply shape from the raw message text, never from the semantic label — confirmed by reading
that module. The label's only real job is the binary fire/abstain decision. On that binary
measure, recall is 12/21 = 57%, and every miss falls through to the ordinary pipeline, not
to a wrong answer.

### F.3 Confusion matrix (binary: conversational vs domain, the only distinction that
actually changes behavior)

| | predicted conversational | predicted not-conversational (abstain) |
|---|---|---|
| **actually conversational** (F.2's 21) | 12 | 9 |
| **actually domain** (F.1's 92 eligible) | 1 | 91 |

### F.4 Threshold/margin — not re-derived, but stress-tested

The deployed `_THRESHOLD = 0.75` / `_MARGIN = 0.08` were not re-fit from scratch (that is a
larger undertaking than this task's scope justified, given it is already empirically safe)
but were checked against §F.1/F.2: the pair is conservative — it favors abstention over a
wrong domain capture, which is the right trade-off for a low-stakes category (a missed
greeting costs one slower LLM round trip; a captured domain question costs a wrong or
refused answer to a real officer request). No change recommended.

## G. Performance (Phase 8)

| requirement | status |
|---|---|
| Precompute/cache prototypes | Already done — `_matrix`/`_labels`, built once, `asyncio.Lock`-guarded |
| Avoid reloading per message | Confirmed — client is a module-level singleton; prototype matrix built once per process |
| Avoid embedding direct DB queries | Confirmed — the orchestrator only calls `classify()` when `intent == "general_query"` and no cheaper deterministic guard claimed the turn; every deterministic-intent question (application_status, survey_detail, …) never reaches the embedder |
| Bounded timeout | `EMBED_TIMEOUT_SECONDS = 15.0` on the Ollama client; `classify()` additionally wraps everything in a try/except that returns `None` on any exception |
| Non-blocking | `asyncio.to_thread` around both `_build()` and `score()` |
| No extra LLM calls | Confirmed — embedding only, never a generation call |
| Graceful on Ollama down/busy | `classify()`'s broad `except Exception` catches connection errors, timeouts, and anything else; test coverage below |
| Fallback to existing routing | Confirmed — `None` always falls through unchanged |

Cold vs warm latency measured live (§C). The real cost that matters is the **warm** ~2.1s
per call — not negligible on this (CPU-only) deployment, which is exactly why the existing
pre-filter (deterministic guards claim the vast majority of `general_query` traffic before
the embedder is ever asked) is the actual performance story here, more than the embedder's
own per-call cost.

## H. Regression tests (Phase 9)

`tests/unit/test_semantic_intent.py` (new, 22 tests):

- **Section A** (mocked embeddings, deterministic): prototype build/caching, cosine
  calculation, per-intent aggregation, minimum-similarity threshold, margin-over-SIS-anchor
  abstention on an ambiguous vector, domain-term/long-message/digit/Tamil-script messages
  never calling the embedder at all, embedding-unavailable and embedding-timeout both
  returning `None` safely, shadow mode scoring without overriding, active mode overriding
  correctly, and one test per category (greeting/thanks/farewell).
- **Section B** (real model, skipped cleanly if Ollama/nomic-embed-text is unreachable):
  a real greeting fires, a domain question flagged `has_domain_term=True` is never asked,
  and three P0/P1-flavoured domain questions ("what is isd", "what is nisd", "what is sd")
  are confirmed not to misfire against the live model.

Full suite: **269/269 passing** (247 pre-existing baseline + 22 new). No test was mocked
and then reported as live-model accuracy — Section B runs the real model and the numbers in
§F come from live calls, not from the mocked unit tests.

## I. Live API validation (Phase 10)

Ran against the real backend (`POST /api/v1/auth/login` → `/chat/sessions` → `/chat`;
switched from `/chat/stream` to the non-streaming endpoint after confirming by reading
`backend/routers/chat.py` that the SSE stream never includes an `"intent"` field per chunk —
only `"content"` — so the non-streaming endpoint was used to get intent labels alongside
each answer for this validation; both endpoints share the same orchestrator logic
underneath). Driver: `test_reports/qbank_artifacts/p2_live_conversations.py`. Full transcript:
`test_reports/qbank_artifacts/p2_live_results.json`.

### Conversation A — small talk

| turn | message | intent | time | answer (truncated) |
|---|---|---|---|---|
| 1 | Hello, good morning. | `greeting` | 8.2s | "Good morning! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant…" |
| 2 | What can you help me with? | `capabilities` | 1.1s | deterministic capability list, no embedding/LLM call — confirms §D |
| 3 | Thanks for the explanation. | `explain_clarify` | 0.9s | "I'm not sure what you mean…" |
| 4 | Goodbye. | `unknown_message` | 0.2s | "I could not understand that message…" |

### Conversation B — domain exclusion

| turn | message | intent | time | answer |
|---|---|---|---|---|
| 1 | Investigate 2026/0153/02/045470. | `application_status` | 5.4s | real DB record (NISD, Rejected, applicant name, survey 110) |
| 2 | What is its status? | `application_status` | 1.0s | "rejected" |
| 3 | When was the registration document registered? | `general_query` | 66.5s | LLM timeout message |
| 4 | What happened before the rejection? | `general_query` | 127.8s | LLM error message |

### Conversation C — context retention

| turn | message | intent | time | answer |
|---|---|---|---|---|
| 1 | Compare 2026/0153/02/045470 and 2026/0153/02/045472. | `compare_applications` | 0.3s | side-by-side table |
| 2 | What about the other one? | `followup_list` | 0.2s | resolved from context, a 1-row table |
| 3 | Which one was submitted first? | `compare_applications` | 0.2s | re-derived the same comparison |
| 4 | Show both applications again. | `compare_applications` | 0.3s | same comparison, repeated correctly |

### Conversation D — ambiguous wording

| turn | message | intent | time | answer |
|---|---|---|---|---|
| 1 | Tell me about this. | `general_query` | 3.8s | "I'm not sure what you mean…" (correct abstention, not misrouted as greeting) |
| 2 | Can you explain that? | `explain_clarify` | 0.2s | same generic fallback |
| 3 | What about the other one? | `followup_clarification` | 0.2s | "I do not know which application or list you mean — nothing has been shown in this conversation yet." (correct — asks for clarification rather than guessing or treating it as small talk) |

**The property Phase 10 actually checks held completely across all 4 conversations: not one
domain question or domain-shaped follow-up (B's and D's turns) was ever misrouted into the
greeting/smalltalk/thanks/farewell path.** Conversation C shows context retention working
unaffected — none of its turns are eligible for the semantic classifier anyway (all carry
application numbers / are resolved deterministically), so this confirms the semantic layer's
narrow eligibility gate is doing its job of staying out of the way.

### Known limitations surfaced by this live run (documented honestly, not fixed — out of this task's scope)

Two conversational-quality gaps were found, both **upstream of** `semantic_intent.py` and
**pre-existing** (confirmed by calling `semantic_intent.classify()` directly in isolation,
which correctly returns `"thanks"` for "Thanks for the explanation." and `"farewell"` for
"Goodbye." — the module itself is not the problem):

1. **"Thanks for the explanation."** right after a capability answer got `intent =
   explain_clarify` instead of reaching `semantic_intent.classify()` at all, because a
   deterministic keyword rule for "explain" claims the turn before `intent == "general_query"`
   is ever reached (the orchestrator only calls the semantic classifier on that specific
   intent value). This is the deterministic-first priority order working exactly as the task
   requires — it just means an "explain"-shaped thank-you gets the wrong deterministic
   answer instead of a chance at the semantic one. Out of scope to fix here (it is a
   `parse_intent` keyword-precedence question, unrelated to hybrid routing).
2. **"Goodbye."** got `intent = unknown_message` ("gibberish" sub-kind) from
   `chat_pipeline.py`'s `_unknown_message_kind()` gate — a separate, earlier gate than the
   `orchestrator.py` one this task is about, which runs before `parse_intent` even assigns
   `general_query`. Because the orchestrator's semantic-classifier guard is specifically
   `intent == "general_query"`, a bare "Goodbye." never reaches `semantic_intent.classify()`
   either, even though it would correctly fire `farewell` if asked directly (verified: cos
   0.99, margin 0.25). **This is a real, narrow gap**: widening the orchestrator's guard to
   also accept `intent == "unknown_message"` would likely close it safely, since that bucket
   is semantically "no deterministic rule matched" just like `general_query` is — but it
   touches a different module's classification order and was not implemented here, both to
   respect the task's "do not reorder the existing dispatch chain" instruction and this
   task's effort budget. Recorded here as a recommended follow-up, not a false "it's fixed."

## J. Git safety confirmation

- `git status --short | wc -l` at session start: 67 (matches the P1-2 remediation's own
  final count, confirming nothing was lost between sessions).
- No destructive git command was run (`reset --hard`, `clean`, `checkout -- .` never used).
- No commit, stash or push was made.
- Files changed by this task:
  - **Modified** (2): `backend/config.py` (added `SEMANTIC_INTENT_MODE` setting),
    `backend/services/chatbot/semantic_intent.py` (added the shadow-mode branch, ~8 lines).
  - **New** (untracked): `tests/unit/test_semantic_intent.py`,
    `test_reports/HYBRID_ROUTING_BASELINE.md`,
    `test_reports/HYBRID_INTENT_ROUTING_REPORT.md` (this file),
    `test_reports/HYBRID_INTENT_ROUTING_RESULTS.csv`,
    `test_reports/qbank_artifacts/p2_live_conversations.py`,
    `test_reports/qbank_artifacts/p2_live_results.json`, plus working scripts under
    `scratch/` (evaluation drivers, not part of the application).
- All pre-existing P0/P1 fixes remain present and passing: 269/269 unit tests pass
  (247 pre-existing + 22 new), confirmed by running `pytest tests/unit/` after every change
  in this task.
- Backend server: started fresh (bytecode cleared first) for live validation, stopped at the
  end of this task; confirmed down (`curl .../api/docs` → connection refused).

## K. Final acceptance criteria — status

| criterion | status |
|---|---|
| Existing deterministic routing remains authoritative | ✅ unchanged; semantic classifier only ever runs after it, and only for 4 already-active categories |
| Nomic configuration and lifecycle verified | ✅ §C |
| Generic intent prototypes defined | ✅ already existed for 4 categories; 3 more considered and deliberately not added (§D) |
| Prototype embeddings cached | ✅ already existed, confirmed + tested |
| User messages embedded only when eligible | ✅ already existed (digit/script/length/domain-term gate), confirmed by live false-positive sweep |
| Cosine similarity and margin computed correctly | ✅ confirmed by direct test (`test_cosine_similarity_calculation`, `test_margin_threshold_abstains_on_ambiguous_message`) |
| Abstention supported | ✅ already existed; measured 9/21 safe abstentions in the recall sweep |
| Domain-specific questions cannot be captured by the generic classifier | ✅ 1/874 (0.11%) on real data; 0/14 domain-shaped turns in the live conversations |
| Shadow mode runs without changing actual intent selection | ✅ new — `SEMANTIC_INTENT_MODE=shadow`, verified live |
| False-positive routing measured on the existing question bank | ✅ §F.1, CSV attached |
| Performance overhead measured | ✅ §C/§G, cold and warm separately |
| Feature activation configurable | ✅ new config flag, default preserves current behavior |
| Embedding failure falls back safely | ✅ already existed, now also unit-tested (`test_embedding_model_unavailable_returns_none`, `test_embedding_timeout_falls_through_safely`) |
| Existing P0/P1 tests remain passing | ✅ 269/269 |
| Live API behaviour evaluated | ✅ §I, all 4 required conversations |
| Final report created | ✅ this document |
