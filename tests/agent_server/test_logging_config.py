import json
import logging

from openhands.agent_server.logging_config import UvicornAccessJsonFormatter


def test_uvicorn_access_formatter_adds_execution_and_http_fields():
    formatter = UvicornAccessJsonFormatter("%(message)s")
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "test.py",
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", "/api/settings", "1.1", 200),
        None,
    )

    data = json.loads(formatter.format(record))

    assert isinstance(data["process_id"], int)
    assert isinstance(data["thread_id"], int)
    assert data["http.client_ip"] == "127.0.0.1"
    assert data["http.method"] == "GET"
    assert data["http.url"] == "/api/settings"
    assert data["http.version"] == "1.1"
    assert data["http.status_code"] == 200
