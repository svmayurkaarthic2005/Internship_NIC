"""
Comprehensive unknown/random question handling test — ~320 questions.

Categories tested (no Ollama, no DB):
  A. CLEARLY_OFF_TOPIC  — must be caught by _is_out_of_scope
  B. CAPABILITY         — must be caught by _is_capability_question
  C. DOMAIN_PASS        — must NOT be caught by either guard (real SIS work)
  D. EDGE_PASS          — tricky phrasing that LOOKS off-topic but contains
                          domain vocabulary; must NOT be blocked
  E. UNSEEN_DETERMINISTIC — unknown data → still routed to a deterministic
                            handler (not general_query / the LLM)

Run:
    python test_unknown_questions.py
    python test_unknown_questions.py --verbose   # print every case
"""
from __future__ import annotations
import sys, argparse
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services.rag import parse_intent
from backend.services.chatbot import _is_out_of_scope, _is_capability_question

# == A. CLEARLY OFF-TOPIC ====================================================
# Must be caught by _is_out_of_scope (and NOT by _is_capability_question).
OFF_TOPIC: list[str] = [
    # Weather & environment
    "what is the weather in Chennai today",
    "will it rain tomorrow in Madurai",
    "what is the temperature today",
    "tell me the weather forecast for this week",
    "how is the weather in Coimbatore",
    "is it going to rain in Tamil Nadu today",
    "what is the weather outside",
    "tell me today's weather report",
    "what is the humidity today",
    "should I carry an umbrella today",
    "weather update for Tuesday",
    "rain today in Thoothukudi",
    "forecast for next week",
    "will it be sunny tomorrow",
    "temperature today in Madurai",
    "is it cold today",
    # Sports
    "who won the cricket match yesterday",
    "what is the IPL score today",
    "who won the world cup",
    "cricket score live",
    "which team won today's match",
    "IPL 2024 winner",
    "who scored the most runs",
    "who won the match score",
    "who won yesterday's football game",
    "football match result today",
    "who won the last ipl season",
    "world cup cricket schedule",
    "ipl match today schedule",
    "what is the match score now",
    # Food & cooking
    "how do I cook biryani",
    "give me a recipe for idli",
    "how do i make chicken curry",
    "tell me how to cook sambar",
    "recipe for dosa batter",
    "how do i cook rice properly",
    "what is the recipe for payasam",
    "how to make mutton biryani",
    "restaurant near me",
    "best restaurant in Chennai",
    "restaurant near my office",
    "where can I eat good food nearby",
    # Jokes, poems, songs
    "tell me a joke",
    "write a joke for me",
    "tell a joke about engineers",
    "make me laugh",
    "write me a poem about the sea",
    "write a poem about rain",
    "compose a poem for me",
    "write me a story about a dog",
    "write a short story",
    "write an essay about climate change",
    "write me an essay",
    "sing a song",
    "sing me a lullaby",
    "write a song for me",
    "write me a song about love",
    "can you rap",
    # General knowledge / trivia
    "what is the capital of France",
    "capital of Germany",
    "what is the capital of Japan",
    "who is the prime minister of India",
    "who is the president of India",
    "president of the united states",
    "who is the prime minister of uk",
    "what year did india gain independence",
    "when did india gain independence",
    "independence day date",
    "india independence day",
    "when was independence",
    "who wrote the national anthem",
    "meaning of life",
    "what is the meaning of life",
    "who created the universe",
    "how old is the earth",
    "what is the population of india",
    # Science & math
    "what is 2 + 2",
    "what is 2+2",
    "what's 2+2",
    "what is the square root of 144",
    "explain quantum physics",
    "explain the theory of relativity",
    "what is e=mc2",
    "solve this equation x+2=5",
    "what is calculus",
    "explain machine learning",
    "what is artificial intelligence",
    "explain deep learning to me",
    # Finance & crypto
    "what is the bitcoin price",
    "bitcoin price today",
    "what is the stock price of reliance",
    "check sensex today",
    "nifty 50 today",
    "cryptocurrency prices",
    "what is ethereum",
    "buy bitcoin",
    "lottery number for today",
    "winning lottery numbers",
    # Health & personal
    "what is my horoscope for today",
    "what is my zodiac sign",
    "horoscope for virgo today",
    "zodiac predictions for scorpio",
    "give me relationship advice",
    "how do I lose weight",
    "best diet for weight loss",
    "what medicine should I take for fever",
    "recommend a doctor",
    # Entertainment & media
    "recommend a good movie",
    "suggest a movie to watch",
    "what is a good movie",
    "best bollywood movie of 2024",
    "recommend a book to read",
    "what book should I read",
    "tell me about the avengers movie",
    # Coding / tech tasks
    "write a python function to sort a list",
    "write code for hello world",
    "write me code in javascript",
    "debug this code for me",
    "write me a sql query to select all records",
    "how do I use git",
    "explain docker to me",
    # Communication drafting (non-SIS)
    "write me an email to my landlord",
    "draft an email asking for leave",
    "write an email to my boss",
    "help me write a resignation letter",
    "write a formal letter for bank",
    # Translation to non-Tamil languages
    "translate hello to french",
    "translate thank you to spanish",
    "how do you say good morning in german",
    "say cheers in german",
    "say goodbye in french",
    "translate this sentence to spanish",
    "what does bonjour mean",
    "how to say thank you in japanese",
    "what is hello in french",
    # Tamil off-topic
    "vananilai eppadi irukku",
    "vananilai epadi irukkirathu",
    "cricket poddi mudiv enna",
    "samayal seivadu eppadi",
    "joke sollu",
    "kavithai ezuthu",
    "padal paadu",
    "tiraippadam parinturai sei",
]

