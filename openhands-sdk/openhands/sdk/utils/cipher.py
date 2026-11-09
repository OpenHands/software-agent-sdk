"""
Cipher utility for preventing accidental secret disclosure in serialized data

SECURITY WARNINGS:
- The secret key is a string for ease of use but should contain at least 256
  bits of entropy

KEY ROTATION:
A ``Cipher`` can carry previous keys. It always encrypts with the current
key and decrypts with the current key first, then each previous key in order
(``cryptography``'s ``MultiFernet``), so values saved under an old key stay
readable after a rotation and are re-encrypted under the current key the
next time they are saved.
"""

import hashlib
from base64 import b64encode
from collections.abc import Iterable
from typing import Final

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from pydantic import SecretStr


# Fernet token prefix used to distinguish ciphertext from legacy plaintext.
# Do not shorten: a 5-char prefix collides with realistic base64 plaintext.
FERNET_TOKEN_PREFIX: Final[str] = "gAAAAA"


class Cipher:
    """
    Simple encryption utility for preventing accidental secret disclosure.
    """

    def __init__(self, secret_key: str, previous_keys: Iterable[str] = ()):
        self.secret_key = secret_key
        # Keys that only decrypt (oldest last). Blank entries and repeats of
        # the current key are dropped; order is kept so the likeliest key
        # (the one rotated away most recently) is tried first.
        previous: list[str] = []
        for key in previous_keys:
            if key.strip() and key != secret_key and key not in previous:
                previous.append(key)
        self.previous_keys: tuple[str, ...] = tuple(previous)
        self._fernet: Fernet | None = None
        self._multi_fernet: MultiFernet | None = None

    def encrypt(self, secret: SecretStr | None) -> str | None:
        if secret is None:
            return None
        secret_value = secret.get_secret_value().encode()
        fernet = self._get_fernet()
        result = fernet.encrypt(secret_value).decode()
        return result

    def decrypt(self, secret: str | None) -> SecretStr | None:
        """
        Decrypt a secret value, returning None if decryption fails.

        This handles cases where existing conversations were serialized with different
        encryption keys or contain invalid encrypted data. A warning is logged when
        decryption fails and a None is returned. This mimics the case where
        no cipher was defined so secrets where redacted.
        """
        if secret is None:
            return None
        try:
            decrypted = self._get_multi_fernet().decrypt(secret.encode()).decode()
            return SecretStr(decrypted)
        except Exception as e:
            # Import here to avoid circular imports
            from openhands.sdk.logger import get_logger

            logger = get_logger(__name__)
            logger.warning(
                f"Failed to decrypt secret value (setting to None): {e}. "
                "This may occur when loading conversations encrypted with a different "
                "key or when upgrading from older versions."
            )
            return None

    def try_decrypt_str(self, value: str) -> str | None:
        """Decrypt to a string, or ``None`` on InvalidToken (no logging)."""
        try:
            return self._get_multi_fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            return None

    def is_current(self, token: str) -> bool:
        """Whether ``token`` decrypts under the current key (not a previous one)."""
        try:
            self._get_fernet().decrypt(token.encode())
        except InvalidToken:
            return False
        return True

    def rotate(self, token: str) -> str | None:
        """Return ``token`` re-encrypted under the current key.

        A token already under the current key is returned unchanged; a token
        only a previous key can decrypt is re-encrypted; ``None`` when no
        configured key can decrypt it.
        """
        if self.is_current(token):
            return token
        try:
            return self._get_multi_fernet().rotate(token.encode()).decode()
        except InvalidToken:
            return None

    @staticmethod
    def _derive_fernet(key: str) -> Fernet:
        # Hash the key to make sure we have a 256 bit value
        return Fernet(b64encode(hashlib.sha256(key.encode()).digest()))

    def _get_multi_fernet(self) -> MultiFernet:
        multi = self._multi_fernet
        if multi is None:
            multi = MultiFernet(
                [self._get_fernet()]
                + [self._derive_fernet(key) for key in self.previous_keys]
            )
            object.__setattr__(self, "_multi_fernet", multi)
        return multi

    def _get_fernet(self) -> Fernet:
        fernet = self._fernet
        if fernet is None:
            fernet = self._derive_fernet(self.secret_key)
            object.__setattr__(self, "_fernet", fernet)
        return fernet
