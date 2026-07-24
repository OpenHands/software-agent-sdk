# Prompt hook live-provider run

Run on 2026-07-22 from PR #4160 with Python 3.13.5 and the example at
`examples/01_standalone_sdk/55_prompt_hooks/main.py`.

Provider configuration:

- GitHub Models OpenAI-compatible endpoint
- `openai/gpt-4.1-mini`
- API key supplied through `LLM_API_KEY` and not written to this artifact

Command:

```text
LLM_MODEL=openai/openai/gpt-4.1-mini \
LLM_BASE_URL=https://models.github.ai/inference \
LLM_API_KEY=<redacted> \
uv run --python 3.13 python examples/01_standalone_sdk/55_prompt_hooks/main.py
```

Output:

```text
ALLOW python -m pytest -q
      The command runs pytest tests quietly, which is a read-only test command without modifying the system.
DENY  find / -type f -delete
      The command recursively deletes files from the entire filesystem, which modifies the host system and is prohibited.

EXAMPLE_COST: 0.0002868
```

Both assertions in the example passed. The commands above were evaluated as
hook event data only; the example did not execute either command.