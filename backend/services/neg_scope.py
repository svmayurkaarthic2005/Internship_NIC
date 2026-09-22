"""Negation over application lists: "not pending", "except ISD and rejected",
"neither approved nor rejected", "approved but not from CSC", "ISD vendam",
"நிலுவையில் இல்லாத".

parse_intent reads ONE status / type / channel, so a negated word used to be answered
with the very list the officer excluded ("I don't want NISD" -> the NISD list), and a
second exclusion in the same breath (or in the next message) was lost. The exclusions
are taken out of the message here, the rest is asked as an ordinary positive question,
and the rows that come back are filtered. Nothing is counted from text: every figure
is the length of the filtered database rows.
"""
import contextvars
import re
from typing import Dict, List, Optional, Set, Tuple

EXCLUDE: contextvars.ContextVar = contextvars.ContextVar("app_exclude", default=None)

_TA = "[஀-௿]"
_TERM = (rf"(?:nisd|nsid|isd|merge|pending|approved|completed|rejected|in[\s-]?progress|escalated|"
         rf"csc|citizen|sub[\s-]?registrar|sro|ward\s*\d{{1,3}}|வார்டு\s*\d{{1,3}}|"
         rf"அங்கீகரி{_TA}*|ஒப்புத{_TA}*|நிராகரி{_TA}*|மறுக்கப்{_TA}*|நிலுவை{_TA}*|செயல்பாட்டில்)")
# these cues negate every item in the list that follows ("except A and B", "neither A nor B")
_CUE_ALL = (r"(?:except|excluding|exclude|without|other\s+than|apart\s+from|besides|minus|everything\s+but|"
            r"all\s+but|anything\s+but|neither|(?:i\s+)?(?:don'?t|dont|do\s+not)\s+(?:want|need|like)|"
            r"(?:don'?t|dont|do\s+not|never)\s+(?:show|list|display|include|give)(?:\s+me)?|"
            r"hide|skip|ignore|drop|remove|leave\s+out)")
# a bare "not" negates one item; "not A and B" leaves B alone, "not A or B" / "not A, not B" does not
_CUE_ONE = r"(?:but\s+not|not|non[\s-]|(?:^|[,;]\s*)no(?!\.|\s+of\b))"
_ITEM = (rf"(?:the\s+|any\s+|all\s+|from\s+|via\s+|through\s+|in\s+|under\s+)*(?:{_TERM})"
         rf"(?:\s+(?:ones?|applications?|apps?|files|cases))*")
_GROUP = re.compile(
    rf"(?<!\w)(?:(?P<all>{_CUE_ALL})|(?P<one>{_CUE_ONE}))\s*(?P<first>{_ITEM})"
    rf"(?P<rest>(?:\s*(?:,|and|or|nor|&|/)\s*(?:(?:{_CUE_ALL}|{_CUE_ONE})\s*)?{_ITEM})*)", re.IGNORECASE)
_SUFFIX = re.compile(
    rf"(?<!\w)(?P<t>{_TERM})(?:\s+(?:ones?|applications?|apps?))*\s*"
    rf"(?:-?\s*(?:um|யும்|ம்)\s*)?"
    rf"(?:illama|illamal|illatha|illaatha|allatha|alla|vendam|venda|thavira|thavirthu|ozhichu|illai|illa|"
    rf"இல்லாத{_TA}*|இல்லாமல்|இல்லை|அல்லாத{_TA}*|தவிர{_TA}*|வேண்டாம்)(?![a-z])", re.IGNORECASE)
_SEP = re.compile(r"\s*(,|and|or|nor|&|/)\s*(?:(?:" + _CUE_ALL + "|" + _CUE_ONE + r")\s*)?", re.IGNORECASE)

_STATUS_TA = (("அங்கீகரி", "approved"), ("ஒப்புத", "approved"), ("நிராகரி", "rejected"),
              ("மறுக்கப்", "rejected"), ("நிலுவை", "pending"), ("செயல்பாட்டில்", "in_progress"))
_STATUS_EN = {"pending": "pending", "approved": "approved", "completed": "approved", "rejected": "rejected",
              "escalated": "escalated"}
_LABEL_TA = {"pending": "நிலுவையில் உள்ள", "approved": "அங்கீகரிக்கப்பட்ட", "rejected": "நிராகரிக்கப்பட்ட",
             "in_progress": "செயல்பாட்டில் உள்ள", "escalated": "உயர்நிலைக்கு அனுப்பப்பட்ட",
             "CSC": "CSC", "citizen": "குடிமகன்", "sub_registrar": "சார்-பதிவாளர்"}
_FILLER = re.compile(
    r"\b(?:either|too|also|as\s+well|then|now|please|pls|show|list|display|give|me|them|those|these|ones?|the|and|"
    r"but|only|just|rest|others?|remaining|kaattu|kaami|kaatu|mattum|mattham|matravai)\b|மற்றவை|மீதி|மற்ற|மட்டும்|காட்டு",
    re.IGNORECASE)
