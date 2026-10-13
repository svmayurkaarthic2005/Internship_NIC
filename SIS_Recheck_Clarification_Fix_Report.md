# SIS Chatbot — Recheck and Clarification Follow-up Fix Report

**National Informatics Centre (NIC) — Sub Inspector Surveyor (SIS) AI Chatbot**  
**Date:** October 13, 2026  
**Status:** Complete & Fully Verified  
**Regression Test Results:** 9/9 Scenarios Passed (100%)  

---

## 1. Executive Summary

A conversational bug was identified where the SIS Chatbot entered a clarification loop following a disagreement over service codes in uploaded files:
1. When comparing service codes across two uploaded CSV files, the officer stated: *"I don't think they are the same."*
2. The assistant appropriately prompted for clarification:
   > *"Understood — it's not clear which answer you mean: the uploaded file(s), or the service-code comparison you just asked about? Let me know which one, and I'll re-check it."*
3. The officer provided the requested clarification: *"I don't think the files have the same service codes."*
4. **Defect**: Instead of retrieving the files and re-running the comparison, the chatbot failed to act on the clarification and repeated a generic apology prompt:
   > *"Understood — I may have misunderstood or provided an inaccurate answer to your previous question. Please clarify what you are looking for, or name the specific application, service code, or record to re-check."*

This root cause has been diagnosed and resolved. The assistant now preserves the disambiguation state, resolves the officer's follow-up intent, retrieves the active uploaded attachments, deterministically recomputes the service code comparison from database rows, renders the specified Markdown table, and provides concrete row-level discrepancy explanations.

---

## 2. Root Cause Analysis

Detailed investigation of `backend/services/chatbot.py`, `backend/services/attachment_qa.py`, and `backend/services/rag.py` identified four interacting root causes:

### Root Cause 1: Lack of State Persistence for Clarification Contracts
In `backend/services/chatbot.py` (`_route_negative_correction`), when an ambiguous pushback occurred (e.g. active attachments existed alongside a recent non-attachment service code query), the system asked the officer to choose between `uploaded file(s)` and `service-code comparison`. However:
- The system did **not** attach a structured `pending_clarification` contract into `structured_data` for the generated assistant message.
- Consequently, on the subsequent turn, the system had no persistent context indicating an outstanding disambiguation question was awaiting officer input.

### Root Cause 2: Negative Correction Re-Routing Without Resolution
When the officer replied *"I don't think the files have the same service codes"*, the phrase started with *"I don't think"*, triggering `attachment_qa.is_negative_correction(message)`. Because there was no handler to resolve `pending_clarification`, the query was routed as an independent negative correction. It hit `_build_general_negative_correction_response()` which emitted the second generic apology rather than executing the recheck.

### Root Cause 3: Placeholder String in Attachment Negative Correction
In `backend/services/attachment_qa.py` (`_handle_negative_correction`, lines 789–803), the branch handling negative corrections on active attachments contained a static placeholder string:
```python
# Old buggy code in attachment_qa.py:
return {
    "text": (
        "Understood — let me recheck the comparison between the uploaded files. "
        "Name the specific service code or row you think is wrong, and I'll verify it."
    ),
    "carry_context": carry,
}
```
Instead of actually recomputing the pairwise service code sets and differences, the code asked the officer yet again to name a specific service code.

### Root Cause 4: Upstream Intent Hijacking in `rag.py`
In `backend/services/rag.py` (line 4615), a regex matched `r"\b(?:both|neither|all of them)\b"` in follow-up queries after a service code inquiry and classified them as `service_code_lookup`. When an officer asked:
```text
Compare the service codes in both files
```
The word `both` caused `rag.py` to hijack the turn as a `service_code_lookup`, short-circuiting attachment processing before it could even begin.

### Root Cause 5: Missing Authoritative Definitions on Disputed Service Code Claims
When the officer clarified that the disputed claim concerned the statutory service code definition (e.g., F-Line 0178 vs F-Line Appeal 0185), the general negative correction handler lacked authoritative definitions and prompted for more clarification instead of providing the exact statutory definitions.

---

## 3. Solution Architecture & Implementation

### 3.1 Pending Clarification State Machine
A structured contract is now persisted in `ChatMessage.structured_data["pending_clarification"]`:
```json
{
  "type": "negative_correction_disambiguation",
  "target_options": ["uploaded_files", "service_code"],
  "task": "file_service_code_comparison",
  "document_ids": ["<uuid-a>", "<uuid-b>"],
  "sources": ["file_a.csv", "file_b.csv"],
  "original_disputed_claim": "I don't think they are the same.",
  "clarification_question": "Understood — it's not clear which answer you mean...",
  "attempt": 1
}
```

