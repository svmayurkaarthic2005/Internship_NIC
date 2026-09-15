--
-- PostgreSQL database dump
--

-- Dumped from database version 11.1
-- Dumped by pg_dump version 14.8 (Ubuntu 14.8-0ubuntu0.22.04.1)

-- Started on 2026-09-02 18:50:41 IST

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

--
-- TOC entry 318 (class 1259 OID 123160307)
-- Name: district_unicode; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.district_unicode (
    state_code character varying(2),
    district_code character varying(255) NOT NULL,
    district_tname character varying(255),
    district_name character varying(255),
    district_sname character varying(3),
    taluk_district_code character varying(255)
);


ALTER TABLE public.district_unicode OWNER TO postgres;

--
-- TOC entry 7924 (class 0 OID 123160307)
-- Dependencies: 318
-- Data for Name: district_unicode; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.district_unicode (state_code, district_code, district_tname, district_name, district_sname, taluk_district_code) FROM stdin;
33	38	மயிலாடுதுறை	Mailaduthurai	MLD	\N
33	02	சென்னை(மாதிரி)	Chennai(Test)	CHN	\N
33	01	திருவள்ளுர(மாதிரி)	Tiruvallur(Test)	TLR	\N
33	14	கரூர்(மாதிரி)	Karur(Test)	KAR	\N
22	03	காஞ்சிபுரம்(மாதிரி)	Kancheepuram(Test)	kpm	\N
33	32	திருப்பூர்(மாதிரி)	TIRUPPUR(Test)	TPR	\N
33	13	திண்டுக்கல்(மாதிரி)	Dindigul(Test)	DGL	\N
33	10	ஈரோடு(மாதிரி)	Erode(Test)	erd	\N
33	04	வேலூர்(மாதிரி)	Vellore(Test)	VEL	\N
33	31	கிருஷ்ணகிரி(மாதிரி)	Krishnagiri(Test)	KGI	\N
33	30	கன்னியாகுமரி(மாதிரி)	Kanniyakumari(Test)	KKM	\N
33	20	திருவாரூர்(மாதிரி)	Thiruvarur(Test)	TVR	\N
33	19	நாகப்பட்டினம்(மாதிரி)	Nagapattinam(Test)	ngp	\N
33	15	திருச்சிராப்பள்ளி(மாதிரி)	Tiruchirappalli(Test)	TRY	\N
33	07	விழுப்புரம்(மாதிரி)	VILUPPURAM(Test)	VPM	\N
33	25	தேனி(மாதிரி)	Theni(Test)	thn	\N
33	29	திருநெல்வேலி(மாதிரி)	Tirunelveli(Test)	tnv	\N
33	21	தஞ்சாவூர்(மாதிரி)	THANJAVUR(Test)	TNJ	\N
33	08	சேலம்(மாதிரி)	Salem(Test)	slm	\N
33	26	விருதுநகர்(மாதிரி)	Virudhunagar(Test)	VNR	\N
33	05	தருமபுரி(மாதிரி)	Dharmapuri(Test)	DPI	\N
33	28	தூத்துக்குடி(மாதிரி)	Thoothukudi(Test)	tut	\N
33	18	கடலூர்(மாதிரி)	Cuddalore(Test)	CUD	\N
33	11	நீலகிரி(மாதிரி)	The Nilgiris(Test)	NLG	\N
33	27	இராமநாதபுரம்(மாதிரி)	Ramanathapuram(Test)	RMD	\N
33	06	திருவண்ணாமலை(மாதிரி)	Tiruvannamalai(Test)	TVM	\N
33	09	நாமக்கல்(மாதிரி)	Namakkal(Test)	nmk	\N
33	23	சிவகங்கை(மாதிரி)	Sivagangai(Test)	svg	\N
33	24	மதுரை(மாதிரி)	Madurai(Test)	MDU	\N
33	12	கோயம்புத்தூர்(மாதிரி)	Coimbatore(Test)	CBE	\N
33	34	தென்காசி(மாதிரி)	TENKASI(Test)	TKS	\N
33	35	செங்கல்பட்டு(மாதிரி)	Chengalpattu(Test)	CPT	\N
33	37	இராணிப்பேட்டை(மாதிரி)	Ranipet(Test)	RPT	\N
33	36	திருப்பத்தூர்(மாதிரி)	Thirupattur(Test)	TPT	\N
33	16	PERAMBALUR--Test--	PERAMBALUR--Test--	PRL	\N
33	22	புதுக்கோட்டை	Pudukkottai	PDK	\N
\.


--
-- TOC entry 7713 (class 2606 OID 142524504)
-- Name: district_unicode district_unicode_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.district_unicode
    ADD CONSTRAINT district_unicode_pkey PRIMARY KEY (district_code);


--
-- TOC entry 7930 (class 0 OID 0)
-- Dependencies: 318
-- Name: TABLE district_unicode; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT ON TABLE public.district_unicode TO PUBLIC;
GRANT SELECT ON TABLE public.district_unicode TO postgrest_auth;
GRANT SELECT ON TABLE public.district_unicode TO murugesh;


-- Completed on 2026-09-02 18:50:42 IST

--
-- PostgreSQL database dump complete
--

