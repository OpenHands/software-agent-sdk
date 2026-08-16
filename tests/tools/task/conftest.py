import pytest

from openhands.sdk.llm import llm_profile_store
from openhands.sdk.subagent import registry


@pytest.fixture(autouse=True)
def _isolate_llm_profile_store(tmp_path, monkeypatch):
    """Redirect the default LLM profile dir so tests never touch the real
    ~/.openhands/profiles (TaskToolSet.create lists saved profiles).

    The registry's profile-store getter is lru_cached, so it is cleared before
    and after each test: a store cached under an earlier test's (now-deleted)
    tmp dir must not leak into the next test.
    """
    registry._get_profile_store.cache_clear()
    monkeypatch.setattr(
        llm_profile_store, "_DEFAULT_PROFILE_DIR", tmp_path / "profiles"
    )
    yield
    registry._get_profile_store.cache_clear()
