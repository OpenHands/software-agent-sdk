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

The helper is additionally bound to the credential-free URL's protocol and
host. Git asks the helper about whatever host it is about to contact, which
after an HTTP redirect is the redirect target, not the original host; a helper
that answers unconditionally would therefore hand the token to a host the
caller never authorized. The helper reads git's standard ``protocol=``/
``host=`` request from stdin and stays silent unless both match.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit


# Environment variables read by the inline credential helper below. Keeping the
# names in one place documents the contract between the helper script and the
# caller that populates the environment.
GIT_CREDENTIAL_USERNAME_ENV = "OPENHANDS_GIT_USERNAME"
GIT_CREDENTIAL_PASSWORD_ENV = "OPENHANDS_GIT_PASSWORD"
# The scope the helper is bound to. The secret is released only when the
# protocol and host git asks about match these values.
GIT_CREDENTIAL_PROTOCOL_ENV = "OPENHANDS_GIT_ALLOWED_PROTOCOL"
GIT_CREDENTIAL_HOST_ENV = "OPENHANDS_GIT_ALLOWED_HOST"

# ``scheme://userinfo@host/...`` where userinfo excludes ``/`` and ``@`` so the
# match cannot span a path segment or a second ``@``.
_URL_USERINFO_RE = re.compile(r"^(https?://)([^@/]+)@(.+)$")

_DEFAULT_PORTS = {"http": 80, "https": 443}


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


def credential_scope(url: str) -> tuple[str, str]:
    """Return the ``(protocol, host)`` a credential may be released for.

    The host is the lowercase hostname with the scheme's default port removed,
    so ``https://GitHub.com:443/o/r.git`` and ``https://github.com/o/r.git``
    describe the same scope and a redirect that only changes the casing or
    spells out ``:443`` still matches. A non-default port stays part of the
    scope, so a redirect to a different port is a different host.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        # Malformed URL (e.g. an unterminated IPv6 literal). An empty scope
        # never matches, so the helper refuses instead of widening its scope.
        return "", ""
    protocol = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    if port is not None and port != _DEFAULT_PORTS.get(protocol):
        return protocol, f"{hostname}:{port}"
    return protocol, hostname


def credential_helper_args() -> list[str]:
    """Return ``git -c`` arguments enabling a host-bound credential helper.

    The helper reads git's standard credential request (``protocol=``,
    ``host=``, ... up to a blank line) from stdin and releases the credentials
    only when both the protocol and the host match the scope in the
    environment. For anything else -- a different host, a different protocol,
    or a request git makes after an HTTP redirect -- it prints nothing and
    exits 0, which is the credential-helper contract for "no credentials".

    ``store`` and ``erase`` are honoured: the request is consumed and nothing
    is written, since this helper never persists or deletes anything.

    The leading empty ``credential.helper=`` clears any inherited helper, so a
    system-wide helper cannot shadow this one or outlive it. Credentials are
    read from the environment, never from argv, and ``printf`` with a fixed
    format string passes the value verbatim: runs of spaces and backslashes
    survive instead of being collapsed or interpreted by the shell.
    """
    helper = (
        "!f() { "
        'op="$1"; proto=""; host=""; '
        "while IFS= read -r line; do "
        'if [ -z "$line" ]; then break; fi; '
        'case "$line" in '
        'protocol=*) proto="${line#protocol=}" ;; '
        'host=*) host="${line#host=}" ;; '
        "esac; "
        "done; "
        'if [ "$op" != "get" ]; then exit 0; fi; '
        'proto=$(printf "%s" "$proto" | tr "[:upper:]" "[:lower:]"); '
        'host=$(printf "%s" "$host" | tr "[:upper:]" "[:lower:]"); '
        'case "$proto:$host" in '
        'http:*:80) host="${host%:80}" ;; '
        'https:*:443) host="${host%:443}" ;; '
        "esac; "
        'if [ -z "${' + GIT_CREDENTIAL_HOST_ENV + ':-}" ] '
        '|| [ "$proto" != "${' + GIT_CREDENTIAL_PROTOCOL_ENV + ':-}" ] '
        '|| [ "$host" != "${' + GIT_CREDENTIAL_HOST_ENV + '}" ]; then exit 0; fi; '
        'printf "username=%s\\n" "${' + GIT_CREDENTIAL_USERNAME_ENV + '}"; '
        'printf "password=%s\\n" "${' + GIT_CREDENTIAL_PASSWORD_ENV + '}"; '
        "}; f"
    )
    return ["-c", "credential.helper=", "-c", f"credential.helper={helper}"]


def credential_env(
    username: str, password: str, protocol: str, host: str
) -> dict[str, str]:
    """Environment for the helper installed by :func:`credential_helper_args`.

    ``protocol``/``host`` must come from the credential-free URL (see
    :func:`credential_scope`); they bind the helper to the host the caller
    actually authorized.
    """
    return {
        GIT_CREDENTIAL_USERNAME_ENV: username,
        GIT_CREDENTIAL_PASSWORD_ENV: password,
        GIT_CREDENTIAL_PROTOCOL_ENV: protocol,
        GIT_CREDENTIAL_HOST_ENV: host,
    }
