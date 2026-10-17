"""Exercise custom event transports against a real Agent Server over Unix sockets.

Run from the SDK checkout: uv run python .pr/verify_websocket_factory.py
No model request, Docker daemon, relay service, or provider credential is needed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from pydantic import SecretStr
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import unix_connect
from websockets.sync.connection import Connection

from openhands.sdk import LLM, Agent, Conversation, RemoteConversation, TextContent
from openhands.sdk.conversation.exceptions import WebSocketConnectionError
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.conversation.types import ConversationCallbackType
from openhands.sdk.event import ConversationStateUpdateEvent, Event, MessageEvent
from openhands.sdk.workspace import LocalWorkspace, RemoteWorkspace


if TYPE_CHECKING:
    from openhands.sdk import WebSocketClientFactory


class UnixWorkspace(RemoteWorkspace):
    socket_path: str

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.host,
                transport=httpx.HTTPTransport(uds=self.socket_path),
                headers=self._headers,
                timeout=10,
            )
        return self._client


class UnixEventClient:
    def __init__(
        self,
        socket_path: str,
        *,
        host: str,
        conversation_id: str,
        callback: ConversationCallbackType,
        api_key: str | None,
        on_reconnect: Callable[[], object] | None,
    ):
        self.socket_path = socket_path
        self.uri = (
            host.replace("http://", "ws://") + f"/sockets/events/{conversation_id}"
        )
        self.callback = callback
        self.api_key = api_key
        self.on_reconnect = on_reconnect
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None
        self.connection: Connection | None = None
        self.lock = threading.Lock()
        self.reconnects = 0

    def start(self) -> None:
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()
        with self.lock:
            connection = self.connection
        if connection is not None:
            connection.close()
        if self.thread is not None:
            self.thread.join(timeout=5)
            assert not self.thread.is_alive(), "custom event worker leaked"

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        return self.ready.wait(timeout)

    def run(self) -> None:
        connected_once = False
        while not self.stopped.is_set():
            try:
                with unix_connect(
                    self.socket_path, uri=self.uri, open_timeout=1, close_timeout=1
                ) as connection:
                    with self.lock:
                        self.connection = connection
                    connection.send(
                        json.dumps({"type": "auth", "session_api_key": self.api_key})
                    )
                    subscribed = False
                    while not self.stopped.is_set():
                        try:
                            message = connection.recv(timeout=0.1)
                        except TimeoutError:
                            continue
                        event = Event.model_validate_json(message)
                        if not subscribed and isinstance(
                            event, ConversationStateUpdateEvent
                        ):
                            if connected_once:
                                if self.on_reconnect is not None:
                                    self.on_reconnect()
                                self.reconnects += 1
                            connected_once = subscribed = True
                            self.ready.set()
                        self.callback(event)
            except (OSError, ConnectionClosed, httpx.HTTPError):
                self.stopped.wait(0.05)
            finally:
                with self.lock:
                    self.connection = None


def wait_for(predicate: Callable[[], bool], description: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(description)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="oh4708-", dir="/tmp") as directory:
        root = Path(directory)
        socket_path = root / "server.sock"
        workspace_path = root / "workspace"
        workspace_path.mkdir()
        config_path = root / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "session_api_keys": ["transport-test-key"],
                    "conversations_path": str(root / "conversations"),
                    "workspace_path": str(workspace_path),
                    "bash_events_dir": str(root / "bash-events"),
                }
            )
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "LANG", "LC_ALL", "PYTHONPATH", "SYSTEMROOT"}
        }
        env.update(
            OPENHANDS_AGENT_SERVER_CONFIG_PATH=str(config_path),
            OH_PERSISTENCE_DIR=str(root / "persistence"),
            OPENHANDS_SUPPRESS_BANNER="1",
            NO_PROXY="127.0.0.1,localhost",
        )
        process: subprocess.Popen | None = None
        log = (root / "server.log").open("w+")
        workspace = UnixWorkspace(
            host="http://transport.invalid",
            socket_path=str(socket_path),
            working_dir=str(workspace_path),
            api_key="transport-test-key",
        )
        conversations: list[RemoteConversation] = []
        clients: list[UnixEventClient] = []

        def stop_server() -> None:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        def start_server() -> subprocess.Popen:
            socket_path.unlink(missing_ok=True)
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "openhands.agent_server.api:create_app",
                    "--factory",
                    "--uds",
                    str(socket_path),
                    "--ws",
                    "wsproto",
                    "--log-level",
                    "warning",
                ],
                env=env,
                stdout=log,
                stderr=log,
            )

            def healthy() -> bool:
                if child.poll() is not None:
                    log.seek(0)
                    raise RuntimeError(log.read()[-3000:])
                try:
                    return workspace.client.get("/health").status_code == 200
                except httpx.HTTPError:
                    return False

            try:
                wait_for(healthy, "Agent Server did not start")
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read()[-5000:], file=sys.stderr)
                child.terminate()
                child.wait(timeout=10)
                raise
            return child

        def make_client(
            *,
            host: str,
            conversation_id: str,
            callback: ConversationCallbackType,
            api_key: str | None,
            on_reconnect: Callable[[], object] | None,
        ) -> UnixEventClient:
            client = UnixEventClient(
                str(socket_path),
                host=host,
                conversation_id=conversation_id,
                callback=callback,
                api_key=api_key,
                on_reconnect=on_reconnect,
            )
            clients.append(client)
            return client

        factory: WebSocketClientFactory = make_client
        events_a: list[Event] = []
        events_b: list[Event] = []

        def has_message(events: list[Event], text: str) -> bool:
            return any(
                isinstance(event, MessageEvent)
                and any(
                    isinstance(content, TextContent) and text in content.text
                    for content in event.llm_message.content
                )
                for event in events
            )

        try:
            process = start_server()
            agent = Agent(
                llm=LLM(model="gpt-4o-mini", api_key=SecretStr("unused")), tools=[]
            )
            parent = Conversation(
                agent,
                workspace=workspace,
                websocket_client_factory=factory,
                callbacks=[events_a.append],
                visualizer=None,
                delete_on_close=False,
            )
            assert isinstance(parent, RemoteConversation)
            conversations.append(parent)
            parent.send_message("before restart")
            wait_for(
                lambda: has_message(events_a, "before restart"),
                "public factory lost event",
            )
            child = parent.fork()
            conversations.append(child)
            created = RemoteConversation.create(
                workspace,
                StartConversationRequest(
                    agent=agent,
                    workspace=LocalWorkspace(working_dir=str(workspace_path)),
                ),
                callbacks=[events_b.append],
                visualizer=None,
                websocket_client_factory=factory,
            )
            conversations.append(created)
            attached = RemoteConversation.attach(
                workspace,
                parent.id,
                visualizer=None,
                websocket_client_factory=factory,
            )
            conversations.append(attached)
            created.send_message("independent transport")
            wait_for(
                lambda: has_message(events_b, "independent transport"),
                "create lost event",
            )
            assert not has_message(events_a, "independent transport")
            assert len(clients) == 4 and len({id(client) for client in clients}) == 4
            stop_server()
            process = start_server()
            wait_for(
                lambda: all(client.reconnects for client in clients),
                "existing transports did not reconnect",
            )
            attached.send_message("after restart")
            wait_for(
                lambda: has_message(events_a, "after restart"),
                "resumed transport lost event",
            )

            def unauthenticated_client(**kwargs) -> UnixEventClient:
                return make_client(**(kwargs | {"api_key": "wrong-key"}))

            previous = os.environ.get("OPENHANDS_REMOTE_WS_READY_TIMEOUT")
            os.environ["OPENHANDS_REMOTE_WS_READY_TIMEOUT"] = "0.3"
            try:
                try:
                    RemoteConversation.attach(
                        workspace,
                        parent.id,
                        visualizer=None,
                        websocket_client_factory=unauthenticated_client,
                    )
                except WebSocketConnectionError:
                    assert clients[-1].stopped.is_set()
                    assert (
                        clients[-1].thread is not None
                        and not clients[-1].thread.is_alive()
                    )
                else:
                    raise AssertionError("invalid credentials unexpectedly subscribed")
            finally:
                if previous is None:
                    os.environ.pop("OPENHANDS_REMOTE_WS_READY_TIMEOUT", None)
                else:
                    os.environ["OPENHANDS_REMOTE_WS_READY_TIMEOUT"] = previous
            print(
                json.dumps(
                    {
                        "public_create_attach_fork": "passed",
                        "uds_rest_and_websocket_auth": "passed",
                        "independent_clients": 4,
                        "server_restart_reconnect": "passed",
                        "failed_startup_cleanup": "passed",
                    }
                )
            )
        finally:
            try:
                for conversation in reversed(conversations):
                    conversation.close()
                assert all(
                    client.stopped.is_set()
                    and (client.thread is None or not client.thread.is_alive())
                    for client in clients
                ), "SDK close returned with a live custom transport"
            finally:
                for client in clients:
                    client.stop()
                workspace.reset_client()
                stop_server()
                log.close()
        assert all(
            client.thread is None or not client.thread.is_alive() for client in clients
        )
        assert process is None or process.poll() is not None


if __name__ == "__main__":
    main()
