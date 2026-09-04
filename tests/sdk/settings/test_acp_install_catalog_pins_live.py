"""Live installability checks for ACP_INSTALL_CATALOG's pinned npm versions.

Runs ``npm view <pkg>@<version>`` against the real npm registry so a yanked
version or a security deprecation is caught here instead of breaking the next
agent-server image build. Credential-free; needs ``npm`` and network access to
the registry (skipped when unavailable). See OpenHands/software-agent-sdk#4830
P0-3.

Deselected from the default run via the ``acp_live`` marker (see
``tests/sdk/agent/test_acp_conformance.py`` for why): the SDK's default test
job is a required merge check, and a live registry dependency must not be
able to block unrelated PRs. Run explicitly with ``pytest -m acp_live``; CI
runs it in the separate, non-required ``acp-live-tests`` job.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess

import pytest

from openhands.sdk.settings.acp_install_catalog import ACP_INSTALL_CATALOG


def _npm_registry_reachable(timeout: float = 3.0) -> bool:
    try:
        socket.create_connection(("registry.npmjs.org", 443), timeout=timeout).close()
        return True
    except OSError:
        return False


requires_npm = pytest.mark.skipif(
    shutil.which("npm") is None or not _npm_registry_reachable(),
    reason="npm not available, or npm registry unreachable",
)

pytestmark = pytest.mark.acp_live

_ALL_PINS = [
    pytest.param(spec.key, pkg.name, pkg.version, id=f"{spec.key}:{pkg.pinned}")
    for spec in ACP_INSTALL_CATALOG.values()
    for pkg in spec.packages
]


def _npm_view_version_deprecated(pinned: str) -> tuple[str, str | None]:
    """Return ``(resolved_version, deprecation_message_or_None)`` for a
    ``name@version`` npm spec, raising ``AssertionError`` if it doesn't
    resolve (yanked, typo'd, never published)."""
    result = subprocess.run(
        ["npm", "view", pinned, "version", "deprecated", "--json"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise AssertionError(
            f"npm view {pinned!r} produced unparseable output "
            f"(exit={result.returncode}): stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        ) from None
    if isinstance(data, dict) and "error" in data:
        raise AssertionError(f"npm view {pinned!r} failed: {data['error']}")
    if isinstance(data, str):
        return data, None
    return data["version"], data.get("deprecated")


@requires_npm
@pytest.mark.parametrize("provider_key,package_name,pinned_version", _ALL_PINS)
def test_pinned_version_resolves_and_is_not_deprecated(
    provider_key: str, package_name: str, pinned_version: str
) -> None:
    pinned = f"{package_name}@{pinned_version}"
    resolved_version, deprecated = _npm_view_version_deprecated(pinned)
    assert resolved_version == pinned_version, (
        f"npm resolved {pinned!r} to version {resolved_version!r}, expected "
        f"{pinned_version!r} — the registry's tag resolution disagrees with "
        "the exact pin"
    )
    assert deprecated is None, (
        f"{pinned!r} (provider={provider_key}) is deprecated on npm: "
        f"{deprecated!r} — bump ACP_INSTALL_CATALOG to a non-deprecated version"
    )


def _npm_view_bins(pinned: str) -> set[str]:
    """Return the executable names ``name@version`` declares in its ``bin`` field.

    npm accepts either a string (one bin, named after the package) or an
    object mapping bin name to path.
    """
    result = subprocess.run(
        ["npm", "view", pinned, "bin", "--json"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if not result.stdout.strip():
        return set()
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise AssertionError(
            f"npm view {pinned!r} bin produced unparseable output "
            f"(exit={result.returncode}): stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        ) from None
    if isinstance(data, dict):
        return set(data)
    if isinstance(data, str):
        return {pinned.rsplit("@", 1)[0].rsplit("/", 1)[-1]}
    return set()


@requires_npm
@pytest.mark.parametrize(
    "spec", ACP_INSTALL_CATALOG.values(), ids=list(ACP_INSTALL_CATALOG)
)
def test_binary_name_is_declared_by_one_of_the_pinned_packages(spec) -> None:
    """``binary_name`` must be a bin some pinned package actually declares.

    The image build's own guard only covers providers a given build installs,
    so a registry-only provider's ``binary_name`` is otherwise validated
    nowhere — and a wrong one silently costs it the pinned-binary path
    (``resolve_acp_command`` keeps the npx invocation) or, when preinstalled,
    ships a wrapper pointing at nothing.
    """
    declared: set[str] = set()
    for pkg in spec.packages:
        declared |= _npm_view_bins(pkg.pinned)
    assert spec.binary_name in declared, (
        f"{spec.key}: binary_name {spec.binary_name!r} is not declared by any "
        f"pinned package; npm reports {sorted(declared)} for "
        f"{[p.pinned for p in spec.packages]}"
    )
