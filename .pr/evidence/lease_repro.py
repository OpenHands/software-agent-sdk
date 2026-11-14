import json, os, signal, subprocess, sys, tempfile, time
from pathlib import Path
from openhands.agent_server.conversation_lease import ConversationLease, LEASE_FILE_NAME

d = Path(tempfile.mkdtemp()) / "conv"
ConversationLease(conversation_dir=d, owner_instance_id="old-server", ttl_seconds=45).claim()
lease = d / LEASE_FILE_NAME

def try_claim(label):
    try:
        ConversationLease(conversation_dir=d, owner_instance_id="new-server").claim()
        print(f"{label}: claimed")
    except Exception as e:
        print(f"{label}: {type(e).__name__}: {e}")

# 1) restarted server reuses the dead server's PID (same container, same PID)
# the old process had this PID but started at another time
p = json.loads(lease.read_text()); p["owner_started_at"] = "1"; lease.write_text(json.dumps(p))
try_claim("same PID as the dead owner")

# 2) owner was killed but not reaped yet (zombie)
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
p["owner_pid"] = child.pid; p.pop("owner_started_at"); lease.write_text(json.dumps(p))
os.kill(child.pid, signal.SIGKILL); time.sleep(0.5)
try_claim("owner killed, not reaped (zombie)")
child.wait()
