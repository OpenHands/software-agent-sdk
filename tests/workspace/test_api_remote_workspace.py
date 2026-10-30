"""Test APIRemoteWorkspace timeout configuration."""

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest


def test_api_timeout_is_used_in_client():
    """Test that api_timeout parameter is used for the HTTP client timeout."""
    from openhands.workspace import APIRemoteWorkspace

    # Mock the entire initialization process
    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime") as mock_init:
        mock_init.return_value = None

        # Create a workspace with custom api_timeout
        custom_timeout = 300.0
        workspace = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
            api_timeout=custom_timeout,
        )

        # The runtime properties need to be set for client initialization
        workspace._runtime_id = "test-runtime-id"
        workspace._runtime_url = "https://test-runtime.com"
        workspace._session_api_key = "test-session-key"
        workspace.host = workspace._runtime_url

        # Access the client property to trigger initialization
        client = workspace.client

        # Verify that the client's timeout uses the custom api_timeout
        assert isinstance(client, httpx.Client)
        assert client.timeout.read == custom_timeout
        assert client.timeout.connect == 10.0
        assert client.timeout.write == 10.0
        assert client.timeout.pool == 10.0

        # Clean up
        workspace._runtime_id = None  # Prevent cleanup from trying to stop runtime
        workspace.cleanup()


def test_api_timeout_default_value():
    """Test that the default api_timeout is 60 seconds."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime") as mock_init:
        mock_init.return_value = None

        workspace = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
        )

        # The runtime properties need to be set for client initialization
        workspace._runtime_id = "test-runtime-id"
        workspace._runtime_url = "https://test-runtime.com"
        workspace._session_api_key = "test-session-key"
        workspace.host = workspace._runtime_url

        # Access the client property to trigger initialization
        client = workspace.client

        # Verify default timeout is 60 seconds
        assert client.timeout.read == 60.0

        # Clean up
        workspace._runtime_id = None
        workspace.cleanup()


def test_different_timeout_values():
    """Test that different api_timeout values are correctly applied."""
    from openhands.workspace import APIRemoteWorkspace

    test_timeouts = [30.0, 120.0, 600.0]

    for timeout_value in test_timeouts:
        with patch.object(
            APIRemoteWorkspace, "_start_or_attach_to_runtime"
        ) as mock_init:
            mock_init.return_value = None

            workspace = APIRemoteWorkspace(
                runtime_api_url="https://example.com",
                runtime_api_key="test-key",
                server_image="test-image",
                api_timeout=timeout_value,
            )

            workspace._runtime_id = "test-runtime-id"
            workspace._runtime_url = "https://test-runtime.com"
            workspace._session_api_key = "test-session-key"
            workspace.host = workspace._runtime_url

            client = workspace.client

            assert client.timeout.read == timeout_value, (
                f"Expected timeout {timeout_value}, got {client.timeout.read}"
            )

            workspace._runtime_id = None
            workspace.cleanup()


def test_startup_wait_timeout_default_and_override():
    """Ensure startup_wait_timeout can be configured."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime") as mock_init:
        mock_init.return_value = None
        default_ws = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
        )
        assert default_ws.startup_wait_timeout == 300.0
        default_ws._runtime_id = None
        default_ws.cleanup()

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime") as mock_init:
        mock_init.return_value = None
        custom_ws = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
            startup_wait_timeout=600.0,
        )
        assert custom_ws.startup_wait_timeout == 600.0
        custom_ws._runtime_id = None
        custom_ws.cleanup()


def test_forward_env_default_is_empty():
    """Test that forward_env defaults to an empty list."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime") as mock_init:
        mock_init.return_value = None

        workspace = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
        )

        assert workspace.forward_env == []

        workspace._runtime_id = None
        workspace.cleanup()


def test_forward_env_is_included_in_start_runtime_payload():
    """Test that forward_env variables are included in the runtime start payload."""
    from openhands.workspace import APIRemoteWorkspace

    # Set up test environment variables
    test_env = {
        "TEST_VAR_1": "value1",
        "TEST_VAR_2": "value2",
        "UNSET_VAR": None,  # This one won't be in os.environ
    }

    with patch.dict(os.environ, {k: v for k, v in test_env.items() if v is not None}):
        with patch.object(
            APIRemoteWorkspace, "_start_or_attach_to_runtime"
        ) as mock_attach:
            mock_attach.return_value = None

            workspace = APIRemoteWorkspace(
                runtime_api_url="https://example.com",
                runtime_api_key="test-key",
                server_image="test-image",
                forward_env=["TEST_VAR_1", "TEST_VAR_2", "UNSET_VAR"],
            )

            # Mock the API request method to capture the payload
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "runtime_id": "test-id",
                "url": "https://test-runtime.com",
                "session_api_key": "test-key",
            }

            with patch.object(
                workspace, "_send_api_request", return_value=mock_response
            ) as mock_request:
                workspace._start_runtime()

                # Verify the API was called with the correct payload
                mock_request.assert_called_once()
                call_kwargs = mock_request.call_args
                payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")

                # Check that environment contains the forwarded variables
                assert "environment" in payload
                assert payload["environment"]["TEST_VAR_1"] == "value1"
                assert payload["environment"]["TEST_VAR_2"] == "value2"
                # UNSET_VAR should not be in environment since it's not in os.environ
                assert "UNSET_VAR" not in payload["environment"]

            workspace._runtime_id = None
            workspace.cleanup()


def test_forward_env_empty_list_results_in_empty_environment():
    """Test that an empty forward_env results in an empty environment dict."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime") as mock_attach:
        mock_attach.return_value = None

        workspace = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
            forward_env=[],
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "runtime_id": "test-id",
            "url": "https://test-runtime.com",
            "session_api_key": "test-key",
        }

        with patch.object(
            workspace, "_send_api_request", return_value=mock_response
        ) as mock_request:
            workspace._start_runtime()

            call_kwargs = mock_request.call_args
            payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")

            assert payload["environment"] == {}

        workspace._runtime_id = None
        workspace.cleanup()


