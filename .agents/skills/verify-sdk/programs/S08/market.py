"""Marketplaces: manifests, the registry, auto-loading and load_plugin().

Usage: market.py MODE

  manifest   Marketplace.load from `.plugin/` and `.claude-plugin/`, entry
             sources of every kind resolved by resolve_plugin_source, a
             directory with no manifest, and an invalid github entry.
  registry   MarketplaceRegistry over local and git marketplaces: duplicate
             names, name@market and bare names, an ambiguous name, unknown
             plugin and marketplace, the lazy manifest cache and the
             resolved ref.
  auto-load  a conversation whose AgentContext registers four marketplaces
             (auto_load True, a list, False, and one that cannot be fetched)
             plus an explicit plugin of an auto-loaded plugin's name.
  runtime    conversation.load_plugin("late@market") between two turns: the
             plugin's skill, MCP tools and PostToolUse hook arrive; then a
             conversation with no marketplace and an unknown plugin.
  imports    the old openhands.sdk.plugin marketplace names.
"""

import sys
from pathlib import Path

from build import MCP_SERVER, git_repo, head, skill_md, write
from recorder import calls, recording_llm
from sdk_verify import emit, run_paths

from openhands.sdk import Agent, AgentContext, Conversation, Tool
from openhands.sdk.event import ObservationEvent
from openhands.sdk.marketplace import (
    AmbiguousPluginError,
    Marketplace,
    MarketplaceNotFoundError,
    MarketplaceRegistration,
    MarketplaceRegistry,
    PluginNotFoundError,
)
from openhands.sdk.plugin import PluginSource
from openhands.tools.terminal import TerminalTool


mode = sys.argv[1]
paths = run_paths()
run, workspace = paths["run"], paths["workspace"]
out = {"ok": True, "mode": mode}


def plugin(root: Path, name: str, skill: str, body: str) -> Path:
    write(root / ".plugin" / "plugin.json", {"name": name, "version": "1.0.0"})
    write(root / "skills" / skill / "SKILL.md", skill_md(skill, body))
    return root


def market(
    root: Path,
    name: str,
    plugins: list,
    skills: list | None = None,
    manifest_dir: str = ".plugin",
    **extra,
) -> Path:
    write(
        root / manifest_dir / "marketplace.json",
        {
            "name": name,
            "owner": {"name": "Verify Team", "email": "v@example.com"},
            "plugins": plugins,
            "skills": skills or [],
            **extra,
        },
    )
    return root


def call(tool, **args):
    return {"tool": tool, "args": args}


def skill_bodies(conv) -> dict:
    ctx = conv.agent.agent_context
    return {s.name: s.content.strip() for s in (ctx.skills if ctx else [])}


if mode == "manifest":
    m1 = market(
        run / "m1",
        "m-one",
        [
            {
                "name": "rel",
                "source": "./plugins/rel",
                "description": "Relative.",
                "version": "1.2.0",
                "author": "Someone",
                "category": "dev",
            },
            {
                "name": "gh",
                "source": {
                    "source": "github",
                    "repo": "owner/repo",
                    "ref": "v2",
                    "path": "plugins/gh",
                },
            },
            {
                "name": "url",
                "source": {
                    "source": "url",
                    "url": "https://git.example/x.git",
                    "ref": "main",
                },
            },
            {"name": "abs", "source": "/opt/plugins/abs"},
        ],
        skills=[{"name": "solo", "source": "./skills/solo", "description": "A skill."}],
        metadata={"version": "3.0.0", "description": "From metadata."},
    )
    mp = Marketplace.load(m1)
    out["name"] = mp.name
    out["owner"] = mp.owner.name
    out["metadata_version"] = mp.metadata.version if mp.metadata else None
    out["plugins"] = [p.name for p in mp.plugins]
    out["skills"] = [s.name for s in mp.skills]
    out["path"] = mp.path == str(m1.resolve())
    resolved = {p.name: list(mp.resolve_plugin_source(p)) for p in mp.plugins}
    out["rel"] = resolved["rel"][0] == str(
        m1.resolve() / "plugins" / "rel"
    ) and resolved["rel"][1:] == [None, None]
    out["gh"] = resolved["gh"]
    out["url"] = resolved["url"]
    out["abs"] = resolved["abs"]
    out["rel_entry_version"] = mp.get_plugin("rel").version
    out["missing_entry"] = mp.get_plugin("nope")
    m2 = market(
        run / "m2",
        "m-two",
        [{"name": "cc", "source": "./cc"}],
        manifest_dir=".claude-plugin",
    )
    out["claude_plugin_dir_name"] = Marketplace.load(m2).name
    (run / "empty").mkdir()
    try:
        Marketplace.load(run / "empty")
        out["no_manifest_error"] = None
    except FileNotFoundError as exc:
        out["no_manifest_error"] = str(exc)
    market(run / "bad", "bad", [{"name": "g", "source": {"source": "github"}}])
    try:
        Marketplace.load(run / "bad")
        out["bad_github_error"] = None
    except ValueError as exc:
        out["bad_github_error"] = str(exc)

