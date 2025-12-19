"""Apptainer-based remote workspace implementation."""

import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from pydantic import Field, PrivateAttr, model_validator

from openhands.agent_server.docker.build import PlatformType, TargetType
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.command import execute_command
from openhands.sdk.workspace import RemoteWorkspace


logger = get_logger(__name__)


def check_port_available(port: int) -> bool:
    """Check if a port is available for binding."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("0.0.0.0", port))
        return True
    except OSError:
        time.sleep(0.1)
        return False
    finally:
        sock.close()


def find_available_tcp_port(
    min_port: int = 30000, max_port: int = 39999, max_attempts: int = 50
) -> int:
    """Find an available TCP port in a specified range."""
    import random

    rng = random.SystemRandom()
    ports = list(range(min_port, max_port + 1))
    rng.shuffle(ports)

    for port in ports[:max_attempts]:
        if check_port_available(port):
            return port
    return -1


class ApptainerWorkspace(RemoteWorkspace):
    """Remote workspace that sets up and manages an Apptainer container.

    This workspace creates an Apptainer container running the OpenHands agent
    server, waits for it to become healthy, and then provides remote workspace
    operations through the container's HTTP API.

    Apptainer (formerly Singularity) is a container runtime that doesn't require
    root access, making it ideal for HPC and shared computing environments.

    Example:
        with ApptainerWorkspace(base_image="python:3.12") as workspace:
            result = workspace.execute_command("ls -la")
    """

    # Override parent fields with defaults
    working_dir: str = Field(
        default="/workspace",
        description="Working directory inside the container.",
    )
    host: str = Field(
        default="",
        description=("Remote host URL (set automatically during container startup)."),
    )

    # Apptainer-specific configuration
    base_image: str | None = Field(
        default=None,
        description=(
            "Base Docker image to use for the agent server container. "
            "Mutually exclusive with server_image and sif_file."
        ),
    )
    server_image: str | None = Field(
        default=None,
        description=(
            "Pre-built agent server image to use. If None, builds from "
            "base_image. Mutually exclusive with base_image and sif_file."
        ),
    )
    sif_file: str | None = Field(
        default=None,
        description=(
            "Path to existing Apptainer SIF file. If provided, skips build. "
            "Mutually exclusive with base_image and server_image."
        ),
    )
    host_port: int | None = Field(
        default=None,
        description="Port to bind the container to. If None, finds available port.",
    )
    forward_env: list[str] = Field(
        default_factory=lambda: ["DEBUG"],
        description="Environment variables to forward to the container.",
    )
    mount_dir: str | None = Field(
        default=None,
        description="Optional host directory to mount into the container.",
    )
    detach_logs: bool = Field(
        default=True, description="Whether to stream container logs in background."
    )
    target: TargetType = Field(
        default="source", description="Build target for the Docker image."
    )
    platform: PlatformType = Field(
        default="linux/amd64", description="Platform for the Docker image."
    )
    extra_ports: bool = Field(
        default=False,
        description="Whether to expose additional ports (VSCode, VNC).",
    )
    cache_dir: str | None = Field(
        default=None,
        description=(
            "Directory for Apptainer cache and SIF files. "
            "Defaults to ~/.apptainer_cache"
        ),
    )
    use_fakeroot: bool = Field(
        default=True,
        description=(
            "Whether to use --fakeroot for consistent file ownership. "
            "Set to False if fakeroot is not supported in your environment."
        ),
    )

    _instance_name: str | None = PrivateAttr(default=None)
    _logs_thread: threading.Thread | None = PrivateAttr(default=None)
    _stop_logs: threading.Event = PrivateAttr(default_factory=threading.Event)
    _sif_path: str = PrivateAttr()
    _process: subprocess.Popen | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _validate_images(self):
        """Ensure exactly one of base_image, server_image, or sif_file is provided."""
        sources = [self.base_image, self.server_image, self.sif_file]
        if sum(x is not None for x in sources) != 1:
            raise ValueError(
                "Exactly one of 'base_image', 'server_image', or 'sif_file' "
                "must be set."
            )
        return self

    def model_post_init(self, context: Any) -> None:
        """Set up the Apptainer container and initialize the remote workspace."""
        # Determine port
        if self.host_port is None:
            self.host_port = find_available_tcp_port()
        else:
            self.host_port = int(self.host_port)

        if not check_port_available(self.host_port):
            raise RuntimeError(f"Port {self.host_port} is not available")

        if self.extra_ports:
            if not check_port_available(self.host_port + 1):
                raise RuntimeError(
                    f"Port {self.host_port + 1} is not available for VSCode"
                )
            if not check_port_available(self.host_port + 2):
                raise RuntimeError(
                    f"Port {self.host_port + 2} is not available for VNC"
                )

        # Ensure apptainer is available
        apptainer_ver = execute_command(["apptainer", "version"]).returncode
        if apptainer_ver != 0:
            raise RuntimeError(
                "Apptainer is not available. Please install Apptainer from "
                "https://apptainer.org/docs/user/main/quick_start.html"
            )

        # Set up cache directory
        if self.cache_dir is None:
            self.cache_dir = str(Path.home() / ".apptainer_cache")
        os.makedirs(self.cache_dir, exist_ok=True)

        # Build or use existing SIF file
        if self.sif_file:
            if not Path(self.sif_file).exists():
                raise RuntimeError(f"SIF file not found: {self.sif_file}")
            self._sif_path = self.sif_file
            logger.info("Using existing SIF file: %s", self._sif_path)
        else:
            self._sif_path = self._prepare_sif_image()

        # Run container
        self._instance_name = f"agent-server-{uuid.uuid4()}"
        self._start_container()

        # Set host for RemoteWorkspace to use
        object.__setattr__(self, "host", f"http://localhost:{self.host_port}")
        # Apptainer inherits SESSION_API_KEY from environment by default
        # We need to match it if present
        session_api_key = os.environ.get("SESSION_API_KEY")
        object.__setattr__(self, "api_key", session_api_key)

        # Wait for container to be healthy
        self._wait_for_health()
        logger.info("Apptainer workspace is ready at %s", self.host)

        # Now initialize the parent RemoteWorkspace with the container URL
        super().model_post_init(context)

    def _prepare_sif_image(self) -> str:
        """Prepare the SIF image file from base_image or server_image."""
        if self.base_image:
            if "ghcr.io/openhands/agent-server" in self.base_image:
                raise RuntimeError(
                    "base_image cannot be a pre-built agent-server image. "
                    "Use server_image=... instead."
                )
            # For base_image, we pull directly from the Docker registry
            # This doesn't require Docker daemon - Apptainer can pull directly
            docker_image = self.base_image
        elif self.server_image:
            docker_image = self.server_image
        else:
            raise RuntimeError("Unreachable: one of base_image or server_image is set")

        # Convert Docker image to SIF
        assert self.cache_dir is not None, "cache_dir must be set in model_post_init"
        sif_name = docker_image.replace(":", "_").replace("/", "_") + ".sif"
        sif_path = os.path.join(self.cache_dir, sif_name)

        if Path(sif_path).exists():
            logger.info("Using cached SIF file: %s", sif_path)
            return sif_path

        logger.info("Pulling and converting Docker image to SIF: %s", docker_image)
        # Use apptainer pull to directly convert from Docker registry
        # This doesn't require Docker daemon
        pull_cmd = [
            "apptainer",
            "pull",
            sif_path,
            f"docker://{docker_image}",
        ]
        proc = execute_command(pull_cmd)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to pull and convert Docker image: {proc.stderr}"
            )

        logger.info("SIF file created: %s", sif_path)
        return sif_path

    def _start_container(self) -> None:
        """Start the Apptainer container instance."""
        # Prepare environment variables
        env_args: list[str] = []
        for key in self.forward_env:
            if key in os.environ:
                env_args += ["--env", f"{key}={os.environ[key]}"]

        # Prepare bind mounts
        bind_args: list[str] = []
        if self.mount_dir:
            mount_path = "/workspace"
            bind_args += ["--bind", f"{self.mount_dir}:{mount_path}"]
            logger.info(
                "Mounting host dir %s to container path %s",
                self.mount_dir,
                mount_path,
            )

        # Build container options
        container_opts: list[str] = [
            "--writable-tmpfs",
            "--cleanenv",  # Prevent host environment leakage
            "--no-home",  # Don't mount host home directory
        ]

        # Add fakeroot for consistent file ownership (user appears as root)
        if self.use_fakeroot:
            container_opts.append("--fakeroot")

        # Run the agent server directly using exec (no instance needed)
        # This is more compatible with environments without systemd/FUSE
        # Explicitly set PATH to ensure container's python is found
        server_cmd = [
            "apptainer",
            "exec",
            *container_opts,
            *env_args,
            *bind_args,
            self._sif_path,
            "/bin/bash",
            "-c",
            "export PATH=/usr/local/bin:/usr/bin:/bin:$PATH && "
            f"cd /workspace && openhands-agent-server "
            f"--host 0.0.0.0 --port {self.host_port}",
        ]

        # Start the server process in the background
        self._process = subprocess.Popen(
            server_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # Optionally stream logs in background
        if self.detach_logs:
            self._logs_thread = threading.Thread(target=self._stream_logs, daemon=True)
            self._logs_thread.start()

    def _stream_logs(self) -> None:
        """Stream container logs to stdout in the background."""
        if not self._process or not self._process.stdout:
            return
        try:
            for line in iter(self._process.stdout.readline, ""):
                if self._stop_logs.is_set():
                    break
                if line:
                    sys.stdout.write(f"[APPTAINER] {line}")
                    sys.stdout.flush()
        except Exception as e:
            sys.stderr.write(f"Error streaming apptainer logs: {e}\n")
        finally:
            try:
                self._stop_logs.set()
            except Exception:
                pass

    def _wait_for_health(self, timeout: float = 120.0) -> None:
        """Wait for the container to become healthy."""
        start = time.time()
        health_url = f"http://127.0.0.1:{self.host_port}/health"

        while time.time() - start < timeout:
            try:
                with urlopen(health_url, timeout=1.0) as resp:
                    if 200 <= getattr(resp, "status", 200) < 300:
                        return
            except Exception:
                pass

            # Check if process is still running
            if self._process and self._process.poll() is not None:
                # Process has terminated
                raise RuntimeError(
                    f"Container process stopped unexpectedly with "
                    f"exit code {self._process.returncode}"
                )

            time.sleep(1)
        raise RuntimeError("Container failed to become healthy in time")

    def __enter__(self) -> "ApptainerWorkspace":
        """Context manager entry - returns the workspace itself."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        """Context manager exit - cleans up the Apptainer container."""
        self.cleanup()

    def __del__(self) -> None:
        """Clean up the Apptainer container when the workspace is destroyed."""
        # Guard against accessing private attributes during interpreter shutdown
        if getattr(self, "__pydantic_private__", None) is not None:
            self.cleanup()

    def cleanup(self) -> None:
        """Stop and remove the Apptainer container."""
        if getattr(self, "_instance_name", None):
            # Stop logs streaming
            self._stop_logs.set()
            if self._logs_thread and self._logs_thread.is_alive():
                self._logs_thread.join(timeout=2)

            # Terminate the server process if running
            if self._process:
                try:
                    logger.info("Terminating Apptainer process...")
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except Exception as e:
                    logger.warning("Error terminating process: %s", e)
                    try:
                        self._process.kill()
                    except Exception:
                        pass

            self._process = None
            self._instance_name = None
