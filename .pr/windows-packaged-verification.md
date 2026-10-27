# Windows terminal recovery verification

Verified on native Windows 11, Windows PowerShell 5.1, Python 3.13.11, and the repository's SDK/Agent Server 1.58.2 package configuration. Runtime source includes helper fix `11459660` and missing-interpreter metadata fix `c28f3657`.

## Before the corrections

- The builtin-interrupt regression failed with the original Python helper when `sys.executable` pointed to a server executable instead of a Python interpreter: PowerShell was marked unresponsive and the next command did not execute normally.
- Running the actual PyInstaller executable with the legacy helper's `-I -c` invocation was rejected by the server's argument parser as unrecognized arguments.
- The first real packaged smoke run, with Python removed from `PATH`, failed on its first command: PowerShell serialized the empty command-lookup pipeline as `{}` for `py_interpreter_path`, causing `CmdOutputMetadata` validation to fail. This was separately reproduced through the normal terminal API before applying the explicit `$null` normalization.

## After the corrections

The native terminal suite passes:

```text
uv run --frozen pytest tests/tools/terminal -q --tb=short --timeout=90
233 passed, 196 skipped in 86.73s
```

The builtin-interrupt test now covers both interpreter and server-executable values of `sys.executable`, with and without Python on the shell's `PATH`. It also verifies that the interrupted shell retains its environment.

All changed runtime/test files pass the repository's pre-commit checks, including Ruff, pycodestyle, Pyright, import rules, and tool registration.

The actual one-file executable was rebuilt using the repository's release spec:

```powershell
uv run --frozen pyinstaller --noconfirm openhands-agent-server/openhands/agent_server/agent-server.spec
$env:PATH = "$env:SystemRoot\System32;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
$env:OPENHANDS_SUPPRESS_BANNER = '1'
.\dist\openhands-agent-server.exe --extra-python-path .pr --import-modules packaged_terminal_smoke
```

The smoke module runs through the executable's supported custom-module import entry point and uses the bundled terminal implementation. It executes a terminating error, a successful command, a timed-out `Start-Sleep`, Ctrl+C, and another command, then closes its temporary shell. No LLM or conversation is created.

Observed result, executable exit code **0**:

```json
{
  "checks": [
    {"command": "throw 'expected-error'", "exit_code": 1},
    {"command": "Write-Output before-interrupt", "exit_code": 0}
  ],
  "executable": "openhands-agent-server.exe",
  "exit_code": 0,
  "frozen": true,
  "python_on_path": null,
  "recovered": true,
  "same_shell": true,
  "state_preserved": true
}
```

This validates the native Windows backend in the actual packaged process. It is not a new Agent Canvas/LLM benchmark run or a Linux/macOS test run. Upstream workflows that require fork-contributor approval still need a maintainer to enable them.
