"""The one way a Pydantic model holds a secret.

Annotate the field with :class:`SecretField`; it installs the validator and the
serializer together. Build the dump/validate context with :func:`cipher_context`
or :func:`client_context` instead of a hand-written dict.
"""

import logging
import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import (
    Annotated,
    Any,
    Final,
    Literal,
    TypedDict,
    Union,
    get_args,
    get_origin,
    overload,
)

from pydantic import (
    BaseModel,
    GetCoreSchemaHandler,
    SecretStr,
    SerializationInfo,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
)
from pydantic_core import CoreSchema, core_schema

from openhands.sdk.utils.cipher import FERNET_TOKEN_PREFIX, Cipher
from openhands.sdk.utils.deprecation import deprecated


REDACTED_SECRET_VALUE = "**********"

# Type for expose_secrets context value
ExposeSecretsMode = Literal["encrypted", "plaintext"] | bool

ResolvedExposeMode = Literal["plaintext", "encrypted", "redact"]

ClientExposure = Literal["redact", "encrypted", "plaintext"]

_logger = logging.getLogger(__name__)


class MissingCipherError(ValueError):
    """Raised when a secret must be encrypted but no cipher is configured."""


# Validation-context flag: when true, a Fernet token no configured key can
# decrypt raises ``SecretDecryptionError`` instead of reading as ``None``.
# Read-modify-write paths set it so a write under a changed key cannot persist
# ``null`` over the stored ciphertext.
STRICT_DECRYPT_CONTEXT_KEY: Final[str] = "strict_decrypt"


# Operator guidance for a stored value no configured key can decrypt.
UNDECRYPTABLE_SECRET_HINT: Final[str] = (
    "It was saved under a key that is neither OH_SECRET_KEY nor in "
    "OH_PREVIOUS_SECRET_KEYS: restore that key as OH_SECRET_KEY, or add it to "
    "OH_PREVIOUS_SECRET_KEYS to rotate to the current key."
)


class SecretDecryptionError(Exception):
    """A stored secret cannot be decrypted with the current cipher.

    Deliberately not a ``ValueError``: Pydantic lets it propagate as is
    instead of wrapping it in a ``ValidationError`` that echoes the token.
    """


class SecretContext(TypedDict, total=False):
    """The Pydantic context that secret fields read.

    Build it with :func:`cipher_context` or :func:`client_context`.
    """

    cipher: Cipher
    expose_secrets: ExposeSecretsMode
    strict_decrypt: bool


def cipher_context(cipher: Cipher | None, *, strict: bool = False) -> SecretContext:
    """Context that encrypts secrets on dump and decrypts them on load.

    Used for persisted state and for encrypted round trips with a client.
    Without a cipher, a dump redacts and a load reads values as they are.
    With ``strict``, loading a token no configured key can decrypt raises
    :class:`SecretDecryptionError` instead of reading it as ``None``.
    """
    if cipher is None:
        return SecretContext()
    context = SecretContext(cipher=cipher)
    if strict:
        context["strict_decrypt"] = True
    return context


def client_context(mode: ClientExposure, cipher: Cipher | None = None) -> SecretContext:
    """Context for a dump that leaves this object.

    ``"redact"`` masks every secret. ``"encrypted"`` writes Fernet ciphertext
    under ``cipher``, which a frontend can send back but not read; dumping
    raises :class:`MissingCipherError` when there is no cipher.
    ``"plaintext"`` writes the raw values, for trusted backends and in-process
    copies.
    """
    if mode == "redact":
        return SecretContext()
    context = SecretContext(expose_secrets=mode)
    if cipher is not None:
        context["cipher"] = cipher
    return context


def resolve_expose_mode(context: Mapping[str, Any] | None) -> ResolvedExposeMode:
    """Resolve a Pydantic context to plaintext / encrypted / redact.

    Cipher presence implies ``"encrypted"`` (storage-path opt-in) unless
    ``expose_secrets`` overrides.
    """
    if not context:
        return "redact"
    expose_mode = context.get("expose_secrets")
    if expose_mode == "plaintext" or expose_mode is True:
        return "plaintext"
    if expose_mode == "encrypted" or context.get("cipher") is not None:
        return "encrypted"
    return "redact"


