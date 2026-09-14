from fastapi import APIRouter, FastAPI

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.routers import add_legacy_runtime_routes


def test_legacy_registration_exposes_deprecation_without_deprecating_local_routes():
    async def endpoint():
        return {"ok": True}

    legacy = APIRouter()
    add_legacy_runtime_routes(legacy, "bash", endpoint, endpoint)
    app = FastAPI()
    app.include_router(legacy, prefix="/api")
    for operations in app.openapi()["paths"].values():
        for operation in operations.values():
            assert operation["deprecated"]
            assert "v1.53.0" in operation["description"]
    paths = create_app(Config()).openapi()["paths"]
    for path, operations in paths.items():
        if "/bash/" in path:
            assert all(not op.get("deprecated", False) for op in operations.values())
