"""
Deterministic conversation reference context for implicit follow-ups.

The problem
-----------
An officer rarely repeats themselves. After "show my pending ISD applications"
they ask "which one is oldest?"; after "give details for 2026/0154/28/001280"
they ask "what is the applicant name?". Neither follow-up contains a pronoun,
an application number, or a scope -- and both are meaningless without the turn
before them.

Until now every one of those was resolved by scraping the *rendered* previous
answer: regexes over the HTML table for application numbers, prose matching for
survey numbers, and string concatenation of the previous user message to carry
a filter forward. That works for the shapes somebody thought to code, and fails
silently otherwise -- "show the field visit for X" followed by "when was it
scheduled?" answered with X's *submission* date, because the word "field visit"
was in the previous turn and the field map sends every "when" to
`submission_date`.

The fix
-------
Record what each deterministic answer was *about*, as data, at the moment it is
produced; then resolve the next turn against that record instead of against
prose. `ChatMessage.structured_data` already existed for this and was never
written to.

Everything in this module is pure: it decides *what* the follow-up refers to.
It never reads the database, and resolving a reference is never a grant --
`chatbot.py` re-fetches through the ordinary officer-scoped query functions, so
a number carried forward is re-checked against the officer's jurisdiction
before a single field of it is shown.

The rule that keeps it honest: **never infer a reference when more than one
referent fits.** A guessed referent produces a confident answer about the wrong
record, which is worse than a question. `Resolution.ambiguous` carries a short
bilingual clarification instead.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from functools import lru_cache

from backend.utils.fuzzy import (
    damerau_levenshtein_distance,
    is_token_typo_match,
)

# ─────────────────────────────────────────────────────────────────────────────
# The context record
# ─────────────────────────────────────────────────────────────────────────────
ENTITY_APPLICATION = "application"
ENTITY_APPLICATION_LIST = "application_list"
ENTITY_FIELD_VISIT = "field_visit"
ENTITY_SURVEY = "survey"

# Version tag on the stored JSON. A context written by an older build that no
# longer parses is ignored rather than half-read -- a stale reference is the
# one thing this module exists to prevent.
CONTEXT_VERSION = 1


@dataclass
class FollowupContext:
    """What the previous deterministic answer was about.

    Stored as JSON on the assistant `ChatMessage`, read back on the next turn.
    Only ever built from values a database query returned -- never from the
    text of an answer, and never from anything the LLM wrote.
    """

    entity: str
    application_numbers: List[str] = field(default_factory=list)
    survey_numbers: List[str] = field(default_factory=list)
    filters: Dict[str, Any] = field(default_factory=dict)
    query_type: Optional[str] = None
    intent: Optional[str] = None
    version: int = CONTEXT_VERSION

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> Optional["FollowupContext"]:
        if not isinstance(raw, dict):
            return None
        if raw.get("version") != CONTEXT_VERSION:
            return None
        entity = raw.get("entity")
        if entity not in (ENTITY_APPLICATION, ENTITY_APPLICATION_LIST,
                          ENTITY_FIELD_VISIT, ENTITY_SURVEY):
            return None
        try:
            return cls(
                entity=entity,
                application_numbers=[str(n) for n in (raw.get("application_numbers") or [])],
                survey_numbers=[str(n) for n in (raw.get("survey_numbers") or [])],
                filters=dict(raw.get("filters") or {}),
                query_type=raw.get("query_type"),
                intent=raw.get("intent"),
            )
        except Exception:  # pragma: no cover - a malformed record is simply unusable
            return None

    @property
    def single_application(self) -> Optional[str]:
        if self.entity in (ENTITY_APPLICATION, ENTITY_FIELD_VISIT) and \
                len(self.application_numbers) == 1:
            return self.application_numbers[0]
        return None


# How many application numbers are worth carrying. A listing of 34 is still a
# usable scope for "how many of them are approved"; beyond this the follow-up
# is re-answered by re-running the question instead, and the row cap keeps the
# JSONB record small.
MAX_CARRIED = 200


def build_context(intent: Optional[str],
                  structured_data: Optional[Dict[str, Any]],
                  fallback_app_number: Optional[str] = None) -> Optional[FollowupContext]:
    """Derive the context from a deterministic answer's structured data.

    Returns None when the answer had no referent worth carrying -- a greeting,
    a document/RAG answer, an access denial. Refusals deliberately carry
    nothing: "that application is outside your jurisdiction" must not leave the
    number behind for the next turn to pick up.
    """
    sd = structured_data or {}
    if not isinstance(sd, dict):
        return None

    # An answer that found nothing, or refused, leaves no referent.
    if sd.get("found") is False or sd.get("accessible") is False:
        return None

    filters = {k: sd[k] for k in (
        # `submission_channels` is the list a multi-channel listing was scoped
        # to ("CSC and Sub-Registrar"); `submission_channel` stays the scalar a
        # single-channel one recorded, so every existing reader is unchanged.
        "status", "type", "application_type", "submission_channel",
        "submission_channels",
        "submission_year", "submission_month", "ward_number", "block_number",
        "start_date", "end_date", "month_label", "sort_by", "sort_dir",
        "transfer_party",
        # "applicant details" then a bare "both" -- the second turn names no
        # field of its own, so without this the answer reverted to the
        # application card (type/status/stage) instead of staying on the
        # applicants (name/mobile/address) the officer had just pivoted to.
        "detail_focus",
        # what a negated listing left out ("not pending" ... "not rejected either")
        "excluded", "excluded_base",
        # the columns a field-visit table has been given / stripped of, so the next request builds on them
        "fv_columns",
    ) if sd.get(k) not in (None, "", [])}

    if intent in ("clarification_required", "outside_jurisdiction") or sd.get("found") is False:
        return FollowupContext(
            entity=ENTITY_APPLICATION_LIST,
            application_numbers=[],
            filters=filters,
            query_type=sd.get("query_type"),
            intent=intent,
        )

    # "show me details for <A> and <B>" answers with several NAMED
    # applications in one turn (`multi_applications` -- chatbot.py's own name
    # for this shape, distinct from `applications`, a filtered listing). A
    # follow-up naming none of them ("which one is older?", "what is the fee
    # for the second one?") found no context at all here and fell to
    # `general_query`, where the LLM answered from the rendered text instead
    # of a deterministic comparison/lookup -- exactly the failure this module
    # exists to prevent, even on the turns the LLM happens to get right.
    multi = sd.get("multi_applications")
    if isinstance(multi, list) and multi:
        numbers = []
        for d in multi[:MAX_CARRIED]:
            if isinstance(d, dict) and d.get("found", True):
                n = d.get("application_number")
                if n and str(n).upper() not in numbers:
                    numbers.append(str(n).upper())
        if numbers:
            return FollowupContext(
                entity=ENTITY_APPLICATION_LIST,
                application_numbers=numbers,
                filters=filters,
                query_type=sd.get("query_type") or "Named Applications",
                intent=intent,
            )

    # fv_unassigned_awaiting stores its list under a different key ("which
    # applications are unscheduled?" -> unassigned_applications, not
    # applications) -- unrecognised here, so no context was ever saved for it
    # and a follow-up ("which application number is that?") fell through to
    # whatever listing came before it in the conversation instead.
    #
    # `or` between the two reads is wrong here: an explicit EMPTY list
    # (`{"applications": []}`, the deliberate barrier a few lines down) is
    # falsy, so `[] or sd.get("unassigned_applications")` silently fell
    # through to whichever key held something -- or to None, erasing the
    # barrier and letting the walk-back reach straight past it again.
    rows = sd["applications"] if isinstance(sd.get("applications"), list) \
        else sd.get("unassigned_applications")
    if isinstance(rows, list) and rows:
        numbers: List[str] = []
        for r in rows[:MAX_CARRIED]:
            if isinstance(r, dict):
                n = r.get("application_number")
                if n and str(n).upper() not in numbers:
                    numbers.append(str(n).upper())
        if numbers:
            return FollowupContext(
                entity=ENTITY_APPLICATION_LIST,
                application_numbers=numbers,
                filters=filters,
                query_type=sd.get("query_type"),
                intent=intent,
            )

    # A listing that matched nothing still says what the officer just asked
    # about, and it must be recorded -- as an EMPTY list. Without this record
    # the loader walks back past this turn to the last answer that had rows,
    # and the next follow-up is answered over a set the officer has already
    # moved on from: "how many NISD applications do I have?" -> "none", then
    # "how many are approved?" -> "None of those 1 application(s)", counted
    # over the ISD list from two turns earlier. An empty record is a barrier:
    # `resolve()` stands aside on it, which sends the follow-up down the
    # ordinary re-scoping path and gets it answered from a fresh query.
    if isinstance(rows, list) and not rows:
        return FollowupContext(
            entity=ENTITY_APPLICATION_LIST,
            application_numbers=[],
            filters=filters,
            query_type=sd.get("query_type"),
            intent=intent,
        )

    # A field-visit summary carries its rows under "field_visits". Recorded as a
    # list (about visits) so "the 2nd one", "when is that scheduled?" and "what
    # about the third" resolve against the rows on screen, not "nothing shown".
    fv_rows = sd.get("field_visits")
    if isinstance(fv_rows, list) and not fv_rows and intent in ("field_visits", "fv_between_dates"):
        # an empty visit table is still what the officer was just looking at
        return FollowupContext(entity=ENTITY_APPLICATION_LIST, application_numbers=[],
                               filters={**filters, "about_visit": True},
                               query_type=sd.get("query_type") or "Field Visits", intent=intent)
    if isinstance(fv_rows, list) and fv_rows:
        numbers = []
        for r in fv_rows[:MAX_CARRIED]:
            n = r.get("application_number") if isinstance(r, dict) else None
            if n and n != "N/A" and str(n).upper() not in numbers:
                numbers.append(str(n).upper())
        if numbers:
            return FollowupContext(
                entity=ENTITY_APPLICATION_LIST,
                application_numbers=numbers,
                filters={**filters, "about_visit": True},
                query_type=sd.get("query_type") or "Field Visits",
                intent=intent,
            )

    number = sd.get("application_number") or fallback_app_number
    if number:
        # A field-visit answer is about the visit, not only about the file. The
        # distinction is what makes "when was it scheduled?" resolvable.
        entity = ENTITY_FIELD_VISIT if _is_field_visit_payload(intent, sd) else ENTITY_APPLICATION
        return FollowupContext(
            entity=entity,
            application_numbers=[str(number).upper()],
            filters=filters,
            query_type=sd.get("query_type"),
            intent=intent,
        )

    survey = sd.get("survey_no") or sd.get("base_survey_no") or sd.get("survey_number")
    if survey:
        return FollowupContext(
            entity=ENTITY_SURVEY,
            survey_numbers=[str(survey)],
            filters=filters,
            query_type=sd.get("query_type"),
            intent=intent,
        )
    return None


def _is_field_visit_payload(intent: Optional[str], sd: Dict[str, Any]) -> bool:
    if (intent or "").startswith("fv_") or sd.get("_asked_field_visit"):
        return True
    if sd.get("query_type") and "field visit" in str(sd["query_type"]).lower():
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Classifying the follow-up
# ─────────────────────────────────────────────────────────────────────────────
# A follow-up is a fragment: it asks something, and names nothing to ask it of.
# The three shapes below are the ones a stored context can answer.

FOLLOWUP_NONE = "none"
FOLLOWUP_SINGULAR = "singular"        # "what is the applicant name?", "which ward?"
FOLLOWUP_LIST_AGGREGATE = "aggregate" # "how many are approved?", "which is oldest?"
FOLLOWUP_LIST_REFINE = "refine"       # "show only NISD", "just the overdue ones"
FOLLOWUP_LIST_FIELD = "list_field"    # "what is the applicant name?" over a list

# A message that names its own subject is a fresh question, never a follow-up.
# The application number is first: a message carrying one needs no context.
_OWN_SUBJECT_RE = re.compile(
    r"\d{4}/\d{3,4}/\d{1,3}/\d+"
    r"|\bapp-\d+-\d+\b"
    r"|\bmy\b|\ball\s+(?:my|the)\b|\bshow\s+me\s+all\b"
    # Naming the noun is naming the subject: "how many ISD applications do I
    # have" is a question, not a continuation of one. A back-reference in the
    # same breath ("how many of them are applications") overrides this below.
    r"|\bapplications?\b|\bapps?\b|\bfiles?\b"
    r"|\bdo\s+i\b|\bi\s+have\b"
    r"|\bsurvey\s+(?:no|number)?\s*\d"
    r"|\bjurisdiction\b|\bworkload\b|\bward\s+\d|\bblock\s+\d"
    # A PLURAL "field visits" / "inspections" is a subject of its own, the same
    # way "applications" is. Without this, "how many field visits are
    # completed" -- a complete question -- was claimed as an aggregate over
    # whatever application list happened to be on screen and answered
    # "2 of those 2 application(s) are pending": the wrong collection, and the
    # wrong status to a question that said "completed".
    #
    # Deliberately plural only. The SINGULAR "is the field visit scheduled?" is
    # a real follow-up about the application already in view, and is the case
    # test_field_visit_followups.py exists to protect.
    r"|\bfield\s+visits\b|\bvisits\b|\binspections\b"
    # "who/what is SIS?" / "who is the Tahsildar?" asks what that role IS,
    # not a follow-up about a row on screen. Without this, "who" is itself a
    # singular-field cue (the same word that resolves "who is the
    # applicant?"), so every one of these was read as pointing back at
    # whatever list was last shown -- "who is SIS?" right after a 70-row
    # listing came back "Which one do you mean? Give the application number,
    # or say 'the first one'", a clarification for a question that named its
    # own subject outright.
    #
    # Scoped to the "who/what is" shape specifically, not a bare mention of
    # the acronym: "how many are ISD?" / "only merge" are real refine/
    # aggregate follow-ups over the carried list and must stay that way --
    # broadening this to any bare "isd"/"nisd"/"merge" anywhere in the
    # message broke exactly those.
    r"|\b(?:who|what)\s+(?:is|are)\s+(?:a\s+|an\s+|the\s+)?"
    r"(?:sis|tahsildar|dis|isd|nisd|merge)\b"
    # "who am I" / "what is my name" -- the officer asking about THEMSELVES,
    # not about a row in the carried list. Bare "who" is itself a
    # singular-field cue (it resolves "who is the applicant?"), so without
    # this "who am i" after a listing was read the same way "who is SIS?" was
    # before the fix just above it -- a clarification for a question that
    # already names its own subject. `chatbot._is_officer_identity_question`
    # is the deterministic handler this exemption clears the way for.
    r"|\bwho\s+am\s+i\b|\bwho\s+i\s+am\b|\bwhoami\b|\bam\s+i\s+(?:a|an|the)\s+sis\b"
    r"|நான்\s*யார்"
    # "what is today's date" / "current date" -- the calendar date, not a
    # field of the application in view. Bare "date" is itself a singular-
    # field cue (it resolves "submission date?"), so without this the
    # question was read as pointing at the carried row and either asked
    # which one was meant or, worse, answered with that row's submission
    # date as if it were today's.
    r"|\btoday'?s?\s+date\b|\bcurrent\s+date\b|\bdate\s+today\b"
    r"|\bdate\s+is\s+it\b|\bwhat\s+date\s+is\b"
    r"|இன்றைய\s*தேதி"
    # Tanglish "my / I have"
    r"|\benaku\b|\benakku\b|\bennoda\b"
    # Tamil is matched as SUBSTRINGS, never with \b: the virama (்) is not a
    # word character, so "என்\b" matched inside "என்ன" ("what") and turned
    # every Tamil question ending in "…என்ன?" into a fresh question. Same trap
    # CLAUDE.md documents for the comparison parser.
    r"|எனது|என்னுடைய|எனக்கு"
    # "applications" (plural). Deliberately NOT the bare stem "விண்ணப்ப",
    # which also starts "விண்ணப்பதாரர்" (applicant) -- that would make
    # "விண்ணப்பதாரர் பெயர் என்ன?" a fresh question instead of a follow-up.
    r"|விண்ணப்பங்க"
    # A calendar date names a fresh period ("from 2026-07-01 to 2026-07-29").
    r"|\b(?:19|20)\d{2}-\d{2}-\d{2}\b"
    # Field-visit POLICY and desk questions carry their own subject: they ask how
    # the process works, not about the row on screen ("can I postpone the field
    # visit?" -- a pronoun still makes it a follow-up, handled by the gate).
    r"|\b(?:postpone|reschedule|prepone)\b|\bscheduling\s+conflicts?\b|\bwho\s+approves\b"
    r"|\bchange\s+of\s+(?:field\s+visit\s+)?date\b|\bassigned\b"
    # Tamil: a plural "field visits", a survey / sub-division number, and a
    # service code + fee are subjects of their own. Matched as substrings.
    r"|கள\s*ஆய்வுகள்|கள\s*ஆய்வுகளை|(?:சர்வே|புல\s*எண்|உட்பிரிவு)\s*\d"
    r"|(?:tslr|csc|isd|nisd|merge)\b.*கட்டண|கட்டண.*\b(?:tslr|csc|isd|nisd|merge)\b"
    # a NAMED district / taluk in a code question ("திருவள்ளூர் மாவட்ட குறியீடு");
    # a bare "மாவட்ட குறியீடு" or "அந்த மாவட்ட ..." still points back.
    r"|(?<!அந்த\s)(?<!இந்த\s)(?<!அதன்\s)\S{3,}\s+(?:மாவட்ட|வட்ட)\S*\s+(?:குறியீடு|taluk_code|district_code)"
    r"|\b(?:taluk|district|ward|block)_code\b"
    # a survey / sub-division number, plural surveys, the department, a TSLR service
    r"|\bsub[\s-]?divisions?\s+\d|\bsurveys\b|\bdepartment(?:_code)?\b|\btslr\b|துறை"
    r"|(?:சர்வே|புல)\s*(?:எண்)?\s*\d",
    re.IGNORECASE,
)

# "என்" ("my") is the commonest way an officer writes "my <noun>" in Tamil --
# at least as common as "எனது" above -- but it cannot go in _OWN_SUBJECT_RE as
# a plain substring the way the other Tamil cues do: "என்" is also the first
# two characters of "என்ன" ("what"), so every Tamil question ending "...என்ன?"
# would match it, the exact virama-substring trap the other Tamil cues already
# had to be built to avoid. Splitting the message into contiguous Tamil-script
# runs (the same technique chatbot._tokens() uses) and requiring "என்" to be a
# whole run sidesteps that: "என்ன" and "என்று" stay one run each and never
# equal "என்", while "என் விண்ணப்பம்" splits on the space and does.
_TAMIL_RUN_RE = re.compile(r"[஀-௿]+")
_OWN_SUBJECT_EXACT_TA = {"என்"}


# Subject nouns worth recognising through a typo, and the real words that must
# never be mistaken for one. The rest of the pipeline matches keywords with
# `is_token_typo_match`; this gate did not, and it is the gate that decides
# whether a message is a question of its own. So "applciaiton from csc" -- a
# plain listing request with two letters transposed -- failed the gate, was
# taken for a bare follow-up (the word "csc" is a field cue), and came back as
# *"That answer covered 24 applications. Which one do you mean?"*: a
# clarification in place of the list, for a question that named its own subject
# perfectly clearly.
#
# `applicant` is the trap. It sits close enough to `application` to fall inside
# the edit budget, and it means something else entirely -- "what is the
# applicant name?" is the canonical singular follow-up this module exists to
# resolve. Words that are themselves real vocabulary are therefore never
# treated as a typo of anything.
_FUZZY_SUBJECT_NOUNS = ("application", "applications")
_NEVER_A_SUBJECT_TYPO = frozenset({
    "applicant", "applicants", "applicable", "applied", "applying", "appliance",
})
_WORD_RE = re.compile(r"[a-z]{6,}")


def _has_own_subject(lowered: str) -> bool:
    if _OWN_SUBJECT_RE.search(lowered):
        return True
    
    tamil_words = _TAMIL_RUN_RE.findall(lowered)
    if any(w in _OWN_SUBJECT_EXACT_TA for w in tamil_words):
        return True
    # A bare startswith("விண்ணப்ப") check used to sit here, and it reintroduced
    # exactly the bug CLAUDE.md documents as fixed: "விண்ணப்பதாரர்" (applicant)
    # starts with the same stem as "விண்ணப்பம்" (application), so "applicant
    # name what?" was taken for a fresh question naming its own subject, the
    # same collision `_NEVER_A_SUBJECT_TYPO` exists to block on the English
    # side. `_OWN_SUBJECT_RE`'s own விண்ணப்பங்க literal already covers the
    # cases that check was for ("show my applications" in Tamil); nothing
    # here needs to re-derive it with a prefix match.
    for token in _WORD_RE.findall(lowered):
        if token in _NEVER_A_SUBJECT_TYPO:
            continue
        if any(is_token_typo_match(token, noun) for noun in _FUZZY_SUBJECT_NOUNS):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Spelling: one correction pass, so every cue table below can stay exact
# ─────────────────────────────────────────────────────────────────────────────
# Officers type fast and misspell constantly, and a follow-up is the worst place
# to be brittle about it: the message is a fragment, so a single wrong letter is
# the whole signal gone. Measured against a ten-row listing, only 9 of 29
# ordinary follow-up shapes survived one typo -- "what is the wrad", "the secnd
# one", "thier wards" and "the staus of each" all stopped being follow-ups at
# all and fell through to the LLM.
#
# Every cue table in this module matches exactly, and there are nine of them.
# Rather than make each one fuzzy -- nine chances to get the precision guards
# wrong -- the message is corrected ONCE, here, against the vocabulary those
# tables already define. The tables stay exact and unchanged; they simply see a
# correctly-spelled message.
#
# Three guards keep it from inventing meaning that was not there:
#   1. A word already in the vocabulary is never touched. This is what keeps
#      `applicant` from being "corrected" into `application` -- both are real
#      cues, and the difference between them decides whether a message is a
#      follow-up at all.
#   2. A tie corrects nothing. If two vocabulary words sit equally close, the
#      token is left exactly as typed rather than guessed between.
#   3. Common words that are nobody's cue are never corrected, so ordinary
#      English cannot drift into the vocabulary.
# `is_token_typo_match` supplies the rest: a length-based edit budget (nothing
# under four letters is correctable at all), first-character survival, and an
# exact-match requirement for Tamil, whose single glyphs carry too much meaning
# for an edit budget. Only ASCII letter runs are rewritten, so Tamil and
# Tanglish text passes through untouched.
# Three letters, not four: a dropped letter turns "them" into "thm" and "ward"
# into "wrd", and those are among the commonest slips there are. The edit budget
# still comes from the TARGET word's length (`_max_edits_for`), so nothing
# three letters or shorter -- "isd", "csc", "fee" -- can be reached by a typo at
# all, which is what protects the short codes.
_ASCII_WORD_RE = re.compile(r"[a-zA-Z]{3,}")

# Words that must never be rewritten: ordinary English that happens to sit near
# a cue. "want" is two edits from "ward", "sent" from "send"/"seen".
_NEVER_CORRECT = frozenset({
    # Tanglish "do it" -- one edit from "pangu" (share), which would turn "delete pannu" into a share question
    "older", "newer", "oldest", "newest", "earlier", "latest", "higher", "lower", "bigger", "larger", "smaller",
    "faster", "slower", "longer", "shorter", "fewer", "fewest",
    "ratio", "ratios", "percent", "percentage", "fraction", "proportion",
    "pannu", "pannunga", "pannidu", "pannren", "panren", "pannuga", "pannungo", "pannitu",
    "wipe", "wiped", "wiping", "expunge", "purge", "erase", "discard", "conversation", "convo", "history",
    "want", "wants", "wanted", "need", "needs", "needed", "give", "gives",
    "send", "sent", "take", "takes", "make", "makes", "have", "has", "had",
    "does", "done", "did", "from", "with", "that", "this", "than", "then",
    "there", "here", "were", "was", "will", "would", "could", "should",
    "your", "yours", "mine", "ours", "also", "some", "much", "very", "well",
    "good", "back", "over", "into", "about", "after", "before", "please",
    "thanks", "thank", "right", "wrong", "other", "another", "again",
    "still", "even", "ever", "never", "always", "like", "want", "know",
    "tell", "said", "say", "says", "get", "got", "put", "see", "saw",
    "now", "new", "old", "one", "two", "ten", "and", "the", "for", "are",
    "can", "may", "any", "all", "not", "but", "out", "off", "its", "his",
    "her", "our", "you", "who", "why", "how", "was", "use", "used",
    # Words the document corpus happens not to contain, each of which edit
    # distance turns into a DIFFERENT real word. Found by running every question
    # in the repo's corpora through the corrector, not by guesswork.
    "mode", "wise", "decision", "clarification", "given", "govern", "governs",
    "none", "named", "camps", "cmp", "thandhai", "items", "core", "court",
    # "bot" is one edit from "both" (an _AGGREGATE_RE cue): "are you a bot"
    # corrected to "are you a both" and was read as an aggregate follow-up
    # over whatever list was on screen, instead of the self-identity question
    # it is -- the same collision class as "field"/"filed" and "state"/"stage"
    # documented above, just for a word this domain never had reason to type
    # until officers started asking the assistant what it is.
    "bot", "bots",
})


def _is_inflection(token: str, candidate: str) -> bool:
    """True when the two differ only by a plural 's' / 'es'.

    A plural is not a misspelling of its singular. Without this, "pattas" was
    rewritten to "patta" and "recommendations" to "recommendation" -- harmless
    for matching, since the cue tables use substrings, but it is still the
    corrector editing a word the officer spelled correctly, and every such edit
    is one more thing that can go wrong later.
    """
    a, b = sorted((token, candidate), key=len)
    return b in (a + "s", a + "es") or (b.endswith("ies") and a.endswith("y")
                                        and b[:-3] == a[:-1])

# ── The dictionary: words that are real, and so are never "corrected" ───────
# A typo and a real word are not distinguishable by edit distance alone, and
# English is dense enough that most short words have a neighbour. Measured over
# the 7,116 questions in this repo's own test corpora, correcting on distance
# alone rewrote 121 real words: `field` -> `filed` (it is one transposition from
# a submission-channel cue, and it broke "show field visit" outright), `bank` ->
# `back`, `state` -> `stage`, `change` -> `charge`, `data` -> `date`.
#
# The only thing that separates the two cases is knowing which strings are
# words, so this consults the department's own document corpus -- the same
# `backend/documents/*.txt` that `backend/ingest.py` embeds. A token that
# appears anywhere in it is a word the domain actually uses, whatever it happens
# to sit close to, and it is left exactly as typed.
#
# The corpus is read once and cached. If it cannot be read the set is empty and
# correction simply falls back to the curated list above -- a degraded guard,
# never a crash, because this is a convenience layer and not an authority.
_DOCS_DIR = Path(__file__).resolve().parents[1] / "documents"
_KNOWN_CACHE: Optional[frozenset] = None


def _known_words() -> frozenset:
    global _KNOWN_CACHE
    if _KNOWN_CACHE is None:
        words: set = set()
        try:
            for path in sorted(_DOCS_DIR.glob("*.txt")):
                text = path.read_text(encoding="utf-8", errors="replace").lower()
                words.update(re.findall(r"[a-z]{3,}", text))
        except Exception:  # pragma: no cover - a missing corpus must not break chat
            words = set()
        _KNOWN_CACHE = frozenset(words)
    return _KNOWN_CACHE


_VOCAB_CACHE: Optional[frozenset] = None


def _build_vocab() -> frozenset:
    """Every word the cue tables in this module actually match on.

    Assembled from the tables themselves so there is no second list to keep in
    step -- adding a cue above automatically makes its spelling correctable.
    """
    words: set = set()

    def add(text: Any) -> None:
        if not isinstance(text, str):
            return
        for w in re.findall(r"[a-z]{3,}", text.lower()):
            words.add(w)

    for cue in _SINGULAR_FIELD_CUES:
        add(cue)
    for table in (_WORD_ORDINALS_EN, _WORD_ORDINALS_TANGLISH, _SLICE_COUNTS,
                  _STATUS_WORDS, _TYPE_WORDS):
        for key in table:
            add(key)
    for _key, needles in _PROJECT_FIELDS:
        for n in needles:
            add(n)
    for phrase, _v in _CHANNEL_PHRASES:
        add(phrase)
    # Words that live inside this module's regexes rather than in a table.
    for extra in (
        "oldest", "newest", "latest", "earliest", "recent", "longest",
        "shortest", "quickest", "fastest", "slowest", "highest", "lowest",
        "largest", "biggest", "smallest", "maximum", "minimum", "total",
        "count", "many", "much", "average", "mean", "both", "each", "every",
        "only", "just", "filter", "keep", "show", "list", "display", "give",
        "details", "detail", "record", "full", "complete", "everything",
        "expand", "elaborate", "more", "info", "information",
        "first", "second", "third", "last", "final", "bottom", "top",
        "initial", "alternate", "even", "odd", "rows", "row", "column",
        "columns", "them", "they", "their", "these", "those", "item",
        "entry", "line", "number", "schedule", "scheduled", "scheduling",
        "visit", "visits", "inspection", "inspections", "site",
        "application", "applications", "approved", "rejected", "pending",
        "escalated", "progress", "overdue",
        # The "is that all?" confirmation cues (`_AGGREGATE_RE`) -- without
        # these, "tahts it" / "thats al" (a transposed or dropped letter, the
        # kind an officer types fast) corrected to nothing, `classify()`
        # returned NONE for the same reason the untransposed spelling used
        # to, and the message fell to the LLM with no grounding again.
        # "that" itself (not just its contraction "thats") for the same
        # reason: "is tht al" -- "that" spelled long-form but fast-typed --
        # left "tht" uncorrected, `_AGGREGATE_RE` never matched, and the turn
        # reached the LLM, which invented an unrelated jurisdiction answer
        # with no table on screen to ground it.
        "thats", "that", "anything", "nothing", "else",
        # The column-exclusion trigger words ("not along ward", "without the
        # status column", "exclude taluk", "remove stage", "hide the ward
        # column") plus their inclusion counterpart "along" -- an officer
        # typing fast drops or transposes a letter here as often as anywhere
        # else in this module, and unlike every other cue table these two
        # words were matched with a plain, non-fuzzy regex, so "alng ward" /
        # "witout ward" / "exclde taluk" corrected to nothing and fell
        # through uncorrected.
        "along", "without", "exclude", "remove", "hide", "skip", "drop", "delete",
        "applicant", "applicants", "unscheduled", "which", "wards", "blocks", "mobile", "status",
        # negation, sort order, deictics and the visit table: a slip in any of these used to
        # leave the fragment unrecognised, and it fell to the model
        "this", "about", "what", "except", "excluding", "excluded", "besides", "dont", "reverse",
        "descending", "ascending", "none", "neither", "completed", "order", "original", "sorting",
        "sorted", "sort", "wrong", "other", "field", "district", "taluk", "town", "minus", "apart",
        # "how do you know these are from SRO" -- the question about how a channel was decided
        "know", "decide", "decided", "determine", "basis", "channel", "source", "registrar", "classify",
        "identify", "proof", "evidence", "reason", "makes", "these", "those", "there", "citizen", "how",
        # the nouns the deterministic handlers key on: a slip in one of them used to reroute the question
        "merged", "merge", "action", "immediate", "proposed", "received", "total", "challan", "treasury",
        "returned", "clarification", "camp", "deed", "patta", "encroachment", "litigation", "overdue",
        "priority", "remarks", "recommendation", "workflow", "jurisdiction", "documents", "surveyor",
        "application", "applications", "applicant", "submitted", "submission", "registered", "signature",
        "certificate", "transfer", "extent", "owner", "owners", "district", "taluk", "village",
        "remark", "remarks", "comment", "comments", "reason", "reasons", "there", "any",
        "survey", "surveys", "today", "tomorrow", "yesterday", "verified", "verify", "office", "approval",
        "flagged", "review", "block", "stand", "conflicts", "conflict", "scheduling", "updated", "completion",
        "required", "requirement", "requirements", "joint", "deadline", "message", "messages", "additional",
        "absent", "draughtsman", "senior", "request", "requested", "stage", "rejection", "approve", "reject",
        "pending", "history", "sketch", "boundary", "measurement", "payment", "receipt", "duration", "delay",
        # Tanglish follow-up words
        "sollu", "kaatu", "kaattu", "enna", "edhu", "evlo", "mattum", "avatroda", "avatrin",
        "modhal", "mudhal", "kadaisi", "irukku", "venuma", "sollunga", "avangaloda",
    ):
        add(extra)
    return frozenset(words)


def _vocab() -> frozenset:
    global _VOCAB_CACHE
    if _VOCAB_CACHE is None:
        _VOCAB_CACHE = _build_vocab()
    return _VOCAB_CACHE


_TAMIL_CUE_WORDS = (
    "அவற்றின்", "அவற்றை", "அவை", "இவை", "பெயர்களை", "பெயர்கள்", "மொபைல்", "எண்கள்", "வார்டு", "பிளாக்",
    "மாவட்டம்", "தாலுகா", "நிலையை", "கட்டணம்", "கட்டணங்களை", "கட்டணங்கள்", "விண்ணப்பம்", "விண்ணப்பங்கள்",
    "விண்ணப்பங்களை", "விண்ணப்பதாரர்", "பழையது", "புதியது", "முதல்", "கடைசி", "இரண்டு", "இரண்டும்",
    "காட்டு", "காட்டுங்கள்", "சொல்லு", "சொல்லுங்கள்", "மட்டும்", "எத்தனை", "மொத்தம்", "அனைத்தும்",
    "முதலாவது", "இரண்டாவது", "மூன்றாவது", "விவரங்கள்", "முகவரி", "தொலைபேசி", "கள ஆய்வுகள்",
    "உரிமையாளர்", "சர்வே", "பட்டா", "வகை", "நிலுவை", "அங்கீகரிக்கப்பட்ட", "நிராகரிக்கப்பட்ட",
)


@lru_cache(maxsize=1024)
def _correct_tamil(message: str) -> str:
    """Tamil tokens one edit (a slipped letter or a dropped vowel sign) away from
    exactly one known cue word are rewritten to it. Only tokens of four or more
    code points, and never a token that is already a cue word."""
    if not message or not re.search(r"[\u0B80-\u0BFF]", message):
        return message
    known = set(_TAMIL_CUE_WORDS)

    def fix(m):
        tok = m.group(0)
        if tok in known or len(tok) < 4:
            return tok
        best = []
        for cand in _TAMIL_CUE_WORDS:
            if " " in cand or abs(len(cand) - len(tok)) > 1:
                continue
            d = damerau_levenshtein_distance(tok, cand)
            if d == 1:
                best.append(cand)
        return best[0] if len(set(best)) == 1 else tok
    return re.sub(r"[\u0B80-\u0BFF]+", fix, message)


# Slips too short for the length-based edit budget, plus "how may" (a real word --
# the month -- so it is only a slip in front of "of them" / "are" / a noun).
_COMMON_SLIPS = {
    "lst": "last", "frist": "first", "fisrt": "first", "secnd": "second", "ther": "their",
    "thier": "their", "thm": "them", "tehm": "them", "thse": "these", "blocs": "blocks",
    "fie": "file", "fiel": "file", "sotr": "sort", "srot": "sort", "onl": "only", "onyl": "only", "oly": "only",
    "ths": "this", "thsi": "this", "thiss": "this", "u": "you", "ur": "your", "aref": "are", "arre": "are",
    "hw": "how", "wad": "ward", "wrd": "ward", "wrad": "ward", "thid": "third", "thrid": "third", "thirdd": "third", "fourh": "fourth", "forth": "fourth", "blck": "block", "blcok": "block", "hwo": "how", "theese": "these", "thes": "these", "thoose": "those", "sya": "say", "sey": "say",
    "knw": "know", "kno": "know", "knwo": "know", "decied": "decide", "decdie": "decide", "basiss": "basis", "bassis": "basis", "nne": "none", "nome": "none", "npne": "none", "lsst": "last", "dnt": "dont", "dnot": "dont", "srt": "sort", "noe": "none", "nnoe": "none",
    "exept": "except", "excpet": "except", "ecept": "except", "excet": "except", "abot": "about", "aobut": "about",
    "laest": "latest", "latst": "latest", "latset": "latest", "lates": "latest", "decending": "descending", "desending": "descending", "acending": "ascending", "thir": "their", "wat": "what", "waht": "what", "whta": "what", "aplications": "applications", "aplication": "application",
}
_HOW_MAY_RE = re.compile(r"\bhow\s+may\b(?!\s+(?:i|we)\b)", re.IGNORECASE)


@lru_cache(maxsize=2048)
def _correct_typos(message: str) -> str:
    """The message with near-miss words rewritten to this module's vocabulary.

    Idempotent and cached, so the many predicates that each call it on the same
    turn cost one pass between them.
    """
    if not message:
        return message
    message = _correct_tamil(message)
    # a slip in "field" in front of "visit(s)": "filed"/"fild"/"fied" are also words or ties, so the distance rule leaves them
    message = re.sub(r"\b(?:filed|fild|fied|feld|feild|fiel|fleid|fiedl)(?=\s+(?:visits?|inspections?)\b)", "field", message, flags=re.IGNORECASE)
    message = _HOW_MAY_RE.sub("how many", message)
    message = re.sub(r"[A-Za-z]+", lambda m: _COMMON_SLIPS.get(m.group(0).lower(), m.group(0)), message)
    vocab = _vocab()

    known = _known_words()

    def fix(m: "re.Match") -> str:
        token = m.group(0)
        low = token.lower()
        if re.match(r"'[a-z]", m.string[m.end():m.end() + 2], re.IGNORECASE):
            return token   # "don't" / "isn't": the stem before the apostrophe is not a typo of "dont"
        # Already a cue, a protected common word, or a word the department's own
        # corpus uses: it is not a typo of anything, whatever it sits close to.
        if low in vocab or low in _NEVER_CORRECT or low in known:
            return token
        # a doubled key ("visitt", "whaat", "soort"): collapsing ONE repeated letter gives a cue word
        for _i in range(1, len(low)):
            if low[_i] == low[_i - 1] and low[:_i] + low[_i + 1:] in vocab:
                return low[:_i] + low[_i + 1:]
        hits = []
        for candidate in vocab:
            if abs(len(candidate) - len(low)) > 2:
                continue
            # A candidate this token is a genuine plural/singular OF is not a
            # typo target -- but that only rules out THIS candidate, not
            # every candidate. "typ" happens to be "types" minus its suffix,
            # which used to abort the whole search here before "type" (edit
            # distance 1, the far better explanation) was ever tried, so
            # "application no with typ and status" silently lost the Type
            # column. A genuine plural like "pattas" still ends up
            # unchanged: nothing else in the vocabulary is a closer match to
            # a real, correctly-spelled word, so `hits` stays empty below.
            if _is_inflection(low, candidate):
                continue
            if is_token_typo_match(low, candidate):
                hits.append((damerau_levenshtein_distance(low, candidate),
                             candidate))
        if not hits:
            return token
        hits.sort()
        # Two vocabulary words equally close is not a correction, it is a
        # guess between two meanings. Leave the token exactly as typed.
        if len(hits) > 1 and hits[0][0] == hits[1][0]:
            # A tie is only broken by length: "detaisl" is a transposition of
            # "details" (same length) and a dropped letter of "detail" -- the
            # transposition is the far commoner slip.
            same_len = [h for h in hits if h[0] == hits[0][0] and len(h[1]) == len(low)]
            if len(same_len) == 1:
                return same_len[0][1]
            return token
        return hits[0][1]

    return _ASCII_WORD_RE.sub(fix, message)


# Public name. `chatbot.py` keeps its own copy of the follow-up cues for the
# no-stored-context fallback path, and needs the same correction pass -- but
# only to DECIDE with. It must never substitute the corrected text for the
# officer's own words, which travel on to handlers with vocabularies of their
# own.
correct_spelling = _correct_typos

# Words that make a fragment a question about one record's field.
_SINGULAR_FIELD_CUES = (
    "name", "applicant", "mobile", "phone", "address", "status", "stage",
    "ward", "block", "district", "taluk", "town", "survey", "patta", "can",
    "area", "extent", "fee",
    "amount", "channel", "deed", "reason", "type", "date", "when", "who",
    "owner", "subdivision", "sub-division", "document", "scheduled",
    "how long", "since when", "how many days", "why",
    "approved", "rejected", "overdue", "igrs", "schedul",
    # "and the ip?" / "is that ip internal?" -- the submitting IP of the file in view
    " ip", "ip address", "ip முகவரி",
    # submission channel / camp: "how was it submitted?", "was it a revenue
    # camp?", "which channel was it filed through?" -- all follow-ups about the
    # file in view, answered from applications.submission_* by chatbot's
    # _asked_submission_channel_field branch.
    "submitted", "submission", "filed", "camp", "csc", "sub registrar",
    "sub-registrar", "sro",
    # Source-extract field nouns. A bare "what is the tax rate?" / "the plot
    # number?" / "its land use?" follow-up carried none of the cues above, so it
    # classified as NONE, lost the application reference, and fell to the LLM --
    # which then invented a value. With the reference kept, the deterministic
    # "not in your SIS register" handler answers it instead.
    "tax", "soil", "plot", "adopted", "land use", "waste", "govern",
    "classification", "group",
    # Parcel-register (urban_parcel_register) columns the survey projection
    # drops. A bare "what is the irrigation source?" / "is it double crop?" /
    # "the form 6 number?" / "is it cultivable?" follow-up after a survey
    # answer carried none of the cues above, classified as NONE, lost the
    # survey reference, and fell to the LLM. With the reference kept, the
    # deterministic "not in your SIS register" parcel handler answers it.
    "irrigation", "crop", "partition", "priority", "form 6", "form 7",
    "form 8", "form6", "form7", "form8", "relinquish", "alienat", "acquisit",
    "assess", "cultivab", "door", "street", "assign",
    # Patta-transfer / registration detail: "why was it transferred?", "where
    # was the deed registered?", "what were the order remarks?", "what was the
    # SIS recommendation?" -- bare follow-ups that carried none of the cues
    # above and fell to the LLM.
    # "was it returned?" / "has it been sent back?" -- a bare follow-up about
    # the file in view. It carried no cue, classified as NONE, lost the
    # reference and fell to the LLM, which answered *"Yes, it was returned."*
    # about a record that says no such thing. With the reference kept, the
    # `_UNTRACKED_APPINFO_FIELDS` return_status handler answers it.
    "returned", "return status", "sent back", "திருப்பி", "திரும்ப",
    "transfer", "transferred", "registered", "registration", "deed",
    "remarks", "remark", "recommendation", "recommended", "order",
    "மாற்ற", "பதிவு", "பரிந்துரை", "குறிப்பு", "ஆணை",
    # Owner (urban_natham_chitta_owner) sub-fields. A bare "what is the gender?"
    # / "the relationship?" / "the aadhaar number?" / "the ownership share?"
    # follow-up after a joint-owner or survey-ownership answer carried none of
    # the cues above -- it classified as NONE, lost the survey/application
    # reference, and fell to the LLM, which invented a value. "name" and "owner"
    # already covered "the owner's name" / "who is the owner"; these cover the
    # rest of the register's owner columns.
    "gender", "sex", "relationship", "relative", "father", "husband", "wife",
    "spouse", "aadhaar", "aadhar", "share", "ownership",
    # Owner columns the register does NOT project (ration card, voter ID/EPIC,
    # PIN, occupation code, CIN, assignment number, owner/relation serial,
    # user number). A bare "what is the ration card number?" / "the occupation
    # code?" follow-up carried none of the cues above, lost the reference, and
    # fell to the LLM. Kept, the deterministic "not in your SIS register" owner
    # handler answers it. "door" / "assign" are already in the list above.
    "ration", "voter", "epic", "election id", "pin code", "pincode",
    "occupation", "cin ", "cin_", "citizen identification",
    "ரேஷன்", "குடும்ப அட்டை", "வாக்காளர்", "பின் கோடு", "பின்கோடு", "தொழில்",
    # Tamil. Substrings, for the reason given on _OWN_SUBJECT_RE; inflection
    # makes token equality unreliable, so these are the stems that carry the
    # meaning.
    "பெயர்", "நிலை", "வார்டு", "மாவட்ட", "தாலுக", "நகர",
    "தேதி", "கட்டணம்", "முகவரி", "எப்போது",
    "அங்கீகரி", "நிராகரி", "திட்டமிட", "ஆய்வு", "யார்", "எங்கே", "கணக்கெண்",
    "பட்டா", "உரிமையாளர்", "தொகை", "வகை",
    # Tamil field nouns that were missing, so a bare Tamil follow-up
    # ("பரப்பளவு என்ன?", "சர்வே எண் என்ன?") classified as NONE and lost the
    # application reference the English form ("what is the area?") kept.
    "பரப்பளவு", "பரப்பு", "விஸ்தீரண", "சர்வே", "குறியீடு", "கிரய", "பத்திர",
    "தொலைபேசி", "கைபேசி",
    # Tamil owner sub-field stems: gender, relationship, husband, wife, father,
    # aadhaar, ownership share -- the Tamil forms of the English owner cues
    # added above, so "பாலினம் என்ன?" / "பங்கு என்ன?" keep the reference too.
    "பாலின", "உறவு", "கணவ", "மனைவி", "தந்தை", "ஆதார", "பங்கு",
    # Tamil parcel-register stems: irrigation, crop, partition, form, door,
    # street, tax-rate -- the Tamil forms of the parcel cues added above.
    "நீர்ப்பாச", "பாசன", "பயிர்", "பிரிவினை", "படிவம்", "கதவு", "தெரு",
    "வரி விகித", "மொத்த வரி",
    # Tanglish, as officers type it
    "enna", "eppo", "eppothu", "yaaru", "yaar", "engay", "yenga", "naal",
    # Tamil "how long / how many days": "இதற்கு எவ்வளவு நாள் ஆகும்?"
    "நாள்", "காலம்", "எவ்வளவு நேரம்",
    "peru", "parappu", "parappalavu", "vistheeranam",
    "paalinam", "uravu", "uravumurai", "kanavan", "manaivi", "thanthai", "pangu",
)

_AGGREGATE_RE = re.compile(
    r"\bhow\s+many\b|\bcount\b|\btotal\b|\bsum\b|\baverage\b|\bmean\b"
    r"|\bwhich\s+(?:one|ones|is|are|of)\b|\boldest\b|\bnewest\b|\blatest\b"
    r"|\bearliest\b|\bmost\s+recent\b|\bany\s+of\b|\bboth\b|\ball\s+of\s+them\b"
    # Superlatives over the rows on screen. "which took the longest?" after a
    # listing carried none of these cues -- "which took" does not match
    # "which (one|ones|is|are|of)" -- so it classified as NONE, lost the list,
    # and reached llama3.1:8b, which spent 78-98 SECONDS naming the wrong
    # application (the deterministic comparison handler puts the longest at 63
    # days; the model answered 14, and for the other officer named a file
    # while saying nothing about duration at all).
    r"|\blongest\b|\bshortest\b|\bquickest\b|\bfastest\b|\bslowest\b"
    r"|\bhighest\b|\blowest\b|\blargest\b|\bbiggest\b|\bsmallest\b"
    r"|\bmaximum\b|\bminimum\b|\btook\s+the\s+most\b"
    r"|நீண்ட|அதிக|குறைந்த"
    # Tanglish. "evlo"/"evvalavu" belong here rather than in the singular
    # cues: "evlo approved?" is a count over the list, and "approved" alone
    # would otherwise make it a one-record question.
    r"|\bevlo\b|\bevvalavu\b|\bethanai\b|\bethana\b|\bmottham\b"
    r"|\bpazhusu\b|\bpazha(?:i)?y(?:a|adhu|athu)\b|\bputhusu\b|\bpudhusu\b|\bpu(?:th|dh)iy(?:a|adhu|athu)\b"
    r"|எத்தனை|மொத்த|பழைய|சமீபத்திய|புதிய"
    # "that's all?", "is that all?", "anything else?", "nothing else?" -- a
    # confirmation-seeking follow-up about the COMPLETENESS of the list just
    # shown, not a fresh question. Unrecognised, this reached llama3.1:8b with
    # no grounding at all and it invented an application number, a type and a
    # date wholesale -- a table already on screen contradicted every word of
    # it. Landing here reuses the same count-confirmation the generic
    # aggregate branch already renders ("1 of those 30 CSC application(s) is
    # pending"), carrying forward whatever status/type/channel scoped the
    # PREVIOUS turn, so the answer restates the true count instead of
    # guessing at a new one.
    # "al" alongside "all" -- a dropped trailing letter ("is that al?") is
    # the same fast-typing shape "thats al" was already fixed for, just with
    # "that" spelled long-form so it reaches this regex uncontracted.
    r"|\bthat'?s\s+al{1,2}\b|\bis\s+that\s+al{1,2}\b|\banything\s+else\b"
    r"|\bnothing\s+else\b|\bthat'?s\s+it\b|\bis\s+that\s+it\b",
    re.IGNORECASE,
)

_REFINE_RE = re.compile(
    r"\b(?:show|list|give|filter|keep)\s+(?:me\s+)?(?:only|just)\b"
    r"|\bonly\s+(?:the\s+)?(?:isd|nisd|merge|approved|rejected|pending|overdue)\b"
    r"|\bjust\s+(?:the\s+)?(?:isd|nisd|merge|approved|rejected|pending|overdue)\b"
    r"|\bwhat\s+about\s+(?:the\s+)?(?:isd|nisd|merge)\b"
    # "show the pending one(s)" -- no "only"/"just", but "the <status>
    # one(s)" is the same request: filter the list just shown to that
    # status. Without this it matched nothing here, `_has_own_subject`
    # didn't claim it either, and it fell through to intent routing, which
    # read "pending" on its own as a complete, self-contained request and
    # re-ran the officer's WHOLE unscoped queue -- silently dropping the
    # channel/type scope of the list the officer was actually pointing at.
    r"|\b(?:show|list|give|find)\s+(?:me\s+)?the\s+"
    r"(?:isd|nisd|merge|approved|rejected|pending|overdue)\s+one(?:s)?\b"
    r"|\bmattum\b|\bmattuma\b"
    r"|மட்டும்",
    re.IGNORECASE,
)

# ── Picking a row of the table by position ──────────────────────────────────
# "the second one", "what abt the 2nd one", "row 7", "number 12", "the last
# one", and the Tamil / Tanglish equivalents. `classify` used to return NONE
# for all of these, so the fragment fell through to the agent layer, which
# spent the whole LLM budget on a question it could not ground and timed the
# stream out (the reported crash). ANY position is understood, not only 1-5:
# `ordinal_index()` returns the number named, `ordinal_pick()` maps it onto the
# carried list, and `resolve()` asks when it points past the end.
#
# Bare "last" / "final" is deliberately NOT an ordinal cue -- it collides with
# "last month" / "last application" (the "last" trap CLAUDE.md documents for the
# comparison parser); only "last one" / "the last?" is. Tamil keeps the
# unambiguous compound ordinals (`முதலாவது`, `இரண்டாவது`, …), never bare
# `முதல்` / `கடைசி`, which are ordinary words.
_WORD_ORDINALS_EN = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12,
}
_WORD_ORDINALS_TANGLISH = {
    "mudhalaavadhu": 1, "mudhalavathu": 1, "rendaavadhu": 2, "rendavathu": 2,
    "moonraavadhu": 3, "moondravathu": 3, "naalaavadhu": 4, "naangaavadhu": 4,
    "anjaavadhu": 5, "aaraavadhu": 6, "ezhaavadhu": 7,
    # "modhal file", "mudhal application" -- "first file" as officers type it.
    "modhal file": 1, "mudhal file": 1, "mudal file": 1, "muthal file": 1,
    "modhal application": 1, "mudhal application": 1, "modhal one": 1,
    "mudhal one": 1, "muthal one": 1,
}
# Tamil ordinals, matched as substrings (virama: see _OWN_SUBJECT_RE).
_WORD_ORDINALS_TA = {
    "முதலாவது": 1, "இரண்டாவது": 2, "மூன்றாவது": 3, "நான்காவது": 4,
    "ஐந்தாவது": 5, "ஆறாவது": 6, "ஏழாவது": 7, "எட்டாவது": 8,
    "ஒன்பதாவது": 9, "பத்தாவது": 10,
    # "the first application / file / one" as a phrase; a bare "முதல்" is also
    # "from", so it is only an ordinal next to its noun.
    "முதல் விண்ணப்பம்": 1, "முதல் கோப்பு": 1, "முதல் ஒன்று": 1,
}
# digit + ordinal suffix ("7th", "2nd"); a positional noun + number ("row 7",
# "number 12", "entry 3", "line 5", "sl no 6"); or the Tamil / Tanglish
# "<n>வது" / "<n>vadhu".
_NUM_ORDINAL_RE = re.compile(
    r"\b(\d{1,3})\s*(?:st|nd|rd|th)\b"
    r"|(?<!survey )(?<!patta )(?<!application )(?<!ward )(?<!block )(?<!door )(?<!street )(?<!town )"
    r"(?<!can )(?<!mobile )(?<!phone )(?<!contact )(?<!district )(?<!taluk )(?<!code )(?<!sub-division )"
    r"\b(?:row|number|no|num|entry|item|record|line|sl\.?\s*no\.?)\s*[:#]?\s*(\d{1,3})\b"
    r"|\b(\d{1,3})\s*-?\s*(?:வது|vadhu|vathu)",  # no trailing \b: Tamil ு is not a word char
    re.IGNORECASE,
)
# "the last one", "what about the last?", the Tamil / Tanglish "last one".
_LAST_ONE_RE = re.compile(
    r"\b(?:last|latest|final)\s+one\b"
    r"|\bthe\s+(?:last|final)\b\s*[?.!]*$"
    r"|கடைசியது|கடைசி\s*ஒன்(?:று|னு)"
    r"|\bkadaisi\s+one\b",
    re.IGNORECASE,
)


def ordinal_index(message: str) -> Optional[int]:
    """The 1-based row position the officer named ("the 7th one" -> 7), or None.

    Any N is understood. "last" has no fixed index and is handled separately by
    `ordinal_pick` / `_LAST_ONE_RE`.
    """
    if not message:
        return None
    message = _correct_typos(message)
    lowered = message.lower()
    for word, idx in _WORD_ORDINALS_EN.items():
        if re.search(rf"\b{word}\b", lowered):
            return idx
    for word, idx in _WORD_ORDINALS_TANGLISH.items():
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return idx
    for word, idx in _WORD_ORDINALS_TA.items():
        if word in message:
            return idx
    m = _NUM_ORDINAL_RE.search(lowered)
    if m:
        for g in m.groups():
            if g:
                n = int(g)
                return n if 1 <= n <= MAX_CARRIED else None
    return None


# ── Picking a RUN of rows: "the first two", "last three", "both" ────────────
# `ordinal_pick` above answers "the 2nd one" -- one row. An officer looking at a
# ten-row table just as often wants the top of it ("show the first two"), and
# looking at a two-row one says "show both". Neither was understood: "first
# two" matched the word "first" and answered about row 1 alone, and "show both
# applications" was not treated as a follow-up at all, so both questions were
# answered by re-running the officer's whole open queue.
_SLICE_COUNTS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "couple": 2, "few": 3,
    "rendu": 2, "moonu": 3, "moondru": 3, "naalu": 4, "anju": 5,
    "இரண்டு": 2, "மூன்று": 3, "நான்கு": 4, "ஐந்து": 5,
}
_HEAD_WORDS = ("first", "top", "initial", "earliest", "mudhal", "முதல்")
_SLICE_RE = re.compile(
    r"\b(first|top|initial|earliest|last|final|bottom|latest)\s+(?:the\s+)?"
    r"(\d{1,3}|two|three|four|five|six|seven|eight|nine|ten|couple|few)\b"
    # "last 7 days" / "last two weeks" / "first 3 months" name a period of time.
    r"(?!\s*(?:days?|weeks?|months?|years?|yrs?|hours?|minutes?|quarters?)\b)",
    re.IGNORECASE,
)
# Tamil / Tanglish put the count after the head word too, but Tamil is matched
# as a substring (virama -- see _OWN_SUBJECT_RE).
_SLICE_TA_RE = re.compile(
    r"(முதல்|கடைசி)\s*(இரண்டு|மூன்று|நான்கு|ஐந்து|\d{1,3}(?![\d-]))"
    r"|\b(mudhal|kadaisi)\s+(rendu|moonu|moondru|naalu|anju|\d{1,3})\b",
    re.IGNORECASE,
)
_BOTH_RE = re.compile(r"\bboth\b|இரண்டும்|\brendume\b", re.IGNORECASE)

# "show me all of them", "list them all" -- the whole table again, as records.
# It has to point BACK ("them" / "these"): a bare "show all applications" is a
# request for the register, and `_OWN_SUBJECT_RE` deliberately keeps it one.
_ALL_ROWS_RE = re.compile(
    r"\ball\s+of\s+(?:them|these|those)\b|\bthem\s+all\b"
    r"|\bevery\s*one\s+of\s+(?:them|these|those)\b|\beach\s+of\s+(?:them|these|those)\b"
    r"|அனைத்தையும்|எல்லாவற்றையும்",
    re.IGNORECASE,
)
# Alternate rows. "even" is positions 2, 4, 6…, "odd" is 1, 3, 5… -- the rows as
# they were NUMBERED on screen, which is the only reading that survives the
# officer counting down the table with a finger.
_EVEN_ROWS_RE = re.compile(r"\beven\s+(?:rows?|ones?|numbers?)\b|\beven\b(?=\s|$)",
                           re.IGNORECASE)
_ODD_ROWS_RE = re.compile(r"\bodd\s+(?:rows?|ones?|numbers?)\b|\bodd\b(?=\s|$)",
                          re.IGNORECASE)
_ALTERNATE_RE = re.compile(r"\balternate\s+(?:rows?|ones?)\b|\bevery\s+second\s+(?:row|one)\b",
                           re.IGNORECASE)
# A request to SEE the records, as opposed to ask something about them. "are all
# of them approved?" names the same rows and wants a yes/no, so a show verb is
# required and an interrogative disqualifies.
_SHOW_VERB_RE = re.compile(
    # "i need all" / "i want all of them" ask for the records just as plainly as
    # "show" does -- and it is what an officer types when the assistant has just
    # asked them "which one do you mean?". Without these it fell to the LLM and
    # came back with a COUNT ("There are 30 applications from CSC.") in place of
    # the list, and a count that disagreed with the 24 rows the clarification
    # had just named.
    r"\b(?:show|list|display|give|open|expand|view|see|print|need|want|send)\b"
    r"|காட்டு|பட்டியல்|வேண்டும்",
    re.IGNORECASE,
)
# A BARE "all" -- no noun of its own. "i need all", "all please", "all of
# them". It can only mean the rows in view, which is what separates it from
# "show all applications": that one names the register and `_OWN_SUBJECT_RE`
# deliberately keeps it a fresh question.
_BARE_ALL_RE = re.compile(
    r"^(?:i\s+)?(?:need|want|give\s+me|show\s+me|send)?\s*(?:the\s+)?all"
    r"(?:\s+of\s+(?:them|these|those))?\s*(?:please|pls)?\s*[.!?]*$",
    re.IGNORECASE,
)
_INTERROGATIVE_RE = re.compile(
    r"\bhow\s+many\b|\bcount\b|\btotal\b|\bwhich\b|\bwhat\b|\bany\b"
    r"|\bare\s+(?:they|all|both)\b|\bis\s+it\b|எத்தனை|மொத்த",
    re.IGNORECASE,
)


def _wants_the_records(lowered: str) -> bool:
    return bool(_SHOW_VERB_RE.search(lowered)
                and not _INTERROGATIVE_RE.search(lowered))


def _slice_count(token: str) -> Optional[int]:
    t = (token or "").strip().lower()
    if t.isdigit():
        return int(t)
    return _SLICE_COUNTS.get(t)


def _alternate_row_pick(lowered: str, numbers: List[str]) -> Optional[List[str]]:
    """Every other row the message names ("even rows", "odd rows", "alternate
    rows"), by position, or None when it names none of those.

    Split from `slice_pick` so `resolve()` can also reach it directly: on a
    short list "even rows" only ever picks ONE row (index 1 of a 2-row
    list), which `slice_pick`'s own size floor correctly refuses to call a
    "run" -- but nothing else picked that one row up either, and "show
    applications in even rows" over a 2-row list answered the ambiguous
    "which one do you mean?" clarification for a request that named exactly
    one row all along.
    """
    if _ALTERNATE_RE.search(lowered) or _EVEN_ROWS_RE.search(lowered) \
            or _ODD_ROWS_RE.search(lowered):
        return (list(numbers[1::2])
                if _EVEN_ROWS_RE.search(lowered) and not _ODD_ROWS_RE.search(lowered)
                else list(numbers[::2]))
    return None


def slice_pick(message: str, numbers: List[str]) -> Optional[List[str]]:
    """The contiguous run of listed rows the officer named, in the order shown.

    "the first two" -> rows 1-2, "last three" -> the final three, "both" -> the
    whole list when it holds exactly two. Returns None when no run was named, or
    when the count is 1 -- a single row is `ordinal_pick`'s job, and answering it
    here would turn "the first one" into a list.
    """
    if not numbers or not message:
        return None
    message = _correct_typos(message)
    lowered = message.lower()

    # "both" says how many there are as well as which: against a list that does
    # not hold exactly two it is not a slice, and is left to the aggregate
    # branch rather than guessed at.
    if _BOTH_RE.search(lowered) or _BOTH_RE.search(message):
        return list(numbers) if len(numbers) == 2 else None

    # "show me all of them" / "list them all" -- every row, as records. Only
    # when the officer asked to SEE them: "are all of them approved?" points at
    # the same rows and wants a yes/no, which the aggregate branch answers.
    if ((_ALL_ROWS_RE.search(lowered) or _ALL_ROWS_RE.search(message))
            and _wants_the_records(lowered)) or _BARE_ALL_RE.match(lowered.strip()):
        # Past the per-row ceiling, a wall of records is worse than the count
        # the aggregate branch gives -- and truncating to the first ten while
        # the officer asked for ALL would be a silently wrong answer.
        return list(numbers) if len(numbers) <= MAX_PER_ROW_ANSWER else None

    # Alternate rows, by the position each row was shown at.
    _alt = _alternate_row_pick(lowered, numbers)
    if _alt is not None:
        if len(_alt) < 2 or len(_alt) > MAX_PER_ROW_ANSWER:
            return None
        return _alt

    head = count = None
    m = _SLICE_RE.search(lowered)
    if m:
        head, count = m.group(1).lower(), _slice_count(m.group(2))
    else:
        m = _SLICE_TA_RE.search(message) or _SLICE_TA_RE.search(lowered)
        if m:
            groups = [g for g in m.groups() if g]
            if len(groups) == 2:
                head, count = groups[0].lower(), _slice_count(groups[1])

    if head is None or count is None or count < 2:
        return None
    count = min(count, len(numbers), MAX_PER_ROW_ANSWER)
    if count < 2:
        return None
    return (list(numbers[:count]) if head in _HEAD_WORDS
            else list(numbers[-count:]))


def mentions_slice(message: str) -> bool:
    """True when the message names a RUN of rows ("the first two", "last three").

    Needed separately from `slice_pick` because `classify` runs before any list
    is in hand. "first two" already reached `resolve` on the strength of the
    ordinal "first"; "last two" carries no ordinal at all, so without this it
    tripped the own-subject gate on "applications" and was routed as a fresh
    question.
    """
    if not message:
        return False
    message = _correct_typos(message)
    lowered = message.lower()
    return bool(_SLICE_RE.search(lowered)
                or _SLICE_TA_RE.search(message) or _SLICE_TA_RE.search(lowered)
                or _ALTERNATE_RE.search(lowered)
                or _EVEN_ROWS_RE.search(lowered) or _ODD_ROWS_RE.search(lowered)
                or ((_ALL_ROWS_RE.search(lowered) or _ALL_ROWS_RE.search(message))
                    and _wants_the_records(lowered))
                or _BARE_ALL_RE.match(lowered.strip()))


def mentions_ordinal(message: str) -> bool:
    """True when the message picks a row by position ("the 2nd one", "row 7").

    `_load_followup_context` uses this to prefer a stored *list* context over a
    more recent single-application one: after "show details for <A>" the officer
    who types "what about the 2nd one?" means the second row of the list they
    saw, not the file they just opened.
    """
    # `_correct_typos` first, to match `ordinal_pick`'s own check -- without
    # it, "remove the las one" (a dropped letter) passed `ordinal_pick`
    # (which does correct first) but failed `mentions_ordinal` (which did
    # not), so `classify()` and `resolve()` disagreed on the very same
    # message: classify() called it NONE and resolve() was never reached.
    return (ordinal_index(message) is not None
            or bool(_LAST_ONE_RE.search(_correct_typos(message or "").lower())))


def names_a_field(message: str) -> bool:
    """True when the follow-up names a record field / event it wants.

    "what is the area of the 2nd one?" does; a bare "what abt the 2nd one?"
    does not. `_apply_followup_resolution` in chatbot.py uses this to decide
    whether a positional follow-up should be rewritten into a plain
    "details of <num>" request or keep its field word for the field map.

    A bare cue-word substring match alone is not enough: "csc" is a genuine
    field cue (a row's submission channel is a real per-row field, "what is
    the channel of the 2nd one" should keep it) but it is ALSO how a channel
    is named in an ordinary listing IMPERATIVE -- "show applications from
    csc" carried no question at all, yet matched "csc" and was treated as if
    IT were the field question to re-ask for a different row. Same fragment/
    question-shape test `classify()`'s own cue branch uses for the identical
    failure mode.
    """
    if not message:
        return False
    message = _correct_typos(message)
    lowered = message.lower()
    if not any(cue in lowered or cue in message for cue in _SINGULAR_FIELD_CUES):
        return False
    words = message.split()
    # "show"/"list"/"give"/"display" are LISTING verbs, not field-lookup
    # ones -- "show applications from csc" is the imperative that names the
    # scope the classify()-side `_FRAGMENT_OPENERS` set exists to admit for
    # a genuine short field fragment ("show me the ward"), and admitting it
    # here too let this exact sentence back in through the opener clause
    # even after the length check correctly rejected its 4 words.
    _question_openers = _FRAGMENT_OPENERS - {"show", "give", "list", "and", "also"}
    return (len(words) <= _MAX_FRAGMENT_WORDS or "?" in message
            or (words and words[0].strip(".,!").lower() in _question_openers))


def _looks_like_bare_field_followup(message: str) -> bool:
    """A short fragment that asks for a field / points back, without naming a
    subject of its own. Used only when a transfer-party context is in view, to
    rescue "what is their gender?" / "and the father's name?" -- which
    `classify` calls NONE because "their" is a plural pointer."""
    text = _correct_typos((message or "").strip())
    if not text or len(text.split()) > _MAX_FOLLOWUP_WORDS:
        return False
    if _has_own_subject(text.lower()):
        return False
    return (names_a_field(text)
            or bool(_BACKREF_RE.search(text.lower()))
            or bool(_PLURAL_BACKREF_RE.search(text.lower())))


# ── Reading one field down a listed column ──────────────────────────────────
# "their wards", "the status of each", "the CAN number column", "list the
# survey numbers" -- after a listing, a request to see ONE field for every row
# shown. Rendered by `_project_field_answer` in chatbot.py over exactly the
# carried rows, re-read under the officer's jurisdiction like every other list
# follow-up.
_PROJECT_FIELDS = (
    # Plural forms are listed explicitly, not relied on via substring
    # containment: `_needle_hit` matches ASCII needles on a real word
    # boundary (see its own docstring -- "applicant" contains "can", so a
    # bare substring match on "can" mistook every applicant-name question
    # for a CAN-number one), and a word boundary does not fall inside a
    # plural "wards"/"blocks" the way a substring check silently did.
    ("ward", ("ward", "wards", "வார்டு")),
    ("block", ("block", "blocks", "பிளாக்")),
    ("district", ("district", "districts", "மாவட்டம்")),
    ("taluk", ("taluk", "taluks", "தாலுகா")),
    ("town", ("town", "towns", "நகரம்")),
    ("status", ("status", "statuses", "நிலை")),
    ("stage", ("stage", "stages", "கட்டம்")),
    ("application_type", ("isd or nisd", "type", "types", "வகை")),
    ("survey_no", ("survey number", "survey no", "survey numbers", "survey",
                   "புல எண்", "சர்வே")),
    ("can_number", ("can number", "can numbers", "can no", "can", "கணக்கெண்")),
    ("submission_channel", ("submission channel", "channel", "channels", "வழி")),
    ("applicant_name", ("applicant name", "applicant", "applicants", "owner",
                        "owners", "names", "பெயர்கள்", "பெயர்களை", "விண்ணப்பதாரர்")),
    ("applicant_mobile", ("mobile", "phone", "contact number", "மொபைல்")),
    ("fee_amount", ("fee", "fees", "amount", "charge", "கட்டணம்", "கட்டணங்கள்",
                    "கட்டணங்களை", "கட்டணத்தை")),
    # Checked ahead of submission_date -- "last updated dates" contains the
    # generic "dates" needle below, which used to claim it for submission_date
    # instead (wrong field, silently) or, worse, let a bare "last updated
    # dates" (no "when") fall through to the single-application field lookup
    # and answer for just one of several rows on screen.
    ("last_updated_date", ("last updated", "last update", "last modified",
                           "modified date", "modified on", "updated on",
                           "last updated date", "last updated dates",
                           "update date", "update time",
                           "கடைசியாக புதுப்பிக்கப்பட்ட", "புதுப்பிக்கப்பட்ட தேதி")),
    ("submission_date", ("submission date", "submitted date", "filed date",
                         "applied date", "date filed", "date submitted",
                         "date", "dates", "submitted", "submission",
                         "சமர்ப்பித்த தேதி", "தேதி", "தேதிகள்", "தேதியை")),
    ("igrs_form6_number", ("igrs number", "igrs", "form 6", "form6")),
    ("subdivisions", ("sub-divisions", "sub divisions", "subdivisions",
                      "உட்பிரிவுகள்")),
)

# "along" ("along with district") -- see `field_projections`'s own use of
# this for why it gets the standard listing columns prepended instead of
# narrowing to just the named field(s), unlike every other trigger.
_ALONG_RE = re.compile(r"\balong\b|\badd(?:ing)?\b|\bserth\w*\b|\bsethu\w*\b|\bcherthu\w*\b|சேர்|\badhuvodu\b|\badhu\s*udan\b", re.IGNORECASE)

# The opposite request -- "not along ward", "without the status column",
# "exclude taluk", "hide the ward column", "remove stage" -- drop a column
# from what is on screen rather than add one. Checked as a WHOLE-message
# prefix pattern for "not along" (so "not along ward" isn't first read as an
# ordinary "along" inclusion, which is what "along" alone would match), and
# as bare trigger words otherwise. "remove"/"without" carry other meanings
# in an unrelated sentence, but `field_projections` only ever runs once
# `classify()` has already decided the turn is a follow-up over a carried
# list, so the blast radius is a listing already on screen, not a fresh
# question.
_EXCLUDE_TRIGGER_RE = re.compile(
    r"\bnot\s+along\b|\bnot\s+with\b|\bwithout\b|\bexclude\b|\bremove\b|\bhide\b|\bskip\b"
    r"|\bdrop\b|\bdelete\b"
    r"|\bexcept\b|\bapart\s+from\b|\bother\s+than\b|\bbut\s+not\b|\beverything\s+(?:but|except)\b|\ball\s+(?:but|except)\b"
    r"|\bnot\s+(?:the\s+)?(?=(?:first|second|third|fourth|fifth|last|\d+(?:st|nd|rd|th)?)\b)"
    r"|\bdon'?t\s+show\b|\bdo\s+not\s+show\b|\bno\s+need\s+(?:of|for)\b"
    # "no ward", "no status column", "not stage" -- a bare column name after no / not takes that column away
    r"|^\s*(?:no|not)\s+(?:the\s+)?(?=(?:ward|block|district|taluk|town|status|stage|type|survey|fee|name|applicant|"
    r"mobile|date|channel|can|patta|sub[\s-]?divisions?)s?\b)"
    # "display" is the same verb as "show" for this purpose -- "don't
    # display the second application" is exactly "don't show" it. Scoped to
    # the negated form specifically, not bare "display", which elsewhere
    # (`_PROJECT_TRIGGER_RE`'s absence of it, and every listing trigger that
    # already treats "display" as a synonym of "show") means the opposite:
    # an inclusion request.
    r"|\bdon'?t\s+display\b|\bdo\s+not\s+display\b"
    # Tanglish, as officers type it: "ward illama" (space) AND "wardilama" /
    # "adhuilama" (fused into one word, which is at least as common a way to
    # type it) -- \billama\w*\b needs a word boundary right before "illama",
    # which a fused compound never has ("adhuilama" has no boundary between
    # "adhu" and "illama"), so \w*illama\w*\b -- no leading boundary
    # required -- catches both shapes. "adhu illama" (space) is also spelled
    # out for "not along/without THAT [column]" specifically, the Tanglish
    # counterpart of "not with"/"without" rather than a per-field word.
    r"|\w*illama\w*\b|\bvenda\w*\b|\badhu\s*illama\b"
    r"|இல்லாமல்|வேண்டாம்",
    re.IGNORECASE,
)

# "show more applications" / "less kami" / "more kami" -- a bare, numberless
# request for a bigger or smaller result set. Scoped to a SHORT fragment
# (optionally with "show"/"applications"/"apps"/"kami"/"kaami"/"please"
# around the bare "more"/"less") so a real sentence that happens to contain
# either word ("show applications that took more than 30 days") is untouched
# -- that already has its own meaning and its own handler.
_VAGUE_COUNT_RE = re.compile(
    r"^(?:show\s+)?(?:me\s+)?(?:some\s+)?(?:more|less|fewer)"
    r"(?:\s+(?:applications?|apps?|files?|kami|kaami|please))*\s*\??$",
    re.IGNORECASE,
)

# Application number, survey number and sub-division number identify the ROW
# itself -- there is no other way to tell which application a line in the
# table is about. Asked to drop one of these, the honest answer is that it
# cannot be done, not a table that silently forgets to say which application
# is which.
_MANDATORY_FIELD_LABELS = (
    ("application number", ("application no", "app no", "application number",
                            "app number", "விண்ணப்ப எண்")),
    ("survey number", ("survey no", "survey number", "survey numbers",
                       "சர்வே எண்", "புல எண்")),
    ("sub-division number", ("sub division", "sub-division", "subdivision",
                             "sub divisions", "subdivisions", "உட்பிரிவு")),
)


def excluded_mandatory_field(message: str) -> Optional[str]:
    """The mandatory row-identity column the officer asked to drop, or None.

    Checked by the caller BEFORE `field_projections()` -- a refusal, not a
    silently-complied-with or silently-ignored request. See
    `_MANDATORY_FIELD_LABELS`.
    """
    if not message:
        return None
    message = _correct_typos(message)
    lowered = message.lower()
    if not _EXCLUDE_TRIGGER_RE.search(lowered):
        return None
    for label, needles in _MANDATORY_FIELD_LABELS:
        for n in needles:
            if _needle_hit(n, lowered, message):
                return label
    return None

# The columns `table_renderer.js` shows for an ordinary application listing
# (frontend/js/table_renderer.js's `engCols` for this shape), in that same
# order. "along with X" means "what I already had, plus X" -- reproducing
# this order on the backend's own field-projection table is what keeps that
# promise instead of replacing the wide table with a bare two-column one.
_STANDARD_LISTING_COLUMNS = (
    "application_type", "survey_no", "subdivisions", "status", "stage",
    "submission_date", "ward", "block",
)
_PROJECT_TRIGGER_RE = re.compile(
    r"\bcolumn\b|\bof each\b|\bfor each\b|\beach\s+(?:one|application|file|row)\b"
    r"|\bone by one\b|\btheir\b|\bevery\s+(?:one|application)\b"
    # "when were THEY last updated" / "when were they submitted" -- the subject
    # pronoun, not just the possessive "their". Missing this, "when were they
    # last updated" carried no recognized trigger at all and fell straight
    # through to the plain re-listing, the same table again, no date in sight.
    r"|\bthey\s+(?:last\s+)?(?:updated|submitted|filed|approved|rejected)\b"
    r"|\bwhen\s+(?:was|were|is|are)\s+(?:it|they|these|those)\b"
    r"|\blist\s+(?:out\s+|down\s+|me\s+)?(?:the\s+|all\s+)?(?:\w+\s+){0,2}\w+s\b"
    # "the CAN number of ALL the above application" / "of all of them" -- the
    # same per-row request as "their CAN numbers", just spelled with "all"
    # instead of a plural pronoun. Missing this, `field_projection()` found
    # the field ("can_number") but not the per-row trigger, so the message
    # fell to the generic aggregate branch, which cannot list a value per
    # application -- exactly the redundant-table / no-answer shape this
    # module exists to fix for the plainer phrasings.
    r"|\ball\s+of\s+them\b|\ball\s+(?:of\s+)?(?:the\s+)?above\b"
    # "application no with type" / "application number and status" -- naming
    # the row key explicitly alongside another field is the same per-row
    # request as "their type", just phrased as a column list instead of a
    # pronoun.
    r"|\b(?:application|app)\s*(?:no\.?|number)\s+(?:with|and)\b"
    # "show applications along district" -- Indian-English "along" for "along
    # with"/"including". Officer-typed shorthand for adding a column, not
    # naming a broader scope; see the ward/taluk/district access guard in
    # chatbot.py, which used to read the same word as a cross-jurisdiction
    # request and refuse it outright.
    # "adhuvodu" / "adhu udan" -- Tanglish for "along with that", the same
    # inclusion request as "along" in a different script.
    r"|\balong\b|\badhuvodu\b|\badhu\s*udan\b"
    # "only submitted date" / "submitted date only" / "just the status" --
    # `_APP_NO_ONLY_RE` below only ever covered this shape for the row key
    # itself ("application no only"); a bare "only <field>" for any OTHER
    # field matched no trigger at all here, so it never reached the
    # needle-matching loop and fell through to a fresh, generic listing
    # instead of narrowing to the one field asked for. Safe as a broad
    # trigger: it only produces a result once an actual field needle is
    # ALSO found below, same as every other trigger here.
    r"|\bonly\b|\bjust\b|\bmattum\b|\bmattuma\b"
    r"|ஒவ்வொன்றின்|அவற்றின்|மட்டும்"
    # "which district are these in?" / "what ward are they in?" -- a column of
    # the rows on screen asked as a question about "these".
    r"|\b(?:which|what)\s+\w+\s+(?:is|are)\s+(?:these|those|they|them)\b"
    r"|(?:இவை|அவை)\s+எந்த"
    # "ward details" / "block details" (+ Tanglish "sollu"): the column, not the
    # application card.
    r"|\b(?:ward|block|district|taluk|town)\s+details?\b"
    # Tanglish "their": "avatroda can number kaatu", "avangaloda names".
    r"|\bavatroda\b|\bavatrin\b|\bavaiyoda\b|\bavangaloda\b"
    # A plural field noun followed by the verb that asks for it: "blocks enna?",
    # "names kaatu", "பெயர்களை காட்டு".
    r"|\b(?:wards|blocks|districts|taluks|towns|statuses|applicants|names|mobiles|"
    r"channels|types|fees|dates|numbers)\s+(?:enna|sollu|sollunga|kaatu|kaattu|kaamii?)\b"
    r"|(?:பெயர்களை|எண்களை|வார்டுகளை|தொகுதிகளை)\s*(?:காட்டு|சொல்லு|சொல்லுங்கள்)",
    re.IGNORECASE,
)

# "application no only" / "only the application number" -- explicitly asks
# for JUST the row key, no other column. A distinct signal from naming a
# field: `field_projections()` returns [] for this (not None, which means
# "not a projection follow-up at all"), so the answer is a one-column table
# instead of falling through to the generic aggregate/count branch.
_APP_NO_ONLY_RE = re.compile(
    r"\b(?:application|app)\s*(?:no\.?|number)\s+only\b"
    r"|\bonly\s+(?:the\s+)?(?:application|app)\s*(?:no\.?|number)\b",
    re.IGNORECASE,
)


# ── Several rows in view, and a question about one field of them ────────────
# "what is the applicant name?" after a two-row listing named a field, not a
# row, so `resolve()` refused it as ambiguous and asked which one was meant.
# Asking was right while the only alternative was PICKING one -- but picking is
# not the only alternative. The officer was shown N rows and the field is on
# record for each of them, so answering it for EVERY row invents no referent:
# it covers all of them. The clarification stays for a list too long to answer
# down (`MAX_PER_ROW_ANSWER`), where a wall of rows is worse than a question.
# Ten was too tight, and inconsistent with the sibling path: "their wards" goes
# through `field_projection`, which renders a column for every carried row with
# no ceiling at all, while the same question without the trigger word ("what is
# the applicant name?") came here and asked "which one do you mean?" over the
# same 20 rows. One officer, one list, two different answers depending on
# whether they said "their". A real queue runs to a couple of dozen files, so
# the ceiling is now above that rather than under it.
MAX_PER_ROW_ANSWER = 25


def _needle_hit(needle: str, lowered: str, message: str) -> bool:
    """An ASCII needle matches on token boundaries, a Tamil one as a substring.

    `field_projection` can afford plain substrings because a trigger phrase has
    already claimed the turn; `field_for_each` has no such gate, and "can" sits
    inside "can you", "type" inside "typed". Tamil keeps the substring rule for
    the reason documented on _OWN_SUBJECT_RE: the virama is not a word
    character, so \b finds boundaries in the middle of words.
    """
    if needle.isascii():
        return bool(re.search(rf"\b{re.escape(needle)}\b", lowered))
    return needle in lowered or needle in message


def field_for_each(message: str) -> Optional[str]:
    """The row field a bare field question names, with no per-row trigger.

    The same field table `field_projection` reads, without its "of each" /
    "their" requirement -- used only once the context is known to hold SEVERAL
    rows, where a field question is per-row by definition.
    """
    if not message:
        return None
    message = _correct_typos(message)
    lowered = message.lower()
    for key, needles in _PROJECT_FIELDS:
        for n in needles:
            if _needle_hit(n, lowered, message):
                return key
    return None


# "details of both", "full record for each", "expand them". A details word asks
# for the RECORD, so it outranks a field word in the same breath: "applicant
# details like name" is answered with both files' details, which contain the
# name, rather than with the name alone.
_FULL_DETAILS_RE = re.compile(
    r"\bdetails?\b|\bfull\s+record\b|\bfull\s+info\w*\b"
    r"|\bmore\s+info\w*\b|\bcomplete\s+info\w*\b|\beverything\b"
    r"|\bexpand\b|\belaborate\b|\bvivaram\w*\b"
    r"|விவர",
    re.IGNORECASE,
)


_GEO_DETAILS_RE = re.compile(r"\b(?:ward|block|district|taluk|town)\s+details?\b", re.IGNORECASE)


def wants_full_details(message: str) -> bool:
    # "ward details" names a column of the listing, not the full record.
    text = _GEO_DETAILS_RE.sub(" ", _correct_typos(message or ""))
    return bool(_FULL_DETAILS_RE.search(text))


def field_projection(message: str) -> Optional[str]:
    """The single row field a 'show me X for each of them' follow-up asks for.

    Returns a canonical row key (see `_PROJECT_FIELDS`) or None. Needs BOTH a
    projection trigger ("of each", "their", "column", "list the …s") and a
    named field, so an ordinary listing request is not swept in.

    Matched on word boundaries via `_needle_hit()`, not a bare substring --
    "their applicant name" used to return `can_number`, because "applicant"
    contains "can" (ap-pli-CAN-t). A trigger phrase having already claimed
    the turn narrows WHETHER this fires, but does nothing to stop it picking
    the wrong field out of a message that legitimately names a different one.
    """
    if not message:
        return None
    message = _correct_typos(message)
    lowered = message.lower()
    if not _PROJECT_TRIGGER_RE.search(lowered):
        return None
    for key, needles in _PROJECT_FIELDS:
        for n in needles:
            if _needle_hit(n, lowered, message):
                return key
    return None


def field_projections(message: str, along_base: Optional[List[str]] = None) -> Optional[List[str]]:
    """Every row field a per-row follow-up names, in the order named.

    "application no with type and status" asks for more than one column at
    once -- `field_projection()` above only ever returns the first field it
    finds, which was fine while every projection follow-up named exactly
    one. Returns:
      * None    -- this is not a per-row projection follow-up at all
      * []      -- it is one, but names no field beyond the row key itself
                   ("application no only" / "only the application number")
      * [k, …]  -- the fields named, in the order the officer typed them

    `along_base` is the column set an "along" trigger builds on -- the
    caller's own last answer, when it has one, instead of always the plain
    listing's columns. Without it, "along district" then "along taluk" each
    started fresh from the plain table and answered with district OR taluk,
    never both, even though "along" means "in addition to" and the officer
    had just been shown district a breath earlier.
    """
    if not message:
        return None
    message = _correct_typos(message)
    lowered = message.lower()
    # Checked AFTER the field-needle scan below, not before: "only" is a
    # substring match with no look-ahead, so "show only application no AND
    # STATUS with SUBMITTED DATE" matched "only application no" on its own
    # and returned [] before the rest of the sentence was ever read --
    # dropping two fields the officer explicitly named. The shortcut only
    # applies when nothing else in the message named a field either.
    _app_no_only = bool(_APP_NO_ONLY_RE.search(lowered))
    _is_exclude = bool(_EXCLUDE_TRIGGER_RE.search(lowered))
    if _app_no_only and not _PROJECT_TRIGGER_RE.search(lowered):
        return []
    if not (_is_exclude or _PROJECT_TRIGGER_RE.search(lowered)):
        return None
    hits: List[Tuple[int, str]] = []
    seen = set()
    for key, needles in _PROJECT_FIELDS:
        if key in seen:
            continue
        best = None
        for n in needles:
            # Word-boundary, not a bare substring -- "applicant" contains
            # "can" (ap-pli-CAN-t), so "their applicant name and status"
            # used to also report a CAN number nobody asked for. `.find()`
            # here still needs a real index for ordering, so this locates
            # the SAME match `_needle_hit` just confirmed exists, rather
            # than re-deciding with a looser rule.
            if not _needle_hit(n, lowered, message):
                continue
            idx = lowered.find(n) if n.isascii() else (
                lowered.find(n) if n in lowered else message.find(n))
            if idx != -1 and (best is None or idx < best):
                best = idx
        if best is not None:
            hits.append((best, key))
            seen.add(key)
    hits.sort()
    if not hits:
        if _app_no_only:
            return []
        # The trigger matched ("their share") but no field this table knows
        # was named -- not the deliberate "app no only" case, just a field
        # this module has no answer for. None, so the caller moves on to
        # whatever else "their share" might be (an owner question, for
        # instance), instead of this claiming the turn and answering an
        # empty table.
        return None
    result = [key for _pos, key in hits]
    # A survey number on its own is ambiguous in this domain -- an ISD/MERGE
    # application splits ONE survey number into several sub-divisions, so
    # "show application no with survey no" without also saying which
    # sub-division leaves out the part that actually identifies the row.
    # Paired automatically, right after survey_no, whenever survey_no is
    # named at all (alone, or combined with any other field) -- other
    # fields (mobile, fee, status, ...) are untouched, they stay exactly
    # what was asked for.
    if "survey_no" in result and "subdivisions" not in result:
        result.insert(result.index("survey_no") + 1, "subdivisions")
    # "not along ward" / "without the status column" / "exclude taluk" --
    # the opposite of "along": drop the named field(s) from what is already
    # on screen (or the plain listing, with no prior projection to build on)
    # instead of narrowing down to them. Caller checks `excluded_mandatory_
    # field()` first, so a mandatory column never reaches here to be dropped.
    if _is_exclude:
        base = along_base if along_base else _STANDARD_LISTING_COLUMNS
        return [k for k in base if k not in result]
    # "along with district" / "show all applications along district" --
    # Indian-English "along" means "in addition to", not "only". Every other
    # trigger phrasing ("their district", "district only") narrows the
    # answer to just the named field(s); "along" was answered the same way,
    # so a full listing followed by "along with district" replaced the
    # eight-column table the officer already had with a two-column one
    # (App No + District) instead of adding a column to what was there.
    if _ALONG_RE.search(lowered):
        base = along_base if along_base else _STANDARD_LISTING_COLUMNS
        result = [k for k in base if k not in result] + result
    return result

# An explicit pointer back at ONE record. Present or not, the fragment still
# has to be a fragment -- this only raises confidence.
_BACKREF_RE = re.compile(
    r"\b(?:it|its|it's|the\s+same)\b"
    # "this week / this month / this year" is a period; "that is / that are" is a
    # relative clause ("applications that are overdue"). Neither points back.
    r"|\bthis\b(?!\s+(?:week|month|year|quarter|time|day|morning|afternoon|evening|weekend|fortnight))"
    r"|\bthat\b(?!\s+(?:is|are|was|were|has|have|had|will|can|could|should|would|need|needs)\b)"
    r"|அது|அதன்",
    re.IGNORECASE,
)

# A PLURAL pointer back is deliberately not this module's business.
# `_rescope_list_followup` in chatbot.py already folds "how many of them are
# approved" back into the question it continues, and answers it well. What was
# missing -- and what this module adds -- is the same thing without a pronoun
# ("which is oldest?"). Claiming the plural case too would replace a working
# answer with a clarification.
_PLURAL_BACKREF_RE = re.compile(
    # A bare "both" is anaphoric on its own: it is only sayable about two things
    # already in view. Without it here, "show both applications" tripped the
    # own-subject gate on the word "applications" and was routed as a fresh
    # question -- the officer was shown their whole open queue instead of the
    # two rows they were pointing at.
    #
    # "all the above application(s)" / "all above" is the same anaphor as
    # "all of them" in different words -- an officer pointing back at a
    # 70-row listing they were just shown. Without it, the bare word
    # "application" inside the phrase tripped the own-subject gate below
    # ("all the above application" reads, to that gate, as naming a fresh
    # subject), and "what is the CAN number of all the above application"
    # was routed as if it named no list at all.
    r"\b(?:they|them|their|these|those|both)\b|\ball\s+of\s+them\b"
    r"|\ball\s+(?:of\s+)?(?:the\s+)?above\b"
    # "application no only" / "application no with type" -- naming the row
    # key explicitly is itself a back-reference to the list on screen (there
    # is no OTHER application number to mean), same as "all the above" a few
    # lines up. Without this, the bare word "application" inside the phrase
    # tripped the own-subject gate before `field_projections()` below ever
    # got a look at it, and "application no only" was routed as a fresh
    # question naming no list at all.
    r"|\b(?:application|app)\s*(?:no\.?|number)\s+only\b"
    r"|\bonly\s+(?:the\s+)?(?:application|app)\s*(?:no\.?|number)\b"
    r"|\b(?:application|app)\s*(?:no\.?|number)\s+(?:with|and)\b"
    r"|அவை|அவற்றை|அவற்றின்|இரண்டும்",
    re.IGNORECASE,
)

# "what is the CAN number of all the above application" is 10 words and is
# still entirely follow-up scaffolding ("what is the ... of all the above")
# wrapped around one field cue ("can number") and one back-reference ("all
# the above") -- an officer spelling the reference out in words rather than
# with a pronoun is not the "long sentence that happens to contain a cue"
# case this cap exists to catch. Raised from 9 to 12 for exactly that room;
# left well short of "no cap at all" so a genuinely long, self-contained
# question is still routed as one.
_MAX_FOLLOWUP_WORDS = 12
# How long a field-cue fragment can be before it needs a question shape too
# (see `classify`'s cue-word branch). "survey number please" (3 words) is
# still almost certainly about the file in view; "the survey team is on the
# mainland" (7 words, no question mark, no interrogative) is an unrelated
# sentence that happens to contain the word "survey". 5 words was still wide
# enough to swallow plenty of ordinary declarative sentences of that length
# -- "the taluk office called me" (5), "block party this weekend" (4), "the
# channel keeps buffering" (4), "district collector visited today" (4),
# "status update meeting at 5pm" (5) all read as a follow-up about a row in
# view and none of them are. Tightened to 3: every genuine bare fragment in
# this file's own test suite is 2 words or carries a "?", so nothing real is
# lost, and a 4+ word sentence almost always has a subject and a verb of its
# own by then.
_MAX_FRAGMENT_WORDS = 3
_FRAGMENT_OPENERS = frozenset({
    "what", "whats", "who", "whos", "when", "where", "how", "which", "why",
    "is", "was", "are", "does", "do", "did", "has", "have", "had",
    "can", "could", "would", "should", "will",
    "show", "give", "tell", "list", "and", "also", "its", "their",
})


_SORT_CUE_RE = re.compile(
    r"\b(?:sort(?:ed|ing)?|sotr|srot|order(?:ed)?|arrange(?:d)?|reverse)\b"
    r"|\b(?:newest|oldest|latest|earliest|recent|old|new)\s+(?:ones?\s+)?first\b"
    r"|\b(?:ascending|descending)\b|\bdate\s*wise\b|வரிசை|varisai",
    re.IGNORECASE)
_SORT_QUESTION_RE = re.compile(r"\b(?:which|what|how|when|who|is|are|does|do|why)\b", re.IGNORECASE)
_APP_NUMBER_WORD_RE = re.compile(r"\bapp(?:lication)?s?\s*(?:number|no\.?|id)\b", re.IGNORECASE)


_NAV_RE = re.compile(
    r"^(?:and\s+|then\s+|now\s+|ok\s+)*(?:the\s+)?(?P<d>next|following|previous|prev)"
    r"(?:\s+(?:one|application|app|row|file))?\s*[?.!]*$"
    r"|^(?P<ta>அடுத்த(?:து|\s+விண்ணப்பம்)|முந்தைய(?:து|\s+விண்ணப்பம்))\s*[?.!]*$"
    r"|^(?:and\s+)?adutha\s+(?:one|application|file)\b|^(?:and\s+)?munnadi\s+(?:one|application)\b",
    re.IGNORECASE)


def nav_direction(message: str) -> Optional[str]:
    """"the next one" / "previous one" / "அடுத்தது" -> "next" | "prev": a step along the
    list a row was just picked from."""
    m = _NAV_RE.match(_correct_typos((message or "").strip()))
    if not m:
        return None
    word = (m.group("d") or m.group("ta") or m.group(0)).lower()
    return "prev" if re.search(r"prev|munnadi|முந்தை", word) else "next"


def is_sort_fragment(message: str) -> bool:
    """"sort by date", "newest first", "date wise sort pannu" -- a request to
    re-order the list on screen, naming no subject of its own."""
    from backend.services.rag import extract_sort_order
    text = _correct_typos((message or "").strip())
    if not text or len(text.split()) > 9 or not _SORT_CUE_RE.search(text):
        return False
    if _SORT_QUESTION_RE.search(text) or re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", text):
        return False
    if _OWN_SUBJECT_RE.search(_APP_NUMBER_WORD_RE.sub(" ", text)):
        return False
    return extract_sort_order(text) is not None


# "what about this", "and that one", "field visit of this", "is it completed" --
# point back at ONE thing and name (almost) nothing else. Bare ones ask what the
# officer wants to know; the others carry a field of their own.
_BARE_DEICTIC_RE = re.compile(
    r"(?:and\s+|then\s+|so\s+|ok\s+)?(?:(?:what|how)\s+(?:about|abt)\s+)?(?:this|that|it)(?:\s+one)?"
    r"|(?:இது|அது|idhu|adhu|idhuku|adhuku)(?:\s+(?:பற்றி|pathi|enna|என்ன))?")
_DEICTIC_FIELD_RE = re.compile(
    r"(?:and\s+)?(?:(?:show|display|give|check)\s+(?:me\s+)?)?(?:the\s+)?(?:field\s+)?visit"
    r"(?:\s+(?:details?|status|info))?\s+(?:of|for|on)\s+(?:this|that|it)(?:\s+one)?"
    r"|is\s+(?:it|this|that)\s+(?:completed|done|finished|scheduled|visited|approved|rejected|pending)"
    r"|(?:has|was)\s+(?:it|this|that)\s+(?:been\s+)?(?:visited|inspected|completed|scheduled)")


# ── Negation in a sort request ──────────────────────────────────────────────
# "not descending" is ascending; "not by date, sort by survey number" is by
# survey number; "don't sort" / "undo the sort" is the original order;
# "reverse the order" flips whatever the list was last sorted by.
_SORT_RESET_RE = re.compile(
    r"\b(?:don'?t|dont|do\s+not|no|stop|remove|undo|cancel|clear|without|skip)\s+(?:the\s+|any\s+|that\s+)?"
    r"(?:sort(?:ing|ed)?|order(?:ing)?)\b(?!\s+by)|\bunsorted\b|\boriginal\s+order\b|\bdefault\s+order\b"
    r"|\bnormal\s+order\b|\bas\s+(?:it\s+was|before)\b", re.IGNORECASE)
_NEG_BY_RE = re.compile(
    r"\b(?:not|don'?t|dont|do\s+not)\s+(?:sort(?:ed)?\s+|order(?:ed)?\s+)?by\s+([a-z ]+?)"
    r"(?=\s*(?:,|;|\.|\bbut\b|\binstead\b|\bsort\b|\border\b|\bby\b)|$)", re.IGNORECASE)
_NOT_DESC_RE = re.compile(
    r"\bnot\s+(?:in\s+)?(?:a\s+)?(?:descending|desc|newest|latest|recent|highest|biggest|z\s*to\s*a)\b"
    r"(?:\s+(?:order|first))*", re.IGNORECASE)
_NOT_ASC_RE = re.compile(
    r"\bnot\s+(?:in\s+)?(?:an?\s+)?(?:ascending|asc|oldest|earliest|lowest|smallest|a\s*to\s*z)\b"
    r"(?:\s+(?:order|first))*", re.IGNORECASE)
_REVERSE_RE = re.compile(r"\breverse(?:d)?\b|\bflip\b|\binvert\b|\bopposite\s+(?:order|way|direction)\b", re.IGNORECASE)
_SORT_FIELD_WORDS = {"submission_date": "submission date", "application_number": "application number",
                     "status": "status", "application_type": "type", "priority": "priority",
                     "ward_number": "ward", "block_number": "block", "survey_no": "survey number",
                     "fee_amount": "fee", "applicant_name": "applicant name"}


def normalise_sort_negation(message: str, ctx: Optional["FollowupContext"] = None) -> Tuple[str, bool]:
    """(message with its sort negation resolved, reset?) -- the message unchanged when
    it carries none. `reset` means "back to the original order"."""
    t = (message or "").strip().lower()
    if not t or len(t.split()) > 14:
        return message, False
    m = _NEG_BY_RE.search(t)
    if m:
        rest = t[m.end():]
        if re.search(r"\b(?:sort(?:ed)?|order(?:ed)?)\s+by\b|\bby\b", rest):
            return rest.strip(" ,;.") or message, False     # "not by date, sort by survey number"
        return "sort by submission date ascending", True     # "don't sort by date"
    if _NOT_DESC_RE.search(t):
        return "sort " + _NOT_DESC_RE.sub(" ascending ", t).strip(), False
    if _NOT_ASC_RE.search(t):
        return "sort " + _NOT_ASC_RE.sub(" descending ", t).strip(), False
    if _REVERSE_RE.search(t) and len(t.split()) <= 6:
        filt = (ctx.filters if ctx else None) or {}
        field, cur = filt.get("sort_by") or "submission_date", filt.get("sort_dir") or "asc"
        return (f"sort by {_SORT_FIELD_WORDS.get(field, 'submission date')} "
                f"{'descending' if cur == 'asc' else 'ascending'}"), False
    if _SORT_RESET_RE.search(t):
        return "sort by submission date ascending", True
    return message, False


# ── Negated reference: "not this one", "the other one", "none of them" ──────
_NEG_REF_RE = re.compile(
    r"(?:no[,!.\s]+)?not\s+(?:this|that|it)(?:\s+one|\s+application)?"
    r"|wrong\s+one|(?:the\s+)?other\s+one|another\s+one|none\s+of\s+(?:them|these|those)|neither(?:\s+of\s+them)?"
    r"|other\s+than\s+(?:these|those|them|this|that)"
    r"|(?:இது|அது)\s+இல்லை|(?:adhu|idhu)\s+illa(?:i)?", re.IGNORECASE)


def is_negated_ref(message: str) -> bool:
    return bool(_NEG_REF_RE.fullmatch((message or "").strip().lower().strip(" ?.!")))



_WHAT_IS_IT_RE = re.compile(
    r"(?:what(?:'s|\s+is|s)|whats)\s+(?:it|this|that)(?:\s+one)?|(?:idhu|adhu)\s+enna|இது\s+என்ன|அது\s+என்ன")


def is_what_is_it(message: str) -> bool:
    """"what is it" -- the officer wants the record itself."""
    return bool(_WHAT_IS_IT_RE.fullmatch((message or "").strip().lower().strip(" ?.!")))


def is_bare_deictic(message: str) -> bool:
    return bool(_BARE_DEICTIC_RE.fullmatch((message or "").strip().lower().strip(" ?.!")))


def is_deictic_field(message: str) -> bool:
    return bool(_DEICTIC_FIELD_RE.fullmatch((message or "").strip().lower().strip(" ?.!")))


def classify(message: str) -> str:
    """Which kind of implicit follow-up this message is, if any.

    Length is part of the test on purpose: a follow-up is a fragment. A long
    sentence that happens to contain "how many" is a question in its own right
    and must be routed normally.
    """
    text = _correct_typos((message or "").strip())
    if not text:
        return FOLLOWUP_NONE
    words = text.split()
    if len(words) > _MAX_FOLLOWUP_WORDS:
        return FOLLOWUP_NONE
    lowered = text.lower()

    # A plural pointer back ("how many of them are ISD?") is a list question,
    # and a stored list answers it over exactly the rows the officer saw. It
    # only ever reaches the two LIST kinds below: a plural fragment is never
    # singular, so it can never turn a working answer into a clarification --
    # which was the reason this module used to hand the whole plural case to
    # `_rescope_list_followup`. That path stays the fallback, and still runs
    # whenever there is no stored context for `resolve()` to use; on its own it
    # folds the fragment into the PREVIOUS USER MESSAGE, so in a chain of
    # follow-ups ("what is the total fee?" then "how many of them are ISD?") it
    # merges with another fragment and the scope resets to the whole desk.
    plural = bool(_PLURAL_BACKREF_RE.search(lowered))

    from backend.services.rag import extract_row_selection as _ers
    if (_ers(text) and not plural
            and re.search(r"\b(?:isd|nisd|merge)\b|\bapplications\b|\bapps\b", lowered)):
        return FOLLOWUP_NONE
    if nav_direction(text) or re.fullmatch(
            r"(?:and\s+|then\s+)?(?:the\s+)?(?:last|first|final)\s+(?:one|application|row|file)\s*[?.!]*", lowered.strip()):
        return FOLLOWUP_SINGULAR
    if is_sort_fragment(text):
        return FOLLOWUP_LIST_REFINE
    if len(words) <= 8 and (is_bare_deictic(text) or is_deictic_field(text) or is_what_is_it(text)
                            or is_negated_ref(text)):
        return FOLLOWUP_SINGULAR
    if len(words) <= 8 and re.match(r"why\b.*\b(?:pending|delayed|late|overdue|stuck|not\s+approved)\b", lowered):
        return FOLLOWUP_SINGULAR
    if (len(words) <= 4 and re.match(r"(?:only|just)\b", lowered) or re.search(r"\b(?:only|mattum)$", lowered)) \
            and len(words) <= 4 and refinement(text)["submission_channel"]:
        return FOLLOWUP_LIST_REFINE
    _neg = negation_normalise(lowered)
    if _neg != lowered and len(words) <= 5 and re.search(r"\bnot\s+" + _NEG_WORD + r"\b", _neg):
        return FOLLOWUP_LIST_REFINE
    if len(words) <= 3 and re.fullmatch(r"(?:not|except)\s+" + _NEG_WORD + r"(?:\s+ones)?", lowered.strip(" ?.!")):
        return FOLLOWUP_LIST_REFINE
    if _REFINE_RE.search(lowered):
        return FOLLOWUP_LIST_REFINE
    # "remove row 2" / "exclude the first one" / "drop 2026/0154/28/001167" --
    # an exclude-trigger word PLUS a row reference (ordinal, a run of rows, or
    # an application number) asks to drop that row from the table, not to
    # pick it as the one thing to describe. Checked ahead of the ordinal /
    # slice branches just below, which otherwise read "remove row 2" as
    # exactly the same request as "row 2" alone and answered with that row's
    # own details -- describing the very row the officer asked to drop --
    # and ahead of the own-subject gate too, since an application number
    # inside the message ordinarily means a fresh question, which this is
    # not. Reuses FOLLOWUP_LIST_REFINE ("show only NISD") rather than a new
    # kind: dropping a row is the same shape of request as narrowing to
    # some -- both re-render the table over a different set of the carried
    # numbers -- so `resolve()` only needs to compute a different set to
    # keep, not a new code path to render it with.
    if _EXCLUDE_TRIGGER_RE.search(lowered) and (
            mentions_ordinal(message) or mentions_slice(message)
            or re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", message or "")):
        return FOLLOWUP_LIST_REFINE
    # "the 2nd one" / "row 7" picks a row by position. Checked ahead of the
    # own-subject gate ("what about the first application" still means row 1)
    # but behind `_AGGREGATE_RE`, so "which is the first to be approved" stays
    # an aggregate question rather than "pick row 1".
    from backend.services.rag import extract_row_selection
    if ((mentions_ordinal(message) or extract_row_selection(message)) and not plural
            and not mentions_slice(message)
            and re.search(r"\b(?:isd|nisd|merge)\b|\bapplications\b|\bapps\b", lowered)):
        return FOLLOWUP_NONE
    if mentions_ordinal(message) and not _AGGREGATE_RE.search(lowered):
        return FOLLOWUP_SINGULAR
    # A run of rows ("the last two") points back just as squarely as "the 2nd
    # one" does, and names no subject of its own. `resolve` turns it into the
    # rows; it is checked here only so the own-subject gate below cannot claim
    # it as a fresh question.
    if mentions_slice(message):
        return FOLLOWUP_SINGULAR
    # "not along application no" -- the own-subject gate below reads
    # "application no" as naming a fresh subject (the same test that lets a
    # genuine "how many ISD applications do I have" through), which would
    # otherwise drop this on the floor before `_scoped_list_answer` ever gets
    # to refuse it in words. Checked ahead of that gate for exactly this one
    # shape; every other exclusion ("not along ward") already survives it,
    # since "ward" alone names no subject of its own.
    if excluded_mandatory_field(message):
        return FOLLOWUP_LIST_AGGREGATE
    # "show more applications" / "less kami" -- see `_VAGUE_COUNT_RE`.
    if _VAGUE_COUNT_RE.match(lowered):
        return FOLLOWUP_LIST_AGGREGATE
    # "what date is it (today)?" -- idiomatic English for "what is today's
    # date", not a back-reference to a carried row. The bare pronoun "it" is
    # ordinarily exactly the signal that a field question ("what is ITS
    # ward") points at the application in view, which is what the own-subject
    # gate just below deliberately stands aside for -- but this is the one
    # idiom where "it" is a dummy subject ("it is raining") and not a
    # pronoun at all, so that gate must not treat it as a back-reference here.
    if re.search(r"\bwhat\s+date\s+is\s+it\b", lowered):
        return FOLLOWUP_NONE
    # "is it okay / allowed / possible to change the inspection date?" -- "it" is a
    # dummy subject here, not a pronoun for the row on screen.
    if re.search(r"^\s*(?:is|are)\s+it\s+(?:okay|ok|allowed|possible|permitted|fine|alright)\b", lowered):
        return FOLLOWUP_NONE
    # "who is this?" / "what is this?" -- an officer asking about the
    # ASSISTANT itself (the same question as "who are you?"), not about a
    # row on screen. "this" is exactly the pronoun `_BACKREF_RE` reads as
    # pointing at the application in view ("what is THIS application's
    # status"), and bare "who" is itself a singular-field cue, so together
    # they made a self-identity question read as a follow-up and gave the
    # "which one do you mean?" clarification instead of ever reaching
    # `chatbot._is_capability_question`. Scoped to the bare two/three-word
    # shape -- "what is this application" still means the row in view.
    if re.match(
        r"^(?:who|what)\s+(?:is\s+)?this\??$|^இது\s*(?:யார்|என்ன)\??$"
        r"|^(?:idhu|ithu)\s+(?:yaaru|enna)\??$",
        lowered.strip()
    ):
        return FOLLOWUP_NONE
    # "what are you saying?" -- questioning the assistant's own last reply.
    # "enna" (what) is a bare Tamil/Tanglish field cue on its own (it
    # resolves "பெயர் enna?"), so "enna solra" was read the same way "who is
    # this" was above.
    if re.match(
        r"^what\s+(?:are\s+you|r\s+u|are\s+u)\s+saying\??$"
        r"|^(?:nee\s+)?enna\s+(?:solra|sollura)\??$",
        lowered.strip()
    ):
        return FOLLOWUP_NONE
    # An own subject makes it a fresh question -- but only when it is not
    # merely a back-reference ("what is its ward" names "ward", not a subject).
    if _has_own_subject(lowered) and not _BACKREF_RE.search(lowered) \
            and not plural:
        return FOLLOWUP_NONE
    # "what does X mean" / "what is the meaning of X" -- a definition
    # question, not a count. Bare "mean" is in `_AGGREGATE_RE` for its
    # statistics sense ("the mean processing time"), which collided with
    # this far commoner shape: "what does a temporary subdivision number
    # mean" was read as an aggregate follow-up with no list to count, and
    # with no context in the conversation either, answered "I do not know
    # which set you mean" -- a clarification about a table, for a question
    # that named no table at all and was never about one.
    if re.search(r"\bwhat\s+(?:does|is|do)\b.{0,40}\bmean(?:s|ing)?\b"
                r"|\bmeaning\s+of\b", lowered):
        return FOLLOWUP_NONE
    if _AGGREGATE_RE.search(lowered):
        return FOLLOWUP_LIST_AGGREGATE
    # "their wards", "the status of each", "the CAN number column",
    # "application no only" (names no field at all -- the row key alone) --
    # one or more fields for every row shown. `field_projections()` (not the
    # older singular `field_projection()`) so the empty-list "app no only"
    # case is recognised too: `is not None` is deliberately not `bool(...)`,
    # since [] is falsy but still means "yes, a projection follow-up".
    if field_projections(message) is not None:
        return FOLLOWUP_LIST_AGGREGATE
    # "show their details", "show details", "full details" -- a request for the
    # RECORDS behind the rows on screen. `resolve` has had a branch for this
    # since "details of both", but it could never be reached: none of these
    # carries an aggregate word, an ordinal or a field cue, so classify called
    # them NONE and the turn fell through to a handler that had no application
    # number and asked for one. Nine of the ten ways an officer writes this
    # failed; the only one that worked did so by accident, because "can I get
    # the details" contains the field cue "can".
    #
    # Naming a subject still wins: "show details of 2026/0154/28/001167" and
    # "show details of my pending applications" are complete requests.
    if wants_full_details(text) and not _has_own_subject(lowered):
        return FOLLOWUP_LIST_AGGREGATE
    if plural:
        # Plural, but not list-shaped. Leave it where it has always been.
        return FOLLOWUP_NONE
    # A cue word is matched as a bare substring against the WHOLE message, with
    # no word-boundary and no question shape required -- right for the short
    # fragments this branch exists for ("survey number?", "status?"), wrong
    # for an ordinary sentence that happens to contain one of these common
    # nouns. "the survey team is on the mainland" is not a question about the
    # application in view, but "survey" is a bare cue, so it was read as one
    # and answered with a survey-number table for rows nobody asked about.
    # A genuine fragment is short, a question, or opens with an interrogative
    # / imperative -- an unrelated declarative sentence is none of those.
    if any(cue in lowered for cue in _SINGULAR_FIELD_CUES):
        if (len(words) <= _MAX_FRAGMENT_WORDS or "?" in text
                or words[0].strip(".,!") in _FRAGMENT_OPENERS):
            return FOLLOWUP_SINGULAR
        return FOLLOWUP_NONE
    return FOLLOWUP_NONE


# Cues that say the follow-up is about the *field visit*, not the application.
# "when was it scheduled" is the case that used to answer with the submission
# date: the words "field visit" were in the previous turn, not this one.
_VISIT_CUE_RE = re.compile(
    r"\bschedul\w*\b|\bvisit\w*\b|\binspect\w*\b|\bsite\b"
    r"|\bkalam\b|\baaivu\b"
    r"|கள\s*ஆய்வு|திட்டமிட|ஆய்வு",
    re.IGNORECASE,
)


def asks_about_visit(message: str) -> bool:
    return bool(_VISIT_CUE_RE.search(_correct_typos(message or "")))


# Refinements a stored list can be narrowed by, read off the fragment itself.
_TYPE_WORDS = {"isd": "ISD", "nisd": "NISD", "merge": "MERGE"}
_STATUS_WORDS = {
    "approved": "approved", "rejected": "rejected", "pending": "pending",
    "in progress": "in_progress", "in-progress": "in_progress",
    # "progres" (dropped trailing 's') should typo-correct to "progress" on
    # its own, but `_correct_typos` reads "progress" = "progres"+"s" as a
    # plural of "progres" (the same false-positive `_is_inflection` guard
    # documented for "typ"/"type") and refuses the correction, with no other
    # vocabulary word to fall back to -- so it stayed "in progres" verbatim,
    # named no status, and "how many are in progres" silently ignored the
    # filter. Listed here directly rather than loosening that guard, which
    # exists specifically to protect genuine plurals like "pattas".
    "in progres": "in_progress",
    "escalated": "escalated",
    # a carried list holds no rejected files, so a finished one is an approved one (stage COMPLETED)
    "completed": "approved",
}
# Tamil status words, matched as substrings (see _OWN_SUBJECT_RE for why).
_STATUS_WORDS_TA = {
    "அங்கீகரி": "approved", "நிராகரி": "rejected", "நிலுவை": "pending",
    # "ஒப்புதல் பெற்ற" is how an officer actually says "approved" -- the
    # attachment aliases already map ஒப்புத -> approved. Without it,
    # "எத்தனை ஒப்புதல் பெற்றவை?" ("how many are approved?") named no status,
    # so the scoped answer fell back to describing the rows and replied
    # "அந்த 2 விண்ணப்பங்களில் 2 நிலுவையில் உள்ளவை" -- how many are PENDING.
    "ஒப்புத": "approved", "ஏற்றுக்கொள்": "approved",
    "மறுக்கப்": "rejected", "நிராகரிக்கப்": "rejected",
    "செயல்பாட்டில்": "in_progress",
}

# The mutually-exclusive lifecycle states. A follow-up that names one of these
# while the carried listing is already filtered to a DIFFERENT one is a fresh
# question, not a refinement of the list (see `_carried_list_status`).
_TERMINAL_STATUSES = {"approved", "rejected", "pending", "in_progress",
                      "escalated"}


def _carried_list_status(context: "FollowupContext") -> Optional[str]:
    """The lifecycle state the carried listing was filtered to, if any.

    Read from `filters['status']` when the answer recorded it, else inferred
    from `query_type` ("Approved Applications", "Rejected Applications", ...).
    Returns None for an unfiltered or non-status listing ("All CSC
    Applications", "Overdue Applications", a month scope).
    """
    if not context:
        return None
    explicit = (context.filters or {}).get("status")
    if explicit in _TERMINAL_STATUSES:
        return explicit
    qt = (context.query_type or "").lower()
    # A compound label ("Pending & In Progress & Escalated & Approved &
    # Rejected Applications", or any listing naming several statuses at
    # once, e.g. "Approved & Pending NISD applications") names MULTIPLE
    # statuses, not one -- picking the first phrase found treated "show all
    # applications" as carrying status=in_progress alone (the first phrase
    # this tuple happens to check), so a follow-up ("show their IGRS
    # number") was silently filtered down to the handful of in_progress
    # files and answered "None of those applications are available to read
    # right now" over a 70-row list the officer could see on screen. Count
    # every phrase this label names; a single hit is a real carried filter,
    # two or more means the listing was unfiltered (or filtered to several
    # statuses at once) and there is no ONE status to carry forward.
    found = [value for phrase, value in (
        ("in progress", "in_progress"), ("in-progress", "in_progress"),
        ("approved", "approved"), ("rejected", "rejected"),
        ("pending", "pending"), ("escalated", "escalated"))
        if phrase in qt]
    if len(set(found)) == 1:
        return found[0]
    return None


_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")


def _carried_list_type(context: "FollowupContext") -> Optional[str]:
    """The application type ("ISD"/"NISD"/"MERGE") the carried listing was
    filtered to, if any -- from `filters` or the `query_type`
    ("Pending ISD Applications"). None for a mixed-type listing."""
    if not context:
        return None
    explicit = ((context.filters or {}).get("application_type")
                or (context.filters or {}).get("type"))
    if explicit:
        return str(explicit).upper()
    qt = (context.query_type or "").lower()
    for w in ("nisd", "merge", "isd"):
        if re.search(rf"\b{w}\b", qt):
            return w.upper()
    return None


def _period_tokens(text: str) -> set:
    """Year and month names a string mentions -- {"2024", "june", ...}."""
    t = (text or "").lower()
    out = set(re.findall(r"\b20\d\d\b", t))
    out.update(m for m in _MONTHS if re.search(rf"\b{m}\b", t))
    return out


# Submission-channel words a follow-up can narrow by -- "how many of them are
# from CSC?", "show only the Sub-Registrar ones". The value matches
# `applications.submission_channel` as `can_channel()` derives it. Deliberately
# conservative: only the unambiguous phrases, checked longest-first so
# "sub registrar" wins before a bare "registrar" could. A bare "portal" or
# "camp" is left out -- both are too easily meant in another sense.
_CHANNEL_PHRASES = (
    ("sub-registrar", "sub_registrar"),
    ("sub registrar", "sub_registrar"),
    ("subregistrar", "sub_registrar"),
    ("sro", "sub_registrar"),
    ("common service centre", "CSC"),
    ("common service center", "CSC"),
    ("csc", "CSC"),
    ("citizen portal", "citizen"),
    ("citizen", "citizen"),
)


# A negated status/type in a FOLLOW-UP fragment ("not approved", "except
# ISD", "other than rejected ones"). `Resolution.status` /
# `.application_type` are single values everywhere they are consumed
# (labels, `_carried_list_status`, the terminal-status guard in `resolve()`
# itself), so unlike the fresh-message fix in chatbot.py's
# `_explicit_status_request` / `_extract_app_types` -- which can safely
# return the concrete list of the other four statuses / other two types --
# widening this one to a list would ripple through every one of those
# call sites. The narrower, still-correct fix: a negated word names NO
# status/type at all here, so the field stays None and the fragment falls
# through to an unfiltered count/list over the carried rows, which is
# honest, rather than being read as literally naming the status it
# negated -- "not approved" was matching bare `\bapproved\b` and silently
# filtering the carried list DOWN to the approved rows, the literal
# opposite of the question.
_NEGATED_REFINEMENT_RE = re.compile(
    r"\b(?:not|except|excluding|other\s+than)\s+(?:the\s+)?\w+",
    re.IGNORECASE,
)


_NEG_WORD = r"(?:rejected|approved|completed|pending|in[\s-]?progress|escalated|nisd|isd|merge)"
_NEG_VERB_RE = re.compile(
    rf"(?:\b(?:exclude|excluding|without|hide|skip|ignore|leave\s+out|remove|drop|except|other\s+than|apart\s+from|"
    rf"besides|minus|but\s+not|everything\s+but|all\s+but|anything\s+but|"
    rf"(?:don'?t|dont|do\s+not|never)\s+(?:show|list|display|include|give)(?:\s+me)?)"
    rf"|(?:^|[,;]\s*|\band\s+)no)\s+(?:the\s+|all\s+|any\s+)?({_NEG_WORD})\b(?!\s+(?:column|row))(?:\s+(?:ones?|please|pls))*"
    rf"(?P<chain>(?:\s*(?:,|and|or|nor|&)\s*(?:the\s+)?{_NEG_WORD}\b(?:\s+ones?)?)*)",
    re.IGNORECASE)
_NEG_SUFFIX_RE = re.compile(
    rf"\b({_NEG_WORD})\s+(?:ones?\s+)?(?:illama|illamal|vendam|venda|thavira|thavirthu|ozhichu)\b|({_NEG_WORD})\s*(?:இல்லாமல்|வேண்டாம்|தவிர)",
    re.IGNORECASE)


def negation_normalise(text: str) -> str:
    """"exclude rejected" / "rejected illama" -> "not rejected", the form the
    refinement rules already read."""
    # "other than ISD and NISD" excludes both: "not ISD and not NISD", never "not ISD and NISD"
    text = _NEG_VERB_RE.sub(
        lambda m: "not " + " and not ".join([m.group(1)] + re.findall(_NEG_WORD, m.group("chain") or "", re.IGNORECASE)),
        text or "")
    text = _NEG_SUFFIX_RE.sub(lambda m: f"not {m.group(1) or m.group(2)}", text)
    # "not approved ones please" -> "not approved": harmless trailing request words
    text = re.sub(rf"\b(not\s+{_NEG_WORD})(?:\s+(?:ones?|please|pls))+\b", r"\1", text, flags=re.IGNORECASE)
    # "show all except ISD" -> "not ISD": what is left is only filler around the negation
    return re.sub(rf"^\s*(?:please\s+)?(?:(?:show|list|give|display)\s+)?(?:me\s+)?(?:all|everything|anything)?\s*"
                  rf"(not\s+{_NEG_WORD})\s*[.!?]*\s*$", r"\1", text, flags=re.IGNORECASE)


def refinement(message: str) -> Dict[str, Optional[str]]:
    """The status / type / channel a refinement or aggregate names, if any.

    Token-based so "nisd" does not match inside a longer word, and NISD is
    checked before ISD because "nisd" contains "isd". `status_excluded` /
    `type_excluded` carry the NEGATED value ("not approved" -> "approved")
    so `_scoped_list_answer` can still compute an exact count ("of those 3,
    0 are not ISD" rather than "3 of those 3 are matching" -- a count of
    everything, mislabelled as an answer to a negated question).
    """
    lowered = negation_normalise(_correct_typos(message or "").lower())
    out: Dict[str, Optional[str]] = {
        "status": None, "application_type": None, "submission_channel": None,
        "status_excluded": None, "type_excluded": None}
    _negated = bool(_NEGATED_REFINEMENT_RE.search(lowered))
    for phrase, value in _STATUS_WORDS.items():
        if re.search(rf"\b{re.escape(phrase)}\b", lowered):
            if re.search(rf"\b(?:not|except|excluding|other\s+than)\s+(?:the\s+)?{re.escape(phrase)}\b", lowered):
                out["status_excluded"] = value
            else:
                out["status"] = value
            break
    if out["status"] is None and not _negated:
        for phrase, value in _STATUS_WORDS_TA.items():
            if phrase in lowered:
                out["status"] = value
                break
    for word in ("nisd", "merge", "isd"):
        if re.search(rf"\b{word}\b", lowered):
            if re.search(rf"\b(?:not|except|excluding|other\s+than)\s+(?:the\s+)?{word}\b", lowered):
                out["type_excluded"] = _TYPE_WORDS[word]
            else:
                out["application_type"] = _TYPE_WORDS[word]
            break
    for phrase, value in _CHANNEL_PHRASES:
        if re.search(rf"\b{re.escape(phrase)}\b", lowered):
            out["submission_channel"] = value
            break
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Resolution
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Resolution:
    """What the follow-up turned out to refer to.

    Exactly one of these is true: `ambiguous` (ask, do not guess), `resolved`
    (act on it), or neither (not a follow-up -- route normally).
    """

    kind: str = FOLLOWUP_NONE
    application_number: Optional[str] = None
    application_numbers: List[str] = field(default_factory=list)
    entity: Optional[str] = None
    about_visit: bool = False
    status: Optional[str] = None
    application_type: Optional[str] = None
    submission_channel: Optional[str] = None
    # The status/type a NEGATED refinement named ("not approved" -> status
    # here is None but status_excluded is "approved"). `status`/
    # `application_type` stay scalar and never hold the negation itself
    # (see `refinement()`'s own docstring for why); these let
    # `_scoped_list_answer` still state an exact count for "how many are
    # not X" instead of falling back to counting everything as "matching".
    status_excluded: Optional[str] = None
    type_excluded: Optional[str] = None
    # A field question that covers EVERY carried row: the projected field key
    # ("applicant_name"), or the whole record for each when a details word was
    # used. Both are answered over the re-read rows, never by picking one.
    per_row_field: Optional[str] = None
    full_details: bool = False
    # How many rows a "remove row 2" / "exclude 2026/.../001167" style request
    # dropped from the carried list. 0 (the default) for every other kind of
    # follow-up. `_scoped_list_answer` checks this FIRST and answers with a
    # plain "N remaining" confirmation instead of running the status/type
    # aggregate logic built for "show only NISD" -- that logic filters the
    # rows by whatever status/type the message named, which for a row
    # removal is nothing at all, and it answered "None of those 0
    # application(s) are pending" for a table that still had a row in it.
    rows_dropped: int = 0
    # "show more" / "less kami" -- a vague request for a bigger or smaller
    # result set with no number attached. There is no default row cap
    # anywhere in this app's listings (every query returns everything that
    # matches), so there is nothing to lift or narrow; this only records
    # that the officer asked so `_scoped_list_answer` can say so plainly
    # instead of falling all the way through to the document-search
    # fallback, which answered "No documents found with the query 'less'."
    # -- a developer-facing string for a request that named an application
    # list, not a document.
    vague_count: bool = False
    ambiguous: bool = False
    clarification: Optional[str] = None
    context: Optional[FollowupContext] = None

    @property
    def resolved(self) -> bool:
        return bool(not self.ambiguous and self.kind != FOLLOWUP_NONE)


def _clarify(text_en: str, text_ta: str, language: Optional[str]) -> str:
    """One short question, in the officer's language.

    A Tanglish turn gets Tamil, not English: everywhere else in the app
    `is_tamil = language in ("ta", "tanglish")`, so an English clarification
    landing beside a Tamil-script answer read as two different assistants.
    """
    return text_ta if language in ("ta", "tanglish") else text_en


# Intents whose message already names what it is about. A comparison names
# both of its sides ("pending versus approved"), a code lookup carries the
# code, a workload question is about the officer. None of these is ever a
# continuation, however short it is -- "average time to approve" is three
# words and a complete question.
SELF_CONTAINED_INTENTS = frozenset({
    "compare_applications", "service_code_lookup", "service_code_guide", "fee_lookup",
    "fv_change_date", "fv_scheduling_conflicts", "fv_reschedule_availability",
    "sub_registrar", "district_code", "jurisdiction_summary",
    "officer_workload", "officer_directory", "greeting", "farewell", "help",
    "can_number_info", "survey_detail", "rejection_info", "last_application",
})


# "last" with no fixed index -- used only inside `ordinal_pick`, which runs
# AFTER `classify` has already decided the message is a follow-up, so the bare
# "last" here cannot misfire on "last month".
_LAST_ORDINAL_RE = re.compile(r"\b(?:last|final)\b|கடைசி|kadaisi", re.IGNORECASE)


def ordinal_pick(message: str, numbers: List[str]) -> Optional[str]:
    """The row the officer named by position, or None if they named none.

    Row order is the order the officer was shown -- the carried numbers are
    stored in that order. Returns None when a position was named but points
    past the end of the list; `resolve()` turns that into a clarification.
    """
    if not numbers:
        return None
    msg = _correct_typos(message or "").lower()
    if _LAST_ONE_RE.search(msg) or _LAST_ORDINAL_RE.search(msg):
        return numbers[-1]
    idx = ordinal_index(message)
    if idx is not None and 1 <= idx <= len(numbers):
        return numbers[idx - 1]
    return None


def excluded_row_numbers(message: str, numbers: List[str]) -> Optional[List[str]]:
    """The carried rows a "remove row 2" / "exclude 2026/.../001167" style
    message asks to drop, or None if this message isn't one of those.

    Checked by `resolve()` against the exact `numbers` list the officer was
    shown, in that same order -- an application number that is not in the
    list, or an ordinal past the end of it, is simply not found here rather
    than guessed at; `classify()` has already confirmed the SHAPE of the
    request (an exclude-trigger word plus a row reference), this confirms
    which row(s) that reference actually names.
    """
    if not numbers or not _EXCLUDE_TRIGGER_RE.search(_correct_typos(message or "").lower()):
        return None
    named = [n for n in re.findall(r"\d{4}/\d{3,4}/\d{1,3}/\d+", message or "")
             if n in numbers]
    if named:
        return named
    run = slice_pick(message, numbers)
    if run:
        return run
    picked = ordinal_pick(message, numbers)
    if picked:
        return [picked]
    return None


def resolve(message: str,
            context: Optional[FollowupContext],
            language: Optional[str] = "en") -> Resolution:
    """Decide what a bare follow-up refers to, or that it is unanswerable.

    Called before intent routing, so the rest of the pipeline sees a question
    that names its own subject. Returns an unresolved `Resolution` for anything
    that is not a follow-up, which is the overwhelmingly common case.
    """
    kind = classify(message)

    # A patta-transfer-party answer ("... new owner (transferee): X") leaves ONE
    # record in view, tagged with the side. A short field fragment after it --
    # "what is their gender?", "and the father's name?" -- reads as plural or
    # names no subject, so classify() calls it NONE. Keep it on that party.
    _tp_side = (context.filters or {}).get("transfer_party") if context else None
    if kind == FOLLOWUP_NONE and _tp_side and _looks_like_bare_field_followup(message):
        kind = FOLLOWUP_SINGULAR

    # A registration-detail answer leaves ONE record in view. A bare "where?" /
    # "and why?" after it carries no cue classify() knows, so it is NONE --
    # keep it on that deed.
    _reg_kind = (context.filters or {}).get("reg_detail") if context else None
    if kind == FOLLOWUP_NONE and _reg_kind and (
            _looks_like_bare_field_followup(message)
            or re.match(r"^(?:and\s+|so\s+|ok\s+|but\s+)?(?:the\s+)?"
                        r"(?:what\s+about\s+)?(when|where|why|what\s+date|"
                        r"which\s+date|which\s+place|எப்போ|எங்க|ஏன்|eppo|enga|yen)"
                        r"\w*\s*\??\s*$", (message or "").strip().lower())):
        kind = FOLLOWUP_SINGULAR

    if kind == FOLLOWUP_NONE:
        return Resolution()

    if context is None:
        # Nothing to attach it to. This layer stands aside rather than asking:
        # the message may well be a complete question that simply reads like a
        # fragment ("what is the total fee?" is a fee query in its own right),
        # and the handlers below already ask for an application number when
        # they genuinely need one. Claiming ambiguity here turned working
        # questions into clarifications.
        return Resolution()

    if context.entity == ENTITY_APPLICATION_LIST and not context.application_numbers:
        # The previous answer was a listing that matched nothing. There is no
        # referent to carry, and reaching further back would attach the
        # follow-up to a scope the officer has left. Stand aside, exactly as
        # for no context at all.
        return Resolution()

    if is_negated_ref(message):
        neither = bool(re.search(r"none|neither", (message or "").lower()))
        if neither:
            return Resolution(kind=kind, ambiguous=True, context=context, clarification=_clarify(
                "Understood -- nothing selected. Give an application number, or ask for a list.",
                "சரி -- எதுவும் தேர்ந்தெடுக்கப்படவில்லை. விண்ணப்ப எண்ணைத் தரவும், அல்லது பட்டியலைக் கேளுங்கள்.",
                language))
        return Resolution(kind=kind, ambiguous=True, context=context, clarification=_clarify(
            "Understood, not that one. Which application do you mean? Give the number, or a position "
            "(the first, the 2nd, the last).",
            "சரி, அது இல்லை. எந்த விண்ணப்பம்? எண்ணை அல்லது இடத்தைத் (முதல், 2-வது, கடைசி) தரவும்.",
            language))
    about_visit = asks_about_visit(message) or (
        bool((context.filters or {}).get("about_visit")) and not names_a_field(message))
    refine = refinement(message)

    # "how many pending" after ONE application's details is a count over the
    # register, not a question about that application.
    if (kind == FOLLOWUP_LIST_AGGREGATE and context.entity == ENTITY_APPLICATION
            and re.search(r"\bhow\s+many\b|\bevlo\b|எத்தனை", (message or "").lower())
            and not re.search(r"\bdays?\b", (message or "").lower())
            and (refine["status"] or refine["application_type"] or refine["submission_channel"])
            and not _PLURAL_BACKREF_RE.search((message or "").lower())):
        return Resolution()

    # ── A contradicting-status aggregate is a fresh question ────────────────
    # "show my approved applications" -> "how many have been rejected?" asks
    # for a rejected count over the whole jurisdiction, not over the approved
    # rows -- scoping it there answers "none of those 52", which reads as "you
    # have no rejected applications" and is false. Stand aside so it routes as
    # its own question. Guarded tightly: only a bare aggregate (no plural
    # pointer -- "how many of THEM are rejected" is the officer pointing back
    # on purpose) that names a lifecycle state the carried listing is already
    # filtered AWAY from.
    _has_plural_ptr = bool(_PLURAL_BACKREF_RE.search(_correct_typos(message or "").lower()))
    if kind == FOLLOWUP_LIST_AGGREGATE and refine["status"] in _TERMINAL_STATUSES \
            and not _has_plural_ptr:
        carried = _carried_list_status(context)
        if carried and carried != refine["status"]:
            return Resolution()

    # Same reasoning for a contradicting TYPE, but only when the fragment ALSO
    # names a status -- then it routes fresh cleanly ("how many approved ISD
    # applications do I have?" -> "how many approved NISD?" -> the jurisdiction
    # NISD count). A bare "how many are ISD?" after an all-NISD list carries too
    # little to route fresh, so it is left to scope and answer the trivial
    # "none of those 50" instead of landing on the pending-ISD desk queue.
    if kind == FOLLOWUP_LIST_AGGREGATE and refine["application_type"] \
            and refine["status"] and not _has_plural_ptr:
        carried_t = _carried_list_type(context)
        if carried_t and carried_t != refine["application_type"]:
            return Resolution()

    # A bare aggregate that names a PERIOD the carried listing does not share is
    # a fresh count. "how many approved ISD applications do I have?" -> "how
    # many did I approve in 2024?" was scoped to the 2 ISD rows and answered
    # "2 of those 2 are approved". A period-scoped count is jurisdiction-wide.
    if kind == FOLLOWUP_LIST_AGGREGATE and not _has_plural_ptr:
        asked = _period_tokens(message)
        if asked and not (asked & _period_tokens(context.query_type or "")):
            return Resolution()

    # Carry the listing's own status filter forward when the follow-up does not
    # name one of its own. Without this, "show my rejected applications" ->
    # "their wards" re-queries with no status and `get_applications_by_numbers`
    # drops every row (rejected files stay out of an operational list unless
    # asked for), so the column answer comes back empty. Runs AFTER the
    # contradicting-status guard, which must see only what the message said.
    carried_status = _carried_list_status(context)
    if carried_status and not refine["status"]:
        refine["status"] = carried_status

    named_pos = ordinal_index(message)

    # ── One record in view ──────────────────────────────────────────────────
    single = context.single_application
    # A list of exactly one row IS one referent for a bare "this / that / what is it".
    if (not single and context.entity == ENTITY_APPLICATION_LIST and len(context.application_numbers) == 1
            and (is_bare_deictic(message) or is_deictic_field(message) or is_what_is_it(message))):
        single = context.application_numbers[0]
    if single and is_what_is_it(message):
        return Resolution(kind=kind, application_number=single, application_numbers=[single],
                          entity=ENTITY_APPLICATION, context=context)
    if single and is_bare_deictic(message):
        return Resolution(
            kind=kind, ambiguous=True, context=context,
            clarification=_clarify(
                f"What would you like to know about {single}? For example its status, field "
                f"visit, fee, documents or applicant.",
                f"{single} பற்றி என்ன தெரிய வேண்டும்? எடுத்துக்காட்டாக நிலை, கள ஆய்வு, "
                f"கட்டணம், ஆவணங்கள் அல்லது விண்ணப்பதாரர்.",
                language))
    if single:
        if named_pos is not None and named_pos > 1:
            # "the 3rd one" against a single-application context -- there is no
            # list here to index into. Ask rather than answer about the one
            # file that is in view.
            return Resolution(
                kind=kind, ambiguous=True, context=context,
                clarification=_clarify(
                    f"The previous answer was about a single application, so "
                    f"there is no number {named_pos}. Give the application "
                    f"number you mean.",
                    f"முந்தைய பதில் ஒரே ஒரு விண்ணப்பம் பற்றியது; எனவே "
                    f"{named_pos}-ஆவது இல்லை. நீங்கள் குறிப்பிடும் விண்ணப்ப "
                    f"எண்ணைத் தரவும்.",
                    language))
        return Resolution(
            kind=kind,
            application_number=single,
            application_numbers=[single],
            entity=context.entity,
            # The previous answer was about the visit, or this question is.
            about_visit=about_visit or context.entity == ENTITY_FIELD_VISIT,
            status=refine["status"],
            application_type=refine["application_type"],
            submission_channel=refine["submission_channel"],
            status_excluded=refine.get("status_excluded"),
            type_excluded=refine.get("type_excluded"),
            context=context,
        )

    # ── A list in view ──────────────────────────────────────────────────────
    if context.entity == ENTITY_APPLICATION_LIST and context.application_numbers:
        numbers = context.application_numbers
        _dir = nav_direction(message)
        if _dir:
            _cur = (context.filters or {}).get("cursor")
            if _cur in numbers:
                _i = numbers.index(_cur) + (1 if _dir == "next" else -1)
            else:
                _i = 0 if _dir == "next" else -1
            if not 0 <= _i < len(numbers):
                return Resolution(
                    kind=FOLLOWUP_SINGULAR, ambiguous=True, context=context,
                    clarification=_clarify(
                        f"That was the {'last' if _dir == 'next' else 'first'} of the {len(numbers)} application(s) in the list.",
                        f"அது பட்டியலின் {len(numbers)} விண்ணப்பங்களில் {'கடைசி' if _dir == 'next' else 'முதல்'} விண்ணப்பம்.",
                        language))
            _nb = numbers[_i]
            return Resolution(kind=FOLLOWUP_SINGULAR, application_number=_nb, application_numbers=[_nb],
                              entity=ENTITY_APPLICATION, context=context)
        # "remove row 2" / "exclude 2026/.../001167" -- drop the named row(s)
        # and re-render the table over what is left, rather than describing
        # the dropped row (which is what the ordinal/slice branches just
        # below this would otherwise do with the same message). A request
        # that names a row not actually in the list (a stale application
        # number, an ordinal past the end) falls through unchanged rather
        # than silently doing nothing -- `excluded_row_numbers` only returns
        # rows it actually found.
        _dropped = excluded_row_numbers(message, numbers)
        if _dropped is not None:
            _kept = [n for n in numbers if n not in _dropped]
            return Resolution(
                kind=FOLLOWUP_LIST_REFINE,
                application_numbers=_kept,
                entity=ENTITY_APPLICATION_LIST,
                status=refine["status"],
                application_type=refine["application_type"],
                submission_channel=refine["submission_channel"],
                status_excluded=refine.get("status_excluded"),
                type_excluded=refine.get("type_excluded"),
                rows_dropped=len(_dropped),
                context=context,
            )
        # "show more applications" / "less kami" -- there is no default row
        # cap on any listing in this app, so the carried set already IS the
        # complete answer to "more"; "less" has nothing to narrow by either,
        # since no criterion was named. Both are answered by saying so, over
        # the same re-read rows -- never by guessing a number to cut to.
        if _VAGUE_COUNT_RE.match(_correct_typos(message or "").lower().strip()):
            return Resolution(
                kind=FOLLOWUP_LIST_AGGREGATE,
                application_numbers=list(numbers),
                entity=ENTITY_APPLICATION_LIST,
                status=refine["status"],
                application_type=refine["application_type"],
                submission_channel=refine["submission_channel"],
                status_excluded=refine.get("status_excluded"),
                type_excluded=refine.get("type_excluded"),
                vague_count=True,
                context=context,
            )
        # "show both", "the first two", "last three". A run of the rows on
        # screen: neither a count of them nor a question about one file. Checked
        # ahead of everything else in this branch because the cues overlap both
        # -- "both" is an aggregate word, and "first two" carries the ordinal
        # "first", so without this the officer got a count for one and row 1
        # alone for the other.
        _run = slice_pick(message, numbers)
        if _run and len(_run) > 1:
            return Resolution(
                kind=FOLLOWUP_LIST_FIELD,
                full_details=True,
                application_numbers=_run,
                entity=ENTITY_APPLICATION_LIST,
                status=refine["status"],
                application_type=refine["application_type"],
                submission_channel=refine["submission_channel"],
                status_excluded=refine.get("status_excluded"),
                type_excluded=refine.get("type_excluded"),
                context=context,
            )
        # "even rows" / "odd rows" on a short list (2 rows: "even" is just
        # row 2) name exactly ONE row -- `slice_pick` correctly refuses to
        # call that a "run" (its own size floor), but nothing picked the
        # single row up either, and the message fell to the generic
        # ambiguous clarification for a request that named one row all
        # along. Treated the same as `ordinal_pick` finding a single row.
        if not _run:
            _alt_single = _alternate_row_pick(
                _correct_typos(message or "").lower(), numbers)
            if _alt_single and len(_alt_single) == 1:
                return Resolution(
                    kind=FOLLOWUP_SINGULAR,
                    application_number=_alt_single[0],
                    application_numbers=_alt_single,
                    entity=ENTITY_APPLICATION,
                    about_visit=about_visit,
                    status=refine["status"],
                    application_type=refine["application_type"],
                    submission_channel=refine["submission_channel"],
                    status_excluded=refine.get("status_excluded"),
                    type_excluded=refine.get("type_excluded"),
                    context=context,
                )
        # "details of both", "show me the full record for each of them". A
        # details request over the listed rows is neither a count nor a
        # question about one file, and it used to be read as the first of
        # those: "both" is an aggregate cue, so "give me the details of both"
        # was answered "2 of those 2 application(s) are pending" -- a count in
        # place of the records asked for. Checked ahead of the aggregate
        # branch, and only when no position was named ("details of the 2nd
        # one" still means row 2).
        # No upper bound. A cap here did not protect the officer from a wall of
        # text, it refused the question: "full details" over a 30-row list was
        # answered "too many to show in full at once", which is not an answer to
        # anything. The renderer already caps how many records it prints and
        # says how many there were, so the honest behaviour is to show the first
        # batch and name the remainder.
        if (wants_full_details(message) and named_pos is None
                and not ordinal_pick(message, numbers)
                and len(numbers) > 1):
            return Resolution(
                kind=FOLLOWUP_LIST_FIELD,
                full_details=True,
                application_numbers=list(numbers),
                entity=ENTITY_APPLICATION_LIST,
                status=refine["status"],
                application_type=refine["application_type"],
                submission_channel=refine["submission_channel"],
                status_excluded=refine.get("status_excluded"),
                type_excluded=refine.get("type_excluded"),
                context=context,
            )
        if kind in (FOLLOWUP_LIST_AGGREGATE, FOLLOWUP_LIST_REFINE):
            return Resolution(
                kind=kind,
                application_numbers=list(numbers),
                entity=ENTITY_APPLICATION_LIST,
                about_visit=about_visit,
                status=refine["status"],
                application_type=refine["application_type"],
                submission_channel=refine["submission_channel"],
                status_excluded=refine.get("status_excluded"),
                type_excluded=refine.get("type_excluded"),
                context=context,
            )
        # "the 9th one" of a 2-row list -- a position that points past the end.
        # Say how many rows there actually were rather than silently falling
        # through to the generic "which one?" clarification.
        if named_pos is not None and named_pos > len(numbers):
            return Resolution(
                kind=kind, ambiguous=True, context=context,
                clarification=_clarify(
                    f"That answer listed only {len(numbers)} application(s), so "
                    f"there is no number {named_pos}. Give the application "
                    f"number you mean, or a position from 1 to {len(numbers)}.",
                    f"அந்தப் பதிலில் {len(numbers)} விண்ணப்பங்கள் மட்டுமே "
                    f"இருந்தன; எனவே {named_pos}-ஆவது இல்லை. விண்ணப்ப எண்ணை, "
                    f"அல்லது 1 முதல் {len(numbers)} வரையிலான இடத்தைத் தரவும்.",
                    language))
        # A singular question against a list of one is unambiguous; against
        # several it is not, and picking the first would be a guess.
        if len(numbers) == 1:
            return Resolution(
                kind=FOLLOWUP_SINGULAR,
                application_number=numbers[0],
                application_numbers=list(numbers),
                entity=ENTITY_APPLICATION,
                about_visit=about_visit,
                status=refine["status"],
                application_type=refine["application_type"],
                submission_channel=refine["submission_channel"],
                status_excluded=refine.get("status_excluded"),
                type_excluded=refine.get("type_excluded"),
                context=context,
            )
        picked = ordinal_pick(message, numbers)
        if picked:
            return Resolution(
                kind=FOLLOWUP_SINGULAR,
                application_number=picked,
                application_numbers=[picked],
                entity=ENTITY_APPLICATION,
                about_visit=about_visit,
                status=refine["status"],
                application_type=refine["application_type"],
                submission_channel=refine["submission_channel"],
                status_excluded=refine.get("status_excluded"),
                type_excluded=refine.get("type_excluded"),
                context=context,
            )
        # A field question that fits every row is answered for every row. The
        # module's rule is never to GUESS a referent; covering all of them
        # guesses nothing, and the officer asked one question, not N.
        _field = field_for_each(message)
        if _field and len(numbers) <= MAX_PER_ROW_ANSWER:
            return Resolution(
                kind=FOLLOWUP_LIST_FIELD,
                per_row_field=_field,
                application_numbers=list(numbers),
                entity=ENTITY_APPLICATION_LIST,
                about_visit=about_visit,
                status=refine["status"],
                application_type=refine["application_type"],
                submission_channel=refine["submission_channel"],
                status_excluded=refine.get("status_excluded"),
                type_excluded=refine.get("type_excluded"),
                context=context,
            )
        shown = ", ".join(numbers[:3]) + ("…" if len(numbers) > 3 else "")
        return Resolution(
            kind=kind,
            ambiguous=True,
            context=context,
            clarification=_clarify(
                f"That answer covered {len(numbers)} applications ({shown}). "
                f"Which one do you mean? Give the application number, or say "
                f"\"the first one\".",
                f"அந்த பதிலில் {len(numbers)} விண்ணப்பங்கள் இருந்தன ({shown}). "
                f"எதைக் குறிப்பிடுகிறீர்கள்? விண்ணப்ப எண்ணைத் தரவும், அல்லது "
                f"\"முதலாவது\" எனக் கூறவும்.",
                language),
        )

    if context.entity == ENTITY_SURVEY and len(context.survey_numbers) == 1:
        return Resolution(kind=kind, entity=ENTITY_SURVEY,
                          about_visit=about_visit, context=context)

    return Resolution(
        kind=kind,
        ambiguous=True,
        context=context,
        clarification=_clarify(
            "Which application do you mean? Please give the application number.",
            "எந்த விண்ணப்பத்தைக் குறிப்பிடுகிறீர்கள்? விண்ணப்ப எண்ணைத் தரவும்.",
            language),
    )
