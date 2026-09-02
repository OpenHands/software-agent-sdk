import re
from pathlib import Path

from openhands.sdk.settings.acp_install_catalog import (
    ACP_INSTALL_CATALOG,
    DEFAULT_PREINSTALLED_ACP_PROVIDERS,
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
ACP_INSTALL_CATALOG_PY = (
    REPO_ROOT
    / "openhands-sdk"
    / "openhands"
    / "sdk"
    / "settings"
    / "acp_install_catalog.py"
)


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


def test_agent_server_dockerfile_has_no_hardcoded_acp_packages() -> None:
    """The acp-providers stage must resolve packages/versions from the
    dependency-free catalog at build time, not from Dockerfile-baked arms.
    """
    dockerfile_text = AGENT_SERVER_DOCKERFILE.read_text(encoding="utf-8")
    acp_stage = dockerfile_text.partition("FROM python:3.13-bookworm AS acp-providers")[
        2
    ].partition("####")[0]

    assert 'case "$provider"' not in acp_stage, (
        "acp-providers stage should no longer branch on a hardcoded provider-key list"
    )
    for spec in ACP_INSTALL_CATALOG.values():
        for pkg in spec.packages:
            assert pkg.pinned not in acp_stage, (
                f"{AGENT_SERVER_DOCKERFILE}: found hardcoded package pin "
                f"{pkg.pinned!r} in the acp-providers stage; it should come "
                "from acp_install_catalog.py at build time instead"
            )


def test_agent_server_dockerfile_acp_stage_uses_install_catalog() -> None:
    dockerfile_text = AGENT_SERVER_DOCKERFILE.read_text(encoding="utf-8")

    assert (
        "COPY openhands-sdk/openhands/sdk/settings/acp_install_catalog.py "
        "/tmp/acp_install_catalog.py" in dockerfile_text
    )
    assert "python3 /tmp/acp_install_catalog.py" in dockerfile_text
    # The COPY path above is relative to the build context root; confirm it
    # actually resolves, matching the relpath build.py stages for the
    # empty-context `base-image-minimal` fast path.
    assert ACP_INSTALL_CATALOG_PY.is_file()


def test_default_preinstalled_acp_providers_matches_dockerfile_and_workflow() -> None:
    """DEFAULT_PREINSTALLED_ACP_PROVIDERS is the single source for the
    default `INSTALL_ACP_PROVIDERS` value baked into the Dockerfile ARG and
    the server workflow's non-dispatch default; both are plain text (a
    Dockerfile/workflow can't import Python), so this guards them from
    drifting apart.
    """
    default_csv = ",".join(DEFAULT_PREINSTALLED_ACP_PROVIDERS)
    assert default_csv == "claude-code,codex,gemini-cli"

    dockerfile_text = AGENT_SERVER_DOCKERFILE.read_text(encoding="utf-8")
    assert f"ARG INSTALL_ACP_PROVIDERS={default_csv}" in dockerfile_text

    workflow_text = SERVER_WORKFLOW.read_text(encoding="utf-8")
    assert f"'{default_csv}'" in workflow_text


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
