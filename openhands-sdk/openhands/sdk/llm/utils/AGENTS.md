# LLM utils guidelines

See the [SDK AGENTS.md](../../AGENTS.md) for package-wide policies.

## Verified model lists (`verified_models.py`)

These lists are a curated set of models that work well, not a catalog of everything a provider offers. Keep them short.

- For each model line, keep only the **two latest versions** (for example `gpt-6` and `gpt-5.6`; `claude-opus-5-5` and `claude-opus-5`).
- Variants of a kept version (`-pro`, `-mini`, `-codex`, `-flash`, dated aliases) stay with that version. Unversioned "current" aliases (`deepseek-chat`, `kimi-for-coding`) stay.
- When you add a new version, remove the oldest version in the same line, in every list where it appears (the provider list and `VERIFIED_OPENHANDS_MODELS`).
- Every entry in `VERIFIED_OPENHANDS_MODELS` must also appear in a provider list, unless it is OpenHands-only; `tests/sdk/llm/test_model_list.py` checks this.
- Do not add a model just because LiteLLM or OpenRouter knows about it. The unverified catalog (`unverified_models.py`) covers that.
