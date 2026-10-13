# SIS Chatbot — Survey Number / Subdivision Number Pairing Fix Report
**Date:** 2026-10-13
**Officer used for live verification:** `marun_chn@sis.tn.gov.in`
**No production database was seeded, cleared or modified. No commit/push was made. No identifier was fabricated or borrowed from another record.**

## 1. Scope of this pass

The requirement is stated broadly ("every applicable path... uploaded TXT/CSV/PDF/DOCX, OCR, database-backed queries, RAG, multi-document comparisons, table extraction, follow-ups, all languages, streaming and non-streaming"). This pass inspected all of those, **implemented the fix in the two paths this session has full, verified context on and controls directly** (the attachment free-text entity extraction built in the two previous QA passes, and the CSV column-projection renderer), and **inspected without modifying** the database-backed path, documenting what was found. Modifying the ~29,000-line `chatbot.py` DB-backed survey/application rendering blindly, without the context this session has for the attachment code it already built, would risk exactly the "modify unrelated functionality" the task explicitly prohibits.

## 2. Confirmed gap #1 — uploaded free-text documents (fixed)

**Before:** `_enumerate_entities_answer()` / `_single_entity_answer()` in `backend/services/attachment_qa.py` (built in the previous two passes) rendered a survey number as a bare value — "The survey numbers mentioned are: 101, 102, 103." — even when the same evidence chunk plainly also carried each one's subdivision number two lines below it ("Synthetic Sub-division Number: 101/1"). The association was never computed; only the survey-number regex ran for this question shape.

**Root cause:** no code anywhere associated a survey-number match with a nearby subdivision-number match. The subdivision-number entity pattern existed (added in the previous pass, for a *separate* bare "what subdivision number..." question) but was never cross-referenced against survey-number matches.

**Fix:**
- `survey_subdivision_pairs()` (new) — for each survey-number occurrence (after the existing neighbour-context exclusion), searches the text between it and the *next* survey-number occurrence in the same evidence chunk (capped at 400 characters, so a subdivision mentioned much later in a long chunk can never be wrongly attached to an earlier survey number) for subdivision-number matches, applying the *same* neighbour-context exclusion on that side too. Several subdivisions in that window are **all** kept — one survey number having more than one subdivision is not narrowed to the first. None found is recorded as an empty list, never guessed.
- `_render_survey_pairs_answer()` (new) — renders the pairing as default prose, a table (`"...in a table"` / `அட்டவணை`), or an occurrence count (`"how many...mentions/are there/are present"`), **always carrying the pairing**, never just in one rendering mode.
- `_render_survey_subdivision_table()` (new) — one row per (survey, subdivision) pair; a survey number with no subdivision gets exactly one row reading "Not specified in source"; a survey number with several subdivisions gets several rows.
- Wired into `plan_answer()`'s existing "one entity, or ask which" block for **both** the single-value answer path and the multi-value enumeration path. The disambiguation ("which one do you mean?") path for a genuinely singular, ambiguous question is **completely unchanged**.

**Files:** `backend/services/attachment_qa.py`.

**Verified live, before/after, same UI session:**
> "List every survey number mentioned." → "The survey numbers mentioned are: **101 (subdivision: 101/1); 102 (subdivision: 102/4); 103 (subdivision not specified in source); 104 (subdivision: 104/1A, 104/1B).** (101 (2×) mentioned more than once.)"

