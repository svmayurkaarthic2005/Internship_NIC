--
-- PostgreSQL database dump
--

\restrict 7GD7GHi4f0f3vLrKeavkbRr3ytHcYs7udG7uHCXzbcQ6IcqknaQVgahLgKgXwhN

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
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

-- *not* creating schema, since initdb creates it


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS '';


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: appl_log_urban; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.appl_log_urban (
    row_id bigint NOT NULL,
    slno integer,
    user_id character varying(60),
    dept_id character varying(60),
    service_code character varying(60),
    district_code character varying(60),
    taluk_code character varying(60),
    village_code character varying(60),
    urban_unit_code character varying(60),
    appl_dt character varying(60),
    appl_status character varying(60),
    update_dt character varying(60),
    appl_id character varying(60),
    survey_no character varying(60),
    subdiv_no character varying(60),
    patta_no character varying(60),
    role_id character varying(60),
    source_id character varying(60),
    csc_charge character varying(60),
    govt_charge character varying(60),
    can character varying(60),
    despatch_date character varying(60),
    received_date date,
    ip_address text,
    generated_date character varying(60),
    source_name character varying(60),
    renewal_no character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    state character varying(60),
    igrs_form6no character varying(60),
    rtr_str character varying(60),
    current_subdivno character varying(60),
    appl_id_q character varying(60),
    automutated character varying(60),
    is_automutated character varying(60),
    automutation_igrs character varying(60),
    camp_flag character varying(60),
    camp_correction_id character varying(60),
    camp_code character varying(60)
);


--
-- Name: appl_log_urban_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.appl_log_urban_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: appl_log_urban_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.appl_log_urban_row_id_seq OWNED BY public.appl_log_urban.row_id;


--
-- Name: applicants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.applicants (
    id uuid NOT NULL,
    name character varying(200) NOT NULL,
    mobile character varying(15),
    aadhaar_last4 character(4),
    address text,
    permanent_address text,
    father_name character varying(200),
    mother_name character varying(200),
    date_of_birth date,
    gender character varying(10),
    occupation character varying(100),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
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
    temporary_sub_division_no character varying(50),
    proposed_sub_division_no character varying(50),
    status character varying(30),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: application_workflow; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.application_workflow (
    row_id bigint NOT NULL,
    slno integer,
    appl_id character varying(60),
    district_code character varying(60),
    taluk_code character varying(60),
    village_code character varying(60),
    action_from character varying(60),
    action_to character varying(60),
    action_date date,
    remarks text,
    action_status character varying(60),
    update_dt character varying(60),
    update_by character varying(60),
    state character varying(60),
    recommend character varying(60),
    field_visit_dt character varying(60),
    received character varying(60),
    income character varying(60),
    rflag character varying(60),
    ip_address text,
    zdt_automatic_recommend character varying(60),
    zdt_automatic_remarks text
);


--
-- Name: application_workflow_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.application_workflow_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: application_workflow_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.application_workflow_row_id_seq OWNED BY public.application_workflow.row_id;


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
    submission_source_name character varying(100),
    submission_ip character varying(50),
    submission_camp_flag character varying(5),
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
    fee_amount numeric(10,2),
    challan_number character varying(50),
    payment_mode character varying(20),
    igrs_form6_number character varying(30),
    merged_application_id character varying(30),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_application_type CHECK (((application_type)::text = ANY ((ARRAY['ISD'::character varying, 'NISD'::character varying])::text[]))),
    CONSTRAINT ck_current_stage CHECK (((current_stage)::text = ANY ((ARRAY['SIS'::character varying, 'SD'::character varying, 'DIS'::character varying, 'TAHSILDAR'::character varying, 'COMPLETED'::character varying, 'REJECTED'::character varying])::text[]))),
    CONSTRAINT ck_current_status CHECK (((current_status)::text = ANY ((ARRAY['pending'::character varying, 'in_progress'::character varying, 'approved'::character varying, 'rejected'::character varying, 'escalated'::character varying])::text[])))
);


--
-- Name: areg_temp_subdivclub; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.areg_temp_subdivclub (
    row_id bigint NOT NULL,
    appl_id character varying(60),
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    survey_no character varying(60),
    temp_sub_div_no character varying(60),
    new_sub_div_no character varying(60),
    existing_patta_no character varying(60),
    area_hec character varying(60),
    area_ares numeric(14,2),
    area_sqmtr character varying(60),
    npatta_waste character varying(60),
    update_dt character varying(60),
    submit_dt character varying(60),
    land_type character varying(60),
    govt_pri character varying(60),
    tax_rate numeric(14,2),
    tax_hect character varying(60),
    soil_typ_p character varying(60),
    soil_typ_s character varying(60),
    adopt_area_hec character varying(60),
    adopt_area_ares character varying(60),
    status character varying(60),
    adopt_area_sqmtr character varying(60),
    sd_adopt_area_hec character varying(60),
    sd_adopt_area_ares character varying(60),
    sd_adopt_area_sqmtr character varying(60),
    dis_adopt_area_hec character varying(60),
    dis_adopt_area_ares character varying(60),
    dis_adopt_area_sqmtr character varying(60),
    new_patta_no character varying(60),
    group_survey_no character varying(60),
    group_sub_div_no character varying(60),
    group_temp_sub_div_no character varying(60),
    group_patta_no character varying(60),
    plot_type character varying(60),
    plot_no character varying(60),
    land_use character varying(200),
    descriptive_landuse character varying(60)
);


