# Why long answers were getting truncated — findings + fixes

## Root cause: the prompt filled the context window

Every LLM fallback answer is generated with `num_ctx = 8192` (input + output
tokens **combined**). The prompt that gets built is large:

| part | approx tokens |
|---|---|
| `build_prompt` system instruction (rag.py, ~180 lines of rules) | ~1,800 |
| conversation history — up to 10 turns, 500 chars each | ~1,250 |
| RAG context — `n_results = 8` chunks × `chunk_size = 2000` chars | ~4,000 |
| `format_structured_data_for_llm` summary | 100–400 |
| **total input** | **~7,200–7,500** |

That left **~700–1,000 tokens** for the answer, regardless of
`num_predict = 1024`. A "list every service code" or "explain the full ISD
workflow" answer needs 2–4× that, so llama3.1:8b stopped mid-sentence — and
when the input alone crossed 8,192, Ollama silently dropped the *tail* of the
prompt, which is the user's question and the `ASSISTANT RESPONSE:` cue.

Two secondary causes:

- **`is_specific_field_query` over-clamped.** In `build_prompt`, any question
  containing a field word (`status`, `date`, `stage`, `name`, `survey number`,
  …) plus a wh-word triggered "DIRECT ANSWER MODE — write 1-2 plain
  sentences". So *"explain each stage of the application status"* was forced
  into two sentences.
- **No timeout on `call_llama`.** A long generation on CPU can outrun the
  client / reverse-proxy timeout and hang the request with nothing rendered.

## Fixes applied

| file | change |
|---|---|
| `backend/config.py` | `LLM_NUM_CTX` 8192 → **16384** (whole prompt + full output room); `LLM_NUM_PREDICT` 1024 → **2048**; new `LLM_TIMEOUT_SECONDS = 120.0` |
| `backend/services/rag.py` `call_llama` | wrap `llm.ainvoke` in `asyncio.wait_for(timeout=LLM_TIMEOUT_SECONDS)`; on timeout return a readable "taking longer than expected" message instead of hanging |
| `backend/services/rag.py` `build_prompt` | new `wants_detail` check — a question that asks for depth ("explain", "in detail", "list all", "step by step", "each stage", "walk me through", Tamil "விளக்கு / விரிவாக / படிப்படியாக" …) now **suppresses** DIRECT ANSWER MODE even when `direct_answer=True` was passed |
| `backend/services/chatbot.py` | general-path RAG retrieval `n_results` 8 → **6** (8 mostly added overlap-duplicated text and diluted an 8b model's focus while eating answer room) |

Net effect for a genuinely long question: input ≈ 5,500–6,000 tokens, leaving
~10k tokens of headroom, and `num_predict` raised to 2,048 so the model is
allowed to use it.

`py_compile` clean on all three files. No test pins these values (grep-checked
`test_service_code_queries.py`, `test_intent_coverage.py`).

## Tradeoff to know

`num_ctx = 16384` roughly doubles the KV-cache RAM llama3.1:8b uses (~+1 GB)
and adds a little prompt-eval time. If the box is memory-constrained, 12288
still fixes the described truncation (7k prompt + 2k output fits with margin) —
change the one line in `config.py`.

## Not changed — worth doing later

- **The `build_prompt` system instruction is ~1,800 tokens** and repeats the
  same rules (no fabrication, ISD/NISD definitions, no technical terms) three
  or four times. Halving it would give back context room on *every* call and,
  on an 8b model, sharpen instruction-following. Left alone here because the
  wording is load-bearing for several shipped test suites.
- **History is capped at 500 chars/message.** A long prior assistant answer is
  cut to 500 chars + "[truncated]", so a follow-up on a detailed previous
  answer loses most of it. Consider 1,200 for assistant turns now that there
  is context room.
- **`call_llama_stream` has no pre-first-token guard.** Streaming degrades
  gracefully (partial text shows), but a model that hangs before the first
  token leaves the officer on a spinner. A `wait_for` on the first `astream`
  chunk would close that.
