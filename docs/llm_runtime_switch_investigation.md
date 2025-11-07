# Runtime LLM Profile Switching – Investigation (agent-sdk-24)

## Current architecture

### LLMRegistry
- Keeps an in-memory mapping `service_to_llm: dict[str, LLM]`.
- Loads/saves JSON profiles under `~/.openhands/llm-profiles` (or a custom directory) via:
  - `list_profiles()` / `get_profile_path()`
  - `save_profile(profile_id, llm)` – strips secret fields unless explicitly asked not to.
  - `load_profile(profile_id)` – rehydrates an `LLM`, ensuring the runtime instance’s `profile_id` matches the file stem via `_load_profile_with_synced_id`.
  - `register_profiles(profile_ids=None)` – iterates `list_profiles()`, calling `load_profile` then `add` for each profile; skips invalid payloads or duplicates.
  - `validate_profile(data)` – wraps `LLM.model_validate` to report pydantic errors as strings.
- `add(llm)` publishes a `RegistryEvent` to the optional subscriber and records the LLM in `service_to_llm` keyed by `llm.service_id`.
- Currently assumes a one-to-one mapping of service_id ↔ active LLM instance.

### Agent & LLM ownership
- `AgentBase.llm` is a (frozen) `LLM` Basemodel. Agents may also own other LLMs (e.g., condensers) discovered via `AgentBase.get_all_llms()`.
- `AgentBase.resolve_diff_from_deserialized(persisted)` reconciles a persisted agent with the runtime agent:
  - Calls `self.llm.resolve_diff_from_deserialized(persisted.llm)`; this only permits differences in fields listed in `LLM.OVERRIDE_ON_SERIALIZE` (api keys, AWS secrets, etc.). Any other field diff raises.
  - Ensures tool names match and the rest of the agent models are identical.
- `LLM.resolve_diff_from_deserialized(persisted)` compares `model_dump(exclude_none=True)` between runtime and persisted objects, allowing overrides only for secret fields. Any other difference triggers a `ValueError`.

### Conversation persistence
- `ConversationState._save_base_state()` -> `compact_llm_profiles(...)` when `OPENHANDS_INLINE_CONVERSATIONS` is false, replacing inline LLM dicts with `{"profile_id": id}` entries.
- `ConversationState.create()` -> `resolve_llm_profiles(...)` prior to validation, so profile references become concrete LLM dicts loaded from `LLMRegistry`.
- When inline mode is enabled (`OPENHANDS_INLINE_CONVERSATIONS=true`), profiles are fully embedded and *any* LLM diff is rejected by the reconciliation flow above.

### Conversation bootstrapping
- `LocalConversation.__init__()` adds all LLMS from the agent to the registry and eagerly calls `register_profiles()` (errors logged at DEBUG level). This ensures the in-memory registry is primed with persisted profiles before a conversation resumes.

## Implications for runtime switching

1. **Registry as switch authority**
   - Registry already centralizes active LLM instances and profile management, so introducing a “switch-to-profile” operation belongs here. That operation will need to:
     - Load the target profile (if not already loaded).
     - Update `service_to_llm` (and notify subscribers) atomically.
     - Return the new `LLM` so callers can update their Agent / Conversation state.

2. **Agent/LLM reconciliation barriers**
   - Current `resolve_diff_from_deserialized` logic rejects *any* non-secret field change. A runtime profile swap would alter at least `LLM.model`, maybe provider-specific params. We therefore need a sanctioned path that:
     - Skips reconciliation when conversations are persisted with profile references (i.e., inline mode disabled).
     - Refuses to switch when inline mode is required (e.g., evals with `OPENHANDS_INLINE_CONVERSATIONS=true`). Switching in inline mode would otherwise break diff validation.
   - This aligns with the instruction to “REJECT SWITCH for eval mode,” but “JUST SWITCH” when persistence is profile-based.

3. **State & metrics consistency**
   - After a switch we must ensure:
     - `ConversationState.agent.llm` points at the new object (and any secondary LLM references, e.g., condensers, are updated if needed).
     - `ConversationState.stats.service_to_metrics` either resets or continues per usage_id; we must decide what data should carry over when the service swaps to a different profile.
     - Event persistence continues to work: future saves should store the new profile ID, and reloads should retrieve the same profile in the registry.

4. **Runtime API surface**
   - Need an ergonomic call for agents/conversations to request a new profile by name (manual selection or automated policy). Potential entry points:
     - `LLMRegistry.switch_profile(service_id, profile_id)` returning the active `LLM`.
     - Conversation-level helper (e.g., `LocalConversation.switch_llm(profile_id)`) that coordinates registry + agent updates + persistence.

5. **Observer / callback considerations**
   - Registry already has a single `subscriber`. If multiple components need to react to switches, we might extend this to a small pub/sub mechanism. Otherwise we can keep a single callback and have the conversation install its own handler.

## Open questions / risks
- What happens to in-flight operations when the switch occurs? (For initial implementation we can require the agent to be idle.)
- How should token metrics roll over? We likely reset or create a new entry keyed by the new profile.
- Tool / condenser LLMs: do we switch only the primary agent LLM, or should condensers also reference profiles? (Out of scope unless required by the plan.)
- Tests must cover: successful switch, rejected switch in inline mode, persistence after switch, registry events.

## Next steps
1. Capture the desired UX/API in the follow-up planning issue (agent-sdk-25).
2. Decide how to bypass reconciliation safely when profile references are used.
3. Define exact testing matrix (registry unit tests, conversation integration tests, persistence roundtrip).
