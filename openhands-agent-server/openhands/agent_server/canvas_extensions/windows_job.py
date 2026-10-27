"""Windows Job Object ownership for Canvas App backend process trees.

Linux backends are owned as a POSIX process group. On native Windows the
equivalent is a Job Object created with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``:

* ``stop``, disable, uninstall, and a graceful Agent Server shutdown call
  ``TerminateJobObject``, which kills the leader and every descendant assigned
  to the job.
* If the Agent Server process exits without teardown, the last handle to the
  job is closed and Windows kills the tree automatically. ``reap_orphans``
  then only has to drop stale run records, and must still refuse to signal a
  process whose identity does not match the record (PID reuse / forged PIDs).

Job assignment can fail when the Agent Server itself runs inside a job that
forbids nesting. In that case stop/recovery fall back to a Toolhelp32 walk of
the still-living leader's descendants. A process is never terminated solely
because its PID matches a persisted record.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final


if os.name == "nt":
    import ctypes
    from ctypes import wintypes


_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: Final[int] = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: Final[int] = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION: Final[int] = 1
_PROCESS_TERMINATE: Final[int] = 0x0001
_PROCESS_SET_QUOTA: Final[int] = 0x0100
_PROCESS_QUERY_INFORMATION: Final[int] = 0x0400
_PROCESS_QUERY_LIMITED_INFORMATION: Final[int] = 0x1000
_PROCESS_VM_READ: Final[int] = 0x0010
_TH32CS_SNAPPROCESS: Final[int] = 0x00000002
_INVALID_HANDLE_VALUE: Final[int] = -1

_WINDOWS_HOST_ENV: Final[tuple[str, ...]] = (
    "SYSTEMROOT",
    "WINDIR",
    "SYSTEMDRIVE",
    "COMSPEC",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "NUMBER_OF_PROCESSORS",
)


def base_environment(home: Path) -> dict[str, str]:
    """Host Win32 variables a native process needs, plus a sandboxed profile.

    ``SYSTEMROOT`` and friends are not secrets and are not optional on Windows:
    ``CreateProcess`` of a PE typically fails or misbehaves without them.
    ``USERPROFILE`` / ``TEMP`` / ``TMP`` point at the backend data dir so the
    process does not write into the real user profile.
    """
    environment = {
        name: os.environ[name] for name in _WINDOWS_HOST_ENV if name in os.environ
    }
    tmp = home / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    environment["USERPROFILE"] = str(home)
    environment["HOMEDRIVE"] = os.path.splitdrive(str(home))[0] or "C:"
    environment["HOMEPATH"] = os.path.splitdrive(str(home))[1] or "\\"
    environment["TEMP"] = str(tmp)
    environment["TMP"] = str(tmp)
    return environment


def path_under_prefix(value: str, prefix: Path) -> bool:
    """True if *value* is *prefix* or a path beneath it (any slash style)."""
    if not value:
        return False
    try:
        root = os.path.normcase(os.path.normpath(str(prefix.resolve())))
        candidate = os.path.normcase(os.path.normpath(value.strip().strip("\"'")))
    except (OSError, ValueError):
        return False
    return candidate == root or candidate.startswith(root + os.sep)


def command_mentions_prefix(command: str, prefix: Path) -> bool:
    """True if a Windows command line refers to a path under *prefix*."""
    if not command:
        return False
    try:
        root = os.path.normcase(os.path.normpath(str(prefix.resolve())))
    except (OSError, ValueError):
        return False
    normalized = os.path.normcase(command.replace("/", "\\"))
    return root in normalized


if os.name != "nt":

    class WindowsJob:
        """Placeholder so non-Windows importers can annotate without ctypes."""

        def terminate(self) -> None:
            return None

        def close(self) -> None:
            return None

        def active_processes(self) -> int:
            return 0

    def try_assign_job(pid: int) -> WindowsJob | None:
        del pid
        return None

    def process_start_time(pid: int) -> int | None:
        del pid
        return None

    def process_image_path(pid: int) -> str | None:
        del pid
        return None

    def process_command_line(pid: int) -> str | None:
        del pid
        return None

    def pid_alive(pid: int) -> bool:
        del pid
        return False

    def terminate_pid(pid: int) -> None:
        del pid

    def terminate_tree(pid: int) -> None:
        del pid

    def descendant_pids(root_pid: int) -> list[int]:
        del root_pid
        return []

else:

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", wintypes.LARGE_INTEGER),
            ("TotalKernelTime", wintypes.LARGE_INTEGER),
            ("ThisPeriodTotalUserTime", wintypes.LARGE_INTEGER),
            ("ThisPeriodTotalKernelTime", wintypes.LARGE_INTEGER),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class _PROCESS_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("Reserved1", ctypes.c_void_p),
            ("PebBaseAddress", ctypes.c_void_p),
            ("Reserved2", ctypes.c_void_p * 2),
            ("UniqueProcessId", ctypes.c_void_p),
            ("Reserved3", ctypes.c_void_p),
        ]

    class _UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", ctypes.c_void_p),
        ]

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")

    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    _kernel32.ReadProcessMemory.restype = wintypes.BOOL
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
    _ntdll.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    ]
    _ntdll.NtQueryInformationProcess.restype = ctypes.c_long

    def _open_process(pid: int, access: int) -> int:
        handle = _kernel32.OpenProcess(access, False, pid)
        return int(handle) if handle else 0

    def _close(handle: int) -> None:
        if handle:
            _kernel32.CloseHandle(handle)

    class WindowsJob:
        """One unnamed Job Object that owns a backend process tree."""

        def __init__(self, handle: int) -> None:
            self._handle = handle

        @classmethod
        def create(cls) -> WindowsJob | None:
            handle = _kernel32.CreateJobObjectW(None, None)
            if not handle:
                return None
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not _kernel32.SetInformationJobObject(
                handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                _close(int(handle))
                return None
            return cls(int(handle))

        def assign(self, pid: int) -> bool:
            access = (
                _PROCESS_SET_QUOTA
                | _PROCESS_TERMINATE
                | _PROCESS_QUERY_LIMITED_INFORMATION
            )
            process = _open_process(pid, access)
            if not process:
                return False
            try:
                return bool(_kernel32.AssignProcessToJobObject(self._handle, process))
            finally:
                _close(process)

        def terminate(self) -> None:
            if self._handle:
                _kernel32.TerminateJobObject(self._handle, 1)

        def active_processes(self) -> int:
            if not self._handle:
                return 0
            info = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
            returned = wintypes.DWORD(0)
            if not _kernel32.QueryInformationJobObject(
                self._handle,
                _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
                ctypes.byref(returned),
            ):
                return 0
            return int(info.ActiveProcesses)

        def close(self) -> None:
            handle = self._handle
            self._handle = 0
            _close(handle)

    def try_assign_job(pid: int) -> WindowsJob | None:
        job = WindowsJob.create()
        if job is None:
            return None
        if job.assign(pid):
            return job
        job.close()
        return None

    def process_start_time(pid: int) -> int | None:
        handle = _open_process(pid, _PROCESS_QUERY_LIMITED_INFORMATION)
        if not handle:
            return None
        try:
            code = wintypes.DWORD()
            if (
                not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                or int(code.value) != 259
            ):
                return None
            creation = wintypes.FILETIME()
            dummy = wintypes.FILETIME()
            if not _kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(dummy),
                ctypes.byref(dummy),
                ctypes.byref(dummy),
            ):
                return None
            return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        finally:
            _close(handle)

    def process_image_path(pid: int) -> str | None:
        handle = _open_process(pid, _PROCESS_QUERY_LIMITED_INFORMATION)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            if not _kernel32.QueryFullProcessImageNameW(
                handle, 0, buf, ctypes.byref(size)
            ):
                return None
            return buf.value or None
        finally:
            _close(handle)

    def process_command_line(pid: int) -> str | None:
        """Read another process's command line from its PEB (x64 only)."""
        handle = _open_process(
            pid,
            _PROCESS_QUERY_INFORMATION
            | _PROCESS_VM_READ
            | _PROCESS_QUERY_LIMITED_INFORMATION,
        )
        if not handle:
            return None
        try:
            pbi = _PROCESS_BASIC_INFORMATION()
            status = _ntdll.NtQueryInformationProcess(
                handle, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None
            )
            if status != 0 or not pbi.PebBaseAddress:
                return None
            params_ptr = ctypes.c_void_p()
            nread = ctypes.c_size_t()
            peb = int(pbi.PebBaseAddress)
            if not _kernel32.ReadProcessMemory(
                handle,
                peb + 0x20,
                ctypes.byref(params_ptr),
                ctypes.sizeof(params_ptr),
                ctypes.byref(nread),
            ):
                return None
            if not params_ptr.value:
                return None
            us = _UNICODE_STRING()
            if not _kernel32.ReadProcessMemory(
                handle,
                int(params_ptr.value) + 0x70,
                ctypes.byref(us),
                ctypes.sizeof(us),
                ctypes.byref(nread),
            ):
                return None
            if not us.Buffer or us.Length == 0:
                return None
            buf = ctypes.create_unicode_buffer(us.Length // 2 + 1)
            if not _kernel32.ReadProcessMemory(
                handle,
                us.Buffer,
                buf,
                us.Length,
                ctypes.byref(nread),
            ):
                return None
            return buf.value or None
        finally:
            _close(handle)

    def pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        handle = _open_process(pid, _PROCESS_QUERY_LIMITED_INFORMATION)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return int(code.value) == 259  # STILL_ACTIVE
        finally:
            _close(handle)

    def terminate_pid(pid: int) -> None:
        handle = _open_process(pid, _PROCESS_TERMINATE)
        if not handle:
            return
        try:
            _kernel32.TerminateProcess(handle, 1)
        finally:
            _close(handle)

    def _snapshot_parents() -> dict[int, int]:
        snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if snapshot == _INVALID_HANDLE_VALUE or not snapshot:
            return {}
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            parents: dict[int, int] = {}
            if not _kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                return {}
            while True:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                if not _kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
            return parents
        finally:
            _close(int(snapshot))

    def descendant_pids(root_pid: int) -> list[int]:
        parents = _snapshot_parents()
        children: dict[int, list[int]] = {}
        for pid, parent in parents.items():
            children.setdefault(parent, []).append(pid)
        found: list[int] = []
        stack = list(children.get(root_pid, ()))
        while stack:
            pid = stack.pop()
            if pid in found or pid == root_pid:
                continue
            found.append(pid)
            stack.extend(children.get(pid, ()))
        return found

    def terminate_tree(pid: int) -> None:
        """Kill *pid* and its current descendants. No-op if *pid* is gone.

        Descendants are taken from a live Toolhelp32 walk of this leader. A
        dead leader is not used as a parent key: Windows reparents orphans,
        and a recycled PID must not pull in unrelated processes.
        """
        if pid_alive(pid):
            for child in reversed(descendant_pids(pid)):
                terminate_pid(child)
        terminate_pid(pid)
