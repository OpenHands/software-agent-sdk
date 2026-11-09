"""Load plugin directories in both layouts and merge them, as a program
does before (or instead of) attaching them to a conversation.

Builds under the run: a Claude Code plugin (.claude-plugin/, a broken skill,
a command, an agent, hooks, .mcp.json), the same with .plugin/ and no
manifest, a single-skill plugin, an Agent Plugins v1.0.0 package (root
plugin.json, dev.openhands/ extensions, an mcp.json with placeholders, an
insecure URL and a spoofed Host header), packages with a fatal manifest and
an invalid mcp.json, and a plugin whose files escape its root via symlinks.
"""

import os

from build import agent_plugins_package, claude_code_plugin, skill_md, write
from sdk_verify import emit, run_paths

from openhands.sdk import AgentContext
from openhands.sdk.extensions.fetch import parse_extension_source
from openhands.sdk.mcp import MCPServer
from openhands.sdk.plugin import (
    Plugin,
    PluginSource,
    detect_format,
    get_plugin_data_dir,
    parse_github_url,
)
from openhands.sdk.skills import Skill


base = run_paths()["run"] / "plugins"
out = {"ok": True}


def error_of(fn):
    try:
        fn()
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"[:200]


# Claude Code layout.
cc = Plugin.load(
    claude_code_plugin(
        base / "cc-plugin",
        author={"name": "Ann", "email": "a@x"},
        hook_log=base / "hooks.log",
    )
)
out["cc"] = {
    "format": type(detect_format(base / "cc-plugin")).__name__,
    "name": cc.name,
    "version": cc.version,
    "author": cc.manifest.author.name if cc.manifest.author else None,
    "skills": sorted(s.name for s in cc.skills),
    "commands": sorted(c.name for c in cc.commands),
    "agents": sorted(a.name for a in cc.agents),
    "hook_events": sorted(
        k for k, v in (cc.hooks.model_dump() if cc.hooks else {}).items() if v
    ),
    "mcp_servers": sorted(cc.mcp_config),
}
command_skill = next(
    (s for s in cc.get_all_skills() if s.name.startswith("/")), None
) or next((s for s in cc.get_all_skills() if "review" in s.name), None)
out["command_skill"] = {
    "name": command_skill.name if command_skill else None,
    "triggers": command_skill.get_triggers() if command_skill else None,
    "description": command_skill.description if command_skill else None,
    "has_arguments_hint": bool(command_skill)
    and "$ARGUMENTS" in (command_skill.content or ""),
}
author_string = Plugin.load(claude_code_plugin(base / "author-string", "author-string"))
out["author_as_string"] = (
    author_string.manifest.author.name if author_string.manifest.author else None
)
dotplugin = Plugin.load(
    claude_code_plugin(base / "dot-plugin", "dot-plugin", manifest_dir=".plugin")
)
out["dot_plugin_name"] = dotplugin.name
write(
    base / "no-manifest" / "skills" / "lonely" / "SKILL.md",
    skill_md("lonely", "LONELY"),
)
out["no_manifest_name"] = Plugin.load(base / "no-manifest").name
write(base / "single" / "SKILL.md", skill_md("single", "SINGLE-BODY"))
single = Plugin.load(base / "single")
out["single_skill_plugin"] = sorted(s.name for s in single.skills)

