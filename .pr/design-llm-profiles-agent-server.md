# Design: connect SDK LLM profiles to agent-server

## Summary

Instead of reviving PR #1544's per-conversation `POST /api/conversations/{id}/llm`
contract, expose `/api/llm-profiles` as REST CRUD over the SDK's existing
`LLMProfileStore`, then let conversation creation/runtime switching refer to
those named profiles by ID.

This fits the SDK as it exists today:

- the SDK already has `LLMProfileStore`
- `LocalConversation` already has `switch_profile(profile_name)`
- conversation restore already follows the issue #1451 direction: runtime-provided
  agent components win on restore after tool verification

The missing link is agent-server. The server today has no REST surface for the
existing profile store, no wiring to use that same store for
start/switch/restore, and no way for remote clients to bind a conversation to a
named profile without resending the full `LLM` payload.

The important design constraint is: `/api/llm-profiles` should be a thin REST
wrapper over that same store, not a second server-side storage model.

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

One limitation only appears if agent-server wants a non-default profile location.
Today `LocalConversation` constructs `LLMProfileStore()` with the SDK default
store directory. If A1 keeps that same default, `switch_profile()` already lines
up. We only need an additive store-dir knob later if agent-server introduces an
explicit override path.

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

1. no REST API for remote CRUD over the existing profile store
2. no agent-server wiring to the existing `LLMProfileStore` for conversation
   start/switch/restore
3. no way to start a conversation by profile reference
4. no REST endpoint to switch an existing conversation's active profile
5. no persisted conversation metadata telling the server that a conversation is
   profile-backed and should be re-resolved on restore

## Goals

1. Let remote clients create/list/update/delete LLM profiles over REST.
2. Let `POST /api/conversations` start a conversation from a profile reference.
3. Let an existing conversation switch to another profile over REST.
4. Make restore use the current profile contents when available.
5. Avoid resending credentials for every conversation start/switch.
6. Keep the REST rollout additive and backward compatible.
7. Reuse the SDK's existing `LLMProfileStore` and `LocalConversation.switch_profile`
   concepts rather than inventing a second model.
8. Keep `/api/llm-profiles` as a thin wrapper over the same store.

## Non-goals

1. Reworking the SDK/Python/REST agent contract so a standard `Agent` can be
   configured by either an inline `LLM` or an `llm_profile_id`.
   That is the larger A2 alternative below, not the recommendation here.
2. Generalizing this design to every agent component in one PR.
   This proposal is LLM-profile-specific.
3. Auto-pushing profile edits into already-running conversations.
   Changes should apply on explicit switch, or on restore.
4. Solving multi-tenant profile isolation.
   The current agent-server is effectively instance-scoped; profiles should be too.

## Creation-contract alternatives

There are two viable directions here.

### A1. Additive profile binding with `agent.llm` unchanged

This document recommends A1.

- keep standard `Agent.llm` required everywhere it is required today
- add `llm_profile_id` only on standard conversation REST models:
  `StartConversationRequest`, `StoredConversation`, and `ConversationInfo`
- treat `llm_profile_id` as server-managed metadata that resolves to a runtime
  `agent.llm`
- persist both:
  - the resolved `agent.llm` snapshot
  - the separate `llm_profile_id` reference
- make no SDK `Agent` or `LLM` schema change

Why A1 first:

- it is fully additive for the current REST contract
- it keeps the SDK Python `Agent` contract unchanged
- it keeps unresolved profile references out of `ConversationState` and agent
  internals
- it avoids ACP/schema cleanup in the same step

### A2. Standard `Agent` accepts either `llm` or `llm_profile_id`

This is the larger cleanup alternative behind non-goal #1.

The important distinction from A1 is that A2 is **not** "put `profile_id`
inside `LLM`". It is: the public standard `Agent` configuration becomes one-of:

- `Agent(llm=LLM(...), ...)`
- `Agent(llm_profile_id="fast", ...)`

Exact model shape still needs design, but semantically it means the standard
agent contract can carry either a concrete LLM config or an unresolved profile
reference.

Blast radius relative to A1:

1. **SDK/Python Agent contract**
   - `openhands.sdk.agent.base.AgentBase` today requires `llm: LLM`
   - `Agent`, `Conversation(...)`, `RemoteConversation(...)`, and direct
     `Agent.model_dump()` / `model_validate()` round-trips all assume that
   - A2 therefore changes the public Python API, not just the REST start request
   - to keep the contract explicit, this likely wants `llm: LLM | None` plus
     `llm_profile_id: str | None` with an exactly-one validator, or a dedicated
     agent-spec union; `llm: LLM | str` would be too ambiguous

2. **Runtime resolution point**
   - the runtime cannot freely carry an unresolved profile reference today
   - agent code directly reads `self.llm.model` and other concrete `LLM`
     attributes
   - so A2 needs a principled resolution step before an `Agent` is used by
     `LocalConversation`, `RemoteConversation`, or `ConversationState.create()`
   - that raises a design question: do we let the runtime `Agent` be partially
     unresolved, or do we introduce a separate agent-spec / resolver boundary so
     runtime agents still always have a real `LLM`?

3. **Serialization and persistence**
   - today an `Agent` model is already its serializable representation
   - under A2, `agent.model_dump()` might now produce an unresolved
     `{..., "llm_profile_id": "fast"}` shape instead of a concrete `llm`
   - that affects `StoredConversation` `meta.json`, `ConversationState`
     `base_state.json`, and any direct SDK serialization/deserialization of
     `Agent`
   - if we still want restore to survive deleted or corrupted profiles, we
     probably need both:
     - the profile reference
     - a resolved snapshot fallback
   - so A2 is not automatically simpler on persistence

4. **Conversation restore semantics**
   - `ConversationState.create()` currently validates the persisted agent,
     verifies tools, then replaces `state.agent` with the runtime agent
   - if persisted state can contain an unresolved profile reference, local SDK
     restore needs a way to resolve it, not just agent-server restore
   - that means the resolution logic must actually live in the local SDK layer

5. **RemoteConversation and REST/OpenAPI**
   - `RemoteConversation` currently serializes `agent.model_dump(...)` straight
     into `POST /api/conversations`
   - with A2, the embedded standard `Agent` schema changes, so REST changes are
     downstream of the Python API change
   - the request path can likely be rolled out additively, but the response
     contract needs a deliberate choice: should `GET /api/conversations/{id}`
     return the original unresolved agent shape, the resolved runtime
     `agent.llm`, or both?
   - that choice affects backward compatibility and the `Agent server REST API
     breakage checks` workflow

6. **ACP / model hierarchy**
   - if we push the one-of fields down into `AgentBase`, ACP inherits them even
     though ACP execution is not selected via `agent.llm`
   - if we keep A2 standard-`Agent`-only, then `Agent` and `ACPAgent` no longer
     share the same `llm` contract, and the model hierarchy becomes more
     asymmetric
   - either way, A2 has more schema fallout than A1

7. **Conversation surface area**
   - because the public agent contract changes, the blast radius is broader than
     just start/switch endpoints
   - `Conversation(...)`, `LocalConversation`, `RemoteConversation`, and any
     helper that assumes a concrete agent `llm` need a documented resolution
     story

At minimum, A2 needs a clear answer to: what is the canonical serialized form of
standard `Agent`? If the answer is "either inline `llm` or `llm_profile_id`",
then this is a broader SDK contract redesign, not just an agent-server feature.

I think A2 is a legitimate future cleanup, but it is materially larger than A1
because it changes the public `Agent` contract and pushes profile resolution
into core SDK construction/serialization paths, not only the REST layer.

## Proposed design (A1)

### 1. Reuse `LLMProfileStore` directly and expose it over REST

A1 does not need a new agent-server "profile store" abstraction or a second
persistence model. The server should use the existing `LLMProfileStore`
directly, both internally and through REST.

Where the server needs it:

- **CRUD**: `/api/llm-profiles` should create/list/load/delete profiles through
  that same store so REST clients can manage reusable profiles
- **start**: resolve `llm_profile_id` into a concrete `LLM` before creating the
  runtime conversation
- **switch**: load the requested profile before calling conversation-side switch
  logic
- **restore**: re-resolve `StoredConversation.llm_profile_id` before constructing
  `LocalConversation`

That is the required profile-store surface for A1.

For the initial design, I would keep the existing SDK default directory
`Path.home() / ".openhands" / "profiles"`.

Why keep the same default for server too?

- it matches the existing `LLMProfileStore` behavior instead of introducing
  server-only path semantics
