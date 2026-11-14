"""Start every built-in ACP provider's real server the way a launcher does
(`ACPAgentSettings(acp_server=KEY)` -> its default `npx` command) and ask it
for its models with `discover_acp_models()`, giving each a syntactically
valid but bogus API key. That runs the real handshake (initialize,
authenticate when offered, session/new, session mode) without any account.

Usage: live_handshake.py [PROVIDER ...]   (default: all of ACP_PROVIDERS)
Needs `npx` and the npm registry; exit 3 (blocked) without them.
"""

import shutil
import sys
import time
import urllib.request

from sdk_verify import blocked, emit, run_paths

from openhands.sdk.agent.acp_model_discovery import discover_acp_models
from openhands.sdk.settings import ACP_PROVIDERS, ACPAgentSettings


if shutil.which("npx") is None:
    blocked("npx (Node.js) is not on PATH")
try:
    urllib.request.urlopen("https://registry.npmjs.org/-/ping", timeout=10).read()
except Exception as exc:  # proxy-aware, unlike a raw socket probe
    blocked(f"npm registry unreachable: {exc}")

BOGUS = "sk-acp-verify-probe-0000000000000000000000000000"
# What a user of each CLI exports besides its API-key variable: kimi-code
# reads no key from the env unless KIMI_MODEL_NAME selects its env-only
# provider (paired with KIMI_MODEL_API_KEY); opencode shows its full model
# catalogue only with OPENCODE_API_KEY present.
EXTRA = {
    "kimi-code": {"KIMI_MODEL_NAME": "kimi-k2.7-code", "KIMI_MODEL_API_KEY": BOGUS},
    "opencode": {"OPENCODE_API_KEY": BOGUS},
}
keys = sys.argv[1:] or sorted(ACP_PROVIDERS)
work = run_paths()["run"] / "acp-live"
work.mkdir(parents=True, exist_ok=True)
probes: dict = {}
for key in keys:
    info = ACP_PROVIDERS[key]
    secrets = {info.api_key_env_var: BOGUS} if info.api_key_env_var else {}
    secrets.update(EXTRA.get(key, {}))
    t0 = time.monotonic()
    try:
        found = discover_acp_models(
            ACPAgentSettings(
                acp_server=key, acp_isolate_data_dir=True, acp_startup_timeout=240.0
            ),
            secrets=secrets,
            work_root=str(work),
        )
        data = found.model_dump(mode="json")
        models = [m.get("model_id") for m in data.get("available_models") or []]
        probes[key] = {
            "agent_name": data.get("agent_name"),
            "version": data.get("agent_version"),
            "error": (data.get("error") or {}).get("code"),
            "error_detail": ((data.get("error") or {}).get("detail") or "")[:200],
            "models": len(models),
            "current_in_models": data.get("current_model_id") in models
            if models
            else None,
            "ok": data.get("error") is None and bool(data.get("agent_name")),
        }
    except Exception as exc:
        probes[key] = {"ok": False, "raised": f"{type(exc).__name__}: {exc}"[:300]}
    probes[key]["seconds"] = round(time.monotonic() - t0, 1)

emit(
    {
        "ok": True,
        "providers": keys,
        "probes": probes,
        "all_ok": all(p.get("ok") for p in probes.values()),
        "failed": sorted(k for k, p in probes.items() if not p.get("ok")),
    }
)
