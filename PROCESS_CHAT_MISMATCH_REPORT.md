# `process_chat()` vs `process_chat_stream()` — Mismatch Report

**File:** `backend/services/chatbot.py`
**Non-streaming:** `process_chat()` — lines 438–3623 (~3186 lines), served by `POST /api/v1/chat` (`routers/chat.py:114`)
**Streaming:** `process_chat_stream()` — lines 3624–6085 (~2462 lines), served by `POST /api/v1/chat/stream` (`routers/chat.py:182`)

Both functions implement the same pipeline but as two independent copies:

```
preamble → intent parse → jurisdiction gate → FETCH phase (intent → structured_data)
        → RAG context → HTML/direct-answer block → RESPONSE phase (structured_data → text)
        → save → return / yield
```

Every phase has drifted. Below is the complete inventory.

---

## 1. Preamble / gate mismatches

| # | Item | `process_chat` | `process_chat_stream` | Impact |
|---|---|---|---|---|
| P1 | `district_code` direct handler (lines 475–505) | Present — answers "district code of Chennai" from `DISTRICT_NAME_MAP` and returns early | **Absent** | Streaming falls through to the LLM and can hallucinate a district code |
| P2 | `_FIELD_VISIT_INTENTS` bypass set | does **not** contain `pending_applications` | **does** contain `pending_applications` | A block officer asking "pending applications in my ward" is denied on `/chat` but served on `/chat/stream` |
| P3 | Ward-officer (`_officer_level == 1`) keyword gate | checks `town` / `நகரம்` → denies town-level | **town check missing** | Ward officer can request town-level data on the streaming path only |
| P4 | Greeting vs jurisdiction gate ordering | jurisdiction gate first, greeting second | greeting first, jurisdiction gate second | No functional effect (`greeting` is in the bypass set and has no min level) — ordering drift only |
| P5 | `context_used` | computed (`len(rag_context) > 0`) and returned | **never computed** | `/chat` reports RAG usage; `/chat/stream` cannot |

---

## 2. FETCH-phase intents present in **only one** path

### Missing entirely from `process_chat_stream` (15 intents)

All are reachable from `parse_intent()` in `rag.py`, so each is a live gap — on the streaming path these queries fall through to the generic LLM branch with **no database data at all**.

| Intent | `process_chat` fetch site | `parse_intent` site |
|---|---|---|
| `jurisdiction_summary` | 1960 | `rag.py:2341` |
| `town_applications` | 2037 | `rag.py:2323, 2326` |
| `block_applications` | 2083 | `rag.py:2333` |
| `rejection_info` | 2129 | `rag.py:2475` |
| `taluk_summary` | 2168 | `rag.py:2482` |
| `litigation_check` | 2185 | `rag.py:2164` |
| `sale_deed_check` (alias arm of `["check_sale_deed","sale_deed_check"]`) | 2201 | `rag.py:2171` |
| `sd_additional_info` | 1452 group | `rag.py:1693` |
| `sd_encroachment_check` | 1452 group | `rag.py:1697` |
| `sd_sketch_readiness` | 1452 group | `rag.py:1701` |
| `sd_forward_check` | 1452 group | `rag.py:1705` |
| `sd_remarks` | 1452 group | `rag.py:1709` |
| `fv_date_select` | 1452 group | `rag.py:1824` |
| `fv_nearby_pending` | 1452 group | `rag.py:1830` |
| `fv_reschedule_availability` | 1452 group | `rag.py:1847` |

### Missing from `process_chat` fetch phase

None. (`process_chat` is the superset at fetch time.)

---

## 3. RESPONSE-phase branches present in **only one** path

| Intent | In `process_chat` | In `process_chat_stream` | Consequence |
|---|---|---|---|
| `application_status` | **no** response branch | yes (5682) | On `/chat`, an app-not-found result produces **no deterministic text** and is handed to the LLM |
| `officer_workload` | **no** response branch | yes (5734) | `/chat` sends workload counts to the LLM instead of formatting them |
| `isd_processing` | handled in the **fetch** phase (1360–1434) | handled in the **response** phase (5865) | Two different answer texts for the same question |
| `sd_additional_info` … `sd_remarks` (5) | yes (3186–3245) | **no** | Streaming has no answer text |
| `fv_date_select`, `fv_nearby_pending` | yes (3246–3264) | **no** | Streaming has no answer text |
| `fv_between_dates` | yes (3265) | **no** (folded into a generic group at 6218) | Different wording, loses the "needed to be visited" count |
| `survey_owners` | yes (3474) | **no** | Streaming sends owner rows to the LLM |
| Non-LLM fallback `"applications" in structured_data` → `"Found N application(s) (qtype)."` | yes (3543) | **no** | Streaming burns an LLM call for a case that has a deterministic answer |

