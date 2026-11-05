"""Secret values in the user info of a plugin source URL.

A token is put into the URL a placeholder names. Tokens such as Bitbucket Data
Center HTTP access tokens are base64 and can contain ``/``, which ends the
URL's authority when inserted as it is. The fetches here go to a local git
server that only serves clients holding its token.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest

from openhands.sdk.plugin import PluginSourceError, preflight_plugin
from openhands.sdk.plugin.strict_fetch import (
    _classify_git_error,
    _expand_source_and_ref,
)


_GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
}

TRICKY_VALUES = [
    "BBDCtoken/with/slashes",
    "token+plus",
    "token=padding==",
    "user@example.com",
    "colon:inside",
    "percent%25and%",
    "plainSafeToken123",
]


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**os.environ, **_GIT_ENV},
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class _PrivateGitServer:
    """Serves the bare repositories under ``root`` to clients with ``token``."""

    def __init__(self, root: Path, token: str) -> None:
        self.root = root
        self.token = token
        handler = type("_Handler", (_GitHandler,), {"server_state": self})
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def host(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"{host}:{port}"

    def __enter__(self) -> _PrivateGitServer:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


class _GitHandler(BaseHTTPRequestHandler):
    server_state: _PrivateGitServer

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        self._serve()

    def do_POST(self) -> None:  # noqa: N802
        self._serve()

    def _serve(self) -> None:
        header = self.headers.get("Authorization", "")
        password = None
        if header.startswith("Basic "):
            decoded = base64.b64decode(header.removeprefix("Basic ")).decode()
            password = decoded.partition(":")[2]
        if password != self.server_state.token:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="private"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        path, _, query = self.path.partition("?")
        env = {
            **os.environ,
            **_GIT_ENV,
            "GIT_PROJECT_ROOT": str(self.server_state.root),
            "GIT_HTTP_EXPORT_ALL": "1",
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "REQUEST_METHOD": self.command,
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
        }
        if protocol := self.headers.get("Git-Protocol"):
            env["GIT_PROTOCOL"] = protocol
        output = subprocess.run(
            ["git", "http-backend"], input=body, env=env, capture_output=True
        ).stdout
        head, _, payload = output.partition(b"\r\n\r\n")
        status = 200
        headers: list[tuple[str, str]] = []
        for line in head.decode().splitlines():
            key, _, value = line.partition(":")
            if key.lower() == "status":
                status = int(value.split()[0])
            elif key:
                headers.append((key, value.strip()))
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _make_bare_repo(root: Path) -> str:
    work = root / "work"
    (work / ".plugin").mkdir(parents=True)
    (work / ".plugin" / "plugin.json").write_text(
        json.dumps({"name": "private-plugin", "version": "1.0.0"})
    )
    _git("init", "-q", "-b", "main", cwd=work)
    _git("add", "-A", cwd=work)
    _git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "-m",
        "plugin",
        cwd=work,
    )
    _git("clone", "-q", "--bare", str(work), str(root / "plugin.git"))
    return _git("rev-parse", "HEAD", cwd=work)


@pytest.fixture
def private_repo(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    yield tmp_path, _make_bare_repo(tmp_path)


@pytest.mark.parametrize("value", TRICKY_VALUES)
def test_user_info_values_are_percent_encoded(value: str):
    source = "https://${USER}:${TOKEN}@git.example.com/scm/proj/repo.git"

    expanded, _ref, masked = _expand_source_and_ref(
        source, None, {"USER": value, "TOKEN": value}
    )

    encoded = quote(value, safe="")
    assert expanded == f"https://{encoded}:{encoded}@git.example.com/scm/proj/repo.git"
    assert value in masked
    assert encoded in masked


def test_placeholders_outside_the_user_info_are_unchanged():
    source = "https://x-access-token:${TOKEN}@git.example.com/${ORG}/repo.git"

    expanded, ref, _masked = _expand_source_and_ref(
        source, "${BRANCH}", {"TOKEN": "a/b", "ORG": "acme/sub", "BRANCH": "x/y"}
    )

    assert expanded == "https://x-access-token:a%2Fb@git.example.com/acme/sub/repo.git"
    assert ref == "x/y"


def test_sources_without_http_user_info_are_expanded_as_before():
    expanded, _ref, masked = _expand_source_and_ref(
        "file://x-access-token:${TOKEN}@/srv/repo", None, {"TOKEN": "a/b"}
    )

    assert expanded == "file://x-access-token:a/b@/srv/repo"
    assert "a/b" in masked


@pytest.mark.parametrize("value", TRICKY_VALUES)
def test_preflight_clones_with_a_token_that_needs_encoding(
    private_repo: tuple[Path, str], value: str
):
    root, sha = private_repo

    with _PrivateGitServer(root, value) as server:
        with preflight_plugin(
            source=f"http://x-access-token:${{TOKEN}}@{server.host}/plugin.git",
            secrets={"TOKEN": value},
            allow_local=True,
        ) as checked:
            assert checked.resolved_ref == sha
            assert checked.source.endswith("@" + server.host + "/plugin.git")
            assert "${TOKEN}" in checked.source


@pytest.mark.parametrize("value", TRICKY_VALUES)
def test_a_rejected_token_is_masked_in_both_forms(
    private_repo: tuple[Path, str], value: str
):
    root, _sha = private_repo

    with _PrivateGitServer(root, "the-right-token") as server:
        with pytest.raises(PluginSourceError) as exc_info:
            with preflight_plugin(
                source=f"http://x-access-token:${{TOKEN}}@{server.host}/plugin.git",
                secrets={"TOKEN": value},
                allow_local=True,
            ):
                pass

    assert exc_info.value.code == "auth_failed"
    assert value not in str(exc_info.value)
    assert quote(value, safe="") not in str(exc_info.value)


@pytest.mark.parametrize(
    "stderr",
    [
        "fatal: unable to access 'https://git.example.com:abc/r.git/': URL "
        "rejected: Port number was not a decimal number between 0 and 65535",
        "fatal: unable to access 'https://x/r.git/': URL using bad/illegal "
        "format or missing URL",
    ],
)
def test_malformed_urls_are_permanent(stderr: str):
    assert _classify_git_error(stderr, "source_unavailable") == "invalid_source"


def test_a_malformed_source_fails_as_invalid_source():
    with pytest.raises(PluginSourceError) as exc_info:
        with preflight_plugin(
            source="http://127.0.0.1:notaport/plugin.git", allow_local=True
        ):
            pass

    assert exc_info.value.code == "invalid_source"
    assert exc_info.value.status_code == 422
