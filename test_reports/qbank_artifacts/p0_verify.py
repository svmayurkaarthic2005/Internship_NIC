"""P0 fix verification: real API, real DB. Not part of the application --
temporary diagnostic instrumentation for the P0 remediation task.
"""
import os
import sys
import json
import requests

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
    r = requests.post(
        f"{BASE}/chat/stream",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"message": message, "session_id": session_id, "language": "auto", "chat_history": chat_history},
        stream=True, timeout=180,
    )
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


def run_sequence(name, questions):
    token = login()
    session_id = new_session(token)
    chat_history = []
    results = []
    for q in questions:
        a = send(token, session_id, q, chat_history)
        chat_history.append({"role": "user", "content": q})
        chat_history.append({"role": "assistant", "content": a})
        results.append({"question": q, "answer": a})
        print(f"[{name}] Q: {q}")
        print(f"[{name}] A: {a[:400]}")
        print()
    return results


ALL = {}

# ---- P0-1 reproduction + mandatory regression set ----
ALL["P0-1_mandatory"] = run_sequence("P0-1", [
    "Explain the application number format.",
    "What is the structure of an application number?",
    "Show applications under service code 0154 and whether owner information is present.",
    "I need information about application 2026/0154/02/012766.",
    "Compare the application information for these two records.",
    "Does the application number contain a service code?",
])

# ---- P0-1 the exact Chain 19 reproduction ----
ALL["P0-1_chain19_repro"] = run_sequence("Chain19", [
    "Show me the applications under service code 0154, but I am mainly interested in applications "
    "involving more than one temporary subdivision. For each matching application, include the "
    "application number, survey number, existing patta, number of subdivision rows, application status "
    "and whether the proposed owner information is present. Do not count owner rows as separate applications.",
])

# ---- P0-2 reproduction + mandatory regression set ----
ALL["P0-2_mandatory"] = run_sequence("P0-2", [
    "Does a populated date column automatically mean a visit is required?",
    "Why should these two tables not automatically be treated as identical?",
    "Was the application automutated?",
    "Show the automutated status for this application.",
    "Is auto mutation recorded?",
    "Show the application information and payment details.",
])

# ---- P0-3 mandatory sequence: the exact 0158 reproduction + regression set ----
ALL["P0-3_0158_sequence"] = run_sequence("P0-3", [
    "Investigate application 2026/0158/02/000002. Trace its correction-modification desk recommendations "
    "and final approval information.",
    "When did the SIS recommendation occur?",
    "What happened at ZDT, SD and DIS after that?",
    "Does an Approved remark automatically mean the entire application is closed?",
    "Check the UAREG records too.",
])

ALL["P0-3_mandatory_singletons"] = run_sequence("P0-3-singles", [
    "For application 2026/0153/02/045470, show the SIS recommendation and remarks.",
    "For application 2026/0154/02/012766, show the workflow recommendation history.",
])

with open("test_reports/qbank_artifacts/p0_verify_results.json", "w", encoding="utf-8") as f:
    json.dump(ALL, f, ensure_ascii=False, indent=2)
print("Wrote test_reports/qbank_artifacts/p0_verify_results.json")
