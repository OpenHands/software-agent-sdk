# Plugin Loading via AgentContext

## 1. Introduction

### 1.1 Problem Statement

PR #1651 adds plugin loading support to the agent-server by introducing `plugin_source`, `plugin_ref`, and `plugin_path` 
fields directly on `StartConversationRequest`. This approach creates an API inconsistency: skills are passed via 
`AgentContext` (inside `Agent`), but plugins are passed as standalone request parameters.

This inconsistency makes the API harder to understand and use. Users must learn two different patterns for configuring 
agent capabilities—one for skills and one for plugins—when conceptually they serve similar purposes.

### 1.2 Proposed Solution

Move plugin configuration into `AgentContext`, consistent with how `load_user_skills` and `load_public_skills` already 
work. This aligns plugin loading with existing patterns:

```python
# Current (inconsistent):
POST /start {"agent": {...}, "plugin_source": "github:owner/repo"}

# Proposed (consistent):
POST /start {"agent": {"agent_context": {"plugin_source": "github:owner/repo"}}}
```

The plugin loading logic moves to a model validator in `AgentContext`, similar to `_load_public_skills`. Plugin outputs 
(skills, MCP config, hooks) are either merged directly or exposed via properties for downstream consumers.

**Trade-off**: Hooks are conceptually conversation-level behavior, not agent context. However, we store them in 
`AgentContext` as a transport mechanism since plugin loading occurs there. Hooks are extracted and passed to 
`Conversation`/`StoredConversation` at conversation creation time. This is pragmatic: it keeps all plugin outputs 
accessible from one location while maintaining the single-request flow.

## 2. User Interface

### 2.1 SDK Usage

Users create an `AgentContext` with plugin source configuration. The plugin is fetched and loaded automatically during 
initialization:

```python
from openhands.sdk import LLM, Agent, AgentContext, Conversation

# Create AgentContext with plugin source
# Plugin is fetched and loaded automatically during initialization
agent_context = AgentContext(
    plugin_source="github:OpenHands/example-plugin",
    plugin_ref="v1.0.0",  # Optional: specific version/branch/commit
    plugin_path="plugins/my-plugin",  # Optional: subdirectory within repo
)

# Create agent with the context
# MCP config from plugin is automatically merged
agent = Agent(
    llm=llm,
    agent_context=agent_context,
    tools=[...],
)

# Create conversation
# Hooks from plugin can be accessed via agent_context.plugin_hooks
conversation = Conversation(
    agent=agent,
    workspace="./workspace",
    hook_config=agent_context.plugin_hooks,  # Use hooks from plugin
)

conversation.send_message("Hello!")
conversation.run()
```

### 2.2 Agent Server API

The agent-server API accepts plugin configuration within the agent's context:

```json
POST /api/conversations/start
{
  "agent": {
    "llm": {"model": "anthropic/claude-sonnet-4-20250514", "api_key": "..."},
    "agent_context": {
      "plugin_source": "github:owner/repo",
      "plugin_ref": "main",
      "plugin_path": "plugins/my-plugin"
    },
    "tools": [...]
  },
  "workspace": {"working_dir": "/workspace"},
  "initial_message": "Hello!"
}
```

## 3. Background Context

### 3.1 Plugin Structure

A plugin is a directory containing:
- `manifest.yaml` or `manifest.json` - Plugin metadata
- `skills/` - Directory of skill definitions (markdown files)
- `hooks.yaml` or `hooks.json` - Hook configuration
- `mcp.yaml` or `mcp.json` - MCP server configuration

### 3.2 Plugin Outputs

When a plugin is loaded, it produces three types of outputs:

| Output | Nature | Consumer |
|--------|--------|----------|
| Skills | Knowledge/prompts for the LLM | Merged into `AgentContext.skills` |
| MCP Config | Tool/server definitions | Merged into `Agent.mcp_config` |
| Hooks | Runtime event handlers | Passed to `Conversation` |

### 3.3 Why Hooks Are Different

