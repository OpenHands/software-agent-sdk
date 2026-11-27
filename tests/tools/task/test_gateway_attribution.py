"""Exercise nested task calls against a real HTTP capture gateway."""

import json
from collections import Counter
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import UUID

import pytest

from openhands.sdk import LLM, Agent, LocalConversation, Tool
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.subagent.registry import (
    _reset_registry_for_tests,
    agent_definition_to_factory,
    register_agent,
)
from openhands.sdk.subagent.schema import AgentDefinition
from openhands.tools.task import TaskToolSet


@pytest.fixture
def gateway():
    requests = []
    calls = Counter()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            model = body["model"]
            headers = {key.lower(): value for key, value in self.headers.items()}
            requests.append((self.path, model, headers))
            calls[model] += 1
            child = {"atlas": "birch", "birch": "cedar"}.get(model)
            message = {"role": "assistant", "content": "Done."}
            finish_reason = "stop"
            if child and calls[model] == 1:
                message["content"] = None
                message["tool_calls"] = [
                    {
                        "id": f"call_{model}",
                        "type": "function",
                        "function": {
                            "name": "task",
                            "arguments": json.dumps(
                                {"prompt": "Run the fixture.", "subagent_type": child}
                            ),
                        },
                    }
                ]
                finish_reason = "tool_calls"
            response = {
                "id": f"fixture-{model}-{calls[model]}",
                "object": "chat.completion",
                "created": 1,
                "model": model,
                "choices": [
                    {"index": 0, "finish_reason": finish_reason, "message": message}
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
            payload = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            thread.join()


@pytest.fixture
def clean_agent_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


@pytest.mark.parametrize("route_mode", ["disabled", "enabled", "different-base"])
def test_nested_profiles_gateway_headers(
    gateway, tmp_path, monkeypatch, clean_agent_registry, route_mode
):
    base, requests = gateway
    endpoint = f"{base}/v1"
    selected_tags = {"ticket": "DEMO-42", "team": "apps", "product": "portal"}
    enable_attribution = route_mode == "enabled"
    gateway_base = f"{base}/another/v1" if route_mode == "different-base" else endpoint
    monkeypatch.setenv(
        "OH_LLM_GATEWAY_ATTRIBUTION",
        json.dumps({gateway_base: [*selected_tags, "invalid"]})
        if route_mode != "disabled"
        else "{}",
    )
    store = LLMProfileStore(tmp_path / "profiles")
    for model in ("atlas", "birch", "cedar"):
        store.save(
            model,
            LLM(
                model=f"openai/{model}",
                base_url=endpoint,
                api_key=f"fixture-{model}",
                extra_headers={"X-Fixture-Profile": model},
                stream=False,
                native_tool_calling=True,
            ),
            include_secrets=True,
        )
    for model in ("birch", "cedar"):
        definition = AgentDefinition(
            name=model,
            model=model,
            tools=[TaskToolSet.name] if model == "birch" else [],
            profile_store_dir=str(tmp_path / "profiles"),
        )
        register_agent(
            name=model,
            factory_func=agent_definition_to_factory(definition),
            description=definition,
        )

    with closing(
        LocalConversation(
            agent=Agent(llm=store.load("atlas"), tools=[Tool(name=TaskToolSet.name)]),
            workspace=tmp_path,
            visualizer=None,
            max_iteration_per_run=8,
            tags={
                **selected_tags,
                "private": "excluded",
                "invalid": "bad\r\nX-Injected: yes",
            },
        )
    ) as conversation:
        conversation.send_message("Start the nested fixture.")
        conversation.run()
        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
        root_id = str(conversation.state.id)

    assert [model for _, model, _ in requests] == [
        "atlas",
        "birch",
        "cedar",
        "birch",
        "atlas",
    ]
    expected_attribution = {}
    if enable_attribution:
        expected_attribution = {
            "x-openhands-root-session-id": root_id,
            **{f"x-openhands-tag-{key}": value for key, value in selected_tags.items()},
        }

    sessions = {}
    for path, model, headers in requests:
        assert path == "/v1/chat/completions"
        assert headers["authorization"] == f"Bearer fixture-{model}"
        assert headers["x-fixture-profile"] == model
        session_id = headers["x-litellm-session-id"]
        expected_session = sessions.setdefault(model, session_id)
        assert UUID(session_id) == UUID(expected_session)
        attribution = {
            name: value
            for name, value in headers.items()
            if name.startswith(("x-openhands-root-session-id", "x-openhands-tag-"))
        }
        assert attribution == expected_attribution
        assert "x-injected" not in headers
    assert sessions["atlas"] == root_id
    assert len(set(sessions.values())) == 3


@pytest.mark.parametrize("enabled", [False, True])
def test_gateway_attribution_reserved_profile_headers(
    gateway, tmp_path, monkeypatch, enabled
):
    base, requests = gateway
    endpoint = f"{base}/v1"
    setting = {endpoint: ["ticket"]} if enabled else {}
    monkeypatch.setenv("OH_LLM_GATEWAY_ATTRIBUTION", json.dumps(setting))
    llm = LLM(
        model="openai/echo",
        api_key="fixture",
        base_url=endpoint,
        extra_headers={
            "X-OpenHands-Root-Session-ID": "manual-root",
            "X-OpenHands-Tag-Ticket": "MANUAL-8",
            "X-OpenHands-Tag-Private": "manual-only",
            "X-Fixture-Profile": "echo",
        },
    )
    with closing(
        LocalConversation(
            agent=Agent(llm=llm),
            workspace=tmp_path,
            visualizer=None,
            tags={"ticket": "DEMO-42"},
        )
    ) as conversation:
        conversation.send_message("Run the fixture.")
        conversation.run()
        root_id = str(conversation.state.id)

    [(_, _, headers)] = requests
    assert headers["x-fixture-profile"] == "echo"
    assert headers["authorization"] == "Bearer fixture"
    assert headers["x-litellm-session-id"] == root_id
    assert headers["x-openhands-root-session-id"] == (
        root_id if enabled else "manual-root"
    )
    assert headers["x-openhands-tag-ticket"] == ("DEMO-42" if enabled else "MANUAL-8")
    assert headers.get("x-openhands-tag-private") == (
        None if enabled else "manual-only"
    )
