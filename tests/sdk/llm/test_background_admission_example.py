import json
import runpy
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from openhands.sdk import Message, TextContent
from openhands.sdk.llm.exceptions import LLMServiceUnavailableError


EXAMPLE = (
    Path(__file__).resolve().parents[3]
    / "examples/01_standalone_sdk/60_background_admission/main.py"
)
admission_llm = runpy.run_path(str(EXAMPLE))["admission_llm"]


@pytest.fixture
def endpoint():
    requests = []
    reply_status = [200]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_json(200, {"object": "list", "data": []})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"path": self.path, "body": body})
            if reply_status[0] != 200:
                self.send_json(503, {"error": {"message": "fixture unavailable"}})
                return
            self.send_json(
                200,
                {
                    "id": "fixture-completion",
                    "object": "chat.completion",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "42"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 16,
                        "completion_tokens": 2,
                        "total_tokens": 18,
                    },
                },
            )

        def send_json(self, status, body):
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/proxy", requests, reply_status
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.fixture
def config():
    return {
        "model": "litellm_proxy/fixture-model",
        "usage_id": "admission-example",
        "api_mode": "chat",
        "max_input_tokens": 65536,
        "max_output_tokens": 10923,
        "reasoning_effort": "high",
        "temperature": 0.4,
        "top_p": 0.9,
        "timeout": 10,
        "drop_params": False,
        "capability_overrides": {
            "supports_reasoning_effort": True,
            "supports_sampling_params": True,
        },
        "litellm_extra_body": {
            "reasoning_budget_tokens": 8192,
            "experimental_speed_projection": False,
        },
    }


def messages():
    return [
        Message(role="system", content=[TextContent(text="Answer directly.")]),
        Message(role="user", content=[TextContent(text="What is six times seven?")]),
    ]


@pytest.mark.parametrize("background", [False, True])
def test_example_preserves_request_and_selects_host_route(endpoint, config, background):
    root, requests, _status = endpoint
    before = deepcopy(config)
    llm = admission_llm(config, root + "/", background=background, api_key="fixture")
    response = llm.completion(messages=messages())
    assert config == before
    assert response.message.content == [TextContent(text="42")]
    assert len(requests) == 1
    route = "/background/v1" if background else "/v1"
    assert requests[0]["path"] == "/proxy" + route + "/chat/completions"
    body = requests[0]["body"]
    assert body["model"] == "fixture-model"
    assert body["max_completion_tokens"] == 10923
    assert body["reasoning_effort"] == "high"
    assert body["reasoning_budget_tokens"] == 8192
    assert body["experimental_speed_projection"] is False
    assert body["temperature"] == 0.4
    assert body["top_p"] == 0.9
    assert [row["role"] for row in body["messages"]] == ["system", "user"]
    assert "adaptive_scheduling" not in body


def test_example_does_not_replay_failed_dispatch(endpoint, config):
    root, requests, status = endpoint
    status[0] = 503
    llm = admission_llm(config, root, background=True, api_key="fixture")
    with pytest.raises(LLMServiceUnavailableError):
        llm.completion(messages=messages())
    assert len(requests) == 1


def test_example_cli_executes_once(endpoint, config, tmp_path, monkeypatch, capsys):
    root, requests, _status = endpoint
    profile = tmp_path / "llm.json"
    prompt = tmp_path / "messages.json"
    profile.write_text(json.dumps(config), encoding="utf-8")
    prompt.write_text(
        json.dumps([message.model_dump(mode="json") for message in messages()]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ADAPTIVE_API_KEY", "fixture")
    monkeypatch.setattr(
        "sys.argv",
        [
            str(EXAMPLE),
            str(profile),
            str(prompt),
            "--gateway-root",
            root,
            "--background",
        ],
    )
    runpy.run_path(str(EXAMPLE), run_name="__main__")
    assert len(requests) == 1
    assert requests[0]["path"] == "/proxy/background/v1/chat/completions"
    output = capsys.readouterr().out
    assert '"text":"42"' in output
    assert "EXAMPLE_COST:" in output


@pytest.mark.parametrize(
    "root",
    [
        "file:///tmp/socket",
        "http://user:password@localhost",
        "http://x/?q=1",
        "http://x/?",
        "http://x/#",
        " http://x",
        "http://x/\tproxy",
        "http://x/\x7fproxy",
        "http://x\\proxy",
        "http://x:0",
        "http://x:65536",
        "http://x:bad",
        "http://x:",
        "http://x/./proxy",
        "http://x/../proxy",
        "http://x/%2fproxy",
        "http://x//proxy",
        "http://x//",
        "http://x/v1",
        "http://x/proxy/background/v1/",
    ],
)
def test_example_rejects_ambiguous_gateway_roots(config, root):
    with pytest.raises(ValueError, match="gateway root"):
        admission_llm(config, root, background=False, api_key="fixture")
