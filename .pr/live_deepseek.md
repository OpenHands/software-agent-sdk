# Live check with DeepSeek V4.1 Flash (thinking mode)

Contributor-supplied live records show that DeepSeek rejects the split tool-call history in all three cases below.
Two fail because the split-off assistant message lacks `reasoning_content`; the third fails because a tool result does not immediately follow its assistant tool-call batch. The fixed history is accepted in all three cases.
These supplied records were inspected for consistency and sensitive data; the live calls were not repeated during this PR update.

## Setup

- Script: [live_deepseek.py](live_deepseek.py). It runs `Conversation.run()` with the real agent and the real
  `file_editor` and `task_tracker` tools in a temporary workspace.
- Only the first completion is scripted: an assistant message with non-empty `reasoning_content` and parallel tool
  calls with DeepSeek-style ids (`call_00_…`). One call fails validation. The second completion is sent to
  OpenRouter unmodified, and the conversation stops after it (`max_iteration_per_run=2`). This means HTTP acceptance of the next request is tested, not completion of
  the whole user task. The successful records end with another tool call and a `ConversationErrorEvent`;
  the SDK emits a run-limit error when this configured iteration cap is reached.
- Model `openai/deepseek/deepseek-v4.1-flash` through `https://openrouter.ai/api/v1`, provider pinned to DeepSeek
  (`allow_fallbacks: false`), `reasoning: {enabled: true, effort: low}`, `max_completion_tokens: 512`,
  temperature 1.0, top_p 0.95. `get_features()` reports `send_reasoning_content=True` for this name.
- Environment: Linux x86_64, Python 3.13.14, LiteLLM 1.93.0, Pydantic 2.12.5.
- Base: `3311ba9eec5044f40ab5d0b3d7eddc9f7e1e2d14`. Fix: `368e96e5f` (implementation `648fad5cc`). The contributor also reports the same result on PyPI
  `openhands-sdk==1.49.6`; separate PyPI records are not included here.
- One real API call per run.

## Cases

| Case | Scripted calls |
|---|---|
| 1 | `task_tracker` with `status: "pending"` (invalid), `file_editor` view (valid) |
| 2 | `file_editor` with arguments that are not JSON (invalid), `task_tracker` plan (valid) |
| 3 | A `file_editor` view (valid), B `task_tracker` with `status: "pending"` (invalid), C `task_tracker` view (valid) |

## Results

Messages after the system and user messages of the second request (`+rc`: non-empty `reasoning_content`):

| Case | Base: messages | Base: result | Fix: messages | Fix: result |
|---|---|---|---|---|
| 1 | assistant[00] +rc, tool(00), assistant[01], tool(01) | 400 (a) | assistant[00, 01] +rc, tool(00), tool(01) | 200 |
| 2 | assistant[00] +rc, tool(00), assistant[01], tool(01) | 400 (a) | assistant[00, 01] +rc, tool(00), tool(01) | 200 |
| 3 | assistant[A, B] +rc, tool(B), assistant[C], tool(A), tool(C) | 400 (b) | assistant[A, B, C] +rc, tool(B), tool(A), tool(C) | 200 |

- (a) ``The `reasoning_content` in the thinking mode must be passed back to the API.``
- (b) `An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id'. (insufficient tool messages following tool_calls message)`

The provider is DeepSeek in every response (`provider_error_code: invalid_request_error` for the 400s).

Within the sanitized request records, only the `messages` array differs between base and fix. The system prompt (same sha256), the user
message, `tools` (same sha256), parameters, every tool call object and every tool message's content are identical.
Tool results keep their order: validation errors first, then executed results (case 3: B, A, C), which DeepSeek
accepts.

The per-run records are in [live_deepseek/](live_deepseek/): the scripted calls, the request parameters and
messages as sent (system prompt replaced by its length and sha256, temporary workspace path replaced by
`<workspace>`), the `tools` sha256, the response (status and error, or the provider and the returned tool calls),
and the SDK event sequence.

## Scope

This checks DeepSeek V4.1 Flash through OpenRouter's Chat Completions endpoint only. Other providers and the
Responses API were not called.

## Reproduction and publication notes

Set `OPENROUTER_API_KEY` in your local environment, then run from the SDK checkout after `make build`:

```bash
OPENHANDS_SUPPRESS_BANNER=1 LITELLM_LOCAL_MODEL_COST_MAP=True \
  uv run --no-sync python .pr/live_deepseek.py --case 1 --label pr --out /tmp/pr-case1.raw.json
```

Repeat with cases 2 and 3 on the base and fixed revisions. This makes billable API requests.
The script records raw diagnostic output and returns zero even for a captured HTTP failure; inspect the
HTTP status in the output. Its raw schema differs from the curated records included in this PR.

The six published JSON files were separately sanitized: account/user IDs and provider request IDs were
removed, local workspace paths were replaced, and the system prompt was replaced by length and SHA-256.
They contain synthetic notes and prompts, not an original user conversation. No API key or authorization
header is included. Tool call IDs are retained to show call/result relationships; the first-response IDs
are synthetic constants in the script. The later response IDs in `sdk_events` are tool-call identifiers.

Fresh script output is **not automatically publication-safe**. It includes response bodies, prompts and
traceback paths; API error bodies may contain account identifiers. Review and sanitize both the output
file and console output before sharing. The API-key assertion is not a general privacy filter.
