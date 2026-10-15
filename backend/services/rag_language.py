"""
Language detection for the SIS chatbot.

Owns: Tamil / Tanglish / English detection, explicit "answer in Tamil"
request parsing (with negation awareness), short-reply (yes/no/filler)
classification, and recovering the active language from recent chat history.

Extracted from backend/services/rag.py (SIS_Chatbot_Modularity_Refactor,
Phase 3) with no change in parsing semantics -- every regex, constant and
threshold below is copied verbatim from the original module. rag.py
re-exports these names so existing imports (`from backend.services.rag
import detect_language, ...`) keep working unchanged.
"""

from typing import Optional
import re

from backend.utils.fuzzy import is_token_typo_match, normalize_text

# Common Romanized Tamil (Tanglish) tokens and spelling variants. Keep this broad
# enough for real conversational wording, but do NOT use every entry as a fuzzy
# target: several valid English words (for example "India") are close to common
# Tanglish words ("indha"). Exact lexical evidence is safe; typo matching below is
# limited to distinctive forms.
_TANGLISH_WORDS = frozenset("""
    enna yenna ennanu ennanga ennathu ennaachu ennachu enapa ennapa
    eppadi epdi eppidi eppadiya epdiya eppo epo eppavum epovum appo ippa ippo
    endha entha enda yentha yendha edhu ethu edha etha edhuku ethuku edhukku ethukku yethuku
    enga engae enge engay yenga yengae yenge yengay inga ingae inge ingay ingu ingaye inki
    yaaru yaru yaar yarru evlo evalo ivlo avlo evvalavu evalavu ethana ethanai ethane
    vanakkam vanakam nandri nanri
    enakku enaku ennaku ennoda enoda yenoda unakku unaku unnaku unnoda unga ungaloda
    naan naanga nanga neenga nenga namma nammaku avanga ivanga avan ava avaru
    indha intha inda andha antha idhu ithu idha itha adhu athu atha athula ithula
    irukku iruka irukka irukaa irukkuma irukuma irukkanga irukanga irukingala irukiya
    irundha iruntha irundhucha illa illai illaya illaiya illena illana illaama
    venum venuma venduma venam vendam venda thevai thevaiya thevaya
    aama ama amaam seri sari romba nalla nallaa konjam puriyala theriyala theriyum theriyanum
    mudiyuma mudiyathu mudiyadhu varum vanthu vandha vanga vaanga ponga pogum poga
    kudunga kudu kudutha kuduthu sollunga solunga sollu solla sollitu
    pannunga panunga pannu panu pannala pannina panra pannra pannitu pannidu pannalam
    kaatu kaattu kaatunga kaami kaaminga kaamikka tharunga tharu kodu
    appuram aparum aprom apram apdi appadi ippadi apdina aachu aaguma
    ellam ellaam ellaa ellathayum mothama muzhu kooda kitta
    panichumai velaichumai kaalathaamadham niluvai niluvaiyil utpirivu kanakku
    innaiku innaikku inniki inniku innaiki innakki inraiku netru naal nal kizhamai kilamai thethi
    panradhu pannradhu seiradhu seyyarathu
    panna thirumba kedaikkum kedaikuma kidaikkum poidum poiduma poyidum azhiyum aagum aagumaa
""".split())

# Only these distinctive words may trigger typo-based Tanglish recognition.
# Common near-neighbours like "India", "today", "date", and "status" are
# intentionally excluded, so English input is not accidentally reclassified.
_TANGLISH_TYPO_WORDS = (
    "evvalavu", "sollunga", "pannunga", "irukkanga", "vanakkam",
    "ennoda", "enakku", "edhukku", "eppadi", "puriyala", "theriyala",
    "kaatunga", "kudunga", "thevaiya", "venduma", "irukku", "illaya",
    "ennanu", "pannalam", "pannidu", "eppavum", "yengae",
)


