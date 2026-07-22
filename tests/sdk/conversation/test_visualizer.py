"""Tests for the conversation visualizer and event visualization."""

import io
import json
import re
import sys
from collections.abc import Sequence
from typing import IO, TYPE_CHECKING, Self, cast
from unittest.mock import MagicMock

from pydantic import Field
from rich.text import Text

from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.conversation.visualizer import (
    DefaultConversationVisualizer,
)
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    CondensationRequest,
    ConversationStateUpdateEvent,
    MessageEvent,
    ObservationEvent,
    PauseEvent,
    SystemPromptEvent,
    UserRejectObservation,
)
from openhands.sdk.event.base import Event
from openhands.sdk.event.types import SourceType
from openhands.sdk.llm import (
    Message,
    MessageToolCall,
    ReasoningItemModel,
    TextContent,
)
from openhands.sdk.llm.utils.metrics import Metrics
from openhands.sdk.tool import Action, Observation, ToolDefinition, ToolExecutor


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation


class _UnknownEventForVisualizerTest(Event):
    """Unknown event type for testing fallback visualization.

    This class is defined at module level (rather than inside a test function) to
    ensure it's importable by Pydantic during serialization/deserialization.
    Defining it inside a test function causes test pollution when running tests
    in parallel with pytest-xdist.
    """

    source: SourceType = "agent"


class _Cp1252Stdout:
    """Minimal stream that reproduces legacy Windows cp1252 stdout encoding."""

    encoding = "cp1252"

    def __init__(self) -> None:
        self._buffer = io.StringIO()

    def fileno(self) -> int:
        return 1

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True

    def write(self, text: str) -> int:
        text.encode(self.encoding)
        return self._buffer.write(text)

    def getvalue(self) -> str:
        return self._buffer.getvalue()


class VisualizerMockAction(Action):
    """Mock action for testing."""

    command: str = "test command"
    working_dir: str = "/tmp"


class VisualizerCustomAction(Action):
    """Custom action with overridden visualize method."""

    task_list: list[dict] = Field(default_factory=list)

    @property
    def visualize(self) -> Text:
        """Custom visualization for task tracker."""
        content = Text()
        content.append("Task Tracker Action\n", style="bold")
        content.append(f"Tasks: {len(self.task_list)}")
        for i, task in enumerate(self.task_list):
            content.append(f"\n  {i + 1}. {task.get('title', 'Untitled')}")
        return content


class VisualizerMockObservation(Observation):
    """Mock observation for testing."""

    pass


class VisualizerMockExecutor(ToolExecutor):
    """Mock executor for testing."""

    def __call__(
        self,
        action: VisualizerMockAction,
        conversation: "LocalConversation | None" = None,
    ) -> VisualizerMockObservation:
        return VisualizerMockObservation.from_text("test")


class VisualizerMockTool(
    ToolDefinition[VisualizerMockAction, VisualizerMockObservation]
):
    """Mock tool for testing."""

    @classmethod
    def create(cls, *args, **kwargs) -> Sequence[Self]:
        return [
            cls(
                description="A test tool for demonstration",
                action_type=VisualizerMockAction,
                observation_type=VisualizerMockObservation,
                executor=VisualizerMockExecutor(),
            )
        ]


def create_tool_call(
    call_id: str, function_name: str, arguments: dict
) -> MessageToolCall:
    """Helper to create a MessageToolCall."""
    return MessageToolCall(
        id=call_id,
        name=function_name,
        arguments=json.dumps(arguments),
        origin="completion",
    )


def test_action_base_visualize():
    """Test that Action has a visualize property."""
    action = VisualizerMockAction(command="echo hello", working_dir="/home")

    result = action.visualize
    assert isinstance(result, Text)

    # Check that it contains action name and fields
    text_content = result.plain
    assert "VisualizerMockAction" in text_content
    assert "command" in text_content
    assert "echo hello" in text_content
    assert "working_dir" in text_content
    assert "/home" in text_content


