"""Usage: secret_rotation.py

A secret rotated mid-conversation with `update_secrets()`, and a program's own
`SecretSource` that returns a new value on each lookup.

`DEPLOY_TOKEN` starts as `dep-v1-s13` and `OAUTH_TOKEN` (a rotating source)
answers `oauth-v<n>-s13`. A scripted agent cats files that hold the values
without naming the secrets (as a token in a git remote or a log would be):

  run 1  cat v1.txt                          (dep-v1-s13 oauth-v1-s13)
  update_secrets({"DEPLOY_TOKEN": "dep-v2-s13"})
  run 2  cat v2.txt                          (dep-v2-s13, never named yet)
         echo $DEPLOY_TOKEN $OAUTH_TOKEN | wc -c   (exports both: v2 and oauth-v2)
         cat v1.txt                          (the previous values again)

Each `*_leaks` field lists the cleartext values found in that observation;
`persisted_leaks` lists every value found in the persisted events.
"""

from pathlib import Path

from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.event import ObservationEvent
from openhands.sdk.secret import SecretSource
from openhands.tools.terminal import TerminalTool


paths = run_paths()
ws = paths["workspace"]
VALUES = ["dep-v1-s13", "dep-v2-s13", "oauth-v1-s13", "oauth-v2-s13"]


class Rotating(SecretSource):
    calls: int = 0

    def get_value(self) -> str | None:
        self.calls += 1
        return f"oauth-v{self.calls}-s13"


(ws / "v1.txt").write_text("remote has dep-v1-s13 log has oauth-v1-s13\n")
(ws / "v2.txt").write_text("config has dep-v2-s13\n")

script = [
    {"tool": "terminal", "args": {"command": "cat v1.txt"}},
    {"finish": "one"},
    {"tool": "terminal", "args": {"command": "cat v2.txt"}},
    {
        "tool": "terminal",
        "args": {"command": "echo $DEPLOY_TOKEN $OAUTH_TOKEN | wc -c"},
    },
    {"tool": "terminal", "args": {"command": "cat v1.txt"}},
    {"finish": "two"},
]
rotating = Rotating()
c = Conversation(
    agent=Agent(llm=make_llm(script), tools=[Tool(name=TerminalTool.name)]),
    workspace=str(ws),
    persistence_dir=str(paths["conversations"]),
    visualizer=None,
    secrets={"DEPLOY_TOKEN": "dep-v1-s13", "OAUTH_TOKEN": rotating},
)
c.send_message("first")
c.run()
c.update_secrets({"DEPLOY_TOKEN": "dep-v2-s13"})
c.send_message("second")
c.run()

texts = [
    "".join(getattr(x, "text", "") for x in e.observation.to_llm_content)
    for e in c.state.events
    if isinstance(e, ObservationEvent) and e.tool_name == "terminal"
]


def leaks(text: str) -> list[str]:
    return [v for v in VALUES if v in text]


persisted = ""
for f in sorted(Path(paths["conversations"]).rglob("events/*.json")):
    persisted += f.read_text()

emit(
    {
        "status": c.state.execution_status.value,
        "observations": len(texts),
        "before_rotation": texts[0] if texts else None,
        "before_rotation_leaks": leaks(texts[0]) if texts else None,
        "new_value": texts[1] if len(texts) > 1 else None,
        "new_value_leaks": leaks(texts[1]) if len(texts) > 1 else None,
        "export_wc": texts[2].split("\n")[0] if len(texts) > 2 else None,
        "previous_values": texts[3] if len(texts) > 3 else None,
        "previous_values_leaks": leaks(texts[3]) if len(texts) > 3 else None,
        "source_lookups": rotating.calls,
        "persisted_leaks": leaks(persisted),
    }
)