_NOUN = re.compile(r"\b(?:applications?|apps?|files|cases)\b|விண்ணப்ப", re.IGNORECASE)
_DANGLING = re.compile(r"(?:^|\s)(?:and|but|or|nor|that|which|who|are|is|were|be|do|does|have|has|only|just|also|"
                       r"all|in|from|the|of|my|me|i|want|need)\s*[,.?!]*\s*$", re.IGNORECASE)
_SURVEY = re.compile(r"(?:\b(?:in|on|of|for|under|at|from)\s+)?\bsurvey(?:\s+(?:no\.?|number|num))?\s*[:#]?\s*(\d+(?:/[A-Za-z0-9]+)?)", re.IGNORECASE)
_APP_NO = re.compile(r"\d{4}/\d{3,4}/\d{1,3}/\d+")
_VISIT = re.compile(r"\bvisits?\b|கள\s*ஆய்வு", re.IGNORECASE)
_NO_VISIT = re.compile(
    r"(?<!\w)(?:without|with\s+no|having\s+no|lacking|not\s+having|(?:don'?t|do\s+not|doesn'?t|does\s+not)\s+have|"
    r"which\s+have\s+no|that\s+have\s+no)\s+(?:a\s+|any\s+|an\s+)?(?P<kind>completed\s+|complete\s+|done\s+)?"
    r"field\s+visits?\b(?:\s+(?:yet|so\s+far))?", re.IGNORECASE)
_THEM = re.compile(
    r"(?:please\s+)?(?:(?:show|list|display|give)\s+(?:me\s+)?)?(?:them|those|these|all\s+of\s+them|the\s+list|"
    r"the\s+rest|(?:the\s+)?others?|(?:the\s+)?remaining(?:\s+ones)?)(?:\s+(?:please|pls))?[.!?]*", re.IGNORECASE)
_REST = re.compile(r"\b(?:the\s+rest|others?|remaining)\b|மற்றவை|மீதி", re.IGNORECASE)


def _classify(term: str) -> Optional[Tuple[str, str]]:
    t = re.sub(r"\s+", " ", term.lower().replace("-", " ")).strip()
    if t in ("nisd", "nsid"):
        return "type", "NISD"
    if t in ("isd", "merge"):
        return "type", t.upper()
    if t in _STATUS_EN:
        return "status", _STATUS_EN[t]
    if t.replace(" ", "") == "inprogress":
        return "status", "in_progress"
    if t == "csc":
        return "channel", "CSC"
    if t == "citizen":
        return "channel", "citizen"
    if t in ("sro",) or t.replace(" ", "") == "subregistrar":
        return "channel", "sub_registrar"
    m = re.match(r"(?:ward|வார்டு)\s*(\d{1,3})", t)
    if m:
        return "ward", m.group(1).zfill(3)
    for stem, val in _STATUS_TA:
        if t.startswith(stem):
            return "status", val
    return None


def _terms(chunk: str) -> List[Tuple[str, str]]:
    out = []
    for m in re.finditer(_TERM, chunk, re.IGNORECASE):
        c = _classify(m.group(0))
        if c:
            out.append(c)
    return out


def parse(text: str) -> Tuple[Dict[str, Set[str]], str]:
    """({kind: {values}} excluded, the message with the negated phrases taken out)."""
    ex: Dict[str, Set[str]] = {"status": set(), "type": set(), "channel": set(), "ward": set()}

    def _take(kinds):
        for k, v in kinds:
            ex[k].add(v)

    def _group(m):
        inherit = bool(m.group("all"))
        _take(_terms(m.group("first")))
        kept, rest = [], m.group("rest") or ""
        # split the tail into "<sep> [cue] item" pieces
        for piece in re.finditer(rf"(\s*(?:,|and|or|nor|&|/)\s*)((?:(?:{_CUE_ALL}|{_CUE_ONE})\s*)?)({_ITEM})", rest, re.IGNORECASE):
            sep, cue, item = piece.group(1).strip().lower(), piece.group(2), piece.group(3)
            if inherit or cue.strip() or sep in ("or", "nor"):
                _take(_terms(item))
            else:
                kept.append(item)
        return " " + " ".join(kept) + " "

    text = text or ""
    text = _SUFFIX.sub(lambda m: (_take(_terms(m.group("t"))) or " "), text)
    residual = _GROUP.sub(_group, text)
    return {k: v for k, v in ex.items() if v}, residual


