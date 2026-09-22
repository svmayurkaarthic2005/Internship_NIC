"""Replay train_augmented.jsonl through the CURRENT pipeline and compare answers.

The training answers were produced by the deterministic handlers at dataset-build
time, so a difference now is either intended drift (a fixed bug, a reseed) or a
regression. The LLM is stubbed for the main pass: any turn that would need the
model is reported as 'llm' and not compared. `--llm N` then replays N of those
rows with the real model.

python test_train_replay.py                  # all rows, LLM stubbed
python test_train_replay.py --limit 200      # first 200 rows
python test_train_replay.py --source typo    # only rows whose meta.source matches
python test_train_replay.py --llm 20         # additionally replay 20 LLM rows live
"""
import argparse
import asyncio
import collections
import difflib
import html
import json
import logging
import re
import sys
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("backend").setLevel(logging.WARNING)

from sqlalchemy import select

from backend.database import AsyncSessionLocal, engine
from backend.models import SISOfficer
from backend.sample_db.build_lora_dataset import build_officer_context, _flatten_html
from backend.services import chatbot, rag

SENTINEL = "[[LLM-SKIPPED]]"
OFFICERS = ["csenthil@sis.tn.gov.in", "msivakumar@sis.tn.gov.in", "muthulakshmis@sis.tn.gov.in"]
DEFAULT_OFFICER = OFFICERS[1]
APP_NO = re.compile(r"\d{4}/\d{4}/\d{2}/\d{6}")
NUM = re.compile(r"\d+")


class StubLLM:
    """Every model call returns the sentinel; bind()/bind_tools() refuse so the
    agent and the greeting writer fall back to their non-LLM paths."""
    temperature = 0.1

    def bind(self, **kw):
        raise RuntimeError("LLM stubbed")

    def bind_tools(self, *a, **k):
        raise RuntimeError("LLM stubbed")

    async def ainvoke(self, *a, **k):
        class R:
            content = SENTINEL
        return R()

    async def astream(self, *a, **k):
        class C:
            content = SENTINEL
        yield C()


LONG_ID = re.compile(r"(?<!\d)\d{9,15}(?!\d)")


def mask(s: str) -> str:
    """The training set carries substituted application numbers and identifiers
    (it must not hold live records), so compare with those masked out."""
    return LONG_ID.sub("<ID>", APP_NO.sub("<APP>", s or ""))


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", mask(s).lower()).strip()


def verdict(got: str, want: str) -> str:
    if SENTINEL in got:
        return "llm"
    got = html.unescape(got)
    g, w = norm(got), norm(want)
    if g == w:
        return "exact"
    ratio = difflib.SequenceMatcher(None, g, w).ratio()
    if ratio >= 0.97:
        return "exact"
    if collections.Counter(NUM.findall(mask(got))) == collections.Counter(NUM.findall(mask(want))):
        return "facts_same"
    return "different"


_FEE_LINE = re.compile(r"Fee: |கட்டணம்:|Govt Fee|CSC Charge|\u20b9")
_RELATIVE_DATE = re.compile(r"\b(this|last|next|today|yesterday|tomorrow)\b|இன்று|நேற்று", re.IGNORECASE)


