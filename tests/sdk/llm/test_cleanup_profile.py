"""Tests for the cleanup LLM profile (``clean_outward_text``).

The cleanup profile repairs an agent's outward text before a human reads it. It
resolves a saved LLM profile named ``cleanup`` and runs a single stateless
completion, failing open (returning the original text) whenever the profile is
missing or the call errors.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import PrivateAttr

from openhands.sdk.llm import (
    CLEANUP_PROFILE_NAME,
    LLM,
    LLMResponse,
    Message,
    TextContent,
    clean_outward_text,
    llm_profile_store,
)
from openhands.sdk.llm.llm import LLMCallContext
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.streaming import TokenCallbackType
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import ToolDefinition


class CapturingTestLLM(TestLLM):
    """TestLLM that records the messages and tools of the last completion."""

    _last_messages: list[Message] = PrivateAttr(default_factory=list)
    _last_tools: Sequence[ToolDefinition] | None = PrivateAttr(default=None)

    @property
    def last_messages(self) -> list[Message]:
        return self._last_messages

    @property
    def last_tools(self) -> Sequence[ToolDefinition] | None:
        return self._last_tools

    def completion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        add_security_risk_prediction: bool = False,
        on_token: TokenCallbackType | None = None,
        call_context: LLMCallContext | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self._last_messages = list(messages)
        self._last_tools = tools
        return super().completion(
            messages=messages,
            tools=tools,
            add_security_risk_prediction=add_security_risk_prediction,
            on_token=on_token,
            call_context=call_context,
            **kwargs,
        )


def _assistant_message(text: str) -> Message:
    return Message(role="assistant", content=[TextContent(text=text)])


def _message_text(message: Message) -> str:
    return "".join(
        content.text for content in message.content if isinstance(content, TextContent)
    )


def _capturing_llm(*replies: str) -> CapturingTestLLM:
    return cast(
        CapturingTestLLM,
        CapturingTestLLM.from_messages(
            [_assistant_message(reply) for reply in replies],
            model="cleanup-model",
            usage_id="cleanup",
        ),
    )


def test_returns_cleaned_text_from_cleanup_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_llm = _capturing_llm("Done! I appreciate the nudge.")

    def load_profile(self: LLMProfileStore, name: str, *, cipher: Any = None) -> LLM:
        assert name == CLEANUP_PROFILE_NAME
        return cleanup_llm

    monkeypatch.setattr(LLMProfileStore, "load", load_profile)

    original = "Done! I appreciate the nudge! \u00f0"
    result = clean_outward_text(original)

    assert result == "Done! I appreciate the nudge."
    # Stateless call: only a system + user message, no tools, no history.
    assert [message.role for message in cleanup_llm.last_messages] == ["system", "user"]
    assert "repair" in _message_text(cleanup_llm.last_messages[0]).lower()
    assert original in _message_text(cleanup_llm.last_messages[1])


def test_missing_profile_returns_original_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A real, empty profile directory: no "cleanup" profile exists, so the
    # feature is off and the original text passes through unchanged.
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    monkeypatch.setattr(llm_profile_store, "_DEFAULT_PROFILE_DIR", profile_dir)

    original = "Ship it \u00f0"
    assert clean_outward_text(original) == original


def test_call_failure_returns_original_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Scripted with no replies: the next completion raises (exhausted), so the
    # cleanup must fail open and return the untouched original.
    failing_llm = _capturing_llm()

    monkeypatch.setattr(
        LLMProfileStore,
        "load",
        lambda self, name, *, cipher=None: failing_llm,
    )

    original = "Keep me exactly \u00e2 as is"
    assert clean_outward_text(original) == original


def test_empty_cleanup_reply_returns_original_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A blank reply must not blank out the message; keep the original.
    cleanup_llm = _capturing_llm("   ")
    monkeypatch.setattr(
        LLMProfileStore,
        "load",
        lambda self, name, *, cipher=None: cleanup_llm,
    )

    original = "Real content here"
    assert clean_outward_text(original) == original


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_blank_input_short_circuits_without_loading_profile(
    blank: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_load(self: LLMProfileStore, name: str, *, cipher: Any = None) -> LLM:
        raise AssertionError("profile should not be loaded for blank input")

    monkeypatch.setattr(LLMProfileStore, "load", fail_load)

    assert clean_outward_text(blank) == blank
