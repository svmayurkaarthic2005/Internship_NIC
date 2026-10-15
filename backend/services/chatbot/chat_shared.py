"""
Foundational helpers shared across the chat modules -- content/clear/mutation detection, domain-evidence scoring, service-code explain-request parsing, register-wide listing/filter detection, and the widely-used regex constants and word lists they and others depend on. No dependency on any other chat_*.py module.

Extracted from backend/services/chatbot.py (SIS_Chatbot_Modularity_Refactor,
chatbot.py >=15,000-line reduction pass) with no change in behavior -- every
function body below is copied verbatim from the original module. chatbot.py
re-exports the names it still needs so existing imports and monkeypatch
targets keep working unchanged.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional, List, Tuple, Sequence
from datetime import datetime, timedelta, date, timezone
import asyncio
import contextvars
import time
import difflib
import re
import json
import dataclasses
from pathlib import Path
import functools
import uuid
import calendar
from backend.config import (
    DISTRICT_NAME_MAP, DISTRICT_TAMIL_NAME_MAP, DISTRICT_CODE_TO_TAMIL_NAME, settings,
)
from backend.schemas import OfficerContext
from . import attachment_qa
from .agent import AgentUnavailable, run_agent, run_agent_stream
from backend.services.readonly_guard import chat_turn
from . import neg_scope as _neg_scope
from . import negq as _negq
from . import calc_qa as _calc_qa
from . import howto_qa as _howto_qa
from . import card_html as _card_html
from . import unheld_qa as _unheld_qa
from . import recheck as _recheck
from . import number_qa as _number_qa
from . import stats_qa as _stats_qa
from . import compare_followup as _compare_followup
from . import qualifier_guard as _qualifier_guard
from backend.services import followup_context as fctx
from . import semantic_intent
from . import subdivision_qa as _subdivision_qa
from . import oldowner_qa as _oldowner_qa
from . import cmflow_qa as _cmflow_qa
from . import appinfo_qa as _appinfo_qa
from . import fline_qa as _fline_qa
from . import merge_qa as _merge_qa
from . import crosscheck_qa as _crosscheck_qa
from . import igrs_owner_qa as _igrs_owner_qa
from . import newowner_qa as _newowner_qa
from . import fulltransfer_qa as _fulltransfer_qa
from . import sdattach_qa as _sdattach_qa
from . import tslr_qa as _tslr_qa
from . import uamod_qa as _uamod_qa
from . import natham_qa as _natham_qa
from . import othercode_detail as _othercode_detail
from backend.services.rag import (
    detect_language,
    is_short_reply,
    get_prior_turn_language,
    detect_jurisdiction_focus,
    wants_details_of_listed,
    requested_listing_count,
    extract_sort_order,
    extract_row_selection,
    extract_result_limit,
    get_rag_context_async,
    build_prompt,
    build_html_response,
    _money,
    call_llama,
    call_llama_stream,
    paraphrase_facts_naturally,
    parse_intent,
    parse_last_application_query,
    parse_visit_plan_query,
    extract_survey_number,
    extract_application_number,
    extract_application_numbers,
    extract_ward_number,
    extract_block_number,
    extract_town_name,
    extract_taluk_name,
    extract_date_range,
    extract_month_scopes,
    format_month_scopes,
    extract_submission_channels,
    ambiguous_channel_clarification,
    _get_projected_application_columns,
    is_bare_date_scope,
    strip_date_scope_phrases,
    extract_date_scope_fragment,
    DATE_SCOPED_INTENTS,
)
from backend.services.postgres import (
    get_all_service_code_applications,
    get_can_details,
    get_last_application,
    get_visit_plan,
    get_applications_by_block,
    get_ward_directory,
    get_pending_applications,
    count_rejected_by_channel,
    get_overdue_applications,
    get_officer_workload,
    get_officer_workload_applications,
    get_application_detail,
    get_survey_detail,
    get_survey_owners,
    get_unscheduled_visits,
    get_applications_by_numbers,
    get_field_visits,
    get_next_subdivision_number,
    check_survey_application_lock,
    get_jurisdiction_filter,
    get_ward_surveys,
    get_all_surveys_in_jurisdiction,
    get_officer_applications,
    get_fee_summary,
    get_recent_fees,
    get_field_presence,
    _application_jurisdiction_subquery,
    _field_visit_jurisdiction_subquery,
)
from backend.models import (
    ChatSession, ChatMessage, Application, SurveyNumber, Block, Ward, Town, Taluk,
    FieldVisit, ApplicationDocument, WorkflowHistory, ApplicationSubDivision,
    OfficerJurisdiction, District, SISOfficer, messages_since_session_start
)
from backend.utils.helpers import service_code_one_liner
from backend.utils.logger import get_logger
from backend.utils import translit
from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import selectinload
from backend.utils.fuzzy import (
    extract_month_from_text,
    extract_tokens,
    is_token_typo_match,
    match_phrase,
    normalize_text,
    resolve_unique_entry,
)
from backend.utils.logger import get_logger

logger = get_logger(__name__)



# Word-boundary match for a district name inside a message. A plain substring
# test ("erode" in msg) false-matches ordinary English -- "erode"/"eroded" is
# a common verb, so "does encroachment erode the boundary" was answered with
# Erode's district code. \b keeps it to the whole word.
_DISTRICT_NAME_RE = re.compile(
    r"\b(" + "|".join(re.escape(_n) for _n in DISTRICT_NAME_MAP) + r")\b", # checks district names in English with regex
    re.IGNORECASE)
# Tamil-script district name ("கோயம்புத்தூர் மாவட்ட குறியீடு என்ன?" -- "what is
# Coimbatore's district code?"). `\b` cannot be used here: every Tamil district
# name ends in a vowel sign or virama-marked consonant, neither of which is
# `\w`, so a trailing `\b` never closes (the same trap `_month_name_re` in
# rag.py documents for month names) -- a lookaround against the Tamil Unicode
# block is used instead. Without this, a Tamil district name matched nothing
# in DISTRICT_NAME_MAP (English-only), fell through to the LLM tool-calling
# agent, and could take 30+ seconds -- once long enough to exceed a whole test
# suite's timeout budget -- for what is a one-line table lookup.
_DISTRICT_NAME_TA_RE = re.compile(
    r"(?<![஀-௿])(" + "|".join(re.escape(_n) for _n in DISTRICT_TAMIL_NAME_MAP) + r")(?![஀-௿])")


# 1-indexed so _MONTH_NAMES[6] == "June".
_MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"]


def token_with_tamil_suffix_pattern(token: str, disallow_slash: bool = False) -> str:
    """Build a regex pattern matching `token` optionally followed by a Tamil case suffix or hyphen.

    Handles Tamil case suffixes like -க்கு, -ற்கு, -யில், -இல், -வில், -ல், -ஐ, -ஆல்,
    -உடன், -இன், -ஆக, -தானா, -ஆ, -வது, -ஓடு, -களின், -கள், -உடைய.
    Because Python regex \\b treats Tamil characters as \\w, \\bTOKEN\\b never matches TOKENக்கு.
    Lookarounds ensure word boundaries across both Latin/ASCII and Tamil scripts.
    """
    pre = r"(?<![/A-Za-z0-9\u0B80-\u0BFA])" if disallow_slash else r"(?<![A-Za-z0-9\u0B80-\u0BFA])"
    post = r"(?![/A-Za-z0-9\u0B82-\u0BD7])" if disallow_slash else r"(?![A-Za-z0-9\u0B82-\u0BD7])"
    return rf"{pre}{re.escape(token)}(?:-)?(?:க்கு|ற்கு|யில்|இல்|வில்|ல்|ஐ|ஆல்|உடன்|இன்|ஆக|தானா|ஆ|வது|ஓடு|களின்|கள்|உடைய)?{post}"


# ─────────────────────────────────────────────────────────────────────────────
# Messages that ask nothing
#
# `parse_intent` resolves ~60 intents and routes everything it does not
# recognise to `general_query`, which now means the agent layer. That is right
# for a real question phrased in a way no handler matched -- and wrong for a
# message that is not a question at all. An officer typing "clear" expects the
# transcript to be wiped; what happened instead was that llama3.1:8b searched
# the policy corpus for the word "clear", and the officer read a sentence about
# a tool argument limit. "ok", "cls", "..." went the same way.
#
# These are answered here, deterministically, before intent routing gets near
# them: no LLM call, no tool call, and an answer that tells the officer
# something true.
#
# The set is deliberately closed and matched whole. Anything that might be a
# real question -- including "yes" and "no", which can answer a disambiguation
# the assistant itself asked -- is left to the pipeline. Guessing that a
# message is meaningless is a far worse failure than sending one extra message
# down the normal path.

# "clear" is a command, so it is CARRIED OUT, not explained. Telling an officer
# who typed "clear" to go and press a button instead is the same failure in a
# politer register: they said what they wanted and the assistant answered with
# homework. The turn returns action="clear_chat" and the frontend wipes the
# transcript and opens a fresh session.
_CLEAR_COMMAND_WORDS = frozenset({
    "clear", "clear chat", "clear all", "clear history", "clear conversation",
    "clear the chat", "clear chats", "cls", "clr", "reset", "reset chat",
    "restart", "start over", "new chat", "newchat", "new conversation",
    "wipe", "erase",
    # Tamil / Tanglish
    "அழி", "அழிக்கவும்", "மீட்டமை", "புதிய உரையாடல்", "alli", "azhi",
})

# Not a clear, and not something a chat turn can do: these need a control that
# lives outside the conversation. They get told which one, once.
_SESSION_COMMAND_WORDS = frozenset({
    "exit", "quit", "close", "logout", "log out", "sign out", "signout",
    "வெளியேறு",
})

_ACKNOWLEDGEMENT_WORDS = frozenset({
    "ok", "okay", "okey", "k", "kk", "kkk", "okk", "okkk", "okie", "oki", "fine", "alright", "all right",
    "done", "got it", "gotit", "cool", "nice", "great", "hmm", "hm",
    "hmmm", "sure", "right", "noted", "understood",
    "yes", "yeah", "yep", "yup", "ya", "yaa", "no", "nope", "nah", "correct",
    "exactly", "true", "aama", "ama", "aamam", "illa", "illai", "ஆம்", "இல்லை",
    "ஆமா", "சரிதான்", "apdi illa", "appadi illa", "illa apdi illa", "அப்படி இல்லை",
    "lol", "lmao", "loll", "lols", "haha", "hahaha", "hehe", "rofl", "xd",
    "yo", "sup", "bruh", "meh", "eh", "huh", "wat", "nvm", "never mind",
    # Tanglish casual opener -- "enapa" ("what's up, da?") carries no content
    # of its own, the same as "yo"/"sup" above. Without this, "enapa" matched
    # no ack word, no greeting, and no intent, so it fell to the agent layer,
    # which called search_documents("enapa"), found nothing, and narrated the
    # failed tool call back as an answer ("The search for 'enapa' did not find
    # any documents.").
    "enapa", "yenapa", "eppa",

    "சரி", "seri", "sari", "okda", "seriya",
    # Tamil-script transliteration of the same laughter -- "லோல்" is how "lol"
    # is actually typed on a Tamil keyboard, not the Latin spelling.
    "லோல்", "லோலு",
    "ஓகே", "சூப்பர்", "சரியா", "இல்ல பரவாயில்ல",
})

# Laughter has open-ended repetition ("hahaha", "lolol", "lmaooo", "roflll")
# that a fixed word list cannot enumerate -- caught by shape (character
# composition) instead. Same rationale as `_is_clear_command` matching the
# wipe command by shape rather than by a closed literal set. Restricted to a
# handful of alphabets that carry no other meaning in this domain, so a real
# domain word is never at risk of a false match.
_LAUGH_RE = re.compile(
    r'^(?:[ha]{3,20}|[he]{3,20}|[lo]{3,20}|l+m+f?a+o+|r+o*f+l+'
    r'|(?:ஹ|ஹா|ஹே){2,10})$'
)


# A dismissal or insult ("poda", "get lost", "useless") asks nothing either. Left
# to the pipeline it reaches the agent, which has no question to answer and
# improvises -- "You cannot see that record.", "I'm not sure what you're
# asking", or a 45-second timeout, depending on the run.
_DISMISS_WORDS = frozenset({
    "poda", "podaa", "podaa", "podi", "po", "pongada", "poda loosu", "podaa loosu",
    "get lost", "go away", "shut up", "shutup", "stupid", "idiot", "useless",
    "loosu", "loose", "nonsense", "bakwas", "waste", "worst", "pathetic",
    "போடா", "போடி", "போ", "லூசு", "முட்டாள்", "பயனற்றது",
})


def _is_ack_word(text: str) -> bool:
    """True for an acknowledgement/filler word, including laughter shapes."""
    return text in _ACKNOWLEDGEMENT_WORDS or bool(_LAUGH_RE.match(text))


# A politeness prefix ("சார்", "தயவு செய்து") or an address particle glued on
# as its own word ("sari da", "seri pa") turned "சார் நன்றி" / "sari da" into
# `general_query` even though the officer said nothing but thanks -- the exact
# frozenset lookup in `_contentless_message` only matched the bare word.
# Stripped the same way `_is_clear_command` strips `_CLEAR_FILLER` before
# testing what is left.
_ACK_FILLER_TOKENS = frozenset({
    "சார்", "சார", "அண்ணா", "அண்ணே", "தம்பி", "மேடம்", "சாமி",
    "please", "pls", "plz", "kindly", "sir", "bro", "anna", "boss",
    "da", "pa", "di", "machi", "machan", "dude", "man",
    "தயவு", "செய்து", "தயவுசெய்து", "கொஞ்சம்",
})


def _is_ack_message(text: str) -> bool:
    """Whole-message ack test, tolerant of a politeness/address wrapper."""
    tokens = [t.strip(_TRIM_CHARS) for t in text.split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return False
    content = [t for t in tokens if t not in _ACK_FILLER_TOKENS]
    if not content:
        return False
    return all(_is_ack_word(t) for t in content)


_ADD_COLUMNS_RE = re.compile(
    r"(?:also\s+)?(?:add|include)\s+(?:the\s+)?(?:(?:ward|block|district|taluk|town|status|stage|type|"
    r"survey(?:\s+number)?|sub[\s-]?division(?:\s+number)?|fee|name|applicant(?:\s+name)?|mobile|phone|"
    r"address|date|channel|can|patta)s?"
    r"(?:\s*(?:,|and|&)\s*|\s+)?)+(?:also|too)?")


_TRIM_CHARS = " \t\r\n.!?,;:'\"()[]{}<>-_*"

# The literal set above covers what officers type most, and nothing else. It
# missed the ordinary longer wordings -- "clear the conversation", "clear chat
# history", "delete this chat" -- which fell through to the pipeline and were
# answered as questions. These are the same command, so they are recognised by
# shape instead of by enumeration: a wipe verb, then nothing but filler and
# words for the transcript itself. Still matched WHOLE, which is what keeps
# "clear my pending applications" a listing query and not a wipe.
_CLEAR_VERBS = frozenset({
    "clear", "reset", "wipe", "erase", "delete", "remove", "flush", "clean",
    # Gerund / progressive forms: "Would you mind clearing the conversation?"
    # is the same command. Gerunds of domain-verbs ("clearing the record",
    # "deleting the application") still fail because the object word is not a
    # transcript noun -- the safety property is unchanged.
    "clearing", "resetting", "wiping", "erasing", "deleting", "removing",
    "flushing", "cleaning",
    "expunge", "expunging", "purge", "purging", "discard", "discarding", "forget", "forgetting",
    "restart", "restarting", "dismiss", "cls",
})
# A verb that is a wipe on its own; the rest need to name what to wipe, since
# "delete" alone says nothing about what.
_CLEAR_BARE_VERBS = frozenset({"clear", "reset", "wipe", "erase"})
_CLEAR_NOUNS = frozenset({
    "chat", "chats", "conversation", "conversations", "history", "message",
    "messages", "transcript", "screen", "thread", "session", "everything",
    "convo", "convos", "talk", "dialogue", "threads", "sessions",
})
_CLEAR_FILLER = frozenset({
    "please", "pls", "plz", "kindly", "the", "this", "that", "these", "those",
    "a", "an", "my", "our", "all",
    "your", "all", "above", "previous", "prior", "entire", "whole", "full",
    "old", "current", "up", "out", "now", "here", "and", "of", "it",
    # Politeness wrapping a long, natural request -- "Can you please clear
    # this whole conversation for me now", "I want you to reset this chat" --
    # is still the same command. None of these carry content of their own, so
    # adding them costs nothing: a real question keeps at least one word that
    # is neither the verb, a transcript noun, nor filler (see
    # "applications", "database", "sketch" in the test suite), and still
    # fails the check below.
    "can", "could", "would", "should", "will", "might", "want", "wanna",
    "need", "like", "to", "for", "me", "us", "you", "i", "we", "go", "ahead",
    "if", "hey", "ok", "okay", "just", "gonna", "do", "right", "completely",
    "entirely", "totally", "fully", "in", "on", "at", "with", "from",
    # "Would you **mind** clearing..." -- pure politeness, not a domain word.
    "mind",
    # Tanglish politeness / verb: "clear pannu", "chat ah clear panren"
    "pannu", "panren", "pannunga", "panunga", "pannungo", "pannidu", "seyyu", "seiyyu", "ah", "konjam", "da", "pa",
    "um", "pazhaya", "puzhaya", "ellaa", "ella", "muzhu", "mothama", "out", "old", "previous", "entire",
    "earlier", "past", "older", "prior", "again", "panidu", "panidunga", "pannalam", "pannunga", "pls",
})
# "start a new chat" / "open new conversation" -- a new-chat wording rather
# than a wipe one, but the same command from the officer's side. Also
# tolerated inside a primary verb's own filler ("delete this and start
# fresh"), so they sit in the shared filler set too.
_CLEAR_START_VERBS = frozenset({"start", "begin", "open", "new"})
_CLEAR_START_WORDS = frozenset({"new", "fresh", "another", "anew"}) | _CLEAR_FILLER
_CLEAR_FILLER = _CLEAR_FILLER | frozenset({"start", "begin", "open", "fresh", "anew"})

# Tamil: matched as substrings, never with \b -- the virama is not a word
# character, so a boundary can land mid-word. Same trap CLAUDE.md documents
# for the comparison parser and the follow-up layer.
_CLEAR_TA_VERBS = ("அழி", "நீக்க", "மீட்டமை", "துடை", "அகற்ற", "கிளியர்", "டெலீட்", "டெலிட்", "ரிமூவ்")
# A wipe verb aimed at the department's record is a change request, not a
# "clear the chat" command.
_TA_DATA_NOUNS = ("விண்ணப்ப", "பதிவ", "தரவ", "ஆவண", "வார்டு", "பிளாக்", "சர்வே", "பட்டா")
# Standard Tamil words for the transcript, plus the transliterated loanwords
# officers actually type ("சாட் ஹிஸ்டரி" for "chat history").
_CLEAR_TA_NOUNS = ("உரையாடல", "அரட்ட", "செய்தி", "வரலாற",
                   "சாட்", "ஹிஸ்டரி", "மெசேஜ்", "செஷன்")


_CLEAR_PHRASE_RE = re.compile(
    r"^(?:please\s+|pls\s+)?(?:start\s+(?:over|fresh|afresh|again)|fresh\s+start|begin\s+again|new\s+(?:conversation|session|convo|chat)"
    r"|end\s+(?:this|the)\s+(?:conversation|chat|convo)(?:\s+and\s+start\s+(?:a\s+)?new(?:\s+one)?)?"
    r"|(?:puthu|puthusa|pudhu|pudhusa)\s*(?:chat|conversation|convo)?\s*start(?:\s+pann\w*|\s+panr\w*)?"
    r"|புதிதாக(?:த்)?\s+தொடங்கு\w*|புதிய\s+உரையாடலை(?:த்)?\s+தொடங்கு\w*)(?:\s+please)?$", re.IGNORECASE)
_TANGLISH_CLEAR_VERB_RE = re.compile(r"\b(?:azhi\w*|neekk?u\w*|nekku\w*|allidu|thodai|thudai)\b")


def _is_clear_command(text: str) -> bool:
    """Whole-message test for 'wipe this conversation', in any usual wording."""
    if any(n in text for n in _TA_DATA_NOUNS):
        return False
    text = re.sub(r"\bget\s+rid\s+of\b", "delete", text)
    text = re.sub(r"\b(?:wipe|clean)\s+(?:out|up)\b", "wipe", text)
    text = re.sub(r"\b(?:the\s+)?whole\b|\ball\s+the\b", "the", text)
    if _CLEAR_PHRASE_RE.match(text.strip(_TRIM_CHARS + " ")):
        return True
    text = _TANGLISH_CLEAR_VERB_RE.sub("clear", text)
    if any(v in text for v in _CLEAR_TA_VERBS) and (
            len(text) <= 40 or any(n in text for n in _CLEAR_TA_NOUNS)):
        return True
    # Strip leading/trailing punctuation from each token so "hey," matches "hey"
    # in _CLEAR_FILLER. The whole message already had _TRIM_CHARS removed before
    # this function is called, but mid-message commas still attach to the word
    # before them after a whitespace split.
    tokens = [t.strip(_TRIM_CHARS) for t in text.replace("-", " ").split()]
    tokens = [t for t in tokens if t]
    # No hard word-count cap: "Hey, could you go ahead and clear the entire
    # conversation history for me" is a real, long, polite sentence and still
    # only the command. Safety comes from the content check below, not from
    # length -- a long REAL question always keeps a word that is neither the
    # verb, a transcript noun, nor filler, and is rejected there instead. A
    # `len(tokens) > 40` cap used to sit here, contradicting this comment: it
    # missed a genuine long-winded clear request (52 tokens of pure filler)
    # and let it fall through unrecognised, so it is gone.
    if not tokens:
        return False
    verb_at = next((i for i, t in enumerate(tokens) if t in _CLEAR_VERBS), None)
    if verb_at is not None:
        head = tokens[verb_at]
        rest = tokens[:verb_at] + tokens[verb_at + 1:]
        if any(t not in _CLEAR_NOUNS and t not in _CLEAR_FILLER for t in rest):
            return False
        named = any(t in _CLEAR_NOUNS for t in rest)
        # A trailing verb ("chat history clear") only reads as a command
        # because the noun is there; "all clear" is an acknowledgement.
        if verb_at != 0:
            return named
        return named or head in _CLEAR_BARE_VERBS
    head, rest = tokens[0], tokens[1:]
    if head in _CLEAR_START_VERBS:
        # "new chat" / "start a new conversation" -- must name the transcript,
        # so "new application" and "open 2026/0154/28/001280" stay questions.
        if any(t not in _CLEAR_NOUNS and t not in _CLEAR_START_WORDS
               for t in rest):
            return False
        return any(t in ("chat", "chats", "conversation", "conversations",
                         "thread", "session") for t in tokens)
    return False


# ── "Explain in detail" follow-up detection ─────────────────────────────────
_DETAIL_REQUEST_PHRASES = [
    # English — multi-word phrases first so regex tries longest match first
    "explain in detail", "explain more", "more detail", "more details",
    "tell me more", "full detail", "full details", "full breakdown",
    "elaborate", "in depth", "in-depth", "detailed explanation",
    "more info", "more information", "give me more", "what does it mean",
    "what does that mean", "break it down", "break down", "go deeper",
    "expand on that", "expand on it", "expand on this", "expand",
    # bare single-word triggers ("detailed", "detail", "explain", "describe", "continue", "resume")
    "detailed", "detail", "explain", "describe", "continue", "resume",
    # "give me a thorough explanation" / "a thorough answer" -- the noun form
    # of "explain" and a synonym for "detailed" that weren't in this list at
    # all, so neither `_has_detail_phrase` nor the typo-tolerant trigger-word
    # fallback (which only budgets a 1-2 edit slip, not a different word)
    # caught them, and the message fell through to `general_query`.
    "explanation", "thorough",
    # Tanglish (Latin script -- \b works the same as it does for English)
    "vivaramaga solunga", "vivaramaga vilakku", "vivaramaga",
    "explain panunga", "explain pannu", "more sollu", "more vithufy",
    "full ah sollu", "full sollu", "details sollu", "details kudunga",
    "innum sollu", "meethi sollu", "adutha vivaram",
]

_DETAIL_REQUEST_RE = re.compile(
    r'\b(?:' +
    '|'.join(re.escape(p) for p in sorted(_DETAIL_REQUEST_PHRASES, key=len, reverse=True)) +
    r')\b',
    re.IGNORECASE,
)

# Tamil-script phrases are matched as plain substrings, never through
# `_DETAIL_REQUEST_RE`'s `\b` -- the same virama trap CLAUDE.md documents for
# the comparison parser and the follow-up layer: a trailing consonant+virama
# (ள/ம்/...) is not a `\w` character, so `\b` lands mid-syllable instead of at
# the word's real edge, and every Tamil-script phrase that used to live inside
# that regex silently never matched. `re.search(r'\bவிரிவாக விளக்கவும்\b', ...)`
# returns None even when the exact text "விரிவாக விளக்கவும்" is right there.
# Each verb's conjugations (imperative "விளக்கு", polite "விளக்கவும்", plural
# "விளக்குங்கள்") are listed separately -- one is not a substring of another.
_DETAIL_REQUEST_PHRASES_TA = [
    "விரிவாக விளக்கு", "விரிவாக விளக்கவும்", "விரிவாக விளக்குங்கள்",
    "விரிவாக சொல்லு", "விரிவாக சொல்லவும்", "விரிவாக சொல்லுங்கள்",
    "விரிவாக கூறு", "விரிவாக கூறவும்", "விரிவாக கூறுங்கள்",
    "விவரமாக சொல்", "விவரமாக சொல்லவும்", "விவரமாக விளக்கு", "விவரமாக விளக்கவும்",
    "முழு விளக்கம்", "கூடுதல் விவரம்", "கூடுதல் தகவல்",
    "தொடரவும்", "மேலும் விவரங்கள்", "மீதி சொல்லவும்", "அடுத்த விவரம்",
]


def _has_detail_phrase(message: str) -> bool:
    """`_DETAIL_REQUEST_RE` (English/Tanglish, `\\b`-bounded) OR a Tamil-script
    phrase (substring -- see `_DETAIL_REQUEST_PHRASES_TA` for why no `\\b`)."""
    if _DETAIL_REQUEST_RE.search(message or ""):
        return True
    return any(p in (message or "") for p in _DETAIL_REQUEST_PHRASES_TA)


_DETAIL_REQUEST_TRIGGER_WORDS = ("explain", "elaborate", "detailed", "detail", "describe", "continue", "resume", "expand")

# "explain BRIEFLY" / "summarise it" / "the short version" asks for LESS
# detail, not more -- a brevity word wins over a bare "explain"/"describe" in
# the same message. Without this, "explain briefly" set brief=False (the
# full multi-bullet answer) simply because it also contains "explain",
# answering the opposite of what was asked.
_BRIEF_REQUEST_WORDS = ("brief", "briefly", "short", "shortly", "quick", "quickly",
                        "summary", "summarize", "summarise", "concise", "concisely")

# Tamil-script brevity words -- checked as plain substrings, not through the
# Latin-token typo-match loop above (`re.findall(r"[a-zA-Z]+", ...)` finds
# nothing in a pure Tamil-script message, so "சுருக்கமாக சொல்லு" -- "say it
# briefly" -- carried no brevity signal at all and fell through to "I don't
# understand" after a service-code explanation, in a message with zero Latin
# characters to match against).
_BRIEF_REQUEST_WORDS_TA = ("சுருக்கமாக", "சுருக்கமா", "சுருக்கமாய்", "சுருக்கம்",
                           "surukkamaaga", "surukkamaga", "surukkama")

# Multi-word brevity cues that single-token matching against
# `_BRIEF_REQUEST_WORDS` can't catch -- "sentence" and "words" are common
# enough words that adding them to that list unqualified would false-match
# unrelated messages, so they are only treated as a brevity request in these
# specific phrasings. Without this, "explain in a sentence" / "explain in
# simple words" carried the bare trigger word "explain" (in
# `_DETAIL_REQUEST_TRIGGER_WORDS`) and NOTHING recognised as brief, so
# `_is_detail_request` read it as a request for MORE detail -- the opposite
# of what was asked -- and "tell me in one sentence" (no explain/describe
# word at all) didn't even register as a re-explain request.
_SENT_ADJ = (r"(?:detailed|detail|full|complete|long|short|brief|simple|plain|"
            r"single|proper|clear|concise)")
_BRIEF_REQUEST_PHRASES_RE = re.compile(
    # "sentence" names the FORM (prose, not a bulleted card); an adjective
    # before it ("in a DETAILED sentence", "a detailed sentence") names how
    # thorough that prose should be, not a switch to the card. Without the
    # adjective group, "in detailed sentence" fell through this regex (the
    # bare "in sentence(s)" alternative needs "in" immediately before
    # "sentence", with nothing but "a"/"one" allowed between them) to the
    # bare word "detailed" in `_has_detail_phrase`, which reads any mention
    # of "detailed" as "give the full bulleted card" -- so an explicit
    # request for a SENTENCE still got the 9-row table.
    rf'\bin\s+(?:a\s+|one\s+|the\s+)?(?:{_SENT_ADJ}\s+)?sentences?\b|'
    rf'\b{_SENT_ADJ}\s+sentences?\b|'
    r'\bsentence\s+or\s+two\b|'
    # "sentence/short/summary FORMAT" named without a leading "in" --
    # "don't use sentence format" has no "in" for the alternative above to
    # anchor on.
    r'\b(?:sentence|short|summary)\s+format\b|'
    r'\b(?:in\s+)?simple\s+(?:words|terms|language)\b|'
    r'\b(?:in\s+)?plain\s+(?:words|terms|language)\b|'
    r'\bone[\s-]?liner\b|\bin\s+a\s+few\s+words\b|\bin\s+short\b',
    re.IGNORECASE,
)

# "answer in 20 words", "explain within 25 words", "in not more than 15
# words", "20 words or less/fewer" -- a word-count ceiling. The register
# answers are fixed, deterministic text (never truncated or LLM-rewritten to
# an exact length -- that risks cutting a fact mid-sentence), so this is
# treated the same as any other brevity request: the shortest honest answer
# already on hand (the one-liner / brief chain), not a literal N-word cut.
# Without this, "explain within 25 words" carried the bare trigger word
# "explain" and nothing recognised as brief, so it was read as a DETAIL
# request and answered with the full bulleted card -- the opposite of a
# 25-word ceiling.
_WORD_LIMIT_RE = re.compile(
    r'\b(?:in|within|under)\s+(?:not\s+more\s+than\s+)?\d{1,3}\s*words?\b'
    r'|\bnot\s+more\s+than\s+\d{1,3}\s*words?\b'
    r'|\b\d{1,3}\s*words?\s+or\s+(?:less|fewer)\b',
    re.IGNORECASE,
)

# A NEGATED brevity instruction -- "dont explain in sentence", "not in
# sentence format", "don't use sentence format" -- asks for the OPPOSITE of
# the phrase it names: the full, detailed explanation, not the one-liner the
# phrase itself describes. Without this, the negator was ignored outright:
# `_BRIEF_REQUEST_PHRASES_RE` matched "in sentence" regardless of the "dont"
# in front of it, so the negative instruction was read as a repeat of the
# positive one and produced the SAME one-line answer the officer had just
# said not to give.
#
# The negator must govern the format phrase itself, not some unrelated
# clause in the same message -- "I don't understand ISD; explain it in a
# sentence" is still a plain request for a sentence, since the "don't" there
# negates "understand", not "in a sentence". The message is split on clause
# boundaries first (a semicolon, a sentence end, or "but"/"however"/...), and
# only a negator and a format cue inside the SAME clause, within a few words
# of each other, counts.
_CLAUSE_SPLIT_RE = re.compile(r'[;.!?]|\b(?:but|however|although|though)\b|ஆனால்', re.IGNORECASE)
_FORMAT_NEGATOR_RE_PART = r"(?:don'?t|do\s+not|dont|never|not)"
_FORMAT_NEGATED_RE = re.compile(
    rf"\b{_FORMAT_NEGATOR_RE_PART}\b(?:\s+\w+){{0,4}}?\s+(?:{_BRIEF_REQUEST_PHRASES_RE.pattern})",
    re.IGNORECASE,
)
# Tanglish follows the same postfix idiom `neg_scope.py` already uses for
# "ISD vendam" (= "not ISD"): the negator trails the noun it negates, so
# "sentence-a venda" / "short beda" negates the format word in front of it.
_FORMAT_NEGATED_POSTFIX_RE = re.compile(
    # the optional `[\s-]*\w{0,3}` soaks up a Tanglish case suffix ("-a", "-o")
    # between the noun and the negator, as in "short-a beda"
    r'\b(?:sentence|short|brief|summary|simple)\w*[\s-]*\w{0,3}[\s-]*(?:venda\w*|vendam\w*|beda\w*|illa\w*|kudathu\w*)\b',
    re.IGNORECASE,
)
_FORMAT_NEGATOR_TA = ("வேண்டாம்", "இல்லை", "கூடாது")


# "what happens at each stage?" / "what happens at every step?" -- a process
# question phrased without the word "explain"/"detail" at all, so neither
# `_has_detail_phrase` nor `_DETAIL_REQUEST_TRIGGER_WORDS` caught it and it
# fell to `general_query` instead of the service-code's own detailed answer.
# Narrow on purpose (just this one shape) -- a bare "what happens" with no
# "stage"/"step" is left alone (it is already its own self-contained-question
# rule elsewhere, `_followup_is_self_contained`'s "what happens" check).
_STAGE_QUESTION_RE = re.compile(
    r"\bwhat\s+(?:happens?|occurs?|takes?\s+place)\s+(?:at|in|during)\s+(?:each|every)\s+(?:stage|step)\b",
    re.IGNORECASE)


def _is_format_negation_request(message: str) -> bool:
    """True for "don't explain in a sentence", "not in sentence format",
    "sentence-a venda" -- a NEGATED format instruction. These ask for the
    detailed answer, not the brief one the phrase itself names, so callers
    must check this BEFORE treating a matched brevity phrase as a request
    for brevity."""
    msg = message or ""
    if _FORMAT_NEGATED_POSTFIX_RE.search(msg):
        return True
    for clause in _CLAUSE_SPLIT_RE.split(msg):
        if _FORMAT_NEGATED_RE.search(clause):
            return True
        if (any(neg in clause for neg in _FORMAT_NEGATOR_TA)
                and (any(w in clause for w in _BRIEF_REQUEST_WORDS_TA)
                     or _BRIEF_REQUEST_PHRASES_RE.search(clause))):
            return True
    return False


# The mirror image of `_FORMAT_NEGATED_RE`: a negator governing a DETAIL
# phrase ("don't explain in detail", "no need for a detailed explanation",
# "not in detail please") asks for the SHORT answer, not a repeat of the
# detailed one the phrase itself names. Without this, "don't explain in
# detail" contained the bare word "detail" (one of `_DETAIL_REQUEST_PHRASES`),
# so `_has_detail_phrase` matched it and `_is_detail_request` returned True --
# exactly backwards from what was asked.
_DETAIL_NEGATED_RE = re.compile(
    rf"\b{_FORMAT_NEGATOR_RE_PART}\b(?:\s+\w+){{0,4}}?\s+(?:{_DETAIL_REQUEST_RE.pattern})"
    # "no need for (a) detailed explanation", "no more detail(s) needed" --
    # "no" on its own is too common a word to add to the shared negator
    # above (it would false-match unrelated "no overdue applications"-style
    # sentences), so this narrower, self-contained shape is checked instead.
    r"|\bno\s+(?:need|further)\s+(?:for\s+)?(?:a\s+|more\s+)?detail(?:ed|s)?\b"
    # "no detail", "no details" -- a short, bare refusal with nothing else in
    # the message for `_has_detail_phrase` to misread as a POSITIVE request.
    # The explain-reroute concatenates an unqualified follow-up with the
    # PREVIOUS question with no punctuation between them ("no detail what is
    # service code 0154?"), so this cannot be fenced to end-of-string the way
    # a standalone check could be -- same unanchored shape as "no need for
    # details" right above it.
    r"|\bno\s+detail(?:ed|s)?\b"
    # "skip the detail(s)", "skip detail", "avoid detail(s)" / "avoid a
    # detailed explanation" -- "skip"/"avoid" name the same refusal as
    # "don't"/"not" without being one of them.
    r"|\b(?:skip|avoid)(?:ping|ing)?\b(?:\s+\w+){0,4}?\s+(?:" + _DETAIL_REQUEST_RE.pattern + r")"
    # "without going into detail", "without a detailed explanation" -- a
    # second common negator verb the generic pattern above doesn't carry
    # (`_FORMAT_NEGATOR_RE_PART` is `don't`/`do not`/`dont`/`never`/`not`
    # only).
    r"|\bwithout\b(?:\s+\w+){0,4}?\s+(?:" + _DETAIL_REQUEST_RE.pattern + r")"
    # "nothing too detailed", "nothing too long" -- the English idiom "nothing
    # too X" means "not very X"; "nothing" alone is too common a word to add
    # to the shared negator (it would false-match ordinary "nothing was
    # found"-style sentences), so only this fixed idiom is recognised.
    r"|\bnothing\s+too\s+(?:detailed|long|much)\b"
    # "less detail", "slightly less detail" -- asks for LESS than whatever was
    # just given, which is the same request as "briefly" even though no
    # negator word ("don't"/"not"/...) appears in it at all.
    r"|\bless\s+detail(?:ed|s)?\b"
    # "no long explanation needed" -- "explanation" (added to
    # `_DETAIL_REQUEST_PHRASES`) made `_has_detail_phrase` match this as a
    # POSITIVE request; the idiom needs its own fixed shape for the same
    # reason "nothing too X" does above ("no" alone is too common to add to
    # the shared negator).
    r"|\bno\s+long\s+(?:explanation|detail(?:ed|s)?)\b"
    # "keep the explanation minimal" / "keep it minimal" -- "minimal" is a
    # brevity word with no negator in front of it at all.
    r"|\bminimal\b",
    re.IGNORECASE,
)

# The negator-side vocabulary above only matches an EXACTLY-spelled detail
# word (`_DETAIL_REQUEST_RE`), so "dont explian in detial" -- a double typo,
# on both the negator-adjacent verb and "detail" itself -- slipped past every
# alternative and fell through to `_is_detail_request`'s own typo-tolerant
# fallback (`is_token_typo_match` against `_DETAIL_REQUEST_TRIGGER_WORDS`),
# which has no notion of negation at all and read it as a positive request.
_DETAIL_NEGATOR_WORDS = frozenset({
    "dont", "don", "not", "never", "no", "avoid", "avoiding", "skip",
    "skipping", "without", "less",
})


def _detail_negated_by_typo(message: str) -> bool:
    """Typo-tolerant fallback for `_is_detail_negation_request`: a negator
    token within a few words of a typo'd `_DETAIL_REQUEST_TRIGGER_WORDS` word
    ("explian", "detial", ...). Only runs when the exact-spelling regex above
    already found nothing, so a clean "don't explain in detail" is answered
    by the faster, simpler regex path as before."""
    tokens = re.findall(r"[a-zA-Z]+", message or "")
    for i, tok in enumerate(tokens):
        if tok.lower() not in _DETAIL_NEGATOR_WORDS:
            continue
        for w in tokens[i + 1:i + 6]:
            if any(is_token_typo_match(w.lower(), target) for target in _DETAIL_REQUEST_TRIGGER_WORDS):
                return True
    return False


# Tanglish postfix idiom, the mirror of `_FORMAT_NEGATED_POSTFIX_RE`:
# "detail venda" / "vivaramaa venda" ("don't want detail(s)").
_DETAIL_NEGATED_POSTFIX_RE = re.compile(
    r'\b(?:detail\w*|vivaram\w*|விரிவா\w*|விவரமா\w*)[\s-]*\w{0,3}[\s-]*'
    r'(?:venda\w*|vendam\w*|beda\w*|illa\w*|kudathu\w*)\b',
    re.IGNORECASE,
)


def _is_detail_negation_request(message: str) -> bool:
    """True for "don't explain in detail", "not in detail", "no need for a
    detailed explanation", "விரிவாக வேண்டாம்", "detail venda" -- a NEGATED
    detail instruction. Still a request to (re-)explain, just in the brief
    form, so callers check this BEFORE treating a matched detail phrase as a
    request for more detail."""
    msg = message or ""
    if _DETAIL_NEGATED_POSTFIX_RE.search(msg):
        return True
    for clause in _CLAUSE_SPLIT_RE.split(msg):
        if _DETAIL_NEGATED_RE.search(clause):
            return True
        if (any(neg in clause for neg in _FORMAT_NEGATOR_TA)
                and (_has_detail_phrase(clause) or _STAGE_QUESTION_RE.search(clause))):
            return True
        if _detail_negated_by_typo(clause):
            return True
    return False


def _is_explain_request(message: str) -> bool:
    """True when the officer is asking to (re-)explain the previous answer AT
    ALL -- "explain", "describe", "elaborate", "explain briefly" -- whether
    they want more detail or less. Used to trigger the explain follow-up
    routing (service-code re-explain, listing/application "explain"); see
    `_is_detail_request` below for "wants MORE detail specifically", which
    toggles the brief/full answer.
    """
    tokens = re.findall(r"[a-zA-Z]+", message or "")
    if re.search(r"\b(?:don't|dont|do not)\s+explain\b", message or "", re.IGNORECASE) and re.search(r"\b(?:numbers?\s+only|data\s+only|give\s+(?:the\s+)?numbers?)\b", message or "", re.IGNORECASE):
        return False
    # A NEGATED format instruction ("dont explain in sentence", "not in
    # sentence format") is still a request to re-explain -- just not in the
    # brief form it names. Checked first so a message with no "explain"/
    # "detail" word of its own ("don't use sentence format") is still
    # recognised as a re-explain follow-up.
    if _is_format_negation_request(message):
        return True
    # A NEGATED detail instruction ("don't explain in detail", "detail
    # venda") is likewise still a request to re-explain -- just in the brief
    # form it names, not a repeat of the full one. Covers a Tamil/Tanglish
    # negated phrase that `_has_detail_phrase` below would not otherwise
    # recognise at all (no English trigger word, no exact Tamil phrase match).
    if _is_detail_negation_request(message):
        return True
    if _has_detail_phrase(message):
        return True
    if _STAGE_QUESTION_RE.search(message or ""):
        return True
    # A typo on the trigger word itself ("ezplain", "eplain") -- every other
    # rule in this codebase tolerates a slip within edit distance, but this
    # one was a plain substring match, so a mistyped "explain" fell all the
    # way through to "I'm not sure what you mean" instead of re-explaining
    # the previous answer.
    if any(is_token_typo_match(tok, target) for tok in tokens for target in _DETAIL_REQUEST_TRIGGER_WORDS):
        return True
    if any(w in message for w in _BRIEF_REQUEST_WORDS_TA):
        return True
    if _BRIEF_REQUEST_PHRASES_RE.search(message or "") or _WORD_LIMIT_RE.search(message or ""):
        return True
    return any(is_token_typo_match(tok, target) for tok in tokens for target in _BRIEF_REQUEST_WORDS)


_CI_VERB =(r"(?:clear\w*|wip(?:e|es|ed|ing)|eras\w*|delet\w*|remov\w*|expung\w*|purg\w*|discard\w*|reset\w*|forget\w*|"
            r"restart\w*|start\s+(?:a\s+)?new|new\s+(?:chat|conversation)|azhi\w*|neekk?u\w*|அழி\w*|நீக்க\w*|நீக்கி\w*|துடை\w*|அகற்ற\w*|மீட்டமை\w*)")
_CI_NOUN = (r"(?:chat|chats|conversation|conversations|convo|history|messages?|thread|transcript|சாட்\w*|உரையாடல\w*|அரட்டை\w*|செய்தி\w*)")
_CI_ASK_EN = re.compile(
    r"\bwhat\s+(?:happens|will\s+happen|would\s+happen|occurs|does|is|if)\b|\bwhat\s+if\b|\b(?:will|does|do|did)\s+(?:it|i|my|the|you|this|that|clear\w*|wip\w*|delet\w*)\b|\bwould\s+(?:it|i|my|the)\b"
    r"|\bcan\s+(?:i|we)\b|\bis\s+(?:it|the|my|there)\b|\bare\s+(?:my|the)\b|\bhow\s+(?:do|can|to|should)\b|\bexplain\b|\bmeaning\b"
    r"|\bif\s+i\b|\bwhen\s+i\b|\bafter\s+(?:i|clearing|wiping|deleting|removing)\b|\bwhy\b|\bwhat\s+will\b", re.IGNORECASE)
_CI_ASK_TA = re.compile(
    r"என்ன\s*(?:ஆகும்|நடக்கும்|நடந்தால்|ஆகுமா)|ஆகுமா|கிடைக்குமா|அழியுமா|நீங்குமா|பாதுகாப்ப|எப்படி|மீண்டும்|என்னவாகும்|என்ன\s+ஆகும்|போய்விடுமா|போயிடுமா|போகுமா|இருக்குமா")
_CI_ASK_TG = re.compile(
    r"\benna\s*(?:aagum|agum|nadakkum|aachu|aagudhu|aagum)\b|\baagumaa\b|\bpoidum\w*\b|\bpoyidum\w*\b|\bkedaikk\w*\b|\bkidaikk\w*\b"
    r"|\bazhiyum\w*\b|\bthirumba\b|\bthirumbi\b|\beppadi\b|\bepdi\b|\bsafe\b|\bvaruma\w*\b|\bnadakkum\b|\bmaari\w*\b", re.IGNORECASE)


def _clear_info_topic(message: str):
    """"what happens if I clear the conversation?" / "will my applications be deleted if I
    wipe the chat?" / "can I get it back?" -- a QUESTION about clearing. Returns the topic
    ("effect", "data", "recover", "remember", "how") or None. Never carried out."""
    text = (message or "").strip().lower()
    if not text or len(text.split()) < 3 and not re.search(r"[\u0B80-\u0BFF]", text):
        return None
    if re.search(r"\d{4}/\d{3,4}/", text):
        return None
    has_verb = re.search(_CI_VERB, text)
    has_noun = re.search(_CI_NOUN, text)
    ta = re.search(r"[\u0B80-\u0BFF]", text)
    asks = bool(_CI_ASK_EN.search(text)) or bool(_CI_ASK_TG.search(text)) or (bool(ta) and bool(_CI_ASK_TA.search(text)))
    if not (has_verb and asks):
        return None
    chatish_verb = re.search(r"\b(?:clear\w*|wip\w*|reset\w*|restart\w*)\b|azhi|அழி|துடை", text)
    if not (has_noun or (chatish_verb and (_CI_ASK_TG.search(text) or _CI_ASK_TA.search(text)))):
        return None
    if re.search(r"\bapplications?\b|விண்ணப்ப", text) and not re.search(r"\b(?:will|does|do|what\s+happens\s+to|azhiyum\w*|poidum\w*|enna\s+aagum|aagumaa)\b|அழியுமா|நீங்குமா|போய்விடுமா|போயிடுமா|இருக்குமா|என்ன\s+ஆகும்", text):
        return None
    if re.search(r"get\s+(?:it\s+)?back|recover|undo|restor|retriev|come\s+back|thirumb|kedaikk|kidaikk|மீண்டும்|திரும்ப", text):
        return "recover"
    if re.search(r"remember|forget\s+everything|நினைவ|ninaivu", text):
        return "remember"
    if re.search(r"\bapplications?\b|\brecords?\b|\bdata\b|register|database|\bfiles?\b|\blose\b|deleted|\bsafe\b|விண்ணப்ப|பதிவ|தரவு|azhiyum|poidum|poyidum|பாதுகாப்ப", text):
        return "data"
    if re.search(r"\bhow\b|எப்படி|eppadi|epdi", text):
        return "how"
    return "effect"


def _looks_like_unclear_clear_attempt(text: str) -> bool:
    """A short message that OPENS with a wipe verb but doesn't qualify as a
    real clear command ("clear sh") -- the trailing word names no transcript
    noun `_is_clear_command` recognises. Guessing it means "clear chat"
    anyway would be exactly the kind of silent guess this project's typo/
    ambiguity fixes exist to avoid elsewhere (a wrong guess here destroys the
    transcript); routing it to `general_query` instead answered "No
    documents found matching the query 'clear sh'" -- a confusing non-answer
    to what was plainly an attempt at a command, even though it names no
    fabricated fact.

    Exactly verb + one trailing word, on purpose: "clear the database",
    "erase all records", "delete application <no>" are each 3 tokens too,
    and each already has a correct, specific answer from
    `_is_mutation_request` (a real-records refusal, not a "did you mean
    clear chat?" nudge) -- calling that function from here would recurse,
    since it calls back into `_contentless_message`, so the token count is
    what keeps this from shadowing it instead.
    """
    tokens = text.split()
    return len(tokens) == 2 and tokens[0] in _CLEAR_VERBS


def _contentless_message(message: str) -> Optional[str]:
    """Classify a message that asks nothing.

    'clear'            -- a command to wipe the transcript; carried out.
    'clear_unclear'    -- opens with a wipe verb but doesn't fully qualify;
                          asked rather than guessed or left to the LLM.
    'session_command'  -- needs a control outside the conversation (logout).
    'ack'              -- an acknowledgement, or punctuation only.
    None               -- might carry a question; let the pipeline handle it.
    """
    if not message:
        return "ack"
    if not any(ch.isalpha() or ch.isdigit() for ch in (message or "")):
        if "?" in message:
            return "qmark_clarify"
        return "punct_clarify"
    text = message.strip().strip(_TRIM_CHARS).strip().lower()
    if not text:
        if "?" in message:
            return "qmark_clarify"
        return "punct_clarify"
    if _clear_info_topic(text):
        return None  # "what happens if I clear the conversation?" is a question, answered elsewhere
    if text in _CLEAR_COMMAND_WORDS or _is_clear_command(text):
        return "clear"
    if text in _SESSION_COMMAND_WORDS:
        return "session_command"
    if text in _DISMISS_WORDS:
        return "dismiss"
    # "correct illa" / "seri illa" -- a trailing "illa"/"illai" NEGATES the
    # word before it ("not correct"), the same way English "not" does. Tested
    # token-by-token, "correct" and "illa" are each individually an ack word
    # (the bare "yes" sense of "correct", the bare "no" sense of "illa"), so
    # `_is_ack_message` read the pair as "all ack" and answered with the
    # generic contentless reply to what is actually a rejection, the opposite
    # polarity of "correct" alone. Left unclassified here (None), it falls
    # through to the ordinary pipeline, where it reads as the negated
    # correction it is.
    if re.match(r"^(?:yes|yeah|yep|yup|ya|correct|right|seri|sari|aama|ama|ok|okay)\s+(?:illa|illai)\b", text):
        return None
    if _is_ack_word(text) or _is_ack_message(text):
        return "ack"
    if _looks_like_unclear_clear_attempt(text):
        return "clear_unclear"
    return None


# ── Negative listing prohibition ("dont show applications", etc.) ─────────────
_PROHIBIT_LISTING_EN_RE = re.compile(
    r'(?<!\w)(?:(?:actually|wait|please|pls)\s*,?\s*)?'
    r'(?:'
    r'(?:don\'?t|dont|do\s+not|never|stop|please\s+(?:don\'?t|dont|do\s+not))\s+'
    r'(?:show|showing|list|listing|display|displaying|fetch|fetching|view|viewing|open|give\s+me)\s+'
    r'(?:any\s+|the\s+)?(?:applications?|apps?|records?|files?|cases?|list\s+of\s+applications?|application\s+list)(?:\s+list)?'
    r'|no\s+(?:applications?|apps?|records?|files?)\s+(?:list|table|details)'
    r'|(?:applications?|apps?|records?|files?)\s*(?:[-:,]|\s+)\s*(?:don\'?t|dont|do\s+not|never|stop)\s+(?:show|showing|list|listing|display|displaying|fetch|view)'
    r')(?!\w)',
    re.IGNORECASE
)

_PROHIBIT_LISTING_TA_RE = re.compile(
    r'(?:'
    r'(?:விண்ணப்ப(?:ங்கள்|ங்களை|ப்\s*பட்டியல்|ப்\s*பட்டியலை|\s+பட்டியல்)?|கோப்பு(?:கள்|களை)?|பட்டியல்)'
    r'[\s\w஀-௿]*?'
    r'(?:காட்ட\s*வேண்டாம்|காட்டாதீர்கள்|காட்டாதே|காண்பிக்க\s*வேண்டாம்|காண்பிக்காதீர்கள்|பட்டியலிட\s*வேண்டாம்|பட்டியலிடாதீர்கள்|வேண்டாம்)'
    r'|(?:காட்ட\s*வேண்டாம்|காட்டாதீர்கள்|காட்டாதே|காண்பிக்க\s*வேண்டாம்|காண்பிக்காதீர்கள்|பட்டியலிட\s*வேண்டாம்|பட்டியலிடாதீர்கள்|வேண்டாம்)'
    r'[\s\w஀-௿]*?'
    r'(?:விண்ணப்ப(?:ங்கள்|ங்களை|ப்\s*பட்டியல்|ப்\s*பட்டியலை|\s+பட்டியல்)?|கோப்பு(?:கள்|களை)?|பட்டியல்)'
    r')',
    re.IGNORECASE
)

_PROHIBIT_LISTING_TL_RE = re.compile(
    r'(?:'
    r'\b(?:applications?|apps?|files?)\b'
    r'[\s\w]*?'
    r'(?:(?:kaatta|kaata|katta|kaami|kami|show|display|list)(?:\s+panna)?\s*(?:vendam|venda|vendaam|pannadhe|pannathe|pannatha|koodadhu|koodathu|maatten|maatren)|kaattadhe|kaatadhe|kattadhe|kaattathe|kaatathe|show\s*pannadhe|show\s*pannathe)'
    r'|(?:(?:kaatta|kaata|katta|kaami|kami|show|display|list)(?:\s+panna)?\s*(?:vendam|venda|vendaam|pannadhe|pannathe|pannatha|koodadhu|koodathu|maatten|maatren)|kaattadhe|kaatadhe|kattadhe|kaattathe|kaatathe|show\s*pannadhe|show\s*pannathe)'
    r'[\s\w]*?'
    r'\b(?:applications?|apps?|files?)\b'
    r')',
    re.IGNORECASE
)


def is_prohibit_listing(message: str) -> bool:
    """Returns True if the message is an explicit instruction prohibiting
    the display or listing of applications (Case A).

    Must distinguish:
    - Case A (Prohibit listing): "dont show applications", "no application list", "விண்ணப்பங்களைக் காட்ட வேண்டாம்", etc. -> True
    - Case B (Count only): "Don't show applications, only tell me the count." -> False (wants count data)
    - Case C (Positive list): "Show pending applications." -> False
    - Case D (Filter negation): "Show applications that are not rejected.", "Don't show rejected applications; show pending ones." -> False
    """
    if not message:
        return False
    msg = message.strip()

    # Check Case B: Count requests must not be treated as a pure prohibition.
    from .chat_response_formatting import _is_count_only_query
    if _is_count_only_query(msg):
        return False

    # Check Case D: If there is an explicit positive listing clause for a category,
    # it is a filter / selective listing request, not a total prohibition.
    if re.search(r'\b(?:show|list|display)\s+(?:pending|approved|isd|nisd|csc|citizen|active|overdue)\b', msg, re.IGNORECASE):
        return False

    return bool(
        _PROHIBIT_LISTING_EN_RE.search(msg)
        or _PROHIBIT_LISTING_TA_RE.search(msg)
        or _PROHIBIT_LISTING_TL_RE.search(msg)
    )


def _prohibit_listing_reply(language: str) -> str:
    """Short acknowledgment acknowledging the instruction not to list applications."""
    if language in ("ta",):
        return "புரிந்தது. விண்ணப்பப் பட்டியல் காட்டப்படாது."
    elif language in ("tanglish",):
        return "Purinjadhu. Application list kaatta maatten."
    return "Understood. I won't display the application list."



# ── No evidence, no factual LLM answer ─────────────────────────────────────
# Everything the deterministic layer did not claim lands in `general_query`, and
# from there in the agent / plain-prompt fallback. That is right for a real SIS
# question phrased in a way no handler matched, and wrong for a message that
# carries nothing to look up ("asdfgh", "blah blah", "whatxyz"): with no tool
# to call and no context to read, the model improvises -- "You cannot see that
# record.", "I'm not sure what you're asking", a 45-second timeout.
#
# Invariant: an unrouted message with no domain evidence gets a clarification,
# never a factual answer. Evidence is any of: SIS vocabulary (exact, or one
# typo away from a core word), an application / survey / service-code shape, or
# a bare number to look up. Follow-ups never reach here without evidence: the
# follow-up layer has already appended the application number they refer to.
_EVIDENCE_EXTRA_TERMS = (
    "isd", "nisd", "csc", "sro", "sla", "ip address", "fee", "charge", "document",
    "status", "stage", "desk", "queue", "workload", "priority", "visit", "inspection",
    "transfer", "sale deed", "encumbrance", "boundary", "measurement", "citizen",
    "applicant", "channel", "submitted", "scheduled", "complaint", "rule", "process",
    "procedure", "service", "duty", "role", "surveyor", "draughtsman", "dsc", "remark", "comment", "reason",
    "விண்ணப்பம்", "சான்று", "கட்டணம்", "நிலை", "ஆவண", "பத்திர", "அளவை", "எண்",
    "முடிப்பு", "கையொப்ப", "அட்டவணை", "புதுப்பிக்க", "சரிபார்", "செலுத்த", "கருவூல", "தொழில்",
    "முரண்பாடு", "மாற்ற கொடி",
)
_EVIDENCE_CORE_WORDS = (
    "application", "survey", "patta", "status", "pending", "approved", "rejected",
    "workflow", "visit", "ward", "block", "document", "service", "field", "overdue",
    "applicant", "workload", "subdivision", "escalated",
)
_EVIDENCE_SHAPES_RE = re.compile(
    r"\b\d{4}/\d{1,4}/\d{1,3}/\d+\b"          # application number
    r"|\b01[5-9]\d\b"                           # service code
    r"|\b\d{1,4}\s*/\s*\d{1,3}[a-z]?\b"        # survey / sub-division reference
    r"|\b\d{3,}\b",                             # any number worth looking up
    re.IGNORECASE)


# Words that appear in any text, so appearing in the corpus proves nothing.
_EVIDENCE_STOP = frozenset("""
    the and for from with are was were been being its this that these those what which who whom whose
    when where why how does did done can could should would will shall may might must not yes then than
    but about into over under after before between during out again more most some any all each every
    other such only own same too very just also you your our their them him her his there here have has
    had having get got give given going make made say said tell told let know need want mean means like
    please thanks thank hello hey okay one two three first second third new old good bad much many few
    little big small long short thing things way ways use used using time day days week month year years
    work works number numbers word words random blah whatever anything something nothing story sing song
    joke doing today tomorrow yesterday now soon later well fine great nice cool sure right wrong true
    false best worst better worse able about above below across along among around because both either
    neither else ever never always often maybe perhaps really quite rather still yet already
    show list see find display check view look showing listing checking looking viewing finding
""".split())
_EVIDENCE_ACRONYMS = frozenset({"sd", "dis", "zdt", "dro", "ec", "sis", "vao", "sro", "csc",
                                "nisd", "isd", "can", "igrs", "dsc", "tn", "id"})


@functools.lru_cache(maxsize=1)
def _corpus_vocab():
    """(English words, Tamil 4-character stems) found in the reference corpus."""
    en, ta = set(), set()
    for f in (Path(__file__).resolve().parents[1] / "documents").glob("*.txt"):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        en.update(w for w in re.findall(r"[a-z]{3,}", text) if w not in _EVIDENCE_STOP)
        ta.update(w[:4] for w in re.findall(r"[஀-௿]{4,}", text))
    return en, ta


# A reference or question word: with a live context these make a bare fragment a
# follow-up ("how long will this type take?", "இதற்கு எவ்வளவு நாள் ஆகும்?").
_CONTEXT_CUE_RE = re.compile(
    r"\b(this|that|it|its|they|them|these|those|here|same|how|what|when|why|who|which|"
    r"continue|resume|proceed|next|more|rest|remaining|further|go\s+on|thodaru|thodarunga|meethi|adutha|innum|"
    r"evlo|evvalavu|enna|eppo|eppadi|yaar|yaaru|edhu|idhu|adhu|idhuku|adhuku)\b"
    r"|[஀-௿]*(இது|அது|இதற்கு|அதற்கு|இதன்|அதன்|எவ்வளவு|எத்தனை|என்ன|எப்போது|ஏன்|யார்|எந்த|தொடர்|தொடர|மீதி|மீத|அடுத்த|அடுத்து|மேலும்)",
    re.IGNORECASE)


def _domain_evidence_tier(message: str, ctx=None):
    """Which kind of evidence justifies an LLM answer to `message`, or None.

    Ordered strongest first, so the corpus is only ever supporting evidence:
      entity      an application / survey / service-code / number shape
      vocabulary  SIS terms, acronyms, or a core word one typo away
      context     a live follow-up context + a reference or question cue
      corpus      a word (or Tamil stem) that appears in the reference documents
    """
    m = (message or "").lower()
    if not m.strip():
        return None
    if _EVIDENCE_SHAPES_RE.search(m) or _ANY_APP_NUMBER_RE.search(m):
        return "entity"
    if any(t in m for t in _DOMAIN_TERMS) or any(t in m for t in _EVIDENCE_EXTRA_TERMS):
        return "vocabulary"
    toks = re.findall(r"[a-z]{2,}", m)
    if any(t in _EVIDENCE_ACRONYMS for t in toks):
        return "vocabulary"
    for tok in toks:
        if len(tok) >= 4 and any(is_token_typo_match(tok, w) for w in _EVIDENCE_CORE_WORDS):
            return "vocabulary"
    if ctx is not None and (getattr(ctx, "application_numbers", None) or getattr(ctx, "entity", None)) \
            and _CONTEXT_CUE_RE.search(m):
        return "context"
    en, ta = _corpus_vocab()
    if any(t not in _EVIDENCE_STOP and t in en for t in toks):
        return "corpus"
    if any(t[:4] in ta for t in re.findall(r"[஀-௿]{4,}", m)):
        return "corpus"
    return None


def _has_domain_evidence(message: str, ctx=None) -> bool:
    return _domain_evidence_tier(message, ctx) is not None


_FV_STATUS_FRAGMENT_RE = re.compile(
    r"(?:show\s+|list\s+|give\s+)?(?:only\s+|just\s+)?(?:the\s+)?"
    r"(?:completed|complete|done|finished|pending|unscheduled|scheduled|overdue|to\s+visit|to\s+be\s+visited)"
    r"(?:\s+ones|\s+visits?|\s+field\s+visits?)?")



# ── Negation: "not ISD", "except completed", "everything but NISD" ──────────
# parse_intent picks a list by the words it sees, so "applications not ISD" was
# answered with the ISD list and "dont show NISD" with the NISD one -- the
# opposite of the question. A negated type becomes the complement ("not ISD" is
# the NISD applications); over visits the exclusion is applied to the rows.
_NEG_LEAD = (r"(?:not|except|excluding|exclude|without|other\s+than|apart\s+from|besides|minus|but\s+not|"
             r"everything\s+but|all\s+but|anything\s+but|(?:don'?t|dont|do\s+not|never)\s+"
             r"(?:show|list|display|include|give)(?:\s+me)?|hide|skip|ignore|drop|remove|leave\s+out)")
_NEG_TERM_RE = re.compile(
    rf"\b{_NEG_LEAD}\s+(?:the\s+|any\s+|all\s+|of\s+)*(nisd|isd|completed|complete|done|finished|"
    rf"unscheduled|rescheduled|scheduled|overdue)\b(?:\s+(?:ones?|applications?|apps?|visits?|field\s+visits?|cases|files))?"
    rf"(?:\s+(?:please|pls))?", re.IGNORECASE)
_VISIT_KEY = {"completed": "completed", "complete": "completed", "done": "completed", "finished": "completed",
              "unscheduled": "unscheduled", "rescheduled": "rescheduled", "scheduled": "scheduled", "overdue": "overdue"}


def _parse_negation(text: str):
    """(types excluded, visit statuses excluded, the text with those phrases removed)."""
    types, visit = set(), set()
    for m in _NEG_TERM_RE.finditer(text or ""):
        w = m.group(1).lower()
        if w in ("nisd", "isd"):
            types.add(w.upper())
        else:
            visit.add(_VISIT_KEY[w])
    return types, visit, re.sub(r"\s+", " ", _NEG_TERM_RE.sub(" ", text or "")).strip()



# ── Columns of a field-visit table accumulate ───────────────────────────────
# "along with district" then "add ward" then "no type": the table keeps every column asked for so far.
_COLW = (r"\b(?:ward|block|district|taluk|town|status|stage|type|survey(?:\s+number)?|fee|name|applicant(?:\s+name)?|"
         r"owner|mobile|phone|address|date|channel|can|patta|sub[\s-]?divisions?)\b")
_COL_LIST = rf"{_COLW}(?:\s*(?:,|and|&)\s*(?:the\s+)?{_COLW})*"
_COL_ADD_RE = re.compile(rf"(?:also\s+)?(?:along\s+with|along|with|add|include|show)\s+(?:the\s+)?({_COL_LIST})"
                         rf"(?:\s+(?:also|too|columns?))?", re.IGNORECASE)
_COL_DROP_RE = re.compile(rf"(?:no|not|without|hide|exclude|remove|drop|skip)\s+(?:the\s+)?(?:along\s+)?({_COL_LIST})"
                          rf"(?:\s+columns?)?", re.IGNORECASE)
# Any of these present -> it is (or may be) a real SIS question; do not block.
_DOMAIN_TERMS = (
    "application", "survey", "patta", "owner", "field visit", "fieldvisit",
    "ward", "block", "taluk", "district", "town", "nisd", " isd",
    "subdivision", "sub-division", "sub division", "encroach", "litigation",
    "workflow", "sis ", "tahsildar", "draughtsman", "jurisdiction", "officer",
    "can number", "aadhaar", "aadhar", "igrs", "deed", "mutation", "parcel",
    "chitta", "natham", "sub-registrar", "sub registrar", "escalat", "pending",
    "approved", "rejected", "overdue", "sketch", "fmb", "deadline", "form 6",
    "form 7", "form 8", "extent", "land type", "land use", "soil", "tax",
    "service code", "0153", "0154", "registrar", "portal",
    "working day", "service level", "turnaround",
    "விண்ணப்ப", "கணக்கெண்", "சர்வே", "பட்டா", "உரிமையாளர்", "வார்டு", "வழக்கு",
    "கள ஆய்வு", "நிலம்", "பணி", "தாசில்தார்",
)
_BARE_REF_RE = re.compile(
    r"^\s*(?:please\s+)?(?:(?:show|give|tell|open|display|get)\s+(?:me\s+)?)?"
    r"(?:it|that|this|that\s+one|this\s+one|the\s+same|the\s+same\s+one|the\s+previous\s+one|"
    r"previous\s+one|the\s+last\s+one|same\s+one|those|them|the\s+above)"
    r"(?:\s+(?:again|please))?\s*[.!?]*\s*$", re.IGNORECASE)


def _year_match(message: str):
    """A year the message names: "in 1900", "year 2023", or a bare 20xx. A bare 19xx
    is not read as a year -- it is as likely a survey number."""
    return (re.search(r"\b(?:year|in|of|from|during|for|since)\s+(19\d{2}|20\d{2})\b", message, re.IGNORECASE)
            or re.search(r"\b(20\d{2})\b", message))


# "what do u think abt me", "do you like me", "rate me", "nee ennai pathi enna nenaikira" -- an
# opinion of a person. The assistant holds none, and says so instead of "I'm not sure what you mean".
_OPINION_RE = re.compile(
    r"\bwh?at\s+(?:do|does|did|would|will)\s+(?:you|u|i)\s+(?:really\s+)?(?:think|feel|thinks?)\s+(?:abt|about|of|on)\s+(?:me|myself|u|you|us)\b"
    r"|\bwh?at(?:'s|\s+is|s)?\s+(?:your|ur)\s+(?:opinion|view|thought|feeling)s?\s+(?:abt|about|of|on)\s+(?:me|myself|us)\b"
    r"|\b(?:do|did)\s+(?:you|u)\s+(?:like|love|hate|know|remember|trust|respect)\s+(?:me|us)\b"
    r"|\b(?:rate|judge|review|assess)\s+(?:me|my\s+(?:work|performance|personality))\b"
    r"|\bam\s+i\s+(?:a\s+)?(?:good|bad|smart|stupid|dumb|best|worst|nice)\b"
    r"|\b(?:nee|nenga|ni)\s+(?:ennai|enna|ennoda|yenna)\w*\s+(?:pathi|patri|pathi)\s+(?:enna|yenna)\s+(?:ninaikira|nenaikira|ninaikireenga|nenaikireenga|nenaikura)"
    r"|என்னைப்?\s*(?:பற்றி|பத்தி)\s*(?:நீ|நீங்கள்|நீங்க)?\s*என்ன\s*(?:நினைக்கிற|நினைக்கிறீர்|நினைக்கிறாய்|நினைக்கிறீங்க)"
    r"|என்னை\s*(?:உனக்கு|உங்களுக்கு)\s*(?:பிடிக்குமா|தெரியுமா)",
    re.IGNORECASE)


def _resolve_month_year(month: int, today: date = None) -> int:
    """
    Year for a month named without one ("applications in June").

    A monthly report means one month of one year, so resolve to the most recent
    occurrence of that month that has already started — June asked in August
    2026 is June 2026; September asked in August 2026 is September 2025.
    Matching the month across every year mixes four years of files into a
    single "monthly" list, which is never what the question meant.
    """
    today = today or date.today()
    return today.year if month <= today.month else today.year - 1


def _period_from_message(message: str):
    """
    (start, end, label) for whatever period the message names, or (None, None, None).

    Covers the four shapes officers use: a union of months ("June and July"),
    an explicit range or relative phrase, a single named month ("in June 2026")
    and a bare year ("in 2024"). Reporting handlers need all four -- reading only
    the first two is how "my completion rate in June 2026" ended up answering
    with the all-time figure.
    """
    scopes = extract_month_scopes(message)
    if scopes:
        return scopes[0][0], scopes[-1][1], format_month_scopes(scopes)

    start_d, end_d = extract_date_range(message)
    if start_d and end_d:
        return start_d, end_d, (_whole_month_label(start_d, end_d) or f"{start_d} to {end_d}")

    month = extract_month_from_query(message)
    year_match = _year_match(message)
    year = int(year_match.group(1)) if year_match else None
    if month:
        year = year or _resolve_month_year(month)
        start_d = date(year, month, 1)
        end_d = date(year, month, calendar.monthrange(year, month)[1])
        return start_d, end_d, f"{_MONTH_NAMES[month]} {year}"
    if year:
        return date(year, 1, 1), date(year, 12, 31), str(year)
    # "this year" / "current year" names a period too, but carries no digits, so
    # every branch above misses it and the handler answered all-time instead.
    if re.search(r"\bthis\s+year\b|\bcurrent\s+year\b", message, re.IGNORECASE) or \
            "இந்த ஆண்டு" in message or "இந்த வருடம்" in message:
        this_year = date.today().year
        return date(this_year, 1, 1), date(this_year, 12, 31), str(this_year)
    return None, None, None


# How a status reads inside a query_type label ("Approved & Pending
# Applications"), for the multi-status case.
_STATUS_LABELS = {
    "approved": "Approved", "rejected": "Rejected", "pending": "Pending",
    "in_progress": "In Progress", "escalated": "Escalated",
}


def _explicit_status_request(message_lower: str):
    """
    The status the officer actually named, or None when they named none.

    "How many rejected applications in 2024" used to answer with the
    non-rejected ones: any date or year scope forced status_filter=None, and the
    word "rejected" was never looked at. A status the officer typed outranks the
    scope default.
    """
    if any(p in message_lower for p in ["history", "approved n rejected",
                                        "approved and rejected", "approved & rejected"]):
        return ["approved", "rejected"]
    # A negated status ("not pending", "except rejected", "other than
    # approved") is not the status it names -- it is every OTHER status.
    # This used to be checked for "rejected"/"approved" only (returning
    # None, i.e. "no status named"), so "not pending applications" matched
    # neither of those two negation phrases, fell through to the plain
    # `\bpending\b` check below (a bare substring test that cannot tell
    # "pending" from "not pending"), AND separately, even the two phrases
    # it did catch fell back to this function's caller's own default --
    # which is ALSO "pending" for an unscoped listing. Either way the
    # officer was shown PENDING applications for a request that named
    # every status except that one: the literal opposite of the question,
    # with nothing to say it had been misread. "Not pending" unambiguously
    # names the other four statuses, so returned directly rather than left
    # for a default to coincidentally get wrong the same way.
    _ALL_STATUSES = ("pending", "in_progress", "escalated", "approved", "rejected")
    # Typo-tolerant on the STATUS word ("not rejcted", "except aproved") the
    # same way the positive match below is -- an officer who typos a status
    # word negated it exactly as often as they typo one they are asking FOR,
    # and an unmatched typo here fell through to the plain substring check
    # below, which then read "not rejcted" as naming no status at all (it
    # only ever tests exact spellings), so the caller's own "pending" default
    # applied -- again the wrong answer, just reached a different way.
    # "not"/"except"/"excluding" stay exact: they are short, common function
    # words, and this codebase's own rule is that a word that short gets no
    # typo budget at all (the same reason "isd" cannot absorb "nisd").
    def _status_word_to_canonical(_word: str) -> Optional[str]:
        _word1 = _word.split()[0] if " " in _word else _word
        if re.match(r"in[ _-]?progres{1,2}\b", _word):
            return "in_progress"
        if is_token_typo_match(_word1, "rejected") or is_token_typo_match(_word1, "reject"):
            return "rejected"
        if (is_token_typo_match(_word1, "approved") or is_token_typo_match(_word1, "approve")
                or is_token_typo_match(_word1, "completed")):
            return "approved"
        if is_token_typo_match(_word1, "escalated"):
            return "escalated"
        if is_token_typo_match(_word1, "pending"):
            return "pending"
        return None

    # Every negated status, not just the first. "not approved and not
    # rejected" (or "neither approved nor rejected") names TWO exclusions;
    # `re.search` only ever finds the first, so "not rejected" was silently
    # dropped and the officer was shown rejected applications alongside the
    # approved ones they also explicitly excluded. "neither X nor Y" is its
    # own construction -- neither word follows "not"/"except", so it needs
    # its own pattern, and both sides of it are excluded, not just X.
    # The second word of the capture group is only ever "progress" (for "in
    # progress"); without excluding the negation markers themselves from it,
    # "not approved not rejected" greedily captured "approved not" as one
    # unit -- swallowing the second "not" into the first match -- so
    # `finditer` never saw a second "not rejected" to match at all, and the
    # word it had actually named twice was excluded only once.
    _excluded: List[str] = []
    for _m in re.finditer(
        r"\b(?:not|except|excluding|other\s+than)\s+(?:the\s+)?"
        r"(\w+(?:[ _-](?!not\b|except\b|excluding\b|other\b|neither\b|nor\b)\w+)?)",
        message_lower
    ):
        _c = _status_word_to_canonical(_m.group(1))
        if _c:
            _excluded.append(_c)
    # "skip anything that is still pending", "ignore rejected", "hide the approved
    # ones" negate a status the same way "not"/"except" do; without them the
    # named status was read as the one being asked FOR -- the opposite request.
    for _m in re.finditer(
        r"\b(?:skip|skipping|ignore|ignoring|hide|hiding|leave\s+out|"
        r"leaving\s+out)\s+"
        r"(?:(?:the|anything|everything|all|any|those|that|which|is|are|still|currently)\s+)*"
        r"(\w+(?:[ _-]\w+)?)",
        message_lower
    ):
        _c = _status_word_to_canonical(_m.group(1))
        if _c:
            _excluded.append(_c)
    _neither = re.search(
        r"\bneither\s+(?:the\s+)?(\w+(?:[ _-]\w+)?)\s+nor\s+(?:the\s+)?(\w+(?:[ _-]\w+)?)\b",
        message_lower)
    if _neither:
        for _g in _neither.groups():
            _c = _status_word_to_canonical(_g)
            if _c:
                _excluded.append(_c)
    if _excluded:
        return [s for s in _ALL_STATUSES if s not in _excluded]
    # EVERY status the officer named, not just the first one matched. "ISD
    # pending and completed applications" names two; returning on the first
    # match dropped "completed" silently and answered 1 to an officer holding
    # 3 (1 pending + 2 approved). A single named status still comes back as
    # the scalar it always was, so every existing caller is unchanged.
    #
    # "completed" is the officer's word for approved (it is also the closing
    # workflow stage). Bare "complete" is deliberately NOT matched -- it is
    # how they ask for "complete details" / "the complete list", which names
    # no status at all.
    # Typo tolerance, not just the exact/prefix regex above: "aproved"
    # (missing a 'p'), "rejcted" (missing an 'e') matched none of these and
    # silently fell through to the "pending" default a few lines up --
    # "show aproved applications" answered with the officer's 2-item desk
    # queue instead of their 64 approved files, no different from asking for
    # nothing at all. Checked per token against the same words the regex
    # above names, with the project's standard edit-distance budget.
    _tokens = extract_tokens(message_lower)
    def _names(*targets: str) -> bool:
        return any(is_token_typo_match(tok, t) for tok in _tokens for t in targets)

    named = []
    if (re.search(r"\brejec", message_lower) or "நிராகரிக்கப்பட்ட" in message_lower
            or _names("reject", "rejected")):
        named.append("rejected")
    if (re.search(r"\bapprove", message_lower) or re.search(r"\bcompleted\b", message_lower)
            or "அங்கீகரிக்கப்பட்ட" in message_lower
            or _names("approve", "approved", "completed")):
        named.append("approved")
    # progres{1,2} tolerates a dropped trailing 's' ("inprogres") without
    # token-level fuzzy matching, which would need "process" excluded --
    # a real, common word in this domain ("approval process") that sits
    # within one edit of "progress".
    if re.search(r"\bin[ _-]?progres{1,2}\b", message_lower):
        named.append("in_progress")
    if (re.search(r"\bescalat", message_lower) or _names("escalated")
            or "எஸ்கலேஷன்" in message_lower):
        named.append("escalated")
    if (re.search(r"\bpending\b", message_lower) or "நிலுவை" in message_lower
            or _names("pending")):
        named.append("pending")
    if not named:
        return None
    return named[0] if len(named) == 1 else named


def _whole_month_label(start_d, end_d) -> str:
    """
    'June 2026' / 'April-June 2026' when [start_d, end_d] covers whole calendar
    months end to end, else '' -- a range that starts or stops mid-month has to
    keep its exact dates.
    """
    if not (start_d and end_d):
        return ""
    if start_d.day != 1 or end_d.day != calendar.monthrange(end_d.year, end_d.month)[1]:
        return ""
    if start_d > end_d:
        return ""
    return format_month_scopes([(start_d, end_d)])


def extract_month_from_query(message: str) -> Optional[int]:
    """
    Extract month from message for filtering applications by submission month.
    Handles English and Tamil month names with token-boundary fuzzy matching for spelling mistakes.

    Returns:
        Month number (1-12) if found, None otherwise
    """
    month_num = extract_month_from_text(message)
    if month_num:
        logger.info(f"Month extracted: {month_num} from query '{message[:40]}'")
    return month_num


# Listings a "first N" / "last N" cap may be applied to. Every one of these
# renders a table of rows; an intent that answers in one sentence or about one
# record has no list to cap, and a count question ("how many of my first two
# ...") is asking about the whole set, not for a shortened table.
_LIMITABLE_LIST_INTENTS = {
    "pending_applications", "isd_applications", "nisd_applications",
    "both_applications", "overdue_applications", "all_service_code_applications",
    "highest_priority_applications", "immediate_action", "assigned_today",
    "town_applications", "block_applications", "awaiting_field_visit",
    "field_visits", "fv_between_dates", "fv_scheduled_this_week",
    "fv_overdue_inspections", "fv_unassigned_awaiting",
}


# Phrases by which an officer points back at an application discussed earlier.
_REFERENCE_PATTERNS = (
    "this application", "that application", "same application",
    "this app", "that app", "the application", "the app",
    "prev application", "previous application", "prev app", "previous app",
    "last application", "last app", "above application", "overdue application",
    # Bare pronouns. An officer who has just been shown an application says
    # "what is its status" far more often than they repeat the number, so these
    # have to count as back-references too. They only ever *enable* a lookup in
    # the confirmed context — whatever they resolve to is still re-validated.
    "it", "its", "this one", "that one", "the same one", "previous one", "same one",
    # Positional references into the list the officer is looking at. Resolved by
    # _pick_from_listing against the previous answer's rows.
    "first one", "second one", "third one", "fourth one", "fifth one", "last one",
    "1st one", "2nd one", "3rd one", "first application", "second application",
    "third application", "first app", "second app", "third app",
    "இந்த விண்ணப்பம்", "அந்த விண்ணப்பம்", "முந்தைய விண்ணப்பம்",
    "இதன்", "அதன்", "இது", "அது", "இதே",
    "adhoda", "idhoda", "adhu", "idhu", "adhe", "idhe",
)

# Matched on whole-word boundaries: a plain substring test makes "the app" fire on
# "the applicant", turning an ordinary field question into a back-reference and
# silently attaching it to some unrelated application.
_REFERENCE_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(p) for p in _REFERENCE_PATTERNS) + r")(?!\w)",
    re.IGNORECASE,
)


def _has_application_back_reference(message: str) -> bool:
    """True when the officer explicitly points back at an earlier application."""
    return bool(_REFERENCE_RE.search(message or ""))


def _officer_turns(chat_history: list) -> list:
    """
    Only the officer's own turns may supply an application number for a follow-up.

    Assistant turns quote application numbers the officer never chose — the example
    number inside a format-error message, the number that was just reported as not
    found, every entry of a suggestion list. Harvesting those makes the bot answer
    about an application nobody asked for, so they are excluded here.
    """
    if not chat_history:
        return []
    return [
        m for m in chat_history
        if isinstance(m, dict) and str(m.get("role", "")).lower() in ("user", "human", "officer")
    ]


_ANY_APP_NUMBER_RE = re.compile(
    r'\b\d{4}/(?:0153|0154)/\d{1,3}/\d+\b'
    r'|\b\d{4}/\d{1,3}/(?:0153|0154)/\d+\b'
    r'|(?:ISD|NISD)/\w+/\d+/\d+'
    r'|APP-\d{4}-\d{6}'
    r'|\b20\d{2}/[\w]+/[\w]+/\d+\b',
    re.IGNORECASE,
)
_CORPUS_BLOB = None


def _corpus_blob() -> str:
    global _CORPUS_BLOB
    if _CORPUS_BLOB is None:
        try:
            from pathlib import Path
            _CORPUS_BLOB = " ".join(p.read_text(encoding="utf-8", errors="ignore")
                                    for p in (Path(__file__).resolve().parents[1] / "documents").glob("*.txt")).lower()
        except Exception:
            _CORPUS_BLOB = ""
    return _CORPUS_BLOB


_FULLFORM_BARE_RE = re.compile(
    r"^(?:and\s+)?(?:its\s+|the\s+|what\s+is\s+(?:its\s+|the\s+)?)?(?:full\s*form|fullform|expansion|abbreviation|expand(?:\s+it)?|"
    r"what\s+does\s+(?:it|that)\s+stand\s+for|stands?\s+for|என்பதன்\s+விரிவாக்கம்|விரிவாக்கம்)\s*(?:enna|என்ன)?\s*[?.!]*$", re.IGNORECASE)
_FULLFORM_ASK_RE = re.compile(
    r"^(?:what\s+is\s+)?(?:the\s+)?(?:full\s*form|expansion|abbreviation)\s+(?:of\s+)?([A-Za-z]{2,8})\s*\??$"
    r"|^what\s+does\s+([A-Za-z]{2,8})\s+(?:stand\s+for|mean)\s*\??$"
    r"|^([A-Za-z]{2,8})\s+(?:full\s*form|expansion)(?:\s+(?:enna|what|is|என்ன))?\s*\??$"
    # "what is TSLR used for?" asks the practical meaning, not the expansion
    # wording, but for an abbreviation this guard has no full form for (TSLR:
    # deliberately unaliased -- it names 5 different codes, see
    # find_service_code_by_name) it is the same refusal either way. Without
    # this branch only the "mean"/"stand for" phrasing was caught; "used for"
    # fell to general_query and the LLM invented a specific, confident wrong
    # expansion ("TSLR = Town Survey Land Register") for the very term the
    # "what does TSLR mean?" phrasing of the same question correctly refused.
    r"|^what\s+is\s+([A-Za-z]{2,8})\s+(?:used|meant)\s+for\s*\??$", re.IGNORECASE)
_FULLFORMS = {
    "igrs": ("IGRS = Inspector General of Registration and Stamps.",
             "IGRS = Inspector General of Registration and Stamps (பதிவுகள் மற்றும் முத்திரைகள் துறை)."),
    "sro": ("SRO = Sub-Registrar Office.", "SRO = Sub-Registrar Office (சார்-பதிவாளர் அலுவலகம்)."),
    "csc": ("CSC = Common Service Centre.", "CSC = Common Service Centre (பொது சேவை மையம்)."),
    "can": ("CAN = Citizen Access Number.", "CAN = Citizen Access Number (குடிமகன் அணுகல் எண்)."),
    "sla": ("SLA = Service Level Agreement -- here, the service time limit for a file, in working days.",
            "SLA = Service Level Agreement — இங்கு, ஒரு கோப்புக்கான சேவை நேர வரம்பு (வேலை நாட்களில்)."),
    "sis": ("SIS = Sub Inspector Surveyor.", "SIS = Sub Inspector Surveyor (உதவி ஆய்வாளர் சர்வேயர்)."),
    "sd": ("SD = Senior Draughtsman.", "SD = Senior Draughtsman (மூத்த வரைவாளர்)."),
    "dis": ("DIS = Deputy Inspector Surveyor.", "DIS = Deputy Inspector Surveyor (துணை ஆய்வு சர்வேயர்)."),
    "dsc": ("DSC = Digital Signature Certificate.", "DSC = Digital Signature Certificate (மின்னணு கையொப்பச் சான்று)."),
    "zdt": ("ZDT = Zonal Deputy Tahsildar.", "ZDT = மண்டல துணை வட்டாட்சியர் (Zonal Deputy Tahsildar)."),
    "isd": ("ISD = Involving Sub-Division (service code 0154).", "ISD = Involving Sub-Division (சேவைக் குறியீடு 0154)."),
    "nisd": ("NISD = Not Involving Sub-Division (service code 0153).", "NISD = Not Involving Sub-Division (சேவைக் குறியீடு 0153)."),
    # "fmb" deliberately removed (2026-10-15): no backing full-form entry
    # any more, so "full form of FMB" now gets the honest "I do not have
    # the full form of FMB" refusal via `_unknown_fullform`, instead of a
    # specific expansion for a concept this register does not track.
}
_KNOWN_ACRONYMS = frozenset({"igrs", "sro", "csc", "can", "sla", "sis", "sd", "dis", "dsc", "zdt", "isd", "nisd",
                             "tahsildar", "cin", "ec", "fee"})


def _fullform_term(message: str):
    m = _FULLFORM_ASK_RE.match((message or "").strip())
    # group(4) ("used for" / "meant for") is deliberately NOT read here: it
    # only exists to catch an UNKNOWN acronym for the refusal below, and must
    # not short-circuit a KNOWN one (ISD, CAN, CSC, ...) into this terse
    # one-liner ahead of its existing, richer handler (service_code_lookup,
    # etc.) -- that handler already answers "what is ISD used for?" fine.
    w = ((m.group(1) or m.group(2) or m.group(3)) if m else "") or ""
    return w.lower() if w.lower() in _FULLFORMS else None


def _unknown_fullform(message: str):
    """The acronym asked for by "full form of XYZ" when nothing here documents it, else None."""
    m = _FULLFORM_ASK_RE.match((message or "").strip())
    if not m:
        return None
    w = (m.group(1) or m.group(2) or m.group(3) or m.group(4) or "").lower()
    return w if w and w not in _KNOWN_ACRONYMS else None


def _app_numbers_in_recent_context(chat_history: list, depth: int = 8) -> Tuple[List[str], bool]:
    """
    Application numbers from the most recent officer turn that referred to one.

    Returns (numbers, blocked).

    `numbers` holds every distinct number in that single turn, so the caller can
    tell a clean back-reference ("show application A" -> "its status") from an
    ambiguous one ("compare A and B" -> "its status"), which must be asked about
    rather than resolved to whichever number matched first.

    `blocked` is True when the most recent application-referring turn offered a
    malformed number. The officer's last attempt to name an application failed,
    so "its status" refers to that failed attempt — not to some older application
    further up the conversation. Reaching past it would answer about an
    application the officer has already moved on from.
    """
    for msg in reversed(_officer_turns(chat_history)[-depth:]):
        content = msg.get("content", "") or ""
        found = []
        for m in _ANY_APP_NUMBER_RE.finditer(content):
            token = m.group(0).upper()
            if token not in found:
                found.append(token)
        if found:
            return found, False
        if detect_offered_app_number_token(content):
            return [], True
    return [], False


# ── Application number format validation ────────────────────────────────────
# extract_application_number() only ever matches well-formed numbers; when it
# returns None we cannot tell "the officer mentioned no application" apart from
# "the officer typed an application number wrongly". These helpers make that
# distinction so both chat paths can say which of the two happened.

# The accepted shapes, kept in sync with rag.extract_application_number().
# Also accepts broader YYYY/A/B/N patterns so user-typed non-standard numbers
# are treated as valid lookup attempts (reaching the DB) rather than format errors.
_VALID_APP_NUMBER_RE = re.compile(
    r'\b\d{4}/(?:0153|0154)/\d{1,3}/\d+\b'
    r'|\b\d{4}/\d{1,3}/(?:0153|0154)/\d+\b'
    r'|\b(?:ISD|NISD)/\w+/\d+/\d+\b'
    r'|\bAPP-\d+-\d+\b'
    r'|\b20\d{2}/[\w]+/[\w]+/\d+\b',  # broad YYYY/A/B/N — lookup in DB, not format error
    re.IGNORECASE,
)


# "application <token>" / "app <token>" — the token right after the cue word is
# being offered as the identifier. Requires a digit AND a letter-or-separator so
# that "application 5" (a count) and plain prose are not mistaken for attempts.
_APP_CUE_TOKEN_RE = re.compile(
    r'\b(?:application|applicaton|appl|app|விண்ணப்ப\w*)\s+(?:no\.?|number|num|#)?\s*'
    r'([A-Za-z0-9][\w\-/]*)',
    re.IGNORECASE,
)

# Words that legitimately follow "application" and must never be read as a number.
_APP_CUE_STOPWORDS = {
    "status", "number", "numbers", "details", "detail", "list", "type", "types",
    "id", "ids", "no", "for", "of", "in", "is", "are", "was", "with", "and",
    "documents", "document", "history", "stage", "stages", "count", "summary",
    "pending", "approved", "rejected", "overdue", "isd", "nisd",
    "received", "submitted", "assigned", "form", "forms", "date", "dates",
    "process", "processing", "workflow", "owner", "owners", "applicant",
    "my", "me", "all", "the", "a", "an", "this", "that", "each", "every",
    # Prepositions and question words that follow the noun in an ordinary
    # question -- "show application FROM csc", "show application BY ward".
    # Without these the preposition itself was read as the identifier and the
    # officer was told that "from" is not a valid application number.
    "from", "by", "via", "through", "under", "at", "on", "to", "between",
    "during", "after", "before", "where", "which", "what", "when", "how",
    "who", "whose", "why", "filed", "sent", "raised", "created", "made",
}

# A bare word offered as the identifier ("show application INVALID") only reads as
# a malformed number when an explicit retrieval verb governs it. Without that
# guard, ordinary prose such as "application received today" would be reported as
# a bad application number.
_APP_FETCH_CUE_RE = re.compile(
    r'\b(?:show|open|get|display|fetch|view|find|search|pull)\s+(?:me\s+)?(?:the\s+)?'
    r'(?:application|applicaton|appl|app|விண்ணப்ப\w*)\s+(?:no\.?|number|num|#)?\s*'
    r'([A-Za-z][\w\-/]*)',
    re.IGNORECASE,
)


def detect_offered_app_number_token(message: str) -> Optional[str]:
    """
    The token an officer explicitly offered as an application number — i.e. the
    word right after "application"/"app" — when it is not a valid one.

    This is a high-confidence signal: "show application INVALID-999" is an
    application request with a bad identifier whatever the intent classifier
    decides, so the gate consults it for every intent. Without it such a message
    fell through to a list intent and answered with unrelated applications.
    """
    if not message:
        return None
    for m in _APP_CUE_TOKEN_RE.finditer(message):
        token = (m.group(1) or "").strip().rstrip("/-_.,?")
        if not token or token.lower() in _APP_CUE_STOPWORDS:
            continue
        if not any(c.isdigit() for c in token):
            continue
        # Needs a letter or a separator too, so "application 5" (a count) and
        # ordinary prose are never mistaken for a malformed identifier.
        if not (any(c.isalpha() for c in token) or "-" in token or "/" in token):
            continue
        if _VALID_APP_NUMBER_RE.search(token):
            continue
        return token

    # All-letter identifier, but only under an explicit "show application X".
    for m in _APP_FETCH_CUE_RE.finditer(message):
        token = (m.group(1) or "").strip().rstrip("/-_.,?")
        if not token or token.lower() in _APP_CUE_STOPWORDS:
            continue
        # An application number always carries a digit or a separator
        # ("APP-2024-000001", "2026/0154/28/000041"). A word made only of
        # letters is a filter or a noun -- "from csc", "from citizen",
        # "by ward" -- not a botched identifier, and reporting it as one
        # blocked every such question.
        if not (any(c.isdigit() for c in token) or "-" in token or "/" in token):
            continue
        if _VALID_APP_NUMBER_RE.search(token):
            continue
        return token
    return None


# ── "what should I go and inspect, and where?" ───────────────────────────────
# "in block 0015", "ward 102" -- a location the officer actually named, as
# opposed to the one _geo_scope infers from their jurisdiction.
_VISIT_PLAN_EXPLICIT_GEO_RE = re.compile(
    r"\b(?:block|ward)\s*(?:no\.?|number|#)?\s*\d[0-9A-Za-z]{0,5}\b", re.IGNORECASE)


async def _fetch_visit_plan(db, message: str, officer, ward_number=None,
                            block_number=None) -> dict:
    """Build the day's (or week's) visit plan for the officer.

    The window comes from the question itself ("tomorrow", "next week"); with no
    period named the plan is simply what is outstanding, so no window is passed
    and every booked visit counts as upcoming.
    """
    request = parse_visit_plan_query(message) or {}
    start_d, end_d = extract_date_range(message)
    # "Which block should I go to?" asks the bot to pick the block. _geo_scope
    # answers "block" with the officer's own block when the message names no
    # number, and filtering the plan to it turns the question into its own
    # answer -- so a where-question keeps the whole jurisdiction in view unless
    # the officer actually named a ward or block.
    if request.get("focus") and not _VISIT_PLAN_EXPLICIT_GEO_RE.search(message or ""):
        ward_number = block_number = None
    plan = await get_visit_plan(db, officer, start_date=start_d, end_date=end_d,
                                ward_number=ward_number, block_number=block_number)
    plan["focus"] = request.get("focus")
    plan["asked_ward"] = ward_number
    plan["asked_block"] = block_number
    plan["query_type"] = "Field Visit Plan"
    return plan


async def _build_jurisdiction_summary(db: AsyncSession, officer,
                                      focus: Optional[str] = None) -> Dict[str, Any]:
    """What this officer actually covers: district, taluk, town, wards, blocks,
    and the survey/application counts inside that jurisdiction.

    Two things this must not do, both of which it used to:
      * omit the blocks -- officer_jurisdictions names the ward and leaves
        block_id NULL, so the Block(s) row rendered empty and "what blocks do I
        cover" went unanswered. The blocks of a held ward are held with it.
      * count every survey number in the TALUK. That reported 316 -- the whole
        database -- to an officer who holds one ward with 90.
    """
    from sqlalchemy.orm import joinedload
    q = select(OfficerJurisdiction).options(
        joinedload(OfficerJurisdiction.district),
        joinedload(OfficerJurisdiction.taluk),
        joinedload(OfficerJurisdiction.town),
        joinedload(OfficerJurisdiction.ward),
        joinedload(OfficerJurisdiction.block)
    ).where(OfficerJurisdiction.officer_id == officer.officer_id)
    jurisdictions = (await db.execute(q)).scalars().all()

    if not jurisdictions:
        return {"found": False, "message": "No jurisdictions assigned.",
                "query_type": "Jurisdiction Summary"}

    first = jurisdictions[0]
    d, tk = first.district, first.taluk

    # Blocks named on the jurisdiction row, plus every block of a ward held
    # without naming one.
    ward_ids = [j.ward.id for j in jurisdictions if j.ward]
    blocks_by_ward: Dict[Any, list] = {}
    if ward_ids:
        for blk in (await db.execute(
                select(Block).where(Block.ward_id.in_(ward_ids))
                .order_by(Block.block_number))).scalars().all():
            blocks_by_ward.setdefault(blk.ward_id, []).append(blk.block_number)

    towns_map: Dict[str, Dict[str, list]] = {}
    for j in jurisdictions:
        if not j.town:
            continue
        wards_map = towns_map.setdefault(j.town.name, {})
        if not j.ward:
            continue
        blocks = wards_map.setdefault(j.ward.ward_number, [])
        named = [j.block.block_number] if j.block else blocks_by_ward.get(j.ward.id, [])
        for b in named:
            if b and all(x["block_number"] != b for x in blocks):
                blocks.append({"block_number": b})

    towns_list = [
        {"name": t_name,
         "wards": [{"ward_number": w, "blocks": bl} for w, bl in wards_map.items()]}
        for t_name, wards_map in towns_map.items()
    ]

    # Counts scoped to the officer's own jurisdiction, not the taluk.
    survey_query = select(func.count(SurveyNumber.id)).join(
        Block, SurveyNumber.block_id == Block.id
    ).join(
        Ward, Block.ward_id == Ward.id
    ).join(
        Town, Ward.town_id == Town.id
    ).join(
        Taluk, Town.taluk_id == Taluk.id
    )
    jurisdiction_filters = await get_jurisdiction_filter(db, officer)
    if jurisdiction_filters:
        survey_query = survey_query.where(or_(*jurisdiction_filters))
    elif tk:
        survey_query = survey_query.where(Taluk.id == tk.id)
    survey_count = (await db.execute(survey_query)).scalar() or 0

    active_count = (await db.execute(
        select(func.count(Application.id)).where(
            and_(
                Application.id.in_(_application_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                Application.current_status.in_(["pending", "in_progress"])
            )
        )
    )).scalar() or 0

    return {
        "jurisdiction": {
            "district": {"name": d.name if d else "N/A",
                         "code": d.district_code if d else "N/A"},
            "taluk": {"name": tk.name if tk else "N/A",
                      "code": tk.taluk_code if tk else "N/A"},
            "towns": towns_list,
            "survey_count": survey_count,
            "active_applications": active_count,
            # Which single level the officer asked about ("what is my taluk?"),
            # or None for the whole-jurisdiction question. The renderer answers
            # with just that value when it is set.
            "focus": focus,
        },
        "query_type": "Jurisdiction Summary"
    }


def _asked_registration_detail(message_lower: str) -> Optional[str]:
    """'place' / 'date' / 'reason' when the question is about where or when the
    deed was registered, or why the parcel was transferred. None otherwise.

    Runs as a pre-empt: without it "where was the deed registered?" is claimed
    by sale_deed_check (which answers "yes it is registered") and "when was it
    registered?" by the field map (which answers the *submission* date)."""
    m = message_lower
    if ("regist" in m or "registered" in m) and any(
            w in m for w in ("document number", "document no", "doc number",
                             "document_number", "ஆவண எண்")):
        return "docnum"
    _reg = ("regist" in m or "deed" in m or "பதிவு" in m or "பத்திர" in m
            or "register pann" in m or "registaar" in m)
    if _reg and any(w in m for w in (
            "where", "place", "which office", "which sub", "sub-registrar office",
            "sub registrar office", "sro office", "registered at",
            "registration place", "registration office",
            "எங்கு", "எந்த அலுவலக", "இடம்", "அலுவலக",
            "enga", "enga", "enge", "yenga")):
        return "place"
    if _reg and any(w in m for w in (
            "when", "what date", "which date", "date of registration",
            "registration date", "registered on", "எப்போது", "எந்த தேதி", "தேதி",
            "eppo", "eppothu", "eppadi date")):
        return "date"
    _tr = "transfer" in m or "mutat" in m or "மாற்ற" in m or "transferred" in m
    if _tr and any(p in m for p in (
            "why was", "why is", "why it was", "why this was", "why did",
            "reason for transfer", "reason for the transfer", "transfer reason",
            "mutation reason", "reason it was transferred", "why transferred",
            "transferred by", "transfer by", "transferred via", "transferred on",
            "yen ", "yaen ", "transfer aachu", "transfer aayiduchu",
            "ஏன் மாற்ற", "மாற்றக் காரணம்", "மாற்றத்திற்கான காரணம்")):
        return "reason"
    # A yes/no about the reason -- "is X a court order transfer?", "was it a
    # gift deed transfer?", "transferred by inheritance?"
    if _tr and _named_transfer_reason(m):
        return "reason"
    return None