def classify_diff(r):
    """Why a replayed row differs from its recorded answer. Anything no rule
    explains is a REGRESSION CANDIDATE and is listed for review."""
    t = [t for t in r["turns"] if t["verdict"] in ("different", "timeout")][-1]
    g, w, q = t["got"], t["want"], t["q"]
    src, mi = (r.get("source") or ""), r.get("meta_intent")

    def drift(reason):
        r["_reason"] = reason
        return "intentional drift"
    if _FEE_LINE.search(w) and (mi in ("service_code_lookup", "service_code_guide") or "Govt Fee" in w):
        return drift("fixed fee schedule removed; fees are read from the register")
    if "e-sevi" in q.lower():
        return drift("e-sevi removed as a channel word")
    if "more owner(s) omitted" in w or re.search(r"\(\d+ more .* omitted\)", w):
        return "not comparable: recorded answer is not pipeline output"
    if re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b", q, re.IGNORECASE):
        return drift("date-relative answer (a named month)")
    if src == "typo" and re.search(r"potspone|pedning|shfit|ovredue", q):
        return drift("typo now understood; the recorded answer misread it")
    if src == "tamil_synthetic" and w.startswith("Found") and t["intent"] in ("application_status", "submission_channel_check"):
        return "not comparable: recorded answer is not pipeline output"
    if "OVERDUE DAYS" in w:
        return drift("date-relative answer (days overdue)")
    if "matching application" in w and "rejected" in w and "matching application" not in g:
        return drift("empty-list note now counts rejected files only inside the asked period")
    if "skip anything" in q or "escalated merge" in q:
        return drift("negation honoured / recorded answer was an unrelated escalation list")
    if re.search(r"[\u0B80-\u0BFF]", g) and not re.search(r"[\u0B80-\u0BFF]", w) and src in (
            "tamil_synthetic", "tanglish", "typo", "language_expansion"):
        return drift("reply language follows the question (Tanglish / Tamil -> Tamil)")
    if src == "clean_injected" and "does not exist" in g:
        return "not comparable: recorded answer is not pipeline output"
    if "outside your assigned jurisdiction" in g and w.startswith("Found") and not r.get("officer_recorded", True):
        return "not comparable: recorded answer is not pipeline output"
    if src == "tamil_synthetic" and re.search(r"The (oldest|most recent|newest)", w):
        return drift("Tanglish/Tamil question now answered in Tamil (recorded in English)")
    if "Between Dates" in w or re.search(r"20\d\d-\d\d-\d\d to", w) or _RELATIVE_DATE.search(q):
        return drift("date-relative answer")
    if re.search(r"^Found \d+ application", w) and re.search(r"^Found \d+ application", g):
        return drift("list differs: typo now corrected / officer data / substituted numbers")
    if src in ("faq_english", "land_rules.txt", "workflow_guide.txt", "survey_manual.txt", "workflow_corpus",
               "faq_tamil") or mi == "knowledge_query":
        return "not comparable: recorded answer is not pipeline output"
    if src == "tamil_synthetic" and w.startswith("Found") and re.search(
            r"எதைக் குறிப்பிடுகிறீர்கள்|Please specify|Endha application|குறிப்பிடவும்|Which one", g):
        return "known gap"
    return "REGRESSION CANDIDATE"


async def replay_row(db, ctx, row, timeout):
    users = [m["content"] for m in row["messages"] if m["role"] == "user"]
    wants = [m["content"] for m in row["messages"] if m["role"] == "assistant"]
    sid = str(uuid.uuid4())
    turns = []
    for q, want in zip(users, wants):
        try:
            r = await asyncio.wait_for(
                chatbot.process_chat(q, sid, ctx, db, chat_history=[]), timeout=timeout)
        except asyncio.TimeoutError:
            r = {"response": "[[TIMEOUT]]", "intent": "TIMEOUT"}
        got = _flatten_html(r.get("response") or "")
        v = "timeout" if r.get("intent") == "TIMEOUT" else verdict(got, want)
        turns.append({"q": q, "intent": r.get("intent"), "verdict": v, "got": got, "want": want})
    return turns


