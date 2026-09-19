"""Win32 plumbing for the PowerShell terminal backend.

Three things the backend needs that ``subprocess`` does not provide on
Windows:

* **Job objects** — the shell and everything it spawns are placed in a job
  with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``.  That gives an exact,
  race-free view of the process tree (``job_pids``) and an atomic teardown
  (``terminate``) that cannot leak grandchildren, and it also ties the
  tree's lifetime to the agent process.
* **Console Ctrl+C delivery** — ``GenerateConsoleCtrlEvent`` only reaches
  processes attached to the *caller's* console, and ``CTRL_C_EVENT`` can
  only be broadcast, never targeted at a process group.  The shell runs on
  its own hidden console (``CREATE_NO_WINDOW``), so a short-lived helper
  process attaches to that console, ignores the event itself and broadcasts
  ``CTRL_C_EVENT`` there.  Only the shell and its descendants share that
  console, which makes this the Windows equivalent of ``killpg(SIGINT)``.
* **Process termination** without spawning a PowerShell/WMI query.

Everything here degrades gracefully: on failure the functions return
``False``/empty and the caller falls back to coarser behaviour.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

IS_WINDOWS = sys.platform == "win32"

# Source of the Ctrl+C helper.  It is executed with ``python -I -c`` so it
# never imports anything from the SDK and starts in well under 100 ms.
CTRL_C_HELPER_SOURCE = (
    "import ctypes,sys\n"
    "k=ctypes.WinDLL('kernel32',use_last_error=True)\n"
    "k.AttachConsole.argtypes=[ctypes.c_uint32]\n"
    "k.SetConsoleCtrlHandler.argtypes=[ctypes.c_void_p,ctypes.c_int]\n"
    "k.GenerateConsoleCtrlEvent.argtypes=[ctypes.c_uint32,ctypes.c_uint32]\n"
    "pid=int(sys.argv[1])\n"
    "k.FreeConsole()\n"
    "if not k.AttachConsole(pid):\n"
    "    sys.exit(2)\n"
    "k.SetConsoleCtrlHandler(None,1)\n"
    "ok=k.GenerateConsoleCtrlEvent(0,0)\n"
    "k.FreeConsole()\n"
    "sys.exit(0 if ok else 3)\n"
)

if IS_WINDOWS:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _PROCESS_TERMINATE = 0x0001
    _TH32CS_SNAPPROCESS = 0x00000002
    _INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
    _MAX_JOB_PIDS = 1024

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [  # noqa: RUF012
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [  # noqa: RUF012
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [  # noqa: RUF012
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _JOBOBJECT_BASIC_PROCESS_ID_LIST(ctypes.Structure):
        _fields_ = [  # noqa: RUF012
            ("NumberOfAssignedProcesses", wintypes.DWORD),
            ("NumberOfProcessIdsInList", wintypes.DWORD),
            ("ProcessIdList", ctypes.c_size_t * _MAX_JOB_PIDS),
        ]

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [  # noqa: RUF012
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_PROCESSENTRY32W),
    ]
    _kernel32.Process32FirstW.restype = wintypes.BOOL
    _kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_PROCESSENTRY32W),
    ]
    _kernel32.Process32NextW.restype = wintypes.BOOL


class WindowsJob:
    """A job object that owns a process tree and kills it when closed."""

    def __init__(self) -> None:
        self._handle: int | None = None
        if not IS_WINDOWS:
            return
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            logger.warning("CreateJobObject failed: %s", ctypes.get_last_error())
            return
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            logger.warning(
                "SetInformationJobObject failed: %s", ctypes.get_last_error()
            )
            _kernel32.CloseHandle(handle)
            return
        self._handle = handle

    @property
    def active(self) -> bool:
        return self._handle is not None

    def assign(self, process_handle: int) -> bool:
        """Put *process_handle* (a ``Popen._handle``) and its future children in the job."""  # noqa: E501
        if self._handle is None:
            return False
        if not _kernel32.AssignProcessToJobObject(self._handle, process_handle):
            logger.warning(
                "AssignProcessToJobObject failed: %s", ctypes.get_last_error()
            )
            return False
        return True

    def pids(self) -> list[int] | None:
        """Return every pid currently in the job, or ``None`` if unavailable."""
        if self._handle is None:
            return None
        info = _JOBOBJECT_BASIC_PROCESS_ID_LIST()
        ok = _kernel32.QueryInformationJobObject(
            self._handle,
            _JOB_OBJECT_BASIC_PROCESS_ID_LIST,
            ctypes.byref(info),
            ctypes.sizeof(info),
            None,
        )
        if not ok:
            return None
        return [
            int(info.ProcessIdList[i]) for i in range(info.NumberOfProcessIdsInList)
        ]

    def terminate(self) -> bool:
        """Kill every process in the job at once."""
        if self._handle is None:
            return False
        return bool(_kernel32.TerminateJobObject(self._handle, 1))

    def close(self) -> None:
        if self._handle is not None:
            _kernel32.CloseHandle(self._handle)
            self._handle = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def terminate_pid(pid: int) -> bool:
    """TerminateProcess(pid).  Returns False if the process is gone or protected."""
    if not IS_WINDOWS:
        return False
    handle = _kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(_kernel32.TerminateProcess(handle, 1))
    finally:
        _kernel32.CloseHandle(handle)


def snapshot_processes() -> list[tuple[int, int, str]]:
    """Return ``(pid, parent_pid, exe_name)`` for every process (Toolhelp32)."""
    if not IS_WINDOWS:
        return []
    snap = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snap == _INVALID_HANDLE_VALUE or not snap:
        return []
    entries: list[tuple[int, int, str]] = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            entries.append(
                (
                    int(entry.th32ProcessID),
                    int(entry.th32ParentProcessID),
                    entry.szExeFile,
                )
            )
            ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snap)
    return entries


def descendant_pids(root_pid: int) -> list[int]:
    """Descendants of *root_pid* by parent-pid walk (fallback when no job)."""
    children: dict[int, list[int]] = {}
    for pid, ppid, _name in snapshot_processes():
        children.setdefault(ppid, []).append(pid)
    result: list[int] = []
    stack = [root_pid]
    seen = {root_pid}
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            if child not in seen:
                seen.add(child)
                result.append(child)
                stack.append(child)
    return result


def send_console_ctrl_c(pid: int, timeout: float = 5.0) -> bool:
    """Broadcast CTRL_C_EVENT on the console that *pid* is attached to.

    Runs a tiny helper process (see :data:`CTRL_C_HELPER_SOURCE`).  Returns
    ``True`` when the event was generated.  A ``True`` result does not mean the
    target reacted: processes that were created with
    ``CREATE_NEW_PROCESS_GROUP`` or that ignore Ctrl+C will not.
    """
    if not IS_WINDOWS:
        return False
    python = sys.executable
    if not python:
        logger.debug("No sys.executable available for the Ctrl+C helper")
        return False
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    try:
        result = subprocess.run(
            [python, "-I", "-c", CTRL_C_HELPER_SOURCE, str(pid)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("Ctrl+C helper failed to run: %s", exc)
        return False
    if result.returncode != 0:
        logger.debug(
            "Ctrl+C helper exited with %s: %s",
            result.returncode,
            result.stderr.decode("utf-8", "replace").strip(),
        )
        return False
    return True
