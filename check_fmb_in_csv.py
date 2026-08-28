"""
Check if FMB sketch data exists in CSV files
"""
import csv
from pathlib import Path

csv_file = Path("backend/sample_table/sub_div_patta_transfer_urban_demo.csv")

print("=" * 80)
print("CHECKING FMB DATA IN CSV FILE")
print("=" * 80)

with open(csv_file, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    
    print(f"\n📂 File: {csv_file.name}")
    print(f"   Columns: {len(reader.fieldnames)}")
    
    # Check if FMB columns exist
    has_sketch_sent = 'sketch_sent_date' in reader.fieldnames
    has_sketch_received = 'sketch_received_date' in reader.fieldnames
    
    print(f"\n📋 FMB Columns:")
    print(f"   sketch_sent_date: {'✅ EXISTS' if has_sketch_sent else '❌ NOT FOUND'}")
    print(f"   sketch_received_date: {'✅ EXISTS' if has_sketch_received else '❌ NOT FOUND'}")
    
    if has_sketch_sent or has_sketch_received:
        # Count non-empty values
        sent_count = 0
        received_count = 0
        total_rows = 0
        
        sample_sent = []
        sample_received = []
        
        for row in reader:
            total_rows += 1
            
            sent_val = row.get('sketch_sent_date', '').strip()
            received_val = row.get('sketch_received_date', '').strip()
            
            if sent_val and sent_val not in ['-', '', 'NULL', 'null']:
                sent_count += 1
                if len(sample_sent) < 5:
                    sample_sent.append({
                        'app': row['application_id'],
                        'sent': sent_val
                    })
            
            if received_val and received_val not in ['-', '', 'NULL', 'null']:
                received_count += 1
                if len(sample_received) < 5:
                    sample_received.append({
                        'app': row['application_id'],
                        'received': received_val
                    })
        
        print(f"\n📊 DATA STATISTICS:")
        print(f"   Total rows: {total_rows}")
        print(f"   sketch_sent_date populated: {sent_count} ({sent_count/total_rows*100:.1f}%)")
        print(f"   sketch_received_date populated: {received_count} ({received_count/total_rows*100:.1f}%)")
        
        if sample_sent:
            print(f"\n📅 SAMPLE sketch_sent_date:")
            for s in sample_sent:
                print(f"      {s['app']}: {s['sent']}")
        
        if sample_received:
            print(f"\n📅 SAMPLE sketch_received_date:")
            for s in sample_received:
                print(f"      {s['app']}: {s['received']}")
        else:
            print(f"\n❌ NO sketch_received_date values in CSV")
        
        print("\n" + "=" * 80)
        print("CONCLUSION:")
        print("=" * 80)
        
        if sent_count > 0:
            print(f"✅ FMB IS PRESENT IN CSV!")
            print(f"   • {sent_count} records have sketch_sent_date")
            print(f"   • {received_count} records have sketch_received_date")
            print(f"   • FMB sketch workflow is tracked in the data")
        else:
            print("❌ NO FMB DATA IN CSV")
            print("   • Columns exist but are empty")
    else:
        print("\n❌ NO FMB COLUMNS IN CSV")
