#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tarfile
import tomllib
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


COMMON_BUILD_BACKENDS = {
    "flit_core.buildapi",
    "hatchling.build",
    "maturin",
    "pdm.backend",
    "poetry.core.masonry.api",
    "setuptools.build_meta",
    "setuptools.build_meta:__legacy__",
}

TEXT_FILE_SUFFIXES = {
    ".bat",
    ".cmd",
    ".js",
    ".json",
    ".ps1",
    ".pth",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

IGNORED_PATH_PARTS = {
    "benchmark",
    "benchmarks",
    "doc",
    "docs",
    "example",
    "examples",
    "script",
    "scripts",
    "test",
    "tests",
}

NATIVE_SUFFIXES = {".dll", ".dylib", ".node", ".pyd", ".so"}

SUSPICIOUS_PATTERNS = {
    "subprocess": re.compile(
        r"\bimport subprocess\b|\bfrom subprocess import\b|"
        r"\basyncio\.create_subprocess_exec\b|\bos\.system\b"
    ),
    "dynamic_execution": re.compile(r"\beval\(|\bexec\("),
    "network_fetch": re.compile(
        r"\burllib\.request\.urlopen\b|\bhttpx\.(AsyncClient|Client|get|post|stream)\b|"
        r"\brequests\.(get|post|request|Session)\b"
    ),
    "base64_decode": re.compile(r"\bbase64\.(b64decode|urlsafe_b64decode)\b"),
    "env_access": re.compile(r"\bos\.(getenv|environ)\b"),
}


@dataclass(slots=True)
class Artifact:
    kind: str
    filename: str
    url: str
    hash: str
    size: int | None
    upload_time: datetime | None


@dataclass(slots=True)
class InspectionResult:
    filename: str
    native_members: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    startup_hooks: list[str] = field(default_factory=list)
    metadata_dependencies: list[str] = field(default_factory=list)
    build_backend: str | None = None
    has_setup_py: bool = False
    suspicious_hits: dict[str, list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class PackageReport:
    name: str
    version: str
    reason: str
    artifacts: list[Artifact]
    lockfile_dependencies: list[str] = field(default_factory=list)
    new_dependencies: list[str] = field(default_factory=list)
    removed_dependencies: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    inspections: list[InspectionResult] = field(default_factory=list)


@dataclass(slots=True)
class SelectedPackage:
    package: dict[str, Any]
    baseline: dict[str, Any] | None
    reason: str


class ScanError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Statically review uv.lock packages without importing or executing them."
        )
    )
    parser.add_argument("--lockfile", type=Path, default=Path("uv.lock"))
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--base-lockfile", type=Path)
    parser.add_argument(
        "--download-dir", type=Path, default=Path(".agent_tmp/uv-security-scan")
    )
    parser.add_argument("--write-requirements", type=Path)
    parser.add_argument("--max-text-bytes", type=int, default=1_000_000)
    parser.add_argument("--max-hits-per-pattern", type=int, default=5)
    parser.add_argument("--min-age-days", type=float)
    return parser.parse_args()


