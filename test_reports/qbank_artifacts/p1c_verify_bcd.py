import os, sys, json, requests
sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8000/api/v1"
EMAIL = os.environ.get("QBANK_EMAIL", "sperumal_chn@sis.tn.gov.in")
PASSWORD = os.environ.get("QBANK_PASSWORD", "Test@1234")

def login():
    r = requests.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    r.raise_for_status()
    return r.json()["data"]["access_token"]

def new_session(token):
    r = requests.post(f"{BASE}/chat/sessions", headers={"Authorization": f"Bearer {token}"}, json={}, timeout=30)
    r.raise_for_status()
    return r.json()["data"]["session_id"]

def send(token, session_id, message, chat_history):
    r = requests.post(f"{BASE}/chat/stream",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"message": message, "session_id": session_id, "language": "auto", "chat_history": chat_history},
        stream=True, timeout=180)
    content = ""
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        raw = line[6:]
        if raw.strip() == "[DONE]":
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if "content" in payload:
            content += payload["content"]
    return content

def run(name, questions):
    token = login(); session_id = new_session(token)
    ch = []
    for q in questions:
        a = send(token, session_id, q, ch)
        ch.append({"role": "user", "content": q})
        ch.append({"role": "assistant", "content": a})
        print(f"[{name}] Q: {q}")
        print(f"[{name}] A: {a[:300]}")
        print()
    return session_id

sid_b = run("TestB", [
    "Compare 2026/0153/02/045470 and 2026/0153/02/045472.",
    "Show the second application.",
    "What is its status?",
    "Show both applications again.",
    "Now show only the first one.",
    "What about the other one?",
])

sid_c = run("TestC", [
    "Compare 2026/0153/02/045470 and 2026/0153/02/045472.",
    "What about the other one?",
    "Now investigate 2026/0154/02/012766.",
    "What is its status?",
    "Compare it with the first application from our earlier comparison.",
])

print("=== TestD: session isolation ===")
token_a = login(); sid_a = new_session(token_a)
send(token_a, sid_a, "Compare 2026/0153/02/045470 and 2026/0153/02/045472.", [])
token_b = login(); sid_b2 = new_session(token_b)
ans_b = send(token_b, sid_b2, "What about the other one?", [])
print("Session B (fresh, no pair) answer to 'other one':", ans_b[:300])
