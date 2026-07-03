#!/usr/bin/env python3
"""Check compatibility of persisted settings payloads across releases.

This script guards the versioned persisted-settings surfaces that are already
loaded through explicit migration entry points today:

- ``validate_agent_settings(...)``
- ``ConversationSettings.from_persisted(...)``
- ``PersistedSettings.from_persisted(...)``

It validates two sources of historical payloads:

1. checked-in golden fixtures under ``tests/sdk/persisted_settings_baselines/``
2. payloads generated from the published PyPI baseline release in an isolated
   virtualenv

The check is intentionally separate from ``check_sdk_api_breakage.py`` because
it answers a different question: not whether exported Python symbols changed,
but whether persisted JSON from older releases still loads at runtime.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging import version as pkg_version
from pydantic import BaseModel

from openhands.agent_server.persistence import (
    PERSISTED_SETTINGS_SCHEMA_VERSION,
    PersistedSettings,
)
from openhands.sdk.settings import (
    AGENT_SETTINGS_SCHEMA_VERSION,
    CONVERSATION_SETTINGS_SCHEMA_VERSION,
    ConversationSettings,
    validate_agent_settings,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests" / "sdk" / "persisted_settings_baselines"
_FIXTURE_DIR_RE = re.compile(r"v(?P<version>[1-9][0-9]*)$")
_FIXTURE_EXPECTED_KEY = "__expected__"
_FIXTURE_SURFACE_PREFIXES = {
    "agent_settings": "agent_settings",
    "conversation_settings": "conversation_settings",
    "persisted_settings": "persisted_settings",
}
_PYPI_USER_AGENT = "openhands-persisted-settings-compat/1.0"
_BASELINE_PAYLOAD_SCRIPT = r"""
from __future__ import annotations

import json
from typing import Any


def _dump(model: Any, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"mode": "json"}
    if context is not None:
        kwargs["context"] = context
    payload = model.model_dump(**kwargs)
    if not isinstance(payload, dict):
        raise TypeError(f"Expected mapping payload, got {type(payload).__name__}")
    return payload


payloads: list[dict[str, Any]] = []


def emit(key: str, payload: dict[str, Any]) -> None:
    payloads.append({"key": key, "payload": payload})


try:
    from openhands.sdk import LLM
except Exception:
    from openhands.sdk.llm import LLM

try:
    from openhands.sdk import Tool
except Exception:
    from openhands.sdk.tool import Tool

import openhands.sdk.settings as settings_mod

AgentSettingsCls = (
    getattr(settings_mod, "OpenHandsAgentSettings", None)
    or getattr(settings_mod, "AgentSettings", None)
    or getattr(settings_mod, "LLMAgentSettings", None)
)
if AgentSettingsCls is None:
    raise RuntimeError("Baseline SDK has no agent settings class")

ConversationSettingsCls = getattr(settings_mod, "ConversationSettings", None)
if ConversationSettingsCls is None:
    raise RuntimeError("Baseline SDK has no ConversationSettings class")

agent_default = AgentSettingsCls()
emit(
    "agent_settings/default",
    _dump(agent_default, context={"expose_secrets": "plaintext"}),
)

agent_populated_kwargs: dict[str, Any] = {
    "llm": LLM(model="baseline-model"),
    "tools": [Tool(name="TerminalTool")],
}
rich_agent_kwargs = {
    **agent_populated_kwargs,
    "enable_sub_agents": True,
    "mcp_config": {
        "mcpServers": {
            "fetch": {
                "command": "uvx",
                "args": ["mcp-server-fetch"],
            }
        }
    },
}
try:
    agent_populated = AgentSettingsCls(**rich_agent_kwargs)
except Exception:
    agent_populated = AgentSettingsCls(**agent_populated_kwargs)

emit(
    "agent_settings/populated",
    _dump(agent_populated, context={"expose_secrets": "plaintext"}),
)