A new fixture, `SIS_Upload_QA_Fixtures/sis_multi_survey_test.txt`, was extended with a fifth record (survey 104, genuinely split into **two** distinct subdivisions, 104/1A and 104/1B) specifically to exercise the "one survey number, several subdivisions, all preserved" requirement, which no existing fixture covered. Ground truth was written independently (`ground_truth.json`'s `survey_subdivision_pairs_expected`) before this fix.

## 3. Confirmed gap #2 — CSV column projection (fixed)

**Before:** `In sis_survey_records_en.csv, list the synthetic_survey_no column.` → "row 1: 801 / row 2: 802" — each row's subdivision value (`synthetic_subdivision_no`, present in the *same row* of the *same* CSV) was silently dropped, even though the deterministic CSV engine already had the full row in hand.

**Root cause:** `csv_ops.execute()`'s `OP_FILTER`-with-column projection (`list the X column`) always isolated to exactly the one requested column: `res.value = [(n, (r.get(operation.column) or "").strip()) for n, r in matched[:20]]`. Nothing checked whether the requested column was itself a survey-number column with a sibling subdivision column sitting in the same row.

**Fix:**
- `_sibling_subdivision_column()` (new, `csv_ops.py`) — finds a survey-number column's own subdivision counterpart **by header name** (e.g. `synthetic_survey_no` → `synthetic_subdivision_no`), never by column position or row order. Returns `None` for any column that is not itself survey-number-shaped, or when no subdivision column exists in the file — so this never touches an unrelated column projection.
- `execute()`'s projection branch now pairs each survey-number cell with its own row's subdivision cell when a sibling column is found, carrying `None`/blank through honestly rather than guessing.
- `render_csv_result()` (`attachment_qa.py`) renders the 3-tuple shape as `"row N: <survey> (subdivision: <subdivision or Not specified in source>)"`, falling back to the original 2-tuple rendering unchanged for every other column.

**A genuinely recorded `"0"` value (NISD files with no sub-division, per CLAUDE.md's own documented data convention) is preserved verbatim, never replaced with "Not specified in source"** — `"0"` is a real recorded answer, not a blank cell; only a truly empty string triggers the "not specified" wording.

**Files:** `backend/services/csv_ops.py`, `backend/services/attachment_qa.py`.

**Verified live:**
> "In sis_survey_records_en.csv, list the synthetic_survey_no column." → "2 match(es): row 1: 801 (subdivision: 801/1) / row 2: 802 (subdivision: 802/1)"

## 4. The database-backed path — inspected, not modified

Per CLAUDE.md's own documented behaviour (confirmed, not assumed, by re-reading `get_application_detail()`'s documented fields and by this session's own earlier live transcripts from prior passes): the live-DB listing tables for ISD/NISD applications already render a "Survey No." column **beside** a "Sub-Divisions" column on every row (confirmed directly in this session's own earlier captured transcripts, e.g. `Application No. | Type | Survey No. | Sub-Divisions | Status | Stage | ...`), and `get_application_detail()` already exposes both `temporary_subdivision_number` and `final_subdivision_number` alongside a file's survey number. `_survey_attribute_answer()` (`chatbot.py`) — the single-field survey lookup used after an officer has already named a survey number — never emits a bare survey number as its OUTPUT; the survey number is always the INPUT key, and its own `"subdiv"` branch already answers "Survey X has N sub-division(s): ...". On the evidence inspected, **the DB-backed path already satisfies this requirement by construction** for the shapes checked. A full, line-by-line audit of every one of `chatbot.py`'s ~29,000 lines (four separate, near-duplicated `_field_keywords` blocks across the sync and streaming call paths) was **not** performed in this pass — flagged honestly as not exhaustively audited, rather than claimed complete.

RAG/document-QA answers (free-text prose questions that reach the LLM rather than the deterministic entity path) are **not** covered by this fix — `build_prompt()`'s rules were not extended to enforce the pairing on the model's own prose answers, because enforcing a structural rule on free-text LLM generation reliably is exactly the kind of prompt-only enforcement the task's own instructions (and this project's established "no evidence, no LLM" / "computed, never narrated" principles) warn against; the fix only applies where a **deterministic** extraction already runs.

OCR, DOCX, and multi-page/multi-chunk PDF association were **not** newly tested in this pass (see §6).

## 5. Entity association correctness (requirement #4)

Verified directly against ground truth, not inferred from the chatbot's own prose:

| survey number | ground truth subdivisions | pairing function's output | match |
|---|---|---|---|
| 101 | `["101/1"]` | `["101/1"]` | ✅ |
| 102 | `["102/4"]` | `["102/4"]` | ✅ |
| 103 | `[]` (genuinely none) | `[]` | ✅ |
| 104 | `["104/1A", "104/1B"]` | `["104/1A", "104/1B"]` | ✅ |

