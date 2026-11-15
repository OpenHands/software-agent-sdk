#!/usr/bin/env python3
"""Stand-in for powershell.exe: runs a foreground command that never yields output."""
import sys, time
sys.stdout.write("PS> "); sys.stdout.flush()
time.sleep(60)  # like `Start-Sleep -Seconds 60`: busy, writes nothing, reads no stdin
