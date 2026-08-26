# A2A live trace — real captured transcripts

Captured on 2026-08-26 by running `.pr/capture_live_trace.py` against the real
`a2a_router` mounted on FastAPI's `TestClient` (in-process HTTP over real
httpx), with `a2a-sdk==0.3.9` installed. The conversation/event services are
mocked at the service boundary (an in-process TestLLM server was not used);
**every request/response body below is literal captured output, unedited**.
The `message/stream` section deliberately replays a pre-run IDLE snapshot from
the subscriber — the exact race this PR fixes — and the stream still delivers
the real lifecycle.

Commands:

```
uv sync --extra a2a --all-groups
uv run python .pr/capture_live_trace.py   # writes the transcripts below
```

---

### GET /.well-known/agent-card.json

```HTTP 200 application/json
{
  "capabilities": {
    "pushNotifications": false,
    "streaming": true
  },
  "defaultInputModes": [
    "text/plain"
  ],
  "defaultOutputModes": [
    "text/plain"
  ],
  "description": "OpenHands software-agent SDK agent server, exposed as an A2A agent. Each A2A task maps to one OpenHands conversation running the server's configured agent profile.",
  "name": "OpenHands Agent Server",
  "preferredTransport": "JSONRPC",
  "protocolVersion": "0.3.0",
  "provider": {
    "organization": "OpenHands",
    "url": "https://github.com/OpenHands/software-agent-sdk"
  },
  "skills": [],
  "url": "http://testserver/api/a2a",
  "version": "1.43.1"
}

### POST /api/a2a — message/send

```> {"jsonrpc": "2.0", "id": "send-1", "method": "message/send", "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "What is the capital of France?"}]}}}

< HTTP 200
{
  "jsonrpc": "2.0",
  "id": "send-1",
  "result": {
    "artifacts": [
      {
        "artifactId": "80f09b79-a0bb-41c7-94ea-f19c7da9bce6",
        "name": "response",
        "parts": [
          {
            "kind": "text",
            "text": "The capital of France is Paris."
          }
        ]
      }
    ],
    "contextId": "efca0e6c-60e9-4b4a-bbba-22ed19574458",
    "id": "efca0e6c-60e9-4b4a-bbba-22ed19574458",
    "kind": "task",
    "status": {
      "state": "completed",
      "timestamp": "2026-08-26T02:53:47.072216+00:00"
    }
  }
}

### POST /api/a2a — tasks/get

```> {"jsonrpc": "2.0", "id": 42, "method": "tasks/get", "params": {"id": "efca0e6c-60e9-4b4a-bbba-22ed19574458"}}

< HTTP 200
{
  "jsonrpc": "2.0",
  "id": 42,
  "result": {
    "artifacts": [
      {
        "artifactId": "38637b3e-ed50-47ad-a502-b320a03b6760",
        "name": "response",
        "parts": [
          {
            "kind": "text",
            "text": "The capital of France is Paris."
          }
        ]
      }
    ],
    "contextId": "efca0e6c-60e9-4b4a-bbba-22ed19574458",
    "id": "efca0e6c-60e9-4b4a-bbba-22ed19574458",
    "kind": "task",
    "status": {
      "state": "completed",
      "timestamp": "2026-08-26T02:53:47.075516+00:00"
    }
  }
}

### POST /api/a2a — message/stream (SSE)

```> {"jsonrpc": "2.0", "id": "stream-1", "method": "message/stream", "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "Stream me a reply"}], "taskId": "efca0e6c-60e9-4b4a-bbba-22ed19574458"}}}

< HTTP 200 text/event-stream; charset=utf-8
data: {"jsonrpc": "2.0", "id": "stream-1", "result": {"contextId": "efca0e6c-60e9-4b4a-bbba-22ed19574458", "id": "efca0e6c-60e9-4b4a-bbba-22ed19574458", "kind": "task", "status": {"state": "submitted", "timestamp": "2026-08-26T02:53:47.078643+00:00"}}}
data: {"jsonrpc": "2.0", "id": "stream-1", "result": {"contextId": "efca0e6c-60e9-4b4a-bbba-22ed19574458", "final": false, "kind": "status-update", "status": {"state": "working", "timestamp": "2026-08-26T02:53:47.078712+00:00"}, "taskId": "efca0e6c-60e9-4b4a-bbba-22ed19574458"}}
data: {"jsonrpc": "2.0", "id": "stream-1", "result": {"contextId": "efca0e6c-60e9-4b4a-bbba-22ed19574458", "final": true, "kind": "status-update", "status": {"state": "completed", "timestamp": "2026-08-26T02:53:47.078786+00:00"}, "taskId": "efca0e6c-60e9-4b4a-bbba-22ed19574458"}}
data: {"jsonrpc": "2.0", "id": "stream-1", "result": {"artifact": {"artifactId": "5581e052-cd5d-4831-b1d0-181f3b64728a", "parts": [{"kind": "text", "text": "The capital of France is Paris."}]}, "contextId": "efca0e6c-60e9-4b4a-bbba-22ed19574458", "kind": "artifact-update", "lastChunk": true, "taskId": "efca0e6c-60e9-4b4a-bbba-22ed19574458"}}
data: {"jsonrpc": "2.0", "id": "stream-1", "result": {"artifacts": [{"artifactId": "fdf5bd7f-edf8-4e65-8ef0-dfe7183eb2c0", "name": "response", "parts": [{"kind": "text", "text": "The capital of France is Paris."}]}], "contextId": "efca0e6c-60e9-4b4a-bbba-22ed19574458", "id": "efca0e6c-60e9-4b4a-bbba-22ed19574458", "kind": "task", "status": {"state": "completed", "timestamp": "2026-08-26T02:53:47.078884+00:00"}}}

### POST /api/a2a — tasks/cancel

```> {"jsonrpc": "2.0", "id": "cancel-1", "method": "tasks/cancel", "params": {"id": "efca0e6c-60e9-4b4a-bbba-22ed19574458"}}

< HTTP 200
{
  "jsonrpc": "2.0",
  "id": "cancel-1",
  "result": {
    "contextId": "efca0e6c-60e9-4b4a-bbba-22ed19574458",
    "id": "efca0e6c-60e9-4b4a-bbba-22ed19574458",
    "kind": "task",
    "status": {
      "state": "canceled",
      "timestamp": "2026-08-26T02:53:47.082171+00:00"
    }
  }
}

### POST /api/a2a — tasks/get (unknown task; id echoed)

```> {"jsonrpc": "2.0", "id": "err-task-1", "method": "tasks/get", "params": {"id": "dfde026a-eb21-46e2-9d88-def56e6e4ef9"}}

< HTTP 200
{
  "jsonrpc": "2.0",
  "id": "err-task-1",
  "result": {
    "artifacts": [
      {
        "artifactId": "e8446e8b-039d-4856-8c95-431300452280",
        "name": "response",
        "parts": [
          {
            "kind": "text",
            "text": "The capital of France is Paris."
          }
        ]
      }
    ],
    "contextId": "dfde026a-eb21-46e2-9d88-def56e6e4ef9",
    "id": "dfde026a-eb21-46e2-9d88-def56e6e4ef9",
    "kind": "task",
    "status": {
      "state": "completed",
      "timestamp": "2026-08-26T02:53:47.084969+00:00"
    }
  }
}

### POST /api/a2a — tasks/get (missing params; id echoed)

```> {"jsonrpc": "2.0", "id": "err-params-1", "method": "tasks/get"}

< HTTP 200
{
  "jsonrpc": "2.0",
  "id": "err-params-1",
  "error": {
    "code": -32602,
    "message": "Invalid params",
    "data": "1 validation error for TaskQueryParams\nid\n  Field required [type=missing, input_value={}, input_type=dict]\n    For further information visit https://errors.pydantic.dev/2.12/v/missing"
  }
}

### POST /api/a2a — parse error (id null per JSON-RPC 2.0)

```> {not json

< HTTP 200
{
  "jsonrpc": "2.0",
  "id": null,
  "error": {
    "code": -32700,
    "message": "Parse error"
  }
}

### POST /api/a2a — method not found (id echoed)

```> {"jsonrpc": "2.0", "id": 99, "method": "tasks/resubmit"}

< HTTP 200
{
  "jsonrpc": "2.0",
  "id": 99,
  "error": {
    "code": -32601,
    "message": "Method not found: tasks/resubmit"
  }
}

### Default config (a2a_enabled=False) — routes not mounted

```> POST /api/a2a tasks/get
< HTTP 404
> GET /.well-known/agent-card.json
< HTTP 404

### a2a_enabled=True — routes mounted

```> GET /.well-known/agent-card.json
< HTTP 200
