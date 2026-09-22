"""A scenario matrix: many follow-up operations, in many contexts, one at a time and several in one message.

Each scenario picks a context (a list or a field-visit table on screen), a few operations (filter, sort, drop a
row, add / remove a column, count, remarks) and asks them either as separate messages or joined in ONE message
("only NISD and sort by date descending and along with district"). A tiny simulator applies the same operations
to the register's own rows (read by SQL, independent of the chatbot) and the final answer must agree with it:
the rows on screen, their order, the columns, every count. A share of the operations carry a spelling slip.

    python test_matrix.py                 # 120 scenarios, seed 1, no LLM
    python test_matrix.py --n 300 --seed 7
    python test_matrix.py --verbose       # print every scenario, not just failures
"""
import asyncio, random, re, sys, uuid
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import psycopg2
from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.sample_db.dbconn import conn_params
from backend.services import rag
from backend.services.chatbot import process_chat, process_chat_stream
from test_followup_context import officer_context
from test_typo_followups import Stub, damage

NUM = re.compile(r"\d{4}/\d{4}/\d+/\d+")
plain = lambda h: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h or ""))
ARG = lambda name, d: type(d)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else d
N, SEED, VERBOSE, STREAM = ARG("--n", 120), ARG("--seed", 1), "--verbose" in sys.argv, "--stream" in sys.argv


def headers(resp):
    return [re.sub(r"<[^>]+>", "", h).strip() for h in re.findall(r"<th[^>]*>(.*?)</th>", resp or "", re.S)]


def shown(r, prev):
    td = r.get("table_data") or {}
    rows = td.get("applications") or td.get("field_visits") or []
    nums = [x.get("application_number") for x in rows if isinstance(x, dict) and x.get("application_number")]
    if not nums and re.search(r"No field visits|No applications", plain(r.get("response"))) and prev:
        return []
    if not nums and "<table" in (r.get("response") or ""):
        nums = NUM.findall(r.get("response") or "")
    return list(dict.fromkeys(nums)) if nums else prev


