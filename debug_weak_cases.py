"""Debug the 3 WEAK cases — print all 4 hits and their full content."""
import os, sys
os.environ["ENVIRONMENT"] = "production"
sys.path.insert(0, ".")
from backend.services.pgvector_store import similarity_search

CASES = [
    ("owners / joint owner", "how are joint owners recorded on a survey number", ["owner", "joint", "share"]),
    ("area / extent", "in what unit is the parcel area recorded", ["area", "extent", "sq", "hectare", "measure"]),
    ("fee / challan", "what is the service charge for a patta transfer", ["fee", "charge", "challan", "rupee", "amount"]),
]

for label, q, expect in CASES:
    print(f"\n{'='*70}")
    print(f"CASE: {label}")
    print(f"Q: {q}")
    hits = similarity_search(q, n_results=6)
    for i, h in enumerate(hits):
        src = h.get("metadata", {}).get("source", "-")
        dist = h.get("distance", 99)
        body = h.get("content", "")[:500].replace("\n", " ")
        # check keyword hit
        matched = [w for w in expect if w in body.lower()]
        print(f"  [{i}] dist={dist:.3f} src={src} kw={matched or 'NONE'}")
        print(f"       {body[:300]}")
