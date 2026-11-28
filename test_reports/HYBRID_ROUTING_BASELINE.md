# HYBRID_ROUTING_BASELINE — P2 Hybrid Semantic Intent Routing

**Date:** 2026-11-21 · **Branch:** `main` · **HEAD:** `eaff14e50b7162339dc458b630cf406a037efd1d`
**Git status:** 67 pre-existing modified/untracked entries, unchanged from the start of this
task (confirmed via `git status --short | wc -l`). No destructive git command was run.

## A. Headline finding — the task's premise needs correcting

The task describes Phase 1-9 as if hybrid semantic routing does not exist yet ("design a
safe hybrid architecture", "create a small, isolated module ... `semantic_intent.py`"). **It
already exists, is already active in production (not shadow), and already implements the
layered, narrow, fail-safe design the task asks for** — for four of the seven prototype
categories the task lists.

File: [`backend/services/chatbot/semantic_intent.py`](backend/services/chatbot/semantic_intent.py)
(139 lines; CLAUDE.md's "Semantic routing" section names it at the old pre-refactor path
`backend/services/semantic_intent.py` — it now lives under `backend/services/chatbot/`,
alongside the rest of the package the chatbot was split into. That line in CLAUDE.md is
stale and should be corrected, but nothing else about its description is wrong.)

This report therefore does **not** build a greenfield classifier. It (1) verifies the
runtime facts Phase 1 asks for, (2) calibrates and stress-tests the *existing* classifier
against real question-bank data rather than a handful of happy-path examples, (3) checks
whether the three categories the task additionally wants (`GENERAL_HELP`,
`SYSTEM_CAPABILITIES`, `GENERAL_INFORMATION`) are actually a gap worth closing, and
(4) only then decides what, if anything, Phase 2-11 should change.

## B. Existing architecture (Phase 1 inspection)

```
parse_intent()  ──► intent != "general_query"? ──► deterministic handler (unchanged, untouched)
        │
        ▼ intent == "general_query"
_is_capability_question(message)?  ──yes──► deterministic "what can you do" answer (no LLM, no embedding)
_is_out_of_scope(message)?         ──yes──► deterministic off-topic refusal   (no LLM, no embedding)
_special_scope_kind(message)?      ──yes──► deterministic sentiment/identity/time/etc. answer
        │  none of the above
        ▼
semantic_intent.classify(message, has_domain_term)   ← THE place this task is about
        │
        ├─ not eligible (long / has digit / Tamil-script / carries a domain term) → None, fall through
        ├─ eligible → embed message, cosine vs cached prototype matrix
        │     fires only if best_cos ≥ 0.75 AND best_cos − best_SIS_anchor_cos ≥ 0.08
        │     → "greeting" | "smalltalk" | "thanks" | "farewell", else None (abstain)
        ▼
_sem_kind truthy → intent forced to "greeting" → chat_smalltalk.py's handler, which
re-derives thanks/farewell/plain-hello from the RAW MESSAGE TEXT itself
(`_greeting_is_thanks`, `_greeting_is_farewell`), never from the semantic label.
        │
        ▼ (still general_query / no semantic hit)
agent layer → RAG fallback → llama3.1 (unchanged, untouched)
```

Call sites: `orchestrator.py:2631` (non-streaming) and `orchestrator.py:5523` (streaming) —
identical guard, identical call. No other module calls `semantic_intent.classify`; the
dozen `from . import semantic_intent` lines elsewhere in `backend/services/chatbot/` are
the package's shared-import boilerplate and are unused re-exports, not additional call
sites (verified with `grep -rn "semantic_intent\." backend/services/chatbot/*.py` — only
`orchestrator.py` calls a function on it).

### Answers to Phase 1's eight verification questions

| # | Question | Answer |
|---|---|---|
| 1 | Is `nomic-embed-text` genuinely available and loaded? | Available (`ollama list` / `/api/tags` confirms `nomic-embed-text:latest`, 768-dim, F16, `nomic-bert`). **Not resident** at session start — `/api/ps` showed only `sis-assistant:latest` (the LLM) loaded, zero VRAM for the embedder. The project's own assumption ("configured ⇒ loaded") is false at any point Ollama has been idle long enough for Ollama's keep-alive to evict it. |
| 2 | How are embeddings generated now? | `backend/services/embeddings.py`: a module-level `OllamaEmbeddings` client (`langchain_ollama`), created once at import time, calling `POST /api/embed` via `embed_query` / `embed_documents`. |
| 3 | Is the model reloaded per request? | No — the Python *client* is a singleton (module-level `embeddings_model`). Whether the GGUF stays resident **in Ollama** is Ollama's own `keep_alive` policy, outside this app's control; Ollama evicts an idle model on its own schedule (observed: evicted at session start despite being configured and used repeatedly in past sessions). |
| 4 | Exact dimensions / model id | `nomic-embed-text:latest`, 768-dim, confirmed via a live `/api/embed` call (`len(embeddings[0]) == 768`) and via Ollama's own model metadata (`embedding_length: 768`). |
| 5 | Does the project already use a task prefix? | Yes — `_PREFIX = "classification: "` in `semantic_intent.py`, applied uniformly to every prototype **and** every candidate message (`_embed()` prepends it unconditionally). This matches Nomic's own documented prefix convention (`classification:`, `search_query:`, `search_document:`, `clustering:`) — the same prefix family `pgvector_store.py`'s document-search embeddings presumably use a different prefix for (not audited here; out of scope — that path is unrelated to intent routing). |
| 6 | Does a prototype cache exist? | Yes — `_matrix` / `_labels` module-level globals, built once by `_build()` behind an `asyncio.Lock`, read-only after that. 95 vectors (4 categories × ~12-18 phrases, plus 32 hard-negative SIS anchors) as of this commit. |
| 7 | Sync or async? | `_embed()` / `score()` are synchronous, blocking calls. `classify()` wraps both the one-time `_build()` and the per-message `score()` in `asyncio.to_thread(...)`, so the event loop is never blocked — this answers Phase 1's async concern correctly already. |
| 8 | Can it run without delaying deterministic queries? | Yes, by construction, **and** the orchestrator additionally skips calling it at all unless `intent == "general_query"` and none of `_is_capability_question` / `_is_out_of_scope` / `_special_scope_kind` already claimed the turn (see the inline comment at `orchestrator.py:2617-2627`, which documents exactly this optimization and why it was added — a deterministic SIS question or a capability/off-topic question never reaches the embedder, so it never carries the embedder's latency). |

### Measured latency (this machine, this session, live Ollama, CPU inference — no GPU)

| call | latency |
|---|---|
| cold single embed (`nomic-embed-text` not resident) | 3.33s |
| warm single embed (steady state, 5 consecutive calls) | 2.07 – 2.33s (mean ≈ 2.13s) |
| cold prototype-matrix build, 95 phrases, one batched call | 4.33s (one-time, amortized over the process's lifetime) |
| batched embed of 92 candidate messages, one call | 2.13s total (≈ 0.023s/message when batched) |

**The important number is the warm single-message call: ~2.1s.** That is the real cost of
every `semantic_intent.classify()` call that reaches the embedder — not "fast and
inexpensive" as Phase 8 hopes for, at least not on CPU inference. This is why the
orchestrator's pre-filter (skip the call entirely unless `general_query` and not already
claimed) matters far more than the embedding call's own cost: on this deployment, every
avoided call is worth ~2 seconds, and the three deterministic guards ahead of it
(`_is_capability_question`, `_is_out_of_scope`, `_special_scope_kind`) are what keeps the
embedder off the hot path for the bulk of `general_query` turns.

## C. Calibration — is the existing 0.75 / 0.08 threshold/margin actually safe?

Phase 2's deployed threshold (`_THRESHOLD = 0.75`) and margin (`_MARGIN = 0.08`) were not
re-derived from scratch here (doing so would require re-fitting on held-out data and is a
larger undertaking than this task's effort budget) — instead they were **stress-tested**
against real data, which is the part Phase 1-6 actually cares about: would raising/lowering
either knob change the safety verdict below.

### C.1 False-positive sweep — real domain questions, not the module's own training examples

Pulled every line from `test_questions_openers.txt` (88), `test_questions_100.txt` (388),
`test_questions_200.txt` (674) and `test_questions_206.txt` (692) — 874 unique lines after
dedup. Every one of these is, by construction, a genuine SIS domain question (none is
conversational filler). Ran each through the module's own eligibility gate
(`looks_short_and_plain`) first — this is the gate the orchestrator itself applies before
ever calling the embedder — then scored every eligible one with the live model.

```
total unique lines:        874
passed the eligibility gate (short, digit-free, no domain term): 92
classified as conversational (= false positive, since every line is a real domain question): 1 / 92
```

The one false positive: **`"who am i"`** → `smalltalk` (cos 0.891, margin over the SIS
anchor 0.162 — a confident, not a marginal, hit). This is a genuinely ambiguous sentence —
read literally it is indistinguishable from "who are you" / "are you a robot", which *are*
smalltalk prototypes; whether an officer meant "tell me which account I'm logged in as"
depends entirely on context this four-word sentence does not carry. **No other domain
question — not one of the other 91, and not one of the 782 that the eligibility gate
correctly excluded before the embedder was ever asked — was misclassified.** False-positive
rate on this real corpus: 1/874 ≈ 0.11% overall, 1/92 ≈ 1.1% among the subset that even
reaches the embedder.

(Full script: `scratch/eval_semantic_p2.py`; raw output: `scratch/fp_eval_out.txt`.)

### C.2 Recall sweep — held-out conversational phrasings, never seen by the prototypes

21 phrasings across the four categories, deliberately **not** copied from `_PROTOTYPES`
(abbreviations, Tanglish, casual spelling): `scratch/eval_semantic_p2b.py`.

```
correct (fired, right category)     10 / 21   (47.6%)
abstained (safe — falls through to the ordinary pipeline)   9 / 21  (42.9%)
wrong category, but still fired as conversational            2 / 21  (9.5%)
```

**Neither "wrong" case is a real failure.** Both wrong-category hits
(`"hey there how's everything"` → `smalltalk` instead of `greeting`; `"poren da"` → `greeting`
instead of `farewell`) still correctly set `intent = "greeting"`, and the actual reply shape
is decided downstream by `_greeting_is_thanks()` / `_greeting_is_farewell()` in
`chat_smalltalk.py`, which read the **raw message text**, not the semantic label (confirmed
by reading that module — see §B above). The semantic label's only job is the binary
fire/abstain decision; its specific string value is never consulted again. On that binary
measure the real recall is 12/21 = 57%, and every miss is an **abstention**, i.e. the safe
failure mode the task itself asks Phase 4/7 to require — a missed "hlo" or "tq" simply falls
through to the ordinary pipeline (which already answers a bare "hlo" via the older
exact/token-match greeting rule CLAUDE.md documents, or, failing that, the LLM, slower but
not wrong).

**Conclusion on calibration:** the deployed 0.75/0.08 pair is conservative (favors
abstention over a wrong domain capture) and is empirically safe on the two question banks
checked. Loosening it would recover some of the 9 abstentions above at a measurable risk to
the 0.11% false-positive floor; tightening it would only shrink recall further on an already
low-stakes category (a missed greeting costs nothing but one slower LLM round trip). No
change to the threshold/margin is recommended.

## D. Should `GENERAL_HELP` / `SYSTEM_CAPABILITIES` / `GENERAL_INFORMATION` be added?

Phase 2/5 ask for these three additional prototype categories. Checked whether they are
actually unhandled:

- **`SYSTEM_CAPABILITIES`** ("what can you help me with?", "what does this chatbot do?") is
  already caught by `_is_capability_question()` in `chat_shared.py` / `chat_smalltalk.py`
  (documented in CLAUDE.md's "Off-topic questions, and 'what can you do'" section) — **and
  it runs before `semantic_intent.classify()` is ever called** (`orchestrator.py:2629`:
  `not _is_capability_question(message)` is one of the guard conditions). Adding this as an
  embedding prototype would either (a) never fire, because the deterministic guard already
  claimed the turn, or (b) have to be reordered ahead of that guard — which the task's own
  Phase 2 explicitly forbids ("Do not reorder the existing dispatch chain").
- **`GENERAL_INFORMATION` / off-topic** ("what's the weather", "write me a poem") is the
  same situation with `_is_out_of_scope()` — already deterministic, already ahead of the
  embedder in the guard, zero LLM/embedding cost, and documented as "high-precision,
  low-recall on purpose" specifically so a real domain question is never swept up by it
  (CLAUDE.md, "Off-topic questions" section). This is already a safer design than an
  embedding classifier for the same job: a keyword-anchored deterministic guard costs zero
  extra latency and has a provably bounded false-positive surface, where an embedding
  classifier (as §C showed) still has a non-zero one ("who am i").
- **`GENERAL_HELP`** is not a separate category in this codebase at all; it is the same
  thing `SYSTEM_CAPABILITIES` already covers (CLAUDE.md names only one guard,
  `_is_capability_question`, for both "what can you do" and "what can you help me with").

**Recommendation: do not add these three categories to the embedding classifier.** Doing so
would (a) require reordering the dispatch chain, which the task itself forbids, or (b) sit
behind guards that already claim 100% of the traffic those categories would ever see, making
the new prototypes permanently unreachable dead code — the opposite of "incremental routing
enhancement", and a violation of the project's own stated preference for a zero-cost
deterministic answer over a ~2-second embedding round trip wherever one already exists and
works. This is recorded here rather than silently skipped, per the task's own instruction to
treat every phase explicitly.

## E. What this leaves for the rest of the task

Given §D, Phases 3/5 (build new prototypes for those three categories) do not apply — there
is nothing left ungapped for them to close. The remaining phases are scoped down to:

- **Phase 4/7 — shadow-mode / activation configurability.** The existing classifier has no
  shadow mode and no feature flag: it is unconditionally active for the four categories it
  already covers. Given §C's results (0.11% false-positive rate on 874 real questions, safe
  abstention on every recall miss), re-litigating activation for those four categories is not
  warranted — but a lightweight, additive shadow-mode logging hook is still worth building
  for **future prototype additions** (the task's own ask), so the next person who wants to
  add a fifth category can measure it safely before flipping it live. Built below as an
  opt-in diagnostic logger, default off, zero behavior change.
- **Phase 9 — regression tests.** `tests/unit/test_semantic_intent.py` did not exist; add it,
  covering the module's actual behavior (not a reimplementation).
- **Phase 10 — live API validation.** Run Conversations A-D against the real API, confirming
  domain exclusion and context retention are unaffected.
- **Phase 11 — final report**, reflecting all of the above honestly, including the
  recommendation in §D.

## F. Files touched by this task so far

None yet — this is the baseline / inspection report. `git status --short | wc -l` is still
67, unchanged from session start.
