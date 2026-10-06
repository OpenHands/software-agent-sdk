# Agent-requested context reset

This example has two phases. First, the agent writes an approval note through
the ordinary file editor tool, leaving the launch code and region in the
conversation history. In a fresh context, it reads the approval file and
retrieves the original launch record using `conversation_history`.

The SDK applies the reset after the tool batch finishes. The reset call, its
handoff and the batch results remain in the model input; older events stay in
the conversation history and can be searched or read by event ID. The handoff
points to the file without copying the record values or approval state.

Run the deterministic example without a provider:

```sh
uv run examples/01_standalone_sdk/agent_reset/main.py --offline
```

The offline mode uses scripted `TestLLM` responses while executing real file
writes, reads, resets, and history searches. It validates the execution path,
not a model's ability to decide when to reset or what to retain.

For a live run, export `LLM_MODEL` and `LLM_API_KEY`, optionally set
`LLM_BASE_URL`, and omit `--offline`. The example checks that a reset occurred,
the original record left the active context, the file was read after the reset,
and a history tool result recovered its code and region. Its live prompt
describes the phase transition and evidence to recover, without prescribing a
particular read call. This is a demonstration rather than an evaluation of
autonomous reset decisions. It prints `EXAMPLE_COST` as the SDK's cost estimate.
The temporary workspace and approval file are removed after the run.

Enable this mode through settings:

```python
from openhands.sdk import LLM, OpenHandsAgentSettings
from openhands.sdk.settings import AgentResetCondenserSettings

settings = OpenHandsAgentSettings(
    llm=LLM(model="your-model"),
    tools=[],
    condenser=AgentResetCondenserSettings(),
)
agent = settings.create_agent()
```

The settings entry point adds `new_context` and `conversation_history` when
missing, without changing the settings object's tool list. Direct `Agent`
construction with `AgentResetCondenser()` requires explicitly selecting these
tools. The normal summarizing condenser remains the default for other agents.

An enabled agent-reset setting contains only `condenser_kind="agent_reset"` and
`enabled=True`. There are no event-count or retained-event-count tuning fields.
The agent can include a handoff in `new_context` or use its available file tools
for notes. Near a known model input limit the SDK provides a reminder; a hard
capacity boundary uses the existing summarization fallback. A successful fallback
does not demonstrate that the model chose a timely reset.

The same settings object can be sent as `agent_settings.condenser` when creating
a server conversation, or under `agent_settings_diff.condenser` in a settings
update. This requires an Agent Server containing the feature; the TypeScript
client's pinned released schema and image are not changed by this example.
