import json

import httpx
import pytest

from openhands.sdk.credential import (
    CredentialBindingUnsupported,
    CredentialConflict,
    CredentialNeedsReauthentication,
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
@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (404, CredentialNeedsReauthentication),
        (409, CredentialConflict),
        (501, CredentialBindingUnsupported),
    ],
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


def test_http_binding_reauthorization_replaces_headers() -> None:
    binding = HttpVersionedCredentialBinding(
        "https://broker.test/credential",
        {"Authorization": "Bearer old"},
    )
    replacement = HttpVersionedCredentialBinding(
        "https://broker.test/credential",
        {"Authorization": "Bearer new"},
    )

    binding.reauthorize(replacement)

    assert binding.headers == {"Authorization": "Bearer new"}
    assert binding.authorization_revision == 1
