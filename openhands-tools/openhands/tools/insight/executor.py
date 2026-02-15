"""Executor for Insight tool."""

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.io import FileStore
from openhands.sdk.logger import get_logger
from openhands.sdk.tool import ToolExecutor
from openhands.tools.insight.definition import (
    InsightAction,
    InsightObservation,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation

logger = get_logger(__name__)


class InsightExecutor(ToolExecutor[InsightAction, InsightObservation]):
    """Executor for analyzing sessions and generating insights.

    This executor scans conversation history to identify usage patterns,
    bottlenecks, and optimization opportunities.
    """

    def __init__(
        self,
        file_store: FileStore,
        llm_model: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
    ):
        """Initialize Insight executor.

        Args:
            file_store: File store for accessing conversation data
            llm_model: LLM model to use for analysis (optional)
            api_key: API key for LLM (optional)
            api_base: Base URL for LLM (optional)
        """
        self.file_store: FileStore = file_store
        self.llm_model: str | None = llm_model
        self.api_key: str | None = api_key
        self.api_base: str | None = api_base
        self.conversations_dir: str = "conversations"
        self.usage_data_dir: str = "usage-data"

    def __call__(
        self,
        action: InsightAction,
        conversation: "BaseConversation | None" = None,  # noqa: ARG002
    ) -> InsightObservation:
        """Execute insight analysis.

        Args:
            action: The insight action with configuration
            conversation: Conversation context (unused, for interface compatibility)

        Returns:
            InsightObservation with analysis results
        """
        logger.info("Starting session insight analysis")

        try:
            # Collect session data
            sessions_data = self._collect_sessions(action.max_sessions)

            if not sessions_data:
                return InsightObservation(
                    summary="No conversation sessions found to analyze.",
                    sessions_analyzed=0,
                )

            # Analyze patterns
            analysis = self._analyze_sessions(sessions_data)

            # Generate HTML report if requested
            report_path = None
            if action.generate_html:
                report_path = self._generate_html_report(analysis, sessions_data)

            # Generate skill suggestions if requested
            suggestions = []
            if action.suggest_skills:
                suggestions = self._generate_suggestions(analysis)

            logger.info(
                f"Insight analysis complete: {len(sessions_data)} sessions analyzed"
            )

            return InsightObservation(
                summary=analysis.get("summary", ""),
                sessions_analyzed=len(sessions_data),
                common_patterns=analysis.get("patterns", []),
                bottlenecks=analysis.get("bottlenecks", []),
                suggestions=suggestions,
                report_path=report_path,
            )

        except Exception as e:
            logger.error(f"Error during insight analysis: {e}")
            return InsightObservation(
                summary=f"Error during analysis: {str(e)}",
                sessions_analyzed=0,
            )

    def _collect_sessions(self, max_sessions: int) -> list[dict[str, Any]]:
        """Collect session data from conversation history.

        Args:
            max_sessions: Maximum number of sessions to collect

        Returns:
            List of session data dictionaries
        """
        sessions = []

        try:
            session_paths = self.file_store.list(self.conversations_dir)
            all_sessions = [
                Path(path).name
                for path in session_paths
                if not Path(path).name.startswith(".")
            ]

            # Sort by modification time (most recent first)
            all_sessions = all_sessions[:max_sessions]

            for session_id in all_sessions:
                session_data = self._extract_session_data(session_id)
                if session_data:
                    sessions.append(session_data)

        except Exception as e:
            logger.warning(f"Error collecting sessions: {e}")

        return sessions

    def _extract_session_data(self, session_id: str) -> dict[str, Any] | None:
        """Extract data from a single session.

        Args:
            session_id: The session ID to extract

        Returns:
            Dictionary with session data or None if extraction fails
        """
        try:
            events_dir = f"{self.conversations_dir}/{session_id}/events"

            # First check if there are any event files
            try:
                event_files = self.file_store.list(events_dir)
                # Filter out non-event files like .eventlog.lock
                event_files = [
                    f for f in event_files
                    if f.endswith(".json") and "event-" in f
                ]
                if not event_files:
                    return None
            except Exception:
                return None

            # Count event types
            action_counts: Counter[str] = Counter()
            error_count = 0
            tool_usage: Counter[str] = Counter()
            event_count = 0
            first_timestamp = None
            last_timestamp = None

            # Try to load and iterate events, handling deserialization errors
            # (older sessions may have incompatible schemas)
            try:
                events = EventLog(self.file_store, events_dir)
                for event in events:
                    try:
                        event_count += 1
                        if first_timestamp is None:
                            first_timestamp = getattr(event, "timestamp", None)
                        last_timestamp = getattr(event, "timestamp", None)

                        if isinstance(event, ActionEvent):
                            if event.action:
                                action_type = type(event.action).__name__
                                action_counts[action_type] += 1
                                # Track tool usage
                                if hasattr(event.action, "tool_name"):
                                    tool_usage[event.action.tool_name] += 1
                        elif isinstance(event, ObservationEvent):
                            if event.observation:
                                # Check for errors
                                obs_text = getattr(event.observation, "text", "")
                                if "error" in obs_text.lower():
                                    error_count += 1
                    except Exception:
                        # Skip individual events that fail to parse
                        event_count += 1
                        continue
            except Exception:
                # EventLog iteration failed (schema incompatibility)
                # Use file count as fallback
                pass

            # If we couldn't parse any events, use file count as estimate
            if event_count == 0:
                event_count = len(event_files)

            if event_count == 0:
                return None

            return {
                "session_id": session_id,
                "event_count": event_count,
                "action_counts": dict(action_counts),
                "tool_usage": dict(tool_usage),
                "error_count": error_count,
                "start_time": first_timestamp,
                "end_time": last_timestamp,
            }

        except Exception as e:
            logger.debug(f"Failed to extract session {session_id}: {e}")
            return None

    def _analyze_sessions(
        self, sessions_data: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Analyze collected session data.

        Args:
            sessions_data: List of session data dictionaries

        Returns:
            Analysis results dictionary
        """
        # Aggregate statistics
        total_events = sum(s.get("event_count", 0) for s in sessions_data)
        total_errors = sum(s.get("error_count", 0) for s in sessions_data)

        # Aggregate action types
        all_actions: Counter[str] = Counter()
        all_tools: Counter[str] = Counter()

        for session in sessions_data:
            all_actions.update(session.get("action_counts", {}))
            all_tools.update(session.get("tool_usage", {}))

        # Identify patterns
        patterns = []
        most_common_actions = all_actions.most_common(5)
        if most_common_actions:
            patterns.append(
                f"Most used actions: {', '.join(a[0] for a in most_common_actions)}"
            )

        most_common_tools = all_tools.most_common(5)
        if most_common_tools:
            patterns.append(
                f"Most used tools: {', '.join(t[0] for t in most_common_tools)}"
            )

        # Calculate average session length
        avg_events = total_events / len(sessions_data) if sessions_data else 0
        patterns.append(f"Average events per session: {avg_events:.1f}")

        # Identify bottlenecks
        bottlenecks = []
        error_rate = total_errors / total_events if total_events > 0 else 0
        if error_rate > 0.1:
            bottlenecks.append(
                f"High error rate detected: {error_rate:.1%} of operations"
            )

        # Check for repetitive patterns
        for action, count in most_common_actions:
            if count > len(sessions_data) * 3:
                bottlenecks.append(
                    f"Repetitive action pattern: '{action}' used {count} times"
                )

        # Generate summary
        summary = (
            f"Analyzed {len(sessions_data)} sessions with {total_events} total events. "
            f"Found {len(patterns)} usage patterns and {len(bottlenecks)} potential "
            f"optimization opportunities."
        )

        return {
            "summary": summary,
            "patterns": patterns,
            "bottlenecks": bottlenecks,
            "total_events": total_events,
            "total_errors": total_errors,
            "action_counts": dict(all_actions),
            "tool_usage": dict(all_tools),
        }

    def _generate_suggestions(
        self, analysis: dict[str, Any]
    ) -> list[str]:
        """Generate optimization suggestions based on analysis.

        Args:
            analysis: Analysis results dictionary

        Returns:
            List of suggestion strings
        """
        suggestions = []

        # Suggest based on error rate
        total_events = analysis.get("total_events", 0)
        total_errors = analysis.get("total_errors", 0)
        if total_events > 0 and total_errors / total_events > 0.1:
            suggestions.append(
                "Consider creating error-handling skills for common failure patterns"
            )

        # Suggest based on repetitive actions
        action_counts = analysis.get("action_counts", {})
        for action, count in action_counts.items():
            if count > 20:
                suggestions.append(
                    f"Create a custom skill to automate '{action}' workflows"
                )

        # Suggest based on tool usage
        tool_usage = analysis.get("tool_usage", {})
        if len(tool_usage) < 3:
            suggestions.append(
                "Explore additional tools to expand capabilities"
            )

        # Default suggestion if none found
        if not suggestions:
            suggestions.append(
                "Your usage patterns look efficient! "
                "Consider documenting your workflows as skills for sharing."
            )

        return suggestions[:5]  # Limit to top 5 suggestions

    def _generate_html_report(
        self,
        analysis: dict[str, Any],
        sessions_data: list[dict[str, Any]],
    ) -> str | None:
        """Generate an HTML dashboard report.

        Args:
            analysis: Analysis results dictionary
            sessions_data: Raw session data

        Returns:
            Path to generated HTML report or None if generation fails
        """
        try:
            # Ensure usage-data directory exists
            report_dir = Path.home() / ".openhands" / self.usage_data_dir
            report_dir.mkdir(parents=True, exist_ok=True)

            # Generate report filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_filename = f"insight_report_{timestamp}.html"
            report_path = report_dir / report_filename

            # Generate HTML content
            html_content = self._build_html_report(analysis, sessions_data)

            # Write report
            report_path.write_text(html_content)

            logger.info(f"Generated HTML report: {report_path}")
            return str(report_path)

        except Exception as e:
            logger.error(f"Failed to generate HTML report: {e}")
            return None

    def _build_html_report(
        self,
        analysis: dict[str, Any],
        sessions_data: list[dict[str, Any]],
    ) -> str:
        """Build HTML content for the report.

        Args:
            analysis: Analysis results dictionary
            sessions_data: Raw session data

        Returns:
            HTML string
        """
        patterns_html = "\n".join(
            f"<li>{p}</li>" for p in analysis.get("patterns", [])
        )
        bottlenecks_html = "\n".join(
            f"<li>{b}</li>" for b in analysis.get("bottlenecks", [])
        )

        # Build action chart data
        action_counts = analysis.get("action_counts", {})
        chart_labels = json.dumps(list(action_counts.keys())[:10])
        chart_data = json.dumps(list(action_counts.values())[:10])

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OpenHands Session Insight Report</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        .card {{
            background: white;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        h1 {{ color: #333; }}
        h2 {{ color: #666; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        .stat {{
            display: inline-block;
            padding: 15px 25px;
            margin: 10px;
            background: #4CAF50;
            color: white;
            border-radius: 8px;
            text-align: center;
        }}
        .stat-number {{ font-size: 2em; font-weight: bold; }}
        .stat-label {{ font-size: 0.9em; opacity: 0.9; }}
        ul {{ padding-left: 20px; }}
        li {{ margin: 8px 0; }}
        .chart-container {{ max-width: 600px; margin: 20px auto; }}
        .timestamp {{ color: #999; font-size: 0.9em; }}
    </style>
</head>
<body>
    <h1>OpenHands Session Insight Report</h1>
    <p class="timestamp">Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>

    <div class="card">
        <h2>Summary</h2>
        <p>{analysis.get("summary", "")}</p>
        <div>
            <div class="stat">
                <div class="stat-number">{len(sessions_data)}</div>
                <div class="stat-label">Sessions Analyzed</div>
            </div>
            <div class="stat">
                <div class="stat-number">{analysis.get("total_events", 0)}</div>
                <div class="stat-label">Total Events</div>
            </div>
            <div class="stat">
                <div class="stat-number">{analysis.get("total_errors", 0)}</div>
                <div class="stat-label">Errors Detected</div>
            </div>
        </div>
    </div>

    <div class="card">
        <h2>Action Usage</h2>
        <div class="chart-container">
            <canvas id="actionChart"></canvas>
        </div>
    </div>

    <div class="card">
        <h2>Usage Patterns</h2>
        <ul>{patterns_html or "<li>No patterns identified</li>"}</ul>
    </div>

    <div class="card">
        <h2>Identified Bottlenecks</h2>
        <ul>{bottlenecks_html or "<li>No bottlenecks identified</li>"}</ul>
    </div>

    <script>
        const ctx = document.getElementById('actionChart').getContext('2d');
        new Chart(ctx, {{
            type: 'bar',
            data: {{
                labels: {chart_labels},
                datasets: [{{
                    label: 'Action Count',
                    data: {chart_data},
                    backgroundColor: 'rgba(76, 175, 80, 0.6)',
                    borderColor: 'rgba(76, 175, 80, 1)',
                    borderWidth: 1
                }}]
            }},
            options: {{
                responsive: true,
                plugins: {{
                    legend: {{ display: false }}
                }},
                scales: {{
                    y: {{ beginAtZero: true }}
                }}
            }}
        }});
    </script>
</body>
</html>"""

        return html
