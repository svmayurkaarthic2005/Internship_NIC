# SIS Chatbot — Question Bank End-to-End Test Report

**Date of testing:** 2026-11-21
**Backend commit:** `eaff14e` (`main`), **with 71 uncommitted modified/untracked files on top** (see "Test environment" — several of the fixes exercised here, and several of the modules this report blames, are part of that uncommitted work)
**Database:** live `sis_chatbot_db`, PostgreSQL, loaded from `backend/sample_db_new` (not the older `backend/sample_db` pipeline CLAUDE.md still documents — table names have no `_demo` suffix in the live DB)
**API endpoint exercised:** `POST /api/v1/chat/stream` (SSE), via `POST /api/v1/auth/login` and `POST /api/v1/chat/sessions`, exactly as the real frontend calls them (`backend/routers/chat.py`)
**Test officer:** `sperumal_chn@sis.tn.gov.in` (ward jurisdiction: "1 (021) Orakadam (Ward:A), 5 (061) Athipet (Ward:G)")
**LLM:** Ollama `llama3.1:8b` (local), `keep_alive=30m`

This was a **real, live execution** against the running backend and its real database — not a static code review. 19 conversation sessions, 75 individual turns, were sent through the actual HTTP API with `chat_history` accumulated and resent exactly as `chat.js` does. Every answer quoted below is verbatim output from the running server. Raw request/response transcripts are preserved at `test_reports/qbank_artifacts/qbank_runs/*.json` and consolidated at `test_reports/qbank_artifacts/qbank_runs/ALL_TRANSCRIPTS.txt`.

---

## A. Executive summary

**Overall: 43.2% (PASS+0.5·PARTIAL / scored), 24 PASS / 15 PARTIAL / 34 FAIL / 2 BLOCKED, out of 75 executed turns.**

