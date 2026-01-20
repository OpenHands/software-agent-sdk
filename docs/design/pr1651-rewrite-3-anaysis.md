# PR #1651 Rewrite Analysis: Conversation-Based vs AgentContext-Based Plugin Loading

## 1. Executive Summary

This document analyzes PR #1651's evolution and the reviewers' feedback to determine the best path forward for plugin loading. The key tension is between two approaches:

1. **Conversation-based** (cd99dd7): Plugin params on `StartConversationRequest` with loading logic in `ConversationService`
2. **AgentContext-based** (current): Plugin params on `AgentContext` with loading in pydantic model validator

Reviewers (@enyst, @xingyaoww) have expressed preference for the **Conversation-based approach** but with implementation changes that address the original shortcomings.

---

## 2. Current State (HEAD of PR Branch)

### Implementation Pattern
- Plugin fields (`plugin_source`, `plugin_ref`, `plugin_path`) are on `AgentContext`
- Plugin loading happens via `AgentContext._load_plugin()` pydantic model validator
- Skills merged into `AgentContext.skills`
- MCP config and hooks exposed via `AgentContext.plugin_mcp_config` and `plugin_hooks` properties
- `ConversationService` extracts hooks from `agent.agent_context.plugin_hooks`
- `LocalConversation` extracts hooks similarly

### API Usage
```python
# Current pattern
agent_context = AgentContext(
    plugin_source="github:owner/repo",
    plugin_ref="v1.0.0",
    plugin_path="plugins/my-plugin",
)
agent = Agent(llm=llm, agent_context=agent_context, tools=[...])
conversation = Conversation(agent=agent, workspace="./workspace")
```

### Key Files Changed (from main)
- `openhands-sdk/openhands/sdk/context/agent_context.py` - Added plugin fields and `_load_plugin()` validator
- `openhands-agent-server/openhands/agent_server/models.py` - Removed plugin fields from `StartConversationRequest`
- `openhands-agent-server/openhands/agent_server/conversation_service.py` - Removed `_load_and_merge_plugin()` method

---

## 3. State at cd99dd7 (Original Conversation-Based)

### Implementation Pattern
- Plugin fields (`plugin_source`, `plugin_ref`, `plugin_path`) on `StartConversationRequest`
- Plugin loading in `ConversationService._load_and_merge_plugin()`
- Skill merging in `ConversationService._merge_skills()`
- Plugin merging in `ConversationService._merge_plugin_into_request()`
- `hook_config` stored on `StoredConversation` (populated from plugin)
- `LocalConversation` accepted `hook_config` parameter

### API Usage
```python
# cd99dd7 pattern (agent-server)
POST /api/conversations/start
{
  "agent": {...},
  "plugin_source": "github:owner/repo",
  "plugin_ref": "v1.0.0",
  "plugin_path": "plugins/my-plugin"
}
```

### Key Implementation Details
```python
# ConversationService at cd99dd7
def _load_and_merge_plugin(
    self, request: StartConversationRequest
) -> tuple[StartConversationRequest, HookConfig | None]:
    # Validates plugin_path for security
    # Calls Plugin.fetch() and Plugin.load()
    # Merges skills via _merge_skills()
    # Merges MCP config directly
    # Returns updated request + hook_config
```

---

## 4. What Reviewers Prefer About Conversation-Based Approach

### 4.1 Correct Ownership Boundary

**@enyst (dismissed review):**
> "I'm not sure about adding plugin source vars to the start request... Could we instead maybe set up the plugin, then start request"

**@openhands-ai analysis:**
> "Conversation-level: Correct ownership boundary for 'things that vary per conversation.' This matches engel's 'references are per conversation'."

**Key insight:** Plugin references naturally belong at the conversation level because:
- Different conversations may use different plugin sets/versions
- Restoring a conversation should restore its plugin set
- Hooks and MCP servers are runtime concerns attached to conversation, not agent knowledge

### 4.2 Natural Fit for Hooks and MCP

**@enyst (inline comment):**
> "Hooks are event handlers that intercept actions during conversation execution... Unlike skills (which are prompt content), hooks are **runtime behavior** attached to the conversation engine, not the agent's knowledge or capabilities."

**@openhands-ai synthesis:**
> "Hooks + MCP are closer to 'conversation runtime config' than 'LLM context'"

