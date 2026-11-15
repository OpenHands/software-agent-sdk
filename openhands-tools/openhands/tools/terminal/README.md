# Terminal Tool

The Terminal Tool provides a persistent shell session for executing bash commands within the OpenHands SDK.

## Features

- **Persistent session**: Environment variables, virtual environments, and working directory persist between commands
- **Multiple backend support**: Auto-detects and uses tmux when available, falls back to subprocess-based PTY
- **Configurable shell**: Support for custom shell binaries (useful on Nix, macOS, or custom environments)
- **Client-configured environment**: Add SDK-provided session environment variables without exposing them as tool-call parameters
- **Long-running command support**: Handle commands with soft timeouts and interrupt capabilities
- **Terminal reset**: Ability to reset the terminal session if it becomes unresponsive

## Shell Configuration

By default, the terminal tool auto-detects bash from your PATH (like `#!/usr/bin/env bash`). You can optionally provide an explicit shell path:

### Using the `shell_path` parameter

```python
from openhands.sdk import Conversation
from openhands.tools.terminal.definition import TerminalTool

# Create conversation
conversation = Conversation()

# Create terminal with custom shell path
tools = TerminalTool.create(
    conv_state=conversation.state,
    terminal_type="subprocess",
    shell_path="/usr/local/bin/bash"
)
```

### Auto-detection (default)

If no explicit `shell_path` is provided, the tool automatically finds bash in your PATH using the equivalent of `which bash`. This works like `#!/usr/bin/env bash` and is portable across different systems.

If bash cannot be found in PATH, the tool will raise a clear error asking you to provide an explicit `shell_path`.

## Environment Configuration

SDK clients can provide extra environment variables when creating the terminal
tool. These values are merged with the sanitized process environment and apply to
the terminal session until it is reset or closed. They are not part of
`TerminalAction` and are not exposed in the model-facing tool schema.

```python
from openhands.sdk import Conversation
from openhands.tools.terminal.definition import TerminalTool

conversation = Conversation()
tools = TerminalTool.create(
    conv_state=conversation.state,
    env={
        "UV_INDEX_URL": "https://example.internal/simple",
        "UV_CACHE_DIR": "/workspace/.cache/uv",
    },
)
```

Raw secret values passed through `env` become session-scoped environment
variables. Explicitly naming a registry secret in a command also exports it into
the persistent session. Neither mechanism isolates credentials from later commands.

## Command credentials

Automatic authentication is opt-in, independently for each consumer and exact host:

```python
from openhands.sdk import TerminalCredentialBinding

bindings = [
    TerminalCredentialBinding(
        consumer="git", host="github.com", username="x-access-token",
        secret_ref="WORK_PAT",
    ),
    TerminalCredentialBinding(
        consumer="gh", host="github.com", secret_ref="WORK_PAT",
    ),
]
# Pass terminal_credential_bindings=bindings and secrets={"WORK_PAT": ...}
# to Conversation, or configure defaults in ConversationSettings.
```

`git`, `gh`, and `glab` are separate consumers. Git requires an explicit HTTPS
username (for example `x-access-token` for GitHub or `oauth2` for GitLab).
Hosts are exact, case-normalized names with an optional port; URLs, paths and
wildcards are not accepted. Secret names do not implicitly enable authentication.
Agent-profile secret restrictions still apply. Bindings do not grant access to
secrets absent from the conversation registry.

The native terminal installs private PATH adapters for bound consumers. Each
adapter launches the executable resolved from the initial configured PATH, not a
replacement selected by a later shell PATH change. Git receives an appended,
HTTPS-host-checked credential helper; existing helpers and `GIT_CONFIG_COUNT`
entries are preserved. `gh` and `glab` receive only the selected token in their
child environment. Tokens are resolved on demand and tracked by the existing
output masker; they are not written to adapter files, Git configuration, remote
URLs, or the persistent shell environment. Reset and close revoke the adapter's
local broker and remove its private files.

