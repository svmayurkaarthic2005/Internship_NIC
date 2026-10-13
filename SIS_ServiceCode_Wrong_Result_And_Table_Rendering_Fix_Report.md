# SIS Chatbot — Critical Bug Fix Report: Service-Code Selection & Markdown Table Rendering
**Repository**: [https://github.com/svmayurkaarthic2005/Internship_NIC](https://github.com/svmayurkaarthic2005/Internship_NIC)  
**Date**: October 13, 2026  
**Auditor & Implementation Engineer**: Antigravity AI Assistant  
**Officer Tested**: `marun_chn@sis.tn.gov.in` (Ward 102/002, SIS-002)  
**Status**: COMPLETE — BOTH DEFECTS FIXED, TESTED, AND VERIFIED IN PRODUCTION CODE & CHROME UI

---

## 1. Executive Summary & Defect Overview

When asking the following query in the SIS Chatbot UI:
> `Show only the differences between 0153 and 0154 in a table`

The chatbot exhibited two critical defects:
1. **Wrong Service-Code Selection**: Instead of comparing the requested codes `0153` (NISD) and `0154` (ISD), the response returned data for unrelated codes `0158` (Modification / Anadeenam) and `0159` (Addition), presenting application counts instead of service definition differences.
2. **Broken Markdown Table Rendering**: The response was displayed as raw, unformatted text containing literal pipe (`|`) characters and dashed divider rows (`|---|---|`) rather than rendering an actual styled HTML table in the web interface.

Both defects have been reproduced, traced to their precise architectural roots, resolved in production code, and verified via automated test suites and live browser testing in Google Chrome.

---

## 2. Root Cause Analysis & Pipeline Trace

### 2.1 Defect 1: Wrong Service Codes (History Bleeding & Semantic Misrouting)

#### The Complete Execution Trace
1. **User Request**: The user asked: `Show only the differences between 0153 and 0154 in a table`.
2. **Preceding Session Context**: In the preceding turn of the same chat session, the user had asked: `What is the difference between Modification / Anadeenam and Addition?`, which introduced service codes `0158` and `0159` into `chat_history`.
3. **Execution Path**:
   - `process_chat_stream()` in `backend/services/chatbot.py` invoked `_calc_turn()` as an early turnkey interceptor before intent parsing.
   - Inside `_calc_turn()`, the check for conceptual differences (`compare_codes_conceptual`) failed to match the phrasing `"Show only the differences"` (its regex strictly anticipated `"What is the difference"` or `"How is ... different"`).
   - The query then reached the general comparison branch (`_CMP_CODES.search(text)`), which matched the token `"differences"`.
4. **The Earliest Divergence (Line 18725 in `chatbot.py`)**:
   ```python
   # Defective pre-fix logic:
   _override_codes = None
   if chat_history:
       for _h in reversed(chat_history):
           _c_hist = find_service_codes(_h.get("content") or "")
           if len(_c_hist) >= 2:
               _override_codes = _c_hist  # <-- EXTRACTED ['0158', '0159'] UNCONDITIONALLY
               break
   reply = _calc_qa.compare_codes(text, _ccrows, ta, codes_override=_override_codes)
   ```
   `_override_codes` was populated from `chat_history` **without checking whether the current query `text` already contained explicit service codes**.
5. **The Precedence Inversion in `calc_qa.py` (Line 541)**:
   ```python
   # Defective pre-fix logic in compare_codes:
   codes = []
   if codes_override and len(codes_override) == 2:
       codes = [c for c in codes_override if c in SIS_URBAN_SERVICES] # <-- OVERRODE EXPLICIT CODES
   if len(codes) != 2:
       for c in find_service_codes(t):
           ...
   ```
   `compare_codes()` inspected `codes_override` *before* extracting service codes from the query `t`. Consequently, `0153` and `0154` were discarded, and `0158` and `0159` were substituted.
6. **Semantic Misrouting (Register Counts vs. Service Definitions)**:
   `compare_codes()` is designed to compare **application register counts** (`Total Applications`, `Approved`, `Pending`, `Rejected`), whereas the user query requested **service definition differences** (statutory fees, SLAs, field visit mandates). Because `_calc_turn` lacked an early definition comparison check, the request was hijacked into application counting.

---

### 2.2 Defect 2: Broken Markdown Table Rendering in the Frontend UI

#### The Frontend Execution Trace
1. **API Response**: The backend streamed or returned valid Markdown table syntax:
   ```markdown
   | Attribute | NISD (0153) | ISD (0154) | Finding | Evidence / Source |
   |---|---|---|---|---|
   | Service Code | 0153 | 0154 | Distinct codes | Urban Service Master |
   ...
   ```
2. **Frontend Handler (`frontend/js/chat.js:formatBotMessage`)**:
   - `formatBotMessage(text)` checked:
     `const hasHTML = /<[a-zA-Z][^>]*>/i.test(text);`
   - Since plain text / Markdown responses contain no HTML tags, `hasHTML` evaluated to `false`.
   - The function then escaped HTML (`let formatted = escapeHtml(text);`) and performed:
     `formatted = formatted.replace(/\n/g, '<br>');`
   - **No Markdown table parser existed** in `formatBotMessage()`.
   - As a result, newline characters were converted to `<br>`, causing pipe characters (`|`) and dashed dividers (`|---|`) to be rendered as literal characters in the browser.

---

## 3. Implemented Fixes & Architectural Enhancements

### 3.1 Backend Fixes

#### 1. In `backend/services/calc_qa.py` (`compare_codes`)
- **Explicit Code Precedence**: Inverted the evaluation order so `find_service_codes(t)` is checked first. Explicit codes named in the user's message always take top precedence over any historical antecedent.
- `codes_override` is only used as a fallback when the user's message contains fewer than 2 codes (e.g. anaphoric queries like *"Which has more applications?"*).

#### 2. In `backend/services/chatbot.py` (`_calc_turn`)
- **Service Definition Comparison Routing**: Added an explicit check before application counts. If `len(_explicit_codes) >= 2` and the message is NOT asking about application counts/files/records (`applications?|files?|records?|counts?|more than`), comparison requests are routed directly to `_build_service_code_comparison_response(_explicit_codes, text, ta)`.
- **History Isolation**: Guarded the `chat_history` scan with `if len(_explicit_codes) < 2 and chat_history:`. Prior turns can no longer bleed into queries that explicitly name their own service codes.

---

### 3.2 Frontend Fixes

#### In `frontend/js/chat.js` (`formatBotMessage`, `renderMarkdownTables`, `formatMarkdownText`)
- Implemented `renderMarkdownTables(text)`:
  - Scans for blocks of Markdown table lines starting and ending with `|`.
  - Parses header rows, delimiter rows (`|---|`), and data rows.
  - Generates secure, structured HTML tables wrapped in `<div class="data-table-container"><table class="data-table">...</table></div>`.
  - Leverages existing CSS classes in `frontend/css/table_styles.css` for sticky headers, clean cell alignment, and responsive horizontal scrolling.
  - Sanitizes cell contents via `escapeHtml()` while preserving inline markdown styling (`**bold**`, `` `code` ``).
- Implemented `formatMarkdownText(raw)`:
  - Converts Markdown headings (`### `, `## `, `# `) into styled semantic tags (`<h3>`, `<h2>`, `<h1>`).
  - Converts bullet lists (`•`, `-`, `*`) and numbered lists into semantic `<ul>` and `<ol>` elements.
  - Preserves application number chips and handles line breaks cleanly.
- Preserved strict security: Arbitrary user-provided HTML or script tags remain fully escaped and neutralized.

---

## 4. Verification & Testing Evidence

### 4.1 Live Browser Verification in Chrome
Using the automated browser subagent, the exact user workflow was tested end-to-end against `http://localhost:3000`:
1. **Turn 1**: User asked: `What is the difference between Modification / Anadeenam and Addition?` (Service codes `0158` and `0159` established in history).
2. **Turn 2**: In the **same conversation session**, user asked: `Show only the differences between 0153 and 0154 in a table`.
   - **Result**: Chatbot compared **`0153` (NISD) and `0154` (ISD)**.
   - Codes `0158` and `0159` were **100% excluded**.
   - Rendered as a **styled HTML table (`<table class="data-table">`)** with 5 columns and proper borders; zero literal pipe characters displayed.
3. **Turn 3**: User asked: `Compare 0153 and 0154 applications in a table`.
   - **Result**: Chatbot returned application statistics for `0153` and `0154` formatted as an HTML table.

**Artifacts & Evidence**:
- Browser recording: [`sis_table_and_code_fix_ui_test_1791904212562.webp`](file:///C:/Users/ASUS/.gemini/antigravity-ide/brain/ea32feb0-f2c1-4d1a-8f9f-ca2ba3543627/sis_table_and_code_fix_ui_test_1791904212562.webp)
- Screenshot of rendered table: [`rendered_tables_comparison_1791904345428.png`](file:///C:/Users/ASUS/.gemini/antigravity-ide/brain/ea32feb0-f2c1-4d1a-8f9f-ca2ba3543627/rendered_tables_comparison_1791904345428.png)

---

### 4.2 Automated Regression Suite Results (9 / 9 Suites Passed)

```powershell
PASS: SIS_ServiceCode_FileDB_Regression_Tests/test_service_code_calc_crash_regression.py
PASS: SIS_ServiceCode_FileDB_Regression_Tests/test_all_servicecode_pairwise_resolution.py
PASS: SIS_ServiceCode_FileDB_Regression_Tests/test_pairwise_comparison_architectural_disparity.py
PASS: SIS_ServiceCode_FileDB_Regression_Tests/test_all_service_comparison_routing.py
PASS: SIS_ServiceCode_FileDB_Regression_Tests/test_fline_appeal_multicode_comparison.py
PASS: SIS_ServiceCode_FileDB_Regression_Tests/test_servicecode_alias_collision.py
PASS: SIS_ServiceCode_FileDB_Regression_Tests/test_csv_plural_column_resolution.py
PASS: SIS_ServiceCode_FileDB_Regression_Tests/test_negative_correction_antecedent_routing.py
PASS: SIS_ServiceCode_FileDB_Regression_Tests/test_service_code_wrong_selection_and_table_rendering.py

Total: 9 suites, Failed: 0
```

---

## 5. Deliverables & Modified Files

### Files Modified:
1. `backend/services/calc_qa.py`: Inverted code resolution precedence in `compare_codes()`.
2. `backend/services/chatbot.py`: Routed definition comparisons to `_build_service_code_comparison_response` in `_calc_turn()`; isolated `chat_history` override.
3. `frontend/js/chat.js`: Implemented `renderMarkdownTables()` and `formatMarkdownText()` in `formatBotMessage()`.

### New Test & Tracking Deliverables:
1. `SIS_ServiceCode_FileDB_Regression_Tests/test_service_code_wrong_selection_and_table_rendering.py`: Dedicated regression test verifying history non-bleed, definition vs application separation, and markdown table syntax.
2. `SIS_ServiceCode_Wrong_Result_Regression_Results.csv`: Complete test execution matrix covering all required permutations.
3. `SIS_ServiceCode_Wrong_Result_Failure_Cases.jsonl`: Formal defect lifecycle tracking for `SVCCOMPARE-FIX-001` and `SVCCOMPARE-FIX-002`.

*Note: All uncommitted work has been preserved intact. No git commits or pushes have been performed.*
