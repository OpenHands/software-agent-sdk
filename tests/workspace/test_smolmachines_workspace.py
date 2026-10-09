"""Test SmolMachinesWorkspace against a fake smolmachines SDK (no VM needed)."""

import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class _Spec:
    """Stands in for the SDK's config dataclasses: records what it was given."""

    kwargs: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        return self.kwargs.get(name)


class _FakeMachine:
    def __init__(self, name: str) -> None:
        self.name = name
        self.files: dict[str, str] = {}
        self.events: list[str] = []
        self.running = True

    def state(self) -> str:
        return "running" if self.running else "stopped"

    def start(self) -> None:
        self.events.append("start")
        self.running = True

    def stop(self) -> None:
        self.events.append("stop")
        self.running = False

    def delete(self) -> None:
        self.events.append("delete")

    def read_file(self, path: str) -> bytes:
        return self.files[path].encode()

    def write_file(self, path: str, data: str) -> None:
        self.files[path] = data


class _NotFound(Exception):
    code = "NOT_FOUND"


@pytest.fixture
def fake_smol(monkeypatch, tmp_path):
    """Install a fake ``smol`` module and skip the health check."""
    from openhands.workspace import SmolMachinesWorkspace

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    machines: dict[str, _FakeMachine] = {}
    connected: list[str] = []
    created: list[_Spec] = []

    def create(config: _Spec) -> _FakeMachine:
        created.append(config)
        machines[config.name] = _FakeMachine(config.name)
        return machines[config.name]

    def connect(name: str) -> _FakeMachine:
        connected.append(name)
        if name not in machines:
            raise _NotFound(name)
        if not machines[name].running:
            machines[name].start()  # The real SDK starts a stopped VM on connect.
        return machines[name]

    module = types.ModuleType("smol")
    setattr(module, "Machine", types.SimpleNamespace(create=create, connect=connect))
    for spec in ("MachineConfig", "MountSpec", "PortSpec", "ResourceSpec"):
        setattr(module, spec, lambda **kwargs: _Spec(kwargs))
    monkeypatch.setitem(sys.modules, "smol", module)
    monkeypatch.setattr(
        SmolMachinesWorkspace, "_wait_for_health", lambda self, timeout: None
    )
    return types.SimpleNamespace(
        machines=machines, created=created, connected=connected
    )


def test_runs_the_agent_server_with_only_the_mount_shared(fake_smol, tmp_path):
    from openhands.workspace import SmolMachinesWorkspace

    workspace = SmolMachinesWorkspace(mount_dir=str(tmp_path), host_port=38123)

    (config,) = fake_smol.created
    assert config.image == "ghcr.io/openhands/agent-server:latest-python"
    assert "--host 0.0.0.0 --port 8000" in config.command[-1]
    assert [(p.host, p.guest) for p in config.ports] == [(38123, 8000)]
    assert [(m.source, m.target) for m in config.mounts] == [
        (str(tmp_path), "/workspace")
    ]
    assert config.user == "0:0"
    assert workspace.host == "http://127.0.0.1:38123"

    workspace.cleanup()
    assert fake_smol.machines[config.name].events == ["delete"]


def test_allow_list_enables_network_and_is_enforced_by_the_machine(fake_smol):
    from openhands.workspace import SmolMachinesWorkspace

    SmolMachinesWorkspace(
        allow_hosts=["api.anthropic.com"], network=False, host_port=38124
    ).cleanup()

    (config,) = fake_smol.created
    assert config.network is True
    assert config.resources.allow_hosts == ["api.anthropic.com"]


