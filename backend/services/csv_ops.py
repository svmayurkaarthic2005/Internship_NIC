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
    row_numbers: List[int] = field(default_factory=list)


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
        # A trailing "s" is allowed after the header text itself -- "service
        # codes" (the natural plural a question asks in) must match a
        # `service_code` header the same way the singular already does.
        # Without this, "List all the service codes in this file." failed
        # this full-header match (the literal header ends in "code", the
        # question in "codes", so the right-hand word boundary never closed)
        # and fell to the single-word fallback below, where "service" alone
        # is ALSO a distinctive word of a different column
        # ("service_description"), so two columns tied and resolve_column()
        # refused to pick either -- confirmed live through the chatbot UI,
        # 2026-10-13 ("I could not find this in the uploaded document." for
        # a column that plainly exists). The "s" is appended only to the
        # very end of the already-escaped header text, so a multi-word
        # header ("application number" -> "application numbers") is
        # pluralised as a whole, not word-by-word.
        if re.search(rf"(?<![a-z0-9]){re.escape(hn)}(?:es|s)?(?![a-z0-9])", t) and len(hn) > best_len:
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
                 "find", "filter", "compare", "comparison", "difference",
                 "versus", "vs", "differ", "consistent", "agree", "எந்த", "ஒப்பிடு")),
]

# The symbolic comparators (">=", "!=", ...) are matched against the RAW
# question text via `_SYMBOL_COMPARATOR_RE` and rewritten to these plain
# words before anything is normalised -- `_norm()` reduces any run of
# non-alphanumeric characters to a single space, which silently erased every
# symbol here (">=" and "!=" both just vanished) before this fix, so the
# default "eq" operator was used no matter what symbol was actually typed.
_SYMBOL_TO_WORD = {
    ">=": "symbolgte", "<=": "symbollte", "!=": "symbolne",
    "=": "symboleq", ">": "symbolgt", "<": "symbollt",
}
_SYMBOL_COMPARATOR_RE = re.compile("|".join(re.escape(s) for s in
    sorted(_SYMBOL_TO_WORD, key=len, reverse=True)))  # longest symbol first

_COMPARATORS: List[Tuple[str, Tuple[str, ...]]] = [
    ("gte", ("at least", "no less than", "symbolgte", "greater than or equal")),
    ("lte", ("at most", "no more than", "symbollte", "less than or equal")),
    ("gt", ("greater than", "more than", "larger than", "above", "over", "symbolgt")),
    ("lt", ("less than", "smaller than", "below", "under", "fewer than", "symbollt")),
    ("ne", ("not equal", "is not", "isn't", "symbolne", "other than", "not")),
    ("eq", ("equals", "equal to", "is exactly", "is", "symboleq")),
]


def _find_conditions(question: str, headers: Sequence[str],
                     rows: Sequence[Dict[str, str]]) -> List[Condition]:
    """Read filter conditions out of the question, conservatively.

    Only two shapes are accepted: an explicit ``<column> <comparator> <value>``,
    and a bare value that occurs in exactly one low-cardinality column. Anything
    less certain than that is left out — a filter nobody asked for silently
    changes the count.
    """
    # Symbols rewritten to plain words BEFORE `_norm()` ever sees them -- see
    # `_SYMBOL_TO_WORD`'s comment.
    q = _SYMBOL_COMPARATOR_RE.sub(lambda m: f" {_SYMBOL_TO_WORD[m.group(0)]} ", question.lower())
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
        # The old `(?:is|...)?` here had no boundary of its own, so it
        # consumed "is" as a bare PREFIX of "isn't" (normalised "isn t"),
        # leaving a mangled "n t rejected" tail the comparator loop below
        # could never recognise as a negation -- `(?=\s|$)` requires "is" be
        # a whole word, so "isn t" survives intact for the "ne" match
        # instead. `(?!\s+not)` keeps the two-word negator "is not" intact
        # the same way, so the comparator loop sees "is not", not "not".
        pattern = rf"(?<![a-z0-9]){re.escape(hn)}(?![a-z0-9])\s*(?:is(?=\s|$)(?!\s+not)|=|:|was|of)?\s*"
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

    # Bare value: "how many approved applications" over a status column, or
    # "show all records except rejected" -- a negator governs the bare value
    # here just as it governs an explicit column reference above, within the
    # same bounded word-gap (never across an unrelated clause).
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
            if not re.search(rf"(?<![a-z0-9]){re.escape(dn)}(?![a-z0-9])", qn):
                continue
            negated = bool(re.search(
                rf"\b(?:except|excluding|other than|not|dont|isnt|exclude)\b"
                rf"(?:\s+\w+){{0,4}}?\s+{re.escape(dn)}\b", qn))
            hits.append(Condition(column=h, operator=("ne" if negated else "eq"), value=d))
    # Only when the value is unambiguous — one column, one value.
    if len(hits) == 1:
        return hits
    if len({h.column for h in hits}) == 1 and len(hits) > 1:
        return []          # same column, several values: ambiguous, decline
    return []


