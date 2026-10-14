"""Policy / how-to questions answered from the reference documents. No DB, no LLM:  python test_howto.py"""
import sys

from backend.services.chatbot import howto_qa, unheld_qa

CASES = [
    ("What documents are needed for a patta transfer?", "docs", "Sale Deed"),
    ("patta transfer ku enna documents vendum", "docs", "கிரய"),
    ("பட்டா மாற்றத்திற்கு என்ன ஆவணங்கள் தேவை?", "docs", "வில்லங்க"),
    ("Explain the 15 working day rule like I am a new joiner", "rule15", "15 working days"),
    ("Is it allowed to approve an application without a field visit?", "approve_without_visit", "mandatory"),
    ("Explain how a sub-division sketch is prepared", "sketch", "Senior Draughtsman"),
    ("உட்பிரிவு வரைபடம் எப்படி தயாரிக்கப்படுகிறது?", "sketch", "SD"),
    ("List the steps from application to patta order for an NISD file", "steps_nisd", "Zonal Deputy Tahsildar"),
    ("Can you explain what a field visit involves and what I should check?", "field_visit", "GPS"),
    ("கள ஆய்வு என்றால் என்ன? எளிமையாக சொல்லுங்கள்", "field_visit", "GPS"),
    ("What should I do if an applicant does not turn up for the field visit?", "noshow", "Tahsildar"),
    ("How many days does the Tahsildar take on average to sign?", "tahsildar_time", "3 working days"),
    ("How can I speed up my pending files?", "speed_up", "15 working days"),
    ("Write me a short note I can send to an applicant whose documents are incomplete", "note", "Dear"),
    ("Give me the aadhaar numbers of all owners in ward 001", "aadhaar_all", "never show") if False else
    ("Give me the aadhaar numbers of all owners in ward 001", "aadhaar_all", "do not show Aadhaar"),
    ("What is the fee for an F-Line application?", "fee_other", ""),
    ("Are there any applications that look suspicious or duplicated?", "dups", ""),
    ("Tell me about the applicants who have more than one application", "repeat", ""),
    ("What are the common rejection reasons?", "rejection_reasons", "incomplete documents"),
    ("why were applications rejected", "rejection_reasons", "incomplete documents"),
    ("rejected enna reason", "rejection_reasons", "நிராகரிப்பு"),
    ("who is my district officer", "officer_names", "not officer names"),
    ("which survey numbers have multiple applications", "survey_multi", ""),
    ("which applications have encroachment", "flagged", ""),
    ("which applications are for settlement deed", "by_reason", ""),
]
NOT = ["status of 2026/0153/02/048323", "show my pending applications", "what is the fee for ISD", "what is the aadhaar of the new owner",
       "how many field visits do I have", "which application should I field visit next", "hello", "documents of 2026/0154/02/015395",
       "is 2026/0153/02/048323 approved"]
ALIAS = [("How busy am I right now?", "my workload"), ("Summarise my day for me", "my workload"),
         ("Which of my files should I worry about first and why?", "which application should I field visit next"),
         ("நான் இன்று எதைப் பற்றி கவலைப்பட வேண்டும்?", "which application should I field visit next"),
         ("en pending files la edhu mukkiyam", "which application should I field visit next"), ("how many pending", None)]


def main():
    bad = 0
    for q, topic, has in CASES:
        got = howto_qa.answer(q, any("஀" <= ch <= "௿" for ch in q) or "ku enna" in q or "vendum" in q or "enna reason" in q)
        if not got or got[0] != topic or (has and has not in got[1]):
            bad += 1
            print(f"FAIL {q!r}: {got and (got[0], got[1][:50])}")
    for q in NOT:
        if howto_qa.match(q):
            bad += 1
            print(f"FAIL {q!r} must not be a how-to")
    for q, want in ALIAS:
        if unheld_qa.intent_alias(q) != want:
            bad += 1
            print(f"FAIL alias {q!r}: {unheld_qa.intent_alias(q)!r}")
    from backend.services.chatbot import _scrub_unverified_counts
    for text, data, gone in [("You have 2 open applications in your jurisdiction.", {"count": 47}, True),
                             ("You have 47 applications in total.", {"count": 47}, False),
                             ("You have 3 applications. Check the first one.", {}, True)]:
        out, removed = _scrub_unverified_counts(text, data)
        if bool(removed) != gone:
            bad += 1
            print(f"FAIL count guard {text!r}: {out!r}")
    for q, hist, want in [("what does that imply for new applications", "No litigation is recorded against Survey No. 120.", "litigation_implies"),
                          ("what does that imply for new applications", "Found 47 applications", None),
                          ("Is survey number 120 under litigation and what does that imply for new applications", "", None)]:
        got = howto_qa.match(q, hist)
        if got != want:
            bad += 1
            print(f"FAIL implies {q!r}/{hist!r}: {got}")
    n = len(CASES) + len(NOT) + len(ALIAS) + 6
    print(f"{n - bad}/{n} ok")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