---

## 4. Confirmed logic bugs / behavioural drift in shared branches

| # | Location | Defect |
|---|---|---|
| B1 | stream `highest_priority_applications` response (5514) | Reads `structured_data.get("apps", [])`, but the **fetch phase (4109) produces `applications`** (identical to `process_chat`). The key never exists → the answer is *always* "No high priority applications found." even when rows were fetched. |
| B2 | `process_chat` `immediate_action` response (3070) | Mirror-image bug: reads `apps`, but its fetch (1123) produces rich `applications` rows → answer is *always* "No applications require immediate action today." Meanwhile the **stream** fetch (4145) produces only `apps` (bare number strings), so `_build_table_data` (which reads `applications`, line 6327) renders **no table** on the streaming path. Each path is broken in the opposite half. |
| B3 | `process_chat` line 1654 | In the `sd_*/fv_*` group, `if not a:` sets `found: False` at 1648 but execution **falls through** to `visit_stmt = select(FieldVisit).where(FieldVisit.application_id == a.id)` at 16-space indent → `AttributeError: 'NoneType' object has no attribute 'id'`. Any `sd_*`/`fv_*` query naming a non-existent application raises and returns the generic "I encountered an error" message. |
| B4 | stream `overdue_applications` (3954) | `extract_date_range(message)` is **not called**; `start_date`/`end_date` are never passed to `get_overdue_applications`, and the range is missing from `query_type`. `process_chat` (857) does both. "Overdue applications between 1 Jan and 31 Mar" silently ignores the range when streaming. |
| B5 | stream `overdue_applications` (3954) | Type detection is `"isd" in msg` / `"nisd" in msg` only. `process_chat` also accepts the service codes `0154` (ISD) and `0153` (NISD). |
| B6 | stream `active_applications_taluks` (4081), `assigned_today` (4131), `immediate_action` (4145), `pending_longest` (4450) | The filter `Application.current_stage == officer.officer_stage` is **missing**. `process_chat` applies it, and `postgres.py` applies it everywhere (lines 149, 423, 605, 700, 1320, 1401 — annotated "CRITICAL FIX: Filter by stage"). Streaming counts applications sitting at other desks (SD/DIS/Tahsildar) as the officer's own. |
| B7 | `process_chat` `fv_reschedule_availability` response (3301) | `structured_data.get("reschedule_date")` with no default → renders literally `"Schedule available on None."` when the fetch group did not run. Stream has a sensible default. |
| B8 | stream `is_nisd_or_isd`/`check_documents`/`check_sale_deed` fetch (4466) | The "no application number given" case returns English-only `"Please provide an application number"`. `process_chat` (1342) emits a **Tamil/Tanglish-aware** prompt with an example. Language regression on the streaming path. |
| B9 | `process_chat` `fv_recently_rescheduled` response (3440) | No zero/singular forms → "0 field visits were rescheduled". Stream has all three forms. |
| B10 | `_field_keywords` (2392 vs 4881) | The two lists differ: `process_chat` has `"form 6"`, `"declared reason"`, `"declared_reason"`; stream has `"சர்வே எண்"`. `_asking_specific_field` → `_bypass_html` → whole direct-answer path diverges for those phrasings. |
| B11 | direct-answer block, `app_no` resolution (2461 vs 4952) | `process_chat`: `sd.get("application_number") or sd.get("application_id") or extract_application_number(message) or ""`. Stream: `sd.get("application_number", "")` only → blank application number in streamed direct answers when `structured_data` uses `application_id`. |
| B12 | `pending_applications` group query_type (671 vs 3773) | `process_chat` has an `elif taluk_name:` arm producing `"Applications in {taluk}…"`. Stream omits it → wrong table heading for taluk-scoped queries. |
| B13 | `application_status` fetch (1882 vs 4496) | Stream supports **multi-application** queries (`extract_application_numbers`, `multi_applications`); `process_chat` does not. `process_chat` supports **workflow/history/timeline** sub-queries (`WorkflowHistory` → `structured_data["history"]`); stream does not. Each path answers a question the other cannot. |
| B14 | `fv_deadline_check` response (3305 vs 5831) | `process_chat` defaults `days_overdue` to `working_days - 15` and `days_remaining` to `15 - working_days` (both can go **negative**); stream clamps with `max(0, …)`. Also different "please specify" wording. |
| B15 | `fv_overdue_inspections` | `process_chat` runs the query **twice** — once at 895 (no jurisdiction filter, emits `overdue_count` + `field_visit_date`/`application_type` keys) and again at 3326 in the response phase (jurisdiction-filtered, emits `overdue_visits_count` + `scheduled_date`/`type`). The second overwrites the first, so the result is correct but one full query is wasted per request. Stream does it once, in the fetch phase. |
| B16 | `fv_scheduling_conflicts` response (3444 vs 5799) | Different text; `process_chat` hardcodes "between 10:00 AM and 11:00 AM" which is not derived from any data. |
| B17 | `highest_priority_applications` (3451) and `fv_overdue_inspections` second copy | `process_chat` has a **second, unreachable** `highest_priority_applications` response branch at 3451 (dead code, contains the only Tamil-localised version). Stream has a matching dead nested branch at 5936. |

