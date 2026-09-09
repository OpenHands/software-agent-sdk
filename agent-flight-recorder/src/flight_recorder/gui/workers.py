from collections.abc import Callable
from pathlib import Path

from flight_recorder.models.envelopes import CommittedBatch
from flight_recorder.services.bundles import BundleService
from flight_recorder.services.repository import TraceRepository
from flight_recorder.services.subscriptions import SubscriptionHandle, TraceSubscription
from PySide6.QtCore import QObject, QThread, Signal


class TaskWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, task: Callable[[Callable[[], bool]], object]) -> None:
        super().__init__()
        self._task = task

    def cancel(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            result = self._task(self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.succeeded.emit(result)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))


class RepositoryWorker(TaskWorker):
    def __init__(self, repository: TraceRepository, trace_id: str) -> None:
        super().__init__(lambda _cancelled: repository.get_timeline(trace_id))


class BundleWorker(TaskWorker):
    def __init__(self, service: BundleService, source: Path) -> None:
        super().__init__(lambda _cancelled: service.import_bundle(source))


class SubscriptionWorker(QObject):
    batch_received = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._handle: SubscriptionHandle | None = None

    def attach(
        self,
        service: TraceSubscription,
        trace_id: str,
        after_sequence: int,
    ) -> None:
        self.cancel()
        self._handle = service.subscribe(
            trace_id,
            after_sequence,
            self._on_batch,
        )

    def _on_batch(self, batch: CommittedBatch) -> None:
        self.batch_received.emit(batch)

    def cancel(self) -> None:
        if self._handle is not None:
            self._handle.unsubscribe()
            self._handle = None
