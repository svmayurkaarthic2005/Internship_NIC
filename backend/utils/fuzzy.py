"""
Production-Grade Deterministic Token & Typo Matching Utility

Used STRICTLY for:
- Deterministic identifiers (e.g. ISD, NISD, MERGE)
- Form fields & exact filter codes
- Month extraction & date tokens
- Token-level spelling typo correction (Damerau-Levenshtein / OSA distance)

NOT used for natural language intent classification (which is handled semantically via LLM + vector embeddings).

Design rules (precision first — never guess when confidence is low):
1. Exact token matches always win over typo matches.
2. A typo match must be *unambiguous*: if a token is equally close to two
   candidates that mean different things, no match is returned.
3. Typos preserve the first character (or transpose the first two). This is
   what prevents the ISD / NISD / SD family from colliding.
4. Confusable candidates (two codes within edit tolerance of each other)
   are downgraded to exact-match-only.
"""

import re
import unicodedata
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "normalize_text",
    "damerau_levenshtein_distance",
    "similarity_ratio",
    "extract_tokens",
    "is_token_typo_match",
    "resolve_unique_match",
    "resolve_unique_entry",
    "match_phrase",
    "match_deterministic_code",
    "extract_month_from_text",
    "MONTH_PATTERNS",
]

_TOKEN_RE = re.compile(r"[\w஀-௿]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")
_TAMIL_RE = re.compile(r"[஀-௿]")


# ─────────────────────────────────────────────────────────────────────────────
# Normalization
# ─────────────────────────────────────────────────────────────────────────────
def normalize_text(text: Any) -> str:
    """
    Normalize text using Unicode NFC, lowercased, whitespace-collapsed.

    Accepts anything: None / non-string inputs yield "" instead of raising.
    """
    if not text or not isinstance(text, str):
        return ""
    # NFC normalization ensures Tamil diacritics and combining marks are standardized
    normalized = unicodedata.normalize("NFC", text).strip().lower()
    return _WS_RE.sub(" ", normalized)


def _has_tamil(text: str) -> bool:
    return bool(_TAMIL_RE.search(text))


# ─────────────────────────────────────────────────────────────────────────────
# Edit distance (Optimal String Alignment / restricted Damerau-Levenshtein)
# ─────────────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=16384)
def _osa_distance(s1: str, s2: str, max_distance: int) -> int:
    """
    Bounded OSA distance on already-normalized strings.

    Returns the true distance when it is <= max_distance, otherwise returns
    max_distance + 1 (early exit — the exact value is never needed by callers).
    """
    if s1 == s2:
        return 0
    len1, len2 = len(s1), len(s2)
    if not len1:
        return len2 if len2 <= max_distance else max_distance + 1
    if not len2:
        return len1 if len1 <= max_distance else max_distance + 1
    if abs(len1 - len2) > max_distance:
        return max_distance + 1

    prev2: Optional[List[int]] = None
    prev: List[int] = list(range(len2 + 1))

    for i in range(1, len1 + 1):
        cur = [i] + [0] * len2
        row_min = i
        c1 = s1[i - 1]
        for j in range(1, len2 + 1):
            cost = 0 if c1 == s2[j - 1] else 1
            val = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and c1 == s2[j - 2] and s1[i - 2] == s2[j - 1]:
                val = min(val, prev2[j - 2] + cost)  # transposition
            cur[j] = val
            if val < row_min:
                row_min = val
        if row_min > max_distance:
            return max_distance + 1
        prev2, prev = prev, cur

    dist = prev[len2]
    return dist if dist <= max_distance else max_distance + 1


def damerau_levenshtein_distance(s1: Any, s2: Any, max_distance: Optional[int] = None) -> int:
    """
    Compute Damerau-Levenshtein (OSA) distance between two strings.
    Handles insertions, deletions, substitutions, and transpositions of adjacent characters.

    Inputs are normalized first. When `max_distance` is given, the search stops
    early and `max_distance + 1` is returned for anything further apart.
    """
    a = normalize_text(s1)
    b = normalize_text(s2)
    if a == b:
        return 0
    bound = max(len(a), len(b)) if max_distance is None else max(0, int(max_distance))
    return _osa_distance(a, b, bound)


def similarity_ratio(s1: Any, s2: Any) -> float:
    """Normalized similarity in [0.0, 1.0] — 1.0 means identical."""
    a = normalize_text(s1)
    b = normalize_text(s2)
    if not a and not b:
        return 0.0
    longest = max(len(a), len(b))
    if longest == 0:
        return 0.0
    return 1.0 - (damerau_levenshtein_distance(a, b) / longest)


