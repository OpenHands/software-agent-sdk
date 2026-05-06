"""Tests for ToolShieldLLMSecurityAnalyzer and toolshield helpers."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from litellm.types.utils import Choices
from litellm.types.utils import Message as LiteLLMMessage
from litellm.types.utils import ModelResponse
from pydantic import SecretStr

from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.llm.utils.metrics import MetricsSnapshot
from openhands.sdk.security.toolshield_llm_analyzer import (
    ToolShieldLLMSecurityAnalyzer,
    _format_action_for_guardrail,
)
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.tool import Action


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockAction(Action):
    command: str = "test"


def _make_action_event(
    command: str = "ls -la",
    tool_name: str = "execute_bash",
    thought: str = "Listing files to check permissions.",
    summary: str | None = "checking directory permissions",
) -> ActionEvent:
    return ActionEvent(
        thought=[TextContent(text=thought)] if thought else [],
        action=_MockAction(command=command),
        tool_name=tool_name,
        tool_call_id="call_123",
        tool_call=MessageToolCall(
            id="call_123",
            name=tool_name,
            arguments=f'{{"command": "{command}"}}',
            origin="completion",
        ),
        llm_response_id="resp_123",
        summary=summary,
    )


def _mock_llm_response(text: str) -> LLMResponse:
    """Build a minimal LLMResponse wrapping plain text content.

    ``raw_response`` must be a real ``ModelResponse`` (not a Mock) because
    Pydantic validates the field against the concrete type even with
    ``arbitrary_types_allowed=True``.
    """
    raw = ModelResponse(
        id="mock-resp-id",
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(content=text, role="assistant"),
            )
        ],
        created=int(time.time()),
        model="mock-model",
        object="chat.completion",
    )
    msg = Message(role="assistant", content=[TextContent(text=text)])
    return LLMResponse(
        message=msg,
        metrics=MetricsSnapshot(
            model_name="mock", accumulated_cost=0.0, max_budget_per_task=None
        ),
        raw_response=raw,
    )


def _make_test_llm() -> LLM:
    """Construct a real LLM instance for Pydantic validation to pass.

    The ``completion`` method will be patched per-test; we never hit the
    network.
    """
    return LLM(
        model="gpt-4o-mini",
        api_key=SecretStr("test-key-not-used"),
        service_id="test-guardrail",
    )


def _make_analyzer(
    history_window: int = 5,
    safety_experiences: str = "",
) -> ToolShieldLLMSecurityAnalyzer:
    """Create an analyzer wired to a real LLM whose completion is patched later."""
    return ToolShieldLLMSecurityAnalyzer(
        llm=_make_test_llm(),
        history_window=history_window,
        safety_experiences=safety_experiences,
    )


def _patch_completion(analyzer: ToolShieldLLMSecurityAnalyzer, response_or_side_effect):
    """Replace analyzer.llm.completion with a mock.

    Accepts either an ``LLMResponse`` (as return_value) or a callable/exception
    (as side_effect).
    """
    mock = MagicMock()
    if isinstance(response_or_side_effect, LLMResponse):
        mock.return_value = response_or_side_effect
    else:
        mock.side_effect = response_or_side_effect
    # Bypass Pydantic's __setattr__ guard -- ``completion`` is a method on LLM,
    # and Pydantic refuses direct assignment since there's no Field for it.
    object.__setattr__(analyzer.llm, "completion", mock)
    return mock


# ---------------------------------------------------------------------------
# _parse_risk
# ---------------------------------------------------------------------------


class TestParseRisk:
    """Risk-label extraction from guardrail LLM output."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("RISK: LOW\nSafe operation.", SecurityRisk.LOW),
            ("RISK: MEDIUM\nPotentially concerning.", SecurityRisk.MEDIUM),
            ("RISK: HIGH\nDestructive command.", SecurityRisk.HIGH),
            # Case insensitive
            ("risk: low\nfine", SecurityRisk.LOW),
            ("Risk: High\n", SecurityRisk.HIGH),
            # Extra whitespace
            ("  RISK:   MEDIUM  \nok", SecurityRisk.MEDIUM),
        ],
    )
    def test_parses_standalone_label(self, text, expected):
        assert ToolShieldLLMSecurityAnalyzer._parse_risk(text) == expected

    def test_inline_label_in_explanation_is_ignored(self):
        """The anchored regex must not match risk words inside prose."""
        # Old (pre-fix) regex would match the inline "RISK: LOW" first and
        # misclassify a HIGH action as LOW.
        text = (
            "RISK: HIGH\nThe agent's earlier steps appeared RISK: LOW "
            "but the current action is clearly destructive."
        )
        assert (
            ToolShieldLLMSecurityAnalyzer._parse_risk(text) == SecurityRisk.HIGH
        )

    def test_multiple_standalone_labels_takes_last(self):
        """If the LLM emits a revision, the final verdict wins."""
        text = "RISK: LOW\nOn reflection, this is more dangerous.\nRISK: HIGH"
        assert (
            ToolShieldLLMSecurityAnalyzer._parse_risk(text) == SecurityRisk.HIGH
        )

    def test_no_label_falls_back_to_high(self):
        """Conservative fallback: mangled output shouldn't allow the action."""
        assert (
            ToolShieldLLMSecurityAnalyzer._parse_risk("This looks suspicious.")
            == SecurityRisk.HIGH
        )

    def test_empty_text_falls_back_to_high(self):
        assert (
            ToolShieldLLMSecurityAnalyzer._parse_risk("") == SecurityRisk.HIGH
        )


