import hashlib
import logging
import os
import socket
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from pydantic import Field, PrivateAttr

from openhands.sdk.tool import Action, Observation, ToolDefinition, ToolExecutor
from openhands.sdk.tool.builtins import (
    FinishExecutor,
    FinishTool,
    SwitchLLMExecutor,
    SwitchLLMTool,
    ThinkExecutor,
    ThinkTool,
    VisionInspectExecutor,
    VisionInspectTool,
)
from openhands.sdk.tool.registry import get_registered_tool_factory
from openhands.sdk.utils.command import execute_command
from openhands.sdk.workspace import RemoteWorkspace
from openhands.tools.browser_use.definition import (
    BrowserClickTool,
    BrowserCloseTabTool,
    BrowserGetContentTool,
    BrowserGetStateTool,
    BrowserGetStorageTool,
    BrowserGoBackTool,
    BrowserListTabsTool,
    BrowserNavigateTool,
    BrowserObservation,
    BrowserScrollTool,
    BrowserSetStorageTool,
    BrowserStartRecordingTool,
    BrowserStopRecordingTool,
    BrowserSwitchTabTool,
    BrowserToolSet,
    BrowserTypeTool,
)
from openhands.tools.file_editor.definition import FileEditorObservation, FileEditorTool
from openhands.tools.glob.definition import GlobObservation, GlobTool
from openhands.tools.grep.definition import GrepObservation, GrepTool
from openhands.tools.task_tracker.definition import TaskTrackerExecutor, TaskTrackerTool
from openhands.tools.terminal.definition import TerminalObservation, TerminalTool


logger = logging.getLogger(__name__)
_EXECUTION_SCOPE_LABEL = "ai.openhands.execution-scope"


def execution_scope_for(conversations_dir: Path) -> str:
    canonical_path = str(conversations_dir.expanduser().resolve())
    return hashlib.sha256(canonical_path.encode()).hexdigest()[:24]


def cleanup_execution_containers(execution_scope: str) -> None:
    result = execute_command(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label={_EXECUTION_SCOPE_LABEL}={execution_scope}",
        ]
    )
    if result.returncode != 0:
        logger.warning(
            "Failed to list stale Docker execution containers: %s", result.stderr
        )
        return
    container_ids = result.stdout.split()
    if not container_ids:
        return
    cleanup = execute_command(["docker", "rm", "-f", *container_ids])
    if cleanup.returncode != 0:
        logger.warning(
            "Failed to remove stale Docker execution containers: %s", cleanup.stderr
        )


_REMOTE_TOOL_NAMES = frozenset(
    {
        "terminal",
        "file_editor",
        "grep",
        "glob",
        "apply_patch",
        "browser_navigate",
        "browser_click",
        "browser_get_state",
        "browser_get_content",
        "browser_type",
        "browser_scroll",
        "browser_go_back",
        "browser_list_tabs",
        "browser_switch_tab",
        "browser_close_tab",
        "browser_get_storage",
        "browser_set_storage",
        "browser_start_recording",
        "browser_stop_recording",
    }
)
_CANONICAL_TOOL_FACTORIES = {
    TerminalTool.name: TerminalTool,
    FileEditorTool.name: FileEditorTool,
    GrepTool.name: GrepTool,
    GlobTool.name: GlobTool,
    TaskTrackerTool.name: TaskTrackerTool,
    BrowserToolSet.name: BrowserToolSet,
    FinishTool.__name__: FinishTool,
    SwitchLLMTool.__name__: SwitchLLMTool,
    ThinkTool.__name__: ThinkTool,
    VisionInspectTool.__name__: VisionInspectTool,
}
_REMOTE_TOOL_TYPES = {
    TerminalTool.name: TerminalTool,
    FileEditorTool.name: FileEditorTool,
    GrepTool.name: GrepTool,
    GlobTool.name: GlobTool,
    BrowserNavigateTool.name: BrowserNavigateTool,
    BrowserClickTool.name: BrowserClickTool,
    BrowserGetStateTool.name: BrowserGetStateTool,
    BrowserGetContentTool.name: BrowserGetContentTool,
    BrowserTypeTool.name: BrowserTypeTool,
    BrowserScrollTool.name: BrowserScrollTool,
    BrowserGoBackTool.name: BrowserGoBackTool,
    BrowserListTabsTool.name: BrowserListTabsTool,
    BrowserSwitchTabTool.name: BrowserSwitchTabTool,
    BrowserCloseTabTool.name: BrowserCloseTabTool,
    BrowserGetStorageTool.name: BrowserGetStorageTool,
    BrowserSetStorageTool.name: BrowserSetStorageTool,
    BrowserStartRecordingTool.name: BrowserStartRecordingTool,
    BrowserStopRecordingTool.name: BrowserStopRecordingTool,
}
_CONTROL_PLANE_TOOLS = {
    FinishTool.__name__: (FinishTool, FinishExecutor),
    TaskTrackerTool.__name__: (TaskTrackerTool, TaskTrackerExecutor),
    SwitchLLMTool.__name__: (SwitchLLMTool, SwitchLLMExecutor),
    ThinkTool.__name__: (ThinkTool, ThinkExecutor),
    VisionInspectTool.__name__: (VisionInspectTool, VisionInspectExecutor),
}

