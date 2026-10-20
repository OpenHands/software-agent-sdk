"""Regression tests: organization skill clones must not leak credentials.

The organization-skills path used to hand a pre-authenticated URL straight to
``git clone``, which put the token in the process argument vector and wrote it
into the clone's ``.git/config``. The first fix moved the credentials into an
environment-backed git credential helper, but that helper answered for *any*
host git asked about -- including the target of an HTTP redirect -- and emitted
the password through an unquoted ``echo``, which collapsed runs of spaces and
interpreted backslash escapes.

These tests exercise the real clone path against local git HTTP servers and
assert on actual bytes: the argv the subprocess received, the on-disk
``.git/config``, the captured stderr, and the ``Authorization`` header a
redirect target actually received.
"""

from __future__ import annotations

import base64
import http.server
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote

import pytest

from openhands.agent_server.git_credentials import (
    credential_env,
    credential_helper_args,
    credential_scope,
    split_url_credentials,
)
from openhands.agent_server.skills_service import (
    _clone_org_repo,
    _kill_process_group,
    load_org_skills_from_url,
)


# A fake token. Not a real credential; chosen to be unique so a substring check
# in argv, config, or stderr cannot match by accident.
FAKE_TOKEN = "ghp_FAKEtokenVALUEforTESTSonly0123456789"
FAKE_USER = "x-access-token"


def _basic(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


def _git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    return result.stdout


def _make_bare_repo(git_root: Path, name: str) -> Path:
    """Create a bare repo named ``<name>.git`` under ``git_root``.

    The scratch work tree lives beside ``git_root`` so it is not itself served
    by ``git http-backend``.
    """
    work = git_root.parent / f"work-{name}"
    work.mkdir()
    _git("init", "-q", "-b", "main", cwd=work)
    _git("config", "user.email", "t@example.com", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    (work / "skills").mkdir()
    (work / "skills" / f"{name}-skill.md").write_text(
        f"---\nname: {name}-skill\ndescription: {name} skill\n---\nbody\n"
    )
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "init", cwd=work)
    bare = git_root / f"{name}.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(work), str(bare)],
        check=True,
        capture_output=True,
    )
    return bare


def _serve(handler_cls: type) -> tuple[int, http.server.ThreadingHTTPServer]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return port, server


