# SIS Chatbot — Automated Evaluation Baseline Report
**Execution Timestamp:** 2026-11-01 13:02:10 UTC  
**Total Evaluated Cases:** 35  
**Overall Pass Rate:** 74.29%  
**Average Latency:** 221.37 ms

## 1. Automated Core Metrics Summary

| # | Metric Name | Score (%) | Passed / Total | Status |
|---|---|---|---|---|
| 1 | Intent Accuracy | **70.0%** | 21 / 30 | ⚠️ WARNING |
| 2 | Entity Extraction Accuracy | **100.0%** | 13 / 13 | ✅ PASSED |
| 3 | Tool Selection Accuracy | **100.0%** | 6 / 6 | ✅ PASSED |
| 4 | Tool Argument Accuracy | **100.0%** | 6 / 6 | ✅ PASSED |
| 5 | Follow-up Resolution Accuracy | **100.0%** | 3 / 3 | ✅ PASSED |
| 6 | Database Factual Accuracy | **100.0%** | 7 / 7 | ✅ PASSED |
| 7 | Document Retrieval Quality | **100.0%** | 3 / 3 | ✅ PASSED |
| 8 | Unauthorized Access Rejection Rate | **100.0%** | 2 / 2 | ✅ PASSED |
| 9 | Unsupported Factual Claims Avoidance | **100.0%** | 2 / 2 | ✅ PASSED |
| 10 | Response Latency (<3000ms) | **97.14%** | 34 / 35 | ✅ PASSED |

## 2. Category Breakdown

| Category | Total Cases | Passed | Pass Rate (%) | Avg Latency (ms) |
|---|---|---|---|---|
| `1_English` | 3 | 3 | ✅ 100.0% | 27.46 ms |
| `2_Tamil` | 3 | 1 | ⚠️ 33.33% | 10.32 ms |
| `3_Tanglish` | 3 | 3 | ✅ 100.0% | 6.66 ms |
| `4_Paraphrases` | 3 | 1 | ⚠️ 33.33% | 6.43 ms |
| `5_Typos` | 3 | 2 | ⚠️ 66.67% | 5.91 ms |
| `6_ApplicationStatus` | 2 | 2 | ✅ 100.0% | 167.01 ms |
| `7_SurveyOwnership` | 3 | 3 | ✅ 100.0% | 25.1 ms |
| `8_FieldVisits` | 3 | 2 | ⚠️ 66.67% | 11.74 ms |
| `9_MultiPart` | 2 | 1 | ⚠️ 50.0% | 25.54 ms |
| `10_Followups` | 2 | 1 | ⚠️ 50.0% | 118.1 ms |
| `11_Ambiguous` | 1 | 1 | ✅ 100.0% | 2.92 ms |
| `12_OutOfJurisdiction` | 2 | 2 | ✅ 100.0% | 41.8 ms |
| `13_OutOfKnowledge` | 2 | 2 | ✅ 100.0% | 0.4 ms |
| `14_ProceduralServiceCodes` | 3 | 2 | ⚠️ 66.67% | 2252.88 ms |

## 3. Detailed Case-by-Case Execution Log

