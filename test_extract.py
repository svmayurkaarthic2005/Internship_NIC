import asyncio
from backend.services.rag import extract_date_range

queries = [
    "what are the all applicstions between june n julty",
    "what are the applications between monday and tuesday",
    "applications on last monday",
    "applications on mondy in june 2026",
    "applications between monday n wednesday"
]

for q in queries:
    res = extract_date_range(q)
    print(f"'{q}' -> {res}")
