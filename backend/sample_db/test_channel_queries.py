"""
Check that submission-channel questions come back with the right answer.

A channel question has three halves, and each can be wrong on its own:

  routing   -- "show sub registrar applications" has to reach the channel
               filter, and "what is the CAN of X" the CAN handler. Checked
               against parse_intent / extract_submission_channel with no DB.

  derivation -- `applications.submission_channel` has to hold what
               `can_channel()` says it should, recomputed here straight from
               the layer-1 columns so a drift in build_app_tables.py shows up.

  answering -- the handler then has to name the channel the register holds,
               and cite the CAN for a CSC or citizen file. A wrong channel is
               worse than no answer: it tells an officer a citizen walked into
               a service centre when the Sub-Registrar raised the file.

The counts are computed from the database independently of the handlers, so a
filter that quietly widens or drops the channel shows up here too.

Run from the project root:
    python -m backend.sample_db.test_channel_queries
    python -m backend.sample_db.test_channel_queries --fast   # skip the LLM cases
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Names in the extracts are Tamil, and a Windows console defaults to cp1252,
# which cannot encode them. Same guard as backend/main.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import logging

from sqlalchemy import func, select, text

from backend.database import AsyncSessionLocal, engine
from backend.models import Application, SISOfficer
from backend.sample_db.identifiers import (CAN_LENGTHS, _looks_self_filed,
                                           can_channel)
from backend.services import postgres
from backend.services.rag import extract_submission_channel, parse_intent

# database.py turns echo on in development; the suite prints its own findings
# and the SQL echo drowns them
engine.echo = False
logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)

CHANNELS = ("CSC", "citizen", "sub_registrar")

# ── 1. routing: the phrase has to reach the channel filter ──────────────────
# (question, expected channel or None)
ROUTING = [
    ("show CSC applications", "CSC"),
    ("show e sevai applications", "CSC"),
    ("applications from common service centre", "CSC"),
    ("e-sevai applications", "CSC"),
    ("show sub registrar applications", "sub_registrar"),
    ("sub-registrar referred apps", "sub_registrar"),
    ("igrs referral list", "sub_registrar"),
    ("how many applications came from the sub registrar", "sub_registrar"),
    ("citizen portal applications", "citizen"),
    ("self-submitted applications", "citizen"),
    ("applications submitted by citizen", "citizen"),
    # a CAN question is about a number, not a channel filter
    ("what is the CAN number of 2023/0153/28/000367", None),
    ("show my pending applications", None),
]

# ── 2. per-application questions that must name the channel ────────────────
PER_APP = [
    "submission channel of {app}",
    "was {app} submitted by CSC or citizen",
    "who submitted {app}",
    # the word "office" here must not pull the question into the stage lookup
    "how did application {app} reach the office",
    "how did application {app} arrive",
]

# per-application phrasings that must NOT be read as channel questions
NOT_CHANNEL = [
    ("what is the current stage of {app}", "application_status"),
    ("which office is {app} at now", "application_status"),
    ("when did {app} reach the DIS", "application_status"),
]

# ── A PLURAL listing is not a question about one file ───────────────────────
# `submission_channel_check` recognised a back-reference to one application by
# plain substring, so "the application" matched inside "the applicationS":
# "what are the applications from CSC" -- a listing that names no file at all --
# was answered *"Please specify the application number you are asking about."*
# The cue is token-bounded now, and \b does the work on its own: in
# "applications" the position after "application" sits between two word
# characters, so no boundary matches there.
PLURAL_NOT_PER_APP = [
    "what are the applications from csc",
    "what are the applications from sub registrar",
    "which applications are from csc",
    "show me the applications from csc",
]
# ...while the singular back-reference must still reach it.
SINGULAR_STILL_PER_APP = [
    "how was this application submitted",
    "how did the application reach the office",
    "was this application from csc",
]

# ── 3. the CAN question, one per channel ───────────────────────────────────
CAN_Q = "what is the CAN number of {app}"

# what the answer must contain for each channel, lowercased
EXPECT = {
    "CSC": ("csc", "common service"),
    "citizen": ("citizen",),
    "sub_registrar": ("sub-registrar", "sub registrar", "igrs"),
}
# and what it must NOT contain -- naming the wrong channel is the failure
# this suite exists to catch
FORBID = {
    "CSC": ("sub-registrar", "sub registrar"),
    "citizen": ("sub-registrar", "sub registrar", "common service"),
    "sub_registrar": ("common service",),
}


async def build_context(db, officer):
    from backend.sample_db.test_questions import build_context as ctx
    return await ctx(db, officer)


def _says(answer: str, words) -> bool:
    low = answer.lower()
    return any(w in low for w in words)


async def main() -> int:
    fast = "--fast" in sys.argv
    failures: list[str] = []

    async with AsyncSessionLocal() as db:
        dbname = (await db.execute(text("SELECT current_database()"))).scalar()
        print(f"database: {dbname}")

        # ── 1. routing ──────────────────────────────────────────────────────
        print("\n[1/4] routing -- the phrase reaches the channel filter")
        for question, want in ROUTING:
            got = extract_submission_channel(question)
            ok = got == want
            print(f"  {'ok  ' if ok else 'FAIL'} {question[:52]:54s} "
                  f"{str(got):14s} expected {want}")
            if not ok:
                failures.append(f"routing: {question} -> {got}, expected {want}")

        for template, want_intent in NOT_CHANNEL:
            question = template.format(app="2023/0153/28/000367")
            got = parse_intent(question)
            ok = got == want_intent
            print(f"  {'ok  ' if ok else 'FAIL'} {question[:52]:54s} "
                  f"{got:14s} expected {want_intent}")
            if not ok:
                failures.append(f"routing: {question} -> {got}, expected {want_intent}")

        for question in PLURAL_NOT_PER_APP:
            got = parse_intent(question)
            ok = got != "submission_channel_check"
            print(f"  {'ok  ' if ok else 'FAIL'} {question[:52]:54s} "
                  f"{got:14s} must not be submission_channel_check")
            if not ok:
                failures.append(
                    f"routing: plural listing {question!r} was read as a "
                    f"question about one application")

        for question in SINGULAR_STILL_PER_APP:
            got = parse_intent(question)
            ok = got == "submission_channel_check"
            print(f"  {'ok  ' if ok else 'FAIL'} {question[:52]:54s} "
                  f"{got:14s} expected submission_channel_check")
            if not ok:
                failures.append(f"routing: {question} -> {got}, "
                                f"expected submission_channel_check")

        # ── 2. derivation ───────────────────────────────────────────────────
        print("\n[2/4] derivation -- the stored channel matches can_channel()")
        rows = (await db.execute(text("""
            SELECT a.application_number, a.submission_channel, a.can_number,
                   l.source_name, l.camp_flag
            FROM applications a
            JOIN LATERAL (
                SELECT source_name, camp_flag, can_number
                FROM urban_application_log
                WHERE application_id = a.application_number
                ORDER BY last_updated_datetime DESC NULLS LAST
                LIMIT 1
            ) l ON true"""))).all()
        drift = [(r[0], r[1], can_channel(r[3], r[4])) for r in rows
                 if can_channel(r[3], r[4]) != r[1]]
        ok = not drift
        print(f"  {'ok  ' if ok else 'FAIL'} {len(rows)} applications re-derived, "
              f"{len(drift)} disagree with the stored channel"
              + (f" e.g. {drift[:3]}" if drift else ""))
        if not ok:
            failures.append(f"derivation: {len(drift)} rows drifted, e.g. {drift[:3]}")

        # every application has a channel, and its CAN is the right length
        counts = dict((await db.execute(text(
            "SELECT submission_channel, count(*) FROM applications GROUP BY 1"))).all())
        unknown = [c for c in counts if c not in CHANNELS]
        ok = not unknown
        print(f"  {'ok  ' if ok else 'FAIL'} channels present: {counts}"
              + (f" -- unknown {unknown}" if unknown else ""))
        if not ok:
            failures.append(f"derivation: unknown channels {unknown}")

        for channel, want in CAN_LENGTHS.items():
            # the lengths are our own constants, so inlining them keeps the
            # statement free of array-binding differences between drivers
            allowed = ", ".join(str(int(n)) for n in want)
            bad = (await db.execute(text(
                "SELECT application_number, can_number FROM applications "
                "WHERE submission_channel = :c AND can_number IS NOT NULL "
                f"AND length(can_number) NOT IN ({allowed})"),
                {"c": channel})).all()
            ok = not bad
            lens = " or ".join(str(n) for n in want)
            print(f"  {'ok  ' if ok else 'FAIL'} {channel}: "
                  f"{counts.get(channel, 0)} applications, CAN is {lens} digits"
                  + (f" -- offenders {bad[:3]}" if bad else ""))
            if not ok:
                failures.append(f"derivation: {channel} CAN length {bad[:3]}")

        # ── 2b. the rule against evidence it never looks at ─────────────────
        # can_channel() reads two columns. Three others record the same fact
        # independently, and a channel rule that disagrees with all of them is
        # wrong however tidy it looks:
        #
        #   ip_address           the Sub-Registrar's IGRS runs on the internal
        #                        10.236.251.x network; a counter dials in from
        #                        a public ISP address
        #   igrs_form6_number    raised only where IGRS built the mutation off
        #                        a registered deed
        #   can_number           a counter-issued CAN is the 133 series
        #
        # These are checked over the raw log rather than the projection, so a
        # future extract that breaks the pattern fails here rather than
        # silently mislabelling an officer's queue.
        print("\n[2b/4] the rule against the evidence it does not read")
        raw = (await db.execute(text("""
            SELECT source_name, camp_flag, ip_address, igrs_form6_number, can_number,
                   application_id
            FROM urban_application_log
            WHERE service_code IN ('0153', '0154', '0155')"""))).all()

        # One application in the extracts carries a placeholder CAN
        # (`123456789123`) instead of a counter number. Its channel is not in
        # doubt -- `tut_tct_t131_02` files 82 other rows -- so this is a damaged
        # value, not a misclassification, and it is named here rather than
        # waved through by a looser rule. A NEW contradiction still fails.
        _KNOWN_BAD_CAN = {"2023/0153/28/000327"}

        expected = {
            # channel:      (internal ip, has igrs form 6, CAN is 133 series)
            "sub_registrar": (True, True, False),
            "CSC": (False, False, True),
            "citizen": (False, False, False),
        }
        seen = {c: 0 for c in CHANNELS}
        contradictions = []
        for source_name, camp_flag, ip, igrs, can, app_id in raw:
            channel = can_channel(source_name, camp_flag)
            seen[channel] = seen.get(channel, 0) + 1
            digits = "".join(c for c in (can or "") if c.isdigit())
            got = (bool(ip and ip.startswith("10.236.251.")),
                   bool(igrs and igrs.strip()),
                   digits.startswith("133"))
            if got != expected[channel] and app_id not in _KNOWN_BAD_CAN:
                contradictions.append((channel, source_name, ip, igrs, can, got))
        ok = not contradictions
        print(f"  {'ok  ' if ok else 'FAIL'} {len(raw)} log rows: every derived "
              f"channel agrees with ip / igrs / CAN series {seen}"
              + (f" -- {len(contradictions)} contradict, e.g. {contradictions[:2]}"
                 if contradictions else ""))
        if not ok:
            failures.append(f"evidence: {len(contradictions)} rows contradict, "
                            f"e.g. {contradictions[:2]}")

        # The two citizen signals were once believed to name the same rows, and
        # can_channel() OR-ed them on that ground. They do not: the extracts set
        # `P` on `tut_tct_t131_01`, a counter account that files nine other rows
        # unflagged, with a 133-series CAN and a counter's public IP. That was
        # escalated and ruled on -- a bare mobile in the operator column is the
        # citizen signal; a camp flag on a counter-coded row is not.
        #
        # So what is checked now is the RULING, not the old assumption: every
        # camp-flagged row whose source_name is a counter code must derive as
        # CSC, and a self-filed row must derive as citizen whatever its flag.
        camp_on_counter = [(sn, cf) for sn, cf, *_ in raw
                           if (cf or "").strip().upper() == "P"
                           and not _looks_self_filed(sn)]
        wrong = [(sn, cf) for sn, cf in camp_on_counter
                 if can_channel(sn, cf) != "CSC"]
        ok = not wrong
        print(f"  {'ok  ' if ok else 'FAIL'} a camp flag on a counter account "
              f"stays CSC: {len(camp_on_counter)} such row(s)"
              + ("" if ok else f" -- derived as citizen: {wrong}"))
        if not ok:
            failures.append(f"camp flag overrode the counter code: {wrong}")

        self_filed = [(sn, cf) for sn, cf, *_ in raw if _looks_self_filed(sn)]
        wrong = [(sn, cf) for sn, cf in self_filed
                 if can_channel(sn, cf) != "citizen"]
        ok = not wrong
        print(f"  {'ok  ' if ok else 'FAIL'} a mobile in the operator column is "
              f"the citizen: {len(self_filed)} such row(s)"
              + ("" if ok else f" -- derived otherwise: {wrong}"))
        if not ok:
            failures.append(f"a self-filed row was not citizen: {wrong}")

        # ── 3. the filter returns what the register holds ───────────────────
        print("\n[3/4] filter -- get_officer_applications honours the channel")
        officer = (await db.execute(
            select(SISOfficer).order_by(SISOfficer.employee_id))).scalars().first()
        ctx = await build_context(db, officer)
        print(f"  officer: {officer.email} ({ctx.jurisdiction_name})")
        # Naming a channel widens the scope the way naming a period does: the
        # question is what the register holds for that channel, not what is on
        # the desk today (a channel query pinned to the current stage answered
        # "none found" to an officer holding 30 CSC files). So the partition is
        # checked against the register view -- every non-rejected application
        # of this officer, across all stages -- and not against the unfiltered
        # call, which is still the desk queue.
        total_unfiltered = await db.scalar(
            select(func.count()).select_from(Application).where(
                Application.assigned_officer_id == ctx.officer_id,
                Application.current_status != "rejected"))
        per_channel = {}
        for channel in CHANNELS:
            res = await postgres.get_officer_applications(
                db, ctx, submission_channel=channel)
            n = res.get("total_count", len(res.get("applications", [])))
            per_channel[channel] = n
            # the handler must never hand back a row from another channel
            wrong = [a for a in res.get("applications", [])
                     if channel and a.get("submission_channel") not in (None, channel)]
            ok = not wrong
            print(f"  {'ok  ' if ok else 'FAIL'} channel={str(channel):14s} "
                  f"{n:3d} applications"
                  + (f" -- leaked {wrong[:2]}" if wrong else ""))
            if not ok:
                failures.append(f"filter: {channel} leaked {wrong[:2]}")
        summed = sum(per_channel.values())
        ok = summed == total_unfiltered
        print(f"  {'ok  ' if ok else 'FAIL'} the three channels partition the "
              f"queue: {summed} vs {total_unfiltered}")
        if not ok:
            failures.append(f"filter: {summed} != {total_unfiltered}")

        # ── 4. answers ──────────────────────────────────────────────────────
        print("\n[4/4] answers -- the handler names the right channel")
        if fast:
            print("  (skipped: --fast)")
        else:
            import uuid

            from backend.services.chatbot import process_chat

            # one application per channel, inside this officer's jurisdiction
            samples = {}
            for channel in CHANNELS:
                row = (await db.execute(text("""
                    SELECT a.application_number FROM applications a
                    JOIN survey_numbers s ON s.id = a.survey_number_id
                    JOIN blocks b ON b.id = s.block_id
                    JOIN officer_jurisdictions j ON j.officer_id = :o
                    WHERE b.ward_id = j.ward_id AND a.submission_channel = :c
                    LIMIT 1"""), {"o": officer.id, "c": channel})).scalar()
                if row:
                    samples[channel] = row
            missing = [c for c in CHANNELS if c not in samples]
            if missing:
                print(f"  note  no application in this officer's ward for {missing}"
                      f" -- those cases are skipped, not failed")

            for channel, app_no in samples.items():
                for template in PER_APP + [CAN_Q]:
                    question = template.format(app=app_no)
                    res = await process_chat(question, str(uuid.uuid4()), ctx, db, [])
                    answer = str(res.get("response") or "")
                    said = _says(answer, EXPECT[channel])
                    lied = _says(answer, FORBID[channel])
                    ok = said and not lied
                    print(f"  {'ok  ' if ok else 'FAIL'} [{channel:13s}] "
                          f"{question[:48]:50s} {answer[:60]!r}")
                    if not ok:
                        failures.append(
                            f"answer: [{channel}] {question} -> {answer[:120]!r}"
                            + (" (named the wrong channel)" if lied else ""))

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1
    print("all channel questions route, derive and answer correctly")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
