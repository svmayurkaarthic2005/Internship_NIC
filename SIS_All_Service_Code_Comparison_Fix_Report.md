# SIS Chatbot — Exhaustive Service-Code Comparison Implementation & Fix Report
**Repository**: [https://github.com/svmayurkaarthic2005/Internship_NIC](https://github.com/svmayurkaarthic2005/Internship_NIC)  
**Date**: October 13, 2026  
**Auditor & Implementation Engineer**: Antigravity AI Assistant  
**Officer Context Verified**: `marun_chn@sis.tn.gov.in` (Ward 102/002, SIS-002)  
**Status**: COMPLETE — ALL DEFECTS FIXED, TESTED, AND VERIFIED (100% PASS RATE)

---

## 1. Executive Summary & Defect Remediation Scorecard

Following the exhaustive similarity and difference audit across all 29 TAMILNILAM Urban service codes, seven distinct defects were authorized for production-code remediation. All seven defects have been resolved with zero regressions to existing functionality.

### Verification Scorecard

| Defect ID | Category | Pre-Fix Status | Post-Fix Status | Verification Test / Evidence |
|---|---|---|---|---|
| **SVCCOMPARE-BUG-001** | Runtime Crash | Fatal `NameError: name '_ccrows' is not defined` (HTTP 500) | **FIXED** (Safe initialization & DB guard) | `test_service_code_calc_crash_regression.py` |
| **SVCCOMPARE-BUG-002** | Runtime Crash | Fatal `NameError: name 'tokens' is not defined` (HTTP 500) | **FIXED** (Regex token initialization) | `test_service_code_calc_crash_regression.py` |
| **SVCCOMPARE-ALL-001** | Pairwise Name Resolution | Only **34 / 406** pairs resolved both names (8.4%) | **406 / 406 (100.0%)** | `test_all_servicecode_pairwise_resolution.py` |
| **SVCCOMPARE-ALL-002** | Single-Winner Collapse | Winner flipped arbitrarily based on phrasing | **ELIMINATED** (Clause decomposition) | `test_all_servicecode_pairwise_resolution.py` |
| **SVCCOMPARE-ALL-003** | Undocumented Attributes | Risk of fee/SLA hallucination for 27 codes | **GUARDED** (Explicit departmental disclaimer) | `_build_service_code_comparison_response` |
| **SVCCOMPARE-ALL-004** | Pairwise Engine Disparity | Tables/negation/both hardcoded exclusively for `{0178, 0185}` | **GENERALIZED** (All 406 pairs supported) | `test_pairwise_comparison_architectural_disparity.py` |
| **SVCCOMPARE-ALL-005** | Catalog-Wide Misrouting | All 11 whole-catalog queries misrouted (0 / 11) | **11 / 11 (100.0%)** resolved to dedicated intent | `test_all_service_comparison_routing.py` |

---

## 2. Root Cause Analysis & Technical Solutions

### 2.1 Bug A (SVCCOMPARE-BUG-001): `_ccrows` NameError in `_calc_turn`
- **Root Cause**: In `backend/services/chatbot.py:18537` (within `_calc_turn`), `_calc_qa.compare_codes(text, _ccrows, ta)` was called when queries matched table/count comparison patterns, but `_ccrows` was defined only further down at line 18544. Additionally, when `db is None` (e.g. during standalone evaluation or fallback), calling `save_chat_messages` attempted an unhandled session rollback.
- **Implemented Fix**:
  1. Initialized `_ccrows = ((await get_all_service_code_applications(db, officer, hide_rejected=False)) or {}).get("applications") or [] if db else []` immediately before calling `compare_codes`.
  2. Guarded `save_chat_messages` with `if db:` and extracted `officer_id=getattr(officer, "officer_id", None)` to prevent crashes with mock or string officer inputs.
- **Verification**: `test_service_code_calc_crash_regression.py` passes cleanly without NameError.

### 2.2 Bug B (SVCCOMPARE-BUG-002): `tokens` NameError in `_is_explain_request`
- **Root Cause**: In `backend/services/chatbot.py:1261`, `_is_explain_request()` checked fuzzy typo matches against `_DETAIL_REQUEST_TRIGGER_WORDS` by iterating over `tokens`, but the local assignment `tokens = ...` had been omitted from the function scope.
- **Implemented Fix**: Initialized `tokens = re.findall(r"[a-zA-Z]+", message or "")` at the top of `_is_explain_request()` before evaluating triggers.
- **Verification**: `_is_explain_request("explian in detial")` and `_is_explain_request("explain briefly")` both return `True` without raising exceptions.

### 2.3 Pairwise Resolution (SVCCOMPARE-ALL-001 & 002): 406 / 406 Exhaustive Name Matching
- **Root Cause**: `find_service_codes()` previously relied on `find_service_code_by_name()`, which scored the entire message against all 29 services as a single pool, producing at most one winner and dropping the second service. Moreover, grammatical particles ("difference between" vs "the same") shifted keyword match ratios arbitrarily.
- **Implemented Fix in `backend/utils/helpers.py`**:
  1. **Catalogue Phrase Index**: Implemented `_build_catalogue_service_phrases()` extracting normalized names, slash-subdivisions, short codes, and known domain aliases for all 29 services.
  2. **Multi-Span Non-Overlapping Resolution**: Implemented `_all_catalogue_service_codes(text)` which scans all matches, sorts candidate spans by starting position and length descending, and eliminates overlapping spans (preventing substring collisions like `0169 Govt to Private` being swallowed by `0179 Settlement Govt to Private`).
  3. **Comparative Clause Splitting**: If fewer than 2 codes are matched and comparative language is detected (`vs`, `and`, `versus`, `மற்றும்`), splits the text on conjunction delimiters and evaluates candidate clauses independently.
- **Verification**: `test_all_servicecode_pairwise_resolution.py` verified that pairwise resolution jumped from **34 / 406 (8.4%) to 406 / 406 (100.0%)** across all combinations of the 29 supported services.

### 2.4 Undocumented Attribute Policy (SVCCOMPARE-ALL-003): Anti-Hallucination Guard
- **Fact Basis**: Departmental revenue rules and source codes in this repository establish statutory application fees and SLA turnarounds **only for 0153 (NISD: Rs. 60, 15 days)** and **0154 (ISD: Rs. 400 + Rs. 600, 30 days)**. The other 27 services have verified names, short codes, and field inspection mandates (`requires_field_visit`), but no statutory fee or SLA schedules exist in repository guidelines.
- **Implemented Policy**:
  - In `_build_service_code_comparison_response` and `_all_service_code_comparison_answer`, any attribute outside statutory grounds strictly outputs:
    `"Not documented in departmental guidelines"` or `"Fees, SLAs, and workflow stages for these service codes are not documented in current departmental guidelines."`
  - Neither the LLM nor deterministic helpers fabricate fee amounts or SLA turnarounds.

### 2.5 Architectural Comparison Generalization (SVCCOMPARE-ALL-004)
- **Root Cause**: `_build_service_code_comparison_response` previously had a hardcoded `if '0178' in exact and '0185' in exact:` block for tables, negations, and BOTH modes, falling back to a one-sentence stub for all other 405 pairs.
- **Implemented Fix**: Replaced the pair-specific logic with a universal domain comparison engine:
  - **Tables**: Produces a standardized 5-column Markdown comparison table (`Attribute`, `Service A`, `Service B`, `Finding`, `Evidence / Source`) covering Code, Name, Field Inspection, Statutory Fee, and SLA.
  - **BOTH Mode**: Explicitly generates both `### Similarities` and `### Differences` sections.
  - **AMBIGUOUS_COMPARISON**: Asks for clarification when ambiguous negation phrases ("what is not the difference") are received.
  - **IDENTITY_CHECK**: Returns an immediate direct verdict (`"No, <Name A> (<Code A>) and <Name B> (<Code B>) are different services under TAMILNILAM Urban:"`).
- **Verification**: `test_pairwise_comparison_architectural_disparity.py` passes 100%.

### 2.6 Whole-Catalogue Comparison Intent (SVCCOMPARE-ALL-005)
- **Root Cause**: Catalog-wide queries like "Compare all supported service codes" or "Show similarities across all service codes" lacked an intent and fell through to application listings (`all_service_code_applications`) or single-application lookups (`application_status`).
- **Implemented Fix**:
  1. Added `_ALL_SERVICE_COMPARISON_RE` in `backend/services/rag.py` matching whole-catalogue comparison phrasing, routing directly to intent `all_service_code_comparison`.
  2. Implemented deterministic handler `_all_service_code_comparison_answer(text, is_tamil)` in `backend/services/chatbot.py`:
     - **Full Matrix**: Returns complete 29-code Markdown table with Code, Official Name, Short Code, Field Visit Required, Documented Detail Level, and Register Status.
     - **Similarities**: Lists the 3 universal attributes shared by all 29 services (TAMILNILAM Urban, Ward SIS jurisdiction, standard register schema).
     - **Differences**: Breaks down field visits (19 mandatory, 10 desk-only), database presence (2 core admitted in ORM, 27 extended), and documentation depth.
     - **Functional Groupings**: Groups all 29 codes into 6 functional clusters (Patta transfers, Demarcation, TSLR extracts, Modifications, Settlement, Special land).
     - **Missing Documentation**: Formally enumerates the 27 codes lacking statutory fee/SLA schedules in guidelines.
  3. Wired handler into `_calc_turn`, `_process_chat_core()`, and `process_chat_stream()`.
- **Verification**: `test_all_service_comparison_routing.py` confirms that all 11 catalog questions resolve to `all_service_code_comparison` (100% pass rate).

### 2.7 Application-Level vs Definition-Level Comparison Separation
- **Implemented Fix in `backend/services/calc_qa.py`**:
  - In `compare_codes()`, updated line 558 to permit comparing applications for `0153` vs `0154` when table, count, status, or record keywords are present (`applications?|files?|records?|status|statuses|table|counts?`).
  - Definition comparisons continue to route cleanly to `_build_service_code_comparison_response`, maintaining crisp boundary between register rows and statutory rules.

---

## 3. Regression Test Suite Execution Summary

All 8 test suites in `SIS_ServiceCode_FileDB_Regression_Tests/` were executed against the active virtual environment (`.venv\Scripts\python.exe`):

```powershell
PASS: SIS_ServiceCode_FileDB_Regression_Tests/test_service_code_calc_crash_regression.py
PASS: SIS_ServiceCode_FileDB_Regression_Tests/test_all_servicecode_pairwise_resolution.py
PASS: SIS_ServiceCode_FileDB_Regression_Tests/test_pairwise_comparison_architectural_disparity.py
PASS: SIS_ServiceCode_FileDB_Regression_Tests/test_all_service_comparison_routing.py
PASS: SIS_ServiceCode_FileDB_Regression_Tests/test_fline_appeal_multicode_comparison.py
PASS: SIS_ServiceCode_FileDB_Regression_Tests/test_servicecode_alias_collision.py
PASS: SIS_ServiceCode_FileDB_Regression_Tests/test_csv_plural_column_resolution.py
PASS: SIS_ServiceCode_FileDB_Regression_Tests/test_negative_correction_antecedent_routing.py

Total: 8, Failed: 0
```

### Generated Artifacts
1. **`SIS_All_Service_Code_Comparison_Matrix.csv`**: Completely regenerated across all 406 pairs:
   - `by_name_correct`: **406 / 406 (100.0%)**
   - `by_bare_code_correct`: **406 / 406 (100.0%)**
2. **`SIS_All_Service_Code_Comparison_Results.csv`**: Contains **424** automated test executions:
   - 406 pairwise name resolutions (100% PASS)
   - 5 pairwise generalized feature tests (Markdown table, BOTH, AMBIGUOUS, IDENTITY, UNDOCUMENTED) (100% PASS)
   - 11 whole-catalogue intent and generation tests (100% PASS)
   - 2 runtime crash regression tests (100% PASS)
   - **Total**: 424 / 424 Passed (100.0%).
3. **`SIS_All_Service_Code_Comparison_Failures.jsonl`**: All 7 failure records updated to `fix_implemented: true`, `fix_verified_live_ui: true`, with verification details.

---

## 4. Code Preservation & Constraints Compliance

1. **Uncommitted Work Preserved**:
   - `git status` confirmed that only 4 files in `backend/` were modified:
     - `backend/services/calc_qa.py`
     - `backend/services/chatbot.py`
     - `backend/services/rag.py`
     - `backend/utils/helpers.py`
   - Concurrent continuation, chat attachments, CSV arithmetic, negation handling, and antecedent tracking logic were fully preserved with zero regression.
2. **Git Commit / Push**:
   - Strictly followed instructions: no commits created, no pushes performed. All changes remain unstaged in the local working directory ready for user inspection.
3. **Domain Grounding**:
   - No fictitious fees, turnaround times, or application records were invented. Undocumented attributes are strictly labeled as not documented in departmental guidelines.
