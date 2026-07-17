"""Manage brokered ACP file credentials."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import httpx

from openhands.sdk.secret import LookupSecret


if TYPE_CHECKING:
    from openhands.sdk.conversation.secret_registry import SecretRegistry


CODEX_AUTH_SECRET_NAME = "CODEX_AUTH_JSON"

_CODEX_AUTH_SYNC_DELAYS: tuple[float, ...] = (0.1, 0.5)
_CODEX_AUTH_HTTP_TIMEOUT = 5.0
_CODEX_AUTH_REMOTE_CHECK_INTERVAL = 60.0
_CODEX_AUTH_DIGEST_HEADER = "X-Codex-Auth-Digest"
_CODEX_AUTH_SANDBOX_HEADER = "X-OH-Sandbox"
_CODEX_AUTH_SCOPE_HEADER = "X-OH-Codex"
_CODEX_LOCAL_AUTH_SCOPE_HEADER = "X-OH-Codex-Token"
_CODEX_LOCAL_REFRESH_USERNAME = "codex"
_CODEX_REFRESH_TOKEN_URL_ENV = "CODEX_REFRESH_TOKEN_URL_OVERRIDE"
_CHATGPT_AUTH_PATH = Path(".codex") / "auth.json"


class ACPFileCredentialNeedsReauthError(RuntimeError):
    pass


class ACPFileCredentialSyncError(RuntimeError):
    pass


class ACPFileCredentialLifecycle(Protocol):
    """Define a brokered ACP file credential lifecycle."""

    secret_name: str
    path: Path | None

    @property
    def may_have_changed(self) -> bool: ...

    def load(self) -> str | None: ...

    def bind(
        self,
        path: Path,
        registry: SecretRegistry,
        remote_value: str,
        env: dict[str, str],
    ) -> None: ...

    def should_preserve_existing(self, path: Path) -> bool: ...

    def record_materialized(self, remote_value: str, local_value: str) -> None: ...

    def on_auth_succeeded(self, method_id: str) -> None: ...

    def on_session_started(self) -> None: ...

    def sync(self) -> None: ...

    def release(self) -> None: ...


def codex_auth_file(env: dict[str, str]) -> Path:
    codex_home = env.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home) / "auth.json"
    return Path.home() / _CHATGPT_AUTH_PATH


def codex_auth_file_is_chatgpt(env: dict[str, str]) -> bool:
    path = codex_auth_file(env)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and "tokens" in data


def write_secret_file(path: Path, value: str) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        file = os.fdopen(fd, "w", encoding="utf-8")
        fd = -1
        with file:
            file.write(value)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            temporary_path.unlink()
        except OSError:
            pass
        raise


def _codex_auth_ancestor_file(path: Path) -> Path:
    return path.with_name(f".{path.name}.cloud-digest")


def _read_codex_auth_ancestor(path: Path) -> str | None:
    try:
        return _codex_auth_ancestor_file(path).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None


def _write_codex_auth_ancestor(path: Path, digest: str) -> None:
    write_secret_file(_codex_auth_ancestor_file(path), digest)


def _update_codex_auth_source(
    source: LookupSecret, value: str, expected_digest: str
) -> None:
    response = httpx.put(
        source.url,
        headers=source.headers,
        json={"expected_digest": expected_digest, "value": value},
        timeout=_CODEX_AUTH_HTTP_TIMEOUT,
    )
    response.raise_for_status()


def _get_codex_auth_source(source: LookupSecret) -> str:
    response = httpx.get(
        source.url,
        headers=source.headers,
        timeout=_CODEX_AUTH_HTTP_TIMEOUT,
    )
    response.raise_for_status()
    return response.text


def _touch_codex_auth_source(source: LookupSecret) -> str | None:
    response = httpx.head(
        source.url,
        headers=source.headers,
        timeout=_CODEX_AUTH_HTTP_TIMEOUT,
    )
    response.raise_for_status()
    digest = response.headers.get(_CODEX_AUTH_DIGEST_HEADER)
    return (
        digest
        if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
        else None
    )


def _release_codex_auth_source(source: LookupSecret) -> None:
    response = httpx.delete(
        source.url,
        headers=source.headers,
        timeout=_CODEX_AUTH_HTTP_TIMEOUT,
    )
    response.raise_for_status()


def _codex_auth_refresh_url(source: LookupSecret) -> str | None:
    headers = {name.lower(): value for name, value in source.headers.items()}
    session_api_key = headers.get(_CODEX_AUTH_SANDBOX_HEADER.lower())
    codex_auth_token = headers.get(
        _CODEX_LOCAL_AUTH_SCOPE_HEADER.lower()
    ) or headers.get(_CODEX_AUTH_SCOPE_HEADER.lower())
    if not codex_auth_token:
        return None
    url = httpx.URL(source.url)
    refresh_path = f"{url.path.rstrip('/')}/refresh"
    return str(
        url.copy_with(
            path=refresh_path,
            username=session_api_key or _CODEX_LOCAL_REFRESH_USERNAME,
            password=codex_auth_token,
        )
    )


def _is_valid_codex_auth_value(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and bool(payload)


class _CodexAuthLifecycle:
    """Manage brokered Codex subscription auth."""

    secret_name = CODEX_AUTH_SECRET_NAME

    def __init__(self, source: LookupSecret, refresh_url: str):
        self.source = source
        self.refresh_url = refresh_url
        self.path: Path | None = None
        self.expected_digest: str | None = None
        self.last_remote_check = 0.0
        self.registry: SecretRegistry | None = None
        self._may_have_changed = False

    @property
    def may_have_changed(self) -> bool:
        return self._may_have_changed

    def load(self) -> str | None:
        try:
            return self.source.get_value()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 422):
                detail = (
                    "ChatGPT authentication was not found in Cloud."
                    if exc.response.status_code == 404
                    else "ChatGPT authentication needs to be refreshed."
                )
                raise ACPFileCredentialNeedsReauthError(detail) from exc
            raise ACPFileCredentialSyncError(
                "Codex credentials could not be loaded from Cloud."
            ) from exc
        except httpx.RequestError as exc:
            raise ACPFileCredentialSyncError(
                "Codex credentials could not be loaded from Cloud."
            ) from exc

    def bind(
        self,
        path: Path,
        registry: SecretRegistry,
        remote_value: str,
        env: dict[str, str],
    ) -> None:
        if not _is_valid_codex_auth_value(remote_value):
            raise ACPFileCredentialSyncError(
                "Cloud returned invalid Codex credentials."
            )
        self.path = path
        self.registry = registry
        self.expected_digest = hashlib.sha256(remote_value.encode()).hexdigest()
        env[_CODEX_REFRESH_TOKEN_URL_ENV] = self.refresh_url
        self._track_transport()

    def should_preserve_existing(self, path: Path) -> bool:
        return _read_codex_auth_ancestor(path) == self.expected_digest

    def record_materialized(self, remote_value: str, local_value: str) -> None:
        path = self.path
        expected_digest = self.expected_digest
        assert path is not None
        assert expected_digest is not None
        _write_codex_auth_ancestor(path, expected_digest)
        local_digest = hashlib.sha256(local_value.encode()).hexdigest()
        self.last_remote_check = (
            time.monotonic() if local_digest == expected_digest else 0.0
        )
        if local_digest != expected_digest:
            self._may_have_changed = True
        self._track_values(remote_value)
        if local_digest != expected_digest:
            self._track_values(local_value)

    def on_auth_succeeded(self, method_id: str) -> None:
        if method_id == "chat-gpt":
            self._may_have_changed = True

    def on_session_started(self) -> None:
        self._may_have_changed = True

    def _track_values(self, value: str) -> None:
        registry = self.registry
        if registry is None:
            return
        exported_values = {self.secret_name: value}
        try:
            tokens = json.loads(value).get("tokens", {})
        except (AttributeError, TypeError, ValueError):
            tokens = None
        if isinstance(tokens, dict):
            for name, token in tokens.items():
                if isinstance(token, str) and token:
                    exported_values[f"{self.secret_name}.tokens.{name}"] = token
        registry.track_exported_values(exported_values)

    def _track_transport(self) -> None:
        registry = self.registry
        if registry is None:
            return
        exported_values = {
            f"{self.secret_name}.refresh_url": self.refresh_url,
        }
        for name, value in self.source.headers.items():
            exported_values[f"{self.secret_name}.header.{name.lower()}"] = value
        registry.track_exported_values(exported_values)

    def sync(self) -> None:
        path = self.path
        expected_digest = self.expected_digest
        if path is None or expected_digest is None:
            return
        try:
            value = path.read_bytes()
            text_value = value.decode()
        except (OSError, UnicodeError) as exc:
            raise ACPFileCredentialSyncError(
                "Codex credentials could not be saved to Cloud."
            ) from exc
        if not _is_valid_codex_auth_value(text_value):
            raise ACPFileCredentialSyncError(
                "Local Codex credentials are invalid; the Cloud copy was preserved."
            )
        digest = hashlib.sha256(value).hexdigest()
        changed = digest != expected_digest
        now = time.monotonic()
        if (
            not changed
            and self.last_remote_check > 0
            and now - self.last_remote_check < _CODEX_AUTH_REMOTE_CHECK_INTERVAL
        ):
            return
        attempts = len(_CODEX_AUTH_SYNC_DELAYS) + 1
        for attempt in range(attempts):
            try:
                if changed:
                    _update_codex_auth_source(self.source, text_value, expected_digest)
                else:
                    remote_digest = _touch_codex_auth_source(self.source)
                    if (
                        isinstance(remote_digest, str)
                        and remote_digest != expected_digest
                    ):
                        self._adopt(_get_codex_auth_source(self.source))
                        return
            except httpx.HTTPStatusError as exc:
                if changed and exc.response.status_code == 409:
                    try:
                        self._adopt(_get_codex_auth_source(self.source))
                    except (httpx.HTTPError, ACPFileCredentialSyncError) as remote_exc:
                        if attempt == attempts - 1:
                            raise ACPFileCredentialSyncError(
                                "Codex credentials could not be reconciled with Cloud."
                            ) from remote_exc
                    else:
                        return
                if attempt == attempts - 1:
                    raise ACPFileCredentialSyncError(
                        "Codex credentials could not be saved to Cloud."
                    ) from exc
            except httpx.RequestError as exc:
                if attempt == attempts - 1:
                    raise ACPFileCredentialSyncError(
                        "Codex credentials could not be saved to Cloud."
                    ) from exc
            else:
                break
            if attempt < attempts - 1:
                time.sleep(_CODEX_AUTH_SYNC_DELAYS[attempt])
        if changed:
            try:
                _write_codex_auth_ancestor(path, digest)
            except OSError as exc:
                raise ACPFileCredentialSyncError(
                    "Codex credentials could not be saved to Cloud."
                ) from exc
            self._track_values(text_value)
            self.expected_digest = digest
        self.last_remote_check = now

    def _adopt(self, value: str) -> None:
        path = self.path
        assert path is not None
        if not _is_valid_codex_auth_value(value):
            raise ACPFileCredentialSyncError(
                "Cloud returned invalid Codex credentials; "
                "the local copy was preserved."
            )
        digest = hashlib.sha256(value.encode()).hexdigest()
        try:
            write_secret_file(path, value)
            _write_codex_auth_ancestor(path, digest)
        except OSError as exc:
            raise ACPFileCredentialSyncError(
                "Codex credentials could not be reconciled with Cloud."
            ) from exc
        self._track_values(value)
        self.expected_digest = digest
        self.last_remote_check = time.monotonic()

    def release(self) -> None:
        try:
            _release_codex_auth_source(self.source)
        except httpx.HTTPError as exc:
            raise ACPFileCredentialSyncError(
                "Codex credential source could not be released."
            ) from exc
        self.clear()

    def clear(self) -> None:
        self.path = None
        self.expected_digest = None
        self.last_remote_check = 0.0
        self.registry = None
        self._may_have_changed = False


def _create_codex_auth_lifecycle(
    source: LookupSecret,
) -> ACPFileCredentialLifecycle | None:
    refresh_url = _codex_auth_refresh_url(source)
    return _CodexAuthLifecycle(source, refresh_url) if refresh_url is not None else None


_ACP_FILE_CREDENTIAL_LIFECYCLE_FACTORIES: dict[
    str, Callable[[LookupSecret], ACPFileCredentialLifecycle | None]
] = {
    CODEX_AUTH_SECRET_NAME: _create_codex_auth_lifecycle,
}


def create_file_credential_lifecycle(
    secret_name: str, source: object
) -> ACPFileCredentialLifecycle | None:
    if not isinstance(source, LookupSecret):
        return None
    factory = _ACP_FILE_CREDENTIAL_LIFECYCLE_FACTORIES.get(secret_name)
    return factory(source) if factory is not None else None
