"""Application filesystem configuration."""

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_path, user_data_path


@dataclass(frozen=True)
class RecorderPaths:
    data: Path
    cache: Path
    traces: Path
    index: Path

    @classmethod
    def defaults(cls) -> "RecorderPaths":
        data = user_data_path("agent-flight-recorder", "OpenHands")
        cache = user_cache_path("agent-flight-recorder", "OpenHands")
        return cls(
            data=data, cache=cache, traces=data / "traces", index=data / "index.db"
        )

    def create(self) -> None:
        self.data.mkdir(parents=True, exist_ok=True)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.traces.mkdir(parents=True, exist_ok=True)
