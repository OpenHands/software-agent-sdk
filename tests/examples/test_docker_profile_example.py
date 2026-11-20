import subprocess
import sys
from pathlib import Path


def test_docker_profile_example_rejects_unknown_spec() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    example = (
        repo_root
        / "examples"
        / "02_remote_agent_server"
        / "18_docker_agent_profile_spec.py"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(example), "--spec-id", "unmapped-spec"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 2
    assert "Unknown sandbox spec ID: 'unmapped-spec'" in result.stderr
