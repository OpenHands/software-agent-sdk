"""Tests for RemoteConversation.fork()."""

import uuid
from unittest.mock import Mock, patch

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import RemoteWorkspace


def _agent() -> Agent:
    return Agent(
        llm=LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test"),
        tools=[],
    )


def _setup_workspace_with_mock_client(
    host: str = "http://localhost:8000",
    conversation_id: str | None = None,
    fork_id: str | None = None,
    fork_tags: dict[str, str] | None = None,
) -> tuple[RemoteWorkspace, Mock]:
    """Set up workspace with a mock client that handles create + fork."""
    workspace = RemoteWorkspace(host=host, working_dir="/tmp")
    mock_client = Mock()
    workspace._client = mock_client

    if conversation_id is None:
        conversation_id = str(uuid.uuid4())
    if fork_id is None:
        fork_id = str(uuid.uuid4())

    def request_side_effect(method: str, url: str, **kwargs: object) -> Mock:
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None

        if method == "POST" and url == "/api/conversations":
            response.json.return_value = {
                "id": conversation_id,
                "conversation_id": conversation_id,
            }
        elif method == "POST" and url.endswith("/fork"):
            response.status_code = 201
            fork_response: dict[str, object] = {
                "id": fork_id,
                "conversation_id": fork_id,
                "tags": fork_tags or {},
            }
            response.json.return_value = fork_response
        elif method == "GET" and "/events" in url:
            response.json.return_value = {"items": [], "next_page_id": None}
        else:
            response.json.return_value = {}

        return response

    mock_client.request.side_effect = request_side_effect
    return workspace, mock_client


@patch("openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient")
def test_remote_fork_sends_post_request(mock_ws_cls: Mock) -> None:
    """fork() must POST to /{id}/fork."""
    mock_ws_cls.return_value = Mock()
    fork_uuid = str(uuid.uuid4())
    workspace, mock_client = _setup_workspace_with_mock_client(
        fork_id=fork_uuid,
    )

    conv = RemoteConversation(agent=_agent(), workspace=workspace)
    fork = conv.fork()

    assert fork.id == uuid.UUID(fork_uuid)

    # Verify a POST …/fork call was made
    fork_calls = [
        c
        for c in mock_client.request.call_args_list
        if c[0][0] == "POST" and str(c[0][1]).endswith("/fork")
    ]
    assert len(fork_calls) == 1


@patch("openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient")
def test_remote_fork_uses_server_returned_tags(mock_ws_cls: Mock) -> None:
    """The forked RemoteConversation constructor must receive tags from the
    server response (which merges title), not the raw input kwargs.

    We verify by monkeypatching RemoteConversation to capture the tags kwarg
    that the fork method passes to the constructor.
    """
    mock_ws_cls.return_value = Mock()
    server_tags = {"env": "test", "title": "My Fork"}
    workspace, _ = _setup_workspace_with_mock_client(fork_tags=server_tags)

    conv = RemoteConversation(agent=_agent(), workspace=workspace)

    # Capture the kwargs passed to the fork's RemoteConversation()
    captured_kwargs: dict[str, object] = {}
    _orig_cls = RemoteConversation

    class _Capture(_orig_cls):
        def __init__(self, **kwargs: object) -> None:  # type: ignore[override]
            captured_kwargs.update(kwargs)
            super().__init__(**kwargs)  # type: ignore[arg-type]

    # Temporarily replace the class reference used by the fork method.
    import openhands.sdk.conversation.impl.remote_conversation as _mod

    _mod.RemoteConversation = _Capture  # type: ignore[misc]
    try:
        conv.fork(title="My Fork", tags={"env": "test"})
    finally:
        _mod.RemoteConversation = _orig_cls  # type: ignore[misc]

    assert captured_kwargs.get("tags") == server_tags


@patch("openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient")
def test_remote_fork_raises_on_agent_param(mock_ws_cls: Mock) -> None:
    """Passing agent= must raise NotImplementedError for remote forks."""
    mock_ws_cls.return_value = Mock()
    workspace, _ = _setup_workspace_with_mock_client()

    conv = RemoteConversation(agent=_agent(), workspace=workspace)

    with pytest.raises(NotImplementedError, match="not supported"):
        conv.fork(agent=_agent())


@patch("openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient")
def test_remote_fork_passes_body_fields(mock_ws_cls: Mock) -> None:
    """Verify conversation_id, title, tags, reset_metrics are sent in body."""
    mock_ws_cls.return_value = Mock()
    custom_id = uuid.uuid4()
    workspace, mock_client = _setup_workspace_with_mock_client(
        fork_id=str(custom_id),
        fork_tags={"env": "prod"},
    )

    conv = RemoteConversation(agent=_agent(), workspace=workspace)
    conv.fork(
        conversation_id=custom_id,
        title="Test Fork",
        tags={"env": "prod"},
        reset_metrics=False,
    )

    fork_calls = [
        c
        for c in mock_client.request.call_args_list
        if c[0][0] == "POST" and str(c[0][1]).endswith("/fork")
    ]
    assert len(fork_calls) == 1

    body = fork_calls[0][1].get("json", {})
    assert body["id"] == str(custom_id)
    assert body["title"] == "Test Fork"
    assert body["tags"] == {"env": "prod"}
    assert body["reset_metrics"] is False
