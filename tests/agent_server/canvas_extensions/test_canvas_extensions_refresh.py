"""Tests for the staged two-step refresh flow: check() / apply()."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from openhands.agent_server.canvas_extensions.installed import (
    _staged_path,
    apply_canvas_extension_update,
    check_canvas_extension_update,
    enable_canvas_extension,
    get_installed_canvas_extension,
    install_canvas_extension,
    uninstall_canvas_extension,
)
from openhands.agent_server.canvas_extensions.manifest import MANIFEST_FILENAME
from openhands.sdk.extensions.fetch import ExtensionFetchError

from .conftest import write_extension as _write_extension


@pytest.fixture
def installed(extension_dir: Path, installed_dir: Path) -> Path:
    """A tracked, enabled install of "my-extension" -- the common baseline."""
    install_canvas_extension(str(extension_dir), installed_dir=installed_dir)
    enable_canvas_extension("my-extension", installed_dir=installed_dir)
    return installed_dir


def _active_version(installed_dir: Path, name: str = "my-extension") -> str:
    manifest = json.loads((installed_dir / name / MANIFEST_FILENAME).read_text())
    return manifest["version"]


# ============================================================================
# check_canvas_extension_update
# ============================================================================


def test_check_returns_none_when_not_installed(installed_dir: Path):
    result = check_canvas_extension_update("nonexistent", installed_dir=installed_dir)
    assert result is None


def test_check_invalid_name_raises(installed_dir: Path):
    with pytest.raises(ValueError, match="Invalid extension name"):
        check_canvas_extension_update("Bad_Name", installed_dir=installed_dir)


def test_check_reports_validated_and_new_resolved_ref(
    extension_dir: Path, installed: Path
):
    _write_extension(extension_dir, version="2.0.0")

    result = check_canvas_extension_update("my-extension", installed_dir=installed)

    assert result is not None
    assert result.validated is True
    # Local sources never resolve to a commit SHA.
    assert result.resolved_ref is None
    assert result.requested_ref is None


def test_check_does_not_touch_active_install(extension_dir: Path, installed: Path):
    _write_extension(extension_dir, version="2.0.0")

    check_canvas_extension_update("my-extension", installed_dir=installed)

    assert _active_version(installed) == "1.0.0"


def test_check_does_not_mutate_metadata_or_enabled_state(
    extension_dir: Path, installed: Path
):
    before = get_installed_canvas_extension("my-extension", installed_dir=installed)
    assert before is not None

    _write_extension(extension_dir, version="2.0.0")
    check_canvas_extension_update("my-extension", installed_dir=installed)

    after = get_installed_canvas_extension("my-extension", installed_dir=installed)
    assert after is not None
    assert after.enabled == before.enabled
    assert after.version == before.version
    assert after.installed_at == before.installed_at


def test_check_stages_new_content(extension_dir: Path, installed: Path):
    _write_extension(extension_dir, version="2.0.0")

    check_canvas_extension_update("my-extension", installed_dir=installed)

    staged = installed / ".staging" / "my-extension" / "local"
    assert staged.is_dir()
    staged_manifest = json.loads((staged / MANIFEST_FILENAME).read_text())
    assert staged_manifest["version"] == "2.0.0"


def test_check_uses_originally_requested_ref_not_latest(
    extension_dir: Path, installed_dir: Path
):
    """A pinned ref must survive a check, unlike update() which forces ref=None."""
    # Patch both: install() fetches via the SDK manager, check() via this module.
    with (
        patch(
            "openhands.sdk.extensions.installation.manager.fetch_with_resolution",
            return_value=(extension_dir, "sha-v1"),
        ),
        patch(
            "openhands.agent_server.canvas_extensions.installed.fetch_with_resolution",
            return_value=(extension_dir, "sha-v1-updated"),
        ) as mock_check_fetch,
    ):
        install_canvas_extension(
            "github:org/repo", ref="v1.0.0", installed_dir=installed_dir
        )
        enable_canvas_extension("my-extension", installed_dir=installed_dir)

        result = check_canvas_extension_update(
            "my-extension", installed_dir=installed_dir
        )

    assert mock_check_fetch.call_args.kwargs["ref"] == "v1.0.0"
    assert result is not None
    assert result.requested_ref == "v1.0.0"
    assert result.resolved_ref == "sha-v1-updated"


def test_check_invalid_manifest_marks_unvalidated_and_cleans_staging(
    extension_dir: Path, installed: Path
):
    (extension_dir / MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "my-extension",
                "display_name": "My Extension",
                "version": "2.0.0",
                # entrypoint dropped -- required field.
            }
        )
    )

    result = check_canvas_extension_update("my-extension", installed_dir=installed)

    assert result is not None
    assert result.validated is False
    assert not (installed / ".staging" / "my-extension").exists()
    # Active install is unaffected.
    assert _active_version(installed) == "1.0.0"


def test_check_escaping_entrypoint_marks_unvalidated_and_cleans_staging(
    tmp_path: Path, extension_dir: Path, installed: Path
):
    outside = tmp_path / "outside.js"
    outside.write_text("payload")
    (extension_dir / "escape.js").symlink_to(outside)
    manifest = json.loads((extension_dir / MANIFEST_FILENAME).read_text())
    manifest["entrypoint"] = "escape.js"
    manifest["version"] = "2.0.0"
    (extension_dir / MANIFEST_FILENAME).write_text(json.dumps(manifest))

    result = check_canvas_extension_update("my-extension", installed_dir=installed)

    assert result is not None
    assert result.validated is False
    assert not (installed / ".staging" / "my-extension").exists()


def test_check_manifest_name_mismatch_marks_unvalidated(
    extension_dir: Path, installed: Path
):
    """A manifest name change isn't a valid update for the tracked extension."""
    _write_extension(extension_dir, name="renamed-extension", version="2.0.0")

    result = check_canvas_extension_update("my-extension", installed_dir=installed)

    assert result is not None
    assert result.validated is False
    assert not (installed / ".staging" / "my-extension").exists()