# ─────────────────────────────────────────────────────────────────────────────
# Tokenization
# ─────────────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=4096)
def _tokenize_cached(normalized: str) -> Tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(normalized))


def extract_tokens(text: Any) -> List[str]:
    """Tokenize text into words, preserving Tamil Unicode characters and alphanumeric codes."""
    normalized = normalize_text(text)
    if not normalized:
        return []
    return list(_tokenize_cached(normalized))


_INFLECTION_MIN_LEN = 4


def _token_matches_part(token: str, part: str, allow_inflection: bool) -> bool:
    """
    A phrase token matches when it is identical, or (for stems of at least
    _INFLECTION_MIN_LEN characters) when the message word merely adds a suffix.

    This covers English plurals ("field visit" in "field visits") and Tamil case
    endings ("கள ஆய்வு" in "கள ஆய்வுக்கு") without reopening substring leakage:
    the match is still anchored at the start of a whole token.
    """
    if token == part:
        return True
    if not allow_inflection or len(part) < _INFLECTION_MIN_LEN:
        return False
    return token.startswith(part)


def match_phrase(tokens: Sequence[str], phrase: Any, allow_inflection: bool = True) -> bool:
    """
    True when the `phrase` occurs as a contiguous token run in `tokens`.

    Word-boundary strict and separator-agnostic, so the keyword "sub-division"
    matches "sub division", "sub-division" and "sub  division" alike, but never
    a stray "division" on its own.
    """
    parts = extract_tokens(phrase)
    if not parts or not tokens:
        return False
    span = len(parts)
    limit = len(tokens) - span
    if limit < 0:
        return False
    first = parts[0]
    for i in range(limit + 1):
        if not _token_matches_part(tokens[i], first, allow_inflection):
            continue
        if all(
            _token_matches_part(tokens[i + k], parts[k], allow_inflection)
            for k in range(1, span)
        ):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Typo matching
# ─────────────────────────────────────────────────────────────────────────────
def _max_edits_for(target: str) -> int:
    """
    Length-based edit tolerance:
      len <= 3 : 0 edits (exact only — protects short codes like 'isd', 'mar')
      4 <= len <= 7 : 1 edit
      len >= 8 : 2 edits
    """
    n = len(target)
    if n <= 3:
        return 0
    if n <= 7:
        return 1
    return 2


def _typo_distance_norm(
    token: str,
    target: str,
    budget: int,
    min_ratio: Optional[float] = None,
) -> Optional[int]:
    """
    Edit distance between two ALREADY-NORMALIZED tokens, or None when they are
    not a legitimate typo pair. Guards are ordered cheapest-first because this
    runs once per (message token × keyword).

    Precision guards beyond raw edit distance:
    - the length gap may not exceed the edit budget
    - the first character must survive the typo (or be a leading transposition)
    - Tamil script requires an exact match (single glyphs carry meaning, so a
      one-edit budget is far too loose)
    - optional `min_ratio` similarity floor
    """
    if token == target:
        return 0
    if budget <= 0 or not token or not target:
        return None

    len_t, len_g = len(token), len(target)
    if abs(len_t - len_g) > budget:
        return None

    # First character must survive the typo, or be a leading transposition.
    # This is what stops 'isd'→'nisd', 'sd'→'isd', 'late'→'date'.
    if token[0] != target[0]:
        if not (len_t > 1 and len_g > 1 and token[0] == target[1] and token[1] == target[0]):
            return None

    if _has_tamil(target) or _has_tamil(token):
        return None

    dist = _osa_distance(token, target, budget)
    if dist > budget:
        return None
    if min_ratio is not None and (1.0 - dist / max(len_t, len_g)) < min_ratio:
        return None
    return dist


def is_token_typo_match(
    token: Any,
    target: Any,
    max_edits: Optional[int] = None,
    min_ratio: Optional[float] = None,
) -> bool:
    """
    Check if a word token matches a target word within allowed edit distance.
    Empty / None / non-string inputs never match.
    """
    token_norm = normalize_text(token)
    target_norm = normalize_text(target)
    if not token_norm or not target_norm:
        return False
    budget = _max_edits_for(target_norm) if max_edits is None else max(0, int(max_edits))
    return _typo_distance_norm(token_norm, target_norm, budget, min_ratio) is not None


