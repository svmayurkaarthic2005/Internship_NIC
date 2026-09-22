import json
import re
import random

def process_conversation(obj, counter):
    msgs = obj['messages']
    
    # Track assigned IDs for canonical matching
    app_list = []
    
    # 1. Pre-process user messages
    for m in msgs:
        if m['role'] == 'user':
            if '000000' in m['content']:
                m['content'] = m['content'].replace('000000', '999999')

    # Main pass over assistant messages
    for msg_idx, m in enumerate(msgs):
        if m['role'] == 'assistant':
            user_msg = msgs[msg_idx-1]['content']
            user_lower = user_msg.lower()
            asst_msg = m['content']
            
            # 2. Invalid survey number (only if explicitly asked for "survey 99999")
            if 'survey 99999' in user_lower:
                asst_msg = "No applications found for Survey No. 99999."
                
            # 4. Stale follow-up context
            if msg_idx >= 2:
                prev2 = msgs[msg_idx-2]['content'].lower()
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
                for m_name in [m_capital.capitalize() for m_capital in months]:
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
                asst_msg = re.sub(r'(?i)approved', 'completed', asst_msg)
                asst_msg = re.sub(r'(?i)approve', 'complete', asst_msg)
                
            # 8. Escalation N/A
            asst_msg = asst_msg.replace('| N/A |', '| 102 |')
            
            # Minor wording issue 1: IGRS 0 of X
            asst_msg = re.sub(r'Only 0 of (\d+) carry an IGRS Form 6 number — the Sub-Registrar referrals\.', 
                              r'None of the \1 carry an IGRS Form 6 number.', asst_msg)

            # 9. CAN placeholder and Unique ID replacement
            new_lines = []
            prefix_cursors = {}
            for line in asst_msg.split('\n'):
                # Handle CAN placeholders
                line = re.sub(r'0{15}', str(random.randint(10**14, 10**15-1)), line)
                line = re.sub(r'0{12}', str(random.randint(10**11, 10**12-1)), line)
                
                # Replace 000000 with canonical ID
                def replacer(match):
                    nonlocal counter
                    prefix = match.group(1)
                    
                    s = re.search(r'Survey No:\s*(\d+)', line)
                    if not s:
                        parts = line.split('|')
                        if len(parts) > 3 and '000000' in parts[0]:
                            survey_no_str = parts[2].strip()
                            if survey_no_str.isdigit():
                                s = survey_no_str
                            else:
                                s = None
                        else:
                            s = None
                    else:
                        s = s.group(1)
                        
                    d = re.search(r'(\d{4}-\d{2}-\d{2})', line)
                    if d:
                        d = d.group(1)
                        
                    found_id = None
                    
                    # Try canonical matching only for follow-ups (msg_idx > 2)
                    if msg_idx > 2:
                        if s:
                            for a in app_list:
                                if a['survey'] == s and a['prefix'] == prefix:
                                    found_id = a['id']
                                    break
                        if not found_id and d:
                            for a in app_list:
                                if a['date'] == d and a['prefix'] == prefix:
                                    found_id = a['id']
                                    break
                        
                        if not found_id:
                            if prefix not in prefix_cursors:
                                prefix_cursors[prefix] = 0
                            nth = prefix_cursors[prefix]
                            matching_apps = [a for a in app_list if a['prefix'] == prefix]
                            if nth < len(matching_apps):
                                found_id = matching_apps[nth]['id']
                                prefix_cursors[prefix] += 1
                    
                    if not found_id:
                        # NEW application (first table, or omitted from first table)
                        found_id = f'{counter:06d}'
                        counter += 1
                        app_list.append({
                            'prefix': prefix,
                            'survey': s,
                            'date': d,
                            'id': found_id
                        })
                        if prefix not in prefix_cursors:
                            prefix_cursors[prefix] = 0
                        prefix_cursors[prefix] += 1
                        
                    return prefix + found_id

                # Handle fake 999999 correctly for negative lookup tests
                if '999999' in user_lower and re.search(r'\d{4}/\d{4}/\d{2}/000000', line):
                    # It's the "does not exist" response matching user's fake ID
                    line = re.sub(r'(\d{4}/\d{4}/\d{2}/)000000', rf'\g<1>999999', line)
                else:
                    line = re.sub(r'(\d{4}/\d{4}/\d{2}/)000000', replacer, line)
                
                new_lines.append(line)
                
            m['content'] = '\n'.join(new_lines)
            
    return obj, counter

cleaned = []
counter = 100000

with open('validation.jsonl.bak', 'r', encoding='utf-8') as f:
    for line in f:
        obj = json.loads(line)
        obj, counter = process_conversation(obj, counter)
        cleaned.append(obj)

with open('validation.jsonl', 'w', encoding='utf-8') as f:
    for obj in cleaned:
        f.write(json.dumps(obj) + '\n')

print("Successfully applied canonical fixes to validation.jsonl")
