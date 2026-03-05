---
name: design-principles
description: Core architectural design principles of the OpenHands Software Agent SDK. Reference when making architectural decisions, reviewing PRs that change agent/tool/state boundaries, or evaluating whether a proposed change aligns with V1 design goals.
triggers:
- design principles
- architecture decisions
- stateless
- serializable
- composable
---

# SDK Design Principles

Reference: <https://docs.openhands.dev/sdk/arch/design>

## Quick Summary

1. **Optional Isolation over Mandatory Sandboxing**
   Sandboxing is opt-in, not universal. Agent and tool execution runs in a single
   process by default. When isolation is needed, the same stack can be transparently
   containerized.

2. **Stateless by Default, One Source of Truth for State**
   All components — agents, tools, LLMs, configurations — are **immutable Pydantic
   models** validated at construction. The only mutable entity is the conversation
   state. This enables deterministic replay and robust persistence.

3. **Clear Boundaries between Agent and Applications**
   Strict separation between SDK (agent core), tools, workspace, and agent server.
   Applications communicate via APIs, not by embedding the agent.

4. **Composable Components for Extensibility**
   Agents are graphs of interchangeable components — tools, prompts, LLMs, contexts —
   described **declaratively with strong typing**. Developers reconfigure capabilities
   without modifying core code.

## Implications for Development

- Since agents are immutable Pydantic models, their configuration **is** their
  serializable representation. There should be no need to "reverse-engineer" agent
  config from runtime instances.
- Tool implementations (callables) are the only non-serializable part; this is solved
  by `tool_module_qualnames` for remote forwarding.
- Everything else (system_prompt, model, skills, tool names) is already declarative
  data that can be serialized and forwarded directly.
- Avoid patterns that create multiple sources of truth for the same configuration
  (e.g., a factory function AND an extracted definition).
- `model_copy(update=...)` should be used sparingly and through well-defined paths to
  avoid undermining statelessness.
