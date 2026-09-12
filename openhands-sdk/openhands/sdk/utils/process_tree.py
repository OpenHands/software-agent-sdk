"""Tie a spawned process tree to a Windows Job Object so teardown reaps all of it.

``Popen.terminate()`` / ``.kill()`` map to ``TerminateProcess`` on Windows, which
acts on a single PID. There is no process-group equivalent, so anything the child
spawned below itself survives. On POSIX this is usually masked — a child dies on
the broken stdio pipe when its parent goes — but on Windows the grandchildren keep
running.

A Job Object is the OS mechanism for this. A process assigned to a job carries its
descendants into the job with it, so a job created with
``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` terminates the whole tree when its last
handle closes — including if the owning process dies without cleaning up.

Everything here is best-effort: teardown must not fail because a Win32 call did.
:meth:`ProcessTreeGuard.for_process` returns ``None`` on non-Windows platforms and
on any failure, and callers fall back to their existing single-PID terminate.
"""

from __future__ import annotations

import sys
from typing import ClassVar

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# winnt.h
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectExtendedLimitInformation = 9
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100


def _build_extended_limit_information():
    """Build ``JOBOBJECT_EXTENDED_LIMIT_INFORMATION`` with kill-on-close set.

    Declared lazily so this module stays importable on non-Windows platforms,
    where ``ctypes.wintypes`` is unavailable.
    """
    import ctypes
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t

    class _BasicLimitInformation(ctypes.Structure):
        _fields_: ClassVar = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ULONG_PTR),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_: ClassVar = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_: ClassVar = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    info = _ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    return info


class ProcessTreeGuard:
    """Owns a Windows Job Object holding one process and its descendants.

    Construct via :meth:`for_process`. :meth:`close` terminates the tree; it is
    idempotent, so callers may invoke it alongside their existing terminate/kill
    without ordering concerns.
    """

    __slots__ = ("_handle",)

    def __init__(self, handle: int) -> None:
        self._handle = handle

    @classmethod
    def for_process(cls, pid: int | None) -> ProcessTreeGuard | None:
        """Create a kill-on-close job and assign *pid* to it.

        Returns ``None`` on non-Windows platforms, when *pid* is absent, or when
        any step fails — the caller keeps whatever teardown it already had.

        Assignment happens after the process exists, which leaves a window in
        which it could spawn a child that escapes the job. In practice ACP
        servers spawn their provider CLI well after the protocol handshake, long
        past this call.
        """
        if sys.platform != "win32" or not isinstance(pid, int):
            return None

        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            logger.debug(
                "CreateJobObject failed (err=%s); process tree will not be "
                "reaped as a group",
                ctypes.get_last_error(),
            )
            return None

        try:
            info = _build_extended_limit_information()
            if not kernel32.SetInformationJobObject(
                job,
                _JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                logger.debug(
                    "SetInformationJobObject failed (err=%s)",
                    ctypes.get_last_error(),
                )
                kernel32.CloseHandle(job)
                return None

            handle = kernel32.OpenProcess(
                _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid
            )
            if not handle:
                logger.debug(
                    "OpenProcess(pid=%s) failed (err=%s)",
                    pid,
                    ctypes.get_last_error(),
                )
                kernel32.CloseHandle(job)
                return None

            try:
                if not kernel32.AssignProcessToJobObject(job, handle):
                    logger.debug(
                        "AssignProcessToJobObject(pid=%s) failed (err=%s)",
                        pid,
                        ctypes.get_last_error(),
                    )
                    kernel32.CloseHandle(job)
                    return None
            finally:
                kernel32.CloseHandle(handle)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("Job object setup failed for pid=%s: %s", pid, e)
            try:
                kernel32.CloseHandle(job)
            except Exception:
                pass
            return None

        logger.debug("Assigned pid=%s to job object for tree teardown", pid)
        return cls(job)

    def close(self) -> None:
        """Close the job handle, terminating every process still in the job."""
        handle, self._handle = self._handle, 0
        if not handle:
            return
        import ctypes

        try:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("Closing job object handle failed: %s", e)
