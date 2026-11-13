"""Windows kernel ownership for one App backend and all its descendants."""

import ctypes
import os
from typing import Protocol


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", ctypes.c_uint32),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_process_limit", ctypes.c_uint32),
        ("affinity", ctypes.c_size_t),
        ("priority", ctypes.c_uint32),
        ("scheduling", ctypes.c_uint32),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits),
        ("io_counters", ctypes.c_uint64 * 6),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("user_time", ctypes.c_int64),
        ("kernel_time", ctypes.c_int64),
        ("period_user_time", ctypes.c_int64),
        ("period_kernel_time", ctypes.c_int64),
        ("page_faults", ctypes.c_uint32),
        ("total_processes", ctypes.c_uint32),
        ("active_processes", ctypes.c_uint32),
        ("terminated_processes", ctypes.c_uint32),
    ]


class WindowsJob(Protocol):
    def assign(self, pid: int) -> None: ...
    def active_processes(self) -> int: ...
    def terminate(self) -> None: ...
    def close(self) -> None: ...


if os.name == "nt":

    class _WindowsJob:
        """An unnamed, non-inheritable job killed when its server handle closes."""

        def __init__(self) -> None:
            self._api = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = ctypes.c_void_p
            dword = ctypes.c_uint32
            boolean = ctypes.c_int
            self._api.CreateJobObjectW.argtypes = [handle, ctypes.c_wchar_p]
            self._api.CreateJobObjectW.restype = handle
            self._api.SetInformationJobObject.argtypes = [
                handle,
                ctypes.c_int,
                handle,
                dword,
            ]
            self._api.SetInformationJobObject.restype = boolean
            self._api.QueryInformationJobObject.argtypes = [
                handle,
                ctypes.c_int,
                handle,
                dword,
                handle,
            ]
            self._api.QueryInformationJobObject.restype = boolean
            self._api.AssignProcessToJobObject.argtypes = [handle, handle]
            self._api.AssignProcessToJobObject.restype = boolean
            self._api.IsProcessInJob.argtypes = [
                handle,
                handle,
                ctypes.POINTER(boolean),
            ]
            self._api.IsProcessInJob.restype = boolean
            self._api.TerminateJobObject.argtypes = [handle, ctypes.c_uint]
            self._api.TerminateJobObject.restype = boolean
            self._api.OpenProcess.argtypes = [dword, boolean, dword]
            self._api.OpenProcess.restype = handle
            self._api.CloseHandle.argtypes = [handle]
            self._api.CloseHandle.restype = boolean
            self._handle = self._api.CreateJobObjectW(None, None)
            if not self._handle:
                raise ctypes.WinError(ctypes.get_last_error())
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self._api.SetInformationJobObject(
                self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                error = ctypes.WinError(ctypes.get_last_error())
                self.close()
                raise error

        def assign(self, pid: int) -> None:
            """Assign the waiting launcher; fail closed when nesting is prohibited."""
            process = self._api.OpenProcess(0x0100 | 0x0001 | 0x1000, False, pid)
            if not process:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                assigned = ctypes.c_int()
                if not self._api.IsProcessInJob(
                    process, self._handle, ctypes.byref(assigned)
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                if assigned.value:
                    return
                if not self._api.AssignProcessToJobObject(self._handle, process):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                self._api.CloseHandle(process)

        def active_processes(self) -> int:
            if not self._handle:
                return 0
            info = _Accounting()
            if not self._api.QueryInformationJobObject(
                self._handle, 1, ctypes.byref(info), ctypes.sizeof(info), None
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            return int(info.active_processes)

        def terminate(self) -> None:
            if self._handle and not self._api.TerminateJobObject(self._handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())

        def close(self) -> None:
            if self._handle:
                if not self._api.CloseHandle(self._handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                self._handle = None


def create_windows_job() -> WindowsJob:
    if os.name != "nt":
        raise RuntimeError("Windows Job Objects require Windows")
    return _WindowsJob()
