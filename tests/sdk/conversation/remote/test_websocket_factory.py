from asyncio import CancelledError
from collections.abc import Callable

import pytest

from openhands.sdk import Conversation, RemoteConversation, WebSocketClientFactory
from openhands.sdk.conversation.exceptions import WebSocketConnectionError
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.conversation.types import ConversationCallbackType
from openhands.sdk.event import Event, MessageEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.workspace import LocalWorkspace


class EventClient:
    def __init__(
        self,
        callback: ConversationCallbackType,
        conversation_id: str,
        *,
        ready: bool,
        start_error: type[BaseException] | None,
    ):
        self.callback = callback
        self.conversation_id = conversation_id
        self.ready = ready
        self.start_error = start_error
        self.stop_calls = 0

    def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error("custom transport startup failed")
        self.callback(
            MessageEvent(
                source="agent",
                llm_message=Message(
                    role="assistant", content=[TextContent(text="custom transport")]
                ),
            )
        )

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        return self.ready

    def stop(self) -> None:
        self.stop_calls += 1


class EventClientFactory:
    def __init__(
        self, *, ready: bool = True, start_error: type[BaseException] | None = None
    ):
        self.clients: list[EventClient] = []
        self.ready = ready
        self.start_error = start_error

    def __bool__(self) -> bool:
        return False

    def __call__(
        self,
        *,
        host: str,
        conversation_id: str,
        callback: ConversationCallbackType,
        api_key: str | None,
        on_reconnect: Callable[[], object] | None,
    ) -> EventClient:
        client = EventClient(
            callback,
            conversation_id,
            ready=self.ready,
            start_error=self.start_error,
        )
        self.clients.append(client)
        return client


@pytest.mark.parametrize("entry_point", ["public", "direct", "create", "attach"])
def test_custom_transport_delivers_events_and_closes(
    remote_transport_workspace, entry_point
):
    workspace, agent, _ = remote_transport_workspace
    custom = EventClientFactory()
    factory: WebSocketClientFactory = custom
    received: list[Event] = []

    if entry_point == "public":
        conversation = Conversation(
            agent=agent,
            workspace=workspace,
            websocket_client_factory=factory,
            callbacks=[received.append],
            visualizer=None,
        )
    elif entry_point == "direct":
        conversation = RemoteConversation(
            agent=agent,
            workspace=workspace,
            websocket_client_factory=factory,
            callbacks=[received.append],
            visualizer=None,
        )
    elif entry_point == "create":
        conversation = RemoteConversation.create(
            workspace,
            StartConversationRequest(
                agent=agent, workspace=LocalWorkspace(working_dir=workspace.working_dir)
            ),
            websocket_client_factory=factory,
            callbacks=[received.append],
            visualizer=None,
        )
    else:
        info = workspace.client.post("/api/conversations", json={}).json()
        conversation = RemoteConversation.attach(
            workspace,
            info["id"],
            websocket_client_factory=factory,
            callbacks=[received.append],
            visualizer=None,
        )

    assert isinstance(conversation, RemoteConversation)
    try:
        assert any(isinstance(event, MessageEvent) for event in received)
        assert custom.clients[0].conversation_id == str(conversation.id)
        assert custom.clients[0].stop_calls == 0
    finally:
        conversation.close()
    conversation.close()
    assert custom.clients[0].stop_calls == 1


def test_fork_keeps_factory_but_owns_an_independent_transport(
    remote_transport_workspace,
):
    workspace, agent, _ = remote_transport_workspace
    custom = EventClientFactory()
    parent = RemoteConversation(
        agent,
        workspace,
        websocket_client_factory=custom,
        visualizer=None,
    )
    try:
        child = parent.fork()
        try:
            assert len(custom.clients) == 2
            assert custom.clients[0].conversation_id == str(parent.id)
            assert custom.clients[1].conversation_id == str(child.id)
            assert child.id != parent.id
            child.close()
            assert custom.clients[1].stop_calls == 1
            assert custom.clients[0].stop_calls == 0
        finally:
            child.close()
    finally:
        parent.close()
    assert custom.clients[0].stop_calls == 1


@pytest.mark.parametrize("start_error", [None, ConnectionError, CancelledError])
def test_attach_failure_stops_custom_transport(remote_transport_workspace, start_error):
    workspace, _, _ = remote_transport_workspace
    info = workspace.client.post("/api/conversations", json={}).json()
    custom = EventClientFactory(ready=False, start_error=start_error)
    error = WebSocketConnectionError if start_error is None else start_error
    with pytest.raises(error):
        RemoteConversation.attach(
            workspace,
            info["id"],
            websocket_client_factory=custom,
            visualizer=None,
        )
    assert custom.clients[0].stop_calls == 1


def test_local_workspace_rejects_remote_transport(remote_transport_workspace, tmp_path):
    _, agent, _ = remote_transport_workspace
    custom = EventClientFactory()
    with pytest.raises(ValueError, match="only supported with a RemoteWorkspace"):
        Conversation(
            agent=agent,
            workspace=tmp_path,
            websocket_client_factory=custom,
        )
    assert not custom.clients
