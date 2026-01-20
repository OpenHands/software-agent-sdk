# Conversation-Based Plugin Loading with Multi-Plugin Support

## 1. Introduction

### 1.1 Problem Statement

PR #1651 currently implements plugin loading via `AgentContext` using a pydantic model validator. This approach has several issues:

1. **Conceptual mismatch**: Plugins contain hooks and MCP config which are conversation runtime concerns, not LLM context. AgentContext should be immutable and focused on what gets sent to the LLM.

2. **I/O in validators**: The `_load_plugin()` model validator performs network I/O (git clone, file reads), which can cause unintended re-downloads on conversation resume or model re-serialization.

3. **Single plugin only**: The current implementation only supports one plugin per conversation (`plugin_source`), but extensible software should support multiple plugins.

4. **API inconsistency**: Plugin params on `AgentContext` but not on `LocalConversation` or `RemoteConversation`, creating different patterns for SDK users vs. server API users.

5. **Parameter naming**: `Plugin.fetch()` uses `subpath` for the repository subdirectory, but this name doesn't clearly indicate it's only relevant for git sources, not local paths.

### 1.2 Proposed Solution

Move plugin configuration to the Conversation level with support for multiple plugins:

1. Add `plugins: list[PluginSource]` parameter to `LocalConversation`, `RemoteConversation`, and the `Conversation` factory
2. Create an SDK utility `load_plugins()` that both `LocalConversation` and `ConversationService` use
3. Remove plugin loading from `AgentContext`
4. Rename `subpath` to `repo_path` in `Plugin.fetch()` for clarity

This approach ensures:
- Conceptually correct placement (plugins are conversation-level)
- Single source of truth for orchestration logic (SDK utility)
- No I/O side effects in model validators
- Support for multiple plugins per conversation
- API parity across SDK and server interfaces

**Trade-off**: Hooks are extracted from plugins during conversation creation and stored separately for persistence. This is necessary because hooks are runtime behavior attached to the conversation engine, not serializable agent context.

## 2. User Interface

### 2.1 SDK Usage

```python
from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.plugin import PluginSource

# Create agent (no plugin config here)
llm = LLM(model="anthropic/claude-sonnet-4-20250514", api_key=SecretStr("..."))
agent = Agent(llm=llm, tools=[...])

# Load multiple plugins at conversation creation
conversation = Conversation(
    agent=agent,
    workspace="./workspace",
    plugins=[
        # Simple plugin from GitHub
        PluginSource(source="github:org/security-plugin", ref="v2.0.0"),
        # Plugin from monorepo (repo_path specifies subdirectory)
        PluginSource(source="github:org/plugins-monorepo", repo_path="plugins/logging"),
        # Local plugin (no repo_path needed)
        PluginSource(source="/path/to/custom-plugin"),
    ],
)

conversation.send_message("Hello!")
conversation.run()
```

### 2.2 Remote Conversation (via Agent Server)

```python
from openhands.sdk import Agent, Conversation
from openhands.sdk.plugin import PluginSource
from openhands.sdk.workspace import RemoteWorkspace

# Plugins are sent to server, loaded there (inside the sandbox)
conversation = Conversation(
    agent=agent,
    workspace=RemoteWorkspace(host="http://agent-server:8000"),
    plugins=[
        PluginSource(source="github:org/security-plugin", ref="v2.0.0"),
    ],
)
```

### 2.3 Agent Server API

```json
POST /api/conversations/start
{
  "agent": {...},
  "plugins": [
    {"source": "github:org/security-plugin", "ref": "v2.0.0"},
    {"source": "github:org/plugins-monorepo", "repo_path": "plugins/logging"},
    {"source": "/local/path/to/plugin"}
  ],
  "workspace": {"working_dir": "/workspace"},
  "initial_message": "Hello!"
}
```

## 3. Other Context

### 3.1 Plugin Structure

A plugin is a directory containing:
```
my-plugin/
├── .claude-plugin/           # or .plugin/
│   └── plugin.json          # Plugin metadata (name, version, description)
├── skills/                  # Agent skills (markdown files)
├── hooks/                   # Event handlers
│   └── hooks.json
├── .mcp.json                # MCP server configuration
└── README.md
```

