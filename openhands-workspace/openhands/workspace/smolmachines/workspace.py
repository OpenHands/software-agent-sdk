"""smol machines workspace: the agent server inside a hardware-isolated microVM.

Requires the ``smolmachines`` extra::

    pip install openhands-workspace[smolmachines]

smol machines run on Linux with KVM and on Apple Silicon macOS. No Docker
daemon is needed.
"""

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any, Self
from urllib.request import urlopen

from pydantic import Field, PrivateAttr

from openhands.sdk.logger import get_logger
from openhands.sdk.workspace import RemoteWorkspace
from openhands.workspace.docker.workspace import (
    check_port_available,
    find_available_tcp_port,
)


logger = get_logger(__name__)

#: The port the agent server listens on inside the machine.
GUEST_PORT = 8000


def _state_dir() -> Path:
    """Local, private metadata; a guest cannot choose which host port we trust."""
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    directory = root / "openhands" / "smolmachines"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return directory


# The agent server image ships either a compiled server or a Python module. Run
# whichever it has, under tini as the image's own ENTRYPOINT does, with the
# arguments DockerWorkspace passes.
LAUNCH_SCRIPT = (
    "if [ -x /usr/local/bin/openhands-agent-server ]; then "
    "set -- /usr/local/bin/openhands-agent-server; "
    "else set -- /agent-server/.venv/bin/python -m openhands.agent_server; fi; "
    f'exec tini -- "$@" --host 0.0.0.0 --port {GUEST_PORT}'
)


def _smol() -> ModuleType:
    try:
        import smol  # type: ignore[import-not-found]
    except ImportError as e:
        raise ImportError(
            "SmolMachinesWorkspace needs the smolmachines SDK; install it "
            "with: pip install openhands-workspace[smolmachines]"
        ) from e
    return smol


