"""Exercise ACP telemetry through a real subprocess, without an LLM service."""

import argparse
import json
import sys
import tempfile
from contextlib import closing

from openhands.sdk import Conversation
from openhands.sdk.agent.acp_agent import ACPAgent


SERVER = r"""
import json
import sys

def send(message):
    print(json.dumps({"jsonrpc": "2.0", **message}), flush=True)

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        result = {
            "protocolVersion": 1,
            "agentCapabilities": {},
            "agentInfo": {"name": "offline-context-repro", "version": "1"},
            "authMethods": [],
        }
    elif method == "session/new":
        result = {"sessionId": "offline-context-session"}
    elif method == "session/prompt":
        session_id = request["params"]["sessionId"]
        for update in [
            {"sessionUpdate": "agent_message_chunk",
             "content": {"type": "text", "text": "Offline turn complete."}},
            {"sessionUpdate": "usage_update", "used": 46000, "size": 1000000,
             "cost": {"amount": 0.25, "currency": "USD"}},
        ]:
            send({"method": "session/update",
                  "params": {"sessionId": session_id, "update": update}})
        result = {
            "stopReason": "end_turn",
            "usage": {"inputTokens": 1300000, "outputTokens": 1000,
                      "totalTokens": 1301000},
        }
    else:
        if "id" in request:
            send({"id": request["id"],
                  "error": {"code": -32601, "message": "Unsupported method"}})
        continue
    send({"id": request["id"], "result": result})
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-context", type=int)
    args = parser.parse_args()
    agent = ACPAgent(acp_command=[sys.executable, "-u", "-c", SERVER])
    with tempfile.TemporaryDirectory() as workspace:
        with closing(
            Conversation(agent=agent, workspace=workspace, visualizer=None)
        ) as conv:
            conv.send_message("Run an offline turn with summed usage and context fill.")
            conv.run()
            metrics = agent.llm.metrics
            assert len(metrics.token_usages) == 1
            latest = metrics.token_usages[-1]
            accumulated = metrics.accumulated_token_usage
            assert accumulated is not None
            assert latest.prompt_tokens == 1300000
            assert latest.completion_tokens == 1000
            assert latest.context_window == 1000000
            assert metrics.accumulated_cost == 0.25
            print(
                json.dumps(
                    {
                        "latest_context": latest.per_turn_token,
                        "accumulated_context": accumulated.per_turn_token,
                        "context_window": latest.context_window,
                        "prompt_tokens": latest.prompt_tokens,
                        "completion_tokens": latest.completion_tokens,
                        "cost": metrics.accumulated_cost,
                    }
                )
            )
            if args.expected_context is not None:
                assert latest.per_turn_token == args.expected_context
                assert accumulated.per_turn_token == args.expected_context


if __name__ == "__main__":
    main()
