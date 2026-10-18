"""Tests for git utils base-ref resolution (``get_valid_ref``)."""

import subprocess
import tempfile
from pathlib import Path

from openhands.sdk.git.utils import (
    GIT_CREDENTIAL_PASSWORD_ENV,
    GIT_CREDENTIAL_USERNAME_ENV,
    GIT_EMPTY_TREE_HASH,
    credential_env,
    credential_helper_args,
    get_valid_ref,
    split_url_credentials,
)


def run_bash_command(command: str, cwd: str) -> subprocess.CompletedProcess:
    """Run a bash command in the specified directory."""
    return subprocess.run(
        command,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_get_valid_ref_purposes_diverge_for_committed_no_remote_repo():
    """The display purpose resolves HEAD for a committed repo without a
    remote (so the Diff view gets git-status semantics), while the default
    export purpose keeps resolving the empty tree — workspace-export patches
    must stay reconstructable from a fresh clone, so the display policy must
    not leak into it."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Arrange — a repo with a commit and no remote.
        run_bash_command("git init -b main", temp_dir)
        run_bash_command("git config user.name 'Test User'", temp_dir)
        run_bash_command("git config user.email 'test@example.com'", temp_dir)
        (Path(temp_dir) / "a.txt").write_text("a")
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'base'", temp_dir)
        head_sha = run_bash_command("git rev-parse HEAD", temp_dir).stdout.strip()

        # Act
        export_ref = get_valid_ref(temp_dir)
        display_ref = get_valid_ref(temp_dir, purpose="display")

        # Assert
        assert export_ref == GIT_EMPTY_TREE_HASH
        assert display_ref == head_sha


# -- credential handling -------------------------------------------------------


def test_split_url_credentials_extracts_token():
    """Inline credentials are split off and the URL is left clean."""
    url, creds = split_url_credentials(
        "https://x-access-token:ghu_TOKEN@github.com/o/r.git"
    )
    assert url == "https://github.com/o/r.git"
    assert creds == ("x-access-token", "ghu_TOKEN")


def test_split_url_credentials_token_only_userinfo():
    """A bare token becomes the password with a conventional username."""
    url, creds = split_url_credentials("https://ghu_TOKEN@github.com/o/r.git")
    assert url == "https://github.com/o/r.git"
    assert creds == ("x-access-token", "ghu_TOKEN")


def test_split_url_credentials_no_credentials():
    """A credential-free URL is returned unchanged with no credentials."""
    url, creds = split_url_credentials("https://github.com/o/r.git")
    assert url == "https://github.com/o/r.git"
    assert creds is None


def test_split_url_credentials_preserves_placeholder():
    """``${VAR}`` userinfo is expanded later, so it must survive untouched."""
    original = "https://x-token-auth:${TOKEN}@host/r.git"
    url, creds = split_url_credentials(original)
    assert url == original
    assert creds is None


def test_split_url_credentials_percent_decoded():
    """Percent-encoded userinfo is decoded before reaching the helper."""
    url, creds = split_url_credentials("https://user%40corp:p%40ss@host/r.git")
    assert url == "https://host/r.git"
    assert creds == ("user@corp", "p@ss")


def test_credential_helper_args_clears_inherited_helper():
    """The helper list must first clear any inherited helper."""
    args = credential_helper_args()
    assert args[:2] == ["-c", "credential.helper="]
    assert args[2] == "-c"
    assert args[3].startswith("credential.helper=")
    # The secret is read from the environment, never embedded in the helper.
    assert "ghu_" not in args[3]


def test_credential_env_carries_secret_out_of_argv():
    """Credentials travel through the environment, not the command line."""
    env = credential_env("x-access-token", "ghu_SECRET")
    assert env[GIT_CREDENTIAL_USERNAME_ENV] == "x-access-token"
    assert env[GIT_CREDENTIAL_PASSWORD_ENV] == "ghu_SECRET"

