import os, re, sys
sys.stdout.reconfigure(encoding='utf-8')

matches = []
for root, dirs, files in os.walk('.'):
    # skip venv, git
    if any(k in root for k in ['.venv', '.git', '__pycache__', '.pytest_cache']):
        continue
    for f in files:
        if f.endswith(('.py', '.txt', '.md', '.sql', '.html', '.js')):
            p = os.path.join(root, f)
            try:
                txt = open(p, encoding='utf-8').read()
            except Exception:
                continue
            for i, line in enumerate(txt.splitlines(), 1):
                if re.search(r'zonal\s+level\s+tahsildar', line, re.I):
                    matches.append((p, i, line.strip()))

print(f"Total occurrences of 'zonal level tahsildar': {len(matches)}")
for p, i, line in matches:
    print(f"{p}:{i}: {line}")
