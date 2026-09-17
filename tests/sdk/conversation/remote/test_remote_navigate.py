"""Tests for RemoteConversation.navigate_to()."""

import uuid
from unittest.mock import Mock, patch

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
) -> tuple[RemoteWorkspace, Mock, str]:
    """Workspace + mock client that handles create, navigate, and state refresh."""
    workspace = RemoteWorkspace(host=host, working_dir="/tmp")
    mock_client = Mock()
    workspace._client = mock_client

    if conversation_id is None:
        conversation_id = str(uuid.uuid4())

    def request_side_effect(method: str, url: str, **kwargs: object) -> Mock:
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        if method == "POST" and url == "/api/conversations":
            response.json.return_value = {
                "id": conversation_id,
                "conversation_id": conversation_id,
            }
        elif method == "GET" and "/events" in url:
            response.json.return_value = {"items": [], "next_page_id": None}
        else:
            # navigate POST and the conversation-info refresh GET both land here.
            response.json.return_value = {
                "id": conversation_id,
                "leaf_event_id": "evt-7",
            }
        return response

    mock_client.request.side_effect = request_side_effect
    return workspace, mock_client, conversation_id


@patch("openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient")
def test_remote_navigate_posts_event_id(mock_ws_cls: Mock) -> None:
    """navigate_to(event) must POST {event_id} to /{id}/navigate."""
    mock_ws_cls.return_value = Mock()
    workspace, mock_client, cid = _setup_workspace_with_mock_client()

    conv = RemoteConversation(agent=_agent(), workspace=workspace)
    conv.navigate_to("evt-7")

    nav_calls = [
        c
        for c in mock_client.request.call_args_list
        if c[0][0] == "POST" and str(c[0][1]).endswith("/navigate")
    ]
    assert len(nav_calls) == 1
    assert str(nav_calls[0][0][1]).endswith(f"/{cid}/navigate")
    assert nav_calls[0][1].get("json", {}) == {"event_id": "evt-7"}


@patch("openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient")
def test_remote_navigate_none_posts_null(mock_ws_cls: Mock) -> None:
    """navigate_to(None) selects the empty tree — body carries event_id=None."""
    mock_ws_cls.return_value = Mock()
    workspace, mock_client, _ = _setup_workspace_with_mock_client()

    conv = RemoteConversation(agent=_agent(), workspace=workspace)
    conv.navigate_to(None)

    nav_calls = [
        c
        for c in mock_client.request.call_args_list
        if c[0][0] == "POST" and str(c[0][1]).endswith("/navigate")
    ]
    assert len(nav_calls) == 1
    assert nav_calls[0][1].get("json", {}) == {"event_id": None}


@patch("openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient")
def test_remote_navigate_refreshes_state(mock_ws_cls: Mock) -> None:
    """navigate_to refreshes cached state, since leaf_event_id isn't broadcast."""
    mock_ws_cls.return_value = Mock()
    workspace, mock_client, cid = _setup_workspace_with_mock_client()

    conv = RemoteConversation(agent=_agent(), workspace=workspace)
    conv.navigate_to("evt-7")

    # The conversation-info GET (refresh_from_server) must come *after* the
    # navigate POST — asserting order (not mere presence) keeps the test honest
    # even if __init__ later starts fetching the same path.
    calls = mock_client.request.call_args_list
    nav_idx = next(
        i
        for i, c in enumerate(calls)
        if c[0][0] == "POST" and str(c[0][1]).endswith("/navigate")
    )
    refresh_after = [
        c
        for c in calls[nav_idx + 1 :]
        if c[0][0] == "GET" and str(c[0][1]).endswith(f"/api/conversations/{cid}")
    ]
    assert len(refresh_after) >= 1
