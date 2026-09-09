from flight_recorder.gui.main_window import MainWindow
from PySide6.QtWidgets import QSplitter


def test_main_window_has_three_panel_workspace(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.windowTitle() == "Agent Flight Recorder"
    workspace = window.centralWidget()
    assert isinstance(workspace, QSplitter)
    assert workspace.count() == 3
