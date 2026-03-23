"""Tests for the MaybeDontAnalyzer class."""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.security.maybedont import MaybeDontAnalyzer
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.tool import Action


class MaybeDontTestAction(Action):
    """Mock action for MaybeDont analyzer testing."""

    command: str = "test_command"


def create_mock_action_event(
    tool_name: str = "execute_bash",
    command: str = "ls -la",
    thought_text: str = "I need to list files",
    summary: str | None = "listing directory contents",
    security_risk: SecurityRisk = SecurityRisk.UNKNOWN,
) -> ActionEvent:
    """Helper to create ActionEvent for testing."""
    return ActionEvent(
        thought=[TextContent(text=thought_text)],
        action=MaybeDontTestAction(command=command),
        tool_name=tool_name,
        tool_call_id="test_call_id",
        tool_call=MessageToolCall(
            id="test_call_id",
            name=tool_name,
            arguments=json.dumps({"command": command}),
            origin="completion",
        ),
        llm_response_id="test_response_id",
        security_risk=security_risk,
        summary=summary,
    )


class TestMaybeDontAnalyzerInit:
    """Tests for MaybeDontAnalyzer initialization."""

    def test_init_with_defaults(self):
        """Test default initialization values."""
        with patch.dict("os.environ", {}, clear=True):
            analyzer = MaybeDontAnalyzer()
            assert analyzer.gateway_url == "http://localhost:8080"
            assert analyzer.timeout == 30.0
            assert analyzer.client_id == "openhands"

    def test_init_with_gateway_url_from_env(self):
        """Test that gateway URL is read from environment."""
        with patch.dict(
            "os.environ", {"MAYBE_DONT_GATEWAY_URL": "http://gateway:9090"}
        ):
            analyzer = MaybeDontAnalyzer()
            assert analyzer.gateway_url == "http://gateway:9090"

    def test_init_with_explicit_gateway_url(self):
        """Test that explicit gateway URL takes precedence over default."""
        analyzer = MaybeDontAnalyzer(gateway_url="http://custom:8080")
        assert analyzer.gateway_url == "http://custom:8080"

    def test_init_explicit_takes_precedence_over_env(self):
        """Test that explicit param takes precedence over env var."""
        with patch.dict(
            "os.environ",
            {"MAYBE_DONT_GATEWAY_URL": "http://from-env:9090"},
        ):
            analyzer = MaybeDontAnalyzer(gateway_url="http://explicit:8080")
            assert analyzer.gateway_url == "http://explicit:8080"

    def test_init_with_custom_timeout(self):
        """Test that custom timeout can be set."""
        analyzer = MaybeDontAnalyzer(timeout=10.0)
        assert analyzer.timeout == 10.0

    def test_init_with_custom_client_id(self):
        """Test that custom client_id can be set."""
        analyzer = MaybeDontAnalyzer(client_id="my-agent")
        assert analyzer.client_id == "my-agent"

    def test_init_logs_configuration(self, caplog: pytest.LogCaptureFixture):
        """Test that initialization logs the configuration."""
        MaybeDontAnalyzer(gateway_url="http://test:8080")
        assert "MaybeDontAnalyzer initialized" in caplog.text
        assert "http://test:8080" in caplog.text


