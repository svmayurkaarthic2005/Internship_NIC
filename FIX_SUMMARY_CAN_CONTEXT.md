# CAN Number Context Extraction Fix - Summary

## Issue Reported
User reported: "I asked can no of previous question but it hallucinate n gives some random result"

## Root Cause
The `can_number_info` intent handler in `backend/services/chatbot.py` was only extracting application numbers from the **current message**, but not checking the **chat history** when the user asked about "the previous question".

### Problematic Code (Before Fix)
```python
elif intent == "can_number_info":
    _can_app_no = extract_application_number(message) or _gate_app_number
    _can_token = re.search(r'\b(\d{12,15})\b', message)
    # ... rest of handler
```

The code would fail when user asked:
- "what is the CAN number of the previous question?"
- "what is the can number of that application?"
- "show me the can number" (when an application was just discussed)

## Solution Applied
Added context extraction using the existing `_extract_app_number_from_context()` helper function, matching the pattern used by the `submission_channel_check` handler.

### Fixed Code
```python
elif intent == "can_number_info":
    _can_app_no = extract_application_number(message) or _gate_app_number
    if not _can_app_no:
        _can_app_no = _extract_app_number_from_context(
            message, chat_history, allow_implicit_continuation=True
        )
    _can_token = re.search(r'\b(\d{12,15})\b', message)
    # ... rest of handler
```

## Changes Made
1. **File Modified**: `backend/services/chatbot.py`
2. **Lines Changed**: 4773-4774 and 8253-8254 (both streaming and non-streaming versions)
3. **Logic Added**: Falls back to chat history extraction when current message doesn't contain an application number

## Testing
Created and ran `test_can_context_simple.py` to verify the fix:

### Test Results (All Passed ✅)
1. **Explicit reference**: "what is the can number of the previous question?" → ✅ Extracted correctly
2. **Implicit continuation**: "what is the can number?" → ✅ Extracted correctly  
3. **This application**: "what is the can number of this application?" → ✅ Extracted correctly
4. **No history**: Returns None as expected → ✅ Correct behavior

## MERGE Applications - Database Check

### Question
"is there anthing abt application mwege in db n csv C:\proj\nic_internship\backend\sample_table"

### Answer
**No, there are currently NO MERGE applications in the database or CSV files.**

#### Current Database State:
- **NISD Applications**: 168
- **ISD Applications**: 41
- **MERGE Applications**: 0
- **Total**: 209 applications

#### CSV Files Check:
No CSV files in `backend/sample_table/` contain MERGE (service code 0155) applications.

#### About MERGE Applications:
- **Service Code**: 0155
- **Type**: Subdivision Merger (உட்பிரிவு இணைப்பு பட்டா மாறுதல்)
- **Fee**: Government ₹0.00, CSC ₹60.00
- **Purpose**: When multiple survey numbers are being merged into one
- **SLA**: 15 working days
- **Workflow**: Citizen/CSC → SIS (Field Boundary & Total Merged Area Verification) → Tahsildar (Digital Signature)

The system **supports** MERGE applications in the code (chatbot.py handles them, service code guide includes them, etc.), but the **sample database was seeded with only ISD and NISD applications**.

## How to Add MERGE Applications
If you need MERGE applications in the database for testing:

1. Add rows to CSV files with `service_code = "0155"` 
2. Set `application_type = "MERGE"`
3. Run the seeding script: `python backend/sample_db/seed_sample_db.py`

Or manually insert via SQL with proper fields matching the MERGE workflow.

## Files Created During Investigation
1. `test_can_context_fix.py` - Full integration test (async DB test)
2. `test_can_context_simple.py` - Unit test for context extraction logic
3. `check_merge_applications.py` - Database and CSV checker for MERGE apps
4. `FIX_SUMMARY_CAN_CONTEXT.md` - This summary document

---

**Fix Status**: ✅ **COMPLETE AND VERIFIED**  
**Date**: 2026-09-02  
**Files Modified**: 1 (`backend/services/chatbot.py`)  
**Tests Pass**: 4/4 (100%)
