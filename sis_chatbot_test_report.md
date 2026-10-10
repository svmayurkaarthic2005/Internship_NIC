# SIS Chatbot -- 150-Question Manual QA Report

**Date:** 2026-10-09
**Officer used for testing:** marun_chn@sis.tn.gov.in (jurisdiction: wards 001 / 005 / 009, Ambattur, Chennai)
**Interaction method:** `backend.services.chatbot.process_chat()` -- the exact function the
`/api/v1/chat/stream` endpoint calls, invoked directly against the live PostgreSQL database
(no mock, no stub, no alternate implementation). Same code path the browser UI uses.
**Database verification:** every expected value below was computed with read-only SQL against
the live `sis_chatbot_db` tables (`applications`, `service_register_entries`, `workflow_history`,
`field_visits`, `f_line_service_urban`, `tslr_sketch_urban`, `applicants`, `wards`, `blocks`) or
against the project's own `SIS_URBAN_SERVICES` reference table. No value was invented.

## Deliverable A -- Executive Summary

| Metric | Value |
|---|---|
| Total questions | 150 |
| Executed | 150 (100%) |
| Blocked (no suitable test data / inaccessible) | 0 |
| PASS | 110 |
| FAIL | 34 |
| PARTIAL | 6 |
| **Pass rate (PASS / 150 executed)** | **73.3%** |
| Pass rate counting PARTIAL as acceptable | 77.3% (116/150) |

**Mandatory Q150 (missing-TSLR-record hallucination test): PASS.** Asked for the TSLR
challan date of an ISD application (2026/0154/02/012547, service code 0154) that genuinely has no
row in `tslr_sketch_urban`. The assistant correctly refused -- *"No TSLR sketch record is held for
application 2026/0154/02/012547. Only a TSLR sketch application (service code 0156) carries one."*
-- naming the real reason instead of inventing a date. Across all 150 questions, including every
other missing-record and missing-field case tested, **zero fabricated dates or invented records**
were observed. The chatbot's refusal/"not recorded" discipline is the strongest result of this run.

**Highest-risk defects (see Deliverable C for full detail):**

| ID | Severity | Summary |
|---|---|---|
| Q144 | Critical | F-Line verifier name never surfaced, despite being on record |
| Q145 | Critical | F-Line verification date never surfaced, despite being on record |
| Q105 | High | "N rejected left out" note reports the wrong (global, not ward-scoped) count |
| Q108 | High | Same bug as Q105, for a block-scoped filter |
| Q041/Q042 | High | "What service does code X represent?" misroutes to "give me an application number" |
| Q113 | High | A direct jurisdiction yes/no question gets no yes/no answer |
| Q122 | High | "Same submission IP" misroutes to an unrelated owner-record comparison |
| Q129 | High | Composite "payment information" claims not-found; the same fields PASS individually |
| Q019 | High | Computes an SLA-overdue narrative for a REJECTED (closed, non-overdue) file |

**Category breakdown is the clearest signal in this run:** `Workflow` questions about one named
application (Q131-140) pass only 20% of the time (2/10) -- the worst of any section -- while the
same application's basic facts (`Application`, 95%), its TSLR record (`TSLR`, 100%) and its
service-code identity (`ServiceCode`, 86.7%) are handled well. `F-Line` is the next weakest at 50%,
driven by the two Critical findings above.

## Deliverable B(i) -- Category breakdown

| Category | Tests | Pass | Fail | Partial | Accuracy |
|---|---:|---:|---:|---:|---:|
| Application | 20 | 19 | 1 | 0 | 95.0% |
| Dates | 20 | 13 | 6 | 1 | 65.0% |
| ServiceCode | 15 | 13 | 2 | 0 | 86.7% |
| ServiceCompare | 15 | 10 | 3 | 2 | 66.7% |
| Counts | 15 | 11 | 2 | 2 | 73.3% |
| Filtering | 15 | 13 | 2 | 0 | 86.7% |
| Jurisdiction | 15 | 10 | 4 | 1 | 66.7% |
| Channel | 15 | 12 | 3 | 0 | 80.0% |
| Workflow | 10 | 2 | 8 | 0 | 20.0% |
| FLine | 6 | 3 | 3 | 0 | 50.0% |
| TSLR | 4 | 4 | 0 | 0 | 100.0% |

