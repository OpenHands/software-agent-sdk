# Design: connect SDK LLM profiles to agent-server

## Summary

Instead of reviving PR #1544's per-conversation `POST /api/conversations/{id}/llm`
contract, expose server-managed **LLM profiles** over REST and let conversation
creation/runtime switching refer to those profiles by ID.

This fits the SDK as it exists today:

- the SDK already has `LLMProfileStore`
- `LocalConversation` already has `switch_profile(profile_name)`
- conversation restore already follows the issue #1451 direction: runtime-provided
  agent components win on restore after tool verification

The missing link is agent-server. The server today has no profile store, no REST
surface for profiles, and no way for remote clients to bind a conversation to a
named profile without resending the full `LLM` payload.

## Why not reuse PR #1544's `/llm` approach?

PR #1544 proposed a generic per-conversation LLM setter:

- `POST /api/conversations/{id}/llm` with either `{ "profile_id": ... }` or
  `{ "llm": {...} }`
- plus a `POST /api/conversations/{id}/llm/switch` alias

That was reasonable at the time, but it predates the current SDK profile work.
Today it feels like the wrong abstraction for the main use case.

### Problems with `/llm`

1. **It conflates two different concerns**
   - defining/storing reusable LLM configurations
   - binding one conversation to one of those configurations

2. **It still encourages pushing full LLM payloads over REST**
   That is exactly what we want to avoid for remote clients that should not resend
   credentials on every start/switch/restore path.

3. **It does not reuse the SDK's current profile mental model**
   The SDK already has named profiles and `switch_profile()`. The server should
   speak that language too.

4. **It makes restore semantics less principled**
   A reusable profile reference gives us a clean story for issue #1451:
   "restore by rebuilding the runtime LLM from the current profile when possible."
   A raw `/llm` setter is just another inline override path.

## Current state in the codebase

### SDK pieces that already exist

#### 1. Named LLM profiles

`openhands-sdk/openhands/sdk/llm/llm_profile_store.py`

- `LLMProfileStore.save(name, llm, include_secrets=False)`
- `LLMProfileStore.load(name)`
- `LLMProfileStore.list()`
- `LLMProfileStore.delete(name)`

This is currently file-based and defaults to `~/.openhands/profiles`.

#### 2. Runtime conversation switching in local SDK

`openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`

`LocalConversation.switch_profile(profile_name)` already:

- loads a named profile from `LLMProfileStore`
- rewrites `usage_id` to `profile:{profile_name}`
- swaps `self.agent.llm`
- updates `self._state.agent`

Because `ConversationState.__setattr__` auto-persists public field changes,
that switch is already persisted to `base_state.json`.

One limitation today: `LocalConversation` constructs `LLMProfileStore()` with the
default directory. Agent-server will need a small additive way to point
`LocalConversation` at the server-configured profile store path when
`OH_LLM_PROFILES_PATH` is overridden.

#### 3. Restore-time flexibility already matches issue #1451

`openhands-sdk/openhands/sdk/conversation/state.py`

`ConversationState.create()` currently does this on restore:

1. load persisted `base_state.json`
2. verify tool compatibility with the runtime agent
3. replace `state.agent` with the runtime-provided agent
4. keep other persisted conversation state

That means we already have the important #1451 property for LLMs:
**if the runtime agent is built from a newer profile, restore will use it**.

### What agent-server does today

#### Start path

`openhands-agent-server/openhands/agent_server/conversation_service.py`

- `StartConversationRequest` requires a full `agent: Agent`
- the request is serialized into `StoredConversation`
- `StoredConversation` is persisted in `meta.json`

The start-related model inheritance today is:

```text
_StartConversationRequestBase
├── StartConversationRequest      # public /api/conversations contract
└── StartACPConversationRequest   # public /api/acp/conversations contract
    └── StoredConversation        # persisted meta.json model
```

The confusing part is that `StoredConversation` is **not** semantically
"the ACP-only stored model". It inherits from
`StartACPConversationRequest` because that model already uses the wider
`ACPEnabledAgent` type, so `StoredConversation` can persist either a normal
`Agent` or an `ACPAgent` without inventing a third agent field type.

#### Restore path

`openhands-agent-server/openhands/agent_server/event_service.py`

- the server loads `StoredConversation` from `meta.json`
- `EventService.start()` reconstructs `agent = type(self.stored.agent).model_validate(...)`
- it creates `LocalConversation(agent=agent, ...)`
- `LocalConversation` then restores `base_state.json`

So the server is already structurally ready for #1451-style restore behavior.
It just needs one more step: **resolve the active profile into the runtime agent
before constructing `LocalConversation`**.

### What is missing

