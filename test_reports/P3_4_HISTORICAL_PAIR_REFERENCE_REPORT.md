# P3.4 Explicit Application and Historical Pair Reference Resolution — Report

## 1. Behaviour before the change

Pre-change run (in-process, real DB; `scratch/p34_before_seq.txt`, `scratch/p34_before_extra.txt`).
The results were identical with normal, absent and stale client history, so client history played no role:

| turn (after "Compare A and B", then "Show the status of C") | before |
|---|---|
| Q3 Compare C with the first application from our earlier comparison | "Which applications do you want to compare? Please specify two…" |
| Q4 What is the status of the second application from our earlier comparison? | **wrong:** register-wide status breakdown (`pending_applications`) |
| Q5 What about the other one? | **wrong:** after Q3/Q4 it silently answered **B**; straight after C it fell to the LLM (timeout) |
| Q6 Show both applications from our previous comparison again | **wrong:** "Your most recent application is …046461" (`last_application`) |
| Compare this with the first application from our earlier comparison | **wrong:** a service-code count comparison |
| Show the difference between this application and the first one we compared earlier | **wrong:** a service-code count comparison |
| Compare C with both applications from our previous comparison | **wrong:** C's details only, A and B silently dropped |
| Show both applications again | A/B, correct (P3.3) |

So the existing `prior_pair` restored A/B only for the literal "show both again" phrasing. Nothing
else could reach it.

## 2. Root cause

1. **No reader for a historical reference.** Nothing mapped "the first / second application from our
   earlier comparison" or "both … from our previous comparison" onto the earlier pair. The word
   "comparison" made `fctx.resolve` treat the message as a new comparison request (ambiguous, no
   sides), and `parse_intent` routed the text by its other words (pending, last application,
   service-code comparison).
2. **`prior_pair` was too fragile.**
   - A comparison derived from it (C vs A) did not count as derived, so a following single turn
     (status of B) overwrote `prior_pair` with C/A and "our earlier comparison" stopped meaning A/B.
   - Returning to a member (B) silently re-activated the A/B pair, so "the other one" quietly resolved to A.
