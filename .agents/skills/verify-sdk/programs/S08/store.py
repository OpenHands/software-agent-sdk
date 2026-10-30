"""The plugin install store and the generic installation manager.

Usage: store.py MODE

  plugins   install_plugin from a git repository and a local path into the
            default store (under OH_PERSISTENCE_DIR), then list, get, disable
            (a conversation no longer sees the skill), enable, force
            reinstall (enabled flag kept), update after a new commit, and
            uninstall; .installed.json is read after each step.
  manager   InstallationManager directly: a manifest name with path
            traversal, a non-kebab name, requested/resolved refs, and an
            untracked directory picked up by list_installed.
  legacy    a .installed.json with the old `plugins` and `skills` keys.
"""

import json
import os
import sys
from pathlib import Path

from build import commit, git_repo, head, skill_md, write
from recorder import recording_llm
from sdk_verify import emit, run_paths

from openhands.sdk import Agent, Conversation
from openhands.sdk.extensions.installation import InstallationManager
from openhands.sdk.plugin import (
    disable_plugin,
    enable_plugin,
    get_installed_plugin,
    install_plugin,
    list_installed_plugins,
    load_installed_plugins,
    uninstall_plugin,
    update_plugin,
)
from openhands.sdk.plugin.installed import (
    PluginInstallationInterface,
    get_installed_plugins_dir,
)


mode = sys.argv[1]
paths = run_paths()
run, workspace = paths["run"], paths["workspace"]
persist = Path(os.environ["OH_PERSISTENCE_DIR"])
out = {"ok": True, "mode": mode}


def plugin_dir(root: Path, name: str, version: str, skill: str) -> Path:
    write(
        root / ".plugin" / "plugin.json",
        {"name": name, "version": version, "description": f"{name} plugin"},
    )
    write(
        root / "skills" / skill / "SKILL.md",
        skill_md(skill, f"Body of {skill} {version}."),
    )
    return root


def metadata(store: Path) -> dict:
    p = store / ".installed.json"
    return json.loads(p.read_text()) if p.exists() else {}


def skills_in_conversation() -> list[str]:
    agent = Agent(llm=recording_llm([{"finish": "done"}]), tools=[])
    conv = Conversation(agent=agent, workspace=str(workspace), visualizer=None)
    conv.send_message("hi")
    names = (
        sorted(s.name for s in conv.agent.agent_context.skills)
        if conv.agent.agent_context
        else []
    )
    conv.close()
    return names


if mode == "plugins":
    store = get_installed_plugins_dir()
    out["store_under_persistence"] = str(store).startswith(str(persist))
    repo = plugin_dir(run / "src" / "gitplug", "gitplug", "1.0.0", "git-skill")
    url = git_repo(repo, tag="v1")
    v1 = head(repo)
    local = plugin_dir(run / "src" / "localplug", "localplug", "0.1.0", "local-skill")

    info = install_plugin(url, ref="v1")
    out["install_name"] = info.name
    out["install_version"] = info.version
    out["install_requested_ref"] = info.requested_ref
    out["install_resolved_is_v1"] = info.resolved_ref == v1
    out["install_path_in_store"] = Path(info.install_path).parent == store
    install_plugin(str(local))
    out["listed"] = sorted(i.name for i in list_installed_plugins())
    out["metadata_keys"] = sorted(metadata(store))
    out["metadata_names"] = sorted(metadata(store).get("extensions", {}))
    out["loaded"] = sorted(p.name for p in load_installed_plugins())
    out["skills_enabled"] = skills_in_conversation()
    try:
        install_plugin(str(local))
        out["reinstall_error"] = None
    except FileExistsError:
        out["reinstall_error"] = "FileExistsError"

    out["disable"] = disable_plugin("localplug")
    out["disabled_flag"] = get_installed_plugin("localplug").enabled
    out["loaded_after_disable"] = sorted(p.name for p in load_installed_plugins())
    out["skills_disabled"] = skills_in_conversation()
    install_plugin(str(local), force=True)
    out["force_keeps_disabled"] = get_installed_plugin("localplug").enabled is False
    out["enable"] = enable_plugin("localplug")
    out["enabled_flag"] = get_installed_plugin("localplug").enabled

    # A plugin installed from its default branch, then updated after a commit.
    track = plugin_dir(run / "src" / "tracking", "tracking", "1.0.0", "track-skill")
    turl = git_repo(track)
    install_plugin(turl)
    write(track / ".plugin" / "plugin.json", {"name": "tracking", "version": "2.0.0"})
    t2 = commit(track, "v2")
    tupd = update_plugin("tracking")
    out["track_update_version"] = tupd.version
    out["track_update_resolved_is_new"] = tupd.resolved_ref == t2
    out["track_installed_file_version"] = json.loads(
        (store / "tracking" / ".plugin" / "plugin.json").read_text()
    )["version"]
    # The plugin installed at tag v1, updated after a commit on main.
    write(repo / ".plugin" / "plugin.json", {"name": "gitplug", "version": "2.0.0"})
    v2 = commit(repo, "v2")
    updated = update_plugin("gitplug")
    out["update_version"] = updated.version
    out["update_resolved_is_v2"] = updated.resolved_ref == v2
    out["update_resolved_is_v1"] = updated.resolved_ref == v1
    out["update_requested_ref"] = updated.requested_ref
    out["installed_file_version"] = json.loads(
        (store / "gitplug" / ".plugin" / "plugin.json").read_text()
    )["version"]
    out["update_missing"] = update_plugin("nothing-here")
    out["uninstall"] = uninstall_plugin("gitplug")
    out["uninstall_again"] = uninstall_plugin("gitplug")
    out["dir_removed"] = not (store / "gitplug").exists()
    uninstall_plugin("tracking")
    out["listed_end"] = sorted(i.name for i in list_installed_plugins())
    out["get_missing"] = get_installed_plugin("gitplug")

