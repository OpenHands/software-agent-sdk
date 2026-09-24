import io
import logging

from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal


def test_tmux_terminal_redacts_secrets_from_libtmux_child_logger(tmp_path):
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    libtmux_logger = logging.getLogger("libtmux")
    libtmux_loggers = [
        libtmux_logger,
        *(
            candidate
            for name, candidate in logging.Logger.manager.loggerDict.items()
            if name.startswith("libtmux.") and isinstance(candidate, logging.Logger)
        ),
    ]
    original_filters = {logger: logger.filters[:] for logger in libtmux_loggers}
    original_handlers = libtmux_logger.handlers[:]
    original_propagate = libtmux_logger.propagate

    libtmux_logger.handlers = [handler]
    libtmux_logger.propagate = False
    try:
        TmuxTerminal(work_dir=str(tmp_path))
        logging.getLogger("libtmux.common").error("tmux send-keys %s", secret)

        assert secret not in stream.getvalue()
        assert "<redacted>" in stream.getvalue()
    finally:
        for logger, filters in original_filters.items():
            logger.filters = filters
        libtmux_logger.handlers = original_handlers
        libtmux_logger.propagate = original_propagate
