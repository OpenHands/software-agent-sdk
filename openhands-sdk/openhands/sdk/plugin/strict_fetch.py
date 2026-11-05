"""Strict plugin source resolution for pre-flight checks and sandbox fetches."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from filelock import FileLock
from pydantic import BaseModel, Field

from openhands.sdk.extensions.fetch import (
    ExtensionFetchError,
    SourceType,
    parse_extension_source,
)
from openhands.sdk.plugin.fetch import DEFAULT_CACHE_DIR, PluginFetchError
from openhands.sdk.plugin.paths import get_pinned_plugin_cache_path
from openhands.sdk.plugin.plugin import Plugin
from openhands.sdk.secret import SecretSource, SecretValue
from openhands.sdk.skills.utils import expand_variable_references
from openhands.sdk.utils.path import resolves_within
from openhands.sdk.utils.redact import (
    redact_url_credentials,
    redact_url_credentials_in_text,
)


_FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_BRACED_VAR_RE = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)(?::-([^}]*))?\}")

_ERROR_STATUSES = {
    "missing_secret": 422,
    "invalid_source": 422,
    "source_not_allowed": 422,
    "auth_failed": 422,
    "source_unavailable": 502,
    "ref_not_found": 422,
    "repo_path_not_found": 422,
    "invalid_plugin": 422,
    "timeout": 504,
}


class PluginSourceError(PluginFetchError):
    """Raised when strict plugin source resolution fails."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status_code(self) -> int:
        return _ERROR_STATUSES.get(self.code, 422)


class CheckedPlugin(BaseModel):
    """A strictly resolved plugin checkout and its public metadata."""

    path: Path = Field(description="Path to the resolved plugin directory")
    resolved_ref: str | None = Field(description="Resolved git commit SHA")
    source: str = Field(description="Original source, with placeholders preserved")
    repo_path: str | None = Field(description="Subdirectory path within the repository")
    manifest: dict[str, Any] = Field(description="Raw plugin.json contents")
    skills: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)


def plugin_source_error_status(code: str) -> int:
    """Return the HTTP status code used for a strict plugin source error."""
    return _ERROR_STATUSES.get(code, 422)


@contextmanager
def preflight_plugin(
    source: str,
    ref: str | None = None,
    repo_path: str | None = None,
    secrets: Mapping[str, SecretValue] | None = None,
    *,
    timeout: int = 60,
    allow_local: bool = False,
) -> Iterator[CheckedPlugin]:
    """Strictly check a plugin source in a throwaway checkout.

    The temporary checkout is deleted when the context exits, including when the
    caller raises while inspecting files referenced by the manifest.
    """
    with tempfile.TemporaryDirectory(prefix="openhands-plugin-preflight-") as tmp:
        yield fetch_plugin_strict(
            source=source,
            ref=ref,
            repo_path=repo_path,
            secrets=secrets,
            cache_dir=None,
            work_dir=Path(tmp),
            timeout=timeout,
            allow_local=allow_local,
            use_cache=False,
        )


