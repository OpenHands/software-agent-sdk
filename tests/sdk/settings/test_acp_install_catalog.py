"""Tests for the dependency-free ACP npm installation catalog."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from openhands.sdk.settings.acp_install_catalog import (
    ACP_INSTALL_CATALOG,
    CLAUDE_AGENT_ACP_VERSION,
    CODEX_ACP_VERSION,
    DEFAULT_PREINSTALLED_ACP_PROVIDERS,
    GEMINI_CLI_VERSION,
    ACPInstallSpec,
    ACPPackagePin,
    _main,
    render_docker_install_plan,
)
from openhands.sdk.settings.acp_providers import ACP_PROVIDERS


CATALOG_PY = __import__(
    "openhands.sdk.settings.acp_install_catalog", fromlist=["__file__"]
).__file__


class TestACPPackagePin:
    def test_pinned_is_name_at_version(self):
        pin = ACPPackagePin(name="@scope/pkg", version="1.2.3")
        assert pin.pinned == "@scope/pkg@1.2.3"

    def test_is_frozen(self):
        pin = ACPPackagePin(name="pkg", version="1.0.0")
        with pytest.raises((AttributeError, TypeError)):
            pin.version = "2.0.0"  # type: ignore[misc]


class TestACPInstallSpecNpxCommand:
    def test_single_package_no_trailing_args(self):
        spec = ACPInstallSpec(
            key="codex",
            packages=(ACPPackagePin("@agentclientprotocol/codex-acp", "1.1.7"),),
            binary_name="codex-acp",
        )
        assert spec.npx_command() == (
            "npx",
            "-y",
            "--prefer-offline",
            "@agentclientprotocol/codex-acp@1.1.7",
        )

    def test_single_package_with_trailing_args(self):
        spec = ACPInstallSpec(
            key="gemini-cli",
            packages=(ACPPackagePin("@google/gemini-cli", "0.46.0"),),
            binary_name="gemini",
            trailing_args=("--acp",),
        )
        assert spec.npx_command() == (
            "npx",
            "-y",
            "--prefer-offline",
            "@google/gemini-cli@0.46.0",
            "--acp",
        )

    def test_multi_package_uses_package_flags_and_entry_binary(self):
        # Mirrors the shape Pi needs: an ACP adapter plus its engine package,
        # both pinned, with the adapter's bin invoked positionally.
        spec = ACPInstallSpec(
            key="pi",
            packages=(
                ACPPackagePin("pi-acp", "0.1.0"),
                ACPPackagePin("@earendil-works/pi-coding-agent", "0.2.0"),
            ),
            binary_name="pi-acp",
        )
        assert spec.npx_command() == (
            "npx",
            "-y",
            "--prefer-offline",
            "--package=pi-acp@0.1.0",
            "--package=@earendil-works/pi-coding-agent@0.2.0",
            "pi-acp",
        )


class TestACPInstallCatalogMatchesACPProviders:
    """ACP_PROVIDERS derives default_command/binary_name from this catalog;
    verify the two stay in lockstep for every npm-installable provider."""

    def test_catalog_keys_match_registered_npm_providers(self):
        assert set(ACP_INSTALL_CATALOG) == set(ACP_PROVIDERS)

    def test_default_commands_are_derived_from_catalog(self):
        for key, info in ACP_PROVIDERS.items():
            assert info.default_command == ACP_INSTALL_CATALOG[key].npx_command()

    def test_binary_names_are_derived_from_catalog(self):
        for key, info in ACP_PROVIDERS.items():
            assert info.binary_name == ACP_INSTALL_CATALOG[key].binary_name

    def test_pinned_versions_unchanged(self):
        assert CLAUDE_AGENT_ACP_VERSION == "0.63.0"
        assert CODEX_ACP_VERSION == "1.1.7"
        assert GEMINI_CLI_VERSION == "0.46.0"


class TestDefaultPreinstalledACPProviders:
    def test_preserves_todays_default_image_contents(self):
        assert DEFAULT_PREINSTALLED_ACP_PROVIDERS == (
            "claude-code",
            "codex",
            "gemini-cli",
        )

    def test_every_default_provider_is_in_the_catalog(self):
        for key in DEFAULT_PREINSTALLED_ACP_PROVIDERS:
            assert key in ACP_INSTALL_CATALOG


class TestRenderDockerInstallPlan:
    def test_default_set(self):
        packages, wrapper_bins = render_docker_install_plan(
            DEFAULT_PREINSTALLED_ACP_PROVIDERS
        )
        assert packages == [
            f"@agentclientprotocol/claude-agent-acp@{CLAUDE_AGENT_ACP_VERSION}",
            f"@agentclientprotocol/codex-acp@{CODEX_ACP_VERSION}",
            f"@google/gemini-cli@{GEMINI_CLI_VERSION}",
        ]
        assert wrapper_bins == ["claude-agent-acp", "codex-acp", "gemini"]

    def test_empty_selection_installs_nothing(self):
        assert render_docker_install_plan([]) == ([], [])

    def test_subset_selection(self):
        packages, wrapper_bins = render_docker_install_plan(["codex"])
        assert packages == [f"@agentclientprotocol/codex-acp@{CODEX_ACP_VERSION}"]
        assert wrapper_bins == ["codex-acp"]

    def test_unknown_key_raises_with_valid_keys_listed(self):
        with pytest.raises(ValueError, match="bogus") as exc_info:
            render_docker_install_plan(["codex", "bogus"])
        message = str(exc_info.value)
        assert "claude-code" in message
        assert "codex" in message
        assert "gemini-cli" in message

    def test_dedupes_shared_packages_preserving_first_seen_order(self):
        shared = ACPPackagePin("@acme/shared-engine", "9.9.9")
        catalog = {
            "a": ACPInstallSpec(key="a", packages=(shared,), binary_name="a-bin"),
            "b": ACPInstallSpec(
                key="b",
                packages=(ACPPackagePin("@acme/b-only", "1.0.0"), shared),
                binary_name="b-bin",
            ),
        }
        packages, wrapper_bins = render_docker_install_plan(["a", "b"], catalog=catalog)
        assert packages == ["@acme/shared-engine@9.9.9", "@acme/b-only@1.0.0"]
        assert wrapper_bins == ["a-bin", "b-bin"]


class TestACPInstallCatalogCLI:
    """The Dockerfile's acp-providers stage COPies this module in and runs it
    directly with the system python3 — exercise that exact entry point."""

    def test_prints_shell_eval_able_plan(self, capsys):
        code = _main(["claude-code,codex"])
        assert code == 0
        out = capsys.readouterr().out
        assert out.splitlines() == [
            'PACKAGES="@agentclientprotocol/claude-agent-acp@'
            f"{CLAUDE_AGENT_ACP_VERSION} @agentclientprotocol/codex-acp@"
            f'{CODEX_ACP_VERSION}"',
            'WRAPPER_BINS="claude-agent-acp codex-acp"',
        ]

    def test_empty_string_installs_nothing(self, capsys):
        code = _main([""])
        assert code == 0
        out = capsys.readouterr().out
        assert out.splitlines() == ['PACKAGES=""', 'WRAPPER_BINS=""']

    def test_unknown_provider_fails_clearly(self, capsys):
        code = _main(["bogus"])
        assert code == 1
        err = capsys.readouterr().err
        assert "bogus" in err

    def test_runs_standalone_with_no_dependencies(self):
        """Regression guard for the Dockerfile's `python3 <file> ...`
        invocation: this must work with nothing but the stdlib on
        sys.path, since the acp-providers stage is a bare python image
        with no OpenHands package (or pydantic) installed.
        """
        result = subprocess.run(
            [sys.executable, "-S", CATALOG_PY, "claude-code"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [
            f'PACKAGES="@agentclientprotocol/claude-agent-acp@{CLAUDE_AGENT_ACP_VERSION}"',
            'WRAPPER_BINS="claude-agent-acp"',
        ]

    def test_output_survives_a_real_shell_eval_multi_package(self):
        """Regression test for the exact bug the Dockerfile hit: `eval`
        re-tokenizes its argument on whitespace, so an unquoted multi-word
        `PACKAGES=a b c` parses as "assign PACKAGES=a, then run command b
        with arg c" (exit 127), not one space-separated assignment. Runs the
        CLI's real stdout through `sh -c 'eval "$PLAN"; ...'`, exactly like
        the Dockerfile's `PLAN=$(...) || exit 1; eval "$PLAN"`.
        """
        plan = subprocess.run(
            [sys.executable, "-S", CATALOG_PY, "claude-code,codex,gemini-cli"],
            capture_output=True,
            text=True,
        ).stdout
        result = subprocess.run(
            ["sh", "-c", 'eval "$PLAN"; echo "$PACKAGES"; echo "$WRAPPER_BINS"'],
            capture_output=True,
            text=True,
            env={**os.environ, "PLAN": plan},
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [
            f"@agentclientprotocol/claude-agent-acp@{CLAUDE_AGENT_ACP_VERSION} "
            f"@agentclientprotocol/codex-acp@{CODEX_ACP_VERSION} "
            f"@google/gemini-cli@{GEMINI_CLI_VERSION}",
            "claude-agent-acp codex-acp gemini",
        ]
