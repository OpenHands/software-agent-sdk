import pytest

from openhands.agent_server.persistence import reset_stores


@pytest.fixture(autouse=True)
def isolate_persistence_dir(tmp_path, monkeypatch):
    """Keep the developer's real ``~/.openhands`` out of every test.

    ``_start_conversation`` reads the settings store on every launch, not just
    the ``agent_profile_id`` one, and ``get_settings_store`` falls back to
    ``~/.openhands`` when ``OH_PERSISTENCE_DIR`` is unset. Tests that build a
    ``ConversationService`` directly never initialise the singleton, so without
    this they would pick up whatever settings.json happens to be on the host and
    pass or fail depending on the machine.
    """
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / ".openhands"))
    reset_stores()
    try:
        yield
    finally:
        reset_stores()
