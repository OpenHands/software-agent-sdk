"""The remote branch read must not trust the append-only event cache."""

import httpx
import pytest

from openhands.sdk.conversation.impl.remote_conversation import RemoteState
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import Message, TextContent


def test_active_branch_uses_authoritative_endpoint_and_limit():
    abandoned = MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="abandoned")]),
    )
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={
                    "items": [abandoned.model_dump(mode="json")],
                    "next_page_id": None,
                },
            )
        assert request.url.path.endswith("/events/active-branch")
        return httpx.Response(200, json=[])

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://server"
    ) as client:
        state = RemoteState(client, "conversation", events_base_path="/api/custom")
        assert len(state.events) == 1
        assert state.active_branch(limit=10) == []
        assert requests[-1].url.params["limit"] == "10"
        assert requests[-1].url.path == "/api/custom/conversation/events/active-branch"


@pytest.mark.parametrize("status", [404, 409])
def test_active_branch_errors_never_fall_back_to_full_history(status):
    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"items": [], "next_page_id": None})
        return httpx.Response(status, json={"detail": "branch snapshot unavailable"})

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://server"
    ) as client:
        state = RemoteState(client, "conversation")
        with pytest.raises(httpx.HTTPStatusError) as error:
            state.active_branch()
        assert error.value.response.status_code == status
        if status == 404:
            assert "upgrade" in str(error.value)
