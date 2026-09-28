"""PDF-vs-register routing and appended-number handling. No DB, no LLM."""
from types import SimpleNamespace

from backend.services.attachment_qa import (
    drop_appended_numbers, is_refusal, targets_attachment, _maybe_expired_notice,
)

DOCS = [SimpleNamespace(filename="sis_field_visit_memo.pdf")]
NO = "2026/0154/28/001197"


def routes_to_file(msg, intent="general_query"):
    return targets_attachment(msg, intent, DOCS)


def test_explicit_document_wins_over_register_words():
    assert routes_to_file("in the memo, what should I carry to the field visit?", "fv_schedule")
    assert routes_to_file("what does the document say about field visit?", "fv_schedule")
    assert routes_to_file("according to the uploaded PDF, what is the fee?", "fee_summary")
    assert routes_to_file("what does page 2 say", "application_status")


def test_register_questions_are_not_hijacked():
    assert not routes_to_file("how many field visits are pending?", "fv_schedule")
    assert not routes_to_file("show my pending applications", "pending_applications")
    assert not routes_to_file(f"what is the fee of {NO}", "application_status")


def test_appended_number_is_dropped_but_typed_number_kept():
    assert drop_appended_numbers(f"what does the document say about sketch {NO}",
                                 "what does the document say about sketch") \
        == "what does the document say about sketch"
    typed = f"what does the memo say about {NO}"
    assert drop_appended_numbers(typed, typed) == typed


def test_refusal_detection():
    assert is_refusal("I could not find this in the uploaded document.")
    assert not is_refusal("The memo says the sketch is missing.")


def test_expired_notice_fires_only_on_document_references():
    assert _maybe_expired_notice("what does page 2 of the memo say", "en")
    assert _maybe_expired_notice("summarize sale.pdf", "en")
    assert _maybe_expired_notice("what does the uploaded file say", "en")
    assert _maybe_expired_notice("show my pending applications", "en") is None
    assert _maybe_expired_notice(f"status of {NO}", "en") is None


def test_field_visit_question_is_not_scoped_to_an_application_list():
    from backend.services import followup_context as fc
    apps = fc.FollowupContext(entity=fc.ENTITY_APPLICATION_LIST, application_numbers=[NO])
    assert not fc.resolve("which field visit is oldest?", apps).resolved


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
