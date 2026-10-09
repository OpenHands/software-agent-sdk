"""Concurrency regression tests for installation metadata"""

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel

from openhands.sdk.extensions.installation import (
    InstallationInterface,
    InstallationManager,
    InstallationMetadata,
    MetadataSession,
    metadata as metadata_module,
)
from openhands.sdk.utils import files as files_module


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

    original_load = InstallationMetadata.load_from_dir.__func__
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

    monkeypatch.setattr(InstallationMetadata, "load_from_dir", classmethod(load))

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


def test_unlocked_reader_never_sees_partial_metadata_during_save(
    manager: InstallationManager[MockExtension],
    installation_dir: Path,
    source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Torn read: a reader that does not take the lock (such as code reading
    ``.installed.json`` directly) must never see a truncated file, so a save
    must swap in a complete file rather than rewrite it in place.
    """
    manager.install(str(source_dir))
    metadata_path = InstallationMetadata.get_metadata_path(installation_dir)
    seen_mid_save = []
    real_replace = os.replace

    def replace(src, dst) -> None:
        if Path(dst) == metadata_path:
            loaded = InstallationMetadata.load_from_dir(installation_dir)
            seen_mid_save.append(loaded.extensions.get("magic-test"))
        real_replace(src, dst)

    monkeypatch.setattr(files_module.os, "replace", replace)

    manager.install(str(source_dir), force=True)

    assert seen_mid_save, "save_to_dir did not write through an atomic replace"
    info = seen_mid_save[0]
    assert info is not None
    assert info.source == str(source_dir)


def test_get_waits_for_an_open_session(
    manager: InstallationManager[MockExtension],
    installation_dir: Path,
    source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """``get()`` reads under the lock, so it can never hold the file open
    while a save replaces it (``os.replace`` fails on Windows in that case).
    """
    manager.install(str(source_dir))
    monkeypatch.setattr(metadata_module, "_LOCK_TIMEOUT_SECONDS", 0.2)

    with InstallationMetadata.open(installation_dir):
        errors = _run_threads(("get", lambda: manager.get("magic-test")))

    assert len(errors) == 1
    assert isinstance(errors[0], TimeoutError)
    info = manager.get("magic-test")
    assert info is not None
    assert info.source == str(source_dir)


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
                "load_from_dir",
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
