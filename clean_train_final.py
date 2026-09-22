import json
import re

with open('train_augmented.jsonl', 'r', encoding='utf-8') as f:
    records = [json.loads(line) for line in f if line.strip()]

cleaned_records = []
prompt_counts = {}
CAP = 20  # Cap identical first-turn user prompts to 20 to avoid overfitting

stats = {
    'shfit_fixed': 0,
    'march_removed': 0,
    'repetitions_dropped': 0
}

for r in records:
    messages = r.get('messages', [])
    if len(messages) < 2:
        continue
    
    first_user_idx = 1 if messages[0]['role'] == 'system' else 0
    first_user_msg = messages[first_user_idx]['content']
    first_user_lower = first_user_msg.lower().strip()

    # 1. Fix "shfit" typo
    if 'shfit' in first_user_lower:
        r['meta']['intent'] = 'fv_change_date'
        # Trim off any follow-ups since it's a procedural answer now
        r['messages'] = r['messages'][:first_user_idx+2]
        r['messages'][-1]['content'] = "To change the field visit date, you should ask the Tahsildar. The Tahsildar has the authority to approve field visit date changes."
        stats['shfit_fixed'] += 1
    
    # 2. Fix Tamil March field visit filter
    if 'மார்ச் மாத கள ஆய்வுகளை காட்டு' in first_user_lower:
        stats['march_removed'] += 1
        continue
        
    # 3. Frequency capping
    if first_user_lower not in prompt_counts:
        prompt_counts[first_user_lower] = 0
        
    prompt_counts[first_user_lower] += 1
    
    if prompt_counts[first_user_lower] > CAP:
        stats['repetitions_dropped'] += 1
        continue
        
    cleaned_records.append(r)

with open('train_augmented.jsonl', 'w', encoding='utf-8') as f:
    for r in cleaned_records:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')

print(f"Total records before: {len(records)}")
print(f"Total records after: {len(cleaned_records)}")
print(f"Stats: {stats}")