class TestMaybeDontAnalyzerBuildRequest:
    """Tests for the _build_request method."""

    @pytest.fixture
    def analyzer(self) -> MaybeDontAnalyzer:
        """Create analyzer with default config."""
        return MaybeDontAnalyzer()

    def test_build_request_basic(self, analyzer: MaybeDontAnalyzer):
        """Test basic request building from ActionEvent."""
        action = create_mock_action_event(
            tool_name="execute_bash",
            command="ls -la",
        )

        request = analyzer._build_request(action)

        assert request["action_type"] == "tool_call"
        assert request["target"] == "execute_bash"
        assert request["parameters"] == {"command": "ls -la"}
        assert request["actor"] == "openhands"

    def test_build_request_includes_thought(self, analyzer: MaybeDontAnalyzer):
        """Test that agent thought is included in context."""
        action = create_mock_action_event(
            thought_text="I need to clean up temporary files",
        )

        request = analyzer._build_request(action)

        assert "context" in request
        assert request["context"]["thought"] == "I need to clean up temporary files"

    def test_build_request_includes_summary(self, analyzer: MaybeDontAnalyzer):
        """Test that action summary is included in context."""
        action = create_mock_action_event(
            summary="cleaning up temp files",
        )

        request = analyzer._build_request(action)

        assert request["context"]["summary"] == "cleaning up temp files"

    def test_build_request_no_summary(self, analyzer: MaybeDontAnalyzer):
        """Test request building without summary."""
        action = create_mock_action_event(summary=None)

        request = analyzer._build_request(action)

        assert "summary" not in request.get("context", {})

    def test_build_request_custom_client_id(self):
        """Test that custom client_id is used as actor."""
        analyzer = MaybeDontAnalyzer(client_id="my-agent")
        action = create_mock_action_event()

        request = analyzer._build_request(action)

        assert request["actor"] == "my-agent"

    def test_build_request_invalid_arguments_json(self, analyzer: MaybeDontAnalyzer):
        """Test that invalid JSON in tool_call arguments sends empty parameters."""
        action = ActionEvent(
            thought=[TextContent(text="test thought")],
            action=MaybeDontTestAction(command="test"),
            tool_name="execute_bash",
            tool_call_id="test_call_id",
            tool_call=MessageToolCall(
                id="test_call_id",
                name="execute_bash",
                arguments="not valid json",
                origin="completion",
            ),
            llm_response_id="test_response_id",
        )

        request = analyzer._build_request(action)

        assert request["parameters"] == {}

    def test_build_request_empty_thought(self, analyzer: MaybeDontAnalyzer):
        """Test request building with empty thought text."""
        action = create_mock_action_event(thought_text="")

        request = analyzer._build_request(action)

        # Empty thought should not be included in context
        assert "thought" not in request.get("context", {})


class TestMaybeDontAnalyzerRiskMapping:
    """Tests for risk_level response mapping."""

    @pytest.fixture
    def analyzer(self) -> MaybeDontAnalyzer:
        """Create analyzer with default config."""
        return MaybeDontAnalyzer()

    def test_map_high_risk(self, analyzer: MaybeDontAnalyzer):
        """Test that 'high' risk_level maps to HIGH."""
        assert (
            analyzer._map_response_to_risk({"risk_level": "high"}) == SecurityRisk.HIGH
        )

    def test_map_medium_risk(self, analyzer: MaybeDontAnalyzer):
        """Test that 'medium' risk_level maps to MEDIUM."""
        assert (
            analyzer._map_response_to_risk({"risk_level": "medium"})
            == SecurityRisk.MEDIUM
        )

    def test_map_low_risk(self, analyzer: MaybeDontAnalyzer):
        """Test that 'low' risk_level maps to LOW."""
        assert analyzer._map_response_to_risk({"risk_level": "low"}) == SecurityRisk.LOW

    def test_map_unknown_risk(self, analyzer: MaybeDontAnalyzer):
        """Test that 'unknown' risk_level maps to UNKNOWN."""
        assert (
            analyzer._map_response_to_risk({"risk_level": "unknown"})
            == SecurityRisk.UNKNOWN
        )

    def test_map_missing_risk_level(self, analyzer: MaybeDontAnalyzer):
        """Test that missing risk_level defaults to UNKNOWN."""
        assert analyzer._map_response_to_risk({}) == SecurityRisk.UNKNOWN

    def test_map_unrecognized_risk_level(self, analyzer: MaybeDontAnalyzer):
        """Test that unrecognized risk_level defaults to UNKNOWN."""
        assert (
            analyzer._map_response_to_risk({"risk_level": "critical"})
            == SecurityRisk.UNKNOWN
        )


