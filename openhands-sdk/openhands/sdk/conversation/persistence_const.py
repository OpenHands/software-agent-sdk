import re


BASE_STATE = "base_state.json"
EVENTS_DIR = "events"
# Accept 5+ digits: the writer pads to a 5-digit minimum but does not cap width.
EVENT_NAME_RE = re.compile(
    r"^event-(?P<idx>\d{5,})-(?P<event_id>[0-9a-fA-F\-]{8,})\.json$"
)
EVENT_FILE_PATTERN = "event-{idx:05d}-{event_id}.json"
