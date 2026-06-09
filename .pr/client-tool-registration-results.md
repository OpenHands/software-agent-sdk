# Client tool registration real-world check

This temporary PR artifact records a reproduction-style check for issue #3604.

Command:

```bash
OPENHANDS_SUPPRESS_BANNER=1 uv run python - <<'PY'
from openhands.sdk.tool.client_tool import (
    ClientToolRegistrationError,
    ClientToolSpec,
    register_client_tools,
)
from openhands.tools.terminal import TerminalTool

try:
    register_client_tools([
        ClientToolSpec(name=TerminalTool.name, description="client terminal")
    ])
except ClientToolRegistrationError as exc:
    print(f"builtin collision rejected: {type(exc).__name__}: {exc}")
else:
    raise SystemExit("builtin collision unexpectedly succeeded")

try:
    register_client_tools([
        ClientToolSpec(name="client_duplicate", description="one"),
        ClientToolSpec(name="client_duplicate", description="one"),
    ])
except ClientToolRegistrationError as exc:
    print(f"duplicate request rejected: {type(exc).__name__}: {exc}")
else:
    raise SystemExit("duplicate request unexpectedly succeeded")

spec = ClientToolSpec(name="client_idempotent_3604", description="idempotent")
first = register_client_tools([spec])
second = register_client_tools([spec])
print(f"idempotent client registration counts: {len(first)}, {len(second)}")
print(f"idempotent client tool names: {[tool.name for tool in second]}")
PY
```

Output:

```text
builtin collision rejected: ClientToolRegistrationError: Client tool name 'terminal' collides with an existing non-client tool. Choose a unique client tool name.
duplicate request rejected: ClientToolRegistrationError: Duplicate client tool name 'client_duplicate' in one registration request. Client tool names must be unique.
idempotent client registration counts: 1, 1
idempotent client tool names: ['client_idempotent_3604']
```
