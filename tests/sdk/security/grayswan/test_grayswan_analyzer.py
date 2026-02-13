"""Tests for the GraySwanAnalyzer class."""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr

from openhands.sdk.event import ActionEvent, MessageEvent, SystemPromptEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.security.grayswan import GraySwanAnalyzer
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.tool import Action


class GraySwanTestAction(Action):
    """Mock action for GraySwan analyzer testing."""

    command: str = "test_command"


def create_mock_action_event(
    tool_name: str = "test_tool",
    command: str = "test",
    security_risk: SecurityRisk = SecurityRisk.UNKNOWN,
) -> ActionEvent:
    """Helper to create ActionEvent for testing."""
    return ActionEvent(
        thought=[TextContent(text="test thought")],
        action=GraySwanTestAction(command=command),
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
    )


def create_mock_message_event(
    content: str = "test message",
    source: str = "user",
) -> MessageEvent:
    """Helper to create MessageEvent for testing."""
    return MessageEvent(
        source=source,  # type: ignore
        llm_message=Message(
            role="user" if source == "user" else "assistant",
            content=[TextContent(text=content)],
        ),
    )


def create_mock_system_prompt_event(
    prompt: str = "You are a helpful assistant.",
) -> SystemPromptEvent:
    """Helper to create SystemPromptEvent for testing."""
    return SystemPromptEvent(
        system_prompt=TextContent(text=prompt),
        tools=[],
    )


class TestGraySwanAnalyzerInit:
    """Tests for GraySwanAnalyzer initialization."""

    def test_init_without_api_key_logs_warning(self, caplog: pytest.LogCaptureFixture):
        """Test that initialization without API key logs a warning."""
        with patch.dict("os.environ", {}, clear=True):
            analyzer = GraySwanAnalyzer()
            assert analyzer.api_key is None
            assert "GRAYSWAN_API_KEY not set" in caplog.text

    def test_init_with_api_key_from_env(self):
        """Test that API key is read from environment."""
        with patch.dict("os.environ", {"GRAYSWAN_API_KEY": "test_key"}):
            analyzer = GraySwanAnalyzer()
            assert analyzer.api_key is not None
            assert analyzer.api_key.get_secret_value() == "test_key"

    def test_init_with_api_key_param(self):
        """Test that API key can be passed as parameter."""
        analyzer = GraySwanAnalyzer(api_key=SecretStr("param_key"))
        assert analyzer.api_key is not None
        assert analyzer.api_key.get_secret_value() == "param_key"

    def test_init_with_default_policy_id(self, caplog: pytest.LogCaptureFixture):
        """Test that default policy ID is used when not provided."""
        with patch.dict("os.environ", {"GRAYSWAN_API_KEY": "test_key"}, clear=True):
            analyzer = GraySwanAnalyzer()
            assert analyzer.policy_id == "689ca4885af3538a39b2ba04"
            assert "Using default GraySwan policy ID" in caplog.text

    def test_init_with_policy_id_from_env(self, caplog: pytest.LogCaptureFixture):
        """Test that policy ID is read from environment."""
        with patch.dict(
            "os.environ",
            {"GRAYSWAN_API_KEY": "test_key", "GRAYSWAN_POLICY_ID": "custom_policy"},
        ):
            analyzer = GraySwanAnalyzer()
            assert analyzer.policy_id == "custom_policy"
            assert "Using GraySwan policy ID from environment" in caplog.text

    def test_init_with_custom_thresholds(self):
        """Test that custom thresholds can be set."""
        analyzer = GraySwanAnalyzer(
            api_key=SecretStr("test_key"),
            low_threshold=0.2,
            medium_threshold=0.5,
        )
        assert analyzer.low_threshold == 0.2
        assert analyzer.medium_threshold == 0.5

    def test_init_with_invalid_threshold_order_raises_error(self):
        """Test that invalid threshold ordering raises ValueError."""
        with pytest.raises(
            ValueError, match="low_threshold.*must be less than.*medium_threshold"
        ):
            GraySwanAnalyzer(
                api_key=SecretStr("test_key"),
                low_threshold=0.7,
                medium_threshold=0.3,
            )

    def test_init_with_equal_thresholds_raises_error(self):
        """Test that equal thresholds raise ValueError."""
        with pytest.raises(
            ValueError, match="low_threshold.*must be less than.*medium_threshold"
        ):
            GraySwanAnalyzer(
                api_key=SecretStr("test_key"),
                low_threshold=0.5,
                medium_threshold=0.5,
            )


