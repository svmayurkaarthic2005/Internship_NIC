"""Phase 10 live API validation for the P2 hybrid-routing task.
Not part of the application -- a diagnostic driver, same pattern as the
P1-2 scripts beside it. Reads QBANK_EMAIL/QBANK_PASSWORD with a fallback
to the seeded test account.
"""
import os
import sys
import json
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


def ask(token, session_id, message, timeout=200):
    # The non-streaming endpoint is used for this validation run: its response
    # includes "intent" directly (the /stream SSE chunks never carry intent,
    # confirmed by reading backend/routers/chat.py and orchestrator.py --
    # every streamed chunk is {"content": ...} only), and it avoids a flaky
    # client-side chunked-read failure seen against /chat/stream in this run.
    r = requests.post(
        f"{BASE}/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={"session_id": session_id, "message": message},
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()["data"]
    return data.get("response", ""), data.get("intent")


CONVERSATIONS = {
    "A_small_talk": [
        "Hello, good morning.",
        "What can you help me with?",
        "Thanks for the explanation.",
        "Goodbye.",
    ],
    "B_domain_exclusion": [
        "Investigate 2026/0153/02/045470.",
        "What is its status?",
        "When was the registration document registered?",
        "What happened before the rejection?",
    ],
    "C_context_retention": [
        "Compare 2026/0153/02/045470 and 2026/0153/02/045472.",
        "What about the other one?",
        "Which one was submitted first?",
        "Show both applications again.",
    ],
    "D_ambiguous_wording": [
        "Tell me about this.",
        "Can you explain that?",
        "What about the other one?",
    ],
}


def main():
    token = login()
    results = {}
    for name, turns in CONVERSATIONS.items():
        print(f"\n=== Conversation {name} ===")
        session_id = new_session(token)
        convo_log = []
        for q in turns:
            import time
            t0 = time.time()
            try:
                answer, intent = ask(token, session_id, q)
            except Exception as exc:
                answer, intent = f"[ERROR: {exc}]", None
            dt = time.time() - t0
            print(f"Q: {q}")
            print(f"A ({dt:.1f}s, intent={intent}): {answer[:300]}")
            convo_log.append({"q": q, "a": answer, "intent": intent, "seconds": round(dt, 1)})
        results[name] = convo_log
    with open("test_reports/qbank_artifacts/p2_live_results.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print("\nwrote test_reports/qbank_artifacts/p2_live_results.json")


if __name__ == "__main__":
    main()
