"""Calculation questions over the register -- every service code, none favoured:
"pending plus approved", "approved minus rejected", "how many more approved than rejected", "ratio of CSC to SRO",
"what percentage of 0158 applications are approved", "which service code has the most applications", "top 3 wards",
"average applications per day", and plain arithmetic ("47 plus 17", "78 / 152 * 100").

The figures come from the register rows (`get_all_service_code_applications`, rejected files included): ISD / NISD are
two service codes among the others (0153 / 0154), not the whole of it. "Pending" is the register's open status
(it already holds the files in progress). Nothing is estimated; every operand is shown beside the result. No LLM."""
from __future__ import annotations

import ast
import operator
import re
from collections import Counter
from datetime import date
from math import gcd
from typing import Any, Dict, List, Optional, Tuple

from backend.services import neg_scope

_APP_NO = re.compile(r"\d{4}/\d{3,4}/\d{1,3}/\d+")
_STATUS_WORDS = (("pending", ("pending", "pnding", "pendng", "open", "outstanding", "நிலுவை", "niluvai")),
                 ("approved", ("approved", "approvd", "accepted", "completed", "அங்கீகரி", "ஒப்புதல்")),
                 ("rejected", ("rejected", "rejectd", "refused", "நிராகரி")))
_CHANNELS = (("CSC", r"\bcsc\b|\bsevai\b"), ("sub_registrar", r"\bsro\b|sub[\s-]?registrar|சார்[\s-]?பதிவாளர்"),
             ("citizen", r"\bcitizen\b|\bportal\b"))
_CODE_WORDS = (("0178", r"\bf[\s-]?line\b"), ("0156", r"\btslr\b"), ("0153", r"\bnisd\b"), ("0154", r"\bisd\b"))
_SUM = re.compile(r"\b(?:plus|combined|together|altogether|sum|add(?:ed)?|total\s+of)\b|\+|கூட்டு", re.IGNORECASE)
_DIFF = re.compile(r"\b(?:minus|subtract\w*|difference|gap|diff)\b|\s-\s|வித்தியாசம்", re.IGNORECASE)
_MORE = re.compile(r"\bhow\s+many\s+(more|fewer|less)\s+(.+?)\s+than\s+(.+?)[?.!]*$", re.IGNORECASE)
_RATIO = re.compile(r"\bratio\b|\bproportion\s+of\b|\s:\s", re.IGNORECASE)
_PCT = re.compile(r"\b(?:percent(?:age)?|pct)\b|%|சதவீத", re.IGNORECASE)
_RANK = re.compile(r"\b(?:which|what)\s+(service\s+code|code|ward|channel|type|status|month|block)s?\b.{0,30}?\b(most|least|highest|lowest|"
                   r"maximum|minimum|fewest|busiest|max|min)\b|\btop\s+(\d+)\s+(service\s+codes?|codes?|wards?|channels?|blocks?)\b",
                   re.IGNORECASE)
_AVG_DAY = re.compile(r"\b(?:average|avg|mean)\b.{0,40}\bper\s+(day|week)\b|\bper\s+(day|week)\b.{0,20}\b(?:average|avg)\b|\bdaily\s+average\b",
                      re.IGNORECASE)
_ARITH_WORDS = (("divided by", "/"), ("multiplied by", "*"), ("times", "*"), ("minus", "-"), ("plus", "+"), ("into", "*"),
                ("over", "/"), ("x", "*"), ("×", "*"), ("÷", "/"))
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.USub: operator.neg}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("not arithmetic")


