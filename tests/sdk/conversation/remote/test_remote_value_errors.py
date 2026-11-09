"""RemoteConversation raises ValueError where BaseConversation documents it."""

import uuid
from unittest.mock import Mock, patch

import httpx
import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.exceptions import RemoteConversationValueError
from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import RemoteWorkspace


def _conversation(status_code: int, detail: str) -> RemoteConversation:
    """A RemoteConversation whose condense and navigate calls fail."""
    conversation_id = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(("/condense", "/navigate")):
            return httpx.Response(status_code, json={"detail": detail})
        if request.method == "POST" and path == "/api/conversations":
            return httpx.Response(201, json={"id": conversation_id})
        if "/events" in path:
            return httpx.Response(200, json={"items": [], "next_page_id": None})
        return httpx.Response(200, json={"id": conversation_id})

    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")
    workspace._client = httpx.Client(
        base_url="http://localhost:8000", transport=httpx.MockTransport(handler)
    )
    agent = Agent(
        llm=LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test"),
        tools=[],
    )
    return RemoteConversation(agent=agent, workspace=workspace)


@pytest.mark.parametrize(
    ("status_code", "detail", "call"),
    [
        (
            409,
            "Cannot condense conversation: No condenser configured",
            lambda conversation: conversation.condense(),
        ),
        (
            404,
            "Unknown event_id: no-such-event",
            lambda conversation: conversation.navigate_to("no-such-event"),
        ),
    ],
    ids=["condense-without-condenser", "navigate-to-unknown-event"],
)
@patch("openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient")
def test_rejected_call_is_a_value_error_and_an_http_error(
    mock_ws_cls: Mock, status_code: int, detail: str, call
) -> None:
    mock_ws_cls.return_value = Mock()
    conversation = _conversation(status_code, detail)

    with pytest.raises(RemoteConversationValueError) as raised:
        call(conversation)

    assert isinstance(raised.value, ValueError)
    assert isinstance(raised.value, httpx.HTTPStatusError)
    assert raised.value.response.status_code == status_code
    assert str(raised.value) == detail


@pytest.mark.parametrize("status_code", [500, 503])
@patch("openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient")
def test_other_server_errors_stay_plain_http_errors(
    mock_ws_cls: Mock, status_code: int
) -> None:
    mock_ws_cls.return_value = Mock()
    conversation = _conversation(status_code, "server error")

    for call in (conversation.condense, lambda: conversation.navigate_to("evt")):
        with pytest.raises(httpx.HTTPStatusError) as raised:
            call()
        assert not isinstance(raised.value, ValueError)
