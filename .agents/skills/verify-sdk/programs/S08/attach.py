"""Plugins attached to a conversation, plus ambient plugins on disk.

Usage: attach.py MODE

  conversation  Conversation(plugins=[...]) with a local Claude Code plugin
                (skill, command, agent, PostToolUse hook, .mcp.json with
                ${TOKEN}), a git-hosted plugin whose source is the secret
                ${GITP_URL} (ref v1, repo_path), and an Agent Plugins
                package; an explicit hook_config; ambient plugins in
                ~/.agents/plugins and <workspace>/.agents/plugins, one with
                the explicit plugin's name. A scripted agent runs a terminal
                command and calls the plugins' MCP tools.
  bad-source    a plugin source that cannot be fetched.
  fetch         fetch_plugin on a git repository: cache, refs, repo_path,
                update=False, a missing subdirectory, a bad URL, a local path.
  fetch-ref     a tag requested after the repository was already cached
                from its default branch.
"""

import os
import sys
from pathlib import Path

from build import (
    agent_plugins_package,
    claude_code_plugin,
    commit,
    git_repo,
    head,
    skill_md,
    write,
)
from recorder import calls, recording_llm, text
from sdk_verify import emit, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.event import ObservationEvent
from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher
from openhands.sdk.plugin import PluginFetchError, PluginSource
from openhands.sdk.plugin.fetch import fetch_plugin
from openhands.tools.terminal import TerminalTool


mode = sys.argv[1]
paths = run_paths()
run, workspace, home = paths["run"], paths["workspace"], paths["home"]
persist = Path(os.environ["OH_PERSISTENCE_DIR"])
out = {"ok": True, "mode": mode}


def call(tool, **args):
    return {"tool": tool, "args": args}


if mode == "conversation":
    log = run / "hooks.log"
    os.environ["TOKEN"] = "host-token"  # the host value must not be used
    cc = claude_code_plugin(
        run / "src" / "cc-plugin",
        hook_log=log,
        mcp_env={
            "TOKEN": "${TOKEN}",
            "KEY": "${PLUGIN_KEY}",
            "DFLT": "${NOPE:-fallback}",
        },
    )
    monorepo = run / "src" / "monorepo"
    write(
        monorepo / "plugins" / "gitp" / ".claude-plugin" / "plugin.json",
        {"name": "gitp", "version": "1.0.0"},
    )
    write(
        monorepo / "plugins" / "gitp" / "skills" / "gitp-skill" / "SKILL.md",
        skill_md("gitp-skill", "GITP-V1"),
    )
    url = git_repo(monorepo, tag="v1")
    v1_sha = head(monorepo)
    write(
        monorepo / "plugins" / "gitp" / "skills" / "gitp-skill" / "SKILL.md",
        skill_md("gitp-skill", "GITP-V2"),
    )
    commit(monorepo, "v2")
    portable = agent_plugins_package(run / "src" / "portable")
    # Ambient plugins: user and project copies of `amb`, and a user plugin
    # that has the explicit plugin's name.
    for where, body in (
        (home / ".agents" / "plugins", "USER-AMB"),
        (workspace / ".agents" / "plugins", "PROJECT-AMB"),
    ):
        write(where / "amb" / ".claude-plugin" / "plugin.json", {"name": "amb"})
        write(
            where / "amb" / "skills" / "amb-skill" / "SKILL.md",
            skill_md("amb-skill", body),
        )
    write(
        home / ".agents" / "plugins" / "shadow" / ".claude-plugin" / "plugin.json",
        {"name": "cc-plugin"},
    )
    write(
        home
        / ".agents"
        / "plugins"
        / "shadow"
        / "skills"
        / "cc-plugin-skill"
        / "SKILL.md",
        skill_md("cc-plugin-skill", "AMBIENT-SHADOW"),
    )

    script = [
        call("terminal", command="echo hi"),
        call("cc-plugin-mcp_getenv", var="TOKEN", reverse=True),
        call("cc-plugin-mcp_getenv", var="KEY", reverse=True),
        call("cc-plugin-mcp_getenv", var="DFLT"),
        call("tools_getenv", var="LITERAL"),
        call("tools_getenv", var="ROOT"),
        {"finish": "done"},
    ]
    agent = Agent(llm=recording_llm(script), tools=[Tool(name=TerminalTool.name)])
    explicit = HookConfig(
        post_tool_use=[
            HookMatcher(
                matcher="*", hooks=[HookDefinition(command=f"echo explicit >> {log}")]
            )
        ]
    )
    conversation = Conversation(
        agent=agent,
        workspace=str(workspace),
        plugins=[
            PluginSource(source=str(cc)),
            PluginSource(source="${GITP_URL}", ref="v1", repo_path="plugins/gitp"),
            PluginSource(source=str(portable)),
        ],
        hook_config=explicit,
        secrets={"TOKEN": "secret-token", "PLUGIN_KEY": "plugin-key", "GITP_URL": url},
        visualizer=None,
    )
    out["resolved_before"] = conversation.resolved_plugins
    out["skills_before"] = sorted(
        s.name
        for s in (
            conversation.agent.agent_context.skills
            if conversation.agent.agent_context
            else []
        )
    )
    conversation.send_message("/cc-plugin:review src/app.py please")
    conversation.run()
    skills = {
        s.name: (s.content or "").strip()
        for s in conversation.agent.agent_context.skills
    }
    out["skills_after"] = sorted(skills)
    out["cc_skill_body"] = skills.get("cc-plugin-skill")
    out["gitp_skill_body"] = skills.get("gitp-skill")
    out["amb_skill_body"] = skills.get("amb-skill")
    out["resolved"] = [
        {
            "source": r.source,
            "resolved_ref": r.resolved_ref,
            "original_ref": r.original_ref,
            "repo_path": r.repo_path,
        }
        for r in conversation.resolved_plugins or []
    ]
    out["git_ref_is_v1"] = any(r["resolved_ref"] == v1_sha for r in out["resolved"])
    out["resolved_has_secret_value"] = any(
        url in (r["source"] or "") for r in out["resolved"]
    )
    out["resolved_names_ambient"] = any(
        "amb" in (r["source"] or "") for r in out["resolved"]
    )
    first = calls("agent")[0]
    out["first_tools"] = sorted(first["tools"])
    user_text = text([m for m in first["messages"] if m.role == "user"][-1])
    out["command_injected"] = "REVIEW-COMMAND-BODY" in user_text
    out["observations"] = [
        e.observation.text[-120:]
        for e in conversation.state.events
        if isinstance(e, ObservationEvent)
    ]
    from openhands.sdk.subagent.registry import get_registered_agent_definitions

    out["registered_agents"] = sorted(
        a.name
        for a in get_registered_agent_definitions()
        if a.name in ("cc-plugin-helper", "portable-helper")
    )
    out["hook_log"] = log.read_text().split() if log.exists() else []
    conversation.close()