**Key insight:** AgentContext is conceptually "what we send/manage for the LLM", but:
- Hooks execute shell scripts at conversation lifecycle events
- MCP servers run processes during conversation runtime
- These don't belong in AgentContext's responsibility scope

### 4.3 Conceptual Clarity

**@enyst (inline comment on pr1651.md):**
> "AgentContext, like other Agent components, should be immutable itself during a conversation. So idk, this reads to me like, let's put some stuff in AgentContext that it shouldn't have"

**@openhands-ai pros for Conversation-level:**
> "It keeps hooks + MCP in the same conceptual bucket as 'conversation runtime config'. It avoids redefining AgentContext's responsibility boundary in a way that could sprawl."

### 4.4 Multiple Plugins Support

**@enyst (inline comment on README.md example):**
> "What if we have multiple plugins, which is normal for an extensible software? 🤔"

**@enyst (inline comment on local_conversation.py):**
> "Is this hooks from a single plugin or from more? ...now the question also becomes: why from one plugin and not more? We want the SDK to support multiple plugins, I believe. Could we maybe load this one plugin sent from the client app the same way we load any other plugin installed here?"

**@jpshackelford (response):**
> "I agree that we should be building to support multiple plugins, without a doubt. I'll make changes to address this."

**Key insight:** The current implementation only supports a single plugin (`plugin_source`). Extensible software should support loading multiple plugins per conversation, with proper merging semantics for skills, hooks, and MCP config from all plugins.

---

## 5. What Was Objectionable in cd99dd7 (Original Implementation)

### 5.1 API Inconsistency

**@xingyaoww (inline comment):**
> "Why are we adding these fields in `StartConversationRequest`, but not in `LocalConversation`? Ideally we will keep the arguments in `StartConversationRequest` the same as `LocalConversation`"

**Problem:** cd99dd7 had plugin params on the server API (`StartConversationRequest`) but not on the SDK's `LocalConversation`. This creates two different patterns for users:
- SDK users: No way to load plugins via `LocalConversation`
- Server users: Plugin params on request body

### 5.2 Implementation Logic in Wrong Layer

**@xingyaoww (inline comment):**
> "Similarly, these implementations should all be in the `openhands-sdk` packages, rather than the server side. Server side could choose to use these functions if needed. That way we create one source of truth."

**Problem:** cd99dd7 had `_load_and_merge_plugin()`, `_merge_skills()`, and `_merge_plugin_into_request()` all in `ConversationService` (agent-server). This means:
- SDK users can't benefit from this logic
- No single source of truth for plugin merging
- Server duplicates SDK-level concerns

### 5.3 Missing SDK Integration

**Problem:** At cd99dd7:
- `LocalConversation.__init__()` had no `plugin_source`, `plugin_ref`, `plugin_path` parameters
- `Conversation()` factory had no plugin parameters
- SDK users had no path to load plugins

---

## 6. Go-Forward Plan

Based on reviewer feedback, the ideal implementation would be:

### 6.1 Plugin Parameters on Conversation (Not AgentContext) - With Multi-Plugin Support

