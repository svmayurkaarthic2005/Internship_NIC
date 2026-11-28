# P3.3 Entity Resolution — Baseline and Call-Path Map

**Note on timing:** this file was written after the fix landed, not before. The pre-fix
evidence below comes from two sources recorded *before* any P3.3 edit: the P3.2 live run
(`scratch/p32_live.txt`), and a mutation run of the new P3.3 tests with the fix patched
back out (`scratch/p33_mutation.txt`).

## 1. Repository state at start

| item | value |
|---|---|
| branch | `main` |
| HEAD | `eaff14e50b7162339dc458b630cf406a037efd1d` |
| `git status --short` | 106 entries (all pre-existing user work, untouched) |
| `pytest tests/unit` | **376 passed** (83 s) |

## 2. Reproduction (pre-fix, live API, P3.2 run)

The same six-turn conversation was sent three ways. The only difference was `chat_history`:

| variant | Q6 "What was its rejection reason?" |
|---|---|
| normal (accurate accumulated history) | 045470 — correct |
| none | 045470 — correct |
| **stale** (fixed copy naming 012766 / 999999, resent every turn) | **"2026/0154/02/012766 is not in a rejected status"** — wrong application |

The server-side FollowupContext held `current_application = 2026/0153/02/045470` throughout.
The client copy outvoted it.

## 3. Root cause

Two context mechanisms coexist:

* **server-side**: `chat_messages.structured_data` → `_load_followup_context()` → `FollowupContext`
  (`active_applications`, `current_application`), already officer-checked and time-limited.
* **client-side**: `ChatRequest.chat_history`, unauthenticated, passed unchanged from the router into
  `process_chat` / `process_chat_stream`.

`grep -c chat_history orchestrator.py` = **205**. Every fallback that scans history for an application
number read the client copy. The main ones:

| consumer | file | what it does with history |
|---|---|---|
| `resolve_application_reference` | chat_application_queries.py | `_cm_strong` scan of last 14 msgs; `_app_numbers_in_recent_context`; `_extract_app_number_from_context`; `_app_numbers_from_last_listing` |
| `_extract_app_number_from_context` | chat_followups.py | regex scan of recent turns |
| `_app_numbers_in_recent_context` | chat_application_queries.py | ambiguity check over recent turns |
| `_rejection_reason_answer`, `_who_decided`, ISD/NISD workflow attempt, `_other_code_workflow`, `_attached_documents` | chat_application_queries.py | fall back to `_unheld_qa.context_app(chat_history)` |
| `_cm_strong`, `_app_evidence_in_scope`, `_bare_app_status` | chat_shared.py / chat_corrections.py | decide routing from history |
| early turn chain (`_merge_turn` … `_number_turn`), `_howto_turn`, record-module hooks | orchestrator / chat_routing | receive `chat_history` directly |

## 4. Call-path map

```
POST /api/v1/chat ─┐                         POST /api/v1/chat/stream ─┐
  routers/chat.py  │ verify_session_ownership     routers/chat.py      │
                   ▼                                                   ▼
            process_chat(msg, sid, officer, db, chat_history)   process_chat_stream(...)
                   │  ← chat_history = client copy (pre-fix)           │
                   ├─ _big_plan → _answer_big → _single_turn ──────────┤
                   ├─ _merge_turn … _number_turn (early chain) ────────┤
                   └─ _process_chat_impl                     _process_chat_stream_impl
                        ├─ _load_followup_context  (server)   ├─ (same)
                        ├─ fctx.resolve / _apply_followup_resolution
                        ├─ _workflow_remarks_answer(ctx)       ├─ (same)
                        ├─ _carried_details_message(ctx)       ├─ (same)
                        ├─ resolve_application_reference(history)  ← Step 2d gate
                        │     → _gate_app_number
                        └─ _early_subdivision_answer(gate_app, history)
                              reasoning → _who_decided → _rejection_reason_answer
                              → ISD/NISD workflow → _other_code_workflow → … record modules
```

The router is the **only** place client history enters (`handle_chat` and the sample-db scripts pass none).
So the smallest shared fix point is the two public entry points.

## 5. Handlers affected pre-fix (mutation run: fix patched out, P3.3 tests run)

| failed under stale history | passed anyway (FollowupContext rewrote the message first) |
|---|---|
| rejection reason (Q6), workflow history, SIS recommendation (record module), fresh session + client history (status, rejection), streaming path | status, details, who-decided |

## 6. Separate pre-existing gaps found while tracing (same on accurate history)

* After "Switch back to the first application", "Show its details" and "Why was this application rejected?"
  covered **both** pair members: `_carried_details_message` and `_workflow_remarks_answer` scoped to
  the whole carried list and ignored `current_application`.
* "Show its workflow history" stayed `general_query` and went to the LLM (timeout), **even with a single
  application in context**. The gate resolved the app, but no intent claimed the turn.
