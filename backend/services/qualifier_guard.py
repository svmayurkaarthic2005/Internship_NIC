"""A listing request with a word nothing in this system can filter by.

"show applications in xyz", "display sri applications", "how many applications from sri",
"show urgent applications": parse_intent reads the words it knows, ignores the rest, and the
officer is shown the default desk queue as if it answered the whole sentence. The unrecognised
word is named instead, with what the assistant can filter by.

Only a short, list-shaped request is judged, and only words nothing here recognises: every word
of the department's documents, every word the follow-up layer matches, and the words below.
Short words (under 6 letters) must be listed to pass -- the document vocabulary is too broad to
trust for them ("sri", "ram", "xyz" are not in it, and are exactly what a slip looks like).
"""
import re
from typing import List

from functools import lru_cache

from backend.services import followup_context as fctx
from backend.utils.fuzzy import is_token_typo_match

_LIST_START = re.compile(
    r"^\s*(?:please\s+|pls\s+|can\s+you\s+|could\s+you\s+)?(?:show|display|list|give|get|find|see|view|fetch|tell|count|"
    r"how\s+many|what|which|no\.?\s+of|number\s+of|total)\b", re.IGNORECASE)
_TANGLISH_ASK = re.compile(r"\b(?:kaattu|kaami|kaatu|kaattunga|sollu|evlo)\b|காட்டு|காண்பி", re.IGNORECASE)
_NOUN = re.compile(r"\b(?:applications?|aplications?|applicatons?|apps?|files?|cases)\b|விண்ணப்ப", re.IGNORECASE)

# words that are fine in a listing request; anything else short is a suspect
_OK = frozenset("""
a an the of in on at to for from by with without and or nor but not no yes all any every each some my mine me i we us our
your you it its this that these those them they their there here is are was were be been being do does did have has had
show display list give get find see view fetch tell count how many much what which who whom whose when where why
please pls kindly also only just even ever again still already yet now then today yesterday tomorrow tonight
application applications aplication aplications app apps file files case cases record records entry entries item items
pending approved rejected completed complete incomplete open closed active overdue escalated progress inprogress
new old older oldest newer newest latest recent recently earliest first last next previous top bottom highest lowest
isd nisd merge type types status stage stages channel channels source sources csc sro citizen portal registrar sub
ward wards block blocks taluk town district survey surveys number numbers no date dates day days week weeks month months
year years total count counts summary details detail info information report list lists table rows row column columns
name names applicant applicants owner owners mobile phone address fee fees area amount sq sqm
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
between before after since until till during within over under above below than more less most least fewer
day morning afternoon evening night working
assigned jurisdiction my mine officer sis desk queue workload
field visit visits scheduled unscheduled rescheduled
sorted sort order ordered ascending descending asc desc oldest newest
along along add include including exclude excluding except other others rest remaining
up down out off off around about approx approximately
one ones both can code codes row rows second third fourth fifth sixth seventh eighth ninth tenth half
ftom frm fom fro hav hve hv per
aana ana enaku enakku iruku irukku irukka iruka evalo pathi patri la irunthu irundhu oda ku kku ellam ella elam vandha vantha venum pannu panni sollunga kaattunga thevai mattum ah um
igrs tahsildar dsc zdt hqdt patta chitta deed sketch remarks litigation encroachment
here kaattu kaami kaatu sollu enna evlo evvalavu edhu ethu yaar eppo epdi
""".split())
_TA = re.compile(r"[஀-௿]")
_NUMLIKE = re.compile(r"^\d")


def _vocab() -> frozenset:
    """Every word the cue tables of the follow-up layer match on -- deliberate domain words at any length."""
    try:
        return fctx._vocab()
    except Exception:
        return frozenset()


def _known() -> frozenset:
    try:
        return fctx._known_words() | fctx._vocab()
    except Exception:
        return frozenset()


def listing_shaped(message: str) -> bool:
    text = (message or "").strip()
    started = bool(_LIST_START.search(text)) or bool(_TANGLISH_ASK.search(text))
    return bool(text) and len(text.split()) <= 12 and started and bool(_NOUN.search(text))