def test_start_runtime_logs_environment_keys_without_values(caplog):
    """Test that start-runtime logs do not include forwarded env values."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.dict(
        os.environ,
        {
            "SECRET_TOKEN": "super-secret-value",
            "ANOTHER_SECRET": "another-secret-value",
        },
    ):
        with patch.object(
            APIRemoteWorkspace, "_start_or_attach_to_runtime"
        ) as mock_attach:
            mock_attach.return_value = None

            workspace = APIRemoteWorkspace(
                runtime_api_url="https://example.com",
                runtime_api_key="test-key",
                server_image="test-image",
                forward_env=["SECRET_TOKEN", "ANOTHER_SECRET"],
            )

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "runtime_id": "test-id",
                "url": "https://test-runtime.com",
                "session_api_key": "test-key",
            }

            with patch.object(
                workspace, "_send_api_request", return_value=mock_response
            ):
                with caplog.at_level("INFO"):
                    workspace._start_runtime()

            log_text = "\n".join(record.getMessage() for record in caplog.records)
            assert "super-secret-value" not in log_text
            assert "another-secret-value" not in log_text
            assert "Runtime start payload:" in log_text
            assert "environment_keys=['ANOTHER_SECRET', 'SECRET_TOKEN']" in log_text

            workspace._runtime_id = None
            workspace.cleanup()


# --- Callback integration tests ---


def _make_workspace():
    """Create a workspace without starting runtime for callback tests."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime"):
        ws = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
        )
        ws._runtime_id = None  # Prevent cleanup from making API calls
        return ws


def test_api_remote_workspace_exit_sends_callback(monkeypatch):
    """Test that APIRemoteWorkspace.__exit__ sends completion callback."""
    monkeypatch.setenv("AUTOMATION_CALLBACK_URL", "https://svc.test/complete")
    monkeypatch.setenv("AUTOMATION_CALLBACK_API_KEY", "test-api-key")
    ws = _make_workspace()

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.Client") as MockClient:
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        ws.__exit__(None, None, None)

        mock_client.post.assert_called_once()
        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["status"] == "COMPLETED"


# --- Runtime API failure paths ---

RUNTIME_API = "https://runtime.test"
RUNTIME_URL = "https://rt-123.runtime.test"


class _FakeHealth:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def runtime_api(monkeypatch):
    """Route APIRemoteWorkspace HTTP calls to scripted per-endpoint responses.

    ``routes`` maps ``"METHOD /path"`` to a list of responses consumed in order
    (the last one repeats). ``calls`` records every ``"METHOD /path"`` hit.
    """
    from openhands.workspace.remote_api import workspace as module

    routes: dict[str, list[httpx.Response]] = {}
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        calls.append(key)
        queue = routes.get(key)
        if not queue:
            return httpx.Response(404, json={"detail": "not found"})
        return queue.pop(0) if len(queue) > 1 else queue[0]

    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "Client", client_factory)
    monkeypatch.setattr(module, "urlopen", lambda *a, **kw: _FakeHealth())
    monkeypatch.setattr("time.sleep", lambda _s: None)
    return routes, calls


def _session(status: str | None, **extra) -> httpx.Response:
    return httpx.Response(200, json={"pod_status": status, **extra})


def _attached(status: str = "running") -> httpx.Response:
    return httpx.Response(
        200, json={"status": status, "runtime_id": "rt-123", "url": RUNTIME_URL}
    )


def _started() -> httpx.Response:
    return httpx.Response(
        200,
        json={"runtime_id": "rt-123", "url": RUNTIME_URL, "session_api_key": "sk"},
    )