def arithmetic(text: str) -> Optional[str]:
    """"47 plus 17", "78/152*100", "20 percent of 150": a sum over numbers only. None when words other than operators remain."""
    t = (text or "").lower().replace(",", "").strip(" ?=.")
    t = re.sub(r"^(?:what\s+is|what's|whats|calculate|compute|find|tell\s+me|how\s+much\s+is|solve)\s+", "", t)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:%|percent|percentage)\s+of\s+(\d+(?:\.\d+)?)", t)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        return f"{m.group(1)}% of {m.group(2)} = {_fmt(a * b / 100)}"
    for w, sym in _ARITH_WORDS:
        t = re.sub(rf"(?<=[\d\s]){re.escape(w)}(?=[\d\s])" if w == "x" else rf"\b{re.escape(w)}\b" if w.isalpha() else re.escape(w), f" {sym} ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not re.fullmatch(r"[\d\s.+\-*/()]+", t) or not re.search(r"\d\s*[+\-*/]\s*[\d(]", t):
        return None
    try:
        val = _eval(ast.parse(t, mode="eval").body)
    except (ValueError, SyntaxError, ZeroDivisionError):
        return "That division is by zero." if "/ 0" in t else None
    return f"{t} = {_fmt(val)}"


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.2f}".rstrip("0").rstrip(".")


_WEEKS = re.compile(r"\bthis\s+week\b.{0,40}\blast\s+week\b|\blast\s+week\b.{0,40}\bthis\s+week\b", re.IGNORECASE)
_CMP = re.compile(r"\b(?:compare|compared|comparison|vs\.?|versus|than|difference|against)\b|ஒப்பிட", re.IGNORECASE)

_NOUN = re.compile(r"\b(?:applications?|aplications?|apps?|files|cases)\b|விண்ணப்ப", re.IGNORECASE)


def looks_like_calc(text: str) -> bool:
    """A cheap gate: only the shapes below are this module's (a bare "how many are approved" stays a follow-up)."""
    t = text or ""
    return bool((_WEEKS.search(t) and _CMP.search(t)) or _MORE.search(t) or _RATIO.search(t) or _RANK.search(t) or _AVG_DAY.search(t) or _SUM.search(t)
                or (_DIFF.search(t) and len(_spec(t)) >= 1) or (_PCT.search(t) and (_NOUN.search(t) or re.search(r"(?i)\bas\s+a?\s*(?:percent|%)", t))))


# ── operands ─────────────────────────────────────────────────────────────────────────────────────────
def _spec(text: str) -> Dict[str, set]:
    low = text.lower()
    spec: Dict[str, set] = {}
    for val, words in _STATUS_WORDS:
        if any(w in low for w in words):
            spec.setdefault("status", set()).add(val)
    if re.search(r"\bin[\s_-]?progress\b", low):
        spec.setdefault("status", set()).add("pending")
    for val, rx in _CHANNELS:
        if re.search(rx, low, re.IGNORECASE):
            spec.setdefault("channel", set()).add(val)
    for code in re.findall(r"\b(01\d\d)\b", text):
        spec.setdefault("code", set()).add(code)
    for val, rx in _CODE_WORDS:
        if re.search(rx, low):
            spec.setdefault("code", set()).add(val)
    for w in re.findall(r"\bward\s*(?:no\.?\s*)?(\d{1,3})\b", low):
        spec.setdefault("ward", set()).add(w.zfill(3))
    return spec


def _has(r: dict, spec: Dict[str, set]) -> bool:
    got = {"status": str(r.get("status") or "").lower(), "channel": str(r.get("submission_channel") or ""),
           "code": str(r.get("service_code") or ""), "ward": str(r.get("ward_number") or "").zfill(3)}
    return all(got[k] in v for k, v in spec.items())


def _label(spec: Dict[str, set]) -> str:
    from backend.utils.helpers import SIS_URBAN_SERVICES
    out = []
    for k in ("status", "channel", "code", "ward"):
        for v in sorted(spec.get(k, ())):
            if k == "code":
                nm = (SIS_URBAN_SERVICES.get(v, {}) or {}).get("name")
                out.append(f"{v} ({nm})" if nm else v)
            elif k == "ward":
                out.append(f"ward {v}")
            else:
                out.append({"sub_registrar": "Sub-Registrar"}.get(v, v))
    return " ".join(out) or "all"


def _count(rows, spec) -> int:
    return sum(1 for r in rows if _has(r, spec))


def _sides(text: str, splitter: re.Pattern) -> List[Dict[str, set]]:
    parts = [p for p in re.split(splitter, text) if p and p.strip()]
    specs = [_spec(p) for p in parts]
    return [s for s in specs if s]


def _shared(text: str) -> Dict[str, set]:
    """Scope the whole sentence carries ("... in ward 001"): applied to every operand."""
    s = _spec(text)
    return {k: v for k, v in s.items() if k == "ward"}


# ── the answer ───────────────────────────────────────────────────────────────────────────────────────
def answer(message: str, rows: List[dict]) -> Optional[str]:
    text = (message or "").strip()
    if not text or len(text.split()) > 30 or _APP_NO.search(text):
        return None
    a = arithmetic(text)
    if a:
        return a
    low = text.lower()
    if not rows or (neg_scope.parse(text)[0] and not re.search(r"\bminus\b", low)):
        return None
    shared = _shared(text)
    base = [r for r in rows if _has(r, shared)] if shared else list(rows)
    where = f" in ward {next(iter(shared['ward']))}" if shared.get("ward") else ""

    if _WEEKS.search(text) and _CMP.search(text):
        from datetime import timedelta
        mon = date.today() - timedelta(days=date.today().weekday())
        weeks = ((mon, mon + timedelta(days=6), "this week"), (mon - timedelta(days=7), mon - timedelta(days=1), "last week"))
        counts = []
        for d0, d1, _lab in weeks:
            counts.append(sum(1 for r in base if str(r.get("submission_date") or "")[:10]
                              and d0.isoformat() <= str(r["submission_date"])[:10] <= d1.isoformat()))
        diff = counts[0] - counts[1]
        verdict = "the same" if diff == 0 else f"{abs(diff)} {'more' if diff > 0 else 'fewer'} than last week"
        return (f"Applications filed this week ({weeks[0][0]} to {weeks[0][1]}): {counts[0]}; last week ({weeks[1][0]} to "
                f"{weeks[1][1]}): {counts[1]} -- this week is {verdict}{where}. (Your open workload now is on the desk summary: ask \"my workload\".)")
    m = _MORE.search(text)
    if m:
        la, lb = _spec(m.group(2)), _spec(m.group(3))
        if la and lb:
            na, nb = _count(base, la), _count(base, lb)
            word = m.group(1).lower()
            diff = na - nb
            return (f"{_label(la)}: {na}, {_label(lb)}: {nb} — {abs(diff)} {'more' if diff >= 0 else 'fewer'} {_label(la)} than "
                    f"{_label(lb)}{where}." if word == "more" or diff >= 0 else
                    f"{_label(la)}: {na}, {_label(lb)}: {nb} — {_label(la)} is {abs(diff)} fewer than {_label(lb)}{where}.")
    if _RATIO.search(text) and not _PCT.search(text):
        sp = _sides(re.sub(r"(?i)^.*?\bratio\s+of\s+", "", text), re.compile(r"\bto\b|\band\b|\bby\b|\s:\s|:"))
        if len(sp) >= 2:
            ns = [_count(base, s) for s in sp[:3]]
            g = 0
            for n in ns:
                g = gcd(g, n)
            red = [n // g for n in ns] if g else ns
            return (" : ".join(_label(s) for s in sp[:3]) + f" = {' : '.join(map(str, ns))}"
                    + (f" (simplest form {' : '.join(map(str, red))})" if g > 1 else "") + f"{where}.")
    if _PCT.search(text) and not _RANK.search(text):
        m2 = re.search(r"(?i)(.+?)\bas\s+a?\s*(?:percent\w*|%)\s+of\s+(.+)", text)
        if m2:
            sn, sd = _spec(m2.group(1)), {k: v for k, v in _spec(m2.group(2)).items()}
        else:
            parts = re.split(r"(?i)\b(?:that\s+|which\s+)?(?:are|is|were|was)\b", text, maxsplit=1)
            if len(parts) == 2 and _spec(parts[1]):
                sn, sd = _spec(parts[1]), {k: v for k, v in _spec(parts[0]).items() if k != "status"}
            else:
                sn, sd = _spec(text), {}
        if sn:
            den = _count(base, sd) if sd else len(base)
            num = _count([r for r in base if _has(r, sd)] if sd else base, sn)
            of = _label(sd) if sd else "all"
            return f"{num} of {den} {of + ' ' if sd else ''}applications{where} ({num * 100 / den:.1f}%) are {_label(sn)}." if den else \
                f"There are no {of} applications{where}."
    if _SUM.search(text) or re.search(r"\btotal\s+(?:number\s+)?(?:of\s+)?(?:\w+\s+){0,2}(?:and|plus)\b", low):
        sp = _sides(text, re.compile(r"\bplus\b|\+|\band\b|,|\bwith\b|\bor\b", re.IGNORECASE))
        if len(sp) >= 2:
            ns = [_count(base, s) for s in sp]
            if len({tuple(sorted((k, tuple(sorted(v))) for k, v in s.items())) for s in sp}) == len(sp):
                return " + ".join(f"{_label(s)} {n}" for s, n in zip(sp, ns)) + f" = {sum(ns)}{where}."
    if _SUM.search(text) and not re.search(r"\bplus\b|\+", low):
        one = _spec(text)
        if one:
            return f"{_label({k: v for k, v in one.items() if k != 'ward'})}: {_count(base, one)}{where}."
    if _DIFF.search(text) and not re.search(r"\bminus\b|\bhow\s+many\b|\bcount\b|\bnumber\s+of\b|\btotal\b|\bapplications\b|\bfiles\b", low) \
            and not any(k in _spec(text) for k in ("status", "channel", "ward")):
        return None          # "difference between ISD and NISD" is a definition question (service_code_guide), not a subtraction
    if _DIFF.search(text):
        sp = _sides(text, re.compile(r"\bminus\b|\s-\s|\band\b|\bbetween\b|\bfrom\b|\bvs\.?\b|\bversus\b", re.IGNORECASE))
        if len(sp) == 2:
            na, nb = _count(base, sp[0]), _count(base, sp[1])
            return f"{_label(sp[0])} {na} − {_label(sp[1])} {nb} = {'−' if na < nb else ''}{abs(na - nb)}{where}."

    rk = _RANK.search(text)
    if rk:
        kind = (rk.group(1) or rk.group(4) or "").lower().replace("service ", "").rstrip("s")
        top = int(rk.group(3)) if rk.group(3) else 1
        key = {"code": lambda r: str(r.get("service_code") or ""), "ward": lambda r: str(r.get("ward_number") or ""),
               "channel": lambda r: str(r.get("submission_channel") or ""), "status": lambda r: str(r.get("status") or ""),
               "type": lambda r: str(r.get("service_code") or ""), "block": lambda r: str(r.get("block_number") or ""),
               "month": lambda r: str(r.get("submission_date") or "")[:7]}.get(kind)
        if key:
            spec = {k: v for k, v in _spec(text).items() if not (k == "code" and kind in ("code", "type"))}
            pool = [r for r in rows if _has(r, spec)]
            cnt = Counter(key(r) for r in pool if key(r))
            if cnt:
                least = bool(re.search(r"least|lowest|minimum|fewest|\bmin\b", rk.group(0), re.IGNORECASE))
                items = sorted(cnt.items(), key=lambda kv: (kv[1] if least else -kv[1], kv[0]))[:max(top, 1) if not rk.group(3) else top]
                from backend.utils.helpers import SIS_URBAN_SERVICES

                def name(k):
                    if kind in ("code", "type"):
                        nm = (SIS_URBAN_SERVICES.get(k, {}) or {}).get("name")
                        return f"{k} ({nm})" if nm else k
                    if kind == "month" and len(k) == 7:
                        import calendar
                        return f"{calendar.month_name[int(k[5:7])]} {k[:4]}"
                    return f"ward {k}" if kind == "ward" else k
                if not rk.group(3):
                    best = items[0][1]
                    tied = [k for k, v in cnt.items() if v == best]
                    return (f"{', '.join(name(k) for k in sorted(tied))} — {best} application(s), the {'fewest' if least else 'most'} "
                            f"of {len(cnt)} {kind}s ({len(pool)} in all).")
                return f"Top {len(items)} {kind}s: " + ", ".join(f"{name(k)} {v}" for k, v in items) + f" ({len(pool)} in all)."

    ad = _AVG_DAY.search(text)
    if ad:
        days = sorted(d for d in (str(r.get("submission_date") or "")[:10] for r in base) if d)
        if days:
            d0, d1 = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
            span = max((d1 - d0).days + 1, 1)
            unit = (ad.group(1) or ad.group(2) or "day").lower()
            n = len(base)
            per = n / (span if unit == "day" else span / 7)
            return (f"Average applications per {unit}: {per:.2f} ({n} applications over {span} days, {d0} to {d1}, "
                    f"quiet days counted as 0){where}.")
    return None
