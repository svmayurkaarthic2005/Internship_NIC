# case-core — Requirements

**Spec:** `case-core`
**Project:** SAKSHYA-Graph
**Phase:** 1 — Foundation layer
**Status:** Draft — awaiting review. Design not started.

---

## 1. Purpose

This phase establishes the four foundations every later phase writes into:

1. The **case** — its identity, its examiner, its incident window, and its typed indicators.
2. The **devices** seized under that case, each with a constrained power/access status.
3. **Storage isolation** — one provisioned, self-contained folder tree per case.
4. A **tamper-evident audit trail** that records every state-changing operation.

No evidence is parsed, scored, correlated, or graphed in this phase.

## 2. Conventions used in this document

Requirements are written in **EARS** (Easy Approach to Requirements Syntax). Each acceptance
criterion uses one of:

| Pattern | Shape |
|---|---|
| Ubiquitous | The `<system>` shall `<response>`. |
| Event-driven | When `<trigger>`, the `<system>` shall `<response>`. |
| State-driven | While `<state>`, the `<system>` shall `<response>`. |
| Unwanted behaviour | If `<condition>`, then the `<system>` shall `<response>`. |
| Optional feature | Where `<feature is present>`, the `<system>` shall `<response>`. |

Requirements are numbered `R<n>`; individual criteria are numbered `R<n>.<m>` and are stable
reference handles for the design document, the task list, and later phases.

`<system>` is one of: **the system**, **the API**, **the case service**, **the device service**,
**the audit service**, **the evidence service**, **the provisioner**, **the UI**.

## 3. Glossary

| Term | Meaning |
|---|---|
| **Case** | The top-level container for one investigation. Owns everything else. |
| **Examiner** | The single human operator. Identity is captured at case creation; there is no login. |
| **Indicator** | A typed known-value seed (mobile / email / UPI / domain / IP / keyword) supplied at case creation. |
| **Device** | A seized item registered against a case, from which artifacts will later be read. |
| **Case root** | The provisioned isolated folder tree for one case. |
| **Audit entry** | One append-only, hash-chained record of a state-changing operation. |
| **Chain** | The ordered sequence of audit entries for a case, linked by cryptographic digest. |
| **Evidence record** | A registered file with its SHA-256, source path, size, and registration metadata. |
| **Case clock** | Elapsed time since the case's recorded seizure-start timestamp. |
| **Forward-declared table** | A table created in this phase but populated by a later phase. |

---

## R1 — Case creation

**User story:** As an examiner, I want to open a case by recording who seized what, where, when,
and what I already know, so that every later artifact has a container and a provenance record.

- **R1.1** — When the examiner submits a case creation request, the case service shall create exactly one case record.
- **R1.2** — The case record shall store a case identifier, examiner (officer) name, examiner designation, seizure location, incident type, incident window start, incident window end, and seizure-start timestamp.
- **R1.3** — If any of the fields in R1.2 is absent or empty, then the case service shall reject the request, create no case, provision no folder, and return a field-level validation error naming each missing field.
- **R1.4** — The case identifier shall be unique across all cases.
- **R1.5** — If a case creation request supplies a case identifier that already exists, then the case service shall reject the request and create no case.
- **R1.6** — The incident type shall be constrained to one of exactly three supported incident templates.
- **R1.7** — If the submitted incident type is not one of the three supported templates, then the case service shall reject the request and create no case.
- **R1.8** — If the incident window end is earlier than the incident window start, then the case service shall reject the request and create no case.
- **R1.9** — The case record shall store a creation timestamp distinct from the seizure-start timestamp.
- **R1.10** — When a case is successfully created, the case service shall append an audit entry per **R7**.
- **R1.11** — The API shall expose a read operation returning a single case with all fields in R1.2, its case root path, and its indicator set.
- **R1.12** — The API shall expose a read operation returning all cases.

> **Open question OQ-1:** The three supported incident templates are not named in this pack. They are
> modelled as a constrained enumeration whose members are declared in configuration, not in the schema
> (see **R6.4**). Confirm the three names before design.

## R2 — Typed indicators