--
-- Name: areg_temp_subdivclub_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.areg_temp_subdivclub_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: areg_temp_subdivclub_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.areg_temp_subdivclub_row_id_seq OWNED BY public.areg_temp_subdivclub.row_id;


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
-- Name: chitta_temp_old_owner; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chitta_temp_old_owner (
    row_id bigint NOT NULL,
    appl_id character varying(60),
    owner_name text,
    owner_ename character varying(60),
    relation character varying(60),
    relative_name text,
    relative_ename character varying(60),
    extent_u1 character varying(60),
    extent_u2 character varying(60),
    extent_u3 character varying(60),
    patta_no character varying(60),
    relation_code character varying(60),
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    survey_no character varying(60),
    subdiv_no character varying(60),
    new_sub_div_no character varying(60),
    existing_patta_no character varying(60),
    owner_num character varying(60),
    status character varying(60),
    share character varying(60),
    aadhar_no character varying(60),
    sex character varying(60)
);


--
-- Name: chitta_temp_old_owner_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.chitta_temp_old_owner_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: chitta_temp_old_owner_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.chitta_temp_old_owner_row_id_seq OWNED BY public.chitta_temp_old_owner.row_id;


--
-- Name: chitta_temp_subdivclub; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chitta_temp_subdivclub (
    row_id bigint NOT NULL,
    appl_id character varying(60),
    owner_name text,
    relation character varying(60),
    relative_name text,
    extent_u1 character varying(60),
    extent_u2 character varying(60),
    extent_u3 character varying(60),
    patta_no character varying(60),
    relation_code character varying(60),
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    survey_no character varying(60),
    temp_sub_div_no character varying(60),
    new_sub_div_no character varying(60),
    existing_patta_no character varying(60),
    owner_num character varying(60),
    status character varying(60),
    owner_ename character varying(60),
    relative_ename character varying(60),
    share character varying(60),
    aadhar_no character varying(60),
    sex character varying(60)
);


--
-- Name: chitta_temp_subdivclub_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.chitta_temp_subdivclub_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: chitta_temp_subdivclub_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.chitta_temp_subdivclub_row_id_seq OWNED BY public.chitta_temp_subdivclub.row_id;


--
-- Name: correction_modification_flow; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.correction_modification_flow (
    row_id bigint NOT NULL,
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    appl_id character varying(60),
    survey_no character varying(60),
    sub_div_no character varying(60),
    pa_submit_date character varying(60),
    reason_code character varying(60),
    sis_rec character varying(60),
    sis_remarks text,
    sis_submit_date character varying(60),
    zdt_rec character varying(60),
    zdt_remarks text,
    zdt_submit_date character varying(60),
    sd_rec character varying(60),
    sd_remarks text,
    sd_submit_date character varying(60),
    dis_rec character varying(60),
    dis_remarks text,
    dis_submit_date character varying(60),
    thl_rec character varying(60),
    thl_remarks text,
    thl_submit_date character varying(60),
    rdo_rec character varying(60),
    rdo_remarks text,
    rdo_submit_date character varying(60),
    collr_dro_rec character varying(60),
    collr_dro_remarks text,
    collr_dro_submit_date character varying(60),
    govt_change_flag character varying(60),
    priv_change_flag character varying(60),
    dro_rec character varying(60),
    dro_remarks text,
    dro_submit_date character varying(60),
    certificate_sis character varying(60),
    certificate_dis character varying(60),
    coss_rec character varying(60),
    coss_remarks text,
    coss_submit_date character varying(60),
    cla_rec character varying(60),
    cla_remarks text,
    cla_submit_date character varying(60),
    camp_code character varying(60)
);


