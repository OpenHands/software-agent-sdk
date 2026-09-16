# Subagent loader (file-based agents): design + invariants

See the [project root AGENTS.md](../../../../AGENTS.md) for repository-wide policies and workflows.

This package (`openhands.sdk.subagent`) centralizes **subagent discovery** and **registration**.
It exists so that contributors (human or agentic) can answer:

- “Where did this agent come from?”
- “Why did this definition win over the other one?”

without reverse-engineering `LocalConversation` and the loader.

## Scope

- **File-based agents**: Markdown files (`*.md`) with YAML frontmatter.
- **Plugin agents**: `Plugin.agents` (already parsed by the plugin loader; registered here).
- **Programmatic agents**: explicit `register_agent(...)` calls.
- **Built-in agents**: supplied by `openhands-tools`, outside this SDK package.

Relevant implementation files:

- `load.py`: filesystem discovery + parse-error handling.
- `schema.py`: Markdown/YAML schema and parsing rules.
- `registry.py`: registry API + “first registration wins” semantics.
- `conversation/impl/local_conversation.py`: lazy plugin and file-agent registration.
- `openhands-tools/openhands/tools/preset/default.py`: built-in agent discovery and
  registration.

## Invariant 1: discovery locations & file rules

### Directories scanned

**Project-level (higher priority than user-level):**

1. `{project}/.agents/agents/*.md`
2. `{project}/.openhands/agents/*.md`

**User-level:**

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

`LocalConversation._ensure_agent_ready()` establishes this order for agents loaded
as part of conversation initialization:

1. Existing registry entries, including explicit `register_agent(...)` calls
2. Plugin-provided agents (`Plugin.agents` → `register_plugin_agents`)
3. Project file-based agents
   - `{project}/.agents/agents/*.md` then `{project}/.openhands/agents/*.md`
4. User file-based agents
   - `~/.agents/agents/*.md` then `~/.openhands/agents/*.md`

Built-ins are discovered and registered separately by `openhands-tools` through
`register_builtins_agents()`. Because all non-programmatic sources use
`register_agent_if_absent(...)`, whichever source registers a name first keeps it.
Call built-in registration after higher-priority sources if built-ins should act as
fallbacks. The agent-server registers built-ins during tool-router import, before
per-conversation file discovery.

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
- `tools` (default: `[]`): one tool name or a list of names
- `skills` (default: `[]`): a comma-separated string or a list of skill names
- `model` (default: `inherit`): `inherit` reuses the parent LLM; another value is
  loaded as an LLM profile name from `profile_store_dir` or the default profile store
- `color` (optional)
- `max_iteration_per_run` (optional, positive integer)
- `max_budget_per_run` (optional, positive number in USD)
- `hooks` (optional hook configuration)
- `profile_store_dir` (optional custom LLM profile directory)
- `mcp_config` (optional MCP server map); `mcp_servers` is a deprecated alias
- `permission_mode` (optional): `always_confirm`, `never_confirm`, or `confirm_risky`;
  omission inherits the parent confirmation policy
- `condenser` (optional): omission uses the default summarizing condenser; `none` or
  `false` disables condensation; a mapping configures a condenser

**Unknown keys are preserved** in `AgentDefinition.metadata`.

### Body → system prompt

The Markdown **body content** becomes the agent’s `system_prompt`.

Currently, when the agent is instantiated, this is applied as:

- `AgentContext(system_message_suffix=agent_def.system_prompt)`

meaning it is appended to the parent system message (not a complete replacement).

### Tool and skill resolution

`tools` values remain names until factory instantiation. Each name must already be
registered; unknown tools raise `ValueError`. Valid names become `Tool(name=...)`.

`skills` resolve when the factory is created. Project skills take priority over user
skills, public skills are excluded, and an unknown skill raises `ValueError`.

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
  - terminal
model: inherit
permission_mode: confirm_risky
color: purple
# Any extra keys are preserved in `metadata`:
audience: maintainers
---

You are a meticulous code reviewer.
Focus on correctness, security, and clear reasoning.
```

## User-facing documentation

User docs for Markdown agents live in the docs repo. If you change any of the
invariants above, update both this file and the user docs.

- Published guide: https://docs.openhands.dev/sdk/guides/agent-file-based
