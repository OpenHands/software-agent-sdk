Closes #5510

## Problem

When the LLM returns an assistant turn that contains text content but no tool call, `ResponseDispatchMixin._handle_content_response()` unconditionally sets `conversation.state.execution_status = FINISHED`. As a result, the agent loop is stopped even though the model was clearly still working and intended to make a further tool call.

This is most visible with local models and OpenAI-compatible endpoints that use string/mock tool-calling (`native_tool_calling=false`), because such models frequently interleave short prose/narration turns before emitting their next tool call.

## Root Cause

In `openhands-sdk/openhands/sdk/agent/response_dispatch.py`, `_handle_content_response()` had this unconditional assignment:

```python
state.execution_status = ConversationExecutionStatus.FINISHED
```

Any response classified as CONTENT (text present, no tool calls) therefore mapped straight to FINISHED. Combined with `classify_response()` treating "has text, no tool call" as a terminal content response, an intermediate narration turn became a hard stop.

## Fix

1. **Removed** `state.execution_status = ConversationExecutionStatus.FINISHED` from `_handle_content_response()`. Content-only turns no longer terminate the agent loop.
2. Instead, the handler emits the message event and sends a **corrective nudge** directing the model to either call `finish` or continue with the next tool call. This mirrors the existing behavior of `_handle_no_content_response()`.
3. Updated `_send_corrective_nudge()` to accept a `content` flag so it can emit appropriate feedback for both scenarios (content-present vs empty/reasoning-only).

## Invariant

**Only explicit terminal signals should end execution** — i.e., the `finish` tool call. A bare content turn can no longer silently end a running task.

## Tests Updated

- `test_response_dispatch.py`: `test_content_response_sets_finished` renamed and updated to `test_content_response_sends_nudge`
- `test_tool_call_recovery.py`: `test_content_response_does_not_inject_nudge` renamed and updated to `test_content_response_injects_nudge`
- `test_reasoning_only_responses.py`: `ContentOnlyLLM` mock now returns finish on the second call, matching the real two-step flow (narration -> nudge -> finish).
