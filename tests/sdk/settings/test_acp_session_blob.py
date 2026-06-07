"""Tests for the allowlisted ACP session-blob export/import helpers."""

import io
import tarfile
from pathlib import Path

import pytest

from openhands.sdk.settings.acp_providers import ACP_PROVIDERS, get_acp_provider
from openhands.sdk.settings.acp_session_blob import (
    export_acp_session_blob,
    import_acp_session_blob,
)


CODEX = ACP_PROVIDERS["codex"]
CLAUDE = ACP_PROVIDERS["claude-code"]
GEMINI = ACP_PROVIDERS["gemini-cli"]


def _make_codex_root(root: Path) -> Path:
    """A realistic codex data root: sessions + credentials side by side."""
    sessions = root / "sessions" / "2026" / "06" / "07"
    sessions.mkdir(parents=True)
    (sessions / "rollout-abc.jsonl").write_text('{"turn": 1}\n')
    (root / "auth.json").write_text('{"refresh_token": "SECRET"}')
    (root / "history.jsonl").write_text("global history\n")
    (root / "config.toml").write_text("model = 'x'\n")
    return root


def _tar_names(blob: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        return tar.getnames()


def _make_blob(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_never_includes_credentials_or_global_state(tmp_path):
    """The credential-leak gate: only the sessions subtree rides the blob."""
    root = _make_codex_root(tmp_path)
    blob = export_acp_session_blob(root, CODEX)
    assert blob is not None
    names = _tar_names(blob)
    assert names == ["sessions/2026/06/07/rollout-abc.jsonl"]
    joined = "\n".join(names)
    assert "auth.json" not in joined
    assert "history.jsonl" not in joined
    assert "config.toml" not in joined


def test_export_claude_projects_subtree(tmp_path):
    projects = tmp_path / "projects" / "-workspace-project"
    projects.mkdir(parents=True)
    (projects / "11111111-2222-3333-4444-555555555555.jsonl").write_text("{}\n")
    (tmp_path / ".credentials.json").write_text('{"token": "SECRET"}')
    blob = export_acp_session_blob(tmp_path, CLAUDE)
    assert blob is not None
    assert _tar_names(blob) == [
        "projects/-workspace-project/11111111-2222-3333-4444-555555555555.jsonl"
    ]


def test_export_returns_none_without_session_files(tmp_path):
    (tmp_path / "auth.json").write_text("{}")
    assert export_acp_session_blob(tmp_path, CODEX) is None


def test_export_returns_none_for_snapshotless_provider(tmp_path):
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "x.jsonl").write_text("{}")
    assert GEMINI.session_subtrees == ()
    assert export_acp_session_blob(tmp_path, GEMINI) is None


def test_export_skips_symlinks(tmp_path):
    root = _make_codex_root(tmp_path)
    (root / "sessions" / "leak.jsonl").symlink_to(root / "auth.json")
    link_dir = root / "sessions" / "leak-dir"
    link_dir.symlink_to(root, target_is_directory=True)
    blob = export_acp_session_blob(root, CODEX)
    assert blob is not None
    assert _tar_names(blob) == ["sessions/2026/06/07/rollout-abc.jsonl"]


def test_export_rejects_symlinked_subtree_root(tmp_path):
    """Credential-leak gate: a symlinked subtree root must not smuggle the data
    root's credentials into the blob (agent does `ln -sf $CODEX_HOME sessions`).
    """
    root = tmp_path
    (root / "auth.json").write_text('{"refresh_token": "SECRET"}')
    (root / ".credentials.json").write_text('{"token": "SECRET"}')
    (root / "history.jsonl").write_text("global history\n")
    # The whole data root masquerading as the sessions subtree.
    (root / "sessions").symlink_to(root, target_is_directory=True)
    assert export_acp_session_blob(root, CODEX) is None


def test_export_realpath_guard_excludes_redirected_files(tmp_path):
    """A real sessions dir whose only entry is a symlink to auth.json yields
    nothing (per-file symlink + realpath guards both reject it)."""
    root = tmp_path
    (root / "auth.json").write_text('{"refresh_token": "SECRET"}')
    sessions = root / "sessions"
    sessions.mkdir()
    (sessions / "leak.jsonl").symlink_to(root / "auth.json")
    assert export_acp_session_blob(root, CODEX) is None


def test_import_rejects_symlinked_subtree_escape(tmp_path):
    """A pre-positioned symlinked subtree dir must not redirect writes outside
    the data root."""
    escape = tmp_path / "escape"
    escape.mkdir()
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "sessions").symlink_to(escape, target_is_directory=True)
    blob = _make_blob({"sessions/x.jsonl": b"payload"})
    with pytest.raises(ValueError, match="escapes data root"):
        import_acp_session_blob(data_root, CODEX, blob)
    assert not (escape / "x.jsonl").exists()


