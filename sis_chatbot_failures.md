# SIS Chatbot -- Failed-Test Analysis (Deliverable C)

34 of 150 questions FAILED (material error, incomplete critical answer, or wrong-record
answer -- no fabricated dates/records were found anywhere in this run, including Q150, the
mandatory hallucination test, which PASSED).

## Q019 -- Application -- Severity: High

**Question:** Is 2026/0154/02/012547 marked overdue?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
Application 2026/0154/02/012547 (ISD) was submitted on 2026-08-05 and is 47 working days old (Monday-Friday; public holidays are not excluded). The ISD service SLA is 30-35 working days; it is 12 working days past the upper limit of 35.
```

**Expected / why wrong:** The chatbot computes '47 working days old... 12 working days past the SLA limit' and reads as an overdue warning for a REJECTED (closed) file. applications.is_overdue is actually FALSE (the overdue clock only applies to OPEN ISD files awaiting a field visit, per the documented SLA rule) -- the file's own stored flag is never consulted or stated.

**Verification evidence:** applications.is_overdue = false; app current_status='rejected' (closed, not SLA-tracked)

**Likely failure layer:** Answer generation logic (SLA/overdue computed without checking the stored flag)

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q027 -- Dates -- Severity: Medium

**Question:** On what date did the first workflow action occur for 2026/0154/02/012547?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
2026/0154/02/012547 was last updated on 2026-09-23 05:10 — the time of its latest workflow action.
```

**Expected / why wrong:** Answered with the LAST update time (2026-09-23 05:10) to a question asking for the FIRST workflow action -- the two are conflated. The first hop was 2026-08-05 13:10:29 UTC (2026-08-05 18:40 IST).

**Verification evidence:** workflow_history ORDER BY performed_at ASC LIMIT 1 = 2026-08-05 13:10:29+00:00

**Likely failure layer:** Field-level answer generation (routed to the right place, wrong/incomplete field extracted)

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q028 -- Dates -- Severity: Medium

**Question:** When did the SIS recommendation happen for 2026/0154/02/012547?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
<div class='table-intro'>No full-field patta transfer record is held for application 2026/0154/02/012547. Only an NISD (0153) file carries one.</div>
```

**Expected / why wrong:** 'No full-field patta transfer record...' answers a DIFFERENT, NISD-only field (fulltransfer_qa's SIS-recommendation column) instead of recognising that workflow_history itself (the right table for an ISD file) has no recommendation flag on any hop. Not false, but answers the wrong record for this file's service code.

**Verification evidence:** workflow_history.remarks IS NULL for all hops; full_field_patta_transfer_urban has no row for an ISD file by design

**Likely failure layer:** Field-level answer generation (routed to the right place, wrong/incomplete field extracted)

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q029 -- Dates -- Severity: Medium

**Question:** When did 2026/0154/02/012547 move from SIS to another stage?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
Details for application 2026/0154/02/012547:
• **Submission Date**: 2026-08-05
• **Current Stage**: Rejected
```

**Expected / why wrong:** Returns a generic details snippet (submission date + current stage) and never states the SIS->SD transition date (2026-09-22 19:11 IST) the question asked for.

**Verification evidence:** workflow_history SIS->SD row performed_at = 2026-09-22 13:41:25+00:00 = 19:11 IST

**Likely failure layer:** Field-level answer generation (routed to the right place, wrong/incomplete field extracted)

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q038 -- Dates -- Severity: Medium

**Question:** Which application in ward 001 was submitted most recently?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
Ward 001 129, Ward 005 19, Ward 009 4 (of 152 in your jurisdiction). Ward 001 leads Ward 009 by 125.
```

**Expected / why wrong:** Answered a completely different question -- ward application-count comparison ('Ward 001 129, Ward 005 19...') -- instead of naming the most recently submitted application IN ward 001 (expected one of the 2026-09-24 filings, e.g. 2026/0154/02/015526).

**Verification evidence:** service_register_entries WHERE ward='001' ORDER BY submission_date DESC: 2026-09-24 rows

**Likely failure layer:** Intent detection / routing

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q039 -- Dates -- Severity: Medium

**Question:** What was the earliest submission date for 0158?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
Service code 0158 = Modification / Anadeenam (MODIFICATION) — TAMILNILAM urban service, field visit required. Files under it are held in your register.
```

