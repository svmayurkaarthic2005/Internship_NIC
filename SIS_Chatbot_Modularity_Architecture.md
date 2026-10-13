# SIS Chatbot — Modularity Architecture (current state)

Scope note: this document reflects what was actually extracted this session
(`rag_language.py` out of `rag.py`), not the full target architecture the
refactor spec proposes. See `SIS_Chatbot_Modularity_Refactor_Report.md` for
why the scope was limited and what remains.

## Module dependency diagram (current)

```
backend/services/
    rag_language.py   ─┐  (NEW) pure functions: detect_language,
                        │  is_short_reply, get_prior_turn_language,
                        │  _explicit_tamil_request. Depends only on
                        │  backend.utils.fuzzy (is_token_typo_match,
                        │  normalize_text) + stdlib (re, typing).
                        │  No DB. No LLM. No import of rag.py or chatbot.py.
                        │
                        ▼
    rag.py             ── facade-imports the 4 names above from
                           rag_language.py and re-exports them at module
                           level (`from backend.services.rag_language import
                           detect_language, is_short_reply,
                           get_prior_turn_language, _explicit_tamil_request`).
                           Everything else in rag.py (retrieval, intents,
                           comparisons, entity extraction, rendering,
                           prompts, LLM calls -- ~8,400 remaining lines, 68
                           functions) is UNCHANGED and still lives here.
                        │
                        ▼
    chatbot.py         ── imports detect_language, is_short_reply,
                           get_prior_turn_language directly from rag.py
                           (`from backend.services.rag import (...)`,
                           unchanged import statement, lines 77-114).
                           ~32,450 lines, 430 functions, entirely untouched
                           this session.
```

No circular import: `rag_language.py` has zero knowledge of `rag.py` or
`chatbot.py`. `rag.py` depends on `rag_language.py`. `chatbot.py` depends on
`rag.py` (as before). Dependency direction is strictly one-way.

## Responsibility map

| Module | Owns | Does NOT own |
|---|---|---|
| `rag_language.py` | Tamil/Tanglish/English detection (`detect_language`), explicit "answer in Tamil" request parsing with clause-aware negation (`_explicit_tamil_request`), short reply classification (`is_short_reply`), recovering the active language from recent turns (`get_prior_turn_language`) | Transliteration (`backend/utils/translit.py`, untouched), the `_TA_NB`/`_TA_NA` Tamil word-boundary regex helper (used by ~8 unrelated call sites throughout `rag.py` for entity matching, not language detection -- correctly left in `rag.py`) |
| `rag.py` (post-extraction) | RAG/pgvector retrieval (`get_rag_context[_async]`), structured-DB-to-LLM formatting, HTML/table response building (`build_html_response`), prompt assembly (`build_prompt`), LLM calls (`call_llama[_stream]`, `paraphrase_facts_naturally`), intent parsing (`parse_intent`, `_parse_intent_core`), comparison-query parsing (`parse_comparison_query`), entity extraction (survey/application/ward/block/town/taluk/district numbers, date ranges, month scopes, submission channels) | Language detection (moved out) |
| `chatbot.py` (unchanged) | Main orchestration (`process_chat`, `process_chat_stream`), service-code lookup and comparison, attachment routing, clarification/negative-correction handling, follow-up/continuation logic, ~25 per-topic `*_qa.py` record modules it coordinates, response persistence | Nothing moved out this session |

## Why `_TA_NB`/`_TA_NA` stayed in `rag.py`

These two regex-boundary constants (`r'(?<![஀-௿])'`, `r'(?![஀-௿])'`) look
language-related by name, but tracing actual call sites
(`grep -n "_TA_NB\|_TA_NA" backend/services/rag.py`) shows 8 unrelated uses
scattered across entity-extraction functions (ward/block reference parsing,
survey-number reference parsing, "what happens next" phrasing, "show/list"
Tamil verb matching) that have nothing to do with language *detection* -- they
are a general Tamil-script word-boundary workaround used wherever a regex
needs to not match mid-word in Tamil (the `\b` metacharacter doesn't work
reliably there because a virama-marked consonant's trailing combining mark is
not `\w`). Moving them into `rag_language.py` would have created a dependency
from unrelated entity-extraction code back into the language module for a
two-line regex utility -- exactly the kind of "move things because they sound
related" the refactor spec explicitly warns against (Phase 3: "Do not move
arbitrary groups of functions merely to reduce the line count"). They remain
in `rag.py`, duplicated nowhere.

## Compatibility measures taken

- `rag.py` still exposes `detect_language`, `is_short_reply`,
  `get_prior_turn_language` at module level, as the **same function objects**
  `rag_language.py` defines (verified: `rag.detect_language is
  rag_language.detect_language` → `True`).
- `chatbot.py`'s existing `from backend.services.rag import (detect_language,
  is_short_reply, get_prior_turn_language, ...)` (lines 77-114) required **no
  edit** -- the facade re-export makes the move invisible to that import.
- Test files that import directly from the facade path
  (`test_tanglish_language.py`: `from backend.services.rag import
  detect_language, parse_intent`; `test_tanglish_to_tamil_policy.py`: same
  pattern) required **no edit** and were re-run unchanged as part of the
  verification.
- `_explicit_tamil_request` (private, underscore-prefixed) is also re-exported
  from the facade, since nothing in the spec says private names are exempt
  from the compatibility requirement and no caller outside `rag_language.py`
  itself was found to need it removed.

## What remains unmodularized (explicitly out of scope this session)

Per the user's own scoping decision (see report), nothing below was attempted:

- `rag.py`'s other 7 proposed modules (retrieval, intents, comparisons,
  entities, rendering, prompts, LLM transport) -- 68 functions / ~8,400 lines
  still live in one file.
- All of `chatbot.py`'s proposed module split (routing, followups, service
  comparison, application queries, attachment routing, response) -- 430
  functions / ~32,450 lines, entirely untouched.

A later pass should repeat this exact methodology (trace real call sites,
confirm a function's actual dependency boundary rather than its name, extract
one cohesive piece, re-run the full baseline, verify facade identity) for the
next candidate -- the entity-extraction block (`extract_survey_number`,
`extract_application_number(s)`, `extract_ward_number`, `extract_block_number`,
`extract_town_name`, `extract_taluk_name`, `extract_district_name`,
`extract_month_scopes`, `extract_date_range`, ...) is the next most
self-contained candidate in `rag.py`: all pure parsing functions, no DB/LLM
dependency, called extensively from `chatbot.py` the same way the language
functions were.