def test_custom_action_visualize():
    """Test that custom actions can override visualize method."""
    tasks = [
        {"title": "Task 1", "status": "todo"},
        {"title": "Task 2", "status": "done"},
    ]
    action = VisualizerCustomAction(task_list=tasks)

    result = action.visualize
    assert isinstance(result, Text)

    text_content = result.plain
    assert "Task Tracker Action" in text_content
    assert "Tasks: 2" in text_content
    assert "1. Task 1" in text_content
    assert "2. Task 2" in text_content


def test_system_prompt_event_visualize():
    """Test SystemPromptEvent visualization."""
    tool = VisualizerMockTool.create()[0]

    event = SystemPromptEvent(
        system_prompt=TextContent(text="You are a helpful assistant."),
        tools=[tool],
    )

    result = event.visualize
    assert isinstance(result, Text)

    text_content = result.plain
    assert "System Prompt:" in text_content
    assert "You are a helpful assistant." in text_content
    assert "Tools Available: 1" in text_content
    assert "visualizer_mock" in text_content


def test_action_event_visualize():
    """Test ActionEvent visualization."""
    action = VisualizerMockAction(command="ls -la", working_dir="/tmp")
    tool_call = create_tool_call("call_123", "terminal", {"command": "ls -la"})
    event = ActionEvent(
        thought=[TextContent(text="I need to list files")],
        reasoning_content="Let me check the directory contents",
        action=action,
        tool_name="terminal",
        tool_call_id="call_123",
        tool_call=tool_call,
        llm_response_id="response_456",
    )

    result = event.visualize
    assert isinstance(result, Text)

    text_content = result.plain
    assert "Reasoning:" in text_content
    assert "Let me check the directory contents" in text_content
    assert "Thought:" in text_content
    assert "I need to list files" in text_content
    assert "VisualizerMockAction" in text_content


def test_action_event_visualize_omits_empty_responses_reasoning_label():
    """Empty Responses reasoning items should not render a reasoning heading."""
    action = VisualizerMockAction(command="ls -la", working_dir="/tmp")
    tool_call = create_tool_call("call_123", "terminal", {"command": "ls -la"})
    event = ActionEvent(
        thought=[],
        responses_reasoning_item=ReasoningItemModel(),
        action=action,
        tool_name="terminal",
        tool_call_id="call_123",
        tool_call=tool_call,
        llm_response_id="response_456",
    )

    text_content = event.visualize.plain

    assert "Reasoning:" not in text_content
    assert "ls -la" in text_content


def test_action_event_visualize_renders_responses_reasoning_content():
    """Responses reasoning plaintext should still render when present."""
    action = VisualizerMockAction(command="ls -la", working_dir="/tmp")
    tool_call = create_tool_call("call_123", "terminal", {"command": "ls -la"})
    event = ActionEvent(
        thought=[],
        responses_reasoning_item=ReasoningItemModel(
            summary=["Check the directory first"]
        ),
        action=action,
        tool_name="terminal",
        tool_call_id="call_123",
        tool_call=tool_call,
        llm_response_id="response_456",
    )

    text_content = event.visualize.plain

    assert "Reasoning:" in text_content
    assert "Check the directory first" in text_content
    assert "ls -la" in text_content


def test_observation_event_visualize():
    """Test ObservationEvent visualization."""
    observation = VisualizerMockObservation(
        content=[TextContent(text="total 4\ndrwxr-xr-x 2 user user 4096 Jan 1 12:00 .")]
    )
    event = ObservationEvent(
        observation=observation,
        action_id="action_123",
        tool_name="terminal",
        tool_call_id="call_123",
    )

    result = event.visualize
    assert isinstance(result, Text)

    text_content = result.plain
    assert "Tool: terminal" in text_content
    assert "Result:" in text_content
    assert "total 4" in text_content