**Add to LocalConversation and Conversation factory:**
```python
# openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py
def __init__(
    self,
    agent: AgentBase,
    workspace: str | Path | LocalWorkspace,
    plugins: list[PluginSource] | None = None,  # NEW: list of plugin specs
    # ... existing params
):

# Where PluginSource is a structured type (in openhands.sdk.plugin.types):
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

**Note on `repo_path`:** This parameter is only relevant when loading from a **git repository** (not local filesystem paths). It specifies a subdirectory within the repo where the plugin is located. Common use case is monorepos:

```
my-plugins-repo/                          # source="github:org/my-plugins-repo"
├── plugins/
│   ├── security-plugin/                  # repo_path="plugins/security-plugin"
│   ├── logging-plugin/                   # repo_path="plugins/logging-plugin"
│   └── custom-tools/                     # repo_path="plugins/custom-tools"
└── README.md
```

For local filesystem paths, just specify the full path in `source`:
```python
PluginSource(source="/path/to/my-plugin")  # No repo_path needed
```

**Alternative simpler API (list of strings with optional structured form):**
```python
def __init__(
    self,
    agent: AgentBase,
    workspace: str | Path | LocalWorkspace,
    plugin_sources: list[str | PluginSource] | None = None,  # NEW
    # ... existing params
):
```

**Add to Conversation factory:**
```python
# openhands-sdk/openhands/sdk/conversation/conversation.py
def Conversation(
    agent: Agent | AgentBase,
    workspace: str | Path | LocalWorkspace,
    plugins: list[PluginSource] | None = None,
    # ... existing params
) -> LocalConversation:
```

**Single plugin backward compatibility (optional):**
```python
# For simpler single-plugin cases, could also support:
plugin_source: str | None = None,  # Convenience for single plugin
plugin_ref: str | None = None,
plugin_path: str | None = None,
# These would be converted to plugins=[PluginSource(...)] internally
```

### 6.2 Plugin Loading as SDK Utility (Not LocalConversation-Only)

**Key Architectural Constraint:**
> "Plugin fetching must happen **inside the sandbox/runtime** (where the agent runs)."

This means:
- **LocalConversation**: Plugin fetch/load happens locally
- **RemoteConversation**: Plugin fetch/load happens on the remote server

The orchestration logic should be a **shared SDK utility**, not embedded in LocalConversation:

```python
# openhands/sdk/plugin/loader.py (new module)
from openhands.sdk.plugin import Plugin
from openhands.sdk.plugin.utils import merge_skills, merge_mcp_configs

def load_plugins(
    plugin_specs: list[PluginSource],
    agent: AgentBase,
    max_skills: int = 100,
) -> tuple[AgentBase, HookConfig | None]:
    """Load multiple plugins and merge them into the agent.
    
    This is the canonical function for plugin loading. It should be used by:
    - LocalConversation (for SDK-direct users)
    - ConversationService (for agent-server users)
    
    Args:
        plugin_specs: List of plugin sources to load
        agent: Agent to merge plugins into
        max_skills: Maximum total skills allowed
        
    Returns:
        Tuple of (updated_agent, merged_hook_config)
    """
    if not plugin_specs:
        return agent, None
    
    merged_context = agent.agent_context
    merged_mcp = agent.mcp_config or {}
    all_hooks: list[HookConfig] = []
    
    for spec in plugin_specs:
        # Fetch (downloads if needed, returns cached path)
        # Note: repo_path maps to Plugin.fetch()'s subpath parameter
        path = Plugin.fetch(source=spec.source, ref=spec.ref, subpath=spec.repo_path)
        plugin = Plugin.load(path)
        
        # Merge skills and MCP using existing SDK utilities
        merged_context, merged_mcp = plugin.merge_into(
            merged_context, merged_mcp, max_skills=max_skills
        )
        
        # Collect hooks for later combination
        if plugin.hooks:
            all_hooks.append(plugin.hooks)
    
    # Combine all hook configs (concatenation semantics)
    combined_hooks = merge_hook_configs(all_hooks) if all_hooks else None
    
    # Create updated agent with merged content
    updated_agent = agent.model_copy(update={
        "agent_context": merged_context,
        "mcp_config": merged_mcp,
    })
    
    return updated_agent, combined_hooks
```

**Why a utility function, not just LocalConversation?**

| Scenario | Where Plugin Loading Happens | Uses Utility |
|----------|------------------------------|--------------|
| SDK → LocalConversation | Locally (in LocalConversation.__init__) | ✅ Yes |
| SDK → RemoteConversation | On server (in ConversationService) | ✅ Yes |
| Agent Server directly | On server (in ConversationService) | ✅ Yes |

The utility ensures **single source of truth** for:
1. Fetch → Load → Merge orchestration
2. Multi-plugin merge semantics
3. Error handling and validation

**Merge semantics:**
| Content | Merge Strategy | Rationale |
|---------|---------------|-----------|
| Skills | Override by name (last wins) | Duplicates confusing for LLM |
| MCP Config | Override by key (last wins) | Server definitions should be unique |
| Hooks | Concatenate (all run) | Multiple handlers is standard pattern |

**How each path uses the utility:**

```python
# LocalConversation.__init__()
if plugins:
    self.agent, hook_config = load_plugins(plugins, agent)
    # hook_config used when creating HookProcessor

