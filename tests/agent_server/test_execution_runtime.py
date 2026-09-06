import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import (
    ConversationService,
    _validate_execution_workspace,
)
from openhands.agent_server.execution_runtime import DockerExecutionWorkspace
from openhands.agent_server.execution_runtime.workspace import (
    RemoteExecutionToolExecutor,
    cleanup_execution_containers,
    execution_scope_for,
)
from openhands.agent_server.models import StartConversationRequest, StoredConversation
from openhands.sdk import LLM, Conversation
from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.tool import Action, Observation, ToolDefinition, ToolExecutor
from openhands.sdk.tool.builtins import FinishTool
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.task_tracker.definition import TaskTrackerExecutor, TaskTrackerTool
from openhands.tools.terminal.definition import (
    TerminalAction,
    TerminalObservation,
    TerminalTool,
)


class _HostExecutor(ToolExecutor):
    def __call__(self, action, conversation=None):
        raise AssertionError("host executor must never run")


class _HostTool(ToolDefinition):
    name = "host_tool"

    @classmethod
    def create(cls, *args, **kwargs):
        return [
            cls(
                description="unsafe host tool",
                action_type=Action,
                observation_type=Observation,
                executor=_HostExecutor(),
            )
        ]


def _terminal_with_executor(executor: ToolExecutor) -> TerminalTool:
    return TerminalTool(
        description="terminal",
        action_type=TerminalAction,
        observation_type=TerminalObservation,
        executor=executor,
    )


class _TrackingDockerWorkspace(DockerExecutionWorkspace):
    close_calls: int = 0

    def close(self) -> None:
        self.close_calls += 1
        super().close()


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


