"""Semantic-search field coverage: one conceptual question per queryable table field/concept ->
check the RAG retrieval surfaces a topically-relevant corpus chunk.

Runs through similarity_search (vector when Ollama is up, lexical fallback
when it is down) + get_rag_context. Prints the nearest source/section and
whether context came back non-empty."""
import os, sys, re
os.environ["ENVIRONMENT"] = "production"
sys.path.insert(0, "/mnt/c/proj/nic_internship")
from backend.services.pgvector_store import similarity_search
from backend.services.rag import get_rag_context

# (field / concept, question, substrings we'd expect in a relevant chunk's source or body)
CASES = [
    ("application_type ISD/NISD", "what is the difference between an ISD and a NISD application", ["isd", "sub-division", "nisd"]),
    ("service_code 0153/0154/0155", "what does service code 0154 mean", ["0154", "service code", "sub-division"]),
    ("submission_channel", "how is the submission channel CSC or sub-registrar decided", ["csc", "sub-registrar", "camp", "channel", "source_name"]),
    ("can_number", "what is a CAN number and how many digits does it have", ["can", "citizen", "digit"]),
    ("igrs_form6_number / SRO", "what does it mean when an application has no IGRS Form 6 number", ["igrs", "sro", "sub-registrar", "form 6"]),
    ("current_status lifecycle", "what are the possible application statuses", ["pending", "approved", "rejected", "status"]),
    ("current_stage / workflow", "which desks does an ISD application pass through", ["sis", "senior draughtsman", "tahsildar", "workflow"]),
    ("workflow role Tahsildar / DSC", "who applies the digital signature to approve a patta transfer", ["tahsildar", "dsc", "digital signature", "sign"]),
    ("field_visit", "when is a field inspection required for an application", ["field", "inspection", "visit", "sub-division"]),
    ("sub_division numbers", "what is a temporary sub-division number", ["sub-division", "subdivision", "temporary", "sketch"]),
    ("patta_transfer", "what is a patta transfer application", ["patta", "transfer", "mutation"]),
    ("survey_number patta", "what is a patta number", ["patta", "survey"]),
    ("owners / joint owner", "how are joint owners recorded on a survey number", ["owner", "joint", "share"]),
    ("area / extent", "in what unit is the parcel area recorded", ["area", "extent", "sq", "hectare", "measure"]),
    ("litigation flag", "how do I flag a survey number as under litigation", ["litigation", "court", "dispute"]),
    ("overdue / SLA", "when does an application become overdue", ["overdue", "sla", "deadline", "days", "time limit"]),
    ("survey application lock", "can two applications be active on the same survey number at once", ["active", "survey", "application", "one", "concurrent"]),
    ("district/taluk/ward/block codes", "what is a ward and a block in the survey hierarchy", ["ward", "block", "town", "district"]),
    ("fee / challan", "what is the service charge for a patta transfer", ["fee", "charge", "challan", "rupee", "amount"]),
    ("decision date", "when is an application considered decided", ["approved", "rejected", "order", "decision", "generated"]),
    ("MERGE application", "what is a merge application", ["merge", "combine", "sub-division"]),
    ("document checklist", "what documents must be uploaded with an application", ["document", "upload", "checklist", "sale deed"]),
]

FLOOR = None  # use configured
def norm(s): return re.sub(r"\s+", " ", (s or "").lower())

vec_mode = None
no_ground, weak = [], []
for label, q, expect in CASES:
    hits = similarity_search(q, n_results=4)
    if hits and vec_mode is None:
        vec_mode = "lexical" if hits[0].get("retrieval") == "lexical" else "vector"
    ctx = get_rag_context(q, "en")
    top = hits[0] if hits else None
    src = (top or {}).get("metadata", {}).get("source", "-")
    dist = f"{top['distance']:.3f}" if top else "  -  "
    # Check across ALL returned hits (not just top-1) — the LLM receives the
    # full context window so a relevant chunk at rank 1 or 2 is real grounding.
    all_hay = " ".join(
        norm(h.get("metadata", {}).get("source", "")) + " " + norm(h.get("content", ""))
        for h in hits
    )
    matched = [w for w in expect if w in all_hay]
    grounded = bool(hits) and bool(ctx)          # the search MECHANISM answered
    topical = grounded and len(matched) >= 1      # ...with an on-topic chunk
    if not grounded:
        no_ground.append(label)
    elif not topical:
        weak.append(label)
    tag = "ok  " if topical else ("WEAK" if grounded else "FAIL")
    print(f"{tag} [{label}]")
    print(f"      q: {q}")
    print(f"      -> {len(hits)} hits, nearest {dist} {src}  ctx={'EMPTY' if not ctx else str(len(ctx))+'c'}  expect-hit={matched or 'NONE'}")

print(f"\nretrieval mode: {vec_mode}")
print(f"{len(CASES)-len(no_ground)-len(weak)}/{len(CASES)} field questions retrieved an on-topic chunk")
if weak:
    print(f"WEAK topical match ({len(weak)}) — corpus has thin content, re-ingest after "
          f"editing backend/documents/*: {', '.join(weak)}")
if no_ground:
    print(f"NO grounding ({len(no_ground)}) — mechanism returned nothing for an in-domain "
          f"question: {', '.join(no_ground)}")

import sys as _sys
# The mechanism must ground every in-domain question; thin topical matches are a
# corpus-content note, not a search failure.
_sys.exit(1 if no_ground else 0)