# The reason phrases officers name, mapped to how transfer_reason reads in the
# extract. Longest / most specific first.
_NAMED_REASON_MAP = [
    ("court order", "Court Order /Judgement"), ("court judgement", "Court Order /Judgement"),
    ("court judgment", "Court Order /Judgement"), ("court decree", "Court Order /Judgement"),
    ("gift deed", "Gift deed"), ("gift settlement", "Gift deed"), ("by gift", "Gift deed"),
    ("settlement deed", "Settlement deed"), ("by settlement", "Settlement deed"),
    ("partition deed", "Partition deed"), ("by partition", "Partition deed"),
    ("release deed", "Release deed"), ("by release", "Release deed"),
    ("legal heir", "Legal Heir"), ("legal-heir", "Legal Heir"),
    ("inheritance", "Legal Heir"), ("inherited", "Legal Heir"),
    ("succession", "Legal Heir"), ("by will", "Legal Heir"),
    ("sale deed", "Sale deed"), ("by sale", "Sale deed"), ("through sale", "Sale deed"),
    ("purchase", "Sale deed"), ("purchased", "Sale deed"),
]


def _named_transfer_reason(message_lower: str) -> Optional[str]:
    """The transfer reason a yes/no question names, or None."""
    for cue, canon in _NAMED_REASON_MAP:
        if cue in message_lower:
            return canon
    return None


