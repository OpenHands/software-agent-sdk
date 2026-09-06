from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import _with_execution_workspace
from openhands.agent_server.execution_runtime import DockerExecutionWorkspace
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk.workspace import LocalWorkspace


def test_execution_only_server_exposes_tools_not_conversations(tmp_path, monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TMUX_TMPDIR", f"/tmp/oh-execution-test-{uuid4().hex}")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(
        Config(
            execution_only=True,
            execution_working_dir=workspace,
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/execution/tools",
            json={
                "tool_name": "terminal",
                "action": {"command": "pwd && printf isolated > proof.txt"},
            },
        )
        assert response.status_code == 200
        observation = response.json()["observation"]
        assert str(workspace) in observation["content"][0]["text"]
        assert observation["metadata"]["working_dir"] == str(workspace)
        assert (workspace / "proof.txt").read_text() == "isolated"
        assert client.get("/api/conversations/search").status_code == 404


def test_docker_execution_workspace_is_server_owned_and_lazy():
    workspace = DockerExecutionWorkspace(
        working_dir="/workspace",
        image="example.invalid/execution-runtime:test",
    )

    assert workspace.runs_conversation_remotely is False
    assert workspace._container_id is None
    assert workspace.model_dump(mode="json")["kind"] == "DockerExecutionWorkspace"
    workspace.api_key = "sandbox-only-token"
    assert "api_key" not in workspace.model_dump(mode="json")

    env_file = workspace._write_env_file("sandbox-only-token")
    try:
        assert env_file.stat().st_mode & 0o777 == 0o600
        assert env_file.read_text().splitlines() == [
            "OH_EXECUTION_ONLY=true",
            "OH_SESSION_API_KEYS_0=sandbox-only-token",
        ]
    finally:
        env_file.unlink()


def test_server_converts_local_workspace_to_execution_container():
    request = StartConversationRequest(
        agent_profile_id=UUID("00000000-0000-0000-0000-000000000001"),
        workspace=LocalWorkspace(working_dir="/host/workspace"),
        worktree=True,
    )

    converted = _with_execution_workspace(
        request,
        runtime="docker",
        image="execution:test",
        platform="linux/amd64",
        volumes=[],
    )

    assert isinstance(converted.workspace, DockerExecutionWorkspace)
    assert converted.workspace.working_dir == "/workspace"
    assert converted.workspace.image == "execution:test"
    assert converted.worktree is False


def test_start_request_accepts_legacy_local_workspace_payloads():
    profile_id = "00000000-0000-0000-0000-000000000001"
    for workspace in (
        {"working_dir": "/workspace"},
        {"type": "local", "working_dir": "/workspace"},
    ):
        request = StartConversationRequest.model_validate(
            {"agent_profile_id": profile_id, "workspace": workspace}
        )
        assert isinstance(request.workspace, LocalWorkspace)
