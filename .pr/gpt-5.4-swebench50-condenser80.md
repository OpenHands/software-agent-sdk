# GPT-5.4 SWE-bench 50-instance eval plan

This branch is a lightweight SDK ref for a manual `run-eval.yml` workflow dispatch.

## Intended eval parameters
- benchmark: `swebench`
- eval_limit: `50`
- model_ids: `gpt-5.4`
- model path: `litellm_proxy/openai/gpt-5.4`
- reasoning_effort: `high` (from `.github/run-eval/resolve_model_config.py`)
- agent_type: `default`
- tool_preset: `default`
- benchmarks_branch: `eval/gpt-5.4-swebench50-condenser80`
- condenser:
  - enabled: `true`
  - max_size: `80`
  - keep_first: `2`

## Why this branch exists
The SDK `run-eval.yml` workflow can target a custom `benchmarks_branch`, but it does not expose condenser inputs directly. The matching benchmarks branch carries the temporary SWE-bench condenser override for this run.
