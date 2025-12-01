# Terminal Tool

The Terminal Tool provides a persistent shell session for executing bash commands within the OpenHands SDK.

## Features

- **Persistent session**: Environment variables, virtual environments, and working directory persist between commands
- **Multiple backend support**: Auto-detects and uses tmux when available, falls back to subprocess-based PTY
- **Configurable shell**: Support for custom shell binaries (useful on Nix, macOS, or custom environments)
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
- On reset, the terminal session will preserve the originally configured shell path
