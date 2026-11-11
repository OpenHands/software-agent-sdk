"""Release cleanup of `.pr/` artifacts, run on real Git fixtures."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ".github/scripts/remove_pr_artifacts.py"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
PREPARE = "prepare-release.yml"
CLEANUP = "Remove temporary PR artifacts"
GUARD = "Reject temporary artifacts in release PRs"
NOTHING_TO_REMOVE = "No tracked .pr/ artifacts to remove.\n"
GIT_ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=GIT_ENV,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _write(repo: Path, files: dict[str, str | bytes]) -> None:
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _tracked(repo: Path, ref: str = "HEAD") -> set[str]:
    names = _git(repo, "ls-tree", "-r", "-z", "--name-only", ref)
    return set(filter(None, names.split("\0")))


def _changes(repo: Path, base: str, head: str) -> set[tuple[str, str]]:
    fields = _git(repo, "diff", "--name-status", "--no-renames", "-z", base, head)
    tokens = fields.split("\0")[:-1]
    return set(zip(tokens[::2], tokens[1::2]))


def _main_repo(directory: str, *, evidence: bool = True) -> Path:
    repo = Path(directory) / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(
        repo,
        {
            SCRIPT: (REPO_ROOT / SCRIPT).read_text(),
            "pyproject.toml": 'version = "1.0.0"\n',
            "README.md": "readme\n",
            "src/app.py": "print('app')\n",
            ".pr-notes/keep.md": "not review evidence\n",
            ".prettierrc": "{}\n",
            "docs/.pr/keep.md": "not root evidence\n",
        },
    )
    if evidence:
        _write(
            repo,
            {
                ".pr/notes.md": "evidence\n",
                ".pr/-leading dash.txt": "evidence\n",
                ".pr/media/demo.mp4": b"\x00\x00\x00\x18ftypmp42\x00\xff",
                ".pr/media/screens/shot.png": b"\x89PNG\r\n\x1a\n\x00\xfe",
            },
        )
        os.symlink("../README.md", repo / ".pr" / "readme-link")
        os.symlink("../src", repo / ".pr" / "src-link")
    _commit(repo, "main")
    return repo


def _step_script(workflow: str, name: str) -> str:
    lines = (WORKFLOWS / workflow).read_text().splitlines()
    stripped = [line.strip() for line in lines]
    start = stripped.index(f"- name: {name}")
    run = next(i for i in range(start, len(lines)) if stripped[i].startswith("run:"))
    inline = stripped[run].removeprefix("run:").strip()
    if inline != "|":
        return inline
    indent = lines[run].index("run:")
    body: list[str] = []
    for line in lines[run + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line)
    return textwrap.dedent("\n".join(body))


def _run_step(
    repo: Path, workflow: str, name: str, **env: str
) -> subprocess.CompletedProcess[str]:
    script = _step_script(workflow, name)
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=repo,
        env={**GIT_ENV, **env},
        capture_output=True,
        text=True,
    )


def _cleanup(repo: Path, cwd: Path | None = None) -> None:
    subprocess.run(
        [sys.executable, str(repo / SCRIPT)],
        cwd=cwd or repo,
        env=GIT_ENV,
        check=True,
        capture_output=True,
    )


def _prepare_release(repo: Path, version: str = "1.2.3") -> str:
    """Run the release-preparation steps that touch Git, in workflow order."""
    env = {"INPUTS_VERSION": version, "GITHUB_ENV": str(repo.parent / "github_env")}
    outputs: list[str] = []
    for name in ("Create release branch", CLEANUP):
        result = _run_step(repo, PREPARE, name, **env)
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)
    # Stands in for the uv and npm version steps.
    (repo / "pyproject.toml").write_text(f'version = "{version}"\n')
    result = _run_step(repo, PREPARE, "Commit version changes", **env)
    assert result.returncode == 0, result.stderr
    return outputs[-1]


def _advance_main(repo: Path, files: dict[str, str | bytes]) -> str:
    _git(repo, "checkout", "-q", "main")
    _write(repo, files)
    _commit(repo, "later product PR")
    _git(repo, "checkout", "-q", "rel-1.2.3")
    return _git(repo, "rev-parse", "main").strip()


def test_prepare_release_removes_root_evidence_in_the_version_commit() -> None:
    with TemporaryDirectory() as directory:
        repo = _main_repo(directory)
        main_files = _tracked(repo)
        evidence = {path for path in main_files if path.startswith(".pr/")}
        assert len(evidence) == 6

        _prepare_release(repo)

        assert _git(repo, "rev-list", "--count", "main..HEAD") == "1\n"
        expected = {("D", path) for path in evidence} | {("M", "pyproject.toml")}
        assert _changes(repo, "main", "HEAD") == expected
        assert _tracked(repo) == main_files - evidence
        assert not (repo / ".pr").exists()
        assert (repo / "README.md").read_text() == "readme\n"
        assert (repo / "src" / "app.py").read_text() == "print('app')\n"

        rerun = _run_step(repo, PREPARE, CLEANUP)
        assert rerun.returncode == 0, rerun.stderr
        assert rerun.stdout == NOTHING_TO_REMOVE
        assert _git(repo, "status", "--porcelain") == ""


def test_prepare_release_without_evidence_commits_only_version_changes() -> None:
    with TemporaryDirectory() as directory:
        repo = _main_repo(directory, evidence=False)

        output = _prepare_release(repo)

        assert output == NOTHING_TO_REMOVE
        assert _changes(repo, "main", "HEAD") == {("M", "pyproject.toml")}


def test_cleanup_resolves_root_pr_from_a_subdirectory_without_following_it() -> None:
    with TemporaryDirectory() as directory:
        repo = _main_repo(directory, evidence=False)
        os.symlink("docs", repo / ".pr")
        _commit(repo, "root .pr symlink")

        _cleanup(repo, cwd=repo / "docs")

        assert _git(repo, "diff", "--cached", "--name-status") == "D\t.pr\n"
        assert not (repo / ".pr").is_symlink()
        nested = repo / "docs" / ".pr" / "keep.md"
        assert nested.read_text() == "not root evidence\n"


def test_refresh_removes_new_base_evidence_in_the_merge_commit() -> None:
    with TemporaryDirectory() as directory:
        repo = _main_repo(directory)
        _prepare_release(repo)
        release_head = _git(repo, "rev-parse", "HEAD").strip()
        main_head = _advance_main(
            repo,
            {
                ".pr/notes.md": "evidence edited by a later PR\n",
                ".pr/later/run.log": "new evidence\n",
                "src/feature.py": "print('feature')\n",
            },
        )

        # Modify/delete conflict: the release deleted notes.md and main edited it.
        merge = subprocess.run(
            ["git", "merge", "--no-commit", "main"],
            cwd=repo,
            env=GIT_ENV,
            capture_output=True,
            text=True,
        )
        assert merge.returncode == 1, merge.stdout
        _cleanup(repo)
        _git(repo, "commit", "-q", "--no-edit")

        parents = _git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]
        assert parents == [release_head, main_head]
        main_evidence = {p for p in _tracked(repo, "main") if p.startswith(".pr/")}
        expected = {("D", path) for path in main_evidence}
        assert _changes(repo, "main", "HEAD") == expected | {("M", "pyproject.toml")}
        assert "src/feature.py" in _tracked(repo)
        assert not (repo / ".pr").exists()
        guard = _run_step(repo, "precommit.yml", GUARD)
        assert guard.returncode == 0, guard.stdout


def test_release_guard_rejects_evidence_merged_without_cleanup() -> None:
    with TemporaryDirectory() as directory:
        repo = _main_repo(directory)
        _prepare_release(repo)
        _advance_main(repo, {".pr/later/run.log": "new evidence\n"})
        _git(repo, "merge", "-q", "--no-edit", "main")

        guard = _run_step(repo, "precommit.yml", GUARD)

        assert guard.returncode == 1
        assert ".pr/later/run.log" in guard.stdout
        assert "Refreshing a release branch" in guard.stdout