def resolve_unique_entry(
    token: Any,
    candidate_map: Dict[str, Any],
    max_edits: Optional[int] = None,
    min_ratio: Optional[float] = None,
    min_length: int = 4,
    keys_normalized: bool = False,
) -> Optional[Tuple[str, Any]]:
    """
    Resolve a token against {candidate_key: canonical_value}, preferring exact
    matches and refusing to guess when a typo match is ambiguous.

    Returns (matched_key, canonical_value), or None when there is no confident match:
    - exact key hit → that entry
    - typo hits at the same best distance that all agree on one canonical value → that entry
    - typo hits spanning two or more different canonical values → None (do not guess)

    Set `keys_normalized=True` when the caller already normalized the keys — it
    skips a per-key normalization on a hot path.
    """
    token_norm = normalize_text(token)
    if not token_norm or not candidate_map:
        return None

    if token_norm in candidate_map:
        return (token_norm, candidate_map[token_norm])

    best_dist: Optional[int] = None
    best: Optional[Tuple[str, Any]] = None
    ambiguous = False

    for key, value in candidate_map.items():
        key_norm = key if keys_normalized else normalize_text(key)
        if len(key_norm) < min_length:
            continue  # short codes are exact-match only
        budget = _max_edits_for(key_norm) if max_edits is None else max(0, int(max_edits))
        dist = _typo_distance_norm(token_norm, key_norm, budget, min_ratio)
        if dist is None:
            continue
        if best_dist is None or dist < best_dist:
            best_dist, best, ambiguous = dist, (key_norm, value), False
        elif dist == best_dist and best is not None and value != best[1]:
            ambiguous = True

    if best is None or ambiguous:
        return None
    return best


def resolve_unique_match(
    token: Any,
    candidate_map: Dict[str, Any],
    max_edits: Optional[int] = None,
    min_ratio: Optional[float] = None,
    min_length: int = 4,
    keys_normalized: bool = False,
) -> Optional[Any]:
    """Canonical value of `resolve_unique_entry`, or None when not confident."""
    entry = resolve_unique_entry(
        token, candidate_map, max_edits=max_edits, min_ratio=min_ratio,
        min_length=min_length, keys_normalized=keys_normalized,
    )
    return entry[1] if entry else None


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic identifier codes (ISD / NISD / MERGE …)
# ─────────────────────────────────────────────────────────────────────────────
def _confusable_keys(code_map: Dict[str, str]) -> frozenset:
    """
    Keys that sit within their own edit tolerance of another key meaning
    something else (e.g. 'isd' vs 'nisd'). Those require an exact match.
    """
    items = [(normalize_text(k), v) for k, v in code_map.items()]
    confusable = set()
    for i, (k1, v1) in enumerate(items):
        for k2, v2 in items[i + 1:]:
            if v1 == v2 or not k1 or not k2:
                continue
            budget = max(_max_edits_for(k1), _max_edits_for(k2))
            if budget and _osa_distance(k1, k2, budget) <= budget:
                confusable.add(k1)
                confusable.add(k2)
    return frozenset(confusable)


def match_deterministic_code(text: Any, code_map: Dict[str, str]) -> Optional[str]:
    """
    Match a deterministic identifier code from a user query.
    Word-boundary strict, exact-first, and ambiguity-safe.

    Args:
        text: User query string
        code_map: Dictionary mapping variations/lowercase tokens to normalized code string.
                 e.g. {"isd": "ISD", "nisd": "NISD", "merge": "MERGE"}

    Returns:
        The matched code string, or None when nothing matches confidently.
    """
    tokens = extract_tokens(text)
    if not tokens or not code_map:
        return None

    normalized_map = {normalize_text(k): v for k, v in code_map.items() if normalize_text(k)}
    if not normalized_map:
        return None

    # Pass 1: exact token match (earliest token in the message wins)
    for token in tokens:
        if token in normalized_map:
            return normalized_map[token]

    # Pass 2: typo match, excluding confusable families and ambiguous hits
    confusable = _confusable_keys(normalized_map)
    typo_map = {k: v for k, v in normalized_map.items() if k not in confusable}
    if not typo_map:
        return None

    for token in tokens:
        if token.isdigit():
            continue
        resolved = resolve_unique_match(token, typo_map, keys_normalized=True)
        if resolved is not None:
            return resolved

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Month extraction
# ─────────────────────────────────────────────────────────────────────────────
MONTH_PATTERNS: Dict[int, List[str]] = {
    1: ["january", "jan", "ஜனவரி", "januvary", "janaury", "jenuary"],
    2: ["february", "feb", "பிப்ரவரி", "feburary", "febuary", "februry"],
    3: ["march", "mar", "மார்ச்", "மார்ச", "marh"],
    4: ["april", "apr", "ஏப்ரல்", "aprill", "aprl"],
    5: ["may", "மே", "மேமாதம்"],
    6: ["june", "jun", "ஜூன்", "joon", "juun"],
    7: ["july", "jul", "ஜூலை", "jully", "julai"],
    8: ["august", "aug", "ஆகஸ்ட்", "agust", "augest"],
    9: ["september", "sep", "sept", "செப்டம்பர்", "septembar", "septmber"],
    10: ["october", "oct", "அக்டோபர்", "octobr", "octobar"],
    11: ["november", "nov", "நவம்பர்", "novembar", "novembr"],
    12: ["december", "dec", "டிசம்பர்", "decembr", "desember"],
}

