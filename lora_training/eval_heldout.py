"""Score a fine-tuned model on the held-out test sets (never trained on).

    python lora_training/eval_heldout.py --model sis-assistant:latest
    python lora_training/eval_heldout.py --model sis-assistant:latest --limit 100

Per example it replays the conversation (gold answers for earlier turns), asks the model for the LAST assistant turn and reports
  exact  - same text after whitespace/case normalisation
  facts  - every number in the gold answer (counts, codes, dates, amounts) also appears in the model's answer, and the model
           invents no application number that is not in the question
Compare the scores against the same run on validation (train-like phrasings): a large gap means overfitting.
"""
import argparse
import collections
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = re.compile(r"\d{4}/\d{4}/\d{1,3}/\d+")
NUM = re.compile(r"\d+(?:[.,]\d+)?")


def norm(s): return re.sub(r"\s+", " ", s.lower()).strip()


def chat(model, messages, url):
    req = urllib.request.Request(url + "/api/chat", json.dumps({"model": model, "messages": messages, "stream": False,
                                                                  "options": {"temperature": 0}}).encode(),
                                 {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=180))["message"]["content"]


def score(gold, out, question):
    exact = norm(gold) == norm(out)
    gn = set(NUM.findall(APP.sub(" ", gold)))
    facts = all(n in out for n in gn) and set(APP.findall(out)) <= set(APP.findall(question + " " + gold))
    return exact, facts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--files", nargs="*", default=["test_heldout_allcodes.jsonl", "test_heldout_flinetslr.jsonl", "test_heldout_jurisdiction.jsonl", "validation_v2.jsonl"])
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    for f in a.files:
        rows = [json.loads(l) for l in (ROOT / f).open(encoding="utf-8")]
        rows = rows[:a.limit] if a.limit else rows
        agg = collections.defaultdict(lambda: [0, 0, 0])
        for r in rows:
            ms = r["messages"]
            try:
                out = chat(a.model, ms[:-1], a.url)
            except Exception as e:
                print("error", e, file=sys.stderr)
                continue
            ex, fa = score(ms[-1]["content"], out, ms[-2]["content"])
            g = agg[r["meta"]["group"]]
            g[0] += 1; g[1] += ex; g[2] += fa
        tot = [sum(v[i] for v in agg.values()) for i in range(3)]
        print(f"\n{f}: n={tot[0]} exact={tot[1] / max(tot[0], 1):.0%} facts={tot[2] / max(tot[0], 1):.0%}")
        for k, v in sorted(agg.items()):
            print(f"  {k:14s} n={v[0]:4d} exact={v[1] / v[0]:4.0%} facts={v[2] / v[0]:4.0%}")


if __name__ == "__main__":
    main()
