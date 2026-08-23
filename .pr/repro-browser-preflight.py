#!/usr/bin/env python3
"""Before/after demonstration of the browser pre-flight check.

Simulates a server where the Chromium binary exists but can't launch
(e.g. missing shared libraries). Without the pre-flight check, the browser
launch hangs. With the check, it fails fast.

USAGE:
  python3 repro-browser-preflight.py /path/to/sdk/without/fix  # before — hangs
  python3 repro-browser-preflight.py /path/to/sdk/with/fix     # after — fails fast
"""

import os
import subprocess
import sys
import tempfile
import threading
import time


sdk_path = sys.argv[1]
for sub in ["openhands-tools", "openhands-sdk", "openhands-agent-server"]:
    p = os.path.join(sdk_path, sub)
    if os.path.isdir(p):
        sys.path.insert(0, p)

# Create a fake "chromium" binary that simulates a real broken install:
# - With --version: prints an error and exits 1 (missing libstdc++.so)
# - Without --version (actual launch): hangs forever (simulates C-level block)
fake_chromium = tempfile.NamedTemporaryFile(suffix="_chromium", delete=False, mode="w")
fake_chromium.write("""#!/usr/bin/env python3
import sys
if "--version" in sys.argv:
    # Simulates: error while loading shared libraries: libstdc++.so.6
    sys.stderr.write("error while loading shared libraries: libstdc++.so.6\n")
    sys.exit(1)
else:
    # Actual launch: hangs forever (simulates C-level lock)
    import time; time.sleep(300)
""")
fake_chromium.close()
os.chmod(fake_chromium.name, 0o755)
fake_path = fake_chromium.name

has_fix = (
    "pre-flight"
    in open(
        os.path.join(sdk_path, "openhands-tools/openhands/tools/browser_use/impl.py")
    ).read()
)

print(f"[repro] SDK: {sdk_path}")
print(f"[repro] has pre-flight fix: {has_fix}")
print(f"[repro] fake chromium: {fake_path}")
print("[repro] --version → exit 1 (missing libstdc++.so)")
print("[repro] launch → hangs 300s (C-level block)")
print()

from unittest.mock import MagicMock  # noqa: E402

from openhands.tools.browser_use.impl import BrowserToolExecutor  # noqa: E402


mock_self = MagicMock(spec=BrowserToolExecutor)
mock_self.check_chromium_available = MagicMock(return_value=fake_path)

if not has_fix:
    # OLD behavior: just check the binary exists, then try to launch (hangs)
    print(
        "[before] OLD: check_chromium_available() → binary found, proceeding to launch"
    )
    print(
        "[before]   In production: BrowserSession.start() dispatches BrowserStartEvent"
    )
    print(
        "[before]   handler hangs on C-level launch, timeout fires"
        "[before]   but thread never freed"
    )
    print()

    print("[before] Launching browser subprocess (simulates _launch_browser)...")
    proc = subprocess.Popen([fake_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    print(f"[before]   PID={proc.pid}, waiting for CDP port (simulated)...")

    # Wait 5s — the process hangs (sleep 300)
    done = threading.Event()

    def _wait():
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        done.set()

    t = threading.Thread(target=_wait, daemon=True)
    t.start()

    if done.wait(timeout=6):
        rc = proc.poll()
        if rc is not None:
            print(f"[before]   process exited with code {rc} (unexpected)")
        else:
            print("[before]   process still running after 5s")
    else:
        print("[before]   *** HANG *** — browser didn't respond after 5s")
        print("[before]   In production, the bubus handler timeout fires after 30s")
        print("[before]   but the thread is never freed → zombie thread accumulates")

    proc.kill()
    zombies = [
        t
        for t in threading.enumerate()
        if t.is_alive() and not t.daemon and t is not threading.main_thread()
    ]
    print(f"[before] RESULT: FAIL — hung, {len(zombies)} zombie thread(s)")
else:
    # NEW behavior: pre-flight check runs --version first
    print("[after] NEW: _ensure_chromium_available() runs pre-flight (--version)")
    print()

    t0 = time.monotonic()
    try:
        BrowserToolExecutor._ensure_chromium_available(mock_self)
        elapsed = time.monotonic() - t0
        print(
            f"[after] RESULT: PASS — pre-flight passed in {elapsed:.1f}s (unexpected)"
        )
    except Exception as e:
        elapsed = time.monotonic() - t0
        msg = str(e)[:200]
        print(f"[after] RESULT: FAIL FAST in {elapsed:.1f}s")
        print(f"[after]   error: {msg}")
        print("[after]   No bubus event dispatched, no zombie thread")

    zombies = [
        t
        for t in threading.enumerate()
        if t.is_alive() and not t.daemon and t is not threading.main_thread()
    ]
    print(f"[after] zombie threads: {len(zombies)}")

os.unlink(fake_path)
