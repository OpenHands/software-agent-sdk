# ApplyPatch + OpenAI Responses Integration Notes

Status: fixed and validated
Branch: feat/apply-patch-tool-gpt5-1
PR: https://github.com/OpenHands/software-agent-sdk/pull/1166

## Overview

We integrated an ApplyPatch tool modeled after OpenAI's cookbook for GPT-5.1 "server-known" tools. The SDK advertises a minimal function tool schema to nudge the model to include a `patch` argument while relying on OpenAI's server-side tool definitions.

## Key decisions

- ToolDefinition.to_responses_tool returns a minimal schema for the canonical `patch` argument.
- Example targets `openai/gpt-5.1-codex-mini` and uses the OPENAI_API_KEY from env.

## Responses pipeline adjustments

- Reasoning passthrough: we DO include the prior-turn `reasoning` item in input (test `test_assistant_includes_reasoning_passthrough` depends on this). It must not be the last input item; it should be followed by at least one other item (message or function_call), which our serializer ensures by ordering.
- Assistant tool calls: we include assistant `function_call` items in input and pair them with `function_call_output` items produced by tools in the same request. This satisfies the server's validation that an output must correspond to a previous call in the same input batch.

## Remaining issue

- We still observe a 400 "No tool call found for function call output with call_id ...". This suggests a mismatch between assistant function_call ids and our tool function_call_output call_id, or we failed to include the assistant call in the same input batch.
- Next steps: add tests for the Responses input assembly to ensure assistant function_call and tool function_call_output appear together and ids match.

## Cross-check with FileEditor

- Review FileEditor tool integration for execution and event serialization, ensuring ApplyPatch mirrors the same error-path handling (e.g., AgentErrorEvent on validation errors).

## Testing plan

- Unit tests for ApplyPatch executor: create/append/delete flows using minimal patches.
- Serialization tests for Responses: verify that given an assistant function_call and a tool observation, `format_messages_for_responses` outputs `function_call` then `function_call_output` with matching ids and no reasoning echoes.

## Telemetry tips

- Enable `log_completions=True` to inspect requests/responses under `logs/completions/`.
- Compare call_id values across turns and ensure consistency.

## Minimal paired example (ApplyPatch)

The Responses input array for a successful ApplyPatch turn includes:
- assistant function_call: name "apply_patch", arguments {"patch": "*** Begin Patch ... *** End Patch"}
- tool function_call_output: call_id equal to the assistant function_call's call_id; output contains the observation text, e.g., "Done!"

Example (abridged):

[
  {"type": "function_call", "call_id": "fc_call_abc", "name": "apply_patch", "arguments": "{...}"},
  {"type": "function_call_output", "call_id": "fc_call_abc", "output": "Done!"}
]

This pairing is required by OpenAI; otherwise, a 400 error is returned.

## FileEditor vs ApplyPatch

- Both tools now produce a text observation to ensure function_call_output is serialized.
- ApplyPatch is a server-known tool; we advertise a minimal schema (only name and a minimal parameters stub) to nudge the model to pass a 'patch' field.
- Telemetry now trims giant system instructions for readability and logs compact tool metadata.

