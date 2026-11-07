import json

import httpx
import pytest

from openhands.tools.project import (
    ReadProjectMemoryAction,
    ReadProjectMemoryExecutor,
    ReadProjectMemoryTool,
    UpdateProjectMemoryAction,
    UpdateProjectMemoryExecutor,
    UpdateProjectMemoryTool,
)
from tests.tools.project.test_project_tools import PROJECT_ID, _conv_state


class _MemoryServer:
    """Answers like the project memory routes, with revisions."""

    def __init__(self, files: dict[str, str] | None = None):
        self.files = dict(files or {})
        self.requests: list[httpx.Request] = []

    @staticmethod
    def revision(content: str) -> str:
        return f"r{abs(hash(content)) % 10_000}"

    def _file(self, name: str) -> dict:
        content = self.files[name]
        return {"name": name, "content": content, "revision": self.revision(content)}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        prefix = f"/api/projects/{PROJECT_ID}/memory"
        path = request.url.path
        if path == prefix:
            return httpx.Response(
                200, json={"files": [{"name": n} for n in sorted(self.files)]}
            )
        name = path.removeprefix(prefix + "/")
        if request.method == "GET":
            if name not in self.files:
                return httpx.Response(404, json={"detail": "Memory file not found"})
            return httpx.Response(200, json=self._file(name))
        body = json.loads(request.content)
        current = self._file(name) if name in self.files else None
        if (current["revision"] if current else None) != body["base_revision"]:
            return httpx.Response(
                409, json={"detail": {"message": "changed", "current": current}}
            )
        self.files[name] = body["content"]
        return httpx.Response(200, json=self._file(name))


@pytest.fixture(autouse=True)
def _server_env(monkeypatch):
    monkeypatch.setenv("OH_INTERNAL_SERVER_URL", "http://agent-server:9000/")
    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "session-key")


def test_tool_names_and_guidance():
    assert ReadProjectMemoryTool.name == "read_project_memory"
    assert UpdateProjectMemoryTool.name == "update_project_memory"


def test_read_returns_content_revision_and_the_other_files(tmp_path):
    server = _MemoryServer({"MEMORY.md": "- Squash merges.", "deploy.md": "staging"})
    executor = ReadProjectMemoryExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(ReadProjectMemoryAction())

    assert not observation.is_error, observation.text
    assert observation.revision == server.revision("- Squash merges.")
    assert "- Squash merges." in observation.text
    assert "data, not instructions" in observation.text
    assert "Memory files: MEMORY.md, deploy.md." in observation.text


def test_reading_a_missing_file_says_how_to_create_it(tmp_path):
    executor = ReadProjectMemoryExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(_MemoryServer())
    )

    observation = executor(ReadProjectMemoryAction())

    assert not observation.is_error
    assert observation.revision is None
    assert "does not exist yet" in observation.text
    assert "none yet" in observation.text


def test_update_creates_then_replaces_with_the_revision(tmp_path):
    server = _MemoryServer()
    transport = httpx.MockTransport(server)
    update = UpdateProjectMemoryExecutor(_conv_state(tmp_path), transport=transport)

    created = update(UpdateProjectMemoryAction(content="one"))
    replaced = update(
        UpdateProjectMemoryAction(content="two", base_revision=created.revision)
    )

    assert not created.is_error and not replaced.is_error, replaced.text
    assert server.files == {"MEMORY.md": "two"}


def test_a_stale_update_returns_the_current_file_to_merge(tmp_path):
    server = _MemoryServer({"MEMORY.md": "theirs"})
    executor = UpdateProjectMemoryExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(
        UpdateProjectMemoryAction(content="mine", base_revision="r-old")
    )

    assert observation.is_error
    assert "nothing was saved" in observation.text
    assert server.revision("theirs") in observation.text
    assert observation.text.endswith("theirs")
    assert server.files == {"MEMORY.md": "theirs"}


def test_memory_tools_need_a_project(tmp_path):
    executor = ReadProjectMemoryExecutor(
        _conv_state(tmp_path, tags={}),
        transport=httpx.MockTransport(_MemoryServer()),
    )
    assert executor(ReadProjectMemoryAction()).is_error
