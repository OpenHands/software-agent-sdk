"""Property test: a remote command's result carries its own output exactly once.

A fake agent server keeps a bash event store and answers
``/bash/bash_events/search`` the way the API documents it (``kind__eq``,
``command_id__eq``, ``order__gt``, ``sort_order``, ``limit`` and ``page_id``
filter and page the stored events; other parameters are ignored). The store
already holds earlier commands' events, and this command's output chunks become
visible a few at a time between polls. Whatever the chunking, the
``CommandResult`` has the concatenation of this command's own stdout and stderr
chunks, each exactly once and in order, and its final exit code.
"""

from typing import Any
from unittest.mock import patch
from uuid import UUID

import httpx
from hypothesis import given, strategies as st

from openhands.sdk.workspace.models import CommandResult
from openhands.sdk.workspace.remote.remote_workspace_mixin import RemoteWorkspaceMixin


OUTPUT_TEXT = st.text(max_size=3)
MAX_REQUESTS = 100


class FakeBashEventsServer:
    def __init__(
        self,
        earlier_outputs: list[tuple[str, int]],
        chunks: list[tuple[str, str]],
        exit_code: int,
        released_per_poll: list[int],
    ) -> None:
        self._last_id = 0
        self.events: list[dict[str, Any]] = []
        for stdout, order in earlier_outputs:
            earlier_command_id = self._store_command()
            self._store(self._output(earlier_command_id, order, stdout, "", 0))
        self.command_id = self._store_command()
        last_order = len(chunks) - 1
        self.pending = [
            self._output(
                self.command_id,
                order,
                stdout,
                stderr,
                exit_code if order == last_order else None,
            )
            for order, (stdout, stderr) in enumerate(chunks)
        ]
        self.released_per_poll = released_per_poll
        self.polls = 0

    def _new_id(self) -> str:
        self._last_id += 1
        return str(UUID(int=self._last_id))

    def _store(self, event: dict[str, Any]) -> None:
        self.events.append({**event, "timestamp": len(self.events)})

    def _store_command(self) -> str:
        command_id = self._new_id()
        self._store({"kind": "BashCommand", "id": command_id, "command": "cmd"})
        return command_id

    def _output(
        self,
        command_id: str,
        order: int,
        stdout: str,
        stderr: str,
        exit_code: int | None,
    ) -> dict[str, Any]:
        return {
            "kind": "BashOutput",
            "id": self._new_id(),
            "command_id": command_id,
            "order": order,
            "stdout": stdout or None,
            "stderr": stderr or None,
            "exit_code": exit_code,
        }

    def _release_pending_chunks(self) -> None:
        self.polls += 1
        if self.polls > len(self.released_per_poll):
            due = len(self.pending)
        else:
            due = self.released_per_poll[self.polls - 1]
        for event in self.pending[:due]:
            self._store(event)
        del self.pending[:due]

    def _search(self, params: dict[str, Any]) -> dict[str, Any]:
        self._release_pending_chunks()
        items = [
            e
            for e in self.events
            if ("kind__eq" not in params or e["kind"] == params["kind__eq"])
            and (
                "command_id__eq" not in params
                or e.get("command_id") == str(params["command_id__eq"])
            )
            and (
                "order__gt" not in params
                or e["kind"] != "BashOutput"
                or e["order"] > int(params["order__gt"])
            )
        ]
        items.sort(
            key=lambda e: e["timestamp"],
            reverse=params.get("sort_order") == "TIMESTAMP_DESC",
        )
        start = next(
            (i for i, e in enumerate(items) if e["id"] == params.get("page_id")), 0
        )
        limit = int(params.get("limit", 100))
        rest = items[start + limit :]
        return {
            "items": items[start : start + limit],
            "next_page_id": rest[0]["id"] if rest else None,
        }

    def handle(self, request: dict[str, Any]) -> httpx.Response:
        url: str = request["url"]
        if url.endswith("/bash/start_bash_command"):
            body: dict[str, Any] = {"id": self.command_id, "kind": "BashCommand"}
        elif url.endswith("/bash/bash_events/search"):
            body = self._search(request.get("params") or {})
        else:
            raise AssertionError(f"unexpected request to {url}")
        return httpx.Response(
            200, json=body, request=httpx.Request(request["method"], url)
        )


def execute_against(server: FakeBashEventsServer) -> CommandResult:
    workspace = RemoteWorkspaceMixin(host="http://agent-server", working_dir="/w")
    generator = workspace._execute_command_generator("cmd", None, 30.0)
    with patch("time.sleep"):
        request = next(generator)
        for _ in range(MAX_REQUESTS):
            try:
                request = generator.send(server.handle(request))
            except StopIteration as stop:
                return stop.value
    raise AssertionError(f"no result after {MAX_REQUESTS} requests")


@given(
    earlier_outputs=st.lists(st.tuples(OUTPUT_TEXT, st.integers(0, 5)), max_size=20),
    chunks=st.lists(st.tuples(OUTPUT_TEXT, OUTPUT_TEXT), min_size=1, max_size=6),
    exit_code=st.integers(0, 255),
    released_per_poll=st.lists(st.integers(0, 3), max_size=8),
)
def test_execute_command_returns_own_output_exactly_once(
    earlier_outputs: list[tuple[str, int]],
    chunks: list[tuple[str, str]],
    exit_code: int,
    released_per_poll: list[int],
) -> None:
    server = FakeBashEventsServer(earlier_outputs, chunks, exit_code, released_per_poll)

    result = execute_against(server)

    assert result.stdout == "".join(stdout for stdout, _ in chunks)
    assert result.stderr == "".join(stderr for _, stderr in chunks)
    assert result.exit_code == exit_code