async def main():
    rag.llm = Stub()
    rnd = random.Random(SEED)
    cur = psycopg2.connect(**conn_params()).cursor()
    cur.execute("""SELECT a.application_number, a.application_type, a.current_status, a.submission_date,
                          NULLIF(regexp_replace(s.survey_no, '[^0-9].*$', ''), '')::int
                   FROM applications a JOIN survey_numbers s ON s.id = a.survey_number_id""")
    ATTR = {n: {"type": t, "status": st, "date": str(d), "survey": sv or 0} for n, t, st, d, sv in cur.fetchall()}
    cur.execute("""SELECT a.application_number, f.status, f.scheduled_date, a.application_type FROM field_visits f
                   JOIN applications a ON a.id = f.application_id""")
    VIS = {n: {"vstatus": st, "vdate": str(d) if d else "", "type": t} for n, st, d, t in cur.fetchall()}

    async with AsyncSessionLocal() as db:
        o = (await db.execute(select(SISOfficer).where(SISOfficer.email.like("csen%")))).scalars().first()
        off = await officer_context(db, o)

        async def say(sid, hist, q):
            if STREAM:
                import json
                parts = []
                async for chunk in process_chat_stream(q, sid, off, db, chat_history=list(hist)):
                    for line in chunk.decode("utf-8", "replace").splitlines():
                        if line.startswith("data:"):
                            try:
                                ev = json.loads(line[5:].strip())
                            except ValueError:
                                continue
                            if isinstance(ev, dict):
                                for k in ("content",):
                                    if ev.get(k):
                                        parts.append(str(ev[k]))
                                if ev.get("table_data"):
                                    pass
                r = {"response": "".join(parts), "table_data": None, "intent": None}
            else:
                r = await process_chat(q, sid, off, db, list(hist))
            hist += [{"role": "user", "content": q}, {"role": "assistant", "content": plain(r.get("response"))}]
            return r

        CONTEXTS = {
            "CSC list": ("show my applications from CSC", "app"), "rejected list": ("show me all rejected applications", "app"),
            "SRO list": ("show my sro applications", "app"), "all applications": ("show all applications", "app"),
            "visit table": ("show field visit", "fv"),
        }
        bad = 0
        for i in range(N):
            cname = rnd.choice(list(CONTEXTS))
            ctx_q, kind = CONTEXTS[cname]
            sid, hist = str(uuid.uuid4()), []
            r0 = await say(sid, hist, ctx_q)
            state = list(dict.fromkeys(NUM.findall(r0.get("response") or "")))
            if len(state) < 2:
                continue
            attr = (lambda n: {**ATTR.get(n, {}), **VIS.get(n, {})})
            cols_added, cols_removed, count_expect, ops_txt = set(), set(), [], []
            plan = []
            # -------- choose operations (each: text, apply(state) -> state, optional expectation)
            def op_filter():
                if kind == "app":
                    t = rnd.choice(["NISD", "ISD"])
                    return (f"only {t}", lambda L, t=t: [n for n in L if attr(n).get("type") == t], None)
                st = rnd.choice(["completed", "unscheduled"])
                return (f"only {st}", lambda L, st=st: [n for n in L if attr(n).get("vstatus") == st], None)

            def op_sort():
                d = rnd.choice(["descending", "ascending"])
                txt = rnd.choice([f"sort by date {d}", "latest first" if d == "descending" else "oldest first"])
                key = "date" if kind == "app" else "vdate"

                def ap(L, d=d, key=key):
                    dated = [n for n in L if attr(n).get(key)] if kind == "fv" else list(L)
                    und = [n for n in L if n not in dated]
                    return sorted(dated, key=lambda n: attr(n).get(key, ""), reverse=(d == "descending")) + und
                return (txt, ap, ("dates", key, d))

            def op_remove():
                k = rnd.choice([1, 2, 3])
                word = {1: "first", 2: "second", 3: "third"}[k]
                return (rnd.choice([f"remove row {k}", f"not the {word} one"]),
                        lambda L, k=k: [n for j, n in enumerate(L) if j != k - 1] if len(L) >= k else L, None)

            def op_count():
                if kind == "app":
                    w = rnd.choice(["approved", "ISD", "NISD"])
                    key = "status" if w == "approved" else "type"
                    val = "approved" if w == "approved" else w
                    return (f"how many are {w}", lambda L: L, ("count", lambda L, key=key, val=val: sum(1 for n in L if attr(n).get(key) == val)))
                w = rnd.choice(["completed", "unscheduled"])
                return (f"how many are {w}", lambda L: L, ("count", lambda L, w=w: sum(1 for n in L if attr(n).get("vstatus") == w)))

            def op_col():
                c = rnd.choice(["district", "taluk", "ward"])
                if rnd.random() < 0.5:
                    return (rnd.choice([f"along with {c}", f"add {c}"]), lambda L: L, ("col+", c))
                c = rnd.choice(["ward", "stage", "status"]) if kind == "app" else "type"
                return (rnd.choice([f"no {c}", f"without {c}"]), lambda L: L, ("col-", c))

            makers = [op_filter, op_sort, op_remove, op_count, op_col]
            n_ops = rnd.choice([1, 2, 2, 3])
            for _ in range(n_ops):
                plan.append(rnd.choice(makers)())
            # a slip in one op
            texts = []
            for txt, ap, exp in plan:
                if rnd.random() < 0.25:
                    words = txt.split()
                    wi = rnd.choice([j for j, w in enumerate(words) if len(w) >= 4] or [0])
                    dm = damage(words[wi].lower(), rnd)
                    if dm:
                        words[wi] = rnd.choice(dm)[1]
                        txt = " ".join(words)
                texts.append(txt)
            compound = rnd.random() < 0.6 and n_ops >= 2
            # -------- simulate
            st, counts, expect_dates, cols = list(state), [], None, {"+": set(), "-": set()}
            for (_, ap, exp), _t in zip(plan, texts):
                if exp and exp[0] == "count":
                    counts.append((exp[1](st), len(st)))
                _new = ap(st)
                if not _new and st and exp is None and _t.lower().split()[0] in ("only", "omly", "olny", "onlly", "oly"):
                    _new = st          # a filter that matches nothing leaves the table on screen as it was
                st = _new
                if exp and exp[0] == "dates":
                    expect_dates = exp[1:]
                if exp and exp[0] == "col+":
                    cols["+"].add(exp[1]); cols["-"].discard(exp[1])
                if exp and exp[0] == "col-":
                    cols["-"].add(exp[1]); cols["+"].discard(exp[1])
            # -------- ask
            problems = []
            last = r0
            cur_shown = list(state)
            if compound:
                sep = rnd.choice([" and ", ", ", " and "])
                msg = sep.join(texts)
                last = await say(sid, hist, msg)
                cur_shown = shown(last, cur_shown)
                resp_text = plain(last.get("response"))
            else:
                resp_text = ""
                for t in texts:
                    last = await say(sid, hist, t)
                    cur_shown = shown(last, cur_shown)
                    resp_text += " " + plain(last.get("response"))
            # -------- judge
            table_ops = [t for (t, ap, exp) in plan if not (exp and exp[0] in ("count", "col+", "col-"))]
            if table_ops or any(exp and exp[0] in ("col+", "col-") for _, _, exp in plan):
                if set(cur_shown) != set(st):
                    problems.append(f"rows: got {len(cur_shown)} want {len(st)}")
                elif expect_dates and len(st) > 1:
                    key, d = expect_dates
                    seq = [attr(n).get(key, "") for n in cur_shown if attr(n).get(key)]
                    if seq != sorted(seq, reverse=(d == "descending")):
                        problems.append(f"order not {d}")
            for got, tot in counts:
                if f"{got} of those {tot}" not in resp_text and f"{got} of {tot}" not in resp_text:
                    problems.append(f"count '{got} of those {tot}' missing")
            hd = headers(last.get("response"))
            if hd and not compound or hd:
                for c in cols["+"]:
                    if c.title() not in " ".join(hd):
                        problems.append(f"column {c} not shown")
                for c in cols["-"]:
                    if c.title() in hd and c not in cols["+"]:
                        problems.append(f"column {c} still shown")
            tag = "compound" if compound else "separate"
            if problems:
                bad += 1
                print(f"  FAIL  [{cname}, {tag}] {' | '.join(texts)!r}\n        {'; '.join(problems)}"
                      f"\n        {resp_text[:150]}")
            elif VERBOSE:
                print(f"  PASS  [{cname}, {tag}] {' | '.join(texts)!r}")
        print(f"\n{N - bad}/{N} scenarios answered as the simulator predicts")
        return bad


bad = asyncio.run(main())
sys.exit(1 if bad else 0)