class SmolMachinesWorkspace(RemoteWorkspace):
    """Remote workspace that runs the agent server in a smol machine.

    The machine is a microVM with its own Linux kernel: commands the agent
    runs, packages it installs and processes it starts stay inside it, and
    only ``mount_dir`` is shared with the host. Egress can be limited to a
    list of hosts or CIDR ranges.

    Example:
        with SmolMachinesWorkspace(
            mount_dir="./project",
            allow_hosts=["api.anthropic.com", "pypi.org"],
        ) as workspace:
            result = workspace.execute_command("uname -r")
    """

    working_dir: str = Field(
        default="/workspace",
        description="Working directory inside the machine.",
    )
    host: str = Field(
        default="",
        description="Agent server URL (set once the machine is ready).",
    )

    server_image: str = Field(
        default="ghcr.io/openhands/agent-server:latest-python",
        description="Pre-built agent server image the machine boots.",
    )
    host_port: int | None = Field(
        default=None,
        description="Host port the agent server is published on. If None, finds "
        "an available port.",
    )
    mount_dir: str | None = Field(
        default=None,
        description="Host directory mounted at working_dir. Nothing else on the "
        "host is visible to the machine.",
    )
    forward_env: list[str] = Field(
        default_factory=lambda: ["DEBUG", "SESSION_API_KEY", "OH_SESSION_API_KEYS_0"],
        description="Host environment variables passed to the agent server.",
    )
    network: bool = Field(
        default=True,
        description="Give the machine network access. Ignored when allow_hosts "
        "or allow_cidrs is set.",
    )
    allow_hosts: list[str] | None = Field(
        default=None,
        description="Only these hosts are reachable from the machine. The agent "
        "server calls the LLM from inside it, so include the provider's API host.",
    )
    allow_cidrs: list[str] | None = Field(
        default=None,
        description="Only these CIDR ranges are reachable from the machine.",
    )
    cpus: int | None = Field(default=None, description="vCPUs for the machine.")
    memory_mb: int | None = Field(default=4096, description="Machine memory in MiB.")
    storage_gb: int | None = Field(default=None, description="Storage disk size in GB.")
    machine_name: str | None = Field(
        default=None,
        description="Machine name. With keep_alive, a policy hash is appended so "
        "a changed image, mount, or network policy uses a different machine.",
    )
    keep_alive: bool = Field(
        default=False,
        description="Stop instead of deleting the machine on cleanup, so a later "
        "workspace with the same machine_name and policy reuses it with its image "
        "already pulled. Host port and forwarded environment are verified using "
        "private host state. Requires machine_name.",
    )
    run_as_root: bool = Field(
        default=True,
        description="Run the agent server as root inside the machine so it can "
        "write mount_dir whatever its host owner. The machine is the isolation "
        "boundary; root in it is not root on the host.",
    )
    health_check_timeout: float = Field(
        default=900.0,
        gt=0.0,
        description="Seconds to wait for the agent server. The first boot pulls "
        "the image inside the machine.",
    )

    _machine: Any = PrivateAttr(default=None)
    _policy: str = PrivateAttr(default="")

    def model_post_init(self, context: Any) -> None:
        """Boot or reuse the machine, then connect once the server is healthy."""
        if self.keep_alive and not self.machine_name:
            raise ValueError("keep_alive needs a machine_name to find the machine")
        if self.machine_name is None:
            object.__setattr__(
                self, "machine_name", f"openhands-{uuid.uuid4().hex[:12]}"
            )

        if self.keep_alive:
            if self.mount_dir:
                mount_root = Path(self.mount_dir).resolve()
                state_root = _state_dir().resolve()
                if state_root == mount_root or mount_root in state_root.parents:
                    raise ValueError(
                        "keep_alive host state must be outside mount_dir so the "
                        "machine cannot change its policy or published port"
                    )
            # The name encodes the permissions under which this machine was created;
            # an old, more permissive VM must never start under a new policy.
            shape = {
                "image": self.server_image,
                "mount": os.path.abspath(self.mount_dir) if self.mount_dir else None,
                "workdir": self.working_dir,
                "network": self.network,
                "hosts": self.allow_hosts,
                "cidrs": self.allow_cidrs,
                "cpus": self.cpus,
                "memory": self.memory_mb,
                "storage": self.storage_gb,
                "root": self.run_as_root,
            }
            digest = hashlib.sha256(
                json.dumps(shape, sort_keys=True).encode()
            ).hexdigest()[:16]
            object.__setattr__(self, "machine_name", f"{self.machine_name}-{digest}")
            forwarded = {
                key: os.environ[key] for key in self.forward_env if key in os.environ
            }
            self._policy = hashlib.sha256(
                json.dumps(forwarded, sort_keys=True).encode()
            ).hexdigest()

        started = time.time()
        reused = self._reuse()
        self._machine = reused if reused is not None else self._create()
        object.__setattr__(self, "host", f"http://127.0.0.1:{self.host_port}")
        object.__setattr__(self, "api_key", os.environ.get("SESSION_API_KEY"))
        try:
            self._wait_for_health(timeout=self.health_check_timeout)
        except BaseException:
            if self.keep_alive and reused is None:
                self._discard_failed_start()
            else:
                self.cleanup()
            raise
        logger.info(
            "smol machine %s is serving the agent at %s (%.0fs)",
            self.machine_name,
            self.host,
            time.time() - started,
        )
        super().model_post_init(context)

    def _state_path(self) -> Path:
        assert self.machine_name is not None
        key = hashlib.sha256(self.machine_name.encode()).hexdigest()
        return _state_dir() / f"{key}.json"

    def _reuse(self) -> Any:
        """Check host-owned policy and port before connecting (which starts a VM)."""
        if not self.keep_alive:
            return None
        state_file = self._state_path()
        if not state_file.exists():
            return None
        state = json.loads(state_file.read_text())
        if state["policy"] != self._policy:
            raise RuntimeError(
                "Forwarded environment changed for kept smol machine "
                f"{self.machine_name}; choose another machine_name or remove it"
            )
        recorded = state["port"]
        if not isinstance(recorded, int) or not (1 <= recorded <= 65535):
            raise RuntimeError(
                f"Invalid host port for smol machine {self.machine_name}"
            )
        if self.host_port is not None and self.host_port != recorded:
            raise RuntimeError(
                f"smol machine {self.machine_name} publishes the agent server on "
                f"port {recorded}, not {self.host_port}"
            )
        smol = _smol()
        assert self.machine_name is not None
        try:
            machine = smol.Machine.connect(self.machine_name)
        except Exception as e:
            if getattr(e, "code", None) == "NOT_FOUND":
                state_file.unlink(missing_ok=True)
                return None
            raise
        object.__setattr__(self, "host_port", recorded)
        logger.info("Reusing smol machine %s on port %d", self.machine_name, recorded)
        return machine

    def _create(self) -> Any:
        smol = _smol()
        if self.host_port is None:
            object.__setattr__(self, "host_port", find_available_tcp_port())
        elif not check_port_available(self.host_port):
            raise RuntimeError(f"Port {self.host_port} is not available")

        restricted = bool(self.allow_hosts or self.allow_cidrs)
        env = {key: os.environ[key] for key in self.forward_env if key in os.environ}
        mounts = None
        if self.mount_dir:
            mounts = [
                smol.MountSpec(
                    source=os.path.abspath(self.mount_dir), target=self.working_dir
                )
            ]
        machine = smol.Machine.create(
            smol.MachineConfig(
                name=self.machine_name,
                image=self.server_image,
                command=["sh", "-c", LAUNCH_SCRIPT],
                ports=[smol.PortSpec(host=self.host_port, guest=GUEST_PORT)],
                mounts=mounts,
                env=env or None,
                network=True if restricted else self.network,
                resources=smol.ResourceSpec(
                    cpus=self.cpus,
                    memory_mb=self.memory_mb,
                    storage_gb=self.storage_gb,
                    allow_hosts=self.allow_hosts,
                    allow_cidrs=self.allow_cidrs,
                ),
                user="0:0" if self.run_as_root else None,
                # Readiness is the health check, which also covers the pull.
                wait_for_ports=False,
                ready_timeout_seconds=self.health_check_timeout,
                persistent=self.keep_alive,
            )
        )
        if self.keep_alive:
            state_file = self._state_path()
            temp = state_file.with_name(f"{state_file.name}.{uuid.uuid4().hex}.tmp")
            try:
                with os.fdopen(
                    os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
                ) as output:
                    json.dump({"policy": self._policy, "port": self.host_port}, output)
                os.replace(temp, state_file)
            except BaseException:
                machine.delete()
                raise
            finally:
                temp.unlink(missing_ok=True)
        return machine

    def _discard_failed_start(self) -> None:
        """A newly created VM with no healthy server must not be reused."""
        try:
            self._machine.delete()
        except Exception as e:
            logger.warning(
                "Could not delete unhealthy smol machine %s: %s", self.machine_name, e
            )
            return
        self._machine = None
        try:
            self._state_path().unlink(missing_ok=True)
        except OSError as e:
            logger.warning(
                "Could not clear smol machine state %s: %s", self.machine_name, e
            )

    def _wait_for_health(self, *, timeout: float) -> None:
        deadline = time.time() + timeout
        url = f"http://127.0.0.1:{self.host_port}/health"
        while time.time() < deadline:
            try:
                with urlopen(url, timeout=2.0) as resp:
                    if 200 <= getattr(resp, "status", 200) < 300:
                        return
            except OSError:
                pass  # not listening yet
            time.sleep(1)
        raise RuntimeError(
            f"The agent server in smol machine {self.machine_name} did not become "
            f"healthy within {timeout:.0f}s"
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        self.cleanup()

    def __del__(self) -> None:
        # Guard against accessing private attributes during interpreter shutdown
        if getattr(self, "__pydantic_private__", None) is not None:
            self.cleanup()

    def cleanup(self) -> None:
        """Delete the machine, or stop it when keep_alive is set."""
        machine = getattr(self, "_machine", None)
        if machine is None:
            return
        try:
            if self.keep_alive:
                machine.stop()
            else:
                machine.delete()
        except Exception as e:
            logger.warning(
                "Could not clean up smol machine %s: %s", self.machine_name, e
            )
        else:
            self._machine = None
