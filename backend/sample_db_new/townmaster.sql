--
-- PostgreSQL database dump
--

-- Dumped from database version 14.1
-- Dumped by pg_dump version 14.8 (Ubuntu 14.8-0ubuntu0.22.04.1)

-- Started on 2025-04-05 17:57:22 IST

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

SET default_table_access_method = heap;

--
-- TOC entry 857 (class 1259 OID 1979306)
-- Name: town; Type: TABLE; Schema: public; Owner: postgres
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


ALTER TABLE public.town OWNER TO postgres;

--
-- TOC entry 4975 (class 0 OID 1979306)
-- Dependencies: 857
-- Data for Name: town; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.town (district_code, taluk_code, town_code, town_name, town_ename, town_flag, user_no, updn_dt, igrs_mutation, igrs_am_date, town_status, nic_dsign) FROM stdin;
20	08	001	கூத்தாநல்லூர்	Koothanallur             	N	0	2006-01-01 01:19:40	Y	2022-05-31	M	\N
12	04	001	மேட்டுப்பாளையம்          	Mettupalayam             	N	0	2014-07-16 11:31:31	Y	2022-05-31	M	\N
37	03	001	அரக்கோணம்	Arakkonam                	N	0	2014-09-02 11:55:53	Y	2022-05-31	M	\N
12	03	002	பொள்ளாச்சி	Pollachi                 	N	0	2014-08-19 17:19:38	Y	2022-05-31	M	\N
19	01	001	நாகப்பட்டினம்	Nagapattinam             	N	0	\N	Y	2022-05-31	M	\N
37	04	002	இராணிப்பேட்டை	Ranipet                  	N	0	2014-03-10 11:09:32	Y	2022-05-31	M	\N
05	01	001	தருமபுரி	Dharmapuri               	S	0	2022-06-14 13:04:04	Y	2024-08-28	M	\N
13	21	001	பழனி                                              	Palani                   	S	0	2014-07-22 13:47:50	Y	2024-08-28	M	\N
01	12	001	ஆவடி                                              	Avadi                    	N	1	2017-04-17 00:00:00	Y	2022-05-31	C	\N
06	06	001	வந்தவாசி	Vandavasi                	S	0	2014-08-06 13:26:26	Y	2024-08-28	M	\N
02	17	001	கொளத்தூர்	Kolathur 	N	0	\N	Y	2022-04-07	C	\N
02	17	002	பெருவள்ளர்	Peruvallur	N	0	\N	Y	2022-04-07	C	\N
02	17	003	சிறுவள்ளர்	Siruvallur	N	0	\N	Y	2022-04-07	C	\N
03	01	001	காஞ்சிபுரம்                                       	Kancheepuram             	N	0	2014-07-25 16:02:44	Y	2022-05-31	C	\N
02	15	001	மாதவரம்                                           	Madhavaram               	N	1	2015-01-30 10:46:48	Y	2022-04-07	C	\N
02	16	001	திருவொற்றியூர்                                    	Thiruvottiyur            	N	1	2015-07-14 10:46:48	Y	2022-04-07	C	\N
07	01	001	விழுப்புரம்	Vilupuram	N	0	2014-01-06 11:36:38	Y	2022-05-31	M	\N
20	01	001	திருவாரூர்	Thiruvarur               	N	0	2006-01-01 01:45:20	Y	2022-05-31	M	\N
25	14	005	கம்பம்	Cumbum                   	N	0	2014-08-28 14:54:28	Y	2022-05-31	M	\N
28	06	001	கோவில்பட்டி	Kovilpatti               	N	0	2014-09-05 22:29:23	Y	2022-05-31	M	\N
35	07	006	மதுராந்தகம்                                       	Madurandagam             	N	0	2014-07-18 13:46:03	Y	2022-05-31	M	\N
20	06	001	மன்னார்குடி	Mannargudi               	N	0	2014-09-26 15:17:10	Y	2022-05-31	M	\N
02	14	001	அம்பத்தூர்                                        	Ambattur                 	N	1	2014-09-06 10:46:48	Y	2022-04-07	C	\N
02	03	001	ஜம்புலி	Jambuli	N	0	\N	Y	2022-04-07	C	\N
02	03	002	சேலவாயல்	Selavoyal	N	0	\N	Y	2022-04-07	C	\N
02	03	003	கொடுங்கையுர்	Kondungaiyur	N	0	\N	Y	2022-04-07	C	\N
02	03	004	எருக்கஞ்சேரி	Erukkencheri	N	0	\N	Y	2022-04-07	C	\N
02	03	006	செம்பியம்	Sembium	N	0	\N	Y	2022-04-07	C	\N
02	04	004	சின்ன செம்பரம்பாக்கம்	Chinna Sembarambakkam	N	0	\N	Y	2022-04-07	C	\N
04	01	001	வேலூர்(வடக்கு)	Vellore(North)           	N	0	2014-07-11 17:00:07	Y	2022-05-31	C	\N
04	01	002	வேலூர்(தெற்கு)	Vellore(South)           	N	0	2014-07-11 17:00:54	Y	2022-05-31	C	\N
09	03	001	திருச்செங்கோடு                                    	Thiruchencode            	N	0	2014-08-08 13:42:00	Y	2022-05-31	M	\N
27	11	001	இராமநாதபுரம்	Ramanathapuram           	N	0	2013-12-03 12:15:40	Y	2022-05-31	M	\N
20	07	001	திருத்துறைப்பூண்டி	Thiruthuraipoondi        	N	0	2014-09-26 16:03:43	Y	2022-05-31	M	\N
04	05	001	குடியாத்தம்	Gudiyatham               	N	0	2014-09-18 17:57:32	Y	2022-05-31	M	\N
07	03	001	திண்டிவனம்	Tindivanam               	N	0	2010-04-01 00:00:00	Y	2022-05-31	M	\N
34	12	006	கடையநல்லூர்	Kadayanallur             	N	0	2014-09-16 13:42:33	Y	2022-05-31	M	\N
02	12	001	ஆலந்துார்                                         	Alandur                  	N	0	2015-01-06 13:31:24	Y	2022-04-07	C	\N
02	04	005	கொன்னூர்	Konnur	N	0	\N	Y	2022-04-07	C	\N
02	04	006	மல்லிகைசேரி	Mallikaicheri	N	0	\N	Y	2022-04-07	C	\N
02	04	007	அயனாவரம் பகுதி 1	Ayanavaram (Part 1)	N	0	\N	Y	2022-04-07	C	\N
02	11	001	செம்மஞ்சேரி	Semmancheri	N	0	2019-09-16 13:23:25	Y	2022-04-07	C	\N
02	05	003	பெரியகூடல்	Periyakudal	N	0	\N	Y	2022-04-07	C	\N
02	05	004	சின்னகூடல்	Chinnakudal	N	0	\N	Y	2022-04-07	C	\N
02	05	005	திருமங்கலம்	Thirumangalam	N	0	\N	Y	2022-04-07	C	\N
02	05	006	நடுவாங்கரை	Naduvankarai	N	0	\N	Y	2022-04-07	C	\N
02	05	007	கோயம்பேடு	Koyembedu	N	0	\N	Y	2022-04-07	C	\N
02	05	008	செஞ்சேரி	Sencheri	N	0	\N	Y	2022-04-07	C	\N
02	05	009	அமைந்தகரை	Aminjikarai	N	0	\N	Y	2022-04-07	C	\N
02	05	010	வடஅகரம்	Vada Agaram	N	0	\N	Y	2022-04-07	C	\N
02	05	011	அரும்பாக்கம்	Arumbakkam	N	0	\N	Y	2022-04-07	C	\N
02	07	002	கோடம்பாக்கம்  பகுதி 1	Kodambakkam (Part 1)	N	0	\N	Y	2022-04-07	C	\N
02	07	004	நெசப்பாக்கம்	Nesappakkam	N	0	\N	Y	2022-04-07	C	\N
02	07	005	கோடம்பாக்கம்  பகுதி 2	Kodambakkam (Part 2)	N	0	\N	Y	2022-04-07	C	\N
02	07	007	சைதாப்பேட்டை	Saidapet	N	0	\N	Y	2022-04-07	C	\N
02	09	001	ஈக்காட்டுதாங்கல்	Ekkatuthangal	N	0	\N	Y	2022-04-07	C	\N
02	09	002	ஆலந்தூர்	Alandur	N	0	\N	Y	2022-04-07	C	\N
02	09	003	அடையாறு பகுதி 1	Adayar (Part 1)	N	0	\N	Y	2022-04-07	C	\N
02	09	005	அரசுபண்ணை	Govt.Farm	N	0	\N	Y	2022-04-07	C	\N
02	09	007	அடையாறு பகுதி 2	Adayar (Part 2)	N	0	\N	Y	2022-04-07	C	\N
02	09	008	கிண்டிபார்க்	Guindy Park	N	0	\N	Y	2022-04-07	C	\N
02	09	009	கோட்டுர்	Kottur	N	0	\N	Y	2022-04-07	C	\N
02	08	002	திருவல்லிக்கேணி பகுதி 1	Triplicane (Part 1)	O	0	\N	Y	2021-08-10	C	\N
02	08	003	திருவல்லிக்கேணி பகுதி 2	Triplicane (Part 2)	O	0	\N	Y	2021-08-10	C	\N
15	01	001	திருச்சிராப்பள்ளி மாநகராட்சி -கோ.அபிஷேகபுரம் பகுதி	Tiruchirappalli City Corp. -Ko.Abhisekapuram  Zone	N	0	2014-08-26 12:39:49	Y	2022-05-31	C	\N
15	01	002	திருச்சிராப்பள்ளி மாநகராட்சி- பொன்மலை பகுதி       	Tiruchirappalli City Corp. -Ponmalai Zone         	N	0	2014-08-26 12:39:49	Y	2022-05-31	C	\N
15	07	001	 திருச்சிராப்பள்ளி மாநகராட்சி- ஸ்ரீரங்கம்  பகுதி  	Srirangam                	N	0	2014-08-19 16:50:40	Y	2022-05-31	C	\N
21	01	001	தஞ்சாவுர்	Thanjavur                	N	0	2014-07-10 15:31:57	Y	2021-09-16	C	\N
26	04	004	சிவகாசி                                           	Sivakasi                 	N	1	2017-12-05 11:06:31	Y	2021-09-16	C	\N
06	01	001	திருவண்ணாமலை	Tiruvannamalai           	N	1	2013-07-09 00:00:00	Y	2021-09-16	M	\N
09	02	001	இராசிபுரம்                                        	Rasipuram                	S	0	2014-08-02 15:26:24	Y	2024-08-08	M	\N
22	02	001	அறந்தாங்கி               	Aranthangi               	S	1	\N	Y	2024-08-28	M	\N
06	05	001	ஆரணி	Arni                     	S	0	2014-02-12 13:58:13	Y	2024-08-28	M	\N
11	02	002	குன்னூர் நகரம்	Coonoor Town             	S	0	2014-07-01 17:48:40	Y	2024-08-28	M	\N
11	01	001	உதகை கிழக்கு                                      	Udhagai East             	S	0	2014-07-01 17:52:28	Y	2024-08-28	M	\N
11	01	003	உதகை மேற்கு                                       	Udhagai West             	S	0	2014-07-01 17:52:42	Y	2024-08-28	M	\N
25	15	002	பெரியகுளம்	Periyakulam              	S	0	2014-08-28 12:38:14	Y	2024-08-28	M	\N
25	11	001	தேனி-அல்லிநகரம்	Theni -  Allinagaram     	S	0	2014-07-14 12:50:41	Y	2024-08-28	M	\N
10	11	002	பெரியசேமூர்	Periyasemur	N	1	2019-06-27 00:00:00	Y	2022-05-31	C	\N
27	24	002	பரமக்குடி	Paramakudi               	N	0	2006-01-01 00:41:38	Y	2022-05-31	M	\N
08	01	001	சேலம்	Salem                    	N	0	2014-06-30 16:42:24	Y	2022-05-31	C	\N
12	01	003	கோயம்புத்தூர் மாநகராட்சி தெற்கு பகுதி	Coimbatore Corporation  South Part	N	0	2014-09-22 12:55:12	Y	2022-05-31	C	\N
13	31	002	கொடைக்கானல் டவுன்                                 	Kodaikanal Town          	S	0	2014-07-28 13:43:29	Y	2024-08-28	M	\N
25	12	003	போடிநாயக்கனூர்                                    	Bodinayakkanur           	S	0	2014-08-29 16:05:40	Y	2024-08-28	M	\N
26	03	003	சாத்தூர்	Sattur                   	S	64	2014-07-15 11:06:31	Y	2024-08-28	M	\N
26	02	002	ஸ்ரீவில்லிபுத்துார்	Srivilliputhur           	S	64	2014-07-15 11:06:31	Y	2024-08-28	M	\N
12	02	004	கோயம்புத்தூர் மாநகராட்சி வடக்கு பகுதி	Coimbatore Corporation  North Part 	N	0	2014-09-22 12:55:12	Y	2022-05-31	C	\N
35	13	007	பம்மல்                                            	Pammal                   	S	0	2019-06-20 15:08:16	Y	2024-08-28	M	\N
02	08	004	மயிலாப்பூர் பகுதி 1	Mylapore (Part 1)	O	0	\N	Y	2021-08-17	C	\N
02	08	005	மயிலாப்பூர் பகுதி 2	Mylapore (Part 2)	O	0	\N	Y	2021-08-17	C	\N
26	05	005	விருதுநகர்                                        	Virudhunagar             	S	64	2014-07-15 11:06:31	Y	2024-08-28	M	\N
02	08	001	சிந்தாதிரிப்பேட்டை	Chintadripet	O	0	\N	Y	2021-08-17	C	\N
02	07	006	மாம்பலம்	Mambalam	N	0	\N	Y	2021-08-10	C	\N
02	01	002	தண்டையார்பேட்டை பகுதி 2	Tondiarpet (Part 2)	O	0	\N	Y	2022-04-07	C	\N
02	01	003	தண்டையார்பேட்டை பகுதி 3	Tondiarpet (Part 3)	O	0	\N	Y	2022-04-07	C	\N
02	01	004	தண்டையார்பேட்டை பகுதி 4	Tondiarpet (Part 4)	O	0	\N	Y	2022-04-07	C	\N
02	02	001	புரசைவாக்கம் பகுதி 1	Purasawalkam (Part 1)	O	0	\N	Y	2022-04-07	C	\N
02	02	002	புரசைவாக்கம் பகுதி 2	Purasawalkam (Part 2)	O	0	\N	Y	2022-04-07	C	\N
08	11	001	சேலம்                                             	Salem	N	0	2014-06-30 16:42:24	Y	2022-05-31	C	\N
08	12	001	சேலம்                                             	Salem	N	0	2014-06-30 16:42:24	Y	2022-05-31	C	\N
10	11	001	ஈரோடு	Erode                    	N	0	2006-09-20 15:51:03	Y	2022-05-31	C	\N
12	02	006	கவுண்டம்பாளையம்	Kavundampalayam	N	0	\N	Y	2022-05-31	C	\N
12	13	005	கோயம்புத்தூர் மாநகராட்சி பேரூர் பகுதி	Coimbatore Corporation  Perur Part 	N	0	2014-09-22 12:55:12	Y	2022-05-31	C	\N
02	13	001	வளசரவாக்கம்              	Valasaravakkam           	N	1	2023-05-22 11:46:48	\N	\N	\N	\N
02	09	004	தி நகர்	T.Nagar	N	0	\N	Y	2022-04-07	C	\N
08	04	001	ஆத்தூர்	Attur	N	0	\N	Y	2022-05-31	M	\N
08	09	001	மேட்டூர்	Mettur	N	0	\N	Y	2022-05-31	M	\N
02	16	002	கத்திவாக்கம்                                      	Kathivakkam              	S	1	2018-08-16 10:46:48	Y	2024-08-28	C	\N
30	01	033	நாகர்கோவில் தெற்கு                                	Nagercoil South          	S	0	\N	Y	2024-08-28	C	\N
30	01	036	வடசேரி மேற்கு                                     	Vadasery West            	S	0	\N	Y	2024-08-28	C	\N
30	01	037	வடசேரி தெற்கு                                     	Vadasery South           	S	0	\N	Y	2024-08-28	C	\N
30	01	038	நீண்டகரை ஏ கிழக்கு                                	Neendakarai A East       	S	0	\N	Y	2024-08-28	C	\N
18	05	001	விருத்தாச்சலம்	Virudhachalam	S	0	\N	Y	2024-08-28	M	\N
34	04	007	தென்காசி                                          	Tenkasi                  	S	0	2014-09-16 13:42:33	Y	2024-08-28	M	\N
34	06	005	செங்கோட்டை	Shenkottai               	S	0	2014-09-16 13:42:33	Y	2024-08-28	M	\N
18	02	001	பண்ருட்டி	Panruti	S	0	\N	Y	2024-08-28	M	\N
30	01	013	நாகர்கோவில் வடக்கு                                	Nagercoil North          	S	0	2013-12-13 16:44:08	Y	2024-08-28	C	\N
30	01	014	வடசேரி கிழக்கு                                    	Vadasery East            	S	0	\N	Y	2024-08-28	C	\N
30	01	030	வடிவீஸ்வரம் வடக்கு                                	Vadeveeswaram North      	S	0	\N	Y	2024-08-28	C	\N
30	01	031	வடிவீஸ்வரம் தெற்கு                                	Vadeveeswaram South      	S	0	\N	Y	2024-08-28	C	\N
18	01	001	கடலூர்	Cuddalore	S	0	\N	Y	2024-08-28	C	\N
08	07	001	எடப்பாடி	Edapadi	N	0	\N	Y	2022-05-31	M	\N
01	11	002	கத்திவாக்கம்	Kathivakkam	N	0	\N	Y	2022-05-31	M	\N
22	04	001	அரிமளம்	Arimalam	N	0	\N	Y	2022-05-31	M	\N
22	05	001	பொன்னமராவதி	Ponnamaravathi     	N	0	\N	Y	2022-05-31	M	\N
22	15	001	ஆலங்குடி	Alangudi                 	N	0	\N	Y	2022-05-31	M	\N
22	16	001	கறம்பக்குடி	Karambakgudi             	N	0	\N	Y	2022-05-31	M	\N
38	05	002	மயிலாடுதுறை	Mailaduthurai	N	0	\N	Y	2022-05-31	M	\N
18	02	002	நெல்லிக்குப்பம்	Nellikuppam	N	0	\N	Y	2022-05-31	M	\N
14	03	001	குளித்தலை	Kulithalai               	N	0	\N	Y	2022-05-31	M	\N
16	03	001	பெரம்பலூர்	Perambalur	N	0	2019-07-04 00:00:00	Y	2022-05-31	M	\N
35	04	002	செங்கல்பட்டு                                      	Chengalpattu             	S	0	2014-09-09 12:16:43	Y	2022-05-31	M	\N
02	04	008	அயனாவரம் பகுதி 2	Ayanavaram (Part 2)	N	0	\N	Y	2022-04-07	C	\N
02	05	001	வில்லிவாக்கம்	Villivakkam	N	0	\N	Y	2022-04-07	C	\N
02	05	002	முல்லம்	Mullam	N	0	\N	Y	2022-04-07	C	\N
02	10	001	வேளச்சேரி பாகம் 1	Velacheri (Part 1)	N	0	\N	Y	2022-04-07	C	\N
02	10	002	வேளச்சேரி பாகம் 2	Velacheri (Part 2)	N	0	\N	Y	2022-04-07	C	\N
02	10	003	தரமணி	Taramani	N	0	\N	Y	2022-04-07	C	\N
02	10	004	கானகம்	Kaanagam	N	0	\N	Y	2022-04-07	C	\N
02	10	005	பள்ளிப்பட்டு	Pallipattu	N	0	\N	Y	2022-04-07	C	\N
02	10	006	களிக்குன்றம்	Kalikundram	N	0	\N	Y	2022-04-07	C	\N
02	10	007	ஊருர்	Urur	N	0	\N	Y	2022-04-07	C	\N
02	10	008	திருவான்மியூர்	Thiruvanmiyur	N	0	\N	Y	2022-04-07	C	\N
02	07	001	சாலிகிராம்ம்	Saligramam	N	0	\N	Y	2022-04-07	C	\N
02	07	003	விருகம்பாக்கம்	Virugambakkam	N	0	\N	Y	2022-04-07	C	\N
02	06	003	புலியூர்	Puliyur	N	0	\N	Y	2022-04-07	C	\N
02	01	001	தண்டையார்பேட்டை பகுதி 1	Tondiarpet (Part 1)	O	0	\N	Y	2022-04-07	C	\N
02	02	003	வேப்பேரி	Vepery	O	0	\N	Y	2022-04-07	C	\N
24	11	001	மதுரை மாநகராட்சி	Madurai Corporation                 	N	0	\N	Y	2021-09-16	C	\N
24	12	001	மதுரை மாநகராட்சி                      	Madurai Corporation      	N	0	\N	Y	2021-09-16	C	\N
24	16	001	மதுரை மாநகராட்சி                                  	Madurai Corporation      	N	0	\N	Y	2021-09-16	C	\N
24	17	001	மதுரை மாநகராட்சி                                  	Madurai Corporation      	N	0	\N	Y	2021-09-16	C	\N
32	01	001	திருப்பூர் வடக்கு	Tiruppur North	N	0	\N	Y	2021-09-16	C	\N
32	08	001	திருப்பூர் தெற்கு	Tiruppur South             	N	0	\N	Y	2021-09-16	C	\N
09	07	001	குமாரபாளையம்                                      	Kumarapalayam            	N	0	2014-09-04 13:50:41	Y	2022-05-31	M	\N
02	09	006	வெங்கடாபுரம்	Venkatapuram	S	0	\N	Y	2024-08-28	C	\N
01	06	001	திருத்தணி                                         	Tiruttani                	S	1	2019-07-11 10:47:58	Y	2024-08-28	M	\N
18	03	001	சிதம்பரம்	Chidambaram              	S	0	2014-11-21 15:34:03	Y	2024-08-28	M	\N
15	05	001	துறையுர்	Thuraiur                 	S	0	2017-07-20 11:59:49	Y	2024-08-28	M	\N
06	04	001	திருவத்திபுரம்	Thiruvathipuram	S	0	2014-07-10 18:10:57	Y	2024-08-28	M	\N
30	03	017	குளச்சல் எ                                        	Colachel A               	S	0	2013-12-13 16:44:08	Y	2024-08-28	M	\N
37	04	001	வாலாஜா                                            	Walajah                  	S	0	2014-03-10 12:07:23	Y	2024-08-28	M	\N
31	05	001	கிருஷ்ணகிரி	Krishnagiri              	N	0	2014-07-16 02:00:30	Y	2022-05-31	M	\N
25	14	004	சின்னமனூர்	Chinnamanur              	N	0	2014-08-01 11:24:04	Y	2022-05-31	M	\N
01	07	001	திருவள்ளுர்                                       	Tiruvallur               	N	0	2014-07-28 10:46:48	Y	2022-05-31	M	\N
15	03	001	மணப்பாறை நகராட்சி                                 	Manaparai  Municipal	N	0	2014-09-22 14:53:46	Y	2022-05-31	M	\N
32	06	001	உடுமலைபேட்டை	Udumalaipet              	N	0	2009-05-13 02:24:05	Y	2021-09-16	M	\N
09	01	001	நாமக்கல்                                          	Namakkal                 	N	0	2014-07-30 10:28:52	Y	2022-05-31	M	\N
35	13	005	பல்லாவரம்                                         	Pallavaram               	N	0	2006-01-01 04:24:08	Y	2022-05-31	M	\N
36	06	001	வாணியம்பாடி	Vaniyambadi              	N	0	2014-10-31 17:18:30	Y	2022-05-31	M	\N
36	09	001	ஆம்பூர்	Ambur                    	N	0	2014-10-07 19:54:18	Y	2022-05-31	M	\N
36	07	001	திருப்பத்தூர் நகரம்                               	Tirupathur Town	N	0	2014-11-11 10:12:28	Y	2022-05-31	M	\N
22	01	001	புதுக்கோட்டை	Pudukkottai              	N	0	\N	Y	2022-05-31	M	\N
23	11	001	சிவகங்கை	Sivagangai                	N	1	2006-01-17 15:38:13	Y	2021-09-16	M	\N
35	13	008	அனகாபுத்தூர்	Anakaputhur	N	0	\N	Y	2022-05-31	M	\N
10	32	001	சத்தியமங்கலம்	Sathyamangalam           	N	0	2006-09-20 15:51:03	Y	2022-05-31	M	\N
13	11	003	திண்டுக்கல் கிழக்கு	Dindigul East	S	0	2014-08-19 12:48:57	Y	2022-05-31	C	\N
02	03	005	பெரம்புர் பகுதி 1	Perambur (Part 1)	O	0	\N	Y	2022-04-07	C	\N
02	03	007	பெரம்புர் பகுதி 2	Perambur (Part 2)	O	0	\N	Y	2022-04-07	C	\N
02	03	008	பெரம்புர் பகுதி 3	Perambur (Part 3)	O	0	\N	Y	2022-04-07	C	\N
02	06	001	எழும்பூர் பகுதி 1	Egmore (Part 1)	O	0	\N	Y	2022-04-07	C	\N
02	06	002	எழும்பூர் பகுதி 2	Egmore (Part 2)	O	0	\N	Y	2022-04-07	C	\N
02	06	004	நுங்கம்பாக்கம்	Nungambakkam	O	0	\N	Y	2022-04-07	C	\N
14	01	001	கரூர்	Karur                    	N	0	\N	Y	2022-05-31	C	\N
15	11	001	திருச்சிராப்பள்ளி மாநகராட்சி - அரியமங்கலம் பகுதி	Tiruchirappalli Corporation -Ariymangalam Zone	N	0	\N	Y	2022-05-31	C	\N
13	32	003	திண்டுக்கல் மேற்கு	Dindigul West                	S	0	2014-08-19 12:48:57	Y	2022-05-31	C	\N
21	05	003	கும்பகோணம்	Kumbakonam               	N	0	2014-07-16 11:26:02	Y	2021-09-16	C	\N
28	01	001	தூத்துக்குடி                                      	Thoothukudi              	N	0	2017-10-13 11:50:45	Y	2022-05-31	C	\N
29	01	001	திருநெல்வேலி	Tirunelveli              	N	0	2014-09-16 13:42:33	Y	2022-05-31	C	\N
29	01	004	தச்சநல்லூர்	Thachanallur             	N	0	2014-10-27 17:27:08	Y	2022-05-31	C	\N
29	02	002	பாளையங்கோட்டை                                     	Palayamkottai            	N	0	2014-09-16 13:42:33	Y	2022-05-31	C	\N
29	02	003	மேலப்பாளையம்                                      	Melapalayam              	N	0	2014-04-09 18:25:55	Y	2022-05-31	C	\N
29	02	004	தச்சநல்லூர்                                       	Thatchanallur            	N	0	2014-04-09 18:25:55	Y	2022-05-31	C	\N
31	09	001	ஓசூர்	Hosur                    	N	0	2014-07-21 17:17:48	Y	2022-05-31	C	\N
35	05	003	தாம்பரம்                                          	Tambaram                 	N	0	2006-01-01 03:09:28	Y	2022-05-31	C	\N
21	07	002	பட்டுக்கோட்டை                                     	Pattukkotai              	N	0	2014-07-16 11:25:04	Y	2021-09-16	M	\N
34	03	009	சங்கரன்கோவில்	Sankarankoil             	N	0	2014-12-11 16:10:38	Y	2022-05-31	M	\N
34	12	010	புளியங்குடி                                       	Puliyankudi              	N	0	2014-12-20 10:27:56	Y	2022-05-31	M	\N
34	04	008	குற்றாலம்	Courtralam               	N	0	2014-12-20 10:27:56	Y	2022-05-31	M	\N
37	02	001	ஆற்காடு நகரம்	Arcot Town               	N	0	2014-08-22 18:44:12	Y	2022-05-31	M	\N
38	06	003	சீர்காழி	Sirkazhi                 	N	0	2014-07-01 01:57:12	Y	2022-05-31	M	\N
23	21	001	தேவகோட்டை	Devakottai               	N	0	2006-01-01 03:34:06	Y	2021-09-16	M	\N
26	06	006	அருப்புக்கோட்டை	Aruppukottai             	N	1	2018-01-01 11:04:29	Y	2021-09-16	M	\N
32	04	001	தாராபுரம்	Dharapuram               	N	0	2009-05-13 02:24:05	Y	2021-09-16	M	\N
24	13	001	மேலூர்                                            	Melur                    	N	0	\N	Y	2021-09-16	M	\N
24	21	001	உசிலம்பட்டி	Usilampatti              	N	0	\N	Y	2021-09-16	M	\N
24	23	001	திருமங்கலம்	Thirumangalam            	N	0	\N	Y	2021-09-16	M	\N
02	02	004	வ உ சி நகர்	V.O.C Nagar	N	0	\N	Y	2022-04-07	C	\N
26	01	001	இராஜபாளையம்                                       	Rajapalaiam              	S	64	2014-07-15 11:06:31	Y	2024-08-28	M	\N
10	31	001	கோபிசெட்டிபாளையம்	Gobichettipalayam        	S	0	2006-09-20 15:51:03	Y	2024-08-28	M	\N
23	22	001	காரைக்குடி	Karaikudi                	S	0	2006-09-20 15:51:03	Y	2024-08-28	M	\N
10	33	001	பவானி	Bhavani                  	S	0	2006-09-20 15:51:03	Y	2024-08-28	M	\N
30	04	035	குழித்துறை                                        	Kuzhithurai              	S	0	\N	Y	2024-08-28	M	\N
30	03	031	பத்மனாபபுரம் எ                                    	Padmanabhapuram A        	S	0	\N	Y	2024-08-28	M	\N
30	03	033	பத்மனாபபுரம் பி                                   	Padmanabhapuram B        	S	0	\N	Y	2024-08-28	M	\N
30	03	052	குளச்சல் பி                                       	Colachel B               	S	0	\N	Y	2024-08-28	M	\N
\.


--
-- TOC entry 4790 (class 2606 OID 25749259)
-- Name: town town_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.town
    ADD CONSTRAINT town_pkey PRIMARY KEY (district_code, taluk_code, town_code);


--
-- TOC entry 4981 (class 0 OID 0)
-- Dependencies: 857
-- Name: TABLE town; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT ON TABLE public.town TO temple;
GRANT SELECT ON TABLE public.town TO igrs;
GRANT SELECT ON TABLE public.town TO clap;
GRANT SELECT ON TABLE public.town TO web_anon;
GRANT SELECT ON TABLE public.town TO authenticator;
GRANT SELECT ON TABLE public.town TO ultuser;


-- Completed on 2025-04-05 17:57:23 IST

--
-- PostgreSQL database dump complete
--

