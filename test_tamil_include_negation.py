"""Regression test for a Tamil exclusion verb neg_scope.py never recognised:

"<X>-ACC சேர்க்காதே" / "சேர்க்க வேண்டாம்" / "சேர்க்காமல்" -- "don't ADD/include X" --
is how an officer naturally asks to EXCLUDE X, but every existing exclusion cue in
neg_scope.py (`_SUFFIX`) only matches a negation particle (இல்லாத/தவிர/வேண்டாம்/...)
glued straight onto the term itself. Here the officer names a separate verb
("சேர்க்க", to add/include) with its own negative conjugation, so the term and the
negation sit in two different words and `_SUFFIX` never fires.

Found live: "0158 ... விண்ணப்பங்களைக் காட்டு; நிராகரிக்கப்பட்டவற்றைச் சேர்க்காதே; ..."
("show 0158 applications; don't include the rejected ones; ...") was answered with
the OPPOSITE of what was asked -- only the 2 rejected 0158 applications, with no
acknowledgement of the exclusion at all. Fixed with a new `_SUFFIX_VERB` regex,
applied in `parse()` ahead of `_SUFFIX`.

No DB or LLM needed for these checks.
"""
import backend.services.neg_scope as ns

bad = []
total = 0


def check(label, cond, detail=None):
    global total
    total += 1
    if not cond:
        bad.append((label, detail))
    print(("ok  " if cond else "FAIL") + f" {label}")


# ---- the live-failing case and its direct variants ----
_cases = [
    "நிராகரிக்கப்பட்டவற்றைச் சேர்க்காதே",
    "நிராகரிக்கப்பட்ட விண்ணப்பங்களை சேர்க்காமல் காட்டு",
    "நிராகரிக்கப்பட்டவற்றை சேர்க்க வேண்டாம்",
]
for m in _cases:
    ex, residual = ns.parse(m)
    check(f'"{m}" excludes "rejected"', ex.get("status") == {"rejected"}, ex)

# ---- Tanglish transliteration of the same verb ----
ex, _ = ns.parse("rejected applications serkadhe")
check('"...applications serkadhe" (Tanglish) excludes "rejected"',
      ex.get("status") == {"rejected"}, ex)

# ---- the exact live compound sentence, end to end through parse() ----
_compound = ("0158 சேவை குறியீட்டின் விண்ணப்பங்களைக் காட்டு; "
             "நிராகரிக்கப்பட்டவற்றைச் சேர்க்காதே; "
             "துணைப்பிரிவு பதிவு இல்லாதவற்றை மட்டும் காட்டு, "
             "அவற்றின் இரண்டாவது விண்ணப்பத்தின் எண்ணையும் குறிப்பிடு.")
ex, residual = ns.parse(_compound)
check("the live compound sentence still excludes \"rejected\" once service-code "
      "and field-presence clauses surround it", ex.get("status") == {"rejected"}, ex)

# ---- the positive ("include X too") form must NOT be read as an exclusion ----
ex, _ = ns.parse("நிராகரிக்கப்பட்டவற்றையும் சேர்த்துக் காட்டுங்கள்")
check('"include rejected too" (சேர்த்து, not சேர்க்காதே/வேண்டாம்) is NOT an exclusion',
      not ex.get("status"), ex)

# ---- an unrelated "சேர்க்க" (include) with no negation stays unflagged ----
ex, _ = ns.parse("அங்கீகரிக்கப்பட்டவற்றை சேர்க்க வேண்டும்")
check('"சேர்க்க வேண்டும்" (DO include -- positive, not வேண்டாம்) is NOT an exclusion',
      not ex.get("status"), ex)

print(f"{total - len(bad)}/{total} ok")
import sys
sys.exit(1 if bad else 0)
