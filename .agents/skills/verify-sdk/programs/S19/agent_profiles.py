"""Agent profiles the way a launcher uses them: save named launch specs in
the user's store (OH_PERSISTENCE_DIR, the run's private ~/.openhands), resolve
one against the saved LLM profile, the user's MCP servers and skills, rename
and delete the LLM profile it cites, seed a default from settings, and launch
the resolved agent in a scripted conversation.
"""

import json
import os
import threading
import warnings
from pathlib import Path

from pydantic import SecretStr
from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import LLM, Conversation, OpenHandsAgentSettings, Tool
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.profiles import (
    AGENT_PROFILE_SCHEMA_VERSION,
    ACPAgentProfile,
    AgentProfileStore,
    DanglingMcpServerRef,
    OpenHandsAgentProfile,
    ProfileLimitExceeded,
    ProfileNotFound,
    ProfileReferenced,
    build_seed_profile,
    delete_llm_profile,
    find_referrers,
    rename_llm_profile,
    resolve_agent_profile,
    resolve_agent_profile_dry_run,
    save_profile_preserving_identity,
    validate_agent_profile,
)
from openhands.sdk.skills import Skill
from openhands.tools.terminal import TerminalTool  # noqa: F401  (registers the tool)


out: dict = {"ok": True, "schema_version": AGENT_PROFILE_SCHEMA_VERSION}


def section(name):
    def wrap(fn):
        try:
            out[name] = fn()
        except Exception as exc:
            out[f"{name}_error"] = f"{type(exc).__name__}: {exc}"[:300]
        return fn

    return wrap


home = Path(os.environ["OH_PERSISTENCE_DIR"])
agents = AgentProfileStore()
llms = LLMProfileStore()
llms.save(
    "fast",
    LLM(model="openai/fast-model", api_key=SecretStr("sk-fast"), usage_id="agent"),
    include_secrets=True,
)
MCP = {
    "github": MCPServer(command="uvx", args=["mcp-server-github"]),
    "fetch": MCPServer(command="uvx", args=["mcp-server-fetch"]),
}
SKILLS = [
    Skill(name="deploy", content="How to deploy."),
    Skill(name="style", content="Style."),
]


@section("model")
def _():
    oh = OpenHandsAgentProfile(name="coder", llm_profile_ref="fast")
    errors = {}
    for label, build in {
        "missing_llm_ref": lambda: OpenHandsAgentProfile(name="x"),
        "acp_unknown_server": lambda: ACPAgentProfile(name="x", acp_server="nope"),
        "acp_with_llm_ref": lambda: ACPAgentProfile(
            name="x", acp_server="codex", llm_profile_ref="fast"
        ),
        "acp_with_persona": lambda: ACPAgentProfile(
            name="x", acp_server="codex", persona="p"
        ),
    }.items():
        try:
            build()
            errors[label] = None
        except Exception as exc:
            errors[label] = type(exc).__name__
    acp = ACPAgentProfile(
        name="claude", acp_server="claude-code", secret_refs=["ANTHROPIC_API_KEY"]
    )
    dumped = oh.model_dump(mode="json")
    return {
        "id_is_uuid": len(str(oh.id)) == 36,
        "revision": oh.revision,
        "mcp_refs_default": oh.mcp_server_refs,
        "acp_secret_refs": acp.secret_refs,
        "errors": errors,
        "dump_keys_have_secret": any("key" in k and "ref" not in k for k in dumped),
    }


@section("migrations")
def _():
    v1 = validate_agent_profile(
        {
            "schema_version": 1,
            "agent_kind": "openhands",
            "name": "old",
            "llm_profile_ref": "fast",
            "tools": [],
        }
    )
    v2 = validate_agent_profile(
        {
            "schema_version": 2,
            "agent_kind": "openhands",
            "name": "old2",
            "llm_profile_ref": "fast",
            "enable_sub_agents": True,
        }
    )
    rejected = {}
    for v in (AGENT_PROFILE_SCHEMA_VERSION + 1, -1, "three"):
        try:
            validate_agent_profile(
                {
                    "schema_version": v,
                    "agent_kind": "openhands",
                    "name": "n",
                    "llm_profile_ref": "fast",
                }
            )
            rejected[str(v)] = False
        except Exception:
            rejected[str(v)] = True
    return {
        "v1_tools": v1.tools,
        "v1_version": v1.schema_version,
        "v2_tools": [t.name for t in v2.tools or []],
        "rejected": rejected,
    }