elif mode == "registry":
    a = market(
        run / "a",
        "a",
        [
            {"name": "alpha", "source": "./alpha"},
            {"name": "shared", "source": "./shared"},
        ],
    )
    b = market(
        run / "b",
        "b",
        [
            {"name": "beta", "source": "./beta"},
            {"name": "shared", "source": "./shared"},
        ],
    )
    try:
        MarketplaceRegistry(
            [
                MarketplaceRegistration(name="a", source=str(a)),
                MarketplaceRegistration(name="a", source=str(b)),
            ]
        )
        out["duplicate_error"] = None
    except ValueError as exc:
        out["duplicate_error"] = str(exc)
    reg = MarketplaceRegistry(
        [
            MarketplaceRegistration(name="a", source=str(a)),
            MarketplaceRegistration(name="b", source=str(b)),
        ]
    )
    s = reg.resolve_plugin("alpha@a")
    out["alpha_at_a"] = [s.source == str(a.resolve() / "alpha"), s.ref, s.repo_path]
    out["beta_bare"] = reg.resolve_plugin("beta").source == str(b.resolve() / "beta")
    out["shared_at_b"] = reg.resolve_plugin("shared@b").source == str(
        b.resolve() / "shared"
    )
    for ref, key in (
        ("shared", "ambiguous"),
        ("nope", "not_found"),
        ("nope@a", "not_found_in_a"),
        ("alpha@zzz", "unknown_market"),
    ):
        try:
            reg.resolve_plugin(ref)
            out[key] = None
        except (
            AmbiguousPluginError,
            PluginNotFoundError,
            MarketplaceNotFoundError,
        ) as exc:
            out[key] = type(exc).__name__
            out[key + "_message"] = str(exc)
    out["list_all"] = sorted(reg.list_plugins())
    # The manifest is fetched once and cached.
    write(
        a / ".plugin" / "marketplace.json",
        {"name": "a", "owner": {"name": "x"}, "plugins": []},
    )
    out["cached_plugins_after_edit"] = reg.list_plugins("a")
    out["fresh_registry_plugins"] = MarketplaceRegistry(
        [MarketplaceRegistration(name="a", source=str(a))]
    ).list_plugins("a")
    # Git-hosted marketplace pinned to a tag.
    g = market(run / "g", "g", [{"name": "gp", "source": "./gp"}])
    url = git_repo(g, tag="v1")
    fetched = MarketplaceRegistry(
        [MarketplaceRegistration(name="g", source=url, ref="v1")]
    ).get_marketplace_with_resolution("g")
    out["git_resolved_ref_is_sha"] = fetched.resolved_ref == head(g)
    out["git_plugin_source_in_cache"] = fetched.marketplace.resolve_plugin_source(
        fetched.marketplace.plugins[0]
    )[0].startswith(str(fetched.path))
    try:
        MarketplaceRegistration(name="x", source=url, repo_path="../up")
        out["repo_path_traversal_error"] = None
    except ValueError:
        out["repo_path_traversal_error"] = "ValueError"

