"""The ACP provider registry and install catalog as a launcher reads them:
which command starts each provider, how a running server or a command is
recognised, which file secrets a provider takes, and the npm install plan the
agent-server image is built from.

Usage: acp_catalog.py [registry]   (default) offline facts
       acp_catalog.py npm          also ask the npm registry that every pinned
                                   version exists (needs network; exit 3 if
                                   the registry cannot be reached)
"""

import sys
from urllib.parse import quote

from sdk_verify import blocked, emit

from openhands.sdk.settings import (
    ACP_PROVIDERS,
    ACPAgentSettings,
    default_acp_file_secrets,
    detect_acp_provider_by_agent_name,
    get_acp_provider,
)
from openhands.sdk.settings.acp_install_catalog import (
    ACP_INSTALL_CATALOG,
    DEFAULT_PREINSTALLED_ACP_PROVIDERS,
    render_docker_install_plan,
)
from openhands.sdk.settings.acp_providers import detect_acp_provider_by_command


mode = sys.argv[1] if len(sys.argv) > 1 else "registry"
out: dict = {"ok": True, "mode": mode}
out["providers"] = sorted(ACP_PROVIDERS)
out["commands_pin_catalog"] = {
    key: all(
        any(pkg.pinned in tok for tok in info.default_command)
        for pkg in ACP_INSTALL_CATALOG[key].packages
    )
    for key, info in ACP_PROVIDERS.items()
    if key in ACP_INSTALL_CATALOG
}
out["native_commands"] = {
    key: list(info.default_command)
    for key, info in ACP_PROVIDERS.items()
    if info.default_command[0] != "npx"
}
out["catalog_is_npm_providers"] = set(ACP_INSTALL_CATALOG) == {
    key for key, info in ACP_PROVIDERS.items() if info.default_command[0] == "npx"
}
out["api_key_env"] = {k: v.api_key_env_var for k, v in ACP_PROVIDERS.items()}
out["by_agent_name"] = {
    name: getattr(detect_acp_provider_by_agent_name(name), "key", None)
    for name in (
        "claude-agent-acp",
        "Codex-ACP",
        "gemini-cli",
        "Antigravity",
        "some-random-agy-utility",
        "legacy",
        "my-own-agent",
    )
}
out["by_command"] = {
    label: getattr(detect_acp_provider_by_command(cmd), "key", None)
    for label, cmd in (
        ("npx_codex", ["npx", "-y", "@agentclientprotocol/codex-acp@1.10.0"]),
        ("bin_claude", ["/opt/node_modules/.bin/claude-agent-acp"]),
        ("wrapper", ["my-codex-acp-wrapper"]),
        ("custom", ["python", "fake_acp.py"]),
        ("agy", ["agy", "acp"]),
        ("bin_agy", ["/usr/local/bin/agy", "acp"]),
        ("agy_wrapper", ["/usr/bin/my-agy-wrapper"]),
        ("legacy", ["legacy", "acp"]),
    )
}
# A native provider: the settings resolve its own launcher, and an explicit
# path survives a JSON round trip.
antigravity = {}
for label, command in (
    ("default", []),
    ("explicit", ["/opt/antigravity/bin/agy", "acp"]),
):
    settings = ACPAgentSettings(acp_server="antigravity", acp_command=command)
    restored = ACPAgentSettings.model_validate_json(settings.model_dump_json())
    agent = restored.create_agent()
    antigravity[label] = {
        "server": agent.acp_server,
        "command": agent.acp_command,
        "detected": getattr(
            detect_acp_provider_by_command(agent.acp_command), "key", None
        ),
    }
antigravity["api_key_env"] = ACP_PROVIDERS["antigravity"].api_key_env_var
antigravity["session_mode"] = ACP_PROVIDERS["antigravity"].default_session_mode
antigravity["in_install_catalog"] = "antigravity" in ACP_INSTALL_CATALOG
out["antigravity"] = antigravity
out["file_secrets"] = {
    k: [s.secret_name for s in v.file_secrets]
    for k, v in ACP_PROVIDERS.items()
    if v.file_secrets
}
out["default_file_secrets"] = sorted(s.secret_name for s in default_acp_file_secrets())
out["unknown_provider"] = get_acp_provider("nope")
packages, bins = render_docker_install_plan(DEFAULT_PREINSTALLED_ACP_PROVIDERS)
out["default_plan"] = {"packages": packages, "bins": bins}
pi_packages, _ = render_docker_install_plan(["pi", "pi"])
out["pi_packages_deduplicated"] = (
    len(pi_packages) == len(set(pi_packages)) == len(ACP_INSTALL_CATALOG["pi"].packages)
)
try:
    render_docker_install_plan(["nope"])
    out["unknown_plan_error"] = None
except ValueError as exc:
    out["unknown_plan_error"] = str(exc)[:200]

if mode == "npm":
    import httpx

    pins = sorted(
        {p for spec in ACP_INSTALL_CATALOG.values() for p in spec.packages},
        key=lambda p: p.pinned,
    )
    found = {}
    try:
        with httpx.Client(timeout=20) as client:
            for pin in pins:
                r = client.get(
                    f"https://registry.npmjs.org/{quote(pin.name, safe='@')}/"
                    f"{pin.version}"
                )
                found[pin.pinned] = (
                    r.status_code == 200 and r.json().get("version") == pin.version
                )
    except httpx.HTTPError as exc:
        blocked(f"npm registry unreachable: {exc}")
    out["pins"] = len(pins)
    out["pins_published"] = sum(found.values())
    out["missing_pins"] = [k for k, v in found.items() if not v]

emit(out)
