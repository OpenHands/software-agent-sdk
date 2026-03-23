"""Tests for InsightTool."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from openhands.tools.insight import InsightAction, InsightObservation, InsightTool
from openhands.tools.insight.executor import InsightExecutor


class TestInsightAction:
    """Tests for InsightAction schema."""

    def test_default_values(self):
        """Test InsightAction default values."""
        action = InsightAction()
        assert action.generate_html is True
        assert action.suggest_skills is True
        assert action.max_sessions == 50

    def test_custom_values(self):
        """Test InsightAction with custom values."""
        action = InsightAction(
            generate_html=False,
            suggest_skills=False,
            max_sessions=10,
        )
        assert action.generate_html is False
        assert action.suggest_skills is False
        assert action.max_sessions == 10


class TestInsightObservation:
    """Tests for InsightObservation schema."""

    def test_empty_observation(self):
        """Test InsightObservation with default values."""
        obs = InsightObservation()
        assert obs.summary == ""
        assert obs.sessions_analyzed == 0
        assert obs.common_patterns == []
        assert obs.bottlenecks == []
        assert obs.suggestions == []
        assert obs.report_path is None

    def test_observation_with_data(self):
        """Test InsightObservation with data."""
        obs = InsightObservation(
            summary="Analyzed 5 sessions",
            sessions_analyzed=5,
            common_patterns=["Pattern 1", "Pattern 2"],
            bottlenecks=["Bottleneck 1"],
            suggestions=["Suggestion 1"],
            report_path="/path/to/report.html",
        )
        assert obs.summary == "Analyzed 5 sessions"
        assert obs.sessions_analyzed == 5
        assert len(obs.common_patterns) == 2
        assert len(obs.bottlenecks) == 1
        assert len(obs.suggestions) == 1
        assert obs.report_path == "/path/to/report.html"

    def test_to_llm_content(self):
        """Test InsightObservation to_llm_content conversion."""
        obs = InsightObservation(
            summary="Test summary",
            sessions_analyzed=3,
            common_patterns=["Pattern A"],
            bottlenecks=["Issue B"],
            suggestions=["Fix C"],
        )
        content = obs.to_llm_content
        assert len(content) == 1
        text = content[0].text
        assert "Test summary" in text
        assert "3" in text
        assert "Pattern A" in text
        assert "Issue B" in text
        assert "Fix C" in text


class TestInsightExecutor:
    """Tests for InsightExecutor."""

    def test_executor_initialization(self):
        """Test InsightExecutor initialization."""
        with tempfile.TemporaryDirectory() as temp_dir:
            from openhands.sdk.io import LocalFileStore

            file_store = LocalFileStore(root=temp_dir)
            executor = InsightExecutor(
                file_store=file_store,
                llm_model="test-model",
                api_key="test-key",
            )
            assert executor.llm_model == "test-model"
            assert executor.api_key == "test-key"
            assert executor.conversations_dir == "conversations"

    def test_executor_no_sessions(self):
        """Test executor when no sessions exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            from openhands.sdk.io import LocalFileStore

            file_store = LocalFileStore(root=temp_dir)
            executor = InsightExecutor(file_store=file_store)

            action = InsightAction(generate_html=False)
            result = executor(action)

            assert result.sessions_analyzed == 0
            assert "No conversation sessions found" in result.summary

    def test_analyze_sessions_empty(self):
        """Test _analyze_sessions with empty data."""
        with tempfile.TemporaryDirectory() as temp_dir:
            from openhands.sdk.io import LocalFileStore

            file_store = LocalFileStore(root=temp_dir)
            executor = InsightExecutor(file_store=file_store)

            analysis = executor._analyze_sessions([])
            assert "0 sessions" in analysis["summary"]
            # Empty sessions still generates average pattern
            assert len(analysis["patterns"]) <= 1

    def test_analyze_sessions_with_data(self):
        """Test _analyze_sessions with session data."""
        with tempfile.TemporaryDirectory() as temp_dir:
            from openhands.sdk.io import LocalFileStore

            file_store = LocalFileStore(root=temp_dir)
            executor = InsightExecutor(file_store=file_store)

            sessions_data = [
                {
                    "session_id": "session-1",
                    "event_count": 10,
                    "action_counts": {"BashAction": 5, "EditAction": 3},
                    "tool_usage": {"terminal": 5},
                    "error_count": 1,
                },
                {
                    "session_id": "session-2",
                    "event_count": 8,
                    "action_counts": {"BashAction": 4, "ReadAction": 2},
                    "tool_usage": {"terminal": 4},
                    "error_count": 0,
                },
            ]

            analysis = executor._analyze_sessions(sessions_data)
            assert "2 sessions" in analysis["summary"]
            assert analysis["total_events"] == 18
            assert analysis["total_errors"] == 1
            assert "BashAction" in analysis["action_counts"]

    def test_generate_suggestions(self):
        """Test _generate_suggestions."""
        with tempfile.TemporaryDirectory() as temp_dir:
            from openhands.sdk.io import LocalFileStore

            file_store = LocalFileStore(root=temp_dir)
            executor = InsightExecutor(file_store=file_store)

            # Test with high error rate
            analysis = {
                "total_events": 100,
                "total_errors": 20,
                "action_counts": {"BashAction": 25},
                "tool_usage": {"terminal": 25},
            }

            suggestions = executor._generate_suggestions(analysis)
            assert len(suggestions) > 0
            # Should suggest error handling due to high error rate
            assert any("error" in s.lower() for s in suggestions)

    def test_generate_html_report(self):
        """Test HTML report generation."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create .openhands directory structure
            openhands_dir = Path(temp_dir) / ".openhands"
            openhands_dir.mkdir(parents=True)

            from openhands.sdk.io import LocalFileStore

            file_store = LocalFileStore(root=temp_dir)
            executor = InsightExecutor(file_store=file_store)
            executor.usage_data_dir = str(openhands_dir / "usage-data")

            analysis = {
                "summary": "Test summary",
                "patterns": ["Pattern 1"],
                "bottlenecks": ["Bottleneck 1"],
                "total_events": 10,
                "total_errors": 1,
                "action_counts": {"TestAction": 5},
            }

            with patch.object(Path, "home", return_value=Path(temp_dir)):
                report_path = executor._generate_html_report(analysis, [])

            assert report_path is not None
            assert Path(report_path).exists()
            content = Path(report_path).read_text()
            assert "Test summary" in content
            assert "Pattern 1" in content
            assert "OpenHands" in content


class TestInsightToolDefinition:
    """Tests for InsightTool definition."""

    def test_tool_name(self):
        """Test InsightTool has correct name."""
        assert InsightTool.name == "insight"

    def test_tool_registration(self):
        """Test InsightTool is registered."""
        from openhands.sdk.tool.registry import list_registered_tools

        # Tool should be registered after import
        registered_tools = list_registered_tools()
        assert "insight" in registered_tools


class TestInsightToolIntegration:
    """Integration tests for InsightTool."""

    def test_full_workflow_no_sessions(self):
        """Test full insight workflow with no sessions."""
        with tempfile.TemporaryDirectory() as temp_dir:
            from openhands.sdk.io import LocalFileStore

            file_store = LocalFileStore(root=temp_dir)
            executor = InsightExecutor(file_store=file_store)

            action = InsightAction(
                generate_html=False,
                suggest_skills=True,
                max_sessions=10,
            )

            result = executor(action)

            assert isinstance(result, InsightObservation)
            assert result.sessions_analyzed == 0

    def test_full_workflow_with_mock_sessions(self):
        """Test full insight workflow with mock session data."""
        with tempfile.TemporaryDirectory() as temp_dir:
            from openhands.sdk.io import LocalFileStore

            file_store = LocalFileStore(root=temp_dir)
            executor = InsightExecutor(file_store=file_store)

            # Mock the session collection
            mock_sessions = [
                {
                    "session_id": "test-session",
                    "event_count": 5,
                    "action_counts": {"BashAction": 3},
                    "tool_usage": {},
                    "error_count": 0,
                }
            ]

            with patch.object(
                executor, "_collect_sessions", return_value=mock_sessions
            ):
                action = InsightAction(generate_html=False, suggest_skills=True)
                result = executor(action)

            assert result.sessions_analyzed == 1
            assert len(result.common_patterns) > 0