### 3.2 Plugin Outputs and Their Consumers

| Output | Nature | Consumer | Merge Strategy |
|--------|--------|----------|----------------|
| Skills | Knowledge/prompts for LLM | `AgentContext.skills` | Override by name (last wins) |
| MCP Config | Tool/server definitions | `Agent.mcp_config` | Override by key (last wins) |
| Hooks | Runtime event handlers | `HookProcessor` | Concatenate (all run) |

### 3.3 Why Plugins Load Where the Agent Runs

From the original PR:
> "Plugin fetching must happen **inside the sandbox/runtime** (where the agent runs), not on the app server."

This is because:
- Plugin scripts need to execute in the sandbox
- MCP servers run inside the sandbox
- Skills may reference files in the sandbox filesystem

Therefore:
- `LocalConversation`: Loads plugins locally
- `RemoteConversation`: Sends plugin specs to server; `ConversationService` loads them there

## 4. Technical Design

### 4.1 PluginSource Model

Define in `openhands/sdk/plugin/types.py`:

```python
class PluginSource(BaseModel):
    """Specification for a plugin to load."""
    
    source: str = Field(
        description="Plugin source: 'github:owner/repo', any git URL, or local path"
    )
    ref: str | None = Field(
        default=None,
        description="Optional branch, tag, or commit (only for git sources)"
    )
    repo_path: str | None = Field(
        default=None,
        description="Subdirectory path within the git repository "
                    "(e.g., 'plugins/my-plugin' for monorepos). "
                    "Only relevant for git sources, not local paths."
    )
```

### 4.2 Rename `subpath` to `repo_path` in Plugin.fetch()

Update `openhands/sdk/plugin/plugin.py`:

```python
@classmethod
def fetch(
    cls,
    source: str,
    cache_dir: Path | None = None,
    ref: str | None = None,
    update: bool = True,
    repo_path: str | None = None,  # Renamed from subpath
) -> Path:
    """Fetch a plugin from a remote source.
    
    Args:
        source: Plugin source (github:owner/repo, git URL, or local path)
        cache_dir: Directory for caching (default: ~/.openhands/cache/plugins/)
        ref: Branch, tag, or commit (only for git sources)
        update: Whether to update if already cached
        repo_path: Subdirectory within the git repository (only for git sources)
    """
```

Also update `openhands/sdk/plugin/fetch.py` to use `repo_path` parameter name.

### 4.3 SDK Utility: load_plugins()

Create `openhands/sdk/plugin/loader.py`:

```python
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.hooks import HookConfig
from openhands.sdk.plugin import Plugin
from openhands.sdk.plugin.types import PluginSource
from openhands.sdk.plugin.utils import merge_skills, merge_mcp_configs


def merge_hook_configs(configs: list[HookConfig]) -> HookConfig | None:
    """Merge multiple hook configs by concatenating handlers per event type."""
    if not configs:
        return None
    
    merged_hooks: dict[str, list] = {}
    for config in configs:
        for event_type, matchers in config.hooks.items():
            if event_type not in merged_hooks:
                merged_hooks[event_type] = []
            merged_hooks[event_type].extend(matchers)
    
    return HookConfig(hooks=merged_hooks)


def load_plugins(
    plugin_specs: list[PluginSource],
    agent: AgentBase,
    max_skills: int = 100,
) -> tuple[AgentBase, HookConfig | None]:
    """Load multiple plugins and merge them into the agent.
    
    This is the canonical function for plugin loading, used by:
    - LocalConversation (for SDK-direct users)
    - ConversationService (for agent-server users)
    
    Args:
        plugin_specs: List of plugin sources to load
        agent: Agent to merge plugins into
        max_skills: Maximum total skills allowed (defense-in-depth limit)
        
    Returns:
        Tuple of (updated_agent, merged_hook_config)
        
    Raises:
        PluginFetchError: If any plugin fails to fetch or load
        ValueError: If max_skills limit exceeded
    """
    if not plugin_specs:
        return agent, None
    
    merged_context = agent.agent_context
    merged_mcp = agent.mcp_config or {}
    all_hooks: list[HookConfig] = []
    
    for spec in plugin_specs:
        # Fetch (downloads if needed, returns cached path)
        path = Plugin.fetch(
            source=spec.source,
            ref=spec.ref,
            repo_path=spec.repo_path,
        )
        plugin = Plugin.load(path)
        
        # Merge skills and MCP using existing SDK utilities
        merged_context, merged_mcp = plugin.merge_into(
            merged_context,
            merged_mcp,
            max_skills=max_skills,
        )
        
        # Collect hooks for later combination
        if plugin.hooks:
            all_hooks.append(plugin.hooks)
    
    # Combine all hook configs (concatenation semantics)
    combined_hooks = merge_hook_configs(all_hooks)
    
    # Create updated agent with merged content
    updated_agent = agent.model_copy(update={
        "agent_context": merged_context,
        "mcp_config": merged_mcp,
    })
    
    return updated_agent, combined_hooks
```

