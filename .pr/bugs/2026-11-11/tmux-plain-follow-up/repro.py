import sys, tempfile
sys.path.insert(0, "/home/user/software-agent-sdk/.agents/skills/verify-sdk/programs")
from sdk_verify import make_llm
from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.event import ObservationEvent
from openhands.sdk.tool import register_tool
from openhands.tools.terminal import TerminalTool

is_input = "--input" in sys.argv
ttype = None
for a in sys.argv:
    if a.startswith("--type="): ttype = a.split("=",1)[1]
register_tool(TerminalTool.name, TerminalTool)
def step(cmd, inp=False):
    args = {"command": cmd}
    if inp: args["is_input"] = True
    return {"tool": "terminal", "args": args}
script = [step("python3"), step("print(6 * 7)", is_input), step("exit()", is_input),
          step("echo back-in-shell"), {"finish": "done"}]
params = {"no_change_timeout_seconds": 3}
if ttype: params["terminal_type"] = ttype
agent = Agent(llm=make_llm(script), tools=[Tool(name=TerminalTool.name, params=params)])
ws = tempfile.mkdtemp(prefix="repl2ws-")
conv = Conversation(agent=agent, workspace=ws, visualizer=None)
print(f"mode: is_input={is_input} terminal_type={ttype}")
conv.send_message("go")
conv.run()
i = 0
for ev in conv.state.events:
    if isinstance(ev, ObservationEvent):
        i += 1
        print(f"===== OBS {i} cmd={getattr(ev.observation,'command',None)!r} =====")
        print("\n".join(getattr(c, "text", "") for c in ev.observation.to_llm_content))
conv.close()
