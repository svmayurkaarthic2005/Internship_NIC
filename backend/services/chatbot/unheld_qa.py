"""Things an officer may ask about an application that NO table of the register holds (caste, market value, sale
amount, stamp duty, acres, the Tahsildar's name, income ...). Answered plainly -- "not held", with what IS held --
instead of the nearest-looking card (an owner card for "caste", the application fee for "sale amount").
Tamil, Tanglish and English. No LLM, no database."""
from __future__ import annotations

import re
from typing import Optional

_APP = re.compile(r"\d{4}/\d{3,4}/\d{1,3}/\d+")

# (cue, English label, Tamil label, what is held instead (English), (Tamil))
_RULES = (
    (r"\bcaste\b|\bcommunity\b|\breligion\b|\bsubcaste\b|சாதி|இனம்|மதம்|\bsaadhi\b|\bjaathi\b|\bmatham\b",
     "caste / community / religion", "சாதி / சமூகம் / மதம்",
     "the owner's name, relative, relationship, sex and share where recorded", "உரிமையாளர் பெயர், உறவினர், உறவு, பாலினம், பங்கு (பதிவாகியிருந்தால்)"),
    (r"\bincome\b|\bsalary\b|\bmarital\b|\bblood\s+group\b|\bbank\s+account\b|\be-?mail\b|வருமானம்|திருமண",
     "income / marital status / bank account / e-mail", "வருமானம் / திருமண நிலை / வங்கி கணக்கு / மின்னஞ்சல்",
     "the owner's name, relative, relationship, sex, mobile and share where recorded", "உரிமையாளர் பெயர், உறவினர், உறவு, பாலினம், கைபேசி, பங்கு (பதிவாகியிருந்தால்)"),
    (r"market\s+(?:value|rate|price)|guide\s*line\s+value|guidance\s+value|land\s+value|property\s+value|valuation|"
     r"sale\s+(?:amount|value|price|consideration)|consideration|stamp\s+duty|registration\s+(?:fee|fees|charges)|"
     r"சந்தை\s*மதிப்பு|வழிகாட்டி\s*மதிப்பு|முத்திரை\s*(?:தீர்வை|கட்டணம்)|விற்பனை\s*(?:தொகை|மதிப்பு)|\bmadhippu\b|\bvilai\b",
     "market / guideline value, sale amount, stamp duty or registration fee", "சந்தை / வழிகாட்டி மதிப்பு, விற்பனைத் தொகை, முத்திரைத் தீர்வை, பதிவுக் கட்டணம்",
     "the registration document (number, place, date, year) and reason for the transfer; the only amount held is the application fee (challan amount)",
     "பதிவு ஆவணம் (எண், இடம், தேதி, ஆண்டு) மற்றும் மாற்றத்தின் காரணம்; பதிவில் உள்ள ஒரே தொகை விண்ணப்பக் கட்டணம் (சலான் தொகை)"),
    (r"\b(?:tahsildar|tahsildhar|thasildar|dis|sd|surveyor|officer|zdt|rdo)(?:'s|s)?\s+(?:name|id|mobile|phone|contact)\b|"
     r"\bname\s+of\s+the\s+(?:tahsildar|dis|sd|officer|surveyor|zdt|rdo)\b|\bwho\s+is\s+the\s+(?:tahsildar|dis|sd|zdt|rdo)\b|"
     r"தாசில்தார்\s*(?:பெயர்|யார்)|அதிகாரி\s*பெயர்",
     "the name of an officer", "அதிகாரியின் பெயர்",
     "the desk that acted (SIS, SD, DIS, Tahsildar ...), the date and the remark", "நடவடிக்கை எடுத்த மேசை, தேதி மற்றும் குறிப்பு"),
    (r"\bpatta\s+holder'?s\s+age\b|\bage\s+of\s+the\s+owner\b|\bowner'?s\s+age\b|\bwhat\s+is\s+the\s+age\b|வயது|\bvayasu\b",
     "age", "வயது", "the date of birth of the applicant where recorded", "விண்ணப்பதாரரின் பிறந்த தேதி (பதிவாகியிருந்தால்)"),
)
_RULES = _RULES + (
    (r"(?<!new )(?<!old )(?<!igrs )(?<!owner )(?<!applicant )(?<!applicant's )(?:aadhaar|aadhar|aadhaar)\b(?!.*(?:owner|applicant|igrs|chitta|recorded))|"
     r"^(?!.*(?:உரிமையாளர்|விண்ணப்பதாரர்)).*ஆதார்",
     "the Aadhaar number", "ஆதார் எண்",
     "whether an Aadhaar is recorded for an owner (the number itself is never shown by this assistant)",
     "உரிமையாளருக்கு ஆதார் பதிவாகியுள்ளதா என்பது மட்டும் (எண் ஒருபோதும் காட்டப்படாது)"),
    (r"\bacres?\b|\bcents?\b|\bgrounds?\b|\bsq\.?\s*ft\b|\bsquare\s+feet\b|ஏக்கர்|சென்ட்",
     "extent in acres / cents / grounds / sq.ft", "ஏக்கர் / சென்ட் / கிரவுண்ட் அளவு",
     "the extent as hectare + ares + sq.m (ask for the area or extent of the application)",
     "பரப்பு ஹெக்டேர் + ஏர் + ச.மீ ஆக (விண்ணப்பத்தின் பரப்பைக் கேளுங்கள்)"),
)
_RULES = tuple((re.compile(c, re.IGNORECASE), *rest) for c, *rest in _RULES)
_LIST = re.compile(r"\b(?:applications|list|all\s+(?:of\s+)?the|each|every)\b", re.IGNORECASE)
_HELD_FIRST = re.compile(r"\b(?:fee|amount|challan|payment)\b", re.IGNORECASE)


