import json
import random
import copy
import re

INPUT_FILE = 'train_augmented.jsonl'
OUTPUT_FILE = 'train_tamil_additions.jsonl'

def load_data():
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]

def get_intent(item):
    return item.get('meta', {}).get('intent', '')

def map_intent_to_category(intent):
    if intent in ['application_search', 'pending_applications', 'assigned_today']:
        return 'application_search'
    if intent == 'application_count':
        return 'application_count'
    if intent in ['application_status', 'survey_detail']:
        return 'application_status'
    if intent in ['service_code_lookup', 'service_code_guide']:
        return 'service_code_lookup'
    if intent in ['field_visits', 'fv_change_date', 'fv_visit_plan']:
        return 'field_visits'
    if intent in ['knowledge_query', 'officer_workload', 'jurisdiction_summary']:
        return 'knowledge_query'
    if intent in ['igrs_can_rule']:
        return 'igrs_can_rule'
    if intent.startswith('followup_'):
        return 'followups'
    return 'other'

def translate_query(original_q, cat):
    original_q = original_q.lower().strip()
    
    if cat == 'application_search':
        if "isd" in original_q and "nisd" not in original_q:
            return random.choice(["எனது ISD விண்ணப்பங்களை காட்டு", "ISD விண்ணப்பங்களை பட்டியலிடு", "enoda ISD applications kudu"])
        elif "nisd" in original_q:
            return random.choice(["எனது NISD விண்ணப்பங்களை காட்டு", "NISD விண்ணப்பங்களை பட்டியலிடு", "enoda NISD applications kudu"])
        elif "approved" in original_q:
            return random.choice(["அங்கீகரிக்கப்பட்ட விண்ணப்பங்களை காட்டு", "approve aana files காமி", "முடிந்த விண்ணப்பங்களை காட்டு"])
        elif "rejected" in original_q:
            return random.choice(["நிராகரிக்கப்பட்ட விண்ணப்பங்களை காட்டு", "என் rejected applications காமி", "reject aana files list"])
        elif "pending" in original_q:
            return random.choice(["எனது நிலுவை விண்ணப்பங்களை காட்டு", "நிலுவையில் உள்ள விண்ணப்பங்களை காட்டு", "pending applications irukka?"])
        elif "sub-registrar" in original_q:
            return random.choice(["Sub-Registrar விண்ணப்பங்களை காட்டு", "பதிவாளர் அலுவலக விண்ணப்பங்களை காமி", "sub-registrar files list"])
        elif "csc" in original_q:
            return random.choice(["CSC மூலமாக வந்த விண்ணப்பங்களை காட்டு", "csc applications காமி", "csc ல இருந்து வந்த files"])
        elif "ward" in original_q:
            match = re.search(r'ward (\d+)', original_q)
            w = match.group(1) if match else "என்"
            return random.choice([f"வார்டு {w} விண்ணப்பங்களை காட்டு", f"ward {w} la applications kaatu", f"ward {w} files"])
        elif "overdue" in original_q:
            return random.choice(["காலதாமதமான விண்ணப்பங்களை காட்டு", "overdue applications காமி"])
        else:
            return random.choice(["எனது விண்ணப்பங்களை காட்டு", "என்னுடைய applications காமி", "show my applications"])

    elif cat == 'followups':
        if any(x in original_q for x in ["status", "satus", "stage"]):
            return random.choice(["அவற்றின் நிலையை காட்டு", "stage mattum", "status enna?"])
        elif "can" in original_q:
            return random.choice(["avatroda can number kaatu", "can numbers sollu", "can details"])
        elif "igrs" in original_q or "irgs" in original_q:
            return random.choice(["அவற்றின் igrs numbers?", "igrs number sollu", "igrs details"])
        elif "survey" in original_q or "survay" in original_q:
            return random.choice(["அவற்றின் survey number?", "survey numbers sollu", "survey no mattum"])
        elif "block" in original_q:
            return random.choice(["அவற்றின் block-ஐ காட்டு", "block numbers sollu", "blocks enna?"])
        elif "fee" in original_q:
            return random.choice(["அவற்றின் கட்டணங்களை காட்டு", "fee amount evlo?", "mottha fee"])
        elif "channel" in original_q:
            return random.choice(["அவற்றின் submission channel?", "channel mattum sollu", "submission channel enna?"])
        elif "type" in original_q:
            return random.choice(["அவற்றின் type என்ன?", "type mattum sollu"])
        elif "name" in original_q or "aplicant" in original_q or "applicant" in original_q:
            return random.choice(["applicant name என்ன?", "பெயர்களை காட்டு", "names mattum"])
        elif "mobile" in original_q:
            return random.choice(["அவற்றின் மொபைல் எண்கள்?", "mobile number sollu"])
        elif "ward" in original_q:
            return random.choice(["அவற்றின் ward-ஐ காட்டு", "ward details sollu"])
        elif "last" in original_q:
            return random.choice(["கடைசி ஒன்று", "last application details", "kadaisi file"])
        elif "2nd" in original_q or "second" in original_q:
            return random.choice(["இரண்டாவது விண்ணப்பம்", "rendavathu details", "2nd one kodu"])
        elif any(x in original_q for x in ["1st", "first", "oldest", "oldst"]):
            return random.choice(["பழையது எது?", "oldest edhu?", "migavum pazhayathu?"])
        elif "newest" in original_q:
            return random.choice(["புதியது எது?", "newest edhu?", "latest application"])
        else:
            return None # Skip

    elif cat == 'service_code_lookup':
        if "0154" in original_q or (("isd" in original_q or "nisd" not in original_q) and "0154" not in original_q and "isd" in original_q):
            return random.choice(["0154 என்றால் என்ன?", "0154 எதைக் குறிக்கிறது?", "0154 na enna?", "ISD என்றால் என்ன?", "ISD செயல்முறை என்ன?", "ISD na enna bro?"])
        elif "0153" in original_q or "nisd" in original_q:
            return random.choice(["0153 என்றால் என்ன?", "0153 எதைக் குறிக்கிறது?", "0153 na enna?", "NISD என்றால் என்ன?", "NISD செயல்முறை என்ன?", "NISD na enna?"])
        elif "0155" in original_q or "merge" in original_q:
            return random.choice(["0155 என்றால் என்ன?", "0155 எதைக் குறிக்கிறது?", "0155 na enna?"])
        else:
            return None

    elif cat == 'application_count':
        if "pending" in original_q:
            return random.choice(["எத்தனை நிலுவையில் உள்ளது?", "ethana pending?", "pending applications count enna?"])
        elif "approved" in original_q:
            return random.choice(["எத்தனை விண்ணப்பங்கள் முடிக்கப்பட்டுள்ளன?", "ethana approve aachu?", "approved applications count enna?"])
        elif "rejected" in original_q:
            return random.choice(["எத்தனை விண்ணப்பங்கள் நிராகரிக்கப்பட்டுள்ளன?", "ethana reject aachu?", "rejected count"])
        elif "completed" in original_q:
            return random.choice(["எத்தனை விண்ணப்பங்கள் நிறைவடைந்துள்ளன?", "completed count enna?"])
        elif "how many" in original_q:
            return random.choice(["எத்தனை விண்ணப்பங்கள் வந்துள்ளன?", "indha month evlo applications?"])
        else:
            return None

    elif cat == 'application_status':
        if "status" in original_q:
            return random.choice(["இந்த விண்ணப்பத்தின் நிலை என்ன?", "இந்த application status என்ன?", "status sollu"])
        elif "survey" in original_q:
            match = re.search(r'survey (\d+)', original_q)
            if match:
                return random.choice([f"survey {match.group(1)} விவரங்களை காட்டு", f"survey number {match.group(1)} details venum", f"survey {match.group(1)} kaatu"])
        return None

    elif cat == 'igrs_can_rule' or cat == 'knowledge_query':
        if "csc" in original_q:
            return random.choice(["CSC என்றால் என்ன?", "csc na enna", "csc pathi sollu"])
        elif "sub-registrar" in original_q:
            return random.choice(["Sub-Registrar என்றால் என்ன?", "sub-registrar na yaru", "sub-registrar office pathi sollu"])
        elif "can" in original_q:
            return random.choice(["CAN number என்றால் என்ன?", "can number na enna?", "can id endral enna"])
        elif "escalation" in original_q:
            return random.choice(["escalation process விதிமுறைகள் என்ன?", "escalation rules enna?", "escalation eppadi nadakkum"])
        elif "igrs" in original_q or "form 6" in original_q:
            return random.choice(["IGRS Form 6 என்றால் என்ன?", "igrs pathi sollu"])
        else:
            return None

    elif cat == 'field_visits':
        if "30 days" in original_q or "last 30" in original_q:
            return random.choice(["கடந்த 30 நாட்களில் திட்டமிடப்பட்ட கள ஆய்வுகள் என்ன?", "last 30 days field visits evlo?", "கடந்த 30 நாள் kalam aivu kaatu"])
        elif "march" in original_q:
            return random.choice(["மார்ச் மாத கள ஆய்வுகளை காட்டு", "march field visits kaatu"])
        elif "no field visit" in original_q:
            return random.choice(["எந்த விண்ணப்பங்களுக்கு கள ஆய்வு தேதியில்லை?", "field visit illatha applications kaatu"])
        elif "schedule" in original_q:
            return random.choice(["திட்டமிடப்பட்ட கள ஆய்வுகள் என்ன?", "field visit dates sollu"])
        else:
            return random.choice(["என் கள ஆய்வுகள் என்ன?", "நிலுவையில் உள்ள கள ஆய்வுகள் உள்ளதா?", "field visit eppo?"])

    return None