# ─────────────────────────────────────────────────────────────────────────────
# detect_language
#   - Default to "tanglish" (no pgvector filter) when content is mixed.
#   - Pure-Tamil threshold at 50% so "Survey 145 எங்கே உள்ளது?"
#     is correctly classified as tanglish, not ta.
# ─────────────────────────────────────────────────────────────────────────────
#   detect_language()'s own clause-aware negation check for an explicit
# Tamil-language REQUEST ("explain in Tamil"), not to be confused with the
# differently-scoped `_is_format_negation_request()` in chatbot.py (brevity/
# detail format, a different axis entirely -- "don't explain in sentence" is
# not a language instruction). `chatbot.py` imports from this module, so this
# module cannot import back from `chatbot.py` without a circular import; the
# same clause-split + negator-before-cue shape is reimplemented locally
# instead, deliberately kept this small rather than factored out, since
# duplicating ~6 lines of regex is cheaper than a shared-utils module for one
# caller on each side.
#
# "Don't explain in Tamil" / "dont explain in tamil" / "Do not answer in
# Tamil" / "Never respond in Tamil" / "Please don't translate this into
# Tamil" all satisfied the OLD bare `_tamil_request` match below via the verb/
# preposition sitting directly in front of "Tamil" -- structurally identical
# to the genuine positive phrasing the pattern exists to catch, since that
# pattern had no negation awareness at all. Confirmed live through the
# rendered chatbot UI (QA audit, 2026-10-13): the officer explicitly asked
# NOT to be answered in Tamil and received a full Tamil-script answer anyway.
#
# A negator governs a clause's Tamil mention only when it sits in the SAME
# CLAUSE and BEFORE it -- "Explain in Tamil, but don't give examples" must
# still select Tamil (the negation is about examples, in a different clause
# split off by "but"). Clauses are read in order and the LAST one that
# actually decides anything (positive or negated) wins, so "Explain in Tamil.
# Actually, don't use Tamil; English only." correctly ends up English -- the
# newest clear instruction, not just whichever clause happens to come first.
_LANG_CLAUSE_SPLIT_RE = re.compile(r'[;.!?]|\b(?:but|however|although|though)\b|ஆனால்', re.IGNORECASE)
_LANG_NEGATOR_RE_PART = r"(?:don'?t|do\s+not|dont|never|not|no)"
_TAMIL_REQUEST_RE = re.compile(
    r'\b(?:in|into|reply|answer|respond|speak|say|translate|write)\b'
    r'[^.?!]{0,15}\btami[lz]h?\b'
    r'|\btami[lz]h?\s*(?:il|la|le)\b',
    re.IGNORECASE,
)
# A negator followed, within a few words, by the bare word "tamil" --
# deliberately NOT tied to the verb-shaped cue above, so "don't **use**
# Tamil" / "No Tamil, English only" (no recognised request verb at all) are
# still caught, not just the exact verb phrasing `_TAMIL_REQUEST_RE` matches.
_TAMIL_NEGATED_MENTION_RE = re.compile(
    rf"\b{_LANG_NEGATOR_RE_PART}\b(?:\s+\w+){{0,6}}?\s+tami[lz]h?\b", re.IGNORECASE)
# The Tamil-script equivalent: a Tamil negation particle (வேண்டாம் "don't
# want" / இல்லை "no" / கூடாது "must not") in the same clause as "தமிழில்"/
# "தமிழ்ல" negates it, symmetric with the English/Tanglish handling above.
_TAMIL_SCRIPT_NEGATOR = ("வேண்டாம்", "இல்லை", "கூடாது")
# A shorthand reply -- the whole clause is nothing but the bare word "tamil"
# (plus throwaway politeness like "please"/"only") -- names no verb for
# `_TAMIL_REQUEST_RE` to match, e.g. a one-word reply "tamil" typed after a
# listing to switch the next answer's language. "Tamil Nadu" is already
# dropped before this runs (see `detect_language`), so this never matches a
# place-name fragment; any OTHER real word in the clause keeps it out.
_LANG_SHORTHAND_FILLER = frozenset({"please", "only", "now", "pls", "kindly"})


def _explicit_tamil_request(lowered: str, original: str) -> bool:
    """True when `lowered`/`original` ends on a genuine, UN-negated request
    for a Tamil-script answer. Clauses are read left to right; each clause
    that mentions "tamil" at all updates a running verdict (True = positive
    request, False = negated), and a clause with no mention leaves the
    running verdict as it was -- so the LAST decisive clause wins, matching
    how an officer's own corrections ("actually, don't...") are meant to be
    read, while a clause negating something else entirely never touches it."""
    verdict = False
    for clause in _LANG_CLAUSE_SPLIT_RE.split(lowered):
        bare_tokens = [t for t in re.findall(r"[a-z]+", clause) if t not in _LANG_SHORTHAND_FILLER]
        if _TAMIL_NEGATED_MENTION_RE.search(clause):
            verdict = False
        elif bare_tokens in (["tamil"], ["tamizh"]) or _TAMIL_REQUEST_RE.search(clause):
            verdict = True
    for clause in _LANG_CLAUSE_SPLIT_RE.split(original):
        has_mention = 'தமிழில்' in clause or 'தமிழ்ல' in clause
        if not has_mention:
            continue
        verdict = not any(neg in clause for neg in _TAMIL_SCRIPT_NEGATOR)
    return verdict


def detect_language(text: str) -> str:
    """
    Detect language of input text using heuristics.

    Returns:
        "ta"        — predominantly Tamil (>50 % Tamil chars) or explicit Tamil request
        "tanglish"  — mixed Tamil + English, Tamil chars present, or Romanized Tanglish keywords
        "en"        — pure English / no Tamil
    """
    if not text:
        return "en"

    # Explicit request for Tamil output in English or Tamil script.
    # The bare word "tamil" is NOT enough — "Tamil Nadu" appears in ordinary
    # English queries (district/taluk names) and must not flip the whole
    # response to Tamil. Require a request verb/preposition around it.
    # Drop the place name first so "applications in Tamil Nadu" stays English.
    # A NEGATED request ("don't explain in Tamil") must not flip it either --
    # see `_explicit_tamil_request()` above.
    lowered = re.sub(r'\btamil\s*nadu\b|\btn\s+state\b', ' ', text.lower())
    if _explicit_tamil_request(lowered, text):
        return "ta"

    # The percentage is of SCRIPT characters only -- digits, "/" and
    # punctuation must not dilute it. An application number is mostly digits,
    # so "2026/0154/28/000950 நிலை என்ன?" (pure Tamil but for the number) used
    # to compute Tamil-chars / ALL-chars, land under 50% and answer in
    # Tanglish (romanized script) instead of Tamil script -- the one part of
    # the message that was never in question (the officer's own words) came
    # back in the wrong writing system.
    tamil_chars = len(re.findall(r'[஀-௿]', text))
    latin_chars = len(re.findall(r'[A-Za-z]', text))
    script_chars = tamil_chars + latin_chars

    if script_chars == 0:
        return "en"

    tamil_pct = (tamil_chars / script_chars) * 100

    if tamil_pct > 50:
        return "ta"
    elif tamil_chars > 0:
        # Any Tamil script present alongside English → tanglish
        return "tanglish"

    # Romanized Tamil has many regional and personal spellings. Exact common
    # function words ("endha", "inga", "enakku", "illaya", etc.) are the
    # strongest signal. Normalize whitespace/case first, then use typo matching
    # only against distinctive words so a normal English sentence is not labelled
    # Tanglish just because it contains a near-neighbour such as "India".
    words = set(re.findall(r"\b[a-zA-Z]+\b", normalize_text(text)))
    if words & _TANGLISH_WORDS:
        return "tanglish"
    for word in words:
        if len(word) >= 4 and any(
            is_token_typo_match(word, target)
            for target in _TANGLISH_TYPO_WORDS
        ):
            return "tanglish"

    return "en"


_SHORT_REPLY_WORDS = frozenset({
    "no", "nope", "nah", "never", "not", "nvm", "never mind",
    "illa", "illai", "illana", "illaye", "apdi illa", "appadi illa", "athuvum illa", "adhu illa",
    "இல்லை", "இல்ல", "அப்படி இல்லை", "இல்ல பரவாயில்ல",
    "yes", "yeah", "yep", "yup", "ya", "yaa", "ok", "okay", "sure", "fine",
    "correct", "exactly", "true", "right",
    "aama", "ama", "aamam", "seri", "sari", "apdi dhaan", "appadi dhaan",
    "ஆம்", "ஆமா", "சரி", "சரிதான்",
})


def is_short_reply(message: str) -> bool:
    """True if message is a short confirmation, denial, or filler reply."""
    cleaned = re.sub(r"[^\w\s]", "", (message or "").strip().lower())
    words = cleaned.split()
    if not words or len(words) > 5:
        return False
    if cleaned in _SHORT_REPLY_WORDS:
        return True
    return all(w in _SHORT_REPLY_WORDS or w in {"da", "pa", "bro", "sir", "please", "la", "ah"} for w in words)


def get_prior_turn_language(chat_history: Optional[list]) -> Optional[str]:
    """Inspect previous turns in chat_history to find the active language."""
    if not chat_history:
        return None
    for turn in reversed(chat_history):
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        if turn.get("role") == "user":
            lang = detect_language(content)
            if lang in ("ta", "tanglish"):
                return lang
            elif lang == "en":
                return "en"
    for turn in reversed(chat_history):
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        if re.search(r"[஀-௿]", content):
            return "ta"
    return None
