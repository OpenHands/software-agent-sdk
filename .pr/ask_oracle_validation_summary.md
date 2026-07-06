# ask_oracle validation evidence

This directory is temporary PR evidence for the `ask_oracle` tool implementation.

## What changed

- Added `ask_oracle`, a read-only tool in **`openhands-tools`** (`openhands/tools/ask_oracle/`). It is a one-shot, tool-less sub-agent: it consults a stronger LLM for a stateless second opinion.
- The Oracle model is a saved LLM profile resolved **by convention** under the name `oracle` (`ORACLE_PROFILE_NAME`). There is no agent setting and no wiring in `OpenHandsAgentSettings`/`model.py`: users add `Tool(name="ask_oracle")` and the tool resolves the `oracle` profile from the conversation's profile store at run time. This mirrors how `TaskToolSet` lives in `openhands-tools` and resolves sub-agents by registered name.
- Removed the previous `OpenHandsAgentSettings.oracle_llm_profile` field and the `AskOracleTool` SDK built-in (and its `BUILT_IN_TOOL_CLASSES` entry). The SDK no longer references the tool at all, so there is no `openhands-sdk` → `openhands-tools` dependency.
- The active conversation LLM is not switched. The Oracle call sends only the Oracle system prompt plus the agent's question and optional context, without forwarding conversation history or tools.

## Live validation (end-to-end)

Evidence file: `.pr/ask_oracle_live_validation.json`

This drives the **real agent loop** — not a unit-level
`conversation.execute_tool()` call. The agent itself decides to call
`ask_oracle`, the tool consults the `oracle` profile, and the agent answers
using the Oracle's response.

Command run:

```bash
OPENHANDS_SUPPRESS_BANNER=1 \
LLM_API_KEY=... LLM_BASE_URL=https://llm-proxy.eval.all-hands.dev \
ASK_ORACLE_PRIMARY_MODEL=litellm_proxy/openai/gpt-5.1 \
ASK_ORACLE_MODEL=litellm_proxy/openai/gpt-5-mini \
uv run python .pr/ask_oracle_live_validation.py
```

Profiles:

- Primary: `litellm_proxy/openai/gpt-5.1` (the agent's own model).
- Oracle: `litellm_proxy/openai/gpt-5-mini`, saved under the conventional profile
  name `oracle`, with `log_completions` enabled so its response can be read
  independently.

Result summary (from the JSON):

- `agent_called_ask_oracle: true` — the agent emitted an `ask_oracle` action in
  the loop (the question it asked the Oracle is recorded).
- `ask_oracle_observation_in_loop: true`, `observation_is_error: false`.
- **`oracle_logged_response_matches_observation: true`** — the Oracle's response
  read straight from its own completion log (telemetry) matches the observation
  the agent acted on, proving the answer came from the `oracle` profile.
- `final_agent_answer` is the two-word reply (e.g. "Mostly mild") and
  `conversation_finished: finished` — the conversation completed normally.
- Temporary profile store and Oracle log dir are removed in a `finally` block.

## Validation commands

Pre-commit command run on changed files: passed.

### Targeted tests

Command run:

```bash
uv run pytest \
  tests/tools/ask_oracle/test_ask_oracle.py \
  tests/sdk/tool/test_builtins.py \
  tests/sdk/test_settings.py::test_llm_agent_settings_export_schema_groups_sections \
  tests/sdk/test_settings.py::test_export_agent_settings_schema_emits_variant_tagged_sections \
  tests/examples/test_examples.py::test_directory_example_is_discovered
```

Result: `10 passed`.

### Example execution

Command run:

```bash
uv run pytest tests/examples/test_examples.py --run-examples -k 55_ask_oracle_tool
```

Result: `1 passed, 66 deselected`.

