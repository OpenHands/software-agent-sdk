"""Sandbox launch metadata on agent profiles.

Usage: profile_sandbox.py

`sandbox=ProfileSandboxSettings(spec_id=...)` on both profile variants:
validation, a round trip through `AgentProfileStore`, an older payload without
the field, the refused shapes, and `resolve()` giving the same settings with
and without it. No model call is made.
"""

from pydantic import SecretStr, ValidationError
from sdk_verify import emit

from openhands.sdk import LLM
from openhands.sdk.launch import LaunchStores, resolve
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.profiles import (
    AgentProfileStore,
    OpenHandsAgentProfile,
    ProfileSandboxSettings,
    validate_agent_profile,
)


SPEC = "registry.example/agent-server:qa-s19"
out: dict = {"ok": True}

old = validate_agent_profile(
    {"schema_version": 4, "name": "old", "llm_profile_ref": "fast"}
)
out["old_payload_sandbox"] = old.sandbox
out["old_dump_sandbox"] = old.model_dump(mode="json").get("sandbox", "absent")

variants = {}
for kind in ("openhands", "acp"):
    payload: dict = {"agent_kind": kind, "name": f"qa-{kind}", "sandbox": {"spec_id": SPEC}}
    if kind == "openhands":
        payload["llm_profile_ref"] = "fast"
    else:
        payload["acp_server"] = "codex"
    profile = validate_agent_profile(payload)
    variants[kind] = {
        "type": type(profile).__name__,
        "spec_id": profile.sandbox.spec_id if profile.sandbox else None,
        "is_settings_type": isinstance(profile.sandbox, ProfileSandboxSettings),
        "round_trip": validate_agent_profile(profile.model_dump(mode="json")) == profile,
    }
out["variants"] = variants

refused = {}
for label, sandbox in {
    "empty_spec": {"spec_id": ""},
    "no_spec": {},
    "extra_key": {"spec_id": "img", "size": "large"},
    "string": "img",
}.items():
    try:
        validate_agent_profile({"name": "bad", "llm_profile_ref": "fast", "sandbox": sandbox})
        refused[label] = "accepted"
    except ValidationError as exc:
        refused[label] = exc.errors()[0]["type"]
out["refused"] = refused

agents = AgentProfileStore()
agents.save(OpenHandsAgentProfile(name="qa-sbx", llm_profile_ref="fast", sandbox=ProfileSandboxSettings(spec_id=SPEC)))
loaded = agents.load("qa-sbx")
out["store"] = {
    "spec_id": loaded.sandbox.spec_id if loaded.sandbox else None,
    "on_disk_spec": (agents.base_dir / "qa-sbx.json").read_text().count(SPEC),
}

llms = LLMProfileStore()
llms.save("fast", LLM(model="openai/fast-model", api_key=SecretStr("sk-fast"), usage_id="agent"), include_secrets=True)
stores = LaunchStores(llm_profiles=llms, mcp_config={}, skills=lambda: [])


def resolved_dump(profile: OpenHandsAgentProfile) -> dict:
    settings = resolve(profile, stores).settings.model_dump(mode="json")
    settings["agent_context"].pop("current_datetime", None)
    return settings


plain = resolved_dump(OpenHandsAgentProfile(name="plain", llm_profile_ref="fast"))
with_sandbox = resolved_dump(
    OpenHandsAgentProfile(name="plain", llm_profile_ref="fast", sandbox=ProfileSandboxSettings(spec_id=SPEC))
)
out["resolve"] = {
    "same_settings": plain == with_sandbox,
    "settings_mention_spec": SPEC in str(with_sandbox),
}
emit(out)