def test_import_refuses_to_write_through_symlinked_target(tmp_path):
    """A target that is itself an existing symlink is refused (no clobber-via-link)."""
    outside = tmp_path / "outside.jsonl"
    outside.write_text("original")
    data_root = tmp_path / "data"
    (data_root / "sessions").mkdir(parents=True)
    (data_root / "sessions" / "x.jsonl").symlink_to(outside)
    blob = _make_blob({"sessions/x.jsonl": b"payload"})
    with pytest.raises(ValueError, match="symlink"):
        import_acp_session_blob(data_root, CODEX, blob)
    assert outside.read_text() == "original"


def test_export_import_round_trip(tmp_path):
    source = _make_codex_root(tmp_path / "source")
    blob = export_acp_session_blob(source, CODEX)
    assert blob is not None
    target = tmp_path / "target"
    written = import_acp_session_blob(target, CODEX, blob)
    assert written == 1
    restored = target / "sessions" / "2026" / "06" / "07" / "rollout-abc.jsonl"
    assert restored.read_text() == '{"turn": 1}\n'
    assert not (target / "auth.json").exists()


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def test_import_is_seed_if_absent(tmp_path):
    live = tmp_path / "sessions" / "2026" / "06" / "07" / "rollout-abc.jsonl"
    live.parent.mkdir(parents=True)
    live.write_text("LIVE COPY")
    blob = _make_blob({"sessions/2026/06/07/rollout-abc.jsonl": b"STALE SNAPSHOT"})
    written = import_acp_session_blob(tmp_path, CODEX, blob)
    assert written == 0
    assert live.read_text() == "LIVE COPY"


def test_import_skips_members_outside_allowlist(tmp_path):
    blob = _make_blob(
        {
            "sessions/a.jsonl": b"{}",
            "auth.json": b'{"refresh_token": "EVIL"}',
            "history.jsonl": b"x",
        }
    )
    written = import_acp_session_blob(tmp_path, CODEX, blob)
    assert written == 1
    assert (tmp_path / "sessions" / "a.jsonl").exists()
    assert not (tmp_path / "auth.json").exists()
    assert not (tmp_path / "history.jsonl").exists()


@pytest.mark.parametrize(
    "name",
    [
        "sessions/../auth.json",
        "/etc/passwd",
        "sessions/../../escape.txt",
    ],
)
def test_import_rejects_traversal(tmp_path, name):
    blob = _make_blob({name: b"x"})
    with pytest.raises(ValueError, match="unsafe path"):
        import_acp_session_blob(tmp_path, CODEX, blob)
    assert not (tmp_path / "auth.json").exists()


def test_import_rejects_links(tmp_path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="sessions/evil-link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../auth.json"
        tar.addfile(info)
    with pytest.raises(ValueError, match="unsupported member type"):
        import_acp_session_blob(tmp_path, CODEX, buffer.getvalue())


def test_import_noop_for_snapshotless_provider(tmp_path):
    blob = _make_blob({"sessions/a.jsonl": b"{}"})
    assert import_acp_session_blob(tmp_path, GEMINI, blob) == 0
    assert not (tmp_path / "sessions").exists()


def test_import_writes_restrictive_permissions(tmp_path):
    blob = _make_blob({"sessions/a.jsonl": b"{}"})
    import_acp_session_blob(tmp_path, CODEX, blob)
    target = tmp_path / "sessions" / "a.jsonl"
    assert target.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "sessions").stat().st_mode & 0o777 == 0o700


def test_registry_allowlists():
    """Pin the allowlists — widening one must be a deliberate, reviewed act."""
    assert get_acp_provider("codex").session_subtrees == ("sessions",)
    assert get_acp_provider("claude-code").session_subtrees == ("projects",)
    assert get_acp_provider("gemini-cli").session_subtrees == ()
