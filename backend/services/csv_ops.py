"""Deterministic operations over the rows of an uploaded CSV.

An exact question about a CSV -- how many, which ones, the total, the average,
the largest, the breakdown by column -- is arithmetic, and arithmetic is not
something llama3.1:8b should be doing over retrieved prose. This module parses
such a question into an operation, runs it in Python against the rows stored in
``attachment_rows``, and hands back the verified figure together with the row
numbers it came from. The answer layer renders that figure and those row
numbers; the model never computes and never chooses a citation.

Two rules the parser holds to:

* **A formula is text.** A cell beginning ``=``/``@`` (or ``+``/``-`` followed
  by a letter) is never evaluated and never parsed as a number -- it is matched
  as the literal string it is. That is both correctness and the CSV-injection
  defence.
* **Guessing a column is worse than declining.** If the question names no
  column that resolves against the header row, the operation that needs one is
  not returned at all, and the question falls through to ordinary evidence
  retrieval.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.services.doc_extract import is_formula_like

# ── operations ───────────────────────────────────────────────────────────────
OP_COUNT = "count"
OP_FILTER = "filter"
OP_SUM = "sum"
OP_AVG = "average"
OP_MIN = "min"
OP_MAX = "max"
OP_GROUP = "group"
OP_SORT = "sort"
OP_LOOKUP = "lookup"

NUMERIC_OPS = {OP_SUM, OP_AVG, OP_MIN, OP_MAX, OP_SORT}


@dataclass
class Condition:
    column: str
    operator: str          # eq | ne | gt | gte | lt | lte | contains
    value: str
    numeric: Optional[float] = None

    def describe(self) -> str:
        words = {"eq": "=", "ne": "≠", "gt": ">", "gte": "≥", "lt": "<",
                 "lte": "≤", "contains": "contains"}
        return f"{self.column} {words.get(self.operator, self.operator)} {self.value}"


@dataclass
class CsvOperation:
    op: str
    column: Optional[str] = None
    conditions: List[Condition] = field(default_factory=list)
    group_column: Optional[str] = None
    descending: bool = True
    limit: int = 10
    row_number: Optional[int] = None


@dataclass
class CsvResult:
    op: str
    ok: bool
    value: Any = None                       # the verified figure / label
    rows: List[int] = field(default_factory=list)      # 1-based row numbers
    groups: List[Tuple[str, int]] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)
    conditions: List[str] = field(default_factory=list)
    total_rows: int = 0
    considered: int = 0                     # rows that carried a usable number
    skipped_non_numeric: int = 0
    detail: str = ""
    samples: List[Dict[str, Any]] = field(default_factory=list)


# ── value handling ───────────────────────────────────────────────────────────

_NUM_CLEAN = re.compile(r"[,\s₹$€£%]|(?:^rs\.?)", re.IGNORECASE)


def to_number(value: str) -> Optional[float]:
    """Parse a cell as a number, or None. Formula-looking cells are never parsed."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or is_formula_like(s):
        return None
    s = _NUM_CLEAN.sub("", s)
    if s in ("", "-", "+", "."):
        return None
    # (1,234) is accounting-negative in exported sheets.
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if neg else n


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def resolve_column(text: str, headers: Sequence[str]) -> Optional[str]:
    """Find the header the text names. Longest header match wins; None if unsure."""
    t = _norm(text)
    if not t:
        return None
    best: Optional[str] = None
    best_len = 0
    for h in headers:
        hn = _norm(h)
        if not hn:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(hn)}(?![a-z0-9])", t) and len(hn) > best_len:
            best, best_len = h, len(hn)
    if best:
        return best
    # Single distinctive header word ("amount" for "Fee Amount (INR)").
    # >= 3, not > 3: a 3-letter word can be exactly the word that was asked
    # for -- "fee", "tax", "sla" -- and excluding it left "which application
    # has the highest FEE" resolving to the "application_number" header
    # (matched on "application") over "fee_amount" (whose only distinctive
    # word, "fee", was being dropped for being one character too short).
    matches = []
    for h in headers:
        words = [w for w in _norm(h).split() if len(w) >= 3]
        if any(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", t) for w in words):
            matches.append(h)
    return matches[0] if len(matches) == 1 else None


def _numeric_headers(headers: Sequence[str], rows: Sequence[Dict[str, str]]) -> List[str]:
    out = []
    for h in headers:
        vals = [to_number(r.get(h, "")) for r in rows[:200]]
        present = [v for v in vals if v is not None]
        if present and len(present) >= max(1, len([1 for r in rows[:200] if (r.get(h) or "").strip()]) * 0.8):
            out.append(h)
    return out


# ── question → operation ─────────────────────────────────────────────────────

_OP_WORDS: List[Tuple[str, Tuple[str, ...]]] = [
    (OP_AVG, ("average", "avg", "mean", "சராசரி")),
    (OP_SUM, ("sum", "total", "altogether", "combined", "மொத்தம்", "கூட்டுத்தொகை")),
    (OP_MAX, ("highest", "maximum", "max", "largest", "biggest", "greatest",
              "top", "most expensive", "அதிக", "அதிகபட்ச")),
    (OP_MIN, ("lowest", "minimum", "min", "smallest", "least", "cheapest",
              "குறைந்த", "குறைந்தபட்ச")),
    (OP_GROUP, ("group by", "grouped by", "breakdown", "break down", "per ",
                "how many of each", "each ", "by category")),
    (OP_SORT, ("sort", "order by", "ranked", "ranking", "top ")),
    (OP_COUNT, ("how many", "count", "number of", "எத்தனை", "எண்ணிக்கை")),
    (OP_FILTER, ("which rows", "which ones", "list", "show me", "show the",
                 "find", "filter", "எந்த")),
]

_COMPARATORS: List[Tuple[str, Tuple[str, ...]]] = [
    ("gte", ("at least", "no less than", ">=", "greater than or equal")),
    ("lte", ("at most", "no more than", "<=", "less than or equal")),
    ("gt", ("greater than", "more than", "larger than", "above", "over", ">")),
    ("lt", ("less than", "smaller than", "below", "under", "fewer than", "<")),
    ("ne", ("not equal", "is not", "isn't", "!=", "other than")),
    ("eq", ("equals", "equal to", "is exactly", " is ", " = ", "=")),
]


def _find_conditions(question: str, headers: Sequence[str],
                     rows: Sequence[Dict[str, str]]) -> List[Condition]:
    """Read filter conditions out of the question, conservatively.

    Only two shapes are accepted: an explicit ``<column> <comparator> <value>``,
    and a bare value that occurs in exactly one low-cardinality column. Anything
    less certain than that is left out — a filter nobody asked for silently
    changes the count.
    """
    q = question.lower()
    conds: List[Condition] = []
    used_spans: List[Tuple[int, int]] = []

    for h in headers:
        hn = _norm(h)
        if not hn:
            continue
        # Word-boundary anchored, like `resolve_column` -- without it "how
        # many rows" against a column named "observation" matched inside the
        # question's OWN filename ("...boundary_observations.csv"), since
        # "observations" carries "observation" as a bare substring. The tail
        # captured after that phantom match ("s csv how many rows are
        # there") then let a single stray letter become a filter value.
        pattern = rf"(?<![a-z0-9]){re.escape(hn)}(?![a-z0-9])\s*(?:is|=|:|was|of)?\s*"
        m = re.search(pattern + r"(.{0,40})", _norm(q))
        if not m:
            continue
        tail = m.group(1).strip()
        if not tail:
            continue
        op = "eq"
        for name, words in _COMPARATORS:
            for w in words:
                w2 = _norm(w)
                if w2 and tail.startswith(w2 + " "):
                    op = name
                    tail = tail[len(w2):].strip()
                    break
            else:
                continue
            break
        token = tail.split()[0] if tail.split() else ""
        if not token:
            continue
        num = to_number(token)
        if num is None and op in ("gt", "gte", "lt", "lte"):
            continue
        value = token
        if num is None:
            # Match the value against what the column actually holds, so
            # "status is approved" is a real filter and "status is fine" is not.
            # A 1-2 character token ("s", "of") is dropped before the substring
            # check below, which otherwise treats it as "found" in nearly every
            # row -- a single stray letter must never become a filter value.
            distinct = {(r.get(h) or "").strip() for r in rows}
            hit = next((d for d in distinct if d and _norm(d) == _norm(token)), None)
            if hit is None and len(_norm(token)) >= 3:
                # Word-boundary anchored -- an unanchored `in` matched "for"
                # inside "beFORe" ("Request reinstatement BEFORE sketch
                # finalisation"), manufacturing a second, wrong condition on
                # the very column the question was asking about.
                tok_n = _norm(token)
                hit = next((d for d in distinct if d and re.search(
                    rf"(?<![a-z0-9]){re.escape(tok_n)}(?![a-z0-9])", _norm(d))), None)
            if hit is None:
                continue
            value = hit
        conds.append(Condition(column=h, operator=op, value=value, numeric=num))
        used_spans.append(m.span())

    if conds:
        return conds

    # Bare value: "how many approved applications" over a status column.
    categorical = {}
    for h in headers:
        distinct = {(r.get(h) or "").strip() for r in rows if (r.get(h) or "").strip()}
        if 0 < len(distinct) <= 50:
            categorical[h] = distinct
    qn = _norm(q)
    hits: List[Condition] = []
    for h, distinct in categorical.items():
        for d in distinct:
            dn = _norm(d)
            if len(dn) < 3:
                continue
            if re.search(rf"(?<![a-z0-9]){re.escape(dn)}(?![a-z0-9])", qn):
                hits.append(Condition(column=h, operator="eq", value=d))
    # Only when the value is unambiguous — one column, one value.
    if len(hits) == 1:
        return hits
    if len({h.column for h in hits}) == 1 and len(hits) > 1:
        return []          # same column, several values: ambiguous, decline
    return []


def parse_csv_question(question: str, headers: Sequence[str],
                       rows: Sequence[Dict[str, str]]) -> Optional[CsvOperation]:
    """Classify an exact CSV question. Returns None when it is not one."""
    if not question or not headers:
        return None
    q = " " + question.lower().strip() + " "

    op: Optional[str] = None
    for name, words in _OP_WORDS:
        if any(w in q for w in words):
            op = name
            break
    # "which row has the highest fee" is a max, not a filter.
    if op in (OP_FILTER, OP_COUNT):
        for name, words in _OP_WORDS:
            if name in (OP_MAX, OP_MIN, OP_AVG, OP_SUM) and any(w in q for w in words):
                op = name
                break

    m_row = re.search(r"\brow\s+(\d+)\b", q)
    if m_row:
        return CsvOperation(op=OP_LOOKUP, row_number=int(m_row.group(1)))

    conds = _find_conditions(question, headers, list(rows))
    column = resolve_column(question, headers)
    # The column a filter names is not the column being measured or shown:
    # in "the fee where ward is 103", 'ward' is the condition and 'fee' the
    # answer. Resolve the answer column against the headers no filter claimed.
    cond_columns = {c.column for c in conds}
    if column in cond_columns:
        column = resolve_column(question, [h for h in headers if h not in cond_columns])

    if op is None:
        # "what is the fee where ward is 103" names no operation word, but it
        # names a condition and a column: that is a projection, not prose.
        if conds and column and not any(c.column == column for c in conds):
            return CsvOperation(op=OP_FILTER, column=column, conditions=conds)
        # No filter at all -- "what is the survey number" on a file where
        # every row carries the same one. Answerable without picking a row
        # only when the column is genuinely constant; a column with several
        # distinct values ("what is the corner") stays unanswered here rather
        # than guessing which row, and falls through to the ordinary
        # evidence path, which asks which one it means.
        if not conds and column:
            distinct = {(r.get(column) or "").strip() for r in rows}
            distinct.discard("")
            if len(distinct) == 1:
                return CsvOperation(op=OP_FILTER, column=column, conditions=[])
        return None

    if op in NUMERIC_OPS:
        # A numeric op can only ever measure a numeric column, so resolve
        # against THAT subset first. Without this, "which application has
        # the highest fee" resolved `column` to "application_number" --
        # matched on the word "application", which is in nearly every SIS
        # question and carries no numbers at all -- while the actually-named
        # numeric column ("fee_amount", matched on "fee") never got a look in,
        # because a non-numeric header had already claimed the single match.
        # A column a filter condition already claims (e.g. "ward" in "total
        # fee for ward 102") is the condition, not the measure -- excluded
        # here for the same reason the general `column` resolution above
        # excludes it, so "ward" (an equally numeric column, and the longer
        # word) cannot outrank "fee" for having more characters.
        numeric = [h for h in _numeric_headers(headers, list(rows)) if h not in cond_columns]
        numeric_column = resolve_column(question, numeric) if numeric else None
        if numeric_column:
            column = numeric_column
        elif column is None or column not in numeric:
            if len(numeric) == 1:
                column = numeric[0]
            else:
                return None
        if op == OP_SORT:
            # OP_SORT is in NUMERIC_OPS purely so it gets the numeric-column
            # resolution above; its actual direction was decided by a second
            # `if op == OP_SORT` block further down that this branch's own
            # `return` made permanently unreachable -- "sort ... ascending"
            # always came back descending; the officer had no way to ask for
            # the smallest fee first.
            desc = not any(w in q for w in ("ascending", "smallest first", "lowest first"))
            return CsvOperation(op=OP_SORT, column=column, conditions=conds, descending=desc)
        return CsvOperation(op=op, column=column, conditions=conds,
                            descending=(op != OP_MIN))

    if op == OP_GROUP:
        group_col = column
        if group_col is None:
            return None
        return CsvOperation(op=OP_GROUP, group_column=group_col, conditions=conds)

    return CsvOperation(op=op, conditions=conds)


# ── execution ────────────────────────────────────────────────────────────────

def _matches(row: Dict[str, str], cond: Condition) -> bool:
    raw = (row.get(cond.column) or "").strip()
    if cond.operator in ("gt", "gte", "lt", "lte"):
        n = to_number(raw)
        if n is None or cond.numeric is None:
            return False
        return {"gt": n > cond.numeric, "gte": n >= cond.numeric,
                "lt": n < cond.numeric, "lte": n <= cond.numeric}[cond.operator]
    if cond.operator == "eq":
        if cond.numeric is not None:
            n = to_number(raw)
            if n is not None:
                return n == cond.numeric
        return _norm(raw) == _norm(cond.value)
    if cond.operator == "ne":
        return _norm(raw) != _norm(cond.value)
    if cond.operator == "contains":
        return _norm(cond.value) in _norm(raw)
    return False


def execute(operation: CsvOperation, headers: Sequence[str],
            rows: Sequence[Dict[str, str]]) -> CsvResult:
    """Run the operation over the stored rows. Rows are 1-based by position."""
    numbered = [(i + 1, r) for i, r in enumerate(rows)]
    matched = [(n, r) for n, r in numbered
               if all(_matches(r, c) for c in operation.conditions)]
    cond_text = [c.describe() for c in operation.conditions]
    res = CsvResult(op=operation.op, ok=True, total_rows=len(rows),
                    conditions=cond_text,
                    columns=[c for c in [operation.column, operation.group_column]
                             if c])

    if operation.op == OP_LOOKUP:
        want = operation.row_number or 0
        hit = next((r for n, r in numbered if n == want), None)
        if hit is None:
            res.ok = False
            res.detail = f"The file has {len(rows)} data rows; there is no row {want}."
            return res
        res.value = hit
        res.rows = [want]
        res.samples = [dict(hit, **{"_row": want})]
        return res

    if operation.op in (OP_COUNT, OP_FILTER):
        res.value = len(matched)
        res.rows = [n for n, _ in matched]
        res.samples = [dict(r, **{"_row": n}) for n, r in matched[:20]]
        if operation.op == OP_FILTER and operation.column:
            # A projection: the asked-for column, cell by cell, with its row.
            res.value = [(n, (r.get(operation.column) or "").strip())
                         for n, r in matched[:20]]
        return res

    if operation.op == OP_GROUP:
        col = operation.group_column
        counts: Dict[str, int] = {}
        rows_by_value: Dict[str, List[int]] = {}
        for n, r in matched:
            key = (r.get(col) or "").strip() or "(blank)"
            counts[key] = counts.get(key, 0) + 1
            rows_by_value.setdefault(key, []).append(n)
        res.groups = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        res.value = res.groups
        res.rows = [n for n, _ in matched]
        res.considered = len(matched)
        return res

    # numeric operations
    col = operation.column
    pairs: List[Tuple[int, float]] = []
    skipped = 0
    for n, r in matched:
        v = to_number(r.get(col, ""))
        if v is None:
            if (r.get(col) or "").strip():
                skipped += 1
            continue
        pairs.append((n, v))
    res.considered = len(pairs)
    res.skipped_non_numeric = skipped
    if not pairs:
        res.ok = False
        res.detail = (f"No usable numbers in column '{col}'"
                      + (f" for {', '.join(cond_text)}" if cond_text else "") + ".")
        return res

    if operation.op == OP_SUM:
        res.value = sum(v for _, v in pairs)
        res.rows = [n for n, _ in pairs]
    elif operation.op == OP_AVG:
        res.value = sum(v for _, v in pairs) / len(pairs)
        res.rows = [n for n, _ in pairs]
    elif operation.op in (OP_MAX, OP_MIN):
        pick = max(pairs, key=lambda p: p[1]) if operation.op == OP_MAX \
            else min(pairs, key=lambda p: p[1])
        res.value = pick[1]
        res.rows = [pick[0]]
        row = next(r for n, r in numbered if n == pick[0])
        res.samples = [dict(row, **{"_row": pick[0]})]
        # Ties are stated rather than hidden: the same question must not name a
        # different row on a reseed.
        ties = [n for n, v in pairs if v == pick[1]]
        if len(ties) > 1:
            res.rows = sorted(ties)
            res.detail = f"{len(ties)} rows tie on this value."
    elif operation.op == OP_SORT:
        ordered = sorted(pairs, key=lambda p: p[1], reverse=operation.descending)
        top = ordered[:operation.limit]
        res.value = [(n, v) for n, v in top]
        res.rows = [n for n, _ in top]
        by_num = dict(numbered)
        res.samples = [dict(by_num[n], **{"_row": n}) for n, _ in top]
    return res


# ── citations ────────────────────────────────────────────────────────────────

def row_ranges(row_numbers: Sequence[int]) -> List[Tuple[int, int]]:
    """Compress row numbers into contiguous (start, end) ranges."""
    out: List[Tuple[int, int]] = []
    for n in sorted(set(row_numbers)):
        if out and n == out[-1][1] + 1:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return out


def row_citation(filename: str, row_numbers: Sequence[int], max_ranges: int = 4) -> str:
    """Render 'register.csv, rows 18–24' from row numbers we actually matched."""
    ranges = row_ranges(row_numbers)
    if not ranges:
        return ""
    shown = ranges[:max_ranges]
    parts = [f"{a}" if a == b else f"{a}–{b}" for a, b in shown]
    word = "row" if (len(ranges) == 1 and ranges[0][0] == ranges[0][1]) else "rows"
    tail = "" if len(ranges) <= max_ranges else f" and {len(ranges) - max_ranges} more"
    return f"{filename}, {word} {', '.join(parts)}{tail}"