**User story:** As an examiner, I want the values I already know recorded under their correct type,
so that later phases can weight a UPI ID differently from a free-text keyword.

- **R2.1** — The case record shall hold a set of indicators, each carrying an indicator type and an indicator value.
- **R2.2** — The indicator type shall be constrained to one of: mobile number, email address, UPI identifier, domain, IP address, free-text keyword.
- **R2.3** — The system shall permit any number of indicators of each type on a single case, including zero.
- **R2.4** — The system shall permit a case with no indicators at all.
- **R2.5** — The system shall preserve the indicator type as a distinct stored attribute and shall not collapse indicators into a single untyped list.
- **R2.6** — If an indicator is submitted with a type outside the set in R2.2, then the case service shall reject the whole case creation request and create no case.
- **R2.7** — When indicators are read back, the system shall return each indicator with the type it was stored under.
- **R2.8** — The system shall retain the order in which indicators of the same type were supplied.
- **R2.9** — Where two indicators on the same case share both type and value, the system shall store them as a single indicator.

> **Open question OQ-2:** This pack does not state whether indicator values are normalised or
> format-validated at entry (e.g. `+91` prefixing for mobile numbers, lower-casing of domains and
> email addresses). Requirements above deliberately store values **as entered**, with R2.9 dedupe on
> the exact stored pair. If normalisation is wanted, it must be decided now — it changes the matching
> behaviour of every later phase. Design will flag this.

## R3 — Case provisioning and storage isolation

**User story:** As an examiner, I want each case to own a separate folder tree on local storage,
so that two cases can never contaminate one another's database or evidence.

- **R3.1** — When a case is created, the provisioner shall create an isolated case root folder on local storage.
- **R3.2** — The case root shall contain, at minimum, separate locations for: the case database, preserved evidence, generated reports, and logs.
- **R3.3** — When provisioning completes, the case service shall record the absolute case root path on the case record.
- **R3.4** — The system shall ensure that no two cases share a database file.
- **R3.5** — The system shall ensure that no two cases share an evidence folder.
- **R3.6** — Provisioning shall be atomic: when any step of case creation or provisioning fails, the system shall leave behind no case record and no partially created folder tree.
- **R3.7** — If the case root path already exists on disk, then the provisioner shall abort provisioning, create no case, and report the collision without modifying the existing folder.
- **R3.8** — If local storage is not writable at the configured root, then the provisioner shall abort, create no case, and report the failure with the path attempted.
- **R3.9** — The base directory under which case roots are created shall be read from configuration and shall not be hardcoded.
- **R3.10** — The system shall derive the case root folder name from the case identifier, sanitised so that it is a valid path segment on the host filesystem.
- **R3.11** — The system shall write each case's database inside that case's own root, and shall not write case data into a shared application-wide database.

> **Open question OQ-3:** A per-case database file (R3.4, R3.11) implies SQLite-per-case, while the
> forward-declared cross-phase tables in **R10** could alternatively live in one shared engine. This
> pack's isolation requirement is read as decisive: **one database per case**, with the forward-declared
> tables created inside each case database at provisioning time. Confirm.

## R4 — Device registration

**User story:** As an examiner, I want to register each seized device against the case with its
chain-of-custody metadata, so that later artifact reads are attributable to a specific device.

- **R4.1** — When the examiner submits a device registration request against an existing case, the device service shall create exactly one device record linked to that case.
- **R4.2** — The device record shall store a human-readable label, seizure timestamp, seizing examiner identity, source path or drive letter, and device power/access status.
- **R4.3** — The device record shall store a serial or identifying number where one is available, and shall permit that field to be absent.
- **R4.4** — The system shall permit any number of devices to be registered against one case.
- **R4.5** — If a device registration names a case that does not exist, then the device service shall reject the request and create no device record.
- **R4.6** — If the device label, seizure timestamp, seizing examiner identity, source path, or status is absent, then the device service shall reject the request and create no device record.
- **R4.7** — When a device is successfully registered, the device service shall append an audit entry per **R7**.
- **R4.8** — The API shall expose a read operation returning all devices registered against a given case, each with every field in R4.2 and R4.3.
- **R4.9** — The system shall not require the source path to be reachable or readable at registration time.

