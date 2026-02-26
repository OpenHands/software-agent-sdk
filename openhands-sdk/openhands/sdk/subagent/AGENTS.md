# Subagent loader (file-based agents): design + invariants

This package (`openhands.sdk.subagent`) centralizes **subagent discovery** and **registration**.
It exists so that contributors (human or agentic) can answer:

- “Where did this agent come from?”
- “Why did this definition win over the other one?”

without reverse-engineering `LocalConversation` and the loader.

## Scope

- **File-based agents**: Markdown files (`*.md`) with YAML frontmatter.
- **Plugin agents**: `Plugin.agents` (already parsed by the plugin loader; registered here).
- **Programmatic agents**: `register_agent(...)` (highest precedence, never overwritten).
- **Built-in agents**: `subagent/builtins/*.md` (lowest precedence; used only as a fallback).

Relevant implementation files:

- `load.py`: filesystem discovery + parse-error handling.
- `schema.py`: Markdown/YAML schema and parsing rules.
- `registry.py`: registry API + “first registration wins” semantics.
- `conversation/impl/local_conversation.py`: the **call order** that establishes precedence.

## Invariant 1: discovery locations & file rules

### Directories scanned

**Project-level (higher priority than user-level):**

1. `{project}/.agents/agents/*.md`
2. `{project}/.openhands/agents/*.md`

**User-level (lower priority than project-level):**

3. `~/.agents/agents/*.md`
4. `~/.openhands/agents/*.md`

Notes:

- Only the **top-level** `*.md` files are scanned.
  - Subdirectories (e.g. `{project}/.agents/skills/…`) are ignored.
- `README.md` / `readme.md` is always skipped.
- Directory iteration is deterministic (`sorted(dir.iterdir())`).

### Parse failures must be non-fatal

If a single file fails to parse (invalid YAML frontmatter, malformed Markdown, etc.),
loading must:

- log a warning (with stack trace), and
- continue scanning other files.

(See `load_agents_from_dir` in `load.py`.)

## Invariant 2: resolution / precedence (“who wins”)

### Core rule: first registration wins

Once an agent name is registered in the global registry (`_agent_factories`), later
sources must not overwrite it.

This is enforced by using:

- `register_agent(...)` (raises on duplicates; used for programmatic registration)
- `register_agent_if_absent(...)` (skips duplicates; used for plugins, file agents, builtins)

### Effective precedence order

When a `LocalConversation` becomes ready, it establishes the following priority:

1. **Programmatic** `register_agent(...)` (pre-existing; must never be overwritten)
2. **Plugin-provided** agents (`Plugin.agents` → `register_plugin_agents`)
3. **Project** file-based agents
   - `{project}/.agents/agents/*.md` then `{project}/.openhands/agents/*.md`
4. **User** file-based agents
   - `~/.agents/agents/*.md` then `~/.openhands/agents/*.md`
5. **SDK built-ins** (`subagent/builtins/*.md`)

This is the order implemented by:

- `LocalConversation._ensure_plugins_loaded()` → registers plugin agents
- `LocalConversation._register_file_based_agents()` → registers project/user file agents, then built-ins

### Deduplication rules inside file-based loading

File-based loading has *two* layers of “first wins” deduplication:

1. **Within a level** (`load_project_agents` / `load_user_agents`):
   - `.agents/agents` wins over `.openhands/agents` for the same agent name.
2. **Across levels** (`register_file_agents`):
   - project wins over user for the same agent name.

If you change these rules, update the unit tests in `tests/sdk/subagent/`.

## Invariant 3: Markdown agent schema & semantics

### Frontmatter keys

Supported YAML frontmatter keys (see `AgentDefinition.load` in `schema.py`):

- `name` (default: filename stem)
- `description`
- `tools` (default: `[]`)
  - accepts either a string (`tools: ReadTool`) or a list
- `model` (default: `inherit`)
  - `inherit` means “use the parent agent’s LLM instance”
  - any other string means “copy parent LLM and override the `model` field”
- `color` (optional)

**Unknown keys are preserved** in `AgentDefinition.metadata`.

### Body → system prompt

The Markdown **body content** becomes the agent’s `system_prompt`.

Currently, when the agent is instantiated, this is applied as:

- `AgentContext(system_message_suffix=agent_def.system_prompt)`

meaning it is appended to the parent system message (not a complete replacement).

### Tools mapping

`tools` values are stored as tool names (`list[str]`) and mapped at instantiation time to:

- `Tool(name=tool_name)`

No validation is performed at load time beyond “stringification”.

### Trigger examples in description

The loader extracts `<example>…</example>` tags from `description` (case-insensitive)
into `AgentDefinition.when_to_use_examples`.

These examples are used for triggering / routing logic elsewhere.

### Minimal example

```markdown
---
name: code-reviewer
description: |
  Reviews code changes.

  <example>please review this PR</example>
  <example>can you do a security review?</example>
tools:
  - ReadTool
  - GrepTool
model: inherit
color: purple
# Any extra keys are preserved in `metadata`:
audience: maintainers
---

You are a meticulous code reviewer.
Focus on correctness, security, and clear reasoning.
```

## Debugging: “why did it win?”

When debugging precedence issues:

1. Check the precedence table above.
2. Enable SDK logs (INFO/DEBUG): registration logs include the agent name and, for file-based agents,
   the source path (`agent_def.source`).
3. Verify the filesystem location: project overrides user; `.agents/` overrides `.openhands/`.

## User-facing documentation

User docs for Markdown agents live in the docs repo. If you change any of the
invariants above, update both this file and the user docs.

- Docs PR tracking this feature: https://github.com/OpenHands/docs/pull/358
