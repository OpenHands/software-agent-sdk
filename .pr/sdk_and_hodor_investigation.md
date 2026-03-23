# SDK + hodor investigation (Responses reasoning, `store=False`)

This document is an investigation artifact for PR #2178.

## A) SDK (LiteLLM-backed Responses path)

### Why this matters

The OpenHands SDK’s `LLM.responses()` path uses `litellm.responses.main.responses`.
We wanted to validate (in a real SDK codepath) whether OpenAI’s documented stateless
mechanism works:

- Request `include=["reasoning.encrypted_content"]`
- Then replay the prior `type:"reasoning"` item in the next request while `store=False`

### What I ran

Script: `.pr/sdk_responses_stateless_replay_test.py`

This script:
1. Runs **turn 1** via `LLM.responses()` (so SDK parsing is exercised)
2. Builds **turn 2** input via `LLM.format_messages_for_responses()`
3. Calls `litellm_responses(...)` directly for **turn 2**, optionally *bypassing* the
   SDK’s new “strip reasoning items” behavior so we can test naive replay.

Outputs:
- `.pr/sdk_responses_stateless_replay_test.openai_o4-mini.output.txt`
- `.pr/sdk_responses_stateless_replay_test.openai_gpt-5-nano.output.txt`
- `.pr/sdk_responses_stateless_replay_test.openai_gpt-5.2.output.txt`
- `.pr/sdk_responses_stateless_replay_test.openai_gpt-5.2-codex.output.txt`

### Results summary

For reasoning models (`openai/o4-mini`, `openai/gpt-5-nano`, `openai/gpt-5.2-codex`):

- With `enable_encrypted_reasoning=True` (SDK default):
  - Turn 1 assistant message had a `responses_reasoning_item` with **non-empty**
    `encrypted_content` (lengths recorded in the output).
  - Turn 2 **succeeded** even when we naively replayed the reasoning item (including
    its `rs_...` id) with `store=False`.

- With `enable_encrypted_reasoning=False`:
  - Turn 1 reasoning item had `encrypted_content=None`.
  - Turn 2 **failed** with the same 404/invalid_request_error:
    `Item with id 'rs_...' not found. Items are not persisted when store is set to false.`

- With the PR’s current SDK behavior (strip reasoning items from input when `store=False`):
  - Turn 2 succeeds even when encrypted reasoning is disabled.

For `openai/gpt-5.2`, I didn’t get a `type:"reasoning"` output item in these runs,
so the replay issue doesn’t appear.

### Does `drop_params=True` affect this?

Not in these tests.

Even with `drop_params=True`, encrypted reasoning still came through when enabled
and stateless replay worked.

## B) Agent-level example

I also ran a short agent `Conversation` via `.pr/sdk_agent_conversation_responses_reasoning.py`.
That script collects `LLMConvertibleEvent.to_llm_message()` entries, which are *input*
messages; these don’t preserve `responses_reasoning_item` from LLM outputs, so it’s not
a reliable way to observe `encrypted_content`.

The SDK-level script above is the reliable verification.

Output:
- `.pr/sdk_agent_conversation_responses_reasoning.o4-mini.output.txt`

## C) hodor (mr-karan/hodor)

Repo cloned at `/workspace/project/hodor` (commit `341f8ce0...`).

Key observation: hodor **intentionally disables encrypted reasoning** in its OpenHands
LLM config.

File: `hodor/llm/openhands_client.py`

- It sets:
  - `llm_config["enable_encrypted_reasoning"] = False`
  - Rationale in comments: avoid
    `Encrypted content is not supported with this model`

- It also applies defensive monkeypatches:
  - Patches OpenHands `select_responses_options` to ensure
    `reasoning.encrypted_content` is removed from `include` when the flag is disabled.
  - Patches `Message.from_llm_responses_output` to drop `responses_reasoning_item` at
    parse time so it is never replayed by `Message.to_responses_dict()`.

The “Item with id 'rs_...' not found … store=false” error is therefore expected if:
- encrypted reasoning is disabled **and** reasoning items are replayed (i.e., without
  the strip patch), or
- the strip patch wasn’t present/active in an older hodor version.

Notably, hodor already references PR #2178 in comments as the upstream fix.

## Takeaway

OpenAI docs are correct: `reasoning.encrypted_content` enables stateless multi-turn
*including reasoning items*.

However, because clients may disable encrypted reasoning (as hodor does), the SDK still
needs a fallback strategy (like the PR’s strip logic) to avoid 404s when `store=False`.
