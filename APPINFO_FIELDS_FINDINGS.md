# NISD/ISD application-info fields — test bank + findings

Scope: the 43 columns of `nisd_transfer_application_info` /
`isd_transfer_application_info` (and the overlapping `urban_application_log`
columns) the user listed, asked the way an SIS officer would, in EN / Tamil /
Tanglish, including previous-message reference chains.

Same environment limit: no Postgres / Ollama / deps here, so findings are from
a static trace of `parse_intent` (`rag.py`), the two `_field_map` blocks + the
field-lookup gates in `chatbot.py`, `get_application_detail()` in
`postgres.py`, and the projection in `build_app_tables.py`.

## Deliverables

| file | what |
|---|---|
| `backend/sample_db/appinfo_question_bank.py` | generator — `build_bank()` → **1320 `Question` records**; `--txt` emits the flat form; no DB/LLM |
| `test_questions_appinfo_fields.txt` | 1320 questions (EN + TA/Tanglish) over all 43 columns: 960 single-field, 90 multi-field, 260 follow-up turns (5 chains × 10 apps), 10 rule-shaped. Each column tagged `field_map` / `intent` / `not_projected` |

Coverage: 744 questions on columns that resolve to a value, 60 on
intent-handled columns, **456 on columns the chatbot has no data for**.

## How each column is answered today

| group | columns | verdict |
|---|---|---|
| **field_map — resolves to a value** | application_id, district_code, taluk_code, ward_code, block_code, can_number, applicant_name, current_address, mobile_number, application_status, last_updated_datetime, permanent_address, mother_name, father_name, date_of_birth, gender, challan_number, payment_mode, payment_amount, igrs_form6_number, merged_application_id, proposed_field_visit_date | ok |
| **intent-handled** | missing_documents → `check_documents`; remarks → workflow-history / `sd_remarks` | ok (remarks only via "workflow history for X" — bare "remarks on X" still falls through, same open item as round 1) |
| **FIXED — was a gap** | `occupation` (stored on `applicants` but never returned by `get_application_detail`, and not in `_field_map`); `town_code` (only "urban unit code" was mapped, so "town code" answered with the town *name*) | now resolve — see below |
| **FIXED — now deterministic "not held", was LLM / wrong** | challan_date, treasury_name, bank_name, bank_branch, barcode_flag, enclosure_details, enclosure_certificate, first/reverse/last_page_document, physical_verification_status, document_sent_date, document_received_date, return_status, owner_correction_reason, auto_mutated_flag, proposed_remarks | see below |

`challan_date` and `document_received_date` were the worst: the bare
`"challan"` and `"received"` keys in `_field_map` caught them and answered with
the challan **number** / the **submission date** respectively — a confident
wrong answer.

## Fixes applied

### 1. `occupation` — now resolved (`postgres.py` + `chatbot.py`)

- `get_application_detail()` now returns `"applicant_occupation"`
  (`app.applicant.occupation`, already populated by `build_app_tables.py`).
- Both `_field_map` copies gain `occupation` / `profession` / `தொழில்` →
  `("applicant_occupation", "Occupation")`.

### 2. `town_code` — now resolved (`chatbot.py`)

Both `_field_map` copies gain `town code` / `town_code` →
`("urban_unit_code", "Urban Unit Code")`, beside the existing
`urban unit code` key.

### 3. 16 not-projected application-info columns — deterministic gate

New `_UNTRACKED_APPINFO_FIELDS` / `_asked_untracked_appinfo_field()` /
`_untracked_appinfo_field_answer()` next to the existing
`_UNTRACKED_WF_FIELDS` (round 1) and `_UNTRACKED_SOURCE_FIELDS`, and one `elif`
added to each of the two field-lookup gates (non-streaming + streaming),
directly after the `_asked_untracked_source_field` branch.

A pointed question that names one of these columns **and** an application
number now gets:

> Application 2022/0153/28/000254's challan date is not held in your SIS
> register. It exists only in the source application-info extract
> (nisd_/isd_transfer_application_info / challan_date), which this assistant
> does not query. I can give you the applicant details, the fee and challan
> number, the payment mode, the document status and the field visit.

in English and Tamil — instead of an ungrounded LLM answer or the
`challan`→number / `received`→submission-date misfire. The gate runs before
`_fuzzy_match_all_fields`, so it pre-empts the wrong match (same rationale the
`_UNTRACKED_SOURCE_FIELDS` block already used for `land type` → `application_type`).

`py_compile` clean on all four files. The gate only fires on its cue sets and
only inside the `app_no` branch — a rule question with no application number
("what does the auto mutated flag mean?") still goes to RAG / `land_rules.txt`.

## Not fixed — recommended

1. **bare "what are the remarks on `<app>`"** (no `sd` token) still falls to
   the LLM; it is partly answerable from `workflow_history.remarks`. Route it
   to `_render_workflow_history` (or widen `_WORKFLOW_HISTORY_RE`). Same item
   flagged in `WORKFLOW_ACTION_FIELDS_FINDINGS.md`.
2. **"who last updated `<app>`"** answers with a timestamp (matches "last
   updated"), not the person.
3. A **combo question mixing a resolved and a not-projected field**
   ("challan number, challan date, payment mode and amount on X") is caught by
   the `challan_date` cue and answered with the whole not-held sentence,
   dropping the parts that *are* available. The sentence does point the
   officer at what I can give, so it degrades sanely, but a combined answer
   would be better.

## Suggested runs (Windows)

```powershell
python -m backend.sample_db.test_intent_coverage
python check_missing_values.py            # confirm applicant.occupation is populated
# feed test_questions_appinfo_fields.txt through the chat harness; check the
# not_projected section says "not held in your register" every time and never
# invents a bank / treasury / date.
```
