"""Run a real agent-server from each supplied Python environment."""

import asyncio
import json
import os
import socket
import sys
import tempfile
import urllib.request
from pathlib import Path


async def probe(python: Path) -> None:
    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as workdir:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        environment = {
            **os.environ,
            "OH_CONVERSATION_RUNTIME": "local",
            "OH_ENABLE_VSCODE": "false",
            "OH_PRELOAD_TOOLS": "false",
            "OH_PERSISTENCE_DIR": str(Path(workdir) / "persistence"),
            "OPENHANDS_SUPPRESS_BANNER": "1",
        }
        process = await asyncio.create_subprocess_exec(
            str(python),
            "-m",
            "openhands.agent_server",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            cwd=workdir,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert process.stdout is not None
        output = []
        try:
            async with asyncio.timeout(60):
                while line := await process.stdout.readline():
                    output.append(line.decode())
                    if b"Uvicorn running on" in line:
                        break
                else:
                    raise RuntimeError("".join(output))
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/sub-agents",
                data=json.dumps({"load_user": False, "load_project": False}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                body = json.load(response)
                print(
                    python.parent.parent.name,
                    "HTTP",
                    response.status,
                    [agent["name"] for agent in body["agents"]],
                    flush=True,
                )
        finally:
            if process.returncode is None:
                process.terminate()
                await asyncio.wait_for(process.communicate(), timeout=20)


async def main() -> None:
    for executable in sys.argv[1:]:
        await probe(Path(executable).absolute())


asyncio.run(main())