ACPAgentSettingsCls = getattr(settings_mod, "ACPAgentSettings", None)
if ACPAgentSettingsCls is not None:
    try:
        acp = ACPAgentSettingsCls(
            acp_server="claude-code",
            acp_model="claude-opus-4-6",
        )
    except Exception:
        acp = None
    if acp is not None:
        emit(
            "agent_settings/acp_populated",
            _dump(acp, context={"expose_secrets": "plaintext"}),
        )

conversation_default = ConversationSettingsCls()
emit("conversation_settings/default", _dump(conversation_default))

try:
    conversation_populated = ConversationSettingsCls(
        max_iterations=42,
        confirmation_mode=True,
        security_analyzer="llm",
    )
except Exception:
    conversation_populated = ConversationSettingsCls(max_iterations=42)

emit("conversation_settings/populated", _dump(conversation_populated))

try:
    from openhands.agent_server.persistence import PersistedSettings
except Exception:
    PersistedSettings = None

if PersistedSettings is not None:
    persisted_default = PersistedSettings()
    emit(
        "persisted_settings/default",
        _dump(persisted_default, context={"expose_secrets": "plaintext"}),
    )
    try:
        persisted_populated = PersistedSettings(
            agent_settings=agent_populated,
            conversation_settings=conversation_populated,
            active_profile="baseline-profile",
        )
    except Exception:
        persisted_populated = PersistedSettings()
    emit(
        "persisted_settings/populated",
        _dump(persisted_populated, context={"expose_secrets": "plaintext"}),
    )

