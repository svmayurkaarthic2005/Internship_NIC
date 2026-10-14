"""Semantic detection of conversational messages (greeting / farewell / thanks /
small talk) by embedding similarity, for phrasings the keyword rules never saw.

It only ever answers "is this a conversational message, and which kind". It does
not route SIS questions: those stay with the deterministic parser, which asks this
module only about short messages that carry no SIS vocabulary and no digits. Any
failure (embedding model down, no prototypes) returns None and the caller carries
on with the ordinary pipeline.
"""
import asyncio
import re
from typing import Dict, List, Optional, Tuple

import numpy as np

from backend.utils.logger import get_logger

logger = get_logger(__name__)

# nomic-embed-text is trained with task prefixes; "classification:" clusters short
# utterances by what they are rather than by their topic words.
_PREFIX = "classification: "

_PROTOTYPES: Dict[str, List[str]] = {
    "greeting": [
        "hello", "hi", "hey there", "hi there", "hey buddy", "good morning",
        "good afternoon", "good evening", "greetings", "hello assistant",
        "vanakkam", "vanakkam sir", "வணக்கம்", "காலை வணக்கம்", "மாலை வணக்கம்",
        "hi da", "hello sir", "namaste", "yo", "hey what's up",
    ],
    "smalltalk": [
        "how are you", "how are you doing today", "how's it going",
        "what's up", "who are you", "are you there", "nalla irukeengala",
        "eppadi irukeenga", "எப்படி இருக்கிறீர்கள்", "நீங்கள் யார்",
        "are you a robot", "can you hear me",
    ],
    "thanks": [
        "thanks", "thank you", "thank you so much", "thanks a lot",
        "many thanks", "appreciate it", "nandri", "romba nandri", "நன்றி",
        "that helps, thanks", "great, thank you", "perfect thanks",
    ],
    "farewell": [
        "bye", "goodbye", "see you later", "see you tomorrow", "good night",
        "take care", "talk to you later", "poitu varen", "போய் வருகிறேன்",
        "i am leaving now", "that is all for now", "signing off",
    ],
}

# What a short SIS message looks like -- so a terse work request is not mistaken
# for chit-chat merely because it is short.
_NEGATIVE: List[str] = [
    "show my pending applications", "how many applications do I have",
    "status of my application", "what is ISD", "field visit schedule",
    "which application is oldest", "who is the applicant", "show overdue files",
    "list my wards", "what is the fee", "what is the survey number",
    "pending list kaattu", "enna status", "எனது விண்ணப்பங்கள்",
    "is there litigation", "show details", "how many field visits",
    "what does this mean", "explain the workflow", "clear my queue",
    "how long does it take", "is it approved", "when will it be done",
    "who approved it", "what is the deadline",
    "after that", "before that", "then what", "and after that", "what next",
    "go on", "continue", "the next one", "and before that", "only those",
    "sort them", "newest first", "date column", "remove that column",
]

_THRESHOLD = 0.75   # minimum cosine to the best conversational prototype
_MARGIN = 0.08      # must beat the best SIS anchor by this much
_MAX_WORDS = 9

_lock = asyncio.Lock()
_matrix: Optional[np.ndarray] = None
_labels: List[str] = []


def _normalise(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n == 0, 1, n)


def _embed(texts: List[str]) -> np.ndarray:
    from backend.services.embeddings import batch_embed
    return _normalise(np.array(batch_embed([_PREFIX + t for t in texts]), dtype=np.float32))


def _build() -> None:
    global _matrix, _labels
    texts: List[str] = []
    labels: List[str] = []
    for kind, phrases in _PROTOTYPES.items():
        texts += phrases
        labels += [kind] * len(phrases)
    texts += _NEGATIVE
    labels += ["sis"] * len(_NEGATIVE)
    _matrix, _labels = _embed(texts), labels


def score(message: str) -> Tuple[Optional[str], float, float]:
    """(best conversational kind, its cosine, best SIS-anchor cosine). Blocking."""
    if _matrix is None:
        _build()
    q = _embed([message])[0]
    sims = _matrix @ q
    best_kind, best = None, -1.0
    sis = -1.0
    for lab, s in zip(_labels, sims):
        if lab == "sis":
            sis = max(sis, float(s))
        elif s > best:
            best_kind, best = lab, float(s)
    return best_kind, best, sis


def looks_short_and_plain(message: str, has_domain_term: bool) -> bool:
    """Cheap gate: only short, digit-free, SIS-vocabulary-free messages are asked."""
    text = (message or "").strip()
    # Tamil-script text embeds as near-identical vectors (every message scores
    # ~1.0 against every anchor), so it is left to the keyword rules.
    if not text or has_domain_term or re.search(r"\d|[஀-௿]", text):
        return False
    return len(text.split()) <= _MAX_WORDS


async def classify(message: str, has_domain_term: bool = False) -> Optional[str]:
    """The conversational kind ("greeting" | "smalltalk" | "thanks" | "farewell")
    of a message, or None when it is not conversational or cannot be decided."""
    if not looks_short_and_plain(message, has_domain_term):
        return None
    try:
        async with _lock:
            if _matrix is None:
                await asyncio.to_thread(_build)
        kind, best, sis = await asyncio.to_thread(score, message)
    except Exception as exc:  # embedding model unavailable, etc.
        logger.warning(f"semantic intent unavailable: {exc}")
        return None
    if kind and best >= _THRESHOLD and best - sis >= _MARGIN:
        logger.info(f"semantic intent: {kind} (cos {best:.2f}, sis {sis:.2f})")
        return kind
    return None
