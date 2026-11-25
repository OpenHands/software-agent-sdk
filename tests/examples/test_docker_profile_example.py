import re
import subprocess
import sys
from pathlib import Path

import pytest


_EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "02_remote_agent_server"
    / "18_docker_agent_profile_spec.py"
)


def test_docker_profile_example_rejects_unknown_spec() -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(_EXAMPLE), "--spec-id", "unmapped-spec"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 2
    assert "Unknown sandbox spec ID: 'unmapped-spec'" in result.stderr


@pytest.mark.docker_live
def test_docker_profile_example_uses_distinct_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exercise the same public-image path with the CI environment set.
    monkeypatch.setenv("SDK_SHA", "0" * 40)
    image_ids = []
    for args, tag in [
        ((), "latest-python-slim"),
        (("--default-image",), "latest-python"),
    ]:
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(_EXAMPLE), *args],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"Using Docker image: ghcr.io/openhands/agent-server:{tag}" in (
            result.stdout.splitlines()
        )
        match = re.search(
            r"^Docker image ID: (sha256:[a-f0-9]{64})$", result.stdout, re.M
        )
        assert match is not None, result.stdout
        image_ids.append(match.group(1))

    assert len(set(image_ids)) == 2
