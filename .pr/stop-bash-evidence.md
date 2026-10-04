# Stop BashCommand evidence (PR #5348, head `f7f84b4a`)

Authentic Linux runtime output for the supported Agent Server endpoint,
collected on the exact PR head after neubig's review
`pullrequestreview-5403366133`.

## Environment

- Head: `f7f84b4ad78f29a0542461f090978cadae7adcfd`
- Runtime: Docker Desktop Linux (`linux/amd64`, kernel
  `6.6.87.2-microsoft-standard-WSL2`), image
  `ghcr.io/astral-sh/uv:python3.13-bookworm`, Python 3.13.11
- Command: `uv sync --frozen`, then the pytest runs below with
  `UV_PROJECT_ENVIRONMENT=/tmp/pr-venv`

## 1. `tests/agent_server/test_bash_service.py -v -k stop_bash`

Result: `4 passed, 11 deselected in 8.36s` (platform linux).

```text
tests/agent_server/test_bash_service.py::test_stop_bash_command_terminates_group_and_records_output PASSED [ 25%]
tests/agent_server/test_bash_service.py::test_stop_bash_command_terminates_descendants PASSED [ 50%]
tests/agent_server/test_bash_service.py::test_stop_bash_command_leaves_concurrent_command_running PASSED [ 75%]
tests/agent_server/test_bash_service.py::test_stop_bash_command_idempotent_for_finished_and_unknown PASSED [100%]
```

## 2. `tests/cross/test_remote_conversation_live_server.py -v -k stop_bash`

Result: `1 passed, 22 deselected in 11.69s` (platform linux).

```text
tests/cross/test_remote_conversation_live_server.py::test_stop_bash_command_endpoint_with_live_server PASSED [100%]
```

## 3. Verbose endpoint trace (`.pr/stop-bash-evidence.py`)

In-process FastAPI app over `httpx.ASGITransport`, same router/service
wiring as `tests/agent_server/test_bash_service.py`. Command ids are
random per run; values below are from this run.

```text
COMMAND: trap 'touch /tmp/stop-evidence-lhf5b0t0/stop_cleanup_ran; exit 0' TERM; touch /tmp/stop-evidence-lhf5b0t0/shell_ready; sleep 30
START: status=200 body={"command":"trap 'touch /tmp/stop-evidence-lhf5b0t0/stop_cleanup_ran; exit 0' TERM; touch /tmp/stop-evidence-lhf5b0t0/shell_ready; sleep 30","cwd":null,"timeout":60,"id":"97059d90d96e43ca93242d5742c2b3d6","timestamp":"2026-10-04T10:12:52.573673Z","kind":"BashCommand"}
COMMAND_ID: 97059d90d96e43ca93242d5742c2b3d6
SHELL_READY: TERM trap installed
STOP: status=200 body={"success":true}
EVENT_STREAM: 1 events
TERMINAL_OUTPUT: exit_code=0 stdout=None stderr='Terminated\n'
CLEANUP_MARKER: exists=True path=/tmp/stop-evidence-lhf5b0t0/stop_cleanup_ran
REPEAT_STOP_FINISHED: status=200
UNKNOWN_STOP: status=404
SEARCH_AFTER_STOP: status=200
EVIDENCE_OK
```

What this shows:

- Start request: `POST /api/bash/start_bash_command` with a 30s sleep
  guarded by a `TERM` trap, returns 200 with the command id.
- Stop request: `POST /api/bash/bash_commands/{id}/stop` returns 200
  `{"success":true}`.
- Event stream: `GET /api/bash/bash_events/search?command_id__eq={id}`
  yields a terminal `BashOutput` with `exit_code=0` (trap ran `exit 0`;
  `stderr='Terminated'` is the shell's SIGTERM notice).
- Cleanup: the trap's marker file exists, proving the SIGTERM-then-SIGKILL
  escalation ran user cleanup instead of a bare kill.
- Idempotency: repeat stop on the finished id returns 200, unknown id
  returns 404 (client treats 404 as success).

## 4. Supporting CI (same head)

- `gh run view 36343159496 --repo OpenHands/software-agent-sdk`
  (`fix/stop-bash-command-5257`): `agent-server-tests` success (4 stop
  tests PASSED in log), `cross-tests` success (`492 passed, 1 skipped`,
  including `test_stop_bash_command_endpoint_with_live_server PASSED`).