--
-- Name: correction_modification_flow_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.correction_modification_flow_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: correction_modification_flow_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.correction_modification_flow_row_id_seq OWNED BY public.correction_modification_flow.row_id;


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
-- Name: f_line_service_application_information_urban; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.f_line_service_application_information_urban (
    row_id bigint NOT NULL,
    appl_id character varying(60),
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    can character varying(60),
    appl_name character varying(60),
    appl_address_cur text,
    mobile_no character varying(60),
    appl_status character varying(60),
    remarks text,
    update_date character varying(60),
    appl_status_code character varying(60),
    reject_reason_code character varying(60),
    challan_no character varying(60),
    challan_date date,
    paymentmode character varying(60),
    firstpage character varying(60),
    reversepage character varying(60),
    lastpage character varying(60),
    sro character varying(60),
    proposed_field_visit_date date,
    proposed_remarks text,
    missing_docs character varying(60),
    amount character varying(60),
    csc_name character varying(200),
    igrs_form6no character varying(60),
    rtr_str character varying(60),
    relative_mobile_no character varying(60),
    appl_address_perm text,
    mother_name character varying(60),
    father_name character varying(60),
    gender character varying(60),
    dob character varying(60),
    enclosures character varying(60)
);


--
-- Name: f_line_service_application_information_urban_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.f_line_service_application_information_urban_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: f_line_service_application_information_urban_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.f_line_service_application_information_urban_row_id_seq OWNED BY public.f_line_service_application_information_urban.row_id;


--
-- Name: f_line_service_urban; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.f_line_service_urban (
    row_id bigint NOT NULL,
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    appl_id character varying(60),
    survey_no character varying(60),
    subdiv_no character varying(60),
    patta_no character varying(60),
    land_type character varying(60),
    extent_u1 character varying(60),
    extent_u2 character varying(60),
    extent_u3 character varying(60),
    applicant_type character varying(60),
    submit_date character varying(60),
    govt_pri character varying(60),
    transaction_status character varying(60),
    total_boundary character varying(60),
    sketch_file text,
    sis_rec character varying(60),
    sis_remarks text,
    sis_reason character varying(60),
    sis_ip_address text,
    sis_verify_date character varying(60),
    sis_username character varying(60),
    dis_rec character varying(60),
    dis_remarks text,
    dis_reason character varying(60),
    dis_ip_address text,
    dis_verify_date character varying(60),
    dis_username character varying(60),
    land_type_code character varying(60),
    sketch_file_dis text,
    tobe_scheduled_date character varying(60)
);


--
-- Name: f_line_service_urban_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.f_line_service_urban_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: f_line_service_urban_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.f_line_service_urban_row_id_seq OWNED BY public.f_line_service_urban.row_id;


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
-- Name: file_attachment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.file_attachment (
    row_id bigint NOT NULL,
    appl_id character varying(60),
    attachmentname text,
    attachmentvalue text,
    verifieddoc character varying(60),
    modeofdoc character varying(60)
);


--
-- Name: file_attachment_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.file_attachment_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: file_attachment_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.file_attachment_row_id_seq OWNED BY public.file_attachment.row_id;


--
-- Name: full_field_patta_transfer_application_information; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.full_field_patta_transfer_application_information (
    row_id bigint NOT NULL,
    appl_id character varying(60),
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    can character varying(60),
    appl_name character varying(60),
    appl_address_cur text,
    mobile_no character varying(60),
    appl_status character varying(60),
    remarks text,
    update_date character varying(60),
    appl_address_perm text,
    mother_name character varying(60),
    father_name character varying(60),
    dob character varying(60),
    gender character varying(60),
    occupation character varying(200),
    enclosures character varying(60),
    barcode_flag character varying(60),
    firstpage character varying(60),
    reversepage character varying(60),
    lastpage character varying(60),
    encertify character varying(60),
    proposed_field_visit_date date,
    proposed_remarks text,
    missing_docs character varying(60),
    verifyphysical character varying(60),
    date_of_sending_document character varying(60),
    date_of_receipt_of_document character varying(60),
    challan_no character varying(60),
    challan_date date,
    treasury character varying(60),
    bank character varying(60),
    bank_branch character varying(200),
    paymentmode character varying(60),
    amount character varying(60),
    igrs_form6no character varying(60),
    rtr_str character varying(60),
    owner_correction_reason character varying(200),
    merge_applid character varying(60),
    is_automutated character varying(60)
);


--
-- Name: full_field_patta_transfer_application_information_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.full_field_patta_transfer_application_information_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: full_field_patta_transfer_application_information_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.full_field_patta_transfer_application_information_row_id_seq OWNED BY public.full_field_patta_transfer_application_information.row_id;


