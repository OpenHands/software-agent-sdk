"""Concurrency regression tests for installation metadata"""

import json
import os
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel

from openhands.sdk.extensions.installation import (
    InstallationInfo,
    InstallationInterface,
    InstallationManager,
    InstallationMetadata,
    MetadataSession,
    manager as manager_module,
    metadata as metadata_module,
)


# Disable/enable pairs the writer runs while the reader reads.
_SAVES = 100


class MockExtension(BaseModel):
    name: str
    version: str = "0.0.1"
    description: str = "Mock extension"


class MockExtensionInstallationInterface(InstallationInterface):
    @staticmethod
    def load_from_dir(extension_dir: Path) -> MockExtension:
        return MockExtension.model_validate_json(
            (extension_dir / "extension.json").read_text()
        )


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "src" / "magic-test"
    directory.mkdir(parents=True)
    (directory / "extension.json").write_text(
        MockExtension(name="magic-test").model_dump_json()
    )
    return directory


@pytest.fixture
def installation_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "installed"
    directory.mkdir()
    return directory


@pytest.fixture
def manager(installation_dir: Path) -> InstallationManager[MockExtension]:
    return InstallationManager(
        installation_dir=installation_dir,
        installation_interface=MockExtensionInstallationInterface(),
    )


def _run_threads(*targets: tuple[str, Callable[[], object]]) -> list[BaseException]:
    """Run each target in a named daemon thread; return exceptions they raised."""
    errors: list[BaseException] = []

    def capture(fn: Callable[[], object]) -> Callable[[], None]:
        def run() -> None:
            try:
                fn()
            except BaseException as e:
                errors.append(e)

        return run

    threads = [
        threading.Thread(target=capture(fn), name=name, daemon=True)
        for name, fn in targets
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive(), f"thread {thread.name!r} did not finish"
    return errors


def _source_of(installation_dir: Path, name: str) -> str:
    data = json.loads(
        InstallationMetadata.get_metadata_path(installation_dir).read_text()
    )
    return data["extensions"][name]["source"]


@pytest.mark.parametrize("operation", ["install", "disable"])
def test_list_cannot_load_metadata_while_another_session_is_open(
    manager: InstallationManager[MockExtension],
    installation_dir: Path,
    source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
):
    """Lost update: a list call that loads metadata before another session
    saves later overwrites that session's change with a stale copy (for a fresh
    install, plus a ``source="local"`` rediscovery). The session lock must keep
    the list call from loading until the other session has saved.
    """
    if operation == "disable":
        manager.install(str(source_dir))

    original_load = InstallationMetadata._load_for_session.__func__
    writer_loading = threading.Event()
    list_loaded = threading.Event()

    def load(cls: type[InstallationMetadata], directory: Path):
        me = threading.current_thread().name
        if me == "writer":
            writer_loading.set()
            assert not list_loaded.wait(0.5), (
                "list_installed loaded metadata while another session was open"
            )
        result = original_load(cls, directory)
        if me == "list":
            list_loaded.set()
        return result

    monkeypatch.setattr(InstallationMetadata, "_load_for_session", classmethod(load))

    def write() -> None:
        if operation == "install":
            manager.install(str(source_dir))
        else:
            manager.disable("magic-test")

    def list_once_writer_is_loading() -> None:
        assert writer_loading.wait(5)
        manager.list_installed()

    errors = _run_threads(("writer", write), ("list", list_once_writer_is_loading))

    assert errors == []
    data = json.loads(
        InstallationMetadata.get_metadata_path(installation_dir).read_text()
    )
    entry = data["extensions"]["magic-test"]
    assert entry["source"] == str(source_dir)
    assert entry["enabled"] is (operation == "install")


def test_reader_thread_finds_the_extension_during_saves(
    manager: InstallationManager[MockExtension],
    source_dir: Path,
):
    """``get()`` takes no lock, so while another thread saves it must still
    find the extension: never a truncated file (which loads as empty). On
    Windows this also covers both sides of the replace: a save that meets the
    reader's open handle, and a read that meets the save's replace, must each
    retry instead of failing.
    """
    manager.install(str(source_dir))
    saving = threading.Event()
    done = threading.Event()
    reads: list[InstallationInfo | None] = []

    def save_repeatedly() -> None:
        try:
            for _ in range(_SAVES):
                manager.disable("magic-test")
                saving.set()
                manager.enable("magic-test")
        finally:
            done.set()

    def read_until_done() -> None:
        assert saving.wait(5)
        while not done.is_set():
            reads.append(manager.get("magic-test"))

    errors = _run_threads(("writer", save_repeatedly), ("reader", read_until_done))

    missed = [info for info in reads if info is None or info.source != str(source_dir)]
    thread_errors = [f"{type(e).__name__}: {e}" for e in errors]
    assert (thread_errors, len(missed)) == ([], 0), (
        f"thread errors: {thread_errors}; "
        f"{len(missed)} of {len(reads)} reads did not find the extension"
    )
    assert reads, "the reader never ran while the writer was saving"


def test_get_does_not_wait_for_an_open_session(
    manager: InstallationManager[MockExtension],
    installation_dir: Path,
    source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """``get()`` is read-only and takes no lock, so a session held open by
    another caller must not block it or make it time out.
    """
    manager.install(str(source_dir))
    monkeypatch.setattr(metadata_module, "_LOCK_TIMEOUT_SECONDS", 0.2)
    results: list[InstallationInfo | None] = []

    with InstallationMetadata.open(installation_dir):
        errors = _run_threads(
            ("get", lambda: results.append(manager.get("magic-test")))
        )

    assert errors == []
    assert len(results) == 1
    assert results[0] is not None
    assert results[0].source == str(source_dir)


def test_session_lock_times_out_instead_of_blocking_forever(
    installation_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """A session that is never closed must not hang every other caller."""
    monkeypatch.setattr(metadata_module, "_LOCK_TIMEOUT_SECONDS", 0.2)

    with InstallationMetadata.open(installation_dir):
        errors = _run_threads(
            ("other", lambda: InstallationMetadata.open(installation_dir)),
        )

    assert len(errors) == 1
    assert isinstance(errors[0], TimeoutError)


def _raise_runtime_error(*args, **kwargs):
    raise RuntimeError("boom")


@pytest.mark.parametrize(
    "failure",
    ["error_inside_session", "save_fails", "load_fails"],
)
def test_lock_is_released_when_a_session_fails(
    installation_dir: Path, monkeypatch: pytest.MonkeyPatch, failure: str
):
    """A failing session must release the lock deterministically.

    The session and ``excinfo`` are kept referenced, as a caller that logs or
    re-raises the exception would keep them. filelock also releases on garbage
    collection, so without those references a missing release goes unnoticed.
    """
    sessions: list[MetadataSession] = []
    monkeypatch.setattr(metadata_module, "_LOCK_TIMEOUT_SECONDS", 0.2)

    with monkeypatch.context() as patch:
        if failure == "save_fails":
            patch.setattr(InstallationMetadata, "save_to_dir", _raise_runtime_error)
        elif failure == "load_fails":
            patch.setattr(
                InstallationMetadata,
                "_load_for_session",
                classmethod(_raise_runtime_error),
            )
        with pytest.raises(RuntimeError) as excinfo:
            with InstallationMetadata.open(installation_dir) as session:
                sessions.append(session)
                if failure == "error_inside_session":
                    raise RuntimeError("boom")

    errors = _run_threads(
        (
            "next",
            lambda: InstallationMetadata.open(installation_dir).__exit__(
                None, None, None
            ),
        ),
    )
    assert errors == []
    assert excinfo.value is not None
    assert len(sessions) == (0 if failure == "load_fails" else 1)


# ============================================================================
# Directory swap during install(force=True) and update()
# ============================================================================


def _staging_dirs(manager: InstallationManager[MockExtension]) -> list[Path]:
    return list(manager.installation_dir.glob(".magic-test.installing-*"))


def _install_disabled_with_new_version(
    manager: InstallationManager[MockExtension], source_dir: Path
) -> Path:
    """Install and disable magic-test, then add a file only the new version has."""
    manager.install(str(source_dir))
    manager.disable("magic-test")
    (source_dir / "new-version").write_text("2")
    return manager.installation_dir / "magic-test"


def test_list_while_update_copies_keeps_the_entry(
    manager: InstallationManager[MockExtension],
    source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """#5985: a list call while update() copies the new version must not see
    the extension missing (and prune its entry) or half copied (and rediscover
    it as local), so a disabled extension stays disabled.
    """
    _install_disabled_with_new_version(manager, source_dir)
    real_copytree = shutil.copytree
    listed: list[list[InstallationInfo]] = []

    def copytree(src, dst, *args, **kwargs):
        if Path(dst).is_relative_to(manager.installation_dir) and not listed:
            errors = _run_threads(
                ("list", lambda: listed.append(manager.list_installed()))
            )
            assert errors == []
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(manager_module.shutil, "copytree", copytree)

    manager.update("magic-test")

    assert len(listed) == 1, "update() did not copy into the installation directory"
    assert [(i.name, i.source, i.enabled) for i in listed[0]] == [
        ("magic-test", str(source_dir), False)
    ]
    info = manager.get("magic-test")
    assert info is not None
    assert (info.source, info.enabled) == (str(source_dir), False)
    assert (manager.installation_dir / "magic-test" / "new-version").exists()
    assert _staging_dirs(manager) == []


def test_list_cannot_run_between_the_swap_renames(
    manager: InstallationManager[MockExtension],
    source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """The swap leaves the install path missing between its two renames. The
    metadata lock must keep a list call from loading in that window, where it
    would prune the entry.
    """
    install_path = _install_disabled_with_new_version(manager, source_dir)
    original_load = InstallationMetadata._load_for_session.__func__
    list_loaded = threading.Event()

    def load(cls: type[InstallationMetadata], directory: Path):
        result = original_load(cls, directory)
        if threading.current_thread().name == "list":
            list_loaded.set()
        return result

    monkeypatch.setattr(InstallationMetadata, "_load_for_session", classmethod(load))
    real_replace = os.replace
    lists: list[threading.Thread] = []
    listed: list[list[InstallationInfo]] = []

    def replace(src, dst) -> None:
        real_replace(src, dst)
        if Path(src) == install_path and not lists:
            thread = threading.Thread(
                target=lambda: listed.append(manager.list_installed()),
                name="list",
                daemon=True,
            )
            lists.append(thread)
            thread.start()
            assert not list_loaded.wait(0.5), (
                "list_installed loaded metadata between the swap renames"
            )

    monkeypatch.setattr(manager_module.os, "replace", replace)

    manager.update("magic-test")

    assert len(lists) == 1, "update() did not move the installed directory aside"
    lists[0].join(10)
    assert [(i.name, i.enabled) for i in listed[0]] == [("magic-test", False)]


@pytest.mark.parametrize(
    "failing_renames",
    [{1}, {2}, {2, 3}],
    ids=["first_rename", "second_rename", "second_rename_and_rollback"],
)
def test_failed_swap_keeps_the_installed_version(
    manager: InstallationManager[MockExtension],
    source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    failing_renames: set[int],
):
    """A rename that fails during the swap (on Windows: a file in the
    directory is open) must leave the previous version installed and its
    metadata unchanged. If even the rollback fails, the previous version is
    kept in the staging directory rather than deleted.
    """
    install_path = _install_disabled_with_new_version(manager, source_dir)
    real_replace = os.replace
    renames: list[tuple[Path, Path]] = []

    def replace(src, dst) -> None:
        if install_path in (Path(src), Path(dst)):
            renames.append((Path(src), Path(dst)))
            if len(renames) in failing_renames:
                raise PermissionError(13, "simulated rename failure")
        real_replace(src, dst)

    monkeypatch.setattr(manager_module.os, "replace", replace)

    with pytest.raises(PermissionError, match="simulated rename failure"):
        manager.update("magic-test")

    monkeypatch.setattr(manager_module.os, "replace", real_replace)
    info = InstallationMetadata.load_from_dir(manager.installation_dir).extensions[
        "magic-test"
    ]
    assert (info.source, info.enabled) == (str(source_dir), False)
    if failing_renames == {2, 3}:
        assert not install_path.exists()
        [staging_dir] = _staging_dirs(manager)
        assert (staging_dir / "previous" / "extension.json").exists()
        assert not (staging_dir / "previous" / "new-version").exists()
    else:
        assert (install_path / "extension.json").exists()
        assert not (install_path / "new-version").exists()
        assert _staging_dirs(manager) == []


def test_previous_version_is_deleted_after_the_lock_is_released(
    manager: InstallationManager[MockExtension],
    source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Deleting the previous version takes time proportional to its size, so
    it must happen outside the lock: another session can open meanwhile.
    """
    _install_disabled_with_new_version(manager, source_dir)
    monkeypatch.setattr(metadata_module, "_LOCK_TIMEOUT_SECONDS", 0.2)
    real_rmtree = shutil.rmtree
    probes: list[list[BaseException]] = []

    def rmtree(path, *args, **kwargs):
        if Path(path).name.startswith(".magic-test.installing-"):
            probes.append(
                _run_threads(
                    (
                        "probe",
                        lambda: InstallationMetadata.open(
                            manager.installation_dir
                        ).__exit__(None, None, None),
                    )
                )
            )
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(manager_module.shutil, "rmtree", rmtree)

    manager.update("magic-test")

    assert probes == [[]]


def test_install_does_not_replace_a_directory_that_appeared_meanwhile(
    manager: InstallationManager[MockExtension],
    source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Without force, an install that finds the extension installed by the
    time it takes the lock (another install finished first) must fail, not
    swap the other install's directory away.
    """
    install_path = manager.installation_dir / "magic-test"
    real_copytree = shutil.copytree

    def copytree(src, dst, *args, **kwargs):
        if not install_path.exists():
            install_path.mkdir(parents=True)
            (install_path / "winner").write_text("1")
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(manager_module.shutil, "copytree", copytree)

    with pytest.raises(FileExistsError):
        manager.install(str(source_dir))

    assert (install_path / "winner").exists()
    assert not (install_path / "extension.json").exists()
    assert _staging_dirs(manager) == []
