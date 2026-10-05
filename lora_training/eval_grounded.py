"""Grounding-aware scorer for the held-out sets (replaces the keyword-overlap score for decisions).

Every value an answer states -- a count, a date, a ward / block, a channel, a status, a survey / sub-division, a service code,
an application number -- is compared with the value in the expected answer, which was built from the data that is IN the prompt.
Each answer gets exactly one outcome:

  correct    every value of the expected answer is stated, and nothing contradicts it (any wording)
  missing    the answer leaves a value out or says it does not know / has no data      (safe, but unhelpful)
  wrong      the answer states a value of the same kind that differs from the expected one, or an application number that is
             not in the question or the expected answer                                (a hallucination: the dangerous one)

An expected "none found / no applications" answer is correct when the reply also says none / 0, wrong when it states a positive number.
`wording` counts correct answers whose words differ a lot from the expected text -- not an error, just reported.

    python lora_training/eval_grounded.py results.json [--model tuned|base] [--show 12]
    python lora_training/eval_grounded.py --selftest
results.json = the list written by the Colab evaluation: {row_id, group, service_code, user, gold, tuned_pred, base_pred}
"""
import argparse
import collections
import json
import random
import re
import sys

APP = re.compile(r"\b\d{4}/\d{4}/\d{1,3}/\d+\b")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
CODE = re.compile(r"(?<![\d/])01\d\d(?![\d/])")
MONTH = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b", re.I)
WARD = re.compile(r"(?:\bward|வார்டு)\s*(\d{3})", re.I)
BLOCK = re.compile(r"(?:\bblock|பிளாக்)\s*(\d{4})", re.I)
SURVEY = re.compile(r"\bsurvey\s+(\d+)", re.I)
SUBDIV = re.compile(r"\bsub-?division\s+(\d+)", re.I)
PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
CHANNELS = {"csc": "CSC", "sub-registrar": "SRO", "sub registrar": "SRO", "sro": "SRO", "citizen portal": "citizen", "citizen": "citizen"}
STATUS = {"approved": "approved", "pending": "pending", "rejected": "rejected"}
NONE_WORDS = ("no application", "none found", "no approved", "no pending", "no rejected", "not found", "no ", "0 application", "zero",
              "இல்லை")  # Tamil "illai"
ABSTAIN = ("not recorded", "not held", "could not find", "cannot", "can't", "do not have", "don't have", "does not hold", "not available",
           "no data", "i cannot", "not shown", "does not show", "cannot say", "not linked", "unable", "no such record")
STOP = {"that", "this", "with", "from", "have", "been", "your", "what", "which", "does", "will", "into", "only", "also", "over", "the",
        "and", "for", "are", "was", "were", "you", "application", "applications"}


def facts(t):
    f = collections.defaultdict(set)
    f["app"] |= set(APP.findall(t))
    f["date"] |= set(DATE.findall(t))
    f["month"] |= {m.lower() for m in MONTH.findall(t)}
    f["code"] |= set(CODE.findall(APP.sub(" ", t)))
    f["ward"] |= set(WARD.findall(t))
    f["block"] |= set(BLOCK.findall(t))
    f["survey"] |= set(SURVEY.findall(t))
    f["subdiv"] |= set(SUBDIV.findall(t))
    f["pct"] |= set(PCT.findall(t))
    low = t.lower()
    for k, v in CHANNELS.items():
        if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", low):
            f["channel"].add(v)
    if "citizen" in f["channel"] and "citizen portal" not in low and "citizen" in low and re.search(r"citizen access", low):
        f["channel"].discard("citizen")
    for k, v in STATUS.items():
        if re.search(r"(?<![a-z])" + k + r"(?![a-z])", low):
            f["status"].add(v)
    rest = APP.sub(" ", t)
    for pat in (DATE, MONTH, WARD, BLOCK, SURVEY, SUBDIV, PCT, CODE):
        rest = pat.sub(" ", rest)
    f["count"] |= {n for n in re.findall(r"(?<![\d.])\d+(?![\d.])", rest)}
    return {k: v for k, v in f.items() if v}


def says_none(t):
    low = t.lower()
    return any(w in low for w in NONE_WORDS) and not re.search(r"(?<![\d.])[1-9]\d*(?![\d.])\s*(?:application|file|விண்ணப்ப)", low)


def words(t):
    return {w for w in re.findall(r"[a-zA-Z஀-௿]{3,}", t.lower()) if w not in STOP}