1. no server-managed LLM profile store
2. no REST API for creating/listing/updating/deleting profiles
3. no way to start a conversation by profile reference
4. no REST endpoint to switch an existing conversation's active profile
5. no persisted conversation metadata telling the server that a conversation is
   profile-backed and should be re-resolved on restore

## Goals

1. Let remote clients define LLM profiles once over REST.
2. Let `POST /api/conversations` start a conversation from a profile reference.
3. Let an existing conversation switch to another profile over REST.
4. Make restore use the current profile contents when available.
5. Avoid resending credentials for every conversation start/switch.
6. Keep the REST rollout additive and backward compatible.
7. Reuse the SDK's existing `LLMProfileStore` and `LocalConversation.switch_profile`
   concepts rather than inventing a second model.

## Non-goals

1. Reworking the full conversation creation contract so `agent.llm` becomes optional.
   That is a larger API cleanup.
2. Generalizing this design to every agent component in one PR.
   This proposal is LLM-profile-specific.
3. Auto-pushing profile edits into already-running conversations.
   Changes should apply on explicit switch, or on restore.
4. Solving multi-tenant profile isolation.
   The current agent-server is effectively instance-scoped; profiles should be too.

## Proposed design

### 1. Add a server-managed profile store

Add agent-server config:

- `OH_LLM_PROFILES_PATH`
- default: `Path.home() / ".openhands" / "profiles"` (the existing SDK default)

Why keep the same home-scoped default for server too?

- it matches the existing `LLMProfileStore` behavior instead of introducing
  server-only path semantics
- it stays consistent with other per-user SDK data already stored under
  `~/.openhands/`
- putting profiles under `workspace/` does **not** meaningfully improve security in
  the current same-UID sandbox model, so a separate `workspace/llm_profiles`
  default would add divergence without real isolation
- deployments that want an explicit location can still override it via
  `OH_LLM_PROFILES_PATH`

I would also add an additive SDK knob such as:

- `LocalConversation(..., profile_store_dir: str | Path | None = None)`

so the existing `switch_profile()` implementation can keep working while letting
agent-server pass `OH_LLM_PROFILES_PATH` when it wants a non-default profile
store location.

### 2. Make `LLMProfileStore` cipher-aware

Today `LLMProfileStore.save()` can expose secrets, but it does not use the
agent-server cipher. For server-managed profiles we want the same secret-at-rest
behavior that conversations already have.

Proposed additive SDK change:

- `LLMProfileStore.save(..., include_secrets=False, cipher: Cipher | None = None)`
- `LLMProfileStore.load(..., cipher: Cipher | None = None)`

Implementation detail:

- save with `context={"cipher": cipher}` when `cipher` is provided
- otherwise preserve current SDK behavior

This keeps the store reusable by the SDK while letting agent-server persist
secret-bearing profiles safely.

### 3. Expose `/api/llm-profiles`

Add a new router alongside the existing `/api/llm` informational endpoints.

#### Endpoints

##### `GET /api/llm-profiles`
List profiles.

Response should be metadata-only, for example:

```json
{
  "profiles": [
    {
      "id": "fast",
      "llm": {
        "model": "openhands/gpt-5.2",
        "base_url": "https://..."
      }
    }
  ]
}
```

Secrets remain redacted.

##### `GET /api/llm-profiles/{profile_id}`
Return one redacted profile.

##### `PUT /api/llm-profiles/{profile_id}`
Create or replace one profile.

Request body:

```json
{
  "llm": { ... full LLM payload ... }
}
```

Server behavior:

- persist the profile under `{profile_id}`
- store secrets encrypted with the agent-server cipher
- normalize the runtime `usage_id` to `profile:{profile_id}` when the profile is
  later bound to a conversation

##### `DELETE /api/llm-profiles/{profile_id}`
Delete the profile definition.

### 4. Extend standard conversation creation to accept a profile reference

Add an optional field on the standard conversation start request and persist it in
stored metadata:

- `StartConversationRequest.llm_profile_id: str | None = None`
- `StoredConversation.llm_profile_id: str | None = None`

Do **not** add this to `StartACPConversationRequest`. `ACPAgent` does not execute
through `agent.llm`; it carries a sentinel `LLM(model="acp-managed")`, and the
actual remote model is chosen through ACP-native configuration such as
`ACPAgent.acp_model`. Advertising `llm_profile_id` on the ACP contract would look
supported while being a no-op.

#### Why put the field there?

**Option A: `_StartConversationRequestBase` or `StartACPConversationRequest`**

Pros:

- one declaration would flow into both public start contracts
- because `StoredConversation` inherits from `StartACPConversationRequest`, the
  field would also persist "for free"

Cons:

- it would advertise `llm_profile_id` on the ACP start API even though ACP does
  not actually select its runtime model through `agent.llm`
- that makes the OpenAPI contract misleading: clients would reasonably assume the
  field is supported for ACP when it is really ignored or rejected
