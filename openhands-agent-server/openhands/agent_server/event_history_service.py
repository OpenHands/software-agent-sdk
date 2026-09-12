import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from openhands.agent_server.models import EventPage, EventSortOrder
from openhands.sdk import Event
from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.event.llm_convertible.message import MessageEvent
from openhands.sdk.io import LocalFileStore
from openhands.sdk.llm.message import content_to_str


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventHistoryService:
    """Read retained conversation events without activating a runtime."""

    events: EventLog
    conversation_id: str

    @classmethod
    def from_conversation_dir(cls, conversation_dir: Path) -> "EventHistoryService":
        return cls(
            events=EventLog(LocalFileStore(str(conversation_dir))),
            conversation_id=conversation_dir.name,
        )

    def _get_event_sync(self, event_id: str) -> Event | None:
        try:
            return self.events[self.events.get_index(event_id)]
        except KeyError:
            return None

    async def get_event(self, event_id: str) -> Event | None:
        return await asyncio.to_thread(self._get_event_sync, event_id)

    def _event_matches_body(self, event: Event, body: str) -> bool:
        if not isinstance(event, MessageEvent):
            return False
        text_parts = content_to_str(event.llm_message.content)
        if event.extended_content:
            text_parts.extend(content_to_str(event.extended_content))
        if event.reasoning_content:
            text_parts.append(event.reasoning_content)
        return body.lower() in " ".join(text_parts).lower()

    def _event_matches_filters(
        self,
        event: Event,
        kind: str | None,
        source: str | None,
        body: str | None,
        timestamp_gte: str | None,
        timestamp_lt: str | None,
    ) -> bool:
        return not (
            (
                kind is not None
                and f"{event.__class__.__module__}.{event.__class__.__name__}" != kind
            )
            or (source is not None and event.source != source)
            or (timestamp_gte is not None and event.timestamp < timestamp_gte)
            or (timestamp_lt is not None and event.timestamp >= timestamp_lt)
            or (body is not None and not self._event_matches_body(event, body))
        )

    def _read_event(self, index: int) -> Event | None:
        try:
            return self.events[index]
        except (FileNotFoundError, UnicodeDecodeError, ValidationError) as exc:
            logger.warning(
                "Skipping unreadable event at index %d for conversation %s (%s)",
                index,
                self.conversation_id,
                type(exc).__name__,
            )
            return None

    def _search_events_sync(
        self,
        page_id: str | None,
        limit: int,
        kind: str | None,
        source: str | None,
        body: str | None,
        sort_order: EventSortOrder,
        timestamp_gte: datetime | None,
        timestamp_lt: datetime | None,
    ) -> EventPage:
        total = len(self.events)
        start_index: int | None = None
        if page_id:
            try:
                start_index = self.events.get_index(page_id)
            except KeyError:
                pass

        reverse = sort_order == EventSortOrder.TIMESTAMP_DESC
        if start_index is None:
            start_index = total - 1 if reverse else 0
        indices = range(start_index, -1, -1) if reverse else range(start_index, total)
        timestamp_gte_str = timestamp_gte.isoformat() if timestamp_gte else None
        timestamp_lt_str = timestamp_lt.isoformat() if timestamp_lt else None

        items: list[Event] = []
        next_page_id: str | None = None
        for index in indices:
            event = self._read_event(index)
            if event is None or not self._event_matches_filters(
                event,
                kind,
                source,
                body,
                timestamp_gte_str,
                timestamp_lt_str,
            ):
                continue
            if len(items) >= limit:
                next_page_id = event.id
                break
            items.append(event)
        return EventPage(items=items, next_page_id=next_page_id)

    async def search_events(
        self,
        page_id: str | None = None,
        limit: int = 100,
        kind: str | None = None,
        source: str | None = None,
        body: str | None = None,
        sort_order: EventSortOrder = EventSortOrder.TIMESTAMP,
        timestamp__gte: datetime | None = None,
        timestamp__lt: datetime | None = None,
    ) -> EventPage:
        return await asyncio.to_thread(
            self._search_events_sync,
            page_id,
            limit,
            kind,
            source,
            body,
            sort_order,
            timestamp__gte,
            timestamp__lt,
        )

    def _count_events_sync(
        self,
        kind: str | None,
        source: str | None,
        body: str | None,
        timestamp_gte: datetime | None,
        timestamp_lt: datetime | None,
    ) -> int:
        timestamp_gte_str = timestamp_gte.isoformat() if timestamp_gte else None
        timestamp_lt_str = timestamp_lt.isoformat() if timestamp_lt else None
        return sum(
            event is not None
            and self._event_matches_filters(
                event,
                kind,
                source,
                body,
                timestamp_gte_str,
                timestamp_lt_str,
            )
            for event in (self._read_event(index) for index in range(len(self.events)))
        )

    async def count_events(
        self,
        kind: str | None = None,
        source: str | None = None,
        body: str | None = None,
        timestamp__gte: datetime | None = None,
        timestamp__lt: datetime | None = None,
    ) -> int:
        return await asyncio.to_thread(
            self._count_events_sync,
            kind,
            source,
            body,
            timestamp__gte,
            timestamp__lt,
        )

    async def batch_get_events(self, event_ids: list[str]) -> list[Event | None]:
        return await asyncio.gather(
            *(self.get_event(event_id) for event_id in event_ids)
        )
