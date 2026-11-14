"""Let git and git-provider CLIs authenticate with a saved provider token secret.

The terminal exports a secret only when the command text names it, so a plain
``git push`` or ``gh pr create`` never sees a saved ``GITHUB_TOKEN``. When a
command runs ``git``, ``gh`` or ``glab`` and a provider token secret is
registered, this exports that token plus a command-scoped git credential helper
(via ``GIT_CONFIG_COUNT``/``GIT_CONFIG_KEY_n``/``GIT_CONFIG_VALUE_n``) that
reads it from the environment. No git config file is written, and helpers the
user already configured are consulted first.
"""

import re
from dataclasses import dataclass
from typing import Final

from openhands.sdk.conversation.secret_registry import SecretRegistry


@dataclass(frozen=True)
class _GitHost:
    url: str
    username: str
    secret_names: tuple[str, ...]


_GIT_HOSTS: Final[tuple[_GitHost, ...]] = (
    _GitHost("https://github.com", "x-access-token", ("GITHUB_TOKEN", "GH_TOKEN")),
    _GitHost("https://gitlab.com", "oauth2", ("GITLAB_TOKEN",)),
)

_GIT_CLI_RE: Final = re.compile(r"(?:^|[\s;&|(`/])(?:git|gh|glab)(?=$|[\s;&|)`])")


def _credential_helper(secret_name: str, username: str) -> str:
    # git appends the operation ("get", "store", "erase") as $1.
    return (
        f'!f() {{ test "$1" = get && test -n "${secret_name}" && '
        f'echo "username={username}" && echo "password=${secret_name}"; }}; f'
    )


def git_credential_env(command: str, registry: SecretRegistry) -> dict[str, str]:
    """Environment that lets ``command``'s git/gh/glab calls use saved tokens."""
    if not _GIT_CLI_RE.search(command):
        return {}

    env: dict[str, str] = {}
    helpers: list[tuple[str, str]] = []
    for host in _GIT_HOSTS:
        name = next(
            (n for n in host.secret_names if n in registry.secret_sources), None
        )
        if name is None:
            continue
        token = registry.get_env_vars_for_keys([name])
        if not token:
            continue
        env.update(token)
        helpers.append(
            (f"credential.{host.url}.helper", _credential_helper(name, host.username))
        )

    if helpers:
        env["GIT_CONFIG_COUNT"] = str(len(helpers))
        for i, (key, value) in enumerate(helpers):
            env[f"GIT_CONFIG_KEY_{i}"] = key
            env[f"GIT_CONFIG_VALUE_{i}"] = value
    return env
