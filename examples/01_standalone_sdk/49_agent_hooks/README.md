# Agent-based Hooks Example

This folder demonstrates the `type="agent"` hook — a lifecycle hook whose
decision is produced by an LLM-driven sub-agent rather than a shell script.

For shell-command hooks see [`../33_hooks/`](../33_hooks).

## Why an agent hook

A shell-based PreToolUse hook can only block what its blacklist literally
matches. The agent rewrites `cat /etc/passwd` as `awk '{print}' /etc/passwd`
and slips through. An agent hook reasons about the **semantic intent** of the
command — "reading a sensitive system file" — and denies regardless of the
exact tool name used.

## Example

- **49_agent_hooks.py** — Two agent hooks wired onto a single conversation:
  - **PreToolUse** "security reviewer" denies a command whose intent is to
    read `/etc/passwd`, even though no obvious keyword appears in a blacklist.
  - **Stop** "quality reviewer" refuses to let the main agent finish until
    the required deliverable (`REPORT.md`) is present in the workspace.

Each hook decision is printed to the console via a `HookExecutionEvent`
callback, so you can watch the allow/deny outcomes as the demo runs.

## Running

```bash
export LLM_API_KEY="your-key"
export LLM_MODEL="anthropic/claude-sonnet-4-5-20250929"  # optional
export LLM_BASE_URL="https://your-endpoint"              # optional

python 49_agent_hooks.py
```

## How an agent hook is configured

```python
HookDefinition(
    type=HookType.AGENT,
    name="security-reviewer",      # bucket for cost metrics (hook-agent:<name>)
    system_prompt="...",           # instructs the hook agent; must request JSON
    tools=["file_editor"],         # optional tools the hook agent may use
                                   # (use registered names, e.g. "file_editor",
                                   # "terminal" — not class names like
                                   # "FileEditorTool")
    timeout=60,                    # forwarded to the per-hook LLM copy
    max_iterations=3,              # cap on hook sub-conversation steps
)
```

The hook agent receives the event JSON and must reply with:

```json
{"decision": "allow" | "deny", "reason": "<short explanation>"}
```

Anything else (non-JSON, missing field, sub-conversation error) defaults to
`allow` so a broken hook cannot wedge the main agent.
