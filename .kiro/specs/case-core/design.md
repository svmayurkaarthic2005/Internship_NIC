# case-core — Design

**Spec:** `case-core`
**Project:** SAKSHYA-Graph
**Phase:** 1 — Foundation layer
**Status:** Draft — awaiting review. Task breakdown not started.
**Requirements:** `.kiro/specs/case-core/requirements.md` (R1–R15)

---

## 0. Decisions taken under unanswered open questions

The requirements document raised nine open questions. Design cannot proceed without settling
them, so each is resolved below with the reasoning. **Overturning any of the three marked
STRUCTURAL changes this document materially**; the other six are contained.

| OQ | Resolution taken | Impact if wrong |
|---|---|---|
| **OQ-1** incident templates | Modelled as a config-declared vocabulary of three keys, named `TEMPLATE_A/B/C` as placeholders (§7.2). | Contained — rename in config. |
| **OQ-2** indicator normalisation | Stored **verbatim**; a `normalized_value` column is populated by a pure function per type, used only for dedupe (§6.3). | Contained — the column exists either way. |
| **OQ-3** database topology | **STRUCTURAL.** One SQLite database per case. No shared application database at all; the case list is built by scanning the case-root base directory (§4, §5). | Rewrites §4, §5, §8. |
| **OQ-4** source path reachability | Not validated at registration. Recorded as text (§7.3). | Contained — add a validator. |
| **OQ-5** elapsed time unit | Integer **seconds**, exposed as `elapsed_seconds` (§9). | Contained. |
| **OQ-6** audit scope | Writes only. Reads, listings and UI navigation are not audited (§10.2). | Moderate — adds an audit call site per read path. |
| **OQ-7** evidence preservation | **STRUCTURAL.** Hashed **in place**; source path recorded, file not copied. The `evidence/` folder is provisioned but stays empty in Phase 1 (§12). | Adds a copy step, a second hash, and storage sizing. |
| **OQ-8** evidence→device link | A nullable `device_id` is included now so the link exists if wanted (§12.2). | Contained. |
| **OQ-9** forward-declared shapes | **STRUCTURAL.** All eight tables shaped from inference. Flagged individually in §13. | Rewrites §13 only — but that is the whole point of doing it now. |

---

## 1. Design goals

Ranked, because they conflict:

1. **Evidentiary integrity first.** A tamper-evident trail that a third party can independently
   recompute beats convenience everywhere it competes with it.
2. **Isolation is physical, not logical.** Two cases must be incapable of contaminating each other
   even under operator error, so separation is at the filesystem level, not the `WHERE` clause.
3. **No mid-build migrations.** Every table Phases 2–5 need exists at the end of Phase 1.
4. **Works with the network cable pulled.** No runtime dependency the workstation cannot satisfy offline.
5. **Small surface.** This phase ships five screens and refuses to grow a sixth.

## 2. Technology choices

| Concern | Choice | Why |
|---|---|---|
| API | FastAPI | Matches repo convention; loopback bind is trivial; OpenAPI for free. |
| ORM | SQLAlchemy 2.x async + `aiosqlite` | Async style matches repo guidance (`select()` + `await execute()`, never `session.query()`). |
| Database | SQLite, one file per case | Satisfies R3.4/R3.11 physically. Single-operator workload; no server process to run offline. |
| Config | `pydantic-settings` | Repo convention: settings object, nothing hardcoded (R3.9, R13.7). |
| Logging | `structlog` via `get_logger(__name__)` | Repo convention: no `print()` in service code. |
| Hashing | `hashlib.sha256`, chunked | Stdlib; no dependency. |
| Frontend | Vanilla HTML/CSS/JS, no npm | R13.5/R13.6 — no CDN, no build step, works offline. |

**Rejected:** PostgreSQL (needs a server; conflicts with per-case file isolation), Alembic (nothing
to migrate if R10 holds), any JS framework (would pull a CDN or a build chain).

## 3. Layering

```
frontend/                      static, loopback-served, no build step
   |
   v  fetch()
backend/routers/               HTTP shape only. No business logic.
   cases.py  devices.py  audit.py  evidence.py
   |
   v
backend/services/              all business logic; owns transactions
   case_service.py             create/read cases
   provisioning.py             atomic case-root creation
   device_service.py           register/list devices
   audit_service.py            append + verify chain
   evidence_service.py         register + hash
   clock.py                    the ONLY source of "now"
   |
   v
backend/db/                    engines, sessions, models
   registry.py                 case-root discovery (no shared DB)
   session.py                  per-case engine cache
   models.py                   ORM models incl. forward-declared
   types.py                    UtcDateTime TypeDecorator
```

