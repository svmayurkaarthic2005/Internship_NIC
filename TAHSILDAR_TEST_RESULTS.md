# Tahsildar (ZDT/HQDT) Test Results

## Test Questions Created
✅ **`test_questions_tahsildar.txt`** — 50 test questions covering:
- Tahsildar approval & digital signature (15 questions)
- Tahsildar workflow & stages (10 questions)
- Tahsildar remarks & recommendations (10 questions)
- Tahsildar rejection reasons (10 questions)
- Tahsildar authority & process (5 questions)

## Database Verification Results

### 1. Workflow History - Tahsildar Stage
- **Applications forwarded TO Tahsildar**: 167
- **Applications processed FROM Tahsildar**: 165
- **Workflow**: SIS → TAHSILDAR → COMPLETED/REJECTED

### 2. Current Applications at Tahsildar
- **Currently at Tahsildar stage**: 2 applications
- **Approved by Tahsildar (completed)**: 0 (moved to COMPLETED stage)
- **Rejected by Tahsildar (completed)**: 0 (moved to REJECTED stage)

### 3. Workflow Transitions
**Incoming to Tahsildar:**
- SIS → TAHSILDAR: 167 times (100%)

**Outgoing from Tahsildar:**
- TAHSILDAR → COMPLETED: 129 times (78.2%)
- TAHSILDAR → REJECTED: 36 times (21.8%)

### 4. Sample Recent Workflows
```
2026/0153/28/001876 - TO Tahsildar - 2026-07-10 - Action: Forwarded
2026/0153/28/001839 - TO Tahsildar - 2026-07-09 - Action: Forwarded
2026/0153/28/001720 - FROM Tahsildar - 2026-07-05 - Action: Forwarded
2026/0153/28/001720 - TO Tahsildar - 2026-06-30 - Action: Forwarded
2026/0153/28/001398 - FROM Tahsildar - 2026-06-12 - Action: Forwarded
```

## Chatbot Query Support Status

### ✅ Already Working (via existing intents)

1. **"Show applications at Tahsildar stage"**
   - Intent: `pending_applications` with stage filter
   - Query: Applications where `current_stage = 'TAHSILDAR'`

2. **"Is application 2026/0153/28/001876 at Tahsildar stage?"**
   - Intent: `application_status`
   - Returns: current_stage field

3. **"Which applications are currently with the Tahsildar?"**
   - Intent: `pending_applications`
   - Filter: `current_stage = 'TAHSILDAR'`

4. **"How many applications are waiting for Tahsildar approval?"**
   - Intent: `officer_workload` or `pending_applications`
   - Count: Applications at TAHSILDAR stage

5. **"Show applications approved by Tahsildar this month"**
   - Intent: `pending_applications` with date filter
   - Status: `approved`, previous stage: `TAHSILDAR`

### ⚠️ Partially Working (need enhancement)

1. **"Did the Tahsildar approve application 2026/0154/28/001167?"**
   - **Current**: Returns generic application status
   - **Needed**: Check workflow_history for TAHSILDAR → COMPLETED transition
   - **Enhancement**: Add Tahsildar-specific approval check

2. **"What remarks did the Tahsildar make?"**
   - **Current**: Falls to general_query (LLM may hallucinate)
   - **Needed**: Query `workflow_history.remarks` where `from_stage = 'TAHSILDAR'`
   - **Enhancement**: Add `tahsildar_remarks_check` intent

3. **"Why did the Tahsildar reject this application?"**
   - **Current**: Falls to `rejection_info` intent (generic)
   - **Needed**: Query `workflow_history.rejection_reason` where `from_stage = 'TAHSILDAR'`
   - **Enhancement**: Filter rejection info by stage

4. **"When did application reach Tahsildar?"**
   - **Current**: Returns last workflow transition (may not be specific)
   - **Needed**: Query `workflow_history.performed_at` where `to_stage = 'TAHSILDAR'`
   - **Enhancement**: Add stage-specific timeline queries

### ❌ Not Yet Implemented

1. **"Has the Tahsildar generated the order?"**
   - **Issue**: No `order_generated` field in database
   - **Workaround**: Check if `current_stage = 'COMPLETED'` and previous stage was `TAHSILDAR`
   - **Enhancement**: Add order generation tracking

2. **"Show Tahsildar's digital signature status"**
   - **Issue**: No digital signature data ingested (CSV only)
   - **Status**: Covered by digital signature test questions (separate feature)
   - **Enhancement**: Implement digital signature ingestion

3. **"What is the Tahsildar's recommendation status?"**
   - **Issue**: CSV fields `tahsildar_recommendation`, `tahsildar_remarks`, `tahsildar_recommendation_reason` not in ORM models
   - **Enhancement**: Add PattaTransfer model with Tahsildar fields

## Key Database Fields

### WorkflowHistory Table
```python
- from_stage: String (e.g., 'SIS', 'TAHSILDAR')
- to_stage: String (e.g., 'TAHSILDAR', 'COMPLETED', 'REJECTED')
- action: String (e.g., 'Forwarded', 'Approved', 'Rejected')
- remarks: Text (officer comments)
- rejection_reason: Text (why rejected)
- performed_at: Timestamp
- performed_by_officer_id: UUID
```

