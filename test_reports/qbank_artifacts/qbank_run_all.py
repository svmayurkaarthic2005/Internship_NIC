import sys, json, os, glob
sys.stdout.reconfigure(encoding="utf-8")
from qbank_harness import run_chain

chains_dir = os.path.join(os.path.dirname(__file__), "qbank_chains")
runs_dir = os.path.join(os.path.dirname(__file__), "qbank_runs")
os.makedirs(runs_dir, exist_ok=True)

files = sorted(glob.glob(os.path.join(chains_dir, "*.json")))
only = sys.argv[1:] if len(sys.argv) > 1 else None

for fp in files:
    with open(fp, "r", encoding="utf-8") as f:
        spec = json.load(f)
    cid = spec["chain_id"]
    if only and cid not in only:
        continue
    out_path = os.path.join(runs_dir, cid + ".json")
    print(f"\n=== RUNNING {cid} ===")
    try:
        run_chain(cid, spec["questions"], out_path)
    except Exception as e:
        print(f"CHAIN {cid} FAILED TO RUN: {e}")
