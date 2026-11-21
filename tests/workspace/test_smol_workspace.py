"""Owned VM lifecycle and configuration contracts without virtualization."""

import json
import subprocess
import sys
import time
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from openhands.sdk.workspace import BaseWorkspace, RemoteWorkspace
from openhands.workspace import SmolWorkspace


def test_import_does_not_load_smol():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from openhands.workspace import "
            "SmolWorkspace; assert 'smol' not in sys.modules; "
            "assert 'smol._native' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0


def test_missing_extra(monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    with pytest.raises(ImportError, match=r"pip install 'openhands-workspace\[smol\]'"):
        SmolWorkspace(server_image="test:server")


@pytest.mark.parametrize(
    "settings",
    [
        {"cpus": 0},
        {"cpus": True},
        {"memory_mb": -1},
        {"storage_gb": 0},
        {"startup_timeout": 0},
        {"cleanup_timeout": float("inf")},
        {"working_dir": "relative"},
        {"server_command": []},
        {"server_command": "sh -c true"},
        {"server_command": [""]},
        {"host": "http://example.com"},
        {"api_key": "caller-secret"},
        {"server_env": {"OH_SECRET_KEY": "caller-secret"}},
        {"server_env": {"OH_SESSION_API_KEYS_1": "caller-secret"}},
        {"server_env": {"OH_CONVERSATION_RUNTIME": "docker"}},
        {"server_env": {"OPENHANDS_AGENT_SERVER_CONFIG_PATH": "/config"}},
        {"server_image": "untagged-image"},
        {"server_image": "https://image:tag"},
        {"server_image": ""},
    ],
)
def test_invalid_settings_do_not_allocate(settings, smol_fake_sdk):
    with pytest.raises(ValidationError):
        SmolWorkspace(**({"server_image": "test:server"} | settings))
    assert smol_fake_sdk() == []


def test_unavailable_host_does_not_allocate(smol_fake_sdk, monkeypatch):
    monkeypatch.setenv("SMOL_TEST_MODE", "unavailable")
    with pytest.raises(RuntimeError, match="availability"):
        SmolWorkspace(server_image="test:server")
    assert [c["event"] for c in smol_fake_sdk()] == ["availability"]


def test_startup_configuration_and_owned_cleanup(smol_fake_sdk, monkeypatch):
    monkeypatch.setenv("SMOL_CLOUD_TOKEN", "ambient-cloud-secret")
    monkeypatch.setenv("SESSION_API_KEY", "ambient-session-secret")
    monkeypatch.setenv("HOME_SECRET", "ambient-home-secret")
    with SmolWorkspace(
        server_image="test:server",
        network=False,
        server_env={"EXPLICIT_KEY": "opt-in-secret"},
        server_command=["/server", "--port", "8000"],
    ) as workspace:
        create = next(c for c in smol_fake_sdk() if c["event"] == "create")
        assert create["target"] == "local"
        assert create["image"] == "test:server"
        assert create["resources"] == {
            "cpus": 2,
            "memory_mb": 2048,
            "storage_gb": 20,
            "network": False,
        }
        assert create["command"] == ["/server", "--port", "8000"]
        assert create["workdir"] == "/workspace"
        assert create["persistent"] is False and create["detach"] is False
        assert create["env"] == {
            "EXPLICIT_KEY": "opt-in-secret",
            "OH_SESSION_API_KEYS_0": workspace.api_key,
            "OH_SECRET_KEY": create["env"]["OH_SECRET_KEY"],
            "OH_CONVERSATION_RUNTIME": "local",
            "OH_CONVERSATION_RUNTIME_SELECTABLE": "false",
        }
        assert workspace.api_key != create["env"]["OH_SECRET_KEY"]
        assert workspace.endpoint.startswith("http://127.0.0.1:")
        assert workspace.resolved_image_digest == "sha256:" + "1" * 64
        identifier = workspace.machine_id
    workspace.close()
    deletes = [c for c in smol_fake_sdk() if c["event"] == "delete"]
    assert deletes == [{"event": "delete", "name": identifier, "target": "local"}]


def test_serialization_reconstructs_fresh_vm(smol_fake_sdk):
    with SmolWorkspace(server_image="test:server", server_env={"TOKEN": "secret"}) as a:
        payload = a.model_dump(mode="json")
        exposed = a.model_dump_json(context={"expose_secrets": True}) + repr(a)
        assert "secret" not in exposed
        assert a.api_key is not None and a.api_key not in exposed
        assert a.host not in exposed
        assert "server_env" not in payload
        assert "host" not in payload and "api_key" not in payload
        assert payload["kind"] == "SmolWorkspace"
        with BaseWorkspace.model_validate(payload) as b:
            assert isinstance(b, SmolWorkspace)
            assert b.machine_id != a.machine_id
            assert b.api_key != a.api_key
            assert b.endpoint != a.endpoint


@pytest.mark.parametrize("mode", ["create_error", "create_interrupt"])
def test_create_failure_before_handle_is_cleaned(smol_fake_sdk, monkeypatch, mode):
    monkeypatch.setenv("SMOL_TEST_MODE", mode)
    with pytest.raises(RuntimeError, match="image/VM startup") as error:
        SmolWorkspace(server_image="test:server")
    assert "do-not-log-test-secret" not in str(error.value)
    create = next(c for c in smol_fake_sdk() if c["event"] == "create")
    assert any(
        c["event"] == "delete" and c["name"] == create["name"] for c in smol_fake_sdk()
    )


@pytest.mark.parametrize(
    "mode",
    ["health_fail", "auth_fail", "nonloopback", "wrong_runtime", "auth_disabled"],
)
def test_readiness_failure_rolls_back(smol_fake_sdk, monkeypatch, mode):
    monkeypatch.setenv("SMOL_TEST_MODE", mode)
    with pytest.raises(RuntimeError, match="Smol startup failed"):
        SmolWorkspace(server_image="test:server", startup_timeout=4)
    assert any(c["event"] == "delete" for c in smol_fake_sdk())


def test_startup_timeout_terminates_native_worker(smol_fake_sdk, monkeypatch):
    monkeypatch.setenv("SMOL_TEST_MODE", "hang_create")
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="startup failed"):
        SmolWorkspace(server_image="test:server", startup_timeout=3, cleanup_timeout=3)
    assert time.monotonic() - start < 8
    assert any(c["event"] == "delete" for c in smol_fake_sdk())


