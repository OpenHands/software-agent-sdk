"""Public client contracts shared by local and Docker orchestration consumers."""

import inspect
import json

import httpx
import pytest

from openhands.sdk.client import AgentServerClient, AsyncAgentServerClient


CID = "e793aaea-50c7-4fd7-a686-2c76a6c2a80e"
PROFILE = "42d0d2ef-f506-4774-b008-e330b4b83d09"


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "plugins",
    [None, [{"source": "openhands/extensions", "repo_path": "plugins/qa-changes"}]],
)
async def test_same_scoped_wire_contract_for_sync_and_async(asynchronous, plugins):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers["X-Session-API-Key"] == "selected-key"
        if request.url.path.endswith("/credentials"):
            return httpx.Response(200, json={"session_api_key": "inner-key"})
        return httpx.Response(200, json={"id": CID, "items": []})

    transport = httpx.MockTransport(respond)
    if asynchronous:
        http = httpx.AsyncClient(transport=transport)
        client = AsyncAgentServerClient(
            "https://server", "selected-key", http_client=http
        )
    else:
        http = httpx.Client(transport=transport)
        client = AgentServerClient("https://server", "selected-key", http_client=http)

    async def call(value):
        return await value if inspect.isawaitable(value) else value

    await call(client.get_server_info())
    await call(
        client.create_conversation(
            conversation_id=CID,
            agent_profile_id=PROFILE,
            working_dir="/runs/job",
            title="Portable workflow",
            tags={"automationrun": CID},
            plugins=plugins,
        )
    )
    await call(client.get_conversation(CID))
    await call(client.send_message(CID, "Continue"))
    await call(client.get_errors(CID))
    await call(client.interrupt(CID))
    runtime = client.runtime_for_api_prefix(f"/api/conversations/{CID}")
    await call(runtime.upload("/runs/job/bundle.tar.gz", b"bundle"))
    await call(runtime.execute("pwd", timeout=20, cwd="/runs/job"))
    await call(runtime.start("python3 main.py", timeout=300))
    await call(runtime.get_output("command-42"))
    assert await call(runtime.get_session_key()) == "inner-key"
    await call(runtime.release())
    root = f"/api/conversations/{CID}"
    assert [r.url.path for r in requests] == [
        "/server_info",
        "/api/conversations",
        root,
        root + "/events",
        root + "/events/search",
        root + "/interrupt",
        root + "/file/upload",
        root + "/bash/execute_bash_command",
        root + "/bash/start_bash_command",
        root + "/bash/bash_events/search",
        root + "/runtime/credentials",
        root + "/runtime",
    ]
    creation = json.loads(requests[1].content)
    if plugins is None:
        assert "plugins" not in creation
    else:
        assert creation["plugins"] == plugins
    assert creation["workspace"] == {
        "kind": "LocalWorkspace",
        "working_dir": "/runs/job",
    }
    assert json.loads(requests[3].content) == {
        "content": [{"type": "text", "text": "Continue"}],
        "run": True,
    }
    assert requests[6].url.params["path"] == "/runs/job/bundle.tar.gz"
    assert requests[9].url.params["command_id__eq"] == "command-42"
    assert requests[-1].method == "DELETE"
    await call(
        client.aclose()
        if isinstance(client, AsyncAgentServerClient)
        else client.close()
    )
    assert not http.is_closed  # Caller owns an injected transport.
    await call(http.aclose() if isinstance(http, httpx.AsyncClient) else http.close())


@pytest.mark.parametrize(
    "value", ["../other", CID + "/file", CID + "?cid=other", "", "https://other"]
)
def test_invalid_scope_is_rejected_before_network(value):
    client = AgentServerClient("https://server", "key")
    try:
        with pytest.raises(ValueError):
            client.get_conversation(value)
        with pytest.raises(ValueError):
            client.runtime(value)
        with pytest.raises(ValueError):
            client.runtime_for_api_prefix("/api/conversations/" + value)
        with pytest.raises(ValueError):
            AsyncAgentServerClient("https://server", "key").runtime_for_api_prefix(
                "/api/conversations/" + value
            )
    finally:
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,raises", [(404, False), (502, True)])
async def test_release_is_idempotent_but_does_not_hide_stop_failure(status, raises):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status))
    ) as http:
        client = AsyncAgentServerClient("https://server", "key", http_client=http)
        if raises:
            with pytest.raises(httpx.HTTPStatusError):
                await client.runtime(CID).release()
        else:
            await client.runtime(CID).release()


def test_legacy_runtime_is_explicit_and_cannot_release_global_server():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        runtime = AgentServerClient("https://server", "key", http_client=http).runtime()
        runtime.execute("pwd")
        assert requests[0].url.path == "/api/bash/execute_bash_command"
        with pytest.raises(ValueError):
            runtime.release()


@pytest.mark.parametrize(
    "result", [{}, {"session_api_key": ""}, {"session_api_key": 42}]
)
def test_missing_runtime_credential_fails_closed(result):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=result))
    ) as http:
        runtime = AgentServerClient("https://server", "key", http_client=http).runtime(
            CID
        )
        with pytest.raises(ValueError):
            runtime.get_session_key()