# ── Patta-transfer parties: "who is the new / previous owner of <app>?" ──────
# A patta transfer names two parties -- the transferor (previous owner) and the
# transferee (new owner) -- carried on patta_transfers.previous_owner /
# new_owner. Asking for either used to fall through to survey_owners, which
# dumps the WHOLE parcel's current co-owner register (100+ rows) and never
# looks at the word "new" or "previous". These answer the party the officer
# actually named, from get_application_detail()["transfer_parties"].
_TRANSFER_NEW_WORDS = ("new owner", "newowner", "transferee", "buyer", "purchaser",
                       "recipient", "person receiving", "புதிய உரிமையாளர்",
                       "பெறுநர்", "pudhiya urimaiyalar")
_TRANSFER_OLD_WORDS = ("previous owner", "prev owner", "old owner", "former owner",
                       "transferor", "seller", "vendor", "person transferring",
                       "பழைய உரிமையாளர்", "முந்தைய உரிமையாளர்", "வழங்குநர்",
                       "pazhaya urimaiyalar", "mundhaiya urimaiyalar")


def _asks_transfer_party(message: str) -> Optional[str]:
    """'new', 'previous', or None -- which side of a patta transfer the officer
    asked about."""
    m = (message or "").lower()
    if any(w in m for w in _TRANSFER_OLD_WORDS):
        return "previous"
    if any(w in m for w in _TRANSFER_NEW_WORDS):
        return "new"
    return None


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+|[\u0b80-\u0bff]+", text))