# ConversationService.start_conversation()
if request.plugins:
    updated_agent, hook_config = load_plugins(request.plugins, request.agent)
    request = request.model_copy(update={"agent": updated_agent})
    # hook_config stored in StoredConversation

# RemoteConversation.__init__() - just sends specs to server
payload = {
    "agent": agent.model_dump(),
    "plugins": [p.model_dump() for p in plugins],  # Server does the loading
}
```

**Benefits:**
- Single source of truth for orchestration logic
- Works for both LocalConversation and ConversationService
- No I/O side effects in model validators
- Explicit control over when loading happens
- Clear error handling path
- Supports multiple plugins naturally

### 6.3 API Parity Between StartConversationRequest and LocalConversation

**StartConversationRequest should match LocalConversation:**
```python
# openhands-agent-server/openhands/agent_server/models.py
class StartConversationRequest(BaseModel):
    agent: AgentBase
    workspace: LocalWorkspace
    plugins: list[PluginSource] | None = None  # NEW (matching LocalConversation)
    # ... existing fields

# PluginSource imported from SDK (openhands.sdk.plugin.types)
from openhands.sdk.plugin.types import PluginSource
```

**Example API usage:**
```json
POST /api/conversations/start
{
  "agent": {...},
  "plugins": [
    {"source": "github:org/security-plugin", "ref": "v2.0.0"},
    {"source": "github:org/logging-plugin"},
    {"source": "github:org/monorepo", "repo_path": "plugins/custom-tools"},
    {"source": "/local/path/to/custom-plugin"}
  ]
}
```

### 6.4 ConversationService Delegates to SDK

**ConversationService passes plugin params to LocalConversation:**
```python
# In ConversationService.start_conversation()
# Plugin loading happens inside EventService → LocalConversation
# ConversationService just passes the params through
stored = StoredConversation(
    id=conversation_id,
    # hook_config populated by LocalConversation after plugin load
    **request.model_dump()
)
```

### 6.5 Remove Plugin Loading from AgentContext

**Remove from AgentContext:**
- Remove `plugin_source`, `plugin_ref`, `plugin_path` fields
- Remove `_load_plugin()` model validator
- Remove `_loaded_plugin_mcp_config`, `_loaded_plugin_hooks` private attrs
- Remove `plugin_mcp_config` and `plugin_hooks` properties

**OR keep as optional backward-compat transport** (if needed for existing integrations).

---

## 7. Benefits of Go-Forward Plan

### vs. Current AgentContext-Based Approach
| Aspect | AgentContext (Current) | Conversation-Based (Proposed) |
|--------|------------------------|------------------------------|
| Conceptual fit | ❌ Hooks/MCP don't belong | ✅ Natural for runtime config |
| I/O in validators | ❌ Side effects | ✅ Explicit loading |
| Resume behavior | ❌ May re-trigger fetch | ✅ No re-fetch |
| Responsibility scope | ❌ AgentContext does too much | ✅ Single responsibility |
| Multi-plugin support | ❌ Single plugin only | ✅ List of plugins |

### vs. cd99dd7 (Original)
| Aspect | cd99dd7 | Go-Forward |
|--------|---------|------------|
| API parity | ❌ Server-only | ✅ SDK + Server aligned |
| Source of truth | ❌ Server implements | ✅ SDK is source of truth |
| SDK usability | ❌ No plugin params | ✅ Full plugin support |
| Multi-plugin support | ❌ Single plugin only | ✅ List of plugins |

---

## 8. Draft PR Comment (Not Posted)

```markdown
## Proposed Changes: Move Back to Conversation-Based Plugin Loading (with Multi-Plugin Support)

Based on the discussion, I understand reviewers prefer plugin configuration at the **Conversation level** rather than AgentContext, and want support for **multiple plugins**. Here's my plan to revise the PR:

### Key Changes
1. **Add `plugins: list[PluginSource]` to LocalConversation, RemoteConversation, and Conversation factory** - Supports multiple plugins per conversation
2. **Create SDK utility `load_plugins(specs, agent)` in `openhands.sdk.plugin.loader`** - Single source of truth for fetch/load/merge orchestration
3. **Both LocalConversation and ConversationService use the utility** - Same logic, different execution location
4. **Restore plugin params on StartConversationRequest** - `plugins` list for API parity
5. **Remove plugin loading from AgentContext** - Clean up the conceptually-incorrect placement
6. **Implement proper merge semantics for multiple plugins:**
   - Skills: later plugins override earlier (by name)
   - MCP config: later plugins override earlier (by key)
   - Hooks: concatenate (all run, order matters for blocking)

