"""Off-topic questions and 'what can you do', plus a check that unseen-data
questions still reach a deterministic handler (not the LLM).

`parse_intent` routes anything it does not recognise to `general_query` -> the
agent / RAG fallback, where llama3.1:8b answered weather questions and wrote
poems as if that were the job. `_is_out_of_scope` / `_is_capability_question`
turn those away with one line naming what the assistant is for -- narrowly, so
a real survey question phrased oddly is never blocked.

No Ollama, no database.

    python test_out_of_scope.py
"""
from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services.rag import parse_intent
from backend.services.chatbot import _is_out_of_scope, _is_capability_question

OFF_TOPIC = [
    "what is the weather in Chennai today",
    "write me a poem about the sea",
    "who won the cricket match yesterday",
    "what is 2 + 2",
    "tell me a joke",
    "what is the capital of France",
    "how do I cook biryani",
    "translate hello to French",
    "what year did India gain independence",
    "recommend a good movie",
    "who is the prime minister of India",
    "explain quantum physics",
    "sing a song",
    "what is the meaning of life",
    "help me write an email to my landlord",
    "what is my horoscope for today",
    "tell me the bitcoin price",
    "வானிலை எப்படி இருக்கிறது",
]

CAPABILITY = [
    "who are you",
    "what can you do",
    "what can you help me with",
    "what do you know",
    "what is your purpose",
    "நீங்கள் யார்",
]

# Must NOT be treated as off-topic or as a capability question -- real SIS work,
# some phrased to brush against a cue word.
DOMAIN = [
    "what is a patta",
    "what is the SIS workflow",
    "how do I approve an application",
    "what is ISD",
    "what is the status of my application",
    "show my pending applications",
    "who owns survey 5",
    "what is the soil type of survey 5",
    "how many field visits do I have",
    "what is the extent of survey 5",
    "is there litigation on survey 5",
    "what documents are needed for NISD",
    "when was 2026/0154/28/001280 approved",
    "translate the remarks on survey 5 to english",
    "who is the sub registrar for this taluk",
    "what is the SLA for an ISD application",
    "how many working days do I have to clear it",
    "what is the deadline for this file",
    "what is the meaning of the escalation flag",
]

# Unseen-data questions must still land on a DETERMINISTIC handler (which then
# reports "not found / not accessible") -- never on general_query/the LLM.
UNSEEN = [
    ("what is the status of application 2099/0153/28/999999", "application_status"),
    ("tell me about survey number 88888", "survey_detail"),
    ("who owns survey 99999", "survey_owners"),
    ("is there litigation on survey 654321", "litigation_check"),
    ("show me applications in ward 999", "pending_applications"),
    ("what is the applicant name for 2050/0154/28/000001", "application_status"),
]
_DETERMINISTIC_OK = {
    "application_status", "survey_detail", "survey_owners", "litigation_check",
    "pending_applications", "field_visits", "check_documents", "can_number_info",
}


def main() -> int:
    failed = 0

    for q in OFF_TOPIC:
        ok = _is_out_of_scope(q) and not _is_capability_question(q)
        print(f"{'PASS' if ok else 'FAIL'}  off-topic     | {q}")
        failed += not ok

    for q in CAPABILITY:
        ok = _is_capability_question(q)
        print(f"{'PASS' if ok else 'FAIL'}  capability     | {q}")
        failed += not ok

    for q in DOMAIN:
        ok = not _is_out_of_scope(q) and not _is_capability_question(q)
        print(f"{'PASS' if ok else 'FAIL'}  domain passes  | {q}")
        failed += not ok

    for q, want in UNSEEN:
        got = parse_intent(q)
        ok = got in _DETERMINISTIC_OK and not _is_out_of_scope(q)
        print(f"{'PASS' if ok else 'FAIL'}  unseen->{got:20s} | {q}")
        failed += not ok

    print(f"\n{'ALL PASSED' if not failed else str(failed) + ' FAILURE(S)'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
