"""The OH_PUBLIC_SKILLS_ENABLED switch for the default public skills.

`EXTENSIONS_PATH` points the default public repository at a local copy built
under the run (skills `alpha` and `beta`, the default marketplace lists only
`alpha`), so nothing is cloned. The program loads public skills with
`load_public_skills()` and `AgentContext(load_public_skills=True)` under each
value of the switch, keeps an explicit skill alongside, and loads another
repository (a local git repository with `gamma`) with the switch off.
"""

import json
import os
import subprocess

from sdk_verify import emit, run_paths

from openhands.sdk import AgentContext
from openhands.sdk.skills import Skill, load_public_skills


run = run_paths()["run"]
local_copy = run / "extensions-copy"
other_repo = run / "other-extensions"


def skill_md(name):
    return f"---\nname: {name}\ndescription: {name} skill.\n---\n{name.upper()}-BODY\n"


def market(names):
    return json.dumps(
        {
            "name": "default",
            "owner": {"name": "verify-sdk"},
            "plugins": [{"name": n, "source": f"./skills/{n}"} for n in names],
        }
    )


def layout(root, skills, listed):
    for name in skills:
        path = root / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(skill_md(name))
    (root / "marketplaces").mkdir(parents=True, exist_ok=True)
    (root / "marketplaces" / "default.json").write_text(market(listed))


layout(local_copy, ["alpha", "beta"], ["alpha"])
layout(other_repo, ["gamma"], ["gamma"])
git = ["git", "-C", str(other_repo), "-c", "user.name=v", "-c", "user.email=v@x"]
subprocess.run(["git", "init", "-q", "-b", "main", str(other_repo)], check=True)
subprocess.run([*git, "add", "-A"], check=True)
subprocess.run([*git, "commit", "-qm", "v1"], check=True)

os.environ["EXTENSIONS_PATH"] = str(local_copy)
explicit = Skill(name="explicit", content="EXPLICIT-BODY", trigger=None)


def names(skills):
    return sorted(s.name for s in skills)


out = {"ok": True}
for label, value in (
    ("unset", None),
    ("false", "false"),
    ("off", "off"),
    ("zero", "0"),
    ("true", "true"),
    ("garbage", "maybe"),
):
    if value is None:
        os.environ.pop("OH_PUBLIC_SKILLS_ENABLED", None)
    else:
        os.environ["OH_PUBLIC_SKILLS_ENABLED"] = value
    out[label] = {
        "load": names(load_public_skills()),
        "context": names(AgentContext(load_public_skills=True).skills),
        "with_explicit": names(
            AgentContext(load_public_skills=True, skills=[explicit]).skills
        ),
    }

# EXTENSIONS_PATH is read in place: no clone in the skills cache so far.
clone = run / "openhands" / "cache" / "skills" / "public-skills" / ".git"
out["cloned_before_other_repo"] = clone.exists()
os.environ["OH_PUBLIC_SKILLS_ENABLED"] = "false"
out["other_repo_when_off"] = names(
    load_public_skills(repo_url=str(other_repo), ref="main")
)
emit(out)
