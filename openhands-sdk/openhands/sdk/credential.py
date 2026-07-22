from __future__ import annotations

import asyncio
import math
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import httpx


_CREDENTIAL_RENEWAL_RETRY_DELAYS = (0.1, 0.5)
_CREDENTIAL_RENEWAL_BACKOFF_SECONDS = (5.0, 30.0, 120.0, 600.0, 1800.0, 3600.0)
_CREDENTIAL_RENEWAL_MAX_EXPIRY_MARGIN_SECONDS = 60.0
_CREDENTIAL_RENEWAL_UNUSABLE_BACKOFF_MAX_SECONDS = 30.0


@dataclass(frozen=True)
class ResolvedCredential:
    value: str
    version: str


class CredentialBindingError(RuntimeError):
    pass


class CredentialNeedsReauthentication(CredentialBindingError):
    pass


class CredentialSyncError(CredentialBindingError):
    pass


class CredentialConflict(CredentialSyncError):
    pass


class CredentialAuthorizationRejected(CredentialSyncError):
    pass


class CredentialRenewalUnavailable(CredentialSyncError):
    pass


class CredentialRenewalRejected(CredentialSyncError):
    pass


class _RetryableCredentialRenewalError(CredentialSyncError):
    pass


class VersionedCredentialBinding(Protocol):
    async def load(self) -> ResolvedCredential: ...

    async def replace(self, expected_version: str, value: str) -> str: ...