- it stays consistent with other per-user SDK data already stored under
  `~/.openhands/`
- putting profiles under `workspace/` does **not** meaningfully improve security in
  the current same-UID sandbox model, so a separate `workspace/llm_profiles`
  default would add divergence without real isolation
- it avoids inventing a server-only store location just for A1

If deployments later need an explicit location, we can add an additive knob such
as `OH_LLM_PROFILES_PATH`, and then a matching SDK hook such as
`LocalConversation(..., profile_store_dir: str | Path | None = None)`. That is
follow-up flexibility for deployment layout, not a separate storage design.

### 2. Make `LLMProfileStore` cipher-aware for REST profile writes

Because A1 does require remote profile create/update over REST,
`LLMProfileStore.save()` should learn to use the agent-server cipher so
server-created secret-bearing profiles get the same secret-at-rest behavior that
conversations already have.

Proposed additive SDK change:

- `LLMProfileStore.save(..., include_secrets=False, cipher: Cipher | None = None)`
- optionally `LLMProfileStore.load(..., cipher: Cipher | None = None)` if the
  persisted profile format needs decryption on read

Implementation detail:

- save with `context={"cipher": cipher}` when `cipher` is provided
- otherwise preserve current SDK behavior

That keeps the store reusable by the SDK while making the required REST write
path safe.

### 3. Expose `/api/llm-profiles` as a thin wrapper over the store

Add a new router alongside the existing `/api/llm` informational endpoints.
This should stay a thin wrapper over `LLMProfileStore`, not a separate storage
layer.

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

Updating a profile via `PUT /api/llm-profiles/{id}` should **not** automatically
mutate every conversation that references the profile.

Instead:

- existing live conversations keep their current in-memory `LLM`
- the new profile definition takes effect on:
  - the next explicit switch to that profile
  - the next server restart / restore of a conversation bound to that profile

This avoids surprising mid-run behavior.

## Detailed execution flow

### New conversation started from a profile

1. client creates or updates `fast` via `PUT /api/llm-profiles/fast`
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
credentials repeatedly. Because A1 includes REST profile CRUD, secret
persistence is central to the design.

Recommended rules:

1. If the server has a cipher (`OH_SECRET_KEY`), profile secrets are stored
   encrypted at rest.
2. If the server has no cipher and the client tries to create a profile with
   secrets, reject the request with `400 Bad Request`.
3. `GET /api/llm-profiles*` never returns exposed secrets.
4. `ConversationInfo` continues returning the effective `agent.llm` with the
   existing redaction behavior.

This is stricter than the plain SDK utility, and that is good. The server should
not silently persist plaintext credentials when it accepts secret-bearing
profiles via REST.

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

Build this as a **profile-first** integration, with REST profile management as a
first-class part of A1:

1. expose `/api/llm-profiles` CRUD as a thin wrapper over the existing
   `LLMProfileStore`
2. reuse that same `LLMProfileStore` directly for conversation start,
   switch, and restore
3. add additive `llm_profile_id` on standard conversation creation/info models
   plus `StoredConversation`
4. add a dedicated standard-conversation profile switch endpoint
5. restore-time re-resolution from profile, with snapshot fallback
6. add small `RemoteConversation` convenience additions for non-ACP conversations

This reuses the SDK's current LLM profile implementation, aligns with issue
#1451's runtime/restore flexibility, and avoids making `/llm` the main public
abstraction for a problem that is really about reusable named profiles.

## Implementation notes for the eventual PR

The lowest-risk implementation path looks like this:

1. make `LLMProfileStore` cipher-aware for REST profile writes
2. add `/api/llm-profiles` CRUD + tests as a thin wrapper over
   `LLMProfileStore()`
3. add `llm_profile_id` to standard conversation request/info models and
   `StoredConversation`
4. resolve profiles inside `ConversationService._start_conversation()` using the
   existing `LLMProfileStore()`
5. add standard-conversation switch endpoint + tests
6. add restore-time profile resolution in `EventService.start()`
7. add `RemoteConversation` support + tests
8. only if deployments actually need it, add `OH_LLM_PROFILES_PATH` and a
   matching SDK store-dir hook

That sequence keeps the first A1 slice reviewable, preserves backward
compatibility at all times, and avoids adding redundant profile-store
abstractions before they are needed.