elif mode == "manager":
    store = run / "store"
    mgr = InstallationManager(
        installation_dir=store, installation_interface=PluginInstallationInterface()
    )
    evil = run / "src" / "evil"
    write(evil / ".plugin" / "plugin.json", {"name": "../escaped"})
    write(evil / "skills" / "x" / "SKILL.md", skill_md("x", "x"))
    try:
        mgr.install(str(evil))
        out["traversal_error"] = None
    except ValueError as exc:
        out["traversal_error"] = str(exc)
    out["escaped_dir_exists"] = (run / "escaped").exists() or (
        store.parent / "escaped"
    ).exists()
    caps = plugin_dir(run / "src" / "caps", "CapsName", "1", "c")
    try:
        mgr.install(str(caps))
        out["caps_error"] = None
    except ValueError as exc:
        out["caps_error"] = type(exc).__name__
    try:
        mgr.uninstall("../store")
        out["uninstall_traversal_error"] = None
    except ValueError as exc:
        out["uninstall_traversal_error"] = type(exc).__name__
    out["store_still_exists"] = run.exists()

    repo = plugin_dir(run / "src" / "refd", "refd", "1", "r")
    url = git_repo(repo, tag="v1")
    sha = head(repo)
    info = mgr.install(url, ref="v1")
    out["requested_ref"] = info.requested_ref
    out["resolved_is_sha"] = info.resolved_ref == sha
    # A directory copied in by hand, not in .installed.json.
    plugin_dir(store / "manual", "manual", "3", "m")
    out["listed"] = sorted(i.name for i in mgr.list_installed())
    out["manual_source"] = mgr.get("manual").source if mgr.get("manual") else None
    out["manual_tracked_after_list"] = "manual" in metadata(store).get("extensions", {})
    # Mismatched directory name is not adopted.
    plugin_dir(store / "wrong-dir", "other-name", "1", "w")
    out["mismatch_adopted"] = "wrong-dir" in {
        i.name for i in mgr.list_installed()
    } or "other-name" in {i.name for i in mgr.list_installed()}

elif mode == "legacy":
    store = run / "legacy-store"
    a = plugin_dir(store / "old-plugin", "old-plugin", "1", "op")
    b = plugin_dir(store / "old-skillish", "old-skillish", "1", "os")
    both = plugin_dir(store / "both", "both", "1", "b")

    def rec(name, path, enabled=True):
        return {
            "name": name,
            "source": f"github:o/{name}",
            "install_path": str(path),
            "enabled": enabled,
        }

    write(
        store / ".installed.json",
        {
            "plugins": {
                "old-plugin": rec("old-plugin", a, False),
                "both": rec("both", both),
            },
            "skills": {"old-skillish": rec("old-skillish", b)},
            "extensions": {
                "both": {**rec("both", both), "source": "github:o/new-both"}
            },
        },
    )
    listed = {i.name: i for i in list_installed_plugins(installed_dir=store)}
    out["listed"] = sorted(listed)
    out["old_plugin_enabled"] = listed["old-plugin"].enabled
    out["old_plugin_source"] = listed["old-plugin"].source
    out["both_source"] = listed["both"].source
    out["loaded"] = sorted(p.name for p in load_installed_plugins(installed_dir=store))
    out["keys_after"] = sorted(metadata(store))

emit(out)
