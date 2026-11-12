"""Tests for the pre-commit check that keeps store I/O off the event loop."""

import importlib.util
import textwrap
from pathlib import Path
from types import ModuleType

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_async_store_access.py"


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_async_store_access", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lines(checker: ModuleType, source: str) -> list[int]:
    violations = checker.check_source(Path("mod.py"), textwrap.dedent(source))
    return [violation.line for violation in violations]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            """
            async def route(config):
                store = get_settings_store(config)
                return store.load()
            """,
            id="local-from-getter",
        ),
        pytest.param(
            """
            async def route(config):
                return get_secrets_store(config).get_secret("X")
            """,
            id="inline-getter",
        ),
        pytest.param(
            """
            async def route(store: FileWorkspacesStore | None):
                store.update(lambda w: w)
            """,
            id="annotated-parameter",
        ),
        pytest.param(
            """
            class Registry:
                provisioning: RuntimeProvisioningStore

            async def route(registry):
                return registry.provisioning.load(1)
            """,
            id="attribute-through-other-object",
        ),
        pytest.param(
            """
            async def route(store: LLMProfileStore):
                return await asyncio.to_thread(store.list_summaries)
            """,
            id="to-thread-hop",
        ),
        pytest.param(
            """
            async def route(config):
                return await asyncio.to_thread(
                    lambda: get_settings_store(config).load() or None
                )
            """,
            id="lambda-hop",
        ),
        pytest.param(
            """
            async def route(loop, store: FileSecretsStore):
                return await loop.run_in_executor(None, store.get_secret, "X")
            """,
            id="run-in-executor-hop",
        ),
    ],
)
def test_flags_store_io_on_the_event_loop(checker: ModuleType, source: str) -> None:
    assert len(_lines(checker, source)) == 1


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            """
            async def route(config):
                store = get_settings_store(config)
                return await run_store(store.load)
            """,
            id="facade",
        ),
        pytest.param(
            """
            async def route(store: FileWorkspacesStore):
                def read():
                    return store.load(), store.load()
                return await run_store(read)
            """,
            id="nested-sync-helper",
        ),
        pytest.param(
            """
            def route(config):
                return get_settings_store(config).load()
            """,
            id="sync-route",
        ),
        pytest.param(
            """
            async def route(registry: Registry, store: LLMProfileStore):
                path = registry.provisioning.manifest_path(1)
                return store.with_cipher(None), path
            """,
            id="pure-methods",
        ),
        pytest.param(
            """
            async def route(loop, store: FileSecretsStore):
                return await loop.run_in_executor(None, probe, store.get_secret)
            """,
            id="store-method-as-callback",
        ),
    ],
)
def test_allows_store_io_off_the_event_loop(checker: ModuleType, source: str) -> None:
    assert _lines(checker, source) == []


def test_agent_server_has_no_blocking_store_access(
    checker: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    assert checker.main([]) == 0, capsys.readouterr().out
