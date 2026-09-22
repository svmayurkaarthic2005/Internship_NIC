"""Arithmetic over the officer's own applications: per year / per month / per quarter breakdowns,
averages per period, growth between years, percentages and rates, fee totals / averages / extremes,
and turnaround times -- each computed from database rows (one per application, every status) handed
in by the caller. Nothing is read from an earlier answer's text, and the denominator of every average
or percentage is stated in the answer.

The fee is recorded on only part of the register: fee sums and averages are over the applications that
carry a fee record, and the answer says how many that is.
"""
import re
from collections import Counter, defaultdict
from datetime import date
from statistics import median
from typing import Dict, List, Optional, Tuple

from backend.services import neg_scope

_APP_NO = re.compile(r"\d{4}/\d{3,4}/\d{1,3}/\d+")
_MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]
_MON_ABBR = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
             "oct": 10, "nov": 11, "dec": 12}
_MONTH_WORD = re.compile(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|"
                         r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", re.IGNORECASE)
_YEAR = re.compile(r"\b(20\d\d)\b")
_FEE = re.compile(r"\bfees?\b|கட்டண", re.IGNORECASE)
_AVG = re.compile(r"\b(?:average|avg|mean)\b|சராசரி", re.IGNORECASE)
_MEDIAN = re.compile(r"\bmedian\b", re.IGNORECASE)
_MAX = re.compile(r"\b(?:highest|maximum|max|largest|biggest|costliest|most\s+expensive)\b", re.IGNORECASE)
_MIN = re.compile(r"\b(?:lowest|minimum|min|smallest|cheapest|least\s+expensive)\b", re.IGNORECASE)
_SUM = re.compile(r"\b(?:total|sum|collected|collection|overall)\b|மொத்த", re.IGNORECASE)
_PCT = re.compile(r"\b(?:percent(?:age)?|pct|share|proportion)\b|%|சதவீத|சதவிகித", re.IGNORECASE)
_RATE = re.compile(r"\b(approval|approved|rejection|rejected|acceptance)\s+rate\b|\brate\s+of\s+(approval|rejection)\b", re.IGNORECASE)
_GROWTH = re.compile(r"\b(?:growth|grew|increase[sd]?|decrease[sd]?|change|rise|rose|drop(?:ped)?|declin\w+|decline|"
                     r"year[\s-]on[\s-]year|yoy|trend)\b", re.IGNORECASE)
_TIME = re.compile(r"\b(?:time|days|duration|turnaround|long)\b|நாட்கள்", re.IGNORECASE)
_PER = re.compile(r"\b(?:per|by|each|every)\s+(year|month|quarter|type|channel|status)s?\b|\b(year|month|quarter|type|channel|status)[\s-]?wise\b"
                  r"|\b(yearly|annual|annually|monthly|quarterly|half[\s-]?yearly)\b|ஆண்டு\s*வாரியாக|மாத\s*வாரியாக"
                  r"|\b(?:(matham|madham|maasam)|(varusham|varudam|aandu))\s*(?:vaar\w*|vaari\w*)\b", re.IGNORECASE)
_MOST = re.compile(r"\b(?:most|highest|busiest|maximum|max|peak)\b", re.IGNORECASE)
_LEAST = re.compile(r"\b(?:least|lowest|fewest|quietest|minimum|min)\b", re.IGNORECASE)
_VS = re.compile(r"\b(?:vs\.?|versus|compare[ds]?|comparison|against|between|from)\b|ஒப்பிட", re.IGNORECASE)
_NOUN = re.compile(r"\b(?:applications?|aplications?|apps?|files?|cases)\b|விண்ணப்ப", re.IGNORECASE)
_QUARTER = re.compile(r"\bq([1-4])\b(?:\s*(?:of\s+)?(20\d\d))?|\b(first|second|third|fourth)\s+quarter\b", re.IGNORECASE)
_HALF = re.compile(r"\b(?:h([12])|(first|second|1st|2nd)\s+half)\b(?:\s*(?:of|in)?\s*(20\d\d))?", re.IGNORECASE)


# ── rows ───────────────────────────────────────────────────────────────────────────────────────────
def _d(r) -> Optional[date]:
    v = r.get("submission_date")
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _st(r) -> str:
    return str(r.get("status") or "").lower().replace(" ", "_")


def _ty(r) -> str:
    return str(r.get("type") or "").upper()


def _match(r, terms) -> bool:
    by: Dict[str, set] = {}
    for k, v in terms:
        by.setdefault(k, set()).add(v)
    got = {"status": _st(r), "type": _ty(r), "channel": str(r.get("submission_channel") or ""),
           "ward": str(r.get("ward_number") or "").zfill(3)}
    return all(got[k] in vals for k, vals in by.items())


def _money(v: float) -> str:
    return f"₹{v:,.2f}"


def _pct(n, d) -> str:
    return f"{n * 100 / d:.1f}%" if d else "0.0%"


def _signed_pct(a, b) -> str:
    """Change from a to b as a percentage of a."""
    if not a:
        return "n/a"
    ch = (b - a) * 100 / a
    return f"{'+' if ch >= 0 else '−'}{abs(ch):.1f}%"


def _label_terms(terms, ta: bool) -> str:
    lab = {"approved": "அங்கீகரிக்கப்பட்ட" if ta else "approved", "rejected": "நிராகரிக்கப்பட்ட" if ta else "rejected",
           "pending": "நிலுவையில் உள்ள" if ta else "pending", "in_progress": "செயல்பாட்டில்" if ta else "in progress",
           "sub_registrar": "Sub-Registrar", "CSC": "CSC", "citizen": "citizen"}
    return " ".join(lab.get(v, v) for _k, v in terms)


# ── periods ────────────────────────────────────────────────────────────────────────────────────────
def _period_of(text: str) -> Optional[Tuple[date, date, str]]:
    """A single named period in the message: "in 2025", "june 2025", "Q1 2025", "first half of 2025"."""
    q = _QUARTER.search(text)
    years = _YEAR.findall(text)
    if q:
        n = int(q.group(1)) if q.group(1) else {"first": 1, "second": 2, "third": 3, "fourth": 4}[q.group(3).lower()]
        y = int(q.group(2)) if q.group(2) else (int(years[0]) if years else date.today().year)
        s, e = date(y, 3 * n - 2, 1), date(y, 3 * n, [31, 30, 30, 31][n - 1] if n in (1, 2, 3, 4) else 31)
        return s, e, f"Q{n} {y}"
    h = _HALF.search(text)
    if h:
        n = 1 if (h.group(1) == "1" or (h.group(2) or "").lower() in ("first", "1st")) else 2
        y = int(h.group(3)) if h.group(3) else (int(years[0]) if years else date.today().year)
        return (date(y, 1, 1), date(y, 6, 30), f"first half of {y}") if n == 1 else (date(y, 7, 1), date(y, 12, 31), f"second half of {y}")
    m = _MONTH_WORD.search(text)
    if m and len(set(years)) == 1:
        mo = _MON_ABBR[m.group(1).lower()[:4] if m.group(1).lower().startswith("sept") else m.group(1).lower()[:3]]
        y = int(years[0])
        last = (date(y + (mo == 12), mo % 12 + 1, 1) - date.resolution)
        return date(y, mo, 1), last, f"{_MONTHS[mo]} {y}"
    if len(set(years)) == 1 and not _VS.search(text):
        y = int(years[0])
        return date(y, 1, 1), date(y, 12, 31), str(y)
    return None


def _in(r, s: date, e: date) -> bool:
    d = _d(r)
    return bool(d and s <= d <= e)


def _bucket(r, kind: str) -> Optional[str]:
    d = _d(r)
    if not d:
        return None
    if kind == "year":
        return str(d.year)
    if kind == "month":
        return f"{d.year}-{d.month:02d}"
    if kind == "quarter":
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    return None


def _pretty(key: str, kind: str) -> str:
    if kind == "month":
        return f"{_MONTHS[int(key[5:7])]} {key[:4]}"
    if kind == "quarter":
        return f"{key[5:]} {key[:4]}"
    return key


def _fill(keys: List[str], kind: str, span: Optional[Tuple[date, date]]) -> List[str]:
    """Every period from the first to the last (zero-filled), so a quiet month is shown as 0."""
    if not keys:
        return []
    if kind == "year":
        a, b = int(min(keys)), int(max(keys))
        if span:
            a, b = span[0].year, span[1].year
        return [str(y) for y in range(a, b + 1)]
    if kind == "month":
        a, b = min(keys), max(keys)
        ay, am = int(a[:4]), int(a[5:7])
        by, bm = int(b[:4]), int(b[5:7])
        if span:
            ay, am, by, bm = span[0].year, span[0].month, span[1].year, span[1].month
            today = date.today()
            if (by, bm) > (today.year, today.month):
                by, bm = today.year, today.month
        out, y, m = [], ay, am
        while (y, m) <= (by, bm):
            out.append(f"{y}-{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return out
    if kind == "quarter":
        a, b = min(keys), max(keys)
        out, y, q = [], int(a[:4]), int(a[6])
        while (y, q) <= (int(b[:4]), int(b[6])):
            out.append(f"{y}-Q{q}")
            y, q = (y + 1, 1) if q == 4 else (y, q + 1)
        return out
    return sorted(keys)


def _years_named(text: str) -> List[int]:
    return sorted({int(y) for y in _YEAR.findall(text)})


# ── the answer ─────────────────────────────────────────────────────────────────────────────────────
_WHICH_PERIOD = re.compile(r"\b(?:which|what)\s+(?:year|month|quarter)\b|\b(?:busiest|quietest)\s+(?:year|month|quarter)\b", re.IGNORECASE)
_TYPE_ASK = re.compile(r"\b(?:which|what)\s+(?:type|service)\b|\bby\s+type\b", re.IGNORECASE)


def looks_analytic(text: str) -> bool:
    t = text or ""
    return bool(_WHICH_PERIOD.search(t) or _YEAR.search(t) or _PER.search(t) or _AVG.search(t) or _MEDIAN.search(t) or _PCT.search(t) or _RATE.search(t)
                or _GROWTH.search(t) or _FEE.search(t) or _QUARTER.search(t) or _HALF.search(t) or _TIME.search(t))


def answer(message: str, rows: List[dict], ta: bool = False) -> Optional[str]:
    text = (message or "").strip()
    if (not text or len(text.split()) > 16 or _APP_NO.search(text) or not rows or neg_scope.parse(text)[0]
            or not looks_analytic(text)):
        return None
    if re.search(r"\b(?:sort(?:ed)?|show|list|display|give\s+me\s+the\s+list)\b", text, re.IGNORECASE) and not (
            _AVG.search(text) or _PCT.search(text) or _PER.search(text)):
        return None
    low = text.lower()
    fee = bool(_FEE.search(text))
    years = _years_named(text)
    terms = [t for t in neg_scope._terms(text) if t[0] in ("status", "type", "channel")]
    per = _PER.search(text)
    per_kind = None
    if per:
        raw = (per.group(1) or per.group(2) or per.group(3) or "").lower()
        if per.group(4):
            raw = "month"
        elif per.group(5):
            raw = "year"
        per_kind = {"yearly": "year", "annual": "year", "annually": "year", "monthly": "month", "quarterly": "quarter",
                    "halfyearly": "half", "half-yearly": "half"}.get(raw, raw)
        if "ஆண்டு" in text:
            per_kind = "year"
        if "மாத" in text:
            per_kind = "month"
    period = _period_of(text)
    span = (period[0], period[1]) if period else None
    base = [r for r in rows if _match(r, terms)] if terms else list(rows)
    scoped = [r for r in base if _in(r, span[0], span[1])] if span else base
    tlabel = _label_terms(terms, ta)
    noun = (f"{tlabel} " if tlabel else "") + ("விண்ணப்பங்கள்" if ta else "applications")

    # ── growth / change between years ───────────────────────────────────────────────────────────────
    if len(years) < 2 and _GROWTH.search(text) and re.search(r"year[\s-]on[\s-]year|yoy|yearly|annual|trend|growth", low) and not fee:
        ys = sorted({_d(r).year for r in base if _d(r)})
        if len(ys) >= 2:
            years = [ys[0], ys[-1]]
    if len(years) >= 2 and (_GROWTH.search(text) or (_PCT.search(text) and _VS.search(text))):
        y0, y1 = years[0], years[-1]
        val = (lambda ys: sum((r["fee"] or 0.0) for r in base if _d(r) and _d(r).year == ys)) if fee else \
              (lambda ys: sum(1 for r in base if _d(r) and _d(r).year == ys))
        seq = [(y, val(y)) for y in range(y0, y1 + 1)]
        fmt = _money if fee else (lambda v: str(int(v)))
        what = ("கட்டணம்" if ta else "Fee collected") if fee else (noun[0].upper() + noun[1:] if not ta else noun)
        if len(seq) == 2:
            (a, va), (b, vb) = seq
            diff = vb - va
            if va == 0:
                return f"{what} — {a}: {fmt(va)}, {b}: {fmt(vb)} (no {a} figure to compare against)."
            if ta:
                verb = "உயர்வு" if diff > 0 else "குறைவு" if diff < 0 else "மாற்றமில்லை"
                return f"{what} — {a}: {fmt(va)}, {b}: {fmt(vb)}: {fmt(abs(diff))} {verb} ({_signed_pct(va, vb)})."
            verb = "up" if diff > 0 else "down" if diff < 0 else "unchanged"
            return f"{what}: {fmt(va)} in {a} → {fmt(vb)} in {b} — {verb} {fmt(abs(diff))} ({_signed_pct(va, vb)})."
        parts = []
        for i, (y, v) in enumerate(seq):
            parts.append(f"{y} {fmt(v)}" + (f" ({_signed_pct(seq[i - 1][1], v)})" if i and seq[i - 1][1] else ""))
        return f"{what} by year — " + " → ".join(parts) + "."

    # ── a comparison of two years / halves on a non-count measure ─────────────────────────────────────
    if len(years) == 2 and _VS.search(text) and not per_kind and (fee or _RATE.search(text) or _AVG.search(text)):
        sides = []
        for y in years:
            grp = [r for r in base if _d(r) and _d(r).year == y]
            sides.append((str(y), grp))
        return _measure_line(text, sides, fee, ta, noun)

    # ── halves compared: "first half of 2025 vs second half" ─────────────────────────────────────────
    hs = re.findall(r"\b(first|second)\s+half\b|\bh([12])\b", low)
    if len(hs) == 2 and (years or True) and _VS.search(text):
        y = years[0] if years else date.today().year
        sides = [("first half of %d" % y, [r for r in base if _in(r, date(y, 1, 1), date(y, 6, 30))]),
                 ("second half of %d" % y, [r for r in base if _in(r, date(y, 7, 1), date(y, 12, 31))])]
        return _measure_line(text, sides, fee, ta, noun)

    # ── two quarters compared: "compare q1 2025 and q3 2025" ────────────────────────────────────────
    qs = list(_QUARTER.finditer(text))
    if len(qs) == 2 and _VS.search(text):
        sides = []
        for m in qs:
            n = int(m.group(1)) if m.group(1) else {"first": 1, "second": 2, "third": 3, "fourth": 4}[m.group(3).lower()]
            y = int(m.group(2)) if m.group(2) else (years[0] if years else date.today().year)
            sides.append((f"Q{n} {y}", [r for r in base if _in(r, date(y, 3 * n - 2, 1), date(y + (n == 4), (3 * n) % 12 + 1, 1) - date.resolution)]))
        return _measure_line(text, sides, fee, ta, noun)

    # ── which year / month / quarter had the most / least ────────────────────────────────────────────
    sup = _MOST.search(text) or _LEAST.search(text)
    which_kind = re.search(r"\b(?:which|what)\s+(year|month|quarter)\b|\b(busiest|quietest)\s+(year|month|quarter)\b", low)
    if sup and which_kind and not fee:
        kind = which_kind.group(1) or which_kind.group(3)
        if kind == "month" and not span:
            return None                   # "which month had the most" over the whole record: the comparison handler's answer
        cnt = Counter(_bucket(r, kind) for r in scoped if _bucket(r, kind))
        if not cnt:
            return None
        keys = _fill(list(cnt), kind, span)
        vals = {k: cnt.get(k, 0) for k in keys}
        want_max = bool(_MOST.search(text)) and not _LEAST.search(text)
        best = max(vals.values()) if want_max else min(vals.values())
        winners = [k for k in keys if vals[k] == best]
        w = ", ".join(_pretty(k, kind) for k in winners)
        word = ("அதிகம்" if want_max else "குறைவு") if ta else ("most" if want_max else "fewest")
        return (f"{w} — {best} {noun}, {word}; {len(keys)} {kind}s compared ({len(scoped)} in all)."
                if not ta else f"{w} — {best} {noun} ({word}).")

    # ── average per month / year / quarter ────────────────────────────────────────────────────────────
    if (_AVG.search(text) or _MEDIAN.search(text)) and not fee and not _TIME.search(text) and per_kind in ("month", "year", "quarter"):
        cnt = Counter(_bucket(r, per_kind) for r in scoped if _bucket(r, per_kind))
        if not cnt:
            return None
        keys = _fill(list(cnt), per_kind, span)
        vals = [cnt.get(k, 0) for k in keys]
        if _MEDIAN.search(text):
            return f"Median {noun} per {per_kind}: {median(vals):g} over {len(keys)} {per_kind}s ({sum(vals)} in all)."
        mean = sum(vals) / len(vals)
        where = f" in {period[2]}" if period else ""
        return (f"Average {noun} per {per_kind}{where}: {mean:.1f} ({sum(vals)} applications over {len(keys)} {per_kind}s"
                f"{'' if period else ', first to last on record, quiet ones counted as 0'})." if not ta else
                f"{where} மாதம்/ஆண்டு சராசரி {noun}: {mean:.1f} ({len(keys)} காலத்தில் {sum(vals)}).")

    # ── turnaround: average days to decide, by type / year ─────────────────────────────────────────────
    if (_TIME.search(text) and (per_kind or re.search(r"\bcompare|vs\b|versus", low)
                                or len({v for k, v in neg_scope._terms(text) if k == "type"}) >= 2) and not fee):
        decided = [r for r in scoped if r.get("days_to_decide") is not None and (not terms or True)]
        if re.search(r"\bapprov", low):
            decided = [r for r in decided if _st(r) == "approved"]
        if not decided:
            return None
        kind = per_kind if per_kind in ("type", "year", "channel", "month") else None
        if kind is None and len({v for k, v in neg_scope._terms(text) if k == "type"}) >= 2:
            kind = "type"
        if kind is None:
            d = [r["days_to_decide"] for r in decided]
            return f"Average time to decide: {sum(d) / len(d):.1f} days over {len(d)} decided applications (median {median(d):g})."
        key = {"type": _ty, "channel": lambda r: str(r.get("submission_channel") or ""),
               "year": lambda r: str(_d(r).year) if _d(r) else "", "month": lambda r: (_bucket(r, "month") or "")}[kind]
        g: Dict[str, List[int]] = defaultdict(list)
        for r in decided:
            if key(r):
                g[key(r)].append(r["days_to_decide"])
        if kind == "type" and len({v for k, v in neg_scope._terms(text) if k == "type"}) >= 2:
            want = {v for k, v in neg_scope._terms(text) if k == "type"}
            g = {k: v for k, v in g.items() if k in want}
        items = sorted(g.items())
        body = ", ".join(f"{_pretty(k, kind) if kind == 'month' else k} {sum(v) / len(v):.1f} days ({len(v)})" for k, v in items)
        slow = max(items, key=lambda kv: sum(kv[1]) / len(kv[1]))
        fast = min(items, key=lambda kv: sum(kv[1]) / len(kv[1]))
        tail = (f" {slow[0]} takes {sum(slow[1]) / len(slow[1]) - sum(fast[1]) / len(fast[1]):.1f} days longer than {fast[0]}."
                if len(items) >= 2 and slow[0] != fast[0] else "")
        return f"Average days from filing to decision by {kind} — {body}.{tail}"

    # ── approval / rejection rate ─────────────────────────────────────────────────────────────────────
    rate = _RATE.search(text) or (re.search(r"\b(?:approval|rejection)\s+rate\b", low))
    if rate:
        want_rej = "reject" in low
        kind = per_kind if per_kind in ("year", "month", "quarter") else None
        def stat(grp):
            done = [r for r in grp if _st(r) in ("approved", "rejected")]
            n = sum(1 for r in done if _st(r) == ("rejected" if want_rej else "approved"))
            return n, len(done)
        name = ("Rejection" if want_rej else "Approval") + " rate"
        if kind:
            buckets = defaultdict(list)
            for r in scoped:
                b = _bucket(r, kind)
                if b:
                    buckets[b].append(r)
            body = ", ".join(f"{_pretty(k, kind)} {_pct(*stat(buckets[k]))} ({stat(buckets[k])[0]} of {stat(buckets[k])[1]})"
                             for k in sorted(buckets) if stat(buckets[k])[1])
            return f"{name} per {kind} (of applications decided) — {body}." if body else None
        if len(years) == 2 and _VS.search(text):
            body = " vs ".join(f"{y}: {_pct(*stat([r for r in base if _d(r) and _d(r).year == y]))} "
                               f"({stat([r for r in base if _d(r) and _d(r).year == y])[0]} of "
                               f"{stat([r for r in base if _d(r) and _d(r).year == y])[1]} decided)" for y in years)
            return f"{name} — {body}."
        n, d = stat(scoped)
        where = f" in {period[2]}" if period else ""
        return f"{name}{where}: {_pct(n, d)} — {n} of {d} decided applications (pending and in-progress files are not counted)." if d else None

    # ── fee arithmetic ────────────────────────────────────────────────────────────────────────────────
    if fee and (per_kind or _TYPE_ASK.search(text) or _AVG.search(text) or _MAX.search(text) or _MIN.search(text) or _PCT.search(text)
                or (_VS.search(text) and (len(years) == 2 or len({v for k, v in neg_scope._terms(text) if k == "type"}) >= 2))
                or re.search(r"\bhigher|lower|more|less|which\b", low)):
        with_fee = [r for r in scoped if r["fee"] is not None]
        if _PCT.search(text) and re.search(r"record|with|have|carry", low):
            return (f"{len(with_fee)} of {len(scoped)} {noun} ({_pct(len(with_fee), len(scoped))}) carry a fee record."
                    if scoped else None)
        if (_MAX.search(text) or _MIN.search(text)) and with_fee:
            hi = _MAX.search(text) is not None
            pick = max if hi else min
            best = pick(r["fee"] for r in with_fee)
            hit = [r for r in with_fee if r["fee"] == best]
            listed = ", ".join(r["application_number"] for r in hit[:3]) + (f" and {len(hit) - 3} more" if len(hit) > 3 else "")
            return (f"{'Highest' if hi else 'Lowest'} fee on record: {_money(best)} — {listed} "
                    f"({len(hit)} application{'s' if len(hit) != 1 else ''}; {len(with_fee)} of {len(scoped)} carry a fee).")
        kind = per_kind if per_kind in ("year", "month", "quarter", "type") else None
        types = {v for k, v in neg_scope._terms(text) if k == "type"}
        if kind is None and (len(types) >= 2 or _TYPE_ASK.search(text)):
            kind = "type"
        if kind is None and len(years) == 2:
            kind = "year"
        if kind:
            g: Dict[str, List[float]] = defaultdict(list)
            n_all: Counter = Counter()
            for r in scoped:
                k = _ty(r) if kind == "type" else _bucket(r, kind)
                if not k or (kind == "type" and types and k not in types):
                    continue
                n_all[k] += 1
                if r["fee"] is not None:
                    g[k].append(r["fee"])
            keys = sorted(n_all) if kind == "type" else [k for k in _fill(list(n_all), kind, span) if n_all.get(k)]
            if kind == "year" and len(years) == 2:
                keys = [str(y) for y in years]
            lines = []
            for k in keys:
                v = g.get(k, [])
                lines.append(f"{_pretty(k, kind) if kind != 'type' else k} {_money(sum(v))}"
                             + (f" (avg {_money(sum(v) / len(v))} over {len(v)} of {n_all.get(k, 0)})" if v else f" (no fee on {n_all.get(k, 0)})"))
            head = "Fee collected " + (f"per {kind}" if kind else "")
            tail = ""
            if len(keys) == 2:
                a, b = (sum(g.get(keys[0], [])), sum(g.get(keys[1], [])))
                if a != b:
                    hi, lo = (keys[0], keys[1]) if a > b else (keys[1], keys[0])
                    tail = f" {hi} is higher by {_money(abs(a - b))}" + (f" ({_signed_pct(min(a, b), max(a, b))} more)" if min(a, b) else "") + "."
            return head + " — " + "; ".join(lines) + "." + tail + " Only applications with a fee record are summed."
        if _AVG.search(text):
            if not with_fee:
                return None
            mean = sum(r["fee"] for r in with_fee) / len(with_fee)
            where = f" in {period[2]}" if period else ""
            return (f"Average fee{where}: {_money(mean)} over the {len(with_fee)} of {len(scoped)} {noun} that carry a fee record "
                    f"(total {_money(sum(r['fee'] for r in with_fee))}).")
        return None

    # ── percentage of applications in a period / per period ──────────────────────────────────────────
    if _PCT.search(text) and not fee:
        if per_kind in ("year", "month", "quarter"):
            cnt = Counter(_bucket(r, per_kind) for r in scoped if _bucket(r, per_kind))
            tot = sum(cnt.values())
            if not tot:
                return None
            keys = _fill(list(cnt), per_kind, span)
            body = ", ".join(f"{_pretty(k, per_kind)} {_pct(cnt.get(k, 0), tot)} ({cnt.get(k, 0)})" for k in keys)
            return f"Share of {noun} per {per_kind} (of {tot}) — {body}."
        if span:
            n = len(scoped)
            tot = len(base)
            line = f"{n} of your {tot} {noun} ({_pct(n, tot)}) were filed in {period[2]}."
            if terms:      # "rejected in 2023" reads two ways: say both
                yr = [r for r in rows if _in(r, span[0], span[1])]
                line = (f"{n} of the {len(yr)} applications filed in {period[2]} were {tlabel} ({_pct(n, len(yr))}); "
                        f"{n} of all {tot} {noun} ({_pct(n, tot)}) were filed then.")
            return line
        return None

    # ── plain breakdown per year / month / quarter (count) ───────────────────────────────────────────
    if per_kind in ("year", "month", "quarter") and (_NOUN.search(text) or _COUNT_WORD.search(text)):
        cnt = Counter(_bucket(r, per_kind) for r in scoped if _bucket(r, per_kind))
        if not cnt:
            return None
        keys = _fill(list(cnt), per_kind, span)
        body = ", ".join(f"{_pretty(k, per_kind)} {cnt.get(k, 0)}" for k in keys)
        where = f" in {period[2]}" if period else ""
        head_ta = {"year": "ஆண்டு", "month": "மாதம்", "quarter": "காலாண்டு"}[per_kind]
        return (f"Applications per {per_kind}{where} — {body} (total {sum(cnt.values())})." if not ta
                else f"{head_ta} வாரியாக விண்ணப்பங்கள் — {body} (மொத்தம் {sum(cnt.values())}).")

    # ── a quarter / half on its own ─────────────────────────────────────────────────────────────────
    if period and (_QUARTER.search(text) or _HALF.search(text)) and (_COUNT_WORD.search(text) or _NOUN.search(text)):
        return f"{len(scoped)} {noun} were filed in {period[2]}."
    return None


_COUNT_WORD = re.compile(r"\b(?:how\s+many|count|number|total)\b|எத்தனை", re.IGNORECASE)


def _measure_line(text: str, sides, fee: bool, ta: bool, noun: str) -> Optional[str]:
    """Two groups compared on fee / approval rate / average time, from the same rows."""
    low = text.lower()
    out = []
    for name, grp in sides:
        if fee:
            v = [r["fee"] for r in grp if r["fee"] is not None]
            out.append((name, sum(v), f"{_money(sum(v))} (avg {_money(sum(v) / len(v))} over {len(v)} of {len(grp)})" if v
                        else f"no fee on {len(grp)}"))
        elif _RATE.search(text):
            want_rej = "reject" in low
            done = [r for r in grp if _st(r) in ("approved", "rejected")]
            n = sum(1 for r in done if _st(r) == ("rejected" if want_rej else "approved"))
            out.append((name, n * 100 / len(done) if done else 0.0, f"{_pct(n, len(done))} ({n} of {len(done)} decided)"))
        elif _AVG.search(text) or _TIME.search(text):
            d = [r["days_to_decide"] for r in grp if r.get("days_to_decide") is not None]
            out.append((name, sum(d) / len(d) if d else 0.0, f"{sum(d) / len(d):.1f} days ({len(d)} decided)" if d else "none decided"))
        else:
            out.append((name, float(len(grp)), f"{len(grp)}"))
    head = "Fee collected" if fee else ("Approval rate" if _RATE.search(text) and "reject" not in low else
                                        "Rejection rate" if _RATE.search(text) else
                                        "Average time to decide" if (_AVG.search(text) or _TIME.search(text)) else "Applications filed")
    line = f"{head} — " + " vs ".join(f"{n}: {t}" for n, _v, t in out) + "."
    if len(out) == 2 and out[0][1] != out[1][1]:
        hi, lo = (out[0], out[1]) if out[0][1] > out[1][1] else (out[1], out[0])
        diff = hi[1] - lo[1]
        amt = (_money(diff) if fee else f"{diff:.1f} points" if _RATE.search(text)
               else f"{diff:.1f} days" if (_AVG.search(text) or _TIME.search(text)) else f"{int(diff)}")
        line += f" {hi[0]} is higher by {amt}" + (f" ({_signed_pct(lo[1], hi[1])} more)" if lo[1] and not _RATE.search(text) else "") + "."
    return line
