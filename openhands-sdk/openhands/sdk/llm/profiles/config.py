from __future__ import annotations

import json
from pathlib import Path
from typing import Final, Self

from pydantic import BaseModel, Field, model_validator


_INVALID_PROFILE_NAMES: Final[set[str]] = {"", ".", "..", "config"}


class ProfileMetadata(BaseModel):
    """Metadata associate to a profile."""

    name: str = Field(description="Name of the profile", pattern=r"^[A-Za-z0-9._-]+$")
    file: str = Field(description="Path to the profile file", pattern=r".*\.json$")
    description: str = Field(description="Description of the profile")
    usage_id: str | None = Field(default=None, description="Usage id of the profile")

    @model_validator(mode="after")
    def validate_name(self) -> Self:
        if self.name in _INVALID_PROFILE_NAMES:
            raise ValueError("Invalid profile name.")
        return self


class ProfilesConfig(BaseModel):
    """Model for the config profile."""

    default_profile: str | None = Field(
        description="Default profile. `None` if not set."
    )
    profiles: list[ProfileMetadata] = Field(description="List of profiles")

    @model_validator(mode="after")
    def validate_unicity_profile_names(self) -> Self:
        unique_profile_names = {profile.name for profile in self.profiles}

        if len(unique_profile_names) != len(self.profiles):
            raise ValueError("Every profile must have a unique name.")

        return self

    @model_validator(mode="after")
    def validate_default_profile_exists(self) -> Self:
        # if the default profile is not set we can skip the validation
        if self.default_profile is None:
            return self

        profile_names = {p.name for p in self.profiles}
        if self.default_profile not in profile_names:
            raise ValueError(
                f"default_profile '{self.default_profile}' "
                f"is not present in profiles: {sorted(profile_names)}"
            )

        return self

    @model_validator(mode="after")
    def validate_unicity_non_none_id_sages(self) -> Self:
        id_usages = [
            profile.usage_id
            for profile in self.profiles
            if profile.usage_id is not None
        ]
        if len(id_usages) != len(set(id_usages)):
            raise ValueError("ID usage are not uniques.")

        return self

    @classmethod
    def empty(cls) -> ProfilesConfig:
        """Return an empty config."""
        return cls(
            default_profile=None,
            profiles=[],
        )

    @classmethod
    def from_json(cls, json_path: str | Path) -> ProfilesConfig:
        input_ = json.loads(Path(json_path).read_text())
        return cls(**input_)
