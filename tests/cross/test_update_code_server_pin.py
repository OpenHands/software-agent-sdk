from datetime import UTC, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory


SPEC = spec_from_file_location(
    "update_code_server_pin",
    Path(__file__).resolve().parents[2] / ".github/scripts/update_code_server_pin.py",
)
assert SPEC and SPEC.loader
updater = module_from_spec(SPEC)
SPEC.loader.exec_module(updater)
NOW = datetime(2026, 10, 8, tzinfo=UTC)
AMD64 = "a" * 64
ARM64 = "b" * 64


def _release(version: str, published: str, **flags: object) -> dict:
    return {
        "tag_name": f"v{version}",
        "published_at": published,
        "draft": False,
        "prerelease": False,
        **flags,
        "assets": [
            {
                "name": f"code-server-{version}-linux-amd64.tar.gz",
                "digest": f"sha256:{AMD64}",
            },
            {
                "name": f"code-server-{version}-linux-arm64.tar.gz",
                "digest": f"sha256:{ARM64}",
            },
        ],
    }


RELEASES = [
    _release("4.144.0", "2026-10-01T00:00:00Z"),
    _release("4.145.0", "2026-10-01T00:00:00Z"),
    _release("4.146.0", "2026-10-07T00:00:00Z"),
    _release("4.147.0-beta.1", "2026-10-01T00:00:00Z", prerelease=True),
    _release("4.148.0", "2026-10-01T00:00:00Z", draft=True),
]
DOCKERFILE = (
    'ARG CODE_SERVER_VERSION="4.144.0"\n'
    f"ARG CODE_SERVER_AMD64_SHA256={'c' * 64}\n"
    f"ARG CODE_SERVER_ARM64_SHA256={'d' * 64}\n"
)


def test_selects_newest_observed_stable_and_updates_all_pins() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "Dockerfile"
        path.write_text(DOCKERFILE)
        assert updater.update_pin(path, RELEASES, NOW) == "4.145.0"
        text = path.read_text()
        assert 'ARG CODE_SERVER_VERSION="4.145.0"' in text
        assert f"ARG CODE_SERVER_AMD64_SHA256={AMD64}" in text
        assert f"ARG CODE_SERVER_ARM64_SHA256={ARM64}" in text


def test_update_is_idempotent_and_never_downgrades() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "Dockerfile"
        path.write_text(DOCKERFILE)
        updater.update_pin(path, RELEASES, NOW)
        before = path.stat().st_mtime_ns
        assert updater.update_pin(path, RELEASES, NOW) == "4.145.0"
        assert path.stat().st_mtime_ns == before

        path.write_text(DOCKERFILE.replace("4.144.0", "4.999.0"))
        assert updater.update_pin(path, RELEASES, NOW) == "4.999.0"


def test_missing_or_ambiguous_pins_are_rejected() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "Dockerfile"
        for text in (
            "",
            DOCKERFILE + DOCKERFILE,
            DOCKERFILE.replace("ARG CODE_SERVER_ARM64_SHA256=", "ARG OTHER="),
        ):
            path.write_text(text)
            try:
                updater.update_pin(path, RELEASES, NOW)
            except ValueError:
                pass
            else:
                raise AssertionError("missing or ambiguous pin accepted")


def test_release_without_asset_digests_fails_before_writing() -> None:
    broken = _release("4.145.0", "2026-10-01T00:00:00Z")
    broken["assets"] = broken["assets"][:1]
    with TemporaryDirectory() as directory:
        path = Path(directory) / "Dockerfile"
        path.write_text(DOCKERFILE)
        try:
            updater.update_pin(path, [broken], NOW)
        except ValueError:
            pass
        else:
            raise AssertionError("release with a missing asset accepted")
        assert path.read_text() == DOCKERFILE