_EXCLUDED_CODE_RE = re.compile(
    r"\b(?:not|except|excluding|outside|other\s+than|apart\s+from|aren.t|isn.t|are\s+not|is\s+not)\b"
    r"[^.?!]{0,25}?\b(?:service\s*code\s*)?(0\d{3})\b", re.IGNORECASE)


def _excluded_service_code_filter(message: str) -> Optional[str]:
    """The one non-ISD/NISD service code a message NEGATES -- "applications
    that are not service code 0158", "except 0158", "outside 0158" -- so a
    listing can exclude it rather than (wrongly) include it. "Show
    applications that are not service code 0158." used to be answered with
    exactly 0158's own 20 applications -- the opposite of what was asked --
    because `_requested_service_code_filter` read the bare digits with no
    awareness of the negation in front of them."""
    from backend.utils.helpers import SIS_URBAN_SERVICES
    m = _EXCLUDED_CODE_RE.search(message or "")
    if not m:
        return None
    code = m.group(1)
    if code in SIS_URBAN_SERVICES and code not in ("0153", "0154"):
        return code
    return None


def _requested_service_code_filter(message: str) -> Optional[str]:
    """The one non-ISD/NISD service code named in a bare listing request --
    "F-Line applications", "show 0158 applications" -- so that request
    returns just those applications, not the whole 152-row everything-table.
    ISD/NISD are excluded: they already have their own dedicated intents and
    a two-value result here would be ambiguous anyway (the all-service-code
    register treats "ISD" as the type column, not a service-code filter).

    Never returns a code the message NEGATES ("applications that are not
    service code 0158") -- that is `_excluded_service_code_filter`'s job, and
    reading the bare digits here regardless of the "not" in front of them
    answered the negated question with exactly the one thing it asked to
    leave out.
    """
    from backend.utils.helpers import find_service_codes
    codes = find_service_codes(message)
    if len(codes) == 1 and codes[0] not in ("0153", "0154"):
        if codes[0] == _excluded_service_code_filter(message):
            return None
        return codes[0]
    return None


