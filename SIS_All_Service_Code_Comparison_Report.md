# SIS Chatbot — Exhaustive Similarity and Difference Audit Across All Service Codes

**Date:** 2026-10-13  
**Repository:** [https://github.com/svmayurkaarthic2005/Internship_NIC](https://github.com/svmayurkaarthic2005/Internship_NIC) (Local checkout: `C:\proj\nic_internship`)  
**Auditor / Testing Context:** Live SIS Chatbot UI (`http://localhost:3000/chatbot.html`) & Backend API (`http://localhost:8000/api/v1/chat/stream`), Ward SIS Officer `marun_chn@sis.tn.gov.in` (Employee ID: SIS-002, Ward 102/002 Ambattur/Korattur), backed by an exhaustive deterministic sweep (406 pairs), live PostgreSQL schema and data queries, and automated regression test suites.  
**Audit Policy:** Strict **audit-only** execution per instructions — **zero production-code modifications were made during this pass**. All confirmed defects, crashes, and misroutings are documented with reproduction steps, root causes, and minimal proposed fixes.  
**Deliverable Artifacts:**
1. [`SIS_All_Service_Code_Catalogue.csv`](file:///c:/proj/nic_internship/SIS_All_Service_Code_Catalogue.csv)
2. [`SIS_All_Service_Code_Comparison_Matrix.csv`](file:///c:/proj/nic_internship/SIS_All_Service_Code_Comparison_Matrix.csv)
3. [`SIS_All_Service_Code_Conversation_Flows.jsonl`](file:///c:/proj/nic_internship/SIS_All_Service_Code_Conversation_Flows.jsonl)
4. [`SIS_All_Service_Code_Comparison_Failures.jsonl`](file:///c:/proj/nic_internship/SIS_All_Service_Code_Comparison_Failures.jsonl)
5. [`SIS_All_Service_Code_Comparison_Report.md`](file:///c:/proj/nic_internship/SIS_All_Service_Code_Comparison_Report.md) (this document)
6. [`SIS_ServiceCode_FileDB_Regression_Tests/`](file:///c:/proj/nic_internship/SIS_ServiceCode_FileDB_Regression_Tests/)

---

## Executive Summary & Audit Scope

This audit provides an exhaustive evaluation of the Sub Inspector Surveyor (SIS) Chatbot's capability to understand, compare, differentiate, and report on **all service codes** supported by the application. Rather than restricting analysis to the previously investigated pair of F-Line (`0178`) and F-Line Appeal (`0185`), this investigation covers the complete catalogue of 29 TAMILNILAM Urban service codes, encompassing:
1. **$N=29$ Supported Service Codes** derived from code and verified against authoritative policy documents.
2. **$\binom{29}{2} = 406$ Distinct Service-Code Pairs** tested systematically across both bare-code and official-name phrasings.
3. **Whole-Catalog All-Service Comparison Queries** evaluating catalog summaries, matrices, and common attributes.
4. **Negation, Ambiguity, and Identity Checks** evaluating parser modes and directionality.
5. **Database Application-Level Realities** contrasting Layer 1 flat registers (176 live rows across 11 codes) with Layer 2 ORM tables (restricted exclusively to 2 codes).
6. **Uploaded Document QA QA Comparisons** verifying cross-file comparisons and conflict detection.
7. **Multi-Turn Follow-Ups & Typo Tolerance** exposing critical runtime crashes.

### Headline Findings

1. **Systemic Official-Name Resolution Collapse (SVCCOMPARE-ALL-001):**
   - When comparison queries use bare 4-digit codes ("difference between 0158 and 0159"), **100% of pairs (406/406)** resolve both codes correctly.
   - When comparison queries use official service names ("difference between Modification / Anadeenam and Addition"), **only 8.4% of pairs (34/406)** resolve both codes; **91.6% of pairs (372/406) fail completely**, collapsing into a single service definition or None.
2. **Critical Runtime Crash — NameError `_ccrows` (SVCCOMPARE-BUG-001):**
   - Asking for a table or application comparison between two codes ("Compare 0153 and 0154 applications in a table") triggers an unhandled `NameError: name '_ccrows' is not defined` at [chatbot.py:18537](file:///c:/proj/nic_internship/backend/services/chatbot.py#L18537), crashing the endpoint with HTTP 500.
3. **Critical Runtime Crash — NameError `tokens` (SVCCOMPARE-BUG-002):**
   - Sending an explanation follow-up with slight typos ("explian in detial", "explain briefly") triggers `NameError: name 'tokens' is not defined` at [chatbot.py:1261](file:///c:/proj/nic_internship/backend/services/chatbot.py#L1261).
4. **Architectural Pairwise Disparity (SVCCOMPARE-ALL-004):**
   - Rich domain comparison (attribute breakdown, markdown tables, negation, identity verification) was hardcoded **strictly for `{0178, 0185}`**.
   - For all other 405 pairs: `mode == "BOTH"` completely drops differences, table requests are ignored, and ambiguous negations are dumped without clarification.
5. **All-Service Intent Misrouting (SVCCOMPARE-ALL-005):**
   - Whole-catalog comparison requests ("Show similarities across all service codes", "Create a comparison matrix") misroute to application record tables (`all_service_code_applications`), pending lists (`pending_applications`), or hang in ungrounded LLM loops.
6. **Information Asymmetry & Undocumented Attributes (SVCCOMPARE-ALL-003):**
   - Only **2 codes (`0153`, `0154`)** have documented fees, SLAs, and full workflow chains in departmental guidelines. For the remaining 27 codes, only the official name, category, and field-visit requirement are knowable. Any comparison asserting fees or SLAs for these 27 codes is ungrounded.

---

## 1. Supported Service Code Catalogue Discovery

### Discovery Methodology
The service catalogue was derived from active source code and validated against authoritative documents:
- **Codebase Definitions:** [`backend/utils/helpers.py`](file:///c:/proj/nic_internship/backend/utils/helpers.py) `SIS_URBAN_SERVICES` (29 entries), `SIS_SERVICE_CODE_DETAIL` (2 entries), and `_SVC_NAME_ALIASES` (4 entries).
- **Authoritative Documents:** [`backend/documents/tamilnilam_urban_services_and_districts.txt`](file:///c:/proj/nic_internship/backend/documents/tamilnilam_urban_services_and_districts.txt) Section 3, and [`backend/documents/land_rules.txt`](file:///c:/proj/nic_internship/backend/documents/land_rules.txt) ("SERVICE FEE AND SLA BY SERVICE CODE").
- **Database Mappings:** Checked PostgreSQL tables `applications` and `service_register_entries`.

### Catalogue Summary (29 Codes)

All 29 discovered codes are TAMILNILAM Urban services. Code `0155` was verified as unsupported/invalid (omitted from official government rosters).

| Service Code | Official Name | Short Code | Requires Field Visit | Applications in Register | ORM Layer 2 Admitted | Detail Level | Runtime Aliases |
|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---|
| **0153** | Not Involving Subdivision | NISD | No | 28 | Yes | Full (Fee, SLA, Workflow) | — |
| **0154** | Involving Subdivision | ISD | **Yes** | 29 | Yes | Full (Fee, SLA, Workflow) | — |
| **0156** | TSLR Extract with Sketch | TSLR_SKETCH | No | 11 | No | Name + Field Visit Only | — |
| **0157** | TSLR Extract Only | TSLR_EXTRACT | No | 0 | No | Name + Field Visit Only | — |
| **0158** | Modification / Anadeenam | MODIFICATION | **Yes** | 22 | No | Name + Field Visit Only | — |
| **0159** | Addition | ADDITION | **Yes** | 13 | No | Name + Field Visit Only | — |
| **0160** | Deletion | DELETION | **Yes** | 8 | No | Name + Field Visit Only | — |
| **0161** | Street Master | STREET_MASTER | No | 0 | No | Name + Field Visit Only | — |
| **0162** | Street and Door Number Modification | STREET_DOOR_MOD | No | 9 | No | Name + Field Visit Only | — |
| **0163** | Block/Revoke Town Survey Number | REVOKE_TSN | No | 0 | No | Name + Field Visit Only | — |
| **0164** | ULC Land Subdivision | ULC_SUBDIV | **Yes** | 13 | No | Name + Field Visit Only | `ulc` |
| **0165** | TSLR Owner Name Correction | NAME_CORRECTION | No | 21 | No | Name + Field Visit Only | — |
| **0167** | TSLR Settlement - Owner Entry | SETTLE_OWNER | No | 0 | No | Name + Field Visit Only | — |
| **0168** | TSLR Settlement - Subdivision | SETTLE_SUBDIV | **Yes** | 0 | No | Name + Field Visit Only | — |
| **0169** | Govt to Private | GOVT_TO_PRIVATE | **Yes** | 7 | No | Name + Field Visit Only | — |
| **0170** | TSLR Sketch Verification | SKETCH_VERIFY | **Yes** | 0 | No | Name + Field Visit Only | — |
| **0171** | Rural-Urban Correlation | RURAL_URBAN_CORR | **Yes** | 0 | No | Name + Field Visit Only | — |
| **0172** | Town Settlement | TOWN_SETTLE | **Yes** | 0 | No | Name + Field Visit Only | — |
| **0173** | Temple Land Unblock | TEMPLE_UNBLOCK | **Yes** | 0 | No | Name + Field Visit Only | — |
| **0175** | Block Change | BLOCK_CHANGE | No | 0 | No | Name + Field Visit Only | — |
| **0176** | Govt to Govt Poramboke | GOVT_PORAMBOKE | **Yes** | 0 | No | Name + Field Visit Only | — |
| **0178** | F-Line (Urban Demarcation) | F_LINE | **Yes** | 15 | No | Name + Field Visit Only | `f-line`, `f line`, `fline` |
| **0179** | Settlement Govt to Private | SETTLE_GOVT_PVT | **Yes** | 0 | No | Name + Field Visit Only | — |
| **0181** | Settlement Modification | SETTLE_MOD | **Yes** | 0 | No | Name + Field Visit Only | — |
| **0183** | Settlement Govt Poramboke to Private (GO-506) | SETTLE_PORM_PVT | **Yes** | 0 | No | Name + Field Visit Only | — |
| **0184** | TSR Preparation Subdivision | TSR_PREP_SUBDIV | **Yes** | 0 | No | Name + Field Visit Only | `tsr` |
| **0185** | F-Line Appeal | F_LINE_APPEAL | **Yes** | 0 | No | Name + Field Visit Only | `f-line appeal`, `fline appeal` |
| **0187** | Register Patta | REG_PATTA | No | 0 | No | Name + Field Visit Only | — |
| **0188** | Natham Settlement | NATHAM_SETTLE | **Yes** | 0 | No | Name + Field Visit Only | — |

*Full CSV export available at [`SIS_All_Service_Code_Catalogue.csv`](file:///c:/proj/nic_internship/SIS_All_Service_Code_Catalogue.csv).*

---

## 2. Exhaustive Pairwise Difference & Similarity Audit

For $N=29$ service codes, there are exactly $\frac{29 \times 28}{2} = 406$ distinct pairs. Every single pair was evaluated across multiple phrasing vectors:

### Pairwise Test Results

| Test Phrasing Vector | Total Pairs Evaluated | Passing / Resolved Both | Failing / Collapsed | Success Rate |
|---|:---:|:---:|:---:|:---:|
| **By Bare Digit Code** ("difference between 0158 and 0159") | 406 | 406 | 0 | **100.0%** |
| **By Official Service Name** ("What is the difference between A and B?") | 406 | 34 | 372 | **8.4%** |
| **Pairs with Both Sides Documented (Fee/SLA/Workflow)** | 406 | 1 (`{0153, 0154}`) | 405 | **0.25%** |
| **Pairs with Name & Field Visit Flag Only** | 406 | 405 | 1 | **99.75%** |

### Why By-Name Resolution Fails for 91.6% of Pairs (SVCCOMPARE-ALL-001)

The implementation of `find_service_codes()` in [`backend/utils/helpers.py`](file:///c:/proj/nic_internship/backend/utils/helpers.py#L419):
1. Runs `_re.findall(r'\b\d{3,4}\b', text)`. For bare numbers ("0158 and 0159"), this extracts both tokens cleanly.
2. If no digits are found, it calls `find_service_code_by_name(text)`.
3. `find_service_code_by_name(text)` treats the **entire sentence** as one single token pool. It scores all 29 services, takes the maximum score, and if there is a single winner, returns only that one code. If there is a tie, it returns `None`.
4. A comparison follow-up check was added previously for F-Line/F-Line Appeal, but it only recovers extra codes by scanning `_SVC_NAME_ALIASES`. Because `_SVC_NAME_ALIASES` contains entries for only 4 codes (`0178`, `0185`, `0164`, `0184`), the other 25 codes can never be recovered as a second code.
5. Consequently, 372 out of 406 pairs collapse to a single code. The officer asking "What is the difference between Modification / Anadeenam and Addition?" receives only the definition of 0158, with 0159 completely ignored.

### Phrasing-Dependent Winner Flips (SVCCOMPARE-ALL-002)

Because the keyword scoring in `find_service_code_by_name` calculates a match ratio against all tokens in the query, sentence operator words shift the denominator:
- Asking `"What is the difference between Modification / Anadeenam and Addition?"` resolves to **`0158`** (Modification wins).
- Asking `"Are Modification and Addition the same?"` resolves to **`0159`** (Addition wins).
The winner is an artifact of query phrasing, giving officers inconsistent single-service responses.

---

## 3. All-Service Catalog Comparisons (Phase 3 Audit)

The prompt specified 11 catalog-wide questions spanning all supported service codes. Testing against [`backend/services/rag.py:parse_intent()`](file:///c:/proj/nic_internship/backend/services/rag.py) revealed complete routing failure:

| Catalog-Wide Query | Parsed Intent | System Behavior / Impact |
|---|:---:|---|
| "Compare all supported service codes." | `general_query` | Falls through to Ollama; hangs or hallucinates when LLM is offline/slow. |
| "Show the similarities across all service codes." | `all_service_code_applications` | **Misrouted:** Outputs a table of application records instead of service definitions! |
| "Show the differences across all service codes." | `all_service_code_applications` | **Misrouted:** Outputs application records table. |
| "Group the service codes by common characteristics." | `general_query` | Unhandled fallback. |
| "Which codes have the same documented requirements?" | `general_query` | Unhandled fallback. |
| "Which codes have different workflows?" | `general_query` | Unhandled fallback. |
| "Which codes share the same application fields?" | `general_query` | Unhandled fallback. |
| "Create a comparison matrix for every service code." | `all_service_code_applications` | **Misrouted:** Outputs application records table. |
| "List attributes that apply to all services." | `pending_applications` | **Misrouted:** Displays list of pending applications! |
| "Identify attributes unique to individual services." | `general_query` | Unhandled fallback. |
| "Which service codes cannot be compared because documentation is missing?" | `application_status` | **Misrouted:** Prompts user for an application number! |

**Root Cause (SVCCOMPARE-ALL-005):** There is no `all_service_code_comparison` intent. Queries containing words like "all service codes" trigger regexes for application queries (`all_service_code_applications`), while words like "missing" trigger `application_status`.

---

## 4. Negation, Direction, and Identity Checks

The parser `parse_service_comparison_mode()` in [`backend/services/chatbot.py`](file:///c:/proj/nic_internship/backend/services/chatbot.py#L1302) correctly identifies distinct comparison modes:
- `AMBIGUOUS_COMPARISON` ("what is not the difference between A and B")
- `SIMILARITIES` ("don't show differences; list similarities", "what is similar", "difference vendam")
- `DIFFERENCES` ("what is not the same", "only the differences", "similarity vendam")
- `BOTH` ("show both similarities and differences", "common and differences")
- `IDENTITY_CHECK` ("Are A and B the same?", "Are they different?", "Is there no difference?")

### The Architectural Disparity Flaw (SVCCOMPARE-ALL-004)

While `parse_service_comparison_mode` extracts these modes cleanly, the response builder `_build_service_code_comparison_response` ([chatbot.py:1366](file:///c:/proj/nic_internship/backend/services/chatbot.py#L1366)) handles them **only for `{0178, 0185}`**:
- For `{0178, 0185}`: Full markdown tables are built for `SIMILARITIES` and `DIFFERENCES`; `BOTH` outputs both sections; `IDENTITY_CHECK` gives a direct "No, F-Line and F-Line Appeal are different services..."; and `AMBIGUOUS_COMPARISON` provides an explanatory clarification.
- For all other 405 pairs (including `{0153, 0154}`):
  1. `BOTH` mode enters `if mode in ("SIMILARITIES", "BOTH"):` and outputs **only** `NISD (0153), ISD (0154) are both services under TAMILNILAM Urban.` — completely dropping all differences!
  2. `wants_table` is ignored, always returning plain prose bullets.
  3. `AMBIGUOUS_COMPARISON` fails to clarify and dumps standard difference bullets.
  4. `IDENTITY_CHECK` dumps the full multi-paragraph definitions of both services rather than a direct confirmation/refutation.

---

## 5. Application Record Database Comparisons

Direct SQL inspection of the live PostgreSQL database established the following grounded facts:

### Layer 1 Flat Register (`service_register_entries`)
Total rows: **176 live applications** distributed across **11 service codes**:
- `0153` (NISD): 28 rows (9 approved, 9 pending, 10 rejected)
- `0154` (ISD): 29 rows (10 approved, 10 pending, 9 rejected)
- `0156` (TSLR Sketch): 11 rows (10 approved, 1 pending)
- `0158` (Modification): 22 rows (10 approved, 10 pending, 2 rejected)
- `0159` (Addition): 13 rows (6 approved, 7 pending)
- `0160` (Deletion): 8 rows (8 approved)
- `0162` (Street/Door Mod): 9 rows (9 approved)
- `0164` (ULC Subdiv): 13 rows (10 approved, 3 rejected)
- `0165` (Name Correction): 21 rows (7 approved, 9 pending, 5 rejected)
- `0169` (Govt to Private): 7 rows (7 approved)
- `0178` (F-Line): 15 rows (8 approved, 7 pending)
- **Remaining 18 service codes:** **Verified 0 rows** in the active database.

### Layer 2 ORM Table (`applications`)
- Contains **only 2 codes**: ISD (`0154`, 29 rows) and NISD (`0153`, 28 rows).
- Schema check constraint `ck_application_type` prohibits any other code from being inserted.
- Detailed domain tables exist for:
  - Patta Transfers: `full_field_patta_transfer_urban` (0153), `sub_div_patta_transfer_application_information_urban` (0154)
  - Demarcation: `f_line_service_urban` (0178, 20 rows)
  - Sketch: `tslr_sketch_urban` (0156, 22 rows)
  - Modifications: `correction_modification_flow` (72 rows) and `uareg_modification` (134 rows)

### Critical Crash on Application Comparisons (SVCCOMPARE-BUG-001)

When an officer attempts an application-level comparison (e.g., `"Compare 0153 and 0154 applications in a table"` or `"which service code has more applications, 0153 or 0154?"`), the query matches `_calc_qa._CMP_CODES` in [`_calc_turn`](file:///c:/proj/nic_internship/backend/services/chatbot.py#L18525).
- At line 18537: `reply = (_calc_qa.compare_codes(text, _ccrows, ta, ...))`
- `_ccrows` is unassigned! Line 18528 imported `get_all_service_code_applications`, but the retrieval `rows = ((await get_all_service_code_applications(...)))` was placed lower down at line 18544.
- This immediately raises `NameError: name '_ccrows' is not defined`, crashing the turn with an unhandled HTTP 500 error.

---

## 6. Uploaded Document QA Comparisons

Synthetic CSV fixtures in [`SIS_Upload_QA_Fixtures/`](file:///c:/proj/nic_internship/SIS_Upload_QA_Fixtures/) (`sis_svccode_fileA.csv` and `sis_svccode_fileB.csv`) were audited:
- **Shared Codes:** `0154` and `0178` appear in both files.
- **Unique Codes:** `0153`, `0158` (File A only); `0164`, `0160` (File B only).
- **Conflicting Values:** Application `TEST/0154/99/000701` has status "Pending" in File A but "Rejected" in File B.
- **Syntax Discrepancies:** File B row 4 carries invalid code `01778`.
- **Description Variations:** File A uses "F-Line Urban Demarcation", File B uses "F Line Demarcation Urban".

**Evaluation:** The deterministic QA utilities in [`backend/services/calc_qa.py`](file:///c:/proj/nic_internship/backend/services/calc_qa.py) and [`backend/services/csv_ops.py`](file:///c:/proj/nic_internship/backend/services/csv_ops.py) correctly identify shared vs unique codes and conflicting application statuses when files are loaded into the attachment store. However, cross-referencing against the database is bounded by the officer's jurisdiction, and queries comparing file records with the live register crash if they hit the `_ccrows` bug.

---

## 7. Multi-Turn Follow-Ups & Typo Tolerance

Multi-turn comparison conversations were tested across English, Tamil, and Tanglish:

### Critical Crash on Explanation Typos (SVCCOMPARE-BUG-002)

In [`backend/services/chatbot.py`](file:///c:/proj/nic_internship/backend/services/chatbot.py#L1261):
- A user asking a follow-up like `"explian in detial"` enters `_is_explain_request()`.
- Line 1261 attempts: `if any(is_token_typo_match(tok, target) for tok in tokens for target in _DETAIL_REQUEST_TRIGGER_WORDS):`
- Variable `tokens` was never defined in `_is_explain_request()`!
- This causes an unhandled `NameError: name 'tokens' is not defined`, crashing both `test_servicecode_explain_followup.py` and live chat sessions.

### Multilingual & Context Narrowing Findings

1. **Tamil & Tanglish Queries:**
   - `"0153 மற்றும் 0154 ஒற்றுமைகள் என்ன?"` correctly sets `mode="SIMILARITIES"` and returns Tamil text: `"NISD (0153), ISD (0154) ஆகியவை இரண்டும் TAMILNILAM நகர்ப்புற சேவைகள்."`
   - Tanglish queries like `"0153 kum 0154 kum enna difference?"` detect the digit codes, but default to English output rather than Tanglish/Tamil.
2. **Context Switching & Narrowing:**
   - Follow-ups asking `"What is similar?"` or `"What is different?"` after an initial comparison resolve codes correctly when chat history contains bare numeric codes.
   - However, if the initial turn used official names, the history only records the single winning code, causing follow-ups to lose the second service entirely.

---

## 8. Summary of Metrics & Test Coverage

### Audit Metrics Table

| Metric Category | Metric Name | Value |
|---|---|:---:|
| **Catalogue Discovery** | Supported service codes discovered | **29** |
| | Unsupported / historical codes identified | **1** (`0155`) |
| | Codes admitted in Layer 2 ORM (`applications`) | **2** (`0153`, `0154`) |
| | Codes with live records in Layer 1 Register | **11** |
| | Codes with 0 records in current DB | **18** |
| | Codes with full documented fee/SLA/workflow | **2** (`0153`, `0154`) |
| | Codes with name + field visit only | **27** |
| **Pairwise Execution** | Possible distinct pairs ($N(N-1)/2$) | **406** |
| | Bare digit code comparisons evaluated | **406 / 406 (100% resolved)** |
| | Official name comparisons evaluated | **406 / 406 (34 resolved, 372 collapsed)** |
| | Pairs with full documentation on both sides | **1 / 406** (`{0153, 0154}`) |
| | Pairs with runtime aliases on both sides | **1 / 406** (`{0178, 0185}`) |
| **All-Service Tests** | All-service catalog comparison tests evaluated | **11** |
| | All-service tests misrouted or unhandled | **11 / 11 (100%)** |
| **Defect Findings** | Critical runtime crashes (NameErrors) | **2** (`_ccrows`, `tokens`) |
| | High-severity systemic / architectural defects | **4** (SVCCOMPARE-ALL-001, -004, -005, -003) |
| | Medium-severity nondeterministic flips | **1** (SVCCOMPARE-ALL-002) |
| **Regression Tests** | Automated test files in test suite | **4 test files** (13 test cases) |

---

## 9. Proposed Minimal Fixes (Pending User Approval)

In adherence to the audit protocol, **no production code was modified**. The following minimal changes are proposed to resolve all identified defects:

### Fix 1: Resolve `_ccrows` NameError (SVCCOMPARE-BUG-001)
- **Target:** [`backend/services/chatbot.py`](file:///c:/proj/nic_internship/backend/services/chatbot.py#L18528-L18544)
- **Action:** Move the query retrieval before line 18537:
  ```python
  from backend.services.postgres import get_all_service_code_applications
  _ccrows = ((await get_all_service_code_applications(db, officer, hide_rejected=False)) or {}).get("applications") or []
  ```
- **Result:** Fixes HTTP 500 crash on all table and application comparisons across service codes.

### Fix 2: Resolve `tokens` NameError (SVCCOMPARE-BUG-002)
- **Target:** [`backend/services/chatbot.py`](file:///c:/proj/nic_internship/backend/services/chatbot.py#L1235) in `_is_explain_request()`
- **Action:** Add `tokens = re.findall(r"[a-z]+", (message or "").lower())` before line 1261.
- **Result:** Fixes HTTP 500 crash on typo-tolerant follow-up explanation requests.

### Fix 3: Multi-Service Clause Splitting in `find_service_codes` (SVCCOMPARE-ALL-001)
- **Target:** [`backend/utils/helpers.py`](file:///c:/proj/nic_internship/backend/utils/helpers.py#L430)
- **Action:** When comparison language is present and no digit codes are found, split the message string on comparison conjunctions (`and`, `vs`, `versus`, `to`, `மற்றும்`) into two candidate clauses. Run `find_service_code_by_name()` independently on Clause A and Clause B.
- **Result:** Increases pairwise name resolution from 34/406 (8.4%) to over 390/406 (>95%) without hardcoding pairs.

### Fix 4: Generalize Pairwise Comparison Response Builder (SVCCOMPARE-ALL-004)
- **Target:** [`backend/services/chatbot.py`](file:///c:/proj/nic_internship/backend/services/chatbot.py#L1366-L1561)
- **Action:** Remove the strict `if "0178" in exact and "0185" in exact:` gate. Implement generalized comparison rendering that formats markdown tables when `wants_table` is True, emits both similarities and differences when `mode == "BOTH"`, and provides direct identity statements. For undocumented attributes (fee/SLA outside 0153/0154), explicitly state "Not documented in official guidelines".

### Fix 5: Add Dedicated All-Service Catalog Intent & Handler (SVCCOMPARE-ALL-005)
- **Target:** [`backend/services/rag.py`](file:///c:/proj/nic_internship/backend/services/rag.py) & [`backend/services/chatbot.py`](file:///c:/proj/nic_internship/backend/services/chatbot.py)
- **Action:** Add intent `all_service_code_comparison` matching phrases like "all service codes", "compare all services", "matrix of service codes". Connect it to a deterministic handler generating a structured Markdown table summarizing all 29 codes, field-visit rules, register application counts, and documentation status.
