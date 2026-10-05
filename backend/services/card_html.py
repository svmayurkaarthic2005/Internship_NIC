"""Plain-text record answers ("2026/0158/02/000002: Service 0158 ...", "• Door no: not recorded") as the same card the
ISD / NISD details use (title, FIELD | DETAILS table, Copy button), so every answer looks alike. A one-line answer
becomes the same intro sentence the other answers open with. The text is escaped; nothing is invented."""
from __future__ import annotations

import html
import re

_LABEL = re.compile(r"^(.{1,70}?)\s*:\s+(.+)$")
_STEP = re.compile(r"^(\d{1,2})[.)]\s+(.+)$")


def _e(t: str) -> str:
    t = html.escape(t)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)


def is_html(text: str) -> bool:
    return bool(re.search(r"<(?:table|div|p|ul|ol|span|strong|br)\b", text or "", re.IGNORECASE))


def card(text: str, ta: bool = False) -> str:
    text = (text or "").strip("\n")
    if not text or is_html(text):
        return text
    lines = [l for l in text.split("\n") if l.strip()]
    if len(lines) == 1:
        return f"<div class='table-intro'>{_e(lines[0].strip())}</div>"
    title, rows, notes = None, [], []
    for raw in lines:
        s = raw.strip()
        bullet = s[:1] in ("•", "-", "–") and not s.startswith("–  ")
        body = s.lstrip("•-– ").strip() if bullet else s
        step = _STEP.match(body)
        m = _LABEL.match(body)
        if title is None and not raw.startswith(" ") and not bullet:
            title = body.rstrip(":")
            if m and not body.endswith(":"):      # "2026/...: Service 0158 - Modification": all of it is the title
                title = body
            continue
        if body.endswith(":") and not bullet:
            rows.append(("section", body.rstrip(":"), ""))
        elif step:
            rows.append(("row", step.group(1), step.group(2)))
        elif m and not re.match(r"^(?:Note|\()", body):
            rows.append(("row", m.group(1), m.group(2)))
        else:
            rows.append(("note", body, ""))
    if not rows:
        return f"<div class='table-intro'>{_e(title or text)}</div>"
    trs = []
    for kind, a, b in rows:
        if kind == "section":
            trs.append(f"<tr><td colspan='2'><strong>{_e(a)}</strong></td></tr>")
        elif kind == "note":
            trs.append(f"<tr><td colspan='2'>{_e(a)}</td></tr>")
        else:
            trs.append(f"<tr><td>{_e(a)}</td><td>{_e(b)}</td></tr>")
    n = sum(1 for k, _, _ in rows if k == "row")
    head = ("புலம்", "விவரம்") if ta else ("FIELD", "DETAILS")
    count = f"<span class='data-table-count'>{n} {'பதிவுகள்' if ta else 'records'}</span>" if n else ""
    return (
        "<div class='data-table-card'><div class='data-table-header'><div class='data-table-title-section'>"
        f"<span class='data-table-icon'>📋</span><h3 class='data-table-title'>{_e(title or '')}</h3>{count}</div>"
        "<button class='btn-copy-table' onclick='window.copyTableData(this)'>Copy</button></div>"
        f"<div class='data-table-container'><table class='data-table'><thead><tr><th>{head[0]}</th><th>{head[1]}</th></tr></thead>"
        f"<tbody class='table-body-rows'>{''.join(trs)}</tbody></table></div></div>"
    )
