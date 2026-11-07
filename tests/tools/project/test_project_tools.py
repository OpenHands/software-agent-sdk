import json
from uuid import UUID, uuid4

import httpx
import pytest

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.project import (
    ListProjectThreadsAction,
    ListProjectThreadsExecutor,
    ListProjectThreadsTool,
    MessageProjectThreadAction,
    MessageProjectThreadExecutor,
    MessageProjectThreadTool,
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


def _thread(status: str, attention: str, title: str = "worker") -> dict:
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
        threads: list[dict],
        coordinator: dict | None = None,
        message_response: httpx.Response | Exception | None = None,
    ):
        self.threads = threads
        self.coordinator = coordinator
        self.message_response = message_response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "GET" and path == f"/api/projects/{PROJECT_ID}/threads":
            return httpx.Response(
                200,
                json={
                    "project_id": PROJECT_ID,
                    "coordinator": self.coordinator,
                    "threads": self.threads,
                },
            )
        if request.method == "POST" and path.endswith("/messages"):
            if isinstance(self.message_response, Exception):
                raise self.message_response
            if self.message_response is not None:
                return self.message_response
            target = path.split("/")[-2]
            status = next(
                t["execution_status"]
                for t in self.threads
                if t["conversation_id"] == target
            )
            return httpx.Response(
                200,
                json={
                    "conversation_id": target,
                    "previous_status": status,
                    "run": "started",
                },
            )
        return httpx.Response(404, json={"detail": "Project not found"})

    def posts(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]


@pytest.fixture(autouse=True)
def _server_env(monkeypatch):
    monkeypatch.setenv("OH_INTERNAL_SERVER_URL", "http://agent-server:9000/")
    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "session-key")


def test_tool_names():
    assert ListProjectThreadsTool.name == "list_project_threads"
    assert MessageProjectThreadTool.name == "message_project_thread"


def test_tools_bind_to_conversation_and_reject_parameters(tmp_path):
    state = _conv_state(tmp_path)

    (list_tool,) = ListProjectThreadsTool.create(state)
    (message_tool,) = MessageProjectThreadTool.create(state, server_url="http://x")

    assert isinstance(list_tool.executor, ListProjectThreadsExecutor)
    assert isinstance(message_tool.executor, MessageProjectThreadExecutor)
    assert list_tool.annotations is not None and list_tool.annotations.readOnlyHint
    with pytest.raises(ValueError, match="does not accept parameters"):
        ListProjectThreadsTool.create(state, project="x")


def test_list_reads_project_from_tags_with_session_key(tmp_path):
    threads = [
        _thread("waiting_for_confirmation", "needs_you", "asks"),
        _thread("running", "working", "busy"),
    ]
    server = _Server(threads)
    executor = ListProjectThreadsExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(ListProjectThreadsAction())

    assert not observation.is_error
    (request,) = server.requests
    assert str(request.url) == (
        f"http://agent-server:9000/api/projects/{PROJECT_ID}/threads"
    )
    assert request.headers["X-Session-API-Key"] == "session-key"
    assert observation.project_id == PROJECT_ID
    assert [t.title for t in observation.threads] == ["asks", "busy"]
    assert "[needs_you] asks" in observation.text
    assert "[working] busy" in observation.text


def test_list_leaves_out_the_caller(tmp_path):
    state = _conv_state(tmp_path)
    me = _thread("running", "working", "me") | {"conversation_id": state.id.hex}
    other = _thread("idle", "idle", "other")
    executor = ListProjectThreadsExecutor(
        state, transport=httpx.MockTransport(_Server([me, other]))
    )

    observation = executor(ListProjectThreadsAction())

    assert [t.title for t in observation.threads] == ["other"]


def test_list_without_project_tag_makes_no_request(tmp_path):
    server = _Server([])
    executor = ListProjectThreadsExecutor(
        _conv_state(tmp_path, tags={}), transport=httpx.MockTransport(server)
    )

    observation = executor(ListProjectThreadsAction())

    assert observation.is_error
    assert "not part of a project" in observation.text
    assert server.requests == []


def test_list_reports_server_errors(tmp_path):
    executor = ListProjectThreadsExecutor(
        _conv_state(tmp_path, tags={"project": uuid4().hex}),
        transport=httpx.MockTransport(_Server([])),
    )

    observation = executor(ListProjectThreadsAction())

    assert observation.is_error
    assert "(404): Project not found" in observation.text


