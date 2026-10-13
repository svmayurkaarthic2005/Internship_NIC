# SIS Chatbot — Latest Commit Issue Review

**Commit reviewed:** `24a2138` "Initial Commit" (HEAD, `origin/main`), parent `3201769`
**Reviewed:** 2026-10-13
**Scope:** `git show HEAD --stat`, full diffs of the files the task names plus the ones the diff actually touched, cross-checked against live behaviour where practical.

## 1. What actually changed

`git show HEAD --stat` reports 63 files, +58943/-5544. The great majority of that is **new QA artefacts from prior audit passes** (fixtures, reports, regression-test files already committed by earlier sessions in this same engagement) plus the deletion of several superseded `sis_chatbot_*` report/schema files from the repo root. The **behaviourally significant** source changes are concentrated in seven files:

| File | +/- | What changed |
|---|---|---|
| `backend/services/attachment_qa.py` | +687/-? | New "project relevance" feature (`is_project_relevance_request`, `_build_project_relevance_assessment`, `_answer_project_relevance`) and new "negative correction" handling (`is_negative_correction`, `_handle_negative_correction`); multi-CSV comparison (`render_multi_csv_result`, the `len(csv_docs) >= 2` branch); `OP_FILTER`-with-no-column rendering fix |
| `backend/services/csv_ops.py` | +63/-16 | Symbol comparator fix (`>=`/`<=`/`!=`/`=` were silently stripped by `_norm()` before this fix); negated bare-value conditions ("except rejected") now produce a filter instead of falling through |
| `backend/services/chatbot.py` | +682/-213 | Tamil "today's date" facet rewrite (weekday vs date vs both, IST-aware); wiring for `is_negative_correction` / `is_project_relevance_request` at several call sites; `is_short_reply` / `get_prior_turn_language` for short Tanglish replies ("illa", "no") keeping the prior turn's language; misc fee/field-visit domain-word gating |
| `backend/services/rag.py` | +188/-27 | Clause-aware Tamil-language negation (`_explicit_tamil_request`) fixing "don't explain in Tamil"; `is_short_reply`, `get_prior_turn_language`; **removal of the bare `"?" in message` shortcut** from fee/field-visit heuristics; `_fee_non_domain` / `_fv_non_domain` gates to stop fee/field-visit words firing on off-topic questions |
| `backend/services/followup_context.py` | +11/-8 | **Removal of the bare `"?" in message` shortcut** from `names_a_field()` and `classify()` — this is the fix for exactly the "a question mark forces follow-up routing" failure mode Phase 7 of this task's own mission describes |
| `backend/services/attachment_store.py` | +15 | (not reviewed in depth this pass — small diff, time-boxed out) |
| `backend/routers/chat.py` | +38/-9 | New `GET /sessions/{id}/attachments/{id}/preview` endpoint; docstring update reflecting OCR addition |

`backend/services/doc_extract.py` is **not in the HEAD diff** (it predates this commit unchanged in its diff-relevant parts) but was read in this pass because the chat.py docstring pointed at it: it already contains a working Tesseract OCR fallback (English+Tamil) for scanned PDF pages, `OCR_MIN_CHARS_PER_PAGE`, `_ocr_pdf_pages()`.

## 2. New defects this commit introduces or leaves exposed

None of the live-reproduced defects in this pass's QA report were newly *introduced* by this commit in the sense of a regression from working code — they are **gaps this commit's new features expose or fail to close**:

- **CSVCOL-001a/b** (CSV "service codes" plural not resolved) is a pre-existing gap in `csv_ops.resolve_column()` that predates this commit, but this commit's own new multi-CSV comparison feature (`len(csv_docs) >= 2` branch, added here) is the first code path that could have closed it for the comparison case and does not — the comparison silently falls through to the ungrounded LLM path this commit's own comment (lines ~2191-2204) explicitly says it is meant to avoid.
- **SVCCODE-001** (F-Line Appeal alias collision) is pre-existing in `backend/utils/helpers.py`, untouched by this commit; it was found because this task's own Phase 2 explicitly asks for a similarly-named-code test, not because the commit changed this logic.
- **BREVITY-001**, **NEGCORR-001**, **FNAME-001**, **ORDCLAR-001**, **LINENO-001** are pre-existing gaps, not introduced by this commit. NEGCORR-001 is worth flagging specifically here: this commit added `is_negative_correction()` / `_handle_negative_correction()` as a genuinely good, well-reasoned fix for "I don't think so" / "not that" **inside attachment conversations** — but scoped it so narrowly (gated on `attachment_topic_active`) that the identical phrase outside an attachment conversation still gets the old generic fallback. The commit closed the gap in exactly one of the two places this task's Phase 6/7 asks about.
- **ARCH-001** (hardcoded "project relevance" prose) is a genuinely **new** pattern introduced by this commit — the single largest behavioural risk in the diff. It is flagged as a code-inspection finding (see the main QA report and failure case ARCH-001) rather than a confirmed live defect, because this pass could not get far enough through the file-disambiguation flow (blocked by FNAME-001/ORDCLAR-001) to capture its actual output on a non-trivial document.

## 3. Positive findings — things this commit fixed correctly

Worth recording, since the task asks for a fair audit, not just a defect list:

- **The "?" in message shortcut removal** in `followup_context.py` and `rag.py` is exactly the fix for Phase 7's "question marks and keywords improperly force a message into a follow-up route" concern. Spot-tested in this pass (not exhaustively): no regression found in the limited turns run.
- **CSV symbol comparators** (`>=`, `<=`, `!=`) were silently discarded by `_norm()` before this fix (every comparator question always matched `eq`, wrong for most of them) — a real, confirmed-by-reading bug closed correctly, with a clear before/after comment trail.
- **CSV negated bare-value conditions** ("show all records except rejected") now produce a real filter instead of falling through — same shape as the already-regression-tested `test_csv_negation_gap.py` from the prior pass; this commit's version adds negation to the *unqualified* categorical-value branch specifically.
- **Tamil-language negation** ("don't explain in Tamil") is a clean, well-documented, clause-aware fix (`_explicit_tamil_request`) with an explicit worked example in its own comment of the exact failure it replaces.

## 4. Missing tests

- No new automated test file accompanies the "project relevance" feature (`_build_project_relevance_assessment`, ~500 lines of new hardcoded prose) anywhere in the diff or the repo's existing `test_attachments.py` / `SIS_Upload_Regression_Tests/`.
- No new automated test accompanies `is_negative_correction()` / `_handle_negative_correction()` for the non-attachment path (consistent with NEGCORR-001: the gap was never tested because the feature was never built for that path).
- The multi-CSV comparison path (`render_multi_csv_result`, `len(csv_docs) >= 2`) has no dedicated regression test in the diff for the "service codes" (or any plural-column) phrasing — only the MULTIDOC-CONFLICT-001 fixtures from the prior pass, which use a singular, exact-match-friendly column name (`synthetic_survey_no`) that never exercises the plural-ambiguity bug this pass found.

## 5. Confirmed vs. suspected (summary)

| Status | Count | IDs |
|---|---|---|
| Confirmed, live-reproduced | 8 | SVCCODE-001, CSVCOL-001a, CSVCOL-001b, BREVITY-001, NEGCORR-001, FNAME-001, ORDCLAR-001, LINENO-001 |
| Plausible, code-inspection only (not independently verified live this pass) | 1 | ARCH-001 |
| Documentation drift (not a behaviour defect) | 1 | DOC-001 |

Full detail, reproductions, and root causes for every ID above are in `SIS_ServiceCode_FileDB_Failure_Cases.jsonl`.