def context_app(chat_history) -> Optional[str]:
    """The application the conversation is about: the newest message that names one. A listing (3+ numbers) is about
    none of its rows, so if that is the newest, nothing is carried (the ordinary handlers ask which one)."""
    from .cmflow_qa import _APP_NO
    for m in reversed((chat_history or [])[-8:]):
        content = (m.get("content") or "") if isinstance(m, dict) else str(m)
        role = str(m.get("role", "")).lower() if isinstance(m, dict) else ""
        if role in ("assistant", "ai", "bot"):
            if len(set(_APP.findall(content))) >= 3:
                return None
            f = _APP_NO.search(content)
            if f:
                return f.group(0)
        else:
            a = _APP.search(content)
            if a:
                return a.group(0)
    return None


_LIT = re.compile(r"\b(?:court\s+cases?|case\s+(?:no|number)|litigation|lawsuit|legal\s+dispute|stay\s+order)\b|நீதிமன்ற\s*வழக்கு|வழக்கு", re.IGNORECASE)


def litigation(message: str, chat_history) -> Optional[str]:
    """"which court case is pending against it": a litigation question about the application in view."""
    text = message or ""
    if _APP.search(text) or not _LIT.search(text) or re.search(r"\bsurvey\b|applications|\bhow\s+many\b", text, re.IGNORECASE):
        return None
    app = context_app(chat_history)
    return f"is there litigation on {app}" if app else None


def answer(message: str, chat_history, ta: bool) -> Optional[str]:
    return (answer_app(message, chat_history, ta) or (None, None))[0]