class TestGraySwanAnalyzerViolationMapping:
    """Tests for violation score to risk mapping."""

    @pytest.fixture
    def analyzer(self) -> GraySwanAnalyzer:
        """Create analyzer with test API key."""
        return GraySwanAnalyzer(api_key=SecretStr("test_key"))

    def test_map_low_violation(self, analyzer: GraySwanAnalyzer):
        """Test that low violation scores map to LOW risk."""
        assert analyzer._map_violation_to_risk(0.0) == SecurityRisk.LOW
        assert analyzer._map_violation_to_risk(0.1) == SecurityRisk.LOW
        assert analyzer._map_violation_to_risk(0.3) == SecurityRisk.LOW

    def test_map_medium_violation(self, analyzer: GraySwanAnalyzer):
        """Test that medium violation scores map to MEDIUM risk."""
        assert analyzer._map_violation_to_risk(0.31) == SecurityRisk.MEDIUM
        assert analyzer._map_violation_to_risk(0.5) == SecurityRisk.MEDIUM
        assert analyzer._map_violation_to_risk(0.7) == SecurityRisk.MEDIUM

    def test_map_high_violation(self, analyzer: GraySwanAnalyzer):
        """Test that high violation scores map to HIGH risk."""
        assert analyzer._map_violation_to_risk(0.71) == SecurityRisk.HIGH
        assert analyzer._map_violation_to_risk(0.9) == SecurityRisk.HIGH
        assert analyzer._map_violation_to_risk(1.0) == SecurityRisk.HIGH

    def test_map_boundary_low_threshold(self, analyzer: GraySwanAnalyzer):
        """Test exact boundary at low threshold."""
        assert analyzer._map_violation_to_risk(0.3) == SecurityRisk.LOW
        assert analyzer._map_violation_to_risk(0.30001) == SecurityRisk.MEDIUM

    def test_map_boundary_medium_threshold(self, analyzer: GraySwanAnalyzer):
        """Test exact boundary at medium threshold."""
        assert analyzer._map_violation_to_risk(0.7) == SecurityRisk.MEDIUM
        assert analyzer._map_violation_to_risk(0.70001) == SecurityRisk.HIGH


