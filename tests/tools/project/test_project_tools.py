import json
from uuid import UUID, uuid4

import httpx
import pytest

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.project import (
    ListProjectConversationsAction,
    ListProjectConversationsExecutor,
    ListProjectConversationsTool,
    MessageProjectConversationAction,
    MessageProjectConversationExecutor,
    MessageProjectConversationTool,
    ReadProjectConversationAction,
    ReadProjectConversationExecutor,
    ReadProjectConversationTool,
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
            target = path.split("/")[-2]
            status = next(
                t["execution_status"]
                for t in self.conversations
                if t["conversation_id"] == target
            )
            return httpx.Response(
                200,
                json={"success": True, "previous_status": status, "run": "started"},
            )
        return httpx.Response(404, json={"detail": "Project not found"})

    def posts(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]


@pytest.fixture(autouse=True)
def _server_env(monkeypatch):
    monkeypatch.setenv("OH_INTERNAL_SERVER_URL", "http://agent-server:9000/")
    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "session-key")


def test_tool_names():
    assert ListProjectConversationsTool.name == "list_project_conversations"
    assert MessageProjectConversationTool.name == "message_project_conversation"


def test_tools_bind_to_conversation_and_reject_parameters(tmp_path):
    state = _conv_state(tmp_path)

    (list_tool,) = ListProjectConversationsTool.create(state)
    (message_tool,) = MessageProjectConversationTool.create(
        state, server_url="http://x"
    )

    assert isinstance(list_tool.executor, ListProjectConversationsExecutor)
    assert isinstance(message_tool.executor, MessageProjectConversationExecutor)
    assert list_tool.annotations is not None and list_tool.annotations.readOnlyHint
    with pytest.raises(ValueError, match="does not accept parameters"):
        ListProjectConversationsTool.create(state, project="x")


def test_list_reads_project_from_tags_with_session_key(tmp_path):
    conversations = [
        _conversation("waiting_for_confirmation", "needs_you", "asks"),
        _conversation("running", "working", "busy"),
    ]
    server = _Server(conversations)
    executor = ListProjectConversationsExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(ListProjectConversationsAction())

    assert not observation.is_error
    (request,) = server.requests
    assert str(request.url) == (
        f"http://agent-server:9000/api/projects/{PROJECT_ID}/conversations"
    )
    assert request.headers["X-Session-API-Key"] == "session-key"
    assert observation.project_id == PROJECT_ID
    assert [t.title for t in observation.conversations] == ["asks", "busy"]
    assert "[needs_you] asks" in observation.text
    assert "[working] busy" in observation.text


def test_list_leaves_out_the_caller(tmp_path):
    state = _conv_state(tmp_path)
    me = _conversation("running", "working", "me") | {"conversation_id": state.id.hex}
    other = _conversation("idle", "idle", "other")
    executor = ListProjectConversationsExecutor(
        state, transport=httpx.MockTransport(_Server([me, other]))
    )

    observation = executor(ListProjectConversationsAction())

    assert [t.title for t in observation.conversations] == ["other"]


def test_list_without_project_tag_makes_no_request(tmp_path):
    server = _Server([])
    executor = ListProjectConversationsExecutor(
        _conv_state(tmp_path, tags={}), transport=httpx.MockTransport(server)
    )

    observation = executor(ListProjectConversationsAction())

    assert observation.is_error
    assert "not part of a project" in observation.text
    assert server.requests == []


def test_list_reports_server_errors(tmp_path):
    executor = ListProjectConversationsExecutor(
        _conv_state(tmp_path, tags={"project": uuid4().hex}),
        transport=httpx.MockTransport(_Server([])),
    )

    observation = executor(ListProjectConversationsAction())

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
def test_message_posts_to_member_conversation(tmp_path, status, attention):
    conversation = _conversation(status, attention)
    server = _Server([conversation])
    state = _conv_state(tmp_path)
    executor = MessageProjectConversationExecutor(
        state, transport=httpx.MockTransport(server)
    )
    hyphenated = str(UUID(conversation["conversation_id"]))

    observation = executor(
        MessageProjectConversationAction(
            conversation_id=hyphenated, message="Also add docs"
        )
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
        "refuse_pending_confirmation": True,
        "sender_conversation_id": state.id.hex,
    }
    assert observation.conversation_id == conversation["conversation_id"]
    assert observation.previous_status == status
    assert observation.run == "started"


def test_message_reports_confirmation_reached_during_delivery(tmp_path):
    conversation = _conversation("running", "working")
    server = _Server(
        [conversation],
        message_response=httpx.Response(
            200,
            json={
                "conversation_id": conversation["conversation_id"],
                "previous_status": "running",
                "run": "waiting_for_confirmation",
            },
        ),
    )
    executor = MessageProjectConversationExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(
        MessageProjectConversationAction(
            conversation_id=conversation["conversation_id"], message="x"
        )
    )

    assert not observation.is_error
    assert observation.run == "waiting_for_confirmation"
    assert "it was not run" in observation.text


def test_message_maps_server_409_to_refusal(tmp_path):
    conversation = _conversation("idle", "idle")
    server = _Server(
        [conversation],
        message_response=httpx.Response(
            409, json={"detail": "Conversation is waiting for confirmation"}
        ),
    )
    executor = MessageProjectConversationExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(
        MessageProjectConversationAction(
            conversation_id=conversation["conversation_id"], message="x"
        )
    )

    assert observation.is_error
    assert "would approve that action" in observation.text


