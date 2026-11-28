"""Exercise real Conversation.run/arun with a failed Jinja cache and local HTTP LLM.

Run from the repository root with .venv/Scripts/python.exe .pr/reproduce_7103.py.
Pass --baseline to import a temporary copy of the SDK with the two changed source
files restored from the original base revision. The working tree is never rewritten.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


repo = Path(__file__).resolve().parents[1]
BASE_REVISION = "15bbd077d8a013fa0cf7554522b6c88e5171060d"
with tempfile.TemporaryDirectory(prefix="openhands-7103-") as temporary:
    root = Path(temporary)
    if "--baseline" in sys.argv:
        source = root / "source"
        shutil.copytree(repo / "openhands-sdk" / "openhands", source / "openhands")
        for relative in (
            "openhands/sdk/context/prompts/prompt.py",
            "openhands/sdk/context/condenser/llm_summarizing_condenser.py",
        ):
            original = subprocess.check_output(
                ["git", "show", f"{BASE_REVISION}:openhands-sdk/{relative}"], cwd=repo
            )
            (source / relative).write_bytes(original)
        sys.path.insert(0, str(source))

    os.environ["OH_PUBLIC_SKILLS_ENABLED"] = "0"
    os.environ["OH_PUBLIC_CATALOG_ENABLED"] = "0"
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["OPENHANDS_SUPPRESS_BANNER"] = "1"
    os.environ["OH_PERSISTENCE_DIR"] = str(root / "state")

    if "--baseline-tests" in sys.argv:
        import pytest

        raise SystemExit(
            pytest.main(
                [
                    "-q",
                    "--tb=short",
                    "tests/sdk/context/prompts/test_prompt_snapshot.py",
                    "tests/sdk/context/test_memory.py::test_load_memory_user_header_reflects_persistence_dir",
                ]
            )
        )

    from pydantic import SecretStr

    from openhands.sdk import Agent, Conversation, LLM, LLMSummarizingCondenser
    from openhands.sdk.context.condenser import llm_summarizing_condenser
    from openhands.sdk.context.prompts import prompt
    from openhands.sdk.event.condenser import Condensation

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            message = {"role": "assistant", "content": "Local summary"}
            finish_reason = "stop"
            if body.get("tools"):
                message["tool_calls"] = [
                    {
                        "id": "finish-call",
                        "type": "function",
                        "function": {
                            "name": "finish",
                            "arguments": '{"message":"done"}',
                        },
                    }
                ]
                finish_reason = "tool_calls"
            response = json.dumps(
                {
                    "id": "local-response",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {"index": 0, "message": message, "finish_reason": finish_reason}
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        for use_async in (False, True):
            requests.clear()
            prompt._get_env.cache_clear()
            prompt._get_template.cache_clear()
            state = root / ("async-state" if use_async else "sync-state")
            os.environ["OH_PERSISTENCE_DIR"] = str(state)
            llm = LLM(
                model="openai/gpt-4o-mini",
                api_key=SecretStr("local-test"),
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                usage_id="agent",
                stream=False,
                num_retries=0,
            )
            agent = Agent(
                llm=llm,
                tools=[],
                condenser=LLMSummarizingCondenser(
                    llm=llm.model_copy(update={"usage_id": "summary"}),
                    max_size=10,
                    keep_first=2,
                ),
            )
            with closing(
                Conversation(
                    agent=agent,
                    workspace=str(root),
                    visualizer=None,
                    stuck_detection=False,
                    max_iteration_per_run=3,
                )
            ) as conv:
                for i in range(12):
                    conv.send_message(f"Remember fact {i} for the summary.")
                # Create the real environment, then make its cache path unusable.
                prompt_dir = str(
                    Path(llm_summarizing_condenser.__file__).parent / "prompts"
                )
                prompt._get_env(prompt_dir)
                cache_dir = state / "cache" / "jinja"
                for cached in cache_dir.iterdir():
                    cached.unlink()
                cache_dir.rmdir()
                cache_dir.write_text("blocked cache", encoding="utf-8")
                try:
                    asyncio.run(conv.arun()) if use_async else conv.run()
                    result = "finished"
                except Exception as error:
                    result = f"{type(error).__name__}: {error}"
                condensed = any(
                    isinstance(event, Condensation) for event in conv.state.events
                )
                print(
                    json.dumps(
                        {
                            "baseline": "--baseline" in sys.argv,
                            "async": use_async,
                            "result": result,
                            "status": conv.state.execution_status.value,
                            "condensed": condensed,
                            "local_model_requests": len(requests),
                        }
                    ),
                    flush=True,
                )
                if "--baseline" in sys.argv:
                    assert conv.state.execution_status.value == "error", result
                    assert "not found" in result
                    assert not requests
                else:
                    assert conv.state.execution_status.value == "finished", result
                    assert condensed
                    assert len(requests) == 2
    finally:
        server.shutdown()
        worker.join()
        server.server_close()
