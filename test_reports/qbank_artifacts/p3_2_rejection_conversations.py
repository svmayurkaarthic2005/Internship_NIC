"""Phase 6 live API validation for the P3.2 rejection-reason remediation.
Not part of the application."""
import os
import json
import time
import requests

BASE = "http://127.0.0.1:8000/api/v1"
EMAIL = os.environ.get("QBANK_EMAIL", "sperumal_chn@sis.tn.gov.in")
PASSWORD = os.environ.get("QBANK_PASSWORD", "Test@1234")

APP_A = "2026/0153/02/045470"
APP_B = "2026/0153/02/045472"
APP_APPROVED = "2026/0154/02/012766"

SEQUENCE = [
    f"Compare {APP_A} and {APP_B}.",
    "What about the other one?",
    "What is its status?",
    "Show both applications again.",
    "Switch back to the first application.",
    "What was its rejection reason?",
]


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


def run_conversation(token, turns, history_mode, label):
    print(f"\n=== {label} (history_mode={history_mode}) ===")
    session_id = new_session(token)
    history = []
    stale_history = [
        {"role": "user", "content": f"Compare {APP_APPROVED} and 2026/0154/02/999999."},
        {"role": "assistant", "content": f"**{APP_APPROVED}** vs **2026/0154/02/999999**"},
    ]
    log = []
    for q in turns:
        t0 = time.time()
        sent = None if history_mode == "none" else (stale_history if history_mode == "stale" else history)
        answer, intent = ask(token, session_id, q, sent)
        dt = time.time() - t0
        print(f"Q: {q}")
        print(f"A ({dt:.1f}s, intent={intent}): {answer[:280]}")
        log.append({"q": q, "a": answer, "intent": intent, "seconds": round(dt, 1)})
        history.append({"role": "user", "content": q})
        history.append({"role": "assistant", "content": answer})
    return log


def main():
    token = login()
    results = {
        "with_history": run_conversation(token, SEQUENCE, "normal", "P3.2: WITH chat_history"),
        "without_history": run_conversation(token, SEQUENCE, "none", "P3.2: WITHOUT chat_history"),
        "stale_history": run_conversation(token, SEQUENCE, "stale", "P3.2: STALE chat_history"),
        "generic_guidance": run_conversation(
            token, ["What are the common reasons applications get rejected?"], "normal", "P3.2: generic guidance"),
        "approved_app": run_conversation(
            token, [f"Why was {APP_APPROVED} rejected?"], "normal", "P3.2: approved app, not rejected"),
    }
    with open("test_reports/qbank_artifacts/p3_2_rejection_results.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print("\nwrote test_reports/qbank_artifacts/p3_2_rejection_results.json")


if __name__ == "__main__":
    main()
