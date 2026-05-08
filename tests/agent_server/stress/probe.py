"""psutil-based resource sampler for stress tests.

Samples RSS, num_fds, num_threads, cpu at fixed cadence in a background asyncio
task. Diff against a baseline taken at fixture entry so budgets are relative to
warm-up, not absolute CI-runner constants.
"""

import asyncio
import contextlib
import os
import time
from dataclasses import dataclass, field
from typing import Self

import psutil


@dataclass(frozen=True, slots=True)
class Sample:
    t: float
    rss_mb: float
    num_fds: int
    num_threads: int
    cpu_percent: float


@dataclass(slots=True)
class ResourceProbe:
    interval_s: float = 0.25
    _proc: psutil.Process = field(default_factory=lambda: psutil.Process(os.getpid()))
    _samples: list[Sample] = field(default_factory=list)
    _task: asyncio.Task | None = None
    _baseline: Sample | None = None
    _start_t: float = 0.0

    async def __aenter__(self) -> Self:
        # Prime cpu_percent — first call returns 0.0.
        self._proc.cpu_percent(interval=None)
        self._start_t = time.monotonic()
        self._baseline = self._take()
        self._samples.append(self._baseline)
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._samples.append(self._take())

    async def _loop(self) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            while True:
                await asyncio.sleep(self.interval_s)
                self._samples.append(self._take())

    def _take(self) -> Sample:
        try:
            num_fds = self._proc.num_fds()
        except (AttributeError, psutil.AccessDenied):
            # psutil exposes num_fds() only on POSIX; AttributeError covers
            # Windows, AccessDenied covers sandboxed/non-owning processes.
            # -1 is the sentinel for "unavailable" — peak_fds()/fd_delta()
            # check it explicitly so FD assertions become no-ops there.
            num_fds = -1
        return Sample(
            t=time.monotonic() - self._start_t,
            rss_mb=self._proc.memory_info().rss / (1024 * 1024),
            num_fds=num_fds,
            num_threads=self._proc.num_threads(),
            cpu_percent=self._proc.cpu_percent(interval=None),
        )

    @property
    def baseline(self) -> Sample:
        assert self._baseline is not None, "ResourceProbe used outside async-with"
        return self._baseline

    @property
    def samples(self) -> list[Sample]:
        return list(self._samples)

    def peak_rss_mb(self) -> float:
        return max(s.rss_mb for s in self._samples)

    def peak_fds(self) -> int:
        """Peak FD count across samples. Returns -1 on platforms where
        psutil cannot read FDs (Windows; sandboxed processes); pair with
        ``fd_delta`` rather than asserting on this directly."""
        return max(s.num_fds for s in self._samples)

    def peak_threads(self) -> int:
        return max(s.num_threads for s in self._samples)

    def rss_delta_mb(self) -> float:
        return self.peak_rss_mb() - self.baseline.rss_mb

    def fd_delta(self) -> int:
        """Peak-minus-baseline FD growth. Returns 0 on platforms where the
        baseline read failed (-1 sentinel from ``_take``), so an
        ``fd_delta() < budget`` assertion silently passes there rather than
        firing on a missing measurement."""
        if self.baseline.num_fds < 0:
            return 0
        return self.peak_fds() - self.baseline.num_fds
