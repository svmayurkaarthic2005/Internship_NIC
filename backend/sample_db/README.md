# sis_chatbot_db — main application database

The main PostgreSQL database `sis_chatbot_db` holding all application tables
and data for the SIS Chatbot system.

**Structure follows the CSVs. Data does not.** Every column list is read
straight from the CSV header, so the tables match the real extract shape.
The rows are generated: code values, statuses, workflow role hops and
application-number formats follow the extracts and `backend/documents/*.txt`,
but every identity-bearing value — owner and applicant names, officer
usernames, CAN / Aadhaar / mobile / document numbers, addresses, digital
signature blobs — is synthetic and verified not to reuse anything from the CSVs.

## Files

| File | Purpose |
| --- | --- |
| `schema_builder.py` | Reads the CSV headers, infers PostgreSQL types, emits DDL |
| `identifiers.py` | The Aadhaar and CAN rules -- shared by the seed and the projection |
| `dsc.py` | Generates the X.509 certificates and PKCS#7 signatures for the DSC columns |
| `schema_sis_chatbot_db.sql` | Generated DDL (regenerate, don't hand-edit) |
| `seed_sample_db.py` | Creates the database, applies the DDL, generates and inserts rows |
| `verify_sample_db.py` | Checks structure, references, population, signatures and non-leakage; exits non-zero on failure |
| `build_app_tables.py` | Projects the sample tables into the app's ORM tables (applications, owners, field visits, ...) |
| `verify_identifiers.py` | Checks every Aadhaar and CAN in both layers against `identifiers.py`; exits non-zero on failure |
| `check_app_wiring.py` | Smoke test: proves the app queries and the chatbot answer from this database |
| `test_questions.py` | Runs a matrix of SIS questions through the chatbot and asserts the answers |
| `test_intent_coverage.py` | Routes one question per intent (59 cases, no DB/LLM) and reports misroutes |
| `test_workflow_logic.py` | Checks the workflow rules hold in the data and that answers agree with each other |
| `test_date_queries.py` | Checks date phrases resolve to the right range and that the answer matches the register for that period |

## Run

```bash
python backend/sample_db/seed_sample_db.py            # 1. create db + seed the 16 CSV-shaped tables
python backend/sample_db/verify_sample_db.py          # 2. verify them
python -m backend.sample_db.build_app_tables          # 3. project them into the app's tables
python -m backend.ingest                              # 4. load document embeddings (into this db)
python -m backend.sample_db.verify_identifiers        # 5. verify the Aadhaar / CAN formats
python -m backend.sample_db.check_app_wiring --chat   # 6. prove the app answers from here
python -m backend.sample_db.test_intent_coverage      # 7. routing (instant)
python -m backend.sample_db.test_workflow_logic       # 8. workflow rules + answer consistency
python -m backend.sample_db.test_date_queries         #    date phrases + date-scoped answers
python -m backend.sample_db.test_questions            # 9. answer quality (--fast skips LLM cases)
```

All connect to `127.0.0.1:5432` as `postgres`. The seed uses a fixed RNG seed,
so re-running produces the same rows.

## How the application uses this database

`.env` points `DATABASE_URL` / `SYNC_DATABASE_URL` at `sis_chatbot_db`, so the
API, the query service, the RAG document store and the chatbot all read from
here. Two places used to hardcode the database name and ignore `.env` —
`backend/database.py` (the async engine) and
`backend/services/pgvector_store.py` (the vector store); both now derive it
from settings. The `@` in the password must stay `%`-encoded in `.env`
(`Mayur%402005`) or the URL parses with the wrong host.

The chatbot answers through `backend/services/postgres.py`, which queries the
ORM models in `backend/models.py` — not the CSV-shaped tables directly. So this
database holds **two layers**:

1. the 16 CSV-shaped tables — the source of record, seeded from the extracts;
2. the app's ORM tables — a projection built from them by `build_app_tables.py`.

Rebuilding the projection is idempotent: it truncates what it owns and derives
everything again. `knowledge_embeddings` is left alone.

What the projection maps:

| Sample table | App table |
| --- | --- |
| `urban_parcel_register` | `districts` → `blocks`, `survey_numbers`, `sub_divisions` |
| `urban_natham_chitta_owner` | `owners`, `survey_ownership` |
| `urban_application_log` | `applications` (+ `applicants`, `application_documents`) |
| `application_workflow_action` | `workflow_history`, `field_visits` |
| `urban_temp_subdivision_parcel` | `application_sub_divisions` |
| `nisd_/isd_transfer_urban_detail` | `patta_transfers` |
| workflow usernames at role 41 | `sis_officers`, `officer_jurisdictions` |

Only service codes `0153` (NISD), `0154` (ISD) and `0155` (MERGE) become
`applications` — the model's `ck_application_type` admits no others, so
settlement and govt-to-private rows (`0167`, `0169`, …) stay in the CSV-shaped
tables only.

Officers are the SIS usernames that open the workflow chain, one ward each, so
jurisdiction filtering has real effect. Sign in with the seeded emails
(e.g. `amuthavalli@sis.tn.gov.in`) and password `Test@1234`.

## Tables

CSV file → table name (the `_demo` suffix is dropped, names tightened):

| CSV | Table | Rows |
| --- | --- | --- |
| `appl_log_urban_demo.csv` | `urban_application_log` | 1180 |
| `application_workflow_demo.csv` | `application_workflow_action` | 4589 |
| `areg_temp_subdivclub_demo.csv` | `urban_temp_subdivision_parcel` | 49 |
| `chitta_temp_subdivclub_demo.csv` | `urban_temp_subdivision_owner` | 117 |
| `full_field_patta_transfer_application_information_demo.csv` | `nisd_transfer_application_info` | 166 |
| `full_field_patta_transfer_igrs_owner_demo.csv` | `nisd_transfer_igrs_owner` | 100 |
| `full_field_patta_transfer_new_owner_demo.csv` | `nisd_transfer_new_owner` | 620 |
| `full_field_patta_transfer_old_owner_demo.csv` | `nisd_transfer_old_owner` | 630 |
| `full_field_patta_transfer_return_owner_demo.csv` | `nisd_transfer_return_owner` | 46 |
| `full_field_patta_transfer_urban_demo.csv` | `nisd_transfer_urban_detail` | 229 |
| `sub_div_patta_transfer_application_information_urban_demo.csv` | `isd_transfer_application_info` | 41 |
| `sub_div_patta_transfer_urban_demo.csv` | `isd_transfer_urban_detail` | 50 |
| `uareg_demo.csv` | `urban_parcel_register` | 1033 |
| `uaregmap_ds_demo.csv` | `urban_parcel_signature` | 800 |
| `uchitta_natham_demo.csv` | `urban_natham_chitta_owner` | 551 |
| `uchitta_nathammap_ds_demo.csv` | `urban_natham_chitta_signature` | 400 |

Each table gets a `row_id BIGSERIAL PRIMARY KEY` on top of the CSV columns —
the extracts have no usable natural key. Indexes are created on
`application_id`, `survey_number`, `patta_number`, `district_code`,
`application_status` and `service_code` where present.

Code columns (`service_code`, `district_code`, `block_code`, statuses) stay
`VARCHAR` so leading zeros survive — `0154`, `0015` and `01` must not become
integers.

## The generated jurisdiction

One urban jurisdiction, matching the extracts: **Thoothukudi (district 28)**,
taluk `01`, town `001`, block `0015`, wards `002` / `102` / `103`, streets
`0001`–`0008`. Survey numbers run in a 13xx series in ward 002 and low series
in wards 102/103; each carries a patta number.

Application ids use the documented urban format
`YYYY / SERVICE_CODE / DISTRICT_CODE / SEQUENCE`, e.g. `2025/0154/28/000001`,
across 2022–2026. Service codes are weighted as in the extract: `0169`
(Govt to Private) and `0167` (TSLR Settlement) dominate, then `0153` (NISD),
`0154` (ISD), with a tail of `0158` / `0161` / `0178`.

Workflow rows walk the chain from `workflow_guide.txt` —
SIS (41) → SD (8) → DIS (12) → ZDT (59) → DRO (53) — with a field-visit date
set on the SIS hop for ISD applications only, and rejection remarks drawn from
the documented rejection reasons.

## Guarantees the verifier enforces

1. Every table's columns equal its source CSV header, in order.
2. No orphans: every `application_id` resolves to `urban_application_log`,
   every `patta_number` to `urban_parcel_register`.
3. NISD tables only carry `0153` applications; ISD and temp-subdivision tables
   only `0154`.
4. Zero identity values reused verbatim from the CSVs.
5. Zero person names reused from the CSVs — neither a whole name nor any
   single word of one. Substring overlap between ordinary Indian names
   (Ramesh / Ram) is allowed; exact reuse is not.
6. Every column in every table carries data -- no column is entirely NULL
   (535 of 535 populated).
7. The DSC columns hold base64 that decodes to real PKCS#7 SignedData, wrapped
   at 76 characters, and the certificate inside names the same officer as the
   row's username column.

## Names

Person names — owner, relative, applicant, mother, father — come from filtered
pools of everyday Indian names, checked against the CSVs so that no name and no
word of one is reused. Candidates like Perumal, Sekar, Manikandan, Saraswathi
and Padmavathi were rejected for exactly that reason.

**Roles, positions and place names are deliberately left alone.** Officer
usernames keep the `tut_<name>` convention, role ids (`41` SIS, `8` SD, `12`
DIS, `44` Tahsildar, `59` ZDT, `53` DRO), sub-registrar offices, courts,
treasury and district names all match the extracts, because those identify
offices rather than private individuals and the chatbot answers questions
about them.

## What the tests cover

Three layers, because they fail in different ways:

- **routing** (`test_intent_coverage.py`) — one question per intent. Catches a
  phrasing that lands on the wrong handler, which is how "what documents are
  required for an ISD application?" ended up listing ISD applications.
- **workflow rules** (`test_workflow_logic.py`) — the process invariants from
  `workflow_guide.txt` asserted against the data: one active application per
  survey, approved work sits at COMPLETED, field visits only on ISD, nothing
  dated in the future, sub-division areas summing to the survey area, stages
  never moving backwards. Plus cross-answer consistency: the workload total,
  the pending count and the overdue count must agree.
- **answers** (`test_questions.py`) — what the officer actually reads. Its
  assertions reject clarification prompts and require exact figures, because a
  check for shape alone will pass a confidently wrong answer.

## Dates

Applications that are still open (`pending` / `in_progress` / `escalated`) are
dated **relative to the day the seed runs**, so "overdue by N days" stays
believable however long after seeding the data is read. Ages are drawn against
the SLA in `workflow_guide.txt` — field visit within 15 working days, ISD
completing in 30–35 — so most open work sits inside the window, some has
slipped, and a few are badly late:

| Age when submitted | Share |
| --- | --- |
| 0–14 days | 34% |
| 15–35 days | 30% |
| 36–70 days | 24% |
| 71–150 days | 12% |

Closed applications (`approved` / `rejected`) keep the full 2022–2026 spread —
that historical depth is what makes the register look real. Nothing is dated in
the future: the workflow chain stops at today rather than walking past it, so a
recently submitted application simply has not reached the later desks yet.

## Statuses, roles and officers

`urban_application_log.application_status` is a code. Cross-tabulating it against
the wording the transfer extracts carry, and against how each workflow chain
actually ends, settles what it means:

| code | `workflow_state` | wording in the transfer extract | projected status |
| --- | --- | --- | --- |
| `01` | `C` (closed) | Approved By ZDT/HQDT (128), Order Generated (20) | `approved` |
| `02` | `C` | Rejected By ZDT/HQDT (33), Rejected (13) | `rejected` |
| `03` | `P` (open) | Send to SIS (4) → `pending`, Forward To ZDT (2) → `in_progress` | open |
| `05` | `C` | Rejected By ZDT/HQDT (2), Rejected (2) | `rejected` |

Checked a third way — against the verdict each chain closes on — the code agrees
on 176 of 180 closed applications and the wording on 173, so the code decides the
status and the wording only splits the open ones by desk. Nothing marks an
application `escalated`, so the database has none. The split is 150 approved,
52 rejected, 5 pending, 2 in progress.

The role ids are read off the data the same way. The applications whose wording
says "Send to SIS" sit at role 44 or role 41, and role 42 shares its actors with
44, so all three are the surveyor's office:

| role | who | stage |
| --- | --- | --- |
| `1` | the CSC / e-Sevai operator who submits | not a desk — the opening hop has no `from_stage` |
| `44`, `42`, `41` | the surveyor's office | `SIS` |
| `8` | Senior Draughtsman | `SD` |
| `16` | ZDT / HQDT — approves and generates the order | `TAHSILDAR` |

`workflow_history.performed_at` comes from `last_updated_datetime` rather than
`action_date`: a file often clears three desks in one day, and dating the hops to
the day alone loses their order and reads as the file moving backwards.

`transaction_status` on the transfer detail extracts follows the same shape:
`01` means the transfer went through, `02/NN` that it was refused with reason
code `NN` (`02/03`, `02/15`, `02/06`, ...), and an empty value that it has not
been decided. Only `01` and an empty value are not refusals — reading everything
that was not `01` as still pending had the chatbot reporting 60 refused transfers
as in flight.

Six applications disagree with their own transfer rows (an approved application
whose transfers were all refused, or the reverse). That is the two extracts
disagreeing at source, not a mapping fault, and it is left as it is.

**Officers** are the usernames that act at role 41. They are given the wards that
carry applications (`002`, `102`, `103`; the parcel register also covers `004`,
which has none), sharing wards out round-robin and holding more than one where
there are fewer officers than wards. Every application therefore ends up with an
officer whose jurisdiction covers it — otherwise the officer's own ward filter
hides their own files.

## Date-scoped questions

The extracts run **2022-03-12 to 2026-06-30**, and the seed dates open
applications relative to the day it ran, so "this month", "last month",
"today" and "in the last 30 days" correctly answer *nothing* — the register
simply stops in June. Ask about a month or a year that is in it and the counts
are real.

A question that names a period is a question about the register, not about the
officer's desk, so `get_officer_applications` drops its `current_stage` filter
whenever a date, month or year filter is present. Without that every past month
answered 0: an application from 2025 left the SIS desk long ago. Rejected
applications stay out of those lists unless they are asked for.

`test_date_queries.py` covers both halves — that "between June 1st and June
30th", "since 2026-01-01", "from January to March 2025", "last month" and the
rest resolve to the range the officer meant, and that the count that comes back
matches the register for that period, negated ranges included.

## Aadhaar, CAN and digital signatures

Both number formats live in `identifiers.py`, so the seed and the ORM
projection derive them the same way.

**Aadhaar** is a synthetic 12-digit number: leading digit 2-9 and a valid
Verhoeff check digit, so it passes the same format and checksum validation a
real one would. It is keyed on the person's name, which means one person
carries the same number in every extract they appear in, and the applicant of
an application who also holds a natham chitta patta matches their owner row.
The numbers belong to no one. `applicants.aadhaar_last4` and
`owners.aadhaar_last4` hold the last four digits -- all the ORM model stores.

**CAN** (Citizen Access Number) comes from the extracts, and its length says
which channel issued it:

| channel | issued by | digits | example |
| --- | --- | --- | --- |
| `CSC` | Common Service Centre / e-Sevai operator | 15 | `133280122203291` |
| `citizen` | the citizen on the TN portal | 12 | `202329380999` |

`urban_application_log.source_name` records the operator or VLE code that
filed it, or `-` when the citizen did, and in the extracts the two signals
agree on every well-formed row -- all 108 twelve-digit CANs carry `-` and all
151 fifteen-digit ones carry an operator code. The projection therefore takes
the channel from `source_name` and enforces the length against it. The CSV-
shaped tables keep the value verbatim; only the `applications` projection is
normalised, and only where the repair is unambiguous:

* a CSC number short of 15 digits with its `133` series code intact
  (`13328018014908`) is re-padded to `133280018014908`;
* a value carrying a prefix (`ESVU202407000005329`) keeps its digits;
* anything else -- a mobile number typed into the field, say -- is not a CAN
  and becomes NULL rather than a guess.

`verify_identifiers.py` re-checks all of this against the built database.

**The DSC columns hold genuine PKCS#7.** `dsc.py` generates one RSA-2048 key
and X.509 certificate per officer, then signs each row's identity
(`district|taluk|town|ward|block|survey|subdivision|patta`) into a real PKCS#7
SignedData structure, DER-encoded, base64ed and wrapped at 76 characters --
the same shape the extracts carry, and it round-trips through any PKCS#7
reader. `document_hash` is the SHA-256 of the signed payload.

The certificate subject follows the revenue-department shape
(`CN=<officer>, O=REVENUE DEPARTMENT, ST=Tamil Nadu`), but the **issuer is a
clearly-labelled sample CA** -- `SIS Sample Data Sub-CA (NOT A REAL CA)` --
never the name of an actual licensed certifying authority. These are valid
cryptographic objects for testing parsers and displays; they are not, and must
not be presented as, genuine credentials.
