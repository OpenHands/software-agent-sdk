from threading import Event

from flight_recorder.gui.workers import TaskWorker


def test_task_worker_emits_result_off_thread(qtbot) -> None:
    worker = TaskWorker(lambda cancelled: 42)

    with qtbot.waitSignal(worker.succeeded, timeout=1000) as signal:
        worker.start()

    assert signal.args == [42]
    worker.wait()


def test_task_worker_cancellation_suppresses_result(qtbot) -> None:
    started = Event()
    release = Event()
    results = []

    def work(cancelled):
        started.set()
        release.wait(1)
        return "ignored"

    worker = TaskWorker(work)
    worker.succeeded.connect(results.append)
    worker.start()
    assert started.wait(1)
    worker.cancel()
    release.set()
    assert worker.wait(1000)
    assert results == []
