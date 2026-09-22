import json
import random
import copy

INPUT_FILE = 'train_augmented.jsonl'
OUTPUT_FILE = 'train_tamil_additions.jsonl'

TARGET_COUNT = 500

PROMPTS = {
    'application_listing': [
        "எனது நிலுவை விண்ணப்பங்களை காட்டு",
        "நிலுவையில் உள்ள ISD விண்ணப்பங்கள் என்னென்ன?",
        "என் வார்டில் உள்ள விண்ணப்பங்களை காட்டு",
        "0154 விண்ணப்பங்களை பட்டியலிடு",
        "இன்று வந்த விண்ணப்பங்கள் உள்ளதா?",
        "pending applications irukka?",
        "enoda ISD applications kudu",
        "ward 102 la applications kaatu",
        "inniku vandha applications irukka?",
        "விண்னப்பம் காமி",
        "applications iruka",
        "pendng applications",
        "show all pending list",
        "reject ana applications kaatu",
        "approve ana files list",
        "என்னோட அப்ளிகேஷன்ஸ் காட்டு"
    ],
    'application_count': [
        "இந்த மாதம் எத்தனை விண்ணப்பங்கள் வந்துள்ளன?",
        "இந்த வாரம் எத்தனை ISD விண்ணப்பங்கள்?",
        "நேற்று எத்தனை விண்ணப்பங்கள் வந்தது?",
        "நிலுவையில் எத்தனை இருக்கிறது?",
        "நிராகரிக்கப்பட்ட விண்ணப்பங்கள் எத்தனை?",
        "indha month evlo applications?",
        "pending applications ethana?",
        "reject aana applications count enna?",
        "evalo applicatins iruku",
        "ethana pending?",
        "evlo fail aachu",
        "today count enna?",
        "mottham ethana applications?"
    ],
    'application_status': [
        "இந்த விண்ணப்பத்தின் நிலை என்ன?",
        "இந்த application status என்ன?",
        "விண்ணப்பம் முடிந்ததா?",
        "இந்த கோப்பு நிலுவையிலா?",
        "இந்த application approve ஆனதா?",
        "indha application status enna?",
        "file approve aayiducha?",
        "application statur enna",
        "status sollu",
        "is this application pendinga?",
        "idhoda nelamai enna?",
        "status eppadi irukku?"
    ],
    'service_code': [
        "0154 என்ன?",
        "0154 என்றால் என்ன?",
        "0154 எதைக் குறிக்கிறது?",
        "ISD என்ன?",
        "ISD na enna?",
        "0155 meaning என்ன?",
        "0170 சேவை குறியீடு என்ன?",
        "0153 pathi sollu",
        "0153 endral enna?",
        "nisd details kodu",
        "service code 0154 pathi vivari",
        "merge na enna bro",
        "isd meaning enna?"
    ],
    'field_visits': [
        "என் கள ஆய்வுகள் என்ன?",
        "நிலுவையில் உள்ள கள ஆய்வுகள் உள்ளதா?",
        "இந்த வார கள ஆய்வுகள் என்ன?",
        "கள ஆய்வு தேதி என்ன?",
        "கள ஆய்வு தேதியை மாற்ற முடியுமா?",
        "field visit eppo?",
        "kalam aivu eppo irukku?",
        "indha week field visits kaatu",
        "field visit thethi",
        "kalam aivu dates",
        "today field visit irukka?",
        "inraiya kalam aivu"
    ],
    'knowledge_query': [
        "ISD செயல்முறை என்ன?",
        "NISD-க்கு கள ஆய்வு வேண்டுமா?",
        "MERGE எப்படி நடக்கும்?",
        "கள ஆய்வு யார் செய்வார்கள்?",
        "IGRS Form 6 என்றால் என்ன?",
        "Sub-Registrar என்றால் என்ன?",
        "ISD process enna?",
        "NISD field visit thevaiya?",
        "merge eppadi nadakkum?",
        "csc na enna",
        "how to check status in tamil",
        "workflow enna",
        "tahsildar role enna"
    ],
    'negation_ambiguity': [
        "இந்த application கிடைக்கிறதா?",
        "999999 application இருக்கிறதா?",
        "இந்த survey number இல்லை என்றால்?",
        "இந்த ward என் jurisdiction-ல இல்லையா?",
        "அந்த தகவல் கிடைக்கவில்லை என்றால் என்ன சொல்ல வேண்டும்?",
        "indha app irukka?",
        "ward 99 jurisdiction la illaya?",
        "idhu en limit la varutha?",
        "jurisdiction thaandi irukka?",
        "thappana number kudutha enna aagum?"
    ]
}

