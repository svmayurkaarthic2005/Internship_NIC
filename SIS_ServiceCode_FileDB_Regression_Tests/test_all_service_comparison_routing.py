"""
Regression test for SVCCOMPARE-ALL-005 (All-service comparison intent misrouting):
Tests the 11 catalog-wide questions specified in Phase 3 of the audit prompt.

Examines the routing of whole-catalogue comparison questions against rag.parse_intent().
Documents that no all-service comparison intent exists, causing questions to misroute
to all_service_code_applications, pending_applications, application_status, or general_query.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.rag import parse_intent


QUESTIONS_AND_ROUTING = [
    ("Compare all supported service codes.", "general_query"),
    ("Show the similarities across all service codes.", "all_service_code_applications"),
    ("Show the differences across all service codes.", "all_service_code_applications"),
    ("Group the service codes by common characteristics.", "general_query"),
    ("Which codes have the same documented requirements?", "general_query"),
    ("Which codes have different workflows?", "general_query"),
    ("Which codes share the same application fields?", "general_query"),
    ("Create a comparison matrix for every service code.", "all_service_code_applications"),
    ("List attributes that apply to all services.", "pending_applications"),
    ("Identify attributes unique to individual services.", "general_query"),
    ("Which service codes cannot be compared because documentation is missing?", "application_status"),
]


def test_all_service_comparison_intents():
    print("Testing All-Service Catalog Comparison Intent Routing (SVCCOMPARE-ALL-005 Fixed):")
    for query, _ in QUESTIONS_AND_ROUTING:
        intent = parse_intent(query)
        print(f"  Q: '{query}' -> parsed: '{intent}' (expected: all_service_code_comparison)")
        assert intent == "all_service_code_comparison", f"Expected all_service_code_comparison, got {intent}"
    print("ALL 11 ALL-SERVICE ROUTINGS RESOLVE TO all_service_code_comparison.")


if __name__ == "__main__":
    test_all_service_comparison_intents()
    print("ALL ALL-SERVICE COMPARISON ROUTING TESTS PASSED.")