### Why This Improves on cd99dd7 (Original)
- **SDK is single source of truth** - Orchestration logic in SDK utility (`load_plugins`), not duplicated in ConversationService
- **Works for both Local and Remote** - LocalConversation calls utility directly; ConversationService uses same utility on server
- **API parity** - Both SDK users (`Conversation()`) and server users (`StartConversationRequest`) have same `plugins` param
- **No blocking I/O in validators** - Explicit utility call vs hidden pydantic side effect
- **Multi-plugin support** - Can load multiple plugins per conversation (the original only supported one)

### Architecture Diagram
```
┌─────────────────────────────────────────────────────────────────────────┐
│                     openhands.sdk.plugin.loader                          │
│                                                                          │
│   load_plugins(specs, agent) -> (updated_agent, hook_config)            │
│     - Plugin.fetch() for each spec                                       │
│     - Plugin.load() for each spec                                        │
│     - plugin.merge_into() for each plugin                               │
│     - merge_hook_configs() to combine hooks                              │
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

### Example Usage
```python
# SDK with LocalWorkspace - plugins loaded locally
conversation = Conversation(
    agent=agent,
    workspace="./workspace",
    plugins=[
        PluginSource(source="github:org/security-plugin", ref="v2.0.0"),
        PluginSource(source="github:org/logging-plugin"),
        # Plugin from monorepo - repo_path specifies subdirectory within git repo
        PluginSource(source="github:org/plugins-monorepo", repo_path="plugins/custom-tools"),
        # Local plugin - no repo_path needed, just full path in source
        PluginSource(source="/local/path/to/my-plugin"),
    ]
)

# SDK with RemoteWorkspace - plugins sent to server, loaded there
conversation = Conversation(
    agent=agent,
    workspace=RemoteWorkspace(host="http://agent-server:8000"),
    plugins=[
        PluginSource(source="github:org/security-plugin", ref="v2.0.0"),
        PluginSource(source="github:org/logging-plugin"),
    ]
)

# Agent Server API
POST /api/conversations/start
{
  "agent": {...},
  "plugins": [
    {"source": "github:org/security-plugin", "ref": "v2.0.0"},
    {"source": "github:org/logging-plugin"},
    {"source": "github:org/plugins-monorepo", "repo_path": "plugins/custom-tools"}
  ]
}
```

### Conceptual Benefits
- Hooks and MCP config are "conversation runtime" concerns, not "agent context"
- AgentContext stays immutable and focused on LLM-facing context
- Plugin refs stored per-conversation for reproducible restore
- Extensible software can load multiple plugins (security, logging, domain-specific, etc.)
- Single SDK utility works for all execution contexts (local, remote server)

Let me know if this direction aligns with what you had in mind.
```

---

## 9. Summary

The reviewers' preference is clear: **plugin configuration belongs at the Conversation level**, not AgentContext, and the SDK should support **multiple plugins per conversation**. The original cd99dd7 had the right intuition but wrong execution (server-only, no SDK parity, single plugin only). The current AgentContext approach solved some consistency issues but introduced conceptual problems.

The go-forward plan combines the best of both:
- Plugin params on Conversation (conceptually correct)
- **`plugins: list[PluginSource]`** for multi-plugin support (extensibility)
- **SDK utility `load_plugins()`** as single source of truth for orchestration
- Utility used by both LocalConversation AND ConversationService (not just one)
- API parity between StartConversationRequest, LocalConversation, and RemoteConversation
- Explicit loading, not pydantic validator (no side effects)
- Clear merge semantics: skills override by name, MCP by key, hooks concatenate

**Key insight**: The question "should it be in a utility so it works with both LocalConversation and RemoteConversation?" led to the realization that:
1. **LocalConversation** loads plugins locally (for SDK-direct users)
2. **RemoteConversation** sends plugin specs to server, where **ConversationService** loads them
3. Both LocalConversation and ConversationService should use the **same SDK utility**
4. This is the true meaning of "single source of truth" - not just for merge logic, but for the entire orchestration