- once the field is public on ACP, removing or changing it later becomes a REST
  compatibility problem

**Option B: `StartConversationRequest` plus `StoredConversation`**

Pros:

- the public API stays semantically honest: only the standard conversation start
  contract exposes an LLM-profile feature
- `StoredConversation` can still persist the profile reference for restore, even
  though it happens to inherit from the ACP request model for agent-type breadth
- it keeps room for ACP to design a separate, ACP-native model-selection field
  later if needed

Cons:

- we duplicate one field instead of inheriting it once
- `ConversationService` has to copy that field explicitly into
  `StoredConversation`, rather than getting it only through inheritance

I think that tradeoff is worth it. The duplication is small, while exposing a
standard-only concept on the ACP contract would be a long-lived semantic footgun.

#### Semantics

When `llm_profile_id` is **not** provided:

- current behavior is unchanged
- `agent.llm` from the request is used

When `llm_profile_id` **is** provided:

- the server loads that profile from the profile store
- the resolved profile LLM replaces `request.agent.llm`
- the conversation stores both:
  - the resolved `agent.llm` snapshot
  - the reference `llm_profile_id`


If `POST /api/conversations` is used with an already-open `conversation_id`, the
current idempotent behavior should stay the same: return the existing
conversation without applying a new profile. Changing an existing conversation's
profile should go through the dedicated switch endpoint.

This keeps the current request contract intact while making profile-based start
possible.

### 5. Add a conversation-scoped switch endpoint

Expose a dedicated profile-binding endpoint rather than a generic `/llm` setter.

#### V1 contract

- `POST /api/conversations/{conversation_id}/llm_profile`

ACP conversations should **not** get an equivalent endpoint in this design.
`ACPAgent` execution is ACP-server-managed, so swapping `agent.llm` would not
change the model that actually runs remotely. Any ACP-side model switching should
be designed separately around ACP-native configuration such as `acp_model`.

Request body:

```json
{ "profile_id": "fast" }
```

Server behavior:

1. load the profile from the server profile store
2. call conversation-side switch logic
3. update `stored.llm_profile_id`
4. update `stored.agent.llm` to the resolved snapshot
5. `save_meta()` immediately
6. return updated conversation info

### 6. Restore from profile first, snapshot second

This is the key restore behavior.

When `StoredConversation.llm_profile_id` is present:

1. attempt to load the named profile
2. if successful, inject it into the runtime agent before constructing
   `LocalConversation`
3. if loading fails, fall back to `stored.agent.llm` and log a warning

The fallback matters. A profile reference should not make an existing
conversation unrecoverable if the profile file was deleted or corrupted later.

This gives us the best of both worlds:

- **when the profile still exists**: restore sees the latest profile contents
- **when the profile is gone**: restore still has the last resolved snapshot

This is much more aligned with issue #1451 than a hard failure.

### 7. Keep profile edits explicit for live conversations

Updating a profile definition via `PUT /api/llm-profiles/{id}` should **not**
automatically mutate every conversation that references it.

Instead:

- existing live conversations keep their current in-memory `LLM`
- the new profile definition takes effect on:
  - the next explicit switch to that profile
  - the next server restart / restore of a conversation bound to that profile

This avoids surprising mid-run behavior.

## Detailed execution flow

### New conversation started from a profile

1. client creates/updates `fast` via `PUT /api/llm-profiles/fast`
2. client calls `POST /api/conversations` with:
   - normal `agent`, `workspace`, etc.
   - `llm_profile_id="fast"`
3. `ConversationService._start_conversation()` loads `fast`
4. it builds a resolved agent where `agent.llm = profile:fast`
5. `StoredConversation` persists:
   - `agent.llm` snapshot
   - `llm_profile_id="fast"`
6. `EventService.start()` constructs `LocalConversation`
7. `LocalConversation` persists the same resolved agent into `base_state.json`

### Existing conversation switched at runtime

1. client calls `POST /api/conversations/{id}/llm_profile`
2. server loads the named profile
3. conversation switches to the loaded profile
4. `ConversationState` auto-persists `state.agent`
5. agent-server updates `stored.llm_profile_id` and `stored.agent.llm`
6. agent-server saves `meta.json`
7. the updated `agent` is visible in `GET /api/conversations/{id}`

### Conversation restored after server restart

1. server loads `meta.json`
2. if `llm_profile_id` is set, it tries to resolve that profile
3. on success, it constructs the runtime agent from the latest profile contents
4. on failure, it uses the persisted `stored.agent.llm` snapshot
5. `LocalConversation` restores `base_state.json`
6. `ConversationState.create()` replaces restored `state.agent` with the runtime
   agent, so the resolved profile LLM becomes active for the resumed conversation

## API model changes

