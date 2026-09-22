import json
import re
import random

def process_conversation(obj, counter):
    survey_to_id = {}
    date_to_id = {}
    
    msgs = obj['messages']
    
    # Pre-process user messages
    for m in msgs:
        if m['role'] == 'user':
            if '000000' in m['content']:
                m['content'] = m['content'].replace('000000', '999999')

    # Main pass
    for j in range(1, len(msgs)):
        if msgs[j]['role'] == 'assistant':
            user_msg = msgs[j-1]['content']
            user_lower = user_msg.lower()
            asst_msg = msgs[j]['content']
            
            # 2. Invalid survey number
            if '99999' in user_lower:
                asst_msg = "No applications found for Survey No. 99999."
                
            # 4. Stale follow-up context
            if j >= 2:
                prev2 = msgs[j-2]['content'].lower()
                if 'no applications found' in prev2 or 'there are no ' in prev2 or 'outside your assigned jurisdiction' in prev2:
                    if 'outside your assigned jurisdiction' in prev2:
                        asst_msg = 'I cannot provide details because that is outside your jurisdiction.'
                    else:
                        asst_msg = 'There are no applications listed to select from.'
            
            # 5. Temporal filters
            months = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december']
            found_month = None
            for m_name in months:
                if re.search(rf'\b{m_name}\b', user_lower):
                    found_month = m_name.capitalize()
                    break
            if found_month:
                month_idx = months.index(found_month.lower()) + 1
                month_str = f'{month_idx:02d}'
                asst_msg = re.sub(r'2026-\d{2}-', f'2026-{month_str}-', asst_msg)
                for m_name in [m.capitalize() for m in months]:
                    if m_name != found_month:
                        asst_msg = asst_msg.replace(m_name, found_month)

            # 6. Intent classification errors
            if 'deputy inspector surveyor' in user_lower and 'விளக்கவும்' in user_lower:
                asst_msg = "The Deputy Inspector Surveyor (DIS) is a supervisory role. The DIS oversees the work of the Sub Inspector Surveyor, verifying field measurements, sketches, and application details before forwarding to the Tahsildar."
                if 'meta' in obj:
                    obj['meta']['intent'] = 'knowledge_query'
            elif 'workflow stage' in user_lower and 'விளக்கவும்' in user_lower:
                asst_msg = "The workflow stage indicates where an application is currently pending, such as SIS (your desk), SD (Senior Draughtsman), DIS (Deputy Inspector Surveyor), or TAHSILDAR."
                if 'meta' in obj:
                    obj['meta']['intent'] = 'knowledge_query'
                    
            # 7. Completed converted to Approved
            if 'completed' in user_lower and 'approved' in asst_msg.lower():
                # Replace approved with completed
                asst_msg = re.sub(r'(?i)approved', 'completed', asst_msg)
                asst_msg = re.sub(r'(?i)approve', 'complete', asst_msg)
                
            # 9. Escalation N/A
            asst_msg = asst_msg.replace('| N/A |', '| 102 |')
            
            # Apply modified content back temporarily for unique ID replacement
            msgs[j]['content'] = asst_msg

            # 1. Unique ID replacement & 8. CAN placeholder
            new_lines = []
            for line in msgs[j]['content'].split('\n'):
                # Handle CAN placeholders first
                line = re.sub(r'0{15}', str(random.randint(10**14, 10**15-1)), line)
                line = re.sub(r'0{12}', str(random.randint(10**11, 10**12-1)), line)
                
                if '|' in line and re.search(r'\d{4}/\d{4}/\d{2}/000000', line):
                    new_id = f'{counter:06d}'
                    counter += 1
                    
                    parts = line.split('|')
                    if len(parts) > 3:
                        survey_no_str = parts[2].strip()
                        if survey_no_str.isdigit():
                            survey_to_id[survey_no_str] = new_id
                    
                    d = re.search(r'(\d{4}-\d{2}-\d{2})', line)
                    if d:
                        date_to_id[d.group(1)] = new_id
                        
                    line = re.sub(r'(\d{4}/\d{4}/\d{2}/)000000', rf'\g<1>{new_id}', line)
                
                elif re.search(r'\d{4}/\d{4}/\d{2}/000000', line):
                    s = re.search(r'Survey No:\s*(\d+)', line)
                    mapped_id = None
                    if s and s.group(1) in survey_to_id:
                        mapped_id = survey_to_id[s.group(1)]
                    else:
                        d = re.search(r'(\d{4}-\d{2}-\d{2})', line)
                        if d and d.group(1) in date_to_id:
                            mapped_id = date_to_id[d.group(1)]
                    
                    if mapped_id:
                        line = re.sub(r'(\d{4}/\d{4}/\d{2}/)000000', rf'\g<1>{mapped_id}', line)
                    else:
                        line = re.sub(r'(\d{4}/\d{4}/\d{2}/)000000', rf'\g<1>999999', line)
                
                new_lines.append(line)
                
            msgs[j]['content'] = '\n'.join(new_lines)
            
    return obj, counter

cleaned = []
counter = 100000

with open('validation.jsonl', 'r', encoding='utf-8') as f:
    for line in f:
        obj = json.loads(line)
        obj, counter = process_conversation(obj, counter)
        cleaned.append(obj)

import shutil
shutil.copy('validation.jsonl', 'validation.jsonl.bak')

with open('validation.jsonl', 'w', encoding='utf-8') as f:
    for obj in cleaned:
        f.write(json.dumps(obj) + '\n')

print("Successfully cleaned validation.jsonl")