**Expected / why wrong:** Gives the service-code DEFINITION (what 0158 is) instead of a DATE -- never answers 'what was the earliest submission date', which is 2026-01-01.

**Verification evidence:** min(submission_date) WHERE service_code='0158' = 2026-01-01

**Likely failure layer:** Conversation/follow-up or answer-composition

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q040 -- Dates -- Severity: Medium

**Question:** Are there applications from the same month but different submission channels?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
<div><strong>1. Are there applications from the same month</strong></div>
<div class='table-intro'>Found <strong>44</strong> pending application(s) across every service code in your jurisdiction (0153 (9), 0154 (10), 0156 (1), 0158 (10), 0165 (7), 0178 (7)). By channel: 36 CSC, 4 citizen portal, 4 Sub-Registrar.</div><table class='data-table'><thead><tr><th>Application No.</th><th>Type</th><th>Survey No.</th><th>Sub-Divisions</th><th>Status</th><th>Stage</th><th>Submitted</th><th>Ward</th><th>Block</th></tr></thead><tbody><tr><td><a href='javascript:void(0)' class='app-table-link' onclick="window.handleAppClick('2026/0158/02/000905')" style='color:#2563eb;text-decoration:underline;cursor:pointer;font-weight:600;'>2026/0158/02/000905</a></td><td>Modification / Anadeenam</td><td>22</td><td>0
```

**Expected / why wrong:** The response is garbled -- a fragment ('1. Are there applications from the same month') followed by an unrelated 44-pending-applications dump. The big-message splitter appears to have misparsed this as a numbered sub-question list; it never actually answers the question.

**Verification evidence:** raw response text is visibly malformed / non-responsive

**Likely failure layer:** Conversation/follow-up or answer-composition

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q041 -- ServiceCode -- Severity: High

**Question:** What service does code 0153 represent?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
Please specify the application number you are asking about — for example 2026/0154/28/NNNNNN.
```

**Expected / why wrong:** 'What service does code 0153 represent?' was answered with 'Please specify the application number you are asking about' -- misrouted to the application-number gate entirely, even though service-code lookup for all 30 SIS_URBAN_SERVICES codes (incl. 0153) is a documented, previously-fixed deterministic feature. This exact phrasing variant ('What service does code X represent') is not recognised by that feature's cue matching.

**Verification evidence:** SIS_URBAN_SERVICES['0153'] exists and is the documented source for this answer

**Likely failure layer:** Intent detection / routing

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q042 -- ServiceCode -- Severity: High

**Question:** What service does code 0154 represent?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
Please specify the application number you are asking about — for example 2026/0154/28/NNNNNN.
```

**Expected / why wrong:** Same misroute as Q041, for code 0154 -- 'Please specify the application number...' instead of the ISD definition.

**Verification evidence:** SIS_URBAN_SERVICES['0154'] exists

**Likely failure layer:** Intent detection / routing

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q064 -- ServiceCompare -- Severity: Medium

**Question:** How is 0164 different from 0161?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
0164 (ULC Land Subdivision): 13 files (3 rejected); 0161 (Street Master): 0 files (0 rejected). 0164 has 13 more.
```

**Expected / why wrong:** 'How is 0164 different from 0161' was answered with a REGISTER FILE-COUNT comparison (13 files vs 0 files) instead of the actual conceptual difference: 0164 is ULC Land Subdivision (requires a field visit), 0161 is Street Master (does not). This is the register count path (compare_applications), not the service-code definition path (service_code_guide).

**Verification evidence:** SIS_URBAN_SERVICES['0164'] vs ['0161']: different purpose, different field-visit requirement

**Likely failure layer:** Intent detection / routing

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q065 -- ServiceCompare -- Severity: Medium