def parse_csv_question(question: str, headers: Sequence[str],
                       rows: Sequence[Dict[str, str]],
                       last_row: Optional[int] = None) -> Optional[CsvOperation]:
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

    # Number mapping for text number words
    _WORD_TO_NUM = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "rendu": 2, "moonu": 3, "naalu": 4, "anju": 5,
        "இரண்டு": 2, "மூன்று": 3, "நான்கு": 4, "ஐந்து": 5,
    }
    def _parse_cnt(s: str) -> int:
        s = str(s or "").strip().lower()
        if s.isdigit():
            return int(s)
        return _WORD_TO_NUM.get(s, 5)

    # 1. Explicit single row: "show row 2", "row 4", "வரிசை 2"
    m_row = re.search(r"\brow\s+(\d+)\b|\bவரிசை\s+(\d+)\b", q)
    if m_row:
        rn = int(m_row.group(1) or m_row.group(2))
        return CsvOperation(op=OP_LOOKUP, row_number=rn, row_numbers=[rn])

    # 2. Explicit row range: "rows 1 to 5", "rows 2-6", "வரிசைகள் 1 முதல் 5"
    m_range = re.search(r"\brows?\s+(\d+)\s*(?:to|-)\s*(\d+)\b|\bவரிசைகள்\s+(\d+)\s*முதல்\s*(\d+)\b", q)
    if m_range:
        s_val = int(m_range.group(1) or m_range.group(3))
        e_val = int(m_range.group(2) or m_range.group(4))
        if s_val <= e_val:
            e_clamped = min(len(rows), e_val)
            return CsvOperation(op=OP_LOOKUP, row_number=s_val, row_numbers=list(range(s_val, e_clamped + 1)))

    # 3. Relative range: "next five rows", "next 5 rows", "following ten rows", "adutha 5 rows", "அடுத்த ஐந்து வரிசைகள்"
    m_next_n = re.search(r"\b(?:next|following|adutha|அடுத்த)\s+(\d{1,3}|two|three|four|five|six|seven|eight|nine|ten|rendu|moonu|naalu|anju|இரண்டு|மூன்று|நான்கு|ஐந்து)\s*(?:rows?|records?|வரிசைகள்|வரிசை)?\b", q)
    if m_next_n:
        n_cnt = _parse_cnt(m_next_n.group(1))
        start = (last_row or 0) + 1
        end = min(len(rows), start + n_cnt - 1)
        r_nums = list(range(start, end + 1)) if start <= len(rows) else []
        return CsvOperation(op=OP_LOOKUP, row_number=start, row_numbers=r_nums)

    # 4. Continuation from row X: "continue from row 8"
    m_cont_from = re.search(r"\bcontinue\s+from\s+row\s+(\d+)\b", q)
    if m_cont_from:
        start = int(m_cont_from.group(1))
        end = min(len(rows), start + 4)
        r_nums = list(range(start, end + 1)) if start <= len(rows) else []
        return CsvOperation(op=OP_LOOKUP, row_number=start, row_numbers=r_nums)

    # 5. Relative single row: "next row", "adutha row", "அடுத்த வரிசை", "following row"
    if re.search(r"\b(?:next|following)\s+row\b|\badutha\s+row\b|\bஅடுத்த\s+வரிசை\b", q):
        target = (last_row or 1) + 1
        r_nums = [target] if target <= len(rows) else []
        return CsvOperation(op=OP_LOOKUP, row_number=target, row_numbers=r_nums)

    # 6. Bare continuation on CSV: "continue", "resume", "thodaru", "தொடரவும்", "show the rest", "show the remaining values", "meethi rows kaatu"
    if re.search(r"\b(?:continue|resume|thodaru|thodarunga|show\s+the\s+rest|remaining|meethi)\b|தொடரவும்|மீதி|மீதமுள்ள", q):
        start = (last_row or 1) + 1
        end = min(len(rows), start + 4)
        r_nums = list(range(start, end + 1)) if start <= len(rows) else []
        return CsvOperation(op=OP_LOOKUP, row_number=start, row_numbers=r_nums)

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
        # "show all records except rejected" -- a negated bare-value
        # condition (caught by `_find_conditions`'s fallback path) but no
        # explicit column word in the question, so `column` above never
        # resolved and this fell all the way to `return None`, past the
        # deterministic "computed, never narrated" CSV engine entirely and
        # into the ungrounded retrieval+LLM path, which then silently
        # dropped rows it should have kept. Scoped to a NEGATED condition
        # only: a bare positive match with no trigger verb ("rejected
        # records") still declines below exactly as before this fix, rather
        # than guessing a filter was meant where none was asked for.
        if conds and not column and any(c.operator == "ne" for c in conds):
            return CsvOperation(op=OP_FILTER, column=None, conditions=conds)
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

    if op == OP_FILTER and column is None and not conds:
        # "show me the rows" / "don't show that one" with no resolvable
        # column and no condition names nothing to project -- answering it
        # anyway reads `len(matched)` (an int) as a `list` in
        # `render_csv_result`'s OP_FILTER branch and prints "0 match(es)"
        # for a file that matched every row. Too vague to compute; fall
        # through to the ordinary evidence path instead of a false zero.
        return None

    # The resolved projection column must ride along -- OP_FILTER's own
    # execution only emits per-row values when `operation.column` is set
    # (see `execute()`); dropping it here made every "list the X" / "show
    # me the X" question that matched an explicit op word (as opposed to
    # the bare-projection shortcut above) report "0 match(es)" even when
    # every row matched, because `res.value` then stayed the bare int
    # `len(matched)` and `render_csv_result` only renders a `list` value.
    return CsvOperation(op=op, column=column, conditions=conds)


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


