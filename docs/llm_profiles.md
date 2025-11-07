LLM Profiles (design)

Overview

This document records the design decision for "LLM profiles" (named LLM configuration files) and how they map to the existing LLM model and persistence in the SDK.

Key decisions

- Reuse the existing LLM Pydantic model schema. A profile file is simply the JSON dump of an LLM instance (the same shape produced by LLM.model_dump(exclude_none=True) or LLM.load_from_json).
- Storage location: ~/.openhands/llm-profiles/<profile_name>.json. The profile_name is the filename (no extension) used to refer to the profile.
- Do not change ConversationState or Agent serialization format for now. Profiles are a convenience for creating LLM instances and registering them in the runtime LLMRegistry.
- Secrets: do NOT store plaintext API keys in profile files by default. Prefer storing the env var name in the LLM.api_key (via LLM.load_from_env) or keep the API key in runtime SecretsManager. The LLMRegistry.save_profile API exposes an include_secrets flag; default False.
- LLM.usage_id semantics: keep current behavior (a small set of runtime identifiers such as 'agent', 'condenser', 'title-gen', etc.). Do not use usage_id as the profile name.

LLMRegistry profile API (summary)

- list_profiles() -> list[str]
- load_profile(name: str) -> LLM
- save_profile(name: str, llm: LLM, include_secrets: bool = False) -> str (path)
- register_profiles(profile_ids: Iterable[str] | None = None) -> None

Implementation notes

- LLMRegistry is the single entry point for both in-memory registration and on-disk profile persistence. Pass ``profile_dir`` to the constructor to override the default location when embedding the SDK.
- Use LLM.load_from_json(path) for loading and llm.model_dump(exclude_none=True) for saving.
- Default directory: os.path.expanduser('~/.openhands/llm-profiles/')
- When loading, do not inject secrets. The runtime should reconcile secrets via ConversationState/Agent resolve_diff_from_deserialized or via SecretsManager.
- When saving, respect include_secrets flag; if False, ensure secret fields (api_key, aws_* keys) are omitted or masked.

CLI

- Use a single flag: --llm <profile_name> to select a profile for the agent LLM.
- Also support an environment fallback: OPENHANDS_LLM_PROFILE.
- Provide commands: `openhands llm list`, `openhands llm show <profile_name>` (redacts secrets).

Migration

- Migration from inline configs to profiles: provide a migration helper script to extract inline LLMs from ~/.openhands/agent_settings.json and conversation base_state.json into ~/.openhands/llm-profiles/<name>.json and update references (manual opt-in by user).

## Proposed changes for agent-sdk-19 (profile references in persistence)

### Goals
- Allow agent settings and conversation snapshots to reference stored LLM profiles by name instead of embedding full JSON payloads.
- Maintain backward compatibility with existing inline configurations.
- Enable a migration path so that users can opt in to profiles without losing existing data.

### Persistence format updates
- **Agent settings (`~/.openhands/agent_settings.json`)**
  - Add an optional `profile_id` (or `llm_profile`) field wherever an LLM is configured (agent, condenser, router, etc.).
  - When `profile_id` is present, omit the inline LLM payload in favor of the reference.
  - Continue accepting inline definitions when `profile_id` is absent.
- **Conversation base state (`~/.openhands/conversations/<id>/base_state.json`)**
  - Store `profile_id` for any LLM that originated from a profile when the conversation was created.
  - Inline the full LLM payload only when no profile reference exists.

### Loader behavior
- On startup, configuration loaders must detect `profile_id` and load the corresponding LLM via `LLMRegistry.load_profile(profile_id)`.
- If the referenced profile cannot be found, fall back to existing inline data (if available) and surface a clear warning.
- Inject secrets after loading (same flow used today when constructing LLM instances).

### Writer behavior
- When persisting updated agent settings or conversation snapshots, write back the `profile_id` whenever the active LLM was sourced from a profile.
- Only write the raw LLM configuration for ad-hoc instances (no associated profile), preserving current behavior.
- Respect the `OPENHANDS_INLINE_CONVERSATIONS` flag (default: true for reproducibility). When enabled, always inline full LLM payloads—even if `profile_id` exists—and surface an error if a conversation only contains `profile_id` entries.

### Migration helper
- Provide a utility (script or CLI command) that:
  1. Scans existing agent settings and conversation base states for inline LLM configs.
  2. Uses `LLMRegistry.save_profile` to serialize them into `~/.openhands/llm-profiles/<generated-name>.json`.
  3. Rewrites the source files to reference the new profiles via `profile_id`.
- Keep the migration opt-in and idempotent so users can review changes before adopting profiles.

### Testing & validation
- Extend persistence tests to cover:
  - Loading agent settings with `profile_id` only.
  - Mixed scenarios (profile reference plus inline fallback).
  - Conversation snapshots that retain profile references across reloads.
- Add regression tests ensuring legacy inline-only configurations continue to work.

### Follow-up coordination
- Subsequent tasks (agent-sdk-20/21/22) will build on this foundation to expose CLI flags, update documentation, and improve secrets handling.


## Persistence integration review

### Conversation snapshots vs. profile-aware serialization
- **Caller experience:** Conversations that opt into profile references should behave the same as the legacy inline flow. Callers still receive fully expanded `LLM` payloads when they work with `ConversationState` objects or remote conversation APIs. The only observable change is that persisted `base_state.json` files can shrink to `{ "profile_id": "<name>" }` instead of storing every field.
- **Inline vs. referenced storage:** Conversation persistence previously delegated everything to Pydantic (`model_dump_json` / `model_validate`). The draft implementation added a recursive helper (`compact_llm_profiles` / `resolve_llm_profiles`) that walked arbitrary dictionaries and manually replaced or expanded embedded LLMs. This duplication diverged from the rest of the SDK, where polymorphic models rely on validators and discriminators to control serialization.
- **Relationship to `DiscriminatedUnionMixin`:** That mixin exists so we can ship objects across process boundaries (e.g., remote conversations) without bespoke traversal code. Keeping serialization rules on the models themselves, rather than sprinkling special cases in persistence helpers, lets us benefit from the same rebuild/validation pipeline.

### Remote conversation compatibility
- The agent server still exposes fully inlined LLM payloads to remote clients. Because the manual compaction was only invoked when writing `base_state.json`, remote APIs were unaffected. We need to preserve that behaviour so remote callers do not have to resolve profiles themselves.
- When a conversation is restored on the server (or locally), any profile references in `base_state.json` must be expanded **before** the state is materialised; otherwise, components that expect a concrete `LLM` instance (e.g., secret reconciliation, spend tracking) will break.

### Recommendation
- Move profile resolution/compaction into the `LLM` model:
  - A `model_validator(mode="before")` can load `{ "profile_id": ... }` payloads with the `LLMRegistry`, while respecting `OPENHANDS_INLINE_CONVERSATIONS` (raise when inline mode is enforced but only a profile reference is available).
  - A `model_serializer(mode="json")` can honour the same inline flag via `model_dump(..., context={"inline_llm_persistence": bool})`, returning either the full inline payload or a `{ "profile_id": ... }` stub. Callers that do not provide explicit context will continue to receive inline payloads by default.
- Have `ConversationState._save_base_state` call `model_dump_json` with the appropriate context instead of the bespoke traversal helpers. This keeps persistence logic co-located with the models, reduces drift, and keeps remote conversations working without additional glue.
- With this approach we still support inline overrides (`OPENHANDS_INLINE_CONVERSATIONS=true`), profile-backed storage, and remote access with no behavioural changes for callers.