**Question:** How is 0178 different from 0185?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
0178 (F-Line (Urban Demarcation)): 15 files (0 rejected); 0185 (F-Line Appeal): 0 files (0 rejected). 0178 has 15 more.
```

**Expected / why wrong:** Same bug as Q064 -- '0178 vs 0185' answered with a file-count comparison (15 vs 0) instead of explaining demarcation (0178) vs appeal (0185).

**Verification evidence:** SIS_URBAN_SERVICES['0178']='F-Line (Urban Demarcation)' vs ['0185']='F-Line Appeal'

**Likely failure layer:** Intent detection / routing

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q066 -- ServiceCompare -- Severity: Medium

**Question:** What is the difference between 0156 and 0157?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
0156 (TSLR Extract with Sketch): 11 files (0 rejected); 0157 (TSLR Extract Only): 0 files (0 rejected). 0156 has 11 more.
```

**Expected / why wrong:** Same bug again -- '0156 vs 0157' answered with a file-count comparison (11 vs 0) instead of explaining 'with Sketch' vs 'Extract Only'. Three for three: 'difference between two service codes' consistently routes to the register-count comparison instead of the code-definition guide.

**Verification evidence:** SIS_URBAN_SERVICES['0156']='TSLR Extract with Sketch' vs ['0157']='TSLR Extract Only'

**Likely failure layer:** Intent detection / routing

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q073 -- Counts -- Severity: Medium

**Question:** Which service has the smallest non-zero count?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
Please specify the application number you are asking about — for example 2026/0154/28/NNNNNN.
```

**Expected / why wrong:** 'Please specify the application number...' -- misrouted to the application-number gate instead of a register-wide analytics comparison across service codes.

**Verification evidence:** 0159 is the smallest non-zero code (2 applications)

**Likely failure layer:** Intent detection / routing

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q083 -- Counts -- Severity: Medium

**Question:** Which service has the highest rejection count?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
Please specify the application number you are asking about — for example 2026/0154/28/NNNNNN.
```

**Expected / why wrong:** Same misroute as Q073 -- analytics ('highest rejection count') read as a request for an application number.

**Verification evidence:** 0153 has the most rejections (10)

**Likely failure layer:** Intent detection / routing

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q088 -- Filtering -- Severity: Medium

**Question:** Show approved applications submitted in 2026.

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
<div class='table-intro'>Found <strong>19</strong> application(s):</div><table class='data-table'><thead><tr><th>Application No.</th><th>Submitted</th></tr></thead><tbody><tr><td><a href='javascript:void(0)' class='app-table-link' onclick="window.handleAppClick('2026/0154/02/012766')" style='color:#2563eb;text-decoration:underline;cursor:pointer;font-weight:600;'>2026/0154/02/012766</a></td><td>2026-08-08</td></tr><tr><td><a href='javascript:void(0)' class='app-table-link' onclick="window.handleAppClick('2026/0154/02/012834')" style='color:#2563eb;text-decoration:underline;cursor:pointer;font-weight:600;'>2026/0154/02/012834</a></td><td>2026-08-10</td></tr><tr><td><a href='javascript:void(0)' class='app-table-link' onclick="window.handleAppClick('2026/0154/02/013180')" style='color:#2563eb
```

**Expected / why wrong:** 'Found 19 application(s)' for 'approved applications submitted in 2026' does not match either grounded figure (82 approved register-wide, or 10 for 0154/ISD alone) and the table shown has no Status column at all, so it cannot be confirmed the result is even filtered to approved. All 152 register rows are already 2026-only, so the 2026 filter should be a no-op and the answer should read close to 82. ROOT CAUSE UNCONFIRMED from the evidence available.

**Verification evidence:** register_status_counts: approved=82 jurisdiction-wide; response shows 19 ISD-only rows, no status column

**Likely failure layer:** ROOT CAUSE UNCONFIRMED

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q096 -- Filtering -- Severity: Medium

**Question:** How many 0158 files came through each channel?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
0158 (Modification / Anadeenam): 22 files in total, including rejected — 2 rejected, 20 not rejected.
```

