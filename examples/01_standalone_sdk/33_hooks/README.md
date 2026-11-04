# Hooks Examples

This folder demonstrates the OpenHands hooks system.

## Example

- **main.py** - Complete hooks demo showing all four hook types

## Scripts

The `hook_scripts/` directory contains reusable hook script examples:

- `block_dangerous.sh` - Blocks rm -rf commands (PreToolUse)
- `log_tools.sh` - Logs tool usage to a file (PostToolUse)
- `inject_git_context.sh` - Injects git status into prompts (UserPromptSubmit)
- `require_summary.sh` - Requires summary.txt before stopping (Stop)

## Running

```bash
# Set your LLM credentials
export LLM_API_KEY="your-key"
export LLM_MODEL="gpt-5.5"  # optional
export LLM_BASE_URL="https://your-endpoint"  # optional

# Run example
python main.py
```

## Hook Types

| Hook | When it runs | Can block? |
|------|--------------|------------|
| PreToolUse | Before tool execution | Yes (exit 2 or JSON denial) |
| PostToolUse | After tool execution | No |
| UserPromptSubmit | Before processing user message | Yes (exit 2 or JSON denial) |
| Stop | When agent tries to finish | Yes (exit 2 or JSON denial) |
| SessionStart | When conversation starts | No |
| SessionEnd | When conversation ends | No |

## Exit Codes

For synchronous command hooks, the exit code belongs to the configured shell
command. The SDK records execution success separately from blocking:

| Exit code | `success` | Blocking decision |
| --- | --- | --- |
| `0` | `True` | A JSON denial can still block. |
| `2` | `False` | Blocks, even if JSON says to allow or continue. |
| Other non-zero, including `1` | `False` | Does not block by itself; a JSON denial can still block. |

The SDK attempts to parse stdout as JSON on every exit code. `"decision": "deny"`
or `"continue": false` blocks. Exit `1` with plain-text output is non-blocking;
exit `1` with `{"decision": "deny"}` blocks while still reporting `success=False`.

JSON `reason` and `additionalContext` remain available on exit `2`. Blocking
explanations prefer `reason`, then stderr, then a default message. For `Stop`
hooks, blocking prevents completion and lets the agent continue; feedback uses
`additionalContext` when present, otherwise the blocking reason.

For new hooks, use exit `0` with a JSON decision, or exit `2` with a reason on
stderr. A JSON allow cannot override exit `2`. Background hooks (`async: true`)
do not wait for a result and cannot block through their exit code or output.
