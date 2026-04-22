# Extensions Config — Implementation Plan

## Goal

A unified `Extensions` bundle that holds the resolved set of skills, hooks,
MCP servers, and agent definitions. Sources produce bundles, bundles merge
with clean first-wins semantics, and the conversation consumes the final
merged bundle to configure the agent. Public API doesn't change until
everything is proven internally.

---

## Phase 1 — Extensions Bundle

**What**: An immutable Pydantic model that is the canonical "resolved extensions" container.

```
openhands-sdk/openhands/sdk/extensions/extensions.py
```

### Fields

| Field | Type | Description |
|---|---|---|
| `skills` | `list[Skill]` | Loaded skills from any source |
| `hooks` | `HookConfig \| None` | Merged hook configuration |
| `mcp_config` | `dict[str, Any]` | MCP server configuration (mcpServers dict shape) |
| `agents` | `list[AgentDefinition]` | Subagent definitions |

Commands (`CommandDefinition`) are flattened to skills before entering the
bundle — they stay a plugin-internal concept.

### Merge operation

`Extensions.merge(other: Extensions) -> Extensions` — binary operation,
`self` wins on collision (first-wins):

- **skills**: first-wins by name (`self` kept on collision, `other` only adds new names)
- **hooks**: concatenate (`self` hooks run before `other` hooks, via `HookConfig.merge`)
- **mcp_config**: first-wins — merge `mcpServers` by server name (`self` kept on collision), `self` kept for other top-level keys
- **agents**: first-wins by name (`self` kept on collision, `other` only adds new names)

All keyed fields use the same precedence direction: `self` (base) wins.
Caller controls precedence by placing the highest-precedence bundle first.
Class also gets:
- `Extensions.empty()` — classmethod returning an empty bundle (identity for merge)
- `Extensions.is_empty() -> bool` — check if everything is empty/None
- `Extensions.collapse(bundles: list[Extensions]) -> Extensions` — reduce via merge (first entry = highest precedence)

### Location

`openhands/sdk/extensions/extensions.py`. Not yet exported from
`openhands/sdk/extensions/__init__.py` — will be exported when the
internal adoption (Phase 4) is complete and the API is ready.

### Tests

`tests/sdk/extensions/test_bundle.py` — unit tests for merge semantics,
empty bundle, collapse, edge cases (duplicate skill names, overlapping MCP
servers, etc).

---

## Phase 2 — Sources

**What**: Functions that produce an `Extensions` bundle from each source type.
Not a formal ABC — just functions grouped in a module.

```
openhands-sdk/openhands/sdk/extensions/sources.py
```

### Source functions

| Function | Inputs | Produces |
|---|---|---|
| `from_plugin(plugin: Plugin)` | A loaded `Plugin` | Skills (incl. command-derived), hooks, MCP config, agents |
| `from_project(work_dir: Path)` | Project/workspace directory | Project skills + project hooks.json |
| `from_user()` | (none — reads `~/.openhands/` etc.) | User-level skills |
| `from_public(marketplace_path)` | Marketplace filter path | Public skills from OpenHands/extensions repo |
| `from_inline(skills, hooks, mcp, agents)` | Explicit values | Wraps explicit config into a bundle (backward compat) |

Server-only sources (org, sandbox) stay in the agent-server's
`skills_service.py` for now — they can produce `Extensions` too but
aren't in the SDK package.

Each function is self-contained, does its own IO, and returns
`Extensions`. No shared base class needed.

### Tests

`tests/sdk/extensions/test_sources.py` — test each source function in
isolation. Mock filesystem/git where needed.

---

## Phase 3 — Merge Integration

**What**: Verify the merge operation works correctly for the real
precedence order used in production.

### Precedence order (highest → lowest)

```
inline → plugins → project → org → user → public → sandbox
```

`Extensions.collapse()` takes a list in this order; the first entry wins
for skills/MCP/agents (first-wins), hooks concatenate with highest
precedence hooks running first.

### Tests

`tests/sdk/extensions/test_merge_order.py` — integration-style tests that
build realistic bundles from multiple sources and verify the final merged
result matches expected precedence.

---

## Phase 4 — Internal Adoption (No API Change) ✅

**What**: Refactor `LocalConversation._ensure_plugins_loaded()` and
`load_plugins()` to build and merge `Extensions` bundles internally.
Delete `Plugin.add_skills_to()` and `Plugin.add_mcp_config_to()` (the
old merge code paths).  Constructor signatures and public API remain
unchanged.

### Deleted from `Plugin`

`add_skills_to()` and `add_mcp_config_to()` — these implemented the
same merge logic that now lives in `Extensions._merge_skills` /
`_merge_mcp_config`.  `test_plugin_merging.py` deleted with them.

