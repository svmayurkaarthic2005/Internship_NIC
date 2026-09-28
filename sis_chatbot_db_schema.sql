--
-- PostgreSQL database dump
--

\restrict Lhr7JQQn8jmljLBFMCSRBZxF4oUSV56qFGi2iAmRMTnx1jzEmmNx91wcdVgyM4W

-- Dumped from database version 17.10
-- Dumped by pg_dump version 17.10

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: notify_app_tables_stale(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.notify_app_tables_stale() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM pg_notify('app_tables_stale', TG_TABLE_NAME);
    RETURN NULL;
END;
$$;


--
-- Name: touch_urban_application_log(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.touch_urban_application_log() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.last_updated_datetime IS NOT DISTINCT FROM OLD.last_updated_datetime THEN
        NEW.last_updated_datetime := now();
    END IF;
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: appl_log_urban_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.appl_log_urban_demo (
    row_id bigint NOT NULL,
    serial_number integer,
    user_id character varying(60),
    department_code character varying(60),
    service_code character varying(60),
    district_code character varying(60),
    taluk_code character varying(60),
    village_code character varying(60),
    urban_unit_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    application_date date,
    application_status character varying(200),
    last_updated_datetime timestamp with time zone,
    application_id character varying(60),
    survey_number character varying(60),
    subdivision_number character varying(60),
    patta_number character varying(60),
    role_id character varying(60),
    source_code character varying(60),
    csc_service_charge numeric(14,2),
    government_service_charge numeric(14,2),
    can_number character varying(60),
    dispatch_date date,
    received_date date,
    ip_address character varying(60),
    generated_datetime timestamp with time zone,
    source_name character varying(60),
    renewal_number character varying(60),
    workflow_state character varying(60),
    igrs_form6_number character varying(60),
    return_status character varying(60),
    current_subdivision_number character varying(60),
    parent_application_id character varying(60),
    auto_mutated_flag character varying(60),
    is_auto_mutated character varying(60),
    igrs_auto_mutation_flag character varying(60),
    camp_flag character varying(60),
    camp_correction_id character varying(60),
    camp_code character varying(60)
);


--
-- Name: applicants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.applicants (
    id uuid NOT NULL,
    name character varying(200) NOT NULL,
    mobile character varying(15),
    email character varying(200),
    aadhaar_last4 character(4),
    address text,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    permanent_address text,
    father_name character varying(200),
    mother_name character varying(200),
    date_of_birth date,
    gender character varying(10),
    occupation character varying(100)
);


--
-- Name: application_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.application_documents (
    id uuid NOT NULL,
    application_id uuid NOT NULL,
    document_type character varying(100) NOT NULL,
    document_name character varying(200),
    is_uploaded boolean,
    is_verified boolean,
    uploaded_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: application_sub_division_owners; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.application_sub_division_owners (
    id uuid NOT NULL,
    application_sub_division_id uuid NOT NULL,
    owner_no integer,
    name character varying(200),
    name_tamil character varying(200),
    relationship_type character varying(50),
    relative_name character varying(200),
    ownership_share character varying(20),
    aadhaar_last4 character varying(4),
    gender character varying(10),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: application_sub_divisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.application_sub_divisions (
    id uuid NOT NULL,
    application_id uuid NOT NULL,
    sub_division_id uuid NOT NULL,
    proposed_area_sqm numeric(12,2),
    proposed_sub_division_no character varying(50),
    status character varying(30),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    temporary_sub_division_no character varying(50)
);


--
-- Name: application_workflow_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.application_workflow_demo (
    row_id bigint NOT NULL,
    serial_number integer,
    application_id character varying(60),
    district_code character varying(60),
    taluk_code character varying(60),
    village_code character varying(60),
    action_from_role_id character varying(60),
    action_to_role_id character varying(60),
    action_date date,
    remarks text,
    action_status character varying(60),
    last_updated_datetime timestamp with time zone,
    updated_by_user character varying(60),
    workflow_state character varying(60),
    recommendation_status character varying(60),
    field_visit_date date,
    received_flag character varying(60),
    annual_income numeric(14,2),
    review_flag character varying(60),
    ip_address character varying(60),
    auto_recommendation_flag character varying(60),
    auto_recommendation_remarks text
);


--
-- Name: application_workflow_action_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.application_workflow_action_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: application_workflow_action_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.application_workflow_action_row_id_seq OWNED BY public.application_workflow_demo.row_id;


--
-- Name: applications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.applications (
    id uuid NOT NULL,
    application_number character varying(30) NOT NULL,
    application_type character varying(10) NOT NULL,
    applicant_id uuid NOT NULL,
    survey_number_id uuid NOT NULL,
    assigned_officer_id uuid NOT NULL,
    submission_channel character varying(20),
    submission_date date NOT NULL,
    sale_deed_number character varying(100),
    sale_deed_registered boolean,
    declared_reason character varying(100),
    can_number character varying(50),
    current_stage character varying(30) NOT NULL,
    current_status character varying(30) NOT NULL,
    field_visit_date date,
    field_visit_scheduled boolean,
    is_overdue boolean,
    priority_flag boolean,
    notes text,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    fee_amount numeric(10,2),
    challan_number character varying(50),
    payment_mode character varying(20),
    igrs_form6_number character varying(30),
    merged_application_id character varying(30),
    submission_source_name character varying(100),
    submission_camp_flag character varying(5),
    submission_ip character varying(50),
    CONSTRAINT ck_application_type CHECK (((application_type)::text = ANY ((ARRAY['ISD'::character varying, 'NISD'::character varying, 'MERGE'::character varying])::text[]))),
    CONSTRAINT ck_current_stage CHECK (((current_stage)::text = ANY ((ARRAY['SIS'::character varying, 'SD'::character varying, 'DIS'::character varying, 'TAHSILDAR'::character varying, 'COMPLETED'::character varying, 'REJECTED'::character varying])::text[]))),
    CONSTRAINT ck_current_status CHECK (((current_status)::text = ANY ((ARRAY['pending'::character varying, 'in_progress'::character varying, 'approved'::character varying, 'rejected'::character varying, 'escalated'::character varying])::text[])))
);


--
-- Name: areg_temp_subdivclub_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.areg_temp_subdivclub_demo (
    row_id bigint NOT NULL,
    application_id character varying(60),
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    survey_number character varying(60),
    temporary_subdivision_number character varying(60),
    new_subdivision_number character varying(60),
    existing_patta_number character varying(60),
    area_hectare numeric(14,2),
    area_ares numeric(14,2),
    area_square_meter numeric(14,2),
    new_patta_waste character varying(60),
    last_updated_datetime timestamp with time zone,
    submitted_datetime timestamp with time zone,
    land_type character varying(60),
    government_private_flag character varying(60),
    tax_rate numeric(14,2),
    tax_per_hectare numeric(14,2),
    primary_soil_type character varying(60),
    secondary_soil_type character varying(60),
    adopted_area_hectare numeric(14,2),
    adopted_area_ares numeric(14,2),
    status character varying(60),
    adopted_area_square_meter numeric(14,2),
    surveyor_adopted_area_hectare numeric(14,2),
    surveyor_adopted_area_ares numeric(14,2),
    surveyor_adopted_area_square_meter numeric(14,2),
    district_adopted_area_hectare numeric(14,2),
    district_adopted_area_ares numeric(14,2),
    district_adopted_area_square_meter numeric(14,2),
    new_patta_number character varying(60),
    group_survey_number character varying(60),
    group_subdivision_number character varying(60),
    group_temporary_subdivision_number character varying(60),
    group_patta_number character varying(60),
    plot_type character varying(60),
    plot_number character varying(60),
    land_use character varying(200),
    land_use_description character varying(200)
);


--
-- Name: attachment_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.attachment_chunks (
    id uuid NOT NULL,
    document_id uuid NOT NULL,
    officer_id uuid NOT NULL,
    session_id uuid NOT NULL,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    content_hash character varying(64) NOT NULL,
    char_count integer NOT NULL,
    location jsonb NOT NULL,
    citation text NOT NULL,
    embedding public.vector(768),
    created_at timestamp with time zone NOT NULL
);


--
-- Name: attachment_rows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.attachment_rows (
    id uuid NOT NULL,
    document_id uuid NOT NULL,
    officer_id uuid NOT NULL,
    session_id uuid NOT NULL,
    row_number integer NOT NULL,
    data jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    id uuid NOT NULL,
    officer_id uuid,
    officer_employee_id character varying(20),
    action character varying(200),
    entity_type character varying(50),
    entity_id uuid,
    old_values jsonb,
    new_values jsonb,
    ip_address character varying(50),
    user_agent text,
    created_at timestamp with time zone
);


--
-- Name: block; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.block (
    district_code character(2) NOT NULL,
    taluk_code character(2) NOT NULL,
    town_code character(3) NOT NULL,
    ward_code character(3) NOT NULL,
    block_code character(4) NOT NULL,
    block_name character varying(50),
    block_ename character varying(50),
    updn_dt timestamp without time zone,
    user_no integer,
    old_taluk_code character(2),
    old_town_code character(3),
    old_block_code character(4)
);


--
-- Name: blocks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.blocks (
    id uuid NOT NULL,
    ward_id uuid NOT NULL,
    block_number character varying(20) NOT NULL,
    block_name character varying(100),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: chat_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_attachments (
    id uuid NOT NULL,
    officer_id uuid NOT NULL,
    session_id uuid NOT NULL,
    filename text NOT NULL,
    stored_name text,
    file_ext character varying(16) NOT NULL,
    mime_type character varying(120),
    byte_size integer NOT NULL,
    content_hash character varying(64) NOT NULL,
    extraction_status character varying(32) NOT NULL,
    status_detail text,
    char_count integer,
    page_count integer,
    chunk_count integer,
    csv_headers jsonb,
    csv_row_count integer,
    is_active boolean NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_attachment_extraction_status CHECK (((extraction_status)::text = ANY ((ARRAY['ok'::character varying, 'no_extractable_text'::character varying, 'failed'::character varying])::text[])))
);


--
-- Name: chat_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_messages (
    id uuid NOT NULL,
    session_id uuid NOT NULL,
    role character varying(10) NOT NULL,
    content text NOT NULL,
    detected_language character varying(10),
    retrieved_context jsonb,
    structured_data jsonb,
    response_time_ms integer,
    created_at timestamp with time zone,
    CONSTRAINT ck_message_role CHECK (((role)::text = ANY ((ARRAY['user'::character varying, 'assistant'::character varying])::text[])))
);


--
-- Name: chat_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_sessions (
    id uuid NOT NULL,
    officer_id uuid NOT NULL,
    session_token character varying(100) NOT NULL,
    started_at timestamp with time zone,
    last_activity timestamp with time zone,
    is_active boolean,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: chitta_temp_subdivclub_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chitta_temp_subdivclub_demo (
    row_id bigint NOT NULL,
    application_id character varying(60),
    owner_name_tamil character varying(200),
    relationship character varying(200),
    relative_name_tamil character varying(200),
    extent_value_1 numeric(14,2),
    extent_value_2 numeric(14,2),
    extent_value_3 numeric(14,2),
    patta_number character varying(60),
    relation_code character varying(60),
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    survey_number character varying(60),
    temporary_subdivision_number character varying(60),
    new_subdivision_number character varying(60),
    existing_patta_number character varying(60),
    owner_no integer,
    status character varying(60),
    owner_name_english character varying(200),
    relative_name_english character varying(200),
    ownership_share character varying(60),
    aadhaar_number character varying(60),
    gender character varying(60)
);


--
-- Name: district_unicode; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.district_unicode (
    state_code character varying(2),
    district_code character varying(255) NOT NULL,
    district_tname character varying(255),
    district_name character varying(255),
    district_sname character varying(3),
    taluk_district_code character varying(255),
    app_uid uuid NOT NULL
);


--
-- Name: field_visits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.field_visits (
    id uuid NOT NULL,
    application_id uuid NOT NULL,
    officer_id uuid NOT NULL,
    scheduled_date date,
    actual_date date,
    status character varying(20),
    visit_notes text,
    encroachment_found boolean,
    encroachment_notes text,
    area_verified boolean,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_visit_status CHECK (((status)::text = ANY ((ARRAY['unscheduled'::character varying, 'scheduled'::character varying, 'completed'::character varying, 'overdue'::character varying, 'rescheduled'::character varying, 'cancelled'::character varying])::text[])))
);


--
-- Name: full_field_patta_transfer_application_information_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.full_field_patta_transfer_application_information_demo (
    row_id bigint NOT NULL,
    application_id character varying(60),
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    can_number character varying(60),
    applicant_name character varying(200),
    current_address text,
    mobile_number character varying(60),
    application_status character varying(200),
    remarks text,
    last_updated_datetime timestamp with time zone,
    permanent_address text,
    mother_name character varying(60),
    father_name character varying(60),
    date_of_birth date,
    gender character varying(60),
    occupation character varying(200),
    enclosure_details text,
    barcode_flag character varying(60),
    first_page_document text,
    reverse_page_document text,
    last_page_document text,
    enclosure_certificate character varying(60),
    proposed_field_visit_date date,
    proposed_remarks character varying(200),
    missing_documents text,
    physical_verification_status character varying(200),
    document_sent_date date,
    document_received_date date,
    challan_number character varying(60),
    challan_date date,
    treasury_name character varying(200),
    bank_name character varying(200),
    bank_branch character varying(200),
    payment_mode character varying(60),
    payment_amount numeric(14,2),
    igrs_form6_number character varying(60),
    return_status character varying(60),
    owner_correction_reason character varying(200),
    merged_application_id character varying(60),
    auto_mutated_flag character varying(60)
);


--
-- Name: full_field_patta_transfer_igrs_owner_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.full_field_patta_transfer_igrs_owner_demo (
    row_id bigint NOT NULL,
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    door_number character varying(60),
    patta_number character varying(60),
    occupation_code character varying(60),
    owner_no integer,
    owner_name_tamil character varying(200),
    owner_name_english character varying(200),
    relation_no integer,
    relative_name_tamil character varying(200),
    relative_name_english character varying(200),
    relation_code character varying(60),
    ownership_share character varying(60),
    address text,
    citizen_identification_number character varying(60),
    form6_number character varying(60),
    assignment_number character varying(60),
    ration_card_number character varying(60),
    voter_id_number character varying(60),
    pin_code character varying(60),
    last_updated_datetime timestamp with time zone,
    user_number character varying(60),
    aadhaar_number character varying(60),
    gender character varying(60),
    igrs_form6_number character varying(60),
    mobile_number character varying(60),
    application_id character varying(60),
    owner_image text
);


--
-- Name: full_field_patta_transfer_new_owner_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.full_field_patta_transfer_new_owner_demo (
    row_id bigint NOT NULL,
    application_id character varying(60),
    owner_name_tamil character varying(200),
    owner_name_english character varying(200),
    relationship character varying(200),
    relative_name_tamil character varying(200),
    extent character varying(60),
    patta_number character varying(60),
    relation_code character varying(60),
    owner_no integer,
    owner_status character varying(60),
    uds_details text,
    owner_photo text,
    relative_name_english character varying(200),
    ownership_share character varying(60),
    aadhaar_number character varying(60),
    gender character varying(60),
    relation_no integer,
    block_code character varying(60),
    survey_number character varying(60),
    subdivision_number character varying(60),
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    owner_image text,
    mobile_number character varying(60)
);


--
-- Name: full_field_patta_transfer_old_owner_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.full_field_patta_transfer_old_owner_demo (
    row_id bigint NOT NULL,
    application_id character varying(60),
    owner_name_tamil character varying(200),
    owner_name_english character varying(200),
    relationship character varying(200),
    relative_name_tamil character varying(200),
    extent character varying(60),
    patta_number character varying(60),
    relation_code character varying(60),
    owner_no integer,
    owner_status character varying(60),
    uds_details text,
    owner_photo text,
    relative_name_english character varying(200),
    ownership_share character varying(60),
    aadhaar_number character varying(60),
    gender character varying(60),
    relation_no integer,
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60)
);


--
-- Name: full_field_patta_transfer_return_owner_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.full_field_patta_transfer_return_owner_demo (
    row_id bigint NOT NULL,
    application_id character varying(60),
    owner_name_tamil character varying(200),
    owner_name_english character varying(200),
    relationship character varying(200),
    relative_name_tamil character varying(200),
    extent character varying(60),
    patta_number character varying(60),
    relation_code character varying(60),
    owner_no integer,
    owner_status character varying(60),
    uds_details text,
    owner_photo text,
    relative_name_english character varying(200),
    ownership_share character varying(60),
    aadhaar_number character varying(60),
    gender character varying(60),
    relation_no integer,
    return_status character varying(60),
    last_updated_datetime timestamp with time zone,
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    owner_image text,
    mobile_number character varying(60)
);


--
-- Name: full_field_patta_transfer_urban_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.full_field_patta_transfer_urban_demo (
    row_id bigint NOT NULL,
    slno integer,
    sno integer,
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    application_id character varying(60),
    survey_number character varying(60),
    subdivision_number character varying(60),
    old_patta_number character varying(60),
    land_type_code character varying(60),
    extent_value_1 numeric(14,2),
    extent_value_2 numeric(14,2),
    extent_value_3 numeric(14,2),
    registration_document_number character varying(60),
    transfer_reason character varying(200),
    registration_place character varying(200),
    registration_date date,
    generated_patta_number character varying(60),
    transaction_status character varying(60),
    order_number character varying(60),
    order_date date,
    order_remarks text,
    last_updated_datetime timestamp with time zone,
    submitted_datetime timestamp with time zone,
    extent_in_ares numeric(14,2),
    tax_rate numeric(14,2),
    succession_certificate_number character varying(60),
    succession_certificate_date date,
    succession_certificate_issued_by character varying(200),
    legal_heir_certificate_issue_place character varying(200),
    court_order_number character varying(60),
    court_order_date date,
    court_name character varying(200),
    street_code character varying(60),
    purchased_area numeric(14,2),
    purchased_area_unit character varying(60),
    transaction_code character varying(60),
    old_street_code character varying(60),
    sis_recommendation character varying(60),
    sis_remarks text,
    sis_recommendation_reason character varying(200),
    new_door_number character varying(60),
    new_total_tax numeric(14,2),
    old_door_number character varying(60),
    old_total_tax numeric(14,2),
    group_survey_number character varying(60),
    group_subdivision_number character varying(60),
    group_patta_number character varying(60),
    owner_correction_reason character varying(200),
    transfer_type character varying(60),
    direct_transfer_flag character varying(60),
    land_category_code character varying(60)
);


--
-- Name: sub_div_patta_transfer_application_information_urban_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sub_div_patta_transfer_application_information_urban_demo (
    row_id bigint NOT NULL,
    application_id character varying(60),
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    can_number character varying(60),
    applicant_name character varying(200),
    current_address text,
    mobile_number character varying(60),
    application_status character varying(200),
    remarks text,
    last_updated_datetime timestamp with time zone,
    application_status_code character varying(60),
    rejection_reason_code character varying(200),
    enclosure_details text,
    challan_number character varying(60),
    challan_date date,
    treasury_name character varying(200),
    bank_name character varying(200),
    bank_branch character varying(200),
    payment_mode character varying(60),
    first_page_document text,
    reverse_page_document text,
    last_page_document text,
    sro_code character varying(60),
    enclosure_certificate character varying(60),
    proposed_field_visit_date date,
    proposed_remarks character varying(200),
    missing_documents text,
    payment_amount numeric(14,2),
    csc_name character varying(200),
    igrs_form6_number character varying(60),
    return_status character varying(60),
    relative_mobile_number character varying(60),
    permanent_address text,
    mother_name character varying(60),
    father_name character varying(60),
    gender character varying(60),
    date_of_birth date,
    merged_application_id character varying(60)
);


--
-- Name: isd_transfer_application_info_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.isd_transfer_application_info_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: isd_transfer_application_info_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.isd_transfer_application_info_row_id_seq OWNED BY public.sub_div_patta_transfer_application_information_urban_demo.row_id;


--
-- Name: sub_div_patta_transfer_urban_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sub_div_patta_transfer_urban_demo (
    row_id bigint NOT NULL,
    slno integer,
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    application_id character varying(60),
    survey_number character varying(60),
    subdivision_number character varying(60),
    old_patta_number character varying(60),
    land_type_code character varying(60),
    extent_value_1 numeric(14,2),
    extent_value_2 numeric(14,2),
    extent_value_3 numeric(14,2),
    registration_document_number character varying(60),
    transfer_reason character varying(200),
    registration_date date,
    registration_place character varying(200),
    submission_date timestamp with time zone,
    government_private_flag character varying(60),
    transaction_status character varying(60),
    last_updated_datetime timestamp with time zone,
    submitted_datetime timestamp with time zone,
    form8a_number character varying(60),
    receipt_date date,
    tahsildar_receipt_date date,
    purchased_area numeric(14,2),
    surveyor_received_date date,
    surveyor_completed_date date,
    court_order_number character varying(60),
    court_order_date date,
    court_name character varying(200),
    purchased_area_unit character varying(60),
    sketch_sent_date date,
    sketch_received_date date,
    incorporation_date date,
    handover_received_date date,
    commissioner_change_date date,
    street_code character varying(60),
    old_street_code character varying(60),
    sis_recommendation character varying(60),
    sis_remarks text,
    sis_recommendation_reason character varying(200),
    surveyor_recommendation character varying(60),
    surveyor_remarks text,
    surveyor_recommendation_reason character varying(200),
    district_recommendation character varying(60),
    district_remarks text,
    district_recommendation_reason character varying(200),
    tahsildar_recommendation character varying(60),
    tahsildar_remarks text,
    tahsildar_recommendation_reason character varying(200),
    land_category_code character varying(60),
    old_door_number character varying(60),
    new_door_number character varying(60),
    payment_amount numeric(14,2),
    generated_patta_number character varying(60),
    total_subdivisions integer
);


--
-- Name: isd_transfer_urban_detail_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.isd_transfer_urban_detail_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: isd_transfer_urban_detail_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.isd_transfer_urban_detail_row_id_seq OWNED BY public.sub_div_patta_transfer_urban_demo.row_id;


--
-- Name: knowledge_embeddings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.knowledge_embeddings (
    id uuid NOT NULL,
    chunk_id text NOT NULL,
    content text NOT NULL,
    embedding public.vector(768) NOT NULL,
    source text,
    category text,
    section text,
    language text,
    page integer,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: nisd_transfer_application_info_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.nisd_transfer_application_info_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: nisd_transfer_application_info_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.nisd_transfer_application_info_row_id_seq OWNED BY public.full_field_patta_transfer_application_information_demo.row_id;


--
-- Name: nisd_transfer_igrs_owner_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.nisd_transfer_igrs_owner_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: nisd_transfer_igrs_owner_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.nisd_transfer_igrs_owner_row_id_seq OWNED BY public.full_field_patta_transfer_igrs_owner_demo.row_id;


--
-- Name: nisd_transfer_new_owner_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.nisd_transfer_new_owner_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: nisd_transfer_new_owner_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.nisd_transfer_new_owner_row_id_seq OWNED BY public.full_field_patta_transfer_new_owner_demo.row_id;


--
-- Name: nisd_transfer_old_owner_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.nisd_transfer_old_owner_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: nisd_transfer_old_owner_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.nisd_transfer_old_owner_row_id_seq OWNED BY public.full_field_patta_transfer_old_owner_demo.row_id;


--
-- Name: nisd_transfer_return_owner_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.nisd_transfer_return_owner_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: nisd_transfer_return_owner_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.nisd_transfer_return_owner_row_id_seq OWNED BY public.full_field_patta_transfer_return_owner_demo.row_id;


--
-- Name: nisd_transfer_urban_detail_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.nisd_transfer_urban_detail_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: nisd_transfer_urban_detail_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.nisd_transfer_urban_detail_row_id_seq OWNED BY public.full_field_patta_transfer_urban_demo.row_id;


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notifications (
    id uuid NOT NULL,
    officer_id uuid NOT NULL,
    application_id uuid,
    title character varying(200),
    message text,
    is_read boolean,
    notification_type character varying(50),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: officer_jurisdictions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.officer_jurisdictions (
    id uuid NOT NULL,
    officer_id uuid NOT NULL,
    jurisdiction_type character varying(20) NOT NULL,
    district_id uuid,
    taluk_id uuid,
    town_id uuid,
    ward_id uuid,
    block_id uuid,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_jurisdiction_not_empty CHECK (((district_id IS NOT NULL) OR (taluk_id IS NOT NULL) OR (town_id IS NOT NULL) OR (ward_id IS NOT NULL) OR (block_id IS NOT NULL))),
    CONSTRAINT ck_jurisdiction_type CHECK (((jurisdiction_type)::text = ANY ((ARRAY['district'::character varying, 'taluk'::character varying, 'town'::character varying, 'ward'::character varying, 'block'::character varying])::text[])))
);


--
-- Name: owners; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.owners (
    id uuid NOT NULL,
    name character varying(200) NOT NULL,
    name_tamil character varying(200),
    father_name character varying(200),
    aadhaar_last4 character(4),
    mobile character varying(15),
    address text,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    gender character varying(10),
    relationship_type character varying(50)
);


--
-- Name: patta_transfers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.patta_transfers (
    id uuid NOT NULL,
    application_id uuid NOT NULL,
    survey_number_id uuid NOT NULL,
    sub_division_id uuid,
    previous_owner_id uuid NOT NULL,
    new_owner_id uuid NOT NULL,
    transfer_order_number character varying(100),
    transfer_date date,
    tahsildar_signature_date date,
    dsc_applied boolean,
    status character varying(30),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    new_patta_number character varying(50),
    signed_by character varying(50),
    transfer_reason character varying(120),
    transfer_type character varying(60),
    registration_place character varying(200),
    registration_date date,
    old_patta_number character varying(50),
    order_number character varying(100),
    order_date date,
    order_remarks text,
    sis_recommendation character varying(10),
    sis_remarks text,
    sis_recommendation_reason text
);


--
-- Name: sis_officers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sis_officers (
    id uuid NOT NULL,
    employee_id character varying(20) NOT NULL,
    name character varying(200) NOT NULL,
    name_tamil character varying(200),
    email character varying(200) NOT NULL,
    password_hash character varying(255) NOT NULL,
    mobile character varying(15),
    designation character varying(100),
    is_active boolean,
    last_login timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: sub_divisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sub_divisions (
    id uuid NOT NULL,
    survey_number_id uuid NOT NULL,
    sub_division_no character varying(50) NOT NULL,
    area_sqm numeric(12,2) NOT NULL,
    status character varying(30),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: survey_numbers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.survey_numbers (
    id uuid NOT NULL,
    block_id uuid NOT NULL,
    survey_no character varying(50) NOT NULL,
    total_area_sqm numeric(12,2) NOT NULL,
    land_type character varying(50),
    patta_number character varying(50),
    has_encroachment boolean,
    has_litigation boolean,
    litigation_reference character varying(200),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: survey_ownership; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.survey_ownership (
    id uuid NOT NULL,
    survey_number_id uuid NOT NULL,
    sub_division_id uuid,
    owner_id uuid NOT NULL,
    ownership_share numeric(5,2),
    is_joint_owner boolean,
    ownership_type character varying(50),
    effective_from date,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: taluk; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.taluk (
    district_code character varying(255) NOT NULL,
    taluk_code character varying(255) NOT NULL,
    taluk_name character varying(255),
    taluk_ename character varying(255),
    updn_dt timestamp without time zone,
    user_no integer,
    id bigint NOT NULL,
    to_code character varying(10),
    ddo_code character varying(10),
    app_uid uuid NOT NULL,
    district_uid uuid
);


--
-- Name: taluk_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.taluk_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: taluk_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.taluk_id_seq OWNED BY public.taluk.id;


--
-- Name: town; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.town (
    district_code character varying(2) NOT NULL,
    taluk_code character varying(2) NOT NULL,
    town_code character varying(3) NOT NULL,
    town_name character varying(50),
    town_ename character varying(50),
    town_flag character varying(1),
    user_no integer,
    updn_dt timestamp without time zone,
    igrs_mutation character(1),
    igrs_am_date date,
    town_status character varying(1),
    nic_dsign character(1)
);


--
-- Name: towns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.towns (
    id uuid NOT NULL,
    taluk_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    town_code character varying(10) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: uareg_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.uareg_demo (
    row_id bigint NOT NULL,
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    street_code character varying(60),
    survey_number character varying(60),
    subdivision_number character varying(60),
    old_subdivision_number character varying(60),
    old_survey_number character varying(60),
    double_crop_flag character varying(60),
    partition_indicator character varying(60),
    door_number character varying(60),
    government_priority_flag character varying(60),
    land_type_code character varying(60),
    irrigation_source_code character varying(60),
    tax_rate_code character varying(60),
    primary_soil_type character varying(60),
    secondary_soil_type character varying(60),
    soil_class_code character varying(60),
    tax_per_hectare numeric(14,2),
    extent_unit character varying(60),
    extent_value_1 numeric(14,2),
    extent_value_2 numeric(14,2),
    extent_value_3 numeric(14,2),
    total_tax numeric(14,2),
    patta_number character varying(60),
    coss_block_code character varying(60),
    land_use_code character varying(60),
    remarks text,
    remarks1 text,
    new_patta_number character varying(60),
    old_patta_number character varying(60),
    form6_number character varying(60),
    form7_number character varying(60),
    form8_number character varying(60),
    relinquishment_number character varying(60),
    assignment_number character varying(60),
    alienation_number character varying(60),
    acquisition_number character varying(60),
    assessed_flag character varying(60),
    cultivable_flag character varying(60),
    last_updated_datetime timestamp with time zone,
    user_number character varying(60),
    descriptive_remark_code character varying(60),
    descriptive_land_use text,
    municipal_tax numeric(14,2),
    new_door_number character varying(60)
);


--
-- Name: uaregmap_ds_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.uaregmap_ds_demo (
    row_id bigint NOT NULL,
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    survey_number character varying(60),
    subdivision_number character varying(60),
    form6_number character varying(60),
    form8_number character varying(60),
    patta_number character varying(60),
    document_hash text,
    digital_signature_content text,
    signed_datetime timestamp with time zone,
    username character varying(60),
    role_id character varying(60),
    dateofverify timestamp with time zone,
    username_verify character varying(60),
    nic_dsign_flag character varying(60)
);


--
-- Name: uchitta_natham_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.uchitta_natham_demo (
    row_id bigint NOT NULL,
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    door_number character varying(60),
    patta_number character varying(60),
    occupation_code character varying(60),
    own_num integer,
    owner_name_tamil character varying(200),
    owner_name_english character varying(200),
    rel_num integer,
    relative_name_tamil character varying(200),
    relative_name_english character varying(200),
    relationship_code character varying(60),
    ownership_share character varying(60),
    address text,
    cin_no character varying(60),
    form6_number character varying(60),
    assignment_number character varying(60),
    ration_card_number character varying(60),
    epic_no character varying(60),
    pin_code character varying(60),
    last_updated_datetime timestamp with time zone,
    user_no character varying(60),
    aadhaar_number character varying(60),
    sex character varying(60)
);


--
-- Name: uchitta_nathammap_ds_demo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.uchitta_nathammap_ds_demo (
    row_id bigint NOT NULL,
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    patta_number character varying(60),
    form6_number character varying(60),
    form8_number character varying(60),
    document_hash text,
    signature_content text,
    signed_datetime timestamp with time zone,
    signed_by_username character varying(60),
    verified_datetime timestamp with time zone,
    username_verify character varying(60),
    nic_digital_signature text
);


--
-- Name: urban_application_log_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.urban_application_log_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: urban_application_log_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.urban_application_log_row_id_seq OWNED BY public.appl_log_urban_demo.row_id;


--
-- Name: urban_natham_chitta_owner_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.urban_natham_chitta_owner_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: urban_natham_chitta_owner_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.urban_natham_chitta_owner_row_id_seq OWNED BY public.uchitta_natham_demo.row_id;


--
-- Name: urban_natham_chitta_signature_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.urban_natham_chitta_signature_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: urban_natham_chitta_signature_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.urban_natham_chitta_signature_row_id_seq OWNED BY public.uchitta_nathammap_ds_demo.row_id;


--
-- Name: urban_parcel_register_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.urban_parcel_register_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: urban_parcel_register_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.urban_parcel_register_row_id_seq OWNED BY public.uareg_demo.row_id;


--
-- Name: urban_parcel_signature_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.urban_parcel_signature_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: urban_parcel_signature_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.urban_parcel_signature_row_id_seq OWNED BY public.uaregmap_ds_demo.row_id;


--
-- Name: urban_temp_subdivision_owner_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.urban_temp_subdivision_owner_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: urban_temp_subdivision_owner_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.urban_temp_subdivision_owner_row_id_seq OWNED BY public.chitta_temp_subdivclub_demo.row_id;


--
-- Name: urban_temp_subdivision_parcel_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.urban_temp_subdivision_parcel_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: urban_temp_subdivision_parcel_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.urban_temp_subdivision_parcel_row_id_seq OWNED BY public.areg_temp_subdivclub_demo.row_id;


--
-- Name: ward; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ward (
    district_code character varying(2) NOT NULL,
    taluk_code character varying(2) NOT NULL,
    town_code character varying(3) NOT NULL,
    ward_code character varying(3) NOT NULL,
    ward_name character varying(150),
    ward_ename character varying(150),
    updn_dt timestamp with time zone,
    user_no integer,
    freeze_flag character varying(1),
    freeze_start_date timestamp with time zone,
    freeze_end_date timestamp with time zone,
    settlement_status character varying(1),
    next_patta_no integer DEFAULT 0 NOT NULL,
    settlement_flag boolean
);


--
-- Name: wards; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wards (
    id uuid NOT NULL,
    town_id uuid NOT NULL,
    ward_number character varying(20) NOT NULL,
    ward_name character varying(100),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: workflow_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow_history (
    id uuid NOT NULL,
    application_id uuid NOT NULL,
    from_stage character varying(30),
    to_stage character varying(30),
    action character varying(100),
    performed_by_officer_id uuid,
    remarks text,
    rejection_reason text,
    performed_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: appl_log_urban_demo row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.appl_log_urban_demo ALTER COLUMN row_id SET DEFAULT nextval('public.urban_application_log_row_id_seq'::regclass);


--
-- Name: application_workflow_demo row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.application_workflow_demo ALTER COLUMN row_id SET DEFAULT nextval('public.application_workflow_action_row_id_seq'::regclass);


--
-- Name: areg_temp_subdivclub_demo row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.areg_temp_subdivclub_demo ALTER COLUMN row_id SET DEFAULT nextval('public.urban_temp_subdivision_parcel_row_id_seq'::regclass);


--
-- Name: chitta_temp_subdivclub_demo row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chitta_temp_subdivclub_demo ALTER COLUMN row_id SET DEFAULT nextval('public.urban_temp_subdivision_owner_row_id_seq'::regclass);


--
-- Name: full_field_patta_transfer_application_information_demo row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_application_information_demo ALTER COLUMN row_id SET DEFAULT nextval('public.nisd_transfer_application_info_row_id_seq'::regclass);


--
-- Name: full_field_patta_transfer_igrs_owner_demo row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_igrs_owner_demo ALTER COLUMN row_id SET DEFAULT nextval('public.nisd_transfer_igrs_owner_row_id_seq'::regclass);


--
-- Name: full_field_patta_transfer_new_owner_demo row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_new_owner_demo ALTER COLUMN row_id SET DEFAULT nextval('public.nisd_transfer_new_owner_row_id_seq'::regclass);


--
-- Name: full_field_patta_transfer_old_owner_demo row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_old_owner_demo ALTER COLUMN row_id SET DEFAULT nextval('public.nisd_transfer_old_owner_row_id_seq'::regclass);


--
-- Name: full_field_patta_transfer_return_owner_demo row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_return_owner_demo ALTER COLUMN row_id SET DEFAULT nextval('public.nisd_transfer_return_owner_row_id_seq'::regclass);


--
-- Name: full_field_patta_transfer_urban_demo row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_urban_demo ALTER COLUMN row_id SET DEFAULT nextval('public.nisd_transfer_urban_detail_row_id_seq'::regclass);


--
-- Name: sub_div_patta_transfer_application_information_urban_demo row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sub_div_patta_transfer_application_information_urban_demo ALTER COLUMN row_id SET DEFAULT nextval('public.isd_transfer_application_info_row_id_seq'::regclass);


--
-- Name: sub_div_patta_transfer_urban_demo row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sub_div_patta_transfer_urban_demo ALTER COLUMN row_id SET DEFAULT nextval('public.isd_transfer_urban_detail_row_id_seq'::regclass);


--
-- Name: taluk id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.taluk ALTER COLUMN id SET DEFAULT nextval('public.taluk_id_seq'::regclass);


--
-- Name: uareg_demo row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uareg_demo ALTER COLUMN row_id SET DEFAULT nextval('public.urban_parcel_register_row_id_seq'::regclass);


--
-- Name: uaregmap_ds_demo row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uaregmap_ds_demo ALTER COLUMN row_id SET DEFAULT nextval('public.urban_parcel_signature_row_id_seq'::regclass);


--
-- Name: uchitta_natham_demo row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uchitta_natham_demo ALTER COLUMN row_id SET DEFAULT nextval('public.urban_natham_chitta_owner_row_id_seq'::regclass);


--
-- Name: uchitta_nathammap_ds_demo row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uchitta_nathammap_ds_demo ALTER COLUMN row_id SET DEFAULT nextval('public.urban_natham_chitta_signature_row_id_seq'::regclass);


--
-- Name: applicants applicants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.applicants
    ADD CONSTRAINT applicants_pkey PRIMARY KEY (id);


--
-- Name: application_documents application_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.application_documents
    ADD CONSTRAINT application_documents_pkey PRIMARY KEY (id);


--
-- Name: application_sub_division_owners application_sub_division_owners_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.application_sub_division_owners
    ADD CONSTRAINT application_sub_division_owners_pkey PRIMARY KEY (id);


--
-- Name: application_sub_divisions application_sub_divisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.application_sub_divisions
    ADD CONSTRAINT application_sub_divisions_pkey PRIMARY KEY (id);


--
-- Name: application_workflow_demo application_workflow_action_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.application_workflow_demo
    ADD CONSTRAINT application_workflow_action_pkey PRIMARY KEY (row_id);


--
-- Name: applications applications_application_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.applications
    ADD CONSTRAINT applications_application_number_key UNIQUE (application_number);


--
-- Name: applications applications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.applications
    ADD CONSTRAINT applications_pkey PRIMARY KEY (id);


--
-- Name: attachment_chunks attachment_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachment_chunks
    ADD CONSTRAINT attachment_chunks_pkey PRIMARY KEY (id);


--
-- Name: attachment_rows attachment_rows_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachment_rows
    ADD CONSTRAINT attachment_rows_pkey PRIMARY KEY (id);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id);


--
-- Name: block block_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.block
    ADD CONSTRAINT block_pkey PRIMARY KEY (district_code, taluk_code, town_code, ward_code, block_code);


--
-- Name: blocks blocks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blocks
    ADD CONSTRAINT blocks_pkey PRIMARY KEY (id);


--
-- Name: chat_attachments chat_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_attachments
    ADD CONSTRAINT chat_attachments_pkey PRIMARY KEY (id);


--
-- Name: chat_messages chat_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_messages
    ADD CONSTRAINT chat_messages_pkey PRIMARY KEY (id);


--
-- Name: chat_sessions chat_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_sessions
    ADD CONSTRAINT chat_sessions_pkey PRIMARY KEY (id);


--
-- Name: chat_sessions chat_sessions_session_token_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_sessions
    ADD CONSTRAINT chat_sessions_session_token_key UNIQUE (session_token);


--
-- Name: district_unicode district_unicode_app_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.district_unicode
    ADD CONSTRAINT district_unicode_app_uid_key UNIQUE (app_uid);


--
-- Name: district_unicode district_unicode_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.district_unicode
    ADD CONSTRAINT district_unicode_pkey PRIMARY KEY (district_code);


--
-- Name: field_visits field_visits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.field_visits
    ADD CONSTRAINT field_visits_pkey PRIMARY KEY (id);


--
-- Name: sub_div_patta_transfer_application_information_urban_demo isd_transfer_application_info_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sub_div_patta_transfer_application_information_urban_demo
    ADD CONSTRAINT isd_transfer_application_info_pkey PRIMARY KEY (row_id);


--
-- Name: sub_div_patta_transfer_urban_demo isd_transfer_urban_detail_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sub_div_patta_transfer_urban_demo
    ADD CONSTRAINT isd_transfer_urban_detail_pkey PRIMARY KEY (row_id);


--
-- Name: knowledge_embeddings knowledge_embeddings_chunk_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_embeddings
    ADD CONSTRAINT knowledge_embeddings_chunk_id_key UNIQUE (chunk_id);


--
-- Name: knowledge_embeddings knowledge_embeddings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_embeddings
    ADD CONSTRAINT knowledge_embeddings_pkey PRIMARY KEY (id);


--
-- Name: full_field_patta_transfer_application_information_demo nisd_transfer_application_info_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_application_information_demo
    ADD CONSTRAINT nisd_transfer_application_info_pkey PRIMARY KEY (row_id);


--
-- Name: full_field_patta_transfer_igrs_owner_demo nisd_transfer_igrs_owner_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_igrs_owner_demo
    ADD CONSTRAINT nisd_transfer_igrs_owner_pkey PRIMARY KEY (row_id);


--
-- Name: full_field_patta_transfer_new_owner_demo nisd_transfer_new_owner_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_new_owner_demo
    ADD CONSTRAINT nisd_transfer_new_owner_pkey PRIMARY KEY (row_id);


--
-- Name: full_field_patta_transfer_old_owner_demo nisd_transfer_old_owner_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_old_owner_demo
    ADD CONSTRAINT nisd_transfer_old_owner_pkey PRIMARY KEY (row_id);


--
-- Name: full_field_patta_transfer_return_owner_demo nisd_transfer_return_owner_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_return_owner_demo
    ADD CONSTRAINT nisd_transfer_return_owner_pkey PRIMARY KEY (row_id);


--
-- Name: full_field_patta_transfer_urban_demo nisd_transfer_urban_detail_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_urban_demo
    ADD CONSTRAINT nisd_transfer_urban_detail_pkey PRIMARY KEY (row_id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: officer_jurisdictions officer_jurisdictions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.officer_jurisdictions
    ADD CONSTRAINT officer_jurisdictions_pkey PRIMARY KEY (id);


--
-- Name: owners owners_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.owners
    ADD CONSTRAINT owners_pkey PRIMARY KEY (id);


--
-- Name: patta_transfers patta_transfers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patta_transfers
    ADD CONSTRAINT patta_transfers_pkey PRIMARY KEY (id);


--
-- Name: sis_officers sis_officers_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sis_officers
    ADD CONSTRAINT sis_officers_email_key UNIQUE (email);


--
-- Name: sis_officers sis_officers_employee_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sis_officers
    ADD CONSTRAINT sis_officers_employee_id_key UNIQUE (employee_id);


--
-- Name: sis_officers sis_officers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sis_officers
    ADD CONSTRAINT sis_officers_pkey PRIMARY KEY (id);


--
-- Name: sub_divisions sub_divisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sub_divisions
    ADD CONSTRAINT sub_divisions_pkey PRIMARY KEY (id);


--
-- Name: survey_numbers survey_numbers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.survey_numbers
    ADD CONSTRAINT survey_numbers_pkey PRIMARY KEY (id);


--
-- Name: survey_ownership survey_ownership_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.survey_ownership
    ADD CONSTRAINT survey_ownership_pkey PRIMARY KEY (id);


--
-- Name: taluk taluk_app_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.taluk
    ADD CONSTRAINT taluk_app_uid_key UNIQUE (app_uid);


--
-- Name: taluk taluk_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.taluk
    ADD CONSTRAINT taluk_pkey PRIMARY KEY (district_code, taluk_code);


--
-- Name: town town_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.town
    ADD CONSTRAINT town_pkey PRIMARY KEY (district_code, taluk_code, town_code);


--
-- Name: towns towns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.towns
    ADD CONSTRAINT towns_pkey PRIMARY KEY (id);


--
-- Name: towns towns_town_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.towns
    ADD CONSTRAINT towns_town_code_key UNIQUE (town_code);


--
-- Name: attachment_chunks uq_attachment_chunk_index; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachment_chunks
    ADD CONSTRAINT uq_attachment_chunk_index UNIQUE (document_id, chunk_index);


--
-- Name: attachment_rows uq_attachment_row_number; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachment_rows
    ADD CONSTRAINT uq_attachment_row_number UNIQUE (document_id, row_number);


--
-- Name: survey_numbers uq_block_survey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.survey_numbers
    ADD CONSTRAINT uq_block_survey UNIQUE (block_id, survey_no);


--
-- Name: sub_divisions uq_survey_subdivision; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sub_divisions
    ADD CONSTRAINT uq_survey_subdivision UNIQUE (survey_number_id, sub_division_no);


--
-- Name: appl_log_urban_demo urban_application_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.appl_log_urban_demo
    ADD CONSTRAINT urban_application_log_pkey PRIMARY KEY (row_id);


--
-- Name: uchitta_natham_demo urban_natham_chitta_owner_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uchitta_natham_demo
    ADD CONSTRAINT urban_natham_chitta_owner_pkey PRIMARY KEY (row_id);


--
-- Name: uchitta_nathammap_ds_demo urban_natham_chitta_signature_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uchitta_nathammap_ds_demo
    ADD CONSTRAINT urban_natham_chitta_signature_pkey PRIMARY KEY (row_id);


--
-- Name: uareg_demo urban_parcel_register_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uareg_demo
    ADD CONSTRAINT urban_parcel_register_pkey PRIMARY KEY (row_id);


--
-- Name: uaregmap_ds_demo urban_parcel_signature_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uaregmap_ds_demo
    ADD CONSTRAINT urban_parcel_signature_pkey PRIMARY KEY (row_id);


--
-- Name: chitta_temp_subdivclub_demo urban_temp_subdivision_owner_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chitta_temp_subdivclub_demo
    ADD CONSTRAINT urban_temp_subdivision_owner_pkey PRIMARY KEY (row_id);


--
-- Name: areg_temp_subdivclub_demo urban_temp_subdivision_parcel_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.areg_temp_subdivclub_demo
    ADD CONSTRAINT urban_temp_subdivision_parcel_pkey PRIMARY KEY (row_id);


--
-- Name: ward ward_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ward
    ADD CONSTRAINT ward_pkey PRIMARY KEY (district_code, taluk_code, town_code, ward_code);


--
-- Name: wards wards_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wards
    ADD CONSTRAINT wards_pkey PRIMARY KEY (id);


--
-- Name: workflow_history workflow_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_history
    ADD CONSTRAINT workflow_history_pkey PRIMARY KEY (id);


--
-- Name: idx_app_officer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_app_officer ON public.applications USING btree (assigned_officer_id);


--
-- Name: idx_app_officer_overdue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_app_officer_overdue ON public.applications USING btree (assigned_officer_id, is_overdue);


--
-- Name: idx_app_officer_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_app_officer_status ON public.applications USING btree (assigned_officer_id, current_status);


--
-- Name: idx_app_officer_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_app_officer_type ON public.applications USING btree (assigned_officer_id, application_type);


--
-- Name: idx_app_stage; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_app_stage ON public.applications USING btree (current_stage);


--
-- Name: idx_app_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_app_status ON public.applications USING btree (current_status);


--
-- Name: idx_app_submission_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_app_submission_date ON public.applications USING btree (submission_date);


--
-- Name: idx_app_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_app_type ON public.applications USING btree (application_type);


--
-- Name: idx_application_workflow_action_application_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_application_workflow_action_application_id ON public.application_workflow_demo USING btree (application_id);


--
-- Name: idx_application_workflow_action_district_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_application_workflow_action_district_code ON public.application_workflow_demo USING btree (district_code);


--
-- Name: idx_appsubdiv_owner_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_appsubdiv_owner_parent ON public.application_sub_division_owners USING btree (application_sub_division_id);


--
-- Name: idx_attachment_chunk_doc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_attachment_chunk_doc ON public.attachment_chunks USING btree (document_id);


--
-- Name: idx_attachment_chunk_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_attachment_chunk_scope ON public.attachment_chunks USING btree (officer_id, session_id);


--
-- Name: idx_attachment_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_attachment_expires ON public.chat_attachments USING btree (expires_at);


--
-- Name: idx_attachment_officer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_attachment_officer ON public.chat_attachments USING btree (officer_id);


--
-- Name: idx_attachment_row_doc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_attachment_row_doc ON public.attachment_rows USING btree (document_id);


--
-- Name: idx_attachment_session_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_attachment_session_active ON public.chat_attachments USING btree (session_id, is_active);


--
-- Name: idx_chat_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_session ON public.chat_messages USING btree (session_id);


--
-- Name: idx_field_visit_officer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_field_visit_officer ON public.field_visits USING btree (officer_id);


--
-- Name: idx_field_visit_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_field_visit_status ON public.field_visits USING btree (status);


--
-- Name: idx_isd_transfer_application_info_application_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_isd_transfer_application_info_application_id ON public.sub_div_patta_transfer_application_information_urban_demo USING btree (application_id);


--
-- Name: idx_isd_transfer_application_info_application_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_isd_transfer_application_info_application_status ON public.sub_div_patta_transfer_application_information_urban_demo USING btree (application_status);


--
-- Name: idx_isd_transfer_application_info_district_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_isd_transfer_application_info_district_code ON public.sub_div_patta_transfer_application_information_urban_demo USING btree (district_code);


--
-- Name: idx_isd_transfer_urban_detail_application_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_isd_transfer_urban_detail_application_id ON public.sub_div_patta_transfer_urban_demo USING btree (application_id);


--
-- Name: idx_isd_transfer_urban_detail_district_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_isd_transfer_urban_detail_district_code ON public.sub_div_patta_transfer_urban_demo USING btree (district_code);


--
-- Name: idx_isd_transfer_urban_detail_survey_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_isd_transfer_urban_detail_survey_number ON public.sub_div_patta_transfer_urban_demo USING btree (survey_number);


--
-- Name: idx_ke_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ke_category ON public.knowledge_embeddings USING btree (category);


--
-- Name: idx_ke_chunk_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ke_chunk_id ON public.knowledge_embeddings USING btree (chunk_id);


--
-- Name: idx_ke_embedding_hnsw; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ke_embedding_hnsw ON public.knowledge_embeddings USING hnsw (embedding public.vector_cosine_ops) WITH (m='16', ef_construction='64');


--
-- Name: idx_ke_language; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ke_language ON public.knowledge_embeddings USING btree (language);


--
-- Name: idx_nisd_transfer_application_info_application_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_application_info_application_id ON public.full_field_patta_transfer_application_information_demo USING btree (application_id);


--
-- Name: idx_nisd_transfer_application_info_application_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_application_info_application_status ON public.full_field_patta_transfer_application_information_demo USING btree (application_status);


--
-- Name: idx_nisd_transfer_application_info_district_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_application_info_district_code ON public.full_field_patta_transfer_application_information_demo USING btree (district_code);


--
-- Name: idx_nisd_transfer_igrs_owner_application_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_igrs_owner_application_id ON public.full_field_patta_transfer_igrs_owner_demo USING btree (application_id);


--
-- Name: idx_nisd_transfer_igrs_owner_district_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_igrs_owner_district_code ON public.full_field_patta_transfer_igrs_owner_demo USING btree (district_code);


--
-- Name: idx_nisd_transfer_igrs_owner_patta_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_igrs_owner_patta_number ON public.full_field_patta_transfer_igrs_owner_demo USING btree (patta_number);


--
-- Name: idx_nisd_transfer_new_owner_application_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_new_owner_application_id ON public.full_field_patta_transfer_new_owner_demo USING btree (application_id);


--
-- Name: idx_nisd_transfer_new_owner_district_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_new_owner_district_code ON public.full_field_patta_transfer_new_owner_demo USING btree (district_code);


--
-- Name: idx_nisd_transfer_new_owner_patta_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_new_owner_patta_number ON public.full_field_patta_transfer_new_owner_demo USING btree (patta_number);


--
-- Name: idx_nisd_transfer_new_owner_survey_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_new_owner_survey_number ON public.full_field_patta_transfer_new_owner_demo USING btree (survey_number);


--
-- Name: idx_nisd_transfer_old_owner_application_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_old_owner_application_id ON public.full_field_patta_transfer_old_owner_demo USING btree (application_id);


--
-- Name: idx_nisd_transfer_old_owner_district_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_old_owner_district_code ON public.full_field_patta_transfer_old_owner_demo USING btree (district_code);


--
-- Name: idx_nisd_transfer_old_owner_patta_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_old_owner_patta_number ON public.full_field_patta_transfer_old_owner_demo USING btree (patta_number);


--
-- Name: idx_nisd_transfer_return_owner_application_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_return_owner_application_id ON public.full_field_patta_transfer_return_owner_demo USING btree (application_id);


--
-- Name: idx_nisd_transfer_return_owner_district_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_return_owner_district_code ON public.full_field_patta_transfer_return_owner_demo USING btree (district_code);


--
-- Name: idx_nisd_transfer_return_owner_patta_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_return_owner_patta_number ON public.full_field_patta_transfer_return_owner_demo USING btree (patta_number);


--
-- Name: idx_nisd_transfer_urban_detail_application_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_urban_detail_application_id ON public.full_field_patta_transfer_urban_demo USING btree (application_id);


--
-- Name: idx_nisd_transfer_urban_detail_district_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_urban_detail_district_code ON public.full_field_patta_transfer_urban_demo USING btree (district_code);


--
-- Name: idx_nisd_transfer_urban_detail_survey_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nisd_transfer_urban_detail_survey_number ON public.full_field_patta_transfer_urban_demo USING btree (survey_number);


--
-- Name: idx_officer_block; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_officer_block ON public.officer_jurisdictions USING btree (officer_id, block_id);


--
-- Name: idx_officer_jurisdiction; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_officer_jurisdiction ON public.officer_jurisdictions USING btree (officer_id);


--
-- Name: idx_officer_ward; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_officer_ward ON public.officer_jurisdictions USING btree (officer_id, ward_id);


--
-- Name: idx_ownership_owner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ownership_owner ON public.survey_ownership USING btree (owner_id);


--
-- Name: idx_ownership_sub_division; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ownership_sub_division ON public.survey_ownership USING btree (sub_division_id);


--
-- Name: idx_ownership_survey; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ownership_survey ON public.survey_ownership USING btree (survey_number_id);


--
-- Name: idx_sub_division_no; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sub_division_no ON public.sub_divisions USING btree (sub_division_no);


--
-- Name: idx_survey_no; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_survey_no ON public.survey_numbers USING btree (survey_no);


--
-- Name: idx_taluk_district_uid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_taluk_district_uid ON public.taluk USING btree (district_uid);


--
-- Name: idx_urban_application_log_application_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_application_log_application_id ON public.appl_log_urban_demo USING btree (application_id);


--
-- Name: idx_urban_application_log_application_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_application_log_application_status ON public.appl_log_urban_demo USING btree (application_status);


--
-- Name: idx_urban_application_log_district_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_application_log_district_code ON public.appl_log_urban_demo USING btree (district_code);


--
-- Name: idx_urban_application_log_patta_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_application_log_patta_number ON public.appl_log_urban_demo USING btree (patta_number);


--
-- Name: idx_urban_application_log_service_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_application_log_service_code ON public.appl_log_urban_demo USING btree (service_code);


--
-- Name: idx_urban_application_log_survey_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_application_log_survey_number ON public.appl_log_urban_demo USING btree (survey_number);


--
-- Name: idx_urban_natham_chitta_owner_district_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_natham_chitta_owner_district_code ON public.uchitta_natham_demo USING btree (district_code);


--
-- Name: idx_urban_natham_chitta_owner_patta_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_natham_chitta_owner_patta_number ON public.uchitta_natham_demo USING btree (patta_number);


--
-- Name: idx_urban_natham_chitta_signature_district_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_natham_chitta_signature_district_code ON public.uchitta_nathammap_ds_demo USING btree (district_code);


--
-- Name: idx_urban_natham_chitta_signature_patta_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_natham_chitta_signature_patta_number ON public.uchitta_nathammap_ds_demo USING btree (patta_number);


--
-- Name: idx_urban_parcel_register_district_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_parcel_register_district_code ON public.uareg_demo USING btree (district_code);


--
-- Name: idx_urban_parcel_register_patta_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_parcel_register_patta_number ON public.uareg_demo USING btree (patta_number);


--
-- Name: idx_urban_parcel_register_survey_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_parcel_register_survey_number ON public.uareg_demo USING btree (survey_number);


--
-- Name: idx_urban_parcel_signature_district_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_parcel_signature_district_code ON public.uaregmap_ds_demo USING btree (district_code);


--
-- Name: idx_urban_parcel_signature_patta_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_parcel_signature_patta_number ON public.uaregmap_ds_demo USING btree (patta_number);


--
-- Name: idx_urban_parcel_signature_survey_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_parcel_signature_survey_number ON public.uaregmap_ds_demo USING btree (survey_number);


--
-- Name: idx_urban_temp_subdivision_owner_application_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_temp_subdivision_owner_application_id ON public.chitta_temp_subdivclub_demo USING btree (application_id);


--
-- Name: idx_urban_temp_subdivision_owner_district_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_temp_subdivision_owner_district_code ON public.chitta_temp_subdivclub_demo USING btree (district_code);


--
-- Name: idx_urban_temp_subdivision_owner_patta_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_temp_subdivision_owner_patta_number ON public.chitta_temp_subdivclub_demo USING btree (patta_number);


--
-- Name: idx_urban_temp_subdivision_owner_survey_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_temp_subdivision_owner_survey_number ON public.chitta_temp_subdivclub_demo USING btree (survey_number);


--
-- Name: idx_urban_temp_subdivision_parcel_application_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_temp_subdivision_parcel_application_id ON public.areg_temp_subdivclub_demo USING btree (application_id);


--
-- Name: idx_urban_temp_subdivision_parcel_district_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_temp_subdivision_parcel_district_code ON public.areg_temp_subdivclub_demo USING btree (district_code);


--
-- Name: idx_urban_temp_subdivision_parcel_survey_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_urban_temp_subdivision_parcel_survey_number ON public.areg_temp_subdivclub_demo USING btree (survey_number);


--
-- Name: idx_workflow_app; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workflow_app ON public.workflow_history USING btree (application_id);


--
-- Name: application_workflow_demo trg_application_workflow_action_stale; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_application_workflow_action_stale AFTER INSERT OR DELETE OR UPDATE ON public.application_workflow_demo FOR EACH STATEMENT EXECUTE FUNCTION public.notify_app_tables_stale();


--
-- Name: full_field_patta_transfer_igrs_owner_demo trg_nisd_transfer_igrs_owner_stale; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_nisd_transfer_igrs_owner_stale AFTER INSERT OR DELETE OR UPDATE ON public.full_field_patta_transfer_igrs_owner_demo FOR EACH STATEMENT EXECUTE FUNCTION public.notify_app_tables_stale();


--
-- Name: appl_log_urban_demo trg_urban_application_log_stale; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_urban_application_log_stale AFTER INSERT OR DELETE OR UPDATE ON public.appl_log_urban_demo FOR EACH STATEMENT EXECUTE FUNCTION public.notify_app_tables_stale();


--
-- Name: appl_log_urban_demo trg_urban_application_log_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_urban_application_log_touch BEFORE UPDATE ON public.appl_log_urban_demo FOR EACH ROW EXECUTE FUNCTION public.touch_urban_application_log();


--
-- Name: uchitta_natham_demo trg_urban_natham_chitta_owner_stale; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_urban_natham_chitta_owner_stale AFTER INSERT OR DELETE OR UPDATE ON public.uchitta_natham_demo FOR EACH STATEMENT EXECUTE FUNCTION public.notify_app_tables_stale();


--
-- Name: uareg_demo trg_urban_parcel_register_stale; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_urban_parcel_register_stale AFTER INSERT OR DELETE OR UPDATE ON public.uareg_demo FOR EACH STATEMENT EXECUTE FUNCTION public.notify_app_tables_stale();


--
-- Name: chitta_temp_subdivclub_demo trg_urban_temp_subdivision_owner_stale; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_urban_temp_subdivision_owner_stale AFTER INSERT OR DELETE OR UPDATE ON public.chitta_temp_subdivclub_demo FOR EACH STATEMENT EXECUTE FUNCTION public.notify_app_tables_stale();


--
-- Name: areg_temp_subdivclub_demo trg_urban_temp_subdivision_parcel_stale; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_urban_temp_subdivision_parcel_stale AFTER INSERT OR DELETE OR UPDATE ON public.areg_temp_subdivclub_demo FOR EACH STATEMENT EXECUTE FUNCTION public.notify_app_tables_stale();


--
-- Name: taluk FKohac1diklw3t6qi0q0qf0vcp0; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.taluk
    ADD CONSTRAINT "FKohac1diklw3t6qi0q0qf0vcp0" FOREIGN KEY (district_code) REFERENCES public.district_unicode(district_code);


--
-- Name: application_documents application_documents_application_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.application_documents
    ADD CONSTRAINT application_documents_application_id_fkey FOREIGN KEY (application_id) REFERENCES public.applications(id);


--
-- Name: application_sub_division_owners application_sub_division_owner_application_sub_division_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.application_sub_division_owners
    ADD CONSTRAINT application_sub_division_owner_application_sub_division_id_fkey FOREIGN KEY (application_sub_division_id) REFERENCES public.application_sub_divisions(id);


--
-- Name: application_sub_divisions application_sub_divisions_application_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.application_sub_divisions
    ADD CONSTRAINT application_sub_divisions_application_id_fkey FOREIGN KEY (application_id) REFERENCES public.applications(id);


--
-- Name: application_sub_divisions application_sub_divisions_sub_division_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.application_sub_divisions
    ADD CONSTRAINT application_sub_divisions_sub_division_id_fkey FOREIGN KEY (sub_division_id) REFERENCES public.sub_divisions(id);


--
-- Name: applications applications_applicant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.applications
    ADD CONSTRAINT applications_applicant_id_fkey FOREIGN KEY (applicant_id) REFERENCES public.applicants(id);


--
-- Name: applications applications_assigned_officer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.applications
    ADD CONSTRAINT applications_assigned_officer_id_fkey FOREIGN KEY (assigned_officer_id) REFERENCES public.sis_officers(id);


--
-- Name: applications applications_survey_number_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.applications
    ADD CONSTRAINT applications_survey_number_id_fkey FOREIGN KEY (survey_number_id) REFERENCES public.survey_numbers(id);


--
-- Name: attachment_chunks attachment_chunks_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachment_chunks
    ADD CONSTRAINT attachment_chunks_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.chat_attachments(id) ON DELETE CASCADE;


--
-- Name: attachment_rows attachment_rows_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachment_rows
    ADD CONSTRAINT attachment_rows_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.chat_attachments(id) ON DELETE CASCADE;


--
-- Name: audit_logs audit_logs_officer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_officer_id_fkey FOREIGN KEY (officer_id) REFERENCES public.sis_officers(id);


--
-- Name: blocks blocks_ward_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blocks
    ADD CONSTRAINT blocks_ward_id_fkey FOREIGN KEY (ward_id) REFERENCES public.wards(id);


--
-- Name: chat_attachments chat_attachments_officer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_attachments
    ADD CONSTRAINT chat_attachments_officer_id_fkey FOREIGN KEY (officer_id) REFERENCES public.sis_officers(id);


--
-- Name: chat_attachments chat_attachments_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_attachments
    ADD CONSTRAINT chat_attachments_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.chat_sessions(id);


--
-- Name: chat_messages chat_messages_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_messages
    ADD CONSTRAINT chat_messages_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.chat_sessions(id);


--
-- Name: chat_sessions chat_sessions_officer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_sessions
    ADD CONSTRAINT chat_sessions_officer_id_fkey FOREIGN KEY (officer_id) REFERENCES public.sis_officers(id);


--
-- Name: field_visits field_visits_application_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.field_visits
    ADD CONSTRAINT field_visits_application_id_fkey FOREIGN KEY (application_id) REFERENCES public.applications(id);


--
-- Name: field_visits field_visits_officer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.field_visits
    ADD CONSTRAINT field_visits_officer_id_fkey FOREIGN KEY (officer_id) REFERENCES public.sis_officers(id);


--
-- Name: notifications notifications_application_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_application_id_fkey FOREIGN KEY (application_id) REFERENCES public.applications(id);


--
-- Name: notifications notifications_officer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_officer_id_fkey FOREIGN KEY (officer_id) REFERENCES public.sis_officers(id);


--
-- Name: officer_jurisdictions officer_jurisdictions_block_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.officer_jurisdictions
    ADD CONSTRAINT officer_jurisdictions_block_id_fkey FOREIGN KEY (block_id) REFERENCES public.blocks(id);


--
-- Name: officer_jurisdictions officer_jurisdictions_district_id_master_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.officer_jurisdictions
    ADD CONSTRAINT officer_jurisdictions_district_id_master_fkey FOREIGN KEY (district_id) REFERENCES public.district_unicode(app_uid);


--
-- Name: officer_jurisdictions officer_jurisdictions_officer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.officer_jurisdictions
    ADD CONSTRAINT officer_jurisdictions_officer_id_fkey FOREIGN KEY (officer_id) REFERENCES public.sis_officers(id);


--
-- Name: officer_jurisdictions officer_jurisdictions_taluk_id_master_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.officer_jurisdictions
    ADD CONSTRAINT officer_jurisdictions_taluk_id_master_fkey FOREIGN KEY (taluk_id) REFERENCES public.taluk(app_uid);


--
-- Name: officer_jurisdictions officer_jurisdictions_town_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.officer_jurisdictions
    ADD CONSTRAINT officer_jurisdictions_town_id_fkey FOREIGN KEY (town_id) REFERENCES public.towns(id);


--
-- Name: officer_jurisdictions officer_jurisdictions_ward_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.officer_jurisdictions
    ADD CONSTRAINT officer_jurisdictions_ward_id_fkey FOREIGN KEY (ward_id) REFERENCES public.wards(id);


--
-- Name: patta_transfers patta_transfers_application_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patta_transfers
    ADD CONSTRAINT patta_transfers_application_id_fkey FOREIGN KEY (application_id) REFERENCES public.applications(id);


--
-- Name: patta_transfers patta_transfers_new_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patta_transfers
    ADD CONSTRAINT patta_transfers_new_owner_id_fkey FOREIGN KEY (new_owner_id) REFERENCES public.owners(id);


--
-- Name: patta_transfers patta_transfers_previous_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patta_transfers
    ADD CONSTRAINT patta_transfers_previous_owner_id_fkey FOREIGN KEY (previous_owner_id) REFERENCES public.owners(id);


--
-- Name: patta_transfers patta_transfers_sub_division_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patta_transfers
    ADD CONSTRAINT patta_transfers_sub_division_id_fkey FOREIGN KEY (sub_division_id) REFERENCES public.sub_divisions(id);


--
-- Name: patta_transfers patta_transfers_survey_number_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patta_transfers
    ADD CONSTRAINT patta_transfers_survey_number_id_fkey FOREIGN KEY (survey_number_id) REFERENCES public.survey_numbers(id);


--
-- Name: sub_divisions sub_divisions_survey_number_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sub_divisions
    ADD CONSTRAINT sub_divisions_survey_number_id_fkey FOREIGN KEY (survey_number_id) REFERENCES public.survey_numbers(id);


--
-- Name: survey_numbers survey_numbers_block_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.survey_numbers
    ADD CONSTRAINT survey_numbers_block_id_fkey FOREIGN KEY (block_id) REFERENCES public.blocks(id);


--
-- Name: survey_ownership survey_ownership_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.survey_ownership
    ADD CONSTRAINT survey_ownership_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.owners(id);


--
-- Name: survey_ownership survey_ownership_sub_division_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.survey_ownership
    ADD CONSTRAINT survey_ownership_sub_division_id_fkey FOREIGN KEY (sub_division_id) REFERENCES public.sub_divisions(id);


--
-- Name: survey_ownership survey_ownership_survey_number_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.survey_ownership
    ADD CONSTRAINT survey_ownership_survey_number_id_fkey FOREIGN KEY (survey_number_id) REFERENCES public.survey_numbers(id);


--
-- Name: taluk taluk_district_uid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.taluk
    ADD CONSTRAINT taluk_district_uid_fkey FOREIGN KEY (district_uid) REFERENCES public.district_unicode(app_uid);


--
-- Name: towns towns_taluk_id_master_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.towns
    ADD CONSTRAINT towns_taluk_id_master_fkey FOREIGN KEY (taluk_id) REFERENCES public.taluk(app_uid);


--
-- Name: wards wards_town_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wards
    ADD CONSTRAINT wards_town_id_fkey FOREIGN KEY (town_id) REFERENCES public.towns(id);


--
-- Name: workflow_history workflow_history_application_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_history
    ADD CONSTRAINT workflow_history_application_id_fkey FOREIGN KEY (application_id) REFERENCES public.applications(id);


--
-- Name: workflow_history workflow_history_performed_by_officer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_history
    ADD CONSTRAINT workflow_history_performed_by_officer_id_fkey FOREIGN KEY (performed_by_officer_id) REFERENCES public.sis_officers(id);


--
-- PostgreSQL database dump complete
--

\unrestrict Lhr7JQQn8jmljLBFMCSRBZxF4oUSV56qFGi2iAmRMTnx1jzEmmNx91wcdVgyM4W