### Application Table
```python
- current_stage: String ('SIS', 'SD', 'TAHSILDAR', 'COMPLETED', 'REJECTED')
- current_status: String ('pending', 'in_progress', 'approved', 'rejected', 'escalated')
```

### CSV Fields (Not Yet in ORM)
```
- tahsildar_receipt_date
- tahsildar_recommendation
- tahsildar_remarks
- tahsildar_recommendation_reason
```

## Tahsildar Workflow Summary

```
┌──────────┐
│   SIS    │ (Sub Inspector Surveyor)
│  Stage   │ - Receives application
└────┬─────┘ - Conducts field visit (ISD)
     │       - Forwards to Tahsildar
     │
     ▼
┌──────────┐
│TAHSILDAR │ (Zonal/HQ Deputy Tahsildar)
│  Stage   │ - Final approval authority
└────┬─────┘ - Applies digital signature (DSC)
     │       - Generates patta order
     │
     ├──────────────┬──────────────┐
     ▼              ▼              ▼
┌──────────┐  ┌──────────┐  ┌──────────┐
│COMPLETED │  │ REJECTED │  │  (Rare)  │
│  (78%)   │  │  (22%)   │  │  REVERT  │
└──────────┘  └──────────┘  └──────────┘
```

## Recommendations for Enhancement

### 1. Add Tahsildar-Specific Intent Handler

```python
# backend/services/rag.py

elif intent == "tahsildar_decision":
    # Extract application number
    app_number = extract_application_number(message)
    if app_number:
        # Query workflow_history for Tahsildar actions
        structured_data = await get_tahsildar_decision(db, app_number, officer)
```

### 2. Add Tahsildar Query Function

```python
# backend/services/postgres.py

async def get_tahsildar_decision(
    db: AsyncSession,
    application_number: str,
    officer: OfficerContext
) -> Dict[str, Any]:
    """Get Tahsildar's decision, remarks, and timing for an application"""
    
    # Get application
    app_query = select(Application).where(
        Application.application_number == application_number
    )
    result = await db.execute(app_query)
    application = result.scalar_one_or_none()
    
    if not application:
        return {"found": False, "error": "Application not found"}
    
    # Get Tahsildar workflow actions
    tahsildar_query = select(WorkflowHistory).where(
        and_(
            WorkflowHistory.application_id == application.id,
            or_(
                WorkflowHistory.from_stage == 'TAHSILDAR',
                WorkflowHistory.to_stage == 'TAHSILDAR'
            )
        )
    ).order_by(WorkflowHistory.performed_at.asc())
    
    result = await db.execute(tahsildar_query)
    tahsildar_actions = result.scalars().all()
    
    # Parse actions
    reached_tahsildar = None
    left_tahsildar = None
    decision = None
    remarks = None
    rejection_reason = None
    
    for action in tahsildar_actions:
        if action.to_stage == 'TAHSILDAR':
            reached_tahsildar = action.performed_at
        if action.from_stage == 'TAHSILDAR':
            left_tahsildar = action.performed_at
            decision = "Approved" if action.to_stage == 'COMPLETED' else "Rejected"
            remarks = action.remarks
            rejection_reason = action.rejection_reason
    
    return {
        "found": True,
        "application_number": application_number,
        "reached_tahsildar": reached_tahsildar.isoformat() if reached_tahsildar else None,
        "left_tahsildar": left_tahsildar.isoformat() if left_tahsildar else None,
        "decision": decision,
        "remarks": remarks,
        "rejection_reason": rejection_reason,
        "currently_at_tahsildar": application.current_stage == 'TAHSILDAR',
        "query_type": "Tahsildar Decision"
    }
```

### 3. Add Intent Detection

```python
# backend/services/rag.py (in parse_intent function)

# Tahsildar decision/approval check
if any(w in msg for w in ["tahsildar", "thasildar", "zdt", "hqdt", "deputy tahsildar"]) and \
   any(w in msg for w in ["approve", "approved", "reject", "rejected", "decision", 
                           "sign", "signed", "remarks", "reason"]):
    return "tahsildar_decision"
```

## Test Coverage

✅ **Workflow queries** - Already working via `application_status` and `pending_applications`
✅ **Stage filters** - Working via existing intents
⚠️ **Specific approval/rejection** - Needs enhancement for stage-specific filtering
⚠️ **Tahsildar remarks** - Needs dedicated handler
❌ **Order generation status** - Not tracked in current database
❌ **Digital signature** - Separate feature (CSV data not ingested)

## Conclusion

The chatbot can already answer **basic Tahsildar queries** using existing intents:
- "Show applications at Tahsildar"
- "Is application X at Tahsildar stage?"
- "How many applications with Tahsildar?"

For **advanced queries** (approval reasons, remarks, decision timeline), add:
1. `tahsildar_decision` intent
2. `get_tahsildar_decision()` query function
3. Stage-specific filtering in existing handlers

**Priority**: Medium (most queries work via existing workflow_history lookups)