elif mode == "auto-load":
    m_all = market(
        run / "m-all",
        "m-all",
        [
            {"name": "alpha", "source": "./alpha"},
            {"name": "dupper", "source": "./dupper"},
        ],
        skills=[
            {"name": "solo", "source": "./skills/solo"},
            {"name": "dup-skill", "source": "./skills/dup-skill"},
        ],
    )
    plugin(m_all / "alpha", "alpha", "alpha-skill", "ALPHA-FROM-MARKET")
    plugin(m_all / "dupper", "dupper", "dup-skill", "DUP-FROM-PLUGIN")
    write(m_all / "skills" / "solo" / "SKILL.md", skill_md("solo", "SOLO-STANDALONE"))
    write(
        m_all / "skills" / "dup-skill" / "SKILL.md",
        skill_md("dup-skill", "DUP-FROM-STANDALONE"),
    )
    m_some = market(
        run / "m-some",
        "m-some",
        [{"name": "beta", "source": "./beta"}, {"name": "gamma", "source": "./gamma"}],
        skills=[
            {"name": "picked", "source": "./skills/picked"},
            {"name": "unpicked", "source": "./skills/unpicked"},
        ],
    )
    plugin(m_some / "beta", "beta", "beta-skill", "BETA")
    plugin(m_some / "gamma", "gamma", "gamma-skill", "GAMMA")
    write(m_some / "skills" / "picked" / "SKILL.md", skill_md("picked", "PICKED"))
    write(m_some / "skills" / "unpicked" / "SKILL.md", skill_md("unpicked", "UNPICKED"))
    m_none = market(
        run / "m-none",
        "m-none",
        [{"name": "delta", "source": "./delta"}],
        skills=[{"name": "quiet", "source": "./skills/quiet"}],
    )
    plugin(m_none / "delta", "delta", "delta-skill", "DELTA")
    write(m_none / "skills" / "quiet" / "SKILL.md", skill_md("quiet", "QUIET"))
    explicit = plugin(run / "explicit-alpha", "alpha", "alpha-skill", "ALPHA-EXPLICIT")
    ctx = AgentContext(
        registered_marketplaces=[
            MarketplaceRegistration(
                name="broken", source=f"file://{run}/no-such-market", auto_load=True
            ),
            MarketplaceRegistration(name="m-all", source=str(m_all), auto_load=True),
            MarketplaceRegistration(
                name="m-some", source=str(m_some), auto_load=["beta", "picked"]
            ),
            MarketplaceRegistration(name="m-none", source=str(m_none)),
        ]
    )
    agent = Agent(llm=recording_llm([{"finish": "done"}]), tools=[], agent_context=ctx)
    conv = Conversation(
        agent=agent,
        workspace=str(workspace),
        visualizer=None,
        plugins=[PluginSource(source=str(explicit))],
    )
    out["skills_before"] = sorted(skill_bodies(conv))
    conv.send_message("hi")
    conv.run()
    bodies = skill_bodies(conv)
    out["skills"] = sorted(bodies)
    out["alpha_body"] = bodies.get("alpha-skill")
    out["dup_body"] = bodies.get("dup-skill")
    out["solo_body"] = bodies.get("solo")
    out["status"] = conv.state.execution_status.value
    conv.close()
    # auto_load False alone loads nothing.
    agent = Agent(
        llm=recording_llm([{"finish": "done"}]),
        tools=[],
        agent_context=AgentContext(
            registered_marketplaces=[
                MarketplaceRegistration(name="m-none", source=str(m_none))
            ]
        ),
    )
    conv = Conversation(agent=agent, workspace=str(workspace), visualizer=None)
    conv.send_message("hi")
    out["registered_only_skills"] = sorted(skill_bodies(conv))
    conv.close()