def _git_backend_handler(
    repos: Path,
    *,
    expected_auth: str,
    auth_prefixes: tuple[str, ...] = (),
    seen: list[tuple[str, str]] | None = None,
) -> type:
    """Build a handler serving ``repos`` through ``git http-backend``.

    Paths under ``auth_prefixes`` require basic auth equal to ``expected_auth``;
    every request is appended to ``seen`` as ``(path, authorization)`` so a test
    can prove what a server actually received.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # Keep test output clean.
        def log_message(self, format: str, *args: object) -> None:
            pass

        def _needs_auth(self, path: str) -> bool:
            return any(path.startswith(prefix) for prefix in auth_prefixes)

        def _authorized(self) -> bool:
            return self.headers.get("Authorization", "") == expected_auth

        def _run_backend(self, path: str, body: bytes) -> tuple[int, str, bytes]:
            if self._needs_auth(path) and not self._authorized():
                return 401, "text/plain", b"auth required"

            env = {
                **os.environ,
                "GIT_PROJECT_ROOT": str(repos),
                "GIT_HTTP_EXPORT_ALL": "1",
                "PATH_INFO": path,
                "REQUEST_METHOD": self.command,
                "QUERY_STRING": self.path.split("?", 1)[1] if "?" in self.path else "",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": str(len(body)),
                "REMOTE_USER": FAKE_USER,
                "HTTP_AUTHORIZATION": self.headers.get("Authorization", ""),
            }
            proc = subprocess.run(
                ["git", "http-backend"],
                input=body,
                capture_output=True,
                env=env,
            )
            # git http-backend emits CGI headers, then the body after a blank
            # line. The Content-Type distinguishes the ref advertisement
            # (info/refs) from the packfile result, and git rejects a mismatch.
            head, _, payload = proc.stdout.partition(b"\r\n\r\n")
            status = 200
            content_type = "application/octet-stream"
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"status:"):
                    status = int(line.split(b":", 1)[1].strip().split()[0])
                elif line.lower().startswith(b"content-type:"):
                    content_type = line.split(b":", 1)[1].strip().decode()
            return status, content_type, payload

        def _respond(self, path: str, body: bytes) -> None:
            if seen is not None:
                seen.append((path, self.headers.get("Authorization", "")))
            status, content_type, payload = self._run_backend(path, body)
            self.send_response(status)
            if status == 401:
                self.send_header("WWW-Authenticate", 'Basic realm="git"')
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):  # noqa: N802 - http.server API
            self._respond(self.path.split("?", 1)[0], b"")

        def do_POST(self):  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            self._respond(self.path.split("?", 1)[0], body)

    return Handler


@pytest.fixture(scope="module")
def git_server(tmp_path_factory: pytest.TempPathFactory):
    """A local git HTTP server requiring basic auth, plus a public repo.

    Serves two repositories over one listener:
      * ``/private.git`` — requires basic auth (username ``x-access-token``,
        password = ``FAKE_TOKEN``).
      * ``/public.git``  — served without auth.
    """
    root = tmp_path_factory.mktemp("gitserver")
    repos = root / "repos"
    repos.mkdir()

    _make_bare_repo(repos, "private")
    _make_bare_repo(repos, "public")

    handler = _git_backend_handler(
        repos,
        expected_auth=_basic(FAKE_USER, FAKE_TOKEN),
        auth_prefixes=("/private.git",),
    )
    port, server = _serve(handler)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def redirect_chain(tmp_path: Path):
    """Server A 301-redirects every request to server B.

    B is the real git backend and requires basic auth. Both servers record what
    they received, so a test can prove A was reached and whether the token was
    ever presented to B. A and B differ by port, i.e. they are different hosts
    for credential scope.
    """
    repos = tmp_path / "repos"
    repos.mkdir()
    _make_bare_repo(repos, "private")

    seen_b: list[tuple[str, str]] = []
    b_handler = _git_backend_handler(
        repos,
        expected_auth=_basic(FAKE_USER, FAKE_TOKEN),
        auth_prefixes=("/",),
        seen=seen_b,
    )
    b_port, b_server = _serve(b_handler)
    b_base = f"127.0.0.1:{b_port}"

    seen_a: list[tuple[str, str]] = []

    class RedirectHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def _redirect(self) -> None:
            seen_a.append((self.path, self.headers.get("Authorization", "")))
            self.send_response(301)
            self.send_header("Location", f"http://{b_base}{self.path}")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):  # noqa: N802 - http.server API
            self._redirect()

        def do_POST(self):  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            self._redirect()

    a_port, a_server = _serve(RedirectHandler)
    try:
        yield f"127.0.0.1:{a_port}", b_base, seen_a, seen_b
    finally:
        a_server.shutdown()
        a_server.server_close()
        b_server.shutdown()
        b_server.server_close()


# --------------------------------------------------------------------------
# Unit tests for the URL/credential helpers
# --------------------------------------------------------------------------


class TestSplitUrlCredentials:
    def test_splits_user_and_token(self):
        url, creds = split_url_credentials(
            "https://x-access-token:TOKEN@github.com/org/repo.git"
        )
        assert url == "https://github.com/org/repo.git"
        assert creds == ("x-access-token", "TOKEN")

    def test_token_only_userinfo_pairs_conventional_username(self):
        url, creds = split_url_credentials("https://TOKEN@github.com/org/repo.git")
        assert url == "https://github.com/org/repo.git"
        assert creds == ("x-access-token", "TOKEN")

    def test_no_credentials_returns_none(self):
        url, creds = split_url_credentials("https://github.com/org/repo.git")
        assert url == "https://github.com/org/repo.git"
        assert creds is None

    def test_placeholder_userinfo_is_preserved(self):
        raw = "https://x-token-auth:${TOKEN}@gitlab.com/org/repo.git"
        url, creds = split_url_credentials(raw)
        assert url == raw
        assert creds is None

    def test_url_encoded_password_is_decoded(self):
        url, creds = split_url_credentials(
            "https://user:p%40ss%3Aword@github.com/org/repo.git"
        )
        assert url == "https://github.com/org/repo.git"
        assert creds == ("user", "p@ss:word")

    def test_ssh_url_untouched(self):
        raw = "git@github.com:org/repo.git"
        assert split_url_credentials(raw) == (raw, None)


class TestCredentialScope:
    def test_default_https_port_is_stripped(self):
        assert credential_scope("https://github.com/o/r.git") == (
            "https",
            "github.com",
        )
        assert credential_scope("https://github.com:443/o/r.git") == (
            "https",
            "github.com",
        )

    def test_default_http_port_is_stripped(self):
        assert credential_scope("http://example.com/o/r.git") == (
            "http",
            "example.com",
        )
        assert credential_scope("http://example.com:80/o/r.git") == (
            "http",
            "example.com",
        )

    def test_non_default_port_is_kept(self):
        assert credential_scope("https://example.com:8443/o/r.git") == (
            "https",
            "example.com:8443",
        )
        assert credential_scope("http://127.0.0.1:8000/o/r.git") == (
            "http",
            "127.0.0.1:8000",
        )

    def test_host_case_is_normalized(self):
        assert credential_scope("https://GitHub.COM/o/r.git") == (
            "https",
            "github.com",
        )

    def test_query_and_fragment_are_not_part_of_the_scope(self):
        assert credential_scope("https://github.com/o/r.git?x=1#frag") == (
            "https",
            "github.com",
        )

    def test_different_ports_are_different_scopes(self):
        assert credential_scope("http://127.0.0.1:8001/o/r.git") != credential_scope(
            "http://127.0.0.1:8002/o/r.git"
        )

    def test_unparseable_url_yields_empty_scope(self):
        # An empty host never matches, so the helper refuses; the point is that
        # this does not raise and does not widen the scope.
        assert credential_scope("not a url") == ("", "")


def _helper_script() -> str:
    """Extract the shell body git will run for the configured helper."""
    prefix = "credential.helper=!"
    for arg in credential_helper_args():
        if arg.startswith(prefix):
            return arg[len(prefix) :]
    raise AssertionError("no shell credential helper configured")


def _run_helper(operation: str, request: str, env_extra: dict[str, str]) -> str:
    """Run the helper exactly as git does: ``sh -c '<body> <operation>'``."""
    script = _helper_script()
    proc = subprocess.run(
        ["sh", "-c", f"{script} {operation}"],
        input=request,
        capture_output=True,
        text=True,
        env={**os.environ, **env_extra},
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


class TestCredentialHelper:
    """The helper must answer only for the protocol/host it was bound to."""

    def _env(self, protocol: str = "https", host: str = "github.com") -> dict[str, str]:
        return credential_env("x-access-token", "TOKEN", protocol, host)

    def test_get_returns_secret_for_allowed_host(self):
        out = _run_helper(
            "get",
            "protocol=https\nhost=github.com\n\n",
            self._env(),
        )
        assert out == "username=x-access-token\npassword=TOKEN\n"

    def test_get_refuses_other_host(self):
        out = _run_helper(
            "get",
            "protocol=https\nhost=evil.example\n\n",
            self._env(),
        )
        assert out == ""

    def test_get_refuses_on_protocol_mismatch(self):
        out = _run_helper(
            "get",
            "protocol=http\nhost=github.com\n\n",
            self._env(protocol="https"),
        )
        assert out == ""

    def test_get_matches_host_case_insensitively(self):
        out = _run_helper(
            "get",
            "protocol=https\nhost=GitHub.COM\n\n",
            self._env(host="github.com"),
        )
        assert out == "username=x-access-token\npassword=TOKEN\n"

    def test_get_normalizes_default_https_port(self):
        out = _run_helper(
            "get",
            "protocol=https\nhost=github.com:443\n\n",
            self._env(host="github.com"),
        )
        assert out == "username=x-access-token\npassword=TOKEN\n"

    def test_get_normalizes_default_http_port(self):
        out = _run_helper(
            "get",
            "protocol=http\nhost=example.com:80\n\n",
            self._env(protocol="http", host="example.com"),
        )
        assert out == "username=x-access-token\npassword=TOKEN\n"

    def test_get_refuses_different_non_default_port(self):
        out = _run_helper(
            "get",
            "protocol=http\nhost=127.0.0.1:8001\n\n",
            self._env(protocol="http", host="127.0.0.1:8000"),
        )
        assert out == ""

    def test_get_matches_non_default_port(self):
        out = _run_helper(
            "get",
            "protocol=http\nhost=127.0.0.1:8000\n\n",
            self._env(protocol="http", host="127.0.0.1:8000"),
        )
        assert out == "username=x-access-token\npassword=TOKEN\n"

    def test_store_and_erase_have_no_side_effects(self):
        for operation in ("store", "erase"):
            out = _run_helper(
                operation,
                "protocol=https\nhost=github.com\n\n",
                self._env(),
            )
            assert out == ""

    def test_empty_host_env_never_matches(self):
        env = {
            "OPENHANDS_GIT_USERNAME": "x-access-token",
            "OPENHANDS_GIT_PASSWORD": "TOKEN",
            "OPENHANDS_GIT_ALLOWED_PROTOCOL": "https",
            "OPENHANDS_GIT_ALLOWED_HOST": "",
        }
        out = _run_helper("get", "protocol=https\nhost=github.com\n\n", env)
        assert out == ""

    def test_helper_references_env_not_literals(self):
        joined = " ".join(credential_helper_args())
        assert "credential.helper=" in joined
        # The helper must reference environment variables, never a literal
        # secret, so nothing sensitive can appear in argv.
        assert "OPENHANDS_GIT_PASSWORD" in joined
        assert credential_env("u", "p", "https", "github.com") == {
            "OPENHANDS_GIT_USERNAME": "u",
            "OPENHANDS_GIT_PASSWORD": "p",
            "OPENHANDS_GIT_ALLOWED_PROTOCOL": "https",
            "OPENHANDS_GIT_ALLOWED_HOST": "github.com",
        }


# --------------------------------------------------------------------------
# End-to-end clone tests against the local auth-enforcing server
# --------------------------------------------------------------------------


class TestOrgCloneCredentialSafety:
    def test_private_repo_clones_with_authentication(self, git_server, tmp_path):
        """A private repo authenticates through the credential helper."""
        dest = tmp_path / "clone"
        ok = _clone_org_repo(
            f"{git_server}/private.git".replace(
                "http://", f"http://{FAKE_USER}:{FAKE_TOKEN}@"
            ),
            dest,
            "acme",
        )
        assert ok is True
        assert (dest / "skills" / "private-skill.md").is_file()

    def test_public_repo_clones_without_credential(self, git_server, tmp_path):
        """A credential-free URL must still clone (no needless credential)."""
        dest = tmp_path / "clone"
        assert _clone_org_repo(f"{git_server}/public.git", dest, "acme") is True
        assert (dest / "skills" / "public-skill.md").is_file()

    def test_token_absent_from_subprocess_argv(self, git_server, tmp_path, monkeypatch):
        """The token must never appear in the argv handed to subprocess."""
        seen: list[list[str]] = []
        real_run = subprocess.run

        def spy(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd and cmd[0] == "git":
                seen.append(list(cmd))
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr("openhands.agent_server.skills_service.subprocess.run", spy)
        dest = tmp_path / "clone"
        assert (
            _clone_org_repo(
                f"http://{FAKE_USER}:{FAKE_TOKEN}@{git_server.removeprefix('http://')}"
                "/private.git",
                dest,
                "acme",
            )
            is True
        )
        assert seen, "expected the clone command to be captured"
        for cmd in seen:
            for arg in cmd:
                assert FAKE_TOKEN not in arg, f"token leaked into argv: {arg!r}"

    def test_token_absent_from_git_config(self, git_server, tmp_path):
        """git records the remote URL; it must be credential-free."""
        dest = tmp_path / "clone"
        assert (
            _clone_org_repo(
                f"http://{FAKE_USER}:{FAKE_TOKEN}@{git_server.removeprefix('http://')}"
                "/private.git",
                dest,
                "acme",
            )
            is True
        )
        config = (dest / ".git" / "config").read_text()
        assert FAKE_TOKEN not in config
        assert FAKE_USER not in config
        assert "@" not in config.split("url = ", 1)[1].splitlines()[0]

    def test_old_tokenized_clone_persists_token_to_config(self, git_server, tmp_path):
        """Proves the vulnerability the fix removes.

        Handing a tokenized URL straight to ``git clone`` (the previous
        behaviour) writes the token into the clone's ``.git/config``, where it
        survives on disk. This is version-independent, unlike stderr echoing.
        """
        dest = tmp_path / "vulnerable"
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                f"http://{FAKE_USER}:{FAKE_TOKEN}@"
                f"{git_server.removeprefix('http://')}/private.git",
                str(dest),
            ],
            check=True,
            capture_output=True,
        )
        config = (dest / ".git" / "config").read_text()
        assert FAKE_TOKEN in config, (
            "expected the old tokenized clone to persist the token; "
            "the regression test needs updating if git no longer does this"
        )

    def test_token_absent_from_git_config_after_fix(self, git_server, tmp_path):
        """The fixed path writes the same clone with a credential-free config."""
        dest = tmp_path / "fixed"
        assert (
            _clone_org_repo(
                f"http://{FAKE_USER}:{FAKE_TOKEN}@"
                f"{git_server.removeprefix('http://')}/private.git",
                dest,
                "acme",
            )
            is True
        )
        config = (dest / ".git" / "config").read_text()
        assert FAKE_TOKEN not in config

    def test_auth_failure_returns_false_without_raising(self, git_server, tmp_path):
        """The production path degrades to an empty result, leaking nothing."""
        dest = tmp_path / "clone"
        ok = _clone_org_repo(
            f"http://{FAKE_USER}:WRONGpasswordVALUE000@"
            f"{git_server.removeprefix('http://')}/private.git",
            dest,
            "acme",
        )
        assert ok is False

    def test_token_absent_from_logs_on_failure(self, git_server, tmp_path, caplog):
        """Failure logging must not include the token."""
        import logging

        dest = tmp_path / "clone"
        with caplog.at_level(logging.DEBUG):
            _clone_org_repo(
                f"http://{FAKE_USER}:{FAKE_TOKEN}@"
                f"{git_server.removeprefix('http://')}/missing.git",
                dest,
                "acme",
            )
        assert FAKE_TOKEN not in caplog.text

    def test_load_org_skills_uses_safe_path_end_to_end(self, git_server, tmp_path):
        """The org-skills entry point itself no longer leaks the token.

        This is the regression test for the original bypass: the entry point is
        called directly, as the router does, with a tokenized URL.
        """
        skills = load_org_skills_from_url(
            org_repo_url=(
                f"http://{FAKE_USER}:{FAKE_TOKEN}@"
                f"{git_server.removeprefix('http://')}/private.git"
            ),
            org_name="acme",
            working_dir=str(tmp_path),
        )
        names = {s.name for s in skills}
        assert "private-skill" in names
        # The temp clone is removed in the finally block, so no on-disk
        # credential remains; assert nothing survived under the working dir.
        leftovers = list(tmp_path.glob("_org_skills_*/**/.git/config"))
        assert leftovers == []

    def test_no_credential_temp_files_after_failure(self, git_server, tmp_path):
        """A failed clone leaves no accessible credential-bearing files."""
        load_org_skills_from_url(
            org_repo_url=(
                f"http://{FAKE_USER}:WRONGpasswordVALUE000@"
                f"{git_server.removeprefix('http://')}/private.git"
            ),
            org_name="acme",
            working_dir=str(tmp_path),
        )
        for path in tmp_path.rglob("*"):
            if path.is_file():
                assert "WRONGpasswordVALUE000" not in path.read_text(errors="ignore")

    def test_repeated_clone_is_idempotent(self, git_server, tmp_path):
        """A second clone into a fresh dir still keeps config credential-free."""
        for i in range(2):
            dest = tmp_path / f"clone{i}"
            assert (
                _clone_org_repo(
                    f"http://{FAKE_USER}:{FAKE_TOKEN}@"
                    f"{git_server.removeprefix('http://')}/private.git",
                    dest,
                    "acme",
                )
                is True
            )
            config = (dest / ".git" / "config").read_text()
            assert FAKE_TOKEN not in config

    def test_existing_clone_update_does_not_store_token(self, git_server, tmp_path):
        """Fetching into an existing clone must not write the token to config."""
        dest = tmp_path / "clone"
        assert (
            _clone_org_repo(
                f"http://{FAKE_USER}:{FAKE_TOKEN}@"
                f"{git_server.removeprefix('http://')}/private.git",
                dest,
                "acme",
            )
            is True
        )
        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            **credential_env(
                FAKE_USER,
                FAKE_TOKEN,
                *credential_scope(
                    f"http://{git_server.removeprefix('http://')}/private.git"
                ),
            ),
        }
        subprocess.run(
            ["git", *credential_helper_args(), "fetch", "--depth", "1", "origin"],
            cwd=dest,
            check=True,
            capture_output=True,
            env=env,
        )
        config = (dest / ".git" / "config").read_text()
        assert FAKE_TOKEN not in config

    def test_special_characters_in_credentials(self):
        """URL-encoded special characters are decoded and handled safely."""
        url, creds = split_url_credentials(
            "https://user:pa%2Fss%3Aword@github.com/o/r.git"
        )
        assert url == "https://github.com/o/r.git"
        assert creds == ("user", "pa/ss:word")
        # And the clean URL never carries the raw secret.
        assert "pa/ss:word" not in url


# --------------------------------------------------------------------------
# Redirect safety: the token must not follow a redirect to another host
# --------------------------------------------------------------------------


class TestRedirectSafety:
    def test_redirect_to_other_host_does_not_leak_token(self, redirect_chain, tmp_path):
        """A 301 from host A to host B must not hand the token to B.

        This exercises the real clone path: git follows the redirect, then asks
        the credential helper about *B*. The helper is bound to A, so it must
        answer nothing and B must never receive the token.
        """
        a_base, b_base, seen_a, seen_b = redirect_chain
        dest = tmp_path / "clone"
        ok = _clone_org_repo(
            f"http://{FAKE_USER}:{FAKE_TOKEN}@{a_base}/private.git",
            dest,
            "acme",
        )

        # A must have been reached, otherwise the redirect was never exercised
        # and the test would pass vacuously.
        assert seen_a, "expected the redirecting host to be contacted"
        # B must have been contacted too: git followed the redirect.
        assert seen_b, "expected the redirect target to be contacted"

        # The token must never have been presented to B, on any request.
        for path, authorization in seen_b:
            assert FAKE_TOKEN not in authorization, (
                f"token leaked to the redirect target on {path!r}"
            )
            assert _basic(FAKE_USER, FAKE_TOKEN) not in authorization
            assert "Basic" not in authorization

        # B refused the unauthenticated request, so the clone must fail rather
        # than succeed with a leaked credential.
        assert ok is False


# --------------------------------------------------------------------------
# Password fidelity: special characters must arrive byte-for-byte
# --------------------------------------------------------------------------


class TestPasswordFidelity:
    @pytest.fixture
    def special_password_server(self, tmp_path: Path):
        """A server whose private repo accepts only the exact special password."""
        repos = tmp_path / "repos"
        repos.mkdir()
        _make_bare_repo(repos, "special")

        # Two consecutive spaces and a literal backslash: the characters the
        # old ``echo password=$VAR`` path mangled.
        password = "a  b\\c"
        handler = _git_backend_handler(
            repos,
            expected_auth=_basic(FAKE_USER, password),
            auth_prefixes=("/special.git",),
        )
        port, server = _serve(handler)
        try:
            yield f"http://127.0.0.1:{port}", password
        finally:
            server.shutdown()
            server.server_close()

    def test_password_with_spaces_and_backslashes_is_delivered_verbatim(
        self, special_password_server, tmp_path
    ):
        """A password of ``a  b\\c`` must authenticate, not become ``a b``.

        The URL carries the value percent-encoded; after decoding it must reach
        the server unchanged. The clone only succeeds if the exact bytes match,
        so a mangling regression fails here.
        """
        base, password = special_password_server
        encoded = quote(password, safe="")
        assert encoded == "a%20%20b%5Cc"

        dest = tmp_path / "clone"
        ok = _clone_org_repo(
            f"http://{FAKE_USER}:{encoded}@{base.removeprefix('http://')}/special.git",
            dest,
            "acme",
        )
        assert ok is True, (
            "the password with consecutive spaces and a backslash was not "
            "delivered verbatim"
        )
        assert (dest / "skills" / "special-skill.md").is_file()

    def test_helper_emits_special_password_verbatim(self):
        """The helper's own output preserves runs of spaces and backslashes."""
        password = "  lead  and\\trail  "
        env = credential_env("u", password, "https", "github.com")
        out = _run_helper("get", "protocol=https\nhost=github.com\n\n", env)
        assert out == f"username=u\npassword={password}\n"


