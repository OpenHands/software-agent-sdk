"""Tests for AgentSandboxWorkspace (no cluster required)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def test_agent_sandbox_workspace_import():
    """AgentSandboxWorkspace can be imported from the package."""
    from openhands.workspace import AgentSandboxWorkspace

    assert AgentSandboxWorkspace is not None


def test_agent_sandbox_workspace_inheritance():
    """AgentSandboxWorkspace is a RemoteWorkspace."""
    from openhands.sdk.workspace import RemoteWorkspace
    from openhands.workspace import AgentSandboxWorkspace

    assert issubclass(AgentSandboxWorkspace, RemoteWorkspace)


def test_agent_sandbox_workspace_fields():
    """The key agent-sandbox knobs are exposed as fields."""
    from openhands.workspace import AgentSandboxWorkspace

    for field in ("warmpool", "namespace", "server_port", "connection", "host_port"):
        assert field in AgentSandboxWorkspace.model_fields


def test_direct_connection_requires_host():
    """connection='direct' without a host is rejected before any k8s call."""
    from openhands.workspace import AgentSandboxWorkspace

    with pytest.raises(ValueError, match="requires 'host'"):
        AgentSandboxWorkspace(warmpool="pool", connection="direct")


@pytest.fixture
def k8s_mocks():
    """Install a fake k8s_agent_sandbox module for the duration of the test."""
    fake_handle = MagicMock()
    fake_handle.sandbox_id = "sandbox-abc"
    fake_handle.claim_name = "sandbox-claim-abc"
    fake_handle.get_pod_name.return_value = "sandbox-abc-pod"

    fake_client = MagicMock()
    fake_client.create_sandbox.return_value = fake_handle

    fake_module = MagicMock()
    fake_module.SandboxClient.return_value = fake_client

    # `_patch_operating_mode` does `from k8s_agent_sandbox.constants import ...`.
    fake_constants = MagicMock(
        SANDBOX_API_GROUP="agents.x-k8s.io",
        SANDBOX_API_VERSION="v1beta1",
        SANDBOX_PLURAL_NAME="sandboxes",
    )

    with patch.dict(
        "sys.modules",
        {
            "k8s_agent_sandbox": fake_module,
            "k8s_agent_sandbox.constants": fake_constants,
        },
    ):
        yield SimpleNamespace(client=fake_client, handle=fake_handle)


def _make_ws(**overrides):
    """Construct a workspace with connection + parent init mocked out."""
    from openhands.workspace import AgentSandboxWorkspace

    kwargs = {"warmpool": "openhands-pool", "detach_logs": False}
    kwargs.update(overrides)
    with (
        patch.object(AgentSandboxWorkspace, "_start_port_forward"),
        patch.object(AgentSandboxWorkspace, "_wait_for_health"),
        # RemoteWorkspace.model_post_init would open a real HTTP client; skip it.
        patch(
            "openhands.sdk.workspace.remote.base.RemoteWorkspace.model_post_init",
            return_value=None,
        ),
    ):
        return AgentSandboxWorkspace(**kwargs)


def test_claims_from_warmpool_on_init(k8s_mocks):
    """model_post_init claims a sandbox from the configured warm pool."""
    _make_ws(namespace="ns1", shutdown_after_seconds=600)

    k8s_mocks.client.create_sandbox.assert_called_once()
    call = k8s_mocks.client.create_sandbox.call_args
    assert call.kwargs["warmpool"] == "openhands-pool"
    assert call.kwargs["namespace"] == "ns1"
    assert call.kwargs["shutdown_after_seconds"] == 600


def test_pause_and_resume_flip_operating_mode(k8s_mocks):
    """pause()/resume() patch the Sandbox operatingMode."""
    ws = _make_ws()
    patch_call = (
        k8s_mocks.client.k8s_helper.custom_objects_api.patch_namespaced_custom_object
    )

    with patch.object(type(ws), "_stop_port_forward"):
        ws.pause()
    assert patch_call.call_args.kwargs["body"] == {
        "spec": {"operatingMode": "Suspended"}
    }

    with (
        patch.object(type(ws), "_start_port_forward"),
        patch.object(type(ws), "_wait_for_health"),
    ):
        ws.resume()
    assert patch_call.call_args.kwargs["body"] == {"spec": {"operatingMode": "Running"}}


def test_cleanup_terminates_sandbox(k8s_mocks):
    """cleanup() deletes the claim via the handle and is idempotent."""
    ws = _make_ws()

    with patch.object(type(ws), "_stop_port_forward"):
        ws.cleanup()
        ws.cleanup()  # second call is a no-op

    k8s_mocks.handle.terminate.assert_called_once()
