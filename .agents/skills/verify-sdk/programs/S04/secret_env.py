"""Give a conversation secrets the way a program does, then let the agent use
them in terminal commands: `update_secrets`, `Conversation(secrets=...)`, a
`SecretSource` subclass, overwriting a key, masking in observations and in the
persisted events. The model is a scripted `TestLLM`; the terminal is real.

Usage: secret_env.py
"""

from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.event import ObservationEvent
from openhands.sdk.secret import SecretSource
from openhands.tools.terminal import TerminalTool


paths = run_paths()
BASE = paths["run"] / "secrets"
out: dict = {"ok": True}
lookups: list[str] = []


class CountingSource(SecretSource):
    """A secret computed on demand; records each lookup."""

    def get_value(self) -> str:
        lookups.append("FUNCTION_TOKEN")
        return "value-from-source-77"


def term(command: str) -> dict:
    return {"tool": "terminal", "args": {"command": command}}


def observations(conversation) -> list[str]:
    return [
        "".join(
            getattr(c, "text", "") for c in e.observation.to_llm_content
        ).splitlines()[0]
        for e in conversation.state.events
        if isinstance(e, ObservationEvent)
    ]


script = [
    term("echo no secrets referenced here"),
    term("echo token=$SECRET_TOKEN"),
    term("echo fn=$FUNCTION_TOKEN"),
    term("echo rotated=$SECRET_TOKEN"),
    term("printf '%s' \"$SECRET_TOKEN\" | wc -c"),
    {"finish": "secrets used"},
]
lookups_at_observation: list[int] = []
conversation = Conversation(
    agent=Agent(llm=make_llm(script), tools=[Tool(name=TerminalTool.name)]),
    workspace=str(paths["workspace"]),
    persistence_dir=str(BASE),
    callbacks=[
        lambda e: lookups_at_observation.append(len(lookups))
        if isinstance(e, ObservationEvent)
        else None
    ],
    visualizer=None,
)
conversation.update_secrets(
    {"SECRET_TOKEN": "first-secret-value-123", "FUNCTION_TOKEN": CountingSource()}
)
conversation.send_message("Use the secrets.")
conversation.run()
obs = observations(conversation)
out["lookups_at_each_observation"] = lookups_at_observation
conversation.close()

# Overwrite a key mid-conversation and continue in a fresh run.
script2 = [
    term("echo rotated=$SECRET_TOKEN"),
    term("printf '%s' \"$SECRET_TOKEN\" | wc -c"),
    {"finish": "rotated"},
]
conversation2 = Conversation(
    agent=Agent(llm=make_llm(script2), tools=[Tool(name=TerminalTool.name)]),
    workspace=str(paths["workspace"]),
    persistence_dir=str(BASE),
    visualizer=None,
)
conversation2.update_secrets({"SECRET_TOKEN": "first-secret-value-123"})
conversation2.update_secrets({"SECRET_TOKEN": "second-secret-value-45678"})
conversation2.send_message("Echo the rotated token.")
conversation2.run()
obs2 = observations(conversation2)
conversation2.close()

on_disk = "".join(p.read_text() for p in BASE.rglob("*.json"))
out |= {
    "unreferenced": obs[0],
    "static": obs[1],
    "from_source": obs[2],
    "length_of_value": obs[4].strip(),
    "rotated": obs2[0],
    "length_after_rotation": obs2[1].strip(),
    "values_in_observations": any(
        v in text
        for text in obs + obs2
        for v in (
            "first-secret-value-123",
            "second-secret-value-45678",
            "value-from-source-77",
        )
    ),
    "values_on_disk": any(
        v in on_disk
        for v in (
            "first-secret-value-123",
            "second-secret-value-45678",
            "value-from-source-77",
        )
    ),
    "names_on_disk": "SECRET_TOKEN" in on_disk,
}

# Secrets given at construction, static and computed; and a plain callable.
script3 = [term("echo api=$API_KEY src=$SRC_KEY"), {"finish": "constructor"}]
conversation3 = Conversation(
    agent=Agent(llm=make_llm(script3), tools=[Tool(name=TerminalTool.name)]),
    workspace=str(paths["workspace"]),
    secrets={"API_KEY": "constructor-value-1", "SRC_KEY": CountingSource()},
    visualizer=None,
)
conversation3.send_message("Echo the keys.")
conversation3.run()
out["constructor"] = observations(conversation3)[0]
out["constructor_value_visible"] = "constructor-value-1" in out["constructor"]
conversation3.close()
try:
    conversation3.update_secrets({"LAMBDA_KEY": lambda: "from-lambda"})  # type: ignore[dict-item]
    out["plain_callable"] = "accepted"
except Exception as exc:
    out["plain_callable"] = f"{type(exc).__name__}: {exc}"
emit(out)
