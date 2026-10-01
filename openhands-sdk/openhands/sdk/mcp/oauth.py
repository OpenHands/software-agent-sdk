"""FastMCP OAuth client that discovers the token endpoint before refreshing."""

from __future__ import annotations

import httpx
from fastmcp.client.auth import OAuth
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_oauth_metadata_request,
    handle_auth_metadata_response,
    handle_protected_resource_response,
)

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

_DISCOVERY_TIMEOUT = httpx.Timeout(10.0)


class MCPOAuth(OAuth):
    """FastMCP ``OAuth`` whose token refresh targets the discovered endpoint.

    The upstream ``OAuthClientProvider`` refreshes an expired access token
    before sending a request, but discovers the authorization server's
    metadata only inside its 401 branch and never persists it. Every
    conversation builds a fresh client, so ``_refresh_token`` falls back to
    ``<MCP server origin>/token``, which is wrong for providers such as GitLab
    (``/oauth/token``) and Atlassian (``/v1/token``). The 404 that follows
    drops the tokens and starts the interactive browser flow, which cannot
    complete inside a sandbox.

    ``_refresh_token`` runs under upstream's context lock after
    ``_initialize()`` restored the stored tokens and expiry, so discovery only
    happens when a refresh is actually about to be attempted.
    """

    async def _refresh_token(self) -> httpx.Request:
        if self.context.oauth_metadata is None:
            await self._discover_oauth_metadata()
        return await super()._refresh_token()

    async def _discover_oauth_metadata(self) -> None:
        """Populate the resource and authorization-server metadata.

        Mirrors the discovery upstream performs on a 401 (protected resource
        metadata first, then authorization server metadata, with the same
        fallback URL lists). A network failure only logs: the refresh then
        goes to upstream's default endpoint, exactly as before.
        """
        server_url = self.context.server_url
        try:
            async with self.httpx_client_factory(timeout=_DISCOVERY_TIMEOUT) as client:
                for url in build_protected_resource_metadata_discovery_urls(
                    None, server_url
                ):
                    response = await client.send(create_oauth_metadata_request(url))
                    prm = await handle_protected_resource_response(response)
                    if prm is None:
                        continue
                    await self._validate_resource_match(prm)
                    self.context.protected_resource_metadata = prm
                    # ``authorization_servers`` has a minimum length of one.
                    self.context.auth_server_url = str(prm.authorization_servers[0])
                    break

                for url in build_oauth_authorization_server_metadata_discovery_urls(
                    self.context.auth_server_url, server_url
                ):
                    response = await client.send(create_oauth_metadata_request(url))
                    ok, metadata = await handle_auth_metadata_response(response)
                    if not ok:
                        break
                    if metadata is not None:
                        self.context.oauth_metadata = metadata
                        break
        except httpx.HTTPError as exc:
            logger.warning(
                "OAuth metadata discovery failed for %s; refreshing against the "
                "default token endpoint: %s",
                server_url,
                exc,
            )
