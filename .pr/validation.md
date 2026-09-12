# Blank Chat Completions content

Base: `9c3571a694547734002518bd94b3cb41a187f9b6`. Tested on macOS with Python 3.13.13.

## Reproduce

From the repository root after `make build`:

```sh
.venv/bin/python .pr/repro_chat_content.py
```

The script exercises the public SDK through LiteLLM and real loopback HTTP. A
strict local server rejects empty content arrays and blank text blocks. It checks
four histories, parses the successful SDK response, and checks that input
messages remain unchanged. It clears inherited credentials and restricts socket
connections to loopback. It does not contact a real provider or mock SDK methods.

[baseline.log](baseline.log) records the original implementation's four HTTP 400
responses. [fixed.log](fixed.log) records four HTTP 200 responses. The portable
`.pr/` script was rerun successfully after preparing these artifacts. To verify
the baseline independently, copy the script into a checkout of the base commit,
set up that checkout's environment, and run the same command (expected exit 1).

## Regression coverage

```sh
uv run pytest tests/sdk/llm/test_message_serialization.py tests/sdk/llm/test_message.py tests/sdk/llm/test_message_tool_call.py tests/sdk/llm/test_thinking_blocks.py tests/sdk/llm/test_reasoning_content.py tests/sdk/llm/test_message_backward_compatibility.py tests/sdk/llm/test_responses_serialization.py -q
```

Recorded during implementation: 120 passed. With the 18 new parameterized cases
added before the production fix, 17 failed and one passed. Coverage includes
roles, tool calls, enabled images, valid whitespace, and unchanged storage data.
The full suite was not rerun when packaging these artifacts.

## Checks and limits

Ruff format, Ruff lint, PEP8 style, Pyright, import rules, and tool registration: passed.
Full pre-commit: failed in check-forbidden-dynamic-attributes on unchanged files:
openhands-sdk/openhands/sdk/agent/stream_context.py:287
openhands-sdk/openhands/sdk/llm/utils/telemetry.py:264
openhands-sdk/openhands/sdk/llm/utils/telemetry.py:270
openhands-sdk/openhands/sdk/llm/utils/telemetry.py:272
The same four violations were reproduced on the pristine base.

The copied reproduction also passed its applicable Python pre-commit checks.
No remote-provider, agent benchmark, or evaluation run was performed.
This is an internal serialization correction; no public signatures, schema,
settings defaults, dependencies, or examples change.