def _requested_status_filter_for_register(message: str):
    """The status filter for `service_register_entries`, which only ever
    carries approved/pending/rejected (coarse Open/Completed), unlike
    `Application`'s full pending/in_progress/escalated/approved/rejected
    state machine. "show pending applications from citizen" used to be
    excluded from the all-service-code listing entirely (the channel-only
    routing rule in rag.py stood aside the moment the message named a
    status), so it fell to pending_applications, which only ever sees the
    ISD/NISD rows in `Application` -- hiding every F-Line (0178) row that
    is also pending on that same channel. in_progress/escalated collapse
    to "pending" here (this table cannot tell them apart); a request for
    every status (e.g. "all pending and approved and rejected") collapses
    to None -- no filter -- rather than a no-op list of all three.
    """
    named = _explicit_status_request(message.lower())
    if named is None:
        return None
    _REGISTER_STATUSES = {"pending", "approved", "rejected"}

    def _coarsen(s):
        return "pending" if s in ("in_progress", "escalated") else s

    if isinstance(named, list):
        mapped = []
        for s in named:
            c = _coarsen(s)
            if c not in mapped:
                mapped.append(c)
        if set(mapped) >= _REGISTER_STATUSES:
            return None
        return mapped
    return _coarsen(named)


def _render_all_service_code_answer(data: dict, is_tamil: bool = False,
                                    count_only: bool = False,
                                    distinct_services: bool = False) -> str:
    """A flat table across every TAMILNILAM service code, from
    `get_all_service_code_applications()`'s `service_register_entries` rows.
    Same column layout as the ISD/NISD applications table (Application No. /
    Type / Survey No. / Sub-Divisions / Status / Stage / Submitted / Ward /
    Block) so the two read as one family. Survey No. and Sub-Divisions are real
    (every service code carries them in the source extract); Stage is a coarse
    Open/Completed read off the extract's own C/P workflow marker, not a
    multi-desk chain.

    Every service code is treated alike -- none is singled out or footnoted.
    The per-code breakdown and the totals are counted from the rows actually
    shown (after any negation or "first N" cap), never from the pre-cap query.
    A question that names ISD/NISD, or something only they carry, is answered
    by the ISD/NISD handler instead and never reaches this renderer.
    """
    from html import escape as _e
    from backend.services.rag import _status, _app_link
    from backend.utils.helpers import SIS_URBAN_SERVICES
    if not data:
        return ""
    apps = data.get("applications") or []
    count = data.get("count", len(apps))
    _chan_label = {"CSC": "CSC", "citizen": "citizen portal", "sub_registrar": "Sub-Registrar"}

    # ---- what the officer asked for, in words -------------------------------
    st = data.get("status_filter")
    st_list = st if isinstance(st, list) else ([st] if st else [])
    st_words = " & ".join(_STATUS_LABELS.get(x, str(x).title()).lower() for x in st_list)
    chans = data.get("channel_filter")
    chans = list(chans) if isinstance(chans, (list, tuple, set)) else ([chans] if chans else [])
    chan_name = " / ".join(_chan_label.get(c, c) for c in chans)
    svc_code = data.get("service_code_filter")
    svc_name = (SIS_URBAN_SERVICES.get(svc_code, {}) or {}).get("name", svc_code) if svc_code else None
    excl_code = data.get("service_code_excluded")
    excl_name = (SIS_URBAN_SERVICES.get(excl_code, {}) or {}).get("name", excl_code) if excl_code else None
    period = data.get("period_label")
    if period and data.get("exclude_date_range"):
        period = f"not {period}"
    geo = []
    if data.get("ward_filter"):
        geo.append(f"Ward {data['ward_filter']}")
    if data.get("block_filter"):
        geo.append(f"Block {data['block_filter']}")

    scope_en = " ".join(x for x in [
        st_words,
        "application(s)",
        f"from {chan_name}" if chan_name else "",
        f"under {svc_code} ({svc_name})" if svc_code else "",
        f"excluding service code {excl_code} ({excl_name})" if excl_code else "",
        f"in {period}" if period else "",
        f"in {' / '.join(geo)}" if geo else "",
    ] if x)
    if data.get("excluded"):
        scope_en += f" ({str(data.get('query_type') or '').replace('Applications', '').strip(' ()')})"

    # ---- per-code breakdown, from the rows kept -----------------------------
    by_svc: dict = {}
    by_chan: dict = {}
    for a in apps:
        by_svc[a.get("service_code")] = by_svc.get(a.get("service_code"), 0) + 1
        if a.get("submission_channel"):
            by_chan[a["submission_channel"]] = by_chan.get(a["submission_channel"], 0) + 1
    svc_summary = ", ".join(f"{code} ({n})" for code, n in sorted(by_svc.items(), key=lambda kv: str(kv[0])))
    chan_summary = ", ".join(f"{n} {_chan_label.get(c, c)}" for c, n in sorted(by_chan.items()))

    hidden = data.get("rejected_hidden") or 0
    hidden_note_en = (f" {hidden} rejected application(s) matching this are left out; "
                      f"ask for rejected applications to see them." if hidden else "")
    hidden_note_ta = (f" இதனுடன் பொருந்தும் {hidden} நிராகரிக்கப்பட்ட விண்ணப்பங்கள் சேர்க்கப்படவில்லை." if hidden else "")

    # "How many services currently have pending applications?" counts the
    # DISTINCT service codes in `by_svc` (one entry per code actually present
    # in the filtered rows -- never a hardcoded or manually maintained list),
    # not `count` (the application total `by_svc`'s own values sum to). Zero
    # qualifying services is stated explicitly, never silently folded into
    # the generic "no applications found" wording below.
    if distinct_services:
        n_services = len(by_svc)
        if is_tamil:
            _ta_status = " ".join({"pending": "நிலுவையில் உள்ள", "approved": "அங்கீகரிக்கப்பட்ட",
                                    "rejected": "நிராகரிக்கப்பட்ட"}.get(x, x) for x in st_list)
            qualifier = " ".join(x for x in [
                f"{period} இல்" if period else "", f"{' / '.join(geo)} இல்" if geo else "",
                f"{chan_name} வழியாக" if chan_name else "", _ta_status,
            ] if x)
            intro = (f"{qualifier + ' ' if qualifier else ''}{n_services} சேவை(கள்) நிலுவையில் உள்ள விண்ணப்பங்களைக் "
                     f"கொண்டுள்ளன (மொத்தம் {count} விண்ணப்பங்கள்)" +
                     (f" — {svc_summary}." if svc_summary else "."))
        else:
            status_word = f"{st_words} " if st_words else "pending "
            scope_bits = " ".join(x for x in [
                f"from {chan_name}" if chan_name else "",
                f"in {period}" if period else "",
                f"in {' / '.join(geo)}" if geo else "",
            ] if x)
            intro = (f"<strong>{n_services}</strong> service code(s) currently have {status_word}applications"
                     f"{(' ' + scope_bits) if scope_bits else ''} ({count} application{'s' if count != 1 else ''} in all)" +
                     (f": {svc_summary}." if svc_summary else "."))
        intro += hidden_note_ta if is_tamil else hidden_note_en
        return f"<div class='table-intro'>{intro}</div>"

    if not apps:
        if is_tamil:
            note = "உங்கள் அதிகார எல்லையில் பொருந்தும் விண்ணப்பங்கள் இல்லை." + hidden_note_ta
        else:
            note = f"No {scope_en} found in your jurisdiction.{hidden_note_en}".replace("application(s)", "applications", 1)
            if data.get("empty_note") and not (st_words or period or geo or chan_name or svc_code):
                note = data["empty_note"]
        return f"<div class='table-intro'>{_e(note)}</div>"

    across = "" if svc_code else (
        f" across every service code in your jurisdiction ({svc_summary})" if svc_summary else "")
    if is_tamil:
        _ta_st = {"pending": "நிலுவையில் உள்ள", "approved": "அங்கீகரிக்கப்பட்ட",
                  "rejected": "நிராகரிக்கப்பட்ட"}
        _ta_status = " ".join(_ta_st.get(x, x) for x in st_list)
        _ta_bits = [b for b in [
            f"{period} இல்" if period else "",
            f"{' / '.join(geo)} இல்" if geo else "",
            f"{chan_name} வழியாக" if chan_name else "",
        ] if b]
        intro = (f"உங்கள் அதிகார எல்லையில் {' '.join(_ta_bits) + ' ' if _ta_bits else ''}"
                 f"{_ta_status + ' ' if _ta_status else ''}{count} விண்ணப்பங்கள் "
                 f"({'அனைத்து சேவை குறியீடுகளிலும்' if not svc_code else svc_code}) — {svc_summary}.")
        if not svc_code and not chans and chan_summary:
            intro += f" வழிவாரியாக: {chan_summary}."
    else:
        intro = f"Found <strong>{count}</strong> {_e(scope_en)}{_e(across)}."
        if not svc_code and not chans and chan_summary:
            intro += f" By channel: {chan_summary}."
    lim = data.get("result_limit")
    if isinstance(lim, dict) and not lim.get("out_of_range") and lim.get("total"):
        which = "last" if lim.get("end") == "tail" else "first"
        if lim.get("end") == "head_nth":
            intro += f" Showing result {lim.get('n')} of {lim['total']}."
        elif lim.get("end") != "rows":
            intro += f" Showing the {which} {lim.get('n')} of {lim['total']}."
    elif isinstance(lim, dict) and lim.get("out_of_range"):
        intro += f" There is no result at that position; the list has {lim.get('total')}."
    intro += hidden_note_ta if is_tamil else hidden_note_en
    if count_only:
        return f"<div class='table-intro'>{intro}</div>"

    head = ("<tr><th>Application No.</th><th>Type</th><th>Survey No.</th>"
            "<th>Sub-Divisions</th><th>Status</th><th>Stage</th><th>Submitted</th>"
            "<th>Ward</th><th>Block</th></tr>")
    if is_tamil:
        head = ("<tr><th>விண்ணப்ப எண்</th><th>வகை</th><th>சர்வே எண்</th>"
                "<th>உட்பிரிவுகள்</th><th>நிலை</th><th>நிலை (Stage)</th>"
                "<th>சமர்ப்பிக்கப்பட்ட தேதி</th><th>வார்டு</th><th>தொகுதி</th></tr>")

    def _type_label(a):
        name = a.get("service_name")
        return name if name and name != "Unknown service" else (a.get("service_code") or "-")

    def _named(number, name):
        # The ward master's own ename is a dumped record, not a display name --
        # "1 (021) Orakadam (Ward:A)" -- so the readable part is pulled out
        # rather than shown (or appended to the number) verbatim.
        number = number or "-"
        if not name or name in ("N/A", "-"):
            return number
        m = re.match(r'^\s*\d+\s*\(\d+\)\s*(.+?)\s*\(Ward:', name, re.IGNORECASE)
        clean = m.group(1).strip() if m else name.strip()
        return clean if clean and clean != number else number

    rows = ""
    for a in apps:
        rows += (
            "<tr>"
            f"<td>{_app_link(a.get('application_number'))}</td>"
            f"<td>{_e(_type_label(a))}</td>"
            f"<td>{_e(a.get('survey_no') or '-')}</td>"
            f"<td>{_e(a.get('subdivision_no') or '-')}</td>"
            f"<td>{_status(a.get('status') or 'pending', 'ta' if is_tamil else 'en')}</td>"
            f"<td>{_e(a.get('stage') or '-')}</td>"
            f"<td>{_e(a.get('submission_date') or '-')}</td>"
            f"<td>{_e(_named(a.get('ward_number'), a.get('ward_name')))}</td>"
            f"<td>{_e(_named(a.get('block_number'), a.get('block_name')))}</td>"
            "</tr>"
        )
    return (f"<div class='table-intro'>{intro}</div>"
            f"<table class='data-table'><thead>{head}</thead><tbody>{rows}</tbody></table>")