# ---------------------------------------------------------------------------
# _format_action_for_guardrail
# ---------------------------------------------------------------------------


class TestFormatAction:
    """Action rendering must expose content the guardrail can reason about."""

    def test_includes_tool_name_and_arguments(self):
        event = _make_action_event(command="rm -rf /")
        rendered = _format_action_for_guardrail(event)
        assert "Tool: execute_bash" in rendered
        assert "rm -rf /" in rendered

    def test_includes_summary_when_present(self):
        event = _make_action_event(summary="deleting system files")
        rendered = _format_action_for_guardrail(event)
        assert "Summary: deleting system files" in rendered

    def test_includes_thought_when_nonempty(self):
        event = _make_action_event(thought="Need to clean up temp files.")
        rendered = _format_action_for_guardrail(event)
        assert "Thought: Need to clean up temp files." in rendered

    def test_omits_empty_thought(self):
        event = _make_action_event(thought="")
        rendered = _format_action_for_guardrail(event)
        assert "Thought:" not in rendered

    def test_unparsed_tool_call_fallback_uses_direct_arguments_field(self):
        """Regression: MessageToolCall.arguments is a direct field, not .function.arguments.

        Previous bug: fallback path (when action.action is None) accessed
        ``.function.arguments``, which always raised AttributeError and
        dropped us into a noisy ``str(tool_call)`` that included id/name/origin
        instead of the clean JSON args.
        """
        event = _make_action_event(command="unparsed_marker")
        # Force the fallback branch: action=None, tool_call still present
        event = event.model_copy(update={"action": None})
        rendered = _format_action_for_guardrail(event)
        # Must see the JSON args, not the Pydantic repr
        assert "unparsed_marker" in rendered
        assert "Arguments (unparsed):" in rendered
        # Noisy Pydantic repr markers shouldn't appear
        assert "id=" not in rendered
        assert "origin=" not in rendered

    def test_does_not_regress_to_event_repr(self):
        """Previous bug: used repr() which returned only id/source/timestamp."""
        event = _make_action_event(command="unique_command_marker")
        rendered = _format_action_for_guardrail(event)
        # Timestamp/ID-only repr would not contain the command
        assert "unique_command_marker" in rendered


# ---------------------------------------------------------------------------
# security_risk
# ---------------------------------------------------------------------------


class TestSecurityRisk:
    """End-to-end analyzer behavior with a mocked LLM."""

    def test_returns_low_when_guardrail_says_low(self):
        analyzer = _make_analyzer()
        _patch_completion(analyzer, _mock_llm_response(
            "RISK: LOW\nBenign command."
        ))
        result = analyzer.security_risk(_make_action_event())
        assert result == SecurityRisk.LOW

    def test_returns_medium_when_guardrail_says_medium(self):
        analyzer = _make_analyzer()
        _patch_completion(analyzer, _mock_llm_response(
            "RISK: MEDIUM\nSlightly concerning."
        ))
        assert analyzer.security_risk(_make_action_event()) == SecurityRisk.MEDIUM

    def test_returns_high_when_guardrail_says_high(self):
        analyzer = _make_analyzer()
        _patch_completion(analyzer, _mock_llm_response(
            "RISK: HIGH\nDestructive."
        ))
        assert analyzer.security_risk(_make_action_event()) == SecurityRisk.HIGH

    def test_returns_unknown_on_infrastructure_error(self):
        """Transient network/rate-limit errors must not block every action."""
        analyzer = _make_analyzer()
        _patch_completion(analyzer, RuntimeError("503 Service Unavailable"))
        assert (
            analyzer.security_risk(_make_action_event()) == SecurityRisk.UNKNOWN
        )

    def test_returns_high_on_unparseable_output(self):
        """Guardrail responded but without a parseable label."""
        analyzer = _make_analyzer()
        _patch_completion(analyzer, _mock_llm_response(
            "I'm not sure what to do."
        ))
        assert analyzer.security_risk(_make_action_event()) == SecurityRisk.HIGH

    def test_action_content_reaches_the_llm(self):
        """Regression for the repr(action) bug."""
        analyzer = _make_analyzer()
        _patch_completion(analyzer, _mock_llm_response("RISK: LOW\n"))
        analyzer.security_risk(_make_action_event(command="marker_value"))

        call_args = analyzer.llm.completion.call_args
        messages = call_args.kwargs.get("messages") or call_args.args[0]
        user_msg = next(m for m in messages if m.role == "user")
        user_text = user_msg.content[0].text
        assert "marker_value" in user_text
        assert "Tool: execute_bash" in user_text


