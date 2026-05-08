# Hooks Examples

This folder demonstrates the OpenHands hooks system.

## Example

- **33_hooks.py** - Complete hooks demo showing all four hook types

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
export LLM_MODEL="anthropic/claude-sonnet-4-5-20250929"  # optional
export LLM_BASE_URL="https://your-endpoint"  # optional

# Run example
python 33_hooks.py
```

## Hook Types

| Hook | When it runs | Can block? |
|------|--------------|------------|
| PreToolUse | Before tool execution | Yes (exit 2) |
| PostToolUse | After tool execution | No |
| UserPromptSubmit | Before processing user message | Yes (exit 2) |
| Stop | When agent tries to finish | Yes (exit 2) |
| SessionStart | When conversation starts | No |
| SessionEnd | When conversation ends | No |

## Exit Codes

Hook scripts signal their result via the exit code (matching the Claude Code
hook contract):

- **`0` — success.** The operation proceeds. `stdout` is parsed as JSON for
  structured output (`decision`, `reason`, `additionalContext`).
- **`2` — block.** The operation is denied. For `Stop` hooks, this prevents
  the agent from finishing and the agent continues running. `stderr` /
  `reason` is surfaced as feedback.
- **Any other non-zero exit code — non-blocking error.** The error is
  logged, but the operation still proceeds.

> **Note:** Only exit code `2` blocks. Exit code `1` (the conventional Unix
> failure code) is treated as a non-blocking error. A hook that is meant to
> enforce a policy must exit with `2`.
