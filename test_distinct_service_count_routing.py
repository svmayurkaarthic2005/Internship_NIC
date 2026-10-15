"""Regression test: "how many service code 0158 applications do I have?"
must NOT be misrouted into the "how many service codes have pending
applications" meta-counter.

Bug: `_DISTINCT_SERVICE_COUNT_RE` matches on the bare phrase "how many
service code" regardless of what follows, so a single-code application
count ("how many service code 0158 applications do I have?") was answered
by the meta-counter, which also hardcodes the word "pending" in its text
whenever no explicit status was named -- so a question that named no status
at all (and whose underlying query therefore returns every non-rejected
status, i.e. pending + approved) was answered "N service code(s) ... have
pending applications (20 in all)" when only 10 of those 20 were actually
pending. Fix: `_asks_distinct_service_count` stands aside whenever the
message names a specific service code.

Run: python test_distinct_service_count_routing.py
"""
import sys

sys.path.insert(0, r"C:\proj\nic_internship")
from backend.services.chatbot.chat_application_queries import _asks_distinct_service_count


def test_single_code_count_not_misrouted():
    assert _asks_distinct_service_count("How many service code 0158 applications do I have?") is False
    assert _asks_distinct_service_count("How many applications does service code 0158 have?") is False
    assert _asks_distinct_service_count("how many 0178 applications do I have") is False


def test_meta_question_still_routed():
    assert _asks_distinct_service_count("How many service codes currently have pending applications?") is True
    assert _asks_distinct_service_count("Number of services with pending applications?") is True
    assert _asks_distinct_service_count("எத்தனை சேவை குறியீடுகளில் நிலுவை விண்ணப்பங்கள் உள்ளன?") is True


if __name__ == "__main__":
    test_single_code_count_not_misrouted()
    test_meta_question_still_routed()
    print("3/3 checks passed")