## Deliverable B(ii) -- Full 150-question test log

Full per-question evidence (question, response, expected, verification evidence) is in `sis_chatbot_test_results.csv`, one row per Q001-Q150, in original order. Below is the compact pass/fail index.

| Test ID | Category | Result | Severity |
|---|---|---|---|
| Q001 | Application | PASS | None |
| Q002 | Application | PASS | None |
| Q003 | Application | PASS | None |
| Q004 | Application | PASS | None |
| Q005 | Application | PASS | None |
| Q006 | Application | PASS | None |
| Q007 | Application | PASS | None |
| Q008 | Application | PASS | None |
| Q009 | Application | PASS | None |
| Q010 | Application | PASS | None |
| Q011 | Application | PASS | None |
| Q012 | Application | PASS | None |
| Q013 | Application | PASS | None |
| Q014 | Application | PASS | None |
| Q015 | Application | PASS | None |
| Q016 | Application | PASS | None |
| Q017 | Application | PASS | None |
| Q018 | Application | PASS | None |
| Q019 | Application | FAIL | High |
| Q020 | Application | PASS | None |
| Q021 | Dates | PASS | None |
| Q022 | Dates | PASS | None |
| Q023 | Dates | PASS | None |
| Q024 | Dates | PASS | None |
| Q025 | Dates | PARTIAL | Low |
| Q026 | Dates | PASS | None |
| Q027 | Dates | FAIL | Medium |
| Q028 | Dates | FAIL | Medium |
| Q029 | Dates | FAIL | Medium |
| Q030 | Dates | PASS | None |
| Q031 | Dates | PASS | None |
| Q032 | Dates | PASS | None |
| Q033 | Dates | PASS | None |
| Q034 | Dates | PASS | None |
| Q035 | Dates | PASS | None |
| Q036 | Dates | PASS | None |
| Q037 | Dates | PASS | None |
| Q038 | Dates | FAIL | Medium |
| Q039 | Dates | FAIL | Medium |
| Q040 | Dates | FAIL | Medium |
| Q041 | ServiceCode | FAIL | High |
| Q042 | ServiceCode | FAIL | High |
| Q043 | ServiceCode | PASS | None |
| Q044 | ServiceCode | PASS | None |
| Q045 | ServiceCode | PASS | None |
| Q046 | ServiceCode | PASS | None |
| Q047 | ServiceCode | PASS | None |
| Q048 | ServiceCode | PASS | None |
| Q049 | ServiceCode | PASS | None |
| Q050 | ServiceCode | PASS | None |
| Q051 | ServiceCode | PASS | None |
| Q052 | ServiceCode | PASS | None |
| Q053 | ServiceCode | PASS | None |
| Q054 | ServiceCode | PASS | None |
| Q055 | ServiceCode | PASS | None |
| Q056 | ServiceCompare | PASS | None |
| Q057 | ServiceCompare | PASS | None |
| Q058 | ServiceCompare | PASS | None |
| Q059 | ServiceCompare | PASS | None |
| Q060 | ServiceCompare | PASS | None |
| Q061 | ServiceCompare | PASS | None |
| Q062 | ServiceCompare | PARTIAL | Low |
| Q063 | ServiceCompare | PARTIAL | Low |
| Q064 | ServiceCompare | FAIL | Medium |
| Q065 | ServiceCompare | FAIL | Medium |
| Q066 | ServiceCompare | FAIL | Medium |
| Q067 | ServiceCompare | PASS | None |
| Q068 | ServiceCompare | PASS | None |
| Q069 | ServiceCompare | PASS | None |
| Q070 | ServiceCompare | PASS | None |
| Q071 | Counts | PARTIAL | Low |
| Q072 | Counts | PASS | None |
| Q073 | Counts | FAIL | Medium |
| Q074 | Counts | PASS | None |
| Q075 | Counts | PASS | None |
| Q076 | Counts | PASS | None |
| Q077 | Counts | PASS | None |
| Q078 | Counts | PASS | None |
| Q079 | Counts | PASS | None |
| Q080 | Counts | PASS | None |
| Q081 | Counts | PASS | None |
| Q082 | Counts | PASS | None |
| Q083 | Counts | FAIL | Medium |
| Q084 | Counts | PASS | None |
| Q085 | Counts | PARTIAL | Low |
| Q086 | Filtering | PASS | None |
| Q087 | Filtering | PASS | None |
| Q088 | Filtering | FAIL | Medium |
| Q089 | Filtering | PASS | None |
| Q090 | Filtering | PASS | None |
| Q091 | Filtering | PASS | None |
| Q092 | Filtering | PASS | None |
| Q093 | Filtering | PASS | None |
| Q094 | Filtering | PASS | None |
| Q095 | Filtering | PASS | None |
| Q096 | Filtering | FAIL | Medium |
| Q097 | Filtering | PASS | None |
| Q098 | Filtering | PASS | None |
| Q099 | Filtering | PASS | None |
| Q100 | Filtering | PASS | None |
| Q101 | Jurisdiction | PASS | None |
| Q102 | Jurisdiction | PARTIAL | Low |
| Q103 | Jurisdiction | PASS | None |
| Q104 | Jurisdiction | PASS | None |
| Q105 | Jurisdiction | FAIL | High |
| Q106 | Jurisdiction | PASS | None |
| Q107 | Jurisdiction | FAIL | Medium |
| Q108 | Jurisdiction | FAIL | High |
| Q109 | Jurisdiction | PASS | None |
| Q110 | Jurisdiction | PASS | None |
| Q111 | Jurisdiction | PASS | None |
| Q112 | Jurisdiction | PASS | None |
| Q113 | Jurisdiction | FAIL | High |
| Q114 | Jurisdiction | PASS | None |
| Q115 | Jurisdiction | PASS | None |
| Q116 | Channel | PASS | None |
| Q117 | Channel | PASS | None |
| Q118 | Channel | PASS | None |
| Q119 | Channel | PASS | None |
| Q120 | Channel | PASS | None |
| Q121 | Channel | PASS | None |
| Q122 | Channel | FAIL | High |
| Q123 | Channel | FAIL | Medium |
| Q124 | Channel | PASS | None |
| Q125 | Channel | PASS | None |
| Q126 | Channel | PASS | None |
| Q127 | Channel | PASS | None |
| Q128 | Channel | PASS | None |
| Q129 | Channel | FAIL | High |
| Q130 | Channel | PASS | None |
| Q131 | Workflow | FAIL | Medium |
| Q132 | Workflow | FAIL | Medium |
| Q133 | Workflow | PASS | None |
| Q134 | Workflow | FAIL | Medium |
| Q135 | Workflow | FAIL | Medium |
| Q136 | Workflow | FAIL | Medium |
| Q137 | Workflow | FAIL | Medium |
| Q138 | Workflow | PASS | None |
| Q139 | Workflow | FAIL | High |
| Q140 | Workflow | FAIL | Medium |
| Q141 | FLine | PASS | None |
| Q142 | FLine | PASS | None |
| Q143 | FLine | PASS | None |
| Q144 | FLine | FAIL | Critical |
| Q145 | FLine | FAIL | Critical |
| Q146 | FLine | FAIL | Medium |
| Q147 | TSLR | PASS | None |
| Q148 | TSLR | PASS | None |
| Q149 | TSLR | PASS | None |
| Q150 | TSLR | PASS | None |

