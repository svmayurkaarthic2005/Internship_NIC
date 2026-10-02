"""
Column-name map between backend/sample_table/'s `*_demo` extracts and
backend/sample_db_new/'s newer batch, loaded into SISchatbot without the
`_demo` suffix and without renaming any column (see load_sample_db_new.py).

Comparing headers between the two batches for the 16 tables they share found:

  * 5 tables are column-for-column identical (sub_div_patta_transfer_urban,
    uareg, uaregmap_ds, uchitta_natham, uchitta_nathammap_ds) -- only the
    table name changed.
  * 8 tables were re-exported with shorter column names throughout
    (application_date -> appl_dt, last_updated_datetime -> update_dt, ...).
    One of those, sub_div_patta_transfer_application_information_urban, also
    dropped a column outright (relative_mobile_number has no counterpart).

backend/sample_db/build_app_tables.py's queries are written against the OLD
names. Rather than create a second, `_demo`-named database object that
duplicates every real table under an alias -- which is exactly the repetition
SISchatbot is meant not to carry -- `newcol()` and `sel()` below translate an
old column name to whatever the real table actually calls it, entirely in
Python, at query-building time. Nothing in SISchatbot is named `_demo`;
only this module knows the two names ever meant the same thing.

Used by backend/sample_db_new/build_app_tables_new.py.
"""
from __future__ import annotations

# Tables whose columns are already named exactly like the old *_demo extract --
# newcol()/sel() pass them through unchanged.
PASSTHROUGH = [
    "sub_div_patta_transfer_urban", "uareg", "uaregmap_ds",
    "uchitta_natham", "uchitta_nathammap_ds",
]

