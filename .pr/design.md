# Make `send_reasoning_content` user-configurable per LLM instance

## Problem

Whether the SDK sends the model's full reasoning content back in the message
input was decided **only** by a hardcoded allow-list,
`SEND_REASONING_CONTENT_MODELS` in
`openhands-sdk/openhands/sdk/llm/utils/model_features.py`.

Consequences of the hardcoded-only approach:

- Enabling a new thinking-capable model required editing the list, cutting a
  new SDK release, and having every consumer upgrade + migrate.
- Users running a model behind a custom/proxy name the list does not match had
  no way to opt in.
- There was no way to force-disable the behavior for a listed model.

## Approach

Reuse the LLM's existing per-instance override channel,
`LLM.capability_overrides` (`openhands-sdk/openhands/sdk/llm/llm.py`), which
already flows into `get_features(..., overrides=...)` and already backs
`supports_vision`, `supports_stop_words`, `supports_prompt_cache`, etc.

`send_reasoning_content` was the one capability that ignored `overrides` and
read the hardcoded list directly. We route it through the same `_resolved_bool`
helper the other capabilities use, keeping the list as the fallback.

Resolution precedence (unchanged pattern, now applied to this field too):

1. explicit `capability_overrides["send_reasoning_content"]` (respects `False`)
2. LiteLLM model metadata, if present
3. fallback: `model_matches(model, SEND_REASONING_CONTENT_MODELS)`

This deliberately keeps provider-specific criteria inside the feature registry
rather than `llm.py`, matching the repo guidance that "LLM-specific behavior
tweaks should start in `model_features.py` whenever they can be expressed as
model/provider capabilities."

## Before / After

Before (`get_features`):

```python
send_reasoning_content=model_matches(model, SEND_REASONING_CONTENT_MODELS),
```

After:

```python
send_reasoning_content=_resolved_bool(
    "send_reasoning_content",
    overrides=overrides,
    metadata=model_info,
    fallback=model_matches(model, SEND_REASONING_CONTENT_MODELS),
),
```

Usage:

```python
# Opt a not-yet-listed model in:
LLM(model="my/new-thinking-model",
    capability_overrides={"send_reasoning_content": True})

# Force-disable for a listed model:
LLM(model="kimi-k2-thinking",
    capability_overrides={"send_reasoning_content": False})
```

## Why this is safe / low-risk

- **Backward compatible**: default behavior is identical — with no override the
  hardcoded list still decides. No behavior change for existing configs.
- **No new public API surface**: `capability_overrides` already exists and is a
  persisted `dict[str, bool | str]` field, so no settings `schema_version` bump
  and no migration are needed.
- **No `llm.py` logic added**: only a doc-string update listing the new
  supported key; the resolution stays in the feature registry.

## Testing

- `test_send_reasoning_content_support` (existing): fallback list unchanged.
- `test_send_reasoning_content_override` (new): override enables a non-listed
  model (`gpt-4o`) and disables a listed one (`kimi-k2-thinking`).

Run:

```bash
uv run pytest tests/sdk/llm/test_model_features.py -k send_reasoning
```