### 4.4 LocalConversation Integration

Update `openhands/sdk/conversation/impl/local_conversation.py`:

```python
from openhands.sdk.plugin.loader import load_plugins
from openhands.sdk.plugin.types import PluginSource


class LocalConversation(BaseConversation):
    def __init__(
        self,
        agent: AgentBase,
        workspace: str | Path | LocalWorkspace,
        plugins: list[PluginSource] | None = None,  # NEW
        hook_config: HookConfig | None = None,
        # ... other existing params
    ):
        # Load plugins if specified
        plugin_hook_config: HookConfig | None = None
        if plugins:
            agent, plugin_hook_config = load_plugins(plugins, agent)
        
        # Combine explicit hook_config with plugin hooks
        effective_hook_config = self._merge_hook_configs(hook_config, plugin_hook_config)
        
        # ... rest of initialization using effective_hook_config
```

### 4.5 RemoteConversation Integration

Update `openhands/sdk/conversation/impl/remote_conversation.py`:

```python
class RemoteConversation(BaseConversation):
    def __init__(
        self,
        agent: AgentBase,
        workspace: RemoteWorkspace,
        plugins: list[PluginSource] | None = None,  # NEW
        # ... other existing params
    ):
        # ...
        if should_create:
            payload = {
                "agent": agent.model_dump(mode="json", context={"expose_secrets": True}),
                "plugins": [p.model_dump() for p in plugins] if plugins else None,  # NEW
                # ... other fields
            }
```

### 4.6 Conversation Factory Integration

Update `openhands/sdk/conversation/conversation.py`:

```python
from openhands.sdk.plugin.types import PluginSource


class Conversation:
    @overload
    def __new__(
        cls,
        agent: AgentBase,
        *,
        workspace: str | Path | LocalWorkspace = "workspace/project",
        plugins: list[PluginSource] | None = None,  # NEW
        # ... other params
    ) -> "LocalConversation": ...

    @overload
    def __new__(
        cls,
        agent: AgentBase,
        *,
        workspace: RemoteWorkspace,
        plugins: list[PluginSource] | None = None,  # NEW
        # ... other params
    ) -> "RemoteConversation": ...

    def __new__(
        cls,
        agent: AgentBase,
        *,
        workspace: str | Path | LocalWorkspace | RemoteWorkspace = "workspace/project",
        plugins: list[PluginSource] | None = None,  # NEW
        # ... other params
    ) -> BaseConversation:
        # Pass plugins to LocalConversation or RemoteConversation
```

### 4.7 Agent Server Integration

#### 4.7.1 Update StartConversationRequest

`openhands-agent-server/openhands/agent_server/models.py`:

```python
from openhands.sdk.plugin.types import PluginSource


class StartConversationRequest(BaseModel):
    agent: AgentBase
    workspace: LocalWorkspace
    plugins: list[PluginSource] | None = Field(
        default=None,
        description="List of plugins to load for this conversation"
    )
    # ... other existing fields
```

#### 4.7.2 Update ConversationService

`openhands-agent-server/openhands/agent_server/conversation_service.py`:

```python
from openhands.sdk.plugin.loader import load_plugins


class ConversationService:
    async def start_conversation(
        self, request: StartConversationRequest
    ) -> tuple[ConversationInfo, bool]:
        # Load plugins using SDK utility (runs in thread pool for async)
        hook_config = None
        if request.plugins:
            updated_agent, hook_config = await asyncio.to_thread(
                load_plugins, request.plugins, request.agent
            )
            request = request.model_copy(update={"agent": updated_agent})
        
        # Store hook_config in StoredConversation for persistence
        stored = StoredConversation(
            id=conversation_id,
            hook_config=hook_config,
            **request.model_dump(exclude={"plugins"}),  # Don't persist raw specs
        )
        # ... rest of method
```

### 4.8 Remove Plugin Loading from AgentContext

Remove from `openhands/sdk/context/agent_context.py`:
- `plugin_source`, `plugin_ref`, `plugin_path` fields
- `_loaded_plugin_mcp_config`, `_loaded_plugin_hooks` private attributes
- `_load_plugin()` model validator
- `plugin_mcp_config` and `plugin_hooks` properties

### 4.9 Architecture Diagram

```plaintext
┌─────────────────────────────────────────────────────────────────────────┐
│                     openhands.sdk.plugin.loader                          │
│                                                                          │
│   load_plugins(specs, agent) -> (updated_agent, hook_config)            │
│     - Plugin.fetch() for each spec                                       │
│     - Plugin.load() for each spec                                        │
│     - plugin.merge_into() for skills + MCP                              │
│     - merge_hook_configs() for hooks                                     │
└─────────────────────────────────────────────────────────────────────────┘
                    ▲                              ▲
                    │                              │
        ┌───────────┴──────────┐     ┌────────────┴─────────────┐
        │  LocalConversation   │     │  ConversationService     │
        │  (runs locally)      │     │  (runs on server)        │
        │                      │     │                          │
        │  - calls load_plugins│     │  - calls load_plugins    │
        │  - uses result       │     │  - stores hook_config    │
        └──────────────────────┘     └──────────────────────────┘
                    ▲                              ▲
                    │                              │
        ┌───────────┴──────────┐     ┌────────────┴─────────────┐
        │  Conversation()      │     │  RemoteConversation      │
        │  with LocalWorkspace │     │  with RemoteWorkspace    │
        └──────────────────────┘     │                          │
                                     │  - sends plugins to      │
                                     │    server in request     │
                                     └──────────────────────────┘
```

## 5. Implementation Plan

All changes must pass existing lints and tests. New functionality requires corresponding test coverage.

### 5.0 Fix CI - Remove Undocumented Examples (M0)

**Goal**: Start with green CI by removing undocumented examples that fail the check-examples job.

- [x] Remove `examples/05_skills_and_plugins/03_plugin_via_agent_context/` - This example was not documented in the docs repo and was causing check-examples CI failure. It will be replaced with the new `03_plugin_via_conversation` example in M6.

### 5.1 Rename subpath to repo_path in Plugin.fetch (M1)

Clarify that the parameter is only relevant for git sources.

#### 5.1.1 Update Plugin.fetch() Signature

- [x] `openhands-sdk/openhands/sdk/plugin/plugin.py` - Rename `subpath` to `repo_path`
- [x] `openhands-sdk/openhands/sdk/plugin/fetch.py` - Update internal usage
- [x] `tests/sdk/plugin/test_plugin_fetch.py` - Update tests to use `repo_path`
- [x] `openhands-sdk/openhands/sdk/context/agent_context.py` - Also renamed `plugin_path` to `plugin_repo_path` for consistency (using `repo_path` alias for backward compatibility)
- [x] `tests/sdk/context/test_agent_context_plugin.py` - Updated tests

### 5.2 PluginSource Model and Loader Utility (M2)

Foundation: Define the plugin specification model and loading utility.

**Goal**: SDK provides `load_plugins()` utility that loads multiple plugins and merges them.

#### 5.2.1 PluginSource Model

- [x] `openhands-sdk/openhands/sdk/plugin/types.py` - Add `PluginSource` model
- [x] `openhands-sdk/openhands/sdk/plugin/__init__.py` - Export `PluginSource`

#### 5.2.2 Loader Utility

