# Design assessment: make LLM profiles the persistent/user-facing LLM source of truth

## Issue found

- GitHub issue: [OpenHands/software-agent-sdk#3511](https://github.com/OpenHands/software-agent-sdk/issues/3511)
- Title: **Simplify LLM settings around profiles and remove raw LLM config from settings surfaces**
- Author: `enyst`
- State: `OPEN`
- Created: 2026-06-04

The issue asks for LLM profiles to become the single persistent and user-facing source of truth for model/base URL/API key/options. Raw `LLM(...)` construction should keep working in Python, but raw LLM objects should be materialized into a profile when they cross persistence or REST boundaries.

## Summary recommendation

Do **not** replace `LLM` inside the runtime agent core. `Agent.llm`, `LocalConversation.switch_llm()`, condensers, title generation, token accounting, hooks, ACP provider env derivation, and LiteLLM transport code all need a fully resolved `LLM` object at execution time.

Do replace raw `LLM` on persistence and app/server boundaries with profile references wherever possible:

1. settings persistence and settings REST
2. profile activation
3. conversation start and stored conversation metadata
4. remote conversation creation
5. model switching endpoints
6. SDK/workspace helper APIs that currently fetch plaintext settings

The clean shape is: **persist profile references; resolve them to `LLM` only at the runtime boundary**.

## Current local state

### Existing profile infrastructure

The SDK already has file-backed profiles in `LLMProfileStore`:

- `openhands-sdk/openhands/sdk/llm/llm_profile_store.py:101` saves a named `LLM` profile.
- `openhands-sdk/openhands/sdk/llm/llm_profile_store.py:172` loads a named profile into a runtime `LLM`.
- `openhands-sdk/openhands/sdk/llm/llm_profile_store.py:252` lists summaries without constructing an `LLM`.
- `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py:812` already supports `switch_profile(profile_name)` by loading a profile and delegating to `switch_llm()`.
- `openhands-agent-server/openhands/agent_server/conversation_router.py:339` already exposes `POST /api/conversations/{id}/switch_profile`.

This is a good base. The missing piece is a first-class **profile reference** in settings/conversation payloads.

### Raw LLM surfaces to replace or deprecate

| Area | Current raw-LLM behavior | Files |
| --- | --- | --- |
| SDK agent settings | `OpenHandsAgentSettings.llm: LLM`; `create_agent()` passes it to `Agent` and condenser | `openhands-sdk/openhands/sdk/settings/model.py:791`, `:895` |
| ACP settings | `ACPAgentSettings.llm: LLM` for metrics and provider env | `openhands-sdk/openhands/sdk/settings/model.py:1227`, `:1282` |
| Persisted settings | `settings.json` stores `agent_settings.llm`; `active_profile` is only metadata | `openhands-agent-server/openhands/agent_server/persistence/models.py:132`, `:136`, `:151` |
| Settings REST | `GET /api/settings` returns `agent_settings` containing raw `llm`; `PATCH /api/settings` accepts `agent_settings_diff.llm` | `openhands-agent-server/openhands/agent_server/settings_router.py`, `openhands-sdk/openhands/sdk/settings/api_models.py:96`, `:137` |
| Profiles REST activation | Activating a profile copies profile config into `agent_settings.llm` | `openhands-agent-server/openhands/agent_server/profiles_router.py:280`, `:313` |
| Conversation start | `StartConversationRequest.agent_settings` is turned into an `Agent` during Pydantic validation; raw `agent` may also carry `agent.llm` | `openhands-sdk/openhands/sdk/conversation/request.py:202`, `:213` |
| Stored conversations | `StoredConversation` extends `StartConversationRequest`; `meta.json` serializes the resolved agent/LLM | `openhands-agent-server/openhands/agent_server/models.py:74`, `openhands-agent-server/openhands/agent_server/event_service.py:100`, `:705` |
| Runtime switch endpoint | `/switch_llm` accepts a raw `LLM` JSON object | `openhands-agent-server/openhands/agent_server/conversation_router.py:371` |
| Remote SDK | `RemoteConversation` creates conversations by sending raw serialized `agent`, including secrets when exposed | `openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py:749` |
| Remote workspace helpers | `get_llm()` fetches plaintext `/api/settings` or profile detail and returns raw `LLM` | `openhands-sdk/openhands/sdk/workspace/remote/base.py:341`, `:387` |
| Cloud workspace helper | `get_llm()` fetches SaaS user LLM settings/profiles and returns raw `LLM` | `openhands-workspace/openhands/workspace/cloud/workspace.py:572` |

## Proposed data model

### New lightweight reference model

Introduce a small typed model or alias in the SDK, near profile-store code:

```python
class LLMProfileRef(BaseModel):
    name: str
```

A plain string field can work initially, but a typed reference makes API schemas and future validation clearer.

Recommended fields:

- `llm_profile: LLMProfileRef | str | None` on OpenHands agent settings.
- `active_profile: str | None` remains the account/session default in persisted settings.
- `profile_name: str | None` or `llm_profile: ...` on conversation-start payloads.

### Compatibility fields

Keep `llm: LLM | None` as a legacy/Python-convenience field during the migration. It should be accepted on input but not preferred on persisted/server-owned output after migration.

Rules:

1. If `llm_profile` is present, resolve that profile.
2. Else if persisted settings have `active_profile`, resolve that profile.
3. Else if legacy `llm` exists, materialize it into a default profile and write only the reference on the next save.
4. Else use the default profile if present, or fail with a clear setup error.

## Proposed architecture

### Boundary split

- **Runtime boundary:** accepts/resolves to `LLM` because model calls need concrete model/base URL/API key/options.
- **Persistence/REST boundary:** carries profile names/references, not raw LLM secrets.
- **Python construction boundary:** remains ergonomic and backward compatible: `Agent(llm=LLM(...))` continues to work.

### Centralized resolver

Add a single resolver function/service used by settings creation, conversation start, title generation, ACP setup, and switching:

```python
def resolve_llm_profile(
    profile_name: str | None,
    *,
    active_profile: str | None,
    profile_store: LLMProfileStore,
    cipher: Cipher | None,
    legacy_llm: LLM | None = None,
) -> LLM:
    ...
```

This avoids duplicating fallback and secret-decryption behavior across routers and SDK helpers.

### Agent creation

`OpenHandsAgentSettings.create_agent()` currently has no profile-store/cipher context, so it cannot resolve encrypted profiles by itself. Options:

1. Add a resolver parameter while keeping the current no-arg behavior for legacy raw `llm` settings.
2. Add a server-side `create_agent_from_settings(settings, resolver=...)` helper and keep `create_agent()` as Python/raw-LLM convenience.
3. Split persisted settings from resolved runtime settings.

Recommended first step: option 2. It minimizes public API disruption and makes the boundary explicit.

## Migration plan

### Phase 1: Add profile references without removing raw LLM

Implementation:

- Add `LLMProfileRef` or a typed `llm_profile: str | None` field.
- Add resolver helper with profile/default/legacy fallback logic.
- Add settings migration that converts persisted `agent_settings.llm` into a default profile when possible and sets `active_profile` or `agent_settings.llm_profile`.
- Change profile activation to update only `active_profile`/`llm_profile`, not copy raw config into `agent_settings.llm`.
- Add conversation-start support for `profile_name`/`llm_profile` while keeping raw `agent` and `agent_settings.llm` accepted.
- Keep `/switch_llm` as deprecated compatibility because its docstring says it supports app-servers that own LLMs directly and do not push profiles to the agent-server filesystem.

Expected local implementation files: about **8-10**.

Primary files:

- `openhands-sdk/openhands/sdk/llm/llm_profile_store.py`
- `openhands-sdk/openhands/sdk/settings/model.py`
- `openhands-sdk/openhands/sdk/settings/api_models.py`
- `openhands-sdk/openhands/sdk/conversation/request.py`
- `openhands-agent-server/openhands/agent_server/persistence/models.py`
- `openhands-agent-server/openhands/agent_server/settings_router.py`
- `openhands-agent-server/openhands/agent_server/profiles_router.py`
- `openhands-agent-server/openhands/agent_server/conversation_router.py`
- `openhands-agent-server/openhands/agent_server/event_service.py`

Tests/examples affected: about **8-12** local files.

### Phase 2: Make server-owned REST responses profile-first

Implementation:

- `GET /api/settings` returns profile refs/default profile state and either omits `agent_settings.llm` or includes it only as deprecated compatibility.
- `PATCH /api/settings` accepts profile refs and still accepts raw `agent_settings_diff.llm` by materializing it into a profile.
- `POST /api/conversations` can start from `profile_name` plus non-LLM agent settings.
- `StoredConversation`/`meta.json` stores the chosen profile reference instead of serialized raw LLM secrets.
- `RemoteConversation` should prefer sending profile refs for server-owned profiles. Raw `agent` remains the Python escape hatch.

Expected local implementation files: about **6-8** more, overlapping phase 1.

### Phase 3: Deprecate raw switch/settings paths

Implementation:

- Mark `/switch_llm` and raw `agent_settings.llm` request paths deprecated in OpenAPI/docs.
- Update examples to use profiles and `switch_profile`.
- Remove frontend/server call sites that fetch plaintext settings only to start a conversation.
- After the required REST compatibility runway, remove raw LLM from server-owned settings surfaces.

Expected local implementation files: about **4-6**, mostly docs/examples/tests after phase 2.

## Testing impact

High-churn local tests:

- `tests/agent_server/test_settings_router.py`: many assertions under `agent_settings["llm"]`; update to profile refs and legacy materialization tests.
- `tests/agent_server/test_profiles_router.py`: activation currently asserts raw LLM copied into settings.
- `tests/agent_server/test_conversation_router.py`: add profile-start tests and keep raw `/switch_llm` compatibility tests.
- `tests/agent_server/test_conversation_service.py`: stored conversation encryption/resume expectations change.
- `tests/sdk/conversation/test_switch_model.py`: profile switching remains, raw switching stays runtime-only.
- `tests/sdk/workspace/remote/test_remote_workspace.py`: `get_llm()` behavior may need profile-first alternatives.
- `tests/workspace/test_cloud_workspace_sdk_settings.py`: SaaS profile payload expectations.
- `tests/sdk/llm/test_llm_profile_store.py`: likely unchanged unless a new profile-ref model/store helper is added.

Examples likely needing updates:

- `examples/02_remote_agent_server/12_settings_and_secrets_api.py`
- `examples/02_remote_agent_server/13_workspace_get_llm.py`
- `examples/02_remote_agent_server/10_cloud_workspace_share_credentials.py`
- `examples/01_standalone_sdk/37_llm_profile_store/main.py`
- model switching/fallback examples if endpoint names or payloads change.

## Scope estimate

This is **medium-large** and should not be a single mechanical rename.

Approximate local change size:

- **Implementation files:** 12-18 total across SDK, agent-server, and workspace packages.
- **Test files:** 8-14 total.
- **Examples/docs:** 4-6 total.
- **Migration logic:** at least 2 paths: persisted settings and stored conversations. Profile files may not need migration unless their schema changes.
- **Cross-repo follow-up:** agent-canvas, OpenHands app-server, OpenHands CLI, and automation all have call sites called out in #3511.

Recommended PR breakdown:

1. Add profile-ref model/resolver and settings/profile activation migration.
2. Add profile-reference conversation start and stored conversation support.
3. Update remote SDK/workspace helpers and examples.
4. Downstream repo updates and deprecation cleanup.

## Risks and compatibility notes

1. **Public REST compatibility:** Raw `llm` cannot disappear immediately from settings/conversation APIs. Additive changes first; deprecate before removal.
2. **Conversation resume semantics:** If `meta.json` stores only a profile name, profile edits/deletes after conversation creation can change or break resume. Decide whether conversation metadata stores only a ref, a non-secret profile snapshot, or requires profile immutability for active conversations.
3. **Secret handling:** This should reduce secret duplication, but migration must avoid writing plaintext raw `LLM` into settings during materialization.
4. **Profile-store locality:** `/switch_llm` exists because some app-servers own LLM config and do not push profiles to the agent-server filesystem. Replacing it requires a way for the runtime server to resolve externally owned profiles.
5. **ACP:** ACP settings use `llm` for cost attribution and provider env. This can become a profile reference, but resolution must happen before provider env/secret registration.
6. **Python API:** Raw `LLM` remains a valid runtime object and public SDK construction pattern. The invariant should apply when persisting or sending through REST, not to in-process Python objects.

## Open questions

1. Should `llm_profile` live in `agent_settings`, top-level persisted settings, or both?
2. What should happen to running/stored conversations when a referenced profile is renamed, deleted, or edited?
3. Should raw `Agent` JSON over remote conversation creation be materialized into a temporary/default profile automatically, or remain a Python-only escape hatch?
4. Do server-side profiles remain filesystem-local, or should the runtime server resolve profiles through the app-server/cloud in SaaS deployments?
5. Is the ACP `llm` field a true LLM profile reference, or should ACP get a separate provider-credentials/profile reference concept?

## Acceptance criteria for the first implementation wave

- Existing raw Python usage (`LLM(...)`, `Agent(llm=...)`) still works.
- Activating a profile no longer duplicates raw profile config into `agent_settings.llm`.
- Persisted settings can load legacy `agent_settings.llm` and materialize it into a profile reference.
- New settings/conversation APIs can use profile references without sending plaintext/encrypted raw LLM settings.
- `/switch_profile` remains the preferred runtime switching path.
- `/switch_llm` remains available but documented as legacy/compatibility until external-owner profile resolution exists.
