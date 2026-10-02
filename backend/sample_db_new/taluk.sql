--
-- PostgreSQL database dump
--

-- Dumped from database version 11.1
-- Dumped by pg_dump version 14.8 (Ubuntu 14.8-0ubuntu0.22.04.1)

-- Started on 2026-09-02 18:51:23 IST

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
-- TOC entry 449 (class 1259 OID 123160973)
-- Name: taluk; Type: TABLE; Schema: public; Owner: postgres
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
    ddo_code character varying(10)
);


ALTER TABLE public.taluk OWNER TO postgres;

--
-- TOC entry 855 (class 1259 OID 142523903)
-- Name: taluk_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.taluk_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.taluk_id_seq OWNER TO postgres;

--
-- TOC entry 7934 (class 0 OID 0)
-- Dependencies: 855
-- Name: taluk_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.taluk_id_seq OWNED BY public.taluk.id;


--
-- TOC entry 7712 (class 2604 OID 142523905)
-- Name: taluk id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.taluk ALTER COLUMN id SET DEFAULT nextval('public.taluk_id_seq'::regclass);


--
-- TOC entry 7926 (class 0 OID 123160973)
-- Dependencies: 449
-- Data for Name: taluk; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.taluk (district_code, taluk_code, taluk_name, taluk_ename, updn_dt, user_no, id, to_code, ddo_code) FROM stdin;
02	02	Purasawalkam--Test--	Purasawalkam--Test--	2014-07-28 10:55:46	\N	69	4201	42010060
30	04	Vilavancode--Test--	Vilavancode--Test--	2013-12-13 00:00:00	0	12	\N	\N
34	12	KADAYANALLUR--Test--	KADAYANALLUR--Test--	2014-09-16 13:59:55	0	51	3402	34020022
35	04	Chengalpattu--Test--	Chengalpattu--Test--	2006-09-20 15:48:00	1	52	3502	35020015
35	07	Maduramdagam--Test--	Maduramdagam--Test--	2006-09-20 15:48:00	1	53	3504	35040006
01	12	Avadi--Test--	Avadi--Test--	2017-04-17 00:00:00	1	18	1803	18030007
01	07	Tiruvallur--Test--	Tiruvallur--Test--	2014-07-28 10:55:46	0	32	1809	18090046
02	01	Tondiarpet--Test--	Tondiarpet--Test--	\N	\N	68	4201	42010049
04	08	காட்பாடி	Katpadi	2025-09-02 16:21:56.228039	0	133	\N	\N
23	11	சிவகங்கை	Sivagangai          	2006-09-20 15:48:00	1	0	1306	13060007
23	21	தேவகோட்டை	Devakottai          	2006-01-01 03:34:36	0	0	1302	13020006
23	22	காரைக்குடி	Karaikudi           	2014-08-05 12:50:42	0	0	1304	13040008
02	03	Perambur--Test--	Perambur--Test--	\N	\N	70	4201	42010048
02	04	Ayanavaram--Test--	Ayanavaram--Test--	\N	\N	71	4201	42010062
02	08	Mylapore--Test--	Mylapore--Test--	\N	\N	65	4101	41010015
02	05	Aminjikarai--Test--	Aminjikarai--Test--	\N	\N	72	4101	41010030
02	10	Velachery--Test--	Velachery--Test--	\N	\N	67	4101	41010027
02	09	Guindy--Test--	Guindy--Test--	\N	\N	66	4101	41010025
02	06	Egmore--Test--	Egmore--Test--	\N	\N	73	4101	41010018
02	16	Thiruvottiyur--Test--	Thiruvottiyur--Test--	2015-07-14 10:42:06	0	50	1806	18060082
05	01	Dharmapuri--Test--	Dharmapuri--Test--	2014-06-24 13:08:15	0	34	0302	03020019
06	01	Tiruvannamalai--Test--	Tiruvannamalai--Test--	2013-07-09 00:00:00	1	46	1606	16060025
06	06	Vandavasi--Test--	Vandavasi--Test--	2014-08-06 13:26:37	0	13	1607	16070050
06	04	cheyyar--Test--	cheyyar--Test--	2014-07-10 18:10:18	0	25	1604	16040092
06	05	Arni--Test--	Arni--Test--	2014-02-12 13:58:24	0	39	1602	16020081
07	01	Viluppuram--Test--	Viluppuram--Test--	2014-01-06 11:29:15	0	17	2409	24090079
07	03	Tindivanam--Test--	Tindivanam--Test--	2014-05-28 17:03:16	0	57	2405	24050092
08	09	Mettur--Test--	Mettur--Test--	\N	\N	36	1403	14030028
08	12	Salem South--Test--	Salem South--Test--	2014-06-30 16:38:58	0	75	1405	14050061
08	01	Salem--Test--	Salem--Test--	\N	\N	78	1405	14050085
08	11	Salem West--Test--	Salem West--Test--	2014-06-30 16:38:58	0	76	1406	14060043
09	01	Namakkal--Test--	Namakkal--Test--	2014-08-01 02:01:03	0	24	2702	27020061
09	03	Thiruchencode--Test--	Thiruchencode--Test--	2014-08-08 13:41:05	0	80	2705	27050072
11	02	Coonoor--Test--	Coonoor--Test--	2014-07-01 17:49:37	0	42	2102	21020008
12	03	Pollachi--Test--	Pollachi--Test--	2014-08-19 17:25:46	0	6	0108	01080065
12	01	Coimbatore (S)--Test--	Coimbatore (S)--Test--	2015-04-24 11:57:13	0	20	0103	01030057
12	02	Coimbatore (N)--Test--	Coimbatore (N)--Test--	2015-04-24 11:57:13	0	21	0104	01040035
12	04	Mettupalayam--Test--	Mettupalayam--Test--	2014-07-17 13:04:14	0	74	0106	01060012
14	03	KULITHALAI--Test--	KULITHALAI--Test--	\N	\N	77	2804	28040029
14	01	KARUR--Test--	KARUR--Test--	\N	\N	10	2803	28030004
15	05	Thuraiyur--Test--	Thuraiyur--Test--	2014-08-06 13:09:50	0	16	1507	15070023
15	01	Tiruchirappalli  West--Test--	Tiruchirappalli  West--Test--	2014-08-26 13:02:20	0	33	1506	15060031
15	07	Srirangam--Test--	Srirangam--Test--	2014-08-07 12:15:14	0	37	1505	15050003
15	03	Manapparai--Test--	Manapparai--Test--	2014-09-20 12:45:21	0	38	1503	15030019
18	03	Chidambaram--Test--	Chidambaram--Test--	2014-11-21 15:36:51	0	40	0202	02020050
19	01	Nagapattinam--Test--	Nagapattinam--Test--	\N	0	15	1002	10020043
20	01	Thiruvarur--Test--	Thiruvarur--Test--	\N	\N	14	2602	26020057
20	08	Koothanallur--Test--	Koothanallur--Test--	2017-09-06 15:55:09	0	19	2605	26050033
20	06	Mannargudi--Test--	Mannargudi--Test--	\N	\N	45	2604	26040011
20	07	Thiruthuraipoondi--Test--	Thiruthuraipoondi--Test--	\N	\N	48	2603	26030035
21	01	THANJAVUR--Test--	THANJAVUR--Test--	2014-07-16 11:14:03	0	29	1902	19020015
21	05	KUMBAKONAM--Test--	KUMBAKONAM--Test--	2014-07-16 11:33:32	0	83	1904	19040026
22	02	Aranthangi--Test--	Aranthangi--Test--	2013-09-16 11:39:31	1	30	1103	11030007
26	04	SIVAKASI--Test--	SIVAKASI--Test--	2017-12-05 11:27:05	1	31	2204	22040066
26	06	ARUPPUKOTTAI--Test--	ARUPPUKOTTAI--Test--	2018-01-01 11:04:29	1	44	2202	22020042
28	01	Thoothukudi--Test--	Thoothukudi--Test--	2014-07-01 17:39:35	0	82	2007	20070009
28	06	Kovilpatti--Test--	Kovilpatti--Test--	2014-09-05 22:29:33	0	35	2002	20020027
29	01	TIRUNELVELI--Test--	TIRUNELVELI--Test--	2014-09-16 13:59:55	0	61	1711	17110032
29	02	PALAYAMKOTTAI--Test--	PALAYAMKOTTAI--Test--	2014-09-16 13:59:55	0	81	1705	17050019
30	01	Agastheeswaram--Test--	Agastheeswaram--Test--	\N	\N	84	0905	09050004
30	03	Kalkulam--Test--	Kalkulam--Test--	2013-12-13 16:32:50	0	28	0903	09030008
38	05	Mailaduthurai--Test--	Mailaduthurai--Test--	2014-07-01 01:52:17	0	1	3802	38020052
38	06	Sirkazhi--Test--	Sirkazhi--Test--	2014-07-01 02:11:27	0	64	3803	38030013
02	17	கொளத்தூர்	Kolathur	2024-09-26 10:45:16.724227	\N	131	\N	\N
02	07	Mambalam--Test--	Mambalam--Test--	\N	\N	27	4101	41010013
15	11	Tiruchirappalli  East--Test--	Tiruchirappalli  East--Test--	2014-08-26 13:02:20	0	26	1506	15060036
25	11	Theni--Test--	Theni--Test--	2014-07-14 13:07:53	0	125	2504	25040006
25	12	Bodinayakkanur--Test--	Bodinayakkanur--Test--	2014-08-29 16:54:03	0	85	2505	25050006
35	13	Pallavaram--Test--	Pallavaram--Test--	2006-01-01 04:53:36	0	106	3508	35080023
22	15	Alangudi--Test--	Alangudi--Test--	\N	\N	128	1102	11020003
26	03	SATTUR--Test--	SATTUR--Test--	2012-07-12 14:10:57	64	87	2206	22060006
27	24	Paramakudi--Test--	Paramakudi--Test--	2014-12-24 04:26:13	0	124	1205	12050007
25	15	Periyakulam--Test--	Periyakulam--Test--	2014-08-28 12:44:21	0	123	2502	25020007
27	11	Ramanathapuram--Test--	Ramanathapuram--Test--	\N	\N	47	1206	12060052
25	14	Uthamapalayam--Test--	Uthamapalayam--Test--	2014-08-28 14:55:54	0	23	2503	25030073
35	05	Tambaram--Test--	Tambaram--Test--	2013-06-09 01:28:19	0	107	3506	35060005
01	06	Tiruttani--Test--	Tiruttani--Test--	2019-07-11 10:50:50	1	101	1808	18080048
02	15	மாதவரம்(--மாதிரி--)	Madhavaram--Test--	2015-01-30 10:42:06	0	92	1811	18110019
02	14	Ambattur--Test--	Ambattur--Test--	2014-09-06 10:55:46	0	99	1802	18020035
36	09	Ambur--Test--	Ambur--Test--	2014-10-07 19:30:35	0	111	3604	36040018
02	11	Shozhinganallur--Test--	Shozhinganallur--Test--	2019-09-16 13:23:45	\N	102	3507	35070021
03	01	Kancheepuram--Test--	Kancheepuram--Test--	2014-07-25 16:13:42	0	117	0604	06040036
04	01	Vellore--Test--	Vellore--Test--	2014-07-10 00:00:00	1	119	2309	23090003
04	05	Gudiyatham--Test--	Gudiyatham--Test--	2014-07-10 00:00:00	1	115	2304	23040008
08	07	Edapadi--Test--	Edapadi--Test--	\N	\N	86	1411	14110020
08	04	Attur--Test--	Attur--Test--	\N	\N	130	1402	14020040
09	02	RASIPURAM--Test--	RASIPURAM--Test--	2014-07-30 10:26:28	0	112	2704	27040008
09	07	Kumarapalayam--Test--	Kumarapalayam--Test--	2014-08-08 13:41:05	0	116	2705	27050073
11	01	UDHAGAI--Test--	UDHAGAI--Test--	2014-07-09 17:21:23	0	121	2105	21050003
12	13	PERUR--Test--	PERUR--Test--	2015-04-24 11:57:13	0	22	0103	01030072
16	03	PERAMBALUR--Test--	PERAMBALUR--Test--	2019-07-04 00:00:00	0	100	2902	29020017
18	01	Cuddalore--Test--	Cuddalore--Test--	\N	\N	90	0203	02030019
18	02	Panruti--Test--	Panruti--Test--	\N	\N	91	0208	02080050
18	05	Virudhachalam--Test--	Virudhachalam--Test--	\N	\N	93	0204	02040073
21	07	PATTUKKOTAI--Test--	PATTUKKOTAI--Test--	2016-06-01 17:25:17	0	120	1905	19050008
22	04	Thirumayam--Test--	Thirumayam--Test--	\N	\N	126	1108	11080003
22	05	Ponnamaravathi--Test--	Ponnamaravathi--Test--	\N	\N	127	1111	11110003
22	16	Karambakudi--Test--	Karambakudi--Test--	\N	\N	129	1112	11120002
26	02	SRIVILLIPUTHUR--Test--	SRIVILLIPUTHUR--Test--	2012-07-12 14:10:57	64	88	2205	22050040
26	01	RAJAPALAIAM--Test--	RAJAPALAIAM--Test--	2012-07-12 14:10:57	64	89	2203	22030004
26	05	VIRUDHUNAGAR--Test--	VIRUDHUNAGAR--Test--	2012-07-12 14:10:57	64	94	2208	22080058
31	05	Krishnagiri--Test--	Krishnagiri--Test--	2014-07-09 15:51:44	0	11	3004	30040045
31	09	Hosur--Test--	Hosur--Test--	2014-07-20 00:00:00	1	60	3003	30030043
32	06	Udumalaipet--Test--	Udumalaipet--Test--	2000-02-07 00:00:00	1	2	3207	32070008
32	04	DHARAPURAM--Test--	DHARAPURAM--Test--	2000-02-07 00:00:00	1	3	3203	32030013
32	01	TIRUPPUR NORTH--Test--	TIRUPPUR NORTH--Test--	2014-07-03 10:45:21	0	8	3206	32060048
32	08	TIRUPPUR SOUTH--Test--	TIRUPPUR SOUTH--Test--	2014-07-03 10:45:21	0	9	3206	32060052
34	04	TENKASI--Test--	TENKASI--Test--	2014-09-16 13:59:55	0	103	3402	34020028
13	32	Dindigul West--Test--	Dindigul West--Test--	\N	\N	114	0402	04020073
24	12	MADURAI SOUTH--Test--	MADURAI SOUTH--Test--	2014-07-14 19:33:46	0	95	0703	07030003
10	11	Erode--Test--	Erode--Test--	2000-01-22 00:00:00	1	56	0504	05040067
24	17	THIRUPPARANKUNDRAM--Test--	THIRUPPARANKUNDRAM--Test--	2014-07-14 19:33:46	0	97	0703	07030008
24	16	MADURAI WEST--Test--	MADURAI WEST--Test--	2014-07-14 19:33:46	0	96	0703	07030007
34	03	SANKARANKOIL--Test--	SANKARANKOIL--Test--	2014-09-16 13:59:55	0	104	3403	34030034
34	06	SHENKOTTAI--Test--	SHENKOTTAI--Test--	2014-09-16 13:59:55	0	105	3404	34040002
36	07	Tirupattur--Test--	Tirupattur--Test--	2014-07-10 00:00:00	1	109	3602	36020059
36	06	Vaniyambadi--Test--	Vaniyambadi--Test--	2014-07-10 00:00:00	1	110	3603	36030004
37	04	Walajapet--Test--	Walajapet--Test--	2014-03-10 11:08:36	0	54	3702	37020025
37	03	Arakkonam--Test--	Arakkonam--Test--	2014-07-10 00:00:00	1	55	3703	37030075
37	02	Arcot--Test--	Arcot--Test--	2014-06-20 15:27:39	0	108	3704	37040007
24	13	MELUR--Test--	MELUR--Test--	2014-07-14 19:33:46	0	43	0704	07040003
10	31	Gobichettipalaym--Test--	Gobichettipalaym--Test--	\N	\N	5	0505	05050084
24	21	USILAMPATTI--Test--	USILAMPATTI--Test--	2014-07-14 19:33:46	0	62	0707	07070037
24	23	THIRUMANGALAM--Test--	THIRUMANGALAM--Test--	2014-07-14 19:33:46	0	118	0705	07050016
10	33	Bhavani--Test--	Bhavani--Test--	2000-01-22 00:00:00	1	7	0503	05030048
13	31	Kodaikanal--Test--	Kodaikanal--Test--	\N	1	79	0403	04030037
10	32	Sathyamangalam--Test--	Sathyamangalam--Test--	2000-01-22 00:00:00	1	41	0508	05080066
24	11	MADURAI NORTH--Test--	MADURAI NORTH--Test--	2014-07-14 19:33:46	0	122	0702	07020045
13	11	Dindigul  East--Test--	Dindigul  East--Test--	\N	\N	63	0402	04020002
13	21	Palani--Test--	Palani--Test--	\N	\N	4	0407	04070032
02	12	ஆலந்தூர்	Alandur_Test	2015-06-01 00:00:00	\N	113	3508	35080022
22	01	Pudukkottai	Pudukkottai--Test--	2015-02-23 14:52:17	0	98	1107	11070007
\.


--
-- TOC entry 7936 (class 0 OID 0)
-- Dependencies: 855
-- Name: taluk_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.taluk_id_seq', 133, true);


--
-- TOC entry 7714 (class 2606 OID 142523913)
-- Name: taluk taluk_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.taluk
    ADD CONSTRAINT taluk_pkey PRIMARY KEY (district_code, taluk_code);


--
-- TOC entry 7715 (class 2606 OID 142582167)
-- Name: taluk FKohac1diklw3t6qi0q0qf0vcp0; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.taluk
    ADD CONSTRAINT "FKohac1diklw3t6qi0q0qf0vcp0" FOREIGN KEY (district_code) REFERENCES public.district_unicode(district_code);


--
-- TOC entry 7933 (class 0 OID 0)
-- Dependencies: 449
-- Name: TABLE taluk; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT ON TABLE public.taluk TO postgrest_auth;
GRANT SELECT ON TABLE public.taluk TO murugesh;


--
-- TOC entry 7935 (class 0 OID 0)
-- Dependencies: 855
-- Name: SEQUENCE taluk_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,USAGE ON SEQUENCE public.taluk_id_seq TO murugesh;


-- Completed on 2026-09-02 18:51:24 IST

--
-- PostgreSQL database dump complete
--

