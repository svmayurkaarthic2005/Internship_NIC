# Workflow questions vs `backend/documents/` — findings

Scope: the workflow content of the RAG corpus (`workflow_guide.txt`,
`survey_manual.txt`, `faq_english.txt`, `land_rules.txt`,
`tamilnilam_urban_services_and_districts.txt`, `sis_upload_checklist.txt`,
`district_codes.txt`, `faq_tamil.txt`) — read in full and cross-checked against
each other, against `backend/config.py`, and against the invariants in
`CLAUDE.md`.

Same environment limit as before: no Postgres / Ollama / deps here, so nothing
was run through the live RAG path. Findings are from reading the corpus.

## Deliverable added

| file | what |
|---|---|
| `backend/sample_db/workflow_doc_question_bank.py` | generator — 138 curated workflow questions with a `[source]` file and an `expect` answer note each; `--txt` emits the flat form |
| `test_questions_workflow_docs.txt` | 138 questions (EN + TA/Tanglish) over 21 topics: ISD/NISD/MERGE chains, roles, field-visit rules, escalation, temp sub-division numbers, area validation, sub-division numbering, encroachment, litigation, merge eligibility, required documents, rejection/resubmission, submission channels, IGRS/SRO, service codes, district codes, land-type codes, legal basis, revenue arrears, upload checklist — plus **6 TRAP questions** (FMB, applicant e-mail, an `escalated` seed row, "which SIS signs the DSC") that must be refused, and **4 follow-up-reference chains** |

The bank is deliberately curated, not 1000 mechanical variants — the corpus
content is finite, and 138 questions with an answer key cover it and its edges.

## Contradictions found

### 1. District codes 34 / 35 / 37 were wrong in one corpus file — FIXED

`tamilnilam_urban_services_and_districts.txt` listed
`34: Tenkasi, 35: Chengalpattu, 37: Ranipet`.
`backend/config.py` `DISTRICT_CODE_MAP` **and** `district_codes.txt` both have
`34: Chengalpattu, 35: Ranipet, 37: Tenkasi`. Also spelling drift:
`Villupuram`→`Viluppuram` (07), `Thiruvarur`→`Tiruvarur` (20),
`Sivaganga`→`Sivagangai` (23), `Tirupattur`→`Tirupathur` (36).

A question like *"which district is code 34?"* would get opposite answers
depending on which chunk pgvector retrieved.

Fixed `tamilnilam_urban_services_and_districts.txt` to match `config.py` /
`district_codes.txt` exactly, with a note pointing at the source of truth.

### 2. NISD workflow string in `chatbot.py` invents a "Deputy Tahsildar" desk — FIXED

`chatbot.py` `service_code_guide`, both copies (L6573 and L10315), had:

> Citizen / CSC → SIS Officer (Document Verification) → **Deputy Tahsildar (Review) → Tahsildar (Digital Signature / DSC)** [No field visit required]

This contradicts every other source:
- `workflow_guide.txt` NISD Step 3: SIS → **Zonal Level Tahsildar**, one desk, who reviews *and* holds the DSC *and* approves.
- `tamilnilam_urban_services_and_districts.txt` L9: NISD → "Zonal Level Tahsildar (ZDT), who holds the DSC key and approves".
- `CLAUDE.md`: "NISD chain … application → SIS → Zonal Level Tahsildar (role 16; `TAHSILDAR` stage)".
- `chatbot.py`'s own `_stage_labels`: `"TAHSILDAR": "Zonal Level Tahsildar (ZDT)"`.

Changed both copies to:

> Citizen / CSC / Sub-Registrar → SIS Officer (Document Verification) → Zonal Level Tahsildar (Review + Digital Signature / DSC + Approval) [No field visit required]

`py_compile` clean; `test_service_code_queries.py` and
`test_intent_coverage.py` do not pin this string (grep‑checked).

### 3. `applicant_email` listed as a stored field — FIXED

`tamilnilam_urban_services_and_districts.txt` L53 listed `applicant_email`
under "Applicant Details". `get_application_detail()` returns no e-mail and
`CLAUDE.md`/AGENTS note "no email addresses stored for applicants (only mobile
numbers)". Replaced with an explicit "no e-mail address is stored" note.

### 4. ISD chain in `tamilnilam_urban_services_and_districts.txt` L9 stopped at DIS — FIXED

It sent verified ISD/Merge "to the SD … then to the DIS" and stopped, while
naming the DSC step only for the NISD branch. `workflow_guide.txt` Step 6 and
`CLAUDE.md` both have `SIS → SD → DIS → Tahsildar (DSC)`. Added
"…and after DIS approval to the Tahsildar, who applies the DSC and generates
the patta transfer order".

(Also tidied `(Colormap )` → `(Colour Map)` on L8.)

## Not fixed — flagged for your call

### A. `chatbot.py` `service_code_guide` 0155 (MERGE) entry looks wrong

```
"sla_days": "15 working days",
"workflow": "Citizen / CSC → SIS Officer (Field Boundary & Total Merged Area
             Verification) → Tahsildar (Digital Signature / DSC)"
```

Every doc says **MERGE follows the ISD chain** (`workflow_guide.txt` L106,
`survey_manual.txt` merge process = SIS → field visit → DIS approval → revenue
update, `tamilnilam` L9). `survey_manual.txt` gives the merge total as
**25-30 working days**, not 15. Suggested:

```
"sla_days": "25-30 working days",
"workflow": "Citizen / CSC / Sub-Registrar → SIS Officer (Field Visit within
             15 days) → Senior Draughtsman (SD Sketch) → DIS (Approval) →
             Tahsildar (Digital Signature / DSC)"
```

Left alone because the seeded MERGE `workflow_history` may be truncated the
same way ISD's is (`SIS → SD → COMPLETED`, see CLAUDE.md), and I could not run
`test_service_code_queries.py` / `test_fee_queries.py` to confirm the fee
figures beside it are still right.

### B. Corpus describes DIS + Tahsildar hops for ISD that the seed data does not contain

`workflow_guide.txt` Steps 5-6, `survey_manual.txt`, `faq_english.txt` and
`tamilnilam` all describe DIS assigning temporary sub-division numbers and the
Tahsildar applying the DSC. `CLAUDE.md` states the seeded `workflow_history`
for ISD is only `SIS → SD → COMPLETED/REJECTED` — no `DIS`, no `TAHSILDAR` hop
— and that `build_app_tables.py` would have to synthesise them.

This is **canonical process vs. truncated seed data**, already documented in
CLAUDE.md, not a corpus contradiction — the corpus should keep describing the
real process. But it means "who assigned the temporary sub-division number on
2022/0154/28/000779?" is answered "DIS" from the corpus while the file's
workflow history shows no DIS hop. If you want them to agree, the fix is in
`build_app_tables.py` (synthesise `8→12` and `12→16`), not in the documents.
`test_temp_subdivision_queries.py --csv` already guards the doc-vs-CSV claims.

### C. `faq_tamil.txt` is UTF-16 LE; every other doc is UTF-8

`ingest.py` L191-196 handles it (UTF-8 attempt, UTF-16 fallback), so retrieval
works. Converting it to UTF-8 for consistency is optional.

## Suggested runs (Windows)

```powershell
python -m backend.ingest                        # re-embed the corrected corpus
python test_service_code_queries.py             # confirm A/#2 did not regress
python test_temp_subdivision_queries.py --csv   # doc claims vs CSV
# then feed test_questions_workflow_docs.txt through the chat harness and check
# each answer against its [source]/expect line; every TRAP must be refused.
```