- [x] `openhands-sdk/openhands/sdk/plugin/loader.py` - Create with `load_plugins()` and `merge_hook_configs()`
- [x] `openhands-sdk/openhands/sdk/plugin/__init__.py` - Export `load_plugins`, `merge_hook_configs`
- [x] `tests/sdk/plugin/test_plugin_loader.py` - Tests for:
  - Loading single plugin
  - Loading multiple plugins with merge semantics
  - Skills override by name
  - MCP config override by key
  - Hooks concatenation
  - Error handling (fetch failure, load failure)
  - max_skills validation

### 5.3 LocalConversation Plugin Support (M3)

**Goal**: SDK users can load plugins when creating a local conversation.

#### 5.3.1 LocalConversation Changes

- [x] `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`
  - Add `plugins: list[PluginSource] | None = None` parameter
  - Call `load_plugins()` in `__init__()`
  - Merge plugin hooks with explicit `hook_config` using `merge_hook_configs()`

#### 5.3.2 Conversation Factory Changes

- [x] `openhands-sdk/openhands/sdk/conversation/conversation.py`
  - Add `plugins` parameter to overloads and implementation
  - Pass to `LocalConversation`

#### 5.3.3 Tests

- [x] `tests/sdk/conversation/test_local_conversation_plugins.py`
  - Test plugin loading via Conversation factory
  - Test multiple plugins merge correctly
  - Test hook_config + plugin hooks combination

**Demo**: User can create `Conversation(agent, workspace, plugins=[...])` and plugins are loaded.

### 5.4 RemoteConversation and Agent Server Support (M4)

**Goal**: Plugins work with remote agent server.

#### 5.4.1 RemoteConversation Changes

- [ ] `openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py`
  - Add `plugins` parameter
  - Include in POST payload to server

#### 5.4.2 Agent Server Model Changes

- [ ] `openhands-agent-server/openhands/agent_server/models.py`
  - Add `plugins: list[PluginSource] | None` to `StartConversationRequest`
  - Import `PluginSource` from SDK

#### 5.4.3 ConversationService Changes

- [ ] `openhands-agent-server/openhands/agent_server/conversation_service.py`
  - Call `load_plugins()` in `start_conversation()`
  - Store `hook_config` in `StoredConversation`
  - Remove old `_load_and_merge_plugin()` method if still present

#### 5.4.4 Tests

- [ ] `tests/agent_server/test_conversation_service_plugins.py`
  - Test plugin loading via API
  - Test multiple plugins
  - Test hook_config persistence

**Demo**: User can POST to `/api/conversations/start` with `plugins` array and they load on server.

### 5.5 Deprecate Legacy Fields in AgentContext (M5)

**Goal**: Mark the AgentContext plugin fields as deprecated (not remove them yet for backward compatibility).

> **Note**: Changed from "Remove" to "Deprecate" to maintain backward compatibility during migration period.

#### 5.5.1 AgentContext Deprecation

- [ ] `openhands-sdk/openhands/sdk/context/agent_context.py`
  - Mark `plugin_source`, `plugin_ref`, `plugin_repo_path` fields as deprecated
  - Add deprecation warnings when these fields are used
  - Keep `plugin_mcp_config` and `plugin_hooks` properties working but deprecated

#### 5.5.2 Agent Cleanup

- [ ] `openhands-sdk/openhands/sdk/agent/base.py`
  - Add deprecation warning for plugin MCP merging in `initialize()` if present

#### 5.5.3 Update Tests

- [ ] Update tests to use new `plugins` parameter instead of AgentContext plugin fields
- [ ] Keep existing tests but expect deprecation warnings

### 5.6 Documentation and Examples (M6)

**Goal**: Document the new pattern with working examples.

#### 5.6.1 Example Updates

- [ ] `examples/05_skills_and_plugins/03_plugin_via_conversation/main.py` - New example
- [ ] `examples/05_skills_and_plugins/03_plugin_via_conversation/README.md` - Documentation
- [ ] Coordinate with docs repo to document the new example

#### 5.6.2 Integration Tests

- [ ] End-to-end test: SDK with multiple plugins
- [ ] End-to-end test: Agent server with multiple plugins

## References

- PR #1651: https://github.com/OpenHands/software-agent-sdk/pull/1651
- Analysis doc: `docs/design/pr1651-rewrite-3-analysis.md`
- Previous design (AgentContext approach): `docs/design/pr1651-rewrite-2.md`