elif mode == "bad-source":
    agent = Agent(llm=recording_llm([{"finish": "done"}]), tools=[])
    try:
        conversation = Conversation(
            agent=agent,
            workspace=str(workspace),
            visualizer=None,
            plugins=[PluginSource(source=f"file://{run}/no-such-repo")],
        )
        out["constructed"] = True
    except Exception as exc:
        out["constructed"] = False
        out["construct_error"] = type(exc).__name__
    else:
        try:
            conversation.send_message("hi")
            out["send_error"] = None
        except Exception as exc:
            out["send_error"] = type(exc).__name__
        conversation.close()

elif mode == "fetch":
    repo = run / "src" / "fetchable"
    write(repo / "plugins" / "one" / ".claude-plugin" / "plugin.json", {"name": "one"})
    write(repo / "plugins" / "one" / "VERSION", "v1")
    url = git_repo(repo, tag="v1")
    v1_sha = head(repo)
    cache = persist / "cache" / "plugins"
    # A first fetch pinned to a tag.
    pinned = fetch_plugin(url, ref="v1", repo_path="plugins/one")
    out["cached_under_persistence"] = str(pinned).startswith(str(cache))
    out["cache_dir_name"] = pinned.parent.parent.name
    out["v1_version"] = (pinned / "VERSION").read_text()
    # A second repository fetched from its default branch.
    repo2 = run / "src" / "moving"
    write(repo2 / "plugins" / "one" / ".claude-plugin" / "plugin.json", {"name": "one"})
    write(repo2 / "plugins" / "one" / "VERSION", "v1")
    url2 = git_repo(repo2, tag="v1")
    sub = fetch_plugin(url2, repo_path="plugins/one")
    root2 = sub.parent.parent
    out["default_version"] = (sub / "VERSION").read_text()
    out["same_cache_dir"] = fetch_plugin(url2).resolve() == root2.resolve()
    out["distinct_cache_per_url"] = root2.resolve() != pinned.parent.parent.resolve()
    write(repo2 / "plugins" / "one" / "VERSION", "v2")
    commit(repo2, "v2")
    out["no_update_version"] = (
        fetch_plugin(url2, update=False, repo_path="plugins/one") / "VERSION"
    ).read_text()
    out["update_version"] = (
        fetch_plugin(url2, repo_path="plugins/one") / "VERSION"
    ).read_text()
    try:
        fetch_plugin(url2, repo_path="plugins/missing")
        out["missing_repo_path_error"] = None
    except PluginFetchError:
        out["missing_repo_path_error"] = "PluginFetchError"
    try:
        fetch_plugin(f"file://{run}/nowhere")
        out["bad_url_error"] = None
    except PluginFetchError:
        out["bad_url_error"] = "PluginFetchError"
    local = run / "src" / "local-plugin"
    local.mkdir(parents=True, exist_ok=True)
    out["local_returned_as_is"] = fetch_plugin(str(local)) == local

elif mode == "fetch-ref":
    repo = run / "src" / "pinned"
    write(repo / ".claude-plugin" / "plugin.json", {"name": "pinned"})
    write(repo / "VERSION", "v1")
    url = git_repo(repo, tag="v1")
    v1_sha = head(repo)
    write(repo / "VERSION", "v2")
    commit(repo, "v2")
    from openhands.sdk.plugin import fetch_plugin_with_resolution

    first, first_sha = fetch_plugin_with_resolution(url)
    out["default_version"] = (first / "VERSION").read_text()
    again, again_sha = fetch_plugin_with_resolution(url, ref="v1")
    out["v1_after_cached_clone"] = (again / "VERSION").read_text()
    out["v1_resolved_is_v1_sha"] = again_sha == v1_sha

emit(out)
