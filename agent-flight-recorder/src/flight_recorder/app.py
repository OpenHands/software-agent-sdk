"""PySide6 desktop application bootstrap."""

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from flight_recorder.config import RecorderPaths
from flight_recorder.gui.main_window import MainWindow
from flight_recorder.gui.workers import BundleWorker
from flight_recorder.models.database import TraceIndex
from flight_recorder.services.bundles import BundleService
from flight_recorder.services.repository import TraceRepository


def run(trace: Path | None = None) -> int:
    application = QApplication.instance() or QApplication([])
    application.setOrganizationName("OpenHands")
    application.setApplicationName("Agent Flight Recorder")
    window = MainWindow()
    window.restore_settings(QSettings())
    if trace is not None:
        paths = RecorderPaths.defaults()
        paths.create()
        index = TraceIndex(paths.index)
        repository = TraceRepository(index)
        worker = BundleWorker(BundleService(index, paths.traces), trace)
        worker.succeeded.connect(
            lambda trace_id: window.set_repository(repository, str(trace_id))
        )
        worker.failed.connect(lambda message: window.statusBar().showMessage(message))

        def detach() -> None:
            worker.cancel()
            worker.wait(1000)

        window.add_detach_callback(detach)
        worker.start()
    window.show()
    return application.exec()
