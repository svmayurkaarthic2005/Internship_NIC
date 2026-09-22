import json
import random
import copy

INPUT_FILE = 'train_augmented.jsonl'
OUTPUT_FILE = 'train_tamil_additions.jsonl'

TARGET_COUNT = 500

EXACT_MAP = {
    # Application Search
    "show my isd applications": [
        "எனது ISD விண்ணப்பங்களை காட்டு",
        "ISD விண்ணப்பங்களை பட்டியலிடு",
        "enoda ISD applications kudu"
    ],
    "show my nisd applications": [
        "எனது NISD விண்ணப்பங்களை காட்டு",
        "NISD விண்ணப்பங்களை பட்டியலிடு",
        "enoda NISD applications kudu"
    ],
    "show my pending applications": [
        "எனது நிலுவை விண்ணப்பங்களை காட்டு",
        "நிலுவையில் உள்ள விண்ணப்பங்களை காட்டு",
        "pending applications irukka?"
    ],
    "show pending isd applications": [
        "நிலுவையில் உள்ள ISD விண்ணப்பங்கள் என்னென்ன?",
        "pending ISD applications காட்டு"
    ],
    "show my rejected applications": [
        "நிராகரிக்கப்பட்ட விண்ணப்பங்களை காட்டு",
        "என் rejected applications காமி",
        "reject aana files list"
    ],
    "show my approved applications": [
        "அங்கீகரிக்கப்பட்ட விண்ணப்பங்களை காட்டு",
        "approve aana files காமி",
        "முடிந்த விண்ணப்பங்களை காட்டு"
    ],
    "show applications from csc": [
        "CSC மூலமாக வந்த விண்ணப்பங்களை காட்டு",
        "csc applications காமி"
    ],
    "show applications from the sub-registrar": [
        "Sub-Registrar விண்ணப்பங்களை காட்டு",
        "பதிவாளர் அலுவலக விண்ணப்பங்களை காமி"
    ],
    "show applications in ward 102": [
        "வார்டு 102 விண்ணப்பங்களை காட்டு",
        "ward 102 la applications kaatu"
    ],
    "show applications in ward 103": [
        "வார்டு 103 விண்ணப்பங்களை காட்டு",
        "ward 103 la applications kaatu"
    ],
    "show my applications": [
        "எனது விண்ணப்பங்களை காட்டு",
        "என்னுடைய applications காமி"
    ],
    
    # Follow-ups (Strict mappings to prevent field swaps)
    "their submission channel": [
        "அவற்றின் submission channel?",
        "submission channel என்ன?"
    ],
    "their survey numbers": [
        "அவற்றின் survey number?",
        "survey numbers sollu"
    ],
    "their stage": [
        "அவற்றின் நிலையை காட்டு",
        "stage mattum"
    ],
    "their blocks": [
        "அவற்றின் block-ஐ காட்டு",
        "blocks enna?"
    ],
    "their fee amounts": [
        "அவற்றின் கட்டணங்களை காட்டு",
        "fee amount evlo?"
    ],
    "their igrs numbers": [
        "அவற்றின் igrs numbers?",
        "igrs number sollu"
    ],
    "their can numbers": [
        "avatroda can number kaatu",
        "can numbers sollu"
    ],
    "their status": [
        "அவற்றின் status என்ன?",
        "status mattum"
    ],
    "their applicant names": [
        "applicant name என்ன?",
        "பெயர்களை காட்டு"
    ],
    "their mobile numbers": [
        "அவற்றின் மொபைல் எண்கள்?",
        "mobile number sollu"
    ],
    "their wards": [
        "அவற்றின் ward-ஐ காட்டு",
        "ward details sollu"
    ],
    "the last one": [
        "கடைசி ஒன்று",
        "kadaisi file"
    ],
    "which one is newest": [
        "புதியது எது?",
        "newest edhu?"
    ],
    "which one is the oldst": [
        "பழையது எது?",
        "oldest edhu?"
    ],
    "which one is oldest": [
        "பழையது எது?",
        "oldest edhu?"
    ],
    "the first one": [
        "முதல் விண்ணப்பம்",
        "modhal file"
    ],
    
    # Application Counts
    "how many applications are pending?": [
        "எத்தனை விண்ணப்பங்கள் நிலுவையில் உள்ளன?",
        "ethana files pending irukku?",
        "pending count enna?"
    ],
    
    # Status
    "what is the status of my application": [
        "இந்த விண்ணப்பத்தின் நிலை என்ன?",
        "இந்த application status என்ன?"
    ],
    
    # Service Codes
    "what does service code 0154 mean?": [
        "0154 என்றால் என்ன?",
        "0154 எதைக் குறிக்கிறது?"
    ],
    "what is isd?": [
        "ISD என்றால் என்ன?",
        "ISD செயல்முறை என்ன?"
    ],
    "what is service code 0154?": [
        "0154 சேவை குறியீடு என்ன?",
        "0154 service code meaning enna?"
    ],
    "what does service code 0153 mean?": [
        "0153 என்றால் என்ன?",
        "0153 எதைக் குறிக்கிறது?"
    ],
    "what is nisd?": [
        "NISD என்றால் என்ன?",
        "NISD செயல்முறை என்ன?"
    ],
    "what does service code 0155 mean?": [
        "0155 என்றால் என்ன?",
        "0155 எதைக் குறிக்கிறது?"
    ],
    
    # Field Visits
    "which field visits are scheduled the last 30 days?": [
        "கடந்த 30 நாட்களில் திட்டமிடப்பட்ட கள ஆய்வுகள் என்ன?",
        "last 30 days field visits evlo?"
    ],
    "show field visits march": [
        "மார்ச் மாத கள ஆய்வுகளை காட்டு",
        "march field visits kaatu"
    ],
    "which applications have no field visit scheduled?": [
        "எந்த விண்ணப்பங்களுக்கு கள ஆய்வு தேதியில்லை?",
        "field visit illatha applications kaatu"
    ],
    
    # Knowledge / IGRS / CAN
    "can you explain, what is csc?": [
        "CSC என்றால் என்ன?",
        "csc na enna"
    ],
    "can you explain, what is sub-registrar?": [
        "Sub-Registrar என்றால் என்ன?",
        "sub-registrar na yaru"
    ],
    "what is can number?": [
        "CAN number என்றால் என்ன?",
        "can number na enna?"
    ],
    "what are the rules for escalation process?": [
        "escalation process விதிமுறைகள் என்ன?",
        "escalation rules enna?"
    ]
}