# == B. CAPABILITY QUESTIONS =================================================
CAPABILITY: list[str] = [
    "who are you",
    "what are you",
    "what can you do",
    "what do you do",
    "what can you help me with",
    "how can you help me",
    "what can i ask you",
    "what do you know",
    "what is your purpose",
    "what are your capabilities",
    "help me understand what you can do",
    "tell me what you do",
    "what kind of questions can i ask",
    "what topics do you cover",
    "neenga yaar",
    "neenga enna seyya mudiyum",
    "enna kettukkalaam",
]

# == C. DOMAIN PASS — must NOT be blocked ====================================
DOMAIN_PASS: list[str] = [
    # Application queries
    "what is a patta",
    "what is the SIS workflow",
    "how do I approve an application",
    "what is ISD",
    "what is the status of my application",
    "show my pending applications",
    "how many pending applications do I have",
    "list my active cases",
    "what is the current status of this file",
    "show all applications in my ward",
    "how many applications were approved this month",
    "which applications are in progress",
    "show rejected applications",
    "how many in_progress applications are there",
    "how many approved applications this year",
    "show me the escalated cases",
    "what is an escalated application",
    "how do applications get escalated",
    "which cases need immediate attention",
    "how many applications have I processed this week",
    # Survey number / ownership
    "who owns survey 5",
    "who is the owner of survey number 145",
    "what is the extent of survey 5",
    "show ownership details for survey 145",
    "are there joint owners on survey 145",
    "how many owners does this survey have",
    "what is the patta number for survey 67",
    "show patta details",
    "what is the land area of this parcel",
    "what is the sub-division extent",
    "who is the applicant for this file",
    "what is the applicant's name",
    "what is the applicant's address",
    # Field visit
    "when is my field visit scheduled",
    "how many field visits do I have this week",
    "what are the field visit requirements for ISD",
    "is there a field visit for this application",
    "show overdue field visits",
    "what applications are overdue for inspection",
    "list applications past the 15 day deadline",
    "how many days are left for the field visit deadline",
    "what is the deadline for this file",
    "who should approve a change to the field visit date",
    "can I reschedule the field visit",
    "what are the overdue applications",
    "show me all overdue cases",
    # Workflow / desk routing
    "which desk is this application on",
    "has this been forwarded to the tahsildar",
    "is the application with the draughtsman",
    "what is the current workflow stage",
    "has the tahsildar signed this application",
    "who applied the digital signature",
    "what is the DSC and who holds it",
    "what happens after the SIS forwards the file",
    "where is this application in the workflow",
    "how long has it been with the tahsildar",
    # Documents
    "what documents are needed for NISD",
    "what documents are required for ISD",
    "which documents are missing from this application",
    "is the sale deed registered",
    "is the encumbrance certificate valid",
    "what is the encumbrance certificate",
    "what is form 6 IGRS",
    "what does IGRS form 6 number mean",
    "what is an igrs number",
    "does this application have an IGRS number",
    "what is the CAN number for this application",
    "how many digits does a CAN number have",
    "is the sale deed from the sub-registrar",
    # ISD / NISD / MERGE specifics
    "what is an ISD application",
    "what is a NISD application",
    "what is the difference between ISD and NISD",
    "what does service code 0154 mean",
    "what does service code 0153 mean",
    "what is service code 0155",
    "what is a merge application",
    "can I merge these sub-divisions",
    "what is a temporary sub-division number",
    "how do temporary numbers become final",
    "what is the format of a temporary sub-division number",
    "how many sub-divisions are proposed in this application",
    "show the proposed sub-divisions",
    # Litigation / encroachment
    "is there litigation on survey 5",
    "is there a court case on this survey number",
    "how do I flag litigation on a survey",
    "is this survey under a stay order",
    "is there encroachment on this survey",
    "what should I do if I find encroachment",
    # Jurisdiction
    "what is my ward number",
    "which ward does this block belong to",
    "what is the taluk for this survey",
    "what is the district code for thoothukudi",
    "what is a ward and a block",
    "what is my jurisdiction",
    "show jurisdiction summary",
    "how many survey numbers are in my jurisdiction",
    # Status / history
    "when was this application approved",
    "when was 2026/0154/28/001280 approved",
    "when was the patta transfer completed",
    "show workflow history for this application",
    "what actions have been taken on this file",
    # Fee / SLA
    "what is the SLA for an ISD application",
    "what is the fee for NISD",
    "what is the government fee for ISD",
    "what is the service charge",
    "how many working days do I have to clear it",
    "what is the time limit for ISD",
    "what is the SLA for NISD",
    # Rules / knowledge
    "what is the meaning of the escalation flag",
    "what is a patta transfer",
    "what is a mutation application",
    "what is the submission channel",
    "how is the CSC channel determined",
    "was this submitted by a sub-registrar",
    "what is the sub-registrar office",
    "what is a sub-division",
    # Tanglish
    "application status enna",
    "pending applications kaattu",
    "field visit yeppo irukku",
    "overdue files ellam kaattu",
    "survey number owner yaar",
    "sis workflow explain pannunga",
]