## Deliverable D -- Prioritized defect list

No P0 (security breach / cross-jurisdiction leakage) defects were found. All 150 jurisdiction and
access-scoping checks that ran (Q101-115, Q122-123, the register-wide listings) returned rows that
belong to this officer's own wards; nothing from outside wards 001/005/009 was ever returned.

### P0 -- security breach / cross-jurisdiction leakage / serious fabricated record (none found)
_None found in this run._ Note: Q144/Q145 are graded Critical for a different reason (a real,
on-record fact never surfaced) -- they are listed under P1 since there is no fabrication or
jurisdiction violation, just an incomplete answer to a verification-field question.

### P1 -- material factual errors / serious hallucinations / incomplete critical answers
- **Q144** (FLine): Renders a generic 5-field summary card (status/submitted/survey/ward-block/channel) that omits the F-Line-specific verification fields entirely -- sis_username ('chn_chandru') is never surfaced, even though it is populated and CLAUDE.md documents F-Line verification fields as an explicitly handled case (fline_qa.py). 'Who verified' gets no name at all.
- **Q145** (FLine): Same generic-card gap as Q144 -- sis_verify_date (2026-06-30 20:58:29) is populated but never appears in the response; the card's 5 fields (status/submitted/survey/ward-block/channel) do not include it.
- **Q019** (Application): The chatbot computes '47 working days old... 12 working days past the SLA limit' and reads as an overdue warning for a REJECTED (closed) file. applications.is_overdue is actually FALSE (the overdue clock only applies to OPEN ISD files awaiting a field visit, per the documented SLA rule) -- the file's own stored flag is never consulted or stated.
- **Q041** (ServiceCode): 'What service does code 0153 represent?' was answered with 'Please specify the application number you are asking about' -- misrouted to the application-number gate entirely, even though service-code lookup for all 30 SIS_URBAN_SERVICES codes (incl. 0153) is a documented, previously-fixed deterministic feature. This exact phrasing variant ('What service does code X represent') is not recognised by that feature's cue matching.
- **Q042** (ServiceCode): Same misroute as Q041, for code 0154 -- 'Please specify the application number...' instead of the ISD definition.
- **Q105** (Jurisdiction): '105... 26 rejected application(s) matching this are left out' -- the EXCLUSION COUNT (26) is wrong for this filter: ward 001's actual rejected count is 24, not 26. 105+24=129 (ward 001's true total); the response instead implies 105+26=131. The note appears to always report the officer's GLOBAL rejected total rather than the count rejected BY THIS FILTER.
- **Q108** (Jurisdiction): Same exclusion-count bug as Q105: '3... 26 rejected application(s) matching this are left out' -- block 0055's actual rejected count is 6, not 26. 3+6=9 (block 0055's true total, verified independently); the response's '26' is the officer's global rejected total, wrongly applied to a block-scoped filter.
- **Q113** (Jurisdiction): 'Application 2026/0154/02/012547 is of type: ISD' never answers the yes/no jurisdiction question that was actually asked.
- **Q122** (Channel): Completely misrouted: the response is an 'Old-owner record vs sub-division record' comparison card, unrelated to submission IP addresses at all.
- **Q129** (Channel): 'I could not find that particular detail... Here is what it does hold' -- claims payment info is unavailable, even though fee_amount=650, challan_number=234 and payment_mode=treasury are all populated and were correctly retrieved by the NARROWER single-field questions (Q124/125/126, all PASS). The composite 'what payment information is available' framing fails where the individual field questions succeed.
- **Q139** (Workflow): 'No F-Line (service code 0178) record is held for application 2026/0154/02/012547' -- a bizarre, unrelated misroute: this is an ISD (0154) file and the question never mentioned F-Line. Looks like a token collision between 'transition' (asked) and an F-Line-specific cue; exact mechanism not confirmed from the evidence available (ROOT CAUSE UNCONFIRMED).


