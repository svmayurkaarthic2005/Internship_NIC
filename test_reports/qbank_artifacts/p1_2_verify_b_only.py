"""Re-verify Scenario B's two-turn sequence after the chat-history
fallback fix for register-only follow-ups."""
from p1_2_verify_live import login, new_session, run_turn

if __name__ == "__main__":
    token = login()
    sid = new_session(token)
    run_turn(token, sid, "B1",
              "Application 2026/0158/02/001804. The register shows this application is still "
              "open, but the Tahsildar has recorded Approved. Does that mean the complete "
              "application is closed? Explain using the register, correction-modification "
              "record and latest workflow transition.")
    run_turn(token, sid, "B2", "What information is still missing before you can definitively say it is completed?")
