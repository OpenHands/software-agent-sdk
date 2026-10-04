"""Explicitly loaded by the logging regression subprocess, never by production.

Inject the reported exception shape at the service boundary; HTTP, WebSocket,
logging configuration and signal handling use the stock server entry point.
"""

import logging
from unittest.mock import patch

from openhands.agent_server.event_service import EventService
from openhands.sdk import Message, TextContent


async def fail_message(
    self: EventService,
    message: Message,
    run: bool = False,
    _from_goal_loop: bool = False,
):
    error = RuntimeError("injected cyclic exception")
    error.__context__ = error
    if any(
        isinstance(part, TextContent) and part.text == "handler"
        for part in message.content
    ):
        logging.getLogger(__name__).error(
            "handler probe", exc_info=(type(error), error, None)
        )
    raise error


patch.object(EventService, "send_message", fail_message).start()
