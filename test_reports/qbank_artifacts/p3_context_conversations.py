"""Phase 9 live API validation for the P3 canonical-context remediation.
Not part of the application. Runs Test A (explicit switch overrides stale
context) both WITH and WITHOUT client-supplied chat_history, to confirm the
fix holds in both variants per the task's explicit acceptance criterion."""
import os
import json
import time
import requests

BASE = "http://127.0.0.1:8000/api/v1"
EMAIL = os.environ.get("QBANK_EMAIL", "sperumal_chn@sis.tn.gov.in")
PASSWORD = os.environ.get("QBANK_PASSWORD", "Test@1234")

APP_A = "2026/0153/02/045470"
APP_B = "2026/0154/02/012766"
APP_PAIR_2 = "2026/0153/02/045472"


def login():
    r = requests.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    r.raise_for_status()
    return r.json()["data"]["access_token"]


def new_session(token):
    r = requests.post(f"{BASE}/chat/sessions", headers={"Authorization": f"Bearer {token}"}, json={}, timeout=30)
    r.raise_for_status()
    return r.json()["data"]["session_id"]


def ask(token, session_id, message, chat_history=None, timeout=200):
    payload = {"session_id": session_id, "message": message}
    if chat_history is not None:
        payload["chat_history"] = chat_history
    r = requests.post(f"{BASE}/chat", headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()["data"]
    return data.get("response", ""), data.get("intent")


def run_conversation(token, turns, send_history: bool, label):
    print(f"\n=== {label} (send_history={send_history}) ===")
    session_id = new_session(token)
    history = []
    log = []
    for q in turns:
        t0 = time.time()
        answer, intent = ask(token, session_id, q, history if send_history else None)
        dt = time.time() - t0
        print(f"Q: {q}")
        print(f"A ({dt:.1f}s, intent={intent}): {answer[:250]}")
        log.append({"q": q, "a": answer, "intent": intent, "seconds": round(dt, 1)})
        history.append({"role": "user", "content": q})
        history.append({"role": "assistant", "content": answer})
    return log


def main():
    token = login()
    results = {}

    test_a = [
        f"Investigate {APP_A}.",
        "What is its status?",
        f"Goodbye, but first show the status of {APP_B}.",
        "When was that application submitted?",
    ]
    results["A_with_history"] = run_conversation(token, test_a, True, "Test A: explicit switch (WITH chat_history)")
    results["B_without_history"] = run_conversation(token, test_a, False, "Test B: explicit switch (WITHOUT chat_history)")

    test_d = [
        f"Compare {APP_A} and {APP_PAIR_2}.",
        "What about the other one?",
        "What is its status?",
        "Show both applications again.",
        "Switch back to the first application.",
    ]
    results["D_with_history"] = run_conversation(token, test_d, True, "Test D: pair/focus continuity (WITH chat_history)")
    results["D_without_history"] = run_conversation(token, test_d, False, "Test D: pair/focus continuity (WITHOUT chat_history)")

    with open("test_reports/qbank_artifacts/p3_context_results.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print("\nwrote test_reports/qbank_artifacts/p3_context_results.json")


if __name__ == "__main__":
    main()
