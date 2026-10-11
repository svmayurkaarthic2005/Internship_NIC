"""Regression tests for negative output-format follow-ups after a service-code answer."""
from types import SimpleNamespace

from backend.services import chatbot as c


def _context(code="0154", entity="service_code"):
    return SimpleNamespace(entity=entity, filters={"service_code": code})


def _check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"ok  {name}")


def test_negative_sentence_format_phrasing():
    positives = (
        "dont explain in sentence",
        "don't explain in a sentence",
        "do not explain in one sentence",
        "don't use sentence format",
        "never answer in a single sentence",
        "not in sentence",
    )
    for message in positives:
        _check(f"recognises negative format instruction: {message}",
               c._is_negative_sentence_format_request(message))

    negatives = (
        "explain in sentence",
        "explain briefly",
        "explain in detail",
        "I don't understand; explain ISD in a sentence.",
        "don't explain how ISD works",
        "what is ISD?",
    )
    for message in negatives:
        _check(f"does not misclassify: {message}",
               not c._is_negative_sentence_format_request(message))


def test_negative_sentence_format_uses_service_code_context():
    _check(
        "bare correction retains the service code already being discussed",
        c._negative_sentence_format_context_code("dont explain in sentence", _context("0154")) == "0154",
    )
    _check(
        "explicit ISD overrides a different carried code",
        c._negative_sentence_format_context_code("don't explain ISD in a sentence", _context("0153")) == "0154",
    )
    _check(
        "explicit NISD overrides a different carried code",
        c._negative_sentence_format_context_code("don't explain NISD in a sentence", _context("0154")) == "0153",
    )
    _check(
        "format correction does not attach to application context",
        c._negative_sentence_format_context_code("dont explain in sentence", _context("0154", "application")) is None,
    )
    _check(
        "positive sentence request does not trigger the reverse-format path",
        c._negative_sentence_format_context_code("explain in sentence", _context("0154")) is None,
    )


def test_detailed_service_code_answer_is_not_the_one_line_answer():
    short = c._service_code_lookup_answer("what is 0154?", False, brief=True)
    detailed = c._service_code_lookup_answer("explain in detail 0154", False, brief=False)
    _check("detailed ISD answer contains meaning, workflow and SLA", all(
        term in detailed for term in ("Meaning:", "Workflow:", "SLA:")
    ))
    _check("detailed answer has multiple points", detailed.count("\n") >= 5)
    _check("detailed answer differs from the one-line answer", detailed != short)


if __name__ == "__main__":
    test_negative_sentence_format_phrasing()
    test_negative_sentence_format_uses_service_code_context()
    test_detailed_service_code_answer_is_not_the_one_line_answer()
    print("All negative sentence-format regression checks passed.")