# ---------------------------------------------------------------------------
# History window
# ---------------------------------------------------------------------------


class TestHistoryWindow:
    def test_first_call_has_empty_history(self):
        analyzer = _make_analyzer()
        _patch_completion(analyzer, _mock_llm_response("RISK: LOW\n"))
        analyzer.security_risk(_make_action_event())

        messages = analyzer.llm.completion.call_args.kwargs.get(
            "messages"
        ) or analyzer.llm.completion.call_args.args[0]
        user_text = next(m for m in messages if m.role == "user").content[0].text
        assert "no prior actions" in user_text

    def test_history_grows_across_calls(self):
        analyzer = _make_analyzer()
        _patch_completion(analyzer, _mock_llm_response("RISK: LOW\n"))

        analyzer.security_risk(_make_action_event(command="first_marker"))
        analyzer.security_risk(_make_action_event(command="second_marker"))

        messages = analyzer.llm.completion.call_args.kwargs.get(
            "messages"
        ) or analyzer.llm.completion.call_args.args[0]
        user_text = next(m for m in messages if m.role == "user").content[0].text
        # Second call's history should contain the first action
        assert "first_marker" in user_text
        # And the second action should be in "Current Action" section
        assert "second_marker" in user_text

    def test_history_capped_at_window(self):
        analyzer = _make_analyzer(history_window=2)
        _patch_completion(analyzer, _mock_llm_response("RISK: LOW\n"))

        for i in range(4):
            analyzer.security_risk(_make_action_event(command=f"cmd_{i}"))

        # Last call's history window = 2 means it saw cmd_1 and cmd_2 in history,
        # with cmd_3 as the current action. cmd_0 should be evicted.
        messages = analyzer.llm.completion.call_args.kwargs.get(
            "messages"
        ) or analyzer.llm.completion.call_args.args[0]
        user_text = next(m for m in messages if m.role == "user").content[0].text
        assert "cmd_0" not in user_text
        assert "cmd_3" in user_text

    def test_history_window_zero_rejected(self):
        with pytest.raises(ValueError, match="history_window must be >= 1"):
            ToolShieldLLMSecurityAnalyzer(llm=_make_test_llm(), history_window=0)

    def test_history_window_negative_rejected(self):
        with pytest.raises(ValueError, match="history_window must be >= 1"):
            ToolShieldLLMSecurityAnalyzer(
                llm=_make_test_llm(), history_window=-1
            )


# ---------------------------------------------------------------------------
# Safety experiences injection
# ---------------------------------------------------------------------------


