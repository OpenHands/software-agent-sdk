"""Install-time MCP OAuth through the agent-server's own redirect route.

A real FastMCP server protected by FastMCP's in-memory OAuth provider performs
dynamic client registration, validates the redirect URI and exchanges the
code. The test plays the browser: it follows the authorization URL and
delivers the provider's redirect to wherever the provider sent it.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.mcp_router import (
    MCPTestRequest,
    _BrowserCoordinatedOAuth,
    _MCPOAuthProbeJob,
    _oauth_probe_jobs,
    _register_oauth_job,
)
from tests.agent_server.test_mcp_oauth_store import (  # noqa: F401 - fixtures
    oauth_provider,
    protected_oauth_mcp_server,
)


PUBLIC_REDIRECT = "https://canvas.example.test/api/mcp/oauth/redirect"


def _client(**config: object) -> TestClient:
    return TestClient(
        create_app(
            Config(
                session_api_keys=[],
                secret_key=SecretStr("mcp-oauth-redirect-test-key"),
                **config,  # type: ignore[arg-type]
            )
        ),
        raise_server_exceptions=False,
    )


def _oauth_server(url: str, state: dict | None = None) -> dict:
    auth: dict = {
        "strategy": "oauth2",
        "authentication": {
            "type": "oauth",
            "client_auth_method": "none",
            "scopes": ["mail.read"],
        },
    }
    if state is not None:
        auth["state"] = state
    return {"transport": "http", "url": url, "auth": auth}


def _start(client: TestClient, body: dict) -> tuple[str, str]:
    response = client.post("/api/mcp/oauth/start", json={"timeout": 10.0, **body})
    assert response.status_code == 200, response.text
    started = response.json()
    assert started["ok"] is True, started
    job_id = started["job_id"]
    for _ in range(100):
        if client.get(f"/api/mcp/oauth/status/{job_id}").json()["callback_ready"]:
            break
        time.sleep(0.05)
    return job_id, started["authorization_url"]


def _redirect_uri(authorization_url: str) -> str:
    return parse_qs(urlparse(authorization_url).query)["redirect_uri"][0]


def _sign_in(authorization_url: str) -> str:
    """Return where the provider redirects the browser after consent."""
    response = httpx.get(authorization_url, follow_redirects=False, timeout=10.0)
    assert response.status_code in {302, 303, 307, 308}, response.text
    return response.headers["location"]


def _wait_for_result(client: TestClient, job_id: str) -> dict:
    status: dict = {}
    for _ in range(200):
        status = client.get(f"/api/mcp/oauth/status/{job_id}").json()
        if status["status"] in {"succeeded", "failed"}:
            return status
        time.sleep(0.05)
    raise AssertionError(f"OAuth job {job_id} did not finish: {status}")


@pytest.fixture
def jobs() -> Generator[None]:
    yield
    _oauth_probe_jobs.clear()


def test_browser_redirect_goes_to_callback_base_url_and_completes(
    protected_oauth_mcp_server: str,  # noqa: F811
    jobs: None,
):
    client = _client()
    job_id, authorization_url = _start(
        client,
        {
            "server": _oauth_server(protected_oauth_mcp_server),
            "callback_base_url": "https://canvas.example.test/",
        },
    )

    assert _redirect_uri(authorization_url) == PUBLIC_REDIRECT
    location = _sign_in(authorization_url)
    assert location.startswith(f"{PUBLIC_REDIRECT}?")
    assert client.get(f"/api/mcp/oauth/status/{job_id}").json()["status"] == (
        "authorizing"
    )

    redirect = urlparse(location)
    landing = client.get(f"{redirect.path}?{redirect.query}")

    assert landing.status_code == 200
    assert "Authorization complete" in landing.text
    status = _wait_for_result(client, job_id)
    assert status["status"] == "succeeded", status
    assert status["tools"] == ["read_subject"]
    assert status["oauth_state"]["tokens"]["access_token"].startswith("gAAAA")


def test_web_url_with_path_prefix_wins_over_callback_base_url(
    protected_oauth_mcp_server: str,  # noqa: F811
    jobs: None,
):
    client = _client(web_url="https://agent.example.com/openhands/")
    job_id, authorization_url = _start(
        client,
        {
            "server": _oauth_server(protected_oauth_mcp_server),
            "callback_base_url": "https://canvas.example.test",
        },
    )

    assert _redirect_uri(authorization_url) == (
        "https://agent.example.com/openhands/api/mcp/oauth/redirect"
    )
    redirect = urlparse(_sign_in(authorization_url))
    # The reverse proxy strips the path prefix before the agent-server.
    assert redirect.path == "/openhands/api/mcp/oauth/redirect"
    landing = client.get(f"/api/mcp/oauth/redirect?{redirect.query}")

    assert landing.status_code == 200
    assert _wait_for_result(client, job_id)["status"] == "succeeded"


def test_without_a_public_url_the_loopback_callback_is_unchanged(
    protected_oauth_mcp_server: str,  # noqa: F811
    jobs: None,
):
    client = _client()
    job_id, authorization_url = _start(
        client, {"server": _oauth_server(protected_oauth_mcp_server)}
    )

    redirect_uri = urlparse(_redirect_uri(authorization_url))
    assert (redirect_uri.scheme, redirect_uri.hostname, redirect_uri.path) == (
        "http",
        "localhost",
        "/callback",
    )
    assert client.get(f"/api/mcp/oauth/status/{job_id}").json()["status"] == (
        "authorizing"
    )
    # The loopback callback server inside the probe receives the redirect.
    httpx.get(_sign_in(authorization_url), timeout=10.0)

    assert _wait_for_result(client, job_id)["status"] == "succeeded"


def test_a_client_registered_for_the_loopback_callback_is_registered_again(
    protected_oauth_mcp_server: str,  # noqa: F811
    jobs: None,
):
    client = _client()
    job_id, authorization_url = _start(
        client, {"server": _oauth_server(protected_oauth_mcp_server)}
    )
    httpx.get(_sign_in(authorization_url), timeout=10.0)
    first = _wait_for_result(client, job_id)
    stored_client = first["oauth_state"]["client_info"]
    assert stored_client["redirect_uris"][0].startswith("http://localhost:")

    job_id, authorization_url = _start(
        client,
        {
            "server": _oauth_server(
                protected_oauth_mcp_server, {"client_info": stored_client}
            ),
            "callback_base_url": "https://canvas.example.test",
        },
    )
    redirect = urlparse(_sign_in(authorization_url))
    client.get(f"{redirect.path}?{redirect.query}")
    second = _wait_for_result(client, job_id)

    assert second["status"] == "succeeded", second
    assert (
        second["oauth_state"]["client_info"]["client_id"]
        != (stored_client["client_id"])
    )
    assert second["oauth_state"]["client_info"]["redirect_uris"] == [PUBLIC_REDIRECT]


def _job() -> _MCPOAuthProbeJob:
    return _MCPOAuthProbeJob(
        request=MCPTestRequest.model_validate(
            {"server": _oauth_server("https://mcp.example.com/mcp")}
        ),
        cipher=None,
        public_redirect_url=PUBLIC_REDIRECT,
    )


def _waiting_job(state: str) -> _MCPOAuthProbeJob:
    job = _job()
    job.oauth_state = state
    job.set_callback_ready(PUBLIC_REDIRECT)
    _register_oauth_job(job)
    return job


def test_redirect_with_an_unknown_state_completes_no_job(jobs: None):
    job = _waiting_job("expected-state")

    response = _client().get("/api/mcp/oauth/redirect?code=abc&state=other-state")

    assert response.status_code == 400
    assert "unknown or has expired" in response.text
    assert not job.redirect_received.is_set()


def test_redirect_before_the_job_waits_for_it_is_rejected(jobs: None):
    job = _job()
    job.oauth_state = "early-state"
    _register_oauth_job(job)

    response = _client().get("/api/mcp/oauth/redirect?code=abc&state=early-state")

    assert response.status_code == 400
    assert not job.redirect_received.is_set()


def test_redirect_for_an_expired_job_is_rejected(
    jobs: None, monkeypatch: pytest.MonkeyPatch
):
    job = _waiting_job("late-state")
    monkeypatch.setattr(job, "created_at", time.monotonic() - 16 * 60)

    response = _client().get("/api/mcp/oauth/redirect?code=abc&state=late-state")

    assert response.status_code == 400
    assert job.id not in _oauth_probe_jobs


def test_provider_error_fails_the_job_and_a_replay_is_rejected(jobs: None):
    job = _waiting_job("the-state")
    client = _client()

    denied = client.get(
        "/api/mcp/oauth/redirect?state=the-state&error=access_denied"
        "&error_description=The+user+denied+access"
    )
    replay = client.get("/api/mcp/oauth/redirect?state=the-state&code=abc")

    assert denied.status_code == 400
    assert "The user denied access" in denied.text
    assert job.redirect_error == "The user denied access"
    assert job.redirect_result is None
    assert replay.status_code == 409


def test_redirect_route_does_not_need_the_session_api_key(jobs: None):
    job = _waiting_job("keyless-state")
    client = TestClient(
        create_app(Config(session_api_keys=["a-session-key"])),
        raise_server_exceptions=False,
    )

    assert client.get("/api/mcp/oauth/status/x").status_code == 401
    response = client.get("/api/mcp/oauth/redirect?code=abc&state=keyless-state")

    assert response.status_code == 200
    assert job.redirect_result is not None
    assert job.redirect_result.code == "abc"


@pytest.mark.parametrize(
    ("client_id", "static_redirect", "expected"),
    [
        (None, None, PUBLIC_REDIRECT),
        ("static-client", None, "http://localhost:"),
        (
            "static-client",
            "https://agent.example.com/api/mcp/oauth/redirect",
            "https://agent.example.com/api/mcp/oauth/redirect",
        ),
    ],
    ids=["dynamic-client", "static-client-keeps-loopback", "static-client-web-url"],
)
def test_pre_registered_clients_only_follow_a_configured_web_url(
    client_id: str | None, static_redirect: str | None, expected: str
):
    job = _MCPOAuthProbeJob(
        request=MCPTestRequest.model_validate(
            {"server": _oauth_server("https://mcp.example.com/mcp")}
        ),
        cipher=None,
        public_redirect_url=static_redirect or PUBLIC_REDIRECT,
        static_client_redirect_url=static_redirect,
    )
    oauth = _BrowserCoordinatedOAuth(
        job=job, mcp_url="https://mcp.example.com/mcp", client_id=client_id
    )

    redirect_uris = [str(uri) for uri in oauth.context.client_metadata.redirect_uris]
    assert len(redirect_uris) == 1
    assert redirect_uris[0].startswith(expected)
    if client_id is not None:
        assert oauth._static_client_info is not None
        assert [
            str(uri) for uri in oauth._static_client_info.redirect_uris or []
        ] == redirect_uris


@pytest.mark.parametrize(
    "callback_base_url",
    [
        "ftp://canvas.example.test",
        "https://user:pass@canvas.example.test",
        "https://canvas.example.test/?next=x",
        "https://canvas.example.test/#x",
        "not a url",
    ],
)
def test_callback_base_url_must_be_a_plain_http_base(callback_base_url: str):
    response = _client().post(
        "/api/mcp/oauth/start",
        json={
            "server": _oauth_server("https://mcp.example.com/mcp"),
            "callback_base_url": callback_base_url,
        },
    )

    assert response.status_code == 422
