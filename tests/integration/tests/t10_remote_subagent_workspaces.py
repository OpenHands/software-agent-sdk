"""Real LLM delegation from a local parent into independently owned Docker sandboxes."""

import json
import os
import platform
import shlex
import subprocess
import time
from pathlib import Path
from threading import BoundedSemaphore
from uuid import uuid4

import httpx

from openhands.agent_server.docker.build import BuildOptions, build
from openhands.sdk import Agent
from openhands.sdk.conversation.response_utils import get_agent_final_response
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.subagent import AgentDefinition, register_agent
from openhands.sdk.tool import Tool, register_tool
from openhands.sdk.workspace import PlatformType
from openhands.tools.task.definition import TaskAction, TaskObservation, TaskToolSet
from openhands.workspace import DockerWorkspace
from tests.integration.base import BaseIntegrationTest, TestResult, get_tools_for_preset


INSTRUCTION = """Use the task tool to launch exactly two NEW remote-worker tasks.
Each child has its own sandbox containing input.json, with a token and a list of
numbers. Tell each child to read that file, compute the sum, write result.txt
containing exactly TOKEN:SUM followed by a newline, and return TOKEN:SUM to you.
The token must come from the child's file, not from guessing. Have children use
their terminal and finish tools. Launch both children in parallel if possible.

After both finish, RESUME each of those task IDs; do not create replacement tasks.
Every task call, including resume calls, must include a prompt describing the work.
Ask each resumed child to read its existing result.txt and append exactly
"resumed\\n" without inserting a blank line: the file already ends with a newline.
The final file must contain exactly two lines: TOKEN:SUM and resumed, each ending
with one newline. Have the child verify this and return its original TOKEN:SUM
again. Finish by reporting both
TOKEN:SUM values. Do not attempt to do the children's work in your local workspace.
"""


