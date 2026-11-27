"""Network results shared by the two completion-callback senders.

Each row is the server's actions, how many attempts should arrive, and the
terminal failure detail. None means the exit logged success or an already
terminal 409, not a failure.
"""

from __future__ import annotations

import pytest

from tests.callback_http import Action, CallbackServer


# 502 then 200 is covered by each sender's payload exit test.
NETWORK: tuple[tuple[tuple[Action, ...], int, str | None], ...] = (
    ((429, 503, 201), 3, None),
    ((204,), 1, None),
    ((409,), 1, None),
    ((502, 409), 2, None),
    ((422, 200), 1, "422"),
    (("drop", 200), 2, None),
    (("drop", "drop", "drop", "drop"), 4, "transport"),
    ((502, 502, 502, 502), 4, "502"),
)


def assert_network(
    server: CallbackServer,
    caplog: pytest.LogCaptureFixture,
    actions: tuple[Action, ...],
    attempts: int,
    warning: str | None,
    secret: str,
) -> None:
    assert len(server.bodies) == attempts
    if attempts > 1:
        assert all(body == server.bodies[0] for body in server.bodies)
    assert server.authorization == [f"Bearer {secret}"] * attempts

    own = [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("openhands.")
    ]
    text = "\n".join(own)
    assert secret not in text
    assert "127.0.0.1" not in text
    assert "http://" not in text
    failures = [
        message for message in own if message.startswith("Completion callback failed")
    ]
    if warning is None:
        assert failures == []
        last = actions[-1]
        code = last[0] if isinstance(last, tuple) else last
        if code == 409:
            assert any("already terminal" in message for message in own)
            assert all("sent" not in message for message in own)
        else:
            assert any(
                f"Completion callback sent (COMPLETED): {code}" in message
                for message in own
            )
        return
    assert len(failures) == 1
    assert "sent" not in failures[0]
    if warning == "transport":
        assert failures[0].rsplit(": ", 1)[1].isidentifier()
        return
    assert warning in failures[0]
