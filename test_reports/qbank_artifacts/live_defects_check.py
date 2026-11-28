"""Live re-check of the reported runtime defects against a real server (not part of the application).

1. 0158 two-application comparison + "the other one" (/chat and /chat/stream)
4. GET /api/v1/applications/ with and without a JWT; /frontend/channel_report.html carries no data
3. telemetry: one record per /chat and /chat/stream turn (read from the server log afterwards)
"""
import json
import os
import re
import sys
import time

import requests

BASE = os.environ.get("QBANK_BASE", "http://127.0.0.1:8001")
EMAIL = os.environ.get("QBANK_EMAIL", "arun_m@sis.tn.gov.in")
PASSWORD = os.environ.get("QBANK_PASSWORD", "Test@1234")
X, Y = "2026/0158/02/000002", "2026/0158/02/000003"


def strip(a):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", a or "")).strip()


def main():
    out = {}
    tok = requests.post(f"{BASE}/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30).json()["data"]["access_token"]
    H = {"Authorization": f"Bearer {tok}"}
    sid = requests.post(f"{BASE}/api/v1/chat/sessions", headers=H, json={}, timeout=30).json()["data"]["session_id"]
    turns = []
    for q in [f"Compare {X} and {Y}.", "What about the other one?", "What is its status?", "Show both applications again."]:
        t = time.time()
        d = requests.post(f"{BASE}/api/v1/chat", headers=H, json={"session_id": sid, "message": q}, timeout=200).json()["data"]
        a = strip(d.get("response"))
        turns.append({"q": q, "intent": d.get("intent"), "ms": round((time.time() - t) * 1000, 1),
                      "X": X in a, "Y": Y in a, "answer": a[:160]})
    out["compare_0158_chat"] = turns
    sid2 = requests.post(f"{BASE}/api/v1/chat/sessions", headers=H, json={}, timeout=30).json()["data"]["session_id"]
    stream_turns = []
    for q in [f"Compare {X} and {Y}.", "What about the other one?"]:
        r = requests.post(f"{BASE}/api/v1/chat/stream", headers=H, json={"session_id": sid2, "message": q}, timeout=200, stream=True)
        parts = []
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data: "):
                try:
                    parts.append(json.loads(line[6:]).get("content") or "")
                except ValueError:
                    pass
        a = strip("".join(parts))
        stream_turns.append({"q": q, "X": X in a, "Y": Y in a, "answer": a[:160]})
    out["compare_0158_stream"] = stream_turns
    r_auth = requests.get(f"{BASE}/api/v1/applications/?limit=5", headers=H, timeout=30)
    body = r_auth.json()
    out["applications_with_jwt"] = {"status": r_auth.status_code, "total": body["data"]["total"],
                                    "returned": len(body["data"]["applications"]), "message": body.get("message")}
    r_pend = requests.get(f"{BASE}/api/v1/applications/?status=pending&limit=100", headers=H, timeout=30).json()
    out["applications_pending_total"] = r_pend["data"]["total"]
    out["applications_without_jwt"] = requests.get(f"{BASE}/api/v1/applications/", timeout=30).status_code
    page = requests.get(f"{BASE}/frontend/channel_report.html", timeout=30)
    out["channel_report"] = {"status": page.status_code, "bytes": len(page.content),
                             "embedded_RAW": "const RAW = [" in page.text,
                             "application_numbers_in_page": len(re.findall(r"\b20\d\d/0\d{3}/\d\d/\d{6}\b", page.text)),
                             "mobile_like_numbers_in_page": len(re.findall(r"\b[6-9]\d{9}\b", page.text))}
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