Explicitly checked: 101's subdivision never appears attached to 102; 102's subdivision never appears attached to 101; 104's two subdivisions never leak onto 103; 103's row renders exactly `"Not specified in source"`, never a value borrowed from a neighbouring record. A survey number that appears only in a boundary-reference sentence ("South - Town Survey Number 999") is excluded from the pairing output entirely, on both the survey side (pre-existing `_NEIGHBOUR_CONTEXT_RE` check) and the subdivision side (the same check newly applied to a subdivision match found inside a boundary-context window).

## 6. What was tested, and what was explicitly not

### Tested (English, both fixtures, live function calls and/or live UI)
- "List every survey number mentioned." (fixed)
- "List all survey numbers with their subdivision numbers." (classified as enumeration; pairs rendered)
- "Show every subdivision under each survey number." *(not a registered enumeration cue as written — falls through to the ordinary LLM path; see below)*
- "How many survey numbers and subdivisions are present?" (fixed — widened `_OCCURRENCE_COUNT_RE` to also match "are present"/"are there", not only "mentions")
- "Show all survey numbers and subdivision numbers in a table." (table rendering, verified)
- "List the synthetic_survey_no column." on a CSV (fixed, live UI verified)
- Tamil rendering of the pairing (identifiers preserved verbatim; verified at the function level)

