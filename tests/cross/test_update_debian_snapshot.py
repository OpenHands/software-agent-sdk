from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / ".github" / "scripts" / "update_debian_snapshot.py"
SPEC = spec_from_file_location("update_debian_snapshot", SCRIPT_PATH)
assert SPEC and SPEC.loader
snapshot = module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)


def test_eligible_snapshot_is_at_least_three_days_old() -> None:
    now = datetime(2026, 9, 15, 12, 30, tzinfo=UTC)

    selected = snapshot.eligible_snapshot(now)

    assert selected == datetime(2026, 9, 12, tzinfo=UTC)
    assert now - selected >= timedelta(days=3)


def test_validate_snapshot_age_rejects_fresh_snapshot() -> None:
    now = datetime(2026, 9, 15, 12, 30, tzinfo=UTC)

    with pytest.raises(ValueError, match="minimum age"):
        snapshot.validate_snapshot_age(now - timedelta(days=2), now)


def test_update_dockerfile_replaces_exactly_one_pin(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM debian:trixie-slim\nARG DEBIAN_SNAPSHOT=20260901T000000Z\n"
    )

    assert snapshot.update_dockerfile(dockerfile, "20260908T000000Z")
    assert "ARG DEBIAN_SNAPSHOT=20260908T000000Z" in dockerfile.read_text()
    assert not snapshot.update_dockerfile(dockerfile, "20260908T000000Z")


def test_update_dockerfile_does_not_downgrade_newer_pin(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM debian:trixie-slim\nARG DEBIAN_SNAPSHOT=20260913T000000Z\n"
    )

    assert not snapshot.update_dockerfile(dockerfile, "20260908T000000Z")
    assert "ARG DEBIAN_SNAPSHOT=20260913T000000Z" in dockerfile.read_text()


def test_update_dockerfile_rejects_missing_pin(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM debian:trixie-slim\n")

    with pytest.raises(ValueError, match="exactly one"):
        snapshot.update_dockerfile(dockerfile, "20260908T000000Z")


def test_workflow_builds_and_scans_before_opening_pr() -> None:
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "update-debian-snapshot.yml"
    ).read_text()

    build = workflow.index("- name: Build binary-minimal")
    scan = workflow.index("- name: Scan binary-minimal")
    pull_request = workflow.index("- name: Open or refresh update PR")
    assert build < scan < pull_request
    assert (
        "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25" in workflow
    )
    assert "cron: 17 6 * * 1" in workflow
