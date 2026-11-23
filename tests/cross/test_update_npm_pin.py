from datetime import UTC, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory


SPEC = spec_from_file_location(
    "update_npm_pin",
    Path(__file__).resolve().parents[2] / ".github/scripts/update_npm_pin.py",
)
assert SPEC and SPEC.loader
updater = module_from_spec(SPEC)
SPEC.loader.exec_module(updater)
NOW = datetime(2026, 10, 8, tzinfo=UTC)


def test_major_crossing_is_an_explicit_opt_in() -> None:
    versions = {
        "11.19.1": {},
        "11.21.0": {},
        "11.22.0": {},
        "12.2.0": {},
        "11.30.0-beta.1": {},
        "11.23.0": {"deprecated": "withdrawn"},
    }
    metadata = {
        "versions": versions,
        "time": dict.fromkeys(versions, "2026-10-01T00:00:00Z"),
    }
    metadata["time"]["11.22.0"] = "2026-10-01T00:00:01Z"
    # Default policy stays on the current major.
    assert updater.select_version(metadata, "11.19.1", NOW) == "11.21.0"
    assert updater.select_version(metadata, "11.25.0", NOW) == "11.25.0"
    # The scheduled workflow opts in explicitly (ALLOW_MAJOR_UPGRADE=true);
    # only that path may cross to the newest observed stable major.
    assert (
        updater.select_version(metadata, "11.19.1", NOW, allow_major=True) == "12.2.0"
    )
    assert (
        updater.select_version(metadata, "11.25.0", NOW, allow_major=True) == "12.2.0"
    )

    metadata["time"]["12.2.0"] = "2026-10-01T00:00:01Z"
    assert (
        updater.select_version(metadata, "11.19.1", NOW, allow_major=True) == "11.21.0"
    )


def test_update_is_idempotent_and_requires_exactly_one_pin() -> None:
    metadata = {
        "versions": {"11.21.0": {}},
        "time": {"11.21.0": "2026-09-30T18:08:02Z"},
    }
    with TemporaryDirectory() as directory:
        path = Path(directory) / "Dockerfile"
        path.write_text("ARG NPM_VERSION=11.19.1\nARG NPM_VERSION\n")
        assert updater.update_pin(path, metadata, NOW) == "11.21.0"
        assert path.read_text() == "ARG NPM_VERSION=11.21.0\nARG NPM_VERSION\n"
        before = path.stat().st_mtime_ns
        updater.update_pin(path, metadata, NOW)
        assert path.stat().st_mtime_ns == before
        for text in ("", "ARG NPM_VERSION=11.19.1\nARG NPM_VERSION=11.20.0\n"):
            path.write_text(text)
            try:
                updater.update_pin(path, metadata, NOW)
            except ValueError:
                pass
            else:
                raise AssertionError("ambiguous or absent pin accepted")
