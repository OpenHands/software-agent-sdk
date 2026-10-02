# Live e2e: sub-agent scoping on a real agent-server

Rig: `.venv/bin/python .pr/sub_agent_scope_e2e.py --mock <agent-canvas>/tests/e2e/mock-llm/scripts/mock-llm-server.py [--server-python <main checkout>/.venv/bin/python]`.

It starts a real agent-server and Agent Canvas's scripted mock LLM, launches conversations over the REST API (`agent_profile_id`, `agent_settings`, explicit `agent`), scripts the model's `task`/`workflow`/`grep`/`terminal` calls, and asserts on what the model was offered and what each delegation returned. Every launch forwards five `agent_definitions`: `grepper` (`[grep]`), `mini-same` / `mini-diff` / `mini-extra` (`[grep]` plus a real stdio MCP server: the user's `mini` config, `mini` with other args, an `extra` server), and `orchestrator` (`[grep, task_tool_set]`). Scenario J restarts the server and resumes a conversation.

## This branch: 47 checks, 0 failures

```
agent-server 1.50.1
browser usable on this host: True

[materialize]
  PASS  saved read-only: tools scoped, MCP not
  PASS  saved mcp-mini: tools and MCP scoped
  PASS  draft with bare workflow: scoped
  PASS  stored profile has no scope param

[A] read-only profile [glob, grep, task_tool_set], mcp refs null
  PASS  finished
  PASS  offered: forwarded helpers only, no built-ins
  PASS  code-explorer refused
  PASS  deprecated alias 'explore' refused as code-explorer
  PASS  grepper ran
  PASS  grepper's own grep call found the file
  PASS  resuming that task as bash-runner refused
  PASS  conversation's agent carries the scope

[B] coder profile [terminal, file_editor, task_tracker, task_tool_set]
  PASS  finished
  PASS  offered: built-ins it can run, not web-researcher or grep helpers
  PASS  web-researcher refused
  PASS  bash-runner ran
  PASS  bash-runner's terminal really ran

[C] mcp-mini profile [grep, task_tool_set], mcp_server_refs=['mini']
  PASS  finished
  PASS  offered: same-config MCP helper, not a different or extra server
  PASS  same name, other config refused
  PASS  extra server refused
  PASS  same server ran

[D] mcp-none profile [grep, task_tool_set], mcp_server_refs=[]
  PASS  offered: no MCP helpers
  PASS  MCP helper refused

[E] nested: orchestrator delegates under the inherited scope
  PASS  finished
  PASS  orchestrator is offered only what it holds
  PASS  nested bash-runner refused
  PASS  nested MCP helper refused (orchestrator has no MCP server)
  PASS  orchestrator finished

[F] wf-set profile: workflow delegation
  PASS  finished
  PASS  workflow refuses code-explorer
  PASS  workflow runs grepper

[F] wf-bare profile: workflow delegation
  PASS  finished
  PASS  workflow refuses code-explorer
  PASS  workflow runs grepper

[G] legacy v2 profile with enable_sub_agents: true
  migrated tools: ['terminal', 'file_editor', 'task_tracker', 'browser_tool_set', 'task_tool_set', 'switch_llm']
  PASS  offered: every built-in its pinned tools allow, no grep helpers
  PASS  web-researcher offered iff the browser is usable
  PASS  general-purpose ran

[H] agent_settings launch, no profile: unchanged
  PASS  every sub-agent offered
  PASS  code-explorer ran

[I] explicit agent launch
  PASS  client-set scope is honoured
  invalid scope param -> HTTP 500
  PASS  invalid scope rejected at start, like any bad tool param
  PASS  server still healthy

[J] restart the server and resume conversation A
  PASS  finished
  PASS  after restart: offered list unchanged
  PASS  after restart: bash-runner still refused
  PASS  after restart: grepper still runs

0 failure(s)
```

## main (8034b0dbc), same rig

On main nothing is scoped, so `code-explorer` really runs from the read-only profile; that consumes scripted turns meant for later steps and the rig stops there.

```
agent-server 1.50.1
browser usable on this host: True
[materialize]
  FAIL  saved read-only: tools scoped, MCP not  []
  FAIL  saved mcp-mini: tools and MCP scoped  []
  FAIL  draft with bare workflow: scoped  [[None]]
  PASS  stored profile has no scope param
[A] read-only profile [glob, grep, task_tool_set], mcp refs null
  FAIL  finished  [error]
  FAIL  offered: forwarded helpers only, no built-ins  [['bash-runner', 'code-explorer', 'general-purpose', 'grepper', 'mini-diff', 'mini-extra', 'mini-same', 'orchestrator', 'web-researcher']]
  FAIL  code-explorer refused  []
  FAIL  deprecated alias 'explore' refused as code-explorer  [Task ID: task_00000001
IndexError: list index out of range
```