On every turn, `_route_negative_correction()` inspects the prior assistant message using `_get_last_assistant_structured_data()`. If `pending_clarification` is active:
1. `_resolve_pending_clarification()` checks if the officer's message resolves to `uploaded_files` (via keywords like `files`, `uploaded`, `csv`, `attachment`, `கோப்பு`, or grammatical subjects pointing to files). If so, it routes immediately to `attachment`.
2. If the user clarifies `service_code`, it routes to `general` and returns the authoritative statutory definitions.
3. If the user's message is an unrelated new query (e.g., *"What is the status of application 2026/0153/01/0001?"*), the pending clarification is cleared and standard routing proceeds.
4. If the message remains ambiguous:
   - On **Attempt 1**: Presents narrowed numbered options (1: CSV files, 2: statutory definitions).
   - On **Attempt 2+**: Activates an **anti-loop safeguard** that breaks the cycle and automatically executes the file recheck comparison.

### 3.2 Deterministic Service Code Comparison Engine
Implemented `find_service_code_column()` and `compare_csv_service_codes()` in `attachment_qa.py`. The engine:
1. Dynamically identifies service code columns using case-insensitive fuzzy matches (`service_code`, `servicecode`, `svc_code`, `service`, `code`).
2. Extracts and normalizes service codes from `attachment_rows` across both files (zero-padded 4-digit formatting).
3. Computes:
   - Unique service codes per file ($A$ and $B$)
   - Common codes ($A \cap B$)
   - Unique differences ($A \setminus B$ and $B \setminus A$)
   - Frequency breakdowns ($count$ per code)
   - Precise row citations using `csv_ops.row_citation(filename, row_numbers)`
4. Formats output using the strict Markdown table structure:
```markdown
| Comparison | File A (<filename>) | File B (<filename>) |
|---|---|---|
| Unique service codes | <actual set> | <actual set> |
| Codes found in both | <intersection> | <intersection> |
| Codes only in this file | <differences> | <differences> |
| Total occurrences | <actual count> | <actual count> |

### Service Code Comparison Summary:
- **Discrepancy Confirmed**: The files do **not** have the same service codes.
- **Codes found in both**: <code citations>
- **Codes only in File A (<name>)**: <code citations>
- **Codes only in File B (<name>)**: <code citations>
```

### 3.3 Negation and Difference Filtering
When the officer specifies:
> *"Don't include matching codes; show only the differences"*
`attachment_qa.plan_answer()` sets `filter_differences_only = True`. The engine omits the `Codes found in both` row from the table and summary. This filter flag is preserved in `structured_data` across rechecks.

### 3.4 Tamil & Tanglish Localization
The engine provides native Tamil headers (`| Comparison (ஒப்பீடு) |`, `இரு கோப்புகளிலும் உள்ளவை`, `கோப்பு A-ல் மட்டும் உள்ளவை`) and Tanglish support, fully aligned with the bilingual officer requirements.

---

## 4. Code Diffs

