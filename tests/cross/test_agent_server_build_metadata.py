import re
from pathlib import Path

from openhands.sdk.settings.acp_providers import (
    CLAUDE_AGENT_ACP_VERSION,
    CODEX_ACP_VERSION,
    GEMINI_CLI_VERSION,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "server.yml"
AGENT_SERVER_DOCKERFILE = (
    REPO_ROOT
    / "openhands-agent-server"
    / "openhands"
    / "agent_server"
    / "docker"
    / "Dockerfile"
)
AGENT_SERVER_SPEC = (
    REPO_ROOT
    / "openhands-agent-server"
    / "openhands"
    / "agent_server"
    / "agent-server.spec"
)

_DOCKERFILE_ACP_PACKAGE = re.compile(
    r'^\s*(?P<key>[\w-]+)\) PACKAGES="\$PACKAGES '
    r'(?P<package>@[^@\s]+)@(?P<version>[^\s";]+)";',
    re.MULTILINE,
)
_REGISTRY_ACP_PACKAGES = {
    "claude-code": ("@agentclientprotocol/claude-agent-acp", CLAUDE_AGENT_ACP_VERSION),
    "codex": ("@agentclientprotocol/codex-acp", CODEX_ACP_VERSION),
    "gemini-cli": ("@google/gemini-cli", GEMINI_CLI_VERSION),
}


def test_server_workflow_passes_git_metadata_build_args() -> None:
    """The published agent-server images should embed git metadata."""
    workflow_text = SERVER_WORKFLOW.read_text(encoding="utf-8")

    assert "OPENHANDS_BUILD_GIT_SHA=${{ env.SDK_SHA }}" in workflow_text
    assert "OPENHANDS_BUILD_GIT_REF=${{ env.SDK_REF }}" in workflow_text


def test_server_workflow_contains_install_acp_providers_expression() -> None:
    """Regression guard for the exact wording of the INSTALL_ACP_PROVIDERS env
    line. This only proves the known-good string is present, not that it
    evaluates correctly in GitHub Actions — see
    test_and_or_shape_preserves_falsy_last_operand for the semantic proof.
    """
    workflow_text = SERVER_WORKFLOW.read_text(encoding="utf-8")

    assert (
        "INSTALL_ACP_PROVIDERS: ${{ github.event_name != 'workflow_dispatch' "
        "&& 'claude-code,codex,gemini-cli' || inputs.install_acp_providers }}"
    ) in workflow_text


def test_server_workflow_contains_install_capabilities_expression() -> None:
    """Regression guard for the exact wording of the INSTALL_CAPABILITIES env
    line. This only proves the known-good string is present, not that it
    evaluates correctly in GitHub Actions — see
    test_and_or_shape_preserves_falsy_last_operand for the semantic proof
    (the shape is identical to INSTALL_ACP_PROVIDERS, just a different
    default/input pair).
    """
    workflow_text = SERVER_WORKFLOW.read_text(encoding="utf-8")

    assert (
        "INSTALL_CAPABILITIES: ${{ github.event_name != 'workflow_dispatch' "
        "&& 'vscode,browser,docker' || inputs.install_capabilities }}"
    ) in workflow_text


def test_and_or_shape_preserves_falsy_last_operand() -> None:
    """GitHub Actions' `&&`/`||` share Python's `and`/`or` short-circuit
    value-return semantics (return an operand, not a coerced bool), so this
    exercises the exact `A && B || C` shape the workflow expression uses.

    A prior version put the maybe-empty dispatch value in B's position:
    `event_name == 'workflow_dispatch' && inputs.value || default`. That
    collapses to `default` whenever `inputs.value` is falsy (e.g. an
    intentional ""), because the trailing `|| default` fires again. Putting
    the maybe-empty value last, gated by the negated condition, avoids the
    second collapse since there is nothing after it to fall through to.
    """

    def resolve(is_dispatch: bool, dispatch_value: str) -> str:
        default = "claude-code,codex,gemini-cli"
        return (not is_dispatch and default) or dispatch_value

    assert (
        resolve(is_dispatch=False, dispatch_value="") == "claude-code,codex,gemini-cli"
    )
    assert resolve(is_dispatch=True, dispatch_value="") == ""
    assert resolve(is_dispatch=True, dispatch_value="codex") == "codex"
    assert (
        resolve(is_dispatch=True, dispatch_value="claude-code,codex,gemini-cli")
        == "claude-code,codex,gemini-cli"
    )


def test_agent_server_binary_copies_openhands_distribution_metadata() -> None:
    """The frozen binary should preserve OpenHands package metadata."""
    spec_text = AGENT_SERVER_SPEC.read_text(encoding="utf-8")

    for distribution in (
        "openhands-agent-server",
        "openhands-sdk",
        "openhands-tools",
        "openhands-workspace",
    ):
        assert f'*copy_metadata("{distribution}")' in spec_text


def test_agent_server_dockerfile_acp_package_versions_match_registry() -> None:
    dockerfile_text = AGENT_SERVER_DOCKERFILE.read_text(encoding="utf-8")
    case_arms = dockerfile_text.partition('case "$provider" in')[2].partition("esac")[0]
    dockerfile_packages = list(_DOCKERFILE_ACP_PACKAGE.finditer(case_arms))

    assert dockerfile_packages, (
        f"No ACP package versions found in {AGENT_SERVER_DOCKERFILE} "
        "INSTALL_ACP_PROVIDERS case arms"
    )

    for match in dockerfile_packages:
        provider_key = match["key"]
        registry_package = _REGISTRY_ACP_PACKAGES.get(provider_key)
        if registry_package is None:
            continue
        expected_package, expected_version = registry_package
        dockerfile_package = match["package"]
        dockerfile_version = match["version"]

        assert dockerfile_package == expected_package, (
            f"{AGENT_SERVER_DOCKERFILE}: ACP provider {provider_key!r} uses package "
            f"{dockerfile_package}, but acp_providers.py uses {expected_package}"
        )
        assert dockerfile_version == expected_version, (
            f"{AGENT_SERVER_DOCKERFILE}: ACP package {dockerfile_package} has version "
            f"{dockerfile_version}, but acp_providers.py pins {expected_version}"
        )


def test_server_workflow_publishes_python_slim_without_acp_providers() -> None:
    workflow_text = SERVER_WORKFLOW.read_text(encoding="utf-8")

    assert re.search(
        r"- variant: python-slim\n"
        r"\s+custom_tags: python\n"
        r"\s+image_flavor: slim\n"
        r"\s+acp_provider_flavor: none",
        workflow_text,
    )
    assert (
        "INSTALL_ACP_PROVIDERS=${{ steps.prep.outputs.install_acp_providers }}"
        in workflow_text
    )
    assert "INSTALL_CAPABILITIES=${{ env.INSTALL_CAPABILITIES }}" in workflow_text
    assert (
        "scope=agent-server-${{ matrix.variant }}-${{ matrix.arch }}" in workflow_text
    )
    assert "variant: [python, python-slim, java, golang]" in workflow_text
