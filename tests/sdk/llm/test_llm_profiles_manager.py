import json
from pathlib import Path
from typing import Any

import pytest

from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.profiles.config import ProfileMetadata, ProfilesConfig
from openhands.sdk.llm.profiles.manager import ProfileManager


def get_config() -> ProfilesConfig:
    return ProfilesConfig(
        default_profile="profile_1",
        profiles=[
            ProfileMetadata(
                name="profile_1",
                file="profile_1.json",
                description="use for translation",
            ),
            ProfileMetadata(
                name="profile_2",
                file="profile_2.json",
                description="use for fun",
            ),
        ],
    )


def get_profiles() -> list[dict[str, Any]]:
    return [
        {
            "model": "profile_1",
            "api_key": "...",
            "base_url": "asd",
            "usage_id": "agent_1",
            "litellm_extra_body": {},
            "OVERRIDE_ON_SERIALIZE": [
                "api_key",
                "aws_access_key_id",
                "aws_secret_access_key",
                "litellm_extra_body",
            ],
        },
        {
            "model": "profile_2",
            "api_key": "...",
            "base_url": "asd",
            "usage_id": "agent_2",
            "litellm_extra_body": {},
            "OVERRIDE_ON_SERIALIZE": [
                "api_key",
                "aws_access_key_id",
                "aws_secret_access_key",
                "litellm_extra_body",
            ],
        },
    ]


@pytest.fixture(scope="function")
def profiles_base_dir(tmp_path_factory) -> Path:
    """The fixture set up a directory with some profiles inside."""
    base_dir = tmp_path_factory.mktemp("tmp_profile_factory")

    config = get_config()
    config_path = base_dir / "config.json"
    config_path.write_text(json.dumps(config.model_dump()))

    profiles = get_profiles()
    for p_metadata, p in zip(config.profiles, profiles):
        file_path = base_dir / p_metadata.file
        Path(file_path).write_text(json.dumps(p))

    return base_dir


def test_init_missing_config(tmp_path_factory) -> None:
    """Test the behaviour in case the profiles config is missing"""
    base_dir = tmp_path_factory.mktemp("tmp_profile_factory")
    store = ProfileManager(base_dir)

    # when no config is present, we should have an empty config
    assert store.config == ProfilesConfig.empty()


def test_init(profiles_base_dir) -> None:
    """Test initialization of the profiles store"""
    store = ProfileManager(base_dir=profiles_base_dir)

    # checks for paths
    assert store.base_dir == profiles_base_dir
    assert store.config_path == profiles_base_dir / "config.json"

    # checks for config
    config = get_config()
    assert store.config == config
    assert store.get_profile_names() == ["profile_1", "profile_2"]
    assert store.list_profiles() == config.profiles


def test_default_profile(profiles_base_dir) -> None:
    """Test default profile is working"""
    store = ProfileManager(base_dir=profiles_base_dir)

    default_profile = store.get_default_profile()
    profiles = get_profiles()

    assert isinstance(default_profile, LLM)
    assert default_profile.model == profiles[0]["model"]
    assert default_profile.usage_id == profiles[0]["usage_id"]
    assert default_profile.base_url == profiles[0]["base_url"]


def test_cache_default_profile(profiles_base_dir) -> None:
    """Test cache for the default profile"""
    store = ProfileManager(base_dir=profiles_base_dir)

    llm_1 = store.get_default_profile()
    llm_2 = store.get_default_profile()

    assert llm_1 is llm_2


def test_clear_cache_default_profile(profiles_base_dir) -> None:
    """Test correct eviction of cache for default profile"""
    store = ProfileManager(base_dir=profiles_base_dir)

    before = store.get_default_profile()
    store.set_default_profile("profile_2")
    after = store.get_default_profile()

    assert before is not None
    assert after is not None
    assert before.model == "profile_1"
    assert after.model == "profile_2"


def test_load_profile(profiles_base_dir) -> None:
    """Test loading profiles"""
    store = ProfileManager(base_dir=profiles_base_dir)

    profiles = get_profiles()
    for profile in profiles:
        llm_instance = store.load_profile(profile["model"])

        assert isinstance(llm_instance, LLM)
        assert llm_instance.model == profile["model"]
        assert llm_instance.usage_id == profile["usage_id"]
        assert llm_instance.base_url == profile["base_url"]


def test_load_fake_profile(profiles_base_dir) -> None:
    """Test error when loading non-existent profile"""
    store = ProfileManager(base_dir=profiles_base_dir)
    with pytest.raises(FileNotFoundError, match="Unknown profile"):
        _ = store.load_profile("fake_profile")


def test_delete_profile(profiles_base_dir) -> None:
    """Test deletion of a profile"""
    store = ProfileManager(base_dir=profiles_base_dir)

    store.delete_profile("profile_2")

    # check that the profile was deleted from disk
    # and no other file was touched
    assert (profiles_base_dir / "config.json").exists()
    assert (profiles_base_dir / "profile_1.json").exists()
    assert not (profiles_base_dir / "profile_2.json").exists()

    # check that the config was update correctly
    assert len(store.config.profiles) == 1
    assert store.config.profiles[0].name == "profile_1"


def test_delete_missing_profile(profiles_base_dir) -> None:
    """Test error when deleting a non-existent profile"""
    store = ProfileManager(base_dir=profiles_base_dir)
    with pytest.raises(ValueError, match="Given profile"):
        store.delete_profile("fake_profile")


def test_delete_default_profile_forbidden(profiles_base_dir) -> None:
    """Test error when deleting default profile"""
    store = ProfileManager(base_dir=profiles_base_dir)
    with pytest.raises(ValueError, match="Cannot delete"):
        store.delete_profile("profile_1")


def test_save_new_profile(profiles_base_dir) -> None:
    store = ProfileManager(base_dir=profiles_base_dir)
    llm = LLM(
        model="profile_3",
        api_key="SOME_HIDDEN_API_KEY",
        base_url="asd",
        usage_id="agent_3",
    )
    store.save_profile(name="profile_3", llm=llm, description="agent_3")

    # the profile was saved
    assert (profiles_base_dir / "profile_3.json").exists()

    # check that the config was update correctly
    # in this case the new profile should be the last one
    assert len(store.config.profiles) == 3
    assert store.config.profiles[-1].name == "profile_3"
    assert store.config.profiles[-1].file == "profile_3.json"
    assert store.config.profiles[-1].description == "agent_3"


def test_override_a_profile(profiles_base_dir) -> None:
    store = ProfileManager(base_dir=profiles_base_dir)
    llm = LLM(
        model="profile_2",
        api_key="SOME_HIDDEN_API_KEY",
        base_url="asd",
        usage_id="best_agent",
    )
    before = store.load_profile("profile_2")
    store.save_profile(name="profile_2", llm=llm, description="new agent")
    after = store.load_profile("profile_2")

    # check that not new files were added and profile_2 was updated
    assert len(list(profiles_base_dir.iterdir())) == 3
    assert (profiles_base_dir / "profile_2.json").exists()
    assert before != after

    # check that the config was update correctly
    assert len(store.config.profiles) == 2
    assert store.config.profiles[-1].name == "profile_2"
    assert store.config.profiles[-1].file == "profile_2.json"
    assert store.config.profiles[-1].description == "new agent"