def generate_examples():
    data = load_data()
    random.seed(42)
    
    targets = {
        'application_search': 80,
        'application_count': 60,
        'followups': 110,
        'service_code_lookup': 50,
        'application_status': 50,
        'field_visits': 40,
        'knowledge_query': 40,
        'igrs_can_rule': 30,
        'other': 40
    }
    
    generated = []
    category_counts = {k: 0 for k in targets.keys()}
    
    candidates = []
    for item in data:
        cat = map_intent_to_category(get_intent(item))
        candidates.append((cat, item))
        
    random.shuffle(candidates)
    
    for cat, item in candidates:
        if category_counts.get(cat, 0) >= targets.get(cat, 0):
            continue
            
        new_item = copy.deepcopy(item)
        translated = False
        
        user_msg_indices = [i for i, m in enumerate(new_item['messages']) if m['role'] == 'user']
        
        # Translate each user message
        all_translated_ok = True
        for relative_idx, absolute_idx in enumerate(user_msg_indices):
            original_q = new_item['messages'][absolute_idx]['content']
            
            effective_cat = cat
            if cat == 'followups':
                if relative_idx == 0:
                    # The first query in a followup conversation is always a setup query (search/listing)
                    effective_cat = 'application_search'
                else:
                    effective_cat = 'followups'
            else:
                if relative_idx > 0:
                    # Any subsequent query in a normal conversation is a followup
                    effective_cat = 'followups'
                else:
                    effective_cat = cat
            
            t = translate_query(original_q, effective_cat)
                
            if t:
                new_item['messages'][absolute_idx]['content'] = t
                translated = True
            else:
                all_translated_ok = False
                break
                
        if translated and all_translated_ok:
            new_item['meta']['source'] = 'tamil_synthetic'
            generated.append(new_item)
            category_counts[cat] += 1
            
        if sum(category_counts.values()) >= 500:
            break
            
    print("Generated counts:", category_counts)
    print(f"Total: {len(generated)}")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for item in generated:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')

if __name__ == '__main__':
    generate_examples()
