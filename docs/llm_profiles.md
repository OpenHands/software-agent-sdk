# LLM profiles

LLM profiles are named JSON configuration files for `openhands.sdk.llm.LLM`.

They let you reuse the same model configuration across scripts and runs without
copy/pasting large inline payloads.

## Storage format and location

- A profile file is simply the JSON representation of an `LLM` instance.
- Default location: `~/.openhands/llm-profiles/<profile_id>.json`
- The `profile_id` is the filename stem (no `.json` suffix).

## Managing profiles with `LLMRegistry`

Use `LLMRegistry` as the entry point for both:

- in-memory registration (`usage_id` -> `LLM`)
- on-disk profile management (`profile_id` -> JSON file)

APIs:

- `LLMRegistry.list_profiles()` returns available `profile_id`s
- `LLMRegistry.load_profile(profile_id)` loads the profile from disk
- `LLMRegistry.save_profile(profile_id, llm, include_secrets=False)` writes a profile

### Secrets

By default, `save_profile(..., include_secrets=False)` omits secret fields (for
example `api_key`, `aws_access_key_id`, `aws_secret_access_key`).

Prefer providing secrets at runtime (env vars / SecretsManager) rather than
committing them to disk.

## Conversation persistence and profile references

Conversation snapshots (`base_state.json`) can either:

- store full inline LLM payloads (default, reproducible), or
- store compact profile references (`{"profile_id": "..."}`) when inline mode is disabled

This is controlled by `OPENHANDS_INLINE_CONVERSATIONS`:

- default: `true` (inline LLM payloads)
- set `OPENHANDS_INLINE_CONVERSATIONS=false` to persist profile references for any `LLM`
  that has `profile_id` set

If you switch back to inline mode and try to resume a conversation that contains
profile references, the SDK raises an error so you don’t accidentally resume
without the full config.

## Examples

- `examples/01_standalone_sdk/31_llm_profiles.py`
- `examples/llm-profiles/gpt-5-mini.json`

