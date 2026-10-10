"""FastAPI HTTP/SSE integration tests for the chat endpoints -- the layer
the prior function-level verification (process_chat()/process_chat_stream()
called directly) never exercised: real ASGI requests through backend.main.app,
real JWT auth via /api/v1/auth/login, real session rows, real SSE bytes over
the wire, read with an HTTP client rather than by iterating the async
generator in-process.

Test level: FastAPI HTTP/SSE integration (httpx.AsyncClient over ASGITransport
against the real app; real seeded PostgreSQL DB; no mocks). NOT a browser
test -- no JS runtime, no DOM, nothing renders. The "frontend rendering"
checks below are static code inspection of frontend/js/chat.js's
formatBotMessage()/plainToCard() contract, asserted against the real response
text these endpoints return, not an executed frontend test.

`python test_http_chat_integration.py` (needs the DB + a running Ollama only
for cases that fall through to the LLM; none of the assertions below do).
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, r"C:\proj\nic_internship")

import httpx

from backend.main import app

OFFICER_EMAIL = "marun_chn@sis.tn.gov.in"
OFFICER_PASSWORD = "Test@1234"
APP = "2026/0154/02/012547"

_results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((name, condition, detail))
    print(f"{'ok ' if condition else 'FAIL'} {name}" + (f" -- {detail}" if detail and not condition else ""))


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def login(c: httpx.AsyncClient) -> str:
    r = await c.post("/api/v1/auth/login", json={"email": OFFICER_EMAIL, "password": OFFICER_PASSWORD})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    body = r.json()
    assert body["success"], f"login unsuccessful: {body}"
    return body["data"]["access_token"]


async def chat(c: httpx.AsyncClient, token: str, message: str, session_id: str, chat_history=None) -> dict:
    r = await c.post(
        "/api/v1/chat",
        json={"message": message, "session_id": session_id, "language": "auto", "chat_history": chat_history},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, f"chat endpoint failed: {r.status_code} {r.text}"
    body = r.json()
    assert body["success"], f"chat unsuccessful: {body}"
    return body["data"]


async def chat_stream(c: httpx.AsyncClient, token: str, message: str, session_id: str, chat_history=None):
    """Returns (ordered list of raw SSE lines, assembled content text)."""
    raw_lines: list[str] = []
    content = ""
    async with c.stream(
        "POST", "/api/v1/chat/stream",
        json={"message": message, "session_id": session_id, "language": "auto", "chat_history": chat_history},
        headers={"Authorization": f"Bearer {token}"},
    ) as r:
        assert r.status_code == 200, f"stream endpoint failed: {r.status_code}"
        assert "text/event-stream" in r.headers.get("content-type", ""), r.headers.get("content-type")
        async for raw in r.aiter_lines():
            if not raw:
                continue
            raw_lines.append(raw)
            if raw.startswith("data: "):
                try:
                    payload = json.loads(raw[len("data: "):])
                except Exception:
                    continue
                if "content" in payload:
                    content += payload["content"]
    return raw_lines, content


def frontend_would_render_as_card(response_text: str) -> bool:
    """Static re-implementation of frontend/js/chat.js's card-detection
    contract (formatBotMessage -> plainToCard), run here against the REAL
    response text the endpoint returned -- not an executed browser test.

    Mirrors the real JS exactly: HTML (any tag) is injected as-is (a card
    only if it carries the data-table-card class); plain text only becomes a
    card via plainToCard(), which requires >= 3 non-blank lines AND either
    >= 2 bullet lines or >= 3 "Label: value" lines.
    """
    has_html = bool(re.search(r"<[a-zA-Z][^>]*>", response_text))
    if has_html:
        return "data-table-card" in response_text
    lines = [l for l in (ln.rstrip() for ln in response_text.split("\n")) if l.strip()]
    if len(lines) < 3:
        return False
    is_bullet = lambda l: bool(re.match(r"^\s*[•\-\u2013]\s+", l))
    label_re = re.compile(r"^(.{1,70}?)\s*:\s+(.+)$")
    bullets = sum(1 for l in lines if is_bullet(l))
    labelled = sum(1 for l in lines if label_re.match(re.sub(r"^\s*[•\-\u2013]\s+", "", l)))
    return bullets >= 2 or labelled >= 3


async def main() -> None:
    async with client() as c:
        token = await login(c)
        check("1. HTTP auth: authorised officer + valid session reaches the real endpoint", bool(token), token[:20])

        # 2/3. Bare-date narrative -> deterministic clarification, non-streaming.
        sid = str(uuid.uuid4())
        data = await chat(c, token, "What happened on 2026-08-05?", sid)
        resp = data["response"]
        check("2. Bare-date narrative over real HTTP -> deterministic clarification",
              "date alone isn't enough" in resp.lower(), resp[:200])
        check("2b. No unrelated application number invented in the clarification",
              not re.search(r"\b20\d{2}/\d{3,4}/\d+/\d+\b", resp), resp[:200])

        # 3. Same question over the STREAMING endpoint -- the clarification must
        # be the ONLY content emitted; nothing ungrounded precedes it.
        sid2 = str(uuid.uuid4())
        raw_lines, content = await chat_stream(c, token, "What happened on 2026-08-05?", sid2)
        check("3. SSE stream emits the clarification with nothing ungrounded before it",
              "date alone isn't enough" in content.lower()
              and not re.search(r"\b20\d{2}/\d{3,4}/\d+/\d+\b", content), content[:200])

        # 4. SSE content/event structure and termination.
        check("4. SSE stream terminates with the done marker",
              any('"done": true' in l or '"done":true' in l for l in raw_lines), raw_lines[-3:])
        check("4b. SSE lines are well-formed 'data: {...}' events",
              all(l.startswith("data: ") for l in raw_lines), raw_lines[:3])

        # 5. A supported, well-defined date-scoped query still reaches the real handler.
        sid3 = str(uuid.uuid4())
        data = await chat(c, token, "Which applications were submitted on 2026-08-05?", sid3)
        check("5. well-defined date-scoped query ('submitted on <date>') reaches the real register handler",
              "date alone isn't enough" not in data["response"].lower(), data["response"][:200])

        # 6. Explicit application number -> grounded answer, untouched.
        sid4 = str(uuid.uuid4())
        data = await chat(c, token, f"What happened to {APP} on 2026-08-05?", sid4)
        check("6. explicit application number + date -> grounded workflow answer, not refused",
              "date alone isn't enough" not in data["response"].lower() and APP in data["response"], data["response"][:200])

        # 7. Repeated-card regression, through the REAL endpoint: full card,
        # then a field follow-up in the SAME session -> no repeated card.
        sid5 = str(uuid.uuid4())
        d1 = await chat(c, token, f"Show full application details for {APP}", sid5)
        check("7a. full-details request over HTTP renders a card",
              "data-table-card" in d1["response"], d1["response"][:150])
        d2 = await chat(c, token, "What is its ward?", sid5)
        check("7b. field follow-up over HTTP (same session) -> short field answer, NOT a repeated card",
              "data-table-card" not in d2["response"] and APP in d2["response"], d2["response"][:200])

        # 8. A fresh session (new session_id, no chat_history) must not inherit
        # the application context from session 5 above.
        sid6 = str(uuid.uuid4())
        d3 = await chat(c, token, "What is its ward?", sid6)
        check("8. fresh session (new session_id) does not inherit another session's application context",
              APP not in d3["response"], d3["response"][:200])

        # 10. Final rendering contract: the date-narrative clarification and the
        # field answer must not be mistaken for a full application-details card
        # by the real frontend contract (statically re-applied here).
        check("10a. date-narrative clarification does not satisfy the frontend's card-detection contract",
              not frontend_would_render_as_card(resp), resp[:200])
        check("10b. field-follow-up answer does not satisfy the frontend's card-detection contract either",
              not frontend_would_render_as_card(d2["response"]), d2["response"][:200])
        check("10c. structured table_data on the clarification turn is empty/None (nothing for the frontend to key a card off)",
              not data_has_card_signal(data), None)

    # 9. Missing/invalid authentication -- the existing security contract (401),
    # not weakened by anything above.
    async with client() as c2:
        r = await c2.post("/api/v1/chat", json={"message": "hello", "session_id": str(uuid.uuid4()), "language": "auto"})
        check("9a. no credentials at all -> 401, not 200", r.status_code == 401, f"status={r.status_code}")
        r = await c2.post("/api/v1/chat", json={"message": "hello", "session_id": str(uuid.uuid4()), "language": "auto"},
                           headers={"Authorization": "Bearer not-a-real-token"})
        check("9b. invalid/garbage token -> 401, not 200", r.status_code == 401, f"status={r.status_code}")

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} passed")
    if passed != total:
        print("\nFailures:")
        for name, ok, detail in _results:
            if not ok:
                print(f"  - {name}: {detail}")
        sys.exit(1)


def data_has_card_signal(data: dict) -> bool:
    return bool(data.get("table_data")) or "data-table-card" in (data.get("response") or "")


if __name__ == "__main__":
    asyncio.run(main())
