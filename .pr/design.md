# Fix: Shared LLM/Agent aliasing across conversations (#3443)

## Problem

When the same `LLM`/`Agent` is reused across multiple `LocalConversation`
instances, two per-conversation values get pinned onto the shared object and
leak to subsequent conversations:

| Value | Storage | Guard | Effect |
|-------|---------|-------|--------|
| `_prompt_cache_key` | `PrivateAttr` on LLM | `if is None` | 2nd conv inherits 1st's OpenAI cache shard |
| `x-litellm-session-id` | `extra_headers` dict (public `Field`) | `if not in existing` | 2nd conv routes to 1st's LiteLLM deployment |

Both use the same pattern: mutate the LLM, skip if already set. The second
conversation sees the first's value and keeps it.

**Practical impact is low** — the server path always deserializes a fresh agent,
so this only hits standalone SDK usage. But it's a correctness issue.

---

## Approaches

### A. Conversation-owned state, call-time injection

Move per-conversation values out of the LLM entirely. The conversation stores
them; they get injected at completion time.

**Changes:**

1. Add a `_call_context` PrivateAttr to LLM (or a small frozen dataclass):
   ```python
   # llm.py
   @dataclass(frozen=True)
   class LLMCallContext:
       prompt_cache_key: str | None = None
       session_id: str | None = None

   class LLM(BaseModel):
       _call_context: LLMCallContext = PrivateAttr(default_factory=LLMCallContext)
   ```

2. `LocalConversation` sets the context instead of mutating headers:
   ```python
   # local_conversation.py — replaces _pin_prompt_cache_key + _pin_session_affinity_header
   def _bind_conversation_context(self, llm: LLM) -> None:
       llm._call_context = LLMCallContext(
           prompt_cache_key=str(self._state.id),
           session_id=str(self._state.id),
       )
   ```

3. `select_chat_options()` reads from context instead of LLM fields:
   ```python
   # chat_options.py (lines 33-34, 99-100)
   ctx = llm._call_context
   if ctx.session_id:
       headers = out.get("extra_headers") or {}
       out["extra_headers"] = {"x-litellm-session-id": ctx.session_id, **headers}
   if ctx.prompt_cache_key:
       out["prompt_cache_key"] = ctx.prompt_cache_key
   ```

**Pros:**
- LLM stays clean — `extra_headers` is purely user-supplied config
- Aligns with "stateless by default" design principle
- Each conversation always stamps its own context; no guards needed
- Sub-agent inheritance works naturally: `model_copy()` shallow-copies the
  `PrivateAttr`, so sub-agents inherit the parent's context until explicitly
  re-bound
- `fork()` JSON round-trip drops PrivateAttr → fresh context on re-init ✅
- Fixes the `select_chat_options` bypass bug for free (session ID is injected
  at call time regardless of user-supplied `extra_headers`)

**Cons:**
- Touches 4 files (llm.py, local_conversation.py, chat_options.py, responses_options.py)
- Slightly more plumbing than approach B
- Sub-agent cache-key sharing needs consideration: currently sub-agents inherit
  parent's `_prompt_cache_key` via `model_copy` guard. With call-time injection,
  we'd need to decide whether `_bind_conversation_context` skips sub-agent LLMs
  or intentionally re-binds them

---

### B. Defensive deep-copy in `LocalConversation.__init__`

Each conversation gets its own agent via JSON round-trip (same as `fork()`).

**Changes:**

```python
# local_conversation.py __init__, ~line 205
agent_cls = type(agent)
agent = agent_cls.model_validate(
    agent.model_dump(context={"expose_secrets": True}),
)
```

**Pros:**
- ~3 lines, fixes everything at once
- Already proven pattern from `fork()`
- No changes to LLM, options, or call-time code

**Cons:**
- Changes the contract: callers can't observe agent mutations after creating
  the conversation (the reference goes stale)
- Extra memory + CPU for the copy on every conversation
- Doesn't fix the `select_chat_options` bypass (user-supplied `extra_headers`
  still overwrites session ID)
- `extra_headers` is a public Field, so the session ID from `_pin_session_affinity_header`
  survives the round-trip — a second conversation from the same source agent
  would still inherit stale headers unless `_pin_session_affinity_header`
  is changed to always-overwrite

---

### C. Always-overwrite (remove guards)

Drop the `if is None` / `if not in existing` checks so each conversation
stamps its own values unconditionally.

**Changes:**

```python
# _pin_prompt_cache_key: remove the guard
def _pin_prompt_cache_key(self) -> None:
    self.agent.llm._prompt_cache_key = str(self._state.id)

# _pin_session_affinity_header: always overwrite
def _pin_session_affinity_header(self, llm: LLM) -> None:
    existing = llm.extra_headers or {}
    llm.extra_headers = {
        "x-litellm-session-id": str(self._state.id),
        **existing,
    }
```

**Pros:**
- Minimal diff (~2 line removals)
- Simple to reason about

**Cons:**
- Still mutates shared LLM state — the last conversation to init "wins" on the
  shared object. Race condition with concurrent conversations.
- Breaks `_prompt_cache_key` sub-agent inheritance: sub-agents get their own
  shard instead of sharing the parent's, defeating cross-sub-agent cache reuse.
- Doesn't fix the `select_chat_options` bypass.

---

## Recommendation

**Approach A** is the cleanest long-term fix. It moves ownership of
per-conversation values to where they conceptually belong (the conversation),
keeps the LLM stateless, and naturally handles all the edge cases:

- Shared LLM across conversations: each conversation binds its own context ✅
- Sub-agent inheritance: `model_copy()` preserves context from parent ✅
- `fork()`: PrivateAttr dropped on round-trip, re-bound on init ✅
- `select_chat_options` bypass: session ID injected at call time ✅

The sub-agent cache-key question needs a decision: should sub-agents share the
parent conversation's cache shard (current behavior) or get their own? If shared,
`_bind_conversation_context` should only run on the primary LLM, not on
`get_all_llms()`. If separate, run it on all.