def load_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def parse_upload_time(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    normalized = raw.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(UTC)


def parse_age_window(raw: str | None) -> timedelta | None:
    if raw is None:
        return None
    value = raw.strip().lower()
    iso_match = re.fullmatch(r"p(?:(?P<weeks>\d+)w)?(?:(?P<days>\d+)d)?", value)
    if iso_match:
        weeks = int(iso_match.group("weeks") or 0)
        days = int(iso_match.group("days") or 0)
        return timedelta(days=days + (weeks * 7))

    match = re.fullmatch(
        r"(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>day|days|week|weeks|hour|hours)", value
    )
    if not match:
        return None

    number = float(match.group("number"))
    unit = match.group("unit")
    if unit.startswith("day"):
        return timedelta(days=number)
    if unit.startswith("week"):
        return timedelta(days=number * 7)
    if unit.startswith("hour"):
        return timedelta(hours=number)
    return None


def get_min_age(args: argparse.Namespace) -> timedelta | None:
    if args.min_age_days is not None:
        return timedelta(days=args.min_age_days)

    pyproject = load_toml(args.pyproject)
    exclude_newer = pyproject.get("tool", {}).get("uv", {}).get("exclude-newer")
    window = parse_age_window(exclude_newer)
    if window is not None:
        return window

    lock = load_toml(args.lockfile)
    options = lock.get("options", {})
    return parse_age_window(options.get("exclude-newer-span"))


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def dependency_names(package: dict[str, Any]) -> list[str]:
    return sorted(
        {
            normalize_name(dependency["name"])
            for dependency in package.get("dependencies", [])
        }
    )


def parse_requires_dist_lines(text: str) -> list[str]:
    dependencies: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("Requires-Dist:"):
            continue
        match = re.match(r"Requires-Dist:\s*([A-Za-z0-9][A-Za-z0-9_.-]*)", line)
        if match is not None:
            dependencies.add(normalize_name(match.group(1)))
    return sorted(dependencies)


def normalize_package(package: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": package.get("version"),
        "source": package.get("source"),
        "dependencies": dependency_names(package),
        "sdist": package.get("sdist"),
        "wheels": package.get("wheels", []),
    }


def package_artifacts(package: dict[str, Any]) -> list[Artifact]:
    artifacts: list[Artifact] = []
    sdist = package.get("sdist")
    if sdist:
        artifacts.append(
            Artifact(
                kind="sdist",
                filename=Path(sdist["url"]).name,
                url=sdist["url"],
                hash=sdist.get("hash", ""),
                size=sdist.get("size"),
                upload_time=parse_upload_time(sdist.get("upload-time")),
            )
        )

    for wheel in package.get("wheels", []):
        artifacts.append(
            Artifact(
                kind="wheel",
                filename=Path(wheel["url"]).name,
                url=wheel["url"],
                hash=wheel.get("hash", ""),
                size=wheel.get("size"),
                upload_time=parse_upload_time(wheel.get("upload-time")),
            )
        )
    return artifacts


def choose_artifacts(artifacts: list[Artifact]) -> list[Artifact]:
    selected: list[Artifact] = []
    sdist = next((artifact for artifact in artifacts if artifact.kind == "sdist"), None)
    if sdist is not None:
        selected.append(sdist)

    wheels = [artifact for artifact in artifacts if artifact.kind == "wheel"]
    universal = next(
        (artifact for artifact in wheels if artifact.filename.endswith("none-any.whl")),
        None,
    )
    if universal is not None:
        selected.append(universal)
    elif len(wheels) == 1:
        selected.append(wheels[0])

    deduped: list[Artifact] = []
    seen: set[str] = set()
    for artifact in selected:
        if artifact.filename not in seen:
            deduped.append(artifact)
            seen.add(artifact.filename)
    return deduped


def sha256_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_artifact(artifact: Artifact, download_dir: Path) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    destination = download_dir / artifact.filename
    if not destination.exists():
        request = urllib.request.Request(
            artifact.url,
            headers={"User-Agent": "OpenHands uv dependency security scan"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            destination.write_bytes(response.read())

    expected_hash = artifact.hash.removeprefix("sha256:")
    actual_hash = sha256_digest(destination)
    if expected_hash and actual_hash != expected_hash:
        raise ScanError(
            "Hash mismatch for "
            f"{artifact.filename}: expected {expected_hash}, got {actual_hash}"
        )
    return destination


def read_member_text(data: bytes) -> str:
    return data.decode("utf-8", errors="ignore")


def record_pattern_hits(
    suspicious_hits: dict[str, list[str]],
    member_name: str,
    text: str,
    *,
    max_hits_per_pattern: int,
) -> None:
    for label, pattern in SUSPICIOUS_PATTERNS.items():
        hits = suspicious_hits.setdefault(label, [])
        if len(hits) >= max_hits_per_pattern:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                hits.append(f"{member_name}:{line_number}: {line.strip()}")
                if len(hits) >= max_hits_per_pattern:
                    break


def should_scan_text_member(member_name: str) -> bool:
    parts = {part.lower() for part in Path(member_name).parts}
    return parts.isdisjoint(IGNORED_PATH_PARTS)


def is_python_startup_hook(member_name: str) -> bool:
    member_path = Path(member_name)
    return member_path.suffix.lower() == ".pth" or member_path.name in {
        "sitecustomize.py",
        "usercustomize.py",
    }


def inspect_wheel(
    path: Path, *, max_text_bytes: int, max_hits_per_pattern: int
) -> InspectionResult:
    result = InspectionResult(filename=path.name)
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            member_name = member.filename
            suffix = Path(member_name).suffix.lower()
            if suffix in NATIVE_SUFFIXES:
                result.native_members.append(member_name)

            if member_name.endswith("entry_points.txt"):
                result.entry_points.extend(
                    line.strip()
                    for line in read_member_text(archive.read(member)).splitlines()
                    if line.strip() and not line.startswith("[")
                )
            if member_name.endswith(".dist-info/METADATA"):
                result.metadata_dependencies = parse_requires_dist_lines(
                    read_member_text(archive.read(member))
                )
            if is_python_startup_hook(member_name):
                result.startup_hooks.append(member_name)

            if (
                suffix in TEXT_FILE_SUFFIXES
                and member.file_size <= max_text_bytes
                and should_scan_text_member(member_name)
            ):
                text = read_member_text(archive.read(member))
                record_pattern_hits(
                    result.suspicious_hits,
                    member_name,
                    text,
                    max_hits_per_pattern=max_hits_per_pattern,
                )
    return result


def inspect_sdist(
    path: Path, *, max_text_bytes: int, max_hits_per_pattern: int
) -> InspectionResult:
    result = InspectionResult(filename=path.name)
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            member_name = member.name
            suffix = Path(member_name).suffix.lower()
            if suffix in NATIVE_SUFFIXES:
                result.native_members.append(member_name)
            if member_name.endswith("setup.py"):
                result.has_setup_py = True

            if member_name.endswith("pyproject.toml"):
                extracted = archive.extractfile(member)
                if extracted is not None:
                    pyproject = tomllib.loads(read_member_text(extracted.read()))
                    result.build_backend = pyproject.get("build-system", {}).get(
                        "build-backend"
                    )
            if member_name.endswith("PKG-INFO"):
                extracted = archive.extractfile(member)
                if extracted is not None:
                    result.metadata_dependencies = parse_requires_dist_lines(
                        read_member_text(extracted.read())
                    )
            if is_python_startup_hook(member_name):
                result.startup_hooks.append(member_name)

            if (
                suffix in TEXT_FILE_SUFFIXES
                and member.size <= max_text_bytes
                and should_scan_text_member(member_name)
            ):
                extracted = archive.extractfile(member)
                if extracted is not None:
                    text = read_member_text(extracted.read())
                    record_pattern_hits(
                        result.suspicious_hits,
                        member_name,
                        text,
                        max_hits_per_pattern=max_hits_per_pattern,
                    )
    return result


def inspect_artifact(
    path: Path,
    artifact: Artifact,
    *,
    max_text_bytes: int,
    max_hits_per_pattern: int,
) -> InspectionResult:
    if artifact.kind == "wheel":
        return inspect_wheel(
            path,
            max_text_bytes=max_text_bytes,
            max_hits_per_pattern=max_hits_per_pattern,
        )
    return inspect_sdist(
        path,
        max_text_bytes=max_text_bytes,
        max_hits_per_pattern=max_hits_per_pattern,
    )


def latest_upload_time(artifacts: list[Artifact]) -> datetime | None:
    upload_times = [
        artifact.upload_time for artifact in artifacts if artifact.upload_time
    ]
    if not upload_times:
        return None
    return max(upload_times)


def select_packages(
    current_lock: dict[str, Any],
    base_lock: dict[str, Any] | None,
    min_age: timedelta | None,
) -> list[SelectedPackage]:
    current_packages = {
        package["name"]: package for package in current_lock.get("package", [])
    }
    if base_lock is not None:
        base_packages = {
            package["name"]: package for package in base_lock.get("package", [])
        }
        changed: list[SelectedPackage] = []
        for name, package in sorted(current_packages.items()):
            baseline = base_packages.get(name)
            if baseline is None:
                changed.append(
                    SelectedPackage(
                        package=package, baseline=None, reason="new package in lockfile"
                    )
                )
            elif normalize_package(baseline) != normalize_package(package):
                changed.append(
                    SelectedPackage(
                        package=package,
                        baseline=baseline,
                        reason="changed package in lockfile",
                    )
                )
        return changed

    selected: list[SelectedPackage] = []
    now = datetime.now(UTC)
    for package in sorted(current_packages.values(), key=lambda entry: entry["name"]):
        artifacts = package_artifacts(package)
        source = package.get("source", {})
        if any(key in source for key in ("git", "path", "url")):
            selected.append(
                SelectedPackage(
                    package=package, baseline=None, reason="non-registry source"
                )
            )
            continue
        if min_age is None:
            continue
        newest = latest_upload_time(artifacts)
        if newest is None:
            continue
        if now - newest < min_age:
            selected.append(
                SelectedPackage(
                    package=package,
                    baseline=None,
                    reason="package newer than minimum age policy",
                )
            )
    return selected


def write_requirements(path: Path, packages: list[dict[str, Any]]) -> None:
    lines = [f"{package['name']}=={package['version']}" for package in packages]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def render_report(
    reports: list[PackageReport],
    *,
    scanned_count: int,
    total_count: int,
    min_age: timedelta | None,
) -> str:
    lines = [
        "# uv lock static security scan",
        "",
        f"- Selected packages: {scanned_count} of {total_count}",
        (
            f"- Minimum artifact age policy: {min_age.total_seconds() / 86400:g} days"
            if min_age is not None
            else "- Minimum artifact age policy: none"
        ),
        "",
    ]

    if not reports:
        lines.append("No packages matched the current scan policy.")
        return "\n".join(lines)

    for report in reports:
        lines.extend(
            [
                f"## {report.name} {report.version}",
                f"- Reason selected: {report.reason}",
            ]
        )

        newest = latest_upload_time(report.artifacts)
        if newest is not None:
            age = datetime.now(UTC) - newest
            age_days = age.total_seconds() / 86400
            lines.append(
                f"- Newest artifact upload: {newest.isoformat()} "
                f"({age_days:.2f} days old)"
            )

        if report.lockfile_dependencies:
            dependencies = ", ".join(
                f"`{dependency}`" for dependency in report.lockfile_dependencies
            )
            lines.append(f"- Lockfile runtime dependencies: {dependencies}")
        if report.new_dependencies:
            new_dependencies = ", ".join(
                f"`{dependency}`" for dependency in report.new_dependencies
            )
            lines.append(f"- New dependencies vs base: {new_dependencies}")
        if report.removed_dependencies:
            removed_dependencies = ", ".join(
                f"`{dependency}`" for dependency in report.removed_dependencies
            )
            lines.append(f"- Removed dependencies vs base: {removed_dependencies}")

        if report.violations:
            lines.append("- Policy violations:")
            lines.extend(f"  - {violation}" for violation in report.violations)
        else:
            lines.append("- Policy violations: none")

        if report.notes:
            lines.append("- Notes:")
            lines.extend(f"  - {note}" for note in report.notes)

        for inspection in report.inspections:
            lines.append(f"- Inspected artifact: `{inspection.filename}`")
            if inspection.build_backend:
                lines.append(f"  - build backend: `{inspection.build_backend}`")
            if inspection.has_setup_py:
                lines.append("  - contains `setup.py`")
            if inspection.startup_hooks:
                startup_hooks = ", ".join(
                    f"`{startup_hook}`" for startup_hook in inspection.startup_hooks
                )
                lines.append(f"  - startup hooks: {startup_hooks}")
            if inspection.metadata_dependencies:
                metadata_dependencies = ", ".join(
                    f"`{dependency}`" for dependency in inspection.metadata_dependencies
                )
                lines.append(f"  - metadata Requires-Dist: {metadata_dependencies}")
            if inspection.entry_points:
                entry_points = ", ".join(
                    f"`{entry}`" for entry in inspection.entry_points
                )
                lines.append(f"  - entry points: {entry_points}")
            if inspection.native_members:
                lines.append("  - native files present:")
                lines.extend(
                    f"    - `{member}`" for member in inspection.native_members[:10]
                )
            for label, hits in sorted(inspection.suspicious_hits.items()):
                if not hits:
                    continue
                lines.append(f"  - {label} hits:")
                lines.extend(f"    - `{hit}`" for hit in hits)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    current_lock = load_toml(args.lockfile)
    base_lock = (
        load_toml(args.base_lockfile)
        if args.base_lockfile and args.base_lockfile.exists()
        else None
    )
    min_age = get_min_age(args)
    selected = select_packages(current_lock, base_lock, min_age)
    packages_only = [item.package for item in selected]

    if args.write_requirements is not None:
        write_requirements(args.write_requirements, packages_only)

    reports: list[PackageReport] = []
    now = datetime.now(UTC)

    for item in selected:
        package = item.package
        baseline = item.baseline
        lockfile_dependencies = dependency_names(package)
        baseline_dependencies = (
            dependency_names(baseline) if baseline is not None else []
        )
        report = PackageReport(
            name=package["name"],
            version=package["version"],
            reason=item.reason,
            artifacts=package_artifacts(package),
            lockfile_dependencies=lockfile_dependencies,
            new_dependencies=sorted(
                set(lockfile_dependencies) - set(baseline_dependencies)
            ),
            removed_dependencies=sorted(
                set(baseline_dependencies) - set(lockfile_dependencies)
            ),
        )

        source = package.get("source", {})
        registry = source.get("registry")
        if registry is None:
            report.violations.append("package source is not a registry source")
        elif registry != "https://pypi.org/simple":
            report.notes.append(f"package uses non-default registry `{registry}`")

        if any(key in source for key in ("git", "path", "url")):
            report.violations.append(
                "package uses a direct git/path/url source instead of a registry lock"
            )

        if not report.artifacts:
            report.violations.append("package has no locked sdist or wheel artifacts")

        newest = latest_upload_time(report.artifacts)
        if min_age is not None and newest is not None and now - newest < min_age:
            newest_age_days = (now - newest).total_seconds() / 86400
            minimum_age_days = min_age.total_seconds() / 86400
            report.violations.append(
                f"newest artifact is {newest_age_days:.2f} days old, "
                f"below the {minimum_age_days:g}-day minimum"
            )

        for artifact in report.artifacts:
            if not artifact.hash:
                report.violations.append(
                    f"{artifact.filename} is missing a hash in uv.lock"
                )
            if artifact.upload_time is None:
                report.notes.append(
                    f"{artifact.filename} is missing upload-time metadata"
                )

        for artifact in choose_artifacts(report.artifacts):
            downloaded = download_artifact(artifact, args.download_dir)
            inspection = inspect_artifact(
                downloaded,
                artifact,
                max_text_bytes=args.max_text_bytes,
                max_hits_per_pattern=args.max_hits_per_pattern,
            )
            report.inspections.append(inspection)

            if (
                inspection.build_backend
                and inspection.build_backend not in COMMON_BUILD_BACKENDS
            ):
                report.notes.append(
                    f"uncommon build backend `{inspection.build_backend}` "
                    "deserves manual review"
                )
            if inspection.native_members:
                report.notes.append(
                    f"artifact `{inspection.filename}` contains native files "
                    "that merit platform-specific review"
                )
            if inspection.startup_hooks:
                report.notes.append(
                    f"artifact `{inspection.filename}` contains Python startup hooks "
                    "that can run at interpreter startup"
                )

        metadata_dependencies = sorted(
            {
                dependency
                for inspection in report.inspections
                for dependency in inspection.metadata_dependencies
            }
        )
        extra_metadata_dependencies = sorted(
            set(metadata_dependencies) - set(report.lockfile_dependencies)
        )
        if extra_metadata_dependencies:
            report.notes.append(
                "artifact metadata declares additional Requires-Dist names not "
                f"present in the lock entry: {', '.join(extra_metadata_dependencies)}"
            )

        reports.append(report)

    report_text = render_report(
        reports,
        scanned_count=len(selected),
        total_count=len(current_lock.get("package", [])),
        min_age=min_age,
    )
    print(report_text)

    return 1 if any(report.violations for report in reports) else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ScanError as exc:
        print(f"scan failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
