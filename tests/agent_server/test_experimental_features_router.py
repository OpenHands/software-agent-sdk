"""Tests for the user-facing experimental features (feature flags)."""

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from openhands.agent_server import experimental_features
from openhands.agent_server.config import Config
from openhands.agent_server.experimental_features import (
    ExperimentalFeatureDefinition,
)
from openhands.agent_server.experimental_features_router import (
    experimental_features_router,
)
from openhands.agent_server.persistence import PersistedSettings, get_settings_store


QA_FEATURE = ExperimentalFeatureDefinition(
    key="qa_feature", label="QA feature", description="A feature for tests."
)


@pytest.fixture
def qa_feature(monkeypatch) -> ExperimentalFeatureDefinition:
    monkeypatch.setattr(experimental_features, "EXPERIMENTAL_FEATURES", [QA_FEATURE])
    return QA_FEATURE


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.config = Config(
        static_files_path=None, session_api_keys=[], secret_key=None
    )
    app.include_router(experimental_features_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _settings_path():
    return get_settings_store()._path


@pytest.mark.asyncio
async def test_listing_needs_no_settings_file_and_creates_none(client, qa_feature):
    response = await client.get("/api/settings/experimental")

    assert response.status_code == 200
    assert response.json() == [
        {
            "key": "qa_feature",
            "label": "QA feature",
            "description": "A feature for tests.",
            "enabled": False,
        }
    ]
    assert not _settings_path().exists()


@pytest.mark.asyncio
async def test_turning_a_feature_on_and_off_is_saved(client, qa_feature):
    on = await client.patch(
        "/api/settings/experimental/qa_feature", json={"enabled": True}
    )
    listed_on = await client.get("/api/settings/experimental")
    off = await client.patch(
        "/api/settings/experimental/qa_feature", json={"enabled": False}
    )

    assert on.status_code == 200
    assert on.json()["enabled"] is True
    assert listed_on.json()[0]["enabled"] is True
    assert off.json()["enabled"] is False
    stored = get_settings_store().load()
    assert stored is not None
    assert stored.experimental_features == {"qa_feature": False}


@pytest.mark.asyncio
async def test_unknown_features_are_refused(client, qa_feature):
    response = await client.patch(
        "/api/settings/experimental/time_travel", json={"enabled": True}
    )

    assert response.status_code == 404
    assert not _settings_path().exists()


@pytest.mark.asyncio
async def test_switching_a_feature_keeps_the_other_settings(client, qa_feature):
    get_settings_store().save(
        PersistedSettings(misc_settings={"kept": True}, active_profile="mine")
    )

    await client.patch("/api/settings/experimental/qa_feature", json={"enabled": True})

    stored = get_settings_store().load()
    assert stored is not None
    assert stored.misc_settings == {"kept": True}
    assert stored.active_profile == "mine"
    assert stored.experimental_features == {"qa_feature": True}


@pytest.mark.asyncio
async def test_a_stored_flag_nobody_knows_is_not_listed_and_is_kept(client, qa_feature):
    """A flag saved by another version of the server does no harm."""
    get_settings_store().save(
        PersistedSettings(experimental_features={"from_the_future": True})
    )

    listed = await client.get("/api/settings/experimental")
    await client.patch("/api/settings/experimental/qa_feature", json={"enabled": True})

    assert [f["key"] for f in listed.json()] == ["qa_feature"]
    stored = get_settings_store().load()
    assert stored is not None
    assert stored.experimental_features == {
        "from_the_future": True,
        "qa_feature": True,
    }


@pytest.mark.asyncio
async def test_a_corrupted_settings_file_is_not_overwritten(client, qa_feature):
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")

    listed = await client.get("/api/settings/experimental")
    response = await client.patch(
        "/api/settings/experimental/qa_feature", json={"enabled": True}
    )

    assert listed.json()[0]["enabled"] is False
    assert response.status_code == 409
    assert path.read_text() == "{not json"


def test_settings_saved_before_experimental_features_load_with_none_on():
    settings = PersistedSettings()
    saved = settings.model_dump(mode="json", context={"expose_secrets": "plaintext"})
    del saved["experimental_features"]

    loaded = PersistedSettings.from_persisted(json.loads(json.dumps(saved)))

    assert loaded.experimental_features == {}
    assert loaded.schema_version == settings.schema_version


@pytest.mark.asyncio
async def test_no_features_are_registered_yet(client):
    response = await client.get("/api/settings/experimental")

    assert response.json() == []
