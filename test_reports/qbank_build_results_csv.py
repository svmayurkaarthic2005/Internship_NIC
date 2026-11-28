# -*- coding: utf-8 -*-
"""Builds SIS_CHATBOT_QUESTION_BANK_RESULTS.csv from the manual scoring table below.
Scores were assigned by reading the real transcripts in qbank_artifacts/qbank_runs/
against the live database (see the report's Individual Test Results section for
the reasoning behind each score). This script only renders the table to CSV.
"""
import csv

ROWS = [
# chain, turn, question_short, verdict, note
("Chain1_045470", 1, "Trace 045470 submission->rejection with dates/remarks/reason", "PARTIAL", "Dates/remarks/reason retrieved correctly but answer fragmented by big_plan splitter; status vs remark distinction weak"),
("Chain1_045470", 2, "Who handled it before final desk, and date?", "FAIL", "Returned Current Stage: N/A, Submission Date: N/A -- follow-up resolver failed"),
("Chain1_045470", 3, "What exactly was missing (doc)?", "FAIL", "Answered from blank missing_docs field; ignored the actual rejection remark text found in Turn 1 ('Linked Documents are not submitted')"),
("Chain1_045470", 4, "Compare final remark in transfer table vs workflow", "PARTIAL", "Returned the one remark again; did not show/compare a second source"),
("Chain1_045470", 5, "Registration doc number/place/date vs submission date", "PASS", "Correctly distinguished: Reg R/17/1/11822/2026, Ambattur, 28-08-2026 vs Submitted 2026-08-29 13:33:34"),
("Chain4_fieldvisit", 1, "NISD with field_visit_dt present -- does that mean visit required?", "PARTIAL", "Correctly says NISD needs no visit, but split answer is garbled and 'raw evidence' was not actually shown"),
("Chain4_fieldvisit", 2, "Which workflow transition contains that date?", "FAIL", "Repeated generic NISD-no-visit line; never answered which transition"),
("Chain4_fieldvisit", 3, "Does a populated date column mean inspection required?", "FAIL", "Misrouted to 'Automutated: not recorded' -- unrelated field (root cause: 'automatically'->'automutated' keyword collapse, appinfo_qa.py:50)"),
("Chain4_fieldvisit", 4, "Explain field-visit-required-by-design vs date-merely-present", "FAIL", "Misrouted to service-code file-count comparison stats, totally unrelated"),
("Chain5_012547", 1, "Full subdivision picture: old owners, temp subdivision, proposed owners, status, rejection, visit info", "PARTIAL", "Old/proposed owners and temp subdivision correct; 'rejection remarks' conflated with unrelated 'proposed field visit remarks' field"),
("Chain5_012547", 2, "How many old-owner records?", "PASS", "4, matches Turn 1 and DB"),
("Chain5_012547", 3, "Who in proposed subdivision-owner records, relationships?", "PARTIAL", "First half correct (showed old-owner survey/subdiv table, mislabeled as 'proposed'); second half timed out after 62s"),
("Chain5_012547", 4, "Proposed subdivision existing patta no, or blank?", "FAIL", "Returned a garbled 22-row dump of blank fields repeated 4x; never answered the patta question"),
("Chain5_012547", 5, "Tahsildar's return remark -- what must be submitted?", "PASS", "Correctly explained correction/modification desk chain doesn't apply to ISD (0154); honest 'not applicable' answer"),
("Chain7_012766", 1, "New subdivision/patta numbers, adopted areas, owners for ISD file; don't mix old/new owner", "FAIL", "3 of 5 sub-answers wrongly claim 'No full-field patta transfer record -- only NISD carries one' for an ISD file whose own subdivision_qa data (shown 2 turns later) proves the real data exists and is retrievable"),
("Chain7_012766", 2, "The two temporary subdivision identifiers?", "PASS", "2/T1, 2/T2 -- correct, and contradicts Turn 1's false 'no record' claim within the same chain"),
("Chain7_012766", 3, "Which new subdivision numbers assigned?", "FAIL", "Answered with generic 'how sketch is prepared' documentation instead of this application's actual new numbers (2, 4), which Turn 4 proves are available"),
("Chain7_012766", 4, "Patta number per resulting subdivision?", "PASS", "Correct: 2/T1->new 2, patta 3908; 2/T2->new 4, patta 84082; totals 266 sq.m"),
("Chain7_012766", 5, "How do proposed ownership records differ between subdivisions?", "FAIL", "Repeated the subdivision/patta/area table again; no ownership information given at all"),
("Chain9_0158_000002", 1, "Trace SIS/ZDT/SD/DIS desk chain + UAREG approval; do workflow and modification evidence agree?", "PARTIAL", "First half: correct 2-row parcel-modification card; second half ('do they agree') misrouted to an unrelated 0158-vs-0167 file-count comparison"),
("Chain9_0158_000002", 2, "When did the SIS recommendation occur?", "FAIL", "Misrouted to 'No full-field patta transfer record -- only NISD carries one' even though cmflow_qa's own correction-modification table (shown in Turn 1) already states SIS recommended on 2026-01-19 (root cause: fulltransfer_qa SISREC keyword claims any 'sis'+'recommendation' question ahead of cmflow_qa, chat_shared.py:2798)"),
("Chain9_0158_000002", 3, "What happened at ZDT/SD/DIS after that?", "PASS", "Full, correct desk-by-desk card: ZDT 2026-01-20, SD/DIS 2026-01-21, matches workflow_history steps"),
("Chain9_0158_000002", 4, "Which later stage recorded final approval, and date?", "PASS", "RDO Approved 2026-01-27, consistent with workflow step 7 (DIS->Closed 2026-01-27)"),
("Chain9_0158_000002", 5, "UAREG approval file reference vs final workflow date", "PARTIAL", "First half correct (A5/5715/2025 / 2026-01-27); second half again misrouted to the unrelated service-code-count comparison"),
("Chain10_0158_001804", 1, "Reconcile open register status vs Approved correction-modification remark", "FAIL", "First sub-answer ('register status') wrongly returned the read-only-guard mutation-refusal boilerplate ('I can only read the records...') instead of the actual status; rest of the answer (workflow, desk table) was otherwise correct and detailed"),
("Chain10_0158_001804", 2, "Which was the latest workflow transition?", "PASS", "Correct 6-step history ending at DIS, consistent with Turn 1"),
("Chain10_0158_001804", 3, "What does correction-modification table say about Tahsildar's remark?", "PARTIAL", "Returned the two modify-operation remarks (proceeding/order references), not a distinct 'Tahsildar remark' field -- plausibly correct if no separate text exists, but not clearly matched to what was asked"),
("Chain10_0158_001804", 4, "Why isn't an Approved remark proof of closure?", "FAIL", "Answered 'No remark is recorded on the workflow' -- a non-answer to an explanatory question; never reasoned about the open-register-vs-Approved-recommendation discrepancy"),
("Chain10_0158_001804", 5, "More than one UAREG row? Show operations separately.", "PASS", "Correctly separated op 01 and op 04 rows with distinct remarks"),
("Chain13_fline_000267", 1, "Distinguish proposed visit date / workflow action date / application update date", "PARTIAL", "All three correct values were eventually retrieved (visit 2026-06-12, workflow Closed/Approved 2026-06-12, amount 860/CP) but the big_plan text-splitting produced garbled sub-headers"),
("Chain13_fline_000267", 2, "Proposed field-visit date?", "PASS", "2026-06-12, consistent"),
("Chain13_fline_000267", 3, "What did the actual workflow record on that date?", "PASS", "Correctly shows Closed/Approved on 2026-06-12, same date as the proposed visit -- a real coincidence in the data, correctly reported without conflation"),
("Chain13_fline_000267", 4, "Amount and payment mode in application-information record?", "PASS", "860.00 / CP, consistent with Turn 1"),
("Chain14_045472_owners", 1, "Count old-owner vs IGRS-owner rows; compare fields; distinguish from applicant-information", "PARTIAL", "Correctly reports no chitta old-owner row and shows a 2-row IGRS table, but never checked/mentioned the NISD-specific full_field_patta_transfer_old_owner table, which is the table that actually could hold an 'old owner' for this NISD file"),
("Chain14_045472_owners", 2, "How many IGRS-owner records linked?", "FAIL", "Answered with an applicant name ('Applicant: பாலாஜி'), not a count"),
("Chain14_045472_owners", 3, "Does old-owner table have the same row count?", "FAIL", "Answered with an unrelated single field ('Owner no: 1'), not a comparison"),
("Chain14_045472_owners", 4, "Why shouldn't these two tables be treated as identical?", "FAIL", "Misrouted to 'Automutated: not recorded' (same root cause as Chain4 Turn 3)"),
("Chain17_servicecode0165", 1, "App info, register status, workflow for 0165 app; check specialised tables", "BLOCKED", "Officer sperumal_chn@sis.tn.gov.in's jurisdiction (wards A/G) does not cover this application's ward -- a test-setup/officer-selection limitation, not a chatbot defect"),
("Chain17_servicecode0165", 2, "Which source tables actually contain this application?", "BLOCKED", "Same jurisdiction refusal"),
("Chain17_servicecode0165", 3, "Which specialised detail sources have no matching record?", "FAIL", "Jurisdiction refusal was NOT given this time; instead returned an unrelated channel-breakdown stat ('CSC 101, Sub-Registrar 19...') -- inconsistent behaviour across turns of the same blocked chain"),
("Chain17_servicecode0165", 4, "Register-level answer without pretending module data exists?", "FAIL", "124.9s LLM timeout: 'The model stopped responding, so this reply was cut short.'"),
("Chain19_list_filter_compare", 1, "List 0154 apps with >1 temp subdivision, with status/owner-presence columns", "FAIL", "Completely misrouted to the canned application-number-format explainer ('An application number follows the format YEAR/SERVICE_CODE/...') -- root cause: unanchored substring check 'format' in message matches inside the ordinary word 'information' (orchestrator.py:2148/5086); the real list query was never attempted"),
("Chain19_list_filter_compare", 2, "Now show only the rejected ones", "FAIL", "'Nothing has been shown in this conversation yet' -- correct GIVEN Turn 1's failure, but confirms no list context was ever established"),
("Chain19_list_filter_compare", 3, "Which has the largest total recorded subdivision area?", "PARTIAL", "Ignored all prior turns' scope (0154-only, >1 subdivision) and returned a generic register-wide subdivision-presence listing across all service codes; the listed rows are real DB data, just not the requested, scoped comparison"),
("Chain19_list_filter_compare", 4, "Owner details for that application, grouped by subdivision", "FAIL", "'Please specify the application number' -- confirms Turn 3's table result was never captured as follow-up context either"),
("ConfusingQuestions", 1, "Investigate 012766 (open-ended)", "PASS", "Correct: ISD, Approved, COMPLETED, Dhanalashmi, survey 76"),
("ConfusingQuestions", 2, "Multiple subdivision numbers -- how many applications?", "PARTIAL", "Correctly shows 2 temp + 2 final subdivision numbers but never explicitly states the trap answer ('still one application')"),
("ConfusingQuestions", 3, "Blank new patta number -- means rejected?", "FAIL", "Misrouted to 'No full-field patta transfer record -- only NISD carries one' (same fulltransfer_qa SISREC/claims_strong false-positive pattern); also the premise doesn't hold for this app (neither new patta is actually blank per Chain7 data)"),
("ConfusingQuestions", 4, "Visit date present but no completed visit -- did anyone visit?", "PARTIAL", "Returned a correct, grounded table showing 012766's visit as Completed (2026-08-25), which does answer the question, but gave no explicit sentence addressing the premise"),
("StandaloneSample_A", 1, "List rejected NISD apps, split missing-doc vs other reasons, with history", "FAIL", "Misrouted to an unrelated subdivision-presence stat ('A subdivision recorded does not apply to any of the 19...'); the actual list/split was never attempted"),
("StandaloneSample_B", 1, "(no context) multiple subdivisions -> how many applications?", "PASS", "Correctly asked for an application number given zero prior context -- appropriate clarification behaviour"),
("StandaloneSample_C", 1, "Actual reason code for 045586 rejection, translated", "PASS", "Correctly stated the NISD application-information record has no such column, rather than inventing a code or translation"),
("StandaloneSample_D", 1, "SIS recommended approval, later desk rejected/differed -- compare remarks", "FAIL", "Returned 'Found 38 applications - showing the last 1' with one unrelated, fully-approved NISD application and no SIS-vs-later-desk comparison at all"),
("Tamil_block1", 1, "012715: 2 temp subdivisions? give areas separately (Tanglish)", "PARTIAL", "Correctly gave both temp subdivision numbers (1/T1, 1/T2) but never gave the requested areas"),
("Tamil_block1", 2, "Compare old owner vs new subdivision owner (Tanglish)", "FAIL", "Falsely claimed proposed/new-owner records 'only exist for NISD (0153) and 0165 files' -- factually wrong as a general claim; Chain5 proves ISD files DO carry proposed subdivision-owner records (chitta_temp_subdivclub)"),
("Tamil_block1", 3, "New subdivision number for the second one? (Tanglish)", "PASS", "Correctly 'not recorded yet' -- consistent with the file still being pending/unapproved"),
("Tamil_block1", 4, "Patta number? (Tanglish)", "PASS", "394, consistent with the original question-bank's own stated fact for survey 293"),
("Tamil_block2", 1, "Application submit date vs registration date, difference (Tanglish)", "FAIL", "Returned a generic field-comparison card with no explicit dates shown and no computed difference, despite both dates being available and shown correctly in the English Chain1 equivalent"),
("Tamil_block2", 2, "Exact rejection reason; compare workflow vs transfer remarks (Tanglish)", "FAIL", "Answered only 'Sale deed' (the reason category), ignoring the actual rejection remark text and the requested two-source comparison"),
("TestB_endtoend", 1, "Investigate 001804: open register vs Approved correction-modification remark", "PASS", "Correctly surfaced the two-operation modification table"),
("TestB_endtoend", 2, "Tell me which table says what", "FAIL", "120.6s LLM timeout, no answer produced"),
("TestB_endtoend", 3, "Which was the last workflow action?", "PASS", "Correct 6-step history, consistent with Chain10"),
("TestB_endtoend", 4, "What did the Tahsildar record?", "FAIL", "120.8s LLM timeout, no answer produced"),
("TestB_endtoend", 5, "Does Approved mean the application is closed?", "FAIL", "'No remark is recorded on the workflow' -- same non-answer bug as Chain10 Turn 4"),
("TestB_endtoend", 6, "Check the UAREG table too", "FAIL", "'I'm not sure what you mean' -- failed to recognise 'UAREG' even though the same data is shown elsewhere as 'parcel modification'"),
("TestB_endtoend", 7, "Why are there two operation rows?", "PASS", "Correctly separated op 01 / op 04 codes (flagged as undefined codes, correctly)"),
("TestB_endtoend", 8, "Which value is the current survey number?", "PASS", "161/0 for both operation rows, consistent"),
("TestB_endtoend", 9, "Is there an approval-file reference?", "PASS", "Correctly 'not recorded / not recorded' for both rows"),
("TestB_endtoend", 10, "What information is missing before saying it's completed?", "FAIL", "'No application-information record is held' -- a wrong-handler non-answer to an explanatory reasoning question (same pattern as Chain10 Turn 4)"),
("AmbiguousFollowups", 1, "Compare 045470 and 045472 (survey/subdiv/patta/deed/status/remark)", "FAIL", "Answer is entirely about 045470 alone; 045472 is never mentioned, named, or compared at all"),
("AmbiguousFollowups", 2, "What about the other one?", "FAIL", "Returned the IDENTICAL 045470-only card verbatim -- never switched entity to 045472"),
("AmbiguousFollowups", 3, "Which one was submitted first?", "PARTIAL", "Only gave 045470's date; never retrieved or compared 045472's date"),
("AmbiguousFollowups", 4, "Was that the rejected one?", "PASS", "Correctly confirmed 045470 is rejected (matches DB appl_status='02')"),
("AmbiguousFollowups", 5, "What was the reason for that?", "PASS", "Correctly: 'sale' (declared reason), consistent"),
("AmbiguousFollowups", 6, "No, I meant the update date, not submission date", "FAIL", "Split into two garbled fragments: one triggered an unrelated 'field correction' offer, the other answered with the submission date -- exactly the value the user said NOT to give"),
]

with open("test_reports/SIS_CHATBOT_QUESTION_BANK_RESULTS.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["chain", "turn", "question_summary", "verdict", "note"])
    for r in ROWS:
        w.writerow(r)

print("wrote", len(ROWS), "rows")
pass_n = sum(1 for r in ROWS if r[3] == "PASS")
partial_n = sum(1 for r in ROWS if r[3] == "PARTIAL")
fail_n = sum(1 for r in ROWS if r[3] == "FAIL")
blocked_n = sum(1 for r in ROWS if r[3] == "BLOCKED")
print(f"PASS={pass_n} PARTIAL={partial_n} FAIL={fail_n} BLOCKED={blocked_n}")
scored = pass_n + partial_n + fail_n
score = (pass_n * 1 + partial_n * 0.5) / scored if scored else 0
print(f"overall score (excl BLOCKED) = {score:.1%} over {scored} scored tests")
