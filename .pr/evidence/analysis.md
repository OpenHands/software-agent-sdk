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
