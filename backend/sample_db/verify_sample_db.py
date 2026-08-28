"""
Verify sis_chatbot_db after seeding.

Three things are checked:
  1. structure  -- every table's columns match its source CSV header exactly
  2. references -- no row points at an application or patta that doesn't exist
  2b. populated -- no column is entirely NULL, and the DSC columns hold real
                   PKCS#7 that parses back
  3. no leakage -- no identity-bearing value (names, usernames, CAN/Aadhaar/
                   mobile/document numbers, addresses) is reused verbatim from
                   the CSVs in backend/sample_table/, and no person name (or
                   word of one) is reused from them either

Run:  python backend/sample_db/verify_sample_db.py
Exits non-zero if any check fails.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

# Names in the extracts are Tamil, and a Windows console defaults to cp1252,
# which cannot encode them. Same guard as backend/main.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import psycopg2

from schema_builder import SAMPLE_TABLE_DIR, TABLE_NAMES, read_header, table_specs

csv.field_size_limit(10 ** 8)  # the DSC extracts carry very long fields

DB_NAME = "sis_chatbot_db"
CONN = dict(host="127.0.0.1", port=5432, user="postgres", password="Mayur@2005")

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


REFERENCE_CHECKS = [
    ("application_workflow_action -> urban_application_log",
     "application_workflow_action w", "urban_application_log l",
     "l.application_id = w.application_id"),
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
    """No column may be entirely NULL -- every field carries sample data."""
    failures = []
    for table, cols in table_specs().items():
        empty = []
        for col, _ in cols:
            cur.execute(f"SELECT count(*) FROM {table} WHERE {col} IS NOT NULL")
            if cur.fetchone()[0] == 0:
                empty.append(col)
        print(f"  {'ok ' if not empty else 'FAIL'} {table:34s} "
              f"{len(cols) - len(empty)}/{len(cols)} columns populated")
        if empty:
            failures.append(f"{table}: all-NULL columns {empty}")
    return failures


# (table, hash column, signature column)
SIGNATURE_TABLES = [
    ("urban_parcel_signature", "document_hash", "digital_signature_content", "username"),
    ("urban_natham_chitta_signature", "document_hash", "signature_content",
     "signed_by_username"),
]


def check_signatures(cur) -> list[str]:
    """The DSC blobs must be genuine base64 PKCS#7, wrapped like the extracts."""
    import base64
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import pkcs7

    failures = []
    for table, hash_col, sig_col, user_col in SIGNATURE_TABLES:
        cur.execute(f"SELECT {hash_col}, {sig_col}, {user_col} FROM {table} LIMIT 25")
        rows = cur.fetchall()
        bad = 0
        mismatched = 0
        widths = set()
        for digest, blob, username in rows:
            lines = blob.splitlines()
            widths.update(len(line) for line in lines[:-1])
            try:
                der = base64.b64decode("".join(lines), validate=True)
                certs = pkcs7.load_der_pkcs7_certificates(der)
                if not certs or len(digest) != 64:
                    bad += 1
                    continue
                # the certificate must name the officer the row credits
                cn = certs[0].subject.get_attributes_for_oid(
                    x509.oid.NameOID.COMMON_NAME)[0].value
                if cn.upper() != username.upper():
                    mismatched += 1
            except Exception:
                bad += 1
        ok = bad == 0 and mismatched == 0 and widths <= {76}
        print(f"  {'ok ' if ok else 'FAIL'} {table:34s} "
              f"{len(rows) - bad}/{len(rows)} parse as PKCS#7, "
              f"signer mismatches={mismatched}, line widths={sorted(widths)}")
        if bad:
            failures.append(f"{table}: {bad} unparseable signature blobs")
        if mismatched:
            failures.append(f"{table}: {mismatched} rows whose cert CN "
                            f"differs from {user_col}")
        if not widths <= {76}:
            failures.append(f"{table}: base64 not wrapped at 76 chars")
    return failures


def _words(value: str) -> set[str]:
    return {p.lower() for p in re.split(r"[\s.,\-_/()]+", value.strip()) if len(p) >= 3}


def check_person_names(cur) -> list[str]:
    """No person name may be reused from the CSVs.

    Exact reuse only: the whole name, or any single word of it. A name that
    merely shares a substring with a CSV name (Ramesh / Ram) is fine -- these
    are ordinary Indian names and such overlap is unavoidable.
    """
    csv_names: set[str] = set()
    csv_words: set[str] = set()
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
                        csv_names.add(v.lower())
                        csv_words |= _words(v)

    db_names: set[str] = set()
    for table, cols in table_specs().items():
        for col, _ in cols:
            if col not in PERSON_NAME_COLUMNS:
                continue
            cur.execute(f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL")
            db_names |= {str(v).strip() for (v,) in cur.fetchall() if str(v).strip()}

    reused_whole = sorted(n for n in db_names if n.lower() in csv_names)
    reused_word = sorted({w for n in db_names for w in _words(n)} & csv_words)

    print(f"  csv person names={len(csv_names)} words={len(csv_words)}  "
          f"db person names={len(db_names)}")
    print(f"  {'ok ' if not reused_whole else 'FAIL'} whole names reused: {len(reused_whole)}")
    for n in reused_whole[:15]:
        print(f"    ! {n}")
    print(f"  {'ok ' if not reused_word else 'FAIL'} name words reused: {len(reused_word)}")
    for w in reused_word[:15]:
        print(f"    ! {w}")

    failures = []
    if reused_whole:
        failures.append(f"{len(reused_whole)} person names reused from the CSVs")
    if reused_word:
        failures.append(f"{len(reused_word)} person-name words reused from the CSVs")
    return failures


def check_no_leakage(cur) -> list[str]:
    csv_values = _csv_identity_values()
    db_values = set()
    for table, cols in table_specs().items():
        for col, _ in cols:
            if col not in IDENTITY_COLUMNS:
                continue
            cur.execute(f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL")
            db_values |= {str(v).strip() for (v,) in cur.fetchall()
                          if len(str(v).strip()) > 3}
    overlap = sorted(csv_values & db_values)
    print(f"  csv identity values={len(csv_values)}  "
          f"db identity values={len(db_values)}  verbatim overlap={len(overlap)}")
    for v in overlap[:20]:
        print(f"    ! reused verbatim: {v!r}")
    return [f"{len(overlap)} identity values reused verbatim"] if overlap else []


def main() -> int:
    conn = psycopg2.connect(dbname=DB_NAME, **CONN)
    failures: list[str] = []
    with conn.cursor() as cur:
        print("\n[1/6] structure follows the CSV headers")
        failures += check_structure(cur)
        print("\n[2/6] referential coherence")
        failures += check_references(cur)
        print("\n[3/6] every column populated")
        failures += check_populated(cur)
        print("\n[4/6] DSC columns hold parseable PKCS#7")
        failures += check_signatures(cur)
        print("\n[5/6] no identity value reused from the CSVs")
        failures += check_no_leakage(cur)
        print("\n[6/6] no person name reused from the CSVs")
        failures += check_person_names(cur)

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
