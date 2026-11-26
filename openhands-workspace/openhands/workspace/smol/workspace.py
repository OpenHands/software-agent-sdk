"""An ephemeral local Smol microVM running the standard Agent Server."""

import importlib.util
import json
import os
import re
import secrets
import select
import signal
import subprocess
import sys
import time
import uuid
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import Any, Self
from urllib.parse import urlsplit

import httpx
from pydantic import ConfigDict, Field, PrivateAttr, SecretStr, model_validator

from openhands.sdk.logger import get_logger
from openhands.sdk.workspace import RemoteWorkspace


class _StartupError(RuntimeError):
    pass


logger = get_logger(__name__)
_WORKER = str(Path(__file__).with_name("_worker.py"))


class SmolWorkspace(RemoteWorkspace):
    """Own one local microVM and access it through the remote workspace API.

    Install ``openhands-workspace[smol]`` and supply a compatible OCI Agent
    Server image. Closing the workspace deletes its VM and private storage.
    See ``examples/02_remote_agent_server/smol/README.md`` for requirements.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    server_image: str = Field(min_length=1)
    working_dir: str = "/workspace"
    cpus: int = Field(default=2, gt=0, strict=True)
    memory_mb: int = Field(default=2048, gt=0, strict=True)
    storage_gb: int = Field(default=20, gt=0, strict=True)
    network: bool = True
    server_command: list[str] | None = None
    server_env: dict[str, str | SecretStr] = Field(
        default_factory=dict, exclude=True, repr=False
    )
    startup_timeout: float = Field(default=120, gt=0, allow_inf_nan=False)
    cleanup_timeout: float = Field(default=30, gt=0, allow_inf_nan=False)
    host: str = Field(default="", exclude=True, repr=False)
    api_key: str | None = Field(default=None, exclude=True, repr=False, frozen=True)

    _process: subprocess.Popen[bytes] | None = PrivateAttr(default=None)
    _machine_id: str | None = PrivateAttr(default=None)
    _image_digest: str | None = PrivateAttr(default=None)
    _server_version: str = PrivateAttr(default="unknown")
    _allocated: bool = PrivateAttr(default=False)
    _read_buffer: bytes = PrivateAttr(default=b"")

    @model_validator(mode="before")
    @classmethod
    def _validate_settings(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if value.get("host") or value.get("api_key"):
            raise ValueError("SmolWorkspace generates its own host and authentication")
        if value.get("runtime_conversation_id") is not None:
            raise ValueError("SmolWorkspace uses the shared local conversation runtime")
        image = value.get("server_image")
        if isinstance(image, str) and (
            image != image.strip()
            or any(c.isspace() for c in image)
            or "://" in image
            or image.startswith(("/", "./", "../"))
            or "\x00" in image
            or (":" not in image.rsplit("/", 1)[-1] and "@" not in image)
        ):
            raise ValueError(
                "server_image must be an OCI reference with a tag or digest"
            )
        path = str(value.get("working_dir", "/workspace"))
        if not PurePosixPath(path).is_absolute() or "\x00" in path:
            raise ValueError("working_dir must be an absolute guest path")
        command = value.get("server_command")
        if command is not None and (
            not isinstance(command, list)
            or not command
            or any(
                not isinstance(arg, str) or not arg or "\x00" in arg for arg in command
            )
        ):
            raise ValueError("server_command must be a nonempty argv list")
        env = value.get("server_env", {})
        if isinstance(env, dict):
            if any(not isinstance(key, str) for key in env):
                raise ValueError("server_env names must be strings")
            reserved = {
                "SESSION_API_KEY",
                "OH_SECRET_KEY",
                "OH_PREVIOUS_SECRET_KEYS",
                "OH_CONVERSATION_RUNTIME",
                "OH_CONVERSATION_RUNTIME_SELECTABLE",
                "OPENHANDS_AGENT_SERVER_CONFIG_PATH",
            }
            if any(
                key in reserved or key.startswith("OH_SESSION_API_KEYS") for key in env
            ):
                raise ValueError(
                    "server_env contains reserved authentication/runtime settings"
                )
            if any(not key or "=" in key or "\x00" in key for key in env):
                raise ValueError("server_env contains an invalid environment name")
        return value

    @property
    def machine_id(self) -> str | None:
        """Exact owned identifier, retained after close for diagnostics."""
        return self._machine_id

    @property
    def endpoint(self) -> str:
        """Resolved loopback Agent Server URL."""
        return self.host

    @property
    def resolved_image_digest(self) -> str | None:
        """Image digest reported by Smol's local image cache."""
        return self._image_digest

    def model_post_init(self, context: Any) -> None:
        if importlib.util.find_spec("smol") is None:
            raise ImportError(
                "SmolWorkspace requires: pip install 'openhands-workspace[smol]'"
            )
        self._machine_id = "openhands-" + uuid.uuid4().hex
        api_key = secrets.token_urlsafe(32)
        object.__setattr__(self, "api_key", api_key)
        env = {
            key: val.get_secret_value() if isinstance(val, SecretStr) else val
            for key, val in self.server_env.items()
        }
        env.update(
            OH_SESSION_API_KEYS_0=api_key,
            OH_SECRET_KEY=secrets.token_urlsafe(32),
            OH_CONVERSATION_RUNTIME="local",
            OH_CONVERSATION_RUNTIME_SELECTABLE="false",
        )
        deadline = time.monotonic() + self.startup_timeout
        stage = "availability"
        try:
            self._process = self._spawn_worker()
            assert self._process.stdin is not None
            self._process.stdin.write(
                json.dumps(
                    {
                        "name": self._machine_id,
                        "image": self.server_image,
                        "resources": {
                            "cpus": self.cpus,
                            "memory_mb": self.memory_mb,
                            "storage_gb": self.storage_gb,
                            "network": self.network,
                        },
                        "command": self.server_command,
                        "env": env,
                        "workdir": self.working_dir,
                        "timeout": self.startup_timeout,
                    }
                ).encode()
                + b"\n"
            )
            self._process.stdin.flush()
            message = self._receive(deadline)
            while "stage" in message:
                if message["stage"] == "create":
                    self._allocated = True
                    stage = "image/VM startup"
                elif message["stage"] == "ports":
                    stage = "port publication (requires Smol publish_atomic)"
                message = self._receive(deadline)
            if message.get("error") == "availability":
                hints = {
                    "KVM_UNAVAILABLE": "check /dev/kvm access and kvm group membership",
                    "HYPERVISOR_UNAVAILABLE": (
                        "check virtualization support and reinstall the Smol wheel"
                    ),
                }
                hint = hints.get(
                    message.get("code") or "",
                    "check host architecture and local virtualization support",
                )
                raise _StartupError(f"Smol startup failed at availability: {hint}")
            if message.get("error") == "atomic_ports":
                raise _StartupError(
                    "Smol startup failed: install a published smolmachines wheel "
                    "with Machine.publish_atomic (the 1.32.4 wheel lacks this API)"
                )
            if message.get("error") == "image_architecture":
                raise _StartupError(
                    "Smol startup failed: use a published image "
                    "matching the host architecture"
                )
            if "error" in message:
                raise RuntimeError(f"Smol startup failed at {stage}")
            endpoint = message["endpoint"]
            address = urlsplit(endpoint)
            if (
                address.scheme != "http"
                or address.hostname != "127.0.0.1"
                or not address.port
                or address.username
                or address.password
                or address.query
                or address.fragment
                or address.path not in ("", "/")
            ):
                raise _StartupError(
                    "Smol startup failed: non-loopback control endpoint"
                )
            object.__setattr__(self, "host", endpoint.rstrip("/"))
            self._image_digest = message["image_digest"]
            stage = "Agent Server health"
            self._wait_for_server(deadline)
            super().model_post_init(context)
            logger.info(
                "Smol workspace ready: machine=%s endpoint=%s image_digest=%s "
                "smol=%s sdk=%s server=%s cpus=%s memory_mb=%s "
                "storage_gb=%s network=%s",
                self.machine_id,
                self.endpoint,
                self.resolved_image_digest,
                message["smol_version"],
                version("openhands-sdk"),
                self._server_version,
                self.cpus,
                self.memory_mb,
                self.storage_gb,
                self.network,
            )
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit, _StartupError)):
                self._close_preserving(error)
                raise
            # Dependency exceptions and request URLs may contain credentials.
            safe = RuntimeError(f"Smol startup failed at {stage}")
            self._close_preserving(safe)
            raise safe from None

    @staticmethod
    def _spawn_worker() -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [sys.executable, _WORKER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            start_new_session=True,
            env={**os.environ, "SMOLVM_PUBLISH_ADDR": "127.0.0.1"},
        )

    def _receive(self, deadline: float) -> dict[str, Any]:
        assert self._process is not None and self._process.stdout is not None
        while b"\n" not in self._read_buffer:
            remaining = deadline - time.monotonic()
            if (
                remaining <= 0
                or not select.select([self._process.stdout], [], [], remaining)[0]
            ):
                raise TimeoutError("Smol startup deadline exceeded")
            chunk = os.read(self._process.stdout.fileno(), 4096)
            if not chunk or len(self._read_buffer) + len(chunk) > 65536:
                raise RuntimeError("Smol helper exited during startup")
            self._read_buffer += chunk
        line, self._read_buffer = self._read_buffer.split(b"\n", 1)
        return json.loads(line)

    @staticmethod
    def _request_timeout(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Agent Server readiness deadline exceeded")
        return min(1.0, remaining)

    def _wait_for_server(self, deadline: float) -> None:
        stage = "health"
        with httpx.Client(base_url=self.host, trust_env=False) as client:
            while time.monotonic() < deadline:
                assert self._process is not None
                if self._process.poll() is not None:
                    raise RuntimeError(
                        "Smol helper exited before Agent Server readiness"
                    )
                try:
                    stage = "health"
                    response = client.get(
                        "/health", timeout=self._request_timeout(deadline)
                    )
                    response.raise_for_status()
                    stage = "authenticated operation"
                    unauthorized = client.post(
                        "/api/bash/execute_bash_command",
                        json={"command": "true", "timeout": 1},
                        timeout=self._request_timeout(deadline),
                    )
                    if unauthorized.status_code not in (401, 403):
                        raise _StartupError(
                            "Smol startup failed: image must require "
                            "session authentication"
                        )
                    response = client.post(
                        "/api/bash/execute_bash_command",
                        headers=self._headers,
                        json={
                            "command": "test -w .",
                            "cwd": self.working_dir,
                            "timeout": 1,
                        },
                        timeout=self._request_timeout(deadline),
                    )
                    if response.status_code in (401, 403):
                        raise _StartupError(
                            "Smol startup failed: Agent Server rejected "
                            "generated authentication"
                        )
                    response.raise_for_status()
                    if response.json()["exit_code"] != 0:
                        raise _StartupError(
                            "Smol startup failed: guest working directory "
                            "is not writable"
                        )
                    stage = "runtime verification"
                    info_response = client.get(
                        "/server_info",
                        headers=self._headers,
                        timeout=self._request_timeout(deadline),
                    )
                    info_response.raise_for_status()
                    info = info_response.json()
                    if info.get("conversation_runtime") != "local":
                        raise _StartupError(
                            "Smol startup failed: image must use the local runtime"
                        )
                    server_version = info.get("version", "")
                    if isinstance(server_version, str) and re.fullmatch(
                        r"\d+\.\d+\.\d+(?:[a-z0-9.+-]{0,32})", server_version
                    ):
                        self._server_version = server_version
                    return
                except (httpx.HTTPError, OSError):
                    time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        raise _StartupError(f"Smol startup failed: Agent Server timed out at {stage}")

    @property
    def client(self) -> httpx.Client:
        if self._process is None:
            raise RuntimeError("SmolWorkspace is closed")
        return super().client

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        primary = exc_val
        try:
            super().__exit__(exc_type, exc_val, exc_tb)
        except BaseException as error:
            primary = error
            raise
        finally:
            self._close_preserving(primary)

    def _close_preserving(self, primary: BaseException | None) -> None:
        try:
            self.close()
        except BaseException as error:
            if primary is None:
                raise
            primary.add_note(str(error))
            logger.warning("%s", error)

    @staticmethod
    def _kill_worker(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def close(self) -> None:
        """Close clients, stop the owned VM and delete its private storage."""
        self.reset_client()
        process, self._process = self._process, None
        if process is None and not self._allocated:
            return
        deadline = time.monotonic() + self.cleanup_timeout
        try:
            if process is None:
                raise BrokenPipeError
            process.communicate(b"\n", timeout=self.cleanup_timeout / 2)
            if process.returncode == 0:
                self._allocated = False
                return
        except (subprocess.TimeoutExpired, BrokenPipeError):
            pass
        finally:
            if process is not None:
                if process.poll() is None:
                    self._kill_worker(process)
                process.wait()
                if process.stdin is not None:
                    process.stdin.close()
                if process.stdout is not None:
                    process.stdout.close()
        if self._allocated:
            cleanup = self._spawn_worker()
            try:
                cleanup.communicate(
                    (
                        json.dumps({"name": self.machine_id, "cleanup": True}) + "\n"
                    ).encode(),
                    timeout=max(0.001, deadline - time.monotonic()),
                )
                if cleanup.returncode == 0:
                    self._allocated = False
                    return
            except (subprocess.TimeoutExpired, BrokenPipeError):
                pass
            finally:
                if cleanup.poll() is None:
                    self._kill_worker(cleanup)
                cleanup.wait()
                if cleanup.stdin is not None:
                    cleanup.stdin.close()
                if cleanup.stdout is not None:
                    cleanup.stdout.close()
            raise RuntimeError(
                f"Smol cleanup failed for {self.machine_id}. Recover with "
                f"smol.Machine.delete_by_name({self.machine_id!r}, "
                "smol.ConnectOptions(target='local'))"
            )

    def __del__(self) -> None:
        try:
            self.close()
        except BaseException:
            pass