def answer_app(message: str, chat_history, ta: bool):
    """The "not held" answer, or None. Needs an application in the message or the last 8 turns (without one the
    question is not about a record and the ordinary handlers take it)."""
    text = message or ""
    if len(text.split()) > 25 or _LIST.search(text):
        return None
    app = _APP.search(text)
    if not app:
        ctx = context_app(chat_history)
        app = _APP.search(ctx) if ctx else None
    if not app:
        return None
    for rx, en, tam, held_en, held_ta in _RULES:
        if rx.search(text):
            # "what is the application fee" is held; only the sale / market value wording is not
            if en.startswith("market") and _HELD_FIRST.search(text) and not re.search(
                    r"sale|market|guide|stamp|valuation|consideration|registration", text, re.IGNORECASE):
                return None
            if en == "the Aadhaar number":  # held, but never displayed
                return (("ஆதார் எண் ஒருபோதும் காட்டப்படாது. உரிமையாளருக்கு ஆதார் பதிவாகியுள்ளதா என்று மட்டும் கேளுங்கள்.", app.group(0)) if ta else
                        # Generic across every owner-record module (old/new/IGRS/natham) -- this helper has
                        # no way to know which one applies to {app}, so it must not assume "new owner" when
                        # the file in view might be an ISD old-owner (chitta) or any other owner record.
                        (f"Aadhaar numbers are never shown by this assistant. I can say only whether one is recorded for an owner of {app.group(0)} "
                         "(ask: is the Aadhaar recorded for the owner).", app.group(0)))
            if ta:
                return f"{tam} பற்றிய தகவல் இந்த பதிவேட்டில் இல்லை ({app.group(0)}). பதிவில் உள்ளது: {held_ta}.", app.group(0)
            return f"{en[0].upper()}{en[1:]} is not held in the SIS register for {app.group(0)}. What is held: {held_en}.", app.group(0)
    return None


_WHERE = re.compile(r"^\W*(?:where\s+is\s+(?:it|this(?:\s+(?:file|application))?)|who\s+(?:has|is\s+holding)\s+it(?:\s+now)?|"
                    r"which\s+desk(?:\s+is\s+it\s+(?:at|with))?|current(?:ly)?\s+(?:at|with|desk|stage))\W*(?:now|right\s+now)?\W*$", re.IGNORECASE)


_WF_OF = re.compile(r"^\W*(?:show\s+|give\s+|what\s+is\s+)?(?:me\s+)?(?:the\s+)?workflow(?:\s+(?:steps?|flow|path|status))?\s+(?:of|for)\b", re.IGNORECASE)


def where_is(message: str, chat_history) -> Optional[str]:
    """"where is it" / "who has it now" with no number: the workflow position of the application in view; and
    "workflow of X" / "workflow steps for X" is the workflow HISTORY of X (one name for one question)."""
    m = _APP.search(message or "")
    if m and _WF_OF.match(message or "") and "history" not in (message or "").lower():
        return f"show workflow history of {m.group(0)}"
    if m or not _WHERE.match((message or "").strip()):
        return None
    app = context_app(chat_history)
    return f"where is {app} in the workflow" if app else None


_BUSY = re.compile(r"\bhow\s+busy\b|\bsummar(?:i[sz]e|y)\b.{0,20}\b(?:my\s+)?(?:day|work|workload|desk)\b|\bmy\s+(?:day|desk)\b.{0,15}\b(?:look|summary)|"
                   r"இன்று.{0,20}(?:வேலை|பணி)\s*(?:எவ்வளவு|சுருக்கம்)|வேலைப்பளு|\bvelai\s*palu\b|\bnaan\s+evlo\s+busy\b", re.IGNORECASE)
_PRIORITY = re.compile(r"\bwhich\s+(?:of\s+)?my\s+(?:files|applications|cases)\b.{0,25}\b(?:worry|first|urgent|priorit\w+|attention)\b|"
                       r"\bworry\s+about\b|\bwhat\s+should\s+i\s+(?:do|handle|look\s+at|take)\s+first\b|\b(?:most\s+urgent|top\s+priority)\b|"
                       r"கவலைப்பட|முதலில்\s*எதை|எது\s*முக்கியம்|\bedhu\s+mukkiyam\b|\bmukkiyam\b", re.IGNORECASE)


def intent_alias(message: str) -> Optional[str]:
    """Open phrasings with a data answer: "how busy am I", "summarise my day" = the workload; "which of my files should I worry
    about first", "நான் எதைப் பற்றி கவலைப்பட வேண்டும்" = the field-visit plan (overdue first, then the longest waiting)."""
    t = message or ""
    if _APP.search(t) or len(t.split()) > 16:
        return None
    if _BUSY.search(t):
        return "my workload"
    if _PRIORITY.search(t):
        return "which application should I field visit next"
    return None