### Changes to `loader.py` (`load_plugins()`)

`load_plugins()` is the public convenience function exported from
`openhands.sdk.plugin`.  Refactored internals:

1. Build `Extensions` per plugin via `from_plugin(plugin)`.
2. Build `agent_bundle` via `from_inline(skills=..., mcp_config=...)`.
3. `Extensions.collapse([*reversed(plugin_bundles), agent_bundle])`
   — reversed so last plugin spec still wins (preserves external
   behavior).
4. Defense-in-depth `max_skills` check on the collapsed result.
5. Apply merged skills + MCP to the agent via `model_copy()`.
6. Return `(updated_agent, merged.hooks)`.

### Changes to `LocalConversation._ensure_plugins_loaded()`

The method has two responsibilities: **merging** (now delegated to
Extensions) and **lifecycle** (fetch, resolve, register, hook processor).
The refactored structure keeps them cleanly separated:

| Step | Concern | What happens |
|------|---------|-------------|
| 1 | Lifecycle | Fetch each plugin + resolve commit SHA → `ResolvedPluginSource` |
| 2 | Lifecycle | `Plugin.load(path)` for each fetched plugin |
| 3 | Merging  | `from_plugin(plugin)` → one `Extensions` bundle per plugin |
| 4 | Merging  | `from_inline(hooks=_pending_hook_config)` → explicit hooks bundle |
| 5 | Merging  | `from_inline(skills=..., mcp_config=...)` → agent base bundle |
| 6 | Merging  | `Extensions.collapse([hooks, *reversed(plugins), agent_base])` |
| 7 | Lifecycle | Apply `merged.skills` + `merged.mcp_config` to agent via `model_copy()` |
| 8 | Lifecycle | `register_plugin_agents(merged.agents, ...)` — agents come from the bundle |
| 9 | Lifecycle | `create_hook_callback(merged.hooks, ...)` → hook processor |
| 10 | Lifecycle | `run_session_start()` |

### Changes to agent-server `ConversationService` / `skills_service`

The server's `load_all_skills()` currently builds separate skill lists and
merges them. Refactor to build `Extensions` bundles and collapse. Hook and
MCP config from server-side sources get included naturally. (Future work —
server code is outside the SDK package.)

### Verification

All existing tests pass (718 SDK tests).  `test_plugin_loader.py`
(25 tests) exercises `load_plugins()` end-to-end with real plugin
directories and confirms last-plugin-wins, MCP merge, hooks concatenation,
and `max_skills` enforcement all behave identically.

---

## Phase 5 — API Migration (Future)

**What**: Surface `Extensions` in the public API, replacing scattered
fields. This is a breaking change and needs deprecation runway per SDK
policy (5 minor releases).

### Candidates for migration

- `StartConversationRequest.plugins` + `.hook_config` → part of an
  extensions spec
- `Agent.mcp_config` → could move to extensions
- `AgentContext.skills` / `.load_public_skills` / `.load_user_skills` →
  skill source config in extensions spec
- `LocalConversation.__init__` `plugins=` / `hook_config=` → extensions

### Unresolved spec (if needed)

If the resolved bundle is too "fat" for wire format, introduce a
lightweight `ExtensionSpec` that describes *what to load* rather than
carrying the loaded content. The resolve step turns spec → bundle.

This phase is speculative — the need for it will become clear after Phase 4
is in production.

---

## File Layout (after Phase 2)

```
openhands-sdk/openhands/sdk/extensions/
├── __init__.py          # (empty until Phase 4 exports are ready)
├── extensions.py        # Extensions model + merge
├── fetch.py             # (existing) git fetch utilities
├── installation/        # (existing) install/uninstall manager
│   ├── __init__.py
│   ├── info.py
│   ├── interface.py
│   ├── manager.py
│   ├── metadata.py
│   └── utils.py
└── sources.py           # from_plugin, from_project, from_user, etc.
```

```
tests/sdk/extensions/
├── test_bundle.py
├── test_sources.py
└── test_merge_order.py
```

---

## Decisions

1. **`Extensions.merge()` logs debug messages on collisions.** Consistent
   with existing `Plugin.add_skills_to()` and `add_mcp_config_to()`
   behavior.

2. **All keyed fields use first-wins.** Skills, MCP config, and agents
   all keep `self`'s value on collision.  `collapse()` takes a list
   ordered highest → lowest precedence.  One precedence direction for
   the entire merge — no per-field exceptions.

3. **Skill auto-loading flags (`load_public_skills`, `load_user_skills`)
   stay on `AgentContext` for now.** They'll move to an extension spec in
   Phase 5. Source functions call the existing loaders in the meantime.
