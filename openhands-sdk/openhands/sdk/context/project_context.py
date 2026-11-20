from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from openhands.sdk.skills import Skill


class ProjectContextProfile(BaseModel):
    """Reusable project context organized into named sections."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sections: dict[str, JsonValue] = Field(min_length=1)

    @classmethod
    def from_file(cls, path: str | Path) -> Self:
        """Load a project context profile from a YAML file."""
        return cls.model_validate(
            yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        )

    def to_skill(self, *section_names: str) -> Skill:
        """Turn selected sections into a repository skill for one agent."""
        if not section_names:
            raise ValueError("Select at least one project context section")
        try:
            selected = {name: self.sections[name] for name in section_names}
        except KeyError as error:
            raise ValueError(
                f"Unknown project context section: {error.args[0]!r}"
            ) from error

        return Skill(
            name="project-context-profile",
            content=yaml.safe_dump(
                selected, allow_unicode=True, sort_keys=False
            ).rstrip(),
        )
