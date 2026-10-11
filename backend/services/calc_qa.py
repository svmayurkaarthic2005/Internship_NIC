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
                 ("rejected", ("rejected", "rejectd", "rejection", "refused", "நிராகரி")))
_CHANNELS = (("CSC", r"\bcsc\b|\bsevai\b"), ("sub_registrar", r"\bsro\b|sub[\s-]?registrar|சார்[\s-]?பதிவாளர்"),
             ("citizen", r"\bcitizen\b|\bportal\b"))
_CODE_WORDS = (("0178", r"\bf[\s-]?line\b"), ("0156", r"\btslr\b"), ("0153", r"\bnisd\b"), ("0154", r"\bisd\b"))
_SUM = re.compile(r"\b(?:plus|combined|together|altogether|sum|add(?:ed)?|total\s+of)\b|\+|கூட்டு", re.IGNORECASE)
_DIFF = re.compile(r"\b(?:minus|subtract\w*|difference|gap|diff)\b|\s-\s|வித்தியாசம்", re.IGNORECASE)
_MORE = re.compile(r"\bhow\s+many\s+(more|fewer|less)\s+(.+?)\s+than\s+(.+?)[?.!]*$", re.IGNORECASE)
_RATIO = re.compile(r"\bratio\b|\bproportion\s+of\b|\s:\s", re.IGNORECASE)
_PCT = re.compile(r"\b(?:percent(?:age)?|pct)\b|%|சதவீத", re.IGNORECASE)
_RANK = re.compile(r"\b(?:which|what)\s+(service\s+code|service|code|ward|channel|type|status|month|block)s?\b.{0,30}?\b(most|least|highest|lowest|"
                   r"maximum|minimum|fewest|busiest|max|min|more|fewer|less|higher|lower|smallest|largest|biggest|greatest|smallst)\b|"
                   r"\btop\s+(\d+)\s+(service\s+codes?|codes?|wards?|channels?|blocks?)\b",
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
                or (_DIFF.search(t) and len(_spec(t)) >= 1) or (_PCT.search(t) and (_NOUN.search(t) or re.search(r"(?i)\bas\s+a?\s*(?:percent|%)", t)))
                or (_DATE_EXTREME.search(t) and not _DATE_EXTREME_EXCLUDE.search(t)))


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
        if kind == "service":
            kind = "code"  # "which SERVICE has..." names a service code, same as bare "code"
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
                least = bool(re.search(r"least|lowest|minimum|fewest|\bmin\b|\bfewer\b|\bless\b|\blower\b|smallest|smallst", rk.group(0), re.IGNORECASE))
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


# ── register counts by service code: total / rejected / not rejected / outside ──────────────────────────
# "how many 0178 files are there", "total number of 0158 files including rejected", "how many non-rejected 0156
# files", "rejected percentage for 0178", "how many files are outside 0154", "total register size". These fell to
# the ISD-only listing (the 0178 in "rejected 0178 files" was dropped and the ISD count given), to the previous
# list on screen ("9 of those 9 are rejected"), or to the LLM. Every figure is counted from the register rows.
_CC_COUNT = re.compile(r"\bhow\s+many\b|\btot(?:al|l)\b|\bnumbe?r?\s+of\b|\bcounts?\b|\bsize\b|\bpercent\w*|\bpercntage\b|"
                       r"\bethana\b|\bevalo\b|\bevlo\b|எத்தனை|எவ்வளவு|டோட்டல்", re.IGNORECASE)
_CC_NOUN = re.compile(r"\bfiles?\b|\bregister\b|\bregiste\b|\bappl?i?c?a?tions?\b|\bapplcations?\b|\baplications?\b|"
                      r"அப்ளிகேஷன்|அப்ளிகேசன்|விண்ணப்ப", re.IGNORECASE)
_CC_SCOPE = re.compile(r"\bward\b|\bwarrd\b|\bblock\b|\bblook\b|\bsurvey\b|\bcsc\b|\bcitizen\b|\bsro\b|sub[\s-]?regist|"
                       r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\b20\d\d\b|\bthis\s+(?:month|week|year)\b|"
                       r"\blast\s+(?:month|week|year)\b|\btoday\b|\bpending\b|\bapprov\w*|\baprov\w*|\bmore\b|\bthan\b|\bmost\b|"
                       r"\bwhich\b|\bcompare\b|\bvs\b|\band\s+0?1\d\d\b|\bactiv\w*|\bopen\b|\bchannel\b", re.IGNORECASE)
