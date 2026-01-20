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

### 6.1 Plugin Parameters on Conversation (Not AgentContext)

**Add to LocalConversation and Conversation factory:**
```python
# openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py
def __init__(
    self,
    agent: AgentBase,
    workspace: str | Path | LocalWorkspace,
    plugin_source: str | None = None,      # NEW
    plugin_ref: str | None = None,          # NEW
    plugin_path: str | None = None,         # NEW
    # ... existing params
):
```

**Add to Conversation factory:**
```python
# openhands-sdk/openhands/sdk/conversation/conversation.py
def Conversation(
    agent: Agent | AgentBase,
    workspace: str | Path | LocalWorkspace,
    plugin_source: str | None = None,
    plugin_ref: str | None = None,
    plugin_path: str | None = None,
    # ... existing params
) -> LocalConversation:
```

### 6.2 Plugin Loading Logic in SDK

**Move loading to LocalConversation (explicit, not validator-based):**
```python
# In LocalConversation.__init__()
if plugin_source:
    plugin = self._load_plugin(plugin_source, plugin_ref, plugin_path)
    # Merge skills into agent.agent_context
    # Merge MCP config into agent.mcp_config
    # Extract hooks for use in hook processor
```

**Benefits over pydantic validator:**
- No I/O side effects in model deserialization
- Won't re-trigger plugin fetch on conversation resume
- Explicit control over when plugin loading happens
- Clear error handling path

### 6.3 API Parity Between StartConversationRequest and LocalConversation

**StartConversationRequest should match LocalConversation:**
```python
# openhands-agent-server/openhands/agent_server/models.py
class StartConversationRequest(BaseModel):
    agent: AgentBase
    workspace: LocalWorkspace
    plugin_source: str | None = None      # NEW (matching LocalConversation)
    plugin_ref: str | None = None
    plugin_path: str | None = None
    # ... existing fields
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

### vs. cd99dd7 (Original)
| Aspect | cd99dd7 | Go-Forward |
|--------|---------|------------|
| API parity | ❌ Server-only | ✅ SDK + Server aligned |
| Source of truth | ❌ Server implements | ✅ SDK is source of truth |
| SDK usability | ❌ No plugin params | ✅ Full plugin support |

---

## 8. Draft PR Comment (Not Posted)

```markdown
## Proposed Changes: Move Back to Conversation-Based Plugin Loading

Based on the discussion, I understand reviewers prefer plugin configuration at the **Conversation level** rather than AgentContext. Here's my plan to revise the PR:

### Key Changes
1. **Add plugin params to LocalConversation and Conversation factory** - `plugin_source`, `plugin_ref`, `plugin_path`
2. **Move plugin loading from AgentContext to LocalConversation** - Explicit loading in `__init__()`, not pydantic validator
3. **Restore plugin params on StartConversationRequest** - For API parity with LocalConversation
4. **Remove plugin loading from AgentContext** - Clean up the conceptually-incorrect placement

### Why This Improves on cd99dd7 (Original)
- **SDK is single source of truth** - Plugin loading logic in `LocalConversation`, not duplicated in `ConversationService`
- **API parity** - Both SDK users (`Conversation()`) and server users (`StartConversationRequest`) have same plugin params
- **No blocking I/O in validators** - Explicit `_load_plugin()` call vs hidden side effect

### Conceptual Benefits
- Hooks and MCP config are "conversation runtime" concerns, not "agent context"
- AgentContext stays immutable and focused on LLM-facing context
- Plugin refs stored per-conversation for reproducible restore

Let me know if this direction aligns with what you had in mind.
```

---

## 9. Summary

The reviewers' preference is clear: **plugin configuration belongs at the Conversation level**, not AgentContext. The original cd99dd7 had the right intuition but wrong execution (server-only, no SDK parity). The current AgentContext approach solved some consistency issues but introduced conceptual problems.

The go-forward plan combines the best of both:
- Plugin params on Conversation (conceptually correct)
- Loading logic in SDK's LocalConversation (single source of truth)
- API parity between StartConversationRequest and LocalConversation (developer experience)
- Explicit loading, not pydantic validator (no side effects)
