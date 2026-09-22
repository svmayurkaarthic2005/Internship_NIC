import json
import re

def get_ordinal(n):
    if 11 <= (n % 100) <= 13:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return str(n) + suffix

cleaned = []
gps_removed = 0
not_isd_fixed = 0
nisd_meta_fixed = 0
ordinal_fixed = 0
can_fixed = 0

with open('train_augmented.jsonl', 'r', encoding='utf-8') as f:
    for line in f:
        obj = json.loads(line)
        line_lower = line.lower()
        
        # 1. GPS removal
        if re.search(r'\bgps\b', line_lower) or re.search(r'\bgis\b', line_lower):
            gps_removed += 1
            continue
            
        # 2. "not ISD" negation fixing
        if 'not isd' in line_lower:
            fixed_not_isd = False
            for m in obj['messages']:
                if m['role'] == 'user' and 'not isd' in m['content'].lower():
                    idx = obj['messages'].index(m)
                    if idx + 1 < len(obj['messages']):
                        resp = obj['messages'][idx+1]
                        if 'ISD' in resp['content'] and 'NISD' not in resp['content']:
                            resp['content'] = resp['content'].replace('| ISD |', '| NISD |')
                            fixed_not_isd = True
            
            meta = obj.get('meta', {})
            if 'filters' in meta and meta['filters'].get('type') == 'ISD':
                meta['filters']['type'] = {'$ne': 'ISD'}
                fixed_not_isd = True
                
            if fixed_not_isd:
                not_isd_fixed += 1
                
        # 3. NISD metadata mismatch (0153 -> NISD, but type -> ISD)
        if '0153' in line_lower:
            meta = obj.get('meta', {})
            if 'filters' in meta and meta['filters'].get('type') == 'ISD':
                meta['filters']['type'] = 'NISD'
                nisd_meta_fixed += 1
                
        # 4. Ordinal follow-ups
        msgs = obj['messages']
        fixed_ordinal = False
        for j in range(1, len(msgs)):
            if 'listed only' in msgs[j]['content']:
                prev_asst = msgs[j-2]['content']
                match = re.search(r'Found (\d+) application', prev_asst)
                if match:
                    found_count = int(match.group(1))
                    new_req = found_count + 1
                    new_req_ord = get_ordinal(new_req)
                    
                    user_msg = msgs[j-1]['content']
                    msgs[j-1]['content'] = re.sub(r'\d+(st|nd|rd|th)', new_req_ord, user_msg)
                    
                    msgs[j]['content'] = f"That answer listed only {found_count} application(s), so there is no number {new_req}. Give the application number you mean, or a position from 1 to {found_count}."
                    
                    if 'meta' in obj and 'selection' in obj['meta'] and 'ordinal' in obj['meta']['selection']:
                        obj['meta']['selection']['ordinal'] = new_req_ord
                    fixed_ordinal = True
        
        if fixed_ordinal:
            ordinal_fixed += 1
            
        # 5. CAN contradictory knowledge
        fixed_can = False
        for m in msgs:
            if m['role'] == 'assistant' and 'every application in the register carries a can number' in m['content'].lower():
                m['content'] = 'If a CAN number is recorded, it will be returned. If the CAN is null or not recorded, it will be shown as "Not recorded".'
                fixed_can = True
                
        if fixed_can:
            can_fixed += 1
            
        cleaned.append(obj)

import shutil
shutil.copy('train_augmented.jsonl', 'train_augmented.jsonl.bak')

with open('train_augmented.jsonl', 'w', encoding='utf-8') as f:
    for obj in cleaned:
        f.write(json.dumps(obj) + '\n')
        
print(f"Removed GPS records: {gps_removed}")
print(f"Fixed 'not ISD' records: {not_isd_fixed}")
print(f"Fixed NISD metadata records: {nisd_meta_fixed}")
print(f"Fixed ordinal records: {ordinal_fixed}")
print(f"Fixed CAN knowledge records: {can_fixed}")
