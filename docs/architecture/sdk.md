# Core SDK architecture constraints

This document captures the structural contracts for the core package
`openhands-sdk/openhands/sdk`.

It is intentionally deeper than [`openhands-sdk/openhands/sdk/AGENTS.md`](../../openhands-sdk/openhands/sdk/AGENTS.md):
that AGENTS file tells contributors **how to change** the SDK safely, while this
page captures **what the SDK is trying to preserve**.

## Package-level invariants

### 1. Configuration is declarative; `ConversationState` is the mutable runtime hub

The SDK is designed so that agents, tools specs, LLM configs, settings, plugin
sources, and workspace configs are declarative Pydantic models.

Durable constraint:

- mutable execution status belongs in `ConversationState`; other objects may keep
  private caches, but replay/persistence should not depend on those caches.

OCL-like:

- `context AgentBase inv Frozen: self.model_config.frozen = true`
- `context Event inv Frozen: self.model_config.frozen = true`

### 2. The event log is the execution trace

Anything that must be auditable, replayable, or reconstructible for the LLM
must appear in the event stream.

Durable constraints:

- events are immutable once emitted
- mutations are expressed as new events, not edits in place
- `LLMConvertibleEvent.events_to_messages(...)` is the canonical reconstruction
  path from event history to LLM-visible message history

### 3. Workspace choice controls local-vs-remote execution

`Conversation(...)` and `Workspace(...)` are factories rather than separate user
entrypoints.

Durable constraints:

- `Conversation(...)` returns `LocalConversation` unless the workspace is a
  `RemoteWorkspace`
- a remote conversation must not accept `persistence_dir`
- `Workspace(host=...)` returns a remote workspace; otherwise it returns a local
  workspace

OCL-like:

- `context Conversation::__new__ pre RemoteNoPersistence: workspace.oclIsKindOf(RemoteWorkspace) implies persistence_dir = null`
- `context Workspace::__new__ post RemoteIffHost: (host <> null) implies result.oclIsKindOf(RemoteWorkspace)`

### 4. Public Python API changes are governed from `openhands.sdk.__all__`

The curated import surface in `openhands.sdk.__all__` is treated as public API.

Durable constraints:

- removing a public symbol requires deprecation first
- breaking public SDK API changes require at least a MINOR version bump
- persisted event schema changes must keep old conversations loadable

### 5. Composition is additive, not ad hoc

Plugins, MCP servers, skills, hooks, and subagents extend the SDK by composing
well-defined seams instead of introducing package-specific side channels.

Durable constraints:

- plugins merge skills, MCP config, and hooks through the canonical loader
- subagents register through the shared registry with explicit precedence rules
- tools resolve from declarative `Tool(name=..., params=...)` specs through the
  tool registry

## Representative low-level invariants

These are small enough to state directly.

- `context ToolRegistry inv RegisteredNamesAreNonEmpty: name.trim().size() > 0`
- `context resolve_tool pre Registered: tool_spec.name in registry`
- `context ActionEvent inv BatchedThoughtOnlyFirst: shared_llm_response_id implies only_first_action_has_thought`
- `context BaseConversation inv ConfirmationModeIff: is_confirmation_mode_active = (state.security_analyzer <> null and not state.confirmation_policy.oclIsKindOf(NeverConfirm))`

## First-level component map

