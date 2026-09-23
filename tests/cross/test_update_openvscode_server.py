from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "scripts"
    / "update_openvscode_server.py"
)
spec = spec_from_file_location("update_openvscode_server", SCRIPT)
assert spec and spec.loader
module = module_from_spec(spec)
spec.loader.exec_module(module)


def write_dockerfile(tmp_path: Path, pin: str) -> Path:
    path = tmp_path / "Dockerfile"
    path.write_text(f'FROM scratch\nARG RELEASE_TAG="{pin}"\n')
    return path


def test_validate_tag_accepts_stable_release() -> None:
    module.validate_tag("openvscode-server-v1.109.5")


@pytest.mark.parametrize(
    "tag",
    ["1.109.5", "openvscode-server-v1.109.5-insiders", "v1.109.5", "latest"],
)
def test_validate_tag_rejects_unexpected_release(tag: str) -> None:
    with pytest.raises(ValueError, match="unexpected OpenVSCode release tag"):
        module.validate_tag(tag)


def test_update_dockerfile_changes_single_pin(tmp_path: Path) -> None:
    path = write_dockerfile(tmp_path, "openvscode-server-v1.98.2")

    assert module.update_dockerfile(path, "openvscode-server-v1.109.5")
    assert 'ARG RELEASE_TAG="openvscode-server-v1.109.5"' in path.read_text()


def test_update_dockerfile_is_idempotent(tmp_path: Path) -> None:
    path = write_dockerfile(tmp_path, "openvscode-server-v1.109.5")

    assert not module.update_dockerfile(path, "openvscode-server-v1.109.5")


def test_update_dockerfile_rejects_missing_or_duplicate_pin(tmp_path: Path) -> None:
    path = tmp_path / "Dockerfile"
    path.write_text("FROM scratch\n")
    with pytest.raises(ValueError, match="expected exactly one"):
        module.update_dockerfile(path, "openvscode-server-v1.109.5")

    path.write_text(
        'ARG RELEASE_TAG="openvscode-server-v1.98.2"\n'
        'ARG RELEASE_TAG="openvscode-server-v1.99.0"\n'
    )
    with pytest.raises(ValueError, match="expected exactly one"):
        module.update_dockerfile(path, "openvscode-server-v1.109.5")