### P2 -- incorrect filters, counts, comparisons, dates, incomplete answers
- **Q027** (Dates): Answered with the LAST update time (2026-09-23 05:10) to a question asking for the FIRST workflow action -- the two are conflated. The first hop was 2026-08-05 13:10:29 UTC (2026-08-05 18:40 IST).
- **Q028** (Dates): 'No full-field patta transfer record...' answers a DIFFERENT, NISD-only field (fulltransfer_qa's SIS-recommendation column) instead of recognising that workflow_history itself (the right table for an ISD file) has no recommendation flag on any hop. Not false, but answers the wrong record for this file's service code.
- **Q029** (Dates): Returns a generic details snippet (submission date + current stage) and never states the SIS->SD transition date (2026-09-22 19:11 IST) the question asked for.
- **Q038** (Dates): Answered a completely different question -- ward application-count comparison ('Ward 001 129, Ward 005 19...') -- instead of naming the most recently submitted application IN ward 001 (expected one of the 2026-09-24 filings, e.g. 2026/0154/02/015526).
- **Q039** (Dates): Gives the service-code DEFINITION (what 0158 is) instead of a DATE -- never answers 'what was the earliest submission date', which is 2026-01-01.
- **Q040** (Dates): The response is garbled -- a fragment ('1. Are there applications from the same month') followed by an unrelated 44-pending-applications dump. The big-message splitter appears to have misparsed this as a numbered sub-question list; it never actually answers the question.
- **Q064** (ServiceCompare): 'How is 0164 different from 0161' was answered with a REGISTER FILE-COUNT comparison (13 files vs 0 files) instead of the actual conceptual difference: 0164 is ULC Land Subdivision (requires a field visit), 0161 is Street Master (does not). This is the register count path (compare_applications), not the service-code definition path (service_code_guide).
- **Q065** (ServiceCompare): Same bug as Q064 -- '0178 vs 0185' answered with a file-count comparison (15 vs 0) instead of explaining demarcation (0178) vs appeal (0185).
- **Q066** (ServiceCompare): Same bug again -- '0156 vs 0157' answered with a file-count comparison (11 vs 0) instead of explaining 'with Sketch' vs 'Extract Only'. Three for three: 'difference between two service codes' consistently routes to the register-count comparison instead of the code-definition guide.
- **Q073** (Counts): 'Please specify the application number...' -- misrouted to the application-number gate instead of a register-wide analytics comparison across service codes.
- **Q083** (Counts): Same misroute as Q073 -- analytics ('highest rejection count') read as a request for an application number.
- **Q088** (Filtering): 'Found 19 application(s)' for 'approved applications submitted in 2026' does not match either grounded figure (82 approved register-wide, or 10 for 0154/ISD alone) and the table shown has no Status column at all, so it cannot be confirmed the result is even filtered to approved. All 152 register rows are already 2026-only, so the 2026 filter should be a no-op and the answer should read close to 82. ROOT CAUSE UNCONFIRMED from the evidence available.
- **Q096** (Filtering): Answered with a STATUS breakdown (2 rejected / 20 not rejected) instead of a CHANNEL breakdown -- the word 'channel' in the question was not used; 0158 is in fact 100% CSC (22/22), which the answer never states.
- **Q107** (Jurisdiction): Answered with the ward-001 PENDING count (38) instead of listing which blocks in ward 001 contain applications -- no block numbers are named at all.
- **Q123** (Channel): Only restates the app's OWN IP (117.218.227.225); never attempts to list OTHER applications with a different IP, which is what was asked. Not a hallucination (no false data given), but non-responsive.
- **Q131** (Workflow): Answers WHEN (last update time) but never WHO performed the action -- the desk that closed the file (SD) is never named, even as 'the register holds desks not names, and the desk was SD'.
- **Q132** (Workflow): Same canned 'last updated' response as Q131 and Q027 and Q030 -- never addresses 'remark' at all; the correct answer is that all 4 workflow_history rows have remarks=NULL.
- **Q134** (Workflow): Same misroute as Q028 -- answers about the NISD-only full-field-transfer SIS-recommendation field instead of this ISD app's own workflow_history (which has no recommendation flag).
- **Q135** (Workflow): 'Currently at: Rejected' restates the CURRENT stage; never names the PRIOR stage (SD) the question asked for.
- **Q136** (Workflow): 'I could not find that particular detail...' -- workflow_history clearly shows the hop after the first SIS entry (SIS->SIS, then SIS->SD); the data exists but was not retrieved for this phrasing.
- **Q137** (Workflow): 'I could not find that particular detail...' for a simple count of workflow_history rows (4) -- the same table answers other workflow questions correctly elsewhere in this run.
- **Q140** (Workflow): Gives the SUBMISSION date (2026-08-05) and the current stage name, but never answers WHEN that stage (REJECTED) was reached -- that is 2026-09-22 (UTC) / 2026-09-23 05:10 IST.
- **Q146** (FLine): Same misroute pattern as Q028/Q134 -- 'No full-field patta transfer record...' (an NISD-only field) instead of this F-Line app's own transaction_status (which is genuinely NULL, but the answer never says so).


### P3 -- minor wording / completeness issues
_None._


## Deliverable F -- Final recommendation

**Ready for further controlled testing -- not yet suitable for a pilot.**

Reasoning:
- The two properties that matter most for a government land-records assistant -- **never
  inventing a record or date, and never leaking another officer's jurisdiction** -- held up
  across all 150 questions, including the mandatory Q150 hallucination test and every
  missing-record / missing-field case this run exercised. That is the precondition for trusting
  the tool at all, and it is met.
- But a single named application's own **workflow history is only answered correctly 20% of the
  time** (Q131-140), and two **F-Line verification fields that are on record are never surfaced**
  (Q144/145, Critical). An officer relying on this assistant for "who signed off and when" on a
  file under appeal would be told "I could not find that" for data that exists.
- The recurring **"N rejected left out" exclusion-count bug** (Q105, Q108: reports the officer's
  global rejected total instead of the count excluded by the actual filter) is a correctness bug
  that would mislead an officer about how many files exist within the view they asked for --
  worth fixing before the counts/filtering features are relied on for real decisions.
- `service_code_lookup` -- a previously-documented, supposedly-fixed feature -- regressed for the
  phrasing "What service does code X represent?" (Q041/Q042), misrouting to an application-number
  prompt instead of the code's definition.

None of this is a security or jurisdiction-leakage problem, and none of it is a hallucination --
both of which would have been release-blocking. It is retrieval/routing incompleteness on specific
phrasings and a specific subsystem (per-application workflow history, F-Line verification fields).
Recommended before a pilot: fix the P1 items above, re-run Q131-146 (the Workflow and F-Line
sections, the two weakest), and complete the matrix sections this 150-question run did not target
in depth (exhaustive typo variants, earliest/latest edge cases beyond what was sampled here, and
the full ward x block x service-code combination space).

## Resolved placeholder values used

| Placeholder | Value | Why chosen |
|---|---|---|
| APP_ID / APP_ID_1 | 2026/0154/02/012547 | ISD, rejected, rich workflow (4 hops) + completed field visit + fee/challan data, in-jurisdiction |
| APP_ID_2 | 2026/0154/02/012715 | submitted 2026-08-07, in-jurisdiction |
| APP_ID_3 | 2026/0154/02/012766 | submitted 2026-08-08, in-jurisdiction |
| WARD | 001 | the officer's largest ward (129 of 152 register rows) |
| BLOCK | 0055 | APP_ID's own block (9 applications) |
| SERVICE_CODE | 0158 | Modification/Anadeenam, 22 applications in-jurisdiction, 100% CSC channel (a clean, verifiable single-channel case) |
| SURVEY_NO | 7 | APP_ID's own survey number (4 applications share it) |
| TSLR_APP | 2026/0156/02/000440 | full TSLR record: survey 13/18, challan 202699863 dated 2026-08-14 |
| NO_TSLR_APP (Q150) | 2026/0154/02/012547 (= APP_ID) | ISD/0154, genuinely has zero rows in `tslr_sketch_urban` |
| FLINE_APP | 2026/0178/02/000309 | SIS-verified (sis_rec='Y', sis_verify_date populated, sis_username='chn_chandru'); DIS not yet verified -- exercises both a populated and a null F-Line field in one record |

## Known limitation of this run

This 150-question sweep targets one representative application per category deeply rather than
exhaustively sampling every application/ward/service-code combination. It did not separately
re-test the full typo matrix or the complete Tamil/Tanglish phrasing set the original prompt's
preamble described (those were covered in an earlier, separate QA pass in this project's history --
see `test_context_regression.py` et al. for typo/Tanglish-specific regression coverage). Every
number and date in this report was computed from the live database at test time, not estimated.
