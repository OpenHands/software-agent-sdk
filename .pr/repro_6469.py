"""Unit-level simulation of SDK#6469 on Linux.

Windows semantics emulated: CloseHandle() on a pipe blocks while another thread
is inside a synchronous ReadFile() on it. On Linux close() would return at once,
so stdout is wrapped in a pipe object that blocks close() while a read is pending.
"""
import importlib.util, os, subprocess, sys, threading, time
import psutil

src = sys.argv[1]
spec = importlib.util.spec_from_file_location(
    "windows_terminal", os.path.join(src, "openhands-tools/openhands/tools/terminal/terminal/windows_terminal.py"))
wt = importlib.util.module_from_spec(spec); spec.loader.exec_module(wt)

class WindowsLikePipe:
    def __init__(self, raw):
        self._raw, self._reading = raw, threading.Condition()
        self._pending = 0
    def read(self, n):
        with self._reading: self._pending += 1
        try: return self._raw.read(n)
        finally:
            with self._reading: self._pending -= 1; self._reading.notify_all()
    def close(self):
        with self._reading:  # CloseHandle waits for the in-flight ReadFile
            while self._pending: self._reading.wait()
        self._raw.close()
    def __getattr__(self, k): return getattr(self._raw, k)

RealPopen = subprocess.Popen
class Popen(RealPopen):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw); self.stdout = WindowsLikePipe(self.stdout)
wt.subprocess.Popen = Popen

term = wt.WindowsTerminal(work_dir="/tmp", shell_path=os.path.abspath("fake_pwsh.py"))
term.initialize()
pid = term.process.pid
time.sleep(0.5)
print(f"powershell stand-in pid={pid} running a 60 s foreground command; reader thread alive={term.reader_thread.is_alive()}")
t0 = time.time(); done = threading.Event()
threading.Thread(target=lambda: (term.close(), done.set()), daemon=True).start()
ok = done.wait(15)
dt = time.time() - t0
alive = psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
if ok:
    print(f"terminal.close() returned after {dt:.2f}s; shell process alive={alive}")
else:
    print(f"terminal.close() STILL BLOCKED after {dt:.0f}s (deadlocked in stdout.close()); shell process alive={alive}")
    import faulthandler; faulthandler.dump_traceback(all_threads=True)
    os.kill(pid, 9)
os._exit(0 if ok and not alive else 1)
