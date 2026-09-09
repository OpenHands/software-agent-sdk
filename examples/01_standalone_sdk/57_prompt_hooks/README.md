# Prompt-based Hooks Example

This example demonstrates `type="prompt"`: a lifecycle hook evaluated by one
LLM completion instead of a shell command or tool-using sub-agent.

The script sends two synthetic `PreToolUse` events through `HookManager`:

- a test command that the policy should allow
- a destructive command that the policy should deny

The commands are event data only and are never executed.

## When to use a prompt hook

Prompt hooks fit policy decisions that can be made from the `HookEvent` payload
alone. They are cheaper and more predictable than agent hooks because they make
one completion and cannot call tools.

Use an agent hook when the evaluator must inspect files, run a command, or gather
other workspace context before deciding. Use a command hook for deterministic
checks that do not need model judgment.

## Running

```bash
export LLM_API_KEY="your-key"
export LLM_MODEL="anthropic/claude-sonnet-4-5-20250929"  # optional
export LLM_BASE_URL="https://your-endpoint"              # optional

python main.py
```

## Configuration

```python
HookDefinition(
    type=HookType.PROMPT,
    name="terminal-safety",
    prompt="Deny terminal commands that recursively delete files ...",
    timeout=30,
)
```

Prompt hooks use the conversation's current LLM. A copied LLM isolates timeout,
metrics, and the stable `prompt-hook:<name>` usage bucket from the main agent.
The configured policy is sent as trusted system context; the serialized hook
event is sent separately and marked as untrusted data.

The SDK automatically selects Chat Completions or the Responses API from the
model's capabilities. Prompt hooks are single-shot and non-streaming, regardless
of the parent LLM's streaming setting.

The executor asks the model for this shared hook result contract:

```json
{"decision": "allow" | "deny", "reason": "<short explanation>"}
```

Missing LLM configuration, provider failures, and invalid responses fail open
with `decision="allow"` and `success=False`, so callers can distinguish an
execution failure from a deliberate allow verdict.