def test_check_clears_previous_unapplied_staged_candidate(
    extension_dir: Path, installed: Path
):
    _write_extension(extension_dir, version="2.0.0")
    check_canvas_extension_update("my-extension", installed_dir=installed)

    _write_extension(extension_dir, version="3.0.0")
    check_canvas_extension_update("my-extension", installed_dir=installed)

    slot = installed / ".staging" / "my-extension"
    candidates = list(slot.iterdir())
    assert len(candidates) == 1
    staged_manifest = json.loads((candidates[0] / MANIFEST_FILENAME).read_text())
    assert staged_manifest["version"] == "3.0.0"


def test_check_is_repeatable_when_nothing_changed(extension_dir: Path, installed: Path):
    first = check_canvas_extension_update("my-extension", installed_dir=installed)
    second = check_canvas_extension_update("my-extension", installed_dir=installed)

    assert first is not None
    assert second is not None
    assert first.validated is True
    assert second.validated is True


def test_check_propagates_fetch_error(extension_dir: Path, installed: Path):
    """A fetch failure (network/auth) is an infra problem, not "this
    content is invalid" -- it must raise, not come back as validated=False.
    """
    with patch(
        "openhands.agent_server.canvas_extensions.installed.fetch_with_resolution",
        side_effect=ExtensionFetchError("network down"),
    ):
        with pytest.raises(ExtensionFetchError):
            check_canvas_extension_update("my-extension", installed_dir=installed)

    assert _active_version(installed) == "1.0.0"
    assert not (installed / ".staging" / "my-extension").exists()


def test_check_does_not_affect_other_extensions_staging(
    tmp_path: Path, installed_dir: Path
):
    other_src = _write_extension(tmp_path / "source" / "other", name="other-ext")
    my_src = _write_extension(tmp_path / "source" / "my-extension")
    install_canvas_extension(str(my_src), installed_dir=installed_dir)
    install_canvas_extension(str(other_src), installed_dir=installed_dir)
    enable_canvas_extension("my-extension", installed_dir=installed_dir)
    enable_canvas_extension("other-ext", installed_dir=installed_dir)

    check_canvas_extension_update("other-ext", installed_dir=installed_dir)

    assert (installed_dir / ".staging" / "other-ext").exists()
    assert not (installed_dir / ".staging" / "my-extension").exists()


# ============================================================================
# apply_canvas_extension_update
# ============================================================================


def test_apply_returns_none_when_not_installed(installed_dir: Path):
    result = apply_canvas_extension_update(
        "nonexistent", resolved_ref=None, enabled=True, installed_dir=installed_dir
    )
    assert result is None


def test_apply_invalid_name_raises(installed_dir: Path):
    with pytest.raises(ValueError, match="Invalid extension name"):
        apply_canvas_extension_update(
            "Bad_Name", resolved_ref=None, enabled=True, installed_dir=installed_dir
        )


def test_apply_without_prior_check_raises(installed: Path):
    with pytest.raises(ValueError, match="No validated staged update"):
        apply_canvas_extension_update(
            "my-extension", resolved_ref=None, enabled=True, installed_dir=installed
        )

    # Nothing about the active install changed.
    assert _active_version(installed) == "1.0.0"