FOLLOWUP_PROMPTS = {
    'followup_list': [
        "அவற்றின் நிலையை காட்டு",
        "application number மட்டும்",
        "அவற்றின் survey number?",
        "avatroda can number kaatu",
        "stage mattum",
        "survey numbers sollu"
    ],
    'followup_selection': [
        "இரண்டாவது விண்ணப்பத்தை காட்டு",
        "முதல் application details",
        "கடைசி ஒன்று",
        "the 2nd one details",
        "moonathu",
        "modhal record"
    ],
    'followup_aggregate': [
        "மொத்தம் எவ்வளவு?",
        "total fee evlo?",
        "mottha katanm"
    ],
    'followup_completeness': [
        "வேற இருக்கா?",
        "is that all?",
        "avlo thana?",
        "meethi irukka?"
    ]
}

def load_data():
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]

def get_intent(item):
    return item.get('meta', {}).get('intent', '')

def map_intent_to_category(intent):
    if intent in ['application_search', 'pending_applications', 'assigned_today']:
        return 'application_listing'
    if intent == 'application_count':
        return 'application_count'
    if intent in ['application_status', 'survey_detail']:
        return 'application_status'
    if intent in ['service_code_lookup', 'service_code_guide']:
        return 'service_code'
    if intent in ['field_visits', 'fv_change_date', 'fv_visit_plan']:
        return 'field_visits'
    if intent in ['knowledge_query', 'igrs_can_rule', 'officer_workload', 'jurisdiction_summary']:
        return 'knowledge_query'
    if intent in ['error_ambiguity', 'out_of_scope', 'fallback', 'small_talk', 'greeting']:
        return 'negation_ambiguity'
    if intent.startswith('followup_'):
        return intent
    return None

def generate_examples():
    data = load_data()
    random.seed(42)
    
    # Target distribution
    targets = {
        'application_listing': 80,
        'application_count': 60,
        'application_status': 50,
        'followup_list': 50,
        'followup_selection': 30,
        'followup_aggregate': 10,
        'followup_completeness': 10,
        'service_code': 50,
        'field_visits': 40,
        'knowledge_query': 80,
        'negation_ambiguity': 40
    }
    
    # Group by category
    categorized_data = {k: [] for k in targets.keys()}
    for item in data:
        cat = map_intent_to_category(get_intent(item))
        if cat in categorized_data:
            categorized_data[cat].append(item)
            
    generated = []
    
    for cat, target_count in targets.items():
        if not categorized_data[cat]:
            print(f"Warning: No examples found for category {cat}")
            continue
            
        pool = categorized_data[cat]
        sampled = random.choices(pool, k=target_count)
        
        for item in sampled:
            new_item = copy.deepcopy(item)
            new_item['meta']['source'] = 'tamil_synthetic'
            
            # Find the user messages
            user_msg_indices = [i for i, m in enumerate(new_item['messages']) if m['role'] == 'user']
            
            if cat.startswith('followup_'):
                # For followups, change the FIRST user message to a general listing one (often required for context)
                if len(user_msg_indices) > 0:
                    new_item['messages'][user_msg_indices[0]]['content'] = random.choice(PROMPTS['application_listing'])
                # Change the FOLLOWUP user message
                if len(user_msg_indices) > 1:
                    new_item['messages'][user_msg_indices[1]]['content'] = random.choice(FOLLOWUP_PROMPTS.get(cat, FOLLOWUP_PROMPTS['followup_list']))
            else:
                # Normal queries: just change the first user message
                if len(user_msg_indices) > 0:
                    new_item['messages'][user_msg_indices[0]]['content'] = random.choice(PROMPTS.get(cat, PROMPTS['knowledge_query']))
            
            generated.append(new_item)
            
    random.shuffle(generated)
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for item in generated:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
            
    print(f"Generated {len(generated)} Tamil examples in {OUTPUT_FILE}")

if __name__ == '__main__':
    generate_examples()