Hooks are event handlers that intercept actions during conversation execution. They can:
- Block tool execution (`PreToolUse`)
- Modify observations (`PostToolUse`)
- Transform user input (`UserPromptSubmit`)

Unlike skills (which are prompt content) and MCP config (which defines tools), hooks are **runtime behavior** attached 
to the conversation engine, not the agent's knowledge or capabilities.

### 3.4 Existing Patterns in AgentContext

`AgentContext` already supports automatic skill loading via model validators:

```python
class AgentContext(BaseModel):
    load_user_skills: bool = False   # Loads from ~/.openhands/skills/
    load_public_skills: bool = False  # Loads from github.com/OpenHands/skills
    
    @model_validator(mode="after")
    def _load_user_skills(self):
        if self.load_user_skills:
            user_skills = load_user_skills()
            # Merge into self.skills avoiding duplicates
        return self
    
    @model_validator(mode="after")
    def _load_public_skills(self):
        if self.load_public_skills:
            public_skills = load_public_skills()
            # Merge into self.skills avoiding duplicates
        return self
```

Our plugin loading follows the same pattern.

## 4. Technical Design

### 4.1 AgentContext Extensions

Add plugin configuration fields and a model validator to `AgentContext`:

```python
class AgentContext(BaseModel):
    # Existing fields...
    skills: list[Skill] = Field(default_factory=list)
    load_user_skills: bool = False
    load_public_skills: bool = False
    
    # New plugin fields
    plugin_source: str | None = Field(
        default=None,
        description="Plugin source: 'github:owner/repo', git URL, or local path"
    )
    plugin_ref: str | None = Field(
        default=None,
        description="Optional branch, tag, or commit for the plugin"
    )
    plugin_path: str | None = Field(
        default=None,
        description="Optional subdirectory path within the plugin repository"
    )
    
    # Private attributes for loaded plugin outputs
    _loaded_plugin_mcp_config: dict[str, Any] | None = PrivateAttr(default=None)
    _loaded_plugin_hooks: HookConfig | None = PrivateAttr(default=None)
    
    @model_validator(mode="after")
    def _load_plugin(self):
        """Load plugin from source if specified."""
        if not self.plugin_source:
            return self
        
        # Validate plugin_path for security
        if self.plugin_path:
            safe_path = Path(self.plugin_path)
            if safe_path.is_absolute() or ".." in safe_path.parts:
                raise ValueError("plugin_path must be a relative path without ..")
        
        try:
            # Fetch and load the plugin
            plugin_dir = Plugin.fetch(
                source=self.plugin_source,
                ref=self.plugin_ref,
                subpath=self.plugin_path,
            )
            plugin = Plugin.load(plugin_dir)
            
            # Merge plugin skills into self.skills
            existing_names = {skill.name for skill in self.skills}
            for skill in plugin.skills:
                if skill.name not in existing_names:
                    self.skills.append(skill)
            
            # Store MCP config and hooks for later extraction
            self._loaded_plugin_mcp_config = plugin.mcp_config
            self._loaded_plugin_hooks = plugin.hooks
            
        except Exception as e:
            logger.warning(f"Failed to load plugin from {self.plugin_source}: {e}")
        
        return self
    
    @property
    def plugin_mcp_config(self) -> dict[str, Any] | None:
        """Get MCP config from loaded plugin."""
        return self._loaded_plugin_mcp_config
    
    @property
    def plugin_hooks(self) -> HookConfig | None:
        """Get hooks from loaded plugin."""
        return self._loaded_plugin_hooks
```

### 4.2 Agent MCP Config Merging

Update `Agent` to merge plugin MCP config with its own `mcp_config` during initialization:

```python
class Agent(BaseModel):
    mcp_config: dict[str, Any] = Field(default_factory=dict)
    agent_context: AgentContext | None = None
    
    def initialize(self, ...):
        # Existing initialization...
        
        # Merge plugin MCP config if present
        if self.agent_context and self.agent_context.plugin_mcp_config:
            # Plugin config takes precedence (overwrites existing keys)
            merged = {**self.mcp_config, **self.agent_context.plugin_mcp_config}
            object.__setattr__(self, 'mcp_config', merged)
```

