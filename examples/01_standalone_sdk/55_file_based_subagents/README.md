# File-Based Subagent Definitions

This folder demonstrates how to define subagents as Markdown files with YAML
frontmatter. The SDK discovers them from `.agents/agents/` (or the legacy
`.openhands/agents/`) at project start and registers them for delegation.

## Layout

```
55_file_based_subagents/
├── main.py
└── .agents/
    └── agents/
        ├── code-reviewer.md
        └── security-auditor.md
```

Only top-level `*.md` files are loaded. `README.md` and subdirectories (such
as `skills/`) are ignored.

## Running

```bash
uv run python examples/01_standalone_sdk/55_file_based_subagents/main.py
```

The script prints discovered and registered agent names. No LLM credentials are
required.

## Delegating work

Pair file-based agents with the task tool in a main agent:

```python
from openhands.tools.task import TaskToolSet

agent = Agent(llm=llm, tools=[Tool(name=TaskToolSet.name)])
conversation.send_message("Delegate to code-reviewer to review README.md")
```

See also:

- `examples/01_standalone_sdk/41_task_tool_set.py` — task handoff with resume
- `examples/01_standalone_sdk/42_file_based_subagents.py` — inline `AgentDefinition`
- `openhands-sdk/openhands/sdk/subagent/AGENTS.md` — precedence and invariants