---

## 5. Wrong / invalid application-number handling (explicit requirement)

Current behaviour when the officer supplies a bad application number:

| Case | `/chat` (`process_chat`) | `/chat/stream` | Desired |
|---|---|---|---|
| Well-formed but non-existent, `application_status` | `get_application_detail` returns `found: False` + `message` + `suggestions`; the direct-answer block is gated on `found` (2457) and **no `application_status` response branch exists** → falls to `call_llama` with no data | 5699 renders `structured_data["message"]` correctly | Both must state "Application X was not found" and offer the suggestions |
| Well-formed but non-existent, `sd_*` / `fv_*` intents | **crashes** (B3) → generic error text | intent not implemented at all (§2) | Both must say not-found |
| **Malformed** (`APP-24-1`, `2026/0153/31`, `APP2024000001`, `app no 1234`) | `extract_application_number` returns `None` → treated as "no application number mentioned" → silently answers about a *different* application or asks nothing | same | Must say the format is invalid and show the accepted formats |
| No number at all and no context | some intents prompt, others silently pick `Application.created_at.desc()` first row (1633, 4855) or hardcode `"APP-2024-000001"` | same | Should prompt for a number |

There is currently **no format validation anywhere** — `rag.py:2528` only matches valid shapes and returns `None` otherwise, and neither path distinguishes "user gave nothing" from "user gave something that is not a valid application number".

---

## 6. Remaining duplication risks

- The two functions are ~5,600 lines of near-copy with no shared helper. Every intent must be written twice, in two phases each — four edit sites per feature.
- `structured_data` is an untyped free-form dict; the producer and consumer of each key live hundreds of lines apart, in different functions. B1/B2 are direct consequences.
- Response text is authored inline in both paths, so wording drifts silently (B9, B14, B16).
- Dead/unreachable `elif` arms already exist in both (B17); the ordering of the `elif` chains differs between paths, so adding a branch in one place can shadow a different branch than it does in the other.

---

# Part 2 — Fixes applied

All changes are confined to `backend/services/chatbot.py`. No files moved, no architecture changed.

## 7.1 New shared helpers (module level, above `process_chat`)

| Helper | Purpose |
|---|---|
| `detect_invalid_app_number(message)` | Returns the offending token when the officer clearly attempted an application number but used an unparseable format; `None` otherwise. Guarded against dates (`01/02/2026`, `1/1/2026`), year spans (`2024/2025`) and year/month (`2026/03`). |
| `build_invalid_app_number_message(token, language)` | Localised (en / ta / tanglish) "that number is not a valid format" reply listing the three accepted shapes. |
| `build_app_not_found_message(structured_data, language)` | Localised "you entered a wrong application number / it is outside your jurisdiction" reply, appending the `suggestions` list `get_application_detail()` already returns. |
| `build_isd_processing_answer(message, structured_data, app_number)` | The full 7-case ISD answer logic, extracted from `process_chat`'s fetch phase and now called by **both** response phases. |

Both paths gained a **Step 2d** gate (after the jurisdiction check, before any DB query) that rejects a malformed application number for the 19 intents in `_APP_NUMBER_INTENTS`.

## 7.2 Wrong / invalid application number — behaviour now

| Case | Both paths now |
|---|---|
| Malformed (`APP2024000001`, `2026/0153`, `ISD/W1/2024`) | "The application number you entered — **X** — is not in a valid format", plus the three accepted formats, in the officer's language. No DB query is run. |
| Well-formed but non-existent | "The application number you asked for — **X** — was not found. You have entered a wrong application number, or it is outside your jurisdiction.", followed by the near-match / in-jurisdiction suggestions. |
| Multi-app query where some are missing | Details for the found ones plus "Not found: X, Y." |
| No number and none in context | Localised "Please specify which application you're asking about. For example: APP-2024-000001". |

