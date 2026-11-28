"""Live API verification for P1-2 (evidence-grounded reasoning).

Runs Scenarios A-D plus their follow-ups, and the Phase 5 deterministic
regression list, through the real chat pipeline:
  POST /api/v1/auth/login -> POST /api/v1/chat/sessions -> POST /api/v1/chat/stream
Prints the verbatim answer for each turn so the remediation report can
quote real output, not synthetic examples.
"""
import json
import os
import time

import requests

BASE = "http://127.0.0.1:8000"
EMAIL = os.environ.get("QBANK_EMAIL", "sperumal_chn@sis.tn.gov.in")
PASSWORD = os.environ.get("QBANK_PASSWORD", "Test@1234")


def login():
    r = requests.post(f"{BASE}/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=20)
    r.raise_for_status()
    return r.json()["data"]["access_token"]


def new_session(token):
    r = requests.post(f"{BASE}/api/v1/chat/sessions", headers={"Authorization": f"Bearer {token}"}, timeout=20)
    r.raise_for_status()
    return r.json()["data"]["session_id"]


def ask(token, session_id, message, timeout=400):
    t0 = time.time()
    r = requests.post(
        f"{BASE}/api/v1/chat/stream",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": message, "session_id": session_id},
        stream=True, timeout=timeout)
    r.raise_for_status()
    text = ""
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        payload = line[len("data: "):]
        if payload == "[DONE]":
            break
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if obj.get("content"):
            text += obj["content"]
        if obj.get("done"):
            break
    return text, time.time() - t0


def run_turn(token, session_id, label, message):
    text, dt = ask(token, session_id, message)
    print(f"[{label}] Q: {message}")
    print(f"[{label}] A ({dt:.1f}s): {text[:1500]}")
    print()
    return text, dt


def main():
    token = login()

    print("=== Scenario A: NISD field_visit_dt vs field-visit mandate ===")
    sid = new_session(token)
    run_turn(token, sid, "A1",
              "This is an NISD application, which normally does not require a field visit. "
              "However, I can see a field_visit_dt in one workflow row. Does that mean an "
              "inspection was mandatory or actually completed? Application 2026/0153/02/045470.")
    run_turn(token, sid, "A2", "Does a populated date column automatically mean the service requires an inspection?")

    print("=== Scenario B: desk Approved vs register closure ===")
    sid = new_session(token)
    run_turn(token, sid, "B1",
              "Application 2026/0158/02/001804. The register shows this application is still "
              "open, but the Tahsildar has recorded Approved. Does that mean the complete "
              "application is closed? Explain using the register, correction-modification "
              "record and latest workflow transition.")
    run_turn(token, sid, "B2", "What information is still missing before you can definitively say it is completed?")

    print("=== Scenario C: blank missing_docs vs rejection remark ===")
    sid = new_session(token)
    run_turn(token, sid, "C1",
              "Application 2026/0153/02/045470. What exactly was missing? The missing_docs "
              "field appears blank, but the rejection remark says linked documents were not "
              "submitted. Explain the difference.")

    print("=== Scenario D: old-owner vs IGRS-owner tables ===")
    sid = new_session(token)
    run_turn(token, sid, "D1",
              "Application 2026/0153/02/045472. The old-owner and IGRS-owner tables have "
              "different structures. Why should we not automatically treat them as identical? "
              "Explain what each table actually records.")

    print("=== Phase 5: deterministic lookups must stay deterministic ===")
    sid = new_session(token)
    for i, q in enumerate([
        "What is the application status for 2026/0153/02/045470?",
        "When was its registration document registered?",
        "When did the SIS recommendation occur for 2026/0158/02/000002?",
        "What are the temporary subdivisions for 2026/0154/02/012715?",
        "What is the new patta number for 2026/0154/02/012766?",
        "What amount and payment mode are recorded for 2026/0178/02/000267?",
        "Show the latest workflow transition.",
    ], 1):
        run_turn(token, sid, f"P5-{i}", q)


if __name__ == "__main__":
    main()
