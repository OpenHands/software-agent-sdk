"""Credential-safe git invocation helpers for the agent server.

Organization skill repositories arrive from the app-server as pre-authenticated
URLs whose userinfo carries a token, e.g.
``https://x-access-token:TOKEN@github.com/org/.openhands.git``. Handing such a
URL straight to ``git clone`` leaks the secret twice:

* it becomes part of the process argument vector, readable by any local user
  through ``ps`` for the lifetime of the clone;
* git records the remote URL verbatim in the clone's ``.git/config``, so the
  token survives on disk in the cache and in any archive built from it.

These helpers split the inline credentials out of the URL and hand them to an
environment-backed git credential helper instead. The secret then travels only
through the child process environment: it never reaches argv and never lands in
a repository's ``.git/config``, because git only ever sees the credential-free
URL.
"""

from __future__ import annotations

import re
from urllib.parse import unquote


# Environment variables read by the inline credential helper below. Keeping the
# names in one place documents the contract between the helper script and the
# caller that populates the environment.
GIT_CREDENTIAL_USERNAME_ENV = "OPENHANDS_GIT_USERNAME"
GIT_CREDENTIAL_PASSWORD_ENV = "OPENHANDS_GIT_PASSWORD"

# ``scheme://userinfo@host/...`` where userinfo excludes ``/`` and ``@`` so the
# match cannot span a path segment or a second ``@``.
_URL_USERINFO_RE = re.compile(r"^(https?://)([^@/]+)@(.+)$")


def split_url_credentials(url: str) -> tuple[str, tuple[str, str] | None]:
    """Split inline URL credentials from the URL itself.

    ``https://x-access-token:TOKEN@github.com/o/r.git`` becomes
    ``("https://github.com/o/r.git", ("x-access-token", "TOKEN"))``.

    Returns ``(url, None)`` when there is no inline userinfo, or when the
    userinfo holds a ``${VAR}`` placeholder: a placeholder is not itself a
    secret, so it is left untouched for a later expansion step to resolve.
    """
    match = _URL_USERINFO_RE.match(url)
    if not match:
        return url, None

    scheme, userinfo, remainder = match.groups()
    if "${" in userinfo:
        return url, None

    username, sep, password = userinfo.partition(":")
    if not sep:
        # Token-only userinfo such as ``https://TOKEN@host``. Git treats the
        # whole userinfo as a username, so pair the token with a conventional
        # username that GitHub and GitLab accept for token authentication.
        username, password = "x-access-token", userinfo
    return f"{scheme}{remainder}", (unquote(username), unquote(password))


def credential_helper_args() -> list[str]:
    """Return ``git -c`` arguments enabling an environment-backed helper.

    The helper reads the credentials from the environment rather than from the
    URL, so the secret never reaches argv or ``.git/config``. The leading empty
    ``credential.helper=`` clears any inherited helper, so a system-wide helper
    cannot shadow this one or outlive it.
    """
    helper = (
        "!f() { "
        f"echo username=${GIT_CREDENTIAL_USERNAME_ENV}; "
        f"echo password=${GIT_CREDENTIAL_PASSWORD_ENV}; "
        "}; f"
    )
    return ["-c", "credential.helper=", "-c", f"credential.helper={helper}"]


def credential_env(username: str, password: str) -> dict[str, str]:
    """Environment for the helper installed by :func:`credential_helper_args`."""
    return {
        GIT_CREDENTIAL_USERNAME_ENV: username,
        GIT_CREDENTIAL_PASSWORD_ENV: password,
    }
