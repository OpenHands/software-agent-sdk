"""Issue #4032 — QA evidence for *the LLM timeout is reset after a restart*.

Everything here is real: a real ``agent-server`` process (SIGKILLed and
restarted on the same persistence dirs), a real mock LLM, a real agent run, and
real HTTP. Nothing is mocked or monkeypatched inside the server.

Run it yourself::

    uv run python .pr/timeout_restart_evidence.py noswitch
    uv run python .pr/timeout_restart_evidence.py switch

    # A/B against another checkout (e.g. main, to see the bug):
    uv run python .pr/timeout_restart_evidence.py switch --repo /path/to/main

Two scenarios:

``noswitch``
    A profile carries a non-default timeout and the conversation is created
    with it. This is what OpenHands does after ``POST /profiles/{name}/
    activate``, which copies the profile's LLM into ``agent_settings.llm``
    (``settings_models.py::switch_to_profile``) — so a profile timeout reaches
    a conversation with no switch anywhere. No ``switch_llm`` /
    ``switch_profile`` call is made at any point in this scenario.

``switch``
    The conversation is created with the default timeout and then moved onto
    the profile via ``POST /conversations/{id}/switch_llm`` — the path the
    OpenHands app-server uses for its profile picker.

The timeout is read three ways, because they disagree and that disagreement is
the most confusing part of the bug report:

cold read
    ``GET /api/conversations/{id}`` *before* the restored conversation is
    hydrated. Conversations hydrate lazily (#4100), so this is answered from
    ``base_state.json`` on disk (``_conversation_info`` ->
    ``_load_persisted_state_sync``) and still shows the *pre-restart* agent.
    It is a stale read, not the live agent.
warm read
    the same endpoint after the conversation has been hydrated, which is when
    ``ConversationState.create()`` actually runs and applies
    ``state.agent = agent`` from ``meta.json``.
behavioural
    the mock LLM stalls and we measure when the agent gives up. An LLM request
    timeout is client side, so this is the only way to see the timeout the
    agent is really applying.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx


HERE = Path(__file__).resolve().parent
DEFAULT_REPO = HERE.parent

# Deliberately tiny so a stalled request reveals the effective timeout in
# seconds rather than minutes. The SDK default (``LLM.timeout``) is 300.
PROFILE_TIMEOUT = 5
SDK_DEFAULT_TIMEOUT = 300

# How long we are willing to watch a stalled request before concluding the
# agent is not using PROFILE_TIMEOUT. Must sit well above PROFILE_TIMEOUT and
# well below SDK_DEFAULT_TIMEOUT so the two outcomes are unambiguous.
BEHAVIOUR_CAP = 45.0

# Any fixed value works; it only has to be stable across the restart, so the
# restored conversation can decrypt the api_key it persisted. Without it the
# server warns "OH_SECRET_KEY was not defined" and the restored conversation
# loses its api_key, failing for a reason unrelated to the timeout.
SECRET_KEY = "issue-4032-evidence-key-not-for-production"


def log(msg: str) -> None:
    print(f"\n>>> {msg}", flush=True)


def port_free(port: int) -> bool:
    """True when nothing is *listening* on the port.

    Uses connect rather than bind: a bind probe also trips on lingering
    TIME_WAIT sockets from a previous run, which are harmless.
    """
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def require_free(port: int) -> None:
    if not port_free(port):
        raise SystemExit(
            f"port {port} is already in use. A stale server there would "
            f"silently serve this run and invalidate it — kill it first."
        )


def wait_http(url: str, timeout: float = 180.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.get(url, timeout=2.0)
            return
        except httpx.HTTPError:
            time.sleep(0.3)
    raise SystemExit(f"timed out waiting for {url}")


def hard_kill(proc: subprocess.Popen[bytes] | None) -> None:
    """SIGKILL the whole process group.

    ``uv run`` spawns a child python; killing only ``uv`` leaves the server
    listening, which would let a stale process serve the next run.
    """
    if proc is None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def timeouts_in(path: Path) -> list[tuple[str, Any]]:
    """Every ``timeout`` value appearing anywhere in a JSON file, with paths."""
    found: list[tuple[str, Any]] = []

    def walk(node: Any, trail: str = "$") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "timeout":
                    found.append((f"{trail}.{key}", value))
                else:
                    walk(value, f"{trail}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{trail}[{index}]")

    walk(json.loads(path.read_text()))
    return found


class Harness:
    def __init__(self, mode: str, repo: Path, agent_port: int, mock_port: int):
        self.mode = mode
        self.repo = repo
        self.agent_port = agent_port
        self.mock_port = mock_port
        self.agent = f"http://127.0.0.1:{agent_port}"
        self.mock = f"http://127.0.0.1:{mock_port}"
        # Written outside the repo on purpose: a run produces whole
        # conversation dirs, and those are artifacts, not evidence to commit.
        self.root = Path(tempfile.gettempdir()) / f"oh-4032-evidence-{mode}-{repo.name}"
        self.server: subprocess.Popen[bytes] | None = None
        self.mock_proc: subprocess.Popen[bytes] | None = None

    # -- process control ---------------------------------------------------

    def start_agent_server(self, tag: str) -> None:
        require_free(self.agent_port)
        env = {
            **os.environ,
            "OH_CONVERSATIONS_PATH": str(self.root / "conversations"),
            "OH_WORKSPACE_PATH": str(self.root / "project"),
            "OH_BASH_EVENTS_DIR": str(self.root / "bash_events"),
            "OH_PERSISTENCE_DIR": str(self.root / "persistence"),
            "OH_ENABLE_VSCODE": "false",
            "OH_SECRET_KEY": SECRET_KEY,
        }
        logf = open(self.root / f"agent-server-{tag}.log", "w")
        self.server = subprocess.Popen(
            [
                "uv",
                "run",
                "python",
                "-m",
                "openhands.agent_server",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.agent_port),
            ],
            cwd=self.repo,
            env=env,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        wait_http(f"{self.agent}/health")
        log(f"agent-server ({tag}) up, pid={self.server.pid}  [{self.repo}]")

    def start_mock_llm(self) -> None:
        require_free(self.mock_port)
        logf = open(self.root / "mock-llm.log", "w")
        self.mock_proc = subprocess.Popen(
            [
                "uv",
                "run",
                "uvicorn",
                "mock_llm:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.mock_port),
                "--log-level",
                "warning",
            ],
            cwd=HERE,
            env={**os.environ, "PYTHONPATH": str(HERE)},
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        wait_http(f"{self.mock}/_calls")
        self.set_mock_mode("fast")

    # -- server interaction ------------------------------------------------

    def set_mock_mode(self, mode: str, hang_seconds: float = 600.0) -> None:
        httpx.post(
            f"{self.mock}/_control",
            json={"mode": mode, "hang_seconds": hang_seconds},
            timeout=10.0,
        )

    def mock_calls(self) -> int:
        return len(httpx.get(f"{self.mock}/_calls", timeout=10.0).json()["calls"])

    def read(self, cid: str) -> dict[str, Any]:
        response = httpx.get(f"{self.agent}/api/conversations/{cid}", timeout=30.0)
        response.raise_for_status()
        info = response.json()
        llm = info["agent"]["llm"]
        return {
            "timeout": llm.get("timeout"),
            "usage_id": llm.get("usage_id"),
            "status": info.get("execution_status"),
        }

    def send(self, cid: str, text: str) -> None:
        httpx.post(
            f"{self.agent}/api/conversations/{cid}/events",
            # run=True: SendMessageRequest.run defaults to False, and without
            # it the message is appended but the agent loop never runs.
            json={
                "role": "user",
                "content": [{"type": "text", "text": text}],
                "run": True,
            },
            timeout=120.0,
        )

    def talk(self, cid: str, text: str, cap: float = 90.0) -> str:
        before = self.mock_calls()
        self.send(cid, text)
        deadline = time.time() + cap
        while time.time() < deadline:
            time.sleep(0.3)
            status = self.read(cid)["status"]
            if self.mock_calls() > before and status != "running":
                return f"{status} (llm calls: {self.mock_calls() - before})"
        return f"TIMED_OUT (llm calls: {self.mock_calls() - before})"

    def behavioural_timeout(self, cid: str) -> tuple[float | None, int]:
        """Stall the LLM, return (seconds until the agent gave up, requests)."""
        self.set_mock_mode("hang")
        before = self.mock_calls()
        self.send(cid, "stall please")

        requested_at = None
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.mock_calls() > before:
                requested_at = time.time()
                break
            time.sleep(0.2)
        if requested_at is None:
            return None, 0

        deadline = requested_at + BEHAVIOUR_CAP
        while time.time() < deadline:
            time.sleep(0.4)
            if self.read(cid)["status"] != "running":
                return time.time() - requested_at, self.mock_calls() - before
        return None, self.mock_calls() - before

    def dump_disk(self, conv_dir: Path, label: str) -> None:
        log(f"on-disk {label}")
        for name in ("meta.json", "base_state.json"):
            path = conv_dir / name
            found = timeouts_in(path) if path.exists() else "<missing>"
            print(f"    {name}: {found}")

    # -- the scenario ------------------------------------------------------

    def run(self) -> int:
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        (self.root / "project").mkdir()

        require_free(self.agent_port)
        require_free(self.mock_port)
        log(f"mode={self.mode}  repo={self.repo}")
        print(f"    run artifacts and server logs: {self.root}")
        self.start_mock_llm()
        self.start_agent_server("phase1")

        profile_llm: dict[str, Any] = {
            "model": "openai/gpt-4o",
            "base_url": f"{self.mock}/v1",
            "api_key": "sk-mock",
            "usage_id": "profile-slow",
            "timeout": PROFILE_TIMEOUT,
            "num_retries": 0,
        }
        log(f"saving LLM profile 'slow' (timeout={PROFILE_TIMEOUT})")
        response = httpx.post(
            f"{self.agent}/api/profiles/slow",
            json={"llm": profile_llm, "include_secrets": True},
            timeout=30.0,
        )
        response.raise_for_status()

        if self.mode == "noswitch":
            creation_llm = {**profile_llm, "usage_id": "agent"}
        else:
            creation_llm = {k: v for k, v in profile_llm.items() if k != "timeout"}
            creation_llm["usage_id"] = "agent"

        log("creating conversation")
        response = httpx.post(
            f"{self.agent}/api/conversations",
            json={
                "agent": {"kind": "Agent", "llm": creation_llm, "tools": []},
                "workspace": {
                    "kind": "LocalWorkspace",
                    "working_dir": str(self.root / "project"),
                },
                "confirmation_policy": {"kind": "NeverConfirm"},
            },
            timeout=180.0,
        )
        response.raise_for_status()
        created = response.json()
        cid = created["id"]
        conv_dir = Path(created["persistence_dir"])
        print(f"    conversation {cid}")
        print(f"    at creation: {self.read(cid)}")

        if self.mode == "switch":
            log("POST /switch_llm — move the conversation onto the profile's LLM")
            response = httpx.post(
                f"{self.agent}/api/conversations/{cid}/switch_llm",
                json={"llm": profile_llm},
                timeout=60.0,
            )
            response.raise_for_status()
            print(f"    after switch: {self.read(cid)}")

        log("talking to the agent")
        print(f"    run ended: {self.talk(cid, 'hello')}")
        before_restart = self.read(cid)["timeout"]
        self.dump_disk(conv_dir, "BEFORE restart")

        log("SIGKILL the agent-server (container restart)")
        hard_kill(self.server)
        self.server = None
        time.sleep(1)

        self.start_agent_server("phase2")
        cold = self.read(cid)
        log(f"restored, COLD read (stale, from base_state.json): {cold}")
        # Touching any endpoint that needs the live EventService hydrates the
        # conversation, which is when ConversationState.create() really runs.
        httpx.get(
            f"{self.agent}/api/conversations/{cid}/events/search?limit=1",
            timeout=180.0,
        )
        warm = self.read(cid)
        log(f"restored, WARM read (live, hydrated agent):        {warm}")
        self.dump_disk(conv_dir, "AFTER restart + hydration")

        self.set_mock_mode("fast")
        log("behavioural probe — stall the LLM, watch when the agent gives up")
        seconds, calls = self.behavioural_timeout(cid)
        observed = (
            f"{seconds:.1f}s"
            if seconds is not None
            else f">{BEHAVIOUR_CAP:.0f}s (still waiting)"
        )
        print(f"    stalled requests seen by the mock: {calls}")
        print(f"    agent gave up after: {observed}")

        log("VERDICT")
        preserved = warm["timeout"] == PROFILE_TIMEOUT
        print(f"    mode                        : {self.mode}")
        print(f"    repo                        : {self.repo}")
        print(f"    configured profile timeout  : {PROFILE_TIMEOUT}")
        print(f"    declared BEFORE restart     : {before_restart}")
        print(f"    declared AFTER  restart COLD: {cold['timeout']}  (stale)")
        print(f"    declared AFTER  restart WARM: {warm['timeout']}  (live agent)")
        print(f"    behavioural AFTER restart   : {observed}")
        print(f"    => TIMEOUT PRESERVED ACROSS RESTART: {preserved}")
        if not preserved:
            print(
                f"    => REVERTED {before_restart} -> {warm['timeout']} "
                f"(the SDK default is {SDK_DEFAULT_TIMEOUT})"
            )
        return 0 if preserved else 1

    def cleanup(self) -> None:
        hard_kill(self.server)
        hard_kill(self.mock_proc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["noswitch", "switch"])
    parser.add_argument(
        "--repo",
        type=Path,
        default=DEFAULT_REPO,
        help="checkout to run the agent-server from (default: this repo)",
    )
    parser.add_argument("--agent-port", type=int, default=8898)
    parser.add_argument("--mock-port", type=int, default=8899)
    args = parser.parse_args()

    harness = Harness(args.mode, args.repo.resolve(), args.agent_port, args.mock_port)
    try:
        return harness.run()
    finally:
        harness.cleanup()


if __name__ == "__main__":
    sys.exit(main())
