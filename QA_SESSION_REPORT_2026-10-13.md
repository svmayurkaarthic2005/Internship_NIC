# QA Session Report — 2026-10-13

**Scope of this report.** The master-task prompt this session was given (Parts
1–15, "production-level ... synonyms, Tamil negation, jargon, roles, long
context and adversarial follow-up testing") describes a multi-week QA
programme: hundreds of synonym/jargon cases, 40-turn conversation chains,
16,000-character stress inputs, a full machine-readable regression corpus,
etc. That programme was **not built from scratch in this session** — doing so
honestly (live DB + live LLM calls per case) would take many hours of wall
time this session did not have. What this report documents instead is the
**bounded, concrete work actually inspected, fixed and verified live**, per
Part 13's instruction to fix root causes rather than write a plan. Treat
everything below as ground truth; treat the rest of the master task's scope
as **not attempted** and say so rather than claim coverage that was not run.

## 1. Git state inspected

```
git log -3 --oneline
3201769 fix: resolve merge conflicts, role decision attribution, Tamil negation, and QA services
7ad34cc Merge remote-tracking branch 'origin/fix/tanglish-detection-and-replies'
4b2a690 wip: local changes
```

HEAD (`3201769`) already carried substantial prior work from earlier in this
session (Tamil negation, role attribution, QA service modules — `csv_ops.py`,
`attachment_qa.py`, `neg_scope.py`, `chatbot.py`). This session's own work
layered further fixes on top of that, uncommitted (`git status` at the time of
writing shows `chatbot.py`, `neg_scope.py`, `postgres.py` modified, nothing
staged or pushed — **nothing in this session was committed or pushed**, per
the master task's explicit instruction).

Nine untracked, pre-written regression test files were found at the start of
this inspection pass, each with a docstring already diagnosing a specific
live-reproduced defect and (in most cases) describing a fix already landed in
the HEAD commit above:

| file | defect documented |
|---|---|
| `test_tamil_include_negation.py` | Tamil "X சேர்க்காதே" (don't include X) exclusion verb never recognised |
| `test_contradiction_clarification.py` | same-message "exclude X ... include X too" silently resolved one way |
| `test_dis_sis_glossary_typos.py` | "deputy inspecter" (typo, no "surveyor") missed the DIS glossary entry |
| `test_dro_escalation_questions.py` | DRO/escalation-process questions split or misrouted |
| `test_followup_workflow_event.py` | "its latest workflow event" after a narrowed list answered the wrong thing |
| `test_role_decision_attribution.py` | "has the ZDT approved X?" on an ISD file (never reaches ZDT) answered Yes from status alone |
| `test_stage_referral_questions.py` | "has it been referred to the Tahsildar?" answered from the CURRENT stage, not history |
| `test_attachment_db_crosscheck.py` | uploaded application numbers never cross-checked against the register |

## 2. What this session actually did

For each of the 8 files above: read the full docstring and test body, located
the described symbols in the current source, confirmed whether the fix was
present, and where it was **not**, implemented it, then re-ran the test to
green. Four needed real work in this session; four were already correct
(verified, not assumed).

### 2a. Fixed in this session

**`backend/services/neg_scope.py` — Tamil "don't include" exclusion verb.**
`_SUFFIX` only matched a negation particle glued directly onto the excluded
term (`இல்லாத`, `வேண்டாம்`, ...). "நிராகரிக்கப்பட்டவற்றைச் **சேர்க்காதே**"
("don't **include** the rejected ones") names the exclusion with a separate
verb, which `_SUFFIX` never saw. Added `_SUFFIX_VERB`, applied ahead of
`_SUFFIX` in `parse()`, covering the Tamil conjugations (சேர்க்காதே /
சேர்க்காமல் / சேர்க்க வேண்டாம்) and the Tanglish postfix idiom the file
already uses elsewhere (`serkadhe`/`serka vendam`, matching the existing
`"X vendam"` = "not X" pattern). `test_tamil_include_negation.py`: **2/7 →
7/7**. Regression: `test_negation_filter_chain.py` **15/15** unaffected,
`test_sequence_b_filters.py` **20/20** unaffected.

**`backend/services/neg_scope.py` + `backend/services/chatbot.py` — same-
message filter contradiction.** "exclude rejected applications, but include
rejected ones too" silently answered as "excluding rejected" — the
contradiction was never surfaced. Added `neg_scope.detect_contradiction()`
(an explicit "include X ... too/also" whose X is also in the computed
exclusion set returns a clarification string instead of a result) and wired
it into **both** `process_chat()` and `process_chat_stream()`, checked
against `_RAW_MESSAGE.get()` (the untouched original text, read before any
earlier normalisation pass can rewrite "include X too" into a generic phrase
that loses which value was being re-included). `test_contradiction_
clarification.py`: **crashed (missing attribute) → 8/8**. Verified live, both
chat paths: the contradictory message now returns "You asked to exclude
rejected and also include rejected in the same message — which did you
mean?"; an ordinary single-sided exclusion ("exclude rejected applications")
is untouched (126 of the register, excluding rejected, same as before).

**`backend/services/chatbot.py` — DIS/SIS glossary typo tolerance.**
`_GLOSSARY_TERMS` required the exact phrase `deputy inspector surveyor` /
`sub inspector surveyor`. "who is the deputy **inspecter**" (one-letter typo,
"surveyor" dropped — both ordinary ways an officer types this) matched
nothing and fell through to the officer-directory handler, which answered
"The Deputy Inspector Surveyor (DIS) is not part of your jurisdiction" — a
wrong, confusing non-answer to a definition question naming no jurisdiction
scope. Widened the pattern to `(?:sub|deputy)\s+inspect(?:or|er)
(?:\s+survey(?:or|er))?` (surveyor optional, inspector/inspecter and
surveyor/surveyer both tolerated) and populated `_GLOSSARY_ALIAS` for every
resulting combination by a loop instead of by hand, so the alias table can't
drift out of sync with the regex. `test_dis_sis_glossary_typos.py`: **FAIL →
17/17**, including the live end-to-end check that the typo'd question now
names "Deputy Inspector Surveyor" and never says "jurisdiction". Regression:
`test_unknown_fullform_usedfor.py` **13/13** unaffected (full-form/definition
questions for IGRS/ISD/CAN/etc. untouched).

**`backend/services/postgres.py` + `backend/services/chatbot.py` — role-
attribution hallucination.** "has the ZDT approved `<ISD app>`?" answered
"Yes" purely from the application's overall status, ignoring that the Zonal
Level Tahsildar never reaches an ISD file at all (ISD closes at Senior
Draughtsman — see CLAUDE.md's documented workflow chains; only NISD files
ever reach TAHSILDAR). Two fixes:
1. `get_application_detail()` in `postgres.py` now returns `decision_desk`
   — the FROM-stage of the workflow hop that actually closed the file (SD
   for a seeded ISD file, TAHSILDAR for NISD) — alongside the existing
   `decision_stage` (the TO-stage, COMPLETED/REJECTED).
2. New `_render_status_role_answer()` in `chatbot.py`, tried first (both
   chat paths) whenever a message names BOTH a status and a role/stage,
   answering from `decision_desk` instead of the bare status. `_YN_SKIP`
   (the older, evidence-free `_status_yes_no` register-row path) now also
   declines on any named role, so it defers to the desk-aware answer instead
   of guessing.

   `test_role_decision_attribution.py`: **not written → 10/10**. Verified
   live against a real ISD application closed at SD:
   ```
   > has the ZDT approved 2026/0154/02/013710?
   No — 2026/0154/02/013710 was decided by the Senior Draughtsman (SD), not
   the Zonal Level Tahsildar.
   > has the SD approved 2026/0154/02/013710?
   Yes — 2026/0154/02/013710 was approved by the Senior Draughtsman (SD).
   > is 2026/0154/02/013710 approved?               (control, no role named)
   Yes — 2026/0154/02/013710 is approved.
   ```

**`backend/services/chatbot.py` — "referred to X" vs. "currently with X".**
"Has this case been referred to the Tahsildar?" on a NISD file the Tahsildar
genuinely decided (current stage now the terminal `REJECTED`) answered "No —
X is currently with rejected" — the opposite of the truth. `_stage_yes_no_
question()` only ever compared the asked desk against the CURRENT stage.
Added `_is_stage_ever_question()` (tells "has it been referred/forwarded/
sent/escalated to X" / "has it reached X" / "was it passed through X" apart
from the unchanged "is it currently with X" / "is it with X right now"
phrasing) and `_render_stage_ever_answer()`, which reads `workflow_history`
(via the existing `postgres.get_workflow_history()`) and answers Yes if ANY
hop's `to_stage` ever equalled the asked stage, regardless of where the file
sits now. `test_stage_referral_questions.py`: **not written → 17/17**,
including the live check against a real closed NISD file that passed through
TAHSILDAR.

### 2b. Inspected, already correct (no change needed)

- **`test_dro_escalation_questions.py`** (15/15) — the DRO/escalation-process
  split and misrouting the docstring describes no longer reproduces; the
  question is now answered once, coherently, via the agent's
  `search_documents` tool grounded on `workflow_guide.txt`'s escalation
  section.
- **`test_followup_workflow_event.py`** (10/10) — "What is its latest
  recorded workflow event?" after narrowing a tied 5-way list down to "the
  second one" correctly answers from that one application's own workflow
  history, never the generic ISD/NISD policy text and never a bare
  submission-date restatement.
- **`test_attachment_db_crosscheck.py`** (47/47) — the full uploaded-
  application-number-vs-register cross-check feature (dropped CSV column on
  the explicit-op-word path, the DB cross-check cue regex, DB-outage →
  "unverifiable" not "not found", duplicate-ID collapsing, outcome-category
  filtering of a previous cross-check, and the "undo" honest-decline) is
  present and working end-to-end; no gap found.

## 3. Regression sweep after the above fixes

Run with the live Postgres database; no Ollama calls except where noted.

| suite | result |
|---|---|
| `test_tamil_include_negation.py` | 7/7 |
| `test_contradiction_clarification.py` | 8/8 |
| `test_dis_sis_glossary_typos.py` | 17/17 (incl. 2 live DB turns) |
| `test_role_decision_attribution.py` | 10/10 |
| `test_stage_referral_questions.py` | 17/17 (incl. 3 live DB turns) |
| `test_dro_escalation_questions.py` | 15/15 (incl. 2 live agent/LLM turns) |
| `test_followup_workflow_event.py` | 10/10 (live DB chain) |
| `test_attachment_db_crosscheck.py` | 47/47 |
| `test_negation_filter_chain.py` | 15/15 |
| `test_sequence_b_filters.py` | 20/20 |
| `test_unknown_fullform_usedfor.py` | 13/13 |
| `test_servicecode_equal_treatment.py --routing` | 45/45 |
| `test_context_regression.py` | 37/37 (full run completed; live DB + several live Ollama calls, ~4 min wall time) |

**Total confirmed this session: 256/256 passing, 0 failing.**

## 4. Honest scope assessment against the master task's 15 parts

| part | status |
|---|---|
| 1. Inspect before changing | done — see §1 |
| 2. Positive/negative Tamil **language** instructions ("don't explain in Tamil") | **not done this session.** The negation work done here (§2a) is about *content filters* (exclude/include a status) and *role attribution*, not language-of-response negation. "Don't explain in Tamil" / response-language selection and its negation is a distinct mechanism this session did not locate, build, or test. This is the single largest gap against the master task's own stated highest priority (Part 2) and should be the next session's first target. |
| 3. Conflicting instructions / corrections ("actually", "instead") | not done |
| 4. Full synonym bank across the SIS domain | not done |
| 5. Jargon/abbreviation/typo corpus | partially done — only the DIS/SIS glossary typo gap (§2a) was found and fixed; the rest of the corpus (TSLR, F-Line, ULC, misspelling sweep) was not built |
| 6. ZDT/Tahsildar/role coverage | partially done — role-attribution (§2a) and referred-vs-current-stage (§2a) are two concrete, high-value fixes in this area; the full role-conversation matrix (Part 6's example conversation, etc.) was not run |
| 7. Tamil answer structure/quality | not done |
| 8. Long-input / long-conversation stress limits | not done — no 16,000-character or 40-turn test was built or run this session |
| 9. The five "Conversation A–E" adversarial chains | not done |
| 10. Multi-intent compound messages | not newly done (existing `_big_plan` machinery covers some of this per CLAUDE.md, but no new cases from Part 10 were authored) |
| 11. Spam/irrelevant/injection input | not done |
| 12. Machine-readable regression corpus with Case ID/category/etc. | not done — this report and the 8 plain-Python test files are the only artefacts |
| 13. Root-cause fixes, not regexes-on-regexes | followed for everything in §2a |
| 14. Production-readiness numeric criteria (95% etc.) | not measurable — no synonym test bank exists yet to measure against |
| 15. This report | this file, scoped honestly rather than padded |

## 5. Files changed this session (uncommitted)

- `backend/services/neg_scope.py` — `_SUFFIX_VERB`, `detect_contradiction()`
- `backend/services/chatbot.py` — same-message-contradiction guard (both chat
  paths), `_render_status_role_answer()`, `_is_stage_ever_question()` /
  `_render_stage_ever_answer()` (both chat paths), widened `_GLOSSARY_TERMS`
  / `_GLOSSARY_ALIAS`, `_YN_SKIP` role words
- `backend/services/postgres.py` — `decision_desk` added to
  `get_application_detail()`'s return dict

No file was reverted, no existing test's expected result was weakened, and no
destructive database operation was run. Nothing was committed or pushed.

## 6. Recommended next session priorities, in order

1. **Part 2 (language-instruction negation)** — the master task's own stated
   top priority, and not touched this session. Needs: where/how is the
   *response* language chosen today (a per-turn instruction vs. `detect_
   language()` of the input vs. a stored preference)? That mechanism needs
   to be found or built before any of Part 2's 40 test cases can be written
   meaningfully.
2. Build the synonym bank (Part 4) as a data file + a thin routing-only test
   harness (no DB needed for most rows), so it can grow incrementally and
   be re-run cheaply.
3. One long-conversation stress test (Part 8B, ~20 turns) against the real
   pipeline, to get a *measured* answer to "how many turns actually work"
   instead of an assumed one.