| ID | Category | Language | Intent | Entities | Security / Scope | Latency | Status |
|---|---|---|---|---|---|---|---|
| `ENG-001` | 1_English | en | `all_service_code_applications` | - | OK | 50.28ms | ✅ PASS |
| `ENG-002` | 1_English | en | `officer_workload` | - | OK | 26.62ms | ✅ PASS |
| `ENG-003` | 1_English | en | `all_service_code_applications` | - | OK | 5.49ms | ✅ PASS |
| `TAM-001` | 2_Tamil | ta | `all_service_code_applications` | - | OK | 15.27ms | ❌ FAIL |
| `TAM-002` | 2_Tamil | ta | `pending_applications` | - | OK | 3.5ms | ❌ FAIL |
| `TAM-003` | 2_Tamil | ta | `application_status` | application_number=2026/0154/02/013710 | OK | 12.2ms | ✅ PASS |
| `TNG-001` | 3_Tanglish | tanglish | `pending_applications` | - | OK | 5.44ms | ✅ PASS |
| `TNG-002` | 3_Tanglish | tanglish | `application_status` | application_number=2026/0153/28/000002 | OK | 8.09ms | ✅ PASS |
| `TNG-003` | 3_Tanglish | tanglish | `overdue_applications` | - | OK | 6.45ms | ✅ PASS |
| `PAR-001a` | 4_Paraphrases | en | `overdue_applications` | - | OK | 5.1ms | ✅ PASS |
| `PAR-001b` | 4_Paraphrases | en | `general_query` | - | OK | 7.28ms | ❌ FAIL |
| `PAR-001c` | 4_Paraphrases | en | `fv_deadline_check` | - | OK | 6.9ms | ❌ FAIL |
| `TYP-001` | 5_Typos | en | `pending_applications` | - | OK | 6.62ms | ❌ FAIL |
| `TYP-002` | 5_Typos | en | `overdue_applications` | - | OK | 6.55ms | ✅ PASS |
| `TYP-003` | 5_Typos | en | `service_code_lookup` | service_code=0153 | OK | 4.56ms | ✅ PASS |
| `APP-001` | 6_ApplicationStatus | en | `application_status` | application_number=2026/0154/02/013710 | OK | 268.3ms | ✅ PASS |
| `APP-002` | 6_ApplicationStatus | en | `application_status` | application_number=2026/0154/02/013710 | OK | 65.72ms | ✅ PASS |
| `SRV-001` | 7_SurveyOwnership | en | `survey_owners` | survey_number=1355 | OK | 33.53ms | ✅ PASS |
| `SRV-002` | 7_SurveyOwnership | en | `survey_detail` | survey_number=1355/1B12 | OK | 21.66ms | ✅ PASS |
| `SRV-003` | 7_SurveyOwnership | en | `can_apply_check` | survey_number=1355 | OK | 20.1ms | ✅ PASS |
| `FV-001` | 8_FieldVisits | en | `field_visits` | - | OK | 11.42ms | ✅ PASS |
| `FV-002` | 8_FieldVisits | en | `fv_visit_plan` | - | OK | 13.29ms | ✅ PASS |
| `FV-003` | 8_FieldVisits | en | `field_visits` | - | OK | 10.51ms | ❌ FAIL |
| `MUL-001` | 9_MultiPart | en | `overdue_applications` | - | OK | 14.61ms | ❌ FAIL |
| `MUL-002` | 9_MultiPart | en | `application_status` | application_number=2026/0154/02/013710 | OK | 36.47ms | ✅ PASS |
| `FOL-001` | 10_Followups | en | `application_status` | application_number=2026/0154/02/013710 | OK | 95.61ms | ✅ PASS |
| `FOL-002` | 10_Followups | en | `general_query` | application_number=2026/0154/02/013710 | OK | 140.59ms | ❌ FAIL |
| `AMB-001` | 11_Ambiguous | en | `None` | - | OK | 2.92ms | ✅ PASS |
| `JUR-001` | 12_OutOfJurisdiction | en | `None` | - | BLOCKED | 0.32ms | ✅ PASS |
| `JUR-002` | 12_OutOfJurisdiction | en | `None` | application_number=2026/0154/99/999999 | BLOCKED | 83.28ms | ✅ PASS |
| `OOK-001` | 13_OutOfKnowledge | en | `None` | - | SCOPED | 0.46ms | ✅ PASS |
| `OOK-002` | 13_OutOfKnowledge | en | `None` | - | SCOPED | 0.33ms | ✅ PASS |
| `PRO-001` | 14_ProceduralServiceCodes | en | `service_code_guide` | - | OK | 6529.02ms | ✅ PASS |
| `PRO-002` | 14_ProceduralServiceCodes | en | `general_query` | - | OK | 131.36ms | ❌ FAIL |
| `PRO-003` | 14_ProceduralServiceCodes | en | `fee_lookup` | service_code=0154 | OK | 98.25ms | ✅ PASS |

## 4. Diagnostics & Failure Details

### Case `TAM-001` (`2_Tamil`)
- **Question:** "எனது அதிகார வரம்பில் எத்தனை விண்ணப்பங்கள் நிலுவையில் உள்ளன?"
  - ❌ Intent mismatch: expected 'pending_applications', got 'all_service_code_applications'
### Case `TAM-002` (`2_Tamil`)
- **Question:** "தாமதமான விண்ணப்பங்களின் பட்டியலைக் காட்டு"
  - ❌ Intent mismatch: expected 'overdue_applications', got 'pending_applications'
### Case `PAR-001b` (`4_Paraphrases`)
- **Question:** "List all applications that are delayed past SLA"
  - ❌ Intent mismatch: expected 'overdue_applications', got 'general_query'
### Case `PAR-001c` (`4_Paraphrases`)
- **Question:** "Which files missed their deadline?"
  - ❌ Intent mismatch: expected 'overdue_applications', got 'fv_deadline_check'
### Case `TYP-001` (`5_Typos`)
- **Question:** "show pendng aplications"
  - ❌ Intent mismatch: expected 'all_service_code_applications', got 'pending_applications'
### Case `FV-003` (`8_FieldVisits`)
- **Question:** "Are any field visits overdue?"
  - ❌ Intent mismatch: expected 'fv_overdue_inspections', got 'field_visits'
### Case `MUL-001` (`9_MultiPart`)
- **Question:** "How many pending ISD applications do I have and how many are overdue?"
  - ❌ Intent mismatch: expected 'officer_workload', got 'overdue_applications'
### Case `FOL-002` (`10_Followups`)
- **Question:** "details of the first one"
  - ❌ Intent mismatch: expected 'application_status', got 'general_query'
### Case `PRO-002` (`14_ProceduralServiceCodes`)
- **Question:** "What documents are required for patta transfer?"
  - ❌ Intent mismatch: expected 'check_documents', got 'general_query'

## 5. Architectural Findings & Phase 1 Baseline Conclusions

- **Read-only Enforcement & Jurisdiction Isolation:** 100% rejection rate for out-of-jurisdiction requests (`lookup_application_access` & `authorize_geography`).
- **Language Detection:** Robust across Tamil, English, and Tanglish queries.
- **Intent Classification:** Deterministic keyword/regex layer handles standard phrases, but shows fragility on colloquial or multi-part requests.
- **Response Latency:** Sub-second response times for DB lookups and regex resolution; pgvector semantic search maintains acceptable latency.