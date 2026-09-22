"""
Verify sis_chatbot_db after seeding.

Four things are checked:
  1. structure  -- every table's columns match its source CSV header exactly
  2. references -- no row points at an application or patta that doesn't exist
  3. fidelity   -- the load is faithful to the CSVs: a column that carries
                   values in the extract must carry them in the table, and no
                   identity-bearing value exists in the database that is not in
                   the extract (nothing is invented)
  4. signatures -- the DSC blobs are genuine base64 PKCS#7 that parses back

The extracts are the source of record and are loaded verbatim, so "this value
also appears in the CSV" is the expected state, not a leak. The one column that
must NOT come from the extracts is aadhaar_number: it arrives stripped and is
replaced with a synthetic, checksum-valid number, which check 3 enforces.

Run:  python backend/sample_db/verify_sample_db.py
Exits non-zero if any check fails.
"""
from __future__ import annotations

import csv
import sys

# Names in the extracts are Tamil, and a Windows console defaults to cp1252,
# which cannot encode them. Same guard as backend/main.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import psycopg2

from dbconn import conn_params
from schema_builder import SAMPLE_TABLE_DIR, TABLE_NAMES, read_header, table_specs

csv.field_size_limit(10 ** 8)  # the DSC extracts carry very long fields

DB_NAME = "sis_chatbot_db"

# Columns whose values identify a person, an officer or a document.
IDENTITY_COLUMNS = {
    "owner_name_tamil", "owner_name_english", "relative_name_tamil",
    "relative_name_english", "applicant_name", "mother_name", "father_name",
    "user_id", "updated_by_user", "username", "signed_by_username",
    "username_verify", "source_name", "can_number", "mobile_number",
    "aadhaar_number", "registration_document_number", "current_address",
    "permanent_address",
}
# Placeholders that carry no identity, so a match on them means nothing.
_NOT_IDENTITY = {"-", "...", "Not Stated", "", "0"}

# Columns holding the name of a *person* (owner, relative, applicant, parent).
# Officer accounts, role ids, designations, court and place names are NOT here:
# those legitimately match the extracts and must not be reworded.
PERSON_NAME_COLUMNS = {
    "owner_name_tamil", "owner_name_english", "relative_name_tamil",
    "relative_name_english", "applicant_name", "mother_name", "father_name",
}


# application_workflow_action is deliberately NOT here: the extract is a
# district-wide dump (288087 rows) and most of it belongs to settlement service
# codes that urban_application_log does not carry. Orphans there are expected;
# what must hold is the other direction, checked separately below -- every
# logged application has at least one workflow row.
REFERENCE_CHECKS = [
    ("nisd_transfer_application_info -> urban_application_log",
     "nisd_transfer_application_info w", "urban_application_log l",
     "l.application_id = w.application_id"),
    ("isd_transfer_application_info -> urban_application_log",
     "isd_transfer_application_info w", "urban_application_log l",
     "l.application_id = w.application_id"),
    ("nisd_transfer_old_owner -> urban_application_log",
     "nisd_transfer_old_owner w", "urban_application_log l",
     "l.application_id = w.application_id"),
    ("nisd_transfer_new_owner -> urban_application_log",
     "nisd_transfer_new_owner w", "urban_application_log l",
     "l.application_id = w.application_id"),
    ("nisd_transfer_return_owner -> urban_application_log",
     "nisd_transfer_return_owner w", "urban_application_log l",
     "l.application_id = w.application_id"),
    ("nisd_transfer_urban_detail -> urban_application_log",
     "nisd_transfer_urban_detail w", "urban_application_log l",
     "l.application_id = w.application_id"),
    ("isd_transfer_urban_detail -> urban_application_log",
     "isd_transfer_urban_detail w", "urban_application_log l",
     "l.application_id = w.application_id"),
    ("urban_temp_subdivision_parcel -> urban_application_log",
     "urban_temp_subdivision_parcel w", "urban_application_log l",
     "l.application_id = w.application_id"),
    ("urban_temp_subdivision_owner -> urban_application_log",
     "urban_temp_subdivision_owner w", "urban_application_log l",
     "l.application_id = w.application_id"),
    ("urban_natham_chitta_owner -> urban_parcel_register",
     "urban_natham_chitta_owner w", "urban_parcel_register l",
     "l.patta_number = w.patta_number"),
    ("urban_parcel_signature -> urban_parcel_register",
     "urban_parcel_signature w", "urban_parcel_register l",
     "l.patta_number = w.patta_number"),
    ("urban_natham_chitta_signature -> urban_parcel_register",
     "urban_natham_chitta_signature w", "urban_parcel_register l",
     "l.patta_number = w.patta_number"),
]