| Component | Responsibility | Durable constraints / invariants |
| --- | --- | --- |
| `agent/` | Agent configuration, prompt assembly, action generation, and tool materialization | `AgentBase` is frozen and stateless by design; runtime tool instances live in private attrs; tool specs resolve through the registry; prompt construction separates mostly-static prompt material from per-conversation dynamic context. |
| `context/` | Agent context, skills, and condensation helpers | Context enriches the agent without becoming a second state store; progressive disclosure flows through `AgentContext.get_system_message_suffix()`; skill prompt descriptions are truncated to the AgentSkills limit (1024 chars). |
| `conversation/` | Conversation factories, orchestration, persistence, locking, and visualization | `ConversationState` is the mutable runtime hub; `Conversation(...)` chooses local vs remote from the workspace type; `ask_agent()` is contractually read-only; async code must not block the event loop on the synchronous state lock. |
| `critic/` | Optional evaluation of actions/messages | Critic behavior is advisory to the agent loop, not a replacement for the event log or execution status; critic-related settings stay explicit in agent/settings models. |
| `event/` | Event model hierarchy and event-to-LLM conversion | Events are frozen discriminated unions; parallel tool calls are represented as multiple action events that share one `llm_response_id`; only the first action in a batch may carry shared thought/reasoning payloads. |
| `git/` | Typed git helpers used by higher-level features | Git interactions are isolated behind helper/models code instead of being spread through agent logic; callers should receive explicit errors rather than silent success on invalid repositories. |
| `hooks/` | Conversation hook configuration and execution plumbing | Hook configuration is declarative; blocked actions/messages are surfaced through conversation state; plugin hook configs concatenate instead of overwriting each other. |
| `io/` | Persistence/storage abstraction (`FileStore` and implementations) | Persistence is hidden behind an interface so conversations can use local or in-memory backends without changing orchestration semantics. |
| `llm/` | LLM config, registry, message types, streaming, and provider-specific adaptation | All model/provider details are normalized into typed models here; message/content objects are the stable bridge between conversation history and provider APIs. |
| `logger/` | Shared logging facade | Logging should be centralized here rather than configured ad hoc throughout the codebase. |
| `mcp/` | Model Context Protocol integration | MCP servers are translated into ordinary tool definitions instead of special-cased agent logic; runtime config now uses typed `MCPConfig`, while serialized settings stay compact and dict-shaped. |
| `observability/` | Optional telemetry/trace integration | Observability augments execution but must not become a behavioral dependency for core agent logic. |
| `plugin/` | Plugin fetch/load/merge lifecycle | Plugin loading is canonicalized in `load_plugins(...)`; skills merge by name with later plugins winning, MCP config merges by key with later plugins winning, and hooks concatenate. |
| `secret/` | Secret value handling | Secret values are modeled explicitly so persistence can either encrypt them with a cipher or redact them when no cipher is available. |
| `security/` | Risk analysis and confirmation policies | Confirmation mode is active iff a security analyzer exists and the policy is not `NeverConfirm`; policy and analyzer remain explicit dependencies instead of hidden global settings. |
| `settings/` | Structured settings models and schema export | `AgentSettings` plus `export_settings_schema()` are the canonical structured settings surface; `SettingsFieldSchema` intentionally omits a `required` flag; `AgentSettings.tools` and `mcp_config` stay aligned with the agent creation payload. |
| `skills/` | Skill fetching/installation helpers | Skill fetching is a source-resolution/cache concern; prompt rendering and activation happen elsewhere, keeping skill acquisition separate from runtime context assembly. |
| `subagent/` | Discovery and registration of Markdown/plugin/programmatic subagents | Registration is precedence-driven and registry-backed; programmatic registration wins absolutely, plugins and file-based agents are first-wins within the registry, and built-ins act as fallback. See the dedicated [`subagent` AGENTS guide](../../openhands-sdk/openhands/sdk/subagent/AGENTS.md). |
| `testing/` | SDK test utilities | Test helpers support deterministic unit/integration tests but are not part of production execution paths. |
| `tool/` | Tool specs, registry, base classes, and schema contracts | Tool resolution is name-based and registry-backed; unknown tools raise `KeyError`; factories must resolve to `Sequence[ToolDefinition]`; tool inputs/outputs are typed schemas rather than unstructured dictionaries. |
| `utils/` | Shared primitives (cipher, deprecation, async helpers, JSON helpers, etc.) | Cross-cutting mechanics live here so core subsystems can reuse them without inventing parallel implementations; deprecation helpers are part of the SDK compatibility contract. |
| `workspace/` | SDK workspace abstraction and factory | `working_dir` is normalized as workspace state rather than ambient process cwd; local pause/resume is a no-op while remote/container backends may implement it as an optional capability. |

## Relationships worth preserving

- `agent/` depends on `tool/`, `llm/`, `context/`, `mcp/`, and `critic/`, but
  does not own persistence.
- `conversation/` owns runtime coordination and persistence, but delegates agent
  decisions to `agent/` and environment I/O to `workspace/`.
- `event/` is the shared audit language between agent, conversation, hooks,
  critic, and visualizers.
- `settings/` is the canonical serialized configuration surface; it should not
  diverge from the models required by `create_agent()`.
- `plugin/`, `skills/`, and `subagent/` all extend the system through explicit
  registries/loaders rather than bespoke imports.

## Depth check

If you are about to change one of these invariants, update both:

1. the relevant AGENTS file, and
2. this document (or a more specific architecture doc if one is added later).