class TestSafetyExperiences:
    def test_experiences_appear_in_system_prompt(self):
        analyzer = _make_analyzer(
            safety_experiences="- Never touch /etc/passwd."
        )
        _patch_completion(analyzer, _mock_llm_response("RISK: LOW\n"))
        analyzer.security_risk(_make_action_event())

        messages = analyzer.llm.completion.call_args.kwargs.get(
            "messages"
        ) or analyzer.llm.completion.call_args.args[0]
        sys_text = next(m for m in messages if m.role == "system").content[0].text
        assert "Never touch /etc/passwd" in sys_text

    def test_empty_experiences_shows_placeholder(self):
        analyzer = _make_analyzer(safety_experiences="")
        _patch_completion(analyzer, _mock_llm_response("RISK: LOW\n"))
        analyzer.security_risk(_make_action_event())

        messages = analyzer.llm.completion.call_args.kwargs.get(
            "messages"
        ) or analyzer.llm.completion.call_args.args[0]
        sys_text = next(m for m in messages if m.role == "system").content[0].text
        assert "No tool-specific safety experiences" in sys_text

    def test_default_none_auto_loads_terminal_and_filesystem(self):
        """Regression: omitting ``safety_experiences`` (default None) should
        auto-load the terminal + filesystem seed when toolshield is installed.
        """
        # Don't pass safety_experiences -> default is None -> should auto-load.
        analyzer = ToolShieldLLMSecurityAnalyzer(
            llm=_make_test_llm(),
            history_window=5,
        )
        # The resolved field should now be a non-empty string
        assert isinstance(analyzer.safety_experiences, str)
        assert len(analyzer.safety_experiences) > 100, (
            "Default seed should be non-trivial; got "
            f"{len(analyzer.safety_experiences or '')} chars"
        )
        # And the experiences should reference the terminal + filesystem tools
        text = analyzer.safety_experiences.lower()
        assert "terminal" in text
        assert "filesystem" in text or "file" in text

    def test_explicit_empty_string_opts_out(self):
        """Passing ``safety_experiences=''`` is a real opt-out -- the default
        terminal + filesystem seed must NOT auto-load."""
        analyzer = ToolShieldLLMSecurityAnalyzer(
            llm=_make_test_llm(),
            history_window=5,
            safety_experiences="",
        )
        assert analyzer.safety_experiences == ""
        _patch_completion(analyzer, _mock_llm_response("RISK: LOW\n"))
        analyzer.security_risk(_make_action_event())
        messages = analyzer.llm.completion.call_args.kwargs.get(
            "messages"
        ) or analyzer.llm.completion.call_args.args[0]
        sys_text = next(m for m in messages if m.role == "system").content[0].text
        assert "No tool-specific safety experiences" in sys_text

    def test_default_falls_back_gracefully_when_toolshield_missing(self):
        """If toolshield isn't installed, the None default must fall back to
        empty string with a warning, not raise ImportError."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            # Force ImportError for the helper module and its dep
            if name == "openhands.sdk.security.toolshield_helpers" or name == "toolshield":
                raise ImportError(f"No module named {name!r} (test)")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            analyzer = ToolShieldLLMSecurityAnalyzer(
                llm=_make_test_llm(),
                history_window=5,
            )
        assert analyzer.safety_experiences == ""


# ---------------------------------------------------------------------------
# ToolShield helpers
# ---------------------------------------------------------------------------


class TestToolShieldHelpers:
    def test_require_toolshield_raises_helpful_error_when_missing(self):
        from openhands.sdk.security.toolshield_helpers import _require_toolshield

        with patch.dict("sys.modules", {"toolshield": None}):
            # Force an ImportError by replacing the module entry
            import builtins

            real_import = builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name == "toolshield":
                    raise ImportError("No module named 'toolshield'")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=fake_import):
                with pytest.raises(ImportError, match="toolshield is not installed"):
                    _require_toolshield()

    def test_detect_active_mcp_tools_always_includes_terminal(self):
        """With no MCP servers responding, terminal-mcp is still returned."""
        from openhands.sdk.security import toolshield_helpers as th

        # Stub out the async MCP scanner so we don't actually hit the network.
        with patch.object(th, "_require_toolshield", return_value=None):
            # Patch asyncio.run to return an empty server list
            with patch.object(th.asyncio, "run", return_value=[]):
                # Also need the toolshield.mcp_scan import to not fail; since
                # _require_toolshield is stubbed, provide a fake module.
                import sys
                fake_mcp_scan = MagicMock()
                fake_mcp_scan.main = MagicMock()
                with patch.dict(sys.modules, {"toolshield.mcp_scan": fake_mcp_scan}):
                    result = th.detect_active_mcp_tools(port_range=(60000, 60001))
        assert "terminal-mcp" in result
        for always_active in th.ALWAYS_ACTIVE_TOOLS:
            assert always_active in result

    def test_experience_name_from_server_name(self):
        """Verify the server-name -> experience-name mapping matches
        toolshield's auto_discover convention."""
        from openhands.sdk.security.toolshield_helpers import (
            _experience_name_from_server_name,
        )
        assert _experience_name_from_server_name("filesystem") == "filesystem-mcp"
        assert _experience_name_from_server_name("Filesystem") == "filesystem-mcp"
        assert _experience_name_from_server_name("filesystem-mcp") == "filesystem-mcp"
        assert _experience_name_from_server_name("Postgres") == "postgres-mcp"
        assert _experience_name_from_server_name("server name") == "server-name-mcp"
