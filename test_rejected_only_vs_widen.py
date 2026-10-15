"""Regression test: "show rejected applications" / "show rejected one" (the
register's own hint text after a count that hides rejected files) must NARROW
to only the rejected rows, not widen to every status.

Bug: `_REJECTED_TOO_RE` had one alternative for imperative phrasings ("show
rejected applications", "ask for rejected applications") that matched the
exact hint text `_render_all_service_code_answer()` prints ("ask for rejected
applications to see them"), but `_resolve_rejected_widen()` treated every
match -- including that one -- as a WIDEN request ("add rejected back to
whatever is on screen"). So after "How many service code 0158 applications do
I have?" (20 non-rejected, 2 rejected hidden), "show rejected one" answered
with all 22 applications instead of the 2 actually rejected ones.

Fix: split into `_REJECTED_TOO_RE` (widen cues: "too"/"include"/"also"/
"with") and `_REJECTED_ONLY_RE` (bare imperative = narrow); `_resolve_
rejected_widen` rewrites to "rejected applications" (narrow) instead of
"all pending approved rejected applications" (widen) when only the latter
matches.

Run: python test_rejected_only_vs_widen.py
"""
import sys

sys.path.insert(0, r"C:\proj\nic_internship")
from backend.services.chatbot.chat_smalltalk import _resolve_rejected_widen


class _Ctx:
    def __init__(self, filters):
        self.filters = filters
        self.intent = "all_service_code_applications"


def test_narrow_phrasings_narrow():
    ctx = _Ctx({"service_code": "0158"})
    for msg in ["show rejected one", "show rejected ones", "show rejected applications",
                "ask for rejected applications", "list rejected applications",
                "display rejected applications", "give rejected applications"]:
        out = _resolve_rejected_widen(msg, [], ctx)
        assert out is not None, msg
        assert out.startswith("rejected applications"), (msg, out)
        assert "all pending approved rejected" not in out, (msg, out)


def test_widen_phrasings_still_widen():
    ctx = _Ctx({"service_code": "0158"})
    for msg in ["include rejected too", "rejected applications too", "also show rejected",
                "with rejected ones"]:
        out = _resolve_rejected_widen(msg, [], ctx)
        assert out is not None, msg
        assert out.startswith("all pending approved rejected applications"), (msg, out)


def test_self_contained_request_not_rewritten():
    ctx = _Ctx({"service_code": "0158"})
    assert _resolve_rejected_widen("Show rejected applications in ward 005.", [], ctx) is None


def test_non_rejected_message_untouched():
    assert _resolve_rejected_widen("show my pending applications", [], None) is None


if __name__ == "__main__":
    test_narrow_phrasings_narrow()
    test_widen_phrasings_still_widen()
    test_self_contained_request_not_rewritten()
    test_non_rejected_message_untouched()
    print("4/4 checks passed")
