"""A2A example: talk to the agent-server as an A2A agent.

The agent-server exposes an A2A (Agent2Agent) JSON-RPC 2.0 endpoint at
``/api/a2a`` and a discovery document at ``/.well-known/agent-card.json``.
This example uses plain httpx (no a2a-sdk dependency):

1. Fetch the agent card from the well-known URI.
2. Send a user message with the ``message/send`` JSON-RPC method and print
   the resulting Task (status + artifact).

Usage:
    python 17_a2a_agent_card.py [base_url] [session_api_key]

    # start a server first, e.g.:
    #   uv run python -m openhands.agent_server.base --port 9000 \
    #       --session-api-key my-secret
    python 17_a2a_agent_card.py http://localhost:9000 my-secret

Requires an agent profile to be configured (the default profile is used
automatically).
"""

import json
import sys

import httpx


DEFAULT_BASE_URL = "http://localhost:9000"


def main() -> None:
    base_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE_URL
    api_key = sys.argv[2] if len(sys.argv) > 2 else None

    headers = {"Content-Type": "application/json"}
    if api_key:
        # A2A convention is Authorization: Bearer; the server also accepts
        # the native X-Session-API-Key header.
        headers["Authorization"] = f"Bearer {api_key}"

    with httpx.Client(timeout=120) as client:
        # 1. Agent card discovery (no auth required).
        card = client.get(f"{base_url}/.well-known/agent-card.json").json()
        print("=== Agent Card ===")
        print(json.dumps(card, indent=2))

        # 2. message/send over JSON-RPC 2.0.
        payload = {
            "jsonrpc": "2.0",
            "id": "a2a-example-1",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "Say hello from A2A!"}],
                }
            },
        }
        print("\n=== message/send ===")
        response = client.post(f"{base_url}/api/a2a", headers=headers, json=payload)
        print(json.dumps(response.json(), indent=2))

        # 3. Poll the task afterwards (taskId == conversationId).
        task_id = response.json().get("result", {}).get("id")
        if task_id:
            print("\n=== tasks/get ===")
            poll = client.post(
                f"{base_url}/api/a2a",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": "a2a-example-2",
                    "method": "tasks/get",
                    "params": {"id": task_id},
                },
            )
            print(json.dumps(poll.json(), indent=2))


if __name__ == "__main__":
    main()
