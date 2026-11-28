"""
FMB (Field Measurement Book) regression: CLAUDE.md documents that FMB does
not exist anywhere in this department's data and was deliberately purged
from the corpus and prompts. These tests check the actual GENERATED prompt
text (not just that the source file lacks the string), since a template
elsewhere could still reintroduce it at runtime.
"""
import re

from backend.services.rag_llm import build_grounded_rag_prompt

_FMB_RE = re.compile(r"\bFMB\b", re.IGNORECASE)

_PASSAGES = [{
    "citation": "workflow_guide.txt",
    "metadata": {},
    "content": "ISD (0154) requires a field inspection; NISD (0153) does not.",
}]


def test_tamil_prompt_has_no_fmb_reference():
    prompt = build_grounded_rag_prompt("isd vs nisd", _PASSAGES, language="ta")
    assert not _FMB_RE.search(prompt), prompt


def test_tanglish_prompt_has_no_fmb_reference():
    prompt = build_grounded_rag_prompt("isd vs nisd", _PASSAGES, language="tanglish")
    assert not _FMB_RE.search(prompt), prompt


def test_english_prompt_has_no_fmb_reference():
    prompt = build_grounded_rag_prompt("isd vs nisd", _PASSAGES, language="en")
    assert not _FMB_RE.search(prompt), prompt


def test_tamil_prompt_still_names_valid_identifiers():
    # The fix must remove FMB only -- ISD/NISD/the service codes are real,
    # documented identifiers and must still be named as ones to keep in
    # Latin script.
    prompt = build_grounded_rag_prompt("isd vs nisd", _PASSAGES, language="ta")
    for identifier in ("ISD", "NISD", "0154", "0153"):
        assert identifier in prompt, f"{identifier!r} missing from {prompt!r}"


def test_tamil_prompt_still_instructs_tamil_script():
    prompt = build_grounded_rag_prompt("isd vs nisd", _PASSAGES, language="ta")
    assert "Tamil script" in prompt