def test_message_event_visualize():
    """Test MessageEvent visualization."""
    message = Message(
        role="user",
        content=[TextContent(text="Hello, how can you help me?")],
    )
    event = MessageEvent(
        source="user",
        llm_message=message,
        activated_skills=["helper", "analyzer"],
        extended_content=[TextContent(text="Additional context")],
    )

    result = event.visualize
    assert isinstance(result, Text)

    text_content = result.plain
    assert "Hello, how can you help me?" in text_content
    assert "Activated Skills: helper, analyzer" in text_content
    assert "Prompt Extension based on Agent Context:" in text_content
    assert "Additional context" in text_content


def test_message_event_visualize_omits_empty_responses_reasoning_label():
    """Empty Responses reasoning items should not render a reasoning heading."""
    message = Message(
        role="assistant",
        content=[TextContent(text="Hello, how can you help me?")],
        responses_reasoning_item=ReasoningItemModel(),
    )
    event = MessageEvent(source="agent", llm_message=message)

    text_content = event.visualize.plain

    assert "Reasoning:" not in text_content
    assert "Hello, how can you help me?" in text_content


def test_agent_error_event_visualize():
    """Test AgentErrorEvent visualization."""
    event = AgentErrorEvent(
        error="Failed to execute command: permission denied",
        tool_call_id="call_err_1",
        tool_name="terminal",
    )

    result = event.visualize
    assert isinstance(result, Text)

    text_content = result.plain
    assert "Error Details:" in text_content
    assert "Failed to execute command: permission denied" in text_content


def test_pause_event_visualize():
    """Test PauseEvent visualization."""
    event = PauseEvent()

    result = event.visualize
    assert isinstance(result, Text)

    text_content = result.plain
    assert "Conversation Paused" in text_content


def test_conversation_visualizer_initialization():
    """Test DefaultConversationVisualizer can be initialized."""
    visualizer = DefaultConversationVisualizer()
    assert visualizer is not None
    assert hasattr(visualizer, "on_event")
    assert hasattr(visualizer, "_create_event_block")


def test_default_visualizer_handles_unicode_on_legacy_windows_stdout(monkeypatch):
    """Visualizer output should not fail on legacy Windows stdout."""
    stream = _Cp1252Stdout()
    monkeypatch.setattr(sys, "stdout", cast(IO[str], stream))

    visualizer = DefaultConversationVisualizer()
    event = MessageEvent(
        source="agent",
        llm_message=Message(
            role="assistant",
            content=[TextContent(text="\U0001f510 Security Policy")],
        ),
    )

    visualizer.on_event(event)

    assert "Security Policy" in stream.getvalue()


def test_visualizer_event_panel_creation():
    """Test that visualizer creates event blocks for different event types."""
    from rich.console import Group

    conv_viz = DefaultConversationVisualizer()

    # Test with a simple action event
    action = VisualizerMockAction(command="test")
    tool_call = create_tool_call("call_1", "test", {})
    action_event = ActionEvent(
        thought=[TextContent(text="Testing")],
        action=action,
        tool_name="test",
        tool_call_id="call_1",
        tool_call=tool_call,
        llm_response_id="response_1",
    )
    block = conv_viz._create_event_block(action_event)
    assert block is not None
    assert isinstance(block, Group)


def test_visualizer_action_event_with_none_action_panel():
    """ActionEvent with action=None should render as 'Agent Action (Not Executed)'."""
    import re

    from rich.console import Console

    visualizer = DefaultConversationVisualizer()
    tc = create_tool_call("call_ne_1", "missing_fn", {})
    action_event = ActionEvent(
        thought=[TextContent(text="...")],
        tool_call=tc,
        tool_name=tc.name,
        tool_call_id=tc.id,
        llm_response_id="resp_viz_1",
        action=None,
    )
    block = visualizer._create_event_block(action_event)
    assert block is not None

    # Render block to string to check content
    console = Console()
    with console.capture() as capture:
        console.print(block)
    output = capture.get()

    # Strip ANSI codes for text comparison
    ansi_escape = re.compile(r"\x1b\[[0-9;]*m")
    plain_output = ansi_escape.sub("", output)

    # Ensure it doesn't fall back to UNKNOWN
    assert "UNKNOWN Event" not in plain_output
    # And uses the 'Agent Action (Not Executed)' title
    assert "Agent Action (Not Executed)" in plain_output