# pattern → month, built once (exact lookup is O(1) instead of O(12 × patterns))
_MONTH_LOOKUP: Dict[str, int] = {}
for _m, _pats in MONTH_PATTERNS.items():
    for _p in _pats:
        _MONTH_LOOKUP[normalize_text(_p)] = _m

# Patterns eligible for typo matching: canonical full names only.
# Applying an edit budget on top of an already-misspelled variant compounds the
# error — 'mark' is one edit from the variant 'marh', but two from 'march', and
# only the latter tolerance is defensible.
_MONTH_TYPO_LOOKUP: Dict[str, int] = {
    normalize_text(pats[0]): m
    for m, pats in MONTH_PATTERNS.items()
    if len(normalize_text(pats[0])) >= 4 and not _has_tamil(normalize_text(pats[0]))
}

# Domain vocabulary that must never be read as a month
_DOMAIN_STOPWORDS = frozenset({
    "app", "appl", "apps", "application", "applications", "applicant", "applicants",
    "survey", "surveys", "subdivision", "subdivisions", "block", "blocks",
    "ward", "wards", "town", "towns", "taluk", "taluks", "district", "districts",
    "merge", "merged", "merging", "isd", "nisd", "status", "pending", "approved",
    "rejected", "escalated", "owner", "owners", "detail", "details",
})

# Tokens that are ordinary English/Tamil words as often as they are months.
# They only count as a month when a date cue sits next to them.
_AMBIGUOUS_MONTH_TOKENS = frozenset({"may", "மே"})
_MONTH_CUE_WORDS = frozenset({
    "in", "of", "on", "during", "for", "from", "to", "by", "since", "until", "till",
    "between", "month", "months", "monthly", "submitted", "filed", "received",
    # The nouns an officer asks about. "may applications" / "may visits" is the
    # month — the modal reading of "may" is followed by a verb ("may i see",
    # "may be"), never by one of these, so admitting them costs no precision.
    "application", "applications", "app", "apps", "appl", "file", "files",
    "visit", "visits", "report", "reports", "list", "summary", "count",
    "மாதம்", "மாதத்தில்", "மாத", "விண்ணப்பம்", "விண்ணப்பங்கள்",
})
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def _month_cue_present(tokens: Sequence[str], idx: int) -> bool:
    """True when a date cue neighbours tokens[idx] (e.g. 'in may', 'may 2026')."""
    prev_tok = tokens[idx - 1] if idx > 0 else ""
    next_tok = tokens[idx + 1] if idx + 1 < len(tokens) else ""
    if prev_tok in _MONTH_CUE_WORDS or next_tok in _MONTH_CUE_WORDS:
        return True
    if _YEAR_RE.match(prev_tok or "") or _YEAR_RE.match(next_tok or ""):
        return True
    return False


def extract_month_from_text(text: Any) -> Optional[int]:
    """
    Extract month number (1-12) from text using token-boundary exact or
    edit-distance matching, scanning left to right so the first month mentioned
    wins ("december and january" → 12).

    Precision guards:
    - substring leakage is impossible ('smart' never matches 'mar')
    - domain vocabulary ('application', 'approved', …) is excluded
    - ambiguous words ('may') require a neighbouring date cue
    - a typo that is equally close to two different months resolves to None
    """
    tokens = extract_tokens(text)
    if not tokens:
        return None

    # Pass 1: exact token match, in message order
    for idx, token in enumerate(tokens):
        if token in _DOMAIN_STOPWORDS or token.isdigit():
            continue
        month = _MONTH_LOOKUP.get(token)
        if month is None:
            continue
        if token in _AMBIGUOUS_MONTH_TOKENS and not _month_cue_present(tokens, idx):
            continue
        return month

    # Pass 2: unambiguous typo match, in message order
    for token in tokens:
        if len(token) < 4 or token in _DOMAIN_STOPWORDS or token.isdigit():
            continue
        if token.startswith("app"):
            continue
        month = resolve_unique_match(token, _MONTH_TYPO_LOOKUP, keys_normalized=True)
        if month is not None:
            return month

    return None
