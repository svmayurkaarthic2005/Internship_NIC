"""Regression for Phase 3.1 of the chatbot defect audit: `_workflow_steps_topic`'s
"what is the next step?" resolver concatenated the last 4 chat-history messages
into one blob and ranked by role specificity (DIS beats SD beats SIS) GLOBALLY,
with no regard for recency or negation.

`_GLOSSARY["sd"]`'s own text says "NISD files do not go to SD.", and
`_WORKFLOW_STEPS_TEXT["NISD"]`'s detailed answer says "...no SD sketch, no
DIS...". Either one sitting in the last 4 turns made "dis"/"sd" match as a
plain substring regardless of the "do not go to" / "no" immediately in front
of it, and because `dis` was checked first, a question about SD's role, asked
right after NISD's workflow, resolved to "AFTER:dis" -- a role NISD never
reaches at all.

No DB, no LLM.
"""
import sys

sys.path.insert(0, r"C:\proj\nic_internship")
from backend.services.chatbot import chat_smalltalk as cs

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


def test_nisd_then_sd_then_next_step_resolves_to_sd():
    hist = [
        {"role": "user", "content": "Explain the NISD workflow."},
        {"role": "assistant", "content": cs._workflow_steps_answer("NISD", is_tamil=False)},
        {"role": "user", "content": "What does SD mean?"},
        {"role": "assistant", "content": cs._GLOSSARY["sd"][0]},
    ]
    topic = cs._workflow_steps_topic("What is the next step?", hist)
    check("'what is the next step?' after NISD-workflow + SD-glossary resolves to AFTER:sd, not AFTER:dis",
          topic == "AFTER:sd", topic)


def test_isd_then_dis_then_next_step_resolves_to_dis():
    hist = [
        {"role": "user", "content": "Explain the ISD workflow."},
        {"role": "assistant", "content": cs._workflow_steps_answer("ISD", is_tamil=False)},
        {"role": "user", "content": "What does DIS mean?"},
        {"role": "assistant", "content": cs._GLOSSARY["dis"][0]},
    ]
    topic = cs._workflow_steps_topic("What happens after that?", hist)
    check("ISD workflow + DIS glossary + 'what happens after that?' resolves to AFTER:dis",
          topic == "AFTER:dis", topic)


def test_bare_nisd_workflow_answer_then_next_step_resolves_to_sis_not_dis():
    # No intervening SD/DIS question at all -- the NISD answer alone (which
    # mentions "no SD sketch, no DIS" in passing) must not make "what next?"
    # resolve past a stage NISD never reaches.
    hist = [
        {"role": "user", "content": "Explain the NISD workflow."},
        {"role": "assistant", "content": cs._workflow_steps_answer("NISD", is_tamil=False)},
    ]
    topic = cs._workflow_steps_topic("What is the next step?", hist)
    check("bare NISD workflow answer + 'next step?' does not jump to AFTER:dis",
          topic != "AFTER:dis", topic)


if __name__ == "__main__":
    test_nisd_then_sd_then_next_step_resolves_to_sd()
    test_isd_then_dis_then_next_step_resolves_to_dis()
    test_bare_nisd_workflow_answer_then_next_step_resolves_to_sis_not_dis()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