def test_execution_only_server_rejects_task_tracker(tmp_path):
    app = create_app(
        Config(
            execution_only=True,
            execution_working_dir=tmp_path,
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/execution/tools",
            json={"tool_name": "task_tracker", "action": {"command": "view"}},
        )

    assert response.status_code == 422


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


def test_server_accepts_only_its_configured_workspace_variety():
    profile_id = UUID("00000000-0000-0000-0000-000000000001")
    local_request = StartConversationRequest(
        agent_profile_id=profile_id,
        workspace=LocalWorkspace(working_dir="/host/workspace"),
    )
    docker_request = StartConversationRequest(
        agent_profile_id=profile_id,
        workspace=DockerExecutionWorkspace(
            working_dir="/workspace",
            image="client-controlled:latest",
            platform="linux/arm64",
        ),
        worktree=True,
    )

    assert (
        _validate_execution_workspace(
            local_request,
            runtime="local",
            image="execution:test",
            platform="linux/amd64",
            volumes=[],
            execution_scope="test-scope",
        )
        is local_request
    )
    validated_docker = _validate_execution_workspace(
        docker_request,
        runtime="docker",
        image="execution:test",
        platform="linux/amd64",
        volumes=[],
        execution_scope="test-scope",
    )
    assert type(validated_docker.workspace) is DockerExecutionWorkspace
    assert validated_docker.workspace.image == "execution:test"
    assert validated_docker.workspace.platform == "linux/amd64"
    assert validated_docker.worktree is False
    with pytest.raises(ValueError, match="only opens DockerExecutionWorkspace"):
        _validate_execution_workspace(
            local_request,
            runtime="docker",
            image="execution:test",
            platform="linux/amd64",
            volumes=[],
            execution_scope="test-scope",
        )
    with pytest.raises(ValueError, match="only opens LocalWorkspace"):
        _validate_execution_workspace(
            docker_request,
            runtime="local",
            image="execution:test",
            platform="linux/amd64",
            volumes=[],
            execution_scope="test-scope",
        )


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


def test_start_request_accepts_explicit_docker_workspace_payload():
    request = StartConversationRequest.model_validate(
        {
            "agent_profile_id": "00000000-0000-0000-0000-000000000001",
            "workspace": {
                "kind": "DockerExecutionWorkspace",
                "working_dir": "/workspace",
            },
        }
    )

    assert type(request.workspace) is DockerExecutionWorkspace


def test_server_restores_execution_scope_after_workspace_serialization():
    workspace = DockerExecutionWorkspace(working_dir="/workspace")
    workspace.set_execution_scope("deployment-scope")
    stored = StoredConversation(
        id=uuid4(),
        workspace=DockerExecutionWorkspace.model_validate(
            workspace.model_dump(mode="json")
        ),
    )

    assert type(stored.workspace) is DockerExecutionWorkspace
    assert stored.workspace._execution_scope is None

    validated = _validate_execution_workspace(
        stored,
        runtime="docker",
        image="execution:test",
        platform="linux/amd64",
        volumes=[],
        execution_scope="deployment-scope",
    )

    assert type(validated.workspace) is DockerExecutionWorkspace
    assert validated.workspace._execution_scope == "deployment-scope"


def test_docker_workspace_rejects_host_tool_executors():
    workspace = DockerExecutionWorkspace()
    tool = _HostTool.create()[0]

    with pytest.raises(ValueError, match="cannot execute in the trusted host"):
        workspace.validate_tool(tool)


def test_docker_workspace_allows_control_plane_executors_only_by_identity():
    workspace = DockerExecutionWorkspace()
    [finish] = FinishTool.create()
    remote_impostor = _HostTool.create()[0].set_executor(
        RemoteExecutionToolExecutor(workspace, "host_tool")
    )

    workspace.validate_tool(finish)
    with pytest.raises(ValueError, match="cannot execute in the trusted host"):
        workspace.validate_tool(remote_impostor)


def test_docker_workspace_keeps_task_tracker_persistent_in_control_plane(tmp_path):
    workspace = DockerExecutionWorkspace()
    agent = Agent(
        llm=LLM(model="test-model", usage_id="test-llm"),
        tools=[],
        include_default_tools=[],
    )
    state = ConversationState(
        id=uuid4(),
        agent=agent,
        workspace=workspace,
        persistence_dir=str(tmp_path),
    )

    [task_tracker] = TaskTrackerTool.create(state)

    workspace.validate_tool(task_tracker)
    assert type(task_tracker.executor) is TaskTrackerExecutor
    assert task_tracker.executor.save_dir == tmp_path
    assert workspace._container_id is None


def test_docker_workspace_rejects_remote_executor_from_another_workspace():
    workspace = DockerExecutionWorkspace()
    other_workspace = DockerExecutionWorkspace()
    terminal = _terminal_with_executor(
        RemoteExecutionToolExecutor(other_workspace, TerminalTool.name)
    )

    with pytest.raises(ValueError, match="cannot execute in the trusted host"):
        workspace.validate_tool(terminal)


def test_docker_workspace_rejects_remote_executor_with_mismatched_route():
    workspace = DockerExecutionWorkspace()
    terminal = _terminal_with_executor(
        RemoteExecutionToolExecutor(workspace, "file_editor")
    )

    with pytest.raises(ValueError, match="cannot execute in the trusted host"):
        workspace.validate_tool(terminal)


def test_docker_workspace_rejects_subclass_using_canonical_name():
    class _TerminalImpostor(TerminalTool):
        pass

    workspace = DockerExecutionWorkspace()
    terminal = _TerminalImpostor(
        description="terminal impostor",
        action_type=TerminalAction,
        observation_type=TerminalObservation,
        executor=RemoteExecutionToolExecutor(workspace, TerminalTool.name),
    )

    with pytest.raises(ValueError, match="cannot execute in the trusted host"):
        workspace.validate_tool(terminal)


def test_docker_workspace_rejects_host_runtime_tool_injection():
    workspace = DockerExecutionWorkspace()
    agent = Agent(
        llm=LLM(model="test-model", usage_id="test-llm"),
        tools=[],
        include_default_tools=[],
    )
    conversation = Conversation(agent=agent, workspace=workspace, visualizer=None)
    assert isinstance(conversation, LocalConversation)
    conversation._ensure_agent_ready()

    with pytest.raises(ValueError, match="cannot execute in the trusted host"):
        agent.add_runtime_tools(_HostTool.create())


def test_conversation_close_releases_workspace_once():
    workspace = _TrackingDockerWorkspace()
    conversation = Conversation(
        agent=Agent(
            llm=LLM(model="test-model", usage_id="test-llm"),
            tools=[],
            include_default_tools=[],
        ),
        workspace=workspace,
        visualizer=None,
    )

    conversation.close()
    conversation.close()

    assert workspace.close_calls == 1


def test_docker_workspace_rejects_unsupported_tool_specs_before_factory_runs():
    workspace = DockerExecutionWorkspace()

    with pytest.raises(ValueError, match="is not supported by Docker execution"):
        workspace.validate_tool_spec("host_tool")


def test_docker_workspace_disables_host_runtime_extensions():
    workspace = DockerExecutionWorkspace()

    with pytest.raises(ValueError, match="custom tool modules"):
        workspace.validate_runtime_extensions(tool_module_qualnames={"x": "pkg.x"})
    with pytest.raises(ValueError, match="plugins"):
        workspace.validate_runtime_extensions(plugins=[object()])
    with pytest.raises(ValueError, match="hooks"):
        workspace.validate_runtime_extensions(hook_config=object())
    with pytest.raises(ValueError, match="MCP"):
        workspace.validate_runtime_extensions(mcp_config={"server": object()})


def test_docker_workspace_rejects_registry_override_of_control_plane_name(
    monkeypatch,
):
    monkeypatch.setattr(
        "openhands.agent_server.execution_runtime.workspace.get_registered_tool_factory",
        lambda name: _HostTool if name == FinishTool.__name__ else None,
    )

    with pytest.raises(ValueError, match="is not supported by Docker execution"):
        DockerExecutionWorkspace().validate_tool_spec(FinishTool.__name__)


def test_docker_resume_rejects_custom_runtime_before_import(tmp_path, monkeypatch):
    imported = []
    monkeypatch.setattr(
        "openhands.agent_server.conversation_service.importlib.import_module",
        imported.append,
    )
    service = ConversationService(
        conversations_dir=tmp_path,
        execution_runtime="docker",
    )
    stored = StoredConversation(
        id=uuid4(),
        workspace=LocalWorkspace(working_dir="/workspace"),
        tool_module_qualnames={"host_tool": "malicious.module"},
    )

    with pytest.raises(ValueError, match="cannot resume conversations"):
        service._prepare_persisted_runtime(stored)

    assert imported == []


def test_docker_server_rejects_persisted_local_workspace():
    stored = StoredConversation(
        id=uuid4(),
        workspace=LocalWorkspace(working_dir="/host/workspace"),
        worktree=True,
    )

    with pytest.raises(ValueError, match="only opens DockerExecutionWorkspace"):
        _validate_execution_workspace(
            stored,
            runtime="docker",
            image="execution:test",
            platform="linux/amd64",
            volumes=[],
            execution_scope="test-scope",
        )


def test_server_refuses_host_mounts_for_docker_execution():
    request = StartConversationRequest(
        agent_profile_id=UUID("00000000-0000-0000-0000-000000000001"),
        workspace=DockerExecutionWorkspace(
            working_dir="/workspace",
            image="execution:test",
        ),
        worktree=False,
    )

    with pytest.raises(ValueError, match="host volume mounts are forbidden"):
        _validate_execution_workspace(
            request,
            runtime="docker",
            image="execution:test",
            platform="linux/amd64",
            volumes=["/home:/workspace"],
            execution_scope="test-scope",
        )


def test_docker_command_uses_defense_in_depth_flags(tmp_path):
    workspace = DockerExecutionWorkspace(image="execution:test")
    workspace.set_execution_scope("deployment-scope")
    command = workspace._docker_run_command(12345, tmp_path / "env")

    assert command[:3] == ["docker", "run", "-d"]
    assert "ai.openhands.execution-scope=deployment-scope" in command
    assert ["--cap-drop", "ALL"] == command[
        command.index("--cap-drop") : command.index("--cap-drop") + 2
    ]
    assert "--rm" not in command
    assert "no-new-privileges" in command
    assert "/workspace:rw,exec,nosuid,nodev,uid=10001,gid=10001,mode=0775" in command
    assert "/tmp:rw,exec,nosuid,nodev,size=512m,mode=1777" in command
    assert "-v" not in command


def test_execution_scope_is_stable_and_separates_deployments(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"

    assert execution_scope_for(first) == execution_scope_for(first)
    assert execution_scope_for(first) != execution_scope_for(second)


def test_cleanup_removes_only_containers_in_its_execution_scope(monkeypatch):
    commands = []
    results = iter(
        [
            subprocess.CompletedProcess(
                [], 0, stdout="container-a\ncontainer-b\n", stderr=""
            ),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        ]
    )

    def capture(command):
        commands.append(command)
        return next(results)

    monkeypatch.setattr(
        "openhands.agent_server.execution_runtime.workspace.execute_command",
        capture,
    )

    cleanup_execution_containers("deployment-scope")

    assert commands == [
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=ai.openhands.execution-scope=deployment-scope",
        ],
        ["docker", "rm", "-f", "container-a", "container-b"],
    ]


def test_docker_workspace_reports_early_exit_logs_and_cleans_up(monkeypatch):
    commands = []

    def fake_execute(command):
        commands.append(command)
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, "exited\n", "")
        if command[1] == "logs":
            return subprocess.CompletedProcess(
                command, 0, "stdout-marker\n", "startup-marker\n"
            )
        if command[1:3] == ["rm", "-f"]:
            return subprocess.CompletedProcess(command, 0, "container-id\n", "")
        raise AssertionError(command)

    monkeypatch.setattr(
        "openhands.agent_server.execution_runtime.workspace.execute_command",
        fake_execute,
    )
    workspace = DockerExecutionWorkspace(health_check_timeout=30)
    workspace._container_id = "container-id"
    workspace.host = "http://127.0.0.1:1"

    with pytest.raises(RuntimeError, match="startup-marker") as exc_info:
        workspace._wait_for_health()

    assert "stdout-marker" in str(exc_info.value)
    assert workspace._container_id is None
    assert ["docker", "logs", "--tail", "100", "container-id"] in commands
    assert ["docker", "rm", "-f", "container-id"] in commands


def test_docker_workspace_starts_once_under_parallel_tool_resolution(monkeypatch):
    starts = 0

    def fake_execute(command):
        nonlocal starts
        if command[1] == "run":
            starts += 1
            time.sleep(0.05)
            return subprocess.CompletedProcess(command, 0, "container-id\n", "")
        if command[1:3] == ["rm", "-f"]:
            return subprocess.CompletedProcess(command, 0, "container-id\n", "")
        raise AssertionError(command)

    monkeypatch.setattr(
        "openhands.agent_server.execution_runtime.workspace.execute_command",
        fake_execute,
    )
    monkeypatch.setattr(DockerExecutionWorkspace, "_wait_for_health", lambda self: None)
    workspace = DockerExecutionWorkspace()
    workspace.set_execution_scope("test-scope")

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            executors = list(
                pool.map(
                    workspace.create_tool_executor,
                    ["terminal", "file_editor", "grep", "browser_navigate"],
                )
            )
        assert all(executor is not None for executor in executors)
        assert starts == 1
    finally:
        workspace.close()
