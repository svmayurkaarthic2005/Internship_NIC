"""The spelling-correction pass must fix typos without touching real words.

`followup_context.correct_spelling()` rewrites near-miss words to the follow-up
vocabulary so a misspelled fragment is still recognised as the follow-up it is.
That is only safe while it leaves real words alone, and edit distance on its own
does not know the difference: measured over the question corpora in this repo,
correcting on distance alone rewrote 121 real words --

    field -> filed      (one transposition from a submission-channel cue; this
                         is the one that broke "show field visit", which came
                         back as "That answer covered 24 applications. Which one
                         do you mean?" instead of the officer's visits)
    bank -> back,  state -> stage,  change -> charge,  data -> date

So the corrector consults the department's own document corpus as its
dictionary. This suite holds it to both halves of the bargain.

Run:
    python test_spelling_safety.py
"""
from __future__ import annotations

import glob
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services import followup_context as fctx  # noqa: E402

FAILURES: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if detail and not ok:
        print(f"        {detail}")
    if not ok:
        FAILURES.append(label)


def section(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


def main() -> int:
    section("1. Real questions pass through untouched")
    # Every question this repo keeps, in every corpus file. These are real
    # phrasings an officer would type, so not one word of them may be rewritten.
    corpora = sorted(glob.glob(str(ROOT / "test_questions_*.txt"))
                     + glob.glob(str(ROOT / "backend" / "documents" / "*.txt")))
    check(bool(corpora), "the question corpora are present", str(len(corpora)))

    scanned = 0
    mangled: dict[str, str] = {}
    for path in corpora:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            q = line.strip()
            if not q:
                continue
            scanned += 1
            fixed = fctx.correct_spelling(q)
            if fixed != q:
                for a, b in zip(q.split(), fixed.split()):
                    if a != b:
                        mangled.setdefault(a.lower(), b.lower())
    print(f"        scanned {scanned} lines across {len(corpora)} files")
    check(not mangled, "no real word is rewritten",
          "; ".join(f"{a!r}->{b!r}" for a, b in list(mangled.items())[:15]))

    section("2. The words that were being mangled, named")
    # Spelled out so each regression is named rather than merely covered by the
    # sweep above. `field` is first because it is the one an officer hit.
    for word in ("field", "bank", "state", "change", "data", "mode", "set",
                 "come", "move", "core", "year", "court", "approve", "reject"):
        check(fctx.correct_spelling(word) == word, f"{word!r} survives",
              fctx.correct_spelling(word))
    for phrase in ("show field visit", "show field visits", "field visit",
                   "what is the bank name", "what is the workflow state",
                   "change the payment mode"):
        check(fctx.correct_spelling(phrase) == phrase,
              f"{phrase!r} survives", fctx.correct_spelling(phrase))

    section("3. ...while genuine typos are still corrected")
    for typo, want in (("applciaiton", "application"),
                       ("aplicant", "applicant"),
                       ("aproved", "approved"),
                       ("oldst", "oldest"),
                       ("thier", "their"),
                       ("frist", "first"),
                       ("secnd", "second"),
                       ("detials", "details"),
                       ("staus", "status")):
        got = fctx.correct_spelling(typo)
        check(got == want, f"{typo!r} -> {want!r}", got)

    section("4. 'show field visit' is a fresh question, not a follow-up")
    # The bug as the officer met it: a 24-row listing on screen, then a complete
    # request for their field visits, answered with "which one do you mean?".
    listing = fctx.FollowupContext(
        entity=fctx.ENTITY_APPLICATION_LIST,
        application_numbers=[f"X/{i}" for i in range(1, 25)])
    for msg in ("show field visit", "show field visits", "field visit",
                "show my field visits",
                # A plural "field visits" names its own subject. This one was
                # answered "2 of those 2 application(s) are pending" -- scoped
                # to the application list on screen, and reporting the wrong
                # status to a question that said "completed".
                "how many field visits are completed",
                "which field visits are pending",
                "how many inspections are overdue"):
        res = fctx.resolve(msg, listing, "en")
        check(not res.ambiguous and not res.resolved,
              f"{msg!r} is left to the intent router",
              f"ambiguous={res.ambiguous} resolved={res.resolved} "
              f"kind={fctx.classify(msg)}")

    section("4b. ...but a SINGULAR field-visit question is still a follow-up")
    # "is the field visit scheduled?" after an application answer is about that
    # application. Making the plural a subject must not sweep this in with it.
    one = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION,
                               application_numbers=["2026/0154/28/001167"])
    for msg in ("is the field visit scheduled?", "when was it scheduled"):
        res = fctx.resolve(msg, one, "en")
        check(res.resolved and res.application_number == "2026/0154/28/001167",
              f"{msg!r} still resolves to the application in view",
              f"resolved={res.resolved} num={res.application_number}")
    # "was the field visit done" carries no field cue at all, so it has never
    # been a resolvable follow-up -- it falls to the intent router and is
    # answered as a field-visit question. Recorded here so the distinction is
    # visible rather than looking like a gap the plural rule opened: what
    # matters is that the SINGULAR form is not swept up as its own subject.
    check(not fctx._has_own_subject("was the field visit done"),
          "a singular 'field visit' is never treated as its own subject")

    section("5. The dictionary is real, and a missing one is survivable")
    known = fctx._known_words()
    check(len(known) > 500, "the document corpus yields a usable word set",
          f"{len(known)} words")
    for w in ("field", "bank", "state", "survey", "patta", "tahsildar"):
        check(w in known, f"{w!r} is in the corpus dictionary")

    print("\n" + "=" * 68)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
