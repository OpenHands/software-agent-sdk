"""The skill install store, and installing what a marketplace lists.

Usage: installed.py [remote]

Installs a local skill into ~/.openhands/skills/installed/ (the run's
OH_PERSISTENCE_DIR), walks it through list, reinstall, disable, enable,
update and uninstall, then installs from a local marketplace.json that
lists a standalone skill, a plugin with two skills and a missing source.
With `remote`, the marketplace also lists a GitHub blob URL (network).
"""

import json
import os
import sys
from pathlib import Path

from sdk_verify import emit, run_paths

from openhands.sdk.skills import (
    disable_skill,
    enable_skill,
    get_installed_skill,
    install_skill,
    install_skills_from_marketplace,
    list_installed_skills,
    load_installed_skills,
    uninstall_skill,
    update_skill,
)


remote = sys.argv[1:] == ["remote"]
run = run_paths()["run"]
store = Path(os.environ["OH_PERSISTENCE_DIR"]) / "skills" / "installed"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def skill_md(name: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {name} skill.\n---\n{body}\n"


def names():
    return sorted(i.name for i in list_installed_skills())


def enabled():
    return sorted(s.name for s in load_installed_skills())


def error_of(fn):
    try:
        fn()
        return None
    except Exception as exc:
        return type(exc).__name__


out = {"ok": True}
source = run / "sources" / "greeter"
write(source / "SKILL.md", skill_md("greeter", "GREETER-V1"))

info = install_skill(str(source))
out["installed_name"] = info.name
out["store_file"] = (store / ".installed.json").is_file()
out["after_install"] = names()
out["reinstall_error"] = error_of(lambda: install_skill(str(source)))
disable_skill("greeter")
out["enabled_after_disable"] = enabled()
out["listed_after_disable"] = names()
install_skill(str(source), force=True)
out["enabled_after_force_reinstall"] = enabled()
enable_skill("greeter")
out["enabled_after_enable"] = enabled()
write(source / "SKILL.md", skill_md("greeter", "GREETER-V2"))
update_skill("greeter")
installed = {s.name: s for s in load_installed_skills()}
out["content_after_update"] = installed["greeter"].content.strip()
out["get_installed"] = bool(get_installed_skill("greeter"))
out["uninstall"] = uninstall_skill("greeter")
out["uninstall_again"] = uninstall_skill("greeter")
out["after_uninstall"] = names()
out["dir_after_uninstall"] = (store / "greeter").exists()

market = run / "market"
write(
    market / "skills" / "standalone" / "SKILL.md", skill_md("standalone", "STANDALONE")
)
write(
    market / "plugins" / "plug" / ".plugin" / "plugin.json",
    json.dumps({"name": "plug"}),
)
write(
    market / "plugins" / "plug" / "skills" / "plug-one" / "SKILL.md",
    skill_md("plug-one", "P1"),
)
write(
    market / "plugins" / "plug" / "skills" / "plug-two" / "SKILL.md",
    skill_md("plug-two", "P2"),
)
skills = [
    {"name": "standalone", "source": "./skills/standalone"},
    {"name": "missing", "source": "./skills/missing"},
]
if remote:
    skills.append(
        {
            "name": "github",
            "source": "https://github.com/OpenHands/extensions/blob/main/skills/github",
        }
    )
write(
    market / ".plugin" / "marketplace.json",
    json.dumps(
        {
            "name": "local-market",
            "owner": {"name": "verify-sdk"},
            "plugins": [{"name": "plug", "source": "./plugins/plug"}],
            "skills": skills,
        }
    ),
)
result = install_skills_from_marketplace(market)
out["marketplace_installed"] = sorted(i.name for i in result)
out["after_marketplace"] = names()
out["again_without_force"] = sorted(
    i.name for i in install_skills_from_marketplace(market)
)
emit(out)
