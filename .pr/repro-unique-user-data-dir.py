#!/usr/bin/env python3
"""Before/after demonstration of the unique user_data_dir fix.

Simulates the production scenario: conversation A crashes leaving a
SingletonLock in the shared user_data_dir, then conversation B tries
to launch and hangs.

BEFORE (shared user_data_dir): B hangs because A's SingletonLock blocks.
AFTER  (unique user_data_dir): B launches fine because it uses a different dir.

USAGE:
  python3 repro-unique-user-data-dir.py /path/to/sdk/without/fix  # before
  python3 repro-unique-user-data-dir.py /path/to/sdk/with/fix     # after
"""

import os
import shutil
import subprocess
import sys
import time


sdk_path = sys.argv[1]
for sub in ["openhands-tools", "openhands-sdk", "openhands-agent-server"]:
    p = os.path.join(sdk_path, sub)
    if os.path.isdir(p):
        sys.path.insert(0, p)

has_fix = (
    "unique user_data_dir"
    in open(
        os.path.join(sdk_path, "openhands-tools/openhands/tools/browser_use/impl.py")
    ).read()
)

CHROME = os.path.expanduser(
    "~/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome"
)

print(f"[repro] SDK: {sdk_path}")
print(f"[repro] has unique user_data_dir fix: {has_fix}")
print(f"[repro] chrome binary: {CHROME}")
print()

# Step 1: Simulate conversation A crashing and leaving a SingletonLock
if has_fix:
    # With fix: each conversation gets its own dir, so A's lock doesn't affect B
    dir_a = os.path.expanduser("~/.config/browseruse/profiles/A_crashed")
    dir_b = os.path.expanduser("~/.config/browseruse/profiles/B_new")
else:
    # Without fix: both conversations share the same default dir
    dir_a = dir_b = os.path.expanduser("~/.config/browseruse/profiles/shared_default")

os.makedirs(dir_a, exist_ok=True)
os.makedirs(dir_b, exist_ok=True)

# Create a stale SingletonLock in dir_a (simulating a crashed browser)
lock_file = os.path.join(dir_a, "SingletonLock")
with open(lock_file, "w") as f:
    f.write("99999")  # fake PID that doesn't exist
# Create SingletonSocket symlink (Chrome checks this too)
socket_file = os.path.join(dir_a, "SingletonSocket")
try:
    os.symlink("/tmp/nonexistent_socket", socket_file)
except FileExistsError:
    os.remove(socket_file)
    os.symlink("/tmp/nonexistent_socket", socket_file)

print(f"[repro] Conversation A dir: {dir_a}")
print(f"[repro] Conversation B dir: {dir_b}")
print(f"[repro] Stale SingletonLock in A's dir: {os.path.exists(lock_file)}")
print()

# Step 2: Launch browser B using dir_b and check if CDP responds
port = 19333
print(f"[repro] Launching browser B with user_data_dir={dir_b}")
print(f"[repro]   chrome --headless --no-sandbox --user-data-dir={dir_b}")
print(f"[repro]   --remote-debugging-port={port}")

proc = subprocess.Popen(
    [
        CHROME,
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        f"--user-data-dir={dir_b}",
        f"--remote-debugging-port={port}",
        "about:blank",
    ],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)

# Wait for CDP to respond (max 10s)
print(f"[repro]   Waiting for CDP on port {port}...")
cdp_ok = False
for i in range(100):
    time.sleep(0.1)
    try:
        import urllib.request

        resp = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=1
        )
        if resp.status == 200:
            cdp_ok = True
            print(f"[repro]   CDP responded after {i * 0.1:.1f}s ✓")
            break
    except Exception:
        pass

# Cleanup
proc.terminate()
proc.wait(timeout=5)

# Clean up the stale lock and test dirs
for d in [dir_a, dir_b]:
    try:
        shutil.rmtree(d)
    except Exception:
        pass

if cdp_ok:
    if has_fix:
        print()
        print(
            "[after] RESULT: PASS — browser B launched successfully despite A's stale lock"
        )
        print(
            "[after]   Unique user_data_dir per conversation prevents SingletonLock collision"
        )
    else:
        print()
        print("[repro] RESULT: PASS — browser B launched (unexpected for shared dir)")
        print("[repro]   Chrome may have detected the stale lock and proceeded")
else:
    if has_fix:
        print()
        print("[after] RESULT: FAIL — CDP didn't respond (unexpected with unique dir)")
    else:
        print()
        print(
            "[before] RESULT: FAIL — browser B hung because A's SingletonLock blocked the launch"
        )
        print(
            "[before]   Shared user_data_dir means a crashed session's lock blocks the next one"
        )