**Rule:** routers never touch a session directly; services never construct HTTP responses. The
audit append happens *inside* the service transaction, never in the router (R7.9).

## 4. Storage layout

```
<CASE_BASE_DIR>/                        from settings; default ./cases
├── <sanitised-case-id>/                the case root  (R3.1, R3.10)
│   ├── case.sqlite3                    THIS case's database only (R3.4, R3.11)
│   ├── evidence/                       preserved evidence (empty in Phase 1 — OQ-7)
│   ├── reports/                        generated reports (empty in Phase 1)
│   ├── logs/                           per-case log output
│   └── case.json                       redundant human-readable descriptor
└── .staging/                           transient; provisioning workspace only
```

`case.json` duplicates the case identifier, root path and creation time. It is **not** authoritative
— the database is — but it makes a case root self-describing if the folder is moved, and it is what
the registry scan reads (§5), so listing cases does not require opening every database.

### 4.1 Path sanitisation (R3.10)

Case identifiers are examiner-supplied and may contain `/`, `\`, `:` or trailing dots. The
provisioner maps the identifier to a folder name by: NFKC-normalising, replacing every character
outside `[A-Za-z0-9._-]` with `_`, collapsing runs of `_`, stripping leading/trailing `._`, rejecting
reserved Windows device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`), and
truncating to 96 characters. If two distinct identifiers sanitise to the same folder name, the second
collides at R3.7 and is rejected — the identifier remains stored verbatim in the database.

## 5. Case discovery without a shared database (R1.12, R3.11)

R3.11 forbids a shared application database. Listing all cases therefore cannot come from a central
table. The registry is a **scan**:

- `registry.list_cases()` enumerates immediate subdirectories of `CASE_BASE_DIR`, skips `.staging`
  and any directory without a readable `case.json`, and returns the descriptors.
- Results are cached in memory with the base directory's mtime as the validity key; a create
  invalidates the cache.
- A directory that exists but has an unreadable or malformed `case.json` is reported as a
  `degraded` entry rather than omitted silently — a case that has gone unreadable is exactly the
  thing an examiner must be told about.

**Known limit:** this is O(number of cases) on the filesystem, fine for the hundreds this tool
targets and wrong for tens of thousands. Documented rather than engineered around.

## 6. Data model — Phase 1 tables

All tables live in the per-case `case.sqlite3`. All primary keys are UUIDv4 stored as 36-char TEXT.
All timestamps use the `UtcDateTime` type from §11.

### 6.1 `cases`

Exactly one row per database. The row's existence *is* the case.

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID |
| `case_identifier` | TEXT UNIQUE NOT NULL | examiner-supplied, verbatim (R1.4) |
| `examiner_name` | TEXT NOT NULL | R12.2 |
| `examiner_designation` | TEXT NOT NULL | R12.2 |
| `seizure_location` | TEXT NOT NULL | |
| `incident_type` | TEXT NOT NULL | vocabulary key (R1.6, §7.2) |
| `incident_window_start` | UtcDateTime NOT NULL | |
| `incident_window_end` | UtcDateTime NOT NULL | `>= start` (R1.8) |
| `seizure_started_at` | UtcDateTime NOT NULL | the case clock origin (R6.1) |
| `case_root_path` | TEXT NOT NULL | absolute (R3.3) |
| `created_at` | UtcDateTime NOT NULL | distinct from seizure start (R1.9) |

Uniqueness of `case_identifier` across *all* cases (R1.4/R1.5) cannot be a table constraint under
per-case databases. It is enforced by the provisioner: the sanitised folder name is derived from the
identifier, and `os.rename` onto an existing path fails (§8), so the filesystem is the uniqueness
authority. Two identifiers that differ only in characters that sanitise away are treated as a
collision — deliberately conservative.

### 6.2 `indicators` (R2)

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | |
| `case_id` | TEXT FK→cases.id NOT NULL | |
| `indicator_type` | TEXT NOT NULL | vocabulary key (R2.2) |
| `value` | TEXT NOT NULL | stored verbatim (OQ-2) |
| `normalized_value` | TEXT NOT NULL | dedupe key only |
| `position` | INTEGER NOT NULL | preserves entry order (R2.8) |

