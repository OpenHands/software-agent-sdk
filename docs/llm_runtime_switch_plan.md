# Runtime LLM Profile Switching – Implementation Plan (agent-sdk-25)

This plan builds on the investigation captured in `docs/llm_runtime_switch_investigation.md` and outlines the work required to let callers swap an agent’s primary LLM to another persisted profile at runtime.

## Goals

1. Allow manual or automated selection of a persisted LLM profile while a conversation is active.
2. Keep the LLMRegistry as the single source of truth for active LLM instances and profile knowledge.
3. Respect existing persistence behaviour:
   - Profile-reference mode (`OPENHANDS_INLINE_CONVERSATIONS=false`) → switching is supported.
   - Inline mode (`OPENHANDS_INLINE_CONVERSATIONS=true`) → switching is rejected early with a clear error.
4. Maintain or improve unit/integration test coverage.

## Proposed architecture changes

### 1. Extend LLMRegistry

Add a `switch_profile(service_id: str, profile_id: str) -> LLM` method that:
- Loads `profile_id` via existing helpers (re-using `_load_profile_with_synced_id`).
- Registers the loaded LLM (using `add` semantics) **replacing** the previous instance for `service_id`.
- Publishes a `RegistryEvent` (re-using the existing subscriber hook) so listeners can update state.
- Returns the new `LLM` instance so callers can synchronously update their agent/state.

Implementation notes:
- If the profile is already active, short-circuit and return the existing LLM.
- Raise a descriptive error when the profile is missing or when the service ID is unknown.

### 2. Conversation-level coordination

Introduce a method on `ConversationState` (and corresponding `LocalConversation`) such as `switch_agent_llm(profile_id: str)` which orchestrates the swap:

1. Check `should_inline_conversations()`; if inline mode is enabled, raise an `LLMSwitchError` instructing the caller to disable inline persistence.
2. Resolve the agent’s `service_id` (current LLM) and call `LLMRegistry.switch_profile(...)`.
3. Update `state.agent` with a new agent object whose `.llm` is the returned instance.
   - To bypass the strict `resolve_diff_from_deserialized` diff, introduce an internal helper on `AgentBase`, e.g. `_with_swapped_llm(new_llm: LLM)`, that clones the agent via `model_copy(update={"llm": new_llm})`. This avoids running `resolve_diff_from_deserialized`, which would otherwise reject the change.
   - Apply the same logic to any condensers or secondary LLMs once the need arises (out of scope for v1).
4. Update `ConversationState.stats` bookkeeping:
   - Either reset the metrics entry for the service to zero or continue accumulating under the same key (decision: start a fresh metrics entry to avoid mixing usage between profiles).
5. Persist immediately by calling existing save hooks (`_save_base_state`) to ensure the new profile ID is captured.

A convenience method on `LocalConversation` (e.g. `switch_llm(profile_id: str)`) will forward to the state method and surface the error if inline mode blocks the operation.

### 3. User-facing API

Depending on SDK ergonomics, expose the feature through:
- A direct conversation method (`conversation.switch_llm("profile-a")`).
- CLI / higher-level integration left for a follow-up once core switching works.

### 4. Prevent mid-operation switches (initial scope)

For the first iteration we can require `state.agent_status == AgentExecutionStatus.IDLE` before switching. If the agent is mid-run we raise an error to avoid edge cases with in-flight steps. Future work can relax this once step coordination is available.

## Testing strategy

1. **Registry unit tests**
   - New suite in `tests/sdk/llm/test_llm_registry_profiles.py` for `switch_profile` covering:
     - Successful swap (existing service → new profile).
     - Swap to the same profile is a no-op.
     - Unknown profile → raises.
     - Unknown service → raises.
     - Subscriber notifications fire.

2. **Conversation integration tests** (extend `tests/sdk/conversation/local/test_state_serialization.py` or create new module):
   - Switching succeeds in profile-reference mode, updating `ConversationState.agent.llm.profile_id` and saving the new profile ID to disk.
   - Switching persists across reloads (start a conversation, switch, re-open conversation, verify new profile in state and registry).
   - Switching is rejected when `OPENHANDS_INLINE_CONVERSATIONS=true`.
   - Switching while the agent is not idle (if enforced) raises the appropriate error.

3. **Metrics tests**
   - Verify that stats either reset or continue according to the chosen policy by inspecting `ConversationState.stats.service_to_metrics` before/after the swap.

4. **Serialization tests**
   - Ensure `compact_llm_profiles` writes the new `profile_id` after a switch, and `resolve_llm_profiles` rehydrates it successfully on reload.

5. **Subscriber behaviour**
   - If we extend beyond a single subscriber, add tests for multiple listeners; otherwise ensure the existing single-subscriber path still works.

## Rollout steps

1. Implement `LLMRegistry.switch_profile` plus targeted unit tests.
2. Wire conversation-level orchestration (`ConversationState` and `LocalConversation`).
3. Expose public API and update documentation (including usage notes and inline-mode restriction).
4. Add/cherry-pick helper utilities (e.g., agent cloning) as required.
5. Update CLI or higher-level integrations if time permits (optional follow-up).
6. Ensure documentation references the new feature in the LLM profiles guide.

## Risks & mitigations

- **Strict diff enforcement**: bypassed via controlled cloning instead of calling `resolve_diff_from_deserialized` when profile references are in use.
- **Persistence consistency**: immediate re-save after switching guarantees the new profile ID lands on disk.
- **Concurrent use**: initial restriction to idle state avoids race conditions; revisit later if needed.
- **Backward compatibility**: existing code paths remain untouched unless `switch_llm(...)` is invoked.
