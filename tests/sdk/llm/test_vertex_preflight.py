"""Tests for the Vertex AI optional-extra preflight check."""

from __future__ import annotations

import pytest

from openhands.sdk.llm.exceptions import LLMBadRequestError
from openhands.sdk.llm.utils import vertex_preflight
from openhands.sdk.llm.utils.vertex_preflight import assert_vertex_sdk_available


def test_noop_for_non_vertex_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even with the SDK absent, non-vertex providers must not raise.
    monkeypatch.setattr(vertex_preflight, "_vertex_sdk_available", lambda: False)
    assert_vertex_sdk_available(None)
    assert_vertex_sdk_available("openai")
    assert_vertex_sdk_available("bedrock")


def test_passes_when_sdk_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vertex_preflight, "_vertex_sdk_available", lambda: True)
    assert_vertex_sdk_available("vertex_ai")


def test_raises_with_install_hint_when_sdk_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vertex_preflight, "_vertex_sdk_available", lambda: False)
    with pytest.raises(LLMBadRequestError) as excinfo:
        assert_vertex_sdk_available("vertex_ai")
    assert "openhands-sdk[vertex]" in str(excinfo.value)