_REMOTE_OBSERVATION_TYPES = {
    TerminalTool.name: TerminalObservation,
    FileEditorTool.name: FileEditorObservation,
    GrepTool.name: GrepObservation,
    GlobTool.name: GlobObservation,
    **{
        tool_name: BrowserObservation
        for tool_name in _REMOTE_TOOL_NAMES
        if tool_name.startswith("browser_")
    },
}


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
        if self.tool_name == "apply_patch":
            from openhands.tools.apply_patch.definition import ApplyPatchObservation

            observation_type = ApplyPatchObservation
        else:
            observation_type = _REMOTE_OBSERVATION_TYPES[self.tool_name]
        return observation_type.model_validate(response.json()["observation"])


class DockerExecutionWorkspace(RemoteWorkspace):
    """Per-conversation Docker sandbox that hosts tool execution only."""

    working_dir: str = "/workspace"
    host: str = ""
    image: str = "ghcr.io/openhands/agent-server:latest-python"
    platform: str = "linux/amd64"
    api_key: str | None = Field(default=None, exclude=True)

    health_check_timeout: float = 120.0

    _execution_scope: str | None = PrivateAttr(default=None)
    _container_id: str | None = PrivateAttr(default=None)
    _startup_lock: threading.RLock = PrivateAttr(default_factory=threading.RLock)

    def set_execution_scope(self, execution_scope: str) -> None:
        self._execution_scope = execution_scope

    @property
    def runs_conversation_remotely(self) -> bool:
        return False

    @property
    def allows_runtime_extensions(self) -> bool:
        return False

    def validate_agent(self, agent: object) -> None:
        from openhands.sdk.agent.agent import Agent

        if type(agent) is not Agent:
            raise ValueError(
                "Docker execution mode only supports the canonical OpenHands Agent; "
                "custom and ACP agents can execute code in the trusted host"
            )
        context = agent.agent_context
        if context is not None and (
            context.skills
            or context.load_public_skills
            or context.load_user_skills
            or context.load_project_skills
            or context.load_memory
            or context.registered_marketplaces
        ):
            raise ValueError(
                "Docker execution mode does not permit skills, memory, or marketplace "
                "discovery because they can read or execute host content"
            )

    def validate_tool_spec(self, tool_name: str) -> None:
        if tool_name == "apply_patch":
            from openhands.tools.apply_patch.definition import ApplyPatchTool

            expected = ApplyPatchTool
        else:
            expected = _CANONICAL_TOOL_FACTORIES.get(tool_name)
        registered = get_registered_tool_factory(tool_name)
        if expected is not None and (
            registered is expected
            or (tool_name in _CONTROL_PLANE_TOOLS and registered is None)
        ):
            return
        raise ValueError(
            f"Tool '{tool_name}' is not supported by Docker execution. "
            "Custom and host-executed tools are disabled in this mode."
        )

    def validate_tool(self, tool: ToolDefinition) -> None:
        executor = tool.executor
        if tool.name == "apply_patch":
            from openhands.tools.apply_patch.definition import ApplyPatchTool

            expected_remote_type = ApplyPatchTool
        else:
            expected_remote_type = _REMOTE_TOOL_TYPES.get(tool.name)
        if (
            expected_remote_type is not None
            and type(tool) is expected_remote_type
            and isinstance(executor, RemoteExecutionToolExecutor)
            and executor.workspace is self
            and executor.tool_name == tool.name
        ):
            return
        control_plane = _CONTROL_PLANE_TOOLS.get(type(tool).__name__)
        if (
            control_plane is not None
            and type(tool) is control_plane[0]
            and type(executor) is control_plane[1]
        ):
            return
        raise ValueError(
            f"Tool '{tool.name}' cannot execute in the trusted host while Docker "
            "execution is enabled"
        )

    def validate_runtime_extensions(
        self,
        *,
        tool_module_qualnames: dict[str, str] | None = None,
        plugins: list[object] | None = None,
        hook_config: object | None = None,
        mcp_config: dict[str, object] | None = None,
    ) -> None:
        if tool_module_qualnames:
            raise ValueError("Docker execution does not permit custom tool modules")
        if plugins:
            raise ValueError("Docker execution does not permit plugins")
        if hook_config is not None:
            raise ValueError("Docker execution does not permit hooks")
        if mcp_config:
            raise ValueError("Docker execution does not permit MCP servers")

    def _docker_run_command(self, port: int, env_file: Path) -> list[str]:
        if self._execution_scope is None:
            raise RuntimeError(
                "Docker execution workspace has no server ownership scope"
            )
        return [
            "docker",
            "run",
            "-d",
            "--platform",
            self.platform,
            "--name",
            f"openhands-execution-{uuid.uuid4()}",
            "--label",
            f"{_EXECUTION_SCOPE_LABEL}={self._execution_scope}",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/workspace:rw,exec,nosuid,nodev,uid=10001,gid=10001,mode=0775",
            "--tmpfs",
            "/tmp:rw,exec,nosuid,nodev,size=512m,mode=1777",
            "--pids-limit",
            "512",
            "-p",
            f"127.0.0.1:{port}:8000",
            "--env-file",
            str(env_file),
            self.image,
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ]

    def _ensure_started(self) -> None:
        with self._startup_lock:
            if self._container_id is not None:
                return
            port = self._available_port()
            sandbox_api_key = uuid.uuid4().hex
            env_file = self._write_env_file(sandbox_api_key)
            try:
                result = execute_command(self._docker_run_command(port, env_file))
            finally:
                env_file.unlink(missing_ok=True)
            if result.returncode != 0:
                raise RuntimeError(
                    f"Failed to start execution sandbox: {result.stderr}"
                )
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
        next_status_check = time.monotonic()
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
                now = time.monotonic()
                if self._container_id is not None and now >= next_status_check:
                    status = execute_command(
                        [
                            "docker",
                            "inspect",
                            "--format",
                            "{{.State.Status}}",
                            self._container_id,
                        ]
                    )
                    if status.returncode != 0 or status.stdout.strip() == "exited":
                        break
                    next_status_check = now + 1
                time.sleep(0.25)

        container_id = self._container_id
        logs = ""
        if container_id is not None:
            result = execute_command(["docker", "logs", "--tail", "100", container_id])
            logs = "\n".join(
                stream.strip() for stream in (result.stdout, result.stderr) if stream
            )
        self.close()
        detail = f": {logs}" if logs else ""
        raise RuntimeError(f"Execution sandbox did not become healthy{detail}")

    def create_tool_executor(self, tool_name: str) -> ToolExecutor | None:
        if tool_name not in _REMOTE_TOOL_NAMES:
            return None
        self._ensure_started()
        return RemoteExecutionToolExecutor(self, tool_name)

    def close(self) -> None:
        with self._startup_lock:
            self.reset_client()
            if self._container_id is not None:
                execute_command(["docker", "rm", "-f", self._container_id])
                self._container_id = None

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._send_completion_callback(exc_type, exc_val)
        self.close()