### 4.1 `backend/services/chatbot.py`
```diff
--- a/backend/services/chatbot.py
+++ b/backend/services/chatbot.py
@@ -2295,6 +2295,108 @@ async def _get_last_assistant_structured_data(db: AsyncSession, session_id: str
+    from sqlalchemy import and_, select
+    from backend.models import ChatMessage, messages_since_session_start
+    try:
+        row = (await db.execute(
+            select(ChatMessage.structured_data)
+            .where(and_(
+                ChatMessage.session_id == session_id,
+                ChatMessage.role == "assistant",
+                messages_since_session_start(session_id)
+            ))
+            .order_by(ChatMessage.created_at.desc())
+            .limit(1)
+        )).scalar_one_or_none()
+        return row if isinstance(row, dict) else None
+    except Exception:
+        return None
+
+async def _resolve_pending_clarification(
+    message: str,
+    pending_clarification: Dict[str, Any],
+    db: AsyncSession,
+    session_id: str,
+    language: str,
+    last_assistant_structured: Dict[str, Any]
+) -> Optional[Dict[str, Any]]:
+    msg_low = (message or "").strip().lower()
+    doc_ids = pending_clarification.get("document_ids") or []
+    sources = pending_clarification.get("sources") or []
+    attempt = pending_clarification.get("attempt", 1)
+
+    # 1. Check if user clarified in favor of uploaded files
+    file_cue = bool(re.search(
+        r"\b(?:files?|documents?|csv|uploaded|attachments?|கோப்பு|ஆவணம்)\b|"
+        r"\b(?:first|1|one|file\s*[ab])\b",
+        msg_low, re.IGNORECASE
+    ))
+    if file_cue or _NEGCORR_FILE_SUBJECT_RE.search(msg_low):
+        return {
+            "route": "attachment",
+            "resolved_target": "uploaded_files",
+            "document_ids": doc_ids,
+            "sources": sources
+        }
+
+    # 2. Check if user clarified in favor of service code definition
+    svc_cue = bool(re.search(
+        r"\b(?:service\s*codes?|statutory|definition|rules?|second|2|two)\b|"
+        r"\b(?:0178|0185|0153|0154)\b",
+        msg_low, re.IGNORECASE
+    ))
+    if svc_cue:
+        return {
+            "route": "general",
+            "resolved_target": "service_code"
+        }
+
+    # 3. Check if user response is STILL ambiguous disagreement
+    if attachment_qa.is_negative_correction(message) or any(w in msg_low for w in ("neither", "both", "check again", "none", "recheck", "still")):
+        if attempt < 2:
+            text = (
+                f"Please specify which answer you would like me to re-check:\n"
+                f"1. The service codes inside your uploaded CSV files ({', '.join(sources)})\n"
+                f"2. The official service-code definitions (e.g. 0178 F-Line vs 0185 F-Line Appeal)"
+            )
+            updated_pc = dict(pending_clarification)
+            updated_pc["attempt"] = attempt + 1
+            return {
+                "route": "ambiguous",
+                "response": {"response": text, "intent": "general_negative_correction"},
+                "carry_context": {"pending_clarification": updated_pc}
+            }
+        else:
+            # Safeguard against endless loop: default to rechecking active attachments
+            return {
+                "route": "attachment",
+                "resolved_target": "uploaded_files",
+                "document_ids": doc_ids,
+                "sources": sources
+            }
+    return None
```

### 4.2 `backend/services/attachment_qa.py`
```diff
--- a/backend/services/attachment_qa.py
+++ b/backend/services/attachment_qa.py
@@ -789,16 +789,14 @@ async def _handle_negative_correction(
-    return {
-        "text": (
-            "Understood — let me recheck the comparison between the uploaded files. "
-            "Name the specific service code or row you think is wrong, and I'll verify it."
-        ),
-        "carry_context": carry,
-    }
+    # Perform deterministic recheck of active CSV service codes
+    doc_a = target_recheck[0]
+    doc_b = target_recheck[1]
+    filter_differences = bool((last_structured or {}).get("filter_differences_only"))
+    rechecked_text = await compare_csv_service_codes(
+        db=db,
+        doc_a=doc_a,
+        doc_b=doc_b,
+        filter_differences_only=filter_differences,
+        language=language
+    )
+    return {
+        "text": rechecked_text,
+        "carry_context": carry,
+    }
```

### 4.3 `backend/services/rag.py`
```diff
--- a/backend/services/rag.py
+++ b/backend/services/rag.py
@@ -4615,7 +4615,8 @@ def parse_intent(msg: str, prev_intent: Optional[str] = None) -> str:
     if prev_intent == "service_code_lookup" and re.search(
         r"\b(?:both|neither|all of them)\b", msg, re.IGNORECASE
-    ):
+    ) and not re.search(
+        r"\b(?:files?|documents?|csv|uploaded|attachments?)\b|கோப்பு|ஆவணம்", msg, re.IGNORECASE
+    ):
         return "service_code_lookup"
```

---

## 5. Test Suite Verification & Results

The test suite `test_recheck_clarification_suite.py` was executed against all target scenarios:

| Test ID | Scenario Name | Status | Verified Criteria |
|:---|:---|:---:|:---|
| **SCEN-A** | Exact Screenshot Failure | **PASS** | Clarification requested on Turn 1; exact Markdown comparison table rendered on Turn 2 without repeat prompt loop; row citations included. |
| **SCEN-B** | Direct Clarification Correction | **PASS** | Answering *"The uploaded files"* routes cleanly to attachment comparison without re-asking. |
| **SCEN-C** | Explicit Definition Correction | **PASS** | Answering *"The service-code comparison"* returns authoritative definitions for 0178 and 0185. |
| **SCEN-D** | Topic Change Away from Comparison | **PASS** | Asking an unrelated application query clears the disambiguation contract and answers normally. |
| **SCEN-E** | Still Ambiguous Pushback & Safeguard | **PASS** | Turn 2 narrows with numbered options; Turn 3 safeguard triggers and defaults to table recheck, avoiding infinite loop. |
| **SCEN-F** | Negation & Difference Filtering | **PASS** | *"Don't include matching codes"* omits the matching row; preserved across rechecks. |
| **SCEN-G** | Tamil & Tanglish Queries | **PASS** | Direct Tamil pushback renders Tamil table (`ஒப்பீடு`) and Tamil discrepancy verdict; Tanglish executes cleanly. |
| **SCEN-H** | Missing or Expired Attachments | **PASS** | Detects inactive/expired attachments and advises re-upload gracefully without 500 error. |
| **SCEN-I** | Streaming Endpoint (`/chat/stream`) | **PASS** | SSE token stream emits table chunks, code lists, and summary without buffering deadlock. |

**Overall Result: 9/9 Passed (100%)**

### Zero Regression Check on Existing Suites
- `test_contradiction_clarification.py`: 8/8 Passed (100%)
- `test_attachment_routing.py`: 6/6 Passed (100%)
- `test_upload_qa_defect_fixes.py`: 38/38 Passed (100%)

---

## 6. Before vs. After Conversational Transcripts

### Before Fix (Buggy Loop)
```text
[Officer]: Compare the service codes in both files.
[Assistant]: | Comparison | File A (file_a.csv) | File B (file_b.csv) |
             |---|---|---|
             | Unique service codes | 0153, 0154 | 0154, 0178 |
             | Codes found in both | 0154 | 0154 |
             | Codes only in this file | 0153 | 0178 |
             | Total occurrences | 3 (0153: 2, 0154: 1) | 2 (0154: 1, 0178: 1) |

[Officer]: I don't think they are the same.
[Assistant]: Understood — it's not clear which answer you mean: the uploaded file(s), or the service-code comparison you just asked about? Let me know which one, and I'll re-check it.

[Officer]: I don't think the files have the same service codes.
[Assistant]: Understood — I may have misunderstood or provided an inaccurate answer to your previous question. Please clarify what you are looking for, or name the specific application, service code, or record to re-check.  <-- BUG: REPEATED CLARIFICATION LOOP
```

### After Fix (Resolved)
```text
[Officer]: Compare the service codes in both files.
[Assistant]: | Comparison | File A (file_a.csv) | File B (file_b.csv) |
             |---|---|---|
             | Unique service codes | 0153, 0154 | 0154, 0178 |
             | Codes found in both | 0154 | 0154 |
             | Codes only in this file | 0153 | 0178 |
             | Total occurrences | 3 (0153: 2, 0154: 1) | 2 (0154: 1, 0178: 1) |

[Officer]: I don't think they are the same.
[Assistant]: Understood — it's not clear which answer you mean: the uploaded file(s), or the service-code comparison you just asked about? Let me know which one, and I'll re-check it.

[Officer]: I don't think the files have the same service codes.
[Assistant]:
| Comparison | File A (file_a.csv) | File B (file_b.csv) |
|---|---|---|
| Unique service codes | 0153, 0154 | 0154, 0178 |
| Codes found in both | 0154 | 0154 |
| Codes only in this file | 0153 | 0178 |
| Total occurrences | 3 (0153: 2, 0154: 1) | 2 (0154: 1, 0178: 1) |

### Service Code Comparison Summary:
- **Discrepancy Confirmed**: The files do **not** have the same service codes.
- **Codes found in both**: 0154 (file_a.csv: 0154 (row 2); file_b.csv: 0154 (row 1)).
- **Codes only in File A (file_a.csv)**: 0153 (0153 (row 1, 3)).
- **Codes only in File B (file_b.csv)**: 0178 (0178 (row 2)).
```

---

## 7. Security, Multi-Tenant Session Isolation & Guardrails

1. **Session & Officer Isolation**:
   - Attachment retrieval strictly queries `chat_attachments.officer_id == officer.id` and `chat_attachments.session_id == session_id`.
   - Officers cannot access, cite, or compare attachments belonging to another officer or another session.
2. **Ground Truth Determinism**:
   - Comparisons are computed purely via database rows (`attachment_rows`) with mathematical set intersections and counts.
   - LLM hallucinations are completely eliminated from numerical and service code counts.
3. **Loop Safeguard**:
   - The disambiguation state machine enforces `attempt < 2`. Officers cannot be trapped in recurring apology loops even if subsequent inputs remain ambiguous.