`UNIQUE (case_id, indicator_type, normalized_value)` implements R2.9. Type is a first-class column,
never folded into the value — R2.5 is the whole reason indicators are not a JSON blob on `cases`.

Normalisation per type is a pure function, used **only** for the dedupe key: mobile → strip
non-digits, keep last 10; email/domain → casefold; IP → parsed and re-serialised canonical form;
UPI → casefold; keyword → casefold + collapse whitespace. An unparseable value falls back to
casefold of the raw string rather than rejecting (R2 has no format-validation requirement).

### 6.3 `devices` (R4, R5)

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | |
| `case_id` | TEXT FK NOT NULL | |
| `label` | TEXT NOT NULL | |
| `serial_number` | TEXT NULL | optional (R4.3) |
| `seized_at` | UtcDateTime NOT NULL | |
| `seized_by_examiner` | TEXT NOT NULL | may differ from case examiner (R12.5) |
| `source_path` | TEXT NOT NULL | not validated (OQ-4, R4.9) |
| `status` | TEXT NOT NULL | vocabulary key — see §7.1 |
| `registered_at` | UtcDateTime NOT NULL | |

### 6.4 `audit_entries` (R7) — see §10

### 6.5 `evidence_records` (R9) — see §12

## 7. Vocabularies without migrations (R5.4, R5.6, R5.7)

### 7.1 The mechanism

Device status is stored as **plain TEXT holding a stable machine key**. There is deliberately:

- **no** SQL `ENUM` type,
- **no** `CHECK (status IN (...))` constraint,
- **no** foreign key to a status lookup table.

Any of those three would make adding a status a schema change, violating R5.4. Validation lives in
the service layer against a Python-side registry:

```python
# backend/vocab.py
DEVICE_STATUSES = Vocabulary("device_status", [
    Term("powered_on",     "Powered on"),
    Term("powered_off",    "Powered off"),
    Term("external_media", "External media"),
    Term("exported_data",  "Exported data"),
])
```

- Writes validate against `DEVICE_STATUSES` and reject unknown keys (R5.2).
- Reads pass the stored key through untouched; if the key is no longer in the vocabulary the read
  returns it with `display_label = key` and `known = false` rather than raising (R5.7).
- `GET /api/v1/vocabularies` exposes every vocabulary so the UI populates dropdowns from the server
  and never hardcodes the list (R5.6, R14.3).

Adding a status in a later phase is a one-line edit to `vocab.py`. No schema touched, no rows
rewritten (R5.5). The same mechanism serves incident types (§7.2) and indicator types.

**Cost accepted:** the database no longer defends its own domain. A direct `sqlite3` write can insert
a nonsense status. That is tolerated because R5.4 makes it unavoidable, and because R5.7 requires
reads to survive exactly that situation anyway.

### 7.2 Incident templates (OQ-1)

Same mechanism, three placeholder terms in `vocab.py`. Renaming them once you confirm OQ-1 touches
one file and no stored data, provided it happens before the first real case is created.

## 8. Atomic provisioning (R3.6, R3.7, R3.8)

Partial case roots are the failure mode this design most wants to prevent, so provisioning never
builds in place:

1. **Pre-check.** Compute the final path. If it exists → abort with a collision error, touching
   nothing (R3.7).
2. **Stage.** Create `<base>/.staging/<uuid4>/` and build the *entire* case inside it: the four
   subfolders, `case.sqlite3` with every table (Phase 1 **and** forward-declared, §13), the `cases`
   row, the indicator rows, the genesis audit entry, and `case.json`.
3. **Fsync.** Flush and close the SQLite connection; fsync the staging directory so the rename cannot
   publish a half-written database.
4. **Publish.** `os.rename(staging, final)` — atomic within one filesystem, and fails rather than
   overwrites if the destination appeared in the meantime.
5. **Clean up on any failure.** `shutil.rmtree(staging, ignore_errors=True)` in a `finally`. Because
   the case only ever becomes visible at step 4, a failure at steps 1–3 leaves **no** case record and
   **no** partial tree (R3.6).

Orphaned staging directories are swept at startup — a power loss mid-provision leaves garbage under
`.staging/`, never a visible half-case.