> **Open question OQ-4:** R4.9 assumes registration is a metadata act, not a mount check — a drive
> letter may be recorded before the device is attached. If Phase 2 expects the path to be validated at
> registration, say so now.

## R5 — Device status as a constrained, migration-free enumeration

**User story:** As a builder of Phase 3, I want device status to be a closed vocabulary I can map
volatility from, and I want to add a status later without a schema migration.

- **R5.1** — The device power/access status shall be constrained to one of: powered on, powered off, external media, exported data.
- **R5.2** — If a device is submitted with a status outside the set in R5.1, then the device service shall reject the request and create no device record.
- **R5.3** — The system shall store status as a stable machine-readable key, distinct from any human-readable display label.
- **R5.4** — The system shall permit a new status value to be added to the vocabulary without altering the database schema and without a data migration.
- **R5.5** — When a status value is added to the vocabulary, existing device records shall remain readable and their stored statuses unchanged.
- **R5.6** — The system shall expose the current status vocabulary as readable data, so that the UI and later phases enumerate it rather than hardcoding it.
- **R5.7** — If a device record is read whose stored status key is no longer in the vocabulary, then the system shall return the raw stored key and shall not fail the read.

## R6 — Case clock

**User story:** As a builder of Phase 3, I want elapsed time to come from the case record, so that
volatility decay is computed from seizure-start and never from an ad-hoc call to the system clock
buried in business logic.

- **R6.1** — The case record shall store a seizure-start timestamp supplied at case creation.
- **R6.2** — The system shall expose elapsed time since the case's seizure-start timestamp as a derived value on the case.
- **R6.3** — The system shall compute elapsed time from the stored seizure-start timestamp, and business logic shall obtain elapsed time from the case record rather than reading the system clock directly.
- **R6.4** — The API shall return elapsed time whenever a case is read.
- **R6.5** — The system shall express elapsed time in a single documented unit.
- **R6.6** — If the seizure-start timestamp is in the future relative to the current time, then the system shall return a non-negative elapsed time of zero and record that the case clock has not yet started.
- **R6.7** — The seizure-start timestamp shall be settable at case creation and shall not require the case to be created at the moment of seizure.

> **Open question OQ-5:** R6.5 assumes elapsed time is exposed in **seconds** (integer), with any
> human formatting done in the UI. Phase 3's decay function should confirm the unit it wants.

## R7 — Append-only, hash-chained audit trail

**User story:** As an examiner presenting findings, I want every state-changing action recorded in a
chain that cannot be silently edited, so that the integrity of the record is demonstrable.

- **R7.1** — When any state-changing operation completes, the audit service shall append exactly one audit entry.
- **R7.2** — Each audit entry shall record: acting examiner identity, action type, target entity type, target entity identifier, timestamp, and a summary of what changed.
- **R7.3** — Each audit entry shall record a cryptographic digest computed over its own content together with the digest of the immediately preceding entry.
- **R7.4** — The first audit entry in a case's chain shall use a fixed, documented genesis value in place of a predecessor digest.
- **R7.5** — Audit entries shall carry a strictly increasing sequence position within their case.
- **R7.6** — The audit trail shall be append-only: the system shall expose no operation that updates or deletes an audit entry.
- **R7.7** — The design shall make append-only structurally true rather than conventional — the persistence layer shall reject UPDATE and DELETE against the audit table, so that append-only holds even for a writer that bypasses the service layer.
- **R7.8** — When an audited operation fails, the audit service shall append no entry for it.
- **R7.9** — The audit entry and the state change it describes shall be committed together, so that no state change exists without its audit entry and no audit entry exists without its state change.
- **R7.10** — Case creation, device registration, and evidence registration shall each produce an audit entry.
- **R7.11** — The API shall expose a read operation returning a case's audit entries in sequence order.
- **R7.12** — Every audit entry shall record the examiner identity captured on its case, per **R12**.
- **R7.13** — The digest algorithm and the exact serialisation of entry content fed into it shall be documented and deterministic, so that a third party can recompute the chain independently.