def is_redacted_secret(v: str | SecretStr | None) -> bool:
    if v is None:
        return False
    if isinstance(v, SecretStr):
        return v.get_secret_value() == REDACTED_SECRET_VALUE
    return v == REDACTED_SECRET_VALUE


def _is_unusable(raw: str) -> bool:
    return not raw.strip() or raw == REDACTED_SECRET_VALUE


def _context_cipher(context: Mapping[str, Any] | None) -> Cipher | None:
    return context.get("cipher") if context else None


def _encode_secret(secret: SecretStr, context: Mapping[str, Any] | None) -> Any:
    mode = resolve_expose_mode(context)
    if mode == "plaintext":
        return secret.get_secret_value()
    if mode == "encrypted":
        cipher = _context_cipher(context)
        if cipher is None:
            raise MissingCipherError(
                "Cannot encrypt secret: no cipher configured. "
                "Set OH_SECRET_KEY environment variable."
            )
        raw = secret.get_secret_value()
        if raw.startswith(FERNET_TOKEN_PREFIX) and cipher.try_decrypt_str(raw):
            # Already a token: keep it, re-encrypted under the current key
            # when only a previous key can read it.
            return cipher.rotate(raw) or raw
        return cipher.encrypt(secret)
    return secret


def _mentions_secret_str(tp: Any) -> bool:
    return tp is SecretStr or any(_mentions_secret_str(arg) for arg in get_args(tp))


def _members(tp: Any) -> list[Any]:
    """Flatten ``Annotated`` and unions into the member types."""
    origin = get_origin(tp)
    if origin is Annotated:
        return _members(get_args(tp)[0])
    if origin is Union or origin is types.UnionType:
        return [m for arg in get_args(tp) for m in _members(arg)]
    return [tp]


def _map_value_type(tp: Any) -> Any | None:
    """The value type when ``tp`` is a ``dict``/``Mapping`` field, else ``None``."""
    for member in _members(tp):
        origin = get_origin(member)
        if isinstance(origin, type) and issubclass(origin, Mapping):
            return get_args(member)[1]
    return None


@dataclass(frozen=True, slots=True)
class _Shape:
    is_map: bool
    as_text: bool


def _shape(source_type: Any) -> _Shape:
    value_type = _map_value_type(source_type)
    if value_type is not None:
        return _Shape(is_map=True, as_text=not _mentions_secret_str(value_type))
    return _Shape(is_map=False, as_text=not _mentions_secret_str(source_type))


_INFERRED: Final = object()