def test_visualizer_user_reject_observation_panel():
    """UserRejectObservation should render a dedicated event block."""
    from rich.console import Console

    visualizer = DefaultConversationVisualizer()
    event = UserRejectObservation(
        tool_name="demo_tool",
        tool_call_id="fc_call_1",
        action_id="action_1",
        rejection_reason="User rejected the proposed action.",
    )

    block = visualizer._create_event_block(event)
    assert block is not None

    # Render block to string to check content
    console = Console()
    with console.capture() as capture:
        console.print(block)
    output = capture.get()

    assert "UNKNOWN Event" not in output
    assert "User Rejected Action" in output
    # ensure the reason is part of the rendered text
    assert "User rejected the proposed action." in output


def test_visualizer_condensation_request_panel():
    """CondensationRequest renders system-styled event block with friendly text."""
    from rich.console import Console

    visualizer = DefaultConversationVisualizer()
    event = CondensationRequest()
    block = visualizer._create_event_block(event)
    assert block is not None

    # Render block to string to check content
    console = Console()
    with console.capture() as capture:
        console.print(block)
    output = capture.get()

    # Should not fall back to UNKNOWN
    assert "UNKNOWN Event" not in output
    # Title should indicate condensation request
    assert "Condensation Request" in output
    # Body should be the friendly visualize text
    assert "Conversation Condensation Requested" in output
    assert "condensation of the conversation history" in output


def test_metrics_formatting():
    """Test metrics subtitle formatting."""
    from unittest.mock import MagicMock

    from openhands.sdk.conversation.conversation_stats import ConversationStats
    from openhands.sdk.llm.utils.metrics import Metrics

    # Create conversation stats with metrics
    conversation_stats = ConversationStats()

    # Create metrics and add to conversation stats
    metrics = Metrics(model_name="test-model")
    metrics.add_cost(0.0234)
    metrics.add_token_usage(
        prompt_tokens=1500,
        completion_tokens=500,
        cache_read_tokens=300,
        cache_write_tokens=0,
        reasoning_tokens=200,
        context_window=8000,
        response_id="test_response",
    )

    # Add metrics to conversation stats
    conversation_stats.usage_to_metrics["test_usage"] = metrics

    # Create visualizer and initialize with mock state
    visualizer = DefaultConversationVisualizer()
    mock_state = MagicMock()
    mock_state.stats = conversation_stats
    visualizer.initialize(mock_state)

    # Test the metrics subtitle formatting
    subtitle = visualizer._format_metrics_subtitle()
    assert subtitle is not None
    assert "1.5K" in subtitle  # Input tokens abbreviated (trailing zeros removed)
    assert "500" in subtitle  # Output tokens
    assert "20.00%" in subtitle  # Cache hit rate
    assert "200" in subtitle  # Reasoning tokens
    assert "0.0234" in subtitle  # Cost


def test_metrics_subtitle_caps_cache_rate_when_cache_exceeds_prompt():
    """Regression for #3044: ACP reports input_tokens excluding cached reads,
    so cache_read_tokens can exceed prompt_tokens. The rendered cache hit
    rate must stay within [0, 100]%."""
    stats = ConversationStats()
    metrics = Metrics(model_name="test-model")
    # Numbers reproduced from the issue: 13 input + ~117,654 cached previously
    # rendered as "cache hit 905030.77%".
    metrics.add_token_usage(
        prompt_tokens=13,
        completion_tokens=568,
        cache_read_tokens=117_654,
        cache_write_tokens=0,
        reasoning_tokens=0,
        context_window=200_000,
        response_id="acp_response",
    )
    stats.usage_to_metrics["acp_usage"] = metrics

    visualizer = DefaultConversationVisualizer()
    mock_state = MagicMock()
    mock_state.stats = stats
    visualizer.initialize(mock_state)

    subtitle = visualizer._format_metrics_subtitle()
    assert subtitle is not None
    match = re.search(r"cache hit (?:\(total )?([\d.]+)%", subtitle)
    assert match, subtitle
    rate = float(match.group(1))
    assert 0.0 <= rate <= 100.0, f"cache hit rate {rate} outside [0, 100]"


