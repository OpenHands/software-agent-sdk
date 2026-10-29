"""Exercise real Conversation.run/arun against a deterministic HTTP model."""

import argparse
import asyncio
import json
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.conversation import stuck_detector
from openhands.sdk.event import ActionEvent, AgentErrorEvent, MessageEvent
from openhands.sdk.llm import TextContent
from openhands.tools.task_tracker import TaskTrackerTool


CASES = (
    "identical", "vary_thought", "vary_invalid_args", "repair_different_fields",
    "repair_control", "recover_after_two",
)


def run_case(mode, asynchronous):
    calls = []
    events = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["messages"][0]["role"] == "system"
            i = len(calls)
            calls.append(body)
            thought = f"Attempt {i + 1}" if mode == "vary_thought" else "Updating the plan."
            tasks = [{"title": "Example", "status": "blocked"}]
            if mode == "vary_invalid_args":
                tasks[0]["title"] = f"Example {i}"
            if mode in ("repair_different_fields", "repair_control"):
                tasks = [
                    {"title": f"Task {j}", "status": "todo"} if j < i
                    else {"status": "todo"} for j in range(6)
                ]
            if mode == "recover_after_two" and i >= 2:
                tasks[0]["status"] = "done"
            done = (mode == "recover_after_two" and i == 3) or (
                mode in ("repair_different_fields", "repair_control") and i == 7
            )
            message = {"role": "assistant", "content": "Done" if done else thought}
            if not done:
                message["tool_calls"] = [{
                    "id": f"call{i}", "type": "function", "function": {
                        "name": "task_tracker",
                        "arguments": json.dumps({"command": "plan", "task_list": tasks}),
                    },
                }]
            reply = {
                "id": f"chat{i}", "object": "chat.completion", "created": 1,
                "model": "gpt-4o", "choices": [{"index": 0, "message": message,
                    "finish_reason": "stop" if done else "tool_calls"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            raw = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as work:
            llm = LLM(
                model="openai/gpt-4o", base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key=SecretStr("local-test"), usage_id=mode, num_retries=1,
            )
            agent = Agent(llm=llm, tools=[Tool(name=TaskTrackerTool.name)])
            conv = Conversation(
                agent=agent, workspace=work, max_iteration_per_run=12,
                stuck_detection=mode != "repair_control", callbacks=[events.append],
                visualizer=None,
            )
            try:
                conv.send_message("Maintain the task plan.")
                if asynchronous:
                    asyncio.run(conv.arun())
                else:
                    conv.run()
                errors = [e for e in events if isinstance(e, AgentErrorEvent)]
                return {
                    "case": mode, "llm_calls": len(calls),
                    "status": conv.state.execution_status.value,
                    "errors": len(errors),
                    "actions_none": sum(isinstance(e, ActionEvent) and e.action is None for e in events),
                    "nudge_count": sum(
                        isinstance(e, MessageEvent) and e.source == "environment"
                        and any(isinstance(c, TextContent) and (
                            "same arguments" in c.text or "failed validation" in c.text
                        ) for c in e.llm_message.content) for e in events
                    ),
                    "validation_error_counts": [
                        int(e.error.split(": ", 1)[1].split()[0]) for e in errors
                        if "validation error" in e.error
                    ],
                }
            finally:
                conv.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--asynchronous", action="store_true")
    parser.add_argument("--expect", choices=("base", "candidate"), required=True)
    args = parser.parse_args()
    source = Path(stuck_detector.__file__).resolve()
    root = next(parent for parent in source.parents if (parent / ".git").exists())
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    results = []
    for case in CASES:
        row = run_case(case, args.asynchronous)
        results.append(row)
        print(json.dumps(row), flush=True)
        expected = ("stuck", 4, 1)
        if case == "recover_after_two":
            expected = ("finished", 4, 0)
        elif case == "repair_control" or (case == "repair_different_fields" and args.expect == "candidate"):
            expected = ("finished", 8, 0)
        elif case == "vary_thought" and args.expect == "base":
            expected = ("error", 12, 0)
        assert (row["status"], row["llm_calls"], row["nudge_count"]) == expected, row
    Path(args.out).write_text(json.dumps({
        "source": str(source), "revision": revision, "expect": args.expect,
        "asynchronous": args.asynchronous, "results": results,
    }, indent=2) + "\n")
    print("PASS: every conversation and owned HTTP server closed.", flush=True)


if __name__ == "__main__":
    main()
