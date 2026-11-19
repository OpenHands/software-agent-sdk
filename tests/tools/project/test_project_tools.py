import json
from uuid import UUID, uuid4

import httpx
import pytest

from openhands.sdk import LLM, Agent
from openhands.sdk.agent.base import PROJECT_COORDINATOR_PROMPT
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.project import (
    ListWorkersAction,
    ListWorkersExecutor,
    ListWorkersTool,
    MessageWorkerAction,
    MessageWorkerExecutor,
    MessageWorkerTool,
    ReadProjectMemoryAction,
    ReadProjectMemoryExecutor,
    ReadWorkerAction,
    ReadWorkerExecutor,
    ReadWorkerTool,
    ResolveConversationAction,
    ResolveConversationExecutor,
    ResolveConversationTool,
)


PROJECT_ID = uuid4().hex


def _conv_state(tmp_path, tags: dict[str, str] | None = None) -> ConversationState:
    state = ConversationState(
        id=uuid4(),
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )
    state.tags = tags if tags is not None else {"project": PROJECT_ID}
    return state


def _conversation(status: str, attention: str, title: str = "worker") -> dict:
    return {
        "conversation_id": uuid4().hex,
        "title": title,
        "execution_status": status,
        "attention": attention,
        "parent_conversation_id": None,
        "created_at": "2026-11-07T10:00:00Z",
        "updated_at": "2026-11-07T10:00:00Z",
    }


class _Server:
    """Records requests and answers like the projects routes."""

    def __init__(
        self,
        conversations: list[dict],
        coordinator: dict | None = None,
        message_response: httpx.Response | Exception | None = None,
    ):
        self.conversations = conversations
        self.coordinator = coordinator
        self.message_response = message_response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if (
            request.method == "GET"
            and path == f"/api/projects/{PROJECT_ID}/conversations"
        ):
            return httpx.Response(
                200,
                json={
                    "project_id": PROJECT_ID,
                    "coordinator": self.coordinator,
                    "conversations": self.conversations,
                },
            )
        if request.method == "POST" and path.endswith("/events"):
            if isinstance(self.message_response, Exception):
                raise self.message_response
            if self.message_response is not None:
                return self.message_response
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404, json={"detail": "Project not found"})

    def posts(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]


@pytest.fixture(autouse=True)
def _server_env(monkeypatch):
    monkeypatch.setenv("OH_INTERNAL_SERVER_URL", "http://agent-server:9000/")
    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "session-key")


def test_tool_names():
    assert ListWorkersTool.name == "list_workers"
    assert MessageWorkerTool.name == "message_worker"


def test_tools_bind_to_conversation_and_reject_parameters(tmp_path):
    state = _conv_state(tmp_path)

    (list_tool,) = ListWorkersTool.create(state)
    (message_tool,) = MessageWorkerTool.create(state, server_url="http://x")

    assert isinstance(list_tool.executor, ListWorkersExecutor)
    assert isinstance(message_tool.executor, MessageWorkerExecutor)
    assert list_tool.annotations is not None and list_tool.annotations.readOnlyHint
    with pytest.raises(ValueError, match="does not accept parameters"):
        ListWorkersTool.create(state, project="x")


def test_list_reads_project_from_tags_with_session_key(tmp_path):
    conversations = [
        _conversation("waiting_for_confirmation", "needs_you", "asks"),
        _conversation("running", "working", "busy"),
    ]
    server = _Server(conversations)
    executor = ListWorkersExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(ListWorkersAction())

    assert not observation.is_error
    (request,) = server.requests
    assert str(request.url) == (
        f"http://agent-server:9000/api/projects/{PROJECT_ID}/conversations"
    )
    assert request.headers["X-Session-API-Key"] == "session-key"
    assert observation.project_id == PROJECT_ID
    assert [t.title for t in observation.conversations] == ["asks", "busy"]
    for conversation in observation.conversations:
        assert (
            f"[{conversation.attention}] [{conversation.title}](conversation:"
            f"{UUID(conversation.conversation_id).hex})"
        ) in observation.text


def test_list_leaves_out_the_caller(tmp_path):
    state = _conv_state(tmp_path)
    me = _conversation("running", "working", "me") | {"conversation_id": state.id.hex}
    other = _conversation("idle", "idle", "other")
    executor = ListWorkersExecutor(
        state, transport=httpx.MockTransport(_Server([me, other]))
    )

    observation = executor(ListWorkersAction())

    assert [t.title for t in observation.conversations] == ["other"]


