import json

new_records = [
    {
        "messages": [
            {"role": "system", "content": "You are the SIS AI Assistant for Tamil Nadu Sub Inspector Surveyor officers. Answer only from the department's register; never invent an application number, count, or date."},
            {"role": "user", "content": "show cancelled applications"},
            {"role": "assistant", "content": "Found 2 application(s):\nApplication No. | Type | Survey No. | Sub-Divisions | Status | Stage | Submitted | Ward | Block |\n2025/0154/28/100098 | ISD | 45 | 45/1A | Rejected | REJECTED | 2025-02-14 | 102 | 0015 |\n2025/0153/28/100099 | NISD | 67 | 67/2 | Rejected | REJECTED | 2025-03-01 | 102 | 0015 |"}
        ],
        "meta": {"intent": "pending_applications", "filters": {"status": "rejected"}}
    },
    {
        "messages": [
            {"role": "system", "content": "You are the SIS AI Assistant for Tamil Nadu Sub Inspector Surveyor officers. Answer only from the department's register; never invent an application number, count, or date."},
            {"role": "user", "content": "how many applications got cancelled yesterday?"},
            {"role": "assistant", "content": "Found 1 application(s) Received Yesterday (2026-09-17) that are Rejected:\nApplication No. | Type | Survey No. | Sub-Divisions | Status | Stage | Submitted | Ward | Block |\n2026/0154/28/100100 | ISD | 12 | 12/3 | Rejected | REJECTED | 2026-09-17 | 102 | 0015 |"}
        ],
        "meta": {"intent": "application_count", "filters": {"status": "rejected"}}
    },
    {
        "messages": [
            {"role": "system", "content": "You are the SIS AI Assistant for Tamil Nadu Sub Inspector Surveyor officers. Answer only from the department's register; never invent an application number, count, or date."},
            {"role": "user", "content": "any cancelled files in ward 102?"},
            {"role": "assistant", "content": "Found 1 application(s) in Ward 102:\nApplication No. | Type | Survey No. | Sub-Divisions | Status | Stage | Submitted | Ward | Block |\n2024/0153/28/100101 | NISD | 88 | - | Rejected | REJECTED | 2024-05-10 | 102 | 0015 |"}
        ],
        "meta": {"intent": "pending_applications", "filters": {"status": "rejected", "ward": "102"}}
    },
    {
        "messages": [
            {"role": "system", "content": "You are the SIS AI Assistant for Tamil Nadu Sub Inspector Surveyor officers. Answer only from the department's register; never invent an application number, count, or date."},
            {"role": "user", "content": "list cancelled application no only"},
            {"role": "assistant", "content": "Found 2 application(s):\nApplication No. |\n2025/0154/28/100098 |\n2025/0153/28/100099 |"}
        ],
        "meta": {"intent": "pending_applications", "filters": {"status": "rejected"}}
    },
    {
        "messages": [
            {"role": "system", "content": "You are the SIS AI Assistant for Tamil Nadu Sub Inspector Surveyor officers. Answer only from the department's register; never invent an application number, count, or date."},
            {"role": "user", "content": "cancel ana applications yethavathu irukka?"},
            {"role": "assistant", "content": "Found 1 application(s):\nApplication No. | Type | Survey No. | Sub-Divisions | Status | Stage | Submitted | Ward | Block |\n2025/0153/28/100102 | NISD | 110 | 110/4 | Rejected | REJECTED | 2025-06-22 | 102 | 0015 |"}
        ],
        "meta": {"intent": "pending_applications", "filters": {"status": "rejected"}}
    }
]

with open('train_augmented.jsonl', 'a', encoding='utf-8') as f:
    for rec in new_records:
        f.write(json.dumps(rec) + '\n')

print('Added 5 cancelled->rejected examples to train_augmented.jsonl')