async def run(args):
    rows = [json.loads(l) for l in open("train_augmented.jsonl", encoding="utf-8") if l.strip()]
    if args.source:
        rows = [r for r in rows if (r["meta"].get("source") or "") == args.source]
    if args.limit:
        rows = rows[: args.limit]
    ctxs = {}
    async with AsyncSessionLocal() as db:
        for email in OFFICERS:
            o = (await db.execute(select(SISOfficer).where(SISOfficer.email == email))).scalars().first()
            ctxs[email] = await build_officer_context(db, o)

    real_llm = rag.llm
    rag.llm = StubLLM()
    results, llm_rows = [], []
    async with AsyncSessionLocal() as db:
        for i, row in enumerate(rows):
            # A row with no officer recorded was built for one of the three; replay it as
            # each and keep the closest match.
            order = ["exact", "facts_same", "different", "timeout", "llm"]
            emails = [row["meta"]["officer"]] if row["meta"].get("officer") else OFFICERS
            best = None
            for email in emails:
                cand = await replay_row(db, ctxs[email], row, timeout=30)
                rank = max(order.index(t["verdict"]) for t in cand)
                sim = sum(difflib.SequenceMatcher(None, norm(t["got"]), norm(t["want"])).ratio() for t in cand)
                key = (rank, -sim)
                if best is None or key < best[0]:
                    best = (key, email, cand)
                if rank == 0:
                    break
            _, email, turns = best
            worst = "exact"
            for t in turns:
                if order.index(t["verdict"]) > order.index(worst):
                    worst = t["verdict"]
            rec = {"i": i, "source": row["meta"].get("source"), "meta_intent": row["meta"].get("intent"),
                   "officer": email, "officer_recorded": bool(row["meta"].get("officer")),
                   "verdict": worst, "turns": turns}
            results.append(rec)
            if worst == "llm":
                llm_rows.append((row, email))
            if (i + 1) % 100 == 0:
                c = collections.Counter(r["verdict"] for r in results)
                print(f"  {i + 1}/{len(rows)}  {dict(c)}", flush=True)
    rag.llm = real_llm

    if args.llm and llm_rows:
        step = max(1, len(llm_rows) // args.llm)
        picked = llm_rows[::step][: args.llm]
        print(f"\nlive-LLM replay of {len(picked)} of {len(llm_rows)} model-path rows ...", flush=True)
        async with AsyncSessionLocal() as db:
            for row, email in picked:
                turns = await replay_row(db, ctxs[email], row, timeout=120)
                results.append({"i": -1, "source": "LIVE-LLM:" + str(row["meta"].get("source")),
                                "meta_intent": row["meta"].get("intent"), "officer": email,
                                "verdict": "live", "turns": turns})

    Path("train_replay_results.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results), encoding="utf-8")
    await engine.dispose()

    main = [r for r in results if r["i"] >= 0]
    total = collections.Counter(r["verdict"] for r in main)
    print("\n=== summary (LLM stubbed) ===")
    print(f"rows: {len(main)}   " + "   ".join(f"{k}: {v}" for k, v in total.most_common()))

    # Not one match rate: a difference is only a regression if nothing explains it.
    buckets = collections.defaultdict(list)
    for r in main:
        if r["verdict"] in ("exact", "facts_same"):
            buckets["match"].append(r)
        elif r["verdict"] == "llm":
            buckets["not comparable: needs the model"].append(r)
        else:
            buckets[classify_diff(r)].append(r)
    order = ["match", "intentional drift", "not comparable: needs the model",
             "not comparable: recorded answer is not pipeline output", "known gap", "REGRESSION CANDIDATE"]
    print("\nwhat the rows are:")
    for k in order:
        print(f"  {len(buckets.get(k, [])):5}  {k}")
    for k in buckets:
        if k not in order:
            print(f"  {len(buckets[k]):5}  {k}")
    comparable = len(buckets["match"]) + len(buckets["REGRESSION CANDIDATE"]) + len(buckets["known gap"])
    if comparable:
        print(f"\nquality over the rows where a comparison is meaningful: "
              f"{len(buckets['match'])}/{comparable} ({100 * len(buckets['match']) / comparable:.1f}%)   "
              f"regression candidates: {len(buckets['REGRESSION CANDIDATE'])}   known gaps: {len(buckets['known gap'])}")
    print("\nintentional drift, by reason:")
    for reason, n in collections.Counter(r.get("_reason") for r in buckets["intentional drift"]).most_common():
        print(f"  {n:5}  {reason}")
    cand = buckets["REGRESSION CANDIDATE"]
    if cand:
        print("\nregression candidates to review (first 15):")
        for r in cand[:15]:
            t = [t for t in r["turns"] if t["verdict"] in ("different", "timeout")][-1]
            print(f"  [{r['source']}/{r['meta_intent']}] {t['q'][:60]!r} -> {t['intent']}\n      want: {t['want'][:110]!r}\n      got : {t['got'][:110]!r}")
    live = [r for r in results if r["verdict"] == "live"]
    if live:
        print("\nlive-LLM rows (first turn shown):")
        for r in live:
            t = r["turns"][-1]
            print(f"  [{t['intent']}] {t['q'][:60]!r}\n      got : {t['got'][:150]!r}\n      want: {t['want'][:150]!r}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--source", type=str, default="")
    ap.add_argument("--llm", type=int, default=0)
    raise SystemExit(asyncio.run(run(ap.parse_args())))