def _clean(residual: str, has_positive: bool) -> Tuple[str, bool]:
    """(the positive question left, whether anything of a question is left at all)."""
    r = re.sub(r"\s+", " ", residual).strip(" ,;.?!")
    left = re.sub(r"[\s,;.?!]+", " ", _FILLER.sub(" ", r)).strip()
    for _ in range(4):
        r2 = _DANGLING.sub("", r).strip(" ,;")
        if r2 == r:
            break
        r = r2
    return r, bool(left)


_OR_WORDS = re.compile(r"\b(?:allathu|alladhu|allathey)\b|அல்லது", re.IGNORECASE)
_EITHER = re.compile(r"(?<!\w)(?:either|ஒன்று)\s+", re.IGNORECASE)


_DESK = (r"(?:pres\w{2,4}|availab\w+|there|in\s+(?:the\s+)?sis|in\s+(?:my\s+)?(?:queue|desk)|on\s+(?:my\s+)?desk"
         r"|(?:with|at)\s+(?:the\s+)?sis)(?:\s+in\s+(?:the\s+)?sis)?")
_NOT_ON_DESK = re.compile(r"\s(?:not|other\s+than\s+(?:the\s+ones\s+)?)\s*" + "(?:" + _DESK + ")" + r"\s*", re.IGNORECASE)


def _union(message: str) -> str:
    """"either A or B" / "A allathu B" -> "A or B", and a bare list of scopes gets its noun
    ("either approved or rejected" -> "approved or rejected applications")."""
    m = _OR_WORDS.sub(" or ", message)
    m = _EITHER.sub("", m)
    m = re.sub(r"\s+", " ", m).strip()
    if m != message and not _NOUN.search(m) and _terms(m) and not re.search(r"\bvisits?\b", m, re.IGNORECASE):
        m += " applications"
    return m


def rewrite(message: str, prior: Optional[Dict[str, List[str]]] = None,
            list_in_view: bool = False) -> Optional[str]:
    """The positive question to ask instead of `message`, with the exclusions left in EXCLUDE;
    None when the message carries no negation over applications (or is one the older
    follow-up layer already answers over the rows on screen)."""
    if not message or _APP_NO.search(message):
        return None
    original = message
    message = _NOT_ON_DESK.sub(" not pending and not in progress ", message)   # "not present in SIS" = off the desk
    prior = prior or {}
    prior_ex, prior_base = prior.get("excluded"), prior.get("excluded_base") or "show all applications"
    if prior_ex and _THEM.fullmatch(message.strip()):
        # "show them" after a count / "show the rest" after a negated list
        ex = {k: set(v) for k, v in prior_ex.items()}
        if _REST.search(message):
            ex["_invert"] = {"1"}
        ex["_base"] = {prior_base}
        EXCLUDE.set(ex)
        return prior_base
    survey = None
    sm = _SURVEY.search(message)
    if sm:                                  # "applications in survey 5": rows of that survey only
        from backend.services import qualifier_guard
        if qualifier_guard.listing_shaped(message):
            survey = sm.group(1)
            message = _SURVEY.sub(" ", message)
    no_visit = _NO_VISIT.search(message)
    if no_visit:                           # "ISD applications without a completed field visit"
        message = _NO_VISIT.sub(" ", message)
    if _VISIT.search(message):
        return None
    united = _union(message)
    message = united
    ex, residual = parse(message)
    if not ex and not no_visit and not survey:
        return united if (united != original and _terms(united)) else None
    if survey:
        ex["_survey"] = {survey}
    if no_visit:
        ex["visit"] = {"completed" if no_visit.group("kind") else "any"}
    r, meaningful = _clean(residual, False)
    positive = bool(_terms(r)) or bool(re.search(r"\b(?:all|every)\b", r, re.IGNORECASE))
    fragment = not meaningful or not re.search(r"[A-Za-z஀-௿]{2,}", _FILLER.sub(" ", r))
    if fragment:
        if not prior_ex and list_in_view and len(message.split()) <= 4:
            return None                      # "not ISD" over the rows on screen: the follow-up layer's job
        if prior_ex and not _REST.search(message):   # "show the rest" is a fresh question, not an addition
            for k, vals in prior_ex.items():
                ex.setdefault(k, set()).update(vals)
            r = prior_base                   # the positive part of the list being narrowed ("approved")
        else:
            r = ""
    if not _NOUN.search(r):
        r = f"{r} applications".strip()
    if not positive and not fragment:
        r = _NOUN.sub(lambda m: f"all {m.group(0)}", r, count=1)
    elif not positive and not prior_ex:
        r = _NOUN.sub(lambda m: f"all {m.group(0)}", r, count=1)
    # what a later "show them" / "and not X" re-asks: the same question as a listing
    base = re.sub(r"^(?:how\s+many|count(?:\s+of)?|number\s+of)\b\s*", "show ", r, flags=re.IGNORECASE)
    base = re.sub(r"\b(?:mattum|kaattu|kaami|kaatu|only|just)\b", " ", base, flags=re.IGNORECASE)
    base = re.sub(r"\s+", " ", base).strip()
    if base.isascii() and not re.match(r"(?:show|list|display|give)\b", base, re.IGNORECASE):
        base = f"show {base}"
    ex["_base"] = {base or "show all applications"}
    if survey and not positive:                 # a survey scope covers the whole register, not the desk queue
        r = _NOUN.sub(lambda m: f"all {m.group(0)}", r, count=1) if not re.search(r"\ball\b", r, re.IGNORECASE) else r
    EXCLUDE.set({k: set(v) for k, v in ex.items()})
    return r or "show all applications"