`searched_number` is now attached to every `found: False` application payload so the message builder can name the number.

## 7.3 Fix list

**Preamble / gate**
- P1 — ported the `district_code` direct handler into `process_chat_stream`.
- P2 — removed `pending_applications` from the stream's jurisdiction-bypass set.
- P3 — added the missing ward-officer `town`/`நகரம்` denial to the stream.
- P5 — the stream now computes `context_used`.

**Missing branches**
- Ported all **15** missing fetch-phase intents into the stream: `jurisdiction_summary`, `town_applications`, `block_applications`, `rejection_info`, `taluk_summary`, `litigation_check`, `sale_deed_check`, the five `sd_*` checks, `fv_date_select`, `fv_nearby_pending`, `fv_reschedule_availability`.
- Ported the missing response branches into the stream: the five `sd_*`, `fv_date_select`, `fv_nearby_pending`, `fv_between_dates`, `survey_owners` (and removed `fv_between_dates` / `survey_owners` from the generic table-intro tuple so the specific branches are reachable).
- Added to `process_chat`: `application_status`, `officer_workload`, `isd_processing` response branches, and the generic table-intro group the stream already had.
- Added to the stream: the deterministic `"Found N application(s) (qtype)."` fallback ahead of the LLM.

**Logic bugs**
- B1 — stream `highest_priority_applications` read `apps`; the fetch emits `applications`. Now reads `applications` (was always answering "none found").
- B2 — `immediate_action`: the stream fetch now builds the same rich `applications` rows as `process_chat` (so the table renders), and both emit `apps` alongside.
- B3 — `process_chat` no longer falls through to `a.id` on `a is None` in the `sd_*`/`fv_*` group (the body is now correctly nested under `else:`).
- B4/B5 — stream `overdue_applications` now passes `start_date`/`end_date` from `extract_date_range()` and accepts service codes `0153`/`0154`.
- B6 — added `Application.current_stage == officer.officer_stage` to the stream's `active_applications_taluks`, `assigned_today`, `immediate_action`, `pending_longest` (matching `process_chat` and `postgres.py`).
- B7 — `process_chat` `fv_reschedule_availability` no longer renders "Schedule available on None."
- B8 — the stream's "no application number" prompt is Tamil/Tanglish-aware.
- B9 — `process_chat` `fv_recently_rescheduled` gained the zero/singular forms.
- B10 — the two `_field_keywords` lists are now the union (`form 6`, `declared reason`, `declared_reason`, `சர்வே எண்`).
- B11 — the stream's direct-answer `app_no` uses the same fallback chain as `process_chat`.
- B12 — the stream gained the `elif taluk_name:` `query_type` arm.
- B13 — multi-application support ported into `process_chat`; workflow/history support ported into the stream.
- B14 — `process_chat` `fv_deadline_check` clamps with `max(0, …)` and uses the same "specify an application number" wording.
- B15 — `process_chat` no longer runs the overdue-inspections query twice; the jurisdiction-filtered query lives in the fetch phase and the response phase just reads the count.
- B16 — `fv_scheduling_conflicts` no longer claims an invented "10:00–11:00 AM" window; both use the same text.

## 7.4 Verification performed

- `ast.parse` clean.
- Symmetry check against the 62 intents `parse_intent()` can return: **62/62 symmetric** in both the fetch phase and the response phase (was 15 fetch gaps + 12 response gaps).
- Scope analysis (AST) of both functions: no undefined names, and no use-before-assignment in any ported block.
- `detect_invalid_app_number` unit-checked against 16 cases including dates, year spans, year/month and all three valid formats — 0 failures.
- `build_isd_processing_answer`, `build_app_not_found_message`, `build_invalid_app_number_message` exercised standalone against representative payloads.
- **Not run**: the live suites (`test_comprehensive_suite.py`, `backend/test_200_suite.py`) — the project venv is a Windows venv (`.venv/Scripts`) and these need a running backend, PostgreSQL and Ollama, none of which are available in this environment.

## 7.5 Remaining cosmetic drift (not functional, left alone)

- Variable naming (`wd_count`/`wd`, `sub_date`/`sub_date_str`, `approaching_apps`/`approaching`).
- `if structured_data else` defensive guards present in the stream but not in `process_chat` (both initialise `structured_data = {}`, so unreachable).
- One dead `highest_priority_applications` response arm in each path (inside the generic table-intro group; the live branch is earlier in the chain and is identical in both).
- An unused `ov_apps` local in `process_chat`'s `escalation_check`.
