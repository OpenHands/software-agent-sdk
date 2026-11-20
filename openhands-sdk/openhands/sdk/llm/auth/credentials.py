"""Credential storage and retrieval for OAuth-based LLM authentication."""

from __future__ import annotations

import json
import os
import time
import warnings
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from httpx import AsyncClient, Client, HTTPError, Response
from pydantic import BaseModel, Field, SecretStr

from openhands.sdk.logger import get_logger
from openhands.sdk.utils.path import get_user_persistence_dir


logger = get_logger(__name__)


def get_credentials_dir() -> Path:
    """Get the directory for storing credentials.

    Returns the ``auth`` subdirectory of the user persistence directory:
    ``OH_PERSISTENCE_DIR/auth`` when ``OH_PERSISTENCE_DIR`` is set, otherwise
    ``~/.openhands/auth``.
    """
    return get_user_persistence_dir() / "auth"


class OAuthCredentials(BaseModel):
    """OAuth credentials for subscription-based LLM access."""

    type: Literal["oauth"] = "oauth"
    vendor: str = Field(description="The vendor/provider (e.g., 'openai')")
    access_token: str = Field(description="The OAuth access token")
    refresh_token: str = Field(description="The OAuth refresh token")
    expires_at: int = Field(
        description="Unix timestamp (ms) when the access token expires"
    )

    def is_expired(self) -> bool:
        """Check if the access token is expired."""
        # Add 60 second buffer to avoid edge cases
        # Add 60 second buffer to avoid edge cases where token expires during request
        return self.expires_at < (int(time.time() * 1000) + 60_000)


class CredentialStore:
    """Store and retrieve OAuth credentials for LLM providers."""

    def __init__(self, credentials_dir: Path | None = None):
        """Initialize the credential store.

        Args:
            credentials_dir: Optional custom directory for storing credentials.
                           Defaults to the ``auth`` subdirectory of the user
                           persistence directory (``OH_PERSISTENCE_DIR/auth``
                           when set, otherwise ``~/.openhands/auth``).
        """
        self._credentials_dir = credentials_dir or get_credentials_dir()
        logger.info(f"Using credentials directory: {self._credentials_dir}")

    @property
    def credentials_dir(self) -> Path:
        """Get the credentials directory, creating it if necessary."""
        self._credentials_dir.mkdir(parents=True, exist_ok=True)
        # Set directory permissions to owner-only (rwx------)
        if os.name != "nt":
            self._credentials_dir.chmod(0o700)
        return self._credentials_dir

    def _get_credentials_file(self, vendor: str) -> Path:
        """Get the path to the credentials file for a vendor."""
        return self.credentials_dir / f"{vendor}_oauth.json"

    def get(self, vendor: str) -> OAuthCredentials | None:
        """Get stored credentials for a vendor.

        Args:
            vendor: The vendor/provider name (e.g., 'openai')

        Returns:
            OAuthCredentials if found and valid, None otherwise
        """
        creds_file = self._get_credentials_file(vendor)
        if not creds_file.exists():
            return None

        try:
            with open(creds_file, encoding="utf-8") as f:
                data = json.load(f)
            return OAuthCredentials.model_validate(data)
        except (json.JSONDecodeError, ValueError):
            # Invalid credentials file, remove it
            creds_file.unlink(missing_ok=True)
            return None

    def save(self, credentials: OAuthCredentials) -> None:
        """Save credentials for a vendor.

        Args:
            credentials: The OAuth credentials to save
        """
        creds_file = self._get_credentials_file(credentials.vendor)
        with open(creds_file, "w", encoding="utf-8") as f:
            json.dump(credentials.model_dump(), f, indent=2)
        # Set restrictive permissions (owner read/write only)
        # Note: On Windows, NTFS ACLs should be used instead
        if os.name != "nt":  # Not Windows
            creds_file.chmod(0o600)
        else:
            warnings.warn(
                "File permissions on Windows should be manually restricted",
                stacklevel=2,
            )

    def delete(self, vendor: str) -> bool:
        """Delete stored credentials for a vendor.

        Args:
            vendor: The vendor/provider name

        Returns:
            True if credentials were deleted, False if they didn't exist
        """
        creds_file = self._get_credentials_file(vendor)
        if creds_file.exists():
            creds_file.unlink()
            return True
        return False

    def update_tokens(
        self,
        vendor: str,
        access_token: str,
        refresh_token: str | None,
        expires_in: int,
    ) -> OAuthCredentials | None:
        """Update tokens for an existing credential.

        Args:
            vendor: The vendor/provider name
            access_token: New access token
            refresh_token: New refresh token (if provided)
            expires_in: Token expiry in seconds

        Returns:
            Updated credentials, or None if no existing credentials found
        """
        existing = self.get(vendor)
        if existing is None:
            return None

        updated = OAuthCredentials(
            vendor=vendor,
            access_token=access_token,
            refresh_token=refresh_token or existing.refresh_token,
            expires_at=int(time.time() * 1000) + (expires_in * 1000),
        )
        self.save(updated)
        return updated


class SubscriptionBrokerCredentialStore(CredentialStore):
    """Resolve short-lived access tokens from the owning runtime's broker."""

    def __init__(self, url: str, session_key: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or (
                parsed.scheme == "http"
                and parsed.hostname
                not in ("127.0.0.1", "localhost", "host.docker.internal")
            )
        ):
            raise ValueError("Invalid subscription broker URL")
        if not session_key:
            raise ValueError("Subscription broker session key is required")
        self._url = url
        self._session_key = SecretStr(session_key)

    def _parse_response(self, response: Response) -> OAuthCredentials | None:
        if response.status_code == 404:
            return None
        if not response.is_success:
            raise RuntimeError("Subscription token broker request failed")
        try:
            data = response.json()
            credentials = OAuthCredentials(
                vendor="openai",
                access_token=data["access_token"],
                refresh_token="",
                expires_at=data["expires_at"],
            )
            if not credentials.access_token or credentials.is_expired():
                raise ValueError("Invalid access token")
            return credentials
        except (ValueError, KeyError, TypeError):
            raise RuntimeError("Invalid subscription token broker response") from None

    def get(self, vendor: str) -> OAuthCredentials | None:
        if vendor != "openai":
            return None
        try:
            with Client(timeout=10, follow_redirects=False) as client:
                response = client.get(
                    self._url,
                    headers={"X-Session-API-Key": self._session_key.get_secret_value()},
                )
        except HTTPError:
            raise RuntimeError("Subscription token broker request failed") from None
        return self._parse_response(response)

    async def aget(self, vendor: str) -> OAuthCredentials | None:
        if vendor != "openai":
            return None
        try:
            async with AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.get(
                    self._url,
                    headers={"X-Session-API-Key": self._session_key.get_secret_value()},
                )
        except HTTPError:
            raise RuntimeError("Subscription token broker request failed") from None
        return self._parse_response(response)

    def save(self, credentials: OAuthCredentials) -> None:  # noqa: ARG002
        raise RuntimeError("Subscription broker credentials are read-only")

    def delete(self, vendor: str) -> bool:  # noqa: ARG002
        raise RuntimeError("Subscription broker credentials are read-only")