def fetch_plugin_strict(
    *,
    source: str,
    ref: str | None = None,
    repo_path: str | None = None,
    secrets: Mapping[str, SecretValue] | None = None,
    cache_dir: Path | None = None,
    work_dir: Path | None = None,
    timeout: int = 60,
    allow_local: bool = False,
    use_cache: bool = True,
) -> CheckedPlugin:
    """Strictly fetch and describe a plugin without installing it."""
    resolved_cache_dir = (
        DEFAULT_CACHE_DIR if use_cache and cache_dir is None else cache_dir
    )

    expanded_source, expanded_ref, secret_values = _expand_source_and_ref(
        source, ref, secrets or {}
    )
    try:
        source_type, url = parse_extension_source(expanded_source)
    except ExtensionFetchError:
        raise PluginSourceError(
            "invalid_source",
            "Invalid plugin source.",
        ) from None

    if source_type == SourceType.LOCAL:
        if not allow_local:
            raise PluginSourceError(
                "source_not_allowed",
                "Local plugin sources are not allowed for this operation.",
            )
        plugin_root = _copy_local_source(url, repo_path, work_dir)
        return _checked_plugin(
            plugin_root=plugin_root,
            source=source,
            repo_path=repo_path,
            resolved_ref=None,
            secret_values=secret_values,
        )

    if expanded_source.startswith("file://") and not allow_local:
        raise PluginSourceError(
            "source_not_allowed",
            "file:// plugin sources are not allowed for this operation.",
        )

    resolved_ref = expanded_ref
    if (
        use_cache
        and resolved_cache_dir is not None
        and resolved_ref is not None
        and _FULL_SHA_RE.fullmatch(resolved_ref)
    ):
        cached_root = get_pinned_plugin_cache_path(
            url, resolved_cache_dir, resolved_ref.lower()
        )
        if cached_root.is_dir():
            plugin_root = _apply_repo_path(cached_root, repo_path)
            return _checked_plugin(
                plugin_root=plugin_root,
                source=source,
                repo_path=repo_path,
                resolved_ref=resolved_ref.lower(),
                secret_values=secret_values,
            )

    temp_ctx: tempfile.TemporaryDirectory[str] | None = None
    if work_dir is None:
        temp_ctx = tempfile.TemporaryDirectory(prefix="openhands-plugin-fetch-")
        work_root = Path(temp_ctx.name)
    else:
        work_root = work_dir
        work_root.mkdir(parents=True, exist_ok=True)

    try:
        fetched_root, fetched_ref = _fetch_git_checkout(
            url=url,
            ref=expanded_ref,
            work_root=work_root,
            timeout=timeout,
            allow_file=allow_local,
            secret_values=secret_values,
        )
        resolved_ref = fetched_ref.lower()

        if use_cache and resolved_cache_dir is not None:
            cached_root = _store_pinned_checkout(
                url, resolved_cache_dir, resolved_ref, fetched_root
            )
            plugin_root = _apply_repo_path(cached_root, repo_path)
        else:
            plugin_root = _apply_repo_path(fetched_root, repo_path)

        return _checked_plugin(
            plugin_root=plugin_root,
            source=source,
            repo_path=repo_path,
            resolved_ref=resolved_ref,
            secret_values=secret_values,
        )
    finally:
        if temp_ctx is not None:
            temp_ctx.cleanup()


def _secret_value(value: SecretValue) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, SecretSource):
        return value.get_value()
    return None


def _expand_source_and_ref(
    source: str,
    ref: str | None,
    secrets: Mapping[str, SecretValue],
) -> tuple[str, str | None, list[str]]:
    referenced = sorted(
        {
            match.group(1)
            for value in (source, ref or "")
            for match in _BRACED_VAR_RE.finditer(value)
        }
    )
    resolved: dict[str, str] = {}
    for name in referenced:
        value = secrets.get(name)
        plain_value = _secret_value(value) if value is not None else None
        if plain_value is None:
            raise PluginSourceError("missing_secret", f"Missing secret: {name}")
        resolved[name] = plain_value

    expanded_source = expand_variable_references(
        source,
        variables=resolved,
        check_env=False,
        support_unbraced=False,
        expand_defaults=False,
    )
    expanded_ref = (
        expand_variable_references(
            ref,
            variables=resolved,
            check_env=False,
            support_unbraced=False,
            expand_defaults=False,
        )
        if ref is not None
        else None
    )
    return expanded_source, expanded_ref, list(resolved.values())


def _copy_local_source(url: str, repo_path: str | None, work_dir: Path | None) -> Path:
    source_root = Path(url).expanduser().resolve()
    plugin_root = _apply_repo_path(source_root, repo_path)
    if work_dir is None:
        return plugin_root
    destination = work_dir / "local-plugin"
    shutil.copytree(plugin_root, destination, symlinks=True)
    return destination


def _fetch_git_checkout(
    *,
    url: str,
    ref: str | None,
    work_root: Path,
    timeout: int,
    allow_file: bool,
    secret_values: list[str],
) -> tuple[Path, str]:
    repo_dir = work_root / "repo"
    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    repo_dir.mkdir(parents=True)

    _run_git(["init"], repo_dir, timeout, allow_file, secret_values)
    _run_git(
        ["remote", "add", "origin", url], repo_dir, timeout, allow_file, secret_values
    )

    fetch_ref = ref or "HEAD"
    try:
        _run_git(
            ["fetch", "--depth", "1", "origin", fetch_ref],
            repo_dir,
            timeout,
            allow_file,
            secret_values,
            default_code="ref_not_found" if ref else "source_unavailable",
        )
    except PluginSourceError as exc:
        if exc.code == "source_unavailable":
            raise
        raise

    resolved_ref = _run_git(
        ["rev-parse", "FETCH_HEAD^{commit}"],
        repo_dir,
        timeout,
        allow_file,
        secret_values,
        default_code="ref_not_found",
    ).strip()
    if not _FULL_SHA_RE.fullmatch(resolved_ref):
        raise PluginSourceError("ref_not_found", f"Ref not found: {ref or 'HEAD'}")

    _run_git(
        ["checkout", "--detach", resolved_ref],
        repo_dir,
        timeout,
        allow_file,
        secret_values,
        default_code="ref_not_found",
    )
    return repo_dir, resolved_ref