def test_list_without_project_tag_makes_no_request(tmp_path):
    server = _Server([])
    executor = ListWorkersExecutor(
        _conv_state(tmp_path, tags={}), transport=httpx.MockTransport(server)
    )

    observation = executor(ListWorkersAction())

    assert observation.is_error
    assert "not part of a project" in observation.text
    assert server.requests == []


def test_list_reports_server_errors(tmp_path):
    executor = ListWorkersExecutor(
        _conv_state(tmp_path, tags={"project": uuid4().hex}),
        transport=httpx.MockTransport(_Server([])),
    )

    observation = executor(ListWorkersAction())

    assert observation.is_error
    assert "(404): Project not found" in observation.text


@pytest.mark.parametrize(
    ("executor_type", "action"),
    [
        (ListWorkersExecutor, ListWorkersAction()),
        (ReadProjectMemoryExecutor, ReadProjectMemoryAction(name="MEMORY.md")),
    ],
)
def test_tools_say_so_when_projects_was_turned_off(tmp_path, executor_type, action):
    """A coordinator started while Projects was on keeps its tools; once the
    user turns the feature off, each call tells it so instead of a bare 404
    (or, for memory, a missing file to create)."""

    def projects_off(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Projects are turned off"})

    executor = executor_type(
        _conv_state(tmp_path, tags={"project": uuid4().hex}),
        transport=httpx.MockTransport(projects_off),
    )

    observation = executor(action)

    assert observation.is_error
    assert "Projects is turned off" in observation.text
    assert "do not retry" in observation.text
    assert "404" not in observation.text


@pytest.mark.parametrize(
    ("status", "attention"),
    [
        ("finished", "idle"),
        ("idle", "idle"),
        ("running", "working"),
    ],
)
def test_message_posts_to_member_conversation(tmp_path, status, attention):
    conversation = _conversation(status, attention)
    server = _Server([conversation])
    state = _conv_state(tmp_path)
    executor = MessageWorkerExecutor(state, transport=httpx.MockTransport(server))
    hyphenated = str(UUID(conversation["conversation_id"]))

    observation = executor(
        MessageWorkerAction(conversation_id=hyphenated, message="Also add docs")
    )

    assert not observation.is_error, observation.text
    (post,) = server.posts()
    assert post.url.path == (
        f"/api/conversations/{conversation['conversation_id']}/events"
    )
    assert json.loads(post.content) == {
        "role": "user",
        "content": [{"type": "text", "text": "Also add docs"}],
        "run": True,
        "sender_conversation_id": state.id.hex,
    }
    assert observation.conversation_id == conversation["conversation_id"]
    assert "a run was requested" in observation.text


def test_message_timeout_says_not_to_resend(tmp_path):
    conversation = _conversation("running", "working")
    server = _Server([conversation], message_response=httpx.ReadTimeout("slow"))
    executor = MessageWorkerExecutor(
        _conv_state(tmp_path), timeout=1, transport=httpx.MockTransport(server)
    )

    observation = executor(
        MessageWorkerAction(
            conversation_id=conversation["conversation_id"], message="x"
        )
    )

    assert observation.is_error
    assert "may still be delivered" in observation.text
    assert "Do not send it again" in observation.text


def test_message_refuses_conversation_waiting_for_confirmation(tmp_path):
    """Running it would approve the pending action, so nothing is sent."""
    conversation = _conversation("waiting_for_confirmation", "needs_you")
    server = _Server([conversation])
    executor = MessageWorkerExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(
        MessageWorkerAction(
            conversation_id=conversation["conversation_id"], message="go ahead"
        )
    )

    assert observation.is_error
    assert "would approve that action" in observation.text
    assert server.posts() == []


@pytest.mark.parametrize("status", ["paused", "error", "stuck"])
def test_message_runs_a_paused_errored_or_stuck_worker(tmp_path, status):
    """Only a pending approval is the user's alone; the coordinator may
    run the worker again from the other stops."""
    conversation = _conversation(status, "needs_you")
    server = _Server([conversation])
    executor = MessageWorkerExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(
        MessageWorkerAction(
            conversation_id=conversation["conversation_id"], message="try again"
        )
    )

    assert not observation.is_error, observation.text
    (post,) = server.posts()
    assert json.loads(post.content)["run"] is True


def test_message_refuses_conversation_outside_project(tmp_path):
    coordinator = _conversation("idle", "idle", "coordinator")
    server = _Server([_conversation("idle", "idle")], coordinator=coordinator)
    executor = MessageWorkerExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    for target in (uuid4().hex, coordinator["conversation_id"]):
        observation = executor(
            MessageWorkerAction(conversation_id=target, message="hi")
        )
        assert observation.is_error
        assert "is not a worker of this project" in observation.text
    assert server.posts() == []


def test_message_rejects_malformed_id_without_requests(tmp_path):
    server = _Server([])
    executor = MessageWorkerExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(
        MessageWorkerAction(conversation_id="Docs conversation", message="hi")
    )

    assert observation.is_error
    assert "is not a conversation id" in observation.text
    assert server.requests == []


def test_message_reads_project_at_call_time(tmp_path):
    state = _conv_state(tmp_path, tags={})
    conversation = _conversation("idle", "idle")
    server = _Server([conversation])
    executor = MessageWorkerExecutor(state, transport=httpx.MockTransport(server))
    action = MessageWorkerAction(
        conversation_id=conversation["conversation_id"], message="hi"
    )

    before = executor(action)
    state.tags = {"project": PROJECT_ID}
    after = executor(action)

    assert before.is_error
    assert not after.is_error


# write_artifact


def _save(tmp_path, handler, tags=None, **action):
    from openhands.tools.project import (
        WriteArtifactAction,
        WriteArtifactExecutor,
    )

    state = _conv_state(tmp_path, tags)
    executor = WriteArtifactExecutor(
        state, server_url="http://server", transport=httpx.MockTransport(handler)
    )
    return state, executor(WriteArtifactAction(**action))


def test_save_artifact_posts_the_callers_id_and_path(tmp_path):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"name": "report.md", "size": 12})

    state, observation = _save(tmp_path, handler, path="out/report.md")

    assert not observation.is_error
    assert observation.name == "report.md" and observation.size == 12
    assert seen[0].url.path == (
        f"/api/projects/{PROJECT_ID}/artifacts/from-conversation"
    )
    assert json.loads(seen[0].content) == {
        "conversation_id": state.id.hex,
        "path": "out/report.md",
    }


