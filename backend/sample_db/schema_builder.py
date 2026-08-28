"""
Derive the sis_chatbot_db table structure directly from the CSVs in
backend/sample_table/.

The column list of every table is taken verbatim from the CSV header, so the
structure always follows the source extract. Only the PostgreSQL type is
inferred, from the column name, using the rules below.

Nothing here reads CSV *rows* -- loading those is seed_sample_db.py's job.
"""
from pathlib import Path
import csv

SAMPLE_TABLE_DIR = Path(__file__).resolve().parents[1] / "sample_table"

# CSV file  ->  table name in sis_chatbot_db.
# The "_demo" suffix is dropped and a few names are tightened so the table
# reads as a table rather than as an export file.
TABLE_NAMES = {
    "appl_log_urban_demo.csv": "urban_application_log",
    "application_workflow_demo.csv": "application_workflow_action",
    "areg_temp_subdivclub_demo.csv": "urban_temp_subdivision_parcel",
    "chitta_temp_subdivclub_demo.csv": "urban_temp_subdivision_owner",
    "full_field_patta_transfer_application_information_demo.csv": "nisd_transfer_application_info",
    "full_field_patta_transfer_igrs_owner_demo.csv": "nisd_transfer_igrs_owner",
    "full_field_patta_transfer_new_owner_demo.csv": "nisd_transfer_new_owner",
    "full_field_patta_transfer_old_owner_demo.csv": "nisd_transfer_old_owner",
    "full_field_patta_transfer_return_owner_demo.csv": "nisd_transfer_return_owner",
    "full_field_patta_transfer_urban_demo.csv": "nisd_transfer_urban_detail",
    "sub_div_patta_transfer_application_information_urban_demo.csv": "isd_transfer_application_info",
    "sub_div_patta_transfer_urban_demo.csv": "isd_transfer_urban_detail",
    "uareg_demo.csv": "urban_parcel_register",
    "uaregmap_ds_demo.csv": "urban_parcel_signature",
    "uchitta_natham_demo.csv": "urban_natham_chitta_owner",
    "uchitta_nathammap_ds_demo.csv": "urban_natham_chitta_signature",
}

# Service codes handled by SIS, per documents/tamilnilam_urban_services_and_districts.txt.
# Only the codes that actually appear in the extracts are exercised by the seeder.
SERVICE_CODES = {
    "0153": "NISD - Not Involving Subdivision",
    "0154": "ISD - Involving Subdivision",
    "0155": "Merge Subdivisions",
    "0156": "TSLR Extract with Sketch",
    "0157": "TSLR Extract Only",
    "0158": "Modification / Anadeenam",
    "0159": "Addition",
    "0160": "Deletion",
    "0161": "Street Master",
    "0165": "TSLR Owner Name Correction",
    "0167": "TSLR Settlement - Owner Entry",
    "0169": "Govt to Private",
    "0178": "F-Line (Urban Demarcation)",
}

# --- type inference -------------------------------------------------------

