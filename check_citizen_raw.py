import csv, re

# Check raw CSV for citizen patterns
with open('backend/sample_table/appl_log_urban_demo.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

print(f'Total rows in appl_log_urban_demo.csv: {len(rows)}')

# Only service codes 0153/0154/0155 (the ones that become applications)
app_rows = [r for r in rows if r['service_code'] in ('0153','0154','0155')]
print(f'Rows with service code 0153/0154/0155: {len(app_rows)}')

# camp_flag=P rows
camp_p = [r for r in app_rows if r.get('camp_flag','').strip() == 'P']
print(f'\ncamp_flag=P rows (citizen): {len(camp_p)}')
for r in camp_p:
    print(f'  {r["application_id"]}  svc={r["service_code"]}  status={r["application_status"]}  source={r["source_name"]}  camp={r["camp_flag"]}  can={r["can_number"]}')

# source_name = bare 10-digit mobile
mobile_re = re.compile(r'^\d{10}$')
mobile_src = [r for r in app_rows if mobile_re.match(r.get('source_name','').strip())]
print(f'\nsource_name=bare mobile (10 digits) rows: {len(mobile_src)}')
for r in mobile_src:
    print(f'  {r["application_id"]}  svc={r["service_code"]}  status={r["application_status"]}  source={r["source_name"]}  camp={r["camp_flag"]}  can={r["can_number"]}')

# source_name = dash (sub_registrar)
dash_rows = [r for r in app_rows if r.get('source_name','').strip() == '-']
print(f'\nsource_name="-" (sub_registrar) count: {len(dash_rows)}')

# Everything else = CSC
csc_rows = [r for r in app_rows if r not in camp_p and r not in dash_rows]
print(f'CSC rows: {len(csc_rows)}')