def _run_git(
    args: list[str],
    cwd: Path,
    timeout: int,
    allow_file: bool,
    secret_values: list[str],
    *,
    default_code: str = "source_unavailable",
) -> str:
    protocol_file = "always" if allow_file else "never"
    cmd = [
        "git",
        "-c",
        "credential.helper=",
        "-c",
        "core.askPass=",
        "-c",
        f"protocol.file.allow={protocol_file}",
        *args,
    ]
    env = os.environ.copy()
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ASKPASS": "true",
            "SSH_ASKPASS": "true",
        }
    )
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise PluginSourceError("timeout", "Git operation timed out.") from exc
    except OSError as exc:
        raise PluginSourceError("source_unavailable", f"Git failed: {exc}") from exc

    if result.returncode == 0:
        return result.stdout

    stderr = _sanitize_message(result.stderr.strip(), secret_values)
    raise PluginSourceError(_classify_git_error(stderr, default_code), stderr)


def _classify_git_error(stderr: str, default_code: str) -> str:
    lower = stderr.lower()
    if "authentication failed" in lower or "authentication required" in lower:
        return "auth_failed"
    if (
        "couldn't find remote ref" in lower
        or "could not find remote ref" in lower
        or "not our ref" in lower
        or "unadvertised object" in lower
        or "bad object" in lower
        or "needed a single revision" in lower
    ):
        return "ref_not_found"
    if (
        "could not read username" in lower
        or "terminal prompts disabled" in lower
        or "repository not found" in lower
        or "could not resolve host" in lower
        or "failed to connect" in lower
        or "connection refused" in lower
        or "does not appear to be a git repository" in lower
        or "access rights" in lower
    ):
        return "source_unavailable"
    return default_code


def _sanitize_message(message: str, secret_values: list[str]) -> str:
    sanitized = redact_url_credentials_in_text(message)
    sanitized = redact_url_credentials(sanitized)
    for value in secret_values:
        if value:
            sanitized = sanitized.replace(value, "****")
    return sanitized


def _store_pinned_checkout(
    source_url: str, cache_dir: Path, resolved_ref: str, fetched_root: Path
) -> Path:
    target = get_pinned_plugin_cache_path(source_url, cache_dir, resolved_ref)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.parent / f"{target.name}.lock"
    with FileLock(lock_path):
        if target.is_dir():
            return target
        tmp_target = target.parent / f".{target.name}.{os.getpid()}.tmp"
        if tmp_target.exists():
            shutil.rmtree(tmp_target)
        shutil.copytree(
            fetched_root,
            tmp_target,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git"),
        )
        tmp_target.rename(target)
    return target


def _apply_repo_path(root: Path, repo_path: str | None) -> Path:
    if not repo_path:
        return root
    plugin_root = root / repo_path.strip("/")
    if not resolves_within(plugin_root, root) or not plugin_root.is_dir():
        raise PluginSourceError(
            "repo_path_not_found", f"repo_path not found: {repo_path}"
        )
    return plugin_root


def _checked_plugin(
    *,
    plugin_root: Path,
    source: str,
    repo_path: str | None,
    resolved_ref: str | None,
    secret_values: list[str],
) -> CheckedPlugin:
    manifest = _read_raw_manifest(plugin_root, secret_values)
    try:
        plugin = Plugin.load(plugin_root)
    except Exception as exc:
        message = _sanitize_message(str(exc), secret_values)
        raise PluginSourceError("invalid_plugin", f"Invalid plugin: {message}") from exc

    return CheckedPlugin(
        path=plugin_root,
        resolved_ref=resolved_ref,
        source=source,
        repo_path=repo_path,
        manifest=manifest,
        skills=[skill.name for skill in plugin.skills],
        commands=[command.name for command in plugin.commands],
    )


def _read_raw_manifest(plugin_root: Path, secret_values: list[str]) -> dict[str, Any]:
    candidates = [
        plugin_root / "plugin.json",
        plugin_root / ".plugin" / "plugin.json",
        plugin_root / ".claude-plugin" / "plugin.json",
    ]
    for candidate in candidates:
        if not candidate.is_file() or not resolves_within(candidate, plugin_root):
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            message = _sanitize_message(str(exc), secret_values)
            raise PluginSourceError(
                "invalid_plugin", f"Invalid plugin manifest: {message}"
            ) from exc
        if not isinstance(data, dict):
            raise PluginSourceError(
                "invalid_plugin",
                "Invalid plugin manifest: plugin.json must be an object.",
            )
        return data
    raise PluginSourceError("invalid_plugin", "Invalid plugin: plugin.json not found.")
