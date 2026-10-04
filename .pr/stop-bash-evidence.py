"""Verbose stop endpoint evidence for PR #5348 (Linux-only, temporary)."""

import asyncio
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI

from openhands.agent_server import bash_router as bash_router_module
from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.config import Config
from openhands.agent_server.server_details_router import (
    mark_initialization_complete,
    server_details_router,
)


async def main() -> None:
    if sys.platform == "win32":
        print("SKIP: needs the Unix process-group backend")
        return
    tmp = Path(tempfile.mkdtemp(prefix="stop-evidence-"))
    service = BashEventService(bash_events_dir=tmp / "bash_events")
    async with service:
        app = FastAPI()
        app.state.config = Config()
        app.state.bash_event_service = service
        app.include_router(server_details_router)
        app.include_router(bash_router_module.bash_router, prefix="/api")
        mark_initialization_complete()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            marker = tmp / "stop_cleanup_ran"
            ready = tmp / "shell_ready"
            command = f"trap 'touch {marker}; exit 0' TERM; touch {ready}; sleep 30"
            print(f"COMMAND: {command}")
            start = await client.post(
                "/api/bash/start_bash_command",
                json={"command": command, "timeout": 60},
            )
            print(f"START: status={start.status_code} body={start.text}")
            assert start.status_code == 200, start.text
            cmd_id = start.json()["id"]
            print(f"COMMAND_ID: {cmd_id}")

            deadline = time.monotonic() + 10
            while not ready.exists():
                assert time.monotonic() < deadline, "shell never ready"
                await asyncio.sleep(0.1)
            print("SHELL_READY: TERM trap installed")

            stop = await client.post(f"/api/bash/bash_commands/{cmd_id}/stop")
            print(f"STOP: status={stop.status_code} body={stop.text}")
            assert stop.status_code == 200, stop.text
            assert stop.json() == {"success": True}

            end = time.monotonic() + 8
            items: list[dict] = []
            while time.monotonic() < end:
                resp = await client.get(
                    "/api/bash/bash_events/search",
                    params={"command_id__eq": cmd_id},
                )
                items = resp.json()["items"]
                terminal = [
                    e
                    for e in items
                    if e["kind"] == "BashOutput" and e.get("exit_code") is not None
                ]
                if terminal:
                    print(f"EVENT_STREAM: {len(items)} events")
                    for e in terminal:
                        print(
                            "TERMINAL_OUTPUT: "
                            f"exit_code={e.get('exit_code')} "
                            f"stdout={e.get('stdout')!r} "
                            f"stderr={e.get('stderr')!r}"
                        )
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("no terminal output after stop")

            await asyncio.sleep(0.2)
            print(f"CLEANUP_MARKER: exists={marker.exists()} path={marker}")
            assert marker.exists(), "SIGTERM trap did not run"

            repeat = await client.post(f"/api/bash/bash_commands/{cmd_id}/stop")
            print(f"REPEAT_STOP_FINISHED: status={repeat.status_code}")
            assert repeat.status_code == 200

            unknown = await client.post(f"/api/bash/bash_commands/{uuid4()}/stop")
            print(f"UNKNOWN_STOP: status={unknown.status_code}")
            assert unknown.status_code == 404

            search = await client.get(
                "/api/bash/bash_events/search",
                params={"command_id__eq": cmd_id},
            )
            print(f"SEARCH_AFTER_STOP: status={search.status_code}")
            print(f"EVIDENCE_TMPDIR: {tmp}")
    print("EVIDENCE_OK")


if __name__ == "__main__":
    asyncio.run(main())
