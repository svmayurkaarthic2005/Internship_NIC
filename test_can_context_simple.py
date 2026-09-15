"""
Simple unit test to verify CAN number context extraction logic.

Tests the _extract_app_number_from_context function directly.
"""
import sys
sys.path.insert(0, 'c:\\proj\\nic_internship')

from backend.services.chatbot import _extract_app_number_from_context

# These suites print Tamil. On Windows the console is cp1252 and the
# first Tamil character raises UnicodeEncodeError, which killed the run
# before any result was reported. Same guard the other suites carry.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def test_can_context_extraction():
    """Test that application numbers are extracted from chat history"""
    print("="*80)
    print("Testing CAN Number Context Extraction Logic")
    print("="*80)
    
    # Simulate chat history
    chat_history = [
        {
            "role": "user",
            "content": "What is the status of application 2026/0154/28/001167?"
        },
        {
            "role": "assistant",
            "content": "Application 2026/0154/28/001167 is currently approved."
        }
    ]
    
    # Test 1: Explicit reference ("previous question")
    message1 = "what is the can number of the previous question?"
    result1 = _extract_app_number_from_context(
        message1, 
        chat_history, 
        allow_implicit_continuation=True
    )
    
    print(f"\nTest 1: Explicit reference")
    print(f"  Message: '{message1}'")
    print(f"  Extracted: {result1}")
    print(f"  Expected: 2026/0154/28/001167")
    print(f"  Status: {'✅ PASS' if result1 == '2026/0154/28/001167' else '❌ FAIL'}")
    
    # Test 2: Implicit continuation ("what is the can number")
    message2 = "what is the can number?"
    result2 = _extract_app_number_from_context(
        message2, 
        chat_history, 
        allow_implicit_continuation=True
    )
    
    print(f"\nTest 2: Implicit continuation")
    print(f"  Message: '{message2}'")
    print(f"  Extracted: {result2}")
    print(f"  Expected: 2026/0154/28/001167")
    print(f"  Status: {'✅ PASS' if result2 == '2026/0154/28/001167' else '❌ FAIL'}")
    
    # Test 3: With "this application" reference
    message3 = "what is the can number of this application?"
    result3 = _extract_app_number_from_context(
        message3, 
        chat_history, 
        allow_implicit_continuation=True
    )
    
    print(f"\nTest 3: 'This application' reference")
    print(f"  Message: '{message3}'")
    print(f"  Extracted: {result3}")
    print(f"  Expected: 2026/0154/28/001167")
    print(f"  Status: {'✅ PASS' if result3 == '2026/0154/28/001167' else '❌ FAIL'}")
    
    # Test 4: No history (should return None)
    message4 = "what is the can number?"
    result4 = _extract_app_number_from_context(
        message4, 
        [], 
        allow_implicit_continuation=True
    )
    
    print(f"\nTest 4: No history")
    print(f"  Message: '{message4}'")
    print(f"  Extracted: {result4}")
    print(f"  Expected: None")
    print(f"  Status: {'✅ PASS' if result4 is None else '❌ FAIL'}")
    
    print("\n" + "="*80)
    all_pass = (
        result1 == '2026/0154/28/001167' and
        result2 == '2026/0154/28/001167' and
        result3 == '2026/0154/28/001167' and
        result4 is None
    )
    
    if all_pass:
        print("✅ ALL TESTS PASSED - CAN context extraction is working correctly!")
        return 0
    else:
        print("❌ SOME TESTS FAILED - Review the logic")
        return 1

if __name__ == "__main__":
    sys.exit(test_can_context_extraction())
