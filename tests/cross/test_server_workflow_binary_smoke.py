"""Guards for the agent-server binary smoke test in ``server.yml``.

These assertions are deliberately textual: the harness is bash that only runs
inside GitHub Actions, so the invariant that can drift and silently reintroduce
a flaky Windows job is the shape of the script, not any Python behavior.
"""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "server.yml"


def _smoke_step() -> str:
    """Return the body of the ``--extra-python-path`` smoke-test step."""
    workflow = SERVER_WORKFLOW.read_text(encoding="utf-8")
    marker = "- name: Test --extra-python-path custom tool import"
    assert marker in workflow, f"{SERVER_WORKFLOW}: smoke-test step is missing"
    step = workflow.partition(marker)[2]
    # Stop at the next step so assertions cannot match unrelated script text.
    return step.partition("- name:")[0]


def test_startup_wait_is_not_a_tight_fixed_cap() -> None:
    """A PyInstaller onefile binary unpacks itself on first run; on a cold or
    loaded Windows runner that alone exceeded the old 45s cap, so a healthy
    server was reported as a failure. The wait must be both generous and
    overridable rather than a hand-tuned constant.
    """
    step = _smoke_step()

    assert "BINARY_STARTUP_TIMEOUT_SECONDS" in step
    assert 'STARTUP_TIMEOUT_SECONDS="${BINARY_STARTUP_TIMEOUT_SECONDS:-' in step
    default = int(step.split("BINARY_STARTUP_TIMEOUT_SECONDS:-", 1)[1].split("}")[0])
    assert default >= 300, (
        "default startup timeout must tolerate slow first-run extraction; "
        f"got {default}s"
    )
    assert "${3:-45}" not in step, "the old fixed 45s cap should be gone"


def test_wait_for_log_takes_the_pid_and_fails_fast() -> None:
    """Waiting the full timeout after the process already died just burns
    runner minutes and hides the real cause, so the wait must be able to
    observe liveness and say which condition it hit.
    """
    step = _smoke_step()

    assert "local pid=$3" in step
    assert 'kill -0 "$pid"' in step
    assert "exited before" in step
    assert "timed out after" in step
    for pid_var in ("NEG_PID", "POS_PID", "CLI_PID"):
        assert f'"${pid_var}"' in step, f"wait_for_log should be passed {pid_var}"


def test_failed_wait_dumps_the_log_even_when_empty() -> None:
    """`cat` on an empty log printed nothing, which made a slow-startup
    failure look like a missing-log bug in the CI output.
    """
    step = _smoke_step()

    assert "dump_log()" in step
    assert "(no output captured from the binary)" in step
    assert not any(
        line.strip().startswith("cat ") and "_test.log" in line
        for line in step.splitlines()
    ), "failure paths should call dump_log rather than a bare cat"


def test_windows_process_teardown_kills_the_whole_tree() -> None:
    """The onefile bootloader runs the server in a child process, so killing
    only the bootloader can leave that child holding the port. Windows must
    therefore terminate the tree, and the call must not let MSYS rewrite the
    `/F /T /PID` switches into paths.
    """
    step = _smoke_step()

    assert "MSYS_NO_PATHCONV=1 taskkill /F /T /PID" in step
    # The tree kill has to stay scoped to Windows; Unix runners have no
    # taskkill and should keep the plain signal path.
    end_of_block = "\n                  }"
    teardown = step.partition("stop_process() {")[2].partition(end_of_block)[0]
    assert 'RUNNER_OS:-}" == "Windows"' in teardown
    assert 'kill "$pid"' in teardown