# "how many 0158 files came through EACH channel" -- a per-channel breakdown, not the
# total/rejected/non-rejected count code_counts() above answers. "channel" in _CC_SCOPE makes
# code_counts() decline (so it no longer silently substitutes a status breakdown for the channel
# breakdown that was actually asked for); this answers the channel-scoped question instead.
_CHANNEL_BREAKDOWN_RE = re.compile(r"\b(?:each|every|by|per)\s+channel\b|\bchannel\s+(?:breakdown|wise|split)\b|"
                                   r"\bchannels?\s+(?:did|does)\s+.{0,20}\bcome\s+(?:through|from)\b", re.IGNORECASE)


_BLOCKS_IN_WARD_RE = re.compile(r"\bwhich\s+blocks?\b.{0,20}\b(?:contain|have|has)\b|\bblocks?\s+(?:in|within)\s+ward\b.{0,20}\bcontain\b", re.IGNORECASE)


def blocks_in_ward(text: str, rows: List[dict], ta: bool = False) -> Optional[str]:
    """"which blocks in ward 001 contain applications?" -- the distinct blocks, with a count each.
    Previously unhandled: no cue named "block" as the thing being asked FOR (only as a filter), so
    this fell to the ward-scoped listing/count path, which answers how many applications are in
    the ward, never which blocks they are in."""
    t = text or ""
    if _APP_NO.search(t) or not _BLOCKS_IN_WARD_RE.search(t):
        return None
    spec = _spec(t)
    if "ward" not in spec:
        return None
    pool = [r for r in rows if _has(r, spec)]
    if not pool:
        return None
    counts = Counter(str(r.get("block_number")) for r in pool if r.get("block_number"))
    ward = next(iter(spec["ward"]))
    parts = ", ".join(f"{b} ({n})" for b, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    if ta:
        return f"வார்டு {ward}-ல் {len(counts)} பிளாக்குகளில் விண்ணப்பங்கள் உள்ளன: {parts}."
    return f"{len(counts)} block(s) in ward {ward} contain applications: {parts}."


def channel_breakdown(text: str, rows: List[dict], ta: bool = False) -> Optional[str]:
    t = text or ""
    if _APP_NO.search(t) or not _CHANNEL_BREAKDOWN_RE.search(t):
        return None
    code = _cc_code(t)
    pool = rows if not code else [r for r in rows if str(r.get("service_code")) == code]
    if not pool:
        return None
    from backend.utils.helpers import SIS_URBAN_SERVICES
    name = (SIS_URBAN_SERVICES.get(code, {}) or {}).get("name") if code else None
    what = f"{code} ({name})" if code else "your register"
    chan_label = {"CSC": "CSC", "citizen": "citizen portal", "sub_registrar": "Sub-Registrar"}
    counts = Counter(r.get("submission_channel") for r in pool if r.get("submission_channel"))
    parts = ", ".join(f"{chan_label.get(c, c)} {n}" for c, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    if ta:
        return f"{what}: {len(pool)} கோப்புகள் — {parts}."
    return f"{what}: {len(pool)} files by channel -- {parts}."
_CC_REJ = re.compile(r"\brej\w*|ரிஜெக்ட்|ரிஜக்ட்|நிராகரி", re.IGNORECASE)
_CC_NONREJ = re.compile(r"\bnon[\s-]?rej\w*|\bwithout\s+(?:the\s+)?rej\w*|\bexclud\w*\s+(?:the\s+)?rej\w*|\bnot\s+rej\w*|"
                        r"\bleft\s+after\s+exclud\w*", re.IGNORECASE)
_CC_INCL = re.compile(r"\bincl\w*\b|\bwith\s+(?:the\s+)?rej\w*|\balong\s+with\b", re.IGNORECASE)
_CC_OUT = re.compile(r"\bout\w*side\b|\boutide\b|\boutsid\b|\bnot\s+under\b|\bother\s+than\b|\bapart\s+from\b|\bexcept\b", re.IGNORECASE)


def _cc_code(text: str) -> Optional[str]:
    from backend.utils.helpers import SIS_URBAN_SERVICES
    for c in re.findall(r"(?<![\d/])(0?1[5-9]\d)(?![\d/])", text):
        c = c.zfill(4)
        if c in SIS_URBAN_SERVICES:
            return c
    for c, rx in _CODE_WORDS:
        if re.search(rx, text, re.IGNORECASE):
            return c
    return None


_CC_TOT = re.compile(r"\btot(?:al|l)\b|டோட்டல்|\bsize\b", re.IGNORECASE)
_CC_TAWORD = re.compile(r"\bethana\b|\bevalo\b|\bevlo\b|எத்தனை|எவ்வளவு|டோட்டல்", re.IGNORECASE)


_DATE_EXTREME = re.compile(
    r"\b(earliest|oldest|latest|newest|most\s+recent(?:ly)?)\b.{0,40}\b(submit\w*|submission|filed|application)\b|"
    r"\b(submit\w*|submission|filed|application)\b.{0,10}\b(earliest|oldest|latest|newest|most\s+recent(?:ly)?)\b",
    re.IGNORECASE)
# "longest to approve" / "took the longest" / "pending the longest" are decision-duration
# questions, answered by the existing compare_applications superlative over DECIDED files --
# a different thing from "which was submitted earliest/latest", which this function answers.
_DATE_EXTREME_EXCLUDE = re.compile(r"\bapprove\b|\bdecide\w*\b|\bpending\s+(?:the\s+)?longest\b|\btook\b|\btake\b", re.IGNORECASE)


def date_extreme(text: str, rows: List[dict], ta: bool = False) -> Optional[str]:
    """"which application in ward 001 was submitted most recently?" / "what was the earliest
    submission date for 0158?" -- a min/max over submission_date, scoped by whatever ward /
    block / service code the question names (via the same `_spec()` scoping the ranking and
    count functions use). Neither question is a duration or a ranking by count; both previously
    fell through to the ranking function above (which answers "which WARD has the most", not
    "which APPLICATION in a ward is newest") or to the bare service-code definition."""
    t = text or ""
    if _APP_NO.search(t) or not _DATE_EXTREME.search(t) or _DATE_EXTREME_EXCLUDE.search(t):
        return None
    latest = bool(re.search(r"latest|newest|most\s+recent", t, re.IGNORECASE))
    spec = _spec(t)
    pool = [r for r in rows if _has(r, spec) and r.get("submission_date")]
    if not pool:
        return None
    dates = sorted({str(r["submission_date"])[:10] for r in pool})
    target = dates[-1] if latest else dates[0]
    apps = sorted(r.get("application_number") for r in pool if str(r.get("submission_date"))[:10] == target)
    which = "most recently" if latest else "earliest"
    apps_str = ", ".join(apps[:10]) + (f" and {len(apps) - 10} more" if len(apps) > 10 else "")
    if ta:
        return f"{which} சமர்ப்பிக்கப்பட்டது: {target} — {apps_str}."
    return f"Submitted {which}: {target} -- {apps_str}."


_CODES_WITH_APPS_RE = re.compile(
    r"\b(?:which|what)\s+services?(?:\s*codes?)?\b.{0,25}\b"
    r"(?:have|has)\s+(?:any\s+|non[\s-]?zero\s+)?applications?\b"
    r"|\bservices?(?:\s*codes?)?\b.{0,20}\bare\s+in\s+use\b"
    r"|\b(?:which|what)\s+services?(?:\s*codes?)?\b.{0,20}\bin\s+use\b"
    r"|\b(?:which|what)\s+services?(?:\s*codes?)?\b.{0,25}\bhave\s+no\s+applications?\b"
    r"|\b(?:which|what)\s+services?(?:\s*codes?)?\b.{0,25}\b(?:no\s+one|nobody|unused|not\s+used)\b"
    r"|\bservices?(?:\s*codes?)?\b.{0,20}\b(?:non[\s-]?zero|a\s+non[\s-]?zero)\s+counts?\b",
    re.IGNORECASE)


def codes_with_applications(text: str, rows: List[dict], ta: bool = False) -> Optional[str]:
    """"which service codes have applications" / "...are in use" / "...have no
    applications" -- every CANONICAL code sorted into has-records / zero-records,
    computed from the register rows and `SIS_URBAN_SERVICES` together, never a
    hardcoded ISD/NISD-biased list. Used to fall through to `general_query` (the
    LLM), which invented "there are 2 service codes in your jurisdiction: 0153
    and 0154" -- a register that actually has 11 codes with live rows."""
    t = text or ""
    if not _CODES_WITH_APPS_RE.search(t):
        return None
    from backend.utils.helpers import SIS_URBAN_SERVICES
    wants_zero = bool(re.search(r"\bno\s+applications?\b|\bno\s+one\b|\bnobody\b|\bunused\b|\bnot\s+used\b", t, re.IGNORECASE))
    present = sorted({str(r.get("service_code")) for r in rows if r.get("service_code")})
    if wants_zero:
        codes = sorted(c for c in SIS_URBAN_SERVICES if c not in present)
    else:
        codes = present
    listing = ", ".join(f"{c} ({SIS_URBAN_SERVICES[c]['name']})" for c in codes if c in SIS_URBAN_SERVICES)
    if ta:
        verb = "விண்ணப்பங்கள் இல்லை" if wants_zero else "விண்ணப்பங்கள் உள்ளன"
        return f"{len(codes)} சேவை குறியீடுகளுக்கு {verb}: {listing}."
    verb = "have no applications in your jurisdiction" if wants_zero else "have applications in your jurisdiction"
    return f"{len(codes)} of {len(SIS_URBAN_SERVICES)} service codes {verb}: {listing}."


def code_counts(text: str, rows: List[dict], ta: bool = False) -> Optional[str]:
    """Total / rejected / not-rejected / rejected-percentage / outside counts of one service code, or of the
    whole register. None for anything scoped further (ward, month, channel, status) or compared."""
    t = text or ""
    if _APP_NO.search(t) or not _CC_COUNT.search(t) or _CC_SCOPE.search(t):
        return None
    code = _cc_code(t)
    files_noun = bool(re.search(r"\bfiles?\b|\bregiste?r?\b", t, re.IGNORECASE))
    out_q, nonrej, incl, tot = (bool(rx.search(t)) for rx in (_CC_OUT, _CC_NONREJ, _CC_INCL, _CC_TOT))
    pct = bool(_PCT.search(t) or re.search(r"\bpercntage\b", t, re.IGNORECASE))
    rej = bool(_CC_REJ.search(_CC_NONREJ.sub(" ", t)))
    semantic = out_q or nonrej or incl or tot or (pct and rej)
    if not code and not (files_noun and semantic):
        return None                       # "how many applications ..." with no code: the register listing answers
    if code and not (files_noun or semantic or _CC_TAWORD.search(t)):
        return None                       # "how many 0154 applications are rejected": the listing already answers
    from backend.utils.helpers import SIS_URBAN_SERVICES
    st = lambda r: str(r.get("status") or "").lower()
    pool = rows if not code else [r for r in rows if (str(r.get("service_code")) != code) == out_q]
    n = len(pool)
    r_ = sum(1 for r in pool if st(r) == "rejected")
    name = (SIS_URBAN_SERVICES.get(code, {}) or {}).get("name") if code else None
    what = (f"outside {code} ({name})" if out_q else f"{code} ({name})") if code else "in your register"
    if ta:
        what_ta = (f"{code} அல்லாத" if out_q else f"{code} ({name})") if code else "உங்கள் பதிவேட்டில்"
        if pct and rej:
            return f"{what_ta}: {n}-ல் {r_} நிராகரிக்கப்பட்டவை ({(100 * r_ / n if n else 0):.1f}%)."
        if nonrej and not tot:
            return f"{what_ta}: நிராகரிக்கப்படாதவை {n - r_} (மொத்தம் {n}, நிராகரிக்கப்பட்டவை {r_})."
        if rej and not (tot or incl):
            return f"{what_ta}: நிராகரிக்கப்பட்டவை {r_} (மொத்தம் {n})."
        return f"{what_ta}: மொத்தம் {n} (நிராகரிக்கப்பட்டவை உட்பட) — நிராகரிக்கப்பட்டவை {r_}, நிராகரிக்கப்படாதவை {n - r_}."
    lab = what[0].upper() + what[1:] if code else "Your register"
    if pct and rej:
        return f"{lab}: {r_} of {n} files are rejected ({(100 * r_ / n if n else 0):.1f}%)."
    if nonrej and not tot:
        return f"{lab}: {n - r_} not rejected (of {n} files in total; {r_} rejected)."
    if rej and not (tot or incl):
        return f"{lab}: {r_} rejected (of {n} files in total)."
    return f"{lab}: {n} files in total, including rejected — {r_} rejected, {n - r_} not rejected."





_CMP_CODES = re.compile(r"\bcompa?r\w*|\bcomapre\b|\bvs\.?\b|\bversus\b|\bsame\s+as\b|\bdifferen\w*|ஒப்பிடு", re.IGNORECASE)


_CMP_CONCEPTUAL = re.compile(r"\bhow\s+is\b.{0,20}\bdifferent\b|\bwhat\s+is\s+the\s+differen\w*\b|"
                             r"\bhow\s+(?:do|does)\b.{0,20}\bdiffer\b", re.IGNORECASE)
_CMP_COUNT_WORD = re.compile(r"\bhow\s+many\b|\bcount\b|\btotal\b|\bfiles?\b|\bapplications?\b|\bapps?\b|"
                             r"\brejected\b|\bapproved\b|\bpending\b|\bmore\b|\bless\b|\bfewer\b", re.IGNORECASE)


def compare_codes_conceptual(text: str, ta: bool = False) -> Optional[str]:
    """"how is 0164 different from 0161?" / "what is the difference between 0156 and 0157?" names no
    count word at all -- it asks what the two services ARE, not how many files of each the officer
    holds. compare_codes() below answers every "different"/"difference" phrasing with a file count,
    which is right for "compare 0164 and 0161" (an implicit count) but wrong here: the officer gets
    told "13 files vs 0 files" to a question that named no number and expected the register_only
    service-code answer. Checked first, before the file-count path, and declines to anything that
    is not unambiguously conceptual (a count word anywhere keeps the existing count behaviour)."""
    t = text or ""
    if _APP_NO.search(t) or not _CMP_CONCEPTUAL.search(t) or _CMP_COUNT_WORD.search(t):
        return None
    from backend.utils.helpers import SIS_URBAN_SERVICES
    codes = []
    for c in re.findall(r"(?<![\d/])(0?1[5-9]\d)(?![\d/])", t):
        c = c.zfill(4)
        if c in SIS_URBAN_SERVICES and c not in codes:
            codes.append(c)
    if len(codes) != 2:
        return None
    (a, b) = codes
    ia, ib = SIS_URBAN_SERVICES[a], SIS_URBAN_SERVICES[b]
    fv = lambda info: ("needs a field visit" if info.get("requires_field_visit") else "needs no field visit")
    fv_ta = lambda info: ("கள ஆய்வு தேவை" if info.get("requires_field_visit") else "கள ஆய்வு தேவையில்லை")
    if ta:
        return (f"{a} ({ia['name']}) மற்றும் {b} ({ib['name']}) வெவ்வேறு சேவைகள். "
                f"{a}-க்கு {fv_ta(ia)}; {b}-க்கு {fv_ta(ib)}.")
    return (f"{a} is {ia['name']}, {b} is {ib['name']} -- different services. "
            f"{a} ({ia['name']}) {fv(ia)}; {b} ({ib['name']}) {fv(ib)}.")


def compare_codes(text: str, rows: List[dict], ta: bool = False) -> Optional[str]:
    """Two service codes side by side from the register. Any code that is not 0153 / 0154 was read as
    ISD vs NISD before ("compare 0156 and 0153" answered ISD 29, NISD 28)."""
    t = text or ""
    if _APP_NO.search(t) or not _CMP_CODES.search(t):
        return None
    from backend.utils.helpers import SIS_URBAN_SERVICES
    codes = []
    for c in re.findall(r"(?<![\d/])(0?1[5-9]\d)(?![\d/])", t):
        c = c.zfill(4)
        if c in SIS_URBAN_SERVICES and c not in codes:
            codes.append(c)
    if len(codes) != 2 or (set(codes) == {"0153", "0154"} and not re.search(r"\bsame\s+as\b", t, re.IGNORECASE)):
        return None              # ISD vs NISD keeps its own comparison (counts, durations)
    st = lambda r: str(r.get("status") or "").lower()
    parts = []
    for c in codes:
        pool = [r for r in rows if str(r.get("service_code")) == c]
        rj = sum(1 for r in pool if st(r) == "rejected")
        nm = SIS_URBAN_SERVICES[c]["name"]
        parts.append((c, nm, len(pool), rj))
    (a, an, na, ra), (b, bn, nb, rb) = parts
    if re.search(r"\bsame\s+as\b", t, re.IGNORECASE):
        head = (f"இல்லை — {a} ({an}) மற்றும் {b} ({bn}) வெவ்வேறு சேவைகள்." if ta
                else f"No — {a} is {an} and {b} is {bn}; they are different services.")
    else:
        head = ""
    only_rej = bool(re.search(r"\brej\w*", t, re.IGNORECASE))
    if ta:
        body = (f"{a} ({an}): {na} கோப்புகள், நிராகரிக்கப்பட்டவை {ra}; {b} ({bn}): {nb} கோப்புகள், நிராகரிக்கப்பட்டவை {rb}.")
    elif only_rej:
        body = f"Rejected: {a} ({an}) {ra} of {na}; {b} ({bn}) {rb} of {nb}."
    else:
        lead = a if na > nb else b if nb > na else None
        body = (f"{a} ({an}): {na} files ({ra} rejected); {b} ({bn}): {nb} files ({rb} rejected)."
                + (f" {lead} has {abs(na - nb)} more." if lead else " They are level."))
    return (head + " " + body).strip()
