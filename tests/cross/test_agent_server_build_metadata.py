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
AGENT_SERVER_DOCKERFILE = (
    REPO_ROOT
    / "openhands-agent-server"
    / "openhands"
    / "agent_server"
    / "docker"
    / "Dockerfile"
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


def test_docker_absent_shim_only_exists_when_docker_is_excluded() -> None:
    """A Docker-less image must explain itself, and must not shadow real Docker.

    Docker is the one gated capability with no runtime recovery — dockerd needs
    --privileged (or Sysbox) granted at container start — so this message is the
    only path a user has. It must be written in the exclusion branch only: a shim
    reachable from the inclusion branch would mask the real CLI.
    """
    dockerfile = AGENT_SERVER_DOCKERFILE.read_text(encoding="utf-8")

    marker = "# --- Docker ---"
    start = dockerfile.index(marker)
    block = dockerfile[start : dockerfile.index("# --- GitHub CLI ---", start)]

    shim_path = "/usr/local/bin/docker"
    assert shim_path in block, (
        f"expected the Docker block to write a shim at {shim_path}"
    )

    # The shim must be written before the exclusion branch exits, and the
    # inclusion branch (`*,docker,*) ;;`) must fall through untouched.
    exclusion = block.index("INSTALL_CAPABILITIES excludes docker")
    assert block.index(shim_path) > exclusion, (
        "the docker shim must be written inside the exclusion branch, otherwise "
        "it would shadow the real Docker CLI in the default image"
    )

    # The message has to name both halves of the fix. Installing Docker is not
    # enough on its own — the daemon still needs elevated privileges at start.
    assert "INSTALL_CAPABILITIES" in block
    assert "--privileged" in block