print(json.dumps(payloads))
"""


class PersistedSettingsCompatError(RuntimeError):
    """Raised when a persisted payload no longer loads compatibly."""


@dataclass(frozen=True, slots=True)
class SurfaceConfig:
    key: str
    display_name: str
    current_version: int
    loader: Callable[[Any], BaseModel]
    migration_guidance: str
    dump_context: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class FixtureCase:
    path: Path
    surface_key: str
    version: int
    payload: dict[str, Any]
    expected_paths: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BaselinePayloadCase:
    source: str
    key: str
    surface_key: str
    payload: dict[str, Any]


SURFACES: dict[str, SurfaceConfig] = {
    "agent_settings": SurfaceConfig(
        key="agent_settings",
        display_name="AgentSettings",
        current_version=AGENT_SETTINGS_SCHEMA_VERSION,
        loader=validate_agent_settings,
        migration_guidance=(
            "If this persisted shape changed intentionally, bump "
            "AGENT_SETTINGS_SCHEMA_VERSION and add a migration in "
            "_AGENT_SETTINGS_MIGRATIONS."
        ),
        dump_context={"expose_secrets": "plaintext"},
    ),
    "conversation_settings": SurfaceConfig(
        key="conversation_settings",
        display_name="ConversationSettings",
        current_version=CONVERSATION_SETTINGS_SCHEMA_VERSION,
        loader=ConversationSettings.from_persisted,
        migration_guidance=(
            "If this persisted shape changed intentionally, bump "
            "CONVERSATION_SETTINGS_SCHEMA_VERSION and add a migration in "
            "_CONVERSATION_SETTINGS_MIGRATIONS."
        ),
    ),
    "persisted_settings": SurfaceConfig(
        key="persisted_settings",
        display_name="PersistedSettings",
        current_version=PERSISTED_SETTINGS_SCHEMA_VERSION,
        loader=PersistedSettings.from_persisted,
        migration_guidance=(
            "If this top-level settings file shape changed intentionally, bump "
            "PERSISTED_SETTINGS_SCHEMA_VERSION and update "
            "PersistedSettings.from_persisted(). Nested agent/conversation shape "
            "changes may also require AGENT_SETTINGS_SCHEMA_VERSION / "
            "_AGENT_SETTINGS_MIGRATIONS or CONVERSATION_SETTINGS_SCHEMA_VERSION / "
            "_CONVERSATION_SETTINGS_MIGRATIONS."
        ),
        dump_context={"expose_secrets": "plaintext"},
    ),
}


def read_version_from_pyproject(path: Path) -> str:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    try:
        return str(data["project"]["version"])
    except KeyError as exc:  # pragma: no cover - configuration contract
        raise SystemExit(f"Could not read version from {path}") from exc


def _parse_version(value: str) -> pkg_version.Version:
    return pkg_version.parse(value)


def _fetch_pypi_project_metadata(distribution: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url=f"https://pypi.org/pypi/{distribution}/json",
        headers={"User-Agent": _PYPI_USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            meta = json.load(response)
    except Exception as exc:
        raise PersistedSettingsCompatError(
            f"Failed to fetch PyPI metadata for {distribution}: {exc}"
        ) from exc
    if not isinstance(meta, dict):
        raise PersistedSettingsCompatError(
            f"Invalid PyPI metadata payload for {distribution}: {type(meta).__name__}"
        )
    return meta


def get_pypi_baseline_version(distribution: str, current: str) -> str | None:
    meta = _fetch_pypi_project_metadata(distribution)
    releases = meta.get("releases")
    if not isinstance(releases, dict):
        raise PersistedSettingsCompatError(
            f"PyPI metadata for {distribution} did not include a releases mapping."
        )
    release_versions = list(releases.keys())
    if not release_versions:
        return None

    if current in release_versions:
        return current

    current_parsed = _parse_version(current)
    older = [
        release
        for release in release_versions
        if _parse_version(release) < current_parsed
    ]
    if not older:
        return None
    return max(older, key=_parse_version)


def get_pypi_release_cutoff(distribution: str, release_version: str) -> str:
    meta = _fetch_pypi_project_metadata(distribution)
    releases = meta.get("releases")
    if not isinstance(releases, dict):
        raise PersistedSettingsCompatError(
            f"PyPI metadata for {distribution} did not include a releases mapping."
        )
    files = releases.get(release_version)
    if not isinstance(files, list) or not files:
        raise PersistedSettingsCompatError(
            f"PyPI metadata for {distribution} had no files for {release_version}."
        )
    upload_times = [
        file_info.get("upload_time_iso_8601")
        for file_info in files
        if isinstance(file_info, dict)
    ]
    valid_upload_times = [value for value in upload_times if isinstance(value, str)]
    if not valid_upload_times:
        raise PersistedSettingsCompatError(
            f"PyPI metadata for {distribution} had no upload_time_iso_8601 values "
            f"for {release_version}."
        )
    return max(valid_upload_times)


def _surface_key_for_name(name: str) -> str:
    for surface_key, prefix in _FIXTURE_SURFACE_PREFIXES.items():
        if name.startswith(prefix):
            return surface_key
    raise PersistedSettingsCompatError(
        f"Unrecognized persisted settings fixture name: {name}"
    )


def _model_dump_persisted(model: BaseModel, surface: SurfaceConfig) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"mode": "json"}
    if surface.dump_context is not None:
        kwargs["context"] = dict(surface.dump_context)
    payload = model.model_dump(**kwargs)
    if not isinstance(payload, dict):
        raise PersistedSettingsCompatError(
            f"{surface.display_name} did not serialize to a mapping."
        )
    return payload


def _copy_payload(data: Any) -> dict[str, Any]:
    if isinstance(data, BaseModel):
        payload = data.model_dump(mode="json")
        if not isinstance(payload, dict):
            raise PersistedSettingsCompatError(
                f"Expected persisted payload mapping, got {type(payload).__name__}"
            )
        return payload
    if isinstance(data, Mapping):
        return dict(data)
    raise PersistedSettingsCompatError(
        f"Expected persisted payload mapping, got {type(data).__name__}"
    )


def collect_fixture_cases(root: Path = FIXTURE_ROOT) -> list[FixtureCase]:
    if not root.exists():
        raise PersistedSettingsCompatError(
            f"Missing persisted settings fixture directory: {root}"
        )

    cases: list[FixtureCase] = []
    for version_dir in sorted(root.iterdir()):
        if not version_dir.is_dir():
            continue
        match = _FIXTURE_DIR_RE.fullmatch(version_dir.name)
        if match is None:
            raise PersistedSettingsCompatError(
                f"Invalid persisted settings fixture directory name: {version_dir.name}"
            )
        directory_version = int(match.group("version"))
        for fixture_path in sorted(version_dir.glob("*.json")):
            raw_fixture = json.loads(fixture_path.read_text())
            if not isinstance(raw_fixture, dict):
                raise PersistedSettingsCompatError(
                    f"Fixture {fixture_path} must contain a JSON object."
                )
            expected_paths_raw = raw_fixture.pop(_FIXTURE_EXPECTED_KEY, {})
            if not isinstance(expected_paths_raw, dict):
                raise PersistedSettingsCompatError(
                    f"Fixture {fixture_path} {_FIXTURE_EXPECTED_KEY} must be an object."
                )
            expected_paths = dict(expected_paths_raw)
            payload = raw_fixture
            surface_key = _surface_key_for_name(fixture_path.stem)
            payload_version = payload.get("schema_version")
            if not isinstance(payload_version, int) or isinstance(
                payload_version, bool
            ):
                raise PersistedSettingsCompatError(
                    f"Fixture {fixture_path} must declare an integer schema_version."
                )
            if payload_version != directory_version:
                raise PersistedSettingsCompatError(
                    f"Fixture {fixture_path} has schema_version {payload_version}, "
                    f"but is stored under {version_dir.name}."
                )
            cases.append(
                FixtureCase(
                    path=fixture_path,
                    surface_key=surface_key,
                    version=payload_version,
                    payload=payload,
                    expected_paths=expected_paths,
                )
            )
    if not cases:
        raise PersistedSettingsCompatError(
            f"No persisted settings fixtures found under {root}"
        )
    return cases


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def validate_fixture_cases(
    cases: Sequence[FixtureCase],
    *,
    surfaces: Mapping[str, SurfaceConfig] = SURFACES,
) -> None:
    seen_versions: dict[str, set[int]] = defaultdict(set)

    for case in cases:
        surface = surfaces[case.surface_key]
        if case.version > surface.current_version:
            raise PersistedSettingsCompatError(
                f"Fixture {case.path} uses schema_version {case.version}, newer than "
                f"supported {surface.display_name} version {surface.current_version}."
            )
        _validate_single_payload(
            payload=case.payload,
            surface=surface,
            origin=f"fixture {_display_path(case.path)}",
            expected_paths=case.expected_paths,
        )
        seen_versions[surface.key].add(case.version)

    for surface in surfaces.values():
        expected = set(range(1, surface.current_version + 1))
        missing = sorted(expected - seen_versions[surface.key])
        if missing:
            formatted = ", ".join(f"v{version}" for version in missing)
            raise PersistedSettingsCompatError(
                f"Missing persisted settings fixtures for {surface.display_name}: "
                f"{formatted}. Add a fixture under "
                "tests/sdk/persisted_settings_baselines/vN/ when introducing "
                "a new persisted schema version."
            )


def _get_nested_value(payload: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for segment in dotted_path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            raise PersistedSettingsCompatError(
                f"Missing expected persisted field {dotted_path!r}."
            )
        current = current[segment]
    return current


def _assert_expected_paths(
    *,
    payload: Mapping[str, Any],
    expected_paths: Mapping[str, Any],
    origin: str,
    surface: SurfaceConfig,
) -> None:
    for dotted_path, expected_value in expected_paths.items():
        actual_value = _get_nested_value(payload, dotted_path)
        if actual_value != expected_value:
            raise PersistedSettingsCompatError(
                f"{surface.display_name} payload from {origin} changed expected field "
                f"{dotted_path!r}: expected {expected_value!r}, got {actual_value!r}."
            )


def _validate_single_payload(
    *,
    payload: Mapping[str, Any],
    surface: SurfaceConfig,
    origin: str,
    expected_paths: Mapping[str, Any] | None = None,
) -> None:
    raw_payload = _copy_payload(payload)
    raw_version = raw_payload.get("schema_version")

    try:
        loaded = surface.loader(raw_payload)
    except Exception as exc:  # pragma: no cover - exercised in tests through message
        raise PersistedSettingsCompatError(
            f"{surface.display_name} payload from {origin} failed to load: {exc}. "
            f"{surface.migration_guidance}"
        ) from exc

    loaded_version = getattr(loaded, "schema_version", None)
    if loaded_version != surface.current_version:
        raise PersistedSettingsCompatError(
            f"{surface.display_name} payload from {origin} loaded with schema_version "
            f"{loaded_version}, expected {surface.current_version}."
        )

    roundtrip = _model_dump_persisted(loaded, surface)
    roundtrip_version = roundtrip.get("schema_version")
    if roundtrip_version != surface.current_version:
        raise PersistedSettingsCompatError(
            f"{surface.display_name} payload from {origin} round-tripped with "
            f"schema_version {roundtrip_version}, expected {surface.current_version}."
        )
    if expected_paths:
        _assert_expected_paths(
            payload=roundtrip,
            expected_paths=expected_paths,
            origin=origin,
            surface=surface,
        )

    try:
        reloaded = surface.loader(roundtrip)
    except Exception as exc:  # pragma: no cover - exercised in tests through message
        raise PersistedSettingsCompatError(
            f"{surface.display_name} payload from {origin} failed to reload after "
            f"round-trip: {exc}."
        ) from exc

    reloaded_version = getattr(reloaded, "schema_version", None)
    if reloaded_version != surface.current_version:
        raise PersistedSettingsCompatError(
            f"{surface.display_name} payload from {origin} reloaded with "
            f"schema_version {reloaded_version}, expected {surface.current_version}."
        )

    if isinstance(raw_version, int) and raw_version < surface.current_version:
        print(
            f"::notice title={surface.display_name}::Migrated {origin} from "
            f"schema_version {raw_version} to {surface.current_version}"
        )


def _venv_python(venv_dir: Path) -> Path:
    if sys.platform == "win32":  # pragma: no cover - CI uses Linux
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _uv_run(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True)


def generate_baseline_payloads(
    *,
    sdk_version: str,
    agent_server_version: str | None,
    exclude_newer: str,
) -> list[BaselinePayloadCase]:
    with tempfile.TemporaryDirectory(prefix="persisted-settings-baseline-") as tmp_dir:
        venv_dir = Path(tmp_dir) / "venv"
        python = _venv_python(venv_dir)
        packages = [f"openhands-sdk=={sdk_version}"]
        if agent_server_version is not None:
            packages.append(f"openhands-agent-server=={agent_server_version}")

        try:
            _uv_run(["uv", "venv", str(venv_dir), "--python", sys.executable])
            _uv_run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--quiet",
                    "--exclude-newer",
                    exclude_newer,
                    *packages,
                ]
            )
            result = _uv_run([str(python), "-c", _BASELINE_PAYLOAD_SCRIPT])
        except subprocess.CalledProcessError as exc:
            output = (exc.stdout or "") + ("\n" + exc.stderr if exc.stderr else "")
            excerpt = output.strip()[-1000:]
            baseline_desc = ", ".join(packages)
            raise PersistedSettingsCompatError(
                "Failed to generate baseline payloads from "
                f"{baseline_desc} with exclude-newer={exclude_newer}: {excerpt}"
            ) from exc

        raw_cases = json.loads(result.stdout)
        if not isinstance(raw_cases, list):
            raise PersistedSettingsCompatError(
                "Baseline payload generator did not return a JSON list."
            )

        cases: list[BaselinePayloadCase] = []
        for item in raw_cases:
            if not isinstance(item, dict):
                raise PersistedSettingsCompatError(
                    f"Invalid baseline payload item: {item!r}"
                )
            key = item.get("key")
            payload = item.get("payload")
            if not isinstance(key, str) or not isinstance(payload, dict):
                raise PersistedSettingsCompatError(
                    f"Invalid baseline payload entry: {item!r}"
                )
            surface_key = key.split("/", 1)[0]
            if surface_key not in SURFACES:
                raise PersistedSettingsCompatError(
                    f"Unknown baseline payload surface: {surface_key}"
                )
            cases.append(
                BaselinePayloadCase(
                    source=(
                        f"PyPI baseline openhands-sdk=={sdk_version}"
                        if agent_server_version is None
                        else (
                            f"PyPI baseline openhands-sdk=={sdk_version}, "
                            f"openhands-agent-server=={agent_server_version}"
                        )
                    ),
                    key=key,
                    surface_key=surface_key,
                    payload=payload,
                )
            )
        return cases


def validate_baseline_payload_cases(
    cases: Sequence[BaselinePayloadCase],
    *,
    surfaces: Mapping[str, SurfaceConfig] = SURFACES,
) -> None:
    for case in cases:
        surface = surfaces[case.surface_key]
        _validate_single_payload(
            payload=case.payload,
            surface=surface,
            origin=f"{case.source} ({case.key})",
        )


def _resolve_pypi_baselines() -> tuple[str | None, str | None, str | None]:
    sdk_current = read_version_from_pyproject(
        REPO_ROOT / "openhands-sdk" / "pyproject.toml"
    )
    sdk_baseline = get_pypi_baseline_version("openhands-sdk", sdk_current)
    if sdk_baseline is None:
        return None, None, None

    cutoffs = [get_pypi_release_cutoff("openhands-sdk", sdk_baseline)]
    agent_server_current = read_version_from_pyproject(
        REPO_ROOT / "openhands-agent-server" / "pyproject.toml"
    )
    agent_server_baseline = get_pypi_baseline_version(
        "openhands-agent-server", agent_server_current
    )
    if agent_server_baseline is not None:
        cutoffs.append(
            get_pypi_release_cutoff(
                "openhands-agent-server",
                agent_server_baseline,
            )
        )
    return sdk_baseline, agent_server_baseline, max(cutoffs)


def main() -> int:
    fixture_cases = collect_fixture_cases()
    validate_fixture_cases(fixture_cases)
    print(
        f"Validated {len(fixture_cases)} persisted settings fixture(s) under "
        f"{FIXTURE_ROOT.relative_to(REPO_ROOT)}"
    )

    sdk_baseline, agent_server_baseline, baseline_cutoff = _resolve_pypi_baselines()
    if sdk_baseline is None or baseline_cutoff is None:
        print(
            "::warning title=Persisted settings baseline::No published openhands-sdk "
            "baseline found; skipping PyPI payload generation"
        )
        return 0

    baseline_cases = generate_baseline_payloads(
        sdk_version=sdk_baseline,
        agent_server_version=agent_server_baseline,
        exclude_newer=baseline_cutoff,
    )
    validate_baseline_payload_cases(baseline_cases)
    baseline_summary = f"openhands-sdk=={sdk_baseline}"
    if agent_server_baseline is not None:
        baseline_summary += f", openhands-agent-server=={agent_server_baseline}"
    print(
        f"Validated {len(baseline_cases)} baseline payload(s) from PyPI release "
        f"{baseline_summary}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PersistedSettingsCompatError as exc:
        print(f"::error title=Persisted settings compatibility::{exc}")
        raise SystemExit(1) from exc