def unknown_words(message: str) -> List[str]:
    """Words of a list-shaped request that nothing here knows how to filter by."""
    if not listing_shaped(message) or re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", message) or _TA.search(message):
        return []
    known = _known()
    out: List[str] = []
    for tok in re.findall(r"(?<![\dA-Za-z])[A-Za-z][A-Za-z\-']*", message):   # "2nd" is an ordinal, not a word
        for w in [x for x in tok.lower().split("-") if x]:
            _judge(w, known, out)
    return out


@lru_cache(maxsize=2048)
def _slip_of_known(w: str) -> bool:
    """A word of four or more letters within the usual edit budget of a word this system knows
    ("tabel", "secnd") is a slip, and the spelling layer's business. Shorter words get no budget."""
    if len(w) < 4:
        return False
    return any(is_token_typo_match(w, c) for c in (_OK | {k for k in _known() if len(k) >= 5}) if len(c) >= 4)


def _judge(w: str, known, out: list) -> None:
    w = w.strip("'")
    if not w or w in _OK or w in _vocab() or (len(w) >= 6 and w in known):
        return
    try:
        fixed = fctx.correct_spelling(w)          # a slip of a known word is the spelling layer's business
    except Exception:
        fixed = w
    if fixed != w and (fixed in _OK or fixed in known):
        return
    if _slip_of_known(w):
        return
    if w not in out:
        out.append(w)


_VERB_ONLY = re.compile(
    r"^\s*(?:please\s+|pls\s+|can\s+you\s+)?(?:display|show|list|give|get|find|see|view|fetch)\s+(?:me\s+)?(?:all\s+|my\s+)?(?P<rest>.+?)\s*[?.!]*\s*$",
    re.IGNORECASE)
_PERIOD_WORDS = frozenset("""
between from to and before after since until till in on of the for during within last this next previous past prior
week weeks month months year years today yesterday tomorrow day days recent recently
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
pending approved rejected completed overdue escalated progress inprogress isd nisd merge csc sro citizen registrar sub portal
""".split())


def add_noun(message: str, has_history: bool = True) -> str:
    """"display between jan and feb 2025" / "show rejected" name a scope and no noun; asked bare they fell
    to the model, which pasted tool output as raw text. The noun is what they leave out."""
    text = (message or "").strip()
    if not has_history and text and not _VERB_ONLY.match(text):
        from backend.services.rag import is_bare_date_scope   # a period with nothing before it: list that period
        if is_bare_date_scope(text) and not _NOUN.search(text):
            return f"show applications {text.rstrip('?.! ')}"
    m = _VERB_ONLY.match(text)
    if m and not has_history and not _NOUN.search(text) and not _TA.search(text):
        # nothing on screen to refine: "show only rejected", "list first 5", "give all" can only mean a listing
        toks0 = [x.lower() for x in re.findall(r"[A-Za-z]+|\d+", m.group("rest"))]
        loose = _PERIOD_WORDS | {"only", "ones", "first", "top", "latest", "oldest", "newest", "recent", "everything", "all", "my"}
        if toks0 and len(toks0) <= 6 and all(x.isdigit() and len(x) <= 3 or x in loose for x in toks0)                 and any(x in _PERIOD_WORDS - {"and", "to", "in", "on", "of", "the", "for", "from", "between"} or x in loose - {"my", "only", "ones"} or x.isdigit() for x in toks0):
            if toks0 == ["everything"]:
                return re.sub(r"everything", "all applications", text, flags=re.IGNORECASE)
            return f"{text.rstrip('?.! ')} applications"
    if not m or _NOUN.search(text) or re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", text) or _TA.search(text):
        return message
    toks = re.findall(r"[A-Za-z]+|\d+", m.group("rest"))
    if not toks or len(toks) > 9:
        return message
    has_period = any(t2.isdigit() and len(t2) == 4 for t2 in toks) or any(
        t2.lower() in _PERIOD_WORDS and t2.lower() not in ("and", "to", "in", "on", "of", "the", "for", "from", "between")
        for t2 in toks)
    if not has_period or any((not t2.isdigit()) and t2.lower() not in _PERIOD_WORDS for t2 in toks):
        return message
    if all(t2.isdigit() and len(t2) != 4 for t2 in toks):
        return message
    return f"{text.rstrip('?.! ')} applications"


_MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_SHARED_YEAR = re.compile(rf"\b({_MON})\b(?!\s*,?\s*(?:19|20)\d\d)(\s*(?:,|and|to|till|until|through|thru|-|\u2013|&)\s*)({_MON})\s*,?\s*((?:19|20)\d\d)\b",
                          re.IGNORECASE)


def share_year(message: str) -> str:
    """"between jan and feb 2025" -> "between jan 2025 and feb 2025": one year written after the last
    month of a span belongs to both. Without it the first month took the CURRENT year, and the span
    (or a comparison of two separate months) was answered for the wrong period. A bare span
    ("jan - mar 2023") also gets its "between", so it reads as the date scope it is."""
    def _fix(m):
        conn = m.group(2).strip()
        lead = "between " if m.start() == 0 else ""
        joiner = " and " if lead else (" to " if conn in ("-", "\u2013") else m.group(2))
        return f"{lead}{m.group(1)} {m.group(4)}{joiner}{m.group(3)} {m.group(4)}"
    out = _SHARED_YEAR.sub(_fix, message or "")
    return message if out == (message or "") else re.sub(r"\s{2,}", " ", out).strip()


_WHOLE_WORD = re.compile(r"\b(?:full|entire|whole|every)\b|\b(?:complete)(?=\s+list\b)", re.IGNORECASE)
_WHOLE_LIST = re.compile(r"\b(?:full|complete|entire|whole|total)\s+list\s+of\b", re.IGNORECASE)
_REGISTER_PLACE = re.compile(r"\b(?:in|on|present\s+in|available\s+in|existing\s+in|stored\s+in|recorded\s+in)\s+(?:the\s+|my\s+)?"
                             r"(?:sis|system|database|db|register|records?)\b|\bon\s+record\b", re.IGNORECASE)
_LEAD_VERB = re.compile(r"^\s*(?:please\s+|pls\s+|can\s+you\s+)?(?:display|show|list|give|get|find|see|view|fetch)\b|^\s*(?:full|complete|entire)\s+list\b",
                        re.IGNORECASE)


def _noun_token(tok: str) -> bool:
    low = tok.lower()
    return low in ("application", "applications", "aplication", "aplications", "apps", "files") or (
        len(low) >= 6 and is_token_typo_match(low, "applications"))


def whole_register(message: str, has_history: bool = True) -> str:
    """"show full applicatio present in sis", "list every application", "full list of applications",
    "show applications in the database": the whole register, said as "all applications". An unscoped
    "show applications" stays the desk queue; these words ask for everything."""
    text = (message or "").strip()
    if not text or not _LEAD_VERB.match(text) or re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", text) or _TA.search(text):
        return message
    toks = re.findall(r"[A-Za-z]+", text)
    if not any(_noun_token(x) for x in toks):
        if not has_history and re.fullmatch(r"\S+\s+(?:me\s+)?(?:the\s+)?(?:full|complete|entire|whole)\s+list\s*[?.!]*", text, re.IGNORECASE):
            return "show all applications"      # nothing on screen to mean by "the list"
        return message
    wants_all = bool(re.search(r"\b(?:al|alll|aal)\s+\w*applic", text, re.IGNORECASE) or _WHOLE_WORD.search(text) or _WHOLE_LIST.search(text) or _REGISTER_PLACE.search(text))
    if not wants_all:
        return message
    out = _WHOLE_LIST.sub("all ", text)
    out = re.sub(r"\b(?:al|alll|aal)\b(?=\s+\w*applic)", "all", out, flags=re.IGNORECASE)
    out = _WHOLE_WORD.sub("all", out)
    # the noun, spelled properly
    out = re.sub(r"[A-Za-z]+", lambda m: "applications" if (len(m.group(0)) >= 6 and m.group(0).lower() not in ("applications", "application")
                                                            and _noun_token(m.group(0))) else m.group(0), out)
    if not re.search(r"\ball\b", out, re.IGNORECASE):
        out = re.sub(r"\b(applications?)\b", r"all \1", out, count=1, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", out).strip()
