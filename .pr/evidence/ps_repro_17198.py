"""Native-PowerShell terminal poisoning repro (OpenHands/OpenHands#17198), run with pwsh on Linux."""
import os, subprocess
from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.terminal import create_terminal_session

sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
print(f"software-agent-sdk @ {sha}, terminal_type=powershell, shell=pwsh {subprocess.check_output(['pwsh','-NoLogo','-Command','$PSVersionTable.PSVersion.ToString()'], text=True).strip()}")
os.makedirs("/tmp/psws/.agents/agents", exist_ok=True)
open("/tmp/psws/.agents/agents/reviewer.md", "w").write("# reviewer\n")
s = create_terminal_session(work_dir="/tmp/psws", terminal_type="powershell", no_change_timeout_seconds=3)
s.initialize()

def run(cmd, **kw):
    obs = s.execute(TerminalAction(command=cmd, **kw))
    text = obs.text if hasattr(obs, "text") else str(obs)
    print(f"\n$ {cmd}")
    print("  output :", (text.strip().replace("\n", "\n           ") or "<empty>")[:400])
    print("  exit   :", obs.metadata.exit_code, "| suffix:", (obs.metadata.suffix or "").strip()[:160])

run("Get-ChildItem -Name .agents/agents")
run("throw 'boom'")
run("Get-ChildItem -Name .agents/agents")
run("Write-Output hello")
run("Get-Item -Path missing.txt -ErrorAction Stop")
run("Write-Output after-error-action-stop")
s.close()
