"""Search a project with `glob` and `grep` through a scripted agent, once on a
host with ripgrep and once on a host without it (PATH without `rg`), and
compare what the model would see. A conversation secret appears in a file,
in a grep pattern and in an apply_patch hunk; observations must mask it.

Usage: search.py [N_MANY]   (default 100 files under many/)
"""

import os
import shutil
import sys
import time

from S14.toolkit import converse, workspace
from sdk_verify import emit

from openhands.sdk import Tool
from openhands.sdk.event import ObservationEvent
from openhands.tools.apply_patch import ApplyPatchTool
from openhands.tools.glob import GlobTool
from openhands.tools.grep import GrepTool


SECRET = "sk-live-4242abcd"
many = int(sys.argv[1]) if len(sys.argv) > 1 else 100
ws = workspace()
(ws / "src" / "pkg").mkdir(parents=True, exist_ok=True)
(ws / "src" / "old.py").write_text("def old():\n    pass\n")
time.sleep(0.05)
(ws / "src" / "pkg" / "new.py").write_text("def new():\n    return 1\n")
(ws / "src" / "notes.md").write_text("def in markdown\n")
(ws / "creds.txt").write_text(f"key={SECRET}\n")
(ws / "many").mkdir(exist_ok=True)
for i in range(many):
    (ws / "many" / f"f{i:03d}.txt").write_text("needle\n")

search_steps = [
    ("glob_py", "glob", {"pattern": "**/*.py"}),
    ("glob_in_path", "glob", {"pattern": "*.txt", "path": "{ws}/many"}),
    ("grep_include", "grep", {"pattern": "def \\w+", "include": "*.py"}),
    ("grep_many", "grep", {"pattern": "needle", "path": "{ws}/many"}),
    ("grep_secret", "grep", {"pattern": SECRET}),
]
patch = f"*** Begin Patch\n*** Add File: added.txt\n+token {SECRET}\n*** End Patch"
tools = [
    Tool(name=GlobTool.name),
    Tool(name=GrepTool.name),
    Tool(name=ApplyPatchTool.name),
]


def run(with_patch: bool) -> dict:
    script = [{"tool": tool, "args": args} for _, tool, args in search_steps]
    if with_patch:
        script.append({"tool": "apply_patch", "args": {"patch": patch}})
    script.append({"finish": "done"})
    conversation = converse(tools, script, secrets={"API_KEY": SECRET})
    observations = [
        e.observation
        for e in conversation.state.events
        if isinstance(e, ObservationEvent) and e.tool_name != "finish"
    ]
    conversation.close()
    out = {}
    names = [n for n, _, _ in search_steps] + (["patch"] if with_patch else [])
    for name, obs in zip(names, observations):
        found = getattr(obs, "files", None) or getattr(obs, "matches", None) or []
        out[name] = {
            "is_error": obs.is_error,
            "count": len(found),
            "files": [os.path.relpath(f, ws) for f in found][:5],
            "truncated": getattr(obs, "truncated", None),
            "text": obs.text[:300],
            "dump": obs.model_dump_json()[:4000],
        }
    return out


saved_path = os.environ["PATH"]
os.environ["PATH"] = str(ws / "no-tools")  # a host without ripgrep
without_rg = run(with_patch=False)
os.environ["PATH"] = saved_path
with_rg = run(with_patch=True)
# Full observation dumps (patch commit included) are checked, then dropped.
secret_seen = SECRET in str(with_rg) or SECRET in str(without_rg)
for result in (with_rg, without_rg):
    for row in result.values():
        row.pop("dump")

emit(
    {
        "ok": True,
        "rg_available": shutil.which("rg") is not None,
        "rg": with_rg,
        "python": without_rg,
        "same_files": all(
            sorted(with_rg[n]["files"]) == sorted(without_rg[n]["files"])
            and with_rg[n]["count"] == without_rg[n]["count"]
            for n, _, _ in search_steps
        ),
        "same_truncated": all(
            with_rg[n]["truncated"] == without_rg[n]["truncated"]
            for n, _, _ in search_steps
        ),
        "newest_first": with_rg["glob_py"]["files"][:1] == ["src/pkg/new.py"],
        "added_file": (ws / "added.txt").read_text(),
        "secret_in_observations": secret_seen,
    }
)
