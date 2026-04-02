import importlib.util
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "scripts"
    / "scan_uv_lock_security.py"
)
SPEC = importlib.util.spec_from_file_location("scan_uv_lock_security", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
SCAN_UV_LOCK_SECURITY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SCAN_UV_LOCK_SECURITY
SPEC.loader.exec_module(SCAN_UV_LOCK_SECURITY)


def write_wheel(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for member_name, text in members.items():
            archive.writestr(member_name, text)


def test_select_packages_detects_dependency_only_change():
    package = {
        "name": "demo",
        "version": "1.0.0",
        "source": {"registry": "https://pypi.org/simple"},
        "sdist": {
            "url": "https://files.pythonhosted.org/packages/demo-1.0.0.tar.gz",
            "hash": "sha256:abc123",
            "size": 123,
            "upload-time": "2026-03-01T00:00:00Z",
        },
        "wheels": [],
    }
    base_lock = {
        "package": [
            {
                **package,
                "dependencies": [{"name": "requests"}],
            }
        ]
    }
    current_lock = {
        "package": [
            {
                **package,
                "dependencies": [{"name": "requests"}, {"name": "urllib3"}],
            }
        ]
    }

    selected = SCAN_UV_LOCK_SECURITY.select_packages(current_lock, base_lock, None)

    assert len(selected) == 1
    assert selected[0].reason == "changed package in lockfile"
    assert selected[0].baseline is not None
    assert SCAN_UV_LOCK_SECURITY.dependency_names(selected[0].package) == [
        "requests",
        "urllib3",
    ]


def test_inspect_artifact_reads_startup_hooks_and_metadata(tmp_path: Path):
    wheel_path = tmp_path / "demo-1.0.0-py3-none-any.whl"
    write_wheel(
        wheel_path,
        {
            "demo-1.0.0.dist-info/METADATA": "\n".join(
                [
                    "Metadata-Version: 2.1",
                    "Name: demo",
                    "Version: 1.0.0",
                    "Requires-Dist: Requests>=2",
                    "Requires-Dist: Local_Pkg; python_version < '4'",
                ]
            ),
            "demo_startup.pth": "import os\nos.environ.get('TOKEN')\n",
        },
    )

    artifact = SCAN_UV_LOCK_SECURITY.Artifact(
        kind="wheel",
        filename=wheel_path.name,
        url="https://example.com/demo.whl",
        hash="",
        size=wheel_path.stat().st_size,
        upload_time=datetime.now(UTC),
    )

    inspection = SCAN_UV_LOCK_SECURITY.inspect_artifact(
        wheel_path,
        artifact,
        max_text_bytes=1_000_000,
        max_hits_per_pattern=5,
    )

    assert inspection.startup_hooks == ["demo_startup.pth"]
    assert inspection.metadata_dependencies == ["local-pkg", "requests"]
    assert inspection.suspicious_hits["env_access"] == [
        "demo_startup.pth:2: os.environ.get('TOKEN')"
    ]


def test_diff_artifacts_reports_member_and_hook_deltas(tmp_path: Path):
    previous_wheel = tmp_path / "demo-0.9.0-py3-none-any.whl"
    current_wheel = tmp_path / "demo-1.0.0-py3-none-any.whl"
    write_wheel(
        previous_wheel,
        {
            "demo/__init__.py": "",
            "demo-0.9.0.dist-info/entry_points.txt": (
                "[console_scripts]\nold = demo:main\n"
            ),
        },
    )
    write_wheel(
        current_wheel,
        {
            "demo/__init__.py": "",
            "demo-1.0.0.dist-info/entry_points.txt": (
                "[console_scripts]\nold = demo:main\nnew = demo.cli:main\n"
            ),
            "demo_startup.pth": "import demo\n",
            "demo/native.so": "binary-placeholder",
            "demo/new_module.py": "print('hi')\n",
        },
    )

    previous_artifact = SCAN_UV_LOCK_SECURITY.Artifact(
        kind="wheel",
        filename=previous_wheel.name,
        url="https://example.com/demo-0.9.0.whl",
        hash="",
        size=previous_wheel.stat().st_size,
        upload_time=datetime.now(UTC),
    )
    current_artifact = SCAN_UV_LOCK_SECURITY.Artifact(
        kind="wheel",
        filename=current_wheel.name,
        url="https://example.com/demo-1.0.0.whl",
        hash="",
        size=current_wheel.stat().st_size,
        upload_time=datetime.now(UTC),
    )

    delta = SCAN_UV_LOCK_SECURITY.diff_artifacts(
        previous_wheel,
        previous_artifact,
        current_wheel,
        current_artifact,
    )

    assert delta.added_member_count == 4
    assert delta.removed_member_count == 1
    assert delta.added_startup_hooks == ["demo_startup.pth"]
    assert delta.added_entry_points == ["new = demo.cli:main"]
    assert delta.added_native_members == ["demo/native.so"]
    assert "demo/new_module.py" in delta.added_members
    assert delta.removed_members == ["demo-0.9.0.dist-info/entry_points.txt"]


def test_render_report_includes_dependency_and_artifact_deltas():
    report = SCAN_UV_LOCK_SECURITY.PackageReport(
        name="demo",
        version="1.0.0",
        reason="changed package in lockfile",
        artifacts=[
            SCAN_UV_LOCK_SECURITY.Artifact(
                kind="wheel",
                filename="demo-1.0.0-py3-none-any.whl",
                url="https://example.com/demo.whl",
                hash="sha256:abc123",
                size=42,
                upload_time=datetime(2026, 3, 1, tzinfo=UTC),
            )
        ],
        lockfile_dependencies=["requests", "urllib3"],
        new_dependencies=["urllib3"],
        inspections=[
            SCAN_UV_LOCK_SECURITY.InspectionResult(
                filename="demo-1.0.0-py3-none-any.whl",
                startup_hooks=["demo_startup.pth"],
                metadata_dependencies=["requests", "urllib3"],
            )
        ],
        artifact_deltas=[
            SCAN_UV_LOCK_SECURITY.ArtifactDelta(
                current_filename="demo-1.0.0-py3-none-any.whl",
                previous_filename="demo-0.9.0-py3-none-any.whl",
                added_member_count=3,
                removed_member_count=1,
                added_members=["demo/new_module.py"],
                removed_members=["demo/old_module.py"],
                added_startup_hooks=["demo_startup.pth"],
                added_entry_points=["new = demo.cli:main"],
                added_native_members=["demo/native.so"],
            )
        ],
    )

    rendered = SCAN_UV_LOCK_SECURITY.render_report(
        [report],
        scanned_count=1,
        total_count=1,
        min_age=None,
    )

    assert "- Lockfile runtime dependencies: `requests`, `urllib3`" in rendered
    assert "- New dependencies vs base: `urllib3`" in rendered
    assert (
        "- Artifact delta: `demo-0.9.0-py3-none-any.whl` -> "
        "`demo-1.0.0-py3-none-any.whl`"
    ) in rendered
    assert "  - members added/removed: +3 / -1" in rendered
    assert "  - new startup hooks: `demo_startup.pth`" in rendered
    assert "  - new entry points: `new = demo.cli:main`" in rendered
    assert "  - startup hooks: `demo_startup.pth`" in rendered
    assert "  - metadata Requires-Dist: `requests`, `urllib3`" in rendered