def load_data():
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]

def verify_record(record):
    """
    Ensures the original English record is semantically clean before we clone it.
    1. Checks for 0154/NISD mismatches.
    2. Checks for multi-turn table row count mismatches.
    """
    messages = record.get('messages', [])
    assistant_msgs = [m['content'] for m in messages if m['role'] == 'assistant']
    
    for content in assistant_msgs:
        # Check for 0154 and NISD in the same line (which indicates a bad dataset row)
        lines = content.split('\n')
        for line in lines:
            if '0154' in line and 'NISD' in line:
                return False
            if '0153' in line and 'ISD' in line and 'NISD' not in line:
                return False
                
    # Check multi-turn continuity (table rows should match)
    if len(assistant_msgs) >= 2:
        ans1 = assistant_msgs[0]
        ans2 = assistant_msgs[1]
        if "Found" in ans1 and "Found" in ans2:
            try:
                # E.g. "Found 24 application(s)"
                count1 = int(ans1.split("Found ")[1].split(" ")[0])
                count2 = int(ans2.split("Found ")[1].split(" ")[0])
                if count1 != count2:
                    return False
            except:
                pass
                
    return True

def generate_examples():
    data = load_data()
    random.seed(42)
    
    # We just want to extract as many exact matches as possible up to ~500.
    generated = []
    
    candidates = []
    for item in data:
        candidates.append(item)
        
    random.shuffle(candidates)
    
    match_count = 0
    
    for item in candidates:
        if not verify_record(item):
            continue
            
        new_item = copy.deepcopy(item)
        translated = False
        all_translated_ok = True
        
        user_msg_indices = [i for i, m in enumerate(new_item['messages']) if m['role'] == 'user']
        
        for absolute_idx in user_msg_indices:
            original_q = new_item['messages'][absolute_idx]['content'].lower().strip()
            
            # STRICT EXACT MATCH ONLY
            if original_q in EXACT_MAP:
                new_item['messages'][absolute_idx]['content'] = random.choice(EXACT_MAP[original_q])
                translated = True
            else:
                all_translated_ok = False
                break
                
        if translated and all_translated_ok:
            new_item['meta']['source'] = 'tamil_synthetic'
            generated.append(new_item)
            match_count += 1
            
        if match_count >= 500:
            break
            
    print(f"Successfully generated {len(generated)} perfectly strict Tamil examples.")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for item in generated:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')

if __name__ == '__main__':
    generate_examples()