def test_apply_with_mismatched_resolved_ref_raises(
    extension_dir: Path, installed: Path
):
    _write_extension(extension_dir, version="2.0.0")
    check_canvas_extension_update("my-extension", installed_dir=installed)

    with pytest.raises(ValueError, match="No validated staged update"):
        apply_canvas_extension_update(
            "my-extension",
            resolved_ref="some-other-sha",
            enabled=True,
            installed_dir=installed,
        )

    assert _active_version(installed) == "1.0.0"


def test_apply_rejects_resolved_ref_with_parent_traversal(
    extension_dir: Path, installed: Path
):
    _write_extension(extension_dir, version="2.0.0")
    check_canvas_extension_update("my-extension", installed_dir=installed)

    with pytest.raises(ValueError, match="Invalid resolved_ref"):
        apply_canvas_extension_update(
            "my-extension",
            resolved_ref="../../../etc",
            enabled=True,
            installed_dir=installed,
        )

    assert _active_version(installed) == "1.0.0"


def test_apply_rejects_absolute_resolved_ref(
    extension_dir: Path, installed: Path, tmp_path: Path
):
    """A resolved_ref shaped like an absolute path must not let Path's
    "/" operator discard the staging root and point anywhere on disk."""
    outside = _write_extension(
        tmp_path / "evil", name="my-extension", version="666.0.0"
    )

    with pytest.raises(ValueError, match="Invalid resolved_ref"):
        apply_canvas_extension_update(
            "my-extension",
            resolved_ref=str(outside),
            enabled=True,
            installed_dir=installed,
        )

    assert _active_version(installed) == "1.0.0"


def test_staged_path_rejects_traversal_and_absolute_refs(installed_dir: Path):
    with pytest.raises(ValueError, match="Invalid resolved_ref"):
        _staged_path(installed_dir, "my-extension", "../escape")
    with pytest.raises(ValueError, match="Invalid resolved_ref"):
        _staged_path(installed_dir, "my-extension", "/etc/passwd")


def test_staged_path_rejects_empty_string_ref(installed_dir: Path):
    """ "" must not silently alias the None/"local" slot -- a caller that
    serializes None as "" would otherwise get a false-positive match."""
    with pytest.raises(ValueError, match="Invalid resolved_ref"):
        _staged_path(installed_dir, "my-extension", "")


def test_staged_path_allows_ref_with_internal_slash(installed_dir: Path):
    """A branch name like "feature/foo" is a legitimate resolved_ref
    fallback value and must not be rejected, only ".." and a leading "/"."""
    path = _staged_path(installed_dir, "my-extension", "feature/foo")
    assert path == installed_dir / ".staging" / "my-extension" / "feature" / "foo"


def test_apply_rejects_unvalidated_check_result(extension_dir: Path, installed: Path):
    (extension_dir / MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "my-extension",
                "display_name": "My Extension",
                "version": "2.0.0",
            }
        )
    )
    result = check_canvas_extension_update("my-extension", installed_dir=installed)
    assert result is not None
    assert result.validated is False

    with pytest.raises(ValueError, match="No validated staged update"):
        apply_canvas_extension_update(
            "my-extension",
            resolved_ref=result.resolved_ref,
            enabled=True,
            installed_dir=installed,
        )


def test_apply_swaps_active_content(extension_dir: Path, installed: Path):
    _write_extension(extension_dir, version="2.0.0")
    check = check_canvas_extension_update("my-extension", installed_dir=installed)
    assert check is not None

    result = apply_canvas_extension_update(
        "my-extension",
        resolved_ref=check.resolved_ref,
        enabled=True,
        installed_dir=installed,
    )

    assert result is not None
    assert result.version == "2.0.0"
    assert _active_version(installed) == "2.0.0"


def test_apply_sets_enabled_explicitly_not_inherited(
    extension_dir: Path, installed: Path
):
    """enabled=False must not be overridden by the prior True state."""
    before = get_installed_canvas_extension("my-extension", installed_dir=installed)
    assert before is not None
    assert before.enabled is True

    _write_extension(extension_dir, version="2.0.0")
    check = check_canvas_extension_update("my-extension", installed_dir=installed)
    assert check is not None

    result = apply_canvas_extension_update(
        "my-extension",
        resolved_ref=check.resolved_ref,
        enabled=False,
        installed_dir=installed,
    )

    assert result is not None
    assert result.enabled is False
    on_disk = get_installed_canvas_extension("my-extension", installed_dir=installed)
    assert on_disk is not None
    assert on_disk.enabled is False


