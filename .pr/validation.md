# Blank Chat Completions content

Base: `9c3571a694547734002518bd94b3cb41a187f9b6`. Tested on macOS with Python 3.13.13.
Updated with upstream `76e9e250` before the prompt-cache regression fix.

## Revalidation after syncing main (2026-09-26)

Merged upstream `a350dc73ef9b4d3a801ffab2aed211a04d2120a9` without conflicts.
The serialization fix and cache-marker regression coverage remain unchanged
relative to current main. On macOS with Python 3.13.13:

- `uv sync --frozen --group dev`: passed.
- `uv run pytest tests/sdk/llm -q`: **1090 passed, 14 warnings**.
- `.venv/bin/python .pr/repro_chat_content.py`: **4/4 HTTP cases passed**.
- `.venv/bin/python .pr/repro_prompt_cache.py`: **6/6 cache markers retained**.
- `uv run pre-commit run --all-files --show-diff-on-failure`: all checks passed.

The HTTP runs still use a strict loopback endpoint. They are not real-provider,
provider-cache-hit, or benchmark evidence. Additional live evaluation, if required
by the maintainer, needs a configured model service.

## Prompt-cache regression follow-up

The review correctly identified that filtering a trailing blank tool block could
discard the SDK's automatically assigned Anthropic cache breakpoint. Tool cache
flags are now collected before content filtering. For other roles, a marker on
a removed trailing blank block moves to the preceding retained content block.
Stored messages and the text of retained blocks remain unchanged.

Run `.venv/bin/python .pr/repro_prompt_cache.py` for SDK completion calls through
LiteLLM to a real loopback Anthropic-compatible HTTP endpoint. The companion
`repro_chat_content.py` supplies the credential-clearing and loopback-only setup.
The [recorded requests](prompt-cache.log) retain all **6/6 cache markers** across
mixed and wholly blank tool results and mixed user content, with both empty and
whitespace-only trailing blocks. Each case also checks the parsed response and
unchanged input messages. This validates request serialization, not live-provider
cache hits, cost, latency, or task performance.

Before the production correction, the four additional role-serialization cases
and six SDK-to-Anthropic request-conversion cases all failed on missing markers.
Afterward, the focused serialization suite plus cross-conversation caching tests
passed **136 tests**. The full LLM module suite, `uv run pytest tests/sdk/llm -q`,
also passed: **1037 passed, 17 warnings**.

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

Originally recorded during implementation: 120 passed. With the 18 parameterized cases
added before the production fix, 17 failed and one passed. Coverage includes
roles, tool calls, enabled images, valid whitespace, and unchanged storage data.
The four original HTTP cases were rerun after the cache correction: **4/4 passed**.

## Checks and limits

`uv run pre-commit run --all-files --show-diff-on-failure`: **passed**, including
Ruff, PEP8, Pyright, dynamic-attribute, import, and tool-registration checks.
Merging upstream #4968 resolved the four previously documented baseline
dynamic-attribute violations.

The copied reproduction also passed its applicable Python pre-commit checks.
No remote-provider, agent benchmark, or evaluation run was performed. A
maintainer-triggered lightweight eval and human review remain outstanding, as
requested in the review. The local HTTP checks are not benchmark evidence.
This is an internal serialization correction; no public signatures, schema,
settings defaults, dependencies, or examples change.