def _sibling_subdivision_column(survey_column: str, headers: Sequence[str]) -> Optional[str]:
    """A survey-number column's own subdivision counterpart, found by header
    NAME (e.g. "synthetic_survey_no" -> "synthetic_subdivision_no"), never
    guessed from column position or row order -- a survey number is never
    returned isolated from its subdivision when the subdivision column is
    right there in the same table. Returns None for any column that is not
    itself survey-number-shaped, or when no subdivision column exists."""
    if not survey_column or "survey" not in survey_column.lower():
        return None
    for h in headers:
        hl = h.lower()
        if h != survey_column and ("subdivision" in hl or "sub_division" in hl or "subdiv" in hl):
            return h
    return None


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
        if operation.row_numbers:
            wants = operation.row_numbers
            if not wants:
                res.ok = False
                res.detail = f"No further rows remain in the file (total data rows: {len(rows)})."
                return res
            hits = []
            for w in wants:
                r = next((row for n, row in numbered if n == w), None)
                if r is not None:
                    hits.append(dict(r, **{"_row": w}))
            if not hits:
                res.ok = False
                res.detail = f"The file has {len(rows)} data rows; requested rows outside range."
                return res
            res.rows = [h["_row"] for h in hits]
            res.samples = hits
            res.value = hits if len(hits) > 1 else hits[0]
            return res
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
            sub_col = _sibling_subdivision_column(operation.column, headers)
            if sub_col:
                # A survey-number column: pair each value with its own row's
                # subdivision value (never another row's), "" meaning
                # genuinely blank in the source -- render_csv_result() turns
                # that into "Not specified in source", never a guess.
                res.value = [(n, (r.get(operation.column) or "").strip(),
                             (r.get(sub_col) or "").strip())
                             for n, r in matched[:20]]
                res.columns = [operation.column, sub_col]
            else:
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
