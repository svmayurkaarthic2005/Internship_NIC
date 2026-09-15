# ISD/NISD Definitions - Verification Report

## Date: 2026-09-02
## Status: ✅ ALL FIXED - 1 Test File Corrected

---

## Correct Definitions

- **ISD (0154)** = **Involving Sub-Division**
  - The parcel is **split into multiple sub-divisions**
  - Requires field inspection
  - Requires SD sketch preparation
  - Full workflow: Application → SIS → SD → DIS → Tahsildar

- **NISD (0153)** = **Not Involving Sub-Division**
  - Simple patta transfer **without creating new sub-divisions**
  - No field visit (document verification only)
  - Short workflow: Application → SIS → Zonal Level Tahsildar

- **MERGE (0155)** = Merge application
  - Multiple survey numbers/sub-divisions combined
  - Follows ISD workflow

---

## Files Checked ✓

### Documentation Files - backend/documents/

| File | Status | Notes |
|------|--------|-------|
| `faq_english.txt` | ✅ CORRECT | Lines 26, 29: Correct expansions |
| `faq_tamil.txt` | ✅ N/A | Tamil FAQ doesn't expand acronyms |
| `workflow_guide.txt` | ✅ CORRECT | Lines 17, 74: "Involving Sub-Division" and "Not Involving Sub-Division" |
| `database_structure_reference.txt` | ✅ CORRECT | Uses "ISD, NISD, or MERGE" without expansion |
| `survey_manual.txt` | ✅ N/A | No ISD/NISD acronym expansion |
| `district_codes.txt` | ✅ N/A | No ISD/NISD mentions |
| `land_rules.txt` | ✅ N/A | No ISD/NISD mentions |
| `sis_upload_checklist.txt` | ✅ N/A | No ISD/NISD mentions |
| `tamilnilam_urban_services_and_districts.txt` | ✅ N/A | No ISD/NISD mentions |

### Project Documentation Files

| File | Status | Notes |
|------|--------|-------|
| `AGENTS.md` | ✅ CORRECT | Line 157: "ISD (`0154`) — **Involving Sub-Division**" ✓<br>Line 159: "NISD (`0153`) — **Not Involving Sub-Division**" ✓ |
| `CLAUDE.md` | ✅ CORRECT | Line 262: "ISD (`0154`) — **Involving Sub-Division**" ✓<br>Line 264: "NISD (`0153`) — **Not Involving Sub-Division**" ✓ |
| `DISTRICT_HANDLING.md` | ✅ N/A | No ISD/NISD mentions |
| `DIGITAL_SIGNATURE_NOTES.md` | ✅ N/A | No ISD/NISD mentions |

### Code Files

| File | Status | Notes |
|------|--------|-------|
| `backend/models.py` | ✅ CORRECT | Line 8: CheckConstraint comment correctly states application types |
| `backend/services/chatbot.py` | ✅ CORRECT | Line 4822-4832: Correct expansions in service code data |
| `backend/services/rag.py` | ✅ CORRECT | **Lines 2081-2086**: Correct definitions<br>**Line 2123**: CRITICAL NOTE: "ISD is NEVER 'Individual Sub-Division' and NISD is NEVER 'Non-Individual'" ✓ |
| `backend/services/postgres.py` | ✅ N/A | No expansions, uses ISD/NISD acronyms only |
| `backend/sample_db/README.md` | ✅ CORRECT | Line 82, 109: Correctly states service codes |
| `backend/sample_db/verify_sample_db.py` | ✅ CORRECT | Line 109: Comment states "0153 = NISD, 0154 = ISD" (no incorrect expansion) |

### Test Files

| File | Status | Notes |
|------|--------|-------|
| `_scratch_isd.py` | ✅ **FIXED** | Line 10: Changed "is isd individual sub division" → "is isd involving sub division" |

---

## Key Findings

### ✅ All Files Now Correct

1. **FAQs** use correct terminology consistently
2. **Workflow Guide** clearly explains both types with correct expansions
3. **Main documentation** (AGENTS.md, CLAUDE.md) has accurate definitions
4. **Code files** have correct definitions and a critical safeguard note
5. **Test file `_scratch_isd.py`** had ONE incorrect test question - **FIXED**

### 🔧 Changes Made

1. **`_scratch_isd.py`** (Line 10):
   - ❌ Before: `"is isd individual sub division"`
   - ✅ After: `"is isd involving sub division"`

---

## Critical Safeguard Found

In **`backend/services/rag.py`** (Line 2123), there's an explicit safeguard note:

```python
# STRICT DATA RULES:
# 6. ALWAYS use the correct definitions and expansions: 
#    ISD = **Involving Sub-Division** (0154, creates new sub-divisions), 
#    NISD = **Not Involving Sub-Division** (0153, transfer only, creates none), 
#    MERGE = 0155 (combines sub-divisions). 
#    ISD is NEVER "Individual Sub-Division" and NISD is NEVER "Non-Individual".
```

This ensures the LLM always uses the correct terminology!

---

## Mnemonic to Remember

```
ISD = Involving Sub-Division     (splits land, needs field work)
NISD = Not Involving Sub-Division (just transfer, docs only)
```

**"Involving" = action verb = actively creating new sub-divisions**
**"Not Involving" = passive = no new sub-divisions created**

---

## Service Codes

| Code | Type | Expansion | Workflow |
|------|------|-----------|----------|
| 0153 | NISD | Not Involving Sub-Division | Short (no SD/DIS) |
| 0154 | ISD | Involving Sub-Division | Full (with SD/DIS) |
| 0155 | MERGE | Merge application | Follows ISD workflow |

---

## Where These Definitions Appear

### In FAQ (faq_english.txt):
```
Q: Is this application NISD or ISD?
A: The system determines if an application is ISD (Involving Sub-Division, 
   Service Code 0154) when it declares sub-division into multiple plots, or 
   NISD (Not Involving Sub-Division, Service Code 0153) for simple patta 
   transfers without creating new sub-divisions.
```

### In Workflow Guide (workflow_guide.txt):
```
=== ISD WORKFLOW (Involving Sub-Division - Service Code: 0154) ===
...
=== NISD WORKFLOW (Not Involving Sub-Division - Service Code: 0153) ===
```

### In AGENTS.md:
```
### Application Types
- **ISD** (`0154`) — **Involving Sub-Division**: the parcel is split, so the 
  file needs a field inspection and an SD sketch.
- **NISD** (`0153`) — **Not Involving Sub-Division**: a straight patta transfer 
  of the whole survey number, no new sub-division and no field visit.
```

### In RAG.py (Critical Safeguard):
```python
# ISD is NEVER "Individual Sub-Division" and NISD is NEVER "Non-Individual".
```

---

## Verification Complete

**Conclusion:** Found and fixed 1 incorrect test question in `_scratch_isd.py`. All documentation, code, and critical safeguards use the correct definitions.

- ISD = Involving Sub-Division ✓
- NISD = Not Involving Sub-Division ✓
- Service codes correctly mapped ✓
- Workflow distinctions clearly explained ✓
- LLM safeguard in place to prevent incorrect expansions ✓