`CASE_BASE_DIR` writability is probed at startup and again at step 2; failure reports the attempted
path (R3.8). Because staging and final share a parent, the rename never crosses a filesystem
boundary — the one condition that would make it non-atomic.

## 9. The case clock (R6)

```python
# backend/services/clock.py
def now() -> datetime:                      # the ONLY "now" in the codebase
    return datetime.now(timezone.utc)

def elapsed_seconds(case: Case) -> int:
    delta = (now() - case.seizure_started_at).total_seconds()
    return max(0, int(delta))               # R6.6
```

`elapsed_seconds` is attached to every serialised case (R6.4) and is the value Phase 3 will feed its
decay function. R6.3's intent — that business logic must not reach for the wall clock — is enforced
by convention plus a lint rule: `datetime.now(`, `datetime.utcnow(`, `time.time(` are banned outside
`clock.py`, checked in CI. `datetime.utcnow()` is banned outright, everywhere (repo convention, R11.6).

Negative elapsed time is clamped to zero and reported with `clock_started = false` so a
future-dated seizure cannot produce a negative decay input downstream (R6.6).

## 10. Audit trail (R7)

### 10.1 Table

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID |
| `case_id` | TEXT NOT NULL | |
| `sequence` | INTEGER NOT NULL UNIQUE | starts at 1, strictly increasing (R7.5) |
| `examiner_name` | TEXT NOT NULL | R7.12 |
| `examiner_designation` | TEXT NOT NULL | |
| `action_type` | TEXT NOT NULL | vocabulary key |
| `target_entity_type` | TEXT NOT NULL | `case` / `device` / `evidence` |
| `target_entity_id` | TEXT NOT NULL | |
| `occurred_at` | UtcDateTime NOT NULL | |
| `summary` | TEXT NOT NULL | human-readable change description |
| `details_json` | TEXT NOT NULL | canonical JSON of changed fields |
| `prev_hash` | TEXT NOT NULL | 64 hex chars |
| `entry_hash` | TEXT NOT NULL | 64 hex chars |

### 10.2 What is audited (OQ-6)

Case creation, device registration, evidence registration. Writes only. Reads, listings, verification
runs and UI navigation append nothing — verification in particular must be side-effect-free (R8.7).

### 10.3 Chain construction (R7.3, R7.4, R7.13)

```
canonical = json.dumps(
    {"sequence", "examiner_name", "examiner_designation", "action_type",
     "target_entity_type", "target_entity_id",
     "occurred_at",            # ISO-8601 UTC, microsecond precision, "Z" suffix
     "summary", "details_json", "prev_hash"},
    sort_keys=True, separators=(",", ":"), ensure_ascii=False,
)
entry_hash = sha256(canonical.encode("utf-8")).hexdigest()
```

`id` is deliberately **excluded** from the digest — it is a storage handle, not evidence. `sequence`
is included, so reordering entries breaks the chain. Genesis `prev_hash` is 64 `0` characters
(R7.4). The serialisation is fully specified above precisely so a third party can recompute it
without reading our source (R7.13); it will be restated in the operator documentation.

### 10.4 Append-only, structurally (R7.6, R7.7)

Three layers, because a convention is not a structure:

1. **No API.** There is no update or delete route, and no service function that emits `UPDATE` or
   `DELETE` against `audit_entries` (R7.6).
2. **Database triggers** — the structural layer required by R7.7:

```sql
CREATE TRIGGER audit_entries_no_update
BEFORE UPDATE ON audit_entries
BEGIN SELECT RAISE(ABORT, 'audit_entries is append-only'); END;

CREATE TRIGGER audit_entries_no_delete
BEFORE DELETE ON audit_entries
BEGIN SELECT RAISE(ABORT, 'audit_entries is append-only'); END;
```

   These fire for **any** writer, including a direct `sqlite3` session, not just our service layer.
3. **The hash chain** as the detection layer for anything that defeats layers 1 and 2.

**Stated honestly:** an operator with filesystem access can `DROP TRIGGER` and rewrite rows. No
local-storage design can prevent that. What it *cannot* do is rewrite them undetectably — that is
what §11's chain and R8's verification are for, and it is why AC-4 tests exactly this attack.

### 10.5 Transactional coupling (R7.8, R7.9)

The audit append shares the state change's transaction:

```python
async with session.begin():          # one transaction
    session.add(device)
    await audit_service.append(session, ...)   # same session, no commit
# commit here — both or neither
```

A failed operation rolls back and appends nothing (R7.8). No state change can exist without its audit
entry, and no entry without its change (R7.9). Sequence allocation reads `MAX(sequence)` inside the
same transaction; SQLite's write serialisation makes that safe for this single-writer workload.

## 11. Verification (R8)

`verify_chain(case_id)` walks entries ordered by `sequence` and reports the **first** break:

| Check | Failure reported |
|---|---|
| `sequence` contiguous from 1 | `gap` at the position where the run breaks (R8.5) |
| `recompute(entry) == entry.entry_hash` | `content_mismatch` naming that entry (R8.3) |
| `entry.prev_hash == previous.entry_hash` | `link_mismatch` naming that entry (R8.4) |
| entry 1 has genesis `prev_hash` | `bad_genesis` |

Result shape:

```json
{"intact": false, "entries_walked": 7,
 "broken_at": {"sequence": 4, "entry_id": "…", "reason": "content_mismatch"}}
```

Empty trail → `{"intact": true, "entries_walked": 0}` (R8.8). Read-only throughout (R8.7); it opens
the session in read mode and holds no write transaction. Zero entries walked is reported alongside
the verdict per R8.6.

## 12. Timestamps (R11)

SQLite has no native timezone-aware type; SQLAlchemy's `DateTime` round-trips through it as **naive**,
which is precisely the corruption R11.2 exists to prevent. So timestamps do not use `DateTime`:

```python
class UtcDateTime(TypeDecorator):
    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None: return None
        if value.tzinfo is None:
            raise ValueError("naive datetime rejected")     # R11.2
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def process_result_value(self, value, dialect):
        if value is None: return None
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
```

- **Stored as** fixed-width ISO-8601 UTC text with microseconds and a literal `Z` (R11.1).
- Fixed width means lexicographic order **is** chronological order, so `ORDER BY` and range filters
  work without parsing — important for Phase 3's timeline.
- A naive datetime raises at the persistence boundary, so R11.2 cannot be violated by forgetting.
- Pydantic request models reject offset-less input at the API boundary (R11.3) and convert non-UTC
  offsets to UTC before storage (R11.4).
- Responses serialise the same format (R11.5).

**Convention, stated for the record (R11.7):** *every* timestamp stored, transmitted, compared or
hashed by SAKSHYA-Graph is timezone-aware UTC. Local time exists **only** in the browser: the UI
renders with `toLocaleString()` at display time and never sends local time back (R11.8). Nothing in
the backend is ever aware of the workstation's timezone.

## 13. Forward-declared schema (R10) — **every table below is inferred**

R10.6 requires flagging what was guessed. The honest answer is **all eight**: this pack describes
these tables' purposes, not their columns. Confidence is graded so review effort goes where it pays.

Common to all eight: `id` TEXT PK, `case_id` TEXT NOT NULL, `created_at` UtcDateTime NOT NULL
(R10.3, R10.5). All are created empty at provisioning (R10.1, R10.2, R15.5) and no Phase 1 read path
touches them (R10.4).

### 13.1 `artifacts` — confidence: **medium**
`device_id` FK · `artifact_type` (vocab key) · `source_path` · `relative_path` · `size_bytes` ·
`sha256` · `collected_at` · `parser_name` · `parser_version` · `attributes_json`

> Assumed one row per parsed source object, with the parser stamped for reproducibility.

### 13.2 `events` — confidence: **medium-low**
`artifact_id` FK · `device_id` FK · `event_type` (vocab key) · `occurred_at` · `occurred_at_precision`
(`exact`/`minute`/`hour`/`day`) · `actor` · `counterparty` · `direction` (`in`/`out`/`n/a`) ·
`summary` · `attributes_json` · `confidence` REAL · `source_offset`

> **Guessed hardest:** `occurred_at_precision` and `confidence`. Real artifacts carry timestamps of
> wildly differing precision, and a timeline that cannot say "this is accurate to the day" will
> mislead. If Phase 3 does not want them, they cost two unused columns; if it wants them and they are
> absent, it is a migration. Included deliberately.

### 13.3 `graph_nodes` — confidence: **medium**
`node_type` (vocab key) · `label` · `canonical_value` · `indicator_id` FK NULL · `first_seen_at` ·
`last_seen_at` · `attributes_json` · `UNIQUE (case_id, node_type, canonical_value)`