# ── IGRS / CAN asked as a RULE, not about a file ─────────────────────────────
# "If the IGRS number is absent, what does it mean -- so it is not from SRO?"
# is a question about the rule, and it must not be answered with a table of
# applications. Two things used to make it one: the officer's word "IGRS" is
# read as a Sub-Registrar channel filter (correctly, for "which of MY
# applications have an IGRS number"), and the LLM fallback had almost nothing
# in the corpus to work from -- "SRO" appears nowhere in it, so "what is SRO"
# came back as "the query was refused, which means it is outside the
# jurisdiction of the officer", which is not even the right kind of answer.
#
# These are documented invariants, not counts, so they are answered in Python
# rather than left to the model. The same statements are in
# backend/documents/land_rules.txt for retrieval.

# Asking about the rule outright.
_RULE_STRONG_RE = re.compile(
    r"\bif\b|\bwhat\s+does\s+(?:it|that|this|an?)\b|\bmeans?\b|\bmeaning\b"
    r"|\bwhy\b|\bsame\s+as\b|\bdifference\b"
    r"|\bwhich\s+applications\s+(?:get|carry|have)\b|\bwho\s+(?:gets|issues)\b"
    r"|\bstand\s+for\b|\bfull\s+form\b"
    r"|என்றால்|அர்த்தம்|ஏன்",
    re.IGNORECASE,
)
# Asking for a definition. The article carries the whole distinction: "what is
# A can number" wants the definition, "what is THE can number" wants this
# application's -- and the second is the commonest follow-up there is, so it
# must not be claimed here.
_RULE_DEFN_RE = re.compile(
    r"\b(?:sro|igrs|can|csc|sub[\s-]?registrar)\s*(?:number\s*)?(?:na|naa)\s+(?:enna|yaaru|yaru|yar)\b"
    r"|\bwhat\s+(?:is|are)\s+(?:a|an)\b"
    r"|\bwhat\s+(?:is|are)\s+(?:sro|igrs|can)\b"
    r"|\bwhat\s+is\s+igrs\s+form\b",
    re.IGNORECASE,
)
# "CAN எண் என்ன" -- bare Tamil "என்ன" ("what") after the subject, the plainer
# twin of "என்றால் என்ன" ("what does X mean") above. Tamil has no articles, so
# unlike "a can number" vs "the can number" in English, this exact phrasing is
# also the commonest follow-up ("status of <app>" then "CAN எண் என்ன?" wants
# THAT application's CAN, not the definition). Kept separate from
# `_RULE_STRONG_RE` so the caller can drop it when an application is already
# in view -- weak on its own, strong only when nothing else already answers it.
_TAMIL_BARE_WHAT_RE = re.compile(r"என்ன")