def test_metrics_abbreviation_formatting():
    """Test number abbreviation with various edge cases."""
    from unittest.mock import MagicMock

    from openhands.sdk.conversation.conversation_stats import ConversationStats
    from openhands.sdk.llm.utils.metrics import Metrics

    test_cases = [
        # (input_tokens, expected_abbr)
        (999, "999"),  # Below threshold
        (1000, "1K"),  # Exact K boundary, trailing zeros removed
        (1500, "1.5K"),  # K with one decimal, trailing zero removed
        (89080, "89.08K"),  # K with two decimals (regression test for bug)
        (89000, "89K"),  # K with trailing zeros removed
        (1000000, "1M"),  # Exact M boundary
        (1234567, "1.23M"),  # M with decimals
        (1000000000, "1B"),  # Exact B boundary
    ]

    for tokens, expected in test_cases:
        stats = ConversationStats()
        metrics = Metrics(model_name="test-model")
        metrics.add_token_usage(
            prompt_tokens=tokens,
            completion_tokens=100,
            cache_read_tokens=0,
            cache_write_tokens=0,
            reasoning_tokens=0,
            context_window=8000,
            response_id="test",
        )
        stats.usage_to_metrics["test"] = metrics

        visualizer = DefaultConversationVisualizer()
        mock_state = MagicMock()
        mock_state.stats = stats
        visualizer.initialize(mock_state)
        subtitle = visualizer._format_metrics_subtitle()

        assert subtitle is not None, f"Failed for {tokens}"
        assert expected in subtitle, (
            f"Expected '{expected}' in subtitle for {tokens}, got: {subtitle}"
        )


def test_metrics_subtitle_shows_per_request_and_cumulative():
    """Two sequential requests on the same LLM: the subtitle for the latest
    event should show both the per-request usage and the running cumulative
    total, not just the total (#4105)."""
    stats = ConversationStats()
    metrics = Metrics(model_name="test-model")
    metrics.add_token_usage(
        prompt_tokens=4390,
        completion_tokens=144,
        cache_read_tokens=0,
        cache_write_tokens=0,
        reasoning_tokens=0,
        context_window=8000,
        response_id="response_1",
    )
    metrics.add_token_usage(
        prompt_tokens=4630,
        completion_tokens=114,
        cache_read_tokens=0,
        cache_write_tokens=0,
        reasoning_tokens=0,
        context_window=8000,
        response_id="response_2",
    )
    stats.usage_to_metrics["agent"] = metrics

    visualizer = DefaultConversationVisualizer()
    mock_state = MagicMock()
    mock_state.stats = stats
    visualizer.initialize(mock_state)

    tool_call = create_tool_call("call_1", "test", {})
    event = ActionEvent(
        thought=[TextContent(text="Testing")],
        action=VisualizerMockAction(command="test"),
        tool_name="test",
        tool_call_id="call_1",
        tool_call=tool_call,
        llm_response_id="response_2",
    )

    subtitle = visualizer._format_metrics_subtitle(event)
    # Full-string assertion pins down which number is per-request vs.
    # cumulative -- individual `in subtitle` checks can't tell "input 4.63K
    # (total 9.02K)" apart from the numbers swapped.
    assert subtitle == (
        "Tokens: [cyan]↑ input 4.63K (total 9.02K)[/cyan] • "
        "[magenta]cache hit 0.00% (total 0.00%)[/magenta] • "
        "[blue]↓ output 114 (total 258)[/blue] • "
        "[green]$ (total 0.00)[/green]"
    )