def grade(gold, pred, question=""):
    """-> (outcome, wording_differs)"""
    gf, pf = facts(gold), facts(pred)
    gold_none = not gf.get("count") and says_none(gold) and not any(k in gf for k in ("date", "ward", "block", "survey", "subdiv", "pct"))
    if gold_none:
        if says_none(pred) or "0" in pf.get("count", set()):
            return "correct", False
        if pf.get("count") and "0" not in pf["count"]:
            return "wrong", False
        return "missing", False
    if not [k for k in gf if k != "app"] and any(x in gold.lower() for x in ABSTAIN + ("not recorded",)):
        # the expected answer is itself "not recorded / not held": the reply must say so too, and state no value
        if any(x in pred.lower() for x in ABSTAIN + ("not recorded", "none", "no ")) and not [k for k in pf if k != "app"]:
            return "correct", False
        return ("wrong", False) if [k for k in pf if k != "app"] else ("missing", False)
    invented = pf.get("app", set()) - gf.get("app", set()) - set(APP.findall(question))
    if invented:
        return "wrong", False
    missing = wrong = False
    for k, gv in gf.items():
        if k == "app":
            continue                       # an application number is only checked for invention (above), never required
        if k in ("status", "channel", "code") and not (gv & set(pf.get(k, set()))) and pf.get(k):
            wrong = True
            continue
        pv = pf.get(k, set())
        if gv <= pv:
            continue
        if not pv:
            missing = True
        elif k == "count" and len(gv) == 1 and (gv & pv):
            continue                       # a count that appears among others (e.g. "4 of 12"): not a contradiction
        else:
            wrong = True
    if wrong:
        return "wrong", False
    if missing:
        return "missing", False
    gw = words(gold)
    ov = len(gw & words(pred)) / len(gw) if gw else 1.0
    if not [k for k in gf if k != "app"]:
        # nothing typed to compare (free text, "not recorded" lines ...): the words have to agree with the expected answer
        if any(x in pred.lower() for x in ABSTAIN):
            return "missing", False
        return ("correct", ov < 0.5) if ov >= 0.35 else ("wrong", False)
    return "correct", bool(gw) and ov < 0.35


def report(results, model="tuned_pred", show=0):
    agg = collections.defaultdict(collections.Counter)
    bad = []
    for r in results:
        out, wd = grade(r["gold"], r[model], r.get("user", ""))
        for k in (r["group"], "ALL"):
            agg[k][out] += 1
            agg[k]["wording"] += int(wd)
        if out == "wrong":
            bad.append(r)
    print(f"{'group':12s} {'n':>5s} {'correct':>8s} {'missing':>8s} {'WRONG':>7s}   (wording-differs among correct)")
    for k, c in sorted(agg.items(), key=lambda x: (x[0] == "ALL", -sum(x[1][o] for o in ("correct", "missing", "wrong")))):
        n = c["correct"] + c["missing"] + c["wrong"]
        print(f"{str(k):12s} {n:5d} {100 * c['correct'] / n:7.0f}% {100 * c['missing'] / n:7.0f}% {100 * c['wrong'] / n:6.0f}%   ({c['wording']})")
    random.seed(3)
    for r in random.sample(bad, min(show, len(bad))):
        print("\nWRONG |", r["group"], "| Q:", r["user"][:80].replace("\n", " "), "\n  GOLD :", r["gold"][:140].replace("\n", " "),
              "\n  MODEL:", r[model][:140].replace("\n", " "))
    return agg


def selftest():
    cases = [
        ("2026/0185/02/116925 is in ward 130, block 0057.", "2026/0185/02/116925 is in ward 130, block 0057.", "correct"),
        ("2026/0185/02/116925 is in ward 130, block 0057.", "It sits in block 0057 of ward 130.", "correct"),
        ("2026/0185/02/116925 is in ward 130, block 0057.", "2026/0185/02/116925 is in ward 001.", "wrong"),
        ("2026/0185/02/116925 is in ward 130, block 0057.", "I do not have that information.", "missing"),
        ("2025/0181/02/297327 was submitted on 2025-02-07.", "2025/0181/02/297327 was submitted on 2025-08-13.", "wrong"),
        ("2024/0168/28/577114 came in through Sub-Registrar.", "2024/0168/28/577114 came in through CSC.", "wrong"),
        ("2024/0168/28/577114 came in through Sub-Registrar.", "It came in through the Sub-Registrar.", "correct"),
        ("Found 4 approved application(s) under 0159 (Addition).", "There are 4 approved Addition applications (0159).", "correct"),
        ("Found 4 approved application(s) under 0159 (Addition).", "Found 9 approved application(s) under 0159.", "wrong"),
        ("Found 4 approved application(s) under 0159 (Addition).", "The system shows the queue for each code.", "missing"),
        ("No approved applications under 0187 (Register Patta) found in your jurisdiction.", "There are no approved 0187 applications.", "correct"),
        ("No approved applications under 0187 (Register Patta) found in your jurisdiction.", "Found 3 approved application(s) under 0187.", "wrong"),
        ("2026/0169/02/785805 is pending (stage: Open).", "2026/0169/02/111111 is pending.", "wrong"),
        ("Service code 0158 is Modification / Anadeenam.", "Service code 0161 is Modification.", "wrong"),
        ("Service code 0187 is Register Patta: Registration of patta records. It does not need a field visit.",
         "Service code 0187 is Register Patta: registering patta records; no field visit.", "correct"),
    ]
    bad = 0
    for gold, pred, want in cases:
        got, _ = grade(gold, pred)
        if got != want:
            bad += 1
            print("FAIL", want, "got", got, "|", pred)
    print(f"{len(cases) - bad}/{len(cases)} selftest cases pass")
    return bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="?")
    ap.add_argument("--model", default="tuned", choices=["tuned", "base"])
    ap.add_argument("--show", type=int, default=10)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest or not a.results:
        sys.exit(1 if selftest() else 0)
    report(json.load(open(a.results, encoding="utf-8")), a.model + "_pred", a.show)