# == D. EDGE PASS — tricky but should NOT be blocked =========================
EDGE_PASS: list[str] = [
    # "translate" but with domain vocab
    "translate the remarks on survey 5 to english",
    "translate the rejection reason to Tamil",
    "what does this remark on the application mean in English",
    # "who is the" — sounds like gen knowledge but has officer/SIS role
    "who is the sub registrar for this taluk",
    "who is the tahsildar for ward 102",
    # "what is the meaning of" — but a domain concept
    "what is the meaning of the escalation flag",
    "what is the meaning of workflow_state C",
    "what does in_progress mean in an application",
    # "how do I" — sounds like a how-to but it's SIS work
    "how do I approve an application",
    "how do i check for encroachment on a survey",
    "how do I flag a litigation case",
    "how do I schedule a field visit",
    "how do I upload documents",
    "how do I change the field visit date",
    # "tell me about" — sounds general but has domain noun
    "tell me about survey number 145",
    "tell me about this application",
    "tell me about the workflow for NISD",
    "tell me about the pending applications",
    "tell me about the sub-division sketch process",
    # "explain" — sounds lecture-like but is domain
    "explain the ISD workflow",
    "explain the sub-division numbering",
    "explain what a patta is",
    "explain NISD to me",
    "explain the escalation process",
    "explain the merge application",
    "explain the difference between ISD and NISD",
    # Application numbers (contain digits — must not confuse math filter)
    "what is the status of 2026/0154/28/001280",
    "check application 2025/0153/28/000123",
    "what happened to application 2024/0155/28/000055",
    # "show" commands
    "show my pending applications",
    "show applications in taluk 28",
    "show approved applications this week",
    "show me the field visit schedule",
    "show owners of survey 145",
    "show document checklist for this application",
    "show the workflow history",
    # "which" queries
    "which applications are overdue",
    "which files have missing documents",
    "which ward does survey 145 belong to",
    "which desk is this application at",
    "which cases do I have scheduled today",
    # "list" commands
    "list all pending applications",
    "list field visits for this week",
    "list applications in my jurisdiction",
    "list sub-divisions in survey 145",
    # "what should I do" — personal but SIS context
    "what should I do for this overdue application",
    "what should I check during a field visit",
    "what should I do if the sale deed is missing",
    "what should I do if I find encroachment",
    "what should I verify in an NISD application",
    # "can I" — might sound off-scope
    "can I reschedule this field visit",
    "can two applications be active on the same survey number",
    "can I approve this file without a field visit",
    "can I submit this without the encumbrance certificate",
    # Urgency / schedule phrasing (previously noted as borderline)
    "which files are behind schedule",
    "which of my applications have missed the deadline",
    "which applications are most urgent",
    "which pending cases need attention today",
    "are any of my applications escalated",
    "which cases have been pending the longest",
    "have any applications exceeded the SLA",
    "have I missed any deadlines",
    "has this application been approved",
    "has the tahsildar approved this",
    "have the documents been verified",
    # Other domain-forward phrasings
    "what are the required documents for ISD",
    "what does the sale deed verify",
    "what does joint owner mean in a patta",
    "does survey 145 have any litigation",
    "is survey 89 encroached",
    "does this application have all its documents",
]