--
-- Name: full_field_patta_transfer_igrs_owner; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.full_field_patta_transfer_igrs_owner (
    row_id bigint NOT NULL,
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    door_no character varying(60),
    patta_no character varying(60),
    occ_cd character varying(60),
    own_num integer,
    owner character varying(60),
    owner_english character varying(60),
    rel_num integer,
    relative character varying(60),
    relative_english character varying(60),
    reltn character varying(60),
    share character varying(60),
    address text,
    cin_no character varying(60),
    form6_no character varying(60),
    assign_no character varying(60),
    ration_card_no character varying(60),
    epic_no character varying(60),
    pincode character varying(60),
    updn_dt character varying(60),
    user_no character varying(60),
    aadhar_no character varying(60),
    sex character varying(60),
    igrs_form6_no character varying(60),
    mobile_number character varying(60),
    appl_id character varying(60),
    owner_img character varying(60)
);


--
-- Name: full_field_patta_transfer_igrs_owner_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.full_field_patta_transfer_igrs_owner_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: full_field_patta_transfer_igrs_owner_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.full_field_patta_transfer_igrs_owner_row_id_seq OWNED BY public.full_field_patta_transfer_igrs_owner.row_id;


--
-- Name: full_field_patta_transfer_new_owner; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.full_field_patta_transfer_new_owner (
    row_id bigint NOT NULL,
    appl_id character varying(60),
    owner_name text,
    owner_ename character varying(60),
    relation character varying(60),
    relative_name text,
    extent character varying(60),
    patta_no character varying(60),
    relation_code character varying(60),
    owner_num character varying(60),
    status_owner character varying(60),
    uds character varying(60),
    photo character varying(60),
    relative_ename character varying(60),
    share character varying(60),
    aadhar_no character varying(60),
    sex character varying(60),
    rel_num integer,
    block_code character varying(60),
    survey_no character varying(60),
    sub_div_no character varying(60),
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    owner_img character varying(60),
    mobile_number character varying(60)
);


--
-- Name: full_field_patta_transfer_new_owner_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.full_field_patta_transfer_new_owner_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: full_field_patta_transfer_new_owner_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.full_field_patta_transfer_new_owner_row_id_seq OWNED BY public.full_field_patta_transfer_new_owner.row_id;


--
-- Name: full_field_patta_transfer_old_owner; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.full_field_patta_transfer_old_owner (
    row_id bigint NOT NULL,
    appl_id character varying(60),
    owner_name text,
    owner_ename character varying(60),
    relation character varying(60),
    relative_name text,
    extent character varying(60),
    patta_no character varying(60),
    relation_code character varying(60),
    owner_num character varying(60),
    status_owner character varying(60),
    uds character varying(60),
    photo character varying(60),
    relative_ename character varying(60),
    share character varying(60),
    aadhar_no character varying(60),
    sex character varying(60),
    rel_num integer,
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60)
);


--
-- Name: full_field_patta_transfer_old_owner_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.full_field_patta_transfer_old_owner_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: full_field_patta_transfer_old_owner_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.full_field_patta_transfer_old_owner_row_id_seq OWNED BY public.full_field_patta_transfer_old_owner.row_id;


--
-- Name: full_field_patta_transfer_return_owner; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.full_field_patta_transfer_return_owner (
    row_id bigint NOT NULL,
    appl_id character varying(60),
    owner_name text,
    owner_ename character varying(60),
    relation character varying(60),
    relative_name text,
    extent character varying(60),
    patta_no character varying(60),
    relation_code character varying(60),
    owner_num character varying(60),
    status_owner character varying(60),
    uds character varying(60),
    photo character varying(60),
    relative_ename character varying(60),
    share character varying(60),
    aadhar_no character varying(60),
    sex character varying(60),
    rel_num integer,
    status character varying(60),
    update_dt character varying(60),
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    owner_img character varying(60),
    mobile_number character varying(60)
);


--
-- Name: full_field_patta_transfer_return_owner_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.full_field_patta_transfer_return_owner_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: full_field_patta_transfer_return_owner_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.full_field_patta_transfer_return_owner_row_id_seq OWNED BY public.full_field_patta_transfer_return_owner.row_id;


--
-- Name: full_field_patta_transfer_urban; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.full_field_patta_transfer_urban (
    row_id bigint NOT NULL,
    slno integer,
    sno integer,
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    appl_id character varying(60),
    survey_no character varying(60),
    sub_div_no character varying(60),
    old_patta_no character varying(60),
    land_type_code character varying(60),
    extent_u1 character varying(60),
    extent_u2 character varying(60),
    extent_u3 character varying(60),
    regi_doc_no character varying(60),
    reason character varying(60),
    regi_place character varying(60),
    regi_on character varying(60),
    zdt_patta_no character varying(60),
    transaction_status character varying(60),
    zdt_order_no character varying(60),
    zdt_order_dt character varying(60),
    zdt_remarks text,
    update_dt character varying(60),
    submit_date character varying(60),
    extentares character varying(60),
    taxrate character varying(60),
    successioncertificateno character varying(60),
    successioncertificatedate character varying(60),
    successioncertificateissuedby character varying(60),
    legalheircertificateplaceofissue character varying(60),
    court_order_no character varying(60),
    court_order_date date,
    court_name character varying(200),
    street_code character varying(60),
    purchased_area numeric(14,2),
    purchased_area_unit character varying(60),
    transaction_code character varying(60),
    oldstreet_code character varying(60),
    sis_rec character varying(60),
    sis_remarks text,
    sis_rec_reason character varying(60),
    new_door_no character varying(60),
    new_tot_tax character varying(60),
    old_door_no character varying(60),
    old_tot_tax character varying(60),
    group_survey_no character varying(60),
    group_sub_div_no character varying(60),
    group_patta_no character varying(60),
    owner_correction_reason character varying(200),
    trans_type character varying(60),
    direct_transfer character varying(60),
    land_cat_code character varying(60)
);


