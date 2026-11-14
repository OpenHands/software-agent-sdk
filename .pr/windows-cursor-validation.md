# Native Windows / Cursor evidence for #5996

The observations in `windows-cursor-validation.json` came from Windows 11
10.0.26200.9445, native PowerShell 5.1, Python 3.12.13, and the reported Cursor
CLI version **2026.10.14-941492e**. Every case used
`composer-2.5[fast=true]`, the same project workspace, and the same project-only
`.cursor/agents/auto-commit.md` definition. Its random response identifier was
not included in the parent's request.

The entry point was the real public `Conversation` / `ACPAgent`, followed by an
absolute PowerShell executable and the official Windows `agent.cmd acp`
launcher. Both the launcher directory and workspace contained spaces. A small
PowerShell prefix recorded its own startup cwd; it changed directory only in
the explicit base-version workaround control. No SDK method or transport was
mocked or replaced. Source-file hashes were checked against the archived Git
revisions.

| Case | Observed child startup cwd | Actual custom Task | Configured child identifier |
| --- | --- | --- | --- |
| Base `1fbfd37`, normal launch | Parent directory | Rejected; only built-in subagent types accepted | Absent |
| Same base, explicit `Set-Location` workaround | Project workspace | Completed | Returned |
| Fixed source `9337afd`, normal launch | Project workspace | Completed | Returned |
| Same fixed conversation, after close and resume | Project workspace | Completed; original ACP session restored | Returned |

The base tool error was `Invalid arguments` / `subagent_type: Invalid option`,
listing `generalPurpose`, `cursor-guide`, `ci-investigator`, and
`best-of-n-runner`. This is the missing-custom-agent rejection; its wording is
not byte-for-byte the issue's older `Invalid enum value` example.

The successful Task notifications include
`raw_input.subagentType.custom.name == "auto-commit"`, terminal `completed`
status, and the provider's `durationMs`. The parent then returns the identifier
from that child's definition. The initial fixed Task reported 6150 ms.
The new and resumed ACP session fingerprints are identical.

## Shutdown limitation observed on both revisions

SDK close terminated the direct PowerShell subprocess but left descendants
running on both base and fixed source. The normal base and fixed new/resumed
cases each had three live descendants after the probe's ten-second grace
period; the workaround control had seven. Python also printed Proactor pipe
finalizer warnings. **This evidence does not claim that Windows process-tree
shutdown passes or is fixed by the cwd change.**

The probe records those failures before its fallback cleanup, then stops only
the process trees it captured for its own launch. A final independent process
scan found zero remaining verification processes. Resume was checked after
that fallback cleanup, rather than allowing the previous CLI process to remain
running. The descendant-shutdown problem should be handled separately from
this bounded cwd fix.

The native run exercised the public SDK execution path, not the Agent Canvas
UI. Other-provider Windows generation was not tested. Existing macOS provider
validation limits still apply. The unchanged SDK dependency lock was used;
LiteLLM 1.93.0 required a local Rust/MSVC build because its published package
has no Windows wheel.

## Reproduction layout

Copy `verify_cursor_windows.py` into an isolated Windows test root containing:

- `sdk-base/`: the repository archived from `1fbfd37`;
- `sdk-fixed/`: this PR's source tree;
- a Python environment installed from the base's frozen SDK dependencies.

Use a normal Cursor account login on that machine. Do not copy credentials into
this directory. The recorded CLI archive was downloaded from the official
vendor URL:
`https://downloads.cursor.com/lab/2026.10.14-941492e/windows/x64/agent-cli-package.zip`.
Extract its `dist-package` directory. As in the official installer, copy its
`cursor-agent.cmd` to `agent.cmd` in the same directory. Verify the version and
login status. The CLI's normal user installation can remain on its current
version.

In PowerShell, with `$probe` set to that test root and `$python` set to the
prepared environment's Python executable:

```powershell
$env:PR_CURSOR_AGENT = "$probe\Reported Cursor With Spaces\dist-package\agent.cmd"
$env:PR_CURSOR_VERSION = '2026.10.14-941492e'
$env:OPENHANDS_SUPPRESS_BANNER = '1'

$env:PYTHONPATH = "$probe\sdk-base\openhands-sdk"
& $python "$probe\verify_cursor_windows.py" base --phases new
& $python "$probe\verify_cursor_windows.py" base --phases new --control-cwd

$env:PYTHONPATH = "$probe\sdk-fixed\openhands-sdk"
& $python "$probe\verify_cursor_windows.py" fixed
```

Run the cases sequentially. The probe creates only its own test workspace and
persistence directories, records events and results, and performs fallback
cleanup for its captured process trees. Read the JSON fields; completing the
probe is not a blanket pass for the shutdown criterion. `cleanup_passed: false`
is intentionally preserved.

The checked-in script was also syntax checked, linted, formatted, and checked
with Pyright's Windows platform setting. On the fixed production source,
528 ACP regression tests pass locally on macOS/Python 3.12.14 in 11.84 seconds.
