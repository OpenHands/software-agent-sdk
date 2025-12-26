import pytest
from pydantic import ValidationError

from openhands.sdk.llm.profiles.config import ProfileMetadata, ProfilesConfig


@pytest.mark.parametrize(
    "name, file, description",
    [
        ("profile_1", "profile_1.json", "something"),
        ("Open.Hands-2_times", "gpt-4.json", ""),
    ],
)
def test_profile_metadata_init(name: str, file: str, description: str) -> None:
    _ = ProfileMetadata(name=name, file=file, description=description)


@pytest.mark.parametrize("name", ["", ".", "..", "config"])
def test_validate_profile_name_exception(name: str) -> None:
    with pytest.raises(ValidationError):
        _ = ProfileMetadata(name=name, file="aaa.json", description="")


def test_empty_profiles_config() -> None:
    empty_config = ProfilesConfig.empty()

    assert empty_config.default_profile is None
    assert empty_config.profiles == []


def test_validate_names_profiles_config() -> None:
    pass


def test_validate_default_profile_config() -> None:
    pass