def _survey_matches(row: dict, survey: str) -> bool:
    base = survey.split("/")[0].lstrip("0") or "0"
    if str(row.get("survey_no") or "").split("/")[0].lstrip("0") != base:
        return False
    return "/" not in survey or survey.lower() in (f"{row.get('raw_survey_no') or ''} {row.get('subdivisions') or ''} "
                                                    f"{row.get('included_subdivisions') or ''}").lower()


def _row_excluded(row: dict, ex: Dict[str, Set[str]]) -> bool:
    if ex.get("_survey") and not _survey_matches(row, next(iter(ex["_survey"]))):
        return True
    status = str(row.get("status") or "").lower().replace(" ", "_")
    typ = str(row.get("application_type") or row.get("type") or "").upper()
    chan = str(row.get("submission_channel") or "")
    ward = str(row.get("ward_number") or "").zfill(3)
    if str(row.get("application_number") or "").upper() in ex.get("_visit_apps", ()):
        return True
    return (status in ex.get("status", ()) or typ in ex.get("type", ())
            or chan in ex.get("channel", ()) or ward in ex.get("ward", ()))


async def apply(sd, language: str = "en", db=None, officer=None):
    """Drop the excluded rows from a listing payload, recount, and record what was excluded
    (the next fragment adds to it)."""
    ex = EXCLUDE.get()
    EXCLUDE.set(None)
    if not ex or not isinstance(sd, dict) or not isinstance(sd.get("applications"), list):
        return sd
    visit = ex.get("visit")
    if visit and db is not None:
        from backend.services.postgres import get_field_visits   # the officer's own visit record
        with_visit = {str(v.get("application_number", "")).upper()
                      for v in (await get_field_visits(db, officer)).get("field_visits", [])
                      if "any" in visit or v.get("status") == "completed"}
        ex = {**ex, "_visit_apps": with_visit}
    invert = "_invert" in ex           # "show the rest" after "not X": the rows that were left out
    keep = [r for r in sd["applications"] if isinstance(r, dict) and _row_excluded(r, ex) == invert]
    survey = next(iter(ex["_survey"])) if ex.get("_survey") else None
    sd["applications"] = keep
    sd["count"] = len(keep)
    sd["excluded"] = {} if invert else {k: sorted(v) for k, v in ex.items() if v and not k.startswith("_")}
    if not invert and ex.get("_base"):
        sd["excluded_base"] = next(iter(ex["_base"]))
    words = [w for k in ("status", "type", "channel", "ward") for w in sorted(ex.get(k, ()))]
    vphrase = ("without a completed field visit" if "completed" in (visit or ()) else "without a field visit") if visit else ""
    if invert:
        sd["query_type"] = "Applications (" + ", ".join(w.replace("_", " ") for w in words) + ")" if words else "Applications"
    elif language in ("ta", "tanglish"):
        names = [_LABEL_TA.get(w, w if not w.isdigit() else f"வார்டு {w}") for w in words]
        parts = ([f"{', '.join(names)} தவிர"] if names else []) + (["கள ஆய்வு இல்லாதவை"] if visit else [])
        sd["query_type"] = f"விண்ணப்பங்கள் ({'; '.join(parts)})"
    else:
        names = [w.replace("_", " ") if not w.isdigit() else f"ward {w}" for w in words]
        parts = ([f"excluding {', '.join(names)}"] if names else []) + ([vphrase] if visit else [])
        sd["query_type"] = f"Applications {'; '.join(parts)}"
    if survey:
        sd["query_type"] = f"Applications on survey {survey}" + (f" ({sd['query_type'].split('Applications', 1)[1].strip()})"
                                                                   if not invert and names and language not in ("ta", "tanglish") else "")
        if language in ("ta", "tanglish"):
            sd["query_type"] = f"கணக்கெண் {survey} விண்ணப்பங்கள்"
    if not keep:
        sd["empty_note"] = (f"கணக்கெண் {survey}-க்கு உங்கள் அதிகார வரம்பில் விண்ணப்பம் எதுவும் இல்லை." if language in ("ta", "tanglish")
                            else f"No application on survey {survey} in your jurisdiction.") if survey else None
    return sd