# == E. UNSEEN DETERMINISTIC =================================================
# parse_intent must NOT return "general_query" for recognisable intent
# patterns pointing at data that doesn't exist.
UNSEEN_DETERMINISTIC: list[tuple[str, set[str]]] = [
    ("what is the status of application 2099/0153/28/999999",
     {"application_status"}),
    ("check application 2050/0154/99/000001",
     {"application_status"}),
    ("status of 2025/0155/28/123456",
     {"application_status"}),
    ("what happened to application 2030/0153/01/000999",
     {"application_status"}),
    ("show details of application 2100/0154/28/000001",
     {"application_status"}),
    ("is application 1999/0153/28/000001 still pending",
     {"application_status"}),
    ("what is the current status of 2026/0154/28/999999",
     {"application_status"}),
    ("tell me about survey number 88888",
     {"survey_detail"}),
    ("what is survey number 99999",
     {"survey_detail"}),
    ("show survey 77777 details",
     {"survey_detail"}),
    ("who owns survey 99999",
     {"survey_owners"}),
    ("who is the owner of survey 55555",
     {"survey_owners"}),
    ("show owners of survey 66666",
     {"survey_owners"}),
    ("are there joint owners on survey 77777",
     {"survey_owners"}),
    ("is there litigation on survey 654321",
     {"litigation_check"}),
    ("does survey 999999 have a court case",
     {"litigation_check"}),
    ("show me applications in ward 999",
     {"pending_applications"}),
    ("list pending applications in ward 1234",
     {"pending_applications"}),
    ("how many applications are pending in ward 99",
     {"pending_applications"}),
    ("what is the applicant name for 2050/0154/28/000001",
     {"application_status", "field_specific_query"}),
    ("what is the sale deed number for 2099/0153/28/999999",
     {"application_status", "field_specific_query"}),
    ("what is the CAN number for 2050/0154/28/000001",
     {"application_status", "field_specific_query", "can_number_info"}),
    ("who are the joint owners of application 2099/0154/28/000001",
     {"joint_owner_check", "application_status"}),
    ("is 2099/0153/28/000001 an ISD or NISD",
     {"is_nisd_or_isd", "application_status"}),
    ("is the sale deed registered for 2099/0154/28/000001",
     {"check_sale_deed", "application_status"}),
]