# table -> [(old_name, new_name_or_None), ...] in the OLD extract's column
# order. `None` means the old extract carried this column, but the new one
# does not -- the view supplies NULL, typed as the old table's own column type
# so build_app_tables.py's queries see the same shape either way.
RENAMED: dict[str, list[tuple[str, str | None]]] = {
    "appl_log_urban": [
        ("serial_number", "slno"), ("user_id", "user_id"),
        ("department_code", "dept_id"), ("service_code", "service_code"),
        ("district_code", "district_code"), ("taluk_code", "taluk_code"),
        ("village_code", "village_code"), ("urban_unit_code", "urban_unit_code"),
        ("ward_code", "ward_code"), ("block_code", "block_code"),
        ("application_date", "appl_dt"), ("application_status", "appl_status"),
        ("last_updated_datetime", "update_dt"), ("application_id", "appl_id"),
        ("survey_number", "survey_no"), ("subdivision_number", "subdiv_no"),
        ("patta_number", "patta_no"), ("role_id", "role_id"),
        ("source_code", "source_id"), ("csc_service_charge", "csc_charge"),
        ("government_service_charge", "govt_charge"), ("can_number", "can"),
        ("dispatch_date", "despatch_date"), ("received_date", "received_date"),
        ("ip_address", "ip_address"), ("generated_datetime", "generated_date"),
        ("source_name", "source_name"), ("renewal_number", "renewal_no"),
        ("workflow_state", "state"), ("igrs_form6_number", "igrs_form6no"),
        ("return_status", "rtr_str"),
        ("current_subdivision_number", "current_subdivno"),
        ("parent_application_id", "appl_id_q"),
        ("auto_mutated_flag", "automutated"),
        ("is_auto_mutated", "is_automutated"),
        ("igrs_auto_mutation_flag", "automutation_igrs"),
        ("camp_flag", "camp_flag"), ("camp_correction_id", "camp_correction_id"),
        ("camp_code", "camp_code"),
    ],
    "application_workflow": [
        ("serial_number", "slno"), ("application_id", "appl_id"),
        ("district_code", "district_code"), ("taluk_code", "taluk_code"),
        ("village_code", "village_code"), ("action_from_role_id", "action_from"),
        ("action_to_role_id", "action_to"), ("action_date", "action_date"),
        ("remarks", "remarks"), ("action_status", "action_status"),
        ("last_updated_datetime", "update_dt"), ("updated_by_user", "update_by"),
        ("workflow_state", "state"), ("recommendation_status", "recommend"),
        ("field_visit_date", "field_visit_dt"), ("received_flag", "received"),
        ("annual_income", "income"), ("review_flag", "rflag"),
        ("ip_address", "ip_address"),
        ("auto_recommendation_flag", "zdt_automatic_recommend"),
        ("auto_recommendation_remarks", "zdt_automatic_remarks"),
    ],
    "areg_temp_subdivclub": [
        ("application_id", "appl_id"), ("district_code", "district_code"),
        ("taluk_code", "taluk_code"), ("town_code", "town_code"),
        ("ward_code", "ward_code"), ("block_code", "block_code"),
        ("survey_number", "survey_no"),
        ("temporary_subdivision_number", "temp_sub_div_no"),
        ("new_subdivision_number", "new_sub_div_no"),
        ("existing_patta_number", "existing_patta_no"),
        ("area_hectare", "area_hec"), ("area_ares", "area_ares"),
        ("area_square_meter", "area_sqmtr"), ("new_patta_waste", "npatta_waste"),
        ("last_updated_datetime", "update_dt"), ("submitted_datetime", "submit_dt"),
        ("land_type", "land_type"), ("government_private_flag", "govt_pri"),
        ("tax_rate", "tax_rate"), ("tax_per_hectare", "tax_hect"),
        ("primary_soil_type", "soil_typ_p"), ("secondary_soil_type", "soil_typ_s"),
        ("adopted_area_hectare", "adopt_area_hec"),
        ("adopted_area_ares", "adopt_area_ares"), ("status", "status"),
        ("adopted_area_square_meter", "adopt_area_sqmtr"),
        ("surveyor_adopted_area_hectare", "sd_adopt_area_hec"),
        ("surveyor_adopted_area_ares", "sd_adopt_area_ares"),
        ("surveyor_adopted_area_square_meter", "sd_adopt_area_sqmtr"),
        ("district_adopted_area_hectare", "dis_adopt_area_hec"),
        ("district_adopted_area_ares", "dis_adopt_area_ares"),
        ("district_adopted_area_square_meter", "dis_adopt_area_sqmtr"),
        ("new_patta_number", "new_patta_no"),
        ("group_survey_number", "group_survey_no"),
        ("group_subdivision_number", "group_sub_div_no"),
        ("group_temporary_subdivision_number", "group_temp_sub_div_no"),
        ("group_patta_number", "group_patta_no"), ("plot_type", "plot_type"),
        ("plot_number", "plot_no"), ("land_use", "land_use"),
        ("land_use_description", "descriptive_landuse"),
        # Synthetic PK added by both loaders (schema_builder.py / load_sample_db_new.py),
        # not a CSV column -- identical in both extracts.
        ("row_id", "row_id"),
    ],
    "chitta_temp_subdivclub": [
        ("application_id", "appl_id"), ("owner_name_tamil", "owner_name"),
        ("relationship", "relation"), ("relative_name_tamil", "relative_name"),
        ("extent_value_1", "extent_u1"), ("extent_value_2", "extent_u2"),
        ("extent_value_3", "extent_u3"), ("patta_number", "patta_no"),
        ("relation_code", "relation_code"), ("district_code", "district_code"),
        ("taluk_code", "taluk_code"), ("town_code", "town_code"),
        ("ward_code", "ward_code"), ("block_code", "block_code"),
        ("survey_number", "survey_no"),
        ("temporary_subdivision_number", "temp_sub_div_no"),
        ("new_subdivision_number", "new_sub_div_no"),
        ("existing_patta_number", "existing_patta_no"),
        ("owner_no", "owner_num"), ("status", "status"),
        ("owner_name_english", "owner_ename"),
        ("relative_name_english", "relative_ename"),
        ("ownership_share", "share"), ("aadhaar_number", "aadhar_no"),
        ("gender", "sex"),
    ],
    "full_field_patta_transfer_application_information": [
        ("application_id", "appl_id"), ("district_code", "district_code"),
        ("taluk_code", "taluk_code"), ("town_code", "town_code"),
        ("ward_code", "ward_code"), ("block_code", "block_code"),
        ("can_number", "can"), ("applicant_name", "appl_name"),
        ("current_address", "appl_address_cur"), ("mobile_number", "mobile_no"),
        ("application_status", "appl_status"), ("remarks", "remarks"),
        ("last_updated_datetime", "update_date"),
        ("permanent_address", "appl_address_perm"),
        ("mother_name", "mother_name"), ("father_name", "father_name"),
        ("date_of_birth", "dob"), ("gender", "gender"),
        ("occupation", "occupation"), ("enclosure_details", "enclosures"),
        ("barcode_flag", "barcode_flag"), ("first_page_document", "firstpage"),
        ("reverse_page_document", "reversepage"),
        ("last_page_document", "lastpage"),
        ("enclosure_certificate", "encertify"),
        ("proposed_field_visit_date", "proposed_field_visit_date"),
        ("proposed_remarks", "proposed_remarks"),
        ("missing_documents", "missing_docs"),
        ("physical_verification_status", "verifyphysical"),
        ("document_sent_date", "date_of_sending_document"),
        ("document_received_date", "date_of_receipt_of_document"),
        ("challan_number", "challan_no"), ("challan_date", "challan_date"),
        ("treasury_name", "treasury"), ("bank_name", "bank"),
        ("bank_branch", "bank_branch"), ("payment_mode", "paymentmode"),
        ("payment_amount", "amount"), ("igrs_form6_number", "igrs_form6no"),
        ("return_status", "rtr_str"),
        ("owner_correction_reason", "owner_correction_reason"),
        ("merged_application_id", "merge_applid"),
        ("auto_mutated_flag", "is_automutated"),
    ],
    "full_field_patta_transfer_igrs_owner": [
        ("district_code", "district_code"), ("taluk_code", "taluk_code"),
        ("town_code", "town_code"), ("ward_code", "ward_code"),
        ("block_code", "block_code"), ("door_number", "door_no"),
        ("patta_number", "patta_no"), ("occupation_code", "occ_cd"),
        ("owner_no", "own_num"), ("owner_name_tamil", "owner"),
        ("owner_name_english", "owner_english"), ("relation_no", "rel_num"),
        ("relative_name_tamil", "relative"),
        ("relative_name_english", "relative_english"),
        ("relation_code", "reltn"), ("ownership_share", "share"),
        ("address", "address"),
        ("citizen_identification_number", "cin_no"),
        ("form6_number", "form6_no"), ("assignment_number", "assign_no"),
        ("ration_card_number", "ration_card_no"), ("voter_id_number", "epic_no"),
        ("pin_code", "pincode"), ("last_updated_datetime", "updn_dt"),
        ("user_number", "user_no"), ("aadhaar_number", "aadhar_no"),
        ("gender", "sex"), ("igrs_form6_number", "igrs_form6_no"),
        ("mobile_number", "mobile_number"), ("application_id", "appl_id"),
        ("owner_image", "owner_img"),
    ],
    "full_field_patta_transfer_urban": [
        ("slno", "slno"), ("sno", "sno"), ("district_code", "district_code"),
        ("taluk_code", "taluk_code"), ("town_code", "town_code"),
        ("ward_code", "ward_code"), ("block_code", "block_code"),
        ("application_id", "appl_id"), ("survey_number", "survey_no"),
        ("subdivision_number", "sub_div_no"), ("old_patta_number", "old_patta_no"),
        ("land_type_code", "land_type_code"), ("extent_value_1", "extent_u1"),
        ("extent_value_2", "extent_u2"), ("extent_value_3", "extent_u3"),
        ("registration_document_number", "regi_doc_no"),
        ("transfer_reason", "reason"), ("registration_place", "regi_place"),
        ("registration_date", "regi_on"),
        ("generated_patta_number", "zdt_patta_no"),
        ("transaction_status", "transaction_status"),
        ("order_number", "zdt_order_no"), ("order_date", "zdt_order_dt"),
        ("order_remarks", "zdt_remarks"), ("last_updated_datetime", "update_dt"),
        ("submitted_datetime", "submit_date"), ("extent_in_ares", "extentares"),
        ("tax_rate", "taxrate"),
        ("succession_certificate_number", "successioncertificateno"),
        ("succession_certificate_date", "successioncertificatedate"),
        ("succession_certificate_issued_by", "successioncertificateissuedby"),
        ("legal_heir_certificate_issue_place", "legalheircertificateplaceofissue"),
        ("court_order_number", "court_order_no"),
        ("court_order_date", "court_order_date"), ("court_name", "court_name"),
        ("street_code", "street_code"), ("purchased_area", "purchased_area"),
        ("purchased_area_unit", "purchased_area_unit"),
        ("transaction_code", "transaction_code"),
        ("old_street_code", "oldstreet_code"),
        ("sis_recommendation", "sis_rec"), ("sis_remarks", "sis_remarks"),
        ("sis_recommendation_reason", "sis_rec_reason"),
        ("new_door_number", "new_door_no"), ("new_total_tax", "new_tot_tax"),
        ("old_door_number", "old_door_no"), ("old_total_tax", "old_tot_tax"),
        ("group_survey_number", "group_survey_no"),
        ("group_subdivision_number", "group_sub_div_no"),
        ("group_patta_number", "group_patta_no"),
        ("owner_correction_reason", "owner_correction_reason"),
        ("transfer_type", "trans_type"),
        ("direct_transfer_flag", "direct_transfer"),
        ("land_category_code", "land_cat_code"),
    ],
    "sub_div_patta_transfer_application_information_urban": [
        ("application_id", "appl_id"), ("district_code", "district_code"),
        ("taluk_code", "taluk_code"), ("town_code", "town_code"),
        ("ward_code", "ward_code"), ("block_code", "block_code"),
        ("can_number", "can"), ("applicant_name", "appl_name"),
        ("current_address", "appl_address_cur"), ("mobile_number", "mobile_no"),
        ("application_status", "appl_status"), ("remarks", "remarks"),
        ("last_updated_datetime", "update_date"),
        ("application_status_code", "appl_status_code"),
        ("rejection_reason_code", "reject_reason_code"),
        ("enclosure_details", "enclosures"), ("challan_number", "challan_no"),
        ("challan_date", "challan_date"), ("treasury_name", "treasury"),
        ("bank_name", "bank"), ("bank_branch", "bank_branch"),
        ("payment_mode", "paymentmode"), ("first_page_document", "firstpage"),
        ("reverse_page_document", "reversepage"),
        ("last_page_document", "lastpage"), ("sro_code", "sro"),
        ("enclosure_certificate", "encertify"),
        ("proposed_field_visit_date", "proposed_field_visit_date"),
        ("proposed_remarks", "proposed_remarks"),
        ("missing_documents", "missing_docs"), ("payment_amount", "amount"),
        ("csc_name", "csc_name"), ("igrs_form6_number", "igrs_form6no"),
        ("return_status", "rtr_str"),
        # The new extract dropped this column outright -- no counterpart.
        ("relative_mobile_number", None),
        ("permanent_address", "appl_address_perm"),
        ("mother_name", "mother_name"), ("father_name", "father_name"),
        ("gender", "gender"), ("date_of_birth", "dob"),
        ("merged_application_id", "merge_applid"),
    ],
}