def test_only_listed_host_variables_reach_the_server(fake_smol, monkeypatch):
    from openhands.workspace import SmolMachinesWorkspace

    monkeypatch.setenv("SESSION_API_KEY", "session-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "host-secret")
    workspace = SmolMachinesWorkspace(host_port=38125)

    assert fake_smol.created[0].env == {"SESSION_API_KEY": "session-key"}
    assert workspace.api_key == "session-key"
    workspace.cleanup()


def test_keep_alive_reuses_the_machine_on_its_recorded_port(fake_smol):
    from openhands.workspace import SmolMachinesWorkspace

    first = SmolMachinesWorkspace(keep_alive=True, machine_name="oh-keep")
    port = first.host_port
    assert port is not None
    name = first.machine_name
    assert isinstance(name, str)
    assert name.startswith("oh-keep-")
    first.cleanup()
    assert fake_smol.machines[name].events == ["stop"]

    second = SmolMachinesWorkspace(keep_alive=True, machine_name="oh-keep")
    assert len(fake_smol.created) == 1
    assert second.host_port == port
    assert fake_smol.machines[name].events == ["stop", "start"]

    with pytest.raises(RuntimeError, match=f"port {port}"):
        SmolMachinesWorkspace(
            keep_alive=True, machine_name="oh-keep", host_port=port + 1
        )


def test_keep_alive_requires_a_machine_name(fake_smol):
    from openhands.workspace import SmolMachinesWorkspace

    with pytest.raises(ValueError, match="machine_name"):
        SmolMachinesWorkspace(keep_alive=True)


def test_unhealthy_server_does_not_leak_the_machine(fake_smol, monkeypatch):
    from openhands.workspace import SmolMachinesWorkspace

    def never_healthy(self, timeout):
        raise RuntimeError("did not become healthy")

    monkeypatch.setattr(SmolMachinesWorkspace, "_wait_for_health", never_healthy)
    with pytest.raises(RuntimeError, match="healthy"):
        SmolMachinesWorkspace(host_port=38126)

    (config,) = fake_smol.created
    assert fake_smol.machines[config.name].events == ["delete"]


def test_missing_sdk_explains_how_to_install_it(monkeypatch):
    from openhands.workspace import SmolMachinesWorkspace

    monkeypatch.setitem(sys.modules, "smol", None)
    with pytest.raises(ImportError, match=r"openhands-workspace\[smolmachines\]"):
        SmolMachinesWorkspace(host_port=38127)


def test_kept_machine_policy_changes_cannot_boot_the_old_machine(fake_smol, tmp_path):
    from openhands.workspace import SmolMachinesWorkspace

    first = SmolMachinesWorkspace(
        keep_alive=True,
        machine_name="oh-project",
        host_port=38129,
        mount_dir=str(tmp_path / "code"),
        allow_hosts=["example.com"],
    )
    old_name = first.machine_name
    first.cleanup()
    second = SmolMachinesWorkspace(
        keep_alive=True,
        machine_name="oh-project",
        host_port=38130,
        mount_dir=str(tmp_path / "code"),
        allow_hosts=["pypi.org"],
    )
    assert second.machine_name != old_name
    assert old_name not in fake_smol.connected
    assert fake_smol.machines[old_name].events == ["stop"]
    second.cleanup()


def test_guest_cannot_change_reuse_port(fake_smol):
    from openhands.workspace import SmolMachinesWorkspace

    first = SmolMachinesWorkspace(
        keep_alive=True, machine_name="oh-port", host_port=38131
    )
    name = first.machine_name
    first._machine.write_file("/etc/openhands-smolmachines-port", "8123")
    first.cleanup()
    second = SmolMachinesWorkspace(keep_alive=True, machine_name="oh-port")
    assert second.host_port == 38131
    assert second.machine_name == name
    second.cleanup()


def test_changed_forwarded_secret_rejected_before_vm_starts(fake_smol, monkeypatch):
    from openhands.workspace import SmolMachinesWorkspace

    monkeypatch.setenv("SESSION_API_KEY", "original")
    first = SmolMachinesWorkspace(
        keep_alive=True, machine_name="oh-credentials", host_port=38132
    )
    machine = fake_smol.machines[first.machine_name]
    first.cleanup()
    monkeypatch.setenv("SESSION_API_KEY", "new-token")
    with pytest.raises(RuntimeError, match="Forwarded environment changed"):
        SmolMachinesWorkspace(keep_alive=True, machine_name="oh-credentials")
    assert machine.events == ["stop"]
    assert not fake_smol.connected
