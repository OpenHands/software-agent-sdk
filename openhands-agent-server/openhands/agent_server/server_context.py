"""Everything a running agent server derives from its configuration.

A server has exactly one :class:`ServerContext` at a time. It is built once per
(re)initialisation -- when the app is created, when its lifespan starts the
services, and again when ``POST /api/init`` delivers the runtime configuration
of a deferred-init server -- and installed on the app with a single assignment,
so a request sees either the previous context or the next one, never a mix of
the two.

Request handlers reach it through the dependencies in
:mod:`openhands.agent_server.dependencies`. Code that runs outside a request is
handed the config, store or service it needs by whoever built the context;
there is no process-wide default to fall back to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from starlette.applications import Starlette
from starlette.requests import HTTPConnection

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_registry import (
    ConversationRegistry,
    create_conversation_registry,
)
from openhands.agent_server.persistence import ServerStores


# The services import modules that read the context, so they are named here
# for typing only.
if TYPE_CHECKING:
    from openhands.agent_server.bash_service import BashEventService
    from openhands.agent_server.conversation_service import ConversationService


@dataclass(frozen=True, slots=True)
class ServerContext:
    """The config of one server (re)initialisation and what was built from it."""

    config: Config
    stores: ServerStores
    conversation_registry: ConversationRegistry
    conversation_service: ConversationService | None = None
    """``None`` until the services are started: before the lifespan runs, and
    for as long as a deferred-init server is dormant."""
    bash_event_service: BashEventService | None = None
    """``None`` under the same conditions as ``conversation_service``."""


def build_server_context(config: Config) -> ServerContext:
    """Build the context for ``config`` with no service started yet."""
    stores = ServerStores.from_config(config)
    return ServerContext(
        config=config,
        stores=stores,
        conversation_registry=create_conversation_registry(config, stores),
    )


def install_server_context(app: Starlette, context: ServerContext) -> None:
    """Make ``context`` the one every later request to ``app`` sees."""
    app.state.server_context = context


def server_context_of(app: Starlette) -> ServerContext:
    """Return the context ``app`` currently runs with."""
    try:
        context = app.state.server_context
    except AttributeError:
        raise RuntimeError(
            "This app has no ServerContext; build it with create_app() or "
            "install one with install_server_context()"
        ) from None
    assert isinstance(context, ServerContext)
    return context


def get_server_context(connection: HTTPConnection) -> ServerContext:
    """Return the context of the app serving this request or WebSocket."""
    return server_context_of(connection.app)