CLI target selection supports explicit host/repository options, HTTPS URLs,
CLI host/repository environment variables, and unambiguous repository remotes.
Conflicting hosts and unsupported command families fail closed. The supported
families are `api`, `pr`, `mr`, `issue`, `repo`, `release`, `workflow`, `run`, and
`search`; help/version run without injected credentials. Absolute executable paths
and commands that replace PATH bypass these adapters and receive no automatic
credentials. Existing CLI authentication remains separate.

These are same-user credential-delivery controls, **not a sandbox**: the CLI and
its children receive the token, and code with access to the private broker
capability can request a bound credential. Masking prevents accidental transcript
exposure, not deliberate exfiltration. Explicit name-based secret injection
remains available and retains its existing, broader session-scoped behavior.
This feature configures native terminal tools, not ACP harnesses, interactive
server terminals, or initial repository cloning.

Terminal processes run below the agent server's CPU priority so CPU-heavy
commands cannot starve it: `taskpolicy -c utility` on macOS, `nice -n 10` on
Linux and Below Normal priority on Windows. Set
`OH_TERMINAL_PROCESS_PRIORITY=none` in the server or terminal environment to
keep the server's priority, e.g. for benchmarks.

## Usage Examples

### Basic Usage

```python
from openhands.sdk import Conversation
from openhands.tools.terminal.definition import TerminalTool, TerminalAction

conversation = Conversation()
tools = TerminalTool.create(conv_state=conversation.state)
terminal = tools[0]

# Execute a command
action = TerminalAction(command="echo 'Hello, World!'")
result = terminal.executor(action)
print(result.text)
```

**Note:** `TerminalAction` and `TerminalObservation` replace the deprecated `ExecuteBashAction` and `ExecuteBashObservation` (which will be removed in version 1.5.0).

### With Custom Shell on Nix/macOS

```python
import shutil
from openhands.sdk import Conversation
from openhands.tools.terminal.definition import TerminalTool

conversation = Conversation()

# Explicitly specify bash path (useful if bash is in a non-standard location)
bash_path = shutil.which("bash")
if not bash_path:
    raise RuntimeError("bash not found in PATH")

tools = TerminalTool.create(
    conv_state=conversation.state,
    terminal_type="subprocess",
    shell_path=bash_path
)
```

## Terminal Types

The tool supports two backend types:

- **tmux**: Uses tmux for terminal session management (preferred when available)
- **subprocess**: Uses Python subprocess with PTY for terminal emulation (fallback)

You can force a specific type using the `terminal_type` parameter:

```python
tools = TerminalTool.create(
    conv_state=conversation.state,
    terminal_type="subprocess"  # or "tmux"
)
```

## Advanced Configuration

### Custom timeout

```python
tools = TerminalTool.create(
    conv_state=conversation.state,
    no_change_timeout_seconds=60  # Wait 60 seconds instead of default 10
)
```

### Username

```python
tools = TerminalTool.create(
    conv_state=conversation.state,
    username="myuser"
)
```

## Troubleshooting

### Bash Not Found in PATH

If you see an error like:
```
RuntimeError: Could not find bash in PATH
```

This means bash is not available in your system's PATH. Solutions:

1. Ensure bash is installed and in your PATH:
   ```bash
   which bash  # Should return a path like /usr/bin/bash
   ```

2. If bash is installed but not in PATH, pass the explicit path when creating the tool:
   ```python
   tools = TerminalTool.create(
       conv_state=conversation.state,
       shell_path="/usr/local/bin/bash"
   )
   ```

### Shell Not Executable Error

If you see:
```
RuntimeError: Shell binary is not executable: /path/to/bash
```

Check the file permissions:
```bash
ls -l /path/to/bash
chmod +x /path/to/bash  # If needed
```

## Notes

- The `shell_path` configuration only affects the subprocess terminal type; tmux terminals will use whatever shell tmux is configured to use
- The shell must be bash-compatible for proper operation
- On reset, the terminal session will preserve the originally configured shell path and client environment
