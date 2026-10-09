"""Podman-based remote workspace implementation."""

import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from typing import Any
from urllib.request import urlopen

from pydantic import Field, PrivateAttr

from openhands.sdk.logger import get_logger
from openhands.sdk.utils.command import execute_command, sanitized_env
from openhands.sdk.workspace import RemoteWorkspace
from openhands.workspace.docker.workspace import (
    check_port_available,
    find_available_tcp_port,
)


logger = get_logger(__name__)


class PodmanWorkspace(RemoteWorkspace):
    """Remote workspace that runs the agent server in a rootless Podman container.

    Podman runs containers without a daemon or root privileges. The container
    runs a pre-built agent server image in its own user, mount, PID, and
    network namespaces, and its port is published on the host loopback
    interface only. Host paths are visible only if listed in ``volumes``.

    Podman must be set up for rootless use, with subordinate UID and GID ranges
    for the current user in ``/etc/subuid`` and ``/etc/subgid``.

    Example:
        with PodmanWorkspace(
            volumes=["/path/to/project:/workspace/project"],
            working_dir="/workspace/project",
        ) as workspace:
            result = workspace.execute_command("ls -la")
    """

    # Override parent fields with defaults
    working_dir: str = Field(
        default="/workspace",
        description="Working directory inside the container.",
    )
    host: str = Field(
        default="",
        description="Remote host URL (set automatically during container startup).",
    )

    # Podman-specific configuration
    server_image: str = Field(
        default="ghcr.io/openhands/agent-server:latest-python",
        description="Pre-built agent server image. Pulled if not present locally.",
    )
    host_port: int | None = Field(
        default=None,
        description=(
            "Host loopback port published for the agent server. If None, finds "
            "an available port."
        ),
    )
    forward_env: list[str] = Field(
        default_factory=lambda: ["DEBUG", "SESSION_API_KEY", "OH_SESSION_API_KEYS_0"],
        description=(
            "Host environment variables forwarded to the container. Only the "
            "names appear on the podman command line; podman reads the values "
            "from its environment."
        ),
    )
    volumes: list[str] = Field(
        default_factory=list,
        description=(
            "Volume specs passed to 'podman run --volume', e.g. "
            "'/host/project:/workspace/project'. On SELinux hosts, append ':Z' "
            "to relabel the host directory for the container."
        ),
    )
    userns: str | None = Field(
        default="keep-id:uid=10001,gid=10001",
        description=(
            "Value for 'podman run --userns'. The default maps the host user to "
            "the agent server image's user (uid 10001), so files the agent "
            "writes to volumes are owned by the host user. None uses podman's "
            "default mapping, in which container root is the host user."
        ),
    )
    detach_logs: bool = Field(
        default=True, description="Whether to stream container logs in background."
    )
    health_check_timeout: float = Field(
        default=120.0,
        gt=0.0,
        description="Timeout in seconds to wait for container health check to pass.",
    )

    _container_name: str | None = PrivateAttr(default=None)
    _logs_process: subprocess.Popen[str] | None = PrivateAttr(default=None)
    _logs_thread: threading.Thread | None = PrivateAttr(default=None)

    def model_post_init(self, context: Any) -> None:
        """Start the container and initialize the remote workspace."""
        if self.host_port is None:
            self.host_port = find_available_tcp_port()
        if not check_port_available(self.host_port):
            raise RuntimeError(f"Port {self.host_port} is not available")

        if shutil.which("podman") is None:
            raise RuntimeError(
                "Podman is not installed. Install the 'podman' package from your "
                "distribution."
            )
        self._pull_image()

        object.__setattr__(self, "host", f"http://127.0.0.1:{self.host_port}")
        # Same resolution order as the agent server's config loader.
        session_api_key = os.environ.get(
            "OH_SESSION_API_KEYS_0", os.environ.get("SESSION_API_KEY")
        )
        object.__setattr__(self, "api_key", session_api_key)

        try:
            self._start_container()
            self._wait_for_health(timeout=self.health_check_timeout)
        except BaseException:
            self.cleanup()
            raise
        logger.info("Podman workspace is ready at %s", self.host)

        super().model_post_init(context)

    def _pull_image(self) -> None:
        """Pull server_image unless it is already present locally."""
        exists = execute_command(
            ["podman", "image", "exists", self.server_image], print_output=False
        )
        if exists.returncode == 0:
            return
        proc = execute_command(["podman", "pull", self.server_image])
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to pull {self.server_image}: {proc.stderr}")

    def _start_container(self) -> None:
        """Run the agent server container detached."""
        name = f"agent-server-{uuid.uuid4()}"
        forwarded = {
            key: os.environ[key] for key in self.forward_env if key in os.environ
        }

        flags: list[str] = []
        for key in forwarded:
            flags += ["--env", key]
        for volume in self.volumes:
            flags += ["--volume", volume]
            logger.info("Adding volume mount: %s", volume)
        if self.userns is not None:
            flags += ["--userns", self.userns]

        run_cmd = [
            "podman",
            "run",
            "--detach",
            "--name",
            name,
            "--publish",
            f"127.0.0.1:{self.host_port}:8000",
            *flags,
            self.server_image,
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ]
        # podman resolves each `--env KEY` from its own environment.
        env = sanitized_env()
        env.update(forwarded)

        self._container_name = name
        logger.info("$ %s", shlex.join(run_cmd))
        proc = subprocess.run(run_cmd, env=env, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to run podman container: {proc.stderr}")
        logger.info("Started container: %s", name)

        if self.detach_logs:
            self._logs_process = subprocess.Popen(
                ["podman", "logs", "--follow", name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self._logs_thread = threading.Thread(
                target=self._copy_logs, args=(self._logs_process,), daemon=True
            )
            self._logs_thread.start()

    @staticmethod
    def _copy_logs(process: subprocess.Popen[str]) -> None:
        """Copy the log follower's output to stdout until it exits."""
        assert process.stdout is not None
        with process.stdout:
            for line in process.stdout:
                sys.stdout.write(f"[PODMAN] {line}")
                sys.stdout.flush()

    def _container_running(self, name: str) -> bool:
        result = execute_command(
            ["podman", "container", "inspect", "--format", "{{.State.Running}}", name],
            print_output=False,
        )
        return result.stdout.strip() == "true"

    def _startup_error(self, name: str, reason: str) -> RuntimeError:
        logs = execute_command(["podman", "logs", name], print_output=False)
        return RuntimeError(f"{reason}. Logs:\n{logs.stdout}\n{logs.stderr}")

    def _wait_for_health(self, *, timeout: float) -> None:
        """Poll the health endpoint until it responds or the container exits."""
        assert self._container_name is not None
        name = self._container_name
        health_url = f"{self.host}/health"
        start = time.time()

        while time.time() - start < timeout:
            try:
                with urlopen(health_url, timeout=1.0) as resp:
                    if 200 <= resp.status < 300:
                        return
            except Exception:
                pass

            if not self._container_running(name):
                raise self._startup_error(name, "Container stopped unexpectedly")
            time.sleep(1)
        raise self._startup_error(name, "Container failed to become healthy in time")

    def __enter__(self) -> "PodmanWorkspace":
        """Context manager entry - returns the workspace itself."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        """Context manager exit - removes the container."""
        self.cleanup()

    def __del__(self) -> None:
        """Remove the container when the workspace is destroyed."""
        try:
            object.__getattribute__(self, "__pydantic_private__")
        except AttributeError:
            return
        self.cleanup()

    def cleanup(self) -> None:
        """Stop and remove the container, then stop log streaming."""
        if self._container_name is not None:
            logger.info("Removing container: %s", self._container_name)
            result = execute_command(
                ["podman", "rm", "--force", "--ignore", self._container_name],
                print_output=False,
            )
            if result.returncode != 0:
                logger.warning(
                    "Failed to remove container %s: %s",
                    self._container_name,
                    result.stderr,
                )
            self._container_name = None

        if self._logs_process is not None:
            self._logs_process.terminate()
            self._logs_process.wait(timeout=5)
            self._logs_process = None
        if self._logs_thread is not None:
            self._logs_thread.join(timeout=2)
            self._logs_thread = None
