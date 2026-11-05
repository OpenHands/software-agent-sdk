"""Saved-profile selection through the real CLI and persisted conversation path."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import inspect
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from openhands.agent_server.conversation_service import (
    get_default_conversation_service,
)
from openhands.agent_server.event_service import EventService
from openhands.agent_server.persistence import PersistedSettings
from openhands.agent_server.persistence.store import FileSettingsStore
from openhands.sdk import LLM, Agent
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.critic.impl.api import APIBasedCritic
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.profiles.agent_profile import (
    OpenHandsAgentProfile,
    ProfileVerificationSettings,
)
from openhands.sdk.profiles.agent_profile_store import AgentProfileStore
from openhands.sdk.settings.model import (
    LLMSummarizingCondenserSettings,
    OpenHandsAgentSettings,
)
from openhands.sdk.tool import Tool
from openhands.sdk.tool.builtins import BUILT_IN_TOOLS, SwitchLLMTool
from openhands.sdk.utils.cipher import Cipher
from openhands.workspace.docker.workspace import find_available_tcp_port


REPO_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = "tests.agent_server.profile_agent_bootstrap"
OBSERVER = "tests.agent_server.test_profile_agent_selection"
OBSERVATIONS_ENV = "PROFILE_SELECTION_OBSERVATIONS"
CUSTOM_AGENT = "ProfileSelectionAgent"
START_TIMEOUT = 30.0
STOP_TIMEOUT = 15.0
# HTTPS skill acquisition is denied by the CLI child Git transport policy.
REQUEST_TIMEOUT = 30.0
LLM_KEY = "profile-selection-llm-test-value"
MCP_KEY = "profile-selection-mcp-test-value"
SUFFIX = "Saved-profile selection sentinel."
LLM_URL = "http://127.0.0.1:9/v1"
MCP_URL = "http://127.0.0.1:9/mcp"
CRITIC_URL = "http://127.0.0.1:9/critic"


def _pin(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": str(path),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _secret_matches(value: str | SecretStr | None, expected: str) -> bool:
    return (
        value.get_secret_value() if isinstance(value, SecretStr) else value
    ) == expected


def _snapshot(service: EventService, custom: bool) -> dict[str, Any]:
    conversation = service.get_conversation()
    agent = conversation.agent
    instance_token = None
    if custom:
        # The real CLI already imported this module before sealing the catalog.
        from tests.agent_server.profile_agent_bootstrap import ProfileSelectionAgent

        exact_class = type(agent) is ProfileSelectionAgent
        assert isinstance(agent, ProfileSelectionAgent)
        instance_token = str(agent.instance_token)
        attachment_absent = agent.owner_attachment_absent
    else:
        exact_class = type(agent) is Agent
        attachment_absent = BOOTSTRAP not in sys.modules

    assert isinstance(agent, Agent)
    context = agent.agent_context
    condenser = agent.condenser
    critic = agent.critic
    assert context is not None
    assert isinstance(condenser, LLMSummarizingCondenser)
    assert isinstance(critic, APIBasedCritic)
    assert critic.iterative_refinement is not None
    mcp = agent.mcp_config["profile-sentinel"]
    assert mcp.headers is not None
    provenance = service.stored.launched_agent_profile
    assert provenance is not None
    return {
        "pid": os.getpid(),
        "running_loop": asyncio.get_running_loop().is_running(),
        "agent_module": type(agent).__module__,
        "agent_qualname": type(agent).__qualname__,
        "exact_class": exact_class,
        "instance_token": instance_token,
        "owner_attachment_absent": attachment_absent,
        "provenance": provenance.model_dump(mode="json"),
        "llm": {
            "model": agent.llm.model,
            "base_url": agent.llm.base_url,
            "temperature": agent.llm.temperature,
            "max_output_tokens": agent.llm.max_output_tokens,
            "stream": agent.llm.stream,
            "secret_preserved": _secret_matches(agent.llm.api_key, LLM_KEY),
        },
        "tools": [{"name": tool.name, "params": tool.params} for tool in agent.tools],
        "include_default_tools": agent.include_default_tools,
        "tool_concurrency_limit": agent.tool_concurrency_limit,
        "mcp": {
            "keys": list(agent.mcp_config),
            "url": mcp.url,
            "enabled": mcp.enabled,
            "secret_preserved": _secret_matches(mcp.headers["X-Sentinel"], MCP_KEY),
        },
        "context_suffix": context.system_message_suffix,
        "condenser": {
            "max_size": condenser.max_size,
            "keep_first": condenser.keep_first,
            "max_tokens": condenser.max_tokens,
            "secret_preserved": _secret_matches(condenser.llm.api_key, LLM_KEY),
        },
        "critic": {
            "server_url": critic.server_url,
            "model_name": critic.model_name,
            "threshold": critic.iterative_refinement.success_threshold,
            "max_iterations": critic.iterative_refinement.max_iterations,
            "secret_preserved": _secret_matches(critic.api_key, LLM_KEY),
        },
    }


class _ProfileObservation(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        directory = Path(os.environ[OBSERVATIONS_ENV])
        plan_path = directory / "observe.json"
        if not plan_path.is_file() or response.status_code != 200:
            return response
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        path = f"/api/conversations/{plan['conversation_id']}/events/search"
        if request.method != "GET" or request.url.path != path:
            return response
        try:
            # The production events route has already activated this service.
            service = await get_default_conversation_service().get_event_service(
                UUID(plan["conversation_id"])
            )
            assert service is not None
            observation = _snapshot(service, plan["custom"])
        except Exception as exc:
            observation = {
                "observer_error": type(exc).__name__,
                "frames": traceback.format_tb(exc.__traceback__),
            }
        observation["activation_status"] = response.status_code
        (directory / "observed.json").write_text(
            json.dumps(observation, sort_keys=True) + "\n", encoding="utf-8"
        )
        return response


def _install_observer() -> None:
    # Import only in the CLI child after its isolated config is in the environment.
    from openhands.agent_server.api import api

    api.add_middleware(_ProfileObservation)


@dataclass
class _Server:
    process: subprocess.Popen[str]
    url: str
    directory: Path
    stdout: Path
    ready: bool = False

    def startup_diagnostic(self) -> dict[str, Any]:
        log = self.stdout.read_text(encoding="utf-8")
        target_error = next(
            (
                line
                for line in log.splitlines()
                if line.startswith("ImportError: cannot import name 'register_agent'")
                and "openhands.sdk.agent" in line
            ),
            None,
        )
        target_absent = (
            self.process.poll() not in (None, 0)
            and target_error is not None
            and "profile_agent_bootstrap.py" in log
        )
        return {
            "kind": "TARGET_FEATURE_ABSENCE" if target_absent else "INVALID_SETUP",
            "boundary": "trusted_bootstrap" if target_absent else "startup",
            "target_error": target_error,
            "exit_code": self.process.poll(),
            "stdout": _pin(self.stdout),
            "unreached": ["profile_selection", "EventService", "reconstruction"],
        }

    def observe(self, conversation_id: UUID, *, custom: bool) -> dict[str, Any]:
        plan = {"conversation_id": str(conversation_id), "custom": custom}
        (self.directory / "observe.json").write_text(json.dumps(plan), encoding="utf-8")
        result_path = self.directory / "observed.json"
        result_path.unlink(missing_ok=True)
        with httpx.Client(base_url=self.url, trust_env=False) as client:
            response = client.get(
                f"/api/conversations/{conversation_id}/events/search",
                timeout=REQUEST_TIMEOUT,
            )
        assert response.status_code == 200
        assert not any(
            item["kind"] == "ActionEvent" for item in response.json()["items"]
        )
        observation = json.loads(result_path.read_text(encoding="utf-8"))
        assert "observer_error" not in observation, observation
        return observation


@dataclass
class _Scenario:
    runtime: Path
    evidence: Path
    profiles: AgentProfileStore
    environment: dict[str, str]
    request: pytest.FixtureRequest
    process_count: int = 0

    def save_profile(self, name: str, selector: str | None) -> OpenHandsAgentProfile:
        custom = selector == CUSTOM_AGENT
        payload: dict[str, Any] = {
            "name": name,
            "revision": 7,
            "llm_profile_ref": "selection-llm",
            "tools": [Tool(name="TerminalTool", params={"terminal_type": "subprocess"})]
            if custom
            else [],
            "system_message_suffix": SUFFIX,
            "mcp_server_refs": ["profile-sentinel"],
            "condenser": LLMSummarizingCondenserSettings(
                max_size=37, keep_first=1, max_tokens=2048
            ),
            "verification": ProfileVerificationSettings(
                critic_enabled=True,
                critic_server_url=CRITIC_URL,
                critic_model_name="profile-critic",
                enable_iterative_refinement=True,
                critic_threshold=0.71,
                max_refinement_iterations=2,
            ),
            "enable_switch_llm_tool": not custom,
            "tool_concurrency_limit": 3,
        }
        if selector is not None:
            payload["agent"] = selector
        profile = OpenHandsAgentProfile.model_validate(payload)
        self.profiles.save(profile)
        if selector is None:
            # Exercise an actually omitted selector in the persisted input.
            path = self.profiles.base_dir / f"{profile.name}.json"
            with self.profiles.lock():
                saved = json.loads(path.read_text(encoding="utf-8"))
                saved.pop("agent")
                path.write_text(json.dumps(saved) + "\n", encoding="utf-8")
        loaded = self.profiles.load(name)
        assert isinstance(loaded, OpenHandsAgentProfile)
        return loaded

    @contextmanager
    def server(self, *, custom: bool) -> Iterator[_Server]:
        self.process_count += 1
        directory = self.evidence / f"process-{self.process_count}"
        directory.mkdir()
        port = find_available_tcp_port()
        modules = f"{BOOTSTRAP},{OBSERVER}" if custom else OBSERVER
        argv = [
            sys.executable,
            "-m",
            "openhands.agent_server",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--import-modules",
            modules,
        ]
        environment = {
            **self.environment,
            OBSERVATIONS_ENV: str(directory),
            "GIT_ALLOW_PROTOCOL": "file",
        }
        stdout = directory / "server.stdout"
        forced_kill = False
        with (
            stdout.open("w", encoding="utf-8") as stream,
            subprocess.Popen(
                argv,
                cwd=REPO_ROOT,
                env=environment,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
            ) as process,
        ):
            server = _Server(process, f"http://127.0.0.1:{port}", directory, stdout)
            try:
                deadline = time.monotonic() + START_TIMEOUT
                with httpx.Client(trust_env=False, timeout=0.5) as client:
                    while time.monotonic() < deadline and process.poll() is None:
                        try:
                            if client.get(f"{server.url}/ready").status_code == 200:
                                server.ready = True
                                break
                        except httpx.RequestError:
                            pass
                        time.sleep(0.1)
                yield server
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=STOP_TIMEOUT)
                    except subprocess.TimeoutExpired:
                        forced_kill = True
                        process.kill()
                        process.wait(timeout=STOP_TIMEOUT)
                self.request.node.add_report_section(
                    "call",
                    "profile-agent-selection-92-process",
                    json.dumps(
                        {
                            "argv": argv,
                            "pid": process.pid,
                            "ready": server.ready,
                            "exit_code": process.returncode,
                            "reaped": process.poll() is not None,
                            "forced_kill": forced_kill,
                            "stdout": _pin(stdout),
                            "startup_failure": None
                            if server.ready
                            else server.startup_diagnostic(),
                        },
                        sort_keys=True,
                    ),
                )
                assert not forced_kill, "CLI failed to stop within its cleanup bound"

    def launch(self, server: _Server, profile: OpenHandsAgentProfile) -> UUID:
        conversation_id = uuid4()
        with httpx.Client(base_url=server.url, trust_env=False) as client:
            response = client.post(
                "/api/conversations",
                json={
                    "conversation_id": str(conversation_id),
                    "agent_profile_id": str(profile.id),
                    "workspace": {"working_dir": str(self.runtime / "workspace")},
                },
                timeout=REQUEST_TIMEOUT,
            )
        assert response.status_code in (200, 201)
        data = response.json()
        assert data["id"] == str(conversation_id)
        assert data["launched_agent_profile"] == {
            "agent_profile_id": str(profile.id),
            "revision": profile.revision,
        }
        return conversation_id

    def record(self, phase: str, observation: dict[str, Any]) -> None:
        self.request.node.add_report_section(
            "call",
            "profile-agent-selection-92-observation",
            json.dumps({"phase": phase, "observation": observation}, sort_keys=True),
        )


@pytest.fixture
def scenario(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[_Scenario]:
    runtime = tmp_path / "runtime"
    evidence = tmp_path / "evidence"
    runtime.mkdir()
    evidence.mkdir()
    persistence = runtime / ".openhands"
    key = secrets.token_hex(32)
    cipher = Cipher(key)
    config_path = runtime / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "session_api_keys": [],
                "secret_key": key,
                "conversations_path": str(runtime / "conversations"),
                "workspace_path": str(runtime / "workspace"),
                "bash_events_dir": str(runtime / "bash-events"),
                "enable_vscode": False,
                "preload_tools": False,
                "telemetry": {"exporter": "none"},
            }
        ),
        encoding="utf-8",
    )
    config_path.chmod(0o600)
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("OH_", "OPENHANDS_", "LMNR_", "OTEL_"))
        and name not in {"SESSION_API_KEY", OBSERVATIONS_ENV}
    }
    environment.update(
        {
            "OH_PERSISTENCE_DIR": str(persistence),
            "OPENHANDS_AGENT_SERVER_CONFIG_PATH": str(config_path),
            "TMUX_TMPDIR": str(runtime / "tmux"),
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    (runtime / "tmux").mkdir()
    try:
        bootstrap = Path(__file__).with_name("profile_agent_bootstrap.py")
        bootstrap_spec = importlib.util.find_spec(BOOTSTRAP)
        cli_spec = importlib.util.find_spec("openhands.agent_server.__main__")
        assert bootstrap_spec is not None and bootstrap_spec.origin is not None
        assert Path(bootstrap_spec.origin).resolve() == bootstrap.resolve()
        assert cli_spec is not None and cli_spec.origin is not None
        assert Path(cli_spec.origin).resolve() == (
            REPO_ROOT / "openhands-agent-server/openhands/agent_server/__main__.py"
        )
        assert Path(inspect.getfile(Agent)).resolve() == (
            REPO_ROOT / "openhands-sdk/openhands/sdk/agent/agent.py"
        )
        cli = subprocess.run(
            [sys.executable, "-m", "openhands.agent_server", "--help"],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=START_TIMEOUT,
            check=False,
        )
        assert cli.returncode == 0 and "--import-modules" in cli.stdout
        request.node.add_report_section(
            "setup",
            "profile-agent-selection-92-prerequisites",
            json.dumps(
                {
                    "interpreter": sys.executable,
                    "python": sys.version,
                    "sdk_version": version("openhands-sdk"),
                    "server_version": version("openhands-agent-server"),
                    "cli_help_exit": cli.returncode,
                    "cli_source": _pin(Path(cli_spec.origin)),
                    "bootstrap": _pin(bootstrap),
                    "observer": _pin(Path(__file__)),
                },
                sort_keys=True,
            ),
        )
        llm = LLM(
            model="openai/gpt-4o-mini",
            api_key=SecretStr(LLM_KEY),
            base_url=LLM_URL,
            temperature=0.23,
            max_output_tokens=731,
            usage_id="profile-selection",
            stream=False,
        )
        LLMProfileStore(persistence / "profiles").save(
            "selection-llm", llm, include_secrets=True, cipher=cipher
        )
        FileSettingsStore(persistence, cipher=cipher).save(
            PersistedSettings(
                agent_settings=OpenHandsAgentSettings(
                    llm=llm,
                    mcp_config={
                        "profile-sentinel": MCPServer(
                            url=MCP_URL,
                            enabled=False,
                            headers={"X-Sentinel": SecretStr(MCP_KEY)},
                        )
                    },
                )
            )
        )
        yield _Scenario(
            runtime,
            evidence,
            AgentProfileStore(persistence / "agent-profiles"),
            environment,
            request,
        )
    finally:
        # Preserve bounded observations/logs, not the fixture encryption key.
        shutil.rmtree(runtime)


def _assert_observation(
    observation: dict[str, Any], profile: OpenHandsAgentProfile, *, custom: bool
) -> None:
    assert observation["activation_status"] == 200
    assert observation["running_loop"] is True
    assert observation["exact_class"] is True
    assert observation["owner_attachment_absent"] is True
    assert observation["agent_module"] == (BOOTSTRAP if custom else Agent.__module__)
    assert observation["agent_qualname"] == (
        CUSTOM_AGENT if custom else Agent.__qualname__
    )
    assert observation["provenance"] == {
        "agent_profile_id": str(profile.id),
        "revision": profile.revision,
    }
    assert observation["llm"] == {
        "model": "openai/gpt-4o-mini",
        "base_url": LLM_URL,
        "temperature": 0.23,
        "max_output_tokens": 731,
        "stream": True,
        "secret_preserved": True,
    }
    assert observation["tools"] == (
        [{"name": "TerminalTool", "params": {"terminal_type": "subprocess"}}]
        if custom
        else []
    )
    defaults = [tool.__name__ for tool in BUILT_IN_TOOLS]
    if not custom:
        defaults.append(SwitchLLMTool.__name__)
    assert observation["include_default_tools"] == defaults
    assert observation["tool_concurrency_limit"] == 3
    assert observation["mcp"] == {
        "keys": ["profile-sentinel"],
        "url": MCP_URL,
        "enabled": False,
        "secret_preserved": True,
    }
    assert observation["context_suffix"] == SUFFIX
    assert observation["condenser"] == {
        "max_size": 37,
        "keep_first": 1,
        "max_tokens": 2048,
        "secret_preserved": True,
    }
    assert observation["critic"] == {
        "server_url": CRITIC_URL,
        "model_name": "profile-critic",
        "threshold": 0.71,
        "max_iterations": 2,
        "secret_preserved": True,
    }


def test_default_saved_profile_preserves_stock_agent(scenario: _Scenario) -> None:
    profiles = [
        scenario.save_profile("omitted-selector", None),
        scenario.save_profile("explicit-stock", "CodeActAgent"),
    ]
    with scenario.server(custom=False) as server:
        assert server.ready, server.startup_diagnostic()
        for profile in profiles:
            conversation_id = scenario.launch(server, profile)
            observation = server.observe(conversation_id, custom=False)
            scenario.record(profile.name, observation)
            _assert_observation(observation, profile, custom=False)


def test_trusted_bootstrap_enables_saved_profile_agent(scenario: _Scenario) -> None:
    profile = scenario.save_profile("trusted-agent", CUSTOM_AGENT)
    with scenario.server(custom=True) as server:
        assert server.ready, server.startup_diagnostic()
        conversation_id = scenario.launch(server, profile)
        fresh = server.observe(conversation_id, custom=True)
        scenario.record("fresh", fresh)
        _assert_observation(fresh, profile, custom=True)
        assert fresh["pid"] == server.process.pid
    persisted = (
        scenario.runtime / "conversations" / conversation_id.hex / "base_state.json"
    )
    scenario.record("persisted_before_restart", _pin(persisted))
    with scenario.server(custom=True) as restored_server:
        assert restored_server.ready, restored_server.startup_diagnostic()
        restored = restored_server.observe(conversation_id, custom=True)
        scenario.record("restored", restored)
        _assert_observation(restored, profile, custom=True)
        assert restored["pid"] == restored_server.process.pid
        assert restored["pid"] != fresh["pid"]
        assert restored["instance_token"] != fresh["instance_token"]


if OBSERVATIONS_ENV in os.environ:
    _install_observer()


# Security cases below reuse the frozen Behavioral preparation and observation.
SECURITY_FIXTURE_MODULE = "tests.sdk.agent.test_agent_registry"
SECURITY_CASE_ENV = "PROFILE_REGISTRY_CASE"
SECURITY_RESULT_ENV = "PROFILE_REGISTRY_RESULT"
MISSING_BOOTSTRAP = "tests.agent_server.profile_agent_bootstrap_missing92"


def _security_import_plan(scenario: _Scenario, case: str) -> Path:
    result = scenario.evidence / f"{case}.json"
    assert not result.exists()
    scenario.environment.update(
        {SECURITY_CASE_ENV: case, SECURITY_RESULT_ENV: str(result)}
    )
    return result


def _security_import_request(
    scenario: _Scenario, server: _Server, result_path: Path, *, duplicate: bool = False
) -> dict[str, Any]:
    assert not result_path.exists()
    conversation_id = uuid4()
    # This ordinary direct-agent request deliberately does not use the profile
    # constructor. Its real tool-module import is the timing boundary under test.
    stock = Agent(
        llm=LLM(
            model="openai/gpt-4o-mini",
            api_key=SecretStr(LLM_KEY),
            base_url=LLM_URL,
            stream=False,
        ),
        tools=[],
    )
    payload = {
        "conversation_id": str(conversation_id),
        "agent": stock.model_dump(mode="json", context={"expose_secrets": True}),
        "workspace": {"working_dir": str(scenario.runtime / "workspace")},
        "tool_module_qualnames": {"profile-registry-probe": SECURITY_FIXTURE_MODULE},
    }
    with httpx.Client(base_url=server.url, trust_env=False) as client:
        response = client.post(
            "/api/conversations", json=payload, timeout=REQUEST_TIMEOUT
        )
    probe = json.loads(result_path.read_text(encoding="utf-8"))
    cause = response.json().get("exception") if response.status_code == 500 else None
    scenario.record(
        "security_request_import",
        {
            "request_keys": sorted(payload),
            "conversation_id": str(conversation_id),
            "http_status": response.status_code,
            "native_exception": cause,
            "probe": probe,
        },
    )
    assert "agent_profile_id" not in payload
    assert probe["pid"] == server.process.pid
    assert probe["module"] == SECURITY_FIXTURE_MODULE
    if duplicate:
        # Qualified kind validation covers the entire hierarchy. After the
        # import, composing this direct request's response can itself refuse.
        # This is delivery evidence, not credit for the NEXT profile lookup.
        assert response.status_code in (200, 201, 500)
        if response.status_code == 500:
            assert isinstance(cause, str) and "Duplicate class definition" in cause
        assert probe["kind"]["unique"] is False
        assert "Duplicate class definition" in probe["kind"]["message"]
        assert probe["registration_calls"] == 0
    else:
        assert response.status_code in (200, 201)
        assert response.json()["id"] == str(conversation_id)
    return probe


def _security_profile_refusal(
    scenario: _Scenario, server: _Server, profile: OpenHandsAgentProfile
) -> dict[str, Any]:
    conversation_id = uuid4()
    state_path = (
        scenario.runtime / "conversations" / conversation_id.hex / "base_state.json"
    )
    assert not state_path.exists()
    with httpx.Client(base_url=server.url, trust_env=False) as client:
        response = client.post(
            "/api/conversations",
            json={
                "conversation_id": str(conversation_id),
                "agent_profile_id": str(profile.id),
                "workspace": {"working_dir": str(scenario.runtime / "workspace")},
            },
            timeout=REQUEST_TIMEOUT,
        )
    observation = {
        "conversation_id": str(conversation_id),
        "profile_id": str(profile.id),
        "profile_revision": profile.revision,
        "http_status": response.status_code,
        "native_exception": response.json().get("exception"),
        "base_state_created": state_path.exists(),
    }
    scenario.record("security_profile_refusal", observation)
    # On this qualified source a constructor ValueError is returned by the
    # existing generic exception handler. Retain the integration refusal and
    # conservation; the duplicate probe separately checks the public factory.
    assert response.status_code == 500, observation
    assert isinstance(observation["native_exception"], str)
    assert observation["base_state_created"] is False, observation
    return observation


def test_cli_seals_before_first_profile_construction(scenario: _Scenario) -> None:
    approved = scenario.save_profile("approved-before-request", CUSTOM_AGENT)
    late = scenario.save_profile("late-request-entry", "LateSelectionAgent")
    result_path = _security_import_plan(scenario, "server-late")
    with scenario.server(custom=True) as server:
        assert server.ready, server.startup_diagnostic()
        assert not list((scenario.runtime / "conversations").glob("*/base_state.json"))
        probe = _security_import_request(scenario, server, result_path)
        assert probe["case"] == "server-late"
        assert probe["kind"]["unique"] is True
        assert probe["registration_calls"] == 1
        conversation_id = scenario.launch(server, approved)
        observation = server.observe(conversation_id, custom=True)
        scenario.record("approved_after_request_registration", observation)
        _assert_observation(observation, approved, custom=True)
        _security_profile_refusal(scenario, server, late)
        assert probe["registration"]["refused"] is True, probe


def test_lookup_refuses_kind_loaded_after_sealing(scenario: _Scenario) -> None:
    profile = scenario.save_profile("approved-before-duplicate", CUSTOM_AGENT)
    result_path = _security_import_plan(scenario, "server-duplicate")
    with scenario.server(custom=True) as server:
        assert server.ready, server.startup_diagnostic()
        original_id = scenario.launch(server, profile)
        original = server.observe(original_id, custom=True)
        scenario.record("approved_before_duplicate", original)
        _assert_observation(original, profile, custom=True)
        probe = _security_import_request(scenario, server, result_path, duplicate=True)
        assert probe["case"] == "server-duplicate"
        assert probe["settings_lookup"]["refused"] is True, probe
        assert probe["settings_lookup"]["validation_error"] is True, probe
        # The same real saved profile must now refuse before a new EventService
        # can persist state, even though its original catalog entry still exists.
        _security_profile_refusal(scenario, server, profile)


def test_missing_trusted_bootstrap_refuses_startup(scenario: _Scenario) -> None:
    assert importlib.util.find_spec(MISSING_BOOTSTRAP) is None
    argv = [
        sys.executable,
        "-m",
        "openhands.agent_server",
        "--host",
        "127.0.0.1",
        "--port",
        str(find_available_tcp_port()),
        "--import-modules",
        MISSING_BOOTSTRAP,
    ]
    stdout = scenario.evidence / "missing-bootstrap.stdout"
    result = None
    try:
        with stdout.open("w", encoding="utf-8") as stream:
            # run() kills and reaps on timeout. A timeout is an unexpected test
            # failure; only the exact intended import failure is accepted here.
            result = subprocess.run(
                argv,
                cwd=REPO_ROOT,
                env={**scenario.environment, "GIT_ALLOW_PROTOCOL": "file"},
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=START_TIMEOUT,
                check=False,
            )
    finally:
        scenario.record(
            "intentional_missing_bootstrap",
            {
                "argv": argv,
                "exit_code": None if result is None else result.returncode,
                "child_completed": result is not None,
                "stdout": _pin(stdout),
            },
        )
    assert result is not None and result.returncode != 0
    output = stdout.read_text(encoding="utf-8")
    expected = f"ModuleNotFoundError: No module named '{MISSING_BOOTSTRAP}'"
    assert expected in output, output
    assert "Starting OpenHands Agent Server" not in output
    assert not list((scenario.runtime / "conversations").glob("*/base_state.json"))


def _security_restore_refusal(
    scenario: _Scenario,
    server: _Server,
    conversation_id: UUID,
    *,
    expected_kind_error: str,
) -> None:
    state_path = (
        scenario.runtime / "conversations" / conversation_id.hex / "base_state.json"
    )
    before = _pin(state_path)
    with httpx.Client(base_url=server.url, trust_env=False) as client:
        response = client.get(
            f"/api/conversations/{conversation_id}/events/search",
            timeout=REQUEST_TIMEOUT,
        )
    after = _pin(state_path)
    cause = response.json().get("exception")
    observation = {
        "pid": server.process.pid,
        "conversation_id": str(conversation_id),
        "http_status": response.status_code,
        "native_exception": cause,
        "state_before": before,
        "state_after": after,
        "persisted_kind": json.loads(state_path.read_text(encoding="utf-8"))["agent"][
            "kind"
        ],
    }
    scenario.record("security_restore_refusal", observation)
    assert response.status_code == 500, observation
    assert isinstance(cause, str) and expected_kind_error in cause, observation
    assert CUSTOM_AGENT in cause
    assert before == after, observation
    assert observation["persisted_kind"] == CUSTOM_AGENT


def test_persisted_restore_refuses_unavailable_or_ambiguous_kind(
    scenario: _Scenario,
) -> None:
    profile = scenario.save_profile("persisted-security-agent", CUSTOM_AGENT)
    with scenario.server(custom=True) as seed_server:
        assert seed_server.ready, seed_server.startup_diagnostic()
        conversation_id = scenario.launch(seed_server, profile)
        seeded = seed_server.observe(conversation_id, custom=True)
        scenario.record("security_restore_seed", seeded)
        _assert_observation(seeded, profile, custom=True)
        seed_pid = seed_server.process.pid
    state_path = (
        scenario.runtime / "conversations" / conversation_id.hex / "base_state.json"
    )
    scenario.record("security_persisted_seed", _pin(state_path))

    unavailable_probe = _security_import_plan(scenario, "server-unavailable")
    with scenario.server(custom=False) as unavailable_server:
        assert unavailable_server.ready, unavailable_server.startup_diagnostic()
        assert unavailable_server.process.pid != seed_pid
        unavailable = _security_import_request(
            scenario, unavailable_server, unavailable_probe
        )
        assert unavailable["case"] == "server-unavailable"
        assert unavailable["bootstrap_loaded"] is False
        assert unavailable["kind_lookup"]["refused"] is True
        assert unavailable["kind_lookup"]["validation_error"] is True
        assert unavailable["registration_calls"] == 0
        _security_restore_refusal(
            scenario,
            unavailable_server,
            conversation_id,
            expected_kind_error="Unknown kind",
        )
        unavailable_pid = unavailable_server.process.pid

    duplicate_probe = _security_import_plan(scenario, "server-duplicate")
    with scenario.server(custom=True) as ambiguous_server:
        assert ambiguous_server.ready, ambiguous_server.startup_diagnostic()
        assert ambiguous_server.process.pid not in {seed_pid, unavailable_pid}
        _security_import_request(
            scenario, ambiguous_server, duplicate_probe, duplicate=True
        )
        _security_restore_refusal(
            scenario,
            ambiguous_server,
            conversation_id,
            expected_kind_error="Duplicate class definition",
        )
