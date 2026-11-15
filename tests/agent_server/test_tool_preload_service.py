from unittest.mock import patch

import pytest

from openhands.agent_server import tool_preload_service


@pytest.mark.parametrize("extensions_path", [None, "/opt/extensions"])
def test_warm_up_discovers_skills_only_from_a_local_extensions_copy(
    monkeypatch, extensions_path
):
    if extensions_path is None:
        monkeypatch.delenv("EXTENSIONS_PATH", raising=False)
    else:
        monkeypatch.setenv("EXTENSIONS_PATH", extensions_path)
    service = "openhands.agent_server.skills_service"

    with (
        patch(f"{service}.discover_profile_skills") as profile_skills,
        patch(f"{service}.discover_public_skill_names") as public_names,
    ):
        tool_preload_service._warm_first_conversation()

    expected_calls = 1 if extensions_path else 0
    assert profile_skills.call_count == expected_calls
    assert public_names.call_count == expected_calls
