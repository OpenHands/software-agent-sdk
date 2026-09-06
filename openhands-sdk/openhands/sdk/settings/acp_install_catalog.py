"""ACP installation catalog — the single source of truth for each built-in
provider's pinned distribution, CLI entry point, and any launch arguments
needed to invoke it.

Two install flavours are described, one per packaging ecosystem the built-in
providers ship in:

- :class:`ACPInstallSpec` — one or more pinned npm packages, launched with
  ``npx`` and preinstallable into the agent-server image.
- :class:`ACPGitCheckoutInstallSpec` — a project installed from a shallow
  checkout of a pinned git ref. On-demand only;
  :func:`render_docker_install_plan` rejects it.

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
from typing import TypeGuard


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

    def launch_command(self) -> tuple[str, ...]:
        """Alias of :meth:`npx_command`, shared with
        :class:`ACPGitCheckoutInstallSpec`."""
        return self.npx_command()

    @property
    def pinned_version(self) -> str | None:
        """The version a correctly-installed CLI reports over ACP.

        For an npm provider the pin *is* that version. The first package is
        the one that answers: a multi-package provider lists its ACP adapter
        first, and the adapter is what runs.
        """
        return self.packages[0].version


@dataclass(frozen=True)
class ACPGitPin:
    """One project pinned to an exact git ref."""

    url: str
    """Clone URL."""

    ref: str
    """Tag or commit SHA to check out. A tag is only as immutable as its
    publisher makes it, which is why
    :attr:`ACPGitCheckoutInstallSpec.reported_version` exists to catch one
    that moved."""

    @property
    def pinned(self) -> str:
        """``<url>@<ref>``, for logs and cache-directory naming."""
        return f"{self.url}@{self.ref}"


@dataclass(frozen=True)
class ACPGitCheckoutInstallSpec:
    """Everything needed to install and launch one provider's ACP CLI from a
    git checkout of a pinned ref.

    The counterpart of :class:`ACPInstallSpec` for a provider that is neither
    an npm package nor installable as a Python *distribution*. Hermes is the
    motivating case, and the reason the obvious ``uvx --from git+…@<ref>``
    shape is not what this renders:

    - Its ``setup.py`` overrides ``bdist_wheel``/``sdist`` to raise outside a
      Nix build, so every PEP 517 wheel path — ``uvx``, ``uv tool install``,
      ``pip install`` — fails by design.
    - The guard is not incidental: ``[tool.setuptools.packages.find]`` never
      packaged ``skills/``, ``locales/`` or ``optional-mcps/``, so a wheel
      forced through with ``HERMES_NIX_BUILD=1`` installs and runs while
      silently missing them.
    - ``uv``'s git backend resolves a ref then re-fetches by commit SHA, and
      that fetch does not complete against a repository advertising ~10^5
      refs (upstream's own installer documents the same GitHub throttle and
      works around it with retries plus a blobless partial clone).

    A shallow, single-branch clone of the tag sidesteps all three: it needs no
    wheel, materialises the bundled assets, and asks the server for one ref
    rather than the full advertisement. The editable install ``uv sync``
    performs is the path upstream supports.

    Nothing here reaches the published agent-server images —
    :func:`render_docker_install_plan` rejects this flavour outright, and the
    checkout is materialised at runtime under the conversation's durable
    cache root.
    """

    key: str
    """Provider key, matching
    :class:`~openhands.sdk.settings.acp_providers.ACPProviderInfo.key`."""

    source: ACPGitPin
    """Pinned git ref the checkout is cloned from."""

    binary_name: str
    """Console script the project installs into its venv
    (``[project.scripts]``), and the name resolved off ``PATH`` at launch."""

    extras: tuple[str, ...] = field(default=())
    """Optional-dependency groups the ACP entry point needs. Hermes keeps
    ``agent-client-protocol`` in an ``acp`` extra, so a default ``uv sync``
    produces a CLI whose ``--check`` fails on ``No module named 'acp'``."""

    trailing_args: tuple[str, ...] = field(default=())
    """Args appended after the console script name (e.g. a subcommand that
    enters ACP mode). Empty for scripts that speak ACP directly."""

    reported_version: str | None = None
    """The version the CLI reports over ACP when built from :attr:`source`.

    A git ref and a project version are separate namespaces — Hermes tags
    releases by date (``v2026.8.31``) while its package version is ``0.21.0``
    — so unlike an npm pin, the ref cannot be compared against what the
    running server says. Recording the expected value keeps that check alive:
    it catches a moved tag, and it fails the live probe when the ref is
    bumped without re-verifying the record."""

    def launch_command(self) -> tuple[str, ...]:
        """The console script, resolved off ``PATH``.

        The checkout's venv is not on ``PATH`` until
        :class:`~openhands.sdk.agent.ACPAgent` prepares it and prepends its
        ``bin`` to the subprocess environment, which is also what keeps this
        command static: the checkout lives under a per-installation directory
        the registry cannot know at import time.
        """
        return (self.binary_name, *self.trailing_args)

    @property
    def pinned_version(self) -> str | None:
        """The version a correctly-installed CLI reports over ACP.

        :attr:`reported_version` rather than the ref, because the two are
        separate namespaces here — see that field.
        """
        return self.reported_version

    def clone_command(self, dest: str) -> tuple[str, ...]:
        """Shallow, single-branch clone of :attr:`source` into ``dest``.

        ``--depth 1 --single-branch`` is what keeps this viable on a
        repository with a very large ref advertisement: with a specific
        ``--branch`` git asks for one ref prefix instead of the full listing,
        which is the difference between seconds and minutes.
        """
        return (
            "git",
            "clone",
            "--depth",
            "1",
            "--single-branch",
            "--branch",
            self.source.ref,
            self.source.url,
            dest,
        )

    def sync_command(self) -> tuple[str, ...]:
        """Editable install of the checkout, at its own locked versions.

        There is deliberately no ``requires_python`` counterpart to
        :attr:`ACPInstallSpec.min_node_version` here: ``uv`` reads the
        project's own ``requires-python`` and selects — or downloads — a
        matching interpreter, so the floor is enforced by the installer
        rather than by anything this catalog could restate.
        """
        extras = tuple(arg for extra in self.extras for arg in ("--extra", extra))
        return ("uv", "sync", "--frozen", *extras)


# Pinned npm versions for the built-in ACP launchers. A bump here is the only
# edit needed — ACP_PROVIDERS, the Dockerfile install stage, and the
# TypeScript mirror (via check-acp-drift.py --write) all derive from
# ACP_INSTALL_CATALOG below.
CLAUDE_AGENT_ACP_VERSION = "0.63.0"
CODEX_ACP_VERSION = "1.10.0"
GEMINI_CLI_VERSION = "0.46.0"
KIMI_CODE_VERSION = "0.38.0"

# Pi is the one provider with two independent pins: pi-acp only adapts ACP and
# spawns a separately installed `pi` engine off PATH.
PI_ACP_VERSION = "0.0.33"
PI_CODING_AGENT_VERSION = "0.83.0"

OPENCODE_VERSION = "1.18.23"

# Hermes is the one provider not distributed through npm. Its releases are
# CalVer git tags on the upstream repository; PyPI's `hermes-agent` is a
# third-party upload (no project URLs, no license metadata, and the repo has
# no publish workflow), and the `curl | bash` installer takes no version flag,
# so neither can be pinned to a reviewed release.
HERMES_REPO_URL = "https://github.com/NousResearch/hermes-agent"
HERMES_REF = "v2026.8.31"


ACPAnyInstallSpec = ACPInstallSpec | ACPGitCheckoutInstallSpec
"""Either install flavour, as the catalog and its consumers see them."""


ACP_INSTALL_CATALOG: Mapping[str, ACPAnyInstallSpec] = {
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
    "pi": ACPInstallSpec(
        key="pi",
        # Adapter first: the live conformance probe compares ``packages[0]``'s
        # version against the ``agentInfo.version`` the server reports, which
        # is pi-acp's, not the engine's.
        packages=(
            ACPPackagePin("pi-acp", PI_ACP_VERSION),
            ACPPackagePin("@earendil-works/pi-coding-agent", PI_CODING_AGENT_VERSION),
        ),
        binary_name="pi-acp",
        # @earendil-works/pi-coding-agent (and its undici dependency) declare
        # `node >=22.19.0`. Below it the pi-acp adapter still starts, so the
        # ACP handshake succeeds and the failure only lands at `session/new`
        # as "Cannot call write after a stream was destroyed" — the engine
        # having already died on `webidl.util.markAsUncloneable is not a
        # function`.
        min_node_version="22.19.0",
    ),
    "opencode": ACPInstallSpec(
        key="opencode",
        # The npm package is a 7.8KB shim whose postinstall pulls the matching
        # per-platform compiled binary (~184MB unpacked) from an
        # optionalDependency, so `--ignore-scripts` leaves a stub that errors.
        packages=(ACPPackagePin("opencode-ai", OPENCODE_VERSION),),
        # The shim's npm bin is ``opencode``, not the package basename.
        binary_name="opencode",
        trailing_args=("acp",),
        # No `engines` field on opencode-ai or its platform packages: the CLI
        # is a compiled Bun binary and only its postinstall runs on Node.
    ),
    "hermes": ACPGitCheckoutInstallSpec(
        key="hermes",
        source=ACPGitPin(url=HERMES_REPO_URL, ref=HERMES_REF),
        # `hermes-acp` and `hermes acp` reach the same entry point; the
        # dedicated console script avoids paying for the `hermes` CLI's
        # subcommand dispatch and its argument grammar.
        binary_name="hermes-acp",
        extras=("acp",),
        reported_version="0.21.0",
    ),
}
"""Every built-in ACP provider and how its CLI is installed. Registry
membership only makes a provider *selectable* (``INSTALL_ACP_PROVIDERS``) —
see :data:`DEFAULT_PREINSTALLED_ACP_PROVIDERS` for what actually ships in the
default image."""


def is_npm_spec(spec: ACPAnyInstallSpec) -> TypeGuard[ACPInstallSpec]:
    """``True`` when ``spec`` describes npm packages (:class:`ACPInstallSpec`).

    Named predicates rather than bare ``isinstance`` at each call site, so the
    catalog can grow a third flavour without every consumer having to learn
    its class. They narrow, so the flavour-specific fields stay type-checked.
    """
    return isinstance(spec, ACPInstallSpec)


def is_git_checkout_spec(
    spec: ACPAnyInstallSpec,
) -> TypeGuard[ACPGitCheckoutInstallSpec]:
    """``True`` when ``spec`` is installed from a git checkout."""
    return isinstance(spec, ACPGitCheckoutInstallSpec)


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
    catalog: Mapping[str, ACPAnyInstallSpec] = ACP_INSTALL_CATALOG,
) -> tuple[list[str], list[str]]:
    """Resolve provider ``keys`` to (npm packages, wrapper bin names).

    Packages are deduplicated (first-seen order preserved) so two providers
    sharing a dependency only install it once. Raises :class:`ValueError`
    listing the valid keys when ``keys`` contains one not in ``catalog``, or
    naming the provider when it is not npm-installable.
    """
    packages: list[str] = []
    wrapper_bins: list[str] = []
    seen: set[str] = set()
    for key in keys:
        spec = catalog.get(key)
        if spec is None:
            valid = ", ".join(k for k, s in catalog.items() if is_npm_spec(s))
            raise ValueError(
                f"Unknown ACP provider {key!r} in INSTALL_ACP_PROVIDERS "
                f"(expected one of: {valid})"
            )
        if not is_npm_spec(spec):
            raise ValueError(
                f"ACP provider {key!r} in INSTALL_ACP_PROVIDERS is not "
                "npm-installable, and the acp-providers build stage installs "
                "only npm packages. It is launched on demand instead — leave "
                "it out of INSTALL_ACP_PROVIDERS."
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
