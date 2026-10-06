import os
from pathlib import Path

import pytest

from openhands.sdk.utils import files
from openhands.sdk.utils.files import atomic_write_text, is_atomic_write_temp_file


def test_is_atomic_write_temp_file_matches_the_writers_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp_paths: list[Path] = []
    real_replace = os.replace

    def recording_replace(src, dst):
        temp_paths.append(Path(src))
        real_replace(src, dst)

    monkeypatch.setattr(files.os, "replace", recording_replace)

    for name in ("base_state.json", "event-00000-abc.json", ".eventlog.lock"):
        atomic_write_text(tmp_path / name, "{}")

    assert len(temp_paths) == 3
    assert all(is_atomic_write_temp_file(path) for path in temp_paths)


@pytest.mark.parametrize(
    "name",
    [
        "base_state.json",
        "meta.json",
        "event-00000-0123abcd-4567-89ef.json",
        ".eventlog.lock",
        ".eventlog-len-12.marker",
        ".hidden",
        "base_state.json.4867ztqe",
    ],
)
def test_is_atomic_write_temp_file_ignores_other_names(name: str) -> None:
    assert not is_atomic_write_temp_file(Path(name))
