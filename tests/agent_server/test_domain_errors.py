"""The domain error table answers exactly as the HTTPExceptions it replaced."""

from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from filelock import FileLock

from openhands.agent_server.api import _add_exception_handlers
from openhands.agent_server.conversation_lease import ConversationOwnershipLostError
from openhands.agent_server.conversation_service import (
    CredentialBindingActivationRequired,
)
from openhands.agent_server.errors import (
    ERROR_STATUS,
    ForwardedStatus,
    error_for_status,
    error_responses,
    status_for,
)
from openhands.agent_server.event_service import ConversationRunLimitExceeded
from openhands.sdk.errors import (
    Busy,
    Conflict,
    DomainError,
    Invalid,
    LockTimeout,
    NotFound,
    RateLimited,
    Unauthenticated,
    Unprocessable,
)
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.meta_profile_store import MetaProfileStore
from openhands.sdk.llm.provider_connection_store import ProviderConnectionNotFound
from openhands.sdk.profiles.agent_profile_store import AgentProfileStore


def _client(raise_error: BaseException) -> TestClient:
    app = FastAPI()
    _add_exception_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise raise_error

    return TestClient(app, raise_server_exceptions=False)


def _response(error: BaseException) -> tuple[int, object, str | None]:
    response = _client(error).get("/boom")
    return response.status_code, response.json(), response.headers.get("retry-after")


@pytest.mark.parametrize("category", list(ERROR_STATUS), ids=lambda c: c.__name__)
@pytest.mark.parametrize(
    "detail",
    ["Profile 'x' not found", {"code": "missing_token", "message": "no token"}],
    ids=["text", "structured"],
)
def test_each_category_answers_like_the_http_exception_it_replaced(
    category: type[DomainError], detail: str | dict[str, str]
) -> None:
    status = ERROR_STATUS[category]
    assert _response(category(detail)) == _response(HTTPException(status, detail))


def test_a_missing_detail_uses_the_status_phrase_like_http_exception() -> None:
    assert _response(Unauthenticated()) == _response(HTTPException(401))


def test_retry_after_is_sent_like_the_header_it_replaced() -> None:
    assert _response(RateLimited("slow down", retry_after="30")) == _response(
        HTTPException(429, "slow down", headers={"Retry-After": "30"})
    )


def test_a_domain_error_inside_an_exception_group_is_mapped() -> None:
    group = ExceptionGroup("task group", [NotFound("Conversation not found")])
    assert _response(group) == _response(HTTPException(404, "Conversation not found"))


def test_forwarded_status_relays_the_upstream_status() -> None:
    error = ForwardedStatus(418, "Conversation runtime rejected the request")
    assert _response(error) == _response(
        HTTPException(418, "Conversation runtime rejected the request")
    )


@pytest.mark.parametrize(
    ("error", "status", "body"),
    [
        # These three had their own handlers in api.py; the bodies are theirs.
        (
            ConversationRunLimitExceeded(
                "Conversation run limit reached. Retry the request later."
            ),
            429,
            {"detail": "Conversation run limit reached. Retry the request later."},
        ),
        (
            ConversationOwnershipLostError(
                conversation_dir=Path("/tmp/c"), owner_instance_id="a", generation=1
            ),
            409,
            {
                "detail": "Conversation is owned by another agent server instance; "
                "the write was refused"
            },
        ),
        (
            CredentialBindingActivationRequired(
                "credential_binding_activation_required"
            ),
            409,
            {"detail": "credential_binding_activation_required", "retryable": True},
        ),
        (ProviderConnectionNotFound("conn-1"), 422, {"detail": "conn-1"}),
    ],
    ids=["run-limit", "ownership-lost", "binding-activation", "provider-missing"],
)
def test_former_special_cases_keep_their_bodies(
    error: DomainError, status: int, body: dict[str, object]
) -> None:
    response = _client(error).get("/boom")
    assert (response.status_code, response.json()) == (status, body)


def test_former_special_cases_keep_their_base_classes() -> None:
    assert isinstance(ConversationRunLimitExceeded("x"), RuntimeError)
    assert isinstance(CredentialBindingActivationRequired("x"), RuntimeError)
    assert isinstance(ProviderConnectionNotFound("x"), ValueError)
    assert isinstance(Unprocessable("x"), ValueError)
    assert isinstance(LockTimeout("x"), TimeoutError)


def test_status_resolves_through_the_nearest_category() -> None:
    assert status_for(Unprocessable("x")) == 422
    assert status_for(Invalid("x")) == 400
    assert status_for(LockTimeout("x")) == 503
    assert status_for(ProviderConnectionNotFound("x")) == 422


def test_error_for_status_builds_the_category_or_relays() -> None:
    assert type(error_for_status(404, "x")) is NotFound
    assert type(error_for_status(503, "x")) is Busy
    relayed = error_for_status(418, "x")
    assert isinstance(relayed, ForwardedStatus) and status_for(relayed) == 418


def test_error_responses_reads_the_table() -> None:
    assert error_responses(
        {
            NotFound: "Item not found",
            ProviderConnectionNotFound: "Dangling",
            Conflict: "Busy",
        }
    ) == {
        404: {"description": "Item not found"},
        422: {"description": "Dangling"},
        409: {"description": "Busy"},
    }


@pytest.mark.parametrize(
    ("make_store", "detail"),
    [
        (LLMProfileStore, "Profile store is busy. Please retry."),
        (AgentProfileStore, "Profile store is busy. Please retry."),
        (MetaProfileStore, "Meta-profile store is busy. Please retry."),
    ],
    ids=["llm-profiles", "agent-profiles", "meta-profiles"],
)
def test_store_lock_timeouts_are_busy_with_the_old_detail(
    tmp_path: Path,
    make_store: type[LLMProfileStore | AgentProfileStore | MetaProfileStore],
    detail: str,
) -> None:
    store = make_store(base_dir=tmp_path)
    held = FileLock(str(store._file_lock.lock_file))
    with held:
        with pytest.raises(LockTimeout) as caught:
            with store._acquire_lock(timeout=0.05):
                pass
    assert caught.value.detail == detail
    assert status_for(caught.value) == 503