# --------------------------------------------------------------------------
# Timeout cleanup: no credential-bearing descendant may outlive the clone
# --------------------------------------------------------------------------


def _pids_with_env_containing(secret: str) -> set[int]:
    """Return PIDs whose environment still contains *secret*.

    Reads ``/proc/<pid>/environ`` directly, so a process that inherited the
    credential from the clone's environment is found even when its command line
    says nothing about it. A zombie exposes no environ; zombies are handled by
    the direct child being reaped in the implementation.
    """
    hits: set[int] = set()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return hits
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            data = Path(f"/proc/{entry}/environ").read_bytes()
        except (OSError, PermissionError):
            continue
        if secret.encode() in data:
            hits.add(int(entry))
    return hits


class TestTimeoutCleanup:
    """A timed-out clone must not leave credential-bearing descendants behind.

    ``git clone`` over HTTP forks ``git-remote-http``, which inherits
    ``OPENHANDS_GIT_PASSWORD``. The old ``subprocess.run(timeout=...)`` killed
    only the direct child, leaving those helpers alive with the secret readable
    in ``/proc/<pid>/environ``.
    """

    @pytest.fixture
    def stalled_http_server(self):
        """An HTTP endpoint that accepts requests and never responds."""
        received = threading.Event()
        release = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:
                pass

            def _stall(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length:
                    self.rfile.read(length)
                received.set()
                # Hold the connection open until the test releases it.
                release.wait(60)

            def do_GET(self):  # noqa: N802 - http.server API
                self._stall()

            def do_POST(self):  # noqa: N802 - http.server API
                self._stall()

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            yield port, received
        finally:
            release.set()
            server.shutdown()
            server.server_close()

    def test_timeout_reaps_process_group_without_leaking_secret(
        self, stalled_http_server, tmp_path: Path, monkeypatch
    ):
        """The whole process tree dies before the function returns."""
        port, received = stalled_http_server
        token = "P1REGRESSIONtokenVALUEonlyforTESTS0123456789"

        # Shorten the production timeout so the real code path (including the
        # process-group teardown) is exercised without waiting 120 seconds.
        monkeypatch.setattr(
            "openhands.agent_server.skills_service._CLONE_TIMEOUT_SECONDS", 1.0
        )

        spawn_kwargs: list[dict[str, object]] = []
        real_popen = subprocess.Popen

        def spy_popen(*args, **kwargs):
            spawn_kwargs.append(dict(kwargs))
            return real_popen(*args, **kwargs)

        monkeypatch.setattr(
            "openhands.agent_server.skills_service.subprocess.Popen", spy_popen
        )

        result: dict[str, object] = {}
        observed: set[int] = set()

        def worker() -> None:
            result["ok"] = _clone_org_repo(
                f"http://{FAKE_USER}:{token}@127.0.0.1:{port}/org/repo.git",
                tmp_path / "clone",
                "acme",
            )

        worker_thread = threading.Thread(target=worker)
        try:
            worker_thread.start()

            # Wait until git is actually talking to the endpoint, so the
            # descendants exist. This is an event, not an arbitrary sleep.
            assert received.wait(timeout=30), "git never contacted the endpoint"

            # Record every credential-bearing descendant while they are alive.
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                found = _pids_with_env_containing(token)
                if found:
                    observed |= found
                    break
                time.sleep(0.05)

            worker_thread.join(timeout=60)
            assert not worker_thread.is_alive(), "clone did not return on timeout"

            # 1. The clone must report failure on timeout.
            assert result.get("ok") is False

            # 2. Git must have been started as its own session/group leader.
            assert spawn_kwargs, "expected the clone to spawn a process"
            assert spawn_kwargs[0].get("start_new_session") is True, (
                "git must own a process group so the whole tree can be signalled"
            )

            # 3. No descendant may still carry the credential once the function
            #    has returned. The implementation waits for teardown, so this
            #    needs no sleep of its own.
            assert observed, "test did not observe a credential-bearing descendant"
            survivors = _pids_with_env_containing(token)
            assert survivors == set(), (
                f"credential-bearing descendants survived the timeout: {survivors}"
            )
        finally:
            # Never leave an orphan or a stalled handler behind, even on failure.
            worker_thread.join(timeout=5)
            for pid in _pids_with_env_containing(token):
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass

    def test_descendant_that_ignores_sigterm_is_killed(self):
        """A descendant that traps SIGTERM must still be SIGKILLed.

        The escalation must key off the *group*, not the direct child. Here the
        direct child exits promptly on SIGTERM while a descendant ignores it; an
        implementation that only SIGKILLs when ``process.wait`` times out returns
        with the descendant alive, still holding the credential.
        """
        token = "SIGTERMignoreREGRESSIONtoken0123456789"
        # The outer shell exits on SIGTERM; the backgrounded inner shell ignores
        # it and keeps sleeping in the same process group.
        script = (
            f"export OPENHANDS_GIT_PASSWORD={token}; "
            "sh -c 'trap \"\" TERM; sleep 300' & "
            "child=$!; "
            "trap 'exit 0' TERM; "
            "echo started; "
            "wait $child"
        )
        process = subprocess.Popen(
            ["sh", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=dict(os.environ),
            start_new_session=True,
        )
        try:
            assert process.stdout is not None
            assert process.stdout.readline().strip() == b"started"

            # The descendant must be alive with the credential before we signal.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not _pids_with_env_containing(token):
                time.sleep(0.05)
            assert _pids_with_env_containing(token), "descendant never started"

            _kill_process_group(process)

            survivors = _pids_with_env_containing(token)
            assert survivors == set(), (
                f"a SIGTERM-ignoring descendant survived the kill: {survivors}"
            )
        finally:
            try:
                process.kill()
            except OSError:
                pass
            for pid in _pids_with_env_containing(token):
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass
