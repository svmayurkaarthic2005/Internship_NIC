import psycopg2, urllib.parse

p = urllib.parse.unquote("Mayur%402005")
conn = psycopg2.connect(host="127.0.0.1", port=5432, dbname="sis_chatbot_db", user="postgres", password=p)
cur = conn.cursor()

# Check the towns.taluk_id for our town
cur.execute("""
    SELECT t.id, t.name, t.taluk_id,
           tk.app_uid, tk.taluk_ename, tk.district_uid,
           d.app_uid AS dist_app_uid, d.district_name
    FROM towns t
    LEFT JOIN taluk tk ON t.taluk_id = tk.app_uid
    LEFT JOIN district_unicode d ON tk.district_uid = d.app_uid
    LIMIT 5
""")
print("towns+taluk+district join (correct: taluk_id=app_uid):")
for r in cur.fetchall():
    print(f"  town={r[1]} taluk_id(town)={r[2]} tk.app_uid={r[3]} tk.ename={r[4]} dist_uid={r[5]} d.app_uid={r[6]} dist={r[7]}")

# Check the jurisdiction_subquery join on line 222 - using Taluk.id (bigint pk)
# towns.taluk_id is UUID, matches taluk.app_uid NOT taluk.id
# This means the jurisdiction subquery would return no rows for district officers
cur.execute("""
    SELECT t.id AS town_id, t.taluk_id AS town_taluk_id_col,
           tk.id AS taluk_bigint_id, tk.app_uid AS taluk_app_uid
    FROM towns t
    JOIN taluk tk ON t.taluk_id::text = tk.id::text  -- this should FAIL (UUID vs bigint)
    LIMIT 3
""")
print("Trying wrong join (town.taluk_id = taluk.id bigint):")
try:
    for r in cur.fetchall():
        print(f"  {r}")
except Exception as e:
    print(f"  ERROR: {e}")

# The selectinload chain: Town.taluk uses Town.taluk_id FK → taluk.app_uid
# Let's verify the FK constraint itself
cur.execute("""
    SELECT conname, conrelid::regclass AS table, confrelid::regclass AS ref_table,
           pg_get_constraintdef(oid)
    FROM pg_constraint
    WHERE contype = 'f'
      AND conrelid IN ('towns'::regclass, 'taluk'::regclass)
    ORDER BY conrelid
""")
print("\nForeign key constraints on towns/taluk:")
for r in cur.fetchall():
    print(f"  [{r[1]}] {r[0]}: {r[3]}")

# Check if the taluk row for our town has district_uid set
cur.execute("""
    SELECT tk.app_uid, tk.taluk_code, tk.district_uid, d.district_name
    FROM taluk tk
    LEFT JOIN district_unicode d ON tk.district_uid = d.app_uid
    WHERE tk.district_uid IS NOT NULL
    LIMIT 5
""")
print("\ntaluk rows with district_uid set:")
for r in cur.fetchall():
    print(f"  tk.app_uid={r[0]} code={r[1]} district_uid={r[2]} name={r[3]}")

cur.execute("SELECT COUNT(*) FROM taluk WHERE district_uid IS NULL")
print(f"\ntaluk rows with district_uid IS NULL: {cur.fetchone()[0]}")

cur.close()
conn.close()
