from __future__ import annotations

import argparse
import asyncio
import socket
import threading
import time
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path

import requests
import uvicorn
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse

from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StoredConversation
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.fifo_lock import FIFOLock
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import Event
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.workspace import LocalWorkspace


warnings.filterwarnings(
    "ignore", category=DeprecationWarning, module=r"websockets(\..*)?"
)
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    module=r"uvicorn\.protocols\.websockets\.websockets_impl",
)
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    module=r"litellm\.llms\.custom_httpx\.async_client_cleanup",
)


@dataclass(slots=True)
class ProbeResult:
    status: str
    elapsed_s: float
    detail: str


@dataclass(slots=True)
class ModeResult:
    mode: str
    websocket_result: str
    health: ProbeResult
    ready: ProbeResult


class SnapshotState:
    def __init__(self) -> None:
        self._lock = FIFOLock()
        self.execution_status = ConversationExecutionStatus.IDLE

    def __enter__(self) -> SnapshotState:
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._lock.release()

    def model_dump(
        self, mode: str = "json", exclude_none: bool = True
    ) -> dict[str, str]:
        del mode, exclude_none
        return {"execution_status": self.execution_status.value}


class ConversationStub:
    def __init__(self, state: SnapshotState) -> None:
        self._state = state


class SilentSubscriber:
    async def __call__(self, _event: Event) -> None:
        return None


class ServerHarness:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        agent = Agent(llm=LLM(model="gpt-4o", usage_id="repro"), tools=[])
        workspace = LocalWorkspace(working_dir="/tmp/repro-event-loop-workspace")
        self.stored = StoredConversation(
            id=uuid.uuid4(), agent=agent, workspace=workspace
        )
        self.service = EventService(
            stored=self.stored,
            conversations_dir=Path("/tmp/repro-event-loop-conversations"),
        )
        self.state = SnapshotState()
        self.service._conversation = ConversationStub(self.state)
        if mode == "vulnerable":
            self.service.subscribe_to_events = self._subscribe_to_events_vulnerable

        self.port = self._find_free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.ws_url = f"ws://127.0.0.1:{self.port}/sockets/events/{self.stored.id}"
        self.server = uvicorn.Server(
            uvicorn.Config(
                self._build_app(),
                host="127.0.0.1",
                port=self.port,
                log_level="warning",
                lifespan="off",
            )
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    async def _subscribe_to_events_vulnerable(self, subscriber) -> uuid.UUID:
        subscriber_id = self.service._pub_sub.subscribe(subscriber)
        if self.service._conversation:
            state = self.service._conversation._state
            with state:
                ConversationStateUpdateEvent.from_conversation_state(state)
        return subscriber_id

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/health")
        async def health() -> PlainTextResponse:
            return PlainTextResponse("OK")

        @app.get("/ready")
        async def ready() -> JSONResponse:
            return JSONResponse({"status": "ready"})

        @app.websocket("/sockets/events/{conversation_id}")
        async def events_socket(websocket: WebSocket, conversation_id: str) -> None:
            del conversation_id
            await websocket.accept()
            await self.service.subscribe_to_events(SilentSubscriber())
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                return

        return app

    def start(self) -> None:
        self.thread.start()
        for _ in range(50):
            try:
                response = requests.get(f"{self.base_url}/health", timeout=0.2)
                if response.status_code == 200:
                    return
            except requests.RequestException:
                time.sleep(0.05)
        raise RuntimeError(f"{self.mode}: server failed to start")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=2)


def probe(base_url: str, path: str, timeout_s: float) -> ProbeResult:
    started = time.monotonic()
    try:
        response = requests.get(f"{base_url}{path}", timeout=timeout_s)
    except requests.RequestException as exc:
        return ProbeResult(
            status="TIMEOUT",
            elapsed_s=time.monotonic() - started,
            detail=f"{type(exc).__name__}: {exc}",
        )
    return ProbeResult(
        status="OK",
        elapsed_s=time.monotonic() - started,
        detail=f"{response.status_code} {response.text!r}",
    )


def trigger_websocket(ws_url: str, hold_s: float, result: dict[str, str]) -> None:
    async def _connect() -> None:
        try:
            async with websockets.connect(ws_url, open_timeout=1) as ws:
                result["result"] = "connected"
                await asyncio.sleep(hold_s)
                await ws.close()
        except Exception as exc:  # noqa: BLE001 - report exact handshake behavior
            result["result"] = type(exc).__name__

    asyncio.run(_connect())


def run_mode(mode: str) -> ModeResult:
    harness = ServerHarness(mode)
    harness.start()
    release_lock = threading.Event()
    lock_ready = threading.Event()

    def hold_state_lock() -> None:
        with harness.state:
            lock_ready.set()
            release_lock.wait(timeout=2)

    lock_thread = threading.Thread(target=hold_state_lock, daemon=True)
    lock_thread.start()
    if not lock_ready.wait(timeout=1):
        harness.stop()
        raise RuntimeError(f"{mode}: failed to acquire state lock")

    websocket_result: dict[str, str] = {}
    websocket_thread = threading.Thread(
        target=trigger_websocket,
        args=(harness.ws_url, 0.2, websocket_result),
        daemon=True,
    )
    websocket_thread.start()
    time.sleep(0.1)

    health = probe(harness.base_url, "/health", timeout_s=0.5)
    ready = probe(harness.base_url, "/ready", timeout_s=0.5)

    release_lock.set()
    lock_thread.join(timeout=1)
    websocket_thread.join(timeout=1)
    harness.stop()

    return ModeResult(
        mode=mode,
        websocket_result=websocket_result.get("result", "no-result"),
        health=health,
        ready=ready,
    )


def validate(result: ModeResult) -> None:
    if result.mode == "vulnerable":
        assert result.health.status == "TIMEOUT", result
        assert result.ready.status == "TIMEOUT", result
        return
    assert result.health.status == "OK", result
    assert result.ready.status == "OK", result


def print_result(result: ModeResult) -> None:
    print(f"[{result.mode}] websocket reconnect result: {result.websocket_result}")
    for path, probe_result in (("/health", result.health), ("/ready", result.ready)):
        print(
            f"[{result.mode}] {path:<7} -> {probe_result.status} in "
            f"{probe_result.elapsed_s:.2f}s ({probe_result.detail})"
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["vulnerable", "fixed", "both"],
        default="both",
        help="Which implementation to run.",
    )
    args = parser.parse_args()

    modes = [args.mode] if args.mode != "both" else ["vulnerable", "fixed"]
    results = [run_mode(mode) for mode in modes]
    for result in results:
        print_result(result)
        validate(result)

    if args.mode == "both":
        print(
            "Repro succeeded: vulnerable mode wedges the event loop while the "
            "current code keeps /health and /ready responsive."
        )


if __name__ == "__main__":
    main()