elif mode == "runtime":
    log = run / "hooks.log"
    m = market(run / "market", "shop", [{"name": "late", "source": "./late"}])
    late = plugin(m / "late", "late", "late-skill", "LATE-SKILL-BODY")
    write(
        late / "hooks" / "hooks.json",
        {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "echo plugin-late:$OPENHANDS_TOOL_NAME"
                                f" >> {log}",
                            }
                        ],
                    }
                ]
            }
        },
    )
    write(
        late / ".mcp.json",
        {
            "mcpServers": {
                "late-mcp": {"command": sys.executable, "args": [MCP_SERVER, "stdio"]}
            }
        },
    )
    ctx = AgentContext(
        registered_marketplaces=[MarketplaceRegistration(name="shop", source=str(m))]
    )
    script = [
        call("terminal", command="echo first"),
        {"finish": "first done"},
        call("echo", text="loaded at runtime"),
        call("terminal", command="echo second"),
        {"finish": "second done"},
    ]
    agent = Agent(
        llm=recording_llm(script),
        tools=[Tool(name=TerminalTool.name)],
        agent_context=ctx,
    )
    conv = Conversation(agent=agent, workspace=str(workspace), visualizer=None)
    conv.send_message("first")
    conv.run()
    terminal_before = conv.agent.tools_map.get("terminal")
    out["skills_before"] = sorted(skill_bodies(conv))
    out["hook_log_before"] = log.read_text().split() if log.exists() else []
    conv.load_plugin("late@shop")
    out["skills_after"] = sorted(skill_bodies(conv))
    out["terminal_same_object"] = (
        conv.agent.tools_map.get("terminal") is terminal_before
    )
    out["resolved_plugins"] = [Path(r.source).name for r in conv.resolved_plugins or []]
    conv.send_message("second")
    conv.run()
    agent_calls = calls("agent")
    out["tools_first_call"] = sorted(agent_calls[0]["tools"])
    out["tools_last_call"] = sorted(agent_calls[-1]["tools"])
    out["observations"] = [
        e.observation.text[-60:]
        for e in conv.state.events
        if isinstance(e, ObservationEvent)
    ]
    out["hook_log"] = log.read_text().split() if log.exists() else []
    out["status"] = conv.state.execution_status.value
    try:
        conv.load_plugin("missing@shop")
        out["unknown_plugin_error"] = None
    except PluginNotFoundError as exc:
        out["unknown_plugin_error"] = type(exc).__name__
    conv.close()
    for key, context in (
        ("no_context_error", None),
        ("no_market_error", AgentContext()),
    ):
        bare = Conversation(
            agent=Agent(
                llm=recording_llm([{"finish": "x"}]), tools=[], agent_context=context
            ),
            workspace=str(workspace),
            visualizer=None,
        )
        try:
            bare.load_plugin("late@shop")
            out[key] = None
        except ValueError as exc:
            out[key] = str(exc)
        bare.close()

elif mode == "imports":
    import openhands.sdk.plugin as plugin_pkg
    import openhands.sdk.plugin.types as plugin_types

    for owner, name in (
        (plugin_pkg, "Marketplace"),
        (plugin_pkg, "MarketplaceRegistry"),
        (plugin_types, "MarketplaceOwner"),
        (plugin_types, "MarketplacePluginEntry"),
    ):
        try:
            getattr(owner, name)
            out[f"{owner.__name__.split('.')[-1]}_{name}"] = "present"
        except AttributeError:
            out[f"{owner.__name__.split('.')[-1]}_{name}"] = "AttributeError"
    try:
        exec("from openhands.sdk.plugin import Marketplace")
        out["from_import"] = "present"
    except ImportError:
        out["from_import"] = "ImportError"
    import openhands.sdk.marketplace as mk

    out["canonical"] = sorted(
        n
        for n in (
            "Marketplace",
            "MarketplaceRegistry",
            "MarketplaceRegistration",
            "MarketplaceOwner",
        )
        if getattr(mk, n, None) is not None
    )

emit(out)
