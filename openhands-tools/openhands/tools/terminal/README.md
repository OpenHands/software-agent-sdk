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

Credential delivery is opt-in for any executable basename. Each binding names a
registry secret and a delivery adapter; it does not constrain how the program
uses the credential.

```python
from openhands.sdk import TerminalCredentialBinding

bindings = [
    TerminalCredentialBinding(
        consumer="my-company-cli", env_var="API_KEY", secret_ref="WORK_API_KEY",
    ),
    TerminalCredentialBinding(
        consumer="gh", env_var="GH_TOKEN", secret_ref="WORK_PAT",
    ),
    TerminalCredentialBinding(
        consumer="glab", env_var="GITLAB_TOKEN", secret_ref="WORK_GITLAB_PAT",
    ),
    TerminalCredentialBinding(
        consumer="git", adapter="git_helper", host="github.com",
        username="x-access-token", secret_ref="WORK_PAT",
    ),
]
# Pass terminal_credential_bindings=bindings and the corresponding secrets
# to Conversation, or configure defaults in ConversationSettings.
```

The default `environment` adapter maps a secret to the caller-selected `env_var`
in the executable's child environment. Multiple bindings can supply multiple
variables to one program, such as AWS access-key and secret-key variables.
The bound value overrides an inherited value of the same variable; unrelated
inherited environment variables are unchanged. If a bound secret cannot be
resolved, the adapter does not launch the program. CLI arguments are passed
through unchanged: there are no provider-specific command allowlists, host
inference, or destination restrictions. Configure the program's own host and
account selection normally. `gh` and `glab` use this same generic adapter.

The `git_helper` adapter implements Git's HTTPS credential protocol. Its `host`
and `username` select which credential to return to a helper request; this is
credential selection, not a restriction on where a recipient can use the value.
Hosts are exact, case-normalized names with an optional port. Existing helpers
and `GIT_CONFIG_COUNT` entries are preserved. The adapter can also target a
renamed executable that implements Git's protocol; it does not key off the
consumer's name.

The native terminal installs private PATH wrappers for bound consumers, resolving
the executable from the initial configured PATH. Credentials are fetched on
demand from the conversation registry and tracked by the existing output masker.
Values are not written to wrapper files or the persistent shell environment.
The broker capability is stored in a private temporary directory and is itself
sensitive. Reset and close revoke the broker and remove those files. Agent-profile
secret restrictions still apply; binding a name does not make an unavailable
registry secret accessible. Resume, fork and task delegation preserve bindings.

This is **not a security sandbox**. A program and its descendants can read and
use their credentials, and same-user code can access the private broker
capability. A program may itself persist or transmit a credential. Masking is
not protection against deliberate exfiltration. Explicit name-based injection
remains available and retains its existing session-scoped behavior.

Absolute executable paths, shell functions/builtins, and replacing PATH can bypass
these wrappers and receive no automatic bound credentials. The feature configures
native terminal tools, not ACP harnesses, interactive server terminals, or initial
repository cloning.

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