def _is_rule_shaped(msg: str, allow_bare_tamil_what: bool = True) -> bool:
    if _RULE_STRONG_RE.search(msg) or _RULE_DEFN_RE.search(msg):
        return True
    return allow_bare_tamil_what and bool(_TAMIL_BARE_WHAT_RE.search(msg))


# The three submission channels, as words an officer uses to name them. Order
# is canonical so "CSC and SRO" and "SRO and CSC" produce the same topic.
_CHANNEL_DEFN_WORDS = (
    ("sub_registrar", re.compile(r"\bsro\b|\bsub[\s-]?registrar\b|சார்-?பதிவாளர்",
                                 re.IGNORECASE)),
    ("CSC", re.compile(r"\bcsc\b|\bcommon\s+service\s+cent(?:er|re)\b"
                       r"|சேவை\s*மைய|இ-?சேவை", re.IGNORECASE)),
    ("citizen", re.compile(r"\bcitizen\b|குடிமகன்", re.IGNORECASE)),
)


# "what are SRO applications", "what are my CSC applications" -- a request for
# the REGISTER, not for what the office is. `_SRO_ROLE_RE` claimed those on the
# words "what are" alone, so an officer asking to SEE their Sub-Registrar files
# was handed the definition of the Sub-Registrar Office and no list at all.
# A message naming applications / files is a listing and is left to the channel
# scope; the singular with an article ("what is an SRO application?") is still
# a definition.
_CHANNEL_LISTING_RE = re.compile(
    r"\b(?:applications?|apps?|files?|cases?)\b|விண்ணப்ப",
    re.IGNORECASE,
)
_CHANNEL_DEFN_SINGULAR_RE = re.compile(
    r"\b(?:a|an|one)\s+(?:sro|csc|citizen|sub[\s-]?registrar)\b",
    re.IGNORECASE,
)


def _asks_for_channel_list(msg: str) -> bool:
    """True when a channel question names the register, so it wants rows."""
    return bool(_CHANNEL_LISTING_RE.search(msg or "")
                and not _CHANNEL_DEFN_SINGULAR_RE.search(msg or ""))


def _channels_defined(msg: str) -> list:
    """Which submission channels a definition question names, canonical order."""
    return [name for name, rx in _CHANNEL_DEFN_WORDS if rx.search(msg or "")]


# ── A follow-up that only makes sense against the list already on screen ─────
# Same idea as the bare-date re-scope above, for the other kind of fragment:
# "show applications from CSC" -> "how many of them are approved". Answered on
# its own, that second question has no list to count and fell through to the
# LLM; worse, one that happened to route to the list intent re-ran the default
# open queue and handed back an unrelated table as if it were the answer.
_LIST_FOLLOWUP_INTENTS = frozenset({
    "pending_applications", "isd_applications", "nisd_applications",
    "both_applications", "all_service_code_applications",
    # "compare A and B" -> "which of these two took longer" folds back the same
    # way, and the two numbers come with it.
    "compare_applications",
    # "show my field visits" -> "which one is overdue?" / "how many are
    # completed?" -- a bare aggregate over the list just shown. Without this
    # the fragment routed to application_status and answered "give me an
    # application number", when the officer meant the 13 visits on screen.
    "field_visits",
})
# ...but only when it names no subject of its own. "how many ISD applications
# do I have" is a fresh question, not a continuation.
_OWN_SUBJECT_RE = re.compile(
    r"\d{4}/\d{3,4}/\d{1,3}/\d+"
    r"|\bapplications?\b|\bapps?\b|\bsurvey\b|\bpatta\b|\bmy\b|\bvisits?\b"
    r"|விண்ணப்ப|கணக்கெண்|என்",
    re.IGNORECASE,
)

# The mutually-exclusive lifecycle states. A fragment naming one of these while
# the previous message named a DIFFERENT one is a fresh question, not a
# continuation: "show my rejected applications" -> "how many are approved?" must
# not fold into "show my rejected applications how many are approved" and be
# answered as a rejected count.
_LIFECYCLE_STATUS_RE = re.compile(
    r"\b(approved?|rejected?|pending|in[\s-]?progress|escalated)\b", re.IGNORECASE)


_SORT_OVERRIDE = contextvars.ContextVar("sort_override", default=None)


_CHANNEL_LABELS = {"CSC": "CSC", "sub_registrar": "Sub-Registrar",
                   "citizen": "citizen"}


def _carried_list_channel(context) -> Optional[str]:
    """The submission channel the carried listing was scoped to, if any.

    Authoritative -- read from what the previous listing WAS, not from the rows
    a narrowing follow-up returned. `filters['submission_channel']` when the
    answer recorded it, else the `query_type` ("All CSC Applications", "All
    Sub-Registrar Applications", "All citizen Applications").
    """
    if not context:
        return None
    _filters = getattr(context, "filters", None) or {}
    # A listing scoped to SEVERAL channels has no single channel to claim.
    # Without this the query_type fallback below read "All CSC & Sub-Registrar
    # Applications", found "csc" in it, and hung "CSC" on a carried set that
    # was half Sub-Registrar.
    _many = _filters.get("submission_channels")
    if isinstance(_many, (list, tuple)) and len(set(_many)) > 1:
        return None
    ch = _filters.get("submission_channel")
    if ch:
        return ch
    qt = (getattr(context, "query_type", None) or "").lower()
    if "sub-registrar" in qt or "sub registrar" in qt:
        return "sub_registrar"
    if "csc" in qt:
        return "CSC"
    if "citizen" in qt:
        return "citizen"
    return None


def _carried_scope_label(rows: list, skip_type: bool = False,
                         carried_channel: Optional[str] = None,
                         narrowed: bool = False) -> str:
    """A phrase naming what the CARRIED set has in common.

    "20 of those 21 are approved" leaves the officer to remember which 21 --
    so when every one of the carried applications shares a channel or a type,
    the answer says so.

    The subtlety: when the follow-up NARROWED the re-query (a status / type /
    channel refinement), `rows` is that subset, not the carried total -- so an
    attribute every subset row happens to share is NOT necessarily shared by
    the whole. "show my approved applications" (29 CSC + 23 SRO) -> "how many
    are ISD?" returned 2 rows that were both CSC, and hanging "CSC" on "those
    52" was a false claim about the other 50. So when `narrowed`, the channel
    is taken only from `carried_channel` (which the previous listing asserts),
    never inferred from the rows.

    `skip_type` drops the type from the phrase when the follow-up is what
    narrowed to that type -- reading it back off the rows said "12 of those 34
    ISD application(s) are ISD".
    """
    if not rows:
        return ""
    bits = []
    if carried_channel:
        bits.append(_CHANNEL_LABELS.get(carried_channel, str(carried_channel)))
    elif not narrowed:
        channels = {r.get("submission_channel") for r in rows}
        if len(channels) == 1:
            only = channels.pop()
            if only:
                bits.append(_CHANNEL_LABELS.get(only, str(only)))
    types = {r.get("type") for r in rows}
    if not skip_type and not narrowed and len(types) == 1:
        only = types.pop()
        if only:
            bits.append(str(only))
    return " ".join(bits)


_OWNER_CTX_COMPARE = re.compile(
    r"\b(?:fields?|details?|things?)\b.{0,25}\b(?:chang\w*|differ\w*|diffrent|same|stay\w*|match\w*)\b.{0,20}\b(?:between|across)\b.{0,15}\b(?:them|these|both|the\s+two)\b"
    r"|\b(?:are|is)\s+(?:they|these|both)\b.{0,20}\b(?:same|different|diffrent)\b"
    r"|\bany\s+(?:differences?|mismatch\w*)\s+between\s+(?:them|these|both)\b", re.IGNORECASE)


def _owner_ctx_compare(message, chat_history=None) -> bool:
    """"which fields changed between them?" right after an owner answer compares those owners."""
    if not _OWNER_CTX_COMPARE.search(message or ""):
        return False
    for m in reversed((chat_history or [])[-4:]):
        if (isinstance(m, dict) and str(m.get("role", "")).lower() in ("assistant", "ai", "bot")):
            c = m.get("content") or ""
            return bool(re.search(r"\((?:new|old|returned) owners?\)|\bowner\b", c[:400], re.IGNORECASE))
    return False


def _na_parse(message, chat_history=None):
    """The Natham-chitta modification question, including a bare follow-up right after one."""
    p = _natham_qa.parse(message)
    if p or not chat_history:
        return p
    if re.search(r"\b(?:owners?|relative|relation(?:ship)?|patta|door|share|address|pincode|ration|epic|cin|aadhaar|aadhar|sex|male|female|operation|status|flow\s*type|full\s+data|user\s+no|updated?)\b", message or "", re.IGNORECASE):
        for m in reversed((chat_history or [])[-4:]):
            if isinstance(m, dict) and str(m.get("role", "")).lower() in ("assistant", "ai", "bot"):
                if "(Natham chitta modification)" in (m.get("content") or "")[:300]:
                    return _natham_qa.parse("natham " + message)
                break
    return None


def _um_parse(message, chat_history=None):
    """The parcel-modification question, including a bare follow-up ("and the door no?") right after one."""
    p = _uamod_qa.parse(message)
    if p or not chat_history:
        return p
    if re.search(r"\b(?:survey|sub[\s-]*division|door|govt|private|land\s+(?:type|use)|tax|soil|extent|patta|remarks|changed?|unchanged|differences?|approval|reject\w*|updated?|assessed|cultivable|street|coss|irrigation|municipal|operation)\b", message or "", re.IGNORECASE):
        for m in reversed((chat_history or [])[-4:]):
            if isinstance(m, dict) and str(m.get("role", "")).lower() in ("assistant", "ai", "bot"):
                if "(parcel modification)" in (m.get("content") or "")[:300]:
                    return _uamod_qa.parse("modification " + message)
                break
    return None