def test_interrupted_readiness_cleans_vm(smol_fake_sdk):
    with patch.object(SmolWorkspace, "_wait_for_server", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            SmolWorkspace(server_image="test:server")
    assert any(c["event"] == "delete" for c in smol_fake_sdk())


def test_parent_exit_failure_still_cleans_vm(smol_fake_sdk):
    workspace = SmolWorkspace(server_image="test:server")
    with patch.object(RemoteWorkspace, "__exit__", side_effect=RuntimeError("parent")):
        with pytest.raises(RuntimeError, match="parent"):
            workspace.__exit__(None, None, None)
    assert any(c["event"] == "delete" for c in smol_fake_sdk())


def test_cleanup_failure_preserves_primary_error(smol_fake_sdk, monkeypatch, caplog):
    monkeypatch.setenv("SMOL_TEST_MODE", "cleanup_error")
    with pytest.raises(ValueError, match="primary") as error:
        with SmolWorkspace(server_image="test:server", cleanup_timeout=5):
            raise ValueError("primary")
    assert "delete_by_name" in " ".join(error.value.__notes__)
    assert "do-not-log-test-secret" not in caplog.text


def test_pause_resume_unsupported(smol_fake_sdk):
    with SmolWorkspace(server_image="test:server") as workspace:
        with pytest.raises(NotImplementedError, match="pause"):
            workspace.pause()
        with pytest.raises(NotImplementedError, match="resume"):
            workspace.resume()


def test_default_entrypoint_is_preserved(smol_fake_sdk):
    with SmolWorkspace(server_image="test:server"):
        create = next(c for c in smol_fake_sdk() if c["event"] == "create")
        assert create["command"] is None
        assert "user" not in create


def test_client_authentication_and_no_ambient_env_in_dump(smol_fake_sdk):
    with SmolWorkspace(server_image="test:server") as workspace:
        assert workspace.client.headers["X-Session-API-Key"] == workspace.api_key
        assert "OH_SECRET_KEY" not in json.dumps(workspace.model_dump())


def test_constructor_preserves_failure_when_cleanup_fails(smol_fake_sdk, monkeypatch):
    monkeypatch.setenv("SMOL_TEST_MODE", "create_cleanup_error")
    with pytest.raises(RuntimeError, match="image/VM startup") as error:
        SmolWorkspace(server_image="test:server", cleanup_timeout=3)
    assert "delete_by_name" in " ".join(error.value.__notes__)
    assert "do-not-log-test-secret" not in str(error.value)


def test_failed_close_can_be_retried(smol_fake_sdk, monkeypatch):
    monkeypatch.setenv("SMOL_TEST_MODE", "cleanup_error")
    workspace = SmolWorkspace(server_image="test:server", cleanup_timeout=3)
    with pytest.raises(RuntimeError, match=workspace.machine_id):
        workspace.close()
    monkeypatch.delenv("SMOL_TEST_MODE")
    workspace.close()
    calls = smol_fake_sdk()
    workspace.close()
    assert smol_fake_sdk() == calls


def test_closed_workspace_cannot_reopen_a_remote_client(smol_fake_sdk):
    workspace = SmolWorkspace(server_image="test:server")
    workspace.close()
    with pytest.raises(RuntimeError, match="closed"):
        workspace.execute_command("true")


def test_startup_cancellation_stops_owned_descendants(smol_fake_sdk, monkeypatch):
    import psutil

    monkeypatch.setenv("SMOL_TEST_MODE", "hang_create_child")
    with pytest.raises(RuntimeError, match="image/VM startup"):
        SmolWorkspace(server_image="test:server", startup_timeout=2, cleanup_timeout=3)
    pid = next(c["pid"] for c in smol_fake_sdk() if c["event"] == "child")
    if psutil.pid_exists(pid):
        assert psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