**Expected / why wrong:** Answered with a STATUS breakdown (2 rejected / 20 not rejected) instead of a CHANNEL breakdown -- the word 'channel' in the question was not used; 0158 is in fact 100% CSC (22/22), which the answer never states.

**Verification evidence:** code_channel_counts: 0158 -> CSC 22, all others 0

**Likely failure layer:** Intent detection / routing

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q105 -- Jurisdiction -- Severity: High

**Question:** How many applications are in ward 001?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
<div class='table-intro'>Found <strong>105</strong> application(s) in Ward 001 across every service code in your jurisdiction (0153 (18), 0154 (20), 0156 (11), 0158 (20), 0159 (1), 0162 (9), 0164 (10), 0165 (1), 0178 (15)). By channel: 82 CSC, 10 citizen portal, 13 Sub-Registrar. 26 rejected application(s) matching this are left out; ask for rejected applications to see them.</div>
```

**Expected / why wrong:** '105... 26 rejected application(s) matching this are left out' -- the EXCLUSION COUNT (26) is wrong for this filter: ward 001's actual rejected count is 24, not 26. 105+24=129 (ward 001's true total); the response instead implies 105+26=131. The note appears to always report the officer's GLOBAL rejected total rather than the count rejected BY THIS FILTER.

**Verification evidence:** service_register_entries WHERE ward='001' AND status='rejected' = 24 (verified), not the global 26

**Likely failure layer:** Database filtering / scoped aggregation

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q107 -- Jurisdiction -- Severity: Medium

**Question:** Which blocks in ward 001 contain applications?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
<div class='table-intro'>Found <strong>38</strong> pending application(s) in Ward 001 across every service code in your jurisdiction (0153 (9), 0154 (10), 0156 (1), 0158 (10), 0165 (1), 0178 (7)). By channel: 30 CSC, 4 citizen portal, 4 Sub-Registrar.</div><table class='data-table'><thead><tr><th>Application No.</th><th>Type</th><th>Survey No.</th><th>Sub-Divisions</th><th>Status</th><th>Stage</th><th>Submitted</th><th>Ward</th><th>Block</th></tr></thead><tbody><tr><td><a href='javascript:void(0)' class='app-table-link' onclick="window.handleAppClick('2026/0158/02/000905')" style='color:#2563eb;text-decoration:underline;cursor:pointer;font-weight:600;'>2026/0158/02/000905</a></td><td>Modification / Anadeenam</td><td>22</td><td>0</td><td>Pending</td><td>Open</td><td>2026-06-12</td><td>Oraka
```

**Expected / why wrong:** Answered with the ward-001 PENDING count (38) instead of listing which blocks in ward 001 contain applications -- no block numbers are named at all.

**Verification evidence:** ward001_blocks: 0001(11), 0008(9), 0055(9), 0025(8), 0003(8), ... (many blocks)

**Likely failure layer:** Intent detection / routing

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q108 -- Jurisdiction -- Severity: High

**Question:** How many applications are in block 0055?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
<div class='table-intro'>Found <strong>3</strong> application(s) in Block 0055 across every service code in your jurisdiction (0156 (1), 0158 (1), 0162 (1)). By channel: 3 CSC. 26 rejected application(s) matching this are left out; ask for rejected applications to see them.</div>
```

**Expected / why wrong:** Same exclusion-count bug as Q105: '3... 26 rejected application(s) matching this are left out' -- block 0055's actual rejected count is 6, not 26. 3+6=9 (block 0055's true total, verified independently); the response's '26' is the officer's global rejected total, wrongly applied to a block-scoped filter.

**Verification evidence:** service_register_entries WHERE block='0055' AND status='rejected' = 6 (verified), not 26

**Likely failure layer:** Database filtering / scoped aggregation

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q113 -- Jurisdiction -- Severity: High

**Question:** Does 2026/0154/02/012547 fall within my jurisdiction?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
Application 2026/0154/02/012547 is of type: ISD
```

**Expected / why wrong:** 'Application 2026/0154/02/012547 is of type: ISD' never answers the yes/no jurisdiction question that was actually asked.

**Verification evidence:** the app IS in ward 001, within this officer's jurisdiction