class RemoteSubagentWorkspacesTest(BaseIntegrationTest):
    INSTRUCTION = INSTRUCTION

    def __init__(self, *args, **kwargs):
        self.workspaces: dict[str, DockerWorkspace] = {}
        self.expected: dict[str, str] = {}
        self.container_ids: list[str] = []
        self.clients: list[httpx.Client] = []
        self.server_image = ""
        self._child_slots = BoundedSemaphore(2)
        self.docker_platform: PlatformType = (
            "linux/arm64"
            if platform.machine().lower() in {"arm64", "aarch64"}
            else "linux/amd64"
        )
        super().__init__(*args, **kwargs)

    @property
    def max_iteration_per_run(self) -> int:
        return 20

    @property
    def tools(self) -> list[Tool]:
        test = self

        class RemoteTaskToolSet(TaskToolSet):
            @classmethod
            def create(
                cls, conv_state, confirmation_handler=None, workspace_factory=None
            ):
                return super().create(
                    conv_state,
                    confirmation_handler=confirmation_handler,
                    workspace_factory=test.create_workspace,
                )

        register_tool("integration_remote_tasks", RemoteTaskToolSet)
        return [Tool(name="integration_remote_tasks")]

    def setup(self) -> None:
        subprocess.run(["docker", "info"], check=True, capture_output=True)
        # Build this checkout, not a published image that may lack the feature.
        self.server_image = (
            os.environ.get("AGENT_SERVER_IMAGE")
            or build(
                BuildOptions(
                    target="source-minimal",
                    platforms=[self.docker_platform],
                    install_acp_providers="",
                    install_capabilities="",
                    push=False,
                )
            )[0]
        )
        register_agent(
            "remote-worker",
            lambda llm: Agent(
                llm=llm,
                tools=get_tools_for_preset(self.tool_preset, enable_browser=False),
            ),
            AgentDefinition(
                name="remote-worker",
                description="Worker with an independently provisioned remote sandbox",
                max_iteration_per_run=12,
                max_budget_per_run=2.0,
            ),
        )
        (Path(self.workspace) / "result.txt").write_text("parent sentinel\n")

    def create_workspace(self, child_id: str, agent_type: str) -> DockerWorkspace:
        assert agent_type == "remote-worker"
        assert self._child_slots.acquire(blocking=False), (
            "Only two child sandboxes are allowed; resume the existing tasks"
        )
        workspace = None
        try:
            workspace = DockerWorkspace(
                server_image=self.server_image,
                platform=self.docker_platform,
                working_dir="/workspace",
            )
            token = uuid4().hex
            numbers = [
                int(token[offset : offset + 4], 16) for offset in range(0, 20, 4)
            ]
            payload = shlex.quote(json.dumps({"token": token, "numbers": numbers}))
            result = workspace.execute_command(
                f"printf %s {payload} > /workspace/input.json"
            )
            assert result.exit_code == 0, result.stderr
            assert workspace._container_id is not None
            self.container_ids.append(workspace._container_id)
            self.clients.append(workspace.client)
            self.expected[child_id] = f"{token}:{sum(numbers)}"
            self.workspaces[child_id] = workspace
            return workspace
        except BaseException:
            self._child_slots.release()
            if workspace is not None:
                try:
                    workspace.__exit__(None, None, None)
                finally:
                    workspace.reset_client()
            raise

    def verify_result(self) -> TestResult:
        assert len(self.workspaces) == 2, (
            "Parent must create exactly two remote children"
        )
        assert len(set(self.container_ids)) == 2, (
            "Children must own different containers"
        )
        assert len({workspace.host for workspace in self.workspaces.values()}) == 2
        actions = [
            event.action
            for event in self.collected_events
            if isinstance(event, ActionEvent) and isinstance(event.action, TaskAction)
        ]
        observations = [
            event.observation
            for event in self.collected_events
            if isinstance(event, ObservationEvent)
            and isinstance(event.observation, TaskObservation)
        ]
        assert len([action for action in actions if not action.resume]) == 2
        assert {action.resume for action in actions if action.resume} == set(
            self.workspaces
        ), "Both original children must be resumed"
        assert observations and all(not result.is_error for result in observations), [
            result.text for result in observations
        ]
        final_response = get_agent_final_response(self.collected_events)
        for child_id, workspace in self.workspaces.items():
            expected = self.expected[child_id]
            returned = [result for result in observations if result.task_id == child_id]
            assert len(returned) >= 2 and all(
                expected in result.text for result in returned
            )
            assert expected in final_response, (
                "Parent must receive and report child results"
            )
            result = workspace.execute_command("cat /workspace/result.txt")
            assert result.exit_code == 0, result.stderr
            assert result.stdout == f"{expected}\nresumed\n", (
                f"Unexpected file contents for {child_id}: {result.stdout!r}"
            )
            assert workspace.conversation_id is not None
            response = workspace.client.get(
                f"/api/conversations/{workspace.conversation_id}/events/search"
            )
            response.raise_for_status()
            tool_names = {
                event["tool_name"]
                for event in response.json()["items"]
                if event["kind"] == "ActionEvent"
            }
            assert {"terminal", "finish"} <= tool_names, tool_names
        assert (Path(self.workspace) / "result.txt").read_text() == "parent sentinel\n"

        self.conversation.close()
        assert all(client.is_closed for client in self.clients)
        for container_id in self.container_ids:
            # Docker's --rm removal can finish after `docker stop` returns.
            deadline = time.monotonic() + 10
            while True:
                remaining = subprocess.run(
                    ["docker", "ps", "-aq", "--filter", f"id={container_id}"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if not remaining.stdout.strip():
                    break
                assert time.monotonic() < deadline, (
                    f"Leaked child container {container_id}"
                )
                time.sleep(0.2)
        return TestResult(
            success=True,
            reason="Real LLM parent delegated to two Docker children, resumed both, "
            "received their private results, and released both sandboxes.",
        )

    def teardown(self):
        try:
            super().teardown()
        finally:
            # The runner reads self.llm.metrics; include the delegated LLM calls.
            for child_id in self.workspaces:
                metrics = self.conversation.conversation_stats.usage_to_metrics.get(
                    f"task:{child_id}"
                )
                if metrics is not None:
                    self.llm.metrics.merge(metrics)
            # Retry infrastructure cleanup if the lifecycle assertion failed.
            for workspace in self.workspaces.values():
                try:
                    if workspace._container_id is not None:
                        workspace.__exit__(None, None, None)
                finally:
                    workspace.reset_client()
