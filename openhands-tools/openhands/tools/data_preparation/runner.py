"""Subprocess runner and LLM credential helpers for DataFlow pipelines.

DataFlow runs isolated in a subprocess so heavy dependencies (torch,
transformers, datasets) never enter the agent-server process. LLM
credentials are derived from the conversation's own LLM config and injected
as environment variables — pipelines must never hardcode secrets.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any


DEFAULT_DATAFLOW_API_URL = "https://api.openai.com/v1/chat/completions"

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


def build_dataflow_env(conversation: Any) -> dict[str, str]:
    """Derive DataFlow LLM env vars from the conversation's agent LLM.

    Injected variables (read by the pipeline, never hardcoded):

    - ``DF_API_KEY``: API key (only when the agent LLM has one)
    - ``DF_API_URL``: chat-completions endpoint of the agent LLM base URL
    - ``DF_MODEL_NAME``: model name without LiteLLM provider prefix
    """

    llm = conversation.state.agent.llm
    env: dict[str, str] = {}
    if llm.api_key is not None:
        env["DF_API_KEY"] = llm.api_key.get_secret_value()
    base_url = (llm.base_url or "").rstrip("/")
    env["DF_API_URL"] = (
        f"{base_url}/chat/completions" if base_url else DEFAULT_DATAFLOW_API_URL
    )
    env["DF_MODEL_NAME"] = openai_compatible_model_name(llm.model)
    return env


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
        return 124, stdout, (stderr + f"\nTimed out after {timeout}s").strip()
    return result.returncode, result.stdout or "", result.stderr or ""
