# Security, Runtime Defects and Stale Documentation — Report (2026-11-28)

Covers the requests that arrived during P3.4: the chat.js XSS bypass, stale documentation, and four
runtime defects observed against the running server. P3.4 itself is in
`P3_4_HISTORICAL_PAIR_REFERENCE_REPORT.md`.

## 1. Frontend XSS via the `hasHTML` bypass (high)

**Defect.** `formatBotMessage()` (chat.js) returned any text containing a tag *as-is* into `innerHTML`.
Script, `img onerror`, `svg onload` or a `javascript:` link in an LLM answer or a quoted record value
would execute.

**Fix.** `formatBotMessage()` now runs every branch through `sanitizeBotHtml()`, an allowlist built from
what the backend actually emits. An inventory of `backend/services` found:
- tags: table / div / span / strong / a / button / li / …;
- attributes: class, style, colspan, and `href='javascript:void(0)'`;
- exactly three handler shapes: `window.copyTableData(this)`, `window.handleAppClick('<app no>')` and
  `handleApplicationClick('<app no>'); return false;`.

What the sanitizer keeps and drops:

| input | result |
|---|---|
| tags and attributes in that inventory | kept |
| script / style / iframe / svg / img / form | dropped, content included |
| unknown tags | unwrapped (text kept) |
| `on*` attributes | removed unless one of the three exact handler shapes |
| `href` | removed unless `#` or `javascript:void(0)` |
| `style` | removed if it can load or run anything |

- Parsing happens in an inert `<template>`, so nothing runs before cleaning.
- The two doc comments claiming "trusted backend source" were corrected.
- `chatbot.html` cache-buster bumped (`chat.js?v=20261128_001`).

**Verified.** `tests/frontend/test_sanitize_bot_html.html` loads the real `chat.js` and passes 16/16 in the
browser pane, including "no payload executed". The legitimate backend HTML (application links, tables,
copy button, plain-text cards) renders unchanged. To re-run it: launch config `sis-static-tests`, then
`/tests/frontend/test_sanitize_bot_html.html`.

## 2. Unauthenticated exposure: `channel_report.html` and the stubbed listing (high)

**Defect.**
- `frontend/channel_report.html` embedded **209 application records with applicant names, mobile
  numbers, CAN numbers and gender** (`const RAW = [...]`, 190 KB). It was readable without login on
  both the frontend server (3000) and the backend's `/frontend` mount.
- The snapshot was also stale (district `28`, the superseded seed).
- `GET /api/v1/applications/` was a Phase-2 TODO stub returning `[]` for every officer.

**Fix.**
- **The endpoint** now returns the caller's own register: every service code, via
  `get_all_service_code_applications` (jurisdiction-scoped). It supports `status`, `stage`,
  `application_type` (ISD, NISD or a 4-digit code; anything else → 422), `skip` / `limit`, and a
  `total`. JWT required, as before.
- **The report page** no longer embeds any record. It loads `js/config.js` (the API base) and fetches
  the listing with the officer's JWT; with no token, or on 401/403, it goes to `login.html`. It keeps the
  ISD/NISD rows it was designed for.
- The per-applicant columns (name, mobile, CAN, fee, …) are not in the listing, so they now show empty.
  Restoring them should be a deliberate decision about which personal fields an officer's report may show.

**Verified live** (server on 127.0.0.1:8001, `arun_m@`):