def test_apply_can_enable_a_previously_disabled_extension(
    extension_dir: Path, installed_dir: Path
):
    # Fresh install lands disabled.
    install_canvas_extension(str(extension_dir), installed_dir=installed_dir)

    _write_extension(extension_dir, version="2.0.0")
    check = check_canvas_extension_update("my-extension", installed_dir=installed_dir)
    assert check is not None

    result = apply_canvas_extension_update(
        "my-extension",
        resolved_ref=check.resolved_ref,
        enabled=True,
        installed_dir=installed_dir,
    )

    assert result is not None
    assert result.enabled is True


def test_apply_preserves_source_and_repo_path(extension_dir: Path, installed_dir: Path):
    with (
        patch(
            "openhands.sdk.extensions.installation.manager.fetch_with_resolution",
            return_value=(extension_dir, "sha-1"),
        ),
        patch(
            "openhands.agent_server.canvas_extensions.installed.fetch_with_resolution",
            return_value=(extension_dir, "sha-1"),
        ) as mock_check_fetch,
    ):
        install_canvas_extension(
            "github:org/repo",
            repo_path="packages/my-extension",
            installed_dir=installed_dir,
        )
        enable_canvas_extension("my-extension", installed_dir=installed_dir)

        check = check_canvas_extension_update(
            "my-extension", installed_dir=installed_dir
        )
        assert check is not None
        applied = apply_canvas_extension_update(
            "my-extension",
            resolved_ref=check.resolved_ref,
            enabled=True,
            installed_dir=installed_dir,
        )

    assert applied is not None
    assert applied.source == "github:org/repo"
    assert applied.repo_path == "packages/my-extension"
    assert mock_check_fetch.call_args.kwargs["repo_path"] == "packages/my-extension"


def test_apply_persists_new_resolved_ref(extension_dir: Path, installed_dir: Path):
    with (
        patch(
            "openhands.sdk.extensions.installation.manager.fetch_with_resolution",
            return_value=(extension_dir, "sha-1"),
        ),
        patch(
            "openhands.agent_server.canvas_extensions.installed.fetch_with_resolution",
            return_value=(extension_dir, "sha-2"),
        ),
    ):
        install_canvas_extension(
            "github:org/repo", ref="main", installed_dir=installed_dir
        )
        enable_canvas_extension("my-extension", installed_dir=installed_dir)

        check = check_canvas_extension_update(
            "my-extension", installed_dir=installed_dir
        )
        assert check is not None

        applied = apply_canvas_extension_update(
            "my-extension",
            resolved_ref=check.resolved_ref,
            enabled=True,
            installed_dir=installed_dir,
        )

    assert applied is not None
    assert applied.resolved_ref == "sha-2"
    assert applied.requested_ref == "main"
    assert applied.source == "github:org/repo"

    on_disk = get_installed_canvas_extension(
        "my-extension", installed_dir=installed_dir
    )
    assert on_disk is not None
    assert on_disk.resolved_ref == "sha-2"
    assert on_disk.requested_ref == "main"


def test_apply_cleans_up_staging_after_success(extension_dir: Path, installed: Path):
    _write_extension(extension_dir, version="2.0.0")
    check = check_canvas_extension_update("my-extension", installed_dir=installed)
    assert check is not None

    apply_canvas_extension_update(
        "my-extension",
        resolved_ref=check.resolved_ref,
        enabled=True,
        installed_dir=installed,
    )

    assert not (installed / ".staging" / "my-extension").exists()
    assert not (installed / ".staging" / "my-extension.previous").exists()


def test_double_apply_without_recheck_raises(extension_dir: Path, installed: Path):
    _write_extension(extension_dir, version="2.0.0")
    check = check_canvas_extension_update("my-extension", installed_dir=installed)
    assert check is not None

    apply_canvas_extension_update(
        "my-extension",
        resolved_ref=check.resolved_ref,
        enabled=True,
        installed_dir=installed,
    )

    with pytest.raises(ValueError, match="No validated staged update"):
        apply_canvas_extension_update(
            "my-extension",
            resolved_ref=check.resolved_ref,
            enabled=True,
            installed_dir=installed,
        )