--
-- Name: full_field_patta_transfer_urban_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.full_field_patta_transfer_urban_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: full_field_patta_transfer_urban_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.full_field_patta_transfer_urban_row_id_seq OWNED BY public.full_field_patta_transfer_urban.row_id;


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
    relationship_type character varying(50),
    aadhaar_last4 character(4),
    mobile character varying(15),
    address text,
    gender character varying(10),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
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
    new_patta_number character varying(50),
    transfer_date date,
    tahsildar_signature_date date,
    signed_by character varying(50),
    dsc_applied boolean,
    status character varying(30),
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
    sis_recommendation_reason text,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: sd_attachment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sd_attachment (
    row_id bigint NOT NULL,
    appl_id character varying(60),
    slno integer,
    file text,
    survey_subdiv_no character varying(60)
);


--
-- Name: sd_attachment_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sd_attachment_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sd_attachment_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sd_attachment_row_id_seq OWNED BY public.sd_attachment.row_id;


--
-- Name: service_register_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.service_register_entries (
    id uuid NOT NULL,
    application_number character varying(30) NOT NULL,
    service_code character varying(10) NOT NULL,
    status character varying(30),
    submission_date date,
    ward_id uuid NOT NULL,
    block_id uuid,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    survey_no character varying(50),
    subdivision_no character varying(50),
    stage character varying(30),
    submission_channel character varying(20),
    submission_ip character varying(50)
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
-- Name: sub_div_patta_transfer_application_information_urban; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sub_div_patta_transfer_application_information_urban (
    row_id bigint NOT NULL,
    appl_id character varying(60),
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    can character varying(60),
    appl_name character varying(60),
    appl_address_cur text,
    mobile_no character varying(60),
    appl_status character varying(60),
    remarks text,
    update_date character varying(60),
    appl_status_code character varying(60),
    reject_reason_code character varying(60),
    enclosures character varying(60),
    challan_no character varying(60),
    challan_date date,
    treasury character varying(60),
    bank character varying(60),
    bank_branch character varying(200),
    paymentmode character varying(60),
    firstpage character varying(60),
    reversepage character varying(60),
    lastpage character varying(60),
    sro character varying(60),
    encertify character varying(60),
    proposed_field_visit_date date,
    proposed_remarks text,
    missing_docs character varying(60),
    amount character varying(60),
    csc_name character varying(200),
    igrs_form6no character varying(60),
    rtr_str character varying(60),
    appl_address_perm text,
    mother_name character varying(60),
    father_name character varying(60),
    dob character varying(60),
    gender character varying(60),
    merge_applid character varying(60)
);


--
-- Name: sub_div_patta_transfer_application_information_urban_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sub_div_patta_transfer_application_information_urban_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sub_div_patta_transfer_application_information_urban_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sub_div_patta_transfer_application_information_urban_row_id_seq OWNED BY public.sub_div_patta_transfer_application_information_urban.row_id;


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
-- Name: tslr_sketch_urban; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tslr_sketch_urban (
    row_id bigint NOT NULL,
    slno integer,
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    survey_no character varying(60),
    sub_div_no character varying(60),
    can character varying(60),
    appl_id character varying(60),
    old_patta_no character varying(60),
    door_no character varying(60),
    type_ecate character varying(60),
    land_type_code character varying(60),
    ext_u1 character varying(60),
    ext_u2 character varying(60),
    ext_u3 character varying(60),
    submit_dt character varying(60),
    challan_no character varying(60),
    challan_date date,
    treasury character varying(60),
    bank character varying(60),
    bank_branch character varying(200),
    paymentmode character varying(60),
    amount character varying(60)
);


--
-- Name: tslr_sketch_urban_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tslr_sketch_urban_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tslr_sketch_urban_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tslr_sketch_urban_row_id_seq OWNED BY public.tslr_sketch_urban.row_id;


--
-- Name: uareg_modification; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.uareg_modification (
    row_id bigint NOT NULL,
    slno integer,
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    appl_id character varying(60),
    appl_status character varying(60),
    operation character varying(60),
    street_code character varying(60),
    survey_no character varying(60),
    subdiv_no character varying(60),
    osubdiv_no character varying(60),
    osurvey_no character varying(60),
    dbl_crop character varying(60),
    part_ind character varying(60),
    door_no character varying(60),
    govt_pri character varying(60),
    land_type character varying(60),
    irr_source character varying(60),
    tax_rate numeric(14,2),
    soil_type_p character varying(60),
    soil_type_s character varying(60),
    soil_class character varying(60),
    tax_hect character varying(60),
    ext_unit character varying(60),
    ext_u1 character varying(60),
    ext_u2 character varying(60),
    ext_u3 character varying(60),
    tot_tax character varying(60),
    patta_no character varying(60),
    occ_cd character varying(60),
    land_use character varying(200),
    remarks text,
    remarks1 text,
    newpatta character varying(60),
    oldpatta character varying(60),
    form7_no character varying(60),
    form8_no character varying(60),
    form6_no character varying(60),
    relinq_no character varying(60),
    assign_no character varying(60),
    alien_no character varying(60),
    aquis_no character varying(60),
    assessed character varying(60),
    cultivable character varying(60),
    updn_dt character varying(60),
    descriptive_remarkcode text,
    descriptive_landuse character varying(60),
    municipal_tax numeric(14,2),
    approval_file_no text,
    approval_file_date character varying(60),
    status character varying(60),
    username character varying(60),
    existing_patta_no character varying(60),
    new_patta_no character varying(60),
    tempsubdiv_no character varying(60),
    reject_reason_code character varying(60),
    coss_block_code character varying(60),
    flowtype character varying(60),
    street_name_citizen character varying(60)
);


--
-- Name: uareg_modification_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.uareg_modification_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: uareg_modification_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.uareg_modification_row_id_seq OWNED BY public.uareg_modification.row_id;


--
-- Name: uchitta_natham_modification; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.uchitta_natham_modification (
    row_id bigint NOT NULL,
    district_code character varying(60),
    taluk_code character varying(60),
    town_code character varying(60),
    ward_code character varying(60),
    block_code character varying(60),
    door_no character varying(60),
    patta_no character varying(60),
    occ_cd character varying(60),
    own_num integer,
    owner character varying(60),
    owner_english character varying(60),
    rel_num integer,
    relative character varying(60),
    relative_english character varying(60),
    reltn character varying(60),
    share character varying(60),
    address text,
    cin_no character varying(60),
    form6_no character varying(60),
    assign_no character varying(60),
    ration_card_no character varying(60),
    epic_no character varying(60),
    pincode character varying(60),
    updn_dt character varying(60),
    user_no character varying(60),
    appl_id character varying(60),
    slno integer,
    appl_status character varying(60),
    operation character varying(60),
    full_data character varying(60),
    sex character varying(60),
    s_patta_no character varying(60),
    flowtype character varying(60),
    aadhar_no character varying(60)
);


--
-- Name: uchitta_natham_modification_row_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.uchitta_natham_modification_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: uchitta_natham_modification_row_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.uchitta_natham_modification_row_id_seq OWNED BY public.uchitta_natham_modification.row_id;


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
-- Name: appl_log_urban row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.appl_log_urban ALTER COLUMN row_id SET DEFAULT nextval('public.appl_log_urban_row_id_seq'::regclass);


--
-- Name: application_workflow row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.application_workflow ALTER COLUMN row_id SET DEFAULT nextval('public.application_workflow_row_id_seq'::regclass);


--
-- Name: areg_temp_subdivclub row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.areg_temp_subdivclub ALTER COLUMN row_id SET DEFAULT nextval('public.areg_temp_subdivclub_row_id_seq'::regclass);


--
-- Name: chitta_temp_old_owner row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chitta_temp_old_owner ALTER COLUMN row_id SET DEFAULT nextval('public.chitta_temp_old_owner_row_id_seq'::regclass);


--
-- Name: chitta_temp_subdivclub row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chitta_temp_subdivclub ALTER COLUMN row_id SET DEFAULT nextval('public.chitta_temp_subdivclub_row_id_seq'::regclass);


--
-- Name: correction_modification_flow row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.correction_modification_flow ALTER COLUMN row_id SET DEFAULT nextval('public.correction_modification_flow_row_id_seq'::regclass);


--
-- Name: f_line_service_application_information_urban row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.f_line_service_application_information_urban ALTER COLUMN row_id SET DEFAULT nextval('public.f_line_service_application_information_urban_row_id_seq'::regclass);


--
-- Name: f_line_service_urban row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.f_line_service_urban ALTER COLUMN row_id SET DEFAULT nextval('public.f_line_service_urban_row_id_seq'::regclass);


--
-- Name: file_attachment row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_attachment ALTER COLUMN row_id SET DEFAULT nextval('public.file_attachment_row_id_seq'::regclass);


--
-- Name: full_field_patta_transfer_application_information row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_application_information ALTER COLUMN row_id SET DEFAULT nextval('public.full_field_patta_transfer_application_information_row_id_seq'::regclass);


--
-- Name: full_field_patta_transfer_igrs_owner row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_igrs_owner ALTER COLUMN row_id SET DEFAULT nextval('public.full_field_patta_transfer_igrs_owner_row_id_seq'::regclass);


--
-- Name: full_field_patta_transfer_new_owner row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_new_owner ALTER COLUMN row_id SET DEFAULT nextval('public.full_field_patta_transfer_new_owner_row_id_seq'::regclass);


--
-- Name: full_field_patta_transfer_old_owner row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_old_owner ALTER COLUMN row_id SET DEFAULT nextval('public.full_field_patta_transfer_old_owner_row_id_seq'::regclass);


--
-- Name: full_field_patta_transfer_return_owner row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_return_owner ALTER COLUMN row_id SET DEFAULT nextval('public.full_field_patta_transfer_return_owner_row_id_seq'::regclass);


--
-- Name: full_field_patta_transfer_urban row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_urban ALTER COLUMN row_id SET DEFAULT nextval('public.full_field_patta_transfer_urban_row_id_seq'::regclass);


--
-- Name: sd_attachment row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sd_attachment ALTER COLUMN row_id SET DEFAULT nextval('public.sd_attachment_row_id_seq'::regclass);


--
-- Name: sub_div_patta_transfer_application_information_urban row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sub_div_patta_transfer_application_information_urban ALTER COLUMN row_id SET DEFAULT nextval('public.sub_div_patta_transfer_application_information_urban_row_id_seq'::regclass);


--
-- Name: taluk id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.taluk ALTER COLUMN id SET DEFAULT nextval('public.taluk_id_seq'::regclass);


--
-- Name: tslr_sketch_urban row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tslr_sketch_urban ALTER COLUMN row_id SET DEFAULT nextval('public.tslr_sketch_urban_row_id_seq'::regclass);


--
-- Name: uareg_modification row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uareg_modification ALTER COLUMN row_id SET DEFAULT nextval('public.uareg_modification_row_id_seq'::regclass);


--
-- Name: uchitta_natham_modification row_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uchitta_natham_modification ALTER COLUMN row_id SET DEFAULT nextval('public.uchitta_natham_modification_row_id_seq'::regclass);


--
-- Name: appl_log_urban appl_log_urban_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.appl_log_urban
    ADD CONSTRAINT appl_log_urban_pkey PRIMARY KEY (row_id);


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
-- Name: application_workflow application_workflow_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.application_workflow
    ADD CONSTRAINT application_workflow_pkey PRIMARY KEY (row_id);


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
-- Name: areg_temp_subdivclub areg_temp_subdivclub_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.areg_temp_subdivclub
    ADD CONSTRAINT areg_temp_subdivclub_pkey PRIMARY KEY (row_id);


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
-- Name: chitta_temp_old_owner chitta_temp_old_owner_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chitta_temp_old_owner
    ADD CONSTRAINT chitta_temp_old_owner_pkey PRIMARY KEY (row_id);


--
-- Name: chitta_temp_subdivclub chitta_temp_subdivclub_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chitta_temp_subdivclub
    ADD CONSTRAINT chitta_temp_subdivclub_pkey PRIMARY KEY (row_id);


--
-- Name: correction_modification_flow correction_modification_flow_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.correction_modification_flow
    ADD CONSTRAINT correction_modification_flow_pkey PRIMARY KEY (row_id);


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
-- Name: f_line_service_application_information_urban f_line_service_application_information_urban_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.f_line_service_application_information_urban
    ADD CONSTRAINT f_line_service_application_information_urban_pkey PRIMARY KEY (row_id);


--
-- Name: f_line_service_urban f_line_service_urban_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.f_line_service_urban
    ADD CONSTRAINT f_line_service_urban_pkey PRIMARY KEY (row_id);


--
-- Name: field_visits field_visits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.field_visits
    ADD CONSTRAINT field_visits_pkey PRIMARY KEY (id);


--
-- Name: file_attachment file_attachment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_attachment
    ADD CONSTRAINT file_attachment_pkey PRIMARY KEY (row_id);


--
-- Name: full_field_patta_transfer_application_information full_field_patta_transfer_application_information_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_application_information
    ADD CONSTRAINT full_field_patta_transfer_application_information_pkey PRIMARY KEY (row_id);


--
-- Name: full_field_patta_transfer_igrs_owner full_field_patta_transfer_igrs_owner_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_igrs_owner
    ADD CONSTRAINT full_field_patta_transfer_igrs_owner_pkey PRIMARY KEY (row_id);


--
-- Name: full_field_patta_transfer_new_owner full_field_patta_transfer_new_owner_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_new_owner
    ADD CONSTRAINT full_field_patta_transfer_new_owner_pkey PRIMARY KEY (row_id);


--
-- Name: full_field_patta_transfer_old_owner full_field_patta_transfer_old_owner_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_old_owner
    ADD CONSTRAINT full_field_patta_transfer_old_owner_pkey PRIMARY KEY (row_id);


--
-- Name: full_field_patta_transfer_return_owner full_field_patta_transfer_return_owner_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_return_owner
    ADD CONSTRAINT full_field_patta_transfer_return_owner_pkey PRIMARY KEY (row_id);


--
-- Name: full_field_patta_transfer_urban full_field_patta_transfer_urban_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.full_field_patta_transfer_urban
    ADD CONSTRAINT full_field_patta_transfer_urban_pkey PRIMARY KEY (row_id);


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
-- Name: sd_attachment sd_attachment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sd_attachment
    ADD CONSTRAINT sd_attachment_pkey PRIMARY KEY (row_id);


--
-- Name: service_register_entries service_register_entries_application_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_register_entries
    ADD CONSTRAINT service_register_entries_application_number_key UNIQUE (application_number);


--
-- Name: service_register_entries service_register_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_register_entries
    ADD CONSTRAINT service_register_entries_pkey PRIMARY KEY (id);


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
-- Name: sub_div_patta_transfer_application_information_urban sub_div_patta_transfer_application_information_urban_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sub_div_patta_transfer_application_information_urban
    ADD CONSTRAINT sub_div_patta_transfer_application_information_urban_pkey PRIMARY KEY (row_id);


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
-- Name: towns towns_taluk_id_town_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.towns
    ADD CONSTRAINT towns_taluk_id_town_code_key UNIQUE (taluk_id, town_code);


--
-- Name: tslr_sketch_urban tslr_sketch_urban_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tslr_sketch_urban
    ADD CONSTRAINT tslr_sketch_urban_pkey PRIMARY KEY (row_id);


--
-- Name: uareg_modification uareg_modification_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uareg_modification
    ADD CONSTRAINT uareg_modification_pkey PRIMARY KEY (row_id);


--
-- Name: uchitta_natham_modification uchitta_natham_modification_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uchitta_natham_modification
    ADD CONSTRAINT uchitta_natham_modification_pkey PRIMARY KEY (row_id);


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
-- Name: idx_active_app_per_survey; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_active_app_per_survey ON public.applications USING btree (survey_number_id) WHERE ((current_status)::text = ANY ((ARRAY['pending'::character varying, 'in_progress'::character varying, 'escalated'::character varying])::text[]));


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
-- Name: idx_service_register_service_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_service_register_service_code ON public.service_register_entries USING btree (service_code);


--
-- Name: idx_service_register_ward; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_service_register_ward ON public.service_register_entries USING btree (ward_id);


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
-- Name: idx_workflow_app; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workflow_app ON public.workflow_history USING btree (application_id);


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
-- Name: officer_jurisdictions officer_jurisdictions_district_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.officer_jurisdictions
    ADD CONSTRAINT officer_jurisdictions_district_id_fkey FOREIGN KEY (district_id) REFERENCES public.district_unicode(app_uid);


--
-- Name: officer_jurisdictions officer_jurisdictions_officer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.officer_jurisdictions
    ADD CONSTRAINT officer_jurisdictions_officer_id_fkey FOREIGN KEY (officer_id) REFERENCES public.sis_officers(id);


--
-- Name: officer_jurisdictions officer_jurisdictions_taluk_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.officer_jurisdictions
    ADD CONSTRAINT officer_jurisdictions_taluk_id_fkey FOREIGN KEY (taluk_id) REFERENCES public.taluk(app_uid);


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
-- Name: service_register_entries service_register_entries_block_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_register_entries
    ADD CONSTRAINT service_register_entries_block_id_fkey FOREIGN KEY (block_id) REFERENCES public.blocks(id);


--
-- Name: service_register_entries service_register_entries_ward_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_register_entries
    ADD CONSTRAINT service_register_entries_ward_id_fkey FOREIGN KEY (ward_id) REFERENCES public.wards(id);


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
-- Name: towns towns_taluk_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.towns
    ADD CONSTRAINT towns_taluk_id_fkey FOREIGN KEY (taluk_id) REFERENCES public.taluk(app_uid);


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

\unrestrict 7GD7GHi4f0f3vLrKeavkbRr3ytHcYs7udG7uHCXzbcQ6IcqknaQVgahLgKgXwhN

