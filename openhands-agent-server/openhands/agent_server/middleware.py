import os
from urllib.parse import urlparse

from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp


class LocalhostCORSMiddleware(CORSMiddleware):
    """Custom CORS middleware that allows any request from localhost/127.0.0.1 domains.

    Also allows the DOCKER_HOST_ADDR IP, while using standard CORS rules for
    other origins.
    """

    def __init__(self, app: ASGIApp, allow_origins: list[str]) -> None:
        super().__init__(
            app,
            allow_origins=allow_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def is_allowed_origin(self, origin: str) -> bool:
        if origin and not self.allow_origins and not self.allow_origin_regex:
            parsed = urlparse(origin)
            hostname = parsed.hostname or ""

            # Allow any localhost/127.0.0.1 origin regardless of port
            if hostname in ["localhost", "127.0.0.1"]:
                return True

            # Also allow DOCKER_HOST_ADDR if set (for remote browser access)
            docker_host_addr = os.environ.get("DOCKER_HOST_ADDR")
            if docker_host_addr and hostname == docker_host_addr:
                return True

        # For missing origin or other origins, use the parent class's logic
        result: bool = super().is_allowed_origin(origin)
        return result
