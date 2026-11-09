"""Load every golden persisted-settings payload the way an upgraded install
loads what an older release wrote: agent settings, agent profiles,
conversation settings and the agent server's settings file, each through the
public loader for its surface, keeping its sentinel values. Then show the
compatibility gate refusing a fixture set with a schema version missing.

Uses the gate's own fixture collection (`.github/scripts/
check_persisted_settings_compat.py`) so the same rules apply.
"""

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

from sdk_verify import emit

from openhands.sdk.profiles import validate_agent_profile
from openhands.sdk.settings import ConversationSettings, validate_agent_settings


repo = Path(__file__).resolve().parents[5]
script = repo / ".github" / "scripts" / "check_persisted_settings_compat.py"
spec = importlib.util.spec_from_file_location("persisted_compat", script)
compat = importlib.util.module_from_spec(spec)
sys.modules["persisted_compat"] = compat
spec.loader.exec_module(compat)

cases = compat.collect_fixture_cases()
loaders = {
    "agent_settings": validate_agent_settings,
    "agent_profile": validate_agent_profile,
    "conversation_settings": ConversationSettings.from_persisted,
}
loaded: Counter = Counter()
failures = []
for case in cases:
    loader = loaders.get(case.surface_key)
    if loader is None:
        continue
    try:
        model = loader(json.loads(json.dumps(case.payload)))
        if model.model_dump(mode="json").get("schema_version") is None:
            raise ValueError("no schema_version after load")
        loaded[case.surface_key] += 1
    except Exception as exc:
        failures.append(f"{case.path.name}: {type(exc).__name__}: {exc}"[:200])

gate_error = None
try:
    compat.validate_fixture_cases(cases)
except Exception as exc:
    gate_error = f"{type(exc).__name__}: {exc}"[:300]

latest = max(c.version for c in cases if c.surface_key == "agent_settings")
missing_error = None
try:
    compat.validate_fixture_cases(
        [
            c
            for c in cases
            if not (c.surface_key == "agent_settings" and c.version == latest)
        ]
    )
except Exception as exc:
    missing_error = str(exc)[:300]

# A fixture whose sentinel no longer survives loading is refused.
tampered = next(c for c in cases if c.expected_paths)
broken = compat.FixtureCase(
    path=tampered.path,
    surface_key=tampered.surface_key,
    version=tampered.version,
    payload=tampered.payload,
    expected_paths={k: "not-the-sentinel" for k in tampered.expected_paths},
)
sentinel_error = None
try:
    compat.validate_fixture_cases([broken])
except Exception as exc:
    sentinel_error = type(exc).__name__

emit(
    {
        "ok": True,
        "fixtures": len(cases),
        "surfaces": dict(Counter(c.surface_key for c in cases)),
        "versions": sorted({c.version for c in cases}),
        "loaded": dict(loaded),
        "load_failures": failures,
        "gate_error": gate_error,
        "missing_version_refused": missing_error is not None
        and "Missing persisted settings fixtures" in missing_error,
        "sentinel_refused": sentinel_error,
    }
)
