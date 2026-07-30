"""Subprocess runner and LLM credential helpers for DataFlow pipelines.

DataFlow runs isolated in a subprocess so heavy dependencies (torch,
transformers, datasets) never enter the agent-server process. The ``text``
profile uses the conversation LLM; the ``vision`` profile prefers server-wide
``DF_*`` configuration and falls back to the conversation LLM. Pipelines must
never hardcode secrets.
"""

from __future__ import annotations

import ast
import hashlib
import os
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from openhands.sdk.utils.redact import redact_text_secrets


DEFAULT_DATAFLOW_API_BASE_URL = "https://api.openai.com/v1"
DEFAULT_DATAFLOW_API_URL = f"{DEFAULT_DATAFLOW_API_BASE_URL}/chat/completions"
SUPPORTED_DATAFLOW_VERSION = "1.0.10"

_DATAFLOW_CHECK_CACHE: dict[str, tuple[bool, str]] = {}
_FORBIDDEN_MANAGED_IMAGE_IMPORTS = {
    "base64",
    "dataflow",
    "httpx",
    "openai",
    "preparation_runtime",
    "requests",
    "urllib",
}


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


def check_dataflow_version(
    python: str,
    expected: str = SUPPORTED_DATAFLOW_VERSION,
) -> tuple[bool, str]:
    """Verify that local Sample execution matches the Pyromind DataFlow pin."""

    try:
        result = subprocess.run(
            [
                python,
                "-c",
                "import dataflow; print(getattr(dataflow, '__version__', ''))",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or "").strip()[-2000:]
    actual = (result.stdout or "").strip()
    if actual != expected:
        return False, f"expected {expected}, found {actual or 'unknown'}"
    return True, actual


def runtime_bundle_fingerprint(
    runtime_dir: Path,
    filenames: Iterable[str],
) -> str:
    """Hash runtime names and bytes in a stable order."""

    digest = hashlib.sha256()
    for filename in sorted(filenames):
        path = runtime_dir / filename
        if not path.is_file():
            raise ValueError(f"DataFlow runtime file is missing: {path}")
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def runtime_public_names(path: Path) -> set[str]:
    """Read a literal ``__all__`` contract without importing the runtime."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise ValueError(f"Could not inspect image_utils public API: {exc}") from exc
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in targets
        ):
            continue
        if node.value is None:
            break
        try:
            value = ast.literal_eval(node.value)
        except (TypeError, ValueError) as exc:
            raise ValueError("image_utils __all__ must be a literal list") from exc
        if not isinstance(value, list) or not all(
            isinstance(name, str) for name in value
        ):
            raise ValueError("image_utils __all__ must contain only strings")
        return set(value)
    raise ValueError("image_utils does not define a public __all__ contract")


def validate_managed_image_pipeline(
    pipeline: Path,
    public_names: set[str],
) -> None:
    """Enforce the configuration-only managed image pipeline contract."""

    try:
        source = pipeline.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(pipeline))
        compile(tree, str(pipeline), "exec")
    except (OSError, SyntaxError) as exc:
        raise ValueError(f"Could not parse image pipeline: {exc}") from exc

    imported_image_utils: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.comprehension)):
            raise ValueError(
                "Managed image pipelines must be configuration-only and cannot "
                "contain for/while loops."
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if alias.name == "image_utils":
                    raise ValueError(
                        "Use explicit `from image_utils import ...` imports."
                    )
                if root in _FORBIDDEN_MANAGED_IMAGE_IMPORTS:
                    raise ValueError(f"Managed image pipelines cannot import {root}.")
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        root = module.split(".", 1)[0]
        if root in _FORBIDDEN_MANAGED_IMAGE_IMPORTS:
            raise ValueError(f"Managed image pipelines cannot import {root}.")
        if module != "image_utils":
            continue
        imported = {alias.name for alias in node.names}
        if "*" in imported:
            raise ValueError("image_utils wildcard imports are not allowed.")
        unknown = sorted(imported - public_names)
        if unknown:
            raise ValueError(
                "Managed image pipeline imports unsupported image_utils APIs: "
                + ", ".join(unknown)
            )
        imported_image_utils.update(imported)

    required = {"ImagePipelineConfig", "run_image_pipeline_from_cli"}
    missing = sorted(required - imported_image_utils)
    if missing:
        raise ValueError("Managed image pipeline must import: " + ", ".join(missing))


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


def build_dataflow_env(
    conversation: Any,
    model_profile: Literal["text", "vision"] = "vision",
) -> dict[str, str]:
    """Resolve DataFlow LLM env vars for an explicit text or vision profile.

    Process-wide ``DF_*`` values configure the vision model without changing
    the conversation's main coding model. Text always uses the conversation
    model. Missing vision values fall back to that model for compatibility.

    Raises:
        ValueError: If the resolved configuration is incomplete or inconsistent.
    """

    llm = conversation.state.agent.llm
    if llm.api_key is None:
        llm_api_key = None
    elif hasattr(llm.api_key, "get_secret_value"):
        llm_api_key = llm.api_key.get_secret_value()
    else:
        llm_api_key = str(llm.api_key)
    fallback_base_url = (llm.base_url or DEFAULT_DATAFLOW_API_BASE_URL).rstrip("/")
    if model_profile == "vision":
        api_key = _nonempty_env("DF_API_KEY") or llm_api_key
        model_name = _nonempty_env("DF_MODEL_NAME") or openai_compatible_model_name(
            str(llm.model)
        )
        base_url, api_url = _resolve_dataflow_urls(
            _nonempty_env("DF_API_BASE_URL"),
            _nonempty_env("DF_API_URL"),
            fallback_base_url,
        )
    else:
        api_key = llm_api_key
        model_name = openai_compatible_model_name(str(llm.model))
        base_url, api_url = _resolve_dataflow_urls(
            None,
            None,
            fallback_base_url,
        )

    missing = [
        name
        for name, value in (
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
    resolved = {
        "DF_API_URL": api_url,
        "DF_API_BASE_URL": base_url,
        "DF_MODEL_NAME": model_name,
    }
    if api_key:
        resolved["DF_API_KEY"] = api_key
    return resolved


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
