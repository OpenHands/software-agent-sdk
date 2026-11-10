# Development Guide

## Setup

```bash
git clone https://github.com/OpenHands/software-agent-sdk.git
cd software-agent-sdk
make build
```

## Repository boundaries

This repository owns the Python SDK and Agent Server. Put agent/tool behavior, conversations, workspaces, events, and new REST/WebSocket endpoints here. The API contract flows through `clients/typescript/` to Agent Canvas in [`OpenHands/OpenHands`](https://github.com/OpenHands/OpenHands); automation scheduling, webhooks, run history, and dispatch belong in [`OpenHands/automation`](https://github.com/OpenHands/automation). If a PR is opened in the wrong repository, recommend closing and moving it to the owning repository. Follow [`.agents/skills/custom-codereview-guide.md`](.agents/skills/custom-codereview-guide.md) for every PR.

## Code Quality

```bash
make format                              # Format code
make lint                                # Lint code
uv run pre-commit run --all-files        # Run all checks
```

Pre-commit hooks run automatically on commit with type checking and linting.

## Testing

```bash
uv run pytest                            # All tests
uv run pytest tests/sdk/                 # SDK tests only
uv run pytest tests/tools/               # Tools tests only
```

## Project Structure

```
software-agent-sdk/
├── openhands-sdk/          # Core SDK package
├── openhands-tools/        # Built-in tools
├── openhands-workspace/    # Workspace management
├── openhands-agent-server/ # Agent server
├── examples/               # Usage examples
└── tests/                  # Test suites
```

## Contributing

1. Create a new branch
2. Make your changes
3. Run tests and checks
4. Push and create a pull request

For questions, join our [Slack community](https://openhands.dev/joinslack).

## LLM message construction invariant

**Every LLM request built in this repository must place a `system` message before
the first `user` message.**

The canonical enforcement lives in `Agent.init_state()`
(`openhands-sdk/openhands/sdk/agent/agent.py`): it guarantees the `SystemPromptEvent`
sits at the head of the event stream (index 0/1) and raises `AssertionError` if a user
`MessageEvent` appears before it. `LLMConvertibleEvent.events_to_messages()`
(`openhands-sdk/openhands/sdk/event/base.py`) projects the event stream to messages in
order, preserving the system lead-in on the main agent loop.

This applies to **all** standalone LLM calls, not just the agent loop: condensers, goal
judges, agent-server profile pre-flight pings, security analyzers, title generation,
cleanup prompts, hook prompts, toolshield analyzers, and vision inspect. Each must begin
its message list with a `system` role.

### Why it matters

- **Consistent steering:** standalone calls that omit a system message lose the shared
  context/format expectations other paths get.
- **Correct transport serialization:** on the OpenAI Responses API,
  `Message.to_responses_value()` returns a string for `system` (→ `instructions`) and a
  dict list for `user` (→ `input`). Sending steering instructions as a lone `user`
  message conflates "how" with "what", and a lone `system` message serializes to
  `instructions` with empty `input`.
- **Subscription/Codex transport** prepends system chunks onto the first user message;
  a missing system lead-in produces a bare, unsteered request.

### Documented exceptions

- **ACP agents:** the real system prompt and tools are managed by the ACP server, not by
  an SDK-constructed message list; the SDK emits a placeholder `SystemPromptEvent` for
  the visualizer.
- **Subscription/Codex transport** (`transform_for_subscription`): system chunks are
  prepended *into* the first user message's content because Codex-style endpoints reject
  long/complex `instructions` — ordering intent preserved, shape flattened.

### Reviewer checklist

The first message role must be `system`, or there must be a documented exception above.