def test_apply_does_not_affect_other_installed_extensions(
    tmp_path: Path, installed_dir: Path
):
    other_src = _write_extension(tmp_path / "source" / "other", name="other-ext")
    my_src = _write_extension(tmp_path / "source" / "my-extension")
    install_canvas_extension(str(my_src), installed_dir=installed_dir)
    install_canvas_extension(str(other_src), installed_dir=installed_dir)
    enable_canvas_extension("my-extension", installed_dir=installed_dir)
    enable_canvas_extension("other-ext", installed_dir=installed_dir)

    _write_extension(my_src, version="2.0.0")
    check = check_canvas_extension_update("my-extension", installed_dir=installed_dir)
    assert check is not None
    apply_canvas_extension_update(
        "my-extension",
        resolved_ref=check.resolved_ref,
        enabled=True,
        installed_dir=installed_dir,
    )

    other_info = get_installed_canvas_extension(
        "other-ext", installed_dir=installed_dir
    )
    assert other_info is not None
    assert other_info.version == "1.0.0"
    assert (installed_dir / "other-ext").exists()


def test_apply_rolls_back_active_install_on_failed_swap(
    extension_dir: Path, installed: Path
):
    _write_extension(extension_dir, version="2.0.0")
    check = check_canvas_extension_update("my-extension", installed_dir=installed)
    assert check is not None

    real_replace = os.replace
    call_count = {"n": 0}

    def flaky_replace(src, dst):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated mid-swap failure")
        return real_replace(src, dst)

    with patch("os.replace", side_effect=flaky_replace):
        with pytest.raises(OSError, match="simulated mid-swap failure"):
            apply_canvas_extension_update(
                "my-extension",
                resolved_ref=check.resolved_ref,
                enabled=True,
                installed_dir=installed,
            )

    # Rolled back: the active install still serves the old content.
    assert _active_version(installed) == "1.0.0"
    assert (installed / "my-extension").is_dir()

    # Metadata untouched by the failed apply.
    info = get_installed_canvas_extension("my-extension", installed_dir=installed)
    assert info is not None
    assert info.version == "1.0.0"
    assert info.enabled is True

    # The validated staged candidate survives the failure -- retryable.
    staged = installed / ".staging" / "my-extension" / "local"
    assert staged.is_dir()


def test_apply_retry_succeeds_after_rolled_back_failure(
    extension_dir: Path, installed: Path
):
    _write_extension(extension_dir, version="2.0.0")
    check = check_canvas_extension_update("my-extension", installed_dir=installed)
    assert check is not None

    real_replace = os.replace
    call_count = {"n": 0}

    def flaky_replace(src, dst):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated mid-swap failure")
        return real_replace(src, dst)

    with patch("os.replace", side_effect=flaky_replace):
        with pytest.raises(OSError):
            apply_canvas_extension_update(
                "my-extension",
                resolved_ref=check.resolved_ref,
                enabled=True,
                installed_dir=installed,
            )

    result = apply_canvas_extension_update(
        "my-extension",
        resolved_ref=check.resolved_ref,
        enabled=True,
        installed_dir=installed,
    )

    assert result is not None
    assert result.version == "2.0.0"
    assert _active_version(installed) == "2.0.0"


def test_apply_leaves_no_backup_when_first_rename_fails(
    extension_dir: Path, installed: Path
):
    """If the first rename fails, nothing moved -- no rollback needed."""
    _write_extension(extension_dir, version="2.0.0")
    check = check_canvas_extension_update("my-extension", installed_dir=installed)
    assert check is not None

    with patch("os.replace", side_effect=OSError("cannot move active install")):
        with pytest.raises(OSError, match="cannot move active install"):
            apply_canvas_extension_update(
                "my-extension",
                resolved_ref=check.resolved_ref,
                enabled=True,
                installed_dir=installed,
            )

    assert _active_version(installed) == "1.0.0"
    staged = installed / ".staging" / "my-extension" / "local"
    assert staged.is_dir()


def test_apply_rejects_manifest_that_became_invalid_since_check(
    extension_dir: Path, installed: Path
):
    """apply() re-validates staged content right before swapping."""
    _write_extension(extension_dir, version="2.0.0")
    check = check_canvas_extension_update("my-extension", installed_dir=installed)
    assert check is not None

    staged = installed / ".staging" / "my-extension" / "local"
    (staged / MANIFEST_FILENAME).write_text("{not valid json")

    with pytest.raises(ValidationError):
        apply_canvas_extension_update(
            "my-extension",
            resolved_ref=check.resolved_ref,
            enabled=True,
            installed_dir=installed,
        )

    assert _active_version(installed) == "1.0.0"


def test_uninstall_drops_orphaned_staged_update(extension_dir: Path, installed: Path):
    _write_extension(extension_dir, version="2.0.0")
    check_canvas_extension_update("my-extension", installed_dir=installed)
    assert (installed / ".staging" / "my-extension").exists()

    uninstall_canvas_extension("my-extension", installed_dir=installed)

    assert not (installed / ".staging" / "my-extension").exists()
