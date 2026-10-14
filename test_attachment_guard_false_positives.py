"""Regression for Phase 2.1 of the chatbot defect audit: generic text-only
guards (`_unknown_message_kind`=="vague_all", `_is_mutation_request`,
`_is_capability_question`) intercepted genuine attachment questions before
`attachment_qa.plan_answer()` ever got a chance to decide from real attachment
state, and two of the three root causes were themselves ordinary unanchored
substring collisions.

No DB, no LLM.
"""
import sys

sys.path.insert(0, r"C:\proj\nic_internship")
from backend.services.chatbot import chat_followups as cf
from backend.services.chatbot import chat_smalltalk as cs

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


# These must reach attachment_qa.plan_answer() -- i.e. NOT be blocked here.
ATTACHMENT_SHAPED = [
    "Show me all details.",
    "Give me all the information.",
    "What is this file about?",
    "Remove rejected rows.",
    "Show only pending rows.",
]

# These must still be blocked -- genuinely not about a file, or a genuine
# register mutation / capability / out-of-scope question.
STILL_BLOCKED = [
    "clear",
    "delete all my applications",
    "what can you do",
    "asdfgh qwerty jklm",
    "help",
    "approve 2026/0153/28/001854",
    "what is the weather today",
    "Change the subdivision status to approved",
    "what is this",            # the bare, whole-message identity question
    "who is this",
]


def test_attachment_shaped_messages_reach_the_planner():
    for m in ATTACHMENT_SHAPED:
        check(f"{m!r} is NOT blocked before the attachment planner",
              not cf._attachment_turn_is_not_about_a_file(m))


def test_genuinely_unrelated_messages_still_blocked():
    for m in STILL_BLOCKED:
        check(f"{m!r} is still blocked", cf._attachment_turn_is_not_about_a_file(m))


def test_capability_cue_is_anchored_not_substring():
    # The exact bug: "what is this" as a bare message is a capability
    # question; "what is this FILE ABOUT" / "what is this APPLICATION'S
    # status" must not be, now that the unanchored duplicate is gone.
    check("'what is this' (bare) is a capability question", cs._is_capability_question("what is this"))
    check("'what is this file about?' is NOT a capability question",
          not cs._is_capability_question("What is this file about?"))
    check("\"what is this application's status\" is NOT a capability question",
          not cs._is_capability_question("what is this application's status"))


def test_rows_is_not_a_mutation_object_columns_still_handled():
    check("'Remove rejected rows.' is not a mutation request", not cs._is_mutation_request("Remove rejected rows."))
    check("'Show only pending rows.' is not a mutation request", not cs._is_mutation_request("Show only pending rows."))
    # Unaffected real mutation requests over actual register objects:
    check("'delete this application' is still a mutation request", cs._is_mutation_request("delete this application"))
    check("'Change the subdivision status to approved' is still a mutation request",
          cs._is_mutation_request("Change the subdivision status to approved"))
    check("'remove the ward column' is still not a mutation request (pre-existing)",
          not cs._is_mutation_request("remove the ward column"))


if __name__ == "__main__":
    test_attachment_shaped_messages_reach_the_planner()
    test_genuinely_unrelated_messages_still_blocked()
    test_capability_cue_is_anchored_not_substring()
    test_rows_is_not_a_mutation_object_columns_still_handled()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