@section("store")
def _():
    p = OpenHandsAgentProfile(name="coder", llm_profile_ref="fast", persona="Pirate")
    agents.save(p)
    path = home / "agent-profiles" / "coder.json"
    on_disk = json.loads(path.read_text()) if path.exists() else {}
    loaded = agents.load("coder")
    try:
        agents.load("nobody")
        miss = None
    except FileNotFoundError as exc:
        miss = str(exc)
    agents.save(OpenHandsAgentProfile(name="tester", llm_profile_ref="fast"))
    tester_id = agents.load("tester").id
    agents.rename("tester", "qa")
    renamed = agents.load("qa")
    (home / "agent-profiles" / "broken.json").write_text("{not json")
    summaries = agents.list_summaries()
    try:
        agents.save(
            OpenHandsAgentProfile(name="third", llm_profile_ref="fast"), max_profiles=2
        )
        limit = None
    except ProfileLimitExceeded:
        limit = "ProfileLimitExceeded"
    (home / "agent-profiles" / "broken.json").unlink()

    # Concurrent identity-preserving saves of one name keep one id.
    def save():
        save_profile_preserving_identity(
            agents, OpenHandsAgentProfile(name="race", llm_profile_ref="fast")
        )

    threads = [threading.Thread(target=save) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    race = agents.load("race")
    agents.delete("qa")
    return {
        "file_in_persistence_dir": path.exists(),
        "file_schema_version": on_disk.get("schema_version"),
        "file_has_id": bool(on_disk.get("id")),
        "loaded_persona": loaded.persona,
        "same_id": str(loaded.id) == on_disk.get("id"),
        "missing_names_available": miss is not None and "coder" in miss,
        "rename_kept_id": renamed.id == tester_id,
        "renamed_name": renamed.name,
        "summaries": sorted(s.get("name") for s in summaries),
        "limit": limit,
        "race_revision": race.revision,
        "list_after_delete": sorted(agents.list()),
    }


@section("resolve")
def _():
    p = OpenHandsAgentProfile(
        name="coder",
        llm_profile_ref="fast",
        mcp_server_refs=["github"],
        disabled_skills=["style"],
        persona="Pirate",
        tools=[Tool(name="terminal")],
    )
    settings = resolve_agent_profile(
        p, llm_store=llms, mcp_config=MCP, available_skills=SKILLS
    )
    all_mcp = resolve_agent_profile(
        OpenHandsAgentProfile(name="all", llm_profile_ref="fast"),
        llm_store=llms,
        mcp_config=MCP,
        available_skills=SKILLS,
    )
    none_mcp = resolve_agent_profile(
        OpenHandsAgentProfile(name="none", llm_profile_ref="fast", mcp_server_refs=[]),
        llm_store=llms,
        mcp_config=MCP,
        available_skills=SKILLS,
    )
    errors = {}
    try:
        resolve_agent_profile(
            OpenHandsAgentProfile(name="x", llm_profile_ref="ghost"),
            llm_store=llms,
            mcp_config=MCP,
            available_skills=None,
        )
    except ProfileNotFound:
        errors["missing_llm"] = "ProfileNotFound"
    try:
        resolve_agent_profile(
            OpenHandsAgentProfile(
                name="x", llm_profile_ref="fast", mcp_server_refs=["gone"]
            ),
            llm_store=llms,
            mcp_config=MCP,
            available_skills=None,
        )
    except DanglingMcpServerRef:
        errors["dangling_mcp"] = "DanglingMcpServerRef"
    dry = resolve_agent_profile_dry_run(
        OpenHandsAgentProfile(
            name="x", llm_profile_ref="ghost", mcp_server_refs=["gone"]
        ),
        llm_store=llms,
        mcp_config=MCP,
        available_skills=SKILLS,
    )
    dry_json = dry.model_dump_json()
    ok_dry = resolve_agent_profile_dry_run(
        p, llm_store=llms, mcp_config=MCP, available_skills=SKILLS
    )
    # Launch the resolved settings: the built agent runs a scripted turn.
    agent = settings.create_agent()
    agent = agent.model_copy(
        update={"llm": make_llm([{"finish": "resolved agent ran"}])}
    )
    conv = Conversation(
        agent=agent, workspace=str(run_paths()["workspace"]), visualizer=None
    )
    conv.send_message("hi")
    conv.run()
    status = str(conv.state.execution_status.value)
    conv.close()
    return {
        "type": type(settings).__name__,
        "model": settings.llm.model,
        "llm_key_loaded": settings.llm.api_key is not None,
        "mcp": sorted(settings.mcp_config),
        "all_mcp": sorted(all_mcp.mcp_config),
        "none_mcp": sorted(none_mcp.mcp_config),
        "skills": sorted(s.name for s in settings.agent_context.skills),
        "persona": settings.persona,
        "tools": [t.name for t in settings.tools or []],
        "errors": errors,
        "dry_has_secret": "sk-fast" in dry_json
        or "sk-fast" in ok_dry.model_dump_json(),
        "dry_dump": json.loads(dry_json),
        "launched_status": status,
    }


@section("refs")
def _():
    agents.save(OpenHandsAgentProfile(name="a1", llm_profile_ref="fast"))
    agents.save(OpenHandsAgentProfile(name="a2", llm_profile_ref="fast"))
    referrers = sorted(find_referrers(agents, "fast"))
    try:
        delete_llm_profile(agents, llms, "fast")
        blocked = None
    except ProfileReferenced as exc:
        blocked = str(exc)
    rewritten = sorted(rename_llm_profile(agents, llms, "fast", "quick"))
    return {
        "referrers": referrers,
        "delete_blocked_names_referrers": blocked is not None and "a1" in blocked,
        "rewritten": rewritten,
        "a1_ref": agents.load("a1").llm_profile_ref,
        "llm_profiles": sorted(llms.list()),
    }


@section("seed")
def _():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        settings = OpenHandsAgentSettings(
            llm=LLM(model="openai/x", usage_id="agent"),
            persona="Pirate",
            tools=[Tool(name="terminal")],
        )
        seed = build_seed_profile(settings, "quick")
        from openhands.sdk.settings import ACPAgentSettings

        acp_seed = build_seed_profile(ACPAgentSettings(acp_server="codex"), None)
    first = save_profile_preserving_identity(agents, seed)
    again = save_profile_preserving_identity(agents, seed)
    return {
        "name": seed.name,
        "llm_ref": seed.llm_profile_ref,
        "persona": seed.persona,
        "tools": [t.name for t in seed.tools or []],
        "disabled_skills": seed.disabled_skills,
        "mcp_refs": seed.mcp_server_refs,
        "acp_type": type(acp_seed).__name__,
        "acp_server": acp_seed.acp_server,
        "id_kept": first.id == again.id,
        "revision_bumped": again.revision == first.revision + 1,
    }


emit(out)