@dataclass(frozen=True, slots=True)
class SecretField:
    """Marks a field as holding secrets: ``Annotated[SecretStr | None, SecretField()]``.

    The marker installs the validator and the serializer together, so a field
    cannot get one without the other. On load, a blank or redacted value reads
    as ``None`` and a Fernet token is decrypted with the context's cipher. On
    dump, the context picks redact, encrypt or plaintext (see
    :func:`cipher_context` and :func:`client_context`).

    A ``dict``/``Mapping`` field is handled per entry. A field typed ``str``
    (no ``SecretStr`` in its type) keeps ``str`` values.

    Args:
        undecryptable: what a token no configured key can decrypt becomes:
            ``"none"`` (unset, or dropped from a map), ``"keep"`` (the
            ciphertext is kept) or ``"raise"`` (validation fails). A strict
            context (``cipher_context(cipher, strict=True)``) raises
            :class:`SecretDecryptionError` for ``"none"`` and ``"keep"``.
        keep_unusable: keep blank and redacted map entries as given instead
            of dropping them.
        secret_keys: for a map, which keys hold secrets; the other entries
            pass through untouched.
        redact_as_text: in redact mode, dump the placeholder string in Python
            mode too, instead of the ``SecretStr``.
        from_client: the value comes from a client request; a blank or
            redacted value fails validation instead of reading as unset.
        serialized_as: the type the dump is declared as, for the
            serialization JSON schema (and so the published OpenAPI). By
            default the schema is the field's own type.
    """

    undecryptable: Literal["none", "keep", "raise"] = "none"
    keep_unusable: bool = False
    secret_keys: Callable[[str], bool] | None = None
    redact_as_text: bool = False
    from_client: bool = False
    serialized_as: Any = _INFERRED

    def __get_pydantic_core_schema__(
        self, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        shape = _shape(source_type)
        return_schema = (
            None
            if self.serialized_as is _INFERRED
            else handler.generate_schema(self.serialized_as)
        )
        return core_schema.with_info_wrap_validator_function(
            partial(self._validate, shape),
            handler(source_type),
            serialization=core_schema.plain_serializer_function_ser_schema(
                partial(self._serialize, shape),
                info_arg=True,
                return_schema=return_schema,
                when_used="always",
            ),
        )

    def read(self, value: Any, context: Mapping[str, Any] | None) -> Any:
        """Read one value: ``SecretStr``, ``None``, or ``value`` itself when it
        is neither a ``str`` nor a ``SecretStr``."""
        plaintext = self._plaintext(value, context)
        return SecretStr(plaintext) if isinstance(plaintext, str) else plaintext

    def _plaintext(self, value: Any, context: Mapping[str, Any] | None) -> Any:
        """Reduce one input to its plaintext ``str``, to ``None`` (unset), or
        leave it as is when it is neither a ``str`` nor a ``SecretStr``.

        The field's own schema then validates the plaintext, so this works in
        JSON mode too, where a ``SecretStr`` object would not be accepted.
        """
        if not isinstance(value, (str, SecretStr)):
            return value
        raw = _raw(value)
        if _is_unusable(raw):
            if self.from_client:
                raise ValueError(
                    "The secret must be a real value, not blank or redacted"
                )
            return None
        cipher = _context_cipher(context)
        if cipher is None or not raw.startswith(FERNET_TOKEN_PREFIX):
            return raw
        if self.undecryptable == "keep":
            plaintext = cipher.try_decrypt_str(raw)
            if plaintext is not None:
                return plaintext
        else:
            decrypted = cipher.decrypt(raw)
            if decrypted is not None:
                return decrypted.get_secret_value()
        if self.undecryptable == "raise":
            raise ValueError(
                "The secret is encrypted but no configured key can decrypt it. "
                + UNDECRYPTABLE_SECRET_HINT
            )
        if context and context.get(STRICT_DECRYPT_CONTEXT_KEY):
            raise SecretDecryptionError(
                "A stored secret cannot be decrypted with any configured key. "
                + UNDECRYPTABLE_SECRET_HINT
            )
        if self.undecryptable == "keep":
            _logger.warning(
                "A secret looks encrypted but could not be decrypted (cipher "
                "mismatch or corruption); leaving the ciphertext in place."
            )
            return raw
        return None

    def write(self, value: Any, context: Mapping[str, Any] | None) -> Any:
        """Dump one value under ``context``."""
        if value is None:
            return None
        secret = value if isinstance(value, SecretStr) else SecretStr(value)
        if self.redact_as_text and resolve_expose_mode(context) == "redact":
            return REDACTED_SECRET_VALUE
        return _encode_secret(secret, context)

    def _validate(
        self,
        shape: _Shape,
        value: Any,
        handler: ValidatorFunctionWrapHandler,
        info: ValidationInfo,
    ) -> Any:
        if shape.is_map:
            return handler(self._plaintext_map(value, info.context))
        result = handler(self._plaintext(value, info.context))
        if not shape.as_text and isinstance(result, str):
            # ``str | SecretStr`` unions pick ``str`` for a plain string.
            return SecretStr(result)
        return result

    def _plaintext_map(self, value: Any, context: Mapping[str, Any] | None) -> Any:
        if not isinstance(value, dict):
            return value
        out: dict[Any, Any] = {}
        for key, entry in value.items():
            if not self._is_secret_entry(key, entry) or (
                self.keep_unusable and _is_unusable(_raw(entry))
            ):
                out[key] = entry
                continue
            plaintext = self._plaintext(entry, context)
            if plaintext is not None:
                out[key] = plaintext
        return out

    def _serialize(self, shape: _Shape, value: Any, info: SerializationInfo) -> Any:
        if not shape.is_map or value is None:
            return self.write(value, info.context)
        out: dict[Any, Any] = {}
        for key, entry in value.items():
            if isinstance(entry, BaseModel):
                out[key] = entry.model_dump(mode=info.mode, context=info.context)
            elif self._is_secret_entry(key, entry):
                out[key] = self.write(entry, info.context)
            else:
                out[key] = entry
        return out

    def _is_secret_entry(self, key: Any, entry: Any) -> bool:
        if self.secret_keys is not None and not self.secret_keys(key):
            return False
        return isinstance(entry, (str, SecretStr))


def _raw(entry: str | SecretStr) -> str:
    return entry.get_secret_value() if isinstance(entry, SecretStr) else entry


@deprecated(
    deprecated_in="1.62.0",
    removed_in="1.67.0",
    details="Annotate the field with SecretField instead.",
)
def serialize_secret(v: SecretStr | None, info):
    """Serialize one secret under ``info.context`` (redact/encrypt/plaintext)."""
    return SecretField().write(v, info.context)


@deprecated(
    deprecated_in="1.62.0",
    removed_in="1.67.0",
    details="Annotate the field with SecretField instead.",
)
def validate_secret(v: str | SecretStr | None, info) -> SecretStr | None:
    """Read one secret: blank/redacted → ``None``, Fernet token → decrypted."""
    if v is not None and not isinstance(v, (str, SecretStr)):
        if not v:
            return None
        raise TypeError(f"A secret must be a string, not {type(v).__name__}")
    return SecretField().read(v, info.context)


@deprecated(
    deprecated_in="1.62.0",
    removed_in="1.67.0",
    details=(
        "Annotate the field with "
        "SecretField(undecryptable='keep', keep_unusable=True) instead."
    ),
)
def validate_secret_dict(
    value: Any,
    info: ValidationInfo,
    *,
    description: str = "secret",  # noqa: ARG001
) -> Any:
    """Decrypt every Fernet-encrypted entry in a ``dict[str, str]`` field."""
    if _context_cipher(info.context) is None:
        return value
    field = SecretField(undecryptable="keep", keep_unusable=True)
    return field._plaintext_map(value, info.context)


@overload
def decrypt_str_with_cipher_or_keep(
    cipher: Cipher, value: str, *, description: str = ...
) -> str: ...


@overload
def decrypt_str_with_cipher_or_keep(
    cipher: Cipher, value: Any, *, description: str = ...
) -> Any: ...


def decrypt_str_with_cipher_or_keep(
    cipher: Cipher,
    value: Any,
    *,
    description: str = "secret",
) -> Any:
    """Decrypt a single Fernet-encrypted string in place.

    Returned unchanged when the value isn't a string, doesn't look like a
    Fernet token (legacy plaintext from clients that pre-date the
    encryption pipeline), or fails to decrypt (cipher mismatch / token
    corruption — logged so the operator can repair rather than the
    object dying at construction).
    """
    if not isinstance(value, str):
        return value
    if not value.startswith(FERNET_TOKEN_PREFIX):
        return value
    decrypted = cipher.try_decrypt_str(value)
    if decrypted is None:
        _logger.warning(
            "%s value looks encrypted but could not be decrypted "
            "(cipher mismatch or corruption); leaving the ciphertext in place.",
            description,
        )
        return value
    return decrypted
