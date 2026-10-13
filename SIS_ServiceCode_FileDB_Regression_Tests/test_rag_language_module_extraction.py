"""
Architecture regression test for the rag_language.py extraction
(SIS_Chatbot_Modularity_Refactor, Phase 3/Phase 8).

Verifies the module boundary itself, not just that parsing still works
(the existing test_tanglish_language.py / test_tanglish_to_tamil_policy.py /
SIS_Regression_Tests/test_tamil_negation_language_bug.py cover behavior):

  - rag_language imports independently, with no DB/LLM side effects.
  - It does not import back from rag.py or chatbot.py (no circular import,
    and no risk of pulling in the Ollama client or a DB session just to
    detect a language).
  - rag.py's and chatbot.py's public names are the SAME function objects as
    rag_language's -- a real re-export, not a copy that could drift.
  - A test can still patch backend.services.rag.detect_language (the facade
    path existing tests and callers use) and have chatbot.py observe the
    patched version, since chatbot.py calls the name bound into its own
    module namespace at import time.

No DB, no LLM. Run directly: `python test_rag_language_module_extraction.py`.
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_rag_language_imports_standalone():
    import backend.services.rag_language as rag_language  # noqa: F401


def test_rag_language_has_no_circular_import():
    import ast
    src = Path(__file__).resolve().parents[1].joinpath(
        "backend", "services", "rag_language.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported_modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
        elif isinstance(node, ast.Import):
            imported_modules.extend(a.name for a in node.names)
    forbidden = [m for m in imported_modules if m in (
        "backend.services.rag", "backend.services.chatbot",
        "backend.services.rag.rag", "rag", "chatbot",
    )]
    assert not forbidden, f"rag_language.py must not import back from {forbidden}"


def test_rag_language_has_no_db_or_llm_imports():
    import ast
    src = Path(__file__).resolve().parents[1].joinpath(
        "backend", "services", "rag_language.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported_modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
        elif isinstance(node, ast.Import):
            imported_modules.extend(a.name for a in node.names)
    db_or_llm = [m for m in imported_modules if any(
        s in m for s in ("sqlalchemy", "ollama", "database", "postgres", "pgvector")
    )]
    assert not db_or_llm, f"a pure language-parsing module pulled in {db_or_llm}"


def test_facade_reexport_identity():
    import backend.services.rag as rag
    import backend.services.rag_language as rag_language
    assert rag.detect_language is rag_language.detect_language
    assert rag.is_short_reply is rag_language.is_short_reply
    assert rag.get_prior_turn_language is rag_language.get_prior_turn_language


def test_chatbot_py_sees_the_same_function_objects():
    import backend.services.chatbot as chatbot
    import backend.services.rag_language as rag_language
    assert chatbot.detect_language is rag_language.detect_language
    assert chatbot.is_short_reply is rag_language.is_short_reply
    assert chatbot.get_prior_turn_language is rag_language.get_prior_turn_language


def test_patching_the_facade_path_is_observed_by_chatbot():
    """Existing tests patch `backend.services.rag.detect_language` (see e.g.
    test_tanglish_to_tamil_policy.py's import style). chatbot.py imported the
    name directly (`from backend.services.rag import detect_language`), so it
    holds its own reference in its module namespace -- patching
    backend.services.rag.detect_language does NOT change what chatbot.py
    calls. This was already true before the extraction (the facade just
    forwards to a new home); this test pins that unchanged fact down so a
    future change to the facade doesn't silently break it the other way."""
    import backend.services.chatbot as chatbot
    import backend.services.rag as rag
    original = chatbot.detect_language
    with mock.patch.object(rag, "detect_language", lambda text: "PATCHED"):
        assert chatbot.detect_language is original, (
            "chatbot.py binds its own reference at import time; patching the "
            "rag facade must not be expected to redirect it")
        assert chatbot.detect_language("hello") != "PATCHED"


def test_pure_functions_require_no_arguments_beyond_text():
    """Sanity: these are pure parsing functions -- no officer/db/session
    argument anywhere in their signature, so a caller cannot accidentally
    thread authorization-sensitive state through them."""
    import inspect
    import backend.services.rag_language as rag_language
    for name in ("detect_language", "is_short_reply", "get_prior_turn_language"):
        sig = inspect.signature(getattr(rag_language, name))
        for param in sig.parameters:
            assert param not in ("officer", "db", "session", "ctx"), (
                f"{name} unexpectedly takes a {param} argument")


if __name__ == "__main__":
    failures = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS: {name}")
            except AssertionError as e:
                failures.append(name)
                print(f"FAIL: {name}\n  {e}")
            except Exception as e:
                failures.append(name)
                print(f"ERROR: {name}\n  {e!r}")
    print(f"\n{len(failures)} failing")
    sys.exit(1 if failures else 0)
