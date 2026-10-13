# SIS Chatbot — Enumeration vs Targeted-Lookup Consistency Fix Report
**Date:** 2026-10-13
**Officer used for live verification:** `marun_chn@sis.tn.gov.in`
**No production database was seeded, cleared or modified. No commit/push was made. No test value was hardcoded into production behavior.**

## 1. The reproduced bug

| question | before fix | document |
|---|---|---|
| "List every survey number mentioned." | "I could not find survey numbers in the uploaded document." | `sis_extraction_test_en.txt` (ONE survey number, 777) |
| "In the uploaded file, what is the synthetic survey number?" | "The synthetic survey number is 777." | same document, same session |

Both questions were re-run live through the real chatbot UI, in the same session with the same document, and in a fresh conversation with the same document re-uploaded — the inconsistency reproduces identically in both, so it is not a conversation-history artefact. `sis_extraction_test_en.txt`'s independent ground truth (`SIS_Upload_QA_Fixtures/ground_truth.json`, written by the fixture builder before any chatbot answer was seen) confirms the file contains exactly one survey number, `777`, and no other survey-number mention.

This is the same underlying defect already investigated and partially fixed in the previous pass (`SIS_Upload_Bug_Fix_Report.md` §1.2). This report documents the deeper, second form of it found by widening the investigation per this task's own instructions — specifically, by building a fixture with **more than one** survey number, which no existing QA fixture had, and which turned out to still be broken.

## 2. Tracing both requests through the implementation

Both questions reach the same place: `chatbot.py`'s `general_query` fallback → `attachment_qa.plan_answer()` → `_answer_from_selected()` in `backend/services/attachment_qa.py`. Up to one point they are identical:

1. `active_documents()` (`attachment_store.py`) resolves the session's attached document(s).
2. `attachment_store.retrieve_evidence()` scores stored `attachment_chunks` rows by a lexical floor (`UPLOAD_MIN_EVIDENCE_SCORE`, 0.4, applied to `Evidence.lexical` only — confirmed by reading `retrieve_evidence()`'s own filter, `kept = [e for e in scored if e.lexical >= floor]`) blended with a vector re-ranking that can never manufacture evidence on its own.
3. `_asked_entity(message)` recognises "survey number" / "subdivision number" / "application number" wording and, if matched, `distinct_entities()` extracts every value from the retrieved evidence via a dedicated regex (`_ENTITY_PATTERNS`) **before either question ever reaches the LLM.**

The two questions diverge at step 3 only in how many values `distinct_entities()` finds:

- One value → (as of the previous pass's fix) answered deterministically.
- **Two or more values → `KIND_CLARIFY`, "...contains N survey numbers: ..., Which one do you mean?", unconditionally — regardless of what the question's own wording asked for.**

That unconditional branch is the actual defect. It was invisible on the single-fixture dataset the previous pass used, because a one-value document never reaches the `len(values) > 1` branch at all.

## 3. Confirming document coverage, not just that a targeted question worked

A new fixture, `SIS_Upload_QA_Fixtures/sis_multi_survey_test.txt`, was built specifically because no existing fixture could expose this: three records naming survey numbers 101, 102 and 103, with 101 genuinely repeated (Records A and C — the same fictional parcel mentioned twice), plus a fourth, **deliberately excluded** mention: Record D's boundary note, "South - Town Survey Number 999", a *neighbouring* parcel's number that must never be counted as this document's own. Ground truth was written to `ground_truth.json` before any question was asked.

Direct inspection (not inference from the chatbot's own answer):
- `attachment_store.retrieve_evidence()` was called directly and its retrieved chunk's full text inspected — the chunk genuinely contains all four mentions.
- `attachment_qa.entity_occurrences()` (new; see §4) was called directly against that evidence and correctly returns `[("101", 2, ...), ("102", 1, ...), ("103", 1, ...)]` — the neighbour's "999" is correctly excluded by the pre-existing `_NEIGHBOUR_CONTEXT_RE` check (unchanged by this fix), and the genuine repeat of 101 is correctly folded into one entry with a count of 2, not two competing entries.

So the broad extraction request was never actually losing information at the retrieval or extraction stage — every value was already being found correctly. The information was lost **immediately afterward**, in the branch that decided how to respond once the values were in hand: the unconditional `len(values) > 1 → ask which one` rule discarded the fact that all three values had already been correctly extracted.

## 4. The fix

`backend/services/attachment_qa.py`:

- **`entity_occurrences()`** (new) — `distinct_entities()`'s former matching loop, pulled out and extended to also return each value's genuine occurrence count and the evidence it was first found in, in document order. `distinct_entities()` is now a one-line wrapper over it, so every existing caller is unaffected.
- **`wants_enumeration()`** (new) — recognises "list every/all/the ... mentioned", "find all", "extract every/all", "show all", "all survey numbers", "every survey number", "are there any", "any more", "how many ... mentions" (English), plus `எல்லா` / `ella` / `irukka` (Tamil/Tanglish — see §6's caveat on how far this actually reaches).
- **The "one entity, or ask which" block** in `_answer_from_selected()` now checks `wants_enumeration()` **before** the existing `len(values) > 1` clarify branch. When the question asks for enumeration and at least one value was found, every genuinely distinct value is listed via a new **`_enumerate_entities_answer()`**, which also distinguishes:
  - the default (unique values, with a note on which ones repeat),
  - `"...including repeated occurrences"` (every mention, duplicates included),
  - `"how many ... mentions"` (the total occurrence count, not the unique count),
  - `"unique"` / `"distinct"` (unique values, repeat note suppressed since the officer explicitly asked for just the unique set).
  The disambiguation path (`len(values) > 1`, no enumeration wording) is **completely unchanged** — a genuinely singular question against an ambiguous multi-value document still asks "which one do you mean?", exactly as before.

No value is invented: every number stated comes from the same regex extraction the disambiguation path already trusted, never from the LLM. No test value (`777`, `101`, etc.) is hardcoded anywhere in the production matching or rendering logic — the fixtures exist only in `SIS_Upload_QA_Fixtures/`.

## 5. Before / after, both verified live

| question | document | before | after |
|---|---|---|---|
| "List every survey number mentioned." | `sis_extraction_test_en.txt` (1 value) | "I could not find survey numbers..." | "The survey number mentioned is 777." |
| "List every survey number mentioned." | `sis_multi_survey_test.txt` (3 values, 1 repeated) | "...contains 3 survey numbers: 101, 102, 103. Which one do you mean?" | "The survey numbers mentioned are: 101, 102, 103. (101 (2×) mentioned more than once.)" |
| "Extract every survey number, including repeated occurrences." | `sis_multi_survey_test.txt` | (same unconditional clarify) | "The survey numbers mentioned, including repeats, are: 101, 101, 102, 103 (4 total)." |
| "What is the synthetic survey number?" | `sis_extraction_test_en.txt` (1 value, unambiguous) | "The synthetic survey number is 777." | unchanged — still "The survey number mentioned is 777." |
| "What is the synthetic survey number?" | `sis_multi_survey_test.txt` (3 values, genuinely ambiguous) | "...Which one do you mean?" | unchanged — still asks which one (correct: this question did NOT ask for all of them) |

## 6. The full phrasing matrix — what was actually run, and what was not

### English — run against `sis_multi_survey_test.txt`

| phrasing | result |
|---|---|
| List every survey number mentioned. | ✅ enumerates (101, 102, 103; notes 101 repeats) |
| List all survey numbers. | ✅ enumerates |
| Find all survey numbers in the uploaded file. | ⚠️ refused — see lexical-floor gap below |
| Extract every survey number, including repeated occurrences. | ✅ lists all 4 mentions including the repeat |
| List all unique survey numbers. | ⚠️ refused — see lexical-floor gap below |
| What is the survey number in the second row?" | not meaningfully testable on this fixture (no "rows" — a free-text document, not a table); left to the existing CSV row-lookup path, untouched |
| Show all survey numbers and subdivision numbers in a table. | **not implemented** — multi-entity table rendering was out of scope for this pass; still falls through to the ordinary LLM path |
| How many survey-number mentions are there? | ⚠️ refused (same lexical-floor gap) when tested standalone, but the SAME wording correctly states "4" when reached via `_enumerate_entities_answer()` directly — see the gap explanation below |
| Give the page number for each survey number. | not implemented; this fixture is a single-chunk TXT with no page boundaries to cite |
| Are there any survey numbers in this document? | ✅ enumerates (reasonable: a yes/no that also answers the "yes, here they are") |

**The lexical-floor gap, found and NOT fixed in this pass:** `retrieve_evidence()`'s floor (0.4) is checked against the **lexical** score component only, by design (`CLAUDE.md`'s own documented rule: "lexical decides whether there is evidence; the vector only reorders... a confident embedding can never manufacture evidence"). "List all unique survey numbers.", "Find all survey numbers in the uploaded file." and (standalone) "How many survey-number mentions are there?" each contain enough *extra, undistinctive* words ("unique", "find", "uploaded", "file", "how many", "mentions" — several of which are already in `_GENERIC_FIELD_TERMS`) that the ratio of matched-to-total distinctive words falls to 0.25–0.33, just under the 0.4 floor, even though the one genuinely distinctive word ("survey") is present in the document. This is a **pre-existing, general characteristic of the lexical evidence floor**, not something this fix introduced or could safely patch without touching a shared anti-hallucination mechanism used by every attachment question in the system — out of this pass's authorized scope (confirmed defect + its regression tests only). Documented here rather than silently left unexplained.

### Follow-ups — tested, found NOT working, honestly reported as unresolved

| follow-up (after "List every survey number mentioned.") | result |
|---|---|
| "What is the first one?" | ❌ "Nothing has been shown in this conversation yet..." |
| "What about the second one?" | ❌ same fallback |
| "Are there any more?" | ❌ "I'm not sure what you mean." |
| "Now show only unique numbers." | ❌ "Nothing has been shown..." |

**This is a genuine, confirmed gap, NOT fixed in this pass.** The enumeration answer (`_enumerate_entities_answer()`) records no follow-up context (`FollowupContext`) for the list it just gave, so a follow-up question has nothing to resolve against and falls through to the ordinary application-list follow-up machinery, which this isn't. Building proper follow-up-context support for an entity enumeration (a new `FollowupContext` entity kind, wired through `followup_context.py` and `_load_followup_context()` in `chatbot.py`) is a materially larger change than the confirmed defect this pass was authorized to fix, and was not attempted. Item 6 of this task's regression-test list ("a follow-up requesting all remaining values must not repeat only the first result") is **not met** and is reported as an open defect, not silently left out.

### Tamil and Tanglish — tested, found NOT working for this entity path

"ella survey number-um kudu.", "Innum survey numbers irukka?" — both return the generic refusal. Root cause (confirmed, not fixed): the same lexical-floor mechanism above; these phrasings' English loanwords ("survey", "number") sit beside enough Tamil/Tanglish grammar words that are not in `_ALIASES`/`_term_groups`'s cross-script table to again fall under the 0.4 lexical floor. **Not fixed in this pass** — language-robustness for this specific entity-enumeration path is a separate, larger body of work than the confirmed defect. (General Tamil/Tanglish support elsewhere in the attachment pipeline, e.g. plain field lookups via `_ALIASES`, is unaffected and untouched.)

### Noisy input — tested

| input | result |
|---|---|
| "LIST EVERY SURVEY NUMBER MENTIONED" (all caps) | ✅ works (case-insensitive matching, unaffected) |
| "list   every  survey number   mentioned" (extra spaces) | ✅ works |
| "List evry survay numbr mentioned." (typos) | ❌ refused — `_ENTITY_QUESTION_WORDS`/`_ENUMERATE_RE` are exact-substring, not typo-tolerant, a **pre-existing** characteristic of this code path (the rest of the SIS chatbot's intent routing uses `is_token_typo_match` for typo tolerance; the attachment entity path does not). Not fixed in this pass — would need the same edit-distance matching threaded through a different module, a larger change than the confirmed defect.

## 7. Validation against independent ground truth

`SIS_Upload_QA_Fixtures/ground_truth.json` (both fixtures), compared as structured values, never derived from the chatbot's own output:

| | expected | actual (after fix) | match |
|---|---|---|---|
| `sis_extraction_test_en.txt` unique survey numbers | `["777"]` | `["777"]` | ✅ |
| `sis_multi_survey_test.txt` unique survey numbers, in order | `["101", "102", "103"]` | `["101", "102", "103"]` | ✅ |
| `sis_multi_survey_test.txt` total genuine mentions | 4 | 4 | ✅ |
| `sis_multi_survey_test.txt` occurrences of "101" | 2 | 2 | ✅ |
| neighbour's boundary number (999) counted as this document's own | must NOT appear | does not appear | ✅ |
| invented identifiers | none | none observed in any test run | ✅ (no case in this pass produced a number not present in the fixture) |
| duplicate handling | 101 deduped to one entry, count preserved | same | ✅ |
| source/page attribution | n/a (single-chunk TXT fixtures, no page boundaries) | citations carried through (`sources`/`document_ids`/`citations` on the `AttachmentPlan`) but not exercised against a multi-page document in this pass | not fully validated |

## 8. Regression tests (item 7 of the task) — mapped to what was actually added

| # | requirement | status |
|---|---|---|
| 1 | broad enumeration returns 777 for the single-value fixture | ✅ `test_attachment_enumeration_consistency.py` + `test_attachment_single_entity_completion.py` |
| 2 | targeted lookup returns 777 | ✅ `test_attachment_single_entity_completion.py` |
| 3 | both requests use the correct uploaded document | ✅ implicit in every test (evidence is always session+officer+document-id scoped; see `test_attachment_storage_safety.py` for the dedicated authorization tests) |
| 4 | an unrelated numeric value is not falsely classified as a survey number | ✅ the neighbour-boundary case (`"999"` excluded), both as a dedicated unit test and inside the multi-value fixture itself |
| 5 | missing/unreadable content must not produce invented identifiers | ⚠️ not specifically exercised in this pass (no OCR-failure or corrupt-file fixture was re-tested here); the existing architecture's documented rule (no evidence → the fixed `REFUSAL_EN` string, never the LLM) was not re-verified end-to-end in this pass specifically for survey-number enumeration |
| 6 | a follow-up requesting all remaining values must not repeat only the first result | ❌ **confirmed NOT working** — see §6; reported as an open defect |
| 7 | multiple identifiers across chunks/pages must all be discoverable | ⚠️ multiple identifiers within ONE chunk: ✅ verified (the multi-survey fixture is one TXT chunk holding 3 distinct + 1 repeated value, all found). Identifiers split across SEPARATE `attachment_chunks` rows (e.g. a multi-page PDF): **not exercised** — no multi-chunk fixture was built or tested in this pass |
| 8 | Tamil/Tanglish variants preserve extraction meaning where supported | ❌ tested, found NOT working for this entity path (§6); reported, not fixed |
| 9 | separate sessions must not read each other's attachments | ✅ covered by the existing, unmodified `attachment_store.retrieve_evidence()` session+officer scoping, and by `test_attachment_storage_safety.py`'s dedicated authorization tests (unaffected by this change) |
| 10 | an incomplete extraction must be reported honestly | ✅ by design — a question with no qualifying evidence gets the literal, fixed refusal string, never a partial or invented answer; this remains true after this fix (the lexical-floor cases in §6 refuse rather than guess) |

New test file: `SIS_Upload_Regression_Tests/test_attachment_enumeration_consistency.py` — 26/26 passing (unit-level: `wants_enumeration()` classification over the full English phrasing matrix tried in this pass, `entity_occurrences()` correctness against ground truth, the enumerate/unique/include-repeats/count-mentions renderers, the neighbour-exclusion safeguard, and the original single-value fixture re-asserted as a permanent regression case).

## 9. Test results

All suites actually executed (none assumed):

| suite | result |
|---|---|
| `SIS_Upload_Regression_Tests/test_attachment_enumeration_consistency.py` (new) | 26/26 |
| `SIS_Upload_Regression_Tests/test_attachment_single_entity_completion.py` | 17/17 |
| `SIS_Upload_Regression_Tests/test_attachment_intent_routing_gap.py` | 12/12 |
| `SIS_Upload_Regression_Tests/test_csv_negation_gap.py` | 18/18 |
| `SIS_Upload_Regression_Tests/test_multidoc_conflict_gap.py` | 23/23 |
| `test_attachment_routing.py` | 6/6 |
| `test_attachment_storage_safety.py` | 14/14 |
| `test_attachment_expiry_followup.py` | 13/13 |
| `test_document_relevance_and_corrections.py` | 28/28 |

No regression. No test was weakened to pass. Full detail in `SIS_Upload_Extraction_Regression_Results.csv`.

## 10. Source diff

| file | change |
|---|---|
| `backend/services/attachment_qa.py` | `entity_occurrences()` (new, `distinct_entities()` now wraps it), `wants_enumeration()` (new), `_enumerate_entities_answer()` (new), the "one entity, or ask which" block gains an enumeration branch ahead of the existing clarify branch |
| `SIS_Upload_QA_Fixtures/sis_multi_survey_test.txt` | new fixture (3 survey numbers, 1 genuinely repeated, 1 neighbour-boundary exclusion case) |
| `SIS_Upload_QA_Fixtures/ground_truth.json` | new entry for the above, written before any chatbot answer was seen |
| `SIS_Upload_Regression_Tests/test_attachment_enumeration_consistency.py` | new regression test, 26/26 |

No schema change. No change to session isolation, authorization, or the lexical evidence floor itself.

## 11. Definition of done — honest status

"The same uploaded document produces consistent, source-grounded results for both targeted identifier lookup and complete enumeration" — **met** for the confirmed defect: every phrasing that reaches the entity-matching path (i.e., clears the pre-existing lexical evidence floor) now answers consistently and correctly, whether it asks for one value or all of them, on both a one-value and a multi-value fixture, verified against independent ground truth.

**Not met**, and explicitly flagged rather than hidden: the follow-up chain (§6, item 6), Tamil/Tanglish for this entity path (§6, item 8), typo tolerance, multi-chunk/multi-page discovery (item 7), and the lexical-floor gap for a handful of "all/unique/how many" phrasings that dilute the distinctive-word ratio below 0.4. Each has a confirmed root cause recorded above; none were fixed in this pass, because each is a separate, materially larger change than the confirmed defect this pass was authorized to address.
