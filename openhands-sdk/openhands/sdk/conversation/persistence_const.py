import re


BASE_STATE = "base_state.json"
EVENTS_DIR = "events"
# The index is zero-padded to a *minimum* of 5 digits by EVENT_FILE_PATTERN
# ({idx:05d}); it is not capped, so logs past 99999 events emit 6+ digit names.
# The reader must therefore accept 5 *or more* digits (\d{5,}) — matching exactly
# 5 would silently drop every event with index >= 100000 on a cold reload.
EVENT_NAME_RE = re.compile(
    r"^event-(?P<idx>\d{5,})-(?P<event_id>[0-9a-fA-F\-]{8,})\.json$"
)
EVENT_FILE_PATTERN = "event-{idx:05d}-{event_id}.json"