> **Open question OQ-6:** "Every state-changing operation" is read as covering **writes to case,
> device, and evidence records** in this phase. Reads, listings, exports, and UI navigation are **not**
> audited. Confirm — if viewing evidence must be audited for court purposes, that changes the design.

## R8 — Audit verification

**User story:** As an examiner, I want a verification pass that tells me whether the trail is intact
and, if not, exactly where it broke.

- **R8.1** — The API shall expose a verification operation that walks a case's audit trail in sequence order.
- **R8.2** — When verification finds every entry's recomputed digest equal to its stored digest and every entry's recorded predecessor digest equal to the preceding entry's stored digest, the audit service shall report the chain as intact.
- **R8.3** — If verification finds an entry whose recomputed digest differs from its stored digest, then the audit service shall report the chain as broken and shall name that entry by its sequence position and identifier.
- **R8.4** — If verification finds an entry whose recorded predecessor digest does not match the preceding entry's stored digest, then the audit service shall report the chain as broken and shall name that entry by its sequence position and identifier.
- **R8.5** — If verification finds a gap in the sequence positions, then the audit service shall report the chain as broken and shall name the position at which the gap begins.
- **R8.6** — Verification shall report the first breaking entry, and shall report the total number of entries walked.
- **R8.7** — Verification shall be read-only and shall not modify, repair, or append to the trail.
- **R8.8** — When verification runs on a case with no audit entries, the audit service shall report the chain as intact with zero entries walked.
- **R8.9** — The UI shall present the verification result, showing intact/broken status and the identified breaking entry when broken.

## R9 — Evidence registration and hashing

**User story:** As an examiner, I want every file I register as evidence fingerprinted at the moment
of registration, so that its integrity can be re-checked independently at any later time.

- **R9.1** — When the examiner registers a file as evidence, the evidence service shall compute a SHA-256 digest of that file's contents.
- **R9.2** — The evidence record shall store the SHA-256 digest, the source path, the file size in bytes, the registering action, and the registration timestamp.
- **R9.3** — The evidence service shall read the file incrementally in bounded chunks, so that files larger than available memory are hashed without being loaded wholly into memory.
- **R9.4** — The computed digest shall equal the digest produced by any standard SHA-256 implementation over the same file.
- **R9.5** — If the file at the source path does not exist or is not readable, then the evidence service shall register no evidence record and shall report the failure with the path attempted.
- **R9.6** — When evidence registration succeeds, the evidence service shall append an audit entry per **R7**, whose target identifies the evidence record.
- **R9.7** — Each evidence record shall be linked to the case under which it was registered.
- **R9.8** — The API shall expose a read operation returning the evidence records registered under a case.
- **R9.9** — The evidence service shall record the digest as a lowercase hexadecimal string of fixed length.

> **Open question OQ-7:** This pack says evidence is *registered* with a hash; it does not say whether
> the file is also **copied** into the case root's preserved-evidence folder at registration. The
> requirements above hash **in place** and record the source path only. If preservation-by-copy is
> expected in Phase 1, it must be added now — it changes R3.2's storage sizing and R9's timing.
> **OQ-8:** Whether a device may be named as the evidence record's origin (device linkage) is also
> unstated; R9.7 links to case only.

## R10 — Forward-declared schema

**User story:** As the builder of Phases 2–5, I want the tables I will populate to already exist with
the right shape, so that no migration is needed mid-build.

- **R10.1** — Provisioning shall create the following tables, empty: artifacts, events, graph nodes, graph edges, candidate actions, action scores, investigator feedback, and evidence packages.
- **R10.2** — The forward-declared tables shall be created in the same case database as the case, device, indicator, audit, and evidence tables.
- **R10.3** — Each forward-declared table shall carry a foreign key or equivalent linkage to the case it belongs to.
- **R10.4** — The system shall function correctly with every forward-declared table empty, and no read path in this phase shall fail because a forward-declared table has no rows.
- **R10.5** — All timestamp columns on forward-declared tables shall follow the timezone convention in **R11**.
- **R10.6** — The design document shall flag every forward-declared table whose shape was inferred rather than specified, stating what was assumed, so that the shape can be confirmed before Phase 2 rather than discovered in Phase 4.
- **R10.7** — No table created in this phase shall require an altering migration in Phases 2–5 for the fields described in this pack.

