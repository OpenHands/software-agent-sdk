"""Secret sources and types for handling sensitive data."""

import os
from abc import ABC, abstractmethod
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import Field, SecretStr, field_serializer, field_validator

from openhands.sdk.logger import get_logger
from openhands.sdk.utils.models import DiscriminatedUnionMixin
from openhands.sdk.utils.pydantic_secrets import (
    is_redacted_secret,
    serialize_secret,
    validate_secret,
)
from openhands.sdk.utils.redact import is_secret_key


logger = get_logger(__name__)

_INTERNAL_SERVER_URL_ENV = "OH_INTERNAL_SERVER_URL"
_DEFAULT_INTERNAL_SERVER_URL = "http://127.0.0.1:8000"


def _resolve_lookup_secret_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.netloc or parsed.scheme:
        return url

    base_url = os.getenv(_INTERNAL_SERVER_URL_ENV, _DEFAULT_INTERNAL_SERVER_URL)
    return urljoin(f"{base_url.rstrip('/')}/", url)


class SecretSource(DiscriminatedUnionMixin, ABC):
    """Source for a named secret which may be obtained dynamically"""

    description: str | None = Field(
        default=None,
        description="Optional description for this secret",
    )

    @abstractmethod
    def get_value(self) -> str | None:
        """Get the value of a secret in plain text"""


class StaticSecret(SecretSource):
    """A secret stored locally"""

    value: SecretStr | None = None

    def get_value(self) -> str | None:
        if self.value is None:
            return None
        return self.value.get_secret_value()

    @field_validator("value")
    @classmethod
    def _validate_secrets(cls, v: SecretStr | None, info):
        return validate_secret(v, info)

    @field_serializer("value", when_used="always")
    def _serialize_secrets(self, v: SecretStr | None, info):
        return serialize_secret(v, info)


class LookupSecret(SecretSource):
    """A secret looked up from some external url"""

    url: str
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def _normalize_url(cls, url: str) -> str:
        return _resolve_lookup_secret_url(url)

    def get_value(self) -> str:
        response = httpx.get(self.url, headers=self.headers, timeout=30.0)
        response.raise_for_status()
        return response.text

    @field_validator("headers")
    @classmethod
    def _validate_secrets(cls, headers: dict[str, str], info):
        result = {}
        for key, value in headers.items():
            if not is_secret_key(key):
                result[key] = value
                continue

            # Drop empty / redacted header values up-front; they carry no
            # usable auth material regardless of cipher state.
            if not value or not value.strip() or is_redacted_secret(value):
                logger.debug(f"Skipping redacted header '{key}' during deserialization")
                continue

            secret_value = validate_secret(SecretStr(value), info)
            if secret_value is None:
                # validate_secret only returns None for a non-empty input when
                # a cipher was supplied in the validation context but
                # decryption failed. That happens when callers (e.g. a frontend
                # building a LookupSecret) send a plaintext auth header but
                # the request is otherwise tagged as containing encrypted
                # secrets. Preserve the original value rather than silently
                # dropping the header — the caller's intent for headers is
                # always plaintext authentication metadata.
                logger.debug(
                    f"Header '{key}' could not be decrypted; "
                    "treating value as plaintext"
                )
                result[key] = value
            else:
                result[key] = secret_value.get_secret_value()
        return result

    @field_serializer("headers", when_used="always")
    def _serialize_secrets(self, headers: dict[str, str], info):
        result = {}
        for key, value in headers.items():
            if is_secret_key(key):
                secret_value = serialize_secret(SecretStr(value), info)
                if secret_value is None:
                    logger.debug(
                        f"Skipping redacted header '{key}' during serialization"
                    )
                    continue
                result[key] = secret_value
            else:
                result[key] = value
        return result


# Type alias for secret values - can be a plain string or a SecretSource
SecretValue = str | SecretSource
