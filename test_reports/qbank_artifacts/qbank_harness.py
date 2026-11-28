"""Ad-hoc test harness for the sample_db_new question-bank QA run.
Not part of the application; temporary diagnostic instrumentation.
Drives the real /api/v1/chat/stream endpoint exactly as the frontend does,
maintaining chat_history across turns within one chain.
"""
import os
import sys
import json
import time
import uuid
import requests

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:8000/api/v1"
# Seeded local sample-DB test officer only (see CLAUDE.md "Test login
# credentials") -- never a real account. Override via env for a different
# test officer without editing this file.
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
    t0 = time.time()
    r = requests.post(
        f"{BASE}/chat/stream",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"message": message, "session_id": session_id, "language": "auto", "chat_history": chat_history},
        stream=True,
        timeout=180,
    )
    content = ""
    meta = {}
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        raw = line[6:]
        if raw.strip() == "[DONE]":
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            print(f"    [raw SSE line could not parse: {raw[:300]!r}]")
            continue
        if "content" in payload:
            content += payload["content"]
        if payload.get("error"):
            meta = payload
        if payload.get("done"):
            meta = payload
    elapsed = time.time() - t0
    return content, elapsed, meta, r.status_code


def run_chain(chain_id, questions, out_path):
    token = login()
    session_id = new_session(token)
    chat_history = []
    results = []
    for i, q in enumerate(questions, 1):
        try:
            answer, elapsed, meta, status = send(token, session_id, q, chat_history)
        except Exception as e:
            results.append({"turn": i, "question": q, "error": str(e)})
            print(f"[{chain_id}] Q{i} ERROR: {e}")
            chat_history.append({"role": "user", "content": q})
            chat_history.append({"role": "assistant", "content": "(no response - error)"})
            continue
        chat_history.append({"role": "user", "content": q})
        chat_history.append({"role": "assistant", "content": answer})
        results.append({"turn": i, "question": q, "answer": answer, "latency_s": round(elapsed, 2), "http_status": status})
        print(f"[{chain_id}] Q{i} ({elapsed:.1f}s): {answer[:200]!r}")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"chain": chain_id, "session_id": session_id, "turns": results}, f, ensure_ascii=False, indent=2)
    return results


if __name__ == "__main__":
    chain_file = sys.argv[1]
    out_path = sys.argv[2]
    with open(chain_file, "r", encoding="utf-8") as f:
        spec = json.load(f)
    run_chain(spec["chain_id"], spec["questions"], out_path)