### New request/response models

- `UpsertLLMProfileRequest`
- `LLMProfileResponse`
- `LLMProfileListResponse`
- `SetConversationLLMProfileRequest`

### Additive fields on existing models

- `StartConversationRequest.llm_profile_id: str | None = None`
- `StoredConversation.llm_profile_id: str | None = None`
- `ConversationInfo.llm_profile_id: str | None = None`

`ACPConversationInfo` intentionally stays unchanged for now because `ACPAgent`
does not use `agent.llm` as its execution model selector.

Returning `llm_profile_id` in conversation info is important so remote clients can
see whether the conversation is profile-backed even though `agent.llm` is still
returned in expanded form.

## Runtime restrictions

I recommend that the switch endpoint reject changes while a conversation is
actively running.

Suggested behavior:

- if `execution_status == RUNNING`, return `409 Conflict`
- allow switching when idle/paused/finished/stuck

This keeps the first version simple and avoids racing a live agent step with an
LLM swap.

## RemoteConversation follow-up

This design is much more useful if the SDK's remote client can use it directly.

I would include these additive SDK changes in the same implementation if the
surface stays small:

1. `RemoteConversation(..., llm_profile_id: str | None = None)`
   - when creating a new non-ACP remote conversation, include the optional field
     in the POST payload
   - reject `llm_profile_id` for `ACPAgent` early rather than silently ignoring it

2. `RemoteConversation.switch_profile(profile_id: str)`
   - for non-ACP remote conversations, call the new REST endpoint
   - invalidate cached conversation info so the next state read reflects the new
     `llm_profile_id`
   - raise `NotImplementedError` or `ValueError` for `ACPAgent`

I would **not** make `switch_profile()` an abstract method on `BaseConversation`
in this step, because that is a broader public API change for external
subclasses. A non-abstract convenience method or a RemoteConversation-only method
is safer.

## Security / secret handling

Profiles are being introduced specifically so clients do not have to resend
credentials repeatedly. That makes secret persistence central to the design.

Recommended rules:

1. If the server has a cipher (`OH_SECRET_KEY`), profile secrets are stored
   encrypted at rest.
2. If the server has no cipher and the client tries to create a profile with
   secrets, reject the request with `400 Bad Request`.
3. `GET /api/llm-profiles*` never returns exposed secrets.
4. `ConversationInfo` continues returning the effective `agent.llm` with the
   existing redaction behavior.

This is stricter than the plain SDK utility, and that is good. The server should
not silently persist plaintext credentials.

## Backward compatibility

This rollout is additive:

- existing `/api/llm/providers` and `/api/llm/models*` remain unchanged
- existing `POST /api/conversations` payloads remain valid
- clients that do not know about profiles keep sending inline `agent.llm`
- no existing REST path is removed or repurposed

This also avoids reviving `/api/conversations/{id}/llm`, so we do not create a
second long-term public contract that we may later regret.

## Alternatives considered

### A. Reintroduce `/api/conversations/{id}/llm`

Rejected as the primary path.

Reason: it pushes us toward inline LLM transport instead of reusable profile
references, and it duplicates concepts the SDK already has.

### B. Only add `/api/llm-profiles`, but no conversation switch endpoint

Rejected.

Reason: then profiles help creation/restore, but not runtime switching. That
would still leave remote clients behind the local SDK API.

### C. Store only `llm_profile_id`, not the resolved snapshot

Rejected.

Reason: deleting or corrupting a profile would make restore brittle. Keeping the
resolved snapshot in `StoredConversation.agent.llm` is a cheap safety net.

## Recommendation

Build this as a **profile-first** integration:

1. server-managed `/api/llm-profiles`
2. additive `llm_profile_id` on standard conversation creation/info models plus
   `StoredConversation`
3. dedicated standard-conversation profile switch endpoint
4. restore-time re-resolution from profile, with snapshot fallback
5. small `RemoteConversation` convenience additions for non-ACP conversations

This reuses the SDK's current LLM profile implementation, aligns with issue
#1451's runtime/restore flexibility, and avoids making `/llm` the main public
abstraction for a problem that is really about reusable named profiles.

## Implementation notes for the eventual PR

The lowest-risk implementation path looks like this:

1. make `LLMProfileStore` cipher-aware
2. add `OH_LLM_PROFILES_PATH` and a tiny server wrapper around the store
3. add `/api/llm-profiles` CRUD + tests
4. add `llm_profile_id` to standard conversation request/info models and
   `StoredConversation`
5. resolve profiles inside `ConversationService._start_conversation()`
6. add standard-conversation switch endpoint + tests
7. add restore-time profile resolution in `EventService.start()`
8. add `RemoteConversation` support + tests

That sequence keeps each step reviewable and preserves backward compatibility at
all times.
