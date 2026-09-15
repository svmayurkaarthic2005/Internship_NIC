import sys
from backend.services.rag import parse_intent

def trace_calls(frame, event, arg):
    if event == 'return' and frame.f_code.co_name == 'parse_intent':
        print(f"Returned from line {frame.f_lineno} with {arg}")
    return trace_calls

sys.settrace(trace_calls)
print(parse_intent('what are the all applicstions between june n julty', None, None))
sys.settrace(None)