class HttpVersionedCredentialBinding:
    def __init__(
        self,
        url: str,
        headers: dict[str, str],
        *,
        renewal_url: str | None = None,
        renewal_interval_seconds: float | None = None,
        authorization_expires_in_seconds: float | None = None,
        timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._validate_renewal(
            renewal_url,
            renewal_interval_seconds,
            authorization_expires_in_seconds,
        )
        self.url = url
        self.timeout = timeout
        self.transport = transport
        self._lock = threading.RLock()
        self._headers = dict(headers)
        self._renewal_url = renewal_url
        self._renewal_interval_seconds = renewal_interval_seconds
        self._authorization_expires_at: float | None = None
        self._authorization_expiry_margin_seconds: float | None = None
        self._next_maintenance_at: float | None = None
        self._set_renewal_schedule(authorization_expires_in_seconds)
        self._renewal_failure_count = 0
        self._authorization_generation = 0
        self._maintenance_in_flight = False

    @property
    def headers(self) -> dict[str, str]:
        with self._lock:
            return dict(self._headers)

    @property
    def renewal_url(self) -> str | None:
        with self._lock:
            return self._renewal_url

    @property
    def renewal_interval_seconds(self) -> float | None:
        with self._lock:
            return self._renewal_interval_seconds

    @property
    def authorization_expires_in_seconds(self) -> float | None:
        with self._lock:
            if self._authorization_expires_at is None:
                return None
            return max(0.0, self._authorization_expires_at - time.monotonic())

    @property
    def authorization_revision(self) -> int:
        with self._lock:
            return self._authorization_generation

    def reauthorize(self, replacement: HttpVersionedCredentialBinding) -> None:
        if replacement.url != self.url:
            raise ValueError("credential_binding_url_mismatch")
        with replacement._lock:
            headers = dict(replacement._headers)
            renewal_url = replacement._renewal_url
            renewal_interval_seconds = replacement._renewal_interval_seconds
            authorization_expires_at = replacement._authorization_expires_at
            authorization_expiry_margin_seconds = (
                replacement._authorization_expiry_margin_seconds
            )
            next_maintenance_at = replacement._next_maintenance_at
        authorization_expires_in_seconds = (
            None
            if authorization_expires_at is None
            else max(0.0, authorization_expires_at - time.monotonic())
        )
        self._validate_renewal(
            renewal_url,
            renewal_interval_seconds,
            authorization_expires_in_seconds,
        )
        with self._lock:
            self._headers = headers
            self._renewal_url = renewal_url
            self._renewal_interval_seconds = renewal_interval_seconds
            self._authorization_expires_at = authorization_expires_at
            self._authorization_expiry_margin_seconds = (
                authorization_expiry_margin_seconds
            )
            self._next_maintenance_at = next_maintenance_at
            self._renewal_failure_count = 0
            self._authorization_generation += 1

    def maintenance_due(self) -> bool:
        with self._lock:
            return (
                not self._maintenance_in_flight
                and self._next_maintenance_at is not None
                and time.monotonic() >= self._next_maintenance_at
            )

    def raise_if_authorization_unusable(self) -> None:
        with self._lock:
            expires_at = self._authorization_expires_at
            margin = self._authorization_expiry_margin_seconds
            if (
                self._renewal_failure_count
                and expires_at is not None
                and margin is not None
                and time.monotonic() >= expires_at - margin
            ):
                raise CredentialRenewalUnavailable(
                    "Credential authorization renewal is unavailable."
                )

    async def maintain(self) -> None:
        with self._lock:
            renewal_url = self._renewal_url
            renewal_interval_seconds = self._renewal_interval_seconds
            if renewal_url is None or renewal_interval_seconds is None:
                return
            if self._maintenance_in_flight:
                return
            self._maintenance_in_flight = True
            headers = dict(self._headers)
            generation = self._authorization_generation

        try:
            for attempt in range(len(_CREDENTIAL_RENEWAL_RETRY_DELAYS) + 1):
                with self._lock:
                    if generation != self._authorization_generation:
                        return
                try:
                    authorization, expires_in_seconds = await self._renew_authorization(
                        renewal_url,
                        headers,
                    )
                except _RetryableCredentialRenewalError as exc:
                    with self._lock:
                        if generation != self._authorization_generation:
                            return
                    if attempt == len(_CREDENTIAL_RENEWAL_RETRY_DELAYS):
                        self._defer_retry_or_raise(generation, exc)
                        return
                    await asyncio.sleep(_CREDENTIAL_RENEWAL_RETRY_DELAYS[attempt])
                    continue
                except CredentialSyncError:
                    with self._lock:
                        if generation != self._authorization_generation:
                            return
                    raise

                with self._lock:
                    if generation != self._authorization_generation:
                        return
                    self._headers = self._with_authorization(
                        self._headers,
                        authorization,
                    )
                    self._authorization_generation += 1
                    self._renewal_failure_count = 0
                    self._set_renewal_schedule(expires_in_seconds)
                return
        finally:
            with self._lock:
                self._maintenance_in_flight = False

    async def _renew_authorization(
        self,
        renewal_url: str,
        headers: dict[str, str],
    ) -> tuple[str, float]:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                response = await client.post(renewal_url, headers=headers)
        except httpx.RequestError as exc:
            raise _RetryableCredentialRenewalError from exc
        if response.status_code in (408, 429) or response.status_code >= 500:
            raise _RetryableCredentialRenewalError
        if response.status_code in (401, 403):
            raise CredentialAuthorizationRejected(
                "Credential authorization was rejected."
            )
        try:
            response.raise_for_status()
            payload = response.json()
            authorization = payload["authorization"]
            expires_in_seconds = payload["authorization_expires_in_seconds"]
        except (httpx.HTTPStatusError, KeyError, TypeError, ValueError) as exc:
            raise CredentialRenewalRejected(
                "Credential authorization renewal failed."
            ) from exc
        if not isinstance(authorization, str):
            raise CredentialRenewalRejected(
                "Credential authorization renewal returned an invalid response."
            )
        scheme, separator, token = authorization.partition(" ")
        if scheme != "Bearer" or not separator or not token.strip():
            raise CredentialRenewalRejected(
                "Credential authorization renewal returned an invalid response."
            )
        if not isinstance(expires_in_seconds, (int, float)) or isinstance(
            expires_in_seconds, bool
        ):
            raise CredentialRenewalRejected(
                "Credential authorization renewal returned an invalid response."
            )
        expires_in_seconds = float(expires_in_seconds)
        if not math.isfinite(expires_in_seconds) or expires_in_seconds <= 0:
            raise CredentialRenewalRejected(
                "Credential authorization renewal returned an invalid response."
            )
        return authorization, expires_in_seconds

    def _set_renewal_schedule(
        self,
        authorization_expires_in_seconds: float | None,
    ) -> None:
        if (
            self._renewal_interval_seconds is None
            or authorization_expires_in_seconds is None
        ):
            self._authorization_expires_at = None
            self._authorization_expiry_margin_seconds = None
            self._next_maintenance_at = None
            return
        now = time.monotonic()
        margin = min(
            _CREDENTIAL_RENEWAL_MAX_EXPIRY_MARGIN_SECONDS,
            authorization_expires_in_seconds / 10,
        )
        self._authorization_expires_at = now + authorization_expires_in_seconds
        self._authorization_expiry_margin_seconds = margin
        self._next_maintenance_at = min(
            now + self._renewal_interval_seconds,
            self._authorization_expires_at - margin,
        )

    def _defer_retry_or_raise(
        self,
        generation: int,
        cause: BaseException,
    ) -> None:
        with self._lock:
            if generation != self._authorization_generation:
                return
            expires_at = self._authorization_expires_at
            margin = self._authorization_expiry_margin_seconds
            if expires_at is None or margin is None:
                raise CredentialSyncError(
                    "Credential authorization renewal is unavailable."
                ) from cause
            now = time.monotonic()
            retry_deadline = expires_at - margin
            delay = _CREDENTIAL_RENEWAL_BACKOFF_SECONDS[
                min(
                    self._renewal_failure_count,
                    len(_CREDENTIAL_RENEWAL_BACKOFF_SECONDS) - 1,
                )
            ]
            self._renewal_failure_count += 1
            if now >= retry_deadline:
                self._next_maintenance_at = now + min(
                    delay,
                    _CREDENTIAL_RENEWAL_UNUSABLE_BACKOFF_MAX_SECONDS,
                )
                raise CredentialRenewalUnavailable(
                    "Credential authorization renewal is unavailable."
                ) from cause
            self._next_maintenance_at = min(now + delay, retry_deadline)

    @staticmethod
    def _validate_renewal(
        renewal_url: str | None,
        renewal_interval_seconds: float | None,
        authorization_expires_in_seconds: float | None,
    ) -> None:
        renewal_values = (
            renewal_url,
            renewal_interval_seconds,
            authorization_expires_in_seconds,
        )
        if any(value is None for value in renewal_values) and any(
            value is not None for value in renewal_values
        ):
            raise ValueError("renewal configuration must be provided together")
        if renewal_url is not None and (not renewal_url or len(renewal_url) > 4096):
            raise ValueError("renewal URL must contain 1 to 4096 characters")
        if renewal_interval_seconds is not None and (
            not math.isfinite(renewal_interval_seconds) or renewal_interval_seconds <= 0
        ):
            raise ValueError("renewal interval must be finite and positive")
        if authorization_expires_in_seconds is not None and (
            not math.isfinite(authorization_expires_in_seconds)
            or authorization_expires_in_seconds <= 0
        ):
            raise ValueError("authorization expiry must be finite and positive")

    @staticmethod
    def _with_authorization(
        headers: dict[str, str],
        authorization: str,
    ) -> dict[str, str]:
        updated = {
            name: value
            for name, value in headers.items()
            if name.casefold() != "authorization"
        }
        updated["Authorization"] = authorization
        return updated

    async def load(self) -> ResolvedCredential:
        headers = self.headers
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                response = await client.get(self.url, headers=headers)
        except httpx.RequestError as exc:
            raise CredentialSyncError("Credential source is unavailable.") from exc
        self._raise_for_status(response)
        try:
            payload = response.json()
            value = payload["value"]
            version = payload["version"]
        except (KeyError, TypeError, ValueError) as exc:
            raise CredentialSyncError(
                "Credential source returned an invalid response."
            ) from exc
        if not isinstance(value, str) or not isinstance(version, str) or not version:
            raise CredentialSyncError("Credential source returned an invalid response.")
        return ResolvedCredential(value=value, version=version)

    async def replace(self, expected_version: str, value: str) -> str:
        headers = self.headers
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                response = await client.put(
                    self.url,
                    headers=headers,
                    json={"expected_version": expected_version, "value": value},
                )
        except httpx.RequestError as exc:
            raise CredentialSyncError("Credential update is unavailable.") from exc
        self._raise_for_status(response)
        try:
            version = response.json()["version"]
        except (KeyError, TypeError, ValueError) as exc:
            raise CredentialSyncError(
                "Credential source returned an invalid response."
            ) from exc
        if not isinstance(version, str) or not version:
            raise CredentialSyncError("Credential source returned an invalid response.")
        return version

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == 404:
            raise CredentialNeedsReauthentication(
                "ChatGPT authentication is missing. Please sign in again."
            )
        if response.status_code == 409:
            raise CredentialConflict(
                "The canonical credential changed in another runtime."
            )
        if response.status_code in (400, 422):
            raise CredentialNeedsReauthentication(
                "ChatGPT authentication is invalid. Please sign in again."
            )
        if response.status_code in (401, 403):
            raise CredentialAuthorizationRejected(
                "Credential authorization was rejected."
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CredentialSyncError("Credential source request failed.") from exc