def _new_workspace(**kwargs):
    from openhands.workspace import APIRemoteWorkspace

    return APIRemoteWorkspace(
        runtime_api_url=RUNTIME_API,
        runtime_api_key="key",
        server_image="img",
        session_id="sess",
        **kwargs,
    )


def test_startup_starts_runtime_when_session_lookup_404s(runtime_api):
    routes, calls = runtime_api
    routes["GET /sessions/sess"] = [
        httpx.Response(404),
        _session("pending"),
        _session("ready"),
    ]
    routes["POST /start"] = [_started()]

    workspace = _new_workspace()

    assert workspace.host == RUNTIME_URL
    assert workspace.api_key == "sk"
    assert calls.count("POST /start") == 1
    workspace._runtime_id = None


def test_startup_propagates_non_404_session_lookup_error(runtime_api):
    routes, calls = runtime_api
    routes["GET /sessions/sess"] = [httpx.Response(500, text="boom")]

    with pytest.raises(httpx.HTTPStatusError):
        _new_workspace()

    assert "POST /start" not in calls


def test_startup_rejects_start_response_without_url(runtime_api):
    routes, calls = runtime_api
    routes["POST /start"] = [httpx.Response(200, json={"runtime_id": "rt-123"})]

    with pytest.raises(ValueError, match="Invalid runtime response"):
        _new_workspace()

    assert "POST /start" in calls


@pytest.mark.parametrize(
    ("pod_status", "match"),
    [
        ("failed", "Pod logs: oom"),
        ("crashloopbackoff", "possibly OOM"),
    ],
)
def test_terminal_pod_status_fails_fast_and_stops_runtime(
    runtime_api, pod_status, match
):
    routes, calls = runtime_api
    routes["POST /start"] = [_started()]
    routes["GET /sessions/sess"] = [
        httpx.Response(404),
        _session(pod_status, pod_logs="oom"),
    ]
    routes["POST /stop"] = [httpx.Response(200, json={})]

    with pytest.raises(ValueError, match=match):
        _new_workspace()

    # Terminal states are not retried: one lookup to attach, one readiness probe.
    assert calls.count("GET /sessions/sess") == 2
    assert calls[-1] == "POST /stop"


def test_unhealthy_runtime_is_retried_until_health_passes(runtime_api, monkeypatch):
    from openhands.workspace.remote_api import workspace as module

    routes, calls = runtime_api
    routes["POST /start"] = [_started()]
    routes["GET /sessions/sess"] = [httpx.Response(404), _session("ready")]
    health = iter([OSError("connection refused"), _FakeHealth()])

    def flaky_urlopen(*args, **kwargs):
        result = next(health)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(module, "urlopen", flaky_urlopen)

    workspace = _new_workspace()

    assert workspace.host == RUNTIME_URL
    assert calls.count("GET /sessions/sess") == 3
    workspace._runtime_id = None


def test_paused_runtime_is_resumed_on_attach(runtime_api):
    routes, calls = runtime_api
    routes["GET /sessions/sess"] = [_attached("paused"), _session("ready")]
    routes["POST /resume"] = [httpx.Response(200, json={})]

    workspace = _new_workspace()

    assert "POST /resume" in calls
    assert "POST /start" not in calls
    workspace._runtime_id = None


@pytest.mark.xfail(
    strict=True,
    reason="BUG: a JSON null pod_status raises AttributeError instead of being "
    "retried like other unknown statuses",
)
def test_null_pod_status_is_retried(runtime_api):
    routes, _ = runtime_api
    routes["POST /start"] = [_started()]
    routes["GET /sessions/sess"] = [
        httpx.Response(404),
        _session(None),
        _session("ready"),
    ]

    workspace = _new_workspace()

    assert workspace.host == RUNTIME_URL
    workspace._runtime_id = None


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, "POST /stop"),
        ({"pause_on_close": True}, "POST /pause"),
    ],
)
def test_cleanup_swallows_api_errors_and_is_idempotent(runtime_api, kwargs, expected):
    routes, calls = runtime_api
    routes["GET /sessions/sess"] = [_attached(), _session("ready")]
    routes[expected] = [httpx.Response(503, text="unavailable")]
    workspace = _new_workspace(**kwargs)

    workspace.cleanup()
    workspace.cleanup()

    assert calls.count(expected) == 1
    assert workspace._runtime_id is None


def test_cleanup_with_keep_alive_makes_no_api_call(runtime_api):
    routes, calls = runtime_api
    routes["GET /sessions/sess"] = [_attached(), _session("ready")]
    workspace = _new_workspace(keep_alive=True)

    workspace.cleanup()

    assert not any(c.startswith("POST") for c in calls)
    assert workspace._runtime_id is None


def test_invalid_resource_factor_rejected_before_any_request(runtime_api):
    _, calls = runtime_api

    with pytest.raises(ValueError, match="resource_factor"):
        _new_workspace(resource_factor=3)

    assert calls == []