_DETERMINISTIC_OK = {
    "application_status", "survey_detail", "survey_owners", "litigation_check",
    "pending_applications", "field_visits", "check_documents", "can_number_info",
    "field_specific_query", "joint_owner_check", "is_nisd_or_isd",
    "check_sale_deed", "sale_deed_check", "survey_detail",
}
# general_query is NOT ok for unseen-data questions — exclude it from default
_DETERMINISTIC_OK_STRICT = _DETERMINISTIC_OK - {"general_query"}


def main(verbose: bool = False) -> int:
    failed = 0
    totals: dict[str, int] = {c: 0 for c in ("A_oos", "B_cap", "C_dom", "D_edge", "E_unseen")}
    fails:  dict[str, int] = {c: 0 for c in totals}

    def report(cat: str, label: str, ok: bool, q: str, extra: str = "") -> None:
        nonlocal failed
        totals[cat] += 1
        if not ok:
            fails[cat] += 1
            failed += 1
        if verbose or not ok:
            tag = "PASS" if ok else "FAIL"
            print(f"{tag}  {label:<22} | {q[:80]}{extra}")

    for q in OFF_TOPIC:
        ok = _is_out_of_scope(q) and not _is_capability_question(q)
        report("A_oos", "off-topic", ok, q)

    for q in CAPABILITY:
        ok = _is_capability_question(q)
        report("B_cap", "capability", ok, q)

    for q in DOMAIN_PASS:
        ok = not _is_out_of_scope(q) and not _is_capability_question(q)
        report("C_dom", "domain-pass", ok, q)

    for q in EDGE_PASS:
        ok = not _is_out_of_scope(q) and not _is_capability_question(q)
        report("D_edge", "edge-pass", ok, q)

    for q, allowed_intents in UNSEEN_DETERMINISTIC:
        intent = parse_intent(q)
        ok = (intent in (_DETERMINISTIC_OK_STRICT | allowed_intents)) and not _is_out_of_scope(q)
        report("E_unseen", f"unseen->{intent[:18]}", ok, q,
               f"  [got: {intent}]" if not ok else "")

    total = sum(totals.values())
    passed = total - failed
    print(f"\n{'='*60}")
    print(f"TOTAL: {passed}/{total} passed  ({failed} failure(s))")
    print(f"  A off-topic      : {totals['A_oos'] - fails['A_oos']:>3}/{totals['A_oos']}")
    print(f"  B capability     : {totals['B_cap'] - fails['B_cap']:>3}/{totals['B_cap']}")
    print(f"  C domain pass    : {totals['C_dom'] - fails['C_dom']:>3}/{totals['C_dom']}")
    print(f"  D edge pass      : {totals['D_edge'] - fails['D_edge']:>3}/{totals['D_edge']}")
    print(f"  E unseen-det     : {totals['E_unseen'] - fails['E_unseen']:>3}/{totals['E_unseen']}")
    if failed:
        print(f"\nFAILED — re-run with --verbose for details on each case")
    else:
        print("\nALL PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--verbose", "-v", action="store_true",
                   help="print every case, not just failures")
    args = p.parse_args()
    sys.exit(main(verbose=args.verbose))
