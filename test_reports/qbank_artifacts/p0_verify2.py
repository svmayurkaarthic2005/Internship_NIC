"""Re-run the exact original Chain4 / Chain14 sequences (with real preceding
context) to confirm the P0-2 'automatically'->automutated bug is fixed in
the exact context it was originally found in, not just standalone."""
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
    chat_history = []
    out = []
    for q in questions:
        a = send(token, session_id, q, chat_history)
        chat_history.append({"role": "user", "content": q})
        chat_history.append({"role": "assistant", "content": a})
        out.append({"q": q, "a": a})
        print(f"[{name}] Q: {q}\n[{name}] A: {a[:300]}\n")
    return out


results = {}
results["Chain4_original_context"] = run("Chain4", [
    "Consider 2026/0153/02/045470. This is an NISD application, which normally follows a workflow without a "
    "mandatory field inspection. However, I can see a field_visit_dt value in one workflow entry. Can you "
    "inspect the raw workflow evidence and the service type separately, and explain whether the stored date "
    "actually establishes a required field visit?",
    "Which workflow transition contains that date?",
    "Does a populated date column automatically mean that the service requires an inspection?",
])
results["Chain14_original_context"] = run("Chain14", [
    "I need to inspect the ownership information for 2026/0153/02/045472. There are separate old-owner and "
    "IGRS-owner extracts, and I don't want their rows mixed up. Please count the rows in each source, "
    "identify the fields they share, compare the available owner and relationship information, and "
    "distinguish source records from the applicant-information record.",
    "How many IGRS-owner records are linked to this application?",
    "Does the old-owner table contain the same number of rows?",
    "Can you explain why these two tables should not automatically be treated as identical?",
])
with open("test_reports/qbank_artifacts/p0_verify2_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("done")
