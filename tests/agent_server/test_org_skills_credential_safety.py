"""Regression tests: organization skill clones must not leak credentials.

The organization-skills path used to hand a pre-authenticated URL straight to
``git clone``, which put the token in the process argument vector and wrote it
into the clone's ``.git/config``. These tests exercise the real clone path
against a local git HTTP server that requires HTTP basic auth, and assert on
actual bytes: the argv the subprocess received, the on-disk ``.git/config``,
and the captured stderr.
"""

from __future__ import annotations

import base64
import http.server
import os
import socket
import subprocess
import threading
from pathlib import Path

import pytest

from openhands.agent_server.git_credentials import (
    credential_env,
    credential_helper_args,
    split_url_credentials,
)
from openhands.agent_server.skills_service import (
    _clone_org_repo,
    load_org_skills_from_url,
)


# A fake token. Not a real credential; chosen to be unique so a substring check
# in argv, config, or stderr cannot match by accident.
FAKE_TOKEN = "ghp_FAKEtokenVALUEforTESTSonly0123456789"
FAKE_USER = "x-access-token"


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

    def make_bare(name: str) -> Path:
        work = root / f"work-{name}"
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
        bare = repos / f"{name}.git"
        subprocess.run(
            ["git", "clone", "-q", "--bare", str(work), str(bare)],
            check=True,
            capture_output=True,
        )
        return bare

    make_bare("private")
    make_bare("public")

    expected_auth = (
        "Basic " + base64.b64encode(f"{FAKE_USER}:{FAKE_TOKEN}".encode()).decode()
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # Keep test output clean.
        def log_message(self, format: str, *args: object) -> None:
            pass

        def _authorized(self) -> bool:
            return self.headers.get("Authorization", "") == expected_auth

        def _run_backend(self, path: str, body: bytes) -> tuple[int, str, bytes]:
            # Reject an unauthenticated request to the private repo the same way
            # a real host would: 401 with a WWW-Authenticate challenge.
            if path.startswith("/private.git") and not self._authorized():
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

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


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

    def test_credential_helper_reads_from_env_only(self):
        args = credential_helper_args()
        joined = " ".join(args)
        assert "credential.helper=" in joined
        # The helper must reference environment variables, never a literal
        # secret, so nothing sensitive can appear in argv.
        assert "OPENHANDS_GIT_PASSWORD" in joined
        assert credential_env("u", "p") == {
            "OPENHANDS_GIT_USERNAME": "u",
            "OPENHANDS_GIT_PASSWORD": "p",
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
            **credential_env(FAKE_USER, FAKE_TOKEN),
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

    def test_special_characters_in_credentials(self, git_server, tmp_path):
        """URL-encoded special characters are decoded and handled safely."""
        url, creds = split_url_credentials(
            "https://user:pa%2Fss%3Aword@github.com/o/r.git"
        )
        assert url == "https://github.com/o/r.git"
        assert creds == ("user", "pa/ss:word")
        # And the clean URL never carries the raw secret.
        assert "pa/ss:word" not in url
