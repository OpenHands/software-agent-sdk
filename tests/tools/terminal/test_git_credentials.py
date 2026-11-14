"""Saved git-provider token secrets authenticate git/gh without naming the secret."""

import os
import shutil
import tempfile

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.llm import LLM
from openhands.tools.terminal import TerminalAction
from openhands.tools.terminal.git_credentials import git_credential_env
from openhands.tools.terminal.impl import TerminalExecutor


# These run a real POSIX shell command line, which PowerShell cannot parse.
requires_posix_git = pytest.mark.skipif(
    os.name != "posix" or shutil.which("git") is None,
    reason="needs a POSIX shell with git",
)


TOKEN = "ghp_SavedTokenValue123"

# Isolated from the host's git config so only the helper under test can answer.
CREDENTIAL_FILL = (
    "printf 'protocol=https\\nhost={host}\\n\\n' | "
    "GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 "
    "git credential fill"
)


def _registry(**secrets: str) -> SecretRegistry:
    registry = SecretRegistry()
    registry.update_secrets(secrets)
    return registry


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "cd repo && git fetch",
        "GIT_TERMINAL_PROMPT=0 git ls-remote origin",
        "/usr/bin/git status",
        "gh pr create --fill",
        "(glab mr list)",
    ],
)
def test_git_cli_commands_get_token_and_helper(command):
    env = git_credential_env(command, _registry(GITHUB_TOKEN=TOKEN))

    assert env["GITHUB_TOKEN"] == TOKEN
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "credential.https://github.com.helper"
    assert TOKEN not in env["GIT_CONFIG_VALUE_0"]


@pytest.mark.parametrize(
    "command", ["echo hello", "cat .gitignore", "python digit.py", "ls ~/.github"]
)
def test_non_git_commands_get_nothing(command):
    assert git_credential_env(command, _registry(GITHUB_TOKEN=TOKEN)) == {}


def test_no_provider_token_gets_nothing():
    assert git_credential_env("git push", _registry(API_KEY="x")) == {}


def test_github_and_gitlab_tokens_each_get_a_helper():
    env = git_credential_env(
        "git push", _registry(GH_TOKEN=TOKEN, GITLAB_TOKEN="glpat-abc")
    )

    assert env["GH_TOKEN"] == TOKEN
    assert env["GITLAB_TOKEN"] == "glpat-abc"
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "credential.https://github.com.helper"
    assert env["GIT_CONFIG_KEY_1"] == "credential.https://gitlab.com.helper"


def test_exported_token_is_tracked_for_masking():
    registry = _registry(GITHUB_TOKEN=TOKEN)

    git_credential_env("git push", registry)

    assert TOKEN not in registry.mask_secrets_in_output(f"token {TOKEN}")


@requires_posix_git
@pytest.mark.parametrize(
    ("secrets", "host", "username"),
    [
        ({"GITHUB_TOKEN": TOKEN}, "github.com", "x-access-token"),
        ({"GITLAB_TOKEN": TOKEN}, "gitlab.com", "oauth2"),
    ],
)
def test_git_receives_saved_token_without_naming_it(secrets, host, username):
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(model="gpt-4o-mini", api_key=SecretStr("k"), usage_id="test-llm")
        conversation = Conversation(
            agent=Agent(llm=llm, tools=[]),
            workspace=temp_dir,
            persistence_dir=temp_dir,
            secrets=secrets,
        )
        executor = TerminalExecutor(working_dir=temp_dir, terminal_type="subprocess")
        try:
            result = executor(
                TerminalAction(command=CREDENTIAL_FILL.format(host=host)),
                conversation=conversation,
            )

            assert result.exit_code == 0, result.text
            assert f"username={username}" in result.text
            assert "password=<secret-hidden>" in result.text
            assert TOKEN not in result.text
        finally:
            executor.close()
            conversation.close()


@requires_posix_git
def test_git_without_provider_token_is_unchanged():
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(model="gpt-4o-mini", api_key=SecretStr("k"), usage_id="test-llm")
        conversation = Conversation(
            agent=Agent(llm=llm, tools=[]),
            workspace=temp_dir,
            persistence_dir=temp_dir,
            secrets={"API_KEY": "unrelated"},
        )
        executor = TerminalExecutor(working_dir=temp_dir, terminal_type="subprocess")
        try:
            result = executor(
                TerminalAction(command=CREDENTIAL_FILL.format(host="github.com")),
                conversation=conversation,
            )

            assert result.exit_code != 0
            assert "password=" not in result.text
        finally:
            executor.close()
            conversation.close()