> **Open question OQ-9:** All eight forward-declared tables will be shaped from the later-phase
> descriptions in this pack, which describe purpose more than columns. **Every one of them is expected
> to carry inferred columns** and will be flagged under R10.6. The ones with the least specification —
> and therefore the highest risk of a Phase 4 surprise — are **action scores** (what score components,
> what weighting fields), **investigator feedback** (what feedback verbs, what it attaches to), and
> **evidence packages** (what constitutes a package and what it references). Expect concrete proposals
> in the design document for confirmation.

## R11 — Timestamp convention

**User story:** As the builder of the timeline and the decay calculation, I need one unambiguous
timestamp representation, because mixed naive and aware timestamps will silently corrupt both.

- **R11.1** — The system shall store every timestamp as a timezone-aware value in UTC.
- **R11.2** — The system shall not store, accept, or produce naive timestamps anywhere in the persistence layer or the API.
- **R11.3** — If a request supplies a timestamp without timezone information, then the system shall reject that request with a validation error rather than assume a timezone.
- **R11.4** — When a timestamp is supplied with a non-UTC offset, the system shall convert it to UTC before storing it.
- **R11.5** — The API shall serialise every timestamp in ISO 8601 with an explicit UTC designator.
- **R11.6** — The system shall obtain the current time as a timezone-aware UTC value, and shall not use naive "now" calls.
- **R11.7** — The design document shall state the timezone convention explicitly, including how the UI is expected to display local time without altering stored values.
- **R11.8** — Where the UI displays a timestamp to the examiner, it shall render local time while the stored and transmitted value remains UTC.

## R12 — Examiner identity without authentication

**User story:** As a single-operator examiner, I do not want a login system, but I do want my identity
on every recorded action.

- **R12.1** — The system shall provide no authentication system, no login, no user accounts, and no session credentials.
- **R12.2** — The system shall capture one examiner identity — name and designation — at case creation.
- **R12.3** — When any audited operation occurs, the system shall record the examiner identity captured on that operation's case.
- **R12.4** — The system shall treat the case's examiner identity as the acting identity for that case's audit entries and shall not require it to be re-supplied per request.
- **R12.5** — The device record's seizing-examiner field shall be recorded independently, so that a device may record a seizing examiner different from the case examiner.

## R13 — Local-only network posture

**User story:** As an examiner working on an isolated forensic workstation, I need the service to bind
locally and to work with networking switched off.

- **R13.1** — The API shall bind to the loopback interface only.
- **R13.2** — If a connection is attempted from a non-loopback address, then the system shall refuse it.
- **R13.3** — While the host network interface is disabled, the system shall start successfully and all functionality in this spec shall remain available.
- **R13.4** — The system shall make no outbound network request during startup or during any operation in this spec.
- **R13.5** — The system shall depend on no remote service, no external API, and no content delivery network at runtime.
- **R13.6** — Where the UI loads assets, it shall load them from local storage only.
- **R13.7** — The bind address and port shall be read from configuration, and the default bind address shall be loopback.

## R14 — User interface scope

**User story:** As an examiner, I want exactly enough UI to run this phase and nothing more.

- **R14.1** — The UI shall provide a case creation form covering all fields in R1.2 and the typed indicator set in R2.
- **R14.2** — The case creation form shall allow adding any number of indicators, each with an explicit type chosen from R2.2.
- **R14.3** — The UI shall provide a device registration form covering all fields in R4.2 and R4.3, with status chosen from the vocabulary exposed per R5.6.
- **R14.4** — The UI shall provide a device list showing each registered device with its metadata and its status.
- **R14.5** — The UI shall provide an audit log viewer listing entries in sequence order with the fields in R7.2, and shall surface chain verification per R8.9.
- **R14.6** — The UI shall provide an empty placeholder for the triage queue, which performs no work in this phase.
- **R14.7** — The UI shall provide no screen beyond those in R14.1 through R14.6.
- **R14.8** — When a request is rejected, the UI shall present the validation or failure message returned by the API.

## R15 — Phase boundary

