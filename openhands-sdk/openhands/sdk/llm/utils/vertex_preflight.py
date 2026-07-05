"""Preflight check for Vertex AI partner-model dependencies.

`google-cloud-aiplatform` is an optional extra (`openhands-sdk[vertex]`). When a
caller targets a `vertex_ai/*` model without the extra installed, LiteLLM fails
with a low-level `ModuleNotFoundError` from inside its provider handler. We
catch that earlier and surface a friendly install hint instead.
"""

from __future__ import annotations

import importlib.util

from openhands.sdk.llm.exceptions import LLMBadRequestError


_INSTALL_HINT = (
    "Vertex AI partner models require the Vertex SDK. "
    'Install with: pip install "openhands-sdk[vertex]"'
)


def _vertex_sdk_available() -> bool:
    return importlib.util.find_spec("vertexai") is not None


def assert_vertex_sdk_available(provider: str | None) -> None:
    """Raise a friendly error if the caller is targeting Vertex without the SDK.

    No-op for any non-`vertex_ai` provider, so it's safe to call unconditionally
    from the transport path.
    """
    if provider != "vertex_ai":
        return
    if _vertex_sdk_available():
        return
    raise LLMBadRequestError(_INSTALL_HINT)
