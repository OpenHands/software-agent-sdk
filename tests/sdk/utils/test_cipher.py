"""Tests for the Cipher utility class."""

from base64 import urlsafe_b64encode

from cryptography.fernet import Fernet
from pydantic import SecretStr

from openhands.sdk.utils.cipher import Cipher


def test_cipher_encrypt_decrypt():
    """Test basic encryption and decryption functionality."""
    # Generate a proper Fernet key
    key = urlsafe_b64encode(b"a" * 32).decode("ascii")
    cipher = Cipher(key)

    secret = SecretStr("my-secret-api-key")

    # Test encryption
    encrypted = cipher.encrypt(secret)
    assert encrypted is not None
    assert encrypted != secret.get_secret_value()
    assert isinstance(encrypted, str)

    # Test decryption
    decrypted = cipher.decrypt(encrypted)
    assert decrypted is not None
    assert decrypted.get_secret_value() == secret.get_secret_value()


def test_cipher_encrypt_none():
    """Test that encrypting None returns None."""
    key = urlsafe_b64encode(b"a" * 32).decode("ascii")
    cipher = Cipher(key)

    result = cipher.encrypt(None)
    assert result is None


def test_cipher_decrypt_none():
    """Test that decrypting None returns None."""
    key = urlsafe_b64encode(b"a" * 32).decode("ascii")
    cipher = Cipher(key)

    result = cipher.decrypt(None)
    assert result is None


def test_cipher_decrypt_invalid_data():
    """Test that decrypting invalid data returns None and logs warning."""
    key = urlsafe_b64encode(b"a" * 32).decode("ascii")
    cipher = Cipher(key)

    # Test with completely invalid data
    result = cipher.decrypt("invalid-encrypted-data")
    assert result is None

    # Test with malformed base64
    result = cipher.decrypt("not-base64!")
    assert result is None


def test_cipher_decrypt_wrong_key():
    """Test that decrypting with wrong key returns None and logs warning."""
    # Create two different keys
    key1 = urlsafe_b64encode(b"a" * 32).decode("ascii")
    key2 = urlsafe_b64encode(b"b" * 32).decode("ascii")

    cipher1 = Cipher(key1)
    cipher2 = Cipher(key2)

    secret = SecretStr("test-secret")

    # Encrypt with first cipher
    encrypted = cipher1.encrypt(secret)
    assert encrypted is not None

    # Try to decrypt with second cipher (wrong key)
    result = cipher2.decrypt(encrypted)
    assert result is None


def test_cipher_fernet_caching():
    """Test that Fernet instance is cached properly."""
    key = urlsafe_b64encode(b"a" * 32).decode("ascii")
    cipher = Cipher(key)

    # Get Fernet instance twice
    fernet1 = cipher._get_fernet()
    fernet2 = cipher._get_fernet()

    # Should be the same instance (cached)
    assert fernet1 is fernet2
    assert isinstance(fernet1, Fernet)


def test_cipher_with_real_fernet_key():
    """Test cipher with a real Fernet-generated key."""
    # Generate a proper Fernet key
    fernet_key = Fernet.generate_key()
    key = fernet_key.decode("ascii")

    cipher = Cipher(key)
    secret = SecretStr("test-api-key-12345")

    # Test round-trip encryption/decryption
    encrypted = cipher.encrypt(secret)
    decrypted = cipher.decrypt(encrypted)

    assert decrypted is not None
    assert decrypted.get_secret_value() == secret.get_secret_value()


def test_cipher_multiple_encryptions_different():
    """Test that multiple encryptions of the same value produce different results."""
    key = urlsafe_b64encode(b"a" * 32).decode("ascii")
    cipher = Cipher(key)

    secret = SecretStr("same-secret")

    # Encrypt the same secret multiple times
    encrypted1 = cipher.encrypt(secret)
    encrypted2 = cipher.encrypt(secret)

    # Results should be different (due to Fernet's built-in randomness)
    assert encrypted1 != encrypted2

    # But both should decrypt to the same value
    decrypted1 = cipher.decrypt(encrypted1)
    decrypted2 = cipher.decrypt(encrypted2)

    assert decrypted1 is not None
    assert decrypted2 is not None

    assert decrypted1.get_secret_value() == secret.get_secret_value()
    assert decrypted2.get_secret_value() == secret.get_secret_value()


def test_cipher_empty_string():
    """Test encryption/decryption of empty string."""
    key = urlsafe_b64encode(b"a" * 32).decode("ascii")
    cipher = Cipher(key)

    secret = SecretStr("")

    encrypted = cipher.encrypt(secret)
    assert encrypted is not None
    assert encrypted != ""

    decrypted = cipher.decrypt(encrypted)
    assert decrypted is not None
    assert decrypted.get_secret_value() == ""


def test_cipher_unicode_content():
    """Test encryption/decryption of unicode content."""
    key = urlsafe_b64encode(b"a" * 32).decode("ascii")
    cipher = Cipher(key)

    secret = SecretStr("🔐 Secret with émojis and ñoñ-ASCII chars! 中文")

    encrypted = cipher.encrypt(secret)
    decrypted = cipher.decrypt(encrypted)

    assert decrypted is not None
    assert decrypted.get_secret_value() == secret.get_secret_value()


def test_cipher_long_content():
    """Test encryption/decryption of long content."""
    key = urlsafe_b64encode(b"a" * 32).decode("ascii")
    cipher = Cipher(key)

    # Create a long secret (1KB)
    long_secret = "x" * 1024
    secret = SecretStr(long_secret)

    encrypted = cipher.encrypt(secret)
    decrypted = cipher.decrypt(encrypted)

    assert decrypted is not None
    assert decrypted.get_secret_value() == long_secret