def test_save_artifact_reports_server_refusal(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"detail": "Path is outside the conversation's workspace"}
        )

    _, observation = _save(tmp_path, handler, path="/etc/passwd", name="x")

    assert observation.is_error
    assert "outside the conversation's workspace" in observation.text


def test_save_artifact_needs_a_project(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    _, observation = _save(tmp_path, handler, tags={}, path="a.txt")

    assert observation.is_error
    assert "not part of a project" in observation.text


# read_artifact and list_artifacts


def _read_artifact(tmp_path, body: bytes, tags=None, **action):
    from openhands.tools.project import ReadArtifactAction, ReadArtifactExecutor

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=body)

    executor = ReadArtifactExecutor(
        _conv_state(tmp_path, tags),
        server_url="http://server",
        transport=httpx.MockTransport(handler),
    )
    return seen, executor(ReadArtifactAction(**action))


def test_read_artifact_returns_small_text_inline(tmp_path):
    seen, observation = _read_artifact(tmp_path, b"# Plan\n", name="plan v2.md")

    assert not observation.is_error
    assert "# Plan" in observation.text and observation.path is None
    assert seen[0].method == "GET"
    assert seen[0].url.raw_path.decode() == (
        f"/api/projects/{PROJECT_ID}/artifacts/plan%20v2.md"
    )
    assert not (tmp_path / ".agent_tmp").exists()


def test_read_artifact_copies_to_the_path_asked_for(tmp_path):
    _, observation = _read_artifact(
        tmp_path, b"a,b\n1,2\n", name="data.csv", path="in/data.csv"
    )

    assert not observation.is_error
    copy = tmp_path / "in" / "data.csv"
    assert copy.read_bytes() == b"a,b\n1,2\n"
    assert observation.path == str(copy.resolve())
    assert list((tmp_path / "in").iterdir()) == [copy]


def test_read_artifact_copies_binary_to_the_artifacts_folder(tmp_path):
    _, observation = _read_artifact(tmp_path, b"\x89PNG\0\x01", name="chart.png")

    assert not observation.is_error
    copy = tmp_path / ".agent_tmp" / "artifacts" / "chart.png"
    assert copy.read_bytes() == b"\x89PNG\0\x01"
    assert "Copied artifact" in observation.text


