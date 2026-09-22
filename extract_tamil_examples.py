import json
import random

with open('train_augmented.jsonl', 'r', encoding='utf-8') as f:
    lines = f.readlines()

tamil_samples = []
for line in lines:
    if not line.strip(): continue
    try:
        data = json.loads(line)
        msgs = data.get('messages', [])
        for m in msgs:
            content = m.get('content', '')
            if any('\u0b80' <= c <= '\u0bff' for c in content):
                tamil_samples.append(data)
                break
    except: pass

print(f'Found {len(tamil_samples)} Tamil samples.')
for s in tamil_samples[:2]:
    print(json.dumps(s, ensure_ascii=False))
