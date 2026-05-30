"""Deprecation of the no-op ``_return_metrics`` / ``return_metrics`` parameter.

Tracks Q1 of #3341: the parameter has no effect (metrics are always returned
via ``LLMResponse.metrics``) and is scheduled for removal in 1.29.0. Until then
it stays in the public signatures and emits a ``DeprecationWarning`` only when a
caller actually passes it.
"""

import warnings

import pytest
from litellm.types.utils import (
    Choices,
    Message as LiteLLMMessage,
    ModelResponse,
    Usage,
)
from pydantic import SecretStr

from openhands.sdk.llm import LLM, Message, TextContent


_MSGS = [Message(role="user", content=[TextContent(text="hi")])]


def _mock_response(content: str = "ok", model: str = "gpt-4o") -> ModelResponse:
    return ModelResponse(
        id="resp-1",
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(content=content, role="assistant"),
            )
        ],
        created=1,
        model=model,
        object="chat.completion",
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _llm(model: str = "gpt-4o") -> LLM:
    return LLM(model=model, api_key=SecretStr("k"), usage_id="test")


def _has_return_metrics_warning(records) -> bool:
    return any(
        issubclass(r.category, DeprecationWarning)
        and "_return_metrics" in str(r.message)
        for r in records
    )


def test_return_metrics_emits_deprecation_warning(monkeypatch):
    llm = _llm()
    monkeypatch.setattr(
        "openhands.sdk.llm.llm.litellm_completion", lambda **kw: _mock_response()
    )

    with pytest.warns(DeprecationWarning, match="_return_metrics"):
        llm.completion(_MSGS, _return_metrics=True)


def test_no_warning_when_not_passed(monkeypatch):
    llm = _llm()
    monkeypatch.setattr(
        "openhands.sdk.llm.llm.litellm_completion", lambda **kw: _mock_response()
    )

    with warnings.catch_warnings(record=True) as records:
        warnings.simplefilter("always")
        llm.completion(_MSGS)

    assert not _has_return_metrics_warning(records)


def test_no_warning_when_falsy(monkeypatch):
    llm = _llm()
    monkeypatch.setattr(
        "openhands.sdk.llm.llm.litellm_completion", lambda **kw: _mock_response()
    )

    with warnings.catch_warnings(record=True) as records:
        warnings.simplefilter("always")
        llm.completion(_MSGS, _return_metrics=False)

    assert not _has_return_metrics_warning(records)