> `indicator_id` links a node back to the seed indicator that produced it — this is what lets Phase 4
> weight indicator-derived nodes above discovered ones, and is the reason R2.5 insists on typed
> indicators. `canonical_value` reuses §6.2's normalisation, so a seed mobile number and a parsed one
> collapse to one node.

### 13.4 `graph_edges` — confidence: **medium**
`src_node_id` FK · `dst_node_id` FK · `edge_type` (vocab key) · `directed` BOOL · `weight` REAL ·
`first_seen_at` · `last_seen_at` · `evidence_refs_json` (event ids) · `attributes_json`

> `evidence_refs_json` rather than an edge↔event join table — flagged as a real trade-off. A join
> table queries better; JSON avoids a ninth table R10.1 did not authorise. **Ask me to switch if
> Phase 4 traverses edges by supporting event.**

### 13.5 `candidate_actions` — confidence: **low**
`action_type` (vocab key) · `target_entity_type` · `target_entity_id` · `title` · `rationale` ·
`status` (`proposed`/`accepted`/`rejected`/`completed`) · `generated_at` · `generator_version` ·
`params_json`

> Assumed to be the triage-queue row — the thing R14.6's placeholder will eventually list.

### 13.6 `action_scores` — confidence: **low** ⚠
`candidate_action_id` FK · `scored_at` · `total_score` REAL · `components_json` ·
`volatility_component` REAL · `elapsed_seconds_at_scoring` INTEGER · `weights_version` ·
`scorer_version`

> ⚠ **Highest-risk table.** Modelled as *one row per scoring run* rather than one per action, so
> rescoring keeps history — scores that change as evidence decays are only interpretable if you can
> see the previous value. `elapsed_seconds_at_scoring` freezes §9's clock reading at scoring time so a
> score is reproducible after the fact. `components_json` keeps the per-component breakdown open,
> since the components are entirely unspecified. **Confirm the component set.**

### 13.7 `investigator_feedback` — confidence: **low** ⚠
`candidate_action_id` FK NULL · `target_entity_type` NULL · `target_entity_id` NULL ·
`verdict` (`useful`/`not_useful`/`incorrect`/`duplicate`) · `rating` INTEGER NULL · `comment` ·
`examiner_name`

> ⚠ Assumed to attach *either* to a candidate action *or* to an arbitrary entity, hence the nullable
> pairs. If feedback only ever targets actions, three columns are dead weight. **Confirm what
> feedback attaches to and what the verdict vocabulary is** — if this feeds learned ranking in
> Phase 5, the verdict set is a modelling decision, not a UI label.

### 13.8 `evidence_packages` — confidence: **low** ⚠
`package_name` · `package_path` · `manifest_json` · `package_sha256` · `size_bytes` · `item_count` ·
`export_format` (vocab key) · `created_by_examiner`

> ⚠ Assumed to be an exported bundle with its own hash for chain-of-custody on the export itself.
> **Open recommendation:** package contents belong in a child `evidence_package_items` table
> (`package_id`, `item_type`, `item_id`, `sha256`), not in `manifest_json` — a manifest you cannot
> query is a manifest you cannot verify selectively. That would be a **ninth** table, which R10.1
> does not list. Say the word and I add it now rather than in Phase 5.

## 14. API surface

All under `/api/v1`, all returning the repo-standard response envelope (R1.3, R14.8).

| Method | Path | Requirement |
|---|---|---|
| `POST` | `/cases` | R1.1 |
| `GET` | `/cases` | R1.12 |
| `GET` | `/cases/{case_id}` | R1.11, R6.4 |
| `POST` | `/cases/{case_id}/devices` | R4.1 |
| `GET` | `/cases/{case_id}/devices` | R4.8 |
| `POST` | `/cases/{case_id}/evidence` | R9.1 |
| `GET` | `/cases/{case_id}/evidence` | R9.8 |
| `GET` | `/cases/{case_id}/audit` | R7.11 |
| `GET` | `/cases/{case_id}/audit/verify` | R8.1 |
| `GET` | `/vocabularies` | R5.6 |

Validation failures return field-level errors naming every missing field (R1.3), not the first one.

## 15. Network posture (R13)

