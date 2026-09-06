import os
import socket
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from pydantic import Field, PrivateAttr

from openhands.sdk.tool import Action, Observation, ToolExecutor
from openhands.sdk.utils.command import execute_command
from openhands.sdk.workspace import RemoteWorkspace


class RemoteExecutionToolExecutor(ToolExecutor):
    def __init__(self, workspace: "DockerExecutionWorkspace", tool_name: str):
        self.workspace = workspace
        self.tool_name = tool_name

    def __call__(self, action: Action, _conversation=None) -> Observation:
        response = self.workspace.client.post(
            "/api/execution/tools",
            json={
                "tool_name": self.tool_name,
                "action": action.model_dump(mode="json", exclude_none=True),
            },
        )
        response.raise_for_status()
        return Observation.model_validate(response.json()["observation"])


class DockerExecutionWorkspace(RemoteWorkspace):
    """Per-conversation Docker sandbox that hosts tool execution only."""

    working_dir: str = "/workspace"
    host: str = ""
    image: str = "ghcr.io/openhands/agent-server:latest-python"
    platform: str = "linux/amd64"
    api_key: str | None = Field(default=None, exclude=True)

    volumes: list[str] = Field(default_factory=list)
    health_check_timeout: float = 120.0

    _container_id: str | None = PrivateAttr(default=None)

    @property
    def runs_conversation_remotely(self) -> bool:
        return False

    def model_post_init(self, context: Any) -> None:
        super().model_post_init(context)

    def _ensure_started(self) -> None:
        if self._container_id is not None:
            return
        port = self._available_port()
        sandbox_api_key = uuid.uuid4().hex
        env_file = self._write_env_file(sandbox_api_key)
        command = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--platform",
            self.platform,
            "--name",
            f"openhands-execution-{uuid.uuid4()}",
            "-p",
            f"127.0.0.1:{port}:8000",
            "--env-file",
            str(env_file),
        ]
        for volume in self.volumes:
            command.extend(["-v", volume])
        command.extend([self.image, "--host", "0.0.0.0", "--port", "8000"])
        try:
            result = execute_command(command)
        finally:
            env_file.unlink(missing_ok=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start execution sandbox: {result.stderr}")
        self._container_id = result.stdout.strip()
        object.__setattr__(self, "host", f"http://127.0.0.1:{port}")
        object.__setattr__(self, "api_key", sandbox_api_key)
        self.reset_client()
        self._wait_for_health()

    @staticmethod
    def _write_env_file(sandbox_api_key: str) -> Path:
        fd, path = tempfile.mkstemp(prefix="openhands-execution-", text=True)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write("OH_EXECUTION_ONLY=true\n")
            stream.write(f"OH_SESSION_API_KEYS_0={sandbox_api_key}\n")
        return Path(path)

    @staticmethod
    def _available_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _wait_for_health(self) -> None:
        deadline = time.monotonic() + self.health_check_timeout
        while time.monotonic() < deadline:
            try:
                request = Request(
                    f"{self.host}/health",
                    headers={"X-Session-API-Key": self.api_key or ""},
                )
                with urlopen(request, timeout=1) as response:
                    if response.status == 200:
                        return
            except Exception:
                time.sleep(0.25)
        self.close()
        raise RuntimeError("Execution sandbox did not become healthy")

    def create_tool_executor(self, tool_name: str) -> ToolExecutor | None:
        if tool_name not in {"terminal", "file_editor", "grep", "glob", "apply_patch"}:
            return None
        self._ensure_started()
        return RemoteExecutionToolExecutor(self, tool_name)

    def close(self) -> None:
        self.reset_client()
        if self._container_id is not None:
            execute_command(["docker", "stop", self._container_id])
            self._container_id = None

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._send_completion_callback(exc_type, exc_val)
        self.close()
