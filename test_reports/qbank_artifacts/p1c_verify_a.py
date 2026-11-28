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

token = login(); session_id = new_session(token)
chat_history = []
Qs = [
    "Compare 2026/0153/02/045470 and 2026/0153/02/045472. Compare their survey numbers, subdivisions, old patta numbers, registration documents, statuses and rejection remarks.",
    "What about the other one?",
    "Which one was submitted first?",
    "What was different in the registration details of the other application?",
    "Was that the rejected one?",
    "What was the reason for that?",
    "No, I meant the update date, not submission date.",
    "Show both applications again in one comparison.",
    "Compare the first and second applications again.",
    "Switch back to the first one. What was its rejection reason?",
]
for q in Qs:
    a = send(token, session_id, q, chat_history)
    chat_history.append({"role": "user", "content": q})
    chat_history.append({"role": "assistant", "content": a})
    print("Q:", q)
    print("A:", a[:500])
    print()