_TIMESTAMP_COLS = {
    "last_updated_datetime", "submitted_datetime", "generated_datetime",
    "signed_datetime", "verified_datetime", "dateofverify",
}
_DATE_COLS = {
    "application_date", "action_date", "field_visit_date", "dispatch_date",
    "received_date", "date_of_birth", "proposed_field_visit_date",
    "document_sent_date", "document_received_date", "challan_date",
    "registration_date", "order_date", "succession_certificate_date",
    "court_order_date", "submission_date", "receipt_date",
    "tahsildar_receipt_date", "surveyor_received_date",
    "surveyor_completed_date", "sketch_sent_date", "sketch_received_date",
    "incorporation_date", "handover_received_date", "commissioner_change_date",
}
_NUMERIC_COLS = {
    "csc_service_charge", "government_service_charge", "payment_amount",
    "annual_income", "area_hectare", "area_ares", "area_square_meter",
    "tax_rate", "tax_per_hectare", "adopted_area_hectare", "adopted_area_ares",
    "adopted_area_square_meter", "surveyor_adopted_area_hectare",
    "surveyor_adopted_area_ares", "surveyor_adopted_area_square_meter",
    "district_adopted_area_hectare", "district_adopted_area_ares",
    "district_adopted_area_square_meter", "extent_value_1", "extent_value_2",
    "extent_value_3", "extent_in_ares", "purchased_area", "total_tax",
    "municipal_tax", "new_total_tax", "old_total_tax",
}
_INTEGER_COLS = {
    "serial_number", "slno", "sno", "owner_no", "own_num", "rel_num",
    "relation_no", "relative_no", "total_subdivisions",
}
_TEXT_COLS = {
    "remarks", "remarks1", "address", "current_address", "permanent_address",
    "document_hash", "digital_signature_content", "signature_content",
    "nic_digital_signature", "owner_image", "owner_photo", "uds_details",
    "enclosure_details", "missing_documents", "order_remarks",
    "sis_remarks", "surveyor_remarks", "district_remarks", "tahsildar_remarks",
    "auto_recommendation_remarks", "descriptive_land_use",
    "first_page_document", "reverse_page_document", "last_page_document",
}
# Everything else is VARCHAR. Codes such as '01', '0154' and '0015' keep their
# leading zeros, so they must never become integers.
_WIDE_VARCHAR = {
    "applicant_name", "owner_name_tamil", "owner_name_english",
    "relative_name_tamil", "relative_name_english", "registration_place",
    "transfer_reason", "application_status", "court_name", "treasury_name",
    "bank_name", "bank_branch", "csc_name", "occupation", "relationship",
    "succession_certificate_issued_by", "legal_heir_certificate_issue_place",
    "owner_correction_reason", "rejection_reason_code", "land_use",
    "land_use_description", "proposed_remarks", "sis_recommendation_reason",
    "surveyor_recommendation_reason", "district_recommendation_reason",
    "tahsildar_recommendation_reason", "physical_verification_status",
}


def pg_type(column: str) -> str:
    if column in _TIMESTAMP_COLS:
        # The extracts carry an offset ("2022-09-28 15:18:25.659+05:30");
        # TIMESTAMPTZ keeps it instead of silently dropping it.
        return "TIMESTAMPTZ"
    if column in _DATE_COLS:
        return "DATE"
    if column in _NUMERIC_COLS:
        return "NUMERIC(14,2)"
    if column in _INTEGER_COLS:
        return "INTEGER"
    if column in _TEXT_COLS:
        return "TEXT"
    if column in _WIDE_VARCHAR:
        return "VARCHAR(200)"
    return "VARCHAR(60)"


def read_header(csv_path: Path) -> list[str]:
    with csv_path.open(encoding="utf-8", errors="replace", newline="") as fh:
        return next(csv.reader(fh))


def table_specs() -> dict[str, list[tuple[str, str]]]:
    """{table_name: [(column, pg_type), ...]} for all 16 extracts."""
    specs = {}
    for csv_name, table in TABLE_NAMES.items():
        path = SAMPLE_TABLE_DIR / csv_name
        if not path.exists():
            raise FileNotFoundError(path)
        specs[table] = [(c, pg_type(c)) for c in read_header(path)]
    return specs


# Indexes worth having for the chatbot's lookup patterns: applications are
# fetched by application_id, parcels by survey number, owners by patta number.
_INDEX_COLUMNS = ("application_id", "survey_number", "patta_number",
                  "district_code", "application_status", "service_code")


def build_ddl() -> str:
    lines = ["-- sis_chatbot_db schema.",
             "-- Column lists are taken verbatim from backend/sample_table/*.csv.",
             "-- Generated by backend/sample_db/schema_builder.py -- do not hand-edit.",
             ""]
    for table, cols in table_specs().items():
        lines.append(f"DROP TABLE IF EXISTS {table} CASCADE;")
        lines.append(f"CREATE TABLE {table} (")
        body = ["    row_id BIGSERIAL PRIMARY KEY"]
        body += [f"    {c} {t}" for c, t in cols]
        lines.append(",\n".join(body))
        lines.append(");")
        for col, _ in cols:
            if col in _INDEX_COLUMNS:
                lines.append(f"CREATE INDEX idx_{table}_{col} ON {table} ({col});")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "schema_sis_chatbot_db.sql"
    ddl = build_ddl()
    out.write_text(ddl, encoding="utf-8")
    specs = table_specs()
    print(f"Wrote {out}")
    print(f"{len(specs)} tables, {sum(len(v) for v in specs.values())} columns total")
    for t, cols in specs.items():
        print(f"  {t:38s} {len(cols):3d} cols")