**User story:** As the person sequencing this build, I want Phase 1 to stop where it is supposed to stop.

- **R15.1** — The system shall not parse, decode, or interpret the contents of any registered file beyond computing its hash and size.
- **R15.2** — The system shall not compute any score, ranking, priority, or recommendation.
- **R15.3** — The system shall not construct, populate, or traverse any graph.
- **R15.4** — The system shall not perform machine learning, inference, or model loading of any kind.
- **R15.5** — The system shall leave every forward-declared table in **R10.1** empty at the end of this phase.
- **R15.6** — The system shall not derive volatility, decay, or elapsed-time-weighted values in this phase, while still exposing elapsed time per **R6**.

---

## 4. Acceptance criteria — examiner-verifiable

These restate the pack's acceptance criteria and bind each to the requirements it exercises.

| # | Criterion | Verifies |
|---|---|---|
| **AC-1** | I create a case with a full indicator set — at least one of each of the six indicator types — and an isolated case folder appears containing the database, evidence, reports, and logs locations. | R1, R2, R3 |
| **AC-2** | I register three devices with three different statuses and see all three listed with their full metadata and their distinct statuses. | R4, R5, R14.4 |
| **AC-3** | The audit log shows every action I performed, in the order I performed them, and verification reports the chain intact. | R7, R8.2, R14.5 |
| **AC-4** | I deliberately alter one audit entry in the database, re-run verification, and it fails and names the altered entry by position and identifier. | R8.3, R8.4, R8.6 |
| **AC-5** | I register a file as evidence and its recorded SHA-256 matches the digest produced by an independent tool over the same file. | R9.1, R9.4, R9.9 |
| **AC-6** | I disable the host network interface, start the service, and complete AC-1 through AC-5 without error. | R13.3, R13.4 |
| **AC-7** | I create two cases and confirm they share neither a database file nor an evidence folder. | R3.4, R3.5 |
| **AC-8** | I force a failure partway through provisioning and confirm no case record and no partial folder tree remain. | R3.6 |
| **AC-9** | I hash a file larger than available RAM and the operation completes without exhausting memory. | R9.3 |

---

## 5. Assumptions inherited from repository guidance

No SAKSHYA-Graph steering files were found in this repository. In their absence, the following
conventions from the repo's existing `CLAUDE.md` and `SKILL.md` have been carried into the
requirements above, and should be confirmed or overridden:

| Inherited convention | Where it lands |
|---|---|
| Timezone-aware UTC everywhere; `datetime.utcnow()` is banned | R11.1, R11.6 |
| Configuration via a settings object; never hardcode paths, URLs, or secrets | R3.9, R13.7 |
| Structured logging via a logger, never `print()` in service code | Design-level; not a requirement here |
| Async SQLAlchemy `select()` + `await execute()`, never `session.query()` | Design-level; not a requirement here |
| A single standard API response envelope for success and error | R1.3, R14.8 |
| FastAPI service layer with routers per resource | R13, R14 |

---

## 6. Open questions for review

Answer these before design; each changes structure, not just wording.

| ID | Question | Blocks |
|---|---|---|
| **OQ-1** | What are the three supported incident templates, by name? | R1.6 |
| **OQ-2** | Are indicator values normalised/format-validated at entry, or stored verbatim? | R2.9 |
| **OQ-3** | One database per case (assumed), or one shared database with case-scoped rows? | R3.4, R3.11, R10.2 |
| **OQ-4** | Must a device's source path be reachable at registration time? | R4.9 |
| **OQ-5** | What unit should elapsed time be exposed in — seconds assumed? | R6.5 |
| **OQ-6** | Are read/view operations audited, or only writes? | R7.1 |
| **OQ-7** | Is registered evidence copied into the case's preserved-evidence folder, or hashed in place? | R9.2, R3.2 |
| **OQ-8** | Should an evidence record link to the device it came from, not just the case? | R9.7 |
| **OQ-9** | Confirm inferred shapes for the eight forward-declared tables — especially action scores, investigator feedback, and evidence packages. | R10.1, R10.6 |

---

**End of requirements. Stopping here for review — design has not been started.**