def test_metrics_subtitle_reasoning_and_cache_hit_are_per_request():
    """Regression: reasoning tokens and cache hit rate must get the same
    per-request "X (total Y)" treatment as input/output tokens. Showing the
    cumulative reasoning count unlabeled next to per-request input/output
    numbers reads as per-request too, which is the exact confusion #4105 set
    out to fix."""
    stats = ConversationStats()
    metrics = Metrics(model_name="test-model")
    metrics.add_token_usage(
        prompt_tokens=1000,
        completion_tokens=100,
        cache_read_tokens=100,
        cache_write_tokens=0,
        reasoning_tokens=200,
        context_window=8000,
        response_id="response_1",
    )
    metrics.add_token_usage(
        prompt_tokens=2000,
        completion_tokens=200,
        cache_read_tokens=1000,
        cache_write_tokens=0,
        reasoning_tokens=50,
        context_window=8000,
        response_id="response_2",
    )
    stats.usage_to_metrics["agent"] = metrics

    visualizer = DefaultConversationVisualizer()
    mock_state = MagicMock()
    mock_state.stats = stats
    visualizer.initialize(mock_state)

    tool_call = create_tool_call("call_1", "test", {})
    event = ActionEvent(
        thought=[TextContent(text="Testing")],
        action=VisualizerMockAction(command="test"),
        tool_name="test",
        tool_call_id="call_1",
        tool_call=tool_call,
        llm_response_id="response_2",
    )

    subtitle = visualizer._format_metrics_subtitle(event)
    assert subtitle is not None
    # Per-request reasoning (response_2 only: 50), not the cumulative 250.
    assert "reasoning 50 (total 250)" in subtitle
    # Per-request cache hit rate (1000 / 2000), not the cumulative rate.
    assert "cache hit 50.00% (total 36.67%)" in subtitle


def test_metrics_subtitle_matches_by_response_id_not_last_index():
    """Regression: get_combined_metrics().token_usages[-1] merges usages
    across every usage id (agent, condenser, ...) and is not chronological
    across them. Per-request usage must be looked up by response_id, which
    telemetry sets to the same id events carry as llm_response_id (#4105)."""
    stats = ConversationStats()

    # Condenser usage id is inserted first...
    condenser_metrics = Metrics(model_name="test-model")
    condenser_metrics.add_token_usage(
        prompt_tokens=2000,
        completion_tokens=50,
        cache_read_tokens=0,
        cache_write_tokens=0,
        reasoning_tokens=0,
        context_window=8000,
        response_id="condenser_response",
    )
    stats.usage_to_metrics["condenser"] = condenser_metrics

    # ...then the agent usage id, which lands last in the merged list.
    agent_metrics = Metrics(model_name="test-model")
    agent_metrics.add_token_usage(
        prompt_tokens=999,
        completion_tokens=9,
        cache_read_tokens=0,
        cache_write_tokens=0,
        reasoning_tokens=0,
        context_window=8000,
        response_id="agent_response",
    )
    stats.usage_to_metrics["agent"] = agent_metrics

    # Sanity check: naive token_usages[-1] would return the agent usage, not
    # the condenser usage this test looks up.
    combined = stats.get_combined_metrics()
    assert combined.token_usages[-1].response_id == "agent_response"

    visualizer = DefaultConversationVisualizer()
    mock_state = MagicMock()
    mock_state.stats = stats
    visualizer.initialize(mock_state)

    tool_call = create_tool_call("call_1", "test", {})
    event = ActionEvent(
        thought=[TextContent(text="Testing")],
        action=VisualizerMockAction(command="test"),
        tool_name="test",
        tool_call_id="call_1",
        tool_call=tool_call,
        llm_response_id="condenser_response",
    )

    request_usage = visualizer._find_request_usage(event)
    assert request_usage is not None
    assert request_usage.prompt_tokens == 2000
    assert request_usage.completion_tokens == 50


