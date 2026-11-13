"""Write plugin directories, git repositories and marketplaces on disk, the
way a plugin author lays them out. Shared by the S08 programs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


MCP_SERVER = str(Path(__file__).resolve().parent.parent / "S09" / "mcp_server.py")


def write(path: Path, content: str | dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        content if isinstance(content, str) else json.dumps(content, indent=2)
    )
    return path


def skill_md(name: str, body: str, **extra: str) -> str:
    lines = [f"name: {name}", f"description: {name} skill."]
    lines += [f"{k}: {v}" for k, v in extra.items()]
    return "---\n" + "\n".join(lines) + f"\n---\n{body}\n"


def hook_writer(log: Path, label: str) -> dict:
    """A PostToolUse hook that appends `label` to `log`."""
    return {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": f"echo {label} >> {log}"}],
                }
            ]
        }
    }


def claude_code_plugin(
    root: Path,
    name: str = "cc-plugin",
    *,
    hook_log: Path | None = None,
    mcp_env: dict | None = None,
    manifest_dir: str = ".claude-plugin",
    author: str | dict = "Plugin Author",
) -> Path:
    """Claude Code layout: manifest, skills (one broken), commands, agents,
    hooks and a .mcp.json starting the local MCP server."""
    write(
        root / manifest_dir / "plugin.json",
        {
            "name": name,
            "version": "2.0.0",
            "description": f"{name} plugin",
            "author": author,
        },
    )
    write(
        root / "skills" / f"{name}-skill" / "SKILL.md",
        skill_md(f"{name}-skill", f"{name.upper()}-SKILL-BODY"),
    )
    write(
        root / "skills" / "broken" / "SKILL.md", "---\nname: [unclosed\n---\nBROKEN\n"
    )
    write(
        root / "commands" / "review.md",
        "---\ndescription: Review the change.\nargument-hint: <path>\n---\n"
        "REVIEW-COMMAND-BODY for $ARGUMENTS\n",
    )
    write(
        root / "agents" / f"{name}-helper.md",
        f"---\nname: {name}-helper\ndescription: Helps with {name}.\n---\n"
        "HELPER-PROMPT\n",
    )
    if hook_log is not None:
        write(root / "hooks" / "hooks.json", hook_writer(hook_log, f"plugin-{name}"))
    write(
        root / ".mcp.json",
        {
            "mcpServers": {
                f"{name}-mcp": {
                    "command": sys.executable,
                    "args": [MCP_SERVER, "stdio"],
                    "env": mcp_env or {},
                }
            }
        },
    )
    return root


def agent_plugins_package(root: Path, name: str = "portable") -> Path:
    """Agent Plugins v1.0.0 layout with OpenHands extensions."""
    write(
        root / "plugin.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": name,
            "version": "1.0.0",
            "description": "portable package",
            "author": {"name": "verify-sdk"},
            "extensions": {"dev.openhands": {"entry_command": "report"}},
        },
    )
    write(
        root / "skills" / "summarize" / "SKILL.md",
        skill_md("summarize", "SUMMARIZE-BODY"),
    )
    write(
        root / "dev.openhands" / "commands" / "report.md",
        "---\ndescription: Report.\n---\nREPORT-BODY\n",
    )
    write(
        root / "dev.openhands" / "agents" / "portable-helper.md",
        "---\nname: portable-helper\ndescription: Portable helper.\n---\n"
        "PORTABLE-HELPER\n",
    )
    # Components outside dev.openhands/ are not honored in this layout.
    write(
        root / "commands" / "ignored.md", "---\ndescription: Ignored.\n---\nIGNORED\n"
    )
    serve = write(
        root / "bin" / "serve.sh",
        f'#!/bin/sh\nexec {sys.executable} {MCP_SERVER} stdio "$@"\n',
    )
    serve.chmod(0o755)
    write(
        root / "mcp.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
            "mcpServers": {
                "tools": {
                    "type": "stdio",
                    "command": "./bin/serve.sh",
                    "args": ["--name", "portable"],
                    "env": {
                        "ROOT": "${PLUGIN_ROOT}",
                        "DATA": "${PLUGIN_DATA}",
                        "LITERAL": "${TOKEN}",
                    },
                },
                "absolute": {
                    "type": "stdio",
                    "command": sys.executable,
                    "args": [MCP_SERVER, "stdio"],
                },
                "insecure": {
                    "type": "streamable-http",
                    "url": "http://example.com/mcp",
                },
                "local-http": {
                    "type": "streamable-http",
                    "url": "http://127.0.0.1:9/mcp",
                    "headers": {
                        "Host": "spoofed.example.com",
                        "X-Root": "${PLUGIN_ROOT}",
                    },
                },
            },
        },
    )
    return root


def git_repo(root: Path, *, tag: str | None = None) -> str:
    """Make `root` a git repository with one commit; return its file:// URL."""

    def git(*args: str) -> None:
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=v",
                "-c",
                "user.email=v@x",
                *args,
            ],
            check=True,
            capture_output=True,
        )

    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    git("add", "-A")
    git("commit", "-qm", "first")
    if tag:
        git("tag", tag)
    return f"file://{root}"


def commit(root: Path, message: str) -> str:
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=v",
            "-c",
            "user.email=v@x",
            "add",
            "-A",
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=v",
            "-c",
            "user.email=v@x",
            "commit",
            "-qm",
            message,
        ],
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def head(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