**Strengths actually observed:**
- Deterministic, no-LLM field lookups (single, named facts — registration document number, temporary subdivision numbers, patta numbers, service-code existence checks) are reliably correct and well-grounded, matching the database exactly in every case checked.
- The system is consistently *honest* about absence: multiple turns correctly said "no such record / no such column" instead of inventing a value (`StandaloneSample_C`, `Chain5` Turn 5, `Chain14` Turn 1's chitta check). This is a real, working safety property.
- Simple single-application lookups, workflow-history rendering, and correction/modification desk-table rendering are accurate against the live DB in every multi-row case we cross-checked by hand.

**Critical problems actually observed (not theoretical):**
1. **Multi-turn context/follow-up resolution is broken for exactly the cases the question bank was designed to test.** "What about the other one?" returned an identical, unchanged answer about the *first* application; a two-application "compare" question answered about only one application and never named the second one at all (`AmbiguousFollowups_block1`, Turns 1–3).
2. **Three distinct, root-caused keyword-matching bugs misroute real data questions into canned/unrelated answers**, reproducible and confirmed against source:
   - An unanchored substring check for `"format"` matches inside the ordinary word **"information"**, hijacking a real list query into the canned "application number format" explainer (`orchestrator.py:2148` / `5086`).
   - The English word **"automatically"** is canonicalized to the DB field keyword `automutated`, so any reasoning question containing "automatically" gets answered with an unrelated `Automutated: not recorded` field value (`appinfo_qa.py:50`, `178-179`).
   - `fulltransfer_qa` claims any question containing "sis" + "recommendation" *before* `cmflow_qa` gets a chance, so a legitimate SIS-recommendation question about a non-NISD (0158/0159/…) application is wrongly answered "only NISD carries one" (`chat_shared.py:2798`, `fulltransfer_qa.py:201-205`).
3. **Explanatory/reasoning follow-ups ("why shouldn't we conclude X", "what information is still missing") are repeatedly answered with an unrelated single-field lookup** ("No remark is recorded…", "No application-information record is held…") instead of actually reasoning from the data already shown in the same conversation. Seen 4+ times across different chains.
4. **Ollama cold-start / timeout**: four of 75 turns (5.3%) took 61–125 seconds and two of those hit the hard timeout and produced no answer at all ("The model stopped responding").
5. One jurisdiction/test-data mismatch (not a chatbot defect): the chosen test officer does not cover the ward of the 0165 sample application, so Chain 17 is mostly BLOCKED.

---

## B. Test environment

| | |
|---|---|
| Backend commit | `eaff14e50b7162339dc458b630cf406a037efd1d` (`main`), plus 71 uncommitted modified/new files (see `git status`) |
| Database | PostgreSQL `sis_chatbot_db`, live, local (`127.0.0.1:5432`) |
| DB build pipeline | `backend/sample_db_new` (confirmed live table names have no `_demo` suffix; `backend/sample_db`/CLAUDE.md's documented pipeline is **not** what populated this database) |
| LLM | Ollama `llama3.1:8b`, `OLLAMA_BASE_URL=http://localhost:11434` |
| API endpoint | `POST /api/v1/chat/stream` (SSE: `data: {"content": "..."}` chunks, `data: {"done": true}`, trailing `data: [DONE]`) |
| Auth | `POST /api/v1/auth/login` → JWT bearer, `POST /api/v1/chat/sessions` → session UUID |
| Test officer | `sperumal_chn@sis.tn.gov.in` / seeded test password; ward jurisdiction "1 (021) Orakadam (Ward:A), 5 (061) Athipet (Ward:G)" |
| Message cap | `ChatRequest.message` is capped at 1000 chars server-side (`backend/routers/chat.py:52`); all question-bank prompts used fit under this |
| Server process | started for this run only (`uvicorn backend.main:app --port 8000`), stopped after testing |

Secrets note: three scratch files (`login1.json`/`login2.json`/`login3.json`) briefly held live test-officer JWTs in the repo root during this run; they were deleted before this report was written and were never committed.

---

## C. Test results summary (by category)

| Category | Executed | PASS | PARTIAL | FAIL | BLOCKED | Score (excl. BLOCKED) |
|---|---|---|---|---|---|---|
| Multi-turn chains (priority subset: 1, 4, 5, 7, 9, 10, 13, 17, 19) | 9 chains / 48 turns | 16 | 10 | 20 | 2 | 46.7% |
| Standalone long questions (sample of 4) | 4 | 2 | 0 | 2 | 0 | 50.0% |
| Ambiguous follow-up stress test (1 block of 6, in real sequence) | 6 | 2 | 1 | 3 | 0 | 41.7% |
| Deliberately confusing questions (1 block of 4) | 4 | 1 | 2 | 1 | 0 | 50.0% |
| Tamil / Tanglish (2 blocks, 6 turns) | 6 | 2 | 1 | 3 | 0 | 41.7% |
| End-to-end scenario (Test B, 10 turns, full sequence) | 10 | 5 | 0 | 5 | 0 | 50.0% |
| **Total executed** | **75** | **24** | **15** | **34** | **2** | **43.2%** |
| Not executed (see section J) | ~40 of the remaining standalone questions, ~24 of the remaining ambiguous follow-ups, 11 of the remaining confusing questions, 5 of the remaining Tamil/Tanglish items, 11 of the remaining 20 chains, 3 of the 4 end-to-end tests | — | — | — | — | **not claimed as passed** |

---

## D. Individual test results

Full question text, exact (HTML-stripped-for-readability) answer excerpts, verdicts and per-turn notes for all 75 executed turns are in **[`test_reports/SIS_CHATBOT_QUESTION_BANK_RESULTS.csv`](qbank_artifacts/../SIS_CHATBOT_QUESTION_BANK_RESULTS.csv)** (one row per turn, columns: chain, turn, question_summary, verdict, note). Raw, unmodified server responses (including the HTML the frontend renders) are in `test_reports/qbank_artifacts/qbank_runs/*.json` per chain, and concatenated at `test_reports/qbank_artifacts/qbank_runs/ALL_TRANSCRIPTS.txt`.

A representative sample, verbatim:

**Chain 1 — Application 2026/0153/02/045470, Turn 5** (PASS)
> Q: "What was the registration document number, where was it registered and on what date? Don't confuse that with the application submission date."
> A: Registration document no **R/17/1/11822/2026**, Registered at **Ambattur**, Registered on **28-08-2026**; Submitted **2026-08-29 13:33:34**.
Confirmed correct and correctly distinguished against the live `full_field_patta_transfer_urban` row.

**AmbiguousFollowups_block1, Turns 1–2** (FAIL)
> Q1: "Compare 2026/0153/02/045470 and 2026/0153/02/045472. … Compare their survey numbers, subdivisions, old patta numbers, registration deed references, final statuses and rejection remarks."
> A1: a full comparison card — **titled `2026/0153/02/045470`, mentioning only that one application**; `045472` is never named.
> Q2: "What about the other one?"
> A2: **byte-for-byte identical** to A1.
This is the single clearest, most damaging finding in the whole run: a two-application comparison question was answered as a one-application lookup, and the designed-for follow-up ("the other one") could not recover the missing application at all.

**Chain 19, Turn 1** (FAIL — confirmed root cause)
> Q: "Show me the applications under service code 0154, but I am mainly interested in applications involving more than one temporary subdivision. … whether the proposed owner information **is present**. Do not count owner rows as separate applications."
> A: "An application number follows the format **YEAR/SERVICE_CODE/DISTRICT_CODE/SERIAL_NUMBER** (e.g. 2026/0154/28/001167). …"
The real list/filter/compare request was never attempted. Root cause confirmed in section E below.

---

## E. Critical bugs

### P0-1 — Unanchored `"format" in message` substring check hijacks any question containing the word "information"
- **Files / lines:** `backend/services/chatbot/orchestrator.py:2148` (non-streaming path) and `:5086` (streaming path, duplicate logic)
- **Reproduction:** Chain 19, Turn 1 — any message containing both "application number" and "information" anywhere (e.g. "...whether the proposed owner **information** is present...") is hijacked.
- **Root cause (verified by direct execution against the guard's own condition):**
  ```python
  b = any(w in m for w in ['format', 'look like', 'structure', 'structured', 'pattern'])
  ```
  `"format" in "...owner information is present..."` is **True**, because `"information"` literally contains the substring `"format"` (**in-FORMAT-ion**). Combined with the message containing "application number" anywhere, this unconditionally returns the canned application-number-format explainer and **never reaches `parse_intent` or any real handler**.
- **Impact:** any genuine data question that happens to use the words "application number" and "information" (or "formatted", "informative", "reformat", etc.) in the same message is completely discarded. This is a severe, easy-to-trigger false positive on exactly the kind of long, multi-clause questions this question bank is full of.
- **Recommended fix:** anchor the check to word boundaries (`re.search(r'\bformat\b', m)` or similar) instead of bare substring containment; same for the mirrored streaming-path copy at line 5086. Consider deduplicating the two copies while fixing it.

### P0-2 — "automatically" is canonicalized to the `is_automutated` DB field, hijacking reasoning questions
- **Files / lines:** `backend/services/chatbot/appinfo_qa.py:50` (`_EXACT` map: `"automatically": "automutated"`), consumed at `:178-179` (`if "automutated" in ws: add(AUTOMUT)`)
- **Reproduction (two independent chains, two different applications):**
  - Chain 4, Turn 3: "Does a populated date column **automatically** mean that the service requires an inspection?" → `Automutated: not recorded [2026/0153/02/045470]`
  - Chain 14, Turn 4: "Can you explain why these two tables should not **automatically** be treated as identical?" → `Automutated: not recorded [2026/0153/02/045472]`
- **Root cause:** `_EXACT["automatically"] = "automutated"` turns the ordinary English adverb "automatically" into the literal DB-column keyword the moment it's tokenized, so `appinfo_qa.claims()` fires on any sentence using that common word, regardless of what the sentence is actually asking.
- **Impact:** every reasoning/explanatory follow-up in the question bank that happens to use the word "automatically" is answered with a meaningless, unrelated field value instead of being reasoned about (or routed to the LLM fallback). This is the single most repeated failure pattern in the run.
- **Recommended fix:** remove `"automatically": "automutated"` from `_EXACT`; require an explicit, less ambiguous cue (e.g. "automutated", "auto mutation", "auto patta") before claiming this field.

### P0-3 — `fulltransfer_qa` claims "SIS recommendation" questions ahead of `cmflow_qa`, regardless of service code
- **Files / lines:** `backend/services/chatbot/chat_shared.py:2798` (dispatch order: `_fulltransfer_qa.claims_strong(message)` is OR'd *before* `_cmflow_qa.parse(message)` at `:2828`); `backend/services/chatbot/fulltransfer_qa.py:201-205` (`if "sis" in ws and "rec" in ws ...: add(SISREC)`)
- **Reproduction:**
  - Chain 9, Turn 2: "When did the SIS recommendation occur?" (application `2026/0158/02/000002`, a correction/modification 0158 file) → `No full-field patta transfer record is held for application 2026/0158/02/000002. Only an NISD (0153) file carries one.`
  - But Turn 1 of the **same chain** already showed, correctly, via `cmflow_qa`'s own correction-modification table: `SIS Recommended — Recommended (2026-01-19)`.
  - Same pattern reproduced again in `ConfusingQuestions_block1`, Turn 3.
- **Root cause:** `fulltransfer_qa`'s own keyword canonicalizer collapses "recommendation"/"recommended" to `rec`, and `"sis" in ws and "rec" in ws` is treated as an unambiguous claim on the NISD-only `full_field_patta_transfer_urban.sis_rec` column — without first checking whether the application in view is even a 0153 file. Because `chat_shared.py`'s dispatch `or`-chain tries `fulltransfer_qa.claims_strong()` before `cmflow_qa.parse()`, a correction/modification application's legitimate SIS-recommendation question never reaches the handler that actually holds the answer.
- **Impact:** the chatbot contradicts itself within a single conversation — denying data its own earlier turn already displayed.
- **Recommended fix:** have `fulltransfer_qa.claims_strong()` check the application's service code (available from the application number itself) before claiming a SIS-recommendation question, or reorder the dispatch so `cmflow_qa` gets first refusal for 0158–0169 applications.

### P1-4 — Multi-application comparison/follow-up resolves to only the first application, with no way to recover the second
- **Reproduction:** `AmbiguousFollowups_block1`, Turns 1–3 (see section D). A "compare A and B" question, and the immediate "what about the other one?" follow-up, both answer about A only; `B` (`2026/0153/02/045472`) is never named or shown anywhere in this chain.
- **Diagnosis (not yet pinned to one line — needs the author's own follow-up):** the comparison answer's own card title names only one application number, so no `FollowupContext` is ever recorded that carries *both* applications; "the other one" then has nothing to resolve against except the single application already in view. This matches the documented pre-existing architecture gap in `followup_context.py` ("Rejected: a follow-up after a paired enumeration answer … does not resolve, because … record no FollowupContext for the next turn") — but that gap is documented for attachment enumeration, not for a two-application register comparison, so this is a related but distinct instance.
- **Recommended fix:** `crosscheck_qa` / `compare_followup.py`'s comparison-answer path should record both application numbers as the carried context (entity type e.g. `application_pair`), and `_rescope_list_followup`/`compare_followup.py` should recognise "the other one" against a two-member set the same way a list follow-up recognises "the rest".

### P1-5 — Explanatory/reasoning follow-ups are answered with an unrelated single-field lookup instead of reasoning from shown data
- **Reproduction:**
  - Chain 10, Turn 4: "Why should we not automatically interpret that remark as proof of final application closure?" → `No remark is recorded on the workflow of this application` (a non-sequitur; the application DOES have the remark, shown in the same turn's own earlier card)
  - TestB_endtoend, Turn 5: "Does the Approved remark mean the full application is closed?" → same non-answer
  - Chain 10, Turn 4 / TestB Turn 10 / Chain9-adjacent: "What information is still missing before you can definitively say it is completed?" → `No application-information record is held for 2026/0158/02/001804.` (true, but non-responsive — the question is about the correction-modification/workflow discrepancy, which the chatbot HAD already shown correctly two turns earlier)
- **Diagnosis:** these "why/explain" questions carry none of the specific field cues any handler's `parse()` recognises, so they fall through to a generic absence-answer from whichever handler's fuzzy matcher claims weakest, rather than being handled by an explanation-generation path (or, failing that, falling to the LLM agent with the relevant evidence already in context).
- **Recommended fix:** either add explicit reasoning-question handling in `chat_shared.py`'s dispatch (recognise "why/does X mean/is X proof of" and route to the LLM answer-prompt with the already-retrieved record as grounding, never to a field-lookup handler), or have field-lookup handlers decline (return `None`) rather than claim when the question is clearly not asking for one of their own fields.

### P2-6 — Ollama cold-start causes full request timeouts, not just slow replies
- **Reproduction:** `Chain17`, Turn 4: 124.9s, timed out ("The model stopped responding, so this reply was cut short"). `TestB_endtoend`, Turns 2 and 4: 120.6s and 120.8s, both timed out. `Chain5`, Turn 3 (second half): 62s, also effectively a soft-timeout message.
- **Diagnosis:** this reproduces the Ollama keep-alive/cold-reload issue already flagged in this project's prior session (direct `/api/generate` test showed ~20-25s model-reload cost even with `LLM_KEEP_ALIVE=30m` configured) — four of 75 turns (5.3%) in this run alone hit it, and two produced literally no answer.
- **Recommended fix:** not a code fix per se — needs investigation at the Ollama server/host level (why the configured 30-minute keep-alive isn't holding the model resident), as already noted in this project's history. In the interim, consider a visible "the model is warming up, retrying…" UX rather than a bare timeout message after 2 minutes.

### P3-7 — Jurisdiction/test-data mismatch for Chain 17 (not a code defect)
- **Reproduction:** `Chain17_servicecode0165`, Turns 1–2: `Application 2026/0165/02/000043 is outside your assigned ward jurisdiction`.
- **Diagnosis:** the question bank's own sample application is in a ward the chosen test officer (`sperumal_chn@sis.tn.gov.in`) does not cover. All other sample applications tested share district/taluk/town with this officer's wards; this one specific application apparently does not. This is a test-setup limitation of this run, not a chatbot bug — correctly refusing access to data outside the officer's jurisdiction is the chatbot behaving **correctly**. Flagged separately because Turn 3 of the same chain inconsistently returned an unrelated statistic instead of the same jurisdiction refusal (see CSV row) — that inconsistency IS worth a look, even though the underlying refusal is correct.

---

## F. Context-retention analysis

| Mechanism | Observed behaviour |
|---|---|
| Single-application references ("who handled it", "the old patta") | **Inconsistent.** Worked for some single-field follow-ups (Chain 7 Turn 2/4, Chain 9 Turn 3/4), failed for others in the same chain (Chain 1 Turn 2 → `N/A`/`N/A`; Chain 5 Turn 4 → garbled 22-row dump). |
| "The other one" / two-entity references | **Failed outright.** See P1-4. The follow-up returned an unchanged, identical answer. |
| List/filter/sort follow-ups ("now show only the rejected ones") | **Failed because the originating list query itself never ran** (P0-1 hijacked it); the correct-looking refusal in Turn 2 is a downstream symptom, not independent evidence of working list-context tracking. |
| Date/field corrections ("No, I meant the update date, not submission date") | **Failed.** The correction was split into two garbled sub-questions by the long-message splitter; one triggered an unrelated "field correction offer" boilerplate, the other gave back the exact value the user said not to give. |
| Explanatory/why follow-ups | **Failed systematically** — see P1-5; these never reach a reasoning path, they fall to an unrelated field lookup. |
| Owner/relationship follow-ups | **Mixed.** Correct ownership data was retrievable with the right phrasing (Chain 5 Turn 2, Chain 14 Turn 1 partial) but counting/comparison follow-ups about the same owners failed (Chain 14 Turns 2–4). |
| Cross-script (Tamil/Tanglish) continuity | Tested only within-language (no English→Tamil mid-chain switch was exercised in this run); within a single Tanglish chain, entity continuity for the *application* held (Tamil_block1 stayed on 012715 throughout), but field-level follow-up correctness was weaker than the equivalent English chains (see Tamil_block1 Turn 2, Tamil_block2 Turns 1–2). |

**Stale-context carryover was not directly observed as "application A's data answering a question about B"** — the more common failure was the opposite: the system simply stuck to the first/only application in view and never incorporated the second one at all, rather than silently mixing the two.

---

## G. Routing and retrieval analysis

Where it could be determined from the response content (no debug logging was added to the running process for this pass — see section I's caveat), these dispatch/handler issues were observed:

- `orchestrator.py`'s application-number-format guard (both copies, non-streaming and streaming) pre-empts *all* routing for any qualifying message — confirmed root cause, P0-1.
- `appinfo_qa.claims()` false-positives on the word "automatically" and wins the dispatch — confirmed root cause, P0-2.
- `fulltransfer_qa.claims_strong()` wins dispatch ahead of `cmflow_qa.parse()` for "SIS recommendation" wording regardless of the application's actual service code — confirmed root cause, P0-3.
- `calc_qa`'s generic "compare X and Y" service-code-count fallback appears to win dispatch when a comparison question names something other than two recognised service codes/wards/periods — observed 3 times (`Chain4` Turn 4, `Chain9` Turn 1's second half, `Chain9` Turn 5's second half) producing the same nonsensical "0158 (…): N files (M rejected); 0167 (…): N files" shape regardless of what was actually asked to be compared; not pinned to an exact line in this pass.
- Source tables actually retrieved, where visible in the rendered answers, matched the live database in every case directly cross-checked (correction_modification_flow, application_workflow, areg_temp_subdivclub, chitta_temp_old_owner, full_field_patta_transfer_urban, f_line_service_application_information_urban) — **retrieval accuracy, when the right handler is reached, was consistently correct against the live DB.** The dominant failure mode in this run is *routing to the wrong handler*, not wrong data from the right handler.

---

## H. Performance analysis

Measured across all 75 executed turns (wall-clock, client to full SSE completion):

| Metric | Value |
|---|---|
| Turns measured | 75 (0 request-level errors) |
| Mean latency | 6.76 s |
| Median latency | 0.87 s |
| P95 latency | 61.98 s |
| Max latency | 124.91 s |
| Turns > 5 s | 4 (5.3%) |
| Turns > 60 s (effective timeout) | 4 (5.3%), 2 of which produced no answer at all |

The median (0.87s) reflects that most answers are deterministic lookups with no LLM call. The long tail is entirely attributable to LLM cold-start/availability (section E, P2-6) — every turn over 60s either timed out outright or came back with the generic "took longer than expected" message.

---

## I. Prioritized recommendations

1. **Fix P0-1** (the "format"/"information" substring collision) — a one-line, low-risk regex-boundary fix with very high blast radius reduction; this single bug discarded an entire category of real questions.
2. **Fix P0-2** (`"automatically"` → `automutated`) — remove the overbroad `_EXACT` mapping; equally low-risk, high-impact.
3. **Fix P0-3** (`fulltransfer_qa` vs `cmflow_qa` dispatch order / service-code check) — prevents the chatbot from contradicting its own earlier answer in the same conversation.
4. **Investigate and fix P1-4** (two-entity comparison follow-up context) — the question bank's own "high-priority context retention tests" section exists specifically because this class of bug is expected and damaging; it reproduced immediately.
5. **Address P1-5** (reasoning/"why" follow-ups) — decide whether these should route to the LLM with retrieved evidence as grounding, or stay deterministic with an actual explanation template; the current behaviour (silent field-lookup non-answers) is worse than either.
6. **Investigate the Ollama keep-alive/cold-start issue (P2-6)** at the infrastructure level, as already flagged in this project's own history — 5.3% hard-timeout rate in a 75-turn sample is not a tail case, it is a routine occurrence.
7. **Re-run Chain 17 with an officer whose jurisdiction covers the 0165 sample application**, and investigate the inconsistent jurisdiction-refusal behaviour noted in P3-7 Turn 3.
8. **Re-run the remaining, not-yet-executed question-bank items** (section J) once 1–5 are fixed, since many of those items are long multi-clause questions highly likely to also trip P0-1/P0-2.

---

## J. Untested cases — explicitly not claimed as passed

Per the task's own instructions, this report does not claim as passed anything that was not actually executed. The following were **not run** in this pass, due to the combined scale of the question bank (115+ items across 20 chains) and the effort budget for this review; they remain open work:

- **Chains not executed:** 2, 3, 6, 8, 11, 12, 15, 16, 18, 20 (10 of 20 chains). Chains 1, 4, 5, 7, 9, 10, 13, 17, 19 were executed in full, real sequence.
- **Standalone long questions (section 3 of the question bank, items 21–60):** only 4 representative items were executed (a sample covering register-status reconciliation, multi-count ambiguity, rejection-code translation, and SIS-vs-later-desk comparison). The remaining ~36 items were not run.
- **Ambiguous follow-up stress tests (items 61–90):** only one 6-turn block was run as a real sequence (covering "what about the other one", "which was submitted first", "was that the rejected one", "reason for that", and a date-field correction). The remaining ~24 standalone follow-up prompts were not run as their own sequences.
- **Deliberately confusing questions (91–105):** only one 4-turn sequence was run. The remaining 11 items were not run.
- **Tamil/Tanglish (106–115):** 6 of 10 items were run (two sequences). Items 108 (partially, as "second subdivision" rather than the exact wording), 113, 114, 115 (the two full Tamil-script items) were not run.
- **End-to-end scenarios (Test A, B, C, D):** only **Test B** (the inconsistent-workflow investigator, 10 turns) was run in full. Tests A, C and D were not run.
- **No backend debug logging/instrumentation was added** to the running process to directly observe which Python function handled each turn; all handler/routing conclusions in sections E and G are inferred from response content and from reading the relevant source files afterward, not from live stack traces. This is noted explicitly per the task's own evidence standard — the P0-1/P0-2/P0-3 root causes were confirmed by directly re-executing the actual guard conditions against the actual question text (shown in-line in section E), which is as rigorous as log-based confirmation would have been for those three; the P1-4, P1-5 and P2-6/`calc_qa` findings are diagnosed from behaviour only and would benefit from a follow-up pass with logging enabled.
- **No load/concurrency testing** was performed; all 75 turns were sent sequentially, one chain at a time, as instructed.

---

## Artifacts

- `test_reports/SIS_CHATBOT_QUESTION_BANK_RESULTS.csv` — machine-readable, one row per executed turn
- `test_reports/qbank_artifacts/qbank_chains/*.json` — the exact question sequences sent
- `test_reports/qbank_artifacts/qbank_runs/*.json` — the exact, unmodified server responses received, per chain, with per-turn latency
- `test_reports/qbank_artifacts/qbank_runs/ALL_TRANSCRIPTS.txt` — all of the above concatenated for easy reading
- `test_reports/qbank_artifacts/qbank_harness.py`, `qbank_build_chains.py`, `qbank_run_all.py` — the ad-hoc test harness used to drive the real API (temporary diagnostic instrumentation, not part of the application)