class TestMaybeDontAnalyzerSecurityRisk:
    """Tests for the security_risk method (end-to-end)."""

    @pytest.fixture
    def analyzer(self) -> MaybeDontAnalyzer:
        """Create analyzer with default config."""
        return MaybeDontAnalyzer()

    def test_security_risk_allowed_low(self, analyzer: MaybeDontAnalyzer):
        """Test allowed action returns LOW risk."""
        action = create_mock_action_event()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "request_id": "abc123",
            "allowed": True,
            "risk_level": "low",
            "message": "Action allowed",
        }

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_get_client.return_value = mock_client

            result = analyzer.security_risk(action)

            assert result == SecurityRisk.LOW
            mock_client.post.assert_called_once()

    def test_security_risk_denied_high(self, analyzer: MaybeDontAnalyzer):
        """Test denied action returns HIGH risk."""
        action = create_mock_action_event(
            tool_name="execute_bash",
            command="rm -rf /",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "request_id": "abc123",
            "allowed": False,
            "risk_level": "high",
            "message": "Action denied by policy",
            "results": [
                {
                    "policy_name": "no-destructive-ops",
                    "policy_type": "ai",
                    "action": "deny",
                    "message": "Destructive operation",
                }
            ],
        }

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_get_client.return_value = mock_client

            result = analyzer.security_risk(action)

            assert result == SecurityRisk.HIGH

    def test_security_risk_audit_only_medium(self, analyzer: MaybeDontAnalyzer):
        """Test audit_only deny returns MEDIUM risk."""
        action = create_mock_action_event()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "request_id": "abc123",
            "allowed": True,
            "risk_level": "medium",
            "message": "Action allowed (audit_only bypass)",
        }

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_get_client.return_value = mock_client

            result = analyzer.security_risk(action)

            assert result == SecurityRisk.MEDIUM

    def test_security_risk_no_policies_unknown(self, analyzer: MaybeDontAnalyzer):
        """Test no policies evaluated returns UNKNOWN risk."""
        action = create_mock_action_event()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "request_id": "abc123",
            "allowed": True,
            "risk_level": "unknown",
            "message": "No policies evaluated",
        }

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_get_client.return_value = mock_client

            result = analyzer.security_risk(action)

            assert result == SecurityRisk.UNKNOWN

    def test_security_risk_gateway_unreachable(self, analyzer: MaybeDontAnalyzer):
        """Test that unreachable gateway returns UNKNOWN risk."""
        action = create_mock_action_event()

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")
            mock_get_client.return_value = mock_client

            result = analyzer.security_risk(action)

            assert result == SecurityRisk.UNKNOWN

    def test_security_risk_gateway_500(self, analyzer: MaybeDontAnalyzer):
        """Test that gateway 500 error returns UNKNOWN risk."""
        action = create_mock_action_event()

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_get_client.return_value = mock_client

            result = analyzer.security_risk(action)

            assert result == SecurityRisk.UNKNOWN

    def test_security_risk_gateway_400(self, analyzer: MaybeDontAnalyzer):
        """Test that gateway 400 error returns UNKNOWN risk."""
        action = create_mock_action_event()

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"error": "missing target field"}'

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_get_client.return_value = mock_client

            result = analyzer.security_risk(action)

            assert result == SecurityRisk.UNKNOWN

    def test_security_risk_timeout(self, analyzer: MaybeDontAnalyzer):
        """Test that gateway timeout returns UNKNOWN risk."""
        action = create_mock_action_event()

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.side_effect = httpx.TimeoutException("Timeout")
            mock_get_client.return_value = mock_client

            result = analyzer.security_risk(action)

            assert result == SecurityRisk.UNKNOWN

    def test_security_risk_invalid_json_response(self, analyzer: MaybeDontAnalyzer):
        """Test that invalid JSON response returns UNKNOWN risk."""
        action = create_mock_action_event()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = json.JSONDecodeError("", "", 0)
        mock_response.text = "not valid json"

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_get_client.return_value = mock_client

            result = analyzer.security_risk(action)

            assert result == SecurityRisk.UNKNOWN


class TestMaybeDontAnalyzerHTTPHeaders:
    """Tests for HTTP header handling."""

    def test_client_sends_content_type_header(self):
        """Test that HTTP client sends correct Content-Type header."""
        analyzer = MaybeDontAnalyzer()
        client = analyzer._create_client()
        try:
            assert client.headers["content-type"] == "application/json"
        finally:
            client.close()

    def test_client_sends_client_id_header(self):
        """Test that HTTP client sends X-Maybe-Dont-Client-ID header."""
        analyzer = MaybeDontAnalyzer(client_id="my-agent")
        client = analyzer._create_client()
        try:
            assert client.headers["x-maybe-dont-client-id"] == "my-agent"
        finally:
            client.close()

    def test_request_url_construction(self, caplog: pytest.LogCaptureFixture):
        """Test that the correct URL is called."""
        analyzer = MaybeDontAnalyzer(gateway_url="http://gateway:9090")
        action = create_mock_action_event()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"risk_level": "low", "allowed": True}

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_get_client.return_value = mock_client

            analyzer.security_risk(action)

            call_args = mock_client.post.call_args
            assert call_args[0][0] == "http://gateway:9090/api/v1/action/validate"

    def test_request_url_strips_trailing_slash(self):
        """Test that trailing slash in gateway_url is handled."""
        analyzer = MaybeDontAnalyzer(gateway_url="http://gateway:9090/")
        action = create_mock_action_event()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"risk_level": "low", "allowed": True}

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_get_client.return_value = mock_client

            analyzer.security_risk(action)

            call_args = mock_client.post.call_args
            assert call_args[0][0] == "http://gateway:9090/api/v1/action/validate"