**Likely failure layer:** Conversation/follow-up or answer-composition

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q122 -- Channel -- Severity: High

**Question:** Which applications share the same submission IP as 2026/0154/02/012547?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
<div class='data-table-card'><div class='data-table-header'><div class='data-table-title-section'><span class='data-table-icon'>📋</span><h3 class='data-table-title'>Old-owner record vs sub-division record for 2026/0154/02/012547 (same application id in both)</h3><span class='data-table-count'>1 records</span></div><button class='btn-copy-table' onclick='window.copyTableData(this)'>Copy</button></div><div class='data-table-container'><table class='data-table'><thead><tr><th>FIELD</th><th>DETAILS</th></tr></thead><tbody class='table-body-rows'><tr><td>Land extent / share vs area</td><td>not recorded (share: not recorded)  |  110.5 sq.m</td></tr><tr><td colspan='2'>No conflicting values found; 1 field(s) could not be compared because one side is not recorded.</td></tr><tr><td colspan='2'>(✔ m
```

**Expected / why wrong:** Completely misrouted: the response is an 'Old-owner record vs sub-division record' comparison card, unrelated to submission IP addresses at all.

**Verification evidence:** raw response title: 'Old-owner record vs sub-division record for 2026/0154/02/012547'

**Likely failure layer:** Intent detection / routing

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q123 -- Channel -- Severity: Medium

**Question:** Which applications have a different submission IP from 2026/0154/02/012547?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
Application 2026/0154/02/012547 was submitted from IP address 117.218.227.225 (public internet).
```

**Expected / why wrong:** Only restates the app's OWN IP (117.218.227.225); never attempts to list OTHER applications with a different IP, which is what was asked. Not a hallucination (no false data given), but non-responsive.

**Verification evidence:** n/a -- open listing never attempted

**Likely failure layer:** Conversation/follow-up or answer-composition

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q129 -- Channel -- Severity: High

**Question:** What payment information is available for 2026/0154/02/012547?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
I could not find that particular detail for 2026/0154/02/012547 in the record. Here is what it does hold — Type: ISD, Status: Rejected, Stage: REJECTED, Applicant: Sumathi, Survey No: 7.
```

**Expected / why wrong:** 'I could not find that particular detail... Here is what it does hold' -- claims payment info is unavailable, even though fee_amount=650, challan_number=234 and payment_mode=treasury are all populated and were correctly retrieved by the NARROWER single-field questions (Q124/125/126, all PASS). The composite 'what payment information is available' framing fails where the individual field questions succeed.

**Verification evidence:** applications.fee_amount=650.00, challan_number='234', payment_mode='treasury' (all present, confirmed via Q124/125/126)

**Likely failure layer:** Field-level answer generation (routed to the right place, wrong/incomplete field extracted)

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q131 -- Workflow -- Severity: Medium

**Question:** Who performed the latest workflow action on 2026/0154/02/012547?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
2026/0154/02/012547 was last updated on 2026-09-23 05:10 — the time of its latest workflow action.
```

**Expected / why wrong:** Answers WHEN (last update time) but never WHO performed the action -- the desk that closed the file (SD) is never named, even as 'the register holds desks not names, and the desk was SD'.

**Verification evidence:** workflow_history last row from_stage='SD'

**Likely failure layer:** Field-level answer generation (routed to the right place, wrong/incomplete field extracted)

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q132 -- Workflow -- Severity: Medium

**Question:** What was the latest workflow remark on 2026/0154/02/012547?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
2026/0154/02/012547 was last updated on 2026-09-23 05:10 — the time of its latest workflow action.
```

**Expected / why wrong:** Same canned 'last updated' response as Q131 and Q027 and Q030 -- never addresses 'remark' at all; the correct answer is that all 4 workflow_history rows have remarks=NULL.

**Verification evidence:** workflow_history.remarks IS NULL for all 4 rows

**Likely failure layer:** Field-level answer generation (routed to the right place, wrong/incomplete field extracted)

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q134 -- Workflow -- Severity: Medium

**Question:** What was the SIS recommendation for 2026/0154/02/012547?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
<div class='table-intro'>No full-field patta transfer record is held for application 2026/0154/02/012547. Only an NISD (0153) file carries one.</div>
```

**Expected / why wrong:** Same misroute as Q028 -- answers about the NISD-only full-field-transfer SIS-recommendation field instead of this ISD app's own workflow_history (which has no recommendation flag).

**Verification evidence:** workflow_history.remarks IS NULL; full_field_patta_transfer_urban has no row for an ISD file

**Likely failure layer:** Field-level answer generation (routed to the right place, wrong/incomplete field extracted)

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q135 -- Workflow -- Severity: Medium

**Question:** What action came immediately before the current stage for 2026/0154/02/012547?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
Application 2026/0154/02/012547 is currently at: Rejected.
```

**Expected / why wrong:** 'Currently at: Rejected' restates the CURRENT stage; never names the PRIOR stage (SD) the question asked for.

**Verification evidence:** workflow_history 3rd row to_stage='SD', 4th row from_stage='SD' to_stage='REJECTED'

**Likely failure layer:** Field-level answer generation (routed to the right place, wrong/incomplete field extracted)

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q136 -- Workflow -- Severity: Medium

**Question:** What action came immediately after SIS for 2026/0154/02/012547?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
I could not find that particular detail for 2026/0154/02/012547 in the record. Here is what it does hold — Type: ISD, Status: Rejected, Stage: REJECTED, Applicant: Sumathi, Survey No: 7.
```

**Expected / why wrong:** 'I could not find that particular detail...' -- workflow_history clearly shows the hop after the first SIS entry (SIS->SIS, then SIS->SD); the data exists but was not retrieved for this phrasing.

**Verification evidence:** workflow_history rows 2 and 3

**Likely failure layer:** Field-level answer generation (routed to the right place, wrong/incomplete field extracted)

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q137 -- Workflow -- Severity: Medium

**Question:** How many workflow entries exist for 2026/0154/02/012547?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
I could not find that particular detail for 2026/0154/02/012547 in the record. Here is what it does hold — Type: ISD, Status: Rejected, Stage: REJECTED, Applicant: Sumathi, Survey No: 7.
```

**Expected / why wrong:** 'I could not find that particular detail...' for a simple count of workflow_history rows (4) -- the same table answers other workflow questions correctly elsewhere in this run.

**Verification evidence:** workflow_history count(*) = 4

**Likely failure layer:** Field-level answer generation (routed to the right place, wrong/incomplete field extracted)

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q139 -- Workflow -- Severity: High

**Question:** What was the final workflow transition for 2026/0154/02/012547?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
<div class='table-intro'>No F-Line (service code 0178) record is held for application 2026/0154/02/012547. F-Line fields (transaction status, boundaries, sketch, SIS / DIS verification) exist only for F-Line applications.</div>
```

**Expected / why wrong:** 'No F-Line (service code 0178) record is held for application 2026/0154/02/012547' -- a bizarre, unrelated misroute: this is an ISD (0154) file and the question never mentioned F-Line. Looks like a token collision between 'transition' (asked) and an F-Line-specific cue; exact mechanism not confirmed from the evidence available (ROOT CAUSE UNCONFIRMED).

**Verification evidence:** raw response names F-Line/0178, which has no connection to this question or this app's service code (0154)

**Likely failure layer:** Intent detection / routing

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q140 -- Workflow -- Severity: Medium

**Question:** On what date did 2026/0154/02/012547 reach its current stage?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
Details for application 2026/0154/02/012547:
• **Submission Date**: 2026-08-05
• **Current Stage**: Rejected
```

**Expected / why wrong:** Gives the SUBMISSION date (2026-08-05) and the current stage name, but never answers WHEN that stage (REJECTED) was reached -- that is 2026-09-22 (UTC) / 2026-09-23 05:10 IST.

**Verification evidence:** workflow_history last row performed_at

**Likely failure layer:** Field-level answer generation (routed to the right place, wrong/incomplete field extracted)

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q144 -- FLine -- Severity: Critical

**Question:** Who verified the F-Line application 2026/0178/02/000309?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
<div class='data-table-card'><div class='data-table-header'><div class='data-table-title-section'><span class='data-table-icon'>📋</span><h3 class='data-table-title'>2026/0178/02/000309: Service 0178 - F-Line (Urban Demarcation)</h3><span class='data-table-count'>5 records</span></div><button class='btn-copy-table' onclick='window.copyTableData(this)'>Copy</button></div><div class='data-table-container'><table class='data-table'><thead><tr><th>FIELD</th><th>DETAILS</th></tr></thead><tbody class='table-body-rows'><tr><td>Status</td><td>approved (Completed)</td></tr><tr><td>Submitted</td><td>2026-03-31</td></tr><tr><td>Survey no</td><td>93 / 1</td></tr><tr><td>Ward / block</td><td>001 / 0051</td></tr><tr><td>Channel</td><td>citizen</td></tr></tbody></table></div></div>
```

**Expected / why wrong:** Renders a generic 5-field summary card (status/submitted/survey/ward-block/channel) that omits the F-Line-specific verification fields entirely -- sis_username ('chn_chandru') is never surfaced, even though it is populated and CLAUDE.md documents F-Line verification fields as an explicitly handled case (fline_qa.py). 'Who verified' gets no name at all.

**Verification evidence:** f_line_service_urban.sis_username = 'chn_chandru' (verified, populated, never appears in the response)

**Likely failure layer:** Field-level answer generation (routed to the right place, wrong/incomplete field extracted)

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q145 -- FLine -- Severity: Critical

**Question:** What date was the F-Line 2026/0178/02/000309 verified?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
<div class='data-table-card'><div class='data-table-header'><div class='data-table-title-section'><span class='data-table-icon'>📋</span><h3 class='data-table-title'>2026/0178/02/000309: Service 0178 - F-Line (Urban Demarcation)</h3><span class='data-table-count'>5 records</span></div><button class='btn-copy-table' onclick='window.copyTableData(this)'>Copy</button></div><div class='data-table-container'><table class='data-table'><thead><tr><th>FIELD</th><th>DETAILS</th></tr></thead><tbody class='table-body-rows'><tr><td>Status</td><td>approved (Completed)</td></tr><tr><td>Submitted</td><td>2026-03-31</td></tr><tr><td>Survey no</td><td>93 / 1</td></tr><tr><td>Ward / block</td><td>001 / 0051</td></tr><tr><td>Channel</td><td>citizen</td></tr></tbody></table></div></div>
```

**Expected / why wrong:** Same generic-card gap as Q144 -- sis_verify_date (2026-06-30 20:58:29) is populated but never appears in the response; the card's 5 fields (status/submitted/survey/ward-block/channel) do not include it.

**Verification evidence:** f_line_service_urban.sis_verify_date = 2026-06-30 20:58:29 (verified, never appears in the response)

**Likely failure layer:** Field-level answer generation (routed to the right place, wrong/incomplete field extracted)

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

## Q146 -- FLine -- Severity: Medium

**Question:** What is the F-Line transaction status for 2026/0178/02/000309?

**Actual response (verbatim, HTML markup included where the UI renders a card):**
```
<div class='table-intro'>No full-field patta transfer record is held for application 2026/0178/02/000309. Only an NISD (0153) file carries one.</div>
```

**Expected / why wrong:** Same misroute pattern as Q028/Q134 -- 'No full-field patta transfer record...' (an NISD-only field) instead of this F-Line app's own transaction_status (which is genuinely NULL, but the answer never says so).

**Verification evidence:** f_line_service_urban.transaction_status IS NULL for this record; full_field_patta_transfer_urban has no row for an F-Line file by design

**Likely failure layer:** Field-level answer generation (routed to the right place, wrong/incomplete field extracted)

**Regression test to run after fixing:** re-ask this exact question against
`2026/0154/02/012547` (or the named application) and confirm the response contains the value in
"Verification evidence" above; add it as a permanent case alongside the project's existing
`test_record_qa.py` / `test_context_regression.py` suites.

---

