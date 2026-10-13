import asyncio, sys
sys.stdout.reconfigure(encoding='utf-8')
from backend.services.rag import parse_intent

for q in [
    "what is the fee for 0153",
    "what is the fee for 0154",
    "what is the fee for ISD",
    "what is the fee for NISD",
    "what is the fee for 0178",
    "Show only the differences between 0153 and 0154 in a table",
    "Compare all supported service codes"
]:
    intent = parse_intent(q)
    print(f"'{q}' -> intent: {intent}")