- Uvicorn binds `settings.BIND_HOST`, defaulting to `127.0.0.1` (R13.1, R13.7).
- A middleware rejects any request whose `request.client.host` is not loopback with `403` — defence
  in depth if the bind address is ever misconfigured (R13.2).
- No outbound calls exist anywhere in the codebase: no fonts, no CDN, no telemetry, no update check
  (R13.4, R13.5). Enforced by a CI grep for `http://`/`https://` outside comments and docs.
- The frontend loads every asset relatively; fonts are system stacks (R13.6).
- Startup performs no network I/O, so the service starts with the interface down (R13.3).

## 16. Evidence hashing (R9)

```python
CHUNK = 1024 * 1024
digest = hashlib.sha256()
with open(path, "rb") as fh:
    while chunk := fh.read(CHUNK):
        digest.update(chunk)
```

1 MiB chunks — constant memory regardless of file size (R9.3, AC-9). Hex digest is lowercase and
64 chars (R9.9), matching `sha256sum` byte-for-byte over the same file (R9.4). Hashing runs in a
thread executor so a multi-gigabyte file does not block the event loop. Missing or unreadable files
register nothing and report the attempted path (R9.5). The `evidence_records` row carries
`case_id`, nullable `device_id` (OQ-8), `source_path`, `sha256`, `size_bytes`, `registering_action`,
`registered_at` (R9.2, R9.7), and the append is audited in-transaction (R9.6).

## 17. Frontend (R14)

Five pages, no sixth (R14.7): `case-new.html`, `device-new.html`, `devices.html`, `audit.html`,
`triage.html` (static placeholder, R14.6). Shared `api.js` and `render.js`; no framework, no npm.

The case form builds indicators as typed rows — a type dropdown populated from `/vocabularies` plus
a value field, add/remove freely, zero rows permitted (R14.2, R2.3, R2.4). The audit viewer lists
entries in sequence order with a verify button showing intact/broken and naming the breaking entry
(R14.5, R8.9). Every timestamp renders via `toLocaleString()` from the UTC value (R11.8).

## 18. Testing strategy

Each acceptance criterion gets an automated test; AC-4 and AC-8 are the ones that matter most because
they test failure paths that are easy to believe work and easy to get wrong.

| Test | Method | AC |
|---|---|---|
| Full-indicator case creates isolated tree | create with all six types; assert four subfolders exist | AC-1 |
| Three devices, three statuses | register and list | AC-2 |
| Chain intact after N actions | verify after a scripted session | AC-3 |
| **Tamper detection** | `DROP TRIGGER`, `UPDATE` entry 4's summary via raw `sqlite3`, verify | AC-4 |
| Hash matches independent tool | compare against `hashlib` over the same bytes and against `sha256sum` | AC-5 |
| Offline start | run with a socket-blocking fixture asserting no outbound connect | AC-6 |
| No shared DB or evidence folder | create two cases; assert path disjointness | AC-7 |
| **Atomic failure** | monkeypatch a failure into step 2; assert no visible root and no `cases` row | AC-8 |
| Large-file hashing | 4 GiB sparse file; assert bounded RSS | AC-9 |
| Naive timestamp rejected | assert `ValueError` at the persistence boundary | R11.2 |
| Unknown status on read survives | insert an unknown key directly; assert read returns it | R5.7 |

## 19. Traceability

| Requirement | Design section |
|---|---|
| R1 | §6.1, §14 |
| R2 | §6.2 |
| R3 | §4, §8 |
| R4 | §6.3 |
| R5 | §7 |
| R6 | §9 |
| R7 | §10 |
| R8 | §11 |
| R9 | §16 |
| R10 | §13 |
| R11 | §12 |
| R12 | §6.1, §10.1 |
| R13 | §15 |
| R14 | §17 |
| R15 | §13 (tables created empty), §1 |

---

## 20. What I need confirmed before tasks

1. **§13.6, §13.7, §13.8** — the three low-confidence forward-declared tables. This is the last
   cheap moment to fix them (R10.6's entire purpose).
2. **§13.4** — `evidence_refs_json` vs. an edge↔event join table.
3. **§13.8** — whether to add the ninth table `evidence_package_items` now.
4. **OQ-7** — hash-in-place is assumed; `evidence/` is provisioned but stays empty in Phase 1.
5. **OQ-1** — the three incident template names.

**End of design. Stopping here for review — task breakdown has not been started.**
