"""Exercise resume failure and recovery over real subprocess JSON-RPC, without an LLM.

Run: uv run python .pr/acp_resume_smoke.py
"""

import asyncio
import json
import sys
import tempfile
import uuid
from contextlib import closing
from pathlib import Path

from acp.exceptions import RequestError

from openhands.sdk import Conversation
from openhands.sdk.agent import ACPAgent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent
from openhands.sdk.event.conversation_error import ConversationErrorEvent


SESSION = "smoke-original-session"


def serve(root: Path) -> None:
    for line in sys.stdin:
        request = json.loads(line)
        method = request.get("method")
        with (root / "calls.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(request) + "\n")
        if "id" not in request:
            continue
        result = {}
        if method == "initialize":
            result = {
                "protocolVersion": 1,
                "agentCapabilities": {"loadSession": True},
                "authMethods": [],
                "agentInfo": {"name": "resume-smoke", "version": "1.0"},
            }
        elif method == "session/new":
            result = {"sessionId": SESSION}
        elif method == "session/load":
            assert request["params"]["sessionId"] == SESSION
            if (root / "fail-resume").exists():
                print(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": request["id"],
                            "error": {
                                "code": -32603,
                                "message": "Internal error",
                                "data": {"reason": "simulated active writer"},
                            },
                        }
                    ),
                    flush=True,
                )
                continue
        elif method == "session/prompt":
            memory = root / "memory.txt"
            if not memory.exists():
                memory.write_text("original-context-marker", encoding="utf-8")
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": SESSION,
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": memory.read_text()},
                            },
                        },
                    }
                ),
                flush=True,
            )
            result = {"stopReason": "end_turn"}
        print(
            json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}),
            flush=True,
        )


async def exercise(root: Path, async_run: bool) -> None:
    conversation_id = uuid.uuid4()

    def open_conversation():
        return Conversation(
            agent=ACPAgent(
                acp_command=[
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--server",
                    str(root),
                ]
            ),
            workspace=str(root),
            persistence_dir=str(root / "conversations"),
            conversation_id=conversation_id,
            delete_on_close=False,
            visualizer=None,
        )

    async def run(conversation):
        if async_run:
            await conversation.arun()
        else:
            conversation.run()

    with closing(open_conversation()) as first:
        first.send_message("Remember the context marker.")
        await run(first)
        assert first.state.agent_state["acp_session_id"] == SESSION

    (root / "fail-resume").touch()
    with closing(open_conversation()) as failed:
        failed.send_message("Recall the context marker.")
        try:
            await run(failed)
        except RequestError as exc:
            assert "No new session was started" in str(exc)
        else:
            raise AssertionError("Resume failure was silently accepted")
        assert failed.state.execution_status == ConversationExecutionStatus.ERROR
        assert failed.state.agent_state["acp_session_id"] == SESSION
        errors = [
            e for e in failed.state.events if isinstance(e, ConversationErrorEvent)
        ]
        assert any("simulated active writer" in e.detail for e in errors)

    calls = [
        json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()
    ]
    assert sum(c.get("method") == "session/new" for c in calls) == 1
    assert sum(c.get("method") == "session/prompt" for c in calls) == 1
    print("PASS: failed resume persisted original ID; no new session or prompt")

    (root / "fail-resume").unlink()
    with closing(open_conversation()) as recovered:
        await run(recovered)
        assert recovered.state.agent_state["acp_session_id"] == SESSION
        assert recovered.state.execution_status == ConversationExecutionStatus.FINISHED
        actions = [e for e in recovered.state.events if isinstance(e, ActionEvent)]
        assert "original-context-marker" in actions[-1].action.model_dump_json()
    calls = [
        json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()
    ]
    assert sum(c.get("method") == "session/new" for c in calls) == 1
    assert sum(c.get("method") == "session/load" for c in calls) == 2
    assert sum(c.get("method") == "session/prompt" for c in calls) == 2
    print(
        f"PASS: {'async' if async_run else 'sync'} close/reopen/retry restored context"
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--server":
        serve(Path(sys.argv[2]))
    else:
        for async_run in (False, True):
            with tempfile.TemporaryDirectory(prefix="acp-resume-smoke-") as directory:
                asyncio.run(exercise(Path(directory), async_run))