### 4.3 Agent Server Changes

#### 4.3.1 Remove Plugin Fields from StartConversationRequest

```python
class StartConversationRequest(BaseModel):
    agent: AgentBase
    workspace: LocalWorkspace
    initial_message: str | None = None
    # REMOVED: plugin_source, plugin_ref, plugin_path
```

#### 4.3.2 Update ConversationService

Remove `_load_and_merge_plugin()` method. Update `start_conversation()`:

```python
async def start_conversation(self, request: StartConversationRequest):
    # Plugin loading now happens automatically when AgentContext is deserialized
    # (during Pydantic model validation of the request)
    
    # Extract hooks from agent context for StoredConversation
    hook_config = None
    if request.agent.agent_context and request.agent.agent_context.plugin_hooks:
        hook_config = request.agent.agent_context.plugin_hooks
    
    stored = StoredConversation(
        id=conversation_id,
        hook_config=hook_config,
        **request.model_dump()
    )
    # ... rest of method
```

### 4.4 LocalConversation Changes

Remove `plugin_source`, `plugin_ref`, `plugin_path` parameters from `LocalConversation.__init__()` and the 
`Conversation` factory function. No backward compatibility is needed since PR #1651 hasn't merged.

### 4.5 Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Request Processing                              │
└─────────────────────────────────────────────────────────────────────────────┘

POST /start {"agent": {"agent_context": {"plugin_source": "..."}}}
                                │
                                ▼
                    ┌───────────────────────┐
                    │  Pydantic Validation  │
                    │  (AgentContext init)  │
                    └───────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    │  _load_plugin()       │
                    │  model validator      │
                    └───────────────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            ▼                   ▼                   ▼
    ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
    │    Skills     │   │  MCP Config   │   │    Hooks      │
    │ (merged into  │   │ (stored as    │   │ (stored as    │
    │  self.skills) │   │  private attr)│   │  private attr)│
    └───────────────┘   └───────────────┘   └───────────────┘
            │                   │                   │
            ▼                   ▼                   ▼
    ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
    │ AgentContext  │   │    Agent      │   │ Conversation  │
    │   .skills     │   │  .mcp_config  │   │ .hook_config  │
    │               │   │  (merged)     │   │               │
    └───────────────┘   └───────────────┘   └───────────────┘