def test_read_artifact_copies_large_text_instead_of_inlining_it(tmp_path):
    body = b"x" * (32 * 1024 + 1)

    _, observation = _read_artifact(tmp_path, body, name="big.txt")

    assert observation.path is not None
    assert (tmp_path / ".agent_tmp" / "artifacts" / "big.txt").stat().st_size == len(
        body
    )


@pytest.mark.parametrize("path", ["../escape.txt", "/etc/passwd", "."])
def test_read_artifact_refuses_a_path_outside_the_workspace(tmp_path, path):
    seen, observation = _read_artifact(tmp_path, b"x", name="a.txt", path=path)

    assert observation.is_error
    assert "outside your working directory" in observation.text
    assert seen == []


def test_read_artifact_needs_a_project(tmp_path):
    seen, observation = _read_artifact(tmp_path, b"x", tags={}, name="a.txt")

    assert observation.is_error
    assert "not part of a project" in observation.text
    assert seen == []


def _list_artifacts(tmp_path, response: httpx.Response, tags=None):
    from openhands.tools.project import ListArtifactsAction, ListArtifactsExecutor

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    executor = ListArtifactsExecutor(
        _conv_state(tmp_path, tags),
        server_url="http://server",
        transport=httpx.MockTransport(handler),
    )
    return seen, executor(ListArtifactsAction())


def test_list_artifacts_names_who_added_each(tmp_path):
    saver = uuid4()
    artifacts = [
        {
            "name": "brief.pdf",
            "size": 2048,
            "source": "user",
            "conversation_id": None,
            "created_at": "2026-11-10T09:00:00Z",
            "updated_at": "2026-11-10T09:00:00Z",
        },
        {
            "name": "report.md",
            "size": 12,
            "source": "agent",
            "conversation_id": saver.hex,
            "created_at": "2026-11-10T10:00:00Z",
            "updated_at": "2026-11-10T11:00:00Z",
        },
    ]

    seen, observation = _list_artifacts(
        tmp_path, httpx.Response(200, json={"artifacts": artifacts})
    )

    assert not observation.is_error
    assert seen[0].url.path == f"/api/projects/{PROJECT_ID}/artifacts"
    assert "2 project artifact(s)" in observation.text
    assert "brief.pdf (2048 bytes), added by the user" in observation.text
    assert "report.md (12 bytes), added by" in observation.text
    assert saver.hex in observation.text


def test_list_artifacts_says_when_there_are_none(tmp_path):
    _, observation = _list_artifacts(
        tmp_path, httpx.Response(200, json={"artifacts": []})
    )

    assert observation.text == "The project has no artifacts yet."


def test_list_artifacts_needs_a_project(tmp_path):
    seen, observation = _list_artifacts(
        tmp_path, httpx.Response(200, json={"artifacts": []}), tags={}
    )

    assert observation.is_error and seen == []


def test_read_reports_the_latest_messages(tmp_path):
    target = uuid4()
    seen: list[httpx.Request] = []

    def server(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "conversation_id": target.hex,
                "messages": [
                    {
                        "id": "m1",
                        "source": "user",
                        "sender": f"conversation:{'c' * 32}",
                        "text": "Fix the bug",
                        "finish": False,
                        "timestamp": "t",
                    },
                    {
                        "id": "m2",
                        "source": "user",
                        "sender": None,
                        "text": "Also add a test",
                        "finish": False,
                        "timestamp": "t",
                    },
                    {
                        "id": "m3",
                        "source": "agent",
                        "sender": None,
                        "text": "Done, with a test.",
                        "finish": True,
                        "timestamp": "t",
                    },
                ],
            },
        )

    executor = ReadWorkerExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(ReadWorkerAction(conversation_id=str(target), limit=5))

    assert not observation.is_error, observation.text
    (request,) = seen
    assert request.url.path == (
        f"/api/projects/{PROJECT_ID}/conversations/{target.hex}/messages"
    )
    assert request.url.params["limit"] == "5"
    assert "[user, from conversation:" in observation.text
    assert "[the user] Also add a test" in observation.text
    assert "[agent (finish)] Done, with a test." in observation.text


def test_read_refuses_the_coordinator_itself(tmp_path):
    """read_worker reads workers; the caller is the coordinator."""
    state = _conv_state(tmp_path)
    executor = ReadWorkerExecutor(
        state, transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )

    observation = executor(ReadWorkerAction(conversation_id=state.id.hex))

    assert observation.is_error
    assert "That is this conversation" in observation.text


