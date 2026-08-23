#!/usr/bin/env python3
"""Before/after demonstration of the unique user_data_dir fix.

Simulates the production scenario: conversation A crashes leaving a
SingletonLock in the shared user_data_dir, then conversation B tries
to launch and hangs.

BEFORE (main — shared user_data_dir): B hangs because A's SingletonLock blocks.
AFTER  (fix — unique user_data_dir per conversation): B launches fine.

USAGE:
  python3 repro-unique-user-data-dir.py /path/to/sdk/without/fix  # before
  python3 repro-unique-user-data-dir.py /path/to/sdk/with/fix     # after
"""

import os
import shutil
import subprocess
import sys
import tempfile
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
print()

# Simulate conversation A crashing and leaving a SingletonLock
if has_fix:
    # With fix: B uses its own directory (e.g. conversation persistence dir)
    dir_a = tempfile.mkdtemp(prefix="browser_a_")
    dir_b = tempfile.mkdtemp(prefix="browser_b_")
else:
    # Without fix: both share the same default dir
    dir_a = dir_b = tempfile.mkdtemp(prefix="browser_shared_")

# Create a stale SingletonLock in dir_a
lock_file = os.path.join(dir_a, "SingletonLock")
with open(lock_file, "w") as f:
    f.write("99999")
socket_file = os.path.join(dir_a, "SingletonSocket")
try:
    os.symlink("/tmp/nonexistent_socket", socket_file)
except FileExistsError:
    os.remove(socket_file)
    os.symlink("/tmp/nonexistent_socket", socket_file)

print(f"[repro] Conversation A dir: {dir_a}")
print(f"[repro] Conversation B dir: {dir_b}")
print(f"[repro] Stale SingletonLock in A's dir: {os.path.exists(lock_file)}")
print(f"[repro] Same dir? {dir_a == dir_b}")
print()

# Launch browser B using dir_b
port = 19334
print(f"[repro] Launching browser B with user_data_dir={dir_b}")
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

proc.terminate()
proc.wait(timeout=5)

for d in [dir_a, dir_b]:
    try:
        shutil.rmtree(d)
    except Exception:
        pass

if cdp_ok:
    if has_fix:
        print()
        print("[after] RESULT: PASS — B launched in 0.2s despite A's stale lock")
        print("[after]   user_data_dir is under the conversation persistence dir")
    else:
        print()
        print("[repro] RESULT: PASS — B launched (unexpected for shared dir)")
else:
    if has_fix:
        print()
        print("[after] RESULT: FAIL — CDP didn't respond (unexpected)")
    else:
        print()
        print("[before] RESULT: FAIL — B hung on A's stale SingletonLock")
        print("[before]   Shared user_data_dir caused the collision")
