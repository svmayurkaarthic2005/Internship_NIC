"""P3.3 completion -- live API validation: real server, real DB, real JWT. Not part of the application.

Runs Q1-Q9 with normal / absent / fixed-stale client history on /chat, the explicit-ID case, and
Q6-Q9 again on /chat/stream for parity. Writes p3_3_entity_resolution_results.json; the per-turn
server-side resolution trace is joined from the server log by p3_3_join_server_log.py."""
import os
import json
import time
import requests

BASE = "http://127.0.0.1:8000/api/v1"
EMAIL = os.environ.get("QBANK_EMAIL", "sperumal_chn@sis.tn.gov.in")
PASSWORD = os.environ.get("QBANK_PASSWORD", "Test@1234")

A, B = "2026/0153/02/045470", "2026/0153/02/045472"
S1, S2 = "2026/0154/02/012766", "2026/0154/02/999999"
STALE = [{"role": "user", "content": f"Compare {S1} and {S2}."},
         {"role": "assistant", "content": f"**{S1}** vs **{S2}**"}]
SEQ = [f"Compare {A} and {B}.", "What about the other one?", "What is its status?",
       "Show both applications again.", "Switch back to the first application.",
       "What was its rejection reason?", "Show its details.", "Show its workflow history.",
       "What happened before it was rejected?"]


def login():
    r = requests.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    r.raise_for_status()
    return r.json()["data"]["access_token"]


def new_session(tok):
    r = requests.post(f"{BASE}/chat/sessions", headers={"Authorization": f"Bearer {tok}"}, json={}, timeout=30)
    r.raise_for_status()
    return r.json()["data"]["session_id"]


def ask(tok, sid, msg, hist, stream=False):
    payload = {"session_id": sid, "message": msg}
    if hist is not None:
        payload["chat_history"] = hist
    t0 = time.time()
    h = {"Authorization": f"Bearer {tok}"}
    if not stream:
        r = requests.post(f"{BASE}/chat", headers=h, json=payload, timeout=200)
        r.raise_for_status()
        d = r.json()["data"]
        return d.get("response") or "", d.get("intent"), round(time.time() - t0, 2)
    r = requests.post(f"{BASE}/chat/stream", headers=h, json=payload, timeout=200, stream=True)
    r.raise_for_status()
    parts = []
    for line in r.iter_lines(decode_unicode=True):
        if line and line.startswith("data: "):
            try:
                parts.append(json.loads(line[6:]).get("content") or "")
            except (ValueError, AttributeError):
                pass
    return "".join(parts), "(stream)", round(time.time() - t0, 2)


def conv(tok, label, turns, mode, stream_from=None):
    print(f"\n=== {label} (history={mode}) ===")
    sid, hist, log = new_session(tok), [], []
    for i, q in enumerate(turns, 1):
        sent = None if mode == "none" else (STALE if mode == "stale" else list(hist))
        a, intent, dt = ask(tok, sid, q, sent, stream=bool(stream_from and i >= stream_from))
        print(f"Q{i}: {q}\nA ({dt}s, {intent}): {a[:200]}")
        log.append({"q": q, "a": a, "intent": intent, "seconds": dt, "history_mode": mode,
                    "client_history_len": 0 if sent is None else len(sent),
                    "endpoint": "stream" if (stream_from and i >= stream_from) else "chat"})
        hist += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    return log


def main():
    tok = login()
    res = {
        "normal": conv(tok, "Q1-Q9 normal history", SEQ, "normal"),
        "none": conv(tok, "Q1-Q9 no history", SEQ, "none"),
        "stale": conv(tok, "Q1-Q9 fixed stale history", SEQ, "stale"),
        "explicit": conv(tok, "Explicit ID under focus A + stale", SEQ[:5] + [f"Show the status of {S1}."], "stale"),
        "stream_stale": conv(tok, "Q1-Q9 stale, Q6-Q9 via /chat/stream", SEQ, "stale", stream_from=6),
        "fresh": conv(tok, "Fresh session + client history only", ["What is its status?"], "stale"),
    }
    with open("test_reports/qbank_artifacts/p3_3_entity_resolution_results.json", "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    print("\nwrote test_reports/qbank_artifacts/p3_3_entity_resolution_results.json")


if __name__ == "__main__":
    main()
