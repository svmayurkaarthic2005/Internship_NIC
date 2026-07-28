"""
Production-Grade Deterministic Token & Typo Matching Utility

Used STRICTLY for:
- Deterministic identifiers (e.g. ISD, NISD, MERGE)
- Form fields & exact filter codes
- Month extraction & date tokens
- Token-level spelling typo correction (Damerau-Levenshtein distance)

NOT used for natural language intent classification (which is handled semantically via LLM + vector embeddings).
"""

import re
import unicodedata
from typing import Dict, List, Optional, Set, Tuple


def normalize_text(text: str) -> str:
    """Normalize text using Unicode NFC, lowercased, with stripped whitespace."""
    if not text:
        return ""
    # NFC normalization ensures Tamil diacritics and combining marks are standardized
    normalized = unicodedata.normalize("NFC", text.strip())
    return normalized.lower()


def damerau_levenshtein_distance(s1: str, s2: str) -> int:
    """
    Compute Damerau-Levenshtein distance between two normalized strings.
    Handles insertions, deletions, substitutions, and transpositions of adjacent characters.
    """
    s1 = normalize_text(s1)
    s2 = normalize_text(s2)

    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)

    d: Dict[Tuple[int, int], int] = {}
    len1 = len(s1)
    len2 = len(s2)

    for i in range(-1, len1 + 1):
        d[(i, -1)] = i + 1
    for j in range(-1, len2 + 1):
        d[(-1, j)] = j + 1

    for i in range(len1):
        for j in range(len2):
            cost = 0 if s1[i] == s2[j] else 1
            d[(i, j)] = min(
                d[(i - 1, j)] + 1,        # deletion
                d[(i, j - 1)] + 1,        # insertion
                d[(i - 1, j - 1)] + cost,  # substitution
            )
            if i > 0 and j > 0 and s1[i] == s2[j - 1] and s1[i - 1] == s2[j]:
                d[(i, j)] = min(d[(i, j)], d[(i - 2, j - 2)] + cost)  # transposition

    return d[(len1 - 1, len2 - 1)]


def extract_tokens(text: str) -> List[str]:
    """Tokenize text into distinct words, preserving Tamil Unicode characters and alphanumeric codes."""
    if not text:
        return []
    normalized = normalize_text(text)
    # Match contiguous word characters (English alphanumeric + Tamil Unicode block U+0B80 to U+0BFF)
    return re.findall(r"[\w\u0B80-\u0BFF]+", normalized)


def is_token_typo_match(token: str, target: str, max_edits: Optional[int] = None) -> bool:
    """
    Check if a word token matches a target word within allowed edit distance.

    Length-based edit tolerance:
    - len <= 3: 0 edits allowed (exact match only, to avoid false hits on short codes like 'isd', 'mar')
    - 4 <= len <= 7: max 1 edit allowed
    - len >= 8: max 2 edits allowed
    """
    token_norm = normalize_text(token)
    target_norm = normalize_text(target)

    if token_norm == target_norm:
        return True

    target_len = len(target_norm)
    token_len = len(token_norm)

    # Don't match tokens with huge length disparity
    if abs(target_len - token_len) > 2:
        return False

    if max_edits is None:
        if target_len <= 3:
            max_edits = 0
        elif target_len <= 7:
            max_edits = 1
        else:
            max_edits = 2

    if max_edits == 0:
        return token_norm == target_norm

    dist = damerau_levenshtein_distance(token_norm, target_norm)
    return dist <= max_edits


def match_deterministic_code(text: str, code_map: Dict[str, str]) -> Optional[str]:
    """
    Match a deterministic identifier code from a user query.
    Word-boundary strict to prevent substring leakage across tokens.

    Args:
        text: User query string
        code_map: Dictionary mapping variations/lowercase tokens to normalized code string.
                 e.g. {"isd": "ISD", "nisd": "NISD", "merge": "MERGE"}

    Returns:
        The matched code string or None.
    """
    tokens = extract_tokens(text)
    if not tokens:
        return None

    # First pass: Exact token match
    for token in tokens:
        if token in code_map:
            return code_map[token]

    # Second pass: Typo match for tokens >= 4 chars
    for token in tokens:
        for code_key, canonical_value in code_map.items():
            if len(code_key) >= 4 and is_token_typo_match(token, code_key):
                return canonical_value

    return None


# Month mapping for English & Tamil with common spellings
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


def extract_month_from_text(text: str) -> Optional[int]:
    """
    Extract month number (1-12) from text using token-boundary exact or edit-distance matching.
    Prevents substring leakage (e.g., 'smart' does not match 'mar', 'maybe' does not match 'may').
    """
    tokens = extract_tokens(text)
    if not tokens:
        return None

    # 1. Exact token match pass
    for month_num, patterns in MONTH_PATTERNS.items():
        for pattern in patterns:
            pattern_norm = normalize_text(pattern)
            for token in tokens:
                if token == pattern_norm:
                    return month_num

    # 2. Edit-distance typo match pass (only for patterns >= 4 chars)
    for token in tokens:
        if len(token) < 4:
            continue
        for month_num, patterns in MONTH_PATTERNS.items():
            for pattern in patterns:
                pattern_norm = normalize_text(pattern)
                if len(pattern_norm) >= 4 and is_token_typo_match(token, pattern_norm):
                    return month_num

    return None
