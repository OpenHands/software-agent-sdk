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

## Phase 4 — Internal Adoption (No API Change)

**What**: Refactor `LocalConversation._ensure_plugins_loaded()` to build
and merge `Extensions` bundles internally. The constructor signature and
public API remain unchanged.

### Changes to `LocalConversation`

`_ensure_plugins_loaded()` currently:
1. Loops over `_plugin_specs`, fetches/loads each plugin
2. Manually merges skills via `plugin.add_skills_to()`
3. Manually merges MCP via `plugin.add_mcp_config_to()`
4. Collects hook configs into a list, merges at the end
5. Updates agent with `model_copy()`

Refactored to:
1. Build `Extensions` from agent's existing `agent_context` + `mcp_config` (inline source)
2. Build `Extensions` from each loaded plugin (plugin source)
3. Build `Extensions` from `_pending_hook_config` (inline source)
4. `Extensions.collapse([inline_agent, *plugin_bundles, inline_hooks])`
5. Apply the final bundle to the agent

### Changes to agent-server `ConversationService` / `skills_service`

The server's `load_all_skills()` currently builds separate skill lists and
merges them. Refactor to build `Extensions` bundles and collapse. Hook and
MCP config from server-side sources get included naturally.

### Verification

All existing tests pass. The behavior is identical — just cleaner
implementation.

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
