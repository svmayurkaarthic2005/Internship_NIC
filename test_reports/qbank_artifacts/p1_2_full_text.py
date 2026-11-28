"""Fetch the FULL (untruncated) answer text for one turn per scenario, to
quote the actual LLM-written Explanation / What-cannot-be-concluded
sections in the remediation report."""
import os
from p1_2_verify_live import login, new_session, ask

QUESTIONS = [
    ("A", "This is an NISD application, which normally does not require a field visit. "
          "However, I can see a field_visit_dt in one workflow row. Does that mean an "
          "inspection was mandatory or actually completed? Application 2026/0153/02/045470."),
    ("B", "Application 2026/0158/02/001804. The register shows this application is still "
          "open, but the Tahsildar has recorded Approved. Does that mean the complete "
          "application is closed? Explain using the register, correction-modification "
          "record and latest workflow transition."),
    ("C", "Application 2026/0153/02/045470. What exactly was missing? The missing_docs "
          "field appears blank, but the rejection remark says linked documents were not "
          "submitted. Explain the difference."),
    ("D", "Application 2026/0153/02/045472. The old-owner and IGRS-owner tables have "
          "different structures. Why should we not automatically treat them as identical? "
          "Explain what each table actually records."),
]

if __name__ == "__main__":
    token = login()
    for label, q in QUESTIONS:
        sid = new_session(token)
        text, dt = ask(token, sid, q)
        print(f"===== {label} ({dt:.1f}s) =====")
        print(text)
        print()
