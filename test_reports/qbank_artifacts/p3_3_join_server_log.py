"""Join the per-turn server resolution trace (history_source / entity_context / entity_gate log lines)
onto the live results, in request order. Usage: python p3_3_join_server_log.py <server.log>"""
import json
import re
import sys

log = open(sys.argv[1], encoding="utf-8", errors="replace").read().splitlines()
turns, cur = [], None
for line in log:
    if "history_source=server" in line:
        cur = {"transcript": line.split("history_source=server", 1)[1].strip()}
        turns.append(cur)
    elif cur is not None and ("entity_context" in line or "entity_gate" in line):
        key = "context" if "entity_context" in line else "gate"
        cur.setdefault(key, re.sub(r"^.*?entity_(?:context|gate)\s+", "", line).strip())

path = "test_reports/qbank_artifacts/p3_3_entity_resolution_results.json"
res = json.load(open(path, encoding="utf-8"))
flat = [t for conv in res.values() for t in conv]
assert len(flat) == len(turns), f"{len(flat)} turns sent, {len(turns)} logged"
for t, s in zip(flat, turns):
    t["server"] = s
json.dump(res, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"joined {len(turns)} turns")
