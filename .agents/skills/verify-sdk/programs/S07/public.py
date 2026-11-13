"""Public skills: clone, marketplace filter, refs, in-process cache, fallback.

Usage: public.py MODE

  local    builds a local git repository shaped like OpenHands/extensions
           (tag v1: marketplace lists alpha; main: alpha and gamma) under the
           run, and loads it with load_public_skills(repo_url=...).
  offline  the same repository has been moved away: loading falls back to the
           clone in the skills cache (a fresh process, so no in-process cache).
  env      reports the ref EXTENSIONS_REF selected (pass --env EXTENSIONS_REF=v1).
  remote   AgentContext(load_public_skills=True) against the real
           github.com/OpenHands/extensions (network).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from sdk_verify import emit, run_paths

from openhands.sdk import AgentContext
from openhands.sdk.skills import load_public_skills


mode = sys.argv[1]
ext = run_paths()["run"] / "extensions"
cache = Path(os.environ["OH_PERSISTENCE_DIR"]) / "cache" / "skills" / "public-skills"


def git(*args):
    subprocess.run(
        ["git", "-C", str(ext), "-c", "user.name=v", "-c", "user.email=v@x", *args],
        check=True,
        capture_output=True,
    )


def write(rel, content):
    path = ext / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def skill_md(name):
    return f"---\nname: {name}\ndescription: {name} skill.\n---\n{name.upper()}-BODY\n"


def market(names):
    # The extensions repository lists its skills as marketplace plugin entries.
    return json.dumps(
        {
            "name": "default",
            "owner": {"name": "verify-sdk"},
            "plugins": [{"name": n, "source": f"./skills/{n}"} for n in names],
        }
    )


def names(skills):
    return sorted(s.name for s in skills)


out = {"ok": True, "mode": mode}
if mode == "local":
    subprocess.run(["git", "init", "-q", "-b", "main", str(ext)], check=True)
    write("skills/alpha/SKILL.md", skill_md("alpha"))
    write("skills/beta/SKILL.md", skill_md("beta"))
    write("skills/gamma.md", "GAMMA-BODY\n")
    write("marketplaces/default.json", market(["alpha"]))
    git("add", "-A")
    git("commit", "-qm", "v1")
    git("tag", "v1")
    write("marketplaces/default.json", market(["alpha", "gamma"]))
    write("marketplaces/small.json", market(["beta"]))
    git("add", "-A")
    git("commit", "-qm", "v2")
    url = str(ext)
    out["main_default"] = names(load_public_skills(repo_url=url, ref="main"))
    out["main_all"] = names(
        load_public_skills(repo_url=url, ref="main", marketplace_path=None)
    )
    out["main_small"] = names(
        load_public_skills(
            repo_url=url, ref="main", marketplace_path="marketplaces/small.json"
        )
    )
    out["cache_clone"] = (cache / ".git").exists()
    # A new commit within the 60 s branch TTL is not seen in this process.
    write("marketplaces/default.json", market(["alpha", "beta", "gamma"]))
    git("commit", "-qam", "v3")
    out["main_default_again"] = names(load_public_skills(repo_url=url, ref="main"))
    out["tag_v1"] = names(load_public_skills(repo_url=url, ref="v1"))
elif mode == "offline":
    out["repo_present"] = ext.exists()
    out["main_default"] = names(load_public_skills(repo_url=str(ext), ref="main"))
elif mode == "env":
    from openhands.sdk.skills import skill as skill_module

    out["ref"] = skill_module.PUBLIC_SKILLS_REF
    out["default_ref_load"] = names(
        load_public_skills(repo_url=str(ext), ref=skill_module.PUBLIC_SKILLS_REF)
    )
elif mode == "remote":
    context = AgentContext(load_public_skills=True)
    out["public_count"] = len(context.skills)
    out["has_github"] = "github" in names(context.skills)
    out["origin"] = subprocess.run(
        ["git", "-C", str(cache), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    out["cache_clone"] = (cache / ".git").exists()
emit(out)
