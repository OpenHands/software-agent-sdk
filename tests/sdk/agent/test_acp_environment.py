"""Public ACP credential/environment scoping against a real child process."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from openhands.sdk import LLMProfileStore
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation import Conversation
from openhands.sdk.launch import LaunchStores, resolve
from openhands.sdk.profiles.agent_profile import ACPAgentProfile
from openhands.sdk.profiles.seed import build_seed_profile
from openhands.sdk.settings import ACPAgentSettings
from openhands.sdk.settings.acp_providers import ACPFileSecretSpec


def run_case(inherit, secrets):
    with tempfile.TemporaryDirectory(prefix="acp-env-") as directory:
        root = Path(directory)
        transcript = root / "peer.json"
        settings = ACPAgentSettings(
            acp_server="custom",
            acp_command=[
                sys.executable,
                str(Path(__file__).with_name("acp_permission_peer.py")),
                str(transcript),
            ],
            acp_permission_mode="deny",
            acp_env_allowlist=inherit,
            acp_secret_allowlist=secrets,
            acp_file_secrets=[
                ACPFileSecretSpec(
                    secret_name="PROOF_FILE_SECRET",
                    filename="fake.credential",
                    env_var="PROOF_FILE_PATH",
                    subdir="fixture",
                )
            ],
        )
        settings = ACPAgentSettings.model_validate_json(settings.model_dump_json())
        agent = settings.create_agent()
        assert agent.acp_env_allowlist == inherit
        assert agent.acp_secret_allowlist == secrets
        conversation = Conversation(
            agent=agent,
            workspace=str(root),
            persistence_dir=str(root / "conversation"),
            visualizer=None,
        )
        try:
            conversation.update_secrets(
                {
                    "PROOF_ALLOWED_SECRET": "approved",
                    "PROOF_FORBIDDEN_SECRET": "forbidden",
                    "PROOF_FILE_SECRET": "fake-file-sentinel",
                }
            )
            with patch.dict(
                os.environ,
                {
                    "PROOF_ALLOWED_ENV": "selected",
                    "PROOF_CONTROL_KEY": "must-not-inherit",
                },
            ):
                conversation.send_message("Request a simulated permission and finish.")
                conversation.run()
            return json.loads(transcript.read_text(encoding="utf-8"))[
                "environment_observation"
            ]
        finally:
            conversation.close()
            agent.close()


def test_explicit_scope_excludes_control_env_and_unselected_credentials():
    observed = run_case(["PATH", "HOME", "PROOF_ALLOWED_ENV"], ["PROOF_ALLOWED_SECRET"])
    assert observed == {
        "allowed_env": True,
        "control_env": False,
        "allowed_secret": True,
        "forbidden_secret": False,
        "file_path": False,
    }


def test_empty_scopes_are_empty_and_selected_file_credentials_remain_supported():
    empty = run_case([], [])
    assert not any(empty.values())
    selected = run_case(["PATH", "HOME"], ["PROOF_ALLOWED_SECRET", "PROOF_FILE_SECRET"])
    assert selected["file_path"] and selected["allowed_secret"]
    assert not selected["control_env"] and not selected["forbidden_secret"]


def test_unspecified_scopes_preserve_legacy_environment_behavior():
    observed = run_case(None, None)
    assert all(observed.values())


def test_profile_seed_persistence_and_resolution_retain_host_controls():
    settings = ACPAgentSettings(
        acp_server="custom",
        acp_command=[sys.executable, "fixture.py"],
        acp_permission_mode="callback",
        acp_permission_timeout=7,
        acp_env_allowlist=["PATH"],
        acp_secret_allowlist=[],
        acp_allow_model_switch=False,
        acp_resume_policy="strict",
    )
    profile = build_seed_profile(settings, None)
    profile = ACPAgentProfile.model_validate_json(profile.model_dump_json())
    resolved = resolve(
        profile,
        LaunchStores(llm_profiles=LLMProfileStore(), mcp_config={}, skills=lambda: []),
    )
    assert isinstance(resolved.settings, ACPAgentSettings)
    agent = resolved.settings.create_agent()
    assert agent.acp_permission_mode == "callback"
    assert agent.acp_permission_timeout == 7
    assert agent.acp_env_allowlist == ["PATH"]
    assert agent.acp_secret_allowlist == []
    assert agent.acp_allow_model_switch is False
    assert agent.acp_resume_policy == "strict"


def test_fixed_model_rejects_change_before_first_turn():
    with tempfile.TemporaryDirectory(prefix="acp-fixed-") as directory:
        settings = ACPAgentSettings(
            acp_server="custom",
            acp_command=[sys.executable, "unused-fixture.py"],
            acp_model="selected-model",
            acp_allow_model_switch=False,
        )
        conversation = Conversation(
            agent=settings.create_agent(), workspace=directory, visualizer=None
        )
        try:
            try:
                conversation.switch_acp_model("another-model")
            except ValueError as error:
                assert "fixed" in str(error)
            else:
                raise AssertionError("Fixed model changed before its first turn")
            assert isinstance(conversation.agent, ACPAgent)
            assert conversation.agent.acp_model == "selected-model"
            assert not conversation.agent.supports_runtime_model_switch
        finally:
            conversation.close()


def _strict_resume_case(*, supported=True, changed_cwd=False):
    with tempfile.TemporaryDirectory(prefix="acp-resume-") as directory:
        root = Path(directory)
        transcript = root / "peer.json"
        settings = ACPAgentSettings(
            acp_server="custom",
            acp_command=[
                sys.executable,
                str(Path(__file__).with_name("acp_permission_peer.py")),
                str(transcript),
                *(["--load-supported"] if supported else []),
            ],
            acp_permission_mode="deny",
            acp_resume_policy="strict",
            acp_allow_model_switch=False,
        )
        identifier = uuid4()
        first = Conversation(
            agent=settings.create_agent(),
            workspace=str(root),
            persistence_dir=str(root / "saved"),
            conversation_id=identifier,
            delete_on_close=False,
            visualizer=None,
        )
        first.send_message("Finish a simulated turn.")
        first.run()
        original = json.loads(transcript.read_text())
        assert "session/new" in original["calls"]
        first.close()
        first.agent.close()
        second = Conversation(
            agent=settings.create_agent(),
            workspace=str(root),
            persistence_dir=str(root / "saved"),
            conversation_id=identifier,
            delete_on_close=False,
            visualizer=None,
        )
        try:
            original_id = second.state.agent_state["acp_session_id"]
            if changed_cwd:
                second.state.agent_state["acp_session_cwd"] = str(root / "different")
                transcript.unlink()
            second.send_message("Resume the original simulated session.")
            try:
                second.run()
            except RuntimeError as error:
                assert "explicit restart" in str(error)
            assert second.state.agent_state["acp_session_id"] == original_id
            if changed_cwd:
                assert not transcript.exists()
                return
            observed = json.loads(transcript.read_text())
            assert ("session/load" in observed["calls"]) is supported
            assert "session/new" not in observed["calls"]
            assert "session/prompt" not in observed["calls"]
        finally:
            second.close()
            second.agent.close()


def test_strict_resume_never_replaces_a_rejected_native_session():
    _strict_resume_case()


def test_strict_resume_requires_advertised_load_capability():
    _strict_resume_case(supported=False)


def test_strict_resume_rejects_changed_workspace_before_process_start():
    _strict_resume_case(changed_cwd=True)
