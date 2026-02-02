import pytest
from pydantic import ValidationError

from openhands.sdk.llm.profiles.config import ProfileMetadata, ProfilesConfig


def _get_profiles_metadata() -> list[tuple[str, ...]]:
    return [
        ("profile_1", "profile_1.json", "something"),
        ("Open.Hands-2_times", "gpt-4.json", ""),
    ]


@pytest.mark.parametrize(
    "name, file, description",
    _get_profiles_metadata(),
)
def test_profile_metadata_init(name: str, file: str, description: str) -> None:
    _ = ProfileMetadata(name=name, file=file, description=description)


@pytest.mark.parametrize("name", ["", ".", "..", "config"])
def test_validate_profile_name_exception(name: str) -> None:
    with pytest.raises(ValidationError):
        _ = ProfileMetadata(name=name, file="aaa.json", description="")


@pytest.mark.parametrize(
    "default_profile, profiles",
    [
        (None, []),
        (None, _get_profiles_metadata()),
        ("profile_1", _get_profiles_metadata()),
    ],
)
def test_profiles_config(
    default_profile: str, profiles: list[tuple[str, str, str]]
) -> None:
    profiles_metadata = [
        ProfileMetadata(name=p[0], file=p[1], description=p[-1]) for p in profiles
    ]
    profile_config = ProfilesConfig(
        default_profile=default_profile,
        profiles=profiles_metadata,
    )

    assert profile_config.default_profile == default_profile
    assert profile_config.profiles == profiles_metadata


def test_validate_unicity_profile_names_exception() -> None:
    # creating duplicate profiles
    profiles = _get_profiles_metadata()
    profiles += profiles

    with pytest.raises(ValueError, match="Every profile must have a unique name."):
        _ = ProfilesConfig(
            default_profile="",
            profiles=[
                ProfileMetadata(name=p[0], file=p[1], description=p[-1])
                for p in profiles
            ],
        )


def test_validate_default_profile_config_exception() -> None:
    profiles = [
        ProfileMetadata(name=p[0], file=p[1], description=p[-1])
        for p in _get_profiles_metadata()
    ]

    with pytest.raises(ValueError, match="default_profile"):
        _ = ProfilesConfig(default_profile="random_profile", profiles=profiles)