_TSLR_FIELD_WORD_RE = re.compile(
    r"\b(?:survey|sub[\s-]*division|old\s+patta|door|challan|treasury|bank|amount|extent|land\s+type|type\s+e|ecate|submitted|district|ward|block)\b",
    re.IGNORECASE)

# Record/module-type names whose bare "what is X?" / "explain X" is ambiguous
# between "define the concept" and "does this application have one" -- see
# `_is_bare_concept_definition`. Deliberately narrow: an ordinary FIELD name
# ("SIS recommendation", "challan date") shaped the same way ("what is X?")
# is NOT here, because for those the chat_history reading (the application
# discussed earlier) is the right one and must keep working.
_MODULE_CONCEPT_RE = re.compile(
    r"\btslr\b|\bf[\s-]?line\b|\bnatham\b|\bcorrection\s*modification\b"
    r"|\bnew\s+owner\b|\bold\s+owner\b|\bigrs\s+owner\b|\bcross\s*check\b"
    r"|\bappinfo\b|\bapplication\s+information\b"
    r"|\bsd\s+attachment\b|\bparcel\s+modification\b|\buareg\s+modification\b"
    r"|\bmerge\s+id\b|\bmerge\s+application\b|\bfull\s+field\s+(?:patta\s+)?transfer\b"
    # Found by sweeping every field name the record-module claimants
    # (appinfo_qa / fulltransfer_qa / uamod_qa) recognise through a bare
    # "what is X?" with no article -- the indefinite-article signal below
    # does not catch these (no "a"/"an"), so they are listed explicitly.
    r"|\bphysical\s+verification\b|\bowner\s+correction\s+reason\b"
    r"|\bland\s+category\b|\blegal\s+heir\b"
    r"|\bpurchased\s+area\b|\bold\s+patta\b|\bzdt\s+patta\b"
    r"|\bdoor\s+number\b|\bcoss\s+block\b",
    re.IGNORECASE)
# "registration place" was removed from the list above: it is an ordinary
# field exactly like "registration date" (never in this list) or "SIS
# recommendation" (see DISTINCT in fulltransfer_qa.py) -- this function's own
# docstring says an ordinary field name must NOT be here, since that keeps it
# from ever resolving "what is the registration place?" from chat_history the
# way every other field follow-up does. Its presence silently contradicted
# that stated rule and routed the bare follow-up to general_query instead.
_DEFN_SHAPE_RE = re.compile(
    r"^\s*(?:what\s+(?:is|are|does|do)|explain|define|meaning\s+of)\b", re.IGNORECASE)
# "what is A challan?" / "what is AN F-Line?" -- the indefinite article is the
# same signal CLAUDE.md already documents for CAN/IGRS questions ("what is A
# can number" wants the definition, "what is THE can number" wants this
# application's"). It generalises past any hand-maintained concept list: a
# sweep of every per-application field name across appinfo_qa / fulltransfer_
# qa / uamod_qa / natham_qa / newowner_qa found the SAME bug for "what is a
# challan?", "a proposed visit", "a transfer order", "a reason code", "a
# transaction status", "a reject reason", "a barcode flag", "a direct
# transfer flag" -- a hand-maintained allowlist can never keep up with every
# field name in every one of those modules, but the article a/an genuinely
# does mean "define this", the same way it already does for CAN/IGRS.
_INDEFINITE_DEFN_RE = re.compile(r"^\s*what\s+(?:is|are)\s+(?:a|an)\b", re.IGNORECASE)
# "what does X mean?" / "meaning of X" / "define X" ask for a definition
# whatever X is -- nobody says "what does the SIS recommendation mean?" to
# ask for its VALUE, "mean" itself is the tell.
_UNCONDITIONAL_DEFN_RE = re.compile(
    r"^\s*what\s+(?:does|do)\s+.+\bmean\b|^\s*meaning\s+of\b|^\s*define\b", re.IGNORECASE)


def _is_bare_concept_definition(message: str) -> bool:
    """"what is TSLR?" / "explain F-Line" / "what is a challan?" -- a
    definition question, not a question about one file.

    Every claimant that reads a record/module/field name alone as "does THIS
    application have one" needs this same guard, and there are several
    independent call sites (`_cm_strong()`'s claim chain, and
    `_early_subdivision_answer_inner()`'s own `_tsl` / `_fl` / ... parses,
    which run through `_extract_app_number_from_context(...,
    allow_implicit_continuation=True)` and so silently inherit an
    application from several turns back) -- hence one shared function rather
    than the regex copied at each site, the same trap CLAUDE.md documents
    for the `cmflow_qa` gates. Looks only at THIS message: an application
    shown earlier must not turn a bare definition into a question about that
    file -- that reading needs an explicit number or a back-reference
    pronoun in the message itself.

    Three ways a message qualifies as a definition (any one is enough):
    `_MODULE_CONCEPT_RE` (a known record-type name), the indefinite article
    ("what is a/an X"), or an unconditionally definitional phrasing ("what
    does X mean", "define X"). A bare field name with no article and no
    "mean" ("what is the SIS recommendation?", "what is door number?") is
    deliberately left alone -- that reading keeps using chat_history, which
    is what makes an ordinary field follow-up work at all.
    """
    msg = message or ""
    if extract_application_number(msg) or _has_application_back_reference(msg):
        return False
    if _UNCONDITIONAL_DEFN_RE.match(msg):
        return True
    if not _DEFN_SHAPE_RE.match(msg):
        return False
    return bool(_MODULE_CONCEPT_RE.search(msg) or _INDEFINITE_DEFN_RE.match(msg))


# "list TSLR applications" / "show my SRO applications" / "show all
# applications in fline" -- a register-wide LISTING scoped by a service code
# or channel, not a question about one file. The claimants this stands aside
# for (`_tslr_parse`, `_fline_qa.parse`, `_appinfo_qa.parse`/`.applicant_
# keys`, ...) read the bare code/channel word alone as a per-application
# field claim regardless of listing shape -- unlike `_is_bare_concept_
# definition`, which only covers "what is X" phrasing, this covers the
# "list/show/all X applications" phrasing that found the same bug for a
# fresh listing request right after a specific application was discussed.
_LISTING_SHAPE_RE = re.compile(
    r"\b(?:list|show|display|give|how\s+many|which|count|all)\b", re.IGNORECASE)
_LISTING_NOUN_RE = re.compile(r"\b(?:applications?|files?|apps?|cases?)\b", re.IGNORECASE)


def _is_register_wide_listing(message: str) -> bool:
    """A listing/count request over the register, not a per-application
    question -- see the module comment above. Only reached once the message
    is already known to carry no explicit application number or back-
    reference (callers check `_is_bare_concept_definition`-style evidence
    first, or do so via the same two checks repeated here for safety)."""
    msg = message or ""
    if extract_application_number(msg) or _has_application_back_reference(msg):
        return False
    return bool(_LISTING_SHAPE_RE.search(msg) and _LISTING_NOUN_RE.search(msg))


def _tslr_parse(message, chat_history=None):
    """The TSLR-sketch question, including a bare follow-up ("and the sub division no?") right after a TSLR answer."""
    if _is_bare_concept_definition(message):
        return None
    p = _tslr_qa.parse(message)
    if p:
        return p
    # The message itself names a 0156 application -- "what is the challan
    # date for 2026/0156/02/000314?" has no word "tslr" in it at all, so this
    # was never claimed here and fell to `appinfo_qa`'s challan-date field,
    # which reads a DIFFERENT table (application-information, 0153/0154
    # only) and answered "no application-information record is held" for a
    # file that genuinely has a challan date -- just in the TSLR table, which
    # this app's own code already points at.
    _tslr_app = extract_application_number(message)
    if (_tslr_app and _tslr_app.count("/") >= 3 and _tslr_app.split("/")[1] == "0156"
            and _TSLR_FIELD_WORD_RE.search(message or "")):
        return _tslr_qa.parse("tslr " + message)
    if not chat_history:
        return None
    if _TSLR_FIELD_WORD_RE.search(message or ""):
        for m in reversed((chat_history or [])[-4:]):
            if isinstance(m, dict) and str(m.get("role", "")).lower() in ("assistant", "ai", "bot"):
                if "(TSLR sketch)" in (m.get("content") or "")[:300] or "TSLR sketch" in (m.get("content") or "")[:300]:
                    return _tslr_qa.parse("tslr " + message)
                break
    return None


def _sd_parse(message, chat_history=None):
    """The SD-attachment question, including a bare follow-up ("is there a file available?") right after an
    SD-attachment answer."""
    p = _sdattach_qa.parse(message)
    if p or not chat_history:
        return p
    if re.search(r"\b(?:files?|survey|sub[\s-]*division|sl\s*no|duplicates?|missing)\b", message or "", re.IGNORECASE):
        for m in reversed((chat_history or [])[-4:]):
            if isinstance(m, dict) and str(m.get("role", "")).lower() in ("assistant", "ai", "bot"):
                if "(SD attachments)" in (m.get("content") or "")[:300] or "SD attachment" in (m.get("content") or "")[:300]:
                    return _sdattach_qa.parse("sd attachment " + message)
                break
    return None


def _cm_strong(message, chat_history=None) -> bool:
    """A desk recommendation / remarks / flag question (correction-modification flow) or an
    application-information field question (challan, amount, proposed visit, ...) that a
    dedicated record answers. A SIS-only question about an ISD / NISD file stays with the
    existing workflow handlers."""
    if re.search(r"\bsurvey\b[^0-9]{0,12}\d", message or "", re.IGNORECASE) and not extract_application_number(message):
        return False  # a question about a survey number is the survey handlers', not a record of an application
    # "what is TSLR?" / "what does TSLR mean?" / "explain the new owner
    # record" -- a bare DEFINITION of a MODULE-LEVEL concept (a record type,
    # not a field inside one), not a question about one file. `_tslr_parse()`
    # and the other claimants below read the bare term alone as "does THIS
    # application have a TSLR/new-owner/... record", true even with no
    # application number anywhere in the conversation, which forced the
    # officer through the application-number gate for a question that was
    # never about one file ("what is TSLR?" -> "Please specify the
    # application number..."). Deliberately does NOT look at chat_history: an
    # application shown two turns ago does not turn a bare "what is TSLR?"
    # into a question about that file -- that reading needs the officer to
    # actually say so, with an explicit number or a back-reference pronoun in
    # THIS message ("what is its TSLR status?", "...of this application?").
    #
    # Fenced to a small list of module/record-type names on purpose: "what is
    # the SIS recommendation?" is shaped identically ("what is X") but names
    # an ordinary FIELD, not a record type, and an officer asking it two
    # turns after "show <app>" does mean that application's recommendation --
    # that reading must keep using chat_history the way it always has. Only
    # the record-type names below are ever ambiguous between "define the
    # concept" and "does this file have one", so only they get this guard.
    if _is_bare_concept_definition(message):
        return False
    if (not extract_application_number(message) and extract_submission_channels(message) and (
            _qualifier_guard.listing_shaped(message)
            or (re.search(r"\b(?:list|show|display|give|how\s+many|which|count|all)\b", message or "", re.IGNORECASE)
                and re.search(r"\b(?:applications|files|apps|cases)\b", message or "", re.IGNORECASE)))):
        return False  # "list SRO applications" / "what are applications from SRO": a channel scope on a
        # list, not the SRO column of one application. `listing_shaped()` (shared with the channel-listing
        # gate and qualifier_guard) also catches the plain "what are..." phrasing the hand-rolled verb list
        # here missed -- that gap let a stale application from context answer a fresh listing question with
        # that one file's SRO field instead of the channel's applications.
    # "show all applications in fline" / "list TSLR applications" -- a
    # register-wide LISTING scoped by service code, not a question about one
    # file. `_fline_qa.claims()` / `_tslr_parse()` read the bare code name
    # alone as a field claim (true even on a listing request), and with a
    # stale application number from the previous turn still reachable via
    # implicit continuation, this answered "Application <stale number> was
    # not found" to a request that never named one file -- the same channel
    # vs. listing trap the guard just above exists for, but for a SERVICE
    # CODE name instead of a channel name.
    if _is_register_wide_listing(message):
        from backend.utils.helpers import find_service_codes as _cm_svc_find_codes
        # `find_service_codes` catches a numeric code or an official service
        # name ("ULC Land Subdivision"); `_MODULE_CONCEPT_RE` catches the
        # informal module names officers actually type ("TSLR", "F-Line") --
        # neither alone covers both "list TSLR applications" and "show 0164
        # applications", so both are checked.
        if _cm_svc_find_codes(message) or _MODULE_CONCEPT_RE.search(message or ""):
            return False
    # A bare channel fragment ("from sro", "sro", "csc", "what are
    # application from sro") with no application number the OFFICER has named
    # -- not in this message, not in a recent turn of their own -- has nothing
    # for a per-application field question to resolve against, so the far more
    # likely reading is a filter on whatever list is already on screen.
    # Without this, `appinfo_qa.claims()` read "sro" as the SRO-name FIELD and
    # demanded an application number for a question that never named one,
    # right after the officer had just been shown a 50-row listing.
    #
    # This used to scan EVERY recent message, assistant replies included --
    # which defeated it in exactly the case it exists for: a listing answer
    # (the field-visit plan, a channel/ward listing, ...) names a dozen
    # application numbers in its own text, so right after being shown one, the
    # guard saw "an application number in recent history" and stood aside,
    # letting appinfo_qa's field claim through anyway. _app_numbers_in_recent_
    # context() reads only the OFFICER's own turns, which is what "has the
    # officer named one" actually means.
    # The word cap was 4, which protected "show applications from sro" but not
    # "what are application from sro" (5 words, the natural phrasing of the
    # same request) -- raised, since the no-application-number condition
    # above already does the real work of telling a channel scope apart from
    # a genuine per-file question.
    if (not extract_application_number(message) and extract_submission_channels(message) and (
            _qualifier_guard.listing_shaped(message)
            or (not _app_numbers_in_recent_context(chat_history, depth=6)[0] and len((message or "").split()) <= 10))):
        # A listing-shaped message settles it regardless of word count or what the
        # officer named earlier -- same reasoning as the `_early_subdivision_answer_
        # inner` copy of this guard, and the same `listing_shaped` escape fixes the
        # same gap: a prior "<app> details" turn used to make this stand aside (an
        # application WAS in recent context) and let the field claim through anyway.
        return False
    # "order columns as district, taluk, ward, block" -- a request to
    # re-render the list on screen with different columns, not a question
    # about any one application. `fulltransfer_qa.claims_strong()` read
    # "district" as a geography field of a single record and demanded an
    # application number for a question that never named one, the same
    # fragment-hijack the channel check above exists to stop.
    if fctx.is_column_reorder_fragment(message):
        return False

    # A code whose OFFICIAL NAME contains record-module vocabulary -- "TSLR
    # Owner Name Correction" (0165) carries both "owner" and "correction" --
    # made `cmflow_qa.parse()` / the owner-record parsers claim a bare listing
    # request for that code ("tslr owner name correction applications
    # please", "can you list everything under TSLR Settlement - Owner
    # Entry"). With no application number anywhere, and nothing but the
    # code's own name plus an ordinary listing word in the message, this is a
    # request for that code's queue, not a question about one record's owner
    # or correction-flow recommendation -- stand aside and let parse_intent's
    # all_service_code_applications path (which already runs the same
    # jurisdiction/listing logic "applications in ward N" uses) answer it.
    if not extract_application_number(message) and not any(
        extract_application_number(m.get("content") if isinstance(m, dict) else str(m)) for m in (chat_history or [])[-6:]
    ):
        from backend.utils.helpers import find_service_codes as _cm_find_codes
        if _cm_find_codes(message) and re.search(
            # The listing words from before, plus field-visit-requirement
            # wording -- "does TSLR Owner Name Correction need a field
            # visit" hit this same "owner" trap a second way: not a listing,
            # but still a code-level question the code's own name should not
            # be read as a record's owner for.
            r"\bapplications?\b|\bapps?\b|\bqueue\b|\bworkload\b|\bhow\s+many\b|\btotal\b|\blist\b|\bshow\b|\beverything\s+under\b"
            r"|\bneed\w*\b|\brequir\w*\b|\bnecessary\b|\bcompulsory\b|\bmandatory\b|\bfield\s*visit\b|\bsite\s*visit\b"
            r"|\bthevai\w*\b|\bvenum\w*\b|\bpannanuma\b|தேவை|கட்டாயம்",
            message or "", re.IGNORECASE,
        ):
            return False
    if (_na_parse(message, chat_history) or _um_parse(message, chat_history) or _tslr_parse(message, chat_history) or _sd_parse(message, chat_history) or _fulltransfer_qa.claims_strong(message) or _owner_ctx_compare(message, chat_history) or _newowner_qa.claims_strong(message) or _igrs_owner_qa.claims_strong(message) or _crosscheck_qa.claims(message) or _appinfo_qa.claims(message)
            or _fline_qa.claims(message)):
        return True
    _fa = extract_application_number(message)
    if not _fa:
        for m in reversed((chat_history or [])[-6:]):
            mm = _cmflow_qa._APP_NO.search((m.get("content") if isinstance(m, dict) else str(m)) or "")
            if mm:
                _fa = mm.group(0)
                break
    if _fa and _fa.count("/") >= 3 and _fa.split("/")[1] == "0178" and _fline_qa.claims_for_fline_app(message):
        return True
    # "tahsildar signature date" names the TAHSILDAR desk, so `q.desks` is
    # never the bare ["sis"] the check below exempts -- it claimed an ISD /
    # NISD file's patta-transfer signature DATE as a correction/modification
    # RECOMMENDATION question instead, and answered "no correction /
    # modification workflow record is held" for a file that was never going
    # to have one (that table only ever covers 0158-0169). The signature
    # date is a real field on 0153/0154's own patta_transfers record; let the
    # generic field map (which now knows "signature date") answer it there.
    if re.search(r"\bsign\w*\s+date\b|\bdate\s+of\s+sign\w*\b", message or "", re.IGNORECASE):
        _sig_app = extract_application_number(message)
        if not _sig_app:
            for m in reversed((chat_history or [])[-6:]):
                mm = _cmflow_qa._APP_NO.search((m.get("content") if isinstance(m, dict) else str(m)) or "")
                if mm:
                    _sig_app = mm.group(0)
                    break
        if _sig_app and _sig_app.count("/") >= 3 and _sig_app.split("/")[1] in ("0153", "0154"):
            return False
    q = _cmflow_qa.parse(message)
    if not q:
        return False
    if not (q.desks == ["sis"] and not q.mode and not q.extra):
        return True
    app = extract_application_number(message)
    if not app:
        for m in reversed(chat_history or []):
            mm = _cmflow_qa._APP_NO.search((m.get("content") if isinstance(m, dict) else str(m)) or "")
            if mm:
                app = mm.group(0)
                break
    return bool(app) and app.count("/") >= 3 and app.split("/")[1] not in ("0153", "0154")