class TestMaybeDontAnalyzerLifecycle:
    """Tests for analyzer lifecycle management."""

    def test_close_cleans_up_client(self):
        """Test that close cleans up the HTTP client."""
        analyzer = MaybeDontAnalyzer()

        mock_client = MagicMock()
        mock_client.is_closed = False
        analyzer._client = mock_client

        analyzer.close()

        mock_client.close.assert_called_once()
        assert analyzer._client is None

    def test_close_handles_no_client(self):
        """Test that close handles case when no client exists."""
        analyzer = MaybeDontAnalyzer()
        # Should not raise
        analyzer.close()

    def test_close_handles_already_closed_client(self):
        """Test that close handles already-closed client gracefully."""
        analyzer = MaybeDontAnalyzer()

        mock_client = MagicMock()
        mock_client.is_closed = True
        analyzer._client = mock_client

        analyzer.close()

        mock_client.close.assert_not_called()

    def test_set_events_stores_events(self):
        """Test that set_events stores events for future use."""
        analyzer = MaybeDontAnalyzer()
        events = ["event1", "event2"]
        analyzer.set_events(events)
        assert analyzer._events == events

    def test_set_events_empty_list(self):
        """Test that set_events handles empty list."""
        analyzer = MaybeDontAnalyzer()
        analyzer.set_events([])
        assert analyzer._events == []


class TestMaybeDontAnalyzerHTTPClientLifecycle:
    """Integration tests for HTTP client lifecycle using MockTransport."""

    def test_client_creation_and_reuse(self):
        """Test that HTTP client is created and reused correctly."""

        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "request_id": "test",
                    "allowed": True,
                    "risk_level": "low",
                },
            )

        transport = httpx.MockTransport(mock_handler)
        analyzer = MaybeDontAnalyzer()
        analyzer._client = httpx.Client(transport=transport)

        action = create_mock_action_event()

        try:
            result = analyzer.security_risk(action)
            assert result == SecurityRisk.LOW

            # Second call should reuse the same client
            result = analyzer.security_risk(action)
            assert result == SecurityRisk.LOW
        finally:
            analyzer.close()

    def test_client_recreated_after_close(self):
        """Test that client is recreated after close() is called."""
        call_count = 0

        def mock_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                200,
                json={
                    "request_id": "test",
                    "allowed": True,
                    "risk_level": "low",
                },
            )

        analyzer = MaybeDontAnalyzer()
        transport = httpx.MockTransport(mock_handler)
        analyzer._client = httpx.Client(transport=transport)

        action = create_mock_action_event()

        try:
            result = analyzer.security_risk(action)
            assert result == SecurityRisk.LOW
            assert call_count == 1

            analyzer.close()
            assert analyzer._client is None

            # Next call should create a new client
            with patch.object(analyzer, "_create_client") as mock_create:
                new_transport = httpx.MockTransport(mock_handler)
                mock_create.return_value = httpx.Client(transport=new_transport)

                result = analyzer.security_risk(action)
                assert result == SecurityRisk.LOW
                mock_create.assert_called_once()
        finally:
            analyzer.close()

    def test_request_body_sent_correctly(self):
        """Test that the correct request body is sent to the gateway."""
        captured_request: httpx.Request | None = None

        def mock_handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(
                200,
                json={
                    "request_id": "test",
                    "allowed": True,
                    "risk_level": "low",
                },
            )

        transport = httpx.MockTransport(mock_handler)
        analyzer = MaybeDontAnalyzer(client_id="test-agent")
        analyzer._client = httpx.Client(transport=transport)

        action = create_mock_action_event(
            tool_name="execute_bash",
            command="rm -rf /tmp/old",
            thought_text="I need to remove old temporary files",
            summary="removing old temp files",
        )

        try:
            analyzer.security_risk(action)

            assert captured_request is not None
            body = json.loads(captured_request.content)
            assert body["action_type"] == "tool_call"
            assert body["target"] == "execute_bash"
            assert body["parameters"] == {"command": "rm -rf /tmp/old"}
            assert body["actor"] == "test-agent"
            assert body["context"]["thought"] == "I need to remove old temporary files"
            assert body["context"]["summary"] == "removing old temp files"
        finally:
            analyzer.close()
