"""Confirmation mode in running conversations, the way a program drives it.

always    AlwaysConfirm, no analyzer: a message-only reply, two parallel
          terminal actions that wait (pause, then approve with run()), one
          action that is rejected, then a lone finish.
risky     LLMSecurityAnalyzer + ConfirmRisky(): the actor's LOW label runs,
          HIGH waits (rejected), an unlabeled action waits (approved), an
          invalid label is an error, a read-only tool's label is dropped.
never     NeverConfirm + LLMSecurityAnalyzer: the HIGH action runs, the
          analysis is still recorded; without an analyzer the label is
          discarded.
raising   an analyzer that raises with a secret in its message.
policy    security_policy_filename: default, custom file, None, "", and an
          empty custom file, read from agent.static_system_message.
"""

import json
from pathlib import Path

from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    ObservationEvent,
    SecurityAnalysisEvent,
    UserRejectObservation,
)
from openhands.sdk.security import (
    AlwaysConfirm,
    ConfirmRisky,
    LLMSecurityAnalyzer,
    SecurityAnalyzerBase,
)
from openhands.tools.glob import GlobTool
from openhands.tools.terminal import TerminalTool


paths = run_paths()
ws = paths["workspace"]
TOOLS = [Tool(name=TerminalTool.name), Tool(name=GlobTool.name)]


def term(command: str, risk: str | None = None) -> dict:
    args: dict = {"command": command}
    if risk is not None:
        args["security_risk"] = risk
    return {"tool": "terminal", "args": args}


def conversation(script, **kwargs):
    return Conversation(
        agent=Agent(llm=make_llm(script), tools=TOOLS),
        workspace=str(ws),
        visualizer=None,
        **kwargs,
    )


def status(c) -> str:
    return c.state.execution_status.value


def pending(c) -> list[str]:
    return [
        getattr(a.action, "command", a.tool_name)
        for a in ConversationState.get_unmatched_actions(c.state.events)
    ]


def analyses(c) -> list[dict]:
    return [
        {
            "analyzer": e.analyzer,
            "policy": e.policy,
            "risks": sorted(r.value for r in e.risks.values()),
            "details": list(e.details.values()),
        }
        for e in c.state.events
        if isinstance(e, SecurityAnalysisEvent)
    ]


def action_risks(c) -> list[str]:
    """The risk on each executable action, in order."""
    return [
        a.security_risk.value
        for a in c.state.events
        if isinstance(a, ActionEvent)
        and a.tool_name != "finish"
        and a.action is not None
    ]


result: dict = {"ok": True}

# --- always: AlwaysConfirm without an analyzer -------------------------------
c = conversation(
    [
        {"text": "Hello, nothing to run."},
        {"tools": [term("echo one > one.txt"), term("echo two > two.txt")]},
        term("echo three > three.txt"),
        {"finish": "done"},
    ]
)
c.set_confirmation_policy(AlwaysConfirm())
c.send_message("Say hello.")
c.run()
always: dict = {"message_only_status": status(c)}
c.send_message("Write one.txt and two.txt, then three.txt.")
c.run()
always["after_parallel"] = status(c)
always["pending"] = pending(c)
always["files_before_approve"] = sorted(p.name for p in ws.glob("*.txt"))
c.pause()
always["after_pause"] = status(c)
c.run()  # approve
always["after_approve"] = status(c)
always["pending_after_approve"] = pending(c)
always["files_after_approve"] = sorted(p.name for p in ws.glob("*.txt"))
c.reject_pending_actions("not now")
always["after_reject"] = status(c)
rejections = [e for e in c.state.events if isinstance(e, UserRejectObservation)]
always["rejections"] = [
    {"tool": e.tool_name, "reason": e.rejection_reason} for e in rejections
]
c.run()  # the agent continues; a lone finish never waits
always["final"] = status(c)
always["three_written"] = (ws / "three.txt").exists()
always["analysis_events"] = len(analyses(c))
always["policy_active"] = c.confirmation_policy_active
always["mode_active"] = c.is_confirmation_mode_active
c.close()
result["always"] = always

