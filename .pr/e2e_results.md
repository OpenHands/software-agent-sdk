# Live e2e: sub-agent scoping on a real agent-server

Rig: `.venv/bin/python .pr/sub_agent_scope_e2e.py --mock <agent-canvas>/tests/e2e/mock-llm/scripts/mock-llm-server.py [--server-python <main checkout>/.venv/bin/python]`.
It launches conversations by `agent_profile_id` (and one by `agent_settings`) against Agent Canvas's scripted mock LLM, which calls the `task` tool. Every launch also forwards one `agent_definitions` entry, `mcp-helper` (tools `[grep]`, its own `fetch` MCP server). "offered" is what the `task` tool description listed to the model; "task result" is the tool result the model got back.

## This branch

```
agent-server 1.50.1

materialize read-only, resolved task_tool_set: [{"name": "task_tool_set", "params": {"sub_agent_scope": {"tools": true, "mcp_servers": false}}}]

[profile read-only [glob, grep, task_tool_set] -> code-explorer]
  offered sub-agents: ['mcp-helper']
  task result: Task ID: unknown Subagent: code-explorer Status: error [An error occurred during execution.]  Failed to execute task: Agent 'code-explorer' uses terminal, which this agent does not have.

[profile coder [terminal, file_editor, task_tracker, task_tool_set] -> web-researcher, then bash-runner]
  offered sub-agents: ['bash-runner', 'code-explorer', 'general-purpose']
  task result: Task ID: unknown Subagent: web-researcher Status: error [An error occurred during execution.]  Failed to execute task: Agent 'web-researcher' uses browser_tool_set, which this agent does not have.
  task result: Task ID: task_00000001 Subagent: bash-runner Status: completed sub done

[profile grep-all-mcp [grep, task_tool_set], mcp_server_refs=null -> mcp-helper]
  offered sub-agents: ['mcp-helper']
  task result: Task ID: task_00000001 Subagent: mcp-helper Status: completed sub done

[profile grep-no-mcp [grep, task_tool_set], mcp_server_refs=[] -> mcp-helper]
  offered sub-agents: ['None: every registered agent uses tools or MCP servers this agent does not have.']
  task result: Task ID: unknown Subagent: mcp-helper Status: error [An error occurred during execution.]  Failed to execute task: Agent 'mcp-helper' uses MCP server 'fetch', which this agent does not have.

[agent_settings (no profile) [glob, grep, task_tool_set] -> code-explorer]
  offered sub-agents: ['bash-runner', 'code-explorer', 'general-purpose', 'mcp-helper', 'web-researcher']
  task result: Task ID: task_00000001 Subagent: code-explorer Status: completed sub done
```

## main (8034b0dbc), same rig

```
agent-server 1.50.1

materialize read-only, resolved task_tool_set: [{"name": "task_tool_set", "params": {}}]

[profile read-only [glob, grep, task_tool_set] -> code-explorer]
  offered sub-agents: ['bash-runner', 'code-explorer', 'general-purpose', 'mcp-helper', 'web-researcher']
  task result: Task ID: task_00000001 Subagent: code-explorer Status: completed sub done

[profile coder [terminal, file_editor, task_tracker, task_tool_set] -> web-researcher, then bash-runner]
  offered sub-agents: ['bash-runner', 'code-explorer', 'general-purpose', 'mcp-helper', 'web-researcher']
  task result: Task ID: task_00000001 Subagent: web-researcher Status: completed sub done

[profile grep-all-mcp [grep, task_tool_set], mcp_server_refs=null -> mcp-helper]
  offered sub-agents: ['bash-runner', 'code-explorer', 'general-purpose', 'mcp-helper', 'web-researcher']
  task result: Task ID: task_00000001 Subagent: mcp-helper Status: completed sub done

[profile grep-no-mcp [grep, task_tool_set], mcp_server_refs=[] -> mcp-helper]
  offered sub-agents: ['bash-runner', 'code-explorer', 'general-purpose', 'mcp-helper', 'web-researcher']
  task result: Task ID: task_00000001 Subagent: mcp-helper Status: completed sub done

[agent_settings (no profile) [glob, grep, task_tool_set] -> code-explorer]
  offered sub-agents: ['bash-runner', 'code-explorer', 'general-purpose', 'mcp-helper', 'web-researcher']
  task result: Task ID: task_00000001 Subagent: code-explorer Status: completed sub done
```
