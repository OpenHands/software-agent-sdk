# Evidence: per-call prompt composition on live agent runs

> `.pr/` is PR-only reviewer context per repository policy (`.github/workflows/pr-artifacts.yml`);
> this directory is removed after PR approval. Nothing here is meant to merge to `main`.

Per-call records from live runs of the default agent (`get_default_agent`, 19 tools,
browser-on) on tasks from the published harness-benchmark short suite
(<https://github.com/rajshah4/harness-benchmark>). Each `calls.jsonl` row carries the
`PromptComposition` estimate and the provider-reported `TokenUsage` for one LLM call
(counts and response ids only; no message content, no credentials).

## Lanes

- `minimax-m3/` — `openai/MiniMax-M3` (chat completions; litellm has no tokenizer mapping for
  this model, so estimates use litellm's fallback tokenizer). Tasks p09-task-01 (trivial
  rename, 5 calls), p09-task-07 (medium refactor, 26 calls), p09-task-10 (hard cache
  implementation, 10 calls).
- `gpt-4o-mini/` — `gpt-4o-mini` (chat completions; litellm maps this model to its real
  `o200k_base` tokenizer — verified by comparing `token_counter` framing overhead against
  `tiktoken.get_encoding("o200k_base")` vs `cl100k_base` on divergent inputs). Tasks
  p09-task-01 (9 calls, verifier PASS) and p09-task-07 (28 calls, verifier FAIL — the model
  left a syntax error in the repo; a completed run, labeled a model-quality failure).

## gpt-4o-mini summary (mapped tokenizer)

| task | calls | avg system | avg tools | avg history | avg latest | avg provider input | est/provider median |
|---|---:|---:|---:|---:|---:|---:|---:|
| p09-task-01 | 9 | 3,340 | 5,702 | 987 | 175 | 10,231 | 1.00 |
| p09-task-07 | 28 | 3,340 | 5,702 | 18,357 | 792 | 28,739 | 0.98 |

- Per-call est/provider ratio band: 0.99–1.00 (task-01), 0.97–0.99 (task-07) — versus
  0.85–0.97 on the unmapped MiniMax-M3 lane. The mapped-tokenizer band tightens to ~1.0, so
  the MiniMax underestimate was dominated by the fallback tokenizer; a residual ~1–3%
  underestimate remains, consistent with uncounted request framing and litellm's tool
  serialization convention.
- One notable event: between calls 9 and 10 of task-07, history dropped 30.8K → 10.0K tokens
  with **no intervening LLM call** — view-property enforcement
  (`View.enforce_properties`: batch/observation/atomicity properties drop events without a
  summarization call), not an LLM-summarizing condensation. The composition tracked the
  shrunken view exactly (ratio stayed 0.99 across the drop), which is itself evidence the
  history bucket measures the payload actually sent.
- Total spend for this lane (litellm-computed `accumulated_cost`, PAYG key): $0.0081
  (task-01) + $0.0808 (task-07) ≈ **$0.089**; 87% of prompt tokens were cache reads.


## MiniMax-M3 summary

| task | calls | avg system | avg tools | avg history | avg latest | avg provider input | est/provider median |
|---|---:|---:|---:|---:|---:|---:|---:|
| p09-task-01 | 5 | 3,331 | 5,749 | 533 | 205 | 11,478 | 0.85 |
| p09-task-07 | 26 | 3,331 | 5,749 | 10,462 | 381 | 21,176 | 0.94 |
| p09-task-10 | 10 | 3,331 | 5,749 | 2,605 | 215 | 13,506 | 0.86 |

- The standing preamble (system + tool schemas ≈ 9,080 estimated tokens) is constant per call:
  ~79% of the average call on the trivial task (avg provider input 11,478), ~84% of first calls.
- Re-sent tool schemas totaled 149,474 estimated tokens on the 26-call task — 27% of that run's
  provider-reported input (550,568).
- Estimated component sum per call runs 0.85–0.97× the provider-reported `prompt_tokens`,
  rising as history grows. The residual gap is consistent with request framing and tokenizer
  mapping differences; the component split is the finding, not the absolute sum.
- MiniMax's automatic prefix caching served 79–94% of prompt tokens as cache reads.
