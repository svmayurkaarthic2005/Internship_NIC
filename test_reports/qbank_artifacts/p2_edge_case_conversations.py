"""Phase 8 live API validation for the P2 routing-edge-case remediation.
Not part of the application. Reads QBANK_EMAIL/QBANK_PASSWORD with a
fallback to the seeded test account."""
import os
import json
import time
import requests

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


def ask(token, session_id, message, chat_history, timeout=200):
    # `chat_history` is a client-supplied field (sessionStorage in the real
    # frontend) -- the server does not reload it from the DB on its own
    # (backend/routers/chat.py's `ChatRequest.chat_history`, "Step 0: Use
    # provided chat history from client" in orchestrator.py). Earlier runs
    # of this script omitted it entirely, which made every context-dependent
    # follow-up look like it had no prior turns at all.
    r = requests.post(
        f"{BASE}/chat", headers={"Authorization": f"Bearer {token}"},
        json={"session_id": session_id, "message": message, "chat_history": chat_history},
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()["data"]
    return data.get("response", ""), data.get("intent")


CONVERSATIONS = {
    "A_gratitude_farewell": [
        "Hello, good morning.",
        "What can you help me with?",
        "Thanks for the explanation.",
        "Goodbye.",
    ],
    "B_identity": [
        "Who are you?",
        "Who am I?",
        "Which officer account is currently active?",
    ],
    "C_registration_date": [
        "Investigate 2026/0153/02/045470.",
        "What is its status?",
        "When was the registration document registered?",
    ],
    "D_rejection_workflow": [
        "Investigate 2026/0153/02/045470.",
        "What happened before the rejection?",
        "Which desk handled it immediately before the final rejection?",
    ],
    "E_mixed": [
        "Thanks, now check application 2026/0153/02/045470.",
        "Goodbye, but first show the status of 2026/0154/02/012766.",
    ],
}


def main():
    token = login()
    results = {}
    for name, turns in CONVERSATIONS.items():
        print(f"\n=== Conversation {name} ===")
        session_id = new_session(token)
        convo_log = []
        history = []
        for q in turns:
            t0 = time.time()
            try:
                answer, intent = ask(token, session_id, q, history)
            except Exception as exc:
                answer, intent = f"[ERROR: {exc}]", None
            dt = time.time() - t0
            print(f"Q: {q}")
            print(f"A ({dt:.1f}s, intent={intent}): {answer[:300]}")
            convo_log.append({"q": q, "a": answer, "intent": intent, "seconds": round(dt, 1)})
            history.append({"role": "user", "content": q})
            history.append({"role": "assistant", "content": answer})
        results[name] = convo_log
    with open("test_reports/qbank_artifacts/p2_edge_case_results.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print("\nwrote test_reports/qbank_artifacts/p2_edge_case_results.json")


if __name__ == "__main__":
    main()
