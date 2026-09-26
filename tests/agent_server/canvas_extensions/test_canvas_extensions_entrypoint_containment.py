"""Adversarial tests for entrypoint containment (security-critical).

Kept separate from generic manifest validation: these exercise the
filesystem-level check in ``resolve_entrypoint`` (path traversal and
symlink escapes), not the syntactic ``entrypoint`` field validator.
"""

from pathlib import Path

import pytest

from openhands.agent_server.canvas_extensions.manifest import (
    CanvasExtensionManifest,
    resolve_entrypoint,
)


def _manifest_with_raw_entrypoint(entrypoint: str) -> CanvasExtensionManifest:
    """Build a manifest bypassing field validation.

    Lets these tests drive ``resolve_entrypoint`` directly with entrypoints
    the syntactic validator would already reject at construction time.
    """
    manifest = CanvasExtensionManifest(
        schema_version=1,
        name="my-extension",
        display_name="My Extension",
        version="1.0.0",
        entrypoint="index.js",
    )
    return manifest.model_copy(update={"entrypoint": entrypoint})


@pytest.fixture
def package_root(tmp_path: Path) -> Path:
    root = tmp_path / "installed" / "my-extension"
    root.mkdir(parents=True)
    (root / "index.js").write_text("console.log('ok')")
    return root


def test_resolves_valid_entrypoint(package_root: Path):
    manifest = _manifest_with_raw_entrypoint("index.js")
    resolved = resolve_entrypoint(manifest, package_root)
    assert resolved == (package_root / "index.js").resolve()


def test_resolves_valid_nested_entrypoint(package_root: Path):
    (package_root / "dist").mkdir()
    (package_root / "dist" / "bundle.js").write_text("console.log('ok')")
    manifest = _manifest_with_raw_entrypoint("dist/bundle.js")
    resolved = resolve_entrypoint(manifest, package_root)
    assert resolved == (package_root / "dist" / "bundle.js").resolve()


def test_resolves_entrypoint_via_in_package_symlink(package_root: Path):
    """A symlink that stays inside ``package_root`` is legitimate (e.g. build
    tooling) and must still resolve — only escapes are rejected.
    """
    (package_root / "dist").mkdir()
    real_bundle = package_root / "dist" / "bundle.js"
    real_bundle.write_text("console.log('ok')")
    (package_root / "index.js").unlink()
    (package_root / "index.js").symlink_to(real_bundle)

    manifest = _manifest_with_raw_entrypoint("index.js")
    resolved = resolve_entrypoint(manifest, package_root)
    assert resolved == real_bundle.resolve()


@pytest.mark.parametrize(
    "entrypoint",
    [
        "../../../../etc/passwd",
        "../sibling-package/index.js",
        "dist/../../escape.js",
        "a/b/c/../../../../../../etc/passwd",
    ],
)
def test_rejects_textual_traversal_escape(package_root: Path, entrypoint: str):
    manifest = _manifest_with_raw_entrypoint(entrypoint)
    with pytest.raises(ValueError, match="resolves outside"):
        resolve_entrypoint(manifest, package_root)


def test_rejects_absolute_entrypoint_bypassing_field_validation(
    package_root: Path, tmp_path: Path
):
    """``root / "/abs/path"`` silently discards ``root`` (a well-known
    pathlib footgun: joining with an absolute path drops everything to its
    left, same as ``os.path.join``). Confirms containment still catches
    this rather than trusting the join, for an entrypoint value that
    reaches ``resolve_entrypoint`` without going through field validation
    (which normally rejects absolute paths first).
    """
    outside_secret = tmp_path / "outside-secret.js"
    outside_secret.write_text("should never be served")
    assert (package_root / str(outside_secret)) == outside_secret  # the footgun

    manifest = _manifest_with_raw_entrypoint(str(outside_secret))
    with pytest.raises(ValueError, match="resolves outside"):
        resolve_entrypoint(manifest, package_root)


def test_rejects_symlinked_file_escaping_root(package_root: Path, tmp_path: Path):
    outside_secret = tmp_path / "outside-secret.js"
    outside_secret.write_text("should never be served")

    escape_link = package_root / "index.js"
    escape_link.unlink()
    escape_link.symlink_to(outside_secret)

    manifest = _manifest_with_raw_entrypoint("index.js")
    with pytest.raises(ValueError, match="resolves outside"):
        resolve_entrypoint(manifest, package_root)


def test_rejects_symlinked_directory_escaping_root(package_root: Path, tmp_path: Path):
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "payload.js").write_text("should never be served")

    (package_root / "linked").symlink_to(outside_dir, target_is_directory=True)

    manifest = _manifest_with_raw_entrypoint("linked/payload.js")
    with pytest.raises(ValueError, match="resolves outside"):
        resolve_entrypoint(manifest, package_root)


def test_rejects_symlinked_package_root_itself(package_root: Path, tmp_path: Path):
    """A symlinked ``package_root`` should still confine resolution to its target."""
    outside_secret = tmp_path / "outside-secret.js"
    outside_secret.write_text("should never be served")

    real_root = tmp_path / "real-root"
    real_root.mkdir()
    (real_root / "index.js").write_text("console.log('ok')")
    (real_root / "escape").symlink_to(outside_secret)

    aliased_root = tmp_path / "aliased-root"
    aliased_root.symlink_to(real_root, target_is_directory=True)

    manifest = _manifest_with_raw_entrypoint("escape")
    with pytest.raises(ValueError, match="resolves outside"):
        resolve_entrypoint(manifest, aliased_root)


def test_rejects_entrypoint_equal_to_package_root(package_root: Path):
    """``.`` is contained (no escape) but is a directory, not an entrypoint."""
    manifest = _manifest_with_raw_entrypoint(".")
    with pytest.raises(ValueError, match="does not resolve to a file"):
        resolve_entrypoint(manifest, package_root)


def test_rejects_entrypoint_pointing_at_a_directory(package_root: Path):
    (package_root / "dist").mkdir()
    manifest = _manifest_with_raw_entrypoint("dist")
    with pytest.raises(ValueError, match="does not resolve to a file"):
        resolve_entrypoint(manifest, package_root)


def test_rejects_missing_entrypoint(package_root: Path):
    manifest = _manifest_with_raw_entrypoint("does-not-exist.js")
    with pytest.raises(ValueError, match="does not resolve to a file"):
        resolve_entrypoint(manifest, package_root)


def test_rejects_symlink_cycle(package_root: Path):
    """A self-referential symlink must resolve() cleanly, not hang or crash,
    and must still be rejected since it never resolves to a real file.
    """
    loop = package_root / "index.js"
    loop.unlink()
    loop.symlink_to(loop)

    manifest = _manifest_with_raw_entrypoint("index.js")
    with pytest.raises(ValueError, match="does not resolve to a file"):
        resolve_entrypoint(manifest, package_root)