# Agent Plugins v1.0.0 package.
portable_root = agent_plugins_package(base / "portable")
portable = Plugin.load(portable_root)
tools = portable.mcp_config.get("tools")
env = {k: v.get_secret_value() for k, v in (tools.env or {}).items()} if tools else {}
local_http = portable.mcp_config.get("local-http")
out["portable"] = {
    "format": type(detect_format(portable_root)).__name__,
    "name": portable.name,
    "skills": sorted(s.name for s in portable.skills),
    "commands": sorted(c.name for c in portable.commands),
    "agents": sorted(a.name for a in portable.agents),
    "entry_slash_command": portable.entry_slash_command,
    "mcp_servers": sorted(portable.mcp_config),
    "tools_command": tools.command if tools else None,
    "root_expanded": env.get("ROOT") == str(portable_root.resolve()),
    "data_expanded": env.get("DATA") == str(get_plugin_data_dir(portable_root)),
    "data_value": env.get("DATA"),
    "literal_kept": env.get("LITERAL"),
    "local_http_headers": sorted((local_http.headers or {}) if local_http else []),
}
write(
    base / "fatal" / "plugin.json",
    {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "Fatal Name",
        "version": "1.0.0",
        "description": "bad name",
    },
)
write(base / "fatal" / "skills" / "never" / "SKILL.md", skill_md("never", "NEVER"))
out["fatal_manifest_error"] = error_of(lambda: Plugin.load(base / "fatal"))
write(
    base / "bad-mcp" / "plugin.json",
    {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "bad-mcp",
        "version": "1.0.0",
        "description": "invalid mcp.json",
    },
)
write(
    base / "bad-mcp" / "skills" / "still-here" / "SKILL.md",
    skill_md("still-here", "STILL"),
)
write(base / "bad-mcp" / "mcp.json", '{"mcpServers": 5}')
bad_mcp = Plugin.load(base / "bad-mcp")
out["bad_mcp"] = {
    "skills": sorted(s.name for s in bad_mcp.skills),
    "mcp_servers": sorted(bad_mcp.mcp_config),
}

# Path containment: symlinks out of the plugin root are refused.
outside = run_paths()["run"] / "outside"
write(outside / "escape" / "SKILL.md", skill_md("escape", "ESCAPED-BODY"))
write(
    outside / "evil.json",
    {"mcpServers": {"evil": {"command": "sh", "args": ["-c", "true"]}}},
)
contained = claude_code_plugin(base / "contained", "contained")
os.symlink(outside / "escape", contained / "skills" / "escape")
(contained / ".mcp.json").unlink()
os.symlink(outside / "evil.json", contained / ".mcp.json")
contained_plugin = Plugin.load(contained)
out["contained"] = {
    "skills": sorted(s.name for s in contained_plugin.skills),
    "mcp_servers": sorted(contained_plugin.mcp_config),
    "commands": sorted(c.name for c in contained_plugin.commands),
}

# Merging into a context and an MCP config.
ctx = AgentContext(
    skills=[
        Skill(name="cc-plugin-skill", content="MINE"),
        Skill(name="other", content="O"),
    ]
)
merged = cc.add_skills_to(ctx)
out["merge"] = {
    "names": [s.name for s in merged.skills],
    "overridden_content": next(
        s.content for s in merged.skills if s.name == "cc-plugin-skill"
    ).strip(),
    "original_untouched": ctx.skills[0].content == "MINE",
    "max_skills_error": error_of(lambda: cc.add_skills_to(ctx, max_skills=2)),
}
mcp = {"cc-plugin-mcp": MCPServer(command="old"), "keep": MCPServer(command="keep")}
merged_mcp = cc.add_mcp_config_to(mcp)
out["merge_mcp"] = {
    "names": sorted(merged_mcp),
    "overridden": merged_mcp["cc-plugin-mcp"].command != "old",
    "original_untouched": mcp["cc-plugin-mcp"].command == "old",
}
out["load_all"] = sorted(
    p.name
    for p in Plugin.load_all(base)
    if p.name in ("cc-plugin", "portable", "single")
)

# Source strings.
out["sources"] = [
    [s, *(lambda r: [str(r[0]), r[1]])(parse_extension_source(s))]
    for s in [
        "github:owner/repo",
        "https://gitlab.com/org/repo",
        "git@bitbucket.org:team/repo.git",
        "git://example.com/repo.git",
        "/abs/plugin",
        "./rel/plugin",
    ]
]
out["bad_source_error"] = error_of(lambda: parse_extension_source("github:only-owner"))
out["repo_path_absolute_error"] = (
    error_of(lambda: PluginSource(source="github:o/r", repo_path="/abs")) is not None
)
out["repo_path_dotdot_error"] = (
    error_of(lambda: PluginSource(source="github:o/r", repo_path="a/../../b"))
    is not None
)


blob = parse_github_url("https://github.com/owner/repo/blob/main/plugins/x")
out["github_blob"] = [blob.owner, blob.repo, blob.branch, blob.path] if blob else None
emit(out)
