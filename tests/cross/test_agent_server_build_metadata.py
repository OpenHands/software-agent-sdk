from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "server.yml"
AGENT_SERVER_SPEC = (
    REPO_ROOT
    / "openhands-agent-server"
    / "openhands"
    / "agent_server"
    / "agent-server.spec"
)


def test_server_workflow_passes_git_metadata_build_args() -> None:
    """The published agent-server images should embed git metadata."""
    workflow_text = SERVER_WORKFLOW.read_text(encoding="utf-8")

    assert "OPENHANDS_BUILD_GIT_SHA=${{ env.SDK_SHA }}" in workflow_text
    assert "OPENHANDS_BUILD_GIT_REF=${{ env.SDK_REF }}" in workflow_text


def test_server_workflow_preserves_explicit_empty_install_acp_providers() -> None:
    """An empty install_acp_providers dispatch input ("install none") must be
    distinguishable from no dispatch input at all — a blank-string check
    can't tell those apart, so the gate must key off the event type instead.
    """
    workflow_text = SERVER_WORKFLOW.read_text(encoding="utf-8")

    assert (
        "INSTALL_ACP_PROVIDERS: ${{ github.event_name == 'workflow_dispatch' "
        "&& inputs.install_acp_providers || 'claude-code,codex,gemini-cli' }}"
    ) in workflow_text


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