def test_metrics_subtitle_fallback_labels_totals():
    """No event, or an event whose response_id is unknown (e.g. the ACP path,
    where response ids are per-session), must label every number "(total)":
    bare numbers next to per-request events read as per-request, which is the
    confusion #4105 set out to fix."""
    stats = ConversationStats()
    metrics = Metrics(model_name="test-model")
    metrics.add_token_usage(
        prompt_tokens=1000,
        completion_tokens=100,
        cache_read_tokens=0,
        cache_write_tokens=0,
        reasoning_tokens=0,
        context_window=8000,
        response_id="response_1",
    )
    stats.usage_to_metrics["agent"] = metrics

    visualizer = DefaultConversationVisualizer()
    mock_state = MagicMock()
    mock_state.stats = stats
    visualizer.initialize(mock_state)

    expected = (
        "Tokens: [cyan]↑ input (total 1K)[/cyan] • "
        "[magenta]cache hit (total 0.00%)[/magenta] • "
        "[blue]↓ output (total 100)[/blue] • "
        "[green]$ (total 0.00)[/green]"
    )

    # No event at all.
    assert visualizer._format_metrics_subtitle() == expected

    # Event carries a response_id that doesn't match any recorded usage.
    tool_call = create_tool_call("call_1", "test", {})
    event = ActionEvent(
        thought=[TextContent(text="Testing")],
        action=VisualizerMockAction(command="test"),
        tool_name="test",
        tool_call_id="call_1",
        tool_call=tool_call,
        llm_response_id="unknown_response",
    )
    assert visualizer._format_metrics_subtitle(event) == expected


def test_metrics_subtitle_user_message_labels_totals():
    """User MessageEvents carry no llm_response_id, so their subtitle can only
    show running totals -- and must say so instead of printing bare numbers."""
    stats = ConversationStats()
    metrics = Metrics(model_name="test-model")
    metrics.add_token_usage(
        prompt_tokens=1000,
        completion_tokens=100,
        cache_read_tokens=0,
        cache_write_tokens=0,
        reasoning_tokens=0,
        context_window=8000,
        response_id="response_1",
    )
    stats.usage_to_metrics["agent"] = metrics

    visualizer = DefaultConversationVisualizer()
    mock_state = MagicMock()
    mock_state.stats = stats
    visualizer.initialize(mock_state)

    user_event = MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="Hello")]),
    )
    assert visualizer._format_metrics_subtitle(user_event) == (
        "Tokens: [cyan]↑ input (total 1K)[/cyan] • "
        "[magenta]cache hit (total 0.00%)[/magenta] • "
        "[blue]↓ output (total 100)[/blue] • "
        "[green]$ (total 0.00)[/green]"
    )


def test_event_base_fallback_visualize():
    """Test that Event provides fallback visualization."""
    event = _UnknownEventForVisualizerTest()
    result = event.visualize
    assert isinstance(result, Text)

    text_content = result.plain
    assert "Unknown event type: _UnknownEventForVisualizerTest" in text_content


def test_conversation_error_event_visualize():
    """Test that ConversationErrorEvent provides a specific visualization."""
    from openhands.sdk.event.conversation_error import ConversationErrorEvent

    event = ConversationErrorEvent(
        source="environment",
        code="TestError",
        detail="Something went wrong",
    )
    text_content = event.visualize.plain

    assert "Unknown event type:" not in text_content
    assert "Conversation Error" in text_content
    assert "TestError" in text_content
    assert "Something went wrong" in text_content


def test_visualizer_conversation_state_update_event_skipped():
    """Test that ConversationStateUpdateEvent is not visualized."""
    visualizer = DefaultConversationVisualizer()
    event = ConversationStateUpdateEvent(key="execution_status", value="finished")

    block = visualizer._create_event_block(event)
    # Should return None to skip visualization
    assert block is None


def test_default_visualizer_create_sub_visualizer_returns_none():
    """Test that DefaultConversationVisualizer.create_sub_visualizer returns None.

    This is the expected default behavior - base visualizers don't support
    sub-agent visualization. Subclasses like DelegationVisualizer can override
    this to provide sub-agent visualizers.
    """
    visualizer = DefaultConversationVisualizer()
    result = visualizer.create_sub_visualizer("test_agent")
    assert result is None