| check | result |
|---|---|
| with JWT | 200, total 152, `"Applications retrieved"` |
| `status=pending` | total 47 (the register's coarse open status; your "45 active" figure likely counts a narrower set) |
| no JWT | **401** |
| `/frontend/channel_report.html` | 200, 58 KB, 0 application numbers, 0 mobile-like numbers |

Unit tests: `tests/unit/test_applications_endpoint.py` (7).

## 3. Register-only (0158 …) two-application comparison collapsed (correctness)

**Defect (reproduced).** "Compare 2026/0158/02/000002 and 2026/0158/02/000003" returned only 000002's
card, and "what about the other one?" repeated it.

**Causes** (three, all on the shared path):
1. The gate (`resolve_application_reference`) answered the **first** number's register card before any
   comparison ran.
2. The comparison's per-side reader (`_side()`) used `get_application_detail()`, which covers only the
   ISD/NISD `applications` table.
3. The register-only card saved its follow-up context **without** `previous_context`, dropping the pair.

**Fix.**
1. A message naming two or more applications passes the gate as a comparison (after the first number's
   jurisdiction check).
2. `_side()` falls back to the jurisdiction-checked register row.
3. Both register-only context saves pass `previous_context`.

**Verified live (`/chat` and `/chat/stream`):**
- compare → both files;
- "the other one" → 000003;
- "what is its status?" → 000003, pair kept;
- "show both applications again" → both.

Tests: `tests/unit/test_register_only_comparison.py` (6; mixed ISD + 0158, a missing second number,
stream = non-stream).

## 4. Telemetry (correctness)

**Defect.**
- `model_used` was a hardcoded `"llama3.1:8b"`.
- `record_llm_call` / `record_retrieval` / `record_tool_call` had **no callers**, so every duration and
  token count was 0.
- `/chat/stream` created no metrics at all.

**Fix.**
- `model_used` = `settings.LLM_MODEL`.
- A request-scoped `current_metrics` ContextVar, plus no-op-outside-a-request recorders, timed at the
  real choke points:
  - `llm_gate.llm_slot()`, inside the slot, which covers `call_llama`, the stream, the agent and query
    understanding;
  - the greeting call, which is not gated;
  - `pgvector_store.similarity_search`;
  - `agent_tools.execute_tool`, by tool name;
  - Ollama token counts from `usage_metadata` in `call_llama`.
- `/chat/stream` builds the same metrics and finalises exactly once when the stream ends, is abandoned
  (`client_disconnected`) or is cancelled.

**Verified live.**
- Six deterministic turns produced six records, two of them from `/chat/stream`, with 0 ms LLM time,
  which is correct for them.
- A greeting on each endpoint recorded `llm_duration_ms` ≈ 6,020 ms with `model_used: sis-assistant:latest`.

Tests: `tests/unit/test_request_telemetry.py` (7).

**Not fixed:**
- The stream path's record says `intent: "stream"` unless a chunk carries an intent; the stream
  pipeline doesn't emit one.
- Embedding time for semantic routing is not counted as retrieval.

## 5. Ollama runs 100% on CPU (`size_vram: 0`) — environment, not code

Ollama 0.40.19's startup log (`%LOCALAPPDATA%\Ollama\server.log`) shows why:
- **AMD ROCm:** "AMD driver is too old", and the Radeon 740M (`gfx1103`) is not in its ROCm target list.
- **Vulkan:** the 740M was seen and dropped as an integrated GPU ("set OLLAMA_IGPU_ENABLE=1 to enable").
- **NVIDIA/CUDA:** no device found. `nvidia-smi` is installed, but Windows lists no NVIDIA adapter.
- Result: `inference compute … library=cpu`, total 15.3 GiB, 4.9 GiB available.
- `LLM_NUM_CTX=16384` (`config.py`) adds a 2 GiB KV cache.

Options, all your call because they change the machine or the model's behaviour:
- update the AMD driver;
- try `OLLAMA_IGPU_ENABLE=1`: the iGPU shares system RAM, so the gain is uncertain;
- lower `LLM_NUM_CTX` (less RAM, faster, but long prompts get truncated);
- run on a machine with a supported discrete GPU.

Nothing was changed here.

## 6. Stale documentation

Each document's file and command references were checked against the tree with a repo-wide filename
index, sorted into ok / moved / absent; then each was fixed.

* **CLAUDE.md**
  - `chatbot.py` → the `backend/services/chatbot/` package (30 mentions);
  - 26 module paths moved into the package; the structure tree rewritten from the real tree;
  - Dev Commands and Testing now show `pytest tests/unit`, which replaces ~45 deleted scripts.
    Feature sections that cite a deleted script mark it *(removed)*, or point to the `tests/unit`
    file where one exists;
  - officer logins corrected (`arun_m@` / `marun_chn@` / `sperumal_chn@`, checked against the DB);
  - LoRA script paths point to `C:\lora_data\…`, where they actually are;
  - new section "Conversation context is server-side (P3–P3.4)", a sanitizer rule under Frontend, and
    the application-identity gotcha rewritten;
  - the seed-data sections are marked as describing the superseded `sample_db` seed. Their counts were
    not re-verified against the live DB, and the file says so.
* **AGENTS.md:** the same structural fixes.
* **SKILL.md:** the "add an intent" steps now name the two dispatch modules; the test, setup, reset and
  integrity commands replaced with ones that exist.
* **README.md:** a Testing section added; the frontend listing corrected. `config.js` is generated by
  `serve_frontend.py` and kept, with that note.

Line endings were preserved (CLAUDE.md and README.md CRLF; AGENTS.md and SKILL.md LF), and every diff is
content-only.

## 7. Git safety and environment notes

* Branch `main`, HEAD `eaff14e` unchanged. Nothing staged, committed, stashed, reset, cleaned or discarded.
* A backend I did not start was listening on `0.0.0.0:8000` (PID 36800). I left it alone and ran every
  live check against my own server on `127.0.0.1:8001`, which is now stopped. **That process is still
  running the code it loaded at 07:38**, so it has none of these fixes until you restart it.
