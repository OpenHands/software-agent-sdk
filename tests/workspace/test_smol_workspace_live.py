"""Opt-in real microVM contracts. No model credentials or paid inference.

OPENHANDS_SMOL_SERVER_IMAGE=image@sha256:... uv run pytest -m smol_live \
    tests/workspace/test_smol_workspace_live.py
"""

import importlib
import json
import os
import shlex
import shutil
import socket
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, Event, RemoteConversation
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import Tool
from openhands.sdk.workspace import RemoteWorkspace
from openhands.tools.terminal import TerminalTool
from openhands.workspace import SmolWorkspace


pytestmark = pytest.mark.smol_live


@pytest.fixture
def smol_image(monkeypatch: pytest.MonkeyPatch) -> str:
    image = os.environ.get("OPENHANDS_SMOL_SERVER_IMAGE")
    if not image:
        pytest.skip("set OPENHANDS_SMOL_SERVER_IMAGE to a published compatible image")
    monkeypatch.setenv("SMOL_CLOUD_TOKEN", "unused-live-test-token")
    monkeypatch.setenv("SMOL_HOST_TEST", "ambient-test-value")
    return image


def assert_removed(identifier: str | None) -> None:
    smol = importlib.import_module("smol")

    names = {m.name for m in smol.Machine.list(smol.ConnectOptions(target="local"))}
    assert identifier not in names


def test_live_isolation_auth_commands_files_and_git(smol_image, tmp_path):
    with (
        SmolWorkspace(
            server_image=smol_image,
            network=False,
            server_env={"SMOL_SERVER_TEST": "opt-in"},
        ) as first,
        SmolWorkspace(server_image=smol_image, network=False) as second,
    ):
        assert first.machine_id != second.machine_id
        assert first.api_key != second.api_key
        assert first.endpoint != second.endpoint
        address = httpx.URL(first.endpoint)
        assert address.host == "127.0.0.1" and address.port is not None
        with socket.socket() as contender:
            with pytest.raises(OSError):
                contender.bind(("127.0.0.1", address.port))
        with httpx.Client(trust_env=False) as client:
            response = client.post(
                first.endpoint + "/api/bash/execute_bash_command",
                json={"command": "true"},
            )
            assert response.status_code in (401, 403)
        # Verify the listening addresses, including IPv6, on the real host.
        import psutil

        listeners = [
            c.laddr.ip
            for process in psutil.Process().children(recursive=True)
            for c in process.net_connections("tcp")
            if c.laddr and c.status == "LISTEN" and c.laddr.port == address.port
        ]
        assert listeners and set(listeners) <= {"127.0.0.1", "::1"}
        expected_user = os.environ.get("OPENHANDS_SMOL_IMAGE_USER", "openhands")
        assert first.execute_command("id -un").stdout.strip() == expected_user
        assert (
            first.execute_command(
                'printf \'%s:%s\' "$SMOL_SERVER_TEST" "${SMOL_HOST_TEST-unset}"'
            ).stdout
            == "opt-in:unset"
        )
        result = first.execute_command("pwd; printf 'out'; printf 'err' >&2; exit 7")
        assert result.exit_code == 7
        assert result.stdout == "/workspace\nout"
        assert result.stderr == "err"
        result = first.execute_command("pwd", cwd="/tmp")
        assert result.exit_code == 0 and result.stdout.strip() == "/tmp"
        assert first.execute_command("touch private-file").exit_code == 0
        assert second.execute_command("test -e private-file").exit_code == 1
        source = tmp_path / "niño-文件.bin"
        source.write_bytes(bytes(range(256)) + "á漢字\n".encode())
        assert first.file_upload(source, "/workspace/niño-文件.bin").success
        destination = tmp_path / "roundtrip.bin"
        assert first.file_download("/workspace/niño-文件.bin", destination).success
        assert destination.read_bytes() == source.read_bytes()
        assert (
            first.execute_command(
                "git init -q /workspace/repo && cd /workspace/repo && "
                "git -c user.name=Test -c user.email=test@example.com "
                "commit --allow-empty -qm initial && printf changed > tracked"
            ).exit_code
            == 0
        )
        assert first.git_changes("/workspace/repo")
        assert (
            first.execute_command(
                "cd /workspace/repo && git add tracked && "
                "git -c user.name=Test -c user.email=test@example.com "
                "commit -qm file && "
                "printf updated > tracked"
            ).exit_code
            == 0
        )
        diff = first.git_diff("/workspace/repo/tracked")
        assert diff.original == "changed" and diff.modified == "updated"
        start = time.monotonic()
        timed = first.execute_command("sleep 30", timeout=1)
        assert timed.exit_code != 0 and time.monotonic() - start < 10
        first_id, second_id = first.machine_id, second.machine_id
    first.close()
    second.close()
    assert_removed(first_id)
    assert_removed(second_id)


def test_live_startup_interrupt_and_parent_exit_cleanup(smol_image):
    interrupted = []

    def interrupt(workspace, deadline):
        interrupted.append(workspace.machine_id)
        raise KeyboardInterrupt

    with patch.object(SmolWorkspace, "_wait_for_server", interrupt):
        with pytest.raises(KeyboardInterrupt):
            SmolWorkspace(server_image=smol_image, network=False)
    assert_removed(interrupted[0])
    workspace = SmolWorkspace(server_image=smol_image, network=False)
    identifier = workspace.machine_id
    with patch.object(RemoteWorkspace, "__exit__", side_effect=RuntimeError("parent")):
        with pytest.raises(RuntimeError, match="parent"):
            workspace.__exit__(None, None, None)
    assert_removed(identifier)


_MODEL_SERVER = """
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
responses = json.loads(Path("/workspace/responses.json").read_text())
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"data": []}')
    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        request = json.loads(self.rfile.read(length))
        if not request.get("tools"):
            body = json.dumps({"id": "title", "object": "chat.completion",
                "created": 0, "model": "gpt-4o", "choices": [{"index": 0,
                "message": {"role": "assistant", "content": "Fixture tool test"},
                "finish_reason": "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not responses:
            self.send_error(500, "script exhausted")
            return
        body = json.dumps(responses.pop(0)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
ThreadingHTTPServer(("127.0.0.1", 18080), Handler).serve_forever()
"""


def test_live_remote_conversation_streams_tool_events(smol_image, tmp_path):
    messages = [
        Message(
            role="assistant",
            content=[],
            tool_calls=[
                MessageToolCall(
                    id="call_terminal",
                    name="terminal",
                    origin="completion",
                    arguments=json.dumps(
                        {"command": "printf fixture-ok > /workspace/tool-result"}
                    ),
                )
            ],
        ),
        Message(
            role="assistant",
            content=[],
            tool_calls=[
                MessageToolCall(
                    id="call_finish",
                    name="finish",
                    origin="completion",
                    arguments=json.dumps({"message": "Tool completed."}),
                )
            ],
        ),
    ]
    fixture = TestLLM.from_messages(list(messages), model="gpt-4o")
    history = [
        Message(role="system", content=[TextContent(text="Test fixture")]),
        Message(role="user", content=[TextContent(text="Run the tool")]),
    ]
    responses = [
        fixture.completion(history).raw_response.model_dump(mode="json")
        for _ in messages
    ]
    for response in responses:
        response["choices"][0]["finish_reason"] = "tool_calls"
    script = tmp_path / "model_server.py"
    script.write_text(_MODEL_SERVER)
    payload = tmp_path / "responses.json"
    payload.write_text(json.dumps(responses))
    with SmolWorkspace(server_image=smol_image, network=False) as workspace:
        assert workspace.file_upload(script, "/workspace/model_server.py").success
        assert workspace.file_upload(payload, "/workspace/responses.json").success
        python = workspace.execute_command("command -v python3 || command -v python")
        assert python.exit_code == 0, "the test image needs a stdlib Python interpreter"
        executable = shlex.quote(python.stdout.strip())
        assert (
            workspace.execute_command(
                f"nohup {executable} /workspace/model_server.py "
                ">/workspace/model.log 2>&1 </dev/null &"
            ).exit_code
            == 0
        )
        assert (
            workspace.execute_command(
                f"{executable} -c "
                + shlex.quote(
                    "import urllib.request,time\n"
                    "for _ in range(50):\n"
                    " try: urllib.request.urlopen('http://127.0.0.1:18080'); break\n"
                    " except OSError: time.sleep(.1)\n"
                    "else: raise RuntimeError('fixture not ready')"
                )
            ).exit_code
            == 0
        )
        events: list[Event] = []
        llm = LLM(
            model="openai/gpt-4o",
            usage_id="smol-live-fixture",
            base_url="http://127.0.0.1:18080/v1",
            api_key=SecretStr("unused"),
            stream=False,
        )
        agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])
        conversation = Conversation(
            agent=agent, workspace=workspace, callbacks=[events.append]
        )
        try:
            assert isinstance(conversation, RemoteConversation)
            conversation.send_message("Run the scripted tool and finish.")
            conversation.run()
            assert any(isinstance(event, ActionEvent) for event in events)
            assert any(isinstance(event, ObservationEvent) for event in events)
            assert (
                workspace.execute_command("cat /workspace/tool-result").stdout
                == "fixture-ok"
            )
        finally:
            conversation.close()
        identifier = workspace.machine_id
    assert_removed(identifier)


def test_live_without_docker(smol_image, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    assert shutil.which("docker") is None
    for path in (Path("/var/run/docker.sock"), Path.home() / ".docker/run/docker.sock"):
        if path.exists():
            pytest.skip("this test requires a host without a Docker socket")
    with SmolWorkspace(server_image=smol_image, network=False) as workspace:
        assert workspace.execute_command("printf docker-free").stdout == "docker-free"
        identifier = workspace.machine_id
    assert_removed(identifier)


def test_live_failed_creation_before_handle_is_removed(smol_image):
    identifier = uuid.uuid4()
    with patch(
        "openhands.workspace.smol.workspace.uuid.uuid4", return_value=identifier
    ):
        with pytest.raises(RuntimeError, match="image/VM startup"):
            SmolWorkspace(
                server_image="ghcr.io/openhands/acceptance-missing-image:does-not-exist",
                network=False,
                startup_timeout=15,
            )
    assert_removed("openhands-" + identifier.hex)
