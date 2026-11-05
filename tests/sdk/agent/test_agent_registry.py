"""Finite registry refusals, each exercised in a fresh SDK process."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from pydantic import ConfigDict, Field, SecretStr, create_model

from openhands.sdk import LLM, Agent, AgentBase
from openhands.sdk.settings.model import OpenHandsAgentSettings


REPO_ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP = "tests.agent_server.profile_agent_bootstrap"
FIXTURE_MODULE = "tests.sdk.agent.test_agent_registry"
CASE_ENV = "PROFILE_REGISTRY_CASE"
RESULT_ENV = "PROFILE_REGISTRY_RESULT"
IMPORT_ENV = "PROFILE_REGISTRY_IMPORT_MARKER"
CHILD_TIMEOUT = 30.0
CUSTOM_AGENT = "ProfileSelectionAgent"
FIXTURE: object | None = None
MAIN_FIXTURE: object | None = None


def _llm() -> LLM:
    return LLM(
        model="openai/gpt-4o-mini",
        api_key=SecretStr("profile-registry-test-value"),
        base_url="http://127.0.0.1:9/v1",
        temperature=0.23,
        max_output_tokens=731,
        stream=False,
    )


def _settings(selector: str | None = None) -> OpenHandsAgentSettings:
    arguments: dict[str, Any] = {
        "llm": _llm(),
        "tools": [],
        "tool_concurrency_limit": 3,
    }
    if selector is not None:
        arguments["agent"] = selector
    return OpenHandsAgentSettings(**arguments)


def _write_result(path: Path, result: dict[str, Any]) -> None:
    path.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")


def _type_name(value: object) -> str:
    cls = value if isinstance(value, type) else type(value)
    return f"{cls.__module__}.{cls.__qualname__}"


def _refusal(operation: Callable[[], object]) -> dict[str, Any]:
    # Only the target operation is inside this boundary. Preparation failures
    # and missing imports are reported by the outer child entry point.
    try:
        value = operation()
    except (TypeError, ValueError, RuntimeError) as exc:
        return {
            "refused": True,
            "error_type": type(exc).__name__,
            "validation_error": isinstance(exc, ValueError),
            "message": str(exc),
        }
    return {
        "refused": False,
        "returned_type": _type_name(value),
        "is_stock": type(value) is Agent,
    }


def _startup_functions() -> tuple[
    Callable[[type[AgentBase]], None], Callable[[], None]
]:
    # Deferred so the absent target API cannot break pytest collection.
    from openhands.sdk.agent import register_agent, seal_agent_registry

    return register_agent, seal_agent_registry


def _approved_class() -> type[Agent]:
    bootstrap = importlib.import_module(BOOTSTRAP)
    approved = bootstrap.ProfileSelectionAgent
    assert isinstance(approved, type) and issubclass(approved, Agent)
    assert approved.__module__ == BOOTSTRAP
    return approved


def _fixture_class() -> type[Agent]:
    fixture = importlib.import_module(FIXTURE_MODULE).FIXTURE
    assert isinstance(fixture, type) and issubclass(fixture, Agent)
    return fixture


def _local_class() -> type[Agent]:
    class LocalSelectionAgent(Agent):
        pass

    return LocalSelectionAgent


def _callable_fixture() -> None:
    return None


def _kind_observation(candidate: type[AgentBase]) -> dict[str, Any]:
    try:
        resolved = AgentBase.resolve_kind(candidate.__name__)
    except ValueError as exc:
        return {"unique": False, "error_type": type(exc).__name__, "message": str(exc)}
    return {"unique": resolved is candidate, "resolved_type": _type_name(resolved)}


def _selector_case(case: str, report: dict[str, Any]) -> None:
    if case == "selector-unregistered":
        candidate = _fixture_class()
        assert AgentBase.resolve_kind(candidate.__name__) is candidate
        selector = candidate.__name__
        report["loaded_unregistered_type"] = _type_name(candidate)
        assert BOOTSTRAP not in sys.modules
    elif case == "selector-import":
        selector = FIXTURE_MODULE
        assert FIXTURE_MODULE not in sys.modules
        assert importlib.util.find_spec(FIXTURE_MODULE) is not None
        assert not Path(os.environ[IMPORT_ENV]).exists()
    else:
        assert case == "selector-unknown"
        selector = "UnknownProfileSelectionAgent92"
        with pytest.raises(ValueError):
            AgentBase.resolve_kind(selector)
    settings = _settings(selector)
    report["prerequisites_complete"] = True
    report["boundary"] = "settings_selector"
    report["selector"] = selector
    report["selection"] = _refusal(settings.create_agent)
    if case == "selector-import":
        report["selector_module_imported"] = FIXTURE_MODULE in sys.modules
        report["import_marker_exists"] = Path(os.environ[IMPORT_ENV]).exists()


def _invalid_registration(case: str, report: dict[str, Any]) -> None:
    report["boundary"] = "trusted_bootstrap"
    approved = _approved_class()
    register, _ = _startup_functions()
    report["bootstrap_completed"] = True
    if case == "invalid-local":
        candidate: object = _local_class()
    elif case == "invalid-main":
        candidate = MAIN_FIXTURE
    elif case == "invalid-non-agent":
        candidate = dict
    elif case == "invalid-instance":
        # A direct stock constructor does not invoke the settings lookup/seal.
        candidate = Agent(llm=_llm(), tools=[])
    elif case == "invalid-callable":
        candidate = _callable_fixture
    else:
        candidate = _fixture_class()

    hierarchy_poisoned = case in {"invalid-local", "invalid-duplicate"}
    if isinstance(candidate, type) and issubclass(candidate, AgentBase):
        report["candidate_kind"] = _kind_observation(candidate)
        if hierarchy_poisoned:
            assert report["candidate_kind"]["unique"] is False
        elif case != "invalid-abstract":
            assert report["candidate_kind"]["unique"] is True
    if case == "invalid-abstract":
        assert isinstance(candidate, type) and inspect.isabstract(candidate)
    if case == "invalid-main":
        assert isinstance(candidate, type) and candidate.__module__ == "__main__"
        assert "<locals>" not in candidate.__qualname__
    if case == "invalid-local":
        assert isinstance(candidate, type) and "<locals>" in candidate.__qualname__
    report["candidate_type"] = _type_name(candidate)
    report["hierarchy_poisoned"] = hierarchy_poisoned
    report["prerequisites_complete"] = True
    report["boundary"] = "registration"
    report["registration"] = _refusal(lambda: register(cast(Any, candidate)))
    report["stock_defaults"] = [
        type(_settings(selector).create_agent()) is Agent
        for selector in (None, "CodeActAgent")
    ]
    if not hierarchy_poisoned:
        report["approved_entry_preserved"] = (
            type(_settings(CUSTOM_AGENT).create_agent()) is approved
        )
    else:
        # Failed registration cannot undo a naturally loaded local/duplicate
        # subclass. The SDK's global kind checks are deliberately untouched.
        report["approved_lookup"] = "NOT_ATTEMPTED_WITH_INVALID_KIND_HIERARCHY"


def _seal_case(case: str, report: dict[str, Any]) -> None:
    report["boundary"] = "trusted_bootstrap"
    approved = _approved_class()
    register, seal = _startup_functions()
    register(approved)
    report["identical_preseal_accepted"] = True
    late = _fixture_class()
    assert AgentBase.resolve_kind(late.__name__) is late
    report["prerequisites_complete"] = True
    report["boundary"] = "catalog_seal"
    if case == "seal-explicit":
        seal()
        seal()
    elif case == "seal-stock-lookup":
        assert type(_settings().create_agent()) is Agent
    else:
        assert case == "seal-custom-lookup"
        assert type(_settings(CUSTOM_AGENT).create_agent()) is approved
    # Test automatic sealing before an explicit seal could hide a missing call.
    report["identical_late"] = _refusal(lambda: register(approved))
    report["different_late"] = _refusal(lambda: register(late))
    seal()
    seal()
    report["seal_idempotent"] = True
    report["approved_entry_preserved"] = (
        type(_settings(CUSTOM_AGENT).create_agent()) is approved
    )
    report["late_selection"] = _refusal(_settings(late.__name__).create_agent)


def _configuration_case(case: str, report: dict[str, Any]) -> None:
    candidate = _fixture_class()
    constructor = cast(Callable[..., Agent], candidate)
    assert AgentBase.resolve_kind(candidate.__name__) is candidate
    # Prove the negative class arrangement through its actual constructor,
    # before asking the missing registration capability to authorize anything.
    if case == "config-unsupported":
        assert "tool_concurrency_limit" not in candidate.model_fields
        ordinary = constructor(llm=_llm(), tools=[], tool_concurrency_limit=3)
        assert ordinary.tool_concurrency_limit == 1
        report["fixture_field_loss"] = {"supplied": 3, "observed": 1}
    elif case == "config-alias":
        ordinary = constructor(llm=_llm(), tools=[], tool_concurrency_limit=3)
        via_alias = constructor(llm=_llm(), tools=[], parallelism=3)
        assert ordinary.tool_concurrency_limit == 1
        assert via_alias.tool_concurrency_limit == 3
        report["fixture_field_loss"] = {
            "field_name_supplied": 3,
            "field_name_observed": 1,
            "alias_observed": 3,
        }
    else:
        assert case == "config-required"
        assert candidate.model_fields["required_startup_value"].is_required()
        with pytest.raises(ValueError):
            constructor(llm=_llm(), tools=[])
        report["fixture_missing_required_refuses"] = True
    report["fixture_arrangement_complete"] = True
    report["boundary"] = "trusted_bootstrap"
    approved = _approved_class()
    register, _ = _startup_functions()
    report["prerequisites_complete"] = True
    report["boundary"] = "configuration_registration"
    registration = _refusal(lambda: register(candidate))
    report["registration"] = registration
    if registration["refused"]:
        report["refusal_boundary"] = "registration"
    else:
        report["boundary"] = "settings_constructor"
        report["selection"] = _refusal(_settings(candidate.__name__).create_agent)
        report["refusal_boundary"] = "settings_constructor"
    report["approved_entry_preserved"] = (
        type(_settings(CUSTOM_AGENT).create_agent()) is approved
    )


def _worker() -> None:
    assert len(sys.argv) == 3
    case, result_name = sys.argv[1:]
    report: dict[str, Any] = {
        "case": case,
        "pid": os.getpid(),
        "completed": False,
        "prerequisites_complete": False,
        "boundary": "ordinary_setup",
    }
    try:
        assert os.environ[CASE_ENV] == case
        assert Path(inspect.getfile(Agent)).resolve() == (
            REPO_ROOT / "openhands-sdk/openhands/sdk/agent/agent.py"
        )
        assert Path(inspect.getfile(OpenHandsAgentSettings)).resolve() == (
            REPO_ROOT / "openhands-sdk/openhands/sdk/settings/model.py"
        )
        assert (REPO_ROOT / "tests/agent_server/profile_agent_bootstrap.py").is_file()
        report["source_paths_valid"] = True
        if case.startswith("selector-"):
            _selector_case(case, report)
        elif case.startswith("invalid-"):
            _invalid_registration(case, report)
        elif case.startswith("seal-"):
            _seal_case(case, report)
        else:
            assert case.startswith("config-")
            _configuration_case(case, report)
        report["completed"] = True
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)
        report["classification"] = (
            "TARGET_API_ABSENT_DOWNSTREAM_UNREACHED"
            if report["boundary"] == "trusted_bootstrap"
            and isinstance(exc, ImportError)
            and "register_agent" in str(exc)
            and "openhands.sdk.agent" in str(exc)
            else "INVALID_SETUP_OR_UNEXPECTED_FAILURE"
        )
        traceback.print_exc()
    finally:
        _write_result(Path(result_name), report)
    if not report["completed"]:
        raise SystemExit(1)


def _run_case(
    tmp_path: Path, request: pytest.FixtureRequest, case: str
) -> dict[str, Any]:
    directory = tmp_path / case
    directory.mkdir()
    result_path = directory / "result.json"
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("OH_", "OPENHANDS_", "LMNR_", "OTEL_"))
        and name
        not in {
            "SESSION_API_KEY",
            "PROFILE_SELECTION_OBSERVATIONS",
            CASE_ENV,
            RESULT_ENV,
            IMPORT_ENV,
        }
    }
    environment.update(
        {
            "OH_PERSISTENCE_DIR": str(directory / ".openhands"),
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_TERMINAL_PROMPT": "0",
            CASE_ENV: case,
            RESULT_ENV: str(result_path),
            IMPORT_ENV: str(directory / "imported.json"),
        }
    )
    argv = [sys.executable, "-m", FIXTURE_MODULE, case, str(result_path)]
    stdout = directory / "stdout.txt"
    stderr = directory / "stderr.txt"
    process_result = None
    report: dict[str, Any] = {"case": case, "completed": False}
    try:
        with (
            stdout.open("w", encoding="utf-8") as out,
            stderr.open("w", encoding="utf-8") as err,
        ):
            process_result = subprocess.run(
                argv,
                cwd=REPO_ROOT,
                env=environment,
                stdout=out,
                stderr=err,
                text=True,
                timeout=CHILD_TIMEOUT,
                check=False,
            )
    finally:
        if result_path.is_file():
            report = json.loads(result_path.read_text(encoding="utf-8"))
        request.node.add_report_section(
            "call",
            "profile-agent-security-92-sdk",
            json.dumps(
                {
                    "argv": argv,
                    "exit_code": None
                    if process_result is None
                    else process_result.returncode,
                    "result": report,
                    "stdout": str(stdout),
                    "stderr": str(stderr),
                    "child_reaped": process_result is not None,
                },
                sort_keys=True,
            ),
        )
    assert process_result is not None and process_result.returncode == 0, report
    assert report["completed"] is True, report
    assert report["prerequisites_complete"] is True, report
    return report


@pytest.mark.parametrize(
    "case",
    ("selector-unknown", "selector-unregistered", "selector-import"),
    ids=("unknown", "loaded-unregistered", "import-shaped"),
)
def test_unapproved_selectors_refuse_without_imports(
    tmp_path: Path, request: pytest.FixtureRequest, case: str
) -> None:
    result = _run_case(tmp_path, request, case)
    assert result["selection"]["refused"] is True, result
    assert result["selection"]["validation_error"] is True, result
    if case == "selector-import":
        assert result["selector_module_imported"] is False
        assert result["import_marker_exists"] is False


@pytest.mark.parametrize(
    "case",
    (
        "invalid-reserved",
        "invalid-duplicate",
        "invalid-abstract",
        "invalid-local",
        "invalid-main",
        "invalid-non-agent",
        "invalid-instance",
        "invalid-callable",
    ),
    ids=(
        "reserved",
        "duplicate",
        "abstract",
        "local",
        "main",
        "non-agent",
        "instance",
        "callable",
    ),
)
def test_invalid_registration_preserves_catalog_and_reserved_default(
    tmp_path: Path, request: pytest.FixtureRequest, case: str
) -> None:
    result = _run_case(tmp_path, request, case)
    assert result["registration"]["refused"] is True, result
    assert result["stock_defaults"] == [True, True], result
    if not result["hierarchy_poisoned"]:
        assert result["approved_entry_preserved"] is True, result


@pytest.mark.parametrize(
    "case",
    ("seal-explicit", "seal-stock-lookup", "seal-custom-lookup"),
    ids=("explicit", "first-stock-lookup", "first-custom-lookup"),
)
def test_registration_idempotence_and_seal_boundaries(
    tmp_path: Path, request: pytest.FixtureRequest, case: str
) -> None:
    result = _run_case(tmp_path, request, case)
    assert result["identical_preseal_accepted"] is True
    assert result["identical_late"]["refused"] is True, result
    assert result["different_late"]["refused"] is True, result
    assert result["seal_idempotent"] is True
    assert result["approved_entry_preserved"] is True
    assert result["late_selection"]["refused"] is True, result
    assert result["late_selection"]["validation_error"] is True, result


@pytest.mark.parametrize(
    "case",
    ("config-unsupported", "config-required", "config-alias"),
    ids=("unsupported-field", "missing-required", "alias-field-loss"),
)
def test_incompatible_configuration_refuses_before_activation(
    tmp_path: Path, request: pytest.FixtureRequest, case: str
) -> None:
    result = _run_case(tmp_path, request, case)
    assert result["fixture_arrangement_complete"] is True
    if result["refusal_boundary"] == "settings_constructor":
        assert result["selection"]["refused"] is True, result
        assert result["selection"]["validation_error"] is True, result
    else:
        assert result["registration"]["refused"] is True, result
    assert result["approved_entry_preserved"] is True


# These are real importable fixture classes. The parent pytest environment never
# sets CASE_ENV; each SDK child or request-import probe has one fixed case.
if __name__ != "__main__":
    _case = os.environ.get(CASE_ENV)
    if _case == "selector-import":
        _write_result(
            Path(os.environ[IMPORT_ENV]),
            {"pid": os.getpid(), "module": __name__},
        )
    elif _case == "selector-unregistered":

        class UnregisteredSelectionAgent(Agent):
            pass

        FIXTURE = UnregisteredSelectionAgent
    elif _case == "invalid-reserved":

        class CodeActAgent(Agent):
            pass

        FIXTURE = CodeActAgent
    elif _case in {"invalid-duplicate", "server-duplicate"}:

        class ProfileSelectionAgent(Agent):
            pass

        FIXTURE = ProfileSelectionAgent
    elif _case == "invalid-abstract":

        class AbstractSelectionAgent(Agent, ABC):
            @abstractmethod
            def required_fixture_method(self) -> None:
                raise NotImplementedError

        FIXTURE = AbstractSelectionAgent
    elif _case in {
        "seal-explicit",
        "seal-stock-lookup",
        "seal-custom-lookup",
        "server-late",
    }:

        class LateSelectionAgent(Agent):
            pass

        FIXTURE = LateSelectionAgent
    elif _case == "config-unsupported":
        UnsupportedPayloadAgent = create_model(
            "UnsupportedPayloadAgent",
            __base__=Agent,
            __module__=__name__,
            tool_concurrency_limit=(ClassVar[int], 1),
        )
        FIXTURE = UnsupportedPayloadAgent
    elif _case == "config-required":

        class RequiredPayloadAgent(Agent):
            required_startup_value: str

        FIXTURE = RequiredPayloadAgent
    elif _case == "config-alias":

        class AliasDroppingAgent(Agent):
            model_config = ConfigDict(validate_by_alias=True, validate_by_name=False)
            tool_concurrency_limit: int = Field(
                default=1, ge=1, validation_alias="parallelism"
            )

        FIXTURE = AliasDroppingAgent

    if _case in {"server-late", "server-duplicate"}:
        _fixture = FIXTURE
        assert isinstance(_fixture, type) and issubclass(_fixture, Agent)
        _observation: dict[str, Any] = {
            "case": _case,
            "pid": os.getpid(),
            "module": __name__,
            "candidate_type": _type_name(_fixture),
            "kind": _kind_observation(_fixture),
            "registration_calls": 0,
        }
        if _case == "server-late":
            assert _observation["kind"]["unique"] is True
            _register, _seal = _startup_functions()
            _observation["registration_calls"] = 1
            _observation["registration"] = _refusal(
                lambda: _register(cast(type[AgentBase], _fixture))
            )
        else:
            _observation["settings_lookup"] = _refusal(
                _settings(CUSTOM_AGENT).create_agent
            )
        _write_result(Path(os.environ[RESULT_ENV]), _observation)
    elif _case == "server-unavailable":
        _write_result(
            Path(os.environ[RESULT_ENV]),
            {
                "case": _case,
                "pid": os.getpid(),
                "module": __name__,
                "bootstrap_loaded": BOOTSTRAP in sys.modules,
                "kind_lookup": _refusal(lambda: AgentBase.resolve_kind(CUSTOM_AGENT)),
                "registration_calls": 0,
            },
        )


if __name__ == "__main__" and os.environ.get(CASE_ENV) == "invalid-main":

    class MainSelectionAgent(Agent):
        pass

    MAIN_FIXTURE = MainSelectionAgent


if __name__ == "__main__":
    _worker()
