"""Deterministic red->green verification. The remediation gate.

Run as a separate workflow step, not by the agent: it trusts nothing the agent
reports and applies the patches to a clean checkout itself. The regression test
must FAIL at the base commit with only ``test.patch`` applied and PASS once
``fix.patch`` is added; ``fix.patch`` may not touch the regression-test file, so
a "fix" cannot just weaken the test.

Uses pytest's built-in JUnit XML (``--junitxml``), so there is no plugin
dependency.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self


NodeOutcome = Literal["passed", "failed", "missing"]


def classify_junit(xml_text: str, node_id: str) -> NodeOutcome:
    """Classify one node id from a JUnit XML report.

    ``passed`` if it ran and passed; ``failed`` if it ran and did not
    (assertion, error, or skip); ``missing`` if it is not in the report.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return "missing"
    want_file, _, want_name = node_id.partition("::")
    want_base = want_name.split("[", 1)[0]
    # pytest's JUnit often omits ``file`` and carries only ``classname``
    # (``tests.pkg.test_x``); compare in a normalized dotted form.
    want_path = want_file.removesuffix(".py").replace("/", ".")
    for case in root.iter("testcase"):
        name = case.get("name", "")
        if name.split("[", 1)[0] != want_base:
            continue
        candidate = (case.get("file") or "").removesuffix(".py").replace(
            "/", "."
        ) or case.get("classname", "")
        if (
            want_path
            and candidate
            and not (candidate.endswith(want_path) or want_path.endswith(candidate))
        ):
            continue
        if (
            case.find("failure") is None
            and case.find("error") is None
            and (case.find("skipped") is None)
        ):
            return "passed"
        return "failed"
    return "missing"


@dataclass(frozen=True, slots=True)
class VerificationSpec:
    dedup_key: str
    target_repo: str
    base_sha: str
    test_node_ids: tuple[str, ...]
    test_patch: str
    fix_patch: str

    @classmethod
    def from_json(cls, data: dict) -> Self:
        return cls(
            dedup_key=str(data["dedup_key"]),
            target_repo=str(data["target_repo"]),
            base_sha=str(data["base_sha"]),
            test_node_ids=tuple(data["test_node_ids"]),
            test_patch=str(data["test_patch"]),
            fix_patch=str(data["fix_patch"]),
        )


@dataclass(slots=True)
class VerificationResult:
    passed: bool
    reason: str
    red_outcomes: list[NodeOutcome] = field(default_factory=list)
    green_outcomes: list[NodeOutcome] = field(default_factory=list)


# A runner takes (repo_dir, node_ids) and returns the JUnit XML for that run.
PytestRunner = Callable[[Path, tuple[str, ...]], str]


def _run_git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _reset(repo_dir: Path) -> None:
    _run_git(repo_dir, "checkout", "--", ".")
    _run_git(repo_dir, "clean", "-fdx")


def _apply(repo_dir: Path, patch_text: str) -> bool:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".patch", delete=False, dir=repo_dir
    ) as fh:
        fh.write(patch_text)
        patch_path = fh.name
    try:
        return _run_git(repo_dir, "apply", "--3way", patch_path).returncode == 0
    finally:
        Path(patch_path).unlink(missing_ok=True)


def _default_runner(repo_dir: Path, node_ids: tuple[str, ...]) -> str:
    """Run pytest for the given node ids and return the JUnit XML."""
    with tempfile.NamedTemporaryFile("r", suffix=".xml", delete=False) as fh:
        xml_path = fh.name
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "--junitxml",
                xml_path,
                *node_ids,
            ],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        return Path(xml_path).read_text()
    finally:
        Path(xml_path).unlink(missing_ok=True)


def _outcomes(
    repo_dir: Path, spec: VerificationSpec, patches: list[str], runner: PytestRunner
) -> list[NodeOutcome] | None:
    _reset(repo_dir)
    for patch in patches:
        if not _apply(repo_dir, patch):
            _reset(repo_dir)
            return None  # patch did not apply cleanly
    xml = runner(repo_dir, spec.test_node_ids)
    outcomes: list[NodeOutcome] = [classify_junit(xml, n) for n in spec.test_node_ids]
    _reset(repo_dir)
    return outcomes


def run_red_green(
    repo_dir: Path, spec: VerificationSpec, *, runner: PytestRunner | None = None
) -> VerificationResult:
    """Apply test.patch (must fail), then + fix.patch (must pass)."""
    run = runner or _default_runner

    test_file = spec.test_node_ids[0].split("::")[0] if spec.test_node_ids else ""
    if test_file and test_file in spec.fix_patch:
        return VerificationResult(
            False, "fix.patch must not modify the regression test"
        )

    red = _outcomes(repo_dir, spec, [spec.test_patch], run)
    if red is None:
        return VerificationResult(False, "test.patch did not apply at base_sha")
    green = _outcomes(repo_dir, spec, [spec.test_patch, spec.fix_patch], run)
    if green is None:
        return VerificationResult(False, "fix.patch did not apply at base_sha", red, [])

    if not all(o == "failed" for o in red):
        return VerificationResult(
            False, f"RED gate: test must fail before the fix (saw {red})", red, green
        )
    if not all(o == "passed" for o in green):
        return VerificationResult(
            False, f"GREEN gate: test must pass after the fix (saw {green})", red, green
        )
    return VerificationResult(True, "red->green verified", red, green)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic red->green verifier")
    parser.add_argument("--spec", required=True, help="path to verification.json")
    parser.add_argument("--repo-dir", required=True, help="clean checkout at base_sha")
    parser.add_argument("--patch-dir", required=True, help="dir with the .patch files")
    args = parser.parse_args(argv)

    data = json.loads(Path(args.spec).read_text())
    patch_dir = Path(args.patch_dir)
    data["test_patch"] = (patch_dir / data["test_patch"]).read_text()
    data["fix_patch"] = (patch_dir / data["fix_patch"]).read_text()
    result = run_red_green(Path(args.repo_dir), VerificationSpec.from_json(data))

    print(json.dumps({"passed": result.passed, "reason": result.reason}))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
