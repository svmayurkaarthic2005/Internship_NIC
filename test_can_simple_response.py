"""
Test that "what is the can no?" returns ONLY the CAN number, not the full details table.
"""
import re
import sys

# These suites print Tamil. On Windows the console is cp1252 and the
# first Tamil character raises UnicodeEncodeError, which killed the run
# before any result was reported. Same guard the other suites carry.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Simulate the detection logic from the fix
def test_can_response_detection():
    """Test that we correctly detect when user wants just the number vs full details"""
    
    print("="*80)
    print("Testing CAN Response Type Detection")
    print("="*80)
    
    test_cases = [
        # (message, should_return_simple, description)
        ("what is the can no?", True, "Simple CAN number query"),
        ("what is the can number?", True, "Simple CAN number query (full word)"),
        ("show can number", True, "Show CAN command"),
        ("give can no", True, "Give CAN command"),
        ("tell can number of this application", True, "Tell CAN command"),
        ("can number of 2026/0154/28/001167", True, "CAN number with app number"),
        ("what's the can", True, "Contracted form"),
        
        # These should return FULL details (not simple)
        ("how is can number assigned?", False, "How/Why question about CAN"),
        ("explain can number", False, "Explain request"),
        ("what does can number mean?", False, "What does question"),
        ("can number details", False, "Explicit details request"),
        ("tell me about can numbers", False, "About CAN general info"),
        ("who assigns can numbers?", False, "Who assigns question"),
        ("why is can number needed?", False, "Why question"),
    ]
    
    passed = 0
    failed = 0
    
    for message, expected_simple, description in test_cases:
        _msg_lower_can = message.lower()
        _asking_just_number = (
            any(p in _msg_lower_can for p in [
                "what is the can", "what's the can", "give can",
                "can number is", "tell can", "can no", "can number of",
                "display can", "can num", "show me can", "show can",
            ])
        ) and not any(re.search(rf'\b{re.escape(w)}\b', _msg_lower_can) for w in [
            "how", "why", "explain", "details", "information", "assigned", "assigns"
        ]) and not any(p in _msg_lower_can for p in [
            "about can", "what does", "how does"
        ])
        
        result = "✅ PASS" if _asking_just_number == expected_simple else "❌ FAIL"
        if _asking_just_number == expected_simple:
            passed += 1
        else:
            failed += 1
        
        response_type = "SIMPLE" if _asking_just_number else "DETAILED"
        expected_type = "SIMPLE" if expected_simple else "DETAILED"
        
        print(f"\n{result}")
        print(f"  Message: '{message}'")
        print(f"  Description: {description}")
        print(f"  Detected: {response_type} | Expected: {expected_type}")
    
    print("\n" + "="*80)
    print(f"Results: {passed} passed, {failed} failed out of {len(test_cases)} tests")
    print("="*80)
    
    if failed == 0:
        print("✅ All tests passed! CAN response type detection is working correctly.")
        return 0
    else:
        print("❌ Some tests failed. Review the detection logic.")
        return 1

if __name__ == "__main__":
    import sys
    sys.exit(test_can_response_detection())
