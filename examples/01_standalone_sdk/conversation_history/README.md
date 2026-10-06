# Conversation history retrieval

Run this deterministic example without an API key:

```sh
uv run python examples/01_standalone_sdk/conversation_history/main.py
```

The example uses `TestLLM` with the existing `LLMSummarizingCondenser`. It saves a
long source message, condenses the conversation, and runs an agent tool call that
searches for the hidden original. The SDK then reads the source in pages and
reopens the persisted conversation to retrieve it again. Scripted responses
verify this workflow; they do not measure model recall or answer quality.

Enable the optional tool on an ordinary agent:

```python
from openhands.sdk import Agent, Tool

agent = Agent(llm=llm, tools=[Tool(name="conversation_history")])
```

The tool works with any condenser, or without one. It does not reset the context,
add a notes system, or change default agent tools. It searches committed events
on the current conversation's active branch, including events removed from the
model's view. Cross-process retrieval requires conversation persistence.

| Command | Inputs | Output |
| --- | --- | --- |
| `search` | `query`, optional `before_event_id` | Up to five newest-first matches, each with an event ID and at most 400 characters of context. Pass `next_before_event_id` to continue toward older events. |
| `read` | `event_id`, optional character `offset` | Up to 4,000 characters. Continue with `next_offset`; offsets at or beyond the end return an empty page. |

Search uses case-insensitive literal matching. An earlier-history cursor does
not promise further matches. Both commands report whether source events were
in the active view when the snapshot was taken. Events on abandoned or sibling
branches are unavailable; forked conversations can retrieve inherited events.

System/internal events, provider reasoning fields, and history tool calls/results
are excluded. Ordinary message and tool text remain retrievable. Results are
historical data, not new instructions. There is no automatic missing-field
detection or retrieval planner: the model chooses when and what to search/read.

A tool batch uses one branch/view snapshot captured under the conversation state
lock before its workers start. Direct `execute_tool` calls take their own locked
snapshot. Retrieved text subsequently emitted by the agent follows the normal
event-persistence and secret-masking pipeline.