_OLD2NEW = {table: dict(cols) for table, cols in RENAMED.items()}
for _t in PASSTHROUGH:
    _OLD2NEW[_t] = None   # sentinel: every column is identity


def real_table(name: str) -> str:
    """appl_log_urban_demo -> appl_log_urban. A name with no `_demo` suffix
    (already the real name) passes through unchanged."""
    return name[:-5] if name.endswith("_demo") else name


def newcol(table: str, old: str) -> str:
    """The column `old` (the name build_app_tables.py's queries use) under
    its real name in `table` (already de-`_demo`'d). Identity for a
    passthrough table or a column that was never renamed.

    Raises if `old` was dropped outright in the new extract (see
    sub_div_patta_transfer_application_information_urban's
    relative_mobile_number) -- a caller hitting that means it needs `sel()`
    instead, which can supply NULL for a dropped column; using the bare name
    directly (in a WHERE/ORDER BY, say) has nothing sensible to fall back to.
    """
    mapping = _OLD2NEW.get(table)
    if mapping is None:
        return old
    if old not in mapping:
        raise KeyError(f"{table}: no column mapping registered for {old!r}")
    new = mapping[old]
    if new is None:
        raise KeyError(f"{table}.{old} does not exist in the new extract "
                       f"-- use sel() to substitute NULL instead")
    return new


def sel(table: str, *old_cols: str) -> str:
    """A SELECT column list, `<real column> AS <old name>, ...`, so a query
    against the real table can still be read (`.mappings()`, `row["old"]`) by
    the name build_app_tables.py's Python code already expects. A column with
    no counterpart in the new extract is supplied as NULL rather than
    raising -- the query still runs, and the value the old code sees is the
    same None it would see for any other blank field.
    """
    mapping = _OLD2NEW.get(table)
    parts = []
    for old in old_cols:
        if mapping is None:
            parts.append(old)
            continue
        if old not in mapping:
            raise KeyError(f"{table}: no column mapping registered for {old!r}")
        new = mapping[old]
        if new is None:
            parts.append(f"NULL::varchar AS {old}")
        elif new == old:
            parts.append(old)
        else:
            parts.append(f"{new} AS {old}")
    return ", ".join(parts)
