"""IGRS Form 6 and CAN number questions.

Two identifiers that are easy to answer confidently and wrongly:

* **CAN** (Citizen Access Number) — its LENGTH names the counter that issued
  it, not the channel that filed the application: 15 digits from a CSC /
  e-Sevai counter (`133` series), 12 from the TN portal. `CAN_LENGTHS` in
  `identifiers.py` bounds what each channel may carry.
* **IGRS Form 6** — carried by Sub-Registrar referrals and by nothing else,
  and always equal to that application's CAN. An empty IGRS field on a CSC or
  citizen file is the rule, not a gap, so the answer has to say which it is
  rather than "no information found".

Every expectation below is computed from the database in the test, never
hardcoded, so the suite still means something after a reseed.

    python test_igrs_can_queries.py            # everything (DB, no LLM)
    python test_igrs_can_queries.py --data     # invariants only, no chat
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import func, select

from backend.database import AsyncSessionLocal
from backend.models import Application, SISOfficer
from backend.sample_db.identifiers import CAN_LENGTHS
from backend.services.chatbot import process_chat
from backend.services.rag import extract_submission_channel
from test_followup_context import officer_context

DATA_ONLY = "--data" in sys.argv

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    _results.append((bool(ok), label, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n        {detail}" if detail else ""))
    return bool(ok)


def section(title: str) -> None:
    print(f"\n── {title} ──")


def plain(html: str, limit: int = 260) -> str:
    return re.sub(r"<[^>]+>", " ", html or "").replace("\n", " ")[:limit].strip()


async def ask(db, officer, turns):
    """Run a short conversation, returning the last result."""
    sid = str(uuid.uuid4())
    history: list = []
    result = None
    for q in turns:
        result = await process_chat(q, sid, officer, db, list(history))
        history += [{"role": "user", "content": q},
                    {"role": "assistant", "content": result.get("response")}]
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 1. The invariants the answers rest on
# ─────────────────────────────────────────────────────────────────────────────
async def test_invariants(db) -> dict:
    section("1. IGRS / CAN invariants in the register")
    apps = (await db.execute(select(Application))).scalars().all()

    by_channel: dict = {}
    for a in apps:
        by_channel.setdefault(a.submission_channel, []).append(a)

    for channel, rows in sorted(by_channel.items(), key=lambda kv: str(kv[0])):
        with_igrs = [a for a in rows if a.igrs_form6_number]
        if channel == "sub_registrar":
            check(len(with_igrs) == len(rows),
                  f"every Sub-Registrar application carries an IGRS number "
                  f"({len(with_igrs)}/{len(rows)})")
        else:
            check(not with_igrs,
                  f"no {channel} application carries an IGRS number "
                  f"(0/{len(rows)} expected, found {len(with_igrs)})")

    mismatched = [a.application_number for a in apps
                  if a.igrs_form6_number and a.igrs_form6_number != a.can_number]
    check(not mismatched,
          "where an IGRS number exists it equals the CAN (the registered deed)",
          ", ".join(mismatched[:3]))

    bad_len = []
    for a in apps:
        allowed = CAN_LENGTHS.get(a.submission_channel)
        if allowed and a.can_number and len(a.can_number) not in allowed:
            bad_len.append(f"{a.application_number} {a.submission_channel} "
                           f"len={len(a.can_number)}")
    check(not bad_len,
          "every CAN length is one CAN_LENGTHS allows for its channel",
          "; ".join(bad_len[:3]))

    csc_133 = [a for a in by_channel.get("CSC", []) if (a.can_number or "").startswith("133")]
    check(len(csc_133) == len(by_channel.get("CSC", [])),
          f"every CSC CAN is in the 133 series ({len(csc_133)}/"
          f"{len(by_channel.get('CSC', []))})")

    section("2. An IGRS question is a Sub-Registrar question")
    for message, expected in [
        ("which of my applications have an igrs number", "sub_registrar"),
        ("igrs form 6 applications", "sub_registrar"),
        ("show applications from sub registrar", "sub_registrar"),
        # A named application is one file, not a scope -- turning it into a
        # channel filter would answer a different question.
        ("does 2022/0154/28/000156 have an igrs number", None),
        ("what is the igrs form 6 number of 2022/0154/28/000156", None),
        # An explicit channel in the same breath wins.
        ("show applications from CSC do they have igrs number", "CSC"),
        ("show my pending applications", None),
    ]:
        got = extract_submission_channel(message)
        check(got == expected, f"channel of {message!r}", f"-> {got} (want {expected})")

    return by_channel


# ─────────────────────────────────────────────────────────────────────────────
# 2. CAN questions
# ─────────────────────────────────────────────────────────────────────────────
async def test_can(db, samples) -> None:
    section("3. CAN number for one application, per channel")
    for channel, app, officer in samples:
        res = await ask(db, officer, [f"what is the can number of {app.application_number}"])
        answer = plain(res.get("response"))
        check(app.can_number in answer,
              f"{channel}: the CAN on record ({app.can_number}) is answered", answer)
        check(str(len(app.can_number)) in answer,
              f"{channel}: the answer states the digit count "
              f"({len(app.can_number)})", answer)

    section("4. CAN reverse lookup, and its jurisdiction boundary")
    channel, app, officer = samples[0]
    res = await ask(db, officer, [f"which application has can number {app.can_number}"])
    answer = plain(res.get("response"))
    check(app.application_number in answer,
          f"a CAN the officer holds resolves to {app.application_number}", answer)

    # A CAN belonging to another officer must not resolve.
    foreign = next(((c, a, o) for c, a, o in samples
                    if o.officer_id != officer.officer_id), None)
    if foreign:
        _, fapp, _ = foreign
        res = await ask(db, officer, [f"which application has can number {fapp.can_number}"])
        answer = plain(res.get("response"))
        check(fapp.application_number not in answer,
              f"another officer's CAN ({fapp.can_number}) does not resolve to "
              f"their application", answer)

    res = await ask(db, officer, ["which application has can number 999999999999"])
    answer = plain(res.get("response"))
    check(not re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", answer),
          "an unknown CAN names no application at all", answer)

    section("5. CAN as a follow-up, in three languages")
    for label, question in (("English", "what is the can number?"),
                            ("Tamil", "CAN எண் என்ன?"),
                            ("Tanglish", "can number enna?")):
        res = await ask(db, officer, [f"status of {app.application_number}", question])
        answer = plain(res.get("response"))
        check(app.can_number in answer,
              f"{label} follow-up answers the CAN of the application in context",
              answer)


# ─────────────────────────────────────────────────────────────────────────────
# 3. IGRS questions
# ─────────────────────────────────────────────────────────────────────────────
async def test_igrs(db, samples) -> None:
    section("6. IGRS on one application — present, and absent for a reason")
    for channel, app, officer in samples:
        res = await ask(db, officer,
                        [f"does {app.application_number} have an igrs number"])
        answer = plain(res.get("response"))
        if channel == "sub_registrar":
            check(app.igrs_form6_number in answer,
                  f"{channel}: the IGRS number ({app.igrs_form6_number}) is answered",
                  answer)
        else:
            lowered = answer.lower()
            check("no igrs" in lowered or "not" in lowered,
                  f"{channel}: the answer says there is none", answer)
            # The point of the handler: say WHY it is absent, rather than
            # reporting a gap in the record.
            check("sub-registrar" in lowered or "sub registrar" in lowered,
                  f"{channel}: ...and why — only a Sub-Registrar referral has one",
                  answer)
            check("no information" not in lowered and "could not find" not in lowered,
                  f"{channel}: ...and does not report it as missing data", answer)

    section("7. IGRS in Tamil and Tanglish")
    sr = next(((c, a, o) for c, a, o in samples if c == "sub_registrar"), None)
    if sr:
        _, app, officer = sr
        for label, question in (
                ("Tamil", f"{app.application_number} IGRS எண் என்ன?"),
                ("Tanglish", f"{app.application_number} igrs number enna?")):
            res = await ask(db, officer, [question])
            answer = plain(res.get("response"))
            check(app.igrs_form6_number in answer,
                  f"{label}: the IGRS number is answered", answer)

    section("8. IGRS over a list, scoped and counted from the register")
    _, app, officer = sr or samples[0]
    # The question names no application, so it scopes to the Sub-Registrar
    # channel; rejected files stay out, as they do for every other listing.
    expected = (await db.execute(
        select(func.count()).select_from(Application)
        .where(Application.assigned_officer_id == officer.officer_id,
               Application.igrs_form6_number.isnot(None),
               Application.current_status != "rejected"))).scalar_one()
    res = await ask(db, officer, ["which of my applications have an igrs number"])
    answer = plain(res.get("response"), 400)
    check(str(expected) in answer,
          f"'which of my applications have an IGRS number' -> {expected} "
          f"(the officer's non-rejected Sub-Registrar files)", answer)

    res = await ask(db, officer, ["show applications from sub registrar",
                                  "do they have an igrs number"])
    answer = plain(res.get("response"), 300)
    check(answer.lower().startswith("yes"),
          "a Sub-Registrar list answers 'yes' in words above the table", answer)

    csc = next(((c, a, o) for c, a, o in samples if c == "CSC"), None)
    if csc:
        _, _, csc_officer = csc
        res = await ask(db, csc_officer, ["show applications from CSC",
                                          "do they have an igrs number"])
        answer = plain(res.get("response"), 300)
        check(answer.lower().startswith("no"),
              "a CSC list answers 'no' in words above the table", answer)


# ─────────────────────────────────────────────────────────────────────────────
# 4. The rule, asked as a rule
# ─────────────────────────────────────────────────────────────────────────────
RULE_CASES = [
    # (question, topic, phrases the answer must contain, phrases it must not)
    ("if igrs number is absent what does it mean", "igrs_absent",
     ("sro", "csc"), ()),
    ("if there is no igrs number does it mean the application is not from SRO",
     "igrs_absent", ("did not come from the sro",), ()),
    ("why do CSC applications not have an igrs number", "igrs_absent",
     ("rule, not a gap",), ()),
    ("which applications get an igrs form 6 number", "igrs_who",
     ("only sub-registrar",), ()),
    ("is the igrs form 6 number the same as the CAN number", "igrs_equals_can",
     ("same number",), ()),
    ("what is a CAN number", "can_what", ("citizen access number",), ()),
    ("does a 15 digit CAN mean it is a CSC application", "can_length",
     ("no.", "issued"), ()),
    ("what is SRO", "sro_what", ("sub-registrar office",), ()),
    # "On what basis are you deciding SRO or CSC or citizen?" asks for the
    # DERIVATION. It was answered with a table of 54 applications and a channel
    # breakdown: a correct summary of the data, and no answer to the question --
    # the same failure this section already records for the IGRS rule. The
    # channel words scoped a listing because no rule topic claimed the turn.
    ("on what based u r deciding applications from sro or csc or citizen",
     "channel_basis", ("source_name", "camp_flag"), ()),
    ("on what basis do you decide sro or csc", "channel_basis",
     ("source_name",), ()),
    ("how do you decide if an application is from csc or sro", "channel_basis",
     ("sub-registrar",), ()),
    ("how do you know it is CSC", "channel_basis", ("camp_flag",), ()),
    ("what determines the submission channel", "channel_basis",
     ("source_name",), ()),
    # The CAN's length is not the basis, and the rule answer has to say so --
    # it is the reading CLAUDE.md warns against, and the one an officer is
    # most likely to assume.
    ("how is the channel determined", "channel_basis",
     ("length plays no part",), ()),

    # "what is sro n csc" asked about TWO channels and was answered about one.
    # `sro_what` explained the Sub-Registrar and never mentioned CSC, so half
    # the question went unanswered with nothing to show it had -- and CSC had
    # no definition of its own at all, so asking about it alone reached the LLM.
    # A definition question now answers every channel it names.
    ("what is sro n csc", "channel_defn:sub_registrar,CSC",
     ("sub-registrar office", "common service centre"), ()),
    ("what is sro and csc", "channel_defn:sub_registrar,CSC",
     ("sub-registrar office", "common service centre"), ()),
    ("what is csc", "channel_defn:CSC",
     ("common service centre", "e-sevai"), ()),
    ("what does CSC stand for", "channel_defn:CSC",
     ("common service centre",), ()),
    ("what is CSC and citizen", "channel_defn:CSC,citizen",
     ("common service centre", "citizen submission"), ()),
    ("what is sro, csc and citizen", "channel_defn:sub_registrar,CSC,citizen",
     ("sub-registrar office", "common service centre", "citizen submission"), ()),
]

# Questions that must NOT be claimed by the rule handler: they are about data.
NOT_RULE = [
    "which of my applications have an igrs number",
    "does 2022/0153/28/000254 have an igrs number",
    "show applications from CSC",
    "can i apply for a new application",
    "can i file another application on survey 5",
    "what is the can number of 2022/0154/28/000156",
    # These name a channel but ask the register, not the rule. The
    # channel_basis cue needs BOTH halves -- how the decision is made, and the
    # channels it is made between -- so a listing keeps its table.
    "how many applications from sro",
    "which applications are from citizen",
    "show me csc applications",
    "submission channel of 2026/0154/28/001167",
]


async def test_rule_questions(db, officer) -> None:
    """"If the IGRS number is absent, what does it mean -- so it's not from SRO?"

    This is a question about the RULE. It names no application, and answering
    it with a table of applications answers a question nobody asked -- which is
    what happened once an IGRS question was read as a Sub-Registrar channel
    filter. The rule is a documented invariant, so it is answered in Python.
    """
    from backend.services.chatbot import _igrs_can_rule_topic

    section("9. IGRS / CAN asked as a rule, not about a file")
    for question, topic, must, must_not in RULE_CASES:
        got = _igrs_can_rule_topic(question)
        check(got == topic, f"topic of {question!r}", f"-> {got} (want {topic})")

        res = await ask(db, officer, [question])
        # 500 was shorter than the rule answers themselves: the channel
        # derivation states its three cases before it gets to the CAN's length,
        # so a phrase check against a 500-character window failed on text that
        # was present. The window has to be longer than the answer, or the
        # assertion is about the truncation rather than the answer.
        answer = plain(res.get("response"), 1400)
        lowered = answer.lower()
        check(res.get("intent") == "igrs_can_rule",
              f"...answered as a rule, not a listing", f"intent={res.get('intent')}")
        # A rule answer must not be a table of the officer's applications.
        check(not re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", answer),
              "...and names no application number", answer[:120])
        for phrase in must:
            check(phrase in lowered, f"...says {phrase!r}", answer[:160])
        for phrase in must_not:
            check(phrase not in lowered, f"...does not say {phrase!r}", answer[:160])

    section("10. Data questions are left alone")
    for question in NOT_RULE:
        got = _igrs_can_rule_topic(question)
        check(got is None, f"not a rule question: {question!r}", f"-> {got}")

    section("11. The rule answers in Tamil")
    for question in ("IGRS எண் இல்லை என்றால் அர்த்தம் என்ன?",
                     "SRO என்றால் என்ன?"):
        res = await ask(db, officer, [question])
        answer = plain(res.get("response"), 300)
        check(res.get("intent") == "igrs_can_rule"
              and any("஀" <= ch <= "௿" for ch in answer),
              f"Tamil rule question answered in Tamil: {question}", answer[:150])

    section("12. The rule is in the corpus, not only in code")
    land = Path("backend/documents/land_rules.txt").read_text(encoding="utf-8").lower()
    faq = Path("backend/documents/faq_english.txt").read_text(encoding="utf-8").lower()
    check("sro stands for sub-registrar office" in land,
          "land_rules.txt spells out SRO", "")
    check("did not come from the sro" in land,
          "land_rules.txt states what an ABSENT IGRS number means", "")
    check("citizen access number" in land,
          "land_rules.txt defines the CAN number", "")
    check("not from the sro" in faq,
          "faq_english.txt carries the absent-IGRS question", "")


async def test_can_listing(db, officer) -> None:
    """A CAN column must carry CANs, and the same request must not have two
    different answers depending on how it is phrased."""
    from backend.services.rag import parse_intent

    section("13. Listing the officer's own CAN numbers")
    # "how" is a substring of "sHOW", so an unbounded cue match sent
    # "show ... can numbers" to the static CAN guide while
    # "list ... can numbers" went to the listing -- one request, two answers.
    for question in ("show my applications with their can numbers",
                     "list the can numbers of my applications",
                     "show can numbers for my approved applications"):
        check(parse_intent(question) != "can_number_info",
              f"{question!r} is a listing, not the static guide",
              f"-> {parse_intent(question)}")

    # ...while a genuine question about the rule still reaches the guide.
    for question in ("how is a can number assigned", "can number csc"):
        check(parse_intent(question) == "can_number_info",
              f"{question!r} still reaches the CAN guide",
              f"-> {parse_intent(question)}")

    res = await ask(db, officer, ["list the can numbers of my applications"])
    answer = plain(res.get("response"), 600)
    shown = re.findall(r"\d{4}/\d{3,4}/\d{1,3}/\d+", answer)
    check(bool(shown), "the listing shows applications", answer[:120])
    if shown:
        expected = (await db.execute(
            select(Application.can_number)
            .where(Application.application_number == shown[0]))).scalar_one_or_none()
        check(expected and expected in answer,
              f"the CAN column carries the real CAN ({expected}), not 'N/A'",
              answer[:200])

    section("14. The CAN guide states the number format")
    res = await ask(db, officer, ["how is a can number assigned"])
    answer = plain(res.get("response"), 700).lower()
    check("number format" in answer,
          "the guide renders the Number format row it computes", answer[:150])
    check("133" in answer and "12 digits" in answer,
          "...naming both counters, so length is not read as the channel",
          answer[:200])


async def main() -> int:
    async with AsyncSessionLocal() as db:
        by_channel = await test_invariants(db)

        # One application per channel, each with the officer who holds it.
        samples = []
        for channel in ("sub_registrar", "CSC", "citizen"):
            rows = [a for a in by_channel.get(channel, []) if a.can_number]
            if not rows:
                continue
            app = rows[0]
            officer_row = (await db.execute(
                select(SISOfficer).where(SISOfficer.id == app.assigned_officer_id)
            )).scalars().first()
            if officer_row is None:
                continue
            samples.append((channel, app, await officer_context(db, officer_row)))

        if not samples:
            check(False, "at least one application per channel to test with")
        elif DATA_ONLY:
            print("\n(--data: chat sections skipped)")
        else:
            print("\nSamples: " + "; ".join(
                f"{c} {a.application_number} CAN={a.can_number} "
                f"({len(a.can_number)}d) IGRS={a.igrs_form6_number or '-'}"
                for c, a, _ in samples))
            await test_can(db, samples)
            await test_igrs(db, samples)
            await test_rule_questions(db, samples[0][2])
            await test_can_listing(db, samples[0][2])

    failed = [r for r in _results if not r[0]]
    print(f"\n{'=' * 70}")
    print(f"{len(_results) - len(failed)}/{len(_results)} checks passed")
    for _, label, _d in failed:
        print(f"  FAILED: {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
