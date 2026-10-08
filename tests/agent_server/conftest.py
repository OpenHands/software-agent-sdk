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
    # The singleton getters resolve ``OH_PERSISTENCE_DIR`` per call, but three
    # SDK defaults are frozen into module constants at import time and therefore
    # ignore the env var above once the module is already imported (which it
    # always is here). Pin them too, or a store constructed with no explicit
    # ``base_dir`` still lands in the developer's real ``~/.openhands``.
    from openhands.sdk.llm import llm_profile_store
    from openhands.sdk.profiles import agent_profile_store
    from openhands.sdk.skills import installed as installed_skills

    persistence_dir = tmp_path / ".openhands"
    monkeypatch.setattr(
        llm_profile_store, "_DEFAULT_PROFILE_DIR", persistence_dir / "profiles"
    )
    monkeypatch.setattr(
        agent_profile_store, "_DEFAULT_PROFILE_DIR", persistence_dir / "agent-profiles"
    )
    monkeypatch.setattr(
        installed_skills,
        "DEFAULT_INSTALLED_SKILLS_DIR",
        persistence_dir / "skills" / "installed",
    )
    reset_stores()
    try:
        yield
    finally:
        reset_stores()


@pytest.fixture(autouse=True)
def isolate_service_singletons():
    """Restore the module service singletons after each test.

    The non-deferred lifespan publishes the process-default conversation and
    bash services to ``conversation_service._conversation_service`` /
    ``bash_service._bash_event_service`` (mirroring its ownership stack, cleared
    on shutdown). Tests that build those singletons directly, or that patch
    service construction, would otherwise leave a service (or mock) behind for
    later tests.
    """
    from openhands.agent_server import (
        api as api_mod,
        bash_service,
        conversation_service,
    )

    conversation_before = conversation_service._conversation_service
    bash_before = bash_service._bash_event_service
    owners_before = list(api_mod._default_service_owners)
    try:
        yield
    finally:
        conversation_service._conversation_service = conversation_before
        bash_service._bash_event_service = bash_before
        api_mod._default_service_owners[:] = owners_before
