import psycopg2, urllib.parse

password = urllib.parse.unquote("Mayur%402005")
conn = psycopg2.connect(host="127.0.0.1", port=5432, dbname="sis_chatbot_db", user="postgres", password=password)
cur = conn.cursor()

print("SURVEY_NUMBERS columns:", end=" ")
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='survey_numbers' ORDER BY ordinal_position")
print([r[0] for r in cur.fetchall()])

print("BLOCKS columns:", end=" ")
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='blocks' ORDER BY ordinal_position")
print([r[0] for r in cur.fetchall()])

# Full sample with correct column names
cur.execute("""
    SELECT
        a.application_number,
        a.application_type,
        a.current_status,
        a.submission_channel,
        a.submission_date,
        a.can_number,
        ap.name            AS applicant_name,
        ap.mobile          AS mobile_number,
        a.fee_amount,
        a.igrs_form6_number,
        a.submission_source_name,
        a.submission_camp_flag,
        w.ward_number,
        sn.survey_no
    FROM applications a
    LEFT JOIN applicants ap ON a.applicant_id = ap.id
    LEFT JOIN survey_numbers sn ON a.survey_number_id = sn.id
    LEFT JOIN blocks b ON sn.block_id = b.id
    LEFT JOIN wards w ON b.ward_id = w.id
    ORDER BY a.submission_channel, a.application_type, a.current_status
    LIMIT 20
""")
cols = [d[0] for d in cur.description]
print("\nCOLS:", cols)
for r in cur.fetchall():
    print(r)

cur.close()
conn.close()
print("\nDone.")