class TestGraySwanAnalyzerAPICall:
    """Tests for GraySwan API calls."""

    @pytest.fixture
    def analyzer(self) -> GraySwanAnalyzer:
        """Create analyzer with test API key."""
        return GraySwanAnalyzer(api_key=SecretStr("test_key"))

    def test_api_call_success_low_risk(self, analyzer: GraySwanAnalyzer):
        """Test successful API call with low violation score."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"violation": 0.1}

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_get_client.return_value = mock_client

            result = analyzer._call_grayswan_api([{"role": "user", "content": "test"}])

            assert result == SecurityRisk.LOW
            mock_client.post.assert_called_once()

    def test_api_call_success_high_risk(self, analyzer: GraySwanAnalyzer):
        """Test successful API call with high violation score."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"violation": 0.9}

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_get_client.return_value = mock_client

            result = analyzer._call_grayswan_api([{"role": "user", "content": "test"}])

            assert result == SecurityRisk.HIGH

    def test_api_call_ipi_detection_escalates_to_high(self, analyzer: GraySwanAnalyzer):
        """Test that indirect prompt injection detection escalates to HIGH risk."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"violation": 0.1, "ipi": True}

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_get_client.return_value = mock_client

            result = analyzer._call_grayswan_api([{"role": "user", "content": "test"}])

            assert result == SecurityRisk.HIGH

    def test_api_call_error_returns_unknown(self, analyzer: GraySwanAnalyzer):
        """Test that API errors return UNKNOWN risk."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_get_client.return_value = mock_client

            result = analyzer._call_grayswan_api([{"role": "user", "content": "test"}])

            assert result == SecurityRisk.UNKNOWN

    def test_api_call_timeout_returns_unknown(self, analyzer: GraySwanAnalyzer):
        """Test that API timeout returns UNKNOWN risk."""
        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.side_effect = httpx.TimeoutException("Timeout")
            mock_get_client.return_value = mock_client

            result = analyzer._call_grayswan_api([{"role": "user", "content": "test"}])

            assert result == SecurityRisk.UNKNOWN

    def test_api_call_without_api_key_returns_unknown(self):
        """Test that API call without API key returns UNKNOWN risk."""
        analyzer = GraySwanAnalyzer(api_key=None)
        result = analyzer._call_grayswan_api([{"role": "user", "content": "test"}])
        assert result == SecurityRisk.UNKNOWN

    def test_api_call_missing_violation_field_returns_unknown(
        self, analyzer: GraySwanAnalyzer
    ):
        """Test that missing violation field in response returns UNKNOWN risk."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"some_other_field": "value"}

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_get_client.return_value = mock_client

            result = analyzer._call_grayswan_api([{"role": "user", "content": "test"}])

            assert result == SecurityRisk.UNKNOWN


class TestGraySwanAnalyzerSecurityRisk:
    """Tests for the security_risk method."""

    @pytest.fixture
    def analyzer(self) -> GraySwanAnalyzer:
        """Create analyzer with test API key."""
        return GraySwanAnalyzer(api_key=SecretStr("test_key"))

    def test_security_risk_without_api_key(self):
        """Test that security_risk returns UNKNOWN without API key."""
        analyzer = GraySwanAnalyzer(api_key=None)
        action = create_mock_action_event()
        result = analyzer.security_risk(action)
        assert result == SecurityRisk.UNKNOWN

    def test_security_risk_with_events(self, analyzer: GraySwanAnalyzer):
        """Test security_risk with conversation history."""
        # Set up events
        events = [
            create_mock_system_prompt_event(),
            create_mock_message_event("Hello", "user"),
        ]
        analyzer.set_events(events)

        action = create_mock_action_event()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"violation": 0.5}

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_get_client.return_value = mock_client

            result = analyzer.security_risk(action)

            assert result == SecurityRisk.MEDIUM
            # Verify the API was called with messages
            call_args = mock_client.post.call_args
            assert call_args is not None
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert "messages" in payload
            assert len(payload["messages"]) > 0

    def test_security_risk_respects_history_limit(self, analyzer: GraySwanAnalyzer):
        """Test that security_risk respects history_limit."""
        analyzer.history_limit = 2

        # Create more events than the limit
        events = [create_mock_message_event(f"Message {i}", "user") for i in range(5)]
        analyzer.set_events(events)

        action = create_mock_action_event()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"violation": 0.1}

        with patch.object(analyzer, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_get_client.return_value = mock_client

            analyzer.security_risk(action)

            # Verify the API was called
            call_args = mock_client.post.call_args
            assert call_args is not None
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            # Should have 2 history events + 1 action = 3 messages
            assert len(payload["messages"]) == 3


class TestGraySwanAnalyzerSetEvents:
    """Tests for the set_events method."""

    def test_set_events(self):
        """Test that set_events stores events."""
        analyzer = GraySwanAnalyzer(api_key=SecretStr("test_key"))
        events = [
            create_mock_message_event("Hello", "user"),
            create_mock_message_event("Hi there", "agent"),
        ]
        analyzer.set_events(events)
        assert analyzer._events == events


class TestGraySwanAnalyzerClose:
    """Tests for the close method."""

    def test_close_cleans_up_client(self):
        """Test that close cleans up the HTTP client."""
        analyzer = GraySwanAnalyzer(api_key=SecretStr("test_key"))

        # Create a mock client
        mock_client = MagicMock()
        mock_client.is_closed = False
        analyzer._client = mock_client

        analyzer.close()

        mock_client.close.assert_called_once()
        assert analyzer._client is None

    def test_close_handles_no_client(self):
        """Test that close handles case when no client exists."""
        analyzer = GraySwanAnalyzer(api_key=SecretStr("test_key"))
        # Should not raise
        analyzer.close()


class TestGraySwanAnalyzerHTTPClientLifecycle:
    """Integration tests for HTTP client lifecycle using MockTransport."""

    def test_client_creation_and_reuse(self):
        """Test that HTTP client is created and reused correctly."""

        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"violation": 0.1})

        transport = httpx.MockTransport(mock_handler)
        analyzer = GraySwanAnalyzer(api_key=SecretStr("test_key"))

        # Manually set the client with mock transport
        analyzer._client = httpx.Client(transport=transport)

        action = create_mock_action_event()

        try:
            # First call should work
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
            return httpx.Response(200, json={"violation": 0.1})

        analyzer = GraySwanAnalyzer(api_key=SecretStr("test_key"))

        # Create initial client with mock transport
        transport = httpx.MockTransport(mock_handler)
        analyzer._client = httpx.Client(transport=transport)

        action = create_mock_action_event()

        try:
            # First call
            result = analyzer.security_risk(action)
            assert result == SecurityRisk.LOW
            assert call_count == 1

            # Close the client
            analyzer.close()
            assert analyzer._client is None

            # Next call should create a new client (but we need to mock it again)
            # Since _get_client creates a real client, we patch it for this test
            with patch.object(analyzer, "_create_client") as mock_create:
                new_transport = httpx.MockTransport(mock_handler)
                mock_create.return_value = httpx.Client(transport=new_transport)

                result = analyzer.security_risk(action)
                assert result == SecurityRisk.LOW
                mock_create.assert_called_once()
        finally:
            analyzer.close()

    def test_client_handles_json_decode_error(self):
        """Test that invalid JSON response is handled gracefully."""

        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not valid json")

        transport = httpx.MockTransport(mock_handler)
        analyzer = GraySwanAnalyzer(api_key=SecretStr("test_key"))
        analyzer._client = httpx.Client(transport=transport)

        action = create_mock_action_event()
        try:
            result = analyzer.security_risk(action)
            assert result == SecurityRisk.UNKNOWN
        finally:
            analyzer.close()