# Each transfer table must only carry applications of its own service code
# (0153 = NISD, 0154 = ISD -- see documents/tamilnilam_urban_services_and_districts.txt).
SERVICE_CHECKS = [
    ("nisd_transfer_application_info", "0153"),
    ("nisd_transfer_urban_detail", "0153"),
    ("isd_transfer_application_info", "0154"),
    ("isd_transfer_urban_detail", "0154"),
    ("urban_temp_subdivision_parcel", "0154"),
    ("urban_temp_subdivision_owner", "0154"),
]


def check_structure(cur) -> list[str]:
    failures = []
    for csv_name, table in TABLE_NAMES.items():
        expected = read_header(SAMPLE_TABLE_DIR / csv_name)
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s AND column_name<>'row_id' "
            "ORDER BY ordinal_position", (table,))
        actual = [r[0] for r in cur.fetchall()]
        if actual != expected:
            missing = set(expected) - set(actual)
            extra = set(actual) - set(expected)
            failures.append(f"{table}: missing={sorted(missing)} extra={sorted(extra)}")
        else:
            print(f"  ok  {table:34s} {len(actual):3d} cols match {csv_name}")
    return failures


def check_references(cur) -> list[str]:
    failures = []
    for label, child, parent, join in REFERENCE_CHECKS:
        cur.execute(f"SELECT count(*) FROM {child} "
                    f"WHERE NOT EXISTS (SELECT 1 FROM {parent} WHERE {join})")
        n = cur.fetchone()[0]
        print(f"  {'ok ' if n == 0 else 'FAIL'} {label:52s} orphans={n}")
        if n:
            failures.append(f"{label}: {n} orphans")
    # the direction that must hold for the workflow dump
    cur.execute("""SELECT count(*) FROM urban_application_log l
                   WHERE NOT EXISTS (SELECT 1 FROM application_workflow_action w
                                     WHERE w.application_id = l.application_id)""")
    n = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM application_workflow_action")
    total = cur.fetchone()[0]
    cur.execute("""SELECT count(*) FROM application_workflow_action w
                   WHERE EXISTS (SELECT 1 FROM urban_application_log l
                                 WHERE l.application_id = w.application_id)""")
    matched = cur.fetchone()[0]
    print(f"  {'ok ' if n == 0 else 'FAIL'} "
          f"{'every logged application has a workflow row':52s} without={n}")
    print(f"  note {total - matched} of {total} workflow rows belong to applications "
          f"outside the log (district-wide extract)")
    if n:
        failures.append(f"{n} applications in urban_application_log have no "
                        f"workflow row")

    for table, service in SERVICE_CHECKS:
        cur.execute(f"SELECT count(*) FROM {table} t "
                    f"JOIN urban_application_log l USING (application_id) "
                    f"WHERE l.service_code <> %s", (service,))
        n = cur.fetchone()[0]
        print(f"  {'ok ' if n == 0 else 'FAIL'} {table + ' service=' + service:52s} wrong={n}")
        if n:
            failures.append(f"{table}: {n} rows with service_code <> {service}")
    return failures


