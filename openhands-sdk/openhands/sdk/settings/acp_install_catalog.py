"""ACP npm installation catalog — the single source of truth for each
built-in provider's pinned npm package(s), CLI entry point, and any launch
arguments needed to invoke it.

Deliberately dependency-free (stdlib only, no pydantic): the agent-server
Dockerfile's ``acp-providers`` stage builds from a bare ``python:*-bookworm``
image with no OpenHands package installed, so this file is COPYed into that
stage on its own and executed with the system ``python3`` to render the ACP
payload's npm-install/wrapper plan (see :func:`render_docker_install_plan` and
the ``acp-providers`` stage in
``openhands-agent-server/openhands/agent_server/docker/Dockerfile``). It must
keep working when imported (or run as a script) with nothing beyond the
standard library on ``sys.path``.

:data:`ACP_PROVIDERS` in ``acp_providers.py`` builds each provider's
``default_command``/``binary_name`` from this catalog, so a package/version
bump only needs to happen here.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ACPPackagePin:
    """One pinned npm package (name + exact version)."""

    name: str
    version: str

    @property
    def pinned(self) -> str:
        """The ``name@version`` token passed to ``npm``/``npx``."""
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class ACPInstallSpec:
    """Everything needed to install and launch one provider's ACP CLI via npm.

    Supports one or more independently pinned packages (e.g. Pi's ``pi-acp``
    adapter plus its ``@earendil-works/pi-coding-agent`` engine).
    """

    key: str
    """Provider key, matching
    :class:`~openhands.sdk.settings.acp_providers.ACPProviderInfo.key`."""

    packages: tuple[ACPPackagePin, ...]
    """One or more npm packages to pin and install. Never empty."""

    binary_name: str
    """CLI entry point: the agent-server's pre-installed ``PATH`` wrapper name,
    and (when :attr:`packages` has more than one entry) the positional command
    ``npx`` is told to run."""

    trailing_args: tuple[str, ...] = field(default=())
    """Args appended after the package spec in :meth:`npx_command` (e.g.
    gemini-cli's ``--acp``, opencode's ``acp``). Empty for CLIs that need no
    subcommand to enter ACP mode."""

    min_node_version: str | None = None
    """Highest ``engines.node`` floor across this provider's packages, when it
    exceeds the ecosystem baseline of ``>=20``.

    The agent-server image installs every selected provider under one Node, so
    the Dockerfile's pin has to clear the highest floor here (asserted by
    ``tests/cross/test_agent_server_build_metadata.py``). The ``npx`` fallback
    runs on the *host's* Node instead, where a floor that isn't met surfaces
    only as a cryptic mid-handshake error from the CLI's own dependencies —
    which is what this field exists to name.
    """

    def npx_command(self) -> tuple[str, ...]:
        """The default ``npx``-based launch command for this provider.

        A single package is invoked the ordinary way (``npx <pkg>@<ver>``); two
        or more use ``npx --package=<pkg>@<ver> ... <binary_name>`` so ``npx``
        knows which installed package's bin to run.
        """
        if len(self.packages) == 1:
            package_tokens: tuple[str, ...] = (self.packages[0].pinned,)
            entry: tuple[str, ...] = ()
        else:
            package_tokens = tuple(f"--package={p.pinned}" for p in self.packages)
            entry = (self.binary_name,)
        return (
            "npx",
            "-y",
            "--prefer-offline",
            *package_tokens,
            *entry,
            *self.trailing_args,
        )


# Pinned npm versions for the built-in ACP launchers. A bump here is the only
# edit needed — ACP_PROVIDERS, the Dockerfile install stage, and the
# TypeScript mirror (via check-acp-drift.py --write) all derive from
# ACP_INSTALL_CATALOG below.
CLAUDE_AGENT_ACP_VERSION = "0.63.0"
CODEX_ACP_VERSION = "1.1.7"
GEMINI_CLI_VERSION = "0.46.0"
KIMI_CODE_VERSION = "0.38.0"


ACP_INSTALL_CATALOG: Mapping[str, ACPInstallSpec] = {
    "claude-code": ACPInstallSpec(
        key="claude-code",
        packages=(
            ACPPackagePin(
                "@agentclientprotocol/claude-agent-acp", CLAUDE_AGENT_ACP_VERSION
            ),
        ),
        binary_name="claude-agent-acp",
    ),
    "codex": ACPInstallSpec(
        key="codex",
        packages=(ACPPackagePin("@agentclientprotocol/codex-acp", CODEX_ACP_VERSION),),
        binary_name="codex-acp",
    ),
    "gemini-cli": ACPInstallSpec(
        key="gemini-cli",
        packages=(ACPPackagePin("@google/gemini-cli", GEMINI_CLI_VERSION),),
        binary_name="gemini",
        trailing_args=("--acp",),
    ),
    "kimi-code": ACPInstallSpec(
        key="kimi-code",
        # Scoped package only: the unscoped npm ``kimi-code`` is an unrelated
        # third-party tool that also ships a ``kimi`` bin.
        packages=(ACPPackagePin("@moonshot-ai/kimi-code", KIMI_CODE_VERSION),),
        binary_name="kimi",
        trailing_args=("acp",),
        # @moonshot-ai/kimi-code declares `node >=22.19.0`.
        min_node_version="22.19.0",
    ),
}
"""Every ACP provider installable via npm. Registry membership only makes a
provider *selectable* (``INSTALL_ACP_PROVIDERS``) — see
:data:`DEFAULT_PREINSTALLED_ACP_PROVIDERS` for what actually ships in the
default image."""


DEFAULT_PREINSTALLED_ACP_PROVIDERS: tuple[str, ...] = (
    "claude-code",
    "codex",
    "gemini-cli",
)
"""Provider keys baked into the default agent-server image
(``INSTALL_ACP_PROVIDERS`` default). Kept separate from
:data:`ACP_INSTALL_CATALOG` so registering a new npm provider there does not,
by itself, grow every published image — joining this default is a separate,
deliberate decision per provider (see OpenHands/software-agent-sdk#4820)."""


def render_docker_install_plan(
    keys: Iterable[str],
    catalog: Mapping[str, ACPInstallSpec] = ACP_INSTALL_CATALOG,
) -> tuple[list[str], list[str]]:
    """Resolve provider ``keys`` to (npm packages, wrapper bin names).

    Packages are deduplicated (first-seen order preserved) so two providers
    sharing a dependency only install it once. Raises :class:`ValueError`
    listing the valid keys when ``keys`` contains one not in ``catalog``.
    """
    packages: list[str] = []
    wrapper_bins: list[str] = []
    seen: set[str] = set()
    for key in keys:
        spec = catalog.get(key)
        if spec is None:
            valid = ", ".join(catalog)
            raise ValueError(
                f"Unknown ACP provider {key!r} in INSTALL_ACP_PROVIDERS "
                f"(expected one of: {valid})"
            )
        for pkg in spec.packages:
            if pkg.pinned not in seen:
                seen.add(pkg.pinned)
                packages.append(pkg.pinned)
        wrapper_bins.append(spec.binary_name)
    return packages, wrapper_bins


def _main(argv: list[str] | None = None) -> int:
    """CLI entry point used by the Dockerfile's ``acp-providers`` build stage.

    Prints a shell-``eval``-able plan (``PACKAGES="..."`` / ``WRAPPER_BINS="..."``)
    for the comma-separated provider keys in ``providers`` (empty string installs
    none). Values are double-quoted: without quotes, ``eval`` re-tokenizes each
    line on whitespace, so a multi-package ``PACKAGES=a b c`` would parse as
    "assign PACKAGES=a, then run command b with arg c" instead of one
    space-separated assignment. Package/bin names never contain a ``"``.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "providers", help="Comma-separated ACP provider keys, or '' for none."
    )
    args = parser.parse_args(argv)
    keys = [k for k in (p.strip() for p in args.providers.split(",")) if k]
    try:
        packages, wrapper_bins = render_docker_install_plan(keys)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f'PACKAGES="{" ".join(packages)}"')
    print(f'WRAPPER_BINS="{" ".join(wrapper_bins)}"')
    return 0


if __name__ == "__main__":
    sys.exit(_main())