@pytest.mark.parametrize(
    ("status", "attention"),
    [
        ("finished", "finished"),
        ("idle", "idle"),
        ("paused", "needs_you"),
        ("error", "needs_you"),
        ("running", "working"),
    ],
)
def test_message_posts_to_member_thread(tmp_path, status, attention):
    thread = _thread(status, attention)
    server = _Server([thread])
    executor = MessageProjectThreadExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )
    hyphenated = str(UUID(thread["conversation_id"]))

    observation = executor(
        MessageProjectThreadAction(conversation_id=hyphenated, message="Also add docs")
    )

    assert not observation.is_error, observation.text
    (post,) = server.posts()
    assert post.url.path == (
        f"/api/projects/{PROJECT_ID}/threads/{thread['conversation_id']}/messages"
    )
    assert json.loads(post.content) == {"text": "Also add docs"}
    assert observation.conversation_id == thread["conversation_id"]
    assert observation.previous_status == status
    assert observation.run == "started"


def test_message_reports_confirmation_reached_during_delivery(tmp_path):
    thread = _thread("running", "working")
    server = _Server(
        [thread],
        message_response=httpx.Response(
            200,
            json={
                "conversation_id": thread["conversation_id"],
                "previous_status": "running",
                "run": "waiting_for_confirmation",
            },
        ),
    )
    executor = MessageProjectThreadExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(
        MessageProjectThreadAction(
            conversation_id=thread["conversation_id"], message="x"
        )
    )

    assert not observation.is_error
    assert observation.run == "waiting_for_confirmation"
    assert "it was not run" in observation.text


def test_message_maps_server_409_to_refusal(tmp_path):
    thread = _thread("idle", "idle")
    server = _Server(
        [thread],
        message_response=httpx.Response(
            409, json={"detail": "Conversation is waiting for confirmation"}
        ),
    )
    executor = MessageProjectThreadExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(
        MessageProjectThreadAction(
            conversation_id=thread["conversation_id"], message="x"
        )
    )

    assert observation.is_error
    assert "would approve that action" in observation.text


def test_message_timeout_says_not_to_resend(tmp_path):
    thread = _thread("running", "working")
    server = _Server([thread], message_response=httpx.ReadTimeout("slow"))
    executor = MessageProjectThreadExecutor(
        _conv_state(tmp_path), timeout=1, transport=httpx.MockTransport(server)
    )

    observation = executor(
        MessageProjectThreadAction(
            conversation_id=thread["conversation_id"], message="x"
        )
    )

    assert observation.is_error
    assert "may still be delivered" in observation.text
    assert "Do not send it again" in observation.text


def test_message_refuses_thread_waiting_for_confirmation(tmp_path):
    thread = _thread("waiting_for_confirmation", "needs_you")
    server = _Server([thread])
    executor = MessageProjectThreadExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(
        MessageProjectThreadAction(
            conversation_id=thread["conversation_id"], message="go ahead"
        )
    )

    assert observation.is_error
    assert "would approve that action" in observation.text
    assert server.posts() == []


def test_message_refuses_conversation_outside_project(tmp_path):
    coordinator = _thread("idle", "idle", "coordinator")
    server = _Server([_thread("idle", "idle")], coordinator=coordinator)
    executor = MessageProjectThreadExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    for target in (uuid4().hex, coordinator["conversation_id"]):
        observation = executor(
            MessageProjectThreadAction(conversation_id=target, message="hi")
        )
        assert observation.is_error
        assert "is not a thread of this project" in observation.text
    assert server.posts() == []


def test_message_rejects_malformed_id_without_requests(tmp_path):
    server = _Server([])
    executor = MessageProjectThreadExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(
        MessageProjectThreadAction(conversation_id="Docs thread", message="hi")
    )

    assert observation.is_error
    assert "is not a conversation id" in observation.text
    assert server.requests == []


def test_message_reads_project_at_call_time(tmp_path):
    state = _conv_state(tmp_path, tags={})
    thread = _thread("idle", "idle")
    server = _Server([thread])
    executor = MessageProjectThreadExecutor(
        state, transport=httpx.MockTransport(server)
    )
    action = MessageProjectThreadAction(
        conversation_id=thread["conversation_id"], message="hi"
    )

    before = executor(action)
    state.tags = {"project": PROJECT_ID}
    after = executor(action)

    assert before.is_error
    assert not after.is_error