def test_read_outside_a_project_is_an_error(tmp_path):
    executor = ReadWorkerExecutor(
        _conv_state(tmp_path, tags={}),
        transport=httpx.MockTransport(lambda r: httpx.Response(500)),
    )

    observation = executor(ReadWorkerAction(conversation_id=uuid4().hex))

    assert observation.is_error
    assert "not part of a project" in observation.text


def test_project_tools_bring_no_role_text(tmp_path):
    """The coordinator's role is its prompt, not a tool's guidance:
    the voice and the job no longer ride on message_worker."""
    (message_tool,) = MessageWorkerTool.create(_conv_state(tmp_path))
    (list_tool,) = ListWorkersTool.create(_conv_state(tmp_path))

    assert message_tool.prompt_guidance is None
    assert list_tool.prompt_guidance is None
    assert ReadWorkerTool.name == "read_worker"


def test_a_coordinators_system_prompt_states_its_role(tmp_path):
    from openhands.sdk.tool import Tool

    agent = Agent(
        llm=LLM(model="gpt-4o", usage_id="cat"),
        tools=[Tool(name="message_worker")],
        system_prompt_filename=PROJECT_COORDINATOR_PROMPT,
    )
    state = _conv_state(tmp_path)
    agent._initialize(state)

    static = agent.static_system_message
    for tag in ("ROLE", "ROUTING", "BRIEFS", "WHAT_YOU_HEAR", "TALKING_TO_THE_USER"):
        assert f"<{tag}>" in static
    assert "unless the user asks you to do it here" in static
    # It routes rather than codes: no coding-workflow sections.
    for tag in ("CODE_QUALITY", "PULL_REQUESTS", "PROBLEM_SOLVING_WORKFLOW"):
        assert f"<{tag}>" not in static
    assert "herd" not in static


def test_resolve_posts_the_agent_resolve_for_its_own_conversation(
    tmp_path, _server_env
):
    state = _conv_state(tmp_path)
    requests: list[httpx.Request] = []

    def server(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    (tool,) = ResolveConversationTool.create(state)
    assert tool.name == "resolve_conversation"
    assert isinstance(tool.executor, ResolveConversationExecutor)
    executor = ResolveConversationExecutor(state, transport=httpx.MockTransport(server))

    observation = executor(ResolveConversationAction())

    assert not observation.is_error
    assert "End your turn" in observation.text
    (request,) = requests
    assert request.method == "POST"
    assert request.url == (
        f"http://agent-server:9000/api/projects/{PROJECT_ID}/conversations/"
        f"{state.id.hex}/resolve"
    )
    assert json.loads(request.content) == {"by": "agent"}
    assert request.headers["X-Session-API-Key"] == "session-key"


def test_resolve_outside_a_project_makes_no_request(tmp_path):
    def server(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    executor = ResolveConversationExecutor(
        _conv_state(tmp_path, tags={}), transport=httpx.MockTransport(server)
    )

    observation = executor(ResolveConversationAction())

    assert observation.is_error
    assert "not part of a project" in observation.text


@pytest.mark.parametrize("name", ["../other/memory/MEMORY.md", "..", "a/b.md"])
def test_memory_and_artifact_names_stay_in_the_project(tmp_path, name):
    """A name with a path in it would be resolved by the URL into another
    project's files; the tools refuse it before any request."""
    from openhands.tools.project import (
        ReadArtifactAction,
        ReadArtifactExecutor,
        ReadProjectMemoryAction,
        ReadProjectMemoryExecutor,
        UpdateProjectMemoryAction,
        UpdateProjectMemoryExecutor,
    )

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/memory"):
            return httpx.Response(200, json={"files": []})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    state = _conv_state(tmp_path)
    observations = [
        ReadArtifactExecutor(state, transport=transport)(ReadArtifactAction(name=name)),
        ReadProjectMemoryExecutor(state, transport=transport)(
            ReadProjectMemoryAction(name=name)
        ),
        UpdateProjectMemoryExecutor(state, transport=transport)(
            UpdateProjectMemoryAction(name=name, content="x")
        ),
    ]

    for observation in observations:
        assert observation.is_error, observation.text
        assert "is not a file name" in observation.text
    assert all("/memory/" not in r.url.path for r in seen)
    assert all("/artifacts/" not in r.url.path for r in seen)