3. **"Other one" fell through.**
   - `_resolve_pair_reference` stood aside when only the unrelated C was active.
   - Where it was flagged ambiguous, a legacy fallback in the orchestrator ("stand aside if the transcript
     names a number in view") overrode the clarification. The number in view was C itself, which "the other
     one" by definition does not mean. The turn then went to the LLM.

**Could the existing resolution object represent "explicit C + historical A"?** Not as a single
`Resolution`: a comparison resolution carries one pair. It didn't need to. Turning the historical
reference into its application number before routing means the P3 explicit two-ID path, which already
validates and orders two explicit numbers, does the rest. No new resolution type and no new context
field were added; `prior_pair` stays a key in the existing `filters` dict.

## 3. Resolution precedence (P3.4 additions in bold)

1. Explicit IDs in the current message win; two IDs → comparison in message order.
2. **An explicit historical reference ("the first/second application from our earlier/previous
   comparison", "the first one we compared earlier", "both applications from our previous comparison")
   is replaced by the number it names, read from the newest server context: `prior_pair`, else the
   active pair. "This" becomes the canonical current application. The result then goes through rule 1.**
3. Pair references (other / first / second / both) against the **active** pair (P3.1).
4. A singular follow-up uses `current_application` (P3.3).
5. Otherwise clarification. **Also:**
   - **no earlier comparison → "no earlier comparison in this conversation";**
   - **"this" with no current application → ask;**
   - **three entities (C + both) → "compare C with A or B?", never a silent drop;**
   - **"the other one" while only an unrelated application is active → ask.**
6. Client history never selects (P3.3).
7. Every rewritten number re-passes the gate (`lookup_application_access`) independently.

## 4. Active pair versus prior pair

| | meaning | who reads it |
|---|---|---|
| `active_applications` | the set currently in view (P3.1 semantics unchanged: a new unrelated application starts its own set) | other / first / second / both, singular focus |
| `current_application` | the one application in focus | "its", "this application", "this" in a historical reference |
| `filters["prior_pair"]` | the last comparison pair the officer **established**, kept beside the active set | only explicit historical references and "show both (again)" |

`_carry_prior_pair` (a wrapper around `build_context`, so every branch gets the same rule):
- leaving a two-application pair records it, in order;
- a comparison **derived** from the prior pair (C/A shares A with A/B) does not replace it;
- a new explicit comparison makes the old active pair the prior pair;
- the prior pair carries forward otherwise, and is dropped once it is active again;
- returning to a member no longer silently re-activates the pair (that P3.3 rule was removed: it is
  what made "the other one" guess).

## 5. Before / after transcripts (stale client history; normal and absent were identical)

| Q | before | after | resolved IDs (server trace) | handler | gate |
|---|---|---|---|---|---|
| 1 Compare A and B | A vs B | A vs B | 045470, 045472 | compare_applications | ok |
| 2 Show the status of C | C approved | C approved | 012766 | application_status | ok 012766 |
| 3 Compare C with the first application from our earlier comparison | generic "which applications?" | **C vs A** (that order) | rewrite → 012766, 045470 | compare_applications | ok (each validated) |
| 4 Status of the second application from our earlier comparison | register breakdown | **B rejected** | rewrite → 045472 | application_status | ok 045472 |
| 5 What about the other one? | B (guessed) / LLM timeout | **asks which one** (names the current application and the earlier pair) | none | followup_clarification | — |
| 6 Show both applications from our previous comparison again | "your most recent application…" | **A vs B** | rewrite → 045470, 045472 | compare_applications | ok |

Also verified live:
- "Compare C with both applications from our previous comparison" → *"I can compare two applications
  at a time. Compare 012766 with which one from the earlier comparison: 045470 or 045472?"*
  (`clarification_required`; all three named, none dropped).
- A fresh session sending only client history, asking about "the second application from our earlier
  comparison" → *"There is no earlier comparison in this conversation…"*.

In-process (`scratch/probe_p34.py`, stale history):
- "Compare this with the first…" → C vs A;
- "Compare C with the second…" → C vs B;
- "difference between this application and the first one we compared earlier" → C vs A;
- "Show both applications again" → A vs B.

## 6. Test counts

| | count |
|---|---|
| start of P3.4 | 450 passed |
| final | **493 passed, 0 failed** |
| new `tests/unit/test_historical_pair_references.py` | 43 |

The 43 cover the 13 required items plus regressions:

| required item | test |
|---|---|
| 1 C + first of A/B | `test_explicit_c_with_first_member_of_prior_pair` (4 history variants) |
| 2 C + second | `test_explicit_c_with_second_member_of_prior_pair` |
| 3 C + "other one" asks | `test_other_one_after_c_asks` (4 variants), `test_other_one_after_unrelated_application_asks` |
| 4 C + both does not drop | `test_c_with_both_from_earlier_comparison_asks_and_drops_nothing`, `test_three_entities_ask…` |
| 5 "show both again" restores A/B | `test_show_both_again_restores_a_b` (both phrasings) |
| 6 new explicit C/D comparison | `test_a_new_explicit_comparison_becomes_active…`, `test_new_explicit_comparison_replaces_the_active_pair` |
| 7 pair order preserved | `test_prior_pair_order_is_preserved` (B/A → "first" = B), `test_leaving_a_pair…in_order` |
| 8 invalid C never falls back | `test_invalid_c_never_falls_back` |
| 9 inaccessible historical member | `test_inaccessible_historical_member_is_refused_not_substituted` (**mocked access**: no seeded file is outside this officer's jurisdiction) |
| 10 no prior pair asks | `test_no_prior_pair_asks`, `test_no_prior_pair_asks` (pure) |
| 11 fresh session | `test_fresh_session_cannot_reach_another_sessions_pair` |
| 12 stale / conflicting history | `test_stale_and_conflicting_history_do_not_change_the_outcome` (normal / none / stale / conflicting all identical) |
| 13 stream = non-stream | `test_stream_matches_non_stream` (3 questions) |
| regressions | explicit ID wins; first/second inside an active pair; other-one inside the active pair (P3.1); singular focus after a historical reference (P3.3); untouched messages without a historical phrase |

No existing test was changed. The P3.1 test pinning `active_applications == [C]` after a new
application still passes.

## 7. Live verification (real server, real DB, real JWT)

- **Setup:** fresh server on `127.0.0.1:8001`; 28 turns; per-turn server trace joined from that server's log.
- Script: `test_reports/qbank_artifacts/p3_4_historical_pair_conversations.py`.
- Results: `p3_4_historical_pair_results.json`. Tables: `p3_4_live_turns.md`.

Results:
- Normal, absent and stale history gave **identical answers** on all six turns.
- Q3–Q6 over `/chat/stream` are character-identical to `/chat`.
- Mean latency per turn: normal 0.41 s, none 0.28 s, stale 0.27 s; max 0.66 s.
- On every stale turn the server logged `client_app_ids=[012766, 999999]`. Those IDs were never used
  except where the officer typed 012766 themselves (Q2, Q3).

**Note on the run environment:** a backend I did not start was already listening on `0.0.0.0:8000`
(PID 36800, started 07:38). A first attempt on port 8000 reached that process and was discarded. The
reported run used my own server on 8001. PID 36800 was left running.

## 8. Session and jurisdiction checks

* **Server context only.** The historical rewrite reads `_load_followup_context`, which is owner-checked:
  - another officer's session gives no context, hence "no earlier comparison";
  - a fresh session has none (tested and live).
* **Each number re-authorized.** The rewrite outputs plain numbers that go through the existing gate.
  An inaccessible member is refused, with no other application substituted (tested, mocked access).
  An invalid C is reported as not found, with no fallback (tested).
* **No state change on a question.** A clarification turn saves no follow-up context
  (`_clarification_turn`), so focus, pair and prior pair stay exactly as they were.
* **Typed text in the transcript.** The transcript stores what the officer typed (`_SAVE_AS`, reset per
  turn), not the rewritten text.
* **Logs:** counts and application numbers only (`historical_ref prior_pair=… current=… resolved=… clarify=…`).

## 9. Unresolved limitations

* **"Earlier comparison" means the most recent established pair.** After several comparisons
  (A/B → C/D → E/F) it means C/D. Phrases naming an older one ("the comparison before that") are not
  supported and fall through to the ordinary paths.
* **Bare ordinals reach back.** A bare "the second one" (no "earlier comparison") after an unrelated
  application still uses the loader's long-standing ordinal reach-back to the newest list. That is
  P2/P3 behaviour, not changed here. Only "the other one" was made to ask.
* **No three-way comparison**, by design. C + both asks which pair to form.
* **Fixed phrasings.** The historical-reference vocabulary is a fixed set ("from our/the
  earlier/previous/last/prior/original comparison/pair", "we compared earlier/before/previously").
  Tamil / Tanglish phrasings of the same reference are not covered.

## 10. Changed files and Git safety

| file | change |
|---|---|
| `backend/services/followup_context.py` | `build_context` → wrapper + `_carry_prior_pair` + `_is_app_pair`; removed the P3.3 single-branch prior-pair block; `_HIST_*` patterns, `has_historical_ref`, `historical_pair`, `rewrite_historical_refs`, `is_other_ref`; `_resolve_pair_reference` gains `language` and the "other one" clarification |
| `backend/services/chatbot/orchestrator.py` | `_historical_ref_turn`, `_clarification_turn`; called at both entry points; `_SAVE_AS` reset per turn; legacy ambiguity override skips "the other one" (both paths) |
| `tests/unit/test_historical_pair_references.py` | new, 43 tests |
| `test_reports/qbank_artifacts/p3_4_*` | live script, log join, results JSON, per-turn tables |

Also done in this turn at your request (see the closing message):
- the `chat.js` XSS fix (`sanitizeBotHtml`, cache-buster bump);
- `tests/frontend/test_sanitize_bot_html.html`;
- a `sis-static-tests` entry in `.claude/launch.json`.

**Git and line endings:**
- Branch `main`, HEAD `eaff14e` unchanged. Nothing staged, committed, stashed, reset, cleaned or discarded.
- During this work one scripted edit wrote `orchestrator.py` in Windows text mode and turned every
  line ending into CRLF. I restored LF, its previous form, by byte replacement and confirmed the diff is
  content-only.
- Files under `core.autocrlf=true` that were already CRLF in the working tree were left as they were.
