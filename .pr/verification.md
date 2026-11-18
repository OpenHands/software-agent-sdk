# Metadata takeover verification

Issue: https://github.com/OpenHands/software-agent-sdk/issues/5629

Verified 2026-10-13 UTC on macOS arm64, Python 3.12.13, SDK/server 1.54.0.
Baseline: `4011cb5d4fd508964fe210df9a50af7d2f19d29d` in a separate worktree
with its own frozen workspace environment. Fix observations used this branch's
working tree; the two product-file SHA-256 hashes are recorded in
[metadata-evidence.json](metadata-evidence.json).

## Real server results

| Revision | Owner exit | Fresh attempts | Disk title after takeover | GET title | Result |
|---|---|---|---|---|---|
| Base | Graceful | 2 | `before B` | `before B` | Reproduced twice |
| Base | SIGKILL | 2 | `before B` | `before B` | Reproduced twice |
| Fix | Graceful | 2 | `after B` | `after B` | Passed twice |
| Fix | SIGKILL | 2 | `after B` | `after B` | Passed twice |

Each attempt launches two authenticated server processes through
`control-agent-server`, doctors both, creates a conversation without running
the agent, starts the observer before changing the owner's title and tags,
then exits the owner. A POST to `/api/conversations/{id}/pause` on the observer
forces a real runtime load. The recorded lease names the observer PID with
generation 1 after graceful release or generation 2 after SIGKILL. The shared
disk and the observer's GET are then checked independently. Every run is stopped
and its port closure verified.

The fix also preserves the latest on-disk tags and `updated_at`. The pause can
advance the in-memory GET timestamp, which is checked not to move backwards;
the persisted metadata timestamp must equal the owner's last timestamp.

```sh
bash .pr/reproduce_metadata.sh /path/to/base-checkout fail
bash .pr/reproduce_metadata.sh "$PWD" pass
```

The script uses the existing verification CLI exclusively for server operations.
There are no model requests, mocked server responses, external accounts or tmux
dependencies. Compact API/state observations, run IDs and lease generations are
in [metadata-evidence.json](metadata-evidence.json). Raw exchanges and ledgers
remain in each run's `evidence/` directory on the verification machine.

## Regression protection and checks

- The same three new regression cases fail on base and pass on the fix. They
  exercise real ConversationService/EventService loading, a live-owner refusal,
  an encrypted stored secret, and corrupt/missing metadata with lease cleanup
  followed by a successful retry after repair.
- Full `tests/agent_server -n 4`: base **2,569 passed, 14 failed**; fix
  **2,572 passed, the same 14 failed**. No newly failing test IDs. The unchanged
  failures are 11 unsupported Canvas backend cases on macOS, one symlink-cycle
  case on macOS/Python 3.12 and two cases requiring the unavailable tmux binary.
  Their IDs are included in the JSON evidence. This is not an all-green suite.
- All applicable pre-commit checks pass: Ruff format/lint, pycodestyle, Pyright,
  dynamic-attribute, import and tool-registration checks. Shell syntax and
  `git diff --check` pass.
- `control-agent-server map check`: 34 files, 935 IDs, no problems.

## Scope and limitations

The original issue's `events/count` reproduction no longer loads a runtime on
this main revision: persisted event reads are lease-free. The F31 recipe now
uses pause to actually exercise takeover and removes the fixed bug markers.
Its full map-run wrapper could not pass this machine's unrelated DeepSeek/tmux
preconditions; the tracked credential-free reproduction executes the affected
workflow directly, twice per revision and exit mode.

New conversations and forks keep the creating request's metadata. Resume-supplied
credential fallback is applied after metadata refresh and still obeys the
persisted profile restrictions; existing fork and credential-resume tests pass.
Encrypted-secret preservation is tested with real in-process services and a
shared cipher, not with encrypted secrets in the two-process live recipe.
No model behavior, Docker/cloud launch, full SDK/tools suite or benchmark was
claimed as verified. No stored schema, public SDK/REST contract, or lease
semantics change is introduced.