```

## 5. Implementation Plan

All changes must pass existing lints and tests. New functionality requires corresponding test coverage.

### 5.1 Extend AgentContext with Plugin Loading (M1) ✅

Foundation: Add plugin fields and model validator to AgentContext.

**Goal**: `AgentContext` can load a plugin and expose skills, MCP config, and hooks.

#### 5.1.1 AgentContext Plugin Fields and Validator

- [x] `openhands-sdk/openhands/sdk/context/agent_context.py`
  - Add `plugin_source`, `plugin_ref`, `plugin_path` fields
  - Add `_loaded_plugin_mcp_config`, `_loaded_plugin_hooks` private attributes
  - Add `_load_plugin()` model validator
  - Add `plugin_mcp_config` and `plugin_hooks` properties
  - Add path validation for security (reject `..` and absolute paths)

- [x] `openhands-sdk/tests/sdk/context/test_agent_context_plugin.py`
  - Test plugin loading via AgentContext
  - Test skills merging (plugin skills added, duplicates handled)
  - Test MCP config exposed via property
  - Test hooks exposed via property
  - Test path validation rejects unsafe paths
  - Test graceful handling of plugin fetch failures

### 5.2 Update Agent to Merge Plugin MCP Config (M2) ✅

**Goal**: `Agent` automatically merges plugin MCP config with its own config.

#### 5.2.1 Agent MCP Config Merging

- [x] `openhands-sdk/openhands/sdk/agent/base.py`
  - Update `initialize()` to merge `agent_context.plugin_mcp_config`
  - Ensure plugin config takes precedence over existing keys

- [x] `openhands-sdk/tests/sdk/agent/test_agent_plugin_mcp.py`
  - Test MCP config merging during initialization
  - Test plugin config overwrites existing keys
  - Test no-op when no plugin MCP config present

### 5.3 Update Agent Server (M3) ✅

**Goal**: Agent server uses the new pattern (plugin config in AgentContext).

#### 5.3.1 Remove Plugin Fields from Request Model

- [x] `openhands-agent-server/openhands/agent_server/models.py`
  - Remove `plugin_source`, `plugin_ref`, `plugin_path` from `StartConversationRequest`

#### 5.3.2 Update ConversationService

- [x] `openhands-agent-server/openhands/agent_server/conversation_service.py`
  - Remove `_load_and_merge_plugin()` method
  - Remove `_merge_skills()` method (now in SDK)
  - Update `start_conversation()` to extract hooks from `agent.agent_context.plugin_hooks`

- [x] `openhands-agent-server/tests/agent_server/test_conversation_service_plugin.py`
  - Test plugin loading via AgentContext in request
  - Test hooks extracted to StoredConversation
  - Test backward compatibility (requests without plugin_source work)

### 5.4 Update LocalConversation (M4) ✅

**Goal**: Remove plugin params from LocalConversation (no backward compat needed).

#### 5.4.1 Remove Plugin Parameters

- [x] `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`
  - Remove `plugin_source`, `plugin_ref`, `plugin_path` from `__init__()`
  - Remove plugin loading logic from constructor

- [x] `openhands-sdk/openhands/sdk/conversation/conversation.py`
  - Remove `plugin_source`, `plugin_ref`, `plugin_path` from `Conversation()` factory

- [x] Update any tests that used the old plugin params pattern

### 5.5 Add SDK Example (M5) ✅

**Goal**: Document the recommended pattern with a working example.

#### 5.5.1 Plugin via AgentContext Example

- [x] `openhands-sdk/examples/05_skills_and_plugins/03_plugin_via_agent_context/main.py`
  - Demonstrate loading plugin via AgentContext
  - Show MCP config merging
  - Show hook extraction and usage

- [x] `openhands-sdk/examples/05_skills_and_plugins/03_plugin_via_agent_context/README.md`
  - Explain the pattern
  - Document when to use plugin_source vs load_public_skills

### 5.6 Cleanup and Final Testing (M6) ✅

**Goal**: Review obsolete code, ensure all tests pass.

#### 5.6.1 Review SDK Plugin Utilities

- [x] Review `openhands-sdk/openhands/sdk/plugin/utils.py`
  - Determine if `merge_skills()`, `merge_mcp_configs()` are still needed
  - **Decision**: KEEP - used by `Plugin.merge_into()` for backward compatibility

- [x] Review `openhands-sdk/openhands/sdk/plugin/plugin.py`
  - Determine if `Plugin.merge_into()` is still needed
  - **Decision**: KEEP - provides manual plugin loading pattern for advanced use cases
    (see `examples/05_skills_and_plugins/02_loading_plugins/`)

#### 5.6.2 Integration Tests

- [x] End-to-end test: SDK with plugin via AgentContext
  - Covered by `tests/sdk/context/test_agent_context_plugin.py`
  - Covered by `tests/sdk/agent/test_agent_plugin_mcp.py`
- [x] End-to-end test: Agent server with plugin via request
  - Covered by `tests/agent_server/test_conversation_service_plugin.py`

## References

- PR #1651: https://github.com/OpenHands/software-agent-sdk/pull/1651
- Docs PR: https://github.com/OpenHands/docs/pull/265
- Confirmation comment: https://github.com/OpenHands/software-agent-sdk/pull/1651#issuecomment-3764103147
- Original feedback: https://github.com/OpenHands/software-agent-sdk/pull/1651#issuecomment-3761625576
