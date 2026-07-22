import asyncio
import json

import httpx
import pytest

from openhands.sdk.credential import (
    CredentialAuthorizationRejected,
    CredentialConflict,
    CredentialNeedsReauthentication,
    CredentialRenewalUnavailable,
    CredentialSyncError,
    HttpVersionedCredentialBinding,
)


@pytest.mark.asyncio
async def test_http_binding_load_and_replace() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"value": "r0", "version": "v0"})
        assert json.loads(request.content) == {
            "expected_version": "v0",
            "value": "r1",
        }
        return httpx.Response(200, json={"version": "v1"})

    binding = HttpVersionedCredentialBinding(
        "https://broker.test/credential",
        {"Authorization": "Bearer scoped"},
        transport=httpx.MockTransport(handler),
    )
    resolved = await binding.load()
    successor = await binding.replace(resolved.version, "r1")

    assert resolved.value == "r0"
    assert successor == "v1"
    assert all(
        request.headers["Authorization"] == "Bearer scoped" for request in requests
    )


@pytest.mark.asyncio
async def test_http_binding_renews_authorization(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr(
        "openhands.sdk.credential.time.monotonic",
        lambda: now[0],
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "authorization": "Bearer successor",
                "authorization_expires_in_seconds": 100,
            },
        )

    binding = HttpVersionedCredentialBinding(
        "https://broker.test/credential",
        {
            "Authorization": "Bearer initial",
            "X-Session-API-Key": "session-key",
        },
        renewal_url="https://broker.test/credential/renew",
        renewal_interval_seconds=10,
        authorization_expires_in_seconds=100,
        transport=httpx.MockTransport(handler),
    )

    assert not binding.maintenance_due()
    now[0] = 110
    assert binding.maintenance_due()
    await binding.maintain()

    assert requests[0].url == "https://broker.test/credential/renew"
    assert requests[0].headers["Authorization"] == "Bearer initial"
    assert requests[0].headers["X-Session-API-Key"] == "session-key"
    assert binding.headers == {
        "Authorization": "Bearer successor",
        "X-Session-API-Key": "session-key",
    }
    assert not binding.maintenance_due()
    now[0] = 120
    assert binding.maintenance_due()


@pytest.mark.asyncio
async def test_http_binding_discards_stale_renewal_response(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr(
        "openhands.sdk.credential.time.monotonic",
        lambda: now[0],
    )
    renewal_started = asyncio.Event()
    finish_renewal = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        renewal_started.set()
        await finish_renewal.wait()
        return httpx.Response(
            200,
            json={
                "authorization": "Bearer stale-renewal",
                "authorization_expires_in_seconds": 100,
            },
        )

    binding = HttpVersionedCredentialBinding(
        "https://broker.test/credential",
        {
            "Authorization": "Bearer initial",
            "X-Session-API-Key": "old-session-key",
        },
        renewal_url="https://broker.test/credential/renew",
        renewal_interval_seconds=10,
        authorization_expires_in_seconds=100,
        transport=httpx.MockTransport(handler),
    )
    renewal = asyncio.create_task(binding.maintain())
    await renewal_started.wait()

    binding.reauthorize(
        HttpVersionedCredentialBinding(
            "https://broker.test/credential",
            {
                "Authorization": "Bearer reactivated",
                "X-Session-API-Key": "new-session-key",
            },
            renewal_url="https://broker.test/credential/renew",
            renewal_interval_seconds=20,
            authorization_expires_in_seconds=200,
        )
    )
    finish_renewal.set()
    await renewal

    assert binding.headers == {
        "Authorization": "Bearer reactivated",
        "X-Session-API-Key": "new-session-key",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", ["Bearer ", "Basic successor", ""])
async def test_http_binding_rejects_invalid_renewal_authorization(
    authorization: str,
) -> None:
    binding = HttpVersionedCredentialBinding(
        "https://broker.test/credential",
        {"Authorization": "Bearer initial"},
        renewal_url="https://broker.test/credential/renew",
        renewal_interval_seconds=10,
        authorization_expires_in_seconds=100,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "authorization": authorization,
                    "authorization_expires_in_seconds": 100,
                },
            )
        ),
    )

    with pytest.raises(CredentialSyncError):
        await binding.maintain()

    assert binding.headers["Authorization"] == "Bearer initial"


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_http_binding_rejects_renewal_unauthorized(status_code: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code)

    binding = HttpVersionedCredentialBinding(
        "https://broker.test/credential",
        {"Authorization": "Bearer initial"},
        renewal_url="https://broker.test/credential/renew",
        renewal_interval_seconds=10,
        authorization_expires_in_seconds=100,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(CredentialAuthorizationRejected):
        await binding.maintain()

    assert calls == 1


@pytest.mark.asyncio
async def test_http_binding_retries_transient_renewal_failures(monkeypatch) -> None:
    monkeypatch.setattr(
        "openhands.sdk.credential._CREDENTIAL_RENEWAL_RETRY_DELAYS",
        (0, 0),
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "authorization": "Bearer successor",
                "authorization_expires_in_seconds": 100,
            },
        )

    binding = HttpVersionedCredentialBinding(
        "https://broker.test/credential",
        {"Authorization": "Bearer initial"},
        renewal_url="https://broker.test/credential/renew",
        renewal_interval_seconds=10,
        authorization_expires_in_seconds=100,
        transport=httpx.MockTransport(handler),
    )

    await binding.maintain()

    assert calls == 3
    assert binding.headers["Authorization"] == "Bearer successor"


@pytest.mark.asyncio
async def test_http_binding_defers_transient_failure_until_near_expiry(
    monkeypatch,
) -> None:
    now = [100.0]
    monkeypatch.setattr(
        "openhands.sdk.credential.time.monotonic",
        lambda: now[0],
    )
    monkeypatch.setattr(
        "openhands.sdk.credential._CREDENTIAL_RENEWAL_RETRY_DELAYS",
        (0, 0),
    )
    monkeypatch.setattr(
        "openhands.sdk.credential._CREDENTIAL_RENEWAL_BACKOFF_SECONDS",
        (5, 30),
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    binding = HttpVersionedCredentialBinding(
        "https://broker.test/credential",
        {"Authorization": "Bearer initial"},
        renewal_url="https://broker.test/credential/renew",
        renewal_interval_seconds=10,
        authorization_expires_in_seconds=100,
        transport=httpx.MockTransport(handler),
    )

    await binding.maintain()

    assert calls == 3
    assert not binding.maintenance_due()
    now[0] = 105
    assert binding.maintenance_due()
    await binding.maintain()

    assert calls == 6
    now[0] = 134
    assert not binding.maintenance_due()
    now[0] = 135
    assert binding.maintenance_due()
    await binding.maintain()

    assert calls == 9
    now[0] = 165
    assert binding.maintenance_due()
    await binding.maintain()

    assert calls == 12
    now[0] = 190
    assert binding.maintenance_due()
    with pytest.raises(CredentialRenewalUnavailable):
        await binding.maintain()

    assert calls == 15
    assert binding.headers["Authorization"] == "Bearer initial"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [(404, CredentialNeedsReauthentication), (409, CredentialConflict)],
)
async def test_http_binding_maps_protocol_errors(status_code, error_type) -> None:
    binding = HttpVersionedCredentialBinding(
        "https://broker.test/credential",
        {},
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status_code, request=request)
        ),
    )
    with pytest.raises(error_type):
        await binding.load()