### NOT tested or NOT fixed in this pass, reported honestly
- **"Find all subdivisions belonging to survey number 101."** — not a registered `wants_enumeration()` cue; this specific reverse-lookup phrasing (subdivisions of one NAMED survey number) was not implemented as its own shape. It currently falls through to the ordinary LLM/retrieval path, with no guarantee of the pairing discipline this fix otherwise provides.
- **"Give the survey number from the second row."** — this is the CSV row-lookup path (`OP_LOOKUP`), unaffected by and not exercised by this fix; it already returns the whole row (confirmed in §3's investigation), which inherently includes the subdivision column.
- **"Extract survey numbers from every page of this PDF."** — no multi-page PDF fixture with page-separated survey/subdivision data was built or tested in this pass. The pairing window logic (next-survey-number-in-the-SAME-evidence-chunk) has not been verified across a chunk/page boundary; a table split across two pages, or a subdivision on the page AFTER its survey number, is a **known, untested gap**.
- **Follow-ups** ("What about the second one?", "What other subdivisions belong to it?", "Only show unique survey numbers" preserving the full subdivision list, "What about the last one?") — this is the SAME follow-up-context gap already confirmed and reported, unfixed, in the previous pass's `SIS_Upload_Extraction_Consistency_Fix_Report.md` §6: the enumeration answer records no follow-up context, so none of these resolve. Still not fixed here — the fix for this pass was scoped to the pairing itself, not the pre-existing follow-up gap.
- **Tanglish and typo-tolerant phrasings** — same pre-existing lexical-evidence-floor and exact-substring-matching limitations already confirmed and documented in the previous pass; not re-investigated or fixed here.
- **OCR, scanned PDFs, low-quality scans, DOCX, Tamil-script documents** — no new fixture was built for this pass; the existing scanned-PDF fixture (`sis_scanned_records_en.pdf`) was not re-tested against this specific requirement.
- **Database-backed paths** — inspected (§4), not modified, not exhaustively audited.

## 7. Regression tests (task §8) — mapped

| # | requirement | status |
|---|---|---|
| 1 | a survey number is returned with its known subdivision | ✅ `test_survey_subdivision_pairing.py` |
| 2 | multiple subdivisions under one survey number are all preserved | ✅ dedicated test against survey 104 (two subdivisions) |
| 3 | different survey numbers are never assigned each other's subdivisions | ✅ explicit cross-contamination checks, all four survey numbers |
| 4 | enumeration returns all supported pairs, not merely the first | ✅ |
| 5 | missing subdivision numbers are flagged without fabrication | ✅ (103's case) |
| 6 | multi-page and multi-chunk results preserve entity association | ❌ **not tested** — no multi-chunk fixture built (§6) |
| 7 | follow-ups return the complete requested pair | ❌ **confirmed not working** — pre-existing gap, not fixed this pass (§6) |
| 8 | table modifications do not remove required identifiers | ✅ table-rendering test (both columns present on every row) |
| 9 | English, Tamil, Tanglish, spelling variants preserve association | ⚠️ **English and Tamil only** — Tanglish/typo inherit the pre-existing, already-documented gap |
| 10 | database and uploaded-document results obey the same rule wherever the source has the fields | ⚠️ uploaded-document path: ✅ fixed. Database path: inspected, found already largely compliant by construction, not independently re-verified end-to-end in this pass |
| 11 | session isolation and attachment authorization unchanged | ✅ no change to `attachment_store.py`'s session/officer scoping; existing `test_attachment_storage_safety.py` / `test_attachment_expiry_followup.py` re-run clean |
| 12 | existing working extraction and enumeration tests continue to pass | ✅ full sweep below |

## 8. Test results — all executed, none assumed

| suite | result |
|---|---|
| `SIS_Upload_Regression_Tests/test_survey_subdivision_pairing.py` (new) | 22/22 |
| `SIS_Upload_Regression_Tests/test_attachment_enumeration_consistency.py` | 26/26 |
| `SIS_Upload_Regression_Tests/test_attachment_single_entity_completion.py` | 17/17 |
| `SIS_Upload_Regression_Tests/test_attachment_intent_routing_gap.py` | 12/12 |
| `SIS_Upload_Regression_Tests/test_csv_negation_gap.py` | 18/18 |
| `SIS_Upload_Regression_Tests/test_multidoc_conflict_gap.py` | 23/23 |
| `test_attachment_routing.py` | 6/6 |
| `test_attachment_storage_safety.py` | 14/14 |
| `test_attachment_expiry_followup.py` | 13/13 |
| `test_document_relevance_and_corrections.py` | 28/28 |

No regression. No test weakened. Full detail in `SIS_Survey_Subdivision_Regression_Results.csv`.

## 9. Source diff

| file | change |
|---|---|
| `backend/services/attachment_qa.py` | `survey_subdivision_pairs()` (new), `_render_survey_subdivision_table()` (new), `_render_survey_pairs_answer()` (new), `_OCCURRENCE_COUNT_RE` / `_ENUMERATE_RE` widened to also catch "...are present"/"...are there" (not only "...mentions"), `render_csv_result()`'s `OP_FILTER` branch extended to render an optional 3rd (subdivision) element, the "one entity, or ask which" block's single-value and enumeration branches call the pairing path for `entity == "survey number"` |
| `backend/services/csv_ops.py` | `_sibling_subdivision_column()` (new), `execute()`'s `OP_FILTER`-with-column branch pairs a survey column with its row's subdivision column when one exists |
| `SIS_Upload_QA_Fixtures/sis_multi_survey_test.txt` | extended with Record E (survey 104, two genuinely distinct subdivisions) |
| `SIS_Upload_QA_Fixtures/ground_truth.json` | updated for Record E; new `survey_subdivision_pairs_expected` entry, written before this fix |
| `SIS_Upload_Regression_Tests/test_survey_subdivision_pairing.py` | new, 22/22 |
| `SIS_Upload_Regression_Tests/test_attachment_enumeration_consistency.py` | updated assertions for the extended fixture (3→4 unique values, 4→5 total mentions); one assertion made stricter (was accidentally passing on a substring coincidence) |

No schema change. No change to session isolation or attachment authorization.

## 10. Definition of done — honest status

**Met**, for the two paths fixed: every survey number returned by the attachment free-text enumeration/lookup path, and by the CSV column-projection path, now carries its subdivision number(s) whenever the source has them, multiple subdivisions are all preserved, missing values are explicitly flagged, and no identifier is invented or borrowed from another record — verified against independent ground truth and live through the real chatbot UI.

**Not fully met**, and explicitly flagged rather than hidden: multi-chunk/multi-page association, follow-up resolution, Tanglish/typo tolerance, OCR/DOCX/scanned-document coverage, RAG free-text prose answers, and an exhaustive audit of the database-backed path. Each is either a pre-existing, already-documented gap from an earlier pass, or a genuinely new boundary this pass did not have scope or time to build fixtures and tests for.
