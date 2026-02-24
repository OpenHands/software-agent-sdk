# PR #2178: Responses API reasoning items + `store=False`

This directory contains **ad-hoc validation artifacts** requested in review for PR #2178.

- PR: https://github.com/OpenHands/software-agent-sdk/pull/2178
- Branch: `fix/strip-reasoning-items-store-false`

## What I did

1. Read the PR discussion (review + inline comments) to understand the requested verification.
2. Read OpenAI docs related to Responses API conversation state:
   - Conversation state guide: https://developers.openai.com/api/docs/guides/conversation-state
   - Responses `create` reference page (for parameter definitions):
     https://developers.openai.com/api/reference/resources/responses/methods/create
3. Wrote a standalone reproduction / exploration script:
   - `.pr/repro_responses_reasoning_store_false.py`
4. Ran it with `store=False` across multiple models using `OPENAI_API_KEY`.

Commands used:

```bash
# Baseline: no include
OPENAI_API_KEY=$OPENAI_API_KEY uv run python .pr/repro_responses_reasoning_store_false.py \
  | tee .pr/repro_responses_reasoning_store_false.output.txt

# Request encrypted reasoning (per docs)
OPENAI_API_KEY=$OPENAI_API_KEY uv run python .pr/repro_responses_reasoning_store_false.py --include-encrypted \
  | tee .pr/repro_responses_reasoning_store_false.include_encrypted.output.txt
```

Environment notes:
- `openai` python package version (via `uv` env): 2.8.1

## Key findings (from actual API calls)

### 1) Some reasoning models return a `type: "reasoning"` output item with an `rs_...` id
For example (turn 1 output items summary):
- `reasoning:rs_...` and `message:msg_...`

### 2) With `store=False`, echoing prior `response.output` items verbatim into the next request can fail *unless encrypted reasoning is included*

**Baseline (no `include=["reasoning.encrypted_content"]`)**:

For reasoning models that emit a reasoning item with an `rs_...` id, **turn 2 fails** with:

> `Item with id 'rs_...' not found. Items are not persisted when store is set to false.`

This reproduces the exact class of error described in the PR.

**With `include=["reasoning.encrypted_content"]`** (per the Responses `create` docs):

- Turn 1 reasoning items include a non-empty `encrypted_content` field.
- Turn 2 **succeeds even with naive echoed `response.output`** for the reasoning models tested (no 404).

### 3) Multiple workarounds succeed with `store=False`
In the script I tested these (for turn 2 input construction):

- **Strip reasoning items**: remove any `{"type": "reasoning", ...}` dicts from the prior output items.
- **Drop ids**: remove the `id` field from *all* prior output items (including the reasoning item).
- **Messages only**: keep only `{"role": el.role, "content": el.content}` extracted from `type: "message"` output items.

All three approaches worked for the reasoning models tested.

### 4) Non-reasoning output case
For `gpt-5.2`, turn 1 did **not** include a `type: "reasoning"` output item, and the naive “echo output items back into input” succeeded.

## Model-by-model results

Full stdout is in:

- Baseline: `.pr/repro_responses_reasoning_store_false.output.txt`
- With encrypted reasoning: `.pr/repro_responses_reasoning_store_false.include_encrypted.output.txt`

### Baseline (no `include=["reasoning.encrypted_content"]`)

| Model | Responses API call works? | Turn 2 with naive echoed `response.output` | Workaround success (strip reasoning / drop ids / messages only) |
|---|---:|---:|---:|
| `o4-mini` | yes | FAIL (404 `rs_...` not found) | all OK |
| `gpt-5-nano` | yes | FAIL (404 `rs_...` not found) | all OK |
| `gpt-5.2` | yes | OK | all OK |
| `gpt-5.2-codex` | yes | FAIL (404 `rs_...` not found) | all OK |

### With `include=["reasoning.encrypted_content"]`

| Model | Turn 1 returned `reasoning.encrypted_content`? | Turn 2 naive echoed `response.output` |
|---|---:|---:|
| `o4-mini` | yes (len 1124) | OK |
| `gpt-5-nano` | yes (len 1144) | OK |
| `gpt-5.2` | no reasoning item | OK |
| `gpt-5.2-codex` | yes (len 868) | OK |

## Notes from OpenAI docs (relevant to `store=False`)

From the Conversation state guide:

- The docs demonstrate manual multi-turn with Responses API and explicitly call out removing IDs when carrying `response.output` forward (JS example has `// TODO: Remove this step` then `delete el.id;`).
- The guide also shows a Python example using `store=False` where history is built using `role` + `content` from the output items.

This aligns with what we saw in practice:
- With `store=False` **and no** `include=["reasoning.encrypted_content"]`, re-sending server-assigned item IDs (notably `rs_...` reasoning ids) causes resolution failures.
- With `store=False` **and** `include=["reasoning.encrypted_content"]`, reasoning items include `encrypted_content` and naive echoing of `response.output` succeeded in my tests.
- Independently of `include`, omitting IDs (or omitting reasoning items) avoids the error.


## SDK check: do we request `reasoning.encrypted_content`?

Yes (in this repo state).

- `LLM.enable_encrypted_reasoning` defaults to `True`:
  - `openhands-sdk/openhands/sdk/llm/llm.py` (field definition around line ~291)
- The Responses options selector appends `"reasoning.encrypted_content"` to `include` when `store=False` and `enable_encrypted_reasoning=True`:
  - `openhands-sdk/openhands/sdk/llm/options/responses_options.py`

Based on the follow-up repro run, including `reasoning.encrypted_content` is sufficient for naive replay of reasoning items to work statelessly (no 404), because the reasoning output item carries a non-empty `encrypted_content` payload.

## How this informed the script “fix” (requested)

The script includes and demonstrates two viable ways to make a multi-turn loop work with `store=False`:

1. Strip reasoning items before sending the next `input`.
2. Drop the `id` field from all prior items before re-sending them (this preserves the reasoning item payload but avoids ID lookups).

See `.pr/repro_responses_reasoning_store_false.py` for the exact transformations tested.