def test_message_timeout_says_not_to_resend(tmp_path):
    conversation = _conversation("running", "working")
    server = _Server([conversation], message_response=httpx.ReadTimeout("slow"))
    executor = MessageProjectConversationExecutor(
        _conv_state(tmp_path), timeout=1, transport=httpx.MockTransport(server)
    )

    observation = executor(
        MessageProjectConversationAction(
            conversation_id=conversation["conversation_id"], message="x"
        )
    )

    assert observation.is_error
    assert "may still be delivered" in observation.text
    assert "Do not send it again" in observation.text


def test_message_refuses_conversation_waiting_for_confirmation(tmp_path):
    conversation = _conversation("waiting_for_confirmation", "needs_you")
    server = _Server([conversation])
    executor = MessageProjectConversationExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(
        MessageProjectConversationAction(
            conversation_id=conversation["conversation_id"], message="go ahead"
        )
    )

    assert observation.is_error
    assert "would approve that action" in observation.text
    assert server.posts() == []


def test_message_refuses_conversation_outside_project(tmp_path):
    coordinator = _conversation("idle", "idle", "coordinator")
    server = _Server([_conversation("idle", "idle")], coordinator=coordinator)
    executor = MessageProjectConversationExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    for target in (uuid4().hex, coordinator["conversation_id"]):
        observation = executor(
            MessageProjectConversationAction(conversation_id=target, message="hi")
        )
        assert observation.is_error
        assert "is not a conversation of this project" in observation.text
    assert server.posts() == []


def test_message_rejects_malformed_id_without_requests(tmp_path):
    server = _Server([])
    executor = MessageProjectConversationExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(
        MessageProjectConversationAction(
            conversation_id="Docs conversation", message="hi"
        )
    )

    assert observation.is_error
    assert "is not a conversation id" in observation.text
    assert server.requests == []


def test_message_reads_project_at_call_time(tmp_path):
    state = _conv_state(tmp_path, tags={})
    conversation = _conversation("idle", "idle")
    server = _Server([conversation])
    executor = MessageProjectConversationExecutor(
        state, transport=httpx.MockTransport(server)
    )
    action = MessageProjectConversationAction(
        conversation_id=conversation["conversation_id"], message="hi"
    )

    before = executor(action)
    state.tags = {"project": PROJECT_ID}
    after = executor(action)

    assert before.is_error
    assert not after.is_error


# save_project_artifact


def _save(tmp_path, handler, tags=None, **action):
    from openhands.tools.project import (
        SaveProjectArtifactAction,
        SaveProjectArtifactExecutor,
    )

    state = _conv_state(tmp_path, tags)
    executor = SaveProjectArtifactExecutor(
        state, server_url="http://server", transport=httpx.MockTransport(handler)
    )
    return state, executor(SaveProjectArtifactAction(**action))


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

    executor = ReadProjectConversationExecutor(
        _conv_state(tmp_path), transport=httpx.MockTransport(server)
    )

    observation = executor(
        ReadProjectConversationAction(conversation_id=str(target), limit=5)
    )

    assert not observation.is_error, observation.text
    (request,) = seen
    assert request.url.path == (
        f"/api/projects/{PROJECT_ID}/conversations/{target.hex}/messages"
    )
    assert request.url.params["limit"] == "5"
    assert "[user, from conversation:" in observation.text
    assert "[the person] Also add a test" in observation.text
    assert "[agent (finish)] Done, with a test." in observation.text


def test_read_outside_a_project_is_an_error(tmp_path):
    executor = ReadProjectConversationExecutor(
        _conv_state(tmp_path, tags={}),
        transport=httpx.MockTransport(lambda r: httpx.Response(500)),
    )

    observation = executor(ReadProjectConversationAction(conversation_id=uuid4().hex))

    assert observation.is_error
    assert "not part of a project" in observation.text


def test_the_tool_that_steers_conversations_brings_the_coordinator_role(tmp_path):
    (message_tool,) = MessageProjectConversationTool.create(_conv_state(tmp_path))
    (list_tool,) = ListProjectConversationsTool.create(_conv_state(tmp_path))

    guidance = message_tool.prompt_guidance or ""
    assert guidance.startswith("<PROJECT_COORDINATOR>")
    assert "unless the person asks you to do it here" in guidance
    # It explains the feature as the person sees it, and keeps the cat's voice.
    for topic in ("needs input", "resolving it", "Project memory", "emoji", "herd"):
        assert topic in guidance
    assert list_tool.prompt_guidance is None
    assert ReadProjectConversationTool.name == "read_project_conversation"


def test_a_coordinators_system_prompt_states_its_role(tmp_path):
    from openhands.sdk.tool import Tool

    agent = Agent(
        llm=LLM(model="gpt-4o", usage_id="cat"),
        tools=[Tool(name="message_project_conversation")],
    )
    state = _conv_state(tmp_path)
    agent._initialize(state)

    assert "<PROJECT_COORDINATOR>" in agent.static_system_message
    assert "<ROLE>" in agent.static_system_message