def _csv_identity_values() -> set[str]:
    values = set()
    for csv_name in TABLE_NAMES:
        path = SAMPLE_TABLE_DIR / csv_name
        with path.open(encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            cols = [c for c in (reader.fieldnames or []) if c in IDENTITY_COLUMNS]
            if not cols:
                continue
            for row in reader:
                for col in cols:
                    v = (row.get(col) or "").strip()
                    if len(v) > 3 and v not in _NOT_IDENTITY:
                        values.add(v)
    return values


def check_populated(cur) -> list[str]:
    """The load is faithful: a column with values in the CSV has them in the table.

    A column that is empty in the extract is empty here too -- the extracts are
    sparse and that is the data, not a defect. What would be a defect is the
    loader dropping values the CSV does carry, so the comparison is against the
    CSV rather than against zero.
    """
    failures = []
    for csv_name, table in TABLE_NAMES.items():
        cols = [c for c, _ in table_specs()[table]]
        empty = []
        for col in cols:
            cur.execute(f"SELECT count({col}) FROM {table}")
            if cur.fetchone()[0] == 0:
                empty.append(col)
        lost = []
        if empty:
            path = SAMPLE_TABLE_DIR / csv_name
            with path.open(encoding="utf-8", errors="replace", newline="") as fh:
                for row in csv.DictReader(fh):
                    for col in empty:
                        v = (row.get(col) or "").strip()
                        if v and v not in _NOT_IDENTITY and col not in lost:
                            lost.append(col)
        print(f"  {'ok ' if not lost else 'FAIL'} {table:34s} "
              f"{len(cols) - len(empty)}/{len(cols)} columns carry data"
              + (f" ({len(empty)} empty in the extract too)" if empty else ""))
        if lost:
            failures.append(f"{table}: columns lost in the load {lost}")
    return failures


# (table, PKCS#7 column, signed-payload column, username column).
# The column names in the extracts are the wrong way round and must be read as
# they are, not as they are named: `document_hash` holds the base64 PKCS#7
# SignedData blob (wrapped at 76 chars), while `digital_signature_content` /
# `signature_content` hold the JSON payload that was signed.
SIGNATURE_TABLES = [
    ("urban_parcel_signature", "document_hash", "digital_signature_content",
     "username"),
    ("urban_natham_chitta_signature", "document_hash", "signature_content",
     "signed_by_username"),
]


def check_signatures(cur) -> list[str]:
    """The DSC blobs must be genuine base64 PKCS#7, wrapped like the extracts."""
    import base64
    import warnings
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import pkcs7

    failures = []
    for table, sig_col, payload_col, user_col in SIGNATURE_TABLES:
        # every row, not a sample: the two signature tables are small enough
        # (1036 + 439) that a corrupt blob outside a 25-row window would
        # otherwise go unseen
        cur.execute(f"SELECT {sig_col}, {payload_col}, {user_col} FROM {table} "
                    f"WHERE {sig_col} IS NOT NULL")
        rows = cur.fetchall()
        bad = 0
        no_payload = 0
        widths = set()
        # one login must always present the same certificate subject
        cn_of: dict[str, str] = {}
        inconsistent = 0
        for blob, payload, username in rows:
            lines = blob.splitlines()
            widths.update(len(line) for line in lines[:-1])
            if not (payload or "").strip().startswith(("{", "[")):
                no_payload += 1
            try:
                der = base64.b64decode("".join(lines), validate=True)
                with warnings.catch_warnings():
                    # the extracts' blobs are BER, not strict DER
                    warnings.simplefilter("ignore")
                    certs = pkcs7.load_der_pkcs7_certificates(der)
                if not certs:
                    bad += 1
                    continue
                # the CN is the officer's name on the certificate, not their
                # login (`tut_ssundari` signs as `SIVAGAMASUNDARI S`), so the
                # check is consistency, not equality
                cn = certs[0].subject.get_attributes_for_oid(
                    x509.oid.NameOID.COMMON_NAME)[0].value
                if cn_of.setdefault(username, cn) != cn:
                    inconsistent += 1
            except Exception:
                bad += 1
        ok = bad == 0 and inconsistent == 0 and no_payload == 0 and widths <= {76}
        print(f"  {'ok ' if ok else 'FAIL'} {table:34s} "
              f"{len(rows) - bad}/{len(rows)} parse as PKCS#7, "
              f"{len(rows) - no_payload}/{len(rows)} carry a signed payload, "
              f"{len(cn_of)} signers, line widths={sorted(widths)}")
        if bad:
            failures.append(f"{table}: {bad} unparseable signature blobs")
        if no_payload:
            failures.append(f"{table}: {no_payload} rows with no signed payload "
                            f"in {payload_col}")
        if inconsistent:
            failures.append(f"{table}: {inconsistent} rows where one {user_col} "
                            f"presents more than one certificate subject")
        if not widths <= {76}:
            failures.append(f"{table}: base64 not wrapped at 76 chars")
    return failures


def _csv_person_names() -> set[str]:
    names: set[str] = set()
    for csv_name in TABLE_NAMES:
        path = SAMPLE_TABLE_DIR / csv_name
        with path.open(encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            cols = [c for c in (reader.fieldnames or []) if c in PERSON_NAME_COLUMNS]
            if not cols:
                continue
            for row in reader:
                for col in cols:
                    v = (row.get(col) or "").strip()
                    if v and v not in _NOT_IDENTITY:
                        names.add(v.lower())
    return names


def check_nothing_invented(cur) -> list[str]:
    """Every identity value in the database comes from the extracts.

    The extracts are the source of record and are loaded verbatim, so overlap
    with the CSVs is the expected state. The defect this catches is the
    opposite one: a name, login, CAN or document number that appears in the
    database but in no extract -- a value the pipeline invented, which would
    make the database say something the source does not.

    `aadhaar_number` is excluded here and checked in the other direction by
    check_aadhaar_is_synthetic(): it arrives stripped and must NOT be a CSV
    value.
    """
    csv_values = _csv_identity_values()
    csv_names = _csv_person_names()
    failures = []

    invented = set()
    for table, cols in table_specs().items():
        for col, _ in cols:
            if col not in IDENTITY_COLUMNS or col == "aadhaar_number":
                continue
            cur.execute(f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL")
            for (v,) in cur.fetchall():
                v = str(v).strip()
                if len(v) > 3 and v not in _NOT_IDENTITY and v not in csv_values:
                    invented.add(v)
    print(f"  csv identity values={len(csv_values)}  "
          f"not found in any extract={len(invented)}")
    for v in sorted(invented)[:15]:
        print(f"    ! not in any extract: {v!r}")
    if invented:
        failures.append(f"{len(invented)} identity values are in the database "
                        f"but in no extract")

    db_names: set[str] = set()
    for table, cols in table_specs().items():
        for col, _ in cols:
            if col not in PERSON_NAME_COLUMNS:
                continue
            cur.execute(f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL")
            db_names |= {str(v).strip() for (v,) in cur.fetchall() if str(v).strip()}
    unknown = sorted(n for n in db_names if n.lower() not in csv_names
                     and n not in _NOT_IDENTITY)
    print(f"  db person names={len(db_names)}  not found in any extract={len(unknown)}")
    for n in unknown[:15]:
        print(f"    ! not in any extract: {n}")
    if unknown:
        failures.append(f"{len(unknown)} person names are in the database but "
                        f"in no extract")
    return failures


def check_aadhaar_is_synthetic(cur) -> list[str]:
    """aadhaar_number must be generated, never carried over from an extract.

    The extracts arrive with the column stripped; identifiers.py fills it with a
    deterministic, checksum-valid synthetic number. If a real one ever appeared
    in an extract, this is what would catch it reaching the database.
    """
    from identifiers import aadhaar_valid

    csv_aadhaar: set[str] = set()
    for csv_name in TABLE_NAMES:
        path = SAMPLE_TABLE_DIR / csv_name
        with path.open(encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            if "aadhaar_number" not in (reader.fieldnames or []):
                continue
            for row in reader:
                v = (row.get("aadhaar_number") or "").strip()
                if v and v not in _NOT_IDENTITY:
                    csv_aadhaar.add(v)

    failures = []
    total = malformed = carried = 0
    for table, cols in table_specs().items():
        if "aadhaar_number" not in [c for c, _ in cols]:
            continue
        cur.execute(f"SELECT aadhaar_number FROM {table} "
                    f"WHERE aadhaar_number IS NOT NULL")
        for (v,) in cur.fetchall():
            total += 1
            if not aadhaar_valid(v):
                malformed += 1
            if v in csv_aadhaar:
                carried += 1
    ok = malformed == 0 and carried == 0
    print(f"  {'ok ' if ok else 'FAIL'} {total} aadhaar numbers, "
          f"{malformed} malformed, {carried} carried over from an extract "
          f"({len(csv_aadhaar)} present in the extracts)")
    if malformed:
        failures.append(f"{malformed} aadhaar numbers fail the format/checksum")
    if carried:
        failures.append(f"{carried} aadhaar numbers were carried over from an "
                        f"extract instead of being generated")
    return failures


def main() -> int:
    conn = psycopg2.connect(**conn_params(DB_NAME))
    failures: list[str] = []
    with conn.cursor() as cur:
        print("\n[1/6] structure follows the CSV headers")
        failures += check_structure(cur)
        print("\n[2/6] referential coherence")
        failures += check_references(cur)
        print("\n[3/6] the load is faithful to the CSVs")
        failures += check_populated(cur)
        print("\n[4/6] DSC columns hold parseable PKCS#7")
        failures += check_signatures(cur)
        print("\n[5/6] nothing in the database was invented")
        failures += check_nothing_invented(cur)
        print("\n[6/6] aadhaar is synthetic, never carried over")
        failures += check_aadhaar_is_synthetic(cur)

        print("\nrow counts")
        for table in table_specs():
            cur.execute(f"SELECT count(*) FROM {table}")
            print(f"  {table:34s} {cur.fetchone()[0]:6d}")
    conn.close()

    if failures:
        print(f"\nFAILED ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
