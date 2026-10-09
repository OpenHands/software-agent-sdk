"""Model discovery and legacy model selection against a real stdio ACP server."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.agent.acp_model_discovery import discover_acp_models
from openhands.sdk.agent.acp_models import ACPModelDiscoveryError, ACPModelInfo
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.settings.model import ACPAgentSettings
from openhands.sdk.workspace.local import LocalWorkspace


FAKE_SERVER = Path(__file__).with_name("fake_acp_models_server.py")
FAKE_COMMAND = [sys.executable, str(FAKE_SERVER)]


@pytest.fixture(autouse=True)
def _clean_fake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("FAKE_ACP_MODELS", "FAKE_ACP_KEY", "FAKE_ACP_REQUIRE_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def work_root(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    return root


def _settings(**overrides) -> ACPAgentSettings:
    return ACPAgentSettings(acp_server="custom", acp_command=FAKE_COMMAND, **overrides)


def test_reports_default_and_models_from_config_options(work_root: Path) -> None:
    result = discover_acp_models(_settings(), work_root=work_root)

    assert result.error is None
    assert (result.agent_name, result.agent_version) == ("fake-acp", "1.2.3")
    assert result.current_model_id == "m1"
    assert [m.model_id for m in result.available_models] == ["m1", "m2"]
    assert list(work_root.iterdir()) == []


def test_reports_models_from_the_legacy_models_block(
    work_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ACP_MODELS", "legacy")

    result = discover_acp_models(_settings(), work_root=work_root)

    assert result.error is None
    assert result.current_model_id == "m1"
    assert result.available_models == [
        ACPModelInfo(model_id="m1", name="M1", description="m1 model"),
        ACPModelInfo(model_id="m2", name="M2", description="m2 model"),
    ]


def test_secrets_reach_the_server(work_root: Path) -> None:
    result = discover_acp_models(
        _settings(), secrets={"FAKE_ACP_KEY": "key"}, work_root=work_root
    )

    assert [m.model_id for m in result.available_models] == ["m1", "m2", "premium"]


def test_reports_the_server_default_not_the_requested_model(work_root: Path) -> None:
    result = discover_acp_models(_settings(acp_model="m2"), work_root=work_root)

    assert result.current_model_id == "m1"


def test_reports_an_auth_failure(
    work_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ACP_REQUIRE_KEY", "1")

    result = discover_acp_models(_settings(), work_root=work_root)

    assert result.error == ACPModelDiscoveryError(
        code="ACPAuthRequired", detail="[-32000] Authentication required"
    )
    assert result.available_models == []
    assert list(work_root.iterdir()) == []


def test_reports_a_spawn_failure(work_root: Path, tmp_path: Path) -> None:
    settings = ACPAgentSettings(
        acp_server="custom", acp_command=[str(tmp_path / "no-such-acp-server")]
    )

    result = discover_acp_models(settings, work_root=work_root)

    assert result.error is not None
    assert result.error.code == "ACPSpawnError"
    assert list(work_root.iterdir()) == []


def _start(agent: ACPAgent, tmp_path: Path) -> None:
    state = ConversationState.create(
        id=uuid.uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )
    with state:
        agent.init_state(state, on_event=lambda _: None)


@pytest.mark.parametrize(
    ("requested", "running"),
    [("m2", "m2"), ("not-offered", "m1")],
)
def test_legacy_server_receives_the_initial_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, requested: str, running: str
) -> None:
    monkeypatch.setenv("FAKE_ACP_MODELS", "legacy")
    agent = ACPAgent(acp_command=FAKE_COMMAND, acp_model=requested)
    try:
        _start(agent, tmp_path)
        assert agent.current_model_id == running
    finally:
        agent.close()


def test_legacy_server_switches_models_at_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ACP_MODELS", "legacy")
    agent = ACPAgent(acp_command=FAKE_COMMAND)
    try:
        _start(agent, tmp_path)
        agent.set_acp_model("m2")
        assert agent.current_model_id == "m2"
        with pytest.raises(Exception, match="Unknown model"):
            agent.set_acp_model("not-offered")
        assert agent.current_model_id == "m2"
    finally:
        agent.close()