# --- risky: LLMSecurityAnalyzer + ConfirmRisky() -----------------------------
c = conversation(
    [
        term("echo low", "LOW"),
        term("echo high > high.txt", "HIGH"),
        term("echo unlabeled > unlabeled.txt"),
        term("echo severe", "SEVERE"),
        {"tool": "glob", "args": {"pattern": "*.txt", "security_risk": "HIGH"}},
        {"finish": "done"},
    ]
)
c.set_security_analyzer(LLMSecurityAnalyzer())
c.set_confirmation_policy(ConfirmRisky())
c.send_message("Run the commands.")
c.run()
risky: dict = {"first_stop": status(c), "pending_first": pending(c)}
risky["low_ran"] = any(
    isinstance(e, ObservationEvent) and e.tool_name == "terminal"
    for e in c.state.events
)
risky["analysis_before_wait"] = analyses(c)[-1] if analyses(c) else None
c.reject_pending_actions("too risky")
c.run()
risky["second_stop"] = status(c)
risky["pending_second"] = pending(c)
c.run()  # approve the unlabeled one; then SEVERE errors and glob stops again
risky["third_stop"] = status(c)
risky["errors"] = [
    e.error[:200] for e in c.state.events if isinstance(e, AgentErrorEvent)
]
c.run()
risky["final"] = status(c)
risky["action_risks"] = action_risks(c)
risky["high_written"] = (ws / "high.txt").exists()
risky["unlabeled_written"] = (ws / "unlabeled.txt").exists()
risky["analyses"] = analyses(c)
risky["mode_active"] = c.is_confirmation_mode_active
c.close()
result["risky"] = risky

# --- never: NeverConfirm keeps recording; no analyzer drops the label --------
c = conversation([term("echo never > never.txt", "HIGH"), {"finish": "done"}])
c.set_security_analyzer(LLMSecurityAnalyzer())
c.send_message("Run it.")
c.run()
result["never"] = {
    "status": status(c),
    "written": (ws / "never.txt").exists(),
    "analyses": analyses(c),
    "action_risks": action_risks(c),
    "policy_active": c.confirmation_policy_active,
    "mode_active": c.is_confirmation_mode_active,
}
c.close()
c = conversation([term("echo bare", "HIGH"), {"finish": "done"}])
c.set_confirmation_policy(ConfirmRisky(confirm_unknown=False))
c.send_message("Run it.")
c.run()
result["no_analyzer"] = {
    "status": status(c),
    "action_risks": action_risks(c),
    "analyses": len(analyses(c)),
}
c.close()


# --- raising: an analyzer error rates HIGH; its secret is masked -------------
class FlakyAnalyzer(SecurityAnalyzerBase):
    """A program's own analyzer whose backend fails, quoting a token."""

    def security_risk(self, action):  # noqa: ARG002
        raise RuntimeError("backend refused token tok-s13-raise-123")


seen: list[dict] = []
c = Conversation(
    agent=Agent(llm=make_llm([term("echo x"), {"finish": "done"}]), tools=TOOLS),
    workspace=str(ws),
    persistence_dir=str(paths["conversations"]),
    visualizer=None,
    secrets={"API_TOKEN": "tok-s13-raise-123"},
    callbacks=[
        lambda e: seen.append(e.model_dump(mode="json"))
        if isinstance(e, SecurityAnalysisEvent)
        else None
    ],
)
c.set_security_analyzer(FlakyAnalyzer())
c.set_confirmation_policy(ConfirmRisky())
c.send_message("Run it.")
c.run()
persisted = "".join(
    p.read_text() for p in Path(c.state.persistence_dir).glob("events/*.json")
)
result["raising"] = {
    "status": status(c),
    "analyses": analyses(c),
    "callback_details": [list(e["details"].values()) for e in seen],
    "persisted_has_value": "tok-s13-raise-123" in persisted,
    "persisted_has_mask": "<secret-hidden>" in persisted,
}
c.close()

# --- policy: the risk policy in the system message ---------------------------
custom = ws / "my_policy.md"
custom.write_text("CUSTOM-POLICY: never touch prod. {{ raw }}\n")
empty = ws / "empty_policy.md"
empty.write_text("")
llm = make_llm([])


def system(**kwargs) -> str:
    return Agent(llm=llm, tools=TOOLS, **kwargs).static_system_message


default_text = system()
custom_text = system(security_policy_filename=str(custom))
none_text = system(security_policy_filename=None)
blank_text = system(security_policy_filename="")
empty_text = system(security_policy_filename=str(empty))
result["policy"] = {
    "default_has_block": "<SECURITY>" in default_text,
    "default_has_builtin": "# 🔐 Security Policy" in default_text,
    "default_has_assessment": "<SECURITY_RISK_ASSESSMENT>" in default_text,
    "custom_verbatim": "CUSTOM-POLICY: never touch prod. {{ raw }}" in custom_text,
    "custom_has_builtin": "# 🔐 Security Policy" in custom_text,
    "custom_has_assessment": "<SECURITY_RISK_ASSESSMENT>" in custom_text,
    "none_has_block": "<SECURITY>" in none_text,
    "none_has_builtin": "# 🔐 Security Policy" in none_text,
    "none_has_assessment": "<SECURITY_RISK_ASSESSMENT>" in none_text,
    "blank_has_block": "<SECURITY>" in blank_text,
    "empty_file_has_builtin": "# 🔐 Security Policy" in empty_text,
    "empty_file_has_assessment": "<SECURITY_RISK_ASSESSMENT>" in empty_text,
}
emit(json.loads(json.dumps(result, default=str)))
