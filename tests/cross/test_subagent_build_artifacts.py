"""Built-ins must survive the sdist used as the Docker build context."""

import subprocess
import tarfile
import zipfile
from pathlib import Path


def test_builtin_subagents_survive_docker_build_context(tmp_path: Path):
    root = Path(__file__).resolve().parents[2]
    definitions = Path("openhands/tools/preset/subagents")
    expected = {
        path.name: path.read_bytes()
        for path in (root / "openhands-tools" / definitions).glob("*.md")
    }
    assert expected

    subprocess.run(
        ["uv", "build", "--sdist", "--out-dir", str(tmp_path / "sdist")],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    (sdist,) = (tmp_path / "sdist").glob("*.tar.gz")
    context = tmp_path / "context"
    with tarfile.open(sdist) as archive:
        archive.extractall(context, filter="data")
    (workspace,) = context.iterdir()
    packaged = workspace / "openhands-tools" / definitions
    assert {path.name: path.read_bytes() for path in packaged.glob("*.md")} == expected

    subprocess.run(
        [
            "uv",
            "build",
            "--package",
            "openhands-tools",
            "--wheel",
            "--out-dir",
            str(tmp_path / "wheel"),
        ],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    (wheel,) = (tmp_path / "wheel").glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        assert {
            Path(name).name: archive.read(name)
            for name in archive.namelist()
            if name.startswith(f"{definitions.as_posix()}/") and name.endswith(".md")
        } == expected
