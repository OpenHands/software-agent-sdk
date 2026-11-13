import time
from datetime import UTC, datetime, timedelta

import pytest

from openhands.sdk.utils.datetime import timestamp_instant, utc_now


@pytest.fixture
def new_york_tz(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_utc_now_isoformat_names_its_offset():
    assert utc_now().isoformat().endswith("+00:00")


def test_timestamp_instant_keeps_an_explicit_offset():
    assert timestamp_instant("2025-01-01T12:00:00+00:00") == datetime(
        2025, 1, 1, 12, tzinfo=UTC
    )
    assert timestamp_instant("2025-01-01T12:00:00Z") == datetime(
        2025, 1, 1, 12, tzinfo=UTC
    )


def test_timestamp_instant_reads_naive_values_as_server_local_time(new_york_tz):
    instant = timestamp_instant("2025-01-01T07:00:00")

    assert instant.utcoffset() == timedelta(hours=-5)
    assert instant == datetime(2025, 1, 1, 12, tzinfo=UTC)
    assert timestamp_instant(datetime(2025, 1, 1, 7)) == instant


def test_timestamp_instant_orders_mixed_formats_by_instant(new_york_tz):
    legacy_later = "2025-01-01T07:45:00"  # 12:45Z
    new_earlier = "2025-01-01T12:30:00+00:00"

    assert legacy_later < new_earlier  # string order is wrong
    assert timestamp_instant(legacy_later) > timestamp_instant(new_earlier)
