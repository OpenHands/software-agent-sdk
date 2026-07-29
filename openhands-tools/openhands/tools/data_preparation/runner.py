"""Subprocess runner and LLM credential helpers for DataFlow pipelines.

DataFlow runs isolated in a subprocess so heavy dependencies (torch,
transformers, datasets) never enter the agent-server process. LLM
credentials come from server-wide ``DF_*`` variables when configured and
otherwise fall back to the conversation LLM. Pipelines must never hardcode
secrets.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

from openhands.sdk.utils.redact import redact_text_secrets


DEFAULT_DATAFLOW_API_BASE_URL = "https://api.openai.com/v1"
DEFAULT_DATAFLOW_API_URL = f"{DEFAULT_DATAFLOW_API_BASE_URL}/chat/completions"

_DATAFLOW_CHECK_CACHE: dict[str, tuple[bool, str]] = {}


def resolve_dataflow_python(override: str | None = None) -> str:
    """Interpreter used to run DataFlow pipelines.

    Priority: explicit override > ``DATAFLOW_PYTHON`` env var > the current
    interpreter. Point ``DATAFLOW_PYTHON`` at a dedicated venv when DataFlow
    should not share the agent-server environment.
    """

    return override or os.environ.get("DATAFLOW_PYTHON") or sys.executable


def check_dataflow_installed(python: str) -> tuple[bool, str]:
    """Return (ok, detail) for ``import dataflow`` under ``python``.

    Only checks the top-level package (which does not pull in
    transformers/torch). Pipeline scripts handle the ``dataflow.serving``
    subpackage import themselves via a stub shim (see skill docs).
    """

    if python in _DATAFLOW_CHECK_CACHE:
        return _DATAFLOW_CHECK_CACHE[python]
    try:
        result = subprocess.run(
            [python, "-c", "import dataflow"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        outcome = (False, str(exc))
    else:
        detail = (result.stderr or "").strip()[-2000:]
        outcome = (result.returncode == 0, detail)
    _DATAFLOW_CHECK_CACHE[python] = outcome
    return outcome


def openai_compatible_model_name(model: str) -> str:
    """Strip the LiteLLM provider prefix (``openai/glm-5`` -> ``glm-5``)."""

    return model.split("/", 1)[1] if "/" in model else model


def _nonempty_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _base_url_from_chat_url(api_url: str) -> str:
    suffix = "/chat/completions"
    normalized = api_url.rstrip("/")
    if not normalized.endswith(suffix):
        raise ValueError(
            "DF_API_URL must be an OpenAI-compatible chat completions endpoint "
            "ending in '/chat/completions'."
        )
    return normalized[: -len(suffix)].rstrip("/")


def _resolve_dataflow_urls(
    configured_base_url: str | None,
    configured_api_url: str | None,
    fallback_base_url: str,
) -> tuple[str, str]:
    if configured_base_url:
        base_url = configured_base_url.rstrip("/")
    elif configured_api_url:
        base_url = _base_url_from_chat_url(configured_api_url)
    else:
        base_url = fallback_base_url.rstrip("/")

    api_url = (
        configured_api_url.rstrip("/")
        if configured_api_url
        else f"{base_url}/chat/completions"
    )
    expected_api_url = f"{base_url}/chat/completions"
    if api_url != expected_api_url:
        raise ValueError(
            "DF_API_BASE_URL and DF_API_URL do not describe the same endpoint: "
            f"expected DF_API_URL={expected_api_url!r}."
        )
    return base_url, api_url


def build_dataflow_env(conversation: Any) -> dict[str, str]:
    """Resolve DataFlow LLM env vars with server configuration taking priority.

    Process-wide ``DF_*`` values configure a separate DataFlow model (for
    example, a vision model) without changing the conversation's main coding
    model. Missing values fall back to the conversation LLM for compatibility.

    Raises:
        ValueError: If the resolved configuration is incomplete or inconsistent.
    """

    llm = conversation.state.agent.llm
    llm_api_key = llm.api_key.get_secret_value() if llm.api_key is not None else None
    api_key = _nonempty_env("DF_API_KEY") or llm_api_key
    model_name = _nonempty_env("DF_MODEL_NAME") or openai_compatible_model_name(
        str(llm.model)
    )
    fallback_base_url = (llm.base_url or DEFAULT_DATAFLOW_API_BASE_URL).rstrip("/")
    base_url, api_url = _resolve_dataflow_urls(
        _nonempty_env("DF_API_BASE_URL"),
        _nonempty_env("DF_API_URL"),
        fallback_base_url,
    )

    missing = [
        name
        for name, value in (
            ("DF_API_KEY", api_key),
            ("DF_MODEL_NAME", model_name),
            ("DF_API_BASE_URL", base_url),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "Incomplete DataFlow model configuration; missing "
            + ", ".join(missing)
            + "."
        )
    assert api_key is not None
    return {
        "DF_API_KEY": api_key,
        "DF_API_URL": api_url,
        "DF_API_BASE_URL": base_url,
        "DF_MODEL_NAME": model_name,
    }


def summarize_dataflow_env(env: dict[str, str]) -> str:
    """Return a secret-free summary suitable for logs and observations."""

    return (
        f"model={env['DF_MODEL_NAME']} "
        f"base_url={env['DF_API_BASE_URL']} "
        f"api_key_configured={'yes' if env.get('DF_API_KEY') else 'no'}"
    )


def _redact_subprocess_output(text: str, env_extra: dict[str, str]) -> str:
    redacted = text
    api_key = env_extra.get("DF_API_KEY")
    if api_key:
        redacted = redacted.replace(api_key, "<redacted>")
    return redact_text_secrets(redacted)


def run_dataflow_python(
    python: str,
    args: list[str],
    *,
    cwd: str,
    env_extra: dict[str, str],
    timeout: int,
) -> tuple[int, str, str]:
    """Run a DataFlow-related subprocess. Returns (rc, stdout, stderr)."""

    env = os.environ.copy()
    env.update(env_extra)
    try:
        result = subprocess.run(
            [python, *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = exc.stdout or "", exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return (
            124,
            _redact_subprocess_output(stdout, env_extra),
            _redact_subprocess_output(
                (stderr + f"\nTimed out after {timeout}s").strip(),
                env_extra,
            ),
        )
    return (
        result.returncode,
        _redact_subprocess_output(result.stdout or "", env_extra),
        _redact_subprocess_output(result.stderr or "", env_extra),
    )
